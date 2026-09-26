# 构建并刷新首页工作台。
"""构建并刷新首页工作台。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.constants import APP_VERSION
from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.ui.dashboard_widgets import metric_card, set_status_badge
from src.ui.image_scaling import asset_pixmap


_SYNC_ERROR_GUIDANCE = {
    "NPCAP_NOT_FOUND": (
        "原因：未安装 Npcap，或系统无法加载 Npcap 驱动。\n"
        "处理：点击“环境配置”，在 Npcap 区下载并安装 Npcap 1.88；安装完成后重新启动背包同步。"
    ),
    "GAME_PROCESS_NOT_FOUND": (
        "原因：未检测到正在运行的游戏进程。\n处理：请查看检测详情，确认组件与游戏状态后重新同步。"
    ),
    "CAPTURE_DEVICE_NOT_FOUND": (
        "原因：设置的抓包网卡不存在，或当前没有可用于游戏连接的网卡。\n"
        "处理：在设置的“背包同步”中清空抓取网卡以恢复自动选择，或填写当前有效网卡后重试。"
    ),
    "SYSTEM_PROBE_FAILED": (
        "原因：Windows 网络连接或进程探测失败。\n"
        "处理：关闭游戏和本程序后重新打开；仍失败时检查安全软件拦截，并尝试以管理员身份运行。"
    ),
    "CAPTURE_ALREADY_RUNNING": (
        "原因：nte-core 中已经存在一个抓包任务。\n处理：点击“重新同步”等待旧会话收尾；若仍未恢复，请退出并重新打开本程序。"
    ),
    "CAPTURE_NOT_RUNNING": ("原因：nte-core 的抓包会话已经停止。\n处理：点击“重新同步”重新建立会话。"),
    "PROTOCOL_VERSION_MISMATCH": (
        "原因：本程序与 nte-core 的协议版本不一致。\n处理：重新安装同一发布包中的完整程序，不要混用旧版 nte-core.exe。"
    ),
    "HANDSHAKE_REQUIRED": (
        "原因：本程序与 nte-core 的初始化握手未完成。\n处理：重启本程序；仍失败时重新安装完整发布包。"
    ),
    "INVENTORY_NOT_READY": (
        "原因：尚未收到完整背包数据。\n处理：请查看检测详情，按当前同步来源的提示完成准备。"
    ),
    "NteCoreNotFoundError": ("原因：程序目录中缺少 nte-core.exe。\n处理：重新安装完整发布包，不要单独复制主程序运行。"),
    "NteCoreTimeoutError": (
        "原因：nte-core 在限定时间内没有响应。\n处理：重启本程序；仍失败时检查安全软件是否拦截 nte-core.exe。"
    ),
    "NteCoreProcessError": (
        "原因：nte-core.exe 无法启动或启动后异常退出。\n"
        "处理：检查安全软件隔离记录与程序目录权限，然后重新安装完整发布包。"
    ),
    "SNAPSHOT_SAVE_FAILED": (
        "原因：稳定背包已收到，但保存到当前账号数据库失败，暂未识别具体原因。\n"
        "处理：请反馈下方技术详情和账号日志中的保存失败记录；后台会自动重试。"
    ),
    "SNAPSHOT_SAVE_BUSY": (
        "原因：当前账号数据库存在锁冲突，等待后仍无法完成保存。\n"
        "处理：退出其他计算器窗口和正在操作该库的工具后重试；持续失败时反馈技术详情。"
    ),
    "SNAPSHOT_SAVE_READONLY": (
        "原因：SQLite 报告当前账号数据库无法写入（只读）。\n"
        "处理：检查账号数据库及所在目录的写入权限、只读状态和安全软件拦截记录。"
    ),
    "SNAPSHOT_SAVE_PERMISSION": (
        "原因：SQLite 拒绝了本次数据库访问操作。\n"
        "处理：检查账号数据目录访问权限和安全软件拦截记录，并反馈技术详情。"
    ),
    "SNAPSHOT_SAVE_FULL": (
        "原因：SQLite 报告数据库或磁盘已满，也可能触及数据库容量限制。\n"
        "处理：检查账号数据所在盘和系统临时目录所在盘的空间；空间充足时反馈技术详情。"
    ),
    "SNAPSHOT_SAVE_CORRUPT": (
        "原因：SQLite 检测到数据库损坏，或文件不是有效的 SQLite 数据库。\n"
        "处理：停止同步并退出程序，保留原账号数据，备份后再检查或恢复；请反馈技术详情。"
    ),
    "SNAPSHOT_SAVE_IO": (
        "原因：保存账号数据时发生底层读写错误，不能仅凭此错误判断为空间不足。\n"
        "处理：检查存储设备、文件系统和安全软件拦截记录；保留原账号数据并反馈技术详情。"
    ),
    "SNAPSHOT_SAVE_OPEN": (
        "原因：SQLite 无法打开保存所需的数据库或辅助文件。\n"
        "处理：检查账号数据目录是否可访问、可写，以及存储设备是否在线，并反馈技术详情。"
    ),
    "SNAPSHOT_SAVE_SCHEMA": (
        "原因：数据库结构发生变化，或保存所需的表、字段与程序不匹配。\n"
        "处理：保留原账号库并反馈程序版本和技术详情，以便检查数据库升级或结构异常。"
    ),
    "SNAPSHOT_SAVE_CONSTRAINT": (
        "原因：写入触发数据库约束冲突，例如外键、唯一性、非空或字段规则。\n"
        "处理：保留原账号库并反馈技术详情中的 SQLite 错误名和失败阶段，以便定位冲突。"
    ),
    "SNAPSHOT_SAVE_TRANSACTION": (
        "原因：账号数据库的事务状态异常。\n"
        "处理：退出并重新打开程序后重试；持续失败时反馈技术详情中的失败阶段和回滚状态。"
    ),
}

_WORKBENCH_VERSION = ".".join(APP_VERSION.split(".")[:2])


def inventory_sync_error_guidance(error_code: str | None, error: str | None, *, capture_source: str) -> str:
    """Translate sync failures into concrete user actions while retaining diagnostics."""
    code = str(error_code or "").strip()
    if code == "GAME_PROCESS_NOT_FOUND":
        if capture_source == "native":
            return (
                "原因：未检测到正在运行的游戏进程。\n"
                "处理：如需部署或更新组件，请完全退出游戏并完成部署，再启动游戏；"
                "组件已部署后，请登录并进入游戏场景，等待同步完成。"
            )
        if capture_source == "packet":
            return "原因：未检测到正在运行的游戏进程。\n处理：先启动游戏并停留在登录页，待抓包监听就绪后再登录。"
    if code == "INVENTORY_NOT_READY":
        if capture_source == "native":
            return (
                "原因：尚未读取到完整背包数据。\n"
                "处理：请登录并进入游戏场景，等待完整数据同步；已进入时请稍候，长时间未恢复请查看检测详情或重启同步。"
            )
        if capture_source == "packet":
            return "原因：尚未捕获到完整背包数据。\n处理：请返回登录页，待抓包监听就绪后重新登录，并等待背包数量稳定。"
    if code in _SYNC_ERROR_GUIDANCE:
        return _SYNC_ERROR_GUIDANCE[code]
    detail = str(error or "").lower()
    if "permission denied" in detail or "access is denied" in detail:
        return "原因：程序没有权限启动组件或写入账号数据。\n处理：检查程序和账号数据目录权限，并尝试以管理员身份运行。"
    if "database is locked" in detail:
        return (
            "原因：当前账号数据库正被另一个程序或本程序的残留进程占用。\n"
            "处理：关闭其他 NTE Drive Calc 窗口，重启本程序后再同步。"
        )
    if "no space" in detail or "disk full" in detail:
        return "原因：磁盘空间不足。\n处理：清理程序所在盘或账号数据所在盘后重新同步。"
    return (
        "原因：背包同步组件发生未分类错误。\n"
        "处理：先停止并重新启动同步；仍失败时查看下方技术详情和日志，再反馈完整错误。"
    )


def _section(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)
    title_label = QLabel(title)
    title_label.setObjectName("cardTitle")
    layout.addWidget(title_label)
    if description:
        subtitle = QLabel(description)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(subtitle)
    return card, layout


def _home_sync_help_text(mode: str) -> str:
    """Keep the next action clear for the current synchronization source."""

    if mode == "offline":
        return (
            "当前是离线模式，只使用已保存数据。\n"
            "要同步：先到“工作模式设置”选择并确认可同步的模式，"
            "再返回工作台开启“自动同步”。"
        )
    if mode in {"medium", "developer"}:
        return (
            "1. 开启“自动同步”，按提示准备组件；若需部署，先退出游戏（Loader 还需退出启动器）。\n"
            "2. 登录并进入游戏场景，等待背包和角色数据保存。\n"
            "3. 数据未更新？点“重启同步”；仍有问题时看“检测详情”。"
        )
    return (
        "1. 开启“自动同步”，按提示确认抓包环境。\n"
        "2. 启动游戏，等待抓包监听就绪后再登录；完整背包会自动保存。\n"
        "3. 数据未更新？点“重启同步”，按提示重新登录；仍有问题时看“检测详情”。"
    )


def _show_home_sync_help(window) -> None:
    mode = getattr(window.work_mode_service.settings.mode, "value", "offline")
    dialog = QDialog(window)
    dialog.setObjectName("homeSyncHelpDialog")
    dialog.setWindowTitle("自动同步 · 如何使用")
    dialog.setWindowModality(Qt.WindowModal)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 16)
    layout.setSpacing(18)
    instructions = QLabel(_home_sync_help_text(str(mode)), dialog)
    instructions.setObjectName("homeSyncHelpInstructions")
    instructions.setWordWrap(True)
    instructions.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    layout.addWidget(instructions)
    actions = QHBoxLayout()
    actions.addStretch()
    selected = {"target": ""}

    def choose(target: str) -> None:
        selected["target"] = target
        dialog.accept()

    mode_button = QPushButton("工作模式设置", dialog)
    mode_button.setObjectName("homeSyncHelpModeSettings")
    mode_button.clicked.connect(lambda: choose("mode"))
    actions.addWidget(mode_button)
    environment_button = QPushButton("环境设置", dialog)
    environment_button.setObjectName("homeSyncHelpEnvironmentSettings")
    environment_button.clicked.connect(lambda: choose("game_path"))
    actions.addWidget(environment_button)
    close_button = QPushButton("关闭", dialog)
    close_button.setDefault(True)
    close_button.setFocus()
    close_button.clicked.connect(dialog.reject)
    actions.addWidget(close_button)
    layout.addLayout(actions)
    fit_dialog_to_available_screen(dialog, QSize(570, 250))
    if dialog.exec() == QDialog.Accepted and selected["target"]:
        window.work_mode_controller.open_settings(selected["target"])


def build_home_page(window) -> QScrollArea:
    page = QWidget()
    page.setObjectName("homePage")
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)

    root = QVBoxLayout(page)
    root.setContentsMargins(22, 18, 22, 22)
    root.setSpacing(16)

    hero = QFrame()
    hero.setObjectName("homeHero")
    hero.setStyleSheet(themed_style("QFrame#homeHero{background:#10243f;border:1px solid #1f6feb;border-radius:12px}"))
    hero_layout = QHBoxLayout(hero)
    hero_layout.setContentsMargins(22, 18, 22, 18)
    title_column = QVBoxLayout()
    title = QLabel(f"NTE Drive Calc {_WORKBENCH_VERSION} 工作台")
    title.setStyleSheet(themed_style("color:#f0f6fc;font-size:21px;font-weight:700"))
    window.home_account_label = QLabel("正在读取账号数据…")
    window.home_account_label.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    title_column.addWidget(title)
    title_column.addWidget(window.home_account_label)
    hero_layout.addLayout(title_column)
    hero_layout.addStretch()
    # 工作台头像取已核验角色目录中的黑羽 256px 图片，不替换战报数据集。
    hero_icon_path = GameUiAssetCatalog(
        window.app_context.paths.cultivation_asset_root
    ).character_icon(1042)
    if hero_icon_path is not None:
        hero_icon = QLabel()
        hero_icon.setObjectName("homeHeroAvatar")
        hero_icon.setFixedSize(72, 72)
        hero_icon.setPixmap(asset_pixmap(
            hero_icon_path, 72, hero_icon.devicePixelRatioF()
        ))
        hero_icon.setStyleSheet("background:transparent")
        hero_layout.addWidget(hero_icon)
    window.home_sync_badge = QLabel("未启动")
    window.home_sync_badge.setAlignment(Qt.AlignCenter)
    set_status_badge(window.home_sync_badge, "未启动", "neutral")
    hero_layout.addWidget(window.home_sync_badge)
    root.addWidget(hero)

    window.home_upgrade_banner = QFrame(page)
    window.home_upgrade_banner.setObjectName("homeUpgradeBanner")
    window.home_upgrade_banner.setStyleSheet(themed_style(
        "QFrame#homeUpgradeBanner{background:#2a2112;border:1px solid #d29922;border-radius:8px}"
    ))
    upgrade_row = QHBoxLayout(window.home_upgrade_banner)
    upgrade_row.setContentsMargins(16, 12, 16, 12)
    window.home_upgrade_summary = QLabel("旧组件升级：请按引导处理。", window.home_upgrade_banner)
    window.home_upgrade_summary.setWordWrap(True)
    upgrade_row.addWidget(window.home_upgrade_summary, 1)
    upgrade_button = QPushButton("继续处理", window.home_upgrade_banner)
    upgrade_button.setObjectName("btnNew")
    upgrade_button.clicked.connect(window.work_mode_controller.show_upgrade_guide)
    upgrade_row.addWidget(upgrade_button)
    window.home_upgrade_banner.hide()
    root.addWidget(window.home_upgrade_banner)

    metrics = QGridLayout()
    metrics.setHorizontalSpacing(12)
    metrics.setVerticalSpacing(12)
    definitions = (
        ("inventory", "稳定背包", "等待首次同步"),
        ("module", "驱动", "当前稳定背包"),
        ("core", "卡带", "当前稳定背包"),
        ("equipped", "已装备", "按当前稳定快照"),
        ("plans", "配装方案", "保存在当前账号"),
        ("characters", "角色目录", "当前账号尚未同步角色"),
    )
    window.home_metric_labels = {}
    for index, (key, label, subtitle) in enumerate(definitions):
        card, value_label, subtitle_label = metric_card(label, "—", subtitle)
        window.home_metric_labels[key] = (value_label, subtitle_label)
        metrics.addWidget(card, index // 3, index % 3)
    root.addLayout(metrics)

    sync_card, sync_layout = _section("游戏数据同步")
    window.home_sync_title = sync_layout.itemAt(0).widget()
    # 保留兼容投影供控制器和诊断读取，常态页面只展示当前同步状态。
    window.home_sync_source_label = QLabel("", sync_card)
    window.home_sync_source_label.hide()
    window.home_sync_detail = QLabel("背包同步尚未启动")
    window.home_sync_detail.setWordWrap(True)
    sync_layout.addWidget(window.home_sync_detail)
    window.home_character_sync_detail = QLabel("角色养成：尚无已保存的游戏养成数据。")
    window.home_character_sync_detail.setWordWrap(True)
    window.home_character_sync_detail.setProperty("savedSummary", window.home_character_sync_detail.text())
    sync_layout.addWidget(window.home_character_sync_detail)
    window.home_character_sync_detail.hide()
    sync_actions = QHBoxLayout()
    window.home_auto_sync_toggle = QCheckBox("自动同步")
    window.home_auto_sync_toggle.setChecked(window.work_mode_service.settings.auto_sync_enabled)
    window.home_auto_sync_toggle.toggled.connect(window.auto_sync_controller.set_enabled)
    window.home_restart_sync_button = QPushButton("重启同步")
    window.home_restart_sync_button.setObjectName("btnPrimary")
    window.home_restart_sync_button.clicked.connect(window.auto_sync_controller.open_restart)
    check = QPushButton("检测详情")
    check.clicked.connect(lambda: window.work_mode_controller.check(show=True))
    window.home_sync_help_button = QPushButton("如何使用")
    window.home_sync_help_button.clicked.connect(lambda: _show_home_sync_help(window))
    sync_actions.addWidget(window.home_auto_sync_toggle)
    sync_actions.addWidget(window.home_restart_sync_button)
    sync_actions.addWidget(check)
    sync_actions.addWidget(window.home_sync_help_button)
    sync_actions.addStretch()
    sync_layout.addLayout(sync_actions)
    window.home_sync_action_hint = QLabel("")
    window.home_sync_action_hint.setWordWrap(True)
    sync_layout.addWidget(window.home_sync_action_hint)
    # 保留兼容投影供控制器与测试读取，页面由“稳定背包”指标卡统一展示保存时间。
    window.home_last_sync_label = QLabel("尚无已保存的背包", sync_card)
    window.home_last_sync_label.hide()
    root.addWidget(sync_card)

    actions_card, actions_layout = _section("快捷操作")
    actions = QHBoxLayout()
    for label, page_key in (
        ("计算配装", "execute"),
        ("查看方案", "equipment"),
        ("角色边际", "my_role"),
        ("仓库管理", "warehouse"),
        ("空幕鉴定", "identify"),
    ):
        button = QPushButton(label)
        button.clicked.connect(lambda _checked=False, key=page_key: window._go(key))
        actions.addWidget(button)
    actions.addStretch()
    actions_layout.addLayout(actions)
    root.addWidget(actions_card)

    root.addStretch()
    return scroll


def _local_snapshot_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def refresh_home_page(window, dashboard: dict[str, Any]) -> None:
    account = dashboard["account"]
    inventory = dashboard.get("inventory")
    window.home_account_label.setText(f"当前账号：{account['account_name']} · 背包、角色养成与配装方案独立保存")

    values = {
        "inventory": int(inventory["stored_item_count"]) if inventory else 0,
        "module": int(inventory["module_count"]) if inventory else 0,
        "core": int(inventory["core_count"]) if inventory else 0,
        "equipped": int(inventory["equipped_count"]) if inventory else 0,
        "plans": int(dashboard["loadout_plan_count"]),
        "characters": int(dashboard["characters"]["catalog_count"]),
    }
    for key, value in values.items():
        window.home_metric_labels[key][0].setText(str(value))

    synced_count = int(dashboard["characters"]["synced_count"])
    window.home_metric_labels["characters"][1].setText(
        f"当前账号已同步 {synced_count} 个角色" if synced_count else "当前账号尚未同步角色"
    )
    profile_count = int(dashboard["characters"]["profile_count"])
    role_detail = (f"角色养成：已保存 {profile_count} 个角色的已确认养成字段。" if profile_count else
                   "角色养成：尚无已保存的游戏养成数据，连接后自动读取。")
    window.home_character_sync_detail.setProperty("savedSummary", role_detail)
    window.home_character_sync_detail.setText(role_detail)

    inventory_subtitle = window.home_metric_labels["inventory"][1]
    if inventory:
        saved_time = _local_snapshot_time(inventory["captured_at_utc"])
        inventory_subtitle.setText(f"快照 #{inventory['snapshot_id']} · {saved_time}")
    else:
        inventory_subtitle.setText("等待首次同步")
    if inventory:
        window.home_last_sync_label.setText(
            f"上次保存（本地时间）：{saved_time} · 驱动 {inventory['module_count']} 件"
            f" · 空幕 {inventory['core_count']} 件"
        )
        window.home_last_sync_label.setToolTip(
            "当前背包快照的保存时间。同步内容未变化时沿用已有快照，保存时间不会更新；本次同步状态见上方。"
        )
    else:
        window.home_last_sync_label.setText("尚无已保存的背包")
        window.home_last_sync_label.setToolTip("")
    window.auto_sync_controller.render()
