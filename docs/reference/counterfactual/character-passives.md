# 角色被动反事实文本盘点

> 归属与解锁来自官方 `PassiveAbilityList`；名称和说明来自发行静态库。
> 主角 1046/1051 按同一逻辑角色去重，以 1046 为反事实代表。
> 行内“待确认”是自动目录的通用审计提示，不表示当前人工进度；整理状态与正式规则以 `review-notes.md` 为准。

当前共 `44` 个逻辑角色被动对象。

## 早雾（1003；character:1003）

### PASSIVE-1003-GA_Sagiri_Passive_1：可以吃吗？

- 解锁：突破 2 阶段。
- 官方说明：「浊燃」强化：「浊燃」状态下，目标身上每有一种持续伤害状态，受到的持续伤害提升25%，上限提升100%。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_003_Sagiri/PassiveEffect/Buff_Sagiri003_Passive2`。
- 直接属性语义：反应扩展写入 `FinalDamageUp`，SourceTags 与 TargetTags 均要求 `State.Damage.Dot`。
- 人工审计：已按目标前向结算状态实现 DOT 种类计数，并作为 `dot_final_multiplier` 只由 DOT 公式消费；
  同类多层只计一种。环合触发只记录待结算反应，不向首个可见浊燃之前的 DOT 倒填浊燃状态；首次浊燃
  跳伤在本击结算后建立状态，该首跳不消费本被动；后续 DOT 才按结算前状态消费。

### PASSIVE-1003-GA_Sagiri_Passive_2：鬼把戏

- 解锁：突破 4 阶段。
- 官方说明：早雾成功对敌人施加「浮空」或「压制」后，使敌人防御力下降10%，持续20秒。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_003_Sagiri/PassiveEffect/Buff_Sagiri003_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 安魂曲（1004；character:1004）

### PASSIVE-1004-GA_Lacrimosa_Passive_1：番茄酱盛宴

- 解锁：突破 2 阶段。
- 官方说明：「失谐」强化：触发「失谐」时，若目标已处于倾陷状态，则对目标额外造成400%攻击力的伤害。
- 运行时根 Buff：`未绑定`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

### PASSIVE-1004-GA_Lacrimosa_Passive_2：就要自然醒

- 解锁：突破 4 阶段。
- 官方说明：技能冷却期间，安魂曲使用「普通攻击：茄汁金属乐」或「普通攻击：茄汁打击乐」5次后，额外获得1次「变轨技能：起床气加载中」使用次数。此效果在每次技能冷却期间仅生效1次。
- 运行时根 Buff：`未绑定`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 翳（1008；character:1008）

### PASSIVE-1008-GA_Skia_Passive_1：现场控制

- 解锁：突破 2 阶段。
- 官方说明：「延滞」强化：「兽牙影刺」锁定「延滞」中目标时，对其额外施加一次「延滞」。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_008_Skia/Passivity/Buff_Skia_Passivity1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

### PASSIVE-1008-GA_Skia_Passive_2：捉拿归案

- 解锁：突破 4 阶段。
- 官方说明：释放「群犬吠形」后的15秒，「兽牙影刺」造成的伤害（包括「啮逐潜影」模式下的触碰伤害）提升10%。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_008_Skia/Passivity/Buff_Skia_Passivity2`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 娜娜莉（1010；character:1010）

### PASSIVE-1010-GA_Nanally_Passive_1：不止「一腔」的热血

- 解锁：突破 2 阶段。
- 官方说明：「创生」强化：「创生株」发射的「创生花」数量提升至10朵，每次发射的时间间隔缩短至1秒。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_010_Nanally/PassiveEffect/Buff_Nanally010_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 反事实策略：`creation-volley`。按用户确认的有界估算，被动已解锁时，将正式 `GE_ActorReaction_1_Damage` 与
  `GE_ActorReaction_1_1019_Damage` 逐击的花伤直接减半，只模拟“每次发射 5 朵提升至 10 朵”的花数差异。只有创生中文标签而缺少正式 GE 的逐击不参与减半，
  保留为 `unavailable`。该估算忽略发射间隔从 2 秒缩短至 1 秒，也不重建生成、到期、覆盖顺序和后续联动；
  因此结果固定为低置信 `partial`，不得标为 `complete`。专用株/齐射状态模型未来给出正式事件归属时，再按事件集替代该估算。

