# NTE Drive Calculator 2.1.0：战报收益画像与后续开发方案（临时）

> 文档状态：2.0.3 战报基线上继续开发，持续更新
> 目标版本：NTE Drive Calculator `2.1.0`（开发中）
> 当前应用基线：`2.0.3`，分支 `2.0.3`
> 当前账号数据库版本：`13`
> 文档版本：`0.8.0`
> 初始整理日期：2026-08-04
> 最近更新日期：2026-08-07
> 已交付基线：[2.0.3 摘要型战报交付说明](../2.0.3/battle-report.md)
> 上游协议边界：[2.1.0 nte-core 合作开发需求](nte-core-collaboration.md)
> 长期收益与配装目标：[2.1.0 队伍收益与配装优化计划](team-benefit-allocation.md)

## 1. 本期决策

2.0.3 已使用当前 nte-core CLI 正式开放的聚合摘要能力交付独立“战报”页面、实时悬浮窗、
最终摘要和账号历史。本文件不再把这些能力列为 2.1.0 待开发项。

2.1.0 在 2.0.3 事实层上继续建立以下收益闭环：

1. 对 2.0.3 保存的上下半、角色、技能和伤害渠道进行可对账分类；
2. 将聚合占比投影为版本化、明确标记 `summary_only` 的 `RoleBenefitProfile`；
3. 结合本地伤害公式计算分渠道边际和单角色综合边际；
4. 以角色在所在半场的伤害占比，折算多角色配装时的队伍收益权重；
5. 角色页和后续单角色配装只消费公开收益画像，不依赖战报页面或队伍页面。

本期不先建立独立的手工编队、最佳四人和最佳八人页面。队伍领域不删除：每份深渊半场战报
本身就是一份 `ObservedTeamSnapshot`，未来队伍搜索、Buff 间接贡献和 Shapley 分摊继续消费相同
战报与收益画像 contract。

合作方协议未修改前，2.0.3 已完成“摘要型战报”闭环，并固定以下前置边界：

- `battle.get_summary` 的最终原始 JSON 是摘要快照的权威事实；
- SQLite 只拆出历史列表、战斗上下文和全局 FIFO 需要的最少索引字段，不为摘要伪造逐击事实；
- 战报是通用战斗快照；深渊使用 `abyss.floor`，非深渊只标记为未知场景，不把
  `abyss.detected=false` 擅自解释为大世界、Boss 或普通副本；
- 当前账号最多保留 100 条战报，其中手动保存最多 50 条；总量超限删除最旧自动记录，第 51 条
  手动保存会删除最旧手动记录；
- 手动保存是把同一记录从自动状态提升为手动状态，不复制原始 JSON；用户可在历史列表右侧
  保存、取消保存或删除记录；
- 页面进入时恢复上次查看的保留快照，并提供“读取历史战报”入口读取当前账号 SQLite 中的自动/
  手动记录；
- “读取历史战报”只读取账号内已保存的聚合摘要；当前不提供面向用户的逐击 JSON 导入入口。
  本地 `nte_capture` 文件只是研究样本，当前 CLI 不能生成，不能作为产品能力前提。

### 1.1 当前开发进度（2026-08-07）

已完成第一条实现代码链并通过自动化验证，真实游戏场景继续由维护者验收：

- 一级“战报”菜单已放在“鉴定”之后；
- 已建立 nte-core 战斗摘要 DTO 的 Integration 校验和不可变 Domain 投影；
- 已建立 Qt 无关的 `BattleCaptureService`，消费 `event.battle.summary`，停止时再读取
  `battle.get_summary` 作为最终内存结果；
- 页面已显示队伍 DPS、总伤害、时长、承伤、角色伤害贡献、技能伤害明细和数据质量摘要；
- 页面已增加跟随当前/上半/下半明细范围，以及定高的角色伤害构成卡；角色卡按直伤、特殊
  伤害、具体环合和其他分类，超过五行时在卡内滚动，公共“其他”单独统计倾陷、环境、共享与
  未归因伤害；
- 已按合作方 Technical HUD 信息结构实现 `380 × 238` 的透明、无边框、置顶和可切换鼠标穿透
  悬浮窗，包括
  队伍摘要、四色角色占比、角色头像/DPS/占比和实时 DPS 小折线；
- 战报采集开始前会暂停正在运行的背包同步，结束或失败后仅在账号和 generation 未变化时恢复；
- 活动战报采集会阻止账号切换，应用退出会先停止战报会话。
- 已新增账号 SQLite v13：不可变摘要事实、自动/手动 retention 和页面恢复状态分表保存；
- 已实现 `BattleReportDaoMixin` 的原始 JSON/SHA-256、operation 幂等、账号总数 100、manual 50、
  自动/手动 FIFO、显式删除和历史只读查询；
- `BattleCaptureService` 已通过窄写入接口把最终 `battle.get_summary` 交给
  `BattleReportPersistenceService`，实时 summary 事件仍不入库；
- 已接入冻结账号 ID、用户库路径和 generation 校验；旧上下文结果不写数据库；
- 页面初始化和账号切换后已可恢复该账号上次战报及跟随当前/上半/下半范围；
- 顶部“保存伤害结果”和“读取历史战报”已接入，历史弹窗展示角色头像、保存时间、深渊层数/
  未知场景、伤害摘要和自动/手动状态；每行右侧提供查看、保存/取消保存和确认删除。

用户已通过真实游戏完成一次深渊第 8 层采集，确认当前 `battle.get_summary`：

- 能返回整场、角色、技能、上下半和质量聚合；
- `abyss.floor=8`、`abyss.success=true`，上下半各四名角色；
- 成功结束后 `abyss.active_half` 可以为 `null`，历史读取不能依赖它决定默认半场；
- 不返回深渊周期、正式场景 ID、上游战斗记录 ID、逐击、绝对时间点或时停区间；
- `quality` 还包含当前 Domain 未完整投影的伤害金额、时停/深渊事件数和服务端修正数，因此持久化
  必须保留完整原始 JSON。

