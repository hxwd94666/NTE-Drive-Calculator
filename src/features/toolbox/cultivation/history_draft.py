# 将养成编辑控件投影为完整草稿，并在屏蔽信号时恢复已验证输入。
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import ExitStack
from typing import Any

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QComboBox, QSpinBox

from src.features.toolbox.cultivation.controls import refresh_participation_tooltip
from src.services.progression.models import CultivationForkSeed, CultivationSeed


def capture_target(
    editor: Any, seed: CultivationSeed, fork_seed: CultivationForkSeed | None,
    skill_inputs: Mapping[str, tuple[QSpinBox, QSpinBox]], *, line_id: str,
) -> dict[str, object]:
    """完整编辑状态独立于有效请求，关闭模块的编辑值也保留。"""
    fork = None
    if fork_seed is not None:
        fork = {
            "fork_id": fork_seed.fork_id, "name": fork_seed.fork_name,
            "enabled": editor.fork_toggle.isChecked(),
            "current_level": editor.fork_current_level.value(),
            "current_stage": int(editor.fork_current_stage.currentData()),
            "target_level": editor.fork_target_level.value(),
            "target_stage": int(editor.fork_target_stage.currentData()),
        }
    return {
        "line_id": line_id, "character_id": seed.character_id, "name": seed.character_name,
        "current_level": editor.current_level.value(), "current_stage": int(editor.current_stage.currentData()),
        "target_level": editor.target_level.value(), "target_stage": int(editor.target_stage.currentData()),
        "character_enabled": editor.character_toggle.isChecked(), "skills_enabled": editor.skills_toggle.isChecked(),
        "skills": [{"skill_id": skill_id, "current_level": current.value(), "target_level": target.value()}
                   for skill_id, (current, target) in skill_inputs.items()],
        "fork": fork,
    }


def apply_target_values(
    editor: Any, target: Mapping[str, Any], skill_inputs: Mapping[str, tuple[QSpinBox, QSpinBox]],
    stage_setter: Callable[[QComboBox, int, object], None],
) -> None:
    """这里只投影先前完整验证的值；范围漂移报错，不静默夹取。"""
    controls = [getattr(editor, name) for name in (
        "current_level", "current_stage", "target_level", "target_stage", "character_toggle", "skills_toggle",
        "fork_current_level", "fork_current_stage", "fork_target_level", "fork_target_stage", "fork_toggle",
    )]
    controls.extend(control for pair in skill_inputs.values() for control in pair)
    with ExitStack() as blockers:
        for control in controls:
            blockers.enter_context(QSignalBlocker(control))
        for prefix in ("current", "target"):
            _apply_level(getattr(editor, f"{prefix}_level"), getattr(editor, f"{prefix}_stage"),
                         target[f"{prefix}_level"], target[f"{prefix}_stage"], stage_setter)
        editor.character_toggle.setChecked(target["character_enabled"])
        editor.skills_toggle.setChecked(target["skills_enabled"])
        for skill in target["skills"]:
            current, desired = skill_inputs[skill["skill_id"]]
            _apply_number(current, skill["current_level"])
            _apply_number(desired, skill["target_level"])
        fork = target["fork"]
        editor.fork_toggle.setChecked(fork is not None and fork["enabled"])
        if fork is not None:
            for prefix in ("current", "target"):
                _apply_level(getattr(editor, f"fork_{prefix}_level"), getattr(editor, f"fork_{prefix}_stage"),
                             fork[f"{prefix}_level"], fork[f"{prefix}_stage"], stage_setter)
        for toggle in (editor.character_toggle, editor.skills_toggle, editor.fork_toggle):
            refresh_participation_tooltip(toggle)


def _apply_number(control: QSpinBox, value: int) -> None:
    if not control.minimum() <= value <= control.maximum():
        raise ValueError("历史输入超出当前控件范围")
    control.setValue(value)


def _apply_level(
    level: QSpinBox, stage: QComboBox, value: int, stage_value: int,
    stage_setter: Callable[[QComboBox, int, object], None],
) -> None:
    _apply_number(level, value)
    stage_setter(stage, value, stage_value)
    if stage.currentData() != stage_value:
        raise ValueError("历史突破阶段与当前资料范围不兼容")
