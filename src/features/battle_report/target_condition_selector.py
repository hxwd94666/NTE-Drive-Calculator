# 战报目标、环境、争锋加成与魔女赐福选择器。
"""Static-catalog-driven target condition editor."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.domain.battle_report import BattleTargetCondition
from src.ui.widgets import NoWheelComboBox


_DAMAGE_TYPES = (
    "normal", "chaos", "cosmos", "incantation", "lakshana",
    "nature", "psyche", "psychically",
)


class BattleTargetConditionSelector(QGroupBox):
    preset_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("目标与外部环境", parent)
        self._catalog: dict[str, Any] = {}
        self._loading = False
        self._feast_option_combos: dict[str, NoWheelComboBox] = {}
        root = QVBoxLayout(self)
        top = QGridLayout()
        top.addWidget(QLabel("战斗环境"), 0, 0)
        self.environment_combo = NoWheelComboBox()
        self.environment_combo.addItem("大世界", "open_world")
        self.environment_combo.addItem("材料 / 养成副本", "clone")
        self.environment_combo.addItem("轨外之境", "outer_realm")
        self.environment_combo.addItem("争锋赏宴", "feast")
        top.addWidget(self.environment_combo, 0, 1)
        top.addWidget(QLabel("魔女赐福"), 0, 2)
        self.witch_combo = NoWheelComboBox()
        top.addWidget(self.witch_combo, 0, 3)
        root.addLayout(top)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)
        self._build_open_world_page()
        self._build_clone_page()
        self._build_outer_realm_page()
        self._build_feast_page()
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)
        self.environment_combo.currentIndexChanged.connect(self._environment_changed)
        self.witch_combo.currentIndexChanged.connect(self._emit_preset)

    def _build_open_world_page(self) -> None:
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("对象（可多选）"), 0, 0)
        self.open_world_list = QListWidget()
        self.open_world_list.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.open_world_list.setMinimumHeight(120)
        layout.addWidget(self.open_world_list, 1, 0, 1, 4)
        layout.addWidget(QLabel("当前计算对象"), 2, 0)
        self.open_world_primary = NoWheelComboBox()
        layout.addWidget(self.open_world_primary, 2, 1, 1, 3)
        layout.addWidget(QLabel("世界等级 / 属性档"), 3, 0)
        self.open_world_variant = NoWheelComboBox()
        layout.addWidget(self.open_world_variant, 3, 1, 1, 3)
        self.open_world_list.itemChanged.connect(
            self._open_world_selection_changed
        )
        self.open_world_primary.currentIndexChanged.connect(
            self._open_world_primary_changed
        )
        self.open_world_variant.currentIndexChanged.connect(self._emit_preset)
        self.stack.addWidget(page)

    def _build_clone_page(self) -> None:
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("副本类目"), 0, 0)
        self.clone_category_combo = NoWheelComboBox()
        layout.addWidget(self.clone_category_combo, 0, 1)
        layout.addWidget(QLabel("副本"), 0, 2)
        self.clone_activity_combo = NoWheelComboBox()
        layout.addWidget(self.clone_activity_combo, 0, 3)
        layout.addWidget(QLabel("难度"), 1, 0)
        self.clone_difficulty_combo = NoWheelComboBox()
        layout.addWidget(self.clone_difficulty_combo, 1, 1)
        layout.addWidget(QLabel("当前计算对象"), 1, 2)
        self.clone_primary_combo = NoWheelComboBox()
        layout.addWidget(self.clone_primary_combo, 1, 3)
        self.clone_targets_label = QLabel()
        self.clone_targets_label.setWordWrap(True)
        layout.addWidget(self.clone_targets_label, 2, 0, 1, 4)
        self.clone_category_combo.currentIndexChanged.connect(
            self._clone_category_changed
        )
        self.clone_activity_combo.currentIndexChanged.connect(
            self._clone_activity_changed
        )
        self.clone_difficulty_combo.currentIndexChanged.connect(
            self._clone_difficulty_changed
        )
        self.clone_primary_combo.currentIndexChanged.connect(self._emit_preset)
        self.stack.addWidget(page)

    def _build_outer_realm_page(self) -> None:
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("环境版本"), 0, 0)
        self.outer_config_combo = NoWheelComboBox()
        layout.addWidget(self.outer_config_combo, 0, 1)
        layout.addWidget(QLabel("层数"), 0, 2)
        self.outer_floor_combo = NoWheelComboBox()
        layout.addWidget(self.outer_floor_combo, 0, 3)
        layout.addWidget(QLabel("分区"), 0, 4)
        self.outer_half_combo = NoWheelComboBox()
        layout.addWidget(self.outer_half_combo, 0, 5)
        layout.addWidget(QLabel("当前计算对象"), 1, 0)
        self.outer_primary_combo = NoWheelComboBox()
        layout.addWidget(self.outer_primary_combo, 1, 1, 1, 5)
        self.outer_targets_label = QLabel()
        self.outer_targets_label.setWordWrap(True)
        layout.addWidget(self.outer_targets_label, 2, 0, 1, 6)
        self.outer_config_combo.currentIndexChanged.connect(self._outer_config_changed)
        self.outer_floor_combo.currentIndexChanged.connect(self._outer_floor_changed)
        self.outer_half_combo.currentIndexChanged.connect(self._outer_half_changed)
        self.outer_primary_combo.currentIndexChanged.connect(self._emit_preset)
        self.stack.addWidget(page)

    def _build_feast_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        selectors = QGridLayout()
        selectors.addWidget(QLabel("挑战对象"), 0, 0)
        self.feast_stage_combo = NoWheelComboBox()
        selectors.addWidget(self.feast_stage_combo, 0, 1)
        selectors.addWidget(QLabel("难度"), 0, 2)
        self.feast_difficulty_combo = NoWheelComboBox()
        selectors.addWidget(self.feast_difficulty_combo, 0, 3)
        layout.addLayout(selectors)
        self.feast_options_widget = QWidget()
        self.feast_options_layout = QFormLayout(self.feast_options_widget)
        self.feast_options_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.feast_options_widget)
        self.feast_stage_combo.currentIndexChanged.connect(self._feast_stage_changed)
        self.feast_difficulty_combo.currentIndexChanged.connect(self._emit_preset)
        self.stack.addWidget(page)

    def set_catalog(self, catalog: dict[str, Any]) -> None:
        self._loading = True
        self._catalog = catalog
        self.open_world_list.clear()
        for target in catalog.get("open_world") or ():
            suffix = f"（{target['subtitle']}）" if target.get("subtitle") else ""
            item = QListWidgetItem(f"{target['name_zh']}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, target)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.open_world_list.addItem(item)
        self.clone_category_combo.clear()
        for category in catalog.get("clone_categories") or ():
            self.clone_category_combo.addItem(category["name_zh"], category)
        self.outer_config_combo.clear()
        for config in catalog.get("outer_realm") or ():
            buff = config.get("season_buff") or {}
            label = str(config["level_config_id"])
            if buff:
                label += f" · {buff['season_name_zh']} · {buff['buff_name_zh']}"
            self.outer_config_combo.addItem(label, config)
        self.feast_stage_combo.clear()
        for stage in catalog.get("feast") or ():
            self.feast_stage_combo.addItem(stage["name_zh"], stage)
        self.witch_combo.clear()
        self.witch_combo.addItem("无", None)
        for buff in catalog.get("witch_buffs") or ():
            self.witch_combo.addItem(buff["name_zh"], buff)
        self._loading = False
        self._clone_category_changed()
        self._outer_config_changed()
        self._feast_stage_changed()

    def render(
        self,
        condition: BattleTargetCondition | None,
        *,
        detected_environment_kind: str = "",
        detected_environment_ref: str = "",
        detected_difficulty_id: int | None = None,
        detected_options: tuple[tuple[str, str], ...] = (),
        detected_floor: int | None = None,
    ) -> None:
        self._loading = True
        kind = (
            condition.environment_kind
            if condition is not None and condition.environment_kind != "manual"
            else detected_environment_kind or "open_world"
        )
        if (
            condition is not None
            and condition.environment_kind == "open_world"
            and condition.environment_ref.startswith("clone|")
        ):
            kind = "clone"
        self.environment_combo.setCurrentIndex(
            max(0, self.environment_combo.findData(kind))
        )
        self.stack.setCurrentIndex(max(0, self.environment_combo.currentIndex()))
        if condition is not None:
            self._restore_open_world(condition)
            self._restore_clone(condition)
            self._restore_outer_realm(condition)
            self._restore_feast(condition)
            witch_index = next((
                index for index in range(self.witch_combo.count())
                if (self.witch_combo.itemData(index) or {}).get("buff_id")
                == condition.witch_buff_id
            ), 0)
            self.witch_combo.setCurrentIndex(witch_index)
        elif detected_environment_ref:
            self._restore_detected_environment(
                kind=kind,
                environment_ref=detected_environment_ref,
                difficulty_id=detected_difficulty_id,
                options=detected_options,
            )
        elif kind == "outer_realm" and detected_floor is not None:
            self._set_combo_data(
                self.outer_floor_combo,
                detected_floor,
                "level_id",
            )
        self._loading = False
        # Restore methods already rebuilt dependent combos and selected their
        # saved values. Re-running the environment refresh here would rebuild
        # Feast option combos once more and silently discard those selections.
        self._emit_preset()

    def _restore_detected_environment(
        self,
        *,
        kind: str,
        environment_ref: str,
        difficulty_id: int | None,
        options: tuple[tuple[str, str], ...],
    ) -> None:
        parts = environment_ref.split("|")
        if kind == "clone" and len(parts) >= 4 and parts[0] == "clone":
            self._set_combo_data(self.clone_category_combo, parts[1], "category_id")
            self._clone_category_changed()
            self._set_combo_data(self.clone_activity_combo, parts[2], "clone_id")
            self._clone_activity_changed()
            try:
                ordinal = int(parts[3])
            except ValueError:
                return
            self._set_combo_data(
                self.clone_difficulty_combo,
                ordinal,
                "difficulty_ordinal",
            )
            self._clone_difficulty_changed()
        elif kind == "outer_realm" and len(parts) >= 3:
            self._set_combo_data(self.outer_config_combo, parts[0], "level_config_id")
            self._outer_config_changed()
            try:
                floor = int(parts[1])
            except ValueError:
                return
            self._set_combo_data(self.outer_floor_combo, floor, "level_id")
            self._outer_floor_changed()
            self._set_combo_data(self.outer_half_combo, parts[2], "stage")
            self._outer_half_changed()
        elif kind == "feast":
            self._set_combo_data(self.feast_stage_combo, environment_ref, "stage_id")
            self._feast_stage_changed()
            self._set_combo_data(
                self.feast_difficulty_combo,
                difficulty_id,
                "difficulty_id",
            )
            for key, option_id in options:
                combo = self._feast_option_combos.get(key)
                if combo is not None:
                    self._set_combo_data(combo, option_id, "option_id")

    def current_preset(self) -> dict[str, Any]:
        kind = str(self.environment_combo.currentData() or "open_world")
        if kind == "open_world":
            preset = self._open_world_preset()
        elif kind == "clone":
            preset = self._clone_preset()
        elif kind == "outer_realm":
            preset = self._outer_realm_preset()
        else:
            preset = self._feast_preset()
        buff = self.witch_combo.currentData()
        preset.update({
            "witch_buff_id": "" if not buff else str(buff["buff_id"]),
            "witch_buff_name_zh": "" if not buff else str(buff["name_zh"]),
            "witch_buff_property_id": "" if not buff else str(buff["property_id"]),
            "witch_buff_value": None if not buff else float(buff["property_value"]),
            "witch_buff_is_percent": bool(buff and buff["is_percent"]),
        })
        return preset

    def _environment_changed(self) -> None:
        self.stack.setCurrentIndex(max(0, self.environment_combo.currentIndex()))
        self._refresh_current_environment()

    def _refresh_current_environment(self) -> None:
        kind = self.environment_combo.currentData()
        if kind == "open_world":
            self._open_world_selection_changed()
        elif kind == "clone":
            self._clone_category_changed()
        elif kind == "outer_realm":
            self._outer_config_changed()
        else:
            self._feast_stage_changed()

    def _open_world_selection_changed(self) -> None:
        previous = self.open_world_primary.currentData()
        selected = self._checked_open_world_targets()
        self.open_world_primary.clear()
        for target in selected:
            self.open_world_primary.addItem(target["name_zh"], target)
        if previous:
            self._set_combo_data(self.open_world_primary, previous.get("target_id"), "target_id")
        self._open_world_primary_changed()

    def _open_world_primary_changed(self) -> None:
        primary = self.open_world_primary.currentData() or {}
        previous = self.open_world_variant.currentData()
        self.open_world_variant.clear()
        variants = tuple(
            variant
            for variant in primary.get("variants") or ()
            if variant.get("profile")
        )
        for variant in variants:
            level = float(variant.get("monster_level") or 1.0)
            self.open_world_variant.addItem(f"Lv.{level:.0f}", variant)
        if isinstance(previous, dict):
            self._set_combo_data(
                self.open_world_variant,
                previous.get("monster_level"),
                "monster_level",
            )
        self._emit_preset()

    def _outer_config_changed(self) -> None:
        config = self.outer_config_combo.currentData() or {}
        previous = self.outer_floor_combo.currentData()
        self.outer_floor_combo.clear()
        for level in config.get("levels") or ():
            label = f"第 {level['level_id']} 层"
            if level.get("name_zh"):
                label += f" · {level['name_zh']}"
            self.outer_floor_combo.addItem(label, level)
        if isinstance(previous, dict):
            self._set_combo_data(self.outer_floor_combo, previous.get("level_id"), "level_id")
        self._outer_floor_changed()

    def _clone_category_changed(self) -> None:
        category = self.clone_category_combo.currentData() or {}
        previous = self.clone_activity_combo.currentData()
        self.clone_activity_combo.clear()
        for activity in category.get("activities") or ():
            self.clone_activity_combo.addItem(activity["name_zh"], activity)
        if isinstance(previous, dict):
            self._set_combo_data(
                self.clone_activity_combo,
                previous.get("clone_id"),
                "clone_id",
            )
        self._clone_activity_changed()

    def _clone_activity_changed(self) -> None:
        activity = self.clone_activity_combo.currentData() or {}
        previous = self.clone_difficulty_combo.currentData()
        self.clone_difficulty_combo.clear()
        for difficulty in activity.get("difficulties") or ():
            level = int(difficulty.get("difficulty_level") or 0)
            team_level = int(difficulty.get("team_level") or 0)
            label = f"难度 {level or int(difficulty['difficulty_ordinal']) + 1}"
            if team_level:
                label += f"（推荐队伍 Lv.{team_level}）"
            self.clone_difficulty_combo.addItem(label, difficulty)
        if isinstance(previous, dict):
            self._set_combo_data(
                self.clone_difficulty_combo,
                previous.get("difficulty_ordinal"),
                "difficulty_ordinal",
            )
        self._clone_difficulty_changed()

    def _clone_difficulty_changed(self) -> None:
        difficulty = self.clone_difficulty_combo.currentData() or {}
        targets = self._clone_targets(difficulty)
        previous = self.clone_primary_combo.currentData()
        self.clone_primary_combo.clear()
        for target in targets:
            label = target["name_zh"]
            if target.get("monster_count", 1) > 1:
                label += f" ×{target['monster_count']}"
            self.clone_primary_combo.addItem(label, target)
        if isinstance(previous, dict):
            self._set_combo_data(
                self.clone_primary_combo,
                previous.get("target_id"),
                "target_id",
            )
        self.clone_targets_label.setText(
            "本难度对象：" + "、".join(target["name_zh"] for target in targets)
            if targets else "本难度没有可用的刷怪模板；可继续人工补充目标参数。"
        )
        self._emit_preset()

    def _outer_floor_changed(self) -> None:
        level = self.outer_floor_combo.currentData() or {}
        previous = self.outer_half_combo.currentData()
        self.outer_half_combo.clear()
        for half in level.get("halves") or ():
            self.outer_half_combo.addItem(half["name_zh"], half)
        if isinstance(previous, dict):
            self._set_combo_data(self.outer_half_combo, previous.get("stage"), "stage")
        self._outer_half_changed()

    def _outer_half_changed(self) -> None:
        config = self.outer_config_combo.currentData() or {}
        half = self.outer_half_combo.currentData() or {}
        targets = list(half.get("targets") or ())
        previous = self.outer_primary_combo.currentData()
        self.outer_primary_combo.clear()
        for target in targets:
            label = f"{target['name_zh']} Lv.{target['monster_level']:.0f}"
            if target.get("monster_count", 1) > 1:
                label += f" ×{target['monster_count']}"
            self.outer_primary_combo.addItem(label, target)
        if isinstance(previous, dict):
            self._set_combo_data(self.outer_primary_combo, previous.get("target_id"), "target_id")
        targets_text = (
            "本分区对象：" + "、".join(target["name_zh"] for target in targets)
            if targets else "本层没有可用的怪物属性包。"
        )
        buff = config.get("season_buff") or {}
        if buff:
            targets_text += f"\n赛季 Buff：{buff['buff_name_zh']}——{buff['description_zh']}"
        self.outer_targets_label.setText(targets_text)
        self._emit_preset()

    def _feast_stage_changed(self) -> None:
        stage = self.feast_stage_combo.currentData() or {}
        previous_difficulty = self.feast_difficulty_combo.currentData()
        self.feast_difficulty_combo.clear()
        for difficulty in stage.get("difficulties") or ():
            self.feast_difficulty_combo.addItem(
                f"{difficulty['name_zh']}（Lv.{difficulty['monster_level']}）",
                difficulty,
            )
        if isinstance(previous_difficulty, dict):
            self._set_combo_data(
                self.feast_difficulty_combo,
                previous_difficulty.get("difficulty_id"),
                "difficulty_id",
            )
        while self.feast_options_layout.rowCount():
            self.feast_options_layout.removeRow(0)
        self._feast_option_combos.clear()
        for category in stage.get("option_categories") or ():
            key = str(category["category_ordinal"])
            combo = NoWheelComboBox()
            combo.addItem("不选择", None)
            for option in category.get("options") or ():
                combo.addItem(self._option_label(option), option)
            combo.currentIndexChanged.connect(self._emit_preset)
            self._feast_option_combos[key] = combo
            self.feast_options_layout.addRow(category["name_zh"], combo)
        self._emit_preset()

    def _restore_open_world(self, condition: BattleTargetCondition) -> None:
        wanted = set(condition.selected_target_ids)
        for index in range(self.open_world_list.count()):
            item = self.open_world_list.item(index)
            item.setCheckState(
                Qt.CheckState.Checked
                if item.data(Qt.ItemDataRole.UserRole)["target_id"] in wanted
                else Qt.CheckState.Unchecked
            )
        self._open_world_selection_changed()
        self._set_combo_data(self.open_world_primary, condition.primary_target_id, "target_id")
        self._open_world_primary_changed()
        self._set_combo_data(
            self.open_world_variant,
            condition.enemy_level,
            "monster_level",
        )

    def _restore_outer_realm(self, condition: BattleTargetCondition) -> None:
        parts = condition.environment_ref.split("|")
        if condition.environment_kind != "outer_realm" or len(parts) < 3:
            return
        self._set_combo_data(self.outer_config_combo, parts[0], "level_config_id")
        self._outer_config_changed()
        try:
            floor = int(parts[1])
        except ValueError:
            return
        self._set_combo_data(self.outer_floor_combo, floor, "level_id")
        self._outer_floor_changed()
        self._set_combo_data(self.outer_half_combo, parts[2], "stage")
        self._outer_half_changed()
        self._set_combo_data(self.outer_primary_combo, condition.primary_target_id, "target_id")

    def _restore_clone(self, condition: BattleTargetCondition) -> None:
        parts = condition.environment_ref.split("|")
        if (
            condition.environment_kind != "open_world"
            or len(parts) < 4
            or parts[0] != "clone"
        ):
            return
        self._set_combo_data(self.clone_category_combo, parts[1], "category_id")
        self._clone_category_changed()
        self._set_combo_data(self.clone_activity_combo, parts[2], "clone_id")
        self._clone_activity_changed()
        try:
            ordinal = int(parts[3])
        except ValueError:
            return
        self._set_combo_data(
            self.clone_difficulty_combo,
            ordinal,
            "difficulty_ordinal",
        )
        self._clone_difficulty_changed()
        self._set_combo_data(
            self.clone_primary_combo,
            condition.primary_target_id,
            "target_id",
        )

    def _restore_feast(self, condition: BattleTargetCondition) -> None:
        if condition.environment_kind != "feast":
            return
        self._set_combo_data(self.feast_stage_combo, condition.environment_ref, "stage_id")
        self._feast_stage_changed()
        self._set_combo_data(self.feast_difficulty_combo, condition.difficulty_id, "difficulty_id")
        selected = dict(condition.feast_options)
        for key, combo in self._feast_option_combos.items():
            self._set_combo_data(combo, selected.get(key), "option_id")

    def _open_world_preset(self) -> dict[str, Any]:
        selected = self._checked_open_world_targets()
        primary = self.open_world_primary.currentData()
        variant = self.open_world_variant.currentData()
        if primary and variant:
            primary = {
                **primary,
                "monster_level": float(variant.get("monster_level") or 1.0),
                "profile_set": variant.get("profile_set"),
                "pack_id": variant.get("pack_id"),
                "profile": dict(variant.get("profile") or {}),
            }
        return self._preset(
            kind="open_world",
            ref="",
            selected=selected,
            primary=primary,
            difficulty=None,
            options={},
        )

    def _outer_realm_preset(self) -> dict[str, Any]:
        config = self.outer_config_combo.currentData() or {}
        level = self.outer_floor_combo.currentData() or {}
        half = self.outer_half_combo.currentData() or {}
        stage = str(half.get("stage") or "")
        return self._preset(
            kind="outer_realm",
            ref=f"{config.get('level_config_id', '')}|{level.get('level_id', '')}|{stage}",
            selected=list(half.get("targets") or ()),
            primary=self.outer_primary_combo.currentData(),
            difficulty=None,
            options={},
        )

    def _clone_preset(self) -> dict[str, Any]:
        category = self.clone_category_combo.currentData() or {}
        activity = self.clone_activity_combo.currentData() or {}
        difficulty = self.clone_difficulty_combo.currentData() or {}
        targets = self._clone_targets(difficulty)
        ordinal = int(difficulty.get("difficulty_ordinal") or 0)
        return self._preset(
            kind="open_world",
            ref=(
                f"clone|{category.get('category_id', '')}|"
                f"{activity.get('clone_id', '')}|{ordinal}"
            ),
            selected=targets,
            primary=self.clone_primary_combo.currentData(),
            # Clone difficulty is encoded in environment_ref. difficulty_id is
            # reserved for the 1..4 Feast contract in account storage.
            difficulty=None,
            options={},
        )

    def _feast_preset(self) -> dict[str, Any]:
        stage = self.feast_stage_combo.currentData() or {}
        difficulty = self.feast_difficulty_combo.currentData()
        target = None
        if difficulty:
            target = {
                "target_id": str(stage.get("boss_monster_id") or ""),
                "name_zh": f"争锋赏宴·{stage.get('name_zh', '目标')}",
                "monster_level": float(difficulty.get("monster_level") or 1.0),
                "profile": {
                    key: difficulty.get(key)
                    for key in (
                        "health_base", "health_up", "health_add", "defense_base",
                        "defense_up", "defense_add", "topple_limit", "resistances",
                    )
                },
            }
        options = {
            key: combo.currentData()
            for key, combo in self._feast_option_combos.items()
            if combo.currentData()
        }
        preset = self._preset(
            kind="feast",
            ref=str(stage.get("stage_id") or ""),
            selected=[] if target is None else [target],
            primary=target,
            difficulty=None if not difficulty else int(difficulty["difficulty_id"]),
            options={key: value["option_id"] for key, value in options.items()},
        )
        for option in options.values():
            if option.get("effect_kind") == "resistance_up" and option.get("damage_type"):
                damage_type = str(option["damage_type"])
                preset["resistances"][damage_type] = (
                    preset["resistances"].get(damage_type, 0.0)
                    + float(option.get("add_value") or 0.0)
                )
        return preset

    @staticmethod
    def _preset(*, kind, ref, selected, primary, difficulty, options) -> dict[str, Any]:
        profile = dict((primary or {}).get("profile") or {})
        resistances = {
            key: (
                float(value.get("resistance_base") or 0.0)
                if isinstance(value, dict)
                else float(value or 0.0)
            )
            for key, value in (profile.get("resistances") or {}).items()
        }
        return {
            "environment_kind": kind,
            "environment_ref": ref,
            "selected_target_ids": tuple(str(row["target_id"]) for row in selected),
            "primary_target_id": "" if not primary else str(primary["target_id"]),
            "difficulty_id": difficulty,
            "feast_options": options,
            "target_name": "" if not primary else str(primary["name_zh"]),
            "enemy_level": 90.0 if not primary else float(primary["monster_level"]),
            "scene": "open_world" if kind == "open_world" else "outer_realm",
            "enemy_defense_base": profile.get("defense_base"),
            "enemy_defense_up": float(profile.get("defense_up") or 0.0),
            "enemy_defense_add": float(profile.get("defense_add") or 0.0),
            "enemy_topple_limit": float(profile.get("topple_limit") or 50.0),
            "resistances": {
                damage_type: float(resistances.get(
                    damage_type,
                    0.2 if damage_type == "normal" else 0.0,
                ))
                for damage_type in _DAMAGE_TYPES
            },
        }

    @staticmethod
    def _option_label(option: dict[str, Any]) -> str:
        effect = str(option.get("effect_kind") or "")
        if effect == "time_limit":
            text = f"{option.get('limit_seconds', 0)} 秒"
        elif effect in {"health_up", "attack_up", "resistance_up"}:
            text = f"+{float(option.get('add_value') or 0.0) * 100:.0f}%"
        else:
            text = str(option["option_id"])
        return f"{text}（积分 {option.get('score', 0)}）"

    @staticmethod
    def _set_combo_data(combo: NoWheelComboBox, wanted: object, key: str | None = None) -> None:
        for index in range(combo.count()):
            data = combo.itemData(index)
            value = data.get(key) if key and isinstance(data, dict) else data
            if value == wanted:
                combo.setCurrentIndex(index)
                return

    def _checked_open_world_targets(self) -> list[dict[str, Any]]:
        return [
            self.open_world_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.open_world_list.count())
            if self.open_world_list.item(index).checkState() == Qt.CheckState.Checked
        ]

    @staticmethod
    def _clone_targets(difficulty: dict[str, Any]) -> list[dict[str, Any]]:
        targets: dict[str, dict[str, Any]] = {}
        for member in difficulty.get("spawn_members") or ():
            target_id = str(
                member.get("monster_manual_id")
                or member.get("monster_template_name")
                or ""
            )
            if not target_id:
                continue
            target = targets.setdefault(target_id, {
                "target_id": target_id,
                "name_zh": str(member.get("name_zh") or target_id),
                "monster_level": float(
                    member.get("monster_level")
                    or difficulty.get("team_level")
                    or 90.0
                ),
                "monster_count": 0,
                "profile": dict(member.get("profile") or {}),
            })
            target["monster_count"] += int(member.get("monster_count") or 1)
        return list(targets.values())

    def _emit_preset(self) -> None:
        if self._loading:
            return
        preset = self.current_preset()
        selected_count = len(preset.get("selected_target_ids") or ())
        if selected_count == 0:
            self.summary_label.setText("请选择至少一个对象；保存前不会参与逐击重放。")
        else:
            self.summary_label.setText(
                f"已选 {selected_count} 个对象；当前逐击按“{preset['target_name']}”计算。"
            )
        self.preset_changed.emit(preset)
