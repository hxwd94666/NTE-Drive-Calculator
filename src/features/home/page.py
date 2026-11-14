# 构建并刷新首页工作台。
"""构建并刷新首页工作台。"""

from __future__ import annotations

from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import plain_label, plain_tooltip, resolve_text_catalog
from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.constants import APP_VERSION
from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.localization.catalog import localized_text
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.features.home.feature_guide import add_feature_guides
from src.ui.common.dashboard_widgets import metric_card, set_status_badge
from src.ui.common.image_scaling import asset_pixmap


# 每条指引登记稳定 key 与发行中文兜底；兜底与原文逐字一致，换行位置和标点不重排。
_SYNC_ERROR_GUIDANCE: dict[str, tuple[str, str]] = {
    "NPCAP_NOT_FOUND": (
        "home.page.sync_guidance.npcap_not_found",
        "原因：未安装 Npcap，或系统无法加载 Npcap 驱动。\n"
        "处理：点击“环境配置”，在 Npcap 区下载并安装 Npcap 1.88；安装完成后重新启动背包同步。",
    ),
    "GAME_PROCESS_NOT_FOUND": (
        "home.page.sync_guidance.game_process_not_found",
        "原因：未检测到正在运行的游戏进程。\n处理：请查看检测详情，确认组件与游戏状态后重新同步。",
    ),
    "CAPTURE_DEVICE_NOT_FOUND": (
        "home.page.sync_guidance.capture_device_not_found",
        "原因：设置的抓包网卡不存在，或当前没有可用于游戏连接的网卡。\n"
        "处理：在设置的“背包同步”中清空抓取网卡以恢复自动选择，或填写当前有效网卡后重试。",
    ),
    "SYSTEM_PROBE_FAILED": (
        "home.page.sync_guidance.system_probe_failed",
        "原因：Windows 网络连接或进程探测失败。\n"
        "处理：关闭游戏和本程序后重新打开；仍失败时检查安全软件拦截，并尝试以管理员身份运行。",
    ),
    "CAPTURE_ALREADY_RUNNING": (
        "home.page.sync_guidance.capture_already_running",
        "原因：nte-core 中已经存在一个抓包任务。\n处理：点击“重新同步”等待旧会话收尾；若仍未恢复，请退出并重新打开本程序。",
    ),
    "CAPTURE_NOT_RUNNING": (
        "home.page.sync_guidance.capture_not_running",
        "原因：nte-core 的抓包会话已经停止。\n处理：点击“重新同步”重新建立会话。",
    ),
    "PROTOCOL_VERSION_MISMATCH": (
        "home.page.sync_guidance.protocol_version_mismatch",
        "原因：本程序与 nte-core 的协议版本不一致。\n处理：重新安装同一发布包中的完整程序，不要混用旧版 nte-core.exe。",
    ),
    "HANDSHAKE_REQUIRED": (
        "home.page.sync_guidance.handshake_required",
        "原因：本程序与 nte-core 的初始化握手未完成。\n处理：重启本程序；仍失败时重新安装完整发布包。",
    ),
    "INVENTORY_NOT_READY": (
        "home.page.sync_guidance.inventory_not_ready",
        "原因：尚未收到完整背包数据。\n处理：请查看检测详情，按当前同步来源的提示完成准备。",
    ),
    "NteCoreNotFoundError": (
        "home.page.sync_guidance.nte_core_not_found",
        "原因：程序目录中缺少 nte-core.exe。\n处理：重新安装完整发布包，不要单独复制主程序运行。",
    ),
    "NteCoreTimeoutError": (
        "home.page.sync_guidance.nte_core_timeout",
        "原因：nte-core 在限定时间内没有响应。\n处理：重启本程序；仍失败时检查安全软件是否拦截 nte-core.exe。",
    ),
    "NteCoreProcessError": (
        "home.page.sync_guidance.nte_core_process_error",
        "原因：nte-core.exe 无法启动或启动后异常退出。\n"
        "处理：检查安全软件隔离记录与程序目录权限，然后重新安装完整发布包。",
    ),
    "SNAPSHOT_SAVE_FAILED": (
        "home.page.sync_guidance.snapshot_save_failed",
        "原因：稳定背包已收到，但保存到当前账号数据库失败，暂未识别具体原因。\n"
        "处理：后台会自动重试；持续失败时请反馈错误码及账号日志。",
    ),
    "SNAPSHOT_SAVE_BUSY": (
        "home.page.sync_guidance.snapshot_save_busy",
        "原因：当前账号数据库存在锁冲突，等待后仍无法完成保存。\n"
        "处理：退出其他计算器窗口和正在操作该库的工具后重试；持续失败时反馈错误码。",
    ),
    "SNAPSHOT_SAVE_READONLY": (
        "home.page.sync_guidance.snapshot_save_readonly",
        "原因：SQLite 报告当前账号数据库无法写入（只读）。\n"
        "处理：检查账号数据库及所在目录的写入权限、只读状态和安全软件拦截记录。",
    ),
    "SNAPSHOT_SAVE_PERMISSION": (
        "home.page.sync_guidance.snapshot_save_permission",
        "原因：SQLite 拒绝了本次数据库访问操作。\n"
        "处理：检查账号数据目录权限与安全软件拦截记录；持续失败时反馈错误码。",
    ),
    "SNAPSHOT_SAVE_FULL": (
        "home.page.sync_guidance.snapshot_save_full",
        "原因：SQLite 报告数据库或磁盘已满，也可能触及数据库容量限制。\n"
        "处理：检查账号数据盘和系统临时目录所在盘的剩余空间；空间充足时反馈错误码。",
    ),
    "SNAPSHOT_SAVE_CORRUPT": (
        "home.page.sync_guidance.snapshot_save_corrupt",
        "原因：SQLite 检测到数据库损坏，或文件不是有效的 SQLite 数据库。\n"
        "处理：停止同步并退出程序，保留原账号数据；备份后再检查，并反馈错误码。",
    ),
    "SNAPSHOT_SAVE_IO": (
        "home.page.sync_guidance.snapshot_save_io",
        "原因：保存账号数据时发生底层读写错误，不能仅凭此错误判断为空间不足。\n"
        "处理：检查存储设备和安全软件拦截记录；保留原账号数据并反馈错误码。",
    ),
    "SNAPSHOT_SAVE_OPEN": (
        "home.page.sync_guidance.snapshot_save_open",
        "原因：SQLite 无法打开保存所需的数据库或辅助文件。\n"
        "处理：检查账号数据目录是否可写、存储设备是否在线；持续失败时反馈错误码。",
    ),
    "SNAPSHOT_SAVE_SCHEMA": (
        "home.page.sync_guidance.snapshot_save_schema",
        "原因：数据库结构发生变化，或保存所需的表、字段与程序不匹配。\n"
        "处理：保留原账号数据并反馈程序版本与错误码，以便检查数据库升级。",
    ),
    "SNAPSHOT_SAVE_CONSTRAINT": (
        "home.page.sync_guidance.snapshot_save_constraint",
        "原因：写入触发数据库约束冲突，例如外键、唯一性、非空或字段规则。\n"
        "处理：保留原账号数据并反馈错误码；不要反复覆盖数据库。",
    ),
    "SNAPSHOT_SAVE_TRANSACTION": (
        "home.page.sync_guidance.snapshot_save_transaction",
        "原因：账号数据库的事务状态异常。\n"
        "处理：退出并重新打开程序后重试；持续失败时反馈错误码和账号日志。",
    ),
}

