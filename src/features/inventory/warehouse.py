# 提供仓库页的 SQLite 快照投影、筛选和虚拟化卡片视图。
"""Warehouse inventory view primitives.

The page deliberately uses a model/delegate ``QListView`` instead of creating
one QWidget per item.  Qt therefore paints only the cards visible in the
viewport, which keeps a 2,000-item inventory responsive.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate

from src.app import runtime
from src.app.theme import theme_color
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


_STAT_LABELS = {
    "AtkAdd": "攻击力", "AtkUp": "攻击力%", "CritBase": "暴击率%",
    "CritDamageBase": "暴击伤害%", "DamageUpGeneralBase": "伤害增加%",
    "DefAdd": "防御力", "DefUp": "防御力%", "HPMaxAdd": "生命值",
    "HPMaxUp": "生命值%", "HealUp": "治疗加成", "MagBase": "环合强度",
    "UnbalIntensityBase": "倾陷强度",
}
_SHAPE_LABELS = {
    "hen2": "H_2", "hen3": "H_3", "hen4": "H_4", "shu2": "V_2",
    "shu3": "V_3", "shu4": "V_4", "z3": "Trap_4_H", "z4": "Trap_4_V",
    "zhijiao1": "L_3_BL", "zhijiao2": "L_3_TL", "zhijiao3": "L_3_TR",
    "zhijiao4": "L_3_BR",
}
_QUALITY_META = {
    "gold": ("金色", "#e3a23b"),
    "purple": ("紫色", "#b07bea"),
    "blue": ("蓝色", "#5d9be8"),
    "green": ("绿色", "#54b86b"),
}
_ROLE_AVATAR_ALIASES = {
    "零": "主角",
    "「零」": "主角",
}


@lru_cache(maxsize=96)
def _legacy_character_avatar(character_name: str) -> QPixmap:
    """Use the shipped config/templates/roles portrait, tolerating decorative aliases."""
    if not character_name:
        return QPixmap()
    avatar_name = _ROLE_AVATAR_ALIASES.get(character_name, character_name)
    normalized_name = _normalize_role_avatar_name(avatar_name)
    for root in _template_root_candidates():
        role_root = root / "roles"
        direct_path = role_root / f"{avatar_name}.png"
        if direct_path.is_file():
            return QPixmap(str(direct_path))
        candidates = [
            path for path in role_root.glob("*.png")
            if _normalize_role_avatar_name(path.stem) == normalized_name
        ]
        if len(candidates) == 1:
            return QPixmap(str(candidates[0]))
        fuzzy_candidates = [
            path for path in role_root.glob("*.png")
            if normalized_name and (
                _normalize_role_avatar_name(path.stem).startswith(normalized_name)
                or normalized_name.startswith(_normalize_role_avatar_name(path.stem))
            )
        ]
        if len(fuzzy_candidates) == 1:
            return QPixmap(str(fuzzy_candidates[0]))
    return QPixmap()


def _normalize_role_avatar_name(value: Any) -> str:
    """Normalize display names such as 「零」 and template names such as 零（男主）."""
    text = str(value or "").strip()
    text = text.strip("「」【】[]")
    text = re.sub(r"[（(].*?[）)]", "", text)
    return re.sub(r"\s+", "", text).casefold()


def _localized(value: Any, fallback: str) -> str:
    if isinstance(value, Mapping):
        for key in ("zh_cn", "zh-CN", "zh", "cn"):
            text = value.get(key)
            if text:
                return str(text)
        for text in value.values():
            if text:
                return str(text)
    return fallback


def _display_suit_name(value: str) -> str:
    """Remove only the decorative full-width title brackets from card tape names."""
    text = str(value or "").strip()
    pairs = (("「", "」"), ("【", "】"), ("[", "]"))
    for left, right in pairs:
        if text.startswith(left) and text.endswith(right) and len(text) > len(left) + len(right):
            return text[len(left):-len(right)].strip()
    return text


def _quality_key(value: Any) -> str:
    text = str(value or "").casefold()
    if "gold" in text or "golden" in text or "orange" in text or "金" in text or "橙" in text:
        return "gold"
    if "purple" in text or "紫" in text:
        return "purple"
    if "blue" in text or "蓝" in text:
        return "blue"
    if "green" in text or "绿" in text:
        return "green"
    return text or "unknown"


def _shape_label(geometry: Any) -> str:
    value = str(geometry or "").removeprefix("EquipmentGeometry_").casefold()
    return _SHAPE_LABELS.get(value, str(geometry or "未知形状"))


def _drive_type_label(shape: str) -> str:
    """Map official grid geometry to the player-facing drive type label."""
    number = next((char for char in reversed(shape) if char.isdigit()), "")
    return {"2": "II型驱动", "3": "III型驱动", "4": "IV型驱动"}.get(number, "驱动")


def _format_stat(stat: Mapping[str, Any]) -> str:
    view = _stat_view(stat)
    return f"{view['label']}  {view['value']}"


def _stat_view(stat: Mapping[str, Any], *, main: bool = False) -> dict[str, Any]:
    """Prepare one stat for aligned card rendering while keeping its original value."""
    label = _localized(stat.get("names"), _STAT_LABELS.get(str(stat.get("property_id") or ""), str(stat.get("property_id") or "未知属性")))
    value = float(stat.get("value", 0.0) or 0.0)
    if stat.get("percent"):
        value_text = f"+{value * 100:g}%"
    else:
        value_text = f"+{value:g}"
    return {"label": label, "value": value_text, "main": main}


def _template_root_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    for source in (
        getattr(runtime, "TEMPLATE_DIR", None),
        Path(getattr(runtime, "BUNDLED_CONFIG_DIR", Path("config"))) / "templates",
        Path("config") / "templates",
    ):
        if isinstance(source, Path) and source not in candidates:
            candidates.append(source)
    return tuple(candidates)


@lru_cache(maxsize=48)
def _equipment_placeholder(shape: str, quality: str) -> QPixmap:
    """Use existing bundled template art as a small temporary card thumbnail."""
    quality_name = {"gold": "Gold", "purple": "Purple", "blue": "Blue"}.get(quality, "Gold")
    for root in _template_root_candidates():
        for filename in (f"{shape}_{quality_name}.png", f"{shape}.png", f"H_3_{quality_name}.png"):
            path = root / filename
            if path.is_file():
                return QPixmap(str(path))
    return QPixmap()


@lru_cache(maxsize=4)
def _asset_catalog(asset_root: str) -> GameUiAssetCatalog:
    return GameUiAssetCatalog(asset_root)


def _equipment_item_icon(
    kind: str,
    item_id: Any,
    *,
    asset_root: str | Path | None = None,
) -> Path | None:
    item_id = str(item_id or "")
    if not item_id:
        return None
    runtime_root = getattr(runtime, "ASSET_DIR", None)
    root = (
        Path(asset_root)
        if asset_root is not None
        else Path(runtime_root) / "game_ui"
        if runtime_root is not None
        else Path.cwd() / "assets" / "game_ui"
    )
    return _asset_catalog(str(root.expanduser().resolve())).inventory_item_icon(kind, item_id)


@lru_cache(maxsize=256)
def _equipment_item_pixmap(path_text: str) -> QPixmap:
    path = Path(path_text)
    return QPixmap(str(path)) if path.is_file() else QPixmap()


def warehouse_item_view(
    row: Mapping[str, Any],
    *,
    source: str = "nte_core",
    asset_root: str | Path | None = None,
) -> dict[str, Any]:
    """Turn one official SQLite item into the compact card data used by the view."""
    source = str(source or "")
    level_known = source != "gamepad"
    state_known = source != "gamepad"
    kind = str(row.get("kind") or "")
    quality_key = _quality_key(row.get("quality"))
    quality_label, quality_color = _QUALITY_META.get(quality_key, (str(row.get("quality") or "未知"), "#8b949e"))
    item_name = _localized(row.get("names"), str(row.get("item_id") or "未知装备"))
    suit_name = _display_suit_name(_localized(row.get("suit_names"), str(row.get("suit_id") or "未识别套装")))
    main_stat_rows = [_stat_view(stat, main=True) for stat in row.get("main_stats") or [] if isinstance(stat, Mapping)]
    sub_stat_rows = [_stat_view(stat) for stat in row.get("sub_stats") or [] if isinstance(stat, Mapping)]
    stats = [f"{stat['label']}  {stat['value']}" for stat in [*main_stat_rows, *sub_stat_rows]]
    kind_label = "驱动" if kind == "module" else "卡带" if kind == "core" else kind or "未知"
    shape = _shape_label(row.get("geometry")) if kind == "module" else "卡带"
    title = _drive_type_label(shape) if kind == "module" else "卡带"
    display_name = title if kind == "module" else suit_name
    state_row = dict(row)
    state_row["state_known"] = state_known
    tags = _state_tags(state_row)
    equipped_character_name = str(row.get("equipped_character_name") or "")
    search_text = " ".join((
        item_name, suit_name, title, equipped_character_name, *stats, *[tag[0] for tag in tags],
    )).casefold()
    return {
        "kind": kind,
        "kind_label": kind_label,
        "quality": quality_key,
        "quality_label": quality_label,
        "quality_color": quality_color,
        "item_name": item_name,
        "item_icon_path": _equipment_item_icon(
            kind,
            row.get("item_id"),
            asset_root=asset_root,
        ),
        "suit_name": suit_name,
        "display_name": display_name,
        "title": title,
        "shape": shape if kind == "module" else "H_3",
        "source": source,
        "level": int(row.get("level", 0) or 0),
        "max_level": int(row.get("max_level", 0) or 0),
        "level_known": level_known,
        "state_known": state_known,
        "stats": stats,
        "main_stats": main_stat_rows,
        "sub_stats": sub_stat_rows,
        "tags": tags,
        "equipped": bool(row.get("equipped")),
        "equipped_character_id": row.get("equipped_character_id"),
        "equipped_character_name": equipped_character_name,
        "locked": bool(row.get("locked")),
        "discarded": bool(row.get("discarded")),
        "search_text": search_text,
        "uid": f"nte-{'module' if kind == 'module' else 'core'}-{row.get('uid_slot', '')}-{row.get('uid_serial', '')}",
    }


def _state_tags(row: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Return the card badges for an item's current, possibly unsaved state."""
    tags: list[tuple[str, str]] = []
    if not row.get("state_known", True):
        return [("状态未知", "#8b949e")]
    if row.get("discarded"):
        tags.append(("已弃置", "#f85149"))
    if row.get("locked"):
        tags.append(("已锁定", "#d29922"))
    if row.get("equipped"):
        tags.append(("已装备", "#58a6ff"))
    return tags


