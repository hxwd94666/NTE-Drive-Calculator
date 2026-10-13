# 版本更新工作流

## 权威入口与输入

先查仓库 `tools/game_data/README.md`、`docs/integrations.md` 的静态构建/晋升与公共游戏图片章节、
`docs/reference/static-catalog.md` 及对应领域文档。外部协议和正式分析组件按 `docs/integrations.md`，不从旧聊天推断当前版本。
如果 README 的旧数字或旧 runtime 说明与已通过行为、schema、代码冲突，核对实际实现，不复制过期结论。

Content 根需含 `DataTable`；本机常见导出层次为 `解包/CN/HT/Content`，也可能直接是 `Content`。
`catalog_characters.resolve_content_root` 当前只接收 Content 或其直接父目录，别把任意解包总目录直接传给构建器。
原始导出与构建器所需规范化来源分开判断：存在 Content/DataTable 只是定位成功，不代表文本表、战斗资源和图片已齐。
`source_file` 中 `combat_blueprint/` 是 importer 命名空间，`#` 可标记投影；路径解析按当前晋升入口语义核对，别误报缺几千个资源。
当前完整 importer 在 `text/ST_*.json` 缺失时，已可读取同版 `Localization/Game/zh-CN/game.json` 的对应 namespace，
并登记聚合文件哈希与 `#namespace` 来源。先核对当前 `_mirror_sources`、`STRING_TABLE_SOURCES` 和实际 namespace 结构；
旧 `source_file` 路径缺失不等于构建缺输入，不因此预先转换 text 或降级。只有现有入口确实不覆盖新格式时，才在隔离目录准备有证据的转换并记录来源；
不要从旧发行 text、说明字符串或别的地区静默补齐。
支持的地区以本次来源和词库为准；其他地区的名称、数字规则、图标或 OCR 不由界面语言自动选择。

先冻结旧发行 manifest/数据库和相关公共资产清单，记录 Git ref、哈希、schema/importer、dataset 与组件身份。
对比旧 `source_file` 的相对路径和 SHA-256，同时检查新表、新行、消失行、结构与资源映射变化。
本 skill 的审计脚本只覆盖旧已登记文件和新目录枚举；新增 Blueprint、GE、技能或素材仍须由现有导入规则和逐角色矩阵继续核对。
游戏版本身份来自用户/来源元数据；dataset_id 由本次配置明确记录，不把文件时间、构建日期或原库身份当新版本证明。

## 候选构建

命令参数每次核对当前 `--help`/parse_args。以下是在当前入口下的示例，变量取自本次核实的本机配置：

```powershell
$python = "<当前项目 Python>"
$source = "<核实的同版 Content 根>"
$dataset = "<本次 dataset_id>"
$asOf = "<本次来源日期 YYYY-MM-DD>"
$candidate = "build/<本次唯一候选>/distribution"
& $python tools/game_data/build_static_database.py `
  --source $source --output "$candidate/game_static.sqlite3" `
  --report-dir "$candidate/report" --dataset-id $dataset --as-of $asOf `
  --manifest "$candidate/manifest.json" --omit-source-payloads
