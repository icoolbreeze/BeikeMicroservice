"""观察期的只读盘点视图（`docs/roughcast-quality-ranking.md` §六 第 1 期出口条件）。

第 1 期的出口条件（**V2.5 改 1 天**）：**连续 1 天产出 COMPLETE 或 PARTIAL run**、
**日志显示实际流量符合第三章**、**每天记录上游 `total` 以观察波动**
（§七.7；11 档切分下每档都重跑，数据滞后 1 天可接受）。
原 V2.4「连续 3 天」已废。三条都只能靠真跑攒，于是观察期里每天都要把
同样几个数从 sqlite 里捞一遍。本模块就是那次捞取。

**只 SELECT。**不发上游请求、不建表、不写一个字节——所以它跑多少遍都不花当日预算，
也不会把 `crawl_log` 的账搅浑（预算是从那张表现算的，见第三章）。
"""

from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, time as dtime, timedelta
from statistics import median
from typing import Callable, Mapping, Sequence

from app.application.roughcast_crawler import RoughcastCrawlConfig
from app.infrastructure.roughcast_repository import COMPLETE, PARTIAL, RoughcastRepository

DEFAULT_RUN_LIMIT = 10
TRAFFIC_DAYS = 7          # 日流量回看天数。1 天观察期 + 余量,又不至于刷爆首屏。
REQUIRED_DAYS = 1         # §六 (V2.5):连续 1 天 COMPLETE 或 PARTIAL 即合规发布。
BREAKER_STATUS = "breaker"

# 变更点率高于这条线就说明 4.5 的哈希还在抖(见 V2.4 修正 5)：稳定期每轮只有
# 少数房源改价改状态,新快照应远低于毛坯总数。
CHANGE_RATE_ALERT = 0.5


# ------------------------------------------------------------------ 数据结构


@dataclass(frozen=True)
class RunLine:
    run_id: int
    queue: str
    status: str
    started_local: datetime
    finished_local: datetime | None
    pages_done: int
    pages_expected: int | None
    request_count: int
    items_seen: int
    upstream_total: int | None
    total_delta: int | None
    roughcast_staged: int
    snapshots_inserted: int
    unknown_fitment: int
    non_roughcast: int
    abort_reason: str | None
    # V2.5:11 档切分每档的判定。`{"0:800": "success", "1200:1500":
    # "skipped_over_cap", ...}`。PARTIAL 时这条会显示失败档。
    bucket_outcomes: Mapping[str, str] | None = None
    # V2.5:覆盖度审计(§三/§4.6):`{requests, rows_returned_total,
    # rows_new_total, dropped_pages}`。`dropped_pages>0` 是深分页硬顶的签名。
    coverage_stats: Mapping[str, int] | None = None

    @property
    def day(self) -> date:
        return self.started_local.date()

    @property
    def duration_minutes(self) -> float | None:
        if self.finished_local is None:
            return None
        return (self.finished_local - self.started_local).total_seconds() / 60.0

    @property
    def published(self) -> bool:
        """COMPLETE / PARTIAL 都算发布过(4.2 规则 1,V2.5 修订)。"""
        return self.status in {COMPLETE, PARTIAL}

    @property
    def leaked_snapshots(self) -> bool:
        """非 COMPLETE/PARTIAL 轮却写进了快照——4.2 明令禁止的越界发布。"""
        return not self.published and self.snapshots_inserted > 0


@dataclass(frozen=True)
class DayLine:
    day: date
    requests: int
    failures: int
    breakers: int
    first_local: datetime | None
    last_local: datetime | None
    gaps: tuple[float, ...]
    interval_violations: int
    outside_window: int
    long_pauses: int
    complete_runs: int

    def _gap(self, pick: Callable[[Sequence[float]], float]) -> float | None:
        return pick(self.gaps) if self.gaps else None

    @property
    def min_gap(self) -> float | None:
        return self._gap(min)

    @property
    def median_gap(self) -> float | None:
        return self._gap(median)

    @property
    def max_gap(self) -> float | None:
        return self._gap(max)


@dataclass(frozen=True)
class BreakerLine:
    at_local: datetime
    run_id: int | None
    queue: str
    note: str | None