为核对上述协议曾临时导出最终摘要诊断 JSON；字段确认后该临时代码已删除，不作为正式
持久化链。正式保存只写当前账号 SQLite，常规日志不得记录完整 payload。

本次尚未实现：逐击轴、版本化聚合收益画像和综合边际。已有实时 UI 曾由用户真实运行；新增
schema、DAO、持久化、恢复和历史管理 UI 已通过专项、core 和 full 自动化验证，真机历史管理和
非深渊场景继续由维护者验收。

当前应用没有跨功能共享一个 nte-core 进程的会话协调器。第一条代码链由组合根注入客户端
工厂，战报开始时先完整停止背包同步，再启动独占 combat 会话；因此不会并发启动两个抓包
进程。后续若建立应用级 nte-core 会话协调器，Controller 和 Service 的窄协议保持不变，只替换
组合根 Provider。

## 2. 当前本地与 nte-core 基线

### 2.1 已开放并已有本地封装的方法

`src/integrations/nte_core.py` 当前已经提供：

- `start_capture(profile="combat", ...)`：启动战斗抓包；
- `stop_capture()`：停止抓包；
- `get_battle_summary(subtract_time_stop=True)`：取得最近战斗聚合摘要；
- `reset_battle()`：清空 nte-core 当前战斗聚合状态；
- `event.battle.summary`：实时摘要事件；
- `CoalescingEventQueue`：只保留最新战斗摘要，避免高频摘要淹没可靠事件。

这些能力适合实时总览和最终聚合，但当前本地 stdio contract 没有分页获取完整逐击轴的方法。
因此 `event.battle.summary` 只能用于可丢弃的实时展示，不能作为逐击历史或最终战报的权威来源。

### 2.2 2.0.3 已交付的保存和历史读取入口

```text
入口 A：现有 nte-core stdio
  capture.start(combat)
    → event.battle.summary 实时展示
    → capture.stop
    → battle.get_summary 最终聚合
    → 保存账号 SQLite summary_only 自动快照

入口 B：读取账号历史战报
  用户点击“读取历史战报”
    → 按保存时间查询账号内最多 100 条记录
    → 展示角色、深渊层数/未知场景和自动/手动状态
    → 读取原始 summary JSON
    → 使用当前解析器恢复只读页面

未来入口 C：合作方新增 stdio RPC
  battle.get_record + battle.get_axis(cursor)
    → 保存规范化逐击并建立更高可信的统计链
```

入口 A 与 B 使用同一种 `summary_only` 记录；B 不创建新记录。只有未来正式的入口 C 才产生逐击
事实。页面、统计 Service 和角色收益计算只读取记录的 capability 和质量状态。

### 2.3 本地研究样本

维护者本地曾检查一份格式版本 `1` 的逐击 JSON。该文件现存放在被 Git ignore 的
`local_game_data/battle_reports/`，不属于仓库文档或产品输入。

| 项目 | 样本结果 |
| --- | --- |
| 深渊 | 已识别 12 层，上下半各四人 |
| 逐击 | 652 条；648 条 outgoing，4 条 incoming |
| 半场 | 上半 513 条，下半 139 条 |
| GameplayEffect | 114 种 |
| 静态库映射 | 648/648 条 outgoing 均命中 `skill_damage.damage_id` |
| 映射伤害金额 | 100% |
| Ability | 594/652 条有值；缺失项仍可由 GE 映射 |
| 时停 | 10 个开始和 10 个结束事件 |
| 目标 | `target_id`、`target_monster_id` 均为空 |
| 伤害属性 | `damage_attribute` 均为空 |
| 空幕 | `empty_curtain` 和对应角色列表为空 |
| 原始网络包 | 2048 条，仅用于合作方诊断，不进入账号库 |

该样本只能证明合作方调试工具曾经产生过这些字段，并可作为未来上游协议设计和本地分类规则的
研究证据。它不能证明当前 CLI 能导出逐击，不能据此开发用户 JSON 导入功能，也不能证明目标、
怪物、Buff/Debuff 区间和空幕覆盖率已经可用。

## 3. 功能范围

### 3.1 2.0.3 已交付基线

- “战报”一级页面和账号内战报列表；
- 使用现有 nte-core 方法开始、停止战斗记录并展示实时摘要；
- 将 `battle.get_summary` 完整原始 JSON 保存为账号内 `summary_only` 战报；
- 支持深渊与非深渊摘要；深渊显示 floor，不能识别的非深渊显示“未知场景”；
- 账号级最多保留 100 条，手动保存最多 50 条；
- 显式“保存伤害结果”把当前自动记录提升为手动记录；
- 历史列表右侧支持保存/取消保存和删除；
- 进入页面恢复上次保留结果，并通过“读取历史战报”入口切换账号内记录；
- 深渊上下半、角色、技能、GameplayEffect 和聚合伤害渠道统计；
- 角色归属伤害占比、共享/环境/未归因伤害单独记账；
- 数据质量、映射覆盖率和可信等级展示；

### 3.2 2.1.0 后续必须完成

- 从 `summary_only` 生成明确标记来源和可信等级的聚合 `RoleBenefitProfile`；
- 计算单角色分渠道边际和综合边际；
- 生成多角色装备分配所需的实测伤害权重；
- 角色页可以选择一份兼容战报作为只读收益画像来源；
- 所有结果固定账号、generation、战报、静态数据集、分类器版本和计算器版本。

### 3.3 当前明确不做

