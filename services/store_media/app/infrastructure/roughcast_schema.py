"""清水房优质指数排名的库表。

对照 `docs/roughcast-quality-ranking.md` V2.5 §4.1。
第 1 期 6 张表：`crawl_runs / crawl_log / crawl_stage / listing_current /
listing_snapshot / communities`。
第 3 期追加 `community_reference_snapshot`（队列 B 参考集 R）。
第 4 期追加 `score_runs` / `listing_scores`。`community_month_benchmark` 仍可后置。
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

-- 小区档案与轮转状态。队列 A 写 id/name/resblock_id/bizcircle/roughcast_count/
-- first_seen_at;队列 B 写 reference_* 与 refresh_*。
CREATE TABLE IF NOT EXISTS roughcast_communities (
    id                TEXT    PRIMARY KEY,          -- resblock_id,或 name:<sha1[:16]> 占位
    name              TEXT    NOT NULL,
    resblock_id       TEXT,
    bizcircle         TEXT,
    district          TEXT,                         -- 商圈目录唯一命中时写入;歧义/未知为 NULL
    -- district_status / districts_json / district_assigned_at 见 migrations/002
    latitude          REAL,                         -- 百度地点检索写入 BD-09
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

-- 商圈 → 行政区目录快照。来源是 CRM rental filter-options 的 districtId 树。
-- 由 scripts/roughcast_assign_districts.py 整表替换;队列 A 发布时按这份目录回填小区。
CREATE TABLE IF NOT EXISTS roughcast_bizcircle_district (
    bizcircle    TEXT NOT NULL,
    district     TEXT NOT NULL,
    captured_at  TEXT NOT NULL,
    PRIMARY KEY (bizcircle, district)
);
-- 小区 ↔ 行政区。唯一商圈一行;跨区商圈多行。本地 `WHERE district=` 走这里。
CREATE TABLE IF NOT EXISTS roughcast_community_district (
    community_id TEXT NOT NULL,
    district     TEXT NOT NULL,
    PRIMARY KEY (community_id, district)
);
CREATE INDEX IF NOT EXISTS idx_roughcast_communities_district
    ON roughcast_communities(district);
CREATE INDEX IF NOT EXISTS idx_roughcast_community_district_district
    ON roughcast_community_district(district);

-- 核分页「打开贝壳」浏览记录。
CREATE TABLE IF NOT EXISTS roughcast_review_views (
    listing_id      TEXT    PRIMARY KEY,
    view_count      INTEGER NOT NULL DEFAULT 0,
    first_viewed_at TEXT    NOT NULL,
    last_viewed_at  TEXT    NOT NULL
);

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

-- 小区参考房源快照,按批次 append。评分只读 `reference_run_id` 指向的那一批,
-- 禁止 `max(run_id)`——那会读到 ABORTED 的半截批次(§4.1 / §4.2)。
-- fitment_status 存原值;is_roughcast 是派生布尔,两者都留(§七.8 / V2.4)。
-- 本表收录该小区搜索到的**全部**在租行(P / R / 装修未知),发布时不过滤,
-- 评分再按 fitment 三分。空装修行必须落库计数,不得静默丢弃。
CREATE TABLE IF NOT EXISTS roughcast_community_reference_snapshot (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    community_id        TEXT    NOT NULL,
    run_id              INTEGER NOT NULL REFERENCES roughcast_crawl_runs(id),
    listing_id          TEXT    NOT NULL,
    rent_mode           TEXT,
    rooms               INTEGER,
    halls               INTEGER,
    baths               INTEGER,
    area_sqm            REAL,
    monthly_rent_yuan   REAL,
    unit_rent           REAL,
    orientation         TEXT,
    is_roughcast        INTEGER NOT NULL,
    fitment_status      TEXT,
    captured_at         TEXT    NOT NULL,
    UNIQUE (run_id, listing_id)
);
CREATE INDEX IF NOT EXISTS idx_roughcast_ref_snapshot_community
    ON roughcast_community_reference_snapshot(community_id, run_id);

-- 第 4 期:一次 Shadow / 正式评分的批次。
CREATE TABLE IF NOT EXISTS roughcast_score_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    status           TEXT    NOT NULL,
    started_at       TEXT    NOT NULL,
    finished_at      TEXT,
    model_version    TEXT    NOT NULL,
    delta_version    INTEGER NOT NULL,
    delta_value      REAL,
    k_scale          REAL    NOT NULL,
    listing_run_id   INTEGER,
    scored_count     INTEGER NOT NULL DEFAULT 0,
    nearby_count     INTEGER NOT NULL DEFAULT 0,
    insufficient_count INTEGER NOT NULL DEFAULT 0,
    data_error_count INTEGER NOT NULL DEFAULT 0,
    extreme_count    INTEGER NOT NULL DEFAULT 0,
    delta_note       TEXT,
    abort_reason     TEXT
);

CREATE TABLE IF NOT EXISTS roughcast_listing_scores (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id               TEXT    NOT NULL,
    score_run_id             INTEGER NOT NULL REFERENCES roughcast_score_runs(id),
    listing_run_id           INTEGER,
    reference_run_id         INTEGER,
    model_version            TEXT    NOT NULL,
    delta_version            INTEGER NOT NULL,
    delta_value              REAL    NOT NULL,
    unit_rent                REAL,
    reference_unit_rent      REAL,
    expected_unit_rent       REAL,
    advantage                REAL,
    quality_score_raw        REAL,
    quality_score            INTEGER,
    quality_status           TEXT    NOT NULL,
    quality_tier             TEXT,
    confidence_score         INTEGER,
    city_rank                INTEGER,
    peer_scope               TEXT,
    comparable_grade         TEXT,
    benchmark_mode           TEXT,
    effective_sample_count   REAL,
    reference_age_days       INTEGER,
    reference_community_count INTEGER,
    reference_spread         REAL,
    extreme_price            INTEGER NOT NULL DEFAULT 0,
    reason                   TEXT,
    benchmark_pool_json      TEXT,
    computed_at              TEXT    NOT NULL,
    UNIQUE (score_run_id, listing_id)
);
CREATE INDEX IF NOT EXISTS idx_roughcast_scores_run_rank
    ON roughcast_listing_scores(score_run_id, quality_status, city_rank);
"""
