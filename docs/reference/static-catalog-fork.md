# 游戏资料库：弧盘数据域

本页记录“工具 → 游戏资料库”的弧盘域只读边界。它只消费发行静态库
`data/game_static.sqlite3`，不读账号库，不写静态库，不启动后台任务或游戏集成。

## 展示来源分类

| 标记 | 含义 | 弧盘域中的例子 |
| --- | --- | --- |
| `official_static` | importer 从正式数据集保留的字段 | 弧盘 ID、名称、等级 modify pack、突破消耗、精炼参数、Buff/GE 资产路径 |
| `project_projection` | importer 对正式行所做的项目结构化投影 | `combat_effect_definition`、`combat_effect_buff_link`、跨表关系跳转 |
| `derived_display` | Service 为界面组合的值，不是新的游戏事实 | 20/30/40/50/60/70 级“突破前/突破后”的等级行 + 突破阶段行组合、百分比格式化 |

界面不从中文说明猜数值。临界等级不做未标注的属性求和；详情同时保留该等级的
`fork_upgrade_level` / `fork_modify_value` 和对应阶段的 `fork_breakthrough` / `fork_modify_value`。

## schema v29 只读审计

当前发行 dataset 为 `cn_1_3_13_20260828`，importer 34。实际 schema 中可展示：

| 内容 | 权威表 / 字段 | 当前行数或覆盖 |
| --- | --- | --- |
| 弧盘/专武目录、品质、类型、资源 | `fork_item`, `fork_type` | 49 件、5 类；49 件均保留 icon/card/painting |
| 角色关联 | `exclusive_character_ids_json`, `character_cultivation_fork_recommendation` | 9 件含独占角色 ID；46 条养成推荐，覆盖 33 件弧盘、23 个角色 |
| 1–80 级成长、每级 `NeedExp` 与面板 modify pack | `fork_upgrade_level`, `fork_modify_pack`, `fork_modify_value` | 1,600 条共享成长行；每件弧盘都能解析 80 级 |
| 0–6 阶突破、等级上限、材料/货币、属性包 | `fork_breakthrough` | 343 条；每件 7 个阶段 |
| 弧盘技能/精炼 1–5 级、描述、消耗、占位参数 | `fork_star_level`, `fork_star_parameter`, `fork_refinement_parameter_value` | 245 级、720 个参数占位、975 个曲线值 |
| Buff 根资产、属性修改、事件触发、目标效果 | `buff_definition`, `buff_modifier`, `buff_trigger_effect` | 245 个根 Buff 定义、176 个根修改、594 个根触发 |
| GE 正式 ID/类路径 | `gameplay_effect_catalog` | 仅按精确资产路径关联根 Buff 和触发目标 |
| 来源追溯 | `source_row`, `source_file` | 行键、相对路径、行/文件 SHA-256；30,835 个 `payload_json` 均为 NULL |

弧盘行来源包括 `DataTable/Fork/DT_ForkItemData.json`、`DT_ForkUpgradeData.json`、
`DT_ForkBreakthroughData.json`、`DT_ForkUpgradeStarDataTable.json`、`CT_ForkBuff.json` 以及
`DataTable/PackData/ModifyData/DT_ForkModifyData.json`。

## 当前 importer 未保留

- schema v29 没有 `fork_skill` / `fork_skill_level` 表。产品中的“弧盘技能”只能展示精炼
  1–5 级描述、参数、Buff 与效果，不可声称另有独立技能目录。
- `source_payloads_omitted=true`；发行库不能还原原始 JSON 全行，只能展示 importer 保留字段、
  来源文件和校验值。
- 弧盘到 GA 没有结构化直接绑定。DAO 只在资产路径精确匹配时返回 GA；当前发行库
  审计为 0，不从描述文本、名称或路径前缀推测。
- 49 件弧盘中 33 件有独占角色 ID 或养成推荐关系，余下 16 件没有结构化角色关系；
  界面保持未解析，不按中文名、描述或效果类型猜专属角色。
- schema v29 没有通用养成材料目录；突破消耗可展示正式 item ID 和数量，但不能补出
  未保留的本地化材料名称。
- `combat_effect_definition` 和 `combat_effect_buff_link` 是 importer 正规化的项目投影；
  其中描述、参数和 Buff 路径仍可回溯正式行，但投影 ID 不标成官方原始字段。

## 分层与集成接口

```text
ForkCatalogWidget
    ↓ StaticCatalogForkService（不引入 Qt，返回冻结 DTO）
StaticCatalogForkDao（参数化固定 SQL，复用 StaticGameDataDao mode=ro/schema 校验）
    ↓
data/game_static.sqlite3
```

域模块本身不修改公共导航。集成任务需要：

1. 在组合根为当前发行静态库创建 `StaticCatalogForkService`，并在页面关闭时调用 `close()`。
2. 把 `ForkCatalogWidget` 放入“游戏资料库”公共 Page；目录筛选和 50 条分页由域 Widget 自理，
   详情只在选中弧盘后加载。
3. 连接 `relation_jump_requested(kind, target_id)` 到公共资料库路由器；
   域内回跳可调用 `select_fork(fork_id)`。正式 ID、Gameplay Tag 和资源路径均可通过
   详情树选中行或右键复制。
4. 公共导航仍由集成任务一次性添加 `parent_key="toolbox"` 的“游戏资料库”子页；
   本域不访问 `MainWindow`、`toolbox/page.py` 或其他并行数据域。
