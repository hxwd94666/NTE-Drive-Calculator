# 战报

## 模块定位

通过当前 nte-core CLI 采集实时聚合伤害摘要，展示角色与技能伤害构成，并在账号库保存最终摘要
历史。

## 当前能力

- 一级“战报”页面和实时悬浮窗；
- 开始/停止 combat 抓包；
- `event.battle.summary` 实时展示；
- 停止后以 `battle.get_summary` 作为最终权威摘要；
- 整场、角色、技能、上半/下半和质量聚合展示；
- 直伤、特殊伤害、具体环合和其他聚合构成；
- SQLite v13 原始 JSON、SHA-256、operation 幂等和账号历史；
- 每账号最多 100 条、手动最多 50 条的 FIFO；
- 保存/取消保存、查看、删除和上次页面恢复；
- 账号 ID、数据库路径和 generation 防护。

## 数据边界

最终原始摘要写当前账号库；实时事件只用于可丢弃展示。页面、Controller 不拼 SQL，也不记录
完整 payload 到日志。战报开始前暂停背包同步，结束后仅在上下文未变化时恢复。

## 当前 CLI 能力边界

当前只有聚合摘要，没有逐击轴、绝对时间、Buff/Debuff 区间、目标实例、怪物 ID、角色/武器
养成快照或稳定场景 ID。非深渊统一显示“未知场景”，深渊只显示 floor，不判断基础/当期。

个人 `nte_capture` JSON 是本地研究样本，当前 CLI 不能生成该文件，应用也不提供面向用户的 JSON
导入入口。逐击和实测覆盖率必须等待上游正式 capability。

## 可信用途

`summary_only` 可用于聚合角色伤害占比、技能占比、渠道构成和明确标记的聚合收益画像；不能宣称
来源清晰的逐击轴、实测 Buff 覆盖率或完整队伍间接贡献。

## 验证状态

DAO、持久化、历史、账号、core 和 full 自动化已通过；真实游戏历史管理和非深渊场景由维护者
继续人工验收。开发进度见 [2.1.0 战报计划](../development/2.1.0/battle-report.md)。

## 主要实现

`src/features/battle_report/`、`src/services/battle_capture_service.py`、
`src/services/battle_report_*`、`src/storage/sqlite/battle_report_dao.py`、
`src/integrations/nte_core_battle.py`。
