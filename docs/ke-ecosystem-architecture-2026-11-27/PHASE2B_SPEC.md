# Phase 2B 执行契约（内网隧道测绘 + P1/P2 轻量快筛）

> 本文档承接 `TASK_SPEC.md` 与 `PHASE2_SPEC.md` 的全部安全、只读、隔离与脱敏红线。所有产物仅写入 `C:\Users\shipi\AI-work\ke-arch-20261127\`，严禁改动 `C:\Users\shipi\vswp\BeikeMicroservice\` 下任何文件。

---

## 0. 防超时拆分策略（2B-1 → 2B-2 串行）
因内网隧道下待测总数较多（原 28 表其余 23 站 + 2A 转入 6 站 + 2A 公网网关拦截复核 4 站 + P0 核心业务 39 站 + P1/P2 快筛 89 站），为确保单次执行稳妥落在 15 分钟内且绝不撞超时，将 Phase 2B 拆为两段串行（均追加落盘至同一 `site_profiles_2b.jsonl`，充分利用 `--resume`）：

- **Phase 2B-1（本轮执行）**：
  1. **Step 2B-1A（P1/P2 89 站轻量 HTTP/SSO 快筛）**：通过脚本内 `try...finally` 托管的临时 SSH SOCKS5 隧道，对 `phase1_diff.md` 第 3.2 节列出的 P1（47 站）与 P2（42 站）共 89 站进行只读并发快筛（建议并发 `max_workers=4`，单站超时 10 秒，严禁访问任何 `/logout|/signout|/logoff`），输出 `phase2b_p1p2_screen.json` 与 `phase2b_p1p2_screen.md`，将每站明确分为：
     - `accessible_verified_or_page`：通过 CAS 或返回受保护业务页（非登录页、非 403/Forbidden），**进入 Phase 2B-2 深度浏览器测绘**；
     - `spa_shell_needs_browser`：HTTP 200 返回极简 SPA 壳且未完成前端 SSO（无法仅靠 HTTP 判定），**进入 Phase 2B-2 深度浏览器测绘**；
     - `denied_403`：返回 HTTP 403 或重定向至 forbidden / 无权限页面，**跳过浏览器深扫**；
     - `login_required`：在携带统一登录票据后仍停留在独立登录页或重定向循环，**跳过浏览器深扫**；
     - `unreachable_or_error`：DNS 失败、连接重置、TLS 错误、5xx 或超时，**跳过浏览器深扫**。
  2. **Step 2B-1B（内网基线 + 2A 转入 + 2A 网关拦截复核，共 33 站深度测绘）**：复用 `scripts/phase2_site_profiler.py --use-tunnel --resume`，输出至 `site_profiles_2b.jsonl`，覆盖：
     - `phase1_diff.md` 第 3.2 节中「已确认 28 表其余内网站点」（23 站）；
     - `phase2a_handoff_to_2b.json` 中 2A 直连受阻转入站点（6 站）；
     - `site_profiles_2a.jsonl` 中 `access_verdict == "denied_403"` 的 4 个公网网关拦截站（用于区分“仅公网 IP 触发安全拦截”还是“内网亦无业务权限”）。
- **Phase 2B-2（下一轮执行）**：
  - 复用 `scripts/phase2_site_profiler.py --use-tunnel --resume` 继续追加写入 `site_profiles_2b.jsonl`，测绘 **P0 核心业务（39 站）** + **Step 2B-1A 筛出的可用/SPA 候选站**。

---

## 1. Phase 2B-1 技术与安全执行细节

### 1.1 目标清单构建（`scripts/build_phase2b_targets.py`）
从本目录已有产物（`phase1_diff.md`、`phase1_discovery.json`、`phase2a_handoff_to_2b.json`、`site_profiles_2a.jsonl`）及只读参考 `summary.json` 自动生成两份目标 JSON：
1. `phase2b_p1p2_targets.json`：P1（47 站）+ P2（42 站）共 89 站，携带各自在 `phase1_discovery.json` 中提取到的真实入口 URL（缺失则回退 `https://<host>/`）与 `priority`（`P1`/`P2`）。
2. `phase2b1_deep_targets.json`：33 站深扫清单（23 内网基线 + 6 个 2A 转入 + 4 个 2A 公网拦截复核），标注 `batch="2B-1"` 与对应 `category`。