- 不从 `packets`、`payload_preview` 或 `payload_hex` 自行重放协议；
- 不实时保存每一条 `event.battle.summary`；
- 不把深渊上半和下半合成一个八人角色优先级；
- 不将 `char_id` 上的全部伤害无条件算作角色自身伤害；
- 不把环境、倾陷或共享伤害套入角色自身词条边际；
- 不在没有状态区间时宣称武器或空幕 Buff 为实测覆盖率；
- 不计算辅助角色的完整 Buff 间接贡献；
- 不搜索最佳四人或最佳八人；
- 不自动保存或覆盖现有配装方案；
- 不修改账号基础权重；
- 不在日志记录逐击明细、完整伤害表、角色 UID、原始 payload 或用户绝对路径。

### 3.4 当前摘要能力边界

`summary_only` 可以用于整场/半场总览、角色伤害占比、技能聚合占比、按上游 `category` 展示的
伤害构成，以及名称明确为“实测伤害配装权重”的半场角色权重。它不能用于：

- 逐击伤害轴、任意时间窗口或技能施放顺序；
- 时停区间、DOT 跳伤时间、目标或怪物绑定；
- 武器、空幕或队伍 Buff 的实测覆盖率；
- 判断深渊属于基础或当期，或跨深渊周期区分同一 floor；
- 辅助、减抗、易伤等完整队伍间接贡献；
- 逐击级或状态区间级高可信收益画像。

非深渊摘要没有场景 ID，当前版本统一显示“未知场景”，不宣称能自动区分大世界、普通副本、
Boss 或训练场，也不要求用户在录制前手工选择场景。

## 4. 统计和收益口径

### 4.1 伤害来源主键

跨层关系不使用中文 `attack_type` 或角色显示名。伤害来源按以下稳定字段组合识别：

```text
DamageSourceKey
  = gameplay_effect_name
  + ability_name（可空）
  + damage_component（可空）
```

分类优先级：

1. `gameplay_effect_name` 命中发行静态库 `skill_damage.damage_id`；
2. `ability_name` 与静态库 `ability_id` 交叉校验；
3. `damage_component` 用于区分同一技能下的追加、被动或专属组件；
4. `attack_type` 只作为上游分类证据和中文展示回退；
5. 无法确认时保留 `unknown`，不能从中文名称猜官方关系。

`gameplay_effect_index` 可以保存为本场诊断字段，但不能作为跨战报稳定主键。

### 4.2 伤害渠道

首期内部渠道至少包括：

- `direct_normal`：普攻及其直接伤害；
- `direct_skill`：E 技能直接伤害；
- `direct_ultimate`：Q 技能直接伤害；
- `direct_special`：闪避反击、格挡反击、觉醒、被动和角色特殊直伤；
- `dot`：DOT 单跳和结算；
- `reaction_creation`：创生和创生花；
- `reaction_burning`：浊燃；
- `reaction_weave`、`reaction_dark_star`、`reaction_infusion`；
- `reaction_other`：其他已经确认的环合；
- `topple`：倾陷伤害；
- `environment`：深渊场地、关卡卡牌等环境来源；
- `shared`：共享机制；
- `unknown`：未确认来源。

页面可以再按普攻、E、Q、被动等动作类型聚合，但动作类型和公式渠道是两个字段，不能混为
同一个枚举。

### 4.3 伤害所有者

每个伤害来源必须分类为：

```text
character
reaction_owner
shared
environment
unattributed
```

样本存在两个必须作为验收用例的边界：

- 上半四名角色的 `party.share_percent` 合计为 `99.716673%`；剩余 `0.283327%` 是 6365 点
  倾陷伤害。该命中虽然带有娜娜莉 `char_id`，但 nte-core 角色聚合没有把它计入娜娜莉；
- 九原逐击中有 222057 点“深渊场地Buff”伤害，占其逐击汇总约 60.89%。它在确认公式归属前
  必须归入 `environment`，不能默认受九原自身装备词条影响。

因此战报角色统计不能只执行 `GROUP BY char_id`。页面必须同时显示角色归属总计和共享、环境、
倾陷、未归因桶，并提供与上游半场总伤害的对账差异。

### 4.4 角色内技能和渠道占比

```text
角色可归属伤害 D_role
  = Σ owner_kind 为 character 或明确 reaction_owner 的伤害

技能占比 P_skill
  = 对应 DamageSourceKey 的可归属伤害 / D_role

渠道占比 P_channel
  = 对应渠道的可归属伤害 / D_role
```

每个占比同时保存分子、分母、命中数、战报 ID、半场、分类器版本和质量标记。不要只保存一个
无法追溯的百分比。

样本可得到的典型结果：

- 「零」约 88.86% 为创生花渠道；
- 浔约 95.73% 为 Q 技能渠道；
- 安魂曲约 46.34% 为特殊伤害；
- 娜娜莉伤害分布在普攻、Q、觉醒、闪避反击等多个渠道。

这些值只描述该次实战轮转，不表示角色的永恒默认比例。

### 4.5 单角色分渠道和综合边际

对属性 `a`、角色 `r`、伤害来源 `s`：

```text
来源相对边际 m(r,s,a)
  = Damage_after(r,s,a) / Damage_before(r,s) - 1

角色综合边际 M(r,a)
  = Σ [P_skill(r,s) × m(r,s,a)]
```

计算时按每个 GE 的静态公式分别处理：

- `skill_damage` 提供技能的攻击、生命、防御倍率和伤害来源分类；
- `combat_level_curve` 提供创生、浊燃、黯星和倾陷等级曲线；
- `DamageCalculationService` 继续计算单次直伤、DOT、倾陷和环合纯公式；
- 战报 Service 只负责以实测伤害占比组合这些来源边际。

一个属性不影响某来源时，该来源边际为 0。环合强度只进入明确受环合强度影响的环合来源；
反应不能暴击时，其暴击率和暴击伤害边际为 0。

战报中的渠道占比已经包含该次轮转的实际命中次数和实际伤害，不再额外乘同一渠道的“出现
覆盖率”。武器、空幕和状态 Buff 的覆盖率是另一层效果覆盖率，只有取得状态区间或明确规则后
才能进入受影响来源的前后伤害计算。

