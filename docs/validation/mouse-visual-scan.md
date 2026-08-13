# 鼠标全量视觉扫描设计与验证

本文定义鼠标驱动的全量视觉扫描实现方案。扫描继续使用本项目现有截图解析器、装备模型和账号 SQLite
快照，不引入参考项目的 OCR、JSON 库存或配装流程。本文只覆盖扫描捕获、并行解析和库存提交。

参考实现固定为 `NTE-Cassette-Tuner` commit
`eeedfc0a17d2785753fbd8176138a8b085240583` 中的 `game_capture.py` 与 `scanner.py`。参考代码采用
MIT License；复制实质代码时必须保留对应版权与许可声明。本项目主要复用其网格遍历、选中格校准和
基于局部特征反馈的滚轮控制思路，不复制其单文件状态、固定 720p 输出格式和 JSON 持久化。

## 1. 范围和硬契约

### 1.1 输入和数据所有权

- 发行静态：游戏 UI 布局 profile、视觉模板、静态装备目录和 dataset 版本；运行时只读。
- 本机共享：本功能不新增共享业务数据。
- 当前账号：扫描截图目录、扫描偏好、运行记录和 `user_data.sqlite3` 中的视觉库存快照。
- 游戏窗口：只作为捕获和鼠标输入目标，不拥有业务状态。

一次扫描启动时冻结以下输入：

- `account_id`、`AppContext.generation`、账号数据库绝对路径和截图目录；
- 静态 dataset/manifest 版本、扫描布局 profile 版本和解析 profile；
- 捕获驱动 `mouse`、预计库存数量、解析并发模式和本次操作 token；
- 游戏窗口 HWND、客户区物理像素尺寸和 16:9 游戏内容区域。

扫描开始后不得追随当前账号、当前前台窗口、最新配置或其他扫描产生的截图目录。回调、SQLite 提交和
结果展示前重新核对 token、generation 和账号数据库路径；不匹配的结果静默丢弃。

### 1.2 输出和互斥关系

鼠标扫描与虚拟手柄扫描属于同一种 `vision` 库存能力，只允许有一个活动视觉库存：

```text
mouse capture  ─┐
                ├→ 本项目解析器 → 完整 vision snapshot → 账号 SQLite 当前指针
gamepad capture ─┘
```

- 两种捕获结果不得合并，也不得各自维护可同时参与计算的库存。
- 后完成且通过完整性检查的全量扫描，以单事务创建新的不可变快照并切换账号当前库存指针。
- 旧快照可以作为受引用的不可变历史保留，但不再是活动视觉库存；“只能保存一个”指当前计算只消费一个
  完整快照，而不是破坏仍被历史方案引用的快照。
- 取消、窗口变化、截图缺失、解析失败或数量不一致不切换当前指针。
- 数据只写当前账号的 `user_data.sqlite3`，不写 JSON 库存，不写发行静态库和本机共享库。
- 快照统一使用 `source="vision"`，以 `capture_driver="mouse"|"gamepad"` 记录捕获方式。实现阶段需要以
  追加迁移扩展来源枚举或元数据；不得继续把鼠标扫描标成 `gamepad`。
- 视觉快照使用本地临时 UID，不能进入要求 nte-core 正式 UID 的流程。

## 2. 分层和状态所有权

```text
Scanning Page
    ↓ 采集预计数量、捕获方式和解析偏好
ScanningController
    ↓ 冻结 dependencies、token、worker 和 UI 投影
FullVisualScanService
    ├→ MouseInventoryCapture Integration
    │      └→ WindowCapture + MouseInput + ScrollTracker
    ├→ ScanParseCoordinator
    │      └→ 现有 BatchProcessor / OCR / 装备解析
    └→ VisionInventorySnapshot Service
           └→ UserDataDao 单事务提交
```

- Page 只持有输入控件和可丢弃进度，不创建扫描器、不打开 DAO。
- Controller 持有本次 worker、取消 token、冻结 dependencies 和进度投影。
- Service 编排捕获、解析、完整性检查和提交，不发送鼠标输入、不拼 SQL。
- Integration 独占 HWND、客户区坐标、截图、鼠标点击、滚轮和画面稳定检测。
- 现有解析器继续独占 OCR、词条归一化、形状和套装识别；鼠标扫描器不解析装备字段。
- DAO 只接收完整规范化快照，并原子切换当前指针。

