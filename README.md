# 异环驱动计算器

异环驱动计算器是一款 Windows 桌面工具，用来辅助整理、识别、评分和分配《异环》中的驱动与卡带。它面向日常配装使用：把背包里的装备截图解析成数据，再按照角色图纸、目标套装和词条权重给出配装方案，也可以单独对某个驱动或卡带做快速鉴定。

项目主页：<https://github.com/hxwd94666/NTE-Drive-Calc>

## 能做什么

- 扫描驱动/卡带仓库，生成本地库存数据。
- 支持全量扫描、增量扫描、离线解析和直接读取库存。
- 根据角色优先级、角色图纸、套装配置和词条权重自动计算配装。
- 支持锁定已有配装，日常更新时只用新装备补强。
- 支持“鉴定”功能：粘贴图片、选择本地图片、截图或手动输入词条，快速查看适合哪些角色。
- 支持角色图纸查看、搜索和配置。
- 支持设置角色权重、套装数据、快捷键、更新检查等常用选项。

## 适合谁用

- 想批量整理驱动/卡带，不想手动录入库存的玩家。
- 想快速判断某个装备值不值得留的玩家。
- 想按多个角色自动分配装备，并保留已有配装结果的玩家。
- 想自己调整词条权重、角色优先级和目标套装的玩家。

## 下载安装

打开 GitHub Releases 页面，下载最新的安装包：

<https://github.com/hxwd94666/NTE-Drive-Calc/releases>

下载 `NTE_Drive_Calc_Setup_x.x.x.exe` 后直接运行安装即可。安装程序需要管理员权限，因为它可能需要安装 ViGEmBus 虚拟手柄驱动。
当前安装包内置 Nefarius ViGEmBus `x64/x86/arm64` 合包驱动，安装时保持 `Install ViGEmBus virtual gamepad driver` 勾选即可。

安装完成后从桌面快捷方式或开始菜单启动程序。

## 常见功能说明

### 扫描

- 全量扫描：适合第一次使用，会覆盖式更新截图并重新生成库存。
- 增量扫描：适合日常新增装备，只处理新出现的驱动/卡带。
- 半自动截图：手动在游戏中切换装备，按截图快捷键连续抓取。
- 离线解析：读取 `scanned_images` 中已有截图，不重新操作游戏。 
应用截图：![执行](config/github/img.png)

### 配装

配装会根据角色图纸、驱动形状、卡带套装、品质、词条权重和角色优先级计算结果。你可以选择角色优先、驱动优先、全局最优或增量更新等策略。
应用截图：![配装](config/github/img_1.png)

### 鉴定

鉴定用于快速判断单个或多个装备适合谁。支持图片识别和手动录入。图片不标准时，程序会让你手动确认驱动形状、卡带套装或主词条，避免误识别影响评分。
应用截图：![鉴定](config/github/img_2.png)

### 图纸

图纸页用于查看角色可使用的驱动形状和卡带位置。搜索角色时会显示该角色全部图纸；未搜索时每个角色只展示部分图纸，便于快速浏览。
应用截图：![图纸](config/github/img_3.png)

### 配置

配置用于自定义角色的实际参数和评分权重，以及各个套装的管理，仅用于有意者修改使用，一般玩家不要随意修改，以免引起评分系统错误。
应用截图：![配置](config/github/img_4.png)

## 运行环境

- Windows 10/11 x64
- 推荐 1080p、2K、4K 或 2560x1600 分辨率
- 需要能正常截取游戏窗口
- 扫描模式需要 ViGEmBus 虚拟手柄驱动

OCR 默认优先使用 OpenVINO。若用户明确需要 DirectML GPU，可自行设置环境变量：

```powershell
$env:NTE_OCR_BACKEND = "directml"
```

一般用户不需要设置这个。

## 本地开发

建议使用虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## 打包

生成桌面程序：

```powershell
.\.venv\Scripts\python.exe .\build_exe.py
```

生成安装包：

```powershell
.\.venv\Scripts\python.exe .\build_installer.py
```

如果已经存在 `dist\NTE_Drive_Calc`，只想重新生成安装包：

```powershell
.\.venv\Scripts\python.exe .\build_installer.py --skip-app-build
```

安装包输出位置：

```text
installer\output\NTE_Drive_Calc_Setup_1.0.1.exe
```

## 常见问题

### 全量扫描提示 VIGEM_ERROR_BUS_NOT_FOUND

这是 ViGEmBus 虚拟手柄驱动没有正常启动。请先重启电脑；如果仍然报错，打开开始菜单里的 `NTE Drive Calc -> Install ViGEmBus Driver` 重新安装/修复驱动，然后再次重启。

## TODO

### 仓库功能

计划增加库存仓库页面，用于筛选和读取库存里的所有驱动、卡带，并标注哪些驱动、卡带已经被配装锁定。点击某个驱动或卡带后，可以直接调用鉴定功能，对该装备进行角色匹配评分。后续还会支持两个卡带或两个驱动的鉴定对比。

## 数据文件

程序会使用本地 `config` 目录保存角色、套装、图纸、词条权重、库存和配装状态等数据。普通用户通常不需要手动编辑这些文件；如果你熟悉 JSON，也可以自行备份和调整。

## 反馈

遇到识别错误、安装问题或配装结果异常，可以在 GitHub Issues 提交截图、日志和复现步骤：

<https://github.com/hxwd94666/NTE-Drive-Calc/issues>
