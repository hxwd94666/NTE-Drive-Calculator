# 数据覆盖、映射与状态时序

## 已导入的官方数据

| 数据 | 官方来源 | 当前解释 |
| --- | --- | --- |
| 倾陷等级乘区 | `DT_GlobalCommonData/UnbaldamagePara` | 1–80 级确切曲线；80 级为 3603 |
| 创生伤害 | `DT_ReactionDamageData/GE_ActorReaction_1_Damage` | 16 个官方档位；末档为 9000 |
| 浊燃伤害 | `DT_ReactionDamageData/Buff_Reaction_5_new`、`Buff_Reaction_5_new_1036` | 普通与残虹专属记录各有 16 个官方档位；末档均为 2700 |
| 黯星伤害 | `DT_ReactionDamageData/Buff_Reaction_4_new` | 16 个官方档位；末档为 45000 |
| 技能倍率 | `DT_SkillDamageData` | 按 GE 保存攻击、生命、防御倍率官方档位 |
| 环合常量 | `DT_ReactionEffectFigure` | 保存官方单点曲线值及已确认单位 |
| 敌方参数 | `DT_MonsterPackData*` | 标准/轨外属性包的防御、抗性和 `UnbalMax` |

角色 GE 的继承标签从 `InheritableAssetTags` 与 `InheritableGameplayEffectTags` 的字符串容器规范化导入；
`State.Damage.Dot` 是 DOT 身份的正式静态证据，不以手工伤害渠道名单替代。残虹蚀心/鸩火 GE 若在 Core
逐击中被复用，而正式伤害名明确为浊燃结算，则浊燃身份优先，原 GE 只保留为来源/触发证据。

九原只对四个正式 GE 使用独立自然伤害公式，不泛化全部 attachment，也不链接某条触发直伤：

- `GE_Player_Kuhara_Seed_Damage`：NATURE、Atk 缩放、character crit，末档倍率 `0.378`；二觉分支再乘 `2`；
- `GE_Player_Kuhara_BudBoom_Damage`：NATURE、Atk 缩放、character crit，末档倍率 `2.518`；正式 Q 动作
  窗口额外消费 `Kuhara_BudBoom_CoefAddUltraSkill=4`，普通清算与到期清算不消费；
- `GE_Player_Kuhara_BudEnd_Damage`：按自身静态倍率走同一独立 NATURE / Atk / character-crit 公式；
- `GE_Player_Kuhara_SeedReaction_Damage`：Atk `15%` 的 NATURE 追加直伤；Core 逐击以
  `GA_Kuhara_Passive_2 / Passive Damage` 标识该二被动结算，因此不读取静态伤害表关联的 A 技能等级。
  静态 `damage_source_category=A` 与 `ability_id=GA_Kuhara_Melee` 作为原始来源事实保留，不覆盖战报被动身份。

四者均正常消费九原攻击、通伤/自然伤、目标防御、自然抗/穿透、角色暴击率和暴伤；raw hit 属性为空时以
skill evidence/replay 的正式 NATURE 为准。`CoefModify` 的 Q 状态系数只属于上述 BudBoom 窄适配，不能
推广为其他附着物或 custom calculation 的经验常数。

候选敌方画像的残差裁决只消费未校正公式分支。已由逐击审计结构化判定为伤害归属冲突的 event_id 对全部
候选共同排除；原始逐击仍保留，也不使用 `corrected_expected_damage` 抵消敌方乘区。

SQLite 原始数组仍保留官方 `source_tier`，不改写成推导后的等级。Service 按下文已确认规则解释技能
15 档和环合 16 档，使来源事实与业务映射保持分离。

## 当前范围

- 已实现：直伤、DOT 单跳和按层结算。
- 已实现：倾陷伤害。
- 已实现：环合归属选择、通用环合强度乘区、覆纹按同一正式事件的被记录原伤害重放及灵可「弱点感应」扩展。
  覆纹比例与结算以本章环合基础规则为准；技能 15 档和环合 16 档等级映射已实现。
