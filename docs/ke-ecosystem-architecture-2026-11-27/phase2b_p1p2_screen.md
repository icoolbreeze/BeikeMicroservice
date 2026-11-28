# Phase 2B-1 P1/P2 轻量 HTTP/SSO 快筛报告（89 站）

> **采集时间（UTC）**：`2026-11-27T18:08:32.021593+00:00`
> **执行通道**：托管临时 SSH SOCKS5 隧道（`try...finally` 退出释放，未触碰系统既有 8020 隧道）；并发 `max_workers=4`，单站超时 `10.0s`；仅发 `GET`，全局拦截 `/logout|/signout|/logoff` 与非企业域外站重定向。
> **基线探活结果**：`family.ke.com` -> `verdict=spa_shell_needs_browser`、`http_status=200`、`cas_callback_seen=False`（耗时 `1.85s`）
> **核心结论**：89 站中共有 **72 站** 筛出需进入 **Phase 2B-2 深度浏览器测绘**（其中 `accessible_verified_or_page` 47 站、`spa_shell_needs_browser` 25 站），其余 **17 站** 在轻量快筛阶段确认为无权限/需独立登录/不可达并跳过浏览器深扫。

---

## 1. 89 站快筛判定分布汇总

| 判定分类 (`verdict`) | P1 (47站) | P2 (42站) | 合计 (89站) | 占比 | 是否进入 2B-2 |
| --- | ---: | ---: | ---: | ---: | :---: |
| `accessible_verified_or_page` | 23 | 24 | **47** | 52.8% | 是 |
| `spa_shell_needs_browser` | 18 | 7 | **25** | 28.1% | 是 |
| `denied_403` | 1 | 2 | **3** | 3.4% | 否 |
| `login_required` | 3 | 4 | **7** | 7.9% | 否 |
| `unreachable_or_error` | 2 | 5 | **7** | 7.9% | 否 |
| **合计** | **47** | **42** | **89** | **100%** | **进入 2B-2：72 站** |

---

## 2. 拟进入 Phase 2B-2 深度浏览器测绘清单（共 72 站）

