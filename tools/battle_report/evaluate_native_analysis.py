# 在只读原库的一致性副本上逐场比较 Python 与独立 Rust 数值后端。
"""Compare complete saved-report analysis results without editing live databases."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import fields, is_dataclass
import hashlib
import json
import math
from pathlib import Path
import sys
import sqlite3
import tempfile
import time
import traceback
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.battle_report.evaluate_existing_reports import (  # noqa: E402
    _readonly_connection,
    _sha256,
)


ABS_TOLERANCE = 1e-9
REL_TOLERANCE = 1e-12
DIFFERENCE_LIMIT = 100


def _clone_database(source_path: Path, destination_path: Path) -> None:
    with closing(_readonly_connection(source_path)) as source:
        with closing(sqlite3.connect(destination_path)) as destination:
            source.backup(destination)


def _account_id(database: Path) -> str:
    with closing(_readonly_connection(database)) as connection:
        row = connection.execute("SELECT account_id FROM database_profile LIMIT 1").fetchone()
    if row is None or not str(row[0]).strip():
        raise RuntimeError("account_database_profile_missing")
    return str(row[0])


def _safe_value(value: Any) -> Any:
    """Retain numerical evidence, but never export arbitrary identifiers or text."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    encoded = repr(value).encode("utf-8")
    return {"type": type(value).__name__, "sha256": hashlib.sha256(encoded).hexdigest()}


def compare_results(left: Any, right: Any, limit: int = DIFFERENCE_LIMIT) -> dict[str, Any]:
    """Walk every field, including confidence, gaps, attribution and damage values."""
    differences: list[dict[str, Any]] = []
    total = 0
    numeric_fields = 0
    max_absolute_difference = 0.0

    def mismatch(path: str, a: Any, b: Any, reason: str) -> None:
        nonlocal total
        total += 1
        if len(differences) < limit:
            differences.append({
                "path": path, "reason": reason,
                "python": _safe_value(a), "rust": _safe_value(b),
            })

    def visit(a: Any, b: Any, path: str) -> None:
        nonlocal numeric_fields, max_absolute_difference
        if type(a) is not type(b):
            mismatch(path, a, b, "type")
        elif is_dataclass(a) and not isinstance(a, type):
            for field in fields(a):
                visit(getattr(a, field.name), getattr(b, field.name), f"{path}.{field.name}")
        elif isinstance(a, Mapping):
            # Mapping keys may be real game UIDs; only their ordinal is exported.
            for index, key in enumerate(a):
                if key not in b:
                    mismatch(f"{path}.key[{index}]", key, None, "missing_key")
                else:
                    visit(a[key], b[key], f"{path}.value[{index}]")
            for index, key in enumerate(b):
                if key not in a:
                    mismatch(f"{path}.extra_key[{index}]", None, key, "extra_key")
        elif isinstance(a, tuple | list):
            if len(a) != len(b):
                mismatch(f"{path}.length", len(a), len(b), "length")
            for index, (item_a, item_b) in enumerate(zip(a, b)):
                visit(item_a, item_b, f"{path}[{index}]")
        elif isinstance(a, float):
            numeric_fields += 1
            delta = abs(a - b)
            if math.isfinite(delta):
                max_absolute_difference = max(max_absolute_difference, delta)
            if not math.isfinite(a) or not math.isfinite(b) or not math.isclose(
                a, b, abs_tol=ABS_TOLERANCE, rel_tol=REL_TOLERANCE,
            ):
                mismatch(path, a, b, "numeric")
        elif a != b:
            mismatch(path, a, b, "value")

    visit(left, right, "analysis")
    return {
        "equal": total == 0,
        "difference_count": total,
        "differences": differences,
        "differences_truncated": total > len(differences),
        "compared_float_count": numeric_fields,
        "maximum_absolute_float_difference": max_absolute_difference,
    }


