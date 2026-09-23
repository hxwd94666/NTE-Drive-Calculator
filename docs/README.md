# NTE Drive Calc 开发文档

根目录 [`AGENTS.md`](../AGENTS.md) 是跨功能硬契约。本目录只保存当前系统事实、外部边界、公式、未完成事项与
实机验收；不保存个人环境、临时调查过程、完成流水账或重复的发布文案。

## 从任务进入文档

| 任务 | 权威文档 |
| --- | --- |
| 核心功能、数据安全、仓库与组件边界 | [仓库开发契约](../AGENTS.md) |
| 分层、数据域、快照、方案和副作用 | [系统架构](architecture.md) |
| 已交付的页面、计算、同步、配装和设置 | [功能原理](features.md) |
| 战报当前行为 | [战报功能](features/battle-report.md) |
| 资料库与养成计算器当前行为 | [工具与游戏资料库](features/tools-and-catalog.md) |
| nte-core、分析组件、插件、OCR、输入和静态构建 | [外部集成](integrations.md) |
| 尚未稳定或仍待外部事实的能力 | [当前路线图](roadmap.md) |
| 战报证据、拟合与分析核心待办 | [战报路线图](roadmap/battle-report.md) |
| 工作模式、逐功能检测与本机生命周期 | [工作模式契约](reference/work-modes.md) |
| 游戏组件整套哈希与自动管理输入 | [组件包格式](reference/game-component-bundle.md) |
| 通用伤害、DOT、环合、倾陷与怪物公式 | [伤害计算规则索引](reference/damage-calculation.md) |
| 战报派生治疗事件 | [治疗事件](reference/treatment-events.md) |
| 战报反事实目录与人工审计 | [反事实审计](reference/counterfactual/README.md) |
| 游戏资料库、角色、弧盘、敌人与覆盖审计 | [静态资料库](reference/static-catalog.md) |
| 确定副本产出的最低活力规划 | [养成体力规划](reference/progression-stamina-calculator.md) |
| 战报导入导出格式 | [战报包格式](reference/battle-report-package.md) |
| 结构化日志与脱敏字段 | [日志事件规范](reference/logging-events.md) |
| Windows、插件、扫描、更新与发行 | [Windows 验收](validation/windows.md) |

## 版本、路线图与发布

- 应用版本唯一来源为 [`src/app/version.py::__version__`](../src/app/version.py)，UI、文档和打包脚本不另写版本常量。
- 应用版本、账号/静态 schema、采集协议和独立组件版本分别管理；修改应用版本不代替数据库迁移或组件更新。
- 未完成范围、依赖、实施顺序和完成条件只写 `roadmap.md`；完成并验收后，将当前行为覆盖式整合进功能、架构或集成
  文档，并从路线图删除对应计划。
- 仓库不维护独立发行说明文件。实际发布范围按目标版本、提交历史、构建输入、验证结果和发布平台当次信息确认。
- [`tools/release/prepare_release.py`](../tools/release/prepare_release.py) 是本地发布准备入口，核对统一版本、静态 dataset、
  组件与安装包，按参数运行检查/构建并输出维护者手工命令；它不推送、不上传，也不自动执行发布。

## 目录与维护规则

- `architecture.md` 只写结构与数据流；`features.md` 和 `features/` 只写当前产品行为；`roadmap.md` 和
  `roadmap/` 只写未完成能力。首页文档保留稳定入口，细节在对应专题维护。
- `reference/` 放公式、字段、格式与只读资料域；`reference/damage-calculation.md` 是公式权威索引，
  `reference/damage/` 承载各公式专题；`reference/counterfactual/` 放反事实目录，`reviews/` 按角色保存人工审计。
- `integrations.md` 是第三方组件、插件、输入与静态构建的唯一集成说明；`validation/` 只保存真实环境验收。
- 一个事实只保留一个权威位置。更新时覆盖相关章节并删除失效内容，不在文末追加补丁式说明。
- 修改后检查相对链接、UTF-8、敏感信息边界和 `git diff --check`。