- 已导入：倾陷完整等级曲线、环合官方伤害档位、环合常量、技能倍率、敌方属性包和 Abyss 绑定。
- 待补充：运行时目标实例与静态属性包的正式场景快照、浸染逐击最终乘区的生产固定轴消费者、各环合伤害
  的完整状态时序，以及失谐倾陷扣除量的进一步实测确认。

DOT 层数按 `(上下半场, 目标实例)` 隔离；安魂曲噩梦、残虹蚀心与鸩火都只消费同一目标此前的施加事件。
多目标技能的“终段附加层数”也分别在各目标的最后一击结算，不再让一个目标的层数串到另一个目标。

暴击语义区分四种状态：读取角色暴击率、使用正式固定暴击率、固定不可暴击、是否允许暴击尚未确认。静态
`fixed_crit_rate=0` 仅表示“没有正数固定暴击率”，不能再同时承担“固定不可暴击”和“读取面板”两个相反
含义；已确认不可暴击的伤害必须显式标记。静态 `damage_type=TRUE` 没有独立 TRUE 抗性：发行静态库的
`enemy_element_resistance` 只有 chaos、cosmos、incantation、lakshana、nature、normal、psyche 和
psychically。TRUE 因而不能全局取消防御/抗性，也不能读取虚构的 TRUE 抗性；缺少专用 channel 适配器时
保持不可重放。`Buff_Tenacity_damage` 使用团队倾陷公式，`GE_Player_Daffodill_ExtraUnbalance_Damage`
使用达芙蒂尔本人的单角色额外倾陷公式，两者均把 TRUE 外壳还原为逐角色固有属性，正常计算防御及对应
元素抗性/穿透；后者本身不证明五觉。

实现位于 `src/services/damage_calculation_service.py`。该 service 为纯函数，不读写 SQLite、
不访问 UI，也不替代旧角色页面的“直伤评分”。

## 已确认映射与默认状态时序

### 技能倍率的 15 档

每个伤害 GE 在 `DT_SkillDamageData` 中保存攻击、生命、防御倍率数组。15 档数组按
`有效技能等级 - 1` 取值，超过数组末档时钳制到末档。角色页的有效技能等级不是直接相加觉醒数量，而是
基础技能等级加上当前具体觉醒选择所激活的 `character_awaken_skill_level_bonus`。三/六觉共鸣只在普通
觉醒勾选数达到门槛时激活。基础技能范围为 1–10 级；当前正式数据只有三觉共鸣提供 `+1`，所以最高生效
等级为 11，六觉不增加技能等级。`character_skill_level` 的 1–9 行是升到 2–10 级的九次升级要求，不是
角色页最大等级 9。派生 Ability、闪避反击、变轨分支与终结技子段不以自身 ID 回退到 1 级，而是通过
`gameplay_ability_level_hint.damage_effect_ids_json` 反查角色实际升级的父技能；同一伤害项存在多个正式
父技能候选时优先采用逐击观测 Ability，其次采用导入绑定，不猜测。未结构化为技能等级修改的说明文本不
参与倍率档位，避免从文案猜数值。

上游导出中的源数据路径（导出目录不属于仓库）：

`SOURCE_EXPORT_ROOT/Content/DataTable/skill/DT_SkillDamageData.json`

SDK 结构为 `FSkillDamageExecutionData`，位于：

`SOURCE_EXPORT_ROOT/CppSDK/SDK/HTGame_structs.hpp`。

### 环合 16 档

确定采用每 5 级一档：

```text
档位 = floor((角色等级 - 1) / 5)
```

1–5 级为第 1 档，76–80 级为第 16 档，适用于创生、浊燃与黯星。

### 环合状态默认规则

- 除黯星外，重复施加暂按刷新持续时间、不创建新实例处理，标记为未实测。
- 黯星按触发者独立计时、独立爆炸；同一触发者再次施加时刷新自己的实例。
- 覆纹逐次记录符合条件的实际伤害和该次伤害属性，到期按上文环合基础规则追加同属性伤害；
  原伤变化通过来源联动计入一次，覆纹自身的强度与扩展按同一公式计算。
- 状态被提前移除时不立刻结算，除非技能专属效果明确要求结算。
- 不额外设定“同一帧”规则，事件按服务端接收先后处理。
- 失谐目前按 `敌方倾陷上限 × 15%` 扣除；固定值、等级系数、联机修正均记录为待定且暂不参与计算。

