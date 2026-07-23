# 词条权重配装重构：Terra 交接文档

更新日期：2026-07-22

适用模型：GPT-5.6 Terra，建议先使用高推理

仓库：`.`

分支：`2.0.0`

当前提交：`907fa14 fix(data): 合并 schema v10 战斗数据`

## 1. 本次接手目标

近期不以伤害模拟为主线。先把现有 2.0 重构后的测试基线恢复，再建立“词条权重配装”所需的用户偏好、计算上下文和页面边界。

产品路线已经确定为：

```text
硬约束
  -> 词条权重快速搜索
  -> 保留 Top-K 候选方案
  -> 未来使用伤害模型重排
  -> 局部换件或套装调优
  -> 保存最终方案
```

“配装后计算伤害”指对尚未保存、尚未实际装配的内存候选方案计算伤害，不是调用一键装配后再验证。

当前第一任务不是开发新 UI，而是恢复完整测试基线并确认版本重构边界。

## 2. 必须先读取的项目约束

开始工作前使用 `nte-drive-calculator-dev` 技能并完整读取：

- `nte-drive-calculator-dev` 技能说明
- `nte-drive-calculator-dev` 的数据与 SQLite 参考
- 涉及页面时读取 `nte-drive-calculator-dev` 的 UI 与发布参考
- 涉及保存方案或一键装配时读取 `nte-drive-calculator-dev` 的同步与装配参考

相关项目文档：

- `docs/architecture.md`
- `docs/2.0.0-handoff.md`
- `data/README.md`
- `tools/game_data/README.md`

注意：`docs/2.0.0-handoff.md` 中“下一步先研究直接伤害公式”的建议已被本次决策覆盖。伤害计算仍然保留，但不再作为当前优化器重构的前置任务。

## 3. Git 与工作区状态

本地 `HEAD` 与 `origin/2.0.0` 均为：

```text
907fa14 fix(data): 合并 schema v10 战斗数据
```

近期相关提交：

```text
907fa14 fix(data): 合并 schema v10 战斗数据
8d8c07c Merge remote-tracking branch 'origin/2.0.0' into 2.0.0
96bb22c feat(data): 导入战斗与 Abyss 静态数据
c5d4f37 feat: 导入官方角色与技能静态数据
4436544 wip：仓库功能
```

当前有四个未跟踪文件：

```text
docs/damage-calculation-handoff.md
docs/damage-calculation.md
src/services/damage_calculation_service.py
tests/test_damage_calculation_service.py
```

这些文件属于之前暂缓的伤害计算工作。除非用户明确要求，否则：

- 不删除；
- 不覆盖；
- 不格式化；
- 不暂存；
- 不提交；
- 不以它们作为当前重构前置条件。

提交、推送、删除、强制 Git 操作和真实游戏装配均需用户明确授权。

## 4. 已完成的数据基础

静态数据库：`data/game_static.sqlite3`

静态 schema：v10

导入器版本：v10

SHA-256：

```text
C88694E197F0EFF32E1F76658DB34842C0185F79BF0976E2F9ABFFE02901D1BA
```

当前数据已经包括：

- 22 个官方角色；
- 角色觉醒、等级成长、突破前后面板；
- 84 个技能、756 条技能等级规则；
- 802 条原始技能伤害记录；
- 装备属性、形状、套装、装备项和官方蓝图；
- 弧盘目录、升级、突破、星级和词条包；
- 倾陷、环合等战斗等级曲线；
- 敌方战斗属性、怪物实例及等级变体；
- Abyss 关卡、波次和怪物池。

重要语义：

- `FT_` / `DT_MonsterPackData_FT` 是“999 夜”子玩法，不是“轨外之境”。
- 999 夜的额外能力配置不进入普通角色功能。
- Abyss 的怪物属性绑定普通 `DT_MonsterPackData`，即 `standard` 档案。
- 失谐不直接产生伤害，当前无需单独研究其伤害公式。
- `Player_056_bat` / 角色 1056 是安魂曲 1004 的技能形态，不是独立普通角色。

数据相关定向测试此前已通过 33 项，`compileall` 和 `git diff --check` 也通过。完整测试的失败需要按下面的基线任务重新处理。

## 5. 当前数据库版本边界

### 静态数据库

- `src/storage/sqlite/static_game_data_dao.py`：`SCHEMA_VERSION = 10`
- `tools/game_data/build_static_database.py`：`SCHEMA_VERSION = 10`、`IMPORTER_VERSION = 10`
- 静态库只读，发行时随程序提供。

### 用户数据库

- `src/storage/sqlite/user_data_dao.py`：`SCHEMA_VERSION = 4`
- 已发布迁移到 `005_user_data_v4.sql`
- 因此下一个用户数据库迁移应是 **v5**，不是 v6。

此前讨论中曾口头称“用户库 v6”，这是基于错误版本假设；接手后必须以当前源码 v4 为准。不要跳过 v5，也不要修改已发布迁移的语义。

## 6. 当前配装链路

现有计算入口：`src/features/allocation/runner.py::_run_allocation`

当前链路大致为：

```text
Allocation UI
  -> 固定当前 user_data.sqlite3 稳定 snapshot_id
  -> SqliteAllocationInventory.build()
  -> 把官方 SQLite 快照投影成旧求解器内存格式
  -> NTEAppFacade.execute_allocation_inventory()
  -> 旧 orchestrator / scoring / solver
  -> final_plan
  -> SavedStateLoadoutBridge
  -> loadout_plan
```

已有的正确边界：

- 计算不再回退读取旧背包 JSON；
- 计算固定一个 `snapshot_id`；
- 结果通过 `SavedStateLoadoutBridge` 保存到用户 SQLite；
- 保存时携带官方角色 ID、原生装备 UID 和来源快照；
- `SqliteLoadoutOptimizer` 已提供官方 ID 的单角色基线入口；
- `src/features/blueprints/page.py::solve_blueprints_from_static()` 已证明新页面可以不读取 `roles.json`。

仍然存在的兼容债务：

- `SqliteAllocationInventory` 用 `_STAT_NAMES` 把官方 `property_id` 转换成中文词条名；
- `_SHAPE_IDS` 把官方 `geometry` 转换成旧项目形状名；
- `src/ui/app.py` 仍加载 `roles.json`、`sets.json`、`shapes.json`；
- `src/solver/orchestrator.py` 仍直接读取 `roles.json`、`sets.json`、`shapes.json`；
- `src/optimizer/scoring.py` 仍依赖旧角色权重配置；
- 旧配置页面仍围绕 `roles.json` 和 `sets.json` 工作。

这些适配层暂时可以保留，但不能继续成为新页面和新算法的权威模型。

## 7. 当前完整测试基线

2026-07-22 重新执行：

```powershell
py -3.11 -m unittest discover -s tests
```

结果：

```text
Ran 622 tests
FAILED (failures=9, errors=27)
```

这比旧记录的 9 failures + 9 errors 多出 18 个 error。新增错误大多出现在静态数据库升级到 v10 后仍使用旧结构测试夹具的 SQLite 链路，但必须逐项确认，不能统一归类或盲目放宽校验。

### 27 个 errors 的分组

- 1 个 `test_blueprint_sqlite_solver`；
- 6 个 `test_drive_assembly_ui_bridge`；
- 3 个 `test_role_execute_workflows` 保存/已保存装备测试；
- 12 个 `test_saved_state_loadout_bridge`；
- 1 个 `test_sqlite_allocation_inventory`；
- 2 个 `test_sqlite_loadout_optimizer`；
- 2 个 `test_vision_inventory_snapshot`。

已知线索：

- 多个测试夹具声明静态 schema v3，运行代码现在严格要求 v10；
- drive assembly UI 测试宿主缺少 `_reload_equipped_state_from_disk`；
- `_save_alloc` 现在要求 `_pending_allocation_snapshot_id`；
- 测试宿主触发 `QMessageBox` 时可能不是有效 QWidget；
- 某个旧测试仍期望 `runner.import_all_role_equipment`；
- 某些测试可能没有关闭 SQLite 连接，Windows 退出时出现临时数据库文件被占用。

### 9 个 failures

- `test_navigation_items_have_stable_keys_and_indexes`
- `test_result_diff_card_hydrates_removed_item_from_inventory_file`
- `test_runner_builds_plan_diff_before_rendering_results`
- `test_saved_equipment_grade_uses_full_350_score_even_without_tape`
- `test_saved_equipment_refresh_passes_change_marker_to_cards`
- `test_saved_equipment_refresh_reloads_state_from_disk`
- `test_saved_equipment_refresh_renders_in_batches`
- `test_build_drive_options_sorts_candidates_and_marks_users`
- `test_parser_consumes_first_capture_before_scan_finishes`

判断原则：

1. 先定义 2.0 当前应该保留的业务行为；
2. 再判断是生产代码退化、兼容层遗漏、测试宿主过时，还是测试预期已经失效；
3. 不要只为绿灯修改断言；
4. 不要在修复基线时顺便重写优化器。

## 8. Terra 的第一阶段任务：恢复基线

建议 Terra 高推理只先完成这一阶段。

### 操作顺序

1. 读取技能、本文档、Git 状态和当前分支。
2. 重跑完整测试并保存精确失败分类。
3. 先处理共因问题，例如旧静态数据库 fixture 与 v10 校验不兼容。
4. 分别检查 UI 测试宿主、保存方案、结果刷新、导航、排序和流式管线。
5. 对每个修改写最贴近问题的测试，避免全局兼容补丁掩盖真实错误。
6. 恢复完整测试为绿色，或明确列出无法在当前授权内解决的真实阻塞。
7. 只在基线稳定后输出下一阶段设计，不直接开发优化器页面。

### 基线阶段交付内容

- 每组失败的根因分类；
- 修改过的文件；
- 哪些是生产代码修复，哪些是测试迁移；
- 完整测试结果；
- 是否存在 SQLite 连接泄漏；
- 是否具备开始用户库 v5 的条件。

## 9. 第二阶段设计：用户库 v5 优化器偏好

只有在测试基线恢复后再实施。

用户库缺的主要不是更多官方静态数据，而是每个账号自己的优化目标。v5 应只承载“词条权重配装偏好”，不要同时塞入完整角色成长和伤害模拟。

建议覆盖：

- 优化配置档案及其版本；
- 官方 `character_id`；
- 官方 `property_id` 权重；
- 目标 `suit_id`；
- 不要求套装、二件套、四件套等模式；
- 核心主词条过滤；
- 副词条优先级；
- 暴击率等属性上限或阈值；
- 角色顺序、优先级分组和分配策略。

数据库设计要求：

- 新增迁移，不修改 v1-v4；
- 新库初始化和 v4 -> v5 迁移都要测试；
- 静态 ID 存储在列中，中文只用于展示；
- 偏好可以修改，但一次计算必须固定配置档案及其版本；
- 不把 `roles.json`、`sets.json` 或 `my_roles_model.json` 内容复制成新权威数据。

具体采用规范化子表还是带版本的 JSON 载荷，应先比较查询、迁移和可验证性后再决定，不要直接照抄旧配置结构。

## 10. 第三阶段设计：不可变 AllocationContext

建议新增统一的服务层计算输入，示意如下：

```text
UI
  -> AllocationService
      -> StaticGameDataDao
      -> UserDataDao
      -> immutable AllocationContext
          -> candidate generation
          -> allocation solver
          -> scoring model
      -> loadout_plan
```

`AllocationContext` 至少应固定：

- 静态数据集标识和 schema 版本；
- 用户数据库账号范围；
- `snapshot_id`；
- 优化配置 ID 和版本；
- 求解器版本；
- 角色列表、顺序和分组；
- 每个角色的官方 ID 偏好；
- 不可变装备候选集；
- 本次计算的硬约束。

后台背包同步可以继续产生新快照，但不得改变正在运行的 `AllocationContext`。

旧求解器应先挂在适配器后面，避免同时重写 UI、数据模型和求解器。

## 11. 第四阶段：新的“词条权重配装”页面

第一版页面不应宣传“伤害最优”，建议名称就是“词条权重配装”。

页面建议分为：

1. 当前固定背包快照展示；
2. 角色选择、顺序和优先级分组；
3. 每角色套装、主词条、词条权重和上限配置；
4. 全局分配策略；
5. 后台计算进度；
6. 结果差异、得分解释和候选比较；
7. 保存到 `loadout_plan`。

硬性要求：

- 新页面不直接读取 `config/*.json`；
- 长计算放工作线程，不阻塞 PySide 主线程；
- 同一数据集、快照、配置版本和求解器版本必须得到可复现结果；
- 保存方案记录来源快照、静态数据集、偏好配置和求解器版本；
- 扫描、同步和真实装配仍归首页、设置和执行链路，不堆进优化器页面。

## 12. 第五阶段：Top-K 与伤害重排

权重优化器需要改进，但不需要等待完整伤害公式。

近期先做到：

- 区分硬约束和软权重；
- 支持属性阈值和溢出惩罚；
- 独立计算套装贡献；
- 输出 Top-K，而不是只返回一个黑箱答案；
- 输出每个候选的选择理由和属性差异；
- 多角色统一分配时保证装备 UID 不重复。

未来评分层可以抽象为：

```text
WeightScorer  -> 当前快速候选生成
DamageScorer  -> 未来真实伤害重排
HybridScorer  -> 权重筛选后做伤害比较
```

伤害模型成熟后，应在保存或实际装配前对内存候选计算伤害。不能先分别求每个角色的最佳方案再拼接，因为全角色之间存在装备竞争。

## 13. 当前明确暂缓

- 不继续研究“失谐伤害”；
- 不优先补齐 999 夜子玩法数据；
- 不把完整伤害模拟作为新页面前置；
- 不在同一任务中同时重写用户数据库、求解器、UI 和一键装配；
- 不删除旧 JSON 兼容链路，直到新页面达到功能对等且有回归测试；
- 不做真实游戏装配、安装器发布或插件加载。

## 14. 验收与验证

源码改动至少执行：

```powershell
py -3.11 -m unittest <最贴近改动的测试模块> -v
py -3.11 -m unittest tests/test_encoding_guard.py
git diff --check
```

基线修复结束必须执行：

```powershell
py -3.11 -m unittest discover -s tests
py -3.11 -m compileall -q src tests
git diff --check
```

如果测试运行生成安装器脚本或临时文件，结束前检查 `git status --short`，不得误提交生成物或那四个伤害计算文件。

## 15. 可直接复制给 Terra 的提示词

```text
接手 NTE-Drive-Calculator 2.0.0 的词条权重配装重构。

仓库：.
分支：2.0.0
当前提交：907fa14

请使用 nte-drive-calculator-dev 技能，完整阅读：
1. 技能 SKILL.md；
2. references/data-and-sqlite.md；
3. docs/allocation-refactor-terra-handoff.md；
4. docs/architecture.md。
涉及 UI 时再读 references/ui-and-release.md；涉及方案保存或装配时再读 references/sync-and-equipment.md。

先检查 git status、当前分支和 HEAD。保留工作区已有修改。

四个未跟踪的伤害计算文件属于暂缓工作，不得删除、覆盖、格式化、暂存或提交：
- docs/damage-calculation-handoff.md
- docs/damage-calculation.md
- src/services/damage_calculation_service.py
- tests/test_damage_calculation_service.py

本任务只做第一阶段：恢复完整测试基线，并给出下一阶段边界结论。不要开始开发新的优化器 UI，也不要实现伤害模拟。

当前已知完整测试结果是 622 项，9 failures + 27 errors。请重新运行确认，并按以下类别逐项判断：
- 生产代码真实退化；
- 重构遗漏的兼容行为；
- 静态库 v10 后过时的测试夹具；
- 过时的测试预期；
- PySide 测试宿主问题；
- SQLite 连接生命周期或 Windows 文件锁问题。

不要为了绿灯盲目修改断言。先确定 2.0 的正确业务行为，再做最小修复。尤其关注：
- 测试夹具的静态 schema v3 与当前 v10 校验；
- drive assembly UI bridge；
- _save_alloc 对固定 snapshot_id 的要求；
- saved state/loadout bridge；
- saved equipment 刷新、评分和批量渲染；
- navigation warehouse 项；
- replacement candidate 排序；
- streaming scan pipeline。

修复完成后运行：
- 最贴近问题的测试；
- tests/test_encoding_guard.py；
- 完整测试；
- python compileall；
- git diff --check。

最后汇报：
1. 每组失败的根因；
2. 修改了什么；
3. 哪些属于代码修复，哪些属于测试迁移；
4. 完整验证结果；
5. 当前是否适合开始用户数据库 v5 的优化器偏好迁移；
6. 下一步 AllocationContext 的建议边界。

不要 commit 或 push，等待我审核。
```

## 16. 第一阶段实际完成记录（2026-07-22）

第一阶段已完成：完整测试基线已经恢复，未开发新的优化器 UI，未实现或修改伤害模拟，未执行真实游戏装配，未提交或推送。

### 工作区与提交边界

- 当前分支仍为 `2.0.0`，基础提交仍为 `907fa14`；本阶段工作尚未提交。
- 以下四个伤害计算文件保持未跟踪、未修改、未暂存，仍不属于当前阶段：
  - `docs/damage-calculation-handoff.md`
  - `docs/damage-calculation.md`
  - `src/services/damage_calculation_service.py`
  - `tests/test_damage_calculation_service.py`
- 本交接文档也是未跟踪文件；它记录当前工作，不应在未经用户确认时单独提交。

### 失败根因与处理