_WORKBENCH_VERSION = ".".join(APP_VERSION.split(".")[:3])


def inventory_sync_error_guidance(
    error_code: str | None,
    error: str | None,
    *,
    capture_source: str,
    text_catalog=None,
) -> str:
    """按错误码和同步来源给出下一步；未分类错误不伪装成功。"""
    code = str(error_code or "").strip()
    if code == "GAME_PROCESS_NOT_FOUND":
        if capture_source == "native":
            return localized_text(
                text_catalog,
                "home.page.sync_guidance.game_process_not_found.native",
                "原因：未检测到正在运行的游戏进程。\n"
                "处理：如需部署或更新组件，请完全退出游戏并完成部署，再启动游戏；"
                "组件已部署后，请登录并进入游戏场景，等待同步完成。",
            )
        if capture_source == "packet":
            return localized_text(
                text_catalog,
                "home.page.sync_guidance.game_process_not_found.packet",
                "原因：未检测到正在运行的游戏进程。\n处理：先启动游戏并停留在登录页，待抓包监听就绪后再登录。",
            )
    if code == "INVENTORY_NOT_READY":
        if capture_source == "native":
            return localized_text(
                text_catalog,
                "home.page.sync_guidance.inventory_not_ready.native",
                "原因：尚未读取到完整背包数据。\n"
                "处理：请登录并进入游戏场景，等待完整数据同步；已进入时请稍候，长时间未恢复请查看检测详情或重启同步。",
            )
        if capture_source == "packet":
            return localized_text(
                text_catalog,
                "home.page.sync_guidance.inventory_not_ready.packet",
                "原因：尚未捕获到完整背包数据。\n处理：请返回登录页，待抓包监听就绪后重新登录，并等待背包数量稳定。",
            )
    guidance = _SYNC_ERROR_GUIDANCE.get(code)
    if guidance is not None:
        key, fallback = guidance
        return localized_text(text_catalog, key, fallback)
    detail = str(error or "").lower()
    if "permission denied" in detail or "access is denied" in detail:
        return localized_text(
            text_catalog,
            "home.page.sync_guidance.permission_denied",
            "原因：程序没有权限启动组件或写入账号数据。\n处理：检查程序和账号数据目录权限，并尝试以管理员身份运行。",
        )
    if "database is locked" in detail:
        return localized_text(
            text_catalog,
            "home.page.sync_guidance.database_locked",
            "原因：当前账号数据库正被另一个程序或本程序的残留进程占用。\n"
            "处理：关闭其他 NTE Drive Calc 窗口，重启本程序后再同步。",
        )
    if "no space" in detail or "disk full" in detail:
        return localized_text(
            text_catalog,
            "home.page.sync_guidance.disk_full",
            "原因：磁盘空间不足。\n处理：清理程序所在盘或账号数据所在盘后重新同步。",
        )
    return localized_text(
        text_catalog,
        "home.page.sync_guidance.unclassified",
        "原因：背包同步组件发生未分类错误。\n"
        "处理：先停止并重新启动同步；仍失败时查看检测详情和账号日志，并反馈错误码。",
    )


