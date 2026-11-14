# 版本更新工作流

## 来源与差异

规则归 `tools/game_data/README.md`、`docs/integrations.md` 与 `docs/reference/static-catalog.md`；
数值身份、参数和能力归当前代码/清单，不在 skill 固化 schema、importer 或组件版本。

- 记录用户/来源元数据支持的游戏版本、地区与批次，另记录本次 `dataset_id` 和 `as_of`。构建日期不是游戏版本证据。
- 地区与渠道分别核对：目录名 CN 不证明正式服。记录来源仓库实际 ref、相关提交及工作区改动；合并提交中的保留版本按实际树核对。
  测试服候选明确标注渠道，不借用正式服旧字段闭合缺项；正式安装前确认该渠道正是用户要替换的目标。
- 正式构建器的 `catalog_characters.resolve_content_root` 只接收 Content 或其直接父目录。
  辅助脚本可在已知地区层次定位唯一根，随后把它返回的 **Content 根**传给数据及图片工具。
- `Content/DataTable` 存在只证明定位成功。核对所选 builder 的 `source_tables`、角色曲线/觉醒、Blueprint 和图片需求。
  同版多来源须明确优先级并登记逐文件来源；不跨版本或从旧发行文本/说明字符串填缺项。
- 当前 `_mirror_sources` 对缺失的 `text/ST_*.json` 支持同版 `Localization/Game/zh-CN/game.json` namespace。
  核对 `STRING_TABLE_SOURCES` 和实际结构后再决定转换/降级；旧登记路径缺失不等于新构建器缺输入。
- 核心表为空或资源级导出报错时先走[解包恢复](export-recovery.md)，再决定是否具备候选输入；进程退出成功不替代逐资源核对。

## 采集与低风险抓包更新

每次游戏更新都单独检查低风险抓包，而不只在原生同步报错时检查。现有消费边界见 `docs/integrations.md`：
抓包入口消费采集 Core 的角色/装备 JSON，当前原生入口从 Calc 静态库投影；独立分析 Core 另管战报分析。
记录各自的实际 EXE 路径、版本、SHA-256 与来源提交。同版号不代表同一字节；运行中的旧进程也不会因文件更新而自动换新。

- 先冻结同版 Content、静态 dataset 与采集目录的来源哈希。检查 Core 的实际资源选择：工作目录、执行物祖先中的外置 JSON 可优先于内嵌目录；源码资源存在或握手成功都不证明当前消费的目录正确。
- 复用历史命名的 `tools/game_data/export_native_sync_catalogs.py --source <Content> --resources <采集仓库/res/data> --output <隔离候选>`，不另造抓包导表。它重建角色、装备数值、形状和图纸；旧目录只提供经正式 ID 核对的展示元数据。来源/身份删除或改名须核实，不静默降级。
- 属性以本批正式 ID 全集为准，不能只遍历旧目录回填。核对 `source_attributes`、`attributes` 和 `added_attributes`，并逐 ID 比较单位与名称来源，不能只改覆盖率计数。`bShowPercent` 必须是已知布尔值，不用默认零或假值补缺。新属性中文来自正式文本；缺可靠外语译文时显式显示正式 ID，报告 `attribute_translation_fallbacks`。
- 重开导出目录并校验 `output_sha256`。抽查新旧角色、卡带/驱动、品质/等级/形状、主副词条及图纸关联；目录来源哈希相同不自动证明导出覆盖完整。隔离候选可供现有组件做离线加载正反例，不安装为正式资源，也不据此声称内嵌执行物已更新。
- 新版背包样本回放分别检查报文识别、字段解析、完整库存/角色关系、锁弃置与装备关系，以及 Calc 稳定化和一次提交。残缺/未知不推进正式快照，取消不留半成品；杂物数量观测与完整装备库存分开。缺样本则保留“待验收”，不借旧版样本、原生通过或离线握手补结论。
- 目录候选、组件源码资源更新、采集 Core 构建、成套晋升及真实游戏验收分别记录。需要构建时遵守所属仓库契约；更新完整清单、来源与许可，并核对两份采集 EXE 字节一致。版本按用户要求处理，不为资源改动自动升号。授权未覆盖的构建/晋升不执行。

从仓库工作目录运行，`$skill` 是本次实际使用的 skill 绝对目录：

```powershell
& $python "$skill/scripts/inspect_update_inputs.py" `
  --repo . --source $source --report "build/$batch/main-inputs.json"