@dataclass(frozen=True)
class _RunFacts:
    """出口条件只需要 run 的三件事。与展示用的 `RunLine` 分开,免得为了算「连续几天」
    去查那些只有表格才用得上的派生计数。"""

    day: date
    status: str
    upstream_total: int | None


@dataclass(frozen=True)
class TotalDrift:
    """上游 `total` 的观察样本（§七.7）。ABORTED 轮的第 1 页 total 也算一次测量。"""

    samples: tuple[tuple[int, date, int], ...] = ()   # (run_id, 本地日, total)

    @property
    def values(self) -> tuple[int, ...]:
        return tuple(total for _, _, total in self.samples)

    @property
    def low(self) -> int | None:
        return min(self.values) if self.samples else None

    @property
    def high(self) -> int | None:
        return max(self.values) if self.samples else None

    @property
    def span(self) -> int | None:
        if not self.samples:
            return None
        return self.high - self.low

    @property
    def span_pct(self) -> float | None:
        if not self.samples or not self.low:
            return None
        return self.span / self.low * 100.0

    @property
    def days(self) -> tuple[date, ...]:
        return tuple(sorted({day for _, day, _ in self.samples}))


@dataclass(frozen=True)
class ExitProgress:
    """三条出口条件的攒进度。全部按**固定 7 天窗口**算,不随 `--status N` 变。"""

    complete_days: tuple[date, ...]
    total_days: tuple[date, ...]
    consecutive_complete_days: int
    observed_requests: int
    interval_violations: int
    outside_window: int
    breakers: int

    @property
    def traffic_ok(self) -> bool:
        # 一次请求都没有时不算「符合」——没观察到不等于合规。
        if not self.observed_requests:
            return False
        return not (self.interval_violations or self.outside_window or self.breakers)

    @property
    def ready(self) -> bool:
        return (
            self.consecutive_complete_days >= REQUIRED_DAYS
            and len(self.total_days) >= REQUIRED_DAYS
            and self.traffic_ok
        )


@dataclass(frozen=True)
class StatusReport:
    generated_local: datetime
    runs: tuple[RunLine, ...]
    days: tuple[DayLine, ...]
    breakers: tuple[BreakerLine, ...]
    drift: TotalDrift
    exit_progress: ExitProgress
    totals: dict[str, int]

    @property
    def latest_published(self) -> RunLine | None:
        for line in reversed(self.runs):
            if line.published:
                return line
        return None


# -------------------------------------------------------------------- 组装


