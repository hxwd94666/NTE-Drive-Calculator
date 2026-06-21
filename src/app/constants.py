# 集中定义应用版本、链接和说明文案常量。
"""Application constants shared by UI and feature modules."""

APP_VERSION = "1.1.2"
ALLOCATION_TOTAL_SCORE_AREA = 35
GITHUB_HOME_URL = "https://github.com/hxwd94666/NTE-Drive-Calc"
GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/hxwd94666/NTE-Drive-Calc/releases/latest"
GITHUB_RELEASES_URL = GITHUB_HOME_URL + "/releases"
QUARK_NETDISK_URL = "https://pan.quark.cn/s/42f0d8bed584"

CORE_CONFIG_FILES = ("roles.json", "sets.json", "shapes.json", "stats.json")
USER_DATA_FILES = ("equipped_state.json", "real_inventory.json")
ACCOUNT_USER_FILES = (
    "equipped_state.json", "real_inventory.json", "priority_config.json",
    "hotkeys.json", "update_config.json", "quick_start_seen.json", "guide_seen.json",
    "ui_preferences.json",
)

SCAN_HELP = {
    "4": "直接读取库存\n\n跳过扫描步骤，直接读取已有的\nreal_inventory.json 进行分配计算。\n\n适合：已有库存数据，只想重跑分配。",
    "3": "离线解析\n\n读取 scanned_images/ 文件夹中的截图，\n用 OCR + 模板匹配提取属性，\n生成 real_inventory.json 后分配。\n\n适合：已有截图，需要解析后分配。",
    "2": "增量扫描\n\n自动探测 NEW 标记，\n截取新装备 → 解析 → 分配。\n\n适合：日常更新，只抓取新装备。",
    "1": "全量扫描\n\n虚拟手柄自动遍历背包，\n全量截图所有驱动 → 解析 → 分配。\n\n适合：首次使用。",
}

DRONE_HELP = {
    "2": "半自动模式\n\n· 自己用鼠标点选装备\n· 按 F9 抓取当前装备\n· 按 F10 结算并触发解析\n· 速度快、精准度高\n\n日常推荐",
    "1": "全自动模式\n\n· 程序自动向下翻页\n· 自动检测 NEW 标记\n· 自动截图所有新装备\n· 无需人工干预\n\n需要游戏画面在背包首页",
}

OFFLINE_HELP = {
    "full": "全量解析\n\n全量扫描已经完成，但解析中断、未解析或未解析完时使用。\n只读取 raw_drive_0001 这类全量截图，并覆盖生成 real_inventory.json。",
    "incremental": "增量解析\n\n增量扫描已经完成，但解析中断、未解析或未解析完时使用。\n只读取 raw_drive_probe、raw_drive_new、raw_drive_semi 这类增量截图，成功后追加到库存并改名接到全量截图序列后面。",
    "all": "全部截图解析\n\n读取截图文件夹根目录下所有图片。\n警告：此模式可能把旧截图重复写入库存，如若产生库存异常，请重新全量扫描。",
}