### 4.6 半场角色伤害权重和多角色配装收益

上半、下半分别计算，不能使用顶层八人汇总占比：

```text
角色半场伤害权重 W_role
  = D_role / D_half_total

角色某候选配装的队伍加权收益
  = W_role × 该候选相对当前配装的角色综合收益
```

`W_role` 使用半场总伤害作为分母，因此存在共享、环境或未归因伤害时，四名角色权重之和允许
小于 1。界面必须显示剩余桶，不能强行把四名角色归一化到 100% 后隐藏差异。

单角色优化时，`W_role` 对同一个角色的所有候选都是常数，不参与角色内部候选排序；多个角色
争夺同一真实装备 UID 时，外层分配器才使用队伍加权收益比较候选。这样单角色优化仍与队伍
模块解耦。

该权重的首期名称固定为“实测伤害配装权重”，不能称为“角色队伍贡献”。辅助、减抗、增益和
环合协同的反事实贡献需要后续队伍模型或 Shapley 分摊。

## 5. 不可变 contract

### 5.1 `BattleRecord`

当前 `summary_only` 至少包含：

- 本地 `battle_record_id`；
- `source_kind=nte_core_summary`、`capability_level=summary_only`；
- 本地 `capture_operation_id`，用于防止同一次结束回调重复提交；
- `combat_context_kind=abyss|non_abyss`；
- 可空的 `abyss_floor`；当前版本不保存、不推断基础/当期类型；
- 开始采集和收到最终摘要的本地 UTC 时间；
- 是否扣除时停及其时长口径；
- 总命中、总伤害、DPS、承伤、角色数、技能数、深渊成功状态；
- 本地 payload schema version、完整 `raw_summary_json` 和 SHA-256；
- 账号 ID 和开始操作时的 generation；账号数据库绝对路径只冻结在 operation dependencies 中，
  不写入战报；
- 当前 nte-core 没有的上游 record ID、正式场景 ID和绝对战斗时间必须保持未知，不得用本地值
  冒充。

未来正式 axis RPC 收敛为 `source_kind=nte_core_axis` 的 `BattleRecord`，只有该正式 capability
可以带 `axis_complete|axis_incomplete`、上游 record ID、逐击和时停事实。开发者本地研究 JSON
不进入产品数据库 contract。

`BattleRecord` 一旦事务提交就不修改原始事实。自动/手动状态属于独立 retention 元数据；重新
分类或重算收益产生新 Projection，不覆盖原始 JSON 或逐击事实。

### 5.2 `BattleSegment`

当前 `summary_only` 不建立空的 `battle_segment` 表；页面从原始 JSON 的 `first_half`、
`second_half` 构造只读内存投影。以下 contract 只适用于未来正式 axis RPC：

- `segment_id`；
- `battle_record_id`；
- `half=first|second|normal`；
- 开始、结束和有效时长；
- 上游命中、输出伤害、承伤和 DPS；
- 四名参与角色及上游角色聚合；
- 时停区间引用；
- 半场完整性和对账结果。

### 5.3 `BattleHit`

当前 `summary_only` 不包含、也不生成 `BattleHit`。以下 contract 只适用于未来正式 axis RPC：

- `segment_id` 和导入顺序 `sequence`；
- 统一时间戳与相对半场时间；
- `direction`；
- 官方 `character_id`，未知时为 `null`；
- 伤害数值；
- `gameplay_effect_name`、`ability_name`、`damage_component`；
- 上游 `attack_type` 和 `gameplay_effect_index`；
- 目标和伤害属性字段，当前允许 `null`；
- 追加伤害字段；
- 上游原始归因/质量字段，当前没有时明确 unknown。

正式协议必须提供稳定 sequence 或明确的数组顺序语义。时间戳可以相同，不能以时间戳作为
唯一键。

### 5.4 `DamageSourceProjection`

按 `BattleRecord + DamageSourceKey + classifier_version` 保存：

- 静态库技能记录 ID；
- 官方 Ability 交叉校验结果；
- 伤害渠道；
- `owner_kind`；
- 适用的角色属性和专属增伤范围；
- 公式来源和可信等级；
- 未确认原因。

分类错误修复时生成新版本 Projection，原始 `BattleHit` 不变。

### 5.5 `BattleRoleProjection`

保存或返回：

- 战报、半场和角色；
- 角色可归属伤害及半场伤害权重；
- 技能、渠道、动作类型明细；
- 共享、环境、倾陷和未知对账桶；
- 数据质量；
- `RoleBenefitProfile`；
- 分渠道边际和综合边际；
- 静态数据集、角色养成指纹、分类器和计算器版本。

角色养成与战报无法证明完全一致时，渠道占比仍可标记为实测，但边际结果必须标记为
`observed_share_with_planned_build`，不得把计划养成伪装成战斗时实测养成。

## 6. 账号 SQLite v13 结构

战报属于当前账号数据。当前已新增用户数据库迁移 `v13`，不能写入发行静态库或本机共享库。

### 6.1 当前摘要事实表 `battle_record`

`battle_record` 保存完整原始 JSON 和列表需要的最少索引字段：

| 字段组 | 字段 |
| --- | --- |
| 标识 | `battle_record_id`、`capture_operation_id UNIQUE` |
| 来源 | `source_kind=nte_core_summary`、`capability_level=summary_only` |
| 上下文 | `combat_context_kind`、`abyss_floor`、上下半可用性 |
| 时间 | `captured_at_utc`、`finalized_at_utc`、`created_at_utc` |
| 汇总索引 | `dps_time_mode`、时长、总伤害、DPS、承伤、命中、角色数、技能数、深渊识别/成功 |
| 权威 payload | `payload_schema_version`、语义完整的 `raw_summary_json`、规范化 JSON 的 `raw_summary_sha256` |

