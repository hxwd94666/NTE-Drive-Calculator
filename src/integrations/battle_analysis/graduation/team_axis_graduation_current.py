# 校验不可变分母与当前更新的协议关联；更新只消费原引用，不为新伤害搜索或重写旧分母。
from src.domain.teams.graduation.axis_graduation_reference import freeze_benchmark_reference
from src.integrations.battle_analysis.graduation.team_axis_graduation import (
    _keys, _require, _choice, _integer, _number,
    validate_common_request, validate_response_header, validate_current_result, validate_axis_snapshot,
)


def reference_context(reference, request, target, engine_version=None, *, axis_snapshot):
    value = freeze_benchmark_reference(reference).to_payload()
    p = value["payload"]
    snapshot = validate_axis_snapshot(axis_snapshot, request, engine_version)
    _require(p["axis_snapshot_sha256"] == snapshot.sha256
             and p["engine_version"] == snapshot.to_payload()["engine_version"])
    _require(p["account_id"] == request["current"]["account_id"] and p["target_sha256"] == target.sha256
             and p["component"] == request["component_provenance"])
    selector = request["battle_request"]
    scope = {key: selector.get(key) for key in ("battle_record_id", "start_us", "end_us", "detail_scope")}
    _require(p["scope"] == scope)
    if engine_version is not None:
        _require(p["engine_version"] == engine_version)
    selected, = (preset for preset in target.presets if preset.preset_id == p["preset_id"])
    _choice(p["build"], selected, request["rules"])
    _require(p["search"]["total_presets"] == len(target.presets))
    return value


def validate_current_request(request, dataset_version):
    target = validate_common_request(request, dataset_version, operation="current_evaluation", extras={"reference", "axis_snapshot"})
    reference_context(request["reference"], request, target, axis_snapshot=request["axis_snapshot"])
    return target


def validate_new_reference(result, request, target, engine_version):
    best = result["theory"]["best"]
    replay_count = result["reference_verification_replays"]
    _integer(replay_count, 0, 1)
    _require(replay_count == (1 if best is not None else 0))
    reference = result["benchmark_reference"]
    if best is None or best["search"]["best_damage"] == 0:
        _require(reference is None)
        return
    value = reference_context(reference, request, target, engine_version, axis_snapshot=result["axis_snapshot"])
    p = value["payload"]
    _require(p["origin_request_sha256"] == result["request_sha256"] and p["preset_id"] == best["target"]["preset_id"]
             and p["build"] == best["search"]["best_build"] and p["reference_damage"] == best["search"]["best_damage"]
             and p["scoring_domain_sha256"] == result["theory"]["scoring_domain_sha256"]
             and p["layout_sha256"] == best["layout_sha256"] and p["cultivation_sha256"] == best["cultivation_sha256"])
    theory = result["theory"]
    search = {key: theory[key] for key in p["search"] if key != "total_presets"}
    search["total_presets"] = len(theory["presets"])
    _require(p["search"] == search)


def validate_current_response(result, request, *, dataset_version, engine_version, request_sha256, extra_fields=()):
    target = validate_response_header(result, request, dataset_version=dataset_version, engine_version=engine_version,
                                     request_sha256=request_sha256, extras={"reference_verification", *extra_fields})
    value = reference_context(result["benchmark_reference"], request, target, engine_version, axis_snapshot=result["axis_snapshot"])
    _require(value == freeze_benchmark_reference(request["reference"]).to_payload())
    domain = validate_current_result(result["current"], request)
    p = value["payload"]
    _require(domain == p["scoring_domain_sha256"])
    verified = _keys(result["reference_verification"], "status reference_damage scoring_domain_sha256 replay_input_sha256 search_evaluations reference_replays")
    _integer(verified["search_evaluations"], 0, 0)
    _integer(verified["reference_replays"], 1, 1)
    _number(verified["reference_damage"])
    _require(verified["status"] == "replayed_unchanged" and verified["reference_damage"] == p["reference_damage"]
             and verified["scoring_domain_sha256"] == domain and verified["replay_input_sha256"] == p["replay_input_sha256"])
    return result