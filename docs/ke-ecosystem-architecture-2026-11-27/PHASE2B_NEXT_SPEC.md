# Phase 2B 断点续跑与 P0 核心业务深扫契约（Phase 2B-1R + P0）

> 承接 `TASK_SPEC.md`、`PHASE2_SPEC.md`、`PHASE2B_SPEC.md` 的全部安全、只读、隔离与脱敏红线。所有产物仅写入 `C:\Users\shipi\AI-work\ke-arch-20261127\`，严禁修改 `C:\Users\shipi\vswp\BeikeMicroservice\`。

---

## 0. 当前断点现状
- **已完成**：
  - `phase2b_p1p2_screen.json` & `phase2b_p1p2_screen.md`（P1/P2 共 89 站轻量快筛已 100% 完成，筛出 72 站进入后续深扫）。
  - `site_profiles_2b.jsonl` 已落盘 `phase2b1_deep_targets.json` 中的前 **19 / 33** 站（均已完成且有效）。
  - 通用测绘器 `scripts/phase2_site_profiler.py` 已调试完毕且支持 `--use-tunnel --resume`。
- **本轮目标（共 53 站，约 10–13 分钟）**：
  1. **补完 `phase2b1_deep_targets.json` 剩余 14 站**（4 站 28 表内网剩余 + 6 站 2A 转入 + 4 站 2A 公网拦截内网复核），直接复用 `--resume` 追加写入 `site_profiles_2b.jsonl`，无需重跑已完成 19 站。
  2. **生成并深扫 P0 核心业务 39 站**（从 `phase1_diff.md` 第 3.2 节 P0 列表与 `phase1_discovery.json` 真实入口 URL 生成 `phase2b_p0_targets.json`，`batch="2B-P0"`），同样使用 `scripts/phase2_site_profiler.py --use-tunnel --resume` 追加写入 `site_profiles_2b.jsonl`。
  3. **严禁在此轮重写或大改 `phase2_site_profiler.py`**（仅在确有崩溃 Bug 时做最小修补），把全部时间集中在稳定跑完这 53 站并产出汇总报告。

---

## 1. 稳定性与提速参数（防再次超时）
- 单站硬超时 `--timeout-ms 20000`（20 秒）；若遇个别慢站超时或连接异常，直接记 `error` / `unreachable` 落盘并继续下一站，**不要为了单个疑难站反复手工重跑浪费时间**。
- 可将剩余 14 站 + P0 39 站合并为一次临时隧道生命周期内的连续执行（或先跑 `phase2b1_deep_targets.json --resume` 再跑 `phase2b_p0_targets.json --resume`），减少隧道反复启停开销。
- 严格保留 `/logout|/signout|/logoff` 路由拦截与每站独立隔离 `BrowserContext`。

---

## 2. 本轮交付物与验收标准
1. `phase2b_p0_targets.json`（39 站 P0 核心业务目标清单）。
2. `site_profiles_2b.jsonl`：总行数达到 **72 行**（33 站 2B-1 全量 + 39 站 P0 核心业务全量，无重复 host）。
3. `phase2b1_p0_summary.md`（中文阶段汇报）：
   - **第一部分**：2B-1 全部 33 站结论总表（重点总结：原 28 表 23 个内网站的真实功能模块/后端 API 域及权限变化，如已观测到积分提佣跳转未授权；6 个 2A 转入站在内网的真实状态；4 个 2A 公网拦截站在内网是否放行）；
   - **第二部分**：P0 核心业务 39 站的登录判定分布（`authenticated_verified` / `authenticated_page` / `denied_403` / `login_required` / `error`）及所有可用业务系统的核心功能模块、认证方式与后端 API 域摘要；
   - **第三部分**：衔接下一轮 **Phase 2B-P1P2**（72 站快筛通过清单）的准备状态与耗时估算。
4. `phase2b_p0_redaction_check.json`：运行 `scripts/redaction_check.py --output phase2b_p0_redaction_check.json` 扫描全工作区，必须 `passed == true` 且 `findings == []`。
5. 进程与隔离确认：确认临时 SSH 隧道与 Playwright 浏览器全部退出释放（绝不触碰系统原有 8020 隧道），`BeikeMicroservice` 零改动。
