# 校验新毕业率正式基准请求与受界响应，只传输冻结值，不实现伤害公式或把未证实结果提升为正式百分比。
from dataclasses import fields
import hashlib
import json
import math

from src.domain.teams.graduation.axis_graduation import CRITICAL_POLICY, DAMAGE_UNIT, MODEL_ID
from src.domain.teams.graduation.axis_graduation_rules import StatReference, TheoryEquipmentRules, TheoryEquipmentSpec
from src.domain.teams.graduation.axis_graduation_target import TargetFork, TargetMember, TargetWorldBonus, decode_graduation_target
from src.integrations.battle_analysis.battle_axis_evaluation import validate_axis_equipment, validate_axis_selector
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError, REQUEST_SCHEMA, RESPONSE_SCHEMA


BATCH_KIND = "team_axis_graduation_v1"
CAPABILITY = "team_axis_graduation_benchmark_v2"
FIXED_AXIS_RATIO_POLICY = "fixed_observed_axis_ratio_v1"
MAX_WIRE_BYTES = 8 * 1024 * 1024
STOP_REASONS = {"neighborhood_complete", "evaluation_budget", "time_budget", "preset_budget",
                "quantification_limited", "no_comparable_seed"}
PROFILE_KEYS = frozenset("character_level breakthrough_stage awakening_level awakening_selection_initialized "
    "selected_awaken_effect_ids likeability_level_10_enabled fork_id fork_level fork_breakthrough_stage "
    "fork_refinement_level skill_levels".split())


def _error():
    return NativeAnalysisError("队伍轴伤毕业请求或响应与冻结契约不一致")


def _require(condition):
    if not condition:
        raise _error()


def _keys(value, names):
    _require(isinstance(value, dict) and set(value) == set(names.split() if isinstance(names, str) else names))
    return value


def _integer(value, minimum=0, maximum=2**63-1):
    _require(type(value) is int and minimum <= value <= maximum)


def _number(value):
    _require(type(value) in (int, float) and math.isfinite(value) and value >= 0)


def _hash(value):
    _require(isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value))


def _text(value):
    _require(isinstance(value, str) and bool(value) and value.strip() == value
             and len(value.encode("utf8")) <= 256 and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value))


def _list(value, maximum, minimum=0):
    _require(isinstance(value, list) and minimum <= len(value) <= maximum)
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _reject(_value):
    raise _error()


def _finite(value):
    parsed = float(value)
    _require(math.isfinite(parsed))
    return parsed


def _decode(raw):
    _require(isinstance(raw, bytes) and len(raw) <= MAX_WIRE_BYTES)
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=_reject, parse_float=_finite)


def _rules(raw):
    data = dict(_keys(raw, {f.name for f in fields(TheoryEquipmentRules)}))
    for name in ("drive_sub_stats", "core_sub_stats", "core_main_stats"):
        data[name] = tuple(StatReference(**_keys(r, "property_id display_value is_percent")) for r in _list(data[name], 64, 1))
    items = []
    for row in _list(data["items"], 4096, 1):
        item = dict(_keys(row, {f.name for f in fields(TheoryEquipmentSpec)}))
        item["intrinsic_main_stats"] = tuple(StatReference(**_keys(r, "property_id display_value is_percent"))
                                             for r in _list(item["intrinsic_main_stats"], 32))
        items.append(TheoryEquipmentSpec(**item))
    data["items"] = tuple(items)
    return TheoryEquipmentRules(**data)


