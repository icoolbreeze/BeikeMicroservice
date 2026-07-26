# property_verification

房源信息验证微服务 —— BeikeMicroservice 平台中的一个**独立服务**。

> 本服务只是平台微服务集合中的一员，不代表整个仓库。

## 职责

- 接收不动产权证图片；
- 提取业务件号、证件编码（视觉模型）；
- 调用**合法授权**的房源信息验证渠道（住建蓉e办）；
- 生成查询结果截图（不同规格，可下载）；
- 提供用户入口（PC / 手机自适应），实时反馈进度与错误。

## 安全机制

- **无需登录**：用户直接上传证件即可验证；
- **IP 限流**：同一 IP 默认 1 次/分钟、10 次/天（可经 `PV_RATE_PER_MIN` / `PV_RATE_PER_DAY` 调整）；
- 上传件校验：仅 JPEG/PNG、≤10MB、魔数嗅探、文件名规范化；
- 上传原件核验后即删；产物存于 `storage/verify_jobs/`（已 gitignore，不入库）；
- 任务 ID 为 uuid4，对未登录用户即作为访问凭据（不可猜测）。

## API（v1）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/verification` | 上传证件（multipart `file`），返回 `job_id`（202） |
| GET | `/api/v1/verification/{id}/events` | SSE 实时进度/错误流，至终态关闭 |
| GET | `/api/v1/verification/{id}/artifacts` | 产物清单 |
| GET | `/api/v1/verification/{id}/download/{spec}` | 下载 `panel` / `full` / `zip` |
| GET | `/api/v1/jobs/{id}` | 任务状态快照 |
| GET | `/api/v1/health` | 健康检查 |

## 快速开始

1. 安装依赖（在仓库托管 Python 环境中）：
   ```bash
   pip install fastapi "uvicorn[standard]" python-multipart pydantic pillow requests numpy playwright
   python -m playwright install chromium
   ```
2. 配置环境变量：复制 `.env.example` 为 `.env`，填入 `OPENROUTER_API_KEY`。
3. 在**服务目录**下启动：
   ```bash
   cd services/property_verification
   uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
   ```
   或在仓库根执行 `make serve-pv`。
4. 浏览器打开 `http://localhost:8000/`，上传证件即可。

## 目录结构（实现相关）

- `app/api`：HTTP 接口（v1：health / verification / jobs）
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
