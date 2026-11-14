# 严格验证参照构筑的逐件/整队反事实建议；只核对合法预算与组件数学证据，不搜索装备或重算伤害。
import math

from src.domain.teams.graduation.axis_graduation_rules import QUALITY_NAMES
from src.integrations.battle_analysis.graduation.team_axis_graduation import _keys, _require, _integer, _list, _axis, _text, validate_common_request
from src.integrations.battle_analysis.graduation.team_axis_graduation_advice import _impact
from src.integrations.battle_analysis.graduation.team_axis_graduation_current import reference_context, validate_current_response


def validate_equipment_advice_request(request, dataset_version):
    target = validate_common_request(request, dataset_version, operation="equipment_advice_evaluation", extras={"reference", "axis_snapshot"})
    reference_context(request["reference"], request, target, axis_snapshot=request["axis_snapshot"])
    return target


def _geometry(value):
    value = str(value or "").strip()
    return "" if not value or value.casefold() == "core" else value if value.startswith("EquipmentGeometry_") else "EquipmentGeometry_"+value


def _mapping(actual, goal):
    if len(actual) != len(goal):
        return None
    used, mapping = set(), []
    for item in goal:
        matches = [n for n, a in enumerate(actual) if n not in used
            and a["kind"] == item["kind"] and a["item_id"] == item["item_id"]
            and QUALITY_NAMES.get(str(a["quality"]).casefold()) == item["quality"]
            and a["grid_count"] == item["grid_count"] and a["suit_id"] == item["suit_id"]
            and _geometry(a.get("geometry")) == _geometry(item["geometry"])
            and (item["kind"] == "core" or all(type(a.get(k)) is int and a[k] == item[k] for k in ("target_row", "target_column")))]
        if len(matches) != 1:
            return None
        used.add(matches[0])
        mapping.append(matches[0])
    return mapping


def _stat_row(ref, scale, group):
    # 与传输声明的满值/品质/格数逐项核对，不另建养成或伤害模型；两端以相同ties-even显示单位冻结。
    display = round(ref["display_value"]*scale*100)/100
    return {"stat_group": group, "property_id": ref["property_id"], "value": display/100 if ref["is_percent"] else display,
            "is_percent": ref["is_percent"]}


def _target_equipment(value, request, preset):
    actors = _list(value, 4, 4)
    rules = request["rules"]
    specs = {s["item_id"]: s for s in rules["items"]}
    coefficients = dict(rules["quality_coefficients"])
    builds = request["reference"]["payload"]["build"]
    for actor, layout, build in zip(actors, preset.layouts, builds):
        _keys(actor, "character_id equipment")
        _integer(actor["character_id"], 1)
        _require(actor["character_id"] == layout.character_id)
        pieces = {p["piece_id"]: p for p in build["pieces"]}
        anchors = {p.piece_id: (p.row, p.column) for p in layout.pieces}
        expected = {layout.core_piece_id: layout.core_item_id, **{p.piece_id: p.item_id for p in layout.pieces}}
        items = _list(actor["equipment"], len(expected), len(expected))
        _require([i.get("theoretical_piece_id") for i in items] == sorted(expected, key=lambda pid: (pid != layout.core_piece_id, pid)))
        for item in items:
            pid = item["theoretical_piece_id"]
            _keys(item, {"theoretical_piece_id", "item_id", "kind", "quality", "level", "max_level", "geometry", "grid_count", "suit_id", "stats"}
                  | ({"target_row", "target_column"} if pid in anchors else set()))
            _require(item["item_id"] == expected[pid])
            spec = specs[item["item_id"]]
            for key in ("kind", "quality", "max_level", "suit_id"):
                _require(type(item[key]) is type(spec[key]) and item[key] == spec[key])
            _integer(item["level"], spec["max_level"], spec["max_level"])
            _integer(item["grid_count"], spec["grid_count"] or 0, spec["grid_count"] or 0)
            _require(item["geometry"] == spec["geometry_id"])
            if pid in anchors:
                for key, position in zip(("target_row", "target_column"), anchors[pid]):
                    _integer(item[key], position, position)
            choice = pieces[pid]
            coefficient = coefficients[spec["quality"]]
            if spec["kind"] == "core":
                ref, = (r for r in rules["core_main_stats"] if r["property_id"] == choice["main_property_id"])
                stats = [_stat_row(ref, coefficient, "main")]
                pool, scale = rules["core_sub_stats"], coefficient
            else:
                stats = [_stat_row(r, 1, "main") for r in spec["intrinsic_main_stats"]]
                pool, scale = rules["drive_sub_stats"], coefficient*spec["grid_count"]
            for prop in choice["sub_property_ids"]:
                ref, = (r for r in pool if r["property_id"] == prop)
                stats.append(_stat_row(ref, scale, "sub"))
            actual_stats = _list(item["stats"], len(stats), len(stats))
            for a, ref in zip(actual_stats, stats):
                _keys(a, "stat_group property_id value is_percent")
                _require(a["stat_group"] == ref["stat_group"] and a["property_id"] == ref["property_id"]
                         and type(a["is_percent"]) is bool and a["is_percent"] == ref["is_percent"]
                         and type(a["value"]) in (int, float) and math.isfinite(a["value"]) and a["value"] == ref["value"])
    return {a["character_id"]: a["equipment"] for a in actors}