### 1.2 P1/P2 轻量快筛脚本（`scripts/phase2b_http_screener.py`）
- **网络通道**：脚本内通过 `try...finally` 托管本地临时 SSH SOCKS5 隧道（与 `phase1_hub_discovery.py` / `phase2_site_profiler.py` 保持一致的端口探测、就绪等待与退出强制清理逻辑）；Python HTTP 请求走该本地 SOCKS5 代理（优先使用工作区 `.venv` 中已有的 `httpx[socks]` / `requests[socks]` / `PySocks` 或 `urllib` + `socks`；若无则可在工作区 `.venv` 内按需安装，严禁触碰 `BeikeMicroservice`）。
- **只读与安全**：
  - 仅发 `GET` 请求；自定义 Redirect 处理器阻止一切含 `/logout`、`/signout`、`/logoff` 的跳转以及跳往非目标企业域的外站重定向；
  - 仅在内存中读取远端会话材料，注入统一登录域票据及已有业务 Cookie；
  - 严格清洗输出：URL 去敏、不落盘任何 Cookie/Token 值、不落盘个人 PII，仅记录 `host`, `name`, `priority`, `entry_url`, `final_url`, `http_status`, `title`, `verdict`, `identity_match`, `cas_callback_seen`, `issued_cookie_names`, `redirect_hosts`, `html_bytes`, `error`, `elapsed_seconds`。
- **熔断机制**：正式开跑 89 站前，先用 1 个已知内网基线枢纽（如工作台首页或员工门户）做 1 次快速连通探活，确认隧道与会话有效后再并发快筛。

### 1.3 复用 `scripts/phase2_site_profiler.py` 跑 33 站深扫
- 检查 `phase2_site_profiler.py` 的 `--use-tunnel` 实现，确保：
  - 临时 SSH SOCKS5 隧道在 `try...finally` 中关闭并验证端口释放；
  - 对 33 站全部启用独立隔离 `BrowserContext`、全局拦截登出路由、`--timeout-ms 25000`；
  - 逐站 `flush` 写入 `site_profiles_2b.jsonl`，并在跑完后以 `--resume` 验证幂等跳过；
  - 特别注意 28 表中已验证的 20 个 B 级入口（此前缺菜单与后端 API 观察），确保完整提取其 `ui_modules`、`backend_apis`、`backend_api_hosts` 与 `auth_mechanism`。

---

## 2. Phase 2B-1 交付物与验收标准
完成 Phase 2B-1 后，`C:\Users\shipi\AI-work\ke-arch-20261127\` 必须包含：
1. `scripts/build_phase2b_targets.py`
2. `scripts/phase2b_http_screener.py`
3. `phase2b_p1p2_targets.json`（89 站清单）与 `phase2b1_deep_targets.json`（33 站清单）
4. `phase2b_p1p2_screen.json` 与 `phase2b_p1p2_screen.md`（89 站快筛明细与统计，明确列出进入 2B-2 深扫的站点数量与清单）
5. `site_profiles_2b.jsonl`（当前包含 Phase 2B-1 的 33 站完整脱敏档案）
6. `phase2b1_summary.md`（中文汇报：33 站深扫结论——尤其是 20 个原 B 级入口补全的功能与后端域、6 个 2A 转入站及 4 个公网拦截站在内网的真实结果 + P1/P2 89 站快筛分布 + 拟进入 Phase 2B-2 的最终目标数）
7. `phase2b1_redaction_check.json`（运行 `scripts/redaction_check.py --output phase2b1_redaction_check.json` 覆盖全工作区，必须 `passed == true` 且 `findings == []`）
8. 确认 `BeikeMicroservice` 零改动、本机无残留临时 SSH 隧道（除系统既有 8020 看板隧道外）与孤儿浏览器进程。