if ($LASTEXITCODE -ne 0) { throw "静态候选构建未通过" }
```

完整构建入口目前已调用毕业模板和分析目录后处理；先看实际编排，别再重复调用或覆盖它们。
人工回退权重在 `character_weight_overrides.json`，特殊形态注解在 `character_overrides.json`；它们不是官方字段证据。
不要从旧发行库继承本应重新导入的角色额外形状、成长、觉醒或弧盘常驻属性。改变规范化语义时递增当前 importer；
schema 变化新增迁移，不改已发布迁移。缺来源先定位导出缺项/格式变化，别空表过关。

独立图鉴和公共图片是第二组交付，不是只在完整战斗来源不足时才构建的替代品。
当前 `ApplicationPaths.game_ui_asset_root` 指向图鉴公共图片，已安装图鉴也有独立消费者；即使主 game 库更新成功，仍检查并维护同版图鉴整包。
按当前产品用途选择以下图鉴入口，分别记录能力、候选和验收；仅交付图鉴时另列主战斗库缺口：

- `build_role_catalog.py --source $source --candidate-dir <build 内候选> --dataset-id $dataset --as-of $asOf`：角色页面目录。
- `build_reference_catalog.py` 同组参数：明确的 reference 图鉴；用途及计算权限按当前契约，不自动替代主战斗库。
- 图鉴数据库之后还须构建图片：role_page 用 `build_role_catalog_assets.py --source $source --database <候选库> --output <候选>/game_ui`；
  reference 用 `build_reference_catalog_assets.py --source $source --candidate-dir <候选>`。
  当前两类资产入口均调用技能图片步骤；缺图、来源冲突、缺正式技能关联在候选阶段处理。

## 素材与资源

角色/弧盘/材料/技能图片由正式 ID 和游戏路径关联；公共技能库统一供角色与配装分享使用。
核对全部新增/改动主动技能的正式 ID、别名、PNG、来源及 SHA-256，不保留 Bot/分享专用副本或用相似图替代。
单纯技能图片维护可复制现有完整角色包到隔离候选，调用 `build_role_skill_assets.py --source $source --candidate-dir <候选>`；
只有数据库字节、dataset、用途与身份清单完全不变，且当次允许安装时，才增加 `--target-dir data/role_catalog` 走图片专用入口。
数据版本变更走完整静态晋升，不借图片入口绕过数据库校验。

模型、额外语言资源、调试符号、源码包、无关素材不默认进入发行包。素材去重只合并字节相同资源且还原清单原路径；
体积预算以当前 `static_release_size_policy.py` 与打包门禁为准，不自动放行 size warning 或删除运行库。

## 核对、最终化与晋升

核对候选完整性/外键、主键与正式关联、每域覆盖/缺口、payload 省略、来源哈希、schema/importer 和最终报告。
每件新/变更弧盘审查精炼曲线、直接 Modifier 与无条件常驻属性；仅条件效果、明确无常驻、缺证据/歧义分别记录，
未经审查不标完备。完整静态与图鉴分开验证，并通过角色适配矩阵证明消费端行为。

正式配置为仓库外 `NTE_LOCAL_CONFIG`，字段和目标以当前晋升工具为准，skill 不保存本机路径或自动创建任意配置。
以下命令按序执行，任何一步非零就保留当前正式包并先处理该阶段原因：

```powershell
& $python tools/game_data/promote_static_release.py `
  --candidate-dir $candidate --local-config $env:NTE_LOCAL_CONFIG --finalize-only
if ($LASTEXITCODE -ne 0) { throw "候选最终化未通过" }
# 成功后再只读预检：
& $python tools/game_data/promote_static_release.py `
  --candidate-dir $candidate --local-config $env:NTE_LOCAL_CONFIG --verify-only
if ($LASTEXITCODE -ne 0) { throw "候选预检未通过" }
```

仅在本次明确允许正式替换且前述门禁通过时，另行执行：

```powershell
& $python tools/game_data/promote_static_release.py `
  --candidate-dir $candidate --local-config $env:NTE_LOCAL_CONFIG
if ($LASTEXITCODE -ne 0) { throw "正式晋升未通过；核对回退状态" }
```

role_page/reference 使用对应候选并显式增加 `--target-dir data/role_catalog`；不要晋升到主 data。
主库、manifest、目录图片各组和分析组件是不同交付，不用一个晋升结果冒充全部完成。
晋升前保留旧正式库/清单/资产包及身份；用已有一致性备份和暂存/原子切换流程，不复制活动 WAL/SHM 或手工覆盖 data。
如果工具缺完整回退交付，先补足或记录待维护者处理，不自造旁路安装。

## 验证与交付

按授权运行新/改动角色的公共行为专项、Ruff 硬门禁、`tools/quality/run_tests.py core/full`。
检查旧角色结果回归、切换账号/取消、已有存档与迁移、固定快照保存、三主题/文本布局及素材关联。
当前已暂挂失败保持原身份；新失败先复现和分类，不用删测试、跳过或假成功清除。
组件握手、声明能力、业务就绪、真实 Windows/游戏验收分别列状态；缺分析组件不回退 Python 私读战报库。
交付候选、变更差异、逐角色适配结论、命令证据、旧包哈希和可核对回退；未获授权不提交/推送/构建安装器/部署组件/操作游戏。