def _actual(actual, target, selector):
    _keys(actual, "account_id generation team_id character_ids dataset_id static_schema static_sha256 source_sha256 world_bonus build")
    for key in ("account_id", "generation"):
        _require(actual[key] == selector[key] and type(actual[key]) is type(selector[key]))
    _require(actual["team_id"] == target.team_id and actual["character_ids"] == list(target.character_ids))
    for key in ("dataset_id", "static_schema", "static_sha256"):
        _require(actual[key] == getattr(target, key) and type(actual[key]) is type(getattr(target, key)))
    for cid in actual["character_ids"]:
        _integer(cid, 1)
    _hash(actual["source_sha256"])
    world = actual["world_bonus"]
    names = "yaodao_attack_add quantao_crit_damage" + (" kaijia_defense_add" if isinstance(world, dict) and "kaijia_defense_add" in world else "")
    _keys(world, names)
    TargetWorldBonus(world["yaodao_attack_add"], world["quantao_crit_damage"])
    if "kaijia_defense_add" in world:
        defense = world["kaijia_defense_add"]
        _require(type(defense) in (int, float) and math.isfinite(defense) and 0 <= defense <= 30)
    _keys(actual["build"], "characters")
    actors = _list(actual["build"]["characters"], 4, 4)
    occupied = set()
    for cid, actor in zip(target.character_ids, actors):
        _keys(actor, "character_id profile equipment equipment_complete")
        _require(type(actor["character_id"]) is int and actor["character_id"] == cid and actor["equipment_complete"] is True)
        p = _keys(actor["profile"], PROFILE_KEYS)
        _require(p["awakening_selection_initialized"] is True and isinstance(p["skill_levels"], dict))
        if p["fork_id"] is None:
            _require(all(p[k] is None for k in ("fork_level", "fork_breakthrough_stage", "fork_refinement_level")))
            fork = None
        else:
            fork = TargetFork(p["fork_id"], p["fork_level"], p["fork_breakthrough_stage"], p["fork_refinement_level"])
        TargetMember(cid, p["character_level"], p["breakthrough_stage"], p["awakening_level"],
                     p["selected_awaken_effect_ids"], p["likeability_level_10_enabled"], fork, tuple(p["skill_levels"].items()))
        items = _list(actor["equipment"], 17)
        if items:
            ids = validate_axis_equipment(items, complete=False)
            _require(not occupied.intersection(ids) and sum(i["kind"] == "core" for i in items) <= 1)
            occupied.update(ids)
            _require(not any("theoretical_piece_id" in i or "virtual" in i for i in items))


def _choice(build, preset, rules):
    specs = {i["item_id"]: i for i in rules["items"]}
    for actor, layout in zip(_list(build, 4, 4), preset.layouts):
        _keys(actor, "character_id pieces")
        _integer(actor["character_id"], 1)
        _integer(actor["character_id"], 1)
        _require(actor["character_id"] == layout.character_id)
        expected = {layout.core_piece_id: layout.core_item_id, **{p.piece_id: p.item_id for p in layout.pieces}}
        pieces = _list(actor["pieces"], len(expected), len(expected))
        _require({p["piece_id"]: p["item_id"] for p in pieces} == expected)
        for piece in pieces:
            _keys(piece, "piece_id item_id main_property_id sub_property_ids")
            spec = specs[piece["item_id"]]
            pool = rules["core_sub_stats"] if spec["kind"] == "core" else rules["drive_sub_stats"]
            subs = _list(piece["sub_property_ids"], spec["sub_stat_count"], spec["sub_stat_count"])
            _require(len(set(subs)) == len(subs) and set(subs) <= {p["property_id"] for p in pool})
            if spec["kind"] == "core":
                _require(piece["main_property_id"] in {p["property_id"] for p in rules["core_main_stats"]})
            else:
                _require(piece["main_property_id"] is None)


