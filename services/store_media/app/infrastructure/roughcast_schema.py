"""第 1 期库表（清水房优质指数排名 · 只采集不评分）。

对照 `docs/roughcast-quality-ranking.md` V2.5 §4.1。本期只建 6 张表：
`crawl_runs / crawl_log / crawl_stage / listing_current / listing_snapshot / communities`。
`community_reference_snapshot` 留到第 3 期，`community_month_benchmark` 与
`listing_scores` 留到第 4 期——`CREATE TABLE IF NOT EXISTS` 是纯追加，延后零代价。
"""

from __future__ import annotations

# 与 current / snapshot 同构的业务字段。抽成常量是因为 4.1 要求 snapshot
# 「与 current 同构」，两处手抄一份迟早漂移。
#
# bizcircle 由 connector 的 bizCircleName 映射而来（改动 E，2026-08-20 落地）。
# 队列 A 每天重扫全量且 current 表按全业务列 upsert，所以该列在 E 落地后的
# 第一轮就对全部活跃房源填满，不必为补历史另买一轮请求。它不在 HASH_FIELDS
# 里，因此加它不会让快照表把每一行都判成变更点。
#
# V2.5 起新加的 community_lookup_status / score_source 见 migrations/001——
# 不放在这里是为了让「全新部署」和「老库升级」共用 schema.py + 一次迁移的两条
# 路径都能跑通,schema 文本里只放所有路径都已存在的列,新列交由迁移来加。
_BUSINESS_COLUMNS = """
    community_name      TEXT NOT NULL,
    community_id        TEXT,
    resblock_id         TEXT,
    bizcircle           TEXT,
    layout              TEXT,
    rooms               INTEGER,
    halls               INTEGER,
    baths               INTEGER,
    area_sqm            REAL,
    monthly_rent_yuan   REAL,
    orientation         TEXT,
    floor_desc          TEXT,
    total_floors        INTEGER,
    rent_mode           TEXT,
    del_type            INTEGER,
    fitment_status      TEXT,
    fitment_status_desc TEXT,
    create_time         TEXT,
    title_image_url     TEXT
"""

