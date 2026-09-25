# 游戏资料库：弧盘数据域

本页记录“工具 → 游戏资料库”的弧盘域只读边界。它消费本次打开时冻结的发行静态库；安装独立
`reference`/`role_page` 图鉴包时使用 `data/role_catalog/game_static.sqlite3`，否则使用
`data/game_static.sqlite3`。一次请求不逐行回退，不读账号库，不写静态库，不启动后台任务或游戏集成。

## 展示来源分类

| 标记 | 含义 | 弧盘域中的例子 |
| --- | --- | --- |
| `official_static` | importer 从正式数据集保留的字段 | 弧盘 ID、名称、等级 modify pack、突破消耗、混频参数、Buff/GE 资产路径 |
| `project_projection` | importer 对正式行所做的项目结构化投影 | `combat_effect_definition`、`combat_effect_buff_link`、跨表关系跳转 |
| `derived_display` | Service 为界面组合的值，不是新的游戏事实 | 20/30/40/50/60/70 级“突破前/突破后”的等级行 + 突破阶段行组合、百分比格式化 |

界面不从中文说明猜数值。临界等级通过公共
`advancement_stage_service.fork_active_panel_stats()` 选择明确的突破阶段，并组合该等级的
`fork_upgrade_level` / `fork_modify_value` 与对应阶段的 `fork_breakthrough` /
`fork_modify_value`，再加入当前精炼的 `fork_permanent_property`；20/30/40/50/60/70 级同时保留突破前和突破后两个节点。

## 图鉴组织与角色适配

首屏使用 `data/role_catalog/game_ui/forks` 和 `GameUiAssetCatalog` 的正式图标渲染全部 49 件弧盘，不创建数据库表格。
8 件限定特刊只消费 `StaticCatalogTerminologyService.list_fork_campaigns()` 返回的 DB-backed
`LocalizedForkCampaign`：正式 `featured_fork_id` 决定限定分组，`release_ordinal` 决定新到旧顺序，
`title.display_name` 决定多语言标题；标题缺失时显示“名称暂未提供”。页面不保留限定 ID、标题或顺序的
私有字典。其余 41 件属于同一首发批次，按品质、类型和名称稳定排列。类型筛选只改变当前卡片投影，不改变
数据或顺序事实。

Campaign 的 canonical identity 是 `pool_id`；`featured_fork_id` 只是关联弧盘，不是 campaign 主键或唯一
身份。同一弧盘未来存在多次复刻时，页面保留全部 campaign records，限定分组仍按关联成立，弧盘卡片与详情
展示其中 `release_ordinal` 最新的一条，排序也取该弧盘最高正式 ordinal，不用字典覆盖较新记录。

名称/正式 ID 搜索常驻首屏；品质与类型筛选收纳在默认折叠的筛选面板中。卡片墙按可用宽度在 1–5 列之间
自动重排，窄窗口不保留固定四列，也不产生横向滚动。筛选只作用于当前只读投影，清空后恢复完整正式顺序。

养成消耗和品质名称必须经过组合根注入的 Qt-free `StaticCatalogTerminologyService` 投影。页面不拥有货币、
物品或品质的中文映射；`ForkItemDisplayNameService` 只消费公共术语结果并生成玩家可读名称。
缺少正式名称的物品统一显示“名称暂未提供”，数量仍保留。玩家页面不展示 raw item ID、资源路径或来源
枚举；View 不硬编码或猜测物品名称。

详情固定展示“详情”和“养成”两个页签。详情顶部只保留紧凑的弧盘大图、名称、品质、类型和归属，不使用
占满横向空间的大看板，也不向玩家展示正式 `fork_id`。页面允许
在 1–80 级、临界突破前后和混频 1–5 级之间切换，联动展示面板、混频技能与效果关系。混频参数直接代入
正式技能描述；结构化效果只投影为玩家可理解的生效时机、效果内容、叠加规则和生效条件。界面不展示
dataset/schema/importer、资源或动画路径、GE/Calculation/requirement 路径、内部枚举和来源哈希。缺少可解释
效果时显示“暂无额外效果说明”，不显示伪造的零值。

每件弧盘都按正式 `group_type` 展示全部同类型可用角色；独占角色和养成推荐关系继续由
`exclusive_character_ids_json` 与 `character_cultivation_fork_recommendation` 标记，角色使用
`GameUiAssetCatalog.character_icon()` 的正式头像。没有独占/推荐关系不妨碍展示同类型可用角色，但不得把
同类型角色写成专属角色。角色头像与名称、混频效果的触发条件、效果内容和叠加规则直接在弧盘详情内呈现，
不再用跨页面跳转按钮代替关系内容。

## 养成材料展示

养成页只读展示当前所选等级的升级经验与突破消耗、0–6 阶完整突破路线，以及混频 1–5 级的正式消耗。
schema v36 另外通过 `fork_exp_material` / `fork_exp_material_cost` 展示淡色、无彩和混沌染剂的正式图片、
单件经验与使用甲硬币；突破阶段的材料和甲硬币同样使用 `progression_items` 图片卡片，不从描述文本提取数值。
材料和甲硬币都由公共术语目录投影正式名称与数量；缺名或缺量保持“名称暂未提供”或“暂未提供”，不显示 raw
ID，也不伪装成 0。页面不提供“当前 → 目标”、材料缺口、账号库存、副本次数或活力计算，不公开养成请求
信号和结果回填接口。

