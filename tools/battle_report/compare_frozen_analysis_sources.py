# 在独立解释器中比较冻结旧源码与候选原生后端的全部真实战报结果。
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.battle_report.evaluate_native_analysis import _record_inventory, _sha256  # noqa: E402


def _source_digest(root):
    files = {path.relative_to(root).as_posix(): _sha256(path)
             for path in (root / "src").rglob("*.py") if "__pycache__" not in path.parts}
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf8")).hexdigest()


def _run_case(args, record, mode):
    """先记录旧源码结果再对照候选；等价与实际原生任务覆盖须分别成立。"""
    case = f"history:{record['record_id']}:{mode}"
    results = {}
    for label, source, backend in (
        ("baseline", args.baseline_source, args.baseline_backend),
        ("candidate", args.candidate_source, "native"),
    ):
        destination = args.output / label
        command = [
            sys.executable, str(ROOT / "tools/battle_report/benchmark_native_analysis.py"),
            "--worker", "--source-root", str(source),
            "--user-database", str(args.user_database), "--static-database", str(args.static_database),
            "--output-dir", str(destination), "--backend", backend, "--case", case, "--run", "1",
        ]
        if label == "candidate":
            command.extend(["--reference-dir", str(args.output / "baseline"), "--executable", str(args.executable)])
        elif backend == "native":
            command.extend(["--executable", str(args.baseline_executable)])
        logfile = args.output / f"{label}-{record['record_id']}-{mode}.log"
        environment = dict(os.environ, PYTHONHASHSEED="0", PYTHONIOENCODING="utf-8")
        with logfile.open("w", encoding="utf8") as stream:
            completed = subprocess.run(
                command, env=environment, cwd=source, stdout=stream, stderr=subprocess.STDOUT, check=False,
            )
        resultfile = destination / case.replace(":", "-") / "run-1.json"
        result = json.loads(resultfile.read_text(encoding="utf8")) if resultfile.exists() else {
            "status": "error", "error_type": "WorkerExitedWithoutResult",
        }
        results[label] = result
        if completed.returncode != 0 or result["status"] != "passed":
            return {"record_id": record["record_id"], "mode": mode,
                    "status": "mismatch" if result["status"] == "mismatch" else "error", "results": results}
    jobs = int(results["candidate"].get("native_stats", {}).get("jobs", 0))
    projection_jobs = int(results["candidate"].get("native_stats", {}).get("projection_jobs", 0))
    return {
        "record_id": record["record_id"], "mode": mode,
        "status": "passed" if jobs or projection_jobs else "no_native_coverage", "results": results,
    }


def _save(args, report):
    """原子更新审计摘要；任何差异、错误或所需原生覆盖缺口均保留失败退出。"""
    cases = report["cases"]
    candidates = [case["results"]["candidate"] for case in cases if "candidate" in case["results"]]
    comparisons = [row["comparisons"]["baseline"] for row in candidates if "baseline" in row.get("comparisons", {})]
    report["summary"] = {
        "completed_cases": len(cases),
        "equal_cases": sum(row["equal"] for row in comparisons),
        "native_covered_cases": sum(case["status"] == "passed" for case in cases),
        "native_covered_records": len({case["record_id"] for case in cases if case["status"] == "passed"}),
        "no_native_coverage_cases": sum(case["status"] == "no_native_coverage" for case in cases),
        "mismatched_cases": sum(case["status"] == "mismatch" for case in cases),
        "error_cases": sum(case["status"] == "error" for case in cases),
        "native_jobs": sum(int(row.get("native_stats", {}).get("jobs", 0)) for row in candidates),
        "native_batches": sum(int(row.get("native_stats", {}).get("batch_calls", 0)) for row in candidates),
        "native_projection_jobs": sum(int(row.get("native_stats", {}).get("projection_jobs", 0)) for row in candidates),
        "native_projection_batches": sum(int(row.get("native_stats", {}).get("projection_batch_calls", 0)) for row in candidates),
        "native_numeric_covered_cases": sum(int(row.get("native_stats", {}).get("jobs", 0)) > 0 for row in candidates),
        "native_projection_covered_cases": sum(int(row.get("native_stats", {}).get("projection_jobs", 0)) > 0 for row in candidates),
        "no_numeric_native_coverage_cases": sum(int(row.get("native_stats", {}).get("jobs", 0)) == 0 for row in candidates),
        "no_projection_native_coverage_cases": sum(int(row.get("native_stats", {}).get("projection_jobs", 0)) == 0 for row in candidates),
        "compared_float_count": sum(row["compared_float_count"] for row in comparisons),
        "maximum_absolute_float_difference": max((row["maximum_absolute_float_difference"] for row in comparisons), default=0.0),
    }
    report["validation_exit_code"] = int(bool(
        report["summary"]["mismatched_cases"] or report["summary"]["error_cases"]
        or not (report["summary"]["native_jobs"] or report["summary"]["native_projection_jobs"])
        or (report.get("require_projection", False) and not report["summary"]["native_projection_jobs"])
    ))
    report["cases"] = sorted(cases, key=lambda case: (case["record_id"], case["mode"]))
    temporary = args.output / "summary.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    temporary.replace(args.output / "summary.json")


