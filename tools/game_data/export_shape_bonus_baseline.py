# 从确认未修改的发行静态库导出额外形状差异迁移基线。
"""生成升级时识别旧版用户额外形状修改所需的发行基线。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.sqlite.shared_data_dao import read_legacy_shape_bonus_catalog


def write_shape_bonus_baseline(
    database_path: str | Path,
    output_path: str | Path,
    *,
    release_version: str,
) -> dict[str, Any]:
    """从确认未修改的发行库生成机器可读迁移基线。"""

    catalog = read_legacy_shape_bonus_catalog(database_path)
    payload = {
        "format_version": 1,
        "baselines": [
            {
                "release_version": str(release_version),
                "database_sha256": catalog["database_sha256"],
                "dataset_id": catalog["dataset_id"],
                "schema_version": catalog["schema_version"],
                "records": catalog["records"],
            }
        ],
    }
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-version", required=True)
    args = parser.parse_args()
    write_shape_bonus_baseline(
        args.database,
        args.output,
        release_version=args.release_version,
    )
    print(f"Baseline: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
