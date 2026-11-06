# 套装常驻：数据阶段与消费者接入审查

状态：本轮已完成十二套人工分类、来源校验和独立候选录入；运行时接入待实施。正式主静态、全局schema/importer及随附组件均未改变，现有打包输入不依赖此候选。分类/数值事实只维护在[常驻参考](../reference/equipment-suit-permanent.md)及其审查数据，不在本方案重复常量表。

## 1. 审查结论与统一激活模型

当前`src/domain/equipment/official_stats.py`汇总装备主副属性和额外形状，没有套装常驻；角色面板、直伤及配装汇总沿用此缺口。静态中已有修改包与战斗Buff证据，但“数据存在”“历史Python战报目录存在”“正式独立组件消费正确”是不同状态，不能以一个代替另一个。

推荐在Domain增加无IO的套装激活/常驻投影，由Service冻结DAO提供的套装定义和审查属性后显式注入；不在UI或优化内环查库，不按中文名或驱动行的套装名判断。

激活输入必须来自本方案自己的来源快照：核心正式suit_id、该核心要求的四种required_shape_ids、实际有效驱动形状。重复形状只算一种；缺核心不激活；普通虚拟占位不证明真实套装完成。二件套/四件套分别激活，四件套同时含二件套，但不重复添加。缺来源、未知形状或不完整候选要带已知性状态，不能静默当成已确认零。理论合法装备使用独立、明确的理论身份，不冒充真实UID。

投影输出按(suit_id,required_count,property_id,source_effect_id)保存来源及单位。套装是整套构筑的属性，不写回某一件卡带/驱动的主副词条、单件基础分或原始库存。条件增益不进入此模型；常驻与条件共享property_id也只叠加已满足的部分。

## 2. 必须接入的功能与实际入口

| 消费者 | 已审查入口/当前缺口 | 接入要求 |
| --- | --- | --- |
| 静态构建、资料与DAO | `tools/game_data/static_database_catalog_imports.py`、`src/storage/sqlite/static_game_data_metadata.py`及catalog查询；现有候选尚非发行表 | 下一追加迁移注册正式审查/属性表，更新importer与来源清单；构建必须逐套覆盖。独立图鉴缺证据时保持未就绪，不混读旧主库 |
| 计算配装候选与暴击门禁 | `src/services/legacy_allocation_static_catalog.py`、`src/services/allocation/context_builder.py`、`src/domain/crit_threshold.py`及原生allocation | 套装常驻随最终图纸/候选变化，不能在搜索前把四件套14%暴击无条件减进所有角色上限。每个合法候选在同口径下检查暴击上/下限，同级、缺件回退、换卡、恢复都复核；基础分配仍保持角色优先 |
| 直伤优先后处理 | `src/services/allocation/direct_projection.py`、`direct_postprocess.py`、`postprocess_facts.py`及独立组件 | 基线与候选均重算套装激活，换套/缺形状时失效缓存，不只沿用旧基线常量；冻结源、锁、UID、合法性与保存事务不变 |
| 战报轴伤优先与队伍增强 | `src/services/allocation/axis_postprocess.py`、`team_enhancement.py`、`src/integrations/damage_allocation_job.py` | 同一固定轴重放中候选装备变化要更新套装来源，不把全队/触发增益当常驻；共同计分域及未知语义保留 |
| 角色属性面板/属性明细 | `src/services/official_role/attributes.py`、`src/features/official_role/role_equipment.py` | 常驻独立展示来源，合入总攻击/生命/防御/暴击/增伤。治疗、护盾、充能保留专属行；不改养成草稿或原装备事实 |
| 角色与技能直伤 | `src/services/official_role/attributes.py::_role_panel_damage_inputs`、`src/services/damage_calculation_service.py` | 复用已有公式输入。当前角色适配只显式转入DefIgnore，需把正式属性DamagePenetrateChaos映射到相应元素的resistance_penetrations，不能错当全元素减抗或防御穿透。无对应公式的辅助属性不伪造伤害收益 |
| 边际权重与替换优化 | `src/services/official_role/marginal_scoring.py`、`replacement.py`及replacement_controller | 动态权重基于包含已激活常驻的当前面板；换核心/形状候选重新判定激活。基础评分仍走唯一ScoringEngine，不给单件基础分额外加整套常驻；A→B→A、保存/历史语义不退化 |
| 游戏/计算配装属性汇总 | `src/services/equipment/saved_loadout_attribute_summary_service.py`、`game_loadout_projection_service.py`、`src/features/inventory/equipment/plan_display_state.py` | 同一方案装备与静态身份一次冻结。游戏观察与计算满级上下文仍各按原定义，套装判定相同；来源未知不显示完整面板 |
| 配装对比、分享 | `src/services/equipment/weighted_loadout_comparison_service.py`、`loadout_share_service.py`及计算结果展示 | 两侧分别解析激活，展示整套属性差异；复用公共面板，不造分享专用常量。不覆盖冻结单件基础分和旧历史评分 |
| 角色个人毕业率与模板 | `src/services/official_role/graduation.py`、`tools/game_data/build_graduation_templates.py` | 分子/分母均按各自套装激活，模板重建而非手补面板；必要来源未知时停止完整百分比。个人直伤基准不替代队伍轴伤分母 |
| 队伍轴伤毕业率/建议 | `src/services/teams/axis_graduation/`及其正式组件协议 | 当前、理论、养成/词条建议使用同一套装模型；改核心/布局需重算依赖。参照/轴快照绑定新静态与组件身份，旧参照不静默重解释，数学比例与动作可执行性仍分开 |
| 战报冻结/重建/环境分析 | `src/services/battle_report/persistence.py`、`build/stat_reconstruction.py`、`buffs/equipment_suits.py`及独立组件 | 必查原生面板是否已含套装。已有战报规则、直接面板重建和新增常驻来源采用同一个来源键去重/归属；不得观测面板再加一次14%，也不得把根Buff和同源常驻投影叠两次。旧事实不重写，新派生结果带算法和来源 |
| 缓存与账号生命周期 | 以上Service、owner、预览及持久化入口 | 缓存绑定账号代次、snapshot/slot、完整装备身份、静态、算法与激活状态；只改无关账号库不使轴变更。取消/切换不写新账号，不从“最新背包”补历史 |