| 原失败组 | 根因分类 | 最小处理 |
| --- | --- | --- |
| 18 个 SQLite 链路 errors：蓝图、保存方案、库存投影、SQLite 优化器、视觉快照 | 过时/不隔离的测试环境 | 本机 `NTE_GAME_STATIC_DB` 指向项目外 schema v3 开发库；发行库本身为 v10。相关测试显式固定项目 `data/game_static.sqlite3`，保留 DAO 对显式环境变量的正常运行时语义。 |
| 6 个 drive assembly UI bridge errors | 过时的 PySide 测试宿主和旧入口预期 | 旧测试依赖已移除的 `_reload_equipped_state_from_disk` 和旧自动装配入口；迁移为当前的装配方式选择、SQLite 方案和返回页面行为测试。 |
| `_save_alloc`、保存方案与结果刷新 errors/failures | 2.0 重构后的正确边界与旧 JSON 测试预期冲突 | 测试改为验证必须绑定 `snapshot_id`、不回退导入旧装备 JSON，以及将 SQLite 已保存方案投影后再渲染。 |
| navigation、流式扫描、差异卡片 failures | 过时测试预期 | 仓库页已成为稳定导航项；扫描不再导出旧 JSON；结果差异卡片不再从旧库存 JSON 回填已移除装备。 |
| replacement candidate 排序 failure | 生产代码真实退化 | `build_drive_replacement_options()` 曾按不完整的伤害边际排序；已改回按现有词条权重分数降序排序，伤害重排留给未来带固定上下文的评分层。 |

此前 Windows 临时 `user.sqlite3` 文件锁出现在 static DAO 创建失败后，测试 `setUp` 未能进入 `tearDown`，导致临时用户 DAO 未关闭；不是已验证的运行时 DAO 连接泄漏。基线恢复后的完整测试未再输出该文件锁清理异常。

### 已修改文件

生产代码修复：

- `src/features/role/replacement_service.py`

测试迁移/隔离：

- `tests/test_blueprint_sqlite_solver.py`
- `tests/test_drive_assembly_ui_bridge.py`
- `tests/test_extension_support.py`
- `tests/test_role_execute_workflows.py`
- `tests/test_saved_state_loadout_bridge.py`
- `tests/test_sqlite_allocation_inventory.py`
- `tests/test_sqlite_loadout_optimizer.py`
- `tests/test_streaming_scan_pipeline.py`
- `tests/test_vision_inventory_snapshot.py`

### 验证结果

```text
py -3.11 -m unittest discover -s tests
Ran 622 tests
OK

py -3.11 -m unittest tests/test_encoding_guard.py
Ran 16 tests
OK

py -3.11 -m compileall -q src tests
OK

git diff --check
OK
```

结论：基线已满足开始用户数据库 v5“词条权重配装偏好”设计和不可变 `AllocationContext` 设计的前提；下一阶段仍应先做数据模型、迁移和服务层边界，不启动新优化器 UI，也不引入伤害模拟。

## 17. 可直接交给 Sol high 的审计提示词

```text
请以 high 推理强度审计 NTE-Drive-Calculator 2.0.0 当前未提交的“词条权重配装重构第一阶段”改动，并在审计后给出下一阶段目标建议。

仓库：.
分支：2.0.0
基础 HEAD：907fa14

先使用 nte-drive-calculator-dev 技能，完整阅读：
1. SKILL.md；
2. references/data-and-sqlite.md；
3. docs/allocation-refactor-terra-handoff.md（尤其第 16、17 节）；
4. docs/architecture.md。
如需评估 UI 或装配测试，再读 ui-and-release.md 与 sync-and-equipment.md。

本次是审计，不要修改文件、不要格式化、不要暂存、不要提交、不要推送、不要执行真实游戏装配。

必须保留以下未跟踪的伤害计算文件，且不得以其作为审计或下一阶段前置：
- docs/damage-calculation-handoff.md
- docs/damage-calculation.md
- src/services/damage_calculation_service.py
- tests/test_damage_calculation_service.py

审计重点：
1. 检查 git diff，区分唯一的生产修复与测试迁移；确认 replacement_service 的候选排序恢复为词条权重是否符合当前产品路线，且没有偷偷引入伤害重排依赖。
2. 审查 v10 静态库测试隔离：测试应固定项目发行库，而 DAO 在真实运行时仍须尊重 NTE_GAME_STATIC_DB 的显式配置。
3. 审查保存、刷新、差异和装配测试是否正确反映 2.0 的 SQLite + 固定 snapshot_id 权威边界，是否有为绿灯而削弱行为测试的风险。
4. 复跑完整测试、编码防护、compileall、git diff --check；确认 Windows 临时 SQLite 文件锁没有复现。
5. 仅提出审计结论和下一阶段目标，不实施 v5、AllocationContext、UI 或伤害模拟。

请输出：
- 按严重程度排序的审计发现（若无问题，明确写“未发现阻塞项”）；
- 对每项测试迁移是否合理的判断；
- 验证结果与工作区保护情况；
- 是否批准进入 v5；
- 下一阶段的单一、可执行目标。若批准，优先建议“先设计并实现用户库 v5 的版本化词条权重偏好 DAO/迁移及测试”，然后才是 AllocationContext；明确不做 UI 和伤害模拟。
```

## 18. Sol high 只读审计结果（2026-07-22）

第一阶段可以判定为“技术基线已恢复，但还有一项非阻塞测试收口”。Sol high 已独立核对工作区差异，并重新执行：

```text
py -3.11 -m unittest discover -s tests
Ran 622 tests
OK

py -3.11 -m unittest tests/test_encoding_guard.py
Ran 16 tests
OK

py -3.11 -m compileall -q src tests
OK

git diff --check
OK
```

审计确认：

- `src/features/role/replacement_service.py` 恢复按现有词条权重分数排序，符合当前产品路线，没有把伤害重排引入旧替换弹窗；
- 测试固定项目发行静态库的隔离方式合理，`StaticGameDataDao` 的真实运行时仍尊重 `NTE_GAME_STATIC_DB`；
- 用户库源码版本为 v4、静态库版本为 v10，下一个用户库版本必须是 v5；
- 完整测试没有复现 Windows 临时 SQLite 文件锁清理异常，没有证据表明存在运行时 DAO 连接泄漏；
- 四个伤害计算文件仍未跟踪、未暂存，本次审计没有修改它们。

唯一的非阻塞发现：

- `tests/test_drive_assembly_ui_bridge.py` 中“全角色确认后启动”和“自动装配最小化窗口”两个旧行为测试被替换为 `callable()` 检查；相关单角色路由和窗口恢复行为在其他模块仍有覆盖，但这两个跨步骤行为需要在下一阶段开始前补回实质断言。

结论：允许进入 v5，但 Terra high 必须先补齐上述两个测试，再开始迁移和 DAO 实现。

## 19. 距离可执行重构流程验收还需要多少阶段

从当前状态起，预计还需要 **4 个主要实现阶段**。每个阶段完成后先由 Sol high 做只读审计，通过后才进入下一阶段；审计门不另计为产品实现阶段。

| 阶段 | 要解决的问题 | 难度 | 实施模型 | 审计模型 | 用户验收状态 |
| --- | --- | --- | --- | --- | --- |
| 2. 用户库 v5 偏好基础 | 补齐第一阶段测试缺口；新增版本化词条权重偏好迁移、DAO 和测试 | 中高 | Terra high | Sol high | 只做技术门禁，不做人工 UI 验收 |
| 3. 不可变 `AllocationContext` | 固定数据集、账号、快照、偏好版本、求解器版本、角色和候选集，隔离后台同步漂移 | 高 | Terra high；实现前可由 Sol high 先审设计 | Sol high | 验收可复现性和边界，不做游戏操作 |
| 4. Top-K 与全角色统一分配 | 区分硬约束/软权重，输出可解释 Top-K，保证多角色装备 UID 不重复 | 高 | Terra high | Sol high | 可做算法样例验收，仍无新页面 |
| 5. 新页面与端到端接线 | UI 读取 v5 偏好，后台构建上下文、运行求解、比较候选并保存方案 | 高 | Terra high | Sol high | 形成第一个需要用户人工验收的可执行重构流程 |

阶段 5 通过自动化验证和 Sol high 最终审计后，才进入用户人工验收。届时验收范围是“词条权重配装”完整流程，不包含完整伤害模拟、伤害重排、真实游戏一键装配或发布安装器。

模型选择规则：

- **Terra middle**：仅用于范围小、低风险、无 schema/架构决策的补测、文档、命名或机械迁移；
- **Terra high**：用于数据库迁移、DAO、服务层、求解器、跨模块 UI 接线等需要实现和调试的阶段；
- **Sol high**：用于只读审计、架构边界、不可变性/可复现性审查和最终验收门禁；默认不修改生产文件。

## 20. 强制阶段交接协议

任何模型完成一个阶段后必须先更新本文档，然后停止继续开发并明确告诉用户：

1. 本阶段解决了什么问题，哪些内容明确未做；
2. 修改文件和完整验证结果；
3. 当前阶段难度及实际风险；
4. 是否达到本阶段验收条件；
5. 下一阶段建议使用 `Terra middle`、`Terra high` 或 `Sol high` 中的哪一个，以及选择原因；
6. 下一模型将完成的唯一阶段目标；
7. 交接文档的绝对路径与应阅读的节号；
8. 是否需要用户人工验收、授权提交或授权推送。

不得在没有向用户完成上述交接的情况下自动开始下一阶段。提交、推送、真实装配、删除和发布仍需用户明确授权。

固定交接文档：

```text
docs/allocation-refactor-terra-handoff.md
```

## 21. 下一阶段：可直接交给 Terra high 的提示词

```text
继续 NTE-Drive-Calculator 2.0.0 的词条权重配装重构，只完成第 2 阶段：“用户库 v5 版本化优化偏好基础”。

仓库：.
分支：2.0.0
基础 HEAD：907fa14
交接文档：docs/allocation-refactor-terra-handoff.md

同时使用：
- nte-drive-calculator-dev
- nte-allocation-refactor-orchestration

完整阅读两个技能的 SKILL.md、nte-drive-calculator-dev/references/data-and-sqlite.md，以及交接文档第 16、18、19、20、21 节。先检查 git status、分支和 HEAD，保留用户已有修改。

必须保护并完全排除以下未跟踪的伤害计算文件：
- docs/damage-calculation-handoff.md
- docs/damage-calculation.md
- src/services/damage_calculation_service.py
- tests/test_damage_calculation_service.py

本阶段按以下顺序执行：

1. 先补回两个被 callable() 弱化的行为测试：
   - 全角色 `confirmed=True` 时，从 SQLite 活动方案中只选择 `nte_core` 来源角色，并调用 `_start_nte_core_equipment_apply`；
   - `_start_automatic_equipment_assembly` 成功启动时最小化窗口，并验证结果/错误回调会恢复配装页。测试应使用当前 SQLite + 工作线程边界，不恢复已移除的旧 JSON 入口。
2. 比较规范化子表与带版本 JSON 载荷两种 v5 方案，按查询能力、官方 ID 约束、版本固定、迁移可验证性和未来 `AllocationContext` 消费方式做出决定，并把简短决策记录到本文档。
3. 新增用户数据库 v5 迁移。当前源码为 v4，迁移文件应按现有编号继续新增，不修改 v1-v4 的含义。
4. 实现版本化优化偏好的 `UserDataDao` API 和测试。至少覆盖：
   - 配置档案及不可变版本；
   - 官方 `character_id` 和 `property_id` 权重；
   - `suit_id` 与不要求/二件/四件等模式；
   - 核心主词条过滤、副词条优先级、属性阈值/上限；
   - 角色顺序、优先级分组和分配策略；
   - 新库直接初始化到 v5；
   - v4 -> v5 迁移；
   - 创建、读取、更新为新版本、列出和停用配置；
   - 计算能够固定 `profile_id + version`，旧版本不被后续编辑覆盖；
   - 账号数据库之间不共享偏好。
5. 保持静态 ID 在数据库列或可严格验证的载荷中，中文只用于展示；不得复制 `roles.json`、`sets.json`、`my_roles_model.json` 成为权威数据。
6. 只做迁移、DAO、测试和必要文档；不要实现 `AllocationContext`、求解器改写、新 UI、伤害模拟或真实装配。

验证至少包括：
- 最贴近 v5 DAO/迁移的测试；
- 两个补强的装配 UI bridge 测试；
- `py -3.11 -m unittest tests/test_encoding_guard.py`；
- `py -3.11 -m unittest discover -s tests`；
- `py -3.11 -m compileall -q src tests`；
- `git diff --check`；
- `git status --short`。

阶段完成后必须遵循第 20 节强制交接协议：更新本文档，停止开发并告诉用户本阶段结果、难度、是否达到门禁；下一模型推荐 Sol high 做只读审计，并给出交接文档绝对路径和审计节号。不要自行开始第 3 阶段，不要 commit 或 push。
```

## 22. Terra high 第 2 阶段实际完成记录（2026-07-22）

### 本阶段范围与决策

本阶段只完成“用户库 v5 版本化优化偏好基础”，并先收回第 18 节指出的两项 UI 行为测试弱化；没有开始 `AllocationContext`、Top-K/全角色求解器、新优化器 UI、伤害模拟、真实装配、提交或推送。

v5 在“单个版本 JSON 载荷”和“规范化子表”之间选择了**规范化子表**。原因是未来 `AllocationContext` 和求解器需要按角色、官方 `character_id`、官方 `property_id`、词条阈值及优先级查询；将这些稳定 ID 放在受键/约束保护的列中，比 JSON 更容易固定版本、验证迁移和审计。JSON 仅适合不参与约束或查询的显示载荷，故本阶段未将其作为权威偏好来源。

### 实现内容

- 新增 `src/storage/sqlite/schema/006_user_data_v5.sql`，将用户库由 v4 迁移至 v5；新增档案、不可变版本、角色偏好、词条权重、副词条优先级、属性上下限六张规范化表。
- `src/storage/sqlite/user_data_dao.py` 升至 `SCHEMA_VERSION = 5`，提供：
  - `create_optimization_profile()`；
  - `create_optimization_profile_version()`（更新只产生新版本）；
  - `get_optimization_profile(..., version_number=...)`；
  - `list_optimization_profiles()`；
  - `deactivate_optimization_profile()`。
- 偏好版本可保存分配策略、角色顺序、优先级分组、套装 ID 与无/二件/四件模式、核心主词条过滤、官方属性 ID 权重、副词条顺序以及属性阈值/上限；数值、重复 ID、顺序和边界均在 DAO 写入前验证。
- `tests/test_user_data_dao.py` 覆盖新库直达 v5、显式 v4→v5、创建/读取/停用、账号隔离、固定旧版本不被新版本覆盖及关键约束。
- `tests/test_drive_assembly_ui_bridge.py` 已恢复两项实质行为断言：全角色确认只路由 SQLite 活动方案中 `nte_core` 来源角色到 `_start_nte_core_equipment_apply`；自动装配工作线程启动会最小化窗口，成功和错误回调都会返回配装页。

### 验证

```text
py -3.11 -m unittest tests.test_user_data_dao tests.test_drive_assembly_ui_bridge -v
Ran 42 tests
OK

py -3.11 -m unittest tests/test_encoding_guard.py
Ran 16 tests
OK

py -3.11 -m unittest discover -s tests
Ran 625 tests
OK

py -3.11 -m compileall -q src tests
OK

git diff --check
OK
```

最终 `git status --short` 确认仍处于 `2.0.0 @ 907fa14`，未暂存任何文件。四个受保护的伤害计算文件 `docs/damage-calculation-handoff.md`、`docs/damage-calculation.md`、`src/services/damage_calculation_service.py`、`tests/test_damage_calculation_service.py` 均保持未跟踪，未修改、暂存或删除。

### 阶段门禁与下一步

本阶段难度为中高：迁移和版本不可变性已具备单元测试覆盖，主要剩余风险是下一阶段把偏好版本、账号与快照固定进不可变上下文时的跨层边界，而不是 v5 数据落盘本身。达到第 2 阶段实现门禁后，应由 **Sol high** 做只读审计；审计期间不修改文件、不提交、不推送，也不要开始第 3 阶段。

Sol high 应完整阅读本交接文档第 18、20、21、22 节以及 `nte-drive-calculator-dev` 的 SQLite 参考，重点审查：v4→v5 迁移原子性、官方 ID 是否保持为权威列、旧偏好版本在停用后是否仍可读取、两项 UI 测试是否真的恢复跨步骤行为、全量 625 测试与保护文件状态。审计通过后，下一实施模型应为 **Terra high**，且其唯一目标是第 3 阶段：设计并实现不可变 `AllocationContext`，固定账号、库存快照、`profile_id + version`、静态数据版本、角色候选集和求解器版本；不得顺带启动求解器改写或 UI。

## 23. Terra high 第 2 阶段审计阻塞修复记录（2026-07-22）

### 修复范围

Sol high 审计未通过第 2 阶段门禁，指出三个数据正确性问题。本节仅修复这三个问题及其测试，未开始 `AllocationContext`、求解器、新 UI、伤害模拟、真实装配、提交或推送。

