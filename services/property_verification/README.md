# property_verification

房源信息验证微服务 —— BeikeMicroservice 平台中的一个**独立服务**。

> 本服务只是平台微服务集合中的一员，不代表整个仓库。

## 职责

- 接收不动产权证图片；
- 提取业务件号、证件编码（视觉模型，顺序主备策略）：
  - 主模型 `nvidia/nemotron-nano-12b-v2-vl:free`（OpenRouter，免费档）
  - 失败（连接异常或未识别出有效字段）自动重试 2 次；仍失败由兜底模型
    `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`（NVIDIA build.nvidia.com）接管；
  - 兜底也失败则启用第二兜底 `stepfun-ai/step-3.7-flash`（NVIDIA build.nvidia.com）；
- 调用**合法授权**的房源信息验证渠道（住建蓉e办）；
- 生成查询结果截图（不同规格，可下载）；
- 提供用户入口（PC / 手机自适应），实时反馈进度与错误。

## 安全机制

- **无需登录**：用户直接上传证件即可验证；
- **IP 限流**：同一 IP 默认 2 次/分钟、30 次/天（可经 `PV_RATE_PER_MIN` / `PV_RATE_PER_DAY` 调整）；
- 视觉模型账户级预算：OpenRouter 与 NVIDIA 各自独立限流；
- 上传件校验：仅 JPEG/PNG、≤10MB、魔数嗅探、文件名规范化；
- 上传原件核验后即删；产物存于 `storage/verify_jobs/`（已 gitignore，不入库）；
- 任务 ID 为 uuid4，对未登录用户即作为访问凭据（不可猜测）。

## API（v1）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/verification` | 上传证件（multipart `file`），返回 `job_id`（202） |
| GET | `/api/v1/verification/stats` | 服务累计受理次数，供页面顶部展示 |
| GET | `/api/v1/verification/{id}/events` | SSE 实时进度/错误流，至终态关闭 |
| GET | `/api/v1/verification/{id}/result` | 结构化核验结果（官方查询表格解析：headers/row/fields/结论） |
| GET | `/api/v1/verification/{id}/artifacts` | 产物清单 |
| GET | `/api/v1/verification/{id}/download/{spec}` | 下载 `panel` / `full` / `zip` |
| GET | `/api/v1/jobs/{id}` | 任务状态快照 |
| GET | `/api/v1/health` | 健康检查 |

## 快速开始

1. 安装依赖（在仓库托管 Python 环境中）：
   ```bash
   pip install fastapi "uvicorn[standard]" python-multipart pydantic pillow requests numpy playwright mcp
   python -m playwright install chromium
   ```
2. 配置环境变量：复制 `.env.example` 为 `.env`，填入 `OPENROUTER_API_KEY` 与 `NVIDIA_API_KEY`（至少其一）。
3. 在**服务目录**下启动：
   ```bash
   cd services/property_verification
   uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
   ```
   或在仓库根执行 `make serve-pv`。
4. 浏览器打开 `http://localhost:8000/`，上传证件即可。

## MCP（pv-mcp）

本服务附带一个 MCP stdio 服务（`app.mcp.server`），作为**已部署服务的薄客户端**，
将查档核验能力提供给 agent：

| 工具 | 说明 |
|---|---|
| `pv_verify_submit(image_path)` | 上传本地产权证图片，返回 `job_id`（异步） |
| `pv_verify_status(job_id)` | 轮询任务状态（succeeded / failed） |
| `pv_verify_result(job_id)` | 官方查询原始数据（headers/row/fields 逐列映射，不总结；结论由 agent 归纳） |
| `pv_verify_artifacts(job_id)` | 产物清单 |
| `pv_verify_download(job_id, spec)` | 下载截图到本地临时目录，返回路径 |
| `pv_verify_share_link(job_id, spec, ttl)` | 生成短期签名下载链接（转发给最终用户） |
| `pv_verify_stats()` | 服务累计受理次数 |

- 连接目标由 `PV_BASE_URL` 决定（默认 `http://127.0.0.1:8000`），
  仓库根 `.mcp.json` 已注册指向云端部署实例；
- agent 工作流技能见 `skills/pv-verify/SKILL.md`（可复制到目标 agent 的技能目录）；
- 本地限流：提交 2 次/分钟（与服务端对齐）、只读操作 60 次/分钟，
  按本地 OS 用户键控；
- 产物保留：截图默认保留 7 天（`PV_ARTIFACT_RETENTION_DAYS`），超期由
  后台清理任务删除；转发截图请用 `pv_verify_share_link` 生成短期签名链接
  （默认 10 分钟），原始下载地址以 `job_id` 为凭据请勿外泄；
- 隐私：证件图片属敏感个人信息，核验完成后应删除本地副本，
  `job_id` 即访问凭据请勿外泄。

## 目录结构（实现相关）

- `app/api`：HTTP 接口（v1：health / verification / jobs）
- `app/mcp`：pv-mcp stdio 服务（client / schemas / rate_limit / server）
- `app/application/services/verification_service.py`：用例编排（限流→建任务→异步执行）
- `app/infrastructure/`：`config/settings.py`、`rate_limiter.py`、`job_store.py`、`verification_runner.py`
- `app/security/file_validation.py`：上传校验
- `app/schemas/`：API 数据契约（pydantic）
- `static/index.html`：响应式用户入口（上传 / 进度 / 下载）
- `storage/verify_jobs/`：验证任务产物（运行期生成，不入库）

> 验证执行复用仓库 `scripts/house_verify.py` 已验证逻辑（提取字段 + Playwright 查询 + 表头宽度适配截图）。

## 独立交付

本服务可独立开发、测试、配置（`.env.example`）、构建镜像（`Dockerfile`）与部署；
与其他服务通过 HTTP 或消息队列通信。

## 开发约定

见仓库根 `docs/service-development-guide.md` 与 `docs/api-conventions.md`；
安全与隐私要求见 `docs/security.md`、`docs/privacy.md`。