def _record_inventory(database: Path) -> tuple[dict[str, Any], ...]:
    with closing(_readonly_connection(database)) as connection:
        rows = connection.execute("""
            SELECT r.battle_record_id, r.axis_stored_hits, r.axis_complete,
                   EXISTS(SELECT 1 FROM battle_build_snapshot b
                          WHERE b.battle_record_id = r.battle_record_id),
                   EXISTS(SELECT 1 FROM battle_build_edit e
                          WHERE e.battle_record_id = r.battle_record_id AND e.is_active = 1),
                   (SELECT COUNT(*) FROM battle_hit_evidence h
                    JOIN battle_axis_capture a ON a.capture_id = h.capture_id
                    WHERE a.battle_record_id = r.battle_record_id)
            FROM battle_record r ORDER BY r.battle_record_id
        """).fetchall()
        frozen_contexts = {
            int(row[0]): {
                "source_inventory_snapshot_id": row[1], "account_generation": row[2],
                "static_dataset_id": row[3], "static_schema_version": row[4],
                "profile_schema_version": row[5], "formula_model_version": row[6],
            }
            for row in connection.execute("""
                SELECT battle_record_id, source_inventory_snapshot_id, account_generation,
                       static_dataset_id, static_schema_version, profile_schema_version,
                       formula_model_version FROM battle_build_snapshot
            """)
        }
    records = []
    for record_id, stored_hits, complete, has_build, has_edit, actual_hits in rows:
        reasons = []
        if not has_build:
            reasons.append("missing_build_snapshot")
        if not stored_hits or not actual_hits:
            reasons.append("missing_hit_axis")
        records.append({
            "record_id": int(record_id), "stored_hits": int(stored_hits or 0),
            "actual_axis_hits": int(actual_hits), "axis_complete": bool(complete),
            "has_build": bool(has_build), "has_active_build_edit": bool(has_edit),
            "frozen_build_context": frozen_contexts.get(int(record_id)),
            "skip_reasons": reasons,
        })
    return tuple(records)


def _case_modes(record: Mapping[str, Any], requested: str) -> tuple[str, ...]:
    if requested == "both":
        return ("original", "effective") if record["has_active_build_edit"] else ("original",)
    return (requested,)


def _dataset_version(database: Path) -> str:
    manifest = database.parent / "manifest.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        declared = data.get("database", {})
        if declared.get("sha256", "").lower() == _sha256(database).lower():
            return str(declared.get("dataset_id") or f"sha256:{_sha256(database)}")
    return f"sha256:{_sha256(database)}"


def _load_analysis(database: Path, static_database: Path, account: str,
                   record_id: int, mode: str, client: Any = None,
                   progress_metrics: dict[str, float] | None = None) -> tuple[Any, float]:
    from src.services.battle_report.history.service import BattleReportHistoryService
    from src.services.battle_report.persistence import (
        BattleReportPersistenceDependencies,
    )

    dependencies = BattleReportPersistenceDependencies(
        account_id=account, user_database_path=database, generation=0,
        static_database_path=static_database,
    )
    kwargs = {} if client is None else {"direct_formula_backend": client}
    history = BattleReportHistoryService(
        dependencies=dependencies, context_is_current=lambda _context: True, **kwargs,
    )
    started = time.perf_counter()
    phase_started = started
    last_notice = started
    active_phase = "load"

    def progress(event: Any) -> None:
        nonlocal phase_started, active_phase, last_notice
        now = time.perf_counter()
        if event.phase != active_phase:
            if progress_metrics is not None:
                progress_metrics[active_phase] = (
                    progress_metrics.get(active_phase, 0.0) + now - phase_started
                )
            phase_started, active_phase = now, event.phase
        if now - last_notice >= 30.0:
            backend = "python" if client is None else "rust"
            print(
                f"progress record={record_id} mode={mode} backend={backend} "
                f"phase={active_phase} completed={event.completed} total={event.total}",
                flush=True,
            )
            last_notice = now

    try:
        analysis = history.load_analysis(
            record_id, use_build_edit=mode == "effective", include_buff_inference=True,
            include_hit_replays=True, include_buff_counterfactuals=True,
            progress_callback=progress,
        )
    finally:
        if progress_metrics is not None:
            progress_metrics[active_phase] = (
                progress_metrics.get(active_phase, 0.0) + time.perf_counter() - phase_started
            )
    elapsed = time.perf_counter() - started
    if analysis is None:
        raise RuntimeError("saved_report_analysis_missing")
    return analysis, elapsed


