# NTE Drive Calc 开发文档

文档已按“一个入口、少量主题、当前事实优先”整理。长期强制约束从根目录
[`AGENTS.md`](../AGENTS.md) 开始阅读。

| 文档 | 用途 |
| --- | --- |
| [系统架构](architecture.md) | 分层、数据域、快照、方案和生命周期 |
| [功能原理](features.md) | 同步、计算、配装、仓库、鉴定、倒带、装配和战报 |
| [外部集成与扩展](integrations.md) | nte-core、二进制、DAO、页面与算法扩展 |
| [当前路线图](roadmap.md) | 尚未完成的结构性工作和上游依赖 |
| [伤害计算规则](reference/damage-calculation.md) | 项目伤害公式金标准 |
| [日志事件规范](reference/logging-events.md) | 结构化日志字段与脱敏要求 |
| [装配插件版本适配](reference/mods-plugin-version-adaptation.md) | 游戏更新后 presence/管道、偏移、Hook 的定位与修复 |
| [Windows 验证](validation/windows.md) | 真实游戏、驱动和人工检查 |
| [云异环模式开发](validation/cloud-nte-mode.md) | 云游戏自动装配输入问题、当前进度和后续验证方案 |
| [鼠标全量视觉扫描](validation/mouse-visual-scan.md) | 鼠标扫描、滚轮反馈、多分辨率、并行解析和账号 SQL 提交设计 |

## 文档维护

- 当前能力直接覆盖对应章节，不追加修改流水账；
- 只记录数据结构、所有权、功能原理、生命周期、算法和外部边界；
- UI 文案、颜色、间距等细节由代码和测试表达；
- 过期版本文档和重复索引直接删除，未实现事项集中在 `roadmap.md`；
- 所有相对链接在提交前自动检查。
