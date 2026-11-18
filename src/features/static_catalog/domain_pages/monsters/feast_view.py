# 争锋赏宴的单画像动态选择视图。
"""Responsive Feast selectors that reload one selected combat profile."""

from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.text_catalog import plain_label, resolve_text_catalog
from pathlib import Path
from typing import Callable, Protocol

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.monsters.detail_view import (
    MonsterContext,
    MonsterDetailView,
)
from src.services.static_catalog.monsters.models import (
    CatalogDetail,
    CatalogEntry,
    FeastSetup,
)
from src.ui.common.widgets import NoWheelComboBox


class FeastDetailLoader(Protocol):
    def __call__(self, period_id: str, stage_id: str, difficulty_id: int,
                 selected_option_ids: tuple[str, ...], boss_monster_id: str | None = None) -> CatalogDetail | None: ...

BlessingDetailLoader = Callable[[str], CatalogDetail | None]


class FeastEncounterView(QWidget):
    """Own only the user's current Feast difficulty and condition choices."""

    def __init__(self, parent: QWidget | None = None, *, text_catalog=None) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._setup: FeastSetup | None = None
        self._icon: Path | None = None
        self._loader: FeastDetailLoader | None = None
        self._blessing_loader: BlessingDetailLoader | None = None
        self._loading = False
        self._layout_bucket = ""
        self._option_combos: list[NoWheelComboBox] = []
        self._selector_widgets: list[QWidget] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 2, 4, 0)
        root.setSpacing(8)
        self.heading = plain_label(resolve_text_catalog(text_catalog).render('ui.battle_report.init.environment_combo_4'), self)
        self.heading.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:20px;font-weight:900"
        ))
        root.addWidget(self.heading)
        self.subtitle = plain_label(self)
        self.subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
        root.addWidget(self.subtitle)

        self.controls = QFrame(self)
        self.controls.setObjectName("feastSelectionPanel")
        self.controls.setStyleSheet(themed_style(
            "QFrame#feastSelectionPanel{background:#161b22;border:0;"
            "border-radius:12px;}"
        ))
        self.controls_layout = QVBoxLayout(self.controls)
        self.controls_layout.setContentsMargins(10, 8, 10, 8)
        self.controls_layout.setSpacing(6)
        root.addWidget(self.controls)

        summary_row = QHBoxLayout()
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.setSpacing(8)
        difficulty_label = plain_label(resolve_text_catalog(text_catalog).render('ui.battle_report.build_feast_page.caption_2'), self.controls)
        difficulty_label.setStyleSheet(themed_style(
            "color:#c9d1d9;font-size:10px;font-weight:800"
        ))
        summary_row.addWidget(difficulty_label)
        self.difficulty_combo = NoWheelComboBox(self.controls)
        self.difficulty_combo.setMinimumWidth(210)
        self.difficulty_combo.currentIndexChanged.connect(self._refresh_detail)
        summary_row.addWidget(self.difficulty_combo, 1)
        self.target_combo = NoWheelComboBox(self.controls)
        self.target_combo.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.static_catalog.init.target_combo'))
        self.target_combo.currentIndexChanged.connect(self._target_changed)
        self.controls_layout.addWidget(self.target_combo)
        self.condition_summary = plain_label(resolve_text_catalog(text_catalog).render('ui.static_catalog.init.condition_summary'), self.controls)
        self.condition_summary.setStyleSheet(themed_style(
            "color:#8b949e;font-size:10px"
        ))
        summary_row.addWidget(self.condition_summary)
        self.conditions_toggle = QToolButton(self.controls)
        self.conditions_toggle.setCheckable(True)
        self.conditions_toggle.setText(resolve_text_catalog(text_catalog).render('ui.static_catalog.init.conditions_toggle'))
        self.conditions_toggle.setStyleSheet(themed_style(
            "QToolButton{color:#58a6ff;background:#0d1117;border:0;"
            "border-radius:7px;padding:5px 10px;font-weight:800;}"
            "QToolButton:hover{background:#21262d;}"
        ))
        self.conditions_toggle.toggled.connect(self._toggle_options)
        summary_row.addWidget(self.conditions_toggle)
        self.blessing_toggle = QToolButton(self.controls)
        self.blessing_toggle.setCheckable(True)
        self.blessing_toggle.setText(resolve_text_catalog(text_catalog).render('ui.battle_report.init.caption_2'))
        self.blessing_toggle.setStyleSheet(self.conditions_toggle.styleSheet())
        self.blessing_toggle.toggled.connect(self._toggle_blessing)
        summary_row.addWidget(self.blessing_toggle)
        self.controls_layout.addLayout(summary_row)

        self.options_host = QWidget(self.controls)
        self.options_layout = QGridLayout(self.options_host)
        self.options_layout.setContentsMargins(0, 2, 0, 0)
        self.options_layout.setHorizontalSpacing(8)
        self.options_layout.setVerticalSpacing(6)
        self.options_host.hide()
        self.controls_layout.addWidget(self.options_host)

        self.blessing_host = QFrame(self.controls)
        self.blessing_host.setObjectName("monsterBlessingPanel")
        self.blessing_host.setStyleSheet(themed_style(
            "QFrame#monsterBlessingPanel{background:#0d1117;border:0;"
            "border-radius:9px;}"
        ))
        blessing_layout = QVBoxLayout(self.blessing_host)
        blessing_layout.setContentsMargins(9, 7, 9, 7)
        blessing_layout.setSpacing(5)
        blessing_row = QHBoxLayout()
        blessing_label = plain_label(resolve_text_catalog(text_catalog).render('ui.static_catalog.init.blessing_label'), self.blessing_host)
        blessing_label.setStyleSheet(themed_style(
            "color:#c9d1d9;font-size:10px;font-weight:800"
        ))
        self.blessing_combo = NoWheelComboBox(self.blessing_host)
        self.blessing_combo.currentIndexChanged.connect(self._refresh_blessing)
        blessing_row.addWidget(blessing_label)
        blessing_row.addWidget(self.blessing_combo, 1)
        blessing_layout.addLayout(blessing_row)
        self.blessing_summary = plain_label(resolve_text_catalog(text_catalog).render('ui.static_catalog.init.blessing_summary'), self.blessing_host)
        self.blessing_summary.setWordWrap(True)
        self.blessing_summary.setStyleSheet(themed_style(
            "color:#8b949e;font-size:10px"
        ))
        blessing_layout.addWidget(self.blessing_summary)
        self.blessing_host.hide()
        self.controls_layout.addWidget(self.blessing_host)

        self.detail = MonsterDetailView(self, text_catalog=text_catalog)
        root.addWidget(self.detail, 1)

    def set_stage(
        self,
        setup: FeastSetup,
        *,
        icon: Path | None,
        loader: FeastDetailLoader,
        blessings: tuple[CatalogEntry, ...],
        blessing_loader: BlessingDetailLoader,
    ) -> None:
        self._loading = True
        self._setup = setup
        self._icon = icon
        self._loader = loader
        self._blessing_loader = blessing_loader
        self.heading.setText(
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.set_stage.heading', period_label=f'{setup.period_label}', challenge_ordinal=f'{setup.challenge_ordinal}')
        )
        self.subtitle.setText(
            f"{setup.title} · {setup.boss_name} · {setup.schedule_label}"
        )
        self.target_combo.clear()
        if len(setup.targets) > 1:
            self.target_combo.addItem(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.set_stage.target_combo'), None)
        for ordinal, target in enumerate(setup.targets, 1):
            self.target_combo.addItem(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.set_stage.target_combo_2', ordinal=f'{ordinal}', display_name=f'{target.display_name}'), target)
        self.target_combo.setVisible(len(setup.targets) > 1)
        self.difficulty_combo.clear()
        for difficulty in setup.difficulties:
            self.difficulty_combo.addItem(
                difficulty.display_name + (f" · Lv.{difficulty.monster_level}" if difficulty.monster_level is not None else ""),
                difficulty.difficulty_id,
            )
        default_index = self.difficulty_combo.findData(setup.default_difficulty_id)
        self.difficulty_combo.setCurrentIndex(max(0, default_index))

        for widget in self._selector_widgets:
            widget.deleteLater()
        self._selector_widgets = []
        self._option_combos = []
        for group in setup.option_groups:
            combo = NoWheelComboBox(self.options_host)
            combo.addItem(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.set_stage.combo'), "")
            for option in group.options:
                combo.addItem(option.display_name, option.option_id)
                combo.setItemData(combo.count() - 1, option.display_name, Qt.ItemDataRole.ToolTipRole)
            combo.currentIndexChanged.connect(self._refresh_detail)
            self._option_combos.append(combo)
            self._selector_widgets.append(
                self._selector(group.display_name, combo)
            )
        self._layout_options(force=True)
        has_options = bool(setup.option_groups)
        self.conditions_toggle.setVisible(has_options)
        self.conditions_toggle.setEnabled(has_options)
        self.conditions_toggle.setChecked(False)
        self._toggle_options(False)
        self.blessing_combo.clear()
        self.blessing_combo.addItem(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.feast_stage_changed.combo'), "")
        for blessing in blessings:
            self.blessing_combo.addItem(blessing.title, blessing.key)
        self.blessing_combo.setCurrentIndex(0)
        self.blessing_toggle.setChecked(False)
        self._toggle_blessing(False)
        self._loading = False
        self._refresh_detail()
        if setup.condition_note:
            self.condition_summary.setText(setup.condition_note)
        self._refresh_blessing()

    def _selector(self, label: str, combo: NoWheelComboBox) -> QWidget:
        host = QWidget(self.options_host)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        caption = plain_label(label, host)
        caption.setStyleSheet(themed_style(
            "color:#c9d1d9;font-size:10px;font-weight:800"
        ))
        combo.setMinimumWidth(140)
        layout.addWidget(caption)
        layout.addWidget(combo)
        return host

    def _toggle_options(self, expanded: bool) -> None:
        self.options_host.setVisible(expanded)
        self.conditions_toggle.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.toggle_options.conditions_toggle') if expanded else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.init.conditions_toggle'))

    def _toggle_blessing(self, expanded: bool) -> None:
        self.blessing_host.setVisible(expanded)
        self.blessing_toggle.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.toggle_blessing.blessing_toggle') if expanded else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.init.caption_2'))

    def _refresh_blessing(self) -> None:
        if self._loading:
            return
        key = str(self.blessing_combo.currentData() or "")
        if not key or self._blessing_loader is None:
            self.blessing_summary.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.init.blessing_summary'))
            return
        detail = self._blessing_loader(key)
        values = detail.sections[0].values if detail and detail.sections else ()
        if not values:
            self.blessing_summary.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.refresh_blessing.blessing_summary'))
            return
        value = values[0]
        label = value.display_label or "赐福效果"
        effect = value.display_value or "效果说明暂未提供"
        description = detail.sections[0].note.strip()
        self.blessing_summary.setText(
            " · ".join(part for part in (f"{label}：{effect}", description) if part)
        )

    def _refresh_detail(self) -> None:
        if self._loading or self._setup is None or self._loader is None:
            return
        difficulty_id = self.difficulty_combo.currentData()
        if difficulty_id is None:
            return
        target = self.target_combo.currentData()
        if len(self._setup.targets) > 1 and target is None:
            self.condition_summary.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.refresh_detail.condition_summary_2'))
            self.detail.hide()
            return
        self.detail.show()
        selected = tuple(
            str(combo.currentData())
            for combo in self._option_combos
            if combo.currentData()
        )
        self.condition_summary.setText(
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.refresh_detail.condition_summary', value=f'{len(selected)}')
            if selected else (self._setup.condition_note or resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.init.condition_summary'))
        )
        if selected and self._setup.condition_note:
            self.condition_summary.setText(self.condition_summary.text() + "；" + self._setup.condition_note)
        detail = self._loader(
            self._setup.period_id,
            self._setup.stage_id,
            int(difficulty_id),
            selected,
            *([target.monster_id] if len(self._setup.targets) > 1 and target is not None else []),
        )
        if detail is None:
            return
        self.detail.set_detail(
            detail,
            icon=self._icon,
            context=MonsterContext(
                play="争锋赏宴",
                scene=(
                    localized_text(getattr(self, 'text_catalog', None), 'auto.features.static_catalog.domain_pages.monsters.feast_view.refresh_detail.fe1e25e4e', '{period_label} · 挑战 {challenge_ordinal} · {title}', period_label=f'{self._setup.period_label}', challenge_ordinal=f'{self._setup.challenge_ordinal}', title=f'{self._setup.title}')
                ),
                level=self.difficulty_combo.currentText(),
            ),
        )

    def _target_changed(self) -> None:
        if self._loading or self._setup is None:
            return
        current = self.difficulty_combo.currentData()
        target = self.target_combo.currentData()
        choices = target.difficulties if target is not None else self._setup.difficulties
        self._loading = True
        try:
            self.difficulty_combo.clear()
            for difficulty in choices:
                label = difficulty.display_name + (f" · Lv.{difficulty.monster_level}" if difficulty.monster_level is not None else "")
                self.difficulty_combo.addItem(label, difficulty.difficulty_id)
            self.difficulty_combo.setCurrentIndex(max(0, self.difficulty_combo.findData(current)))
        finally:
            self._loading = False
        self._refresh_detail()

    def _layout_options(self, *, force: bool = False) -> None:
        if self.width() < 680:
            bucket = "narrow"
            columns = 1
        elif self.width() < 1000:
            bucket = "medium"
            columns = 2
        else:
            bucket = "wide"
            columns = 3
        if not force and bucket == self._layout_bucket:
            return
        while self.options_layout.count():
            self.options_layout.takeAt(0)
        for column in range(3):
            self.options_layout.setColumnStretch(column, 0)
        for index, widget in enumerate(self._selector_widgets):
            self.options_layout.addWidget(widget, index // columns, index % columns)
        for column in range(columns):
            self.options_layout.setColumnStretch(column, 1)
        self._layout_bucket = bucket

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._layout_options()