def main():
    parser = argparse.ArgumentParser(description="跨冻结旧源码与候选原生路径进行完整战报差分。")
    parser.add_argument("--baseline-source", required=True, type=Path)
    parser.add_argument("--baseline-backend", choices=("python", "native"), default="python")
    parser.add_argument("--baseline-executable", type=Path)
    parser.add_argument("--candidate-source", required=True, type=Path)
    parser.add_argument("--user-database", required=True, type=Path)
    parser.add_argument("--static-database", required=True, type=Path)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--require-projection", action="store_true", help="Fail if no native Buff projection jobs execute in the entire audit")
    args = parser.parse_args()
    for field in ("baseline_source", "candidate_source", "user_database", "static_database", "executable", "output"):
        setattr(args, field, getattr(args, field).resolve())
    if args.baseline_backend == "native":
        args.baseline_executable = (args.baseline_executable or args.baseline_source / "third_party/analysis-core/bin/nte-analysis-core.exe").resolve()
        if not args.baseline_executable.is_file():
            raise ValueError("baseline native executable is missing")
    elif args.baseline_executable is not None:
        raise ValueError("baseline executable requires native baseline backend")
    if args.workers < 1:
        raise ValueError("workers must be positive")
    if args.output.exists():
        raise ValueError("choose a fresh output directory")
    args.output.mkdir(parents=True)
    inventory = _record_inventory(args.user_database)
    jobs = [(record, mode) for record in inventory if not record["skip_reasons"]
            for mode in (("original", "effective") if record["has_active_build_edit"] else ("original",))]
    report = {
        "finished": False, "inventory": inventory,
        "skipped_records": [record for record in inventory if record["skip_reasons"]],
        "user_snapshot_sha256": _sha256(args.user_database),
        "static_snapshot_sha256": _sha256(args.static_database),
        "executable_sha256": _sha256(args.executable),
        "baseline_backend": args.baseline_backend,
        "require_projection": args.require_projection,
        "baseline_executable_sha256": _sha256(args.baseline_executable) if args.baseline_backend == "native" else None,
        "baseline_source_sha256": _source_digest(args.baseline_source),
        "candidate_source_sha256": _source_digest(args.candidate_source),
        "comparison": f"frozen baseline {args.baseline_backend} source vs candidate native source in independent interpreters",
        "timing_note": "parallel correctness audit; times are not performance benchmarks",
        "coverage_note": "native_jobs counts direct numeric jobs; native_projection_jobs counts Buff projections separately. Both may repeat the same hit across counterfactual candidates.",
        "workers": args.workers, "scheduled_cases": len(jobs), "cases": [],
    }
    _save(args, report)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(_run_case, args, record, mode): (record, mode) for record, mode in jobs}
        for future in as_completed(futures):
            try:
                case = future.result()
            except Exception as error:
                record, mode = futures[future]
                case = {"record_id": record["record_id"], "mode": mode, "status": "error",
                        "results": {}, "error_type": type(error).__name__}
            report["cases"].append(case)
            _save(args, report)
            print(f"[{len(report['cases'])}/{len(jobs)}] record={case['record_id']} mode={case['mode']} status={case['status']}", flush=True)
    report["finished"] = True
    _save(args, report)
    return report["validation_exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