ROUGHCAST_SCHEMA_SQL = f"""
-- 一轮采集的事务边界。RUNNING → COMPLETE | PARTIAL | ABORTED | FAILED。
-- PARTIAL 是 V2.5 新增终态:11 档切分里 ≥1 档成功 + ≥1 档 totalCount ≥ 1000
-- 失败时落 PARTIAL,成功档照常发布,失败档的 stage 行作废(见 §4.2)。
-- planned_buckets / bucket_outcomes 由 migrations/001 加入。
CREATE TABLE IF NOT EXISTS roughcast_crawl_runs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    queue                 TEXT    NOT NULL,          -- 'A' | 'B'
    status                TEXT    NOT NULL,          -- RUNNING / COMPLETE / PARTIAL / ABORTED / FAILED
    started_at            TEXT    NOT NULL,
    finished_at           TEXT,
    pages_expected        INTEGER,
    pages_done            INTEGER NOT NULL DEFAULT 0,
    items_seen            INTEGER NOT NULL DEFAULT 0,
    request_count         INTEGER NOT NULL DEFAULT 0,
    upstream_total        INTEGER,                   -- 第 1 页返回的 total,§七.7 的观察依据
    unknown_fitment_count INTEGER NOT NULL DEFAULT 0,
    non_roughcast_count   INTEGER NOT NULL DEFAULT 0,
    abort_reason          TEXT
);
CREATE INDEX IF NOT EXISTS idx_roughcast_runs_status
    ON roughcast_crawl_runs(queue, status, started_at);

-- 节流审计与熔断依据。每一次真实上游请求一行 —— 当日预算就是这里的行数。
-- V2.5 加 rows_returned / rows_new:前者是上游本次返回的行数,后者是其中
-- 实际新增到 stage 的行数。rows_new=0 且 rows_returned>0 是深分页硬顶的
-- 签名,run 1 漏 70% 就是这种病灶,这两列没记就没人能看出来。
-- 由 migrations/001 加入。
CREATE TABLE IF NOT EXISTS roughcast_crawl_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER REFERENCES roughcast_crawl_runs(id),
    queue        TEXT    NOT NULL,                  -- 'A' | 'B'
    target       TEXT    NOT NULL,                  -- 'bucket=0:800&page=1' / 'resblock=3011...'
    requested_at TEXT    NOT NULL,
    status       TEXT    NOT NULL,                  -- issued / ok / failed / breaker / ...
    http_status  INTEGER,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS idx_roughcast_crawl_log_day
    ON roughcast_crawl_log(requested_at);

-- 采集期落地区。RUNNING 期间数据唯一的出口(4.2 规则 1):ABORTED / FAILED
-- 的 run 到此为止,snapshot 与 current 一个字节都碰不到。
-- V2.5 加 bucket:11 档切分后发布按 bucket 过滤(PARTIAL 时只发成功档)。
-- 旧数据走 DEFAULT '0:+inf'——单一查询时代只有这一档。由 migrations/001 加入。
CREATE TABLE IF NOT EXISTS roughcast_crawl_stage (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL REFERENCES roughcast_crawl_runs(id),
    listing_id     TEXT    NOT NULL,
    content_hash   TEXT    NOT NULL,
    fitment_status TEXT,                            -- 原值,不是布尔(4.1)
    payload_json   TEXT    NOT NULL,
    seen_at        TEXT    NOT NULL,
    UNIQUE (run_id, listing_id)
);
CREATE INDEX IF NOT EXISTS idx_roughcast_stage_run
    ON roughcast_crawl_stage(run_id);

-- 小区档案与轮转状态。本期只写 id/name/resblock_id/bizcircle/roughcast_count/
-- first_seen_at;reference_* 与 refresh_* 是队列 B(第 3 期)的字段,建列不写。
CREATE TABLE IF NOT EXISTS roughcast_communities (
    id                TEXT    PRIMARY KEY,          -- resblock_id,或 name:<sha1[:16]> 占位
    name              TEXT    NOT NULL,
    resblock_id       TEXT,
    bizcircle         TEXT,
    district          TEXT,                         -- 行里没有,本期恒 NULL
    latitude          REAL,                         -- 同上(§七.5 已定案放弃坐标)
    longitude         REAL,
    roughcast_count   INTEGER NOT NULL DEFAULT 0,
    reference_run_id  INTEGER,
    refreshed_at      TEXT,
    next_refresh_at   TEXT,
    refresh_fail_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at     TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_roughcast_communities_resblock
    ON roughcast_communities(resblock_id) WHERE resblock_id IS NOT NULL;

-- 清水房当前态,唯一真相。只在 COMPLETE / PARTIAL 事务里被刷新。
-- community_lookup_status / score_source 由 migrations/001 加入。
CREATE TABLE IF NOT EXISTS roughcast_listing_current (
    listing_id      TEXT    PRIMARY KEY,
{_BUSINESS_COLUMNS},
    content_hash    TEXT    NOT NULL,
    first_seen_at   TEXT    NOT NULL,               -- 只是下界,不是上架时间(4.4)
    last_seen_at    TEXT    NOT NULL,               -- 只用于下架判定(4.4)
    last_seen_run_id INTEGER NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_roughcast_current_community
    ON roughcast_listing_current(community_id, is_active);
CREATE INDEX IF NOT EXISTS idx_roughcast_current_active
    ON roughcast_listing_current(is_active, last_seen_run_id);

-- 清水房历史,只写变更点(4.5)。captured_at 与 last_confirmed_at 构成闭区间;
-- 新鲜度算 last_confirmed_at,不是 captured_at。
-- V2.5 起 snapshot 也带 community_lookup_status / score_source,由
-- migrations/001 加入。
CREATE TABLE IF NOT EXISTS roughcast_listing_snapshot (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id            TEXT    NOT NULL,
    captured_at           TEXT    NOT NULL,          -- 这个状态开始的时间
    captured_run_id       INTEGER NOT NULL,
    last_confirmed_at     TEXT    NOT NULL,          -- 最后一次确认它仍是这个状态
    last_confirmed_run_id INTEGER NOT NULL,
    content_hash          TEXT    NOT NULL,
{_BUSINESS_COLUMNS}
);
CREATE INDEX IF NOT EXISTS idx_roughcast_snapshot_listing
    ON roughcast_listing_snapshot(listing_id, captured_at);
"""
