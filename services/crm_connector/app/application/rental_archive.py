"""Rental listing localization daemon (default off).

Walks Chengdu districts the way an employee browses the workbench: one list
page at a time, checking the local archive *before* spending a detail call,
with randomized human-scale pacing and a daily request cap shared with no one.

Two hard product rules are enforced here rather than in the client:

* 具体楼层 / 详细地址 are click-to-view and quota-limited upstream, so no
  route that reveals them is ever called; the cheap list page's coarse
  高/中/低 floor description is what the archive keeps.
* 普租 (del_type=2) 跟进记录随详情归档: the plain house_info aggregation
  runs with ``include_follows=True`` and the parsed detailFollow records
  are stored on ``house_info.follows``. Rows archived before follows were
  kept — a completed plain detail with no ``follows`` key — get one
  backfill refresh the next time the list sweep sights them, then normal
  TTL applies. 托管 (del_type=5) never takes the 普租 follow route.
"""

from __future__ import annotations

import hashlib
import logging
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.application.service import ConnectorService
from app.domain.errors import (
    ConnectorError,
    NetworkRequiredError,
    UpstreamInvalidInputError,
)
from app.domain.models import ConnectionState, RentalListing
from app.infrastructure.rental_archive_plan import (
    DISTRICTS,
    LIST_TOTAL_CAP,
    ArchiveCursor,
    build_filters,
    can_split_unit,
    clamp_page_size,
    is_skipped_district,
    last_page,
    needs_unit_split,
    next_crawled_district_index,
    next_page,
    next_unit,
    split_current_unit,
)
from app.infrastructure.rental_archive_sanitize import (
    as_archive_dict,
    sanitize_archive_payload,
)
from app.infrastructure.rental_archive_store import (
    DETAIL_STATUS_COMPLETE,
    DETAIL_STATUS_FAILED,
    DETAIL_STATUS_NONE,
    DETAIL_STATUS_SKIPPED,
    LOCALIZED_DETAIL_STATUSES,
    ArchiveListingRow,
    ArchiveRun,
    DailyCounters,
    RentalArchiveStore,
)
from app.infrastructure.settings import Settings

logger = logging.getLogger(__name__)

SHANGHAI = ZoneInfo("Asia/Shanghai")

DEL_TYPE_PLAIN = 2       # 普租 — detailHead / prospect / house_info
DEL_TYPE_TRUSTEESHIP = 5  # 托管 — trusteeship domain only

RUN_RUNNING = "RUNNING"
RUN_COMPLETE = "COMPLETE"
RUN_ABORTED = "ABORTED"
RUN_FAILED = "FAILED"

SKIP_DISABLED = "disabled"
SKIP_DAILY_CAP = "daily_cap"
SKIP_OUTSIDE_WINDOW = "outside_window"
SKIP_AUTH_REQUIRED = "auth_required"
SKIP_UPSTREAM_ERROR = "upstream_error"


@dataclass(frozen=True)
class ArchiveTick:
    """Outcome of one unit of work."""

    did_work: bool
    skipped_reason: str | None
    cursor: ArchiveCursor
    counters: DailyCounters
    detail_listing_id: str | None = None
    pages_done: int = 0


@dataclass(frozen=True)
class ArchiveDistrictStatus:
    district_id: str
    district_name: str
    total: int
    active: int
    complete: int
    pending: int
    skipped: int
    failed: int


@dataclass(frozen=True)
class ArchiveStatus:
    connection_state: str
    enabled: bool
    running: bool
    db_path: str
    window_start: str
    window_end: str
    within_window: bool
    local_date: str
    daily_request_cap: int
    daily_detail_cap: int
    detail_ttl_days: int
    cursor: ArchiveCursor
    counters: DailyCounters
    listing_total: int
    listing_active: int
    listing_complete: int
    listing_pending: int
    listing_skipped: int
    listing_failed: int
    districts: tuple[ArchiveDistrictStatus, ...]
    last_run: ArchiveRun | None


