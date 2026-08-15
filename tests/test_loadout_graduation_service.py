# 验证配装毕业度服务的评分与属性汇总。
from __future__ import annotations

import os
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QWidget

from src.features.inventory.equipment_graduation_controller import (
    request_equipment_graduation_rate,
)
from src.services import official_role_graduation_service as graduation_service


def test_graduation_rate_uses_role_page_damage_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        graduation_service,
        "graduation_benchmark_damage",
        lambda _detail: 200.0,
    )
    monkeypatch.setattr(
        graduation_service,
        "calculate_official_role_margins",
        lambda _detail, context_key: {
            "damage": 150.0 if context_key == "saved" else 100.0
        },
    )

    assert graduation_service.graduation_rate({}, "saved") == 75.0
    assert graduation_service.graduation_rate({}, "current") == 50.0


def test_loadout_graduation_loader_uses_requested_context(monkeypatch) -> None:
    captured = {}

    def fake_detail(path, character_id, **kwargs):
        captured.update({
            "path": path,
            "character_id": character_id,
            **kwargs,
        })
        return {"detail": True}

    monkeypatch.setattr(
        "src.services.official_role_page_service.load_official_role_detail",
        fake_detail,
    )
    monkeypatch.setattr(
        graduation_service,
        "graduation_rate",
        lambda detail, context_key: 88.8
        if detail == {"detail": True} and context_key == "saved"
        else None,
    )

    result = graduation_service.load_official_role_graduation_rate(
        Path("account.sqlite3"),
        1003,
        context_key="saved",
    )

    assert result == 88.8
    assert captured["character_id"] == 1003
    assert captured["include_inventory_contexts"] is True


def test_loadout_summary_reuses_role_page_graduation_tooltip(monkeypatch) -> None:
    detail = {"graduation_template": {"equipment": []}}
    monkeypatch.setattr(
        "src.services.official_role_page_service.load_official_role_detail",
        lambda *_args, **_kwargs: detail,
    )
    monkeypatch.setattr(graduation_service, "graduation_rate", lambda *_args: 66.6)

    summary = graduation_service.load_official_role_graduation_summary(
        Path("account.sqlite3"),
        1003,
        context_key="saved",
    )

    assert summary.rate == 66.6
    assert summary.tooltip == graduation_service.graduation_tooltip(detail)


def test_graduation_summary_resolves_the_selected_secondary_slot(monkeypatch) -> None:
    captured = {}
    detail = {
        "equipment_contexts": {
            "saved": {"slot_id": 18},
            "saved:27": {"slot_id": 27},
        },
        "graduation_template": {"equipment": []},
    }
    monkeypatch.setattr(
        "src.services.official_role_page_service.load_official_role_detail",
        lambda *_args, **_kwargs: detail,
    )
    monkeypatch.setattr(
        graduation_service,
        "graduation_rate",
        lambda _detail, context_key: captured.setdefault("context_key", context_key) or 73.2,
    )

    graduation_service.load_official_role_graduation_summary(
        Path("account.sqlite3"),
        1003,
        context_key="saved:27",
    )

    assert captured["context_key"] == "saved:27"


def test_graduation_summary_maps_selected_primary_projection_back_to_saved(monkeypatch) -> None:
    captured = {}
    detail = {
        "equipment_contexts": {"saved": {"slot_id": 18}},
        "graduation_template": {"equipment": []},
    }
    monkeypatch.setattr(
        "src.services.official_role_page_service.load_official_role_detail",
        lambda *_args, **_kwargs: detail,
    )
    monkeypatch.setattr(
        graduation_service,
        "graduation_rate",
        lambda _detail, context_key: captured.setdefault("context_key", context_key) or 73.2,
    )

    graduation_service.load_official_role_graduation_summary(
        Path("account.sqlite3"),
        1003,
        context_key="saved:18",
    )

    assert captured["context_key"] == "saved"