| # | 优先级 | 主机 (`host`) | 系统名称 | 快筛判定 | HTTP | CAS | 入口 URL |
| ---: | :---: | --- | --- | --- | ---: | :---: | --- |
| 1 | P1 | `recruit.ke.com` | 招聘/入职 / 入职管理系统 | `accessible_verified_or_page` | 200 | `true` | `https://recruit.ke.com/` |
| 2 | P1 | `renshi.ehr.lianjia.com` | LinkHR / 个人电子档案 | `accessible_verified_or_page` | 200 | `true` | `http://renshi.ehr.lianjia.com/` |
| 3 | P1 | `okr.ke.com` | OKR | `accessible_verified_or_page` | 200 | `true` | `https://okr.ke.com/p/okr` |
| 4 | P1 | `ones.ke.com` | KeOnes 项目管理 | `spa_shell_needs_browser` | 200 | `false` | `http://ones.ke.com/` |
| 5 | P1 | `report.ke.com` | 小贝汇报 | `accessible_verified_or_page` | 200 | `true` | `https://report.ke.com/` |
| 6 | P1 | `bpm.ke.com` | HR 权限申请/礼品申报 | `accessible_verified_or_page` | 200 | `true` | `https://bpm.ke.com/portal/r/w?cmd=com.actionsoft.apps.workbench_mobile_process_start&groupId=obj_1ca3700b12884bbcadd04da70bb8d0b2&processDefId=obj_ddc46144c54d4d66ad3e6a7ae65a057f` |
| 7 | P1 | `bpm-m.ke.com` | 离职申请 | `accessible_verified_or_page` | 200 | `true` | `https://bpm-m.ke.com/portal/r/w?cmd=com.actionsoft.apps.workbench_mobile_process_start&groupId=obj_395fd2a3ea4d4f8f840a30c67f1e33b5&processDefId=obj_b37027b10dc54826aafeb952727664f3` |
| 8 | P1 | `finops.ke.com` | 贝壳费控平台 | `accessible_verified_or_page` | 200 | `true` | `https://finops.ke.com/` |
| 9 | P1 | `bfa.ke.com` | 财务+ | `accessible_verified_or_page` | 200 | `true` | `https://bfa.ke.com/home` |
| 10 | P1 | `pay.ke.com` | Kepay | `spa_shell_needs_browser` | 200 | `false` | `https://pay.ke.com/` |
| 11 | P1 | `rpm.ke.com` | 报账平台 | `accessible_verified_or_page` | 200 | `true` | `https://rpm.ke.com/` |
| 12 | P1 | `caigou.ke.com` | 采购系统 | `spa_shell_needs_browser` | 200 | `false` | `http://caigou.ke.com/` |
| 13 | P1 | `budget-control.ke.com` | 预算 | `accessible_verified_or_page` | 200 | `true` | `http://budget-control.ke.com/` |
| 14 | P1 | `zichan.ke.com` | 资产 2.0 | `spa_shell_needs_browser` | 200 | `false` | `https://zichan.ke.com/` |
| 15 | P1 | `mzichan.ke.com` | 我的资产/资产申请 | `spa_shell_needs_browser` | 200 | `false` | `https://mzichan.ke.com/asset/list` |
| 16 | P1 | `its.ke.com` | IT 服务、视频会议 | `spa_shell_needs_browser` | 200 | `false` | `https://its.ke.com/article/articledetail?cid=9&aid=80` |
| 17 | P1 | `its.lianjia.com` | 软件下载 | `spa_shell_needs_browser` | 200 | `false` | `https://its.lianjia.com/software/windows` |
| 18 | P1 | `new-meeting.ke.com` | 会议室预定 | `spa_shell_needs_browser` | 200 | `false` | `https://new-meeting.ke.com/reserve` |
| 19 | P1 | `meeting-assistant.ke.com` | 会议室系统 | `accessible_verified_or_page` | 200 | `true` | `https://meeting-assistant.ke.com/qw-room` |
| 20 | P1 | `wen.ke.com` | 贝壳问卷 | `accessible_verified_or_page` | 200 | `true` | `https://wen.ke.com/home/create` |
| 21 | P1 | `yes-or-no.ke.com` | Yes or No 员工交流 | `spa_shell_needs_browser` | 200 | `false` | `https://yes-or-no.ke.com/p/posts` |
| 22 | P1 | `profile.ke.com` | 人才管理 | `accessible_verified_or_page` | 200 | `true` | `https://profile.ke.com/talent` |
| 23 | P1 | `profile-h5.ke.com` | 人才标准 | `accessible_verified_or_page` | 200 | `true` | `https://profile-h5.ke.com/talent/development-channel` |
| 24 | P1 | `inrecruit.ke.com` | 内部活水 | `spa_shell_needs_browser` | 200 | `false` | `https://inrecruit.ke.com/` |
| 25 | P1 | `spi.ke.com` | 运营绩效系统 | `accessible_verified_or_page` | 200 | `true` | `https://spi.ke.com/` |
| 26 | P1 | `codin.ke.com` | CHO 驾驶舱 | `spa_shell_needs_browser` | 200 | `false` | `https://codin.ke.com/portal/3956/49460` |
| 27 | P1 | `chip.ke.com` | 电子合同下载校验 | `spa_shell_needs_browser` | 200 | `false` | `https://chip.ke.com/hr/verify?type=contract&d=%7B%22tip%22%3A%22%E8%AF%B7%E6%82%A8%E5%A6%A5%E5%96%84%E4%BF%9D%E7%AE%A1%E7%94%B5%E5%AD%90%E5%90%88%E5%90%8C%EF%BC%8C%E5%A6%82%E6%9C%89%E7%96%91%E9%97%AE%E5%8F%AF%E8%81%94%E7%B3%BBHR%E5%92%A8%E8%AF%A2%22%2C%22title%22%3A%22%E7%94%B5%E5%AD%90%E5%90%88%E5%90%8C%22%2C%22originUrl%22%3A%22https%3A%2F%2Ffamily.ke.com%2FmyInfo%2Fprofile%23contractInfo%22%7D` |
| 28 | P1 | `mdm.ke.com` | 主数据管理平台 MDM | `accessible_verified_or_page` | 200 | `true` | `https://mdm.ke.com/` |
| 29 | P1 | `kepler.ke.com` | 税务平台 | `accessible_verified_or_page` | 200 | `true` | `https://kepler.ke.com/` |
| 30 | P1 | `shandian.lianjia.com` | 闪店 | `accessible_verified_or_page` | 200 | `true` | `http://shandian.lianjia.com/` |
| 31 | P1 | `sis.lianjia.com` | SIS 门店损益 | `accessible_verified_or_page` | 200 | `true` | `https://sis.lianjia.com/` |
| 32 | P1 | `legal-compliance.ke.com` | 合规工作台 | `accessible_verified_or_page` | 200 | `true` | `https://legal-compliance.ke.com/` |
| 33 | P1 | `m-legal.ke.com` | 合同管理 | `accessible_verified_or_page` | 200 | `true` | `https://m-legal.ke.com/#/contract` |
| 34 | P1 | `contract.ke.com` | 合同管理 | `accessible_verified_or_page` | 200 | `true` | `https://contract.ke.com/` |
| 35 | P1 | `case-legal.ke.com` | 诉讼管理 | `spa_shell_needs_browser` | 200 | `false` | `http://case-legal.ke.com/index` |
| 36 | P1 | `company-legal.ke.com` | 股权管理 | `accessible_verified_or_page` | 200 | `true` | `https://company-legal.ke.com/companyManage` |
| 37 | P1 | `legal-lawyer-library.ke.com` | 律师库 | `spa_shell_needs_browser` | 200 | `false` | `http://legal-lawyer-library.ke.com/` |
| 38 | P1 | `vprivacy.ke.com` | 数据合规 | `spa_shell_needs_browser` | 200 | `false` | `https://vprivacy.ke.com/#/ServiceHall/index` |
| 39 | P1 | `mknow.ke.com` | 违纪通报 | `spa_shell_needs_browser` | 200 | `false` | `https://mknow.ke.com/#/?nodeId=28981&scene=lianjiebeike` |
| 40 | P1 | `homeworld.ke.com` | 行政服务 | `spa_shell_needs_browser` | 200 | `false` | `https://homeworld.ke.com/` |
| 41 | P1 | `homeworld-front.ke.com` | 日常用品申请/公司菜单 | `spa_shell_needs_browser` | 200 | `false` | `https://homeworld-front.ke.com/showmenu` |
| 42 | P2 | `apollo.portal.life.ke.com` | Apollo 配置中心 | `accessible_verified_or_page` | 200 | `true` | `http://apollo.portal.life.ke.com/` |
| 43 | P2 | `cloud.intra.ke.com` | 贝壳云、产品树 | `accessible_verified_or_page` | 200 | `true` | `https://cloud.intra.ke.com/home` |
| 44 | P2 | `easy.intra.lianjia.com` | 链家网 EASY | `accessible_verified_or_page` | 200 | `true` | `http://easy.intra.lianjia.com/` |
| 45 | P2 | `compass.ke.com` | 埋点平台 / 罗盘 | `spa_shell_needs_browser` | 200 | `false` | `http://compass.ke.com/` |
| 46 | P2 | `risk-platform.ke.com` | 风控平台 | `spa_shell_needs_browser` | 200 | `false` | `https://risk-platform.ke.com/personal` |
| 47 | P2 | `workflow.lianjia.com` | 通用审批流 | `accessible_verified_or_page` | 200 | `true` | `http://workflow.lianjia.com/` |
| 48 | P2 | `fssc.lianjia.com` | 老费控 | `accessible_verified_or_page` | 200 | `true` | `http://fssc.lianjia.com/` |
| 49 | P2 | `xinchou-data.ke.com` | 薪酬预处理平台 | `accessible_verified_or_page` | 200 | `true` | `https://xinchou-data.ke.com/` |
| 50 | P2 | `cb.ke.com` | 基薪管理 | `accessible_verified_or_page` | 200 | `true` | `https://cb.ke.com/` |
| 51 | P2 | `salary-middle.ke.com` | 薪酬中心 | `accessible_verified_or_page` | 200 | `true` | `https://salary-middle.ke.com/` |
| 52 | P2 | `salary-monitor.ke.com` | 服务监控 | `accessible_verified_or_page` | 200 | `true` | `https://salary-monitor.ke.com/` |
| 53 | P2 | `salary-tax.ke.com` | 税友平台 | `accessible_verified_or_page` | 200 | `true` | `https://salary-tax.ke.com/` |
| 54 | P2 | `shebao.ke.com` | 社保系统 | `spa_shell_needs_browser` | 200 | `false` | `https://shebao.ke.com/` |
| 55 | P2 | `ganbu.ke.com` | 干部画像 | `accessible_verified_or_page` | 200 | `true` | `https://ganbu.ke.com/` |
| 56 | P2 | `leader.ke.com` | 管理者工作台 | `spa_shell_needs_browser` | 200 | `false` | `https://leader.ke.com/` |
| 57 | P2 | `nltx.ke.com` | 职级平台 | `accessible_verified_or_page` | 200 | `true` | `https://nltx.ke.com/pageo/integral-detail` |
| 58 | P2 | `hrlearn.ke.com` | HR价值流 | `accessible_verified_or_page` | 200 | `true` | `http://hrlearn.ke.com/home` |
| 59 | P2 | `resume-agc.ke.com` | 聘多多 | `accessible_verified_or_page` | 200 | `true` | `https://resume-agc.ke.com/talent/candidateList` |
| 60 | P2 | `vm.ke.com` | 价值观评分 | `accessible_verified_or_page` | 200 | `true` | `https://vm.ke.com/` |
| 61 | P2 | `vote-admin.ke.com` | 荣誉评选系统 | `accessible_verified_or_page` | 200 | `true` | `https://vote-admin.ke.com/pc/values/activityList` |
| 62 | P2 | `admin-huaqiao.ke.com` | 花桥学堂 | `accessible_verified_or_page` | 200 | `true` | `https://admin-huaqiao.ke.com/` |
| 63 | P2 | `admin-study.ke.com` | 贝好学管理端 | `accessible_verified_or_page` | 200 | `true` | `https://admin-study.ke.com/` |
| 64 | P2 | `admin-sunshine.ke.com` | 阳光贝壳 | `spa_shell_needs_browser` | 200 | `false` | `https://admin-sunshine.ke.com/web/#/login` |
| 65 | P2 | `be-admin.ke.com` | 新经纪榜样 | `accessible_verified_or_page` | 200 | `true` | `https://be-admin.ke.com/pc/example/activitylist` |
| 66 | P2 | `esghub.ke.com` | 碳管理 | `spa_shell_needs_browser` | 200 | `false` | `https://esghub.ke.com/hub/beke/request` |
| 67 | P2 | `gongyi.ke.com` | 贝壳公益 | `spa_shell_needs_browser` | 200 | `false` | `https://gongyi.ke.com/web/pc/dataKanban` |
| 68 | P2 | `jingyingshe-admin.ke.com` | 精英社 | `accessible_verified_or_page` | 200 | `true` | `https://jingyingshe-admin.ke.com/` |
| 69 | P2 | `kehome-cms.ke.com` | 贝壳之家 | `accessible_verified_or_page` | 200 | `true` | `https://kehome-cms.ke.com/columnManagement/columnList` |
| 70 | P2 | `lcm.ke.com` | 预实管理 | `accessible_verified_or_page` | 200 | `true` | `https://lcm.ke.com/` |
| 71 | P2 | `m-intelligencecampus-hq.ke.com` | 老左图书馆 | `accessible_verified_or_page` | 200 | `true` | `https://m-intelligencecampus-hq.ke.com/LibraryBooking/bookingIndex` |
| 72 | P2 | `mcg.ke.com` | CG文化圈 | `accessible_verified_or_page` | 200 | `true` | `https://mcg.ke.com/` |