1. `UserDataDao._migrate_schema()` 不再使用会隐式提交的 `executescript()`。每个迁移现在以 `BEGIN IMMEDIATE` 开始，通过 `sqlite3.complete_statement()` 在同一显式事务内逐条执行 DDL，再写入 `schema_migration` 标记并提交；失败则完整回滚。DAO 构造失败时也会关闭已打开的连接，避免失败迁移占用数据库。
2. `optimization_preference_character` 增加数据库 `CHECK`：`two_piece`/`four_piece` 必须有非空 `target_suit_id`。DAO 的跨字段校验同步拒绝这一矛盾组合。
3. `create_optimization_profile()` 先完成全部输入验证，再在一个 `BEGIN IMMEDIATE` 事务中插入档案和首个不可变版本。`create_optimization_profile_version()` 也复用同一个无提交内部写入函数；任一步失败均回滚，不会留下活动空档案。
4. 补上停用后按 `version_number=1` 仍可读取旧版本的直接断言。

### 新增回归覆盖

- 构造 v4 数据库并注入“已创建两张表后 SQL 失败”的 v5 脚本：断言 schema 仍为 v4、所有部分 DDL 均不存在，恢复真实脚本后可以成功迁移至 v5；
- DAO 和直接 SQLite `INSERT` 均拒绝缺少 `target_suit_id` 的四件套；
- 强制首版本插入抛出 SQLite 异常：断言档案行不存在；
- 停用档案后仍可按固定版本读取 v1。

### 验证

```text
py -3.11 -m unittest tests.test_user_data_dao tests.test_drive_assembly_ui_bridge -v
Ran 43 tests
OK

py -3.11 -m unittest tests/test_encoding_guard.py
Ran 16 tests
OK

py -3.11 -m unittest discover -s tests
Ran 626 tests
OK

py -3.11 -m compileall -q src tests
OK

git diff --check
OK
```

最终 `git status --short` 确认仍处于 `2.0.0 @ 907fa14`，没有暂存文件。四个未跟踪伤害计算文件继续完全排除在本次修复之外：`docs/damage-calculation-handoff.md`、`docs/damage-calculation.md`、`src/services/damage_calculation_service.py`、`tests/test_damage_calculation_service.py` 均未修改、暂存或删除。

### 复审门禁

本次修复仍属中高难度的 SQLite 数据边界工作。代码和回归覆盖已针对 Sol high 的 P1/P2 指出项收口，但**不得自行判定可进入第 3 阶段**；现在唯一下一步是由 **Sol high** 做只读复审。

Sol high 应阅读第 18、20、22、23 节及 SQLite 参考，并重点复现：迁移中途失败后的回滚与重试、数据库层与 DAO 层的套装一致性、首档案与首版本的原子性、停用后固定版本读取、全量测试和保护文件状态。复审通过前，不进行 `AllocationContext` 工作；不需要用户人工验收、提交或推送授权。

## 24. Terra high 第 3 阶段实际完成记录：不可变 AllocationContext（2026-07-22）

### 前置审计与范围

Sol high 已复审并批准关闭第 2 阶段。本阶段只实现可复现性边界 `AllocationContext`，没有修改现有分配求解器、`_run_allocation`、任何 UI、伤害模型或真实装配，也没有提交或推送。

### 设计与实现

新增 `src/services/allocation_context.py`。`build_allocation_context()` 必须由调用方显式传入 `snapshot_id`、`profile_id` 和 `profile_version`，不会读取“当前快照”或“最新偏好”的可变指针。构造完成后返回由 `frozen=True, slots=True` 数据类与 tuple 组成的上下文，固定：

- 用户账号 ID；
- 静态数据库 schema 版本、`dataset_id`、导入器版本与构建时间；
- 背包快照 ID、来源、generation/sequence、采集时间与数量摘要；
- 偏好档案 ID、不可变版本号、分配策略、角色顺序与优先级分组；
- 套装/核心主词条约束、官方 `property_id` 权重、副词条顺序和属性上下限；
- `solver_version`；
- 由固定快照完整复制出的候选装备集：原生 `(slot, serial)` UID、官方 `item_id`/`suit_id`/`geometry` 及主副词条官方 ID；不含中文显示名或旧 JSON。

候选、词条、角色偏好和限制均为嵌套冻结数据类或 tuple，因此构造后不能被调用方或后台同步原地修改。快照和偏好版本本身在 SQLite 中也不可变；即使随后导入新快照、创建新偏好版本或停用档案，已经生成的上下文仍保持原始数据，且可从停用档案读取指定历史版本。

### 回归覆盖

新增 `tests/test_allocation_context.py`，覆盖：

- 全部可复现性输入和官方 ID 被复制到冻结上下文；
- 写入冻结上下文或候选会抛出 `FrozenInstanceError`；
- 新快照和新偏好版本产生后，既有上下文的快照、策略、权重和候选 UID 均不变；
- 停用档案后仍可构造指定旧版本上下文；
- 不存在的快照或偏好版本被拒绝；默认求解器版本被固定。

### 验证

```text
py -3.11 -m unittest tests.test_allocation_context tests.test_user_data_dao tests.test_sqlite_allocation_inventory tests.test_drive_assembly_ui_bridge -v
Ran 47 tests
OK

py -3.11 -m unittest tests/test_encoding_guard.py
Ran 16 tests
OK

py -3.11 -m unittest discover -s tests
Ran 629 tests
OK

py -3.11 -m compileall -q src tests
OK

git diff --check
OK
```

最终工作区仍为 `2.0.0 @ 907fa14`，无暂存、提交或推送。四个受保护的伤害计算文件仍未跟踪，未修改、暂存或删除。

### 阶段门禁与下一步

第 3 阶段难度为高；主要风险在于未来 Top-K/全角色求解器必须只消费这个上下文而不能重新读取 DAO 或修改候选集。当前实现没有改变求解结果，满足本阶段“建立不可变边界”的验收目标，但必须先由 **Sol high** 做只读审计。

Sol high 应阅读第 20、22、23、24 节以及 `src/services/allocation_context.py` 和其测试，重点检查：显式版本选择是否绕开所有 moving-current 指针、嵌套对象是否真的不可变、静态数据与官方 ID 是否完整固定、后台新快照/新偏好版本是否无法影响现有上下文、以及是否严格没有求解器/UI/伤害/装配越界。复审通过后，下一实施模型为 **Terra high**，唯一目标是第 4 阶段：基于 `AllocationContext` 实现可解释 Top-K 候选和全角色统一分配，保证 UID 不重复；不得开发新 UI 或伤害模型。

## 25. Terra high 第 3 阶段审计阻塞修复：冻结静态硬约束（2026-07-22）

### 修复范围

Sol high 指出第 24 节的 Context 只保存静态数据集标识，未来求解器仍会被迫重读 `StaticGameDataDao` 的蓝图和套装数据。此修复仍只属于第 3 阶段：补足冻结静态硬约束及验证；没有实现 Top-K、没有修改现有求解器、UI、伤害模型或真实装配。

### 实现

- `AllocationRolePreference` 现在内嵌冻结 `RoleEquipmentConstraints`，包含角色官方装备蓝图、核心模板、所有格位 `(row, column)` 及其可选锚点模板、模块模板、核心/推荐属性 ID、核心套装和偏好套装的 `required_shape_ids`。
- 新增冻结 `EquipmentTemplate`、`BlueprintCell`、`SuitConstraint` 与 `RoleEquipmentConstraints`；后续纯求解器可从角色上下文取得官方 `item_id`、`geometry_id`、格位坐标、核心 ID、套装 ID 与形状硬约束，无需再读 DAO。
- Context 构造时校验：角色存在官方装备蓝图；核心与蓝图格位/模块模板存在且类型正确；核心与偏好套装存在；蓝图核心/推荐属性、v5 权重/副词条/阈值/核心主词条和快照词条均是官方 `attribute_id`。
- `StaticGameDataDao` 新增 `list_equipment_attributes()` 与 `get_equipment_attribute()`，保持 Context 对静态数据库的访问经 DAO 边界完成。

### 新增回归覆盖

- 构造 Context 后关闭 `UserDataDao` 和 `StaticGameDataDao`，纯消费者仍可读取核心模板、完整格位、模块模板和套装 required shapes；
- 构造时拒绝未知角色、套装、属性 ID 和无法解析的官方装备模板；
- 静态 DAO 的官方属性查询有独立 fixture 覆盖。

### 验证

```text
py -3.11 -m unittest tests.test_allocation_context tests.test_static_game_data_dao tests.test_user_data_dao tests.test_sqlite_allocation_inventory tests.test_drive_assembly_ui_bridge
Ran 60 tests
OK

py -3.11 -m unittest tests/test_encoding_guard.py
Ran 16 tests
OK

py -3.11 -m unittest discover -s tests
Ran 632 tests
OK

py -3.11 -m compileall -q src tests
OK

git diff --check
OK
```

最终工作区仍为 `2.0.0 @ 907fa14`，没有暂存、提交或推送。四个受保护伤害计算文件仍未跟踪，未修改、暂存或删除。

### 复审门禁

本次修复保持第 3 阶段高难度边界：未来第 4 阶段求解器必须只接收 `AllocationContext`，绝不重新查询 `UserDataDao` 或 `StaticGameDataDao`。实现与回归已覆盖这一前提，但**不得自行进入第 4 阶段**；唯一下一步是 **Sol high** 只读复审。

Sol high 应阅读第 20、24、25 节，审查 `src/services/allocation_context.py`、`src/storage/sqlite/static_game_data_dao.py` 与对应测试；重点检查静态蓝图是否完整冻结、所有官方 ID 是否在构造期校验、DAO 关闭后的纯消费者是否仍有完整硬约束、以及没有任何求解器/UI/伤害/装配越界。复审通过后，下一实施模型为 Terra high，唯一目标才是基于 Context 的 Top-K 和全角色统一分配。

## 26. Terra high 第 3 阶段复审阻塞修复：官方目录与原子快照，2026-07-22

### 修复范围

本节只收口 Sol high 在第 3 阶段复审提出的三个构造边界：候选装备的官方 ID 校验、静态蓝图及其嵌套引用完整性、以及快照摘要与候选集合的一致原子读取。没有实现 Top-K 或全角色求解器，没有修改既有求解器、UI、伤害模型或真实装配，也没有提交或推送。

### 实现

- `build_allocation_context()` 在构造期一次读取官方角色、套装、形状、装备模板和属性目录，并以这些目录校验每个候选：`item_id` 必须存在、`kind` 必须与模板一致、非空 `suit_id` 必须存在、模块的规范化 `geometry` 必须是官方形状且和模板一致；已装备角色 ID 同样必须在官方角色目录中。
- 所有被冻结的官方装备模板都校验种类、模块形状和可选套装引用。套装的每个 `required_shape_id` 必须落在官方形状目录中。
- 每个角色蓝图必须恰好包含 20 个唯一、范围为 1..5 的格位；锚点模块模板的多重集必须和蓝图模块模板的多重集一致。核心、模块、套装和属性引用仍全部在构造期拒绝未知官方 ID。
- `UserDataDao.export_inventory_snapshot(snapshot_id)` 在同一 SQLite 读事务内读取快照摘要和全部物品，并在返回前核对 `stored_item_count` 与实际候选数。Context 只使用此原子导出；快照消失、读取失败或数量不一致都会拒绝构造，不会生成半份上下文。

### 新增回归覆盖

- 分别拒绝未知候选 `item_id`、`suit_id`、`geometry`，以及模块候选引用核心模板的类型错配。
- 分别拒绝 19 格蓝图、模块模板引用未知形状、套装必要形状引用未知形状。
- 使用两个真实 SQLite 连接复现“摘要已读、后台删除固定快照”的竞态：原子导出仍返回同一读事务中的完整摘要和候选；另外直接破坏摘要/物品计数后，DAO 和 Context 均拒绝继续构造。
- 已有“关闭两个 DAO 后纯消费者仍拥有完整蓝图、模板和套装形状”的覆盖保持有效。

### 验证

```text
py -3.11 -m unittest tests.test_allocation_context tests.test_static_game_data_dao tests.test_user_data_dao tests.test_sqlite_allocation_inventory tests.test_drive_assembly_ui_bridge -v
Ran 66 tests
OK

py -3.11 -m unittest tests/test_encoding_guard.py
Ran 16 tests
OK

py -3.11 -m unittest discover -s tests
Ran 638 tests
OK

py -3.11 -m compileall -q src tests
OK

git diff --check
OK
```

### 复审门禁

工作区仍为 `2.0.0 @ 907fa14`，没有暂存、提交或推送。四个受保护的未跟踪伤害计算文件 `docs/damage-calculation-handoff.md`、`docs/damage-calculation.md`、`src/services/damage_calculation_service.py`、`tests/test_damage_calculation_service.py` 未被修改、暂存或删除。

第 3 阶段仍不得自行判定为完成或进入第 4 阶段。下一步仅由 **Sol high** 做只读复审：阅读第 20、24、25、26 节、`src/services/allocation_context.py`、`src/storage/sqlite/user_data_dao.py` 及相应测试，重点复现候选/蓝图官方 ID 拒绝、DAO 关闭后的纯消费、以及并发 prune 下的原子快照读。复审通过前，不开始 Top-K、全角色求解器、UI、伤害或真实装配。

## 27. Terra high 第 4 阶段实际完成记录：本地图纸 Top-K 与全角色统一分配，2026-07-22

### 术语与产品边界

本阶段明确拆分两个过去都被称为“蓝图”的概念：

- **官方配装预设**：`equipment plan` 中的推荐空幕、固定模块/锚点、推荐套装/词条和参考位置；只作默认参考。
- **求解蓝图**：本项目在角色 20 格可用底盘上，通过 `PuzzleCombinatorics + DFSPuzzleSolver` 动态生成的合法布局；这是 Top-K 和最终统一分配唯一使用的布局。

真正的硬约束只有角色可用格位、官方形状完整格子坐标、实际候选装备的类型/形状/套装/UID，以及用户显式选择二件或四件套时该套装的必要形状。官方推荐核心、模块列表、锚点、推荐套装与推荐词条均没有固化为候选筛选或布局硬约束；除非未来单独提供“严格按官方推荐”模式。

### 实现

- `AllocationContext` 新增冻结的 `OfficialShape` / `OfficialShapeCell` 与完整官方套装目录。构造完成后关闭两个 DAO，纯消费者仍拥有运行本地图纸求解器所需的形状面积和相对格坐标。
- 新增 `src/services/allocation_solver.py`，唯一入口为 `solve_allocation_context(context, ...)`；不导入或查询 `UserDataDao` / `StaticGameDataDao`，不保存方案、不接 UI、不执行真实装配。
- 求解器从 Context 的 20 格底盘、官方形状和用户套装模式调用既有 `PuzzleCombinatorics`、`DFSPuzzleSolver`。对同一形状重复出现时临时使用唯一别名，保留每件动态拼图的实际占格；最终结果因此可解释且不依赖官方锚点。
- 空幕按用户核心主词条、用户显式套装和词条权重从实际背包选择；官方 `CoreID` 只以 `official_recommendation_item_id` 出现在解释输出中。模块同样按动态布局的官方 geometry 匹配，不要求官方预设 `module_templates` 或锚点 item ID。
- 每个角色使用精确的一对一矩阵 Top-K 枚举并保留词条贡献、用户套装/形状、动态 DFS 盘面与属性上下限解释。统一阶段在更深的有界候选前沿中消解跨角色 UID 冲突，输出无重复 UID 的团队方案，同时对外保留请求的 Top-K 展示集合。

### 新增回归覆盖

- Context 在关闭 DAO 后仍保有官方形状格子和套装定义；
- 真实静态库 + 用户库构造 Context 后关闭 DAO，仍可生成本地图纸并求解；
- “官方配装预设不是最优解”场景：官方推荐核心/模块存在但低分，求解器选择不同的合法核心、不同模块并取得更高分；
- 动态 Top-K 的词条贡献解释、四件套必要形状、属性上限过滤、跨角色共享 UID 的冲突消解和 UID 唯一性。

### 验证

```text
py -3.11 -m unittest tests.test_allocation_context tests.test_allocation_solver tests.test_static_game_data_dao tests.test_user_data_dao tests.test_sqlite_allocation_inventory tests.test_drive_assembly_ui_bridge -v
Ran 72 tests
OK

py -3.11 -m unittest tests/test_encoding_guard.py
Ran 16 tests
OK

py -3.11 -m unittest discover -s tests
Ran 644 tests
OK

py -3.11 -m compileall -q src tests
OK

git diff --check
OK
```

### 复审门禁

本阶段难度为高，主要风险是把官方配装预设误回归为硬约束，或在统一分配中重复使用同一原生 UID。实现阶段已经覆盖这两个真实产品风险，但不得自行进入第 5 阶段 UI。

下一步仅由 **Sol high** 做只读复审。应阅读第 20、24、25、26、27 节，重点审查 `src/services/allocation_context.py`、`src/services/allocation_solver.py` 及其测试：确认 Context 关闭 DAO 后可运行现有拼图求解器；官方预设没有参与最终候选/布局硬筛选；用户套装形状、属性限制和 UID 唯一性成立；没有 UI、伤害、保存方案或真实装配越界。复审通过前，不需要用户人工验收，也不需要提交或推送授权。

## 28. Terra high 第 4 阶段审计返工：复用既有推荐算法（2026-07-22）

### 返工结论与术语

第 27 节的实现曾错误地在新入口重写评分和部分分配逻辑。本节将其收回：**官方配装预设**（官方推荐核心、模块、锚点、推荐套装和推荐词条）只保留在静态资料中作为参考；**求解蓝图**专指由本项目现有 `PuzzleCombinatorics + DFSPuzzleSolver` 在角色 20 格底盘上动态生成的合法布局。后续选优只使用后者。

