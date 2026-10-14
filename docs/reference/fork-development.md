# Fork 开发：沿基础权重走通真实业务

本页是现有业务的阅读与修改路径，不增加教程平台或另一套生产入口。依赖准备、Windows 桌面基线、
公开 ref、启动和构建选择统一见 [README 本地开发](../../README.md)；分层与生命周期以
[系统架构](../architecture.md)为准，本文只解释如何沿实际文件完成一次修改。

## 1. 最小离线输入与资源边界

参考用例使用真实 `BasicWeightPage`、Controller、Service 和 SQLite DAO，所有可写输入由用例创建：

| 输入 | 来源与用途 |
| --- | --- |
| 合成静态库及匹配清单 | [共享夹具](../../tests/characters/configuration/fixture.py)复用正式 `SCHEMA_PATHS`，生成临时 SQLite 和 `manifest.json`；不复制发行库 |
| 临时账号库 | `UserDataDao` 按正式 schema/追加迁移新建；不读取真实账号或账号索引 |
| 词条标签与规则 | 公开 [config/stats.json](../../config/stats.json)；正式属性 ID 与显示标签分离 |
| 公开 Python/Qt 依赖 | 按 README 与锁文件准备；本例不安装驱动、不加载 OCR 或独立游戏组件 |

夹具只有四个角色、`CritBase`/`AtkUp` 两个属性和一个合成套装；两种主角正式 ID 使用
`available_avatar_variant` 关系，默认菜单角色为 `1051`，基础权重来源为 `1046`。显示名是合成文本。
夹具的 dataset 明示为 `basic-weight-synthetic-fixture`，清单带 `synthetic_fixture` 标记。
它只覆盖权重用例所需关系，不是发行静态、完整游戏资料、正式背包、伤害模型或新的游戏能力。

本例不需要图片、OCR 模型、截图、抓包、真实 UID 或 Core/DLL。转到完整应用、资料库、计算或识别前，
再按[静态构建说明](../../tools/game_data/README.md)与[资源/组件说明](../integrations.md)
取得并校验相应公开资源；不要用维护者其他目录、私有源码或假执行物补齐。分析、采集与游戏交互执行物
各有独立清单、哈希、能力与就绪检查，源码用例通过不提升这些能力。

## 2. 沿一次页面操作追踪责任

组合根 [src/ui/app.py](../../src/ui/app.py) 创建 `BasicWeightPage(app_context, navigate, changed, parent)`，
并注册账号生命周期。页面与正式导航之间的窄边界在
[configuration_controller.py](../../src/ui/controllers/configuration_controller.py)，不靠页号定位。

| 环节 | 实际入口与本层负责的内容 |
| --- | --- |
| 读取 | [page_owner.py](../../src/features/configuration/page_owner.py)的 `refresh/controller` → [editing/form.py](../../src/features/configuration/editing/form.py)的 `switch_config_form` → [controller.py](../../src/features/configuration/controller.py)的 `request_form_data`；读取后台执行，缓存按来源身份失效 |
| 冻结 | [dependencies.py](../../src/features/configuration/dependencies.py)一次取得账号、generation、用户库、配置及静态/共享路径，生成 Domain 的 [BasicWeightDependencies](../../src/domain/basic_weight.py)；不是读取时再找当前账号 |
| 组装目录 | [basic_weight/service.py](../../src/services/characters/basic_weight/service.py)的 `load_form_data` 解析官方/自建角色、有效权重及属性选择；官方目录经[正式静态读取单元](../../src/integrations/static_catalog_release.py)校验清单、字节与 dataset；开页不种账号默认记录 |
| 编辑 | `editing/form.py` 的 `save_role_weight_value` 等只改变本页草稿和 dirty ID 集合；key 为正式 `character_id`，同名角色不合并，同账号刷新不覆盖草稿 |
| 保存 | [editing/commit.py](../../src/features/configuration/editing/commit.py)的 `save_config_form` 深拷贝草稿及脏字段集合；Controller 的提交通道接受一次工作，忙时不再排一个重复保存 |
| 业务校验与持久化 | `BasicWeightService.save_changes` 校验全部角色、形状、底盘和套装后才开始业务写入；[character_weight_service.py](../../src/services/characters/character_weight_service.py)生成 Domain 的 `AccountWeightChange`；[UserDataDao](../../src/storage/sqlite/user_data_dao.py)在唯一连接的 `write_transaction` 中整批提交，内层保存点不提前提交 |
| 重置 | `reset_current_config_weights/reset_all_config_weights` → `reset_weights` → 同一账号事务恢复指定官方角色默认权重；主角共享来源去重，自建角色没有发行默认值；额外形状、底盘、套装不因重置权重改变 |
| 失败 | 事务/校验失败保留已保存事实；提交失败保留页面草稿并停止待处理导航。已经提交而投影失败是另一终态，只重载展示，不自动重新保存 |
| 离页与关闭 | `confirm_leave` 保持 Save/Discard/Cancel 交互；Save 只在提交终态后继续一次导航，Discard 只丢弃未保存草稿，Cancel 留页。`deactivate` 使读取失效；已接受写入先物理收尾，账号生命周期与退出不把它取消成未知写入 |

