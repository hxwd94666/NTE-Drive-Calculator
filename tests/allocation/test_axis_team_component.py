# 在临时战报与正式静态目录上验证 Rust 队伍预检只读、零伤队员及来源摘要，不读取个人账号。
import os
from pathlib import Path

from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.battle_report.analysis.axis_evaluation_catalog_fixture import ROOT, create_axis_catalog_case
from tests.allocation.axis_team_fixture import attach_native_team

NTE_TEST_TIER = "core"


def test_native_database_team_preflight_uses_frozen_evidence_and_is_read_only(tmp_path):
    case = create_axis_catalog_case(tmp_path, core_suit_id="Suit1")
    deps = case["dependencies"]
    exe = Path(os.environ.get("NTE_BATTLE_AXIS_TEST_EXE") or ROOT / "third_party/analysis-core/bin/nte-analysis-core.exe")
    client = NteAnalysisCoreClient.from_executable(exe, case["dataset_id"])
    selector = dict(account_id=deps.account_id, generation=deps.generation,
                    user_database_path=str(deps.user_database_path), static_database_path=str(deps.static_database_path),
                    semantics_path=str(ROOT / "config/gameplay_effect_semantics.json"),
                    battle_record_id=case["battle_record_id"], detail_level="hit")
    missing = client.resolve_battle_axis_team(selector, [1072])
    assert not missing["ready"] and missing["team_character_ids"] == []
    ids = [1072, 1003, 1004, 1009]
    attach_native_team(case, ids)
    with UserDataDao(deps.user_database_path) as dao:
        before = list(dao._db().iterdump())
    result = client.resolve_battle_axis_team(selector, [1003])
    assert result["ready"] and result["team_character_ids"] == sorted(ids)
    assert result["fixed_character_ids"] == [1004, 1009, 1072]
    assert result["source_input_digest"] != missing["source_input_digest"]
    assert result["source_input_digest"] == client.load_battle_page_identity(selector)
    outside = client.resolve_battle_axis_team(selector, [1003, 9999])
    assert outside["outside_character_ids"] == [9999] and not outside["ready"]
    with UserDataDao(deps.user_database_path) as dao:
        assert list(dao._db().iterdump()) == before


def test_unscoped_abyss_hit_is_excluded_from_the_same_native_allocation_objective(tmp_path):
    """合成一击无半场归属，穿过真实预检和完整 Rust 作业；原战报不变。"""
    import json
    from tests.allocation.test_axis_postprocess_component import axis_facade_case, run_axis_facade

    case = axis_facade_case(tmp_path, joint=True)
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        conn = dao._db()
        raw = json.loads(conn.execute("SELECT raw_record_json FROM battle_axis_capture").fetchone()[0])
        attempts = raw["native_capture"]["scopeAttempts"]
        attempts["upper"] = attempts.pop("combat")
        context = raw["calc_capture_context"]
        context["native_scope_builds"]["upper"] = context["native_scope_builds"].pop("combat")
        context["native_scope_validation"]["upper"] = context["native_scope_validation"].pop("combat")
        rows = conn.execute("SELECT sequence_order,direction FROM battle_hit_evidence ORDER BY sequence_order").fetchall()
        assert len(rows) >= 2 and rows[-1]["direction"] == "outgoing"
        outgoing = sum(row["direction"] == "outgoing" for row in rows)
        conn.execute("UPDATE battle_axis_capture SET raw_record_json=?", (json.dumps(raw),))
        conn.execute("UPDATE battle_hit_evidence SET abyss_half='upper'")
        conn.execute("UPDATE battle_hit_evidence SET abyss_half=NULL WHERE sequence_order=?", (rows[-1]["sequence_order"],))
        conn.commit()
        before = list(conn.iterdump())
    plans, _ = run_axis_facade(case)
    result = plans["灵可"]["damage_optimization"]
    assert result["status"] == "completed", result
    assert result["scope"]["excluded_unscoped_event_count"] == 1
    assert result["scope"]["outgoing_event_count"] == outgoing - 1
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        assert list(dao._db().iterdump()) == before
