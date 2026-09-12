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
| 工作模式、逐功能检测与本机生命周期 | [工作模式契约](reference/work-modes.md) |
| 游戏组件整套哈希与自动管理输入 | [组件包格式](reference/game-component-bundle.md) |
| 2.2.2 未交付能力及实机验收边界 | [当前路线图](roadmap.md) |
| 面向用户的发行说明 | [版本说明](../RELEASE_NOTES.md) |
| 通用伤害、DOT、环合、倾陷与怪物公式 | [伤害计算规则](reference/damage-calculation.md) |
| 战报派生治疗事件 | [治疗事件](reference/treatment-events.md) |
| 战报反事实的静态目录与人工规则 | [反事实审计](reference/counterfactual/README.md) |
| 游戏资料库、术语、角色、弧盘、敌人与覆盖审计 | [静态资料库](reference/static-catalog.md) |
| 确定副本产出的最低活力规划 | [养成体力规划](reference/progression-stamina-calculator.md) |
| 战报导入导出格式 | [战报包格式](reference/battle-report-package.md) |
| 结构化日志与脱敏字段 | [日志事件规范](reference/logging-events.md) |
| Windows、插件、扫描与更新实机验收 | [Windows 验收](validation/windows.md) |

## 版本与发布文档

- 应用版本唯一来源为 [`src/app/version.py::__version__`](../src/app/version.py)，不在 UI 或打包脚本另写版本常量。
- 未完成改版的范围、依赖、实施顺序与完成条件只写 `roadmap.md`；目标版本未确定时不猜版本号，不提前宣称发布。
- 实现并验收后，当前行为归入功能/架构/集成文档，删除对应路线图计划；`RELEASE_NOTES.md` 在正式发行时按
  实际交付范围重写，标题与目标版本一致，不把 SDK 候选、已编译或已部署等同于实机验收通过。
- [`tools/release/prepare_release.py`](../tools/release/prepare_release.py) 是本地发布准备入口，读取统一版本并核对
  标签、组件输入，按参数执行检查/构建；它不负责推送和上传。该脚本可能运行全量测试和打包，不能因修改文档
  或版本号就自动执行。发布验证与 Windows 验收要求见 [Windows 验收](validation/windows.md) 及根契约。
- 应用版本、账号/静态 schema、采集协议和独立组件版本分别管理；改应用版本不能代替数据库迁移或组件更新。

## 目录规则

- `architecture.md` 只写结构与数据流；`features.md` 只写当前产品行为；`roadmap.md` 只写未完成能力。
- `reference/` 放公式、字段、格式与只读资料域；`reference/counterfactual/` 放战报反事实的目录和人工审计。
- `integrations.md` 是所有第三方组件、插件与静态构建的唯一集成说明；不再拆分版本适配专题。
- `validation/` 只保存真实环境验收步骤与证据要求。
- 一个事实只有一个权威位置。修改后检查相对链接、UTF-8 与 `git diff --check`。
