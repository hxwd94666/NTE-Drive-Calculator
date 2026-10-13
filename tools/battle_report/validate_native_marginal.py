# 使用已有本机冻结分析验证完整 Rust 边际面板，不读写活动账号库。
from __future__ import annotations

import argparse
import json
import pickle
import time
from dataclasses import asdict
from pathlib import Path

from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.marginal.calculation import BattleMarginalCalculationService
from tools.battle_report.evaluate_native_analysis import compare_results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-pickle", type=Path, required=True)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    saved = pickle.loads(args.analysis_pickle.read_bytes())
    analysis = saved["page"]["analysis"]
    native = NteAnalysisCoreClient.from_executable(args.exe, "frozen-marginal-validation")
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {"hits": len(analysis.hits), "cases": []}
    for baseline in analysis.baselines:
        inputs = {"analysis": asdict(analysis), "character_id": baseline.character_id,
                  "edited_values": {}, "units": saved["units"]}
        started = time.perf_counter()
        expected = BattleMarginalCalculationService.calculate(
            analysis=analysis, character_id=baseline.character_id, edited_values={}, units=saved["units"],
        )
        python_seconds = time.perf_counter() - started
        started = time.perf_counter()
        actual, = native.compute_batch("battle_marginal_v1", (inputs,))
        native_seconds = time.perf_counter() - started
        expected = json.loads(json.dumps([asdict(row) for row in expected], ensure_ascii=False))
        difference = compare_results(expected, actual["results"])
        case = {"character_id": baseline.character_id, "python_seconds": python_seconds,
                "native_seconds": native_seconds, "difference": difference}
        summary["cases"].append(case)
        (args.output / f"panel-{baseline.character_id}.pickle").write_bytes(pickle.dumps(
            {"inputs": inputs, "expected": expected, "actual": actual["results"]},
        ))
        (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(case, ensure_ascii=False), flush=True)
    return 0 if all(case["difference"]["equal"] for case in summary["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
