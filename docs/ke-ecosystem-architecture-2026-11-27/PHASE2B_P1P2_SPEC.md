# Phase 2B-P1P2 执行契约（最后一批：P1/P2 72 站内网隧道深扫）

> 承接 `TASK_SPEC.md`、`PHASE2_SPEC.md`、`PHASE2B_SPEC.md`、`PHASE2B_NEXT_SPEC.md` 的全部安全、只读、隔离与脱敏红线。所有产物仅写入 `C:\Users\shipi\AI-work\ke-arch-20261127\`，严禁改动 `C:\Users\shipi\vswp\BeikeMicroservice\` 下任何文件。

---

## 0. 目标范围与独立落盘设计（保护已有 72 站成果）
- **输入来源**：从 `phase2b_p1p2_screen.json` 筛出的 **72 站**（P1 41 站 + P2 31 站，均来自 `phase2b_p1p2_targets.json` 中命中 `accessible_verified_or_page` 或 `spa_shell_needs_browser` 的条目，与 `site_profiles_2b.jsonl` 已有的 72 站零重叠）。
- **独立输出文件（关键保护）**：
  - 本轮测绘结果**写入独立文件 `site_profiles_2b_p1p2.jsonl`**（不直接覆盖/改写已定稿的 `site_profiles_2b.jsonl`，避免 `load_resume_records` 丢弃已确认的 4 条 `error` 记录）。
  - 目标清单生成脚本 `scripts/build_phase2b_p1p2_targets.py` -> 输出 `phase2b_p1p2_deep_targets.json`（72 站，`batch="2B-P1P2"`）。
  - 直接复用现有的 `scripts/phase2_site_profiler.py --targets-json phase2b_p1p2_deep_targets.json --output-jsonl site_profiles_2b_p1p2.jsonl --use-tunnel --resume --timeout-ms 20000`（严禁大改或重写测绘器）。

---

## 1. 稳定性与防重置策略
- **单隧道一口气跑完**：72 站均值约 11 秒/站，预计总耗时 13–16 分钟；在单次临时 SSH SOCKS5 隧道生命周期内连续完成，不拆分多次启停。
- **遇 SSH 连接重置冷却**：若中途发生远端 SSH 建连重置（`code=255`），等待 35–45 秒冷却后使用 `--resume` 继续补完剩余站点，切勿高频秒级重试。
- **单站硬超时与跳过**：保持 `--timeout-ms 20000`，遇到个别超时、404 或不可达站直接记 `error` 落盘跳过，绝不反复手工重试拖延时间。
- **全局禁登出拦截**：保持 `/logout|/signout|/logoff` 路由 `abort()` 与每站独立隔离 `BrowserContext`。

---

## 2. 隐私专项复查（吸取上轮 `notice` 卡片经验）
由于 P1/P2 包含大量 HR、薪酬、财务、资产、绩效与员工服务系统，页面 DOM（`title`、`ui_modules.top_nav`、`ui_modules.side_nav_or_tabs`、`ui_modules.page_headings_or_cards`）更易出现当前登录员工的真实姓名、部门全称、工号或邮箱：
- 测绘结束后，必须在内存中读取当前主体姓名/工号/UCID/手机号集合，写一段只读检查逻辑扫一遍 `site_profiles_2b_p1p2.jsonl` 的全部字符串字段；若发现任何未脱敏的个人姓名或隐私项，立即替换为 `[REDACTED]` 或清零对应字段（绝不在日志或报告中打印原值）。
- 随后运行 `scripts/redaction_check.py --output phase2b_p1p2_redaction_check.json` 对全工作区做离线扫描，确保 `passed == true` 且 `findings == []`。

---

## 3. 本轮交付物与验收标准
1. `scripts/build_phase2b_p1p2_targets.py` 与 `phase2b_p1p2_deep_targets.json`（72 站目标清单）。
2. `site_profiles_2b_p1p2.jsonl`（72 行去重 host 完整脱敏档案；与 `site_profiles_2a.jsonl` 22 行 + `site_profiles_2b.jsonl` 72 行形成完整 166 次实测证据库）。
3. `phase2b_p1p2_summary.md`（中文阶段汇报）：
   - 72 站按 P1（41 站）/ P2（31 站）的判定分布总表（`authenticated_verified` / `authenticated_page` / `denied_403` / `login_required` / `error`）；
   - 按职能业务域（财务费控资产、法务合规合同、HR 招聘绩效薪酬、产研基建与办公协作、企业文化与行政服务）归纳真正可用的旁路系统、核心模块、认证机制与跨站共享后端 API 域；
   - 特别标出快筛阶段放行但在浏览器渲染后实际落入无权限/403/独立登录的“假通过”站点清单（为 Phase 3 精准划分可用边界提供依据）；
   - 隐私复查、脱敏自检与临时隧道清理确认。
4. `phase2b_p1p2_redaction_check.json`（全工作区 `passed == true` 且 `findings == []`）。
5. 确认 `BeikeMicroservice` 零修改，本机无残留临时 SSH 隧道（保留系统既有 8020 隧道）与孤儿浏览器进程。