### PASSIVE-1010-GA_Nanally_Passive_2：绝对「公正」的决斗

- 解锁：突破 4 阶段。
- 官方说明：在「一代目的权柄」状态下，队伍中任意角色每对敌人造成1次异能环合伤害，娜娜莉将额外对单体敌人施加1次追加攻击，造成60%*攻击力的灵属性异能伤害，倍率跟随普通攻击技能等级成长，11级成长至129.5%。<br>该效果每2秒触发1次。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_010_Nanally/Upgrade/Level1/Buff_Nanally010_Level1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 反事实策略：`nanally-reaction-follow-up`。真实轴中由 `GA_Nanally_Passive_2` 或
  `GE_Nanally010_Lv1_Damage` 明确标记的追加攻击可以直接移除，证据 event ID 随结果保留。该部分以娜娜莉为来源、
  以真实逐击的角色为伤害提供者；回能、后续动作可用性和命中联动保持未量化，因此总结果为 `partial`。

## 薄荷（1019；character:1019）

### PASSIVE-1019-GA_Mint_Passive_1：变身！超级薄荷！

- 解锁：突破 2 阶段。
- 官方说明：「创生」强化：「创生花」命中时伤害范围扩大。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_019_Mint/PassiveEffect/Buff_Mint019_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 反事实策略：`creation-radius`。范围扩大不改变单朵倍率；已确认单目标时对固定轴伤害为 `not_applicable`，
  显示 0 但不删除逐击。多目标缺少花命中点、基础范围和目标位置时显示 `unavailable`，不根据已命中目标数猜额外收益。

### PASSIVE-1019-GA_Mint_Passive_2：收工！宾果时间！

- 解锁：突破 4 阶段。
- 官方说明：薄荷在前台时，自身防御力提升20%，抗打断能力提升30%。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_019_Mint/PassiveEffect/New/Buff_Mint019_Passive2_New`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 哈尼娅（1020；character:1020）

### PASSIVE-1020-GA_Haniel_Passive_1：是友情啊

- 解锁：突破 2 阶段。
- 官方说明：「黯星」强化：「黯星」状态结束时，全队角色从目标汲取攻击力。目标损失相当于哈尼娅4%基础攻击力的攻击力，全队角色获得哈尼娅8%基础攻击力。累计汲取不超过哈尼娅16%基础攻击力，目标损失不超过20%初始攻击力。离开战斗状态时还原双方属性。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_020_haniel/PassiveEffect/Buff_Haniel020_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

### PASSIVE-1020-GA_Haniel_Passive_2：是羁绊啊

- 解锁：突破 4 阶段。
- 官方说明：当在场角色触发「合奏」时，可以为哈尼娅累积1层「主角光环」，用以提高「超异科魔法炮」的弹射次数。每触发1次「合奏」使得哈尼娅获得1层「主角光环」。<br>当哈尼娅处于「超异科王牌」状态时，每触发1次「合奏」使得哈尼娅获得2层「主角光环」。<br>通过这种方式至多累积4层「主角光环」。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_020_haniel/PassiveEffect/Buff_Haniel020_Passive2`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 埃德嘉（1021；character:1021）

### PASSIVE-1021-GA_Edgar_Passive_1：温和的锋芒

- 解锁：突破 2 阶段。
- 官方说明：「盈蓄」强化：使用援护技触发「盈蓄」的角色在触发瞬间获得120点终结能量，后续创生花命中迟缓中目标时不再提供终结能量，此效果冷却时间30秒。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_021_Edgar/PassiveEffect/Buff_Edgar021_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 反事实策略：`edgar-charge-reaction`。该被动先改变援护技触发的盈蓄能量与 30 秒抑制窗，再可能改变未来 Q/动作。
  完整资源事件轴证明本段没有触发时为 `not_applicable`；否则缺少触发、充能效率、被抑制回能或未来动作消费任一证据时
  均显示资源类 `unavailable`，不把 120 能量直接换算为伤害。