权重编辑本身没有库存 `snapshot_id`：它只写当前账号权重，不重算旧方案。进入分配/保存时，
由该业务另行冻结库存、角色顺序、权重、dataset、目标 `slot_id` 等输入，保存只消费相应预览。
已有方案的 `source_snapshot_id` 与 `assignment_scores` 仍是历史事实；不要把“权重现在变了”实现为
重写旧方案分数。完整冻结约定链接[仓库契约](../../AGENTS.md)，不在本页复制字段清单。

## 3. 一次可复现的输入 → 处理 → 结果与失败分支

在 README 已准备好的源码环境中，沿现有质量入口运行下面三个确切模块；`--no-sync` 不隐式安装依赖。

```powershell
$env:NTE_TESTING = "1"
$env:QT_QPA_PLATFORM = "offscreen"
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
uv run --no-sync python -B tools/quality/run_tests.py full -j 1 --pattern test_basic_weight_page.py
uv run --no-sync python -B tools/quality/run_tests.py full -j 1 --pattern test_basic_weight_commit_service.py
uv run --no-sync python -B tools/quality/run_tests.py full -j 1 --pattern test_configuration_async.py
```

| 用例 | 输入、处理与可检查结果 |
| --- | --- |
| [真实页面读/编辑/保存](../../tests/characters/configuration/test_basic_weight_page.py) | 创建与 `1051` 同名的自建角色；通过实际权重控件输入 `CritBase=1.55` 并点击保存；用真实 DAO 确认仅该自建 ID 写入 `1.55`，官方共享来源没有新增覆盖；同模块还验证官方重置及自建角色创建/删除 |
| [整批成功与数据库回退](../../tests/characters/configuration/test_basic_weight_commit_service.py) | 输入角色 A 的 `CritBase=2.25` 与角色 B 的形状/底盘/套装；正常分支确认同批字段提交。失败分支在临时库创建 `RAISE(ABORT, 'fixture SQL failure')` 触发器，确认较早写入的 A 权重也回退；多角色重置的后段 SQL 失败保留全部原覆盖 |
| [冻结、失败保草稿与离页](../../tests/characters/configuration/test_configuration_async.py) | 受控 worker 延迟/失败响应验证保存冻结副本、重复保存拒绝、失败保留权重草稿并不导航、Save 只继续一次、Cancel/Discard 及账号换代丢弃旧展示确认；这里的延迟/失败响应是测试注入，不称为真实组件或游戏验收 |

第一项最小修改可以直接把页面用例中的输入和对应 DAO 断言 `1.55` 同时改为 `1.75`，再运行同一模块；
保留同名正式 ID 分离与官方来源未写入的断言。它验证实际页面到持久化，不靠打印预期值通过。
修改生产规则时则进入下节对应责任入口，不以改测试期望掩盖生产错误。

当前源码切片的隔离复现记录见[架构验证](../validation/architecture-renewal-day1.md)。隔离运行须记录
新进程实际 `src`/`tools`/测试包的导入路径来自明确记录的源码根，第三方包来自按锁文件 wheel 哈希
安装的新虚拟环境，而不是旧 editable 安装、另一工作区或复用的 site-packages。最小参考依赖的安装方式
只在 README 维护；参考环境不是完整产品环境，基线 ref 与未提交工作树 overlay 分开记录。
公开 ref 的全新 checkout、完整应用和资源前置仍各自验收。
现有质量入口的源码卫生检查直接询问 Git：运行数据须被忽略，`src/`、`tools/`、`tests/` 中现存 Python 源码不得被规则隐藏。
它同时覆盖已跟踪文件和新文件；导出的无 Git 副本跳过该项。此检查不代替资源清单、动态入口或干净 checkout 的运行验收。
离屏专项、core/full、真实 Windows 窗口、真实组件及发行构建分别计证据，不互相替代。

## 4. 五类常见修改配方

下列是实际扩展入口，不是本轮已经执行的功能改动。每次仅修改所属数据域，保留相关公共行为检查；
质量参数、依赖与启动方式继续使用前述唯一入口，不另造脚本平台。

### 4.1 角色资料与默认权重