当前上下文投影规则：

```text
abyss.detected = true
  → combat_context_kind = abyss
  → abyss_floor = 上游 floor

abyss.detected = false
  → combat_context_kind = non_abyss
  → abyss_floor = null
  → 展示“未知场景”
```

当前摘要没有可靠的基础/当期标识，本期不增加 `abyss_variant` 字段，也不根据 floor 或本地静态
库推断类型。所有深渊记录统一显示“深渊 · 第 N 层”；非深渊显示“未知场景”。以后上游提供
稳定类型标识时再通过新迁移和兼容投影扩展，不阻塞当前持久化实现。

### 6.2 保留元数据 `battle_record_retention`

保留状态与不可变事实分离：

- `battle_record_id` 一对一外键；
- `retention_kind=auto|manual`；
- `auto_saved_at_utc`；
- `manual_saved_at_utc`，自动记录为 `null`；
- `updated_at_utc`。

点击“保存伤害结果”是把同一记录从 `auto` 提升为 `manual`，不复制 `battle_record`。手动 FIFO
按点击保存时间排序；已经是 manual 时幂等返回且不刷新排序时间。历史列表允许把 manual 取消
保存为 auto，也允许用户显式删除任意记录。

账号级保留上限固定为：

- `battle_record` 总数最多 100；
- manual 最多 50；
- 新增第 101 条时删除最旧 auto；manual 不参与总量淘汰；
- 保存第 51 条 manual 时删除最旧 manual；
- manual 上限保证总量达到 100 时一定存在可淘汰的 auto；
- 自动淘汰不弹窗，用户显式删除应确认。

### 6.3 页面恢复状态 `battle_report_page_state`

账号内单例保存：

- `last_battle_record_id`，被 FIFO 删除时 `ON DELETE SET NULL`；
- `last_detail_scope=current|first|second`；
- `updated_at_utc`。

进入页面依次恢复：仍存在的上次记录 → 全账号最新记录 → 空状态。成功结束后 `active_half` 可以
为 `null`；此时有两个半场且没有历史选择时默认显示上半。

### 6.4 FIFO 事务

结束生成有效摘要时，在一个 `BEGIN IMMEDIATE` 事务内：

1. 插入不可变 `battle_record` 和 `auto` retention；
2. 如果账号总数超过 100，删除最旧 auto，直到恢复上限；
3. 更新页面状态为新记录；
4. 提交。

手动保存时，在一个事务内：

1. 将当前 retention 提升为 manual；
2. 如果 manual 超过 50，删除最旧 manual，直到恢复上限；
3. 保持页面指向当前记录；
4. 提交。

删除 `battle_record` 时级联删除 retention；排序使用 UTC 时间加 `battle_record_id` 打破同毫秒
并列。只有最终 payload 存在、格式有效且 `total_damage > 0` 或 `total_hits > 0` 时自动入库。

### 6.5 后续逐击扩展

`battle_segment`、`battle_participant`、`battle_hit`、`battle_time_stop_interval` 和逐击质量问题
推迟到合作方正式 axis RPC 交付后。它们以 `battle_record_id` 关联当前记录头，不能为了统一
表形在 `summary_only` 记录下生成伪逐击或空事实。

## 7. 主要方法和依赖方向

### 7.1 Integration

建议新增：

- `NteCoreBattleSummaryProvider`：包装现有 `start_capture`、事件、summary、stop 和 reset，同时
  返回完整原始 payload 与校验后的 Domain 摘要；
- `NteCoreBattleAxisProvider`：未来 capability 可用时分页取得 record/axis。

Integration 只解析外部格式，不写 SQLite、不计算占比、不引用 Qt。

### 7.2 Domain

建议新增 `src/domain/battle_report.py`：

- `BattleRecord`、`BattleSegment`、`BattleHit`；
- `DamageSourceKey`、`DamageChannel`、`DamageOwnerKind`；
- `DamageSourceProjection`、`BattleReportQuality`；
- `SkillDamageShare`、`ChannelDamageShare`、`RoleDamageShare`；
- `BattleRoleProjection`。

现有长期计划中的 `RoleBenefitProfile` 保持独立公开 contract，可放在
`src/domain/role_benefit.py`，不能由 `features/battle_report` 定义。

### 7.3 DAO

已新增 `BattleReportDaoMixin` 并组合进账号 `UserDataDao`，独占：

- `insert_auto_summary_snapshot(...)`：写入原始摘要、执行全局 100 条上限并更新页面状态；
- `promote_battle_record_to_manual(record_id)`：提升同一记录、执行 manual 50 条上限；
- `unmark_manual_battle_record(record_id)`：取消保存并使记录重新参与 auto 淘汰；
- `delete_battle_record(record_id)`：用户明确删除；
- `list_battle_records(...)`、`load_battle_record(record_id)`；
- `battle_report_page_state()`、`update_battle_report_page_state(...)`、
  `restore_battle_report_record()`；
- `import_axis_battle_record(record)`、`load_battle_hits(...)`：未来正式 axis RPC 使用；
- `save_source_projection(...)`；
- `save_role_projection(...)`；
- `select_role_profile(...)`；
- `delete_battle_record(...)`：检查画像引用后删除。

页面和 Service 不拼 SQL。未来 axis 提交失败必须整场回滚，不能提交半场或部分 hits。

### 7.4 Application Service

当前已新增或后续计划新增：

- `BattleCaptureService.start/finalize/cancel(...)`：只管理 nte-core 会话和最终 payload；
- `BattleReportPersistenceService.finalize_summary(...)`：校验账号/generation、战斗上下文投影和自动保存；
- `BattleReportHistoryService.save_record/unmark_record/delete_record(...)`；
- `BattleReportHistoryService.restore_last_summary/list_records/load_summary(...)`；
- `BattleReportValidationService.validate(record)`；
- `DamageSourceClassificationService.classify(record, static_catalog)`；
- `BattleReportProjectionService.project(record, classification)`；
- `BattleRoleBenefitService.build_profile(role_projection, build_snapshot)`；
- `BattleRoleMarginService.calculate(profile, build_snapshot, scenario)`。