### PASSIVE-1021-GA_Edgar_Passive_2：不变的暖意

- 解锁：突破 4 阶段。
- 官方说明：释放「变轨技能：狂流」或「援护技：知识的重量」后可获得一把「真理之匙」，最多可获得三把；<br>每把「真理之匙」可延长1秒「极轨终结：芬尼根守灵夜」持续时间。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_021_Edgar/PassiveEffect/Buff_Edgar021_Passive2`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 白藏（1023；character:1023）

### PASSIVE-1023-GA_Cang_Passive_1：适度恶趣味

- 解锁：突破 2 阶段。
- 官方说明：「浊燃」强化：处于「浊燃」状态下的目标被生成言灵字时，对目标再施加一次与已有效果相同的「浊燃」。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_023_Cang/PassiveEffect/Buff_Cang023_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

### PASSIVE-1023-GA_Cang_Passive_2：适度上工

- 解锁：突破 4 阶段。
- 官方说明：白藏攻击力提升20%；<br>当队伍中有禁制机动队四队成员时，白藏可与队员们协同作战。<br>战斗状态下，翳在潜行期间切换白藏，可直接触发白藏的一次攻击并在目标身上生成1个言灵字「噤」，冷却30秒。<br>战斗状态下，法帝娅的生命值低于20%时，白藏会上场攻击敌方1次，治疗法帝娅1次，并在目标身上生成1个言灵字「噤」，冷却30秒。<br>安魂曲在蝙蝠状态期间切换白藏，可触发1次白藏的超级跳跃。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_023_Cang/PassiveEffect/Buff_Cang023_Passive2`。
- 直接属性语义：AtkUp。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 哈索尔（1025；character:1025）

### PASSIVE-1025-GA_Hathor_Passive_1：延时预警

- 解锁：突破 2 阶段。
- 官方说明：「延滞」强化：目标受到「延滞」影响时间延长至12秒，全队角色攻击受「延滞」影响的目标时暴击率提升10%。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_025_Hathor/PassiveEffect/Buff_Hathor025_Passive1`。
- 直接属性语义：CritAdd。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

### PASSIVE-1025-GA_Hathor_Passive_2：效率推进

- 解锁：突破 4 阶段。
- 官方说明：哈索尔每次击败目标后，获得1层「闪送之力」。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_025_Hathor/PassiveEffect/Buff_Hathor025_Passive2`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 阿德勒（1033；character:1033）

### PASSIVE-1033-GA_Adler_Passive_1：克己

- 解锁：突破 2 阶段。
- 官方说明：「浊燃」强化：每次对目标施加「浊燃」时随机附带攻击力降低20%、属性抗性降低10%、倾陷效率提高10%三种负面效果之一，持续15秒，同一效果不可叠加。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_033_Adler/PassiveEffect/Buff_Adler033_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

### PASSIVE-1033-GA_Adler_Passive_2：正心

