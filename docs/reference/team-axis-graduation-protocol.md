# 队伍轴伤毕业：基准与当前评价协议

状态：正式路由、Calc传输及界面已接入，分析组件已成套交付；大型真实轴新增有界无损快照表示，不改变伤害模型。限定覆盖不代表全部角色机制或稳定发行。范围与待办见[开发方案](../roadmap/teams.md)，实际证据见[验证清单](../validation/team-axis-graduation.md)。

## 1. 能力、传输与职责

- 路由 `team_axis_graduation_v1`，正文版本 `2`；已实现 `theoretical_benchmark`（同时评价当前并搜索理论）及 `current_evaluation`（复用固定参照，仅更新当前）。
- 基准和当前操作分别要求 `team_axis_graduation_benchmark_v2` 与 `team_axis_graduation_current_v2`。源码版本探针不声明完整的 `team_axis_graduation_v1` 能力；建议范围见第6节，完整持久化和UI仍待接入。发行组件是否可用以清单、执行文件哈希、实际探针和版本共同判断，不从源码支持推断部署状态。
- 沿用 `nte-analysis-request-v1` / `nte-analysis-response-v1` 的一次性 UTF-8 JSON。单份新请求与响应最多 8 MiB；目标正文仍受其自身 512 KiB 上限限制，实际构筑正文受分析准备器的 2 MiB 上限限制。
- 全层递归拒绝重复 JSON 键、非有限数值、歧义整数/布尔和未知协议字段。保留既有传输的超时、独立子进程取消与回收；不启动采集 Core，不连接游戏、不运行 Python 伤害回退。
- Calc 冻结实际来源、请求和组件身份，独立分析只读加载事实与静态、计算并返回派生值。请求不包含全背包候选；理论装备没有真实 UID，不进入装配或借出。

## 2. 请求信封

根字段严格为 `schema_version`、`dataset_version`、`batch_kind`、`request`。`request` 的共同字段及操作专属字段：

