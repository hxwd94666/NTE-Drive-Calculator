# nte-core 集成边界

## 当前正式集成

NTE Drive Calculator 通过 `src/integrations/nte_core.py` 调用随应用发行的 nte-core CLI。Integration
负责 stdio 协议、进程、事件和外部错误适配，不负责评分、收益、配装或 UI。

当前已实际调用的主要能力：

- `capture.detect`；
- `capture.start(profile="inventory"|"combat")`；
- `capture.stop`；
- `inventory.get_latest`；
- 仓库状态写回和极速装配相关 RPC；
- `event.battle.summary`；
- `battle.get_summary`；
- `battle.reset`。

## 当前未开放能力

- `battle.get_axis`、`battle.get_record` 和逐击分页；
- Buff/Debuff 状态区间；
- 角色等级、突破、觉醒、武器等级和精炼的稳定快照；
- 当前四人队和深渊双队快照；
- 目标实例、怪物 ID 和正式战斗场景 ID；
- 通过当前 CLI 导出 `nte_capture` 历史 JSON。

仓库里曾核对过一份合作方桌面端/调试工具产生的逐击 JSON，但该文件不是当前 CLI contract，
也不是普通用户可稳定生成的产品输入。个人样本只保存在被忽略的 `local_game_data/`。

## 会话与数据边界

- 同一时刻不能让背包同步和战报各自抢占抓包会话；
- 当前战报开始前停止背包同步，结束后按账号/generation 条件恢复；
- Integration 不写 SQLite，不计算伤害占比或覆盖率；
- RPC 接受不等于游戏状态最终确认；
- 完整 payload、UID 和鉴权材料不得写日志。

## 二进制管理

| 路径 | 含义 | Git 策略 |
| --- | --- | --- |
| 根目录 `nte-core.exe` | 维护者本机编译/替换副本 | 忽略，不提交 |
| 根目录 `dwmapi.dll` | 维护者本机插件副本 | 忽略，不提交 |
| `third_party/nte-core/bin/nte-core.exe` | 明确晋升的发行内置组件 | 追踪，需来源与版本 |
| `third_party/mods-plugin/bin/dwmapi.dll` | 明确晋升的发行内置组件 | 追踪，需来源与版本 |

不得把本机编译结果直接覆盖并提交为发行组件。晋升 `third_party` 前必须确认上游 commit、版本、
许可、协议兼容性、打包断言和真实 Windows 验收。

## 合作开发

正式需求、难度和交付状态见
[2.1.0 nte-core 合作开发需求](../development/2.1.0/nte-core-collaboration.md)。
