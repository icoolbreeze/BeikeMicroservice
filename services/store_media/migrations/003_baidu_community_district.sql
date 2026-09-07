-- 003_baidu_community_district.sql
-- 小区行政区改由百度地点检索落库。位置不会变,标过一次就保留。

ALTER TABLE roughcast_communities
    ADD COLUMN district_source TEXT;
--> statement-breakpoint
ALTER TABLE roughcast_communities
    ADD COLUMN baidu_uid TEXT;
--> statement-breakpoint
ALTER TABLE roughcast_communities
    ADD COLUMN baidu_poi_name TEXT;