建议新增：

```text
src/integrations/game_input/mouse_backend.py
src/integrations/vision/inventory_layout.py
src/integrations/vision/mouse_inventory_capture.py
src/services/full_visual_scan_service.py
```

普通鼠标后端应从 `src.features.drive_assembly` 的功能私有位置迁到 Integration 公共接口，装配和扫描通过
公开 contract 复用，避免扫描 feature 反向依赖装配 feature。

## 3. 捕获契约

鼠标捕获驱动实现与现有全量扫描流水线兼容的窄接口：

```python
class InventoryCaptureDriver(Protocol):
    def start_scan(
        self,
        total_items: int,
        *,
        on_capture: Callable[[str, int, int], None],
        cancel_check: Callable[[], bool],
        commit_on_complete: bool = False,
    ) -> CaptureReport: ...
```

`CaptureReport` 至少包含：

- 捕获方式、布局 profile、冻结窗口尺寸；
- 预计数量、成功截图数量、最后完成索引；
- 每页截图数、滚轮命令次数、滚动停滞次数和页面耗时；
- 取消、窗口变化、焦点丢失、面板未稳定和写盘失败等终止原因；
- 临时目录和按索引排序的截图清单。

捕获期间图片只写账号截图目录下本次任务的临时子目录。只有捕获和解析均完整结束后才执行文件提交和
SQLite 快照提交。

## 4. 多分辨率坐标模型

参考项目使用 1280×720、7 列×3 行，基准参数为：

```text
first_cell = (110, 180)
spacing = (112, 135)
detail_panel = (870, 130, 370, 460)
```

实现中只保留其归一化比例，不把 720p 像素写入执行逻辑：

```text
first_x_ratio = 110 / 1280
first_y_ratio = 180 / 720
spacing_x_ratio = 112 / 1280
spacing_y_ratio = 135 / 720
```

鼠标坐标计算统一基于 `ClientToScreen` 得到的客户区物理像素和 `game_content_rect()` 返回的 16:9 内容
区域。支持的首轮验收分辨率为：

| 游戏内容尺寸 | 比例 | 用途 |
| --- | ---: | --- |
| 1920×1080 | 1.5× 720p | 默认开发基线 |
| 2560×1440 | 2× 720p | 2K 基线 |
| 3840×2160 | 3× 720p | 4K 基线 |

2560×1600 等高屏客户区仍按顶部对齐的 2560×1440 游戏内容区域映射。DPI 缩放不得再次乘入客户区物理
像素；启动诊断必须同时记录客户区尺寸、内容区域和第一行七个候选点击点。

每次扫描先生成一张不含账号信息的布局诊断图，标出：

- 三行七列候选点击中心；
- 详情面板稳定检测区域；
- 滚动特征模板和搜索区域；
- 游戏内容区域边界。

布局诊断通过后才能开始实际点击。窗口 HWND、客户区尺寸或内容区域在运行中变化时立即停止本次扫描。

## 5. 单件捕获和画面稳定

每件装备执行：

```text
计算安全点击中心并加入随机偏差
→ 移动鼠标并随机等待
→ 按下/释放左键
→ 等待选中框或详情面板发生变化
→ 连续稳定帧确认详情完成渲染
→ 截取完整客户区
→ PNG 低压缩写入临时目录
→ on_capture(path, index, total)
```

截图继续保存完整客户区，文件名为 `raw_drive_NNNN.png`，保持现有 `BatchProcessor`、离线补录和状态图标
检测输入格式。不得改成参考项目只保存右侧面板裁剪图。

稳定条件不得只依赖固定 sleep。默认要求：

- 选中框或详情面板相对上一件出现可测变化；
- 详情 ROI 连续两帧差异低于 profile 阈值；
- 首次检测最早在点击后 45 ms 开始；
- 超过 1.2 s 仍未稳定时重试一次点击；第二次仍失败则结束扫描，不保存半成品。