# 独立图鉴有自己的基线，不以主库差异代替：
& $python "$skill/scripts/inspect_update_inputs.py" `
  --repo . --source $source --baseline-dir data/role_catalog `
  --report "build/$batch/catalog-inputs.json"
```

脚本对比所选包的数据库/manifest 身份与已登记来源，拆解 `combat_blueprint/` 和 `#namespace`，同一物理文件只哈希一次。
`unregistered_table_files` 只表示未被旧包登记，**不是版本新增证明**；缺基线返回未知，不返回零差异。
它不验证图片整包、行级差异或功能就绪。继续核对新/删/改角色行、字段类型、关系、技能/GE/资源映射及影响消费者；
用 `catalog_characters.py --source $source --output-dir <build 内报告> --as-of $asOf` 辅助区分逻辑角色、未实装和特殊形态。

## 候选与公共素材

以下变量来自本次已核实输入：`$python` 为项目 Python，`$source` 为唯一 Content 根，`$dataset` 为 dataset，
`$asOf` 为来源日期，`$candidate` 为仓库 `build/` 内唯一候选。先核对实际 `parse_args`/`--help`，非零即停在该阶段。

| 用途 | 数据入口与必要参数 | 图片入口与必要参数 |
| --- | --- | --- |
| 完整 `game` | `build_static_database.py --source $source --output "$candidate/game_static.sqlite3" --report-dir "$candidate/report" --dataset-id $dataset --as-of $asOf --manifest "$candidate/manifest.json" --omit-source-payloads` | 公共图片由独立图鉴提供，主库成功不替代图鉴/素材维护 |
| `role_page` | `build_role_catalog.py --source $source --candidate-dir $candidate --dataset-id $dataset --as-of $asOf` | `build_role_catalog_assets.py --source $source --database "$candidate/game_static.sqlite3" --output "$candidate/game_ui"` |
| `reference` | `build_reference_catalog.py`，同上四个数据参数 | `build_reference_catalog_assets.py --source $source --candidate-dir $candidate` |

表中工具均位于 `tools/game_data/`。两类图鉴数据 CLI **不自动执行图片 CLI**；各图片入口内部调用技能图片步骤。
`ApplicationPaths.game_ui_asset_root` 消费图鉴公共素材，两组候选、清单与验收分别保留。
图鉴不提供完整 Blueprint、弧盘常驻效果或毕业模板；`reference` 的评分/分配权限按当前用途 contract，不能据分析目录存在推战报支持。

完整 builder 已编排毕业模板和分析目录后处理，不重复调用。人工分类在 `character_overrides.json`、回退权重在
`character_weight_overrides.json`，它们不充当官方数值证据。成长、额外形状、觉醒和弧盘属性重新导入；
规范化语义变化递增 importer，schema 只追加迁移。
候选构建后若又修正目标或分析目录导出逻辑，旧 `battle_analysis_catalog` 不会随最终化自动重建；
须在隔离候选经正式构建／后处理入口重建，核对嵌入目录中的真实目标身份与画像，再刷新清单及预检。
只检查 UI 目录或源码测试通过，不能证明 EXE 实际消费的冻结目录已更新。

角色行和技能已齐仍可能有字段级悬空引用。构建失败时先按正式引用检查好感十级修改包、觉醒、成长、弧盘及技能依赖，
在同一 Content 根内排除表迁移、大小写和来源漏项，再选择最小接入。缺少可选养成参数不等于可以删除整名角色；
需要支持部分输入时，将缺项身份、来源与原因正式持久化并传至 DAO、面板、评分、计算与同步消费者。
选择了未知字段的计算应明确停在该缺口；明确未启用该字段时才计算其余已证实输入。
只跳过 importer 异常、填零、复制其他角色数值、让原生同步把未知写为 false，均不算字段级支持。
已观测到精炼 DataList 的曲线字段从 `NameID` 变为 `NameId`；仅在 importer 边界接受这两种明确字段名，
保持正式曲线 ID 与占位顺序。两字段并存但值冲突、空身份或类型错误仍中止候选，不做任意键名模糊匹配。
角色卡池的 `CharacterIDs`／`CharacterIds` 同样只接受已证实别名，并验证冲突、非空及正式掉落关系；
培养指南的显式 `ForkItemId: None` 表示无推荐，不生成悬空外键，保留原来源行。其他未知 ID 仍阻止构建。
新轨外赛季需同时审查根 Buff、事件引用的子 Buff、各属性曲线、持续和叠层；更新已审计组件时登记实际子效果路径，
不沿用历史 `_effect` 命名假定。终结技事件不能替代受益角色证据，条件增益仍要求正式接受者/效果观测。
赏宴的预览数组不等于正式刷怪身份：用同关卡副本难度的 SpawnID、刷怪成员、怪物实例及各自属性包核对全体目标，
不按目录名拼 `_BP_DiyBoss`，也不将一个目标的等级或总览名称套给其他目标。同名 stage_id 不证明跨 dataset 的历史成员不变。
除常规 `OptionChoices` 外，还需核对 `MechanismOptionChoices[].OptionID`、正式描述和 `MechanismScoreLimit`。
机制目标的可组合性与常规单选条件分别验证，不因已有选项总表包含对应 ID 就认定关卡成员与界面已接入。
保留全部 PlayerBuffs／BossBuffs 的来源和受体；选择目标不证明完成、触发或已获得属性，源分值上限不充当选项数量限制。
满练度毕业基准遇到必要养成来源未知时，保留角色与来源缺项，显式记录未生成原因；不降低“满练度”定义或造零伤害模板。
最终报告必须从当前候选重算并校验该缺项；构建报告已有记录不证明最终化后仍保留，重复最终化也不得丢失原因或将未知改成完整。