聚合画像的关键验证顺序：

```text
最终摘要格式和版本
  → 整场与半场聚合
  → 角色/技能总伤害对账
  → GameplayEffect 静态映射
  → 归属和渠道分类
  → 角色/技能/渠道占比
  → 收益画像和边际
```

统计 Service 不读取“当前角色页”。调用方必须显式传入冻结的战报、角色养成、静态数据集、
战斗上下文和计算器版本。

### 7.5 Controller 和页面

建议新增 `src/features/battle_report`：

- `dependencies.py`：冻结账号 ID、数据库路径、generation、静态库和公开 Service；
- `controller.py`：只拥有战斗记录 worker、历史读取状态、取消和忙碌状态；未来 axis 接入仍通过
  公开 Service，不在页面解析协议；
- `page.py`：一级战报页面；
- `record_list.py`、`overview.py`、`role_detail.py`、`quality_view.py`：按展示状态拆分。

应用组合根 `src.ui.app` 创建并注入 nte-core 客户端工厂或未来的共享窄 Provider。战报
Controller 不拼 SQL、不解析原始 JSON、不执行 FIFO，也不调用角色页面私有方法。当前过渡实现
通过组合根注入的公开回调暂停/恢复背包同步，再由工厂建立独占 combat 会话；同一时刻只保留
一个抓包进程。

## 8. 页面信息结构

### 8.1 顶部操作

- “开始采集”：检查当前抓包 owner，实时事件只更新页面；
- “结束并生成战报”：停止抓包、读取最终 summary，并自动写入当前账号的战报历史；
- “保存伤害结果”：把当前自动记录提升到手动池，已是手动记录时禁用或幂等；
- “读取历史战报”：打开当前账号 SQLite 历史选择器，不创建新记录；
- 时停口径显示：默认沿用 `subtract_time_stop=True`，写入记录配置。

`battle.reset` 会清空 nte-core 当前战斗状态。正式持久化必须在 reset 之前完成，不能在页面打开
或读取历史时自动调用。

### 8.2 战报列表

显示：

- 角色头像：普通摘要最多四名；深渊摘要按上下半展示最多八名；
- 保存时间；当前上游没有绝对战斗时间，不能把它标成权威战斗时间；
- 深渊统一显示“深渊 · 第 N 层”，非深渊显示“未知场景”；本期不判断基础/当期；
- 自动/手动标记和上下半可用性；
- 总伤害、DPS、有效时长；
- 当前 `summary_only` capability；未来正式逐击 RPC 接入后再显示 axis 等级；
- 数据质量和来源；
- 后续 axis 记录是否已经被角色收益画像选用。

每行右侧提供“查看”“保存/取消保存”“删除”。选择记录后复用当前总览、角色、技能和伤害
构成区域进行只读展示。列表最多 100 条，manual 最多 50 条；自动淘汰不产生隐藏记录。若未来
收益画像引用某条记录，必须先定义引用与 FIFO 的冲突策略，不能直接沿用当前摘要轮转规则。

### 8.3 半场总览

- 上半/下半切换；
- 半场总伤害、有效时长、DPS、时停；
- 四名角色实测伤害占比；
- 共享、环境、倾陷和未归因桶；
- 上游聚合与逐击重新汇总的差异；
- 分类覆盖率和自动使用门槛。

### 8.4 角色详情

- 角色归属伤害和半场伤害权重；
- 按技能/GE 的伤害、占比、命中数和平均伤害；
- 按直伤、DOT、具体环合等渠道的占比；
- 分渠道边际；
- 综合边际和只读最终权重；
- 数据来源、角色养成匹配状态和可信等级；
- “设为角色收益画像”操作，只保存引用和版本化投影，不修改账号基础权重。

### 8.5 数据质量

- 上游格式版本和 capability；
- 总命中、逐击数量和半场数量对账；
- GE、Ability、目标、属性、空幕和状态区间完整率；
- 共享、环境和未知伤害比例；
- 时间戳逆序、重复时间戳和区间异常；
- 阻止自动综合边际或配装的具体原因。

## 9. 生命周期与并发

开始记录时冻结：

- 账号 ID 和账号数据库绝对路径；
- `AppContext.generation`；
- operation token；
- 静态数据集 ID；
- nte-core capability/协议版本；
- 是否扣除时停；

回调落地前重新核对 token、generation 和账号路径。旧结果静默丢弃，不得写入新账号。

运行规则：

- 同一账号同时只有一个战斗记录 operation；
- 使用组合根注入的 nte-core 窄 Provider；当前先停止背包同步再启动独占 combat 会话，不并发
  启动第二个客户端抢占抓包；
- 抓包 owner 冲突时由公开协调边界拒绝开始，不调用其他 Controller；
- 活动战斗记录阻止账号切换，或先走公开取消并等待安全停止；
- `event.battle.summary` 只更新页面最新值，旧 generation 事件丢弃；
- 应用退出先停止当前战斗记录，再关闭共享 nte-core 客户端；

## 10. 数据质量和可信等级

在现有 T0～T3 基础上增加战报能力门禁：

| 状态 | 条件 | 允许用途 |
| --- | --- | --- |
| `summary_only` | 只有 nte-core 聚合摘要 | 战报总览、角色/技能/渠道聚合占比和低等级画像 |
| `axis_incomplete` | 有部分逐击或对账失败 | 诊断和手工查看，不生成自动画像 |
| `axis_complete` | 逐击、半场、角色和总伤害对账通过 | 技能/渠道占比、实测伤害权重 |
| `profile_hybrid` | 完整逐击，但养成来自本地计划配置 | 综合边际预览，标记混合来源 |
| `profile_observed` | 完整逐击并绑定战斗时角色/武器快照 | 可用于更高可信的配装排序 |
| `status_complete` | 再包含 Buff/Debuff 状态区间 | 可计算实测覆盖率和队伍间接贡献 |

