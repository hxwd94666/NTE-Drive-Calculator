# 验证正式队伍门禁早于图纸和基础求解，取消及来源变化不产生预览或写库。
from concurrent.futures import CancelledError
from dataclasses import replace
from unittest.mock import Mock, patch
import os
from pathlib import Path

import pytest

from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.allocation.axis_selection import AxisAllocationSelectionService
from src.services.allocation.axis_team_preflight import prepare_axis_team_selection
from src.integrations.static_catalog_release import static_catalog_read_unit
from tests.battle_report.analysis.axis_evaluation_catalog_fixture import ROOT
from tests.allocation.axis_team_fixture import attach_native_team
from tests.allocation.test_axis_postprocess_component import axis_facade_case, run_axis_facade

NTE_TEST_TIER = "core"


@pytest.mark.parametrize("kind", ["outside", "unknown", "missing_team", "missing_fixed_gear", "old_single_scope"])
def test_invalid_team_stops_before_blueprints_and_baseline(tmp_path, kind):
    case = axis_facade_case(tmp_path, joint=kind != "old_single_scope")
    case["selected_roles"] = ["灵可", "早雾"]
    expected = "早雾"
    if kind == "outside":
        attach_native_team(case, [1072, 1004, 1009, 1007])
    elif kind == "unknown":
        case["selected_roles"] = ["灵可", "未映射角色"]
        expected = "未映射角色"
    elif kind == "missing_team":
        attach_native_team(case, [1072])
        expected = "完整四人队伍证据"
    elif kind == "missing_fixed_gear":
        from src.storage.sqlite.user_data_dao import UserDataDao
        import json
        with UserDataDao(case["dependencies"].user_database_path) as dao:
            conn = dao._db()
            raw = json.loads(conn.execute("SELECT raw_record_json FROM battle_axis_capture").fetchone()[0])
            raw["calc_capture_context"]["native_scope_builds"]["combat"]["equipment"] = []
            conn.execute("UPDATE battle_axis_capture SET raw_record_json=?", (json.dumps(raw),)); conn.commit()
        expected = "固定队友"
    else:
        expected = "范围与计算成员不一致"
    service = AxisAllocationSelectionService(dependencies=case["dependencies"], context_is_current=lambda _: True)
    case["selection"] = service.select(service.choices(case["selected_roles"]), battle_record_id=case["battle_record_id"],
        selected_role="灵可" if kind == "old_single_scope" else "__all_groups__", detail_scope="all", anchor_mode="original")
    with patch("src.app.facade.create_allocation_executor", side_effect=AssertionError("baseline must not start")) as allocate, \
            patch("src.app.facade.create_blueprint_executor", side_effect=AssertionError("blueprints must not start")) as blueprints:
        with pytest.raises(ValueError, match=expected):
            run_axis_facade(case)
        allocate.assert_not_called()
        blueprints.assert_not_called()


def test_team_source_change_after_preflight_cancels_before_baseline(tmp_path):
    case = axis_facade_case(tmp_path, joint=True)
    allocate, observer = Mock(side_effect=AssertionError("stale baseline")), Mock()
    with patch("src.app.facade.create_allocation_executor", return_value=allocate), \
            patch.object(NteAnalysisCoreClient, "load_battle_page_identity", return_value="b" * 64):
        with pytest.raises(CancelledError):
            run_axis_facade(case, observer=observer)
    allocate.assert_not_called()
    observer.assert_not_called()


def test_foreign_account_selection_stops_before_native_team_request(tmp_path):
    case = axis_facade_case(tmp_path, joint=True)
    case["selection"] = replace(case["selection"], account_id="another-account")
    with patch.object(NteAnalysisCoreClient, "resolve_battle_axis_fixed_team", side_effect=AssertionError("wrong owner")) as resolve:
        with pytest.raises(CancelledError):
            run_axis_facade(case)
        resolve.assert_not_called()


def test_zero_damage_member_is_accepted_without_rewriting_original_selection(tmp_path):
    case = axis_facade_case(tmp_path, joint=True)
    original = case["selection"]
    client = NteAnalysisCoreClient.from_executable(Path(os.environ.get("NTE_BATTLE_AXIS_TEST_EXE") or ROOT / "third_party/analysis-core/bin/nte-analysis-core.exe"), case["dataset_id"])
    with static_catalog_read_unit(case["dependencies"].static_database_path) as unit:
        selected = prepare_axis_team_selection(client, original, ["早雾"],
            user_database_path=case["dependencies"].user_database_path, static_read_unit=unit,
            semantics_path=ROOT / "config/gameplay_effect_semantics.json")
    assert original.participating_character_ids == (1072,)
    assert original.team_source_input_digest is None
    assert selected.participating_character_ids == (1003, 1004, 1008, 1072)
    assert selected.selected_team_character_ids == (1003,) and len(selected.team_source_input_digest) == 64