新入口不再按“实际词条数值 × 权重”自行评分，也不会在用户权重为空时退回 `equipment plan` 的推荐属性。行为真值是现有异环工坊权重、`ScoringEngine`、`NTEPipelineOrchestrator.solve_blueprints()` 和 `DispatcherEngine` 的三种策略（角色优先、驱动优先、全局最优）。

### 实现

- `AllocationContext` 窄化为求解实际所需的冻结输入：角色 20 格底盘、官方形状完整格子、套装必要形状、官方属性 ID、固定快照候选 UID/类型/形状/套装/词条，以及固定的偏好版本。官方推荐核心、模块、锚点和推荐属性不再进入运行时 Context 的构造门禁或选优输入。
- 构造 Context 时一次读取异环工坊同步缓存 `config/roles.json`，把默认词条权重、主词条权重、`extra_shape_label` 和额外形状收益复制为冻结的有效权重；v5 用户配置对同一属性显式覆盖默认值。求解期间不再读取 DAO，也不会随后的同步缓存或偏好新版本变化。
- 新增 `src/services/allocation_legacy_adapter.py`。它只把冻结 Context 投影为短生命周期的旧内存模型，然后直接调用既有 `NTEPipelineOrchestrator.solve_blueprints()`、`ScoringEngine.evaluate_global_inventory()` 和 `DispatcherEngine.execute_dispatch()`；没有复制评分公式、图纸搜索或三种策略实现。图纸 orchestrator 以 `__new__` 创建并注入 Context 数据，避免构造时重读当前 `roles.json`、`sets.json` 或 `shapes.json`。
- `src/services/allocation_solver.py` 现在是纯输出门面：单角色 Top-K 通过排除已选 UID 后重新运行原单角色链路得到不同候选；跨角色统一结果直接使用原 Dispatcher 策略并额外断言原生 `(slot, serial)` UID 不重复。解释对象只呈现既有评分结果和匹配词条种类，不会计算另一套总分。
- 保留旧算法的 `extra_shape_label`、额外形状收益、主/副词条权重、词条优先级、暴击率上限与三种策略语义。没有新增 UI、伤害模型、方案保存或真实装配。

### 新增/修正回归

- 在同一冻结库存、权重、套装和角色顺序下，分别验证角色优先、驱动优先、全局最优三种策略：新入口 `top_k=1` 的统一结果与直接运行旧 Dispatcher 的 Top-1 使用完全相同的 UID 集合。
- 保持“官方配装预设不是最优解”场景：合法且评分更高的动态求解结果可以不同于官方预设。
- 构造 Context 后关闭 `UserDataDao` 与 `StaticGameDataDao`，仍可调用旧图纸/评分/分配链路；适配器未重读 DAO 或可变配置。
- 保持四件套必要形状、属性上限、共享 UID 冲突消解和 Top-K 不同 UID 集合的覆盖。

### 验证

```text
py -3.11 -m unittest tests.test_allocation_context tests.test_allocation_solver tests.test_scoring_account_workflows tests.test_blueprint_sqlite_solver tests.test_sqlite_allocation_inventory tests.test_user_data_dao -q
Ran 77 tests
OK

py -3.11 -m unittest tests/test_encoding_guard.py
Ran 16 tests
OK

py -3.11 -m unittest discover -s tests -q
Ran 645 tests
OK

py -3.11 -m compileall -q src tests
OK

git diff --check
OK
```

### 复审门禁

工作区仍为 `2.0.0 @ 907fa14`，没有暂存、提交或推送。本阶段未修改、暂存或删除四个受保护的未跟踪伤害计算文件：`docs/damage-calculation-handoff.md`、`docs/damage-calculation.md`、`src/services/damage_calculation_service.py`、`tests/test_damage_calculation_service.py`。

**不得自行进入第 5 阶段 UI。**下一步仅由 **Sol high** 做只读复审。审计应阅读第 20、24、25、26、27、28 节，并重点确认：

- 没有残留“实际数值 × 权重”的新评分或官方推荐词条回退；
- `ScoringEngine`、异环工坊权重、`PuzzleCombinatorics + DFSPuzzleSolver`、三种 Dispatcher 策略、额外形状和暴击/优先级行为均为实际执行路径；
- 官方配装预设不参与硬约束或选优，Context 关闭 DAO 后仍足以运行动态图纸；
- 新旧同输入 Top-1 等价、跨角色 UID 不重复，且没有 UI、伤害、保存或真实装配越界；
- 受保护伤害文件保持未跟踪且未暂存。

## 29. Terra high 第 4 阶段审计返工：共享 AllocationKernel 与旧契约补全（2026-07-22）

### 返工结论

本节收口 Sol high 指出的旧算法适配语义问题，并取代第 28 节中“Context 适配器直接拼接旧组件、以 `__new__` 绕过 orchestrator 构造”的临时接法。没有复制评分、拼图或 Dispatcher；旧 UI 入口和 `AllocationContext` 入口现在共同调用唯一的内存态 `AllocationKernel`。

### 实现

- 新增 `src/optimizer/allocation_kernel.py`：显式请求对象区分模块套装目标、核心套装目标、核心主词条过滤、副词条优先级、通用属性限制和角色分组。Kernel 仍实际调用现有 `ScoringEngine` 与三种 `DispatcherEngine` 策略，输出保持原始 `AllocationResult`。
- `NTEPipelineOrchestrator.run_full_allocation()` 改为旧 UI 的兼容包装器：原始库存投影、图纸生成和既有筛选上限准备好后进入同一 Kernel。`from_frozen_inputs()` 是 Context 图纸适配的正式构造入口，不再由 adapter 用 `__new__` 注入私有字段。
- Dispatcher 契约新增独立的核心套装目标。`none` 仅表示模块不要求套装，核心仍按冻结主词条从任意真实套装中选择；`two_piece` 与 `four_piece` 的核心继续受用户目标套装约束。有效方案必须同时具有核心、完整模块数和唯一 UID。
- Context 投影直接复用旧背包投影的百分比单位与官方形状→旧图纸形状 ID 映射；形状 `label` 保留旧 `Type-2/Type-3/Type-4`，不会把官方 geometry ID 错当作额外形状标签。
- 工坊角色默认值优先根据 `workshop_item_id/workshop_item_ids` 对应官方角色 ID，名称仅作回退，修复 1046/1051 的“主角”映射。兼容层还使用旧求解器的属性显示名，避免静态库别名（如“通用伤害增强”）改变 `ScoringEngine` 的标准化筛选结果。
- v5 任意属性 `minimum/maximum` 由 Kernel 在相同旧算法重跑分支中验证；暴击上限仍保留旧策略的兼容参数。额外形状收益计入限制校验，未引入新的评分公式。
- 修正 Hungarian 核心分配结果的 NumPy 索引类型：在写回角色键前显式转换为 Python `int` 并绑定 `str` 角色名，消除 IDE 对 `list | str` 不可哈希字典键的静态报错，运行时结果不变。

### 回归与验证

- 新增真实旧入口等价回归：固定同一 SQLite 快照、角色、套装、权重和核心主词条，直接调用旧 `NTEPipelineOrchestrator.run_full_allocation()`，与 Context 适配后 Kernel 比较角色、核心 UID、模块 UID 和总分；角色优先、驱动优先、全局最优均一致。
- 覆盖百分比 `0.1 → 10.0` 的旧投影单位、`Type-3` 标签、1046 工坊默认映射、`none` 模式仍选择跨套装合法核心，以及通用属性上限拒绝高分违规候选。
- 定向回归：137 项通过。
- 编码防护：16 项通过；`python -m compileall -q src tests` 通过；`git diff --check` 通过。
- 全量：649 项通过。

### 复审门禁

工作区仍为 `2.0.0 @ 907fa14`，没有暂存、提交或推送。本次没有进入第 5 阶段 UI，也没有接入伤害、保存方案或真实装配。四个受保护的未跟踪伤害计算文件仍保持未跟踪、未暂存、未修改：`docs/damage-calculation-handoff.md`、`docs/damage-calculation.md`、`src/services/damage_calculation_service.py`、`tests/test_damage_calculation_service.py`。

**不得自行进入第 5 阶段 UI。**下一步仅由 **Sol high** 做只读复审。审计应阅读第 20、24、25、26、27、28、29 节，并重点确认：

- 旧 UI 和 Context 是否实际共用 `AllocationKernel`，且没有第二套评分、拼图或 Dispatcher；
- `none/two_piece/four_piece` 是否分别表达模块约束与核心筛选，通用属性限制和核心完整性是否成立；
- 真实旧入口三种策略的等价回归是否有效，不是新入口与自身比较；
- 百分比、`Type-3`、1046/1051 映射及旧属性别名是否保持既有语义；
- 没有 UI、伤害、保存或真实装配越界，且受保护伤害文件未被暂存或修改。

## 30. Terra high 第 4 阶段增量阻塞修复：共享候选池上限（2026-07-22）

Sol high 增量复审仅保留一个真实多角色漏解问题：Context adapter 曾让 Kernel 回退到驱动 Top 15、核心每套 Top 3；旧入口则会依据同批蓝图形状需求和优先级组扩大候选池。在娜娜莉（1010）与九原（1055）的 `Type-2 + none` 正常静态库场景中，这会使单角色均可解、统一分配却没有任何完整方案。

### 修复

- 将旧入口的“每角色蓝图形状最大需求跨角色求和 + 5”及优先级组下限提取为 `src/optimizer/allocation_kernel.py::estimate_candidate_pool_limits()`。
- `NTEPipelineOrchestrator.run_full_allocation()` 和 `run_legacy_allocation()` 均在图纸生成后调用该共享函数；Context 不再退回 Kernel 请求默认的 `15/3`。
- 核心候选池同样统一为 `max(6, 最大优先级组角色数 × 4)`；驱动池统一为 `max(15, 最大形状需求 + 5, 最大优先级组角色数 × 10)`。
- 删除旧入口中已被提取的重复驱动候选池估算，未修改评分、拼图、Dispatcher 策略、UI、伤害、保存方案或真实装配。

### 新增真实回归与验证

- 使用发行静态库和临时真实 `UserDataDao` 构造 1010 娜娜莉、1055 九原的 `Type-2 + none` 双角色上下文：16 个横向 Type-2、4 个纵向 Type-2、2 个合法核心。共享估算将驱动上限提升至 Top 25；统一结果包含两人，每人均为 1 核心 + 10 模块，全部原生 UID 唯一。
- 定向回归：115 项通过。
- 编码防护：16 项通过；`compileall`、`git diff --check` 通过。
- 全量：650 项通过。

### 增量复审门禁

工作区仍为 `2.0.0 @ 907fa14`，没有暂存、提交或推送；四个受保护伤害文件仍未跟踪、未暂存、未修改。仍不得进入第 5 阶段 UI，也不需要人工功能验收。

下一步只由 **Sol high** 对本节增量差异做只读复审：确认共享估算函数由旧 UI 和 Context 两个入口实际调用、真实双角色回归能防止固定 15/3 回退、并确认没有改变评分/拼图/Dispatcher 或越界进入 UI。无需重审此前已关闭事项。

## 31. Terra high 第 5 阶段实际完成记录：词条权重配装 UI 与端到端接线（2026-07-22）

### 实现范围

- 新增独立导航入口“词条配装”，保留原有“计算”入口不删除、不改写。新入口显式列出完整背包快照和 v5 偏好档案的精确版本；运行时不会追随之后的同步、快照清理或偏好编辑。
- 新增 `src/features/weighted_allocation/runner.py` 的 UI 门面：WorkerThread 内打开 DAO、构造已审计的 `AllocationContext`、调用 `solve_allocation_context()`，然后立即关闭 DAO。该门面不包含任何评分、拼图、候选池或 Dispatcher 实现。
- 新页面提供 v5 偏好档案/版本编辑器：可选择角色、策略、套装模式与目标、核心主词条、词条权重、副词条优先级及任意属性上下限。留空权重继续由构造 Context 时冻结的异环工坊默认值提供；用户填写的 v5 权重按既有规则覆盖默认值。
- 结果页展示统一分配、未分配角色、每角色 Top-K、原生 UID、官方装备 ID、套装/形状、旧 `ScoringEngine` 得分以及现有求解链路给出的约束解释；页面不重新计算评分。
- 可将当前**统一**结果保存为 SQLite `loadout_plan`。保存 payload 记录固定的 `snapshot_id`、静态数据集标识、`profile_id + profile_version`、`solver_version` 和策略；保存仅调用 `SavedStateLoadoutBridge`，不会调用 RPC、不会实际装备游戏内物品。
- 用户发现的 `role_priority_strategy.py` SciPy 分配索引 IDE 报错已收口：先将 NumPy 行/列索引转换为 Python `int`，再取得已规范化的字符串角色键写回 `assigned_tapes`；仅修复静态类型收窄，不改变 Hungarian 分配结果。

### 新增回归与验证

- 新增 `tests/test_weighted_allocation_ui.py`：覆盖门面只按顺序调用 Context 构造和已审计求解门面、非法 Top-K 的前置拒绝、v5 覆盖字段解析、结果表格渲染、以及保存 payload 的所有可复现性标识。
- 导航回归更新为同时验证旧 `execute` 与新 `weighted_allocation` 稳定存在，索引映射随新增入口一致更新。
- 定向：`py -3.11 -m unittest tests.test_weighted_allocation_ui tests.test_extension_support tests.test_encoding_guard -q`，46 项通过。
- 全量：`py -3.11 -m unittest discover -s tests -q`，657 项通过。
- `py -3.11 -m compileall -q src tests`、`git diff --check` 均通过。

### 明确未做

- 未修改 `AllocationKernel`、`ScoringEngine`、`PuzzleCombinatorics`、`DFSPuzzleSolver`、Dispatcher 或第 4 阶段候选池估算；没有第二套推荐算法。
- 未接入伤害模型或伤害重排；未执行真实游戏配装、没有安装器/发布操作。
- 没有删除旧 UI 或旧配置兼容链路，也没有提交、暂存或推送。

### 风险与复审门禁

本阶段的主要风险在 UI 写入时是否把固定求解结果完整投影为可追溯 SQLite 方案，以及是否在后台线程之外误读或重算可变数据。保存仍只保存已生成的统一方案；用户人工验收前不应开始真实装配。

工作区仍为 `2.0.0 @ 907fa14`，暂存区为空。四个受保护的未跟踪伤害文件 `docs/damage-calculation-handoff.md`、`docs/damage-calculation.md`、`src/services/damage_calculation_service.py`、`tests/test_damage_calculation_service.py` 的 SHA-256 保持不变，未修改、暂存或删除。

**第 5 阶段在此停止，下一步仅由 Sol high 只读增量复审。**复审应阅读第 20、30、31 节以及 `src/features/weighted_allocation/`、`src/ui/navigation.py`、`src/ui/main_window_mixins.py`、`tests/test_weighted_allocation_ui.py`。重点确认：

- 新 UI 唯一通过 `AllocationContext` / `solve_allocation_context()` 门面进入已冻结的第 4 阶段算法，没有重读旧 JSON、更没有改写评分、拼图或 Dispatcher；
- 快照和档案版本显式固定，后台计算关闭 DAO 后才返回，结果和保存 payload 记录完整可复现标识；
- 保存统一方案时只写入 SQLite，核心/模块 UID、锚点和来源快照正确，不会触发真实装备 RPC；
- Top-K 与选择解释可操作呈现，旧入口仍存在；
- 受保护伤害文件与第 4 阶段共享内核未被触碰。

复审通过后才进入用户人工验收“词条权重配装”的完整流程；仍不需要提交、推送或真实装配授权。

## 32. Terra medium 第 5 阶段可用性阻塞修复：配置界面与账号固定（2026-07-22）

### 问题与收口

第 31 节的初始 UI 接线虽然保留了第 4 阶段求解边界，但增量检查发现两个实际用户阻塞：`_card()` 已有 `QVBoxLayout`，页面又创建带同一父控件的 `QFormLayout`，Qt 因而没有把输入表单挂入可见树；同时，配置编辑器要求用户输入官方 ID 和协议字符串，不能作为正常配置流程使用。

- 输入卡片现在把 `QFormLayout` 显式加入 `selection_card.layout()`。背包快照、配装配置、每角色候选数、新建/复制、运行和保存按钮均处于可见控件树中。
- 用户可见术语统一为“配装配置”，不展示 v5、数据库版本或官方 ID。角色、套装和核心主词条使用中文下拉选择；副词条使用勾选控件；权重与属性上下限使用独立数值控件。控件的 `userData` 仍保存官方 ID，最终仍经 v5 不可变配置版本 DAO 写入，未回退旧 JSON。
- `WeightedAllocationPreview` 现在冻结计算发起时的 `account_id` 与 `user_database_path`。保存门面只打开该冻结路径；若运行中切换账号，刷新会清除旧预览，保存前也会再次拒绝并清除，不能把旧账号的预览写进新账号数据库。
- 本轮未修改 `AllocationKernel`、评分、拼图、Dispatcher、伤害模型或真实装配路径。

### 回归

- `tests/test_weighted_allocation_ui.py` 新增离屏页面构建回归：断言新建配置、运行和保存按钮都有可见父控件；并验证中文配置控件投影为正确的官方 ID/v5 payload。
- 保存回归验证预览所冻结的数据库路径是 `UserDataDao` 的实际打开目标，而不是保存时的全局账号路径。

### 验证与下一步

```text
py -3.11 -m unittest tests.test_weighted_allocation_ui tests.test_extension_support tests.test_user_data_dao -q
Ran 61 tests
OK

py -3.11 -m compileall -q src tests
OK

git diff --check
OK

py -3.11 -m unittest discover -s tests -q
Ran 659 tests
OK
```

