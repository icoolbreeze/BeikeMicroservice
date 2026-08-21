# 优质清水房排名方案 V2.4

本文是「全市清水房优质指数排名」的设计基准。实现以本文为对照；实现与本文冲突时，
先改本文再改代码。

范围：全市清水房（CRM 租赁房源 `fitment=002` 毛坯），**2026-08-20 实测 `total = 3289`**
（`scope=all`，含整租与合租、普租与托管）。目标是给每套房算一个
**优质指数**，回答「同样的小区、同样的房型，这套房便宜得不正常吗」。

现状基线：`app/infrastructure/roughcast_rental_fetcher.py` 目前是「一页一透传」
（每次只按 `page` 向 crm_connector 请求一页，按页缓存 60 秒）。没有全量视图，
因此**做不了全局排序**。本方案要补的正是全量、离线、可解释的那一层。

## 已用真实上游验证的事实（2026-08-20，共 3 次请求）

本文原先有大量「待抓包确认」的假设。以下是**已经实测的**，读本文时不要再当假设；
其余未验证事项仍集中在第七章。

| 事实 | 实测结果 | 影响 |
| --- | --- | --- |
| 全市清水房总数 | `total = 3289`，同日复测 `3290` | 66 页；第三章节奏与预算按此重算 |
| 行级 `resblockId` | **有**，且把它塞回搜索能正确过滤 | 队列 B 每小区 1 请求，**suggest 归零**，预算余量从 14 次变成 82 次 |
| 行级装修状态 | **有** `fitmentStatus` + `fitmentStatusDesc`，租赁侧码值与销售侧一致（`002 毛坯 / 001 简装 / 003 精装`） | `is_roughcast` 直接读取；精装/简装可分开统计（原判断「做不到」是错的） |
| 装修状态可为空 | 某小区 10 套里 **2 套** `fitmentStatus` 为 `None` | **新增约束**：这类行 P 和 R 都不进，见第七章第 8 条 |
| 室厅卫 | **有** `bedroomAmount / hallAmount / bathroomAmount`（另有 `kitchenAmount`，不用） | S 级「室厅卫全等」可判 |
| 挂牌时长 | `alreadyCreateDays` **没有**；但有 `createTime`（epoch 毫秒） | 改存绝对时间戳，天数查询时现算，见 4.4 第 3 条 |
| 商圈 | **有** `bizCircleName`（实测 `'华侨城'`）；`bizCode` 为空 | 2.4.2 门槛 2 的「同商圈」可零额外请求实现，见第七章第 5 条 |
| 经纬度 | **没有**，搜索行无任何坐标字段 | 门槛 2 的「距离 ≤1.5km」需额外接口，每小区一请求 → 待决策 |
| 精确楼层 | `trueFloor` 为空，只有 `floorLevel='中'` | 印证第三章「全量抓取阶段禁止打详情」的代价是可接受的 |

复现脚本：`crm_connector/run/probe_rental_row.py`、
`crm_connector/run/probe_rental_community.py`。

## 第 2 期探针实测结果（2026-08-20，共 31 次请求）

脚本 `crm_connector/run/probe_s_coverage.py`，30 个小区、770 条在租行、77 套目标清水房。
产物：`probe_s_coverage.out.txt` / `probe_s_coverage_targets.csv` / `probe_s_coverage_communities.csv`。

### 三个覆盖率

| 指标 | 实测 | 第 2 期阈值 | 判定 |
| --- | --- | --- | --- |
| `s_grade_coverage` | **27/77 = 35.1%** | ≥60 / 20–60 / <20 | 落中档 |
| `strong_comparable_coverage` | 36/77 = 46.8% | 告警线 50% | 已低于告警线 |
| `valid_community_benchmark_coverage` | 66/77 = 85.7% | 告警线 60% | 健康 |

`benchmark_mode` 分布：`D_ONLY` 40.3% / `S_ONLY` 15.6% / `INVALID` 14.3% /
`B_ONLY` 14.3% / `S2_BLEND` 7.8% / `S1_BLEND` 6.5% / `S2_ONLY_WEAK` 1.3%；
`A_ONLY`、`C_ONLY`、`NEARBY` **一次都没出现**。

### 35.1% 不能直接当成「数据不支持 S 级」——先做归因

按预案，35.1% 落在 20–60% 档就该「六级压成三级」。但下钻 50 个 `n_S=0`
的目标后，病因分布是这样的（判别方法：`n_A>0` 说明 layout 相等过了、
是面积窗口卡住；`n_A=0` 且 `n_B>0` 说明同室数有货、是室厅卫三元组卡住）：

| 病因 | 个数 | 占失败 | 集中在 |
| --- | --- | --- | --- |
| **室厅卫三元组卡住**（同室数有参考，厅或卫差 1） | 19 | 38% | 4室 10/13、3室 5/12 |
| 连同室数的参考都没有 | 16 | 32% | 1室 9 |
| **面积窗口卡住**（layout 相等但差 >5㎡） | 9 | 18% | 1室 8 |
| 参考集为空 | 6 | 12% | |

两个**标定错误**，不是数据限制：

1. **`classify` 的 S/A 判据用绝对 5㎡ / 10㎡，`sim_area` 却用相对偏差**（σ=0.20）。
   同一个 5㎡ 在不同户型是完全不同的严格度：

   | 户型 | 面积中位数 | 5㎡ 相当于 | S 命中率 |
   | --- | --- | --- | --- |
   | 0室 | 24.0 | ±20.8% | 0/2 |
   | 1室 | 30.6 | ±16.3% | 4/26 |
   | 2室 | 78.8 | ±6.3% | **5/6** |
   | 3室 | 128.0 | ±3.9% | 10/22 |
   | 4室 | 184.1 | ±2.7% | 8/21 |

   2室 命中 83%、4室 只有 38%，不是因为 4室 数据差，是因为 ±2.7% 谁也过不了。

2. **S/A 要求室厅卫三元组完全相等，而 `sim_layout` 给「室厅相等、卫不同」打 0.90**。
   同一份方案里，权重函数说「几乎一样」，分级函数说「完全不可比」——这是自相矛盾。
   4室 的卫数本就自由浮动（4室2厅2卫 / 4室2厅3卫），10/13 的失败都栽在这里。

**所以「压成三级」有可能是在治症状。** 修掉这两处标定后 `s_grade_coverage`
预计升到 55–61%，正好压在 60% 档的边界上——**但这只是估计，必须实测**，
而实测受阻于下面这个探针缺陷。

### 探针缺陷：没存原始行，重新标定要再花一轮请求

探针只输出了打分后的 CSV，**没有落地 770 条原始 JSON 行**。
所以任何「换个判据再算一遍覆盖率」的尝试都要重跑 31 次请求。

> **规则：探针必须留下原始数据。**
> 否则每次重新标定都要再买一轮上游请求。这和「字段存在 ≠ 字段可用」
> 是同一类教训——探针的产物不只是结论，还包括**让结论可被重算的原料**。

### Resolver 的一处非单调缺陷（与覆盖率无关，是 bug）

`n_S == 1` 且 `secondary`（A∪B）不足 2 条时，2.3 直接返回 `INVALID`，
**没有落到 A/B/C/D 阶梯**。后果是**多一套 S 级参考反而丢掉基准**：