- 解锁：突破 4 阶段。
- 官方说明：阿德勒的防御提升20%。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_033_Adler/PassiveEffect/Buff_Adler033_Passive2`。
- 直接属性语义：DefUp。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 残虹（1036；character:1036）

### PASSIVE-1036-GA_Zankou_Passive1：暮落残阳

- 解锁：突破 2 阶段。
- 官方说明：「浊燃」强化：「浊燃」效果可叠加，上限3层。全队对「浊燃」状态下的目标施加持续伤害效果时，每施加1层持续伤害效果，就由残虹为其施加1层「浊燃」，已有「浊燃」的伤害、元素类型、持续时间刷新为本次施加的「浊燃」的效果。施加「浊燃」不重复触发此效果。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_036_Zankou/PassiveEffect/Buff_Zankou_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：基础浊燃 `StackLimitCount=1`；残虹专属浊燃 `StackLimitCount=3`。对已处于浊燃的目标实际
  施加每层非浊燃 DOT 时，由残虹补 1 层；首次触发后，已有基础层和新增层统一改用残虹本次施加的伤害、
  元素与持续时间快照，不作为两组浊燃分别结算；浊燃自身的施加和周期跳伤均不递归触发。

### PASSIVE-1036-GA_Zankou_Passive2：殷红幻景

- 解锁：突破 4 阶段。
- 官方说明：进入战斗时，残虹的「环合值」为100点，此效果有30秒冷却且单场战斗中最多生效1次。此外，残虹在队伍时，自身「环合强度」提升100点。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_036_Zankou/PassiveEffect/Buff_Zankou_Passive2`。
- 直接属性语义：MagBase。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 法帝娅（1039；character:1039）

### PASSIVE-1039-GA_Fadia_Passive_1：罪感熔炉

- 解锁：突破 2 阶段。
- 官方说明：「黯星」强化：「黯星」状态结束时，全队角色从目标汲取生命上限。目标损失相当于法帝娅固有生命上限200%的生命上限，全队角色各获得法帝娅10%固有生命上限，累计汲取不超过法帝娅固有生命上限的50%。离开战斗状态时还原双方属性。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_039_Fadia/PassiveEffect/Buff_Fadia039_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

### PASSIVE-1039-GA_Fadia_Passive_2：拒斥与豪掠

- 解锁：突破 4 阶段。
- 官方说明：如婴儿渴求母亲的奶水一般。法帝娅在队伍中时，全队获得10%最大生命值提升。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_039_Fadia/PassiveEffect/Buff_Fadia039_Passive2`。
- 直接属性语义：HPMaxUp。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 「零」（1046；protagonist）

### PASSIVE-1046-GA_Female_Passive_1：鉴定师

- 解锁：突破 2 阶段。
- 官方说明：「盈蓄」强化：当前驻场角色获得终结能量时，基于零基础攻击力的50%回复生命。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_051_Female/PassiveEffect/Buff_Female051_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

### PASSIVE-1046-GA_Female_Passive_2：异象感知力

- 解锁：突破 4 阶段。
- 官方说明：「极轨终结：奇零除尽」造成的伤害提升25%。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_051_Female/PassiveEffect/Buff_Female051_Passive2`。
- 直接属性语义：DamageUpGeneralBase。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 浔（1052；character:1052）

### PASSIVE-1052-GA_Jin_Passive_1：鬼兰家纹

- 解锁：突破 2 阶段。
- 官方说明：「创生」强化：时停期间，「创生株」攻击不暂停。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_052_Jin/PassiveEffect/Buff_Jin052_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 反事实策略：`creation-time-stop`。只有时停轴完整且当前范围没有时停区间时才为 `not_applicable`。存在时停或时停轴不完整时，
  缺少株身份、剩余生命、发射日程、覆盖顺序或未来动作轴均为 `unavailable`；已观测的时停内花击不等于移除被动后必然消失的总伤害。

### PASSIVE-1052-GA_Jin_Passive_2：天下万宝

- 解锁：突破 4 阶段。
- 官方说明：「极轨终结：浮世来潮」的终结伤害倍率提升100%。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_052_Jin/PassiveEffect/Buff_Jin052_Passive2`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 达芙蒂尔（1054；character:1054）

### PASSIVE-1054-GA_Daffodill_Passive_1：破鞘

- 解锁：突破 2 阶段。
- 官方说明：「失谐」强化：当目标进入「失谐」状态时，额外削减其10%倾陷值上限，持续30秒。当目标脱离战斗时，其倾陷值上限将恢复原状。多次触发「失谐」强化，削减效果可叠加，最高削减量为目标初始倾陷值上限的20%。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_054_daffodill/PassiveEffect/Buff_Daffodill054_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

### PASSIVE-1054-GA_Daffodill_Passive_2：空蝉

- 解锁：突破 4 阶段。
- 官方说明：「幻影移行」造成的伤害提升80%。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_054_daffodill/PassiveEffect/Buff_Daffodill054_Passive2`。
- 直接属性语义：DamageUpGeneralBase。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 九原（1055；character:1055）

