# 编排配装毕业度计算与界面状态更新。
"""Asynchronous graduation-rate projection for the selected loadout role."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QLabel, QWidget

from src.app.workers import WorkerThread
from src.utils.logger import logger


def request_equipment_graduation_rate(
    window: Any,
    role_name: str,
    state: dict[str, Any],
    value_label: QLabel,
    tooltip_widget: QWidget | tuple[QWidget, ...] | None = None,
    *,
    database_path: str | Path,
) -> None:
    """Resolve one selected role's graduation rate without blocking Qt."""

    tooltip_widgets = (
        tooltip_widget
        if isinstance(tooltip_widget, tuple)
        else (tooltip_widget,) if tooltip_widget is not None else ()
    )

    def apply_tooltip(tooltip: str) -> None:
        value_label.setToolTip(tooltip)
        for widget in tooltip_widgets:
            widget.setToolTip(tooltip)

    if state.get("_graduation_rate_loaded"):
        value = state.get("_graduation_rate")
        value_label.setText(f"{float(value):.1f}%" if value is not None else "--")
        tooltip = str(state.get("_graduation_tooltip") or "")
        apply_tooltip(tooltip)
        return
    character_id = state.get("_character_id")
    if character_id is None:
        value_label.setText("--")
        return
    app_context = getattr(window, "app_context", None)
    generation = getattr(app_context, "generation", None)
    mode = "game" if state.get("_game_mode") else "saved"
    slot_id = state.get("_loadout_slot_id")
    context_key = (
        "current"
        if mode == "game"
        else f"saved:{int(slot_id)}"
        if slot_id is not None
        else "saved"
    )
    token = object()
    tokens = getattr(window, "_equipment_graduation_tokens", None)
    if not isinstance(tokens, dict):
        tokens = {}
        window._equipment_graduation_tokens = tokens
    cache_key = (mode, role_name, context_key)
    tokens[cache_key] = token

    def target():
        from src.services.official_role_graduation_service import (
            load_official_role_graduation_summary,
        )

        return load_official_role_graduation_summary(
            database_path,
            int(character_id),
            context_key=context_key,
        )

    def still_current() -> bool:
        current_context = getattr(window, "app_context", None)
        current_states = (
            getattr(window, "_game_loadout_states", {})
            if mode == "game"
            else getattr(window, "_saved_equipment_states", {})
        ) or {}
        return (
            tokens.get(cache_key) is token
            and getattr(current_context, "generation", None) == generation
            and any(candidate is state for candidate in current_states.values())
        )

    def loaded(summary) -> None:
        if not still_current():
            return
        value = summary.rate
        tooltip = str(summary.tooltip or "")
        state["_graduation_rate"] = float(value) if value is not None else None
        state["_graduation_tooltip"] = tooltip
        state["_graduation_rate_loaded"] = True
        try:
            value_label.setText(
                f"{float(value):.1f}%" if value is not None else "--"
            )
            apply_tooltip(tooltip)
        except RuntimeError:
            pass

    def failed(error) -> None:
        if still_current():
            state["_graduation_rate"] = None
            state["_graduation_tooltip"] = ""
            state["_graduation_rate_loaded"] = True
            logger.warning(f"配装毕业率计算失败 role={role_name}: {error}")
            try:
                value_label.setText("--")
                apply_tooltip("")
            except RuntimeError:
                pass

    worker = WorkerThread(target=target, parent=window)
    workers = getattr(window, "_equipment_graduation_workers", None)
    if not isinstance(workers, dict):
        workers = {}
        window._equipment_graduation_workers = workers
    workers[token] = worker
    worker.result_ready.connect(loaded)
    worker.error.connect(failed)
    worker.finished.connect(lambda: workers.pop(token, None))
    worker.start()