---

## 3. 89 站逐站快筛全量明细

| # | 优先级 | 主机 (`host`) | 系统名称 | 判定 (`verdict`) | HTTP | 页面标题 | 终态 URL | 签发 Cookie 数 | 耗时 | 备注/错误 |
| ---: | :---: | --- | --- | --- | ---: | --- | --- | ---: | ---: | --- |
| 1 | P1 | `recruit.ke.com` | 招聘/入职 / 入职管理系统 | `accessible_verified_or_page` | 200 | 贝壳招聘 | `https://recruit.ke.com/application-zhaopin-datacenter` | 2 | 5.9s | — |
| 2 | P1 | `renshi.ehr.lianjia.com` | LinkHR / 个人电子档案 | `accessible_verified_or_page` | 200 | — | `http://renshi.ehr.lianjia.com/` | 3 | 3.64s | — |
| 3 | P1 | `okr.ke.com` | OKR | `accessible_verified_or_page` | 200 | 贝壳OKR | `https://okr.ke.com/p/okr` | 3 | 4.48s | — |
| 4 | P1 | `ones.ke.com` | KeOnes 项目管理 | `spa_shell_needs_browser` | 200 | KeOnes | `https://ones.ke.com/` | 2 | 3.09s | — |
| 5 | P1 | `report.ke.com` | 小贝汇报 | `accessible_verified_or_page` | 200 | 小贝汇报 | `https://report.ke.com/` | 3 | 2.84s | — |
| 6 | P1 | `bpm.ke.com` | HR 权限申请/礼品申报 | `accessible_verified_or_page` | 200 | 暂时无法访问 | `https://bpm.ke.com/portal/r/w?cmd=com.actionsoft.apps.workbench_mobile_process_start&groupId=obj_1ca3700b12884bbcadd04da70bb8d0b2&processDefId=obj_ddc46144c54d4d66ad3e6a7ae65a057f` | 3 | 3.93s | — |
| 7 | P1 | `bpm-m.ke.com` | 离职申请 | `accessible_verified_or_page` | 200 | 暂时无法访问 | `https://bpm.ke.com/portal/r/w?cmd=com.actionsoft.apps.workbench_mobile_process_start&groupId=obj_395fd2a3ea4d4f8f840a30c67f1e33b5&processDefId=obj_b37027b10dc54826aafeb952727664f3` | 3 | 2.47s | — |
| 8 | P1 | `finops.ke.com` | 贝壳费控平台 | `accessible_verified_or_page` | 200 | 加载中 | `https://finops.ke.com/ecs_console/index.html` | 1 | 2.45s | — |
| 9 | P1 | `bfa.ke.com` | 财务+ | `accessible_verified_or_page` | 200 | 商业化 | `https://bfa.ke.com/` | 2 | 3.02s | — |
| 10 | P1 | `pay.ke.com` | Kepay | `spa_shell_needs_browser` | 200 | 贝壳Pay | `https://pay.ke.com/` | 1 | 0.72s | — |
| 11 | P1 | `rpm.ke.com` | 报账平台 | `accessible_verified_or_page` | 200 | 加载中 | `https://rpm.ke.com/ecs_console/index.html` | 2 | 2.55s | — |
| 12 | P1 | `caigou.ke.com` | 采购系统 | `spa_shell_needs_browser` | 200 | — | `https://caigou.ke.com/mall/` | 4 | 1.43s | — |
| 13 | P1 | `budget-control.ke.com` | 预算 | `accessible_verified_or_page` | 200 | 预算管控系统 | `http://budget-control.ke.com/` | 3 | 1.97s | — |
| 14 | P1 | `zichan.ke.com` | 资产 2.0 | `spa_shell_needs_browser` | 200 | 资产管理平台 | `https://zichan.ke.com/` | 2 | 0.57s | — |
| 15 | P1 | `mzichan.ke.com` | 我的资产/资产申请 | `spa_shell_needs_browser` | 200 | 贝壳资产 | `https://mzichan.ke.com/asset/list` | 2 | 0.4s | — |
| 16 | P1 | `mail.ke.com` | 贝壳邮箱 / 网页邮箱 | `login_required` | 200 | Outlook | `https://mail.ke.com/owa/auth/logon.aspx?url=https%3A%2F%2Fmail.ke.com%2Fowa%2F&reason=0` | 0 | 1.48s | — |
| 17 | P1 | `owa.lianjia.com` | 邮箱 | `login_required` | 200 | Outlook | `https://owa.lianjia.com/owa/auth/logon.aspx?url=https%3A%2F%2Fowa.lianjia.com%2Fowa%2F&reason=0` | 0 | 1.29s | — |
| 18 | P1 | `its.ke.com` | IT 服务、视频会议 | `spa_shell_needs_browser` | 200 | IT门户 | `https://its.ke.com/article/articledetail?cid=9&aid=80` | 2 | 0.6s | — |
| 19 | P1 | `its.lianjia.com` | 软件下载 | `spa_shell_needs_browser` | 200 | IT门户 | `https://its.lianjia.com/software/windows` | 2 | 0.66s | — |
| 20 | P1 | `wiki.lianjia.com` | WIKI | `login_required` | 200 | 登录 - 贝壳 | `https://wiki.lianjia.com/login.action?ticket=[REDACTED]` | 2 | 1.73s | — |
| 21 | P1 | `yunpan.lianjia.com` | 贝壳云盘 | `unreachable_or_error` | 302 | — | `https://yunpan.lianjia.com/` | 2 | 0.61s | — |
| 22 | P1 | `new-meeting.ke.com` | 会议室预定 | `spa_shell_needs_browser` | 200 | 会议系统 | `https://new-meeting.ke.com/reserve` | 1 | 0.49s | — |
| 23 | P1 | `meeting-assistant.ke.com` | 会议室系统 | `accessible_verified_or_page` | 200 | — | `https://meeting-assistant.ke.com/qw-room` | 3 | 2.58s | — |
| 24 | P1 | `wen.ke.com` | 贝壳问卷 | `accessible_verified_or_page` | 200 | 贝壳问卷 | `https://wen.ke.com/home/create` | 2 | 2.72s | — |
| 25 | P1 | `ketalk.ke.com` | 贝壳圈 | `denied_403` | 403 | — | `https://ketalk.ke.com/?ticket=[REDACTED]` | 1 | 1.97s | — |
| 26 | P1 | `yes-or-no.ke.com` | Yes or No 员工交流 | `spa_shell_needs_browser` | 200 | Yes or No | `https://yes-or-no.ke.com/p/posts` | 2 | 0.53s | — |
| 27 | P1 | `profile.ke.com` | 人才管理 | `accessible_verified_or_page` | 200 | 贝壳人才管理系统 | `https://profile.ke.com/talent` | 2 | 2.94s | — |
| 28 | P1 | `profile-h5.ke.com` | 人才标准 | `accessible_verified_or_page` | 200 | 贝壳人才管理系统 | `https://profile-h5.ke.com/talent/development-channel` | 2 | 2.0s | — |
| 29 | P1 | `inrecruit.ke.com` | 内部活水 | `spa_shell_needs_browser` | 200 | 活水计划 | `https://inrecruit.ke.com/` | 1 | 0.5s | — |
| 30 | P1 | `spi.ke.com` | 运营绩效系统 | `accessible_verified_or_page` | 200 | 运营绩效 | `https://spi.ke.com/` | 2 | 2.79s | — |
| 31 | P1 | `codin.ke.com` | CHO 驾驶舱 | `spa_shell_needs_browser` | 200 | 奥丁数据分析平台 | `https://codin.ke.com/portal/3956/49460` | 2 | 1.01s | — |
| 32 | P1 | `chip.ke.com` | 电子合同下载校验 | `spa_shell_needs_browser` | 200 | 人事 | `https://chip.ke.com/hr/verify?type=contract&d=%7B%22tip%22%3A%22%E8%AF%B7%E6%82%A8%E5%A6%A5%E5%96%84%E4%BF%9D%E7%AE%A1%E7%94%B5%E5%AD%90%E5%90%88%E5%90%8C%EF%BC%8C%E5%A6%82%E6%9C%89%E7%96%91%E9%97%AE%E5%8F%AF%E8%81%94%E7%B3%BBHR%E5%92%A8%E8%AF%A2%22%2C%22title%22%3A%22%E7%94%B5%E5%AD%90%E5%90%88%E5%90%8C%22%2C%22originUrl%22%3A%22https%3A%2F%2Ffamily.ke.com%2FmyInfo%2Fprofile%23contractInfo%22%7D` | 2 | 0.55s | — |
| 33 | P1 | `mdm.ke.com` | 主数据管理平台 MDM | `accessible_verified_or_page` | 200 | 主数据管理平台 | `https://mdm.ke.com/` | 3 | 3.27s | — |
| 34 | P1 | `kepler.ke.com` | 税务平台 | `accessible_verified_or_page` | 200 | 税务平台 | `https://kepler.ke.com/` | 2 | 2.66s | — |
| 35 | P1 | `shandian.lianjia.com` | 闪店 | `accessible_verified_or_page` | 200 | 闪店 | `https://shandian.lianjia.com/` | 2 | 3.66s | — |
| 36 | P1 | `sis.lianjia.com` | SIS 门店损益 | `accessible_verified_or_page` | 200 | 集团SIS-门店损益 | `http://sis.lianjia.com/` | 3 | 2.09s | — |
| 37 | P1 | `legal-compliance.ke.com` | 合规工作台 | `accessible_verified_or_page` | 200 | 合规工作台 | `https://legal-compliance.ke.com/` | 2 | 3.18s | — |
| 38 | P1 | `m-legal.ke.com` | 合同管理 | `accessible_verified_or_page` | 200 | 法螺系统 | `https://m-legal.ke.com/` | 4 | 3.12s | — |
| 39 | P1 | `contract.ke.com` | 合同管理 | `accessible_verified_or_page` | 200 | 合同管理 | `https://contract.ke.com/` | 2 | 2.83s | — |
| 40 | P1 | `case-legal.ke.com` | 诉讼管理 | `spa_shell_needs_browser` | 200 | 贝壳诉讼管理 | `https://case-legal.ke.com/index` | 1 | 0.72s | — |
| 41 | P1 | `company-legal.ke.com` | 股权管理 | `accessible_verified_or_page` | 200 | 股权管理系统 | `https://company-legal.ke.com/companyManage` | 5 | 4.42s | — |
| 42 | P1 | `legal-lawyer-library.ke.com` | 律师库 | `spa_shell_needs_browser` | 200 | 律师库系统 | `http://legal-lawyer-library.ke.com/` | 1 | 0.34s | — |
| 43 | P1 | `vprivacy.ke.com` | 数据合规 | `spa_shell_needs_browser` | 200 | — | `https://vprivacy.ke.com/` | 1 | 1.14s | — |
| 44 | P1 | `mknow.ke.com` | 违纪通报 | `spa_shell_needs_browser` | 200 | 知识目录 | `https://mknow.ke.com/` | 1 | 0.6s | — |
| 45 | P1 | `homeworld.ke.com` | 行政服务 | `spa_shell_needs_browser` | 200 | 行政服务平台 | `https://homeworld.ke.com/` | 2 | 1.14s | — |
| 46 | P1 | `homeworld-front.ke.com` | 日常用品申请/公司菜单 | `spa_shell_needs_browser` | 200 | 行政服务 | `https://homeworld-front.ke.com/showmenu` | 2 | 0.66s | — |
| 47 | P1 | `zjjg.homelink.com.cn` | 理房通 | `unreachable_or_error` | — | — | `http://zjjg.homelink.com.cn/escrow/login.html` | 0 | 3.29s | RemoteProtocolError: Server disconnected without sending a response. |
| 48 | P2 | `admin.uc.lianjia.com` | UC 权限 | `denied_403` | 200 | 链家网-UC | `http://admin.uc.lianjia.com/noauth` | 3 | 2.45s | — |
| 49 | P2 | `apollo.portal.life.ke.com` | Apollo 配置中心 | `accessible_verified_or_page` | 200 | {{'Common.Title' \| translate }} | `http://apollo.portal.life.ke.com/` | 2 | 1.41s | — |
| 50 | P2 | `cloud.intra.ke.com` | 贝壳云、产品树 | `accessible_verified_or_page` | 200 | 贝壳云平台 | `https://cloud.intra.ke.com/home` | 8 | 4.42s | — |
| 51 | P2 | `easy.intra.lianjia.com` | 链家网 EASY | `accessible_verified_or_page` | 200 | 链家网EASY | `http://easy.intra.lianjia.com/?ticket=[REDACTED]` | 2 | 1.49s | — |
| 52 | P2 | `compass.ke.com` | 埋点平台 / 罗盘 | `spa_shell_needs_browser` | 200 | 罗盘 | `http://compass.ke.com/` | 1 | 0.28s | — |
| 53 | P2 | `design.ke.com` | KeDesign | `unreachable_or_error` | 404 | — | `http://design.ke.com/page/home` | 0 | 0.32s | — |
| 54 | P2 | `dawei.ke.com` | 知产管理 / 专利系统 | `login_required` | 200 | 大为Innojet知识产权协同创新管理系统 | `https://dawei.ke.com/login.html?ReturnUrl=%2F` | 1 | 1.93s | — |
| 55 | P2 | `risk-platform.ke.com` | 风控平台 | `spa_shell_needs_browser` | 200 | — | `https://risk-platform.ke.com/personal` | 1 | 1.03s | — |
| 56 | P2 | `weapons.ke.com` | 落兵台 | `login_required` | 302 | — | `https://weapons.ke.com/login?gotoURL=%2Flogin%3FgotoURL%3D%2Flogin%3FgotoURL%3D%2Fgroup%2F75428&ticket=[REDACTED]&ticket=[REDACTED]&ticket=[REDACTED]` | 1 | 4.92s | redirect_loop |
| 57 | P2 | `workflow.lianjia.com` | 通用审批流 | `accessible_verified_or_page` | 200 | 流程定义列表 | `http://workflow.lianjia.com/index` | 3 | 3.02s | — |
| 58 | P2 | `fssc.lianjia.com` | 老费控 | `accessible_verified_or_page` | 200 | — | `http://fssc.lianjia.com/;jsessionid=[REDACTED]` | 2 | 1.52s | — |
| 59 | P2 | `hecapp.homelink.com.cn` | AFA 爱发 | `login_required` | 200 | 链家集团费用报销管理系统 | `http://hecapp.homelink.com.cn/` | 2 | 0.4s | — |
| 60 | P2 | `ireport.lianjia.com` | 数据收集平台 | `denied_403` | 200 | 数据收集平台 | `http://ireport.lianjia.com/401` | 4 | 1.86s | — |
| 61 | P2 | `xinchou-data.ke.com` | 薪酬预处理平台 | `accessible_verified_or_page` | 200 | 薪酬数据预处理平台 | `https://xinchou-data.ke.com/` | 2 | 2.44s | — |
| 62 | P2 | `cb.ke.com` | 基薪管理 | `accessible_verified_or_page` | 200 | 基础薪酬 | `https://cb.ke.com/` | 2 | 2.5s | — |
| 63 | P2 | `salary-middle.ke.com` | 薪酬中心 | `accessible_verified_or_page` | 200 | 薪酬中心 | `https://salary-middle.ke.com/` | 2 | 2.57s | — |
| 64 | P2 | `salary-monitor.ke.com` | 服务监控 | `accessible_verified_or_page` | 200 | 服务监控 | `https://salary-monitor.ke.com/` | 2 | 2.41s | — |
| 65 | P2 | `salary-tax.ke.com` | 税友平台 | `accessible_verified_or_page` | 200 | 税友中心 | `https://salary-tax.ke.com/` | 2 | 2.32s | — |
| 66 | P2 | `shebao.ke.com` | 社保系统 | `spa_shell_needs_browser` | 200 | 社保公积金管理系统 | `https://shebao.ke.com/` | 1 | 0.61s | — |
| 67 | P2 | `ganbu.ke.com` | 干部画像 | `accessible_verified_or_page` | 200 | 干部档案 | `https://ganbu.ke.com/` | 2 | 1.98s | — |
| 68 | P2 | `leader.ke.com` | 管理者工作台 | `spa_shell_needs_browser` | 200 | hobber -- with ♥︎ by ke-fe | `https://leader.ke.com/` | 2 | 0.56s | — |
| 69 | P2 | `nltx.ke.com` | 职级平台 | `accessible_verified_or_page` | 200 | 积分职级 | `https://nltx.ke.com/pageo/integral-detail` | 4 | 3.35s | — |
| 70 | P2 | `pm.ke.com` | 人月系统 | `unreachable_or_error` | — | — | `https://pm.ke.com/` | 0 | 0.11s | ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1032) |
| 71 | P2 | `hrlearn.ke.com` | HR价值流 | `accessible_verified_or_page` | 200 | HR 门户 | `http://hrlearn.ke.com/home` | 5 | 2.13s | — |
| 72 | P2 | `recruit-ops.ke.com` | 招聘调研系统 | `unreachable_or_error` | — | — | `http://recruit-ops.ke.com/intelligence-library` | 0 | 133.9s | ProtocolError: Malformed reply |
| 73 | P2 | `resume-agc.ke.com` | 聘多多 | `accessible_verified_or_page` | 200 | 聘多多 | `https://resume-agc.ke.com/talent/candidateList` | 5 | 2.79s | — |
| 74 | P2 | `solicitude.hr.lianjia.com` | 员工关怀 | `unreachable_or_error` | 404 | — | `http://solicitude.hr.lianjia.com/` | 0 | 0.34s | — |
| 75 | P2 | `vm.ke.com` | 价值观评分 | `accessible_verified_or_page` | 200 | 价值观评分 | `https://vm.ke.com/` | 2 | 2.25s | — |
| 76 | P2 | `vote-admin.ke.com` | 荣誉评选系统 | `accessible_verified_or_page` | 200 | 荣誉系统 | `https://vote-admin.ke.com/pc/values/activityList` | 2 | 3.29s | — |
| 77 | P2 | `zhidao.ke.com` | 贝经堂 | `login_required` | 200 | 登录 | `https://zlogin.ke.com/login?site=https%3A%2F%2Fzhidao.ke.com%2F` | 4 | 1.44s | — |
| 78 | P2 | `admin-huaqiao.ke.com` | 花桥学堂 | `accessible_verified_or_page` | 200 | 花桥学堂CMS | `https://admin-huaqiao.ke.com/` | 2 | 2.5s | — |
| 79 | P2 | `admin-study.ke.com` | 贝好学管理端 | `accessible_verified_or_page` | 200 | 贝好学 | `https://admin-study.ke.com/` | 2 | 2.07s | — |
| 80 | P2 | `admin-sunshine.ke.com` | 阳光贝壳 | `spa_shell_needs_browser` | 200 | 贝壳管理系统 | `https://admin-sunshine.ke.com/web/` | 1 | 0.68s | — |
| 81 | P2 | `be-admin.ke.com` | 新经纪榜样 | `accessible_verified_or_page` | 200 | 新经纪榜样 | `https://be-admin.ke.com/pc/example/activitylist` | 2 | 2.4s | — |
| 82 | P2 | `esghub.ke.com` | 碳管理 | `spa_shell_needs_browser` | 200 | ESGhub | `https://esghub.ke.com/hub/beke/request` | 1 | 0.59s | — |
| 83 | P2 | `gongwei.ke.com` | 工位系统 | `unreachable_or_error` | 404 | — | `https://gongwei.ke.com/` | 0 | 0.58s | — |
| 84 | P2 | `gongyi.ke.com` | 贝壳公益 | `spa_shell_needs_browser` | 200 | 贝壳公益 | `https://gongyi.ke.com/web/pc/dataKanban` | 1 | 0.77s | — |
| 85 | P2 | `jingyingshe-admin.ke.com` | 精英社 | `accessible_verified_or_page` | 200 | 精英社 | `https://jingyingshe-admin.ke.com/` | 2 | 2.51s | — |
| 86 | P2 | `kehome-cms.ke.com` | 贝壳之家 | `accessible_verified_or_page` | 200 | 企业社会发展中心 | `https://kehome-cms.ke.com/columnManagement/columnList` | 2 | 2.57s | — |
| 87 | P2 | `lcm.ke.com` | 预实管理 | `accessible_verified_or_page` | 200 | 预实管理系统 | `https://lcm.ke.com/` | 4 | 3.84s | — |
| 88 | P2 | `m-intelligencecampus-hq.ke.com` | 老左图书馆 | `accessible_verified_or_page` | 200 | 花桥 | `https://m-intelligencecampus-hq.ke.com/LibraryBooking/bookingIndex` | 2 | 2.1s | — |
| 89 | P2 | `mcg.ke.com` | CG文化圈 | `accessible_verified_or_page` | 200 | mcg_ke | `https://mcg.ke.com/` | 2 | 2.25s | — |

---

## 4. 临时隧道释放确认

- `tunnel_cleanup`: `{"started": true, "terminated": true, "port_released": true, "return_code": 1}`
