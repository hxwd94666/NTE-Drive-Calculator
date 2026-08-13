# 外部集成与扩展

## 1. nte-core

`src/integrations/nte_core.py` 负责 stdio 协议、进程、事件和错误适配，不负责评分、配装或 UI。
当前正式使用：

- `capture.detect`；
- `capture.start(profile="inventory"|"combat")`、`capture.stop`；
- `inventory.get_latest`；
- 仓库状态写回和极速装配 RPC；
- `event.battle.summary`、`battle.get_summary`、`battle.reset`。

尚未作为产品 contract 使用：逐击分页、Buff/Debuff 区间、完整养成/武器快照、四人/双队、敌人实例与
正式场景 ID、历史逐击 JSON 导出。仓库中的本地样本不代表公开 CLI 能力。

同一时刻背包同步与战报不能争抢捕获会话。Integration 不写 SQLite、不计算收益；RPC 接受不等于游戏
状态确认；完整 RPC、UID 和鉴权材料不写日志。

## 2. 二进制边界

根目录 `nte-core.exe`、`dwmapi.dll` 和插件副本是本机文件，保持 Git ignore。`third_party` 只保存明确
晋升的发行组件。晋升前记录上游 commit、版本和许可，并验证协议、打包断言、哈希与真实 Windows 行为。
游戏更新后的 presence、管道、偏移与 Viewport Hook 排查见
[装配插件版本适配](reference/mods-plugin-version-adaptation.md)。

## 3. 新页面和公共组件

在 `src/features/<feature>/` 实现页面/Controller，由 `src/ui/navigation.py` 注册导航，通过组合根注入
窄 dependencies。快捷入口使用导航 key，不使用堆叠页数字。公共行为先建立 Service 或不可变 contract，
不通过 MainWindow 字段或 Controller 转发。

## 4. 静态数据和 DAO

官方数据只通过 `tools/game_data` 生成候选静态库，检查来源、schema、外键、摘要和 manifest。静态查询
加入 `StaticGameDataDao`，账号查询加入 `UserDataDao`。账号 schema 变化新增迁移并覆盖新建、升级、
回滚和打包测试。快照清理走公开 DAO 并保护所有引用。

## 5. 新优化算法

复用官方 ID、固定快照、候选对象和保存方案结构。角色边际或战斗收益实现为独立评分器；多角色竞争由
统一分配器处理，不能让逐角色循环修改背包。算法输出仍需包含真实 UID、目标位置、逐件评分、来源
快照和算法/profile 版本。

## 6. 新同步或装配方式

同步实现必须提供等待、收集、保存、稳定监听和错误状态，并只提交完整快照。装配器只消费保存方案，
不重新优化；调用前验证来源、角色、UID、位置和重复，调用后产生可确认的新状态。批量装配使用任务、
角色项和事件持久化进度。
