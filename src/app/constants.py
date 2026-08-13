# 集中定义应用版本、链接和说明文案常量。
"""Application constants shared by UI and feature modules."""

from src.app.version import __version__


APP_VERSION = __version__
ALLOCATION_TOTAL_SCORE_AREA = 35
GITHUB_HOME_URL = "https://github.com/hxwd94666/NTE-Drive-Calc"
GITHUB_RELEASES_URL = GITHUB_HOME_URL + "/releases"
MIRROR_UPDATE_API = "https://mirrorchyan.com/api/resources/NTE-Drive-Calc/latest"
BILIBILI_HOME_URL = "https://b23.tv/nXJGdh3"
WORKSHOP_WEIGHT_CONFIGS_API = "https://yh.zzzmap.com/api/open/game-character/weight-configs"
QUARK_NETDISK_URL = "https://pan.quark.cn/s/82f16b845aec"
BAIDU_NETDISK_URL = "https://pan.baidu.com/s/1sPVqCpzmkQwKYCGstcZuIQ?pwd=ygke"
NETDISK_DOWNLOAD_LINKS = (
    ("夸克网盘", QUARK_NETDISK_URL),
    ("百度网盘", BAIDU_NETDISK_URL),
)

CORE_CONFIG_FILES = ("stats.json",)
ACCOUNT_USER_FILES = (
    "hotkeys.json", "update_config.json", "quick_start_seen.json", "guide_seen.json",
    "ui_preferences.json",
)

SCAN_HELP = {
    "4": "· 使用现有库存\n· 不扫描、不解析\n· 适合直接重新计算",
    "3": "· 解析已有截图\n· 生成库存记录\n· 适合扫描后尚未解析",
    "2": "· 只录入新装备\n· 追加到现有库存\n· 适合日常更新",
    "1": "· 重新扫描整个背包\n· 重新生成库存记录\n· 适合首次使用或彻底重扫",
}

DRONE_HELP = {
    "2": "· 手动点选装备\n· F9 截图，F10 完成\n· 更快、更稳定\n· 日常推荐",
    "1": "· 自动翻页并寻找 NEW 装备\n· 自动完成截图\n· 开始前停在背包首页",
}

OFFLINE_HELP = {
    "full": "· 读取全量扫描截图\n· 重新生成库存\n· 适合全量扫描解析中断",
    "incremental": "· 读取增量扫描截图\n· 追加到现有库存\n· 适合增量解析中断",
    "all": "· 读取文件夹中的全部截图\n· 可能重复录入旧截图\n· 库存异常时请重新全量扫描",
}
