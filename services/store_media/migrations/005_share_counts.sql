-- 005_share_counts.sql
-- 分享海报被调用的次数累计。
-- 表结构同 roughcast_review_views,只用 share_count / first_shared_at /
-- last_shared_at,跟"已看 n 次"对称。多次分享同一 listing 持续累加。
-- 自增放在路由 /api/v1/display/roughcast-ranked/{listing_id}/share 调通
-- 时同步 +1;前端读 X-Share-Count response header 拿到新值。

CREATE TABLE IF NOT EXISTS roughcast_share_counts (
    listing_id      TEXT    PRIMARY KEY,
    share_count     INTEGER NOT NULL DEFAULT 0,
    first_shared_at TEXT    NOT NULL,
    last_shared_at  TEXT    NOT NULL
);