工作区仍为 `2.0.0 @ 907fa14`，未暂存、提交或推送。四个受保护的未跟踪伤害计算文件未修改、未暂存或删除。

第 5 阶段仍停在 **Sol high 增量只读复审** 门前；复审仅需检查本节的页面可见性、配置控件的官方 ID 投影以及跨账号保存边界，无需重审已冻结的第 4 阶段求解内核。通过后才进入人工 UI 验收；仍不授权真实装配。

### 后续 UI 修订（2026-07-22）

用户反馈初版配置弹窗的“添加角色”流程和信息层级仍不能接受。本次未触及求解语义，而是将角色选择改为与旧入口一致的搜索、角色卡片和已选优先级条：点击卡片加入，已选角色可以单独进入“配置”或移除。角色目录在进入 UI 前过滤为具有官方装备底盘的角色，避免把后续必然无法求解的角色加入配置。

词条编辑拆为两个页签：中文副词条勾选，以及只列出用户主动添加的“权重与属性限制”表；不再将完整官方属性目录塞进开发者式大表。新增回归直接点击可见角色卡片并验证角色进入 v5 payload。

开发环境另发现 `NTE_GAME_STATIC_DB` 若指向旧 schema v3 外部构建库，`StaticGameDataDao` 会按显式覆盖规则拒绝打开；发行项目内 `data/game_static.sqlite3` 为 v10。该环境变量需指向 v10 构建库或取消设置，不能把这一运行环境错误误判为配置 UI 的角色添加逻辑。

### 按用户要求回归旧版交互（2026-07-22）

用户明确要求先完整复用旧版配装 UI，而不是继续迭代一套相似但不同的新表单。`_WeightedProfileDialog` 因此直接嵌入既有 `RoleSelector`：角色卡片、中文/拼音搜索、选中顺序、拖放调整、优先级组符号和“管理”弹窗均为原实现；原先会写旧 JSON 的恢复、保存、读取按钮隐藏。新页面只用静态 SQLite 构造该控件的显示目录，保存时将显示名映射回官方角色/套装/属性 ID 并写入 v5 配置版本。

本次保留了旧入口的核心主词条和副词条选择、套装效果模式及优先级语义；尚未重新增加“逐属性数值权重/通用上下限”的新控件，避免在旧 UI 回归尚未验收前再次引入另一套交互。它们应在用户确认旧版选择流程可用后，以原管理弹窗的扩展方式单独补入，不得重写 `RoleSelector` 或改变求解内核。

### 页面收敛返工（2026-07-22）

按产品决策删除配置弹窗与所有高级输入。主页面现在只显示旧版角色优先级选择器、开始计算、统一结果和保存方案。背包快照自动取当前账号最新完整快照；内部只请求 Top-1，以避免为不可见候选重复运行求解；策略固定为 `role_priority`；角色选择或同级分组变化时才自动创建新的隐藏 v5 配置版本。普通结果只显示统一选中的角色、核心/驱动数量和布局，不渲染候选 Top-K、UID、官方装备 ID、形状或版本信息。

## 33. Terra medium 第 5 阶段收口：保存线程生命周期（2026-07-22）

### 修复

- `start_weighted_allocation_save()` 现在将保存用 `WorkerThread` 保存为窗口的 `_weighted_allocation_save_worker` 属性，避免局部变量在 Qt 后台任务尚未结束时被回收。
- 保存开始时禁用保存按钮；成功或失败回调均清理该引用并重新启用按钮。保存仍只写入已计算的 SQLite 方案，不触发真实装配。
- 使用当前账号的真实快照 11（955 件）对比旧入口与 Context 入口：1051 的核心、7 个驱动 UID 与 303.12 分完全一致；1055 的核心、图纸、281.92 分一致，存在一件同分 II 型驱动的 tie-break UID 差异。该差异不影响当前基础试用，但“逐 UID 完全等价”作为后续确定性排序债务保留，不阻塞基础配装 UI。

### 回归

- `tests/test_weighted_allocation_ui.py` 新增保存线程回归：断言线程在结果回调前由窗口持有，并在回调后释放。
- `py -3.11 -m unittest tests.test_weighted_allocation_ui tests.test_encoding_guard -q`：22 项通过。
- `py -3.11 -m compileall -q src tests`、`git diff --check`：通过。

### 阶段状态

基础词条配装可进入用户人工试用：角色优先级选择、自动最新完整快照、一次统一求解、结果查看与仅 SQLite 保存均已就绪。Top-K 生成展示及持久化留待未来伤害细调阶段，不阻塞本阶段收口。未提交、暂存或推送；四个受保护的伤害计算未跟踪文件未修改。

## 34. Terra high 第 5 阶段增量返工：核心偏好与结果可读性（2026-07-22）

### 修复范围

- 纠正第 33 节的人工试用结论：本节完成后仍须先由 Sol high 对本次 UI 增量做只读复审，复审通过后才进入人工界面验收。
- 官方 `equipment plan` 的核心套装和主词条现在仅作为“核心偏好”对话框中的可见默认说明；未由用户明确选择时，写入的 v5 偏好为 `target_suit_id=None`、`core_main_property_id=None`、`suit_requirement_mode=none`，不会把官方推荐升级为隐藏的核心或模块硬约束。
- 用户在“核心偏好”中明确选择套装或主词条后，才将该值固定到 Context；模块仍是 `none` 模式，核心套装筛选与模块套装要求保持拆分。
- 结果页继续只展示统一选中解，但直接复用旧 `results_view` 的拼图棋盘和装备卡组件；每个角色显示核心、驱动、品质、主/副词条、单件分数/评级及所选装备属性汇总，不再显示原始 UID/官方 ID/Top-K 表格。
- 对没有统一解的角色，若显式核心偏好在固定快照中无匹配，会显示例如“卡厄斯：缺少 街头拳王＋暴击率 主词条核心”；否则显示缺少可组成完整图纸的驱动。
- 角色目录只加载已有同步推荐权重的角色；未同步者不会进入可选列表，并在页面状态中按中文名提示“推荐权重尚未同步，暂不可选择”。
- 页面计算完成后显示所固定快照的采集时间。没有修改评分公式、PuzzleCombinatorics、DFSPuzzleSolver、Dispatcher、Top-K 行为、伤害模型或真实装配。

### 回归与验证

- `tests/test_weighted_allocation_ui.py` 新增/更新：官方默认不成为硬约束、显式核心偏好投影、无同步权重角色过滤、精确无核心原因、v5 品质名到旧结果卡品质名映射，以及离屏渲染中旧拼图与 `equipmentCard` 实际加入可见控件树。
- 定向：`py -3.11 -m unittest tests.test_weighted_allocation_ui tests.test_allocation_context tests.test_allocation_solver tests.test_encoding_guard -q`，48 项通过。
- 全量：`py -3.11 -m unittest discover -s tests -q`，661 项通过。
- `py -3.11 -m compileall -q src tests`、`git diff --check` 通过。
- 工作区仍为 `2.0.0 @ 907fa14`，暂存区为空；四个受保护的未跟踪伤害文件保持未修改、未暂存、未删除。本轮未提交或推送。

### 复审门禁

第 5 阶段仍停在 **Sol high 增量只读复审** 门前。复审仅检查本节涉及的 `src/features/weighted_allocation/page.py`、`src/services/allocation_context.py`、`src/services/allocation_legacy_adapter.py`、`src/features/weighted_allocation/runner.py` 和 `tests/test_weighted_allocation_ui.py`：官方默认是否未被隐式升级为硬约束、显式偏好是否正确、旧结果组件是否真实复用、缺失权重与无解信息是否可操作、账号/快照固定边界是否保持。

复审不应重新审查第 4 阶段共享 Kernel 或评分/拼图/Dispatcher 冻结基线。Sol 通过后，才请用户进行一次人工 UI 验收；仍不需要提交、推送、伤害接线或真实装配授权。

## 35. Terra high 第 5 阶段产品语义修正：官方驱动预设与可空核心（2026-07-22）

### 修复

用户澄清了默认语义：不能以 `none` 套装模式解出无约束的全散件图纸。词条配装页默认改为使用官方配装预设的 `four_piece + target_suit_id` 和核心主词条来生成、筛选驱动图纸；这确保官方推荐套装的必要形状继续参与 `PuzzleCombinatorics + DFSPuzzleSolver`。

官方推荐核心仍会优先匹配。但若当前冻结背包没有符合该套装和主词条的核心，页面专用入口会保留已匹配的完整驱动方案，并把核心留空；结果卡会显示“空幕缺失：缺少 <套装>＋<主词条> 主词条核心（驱动图纸已匹配）”。这不是把 `none` 作为回退求解，也不会将不符合官方推荐的核心偷换进结果。

共享 `AllocationKernel` 增加显式 `allow_missing_core` 请求开关。旧入口与普通 Context 调用保持核心必需；仅 `weighted_allocation` 页面经后台 runner 以该开关调用，因此没有复制评分、拼图或 Dispatcher，也不改变第 4 阶段基线的默认行为。

### 回归

- UI 投影回归断言默认配置为官方 `four_piece` 套装和核心主词条，而不是 `none`。
- 新增驱动专门回归：官方四件套驱动完整、核心候选为空时，页面路径仍产出仅含驱动的有效统一方案；正常 Context 路径保持原有核心必需语义。
- 新增结果文本回归，确认空核心不会被写成“无方案”。
- 定向：`py -3.11 -m unittest tests.test_weighted_allocation_ui tests.test_allocation_context tests.test_allocation_solver tests.test_encoding_guard -q`，50 项通过。
- 全量：`py -3.11 -m unittest discover -s tests -q`，663 项通过；`py -3.11 -m compileall -q src tests` 与 `git diff --check` 通过。
- 分支仍为 `2.0.0 @ 907fa14`，暂存区为空；四个受保护的未跟踪伤害文件未修改、未暂存、未删除。本轮未提交或推送。

### 复审更新

第 5 阶段继续停在 **Sol high 增量只读复审** 门前。本次复审还须检查 `src/optimizer/allocation_kernel.py`、`src/services/allocation_solver.py` 与 `src/features/weighted_allocation/runner.py` 的 `allow_missing_core` 仅被新页面开启：默认官方套装图纸必须保留，缺失核心仅允许空位而不能使驱动图纸失效，旧入口的核心必需行为不得变化。仍不进入人工验收、提交、推送、伤害接线或真实装配。

## 36. Terra medium 第 5 阶段 UI 返工：空幕管理、合法词条池与旧结果布局（2026-07-22）

### 用户反馈与修正

用户确认计算本身暂不需要调整，要求配置和结果先与旧配装入口保持一致。本轮只修改 UI 投影与文案，没有改变 `AllocationKernel`、评分、拼图、Dispatcher、Top-K、`allow_missing_core` 或保存/真实装配边界。

- 删除独立的“核心偏好”按钮；词条配装页继续复用旧 `RoleSelector`，已选角色旁的绿色“管理”按钮现在可注入页面专用偏好弹窗。旧页面不传回调时仍打开原管理弹窗。
- 用户可见术语全部改为“空幕”：弹窗使用“空幕配置”“空幕主词条”“空幕/驱动副词条”，结果使用“空幕”“空幕属性汇总”和“主词条空幕”。内部官方 kind/字段仍保持 `core`，没有修改协议或 schema。
- 空幕主词条不再采用官方推荐默认值；初次进入显示“未选择”，写入 v5 的 `core_main_property_id=None`。官方空幕套装仍作为默认 `four_piece + target_suit_id`，因此驱动图纸不会退化为无套装约束。
- 修复错误词条来源：不再把完整 `equipment_attribute` 目录（其中包含收藏、抗性、韧性、重复基础属性等）直接展示给用户。界面严格复用旧“管理”的 `StatCatalog.tape_main_stats` 15 项主词条顺序和 `gold_base_values` 11 项副词条顺序，再在 UI 边界映射为官方 property ID。实际背包使用的平攻、平防、平生命、环合、倾陷分别映射为 `AtkAdd`、`DefAdd`、`HPMaxAdd`、`MagBase`、`UnbalIntensityBase`；暴击与通用伤害使用当前快照实际采用的 `CritBase`、`CritDamageBase`、`DamageUpGeneralBase`。
- 副词条选择顺序写入 v5 `substat_priorities`，不再始终提交空列表；主词条仍为 schema 支持的单项硬筛选。
- 结果页继续复用旧 `PuzzleBoardWidget` 与 `_equip_card`，同时把角色标题、评分/评级徽标、拼图图纸、空幕属性汇总、空幕区和“驱动 (N个)”区的层级与样式收敛到旧 `_render_results`。不展示 UID、官方 ID 或 Top-K 开发信息。

### 回归与阶段状态

- 定向：`py -3.11 -m unittest tests.test_weighted_allocation_ui tests.test_priority_config_workflows tests.test_role_execute_workflows tests.test_allocation_context tests.test_allocation_solver tests.test_user_data_dao tests.test_encoding_guard -q`，157 项通过。
- 全量：`py -3.11 -m unittest discover -s tests -q`，664 项通过。
- `py -3.11 -m compileall -q src tests` 与 `git diff --check` 通过。
- 离屏真实静态库页面检查确认：19 名可选角色、15 项主词条和 11 项副词条完整加载；默认行保留官方套装、`core_main_property_id=None`。
- 工作区仍为 `2.0.0 @ 907fa14`，未暂存、提交或推送；四个受保护的伤害计算未跟踪文件未修改、未暂存、未删除。

第 5 阶段仍停在 **Sol high 增量只读复审** 门前。复审应重点检查本节的 `RoleSelector` 回调兼容、合法词条池到官方 property ID 的唯一映射、默认空主词条与官方套装图纸并存、`substat_priorities` 投影，以及新结果布局是否只复用旧组件而未改变求解语义。通过后再请用户进行人工界面验收；仍不授权提交、推送、伤害接线或真实装配。

## 37. Terra medium 第 5 阶段 UI 续改：驱动形状图、SHU4 标题与旧空幕汇总（2026-07-22）

### 用户反馈与实现

用户要求继续对照旧版结果界面：驱动标题使用 `SHU4` 一类形状名、恢复形状图片，并让空幕汇总继续模仿旧版。本轮仍只调整结果 UI 的兼容投影，没有改变求解、评分、拼图、候选池、Dispatcher、Top-K、SQLite schema、方案保存或真实装配。

- 驱动卡片不再使用背包物品名称作为标题，改为展示官方 geometry 的大写形式，例如 `shu4` 显示为 `SHU4`；空幕卡片仍显示空幕物品名称。
- `AllocationContext.shapes` 中的官方 geometry 与 `legacy_shape_id` 被整理为结果资源映射，例如官方 `shu4` 仅在旧 UI 资源边界转换为 `V_4`。求解与保存继续以官方 geometry 为事实来源；旧 ID 只用于加载既有质量分级图片，如 `V_4_Gold.png`。
- 结果中的空幕与驱动先适配为旧 `results_view` 的装备数据契约，再直接调用既有 `_equip_card`，因此形状图、品质、主副词条、分数与评级继续沿用旧版卡片表现。
- 空幕汇总改为直接复用旧 `_role_bonus_summary_panel`，保留“空幕属性汇总 / 角色属性汇总”切换、紧凑属性行和“更多”详情入口。仅在不带旧结果 mixin 的轻量测试宿主中保留简化回退面板。

### 回归、边界与复审门禁

- 定向：`py -3.11 -m unittest tests.test_weighted_allocation_ui tests.test_priority_config_workflows tests.test_role_execute_workflows tests.test_allocation_context tests.test_allocation_solver tests.test_user_data_dao tests.test_encoding_guard -q`，157 项通过。
- 全量：`py -3.11 -m unittest discover -s tests -q`，664 项通过。
- `py -3.11 -m compileall -q src tests`、页面单文件编译与 `git diff --check` 通过；离屏渲染确认拼图、旧空幕汇总和实际驱动形状图片均进入可见控件树。
- 工作区仍为 `2.0.0 @ 907fa14`，未暂存、提交或推送；四个受保护的伤害计算文件本轮未修改、未暂存、未删除。

第 5 阶段继续停在 **Sol high 增量只读复审** 门前。下一模型只审计本节 UI 增量：确认官方 geometry 到旧图片资源 ID 的映射不会泄漏回求解或持久化，驱动标题与图片来源一致，旧 `_role_bonus_summary_panel` 的适配输入完整，并确认没有静默修改计算语义。复审通过后再进入用户人工 UI 验收；仍不授权提交、推送、伤害接线或真实装配。

## 38. Terra medium 第 5 阶段数据源与旧 UI 校正：SQLite、官方空幕图、数值精度及有效词条色（2026-07-22）

### 数据源审计与修正

用户要求确认新页面没有以 JSON 文件存储，也没有继续使用旧角色或旧背包数据。审计发现此前加权页面仍会读取 `config/roles.json` 和 `StatCatalog.from_config_dir`，runner 还会把旧工作室角色路径传入 `build_allocation_context`；本节已从新页面运行链路删除这些依赖。