def warehouse_item_with_state(item: Mapping[str, Any], target_state: str) -> dict[str, Any]:
    """Apply a local state edit to one projected card without touching SQLite."""
    if target_state not in {"normal", "locked", "discarded"}:
        raise ValueError(f"未知仓库状态：{target_state}")
    if not item.get("state_known", True):
        raise ValueError("当前库存来源无法读取或修改锁定、弃置状态")
    updated = dict(item)
    updated["discarded"] = target_state == "discarded"
    updated["locked"] = target_state == "locked"
    updated["tags"] = _state_tags(updated)
    updated["search_text"] = " ".join(
        (
            str(updated.get("item_name") or ""),
            str(updated.get("suit_name") or ""),
            str(updated.get("title") or ""),
            str(updated.get("equipped_character_name") or ""),
            *[str(value) for value in updated.get("stats") or []],
            *[tag for tag, _color in updated["tags"]],
        )
    ).casefold()
    return updated


def warehouse_item_type_key(item: Mapping[str, Any]) -> str:
    """Return the type-filter key: drive model for modules, suit name for cards."""
    kind = str(item.get("kind") or "")
    label = str(item.get("title") if kind == "module" else item.get("suit_name") or "")
    return f"{kind}:{label}"


def warehouse_item_compare_category(item: Mapping[str, Any]) -> str:
    """Return the broad comparison category; modules and cards are incompatible."""
    return str(item.get("kind") or "")