建议自动生成收益画像的最低门槛：

- outgoing 伤害金额对账误差不超过版本化容差；
- 角色和半场可识别；
- 已分类可归属伤害达到配置门槛；
- 环合等特殊渠道具有明确映射；
- 不存在阻止性时间或格式错误。

具体百分比门槛在实现 contract 前由用户确认，不能写死为无来源的经验值。

## 11. 分阶段实施

### BR0：冻结 contract 和摘要口径

- 固定本文 contract、公式、owner/channel 枚举和质量状态；
- 记录本地研究样本的已知字段，但不把它当作产品入口或正式协议；
- 固定 `summary_only` 与未来 axis 的能力边界；
- 固定倾陷剩余桶和深渊场地伤害边界。

完成标准：文档、样本期望和公开行为明确，不修改旧角色页评分语义。

### BR1：账号 schema v13 与摘要快照

本阶段已在 2.0.3 交付。

- 新增迁移和 `BattleReportDao`；
- 保存 `battle.get_summary` 完整原始 JSON 和最少索引字段；
- 实现通用战斗上下文、`capture_operation_id` 幂等和 payload SHA-256；
- 实现账号级总数 100、manual 50 的事务 FIFO；
- 实现上次页面状态、历史列表和记录只读查询。

完成标准：新库创建、v12 升级、失败回滚、账号隔离、100/50 边界和自动提升行为明确。

### BR2：现有 nte-core 摘要持久化和历史入口

本阶段已在 2.0.3 交付。

- 结束时读取最终 payload，旧账号/generation 结果不落库；
- 页面增加“保存伤害结果”和“读取历史战报”；
- 进入页面恢复上次记录和明细范围；
- 历史记录复用当前总览、上下半、角色、技能、构成和质量展示；
- summary_only 只显示当前聚合能力，不出现逐击、覆盖率或完整队伍贡献文案。

完成标准：结束自动入库不依赖最后一次 UI 事件；历史切换不启动 nte-core；账号切换后不显示旧
账号记录。

### BR3：聚合伤害画像和来源分类

- 从最终聚合摘要建立 GE/技能 → 静态技能 → 渠道/owner 投影；
- 计算角色、技能、直伤、特殊伤害和具体环合的聚合占比；
- 单独记录共享、环境、倾陷和未归因桶；
- 生成明确标记 `summary_only` 的版本化 `RoleBenefitProfile`；
- 缺少逐击、状态区间或养成观测时保持低可信等级。

完成标准：整场、半场、角色、技能和渠道聚合可以对账；同一输入与分类器版本重复结果一致；
未知来源不进入角色自身词条边际。

### BR4：战报收益画像和单角色综合边际

- 从 `summary_only` 聚合画像生成低等级综合边际；
- 按 GE 分别计算属性边际，再按聚合技能伤害占比加权；
- 角色页显示直伤边际和战报综合边际，不覆盖旧字段；
- 允许用户选择或解除当前角色的战报画像；
- 动态综合权重只读，不写账号基础权重。

完成标准：环合强度只进入适用来源；占比之和、边际分解和综合结果可对账；同一输入结果一致。

### BR5：聚合伤害权重接入单角色配装和外层分配

- 单角色优化器消费收益画像生成 Top-K；
- 单角色候选内部不乘角色半场权重；
- 外层分配装备 UID 冲突时使用队伍加权收益；
- 固定背包快照、配装锁、画像、战报和计算器版本；
- 只生成 Preview，用户确认后才保存。

完成标准：锁定方案不变、装备 UID 唯一、旧 Preview 不补读当前状态、动态权重不污染基础权重。

### BR6：合作方完整逐击 RPC 接入

- capability 检测 `battle_record_v1`、`battle_axis_v1`；
- 分页读取并校验 sequence、数量、总伤害和 final 状态；
- 建立规范化逐击并复用现有渠道和画像投影链；
- 保存上游稳定 record ID 和关联快照；

完成标准：停止抓包后能够可靠取得最终页；逐击与上游摘要对账；缺页和不完整状态不会生成
高可信画像。

BR0～BR2 已作为 2.0.3 战报基线交付；BR3～BR5 在 2.1.0 基于当前聚合摘要完成低等级收益与
配装闭环；BR6 随合作方正式逐击接口交付升级事实质量，不依赖个人本地 JSON 文件。

## 12. 队伍模块的后续边界

战报首期已有的队伍信息仅是：

- 某次真实深渊上半/下半的四名参与者；
- 每名角色的归属伤害、渠道占比和实测伤害权重；
- 共享、环境和未知伤害；
- 该次轮转和敌人的观测结果。

后续独立队伍模块仍负责：

- 手工或自动创建候选四人队；
- 改变队员后的反事实伤害模拟；
- Buff、减抗、易伤和触发协同的间接贡献；
- Shapley 等可加总队伍贡献；
- 最佳四人和深渊角色不重复的最佳八人搜索。

战报页面不得演变成队伍求解器。它只生产 `ObservedTeamSnapshot`、`BattleRoleProjection` 和
`RoleBenefitProfile`；队伍模块以后消费这些公开 contract，不反向访问战报页面或 Controller。

## 13. 文件规划

