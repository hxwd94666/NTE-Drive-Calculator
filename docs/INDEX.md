# NTE Drive Calc 技术文档索引

本索引面向项目维护者、合作方和合作 AI。普通用户的安装与使用说明仍以根目录
[`README.md`](../README.md) 为准；全项目长期约束以根目录 [`AGENTS.md`](../AGENTS.md) 为准。

## 建议阅读顺序

1. [`AGENTS.md`](../AGENTS.md)：长期架构、数据所有权和不可破坏契约；
2. 本索引：选择当前任务对应的文档；
3. [`modules/INDEX.md`](modules/INDEX.md)：模块现有能力和数据边界；
4. 对应版本的 [`development/`](development/) 文档：开发进度、临时决策和后续计划；
5. [`integrations/`](integrations/)：外部组件协议和双方职责。

## 目录职责

| 目录 | 内容 | 是否描述未来计划 |
| --- | --- | --- |
| `architecture/` | 系统结构、依赖方向和公共数据模型 | 只保留长期设计 |
| `modules/` | 各业务模块当前已实现能力、输入输出、数据所有权和限制 | 否 |
| `integrations/` | nte-core、扩展组件和外部协议边界 | 只记录已开放能力与明确需求 |
| `development/<version>/` | 版本方案、阶段进度、临时决策和验收状态 | 是 |
| `reference/` | 公式、日志字段等跨模块参考资料 | 否 |
| `validation/` | 自动化和真实 Windows 验收清单 | 否 |

模块文档只描述当前事实。未实现内容必须写在 `development/`，或在模块文档的“当前限制”中
链接过去，不能把设计稿描述成现有能力。

## 当前模块

| 模块 | 当前能力摘要 | 文档 |
| --- | --- | --- |
| 应用上下文与账号 | 路径组合根、账号 generation、切换生命周期 | [查看](modules/accounts-and-context.md) |
| 数据存储 | 发行静态、本机共享、账号 SQLite 三域 | [查看](modules/data-storage.md) |
| 背包同步 | nte-core 稳定快照、角色实例和当前指针 | [查看](modules/inventory-sync.md) |
| 计算与优化器 | 固定快照、角色优先/全局分配、锁和 UID 唯一 | [查看](modules/allocation-and-optimizer.md) |
| 角色与权重 | 养成配置、面板、直伤边际、基础权重和图纸 | [查看](modules/official-role-and-weights.md) |
| 仓库 | 固定快照浏览、比较和状态写回 | [查看](modules/warehouse.md) |
| 鉴定 | 截图、剪贴板、手工输入和公开装备展示 | [查看](modules/identification.md) |
| 视觉扫描与 OCR | 批量解析、文件生命周期和视觉库存快照 | [查看](modules/scanning-and-ocr.md) |
| 配装与自动装配 | 保存方案、替换、计算保留锁和两条装配链 | [查看](modules/loadout-and-equipment-apply.md) |
| 战报 | nte-core 聚合摘要、悬浮窗、账号历史和伤害构成 | [查看](modules/battle-report.md) |
| 设置、更新和日志 | 账号设置、环境诊断、Mirror 更新和结构化日志 | [查看](modules/settings-update-and-observability.md) |

## 外部集成

- [nte-core 当前能力与二进制边界](integrations/nte-core.md)
- [扩展开发指南](integrations/extension-guide.md)

## 当前开发版本

- [2.1.0 开发索引](development/2.1.0/INDEX.md)
- [战报统计与收益画像](development/2.1.0/battle-report.md)
- [nte-core 合作需求与进度](development/2.1.0/nte-core-collaboration.md)
- [队伍收益与配装优化长期计划](development/2.1.0/team-benefit-allocation.md)

## 数据与隐私

- 个人逐击 JSON、PCAP、日志、截图、账号库和本地运行数据库不得提交；
- 本地逐击样本存放在被忽略的 `local_game_data/battle_reports/`；
- 根目录本机 `nte-core.exe`、`dwmapi.dll` 和本机插件目录不得提交；
- `third_party/` 中的二进制是明确晋升后的发行组件，必须记录来源、版本和许可后才允许更新；
- 文档若需要协议示例，只提交专门制作的最小脱敏样本，不能提交个人真实录制文件。

## 维护规则

- 模块能力发生变化时更新对应 `modules/` 文档；
- 开发阶段、产品决策或验收状态变化时更新对应 `development/<version>/` 文档；
- 外部接口或 capability 变化时同时更新 `integrations/` 和合作需求文档；
- 移动文件后必须检查所有 Markdown 链接；
- 不在文档中记录账号显示名、角色 UID、用户绝对路径、完整 RPC 或真实战斗 payload。