def test_cached_loadout_graduation_does_not_start_worker() -> None:
    app = QApplication.instance() or QApplication([])
    label = QLabel()
    state = {
        "_graduation_rate_loaded": True,
        "_graduation_rate": 92.34,
        "_graduation_tooltip": "角色页同款毕业基准说明",
    }
    tooltip_widget = QWidget()

    request_equipment_graduation_rate(
        object(),
        "测试角色",
        state,
        label,
        tooltip_widget,
        database_path=Path("unused.sqlite3"),
    )

    assert label.text() == "92.3%"
    assert label.toolTip() == "角色页同款毕业基准说明"
    assert tooltip_widget.toolTip() == "角色页同款毕业基准说明"
    del app


def test_graduation_tooltip_is_applied_to_title_value_and_container() -> None:
    app = QApplication.instance() or QApplication([])
    value_label = QLabel()
    title_label = QLabel("毕业率")
    container = QWidget()
    state = {
        "_graduation_rate_loaded": True,
        "_graduation_rate": 88.8,
        "_graduation_tooltip": "统一的毕业率说明",
    }

    request_equipment_graduation_rate(
        object(),
        "测试角色",
        state,
        value_label,
        (title_label, container),
        database_path=Path("unused.sqlite3"),
    )

    assert value_label.toolTip() == "统一的毕业率说明"
    assert title_label.toolTip() == value_label.toolTip()
    assert container.toolTip() == value_label.toolTip()
    del app


def test_selected_loadout_graduation_resolves_in_background(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        graduation_service,
        "load_official_role_graduation_summary",
        lambda *_args, **_kwargs: graduation_service.OfficialRoleGraduationSummary(
            rate=77.7,
            tooltip="角色页同款毕业基准说明",
        ),
    )
    state = {"_character_id": 1003}
    window = QWidget()
    window.app_context = SimpleNamespace(generation=4)
    window._saved_equipment_states = {"测试角色": state}
    label = QLabel(parent=window)
    tooltip_widget = QWidget(parent=window)

    request_equipment_graduation_rate(
        window,
        "测试角色",
        state,
        label,
        tooltip_widget,
        database_path=Path("account.sqlite3"),
    )
    deadline = monotonic() + 2.0
    while not state.get("_graduation_rate_loaded") and monotonic() < deadline:
        app.processEvents()
        sleep(0.01)

    assert state["_graduation_rate"] == 77.7
    assert label.text() == "77.7%"
    assert label.toolTip() == "角色页同款毕业基准说明"
    assert tooltip_widget.toolTip() == "角色页同款毕业基准说明"
    for worker in list(getattr(window, "_equipment_graduation_workers", {}).values()):
        worker.wait(1000)
    window.deleteLater()
    app.processEvents()
    del app


def test_selected_loadout_graduation_requests_its_own_slot_context(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    captured = {}

    def load_summary(*_args, **kwargs):
        captured["context_key"] = kwargs["context_key"]
        return graduation_service.OfficialRoleGraduationSummary(rate=77.7, tooltip="说明")

    monkeypatch.setattr(graduation_service, "load_official_role_graduation_summary", load_summary)
    state = {"_character_id": 1003, "_loadout_slot_id": 27}
    window = QWidget()
    window.app_context = SimpleNamespace(generation=4)
    window._saved_equipment_states = {"slot:27": state}
    label = QLabel(parent=window)

    request_equipment_graduation_rate(
        window,
        "测试角色",
        state,
        label,
        database_path=Path("account.sqlite3"),
    )
    deadline = monotonic() + 2.0
    while not state.get("_graduation_rate_loaded") and monotonic() < deadline:
        app.processEvents()
        sleep(0.01)

    assert captured["context_key"] == "saved:27"
    for worker in list(getattr(window, "_equipment_graduation_workers", {}).values()):
        worker.wait(1000)
    window.deleteLater()
    app.processEvents()
    del app
