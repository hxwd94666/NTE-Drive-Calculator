# 赏宴条件只消费冻结正式目标目录，多目标显式选择主目标并分别保存完整画像。
from src.ui.common.text_catalog import plain_label, resolve_text_catalog
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QGridLayout, QVBoxLayout, QWidget

from src.ui.common.widgets import NoWheelComboBox
from src.services.static_catalog.monsters.feast import feast_mechanism_note
from src.services.battle_report.targets.feast_profiles import feast_target_profiles


def _targets(stage):
    if stage.get("targets") is not None:
        return stage["targets"]
    # 已发布单目标目录保留原有契约，不从空身份或预览字段补目标。
    identity = stage.get("boss_monster_id")
    return [{"boss_monster_id": identity, "name_zh": stage.get("name_zh"),
             "difficulties": stage.get("difficulties", [])}] if identity else []


class FeastConditionMixin:
    def _build_feast_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        selectors = QGridLayout()
        self.feast_stage_combo = NoWheelComboBox()
        self.feast_target_combo = NoWheelComboBox()
        self.feast_difficulty_combo = NoWheelComboBox()
        selectors.addWidget(plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build_feast_page.caption')), 0, 0)
        selectors.addWidget(self.feast_stage_combo, 0, 1)
        selectors.addWidget(plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build_feast_page.caption_2')), 0, 2)
        selectors.addWidget(self.feast_difficulty_combo, 0, 3)
        selectors.addWidget(plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build_feast_page.caption_3')), 1, 0)
        selectors.addWidget(self.feast_target_combo, 1, 1, 1, 3)
        layout.addLayout(selectors)
        self.feast_options_widget = QWidget()
        self.feast_options_layout = QFormLayout(self.feast_options_widget)
        self.feast_options_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.feast_options_widget)
        self.feast_goal_note = plain_label()
        self.feast_goal_note.setWordWrap(True)
        layout.addWidget(self.feast_goal_note)
        self.feast_stage_combo.currentIndexChanged.connect(self._feast_stage_changed)
        self.feast_target_combo.currentIndexChanged.connect(self._feast_target_changed)
        self.feast_difficulty_combo.currentIndexChanged.connect(self._emit_preset)
        self.stack.addWidget(page)

    def _feast_stage_changed(self):
        previous = self._loading
        self._loading = True
        try:
            stage = self.feast_stage_combo.currentData() or {}
            targets = _targets(stage)
            self.feast_target_combo.clear()
            if len(targets) > 1:
                self.feast_target_combo.addItem(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.feast_stage_changed.feast_target_combo'), None)
            for index, target in enumerate(targets, 1):
                self.feast_target_combo.addItem(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.feast_stage_changed.feast_target_combo_2', index=f'{index}', value=f"{target.get('name_zh') or '名称暂未提供'}"), target)
            self.feast_target_combo.setEnabled(len(targets) > 1)
            self.feast_difficulty_combo.clear()
            while self.feast_options_layout.rowCount():
                self.feast_options_layout.removeRow(0)
            self._feast_option_combos.clear()
            self.feast_goal_note.setText(feast_mechanism_note(stage))
            for category in stage.get("option_categories") or ():
                combo = NoWheelComboBox()
                combo.addItem(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.feast_stage_changed.combo'), None)
                for option in category.get("options") or ():
                    combo.addItem(self._option_label(option, text_catalog=getattr(self, 'text_catalog', None)), option)
                    combo.setItemData(combo.count() - 1, self._option_label(option, text_catalog=getattr(self, 'text_catalog', None)), Qt.ItemDataRole.ToolTipRole)
                combo.currentIndexChanged.connect(self._emit_preset)
                self._feast_option_combos[str(category["category_ordinal"])] = combo
                self.feast_options_layout.addRow(category["name_zh"], combo)
            self._feast_target_changed()
        finally:
            self._loading = previous
        self._emit_preset()

    def _feast_target_changed(self):
        previous = self._loading
        wanted = (self.feast_difficulty_combo.currentData() or {}).get("difficulty_id")
        self._loading = True
        try:
            target = self.feast_target_combo.currentData() or {}
            self.feast_difficulty_combo.clear()
            for row in target.get("difficulties") or ():
                self.feast_difficulty_combo.addItem(f"{row['name_zh']}（Lv.{row['monster_level']}）", row)
            if wanted is not None:
                self._set_combo_data(self.feast_difficulty_combo, wanted, "difficulty_id")
        finally:
            self._loading = previous
        self._emit_preset()

    def _restore_feast(self, condition):
        if condition.environment_kind != "feast":
            return
        self._set_combo_data(self.feast_stage_combo, condition.environment_ref, "stage_id")
        self._feast_stage_changed()
        self._set_combo_data(self.feast_target_combo, condition.primary_target_id, "boss_monster_id")
        self._feast_target_changed()
        self._set_combo_data(self.feast_difficulty_combo, condition.difficulty_id, "difficulty_id")
        for key, combo in self._feast_option_combos.items():
            self._set_combo_data(combo, dict(condition.feast_options).get(key), "option_id")

    def _feast_preset(self):
        stage = self.feast_stage_combo.currentData() or {}
        main = self.feast_target_combo.currentData()
        difficulty = self.feast_difficulty_combo.currentData()
        options = {key: combo.currentData() for key, combo in self._feast_option_combos.items() if combo.currentData()}
        selected = []
        if main and difficulty:
            for target in _targets(stage):
                row = next((value for value in target.get("difficulties", ())
                            if value["difficulty_id"] == difficulty["difficulty_id"]), None)
                if row is None or not row.get("profile_available", True):
                    selected = []
                    break
                profile = {key: row.get(key) for key in ("health_base", "health_up", "health_add", "defense_base",
                                                          "defense_up", "defense_add", "topple_limit", "resistances")}
                for option in options.values():
                    if option.get("effect_kind") == "health_up":
                        profile["health_up"] = float(profile.get("health_up") or 0) + float(option.get("add_value") or 0)
                selected.append({"target_id": target["boss_monster_id"],
                                 "monster_template_name": target["boss_monster_id"],
                                 "profile_set": row.get("profile_set", ""), "pack_id": row.get("pack_id", ""),
                                 "name_zh": target.get("name_zh") or stage.get("name_zh", "名称暂未提供"),
                                 "monster_level": float(row["monster_level"]), "profile": profile})
        if main and difficulty and "targets" in difficulty:
            selected = feast_target_profiles(stage, difficulty, tuple(options.values()))
        primary = next((target for target in selected if main and target["target_id"] == main["boss_monster_id"]), None)
        preset = self._preset(kind="feast", ref=str(stage.get("stage_id") or ""), selected=selected, primary=primary,
                              difficulty=None if not difficulty else int(difficulty["difficulty_id"]),
                              options={key: value["option_id"] for key, value in options.items()})
        for option in options.values():
            if "targets" not in (difficulty or {}) and option.get("effect_kind") == "resistance_up" and option.get("damage_type"):
                damage_type = str(option["damage_type"])
                amount = float(option.get("add_value") or 0)
                preset["resistances"][damage_type] = preset["resistances"].get(damage_type, 0) + amount
                for profile in preset["selected_target_profiles"]:
                    profile["resistances"][damage_type] = profile["resistances"].get(damage_type, 0) + amount
        return preset
