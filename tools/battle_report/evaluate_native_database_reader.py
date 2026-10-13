# 在只读战报副本上对照 Rust 数据库读取字段，不输出私有内容。
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.storage.sqlite.user_data_dao import UserDataDao  # noqa: E402
from tools.battle_report.evaluate_native_analysis import compare_results  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    database = args.database.resolve()
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    # 只读 oracle 不初始化或迁移结构；本次读事务复用现有 DAO 查询。
    dao = UserDataDao.__new__(UserDataDao)
    dao._connection = connection
    results = []
    try:
        ids = connection.execute("SELECT battle_record_id FROM battle_record ORDER BY battle_record_id")
        for (record_id,) in ids:
            expected = {
                key: function(record_id)
                for key, function in (
                    ("record", dao.load_battle_record),
                    ("build", dao.load_battle_build_snapshot),
                    ("edit", dao.load_battle_build_edit),
                    ("target", dao.load_battle_target_condition),
                    ("inferred", dao.load_battle_inferred_target_snapshot),
                )
            }
            expected = json.loads(json.dumps(expected, ensure_ascii=False, allow_nan=False))
            process = subprocess.run(
                [str(args.executable.resolve())],
                input=json.dumps({"user_path": str(database), "record_id": record_id}).encode("utf-8"),
                capture_output=True, timeout=30, check=False,
            )
            if process.returncode:
                results.append({"record_id": record_id, "error": "native_reader_failed"})
                continue
            comparison = compare_results(expected, json.loads(process.stdout))
            results.append({"record_id": record_id, **comparison})
    finally:
        dao.close()
    summary = {
        "cases": len(results),
        "failed": sum(bool(row.get("error") or row.get("difference_count")) for row in results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}))
    return int(summary["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