- 新页面不读写 `roles.json`、`stats.json`、旧 `my_roles_model` 或旧 `equipped_state`。角色、官方配装预设、词条和背包候选以静态游戏 SQLite 与固定的账号快照为事实来源；用户偏好、快照及方案继续写入 user SQLite v5。
- 官方配装预设中的推荐词条 ID 被投影成首次创建的 v5 有效词条权重；用户后续修改以 v5 profile 为准。新页面不再用旧 JSON 权重筛选角色，拥有官方配装预设的角色均可选择。
- 主词条 15 项、副词条 11 项的顺序以旧管理 UI 的既定用户语义固化为官方 property ID 常量，不再运行时读取旧 `stats.json`。
- `build_allocation_context(workshop_roles_path=...)` 仅保留显式传参的旧版兼容入口；加权页面不传该参数。旧版等价性测试需要兼容数据时会明确传入路径，不会污染新页面运行链路。
- `assets/game_ui/manifest.json` 仍是只读的打包资源索引，不是业务数据或用户数据存储；既有 SQLite 表中用于结构化方案载荷的 JSON 文本列也仍属于 SQLite 持久化，不代表重新使用旧 JSON 文件。

### 官方空幕图与旧 UI 表现

- `output_combat/HT/Content` 中保存的是导出元数据，实际对应 PNG 位于相邻的 `output/HT/Content/UI/UI_Icon/kongmu/256`。静态库 `equipment_item.icon_path` 共映射 38 个官方空幕物品 ID、36 张唯一 PNG。
- 资源构建清单新增 `equipment_items` 组，并按官方 item ID 写入 `assets/game_ui/equipment/core/`；图片缩放上限为 128 像素。轻量 UI 资源当前共 63 个、3.06 MiB。
- `GameUiAssetCatalog` 新增按官方 item ID 获取空幕图片的入口；结果卡继续复用旧 `_equip_card`，空幕显示官方 PNG，驱动仍显示旧形状品质图。
- 结果汇总改为只用固定官方候选的主副词条计算“空幕属性汇总”，不再调用会读取旧角色模型的旧汇总 Context。布局、紧凑行和“更多”入口继续模仿旧版，但删除当前数据无法准确支持的“角色属性汇总”切换。

### 数值与颜色修正

- 快照中存在 `0.17499999701976776`、`0.30000001192092896` 一类 float32 展开值。求解和评分保持原始值不变，仅在 UI 展示边界统一最多保留两位小数并去掉无意义尾零，例如显示为 `17.5`、`30`；整数值不再显示成 `64.0`。
- 结果卡此前误传空权重，导致所有词条均呈灰色。现在按角色 v5 `effective_property_weights` 投影到旧 `_stat_c` 色阶：无效词条保持灰色，有效词条按权重使用旧版的蓝、绿、黄、橙亮色。空幕汇总使用同一规则。

### 改动相关回归与阶段状态

- 改动相关回归：`py -3.11 -m unittest tests.test_weighted_allocation_ui tests.test_allocation_context tests.test_allocation_solver tests.test_game_ui_assets tests.test_priority_config_workflows tests.test_role_execute_workflows tests.test_user_data_dao tests.test_encoding_guard -q`，164 项通过。
- 相关文件编译、`git diff --check` 通过；离屏渲染确认官方空幕 PNG、旧驱动形状图、旧式布局及有效词条亮色进入可见控件树。
- 用户随后明确要求不再做全量回归；后续只运行本次改动直接覆盖的回归，除非用户另行授权，不再重复全量测试。
- 工作区仍为 `2.0.0 @ 907fa14`，未暂存、提交或推送；四个受保护的伤害计算文件未修改、未暂存、未删除。

第 5 阶段仍停在 **Sol high 增量只读复审** 门前。下一模型只审计第 38 节增量：确认加权页面运行链路不再读取旧 JSON/旧角色与旧背包数据；官方 item ID 到空幕 PNG 的映射完整；展示舍入没有进入求解；v5 有效权重只影响旧 UI 色阶；官方数据汇总没有重新引入旧模型。复审只读，不重复全量回归，不扩展到共享 Kernel、伤害接线、真实装配、提交或推送。

## 39. Terra medium 第 5 阶段 UI 续改：驱动主词条清理与装配按钮复用（2026-07-22）

### 用户反馈与实现

- 驱动卡顶部蓝色“攻击力”来自将官方候选的 `main_stats` 错投影到旧 `_equip_card` 主词条参数。旧版驱动卡只在该位置展示空值、词条全部位于副词条区；本节恢复该契约。驱动主词条不再显示在卡片顶部，但候选原始数据、评分和空幕属性汇总均未删除或重算。
- 词条配装页操作区新增“一键装配”和“自动装配”，每个角色结果标题右侧新增“装配”。三个入口均复用 `src/features/inventory/page.py` 的既有 SQLite 方案与装配实现，没有复制 RPC、界面自动化或验证逻辑。
- “一键装配”和单角色“装配”读取当前账号 `sync_settings.equipment_apply_method`：`nte_core` 进入已有极速装配，`gamepad` 进入已有自动装配；显式“自动装配”始终进入游戏界面自动化流程。
- 如果当前计算结果尚未保存，点击装配会先通过既有 `save_weighted_allocation_preview()` 保存当前统一方案，保存成功后才进入原装配确认流程；保存失败或账号已切换时不会调用装配实现。这样不会误用此前的活动方案。
- 批量装配只传入本次统一结果的角色列表。库存页既有批量入口增加可选角色子集参数；从库存页原按钮调用时仍保持“全部活动方案”的原行为，新页面不会把账号内其他旧方案一起装配。
- 本轮只完成按钮、保存前置和路由接线；没有执行真实游戏装配，没有改变 `EquipmentApplyService`、自动装配执行器、评分、拼图、Dispatcher、SQLite schema 或伤害模型。

### 改动相关回归与门禁

- `tests/test_weighted_allocation_ui.py` 覆盖：三个按钮可见、驱动 `main_stats` 不再形成顶部蓝色词条、配置为 `nte_core/gamepad` 时路由到对应既有实现、显式自动装配固定路由，以及装配前先保存当前预览。
- `tests/test_drive_assembly_ui_bridge.py` 覆盖极速和自动装配均只处理本次结果传入的角色子集；`tests/test_inventory_nte_core_route.py` 保持原库存页路由兼容。
- 改动相关回归：`py -3.11 -m unittest tests.test_weighted_allocation_ui tests.test_drive_assembly_ui_bridge tests.test_inventory_nte_core_route tests.test_encoding_guard -q`，67 项通过。未运行全量回归。
- 相关源码编译和 `git diff --check` 通过。工作区仍为 `2.0.0 @ 907fa14`，未暂存、提交或推送；四个受保护的伤害计算文件未修改、未暂存、未删除。

第 5 阶段继续停在 **Sol high 增量只读复审** 门前。下一模型只审计第 39 节增量：确认驱动主词条清理仅发生在显示投影；装配前确实保存当前冻结预览；配置路由复用旧实现；批量角色子集不会包含旧活动方案；账号切换与保存失败不会触发装配。复审不执行真实装配、不跑全量回归，也不重新审计共享求解内核。

## 40. Terra medium 第 5 阶段持久化修正：SQLite 自动回读与保存方案恢复（2026-07-22）

### 真实数据核对与问题结论

- 用户反馈“保存方案好像没用”后，使用当前账号 `accounts/default/user_data.sqlite3` 做只读核对：数据库 schema 为 v5，隐藏档案 `__weighted_allocation_role_priority__` 已有 16 个不可变版本，账号内共有 13 条方案。活动方案 13 为角色 1051「零」、快照 11、评分 299.12、9 件装备，payload 来源为 `weighted_allocation`，并固定档案 2/v16。
- 因此保存本身已经写入 SQLite；实际缺口是词条配装页只写不读。重开应用或切换账号后，页面没有恢复角色顺序、同优先级分组、空幕偏好和已保存结果，导致“保存方案”在该页面上看起来没有作用。
- 本节没有增加 JSON 文件存储，也不读取旧 `priority_config.json`、角色 JSON、旧背包或旧 equipped state。用户偏好、背包快照和方案仍全部来自账号 user SQLite；官方目录与装备语义仍来自静态游戏 SQLite。

### 自动回读与安全恢复

- 页面首次加载某账号时读取隐藏 v5 档案最新版本，恢复角色顺序、优先级组、套装、空幕主词条、副词条顺序，以及 UI 暂未暴露但已持久化的精确 `property_weights`、`property_limits` 和 `suit_requirement_mode`。再次计算时不会因为丢失隐藏字段而无意义地创建内容不同的新版本。
- 只有当最新档案内每个角色都存在活动的 `weighted_allocation` 方案，且全部固定到同一快照、档案版本、solver 和静态数据集时，才启动后台结果恢复。恢复会使用这些固定 ID 重新构造 Context 和统一解，并逐角色校验保存评分与完整 UID 集；任一项不一致只保留偏好回读并提示方案无法安全恢复，不会把过期结果启用为装配来源。
- 安全恢复成功后，旧式拼图、空幕汇总和装备卡重新渲染；`保存方案`、`一键装配`、`自动装配` 和单角色 `装配` 重新可用，且恢复预览标记为已保存，不会在装配前重复创建方案。
- 用户改变角色、顺序、同级关系或空幕偏好后，当前恢复/计算预览立即失效，保存与装配按钮禁用，必须重新计算，避免配置和可执行方案错位。
- `MainWindow._switch_account()` 在完成账号数据切换后立即刷新词条配装页；旧账号预览会先清理，再读取新账号 SQLite。相同账号内普通页面往返不强制覆盖尚未计算的内存编辑，也不重复执行恢复求解。

### 改动相关验证与门禁

- 当前真实账号恢复验证：档案 2/v16、快照 11、方案 13 成功重建为角色 1051、299.12 分、9 件装备，保存 UID 集完全一致。
- `tests/test_weighted_allocation_ui.py` 新增：隐藏 v5 权重/限制无损回投、SQLite 角色顺序与同级分组恢复、完整活动方案集合门禁；既有保存、装配和旧 UI 回归继续通过。
- 改动相关回归：`python -m unittest tests.test_weighted_allocation_ui tests.test_account_user_database tests.test_encoding_guard`，37 项通过；相关源码 `py_compile` 与 `git diff --check` 通过。按用户要求未运行全量回归。
- 工作区仍为 `2.0.0 @ 907fa14`，未暂存、提交或推送；四个受保护的伤害计算文件未修改、未暂存、未删除，也未执行真实游戏装配。

第 5 阶段继续停在 **Sol high 增量只读复审** 门前。下一模型只审计第 40 节增量：确认自动读取只使用 SQLite；不完整、过期或静态数据不一致的方案不会恢复为可装配预览；偏好隐藏字段无损回投；用户编辑和账号切换会正确失效旧预览；后台恢复不会绕过既有保存/装配边界。复审不执行真实装配、不跑全量回归，也不重新审计共享求解内核。

## 41. Terra medium 第 5 阶段 UI 续改：空幕替换与驱动优化（2026-07-22）

### 用户反馈与实现

- 词条配装结果继续复用旧 `_equip_card` 的标题行操作：空幕卡显示“替换”，驱动卡显示“优化”。按钮点击前沿用第 39 节边界，当前预览未保存时先写入 SQLite，保存失败或账号切换不会打开替换弹窗。
- 两个按钮复用 `src/features/inventory/page.py::_optimize_saved_equipment()` 的稳定快照候选、旧式弹窗、替换确认与新活动方案写入逻辑，没有复制候选读取或 SQLite 方案写入实现。
- 新页面显式传入当前冻结 `AllocationRolePreference` 的 v5 有效副词条/主词条权重，并以“按当前词条配装权重评分排序”展示候选；该分支不读取 `roles_db`、旧角色面板或旧 JSON，也不调用直伤排序。库存页原入口不传覆盖参数时仍保持既有直伤替换行为。
- 空幕分支在复用弹窗时使用“空幕”术语；库存页原有“卡带”文案保持兼容。候选只来自活动方案固定的同一 SQLite 快照，并排除已经被其他活动角色方案占用的 UID，避免手动优化破坏多角色 UID 唯一性。
- 替换成功后立即更新当前内存结果的装备 UID、单件评分、角色总分和统一总分，重新渲染旧式结果卡；方案已由原库存替换逻辑保存，不会再把替换前预览覆盖回数据库。
- 新保存方案 payload 增加逐件 `assignment_scores`。自动回读若发现方案 UID 与原求解 Top-1 不同，会在同一固定快照中按官方 geometry、保存锚点和逐件评分重建手动替换结果；缺少候选、评分或布局不一致时仍拒绝恢复。旧方案没有该字段但 UID 与求解结果一致时继续正常恢复。
- 本轮没有执行真实装配，没有修改评分公式、拼图、Dispatcher、SQLite schema 或伤害模型。

### 改动相关验证与门禁

- `tests/test_weighted_allocation_ui.py` 覆盖：空幕“替换”与驱动“优化”按钮可见、保存前置、官方 UID 字符串路由、v5 权重与空幕文案注入、排除其他角色已占用 UID、替换后的内存结果刷新、逐件评分写入 payload，以及按官方坐标恢复手动替换方案。
- 当前真实账号档案 2/v17、快照 11 的三角色保存结果再次自动恢复，角色顺序保持 `1051 → 1071 → 1025`。
- 改动相关回归：`python -m unittest tests.test_weighted_allocation_ui tests.test_role_execute_workflows tests.test_encoding_guard`，96 项通过；相关源码 `py_compile` 与 `git diff --check` 通过。按用户要求未运行全量回归。
- 工作区仍为 `2.0.0 @ 907fa14`，未暂存、提交或推送；四个受保护伤害文件未修改、未暂存、未删除。

第 5 阶段继续停在 **Sol high 增量只读复审** 门前。下一模型只审计第 41 节增量：确认两个按钮只复用 SQLite 固定快照替换链路；加权页面不读取旧权重/角色面板；候选不会造成跨角色 UID 重复；替换前保存、替换后内存结果与活动方案一致；逐件评分和官方坐标足以安全恢复手动方案。复审不执行真实装配、不跑全量回归，也不重新审计共享求解内核。

## 42. Terra medium 第 5 阶段 UI 微调：候选卡操作位置与收益占位（2026-07-22）

- 用户指出候选卡右上角已经显示评分，卡片下方再次显示“词条评分”属于重复信息。本节删除加权替换分支的该行；未来角色伤害链路接通后，再在这个位置显示真实“伤害收益”，当前不以词条分冒充伤害收益。
- 候选装备的“替换”按钮移入 `_equip_card` 标题行右侧，与结果页外层驱动“优化”按钮保持同一位置和样式。`_equip_card` 新增可选 `replacement_text`，候选驱动显式覆盖为“替换”，不再因存在形状图而显示“优化”。
- 旧库存直伤替换分支仍在候选卡下显示真实“直伤收益”，但替换按钮同样位于卡内标题行；SQLite 保存、UID 过滤、评分和持久化均未改变。
- 改动相关回归：`python -m unittest tests.test_weighted_allocation_ui tests.test_role_execute_workflows tests.test_encoding_guard`，97 项通过；相关源码 `py_compile` 与 `git diff --check` 通过。未运行全量回归、未执行真实装配，受保护伤害文件未修改。

第 5 阶段仍停在 **Sol high 增量只读复审** 门前。下一模型将第 42 节作为第 41 节的纯 UI 增量一起审计：确认加权候选不重复显示词条评分、未提前伪造伤害收益、候选“替换”位于卡片标题行且回调不变。无需扩展审计范围。

## 43. Terra medium 第 5 阶段 UI 微调：空幕替换卡官方图片（2026-07-22）

- 空幕替换弹窗的“当前空幕”和全部候选空幕卡现在都保留固定背包快照中的官方 `item_id`，并通过既有 `assets/game_ui/manifest.json` 与 `GameUiAssetCatalog` 显示对应的轻量官方 PNG。
- 图片仍复用旧 `_equip_card` 的 `item_icon_path` 展示入口；驱动替换卡继续使用原形状品质图，缺失映射时安全退回无空幕图，不改变候选排序、替换回调或 SQLite 保存逻辑。
- 运行时没有访问外部 UnrealExporter 原始导出目录，也没有新增 JSON 业务存储或读取旧数据；manifest 仅作为既有打包图片索引。
- 改动相关回归：`python -m unittest tests.test_weighted_allocation_ui tests.test_game_ui_assets tests.test_encoding_guard`，48 项通过；`src/features/inventory/page.py` 编译和 `git diff --check` 通过。按用户要求未运行全量回归。

第 5 阶段仍停在 **Sol high 增量只读复审** 门前。下一模型将第 43 节作为第 41—42 节的 UI 增量一起审计：确认当前与候选空幕均按快照官方 item ID 取打包 PNG，驱动图和替换数据链路不受影响，运行时不依赖外部导出目录。

## 44. Terra high 角色页重构第一阶段：官方指针、三装备上下文与直伤边际（2026-07-22）

### 用户确认后的新模型

- 用户明确放弃在旧 `my_roles.json` 模型上继续修补，改为新写角色 UI。账号侧角色记录只保存官方指针和用户养成状态：`character_id`、角色等级、突破阶段、觉醒等级、`fork_id`、弧盘等级、精炼等级、直伤技能 ID 与技能等级；角色名、面板数值、弧盘成长、技能倍率和说明均回查官方静态 SQLite。
- 用户数据库新增 v6 迁移 `007_user_data_v6.sql`，包含 `character_profile` 与 `character_profile_skill`。没有把中文名、角色面板、弧盘详情或旧 JSON 复制进账号库；v5 词条配装偏好保持原表和不可变版本语义。
- 主窗口“角色”导航已切换到 `src/features/official_role/page.py`。旧角色页面源码暂留作兼容参考，但新运行页面不读取 `my_roles.json`、`my_roles_model.json`、`roles.json`、旧弧盘配置或 `equipped_state.json`。

### 新页面与装备上下文

