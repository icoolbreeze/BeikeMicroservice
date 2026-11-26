# 归档看板：指标与验收

## 本次范围

原生 HTML/JS 看板增加总览、租赁、买卖、视觉识别四个页签。新增接口均为只读，不调用 CRM 或视觉模型；原来的 status、listings、tick 接口契约保留。

本次不提供暂停/恢复、手动 Tick、一键重试或 SSE。控制操作需要独立的权限、审计、可靠入队与限额设计。

## 接口

- GET /api/v1/archive/rental/dashboard
- GET /api/v1/archive/sale/dashboard
- GET /api/v1/archive/{biz}/issues?status=failed&page=1&page_size=20
- GET /api/v1/archive/{biz}/issues?status=skipped_unsupported&district_id=510104
- GET /api/v1/archive/vision/status

门店媒体入口已代理这些 GET 路由。issues 仅支持指定状态、行政区和分页参数，page_size 不超过 50；不返回原始堆栈、上游响应或认证信息。关联日志只输出 ID 和固定错误码，历史记录缺少可用编码时回退为 DETAIL_FAILED。

## 统计口径

dashboard 字段为独立、带 computed_at 的统计快照，按数据库路径和 TTL 缓存 5 秒。新鲜度聚合与最近成功时间使用同一覆盖索引查询，避免读取大型 payload 数据页。首次部署需要构建采集时间覆盖索引及日志关联索引，应为较大的库预留一次索引构建时间，再开始服务验收。

- total：累计业务归档记录；租售相加不代表物理房源去重数。
- active / inactive：源库标记在架 / 非在架；不承诺上游实时可交易状态。
- complete / pending / failed / skipped：全库详情状态。
- active_fresh：在架且详情完成，采集时间有效、不在未来、距离当前时间严格小于 TTL。
- active_stale：在架且详情完成，但已到期、时间缺失/非法或在未来。
- active_pending / active_failed / active_skipped / active_unknown：其余在架详情状态，和 fresh/stale 互斥。
- 成功率只计 complete；已处理率计 complete + skipped。分母为库内记录，不是全市房源。
- 分母为 0 显示未知；未完全完成时向下保留两位小数，不能提前显示 100%。
- 历史计数来自 daily_counters，详情处理含已有房源刷新，不等于新增入库。
- 近期速率根据同一业务日成功详情计数的增量与单调时钟计算；至少三个样本且跨度一分钟。跨日或计数回退重置。
- ETA 仅为已知在架过期与待补积压的粗略处理时间，不包括新增任务、窗口外等待、限额与突发故障。

行政区扫描：旧库没有可靠完整区轮次记录，因此仅标注当前区，其余为 unknown；不使用入库数量、单个价格分片或零记录推断“本轮已完成”。进一步的完整区覆盖需要后续持久化完整轮次事件。

## 运行状态

线程存活、最近游标进展和最近成功详情时间分别展示。正常运行窗口、日请求限额、认证、停用和线程停止分别表达。无进展警戒至少 15 分钟，并覆盖既有长休息间隔；不能把短期限速休息判为故障。

独立视觉 Worker 每 15 秒更新心跳；90 秒未更新显示 stale。正常结束保留 stopped，异常退出保留 error。心跳不等于识图成功，最近成功时间单独展示。

心跳与最近批次分别保存在 archive_vision_meta 的 dashboard:heartbeat / dashboard:batch。视觉数据库尚未建立时，用同路径 .status.json 小文件保存心跳，避免磁盘不足时为监控创建视觉数据库。写入失败不停止业务处理；读接口不创建缺失的数据库。

## 视觉统计边界

- 租赁源库对应视觉存储 biz=rent，买卖为 sale。
- 房源汇总统计通过 listing_id 与源库在架记录关联；图片观测单独计数，不与房源数相加。
- “当前版本匹配”同时检查 model/prompt/schema/rules，不等于已重新核对全部源图片指纹。
- 当前没有可直接查询的全库图片候选索引，因此 total_listings_with_plan、pending_count、effective_valid_count 保留 null，并提供原因。批次 pending 不能当作全库队列长度。
- URL 缓存与 SHA256 内容复用分开计数；model_calls 仅在真正调用模型函数时增加，缓存不算调用。
- average_model_seconds 是逻辑模型调用端到端时间，包含等待和重试；http_attempts 单独记录。
- Token 只累计上游真实返回的 usage；未返回字段为 null。usage_responses 表示用量覆盖的响应数，不能据此推算完整成本。
- 最近批次指标不是终身累计指标；不新增无限增长的调用日志。
- 视觉统计只读取索引字段，不在每次轮询解析全库 payload 或触发图片下载。

## 刷新和交互

当前页签每 15 秒刷新，其他业务约每分钟刷新摘要；总览刷新三类业务。后台页面暂停轮询，重新可见时立即刷新。

前端请求 12 秒主动超时；入口代理超时为 10 秒。失败保留上次数据并提示陈旧，每类业务有独立更新时间。区表支持筛选、排序、按当前筛选计算的加权合计和窄屏横向滚动。文本统一转义，失败抽屉支持分页、Esc 关闭和键盘操作。业务时间统一使用 Asia/Shanghai。

## 验收

从 services/crm_connector 运行：

    python -m pytest -q tests/unit/test_archive_dashboard.py tests/unit/test_archive_vision_worker.py tests/unit/test_archive_vision_boundaries.py tests/unit/test_archive_vision.py tests/unit/test_archive_vision_acceptance.py tests/unit/test_archive_vision_unreadable.py tests/unit/test_independent_archive_acceptance.py tests/unit/test_rental_archive.py tests/unit/test_sale_archive.py tests/unit/test_archive_dashboard_indexes.py tests/integration/test_rental_archive_api.py tests/integration/test_sale_archive_api.py

从 services/store_media 运行：

    python -m pytest -q tests/unit/test_crm_archive_proxy.py

从仓库根目录运行可选浏览器检查（需要 playwright 和浏览器；Windows 使用 Edge）：

    python services/crm_connector/tests/ui/archive_dashboard_smoke.py

浏览器检查使用临时数据库和模拟接口，覆盖完成率边界、分页、转义、键盘切换、超时保留旧数据及恢复、窄屏溢出；不调用真实 CRM/模型。临时目录保存截图和报告。

## 部署与回滚

本次修改涉及三个进程：

1. crm-connector：新增 dashboard/API/telemetry 模块、归档存储索引与看板页面。
2. archive-vision：独立发布目录也必须包含新版 archive_vision.py 和 archive_telemetry.py；仅更新 CRM 网页目录不会更新 Worker。
3. store-media：新增路由代理，需要更新其 app/main.py 并重启入口服务。

CC_ARCHIVE_VISION_DB_PATH 可指定视觉数据库；默认使用租赁归档库同目录下的 archive_vision.sqlite3。生产通常为 /var/lib/crm-connector/archive_vision.sqlite3。不得移动、清空或重建现有归档和凭证库。

先暂存、校验完整代码并备份各发布目录，再切换相关服务；归档游标继续从原持久化目录恢复。三个组件应一起验收。回滚时恢复代码/发布链接，保留归档业务库。新增索引和 metadata 与旧代码兼容。

上线验收应检查：三个服务存活；dashboard/vision/issues 经 8080 返回；心跳连续更新且批次推进；租售游标继续推进；原始 status 接口保留。代码本地验收通过不代表已完成生产部署。