- **入口：** [角色规范化](../../tools/game_data/static_database_character_imports.py)、
  [角色关系注解](../../tools/game_data/character_overrides.json)、
  [推荐权重导入](../../tools/game_data/static_database_equipment_imports.py)及
  [既有权重回退条目](../../tools/game_data/character_weight_overrides.json)。账号个人权重则沿本页完整业务，
  由 `character_weight_service.py` 写账号库，不编辑工具默认值。
- **输入/输出：** 已确认的角色/属性关系与来源证据 → `build/` 中规范化候选的角色/推荐权重；
  账号显式覆盖优先，未修改默认记录可随公共默认更新。官方面板静态事实与个人配置是不同数据域。
- **身份边界：** 以 `character_id`、逻辑角色和 `property_id` 连接；主角显示 ID 与权重来源 ID 分开。
  名称修改只影响展示，不生成新的角色身份，不改库存/方案快照。
- **最小例：** 在已审计的 `1072` 回退条目中把 `{"property_id":"CritBase","weight":1.0}`
  调为 `weight:1.1`；这是项目推荐权重回退，不把它描述成官方属性。经静态候选/晋升链处理，
  不直接写 `data/`。若改规范化语义，同时更新 importer 身份。
- **关键检查：** [正式 ID 与缺项默认解析](../../tests/test_recommended_weights.py)、
  [默认更新/账号覆盖/重置](../../tests/characters/test_character_weight_service.py)、
  [主角基础权重契约](../../tests/characters/test_protagonist_base_weight_contract.py)，以及本页同名 ID/保存用例。

### 4.2 评分规则

- **入口：** 唯一公式入口 [ScoringEngine](../../src/optimizer/scoring.py)的
  `calculate_drive_score/calculate_cartridge_score`；
  [冻结对比评分适配](../../src/services/allocation/comparison_scoring.py)负责正式装备字段转换，不另写公式。
- **输入/输出：** 冻结词条目录、装备形状/品质、基础权重和主词条权重 → 单件分数；
  零权重与虚拟占位语义保留，真实 UID/`slot_id` 不因评分改变。
- **最小例：** 若需求明确调整紫品质系数，只在 `ScoringEngine.quality_map` 中把 `"Purple": 0.8`
  改为目标值；不要同时在卡片、倒带或计算分支再乘一次。常规参数调整不等于新增伤害求解策略。
- **冻结边界/检查：** 新对比两侧用同一次冻结口径，旧 `assignment_scores` 不覆盖；保留
  [权重与评分冻结](../../tests/allocation/test_allocation_scoring_freeze.py)、
  [历史评分不改写](../../tests/allocation/test_allocation_comparison_scoring.py)和
  [逐件分数保存](../../tests/allocation/test_allocation_saved_assignment_scores.py)。

### 4.3 接入页面与显式接线

- **入口：** 以 `features/configuration/page_owner.py` 为真实页面模板；Controller 只持有任务、取消、缓存与投影，
  Service 编排业务，Domain 放 DTO/纯规则，DAO 独占 SQL。组合根在 `src/ui/app.py` 明确创建并注入依赖。
- **最小接线参照：** `BasicWeightPage(app_context, navigate, changed, parent)`；
  经 `register_page_configuration_lifecycle(app_context, owners_provider, after_stop=page.clear_projection)`
  注册 owner，读 owner 状态不触发创建/建库。导航项在 [navigation.py](../../src/ui/navigation.py) 的 `NAV_ITEMS`
  声明 key/`parent_key`，由 [main_window_navigation_mixin.py](../../src/ui/main_window_navigation_mixin.py) 的 `_build_ui`
  接入组合根已创建的页面；既有基础权重 key 为 `config`、parent 为 `my_role`，不复用任意页号。
- **输入/输出与边界：** 当前上下文一次冻结 → 普通 DTO/页面投影或持久化终态；
  页面草稿不是数据库事实，账号换代前停止旧 owner，迟到结果不写新账号。自建 Page 不读取其他 Feature 私有控件，
  不给 MainWindow 动态挂草稿字段，不把现有 Service 包一层再保留旧入口。
- **关键检查：** 本页三组用例，另看
  [公开账号生命周期](../../tests/characters/configuration/test_configuration_account_lifecycle.py)与
  [owner 物理寿命](../../tests/characters/configuration/test_basic_weight_owner_lifetime.py)；Qt 离屏通过仍需真实窗口的键盘/DPI 验收。

### 4.4 界面语言补丁

- **入口：** [中文基础目录](../../assets/locales/zh-CN.json)、
  [TextCatalog](../../src/domain/localization/catalog.py)、
  [语言 Service](../../src/services/preferences/language_settings_service.py)和
  [原子补丁文件边界](../../src/integrations/preferences/locale_files.py)。
