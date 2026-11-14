# 校验分析端整队比较条件的有界证据；不在Calc复制养成判定或把条件通过视为可执行性证明。
from src.integrations.battle_analysis.graduation.team_axis_graduation import _keys, _require, _integer, _text, _list


CAP_FIELDS = frozenset("character_level breakthrough_stage awakening_level likeability_level_10_enabled fork_level fork_breakthrough_stage fork_refinement_level".split())
WORLD_FIELDS = frozenset(("yaodao_attack_add", "quantao_crit_damage"))
ENTRY_FIELDS = frozenset(("skill_levels", "selected_awaken_effect_ids"))


def validate_target_qualification(value, request, target):
    value = _keys(value, "policy_id scope exact_domain_membership target_sha256 current_source_sha256 status compatible_preset_ids presets")
    _require(value["policy_id"] == "team_axis_comparison_qualification_v1"
             and value["scope"] == "cultivation_and_world_caps_v1" and value["exact_domain_membership"] == "not_evaluated"
             and value["target_sha256"] == target.sha256 and value["current_source_sha256"] == request["current"]["source_sha256"])
    rows = _list(value["presets"], len(target.presets), len(target.presets))
    expected_ids = [p.preset_id for p in target.presets]
    members = {a["character_id"]: a["profile"] for a in request["current"]["build"]["characters"]}
    compatible = []
    for row, expected in zip(rows, expected_ids):
        _keys(row, "preset_id compatible issues issues_omitted")
        _require(row["preset_id"] == expected and type(row["compatible"]) is bool)
        issues = _list(row["issues"], 128)
        _integer(row["issues_omitted"], 0, 1024)
        _require(not row["issues_omitted"] or len(issues) == 128)
        _require(row["compatible"] == (not issues and row["issues_omitted"] == 0))
        seen = set()
        for issue in issues:
            _keys(issue, "character_id field entry_id code")
            actor, field, entry, code = (issue[key] for key in ("character_id", "field", "entry_id", "code"))
            _require(isinstance(field, str) and isinstance(code, str))
            if field in WORLD_FIELDS:
                _require(actor is None and entry is None and code == "exceeds_target")
            else:
                _integer(actor, 1)
                _require(actor in members)
                if field in ENTRY_FIELDS:
                    _text(entry)
                    _require(entry in members[actor][field] and code in {"exceeds_target", "not_in_target"})
                    if field == "selected_awaken_effect_ids":
                        _require(code == "not_in_target")
                elif field == "fork_id":
                    _require(entry is None and code == "not_in_target")
                else:
                    _require(field in CAP_FIELDS and entry is None and code == "exceeds_target")
            key = (actor, field, entry, code)
            _require(key not in seen)
            seen.add(key)
        if row["compatible"]:
            compatible.append(expected)
    _list(value["compatible_preset_ids"], len(target.presets))
    _require(value["compatible_preset_ids"] == compatible)
    _require(value["status"] == ("within_target_conditions" if compatible else "outside_target_conditions"))
    return "axis_feasibility_unverified" if compatible else "outside_target"