def _section(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)
    title_label = plain_label(title)
    title_label.setObjectName("cardTitle")
    layout.addWidget(title_label)
    if description:
        subtitle = plain_label(description)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(subtitle)
    return card, layout


def _home_sync_help_text(mode: str, *, text_catalog=None) -> str:
    """帮助只解释当前已确认模式的准备顺序，不开启自动同步。"""

    if mode == "offline":
        return localized_text(
            text_catalog,
            "home.page.home_sync_help.offline",
            "当前是离线模式，只使用已保存数据。\n"
            "要同步：先到“工作模式设置”选择并确认可同步的模式，"
            "再返回工作台开启“自动同步”。",
        )
    if mode in {"medium", "developer"}:
        return localized_text(
            text_catalog,
            "home.page.home_sync_help.native",
            "1. 开启“自动同步”，按提示准备组件；若需部署，先退出游戏（Loader 还需退出启动器）。\n"
            "2. 登录并进入游戏场景，等待背包和角色数据保存。\n"
            "3. 数据未更新？点“重启同步”；仍有问题时看“检测详情”。",
        )
    return localized_text(
        text_catalog,
        "home.page.home_sync_help.packet",
        "1. 开启“自动同步”，按提示确认抓包环境。\n"
        "2. 启动游戏，等待抓包监听就绪后再登录；完整背包会自动保存。\n"
        "3. 数据未更新？点“重启同步”，按提示重新登录；仍有问题时看“检测详情”。",
    )


def _show_home_sync_help(window) -> None:
    text_catalog = getattr(window, 'text_catalog', None)
    _ui_text = resolve_text_catalog(text_catalog).render
    mode = getattr(window.work_mode_service.settings.mode, "value", "offline")
    dialog = QDialog(window)
    dialog.setObjectName("homeSyncHelpDialog")
    dialog.setWindowTitle(_ui_text('home.page.show_home_sync_help.dialog.title'))
    dialog.setWindowModality(Qt.WindowModal)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 16)
    layout.setSpacing(18)
    instructions = plain_label(_home_sync_help_text(str(mode), text_catalog=text_catalog), dialog)
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

    mode_button = QPushButton(_ui_text('home.page.show_home_sync_help.mode_button'), dialog)
    mode_button.setObjectName("homeSyncHelpModeSettings")
    mode_button.clicked.connect(lambda: choose("mode"))
    actions.addWidget(mode_button)
    environment_button = QPushButton(_ui_text('home.page.show_home_sync_help.environment_button'), dialog)
    environment_button.setObjectName("homeSyncHelpEnvironmentSettings")
    environment_button.clicked.connect(lambda: choose("game_path"))
    actions.addWidget(environment_button)
    close_button = QPushButton(_ui_text('home.page.show_home_sync_help.close_button'), dialog)
    close_button.setDefault(True)
    close_button.setFocus()
    close_button.clicked.connect(dialog.reject)
    actions.addWidget(close_button)
    layout.addLayout(actions)
    fit_dialog_to_available_screen(dialog, QSize(570, 250))
    if dialog.exec() == QDialog.Accepted and selected["target"]:
        window.work_mode_controller.open_settings(selected["target"])


