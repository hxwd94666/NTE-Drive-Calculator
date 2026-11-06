# 队伍战报匹配专项验证

## 范围与结论

2026-10-22，在 Windows、Python 3.11 与实际 PySide6 窗口环境下执行用户授权的专项测试及静态检查。
仅使用临时合成账号／战报和随附只读分析组件；未操作个人账号、连接游戏、采集、装配、编译、打包、部署、提交或发布。
已实现行为见[功能说明](../features.md#按阵容匹配战报)，剩余门禁见[路线图](../roadmap/team-report-matching.md)。

队伍匹配及相关专项合计 **91 项通过**。额外扫描生命周期回归为 **32 通过、2 失败**；
隔离原提交复现同两项失败，故未判作本轮新增，也未删测、跳过或改扫描生产逻辑掩盖失败。
硬静态门禁通过；未运行不带 pattern 的 core 或 full，不能据本轮结果宣称全项目通过。

## 执行命令与结果

各行均通过质量入口执行，下列命令的 `PATTERN` 替换为表中模式：

```powershell
python tools/quality/run_tests.py core --pattern PATTERN -j 1
```

| PATTERN | 字面汇总 | 退出码 |
| --- | --- | --- |
| `test_team_report_matching*.py` | `36 passed in 70.99s (0:01:10)` | 0 |
| `test_axis_allocation_selection.py` | `6 passed in 24.28s` | 0 |
| `test_axis_team_preflight.py` | `8 passed in 43.44s` | 0 |
| `test_team_axis_enhancement.py` | `10 passed in 10.03s` | 0 |
| `test_team_management.py` | `14 passed in 18.86s` | 0 |
| `test_damage_postprocess_ui.py` | `17 passed in 19.01s` | 0 |
| `test_scan_stage_retry.py` | `2 failed, 32 passed in 82.02s (0:01:22)` | 1 |

```powershell
python -m ruff check src tests --select E9,F63,F7,F821,F401
git diff --check
```

Ruff 返回 `All checks passed!`，退出码 0；差异空白检查退出码 0，仅有部分文件 CRLF 将被 Git 规范化为 LF 的提示。
本轮 Python 文件另经 AST 解析、800 行限制及新增文件中文首行检查。

### 基线与失败归类

源码基线为 `25e796d41401b059f9ce6cdc22f5e061875f9d06`，使用 `git archive` 创建隔离副本，不切换或回退当前工作区。
将新增匹配规则测试放入旧源码后，通过同一质量入口运行，预期退出码 2，字面错误为：
`ModuleNotFoundError: No module named 'src.domain.teams.models'; 'src.domain.teams' is not a package`。
这只证明旧源码尚无新增接口，不是旧版本业务回归通过或完整行为复现。

扫描失败项在原始基线使用同一 `test_scan_stage_retry.py` 模式重跑，退出码 1，
结果为 `2 failed, 32 passed in 108.51s (0:01:48)`：

- `test_gui_continue_exact_stage_restores_draft_and_commits_once`：根目录文件仍为旧内容。
- `test_gui_retry_is_file_only_and_duplicate_click_does_not_resave`：文件发布状态仍为 `pending`。

本轮只为该测试的轻量 owner 补充新生命周期字段，未修改其断言或扫描文件发布实现；两项仍须在独立扫描任务排查。

预检测试另修正了早先已删除图纸工厂的陈旧 patch 目标，改为当前正式 `DeferredDamageBlueprints`，随后 8 项通过。
初次合并运行曾输出 Windows 原生异常诊断，虽退出码为 0 仍未作为干净通过证据；
后续隔离运行及上述最终分组未再出现该诊断。
真实组件窗口联调的测试等待循环原用 `QTest.qWait` 持续占用 GIL，定位到后台路径读取被饿死后，
改为事件处理加释放 GIL 的短暂等待；未放宽时限、跳过组件或改生产逻辑来获得通过。

## 已证明的行为边界

- 领域／服务覆盖四人集合、零伤害队友、范围拆分、差异／缺证／冲突、无库存、分页、缓存摘要和资源身份失效。
- UI 覆盖显式 B 优先于旧 A、队伍页明确范围优先、手工微秒范围、同记录两半场、非零伪装、取消物理退场与末次请求。
- 3 项随附组件测试实际读取合成冻结战报，包含组合根→队伍窗口、异步增强窗口确认与摘要冻结；查询前后账号库内容不变。
- 三主题、640×600 与 700×720 可用区域验证关键控件未被遮挡；键盘取消受测。
- 导航仅准备目录和配置，未开始分配；确认摘要变化会在正式预检前拦截。

上述不等于完整键盘体验、真实个人历史性能、混合 DPI 多屏、真实游戏或发行验收。
条件式候选双队优化组件用例不属于本轮匹配联调通过声明；本轮未指定候选程序，也未构建或晋升组件。
本机逐命令输入、字面输出、退出状态、源码哈希、补丁和隔离回退证据保留在忽略的构建目录，
不将原始临时路径、测试输出或账号资料纳入本文与发行包。
