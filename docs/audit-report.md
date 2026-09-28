# 审计与修复（2026-09-28）

分支 `perf/snapshot-write-and-indexes`，PR #65 的基准是上游 `test`，目前是草稿。改动都在独立副本 `_repo-perf` 里，`_repo` 的 `main` 和 `deploy.ps1`、`deploy_to_program_files.bat` 没有动过。外部二进制、游戏程序、组件包只做了只读核对，没有改过。

## 已修复

| # | 问题 | 改动 | 测试 |
| --- | --- | --- | --- |
| F1 | 测试进程被非 GUI 的 Qt 实例污染，Qt 触发致命退出（`0xC0000409`），测试进程直接没了，分片拿不到汇总行 | 加进程级唯一 GUI 应用的 fixture，相关测试改用它；另加守卫禁止测试自建非 GUI 实例 | `test_qt_application_isolation_boundaries.py`（2 条） |
| F2 | 迁移出现外键违规时既不能回滚，也会被后续启动跳过 | 外键校验移到 `commit()` 之前 | `test_user_data_migration_foreign_key_rollback.py`（改动前 `43 != 44`） |
| F3 | 账号切换可能半途失败，账号索引已落盘但内存代次没推进 | `AppContext` 加 `_run_switch_step`，stop/rebuild/notify/start 逐项隔离并记录告警 | `test_app_context.py` 新增 3 条 |
| F4 | 过期扫描结果仍会搬移截图、删掉临时目录 | 提交前复核代次，抽出 `_is_stale_result` / `_stale_scan_stats` | `test_streaming_scan_commit_boundaries.py`（改动前红） |
| F5 | 评分与配装内核重复计算：属性上下限搜索最坏要把全量评分跑 256 遍 | 角色级预计算、名称归一化缓存、搜索复用首轮结果（`reuse_scores`） | `test_allocation_kernel_property_limits.py`（4 条，改动前 `1 != 2`；该路径此前零覆盖） |
| F6 | 静态库短生命周期开连时重复做 Windows 路径解析 | `_is_temp_path` 去掉二次 `Path.resolve()` 并缓存临时目录判定 | `test_static_storage_perf.py` |
| F7 | 快照写入逐条 `execute` | 改成分组 `executemany`，写入顺序和事务边界不变 | 既有快照测试 |
| F8 | `static_game_data_dao.py` 878 行，超了 `AGENTS.md` §7 的 800 行契约，守卫测试会红 | 拆出角色成长与权重两个 mixin，878 → 756 行 | `test_repository_hygiene.py` |
| F9 | 装备详情曲线按等级逐条查询，且物品缺失时抛未处理的 `StopIteration` | DAO 新增 `evaluate_equipment_base_attribute_curve_levels`，一次读曲线在内存求值，缺物品返回空曲线 | `test_static_catalog_equipment_page_ui.py`（2 条，改动前红） |
| F10 | 「清空配装」逐条写库，中途失败会留下部分已生效状态且界面不刷新 | DAO 新增 `deactivate_loadout_plans`（单事务批量），控制器改用并在 `finally` 统一失效缓存 | `test_loadout_plan_batch_deactivate_dao.py`（3 条） |
| F11 | 账号切换和启动时在 GUI 线程同步重建配装目录（中位 1174 ms），是「经常未响应」的主因 | 抽出 `_read_and_apply_allocation_catalog` / `_start_allocation_catalog_worker`，GUI 宿主走后台线程；`app.py` 的收尾移进 `_finish_account_switch`，等数据就绪再执行 | `test_main_window_catalog_load_boundaries.py`（2 条，改动前红） |
| F12 | 角色头像有两套查找路径（正式图鉴 + 遗留兼容查找） | 经裁定该兼容查找已无实际使用者，索引、遗留查找、别名表、归一化函数和回退调用一并删除，头像统一取 `equipped_character_icon_path` | 过时用例删除，受影响测试通过 |
| F13 | 配装目录重建逐角色 N+1，一次重建 1417 条 SQL | 新增 6 个批量 DAO，图纸改用 `list_equipment_plans`，空输入短路，目录作用域与账号设置副本按请求缓存，弧盘模板改用 `dataset_info()` | 1417 → 887 条 SQL，中位 1174 → 1027 ms（后又降到 663 ms，见 F18/F19） |
| F14 | 静态库共享连接跨线程没有保护，退出时也不回收，残留句柄抛裸 `sqlite3.ProgrammingError` | `_rows` 与 `close()`/`close_shared_connections()` 共用一把锁（SQLite 本身是 serialized 模式，所以没有引入连接池）；「连接已关闭」转成 `StaticGameDataError`；`MainWindow.closeEvent` 退出时回收 | `test_static_connection_lifecycle_boundaries.py`（改动前 1 失败 1 报错） |
| F15 | 局部/按角色响应可以推进正式库存指针：action 回包只要求覆盖「本次变更目标」，而导入只校验 `complete` 标志 | 替换前要求回包覆盖当前完整库存的全部 UID，否则回落到状态投影和冻结守卫；允许额外行（更新的完整回包），不允许缺行 | `test_warehouse_state_management.py` 新增 1 条（改动前失败）；同步路径的既有守卫由 `test_inventory_snapshot_stabilizer.py` 覆盖 |
| F16 | `ScanWorkerThread.run` 失败或取消时不释放 scanner | 加 `finally` 释放，与同文件既有实现一致 | `test_scan_worker_lifecycle_boundaries.py`（改动前红） |
| F17 | 原生会话关闭在 GUI 线程同步等待，最坏约 6 s | HUD 超时 2.0→0.5、快照域 1.5→0.4，实例不可用时跳过快照关闭 RPC | 参数级改动，既有会话测试通过 |
| F18 | 逐角色各开一次账号库，一次重建开了 25 次，建连和迁移检查被按角色数放大 | `load_official_role_detail` 新增可选 `user_dao`，目录重建复用同一个连接，只关自己打开的那个 | `test_allocation_catalog_connection_boundaries.py`（改动前 25 次） |
| F19 | 弧盘模板投影每次重建都重算（51 个弧盘逐级做面板统计，约 0.95 s，纯 CPU），而它只随静态数据集发布变化 | `fork_templates_as_weapon_models` 按数据集身份缓存；数据集变化或 `clear_weapon_model_cache()` 后重算 | `test_fork_weapon_model_cache.py`（3 条） |

