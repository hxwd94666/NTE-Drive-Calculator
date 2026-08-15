# NTE Drive Calc 开发文档

根目录 [`AGENTS.md`](../AGENTS.md) 是仓库强制契约；本目录只保存系统原理、现有功能、外部边界、仍未完成
事项和真实环境验收。阅读时先确认任务类型，再进入对应文档。

## 文档地图

| 文档 | 适用场景 |
| --- | --- |
| [系统架构](architecture.md) | 理解分层、组合根、数据域、快照、配装槽位和副作用确认 |
| [功能原理](features.md) | 修改同步、扫描、计算、配装、仓库、倒带、装配、战报或设置 |
| [外部集成](integrations.md) | 接入 nte-core、插件、OCR、鼠标/手柄、静态数据或新算法 |
| [当前路线图](roadmap.md) | 判断某项能力是否仍在开发、受何种上游条件阻塞 |
| [伤害计算规则](reference/damage-calculation.md) | 修改直伤、DOT、环合、倾陷、怪物属性或技能档位 |
| [日志事件规范](reference/logging-events.md) | 增加结构化事件、运行日志或脱敏字段 |
| [装配插件版本适配](reference/mods-plugin-version-adaptation.md) | 排查 presence、IPC 管道、动态 SDK 和插件升级 |
| [Windows 验收](validation/windows.md) | 验证真实游戏、驱动、插件、扫描、装配和更新 |

## 维护规则

- 当前能力只写入 `features.md`，未完成事项只写入 `roadmap.md`。
- 架构事实只写入 `architecture.md`；具体公式和字段放入 `reference/`。
- 真实设备步骤统一放入 `validation/windows.md`，不再为单个功能维护重复验收清单。
- 功能落地后，将稳定契约整合进架构/功能文档并删除实施方案、调查笔记和阶段流水账。
- 一个事实只保留一个权威位置，其他文件通过相对链接引用。
- 所有文档使用 UTF-8，提交前检查相对链接与 `git diff --check`。