## 6. 随机输入扰动

鼠标输入必须避免固定周期和固定像素中心。随机化只在安全区域内进行，不得影响格子命中、截图顺序和
可复现诊断。

默认 profile 以 1080p 物理像素为基准，按 `content_height / 1080` 缩放：

| 动作 | 1080p 默认范围 |
| --- | --- |
| 点击 X/Y 偏差 | 截断正态分布，分别限制在 ±4 px |
| 移动耗时 | 45–110 ms |
| 按键保持 | 28–65 ms |
| 点击后首次检测等待 | 45–110 ms |
| 相邻装备附加间隔 | 35–95 ms |
| 滚轮命令后等待 | 130–240 ms；仍以画面反馈为最终条件 |

2K 和 4K 只缩放像素偏差，不按分辨率放大时间。最终偏差还要裁剪到格子安全半径的 15% 以内。每次任务
创建独立随机上下文；测试注入固定 seed，正式运行不使用常量 seed。日志只记录范围、profile 版本和聚合
耗时，不记录完整逐次随机序列。

## 7. 滚轮反馈控制

参考项目的重点不是固定滚轮次数，而是每发出一次滚轮命令就重新定位上一页最后选中装备；命令次数由
画面位移决定。首版沿用这一闭环：

1. 页面完成后，从最后一个已扫描格截取局部灰度特征；
2. 鼠标移动到网格安全区域；
3. 在缩放后的纵向搜索带中执行模板匹配；
4. 特征中心仍低于粗滚动阈值时发送粗滚动，否则发送细滚动；
5. 每次命令后等待随机区间，再重新截图测量；
6. 特征中心到达下一页目标首行区域后停止滚动，并将实测中心作为下一页 Y 基准。

参考参数换算为归一化阈值：

```text
target_top_y = 220 / 720
coarse_boundary_y = 350 / 720
template_size = 50 / 720 × 50 / 720（按内容高度缩放）
template_confidence >= 0.40
4 次近零位移：执行一次焦点唤醒
10 次近零位移：判定触底或滚动失效
```

滚轮输入量首轮以参考值 `-280`（粗）和 `-120`（细）作为实验 profile，而不是按 1.5/2/3 倍随分辨率
线性扩大。滚轮输入是逻辑 wheel delta，实际像素位移由游戏、Windows 设置和输入后端共同决定。1080p、
2K、4K 实机验证分别记录：

- 每页粗滚动和细滚动命令次数；
- 每次命令的特征中心位移；
- 到达目标行的总耗时；
- 模板丢失、焦点唤醒和触底次数。

验证结果形成三个发行布局 profile。运行时仍保留特征反馈和最大命令数，profile 中的历史次数只用于选择
初始粗细滚动策略，不替代画面确认。滚动控制应作为独立 Integration 组件，本文仅定义扫描侧调用。

## 8. 边截图边解析

继续使用现有 `ScanParseCoordinator` 和 `BatchProcessor`。捕获是单生产者，解析默认是单消费者；OCR
后端不在扫描线程初始化或调用。

```text
Mouse capture ─→ bounded queue ─→ BatchProcessor ─→ ordered parsed results
      │                                      │
      └──────── capture report ──────────────┘
```

首版默认开启边扫边解析，但必须满足以下实现要求：

- 队列有界，建议容量 21 张，即一页 7×3；禁止当前无界队列持续积压 4K 图片路径和解析状态。
- 捕获写盘成功后才入队；解析结果按扫描索引保存，完成顺序不得改变最终 UID 顺序。
- 解析比捕获慢时允许最多积压一页，队列满后捕获线程在安全点背压，不在鼠标按下或滚动中阻塞。
- AMD 兼容或低负载模式继续支持“先完整截图、后单线程解析”。
- 捕获取消后停止生产，消费者只完成当前项并退出；已解析项目不得提交为库存。
- OCR 初始化失败必须发生在第一件游戏输入之前，避免先接管鼠标再发现解析器不可用。

是否具有收益以实测决定，不凭扫描速度推断。验收采集：

```text
capture_item_ms
parse_item_ms
queue_depth / queue_high_watermark
producer_block_ms
scan_total_ms
parse_tail_ms
end_to_end_ms
```

