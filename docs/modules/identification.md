# 鉴定

## 模块定位

从截图、剪贴板、手工输入或仓库单件入口解析装备，并使用公共评分与展示契约给出角色适配结果。

## 当前能力

- 单张截图、剪贴板和手工输入；
- 连续截图会话及配置化完成热键；
- OCR/手工输入统一投影为装备对象；
- 调用公共评分、角色适配和 `EquipmentPresentation`；
- 每次任务冻结账号、generation、截图/配置目录和用户库。

## 数据边界

Controller 只拥有鉴定 worker、连续截图会话和临时输入；解析 Integration 不直接写账号库。鉴定
不能访问仓库页面、扫描 Controller 或计算结果 widget 的私有状态。

## 生命周期

连续截图期间由 `GlobalHotkeyManager` 持有 owner=`identification` 的会话，并阻止账号切换。页面
销毁、账号切换和应用退出通过公开停止入口释放。

## 当前限制

- F12 不是鉴定完成操作；
- OCR 结果仍需遵守来源能力和质量提示；
- 鉴定不会修改账号基础权重或自动保存配装。

## 验证

主要覆盖 identification、warehouse-identification、global-hotkeys 和装备展示边界测试。

## 主要实现

`src/features/identification/`、`src/services/warehouse_identification_service.py`、
`src/integrations/global_hotkeys.py`。
