# 用发行正式图鉴和公开finalized长页采集流程建立多击临时战报，不读取个人账号。
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from src.domain.equipment.properties.equipment_normalizer import calculate_drive_main_stats
from src.integrations.native_core.nte_core_battle import parse_battle_summary
from src.integrations.static_catalog.static_catalog_release import StaticCatalogReleaseReader
from src.observability import OperationContext
from src.services.battle_report.history.service import BattleReportHistoryService
from src.services.battle_report.persistence import BattleReportPersistenceDependencies, BattleReportPersistenceService
from src.storage.sqlite.user_data_dao import UserDataDao


ROOT = Path(__file__).resolve().parents[3]


def create_axis_catalog_case(directory: Path, *, unknown_event=False, core_suit_id=None, second_character=False, account_id="axis-evaluation-public", main_character_id=1072, capture_suffix="", merge_inventory=False, hit_count=3):
    """Reuse the mature long-page persistence path; only UIDs, rolls and recorded hit amounts are synthetic."""
    database, static_path = directory / "synthetic-axis-user.sqlite3", ROOT / "data/game_static.sqlite3"
    capture_operation = "capture-axis-public" + capture_suffix
    source_record = "battle-axis-public" + capture_suffix
    with StaticCatalogReleaseReader().read_unit(static_path) as unit:
        plan = unit.dao.get_equipment_plan(main_character_id)
        templates = {row["item_id"]: row for row in unit.dao.list_equipment_items()}
        if core_suit_id is not None:
            # complete 场景明示选择另一件正式卡带，不删除默认 Suit2 的条件状态缺口。
            plan["core_item_id"] = next(row["item_id"] for row in templates.values()
                if row["kind"] == "core" and row["suit_id"] == core_suit_id and row["quality"] == "ORANGE")
        dataset = unit.release.dataset_id
        items = []
        for ordinal, identifier in enumerate([plan["core_item_id"], *plan["module_item_ids"]]):
            template = templates[identifier]
            main = [{"property_id": "AtkUp", "value": 0.05, "percent": True, "names": {}}] if ordinal == 0 else [
                {"property_id": key, "value": value, "percent": False, "names": {}} for key, value in zip(
                    ("AtkAdd", "HPMaxAdd"), calculate_drive_main_stats(template["grid_count"], "Gold").values())]
            items.append({"uid": {"slot": 4 if ordinal == 0 else 3, "serial": 100 if ordinal == 0 else ordinal},
                "kind": template["kind"], "item_id": identifier, "suit_id": template["suit_id"],
                "geometry": template["geometry_id"], "grid": template["grid_count"], "quality": "orange",
                "level": 0, "max_level": template["max_level"], "locked": False, "discarded": False,
                "equipped": False, "equipped_character_id": None, "equipped_character_uid": None,
                "equipped_placement": None, "names": {}, "suit_names": {}, "main_stats": main, "sub_stats": []})
        stronger = deepcopy(items[0])
        stronger["uid"]["serial"] = 101
        stronger["main_stats"][0]["value"] = 0.10
        items.append(stronger)
    with UserDataDao(database, account_id=account_id) as dao:
        if merge_inventory and dao.current_inventory_snapshot_id() is not None:
            _, previous = dao.export_inventory_snapshot(dao.current_inventory_snapshot_id())
            previous = [{**row, "uid": {"slot": row["uid_slot"], "serial": row["uid_serial"]}, "grid": row["grid_count"]} for row in previous]
            combined = {(row["kind"], row["uid"]["slot"], row["uid"]["serial"]): row for row in [*previous, *items]}
            items = list(combined.values())
        snapshot = dao.import_inventory_snapshot({"method": "event.inventory.snapshot", "params": {
            "complete": True, "generation": 1, "sequence": 1, "observed_at_unix_ms": 1_787_000_000_000,
            "item_count": len(items), "items": items}})
        _, actual = dao.export_inventory_snapshot(snapshot)
    dependencies = BattleReportPersistenceDependencies(account_id, database, 1, static_path)
    persistence = BattleReportPersistenceService(dependencies=dependencies, context_is_current=lambda _: True,
                                                operation_context=OperationContext.create("battle_report"))
    persistence.begin_capture(capture_operation_id=capture_operation, captured_at_utc="2026-08-20T00:00:00+00:00")
    hits = []
    for sequence, amount in enumerate((100.0 * (index % 3 + 1) for index in range(hit_count)), 1):
        hits.append({"sequence": str(sequence), "timestamp_unix": 100 + sequence * 0.5,
            "relative_time_seconds": sequence * 0.5, "character_id": 1072, "character_name": "灵可",
            "character_known": True, "direction": "outgoing", "damage": amount, "total_damage": amount,
            "overkill_damage": 0.0, "max_hp_reduction": 0.0, "follow_up_damage": 0.0,
            "ability_name": "GA_Radio072_Skill", "damage_name": "公开多击轴", "damage_component": "skill",
            "attack_type": "skill", "damage_attribute": "nature", "target_id": "enemy-wire:wish",
            "target_max_hp": 5_419_605.0, "follow_up_labels": [],
            "gameplay_effect_name": "GE_Fixture_Unknown" if unknown_event and sequence == 2 else (
                "GE_Player_Radio072_Skill2_Damage" if sequence == 2 else "GE_Player_Radio072_Skill1_Damage")})
    if second_character:
        # 此正式 GE 已经发行目录核实归属1003；不是复制1072技能或凑不存在的damage模板。
        hits.append({**deepcopy(hits[0]), "sequence": "4", "timestamp_unix": 102.0, "relative_time_seconds": 2.0,
            "character_id": 1003, "character_name": "早雾", "damage": 120.0, "total_damage": 120.0,
            "ability_name": "GA_Sagiri_Melee", "attack_type": "normal", "damage_component": "normal",
            "damage_attribute": "incantation", "gameplay_effect_name": "GE_Player_Sagiri_Melee1_Damage"})
    if main_character_id == 1003:
        for hit in hits:
            hit.update(character_id=1003, character_name="早雾", ability_name="GA_Sagiri_Melee",
                       attack_type="normal", damage_component="normal", damage_attribute="incantation",
                       gameplay_effect_name="GE_Player_Sagiri_Melee1_Damage")
    if hit_count != 3:
        assert not second_character, "long-axis fixture uses one explicitly frozen character"
    persistence.append_axis_page(capture_operation_id=capture_operation, page={
        "contract_version": 4, "battle_record_id": source_record, "generation": "1", "complete": True,
        "total_hits": str(len(hits)), "retained_hits": len(hits), "rows": hits})
    summary = {"duration_seconds": 2.0, "dps_time_mode": "subtract_time_stop", "total_damage": 600.0,
        "total_dps": 300.0, "total_damage_taken": 0.0, "total_hits": 3, "characters": [{
            "char_id": 1072, "name": "灵可", "hits": 3, "damage": 600.0, "dps": 300.0, "damage_share_percent": 100.0}],
        "skills": [], "abyss": {"detected": False}, "quality": {}}
    if hit_count != 3:
        total = sum(hit["damage"] for hit in hits)
        duration = hit_count * .5 + .5
        summary.update(duration_seconds=duration, total_damage=total, total_hits=hit_count, total_dps=total / duration)
        summary["characters"][0].update(hits=hit_count, damage=total, dps=total / duration)
    if main_character_id == 1003:
        summary["characters"][0].update(char_id=1003, name="早雾")
    if second_character:
        summary.update(total_damage=720.0, total_hits=4, duration_seconds=2.5, total_dps=288.0)
        summary["characters"].append({"char_id": 1003, "name": "早雾", "hits": 1, "damage": 120.0,
                                     "dps": 48.0, "damage_share_percent": 120 / 720 * 100})
        summary["characters"][0].update(dps=240.0, damage_share_percent=600 / 720 * 100)
    outcome = persistence.finalize_summary(raw_summary_payload=summary, summary=parse_battle_summary(summary),
        capture_operation_id=capture_operation, captured_at_utc="2026-08-20T00:00:00+00:00",
        finalized_at_utc="2026-08-20T00:00:02+00:00", raw_record_payload={
            "contract_version": 4, "battle_record_id": source_record, "generation": "1", "axis_complete": True,
            "axis_first_sequence": "1", "axis_total_hits": str(len(hits)), "time_stop_intervals": []})
    assert outcome.battle_record_id is not None
    history = BattleReportHistoryService(dependencies=dependencies, context_is_current=lambda _: True)
    history.save_target_condition(outcome.battle_record_id, {"target_name": "公开轴目标", "enemy_level": 90,
        "scene": "open_world", "defense_reduction": 0.0, "vulnerability": 0.0, "environment_kind": "manual",
        "resistances": {element: 0.2 for element in ("normal", "chaos", "cosmos", "incantation", "lakshana", "nature", "psyche", "psychically")}})
    baseline = [row for row in actual if not (row["kind"] == "core" and row["uid_serial"] == 101)]
    candidate = [next(row for row in actual if row["kind"] == "core" and row["uid_serial"] == 101),
                 *[row for row in actual if row["kind"] == "module"]]
    return {"main_character_id": main_character_id, "dependencies": dependencies, "battle_record_id": outcome.battle_record_id, "dataset_id": dataset,
            "source_snapshot_id": snapshot, "baseline_equipment": baseline, "candidate_equipment": candidate}
