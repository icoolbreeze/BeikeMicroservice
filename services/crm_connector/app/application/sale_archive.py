"""买卖 listing localization daemon (enabled by default).

Parallel to the rental archive but its own runner, SQLite file, cursor and
HTTP API. Walks the same 14 Chengdu districts rental localizes the way an
employee browses the workbench — one list page at a time, checking the local
archive *before* spending detail calls, with randomized human-scale pacing
and a daily request cap — inside the same 09:00–21:00 Asia/Shanghai window.
The CRM login is shared with rental; each runner keeps its own lock, so
neither stalls the other.

Product rules enforced here rather than in the client:

* Only the 14 crawled districts (the five 主城 districts plus 近郊 and
  天府新区/高新西/高新/天府新区南区) are ever crawled, always on the ``all``
  scope (不限) with an explicit ``disId`` — never ``gdiv_mt``, never 不限
  (``disId=-1``), never the ten far-suburban districts the plan skips.
* 具体楼层 / 详细地址 stay click-to-view and are never requested; the cheap
  list page's coarse 高/中/低 floor text is what the archive keeps.
* A unit above the upstream 1000-row deep-paging cap is split — 总价, then
  面积, then 室数 — before any later page is requested; a fully-exhausted
  slice sweeps only the reachable 1000 rows and never deactivates.
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
from app.domain.models import (
    ConnectionState,
    SaleListing,
    empty_sale_agent_comment,
    empty_sale_listing_score,
    empty_sale_owner_recommend,
)
from app.infrastructure.sale_archive_plan import (
    DISTRICTS,
    LIST_TOTAL_CAP,
    ArchiveCursor,
    build_filters,
    can_split_unit,
    is_skipped_district,
    last_page,
    needs_unit_split,
    next_crawled_district_index,
    next_page,
    next_unit,
    split_current_unit,
    unit_signature,
)
from app.infrastructure.sale_archive_sanitize import (
    as_archive_dict,
    sanitize_sale_archive_payload,
)
from app.infrastructure.sale_archive_store import (
    DETAIL_STATUS_COMPLETE,
    DETAIL_STATUS_FAILED,
    DETAIL_STATUS_NONE,
    DETAIL_STATUS_SKIPPED,
    LOCALIZED_DETAIL_STATUSES,
    ArchiveListingRow,
    ArchiveRun,
    DailyCounters,
    SaleArchiveStore,
)
from app.infrastructure.settings import Settings

logger = logging.getLogger(__name__)

SHANGHAI = ZoneInfo("Asia/Shanghai")

# 买卖 del_type is always 1; there is no 普租/托管 split and no delType is sent.
DEL_TYPE_SALE = 1

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


class SaleArchiveRunner:
    """Owns the crawl cursor, the pacing budget and the daemon thread."""

    def __init__(
        self,
        settings: Settings,
        service: ConnectorService,
        store: SaleArchiveStore,
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
        # Default sleeper waits on the stop event so a pacing pause never
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
            target=self._loop, name="crm-sale-archive", daemon=True
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
    def store(self) -> SaleArchiveStore:
        return self._store

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                tick = self.run_tick()
            except Exception as exc:  # defensive: the daemon must never die
                logger.exception(
                    "sale archive tick failed class=%s", exc.__class__.__name__
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
        # appended districts show up before their first row; far-suburban
        # SKIPPED_DISTRICT_IDS are out of scope. Completeness is tracked
        # against the plan, not the current DB.
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
            enabled=settings.sale_archive_enabled,
            running=self.running,
            db_path=self._store.db_path,
            window_start=settings.sale_archive_window_start,
            window_end=settings.sale_archive_window_end,
            within_window=self._within_window(moment),
            local_date=self._local_date(moment),
            daily_request_cap=settings.sale_archive_daily_request_cap,
            daily_detail_cap=settings.sale_archive_detail_daily_cap,
            detail_ttl_days=settings.sale_archive_detail_ttl_days,
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

            if not self._settings.sale_archive_enabled:
                return ArchiveTick(False, SKIP_DISABLED, cursor, counters)
            if _at_cap(
                self._settings.sale_archive_daily_request_cap, counters.request_count
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
        signature = unit_signature(cursor.price_band, cursor.extra_filters)
        run_id = cursor.run_id
        if run_id is None:
            run_id = self._store.start_run(
                district_id=cursor.district_id,
                price_band=signature,
                started_at=moment,
            )
            cursor = replace(cursor, run_id=run_id)

        filters = build_filters(
            district_id=cursor.district_id,
            price_band=cursor.price_band,
            page=cursor.page,
            extra_filters=cursor.extra_filters,
        )
        page = self._service.search_sale_listings(filters)
        self._count_request(local_date, run_id, kind="list", listing_id=None)

        over_cap = needs_unit_split(cursor, page.total, cursor.page)
        if over_cap and can_split_unit(cursor):
            # A unit above the upstream's 1000-row deep-paging cap cannot be
            # swept whole: retire the probe run without inactive-marking and
            # restart the unit sliced smaller (总价, then 面积, then 室数).
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
        # Over cap with every split dimension exhausted: sweep the 1000
        # reachable rows, but this run is not a complete census of the unit,
        # so it must never flip unseen rows inactive.
        partial_only = over_cap
        if partial_only:
            logger.warning(
                "sale archive unit over %d-row cap with no split left"
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
            if self._needs_detail(outcome, moment) or self._needs_extra_backfill(
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
        if cursor.page >= last_page(page.total) or not page.items:
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
        """Close a fully swept district/slice and retire listings it never saw."""
        signature = unit_signature(cursor.price_band, cursor.extra_filters)
        if cursor.unit_resumed:
            # The unit was interrupted earlier (or only the reachable cap was
            # swept), so this run's seen-set is not a complete census: never
            # flip inactive off it.
            self._store.finish_run(
                run_id, status=RUN_COMPLETE, finished_at=moment,
                abort_reason="resumed_no_deactivate",
            )
        else:
            self._store.finish_run(run_id, status=RUN_COMPLETE, finished_at=moment)
            self._store.deactivate_unseen(
                district_id=cursor.district_id,
                price_band=signature,
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
            self._settings.sale_archive_detail_daily_cap, counters.detail_count
        ):
            # Detail budget spent for today; keep the cheap list sweep moving.
            cursor = replace(cursor, pending_detail_ids=())
            self._save_cursor(cursor, moment)
            return self._tick_list_page(cursor, moment, local_date)

        listing_id = cursor.pending_detail_ids[0]
        remaining = cursor.pending_detail_ids[1:]
        run_id = cursor.detail_run_id

        try:
            detail = self._fetch_detail(listing_id, run_id, local_date)
        except UpstreamInvalidInputError as exc:
            # Upstream rejects this one listing (stale id, gone from pool):
            # one bad row must not abort the unit. Mark it failed — a later
            # list sweep requeues failed rows — log, and serve the queue tail.
            logger.warning(
                "sale archive detail rejected listing=%s code=%s",
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
            listing_id, status=DETAIL_STATUS_COMPLETE, detail=detail,
            captured_at=moment,
        )
        # One localized listing, regardless of the upstream calls spent.
        self._store.bump_daily(local_date, details=1)
        self._store.bump_run(run_id, details=1)
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

    def _fetch_detail(
        self, listing_id: str, run_id: int | None, local_date: str
    ) -> dict[str, object]:
        """One localized detail = six service calls, seven upstream requests.

        ``get_sale_listing_detail_head`` spends two authorized fetches
        (housedel views + extInfo). Maintain info, follows, the agent
        comment, the owner recommend and the haofang score spend one each.
        The forbidden ``get_sale_listing_detail`` (itself a search by
        del_code) is never called, and neither are exact-floor / address
        reveals or the 证件 tab.
        """
        head = self._service.get_sale_listing_detail_head(listing_id)
        self._count_request(local_date, run_id, kind="detail", listing_id=listing_id)
        self._count_request(local_date, run_id, kind="detail", listing_id=listing_id)
        maintain = self._service.get_sale_listing_maintain_info(listing_id)
        self._count_request(local_date, run_id, kind="detail", listing_id=listing_id)
        follows = self._service.get_sale_listing_follows(listing_id)
        self._count_request(local_date, run_id, kind="detail", listing_id=listing_id)
        comment = self._fetch_optional_block(
            self._service.get_sale_listing_agent_comment,
            listing_id, run_id, local_date, empty_sale_agent_comment,
        )
        owner = self._fetch_optional_block(
            self._service.get_sale_listing_owner_recommend,
            listing_id, run_id, local_date, empty_sale_owner_recommend,
        )
        score = self._fetch_optional_block(
            self._service.get_sale_listing_score,
            listing_id, run_id, local_date, empty_sale_listing_score,
        )
        return _clean(
            {
                "head": as_archive_dict(head),
                "maintain": as_archive_dict(maintain),
                "follows": as_archive_dict(follows),
                "agent_comment": comment,
                "owner_recommend": owner,
                "score": score,
            }
        )

    def _fetch_optional_block(
        self,
        call: Callable[[str], object],
        listing_id: str,
        run_id: int | None,
        local_date: str,
        empty: Callable[[], object],
    ) -> object:
        """One extra detail block. A rejected id stores the empty object.

        Auth and network errors still propagate and fail the tick. The
        request is counted either way, because the upstream call was spent.
        """
        try:
            value = call(listing_id)
        except UpstreamInvalidInputError:
            logger.warning(
                "sale archive optional detail empty listing=%s", listing_id,
            )
            value = empty()
        self._count_request(local_date, run_id, kind="detail", listing_id=listing_id)
        return as_archive_dict(value)

    def _needs_detail(self, outcome, moment: datetime) -> bool:
        if outcome.detail_status not in LOCALIZED_DETAIL_STATUSES:
            return True
        captured = outcome.detail_captured_at
        if captured is None:
            return True
        ttl = timedelta(days=self._settings.sale_archive_detail_ttl_days)
        if moment - captured >= ttl:
            return True
        # Fresh, but the list row itself changed (总价 / cover / layout /
        # community / area): refresh the detail too.
        return outcome.content_changed

    def _needs_extra_backfill(self, listing_id: str) -> bool:
        """One-shot refresh of rows archived before score, comment and owner.

        Read after the list upsert (which never replaces ``payload["detail"]``).
        Only a completed detail missing ``score``, ``agent_comment`` or
        ``owner_recommend`` qualifies. A present key, even an empty object,
        stops the backfill from looping. After that the 14-day TTL applies.
        """
        stored = self._store.get_listing(listing_id)
        if stored is None or stored.detail_status != DETAIL_STATUS_COMPLETE:
            return False
        detail = stored.payload.get("detail")
        if not isinstance(detail, dict):
            return False
        return any(
            key not in detail for key in ("score", "agent_comment", "owner_recommend")
        )

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
            logger.warning("sale archive tick network_retry code=%s", reason)
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
        logger.warning("sale archive tick aborted code=%s", reason)
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
        details must survive it; their discovering run may already be closed,
        so ``detail_run_id`` is intentionally retained too.
        """
        resumed = replace(cursor, run_id=None, unit_resumed=True)
        self._save_cursor(resumed, moment)
        return resumed

    # -- helpers ------------------------------------------------------------

    def _build_row(
        self, listing: SaleListing, cursor: ArchiveCursor
    ) -> ArchiveListingRow:
        signature = unit_signature(cursor.price_band, cursor.extra_filters)
        payload = _clean({"list": as_archive_dict(listing)})
        return ArchiveListingRow(
            listing_id=listing.listing_id,
            district_id=cursor.district_id,
            price_band=signature,
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
            low = settings.sale_archive_detail_delay_min_seconds
            high = settings.sale_archive_detail_delay_max_seconds
        else:
            low = settings.sale_archive_list_delay_min_seconds
            high = settings.sale_archive_list_delay_max_seconds
        self._sleep(max(self._uniform(low, high), 0.0))

        if kind == "detail":
            # One listing = one 2s gap; do not insert the list-page long pause.
            return

        self._calls_since_pause += 1
        if self._calls_until_pause <= 0:
            self._calls_until_pause = int(
                self._uniform(
                    settings.sale_archive_pause_every_min,
                    settings.sale_archive_pause_every_max,
                )
            )
        if self._calls_since_pause >= max(self._calls_until_pause, 1):
            self._calls_since_pause = 0
            self._calls_until_pause = 0
            self._sleep(
                max(
                    self._uniform(
                        settings.sale_archive_pause_seconds_min,
                        settings.sale_archive_pause_seconds_max,
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
        start = _parse_clock(self._settings.sale_archive_window_start, time(9, 0))
        end = _parse_clock(self._settings.sale_archive_window_end, time(21, 0))
        if start <= end:
            # Same-day window (default 09:00–21:00, working hours only):
            # pause 21:00 inclusive to 09:00 exclusive.
            return start <= local <= end
        # Overnight window (start > end): pause `end` inclusive to `start`
        # exclusive.
        return local >= start or local < end


def _clean(payload: dict[str, object]) -> dict[str, object]:
    cleaned = sanitize_sale_archive_payload(payload)
    return cleaned if isinstance(cleaned, dict) else {}


def _content_hash(listing: SaleListing) -> str:
    """List-level fingerprint: 总价, cover image, layout, community, area.

    Deliberately excludes 评分/带看/维护完成度/holder level: those churn on
    every list pull without the listing changing.
    """
    parts = (
        listing.total_price_yuan,
        listing.surface_image_url,
        listing.layout,
        listing.community,
        listing.area_sqm,
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