def _provenance(value, actual):
    _keys(value, "team_revision source_mode source_revision bindings world_source members")
    _integer(value["team_revision"], 1)
    _integer(value["source_revision"], 1)
    _require(value["source_mode"] in {"current", "slots", "game"} and value["world_source"] == "account")
    bindings = _list(value["bindings"], 4)
    for pair in bindings:
        _list(pair, 2, 2)
        for key in pair:
            _integer(key, 1)
    _require(len({p[0] for p in bindings}) == len(bindings))
    _require(value["source_mode"] == "slots" or not bindings)
    for evidence, actor in zip(_list(value["members"], 4, 4), actual["build"]["characters"]):
        _keys(evidence, "character_id slot_id plan_id snapshot_id source_fingerprint field_sources skill_level_sources")
        _integer(evidence["character_id"], 1)
        _require(evidence["character_id"] == actor["character_id"])
        for name in ("slot_id", "plan_id", "snapshot_id"):
            if evidence[name] is not None:
                _integer(evidence[name], 1)
        if value["source_mode"] != "game":
            _require(all(evidence[name] is not None for name in ("slot_id", "plan_id", "snapshot_id")))
        if value["source_mode"] == "slots":
            _require([evidence["character_id"], evidence["slot_id"]] in bindings)
        _hash(evidence["source_fingerprint"])
        sources = _keys(evidence["field_sources"], PROFILE_KEYS)
        for name, source in sources.items():
            _require(source in {"account", "native_observed"} or name == "skill_levels" and source == "mixed"
                or source == "derived_zero_awakening" and actor["profile"]["awakening_level"] == 0
                    and name in {"selected_awaken_effect_ids", "awakening_selection_initialized"}
                or source == "derived_unequipped_fork" and actor["profile"]["fork_id"] is None
                    and name in {"fork_level", "fork_breakthrough_stage", "fork_refinement_level"})
        skills = _keys(evidence["skill_level_sources"], actor["profile"]["skill_levels"])
        _require(all(source in {"account", "native_observed"} for source in skills.values()))


def validate_common_request(request, dataset_version, *, operation, extras):
    if isinstance(request, dict) and "fixed_axis_ratio_policy" in request:
        _require(request["fixed_axis_ratio_policy"] == FIXED_AXIS_RATIO_POLICY)
        extras = {*extras, "fixed_axis_ratio_policy"}
    _keys(request, set("version operation request_id source_input_digest target_sha256 battle_request target rules current current_provenance component_provenance".split()) | set(extras))
    _require(type(request["version"]) is int and request["version"] == 2 and request["operation"] == operation)
    _text(request["request_id"])
    _hash(request["source_input_digest"])
    _hash(request["target_sha256"])
    selector = request["battle_request"]
    validate_axis_selector(selector)
    target = decode_graduation_target(json.dumps(request["target"], ensure_ascii=False, allow_nan=False))
    _require(target.sha256 == request["target_sha256"] and target.dataset_id == dataset_version)
    target.verify_rules(_rules(request["rules"]))
    _actual(request["current"], target, selector)
    _provenance(request["current_provenance"], request["current"])
    component = _keys(request["component_provenance"], "component_executable_sha256 component_manifest_sha256")
    for digest in component.values():
        _hash(digest)
    return target


def validate_request(request, dataset_version):
    if isinstance(request, dict) and request.get("operation") == "equipment_advice_evaluation":
        from src.integrations.battle_analysis.graduation.team_axis_graduation_equipment_advice import validate_equipment_advice_request
        return validate_equipment_advice_request(request, dataset_version)
    if isinstance(request, dict) and request.get("operation") == "recommendation_evaluation":
        from src.integrations.battle_analysis.graduation.team_axis_graduation_advice import validate_advice_request
        return validate_advice_request(request, dataset_version)
    if isinstance(request, dict) and request.get("operation") == "current_evaluation":
        from src.integrations.battle_analysis.graduation.team_axis_graduation_current import validate_current_request
        return validate_current_request(request, dataset_version)
    target = validate_common_request(request, dataset_version, operation="theoretical_benchmark", extras={"seeds", "budget"})
    budget = _keys(request["budget"], "max_evaluations max_millis")
    _integer(budget["max_evaluations"], 1, 100_000)
    _integer(budget["max_millis"], 1, 600_000)
    seeds = _list(request["seeds"], 16, 1)
    _require(len(seeds) == len(target.presets))
    ids = set()
    for group in seeds:
        _keys(group, "preset_id seeds")
        _require(group["preset_id"] not in ids)
        ids.add(group["preset_id"])
        _list(group["seeds"], 16, 1)
        for build in group["seeds"]:
            _list(build, 4, 4)
            for actor, cid in zip(build, target.character_ids):
                _keys(actor, "character_id pieces")
                _require(type(actor["character_id"]) is int and actor["character_id"] == cid)
                for piece in _list(actor["pieces"], 21, 2):
                    _keys(piece, "piece_id item_id main_property_id sub_property_ids")
                    _text(piece["piece_id"])
                    _text(piece["item_id"])
                    if piece["main_property_id"] is not None:
                        _text(piece["main_property_id"])
                    subs = _list(piece["sub_property_ids"], 32)
                    _require(len(subs) == len(set(subs)))
                    for key in subs:
                        _text(key)
    _require(ids == {p.preset_id for p in target.presets})
    for group in seeds:
        preset, = (p for p in target.presets if p.preset_id == group["preset_id"])
        for build in group["seeds"]:
            _choice(build, preset, request["rules"])
    return target