### PASSIVE-1055-GA_Kuhara_Passive_1：顺势而获

- 解锁：突破 2 阶段。
- 官方说明：「创生」强化：额外生成1株创生株，场上创生株的存在上限提升至6株。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_055_Kuhara/PassiveEffect/Buff_Kuhara055_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 反事实策略：`creation-cap`。缺少原株/额外株身份、三株与六株上限下的生成覆盖顺序、剩余生命和发射次数时，
  显示 `unavailable`。专用状态模型可提供移除该被动后消失的正式 event 集；不允许把全部创生伤害乘二或除二。

### PASSIVE-1055-GA_Kuhara_Passive_2：风声为我所用

- 解锁：突破 4 阶段。
- 官方说明：创生花命中未缔结「致命玫约」的目标时，将强制缔结玫约。若命中的是已缔结目标，则会触发追加清算，使其承受额外 15%倍率的灵属性异能伤害。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_055_Kuhara/PassiveEffect/Buff_Kuhara055_Passive2`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 反事实策略：`kuhara-rose-settlement`。由 `GA_Kuhara_Passive_2` 或
  `GE_Player_Kuhara_SeedReaction_Damage` 明确标记的追加清算可以从真实轴直接移除，以九原为来源并保留证据 event ID。
  首次缔约、逐目标玫约状态、目标生命进程及后续联动尚未整体重放，因此直接伤害以 `partial` 展示，不冒充完整被动收益。

## 海月（1070；character:1070）

### PASSIVE-1070-GA_Mitsuki_Passive1：泛音

- 解锁：突破 2 阶段。
- 官方说明：「黯星」强化：目标的「黯星」状态结束后，将受到3次来自海月50%攻击力的魂属性异能伤害。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_070_Mitsuki/Passivity/Buff_Mitsuki070_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

### PASSIVE-1070-GA_Mitsuki_Passive2：渐强

- 解锁：突破 4 阶段。
- 官方说明：每当水母弹命中目标时，海月的攻击力提升1%，效果持续5秒，最多可叠加10层。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_070_Mitsuki/Passivity/Buff_Mitsuki070_Passive2`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 卡厄斯（1071；character:1071）

### PASSIVE-1071-GA_Chaos_Passive_1：未迟到的正义

- 解锁：突破 2 阶段。
- 官方说明：「延滞」强化：当目标的「延滞」状态解除时，卡厄斯将会根据本次「延滞」持续时间对目标造成800%*攻击力的相属性异能伤害，基础5秒外，每额外持续1秒整体伤害提升45%，上限300%。重复施加「延滞」状态将重置持续时间。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_071_Chaos/PassiveEffect/Buff_Chaos071_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

### PASSIVE-1071-GA_Chaos_Passive_2：重点关注！

- 解锁：突破 4 阶段。
- 官方说明：「追缉许可」的伤害增加效果提升至30%。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_071_Chaos/Buff/Buff_Chaos_Passvie2`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 灵可（1072；character:1072）

### PASSIVE-1072-GA_Radio072_Passive_1：弱点感应

- 解锁：突破 2 阶段。
- 官方说明：灵可在队伍中时，覆纹的额外追加攻击伤害提升至30%；受覆纹状态影响的目标，所受追加攻击伤害提高10%。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_072_Radio/PassiveEffect/Buff_Radio072_Passive1`。
- 直接属性语义：DamageUpGeneralBase。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

### PASSIVE-1072-GA_Radio072_Passive_2：精确调频