- 新页面直接展示官方角色目录、轻量角色 PNG、官方等级/突破面板、觉醒、技能目录和同类型弧盘。专属弧盘优先作为默认；专属默认 1 精炼、同类型常驻弧盘默认 5 精炼，用户保存的实际选择优先。
- 装备区域明确拆成三份且不互相覆盖：
  - “游戏当前”只读当前稳定背包快照中 `equipped_character_id` 对应的真实空幕/驱动；
  - “已保存配装”只读该角色活动 `loadout_plan` 固定的 `source_snapshot_id + UID`；
  - “理论最优”使用 v5 角色有效权重最高的四个官方属性 ID；无有效权重时固定回退为增伤、暴击、爆伤、攻击力%，空幕物品和主属性来自官方 `equipment_plan`。
- 当前静态库尚未把理论金色词条的标准档位/数值规范化为可查询表，因此理论页目前只准确展示目标属性与官方主属性，显式标记 `numeric_ready=False`，不伪造理论伤害或毕业率。

### 直伤边际接线与已知限制

- “游戏当前”与“已保存配装”已有真实词条数值；新页面按用户角色/技能/弧盘指针、官方成长和技能倍率构造输入，直接调用 `DamageCalculationService.calculate_direct()`，对攻击力%、暴击率%、暴击伤害%和增伤%逐项增加 1% 后重算边际收益。没有继续调用旧角色页的粗略直伤公式。
- 第一版固定使用 80 级外境目标、基础暴击 5%/爆伤 50%，并聚合所选技能的官方伤害记录。角色技能选择与等级已进入账号指针，可继续扩展敌方/场景选择。
- 弧盘精炼等级已正确保存为指针，但精炼条件被动目前只有官方描述/原始 buff，尚未规范化为静态数值，所以暂未进入直伤；UI 明确提示该限制。驱动固有基础攻击/生命曲线与理论词条档位也需后续规范化后，才能宣称完整面板和理论毕业率准确。
- 受保护的 `src/services/damage_calculation_service.py` 本节只被新服务导入和调用，没有修改；其文档和测试文件也未编辑、暂存或删除。

### 文件与验证

- 新增：`src/features/official_role/`、`src/services/official_role_page_service.py`、`src/storage/sqlite/schema/007_user_data_v6.sql`、`tests/test_official_role_page.py`。
- 修改：`src/storage/sqlite/user_data_dao.py`、`src/storage/sqlite/static_game_data_dao.py`、`src/ui/main_window_mixins.py`、`src/ui/app.py`、`tests/test_user_data_dao.py`。
- 改动相关回归：`python -m unittest tests.test_official_role_page tests.test_damage_calculation_service tests.test_user_data_dao tests.test_static_game_data_dao tests.test_account_user_database tests.test_extension_support tests.test_encoding_guard`，87 项通过。
- 相关源码 `py_compile` 与 `git diff --check` 通过。使用当前账号数据库的临时副本验证 v5→v6 迁移和离屏页面构建：schema 6、19 个官方角色、装备三页签正常；没有写入真实账号库。按用户要求未运行全量回归。
- 工作区仍为 `2.0.0 @ 907fa14`，未暂存、提交或推送，也未执行真实装配。

### 门禁

本阶段属于高难度的 schema＋数据边界＋新 UI＋计算接线增量。实现已达到“官方指针模型和第一版页面可运行”的阶段目标，但尚不适合宣称角色伤害/理论毕业率完全准确。

下一步只由 **Sol high** 做第 44 节增量只读审计：确认 v5→v6 迁移不丢偏好；账号库没有复制静态详情；三套装备上下文不会串用 moving-current；理论四词条规则与官方主属性正确；当前/已保存边际确实只调用新直伤服务；未规范化的理论词条和弧盘精炼被动没有被伪造。复审不跑全量、不修改文件、不进入词条档位或精炼 buff 解析。

## 45. Terra medium 角色页 UI 收口：复用旧版视觉骨架（2026-07-22）

### UI 对齐

- 保持第 44 节官方静态 SQLite、账号 v6 指针和直伤边际服务不变，只重排 `src/features/official_role/page.py`。页面恢复旧版的 20/16 边距、顶部“重置 / 保存”、拼音搜索、可拖动横向角色标签，以及每个角色独立滚动页。
- 角色内固定按旧版顺序展示“边际收益 → 驱动 → 基础加成 → 弧盘 → 空幕 → 词条权重”。角色头像、等级/突破、觉醒、直伤技能、官方生命/攻击/防御和技能等级目录合入“基础加成”；弧盘选择、等级和精炼回到独立“弧盘”分组。
- 原本合并的装备上下文拆回“驱动”和“空幕”两块，各自保留“游戏当前 / 已保存配装 / 理论最优”页签。理论驱动使用官方 `equipment_plan.module_item_ids`，理论空幕使用官方空幕和推荐主属性；词条权重单独显示最终四项理论目标。
- “重置”只丢弃当前角色尚未保存的控件修改并重新读取账号 SQLite，不覆盖其他角色、静态数据或方案；“保存”会一次保存所有已加载且被修改的角色指针。角色切换后的未保存编辑继续保留，关闭页面仍走既有保存 / 放弃 / 取消确认。
- 没有恢复旧 JSON、旧角色模型或旧伤害公式；未修改 schema、DAO、官方角色服务和受保护伤害计算文件。

### 改动相关验证与门禁

- `tests/test_official_role_page.py` 更新为检查旧版骨架：拼音搜索、角色横向标签、六个分组及顺序，并新增从页面编辑器保存觉醒指针后重新读取账号 SQLite 的持久化验证。
- 完成 1200×900 离屏视觉检查：19 个官方角色标签正常生成，首角色按六分组顺序渲染，头像和三套装备页签位于对应旧式分组内。
- 改动相关回归：`python -m unittest tests.test_official_role_page tests.test_damage_calculation_service tests.test_encoding_guard`，32 项通过；相关源码 `py_compile` 与 `git diff --check` 通过。按用户要求未运行全量回归。
- 工作区仍为 `2.0.0 @ 907fa14`，未暂存、提交或推送，也未执行真实装配。

第 5 阶段仍停在 **Sol high 增量只读复审** 门前。下一模型将第 45 节作为第 44 节的纯 UI 增量审计：确认旧版分组、搜索和标签页不会改变官方数据边界；多角色脏状态保存与当前角色重置不会串写；驱动、空幕三上下文仍保持隔离。复审不跑全量、不修改文件，也不扩展到理论词条数值或精炼被动解析。

## 46. Terra medium 角色页第二轮 UI 对齐：复刻旧组件内部结构（2026-07-22）

### 差异定位与修正

- 第 45 节只对齐了页面骨架，但角色分组内部仍残留新版的装备三页签、技能大表和四个权重色块。本节用 1200×900 同尺寸离屏截图直接对照 `src/features/role/page.py` 的真实旧页面，并分别检查页面顶部、中段和底部滚动位置。
- 边际区恢复旧标题“边际收益（按每单位收益排序）”、橙色直伤评分、右侧“自动设为权重 / 设为权重”和四列表格“参数 / 当前值 / 1单位 / 每单位提升”。按钮不会写回旧 JSON，而是明确引导到词条配装的角色管理。
- 驱动区恢复旧式“空幕加成”紧凑汇总：顶部装备数量与橙色直伤收益、内部“汇总属性（实时计算）”双端属性行、底部“查看驱动详情”。游戏当前、已保存配装和理论最优改为一个紧凑方案选择器；完整三上下文列表只在详情弹窗中展示，不再占满主页面。
- “基础加成”恢复旧版头像左栏、窄等级下拉、问号按钮和右侧生命/攻击/防御/暴击/爆伤双列只读面板。觉醒、直伤技能和技能等级放入问号按钮打开的养成指针对话框，账号指针仍可编辑并保留所有其他技能等级。
- “弧盘加成”恢复单行名称、等级、精炼与橙色收益，下面显示官方基础加成和官方精炼说明；“空幕”恢复当前空幕、方案选择、详情按钮与紧凑词条行；“词条权重”恢复旧版“添加词条”、逐行数值框和删除按钮布局。
- `load_official_role_detail()` 仅补充最终四项理论属性对应的实际 v5 权重显示投影；无有效保存权重时四个默认属性以等权 1.0 展示。没有改变四属性选择、求解、SQLite schema、角色指针或伤害计算语义。

### 验证与门禁

- `tests/test_official_role_page.py` 继续验证六个旧式分组及顺序，并新增：主页面只保留一个角色标签控件、驱动/空幕的三上下文紧凑选择器、默认理论权重投影及页面保存持久化。
- 改动相关回归：`python -m unittest tests.test_official_role_page tests.test_damage_calculation_service tests.test_encoding_guard`，32 项通过；相关源码 `py_compile` 与 `git diff --check` 通过。按用户要求未运行全量回归。
- 离屏对照确认旧页与新页在顶部边际/汇总、中段基础/弧盘、底部空幕/权重的卡片宽度、内边距、表格和逐行控件结构已经使用同一视觉骨架。工作区仍为 `2.0.0 @ 907fa14`，未暂存、提交或推送，未执行真实装配；四个受保护伤害文件未修改、未暂存、未删除。

第 5 阶段继续停在 **Sol high 增量只读复审** 门前。下一模型只审计第 46 节：确认权重显示投影不改变 v5 权威数据；主页面移除三页签和技能大表后仍能编辑并保存全部角色指针；详情弹窗三上下文继续隔离；旧式按钮不会回写旧 JSON。复审不跑全量、不修改文件，也不扩展到精炼参数解析或理论词条档位。

## 47. Terra high 角色推荐权重：开发期 API 静态快照与账号可编辑副本（2026-07-22）

### 数据分层与迁移

- 静态数据库升级到 schema v11，新增 `character_weight_recommendation` 与属性明细表。推荐记录使用官方 `character_id`、`equipment_attribute.attribute_id`，并显式区分 `workshop_api`、`workshop_cache` 和 `default`；运行时只读，不访问外部 API。
- 用户数据库升级到 schema v7，新增账号独立的角色权重种子与属性明细表。角色首次使用时从当前静态数据集复制一次；之后静态库更新不会覆盖用户编辑。已有 v5 计算配置中的角色权重会优先作为迁移种子，避免升级时丢失用户已经调整过的值。
- v7 账号权重是当前可编辑基线；v5 优化配置版本继续作为每次计算的不可变快照。账号权重变化后，旧保存方案不会冒充当前配置恢复，页面会保留角色、优先级、套装和空幕选择并要求重新计算。

### 开发同步、默认值和当前发行库

- 新增 `tools/game_data/sync_recommended_weights.py`：开发期从异环工坊开放 API 拉取后，先完成规范化和校验，再原子替换 `data/game_static.sqlite3` 中的推荐权重。`build_exe.py` 的开发同步入口已改用该工具，不再更新 `config/roles.json`。
- API 未返回的角色固定使用增伤 0.75、暴击 1.0、爆伤 1.0、攻击力% 0.70。当前发行库中伊洛伊 `1075` 明确为 `default`。
- 当前开发机没有 `WORKSHOP_API_KEY`，因此无法声称完成了 2026-07-22 的在线刷新。现有 v11 发行库使用仓库最后一次 API 同步缓存导入并标为 `workshop_cache`：用户角色目录 19 人中 18 个缓存、伊洛伊 1 个默认；若把两条主角配装图纸 ID 一并计入，静态表共 21 条、19 个缓存、2 个默认。这次导入仅用于生成只读二进制，应用运行时和新同步工具都不读取旧 JSON。拿到 key 后需运行新工具，将可用记录更新为 `workshop_api`。
- 当前发行库数据集为 `unversioned_20260723_update`，importer/schema 为 11，SHA-256 为 `1CAA1AA6BEFDECD3E871778EB2214368A07116276386B62C982CBD93F3F29FFF`，外键检查 0 项。2026-07-23 更新继续保留 `workshop_cache=19/default=2`；伊洛伊 `1075` 尚无在线 API 结果，仍使用默认权重。

### UI 与计算接线

- 词条配装的角色管理对话框新增 11 项数值权重编辑器，0 表示不参与评分；确认后立即保存到当前账号 SQLite。副词条优先顺序不再用固定 `1.0/0.85/...` 覆盖用户填写的精确权重。
- 加权页面初始化和角色详情均先确保账号副本存在，并从账号 v7 读取当前权重。角色页仍按旧 UI 骨架展示权重，并明确引导到词条配装角色管理中编辑；没有恢复旧角色 JSON 或旧权重链路。

### 改动相关验证与门禁

- 改动相关回归：`python -m unittest tests.test_recommended_weights tests.test_user_data_dao tests.test_static_game_data_dao tests.test_account_user_database tests.test_official_role_page tests.test_weighted_allocation_ui tests.test_encoding_guard`，87 项通过；没有运行全量回归。
- 相关源码 `py_compile`、`git diff --check` 通过；开发同步无 key 的 `--optional` 路径安全退出。静态库校验确认 schema 11、来源数量 `workshop_cache=19/default=2`、伊洛伊默认四权重和外键 0 项。
- 未写入真实账号数据库，未执行真实装配，未暂存、提交或推送；四个受保护伤害文件未在本节修改。

第 5 阶段继续停在 **Sol high 增量只读复审** 门前。下一模型只审计第 47 节：确认开发同步不会把 API key 或旧 JSON 带入运行时；静态推荐只复制一次且不会覆盖账号编辑；v5 历史权重迁移优先级正确；账号权重变化会使旧预览失效；精确数值不会再被副词条排序覆盖。复审不在线请求 API、不跑全量、不写真实账号库，也不扩展到伤害模型。

## 48. Terra medium 角色面板权重编辑、推荐回退与动态百分比清理（2026-07-23）

### 权重优先级与角色面板

- 角色详情现在明确按“账号 v7 可编辑权重 → 静态库工坊推荐”的顺序解析。首次没有账号记录时，`ensure_account_character_weights()` 只复制静态推荐；旧 v5 优化配置继续作为计算历史快照，不再反向成为新账号权重的默认来源。
- 角色服务同时返回完整 `property_weights`、来源类型和是否已有账号副本；理论最优仍只取权重最高四项，但角色面板不再只展示这四项，而是展示账号完整权重。
- “词条权重”组件恢复旧版结构：顶部“+ 添加词条”、可编辑三位小数权重行和行内删除按钮。添加池固定为当前配装支持的 11 个官方属性 ID；修改、添加或删除后进入角色页脏状态，点击顶部“保存”与养成指针一起写入当前账号 SQLite。
- 边际区的“设为权重”不再只弹出跳转提示，而是像旧版一样把当前直伤边际更新到已有的匹配权重，并立即刷新下方编辑行；自动/手动按钮的文字与样式切换也恢复旧行为，最终仍由顶部“保存”写入账号库。

### 百分比叠加 Bug 与旧版 UI 对齐

- 根因是新角色页 `_clear_layout()` 只删除直接控件，未递归清除 `QHBoxLayout` 中的属性标签。弧盘等级变化时旧的 `+45%` 等行仍留在父控件下，随后继续新增。
- 清理函数现在递归处理子布局，并在延迟销毁前立即隐藏、解除父子关系。实际连续切换弧盘等级 `70 → 60 → 80` 后，每次只保留当前等级的两条基础加成；同一清理函数也覆盖驱动方案、空幕方案和整页刷新。
- 继续对照旧 `BaseStatsWidget`：补齐“基础加成”组标题粗体、左侧区 8px 间距、等级行 6px 间距和右侧区 8px 间距。官方基础面板仍保持只读，这是新数据边界的有意差异；没有恢复旧 JSON 自定义面板值。
- 完成 1200×900 离屏顶部/中段/底部检查：六组顺序、基础双列、弧盘单行与基础加成、空幕紧凑行、完整权重编辑行均保持旧版视觉骨架。诊断旧页面时其既有加载逻辑重新序列化了 `accounts/default/config/role_order.json` 的同一计算顺序；未写真实账号 SQLite、角色养成或配装方案。

### 改动相关验证与门禁

- 新增回归覆盖：用户权重优先、首次复制忽略旧 v5 计算快照、角色面板权重可编辑并保存、连续修改弧盘等级不累积旧百分比行。
- 改动相关回归：`python -m unittest tests.test_official_role_page tests.test_weighted_allocation_ui tests.test_user_data_dao tests.test_recommended_weights tests.test_encoding_guard`，78 项通过；相关源码 `py_compile` 与 `git diff --check` 通过。按用户要求未运行全量回归。
- 未修改静态库/schema、求解器或四个受保护伤害文件，未执行真实装配，未暂存、提交或推送。

第 5 阶段继续停在 **Sol high 增量只读复审** 门前。下一模型只审计第 48 节：确认角色面板保存确实进入账号 v7；首次种子只来自静态推荐而不是 v5 历史；完整权重与理论 Top-4 没有混淆；递归清理不会误删固定标题或留下可见旧行。复审不跑全量、不写真实账号库，也不扩展到伤害或求解逻辑。

## 49. 角色面板空幕/驱动合并与新版直伤边际重算（2026-07-23）

### 旧版语义恢复

- 旧角色页的“空幕加成”本来就是驱动与空幕的合并汇总，不是两个主页面分组。新角色页已移除重复的独立“空幕”卡片，主页面恢复为“边际收益 → 空幕加成 → 基础加成 → 弧盘加成 → 词条权重”；详情按钮改为“查看空幕 / 驱动详情”，弹窗内再分别查看驱动、空幕及三套装备上下文。
- “空幕加成”的装备数量、汇总属性和直伤收益都同时使用所选上下文中的驱动与空幕。装备收益基线只去掉这两类装备，保留同一角色成长、技能和弧盘指针，避免把弧盘收益误算进装备收益。

