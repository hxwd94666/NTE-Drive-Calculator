# 构建独立觉醒等级、普通效果选择与共鸣显示控件。
from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.text_catalog import plain_label, plain_tooltip, resolve_text_catalog
import re
from PySide6.QtWidgets import QCheckBox, QGroupBox, QLabel, QVBoxLayout
from src.services.official_role.panel.awakening import render_awaken_effect_description
from src.ui.common.widgets import NoWheelSpinBox
from .role_calculation import _mark_dirty, _refresh_role_calculations


def _plain_effect_text(value: object) -> str:
    return re.sub(r"<[^>]*>", "", str(value or "")).strip()


def _resonance_threshold(effect: dict) -> int | None:
    match = re.search(r"(?:^|_)(\d+)$", str(effect.get("effect_id") or ""))
    return int(match.group(1)) if match is not None else None


def _build_awakening_group(
    window,
    character_id: int,
    detail: dict,
    editor: dict,
) -> QGroupBox:
    group = QGroupBox(resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.official_role.build_awakening_group.group'))
    group.setObjectName("officialRoleAwakeningGroup")
    layout = QVBoxLayout(group)
    layout.setSpacing(8)
    selected_ids = {
        str(effect_id)
        for effect_id in detail["profile"].get("selected_awaken_effect_ids") or ()
    }
    awakening_level = NoWheelSpinBox()
    awakening_level.setObjectName("officialRoleAwakeningLevel")
    awakening_level.setRange(0, 6)
    awakening_level.setPrefix(resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.official_role.build_awakening_group.awakening_level'))
    awakening_level.setToolTip(
        plain_tooltip(resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.official_role.build_awakening_group.awakening_level_2'))
    )
    awakening_level.setValue(int(detail["profile"].get("awakening_level") or 0))
    layout.addWidget(awakening_level)
    explanation = plain_label(
        resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.official_role.build_awakening_group.explanation')
    )
    explanation.setWordWrap(True)
    explanation.setStyleSheet("color:#8b949e;")
    layout.addWidget(explanation)
    editor["awakening_level"] = awakening_level
    checks: dict[str, QCheckBox] = {}
    description_labels: list[tuple[dict, QLabel]] = []
    normal_effects = [
        effect
        for effect in detail.get("awakenings") or ()
        if str(effect.get("awaken_type") or "") == "Awaken_Effect"
    ]
    resonance_effects = [
        effect
        for effect in detail.get("awakenings") or ()
        if str(effect.get("awaken_type") or "") == "Awaken_Resonance"
    ]
    for index, effect in enumerate(normal_effects, start=1):
        effect_id = str(effect.get("effect_id") or "")
        title = str(effect.get("title_zh") or localized_text(getattr(window, 'text_catalog', None), 'auto.features.official_role.role_awakening.build_awakening_group.f8de2dc3e', '觉醒 {index}', index=f'{index}'))
        check = QCheckBox(f"{index}. {title}")
        check.setChecked(effect_id in selected_ids)
        layout.addWidget(check)
        description = plain_label(_plain_effect_text(effect.get("description_zh")) or resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.official_role.build_awakening_group.description'))
        description.setWordWrap(True)
        description.setContentsMargins(24, 0, 8, 2)
        description.setStyleSheet("color:#8b949e;")
        layout.addWidget(description)
        description_labels.append((effect, description))
        checks[effect_id] = check

    resonance_labels: list[tuple[dict, int, QLabel]] = []
    if resonance_effects:
        resonance_title = plain_label(resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.official_role.build_awakening_group.resonance_title'))
        resonance_title.setStyleSheet("font-weight:bold;color:#58a6ff;margin-top:4px;")
        layout.addWidget(resonance_title)
    for effect in resonance_effects:
        threshold = _resonance_threshold(effect)
        if threshold is None:
            continue
        title = str(effect.get("title_zh") or localized_text(getattr(window, 'text_catalog', None), 'auto.features.official_role.role_awakening.build_awakening_group.f07009404', '{threshold} 觉效果', threshold=f'{threshold}'))
        label = plain_label()
        label.setWordWrap(True)
        label.setContentsMargins(8, 0, 8, 0)
        label.setProperty("resonance_title", title)
        resonance_labels.append((effect, threshold, label))
        layout.addWidget(label)

    editor["awakening_checks"] = checks

    def current_profile() -> dict:
        return {
            **detail["profile"],
            "awakening_level": awakening_level.value(),
            "skill_levels": dict(
                editor.get("skill_levels")
                or detail["profile"].get("skill_levels")
                or {}
            ),
            "selected_awaken_effect_ids": [
                effect_id
                for effect_id, check in checks.items()
                if check.isChecked()
            ],
            "awakening_selection_initialized": True,
        }

    def rendered_description(effect: dict) -> str:
        rendered = render_awaken_effect_description(
            effect,
            current_profile(),
            detail.get("awakenings") or (),
        )
        return _plain_effect_text(rendered) or "暂无效果说明"

    def refresh_descriptions() -> None:
        for effect, label in description_labels:
            label.setText(rendered_description(effect))

    def refresh_resonance() -> None:
        level = awakening_level.value()
        for effect, threshold, label in resonance_labels:
            active = level >= threshold
            state = "已激活" if active else localized_text(getattr(window, 'text_catalog', None), 'auto.features.official_role.role_awakening.refresh_resonance.f9eb2cdc6', '未激活（需要 {threshold} 觉）', threshold=f'{threshold}')
            label.setText(
                f"{state}｜{label.property('resonance_title')}\n"
                f"{rendered_description(effect)}"
            )
            label.setStyleSheet(
                "color:#3fb950;font-weight:600;" if active else "color:#8b949e;"
            )

    def selection_changed(*_args) -> None:
        refresh_descriptions()
        refresh_resonance()
        _mark_dirty(window, character_id)
        for callback in tuple(editor.get("awakening_refreshers") or ()):
            callback()
        _refresh_role_calculations(editor)

    def level_changed(*_args) -> None:
        # Lowering capacity clears excess selections deterministically.
        selected = [check for check in checks.values() if check.isChecked()]
        for check in selected[awakening_level.value():]:
            check.blockSignals(True)
            check.setChecked(False)
            check.blockSignals(False)
        selection_changed()

    def effect_changed(*_args) -> None:
        selected_count = sum(check.isChecked() for check in checks.values())
        if selected_count > awakening_level.value():
            awakening_level.setValue(selected_count)
        else:
            selection_changed()

    awakening_level.valueChanged.connect(level_changed)
    for check in checks.values():
        check.toggled.connect(effect_changed)
    editor["refresh_awakening_descriptions"] = refresh_descriptions
    refresh_descriptions()
    refresh_resonance()
    return group