def build_home_page(window) -> QScrollArea:
    text_catalog = getattr(window, 'text_catalog', None)
    _ui_text = resolve_text_catalog(text_catalog).render
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
    title = plain_label(_ui_text('home.page.build_home_page.title.state', WORKBENCH_VERSION=f'{_WORKBENCH_VERSION}'))
    title.setStyleSheet(themed_style("color:#f0f6fc;font-size:21px;font-weight:700"))
    window.home_account_label = plain_label(_ui_text('home.page.build_home_page.home_account_label'))
    window.home_account_label.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    title_column.addWidget(title)
    title_column.addWidget(window.home_account_label)
    hero_layout.addLayout(title_column)
    hero_layout.addStretch()
    # 工作台头像按正式角色 ID 读取当前图鉴的曳尔沐图片。
    hero_icon_path = GameUiAssetCatalog(
        window.app_context.paths.cultivation_asset_root
    ).character_icon(1090)
    if hero_icon_path is not None:
        hero_icon = plain_label()
        hero_icon.setObjectName("homeHeroAvatar")
        hero_icon.setFixedSize(72, 72)
        hero_icon.setPixmap(asset_pixmap(
            hero_icon_path, 72, hero_icon.devicePixelRatioF()
        ))
        hero_icon.setStyleSheet("background:transparent")
        hero_layout.addWidget(hero_icon)
    window.home_sync_badge = plain_label(_ui_text('home.page.build_home_page.home_sync_badge'))
    window.home_sync_badge.setAlignment(Qt.AlignCenter)
    set_status_badge(window.home_sync_badge, resolve_text_catalog(getattr(window, 'text_catalog', None)).render('home.page.build_home_page.home_sync_badge'), "neutral")
    hero_layout.addWidget(window.home_sync_badge)
    root.addWidget(hero)

    window.home_upgrade_banner = QFrame(page)
    window.home_upgrade_banner.setObjectName("homeUpgradeBanner")
    window.home_upgrade_banner.setStyleSheet(themed_style(
        "QFrame#homeUpgradeBanner{background:#2a2112;border:1px solid #d29922;border-radius:8px}"
    ))
    upgrade_row = QHBoxLayout(window.home_upgrade_banner)
    upgrade_row.setContentsMargins(16, 12, 16, 12)
    window.home_upgrade_summary = plain_label(_ui_text('home.page.build_home_page.home_upgrade_summary'), window.home_upgrade_banner)
    window.home_upgrade_summary.setWordWrap(True)
    upgrade_row.addWidget(window.home_upgrade_summary, 1)
    upgrade_button = QPushButton(_ui_text('home.page.build_home_page.upgrade_button'), window.home_upgrade_banner)
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
        card, value_label, subtitle_label = metric_card(
            ui_enum_text(text_catalog, label), "—", ui_enum_text(text_catalog, subtitle)
        )
        window.home_metric_labels[key] = (value_label, subtitle_label)
        metrics.addWidget(card, index // 3, index % 3)
    root.addLayout(metrics)

    sync_card, sync_layout = _section(resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.home.build_home_page.sync_card_sync_layout'))
    window.home_sync_title = sync_layout.itemAt(0).widget()
    # 保留兼容投影供控制器和诊断读取，常态页面只展示当前同步状态。
    window.home_sync_source_label = plain_label("", sync_card)
    window.home_sync_source_label.hide()
    window.home_sync_detail = plain_label(_ui_text('home.page.build_home_page.home_sync_detail'))
    window.home_sync_detail.setWordWrap(True)
    sync_layout.addWidget(window.home_sync_detail)
    window.home_character_sync_detail = plain_label(_ui_text('home.page.build_home_page.home_character_sync_detail'))
    window.home_character_sync_detail.setWordWrap(True)
    window.home_character_sync_detail.setProperty("savedSummary", window.home_character_sync_detail.text())
    sync_layout.addWidget(window.home_character_sync_detail)
    window.home_character_sync_detail.hide()
    sync_actions = QHBoxLayout()
    window.home_auto_sync_toggle = QCheckBox(_ui_text('home.page.build_home_page.home_auto_sync_toggle'))
    window.home_auto_sync_toggle.setChecked(window.work_mode_service.settings.auto_sync_enabled)
    window.home_auto_sync_toggle.toggled.connect(window.auto_sync_controller.set_enabled)
    window.home_restart_sync_button = QPushButton(_ui_text('home.page.build_home_page.home_restart_sync_button'))
    window.home_restart_sync_button.setObjectName("btnPrimary")
    window.home_restart_sync_button.clicked.connect(window.auto_sync_controller.open_restart)
    check = QPushButton(_ui_text('home.page.build_home_page.check'))
    check.setObjectName("btnNew")
    check.clicked.connect(lambda: window.work_mode_controller.check(show=True))
    window.home_sync_help_button = QPushButton(_ui_text('home.page.build_home_page.home_sync_help_button'))
    window.home_sync_help_button.clicked.connect(lambda: _show_home_sync_help(window))
    sync_actions.addWidget(window.home_auto_sync_toggle)
    sync_actions.addWidget(window.home_restart_sync_button)
    sync_actions.addWidget(check)
    sync_actions.addWidget(window.home_sync_help_button)
    sync_actions.addStretch()
    sync_layout.addLayout(sync_actions)
    window.home_sync_action_hint = plain_label("")
    window.home_sync_action_hint.setWordWrap(True)
    sync_layout.addWidget(window.home_sync_action_hint)
    # 保留兼容投影供控制器与测试读取，页面由“稳定背包”指标卡统一展示保存时间。
    window.home_last_sync_label = plain_label(_ui_text('home.page.build_home_page.home_last_sync_label'), sync_card)
    window.home_last_sync_label.hide()
    root.addWidget(sync_card)

    guides_card, guides_layout = _section(resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.home.build_home_page.guides_card_guides_layout'))
    add_feature_guides(guides_layout, window, window._go, text_catalog=getattr(window, 'text_catalog', None))
    root.addWidget(guides_card)

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
    text_catalog = getattr(window, 'text_catalog', None)
    _ui_text = resolve_text_catalog(text_catalog).render
    account = dashboard["account"]
    inventory = dashboard.get("inventory")
    window.home_account_label.setText(_ui_text('home.page.refresh_home_page.home_account_label.state', value=f"{account['account_name']}"))

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
        _ui_text('home.page.refresh_home_page.home_metric_labels__characters___1.state', synced_count=f'{synced_count}') if synced_count else _ui_text('home.page.refresh_home_page.home_metric_labels__characters___1.state_2')
    )
    profile_count = int(dashboard["characters"]["profile_count"])
    if profile_count:
        role_detail = localized_text(
            text_catalog,
            "home.page.refresh_home_page.role_detail_saved",
            "角色养成：已保存 {count} 个角色的已确认养成字段。",
            count=profile_count,
        )
    else:
        role_detail = ui_enum_text(text_catalog, "角色养成：尚无已保存的游戏养成数据，连接后自动读取。")
    window.home_character_sync_detail.setProperty("savedSummary", role_detail)
    window.home_character_sync_detail.setText(role_detail)

    inventory_subtitle = window.home_metric_labels["inventory"][1]
    if inventory:
        saved_time = _local_snapshot_time(inventory["captured_at_utc"])
        inventory_subtitle.setText(_ui_text('home.page.refresh_home_page.inventory_subtitle.state', value=f"{inventory['snapshot_id']}", saved_time=f'{saved_time}'))
    else:
        inventory_subtitle.setText(_ui_text('home.page.refresh_home_page.inventory_subtitle'))
    if inventory:
        window.home_last_sync_label.setText(
            _ui_text('home.page.refresh_home_page.home_last_sync_label.state', saved_time=f'{saved_time}', value=f"{inventory['module_count']}", value_2=f"{inventory['core_count']}")
        )
        window.home_last_sync_label.setToolTip(
            plain_tooltip(_ui_text('home.page.refresh_home_page.home_last_sync_label.hint'))
        )
    else:
        window.home_last_sync_label.setText(_ui_text('home.page.refresh_home_page.home_last_sync_label'))
        window.home_last_sync_label.setToolTip("")
    window.auto_sync_controller.render()
