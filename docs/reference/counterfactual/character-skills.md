# 角色技能反事实文本盘点

> 本文件由 `tools/game_data/generate_counterfactual_inventory.py` 从只读发行静态库生成。
> “自动预判”和行内“待确认”不是当前人工进度；整理状态与正式语义以 `review-notes.md` 为准。

当前共 `23` 条含技能的静态角色记录、`22` 个逻辑角色、`92` 个原始技能目录项，另有 `59` 个不在角色技能目录中的绑定直伤 Ability。
同一逻辑角色的静态形态不会被误计为多个角色；具体归并和公式兜底以人工审计结论为准。

另有 `2` 条已确认战斗形态没有角色技能目录：安魂曲（1056 → 1004）、安魂曲（1091 → 1004）。
它们保留身份与原始 Ability 绑定，但不作为独立角色进入本期正式伤害审计。

## 早雾（1003；character:1003）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1003-GA_Sagiri_Melee | A / Z | 鬼郎丸头锤 | GA_Sagiri_Melee | 9 | 攻击 | 22 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1003-GA_Sagiri_Skill | E | 一口吞食 | GA_Sagiri_Skill | 5 | 攻击 | 10 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1003-GA_Sagiri_UltraSkill | Q | 千钧隆重的饕餮宴 | GA_Sagiri_UltraSkill | 3 | 攻击 | 8 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1003-GA_Sagiri_QTE | QTE | 砸扁你！ | GA_Sagiri_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1003-GA_Sagiri_Appear | 被动 / 特殊 | GA_Sagiri_Appear | GA_Sagiri_Appear | 2 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1003-GA_Sagiri_PerfectEvade | 闪避反击 | GA_Sagiri_PerfectEvade | GA_Sagiri_PerfectEvade | 1 | 攻击 | 3 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 安魂曲（1004；character:1004）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1004-GA_Lacrimosa_Melee | A / Z | 酸甜口味的制裁 | GA_Lacrimosa_Melee | 22 | 攻击 | 28 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1004-GA_Lacrimosa_Skill | E | 起床气加载中 | GA_Lacrimosa_Skill | 55 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1004-GA_Lacrimosa_UltraSkill | Q | 工作日最终审判 | GA_Lacrimosa_UltraSkill | 5 | 攻击 | 7 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1004-GA_Lacrimosa_QTE | QTE | 紧急唤醒 | GA_Lacrimosa_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1004-GA_Lacrimosa_SwitchSkill | 被动 / 特殊 | GA_Lacrimosa_SwitchSkill | GA_Lacrimosa_SwitchSkill | 2 | 攻击 | 8 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1004-GA_Lacrimosa_Appear | 被动 / 特殊 | GA_Lacrimosa_Appear | GA_Lacrimosa_Appear | 1 | 攻击 | 2 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 翳（1008；character:1008）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1008-GA_Skia_Melee | A / Z | 拘管格斗术 | GA_Skia_Melee | 12 | 攻击 | 16 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1008-GA_Skia_Skill | E | 潜影追击 | GA_Skia_Skill | 4 | 攻击 | 12 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1008-GA_Skia_UltraSkill | Q | 群犬吠形 | GA_Skia_UltraSkill | 2 | 攻击 | 8 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1008-GA_Skia_QTE | QTE | 缉令 | GA_Skia_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1008-GA_Skia_Evade | 被动 / 特殊 | GA_Skia_Evade | GA_Skia_Evade | 1 | 攻击 | 3 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1008-GA_Skia_Appear | 被动 / 特殊 | GA_Skia_Appear | GA_Skia_Appear | 1 | 攻击 | 2 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1008-GA_Skia_ShadowEvadeEnd | 被动 / 特殊 | GA_Skia_ShadowEvadeEnd | GA_Skia_ShadowEvadeEnd | 1 | 攻击 | 2 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1008-GA_Skia_ShadowEvade | 被动 / 特殊 | GA_Skia_ShadowEvade | GA_Skia_ShadowEvade | 1 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1008-GA_Skia_ShadowEvadeEnd1 | 被动 / 特殊 | GA_Skia_ShadowEvadeEnd1 | GA_Skia_ShadowEvadeEnd1 | 1 | 攻击 | 2 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1008-GA_Skia_PerfectEvade | 闪避反击 | GA_Skia_PerfectEvade | GA_Skia_PerfectEvade | 1 | 攻击 | 3 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 娜娜莉（1010；character:1010）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1010-GA_Nanally_Melee | A / Z | 柯林斯秘传技法 | GA_Nanally_Melee | 17 | 攻击 | 35 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1010-GA_Nanally_Skill | E | 柯林斯·嗷呜术 | GA_Nanally_Skill | 1 | 攻击 | 7 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1010-GA_Nanally_UltraSkill | Q | 柯林斯·终极术 | GA_Nanally_UltraSkill | 19 | 攻击 | 9 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1010-GA_Nanally_QTE | QTE | 天降正义 | GA_Nanally_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1010-GA_Nanally_Appear | 被动 / 特殊 | GA_Nanally_Appear | GA_Nanally_Appear | 2 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1010-GA_Nanally_PerfectEvade | 闪避反击 | GA_Nanally_PerfectEvade | GA_Nanally_PerfectEvade | 1 | 攻击 | 3 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 薄荷（1019；character:1019）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1019-GA_Mint019_Melee | A / Z | 满分收容术 | GA_Mint019_Melee | 13 | 攻击 | 39 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1019-GA_Mint019_Skill | E | 奥义·超级连风爪 | GA_Mint019_Skill | 5 | 攻击 | 6 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1019-GA_Mint019_UltraSkill | Q | 奥义·霹雳疾风斩 | GA_Mint019_UltraSkill | 7 | 攻击 | 14 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1019-GA_Mint019_QTE | QTE | 奥义·无敌飓风刃 | GA_Mint019_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1019-GA_Mint019_Appear | 被动 / 特殊 | GA_Mint019_Appear | GA_Mint019_Appear | 2 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1019-GA_Mint019_PerfectEvade | 闪避反击 | GA_Mint019_PerfectEvade | GA_Mint019_PerfectEvade | 1 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 哈尼娅（1020；character:1020）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1020-GA_Haniel_Melee | A / Z | 盖罗塞克特术法 | GA_Haniel_Melee | 8 | 攻击 | 9 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1020-GA_Haniel_Skill | E | 静默月林的守护者 | GA_Haniel_Skill | 2 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1020-GA_Haniel_UltraSkill | Q | 名为「哈尼娅」的旋律 | GA_Haniel_UltraSkill | 3 | 攻击 | 5 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1020-GA_Haniel_QTE | QTE | 彩蛋时间 | GA_Haniel_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1020-GA_Haniel_Appear | 被动 / 特殊 | GA_Haniel_Appear | GA_Haniel_Appear | 1 | 攻击 | 2 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 埃德嘉（1021；character:1021）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1021-GA_Edgar_Melee | A / Z | 体术实战 | GA_Edgar_Melee | 6 | 攻击 | 12 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1021-GA_Edgar_Skill | E | 狂流 | GA_Edgar_Skill | 1 | 攻击 | 4 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1021-GA_Edgar_UltraSkill | Q | 芬尼根守灵夜 | GA_Edgar_UltraSkill | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1021-GA_Edgar_QTE | QTE | 知识的重量 | GA_Edgar_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1021-GA_Edgar_PerfectEvade | 闪避反击 | GA_Edgar_PerfectEvade | GA_Edgar_PerfectEvade | 1 | 攻击 | 3 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1021-GA_Edgar_Appear | 被动 / 特殊 | GA_Edgar_Appear | GA_Edgar_Appear | 1 | 攻击 | 2 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 白藏（1023；character:1023）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1023-GA_Cang_Melee | A / Z | 言行合一 | GA_Cang_Melee | 14 | 攻击 | 14 | 倍率来源混合；专用逐击待审计 | 待确认全部分支、状态和消费者 |
| SKILL-1023-GA_Cang_Skill | E | 不吝赐教 | GA_Cang_Skill | 1 | 攻击 | 6 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1023-GA_Cang_UltraSkill | Q | 判予秋 | GA_Cang_UltraSkill | 6 | 攻击 | 3 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1023-GA_Cang_QTE | QTE | 摸鱼结束 | GA_Cang_QTE | 1 | 攻击 | 3 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1023-GA_Cang_Appear | 被动 / 特殊 | GA_Cang_Appear | GA_Cang_Appear | 1 | 攻击 | 2 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1023-GA_Cang_PerfectEvade | 闪避反击 | GA_Cang_PerfectEvade | GA_Cang_PerfectEvade | 1 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1023-GA_Cang_SkillA | 被动 / 特殊 | GA_Cang_SkillA | GA_Cang_SkillA | 3 | 攻击 | 6 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 哈索尔（1025；character:1025）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1025-GA_Hathor_Melee | A / Z | 高频送达 | GA_Hathor_Melee | 11 | 攻击 | 26 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1025-GA_Hathor_Skill | E | 空中指令 | GA_Hathor_Skill | 6 | 攻击 | 12 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1025-GA_Hathor_UltraSkill | Q | 铁骑速递 | GA_Hathor_UltraSkill | 2 | 攻击 | 6 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1025-GA_Hathor_QTE | QTE | 坠点标定 | GA_Hathor_QTE | 2 | 攻击 | 4 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1025-GA_Hathor_Appear | 被动 / 特殊 | GA_Hathor_Appear | GA_Hathor_Appear | 2 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1025-GA_Hathor_PerfectEvade | 闪避反击 | GA_Hathor_PerfectEvade | GA_Hathor_PerfectEvade | 2 | 攻击 | 5 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 阿德勒（1033；character:1033）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1033-GA_Adler_Melee | A / Z | 度恶 | GA_Adler_Melee | 9 | 攻击 | 27 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1033-GA_Adler_Skill | E | 诛恶护持 | GA_Adler_Skill | 3 | 防御 | 7 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1033-GA_Adler_UltraSkill | Q | 万象澄寂 | GA_Adler_UltraSkill | 2 | 防御 | 4 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1033-GA_Adler_QTE | QTE | 明镜本无尘 | GA_Adler_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1033-GA_Adler_Appear | 被动 / 特殊 | GA_Adler_Appear | GA_Adler_Appear | 2 | 攻击 | 6 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1033-GA_Adler_PerfectEvade | 闪避反击 | GA_Adler_PerfectEvade | GA_Adler_PerfectEvade | 1 | 攻击 | 3 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 残虹（1036；character:1036）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1036-GA_Zankou_Melee | A / Z | 燎原 | GA_Zankou_Melee | 32 | 攻击 | 38 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1036-GA_Zankou_Skill | E | 绯影闪 | GA_Zankou_Skill | 11 | 攻击 | 27 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1036-GA_Zankou_UltraSkill | Q | 焚天烬灭舞 | GA_Zankou_UltraSkill | 7 | 攻击 | 4 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1036-GA_Zankou_QTE | QTE | 饲火 | GA_Zankou_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1036-GA_Zankou_Appear | 被动 / 特殊 | GA_Zankou_Appear | GA_Zankou_Appear | 3 | 攻击 | 6 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1036-GA_Zankou_PerfectEvade | 闪避反击 | GA_Zankou_PerfectEvade | GA_Zankou_PerfectEvade | 1 | 攻击 | 3 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 法帝娅（1039；character:1039）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1039-GA_Fadia_Melee | A / Z | 话语中断的否定 | GA_Fadia_Melee | 9 | 攻击 | 18 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1039-GA_Fadia_Skill | E | 存在证明的体验 | GA_Fadia_Skill | 4 | 生命 | 7 | 倍率来源混合；专用逐击待审计 | 待确认全部分支、状态和消费者 |
| SKILL-1039-GA_Fadia_UltraSkill | Q | 痛苦让位于狂喜 | GA_Fadia_UltraSkill | 11 | 生命 | 6 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1039-GA_Fadia_QTE | QTE | 脱格者的嘲弄 | GA_Fadia_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1039-GA_Fadia_Appear | 被动 / 特殊 | GA_Fadia_Appear | GA_Fadia_Appear | 1 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1039-GA_Fadia_PerfectEvade | 闪避反击 | GA_Fadia_PerfectEvade | GA_Fadia_PerfectEvade | 1 | 攻击 | 3 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 「零」（1046；protagonist）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1046-GA_Female046_Melee | A / Z | 鉴痕 | GA_Female046_Melee | 10 | 攻击 | 22 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1046-GA_Female046_Skill | E | 铭隙鉴刻 | GA_Female046_Skill | 6 | 攻击 | 16 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1046-GA_Female046_UltraSkill | Q | 奇零除尽 | GA_Female046_UltraSkill | 4 | 攻击 | 8 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1046-GA_Female046_QTE | QTE | 绽裂 | GA_Female046_QTE | 2 | 攻击 | 4 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1046-GA_Female046_Appear | 被动 / 特殊 | GA_Female046_Appear | GA_Female046_Appear | 2 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1046-GA_Female046_PerfectEvade | 闪避反击 | GA_Female046_PerfectEvade | GA_Female046_PerfectEvade | 1 | 攻击 | 2 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 「零」（1051；protagonist）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1051-GA_Female051_Melee | A / Z | 鉴痕 | GA_Female051_Melee | 10 | 攻击 | 20 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1051-GA_Female051_Skill | E | 铭隙鉴刻 | GA_Female051_Skill | 6 | 攻击 | 16 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1051-GA_Female051_UltraSkill | Q | 奇零除尽 | GA_Female051_UltraSkill | 4 | 攻击 | 8 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1051-GA_Female051_QTE | QTE | 绽裂 | GA_Female051_QTE | 2 | 攻击 | 4 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1051-GA_Female051_Appear | 被动 / 特殊 | GA_Female051_Appear | GA_Female051_Appear | 2 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1051-GA_Female051_PerfectEvade | 闪避反击 | GA_Female051_PerfectEvade | GA_Female051_PerfectEvade | 1 | 攻击 | 3 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 浔（1052；character:1052）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1052-GA_Jin_Melee | A / Z | 胧月流 | GA_Jin_Melee | 12 | 攻击 | 22 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1052-GA_Jin_Skill | E | 今景复映 | GA_Jin_Skill | 3 | 攻击 | 7 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1052-GA_Jin_UltraSkill | Q | 浮世来潮 | GA_Jin_UltraSkill | 9 | 攻击 | 19 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1052-GA_Jin_QTE | QTE | 店主威势 | GA_Jin_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1052-GA_Jin_Appear | 被动 / 特殊 | GA_Jin_Appear | GA_Jin_Appear | 2 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1052-GA_Jin_PerfectEvade | 闪避反击 | GA_Jin_PerfectEvade | GA_Jin_PerfectEvade | 1 | 攻击 | 3 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 达芙蒂尔（1054；character:1054）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1054-GA_Daffodill_Melee | A / Z | 止水 | GA_Daffodill_Melee | 29 | 攻击 | 51 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1054-GA_Daffodill_Skill | E | 同振 | GA_Daffodill_Skill | 9 | 攻击 | 18 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1054-GA_Daffodill_UltraSkill | Q | 见此终幕 | GA_Daffodill_UltraSkill | 6 | 攻击 | 12 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1054-GA_Daffodill_QTE | QTE | 交仞 | GA_Daffodill_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1054-GA_Daffodill_Appear | 被动 / 特殊 | GA_Daffodill_Appear | GA_Daffodill_Appear | 10 | 攻击 | 19 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1054-GA_Daffodill_PerfectEvade | 闪避反击 | GA_Daffodill_PerfectEvade | GA_Daffodill_PerfectEvade | 1 | 攻击 | 3 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 九原（1055；character:1055）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1055-GA_Kuhara_Melee | A / Z | 秘密成型时 | GA_Kuhara_Melee | 20 | 攻击 | 16 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1055-GA_Kuhara_Skill | E | 情报猎手 | GA_Kuhara_Skill | 3 | 攻击 | 3 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1055-GA_Kuhara_UltraSkill | Q | 终局清账 | GA_Kuhara_UltraSkill | 4 | 攻击 | 11 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1055-GA_Kuhara_QTE | QTE | 秘闻锁定 | GA_Kuhara_QTE | 1 | 攻击 | 1 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1055-GA_Kuhara_Appear | 被动 / 特殊 | GA_Kuhara_Appear | GA_Kuhara_Appear | 2 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1055-GA_Kuhara_PerfectEvade | 闪避反击 | GA_Kuhara_PerfectEvade | GA_Kuhara_PerfectEvade | 1 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 海月（1070；character:1070）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1070-GA_Mitsuki_Melee | A / Z | 清唱 | GA_Mitsuki_Melee | 8 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1070-GA_Mitsuki_Skill | E | 华彩唱段 | GA_Mitsuki_Skill | 1 | 攻击 | 3 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1070-GA_Mitsuki_UltraSkill | Q | 众声轮唱 | GA_Mitsuki_UltraSkill | 2 | 攻击 | 4 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1070-GA_Mitsuki_QTE | QTE | 不协和音 | GA_Mitsuki_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1070-GA_Mitsuki_Evade | 被动 / 特殊 | GA_Mitsuki_Evade | GA_Mitsuki_Evade | 1 | 攻击 | 6 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1070-GA_Mitsuki_PerfectEvadeAtkBullet | 闪避反击 | GA_Mitsuki_PerfectEvadeAtkBullet | GA_Mitsuki_PerfectEvadeAtkBullet | 1 | 攻击 | 2 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 卡厄斯（1071；character:1071）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1071-GA_Chaos071_Melee | A / Z | 追猎 | GA_Chaos071_Melee | 24 | 攻击 | 63 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1071-GA_Chaos071_Skill | E | 疑点锁定 | GA_Chaos071_Skill | 2 | 攻击 | 5 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1071-GA_Chaos071_UltraSkill | Q | 罪业清偿 | GA_Chaos071_UltraSkill | 3 | 攻击 | 11 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1071-GA_Chaos071_QTE | QTE | 强袭 | GA_Chaos071_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1071-GA_Chaos071_Appear | 被动 / 特殊 | GA_Chaos071_Appear | GA_Chaos071_Appear | 2 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1071-GA_Chaos071_PerfectEvade | 闪避反击 | GA_Chaos071_PerfectEvade | GA_Chaos071_PerfectEvade | 1 | 攻击 | 3 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 灵可（1072；character:1072）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1072-GA_Radio072_Melee | A / Z | 普通攻击：恶灵CQC | GA_Radio072_Melee | 11 | 攻击 | 23 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1072-GA_Radio072_Skill | E | 变轨技能：瞬息全频振 | GA_Radio072_Skill | 4 | 攻击 | 13 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1072-GA_Radio072_UltraSkill | Q | 超负荷共鸣 | GA_Radio072_UltraSkill | 1 | 攻击 | 1 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1072-GA_Radio072_QTE | QTE | 恶灵左直拳！ | GA_Radio072_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1072-GA_Radio072_Appear | 被动 / 特殊 | GA_Radio072_Appear | GA_Radio072_Appear | 2 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1072-GA_Radio072_PerfectEvade | 闪避反击 | GA_Radio072_PerfectEvade | GA_Radio072_PerfectEvade | 2 | 攻击 | 7 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1072-GA_Radio072_UltraSkillLTE_AOE | 被动 / 特殊 | GA_Radio072_UltraSkillLTE_AOE | GA_Radio072_UltraSkillLTE_AOE | 1 | 攻击 | 2 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1072-GA_Radio072_UltraSkillLTE_AOE_Level4 | 被动 / 特殊 | GA_Radio072_UltraSkillLTE_AOE_Level4 | GA_Radio072_UltraSkillLTE_AOE_Level4 | 1 | 攻击 | 2 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1072-GA_Radio072_QTE_BackToLTE | 被动 / 特殊 | GA_Radio072_QTE_BackToLTE | GA_Radio072_QTE_BackToLTE | 1 | 攻击 | 2 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1072-GA_Radio072_Skill_Throw_XiaoZhen | 被动 / 特殊 | GA_Radio072_Skill_Throw_XiaoZhen | GA_Radio072_Skill_Throw_XiaoZhen | 2 | 攻击 | 6 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 小吱（1073；character:1073）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1073-GA_Chiichan073_Melee | A / Z | 失乡剑术 | GA_Chiichan073_Melee | 9 | 攻击 | 19 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1073-GA_Chiichan073_Skill | E | 粉爪在上原则 | GA_Chiichan073_Skill | 8 | 攻击 | 23 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1073-GA_Chiichan073_UltraSkill | Q | 零和博弈 | GA_Chiichan073_UltraSkill | 3 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1073-GA_Chiichan073_QTE | QTE | 临时入场 | GA_Chiichan073_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1073-GA_Chiichan073_Appear | 被动 / 特殊 | GA_Chiichan073_Appear | GA_Chiichan073_Appear | 2 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1073-GA_Chiichan073_PerfectEvade | 闪避反击 | GA_Chiichan073_PerfectEvade | GA_Chiichan073_PerfectEvade | 2 | 攻击 | 6 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 伊洛伊（1075；character:1075）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1075-GA_Oneiroi_Melee | A / Z | 分工合作 | GA_Oneiroi_Melee | 10 | 攻击 | 14 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1075-GA_Oneiroi_Skill | E | 未来自我连续性假设 | GA_Oneiroi_Skill | 19 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1075-GA_Oneiroi_UltraSkill | Q | 三十八亿年的海市蜃楼 | GA_Oneiroi_UltraSkill | 2 | 攻击 | 5 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1075-GA_Oneiroi_QTE | QTE | 砰！ | GA_Oneiroi_QTE | 1 | 攻击 | 3 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1075-GA_Oneiroi_Appear | 被动 / 特殊 | GA_Oneiroi_Appear | GA_Oneiroi_Appear | 1 | 攻击 | 2 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1075-GA_Oneiroi_PerfectEvade | 闪避反击 | GA_Oneiroi_PerfectEvade | GA_Oneiroi_PerfectEvade | 1 | 攻击 | 3 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |

## 真红（1076；character:1076）

| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |
| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |
| SKILL-1076-GA_Shinku_Melee | A / Z | 普通攻击：特种格斗术 | GA_Shinku_Melee | 34 | 攻击 | 36 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1076-GA_Shinku_Skill | E | 高速突破 | GA_Shinku_Skill | 6 | 攻击 | 6 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1076-GA_Shinku_UltraSkill | Q | 极轨终结：沸血赤红 | GA_Shinku_UltraSkill | 9 | 攻击 | 15 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| SKILL-1076-GA_Shinku_QTE | QTE | 破袭战术 | GA_Shinku_QTE | 1 | 攻击 | 2 | 固定轴通用逐击候选 | 待确认全部分支、状态和消费者 |
| BOUND-1076-GA_Shinku_Appear | 被动 / 特殊 | GA_Shinku_Appear | GA_Shinku_Appear | 2 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
| BOUND-1076-GA_Shinku_PerfectEvade | 闪避反击 | GA_Shinku_PerfectEvade | GA_Shinku_PerfectEvade | 1 | 攻击 | 4 | 绑定直伤 Ability；纳入逐击反事实 | 待确认输入分支、控制效果和资源值 |