def warehouse_type_options(items: Iterable[Mapping[str, Any]], category: str = "all") -> list[tuple[str, str]]:
    """Build a compact linked type list from the already loaded snapshot."""
    options: dict[str, str] = {}
    for item in items:
        kind = str(item.get("kind") or "")
        if category != "all" and kind != category:
            continue
        key = warehouse_item_type_key(item)
        label = str(item.get("title") if kind == "module" else item.get("suit_name") or "未知类型")
        options[key] = label
    return sorted(options.items(), key=lambda pair: (pair[1], pair[0]))


def load_warehouse_snapshot(database_path: str | Path) -> dict[str, Any]:
    """Read one immutable current snapshot in a worker thread."""
    path = Path(database_path)
    if not path.is_file():
        return {"snapshot_id": None, "items": []}
    with UserDataDao(path) as dao, StaticGameDataDao() as static_dao:
        snapshot_id = dao.current_inventory_snapshot_id()
        if snapshot_id is None:
            return {"snapshot_id": None, "items": []}
        summary = dao.inventory_snapshot_summary(snapshot_id) or {}
        source = str(summary.get("source") or "")
        rows = dao.list_inventory_items(snapshot_id)
        character_names = {
            int(character["character_id"]): str(character.get("name_zh") or "")
            for character in static_dao.list_characters()
            if character.get("character_id") is not None
        }
    for row in rows:
        character_id = row.get("equipped_character_id")
        if isinstance(character_id, int):
            row["equipped_character_name"] = character_names.get(character_id, "")
    return {
        "snapshot_id": snapshot_id,
        "source": source,
        "items": [warehouse_item_view(row, source=source) for row in rows],
    }