同一库存分别运行串行和并行模式。并行模式的目标是减少扫描结束后的解析尾延迟；若 4K 下导致游戏帧率、
OCR 稳定性或截图完整率下降，则该设备 profile 自动使用低负载模式。

## 9. 完整性和账号 SQLite 提交

提交前必须同时满足：

- `captured_count == expected_count`；
- 成功解析数加人工补录数等于预计数量；
- 索引从 1 到 N 连续且无重复；
- 每张截图属于本次冻结临时目录；
- Controller token、账号、generation 和数据库路径仍匹配；
- 静态 dataset 和解析 profile 未变化；
- 快照通过现有装备类型、品质、形状、套装和词条校验。

提交事务：

```text
构建完整 vision snapshot
→ INSERT inventory_snapshot/items/stats
→ 将此前 current 指针清零
→ 将新 snapshot 标记 current
→ COMMIT
```

任何步骤失败都回滚，新快照及其物品不可见，旧当前快照保持不变。捕获方式只作为快照元数据，鼠标和
手柄不得各自切换不同的“当前视觉库存”。

## 10. 取消和生命周期

- F12、页面停止、账号切换和应用退出都设置同一个取消 token。
- Integration 在移动前、点击前、截图前、滚轮前和每次反馈等待后检查 token。
- 停止时先确保鼠标左键释放，再结束生产者和解析消费者。
- worker 在初始化前和释放后都可能为 `None`；Controller 使用局部引用检查 `isRunning()`。
- 取消任务保留受控诊断文件，删除业务临时截图，不切换 SQLite 当前快照。
- 旧 generation 的进度和完成信号不更新页面、不启动计算。

## 11. 测试切片

实现前先增加公共行为测试：

```text
tests/test_mouse_inventory_capture.py
tests/test_mouse_scan_scroll_tracker.py
tests/test_full_visual_scan_service.py
tests/test_visual_scan_generation_boundary.py
tests/test_vision_inventory_snapshot.py
tests/test_streaming_scan_pipeline.py
```

至少覆盖：

- 1080p、2K、4K 和 2560×1600 顶部对齐坐标；
- 随机点击始终落在安全区域，固定 seed 可复现；
- 最后一行 1 至 6 件、恰好整行和 2000 件上限；
- 粗滚动、细滚动、模板丢失、4 次唤醒和 10 次停滞；
- 边扫边解析有界队列、顺序保持、背压和解析尾延迟；
- F12、焦点变化、窗口尺寸变化、截图失败和 OCR 失败均不提交；
- 鼠标扫描完成后替换手柄视觉当前快照，反向替换同理，且不合并物品；
- 账号切换后旧 generation 不写旧库或新库；
- 不完整快照回滚后旧当前指针保持不变。

## 12. 实机验收门槛

每个目标分辨率至少完成三次同一库存全量扫描：

- 三次捕获数量与人工填写数量一致；
- 不出现重复索引、漏图或错位点击；
- 串行与并行解析生成的装备内容指纹一致；
- 随机偏差后的点击命中率为 100%；
- 每页滚轮命令次数稳定，异常页能由反馈闭环收敛；
- F12 在点击、滚动和解析积压阶段都能停止且不产生新当前快照；
- 鼠标与手柄扫描先后运行时，账号 SQL 始终只有一个 current 库存快照；
- 日志不包含账号显示名、完整路径、截图内容、完整 OCR 文本或装备 UID 列表。

通过专项测试后依次运行：

```powershell
python tools/quality/run_tests.py core
python -m ruff check .
python -X pycache_prefix=build/compile-cache -m compileall -q src tests tools
git diff --check
```

真实 Windows 验收完成后再运行 full，并把各分辨率的布局、滚轮统计和失败恢复结果补充到本文，而不是把
未验证参数描述成稳定能力。

## 13. 本文不包含

- 游戏内装备状态修改；
- 仓库管理计划或状态写回；
- 配装计算和自动装配；
- nte-core 正式 UID 获取；
- 参考项目 OCR、Web UI、HTTP API 或 JSON 库存迁移。
