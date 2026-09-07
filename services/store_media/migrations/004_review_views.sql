-- 004_review_views.sql
-- 核分页浏览记录：打开工作台后标记已看，并累计次数。

CREATE TABLE IF NOT EXISTS roughcast_review_views (
    listing_id      TEXT    PRIMARY KEY,
    view_count      INTEGER NOT NULL DEFAULT 0,
    first_viewed_at TEXT    NOT NULL,
    last_viewed_at  TEXT    NOT NULL
);
