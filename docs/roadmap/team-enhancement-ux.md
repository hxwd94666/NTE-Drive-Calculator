# 队伍与战报增强：界面交付及剩余验收

## 1. 范围与当前状态

本方案的 P1-A、P1-B、P2-A、P2-B、P3 已在源码实现；用户可见行为唯一说明见[队伍配装简报](../features.md#队伍配装简报)与[计算结果后处理](../features.md#计算结果后处理)。本页保留实现责任、证据和仍待人工／发布验收的范围，不另维护一份按钮说明。

目标是让用户看懂队伍配装、固定轴毕业率和已保存增强，并顺畅完成选队、选战报、确认、计算、保存。不新增第二套计算流程，不把配置确认当作开始计算，不以布局压缩隐藏来源不足、未知或过期。

设计沿用 PySide6、现有三主题、头像、TextActionButton 和 Disclosure。审查使用本机 `ui-ux-pro-max` 的 keyboard-nav、error-feedback、primary-action、visual-hierarchy、spacing-scale、scroll-behavior、state-clarity、dynamic-type；桌面控件不套用网页框架或移动触控尺寸。

## 2. 实现责任及源码状态

| 批次 | 源码完成项 | 责任与边界 |
| --- | --- | --- |
| P1-A | 队伍卡片摘要与操作共列；紧凑选队、匹配战报、就近状态、固定底栏 | `features/teams/` 与 `features/allocation/team_axis/`；互斥、最多两队、正式匹配及范围重核验不变 |
| P1-B | 队伍配装多槽、空槽、失效与缺来源状态；只读单槽默认；计算增强行配置摘要 | `features/teams/loadouts/` 与 `team_axis_controls.py`；导航由组合根显式注入，不以 UI 读 SQL 或跨 Feature 私有调用实现 |
| P2-A | 毕业率结果优先；结构化目标摘要和只读预设；目标／建议单层滚动；紧凑历史行；诊断默认折叠、内容初始尺寸与右侧固定底栏 | `features/teams/graduation/`；只消费结构化比例，不修改分母、搜索算法、来源或历史；编辑不求解、不保存 |
| P2-B | 按提交时间读取增强历史；优化前后、来源、覆盖、关联变化四区；来源技术字段及详细诊断默认折叠；说明区按内容收缩 | `enhancement_history_dialog.py`；保留分页、负提升、未知与联合结果原义，不挑历史最高收益 |
| P3 | 明确动作回车、可访问折叠状态、窄窗底栏和重复展示收敛 | `dialog_support.py`；仅复用展示和焦点行为，不接管 worker、取消、事务或窗口物理退场 |

目录归属遵循[架构](../architecture.md#源码职责目录与保留入口)。本轮没有改 DAO、领域公式或分析组件；原始战报、冻结评分、来源快照与历史结果不改写。来源单槽默认的领域与 CAS 规则已在原专项中证明，本轮补齐可见状态而非在标签中另造规则。

## 3. 完成核对与必要证据

| 要求 | 可核对的实现／专项 |
| --- | --- |
| 保留四头像、入口及上下对齐；选队单一状态区、管理在确认左侧 | `test_team_selection_ux.py`、`test_team_report_matching_ui.py`、队伍页现有专项 |
| 单槽默认、多槽选择、空槽、失效绑定、同名槽、并发与取消不写入 | `test_team_source_defaults.py`、`test_team_overview_ui.py`、`test_team_ux_remaining.py` |
| 配装来源未保存／已确认分开；取消导航不保存，过期账号回调不导航 | `test_team_ux_remaining.py` 与原队伍生命周期专项 |
| 配置摘要不提升就绪状态；关闭保留、账号重置清空，原开关与单独计算保持原义 | `test_team_ux_remaining.py`、`test_team_axis_enhancement.py`、`test_team_single_calculation.py` |
| 百分比读结构化字段，未知／0／超过100不混淆，历史不重算，草稿不隐式保存 | `test_team_axis_graduation_history_ui.py`、`test_team_ux_remaining.py`、`test_team_dialog_compact.py`、`test_team_graduation_target_view.py` |
| 历史来源、覆盖和关联可读，负提升保留，取消及分页归原 owner | `test_team_enhancement_history.py`、`test_team_dialog_compact.py`、`test_team_graduation_target_view.py` |
| 三主题、窄宽窗、长语言补丁、底栏与输入不遮挡，键盘显式确认 | `test_team_selection_ux.py`、`test_team_ux_remaining.py`、`test_team_dialog_compact.py`、`test_team_graduation_target_view.py`，实际 Qt 控件离屏预览（包含展开诊断、异步回填保留用户尺寸和分页不跳窗） |
| 中文／示例补丁／实际调用方同步；缺项回退和参数一致 | 上述 UX 专项的目录及命名参数核对 |

离屏预览只使用公共素材和合成数据，示例百分比不作为真实战斗或分析引擎验证。验证记录按本轮本机构建证据保存，不将截图、账号或临时库加入源码交付。

## 4. 保留待验收

- 真实 Windows 混合 DPI、双屏切换及整应用实际观感，按[Windows 验收](../validation/windows.md)和[队伍简报](team-overview.md)独立处理；离屏图不替代实机。
- 完整 core/full、应用构建、安装／升级与真实游戏验收均未纳入本轮授权，不声明发布完成。
- 原生建议 UI 的既有可选用例需要明确的合成原生建议响应文件；本机未提供时保留跳过。目标编辑与只读布局由当前合成 UI 夹具专项覆盖，不以旧静态哈希失效的文件夹具伪造兼容。

无新增产品语义待确认。后续人工／发布验收若发现问题，应修本责任模块，并保持现有来源、冻结、历史与取消边界。
