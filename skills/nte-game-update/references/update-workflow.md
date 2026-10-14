# 版本更新工作流

## 来源与差异

规则归 `tools/game_data/README.md`、`docs/integrations.md` 与 `docs/reference/static-catalog.md`；
数值身份、参数和能力归当前代码/清单，不在 skill 固化 schema、importer 或组件版本。

- 记录用户/来源元数据支持的游戏版本、地区与批次，另记录本次 `dataset_id` 和 `as_of`。构建日期不是游戏版本证据。
- 正式构建器的 `catalog_characters.resolve_content_root` 只接收 Content 或其直接父目录。
  辅助脚本可在已知地区层次定位唯一根，随后把它返回的 **Content 根**传给数据及图片工具。
- `Content/DataTable` 存在只证明定位成功。核对所选 builder 的 `source_tables`、角色曲线/觉醒、Blueprint 和图片需求。
  同版多来源须明确优先级并登记逐文件来源；不跨版本或从旧发行文本/说明字符串填缺项。
- 当前 `_mirror_sources` 对缺失的 `text/ST_*.json` 支持同版 `Localization/Game/zh-CN/game.json` namespace。
  核对 `STRING_TABLE_SOURCES` 和实际结构后再决定转换/降级；旧登记路径缺失不等于新构建器缺输入。

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

每件新/变更弧盘先审精炼曲线、直接 Modifier 和无条件常驻证据；已证实、明确无常驻、仅条件、缺证据/歧义分别记录。
图鉴 builder 的 `fork_permanent_property_audit` 当前为 unavailable，不能把页面数值已存在解释成该审查闭合。
各功能再按 [适配矩阵](character-adaptation.md) 决定数据变更、代码接入或待支持。

核对公共 `game_ui/manifest.json` 的 `skills` 正式 SkillID 映射、同版显示/别名关联、PNG 与逐文件来源/输出 SHA-256，
覆盖全部可分享主动技能。当前 `GameUiAssetCatalog.skill_icon` 只按正式 ID 查 `skills`，没有按中文别名找图的旁路；
缺图或冲突在候选中解决，不留分享专用技能副本。无关素材、模型、源码和调试符号不进发行。

**纯技能图片维护**：只对已核验完整旧角色包制作隔离候选，运行
`build_role_skill_assets.py --source $source --candidate-dir $candidate`。
仅当数据库字节、dataset、scope、数据库 manifest 及其他资源组不变且获准安装时，增加 `--target-dir data/role_catalog`。
这是 CLI 调用 `promote_role_catalog.py::promote_role_skill_assets` helper，不是直接运行 `promote_role_catalog.py`；数据版本变化走整包晋升。

## 校验、晋升与回退

必要校验包括 SQLite 完整性/外键、正式关联、来源哈希、payload 省略、schema/importer、数值/机制审查与公共素材映射。
所选 dataset 和字节身份冻结到计算/预览；资料更新后旧结果重算，不重解释历史方案评分或原始战报。
测试按本次变更和授权选择少量公共行为；集中 core/full、组件与实机验收各有独立结论，不为 skill 维护执行真实更新。

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
不删除唯一备份、不用新版本配置验证旧包；主数据、图鉴/素材和外部组件分别恢复。

交付只留本批差异、原件/哈希、一个验证记录及受保护回退；来源原文与本机路径留本机，未知项指向所属功能或组件。