## 3. 明确不应直接接入的地方

- 单件基础评分、鉴定、弃置锁定、倒带评分不假设一件装备已激活二/四件套，不把整套属性按件分摊加分；需要整套收益时使用明确的构筑上下文与独立指标。
- 背包同步、采集、极速装配、锁/弃置RPC不改原物品属性、不新增真实UID或制造游戏内状态。此变更不是外部写操作授权。
- 护盾效率/治疗效率/充能常驻可以进入属性表，但没有对应输出模型时不把它们当作通伤或直接DPS。条件四件套继续走动作/Buff路径，不默认满层或全程覆盖。
- 旧方案、旧战报、旧参照和历史评分不全库批量重算；展示更新、显式新计算及迁移后的派生结果必须区分来源版本。

## 4. 推荐工作包及必要验证

1. **数据已完成的本轮**：24条审查与17条常驻候选；15项专项覆盖完整值、证据摘要、缺项、条件/有限时/曲线拒绝、重开与输入保护。正式data与组件不动；该候选没有发行manifest，不能晋升成game包。
2. **P0公共投影和正式数据**：追加迁移、DAO、激活纯函数；必要用例为无核心、0/1/2/3/4种形状、重复形状、虚拟占位、核心换套、来源缺失、四含二只加一次。核对静态身份、导出协议与分析组件schema兼容，再成套晋升，未完成前不提高现有打包硬要求。
3. **P0面板/直伤/计算暴击门禁**：先把最显著的14%暴击、攻击/生命/防御/增伤/抗穿接到统一投影与真实原生候选门禁，再接替换、汇总及对比/分享。选择一个二件、三个混合四件与两个辅助常驻做差分；不另起全局新优化策略。
4. **P1轴伤/毕业率与战报去重**：新旧来源、原生已含面板、冻结重建、根Buff/条件部分、候选换套、未知和历史回放差分。只在私库改分析源码，交付仍为核验过的组件包；各项操作按正式能力开放。
5. **P1辅助模型与发行**：治疗/护盾收益按各自公式另验，充能对轴可执行性的影响另建证据。完整回归、打包和实机验收是独立授权证据，不拿本轮数据专项代替。

每个工作包只跑本次必要专项与硬静态，输入未变的已通过证据复用。未接入项保持明确状态，不以17条入库宣称计算、面板、战报已经生效。

## 5. 更新skill

弧盘逐件审查已在版本更新工作流和适配矩阵中，本机调用副本已核对。此次补上套装二/四件套分拆、候选与正式录入的区别，以及上述消费者审查、防重复和暴击门禁要求；skill维护与运行时交付分别验收。受控同步只更新相应参考文件，不改用户其他skills。
