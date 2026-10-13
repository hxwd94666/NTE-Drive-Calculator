# 从只读冻结战报和既有分析副本重建逐击证据，对完整 Rust 重放结果做差分。
from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.build.edit_projection import apply_battle_build_edit
from src.services.battle_report.build.profile_normalization import normalize_inferred_battle_build
from src.services.battle_report.build.stat_reconstruction import BattleBuildStatReconstructionService
from src.services.battle_report.characters.inferred_facts import BattleInferredCharacterFactService
from src.services.battle_report.replay.service import BattleHitReplayService
from src.services.battle_report.history.service import BattleReportHistoryService
from src.services.battle_report.persistence import BattleReportPersistenceDependencies
from src.services.battle_report.damage.skill_evidence import BattleSkillDamageEvidenceService
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from tools.battle_report.evaluate_native_analysis import compare_results


class ReadOnlySnapshot(UserDataDao):
    def __init__(self, path: Path):
        self.database_path = path.resolve()
        self._connection = sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)
        self._connection.row_factory = sqlite3.Row


def main():
    parser = argparse.ArgumentParser()
    for name in ("analysis-pickle", "user-db", "static-db", "exe", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--record-id", type=int, required=True)
    args = parser.parse_args()
    # This input is an earlier locally generated validation artifact, never an imported capture.
    saved = pickle.loads(args.analysis_pickle.read_bytes())
    analysis = saved["page"]["analysis"] if isinstance(saved, dict) and "page" in saved else saved
    with ReadOnlySnapshot(args.user_db) as dao:
        build = normalize_inferred_battle_build(dao.load_battle_build_snapshot(args.record_id))
        edit = dao.load_battle_build_edit(args.record_id)
        account = dao._one("SELECT account_id FROM database_profile WHERE singleton_id=1", ())["account_id"]
    apply_battle_build_edit(build, edit)
    BattleInferredCharacterFactService.apply_to_build(build, analysis.inferred_character_facts,
                                                      disabled_fact_ids=frozenset())
    deps = BattleReportPersistenceDependencies(account_id=account, user_database_path=args.user_db,
                                              static_database_path=args.static_db, generation=0)
    BattleBuildStatReconstructionService.enrich(build, deps)
    with StaticGameDataDao(args.static_db) as static:
        evidence = BattleSkillDamageEvidenceService.load(static, analysis, build)
    history = BattleReportHistoryService(dependencies=deps, context_is_current=lambda _: True)
    configs = history._load_topple_character_configs(analysis)
    native = NteAnalysisCoreClient.from_executable(args.exe, "frozen-replay-validation")
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {"record_id": args.record_id, "hits": len(analysis.hits),
               "evidence": len(evidence), "cases": []}
    for refined in (False, True):
        started = time.perf_counter()
        expected = BattleHitReplayService.replay(analysis, evidence,
                     topple_character_configs=configs, apply_observed_refinements=refined)
        python_seconds = time.perf_counter() - started
        inputs = {"analysis": asdict(analysis), "skill_evidence": [asdict(e) for e in evidence],
                  "topple_configs": {str(k): asdict(v) for k, v in configs.items()},
                  "apply_observed_refinements": refined}
        started = time.perf_counter()
        actual, = native.compute_batch("battle_replay_v1", (inputs,))
        native_seconds = time.perf_counter() - started
        expected_wire = json.loads(json.dumps([asdict(v) for v in expected], ensure_ascii=False))
        difference = compare_results(expected_wire, actual["results"])
        (args.output / f"replay-{int(refined)}.pickle").write_bytes(pickle.dumps({
            "inputs": inputs, "expected": expected_wire, "actual": actual["results"],
        }))
        case = {"refined": refined, "python_seconds": python_seconds,
                "native_seconds": native_seconds, "difference": difference}
        summary["cases"].append(case)
        (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({**case, "difference": {k: v for k, v in difference.items()
                                                 if k != "differences"}}, ensure_ascii=False), flush=True)
    return 0 if all(case["difference"]["equal"] for case in summary["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
