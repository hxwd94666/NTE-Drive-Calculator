# 配装保存与自动装配

## 模块定位

保存计算产生的配装方案，管理计算保留锁，并通过 nte-core 或游戏界面自动化执行装配。

## 当前能力

- 保存活动配装及来源快照；
- 展示评分、收益、替换差异和锁定状态；
- 单件替换优化；
- 计算保留锁的锁定/解除；
- nte-core 极速装配；
- 游戏界面视觉/虚拟手柄自动装配；
- 批量任务、角色项、事件和失败重试；
- 新稳定快照最终确认。

## 数据边界

配装方案和任务属于账号库。真实装备使用正式 UID；视觉临时 UID 不进入极速装配。锁属于账号
计算保留契约，不调用或伪装为游戏装备锁 RPC。

## 关键约束

- 锁定方案不能删除、覆盖或单件替换；
- 其他角色不能借用锁定方案的真实 UID；
- 装配期间阻止账号切换；
- RPC 接受和 UI 操作完成都不等于最终成功；
- 最终状态必须由新稳定快照确认。

## 验证

主要覆盖 drive-assembly、equipment-apply、bulk apply、loadout lock 和 replacement 测试。

## 主要实现

`src/features/drive_assembly/`、`src/services/bulk_equipment_apply_service.py`、
`src/services/equipment_apply_service.py`、`src/storage/sqlite/loadout_plan_*`。