def _codes(value, maximum=128):
    for code in _list(value, maximum):
        _text(code)
    _require(len(value) == len(set(value)))


def _axis(row, domain):
    _keys(row, "model_id critical_policy unit scoring_domain_sha256 status damage known_damage total_events quantified_events global_gap_codes event_gaps")
    _require(row["model_id"] == MODEL_ID and row["critical_policy"] == CRITICAL_POLICY and row["unit"] == DAMAGE_UNIT
             and row["scoring_domain_sha256"] == domain)
    _integer(row["total_events"], 1, 100_000)
    _integer(row["quantified_events"], 0, row["total_events"])
    _number(row["known_damage"])
    _codes(row["global_gap_codes"])
    gaps = _list(row["event_gaps"], row["total_events"])
    seen = set()
    for gap in gaps:
        _keys(gap, "event_id codes")
        _text(gap["event_id"])
        _require(gap["event_id"] not in seen and bool(gap["codes"]))
        seen.add(gap["event_id"])
        _codes(gap["codes"])
    _require(len(gaps) + row["quantified_events"] == row["total_events"])
    complete = not gaps and not row["global_gap_codes"]
    _require(row["status"] == ("complete" if complete else "unavailable"))
    if complete:
        _number(row["damage"])
        _require(row["damage"] == row["known_damage"])
    else:
        _require(row["damage"] is None)


def _search(row, budget):
    _keys(row, "best_build best_damage evaluated_candidates cache_hits unquantified_candidates neighborhood_complete elapsed_ms stop_reason neighborhood")
    for key in ("evaluated_candidates", "cache_hits", "unquantified_candidates", "elapsed_ms"):
        _integer(row[key])
    _require(row["evaluated_candidates"] <= budget and row["unquantified_candidates"] <= row["evaluated_candidates"])
    _require(type(row["neighborhood_complete"]) is bool and row["stop_reason"] in STOP_REASONS
             and row["neighborhood_complete"] == (row["stop_reason"] == "neighborhood_complete")
             and row["neighborhood"] == "single_property_and_top4_comparable_cross_actor_pairs_v1")
    _require((row["best_build"] is None) == (row["best_damage"] is None))
    if row["best_damage"] is not None:
        _number(row["best_damage"])


def _sample(value, unknown):
    _require((value is not None) == (unknown > 0))
    if value is None:
        return
    _keys(value, "basis total_events affected_events global_gap_codes event_gap_codes codes_omitted")
    _require(value["basis"] == "first_unquantified_candidate_v1")
    _integer(value["total_events"], 1, 100_000)
    _integer(value["affected_events"], 0, value["total_events"])
    _integer(value["codes_omitted"])
    _codes(value["global_gap_codes"], 64)
    _codes(value["event_gap_codes"], 64)