def _changes(item, actual, goal):
    changes = _list(item["changes"], 4, 1)
    _require(item["kind"] in {"combined_equipment", "member_equipment", "piece_equipment"})
    _require((len(changes) > 1) == (item["kind"] == "combined_equipment"))
    seen = set()
    for change in changes:
        _keys(change, "character_id mode piece_ids")
        cid = change["character_id"]
        _integer(cid, 1)
        _require(cid in actual and cid not in seen)
        seen.add(cid)
        ids = _list(change["piece_ids"], 17, 1)
        for pid in ids:
            _text(pid)
        _require(len(ids) == len(set(ids)))
        all_ids = [p["theoretical_piece_id"] for p in goal[cid]]
        if item["kind"] == "piece_equipment":
            _require(change["mode"] == "replace_pieces" and len(ids) == 1 and ids[0] in all_ids
                     and _mapping(actual[cid], goal[cid]) is not None)
        else:
            _require(change["mode"] == "replace_loadout" and ids == all_ids)


def validate_equipment_advice_response(result, request, *, dataset_version, engine_version, request_sha256):
    validate_current_response(result, request, dataset_version=dataset_version, engine_version=engine_version,
                              request_sha256=request_sha256, extra_fields={"equipment_recommendations"})
    target = validate_equipment_advice_request(request, dataset_version)
    advice = _keys(result["equipment_recommendations"], "scope preset_id target_equipment piece_steps_unavailable_members candidate_limit generated_candidates attempted_candidates evaluated_candidates preparation_unavailable_candidates omitted_candidates stop_reason items")
    _require(advice["scope"] == "equipment_to_reference_build_v1" and advice["preset_id"] == request["reference"]["payload"]["preset_id"])
    preset, = (p for p in target.presets if p.preset_id == advice["preset_id"])
    goal = _target_equipment(advice["target_equipment"], request, preset)
    actual = {a["character_id"]: a["equipment"] for a in request["current"]["build"]["characters"]}
    _integer(advice["candidate_limit"], 64, 64)
    for key in ("generated_candidates", "attempted_candidates", "evaluated_candidates", "preparation_unavailable_candidates", "omitted_candidates"):
        _integer(advice[key], 0, 73)
    items = _list(advice["items"], 64)
    count = advice["generated_candidates"]
    _require(advice["attempted_candidates"] == len(items) == min(count, 64) and advice["omitted_candidates"] == max(0, count-64))
    unavailable = _list(advice["piece_steps_unavailable_members"], 4)
    for cid in unavailable:
        _integer(cid, 1)
    compatible = advice["preset_id"] in result["current"]["target_qualification"]["compatible_preset_ids"]
    _require(unavailable == ([cid for cid in actual if _mapping(actual[cid], goal[cid]) is None] if compatible else []))
    _require(advice["stop_reason"] == ("reference_preset_outside_current_conditions" if not compatible else
                                     "candidate_limit" if count > 64 else "declared_candidates_complete"))
    if not compatible:
        _require(count == 0)
    ids, changes_seen, order = set(), set(), []
    domain = result["current"]["axis_evaluation"]["scoring_domain_sha256"]
    for item in items:
        _keys(item, "candidate_id kind changes status error_code evaluation impact")
        cid = item["candidate_id"]
        _require(isinstance(cid, str) and 0 < len(cid.encode("utf8")) <= 512 and cid.strip() == cid
                 and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in cid))
        _require(cid not in ids)
        ids.add(cid)
        _changes(item, actual, goal)
        signature = tuple((c["character_id"], c["mode"], tuple(c["piece_ids"])) for c in item["changes"])
        _require(signature not in changes_seen)
        changes_seen.add(signature)
        if item["evaluation"] is None:
            _require(item["status"] == "preparation_unavailable" and item["impact"] is None)
            _text(item["error_code"])
        else:
            _axis(item["evaluation"], domain)
            _require(item["error_code"] is None)
            if item["impact"] is None:
                _require(item["status"] == "unquantified")
            else:
                _require(item["status"] == "mathematical_only")
                _impact(item["impact"], result["current"]["axis_evaluation"]["damage"], item["evaluation"]["damage"],
                        request["reference"]["payload"]["reference_damage"])
        delta = item["impact"]["damage_delta"] if item["impact"] is not None else None
        order.append((delta is None, -delta if delta is not None else 0, cid))
    evaluated = sum(i["evaluation"] is not None for i in items)
    _require(advice["evaluated_candidates"] == evaluated and advice["preparation_unavailable_candidates"] == len(items)-evaluated
             and order == sorted(order))
    return result