- 解锁：突破 4 阶段。
- 官方说明：触发「同频合击」时，将根据触发此次「同频合击」的角色，降低目标对应属性8%的异能抗性，持续12秒。目标身上可同时存在多种属性的减抗效果，「同频合击」造成的同种属性的减抗效果不可叠加，重复触发将刷新持续时间。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_072_Radio/PassiveEffect/Passive3/Buff_Radio072_Passive3`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 小吱（1073；character:1073）

### PASSIVE-1073-GA_Chiichan073_Passive_1：飞鸟症候群

- 解锁：突破 2 阶段。
- 官方说明：「盈蓄」强化：前台角色获得的终结能量提升4点。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_073_Chiichan/PassiveEffect/Buff_Chiichan073_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

### PASSIVE-1073-GA_Chiichan073_Passive_2：囤积癖

- 解锁：突破 4 阶段。
- 官方说明：小吱在前台时，自身充能效率提升20%。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_073_Chiichan/PassiveEffect/Buff_Chiichan073_Passive2`。
- 直接属性语义：ChargeGetEfficiencyBase。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

## 伊洛伊（1075；character:1075）

### PASSIVE-1075-GA_Oneiroi_Passive_1：镜象

- 解锁：突破 2 阶段。
- 官方说明：「创生」强化：当前创生株生成3秒后，将额外生成1株「创生株复制体」。复制体消失后不会再次生成复制体，且不计入创生株数量上限。复制体共绽放20朵「复制创生花」；每隔0.5秒，「复制创生花」会飞向射程内的目标并爆炸，每朵造成原「创生花」37.5%的伤害。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_075_Oneiroi/PassiveEffect/Buff_Oneiroi075_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 反事实策略：`creation-copy`。由 `GA_Oneiroi_Passive_1` 或
  `GE_ActorReaction_1_1019_Damage` 明确标记的复制花可以从真实轴直接移除；结果来源始终是伊洛伊，伤害提供者则沿用
  实际逐击的原创生提供者，两者不得合并。复制株生命周期、时停推进、目标选择以及复制花触发的九原清算保持未量化，所以显示 `partial`。

### PASSIVE-1075-GA_Oneiroi_Passive_2：交感性神经系统

- 解锁：突破 4 阶段。
- 官方说明：伊洛伊在提供治疗时，额外为全队提供5%的无视防御的效果，持续20秒。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_075_Oneiroi/PassiveEffect/Buff_Oneiroi075_Passive2`。
- 直接属性语义：`DefIgnore=0.05`，全队，20 秒，同名效果不叠加、重复治疗刷新。
- 固定轴先按正式技能与动画事件派生治疗：点按 E 的 `E.1`、QTE 的 `QTE.2`、Q 的 `UltraSkill.1` 即时
  治疗，以及 Q 施加后第 1～14 个有效战斗秒的周期治疗。每个治疗事件都刷新本被动，时停期间周期和
  持续时间均暂停；满血导致实际回复为 0 仍触发。缺少正式事件证据时不从 E/Q 动作猜测治疗。

## 真红（1076；character:1076）

### PASSIVE-1076-GA_Shinku_Passive_1：独行

- 解锁：突破 2 阶段。
- 官方说明：「盈蓄」强化：当场上角色获得终结能量时，后台角色将获得等量充能。真红位于前台且处于盈蓄期间时，每次触发都会造成 400%*攻击力 的范围伤害（倍率跟随普通攻击技能等级成长，11级成长至863.6%），并使自身攻击力提升 5%，持续 30 秒，最多叠加 10 层。此效果有1秒冷却。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_076_Shinku/PassiveEffect/Buff_Shinku076_Passive1`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。

### PASSIVE-1076-GA_Shinku_Passive_2：逆鳞

- 解锁：突破 4 阶段。
- 官方说明：真红的「极轨终结」对非Boss目标造成的伤害提升50%。
- 运行时根 Buff：`/Game/Blueprints/Abilities/Player/Ability_076_Shinku/PassiveEffect/Buff_Shinku076_Passive2`。
- 直接属性语义：无；属于触发/反应/专用逻辑。
- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。