def _theory(row, target, request, domain):
    _keys(row, "target_id team_id target_revision target_sha256 scoring_domain_sha256 search_scope budget_allocation_policy optimality best presets evaluated_candidates unquantified_candidates cache_hits evaluation_budget time_budget_ms elapsed_ms visited_presets all_presets_visited neighborhood_complete stop_reason axis_feasibility")
    _require((row["target_id"], row["team_id"], row["target_revision"], row["target_sha256"], row["scoring_domain_sha256"])
             == (target.target_id, target.team_id, target.revision, target.sha256, domain))
    _integer(row["target_revision"], 1)
    _integer(row["evaluation_budget"], 1, 100_000)
    _integer(row["time_budget_ms"], 1, 600_000)
    _require(row["search_scope"] == "target_presets_bounded_v1" and row["budget_allocation_policy"] == "equal_remaining_preset_share_v1"
             and row["optimality"] == "bounded_best" and row["axis_feasibility"] == "unverified")
    _require(row["evaluation_budget"] == request["budget"]["max_evaluations"] and row["time_budget_ms"] == request["budget"]["max_millis"])
    for key in ("evaluated_candidates", "unquantified_candidates", "cache_hits", "elapsed_ms", "visited_presets"):
        _integer(row[key])
    _require(row["evaluated_candidates"] <= row["evaluation_budget"] and row["unquantified_candidates"] <= row["evaluated_candidates"])
    for key in ("all_presets_visited", "neighborhood_complete"):
        _require(type(row[key]) is bool)
    _require(row["stop_reason"] in STOP_REASONS and row["neighborhood_complete"] == (row["stop_reason"] == "neighborhood_complete"))
    presets = _list(row["presets"], len(target.presets), len(target.presets))
    _require([p["preset_id"] for p in presets] == [p.preset_id for p in target.presets])
    visited = 0
    for preset in presets:
        _keys(preset, "preset_id allocated_evaluations allocated_millis search unquantified_sample")
        _integer(preset["allocated_evaluations"])
        _integer(preset["allocated_millis"])
        if preset["search"] is not None:
            visited += 1
            _search(preset["search"], preset["allocated_evaluations"])
            _sample(preset["unquantified_sample"], preset["search"]["unquantified_candidates"])
        else:
            _require(preset["allocated_evaluations"] == preset["allocated_millis"] == 0 and preset["unquantified_sample"] is None)
    _require(visited == row["visited_presets"] and row["all_presets_visited"] == (visited == len(presets)))
    for key in ("evaluated_candidates", "unquantified_candidates", "cache_hits"):
        _require(row[key] == sum(p["search"][key] for p in presets if p["search"] is not None))
    best = row["best"]
    if best is None:
        _require(all(p["search"] is None or p["search"]["best_damage"] is None for p in presets))
        return
    _keys(best, "target search_scope search axis_evaluation unquantified_sample equipment layout_sha256 cultivation_sha256 cultivation_validation axis_feasibility")
    identity = _keys(best["target"], "target_id team_id revision sha256 preset_id preset_count")
    _require((identity["target_id"], identity["team_id"], identity["revision"], identity["sha256"], identity["preset_count"])
             == (target.target_id, target.team_id, target.revision, target.sha256, len(presets)))
    _integer(identity["revision"], 1)
    _integer(identity["preset_count"], 1, 16)
    selected, = (p for p in target.presets if p.preset_id == identity["preset_id"])
    matching, = (p for p in presets if p["preset_id"] == selected.preset_id)
    _search(best["search"], matching["allocated_evaluations"])
    _choice(best["search"]["best_build"], selected, request["rules"])
    _require(best["search"] == matching["search"] and best["search"]["best_damage"] is not None
             and best["unquantified_sample"] == matching["unquantified_sample"])
    _require(best["search_scope"] == "selected_preset_only" and best["axis_feasibility"] == "unverified"
             and best["cultivation_validation"] == "static_cultivation_validated_v1")
    for key in ("layout_sha256", "cultivation_sha256"):
        _hash(best[key])
    _axis(best["axis_evaluation"], domain)
    _require(best["axis_evaluation"]["damage"] == best["search"]["best_damage"])
    _require(best["search"]["best_damage"] == max(p["search"]["best_damage"] for p in presets
                if p["search"] is not None and p["search"]["best_damage"] is not None))
    specs = {i["item_id"]: i for i in request["rules"]["items"]}
    for actor, layout, choice in zip(_list(best["equipment"], 4, 4), selected.layouts, best["search"]["best_build"]):
        _keys(actor, "character_id equipment")
        _integer(actor["character_id"], 1)
        _require(actor["character_id"] == layout.character_id)
        expected = {layout.core_piece_id: layout.core_item_id, **{p.piece_id: p.item_id for p in layout.pieces}}
        items = _list(actor["equipment"], len(expected), len(expected))
        _require({i.get("theoretical_piece_id"): i.get("item_id") for i in items} == expected)
        choices = {p["piece_id"]: p for p in choice["pieces"]}
        for item in items:
            spec = specs[item["item_id"]]
            piece = choices[item["theoretical_piece_id"]]
            _integer(item["level"])
            _integer(item["max_level"])
            _require(item["level"] == spec["max_level"] == item["max_level"] and item["kind"] == spec["kind"]
                     and item["quality"] == spec["quality"] and item["suit_id"] == spec["suit_id"])
            stats = _list(item["stats"], 32, 1)
            _require(sorted(stat["property_id"] for stat in stats if stat["stat_group"] == "sub")
                     == sorted(piece["sub_property_ids"]))
            mains = [stat["property_id"] for stat in stats if stat["stat_group"] == "main"]
            expected_main = [piece["main_property_id"]] if spec["kind"] == "core" else [stat["property_id"] for stat in spec["intrinsic_main_stats"]]
            _require(sorted(mains) == sorted(expected_main))
            _require("uid_slot" not in item and "uid_serial" not in item)
            for stat in _list(item["stats"], 32, 1):
                _text(stat["property_id"])
                _number(stat["value"])
                _require(stat["stat_group"] in {"main", "sub"} and type(stat["is_percent"]) is bool)
                references = [*request["rules"]["core_main_stats"], *request["rules"]["core_sub_stats"],
                              *request["rules"]["drive_sub_stats"], *spec["intrinsic_main_stats"]]
                _require(any(stat["property_id"] == ref["property_id"] and stat["is_percent"] is ref["is_percent"] for ref in references))