| 层 | 计划位置 | 职责 |
| --- | --- | --- |
| Domain | `src/domain/battle_report.py` | 不可变战报、命中、分类、统计值对象 |
| Domain | `src/domain/role_benefit.py` | 跨战报/角色/配装共享的收益画像 |
| Integration | `src/integrations/nte_core_battle.py` | 现有摘要与未来 axis Provider |
| DAO | `src/storage/sqlite/battle_report_dao.py` | 摘要事实、retention、页面状态；后续逐击与投影 |
| Schema | `src/storage/sqlite/schema/014_user_data_v13.sql` | 账号战报迁移 |
| Service | `src/services/battle_report_persistence_service.py` | 最终摘要、上下文投影、100/50 FIFO |
| Service | `src/services/battle_report_history_service.py` | 上次恢复、历史列表、详情加载和保留状态管理 |
| Service | `src/services/battle_report_projection_service.py` | 分类、对账和统计 |
| Service | `src/services/battle_role_benefit_service.py` | 收益画像、分渠道和综合边际 |
| Controller/UI | `src/features/battle_report/page.py`、`history_dialog.py`、`controller.py` | worker 生命周期、实时/历史复用展示和右侧管理操作 |
| Composition | `src/features/battle_report/dependencies.py`、`src/ui/app.py` | 由应用组合根调用窄工厂，显式注入 nte-core、持久化和历史 Service |

具体实现时按 800 行规则拆分，不将外部协议、统计、UI 和生命周期压入单文件。

## 14. 最小验证矩阵

代码阶段按 AGENTS.md 要求等待用户明确允许后再执行验证。需要准备的公共行为包括：

| 范围 | 必须证明 |
| --- | --- |
| 摘要存储 | 原始 JSON 字段和值完整保留、索引字段一致、operation 幂等、空摘要不提交 |
| 轮转 | 总数 100、manual 50；第 101 条淘汰最旧 auto，第 51 条 manual 淘汰最旧 manual |
| 历史读取 | 恢复上次记录、被淘汰后的全局最新回退、上下半选择恢复 |
| 半场 | 上下半角色、技能和总计聚合对账 |
| 静态映射 | 摘要中的 GE 映射覆盖率和缺失原因明确 |
| 未来 axis | sequence、缺页、事务回滚、逐击与聚合对账；上游交付前不伪造测试输入 |
| 归属 | 倾陷剩余桶、深渊场地伤害和角色伤害不混算 |
| 统计 | 技能/渠道分子分母、占比和总伤害一致 |
| 边际 | 不适用渠道为 0，综合边际等于分渠道加权和 |
| 权重 | 上下半独立；共享存在时角色权重允许小于 1 |
| nte-core | 摘要合并、final summary、reset 确认和 owner 冲突 |
| 账号 | generation/path/token 冻结，旧回调不落新账号 |
| 角色页 | 只读综合权重不覆盖基础权重和旧直伤字段 |
| 配装 | 原 Preview、快照、画像、锁和 UID 契约不变 |
| 日志 | 只记录数量、版本、阶段、质量和安全错误码 |

## 15. 当前状态

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| BR0 contract 与摘要口径 | 2.0.3 已交付 | 本地样本仅作研究证据，不是产品入口 |
| BR1 schema 与摘要快照 | 2.0.3 已交付 | v13、原始 JSON、幂等、100/50 retention 和页面状态 |
| BR2 摘要持久化与历史入口 | 2.0.3 已交付 | 自动入库、账号防护、恢复和历史管理；真机继续验收 |
| BR3 聚合伤害画像 | 未开始 | 基于当前 summary 的角色、技能和渠道占比 |
| BR4 收益画像与综合边际 | 未开始 | 使用聚合占比和本地公式，明确低可信等级 |
| BR5 配装接入 | 未开始 | 单角色优化、外层 UID 分配 |
| BR6 完整逐击 RPC | 等待上游 | 合作方交付后接入，不阻塞 BR0～BR5 |
| 独立队伍搜索 | 暂缓 | 战报闭环稳定后继续长期计划 |

## 16. 变更记录

| 文档版本 | 日期 | 变更 |
| --- | --- | --- |
| 0.1.0 | 2026-08-04 | 初始版本；确定战报优先、现有 nte-core 双入口、样本口径、账号 schema v13、页面、收益画像和分阶段开发方案 |
| 0.2.1 | 2026-08-04 | 记录实时摘要页面、悬浮窗、上下半范围和伤害构成卡的开发进度 |
| 0.3.0 | 2026-08-07 | 按当前协议冻结 summary-only 原始 JSON、floor 临时场景键、每场景 3 自动 + 3 手动、上次恢复和“读取历史战报”入口；完整 JSON 导入继续保持独立 |
| 0.4.0 | 2026-08-07 | 战报扩展为深渊和非深渊通用快照；取消按场景 3+3，改为账号总数 100、manual 50；历史列表显示角色、保存时间、深渊类型/层数或未知场景，并提供右侧管理操作 |
| 0.4.1 | 2026-08-07 | 当前协议无法可靠判断基础/当期，本期移除 `abyss_variant` 及 floor 推断；深渊只保存层数并统一展示，类型识别留待上游提供稳定标识后扩展 |
| 0.5.0 | 2026-08-07 | 实现账号 SQLite v13、战报 DAO、100/50 FIFO、最终摘要自动保存、账号/generation 防护和上次战报恢复；手动保存与历史列表 UI 继续开发 |
| 0.6.0 | 2026-08-07 | 接入顶部手动保存和历史入口；历史弹窗按角色、时间、深渊层数/未知场景展示，并支持查看、保存/取消保存、确认删除及淘汰后页面回退 |
| 0.7.0 | 2026-08-07 | 更正逐击 JSON 边界：个人样本移出仓库并仅作研究证据；取消当前产品 JSON 导入计划，BR3 改为基于现有 CLI 摘要的聚合伤害画像，完整逐击等待正式 axis RPC |
| 0.8.0 | 2026-08-07 | 将战报页面、悬浮窗、SQLite v13 和历史管理收口为 2.0.3；2.1.0 从 BR3 聚合收益画像继续开发 |