class RentalArchiveRunner:
    """Owns the crawl cursor, the pacing budget and the daemon thread."""

    def __init__(
        self,
        settings: Settings,
        service: ConnectorService,
        store: RentalArchiveStore,
        *,
        sleep: Callable[[float], None] | None = None,
        now: Callable[[], datetime] | None = None,
        uniform: Callable[[float, float], float] | None = None,
    ) -> None:
        self._settings = settings
        self._service = service
        self._store = store
        self._now = now or (lambda: datetime.now(UTC))
        self._uniform = uniform or random.uniform
        self._stop = threading.Event()
        # Default sleeper waits on the stop event so a 45s pacing pause never
        # delays shutdown.
        self._sleep = sleep or self._stop.wait
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._cursor: ArchiveCursor | None = None
        self._calls_since_pause = 0
        self._calls_until_pause = 0

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        thread = threading.Thread(
            target=self._loop, name="crm-rental-archive", daemon=True
        )
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def store(self) -> RentalArchiveStore:
        return self._store

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                tick = self.run_tick()
            except Exception as exc:  # defensive: the daemon must never die
                # exception() keeps the traceback: a class-name-only warning
                # once let an UnboundLocalError loop every 60s for ~20 hours
                # unnoticed.
                logger.exception(
                    "rental archive tick failed class=%s", exc.__class__.__name__
                )
                self._stop.wait(60.0)
                continue
            if not tick.did_work:
                # Nothing to do (cap spent, outside window, auth missing):
                # back off instead of spinning on the same gate.
                self._stop.wait(60.0)

    # -- status -------------------------------------------------------------

    def status(self) -> ArchiveStatus:
        settings = self._settings
        moment = self._now()
        counters = self._store.daily_counters(self._local_date(moment))
        total, active = self._store.counts()
        details = self._store.detail_counts()
        # Report every *crawled* catalog district (zeros included) so freshly
        # appended districts — 高新 first among them — show up before their
        # first row; far-suburban SKIPPED_DISTRICT_IDS are out of scope.
        # Completeness is tracked against the plan, not the current DB.
        stats_by_id = {row.district_id: row for row in self._store.district_stats()}
        districts = tuple(
            ArchiveDistrictStatus(
                district_id=district.district_id,
                district_name=district.name,
                total=stat.total,
                active=stat.active,
                complete=stat.complete,
                pending=stat.pending,
                skipped=stat.skipped,
                failed=stat.failed,
            )
            if (stat := stats_by_id.get(district.district_id)) is not None
            else ArchiveDistrictStatus(
                district_id=district.district_id,
                district_name=district.name,
                total=0,
                active=0,
                complete=0,
                pending=0,
                skipped=0,
                failed=0,
            )
            for district in DISTRICTS
            if not is_skipped_district(district.district_id)
        )
        return ArchiveStatus(
            connection_state=self._service.connection_status().state.value,
            enabled=settings.rental_archive_enabled,
            running=self.running,
            db_path=self._store.db_path,
            window_start=settings.rental_archive_window_start,
            window_end=settings.rental_archive_window_end,
            within_window=self._within_window(moment),
            local_date=self._local_date(moment),
            daily_request_cap=settings.rental_archive_daily_request_cap,
            daily_detail_cap=settings.rental_archive_detail_daily_cap,
            detail_ttl_days=settings.rental_archive_detail_ttl_days,
            cursor=self._load_cursor(),
            counters=counters,
            listing_total=total,
            listing_active=active,
            listing_complete=details.get(DETAIL_STATUS_COMPLETE, 0),
            listing_pending=details.get(DETAIL_STATUS_NONE, 0),
            listing_skipped=details.get(DETAIL_STATUS_SKIPPED, 0),
            listing_failed=details.get(DETAIL_STATUS_FAILED, 0),
            districts=districts,
            last_run=self._store.latest_run(),
        )

    # -- one unit of work ---------------------------------------------------

    def run_tick(self) -> ArchiveTick:
        """Do at most one list page or one listing's details.

        Serialized: the daemon thread and ``POST /tick`` share one cursor.
        """
        with self._lock:
            moment = self._now()
            local_date = self._local_date(moment)
            counters = self._store.daily_counters(local_date)
            cursor = self._load_cursor()

            if not self._settings.rental_archive_enabled:
                return ArchiveTick(False, SKIP_DISABLED, cursor, counters)
            if _at_cap(
                self._settings.rental_archive_daily_request_cap, counters.request_count
            ):
                return ArchiveTick(False, SKIP_DAILY_CAP, cursor, counters)
            if not self._within_window(moment):
                return ArchiveTick(False, SKIP_OUTSIDE_WINDOW, cursor, counters)
            if not self._session_ready():
                # No QR login is started from here; abort the open run so the
                # next tick resumes the cursor instead of retry-storming.
                self._abort_open_run(cursor, SKIP_AUTH_REQUIRED, moment)
                cursor = self._mark_resumed(cursor, moment)
                self._store.log_call(
                    run_id=cursor.run_id, kind="list", listing_id=None,
                    status="skipped", note=SKIP_AUTH_REQUIRED, requested_at=moment,
                )
                return ArchiveTick(False, SKIP_AUTH_REQUIRED, cursor, counters)

            if is_skipped_district(cursor.district_id):
                # Stale cursor parked on a far-suburban district that has left
                # localization scope: never censused it, so no finish_run /
                # deactivate_unseen — just jump to a fresh unsliced probe of
                # the next crawled district, dropping the skipped district's
                # pending units / seen set / detail queue. did_work keeps the
                # loop chaining through skipped ids instead of backing off 60s.
                cursor = ArchiveCursor(
                    district_index=next_crawled_district_index(cursor.district_index)
                )
                self._save_cursor(cursor, moment)
                return ArchiveTick(True, None, cursor, counters)

            try:
                if cursor.pending_detail_ids:
                    return self._tick_detail(cursor, moment, local_date)
                return self._tick_list_page(cursor, moment, local_date)
            except ConnectorError as exc:
                return self._fail_tick(cursor, exc, moment, local_date)

    # -- list sweep ---------------------------------------------------------

    def _tick_list_page(
        self, cursor: ArchiveCursor, moment: datetime, local_date: str
    ) -> ArchiveTick:
        run_id = cursor.run_id
        if run_id is None:
            run_id = self._store.start_run(
                district_id=cursor.district_id,
                price_band=cursor.price_band,
                started_at=moment,
            )
            cursor = replace(cursor, run_id=run_id)

        page_size = clamp_page_size(self._settings.rental_archive_page_size)
        filters = build_filters(
            district_id=cursor.district_id,
            price_band=cursor.price_band,
            page=cursor.page,
            page_size=page_size,
            extra_filters=cursor.extra_filters,
        )
        page = self._service.search_rental_listings(filters)
        self._count_request(local_date, run_id, kind="list", listing_id=None)

        over_cap = needs_unit_split(cursor, page.total, cursor.page)
        if over_cap and can_split_unit(cursor):
            # A unit above the upstream's 1000-row deep-paging cap cannot be
            # swept whole: not the unsliced district probe, and not a fat
            # static band either. Retire the probe run without
            # inactive-marking and restart the unit sliced smaller (rent
            # halves, then delType when a 1-yuan band still overflows).
            self._store.finish_run(
                run_id, status=RUN_ABORTED, finished_at=moment,
                abort_reason="price_split",
            )
            cursor = split_current_unit(cursor)
            self._save_cursor(cursor, moment)
            self._pace("list", moment)
            return ArchiveTick(
                True, None, cursor, self._store.daily_counters(local_date), pages_done=1
            )
        # Over cap with every split dimension (rent + delType) exhausted:
        # sweep the 1000 reachable rows, but this run is not a complete census
        # of the unit, so it must never flip unseen rows inactive.
        partial_only = over_cap
        if partial_only:
            logger.warning(
                "rental archive unit over %d-row cap with no split left"
                " district=%s band=%s filters=%s total=%s",
                LIST_TOTAL_CAP,
                cursor.district_id,
                cursor.price_band,
                cursor.extra_filters,
                page.total,
            )

        seen = list(cursor.seen_ids)
        pending: list[str] = []
        for listing in page.items:
            if not listing.listing_id:
                continue
            seen.append(listing.listing_id)
            outcome = self._store.upsert_list_row(
                self._build_row(listing, cursor), run_id=run_id, now=moment
            )
            if self._needs_detail(outcome, moment) or self._needs_follow_backfill(
                listing.listing_id
            ):
                pending.append(listing.listing_id)

        self._store.bump_run(
            run_id, pages=1, items=len(page.items)
        )
        self._store.log_call(
            run_id=run_id, kind="list", listing_id=None, status="ok",
            note=f"district={cursor.district_id} page={cursor.page} rows={len(page.items)}",
            requested_at=moment,
        )

        cursor = replace(
            cursor,
            seen_ids=tuple(dict.fromkeys(seen)),
            pending_detail_ids=tuple(pending),
            detail_run_id=run_id,
            unit_total=page.total,
            unit_resumed=cursor.unit_resumed or partial_only,
        )
        if cursor.page >= last_page(page.total, page_size) or not page.items:
            cursor = self._complete_unit(cursor, run_id, moment)
        else:
            cursor = next_page(cursor)
        self._save_cursor(cursor, moment)
        self._pace("list", moment)
        return ArchiveTick(
            True, None, cursor, self._store.daily_counters(local_date), pages_done=1
        )

    def _complete_unit(
        self, cursor: ArchiveCursor, run_id: int, moment: datetime
    ) -> ArchiveCursor:
        """Close a fully swept district/band and retire listings it never saw."""
        if cursor.unit_resumed:
            # The unit was interrupted earlier, so this run's seen-set is not a
            # complete census of the district: never flip inactive off it.
            self._store.finish_run(
                run_id, status=RUN_COMPLETE, finished_at=moment,
                abort_reason="resumed_no_deactivate",
            )
        else:
            self._store.finish_run(run_id, status=RUN_COMPLETE, finished_at=moment)
            self._store.deactivate_unseen(
                district_id=cursor.district_id,
                price_band=cursor.price_band,
                run_id=run_id,
            )
        advanced = next_unit(cursor)
        if not cursor.pending_detail_ids:
            return advanced
        # Details discovered on the last page still need fetching; keep them
        # with their own run id while the cursor moves on.
        return replace(
            advanced,
            pending_detail_ids=cursor.pending_detail_ids,
            detail_run_id=cursor.detail_run_id,
        )

    # -- detail localization ------------------------------------------------

    def _tick_detail(
        self, cursor: ArchiveCursor, moment: datetime, local_date: str
    ) -> ArchiveTick:
        counters = self._store.daily_counters(local_date)
        if _at_cap(
            self._settings.rental_archive_detail_daily_cap, counters.detail_count
        ):
            # Detail budget spent for today; keep the cheap list sweep moving.
            cursor = replace(cursor, pending_detail_ids=())
            self._save_cursor(cursor, moment)
            return self._tick_list_page(cursor, moment, local_date)

        listing_id = cursor.pending_detail_ids[0]
        remaining = cursor.pending_detail_ids[1:]
        run_id = cursor.detail_run_id
        stored = self._store.get_listing(listing_id)
        del_type = stored.del_type if stored is not None else None

        try:
            if del_type == DEL_TYPE_PLAIN:
                detail = self._fetch_plain_detail(listing_id, run_id, local_date)
                status = DETAIL_STATUS_COMPLETE
                counted_detail = 1
            elif del_type == DEL_TYPE_TRUSTEESHIP:
                detail = self._fetch_trusteeship_detail(listing_id, run_id, local_date)
                status = DETAIL_STATUS_COMPLETE
                counted_detail = 1
            else:
                # Unknown del_type: the allowlist has no safe route, so mark it
                # localized-as-skipped and spend nothing.
                detail = None
                status = DETAIL_STATUS_SKIPPED
                counted_detail = 0
                self._store.log_call(
                    run_id=run_id, kind="detail", listing_id=listing_id,
                    status="skipped", note="unsupported_del_type", requested_at=moment,
                )
        except UpstreamInvalidInputError as exc:
            # Upstream rejects this one listing (stale id, delType mismatch):
            # one bad row must not abort the unit. Mark it failed — a later
            # list sweep requeues failed rows — log, and serve the queue tail.
            logger.warning(
                "rental archive detail rejected listing=%s code=%s",
                listing_id, exc.code,
            )
            self._store.mark_detail(
                listing_id, status=DETAIL_STATUS_FAILED, detail=None,
                captured_at=moment,
            )
            self._store.log_call(
                run_id=run_id, kind="detail", listing_id=listing_id,
                status="error", note=exc.code, requested_at=moment,
            )
            cursor = replace(cursor, pending_detail_ids=remaining)
            self._save_cursor(cursor, moment)
            return ArchiveTick(
                True,
                None,
                cursor,
                self._store.daily_counters(local_date),
                detail_listing_id=listing_id,
            )

        self._store.mark_detail(
            listing_id, status=status, detail=detail, captured_at=moment
        )
        if counted_detail:
            self._store.bump_daily(local_date, details=counted_detail)
            self._store.bump_run(run_id, details=counted_detail)
            self._pace("detail", moment)
        cursor = replace(cursor, pending_detail_ids=remaining)
        self._save_cursor(cursor, moment)
        return ArchiveTick(
            True,
            None,
            cursor,
            self._store.daily_counters(local_date),
            detail_listing_id=listing_id,
        )

    def _fetch_plain_detail(
        self, listing_id: str, run_id: int | None, local_date: str
    ) -> dict[str, object]:
        """普租 (del_type=2): head + 实勘 + 房源信息 (含 detailFollow 跟进记录).

        具体楼层 / 详细地址 stay forbidden; the extra follow HTTP rides
        inside this one house_info call, so no per-follow request is counted.
        """
        head = self._service.get_rental_listing_detail(listing_id)
        self._count_request(local_date, run_id, kind="detail", listing_id=listing_id)
        prospect = self._service.get_rental_listing_prospect(listing_id)
        self._count_request(local_date, run_id, kind="detail", listing_id=listing_id)
        info = self._service.get_rental_listing_house_info(
            listing_id, include_follows=True
        )
        self._count_request(local_date, run_id, kind="detail", listing_id=listing_id)
        return _clean(
            {
                "kind": "plain",
                "head": as_archive_dict(head),
                "prospect": as_archive_dict(prospect),
                "house_info": as_archive_dict(info),
            }
        )

    def _fetch_trusteeship_detail(
        self, listing_id: str, run_id: int | None, local_date: str
    ) -> dict[str, object]:
        """托管 (del_type=5): only the trusteeship head, sans signalFloor."""
        cell_code = self._service.get_rental_listing_redirect_url(listing_id)
        self._count_request(local_date, run_id, kind="redirect", listing_id=listing_id)
        if not cell_code:
            return _clean({"kind": "trusteeship", "cell_code": None})
        detail = self._service.get_trusteeship_detail(cell_code)
        self._count_request(local_date, run_id, kind="tuoguan", listing_id=listing_id)
        return _clean(
            {
                "kind": "trusteeship",
                "cell_code": cell_code,
                "head": as_archive_dict(detail),
            }
        )

    def _needs_detail(self, outcome, moment: datetime) -> bool:
        if outcome.detail_status not in LOCALIZED_DETAIL_STATUSES:
            return True
        captured = outcome.detail_captured_at
        if captured is None:
            return True
        ttl = timedelta(days=self._settings.rental_archive_detail_ttl_days)
        if moment - captured >= ttl:
            return True
        # Fresh, but the list row itself changed (rent / status / cover /
        # layout / community): refresh the detail too.
        return outcome.content_changed

    def _needs_follow_backfill(self, listing_id: str) -> bool:
        """One-shot backfill of rows archived before 普租 follows were kept.

        Read after the list upsert (which never replaces ``payload["detail"]``):
        only a *completed* *plain* detail whose ``house_info`` has no
        ``follows`` key qualifies. Trusteeship details, skipped rows and a
        present key (even ``[]``) do not — the key presence is what stops the
        backfill from looping.
        """
        stored = self._store.get_listing(listing_id)
        if stored is None or stored.detail_status != DETAIL_STATUS_COMPLETE:
            return False
        detail = stored.payload.get("detail")
        if not isinstance(detail, dict) or detail.get("kind") != "plain":
            return False
        house_info = detail.get("house_info")
        if not isinstance(house_info, dict):
            return False
        return "follows" not in house_info

    # -- failure handling ---------------------------------------------------

    def _fail_tick(
        self,
        cursor: ArchiveCursor,
        exc: ConnectorError,
        moment: datetime,
        local_date: str,
    ) -> ArchiveTick:
        reason = exc.code
        if isinstance(exc, NetworkRequiredError):
            # A transport blip (云枢 ConnectError etc.) is transient: keep the
            # RUNNING run, the in-flight listing/page and the pending detail
            # queue exactly where they are — no finish_run, no resumed mark.
            # _loop backs off 60s on did_work=False and retries the same tick.
            self._store.log_call(
                run_id=cursor.run_id, kind="list", listing_id=None, status="error",
                note=reason, requested_at=moment,
            )
            logger.warning("rental archive tick network_retry code=%s", reason)
            return ArchiveTick(
                False, SKIP_UPSTREAM_ERROR, cursor,
                self._store.daily_counters(local_date),
            )
        status = RUN_ABORTED if exc.status_code in (401, 503) else RUN_FAILED
        if cursor.run_id is not None:
            self._store.finish_run(
                cursor.run_id, status=status, finished_at=moment, abort_reason=reason
            )
        self._store.log_call(
            run_id=cursor.run_id, kind="list", listing_id=None, status="error",
            note=reason, requested_at=moment,
        )
        logger.warning("rental archive tick aborted code=%s", reason)
        cursor = self._mark_resumed(cursor, moment)
        skipped = (
            SKIP_AUTH_REQUIRED if exc.status_code == 401 else SKIP_UPSTREAM_ERROR
        )
        return ArchiveTick(
            False, skipped, cursor, self._store.daily_counters(local_date)
        )

    def _abort_open_run(
        self, cursor: ArchiveCursor, reason: str, moment: datetime
    ) -> None:
        if cursor.run_id is None:
            return
        run = self._store.get_run(cursor.run_id)
        if run is not None and run.status == RUN_RUNNING:
            self._store.finish_run(
                cursor.run_id, status=RUN_ABORTED, finished_at=moment,
                abort_reason=reason,
            )

    def _mark_resumed(self, cursor: ArchiveCursor, moment: datetime) -> ArchiveCursor:
        """Drop the open run but keep the position and the detail queue.

        The unit may not deactivate after an interruption, and pending
        details must survive it: clearing the queue here is what used to stall
        rows whose detail call was in flight when auth/network went away.
        Their discovering run may already be closed, so ``detail_run_id`` is
        intentionally retained too.
        """
        resumed = replace(cursor, run_id=None, unit_resumed=True)
        self._save_cursor(resumed, moment)
        return resumed

    # -- helpers ------------------------------------------------------------

    def _build_row(
        self, listing: RentalListing, cursor: ArchiveCursor
    ) -> ArchiveListingRow:
        payload = _clean({"list": as_archive_dict(listing)})
        return ArchiveListingRow(
            listing_id=listing.listing_id,
            district_id=cursor.district_id,
            price_band=cursor.price_band,
            del_type=listing.del_type,
            community=listing.community or None,
            payload=payload,
            content_hash=_content_hash(listing),
        )

    def _count_request(
        self, local_date: str, run_id: int | None, *, kind: str, listing_id: str | None
    ) -> None:
        self._store.bump_daily(local_date, requests=1)
        self._store.bump_run(run_id, requests=1)
        self._store.log_call(
            run_id=run_id, kind=kind, listing_id=listing_id, status="ok",
            note=None, requested_at=self._now(),
        )

    def _pace(self, kind: str, moment: datetime) -> None:
        """Sleep like a human: per-call jitter plus an occasional long pause."""
        settings = self._settings
        if kind == "detail":
            low = settings.rental_archive_detail_delay_min_seconds
            high = settings.rental_archive_detail_delay_max_seconds
        else:
            low = settings.rental_archive_list_delay_min_seconds
            high = settings.rental_archive_list_delay_max_seconds
        self._sleep(max(self._uniform(low, high), 0.0))

        if kind == "detail":
            # One listing = one 2s gap; do not insert the list-page long pause.
            return

        self._calls_since_pause += 1
        if self._calls_until_pause <= 0:
            self._calls_until_pause = int(
                self._uniform(
                    settings.rental_archive_pause_every_min,
                    settings.rental_archive_pause_every_max,
                )
            )
        if self._calls_since_pause >= max(self._calls_until_pause, 1):
            self._calls_since_pause = 0
            self._calls_until_pause = 0
            self._sleep(
                max(
                    self._uniform(
                        settings.rental_archive_pause_seconds_min,
                        settings.rental_archive_pause_seconds_max,
                    ),
                    0.0,
                )
            )

    def _session_ready(self) -> bool:
        # Local read only — never triggers an upstream call or a QR login.
        state = self._service.connection_status().state
        return state in (ConnectionState.READY, ConnectionState.EXPIRING)

    def _load_cursor(self) -> ArchiveCursor:
        if self._cursor is None:
            self._cursor = self._store.load_cursor() or ArchiveCursor()
        return self._cursor

    def _save_cursor(self, cursor: ArchiveCursor, moment: datetime) -> None:
        self._cursor = cursor
        self._store.save_cursor(cursor, moment)

    def _local_date(self, moment: datetime) -> str:
        return moment.astimezone(SHANGHAI).date().isoformat()

    def _within_window(self, moment: datetime) -> bool:
        local = moment.astimezone(SHANGHAI).time()
        start = _parse_clock(self._settings.rental_archive_window_start, time(9, 0))
        end = _parse_clock(self._settings.rental_archive_window_end, time(21, 0))
        if start <= end:
            # Same-day window (default 09:00–21:00, working hours only):
            # pause 21:00 inclusive to 09:00 exclusive, i.e. 晚上 9 点到
            # 早上 9 点不跑.
            return start <= local <= end
        # Overnight window (start > end, e.g. the old 09:00–01:00): pause
        # `end` inclusive to `start` exclusive.
        return local >= start or local < end


def _clean(payload: dict[str, object]) -> dict[str, object]:
    cleaned = sanitize_archive_payload(payload)
    return cleaned if isinstance(cleaned, dict) else {}


def _content_hash(listing: RentalListing) -> str:
    """List-level fingerprint: rent, status, cover image, layout, community."""
    parts = (
        listing.monthly_rent_yuan,
        listing.del_status_text,
        listing.title_image_url,
        listing.layout,
        listing.community,
    )
    joined = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _at_cap(cap: int, count: int) -> bool:
    """True when a positive daily cap has been reached. ``cap <= 0`` is unlimited."""
    return cap > 0 and count >= cap


def _parse_clock(raw: str, fallback: time) -> time:
    try:
        hour, minute = (int(part) for part in str(raw).strip().split(":", 1))
        return time(hour=hour, minute=minute)
    except (TypeError, ValueError):
        return fallback
