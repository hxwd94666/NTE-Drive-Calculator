# 提供公开合成的轴批量协议事实，用于独立身份、四态和守恒验收。
from __future__ import annotations

from copy import deepcopy


def gear(main=0.05):
    return [
        {"uid_slot": 4, "uid_serial": 100, "kind": "core", "item_id": "Core_Test",
         "suit_id": "Suit1", "geometry": "Core", "grid_count": 0, "quality": "Purple",
         "level": 0, "max_level": 20, "locked": False,
         "stats": [{"stat_group": "main", "ordinal": 0, "property_id": "AtkUp",
                    "value": main, "is_percent": True}]},
        {"uid_slot": 3, "uid_serial": 1, "kind": "module", "item_id": "Module_Test",
         "suit_id": "Suit1", "geometry": "EquipmentGeometry_Hen2", "grid_count": 2,
         "quality": "Gold", "level": 0, "max_level": 20, "locked": False,
         "stats": [{"stat_group": "sub", "ordinal": 0, "property_id": "AtkAdd",
                    "value": 1.0, "is_percent": False}]},
    ]


def wire_request():
    return {"version": 1, "input_identity": {"request_id": "public-fixture", "input_sha256": "0" * 64},
            "battle_request": {"account_id": "fixture", "generation": 3, "battle_record_id": 7,
                               "user_database_path": "F:/fixture/user.sqlite3",
                               "static_database_path": "F:/fixture/static.sqlite3",
                               "semantics_path": "F:/fixture/semantics.json", "detail_level": "hit"},
            "anchor_mode": "original", "selected_character_id": 1072, "equipment_value_mode": "actual_level",
            "baseline_equipment_by_character": [{"character_id": 1072, "equipment": gear()}],
            "candidates": [{"candidate_id": "candidate-1", "equipment": gear(0.10)}]}


def response_row(identifier, candidate, *, baseline=110.0):
    return {"candidate_id": identifier, "status": "complete", "model_id": "frozen_axis_expected_v1",
            "unit": "reported_weight_expected_damage", "scope": "all_frozen_axis_events_v1",
            "anchor_value": 100.0, "candidate_value": candidate,
            "delta_from_anchor": candidate - 100.0, "delta_from_baseline": candidate - baseline,
            "quantification": {"status": "complete", "basis_damage": 100.0,
                               "fully_quantified_damage": 100.0, "partially_quantified_damage": 0.0,
                               "unavailable_damage": 0.0, "proven_unchanged_damage": 0.0,
                               "quantified_increment": candidate - 100.0, "gaps": []}, "gaps": []}


def wire_response(request=None):
    request = request or wire_request()
    return {"schema_version": "nte-analysis-response-v1", "engine_version": "0.3.0",
            "dataset_version": "fixture-static", "batch_kind": "battle_axis_evaluate_v1", "version": 1,
            "input_identity": deepcopy(request["input_identity"]),
            "source_input_digest": "a" * 64,
            "frozen_identity": {key: "1" * 64 for key in
                                ("facts_sha256", "anchor_sha256", "baseline_sha256", "axis_sha256")},
            "model_id": "frozen_axis_expected_v1", "equipment_value_mode": "actual_level",
            "critical_policy": "expected_with_frozen_observed_triggers",
            "scope": {"kind": "all_frozen_axis_events_v1", "selected_character_id": 1072,
                      "anchor_mode": "original", "range_start_us": 0, "range_end_us": 1_000_000,
                      "outgoing_event_count": 1, "includes_linked_team_effects": True,
                      "event_domain": "saved_frozen_observed_events", "source_axis_complete": False,
                      "capture_state": "finalized", "native_transport_complete": True, "source_coverage": "unknown"},
            "baseline": response_row("baseline", 110.0),
            "candidates": [response_row("candidate-1", 125.0)]}
