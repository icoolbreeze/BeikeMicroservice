# scripts

平台开发脚本目录。

## create_service.py

创建新的微服务骨架（目录与占位文件，不包含任何业务实现）：

```bash
python scripts/create_service.py my_service
# 或使用 Makefile
make new-service NAME=my_service
```

生成结果位于 `services/<service_name>/`，分层约定与 `services/property_verification` 一致。
创建后请在 `docs/service-registry.md` 中登记新服务。

## roughcast_crawl_once.py

手动跑一轮 `store_media` 的清水房全量采集（队列 A，只采集不评分）。
常驻线程默认关闭，第 1 期头几天由人手工触发：

```bash
python scripts/roughcast_crawl_once.py --dry-run   # 只打印装配参数，零上游请求
python scripts/roughcast_crawl_once.py             # 真跑一轮，约 66–76 次上游请求
python scripts/roughcast_crawl_once.py --status    # 只读盘点观察期进度，零上游请求
```

**会打真实 CRM 上游**，一轮 40–90 分钟。`--status`（可带轮数，默认 10）只 SELECT：
近 N 轮 run 摘要、上游 `total` 波动、按本地日的实际流量与熔断记录，并对照第 1 期的三条出口条件。
参数与纪律见
[`services/store_media/README.md`](../services/store_media/README.md#清水房采集第-1-期默认关闭)
与 [`services/store_media/docs/roughcast-quality-ranking.md`](../services/store_media/docs/roughcast-quality-ranking.md)。

## roughcast_queue_b_once.py

手动跑 `store_media` 的小区参考集采集（队列 B）。第一次用 `--full-sweep` 一次性扫完
还没有参考集的小区；中断后续跑会跳过已 COMPLETE 的小区。

```bash
python scripts/roughcast_queue_b_once.py --full-sweep --dry-run
python scripts/roughcast_queue_b_once.py --full-sweep
python scripts/roughcast_queue_b_once.py --status
```

## roughcast_coverage.py

用本地库离线复算三个覆盖率（零上游请求），对照修正前/后两套分级口径：

```bash
python scripts/roughcast_coverage.py
```

## roughcast_mark_districts.py

用百度地点检索给小区打行政区，并写入 BD-09 坐标。百度接口不可用时回退到
贝壳买卖地图联想（同一套百度坐标系，带 `districtName`）。小区位置不会变，
已标记 `district_source` 为 `baidu` / `beike_map` 的行默认跳过。

```bash
python scripts/roughcast_mark_districts.py --limit 5
python scripts/roughcast_mark_districts.py
```

## roughcast_assign_districts.py

把小区归到行政区并写入本地库。先拉一次 CRM「区 → 商圈」目录，再按小区已有
`bizcircle` 填写 `roughcast_communities.district` 和
`roughcast_community_district`。之后本地 SQL 即可按区分析，不必再打 CRM。

```bash
python scripts/roughcast_assign_districts.py --dry-run
python scripts/roughcast_assign_districts.py
```

## roughcast_shadow_score.py

第 4 期 Shadow Run：用本地 P + 指针 R 算优质指数并写入 `listing_scores`，**不上前端**。

```bash
python scripts/roughcast_shadow_score.py --dry-run
python scripts/roughcast_shadow_score.py
```

## start_store_media.ps1

`store_media` 的 Windows 本地长跑入口（模仿 `start_crm_connector.ps1`）。
`SM_ROUGHCAST_CRAWL_ENABLED=1` 启动 uvicorn，**只在那个进程里**打开开关，
不动仓库里的 `.env.example`。如果 8010 已经在监听（通常是老进程没退干净），
脚本直接打印「already listening」并 `exit 0`，不杀活进程。修法是先停掉老进程
再重跑，否则新代码里的 `roughcast-daily-loop` 与 ranked API 不会被加载。

```powershell
..\..\scripts\start_store_media.ps1
```

云端 systemd / docker-compose 不在这条路径上——本脚本是 Windows 本地专用的。