def _evaluate_case(record: Mapping[str, Any], mode: str, frozen_database: Path,
                   static_database: Path, account: str, executable: Path,
                   dataset_version: str, scratch: Path) -> dict[str, Any]:
    from src.integrations.nte_analysis_core import NteAnalysisCoreClient

    record_id = int(record["record_id"])
    result: dict[str, Any] = {
        "record_id": record_id, "build_mode": mode,
        "has_active_build_edit": record["has_active_build_edit"],
        "effective_equals_original_without_edit": not record["has_active_build_edit"],
        "actual_axis_hits": record["actual_axis_hits"],
        "axis_complete": record["axis_complete"],
    }
    client = None
    try:
        with tempfile.TemporaryDirectory(prefix=f"case-{record_id}-{mode}-", dir=scratch) as path:
            work = Path(path)
            python_database, rust_database = work / "python.sqlite3", work / "rust.sqlite3"
            _clone_database(frozen_database, python_database)
            _clone_database(frozen_database, rust_database)
            client = NteAnalysisCoreClient.from_executable(executable=executable, dataset_version=dataset_version)
            # Alternate order to expose, rather than systematically hide, warm-cache effects.
            order = ("python", "rust") if record_id % 2 else ("rust", "python")
            outputs = {}
            for backend in order:
                phase_metrics: dict[str, float] = {}
                result[f"{backend}_phase_seconds"] = phase_metrics
                analysis, elapsed = _load_analysis(
                    python_database if backend == "python" else rust_database,
                    static_database, account, record_id, mode,
                    None if backend == "python" else client,
                    phase_metrics,
                )
                outputs[backend] = analysis
                result[f"{backend}_seconds"] = elapsed
            result["execution_order"] = list(order)
            result["comparison"] = compare_results(outputs["python"], outputs["rust"])
            result["native_stats"] = dict(client.stats)
            result["analysis_hit_count"] = len(outputs["python"].hits)
            result["replay_hit_count"] = len(outputs["python"].hit_replays)
            result["buff_counterfactual_count"] = len(outputs["python"].buff_counterfactuals)
            result["native_formula_jobs"] = int(client.stats.get("jobs", 0))
            result["native_coverage_note"] = (
                "jobs count direct-formula evaluations, including repeated counterfactual "
                "candidates; they are not unique native hits or full-axis Rust coverage"
            )
            result["speedup"] = result["python_seconds"] / result["rust_seconds"]
            result["status"] = "passed" if result["comparison"]["equal"] else "mismatch"
    except Exception as error:
        # Exception messages may include raw RPC values or private paths.
        result.update(status="error", error_type=type(error).__name__, error=_safe_value(str(error)))
        result["error_frames"] = [
            {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(error.__traceback__)
        ]
    finally:
        if client is not None:
            result.setdefault("native_stats", dict(client.stats))
            close = getattr(client, "close", None)
            if close is not None:
                close()
    return result


def _checkpoint(output: Path, report: dict[str, Any]) -> None:
    cases = report["cases"]
    for case in cases:
        if case["status"] == "passed" and not case.get("native_formula_jobs", 0):
            case["status"] = "no_native_coverage"
    completed = [case for case in cases if case["status"] in (
        "passed", "mismatch", "no_native_coverage",
    )]
    jobs = sum(int(case.get("native_stats", {}).get("jobs", 0)) for case in cases)
    report["summary"] = {
        "completed_cases": len(completed),
        "passed_cases": sum(case["status"] == "passed" for case in cases),
        "semantically_equal_cases": sum(case["status"] in (
            "passed", "no_native_coverage",
        ) for case in cases),
        "no_native_coverage_cases": sum(case["status"] == "no_native_coverage" for case in cases),
        "mismatched_cases": sum(case["status"] == "mismatch" for case in cases),
        "error_cases": sum(case["status"] == "error" for case in cases),
        "skipped_records": len(report["skipped_records"]),
        "native_formula_jobs": jobs,
        "zero_native_cases": [
            {"record_id": case["record_id"], "build_mode": case["build_mode"]}
            for case in completed if not case.get("native_formula_jobs", 0)
        ],
        "python_seconds": sum(case.get("python_seconds", 0.0) for case in cases),
        "rust_seconds": sum(case.get("rust_seconds", 0.0) for case in cases),
    }
    ordered = dict(report)
    ordered["cases"] = sorted(cases, key=lambda case: (case["record_id"], case["build_mode"]))
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(ordered, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(output)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="在独立数据库副本上比较 Python / Rust 完整战报分析。")
    parser.add_argument("--user-database", type=Path, required=True)
    parser.add_argument("--static-database", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--record-ids", default="", help="逗号分隔 ID；默认枚举全部战报并列明缺失证据。")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--build-mode", choices=("original", "effective", "both"), default="both")
    return parser


def _exit_code(summary: Mapping[str, Any]) -> int:
    return int(bool(summary["mismatched_cases"] or summary["error_cases"]
                    or summary["native_formula_jobs"] == 0))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.workers < 1:
        raise ValueError("workers must be positive")
    for source in (args.user_database, args.static_database, args.executable):
        if not source.is_file():
            raise FileNotFoundError(source.name)
    if args.output.resolve() in {
        args.user_database.resolve(), args.static_database.resolve(), args.executable.resolve(),
    }:
        raise ValueError("output must not overwrite an input")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="native-analysis-validation-") as directory:
        scratch = Path(directory)
        frozen = scratch / "frozen-user.sqlite3"
        frozen_static = scratch / "game_static.sqlite3"
        _clone_database(args.user_database, frozen)
        _clone_database(args.static_database, frozen_static)
        account = _account_id(frozen)
        records = _record_inventory(frozen)
        all_ids = {record["record_id"] for record in records}
        requested = {
            int(token.strip()) for token in args.record_ids.split(",") if token.strip()
        } if args.record_ids.strip() else all_ids
        if requested - all_ids:
            raise ValueError(f"requested record ids do not exist: {sorted(requested - all_ids)}")
        records = tuple(record for record in records if record["record_id"] in requested)
        dataset_version = _dataset_version(args.static_database)
        report: dict[str, Any] = {
            "format_version": 1, "finished": False,
            "user_database_snapshot_sha256": _sha256(frozen),
            "static_database_snapshot_sha256": _sha256(frozen_static),
            "static_database_source_sha256": _sha256(args.static_database),
            "native_executable_sha256": _sha256(args.executable),
            "dataset_version": dataset_version,
            "database_snapshot_method": "SQLite backup from mode=ro; WAL included; no immutable flag",
            "tolerance": {"absolute": ABS_TOLERANCE, "relative": REL_TOLERANCE},
            "comparison_scope": "all fields of complete BattleAnalysisSnapshot; no fields ignored",
            "migration_scope": (
                "Capability-declared frozen battle batches: formulas, buff projection/planning, "
                "state and action inference, treatment, fitting and counterfactual comparison; "
                "Python owns evidence/configuration IO, orchestration and result materialization"
            ),
            "timing_note": (
                "single observation per path; alternating backend order; Python process caches "
                "may be shared; concurrent timings are not an isolated performance benchmark"
            ),
            "workers": args.workers, "build_mode": args.build_mode,
            "inventory": list(records),
            "skipped_records": [record for record in records if record["skip_reasons"]],
            "cases": [],
        }
        _checkpoint(args.output, report)
        jobs = [(record, mode) for record in records if not record["skip_reasons"]
                for mode in _case_modes(record, args.build_mode)]
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            pending = [executor.submit(
                _evaluate_case, record, mode, frozen, frozen_static, account,
                args.executable.resolve(), dataset_version, scratch,
            ) for record, mode in jobs]
            for future in as_completed(pending):
                case = future.result()
                report["cases"].append(case)
                _checkpoint(args.output, report)
                print(
                    f"[{len(report['cases'])}/{len(jobs)}] record={case['record_id']} "
                    f"mode={case['build_mode']} status={case['status']} "
                    f"native_jobs={case.get('native_stats', {}).get('jobs', 0)}",
                    flush=True,
                )
        report["finished"] = True
        _checkpoint(args.output, report)
        return _exit_code(report["summary"])


if __name__ == "__main__":
    raise SystemExit(main())
