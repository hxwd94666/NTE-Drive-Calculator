# 组织配装主列表与详情区域的联动展示。
"""Same-page role navigator and selected loadout detail."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QSize, QTimer, Qt
from PySide6.QtGui import QIcon, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.i18n import display_term, tr
from src.app.theme import themed_style
from src.features.inventory.equipment_plan_renderer import (
    _allocation_lock_icon,
    _render_equip_role,
)
from src.optimizer.contracts import DIFF_CHANGED, ROLE_LAST_DIFF, ROLE_TOTAL_SCORE
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.ui.widgets import match_pinyin


class _HorizontalRoleScrollArea(QScrollArea):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_source: QWidget | None = None
        self._drag_origin = QPoint()
        self._drag_scroll_origin = 0
        self._drag_active = False

    def enable_drag_scroll(self, widget: QWidget) -> None:
        """Let presses on role cards become horizontal panning after movement."""

        widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        event_type = event.type()
        if (
            event_type == QEvent.MouseButtonPress
            and event.button() == Qt.LeftButton
            and isinstance(watched, QWidget)
        ):
            self._drag_source = watched
            self._drag_origin = event.globalPosition().toPoint()
            self._drag_scroll_origin = self.horizontalScrollBar().value()
            self._drag_active = False
            return False
        if (
            event_type == QEvent.MouseMove
            and self._drag_source is not None
            and event.buttons() & Qt.LeftButton
        ):
            delta = event.globalPosition().toPoint() - self._drag_origin
            if not self._drag_active:
                drag_distance = QApplication.startDragDistance()
                if abs(delta.x()) < drag_distance or abs(delta.x()) <= abs(delta.y()):
                    return False
                self._drag_active = True
                if isinstance(self._drag_source, QAbstractButton):
                    self._drag_source.setDown(False)
                self._drag_source.setCursor(Qt.ClosedHandCursor)
            self.horizontalScrollBar().setValue(
                self._drag_scroll_origin - delta.x()
            )
            event.accept()
            return True
        if event_type == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            was_dragging = self._drag_active
            source = self._drag_source
            self._drag_source = None
            self._drag_active = False
            if source is not None and was_dragging:
                if isinstance(source, QAbstractButton):
                    source.setDown(False)
                source.unsetCursor()
            if was_dragging:
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta:
            bar = self.horizontalScrollBar()
            scaled_delta = int(delta / 2)
            if scaled_delta == 0:
                scaled_delta = 1 if delta > 0 else -1
            bar.setValue(bar.value() - scaled_delta)
            event.accept()
            return
        super().wheelEvent(event)


def _clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def _asset_catalog(window: Any) -> GameUiAssetCatalog | None:
    context = getattr(window, "app_context", None)
    asset_dir = (
        getattr(getattr(context, "paths", None), "asset_dir", None)
        if context is not None
        else getattr(window, "asset_dir", None)
    )
    if asset_dir is None:
        return None
    return GameUiAssetCatalog(Path(asset_dir) / "game_ui")


def _role_status(state: dict[str, Any]) -> str:
    score = float(state.get(ROLE_TOTAL_SCORE) or 0.0)
    return tr("评分 {score}", score=f"{score:.1f}")


def sorted_equipment_role_states(
    states: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Group visible slots under their character in the top navigator."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    direct_rows: list[tuple[str, dict[str, Any]]] = []
    for _state_key, state in states.items():
        if not isinstance(state, dict):
            continue
        role_name = str(state.get("_role_name") or _state_key)
        if state.get("_game_mode"):
            direct_rows.append((role_name, state))
            continue
        grouped.setdefault(role_name, []).append(state)
    rows = list(direct_rows)
    for role_name, slots in grouped.items():
        slots.sort(key=lambda state: (
            str(state.get("_loadout_slot_key") or "") != "primary",
            str(state.get("_loadout_slot_name") or "").casefold(),
        ))
        summary = dict(slots[0])
        summary["_role_slot_states"] = slots
        summary["_display_name"] = role_name
        summary["_allocation_locked"] = False
        summary[ROLE_LAST_DIFF] = {
            DIFF_CHANGED: any(
                bool((slot.get(ROLE_LAST_DIFF) or {}).get(DIFF_CHANGED))
                for slot in slots
            )
        }
        rows.append((role_name, summary))
    def score(row: tuple[str, dict[str, Any]]) -> float:
        try:
            return float(row[1].get(ROLE_TOTAL_SCORE) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    return sorted(rows, key=lambda row: (-score(row), row[0].casefold()))


def _role_badges(state: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    badges: list[tuple[str, str, str, str]] = []
    if state.get("_game_mode"):
        if state.get("_game_existing_plan_locked"):
            badges.append(("", "对应计算配装已锁定", "#30363d", "lock"))
        if state.get("_game_imported"):
            badges.append(("✓", tr("当前游戏配装已导入"), "#238636", "imported"))
        elif not state.get("_game_importable"):
            badges.append(("!", str(state.get("_game_reason") or tr("游戏配装不完整")), "#9e6a03", "incomplete"))
        else:
            tip = str(state.get("_game_reason") or tr("可导入为计算配装"))
            if state.get("_game_existing_plan_locked"):
                tip += "（需先解除计算配装锁定）"
            badges.append(("↓", tip, "#1f6feb", "importable"))
        return badges
    if state.get("_allocation_locked"):
        badges.append(("", "计算配装已锁定", "#30363d", "lock"))
    if (state.get(ROLE_LAST_DIFF) or {}).get(DIFF_CHANGED):
        badges.append(("Δ", "与上一套计算配装存在变动", "#1f6feb", "changed"))
    return badges


def _apply_role_button_status(
    button: QToolButton,
    role_name: str,
    state: dict[str, Any],
) -> None:
    for badge in button.findChildren(
        QLabel,
        "equipmentRoleStatusBadge",
        Qt.FindDirectChildrenOnly,
    ):
        badge.hide()
        badge.deleteLater()
    badges = _role_badges(state)
    status_text = "、".join(badge[1] for badge in badges) or "暂无特殊状态"
    button.setToolTip(
        tr("{role}\n状态：{status}", role=display_term(role_name), status=status_text)
    )
    for index, badge_data in enumerate(badges):
        badge_text, badge_tip, badge_color, badge_kind = badge_data
        badge = QLabel(badge_text, button)
        badge.setObjectName("equipmentRoleStatusBadge")
        badge.setFixedSize(20, 18)
        badge.move(button.width() - ((index + 1) * (badge.width() + 3)), 3)
        badge.setAlignment(Qt.AlignCenter)
        badge.setToolTip(badge_tip)
        badge.setProperty("badgeKind", badge_kind)
        badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        badge.setStyleSheet(
            f"background:{badge_color};color:#ffffff;border:none;"
            "border-radius:8px;font-size:10px;font-weight:800"
        )
        if badge_kind == "lock":
            badge.setPixmap(_allocation_lock_icon(True).pixmap(18, 18))
        badge.show()
        badge.raise_()


def update_equipment_role_status(
    window: Any,
    role_name: str,
    **changes: Any,
) -> None:
    """Update one role navigator entry without rebuilding its detail panel."""

    state = (getattr(window, "_equip_role_states", {}) or {}).get(role_name)
    if isinstance(state, dict):
        state.update(changes)
    saved_state = (getattr(window, "_saved_equipment_states", {}) or {}).get(
        role_name
    )
    if isinstance(saved_state, dict):
        saved_state.update(changes)
    button = (getattr(window, "_equip_role_buttons", {}) or {}).get(role_name)
    if button is not None and isinstance(state, dict):
        _apply_role_button_status(
            button,
            str(state.get("_display_name") or role_name),
            state,
        )


def capture_equipment_navigation_state(window: Any) -> None:
    """Remember the selected role and its detail-scroll position before a refresh."""

    mode = getattr(window, "_equipment_mode", "saved")
    selected_by_mode = getattr(window, "_equip_selected_role_by_mode", {})
    if not isinstance(selected_by_mode, dict):
        return
    role_name = selected_by_mode.get(mode)
    if not role_name:
        return
    detail_scroll = getattr(window, "equip_scroll", None)
    try:
        detail_value = (
            detail_scroll.verticalScrollBar().value()
            if detail_scroll is not None
            else 0
        )
    except RuntimeError:
        return
    states = getattr(window, "_equip_navigation_state_by_mode", None)
    if not isinstance(states, dict):
        states = {}
        window._equip_navigation_state_by_mode = states
    states[mode] = {
        "role_name": str(role_name),
        "detail_scroll_value": int(detail_value),
    }


def _restore_equipment_navigation(
    window: Any,
    role_name: str,
    *,
    detail_scroll_value: int | None,
) -> None:
    """Restore after Qt has calculated the rebuilt navigator geometry."""

    def restore(attempt: int = 0) -> None:
        selected_by_mode = getattr(window, "_equip_selected_role_by_mode", {})
        mode = getattr(window, "_equipment_mode", "saved")
        if not isinstance(selected_by_mode, dict) or selected_by_mode.get(mode) != role_name:
            return
        button = (getattr(window, "_equip_role_buttons", {}) or {}).get(role_name)
        if button is not None:
            window.equip_role_scroll.ensureWidgetVisible(button, 12, 0)
        if detail_scroll_value is not None:
            bar = window.equip_scroll.verticalScrollBar()
            bar.setValue(min(max(0, detail_scroll_value), bar.maximum()))
            # Detail cards can report their final height after the first event
            # loop turn. Retry a few times so a restored position is not
            # clamped to zero while the new layout is still empty.
            if attempt < 4 and bar.maximum() < detail_scroll_value:
                QTimer.singleShot(20, lambda: restore(attempt + 1))
                return

    # The role strip's content width is only reliable after the first layout pass.
    QTimer.singleShot(0, restore)


def build_equipment_master_detail(window: Any, root: QVBoxLayout) -> None:
    capture_equipment_navigation_state(window)
    role_panel = QFrame()
    role_panel.setObjectName("equipmentRoleNavigator")
    role_panel.setFixedHeight(70)
    role_panel.setStyleSheet(
        themed_style(
            "QFrame#equipmentRoleNavigator{background:#0d1117;"
            "border:1px solid #30363d;border-radius:8px}"
        )
    )
    role_panel_layout = QVBoxLayout(role_panel)
    role_panel_layout.setContentsMargins(8, 4, 8, 4)
    role_panel_layout.setSpacing(0)

    role_scroll = _HorizontalRoleScrollArea(role_panel)
    role_scroll.setWidgetResizable(True)
    role_scroll.setFrameShape(QFrame.NoFrame)
    role_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    role_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    role_scroll.setFixedHeight(62)
    role_scroll.setStyleSheet(themed_style("QScrollArea{background:transparent;border:none}"))
    role_content = QWidget(role_scroll)
    window.equip_role_layout = QHBoxLayout(role_content)
    window.equip_role_layout.setSizeConstraint(QLayout.SetMinAndMaxSize)
    window.equip_role_layout.setContentsMargins(0, 0, 0, 0)
    window.equip_role_layout.setSpacing(6)
    window.equip_role_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    role_scroll.setWidget(role_content)
    role_scroll.enable_drag_scroll(role_scroll.viewport())
    role_scroll.enable_drag_scroll(role_content)
    role_panel_layout.addWidget(role_scroll)

    detail_scroll = QScrollArea()
    detail_scroll.setWidgetResizable(True)
    detail_scroll.setFrameShape(QFrame.NoFrame)
    window.equip_scroll = detail_scroll
    window.equip_content = QWidget(detail_scroll)
    window.equip_content_layout = QVBoxLayout(window.equip_content)
    window.equip_content_layout.setContentsMargins(8, 0, 4, 0)
    window.equip_content_layout.setSpacing(8)
    detail_scroll.setWidget(window.equip_content)

    window.equip_role_scroll = role_scroll
    window.equip_role_content = role_content
    window.equip_role_strip = role_panel
    window._equip_role_buttons = {}
    window._equip_role_states = {}
    # The page can be recreated by a shell refresh.  Role-button widgets are
    # disposable, but the selected role is page state and must survive until
    # an account reset explicitly clears it.
    if not isinstance(getattr(window, "_equip_selected_role_by_mode", None), dict):
        window._equip_selected_role_by_mode = {}
    root.addWidget(role_panel)
    root.addWidget(detail_scroll, 1)


def clear_equipment_master_detail(window: Any) -> None:
    detail_layout = getattr(window, "equip_content_layout", None)
    if detail_layout is not None:
        _clear_layout(detail_layout)
    role_layout = getattr(window, "equip_role_layout", None)
    if role_layout is not None:
        _clear_layout(role_layout)
    window._equip_role_buttons = {}


def _empty_detail(window: Any, message: str) -> None:
    _clear_layout(window.equip_content_layout)
    label = QLabel(message)
    label.setAlignment(Qt.AlignCenter)
    label.setWordWrap(True)
    label.setStyleSheet(themed_style("color:#6e7681;padding:28px"))
    window.equip_content_layout.addWidget(label)
    window.equip_content_layout.addStretch(1)


def select_equipment_role(
    window: Any,
    role_name: str,
    *,
    restore_detail_scroll_value: int | None = None,
) -> None:
    state = (getattr(window, "_equip_role_states", {}) or {}).get(role_name)
    if not isinstance(state, dict):
        return
    mode = getattr(window, "_equipment_mode", "saved")
    window._equip_selected_role_by_mode[mode] = role_name
    for name, button in getattr(window, "_equip_role_buttons", {}).items():
        button.setChecked(name == role_name)
    _clear_layout(window.equip_content_layout)
    _render_equip_role(
        window,
        role_name,
        state,
        target_layout=window.equip_content_layout,
    )
    window.equip_content_layout.addStretch(1)
    if restore_detail_scroll_value is None:
        window.equip_scroll.verticalScrollBar().setValue(0)
    _restore_equipment_navigation(
        window,
        role_name,
        detail_scroll_value=restore_detail_scroll_value,
    )


def show_equipment_master_detail(
    window: Any,
    roles: list[tuple[str, dict[str, Any]]],
    *,
    empty_message: str,
) -> None:
    _clear_layout(window.equip_role_layout)
    window._equip_role_states = dict(roles)
    window._equip_role_buttons = {}
    if not roles:
        _empty_detail(window, empty_message)
        return

    catalog = _asset_catalog(window)
    group = QButtonGroup(window.equip_role_strip)
    group.setExclusive(True)
    window._equip_role_button_group = group
    for role_name, state in roles:
        button = QToolButton()
        button.setCheckable(True)
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(42, 42))
        button.setFixedSize(148, 60)
        button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        display_name = display_term(str(state.get("_display_name") or role_name))
        button.setText(f"{display_name}\n{_role_status(state)}")
        character_id = state.get("_character_id")
        icon_path = (
            catalog.character_icon(int(character_id))
            if catalog is not None and character_id is not None
            else None
        )
        if icon_path is not None:
            button.setIcon(QIcon(str(icon_path)))
        slot_id = state.get("_loadout_slot_id")
        if slot_id is not None:
            manage_button = QToolButton(button)
            manage_button.setToolTip(tr("管理该角色的配装槽位"))
            manage_button.setFixedSize(20, 20)
            manage_button.move(124, 36)
            manage_button.setStyleSheet(
                "QToolButton{border:2px solid #35dc83;border-radius:10px;"
                "background:#0d1117;padding:0}"
                "QToolButton:hover{border-color:#82f5b5;background:#123525}"
            )
            inner_ring = QFrame(manage_button)
            inner_ring.setAttribute(Qt.WA_TransparentForMouseEvents)
            inner_ring.setFixedSize(12, 12)
            inner_ring.move(4, 4)
            inner_ring.setStyleSheet(
                "QFrame{border:2px solid #35dc83;border-radius:6px;background:transparent}"
            )
            manage_button.clicked.connect(
                lambda _checked=False, slot_id=int(slot_id), role_name=role_name: window._manage_loadout_slot(
                    slot_id,
                    role_name=role_name,
                )
            )
            manage_button.show()
        button.setStyleSheet(
            themed_style(
                "QToolButton{background:#161b22;color:#c9d1d9;"
                "border:1px solid #30363d;border-radius:7px;padding:5px;"
                "font-size:12px;text-align:left}"
                "QToolButton:hover{border-color:#58a6ff;background:#1f6feb22}"
                "QToolButton:checked{border:2px solid #58a6ff;"
                "background:#1f6feb;color:#ffffff;font-weight:700}"
            )
        )
        button.clicked.connect(
            lambda _checked=False, name=role_name: select_equipment_role(
                window,
                name,
            )
        )
        group.addButton(button)
        window.equip_role_layout.addWidget(button)
        window._equip_role_buttons[role_name] = button
        window.equip_role_scroll.enable_drag_scroll(button)
        _apply_role_button_status(button, display_name, state)
    window.equip_role_layout.addStretch(1)
    window.equip_role_content.adjustSize()

    mode = getattr(window, "_equipment_mode", "saved")
    preferred = getattr(window, "_equip_pending_role_name", None)
    remembered = window._equip_selected_role_by_mode.get(mode)
    available = window._equip_role_states
    selected = str(
        preferred if preferred in available
        else remembered if remembered in available
        else roles[0][0]
    )
    navigation_states = getattr(window, "_equip_navigation_state_by_mode", {})
    navigation_state = (
        navigation_states.pop(mode, None)
        if isinstance(navigation_states, dict)
        else None
    )
    restore_detail_scroll_value = (
        int(navigation_state.get("detail_scroll_value") or 0)
        if isinstance(navigation_state, dict)
        and navigation_state.get("role_name") == selected
        else None
    )
    window._equip_pending_role_name = None
    select_equipment_role(
        window,
        selected,
        restore_detail_scroll_value=restore_detail_scroll_value,
    )


def filter_equipment_master_detail(window: Any) -> None:
    states = (
        getattr(window, "_game_loadout_states", {})
        if getattr(window, "_equipment_mode", "saved") == "game"
        else getattr(window, "_saved_equipment_states", {})
    ) or {}
    text = window.equip_search.text().strip()
    roles = [
        (name, state)
        for name, state in sorted_equipment_role_states(states)
        if not text or match_pinyin(name, text)
    ]
    empty_message = (
        tr("没有匹配的游戏内角色。")
        if getattr(window, "_equipment_mode", "saved") == "game"
        else tr("没有匹配的已配装角色。")
    )
    show_equipment_master_detail(window, roles, empty_message=empty_message)
