# NTE Drive Calculator 2.0.3：摘要型战报交付说明

> 文档状态：2.0.3 候选交付说明
> 目标版本：NTE Drive Calculator `2.0.3`
> 当前账号数据库版本：`13`
> 最近更新日期：2026-08-07
> 后续开发：[2.1.0 战报收益画像方案](../2.1.0/battle-report.md)

## 1. 交付目标

2.0.3 使用 nte-core 当前正式开放的聚合摘要能力完成一条可独立使用的战报闭环：

1. 开始 combat 抓包并展示实时摘要和悬浮窗；
2. 结束时以 `battle.get_summary` 返回值生成最终权威战报；
3. 展示整场、上半、下半、角色、技能和伤害构成；
4. 将完整原始 summary JSON 保存到当前账号 SQLite；
5. 提供自动/手动保留、历史查看、取消保存、删除和页面恢复；
6. 在账号切换、背包同步和应用退出期间保持明确的生命周期边界。

本版本是战报基础设施版本，不把摘要数据包装成尚未实现的逐击、覆盖率或队伍收益能力。

## 2. 已实现能力

### 2.1 采集与展示

- 一级“战报”菜单位于“鉴定”之后；
- `event.battle.summary` 只驱动实时页面和悬浮窗，不直接持久化；
- 停止抓包后重新调用 `battle.get_summary`，以最终返回值覆盖实时投影；
- 支持“跟随当前 / 上半 / 下半”切换角色贡献和技能明细；
- 汇总区保持整场口径，不随明细范围切换；
- 角色卡展示直伤、特殊伤害、具体环合与“其他”，分类过多时卡内滚动；
- 悬浮窗提供队伍摘要、角色占比、头像、DPS 和实时趋势，并支持鼠标穿透。

### 2.2 场景边界

- 深渊记录 `abyss.floor`、成功状态和上下半；
- 当前协议无法判断基础深渊或当期深渊，不进行推断；
- 非深渊没有稳定场景 ID，统一保存为 `non_abyss` 并显示“未知场景”；
- 不要求用户录制前手工选择场景。

### 2.3 账号历史

- 迁移 `014_user_data_v13.sql` 创建 `battle_record`、`battle_record_retention` 和
  `battle_report_page_state`；
- `battle_record` 保存不可变原始摘要、SHA-256、最少列表索引和 capability；
- `capture_operation_id` 提供幂等边界，同一操作不能写入不同 payload；
- 每账号最多 100 条记录，手动记录最多 50 条；
- 结束时先保存自动记录，用户点击保存时将同一记录提升为手动，不复制原始 JSON；
- 历史列表支持查看、保存/取消保存和确认删除；
- 页面恢复上次记录和 `current/first/second` 明细范围；
- 所有写入冻结账号 ID、账号数据库绝对路径和 generation，旧上下文结果不写入新账号。

### 2.4 nte-core 会话

- 战报开始前停止正在运行的背包同步；
- 战报结束或失败后，仅在账号和 generation 未变化时恢复此前允许自动运行的同步；
- 活动采集阻止账号切换；
- 应用退出先停止战报会话；
- 当前不建立并发共享抓包进程，战报和背包同步按生命周期串行使用 nte-core。

## 3. 数据所有权

| 数据 | 所有者 | 说明 |
| --- | --- | --- |
| 实时摘要 | Controller 内存 | 可丢弃，不写日志和数据库 |
| 最终 summary JSON | 当前账号 `user_data.sqlite3` | 战报权威事实 |
| 保留状态和恢复指针 | 当前账号 `user_data.sqlite3` | 可变用户状态 |
| 分类名称和头像 | 发行静态库/资源 | 运行时只读 |
| 本地逐击样本 | `local_game_data/battle_reports/` | Git ignore，仅研究使用 |

页面和 Controller 不拼 SQL；DAO 独占事务、保留规则和查询；Integration 独占 nte-core 格式校验；
Service 负责最终摘要持久化、历史读取和账号上下文防护。

## 4. 当前运行证据

维护者已通过 IDE 直接运行 `main.py` 使用本功能。2026-08-07 对当前账号数据库进行只读检查：

- `PRAGMA integrity_check` 返回 `ok`；
- schema migration 为 `13`；
- 三张战报表存在，`foreign_key_check` 违规数为 `0`；
- 当前保存 5 条手动深渊战报，覆盖第 8、9、10、11、12 层；
- 最近记录包含上下半、8 个角色和 49 个技能；
- 页面恢复指针指向最近记录，明细范围为上半。

上述内容只证明本地数据库结构和已运行数据正常，不替代未在本次分支整理中执行的自动化、
非深渊真机或发布验证。文档不得保存原始 JSON、角色 UID、账号显示名或用户绝对路径。

## 5. 2.0.3 明确不包含

- 逐击伤害轴、绝对时间和任意时间窗口；
- Buff/Debuff 生效区间及武器、空幕 Buff 实测覆盖率；
- 目标实例、怪物 ID、稳定场景 ID 或深渊周期；
- 角色等级、觉醒、武器、装备等级和精炼观测快照；
- 版本化 `RoleBenefitProfile`；
- DOT、具体环合占比驱动的综合边际；
- 辅助、减抗、易伤等完整队伍间接贡献；
- 最佳四人和深渊八人搜索；
- 面向用户的逐击 JSON 导入。

这些能力继续由 [2.1.0 开发索引](../2.1.0/INDEX.md)、
[nte-core 合作需求](../2.1.0/nte-core-collaboration.md) 和
[队伍收益与配装优化计划](../2.1.0/team-benefit-allocation.md) 管理。

## 6. 主要实现

- `src/features/battle_report/`
- `src/domain/battle_report.py`
- `src/integrations/nte_core_battle.py`
- `src/services/battle_capture_service.py`
- `src/services/battle_report_persistence_service.py`
- `src/services/battle_report_history_service.py`
- `src/storage/sqlite/battle_report_dao.py`
- `src/storage/sqlite/schema/014_user_data_v13.sql`

## 7. 推送与发布边界

- 当前 `2.0.3` 分支先推维护者 fork；
- 未经再次确认不推 `upstream/test`；
- upstream owner 同意后可以把本分支作为 test 候选；
- 合入 main 或发布前，按项目验证矩阵补齐维护者要求的检查；
- 本机 `nte-core.exe`、DLL、账号库、日志和原始战报不进入 Git。
