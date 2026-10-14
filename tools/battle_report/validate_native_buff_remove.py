# 从本机冻结分析及已验证技能输入检查完整 Rust Buff 移除，不访问活动数据库。
from __future__ import annotations

import argparse
import json
import pickle
import time
from dataclasses import asdict
from pathlib import Path

from src.domain.battle_report.replay.models import BattleSkillDamageEvidence
from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.buffs.counterfactual import BattleBuffCounterfactualService
from src.services.battle_report.replay.topple import BattleToppleCharacterConfig
from src.services.battle_report.replay.preparation import BattleHitProjectionPreparationService
from tools.battle_report.evaluate_native_analysis import compare_results


def normalize_float_buckets(value):
    """Empty Python sums may produce int zero in these declared float fields."""
    if isinstance(value, list):
        return [normalize_float_buckets(row) for row in value]
    if isinstance(value, dict):
        float_fields = {"fully_quantified_damage", "partially_quantified_damage",
                        "unavailable_damage", "proven_unchanged_damage"}
        return {key: float(row) if key in float_fields and isinstance(row, (int, float))
                and not isinstance(row, bool) else normalize_float_buckets(row)
                for key, row in value.items()}
    return value


def main():
    parser = argparse.ArgumentParser()
    for name in ("analysis-pickle", "replay-pickle", "exe", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--prepared", action="store_true", help="Use the page's prepared formula-beneficiary aliases")
    args = parser.parse_args()
    analysis = pickle.loads(args.analysis_pickle.read_bytes())["page"]["analysis"]
    replay = pickle.loads(args.replay_pickle.read_bytes())["inputs"]
    evidence = tuple(BattleSkillDamageEvidence(**row) for row in replay["skill_evidence"])
    configs = {int(k): BattleToppleCharacterConfig(**row) for k, row in replay["topple_configs"].items()}
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    projections = BattleHitProjectionPreparationService.prepare(analysis, evidence) if args.prepared else None
    expected = BattleBuffCounterfactualService.calculate(
        analysis, evidence, topple_character_configs=configs,
        original_projection_by_event=None if projections is None else projections.beneficiary_by_event,
    )
    python_seconds = time.perf_counter() - started
    expected = json.loads(json.dumps([asdict(row) for row in expected], ensure_ascii=False))
    inputs = {"analysis": asdict(analysis), "skill_evidence": replay["skill_evidence"],
              "topple_configs": replay["topple_configs"], "prepared_beneficiaries": args.prepared}
    (args.output / "oracle.pickle").write_bytes(pickle.dumps({"inputs": inputs, "expected": expected}))
    print(json.dumps({"stage": "oracle", "python_seconds": python_seconds, "groups": len(expected)}), flush=True)
    native = NteAnalysisCoreClient.from_executable(args.exe, "buff-remove-full-validation")
    started = time.perf_counter()
    actual, = native.compute_batch("battle_buff_remove_v1", (inputs,))
    native_seconds = time.perf_counter() - started
    wire_difference = compare_results(expected, actual["results"])
    difference = compare_results(normalize_float_buckets(expected), normalize_float_buckets(actual["results"]))
    (args.output / "native.pickle").write_bytes(pickle.dumps(actual))
    summary = {"hits": len(analysis.hits), "groups": len(expected), "python_seconds": python_seconds,
               "native_seconds": native_seconds, "difference": difference,
               "wire_difference": wire_difference}
    if args.prepared:
        page_expected = json.loads(json.dumps([asdict(row) for row in analysis.buff_counterfactuals], ensure_ascii=False))
        summary["page_difference"] = compare_results(normalize_float_buckets(page_expected), normalize_float_buckets(actual["results"]))
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if difference["equal"] and summary.get("page_difference", {"equal": True})["equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
