# NTE Drive Calc 开发文档

根目录 [`AGENTS.md`](../AGENTS.md) 是强制契约。本目录只保存当前系统事实、外部边界、公式、路线图与实机验收；
不保存个人环境、临时调查过程或已完成实施计划。

## 从任务进入文档

| 任务 | 权威文档 |
| --- | --- |
| 分层、数据域、快照、方案和副作用 | [系统架构](architecture.md) |
| 已交付的页面、计算、同步、配装、战报和设置 | [功能原理](features.md) |
| nte-core、插件、OCR、输入、静态库与外部算法 | [外部集成](integrations.md) |
| 尚未稳定或尚待上游事实的能力 | [当前路线图](roadmap.md) |
| 通用伤害、DOT、环合、倾陷与怪物公式 | [伤害计算规则](reference/damage-calculation.md) |
| 战报派生治疗事件 | [治疗事件](reference/treatment-events.md) |
| 战报反事实的静态目录与人工规则 | [反事实审计](reference/counterfactual/README.md) |
| 游戏资料库、术语、角色、弧盘、敌人与覆盖审计 | [静态资料库](reference/static-catalog.md) |
| 确定副本产出的最低活力规划 | [养成体力规划](reference/progression-stamina-calculator.md) |
| 战报导入导出格式 | [战报包格式](reference/battle-report-package.md) |
| 结构化日志与脱敏字段 | [日志事件规范](reference/logging-events.md) |
| Windows、插件、扫描与更新实机验收 | [Windows 验收](validation/windows.md) |

## 目录规则

- `architecture.md` 只写结构与数据流；`features.md` 只写当前产品行为；`roadmap.md` 只写未完成能力。
- `reference/` 放公式、字段、格式与只读资料域；`reference/counterfactual/` 放战报反事实的目录和人工审计。
- `integrations.md` 是所有第三方组件、插件与静态构建的唯一集成说明；不再拆分版本适配专题。
- `validation/` 只保存真实环境验收步骤与证据要求。
- 一个事实只有一个权威位置。修改后检查相对链接、UTF-8 与 `git diff --check`。