每件新/变更弧盘先审精炼曲线、直接 Modifier 和无条件常驻证据；已证实、明确无常驻、仅条件、缺证据/歧义分别记录。
图鉴 builder 的 `fork_permanent_property_audit` 当前为 unavailable，不能把页面数值已存在解释成该审查闭合。
各功能再按 [适配矩阵](character-adaptation.md) 决定数据变更、代码接入或待支持。

套装更新另逐项审查二件套与四件套：区分纯常驻、常驻/条件混合与纯条件，从修改包或无条件Buff/曲线取值，不从描述填数。
常驻分类和录入边界以仓库 `docs/reference/equipment-suit-permanent.md` 为准，消费者及防重复审查见 `docs/roadmap/equipment-suit-permanent.md`。
`tools/game_data/build_equipment_suit_permanent_candidate.py` 仅生成绑定逐效果证据的独立审查候选，不是game包，不作为运行时侧库；来源变化需重新审查，不能自动重签旧分类。
正式接入时另补追加迁移、importer和组件兼容，核对核心/有效图纸形状激活、四含二、暴击门禁、面板/直伤/替换/汇总/分享/毕业率及战报来源去重。
单件评分/鉴定不假设整套激活；有限时、满层、队伍触发效果不升为常驻，护盾/治疗/充能不偷换成普通增伤。数据入库不等于这些消费者已生效。

核对公共 `game_ui/manifest.json` 的 `skills` 正式 SkillID 映射、同版显示/别名关联、PNG 与逐文件来源/输出 SHA-256，
覆盖全部可分享主动技能。当前 `GameUiAssetCatalog.skill_icon` 只按正式 ID 查 `skills`，没有按中文别名找图的旁路；
缺图或冲突在候选中解决，不留分享专用技能副本。无关素材、模型、源码和调试符号不进发行。
首张缺图导致构建中止时，继续对全部正式资源请求做只读清单审计，而不是逐张重跑碰错；区分头像、立绘与技能缺口。
维护者接受待补素材时，可保留显式 `unresolved_assets` 的隔离部分候选与已核验图片，不删除数据库角色、不借旧图，
也不清空缺项标记绕过整包晋升门禁。补源后重跑正式图片入口并核对数据库／dataset 绑定。
若维护者明确要求先安装已有静态资料、后补角色展示图，使用 `defer_optional_role_images.py --candidate-dir $candidate --source $source`
并逐项传 `--defer-character-image characters:<正式角色ID>` 或 `character_arts:<正式角色ID>`。
工具须核对同版正式绑定和文件确实缺失，保留 `unresolved_assets`，另登记与其精确相等的 `deferred_optional_assets`。
仅这两类可选展示图允许延期；技能图、未知身份、额外未确认缺项、已有文件损坏和数值来源不足仍不放行。
这不是把部分候选改称完整素材；运行时缺图返回空，保留正式角色和数值缺口，正式数据晋升仍走原入口。

**纯技能图片维护**：只对已核验完整旧角色包制作隔离候选，运行
`build_role_skill_assets.py --source $source --candidate-dir $candidate`。
仅当数据库字节、dataset、scope、数据库 manifest 及其他资源组不变且获准安装时，增加 `--target-dir data/role_catalog`。
这是 CLI 调用 `promote_role_catalog.py::promote_role_skill_assets` helper，不是直接运行 `promote_role_catalog.py`；数据版本变化走整包晋升。

