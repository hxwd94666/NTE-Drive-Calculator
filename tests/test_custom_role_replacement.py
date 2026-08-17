# 测试自建角色装备替换。
"""Regression coverage for custom-role generic replacement routing."""

from __future__ import annotations

from src.features.inventory import equipment_plan_optimizer as optimizer


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
