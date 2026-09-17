# 验证加权分配冻结角色详情但保留工坊基础权重。
"""Pinned allocation keeps workshop weights while freezing role details."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from src.features.weighted_allocation.role_weight_freeze import (
    freeze_official_role_details,
)
from src.services.allocation_context import (
    AllocationContext,
    AllocationRolePreference,
    RoleEquipmentConstraints,
    StaticDatasetReference,
)


class WeightedAllocationRunnerTests(unittest.TestCase):
    def test_freezes_role_details_without_replacing_base_weights(self) -> None:
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
            profile_id=1, profile_version=1, allocation_strategy="role_priority",
            solver_version="test", roles=(role,), candidates=(), shapes=(), suits=(),
        )
        detail = {"profile": {"fork_refinement_level": 5}}
        with patch(
            "src.features.weighted_allocation.role_weight_freeze.load_official_role_detail",
            return_value=detail,
        ):
            frozen, details = freeze_official_role_details(
                context,
                user_database_path=Path("account.sqlite3"),
                shared_database_path=None,
                static_database_path=None,
            )

        self.assertIs(context, frozen)
        self.assertEqual(detail, details[1001])
        self.assertEqual(
            (("AtkUp", 0.4),),
            frozen.roles[0].effective_property_weights,
        )
        self.assertEqual(
            (("AtkUp", 0.4),),
            frozen.roles[0].effective_main_property_weights,
        )


if __name__ == "__main__":
    unittest.main()

