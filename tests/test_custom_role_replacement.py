# 测试自建角色装备替换。
"""Regression coverage for custom-role generic replacement routing."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget

from src.features.inventory.equipment import plan_optimizer as optimizer


def test_custom_role_plan_routes_to_generic_weight_evaluation(monkeypatch) -> None:
    class Dao:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get_loadout_plan(self, plan_id: int):
            assert plan_id == 21
            return {"plan_id": plan_id, "character_id": 1_500_000_001}

        def get_active_loadout_plan_for_role(self, _role_name: str):
            raise AssertionError("指定槽位方案应优先按 plan_id 查询")

        def list_custom_characters(self):
            return [{"character_id": 1_500_000_001, "name_zh": "自建角色"}]

    monkeypatch.setattr(optimizer, "UserDataDao", lambda _path: Dao())

    assert optimizer._saved_plan_uses_custom_character(
        "account.sqlite3",
        "自建角色",
        plan_id=21,
    )


def test_official_role_plan_does_not_use_generic_custom_route(monkeypatch) -> None:
    class Dao:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get_loadout_plan(self, _plan_id: int):
            return {"character_id": 1003}

        def get_active_loadout_plan_for_role(self, _role_name: str):
            return None

        def list_custom_characters(self):
            return [{"character_id": 1_500_000_001, "name_zh": "自建角色"}]

    monkeypatch.setattr(optimizer, "UserDataDao", lambda _path: Dao())

    assert not optimizer._saved_plan_uses_custom_character(
        "account.sqlite3",
        "官方角色",
        plan_id=8,
    )


def test_custom_replacement_weights_use_equipment_card_labels(monkeypatch) -> None:
    class Dao:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get_character_weight_preferences(self, character_id: int):
            assert character_id == 1_500_000_001
            return {
                "property_weights": {"CritBase": 0.8, "AtkAdd": 0.25},
                "main_property_weights": {"CritDamageBase": 0.9},
            }

    monkeypatch.setattr(optimizer, "UserDataDao", lambda _path: Dao())

    weights, main_weights = optimizer._custom_plan_weight_overrides(
        "account.sqlite3",
        {"character_id": 1_500_000_001},
    )

    assert weights == {"暴击率%": 0.8, "攻击力": 0.25}
    assert main_weights == {"暴击伤害%": 0.9}


@pytest.mark.parametrize("boundary", ["confirm", "generation"])
def test_custom_optimizer_expands_only_frozen_eligible_pool_and_keeps_virtual_target(monkeypatch, boundary):
    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    owner.app_context = SimpleNamespace(generation=1)
    weights = {"value": 1.0}
    owner.roles_db = {"自建角色": {"weights": weights}}
    reads, saved, rendered, refreshed = [], [], [], []
    def card(_label, _main, _sub, _shape, uid, frozen_weights, score_grade, *_args, **kwargs):
        widget = QFrame()
        widget.setFixedSize(240, 58)
        layout = QVBoxLayout(widget)
        if kwargs.get("replacement_callback") is not None:
            rendered.append((uid, score_grade[0], dict(frozen_weights)))
            button = QPushButton(uid)
            button.setObjectName("candidateChoice")
            button.clicked.connect(kwargs["replacement_callback"])
            layout.addWidget(button)
        else:
            layout.addWidget(QLabel("当前虚拟占位"))
        return widget
    owner.equipment_presentation = SimpleNamespace(equipment_card=card, shape_area=lambda *_args: 3,
        score_drive=lambda sub, _shape, frozen_weights, _quality: sub.get("value", 0)*frozen_weights["value"])
    owner.invalidate_saved_equipment_cache = lambda: None
    owner._refresh_equip = lambda **_kwargs: refreshed.append(True)
    current = {"uid": "nte-module-0-1", "_uid_slot": 0, "_uid_serial": 1, "shape_id": "H_3", "virtual": True}
    pool = [{"uid": f"nte-module-17-{index}", "_uid_slot": 17, "_uid_serial": index,
             "shape_id": "H_3", "sub_stats": {"value": float(index)}} for index in range(1, 65)]
    pool.append({**pool[-1], "uid": "blocked", "sub_stats": {"value": 999.0}, "_used_by": ("其他角色",)})
    plan = {"character_id": 1_500_000_001, "source_snapshot_id": 7,
            "assignments": [{"kind": "module", "uid_slot": 0, "uid_serial": 1}], "payload": {}}
    def read(*_args, **_kwargs):
        reads.append(True)
        return plan, current, pool, [], None
    monkeypatch.setattr(optimizer, "_equipment_paths", lambda _owner: (Path("unused"), Path("unused"), Path("unused")))
    monkeypatch.setattr(optimizer, "_sqlite_replacement_candidates", read)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    def inspect(dialog):
        dialog.show()
        app.processEvents()
        group = dialog.findChild(QWidget, "equipmentReplacementCandidateGroup")
        assert len(rendered) == 30
        group.findChild(QPushButton, "equipmentReplacementAllCandidates").click()
        group.findChild(QPushButton, "equipmentReplacementAllCandidates").click()
        weights["value"] = 99.0
        if boundary == "generation":
            owner.app_context.generation += 1
            QTest.qWait(50)
            assert len(rendered) == 30 and not dialog.isVisible()
        else:
            for _ in range(100):
                if len(rendered) == 64:
                    break
                QTest.qWait(5)
            assert [row[0] for row in rendered] == [f"nte-module-17-{index}" for index in range(64, 0, -1)]
            assert all(row[2] == {"value": 1.0} for row in rendered)
            QCoreApplication.sendPostedEvents(None, QEvent.LayoutRequest)
            app.processEvents()
            last = group.findChildren(QPushButton, "candidateChoice")[-1]
            group.findChild(QScrollArea).ensureWidgetVisible(last)
            app.processEvents()
            QTest.mouseClick(last, Qt.LeftButton)
        return dialog.result()
    monkeypatch.setattr(QDialog, "exec", inspect)
    try:
        optimizer._optimize_saved_equipment(owner, "自建角色", "drive", current["uid"],
            rank_by_damage=False, exclude_used_by_others=True,
            replacement_persister=lambda selected, score, old_score: saved.append((selected["uid"], score, old_score)))
        assert reads == [True]
        assert saved == ([("nte-module-17-1", 1.0, 0.0)] if boundary == "confirm" else [])
        assert bool(refreshed) == (boundary == "confirm")
    finally:
        owner.close()
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
