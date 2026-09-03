# 验证加权分配会冻结包含弧盘属性的官方角色边际权重。
"""Pinned allocation must freeze fork-aware official marginal weights."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from src.features.weighted_allocation.role_weight_freeze import (
    freeze_official_role_final_weights,
)
from src.services.allocation_context import (
    AllocationContext,
    AllocationRolePreference,
    RoleEquipmentConstraints,
    StaticDatasetReference,
)


class WeightedAllocationRunnerTests(unittest.TestCase):
    def test_freezes_official_final_weights_before_solver(self) -> None:
        role = AllocationRolePreference(
            character_id=1001,
            ordinal=0,
            priority_group=0,
            target_suit_id=None,
            suit_requirement_mode="none",
            core_main_property_id=None,
            property_weights=(("AtkUp", 0.4),),
            substat_priorities=(),
            property_limits=(),
            equipment=RoleEquipmentConstraints(character_id=1001, cells=()),
            effective_property_weights=(("AtkUp", 0.4),),
            effective_main_property_weights=(("AtkUp", 0.4),),
        )
        context = AllocationContext(
            account_id="test",
            static_dataset=StaticDatasetReference(
                schema_version=32,
                dataset_id="test-dataset",
                importer_version=38,
                built_at_utc="2026-08-31T00:00:00+00:00",
            ),
            snapshot=None,
            profile_id=1, profile_version=1, allocation_strategy="global_optimal",
            solver_version="test", roles=(role,), candidates=(), shapes=(), suits=(),
        )
        detail = {"profile": {"fork_refinement_level": 5}}
        final = {
            "property_weights": {"AtkUp": 0.3, "CritBase": 1.0},
            "main_property_weights": {"AtkUp": 0.2, "CritBase": 0.9},
        }
        with patch(
            "src.features.weighted_allocation.role_weight_freeze.load_official_role_detail",
            return_value=detail,
        ), patch(
            "src.features.weighted_allocation.role_weight_freeze.calculate_official_role_final_weights",
            return_value=final,
        ) as calculation:
            frozen, details = freeze_official_role_final_weights(
                context,
                user_database_path=Path("account.sqlite3"),
                shared_database_path=None,
                static_database_path=None,
            )

        calculation.assert_called_once()
        self.assertEqual(detail, details[1001])
        self.assertEqual(
            (("AtkUp", 0.3), ("CritBase", 1.0)),
            frozen.roles[0].effective_property_weights,
        )
        self.assertEqual(
            (("AtkUp", 0.2), ("CritBase", 0.9)),
            frozen.roles[0].effective_main_property_weights,
        )


if __name__ == "__main__":
    unittest.main()
