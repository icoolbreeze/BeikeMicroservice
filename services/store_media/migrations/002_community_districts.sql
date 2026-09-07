-- 002_community_districts.sql
-- 把 CRM「区 → 商圈」目录和小区行政区归类落库,供本地查询。
-- district 列原本就在 communities 表里,一直是空的。

ALTER TABLE roughcast_communities
    ADD COLUMN district_status TEXT;
--> statement-breakpoint
ALTER TABLE roughcast_communities
    ADD COLUMN districts_json TEXT;
--> statement-breakpoint
ALTER TABLE roughcast_communities
    ADD COLUMN district_assigned_at TEXT;
--> statement-breakpoint

CREATE TABLE IF NOT EXISTS roughcast_bizcircle_district (
    bizcircle    TEXT NOT NULL,
    district     TEXT NOT NULL,
    captured_at  TEXT NOT NULL,
    PRIMARY KEY (bizcircle, district)
);
--> statement-breakpoint

CREATE TABLE IF NOT EXISTS roughcast_community_district (
    community_id TEXT NOT NULL,
    district     TEXT NOT NULL,
    PRIMARY KEY (community_id, district)
);
--> statement-breakpoint

CREATE INDEX IF NOT EXISTS idx_roughcast_communities_district
    ON roughcast_communities(district);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS idx_roughcast_community_district_district
    ON roughcast_community_district(district);
