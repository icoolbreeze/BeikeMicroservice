-- 001_add_bucket_and_status_columns.sql
-- V2.5 schema 升级:11 档切分 + 标记字段 + 覆盖度审计列。
-- 对应 docs/roughcast-quality-ranking.md §4.1 / §4.2。
--
-- IF NOT EXISTS 让脚本在 V2.5 schema(空库)上重复运行也是空操作。
-- 在 V2.4 schema(已存在 6 张表)上跑这些 ALTER 才是它的本职。
--
-- 注意:SQLite 的 ALTER TABLE ADD COLUMN 不支持 NOT NULL DEFAULT 之外的复杂
-- 表达式;这里 DEFAULT 全部给到字面量,旧行的填充是瞬时且不阻塞的。

-- 1. crawl_runs:状态机加 PARTIAL 终态的字段(枚举值没改,只是 status 列是 TEXT)
--    实际不需 ALTER:status 已经是 TEXT,接受新枚举值。补 planned_buckets /
--    bucket_outcomes 两条。
ALTER TABLE roughcast_crawl_runs
    ADD COLUMN planned_buckets TEXT NOT NULL DEFAULT '[]';
--> statement-breakpoint
ALTER TABLE roughcast_crawl_runs
    ADD COLUMN bucket_outcomes TEXT;
--> statement-breakpoint

-- 2. crawl_log:覆盖度审计的两列。
--    run 1 的 67 行缺 rows_returned / rows_new,会在 --status 里显示为 0
--    ——这是已知病灶,迁移后第 2 轮起恢复正常。
ALTER TABLE roughcast_crawl_log
    ADD COLUMN rows_returned INTEGER NOT NULL DEFAULT 0;
--> statement-breakpoint
ALTER TABLE roughcast_crawl_log
    ADD COLUMN rows_new INTEGER NOT NULL DEFAULT 0;
--> statement-breakpoint

-- 3. crawl_stage:加 bucket 列,旧行走 DEFAULT '0:+inf'(单一查询时代)。
ALTER TABLE roughcast_crawl_stage
    ADD COLUMN bucket TEXT NOT NULL DEFAULT '0:+inf';
--> statement-breakpoint

-- 4. listing_current / listing_snapshot:业务列加 community_lookup_status / score_source。
--    旧行:lookup_status = 'not_found'(无 resblock_id 是历史常态),
--          score_source = 'fallback'(历史小区数据未参与新计算)。
ALTER TABLE roughcast_listing_current
    ADD COLUMN community_lookup_status TEXT NOT NULL DEFAULT 'not_found';
--> statement-breakpoint
ALTER TABLE roughcast_listing_current
    ADD COLUMN score_source TEXT NOT NULL DEFAULT 'fallback';
--> statement-breakpoint
ALTER TABLE roughcast_listing_snapshot
    ADD COLUMN community_lookup_status TEXT NOT NULL DEFAULT 'not_found';
--> statement-breakpoint
ALTER TABLE roughcast_listing_snapshot
    ADD COLUMN score_source TEXT NOT NULL DEFAULT 'fallback';
--> statement-breakpoint

-- 5. 索引:5.1 文档已说明的几条。
CREATE INDEX IF NOT EXISTS idx_roughcast_stage_bucket
    ON roughcast_crawl_stage(run_id, bucket);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS idx_roughcast_current_lookup
    ON roughcast_listing_current(community_lookup_status);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS idx_roughcast_current_score_source
    ON roughcast_listing_current(score_source, last_seen_run_id);
