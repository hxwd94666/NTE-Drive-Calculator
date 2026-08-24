# 伤害类型反事实审计

本文档用于确定一个伤害项是直伤、持续伤害、追加攻击或其他特殊类型。“在一段时间内多次发生”
不能自动等同于“持续伤害”。

## 审计顺序

1. 先检查官方 GE/Ability 字段与 Gameplay Tag。持续伤害的强证据为 `State.Damage.Dot` 或子标签。
2. 再检查官方技能说明、GE 周期与状态对象，确定该标签对应的具体伤害名称。
3. 最后由用户确认实际游戏语义。未确认项不进入「可以吃吗？」的持续伤害种类计数。

`DT_SkillDamageData.DamageTypeEX` 表示咒/暗/光等伤害属性，不表示是否为持续伤害。`DurationPolicy` 或
`Period` 只能证明 GE 持续/周期执行，也不能单独作为伤害类型结论。

## 已确认的持续伤害种类

| 审计 ID | 伤害/状态 | 官方资产 | 官方字段 | 人工结论 |
| --- | --- | --- | --- | --- |
| `DAMAGE-TYPE-scorch` | 浊燃 | `Buff_Reaction_5_new` | `State.Damage.Dot` | 是持续伤害；浊燃自身计为一种 |
| `DAMAGE-TYPE-nightmare` | 噩梦 | `GE_Player_Lacrimosa_Blood_Damage` / `LV6` | `State.Damage.Dot.Blood` | 是持续伤害 |
| `DAMAGE-TYPE-zankou-heart` | 蚀心 | `GE_Player_Zankou_DotDamage` | `State.Damage.Dot` | 是持续伤害 |
| `DAMAGE-TYPE-zankou-fire` | 鸩火 | `GE_Player_Zankou_DotUltraDamage` | `State.Damage.Dot` | 是持续伤害；与蚀心是不同状态种类 |
| `DAMAGE-TYPE-cang-q` | 白藏「判予秋」领域伤害 | `GE_Player_Cang_UltraSkill_Damage` | `State.Damage.Dot`；20 秒；周期 1 秒 | 是持续伤害 |
| `DAMAGE-TYPE-adler-e` | 阿德勒「诛恶护持」后续伤害 | `GE_Player_Adler_Skill_Damage` | `State.Damage.Dot`；周期 GE | 是持续伤害 |

同一种持续伤害的层数、刷新或多次周期结算不增加“种类数”。

## 已确认的非持续伤害反例

| 审计 ID | 伤害/状态 | 官方资产 | 直接证据 | 人工结论 |
| --- | --- | --- | --- | --- |
| `DAMAGE-TYPE-kuhara-attachment` | 九原「致命玫约」与「致约清算」 | `GE_Player_Kuhara_Seed_Damage`、`GE_Player_Kuhara_BudBoom_Damage`、`GE_Player_Kuhara_BudEnd_Damage` | `State.Damage.Attachment`；名词说明明确称“附着物”和“附着类灵属性异能伤害” | 是附着物/附着类伤害，不是持续伤害，不进入持续伤害种类计数 |

持续存在、延迟成熟或超时自动结算均不足以判为持续伤害。九原「致命玫约」会存在一段时间并持续吞噬目标
生命力，但其正式伤害类型标签是 `State.Damage.Attachment`，正是不能按表现形态猜 DOT 的反例。
`GE_Player_Kuhara_SeedReaction_Damage` 是玫约状态触发的 15% 追加直伤，但自身只有普通伤害和九原能力标签，
没有 `State.Damage.Dot` 或 `State.Damage.Attachment`，因此不与玫约本体或致约清算合并。

`GE_Player_Mismo_UltraSkill_Damage` 也带 `State.Damage.Dot`，但当前发行角色/技能目录不存在对应正式角色，
且资产内复用白藏曲线和音效；本期不进入正式伤害类型覆盖率。`Buff_Reaction_5_new_1003_old` 同样不作为新类型。