| 字段 | 语义与校验 |
| --- | --- |
| `version`、`operation`、`request_id` | `2`、`theoretical_benchmark` / `current_evaluation` / `recommendation_evaluation` 与本次请求稳定 ID；其他操作返回具体错误，不借旧模型完成 |
| `source_input_digest` | 建基准时从正式战报身份接口冻结，并在Context中复核；当前/建议固定使用该快照的原摘要，不探测源战报的新摘要 |
| `target_sha256`、`target` | 正式目标正文及其规范内容摘要；按[目标定义](../roadmap/teams.md#106-目标正文与规范身份)校验，不接受另一目标的摘要 |
| `battle_request` | 明确账号/generation、战报 ID、账号库/主静态/机制路径及所选范围；`detail_level=hit`；禁止隐含边际编辑、额外候选、倒置范围或相对路径 |
| `rules` | 冻结的完整 Calc 理论装备规则，绑定目标的 dataset/schema/静态/评分身份；Rust 再次验证合法性 |
| `seeds`（仅建立基准） | 每个目标预设唯一一组完整起点，必须覆盖所有预设；每组 1–16 个完整四人合法构筑，不让小预算隐藏后续非法种子 |
| `current` | 账号、generation、队伍及成员顺序、静态身份、来源内容摘要、家具和实际角色/装备值；不传预计算面板 |
| `current_provenance` | 来源修订、槽位/快照及逐字段来源，详见下节；摘要回显不替代来源证据 |
| `component_provenance` | 冻结执行文件和组件清单的两个SHA-256；Calc核对与当前客户端绑定身份相同，派发前后仍核对真实字节，缺清单不建立此参照 |
| `reference`（更新当前/建议） | 先前响应的不可变参照；不接受搜索起点或预算，不按新当前状态再求解 |
| `axis_snapshot`（更新当前/建议） | 基准响应中的完整不透明冻结轴；必须匹配参照的快照SHA，不读取原战报或机制文件，详见第7节 |
| `budget`（仅建立基准） | `max_evaluations` 为 1–100000，`max_millis` 为 1–600000；各理论预设共享总预算 |

实际构筑的 `build.characters` 必须是同序四人，每人含 `character_id`、`profile`、`equipment`、`equipment_complete`。profile 明确等级/突破、觉醒数量与选择/初始化、基础技能、好感、弧盘型号及成长；未装备弧盘必须显式 null。装备保留实际强化、属性单位和数值，逐队检查真实 UID 唯一性，不以目标满值重解释。完整性未确认时停止；明确空集合与未知集合不同。

## 3. 来源证据与派发复核

`current_provenance` 严格包含 `team_revision`、`source_mode`、`source_revision`、`bindings`、`world_source`、同序四人 `members`。

- `source_mode` 为 `game` / `current` / `slots`。修订为正整数；只有 `slots` 接受绑定，每名成员必须有对应稳定槽位，不使用显示名。
- 每名成员记录 `character_id`、`slot_id`、`plan_id`、`snapshot_id`、`source_fingerprint`、`field_sources`、`skill_level_sources`。非游戏来源必须有实际槽位、方案与来源快照，不追随最新背包重解释旧方案。
- 字段来源逐一覆盖实际 profile，基础技能来源逐一覆盖正式 SkillID。接受 `account` / `native_observed`；仅技能集合可标 `mixed`，仍须提供逐技能来源。模板及未知来源不被提升为实际进度。
- 已知零觉醒可明确推导空选择及已确认状态，来源标为 `derived_zero_awakening`，只用于这两个字段；确认未装备弧盘可推导三个空成长字段，来源标为 `derived_unequipped_fork`。这两种推导分别以明确零觉醒或空弧盘为前提，不推导其他养成。
- 家具当前只接纳已确认账号设置，`world_source=account`；目标家具独立冻结。当前输入可附加 `kaijia_defense_add`（0..30）；旧两字段输入继续可读，当前账号旧设置缺该新增字段时取产品默认满值。目标 DTO 保留原两字段及摘要，防御目标固定满值 30，不增加第三个可编辑目标字段。
- 新冻结参照的 `anchor_world` 可含第三项防御，按实际存在键参与规范化和 IEEE-754 摘要；旧两字段参照和轴快照严格保持原字段集合及摘要，不在冻结正文中补满或补零。

应用 owner 仍须在派发/保存前调用既有实际来源复核并检查账号代次。当前请求工厂只构造独立正文，不自行读取数据库、启动求解或保存结果；Controller/owner 闭环仍待实现。Rust 的证据结构校验与请求摘要绑定不等于已经证明调用方声明的来源真实。

## 4. 返回与未就绪语义

响应回显 schema/引擎/dataset/路由/正文版本、操作、请求 ID、轴来源摘要及目标摘要，并返回 `request_sha256`：对真正收到的完整请求字节计算 SHA-256。Calc 对真正派发的字节核对，不依赖跨语言 JSON 浮点重编码。

`model_id`、`critical_policy`、`unit` 分别使用新毕业模型的固定标识；不接受旧增强的实测权重单位或观测触发策略。`current` 返回实际轴伤、来源/输入/静态养成摘要、计分域及量化缺口；建立基准时的`theory`返回最佳完整理论构筑、逐预设搜索证据、预算/停止原因及未知样本。三操作均返回`benchmark_reference`，更新当前时必须与传入引用一致。客户端检查两侧计分域、搜索汇总、最佳值与逐件候选对应关系、理论装备无真实 UID、属性单位及当前未知不伪装为零。

当前比较条件通过时返回 `status=axis_feasibility_unverified`，超出所有目标预设时返回 `status=outside_target`；两者均保持 `current_ratio_percent=null`。数学轴伤完整量化和养成条件通过都不等于装备精确合法域、轴可执行性与条件传播已经闭合，不能据此构造正式毕业评价或显示完整毕业百分比。未知候选、无可比起点和零伤害分别保留；有限搜索只标 `bounded_best`，不声明全局最优。

### 4.1 当前比较条件证据

`current.target_qualification`只在实际输入通过公共静态养成/装备投影后生成，不把缺失或非法输入归类为低养成。严格字段：

| 字段 | 含义 |
| --- | --- |
| `policy_id` / `scope` | `team_axis_comparison_qualification_v1` / `cultivation_and_world_caps_v1` |
| `exact_domain_membership` | 固定为`not_evaluated`，本证据不是完整装备合法域成员证明 |
| `target_sha256` / `current_source_sha256` | 当前目标及本次实际来源的摘要绑定 |
| `status` | `within_target_conditions`或`outside_target_conditions` |
| `compatible_preset_ids` | 按目标规范顺序列出满足条件的整队预设ID；至少一个满足才通过 |
| `presets` | 覆盖全部目标预设，每项为`preset_id`、`compatible`、`issues`、`issues_omitted` |

每个预设独立校验四名成员及两项家具，整队须满足**同一个**预设；不逐人取不同预设的最大投入再拼成虚构目标。所匹配的预设无需等于参照最优候选所在预设，允许目标本来就是多个完整预设的并集。

- 实际角色等级、突破、觉醒数量、同一SkillID的基础技能等级分别不得高于该预设；实际觉醒效果须为其明确选择的子集。目标未启用好感十级时，当前已启用属于超条件。
- 当前明确未装备弧盘是正常养成差距。已装备时须为该预设同一型号，等级/突破/精炼分别不超过对应上限；型号不同不是按攻击高低猜可比性。
- 实际两项家具分别不超过目标家具。较低养成、较低强化以及不同实际装备/套装/词条不因未达到理论满值被判超条件；本层不检查当前装备是否精确属于理论图纸/满值离散域。
- 低投入被接纳不证明伤害随投入单调增加，也不保证有界参照一定高于当前；后续精确域/起点保证、动作资源可执行性和大于100%诊断仍分别实现。

诊断项严格为`character_id`、`field`、`entry_id`、`code`：角色使用正式ID，家具的角色为null；技能/觉醒选择用正式条目ID，其余`entry_id=null`。数值/开关超限为`exceeds_target`，型号/技能/觉醒选择不属于目标为`not_in_target`。每预设最多128项，剩余数量由`issues_omitted`明确报告；省略诊断不会变成兼容。Calc只校验结构、身份、原因和汇总守恒，不复制Rust养成判定。

资格证据随每次当前输入更新，不写进不可变参照正文。当前从超条件改回目标范围时只更新本次证据，旧基准ID、参考伤害和搜索证据保持原样；超条件也不触发静默重搜。

## 5. 不可变数学参照与当前更新

`benchmark_reference`格式为`team_axis_graduation_reference_v2`，规范正文最多512 KiB。根字段严格为`format`、`benchmark_id`、`reference_sha256`、`payload`，其中ID为`axis-benchmark-<内容摘要>`。它是数学参照，不是库存装备或已证明可执行的100%毕业证书；零分母/无可比候选时不生成此引用。

payload逐项保存：

- `account_id`、`component`（执行文件/清单双哈希）、`scope`（战报ID、起止、半场选择）；不含generation或数据库路径。账号隔离仍由账号上下文及保存owner核验，同账号新代次可重新冻结当前，但旧回调不能因此生效。
- `axis_snapshot_sha256`绑定本次Context完整冻结轴的字节身份，不接受旧格式引用或隐式补齐。
- `target_sha256`、`rules_sha256`、`preset_id`、完整`build`、`layout_sha256`及`cultivation_sha256`。完整规则摘要由Rust对typed规则计算，防止保留旧来源标签却偷换实际规则。
- `scoring_domain_sha256`、`replay_input_sha256`、`anchor_world`、`reference_damage`；消费身份包含实际重放正文、技能/目标/增益/锚及模型缺口，不只看总伤害或事件数量。
- `engine_version`、`model_id`、`critical_policy`、`unit`、`axis_feasibility`、`origin_request_sha256`及原有界搜索摘要`search`。有限邻域完成不成为全局最优证明。

摘要规范：payload递归按键排序、UTF-8 Unicode原文、无额外空白；`reference_damage`及`anchor_world`两项家具在哈希投影中替换为IEEE-754 binary64大端16位小写十六进制串。家具零同时在冻结正文规范为正零，整数/小数/指数拼写不改变同一内容身份；数组顺序保留。共享合成黄金正文为[`team_axis_graduation_reference_v2.json`](../../tests/fixtures/team_axis_graduation_reference_v2.json)。

基准求解后单独复核incumbent一次，响应`reference_verification_replays`标明该额外重放，不偷偷计入或挪用搜索预算。此值为理论候选复核数，不是包含环境准备在内的进程总公式调用数。

更新当前时，只重建引用中的固定构筑，不调用理论搜索。组件、账号、目标、规则或范围不同先停止；同一范围的消费输入摘要及参考伤害仍须经过独立重放复核，不相信手改后重新摘要的分母。`reference_verification`返回`replayed_unchanged`、旧参考伤害/计分域/重放摘要、`search_evaluations=0`、`reference_replays=1`。任何变化返回具体过期/不匹配错误，不发布新当前结果、不改旧引用。

`source_input_digest`在当前/建议请求中固定为基准快照原值。历史A的锚已包含在快照中，必须与参照`anchor_world`相同，绝不覆盖还原后的家具来掩盖不匹配。当前投影独立使用本次明确的实际家具。源战报后续修复、删除或机制文件更新不改变既有冻结轴；显式重新建基准才消费新的Context。

引用摘要不是来源认证，不代替账号代次、当前实际来源、目标修订和组件身份复核。Calc准备和执行当前/建议不再调用实时战报identity；已完成预览保存前仍复核实际来源及组件，不要求源战报继续存在。账号DAO持久化见第8节，历史交互展示仍待实现。

## 6. 养成建议独立评价

`operation=recommendation_evaluation`复用当前更新的严格请求字段及不可变引用，不接收seeds、搜索预算或全背包。当前源码单独声明`team_axis_graduation_cultivation_advice_v2`，不把养成建议冒称为全部词条/装备建议或完整毕业能力；旧组件未晋升该能力时该入口保持待更新。

当前比较条件通过时，优先选择参照最佳构筑所在的兼容整队预设；若当前只满足其他允许预设，则选择规范顺序的首个兼容预设，并明确返回`preset_id`。全部预设均超条件时不生成养成建议，返回具体停止原因，旧参照仍保留。

候选从实际养成向所选明确目标生成：角色等级/突破成组，技能升级附带必要目标等级/突破前置，觉醒数量/效果选择成组，弧盘型号/成长成组，好感和两项家具分别评价。两个及以上变化组另生成一份去重的完整联合养成候选，单独整队重放，绝不把单步收益相加。只有一个变化组时不重复评价同一联合构筑。各候选保留实际装备，不虚构库存件、不调用装配或保存真实配装；内部投影有独立建议来源标签，不将假设升级为已发生的实际养成。

响应在当前更新的共同字段外新增`recommendations`：

| 字段 | 规则 |
| --- | --- |
| `scope` | 固定`cultivation_to_compatible_preset_v1` |
| `preset_id` / `stop_reason` | 所选预设；完成声明候选为`declared_candidates_complete`，截断为`candidate_limit`，当前超条件为`current_outside_target_conditions` |
| `candidate_limit` | 固定64；稳定候选顺序，联合候选优先保留；这不是全游戏养成变量的穷举最优证明 |
| `generated_candidates` / `attempted_candidates` / `omitted_candidates` | 声明候选总数、实际尝试数及未尝试数，后两者之和等于总数 |
| `evaluated_candidates` / `preparation_unavailable_candidates` | 真正完成数学重放数及准备失败数，二者之和等于实际尝试数；准备失败不冒充已评价 |
| `items` | 每个已尝试候选的完整变化及独立结果，已知数学差值降序、未知在后，同分按候选ID稳定排序 |

item严格包含`candidate_id`、`kind`、`changes`、`status`、`error_code`、`evaluation`、`impact`。变化逐项包含正式`character_id`（家具为null）、`field`、`entry_id`（仅技能为正式SkillID）和目标`value`。候选身份不是装备UID。Calc验证目标值、前置字段、角色及唯一变化；不在Python另算候选伤害。

- `mathematical_only`：重放同域且数学差值已知；`impact.damage_delta`是整队伤害差，`relative_gain`为相对当前的无量纲增幅，`reference_fraction_delta`为差值/旧分母，不是已证明可兑现的毕业百分点。当前零伤害时相对增幅为null，负收益保留。
- `unquantified`：保留候选评价中的机制/条件缺口，`impact=null`；未知不转成无提升。
- `preparation_unavailable`：保留固定错误码，评价及收益为null；不吞掉其他独立候选，也不修改基准。

现阶段`impact.graduation_percentage_points`和顶层`current_ratio_percent`均为null，轴可执行性仍未证实。理论搜索次数仍为0，旧参照经固定重放复核后原样返回。取消或身份漂移不发布半批结果。本操作仍仅评价养成；装备/词条由下节独立能力处理，不将该受限scope扩写为完整建议。

### 6.1 参照装备/词条建议

`operation=equipment_advice_evaluation`沿用当前更新的严格请求字段、正文版本与冻结参照/轴快照，不接收库存、seeds或重搜预算。独立能力为`team_axis_graduation_equipment_advice_v2`，与养成建议能力分开；缺此能力只限制该操作，不提升现有组件打包要求。

只评价既有参照的获胜预设与合法逐件词条构筑，不另搜分母。候选保持实际角色/技能/觉醒/弧盘/好感和家具不变，将装备向参照目标变更：整队联合、每人整套，以及有严格逐件结构对应时的单件目标。对应要求同正式物品、品质、形状、套装和已知锚点；没有对应时只评价完整合法目标布局，返回局部步骤缺口，不猜坐标。只有一个变化件时不重复生成相同的单件与整套候选。联合候选独立整队重放，不加总单项收益；各人逐件预算独立，不互相转赠。

响应在共同当前更新字段外新增`equipment_recommendations`：

| 字段 | 语义 |
| --- | --- |
| `scope` / `preset_id` | `equipment_to_reference_build_v1`；固定为参照预设，不静默改为其他兼容预设 |
| `target_equipment` | 由参照`build`及目标布局物化的四人完整理论装备；保留正式词条值/单位/品质/锚点，无真实UID |
| `piece_steps_unavailable_members` | 逐件结构对应未证实的正式角色ID；不是整套目标不合法或没有收益 |
| `candidate_limit`及各计数 | 固定最多64次；完整队伍/成员组优先，剩余按稳定身份顺序尝试单件。生成=尝试+省略，尝试=完成重放+准备失败 |
| `stop_reason` | `declared_candidates_complete`、`candidate_limit`；实际养成不满足参照预设比较条件时为`reference_preset_outside_current_conditions`且不生成候选 |
| `items` | 严格包含`candidate_id kind changes status error_code evaluation impact`；已知整队伤害差降序、未知在后、同分按ID稳定排序 |

`kind`为`combined_equipment`、`member_equipment`或`piece_equipment`；变化项严格为`character_id mode piece_ids`。整队/单人组使用`replace_loadout`并列出该角色完整理论piece集合；单件使用`replace_pieces`且只列一个正式目标piece身份。变化不带装备UID，也不是装配指令。`evaluation`为同计分域的完整数学轴伤证据；状态、固定错误码与`impact`单位沿用上节，但没有伪造的实际养成评价身份。

仅词条变化复用当前模板与公式memo；套装/形状等正式规则依赖变化时，从同一冻结Context重新准备候选规则，不复用旧套装图。缺模型或准备失败分别保留未知，不把失败当零收益。所有结果仍是数学反事实；固定轴比例扩展见第9节，不代表实战可兑现。核心总路由、Calc严格传输、应用服务、显式保存/历史和界面入口已接入，实际执行物及能力以成套组件清单为准。

应用请求冻结实际来源/账号代次和原参照/轴快照，不接受库存搜索或新分母。界面只在保存基准或选用历史后开放装备建议，防止先覆盖尚未保存的首基准预览；服务仍不隐式保存。保存操作保持`equipment_advice_evaluation`真实类别，追加迁移扩展评价表CHECK，保留原正文、索引、选择及CAS修订，不改已发布迁移。父表重建沿用正式迁移器的一致性备份、事务、外键暂关与提交前检查；失败回滚后可重试。

## 7. 正式协议绑定的轴消费上下文快照

正式基准在同一次已准备Context中生成`team_axis_context_snapshot_v1`，响应携带`axis_snapshot`，参照v2绑定其SHA。当前/建议请求必须同时提供参照和快照，响应原样返回；只还原快照并打开同身份主静态。源账号库和机制文件即使已不存在，固定参照重放与数学评价仍有受控正式路由证据。旧v1引用不迁就为已绑定快照；旧组件的v1操作能力也不满足本版要求。

外层严格字段：`format`、`account_id`、`scope`、`dataset_id`、`static_schema`、`static_sha256`、`engine_version`、`source_input_digest`、`payload_sha256`、`snapshot_sha256`、`payload_json`。scope为战报ID、起止及半场选择；不保存数据库/机制路径或generation。同账号新代次可重新构造本次选择器，账号和范围仍须相符。

`payload_json`是不透明JSON文本，按Rust生成字节保存八个已消费上下文域：record、evidence、build、edit、saved_target、inferred_target、world_bonus、semantics。它既包含观测输入，也可能包含原Context已经准备的环境派生值，不是新的原始战报或来源真实性证书。它不携带完整库存或静态目录；还原时只打开同身份的完整主静态，并重用其目录和正式版本/dataset门禁，不再次推断已经准备的环境。

- 传输正文最多4 MiB，完整外层最多8 MiB；超限报具体错误，不删事件或截正文冒充完整。小正文继续是原始Facts JSON字节；较大正文可采用下述显式紧凑封装，恢复后的消费事实保持原字节/属性顺序、数值和未知值，不以压缩改变来源身份。
- 紧凑正文严格含`codec/decoded_bytes/decoded_sha256/data_hex`，`codec=zlib_hex_v1`。`data_hex`是单一完整zlib流的小写十六进制文本，拒绝奇数长度、非规范字符、损坏、截断、尾随或拼接流。声明的还原长度须为正整数且不超过64 MiB；输出写入器先按声明长度限位，只有解压器明确到达StreamEnd、消费全部压缩输入、长度及解码SHA-256相符后，才严格解析Facts JSON。
- 紧凑编码额外声明`team_axis_context_snapshot_compact_v1`。它不是启动/打包或旧战报页的新硬依赖；结果和后续操作仍须匹配已冻结的完整组件来源。内部表示修复不改变数值模型，实际候选由清单、执行物哈希和源码收据区分，不只凭相同引擎版本认定实现相同。
- `payload_sha256`按不透明文本UTF-8原字节计算，保留属性/数组顺序和浮点拼写。
- `snapshot_sha256`对外层去掉`payload_json`和自身后，递归按键排序、Unicode原文、无额外空白的UTF-8 JSON计算；该投影只有文本、整数和null，不依赖伤害浮点重编码。
- Calc只校验外层身份/限制和字节完整性，不拆逐击重算source_input_digest；该摘要来自正式Context身份接口。Rust在受界还原后完整解析Facts、拒绝未知/重复字段与非有限数，并要求家具是已规范化完整值，缺项不采用旧路径默认值补齐。
- 还原同时核对账号、所选范围、引擎、dataset/schema/静态哈希及原消费身份；静态文件前后校验。源机制文件不再被打开，源账号文件也不再被读取。摘要被自行重签不等于可信来源，正式基准同时绑定快照摘要和组件身份。

双端黄金正文见[`team_axis_context_snapshot_v1.json`](../../tests/fixtures/team_axis_context_snapshot_v1.json)，只含合成数据。快照仍属账号私有数据，不能进入常驻日志、公共素材或组件包；保存/导出须走本账号正式边界。该类型不赋予轴可执行性、完整覆盖或正式毕业百分比。

## 8. 可迁移数学结果存储

`SavedAxisResult`格式为`team_axis_graduation_saved_result_v1`，根严格为`format/request/response`，最多20 MiB UTF-8正文。保存的是已完成数学预览，当前版本仍不产生正式毕业百分比。参照不存在或无正分母时不建立可保存基准。

- request保留完整目标、规则、实际构筑/来源证据、操作和原搜索输入；选择器去掉本机三个文件路径及generation，当前构筑也去掉generation。更新当前/建议的参照和快照只保存response中的一份，不重复放入request。
- response保留原协议结果、原请求哈希、参照、冻结轴、覆盖及搜索/建议证据。`payload_json`不透明快照的原始文本不重编码；其他JSON字段规范保存，整个可迁移记录有独立SHA-256。原请求哈希是审计事实，不拿移除路径后的正文冒充原派发字节。
- DAO核对记录SHA、账号、队伍、目标版本、参照/快照/组件/静态关联及外键；应用保存、历史读取和账号导入/导出再经正式协议完整校验。历史校验临时补齐的选择器占位只用于纯结构验证，绝不读取源文件、执行重放或交给派发入口。
- 记录只包含本次冻结子集，不携带完整库存、本机配置、机制文件路径或运行期代次。快照仍属于账号私有数据，不能写常驻日志或进入组件包。

账号表与事务定义由[开发方案第11节](../roadmap/teams.md#11-状态流持久化和失效规则)维护；SQL/schema以追加迁移为事实源。持久化成功不等于当前状态仍有效，不重解释历史比例或自动选择更高分母。

## 9. 验证边界

已验证正式库总路由 → 真实受控 SQLite/Context → 当前/理论评价 → JSON 响应 → Calc 校验，并单独验证源码版本探针的能力范围。实际源冻结服务到请求适配亦有专项。协议样本只含合成身份，不包含私有源码或真实账号。

上述证据不是新 EXE 的 stdin/stdout 验收或组件晋升。Release 候选、成套清单、跨进程烟测、装备/词条建议、首次目标/轴交互和完整有效性投影仍按各自门禁推进。旧组件缺该能力时，新入口保持待更新，原队伍来源、匹配和增强不受影响。

## 9. 固定观测轴假设下的比例

用户已确认当前产品指标为固定观测动作/时点不变的数学比例，实战可执行性独立标为未证实。旧技术字段`current_ratio_percent`继续为空，不作为第二条用户指标；新结果位于`fixed_axis_graduation`。

新增独立能力`team_axis_graduation_fixed_axis_ratio_v1`。请求正文保持version 2，只有支持该能力的组件才显式增加`fixed_axis_ratio_policy=fixed_observed_axis_ratio_v1`。缺字段沿用旧响应，不增加新根字段；显式null、非文本或其他策略拒绝。Calc服务按组件能力请求，不为旧组件添加必需能力或更改打包门禁。

带策略的响应必须且只能额外返回`fixed_axis_graduation`，旧请求收到未请求扩展同样拒绝。基准、当前更新及两类建议共用该扩展；请求字节摘要覆盖策略，旧参照/轴快照身份不因比例变化而重签。

| 字段 | 语义 |
| --- | --- |
| `policy_id` / `assumption` | `fixed_observed_axis_ratio_v1` / `fixed_observed_actions_and_timing_v1` |
| `axis_feasibility` | `unverified`；当前数学比例不背书实战执行 |
| `benchmark_id` / `current_input_sha256` / `scoring_domain_sha256` | 绑定本次原参照、当前完整输入和共同计分域；无正分母时benchmark为null |
| `status` | `comparable`、`outside_target_conditions`、`reference_unavailable`、`current_unquantified`或`numeric_unavailable` |
| `ratio_percent` | 内核当前期望轴伤/冻结正参照轴伤×100；未知/超条件/数值异常为空，当前已知零为0，超过100不截断 |
| `damage_gap` | 参照减当前的有符号伤害差；不是比例百分点 |
| `relative_gain_to_reference` | 到参照的相对伤害变化（无量纲）；当前为0或该数值溢出时为空，不抹掉仍可表示的比例 |
| `above_reference` | 可比较时为当前是否高于冻结参照，其他状态为空；不能单凭此字段判装备非法或已证明搜索全局最优 |

数学覆盖不完整、非有限值和不一致计分域继续受原门禁约束，不能靠固定轴假设填补伤害未知。当前更新保留原分母且不重搜；显示负差值/超过100%的比例并提示搜索预算或目标差异，而不是夹到100%。所有数字由内核产生，Calc仅校验一致性、格式化与保存。

该扩展直接随既有数学结果存储/导出，不改schema或旧迁移；旧历史缺策略与扩展时仍可读，只是不补算比例。实战可执行性/完整参数域证据后续独立接入，不混为已证实承诺。
