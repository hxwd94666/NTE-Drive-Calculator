# NTE Drive Calc 开发文档

根目录 [`AGENTS.md`](../AGENTS.md) 是跨功能硬契约，[项目声明](../RESPONSIBLE_USE.md) 说明用途与协作边界。
本目录只保存当前系统事实、外部边界、公式、未完成事项与实机验收；不保存个人环境、临时调查过程或完成流水账。

## 文档地图

| 分类 | 文档 |
| --- | --- |
| 总览 | [功能与操作语义地图](architecture-map.md)（按菜单、按钮与配置限制）· [系统架构](architecture.md)（分层、数据域、快照与副作用）· [外部集成](integrations.md)（nte-core、分析组件、插件、OCR、输入、静态构建与发行） |
| 已交付行为 | [功能原理](features.md) · [战报](features/battle-report.md) · [仓库管理与智能规则](features/warehouse-recommendation.md) · [倒带兼顾模式](features/rewind-dual-target.md) · [工具与游戏资料库](features/tools-and-catalog.md) · [界面语言补丁](features/localization.md) |
| 公式与规则 | [伤害计算规则](reference/damage/README.md) · [反事实审计](reference/counterfactual/README.md) · [治疗事件](reference/treatment-events.md) · [套装无条件常驻](reference/equipment-suit-permanent.md) · [养成体力规划](reference/progression-stamina-calculator.md) · [游戏资料库](reference/static-catalog.md) |
| 协议与格式 | [工作模式](reference/work-modes.md) · [游戏组件整包](reference/game-component-bundle.md) · [本方插件宿主](reference/native-plugin-host.md) · [战报包格式](reference/battle-report-package.md) · [队伍轴伤基准协议](reference/team-axis-graduation-protocol.md) · [日志事件](reference/logging-events.md) · [性能指标](reference/performance-metrics.md) |
| 开发指南 | [界面翻译协作（i18n）](../I18N.md) · [Fork 开发路线](reference/fork-development.md) · [版本更新 skill](../skills/nte-game-update/SKILL.md) · [测试维护](../tests/README.md) |
| 未完成工作 | [路线图](roadmap.md) · [队伍](roadmap/teams.md) · [战报](roadmap/battle-report.md) · [伤害分配](roadmap/damage-allocation.md) · [养成历史](roadmap/cultivation-history.md) · [免安装发行](roadmap/portable-distribution.md) · [同步生命周期](roadmap/sync-lifecycle.md) · [页面响应性](roadmap/ui-responsiveness.md) |
| 验收记录 | [Windows 人工验收](validation/windows.md) · [队伍轴伤毕业率](validation/team-axis-graduation.md) · [页面响应性](validation/ui-responsiveness.md) |

## 版本与发布

- 应用版本唯一来源为 [`src/app/version.py`](../src/app/version.py)；账号/静态 schema、采集协议和独立组件版本分别管理。
- 仓库不维护独立发行说明。发布范围由目标版本、提交历史、构建输入、验证结果和发布平台信息确认。
- [`tools/release/prepare_release.py`](../tools/release/prepare_release.py) 只做本机核对并输出维护者手工命令，不推送、不上传。

## 维护规则

- 四类目录：`features/` 已交付行为，`roadmap/` 未完成能力与门禁，`reference/` 稳定公式／字段／协议，`validation/` 实际验收证据；
  根目录只留跨领域总览。
- 一个事实只保留一个权威位置；更新时覆盖原章节并删除失效内容，不在文末追加补丁说明，不为单一链接保留短指针文件。
- 能力完成并验收后，把当前行为写入 features／architecture／integrations，再从路线图删除；一次性调查与合并记录不入库。
- 指向外部私有仓库的路径用代码样式标注，不复制其清单。
- 修改后检查相对链接与锚点、UTF-8、敏感信息边界和 `git diff --check`。