- **最小例：** 部分补丁只翻译 `settings.language.save`，或以相同 `{language}` 参数翻译
  `settings.language.saved`；安装补丁后保存选择，下次启动生效。完整 JSON 身份和安装流程唯一见
  [界面语言补丁](../features/localization.md)，这里不复制格式表。
- **输入/输出与边界：** UTF-8 文本包 → 已校验显示目录及本机 `locale` 偏好；缺 key 回退中文，
  参数/重复 key/路径/身份错误保留旧有效文件。正式角色 ID、协议字段、日志码和账号库不翻译，
  本机偏好不随账号导入导出。运行中的目录保持冻结，不触发 OCR 模型切换。
- **关键检查：** [语言偏好公共用例](../../tests/preferences/test_language_settings_service.py)的部分目录回退、
  坏包/原子替换失败、参数及跨账号状态；真实长文案另用
  [伪语言样例](../examples/locales/qps-ploc.json)验收，不将伪语言称为外语发布。

### 4.5 更新游戏静态资料

- **入口：** [build_static_database.py](../../tools/game_data/build_static_database.py)及所属字段导入模块；
  资料不足时按正式用途选 `build_role_catalog.py/build_reference_catalog.py` 与配套图片构建器；
  只经 [promote_static_release.py](../../tools/game_data/promote_static_release.py)晋升。逐角色接入由
  [版本更新 skill](../../skills/nte-game-update/SKILL.md)和[静态构建说明](../../tools/game_data/README.md)维护。
- **输入/输出：** 唯一确认的 Content 根、本机来源配置、逐文件哈希及规范化语义 → `build/` 候选的
  SQLite、清单、报告与公共图片；核对后整套晋升，不把 Content 全量复制为规范化数据，不写账号库。
- **最小例：** 为一项已证实的新增正式 `property_id` 在对应导入模块补映射，使用同版源文件重建隔离候选；
  然后对该候选执行正式入口的 `--finalize-only` 与 `--verify-only`。构建/晋升完整 argv 只在上述构建说明维护，
  本文不另抄一套；实际晋升与恢复旧数据库/清单是一对独立操作，不由示例测试代做。
- **身份/快照边界：** dataset、schema/importer、来源哈希、图片关联都闭合；新增角色的全部可分享技能使用正式
  SkillID/图标路径，同步更新来源与 SHA 清单。新的静态身份令旧预览失效，历史方案/战报仍保持自己的来源。
- **关键检查：** [静态规范化与关系](../../tests/test_static_game_database.py)、
  [成对晋升、来源失配与回退](../../tests/test_static_release_promotion.py)；
  合成权重夹具只测试其消费关系，不证明新版完整数据、图片、原生同步或游戏验收完成。

## 5. 未来接入与本次证据的限制

新语种 OCR 仍需对应识别模型/字典、别名到正式 ID、布局模板、冻结/缓存身份及真实样本；
职责见[功能内语言预备](../roadmap/architecture-post-refactor-review.md#鉴定与扫描的功能内语言预备)，
不由界面补丁推断支持。新伤害分配沿[伤害备忘](../roadmap/damage-allocation.md)后续开发，
本例不启用搜索器、不提供假伤害结果。

修改前记录当前 ref、工作区和实际输入。改后保留本批原件/hash、diff、确切命令/输入/字面结果与退出状态、
以及只覆盖本批确切文件的回退；不要用清空工作区恢复，也不把其他未提交工作当原件。
已有公开 ref 未完成全新 checkout/锁定安装/完整资源验收时，继续保留明确待验收状态；维护者确认 ref 后，
在该干净检出与 README 所述环境中运行第 3 节现有质量命令，再按实际结果记录。
由未参与重构的开发者按文档完成约定修改，核对其能找到实际入口和验证结果，作者自测不替代这项验收。

## 6. 接口关键说明模板

新增、迁移与保留实现只解释接口确有的业务责任，不给每个短函数粘贴整块模板：

```text
模块摘要：本模块负责什么；哪些责任交给其他层。
接口约定：输入/输出、单位、正式身份与未知值；调用线程和副作用。
关键原因：这个约束或分支为何存在；删除会破坏什么行为。
生命周期：状态如何推进；取消、失效、关闭时由谁收尾。
一致性：事务范围、缓存失效、失败保留什么；为何允许或禁止重试。
验证依据：对应公共行为用例，必要时链接权威领域文档。
```

例如本页保存接口需解释“先校验全部角色再提交；事务失败保留草稿；提交后投影失败只重载，不再写入”；
异步回调需解释“账号代次失效即丢弃结果，不用新账号补齐旧请求”。已有说明准确时保留，失效说明删除，
不从函数名猜测并发或失败策略。技术标识、协议与第三方许可保持原名。
