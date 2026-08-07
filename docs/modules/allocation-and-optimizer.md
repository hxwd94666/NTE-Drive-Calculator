# 计算与优化器

## 模块定位

在固定背包快照内，为一组角色生成满足过滤、套装、优先级、锁和真实装备 UID 唯一约束的配装
预览。

## 当前能力

- 角色优先和平级组分配；
- 全局最优分配；
- 驱动副词条黑名单与顺序/一致候选池；
- 卡带套装、主词条、副词条硬过滤；
- 评分等级、暴击限制、账号基础权重和额外形状；
- `AllocationLockSnapshot` 在候选构造前剔除全部锁定 UID；
- 返回不可变 `WeightedAllocationPreview`；
- 保存前复核账号、快照、profile 和锁。

## 输入与输出边界

输入必须冻结账号、generation、`snapshot_id`、静态数据集、profile version 和配装锁。Optimizer 是
纯计算模块，不读取 SQLite、Qt 页面、当前账号或 Integration。

## 当前限制

- 当前评分不是队伍伤害模拟；
- 角色页动态直伤权重不写回基础权重；
- 最佳四人/八人和综合收益画像驱动优化仍属于后续版本计划；
- 保存只能使用原 Preview，不能重新读取移动中的当前状态补齐旧结果。

## 验证

主要覆盖 allocation、weighted-allocation、lock、replacement 和 dependency-boundaries 测试。

## 主要实现

`src/optimizer/`、`src/services/allocation_*`、`src/features/weighted_allocation/`。