def validate_axis_snapshot(value, request, engine_version=None):
    from src.domain.teams.graduation.axis_graduation_snapshot import freeze_axis_context_snapshot
    try:
        frozen = freeze_axis_context_snapshot(value)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise _error() from None
    meta = frozen.to_payload()
    scope = {key: request["battle_request"].get(key) for key in ("battle_record_id", "start_us", "end_us", "detail_scope")}
    _require(meta["account_id"] == request["current"]["account_id"] and meta["scope"] == scope
             and meta["source_input_digest"] == request["source_input_digest"])
    for key in ("dataset_id", "static_schema", "static_sha256"):
        _require(meta[key] == request["rules"][key])
    if engine_version is not None:
        _require(meta["engine_version"] == engine_version)
    return frozen


def validate_response_header(result, request, *, dataset_version, engine_version, request_sha256, extras):
    if "fixed_axis_ratio_policy" in request:
        extras = {*extras, "fixed_axis_graduation"}
    _keys(result, set("schema_version engine_version dataset_version batch_kind version operation request_id request_sha256 source_input_digest target_sha256 model_id critical_policy unit status current_ratio_percent current benchmark_reference axis_snapshot".split()) | set(extras))
    _require((result["schema_version"], result["engine_version"], result["dataset_version"], result["batch_kind"])
             == (RESPONSE_SCHEMA, engine_version, dataset_version, BATCH_KIND))
    _require(type(result["version"]) is int and result["version"] == 2)
    for key in ("operation", "request_id", "source_input_digest", "target_sha256"):
        _require(result[key] == request[key])
    _require(result["request_sha256"] == request_sha256 and result["model_id"] == MODEL_ID
             and result["critical_policy"] == CRITICAL_POLICY and result["unit"] == DAMAGE_UNIT
             and result["current_ratio_percent"] is None)
    target = validate_request(request, dataset_version)
    validate_axis_snapshot(result["axis_snapshot"], request, engine_version)
    if request["operation"] != "theoretical_benchmark":
        _require(result["axis_snapshot"] == request["axis_snapshot"])
    _require(isinstance(result["current"], dict) and "target_qualification" in result["current"])
    from src.integrations.battle_analysis.graduation.team_axis_graduation_qualification import validate_target_qualification
    _require(result["status"] == validate_target_qualification(result["current"]["target_qualification"], request, target))
    if "fixed_axis_ratio_policy" in request:
        from src.integrations.battle_analysis.graduation.team_axis_graduation_ratio import validate_fixed_axis_ratio
        validate_fixed_axis_ratio(result)
    return target