## 当前发行只读审计

当前主库来自同一正式服导出，经完整构建和正式晋升；精确 dataset、schema、importer、大小与哈希以
`data/manifest.json` 为准。独立图鉴以 `data/role_catalog/manifest.json` 为准，不向主库混入测试服数据。
角色、弧盘、技能、Buff 和敌人均按正式 ID、资产绑定及来源哈希关联。

| 内容 | 权威表 / 字段 | 覆盖规则 |
| --- | --- | --- |
| 弧盘目录与资源 | `fork_item`, `fork_type` | 正式品质、类型、icon/card/painting |
| 角色关联 | `exclusive_character_ids_json`, `character_cultivation_fork_recommendation` | 仅正式独占 ID 或养成推荐 |
| 升级、突破、混频 | `fork_upgrade_level`, `fork_breakthrough`, `fork_star_level` | 正式等级、属性包、消耗和占位参数 |
| 常驻面板 | `fork_permanent_property` | 按弧盘、精炼和来源参数唯一投影 |
| Buff、触发与 GE | `buff_definition`, `buff_modifier`, `buff_trigger_effect`, `gameplay_effect_catalog` | 精确资产路径关联，不由说明猜倍率 |
| 材料、本地化与染剂 | `progression_item`, `localized_term*`, `fork_exp_material*` | 正式物品身份、名称、经验和成本 |
| 排期和季节增益 | `outer_realm_rotation*`, `outer_realm_season_buff*` | 官方排期与明确维护者补注分开保存；受益对象证据见战斗覆盖文档 |
| 来源追溯 | `source_row`, `source_file` | 文件及行哈希；发行库省略原始 payload |

弧盘行来源包括 `DataTable/Fork/DT_ForkItemData.json`、`DT_ForkUpgradeData.json`、
`DT_ForkBreakthroughData.json`、`DT_ForkUpgradeStarDataTable.json`、`CT_ForkBuff.json` 以及
`DataTable/PackData/ModifyData/DT_ForkModifyData.json`。

## 当前 importer 边界

- 当前 schema 没有 `fork_skill` / `fork_skill_level` 表。产品中的“弧盘技能”只能展示混频
  1–5 级描述、参数、Buff 与效果，不可声称另有独立技能目录。
- `source_payloads_omitted=true`；发行库不能还原原始 JSON 全行，只能展示 importer 保留字段、
  来源文件和校验值。
- 弧盘到 GA 没有结构化直接绑定。DAO 只在资产路径精确匹配时返回 GA；当前发行库
  审计为 0，不从描述文本、名称或路径前缀推测。
- 有正式独占角色 ID 或养成推荐关系的弧盘显示对应徽记，其余不显示；所有弧盘仍可依据
  正式 `group_type` 展示同类型可用角色，不按中文名、描述或效果类型猜专属角色。
- 当前 schema 已提供通用养成物品和本地化目录；突破消耗通过稳定 item ID 关联正式名称。缺名仍显示
  “名称暂未提供”，玩家页面不展示 raw ID。
- `combat_effect_definition` 和 `combat_effect_buff_link` 是 importer 正规化的项目投影；
  其中描述、参数和 Buff 路径仍可回溯正式行，但投影 ID 不标成官方原始字段。
- `fork_permanent_property` 先按正式属性 ID 与精炼参数完整标识关联；名称简写时，只在每级第一参数和唯一
  无条件直接 Modifier 同时成立时采用结构关联。施加条件、来源/目标标签、同级多候选或精炼曲线不完整均不猜值，
  并进入构建审计。子 Buff 未声明本地 Modifier 时会沿正式 Blueprint `Super` 链读取最近父级 Modifier；当前审计为
  20 件已解析、5 件仅有条件候选、24 件没有直接计算证据。
  触发、层数、持续时间和状态限制效果继续由战斗状态链路处理。
- schema v36 将 20 件弧盘的 100 条闭合结果持久化，并据此重新生成 22 份毕业模板；运行时不读取仓库外
  `Content` 文件，也不新增账号配置或手工入口。

## 分层与集成接口

```text
ForkCatalogPage → ForkProfileView
    ↓
StaticCatalogForkService + StaticCatalogCharacterService（Qt 无关冻结 DTO）
    ↓
现有参数化只读 DAO（复用 StaticGameDataDao mode=ro/schema 校验）
    ↓
data/game_static.sqlite3
```

域模块本身不修改公共导航。集成任务需要：

1. 调用公开 `build_fork_catalog_page(database_path=..., game_ui_asset_root=...,
   terminology_service=...)` 创建独立页面；公共术语 Service 是必需注入，factory 从它加载一次冻结的
   campaign records，不创建第二个术语 DAO。factory 只组合现有只读 DAO/Service 和资源目录，不修改公共导航。
2. 把返回的 `ForkCatalogPage` 放入“游戏资料库”公共 Page，并在生命周期结束时调用 `dispose()`。
   页面会尝试关闭全部已拥有 Service；单个关闭失败不会跳过后续 owner，最终以 `ExceptionGroup` 暴露错误。
   已成功关闭的 owner 从待关闭集合移除，后续重试只调用失败项，全部完成后 `dispose()` 幂等。
3. 公共导航仍由集成任务一次性添加 `parent_key="toolbox"` 的“游戏资料库”子页；
   本域不访问 `MainWindow`、`toolbox/page.py` 或其他并行数据域。
