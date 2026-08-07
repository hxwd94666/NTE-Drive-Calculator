# 视觉扫描与 OCR

## 模块定位

在 nte-core 不可用或需要图片输入时，从游戏画面批量解析装备并提交完整视觉库存快照。

## 当前能力

- 批量截图与 OCR 解析；
- 装备分类、UID 生成和重复过滤；
- `ScanFileLifecycle` 管理成功、失败、去重、重命名和清理；
- `StreamingScanService` 编排流式扫描；
- 完整结果通过 `import_vision_inventory` 提交账号快照；
- 与鉴定共享公开解析契约和应用级热键管理器。

## 数据边界

每次任务冻结账号、generation、截图/模板目录和用户库。Integration 只解析，不直接写数据库；
取消或异常不能提交半成品快照。

## 生命周期

扫描使用 owner=`scanning` 的热键会话，只能停止自己的 generation。运行中的会话冻结启动时配置，
设置变更从下一次会话生效。

## 当前限制

- 视觉 UID 不是正式游戏 UID；
- 视觉来源不能用于 nte-core 极速装配或可靠状态写回；
- 扫描 Controller 不提供鉴定、装备展示或配装接口。

## 验证

主要覆盖 scanning、streaming、OCR golden sample、file lifecycle 和 hotkey boundary 测试。

## 主要实现

`src/features/scanning/`、`src/scanner/`、`src/integrations/vision/`、
`src/services/streaming_scan_service.py`。