def validate_current_result(current, request):
    current = _keys(current, "source_sha256 input_sha256 cultivation_sha256 cultivation_validation axis_evaluation axis_feasibility target_qualification")
    _require(current["source_sha256"] == request["current"]["source_sha256"] and current["axis_feasibility"] == "unverified"
             and current["cultivation_validation"] == "static_cultivation_validated_v1")
    _hash(current["input_sha256"])
    _hash(current["cultivation_sha256"])
    domain = current["axis_evaluation"]["scoring_domain_sha256"]
    _hash(domain)
    _axis(current["axis_evaluation"], domain)
    return domain


def validate_response(result, request, *, dataset_version, engine_version, request_sha256):
    if request["operation"] == "equipment_advice_evaluation":
        from src.integrations.battle_analysis.graduation.team_axis_graduation_equipment_advice import validate_equipment_advice_response
        return validate_equipment_advice_response(result, request, dataset_version=dataset_version,
            engine_version=engine_version, request_sha256=request_sha256)
    if request["operation"] == "recommendation_evaluation":
        from src.integrations.battle_analysis.graduation.team_axis_graduation_advice import validate_advice_response
        return validate_advice_response(result, request, dataset_version=dataset_version,
            engine_version=engine_version, request_sha256=request_sha256)
    if request["operation"] == "current_evaluation":
        from src.integrations.battle_analysis.graduation.team_axis_graduation_current import validate_current_response
        return validate_current_response(result, request, dataset_version=dataset_version,
            engine_version=engine_version, request_sha256=request_sha256)
    target = validate_response_header(result, request, dataset_version=dataset_version,
        engine_version=engine_version, request_sha256=request_sha256, extras={"theory", "reference_verification_replays"})
    domain = validate_current_result(result["current"], request)
    _theory(result["theory"], target, request, domain)
    from src.integrations.battle_analysis.graduation.team_axis_graduation_current import validate_new_reference
    validate_new_reference(result, request, target, engine_version)
    return result


def evaluate_team_axis_graduation(client, request, *, checkpoint=None):
    client._checkpoint(checkpoint)
    _require(isinstance(request, dict))
    capability = {"current_evaluation": "supports_team_axis_graduation_current",
        "recommendation_evaluation": "supports_team_axis_graduation_advice",
        "equipment_advice_evaluation": "supports_team_axis_graduation_equipment_advice",
        "theoretical_benchmark": "supports_team_axis_graduation_benchmark"}.get(request.get("operation"))
    _require(capability is not None)
    if "fixed_axis_ratio_policy" in request and not getattr(client, "supports_team_axis_graduation_fixed_axis_ratio", False):
        raise NativeAnalysisError("固定观测轴比例组件待更新，旧数学结果仍可读取")
    if not getattr(client, capability, False):
        raise NativeAnalysisError("队伍轴伤基准组件待更新，其他队伍功能保持可用")
    try:
        payload = json.dumps({"schema_version": REQUEST_SCHEMA, "dataset_version": client.dataset_version,
            "batch_kind": BATCH_KIND, "request": request}, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
        frozen = _decode(payload)["request"]
        validate_request(frozen, client.dataset_version)
        _require(frozen["component_provenance"] == client.component_provenance)
        result = _decode(client._run(payload, checkpoint=checkpoint))
        validated = validate_response(result, frozen, dataset_version=client.dataset_version,
            engine_version=client.engine_version, request_sha256=hashlib.sha256(payload).hexdigest())
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError, StopIteration, UnicodeError):
        raise _error() from None
    client._verify_component(checkpoint)
    client._checkpoint(checkpoint)
    return validated