def filter_warehouse_items(
    items: Iterable[Mapping[str, Any]], *, search: str = "", kind: str = "all",
    quality: str = "all", status: str = "all", character_id: int | None = None, item_type: str = "all",
) -> list[dict[str, Any]]:
    """Filter already projected cards; this is intentionally inexpensive for 2k rows."""
    needle = str(search or "").strip().casefold()
    result: list[dict[str, Any]] = []
    for source in items:
        item = dict(source)
        if kind != "all" and item.get("kind") != kind:
            continue
        if quality != "all" and item.get("quality") != quality:
            continue
        if character_id is not None and item.get("equipped_character_id") != character_id:
            continue
        if item_type != "all" and warehouse_item_type_key(item) != item_type:
            continue
        if status != "all" and not item.get("state_known", True):
            continue
        if status == "equipped" and not item.get("equipped"):
            continue
        if status == "locked" and not item.get("locked"):
            continue
        if status == "discarded" and not item.get("discarded"):
            continue
        if status == "unequipped" and item.get("equipped"):
            continue
        if needle and needle not in str(item.get("search_text") or ""):
            continue
        result.append(item)
    return result


class WarehouseInventoryModel(QAbstractListModel):
    """A lightweight data model; no card widgets are created for inventory rows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[dict[str, Any]] = []

    def set_items(self, items: Iterable[Mapping[str, Any]]) -> None:
        self.beginResetModel()
        self._items = [dict(item) for item in items]
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        if role == Qt.UserRole:
            return item
        if role == Qt.DisplayRole:
            return f"{item['title']}\n{item['item_name']}\n{item['suit_name']}"
        return None


class WarehouseGridView(QListView):
    """Icon view that always divides the available row width into four cards."""

    COLUMN_COUNT = 4

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = max(1, (self.viewport().width() - 16) // self.COLUMN_COUNT)
        self.setGridSize(QSize(width, WarehouseCardDelegate.CARD_SIZE.height()))

    def wheelEvent(self, event) -> None:
        """Keep mouse-wheel movement below one page while still traversing a large inventory quickly."""
        if event.pixelDelta().y() or not event.angleDelta().y():
            return super().wheelEvent(event)
        page_step = max(1, int(self.viewport().height() * 0.40))
        notches = event.angleDelta().y() / 120.0
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(round(scrollbar.value() - page_step * notches))
        event.accept()


class WarehouseCardDelegate(QStyledItemDelegate):
    """Paint compact inventory cards directly, allowing QListView to virtualize them."""

    state_toggle_requested = Signal(QModelIndex, str)
    identify_requested = Signal(QModelIndex)
    compare_requested = Signal(QModelIndex)

    CARD_SIZE = QSize(228, 260)

    def sizeHint(self, _option, _index):
        return self.CARD_SIZE

    @staticmethod
    def _text(painter: QPainter, rect: QRect, text: str, color: str, size: int, *, bold: bool = False) -> None:
        font = QFont(painter.font())
        font.setPointSize(size)
        font.setBold(bold)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(rect, Qt.AlignVCenter | Qt.TextSingleLine, text)

    @staticmethod
    def _stat_row(painter: QPainter, rect: QRect, stat: Mapping[str, Any], *, main: bool = False) -> None:
        label_color = theme_color("#f0f6fc" if main else "#8b949e")
        value_color = theme_color("#f0f6fc" if main else "#c9d1d9")
        if main:
            painter.fillRect(rect, QColor(theme_color("#21262d")))
            painter.fillRect(QRect(rect.left(), rect.top() + 2, 2, rect.height() - 4), QColor("#58a6ff"))
        font = QFont(painter.font())
        font.setPointSize(10)
        font.setBold(main)
        painter.setFont(font)
        value_rect = QRect(rect.right() - 62, rect.top(), 58, rect.height())
        label_rect = QRect(rect.left() + 6, rect.top(), value_rect.left() - rect.left() - 12, rect.height())
        painter.setPen(QColor(label_color))
        painter.drawText(label_rect, Qt.AlignVCenter | Qt.AlignLeft | Qt.TextSingleLine, str(stat.get("label") or ""))
        painter.setPen(QColor(value_color))
        painter.drawText(value_rect, Qt.AlignVCenter | Qt.AlignRight | Qt.TextSingleLine, str(stat.get("value") or ""))

    @staticmethod
    def _button_rects(rect: QRect) -> tuple[QRect, QRect, QRect, QRect, QRect]:
        top = rect.top() + 9
        lock = QRect(rect.right() - 29, top, 20, 20)
        discard = QRect(lock.left() - 24, top, 20, 20)
        inspect = QRect(rect.right() - 29, rect.bottom() - 29, 20, 20)
        compare = QRect(inspect.left() - 28, inspect.top(), 24, 20)
        avatar = QRect(rect.right() - 64, lock.bottom() + 5, 42, 42)
        return compare, inspect, discard, lock, avatar

    @staticmethod
    def _paint_action_button(painter: QPainter, rect: QRect, *, action: str, active: bool, available: bool = True) -> None:
        background = "#5b2026" if action == "discard" and active else "#3a2f13" if active else "#172a45" if action == "inspect" else "#21262d"
        foreground = "#ff7b72" if action == "discard" and active else "#e3b341" if active else "#79c0ff" if action == "inspect" else "#8b949e"
        if not available:
            background, foreground = "#1b2027", "#484f58"
        painter.setBrush(QColor(theme_color(background)))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(QPen(QColor(theme_color(foreground)), 1.5))
        if action == "discard":
            can = rect.adjusted(6, 8, -6, -4)
            painter.drawRect(can)
            painter.drawLine(can.left() - 2, can.top() - 3, can.right() + 2, can.top() - 3)
            painter.drawLine(rect.center().x() - 3, can.top() - 5, rect.center().x() + 3, can.top() - 5)
            painter.drawLine(can.left() + 3, can.top() + 2, can.left() + 3, can.bottom() - 2)
            painter.drawLine(can.right() - 3, can.top() + 2, can.right() - 3, can.bottom() - 2)
        elif action == "lock":
            body = rect.adjusted(5, 10, -5, -4)
            painter.drawRoundedRect(body, 2, 2)
            shackle = QRect(body.left() + 2, body.top() - 7, body.width() - 4, 11)
            painter.drawArc(shackle, 0, 180 * 16)
            painter.drawPoint(body.center().x(), body.center().y() + 1)
        elif action == "inspect":
            lens = rect.adjusted(5, 5, -8, -8)
            painter.drawEllipse(lens)
            painter.drawLine(lens.right() - 1, lens.bottom() - 1, rect.right() - 5, rect.bottom() - 5)
        else:
            font = QFont(painter.font())
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignCenter, "VS")

    def editorEvent(self, event, model, option, index):
        if event.type() != QEvent.MouseButtonRelease or event.button() != Qt.LeftButton:
            return super().editorEvent(event, model, option, index)
        item = index.data(Qt.UserRole)
        if not isinstance(item, Mapping):
            return False
        compare_rect, inspect_rect, discard_rect, lock_rect, _avatar_rect = self._button_rects(option.rect.adjusted(4, 4, -4, -4))
        position = event.position().toPoint()
        if compare_rect.contains(position):
            self.compare_requested.emit(index)
            return True
        if inspect_rect.contains(position):
            self.identify_requested.emit(index)
            return True
        if discard_rect.contains(position) and item.get("state_known", True):
            self.state_toggle_requested.emit(index, "normal" if item.get("discarded") else "discarded")
            return True
        if lock_rect.contains(position) and item.get("state_known", True):
            self.state_toggle_requested.emit(index, "normal" if item.get("locked") else "locked")
            return True
        return super().editorEvent(event, model, option, index)

    def paint(self, painter: QPainter, option, index) -> None:
        item = index.data(Qt.UserRole)
        if not isinstance(item, Mapping):
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(4, 4, -4, -4)
        selected = bool(option.state & QStyle.State_Selected)
        background = theme_color("#1c2128" if selected else "#161b22")
        border = theme_color("#58a6ff" if selected else "#30363d")
        painter.setBrush(QColor(background))
        painter.setPen(QPen(QColor(border), 1))
        painter.drawRoundedRect(rect, 9, 9)

        left, top, width = rect.left() + 12, rect.top() + 10, rect.width() - 24
        quality_color = str(item.get("quality_color") or "#8b949e")
        icon_rect = QRect(left, top, 44, 44)
        placeholder = _equipment_item_pixmap(str(item.get("item_icon_path") or ""))
        if placeholder.isNull():
            placeholder = _equipment_placeholder(
                str(item.get("shape") or "H_3"),
                str(item.get("quality") or "gold"),
            )
        if not placeholder.isNull():
            painter.drawPixmap(icon_rect, placeholder)
        else:
            painter.setBrush(QColor(quality_color))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(icon_rect.adjusted(8, 8, -8, -8))
        compare_rect, inspect_rect, discard_rect, lock_rect, avatar_rect = self._button_rects(rect)
        self._paint_action_button(painter, compare_rect, action="compare", active=False)
        self._paint_action_button(painter, inspect_rect, action="inspect", active=False)
        state_known = bool(item.get("state_known", True))
        self._paint_action_button(painter, discard_rect, action="discard", active=bool(item.get("discarded")), available=state_known)
        self._paint_action_button(painter, lock_rect, action="lock", active=bool(item.get("locked")), available=state_known)
        name_size = 10 if item.get("kind") == "core" else 11
        self._text(painter, QRect(left + 52, top + 2, width - 112, 20), str(item.get("display_name") or item.get("item_name") or ""), theme_color("#f0f6fc"), name_size, bold=True)
        if item.get("level_known", True):
            level = f"Lv.{item.get('level', 0)}"
            max_level = int(item.get("max_level", 0) or 0)
            if max_level:
                level += f" / {max_level}"
        else:
            level = "未知等级"
        self._text(painter, QRect(left + 52, top + 23, width - 112, 16), level, quality_color, 9)
        if item.get("equipped"):
            avatar = _legacy_character_avatar(str(item.get("equipped_character_name") or ""))
            if not avatar.isNull():
                painter.drawPixmap(avatar_rect, avatar)
            else:
                self._text(painter, avatar_rect, "已装", theme_color("#58a6ff"), 8, bold=True)
        content_top = top + 70
        stats = [*(item.get("main_stats") or []), *(item.get("sub_stats") or [])]
        if not stats:
            self._text(painter, QRect(left, content_top + 7, width, 18), "暂无词条数据", theme_color("#6e7681"), 10)
        else:
            for number, stat in enumerate(stats[:6]):
                row_rect = QRect(left, content_top + number * 23, width, 21)
                self._stat_row(painter, row_rect, stat, main=bool(stat.get("main")))
        painter.restore()