def build_status_report(
    repository: RoughcastRepository,
    config: RoughcastCrawlConfig,
    *,
    limit: int = DEFAULT_RUN_LIMIT,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> StatusReport:
    tz = config.timezone
    # 升序:Δ 要跟「上一轮」比,倒序算出来的差会反号。
    runs = list(reversed(repository.recent_runs(limit=limit)))
    run_ids = [int(run["id"]) for run in runs]
    staged = repository.staged_target_counts(run_ids)
    snapshots = repository.snapshot_insert_counts(run_ids)

    lines: list[RunLine] = []
    samples: list[tuple[int, date, int]] = []
    previous_total: int | None = None
    for run in runs:
        run_id = int(run["id"])
        started = _to_local(run["started_at"], tz)
        total = None if run["upstream_total"] is None else int(run["upstream_total"])
        delta = None if total is None or previous_total is None else total - previous_total
        if total is not None:
            samples.append((run_id, started.date(), total))
            previous_total = total
        lines.append(RunLine(
            run_id=run_id,
            queue=str(run["queue"]),
            status=str(run["status"]),
            started_local=started,
            finished_local=_to_local(run["finished_at"], tz),
            pages_done=int(run["pages_done"]),
            pages_expected=run["pages_expected"] and int(run["pages_expected"]),
            request_count=int(run["request_count"]),
            items_seen=int(run["items_seen"]),
            upstream_total=total,
            total_delta=delta,
            roughcast_staged=staged.get(run_id, 0),
            snapshots_inserted=snapshots.get(run_id, 0),
            unknown_fitment=int(run["unknown_fitment_count"]),
            non_roughcast=int(run["non_roughcast_count"]),
            abort_reason=run["abort_reason"],
            bucket_outcomes=repository.bucket_outcomes_for(run_id),
            coverage_stats=repository.coverage_stats_for(run_id),
        ))

    now_local = _to_local_datetime(clock(), tz)
    since = _local_midnight_utc(now_local.date() - timedelta(days=TRAFFIC_DAYS - 1), tz)
    log_rows = repository.requests_since(since)
    # 出口条件与日流量都按这个固定窗口算,与 `limit` 无关:`--status 2` 不该
    # 把「已经连续 1 天」显示成 0 天。
    window_facts = tuple(_run_facts(row, tz) for row in repository.runs_since(since))
    days = _day_lines(log_rows, window_facts, config)
    breakers = tuple(
        BreakerLine(
            at_local=_to_local_datetime(datetime.fromisoformat(row["requested_at"]), tz),
            run_id=row["run_id"] and int(row["run_id"]),
            queue=str(row["queue"]),
            note=row["note"],
        )
        for row in log_rows if row["status"] == BREAKER_STATUS
    )

    return StatusReport(
        generated_local=now_local,
        runs=tuple(lines),
        days=days,
        breakers=breakers,
        drift=TotalDrift(samples=tuple(samples)),
        exit_progress=_exit_progress(window_facts, days, breakers),
        totals=repository.status_totals(),
    )


def _run_facts(row: sqlite3.Row, tz: timedelta) -> _RunFacts:
    return _RunFacts(
        day=_to_local_datetime(datetime.fromisoformat(row["started_at"]), tz).date(),
        status=str(row["status"]),
        upstream_total=None if row["upstream_total"] is None else int(row["upstream_total"]),
    )


def _day_lines(log_rows: Sequence[sqlite3.Row], facts: Sequence[_RunFacts],
               config: RoughcastCrawlConfig) -> tuple[DayLine, ...]:
    tz = config.timezone
    grouped: dict[date, list[tuple[datetime, sqlite3.Row]]] = {}
    for row in log_rows:
        moment = _to_local_datetime(datetime.fromisoformat(row["requested_at"]), tz)
        grouped.setdefault(moment.date(), []).append((moment, row))

    complete_per_day: dict[date, int] = {}
    for fact in facts:
        if fact.status == COMPLETE:
            complete_per_day[fact.day] = complete_per_day.get(fact.day, 0) + 1

    lines: list[DayLine] = []
    for day in sorted(grouped):
        entries = grouped[day]
        breakers = sum(1 for _, row in entries if row["status"] == BREAKER_STATUS)
        # breaker 行不是一次上游请求(是 trip() 写的告警留痕)。预算故意把它算进去
        # ——宁可少花——但看节奏时必须剔掉,否则会伪造出一个 0 秒间隔。
        issued = [moment for moment, row in entries if row["status"] != BREAKER_STATUS]
        gaps = tuple(
            (later - earlier).total_seconds()
            for earlier, later in zip(issued, issued[1:])
        )
        lines.append(DayLine(
            day=day,
            requests=len(issued),
            failures=sum(1 for _, row in entries if row["status"] == "failed"),
            breakers=breakers,
            first_local=issued[0] if issued else None,
            last_local=issued[-1] if issued else None,
            gaps=gaps,
            interval_violations=sum(
                1 for gap in gaps if gap < config.min_request_interval_seconds
            ),
            outside_window=sum(
                1 for moment in issued if not _within_window(moment, config)
            ),
            long_pauses=sum(1 for gap in gaps if gap >= config.long_pause_seconds[0]),
            complete_runs=complete_per_day.get(day, 0),
        ))
    return tuple(lines)


def _exit_progress(facts: Sequence[_RunFacts], days: Sequence[DayLine],
                   breakers: Sequence[BreakerLine]) -> ExitProgress:
    complete_days = tuple(sorted({fact.day for fact in facts if fact.status == COMPLETE}))
    total_days = tuple(sorted(
        {fact.day for fact in facts if fact.upstream_total is not None}
    ))
    return ExitProgress(
        complete_days=complete_days,
        total_days=total_days,
        consecutive_complete_days=_trailing_streak(complete_days),
        observed_requests=sum(day.requests for day in days),
        interval_violations=sum(day.interval_violations for day in days),
        outside_window=sum(day.outside_window for day in days),
        breakers=len(breakers),
    )


def _trailing_streak(days: Sequence[date]) -> int:
    """从最近一天往前数的连续天数。中间断一天就重新起算。"""
    if not days:
        return 0
    streak = 1
    for index in range(len(days) - 1, 0, -1):
        if days[index] - days[index - 1] != timedelta(days=1):
            break
        streak += 1
    return streak


def _within_window(local: datetime, config: RoughcastCrawlConfig) -> bool:
    return config.window_start <= local.time() <= config.window_end


def _to_local_datetime(moment: datetime, tz: timedelta) -> datetime:
    """UTC aware → 本地朴素时间。与 crawler 的 `_local_now` 同一套约定。"""
    return moment.astimezone(UTC).replace(tzinfo=None) + tz


def _to_local(text: str | None, tz: timedelta) -> datetime | None:
    if not text:
        return None
    return _to_local_datetime(datetime.fromisoformat(text), tz)


def _local_midnight_utc(day: date, tz: timedelta) -> datetime:
    return datetime.combine(day, dtime.min, tzinfo=UTC) - tz


# -------------------------------------------------------------------- 渲染


def render_status(report: StatusReport, config: RoughcastCrawlConfig, *,
                  database_path: object | None = None) -> str:
    out: list[str] = [
        f"清水房采集状态 · {report.generated_local:%Y-%m-%d %H:%M}"
        f"（本地 UTC+{config.utc_offset_hours}）"
    ]
    if database_path is not None:
        out.append(f"数据库 : {database_path}")
    for section in (_runs_section, _bucket_section, _drift_section,
                    _traffic_section, _breaker_section, _exit_section,
                    _inventory_section):
        out.append("")
        out.extend(section(report, config))
    return "\n".join(out)


def _runs_section(report: StatusReport, config: RoughcastCrawlConfig) -> list[str]:
    if not report.runs:
        return ["近 0 轮:还没有任何 run。",
                "  先跑一轮:python scripts/roughcast_crawl_once.py"]

    rows = []
    for line in reversed(report.runs):        # 最近的排最上面
        rows.append([
            str(line.run_id),
            line.queue,
            line.status,
            f"{line.started_local:%m-%d %H:%M}",
            "—" if line.duration_minutes is None else f"{line.duration_minutes:.0f}m",
            f"{line.pages_done}/{line.pages_expected if line.pages_expected else '?'}",
            str(line.request_count),
            "—" if line.upstream_total is None else str(line.upstream_total),
            "" if line.total_delta is None else f"{line.total_delta:+d}",
            str(line.roughcast_staged),
            str(line.snapshots_inserted),
            str(line.unknown_fitment),
            str(line.non_roughcast),
        ])
    out = [f"近 {len(report.runs)} 轮"]
    out += _table(
        ["run", "队", "状态", "开始", "耗时", "页数", "请求", "total", "Δ",
         "毛坯", "新快照", "未知", "非毛坯"],
        rows,
        aligns="rllllrrrrrrrr",
    )
    for line in reversed(report.runs):
        if line.abort_reason:
            out.append(f"  run {line.run_id} 未发布:{line.abort_reason}")
        if line.leaked_snapshots:
            out.append(
                f"  ⚠ run {line.run_id} 状态 {line.status} 却写入了 "
                f"{line.snapshots_inserted} 行快照——违反 4.2「非 COMPLETE 绝不发布」,查发布事务"
            )
    out.append("  毛坯 = stage 里 fitment=002 的行数（COMPLETE 轮即发布数）;"
               "新快照 = 4.5 的变更点行数。")
    out.extend(_change_rate_hint(report))
    return out


def _change_rate_hint(report: StatusReport) -> list[str]:
    """4.5 那 36 倍收益是否真的生效——第 2 天起新快照应明显低于毛坯数。"""
    published = [line for line in report.runs if line.published]
    if len(published) < 2:
        return ["  第 2 轮起才能看变更点率(第 1 轮必然是 100%:每套都是新的)。"]
    latest = published[-1]
    if not latest.roughcast_staged:
        return []
    rate = latest.snapshots_inserted / latest.roughcast_staged
    verdict = ("偏高,4.5 的哈希可能还在抖(查数值规范化)"
               if rate > CHANGE_RATE_ALERT else "正常,4.5 的变更点写入在生效")
    return [f"  最近一轮变更点率:{latest.snapshots_inserted}/{latest.roughcast_staged}"
            f" = {rate:.1%} —— {verdict}"]


def _bucket_section(report: StatusReport, config: RoughcastCrawlConfig) -> list[str]:
    """V2.5:11 档切分每档的判定 + 覆盖度审计(§三 / §4.6)。

    COMPLETE 轮这里全 success 是好;PARTIAL 轮这里能看到哪些档被
    `skipped_over_cap` / `bucket_failed` 切掉,以及覆盖度审计的
    `dropped_pages` 是否为 0(`>0` 意味着深分页硬顶仍存在,见
    [[roughcast-deep-paging-cap]])。
    """
    if not report.runs:
        return ["11 档切分(V2.5):尚无 run。"]
    out = ["11 档切分(V2.5)"]
    for line in reversed(report.runs):
        if not line.bucket_outcomes:
            continue
        statuses = line.bucket_outcomes
        success = sum(1 for v in statuses.values() if v == "success")
        over_cap = sum(1 for v in statuses.values() if v == "skipped_over_cap")
        failed = sum(1 for v in statuses.values() if v == "bucket_failed")
        out.append(
            f"  run {line.run_id}({line.status}):"
            f" success={success} / over_cap={over_cap} / failed={failed}"
        )
        if failed or over_cap:
            for bucket, status in statuses.items():
                if status != "success":
                    out.append(f"    {bucket} = {status}")
    latest_published = next(
        (line for line in reversed(report.runs) if line.published), None
    )
    if latest_published and latest_published.coverage_stats:
        cov = latest_published.coverage_stats
        out.append(
            f"  最近一轮覆盖度:crawl_log {cov['requests']} 次 / "
            f"rows_returned={cov['rows_returned_total']} / "
            f"rows_new={cov['rows_new_total']}"
        )
        if cov["dropped_pages"]:
            out.append(
                f"  ⚠ dropped_pages={cov['dropped_pages']}:有请求上行返回了行但没新增,"
                "深分页硬顶([[roughcast-deep-paging-cap]])未根除,需重跑探针"
            )
    return out


def _drift_section(report: StatusReport, config: RoughcastCrawlConfig) -> list[str]:
    drift = report.drift
    if not drift.samples:
        return ["上游 total 波动（§七.7）:暂无样本。"]
    trail = " → ".join(str(value) for value in drift.values[-6:])
    return [
        f"上游 total 波动（§七.7,近 {len(report.runs)} 轮）",
        f"  样本（{len(drift.samples)} 次 / {len(drift.days)} 个本地日）:{trail}",
        f"  区间 {drift.low}–{drift.high},极差 {drift.span}（{drift.span_pct:.2f}%）",
        f"  页数每轮由 total 现算（每页 {config.page_size} 条）,66 不是常数。",
    ]


def _traffic_section(report: StatusReport, config: RoughcastCrawlConfig) -> list[str]:
    if not report.days:
        return [f"日流量（近 {TRAFFIC_DAYS} 个本地日,§三）:crawl_log 无记录。"]
    rows = []
    for day in reversed(report.days):
        span = ("—" if day.first_local is None
                else f"{day.first_local:%H:%M}–{day.last_local:%H:%M}")
        rows.append([
            f"{day.day:%m-%d}",
            f"{day.requests}/{config.daily_request_cap}",
            str(day.complete_runs),
            str(day.failures),
            str(day.breakers),
            span,
            "/".join(_seconds(gap) for gap in (day.min_gap, day.median_gap, day.max_gap)),
            str(day.long_pauses),
            str(day.interval_violations),
            str(day.outside_window),
        ])
    out = [f"日流量（近 {TRAFFIC_DAYS} 个本地日,§三）"]
    out += _table(
        ["日期", "请求/硬顶", "完成轮", "失败", "熔断", "首末",
         "间隔 min/中位/max", "长停顿", "破间隔", "窗口外"],
        rows,
        aligns="lrrrrlcrrr",
    )
    out.append(f"  破间隔 = 相邻请求间隔 < {config.min_request_interval_seconds:.0f}s;"
               f"窗口外 = 落在 {config.window_start:%H:%M}–{config.window_end:%H:%M} 之外;"
               "两者都应恒为 0。")
    return out


def _breaker_section(report: StatusReport, config: RoughcastCrawlConfig) -> list[str]:
    if not report.breakers:
        return [f"熔断记录（近 {TRAFFIC_DAYS} 天）:无。"]
    out = [f"熔断记录（近 {TRAFFIC_DAYS} 天）:{len(report.breakers)} 次"
           " —— 熔断当天剩余任务全部取消,不重试打穿（§三）"]
    for event in report.breakers:
        run = "" if event.run_id is None else f" run {event.run_id}"
        out.append(f"  {event.at_local:%m-%d %H:%M} 队列 {event.queue}{run}"
                   f":{event.note or '(无原因)'}")
    return out


def _exit_section(report: StatusReport, config: RoughcastCrawlConfig) -> list[str]:
    progress = report.exit_progress
    days = progress.complete_days
    tail = ("（" + ", ".join(f"{day:%m-%d}" for day in days[-REQUIRED_DAYS:]) + "）"
            if days else "")
    if not progress.observed_requests:
        traffic = "尚无请求可看"
    elif progress.traffic_ok:
        traffic = "符合"
    else:
        traffic = (f"破间隔 {progress.interval_violations} 次 / "
                   f"窗口外 {progress.outside_window} 次 / 熔断 {progress.breakers} 次")
    out = [
        f"出口条件（§六 第 1 期,近 {TRAFFIC_DAYS} 天）",
        f"  {_mark(progress.consecutive_complete_days >= REQUIRED_DAYS)} 连续 COMPLETE 天数 "
        f"{progress.consecutive_complete_days}/{REQUIRED_DAYS}{tail}",
        f"  {_mark(len(progress.total_days) >= REQUIRED_DAYS)} total 观察天数 "
        f"{len(progress.total_days)}/{REQUIRED_DAYS}",
        f"  {_mark(progress.traffic_ok)} 实际流量符合第三章:{traffic}",
    ]
    out.append("  三条齐了 → 可以开第 3 期(队列 B + 半月轮转 + 参考集 R)。"
               if progress.ready else "  本轮尚未 COMPLETE/PARTIAL,跑完一次即触发。")
    return out


def _inventory_section(report: StatusReport, config: RoughcastCrawlConfig) -> list[str]:
    totals = report.totals
    score_total = totals.get("score_self", 0) + totals.get("score_neighbor", 0) + totals.get("score_fallback", 0)
    score_pct = (totals.get("score_self", 0) / score_total * 100.0) if score_total else 0.0
    return [
        f"库存:在架 {totals['active']} 套 / 已下架 {totals['inactive']} 套 / "
        f"快照 {totals['snapshots']} 行 / 小区 {totals['communities']} 个",
        f"  score_source:self={totals.get('score_self', 0)} "
        f"/ neighbor={totals.get('score_neighbor', 0)} "
        f"/ fallback={totals.get('score_fallback', 0)} "
        f"(self 占比 {score_pct:.1f}% —— 自用排名 ≥30% 可接受,展示前应 ≥60%)",
        f"  community_lookup_status:not_found={totals.get('community_not_found', 0)} 套"
        f"({'高于 5% 查 connector' if totals.get('community_not_found', 0) > 0.05 * totals['active'] else '正常'})",
    ]


def _mark(ok: bool) -> str:
    return "[√]" if ok else "[ ]"


def _seconds(value: float | None) -> str:
    if value is None:
        return "—"
    if value < 90:
        return f"{value:.0f}s"
    return f"{value / 60:.1f}m"


# --------------------------------------------------------------- 等宽小工具
# 中文是双宽字符,`str.ljust` 按码点数补空格会把表头和数据列错开一大截。


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _pad(text: str, width: int, align: str) -> str:
    filler = " " * max(width - _display_width(text), 0)
    if align == "r":
        return filler + text
    if align == "c":
        half = len(filler) // 2
        return filler[:half] + text + filler[half:]
    return text + filler


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]], *, aligns: str) -> list[str]:
    widths = [
        max(_display_width(str(cell)) for cell in (header, *(row[index] for row in rows)))
        for index, header in enumerate(headers)
    ]
    out = ["  " + "  ".join(
        _pad(header, widths[i], aligns[i]) for i, header in enumerate(headers)
    )]
    for row in rows:
        out.append("  " + "  ".join(
            _pad(str(cell), widths[i], aligns[i]) for i, cell in enumerate(row)
        ))
    return out
