# 构建期导出公开计算声明与规范化 SQL，不包含账号或战报数据。
from __future__ import annotations

import argparse
import ast
import inspect
from dataclasses import asdict
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.services.battle_report.characters.passives import _CATALOG, _DIRECT_RULES, BattleCharacterPassiveService
from src.services.battle_report.characters.skill_buffs import BattleCharacterSkillBuffService
from src.domain.battle_report.buffs.models import BattleStaticBuffRule
from src.domain.battle_report.buffs.models import BattleBuffModifierEvidence
from src.services.battle_report.forks.state_compute import _BASIS as FORK_STATE_BASIS
from src.services.battle_report.native.fork_damage_state import _BASIS as FORK_DAMAGE_BASIS
from src.services.battle_report.mechanics.dot_evidence import DOT_KIND_NAMES as DOT_NAMES, dot_basis
from src.services.battle_report.treatment.native import TREATMENT_TEXT, TREATMENT_BUFF_TEXT
from src.services.battle_report.creation.counterfactual import _RULES as CREATION_RULES
from src.services.battle_report.creation.evaluation import _POLICIES as CREATION_POLICIES
from src.storage.sqlite.catalog.static_game_data_skill_damage_queries import StaticGameDataSkillDamageQueriesMixin
from src.services.battle_report.buffs.inference import (
    BattleBuffInferenceService, _SelectedEffect, _skill_rules,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.catalog.fork_permanent_projection import (
    FORK_PERMANENT_EVIDENCE_SQL, FORK_REFINEMENT_LEVEL_SQL,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--static", type=Path, default=Path("data/game_static.sqlite3"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, sql in (("fork_permanent_evidence.sql", FORK_PERMANENT_EVIDENCE_SQL),
                      ("fork_refinement_levels.sql", FORK_REFINEMENT_LEVEL_SQL)):
        (args.output / name).write_text(sql.strip() + "\n", encoding="utf-8")
    tree = ast.parse(inspect.getsource(StaticGameDataSkillDamageQueriesMixin))
    for method, output in (("list_skill_level_ability_candidates", "skill_level_candidates.sql"),
                           ("list_skill_damage_owner_character_ids", "skill_damage_owners.sql")):
        function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == method)
        sql = next(node.value for node in ast.walk(function)
                   if isinstance(node, ast.Constant) and isinstance(node.value, str) and "SELECT" in node.value)
        (args.output / output).write_text(sql.strip() + "\n", encoding="utf-8")
    payload = {"schema": 1, "passives": [asdict(row) for row in _CATALOG], "direct_passive_rules": _DIRECT_RULES,
               "fork_state_basis": FORK_STATE_BASIS, "fork_damage_basis": FORK_DAMAGE_BASIS}
    payload["dot_names"] = DOT_NAMES
    payload["treatment_text"] = TREATMENT_TEXT
    payload["treatment_buff_text"] = TREATMENT_BUFF_TEXT
    payload["creation_rules"] = [{**asdict(row), "ability_ids": sorted(row.ability_ids),
                                   "gameplay_effect_ids": sorted(row.gameplay_effect_ids)} for row in CREATION_RULES]
    payload["creation_policies"] = {key: asdict(value) for key, value in CREATION_POLICIES.items()}
    payload["dot_basis"] = {
        f"{kind}:{int(lower)}:{int(early)}:{int(zankou)}": dot_basis({
            "kind": kind, "lower": lower, "early": early,
            "effect": "buff_reaction_5_new_1036" if zankou else "buff_reaction_5_new",
        }) for kind in DOT_NAMES for lower in (False, True) for early in (False, True) for zankou in (False, True)
    }
    with StaticGameDataDao(args.static) as dao:
        payload["dataset"] = dao._rows("SELECT dataset_id,importer_version,built_at_utc FROM dataset")
        definitions = {row["effect_definition_id"]: row for row in dao.list_combat_effect_definitions()}
        for character in dao.list_characters():
            for effect in [*(f"Effect{i}" for i in range(1, 7)), "resonance_3", "resonance_6"]:
                definitions.setdefault(f"character_awaken:{character['character_id']}:{effect}", None)
        templates = {}
        for identity, definition in definitions.items():
            selected = _SelectedEffect(0, "$source_name", identity, definition)
            with patch("src.services.battle_report.buffs.inference._selected_effects", return_value=(selected,)):
                templates[identity] = [asdict(row) for row in BattleBuffInferenceService.load_rules(dao, None)]
        payload["effect_rules"] = templates
        payload["skill_bound_rules"] = {
            str(character["character_id"]): [asdict(row) for row in _skill_rules(dao, {"characters": [{
                "character_id": character["character_id"], "observed_name": "$source_name",
            }]})] for character in dao.list_characters()
        }
        payload["passive_rules"] = {}
        for raw in dao.list_characters():
            identity = raw["character_id"]
            for stage in (0, 2, 4):
                for effect6 in (False, True):
                    character = {"character_id": identity, "observed_name": "$source_name", "breakthrough_stage": stage,
                                 "profile": {"awakening_selection_initialized": True,
                                             "selected_awaken_effect_ids": ["Effect6"] if effect6 else []}}
                    payload["passive_rules"][f"{identity}:{stage}:{int(effect6)}"] = [
                        asdict(row) for row in BattleCharacterPassiveService.load_rules({"characters": [character]}, BattleStaticBuffRule)
                    ]
        payload["source_attack_rules"] = {}
        for identity in (1003, 1020):
            for mask in ((0, 8) if identity == 1003 else (0,)):
                for level in range(1, 16):
                    character = {"character_id": identity, "observed_name": "$source_name",
                                 "profile": {"awakening_selection_initialized": True,
                                             "selected_awaken_effect_ids": [f"Effect{i+1}" for i in range(6) if mask & (1 << i)],
                                             "skill_levels": {"GA_Haniel_Skill": level, "GA_Haniel_UltraSkill": level}},
                                 "stats": [{"source_group": "character", "property_id": "AtkBase", "value": 1.0}]}
                    payload["source_attack_rules"][f"{identity}:{mask}:{level}"] = [asdict(row) for row in
                        BattleCharacterSkillBuffService.load_rules(dao, {"characters": [character]}, BattleStaticBuffRule, BattleBuffModifierEvidence)]
    (args.output / "public_static_declarations.json").write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()
