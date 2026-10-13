# 冻结候选核心并顺序核对全库页面、显式范围与隔离库首次拟合，不执行性能验收。
"""仅在显式准备的隔离库副本核对原生页面；差分耗时不作为性能验收。"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.battle_report.evaluate_native_analysis import _clone_database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--static", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--focus-record", type=int)
    parser.add_argument("--range-record", type=int)
    parser.add_argument("--start-us", type=int)
    parser.add_argument("--end-us", type=int)
    parser.add_argument("--fresh-fit-record", type=int)
    args = parser.parse_args()
    if args.range_record is not None and (args.start_us is None or args.end_us is None):
        parser.error("--range-record requires --start-us and --end-us")
    destination = args.output_directory.resolve()
    # 隔离数据库副本只放本仓库忽略的 output，不能进入源码或交付目录。
    if not destination.is_relative_to((ROOT / "output").resolve()):
        parser.error("--output-directory must be under the repository output directory")
    destination.mkdir(parents=True, exist_ok=True)
    binary = destination / "nte-analysis-core.exe"
    source_hash = hashlib.sha256(args.executable.read_bytes()).hexdigest()
    if binary.exists() and hashlib.sha256(binary.read_bytes()).hexdigest() != source_hash:
        parser.error("output directory already contains another core; choose a new directory")
    if not binary.exists():
        shutil.copy2(args.executable, binary)
    database = destination / "suite-user.sqlite3"
    static = destination / "suite-static.sqlite3"
    _clone_database(args.database.resolve(), database)
    _clone_database(args.static.resolve(), static)
    summary = {"binary_sha256": source_hash.upper(), "timing_status": "diagnostic_only", "runs": []}

    def run(name: str, level: str, extra: list[str], db: Path = database) -> None:
        output = destination / f"{name}.json"
        command = [sys.executable, str(ROOT / "tools/battle_report/evaluate_native_page_hit.py"),
                   "--database", str(db), "--static", str(static), "--executable", str(binary),
                   "--level", level, "--output", str(output), *extra]
        with (destination / f"{name}.log").open("w", encoding="utf-8") as stream:
            process = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=False)
        report = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
        result = {"name": name, "exit_code": process.returncode}
        if "cases" in report:
            result.update(cases=report["cases"], failed=report["failed"],
                          failed_records=[row["record"] for row in report["results"] if row["failed"]])
        else:
            result.update(difference_count=report.get("comparison", {}).get("difference_count"),
                          wire_decode_error=report.get("wire_decode_error"),
                          native_error=report.get("native_error"))
        summary["runs"].append(result)
        (destination / "suite-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)

    run("all-hit", "hit", ["--all"])
    run("all-overview", "overview", ["--all"])
    if args.focus_record is not None:
        run("focus-hit", "hit", ["--record", str(args.focus_record)])
    if args.range_record is not None:
        run("range-hit", "hit", ["--record", str(args.range_record),
                                  "--start-us", str(args.start_us), "--end-us", str(args.end_us)])
    if args.fresh_fit_record is not None:
        fresh = destination / "fresh-fit-user.sqlite3"
        _clone_database(database, fresh)
        with closing(sqlite3.connect(fresh)) as conn:
            conn.execute("DELETE FROM battle_inferred_target_snapshot WHERE battle_record_id=?",
                         (args.fresh_fit_record,))
            conn.commit()
        for level in ("hit", "overview"):
            run(f"fresh-{level}", level, ["--record", str(args.fresh_fit_record)], fresh)
    return int(any(row["exit_code"] for row in summary["runs"]))


if __name__ == "__main__":
    raise SystemExit(main())
