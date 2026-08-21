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