| 小区 | 参考集 | S/A/B/C/D | 实际 | n_S 若为 0 会是 |
| --- | --- | --- | --- | --- |
| 龙湖梵城 | 45 | 1/1/0/**25**/18 | `INVALID` | `C_ONLY`（n_C=25） |
| 华侨城粼港樾府 | 6 | 1/0/0/2/**3** | `INVALID` | `D_ONLY`（n_D=3） |

一个有 45 条同小区参考的房源被判为「无法给出基准」，这解释不通。
**修法**：`n_S == 1 && !secondary_ok` 不返回 `INVALID`，而是继续向下走
A/B/C/D 阶梯（`structure_cap` 取该档的值）。只有阶梯也全不满足才 `INVALID`。
影响 2/77 = 2.6%，`valid_community_benchmark_coverage` 由 85.7% → **89.6%**。
数量小，但它是正确性问题：加入一条更好的参考不该让结果变差。

顺带记一个次优（不是 bug）：`翡翠城五期` 有 9 个目标是 `0/1/1/33`，
1 条 A + 1 条 B 因不满 3 条被整体丢弃，改用 33 条 D 做基准。
信息上 1 条 A 强于 33 条 D。可考虑增设 `AB_ONLY`（`n_A+n_B ≥ 2`，置于 `C_ONLY` 之上），
影响 9/77 = 11.7%。优先级低于上面那个 bug。

### 约束 3 在真实数据上验证通过

`翡翠城五期` 一个小区有 **8 套几乎相同的清水房**，分级向量全部相同
（`0/1/1/33`）、`benchmark_mode` 全部 `D_ONLY`、基准值相同。
即「同小区 8 套同价清水房」并没有因为数量而互相抬分——
这正是约束 3 要的行为，且是在真实数据上出现的。**应固化为 fixture 回归测试**（见第八章）。

### δ 的首个实测值，以及尺度参数 `0.18` 实测过紧

这一节与分级阶梯无关，是探针**顺带产出的独立结论**，可以先于第 6 条落地。

`ln(reference_u / u)` 在 66 个拿到基准的目标上：中位数 **0.868**，
p10 = 0.286，p90 = 2.015。因为 `advantage = ln(reference_u/u) + ln(δ)`，
减去一个全市统一的 δ **只平移不改变离散度**，所以这份离散度可以直接用于标定 `0.18`。

**δ ≈ 0.42–0.49**（`e^−0.868` ~ `e^−0.705`），即**清水房单价应在装修房的一半以下**。
取值区间的上端来自只看强基准组（`S_ONLY` + `S2_BLEND`，18 个目标，中位数 0.705），
它比全体中位数可信——全体里混了 31 个 `D_ONLY`。
注意这仍不是 2.5 规定的口径（小区等权、同 comparable 逻辑），只能当量级参考。

离散度按基准强弱分层，`D_ONLY` 的噪声是强基准组的 3.5 倍：

| 基准强度 | n | 中位数 | p10 | p90 | IQR |
| --- | --- | --- | --- | --- | --- |
| 强（`S_ONLY`/`S2_BLEND`） | 18 | 0.705 | 0.251 | 1.484 | **0.451** |
| 中（`B_ONLY`/`S1_BLEND`/`S2_ONLY_WEAK`） | 17 | 0.876 | 0.304 | 1.163 | 0.302 |
| 弱（`D_ONLY`） | 31 | 0.947 | 0.202 | 2.148 | **1.582** |

这一列 IQR 独立印证了「弱基准的误差是方差、必须靠 `confidence` 承载」的判断，
也说明 `D_ONLY` 不该与强基准共用同一条映射曲线之外的任何优待。

**问题**：以强基准组为准，advantage 的 Q1–Q3 约 ±0.23、p10–p90 约 ±0.62。代入现行公式：

| advantage | `advantage/0.18` | `quality_score` |
| --- | --- | --- |
| Q1 −0.23 / Q3 +0.23 | ∓1.25 | **12 / 88** |
| p10 −0.62 / p90 +0.62 | ∓3.4 | **5 / 95（撞 clamp）** |

即**中间 50% 的房源就已铺满 12–88 分，外侧 20% 全部钉死在 clamp 上**，
分数在头尾失去分辨力。`0.18` 是按「便宜 5% 该得 62 分」这种直觉设的，
但真实残差比它宽 2.5–3 倍。

**建议**：`0.18 → 0.50`（版本化为新 `model_version`，历史分数不重算）。
推导：要让 p10/p90 落在 10/90 需 `k ≈ 0.44`，要让 Q1/Q3 落在 35/65 需 `k ≈ 0.67`，取中。

顺带一个收益：δ 越大，δ 本身的估计误差对分数的杠杆越大。δ 从 0.42 误估到 0.46
会让**每一套房**的 advantage 平移 `ln(0.46/0.42) = 0.09`；`k=0.18` 时这是 ±20 分，
`k=0.50` 时是 ±8 分。**放宽 k 同时降低了对 δ 标定精度的依赖。**

定案前提与有效期：上面的数字建立在「全市统一 δ」之上。若 δ 实际按小区/品类分化而
**方案不去建模**，这份离散度里就含着 δ 差异而全局 δ 吸收不了它，k 只能更宽；
反过来，将来若真引入分层 δ，残差收窄，**k 必须回调收紧**。两个方向都要重新标定。

还要明说一个弱点：`k` 的**取值**只建立在强基准组 18 个样本上，偏薄。
但**方向是确定的**——`0.18` 过紧不依赖分层：全体 66 个目标的 p10/p90 也一样撞 clamp。
所以处置是「按 `0.50` 落地为 provisional，第 1 期首轮全量（3289 套）跑完后用
正式样本重估 k 并升 `model_version`」，而不是等一个更大的探针。

### 新发现的数据脏点

- **`halls == 0 && baths == 0` 共 21 套（27% 的目标）**，其中 20 套是「1室0厅0卫」、
  2 套是 0室。这是本次最大的单一污染源，且**原先被低估了 10 倍**——
  先前只记了「`rooms == 0` 共 2 套」，真正卡住的是厅卫三元组里的那两个 `0`。
  证据是这 21 套的表现与其余 56 套完全不同：

  | 分组 | n | `n_S ≥ 3` | `n_S == 0` | `D_ONLY` | 小区参考集中位数 |
  | --- | --- | --- | --- | --- | --- |
  | `0H0B` | 21 | **0.0%** | **100%** | **95.2%** | **35** |
  | 其余 | 56 | 21.4% | 51.8% | 19.6% | 11 |

  **参考集中位数 35 却 100% 拿不到 S 级**，这不可能是数据稀缺，只能是判据问题：
  `1室0厅0卫` 在「室厅卫三元组全等」下永远匹配不上 `1室1厅1卫`。
  这与本节前面认定的标定错误 2 是同一个根因，此处只是给出量级。
  剔除这 21 套后 `D_ONLY` 由 **40.3% 降到 19.6%**，`s_grade_coverage` 为 21.4%
  （仍在 20–60% 档，但档内位置和病因分布都变了）。

  处置需要先定性，而**行里没有任何用途字段可用**：`productModelType` 为 `None`，
  `delTypeDesc` 只区分普租/托管，99 个键里没有住宅/公寓/非住宅的标识。
  所以只能用启发式，且两种解释导致的处置不同：
  - 若 `0` 是**未填**（录入缺失）→ 应按 `None` 走 `sim_layout` 兜底，不参与三元组全等；
  - 若 `0` 是**真实的单间/公寓**（29㎡/400元、参考池单价 100 元/㎡ 这类）→ 它是
    **另一个产品品类**，不该与住宅共用基准，需单独分层。

  两种解释都要求「`0H0B` 不进三元组全等判定」，这一条可以先落地；
  是否单独分层留待重跑探针后按原始行判断。
- **存在「1室 260㎡」**（1室 面积范围 29~260）。户型与面积严重不自洽，
  多半是 loft/别墅被录成 1室。需要面积-户型一致性哨兵，否则它会污染同室数参考池。

## 〇、V2 相对 V1 改了什么

V1 有两个真实缺陷，V2 修掉它们，其余是精度和工程可靠性的加强。

| # | V1 的做法 | V2 的做法 | 为什么必须改 |
| --- | --- | --- | --- |
| 1 | 优质指数 = advantage 的**全市百分位** | 固定单调映射 `50+45·tanh(advantage/k)` | 百分位把「数量无关」的 advantage 又塞回一个**依赖全市经验分布**的映射里。某小区涌入 20 套低价房，分布左移，**别的房源分数会跟着变**，直接违反约束 3。且百分位没有绝对含义：全市都不便宜的那天，只便宜 2% 的房也能拿 99 分 |
| 2 | 一轮采集没有「完整」的定义，`last_seen_at` 直接标下架 | `roughcast_crawl_runs` + **只有 COMPLETE 的 run 才允许发布** | 抓到第 37 页 session 掉了，V1 会把 38–60 页的房源**全部标记下架**。这是静默数据损坏，不是新鲜度问题 |
| 3 | 单一 `roughcast_listings` 表混装当前态与历史 | 拆 `..._listing_current` / `..._listing_snapshot` | 历史参考、可复现、下架判定都需要两者分离 |
| 4 | 同室数分组 + 一句没有公式的「面积弹性修正」 | S/A/B/C/D 分级 comparable，同房型 + 面积 ±5㎡ 优先 | 小区中位数会被 40㎡ 一室和 140㎡ 四室**稀释**掉 88–92㎡ 三室的真实行情 |
| 5 | 可信度只体现在 `reason` 文案里 | 独立 `confidence_score`（0–100，四维） | 文案不能排序、不能筛选，业务等于拿不到它 |
| 6 | 全市 1/99 分位裁剪，超出者不给分 | 区分 `data_error`（不给分）与 `extreme_price`（给分并标记待核） | 「静默删掉最便宜的房源」删掉的正是这个功能存在的理由 |
| 7 | 历史数据未参与参考 | 本小区历史（30/60/90 天）**优先于**周边小区 | 同小区一个月前的高相似房源，比 1.5km 外另一个小区今天的数据更可信 |
| 8 | δ 无版本 | δ 与全部模型参数版本化入库 | 固定映射下 δ 直接平移每一个分数；百分位时代 δ 漂移会部分抵消，现在不会 |
| 9 | δ 的估计口径未定义 | δ 必须用**与评分完全相同的 comparable 选择逻辑**估计 | 见 2.5，这是最容易埋进去、且最难发现的系统性偏差 |

### V2.1 相对 V2 改了什么

V2.1 **不改主算法**，只消除实现歧义、补齐边界定义、修正过度承诺。
方向性设计（固定映射、三指标分离、δ 小区等权与同口径、回退顺序、
COMPLETE run 才发布、current/snapshot 分表、整租合租隔离、极端价照常评分）
全部保留不动。

| # | V2 留下的问题 | V2.1 的修法 |
| --- | --- | --- |
| 1 | S/A/B/C/D 条件重叠：一套 S 级房源同时满足 A、B、D | 等级**严格互斥**，`S→A→B→C→D` 命中即停，`classify_comparable` 返回单值 |
| 2 | 2.3 说「S=2 可融合出基准」，2.4 又说「某等级 ≥3 才算 community」——**两条互相冲突，开发者无法实现** | 判定权收归唯一的 **Comparable Pool Resolver**；能产出 valid benchmark 就是 `community` |
| 3 | `S=2` / `S=1` 且**无 secondary** 时怎么办，没有定义 | `S=2` 无 secondary → 纯 S + cap 70；`S=1` 无 secondary → `INVALID`，转历史回退 |
| 4 | secondary 池没有最低样本要求，`S=1 + A=1` 会伪装成拥有市场基准 | secondary 需 **≥2 套独立 listing**，否则 `unavailable` |
| 5 | `community_history` 没有 confidence 上限，90 天前的数据能拿 80 分 | 按数据年龄分档：100 / 80 / 70 / 60 / 45 |
| 6 | confidence 上限散落在 2.3 和 2.4 两处，代码路径各自截断 | 统一为 `min(raw, source_cap, structure_cap)`，全局唯一截断点 |
| 7 | 排名用取整后的整数分，82.49 与 82.01 都显示 82 且无法定序 | 新增 `quality_score_raw REAL`，排名用它 |
| 8 | 「S 覆盖率 <20% → S 级降为加分项」易被读成 `quality += bonus` | 改为「调整 Comparable Pool 策略」，明文禁止任何 Quality 加分项 |
| 9 | 预算按「小区数」估算，但一个小区可能是多页 + 重试 | 预算按**真实上游请求**逐次扣减，每请求扣 1 |
| 10 | 声称 5 个字段即可完全复现，但 `nearby`/`history` 涉及多小区多 run | 增 `benchmark_pool_json` 记录实际参考池，并重述可复现性的边界 |
| 11 | 缺 `reference_unit_rent`，解释链断在「装修房参考价是多少」这一环 | 正式入库并出 API |
| 12 | 排查「为什么是这个基准」只能解析 `reason` 字符串 | 正式入库 `benchmark_mode` |
| 13 | `s_grade_coverage` 一个名字同时表示「S 级覆盖率」和「S/A 级覆盖率」 | 拆成 `s_grade_coverage` / `strong_comparable_coverage` / `valid_community_benchmark_coverage` 三个指标 |
| 14 | 回归测试只覆盖主路径，弱参考分支与预算扣减无测试 | 新增 A–Y 二十五条边界测试（第八章） |
| 15 | 周边基准的房源照常进 `city_rank`，而首键是 `quality_score_raw`，于是周边估算的 92 分压过本小区实测的 88 分 | 周边整体降级：三道进入门槛（含**离散度护栏**）+ 不进主排名（`nearby_estimate`）+ 只显示档位，见 2.4.2；新增测试 K–P |
| 16 | 「历史优先于周边」写了顺序但没写**必须穷尽**，实现可能 30 天不 valid 就跳周边 | 2.4.1 明确三档全部不 valid 才允许进周边，测试 K 用 spy 断言未加载周边数据 |
| 17 | 参考快照按批次 append，但**没有指针说明该读哪一批**，实现只能猜 `max(run_id)`——会读到 ABORTED run 的半截批次 | `roughcast_communities.reference_run_id` 指向最近一个 COMPLETE 批次，与 `listing_current` 同事务前移（4.1 / 4.2），测试 S |
| 18 | 日快照逐轮全量写入 = 110 万行/年，绝大多数是重复行；且**没有任何保留策略**，等于默认永久 | 改为**变更点写入**（约 3 万行/年，同时免费得到价格变动史），并给出分层保留表（4.5），测试 Q/R/T |
| 19 | `first_seen_at` / `last_seen_at` / `captured_at` / `listed_days` 四个时间语义未定义，新鲜度极易算错 | 4.4 给出四者语义与三条硬规则；明确 `listed_days` 当前拿不到，`first_seen_at` 只是挂牌时长的**下界** |

### V2.2 相对 V2.1 改了什么

V2.2 **不含算法结构改动**，全部由 2026-08-20 的三次真实上游请求驱动——
把假设换成实测，并修掉实测暴露出的一个真实漏洞。

| # | V2.1 的写法 | V2.2 的修正 | 触发原因 |
| --- | --- | --- | --- |
| 1 | 「行级装修拿不到，精装/简装分开统计**做不到**」 | 行级 `fitmentStatus` 可读，两件事都能做 | 抓包证明原判断是错的。**这是本次唯一被推翻的结论** |
| 2 | P 与 R 二分：清水房 / 非清水房 | **三分**：清水房 / 非清水房 / **装修未知**，第三类两边都不进（2.1、第七章第 8 条） | 实测某小区 10 套里 2 套 `fitmentStatus` 为空。二分法会把它们默认塞进 R，等于赌它们不是毛坯——赌输就污染基准 |
| 3 | `resblock_id` 可能需要 suggest 解析，预算上界 246 / 硬顶 260 | suggest 归零，预算 178，余量 82 | `resblockId` 可读且塞回搜索能正确过滤（已验证闭环，不只是字段存在） |
| 4 | 挂牌时长只有 `first_seen_at` 这个下界 | 改用 `createTime`（epoch 毫秒）；存绝对时间戳，天数查询时现算 | `alreadyCreateDays` 不存在，`createTime` 存在且比它更好 |
| 5 | 总数「约 3000」 | 实测 3289 / 复测 3290，66 页 | 队列 A 页数、节奏、预算全部按实测重算 |
| 6 | 第 2 期探针需要建库和连接器改动先落地 | 探针降级为一次性脚本（取 resblockId → 逐小区搜 → CSV），并**必须一并输出装修状态分布** | 只测 S 级命中率会把「参考池被空装修行缩小」误诊成「S 级判据太严」，从而改错东西 |

第 3 条那类收益值得强调一次：**「字段存在」和「字段可用」是两件事**。
`resblockId` 存在只说明能读；把它塞回 `resblock_ids` 搜出来的
`resblockName` 只有一个值，才说明读写口径一致、队列 B 真的能省掉一次请求。
第七章剩下的未验证项也应按这个标准收口，不要只确认字段在不在。

### V2.3 相对 V2.2 改了什么

V2.3 由第 2 期探针（31 次真实请求、770 行、77 目标）驱动。
**算法结构有一处 bug 修正，但分级阶梯本身尚未定稿**——见下表第 6 条。

| # | V2.2 的写法 | V2.3 的修正 | 触发原因 |
| --- | --- | --- | --- |
| 1 | Resolver：`n_S==1` 且 secondary 不足 → `INVALID` | **落到 A/B/C/D 阶梯**，只有阶梯也不满足才 `INVALID`（新测试 V，并同步修正测试 E） | **非单调 bug**：`龙湖梵城` 有 45 条同小区参考、25 条 C 级，却因为「恰好只有 1 条 S」被判无基准。多一条更好的参考不该让结果变差 |
| 2 | 门槛 2 待定：同商圈优先 + 跨商圈距离 ≤1.5km | **降级为「只允许同商圈」**，放弃坐标接口（第七章第 5 条已定案） | 实测 `valid_community_benchmark_coverage = 85.7%`（修 bug 后 89.6%），周边分支需求 <10.4% 且还会被历史回退吃掉。为它加 60 次请求会吃光余量 69 |
| 3 | 队列 B 假设 1.3 页/小区，预算 178、余量 82 | **1.5 页/小区**，预算 191、余量 69；并注明这是偏高估计（抽样偏向大盘小区） | 实测 11/30 小区超 50 条，最大 162 条。「V1 只取前 2 页」会漏掉一半参考 |
| 4 | 合租风险需抓数确认，可能整体排除 | 风险**结构性消除**：`classify()` 首判 `rentType` 相等，合租不可能成为整租目标的 comparable；且 77/77 目标全是整租。2.8 保留为一行守卫 | 探针实测 |
| 5 | 装修为空占比未知，担心到 20% | 实测 **5.7%**，不构成覆盖率问题；但发现新问题：R 混装 `001`(101) 与 `003`(548)，档次比例悬殊会引入**偏差**（新增第七章第 9 条） | 探针实测 770 行装修分布 |
| 6 | 「`s_grade_coverage` 落 20–60% → 六级压成三级」 | **暂不执行。** 实测 35.1%，但归因显示 38% 的失败源于「室厅卫三元组过严」、18% 源于「绝对 5㎡ 窗口对大户型过紧」——都是**标定错误**，不是数据限制 | 见文首归因表。压成三级可能是治症状；但**重新标定后的覆盖率无法离线复算**，因为探针没存原始行 |
| 7 | — | 新增数据脏点规则：`rooms == 0` 不可排名、户型-面积一致性哨兵（新测试 W、X） | 实测 2 套 0室、1 套「1室 260㎡」 |
| 8 | 尺度参数 `0.18`，注「首轮数据出来后可以调」 | **调为 `0.50`**（新 `model_version`，历史分数不重算） | 实测强基准组 advantage 的 p10–p90 约 ±0.62、Q1–Q3 约 ±0.23。`0.18` 下中间 50% 就铺满 12–88 分、外侧 20% 全部撞 clamp，头尾失去分辨力 |
| 9 | δ 只有口径定义（2.5），无任何实测量级 | 首个实测：**δ ≈ 0.42–0.49**，即清水房单价应在装修房**一半以下** | 66 个目标的 `ln(reference_u/u)` 中位数 0.868（强基准组 0.705）。同时说明 δ 的标定误差杠杆很大，是放宽 k 的第二个理由 |
| 10 | 数据脏点：「`rooms == 0` 共 2 套」 | **量级低估 10 倍**：真正卡住的是 `halls==0 && baths==0`，共 **21 套 = 27% 的目标** | 这 21 套参考集中位数 35 却 **100% 拿不到 S 级**、95.2% 落 `D_ONLY`；剔除后 `D_ONLY` 由 40.3% 降到 19.6%。它是标定错误 2 的最大受害者 |

第 6 条是本次唯一悬而未决的事，**它卡在一个可以避免的失误上**：
探针只输出了打分后的 CSV，没有落地原始 JSON 行，所以「换判据再算一遍」
必须重跑 31 次请求。**探针的产物不只是结论，还包括让结论可被重算的原料。**

### V2.4 相对 V2.3 改了什么

V2.4 由**第 1 期开工前的落地对照**驱动，没有新的上游请求。
算法一个字未改，改的是分期边界、DDL 字段清单和两处会导致静默数据错误的实现细节。

| # | V2.3 的写法 | V2.4 的修正 | 触发原因 |
| --- | --- | --- | --- |
| 1 | 第 1 期出口条件含「离线复算三个覆盖率并定稿 2.2 阶梯」，且称第 1 期跑完就有「全部小区参考集」 | 覆盖率复算与阶梯定稿、`classify`/Resolver、四条修正**全部挪到第 3 期**；第 1 期是纯采集 | **自相矛盾**：队列 A 带 `fitment=002`，只采到 P；三个覆盖率全定义在参考集 R 上，而 R 只能由第 3 期的队列 B 产出。只有 P 时分子恒为 0。详见第六章新增小节 |
| 2 | 4.1 的 `listing_current` / `listing_snapshot` 字段清单无 `resblock_id / fitment_status / create_time` | 补齐，并补 `bizcircle / fitment_status_desc`（`bizcircle` 建列不填，见下） | 第六章本来就要求队列 A 入库写这几个字段，4.1 的清单漏列 |
| 3 | 4.1 的 `crawl_runs` 无处记录每日 `total` | 增 `upstream_total / unknown_fitment_count / non_roughcast_count` | 第七章第 7 条要求连续 3 天观察 `total` 波动、第 9 条要求空装修行计数落库，此前都没有落地位置 |
| 4 | 4.2 规则 1：采集过程直接写 `listing_snapshot` | 新增 `roughcast_crawl_stage`，采集期**只写 stage**；变更点写入与 current 刷新一起进 COMPLETE 事务 | 4.5 的变更点逻辑在「无变化」时会就地 UPDATE `last_confirmed_*`，于是 ABORTED run 仍改动了 snapshot 既有行。「ABORTED 绝不发布」因此退化成一条读时纪律——而 4.2 自己就说这是「最难通过抽查发现的一类错误」 |
| 5 | 4.5 只给出 `content_hash = hash(...)` | 明确**哈希前先规范化数值**（`round(x,2)` + 固定序列化 + `None` 唯一表示） | `4300` / `4300.0` / `"4300"` 会算出三个 hash，每轮都判成变更点，4.5 的 36 倍收益归零，而症状只是「快照表长得有点快」 |
| 6 | 第三章预算：扣减发生在发起请求处 | 补两条：已花额度**以 `crawl_log` 现算**（内存只作缓存）；记录 connector `_maybe_autorefresh` 的记账缺口 | 内存计数器一重启就忘掉当天已花额度，硬顶形同虚设。connector 在 401 时自行重发的那次请求是 store_media 节流器看不见的真实上游请求 |
| 7 | 熔断只列 429 / 验证码 / session 失效 / 连续 3 次失败 | 补出四条信号对应的 connector 出口（429 / 401+`CRM_AUTH_REQUIRED` / 502+`CRM_UPSTREAM_CHANGED`），并新增 `window_closed` 中止原因 | 「验证码」在实现里的可观测形式是 HTTP 502——`kecom_session_provider` 对非 JSON 的 200 body 抛 `UpstreamChangedError`。不写清楚就会漏掉这条最关键的触发信号 |
| 8 | 队列 A「`page_size=50`，**66 页**」 | 改为「页数每轮由第 1 页的 `total` 现算，66 是 2026-08-20 的实测值」 | 与第七章第 7 条一致；66 写成常数会在市场变动后静默漏采 |

第 1 期只建 6 张表（`crawl_runs / crawl_log / crawl_stage / listing_current /
listing_snapshot / communities`）。`community_reference_snapshot` 留到第 3 期，
`community_month_benchmark` 与 `listing_scores` 留到第 4 期——
`CREATE TABLE IF NOT EXISTS` 是纯追加，延后建表零代价。

## 一、设计约束

来自业务方的硬约束，实现不得违背：

| # | 约束 | 落地方式 |
| --- | --- | --- |
| 1 | 优质指数**只参考本小区** | 基准取自本小区的非清水房在租房源，见 2.2 与 4.1 的口径说明 |
| 2 | 本小区无参考数据时，参考**周边小区** | 回退链 本小区当前 → 本小区历史 → 周边 → 无分 |
| 3 | 同小区 2 套同价位与 20 套同价位，**评分必须差不多**；数量不得抬高优质指数 | 参考集与被排序集分离（2.1）+ 小区等权（2.5）+ 固定映射（2.6），三处共同保证 |
| 4 | 每天更新，节奏必须平缓 | 队列 A + 共享节流器 + 熔断，见第三章 |
| 5 | 小区数据**暂存本地库**，评分时不再反复筛选小区 | `roughcast_communities` + `..._community_reference_snapshot`，命中即取 |
| 6 | 本地小区数据也要更新，全量周期可拉长到**半个月** | 队列 B，每日配额 `ceil(小区总数/15)` |

## 二、算法

### 2.1 参考集 ≠ 被排序集（约束 3 的第一道保证）

- **被排序集 P**：全市清水房，实测 3289 套（2026-08-20）。
  判据是行级 `fitmentStatus == '002'`（2026-08-20 抓包确认，见第七章第 1 条）。
- **参考集 R(小区)**：该小区的**非清水房**在租房源，判据 `fitmentStatus in ('001','003')`。
- **两个集合都不进的第三类**：`fitmentStatus` 为空的行（实测存在，见第七章第 8 条）。
  它不是「非清水房」——它是**装修未知**。塞进 R 就等于赌它不是毛坯，赌输就污染基准。

清水房本来就该比装修房便宜，所以不能直接和 R 比。引入**清水房应有折价** `δ`（见 2.5）。

**反例（本方案明确拒绝的做法）**：拿「本小区清水房自己的中位单价」当基准。
某小区 20 套清水房都在 30 元/㎡ → 中位数就是 30 → 20 套折价全为 0，
哪怕 30 元/㎡ 在全市便宜得离谱；而另一个小区仅有 1 套时基准被拉向父级，
同样的便宜程度却能拿高分。分数含义会随样本量漂移，且簇内偶数套时中位数落在
两套之间，一半微正一半微负——**簇内排序退化成噪声**。这条写进文档是为了防止回归。

### 2.2 参考房源分级

对目标清水房 `t`，在同小区非清水房里挑 comparable `p`。**只与同租赁模式比较**
（整租对整租，合租对合租），理由见 2.8。

| 级 | 条件（**自上而下命中即停，互斥**） | grade 分 |
| --- | --- | --- |
| S | 同小区 + 同租赁模式 + **室厅卫全等** + `\|面积差\| ≤ 5㎡` | 100 |
| A | 不属于 S + 同小区 + 同租赁模式 + 室厅卫全等 + `5㎡ < \|面积差\| ≤ 10㎡` | 85 |
| B | 不属于 S/A + 同小区 + 同租赁模式 + **室数相等**（厅卫可不同）+ `sim_area ≥ 0.6` | 65 |
| C | 不属于 S/A/B + 同小区 + 同租赁模式 + 室数差 1 + `sim_area ≥ 0.5` | 45 |
| D | 不属于 S/A/B/C + 同小区 + 同租赁模式，其他可用非清水房 | 25 |
| N | 周边小区（走 2.4 第 3 级） | 15 |

**硬约束**：`S ∩ A ∩ B ∩ C ∩ D = ∅`。实现必须是一个返回**单个** grade 的函数

```
classify_comparable(target, candidate) -> Grade    # 'S' | 'A' | 'B' | 'C' | 'D' | None
```

**不允许**「分别查五个集合再合并」。V2 的等级条件是重叠的（`|面积差| ≤ 5㎡`
同时满足 A 的 `≤ 10㎡`、B 的室数相等、D 的任意房型），分别查询会让一套 S 级房源
同时落进 A、B、D 三个池，样本数直接虚增三倍，`n_eff` 和 confidence 一起失真。
V2.1 给 A/B/C/D 加上「不属于上一级」前缀，就是为了让互斥性写在**条件里**
而不是靠调用方自觉。

朝向**不参与分级**，只作为 `reason` 的补充说明。房型和面积是价格的一阶因素，
朝向是二阶的；把朝向提进硬条件会把本来够用的 S 级样本切碎。

相似度函数（`a_t` 为目标面积）：

```
r         = |a_t - a_p| / a_t
sim_area  = exp( -(r / 0.20)^2 / 2 )        r=0.05→0.97  0.10→0.88  0.20→0.61  0.30→0.32
```

| 房型关系 | `sim_layout` |
| --- | --- |
| 室厅卫全等 | 1.00 |
| 室、厅相等，卫不同 | 0.90 |
| 室相等，厅不同 | 0.80 |
| 室差 1 | 0.50 |
| 室差 ≥ 2 | 0.20 |

新鲜度权重 `w_fresh`：当前态 1.00；历史 ≤30 天 0.85；≤60 天 0.70；≤90 天 0.55。

综合权重 `w_p = sim_layout × sim_area × w_fresh`。

### 2.3 Comparable Pool Resolver（先定池、再融合）

**禁止逐条累加权重**。不允许把所有 comparable 按权重累加成一个「总证据量」——
那样一个小区堆 20 套 D 级房就能伪造出高可信度，密度又从后门溜回来了。

正确做法：**先按等级确定池，再在池内做加权中位数**。判定统一由
`Comparable Pool Resolver` 完成，它返回一个 `BenchmarkResult`：

```python
@dataclass
class BenchmarkResult:
    is_valid: bool                     # 能否产出有效基准
    benchmark: float | None            # reference_unit_rent (元/㎡·月)
    benchmark_mode: str                # 见下方取值表
    pool: dict                         # 实际参与的分级池结构
    n_eff: float                       # 池内有效样本量
    structure_cap: int                 # 该结构下的 confidence 上限
    peer_scope: str                    # 'community' 或回退
```

#### Resolver 逻辑

设 `pool = classify_all(target, candidates)`，将候选房源按 `classify_comparable`
归入 S/A/B/C/D。**定义 secondary = A ∪ B，且要求 `|secondary| ≥ 2`**（独立 listing
数，非权重和），否则 `secondary = unavailable`。

| 条件 | `benchmark_t` | `benchmark_mode` | `structure_cap` |
| --- | --- | --- | --- |
| `n_S ≥ 3` | `weighted_median(S)` | `S_ONLY` | 100 |
| `n_S = 2` 且 secondary 可用 | `0.80 × wmedian(S) + 0.20 × wmedian(secondary)` | `S2_BLEND` | 100 |
| `n_S = 2` 且 secondary 不可用 | `weighted_median(S)` | `S2_ONLY_WEAK` | 70 |
| `n_S = 1` 且 secondary 可用 | `0.40 × (唯一 S) + 0.60 × wmedian(secondary)` | `S1_BLEND` | 60 |
| `n_S = 1` 且 secondary 不可用 | **INVALID** | — | — |
| `n_S = 0`，A→B→C→D 中首个 ≥3 的等级 | `weighted_median(该等级)` | `A_ONLY` / `B_ONLY` / `C_ONLY` / `D_ONLY` | 取该等级 grade 分 |
| 以上都不满足 | **INVALID** | — | — |

`INVALID` 不是错误，是信号：调用方应走 2.4 回退链。**不得**在 INVALID 时
用空池或单样本产出基准并期望调用方自行判断。

`weighted_median` = 按 `w_p` 加权的中位数（权重累加过半处的样本值）。
对空池未定义，表中每一行都不会传入空池——INVALID 分支的存在正是为了防止。

`n_eff = Σ w_p`（池内权重和）。**`n_eff` 只能进入 `confidence_score`，
不允许进入 `quality_score`、`expected_unit_rent`、`advantage` 或
`comparable_grade`**。这是约束 3 的第四道防线（参考集分离、小区等权、
固定映射、以及本条：数量信息与质量值的完全解耦）。

#### 设计说明

- `n_S = 2 + secondary` 用 `structure_cap = 100`：因为此时 blend 中的
  secondary 权重只占 20%，且 secondary 有最低 2 套的要求，S 级主导地位明确。
- `S2_ONLY_WEAK` 虽然用了纯 S 中位数，但 2 套样本无次级交叉验证，
  设 `structure_cap = 70` 反映「结构偏弱」。
- `n_S = 1` 无 secondary → INVALID：单套房源没有任何交叉验证，
  用它定价比没有基准更危险。宁可让房源从前端「高可信捡漏」筛掉，
  也不能让人误以为「1 套就是可信的市场基准」。

> `n_S = 1` 的融合比例，V2 提案原文给的是 55/45。这里改成 40/60：
> 55% 的权重压在单套房源上，一份异常报价就能带偏整条结论，
> 而单套样本恰恰没有任何交叉验证。宁可让它显示为「参考偏弱」被筛掉。

### 2.4 回退链（约束 2）

| 顺序 | 数据源 | 进入条件 | `peer_scope` | `source_cap` | 进主排名 |
| --- | --- | --- | --- | --- | --- |
| 1 | 本小区**当前**参考快照 | Resolver 返回 `is_valid` | `community` | 100 | ✅ |
| 2a | 本小区历史 ≤30 天 | Resolver 返回 `is_valid` | `community_history` | 80 | ✅ |
| 2b | 本小区历史 31–60 天 | 同上 | `community_history` | 70 | ✅ |
| 2c | 本小区历史 61–90 天 | 同上 | `community_history` | 60 | ✅ |
| 3 | 周边小区（同商圈或 ≤1.5km） | 见 2.4.2 的三道门槛 | `nearby` | 45 | ❌ **单独分组** |
| 4 | — | 以上都不满足 | `insufficient` | 无正式 confidence | ❌ |

**唯一判据是 Resolver 是否返回 valid benchmark。回退链自己不再另设
「某等级样本 ≥3」的门槛。**

这是 V2 里一处真实的实现级冲突：2.3 允许 `n_S = 2 + secondary` 融合出基准，
2.4 又要求「本小区某等级样本 ≥3」才算 `community`。于是 `S=2, A=3` 这种
非常常见的情形，按 2.3 该判 `community`，按 2.4 该进历史回退——两条规则
指向相反结果，开发者无从下手。V2.1 把门槛全部收进 Resolver，
回退链只负责「换数据源再问一次」。

#### 2.4.1 本小区历史必须先被穷尽

**顺序是硬的：本小区历史优先于周边小区，没有例外。** 理由不只是「同小区更像」，
还有成本结构上的不对称：

- 历史回退**零上游请求**。`roughcast_community_reference_snapshot` 与
  `roughcast_listing_snapshot` 里已经躺着过去 90 天的每一轮快照，
  查历史就是一条本地 SQL，可以查得很彻底，代价是零。
- 周边回退**需要另外 N 个小区的参考数据**。那些数据要么已在本地库
  （但它们各自的新鲜度、覆盖情况都不受本小区任务控制），要么还得排进队列 B 等轮转。

所以实现上不允许出现「本小区历史只查 30 天，不 valid 就去查周边」这种偷懒路径。
必须**逐档尝试到 90 天为止**：≤30 天喂给 Resolver，不 valid 才放宽到 60 天、
再到 90 天，`source_cap` 随档位下降；**三档全部不 valid，才允许进入周边**。

历史样本**必须按 `DISTINCT listing_id` 去重，每个 id 只取最近一条快照**——
同一套房存在 30 天就是库里 30 行，朴素 `COUNT(*)` 会得到 `n=30`，
样本量与 confidence 双双虚增。去重后取的那条快照的 `captured_at`
决定它的 `w_fresh` 与 `reference_age_days`。

> 90 天是上限，不是可调参数。再往前的租金水平已经不是「同一个市场」，
> 拿它当基准得到的偏差不比周边小区小，却因为 `peer_scope = community_history`
> 而顶着一个偏高的 `source_cap`——那比直接用周边更危险。
> 要延长必须先有数据支撑（同小区租金 90 天以上的实际漂移幅度）。

#### 2.4.2 周边回退是**估算**，必须整体降级

周边基准的误差来源和本小区完全不同：本小区的弱参考误差是**方差**
（样本少、噪声大，`confidence_score` 正好描述这件事），而周边基准的误差是**偏差**
——隔一条街的小区，单价系统性地高 15% 或低 15%，这个偏差会**原封不动地穿过
`advantage` 进入 `quality_score`**。压低 confidence 对它无效：分数本身就是错的，
不是「对但不确定」。

因此周边必须在三个层面同时降级：

周边基准的算法本身不变，仍是**小区等权**：

```
benchmark_nearby = median( b_1, b_2, ..., b_k )      k ≥ 3
```

`b_i` 是**第 i 个周边小区自己的 benchmark 值**，不是「周边所有房源的中位数」。
按房源等权会让一个 200 套在租的大盘小区独自决定周边基准，密度又从后门溜回来。
`nearby` 的池元素因此是**小区聚合值**而不是房源——这一点在 4.3 的可复现性
设计里有直接后果（一个 `reference_run_id` 描述不了 k 个小区的数据来源）。

**① 三道进入门槛**（任一不满足 → 不产出周边基准，直接判 `insufficient`）：

```
门槛 1  覆盖 ≥3 个各自 benchmark_mode ∈ {S_ONLY, S2_BLEND, A_ONLY} 的小区
        —— 周边小区自己的基准必须是强基准。用一堆 D_ONLY 的邻居
           拼出来的中位数，误差叠误差，没有任何意义
门槛 2  同商圈优先；跨商圈时距离 ≤1.5km 且必须记录实际距离
门槛 3  离散度护栏：设 3+ 个小区基准值为 b_1..b_k，
        spread = (max(b) − min(b)) / median(b)
        spread > 0.25 → 判定周边不可用（邻居们自己都不一致，
                        凭什么相信它们的中位数适用于本小区）
```

门槛 3 是这一节的关键。它把「周边到底准不准」从一句主观判断变成**可测量、
可断言的量**：邻居之间的分歧幅度就是我们对该基准误差的下界估计。
`spread` 必须入库（`benchmark_pool_json` 里记全部 `b_i` 与算出的 `spread`），
并进监控。

**② 不进主排名。** `quality_status = 'nearby_estimate'`，
`city_rank = NULL`。它进前端的「周边估算」独立分组，可以看、可以按分数排序，
但**永远不与本小区有实测参考的房源混在一张榜上**。

这是本次修订相对 V2 的实质改动：V2 让周边房源照常进 `city_rank`，
而 `city_rank` 首键是 `quality_score_raw`。结果是一套周边估算的 92 分
稳稳压过本小区实测的 88 分，`confidence_score = 45` 只是个徽标，拦不住它。
「全市优质清水房 Top 20 里有 6 套是猜的」——这个产品就废了。

**③ 展示为档位，不给精确整数。** 周边分组里不显示 `优质指数 88`，
只显示三档：

| `quality_score_raw` | 档位标签 |
| --- | --- |
| ≥ 70 | 疑似划算（周边估算） |
| 40 – 70 | 一般（周边估算） |
| < 40 | 疑似偏贵（周边估算） |

精确整数会被当成可信结论去比较（「这套 88 比那套 86 好」），
而周边基准的误差量级远大于 2 分。`quality_score` / `quality_score_raw`
**照常入库**（δ 标定要用、日后回填要用、档位由它算出），
只是**不出现在前端的精确数字位置上**。

> 为什么不干脆把周边也砍掉、直接判 `insufficient`？因为「疑似划算」
> 这个信息量对业务仍然有用——它能指出「这个小区值得派人去看一眼」。
> 只要它不冒充精确排名，保留就是净收益。判断标准是：
> **它是不是可能被误当成结论使用。** 独立分组 + 档位标签 + 明示「估算」，
> 三者同时具备时不会。


### 2.5 δ 的标定（口径必须与评分一致）

```
δ = median over 小区 c ( median over t∈c ( u_t / benchmark_t ) )
```

其中 `t` 取 **`peer_scope = community` 且 `benchmark_mode ∈ {S_ONLY, S2_BLEND,
S2_ONLY_WEAK, A_ONLY, B_ONLY}`** 的清水房，`benchmark_t` 由 **2.3 的同一个
Resolver** 产出。排除 `S1_BLEND / C_ONLY / D_ONLY` 与全部回退来源：
标定 δ 用的必须是最可信的那批基准，否则弱参考的噪声会直接进 δ，
再乘回到全市每一套房上。

两条都是硬要求：

1. **小区等权**。每个小区先内部聚合成一个比值，再全市取中位数。若按房源等权，
   拥有 20 套清水房的小区会主导 δ，密度就从后门溜回来了。
2. **同口径**。如果单套房的 `benchmark_t` 用 S 级（同房型 ±5㎡，很紧），而 δ 却用
   小区级中位数（很松）估出来，两者**不在同一标尺上**，`expected_u` 会带一个
   固定偏移。这个偏移**看不出来**——所有分数一起偏，没有任何单点异常，
   人工抽查也发现不了。这是本方案最需要在代码评审时盯住的一行。

**语义后果，必须写清楚**：δ 这样估出来，等于把「中位清水房」定在 advantage ≈ 0、
即 `quality_score ≈ 50`。这正是要的业务含义——「这套比一般清水房**该有的**价还便宜」。
但它同时意味着 **`quality_score` 衡量的是相对于清水房定价惯例的便宜度，
不是绝对市场时点**：全市清水房整体相对装修房降 10%，δ 跟着降，大家分数仍在 50 附近。
不得把它当市场指数使用。跨期可比性靠的是**冻结 δ 版本**，不是靠 δ 自动跟随。

护栏：δ 必须落在 `[0.55, 0.95]`，否则判定标定失败，回退到配置值
`SM_ROUGHCAST_DELTA_FALLBACK` 并告警，**不得静默使用异常 δ**。

### 2.6 三个分离的指标

```
u                 = 月租 / 面积                元/㎡·月
reference_u       = benchmark_t                2.3 Resolver 产出的装修房参考单价
expected_u        = reference_u × δ            这套房「本该」的单价
advantage         = ln(expected_u / u)         >0 表示比本该的价还便宜
quality_score_raw = clamp( 50 + 45·tanh(advantage / 0.50), 5, 95 )   REAL，排名用
quality_score     = round(quality_score_raw)                          整数，展示用
```

`reference_u / δ / expected_u` **三者全部入库**（见 4.1），解释链必须闭合：
业务问「45 元/㎡ 是哪来的」时能答得出。V2 只存了 `expected_unit_rent`，
参考价这一环断在库外。

映射是**固定的、绝对的、单调的**，不依赖当天全市分布：

| 实际 vs 合理价 | advantage | `quality_score` | 旧 `k=0.18` |
| --- | --- | --- | --- |
| 贵 30% | −0.262 | 28 | 10 |
| 贵 20% | −0.182 | 34 | 15 |
| 贵 15% | −0.140 | 38 | 21 |
| 贵 10% | −0.095 | 42 | 28 |
| 贵 5% | −0.049 | 46 | 38 |
| 持平 | 0 | 50 | 50 |
| 便宜 5% | 0.051 | 55 | 62 |
| 便宜 10% | 0.105 | 59 | 74 |
| 便宜 15% | 0.163 | 64 | 82 |
| 便宜 20% | 0.223 | 69 | 88 |
| 便宜 30% | 0.357 | 78 | 93 |
| 便宜 40% | 0.511 | 85 | 95 |
| 便宜 50% | 0.693 | 90 | 95 |

表中数值是公式的精确取整值，测试 11 直接拿它逐行对。`clamp` 到 `[5,95]` 是安全网——
`50+45·tanh(·)` 本身已落在开区间内，clamp 只在 advantage 为 `inf`/`nan` 时起作用。

「旧 `k=0.18`」一列保留，是为了让这次调参的理由留在正文里：旧刻度下
**便宜 40% 与便宜 50% 都是 95 分**（撞 clamp，无法定序），而**便宜 5% 就已经 62 分**——
按实测，相对本小区同房型强基准便宜 5% 是常态而非优质。新刻度把「便宜 10% = 59 分」
读作「略好于中位」，这与实测分布一致；直觉上觉得偏低，是因为直觉里的对照
是全市平均，而本方案的对照是**本小区同房型**，后者严格得多。

`0.50` 是尺度参数，与 δ 一样**必须版本化**（`model_version`）。当前值是
**provisional**：方向（`0.18` 过紧）已由 66 个目标证实，但取值只建立在
18 个强基准样本上。第 1 期首轮全量跑完后必须用正式样本重估并升版本；
调完就是新版本，历史分数不重算、不混用。推导过程见文首「δ 的首个实测值」一节。

**`confidence_score`（0–100）** —— 四维加权得 `raw_confidence`，
最终值**取三者最小**：

| 维度 | 权重 | 定义 |
| --- | --- | --- |
| 参考等级 | 40% | 2.2 表里的 grade 分（`nearby` 取 15） |
| 有效样本量 | 25% | `100 × (1 − exp(−n_eff / 3))` → 1→28, 3→63, 5→81, 8→93 |
| 数据新鲜度 | 20% | 当前态 100；历史 `d` 天 → `max(0, 100 − d)` |
| 房型面积匹配度 | 15% | `100 × mean(sim_layout × sim_area)`，池内平均 |

```
raw_confidence   = 0.40·grade + 0.25·sample + 0.20·freshness + 0.15·similarity
confidence_score = round( min( raw_confidence, source_cap, structure_cap ) )
```

- `source_cap` 来自 2.4：`community` 100 / 历史 80·70·60 / `nearby` 45
- `structure_cap` 来自 2.3：`S2_ONLY_WEAK` 70 / `S1_BLEND` 60 / 其余 100

**全代码只允许这一个截断点。** V2 把上限分散写在 2.3（`S=1 → ≤60`）和
2.4（`nearby → ≤45`）两处，实现时极易变成「不同代码路径各自截一刀」，
于是同一情形因调用顺序不同得到不同 confidence。V2.1 要求两个 cap 作为
Resolver 与回退链的**返回值**传出来，在唯一一处取 min。

推论（测试 5 查的就是这个）：20 套 D 级参考即使 `n_eff` 很大，
`grade` 维只有 25 分、`similarity` 维也低，`raw_confidence` 上不去；
而 3 套 S 级 `grade` 满分。**数量买不到可信度。**

**`city_rank`** —— 每日动态，**只在 `quality_status = 'scored'` 的房源里**排：

```sql
WHERE quality_status = 'scored'          -- 即 peer_scope ∈ {community, community_history}
ORDER BY quality_score_raw DESC, confidence_score DESC, listing_id ASC
```

三级断链的理由：① 先看真实划算程度，用**未取整**的 `quality_score_raw`——
`82.49` 与 `82.01` 都显示 82，用整数排名会把两者视为同分并交给下一级断链，
真实差异被抹掉；② 真正同分时让参考更可靠的房源靠前；
③ `listing_id` 保证两次运行完全一致、结果可复现。

**`peer_scope = nearby` 的房源不在这个集合里**（`quality_status =
'nearby_estimate'`，`city_rank = NULL`），理由见 2.4.2 ②：主键是
`quality_score_raw`，而周边基准的误差是偏差不是方差，放进来就会用一个
系统性偏高的分数压掉本小区的实测结果，`confidence_score` 作为第二键拦不住。
周边分组内部可以自行按 `quality_score_raw` 排序，但那是另一张榜。

`confidence_score` **只作 tie-break，不进入 `quality_score` 公式**。
这不违反「三者不得合成一个总分」——Quality 的值一位都没被 confidence 改动，
只是在 Quality 完全相同时用它决定先后。

三者**不得合成一个总分**。「92 分但只有 1 套参考」和「85 分有 9 套参考」
是两种不同的东西，业务需要分别筛选，合成会把信息压掉。

### 2.7 异常与缺数据

不做百分位裁剪。平台前提是真实房源、真实价格，「1/99 分位外不给分」
会静默删掉最有价值的结果。改为两类：

| 类别 | 判据 | 处理 |
| --- | --- | --- |
| `data_error` | 面积 ≤5㎡、租金 ≤0、缺租金或缺面积、`u` 落在 `[1, 500]` 元/㎡·月 之外 | 不给分，排序沉底，`quality_status='data_error'` |
| `insufficient` | 回退链走到底仍无参考（含周边未过 2.4.2 三道门槛） | `quality_score = NULL`，**不进排名**，单独分组展示 |
| `extreme_price` | `\|advantage\| > 0.7`（便宜/贵 50% 以上） | **照常给分、照常排名**，同时 `extreme_price=1` 标记待人工核 |

`quality_status` 的完整取值：

| 值 | 含义 | `city_rank` | 前端位置 |
| --- | --- | --- | --- |
| `scored` | 本小区当前或历史参考，基准可信 | 有名次 | 主榜 |
| `nearby_estimate` | 仅有周边小区基准（2.4.2） | `NULL` | 「周边估算」分组，显示档位不显示整数 |
| `insufficient` | 无任何可用基准 | `NULL` | 「缺参考」分组 |
| `data_error` | 房源数据本身有问题 | `NULL` | 「数据异常」分组 |

四者互斥。`extreme_price` 是**独立的布尔标记**，不是 `quality_status` 的取值——
一套房可以同时是 `scored` 且 `extreme_price=1`。

`insufficient` 选择「不给分」而不是「用全市基准硬给一个分」：业务要的是
「优质清水房排名」，不是「3289 套必须都有名次」。给一个没有同小区依据的分数，
比不给分更有害——它会被当成可信结论使用。这些房源在前端进「缺参考」分组，
可查看但不排名。

### 2.8 护栏

- **合租必须与整租隔离**。合租行的 `price` 是单间租金、`area` 常是整套面积，
  混进来会直接霸榜。`rent_mode_label` 为此必须进快照（连接器已返回，当前被丢弃）。
  分级、δ 标定、回退链全部在同一 `rent_mode` 内进行。
- **已知偏差（V1 未解决，V2 明确记录为待改进）**：B/C 级参考跨面积区间比较单价时，
  大面积单价天然偏低 → benchmark 偏低 → advantage 偏低 → **大户型系统性吃亏**。
  `sim_area` 加权只能缓解，不能消除。V2 接受这个偏差，条件是：
  ① S/A 级不受影响（±5/±10㎡ 内可忽略）；② `reason` 里带出参考等级，
  业务能看出这是弱参考；③ 监控 `strong_comparable_coverage`（S 或 A 级覆盖率，
  定义见第六章第 2 期），它高则受影响的面本就小。
  真要修，需要一个面积-单价弹性模型，留到 V3 且必须先有数据支撑。
- 室厅卫**不要**从 `layout` 字符串反解。连接器 `_parse_listing` 拿到的原始行里
  本来就有 `bedroomAmount / hallAmount / bathroomAmount` 三个整数，却拼成了
  `"3室2厅1卫"` 再丢掉原值；上游缺字段时该段直接省略，于是 `"3室2厅"` 无法区分
  「0 卫」和「卫数未知」——而 S 级要求室厅卫**全等**，这个区分是必须的。
  见第六章「建议的 connector 改动」A 项。
- 参考集里若混入未识别的清水房，benchmark 会被拉低、advantage 被拉低——
  方向是**保守的**（少报优质，不多报），可接受。
  但这只是**兜底论证，不是许可**：行级 `fitmentStatus` 已确认可读（第七章第 1 条），
  能识别的清水房必须识别出来剔除；真正剔不掉的只有装修状态为空的行，
  而那类行按 2.1 的规则**根本不进 R**。

### 2.9 输出字段

```
unit_rent / reference_unit_rent / expected_unit_rent / advantage
quality_score_raw / quality_score / quality_status / quality_tier
confidence_score / city_rank
peer_scope / comparable_grade / benchmark_mode
effective_sample_count / reference_age_days
reference_community_count / reference_spread
extreme_price / reason
```

`quality_tier` 只在 `quality_status = 'nearby_estimate'` 时有值
（`疑似划算 / 一般 / 疑似偏贵`，阈值见 2.4.2 ③）；`scored` 的房源直接显示
`quality_score` 精确整数，`quality_tier` 为 NULL。

`reference_community_count` / `reference_spread` 只在 `peer_scope = nearby` 时有值：
分别是参与中位数的周边小区数 `k` 和离散度 `spread`（2.4.2 门槛 3）。
`spread` 必须落库——它是「这次周边估算有多不靠谱」的唯一量化记录，
事后复盘周边分组的准确率时只能靠它。

`benchmark_mode` 取值：
`S_ONLY / S2_BLEND / S2_ONLY_WEAK / S1_BLEND / A_ONLY / B_ONLY / C_ONLY / D_ONLY / NEARBY`

> V2.1 提案原文的取值表里还有 `HISTORY`。这里**不采用**：历史回退的
> **池结构**与当前态完全相同（仍是 `S_ONLY` / `S2_BLEND` / …），
> 数据来源已由 `peer_scope = community_history` 和 `reference_age_days`
> 表达。把来源塞进 `benchmark_mode` 会让同一件事有两种记法，
> 且丢失「历史数据里池结构是什么」这个真正有用的信息。
> `NEARBY` 保留——它的池元素确实不同：是**小区基准值**而不是房源。

`reason` 必须带出**等级、结构、样本数和完整算式**，让人一眼看出这个分能不能信。
「完整算式」指四个数缺一不可：`reference_unit_rent`、`δ`、`expected_unit_rent`、
`unit_rent`。下面例 2–5 只展示各分支特有的结构说明部分，算式段落与例 1 同构，
**实际入库的 `reason` 每条都必须带**。

> 本小区同户型（3室2厅1卫 88–92㎡）在租 7 套，参考单价 45.1 元/㎡；
> δ 0.72 → 合理清水 32.5 元/㎡，此房 26.0 元/㎡ ·
> S 级参考 · `S_ONLY` · 有效样本 6.2

> 本小区 S 级参考仅 2 套，无 A/B 次级样本，直接取两套加权中位数 ·
> `S2_ONLY_WEAK` · 该结构 Confidence 上限 70

> 本小区当前仅 1 套 S 级，结合 3 套 A/B 级按 40/60 融合 ·
> `S1_BLEND` · Confidence 上限 60

> 本小区当前仅 1 套高度相似参考且无其他有效样本，当前基准不足，
> 转用本小区 30 天内历史（S 级 4 套）· Confidence 上限 80

> 本小区无在租参考，取自周边 4 个小区的基准中位数 · `NEARBY` ·
> Confidence 上限 45

「本小区同户型 8 套里最低」和「本小区仅此 1 套」的可信度天差地别，
不能显示成同一个分数而不加说明。`benchmark_mode` 同时入库的意义在于：
排查「为什么这套房是这个基准」时不需要去解析 `reason` 字符串。

### 2.10 API 响应示例

```json
{
  "items": [
    {
      "listing_id": "1069xxxxxxxx",
      "community": "阳光丽景",
      "layout": "3室2厅1卫",
      "area_sqm": 89.5,
      "monthly_rent_yuan": 2330,
      "orientation": "南",
      "floor": "中楼层/18层",
      "image": "https://img.ljcdn.com/....750x.jpg",
      "unit_rent": 26.03,
      "reference_unit_rent": 45.1,
      "expected_unit_rent": 32.47,
      "advantage": 0.221,
      "quality_score_raw": 87.8873,
      "quality_score": 88,
      "quality_status": "scored",
      "confidence_score": 78,
      "city_rank": 14,
      "peer_scope": "community",
      "comparable_grade": "S",
      "benchmark_mode": "S_ONLY",
      "effective_sample_count": 6.2,
      "reference_age_days": 0,
      "extreme_price": false,
      "reason": "本小区同户型在租 7 套，参考单价 45.1 元/㎡；δ 0.72 → 合理清水 32.5 元/㎡，此房 26.0 元/㎡ · S 级参考 · S_ONLY · 有效样本 6.2"
    }
  ],
  "total": 2864,
  "sort_applied": "quality",
  "model_version": "v2.1",
  "delta_version": 7,
  "delta_value": 0.72,
  "scored_at": "2026-08-20T20:14:03+00:00",
  "snapshot_run_id": 431
}
```

## 三、采集节奏（约束 4、6）

**定位**：这是**内部授权系统的上游负载控制**，不是规避检测。目标是让自动任务的
访问量长期低于一个正常业务用户，不给上游制造集中压力。契合项目已有文化——
`crm_connector/app/application/session_watchdog.py` 明确写了
「空闲服务产生零上游流量」。

**长期方向**：页面接口自动化只是**兼容手段**。正解是内部只读 API / 专用查询端点 /
授权批量导出 / 服务账号。方案的采集层因此必须与数据来源解耦（见 4.3），
将来换成正式接口时只替换 fetcher，评分与库表不动。

### 队列 A · 清水房全量（每天一轮）

- `page_size=50`，页数**每轮由第 1 页返回的 `total` 现算**
  （`ceil(total / page_size)`；2026-08-20 实测 3289 → 66 页）。
  **66 不得写成常数**——`total` 会随市场变动，见第七章第 7 条
- 串行单并发；页间随机 25–90 秒
- 每 8–15 页插入 3–8 分钟停顿
- 仅在 09:30–19:00 窗口内运行；每日启动时间随机 ±40 分钟
- 全程约 1.6 小时，稳稳落在窗口内：65 个页间间隔 × 均 57.5 秒 ≈ 62 分钟，
  加约 6 次长停顿 × 均 5.5 分钟 ≈ 31 分钟，合计约 93 分钟
- **抓取阶段不做楼层详情回补**。`roughcast_rental_fetcher._fill_missing_floors`
  会对每行 `del_type == 2` 的房源打一次详情接口，全量场景下放大成上千次额外请求。
  它只允许用在「用户正在看的那一页」

### 队列 B · 小区参考（半个月轮转一遍）

- 小区总数估 650–1100（3289 套 ÷ 平均 3–5 套）
- 每日配额 `ceil(小区总数 / 15)` ≈ 40–60 个小区
- 每个小区**通常 1 次**搜索请求：按 `resblock_ids` 精确搜、**不带 fitment 过滤**，
  返回后按行级 `fitmentStatus` 就地分类（已实测可行，见第七章第 1、2 条）。
  不需要 suggest 解析步骤。
- 但实际请求数仍由**分页数**和**是否重试**决定，**全部计入预算实际计数**。
  不要把「1 个小区 = 1 个请求」写成硬事实——大盘小区超过单页上限就是 2 页起
  （实测某小区 10 套，单页够用；50 套以上的大盘是少数但存在，见第七章第 4 条）
- 优先级：

| 级 | 对象 | 理由 |
| --- | --- | --- |
| P0 | 新出现、**从未有过基准**的小区 | 否则纯按 `refreshed_at` 轮转会让新小区永远排在队尾，永远拿不到分 |
| P1 | `refresh_fail_count > 0` 的小区 | 上次失败的优先补 |
| P2 | 清水房套数多的小区 | 影响面大 |
| P3 | `refreshed_at` 最旧 | 保证 15 天全覆盖 |

P0 插队必须有**每日上限**（默认占配额的 30%），否则某天大量新小区涌入会把
轮转队列整体饿死。

### 共享节流器

- **每日预算按计划推导**：

  ```
  planned_requests =
        queue_A_expected_page_requests            # 全量清水房页数 = ceil(total / page_size)
      + Σ queue_B_expected_page_requests          # 逐小区求和,大盘小区可能多页
      + retry_reserve                             # 允许的重试次数

  daily_budget = min( ceil(planned_requests × safety_factor),
                      SM_ROUGHCAST_DAILY_REQUEST_CAP )
  ```

  `safety_factor = 1.10 ~ 1.15`；硬顶 `SM_ROUGHCAST_DAILY_REQUEST_CAP` 默认 260。

  **按 2026-08-20 实测总数代入（说明硬顶余量有多薄）**：

  | 项 | 请求数 | 依据 |
  | --- | --- | --- |
  | 队列 A 页数 | 66 | `ceil(3289/50)`，实测 |
  | 队列 B（60 小区，均 **1.5** 页） | ~90 | **第 2 期探针实测**：30 小区共需 45 页 = 1.5 页/小区；11/30 超 50 条，最大 162 条（4 页） |
  | suggest | **0** | 第七章第 2 条已验证 `resblockId` 直接可读且塞回去能搜 |
  | 重试预留 | 10 | |
  | 小计 × 1.15 | **191** | 距硬顶 260 余量 **69** 次 |

  > 1.5 页/小区是**偏高估计**：探针的 30 个小区取自清水房搜索首页，
  > 房源多的小区更容易出现在首页，所以真实均值应 ≤1.5。
  > 预算往高处算是安全方向，不修正。V2.2 曾按「均 1.3 页」估出 ~78 页 / 小计 178，
  > 那个假设的依据只是「实测某小区 10 套 → 单页够用」——**一个小区的样本推不出全市分布**。

  **suggest 归零是抓包换来的实际收益**：原先估 0–60 次，最坏情况让上界冲到 246、
  只剩 14 次余量；现在稳定在 191。两条实现要求仍然成立：

  1. `resblock_id` 必须**固化进 `roughcast_communities`**。虽然不再需要 suggest 解析，
     但小区身份得有本地主键，否则每轮都要靠 `resblockName` 字符串对齐，
     改名或同名小区就会串。
  2. 队列 B 的每日配额**不能是常数 60**，必须由「硬顶 − 队列 A 实际页数 − 重试预留」
     反算出来。队列 A 页数会随市场涨——总数涨到 5000 就是 100 页——
     此时应该缩的是队列 B 的当日配额（延长轮转周期），
     而不是让队列 A 半途撞预算产出 ABORTED run。

- **扣减规则（硬规则）**：**每产生一次真实上游请求，预算扣 1。**
  不是「完成一个小区任务扣 1」。一个小区若发生 2 页搜索 + 1 次重试，就扣 **3**。
  预算归零 → 立即停止发起任何新的上游请求，剩余任务顺延到明天。

  扣减必须发生在**发起请求的那一处**，不是任务完成处。这样将来新增任何请求类型
  都自动计入，不必回来改预算代码。

  **已花额度必须以 `roughcast_crawl_log` 为准现算**（当天 Asia/Shanghai 零点起的
  行数），内存计数器只作缓存。否则进程重启就把当天已花的额度忘光，硬顶形同虚设。

  **一处已知记账缺口（V2.4 记录）**：`crm_connector` 的
  `kecom_session_provider._maybe_autorefresh` 在 401 时会自行重发一次请求。
  那是一次真实上游请求，但发生在 connector 进程内，store_media 侧的节流器
  **看不见、扣不到**。当前由 `retry_reserve` 吸收（它每 run 上限个位数，
  且 401 会同时触发熔断、当天不再有后续请求，所以泄漏量有界）。
  是否把计数下移到 connector 的 `authorized_fetch`，登记进第七章待确认。

  V2 的 `(queue_A_pages + queue_B_daily_quota) × 1.15` 把「小区数」直接当成
  「请求数」，遇到大盘小区多页就会**静默超支**——预算显示没超，
  实际请求数已经翻倍。这正是预算机制要防的事。
- **全局**最小请求间隔 20 秒，跨队列生效，避免两条队列撞在同一分钟
- 复用已有的浏览器签名 UA 镜像（见提交 `3d0768f`）
- **熔断**：遇 429 / 验证码 / session 失效 / 连续 3 次失败 →
  当天剩余任务全部取消并告警，run 标记 `ABORTED`，**绝不重试打穿**。
  这条比任何 jitter 都重要

  落到实现，四条触发信号对应 connector 的这些出口：

  | 信号 | 观测到的形式 |
  | --- | --- |
  | 429 | HTTP 429 |
  | session 失效 | HTTP 401 / `code=CRM_AUTH_REQUIRED`（`AuthenticationRequiredError`） |
  | 验证码 / 登录页 | HTTP 502 / `code=CRM_UPSTREAM_CHANGED`——`kecom_session_provider` 对非 JSON 的 200 body 抛的正是这个 |
  | 连续 3 次失败 | 任意类型累计 |

- **窗口关闭也判 ABORTED**（`abort_reason = 'window_closed'`）：跑到 19:00 仍未翻完
  全部页，则停止发起新请求、本轮不发布，剩余顺延到明天。
  不越窗，也不发布半截数据（原则 3）。正常 93 分钟的一轮不会撞上这条，
  它撞上说明当天有异常，而异常时正确的动作是不发布。
- 所有请求时间戳入 `roughcast_crawl_log`，可事后审计当天的实际流量曲线

每日总请求约 110–160 次，分散在 10 小时窗口内，平均 1 次 / 4–5 分钟。

### 可用性

评分永远读**最后一份 COMPLETE 的 run**。采集失败只影响新鲜度，不影响可用性，
页面永不空窗。

## 四、本地库（约束 5）

现有 SQLite 已是 `CREATE TABLE IF NOT EXISTS` 幂等初始化
（`app/infrastructure/database.py:31` 的 `initialize()` 里一段 `executescript`），
新表追加进同一段即可，无需迁移框架。

### 4.1 表

| 表 | 作用 | 关键字段 |
| --- | --- | --- |
| `roughcast_communities` | 小区档案与轮转状态 | `id / name / resblock_id / bizcircle / district / latitude / longitude / roughcast_count / reference_run_id / refreshed_at / next_refresh_at / refresh_fail_count / first_seen_at` |
| `roughcast_community_reference_snapshot` | 小区参考房源快照，**按批次 append**（评分只读这里，不再筛小区） | `id / community_id / run_id / listing_id / rent_mode / rooms / halls / baths / area_sqm / monthly_rent_yuan / unit_rent / orientation / is_roughcast / captured_at` |
| `roughcast_listing_current` | 清水房**当前态**（唯一真相，供页面与评分） | `listing_id PK / community_id / community_name / resblock_id / bizcircle / layout / rooms / halls / baths / area_sqm / monthly_rent_yuan / orientation / floor_desc / total_floors / rent_mode / del_type / fitment_status / fitment_status_desc / create_time / title_image_url / content_hash / first_seen_at / last_seen_at / last_seen_run_id / is_active` |
| `roughcast_listing_snapshot` | 清水房**历史**，**只写变更点**（供历史参考与降价分析） | `id / listing_id / captured_at / captured_run_id / last_confirmed_at / last_confirmed_run_id / content_hash` + 与 current 同构的业务字段 |
| `roughcast_community_month_benchmark` | 小区月度基准，**永久保存** | `community_id / year_month / rent_mode / rooms / benchmark_unit_rent / sample_count / benchmark_mode / computed_at` |
| `roughcast_listing_scores` | 评分结果，按 run 版本化 | 见下方字段清单 |
| `roughcast_crawl_runs` | 一轮采集的事务边界 | `id / queue / status / started_at / finished_at / pages_expected / pages_done / items_seen / request_count / upstream_total / unknown_fitment_count / non_roughcast_count / abort_reason` |
| `roughcast_crawl_log` | 节流审计与熔断依据 | `id / run_id / queue / target / requested_at / status / http_status / note` |
| `roughcast_crawl_stage` | 采集期落地区，**RUNNING 期间唯一的数据出口**（见 4.2） | `id / run_id / listing_id / content_hash / fitment_status / payload_json / seen_at` |

`roughcast_listing_current` / `..._snapshot` 的 `resblock_id / fitment_status /
fitment_status_desc / create_time` 是第 1 期队列 A 入库必写的字段（见第六章），
V2.3 的字段清单漏列，V2.4 补上。

`bizcircle` **建列但第 1 期不填**：原始行里确实有 `bizCircleName`
（`crm_connector/run/probe_rental_row.out.json` 已确认），但 connector 的
`RentalListing` 尚未映射它，属于**待办的 connector 改动 E**。
不急着做的理由是队列 A 每天重扫全量，改动 E 落地的次日该列就自动填满，
不需要为补历史值另买一轮请求。它是 2.4.2 门槛 2（同商圈）的输入，
最迟在第 4 期评分器上线前落地即可。

`district / latitude / longitude` 同样建列不填，且**不会**有对应的 connector 改动——
原始行里没有这三个字段（§七.5 已定案放弃坐标接口）。

`create_time` 存**绝对时间戳**，`listed_days` 不建列——理由见 4.4 规则 3。

`roughcast_crawl_runs.upstream_total` 记录本轮第 1 页返回的 `total`。
第七章第 7 条要求连续 3 天观察 `total` 的波动幅度，这是唯一的落地位置；
`pages_expected` 由它现算，**66 不得写成常数**。
`unknown_fitment_count` / `non_roughcast_count` 落实第七章第 9 条的
「空装修行必须计数并落库，不得静默丢弃」。

`roughcast_communities.id` 在 `resblock_id` 解析出来之前，用小区名规范化后的哈希占位，
解析后固化并保留原 id 映射。

`roughcast_communities.reference_run_id` 指向**最近一次成功刷新该小区参考集的
COMPLETE run**。这个指针是必须的：参考快照是按批次 append 的，
同一个小区在库里会有多个 `run_id` 的批次，评分必须知道读哪一批：

```sql
-- 本小区「当前」参考集 = 指针指向的那一批，不是「全部行」也不是「max(run_id)」
SELECT * FROM roughcast_community_reference_snapshot s
  JOIN roughcast_communities c ON c.id = s.community_id
 WHERE s.community_id = ? AND s.run_id = c.reference_run_id;
```

用 `max(run_id)` 是错的——那会读到一个 ABORTED run 写进去的半截批次。
指针只在 run 判定 COMPLETE 时、和刷新 `listing_current` 同一个事务里前移
（4.2 的规则对参考表同样成立）。

`roughcast_community_reference_snapshot` 里 `is_roughcast` 标记该参考房源是否为清水房，
用于把它从参考集 R 中剔除。它**直接来自行级 `fitmentStatus == '002'`**
（2026-08-20 抓包确认），不是靠「已知清水房 id 集合相减」推出来的。

因此该表必须**原样存下 `fitment_status`**，而不是只存 `is_roughcast` 布尔值：
布尔值会把「简装」「精装」「装修未知」三种情况压成同一个 `false`，
而它们在 2.1 里的归属完全不同（前两者进 R，第三者两边都不进）。
存原值同时也让「精装/简装分别统计」成为一条 SQL 而不是一次重新采集。

`roughcast_listing_scores` 完整字段：

```
id
listing_id
score_run_id                 # 本次评分批次
listing_run_id               # 房源数据来自哪轮采集（COMPLETE run）
reference_run_id             # 本小区参考数据来自哪轮（NEARBY/多小区时为主 run）
model_version
delta_version
delta_value

unit_rent            REAL    # 本房源实际单价
reference_unit_rent  REAL    # 装修房基准单价（Resolver 输出的 benchmark，δ 之前）
expected_unit_rent   REAL    # = reference_unit_rent × δ
advantage            REAL    # = ln(expected_unit_rent / unit_rent)

quality_score_raw    REAL    # 未取整，排序用
quality_score        INTEGER # 取整，展示用
quality_status       TEXT    # scored / nearby_estimate / insufficient / data_error
quality_tier         TEXT    # 仅 nearby_estimate：疑似划算 / 一般 / 疑似偏贵
confidence_score     INTEGER
city_rank            INTEGER # 仅 quality_status='scored' 有值，其余 NULL

peer_scope           TEXT    # community / community_history / nearby
comparable_grade     TEXT    # S / A / B / C / D / N
benchmark_mode       TEXT    # S_ONLY / S2_BLEND / ... / NEARBY
effective_sample_count REAL  # n_eff
reference_age_days   INTEGER
reference_community_count INTEGER  # 仅 nearby：参与中位数的小区数 k
reference_spread     REAL           # 仅 nearby：(max−min)/median，门槛 3 的实测值
extreme_price        INTEGER
reason               TEXT

benchmark_pool_json  TEXT    # 实际采用的参考池，见 4.3
computed_at
```

`reference_unit_rent` **必须入库**，不能只留 `expected_unit_rent`。缺了它，
解释链就断在「装修房到底多少钱」这一环：用户看到「合理清水价 32.5」却无法知道
它是「45.1 × 0.72」还是「40.6 × 0.80」——而这两者对「δ 标错了还是基准取错了」
是完全不同的结论。它同时是 δ 标定与线上排查的唯一交叉验证入口。

`benchmark_mode` 同理必须是**独立列**而不是 `reason` 里的一个词。排查
「为什么这套房是这个基准」、统计「有多少房源落在 `S2_ONLY_WEAK` 上」，
都不应该依赖字符串解析。

`quality_score_raw` 与 `quality_score` **两列都存**。前者定序，后者展示；
只存整数则 82.49 与 82.01 无法定序（见 2.6 的排序规则）。

### 4.2 run 状态机与「只有 COMPLETE 才发布」

```
RUNNING ──成功走完全部页──▶ COMPLETE
   ├──熔断触发─────────────▶ ABORTED
   └──异常/进程退出────────▶ FAILED
```

硬规则，**这是 V1 的数据损坏 bug 的修法**：

1. 采集过程**只写 `roughcast_crawl_stage` 与 `..._crawl_log`**（V2.4 修订，见下）。
2. run 变为 `COMPLETE` 时，**在一个事务里**由本 run 的 stage 行做变更点写入
   `roughcast_listing_snapshot`（4.5）、刷新 `roughcast_listing_current`，
   并把「本 run 未出现的房源」标记 `is_active=0`；
   队列 B 的 run 则在同一事务里把涉及小区的
   `roughcast_communities.reference_run_id` 前移到本 run。
3. `ABORTED` / `FAILED` 的 run **绝不参与** `is_active` 计算，也绝不刷新 current，
   **也绝不前移 `reference_run_id`**，且**绝不写入 snapshot**。
   它的 stage 数据保留（可用于排查），随 4.5 的保留策略清理。
4. 评分器启动时读「最新的 COMPLETE run」；没有就沿用上一次评分结果，不产出新版本。

**V2.4 为什么引入 `roughcast_crawl_stage`**：V2.3 的规则 1 让采集过程直接写
`listing_snapshot`。但 4.5 的变更点写入在「无变化」时要**就地 UPDATE**
`last_confirmed_at / last_confirmed_run_id`——于是一个最终 ABORTED 的 run
仍然改动了 snapshot 的既有行。「ABORTED 绝不发布」就从一条结构性事实退化成
一条**读时纪律**：每个读路径都必须 join `crawl_runs` 过滤 `status='COMPLETE'`，
漏一处就重演本节要修的那类 bug——而下面那段推论刚好点明，这是
「本方案里最难通过抽查发现的一类错误」。

改为「采集期只写 stage，变更点写入与 current 刷新一起放进 COMPLETE 事务」后，
ABORTED / FAILED 的 run 对 `snapshot` 与 `current` **一个字节都没碰过**，
不完整的数据在物理上无法被读到，不依赖任何读时纪律。
代价是多一张表和一条保留策略（stage 保留 30 天）。

推论：**`reference_run_id` 永远指向一个完整批次**，所以 4.1 里那句「不要用
`max(run_id)`」不是风格建议——`max(run_id)` 会读到 ABORTED run 写进去的半截批次，
Resolver 拿着一个缺了一半房源的参考集照样能算出 benchmark，而且算得出的那个数
看起来完全正常。这是本方案里最难通过抽查发现的一类错误。

`pages_expected` 与 `pages_done` 必须都记：只有两者一致（或已确认 `has_more=false`）
才允许判 COMPLETE。

### 4.3 可复现性

V2 声称「`listing_run_id / reference_run_id / model_version / delta_version /
delta_value` 五个值即可完全复现」。**这个说法只在 `peer_scope = community` 时成立。**

`community_history` 会跨多个 run 取快照；`nearby` 会跨多个小区、多个
`reference_run_id`。单个 `reference_run_id` 根本描述不了实际用了哪些参考。
声称能复现而实际不能，比不声称更糟——排查时会朝错误的方向找。

**正确表述**：

> 给定 `score_id`，凭 `model_version`、`delta_version`、`delta_value`、
> 目标房源快照（`listing_run_id` + `listing_id`）以及 `benchmark_pool_json`
> 中记录的**实际参考池**，可完整重算该评分并逐位比对。

`benchmark_pool_json` 只记**实际参与本次基准计算**的元素，不记搜索到的全部候选：

- `S_ONLY` → 只记 S 池
- `S2_BLEND` / `S1_BLEND` → 记 S 池 **加上**真正进了 secondary 的 A/B 房源
- `NEARBY` → 记参与中位数的**周边小区及其基准值**（池元素是小区基准，不是房源）

每个房源元素记：

```
listing_id / community_id / source_run_id / grade
rent_mode / rooms / halls / baths / area_sqm / unit_rent
sim_layout / sim_area / w_fresh / w_p / reference_age_days
```

**为什么选 JSON 列而不是 `roughcast_score_comparables` 明细表**：几千房源 ×
每套 3–10 个参考 = 每轮上万行，而这份数据的唯一用途是**按单个 score 行整体回放**，
从不跨 score 做聚合查询。明细表要付出外键、随 run 版本化的清理、以及
「score 表与明细表状态不一致」的风险，换来一个用不上的查询能力。
JSON 列与 score 行同生共死，天然不会失配。

若日后确实出现「统计全城 sim_area 分布」这类跨行需求，再从 JSON 反向物化明细表——
那是纯增量工作，不需要改评分器。

### 4.4 四个时间字段，语义不得混用

这是最容易写错、且写错后**不报错只偏分**的一处。

| 字段 | 语义 | 谁用它 |
| --- | --- | --- |
| `first_seen_at` | **我们**第一次抓到这套房的时间 | 「挂了多久」的**下界**估计、新房源识别 |
| `last_seen_at` | 最近一次在 **COMPLETE** run 里出现 | 下架判定（`is_active`）；**不用于新鲜度** |
| `captured_at` | **这一条快照行**对应的抓取时刻 | 2.2 的 `w_fresh`、2.4 的 `reference_age_days`、历史分档 30/60/90 天 |
| `listed_days` | 上游给出的**真实**挂牌天数 | 捡漏信号（挂久 = 可谈） |

三条硬规则：

1. **新鲜度只看 `captured_at`。** 用 `first_seen_at` 算 `w_fresh` 会得到荒谬结果：
   一套上线时就存在、今天仍在架的房，`first_seen_at` 是半年前，
   但它的报价是**今天**的，`w_fresh` 应该是 1.00。
2. **`first_seen_at` 不是上架时间，只是下界。** 系统上线前已经挂了 200 天的房，
   `first_seen_at` 也是上线那天。这个下界性质**永远成立**，
   所以哪怕有了第 3 条的 `create_time`，`first_seen_at` 也只能回答
   「我们什么时候第一次看到它」，不能回答「它挂了多久」。
3. ~~**`listed_days` 目前拿不到**~~ **改为 `create_time`，2026-08-20 抓包后修正**。
   搜索行里确实**没有** `alreadyCreateDays`（整行无任何 `*days*` 键），
   但有 **`createTime`（epoch 毫秒）**，实测解出 2026-07-16 / 08-03 / 08-16。
   所以挂牌时长**不再只有下界可用**：

   ```
   listed_days = (今天 - create_time.astimezone(Asia/Shanghai).date()).days
   ```

   三点必须守住：
   - **存 `create_time` 绝对时间戳，不存天数。** 天数是查询时算出来的派生值，
     入库就会隔夜失效——一套昨天入库写着 30 天的房，今天仍然显示 30 天。
     这也是它比 `alreadyCreateDays` 更好的地方。
   - **取 `.date()` 前必须转 Asia/Shanghai。** connector 的 `_ts_to_datetime`
     统一返回 UTC（epoch 毫秒本身无时区，存 UTC 没问题），但直接对 UTC 取
     `.date()` 会把北京时间当晚 8 点后录入的房源算成前一天，「录入至今」整体偏大 1 天。
   - `create_time` 的确切语义仍未与业务方核对：它可能是**CRM 录入时间**而非
     对外挂牌时间，且**重新上架是否重置未知**。所以文案上应写「录入至今」，
     不要写「挂牌 X 天」，直到核对为止。这一条不阻塞评分（挂牌时长不是评分输入）。

### 4.5 写入方式与保留策略

两张历史表的**写入频率差两个数量级**，因此策略不同。不要强行统一。

#### 清水房日快照：只写变更点

队列 A 每天扫全部 3289 套。若每天每套都 insert 一行，一年 **120 万行**，
而其中绝大多数是「与昨天逐字段相同」的重复行——评分只读 90 天（2.4.1 的硬上限），
这些重复行对评分的贡献是零。

改为**变更点写入**：

```
content_hash = hash(monthly_rent_yuan, area_sqm, rooms, halls, baths,
                    orientation, floor_desc, total_floors, rent_mode, del_type)

对本 run 抓到的每套房：
  取该 listing_id 在 snapshot 里最新的一行 prev
  if prev is None or prev.content_hash != 本次 hash:
        INSERT 新行（captured_at / captured_run_id = 本轮）
  else:
        UPDATE prev SET last_confirmed_at = 本轮时间,
                        last_confirmed_run_id = 本轮 run     -- 不新增行
```

**哈希前必须先规范化取值**（V2.4）：数值统一 `round(x, 2)` 后按固定格式序列化，
`None` 有唯一表示。否则 `4300` / `4300.0` / `"4300"` 会算出三个不同的 hash，
每轮都被判成变更点，本节 36 倍的收益直接归零，而症状只是「快照表长得有点快」。

`fitment_status` **不进 hash**：队列 A 带 `fitment=002` 过滤，一套房装修状态变了
会直接从结果集里消失，由 `is_active=0` 表达，不需要 hash 覆盖这条变化。

`captured_at` = 这个状态**开始**的时间，`last_confirmed_at` = 最后一次确认它仍是
这个状态的时间。两者构成一个**闭区间**，等于免费得到了完整的价格变动史：
一套房降过两次价，库里就是 3 行，每行带自己的生效区间。

量级：假设房源平均在架 60 天、生命周期内变动 1–2 次，
一套房约产生 3 行；年周转约 1 万套 → **约 3 万行/年**，比逐日全量小 36 倍。

对 2.4 的历史查询完全等价——按 `DISTINCT listing_id` 取最近一条快照本来就是
去重逻辑（2.4.1），变更点写入只是把去重提前到写入时做掉。唯一要注意的是
**新鲜度算的是 `last_confirmed_at`，不是 `captured_at`**：一套房 60 天没改价
但今天还在架，它的报价仍然是今天有效的。

> 这里 `captured_at` 与 4.4 的规则有一层：4.4 说新鲜度看 `captured_at`，
> 在**逐日全量**模型下两者等价（每天都是新行）。变更点模型下
> 「该状态最后一次被确认有效」的时间是 `last_confirmed_at`，
> 它才是 4.4 规则里那个语义。实现时以本节为准。

#### 小区参考快照：批次全量写入，不做变更点

每个小区 15 天才刷一次，`700 小区 × 每轮约 20 条 × 24 轮/年 ≈ 34 万行/年`。
这个量级不值得为它引入变更点逻辑，而**批次完整性**是刚需——
`reference_run_id` 指针要指向一个完整批次，Resolver 要拿到「那一刻该小区的全貌」，
4.3 的可复现性也依赖批次能原样取回。变更点写入会把批次概念打散。

#### 保留策略：分层，不永久保存房源级原始行

| 数据 | 保留 | 依据 |
| --- | --- | --- |
| `roughcast_listing_current` | **永久** | 唯一真相表，只有约 3300 活跃行 + 已下架行 |
| `roughcast_listing_snapshot` | **永久**（变更点已经很小） | 3 万行/年，10 年 30 万行，SQLite 无压力；且它是降价分析的全部依据 |
| `roughcast_community_reference_snapshot` | **400 天** | 覆盖 90 天评分窗口 + 一年同期对比，再往前对评分零价值 |
| `roughcast_community_month_benchmark` | **永久** | 每小区每月每房型一行，`700 × 12 × 3 ≈ 2.5 万行/年`，这才是值得长期留的东西 |
| `roughcast_listing_scores` | **永久** | 可复现性与「昨天 92 今天 71」的追溯依据 |
| `roughcast_crawl_log` | **180 天** | 审计用途，过期后聚合成日统计留存 |
| `roughcast_crawl_stage` | **30 天** | 只在 COMPLETE 事务里被读一次；保留一个月供 ABORTED run 排查，之后无价值 |
| `roughcast_crawl_runs` | **永久** | 行数极少，且 scores 通过 run_id 引用它 |

**清理必须是独立的定时任务，绝不写在采集或评分流程里。** 采集流程里做 DELETE，
一旦清理逻辑有 bug 就会在每天最忙的路径上损坏数据，而且熔断中断时清理可能
只执行了一半。清理任务每周跑一次，先 `COUNT` 确认待删行数落在预期区间
（比如「不超过总行数的 5%」），超出就告警并放弃本次清理——
宁可多占磁盘，不可误删历史。

`roughcast_community_month_benchmark` 在参考快照被清理**之前**由聚合任务生成：
每月对每个小区、每个 `rent_mode`、每个室数，用 2.3 的同一个 Resolver 算出该月基准值
落库。口径必须与评分一致（2.5 的第 2 条），否则将来拿它做同期对比时
又是一个看不出来的系统性偏移。

## 五、服务与页面

评分是**离线批处理**：当天采集 COMPLETE 后跑一次，几千条毫秒级，
结果写 `roughcast_listing_scores`。API 只读库，请求路径**零上游依赖、零等待**——
上游抖动和熔断都碰不到用户。

- `/api/v1/display/roughcast-rentals`
  - `sort=quality|confidence|rent_asc|unit_rent_asc|latest`（默认 `quality`）
  - `min_confidence`（默认 0）
  - `peer_scope_in=community,community_history`（「只看有本小区参考」开关）
  - `benchmark_mode_in=S_ONLY,S2_BLEND`（可选，运营排查用；默认不过滤）
  - 响应带 `sort_applied / total / model_version / delta_version / delta_value / scored_at / snapshot_run_id`
  - **`sort=quality` 在库里按 `quality_score_raw` 排**（见 2.6），
    响应里两个字段都出：`quality_score` 给展示，`quality_score_raw` 给排序自证与排查
- 卡片：`xx 元/㎡` + `优质指数 88` + 可信度徽标 + `reason` 一行 + 参考来源标注。
  `reason` 里必须能看到装修参考单价、δ、合理清水价、实际单价与 `benchmark_mode`，
  否则「凭什么 88 分」在页面上无法自证
- 筛选项：
  - 只看有本小区参考
  - **高可信捡漏**：`quality_status = 'scored'` 且 `quality_score ≥ 75`
    且 `confidence_score ≥ 70`——这是业务真正想点的按钮。
    `nearby_estimate` **永不进入这个筛选结果**，无论它分多高
  - 待人工核（`extreme_price=1`）
- 分组呈现，四组物理分离，**不同组之间不做分数比较**：

| 分组 | 条件 | 展示 |
| --- | --- | --- |
| 主榜 | `quality_status='scored'` | `优质指数 88` + `city_rank` + 可信度徽标 |
| 周边估算 | `quality_status='nearby_estimate'` | `疑似划算（周边估算）` + 参考小区数 `k` + `spread`，**无精确分数、无名次** |
| 缺参考 | `quality_status='insufficient'` | 仅房源信息 |
| 数据异常 | `quality_status='data_error'` | 仅房源信息，沉底 |

  周边估算分组默认**折叠**，需用户主动展开。默认展开等于把估算值摆在
  与实测值同等的视觉地位上，用户不会去读那行小字标注。

注意：`tests/integration/test_roughcast_rentals_api.py` 对响应体是 **JSON 全等断言**
（约 82 行），新增字段必然使其失败，须同步更新。
`tests/unit/test_roughcast_rental_fetcher.py` 用 `set(primary) == {...}` 钉死了
8 字段展示契约，同样要改。

隐私纪律不变：`owner_phone` / `upload_user` 等内部字段永不出库出网；
`_fetch_prospect` 只保留 `image_type == "REAL"`；图片 URL 必须 http(s)。

## 六、分期

| 期 | 内容 | 出口条件 |
| --- | --- | --- |
| 1 | **「建议的 connector 改动」A/B/C/D（已完成）** + 库表 + 队列 A + 节流器/熔断 + `crawl_runs` 状态机。**只采集，不评分**，也不写 `classify`/Resolver（理由见下方「为什么覆盖率复算挪到第 3 期」） | 连续 3 天产出 COMPLETE run；日志显示实际流量符合第三章；连续 3 天记录上游 `total` 以观察其波动（第七章第 7 条） |
| 2 | ~~S 级覆盖率探针~~ **已完成（2026-08-20，31 次请求）** | 结论见文首；四条待落地结论见下 |
| 3 | 队列 B + 半月轮转 + `community_reference_snapshot`，**随后**离线复算三个覆盖率、定稿 2.2 分级阶梯（V2.3 第 6 条）并落地下面「第 2 期的四条结论」 | 15 天内小区覆盖率 ≥95%；三个覆盖率复算完成且阶梯定稿 |
| 4 | δ 标定 + Resolver + 评分器 + **Shadow Run** | 第八章 1–12 与 A–Y 全绿；算分但不上前端；人工核 50 套（含 10 套高分、10 套低分、10 套 `extreme_price`）；用正式样本重估 `k`（provisional 0.50）并升 `model_version` |
| 5 | 排名 API + 前端 + 上云推送 | 本地跑通后再发布云端 |

### 第 2 期的四条结论（第 3 期实现 `classify`/Resolver 时直接带上）

第 6 条「六级压成三级」**不执行**，但探针查出的三处标定错误和一个 bug 必须带上——
它们不是「优化」，是当前判据自相矛盾或不单调。归因与证据见文首。

| # | 修正 | 现状为什么是错的 |
| --- | --- | --- |
| 1 | S/A 的面积窗口由绝对 `5㎡ / 10㎡` 改为**相对偏差**（与 `sim_area` 的 σ=0.20 同口径） | 同一个 5㎡ 在 4室（中位 184㎡）相当于 ±2.7%，谁也过不了；在 1室（中位 31㎡）相当于 ±16%。实测 2室 S 命中 83%、4室 只有 38%，差异来自刻度而非数据 |
| 2 | S/A 不再要求**室厅卫三元组全等**（厅或卫差 1 仍可入 S/A） | `sim_layout` 给「室厅相等、卫不同」打 0.90，分级函数却判「完全不可比」——同一份方案里两个函数互相矛盾。38% 的 S 级失败栽在这里 |
| 3 | `halls == 0 && baths == 0` 视为**户型未知**，不参与任何全等判定 | 21 套 = 27% 的目标是 `0H0B`，参考集中位数 35 却 **100% 拿不到 S 级**、95.2% 落 `D_ONLY`。`0` 既可能是真实单间也可能是录入缺失，两种解释都要求它别当精确键用 |
| 4 | Resolver：`n_S == 1` 且 secondary 不足时**落 A/B/C/D 阶梯**，不直接 `INVALID` | 非单调 bug：`龙湖梵城` 有 45 条同小区参考、25 条 C 级，却因「恰好只有 1 条 S」被判无基准。多一条更好的参考不该让结果变差 |

**为什么不再单独跑一轮探针**：第 3 期跑完，本地库里就有 3289 个目标和全部小区
参考集，重新标定的原料全在且免费。花 31 次上游请求去买 77 个样本，
提前决定一件能用 3289 个样本决定的事，不划算。分级阶梯是单一入口
（`classify` + Resolver），改它不动 DDL——`benchmark_mode` 是字符串列。

### 为什么覆盖率复算挪到第 3 期（V2.4 修订）

V2.3 把「离线复算三个覆盖率并定稿 2.2 分级阶梯」放在第 1 期出口条件里，
同时声称「第 1 期跑完，本地库里就有 3289 个目标**和全部小区参考集**」。
**后半句做不到，前半句因此也做不到。**

队列 A 的固定查询带 `condition_filters={"fitment": "002"}`，采到的只有
**被排序集 P**。而三个覆盖率（`s_grade_coverage` /
`strong_comparable_coverage` / `valid_community_benchmark_coverage`）全部定义在
「同小区**非清水房** comparable」之上，即**参考集 R**——按 2.1，R 与 P 是
不相交的两个集合，R 只能由**队列 B**（按 `resblock_ids` 逐小区搜、不带 fitment 过滤）
产出。只有 P 没有 R 时，`classify()` 一条 comparable 都算不出来，
三个覆盖率的分母存在而分子恒为 0。

连带结论：**`classify` / Resolver 不属于第 1 期**。上面四条修正在第 3 期
有了真实 R 之后再带上——那时才能用 3289 个样本一次定稿，
而不是先写一版没有数据可验的判据、再改一遍。第 1 期是纯采集。

**第 1 期与第 2 期相互独立**（第 2 期已完成）。
映射改动属于第 1 期是因为队列 A 入库要写 `resblock_id / fitment_status /
create_time / 室厅卫`，走的是 connector 的类型化 API，字段不映射就写不进去。
第 2 期探针**不经过那条链路**（直接 `authorized_fetch` 读原始 JSON），
所以两期没有先后依赖。

编号保留 1/2 的原顺序只是为了不打乱全文的交叉引用，不代表执行顺序。

### 第 2 期：S 级覆盖率探针（必做，且必须在写评分器之前）

**这是本方案唯一的结构性风险点。** S 级要求同时满足：同小区 + 非清水房 +
同租赁模式 + 室厅卫全等 + 面积 ±5㎡。一个总共十几到几十套在租房源的小区，
对某个具体的 3室2厅1卫 89㎡，命中往往是 0–2 套。

如果 S 级覆盖率只有 20%，那么 80% 的房源走的是 B/C/历史/周边分支——
复杂度预算就花在了少数分支上，而真正决定产品成色的是回退链的质量。
**先测，再定算法。**

实验：抽 1 天、约 50 套清水房、覆盖 30 个小区，拉这些小区的在租房源，
统计 S/A/B/C/D 各级命中分布。约 30–50 个请求，一个下午。

**探针实现已被抓包简化**（2026-08-20）：`resblockId` 直接从清水房行上读、
塞回 `resblock_ids` 就能拉该小区全部在租房源，装修状态由行级 `fitmentStatus` 直接分类。
所以探针是「1 次全量首页 → 取 30 个 resblockId → 30 次小区搜索」，
**不需要 suggest、不需要建库**，一次性脚本输出 CSV 即可。

**它也不需要「建议的 connector 改动」先落地。** 探针直接 `authorized_fetch`
读原始 JSON，S 级判据要的 `resblockId / fitmentStatus / rentType /
bedroomAmount / hallAmount / bathroomAmount / area` 在原始行里全都有,
不经过 `_parse_listing`。所以本期与那些映射改动**没有先后依赖**——
反过来才对：先跑探针，再按实测确定的分级口径去决定映射哪些字段，
避免映射了最终算法不用的字段（`kitchenAmount` 就是个例子，S 级不比厨房数）。

**探针必须一并输出装修状态分布**，不只是 S 级命中率——
第七章第 8 条的空装修占比直接决定 R 的实际大小，
而 R 变小会同时压低下面三个覆盖率指标。只测 S 级命中率会把「参考池太小」
误诊成「S 级判据太严」，从而改错东西。

三个覆盖率指标含义不同，**不得混用同一个名字**：

| 指标 | 定义 |
| --- | --- |
| `s_grade_coverage` | 存在 **≥1 套 S 级** comparable 的目标清水房比例 |
| `strong_comparable_coverage` | 存在 **S 或 A 级** comparable 的目标清水房比例 |
| `valid_community_benchmark_coverage` | 能由 `community`（当前态）产出**正式基准**的目标房比例，即 Resolver 未返回 `INVALID` 的比例 |

探针的决策依据是 `s_grade_coverage`：

| `s_grade_coverage` | 决策 |
| --- | --- |
| ≥ 60% | 按 2.2 原样实施，S 级主导 |
| 20% – 60% | 六级压成三级（S∪A、B∪C、D），力气挪到本小区历史回退 |
| < 20% | **调整 Comparable Pool 策略**，见下 |

`< 20%` 那一档的准确含义：**S 级不再作为主路径里的独立基准层**，
改为① comparable 排序时的最高优先证据、② Confidence 的强证据；
主路径基准回到「同室数 + 面积分层」，并重新评估 2.8 的面积偏差是否还能接受。

> **它绝不是 `quality_score += S_bonus`。** 任何形式的「有 S 级就加分」都违反
> 2.1 与 2.6：`quality_score` 只能是 `advantage` 的函数，而 `advantage` 只能来自
> 基准与实际单价之比。参考质量的强弱只允许通过 **`confidence_score` 与
> `structure_cap`** 表达，永远不许流进 Quality。V2 原文「S 级降为加分项」
> 这句话极易被读成前者，故此处改掉。

#### 探针跑完后的补充规定（2026-08-20，实测 35.1%）

上面这张阈值表**只能在归因之后使用**。第一次跑完的教训：

1. **低覆盖率必须先归因，再决定改哪里。** 实测 35.1% 落进「20–60% → 压成三级」
   一档，但下钻发现 38% 的失败是「室厅卫三元组过严」、18% 是「绝对 5㎡ 窗口
   对大户型过紧」——**这两条都是判据标定问题，压成三级并不修它们**，
   只是把测不准的东西合并起来看不见。归因方法见文首「第 2 期探针实测结果」。
2. **探针必须落地原始行**（`resblock_id / rentType / rooms / halls / baths /
   area / price / fitmentStatus / listing_id`，逐条）。
   否则任何「换个判据再算一次覆盖率」都要重买一轮上游请求。
   第一版探针没做，直接导致第 6 条的决策卡住。
3. **探针必须输出分档命中率**（按 `rooms` 分组的 S 命中率、
   `n_S=0` 的病因分布），而不只是一个总覆盖率。
   总数 35.1% 掩盖了「2室 83% / 4室 38%」这种五倍差异，
   而这个差异正是找到病因的唯一线索。
4. **探针必须输出 `001` 与 `003` 的单位租金分布差**（第七章第 9 条）。

### 建议的 connector 改动

全部是**纯字段映射补全，零新增上游请求**。`RentalListing` 是带默认值的 frozen
dataclass，`RentalListingResponse.from_domain` 用 `cls(**listing.__dict__)`，
所以每项改动 ≈ dataclass 加字段 + `_parse_listing` 加一行 + schema 加一行，
现有调用方不受影响（connector 自己的单测要同步）。

| 项 | 改动 | 状态 | 不做的代价 |
| --- | --- | --- | --- |
| **0** | `RentalListingPage` 增 `total: int`（`totalCount`）+ schema 透出 | **已完成（2026-08-20）**。`_parse_page` 本来就算了 `total_count` 却丢掉了；与同文件 `TrusteeshipListingPage` 对齐 | 队列 A 无法在开跑前算出 `pages_expected`，4.2 的 COMPLETE 判据无从实现，只能「一直翻到 `has_more=false`」——那样区分不了「翻完了」和「翻到一半被掐断」 |
| **A** | `RentalListing` 增 `bedroom_amount / hall_amount / bathroom_amount: int \| None` | **已完成（2026-08-20）**：原始行确有 `bedroomAmount=2 / hallAmount=2 / bathroomAmount=1`，`_parse_listing` 读到后拼成字符串就丢了。**`kitchenAmount` 不映射**——S 级不比厨房数，映射了就是无人使用的字段 | S 级「室厅卫全等」判不准：`"3室2厅"` 分不清 0 卫与卫数未知 |
| **B** | `_parse_listing` 增映射 `resblock_id` + `resblock_name` | **已完成（2026-08-20）**，抓包已验证闭环：行里有 `resblockId=3011054720095`、`resblockName='兴盛世家D区'`。把读到的 id 原样塞回 `resblock_ids` 再搜，`resblockName` 集合只有 1 个值——**过滤真的生效**。注意 `resblockId` 是 **JSON 整数**，映射时必须 `_opt_str` 转字符串，别当 int 存 | 队列 B 每个小区要先 `map/suggest` 解析一次 → 每 15 天多花 650–1100 次，直接顶到第三章的硬顶 |
| **C** | `_parse_listing` 增 `fitment_status` / `fitment_status_desc: str \| None` | **已完成（2026-08-20）**：行里有 `fitmentStatus='002'` + `fitmentStatusDesc='毛坯'`，租赁侧码值与销售侧一致。**注意可为 `None`/`''`**，见第七章第 8 条 | 不映射就只能靠「已知清水房 id 集合相减」标记参考集，且精装/简装无法分开统计，更无法识别「装修未知」这第三类 |
| **D** | `_parse_listing` 增 `create_time: datetime \| None`（`createTime`，**不是** `listed_days`） | **已完成（2026-08-20）**，答案和预期不同：`alreadyCreateDays` 在搜索行里**不存在**（整行没有任何 `*days*` 键）。取而代之的是 `createTime`，epoch 毫秒，实测三行解出 2026-07-16 / 08-03 / 08-16，量级合理。已有 `_ts_to_datetime` 助手可直接用。**这比 `alreadyCreateDays` 更好**：存绝对时间戳不会隔夜失效，天数由查询时现算 | 拿不到挂牌时长。这是捡漏的强信号（挂久 = 可谈），但不阻塞评分 |

0 已落地（含 `total=0` 时视为「总数未知」、退回翻到 `has_more=false` 的单测）。
**A/B/C/D 已于 2026-08-20 全部落地并通过 205 项 connector 测试。** 字段问题先用两次
上游请求问清（脚本 `crm_connector/run/probe_rental_row.py` 问字段在不在、
`crm_connector/run/probe_rental_community.py` 问 `resblockId` 塞回去搜是否生效），
再做映射，所以落地时无未知项。参见 memory 里的 CRM 抓包工作流。

落地时发现并处理掉的三件事，写在这里免得后人重踩：

1. **`from_domain` 用 `cls(**listing.__dict__)`，而 pydantic 默认丢弃多余键。**
   所以「dataclass 加了字段 + `_parse_listing` 填了值」全部通过单测，
   字段却**根本没出 API**——store_media 那边一个都收不到，且没有任何报错。
   必须有一条断言完整 JSON 的集成测试把这一层钉住
   （`test_search_wanxiangcheng_flows_through_full_app_pipeline`，逐键相等）。
   本节开头「schema 加一行」那句话不是可选步骤，漏了它这项改动等于没做。
2. **`layout` 拼装原先用 `isinstance(x, (int, float))`，与全文件其他字段的
   `_as_int` 不一致**（前者拒绝 `"2"` 这种数字字符串）。已统一为 `_as_int`；
   实测上游发的是 int，所以行为无实际变化，但去掉了一处双重口径。
3. **`create_time` 存的是 UTC**（`_ts_to_datetime` 全文件统一如此，不单独破例）。
   epoch 毫秒本身无时区，所以存储没问题；但 4.4 第 3 条那句
   `listed_days = (今天 - create_time.date()).days` **必须先转 Asia/Shanghai
   再取 `.date()`**，否则北京时间当晚 8 点之后录入的房源会被算成前一天，
   「录入至今」整体偏大 1 天。

**store_media 侧不需要改**：`RoughcastRentalListing` 是刻意的展示白名单
（「Only the fields that the mobile list is allowed to expose」），新字段
不流向手机列表是正确的。第 1 期的入库管线是**服务端**直读 connector 的
类型化 API，不走这个投影。

抓包顺带发现的、原本没在计划里的行级字段（**本期不接入，登记备查**）：

| 字段 | 实测值 | 为什么记下来 |
| --- | --- | --- |
| `hqiScore` | 41 / 56 / 77 | 上游自己的房源评分。**不能当本方案的输入**（口径不明、且它评的不是「同小区性价比」），但可以拿来做**上线后的合理性交叉验证**：如果我们的 `quality_score` 与它完全无关，得回头查是不是算错了 |
| `haoFangScore` | 8.3 / 8 / 8 | 同上，且区分度明显更低 |
| `priceChange` / `priceTrend` / `priceDifference` | `None` / `0` / `None` | 上游疑似自带调价信息。三行全空，**不能依赖**，4.5 的变更点快照仍是唯一可靠的价格历史来源 |
| `kitchenAmount` | 1 | 与室厅卫同族，但 S 级不比厨房数，**因此不映射**。登记在此是为了将来真需要时知道它存在——「先映射着不使用」是往数据模型里加没人用的字段，违反第十章第 5 条 |
| `trueFloor` | `None`（`floorLevel='中'` 有值） | 印证第三章那条禁令：精确楼层只有详情接口有，全量抓取阶段拿不到 |

**不需要改的**：`condition_filters` 透传已经存在（`kecom_crm_client.py:128`），
`fitment=002` 全量搜索和「按 `resblock_ids` 搜单个小区」今天就能用。

### 上云

沿用 featured 的推送模式（`crm_connector/app/application/featured_snapshot_push.py`：
daemon 线程 + `scp` 到 `{remote}.tmp` 再 `ssh mv`，远端路径必须留在
`/var/lib/store-media/` 下）。云端 store_media 永不主动连内网 CRM 机器。

采集与评分模块因此要与传输方式无关：本地阶段由 store_media 内的后台线程直抓
connector，上云阶段由 connector 侧复用同一模块并推送快照，
store_media 只需把数据源指向推送产物。

## 七、待联调确认

以下都是**未验证**的事项，不得当作已确认事实写进代码注释。

1. ~~**租赁侧 fitment 码，以及参考集里清水房的识别方式**~~ **已解决（2026-08-20 抓包）**。
   行里有 `fitmentStatus` + `fitmentStatusDesc`，**租赁侧码值与销售侧一致**，
   由中文描述直接自证：`'002'→'毛坯'`、`'001'→'简装'`、`'003'→'精装'`。
   原先「拿不到行级装修，只能靠已知 id 集合相减」的判断**是错的**，
   相应地：`is_roughcast` 直接读 `fitmentStatus == '002'`，
   V2 那条「分别统计精装/简装样本数」的要求**现在可以做到**，不必再走分查或降级。

   **但抓包同时暴露一个新问题，见下面第 8 条——装修状态可以是空的。**
2. ~~**租赁搜索行是否直接带 `resblockId`**~~ **已解决且已验证闭环（2026-08-20）**。
   行里有 `resblockId`（JSON 整数）与 `resblockName`。
   把读到的 id 原样填进 `RentalListingFilters.resblock_ids` 再搜一次，
   返回结果的 `resblockName` 集合只有一个值——**过滤生效，读写口径一致**。
   结论：**队列 B 每个小区 1 个请求，完全不需要 `rental_map_suggest`**，
   第三章预算表里的 suggest 一项归零。
3. **δ 的实际取值**。~~须人工确认落在 0.6–0.85~~ **预期区间已被实测推翻**：
   2026-08-20 探针测得 `ln(reference_u/u)` 中位数 0.868（强基准组 0.705），
   即 **δ ≈ 0.42–0.49**，清水房单价在装修房的**一半以下**，远低于原先估的 0.6–0.85。
   首轮全量的人工确认改为对照 **0.35–0.55**，落到区间外要先查是不是参考集混进了
   毛坯或未知装修的行。仍保留的限制：同时拥有清水房和装修房、且清水房有 B 级以上
   参考的小区若太少（<30 个），δ 不可信，退化为配置值。
   另见文首「δ 的首个实测值」一节——那里同时给出了 `k` 由 `0.18` 调到 `0.50` 的推导。
4. ~~**大盘小区参考房源可能超 50 条**~~ **已实测（2026-08-20，第 2 期探针）**。
   30 个小区里 **11 个超 50 条 = 36.7%**，最大 `麓公子 total=162`（4 页），
   其余超限的是 111 / 105 / 74 / 73 / 70 / 62 / 55 / 54 / 54 / 51。
   截断**不是边缘情况，是三分之一的常态**，队列 B 必须翻页。
   按 `ceil(total/50)` 算这 30 个小区共需 **45 页 = 平均 1.5 页/小区**，
   高于第三章原先假设的 1.3。预算重算：66 + 90 + 0 + 10 = 166，
   ×1.15 = **191**，距硬顶 260 余量 **69**（原 82）。
   **一个抽样偏差的提醒**：这 30 个小区是从清水房搜索首页取的，
   房源多的小区更容易出现在首页，所以 1.5 页/小区是**偏高估计**，
   真实均值应 ≤1.5。预算往高处算是安全方向，不修正。
   V1 只取前 2 页的做法会漏掉 `麓公子` 这类小区的一半参考，
   **截断必须 log 出来，不得静默**（第九章已有指标）。
5. **周边小区的判定依据**——**已可决定（2026-08-20，探针给出需求量）**。
   - ✅ **商圈**：租赁搜索行**直接带 `bizCircleName`**（实测 `'华侨城'`），
     不需要绕 `SaleMapSuggestion`。所以 2.4.2 门槛 2 的「同商圈优先」这半边
     **可以只靠队列 A/B 已有的数据实现**，无额外请求。
     同行还有个 `bizCode`（实测为 `None`），若它将来有值会比中文名更适合做键——
     商圈中文名改名就会串，和 `resblockName` 同一类问题。
   - ❌ **坐标**：搜索行里**没有任何** lat/lng/经纬度字段（已逐键确认，
     只有 `mapArea` / `mapPrice` / `mapTitle` 这类展示字段）。
     所以门槛 2 的「跨商圈时距离 ≤1.5km」**目前无法实现**，
     需要另打 `RentalMapBubble` 之类的接口，那是**每小区一次额外请求**。

   **结论：门槛 2 降级为「只允许同商圈」，放弃跨商圈距离判定。**
   依据是探针实测的周边分支需求量：`valid_community_benchmark_coverage = 85.7%`
   （修掉上面那个非单调 bug 后 **89.6%**），即最多 10.4% 的目标房需要回退；
   而 2.4.1 规定本小区历史必须**先**被穷尽，所以真正走到 `nearby` 的比例还要更低。
   为一个 <10% 且还会被历史回退进一步吃掉的分支，增加每小区一次请求
   （60 个小区 = +60 次，余量 69 会被吃光），不划算。
   代价照实记下：**商圈边缘的小区会失去本可用的邻居**，
   表现为这部分房源停在 `INVALID` 而非拿到 `NEARBY` 估算。
   若上线后 `nearby` 需求量显著高于预期，再回来加坐标接口。
6. ~~**合租行的 `area` 到底是整套还是单间**~~ **风险已被结构性消除，占比也已实测**。
   77 套目标清水房**全部是整租**（`rentType` 全为 `'001'`，`delType` 全为 `2`），
   一套合租都没有——所以被排序集 P 侧不需要「单间价 vs 整套面积」的分支。
   更要紧的是：`classify()` 的第一道判据就是
   `target.rentType != cand.rentType -> 不可比`，
   **参考集里即便混有合租，也不可能成为整租目标的 comparable**。
   所以 2.8 的合租隔离**留下，但只是这一行守卫，不是一个子系统**。
   仍未确认的是合租行 `area` 本身的语义，但在上述守卫下它已不影响 V1 结果；
   等 P 侧真的出现合租再查。
7. ~~**清水房总数是否稳定在 3000 上下**~~ **量级已确认，稳定性待观察**。
   2026-08-20 实测 `total = 3289`（`scope=all` + `fitment=002`，`page_size=50`
   → `has_more=true`，66 页）。量级假设成立，第三章节奏参数已按 66 页重算，
   仍落在 09:30–19:00 窗口内。
   **仍未确认的是稳定性**——单次测量证明不了它不会在某天变成 8000。
   第 1 期出口条件「连续 3 天产出 COMPLETE run」正好提供这个观察窗口，
   期间需记录每日 `total` 并确认波动幅度。队列 A 的实现**不得把 66 写成常数**，
   必须每轮从 `total` 现算 `pages_expected`（4.2 的 COMPLETE 判据依赖它）。
   **已有第三个数据点**：`3289` → `3290`（同日稍后）→ **`3291`**（第 2 期探针，同日）。
   一天内 +2、单调上升，量级非常稳定；但三点仍不足以定波动区间，
   且都在同一天，跨天行为未知。第 1 期出口条件的 3 天窗口仍需照做。
8. ~~**装修状态为空的房源占比是多少**~~ **已实测 5.7%（2026-08-20，第 2 期探针，770 行）**。
   全市 30 小区 770 条在租行的装修分布：
   `003 = 548 (71.2%)`、`001 = 101 (13.1%)`、`002 = 77 (10.0%)`、**空 = 44 (5.7%)**。
   5.7% 远低于第九章 20% 的告警线，**不构成覆盖率问题**——
   35.1% 的 `s_grade_coverage` 不能归因于装修缺失（见开头「第 2 期探针实测结果」的归因表）。
   规则不变，仍照执行（空值行落库计数，不进 P 也不进 R）：

   ```
   fitmentStatus == '002'            -> 进 P（清水房，被排序）
   fitmentStatus in ('001','003')    -> 进 R（参考集）
   fitmentStatus 为 None / '' / 其他 -> 两个集合都不进，但要计数并落库
   ```

   落库时不要把空装修行直接丢掉：丢掉就永远不知道占比在变大还是变小。

   **但冒出一个新问题（列为第 9 条）**。
9. **参考集 R 混装了 `001` 和 `003`，而两者数量差 5 倍**（548 : 101）。
   R 目前把这两档一视同仁地丢进同一个池子算 `b_i`。若 `001` 与 `003`
   的单位租金系统性不同（大概率不同，它们是不同装修档次），那么
   **一个小区的 R 恰好偏向某一档，它的 benchmark 就被整体推高或推低**——
   这是**偏差**（bias），会直接进 `quality_score`，性质和 2.4.2 的周边偏差同级，
   不是能靠样本量摊平的方差。
   两个候选修法：①R 只取 `003`（548 行，量足够），`001` 作次级回退；
   ②按装修档分层算 `b_i` 再融合。
   **无法用现有产物判定**——探针的 CSV 没有逐条参考行的 `fitmentStatus` × 单位租金，
   这正是上面「探针必须留下原始数据」那条缺陷的第二个受害者。
   下一轮探针**必须一并输出 `001` 与 `003` 的单位租金分布差**。
10. **预算记账在 connector 里有一处缺口**（V2.4 新增，与上游行为无关，是我们自己的实现）。
   `crm_connector/app/infrastructure/kecom_session_provider.py` 的 `_maybe_autorefresh`
   在遇到 401 时会**自行重发一次请求**。那是一次真实上游请求，
   但它发生在 connector 进程内部，store_media 侧的节流器看不见、扣不到——
   即当天实际请求数可能比 `roughcast_crawl_log` 的行数多。

   现状可接受：401 会同时触发熔断，当天不再有后续请求，所以泄漏量上界很小
   （每 run 至多个位数），由 `retry_reserve` 吸收。
   **待确认**：要不要把预算扣减下移到 connector 的 `authorized_fetch`。
   下移的好处是「每一次真实上游请求都被计到」成为结构性事实；
   坏处是节流状态要跨服务共享，两个服务的职责边界会被打破。
   在队列 B 上线（请求数翻倍、余量只剩 69）之前必须定案。

## 八、测试要求

### 必有的回归测试

| # | 测试 | 断言 |
| --- | --- | --- |
| 1 | **密度不变性 · 同价** | 同小区 2 套 vs 20 套**完全同价**清水房，`quality_score` **精确相等**。这是约束 3 的核心回归测试 |
| 2 | **密度不变性 · 同分布** | 同小区 2 套 vs 20 套**同分布**（非同价）清水房，`\|Δquality\| ≤ 2`。样本量不同时中位数估计值本就不同，**必须给容差**，否则测试会随机变红 |
| 3 | δ 小区等权 | 一个 20 套清水房的小区不得主导 δ；与 20 个各 1 套的小区权重一致 |
| 4 | δ 同口径 | δ 标定使用的 benchmark 逻辑与评分使用的**同一函数**（用 spy/mock 断言调用同一实现） |
| 5 | 禁止权重累加 | 20 套 D 级参考的 `confidence_score` 不得超过 3 套 S 级参考 |
| 6 | 分级阶梯 | S≥3 纯 S；S=2 用 80/20；S=1 用 40/60 且 confidence ≤60 |
| 7 | 回退链四级 | community → community_history → nearby → insufficient，逐级构造数据验证 |
| 8 | 历史去重 | 同一 `listing_id` 的 30 条快照只算 1 个样本 |
| 9 | 合租隔离 | 合租房源不参与整租的参考池，反之亦然 |
| 10 | 异常分类 | `data_error` 不给分；`extreme_price` **照常给分并进排名** |
| 11 | 固定映射 | 给定 advantage，`quality_score` 与 2.6 对照表逐行一致；且**与其他房源的存在无关**（加入 100 套新房源不改变已有分数） |
| 12 | 同分稳定序 | 同 `quality_score` 按 `listing_id` 排，两次运行 `city_rank` 完全一致 |

### V2.1 新增回归测试

| # | 测试 | 构造 | 断言 |
| --- | --- | --- | --- |
| A | **等级互斥** | 同小区非清水整租、室厅卫全等、面积差 2㎡ | 该 comparable 的 `grade` **只能是 `S`**；遍历分级函数输出，断言每个 comparable 恰好落在一个等级里（不得同时进 S 和 A/B/D 池）。这是 2.2「最高等级唯一归属」的直接回归 |
| B | **S=2 + secondary** | S=2，A=3 | `benchmark_mode == "S2_BLEND"`；`benchmark == 0.80·wm(S) + 0.20·wm(secondary)`（按 2.3 公式逐位比对）；`structure_cap == 100` |
| C | **S=2 无 secondary** | S=2，A=B=0 | `benchmark == wm(S)`；`peer_scope == "community"`；`benchmark_mode == "S2_ONLY_WEAK"`；`confidence_score <= 70`。**不得**出现 NaN / 抛异常 / 直接跳到 `community_history` |
| D | **S=1 + secondary** | S=1，A∪B ≥ 2 条不同 `listing_id` | `benchmark_mode == "S1_BLEND"`；`benchmark == 0.40·u_S + 0.60·wm(secondary)`；`confidence_score <= 60` |
| E | **S=1 无 secondary，且阶梯也不满足** | S=1，A∪B 为空（或只有 1 条），**且 A/B/C/D 各档均 < 3 条** | Resolver 返回 `INVALID`；**必须**进入 `community_history` 分支。断言绝不出现「用唯一 1 套房当正式基准」的结果。**V2.3 修正**：原测试没有「阶梯也不满足」这个前提，与新增的 V 条冲突——见 V |
| F | **历史 Confidence 单调递减** | 同一份 8 套高度相似 S 级参考，分别置于当前 / 30d / 60d / 90d | `conf(current) > conf(30d) > conf(60d) > conf(90d)`，且各自 `<=` 对应来源上限 100/80/70/60。同时验证 `quality_score` **不因新鲜度而变**（新鲜度只进 Confidence） |
| G | **预算按真实请求扣减** | 一个小区任务发生 2 次分页搜索 + 1 次重试 | 预算恰好扣 **3**，不是 1（按任务数扣会得到 1）。构造用的是分页与重试而**不是** suggest——`resblockId` 已可直接读取，suggest 不在方案内（第七章第 2 条）；但扣减规则本身必须对**任何**真实上游请求成立，将来若新增请求类型不需要改这条测试 |
| H | **排序用未取整分** | A `quality_score_raw=82.49`，B `=82.01`，两者 `quality_score` 都是 82 | `rank(A) < rank(B)`。若实现误用整数排序，此测试必红 |
| I | **`reference_unit_rent` 可解释** | 任意已评分房源 | `expected_unit_rent ≈ reference_unit_rent × delta_value`（浮点容差 1e-6）；且 `advantage ≈ ln(expected_unit_rent / unit_rent)` |
| J | **NEARBY 可复现** | `nearby` 取 3 个小区的基准，且这 3 个基准来自**不同的 `reference_run_id`** | 仅凭 `score_id` 对应行的 `benchmark_pool_json` + `model_version` + `delta_value`，重算出的 `reference_unit_rent / expected_unit_rent / advantage / quality_score_raw` 与入库值逐位一致。这条同时证明 4.3 的可复现性表述成立——单个 `reference_run_id` 做不到这件事 |
| K | **历史必须先被穷尽** | 本小区当前态 `INVALID`；历史 ≤30d `INVALID`；历史 31–60d **valid** | 结果 `peer_scope == "community_history"`、`source_cap == 70`。断言全程**未调用**周边小区数据加载函数（spy/mock）。若实现在 30 天不 valid 时就跳去周边，此测试必红（2.4.1） |
| L | **周边不进主排名** | 一套 `nearby` 房源 `quality_score_raw = 92`，一套 `community` 房源 `= 88` | `nearby` 那套 `quality_status == "nearby_estimate"` 且 `city_rank IS NULL`；`community` 那套的 `city_rank` 是全场第一。断言主榜查询结果里**不含**任何 `peer_scope='nearby'` 的行（2.4.2 ②） |
| M | **周边离散度护栏** | 3 个周边小区基准 `40 / 46 / 52`（`spread = (52−40)/46 = 0.26 > 0.25`） | 判定周边不可用 → `quality_status == "insufficient"`，不产出 `nearby` 基准。再构造 `44 / 46 / 50`（`spread = 0.13`）→ 正常产出 `nearby`，且 `reference_spread` 落库为 0.13（容差 1e-6） |
| N | **周边只用强基准邻居** | 5 个周边小区，其中 4 个 `benchmark_mode = D_ONLY`，仅 1 个 `S_ONLY` | 强基准邻居数 1 < 3 → 门槛 1 不过 → `insufficient`。不得用那 4 个 `D_ONLY` 凑数（2.4.2 门槛 1） |
| O | **周边展示为档位** | `nearby` 房源 `quality_score_raw = 88.4` | API 返回 `quality_tier == "疑似划算"`；`quality_score` 仍照常入库（δ 标定与回填要用），但前端契约里该房源不出现精确整数位（2.4.2 ③） |
| P | **高可信捡漏排除周边** | `nearby` 房源 `quality_score = 95`、`confidence_score = 45` | 「高可信捡漏」筛选结果为空。即使把该房 `confidence_score` 人为改到 90，仍不得进入——筛选条件的第一项是 `quality_status = 'scored'` |
| Q | **变更点写入** | 同一 `listing_id` 连续 5 轮抓到，其中第 3 轮租金从 2400 降到 2300 | snapshot 里恰好 **2 行**：第 1 行 `captured_at`=轮1、`last_confirmed_at`=轮2；第 2 行 `captured_at`=轮3、`last_confirmed_at`=轮5。不得出现 5 行（4.5） |
| R | **变更点下的新鲜度** | 一套房 60 轮未改价，最近一轮仍在架 | `w_fresh == 1.00`、`reference_age_days == 0`。若实现误用 `captured_at`（60 天前）会得到 0.70，此测试必红（4.4 规则 1 + 4.5 末尾的说明） |
| S | **参考集指针指向完整批次** | 小区 X 有两批参考：run 500（COMPLETE，20 条）、run 501（ABORTED，写入 8 条后中断） | `reference_run_id == 500`；Resolver 读到 20 条而非 8 条或 28 条。断言实现**未使用** `max(run_id)`（4.1 / 4.2） |
| T | **清理任务的安全阀** | 构造待删行数占总行数 40% 的场景 | 清理任务**放弃本次清理**并告警，一行未删。再构造 2% 的场景 → 正常删除（4.5） |
| U | **装修三分类，未知两边都不进** | 一个小区 10 行：`fitmentStatus` 为 `002`×1、`001`×3、`003`×4、`None`×1、`''`×1 | P 恰好 1 条、R 恰好 **7** 条（不是 9 条）；两条未知装修行**既不在 P 也不在 R**，但 `unknown_fitment_count == 2` 被记录。再断言这 2 行**已落库**（`fitment_status` 原值存下来，不是被丢弃、也不是被压成 `is_roughcast=false`）。若实现把未知当非清水房塞进 R，`reference_unit_rent` 会偏离，此测试必红（2.1 / 第七章第 8 条） |

### V2.3 新增回归测试（全部来自第 2 期探针的真实数据）

| # | 测试 | 构造 | 断言 |
| --- | --- | --- | --- |
| V | **Resolver 单调性：多一条 S 不得让结果变差** | 用探针实测的 `龙湖梵城` 向量：`n_S=1, n_A=1, n_B=0, n_C=25, n_D=18`，secondary 只有 1 条 | 结果**不是** `INVALID`，而是落到 `C_ONLY`（`n_C=25 ≥ 3`），`structure_cap == 45`。再构造把那条 S 拿掉的同一份数据（`n_S=0`），断言两者都 valid、且**加了 S 的那份 `confidence_score` 不低于**没加的那份。原实现在 `n_S==1 && !secondary_ok` 直接 return `INVALID`，此测试必红。第二个真实向量 `华侨城粼港樾府` `1/0/0/2/3` → 应得 `D_ONLY` 而非 `INVALID` |
| W | **`rooms == 0` 不可排名** | 两套探针实测的 0室 房：`29㎡/400元/0厅0卫` 与 `19㎡/500元/1厅0卫` | 两套都**不进入被排序集**（或进入但 `quality_status == "insufficient"`），且**不得互相成为 comparable**。若实现只对 `rooms is None` 做兜底，`0 == 0` 会让它们判成同户型，此测试必红 |
| X | **户型-面积一致性哨兵** | 探针实测的「1室 260㎡」（1室面积区间 29~260） | 该行被标记为可疑并排除出参考池 R；断言它**没有**成为任何 1室 目标房的 comparable。哨兵规则与阈值须落在配置里，不得硬编码 |
| Y | **约束 3 的真实数据 fixture** | 直接用探针 CSV 里 `翡翠城五期` 的 **8 套** 清水房 + 该小区 34 条参考（真实面积/租金/户型） | 8 套的 `benchmark` 逐位相同、`benchmark_mode` 全为同一值、`n_eff` 相同。再把这 8 套裁剪成 2 套重跑，断言**留下的那 2 套评分与 8 套时完全一致**（容差 1e-6）。这是约束 3「2 套和 20 套评分应差不多」在真实数据上的回归，比合成数据更有说服力 |

> Y 的 fixture 从 `probe_s_coverage_targets.csv` / `_communities.csv` 落成静态文件签入仓库，
> **不要在测试里联网重取**——探针数据会变，回归测试不能跟着变。


- 分页终止、每日预算上限、最小间隔、熔断触发后不再发请求、抓取期不打详情接口
- `crawl_runs`：ABORTED 的 run **不刷新** `listing_current`、**不标记下架**
  （V1 bug 的直接回归测试）
- COMPLETE 事务性：刷新过程中异常 → current 表保持原样，无半更新
- 小区命中缓存不重复请求、`next_refresh_at` 轮转选取、P0 插队上限

### 集成

- 排序切页不重不漏、`sort_applied`、`min_confidence` 过滤、库空时的降级、页面渲染
- 收尾在 `services/store_media` 下跑 `python -m pytest`

## 九、监控

| 指标 | 用途 | 告警线 |
| --- | --- | --- |
| `s_grade_coverage` | 有 ≥1 套 S 级参考的房源占比。**决定整个排名的含义** | < 30% 需重新评估算法 |
| `strong_comparable_coverage` | 有 S 或 A 级参考的房源占比 | < 50% |
| `valid_community_benchmark_coverage` | 本小区当前态能出正式基准的房源占比（Resolver 非 `INVALID`） | < 60% |
| `benchmark_mode_distribution` | 各基准结构占比；`S2_ONLY_WEAK + S1_BLEND` 过高说明参考普遍偏薄 | 二者合计 > 40% |
| `community_reference_coverage` | 有基准的小区占比 | < 90% |
| `nearby_estimate_ratio` | 落到「周边估算」分组的房源占比。**这是回退链健康度的核心指标** | > 15%，说明队列 B 覆盖不足或 Resolver 门槛过严 |
| `nearby_spread_p50` / `_p90` | 周边基准离散度分布。p90 持续接近 0.25 说明周边参考本就不适用于本市小区结构 | p90 > 0.22 |
| `history_rescue_ratio` | 本小区当前态不 valid、但被历史救回的房源占比 | 突然掉到 0 通常是历史查询写错了，而不是市场变了 |
| `delta_value` / `delta_version` | δ 漂移监控 | 越出 `[0.55,0.95]` |
| `null_quality_ratio` | 无分房源占比 | > 25% |
| `unknown_fitment_ratio` | 参考快照里 `fitment_status` 为空的行占比。**它上涨会同时压低上面三个覆盖率**，而且症状看起来像「S 级判据太严」——先看这个指标再动算法 | > 20%，或**相对上周翻倍** |
| `extreme_price_count` | 待人工核数量 | 突增 3 倍 |
| `daily_request_count` | 上游负载 | 超预算 |
| `breaker_trips` | 熔断次数 | > 0 即告警 |
| `snapshot_age_hours` | 最新 COMPLETE run 的年龄 | > 36h |
| `run_status_distribution` | ABORTED/FAILED 占比 | ABORTED > 20% |

### 首次实测基线（2026-08-20，第 2 期探针，30 小区 / 770 行 / 77 目标）

告警线是拍的，这几个是量的。上线后第一次采数应先跟这组比，而不是直接跟告警线比：

| 指标 | 首测值 | 对告警线 |
| --- | --- | --- |
| `s_grade_coverage` | 35.1% | 高于 30%，但归因显示是标定问题（见文首） |
| `strong_comparable_coverage` | 46.8% | **已低于 50% 告警线** |
| `valid_community_benchmark_coverage` | 85.7%（修 bug 后 89.6%） | 远高于 60% |
| `unknown_fitment_ratio` | 5.7% | 远低于 20% |
| `benchmark_mode_distribution` | `S2_ONLY_WEAK + S1_BLEND = 7.8%` | 远低于 40% |
| `nearby_estimate_ratio` | 探针未实现周边分支，**未测** | — |

两点必须记住：

1. **`strong_comparable_coverage` 开局就破线**。这条告警线是在不知道分级判据
   有标定错误时定的。修完标定再看它，不要因为它响就先去改告警线——
   **告警线响了先查被测的东西，不是先改尺子**。
2. `D_ONLY` 占 40.3%，说明多数房源的基准来自最松一档。
   这不触发任何现有告警，但它意味着 `quality_score` 的平均信息量比设计预期低。
   应新增一条：`d_only_ratio > 50%` 告警。

## 十、原则

1. **数量不得转化为优质**。参考集分离、小区等权、固定映射、禁止权重累加，
   四处任一处松动，约束 3 就失效。
2. **口径必须唯一**。δ 与 benchmark 同逻辑，`n_eff` 只有一个定义。
   不同口径混用产生的偏差是全局的、无异常点的、抽查发现不了的。
3. **不完整的数据绝不发布**。只有 COMPLETE 的 run 能刷新当前态。
   宁可用昨天的完整数据，不用今天的一半数据。
4. **可信度必须可见可筛**。低可信的高分是误导，不是发现。
5. **不确定的事写进第七章，不写进代码注释**。猜测不得伪装成已确认事实；
   新增模块必须标明是「建议新增」，不得凭空引用不存在的函数和接口。

## 十一、方案自检

**这是代码符合性检查表，不是文档定稿检查表。** 除「一致性」组以外，
每一条问的都是「代码里是不是这样写的」——在没有代码时逐条打勾只是拿文档
核对文档，抓不到任何问题。

因此按组绑定到第六章的分期出口，各组在对应期的验收里过：

| 组 | 检查时机 |
| --- | --- |
| 一致性 | **文档定稿时**（即现在），是唯一可以脱离代码检查的一组 |
| 采集与库 | 第 1 期出口 |
| 分级与基准 / 数量不得转化为优质 | 第 4 期出口（评分器） |
| 可信度上限 / 回退链与周边降级 | 第 4 期出口 |
| 采集预算 | 第 1 期出口，第 3 期（队列 B 上线后）复查一次 |
| 排名与存证 | 第 4、5 期出口 |

任一条打不了勾，先判断是**代码没照文档写**还是**文档这条本身没定义清楚**：
前者改代码，后者先改文档再改代码，不允许就地放宽标准。

**分级与基准**

- [ ] S/A/B/C/D 严格互斥（2.2）
- [ ] 一套 comparable 只能属于一个 grade，且是它满足的**最高**等级
- [ ] `n_S = 2` 且 secondary 可用 → 定义明确（`S2_BLEND`，80/20）
- [ ] `n_S = 2` 且 secondary 不可用 → 定义明确（`S2_ONLY_WEAK`，cap 70，仍属 community）
- [ ] `n_S = 1` 且 secondary 可用 → 定义明确（`S1_BLEND`，40/60，cap 60）
- [ ] `n_S = 1` 且 secondary 不可用 → 返回 `INVALID` 并正确进入历史回退
- [ ] secondary 的最低样本要求**只有一个定义**（A∪B 中 ≥2 条不同 `listing_id`）
- [ ] 「本小区基准是否成立」**只由 Resolver 判定**，回退链不再自带一套并行条件（2.3/2.4 无冲突）

**可信度上限**

- [ ] `community_history` 有按 30/60/90 天递减的来源上限（80/70/60）
- [ ] `nearby` 仍有上限（45）
- [ ] `confidence_score = round(min(raw, source_cap, structure_cap))`，全局只有这一处截断

**回退链与周边降级**

- [ ] 本小区历史**三档全部试过**才允许进周边（2.4.1）
- [ ] 历史查询只走本地库，零上游请求
- [ ] 历史样本按 `DISTINCT listing_id` 去重，每 id 只取最近一条快照
- [ ] 周边门槛 1：≥3 个邻居，且各自是 `S_ONLY / S2_BLEND / A_ONLY` 强基准
- [ ] 周边门槛 3：`spread > 0.25` 判周边不可用，且 `reference_spread` 落库
- [ ] 周边基准按**小区等权**取中位数，不是按房源等权
- [ ] `nearby` → `quality_status='nearby_estimate'`、`city_rank IS NULL`、不进主榜
- [ ] `nearby` 前端只显示档位，不显示精确整数
- [ ] 「高可信捡漏」筛选条件第一项是 `quality_status='scored'`，`nearby` 永不入选

**数量不得转化为优质**

- [ ] `s_grade_coverage` 低时**绝不**给 Quality 加分，只影响 pool 策略与 Confidence（第 2 期）
- [ ] `quality_score` 只是 `advantage` 的函数，不含任何样本量项
- [ ] δ 按小区等权，密集小区不能主导
- [ ] 融合是「先定池、再融合」，不存在按 comparable 逐个累加权重的路径

**采集预算**

- [ ] 预算按**真实上游请求**扣减，不是按任务数
- [ ] 大盘小区多页会正确消耗多个预算额度
- [ ] 重试也扣 1；扣减发生在**发起请求处**，不在任务完成处——
      这样将来新增任何请求类型都自动计入，无需修改预算代码

**排名与存证**

- [ ] `quality_score_raw` 用于精确排名，`quality_score` 只用于展示
- [ ] `reference_unit_rent` 正式入库
- [ ] `benchmark_mode` 正式入库（独立列，不靠解析 `reason`）
- [ ] `nearby` / `community_history` 的多来源能真正复现（4.3）
- [ ] 只保存**实际参与**基准计算的 comparable，不保存全部候选
- [ ] 给定 `score_id`，证据链完整：模型版本 + δ + 目标房源快照 + `benchmark_pool_json` 即可重算

**采集与库**

- [ ] `reference_run_id` 指向 COMPLETE 批次，实现里没有 `max(run_id)`
- [ ] 日快照按 `content_hash` 变更点写入，未变化只更新 `last_confirmed_at`
- [ ] 新鲜度用 `last_confirmed_at`，不是 `captured_at`、更不是 `first_seen_at`
- [ ] 参考快照仍按批次全量写入，未被改成变更点
- [ ] 清理任务独立于采集与评分流程，且有「待删占比超阈值即放弃」的安全阀
- [ ] 月度基准聚合任务在参考快照被清理之前运行，且与评分同口径
- [ ] 代码与文案里没有把 `first_seen_at` 当作真实挂牌时长
- [ ] `create_time` 以绝对时间戳入库，`listed_days` 是查询时派生的，没有被固化进表
- [ ] 装修状态按**三分类**处理：`002`→P、`001`/`003`→R、空值两边都不进
- [ ] 参考快照存 `fitment_status` **原值**，不是只存 `is_roughcast` 布尔值
- [ ] 空装修行**落库并计数**，没有在采集阶段被静默丢弃

**一致性**

- [ ] 无前后章节互相矛盾
- [ ] 无公式引用不存在的集合（如 `n_S=1` 时的 `wm(S)`）
- [ ] 每个分支的边界条件都有定义，无「其余情况未定义」
- [ ] 所有核心规则都能写成确定性单测（对应第八章 1–12 与 A–Y）
