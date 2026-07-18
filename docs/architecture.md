# 2.0 架构说明

## 数据边界

2.0 把数据分成两类 SQLite 文件：

- `data/game_static.sqlite3` 是随版本发布的只读静态数据库，由开发者从已准备好的游戏官方文件生成。程序运行时不修改它。
- `accounts/<account_id>/user_data.sqlite3` 是每个账号独立的用户数据库，保存同步设置、不可变背包快照和装配方案。

静态表和用户表都保留游戏使用的 `character_id`、`item_id`、`suit_id`、`geometry`、`property_id`。新服务不先转换成旧项目的显示名称；界面名称只在展示边界解析。

## 背包同步

`src/services/inventory_sync_service.py` 在应用生命周期内复用一个 nte-core 进程。事件回调只替换单槽内存队列中的最新完整事件，SQLite 写入由工作线程完成，因此持续事件不会堵塞协议读取线程。

`src/services/inventory_snapshot_stabilizer.py` 通过完整内容指纹和安静窗口判断稳定：

- 不假设任何固定背包数量；
- 数量不变但内容变化时重新计时；
- 连续重复事件不会延长等待；
- 后续新增或移除装备会形成新的不可变快照；
- 同一核心会话内忽略倒序 generation/sequence。

计算开始时必须固定 `snapshot_id`。后台可继续同步新快照，但当前计算通过 `UserDataDao.list_inventory_items(snapshot_id)` 读取原输入，直到任务结束都不会漂移。

## 配装计算

旧版完整算法的公共输入协议仍位于 `src/optimizer/contracts.py`，方便现有页面继续运行。旧版属性名称兼容入口集中在 `src/domain/stat_catalog.py`。

2.0 的首个新入口是 `src/services/sqlite_loadout_optimizer.py`：

1. 从静态数据库读取角色的官方装备蓝图；
2. 固定用户数据库中的一个稳定快照；
3. 根据核心 `suit_id` 和套装 `required_shape_ids` 保证必要形状激活套装；
4. 用官方 `property_id` 权重给候选装备评分；
5. 保存含原生 UID、目标行列和 `source_snapshot_id` 的方案。

当前入口是“官方固定蓝图 + 套装约束 + 属性权重”的可复现基线。后续自定义布局、全角色竞争分配、战斗模型和属性边际收益，应继续消费同一官方 ID 输入，而不是复制一套名称映射。

## 一键装配

`src/services/equipment_apply_service.py` 只接受已保存的 SQLite 方案。调用前会检查：

- 背包同步处于稳定监听状态；
- nte-core 握手包含 `equipment` 能力；
- 当前快照与同步状态一致；
- 角色、核心和驱动 UID 合法且仍存在；
- 驱动坐标位于 1–5，且方案不依赖协议不支持的旋转。

派发 `equipment.equip_one_key` 后，服务会等待比装配前更新的稳定快照，再核对角色 UID、角色 ID、核心和每个驱动的锚点位置。这个同步方法应从界面工作线程调用，不能阻塞 Qt 主线程。

## 界面与生命周期

主页是状态工作台，不是执行页。导航定义集中在 `src/ui/navigation.py`，主页页面位于 `src/features/home/page.py`。应用窗口负责启动、停止和切换账号时重建同步服务；页面只订阅状态，不直接管理核心进程。

轻量 UI 图片位于 `assets/game_ui`，运行时通过 `src/ui/game_asset_catalog.py` 按官方 ID 查找。生成脚本会缩小尺寸并按内容去重，避免把大尺寸源图直接装进安装包。

## 发布资源

`build_exe.py` 会把以下运行资源放入 PyInstaller 的 `_internal`：

- `src/storage/sqlite/schema` 全目录；
- `data/game_static.sqlite3`；
- `assets`；
- 构建机提供的 `nte-core.exe`。

`build_installer.py` 在生成 Inno Setup 脚本前会校验主程序、核心组件、数据库结构和静态数据库。正式构建应使用合作项目的固定 Release 产物及配套许可证文件。
