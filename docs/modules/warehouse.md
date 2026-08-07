# 仓库

## 模块定位

从固定稳定快照浏览、比较和管理批量装备库存，并通过公开服务编排游戏状态写回。

## 当前能力

- 按快照展示驱动、卡带、已装备、锁定和角色实例信息；
- 使用公共装备展示组件生成卡片和评分；
- 生成锁定/弃置等状态计划；
- 通过 nte-core Integration 提交状态写回；
- 等待后续递增稳定快照确认最终状态；
- 从仓库单件进入公开鉴定服务。

## 数据边界

仓库只读取固定 `snapshot_id`，状态计划固定目标 UID。页面不写 SQL，不持有鉴定或扫描
Controller。状态写回由 Service/Integration 负责。

## 当前限制

- RPC 接受不代表游戏状态已确认；
- 超时只显示“待快照确认”；
- 视觉扫描库存没有可靠锁定/弃置状态，不能伪装成可写 nte-core 库存。

## 验证

主要覆盖 warehouse inventory、state management、writer 和 identification boundary 测试。

## 主要实现

`src/features/inventory/`、`src/services/warehouse_*`、`src/ui/equipment_presentation.py`。
