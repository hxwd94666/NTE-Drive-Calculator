# 系统架构

本文描述 NTE Drive Calc 的稳定系统结构。编码代理的强制约束以根目录
[`AGENTS.md`](../AGENTS.md) 为准。

## 1. 总体数据流

```text
nte-core 事件 ─┐
               ├→ 稳定化 → 账号不可变库存快照 → 计算上下文 → 配装预览
视觉扫描结果 ──┘                                      ↓
                                              活动配装与冻结评分
                                                        ↓
                                  nte-core 极速装配 / 游戏界面自动装配
                                                        ↓
                                              后续稳定快照确认
```

“当前背包”只在操作开始前解析一次。计算、保存、替换、倒带和装配都保留明确的 `snapshot_id`；
历史配装按自身 `source_snapshot_id` 解析，不跟随最新指针。

## 2. 分层

| 层 | 职责 | 不应持有 |
| --- | --- | --- |
| UI | 输入和结果投影 | SQL、协议、业务算法、其他页面控件 |
| Controller | worker、取消、token、忙碌状态 | DAO 查询细节、其他 Controller |
| Service | 冻结输入、业务编排、事务 | Qt 页面、MainWindow |
| Domain/Optimizer | 纯规则和求解 | SQLite、Qt、日志、全局账号 |
| DAO | SQL、schema、迁移、事务 | UI、协议 |
| Integration | nte-core、OCR、进程、文件格式 | UI、业务评分 |

公共 `EquipmentPresentation` 和 `GlobalHotkeyManager` 只在 `src.ui.app` 组合根创建并注入。

## 3. 数据域

| 数据域 | 路径 | 主要内容 |
| --- | --- | --- |
| 发行静态 | `data/game_static.sqlite3` | 官方角色、装备、形状、套装、属性、伤害数据、推荐值 |
| 本机共享 | `app_shared.sqlite3` | 明确跨账号的用户差异 |
| 应用全局 | `config/global_ui_preferences.json` | 跨账号一致的主题偏好 |
| 账号私有 | `accounts/<account_id>/user_data.sqlite3` | 快照、养成、权重、偏好、方案、锁、任务、战报 |

`AppContext` 提供应用路径和当前 `AccountContext`。账号切换递增 generation；长任务必须冻结并复核
账号路径、generation、快照和配置版本。

## 4. 快照来源能力

nte-core 和视觉扫描共享库存 schema，但能力不同。nte-core 提供正式 UID、角色实例和可写状态；视觉
快照只提供分析输入。来源能力必须显式检查，不能因为表结构相同就允许状态写回或极速装配。

## 5. 方案与锁

活动方案保留来源快照、assignment、逐件评分、卡带满级主词条和来源类型。计算保留锁是账号内方案
契约，不是游戏装备锁。锁定 UID 在候选构造前排除，并在 DAO 保存事务再次检查。

## 6. 副作用确认

RPC 接受、手柄动作结束和 UI 更新都不是最终成功。仓库状态写回与装配必须等待比操作前更新的稳定
快照，核对目标 UID、角色和状态。超时保留待确认，失败项按任务记录重试。

## 7. 导航与生命周期

一级功能包括工作台、计算、配装、角色、仓库、鉴定、战报、工具和设置。MainWindow 只负责组合与
生命周期；业务状态分别由页面、Controller、Service 和 DAO 持有。账号切换、页面销毁和退出都必须
停止或失效后台任务。