## 发现但没修

| # | 问题 | 为什么没修 |
| --- | --- | --- |
| N2 | 轴分页缺 `complete` 字段时默认按已完成处理，最终化路径的默认值相反 | 设备是否总会发这个字段，需要实机或上游协议证据。行为没改，只用测试把当前判定钉住，等有证据再统一，那条测试会直接变红 |
| N3 | 配装目录重建还是要 663 ms，只是不在 GUI 线程了 | 后台读取期间执行页会短暂用旧目录。剩余时间主要在 N4 和 N11 |
| N4 | 逐角色详情仍是剩余最大项（`load_official_role_detail` 24 次，约 0.615 s） | 做完大概能从 663 ms 降到 300~400 ms，但它之后就不是瓶颈了。风险不划算：默认档案解析依赖技能、觉醒、面板成长，另写一份投影入口容易和角色页算出不同结果，而且不报错；数据不足时还会从「抛错」变成「静默返回空投影」。要做也得是给现有函数加 `projection_only` 跳过无关部分，不能另起炉灶，并且得先有全角色逐项对比的测试。没有那条测试就不做 |
| N7 | 装备域完整性由背包域标志代替，能力不足时沿用遗留的 ready 布尔 | 要实机核对各域真实能力才能判断 |
| N9 | 「清空配装」的控制器分支缺 Qt 层回归测试 | DAO 原子性已由 F10 覆盖，控制器交互要 Qt 测试 |
| N10 | `_template_root_candidates` / `_TEMPLATE_ROOTS` 变成只写不读的死访问器 | `configure_warehouse_view_template_roots` 是组合根 API，简化要同时动 `app.py` |
| N11 | `list_fork_templates` 一次重建被调用 3 次（约 0.366 s） | 合并要跨模块传弧盘列表，或在 DAO 层按数据集缓存，而它返回可变列表，缓存有被改写的风险。收益约 0.24 s，性价比一般 |

## 数字

`tools/quality/bench` 跑出来，预热 + 多轮取中位，单次运行不作数。

| 场景 | 改动前 | 现在 |
| --- | --- | --- |
| 静态库开连（含一次查询） | 5.39 ms | 2.0 ms |
| 评分整轮（700 件 × 8 角色） | 556 ms | 159 ms |
| 属性上限排除搜索（每轮） | 403 ms | 19.5 ms |
| 快照写入（600 件） | 209 ms | 185 ms |
| 装备详情曲线（53 属性） | 144 ms | 16 ms |
| 配装目录重建 | 1174 ms（1417 条 SQL） | 663 ms（887 条 SQL） |
| 原生会话关闭的 GUI 线程等待（最坏） | 约 6 s | 约 2 s（按超时参数估的，没实机计时） |

## 验证

| 项目 | 结果 |
| --- | --- |
| `run_tests.py core -j 3` | EXIT=0，三分片各 `Ran 404 tests`，依次 `OK (skipped=3)`、`OK`、`OK`（提交 `69966a9`） |
| `ruff check src tools tests` | All checks passed |
| `mypy` | 既有报错不变，没有新增（改动文件逐个做前后对比） |
| `run_tests.py full` | 早前跑过一次：3043 条，1 个 error（`test_static_data_manifest` 缺 `dist` 里的 OCR 模型，环境问题）。这次没重跑 |

行数口径提醒：800 行契约用 `len(read_text().splitlines())` 判，别用 PowerShell 的 `Measure-Object -Line`，同一个文件它会给出明显偏小的数（891 对 798）。

## 范围

- PR 基准是 `test`，没有向上游 `main` 发起过合并。
- 没有改 IPC 协议，也没有碰 `nte-core` / `nte-analysis-core` 组件和 `third_party` 里的任何二进制与清单。
- N2 的行为保持原样，只加了测试和证据说明。
- 头像遗留查找的移除依据是「经裁定该兼容查找已无实际使用者」，不以目录是否存在作为理由。
- 复核原则：每处改动都要说清为什么必要、拿什么证明。证明不了收益的改动（比如一度合并过的 `summary()` 逐表 COUNT）已经回退；改动前后都能通过的测试也删掉了。
