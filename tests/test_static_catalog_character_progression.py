# 验证角色养成结果使用公共正式术语投影。
from __future__ import annotations

import unittest

from src.domain.progression_stamina import (
    IdentificationLevelProjection,
    MaterialDeficit,
    ProgressionStaminaResult,
    StaminaPlanStatus,
)
from src.domain.static_catalog_terminology import LocalizedTermRecord
from src.features.static_catalog.domain_pages.character_progression import (
    project_progression_result,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


NTE_TEST_TIER = "core"


class _TerminologySource:
    def lookup_localized_term(self, entity_kind, stable_id, *, context):
        if (entity_kind, stable_id, context) != (
            "item",
            "gold",
            "progression_cost",
        ):
            return None
        return LocalizedTermRecord(
            entity_kind="item",
            canonical_id="Fons",
            names={"zh-CN": "方斯"},
            text_table="/Game/Text/ST_Item.ST_Item",
            text_key="item_Fons_name",
        )


class CharacterProgressionProjectionTests(unittest.TestCase):
    def test_cost_names_consume_public_terminology_projection(self) -> None:
        projection = project_progression_result(
            ProgressionStaminaResult(
                status=StaminaPlanStatus.UNAVAILABLE,
                identification=IdentificationLevelProjection(60, 7, 7, False),
                deficits=(MaterialDeficit("gold", 30, 10, 20),),
                runs=(),
                known_stamina=0,
                total_stamina=None,
                unresolved_item_ids=("gold",),
                gaps=("material_yield_unavailable",),
            ),
            terminology=StaticCatalogTerminologyService(_TerminologySource()),
        )

        self.assertIn("方斯 × 20", projection.text)
        self.assertNotIn("甲硬币", projection.text)
        self.assertNotIn("金币", projection.text)
        self.assertNotIn("gold", projection.text)
        more_info_text = "\n".join(
            f"{label} {value}" for label, value in projection.more_info
        )
        self.assertIn("gold", more_info_text)
        self.assertIn("Fons", more_info_text)
        self.assertIn("item_Fons_name", more_info_text)


if __name__ == "__main__":
    unittest.main()
