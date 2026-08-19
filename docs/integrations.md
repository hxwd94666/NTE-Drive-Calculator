# 外部集成与扩展

本文说明外部能力进入项目时的边界。业务所有权与生命周期见 [系统架构](architecture.md)，真实环境步骤
见 [Windows 验收](validation/windows.md)。

## 1. nte-core

`src/integrations/nte_core.py` 负责 stdio 协议、进程、事件和错误适配，不负责评分、配装或 UI。当前产品
使用的能力包括：

- `capture.detect`；
- `capture.start(profile="inventory"|"combat")` 与 `capture.stop`；
- `inventory.get_latest`；
- 仓库状态写回与极速装配 RPC；
- `event.battle.summary`、`battle.get_summary`、`battle.reset`。

`inventory.get_latest` 读取最近捕获结果，不等于强制刷新。完整库存事件进入快照稳定化；残缺状态事件只
更新固定完整快照中的已知 UID。RPC 接受只代表已提交，最终状态由后续稳定快照或正式范围事件确认。

设置页的 nte-core 抓包诊断只调用 `capture.detect`，并以 Windows 只读探测补充 Npcap 驱动服务、已启用
适配器、Npcap 安装痕迹和常见 `wpcap.dll`/`Packet.dll` 位置的摘要。它不创建抓包会话，不读写网络配置，
不输出 MAC、IP、远端地址、完整本机路径或核心 capability 清单。`capture.detect` 返回零设备时，报告按
安装缺失、驱动服务、无活动网卡或“驱动/过滤器/运行时待排查”给出下一步；核心没有提供 libpcap 枚举错误
文本或逐网卡过滤理由时，报告必须明确该边界，不能伪造原因。

逐击分页、Buff/Debuff 区间、完整养成/武器快照、四人/双队、敌人实例、正式场景 ID 和历史逐击导出尚未
成为产品 contract。调试样本不代替公开 CLI capability。

## 2. 视觉、OCR 与游戏输入

`src/integrations/vision` 独占窗口坐标、截图、格位检测、鼠标动作和扫描后状态同步；`src/scanner` 与解析
Service 负责 OCR、归一化和装备字段。Integration 返回截图、索引、解析结果或诊断，不写业务快照，
不决定评分和保留规则。

鼠标与虚拟手柄全量扫描最终都提交为统一 `vision` 快照。游戏界面自动装配和实验云模式使用同一公开
输入 contract；页面不创建鼠标后端或虚拟手柄。云模式当前固定关闭，只保留映射与诊断代码。

新增输入后端必须定义：窗口身份、物理像素坐标、前置页面、动作序列、可见后置状态、取消检查、输入
释放、超时和回滚。缺少后置确认时停止当前任务，不盲目补点。

## 3. 二进制与插件

根目录 `nte-core.exe`、`dwmapi.dll`、`nte-mod-loader.exe` 和插件副本是本机文件。`third_party` 只保存明确晋升的发行组件；
晋升前记录上游 commit、版本、许可和 SHA-256，并完成协议、打包和真实 Windows 验证。

mods 插件运行时脚本与 SDK 缓存位于应用配置目录的可写 `mods-plugin` 工作区，不进入 Git 或发行模板。
游戏更新后的 presence、IPC 管道、
动态 SDK 和 Hook 排查见 [装配插件版本适配](reference/mods-plugin-version-adaptation.md)。

Mods 插件默认通过游戏目录中的代理 `dwmapi.dll` 加载。只有代理未生效时，用户才可显式选择备用
`nte-mod-loader.exe`；两种方式互斥。Loader 由 Integration 使用 UAC、唯一 stop event 和 owner PID 管理，
应用退出或用户停止时协作退出，不以强制杀进程作为正常路径。Integration 从已选择的 `HTGame.exe` 安装根
定位官方启动器，并通过 Loader 的 launcher override 明确传入，不依赖本地化卸载名称或固定盘符。Loader
启动前处理游戏目录代理 DLL，停止超时后禁止部署代理方式；Loader 进程存活只表示正在监控，装备 IPC
管道出现后才确认游戏插件已加载。运行时不固定 Loader 的 SHA-256；环境变量指定文件、随包文件或应用
目录同名文件只要存在即可启动，便于用户用可信来源的新版或修正版直接替换。

## 4. 静态数据与资源

官方数据只通过 `tools/game_data` 生成候选静态库。导入器保留来源文件、行键与摘要，验证 schema、外键、
业务约束和 manifest 后才替换发行数据库。静态库和 `data/manifest.json` 作为一个原子变更审查。

游戏 UI 图片由资源构建工具生成到 `assets/game_ui`，`manifest.json` 明确记录 ID 映射、文件哈希和
`unresolved_assets`。缺失资源保持显式 unresolved，不用相似图片或本机路径占位。

账号 schema 变化新增迁移并覆盖新建、升级、失败回滚和重试。DAO 独占 SQL；快照清理走公开 DAO 并
保护所有引用。

## 5. 新页面和公共组件

在 `src/features/<feature>/` 实现 Page/View 与 Controller，由 `src/ui/navigation.py` 注册导航，通过组合根
注入窄 dependencies。快捷入口使用导航 key，不使用堆叠页数字。跨功能复用先建立 Service、不可变 contract
或组合根公共组件，不经 MainWindow 字段和其他 Controller 转发。

## 6. 新算法、同步与装配方式

- 新算法复用官方 ID、固定快照、候选对象、配装槽位和保存方案 payload；输出包含真实 UID、目标位置、
  逐件评分、来源快照与算法/profile 版本。
- 多角色竞争由统一分配器处理，不用逐角色循环修改背包。
- 新同步方式提供等待、收集、完整性、稳定监听、单事务提交和明确错误状态；只提交完整快照。
- 新装配器只消费冻结的已保存方案，不在执行阶段重新优化；执行前校验来源、角色、槽位、UID 和位置，
  执行后产生可确认的新状态。
- 批量副作用使用持久任务、角色/槽位项和事件记录进度。
