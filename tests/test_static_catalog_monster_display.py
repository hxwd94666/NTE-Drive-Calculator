# 怪物与玩法页面的中央术语消费契约测试。
from __future__ import annotations

import unittest

from src.domain.static_catalog_terminology import LocalizedTermRecord
from src.services.static_catalog_monster_display import (
    NAME_UNAVAILABLE,
    display_buff_option,
    display_damage_type,
    display_fight_stage,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


_RESISTANCE_IDS = (
    "chaos",
    "cosmos",
    "incantation",
    "lakshana",
    "nature",
    "psyche",
    "psychically",
    "normal",
)


class _RecordingTerminologySource:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.records = {
            ("damage_resistance", stable_id): LocalizedTermRecord(
                entity_kind="damage_resistance",
                canonical_id=stable_id,
                names=(
                    {} if stable_id == "normal" else {"zh-CN": "中央抗性名称"}
                ),
                source_kind=(
                    "name_missing"
                    if stable_id == "normal"
                    else "formal_localization"
                ),
            )
            for stable_id in _RESISTANCE_IDS
        }
        self.records.update({
            (
                "outer_realm_fight_stage",
                "EAbyssFightStage::FirstHalf",
            ): LocalizedTermRecord(
                entity_kind="outer_realm_fight_stage",
                canonical_id="EAbyssFightStage::FirstHalf",
                names={"zh-CN": "上半场"},
                source_kind="ui_state",
            ),
        })

    def lookup_localized_term(
        self,
        entity_kind: str,
        stable_id: str,
        *,
        context: str | None,
    ) -> LocalizedTermRecord | None:
        self.calls.append((entity_kind, stable_id, context))
        return self.records.get((entity_kind, stable_id))

    def list_fork_campaigns(self) -> tuple[()]:
        return ()


class StaticCatalogMonsterDisplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _RecordingTerminologySource()
        self.terminology = StaticCatalogTerminologyService(self.source)

    def test_all_resistance_ids_are_forwarded_exactly_to_central_projection(self) -> None:
        values = {
            stable_id: display_damage_type(self.terminology, stable_id)
            for stable_id in _RESISTANCE_IDS
        }

        self.assertEqual(NAME_UNAVAILABLE, values.pop("normal"))
        self.assertEqual({"中央抗性名称"}, set(values.values()))
        self.assertEqual(
            [
                ("damage_resistance", stable_id, None)
                for stable_id in _RESISTANCE_IDS
            ],
            self.source.calls,
        )

    def test_normal_resistance_without_formal_name_uses_shared_placeholder(self) -> None:
        self.assertEqual(
            NAME_UNAVAILABLE,
            display_damage_type(self.terminology, "normal"),
        )
        self.assertNotIn("normal", display_damage_type(self.terminology, "normal"))

    def test_outer_stage_requires_the_complete_enum_identity(self) -> None:
        self.assertEqual(
            "上半场",
            display_fight_stage(
                self.terminology,
                "EAbyssFightStage::FirstHalf",
            ),
        )
        self.assertEqual(
            NAME_UNAVAILABLE,
            display_fight_stage(self.terminology, "FirstHalf"),
        )
        self.assertIn(
            ("outer_realm_fight_stage", "FirstHalf", None),
            self.source.calls,
        )

    def test_buff_projection_reuses_central_resistance_name(self) -> None:
        text = display_buff_option(
            self.terminology,
            {
                "effect_kind": "resistance_up",
                "damage_type": "chaos",
                "add_value": 0.25,
                "score": 800,
            },
        )

        self.assertEqual("中央抗性名称 · 数值 0.25 · 额外得分 800", text)

    def test_buff_projection_keeps_missing_normal_name_explicit(self) -> None:
        text = display_buff_option(
            self.terminology,
            {
                "effect_kind": "resistance_up",
                "damage_type": "normal",
                "add_value": 0.1,
                "score": 200,
            },
        )

        self.assertEqual("名称暂未提供 · 数值 0.1 · 额外得分 200", text)
        self.assertNotIn("normal", text)


if __name__ == "__main__":
    unittest.main()
