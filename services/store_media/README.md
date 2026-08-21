# store_media

门店多媒体广告发布、房源轮播与角色管理微服务。服务可独立部署，默认监听 `8010`。

## 功能

- 管理员、区域经理、店长上传和维护所属范围内的图片或视频，上传时默认立即发布；
- 图片可设置 1–3600 秒停留时长，视频播放完毕后自动切换；
- 标题、图片时长、顺序、发布状态和待删除项通过一个事务统一保存；
- 展示端不叠加任何文字，每 30 秒同步一次清单，图片底边显示低亮度进度条；
- 展示端预加载并复用媒体节点，已发布文件使用不可变资源缓存，避免每轮切换重复加载；
- 系统管理员、区域经理、店长、店员四级 RBAC，权限按区域和门店隔离；
- SQLite 元数据、密码 PBKDF2 哈希、随机不透明会话令牌、本地媒体持久化；
- 管理页 `/`，门店展示页 `/display.html?store_id=<门店标识>`，精选房源大屏 `/featured.html`，OpenAPI `/docs`。

## 本地启动

```powershell
cd services/store_media
$env:SM_BOOTSTRAP_ADMIN_USERNAME="admin"
$env:SM_BOOTSTRAP_ADMIN_PASSWORD="请设置至少8位的随机密码"
python -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8010
```

首次成功启动会在空数据库中创建系统管理员。随后应移除引导账号环境变量；已有用户时不会重复创建。
生产环境建议把 `SM_STORAGE_DIR` 指向持久卷。SQLite 适合单实例初期部署；多实例部署前应按架构文档迁移至 PostgreSQL 和对象存储。

## 权限边界

| 角色 | 管理范围 |
| --- | --- |
| 系统管理员 | 全部区域、门店、账号和内容 |
| 区域经理 | 所属区域内门店、店长、店员和内容 |
| 店长 | 所属门店店员和内容 |
| 店员 | 登录并查看所属门店，无发布权限 |

## 毛坯房浏览（内部）

手机页面：`/roughcast.html`。它只展示 CRM 租赁房源中的“毛坯”结果：
服务端固定请求 `scope=all`、`fitment=002`，每页 30 条；页面下拉到末尾后按页码自动加载，
在列表顶部下拉可刷新。页码之外不接受任何页面筛选参数。点击房源卡片可查看该房源的 `REAL` 实勘图片，图片由
展示服务代理、清洗后返回，浏览器不会接触 CRM 凭证或实勘上传人等内部字段。

展示服务通过 `SM_CRM_CONNECTOR_BASE_URL`（默认 `http://127.0.0.1:8020`）调用已认证的
`crm_connector`；浏览器不会获得 CRM 凭证。可用 `SM_ROUGHCAST_CACHE_SECONDS` 调整服务端
缓存时间，默认 60 秒。

## 清水房采集（第 1 期，默认关闭）

按 [`docs/roughcast-quality-ranking.md`](docs/roughcast-quality-ranking.md) 的第 1 期范围，
本服务内置一条**只采集、不评分**的清水房全量采集链路（队列 A）。它与上面的展示接口互不影响：
展示走实时请求 + 内存缓存，采集写自己的 6 张 `roughcast_*` 表。

**默认不跑。** `SM_ROUGHCAST_CRAWL_ENABLED` 未打开时不起线程、不发一次上游请求。
头几天先手动跑，确认上游流量曲线之后再打开常驻线程：

```powershell
python ..\..\scripts\roughcast_crawl_once.py --dry-run   # 只打印装配参数，零上游请求
python ..\..\scripts\roughcast_crawl_once.py             # 真跑一轮，约 66–76 次请求 / 40–90 分钟
```

一轮 = 一个 `roughcast_crawl_runs` 行，终态只有 `COMPLETE` / `ABORTED` / `FAILED`，
且**只有 `COMPLETE` 会发布**（刷新 `roughcast_listing_current`、写变更点快照、判下架），
发布的全部动作在一个事务里。中止或失败的一轮，数据止步于 `roughcast_crawl_stage`，
对 current / snapshot 一个字节都不碰。

上游负载控制（全部经由 `roughcast_throttle`，任何新增请求类型自动计入）：

| 环境变量 | 默认 | 含义 |
| --- | --- | --- |
| `SM_ROUGHCAST_CRAWL_ENABLED` | `0` | 常驻线程开关。关闭时仍可用上面的手动入口 |
| `SM_ROUGHCAST_DAILY_REQUEST_CAP` | `260` | 当日请求硬顶。任何推导预算都不得越过 |
| `SM_ROUGHCAST_MIN_REQUEST_INTERVAL_SECONDS` | `20` | 全局最小请求间隔 |
| `SM_ROUGHCAST_CRAWL_WINDOW` | `09:30,19:00` | 采集窗口（本地时间），出窗口即判 `ABORTED` |
| `SM_ROUGHCAST_CRAWL_PAGE_SIZE` | `50` | 每页条数。页数由第 1 页的 `total` 现算，不是常数 |
| `SM_ROUGHCAST_CRAWL_SAFETY_FACTOR` | `1.15` | 预算安全系数 |
| `SM_ROUGHCAST_CRAWL_RETRY_RESERVE` | `10` | 重试预留次数（重试同样扣预算） |
| `SM_ROUGHCAST_PAGE_GAP_SECONDS` | `25,90` | 页间随机停顿 |
| `SM_ROUGHCAST_LONG_PAUSE_EVERY_PAGES` | `8,15` | 每隔多少页插一次长停顿 |
| `SM_ROUGHCAST_LONG_PAUSE_SECONDS` | `180,480` | 长停顿时长 |

当日已花额度**从 `roughcast_crawl_log` 现算**，不是内存计数器——进程重启不会忘记今天的账。
遇到 429、`CRM_AUTH_REQUIRED`、`CRM_UPSTREAM_CHANGED` 或连续 3 次失败即熔断：当天剩余任务
全部取消并告警，绝不重试打穿。采集阶段不打详情接口（`RoughcastCrawlClient` 只有搜索方法）。

## 测试

```powershell
python -m pytest
```

## 服务器发布

生产 systemd 单元默认监听 `8080`，部署目录、持久化目录和验证命令见 [`deploy/README.md`](deploy/README.md)。
Docker Compose 同样默认将宿主机 `8080` 映射到容器 `8010`，可通过 `SM_PUBLIC_PORT` 修改宿主端口。