### 新公式边际重算

- 复核旧版实现后，保留其“总面板 → 每项增加一个单位 → 重算直伤 → 按收益排序”的交互语义，但计算入口统一改为现有 `DamageCalculationService.calculate_direct()`，没有恢复旧粗略公式，也没有修改受保护的新伤害服务。
- 边际项不再固定写死四个百分比词条，而是从当前账号权重中选取新直伤公式可解释的攻击力%/固定攻击、生命%/固定生命、防御%/固定防御、暴击、爆伤和增伤。百分比每次增加 1%，固定值每次增加 1；表格现在展示实际当前值、单位、重算后伤害与相对提升，不再以“—”代替当前值。
- 工坊/账号默认只有四项时仍显示对应四项；用户权重中存在其他新公式支持的词条时会一并计算。环合强度和倾陷强度不属于当前直伤公式，继续不伪造边际结果。

### 改动相关验证

- `tests/test_official_role_page.py` 新增合并分组、合并详情入口、真实当前值、扩展边际词条及装备合并收益验证。
- 改动相关回归：`python -m unittest tests.test_official_role_page tests.test_damage_calculation_service tests.test_encoding_guard`，37 项通过；1200×900 离屏检查确认主页面只保留一个空幕/驱动汇总卡片。按用户要求未运行全量回归。
- 未写真实账号数据库，未执行真实装配，未暂存、提交或推送；四个受保护伤害文件仍未修改。

## 50. 旧 benefit_one 边际单位与直伤乘区明细（2026-07-23）

### 边际单位修正

- 第 49 节把所有百分比临时按 1%、固定属性按 1 点扰动，与旧角色页 `stats.json -> benefit_one` 的“一条标准边际”语义不一致。本节按用户确认值修正为：暴击率 1%、暴击伤害 2%、伤害增加 1%、攻击力 1.25%、固定攻击 8、角色对应异能伤害 1.25%。
- 这些值只作为旧 UI 语义固化在新角色服务中；新页面运行时仍不读取 `stats.json`。每项增加对应单位后，继续调用现有 `DamageCalculationService.calculate_direct()` 重算并显示相对提升。
- 边际表恢复旧版四列和筛选语义，只显示当前账号权重中可由旧 `benefit_one` 定义且能进入新版直伤公式的项目。生命、防御、环合与倾陷没有出现在旧 `benefit_one` 中，本节不猜测单位。

### 100% 直伤公式详情

- 角色面板直伤口径改为单个代表伤害记录、技能倍率固定 100%；倍率对应属性仍按官方技能记录选择攻击/生命/防御。边际百分比不受技能倍率归一化影响，直伤评分则恢复为可跨技能对比的 100% 面板值。
- 在旧边际框之后新增“直伤公式详情”：先列角色基础、弧盘、空幕/驱动的全部已有数值加成，再依次展示技能倍率、倍率对应属性、增伤、暴击、防御、抗性、易伤和独立乘区的结果及组成，最后展开完整乘法表达式和最终直伤。
- 详情由角色服务读取现有新公式输入和 `DirectDamageResult` 返回值组装；没有复制或修改受保护的伤害公式。当前尚未规范化的弧盘条件被动、易伤和独立增伤会明确显示为空乘区 1，不伪造来源。

### 改动相关验证

- `tests/test_official_role_page.py` 新增六种旧边际单位、异能属性映射、100% 技能倍率、八个乘区、乘积等于最终直伤、已有加成表及页面分组顺序断言。
- 改动相关回归：`python -m unittest tests.test_official_role_page tests.test_damage_calculation_service tests.test_encoding_guard`，37 项通过；相关源码 `py_compile` 与 1200×900 离屏布局检查通过。按用户要求未运行全量回归。
- 未写真实账号数据库、未执行真实装配、未暂存、提交或推送；`src/services/damage_calculation_service.py` 及其文档/测试未修改。

## 51. 角色首块权重头像、动态边际与旧式空幕/驱动详情（2026-07-23）

### 首块重排

- 角色内容顺序调整为“词条权重 → 边际收益 → 直伤公式详情 → 空幕加成 → 基础加成 → 弧盘加成”。词条权重卡左侧新增当前官方角色头像、角色名和账号/推荐权重来源；头像从基础加成卡移除，页面不再重复显示。
- 权重编辑和保存边界不变：仍编辑账号 v7 副本，顶部保存时与角色指针一并写入当前账号 SQLite；没有恢复旧角色 JSON。

### 动态计算链

- 新增角色页临时计算投影：把尚未保存的等级/突破、觉醒、直伤技能及等级、弧盘/等级/精炼和当前权重投影到内存 detail，再交给现有官方角色服务。该投影不写数据库。
- 边际表、100% 直伤公式详情和空幕/驱动整体收益注册为同一组刷新器。等级、觉醒、技能、技能等级、弧盘、权重或装备上下文变化时立即重算；连续刷新会先递归清除旧控件，离屏验证未产生重复边际表或公式结果。
- “游戏当前 / 已保存配装 / 理论最优”选择现在同时驱动边际、公式和装备汇总。理论上下文没有标准数值时明确显示不可计算；切回数值上下文后即时恢复。自动设为权重保持旧版语义：动态刷新后若开关开启，会把最新边际更新到现有匹配权重。

### 旧式空幕/驱动详情

- 删除原先“驱动/空幕外层页签＋三上下文内层页签”的嵌套详情。新弹窗顶部只保留一个装备方案选择器，内容按旧版顺序显示“空幕属性汇总 → 空幕 → 驱动（N个）”。
- 当前/已保存上下文继续复用主窗口旧 `_equip_card`：空幕使用官方轻量 PNG，驱动使用官方 geometry 映射的旧形状品质图；每件空幕和驱动下方分别显示从同一固定上下文移除该物品后，以新版 100% 直伤公式重算的真实收益。
- 理论上下文仍只展示官方空幕、主属性与驱动目录；由于标准词条档位尚未入库，不生成卡片数值、逐件收益、旧拼图或替换按钮。这样保留旧视觉层级，但不伪造当前数据没有的图纸和操作状态。

### 改动相关验证

- `tests/test_official_role_page.py` 新增首块顺序与头像唯一性、等级和装备上下文动态刷新、重复控件清理、旧详情三段结构、逐件收益及旧卡片/`V_4` 形状映射断言。
- 改动相关回归：`python -m unittest tests.test_official_role_page tests.test_damage_calculation_service tests.test_encoding_guard`，39 项通过；相关源码 `py_compile`、1200×900 主页面离屏检查和 1000×700 详情离屏检查通过。按用户要求未运行全量回归。
- 未写真实账号数据库、未执行真实装配、未暂存、提交或推送；受保护的伤害服务、文档和测试未修改。

## 52. 新角色与词条配装共享空幕/驱动加成口径（2026-07-23）

### 共享计算与问题修复

- 新增 `official_equipment_bonus_service`，新角色页的空幕/驱动汇总、新角色页整体直伤收益输入和词条配装结果的“空幕属性汇总”统一调用同一个官方 ID 计算函数，不再分别直接累加快照 `main_stats/sub_stats`。
- 空幕继续使用快照中的真实主副词条；驱动忽略快照中的等级主属性，按旧配装口径由格数推导固有攻击力和生命值，再叠加副词条。金、紫、蓝分别复用现有 `calculate_drive_main_stats()` 的 `1.0 / 0.8 / 0.6` 品质系数。
- 词条配装汇总同时按角色 `extra_shape_label` 统计匹配驱动，并把 `extra_shape_buffs` 的旧百分比显示值规范化为官方小数后叠加。百分比标记由静态属性目录传入，不再靠固定属性黑名单猜测。
- 新角色页整体直伤收益通过同一共享装备总量进入既有 `DamageCalculationService`；驱动快照主属性即使随等级变化或异常，也不会再与格数固有白值重复计算。
- 按用户要求未修改旧角色页、旧配装页及其底层汇总实现；本节只改两个新页面和新共享服务。

### 改动相关验证

- 新增金/紫/蓝两格驱动固有白值、空幕主副词条、空驱动过滤、角色形状额外加成和词条配装共享汇总回归；新角色详情额外验证驱动快照主属性不会改变整体直伤收益，并显示四格金色驱动的 `+92` 攻击与 `+1120` 生命汇总。
- 改动相关回归：`python -m unittest tests.test_bonus_summary tests.test_character_stat_engine tests.test_official_equipment_bonus_service tests.test_official_role_page tests.test_weighted_allocation_ui tests.test_encoding_guard`，69 项通过；`git diff --check` 通过。
- 未修改受保护伤害服务，未写真实账号数据库，未执行真实装配，未暂存、提交或推送。

第 5 阶段继续停在 **Sol high 增量只读复审** 门前。下一模型只审计第 52 节：确认两个新页面确实共享同一装备加成函数；驱动固有白值按格数和品质生成且不重复累加快照主属性；角色形状加成只按匹配驱动件数叠加；旧版本页面保持不变。复审不跑全量、不写真实账号库，也不扩展到伤害公式。

## 53. 新版配装三层存储与确认保存事务审计（2026-07-23）

### 当前结构确认

- 实际背包已由不可变 `inventory_snapshot / inventory_item / inventory_item_stat` 表完整表达；每件空幕或驱动保存官方 UID、词条、品质、等级、当前使用角色及驱动位置，稳定快照可作为计算固定输入。
- 新版最后一次计算结果目前只存在于内存 `WeightedAllocationPreview`，没有独立的计算批次、角色结果和分配项持久化表。当前“恢复”实际依赖已保存方案重新计算，不等同于保留最后一次未确认计算方案。
- 已保存方案使用 `loadout_plan / loadout_plan_item`，每个角色只能有一条 active 方案，但没有保存批次 ID，也没有数据库级“所有 active 方案中同一装备 UID 只能出现一次”的约束。

### 当前确认保存缺口

- `save_weighted_allocation_preview()` 按角色循环调用 `save_role_plan()`；每个角色内部独立提交。后续角色失败时，前面角色已经保存，整次多角色确认不是原子事务。
- 保存桥接会确认每个 UID 存在于计算固定快照且 kind 正确，但不会确认固定快照仍是当前实际背包，也不会在保存入口重新验证所有角色合并后的 UID 唯一性。
- `save_loadout_plan(is_active=True)` 只停用同一角色的旧 active 方案。若新方案使用了其他角色已保存方案中的空幕或驱动，旧角色方案不会先“脱下”这些 UID，因此 active 保存空间可能出现跨角色重复占用。
- 实际一键装配服务会在执行前重新读取当前稳定背包，并由游戏 RPC 完成真实移动，再用新快照确认；“保存方案”不应直接修改实际背包或调用装配 RPC。

### 建议事务边界

- 增加持久化计算批次：批次头固定账号、背包快照、偏好版本、静态数据版本和求解器版本；角色结果与装备分配项作为不可变子记录。最后一次计算与用户已确认方案由状态明确区分。
- 用户确认时先在内存完成全量校验：计算批次仍属于当前账号；固定快照满足选定的过期策略；全部 UID 在实际稳定背包存在、kind 一致、角色间无重复；驱动坐标与空幕数量合法。
- 在一个 `BEGIN IMMEDIATE` 事务内读取全部 active 保存方案，先从所有角色方案中移除本次计算占用的 UID，再整体覆盖本次计算涉及的角色。被抢占但未参与本次计算的角色应生成一条扣除 UID 后的新修订；若已不完整则明确标记 incomplete，而不是保留可执行 ready 状态。
- 最终提交前再次验证 active 保存空间的全局 UID 唯一性；任一步失败则整批回滚。建议增加保存批次表和当前分配表，并由当前分配表对 `(uid_serial, uid_slot)` 建立主键/唯一约束，`loadout_plan` 继续保留历史审计。

本节只完成数据结构与事务审计，未修改 schema、DAO、保存流程或真实账号数据库。下一步实现应先由 Terra medium 完成 schema/DAO/纯事务测试，再进入 Sol high 只读审计；不要直接从 UI 循环保存角色。

## 54. 新版词条配装原子保存、替换与优化统一（2026-07-23）

### 公共保存边界

- `SavedStateLoadoutBridge` 新增只校验和转换、不写数据库的 `prepare_role_plan()`；原有 `save_role_plan()` 继续复用它，旧页面默认保存行为不变。
- 新版 `save_weighted_allocation_preview()` 不再逐角色提交，而是先准备全部角色方案，再一次调用 `UserDataDao.replace_active_loadout_plans()`。任一角色、UID、坐标或快照校验失败时，整批回滚。
- DAO 在同一个 `BEGIN IMMEDIATE` 中同时校验计算固定快照和当前实际稳定背包：全部空幕/驱动必须仍存在且 kind 一致，批次内 UID 必须全局唯一，每个激活方案至少保留一个驱动。
- 提交前会停用本次涉及角色的旧 active 方案；若新方案抢占其他角色已保存方案中的装备，会先从该角色方案卸下冲突 UID，再生成新的 active 修订。完全被清空的角色不再保留 active 方案；残余修订把 `payload.source` 标为 `active_plan_overlay`，避免被新版页面误识别为可精确恢复的原始计算结果。
- 插入全部新方案后再次扫描所有 active 方案的 UID；只要仍有跨角色重复占用，事务整体回滚。保存仍只修改用户 SQLite，不调用游戏装配 RPC。

### 新页面替换与优化

- 新页面保存、角色/全体装配、空幕替换和驱动优化统一复用当前预览及账号校验，并通过“当前预览已经保存”公共门禁决定是否先保存。
- 替换预览更新抽成纯函数：固定使用计算上下文中的候选，校验装备类型、驱动形状及当前方案全局 UID 唯一性，只替换一个目标 UID，并按原分配项评分重算角色和总评分。
- 旧库存替换弹窗新增可选持久化回调；旧页面不传回调时仍走原单角色保存，新词条配装页面传入原子保存回调，因此替换/优化确认后不会先留下跨角色重复方案。

### 改动相关验证与剩余项

- 新增跨角色抢占后自动卸下、批次中重复 UID 拒绝、当前实际背包二次校验、任一角色失败整批回滚、替换候选占用冲突拒绝及新版替换重新原子保存回归。
- 改动相关回归：`python -m unittest tests.test_saved_state_loadout_bridge tests.test_user_data_dao tests.test_weighted_allocation_ui tests.test_sqlite_equipment_replacement tests.test_saved_plan_optimization_context tests.test_encoding_guard -q`，90 项通过；相关源码 `compileall` 与 `git diff --check` 通过。
- 本节没有新增 schema 或计算批次表；第 53 节建议的“最后一次未确认计算结果持久化/批次审计 ID”仍是后续独立工作，不影响本节确认保存事务。未写真实账号数据库，未执行真实装配，未修改受保护伤害文件，未暂存、提交或推送。

第 5 阶段继续停在 **Sol high 增量只读复审** 门前。下一模型只审计第 54 节：确认批量覆盖只在一个事务中提交；当前实际背包校验不会被固定快照替代；被抢占角色的残余方案不会参与精确恢复；新版替换回调失败时旧页面默认路径不受影响。复审不运行真实装配、不写真实账号库，也不扩展到持久化计算批次。

## 55. 旧版毕业方案静态化与新版角色页 O(1) 毕业率（2026-07-23）

### 固定模板与数据边界

- 静态数据库升级到 schema v12，新增 `character_graduation_template`。每个可用角色保存完整角色面板、弧盘、空幕/驱动模板、来源类型和预计算直伤基准，模板只在开发构建期写入，应用运行时只读。
- 模板沿用旧版毕业方案：角色满级满突破与六觉、可用技能满级、满级精 1 专属弧盘、20 格金色驱动、四条最高权重副词条、固定图纸额外形状加成、四条最高权重空幕副词条，并枚举正权重空幕主词条选择直伤最高项。
- 18 个旧配置角色标记为 `legacy_role_config`；伊洛伊 `1075` 尚无旧 `roles.json` 配置，使用官方默认套装和静态推荐权重生成 `official_default` 模板。旧配置只由开发工具读取，不进入新版运行时。
- `build_static_database.py` 会在基础静态表生成后自动写入毕业模板；`sync_recommended_weights.py` 更新工坊权重后也会原子重建模板，避免推荐权重与毕业基准脱节。两个脚本都支持外部 `--database` 和 `--config-dir` 参数。

### 新版毕业率

- 新角色页不再在每次刷新时读取 `stats.json`、调用图纸求解器或枚举空幕主词条；只从静态 DAO 读取当前角色的 `benchmark_damage`，毕业率继续按“当前 100% 直伤 / 固定毕业基准 × 100%”计算。
- 完整模板保留在 SQLite 便于审计和后续展示，但页面计算只读取基准伤害，避免 UI 刷新触发重复重计算。旧角色页和旧配装页未修改。
- 受保护的 `damage_calculation_service.py` 及其文档、测试未修改；模板构建只调用现有公开直伤入口。

### 门禁与下一步

- 本节新增 schema/DAO、模板生成器、构建/权重同步接线和静态库发行数据；相关 DAO、静态数据库、新角色页、词条配装和保存事务回归需在收尾时统一通过。
- 第 5 阶段仍停在 **Sol high 增量只读复审** 门前。下一模型只审计第 55 节及其直接调用点：确认 19 个角色均有正数模板、同步权重后模板会重建、运行时不再读取旧配置或求解、旧页面和受保护伤害文件保持不变。
