# 通过真实独立候选组件验证发行图鉴与多击正式临时战报的冻结批量评价链。
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.battle_report.analysis.axis_evaluation_catalog_fixture import create_axis_catalog_case, ROOT
from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.analysis.load import BattleReportAnalysisLoadRequest
from src.services.battle_report.analysis.native_page import BattleNativePageService


NTE_TEST_TIER = "core"


def case_owner(case):
    executable = Path(os.environ.get("NTE_BATTLE_AXIS_TEST_EXE") or ROOT / "third_party/analysis-core/bin/nte-analysis-core.exe")
    client = NteAnalysisCoreClient.from_executable(executable, case["dataset_id"])
    service = BattleNativePageService(client=client, dependencies=case["dependencies"],
        semantics_path=ROOT / "config/gameplay_effect_semantics.json", context_is_current=lambda _: True)
    return service, client


def freeze(service, case, candidates):
    return service.freeze_axis_evaluation(BattleReportAnalysisLoadRequest(case["battle_record_id"]),
        anchor_mode="original", selected_character_id=1072,
        baseline_equipment_by_character=[{"character_id": 1072, "equipment": case["baseline_equipment"]}],
        candidates=candidates)


def test_formal_catalog_multihit_batch_is_complete_repeatable_and_readonly(tmp_path):
    case = create_axis_catalog_case(tmp_path, core_suit_id="Suit1")
    service, client = case_owner(case)
    stronger = {"candidate_id": "strong", "equipment": case["candidate_equipment"]}
    weaker = deepcopy(stronger)
    weaker["candidate_id"] = "weak"
    weaker["equipment"][0]["main_stats"][0]["value"] = 0.01
    gap = deepcopy(stronger)
    gap["candidate_id"] = "unit-gap"
    gap["equipment"][0]["main_stats"][0]["is_percent"] = False
    frozen = freeze(service, case, [stronger, weaker, gap])
    before = hashlib.sha256(case["dependencies"].user_database_path.read_bytes()).hexdigest()
    with patch.object(client, "_run", wraps=client._run) as run, \
            patch.object(service, "load", side_effect=AssertionError("candidate page loop")), \
            patch("src.services.battle_report.analysis.native_page.persist_native_snapshot", side_effect=AssertionError("candidate persisted")):
        result = service.evaluate_axis(frozen)
        # 身份前验与后验保护同一冻结输入，中间只有一次批量评价，未循环候选页面。
        sent = [json.loads(call.args[0]) for call in run.call_args_list]
        assert [item["batch_kind"] for item in sent] == ["battle_page_v1", "battle_axis_evaluate_v1", "battle_page_v1"]
        assert sent[0]["request"]["detail_level"] == sent[2]["request"]["detail_level"] == "identity"
        assert sent[0]["request"] == sent[2]["request"]
    assert result["baseline"]["status"] == "complete", result["baseline"]
    assert result["scope"]["outgoing_event_count"] == 3
    assert result["scope"]["event_domain"] == "saved_frozen_observed_events"
    strong, weak, unavailable = result["candidates"]
    assert strong["status"] == "complete" and strong["delta_from_baseline"] > 0.0
    assert weak["status"] == "complete" and weak["delta_from_baseline"] < 0.0
    assert unavailable["status"] == "unavailable" and unavailable["candidate_value"] is None
    assert unavailable["gaps"][0]["code"] == "axis_equipment_property_unit_mismatch"
    repeated = service.evaluate_axis(frozen)
    assert repeated == result
    reordered = service.evaluate_axis(freeze(service, case, [gap, weaker, stronger]))
    assert reordered["frozen_identity"] == result["frozen_identity"]
    assert {row["candidate_id"]: row for row in reordered["candidates"]} == {row["candidate_id"]: row for row in result["candidates"]}
    assert strong["candidate_value"] == pytest.approx(result["baseline"]["candidate_value"] + strong["delta_from_baseline"])
    assert hashlib.sha256(case["dependencies"].user_database_path.read_bytes()).hexdigest() == before


def test_formal_unknown_event_never_exposes_partial_projection_as_full_damage(tmp_path):
    case = create_axis_catalog_case(tmp_path, unknown_event=True, core_suit_id="Suit1")
    service, _ = case_owner(case)
    result = service.evaluate_axis(freeze(service, case, [{"candidate_id": "strong", "equipment": case["candidate_equipment"]}]))
    assert result["baseline"]["status"] in {"partial", "unavailable"}
    assert result["baseline"]["candidate_value"] is None
    assert result["candidates"][0]["candidate_value"] is None
    assert result["candidates"][0]["gaps"][0]["code"] == "axis_baseline_unquantified"


def test_formal_conditional_set_keeps_buff_evidence_gap_instead_of_completing_model(tmp_path):
    case = create_axis_catalog_case(tmp_path)
    service, _ = case_owner(case)
    result = service.evaluate_axis(freeze(service, case, [{"candidate_id": "strong", "equipment": case["candidate_equipment"]}]))
    assert result["baseline"]["status"] == "partial"
    assert result["baseline"]["candidate_value"] is None
    assert "buff_state_inferred" in {gap["code"] for gap in result["baseline"]["gaps"]}
    assert result["candidates"][0]["candidate_value"] is None
