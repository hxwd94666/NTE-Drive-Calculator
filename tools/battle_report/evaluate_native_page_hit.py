# 在只读账号副本上对照原生数据库入口与 Python 逐击页，结果只记录脱敏字段差异。
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.services.battle_report.history.service import BattleReportHistoryService  # noqa: E402
from src.services.battle_report.persistence import BattleReportPersistenceDependencies  # noqa: E402
from tools.battle_report.evaluate_native_analysis import _clone_database, compare_results  # noqa: E402
from tools.battle_report.evaluate_native_application_parts import wire, numeric_wire  # noqa: E402


def normalize_role_ties(value):
    """Only reorder contiguous equal-damage roles; every identity and field remains compared."""
    if isinstance(value, list):
        return [normalize_role_ties(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: normalize_role_ties(item) for key, item in value.items()}
    roles = result.get("roles")
    if not isinstance(roles, list) or not roles:
        return result
    field = next((key for key in ("damage", "total_damage")
                  if all(isinstance(row, dict) and type(row.get(key)) in (int, float)
                         for row in roles)), None)
    if field is None:
        return result
    start = 0
    while start < len(roles):
        end = start + 1
        while end < len(roles) and roles[end][field] == roles[start][field]:
            end += 1
        roles[start:end] = sorted(roles[start:end], key=lambda row: (
            row.get("character_id") is None, row.get("character_id") or 0,
        ))
        start = end
    return result


def evaluate(args):
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="native-page-hit-", dir=args.output.parent) as temporary:
        python_db = Path(temporary).resolve() / "python.sqlite3"
        native_db = Path(temporary).resolve() / "native.sqlite3"
        _clone_database(args.database.resolve(), python_db)
        _clone_database(args.database.resolve(), native_db)
        with closing(sqlite3.connect(args.static.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            dataset = conn.execute("SELECT dataset_id FROM dataset LIMIT 1").fetchone()[0]
        with closing(sqlite3.connect(native_db.as_uri() + "?mode=ro", uri=True)) as conn:
            account = conn.execute("SELECT account_id FROM database_profile LIMIT 1").fetchone()[0]
        request = {"schema_version": "nte-analysis-request-v1", "batch_kind": "battle_page_v1",
                   "dataset_version": dataset, "request": {"account_id": account, "generation": 0,
                   "battle_record_id": args.record, "user_database_path": str(native_db),
                   "static_database_path": str(args.static.resolve()),
                   "semantics_path": str(ROOT / "config/gameplay_effect_semantics.json"), "detail_level": args.level, "detail_scope": args.detail_scope,
                   "start_us": args.start_us, "end_us": args.end_us}}
        started = time.perf_counter()
        native = subprocess.run([str(args.executable.resolve())], input=json.dumps(request),
                                text=True, encoding="utf-8", capture_output=True, timeout=300)
        native_seconds = time.perf_counter() - started
        if native.returncode:
            code = json.loads(native.stdout).get("error", {}).get("code", "native_process_failed")
            args.output.write_text(json.dumps({"native_error": code, "record": args.record}), encoding="utf-8")
            print(json.dumps({"native_error": code}))
            return 1
        page = json.loads(native.stdout)["result"]
        actual = page["analysis"]
        decode_error = None
        try:
            from src.integrations.battle_analysis.page_wire import decode_page
            decode_page(page)
        except Exception as error:
            decode_error = {"type": type(error).__name__, "message": str(error)}
        history = BattleReportHistoryService(dependencies=BattleReportPersistenceDependencies(
            account, python_db, 0, args.static.resolve()), context_is_current=lambda _: True)
        started = time.perf_counter()
        expected = wire(history.load_analysis(args.record, include_buff_counterfactuals=False,
                    include_buff_inference=args.level != "overview", include_hit_replays=args.level != "overview",
                    detail_scope=args.detail_scope, start_us=args.start_us, end_us=args.end_us))
        python_seconds = time.perf_counter() - started
        expected, actual = normalize_role_ties(expected), normalize_role_ties(actual)
        comparison = compare_results(numeric_wire(expected), numeric_wire(actual), limit=150)
        if args.diagnostic_wire and comparison["difference_count"]:
            # Private local diagnostic only; callers must keep this output under ignored output/.
            args.output.with_suffix(".wire.json").write_text(json.dumps(
                {"expected": expected, "actual": actual}, ensure_ascii=False, allow_nan=False,
            ), encoding="utf-8")
        fields = {}
        expected_fields = expected if isinstance(expected, dict) else {}
        actual_fields = actual if isinstance(actual, dict) else {}
        for key in sorted(set(expected_fields) | set(actual_fields)):
            values = compare_results(numeric_wire(expected_fields.get(key)), numeric_wire(actual_fields.get(key)), limit=12)
            if values["difference_count"]:
                fields[key] = values
        report = {"record": args.record, "native_seconds": native_seconds, "python_seconds": python_seconds,
                  "comparison": comparison, "fields": fields, "wire_decode_error": decode_error}
        # Buff names/definition ids are public static identities; never emit runtime targets/event ids.
        for key in ("buff_intervals", "baselines"):
            left, right = expected_fields.get(key, []), actual_fields.get(key, [])
            if len(left) != len(right):
                report[f"{key}_counts"] = [len(left), len(right)]
            if key == "buff_intervals":
                def buckets(rows):
                    result = {}
                    for row in rows:
                        source = row["source_effect_definition_id"]
                        result[source] = result.get(source, 0) + 1
                    return result
                report["buff_definition_counts"] = {"python": buckets(left), "native": buckets(right)}
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"record": args.record, "native_seconds": native_seconds,
                          "python_seconds": python_seconds, "difference_count": comparison["difference_count"],
                          "fields": {key: value["difference_count"] for key, value in fields.items()}, "wire_decode_error": decode_error}))
        return int(bool(comparison["difference_count"] or decode_error))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--static", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--record", type=int, default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--level", choices=("hit", "overview"), default="hit")
    parser.add_argument("--detail-scope", default=None)
    parser.add_argument("--start-us", type=int, default=None)
    parser.add_argument("--end-us", type=int, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnostic-wire", action="store_true")
    args = parser.parse_args()
    if not args.all:
        if args.record is None:
            parser.error("--record or --all is required")
        return evaluate(args)
    with closing(sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        ids = [row[0] for row in conn.execute("SELECT battle_record_id FROM battle_record ORDER BY battle_record_id")]
    results = []
    destination = args.output
    for identity in ids:
        args.record = identity
        args.output = destination.parent / f"{destination.stem}-{identity}.json"
        try:
            code = evaluate(args)
            report = json.loads(args.output.read_text(encoding="utf-8"))
        except Exception as error:
            code, report = 1, {"python_error_type": type(error).__name__}
            print(json.dumps({"record": identity, **report}), flush=True)
        results.append({"record": identity, "failed": bool(code), **report})
        destination.write_text(json.dumps({"cases": len(results), "failed": sum(r["failed"] for r in results),
                                          "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    return int(any(r["failed"] for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