### 怪物实例与属性包

静态迁移 v4 引入 `monster_instance_profile` 与 `monster_instance_profile_variant`，保留怪物实例到属性包
以及世界/副本/深渊等级变体的原始绑定；静态迁移 v5 引入 `abyss_level`、
`abyss_level_monster_spawn` 与 `abyss_monster_pool_entry`，导入 Abyss 关卡、波次、怪物池和属性包关系。
这些表继续保留在当前静态 schema v30 中；v27 保存轨外怪物池条目的官方本地化名称，v28 保存高危委托及其逐难度正式怪物池，v29 保存 `DT_BossSupportDataTable` 的正式 Boss 模板成员，v30 增加正式术语、角色获取关系、养成物品与确定掉落投影。v20 起保存属性包的 `HPMaxBase`、`HPMaxUp`、`HPMaxAdd`，
以及 RogueLike 怪物实例和属性修正；战报可据此计算单目标最大生命上限变化，但仍需以运行时敌方实例绑定
确认实际使用的属性包和修正组合。
该链路仅以 `HT/Content/DataAssets/DataAssetSet/Abyss` 的专用配置为准：`AbyssCloneLevelDataTable`
→ `MonsterPoolID` → `DT_AbyssMonsterPool` → `AttributeID` → `DT_MonsterPackData`。当前 648 条怪物池成员
使用 630 个属性包，全部能闭合到普通敌方属性表。v23 另保存官方怪物图鉴、材料/养成副本类目、难度与
刷怪模板绑定，但刷怪模板未必都能闭合到唯一属性包；
未闭合时只能作为目标身份证据，不能代替用户确认的防御和抗性。唯一 `AttributeID` 均命中普通属性包。
`FT_` 是 999 夜子玩法前缀，不表示轨外之境或 Abyss 场景，
不作为场景判定依据。
v24 保存限时战斗奖励任务提供的 `AbyssID` 大陆服生效区间，并标记构建日对应的当前与下一配置。
`AbyssID=Abyss_8` 表示一套包含多个 `LevelID` 的关卡配置；同一任务中的 `AbyssLevel=8` 才表示第 8 层。
v26 进一步保存当前/下一配置的赛季 Buff；赛季说明与曲线属于正式静态事实，逐击触发和倾陷窗口仍由本场
正式命中重建，不把今天的环境覆盖到其他配置。
第 10 期援护增伤保留正式 GE 的两个独立标签组；第 10、11 期限定环合参与角色的限时增益，
由独立分析组件依据同一受益角色的原生生命周期、属性修正或已求值 Spec 确认。
静态定义只提供数值、时长和效果路径，不证明某个角色参加了环合；缺少受益对象证据时标为未知，
相应反事实收益不可量化，不向全队创建持续区间。生命周期刷新、移除和角色隔离均沿用原生证据规则。
第 12 期无条件相、灵属性增伤使用正式曲线。以上为静态与专项模型验证，真实游戏验收仍需当前版本战报。

战报环境反向识别把当前分析范围内每个正式目标实例出现过的最高 `target_max_hp` 作为其初始最大生命，
以实例数及最大生命多重集与静态完整遭遇配置做精确相等匹配；轨外目标句柄按上下半分域。Core 玩法、层数、
半场和怪物 ID 作为额外过滤证据。单目标战报还可使用受击 GameplayEffect，但必须由索引与 ID 共同命中
静态目录，正式类路径明确位于怪物目录，且所有可用归属一致；不得直接解析 GE 展示名，多目标时也不补证。
通常只有一个环境配置完整符合时才投影目标身份和共享敌方参数；黑之书
与无首铁驭在相同世界等级具有相同生命且缺少怪物 ID 时，按产品约定默认黑之书，同时降为中置信并在当前
范围标红“歧义”，供用户打开条件选择器确认。自动候选仅包含当前/下一轨外配置、争锋
全部八个挑战对象、具有逐难度正式怪物池的高危委托、四类材料养成副本和异象追猎单 Boss，不包含只有
通用 Key=0 怪物池的旧高危委托、异象巡礼或普通大世界怪群。