## 校验、晋升与回退

必要校验包括 SQLite 完整性/外键、正式关联、来源哈希、payload 省略、schema/importer、数值/机制审查与公共素材映射。
所选 dataset 和字节身份冻结到计算/预览；资料更新后旧结果重算，不重解释历史方案评分或原始战报。
测试按本次变更和授权选择少量公共行为；集中 core/full、组件与实机验收各有独立结论，不为 skill 维护执行真实更新。
静态 schema 变化后，用正式分析客户端对同一业务夹具分别冻结旧发行与新候选输入，实际核对身份读取及受影响页面；
组件版本、能力声明与哈希通过不证明它接受新的 schema。`battle_static_schema_unsupported` 等拒绝需在组件所属仓库解决，
不放宽 Calc 校验或回退 Python 直读。只有旧库通过的控制组才能帮助区分新候选兼容缺口与通用环境故障。
需要重建独立组件时遵守其所属仓库契约；逐文件源码收据留在私库，导出包只携执行物、必要许可与绑定整体输入摘要的清单，
不夹带私有源码、内部冻结清单或调试符号。候选导出、Calc 组件晋升及数据晋升仍分别确认。

发行数据变化可能使旧覆盖率计数失败。先用该测试引入时的正式 dataset／哈希复现旧基线，比较新增、删除、恢复及持续缺项的身份集合；
核对登记 source_row 行键与规范化主键是否完整对应，区分来源本就缺项与 importer 漏行。
登记行键覆盖不证明原始导出完整，原始 GAName 的缺字段／空值也须同版原文核对。
查询规则宜用合成夹具验证；发行覆盖率基线绑定 dataset、数据库哈希及已审计缺项身份，不直接改数字、删测或放宽断言制造通过。

`NTE_LOCAL_CONFIG` 指向仓库外配置；当前晋升必需字段是 `dataset_id`、`official_content_root`。
目标 `$target`：完整主库用 `data`，两类图鉴只用 `data/role_catalog`。不要把图鉴晋升到主库。

```powershell
& $python tools/game_data/promote_static_release.py `
  --candidate-dir $candidate --local-config $env:NTE_LOCAL_CONFIG `
  --target-dir $target --finalize-only
if ($LASTEXITCODE -ne 0) { throw "候选最终化未通过" }
& $python tools/game_data/promote_static_release.py `
  --candidate-dir $candidate --local-config $env:NTE_LOCAL_CONFIG `
  --target-dir $target --verify-only
if ($LASTEXITCODE -ne 0) { throw "候选预检未通过" }
```

最终化只写候选，预检只读；正式晋升会再次最终化，获准替换后才去掉 mode 参数执行同一命令。
体积门禁以 `static_release_size_policy.py` 为准，size warning 不自动放行。
晋升后重开正式包核对 dataset、scope、库/清单哈希与图片关联；源码接入、两组数据晋升及组件支持分别记结果。

**持久回退先准备**：两个晋升入口成功后都会清理临时旧件，自动失败回滚不等于旧发行备份。
晋升前保留已验证旧整包、身份/哈希及旧 dataset 对应配置/来源；账号活库只用一致性备份，不复制活动 WAL/SHM。
需要恢复时，对 `$previousCandidate` 和 `$previousConfig` 先用上述正式入口最终化/预检，再获准晋升到相同 `$target`。
旧包须满足当前 schema/importer 和来源门禁；预检失败则停止恢复并交维护者处理，不直接复制覆盖 `data/`。
若恢复的是本次晋升前已逐文件哈希保全的原件而非重建旧候选，使用单独受保护的原件恢复脚本：
先核对当前新版与备份旧版的全部托管文件身份，拒绝后续变动，再调用 `static_release_installation.install_static_release_pair`
和角色目录事务恢复整包；隔离副本实际回退并重开验证。此路径不把旧 schema 伪装成当前构建，不验证无关新版来源，也不接受任意外来旧包。
静态数据更新后，回退到只支持旧 schema 的源码／组件之前必须先恢复匹配数据，避免只退代码留下新版库。
不删除唯一备份、不用新版本配置验证旧包；主数据、图鉴/素材和外部组件分别恢复。

交付只留本批差异、原件/哈希、一个验证记录及受保护回退；来源原文与本机路径留本机，未知项指向所属功能或组件。
