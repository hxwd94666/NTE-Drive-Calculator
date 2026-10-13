# Calc UI 响应性验证记录

状态：保留历史优化对比；当前基础权重基准已恢复并完成新的冷/热与资源代理测量。
历史表不解释当前源码，当前基准不等同于整应用、低配真机或发行包验收。
交付行为见[功能契约](../features.md#页面响应性与后台任务)，边界及目标见[优化方案](../roadmap/ui-responsiveness.md)。

## 历史优化：测量条件与口径

- 同一台 Windows 机器，Python 3.11.9、Qt offscreen。两侧使用同一冻结的 SQLite 一致性副本、发行配置、工坊模板及图片目录。
- 优化前源码保留开发开始时的工作区状态，包括其他开发者的未提交改动；不是拿另一个 Release 或不同账号作比较。
- 角色页 24 个角色，基础权重 25 个角色，工作台库存 352 件。全部对比脚本串行运行，每项热状态 30 次；目录权重编辑 5 次。
- 测量真实角色页、权重页、真实计算角色选择器及 Service/DAO；不启动完整 MainWindow、Core/DLL、游戏操作或实际同步连接。
- 写入仅发生在新建的隔离副本。没有修改活动账号、静态库、评分公式、分配算法或外部协议。
- 单位为毫秒。P95 为排序后向上取整的第 95 百分位。回调表示主线程返回；就绪包含后台读取及结果应用；事件循环延迟由 10 ms 心跳测得。
- 基础权重保存/重置脚本验证本页提交完成，并单独验证计算目录刷新；不把本页完成时间称为整个计算目录已经就绪。

## 热状态前后对比

| 场景 | 回调前 P95 | 回调后 P95 | 就绪前 P95 | 就绪后 P95 | 事件循环延迟前 P95 | 事件循环延迟后 P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 角色页热切换（30 次） | 803.164 | 0.187 | 803.229 | 34.984 | 793.190 | 2.313 |
| 工作台摘要（30 次） | 87.596 | 0.362 | 87.599 | 117.285 | 77.612 | 2.414 |
| 基础权重热切换（30 次） | 1042.321 | 0.568 | 1042.333 | 50.118 | 1034.167 | 12.231 |
| 保存基础权重（30 次） | 4314.843 | 1.098 | 4314.850 | 495.086 | 4304.876 | 8.878 |
| 重置当前权重（30 次） | 2328.415 | 0.462 | 2328.421 | 1934.309 | 2318.623 | 8.654 |
| 无变动的计算目录校验（30 次） | 5.769 | 0.300 | 6.607 | 34.112 | 0.000 | 1.493 |
| 无关写入后的计算目录校验（30 次） | 8.573 | 1.000 | 1697.790 | 115.039 | 36.988 | 2.803 |
| 新权重的计算目录刷新（5 次） | 7.196 | 1.249 | 1221.878 | 1342.955 | 52.608 | 35.124 |

主要收益是主线程不再等待整页查库及同步刷新。后台总耗时并非处处缩短：未变化目录校验增加了后台读取/调度，
工作台摘要就绪 P95 也更长；这些等待期间窗口仍可处理输入。权重重置单次最大就绪时间在本机样本中仍可能更长，
不以回调变快宣称所有计算都变快，也不代表游戏帧率、低配设备通用结果或 GPU 绘制性能。

## 首次进入

以下是每侧一个独立进程的首次样本，包含所测窗口/页面的首次字体、布局及图片准备，不是整个安装包启动时间。

| 场景 | 回调前 | 回调后 | 就绪前 | 就绪后 | 事件循环最大延迟前 | 事件循环最大延迟后 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 角色页面 | 934.578 | 85.122 | 935.257 | 668.763 | 924.636 | 75.248 |
| 工作台摘要 | 53.112 | 0.046 | 53.116 | 79.155 | 43.212 | 2.351 |
| 基础权重 | 194.342 | 0.196 | 194.348 | 431.829 | 184.412 | 4.319 |
| 计算目录及真实角色选择器 | 1653.390 | 0.234 | 1664.488 | 1940.485 | 1643.523 | 113.971 |

计算目录初版后台化后仍有首次约 626 ms 的主线程停顿，定位为拼音库冷加载及排序准备；
纯排序键现已在后台准备并有界缓存。初版样本保留为诊断，不混入上表的最终对比。

## 重复工作与结果一致性

- 相同同步版本连续通知 30 次：首页摘要查询 30 → 0；首页隐藏期间通知 30 次：查询 30 → 0，返回时读取最新版本。
- 无关账号写入 30 次：完整计算目录读取 30 → 0；角色卡两侧均不重建。不是只减少控件刷新，还避免了整目录读取。
- 真正修改权重 5 次：两侧均读取/更新目录 5 次，逐次确认新暴击权重生效；旧冻结评分引擎保持不变。
- 基础权重 30 次保存及 30 次重置：同步全目录读取 60 → 0，提交通知 0 → 60。
- 全部 24 个角色详情模型（只排除已移至按需读取的候选池及其加载标记）前后逐字段摘要一致。
- 工作台完整摘要、25 角色最终权重表单、30 次保存结果、计算初始目录及 5 次修改后的目录摘要均一致。
- 权重表单只读解析不再落库默认值；仍保持 DAO 原来的零权重过滤，未把零值额外加入下拉或评分口径。

## 行为与静态检查

最终选定专项：**164 项通过**。完整命令、字面输出及退出码保存在本机验证记录。
覆盖单活动/单最新读取、后台线程与 UI 回调边界、取消/关闭、旧账号回调丢弃、有界缓存、草稿保护、
保存失败/提交后刷新失败的区分、重复提交拒绝、离页保存只导航一次、正式账号切换阻止活动提交、
无关写入复用、读取期间相关变化的有限重试、权重改变后恢复的修订检查、下一次计算门禁，以及原优先级/暴击设置规则。
计算资料未就绪时依赖页面暂停刷新和操作；就绪后仅刷新仍显示的页面，并保留其他 owner 原有的禁用状态。

Ruff `E9/F63/F7/F821/F401`、差异空白及本轮 Python 文件 800 行门禁通过。
另两项旧测试在保存的优化前源码中同样失败，不属于本轮新增失败：

- `test_basic_weight_reference_catalog.py::test_reference_roles_load_save_and_reset_basic_weights`：旧测试预期 reference 数据来源，当前正式计算路径使用完整游戏静态库。
- `test_configuration_custom_role_ui.py::test_custom_role_is_loaded_into_calculation_role_selector`：旧 weighted 页面入口的加载预期。

本轮没有为使旧测试通过而放宽来源边界、删除测试或恢复废弃入口。失败定位及基线输出单独保留。

## 当前基础权重基准（v2）

旧工具调用已删除的页面入口，实际运行退出 1；现使用真实 BasicWeightPage、Controller、Service 和 DAO，
以实际控件编辑/点击完成保存、重置。不改生产业务、评分或界面。

- 冻结公开静态库、stats.json 和合成工坊模板；账号库由 DAO 新建，worker 以 SQLite 一致性备份建立独立写入副本，不读取真实账号。
- 30 个独立进程 cold 样本；另一个进程内做 30 轮热刷新、保存/重置及 30 次代次刷新。每个新进程绑定一个逻辑 CPU、Qt offscreen；不清空操作系统磁盘缓存。
- 保存等待读写 lane 物理结束、通知和清洁草稿；重置还等随后读取及投影。逐轮核对 DAO 精确值、另一角色不变和旧 owner 物理销毁。
- 就绪时间从排队页面动作开始，包含创建/读取/投影，不包含导入、QApplication 创建及夹具准备。10 ms 心跳从准备后的新 tick 起计，结束包含完成后下一 tick。
- P95/P99 用 nearest-rank；30 样本的 P99 接近最大值，热轮次不是独立进程样本。

| 当前场景 | 样本数 | 就绪 P95（ms） | 就绪 P99（ms） | 动作窗口最大额外心跳延迟（ms） |
| --- | ---: | ---: | ---: | ---: |
| 进程冷态首次进入 | 30 | 113.545 | 120.453 | 12.248 |
| 同 owner 无变化热刷新 | 30 | 13.399 | 14.154 | 2.700 |
| 热保存 | 30 | 40.229 | 41.842 | 3.311 |
| 热重置及后续读取 | 30 | 104.147 | 111.712 | 6.885 |

31 个 worker 全部退出 0，初始/最终模型分别一致，输入/源码哈希不变。突发 30 次刷新新增 Service 读取为 0，
但计数不是物理磁盘 I/O。最终页面、owner、宿主销毁，控件剩余 0；热重置静止点原生线程保持 13，
关闭后仍有进程级线程/句柄，未回到导入后基线。工作集及包含导入的生命周期峰值不是动作峰值或无泄漏证明。
动作窗口没有超过 200 ms 的额外心跳停顿，不保证真机绘制、滚动或所有功能流畅。

首轮第 28 次保存因浮点预期未按真实页面三位小数取整而失败，保留原记录；工具修正后仍对 DAO 精确断言。
真实保存故障在受控子进程验证，避免污染 pytest 日志 sinks 或导入器。10 项专项与 Ruff 硬门禁通过。
三层逻辑超时探针连同 venv launcher 的六个已观测物理进程全部收尾；不是任意逃逸进程隔离。
本批恢复当前可复现基线，不声称提升生产性能；历史源码缺少完整可重放输入，不拼成伪前后对比。

## 复测与回退

四个入口均要求显式隔离标记及数据库一致性副本；使用冻结的配置和图片输入，不直接指向活动账号。

```powershell
# 以下历史入口先核对其真实消费者与完整源码；当前权重入口另见下方。
.venv/Scripts/python.exe tools/quality/benchmark_ui_responsiveness.py --source-root SOURCE --fixture FIXTURE --output RESULT_DIR/role.json --iterations 30
.venv/Scripts/python.exe tools/quality/benchmark_dashboard_responsiveness.py --source-root SOURCE --fixture FIXTURE --output RESULT_DIR/home.json --iterations 30
.venv/Scripts/python.exe tools/quality/benchmark_catalog_responsiveness.py --source-root SOURCE --fixture FIXTURE --output RESULT_DIR/catalog.json --iterations 30
.venv/Scripts/python.exe tools/quality/benchmark_ui_responsiveness.py --source-root SOURCE --fixture FIXTURE --output RESULT_DIR/models.json --models-only
```

当前权重基准要求新建合成夹具，不给真实账号副本补标记。公开依赖及发行静态资料齐备后，
在检出根目录执行（目录/结果路径必须不存在）：

```powershell
@'
from pathlib import Path
import json, shutil
from tools.quality.benchmark_weight_responsiveness import isolate_source
root = Path.cwd().resolve()
isolate_source(root)
from src.storage.sqlite.user_data_dao import UserDataDao
fixture = root / 'build/weight-benchmark-fixture'
fixture.mkdir(parents=True)
(fixture / 'config').mkdir()
shutil.copy2(root / 'data/game_static.sqlite3', fixture / 'game_static.sqlite3')
shutil.copy2(root / 'config/stats.json', fixture / 'config/stats.json')
(fixture / 'config/workshop_weight_template.json').write_text(json.dumps({
    'schema_version': 1, 'payload_sha256': 'synthetic-weight-performance-v1',
    'characters': {'1046': {'character_id': 1046, 'properties': [
        {'property_id': 'CritBase', 'weight': 1.25, 'main_weight': 1.25}]}}}), encoding='utf-8')
(fixture / 'ui-benchmark-fixture.json').write_text(json.dumps({
    'isolated': True, 'synthetic': True}), encoding='utf-8')
for name in ('user.sqlite3', 'shared.sqlite3'):
    with UserDataDao(fixture / name, account_id='weight-benchmark'):
        pass
'@ | .venv/Scripts/python.exe -
.venv/Scripts/python.exe tools/quality/benchmark_weight_responsiveness.py --fixture build/weight-benchmark-fixture --output build/weight-benchmark-v2.json --iterations 30 --cold-iterations 30 --one-cpu
```

保留聚合、逐 worker JSON、stdout/stderr、退出状态和冻结哈希。执行 wrapper 记录与 --output 使用不同路径，避免同名覆盖。
换静态资料、模板、源码或终点后建立新基线，不覆盖已有输出。

本机证据包含逐项统计 JSON/CSV、冻结源码、逐文件 SHA-256、补丁、完整验证记录和默认只预览的回退脚本。
回退只恢复本轮代码，检查当前文件哈希，保护后续修改及其他开发者改动；不恢复账号数据库。
补丁应用和实际回退仅在隔离副本执行。本轮没有在真实工作区撤销优化、提交、推送或打包。

## 尚未执行的发行门禁

本节历史优化对比未运行 core/full；当前冻结回归与失败分类见
[架构验收剩余范围](architecture-renewal-day1.md#29-当前验证边界与剩余门禁)，不由局部专项替代。
安装包构建、真实同步/游戏或 GPU 测量未运行。
三主题、键盘操作、混合 DPI、持续监听下整应用交互及内存长期趋势仍待实机核对，
统一清单见[Windows 页面响应性验收](windows.md#页面响应性实机验收)。本机隔离结果不替代这些门禁，也不标记所有卡顿已解决。
