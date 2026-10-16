# 验证原值边界只挑选冻结身份行、隔离副本并及时取消，不在 Python 计算属性。
from __future__ import annotations

from concurrent.futures import CancelledError
from copy import deepcopy
from unittest.mock import patch

import pytest

from tests.allocation.test_direct_postprocess import fixture
from src.services.allocation.direct_projection import freeze_direct_projection_inputs

NTE_TEST_TIER = "core"


def test_raw_projection_serializer_preserves_original_units_and_omits_page_context():
    request, _, _, raw, detail = fixture()
    detail["equipment_contexts"] = {"stale": {"items": ["not-consumed"]}}
    detail["static_database_path"] = "not-consumed"
    before = deepcopy((raw, detail))
    with patch("src.services.official_role.attributes.calculate_official_role_combat_stat_components",
               side_effect=AssertionError("Python panel calculation called")), \
            patch("src.domain.equipment.official_stats.calculate_official_equipment_stats",
                  side_effect=AssertionError("Python equipment calculation called")):
        result = freeze_direct_projection_inputs(request, (("A", detail),), raw)
    assert result["inventory"]["nte-core-3-30"]["main_stats"][0]["value"] == 0.10
    assert result["details"]["A"]["shape_bonus"]["properties"][0]["display_value"] == 5.0
    assert len(result["details"]["A"]["growth_rows"]) == 1
    assert "equipment_contexts" not in result["details"]["A"]
    assert "static_database_path" not in result["details"]["A"]
    assert (raw, detail) == before
    raw[0]["official"]["sub_stats"][0]["value"] = 999.0
    assert result["inventory"]["nte-module-1-10"]["sub_stats"][0]["value"] == 0.06


def test_raw_projection_serializer_cancels_or_rejects_incomplete_input_without_partial_output():
    request, _, _, raw, detail = fixture()
    def cancelled():
        raise CancelledError
    with pytest.raises(CancelledError):
        freeze_direct_projection_inputs(request, (("A", detail),), raw, checkpoint=cancelled)
    with pytest.raises(ValueError):
        freeze_direct_projection_inputs(request, (("A", detail),), raw[:-1])
    with pytest.raises(ValueError):
        freeze_direct_projection_inputs(request, (("A", detail), ("A", detail)), raw)
