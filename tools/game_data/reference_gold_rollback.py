# 将已校验的独立图鉴基线整包回滚，仅接受本次修复版本作为目标。
"""Restore the verified reference-catalog baseline as one owned package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.integrations.static_catalog.role_catalog_release import read_role_catalog
from tools.game_data.promote_role_catalog import promote_role_catalog


BASELINE_SHA256 = "BE81D63737722AADD14441718C6FD13E5DFDC2FCB6EFFD8D1AA863A9019CEC1C"
MODIFIED_SHA256 = "500414EADB0EB59710271D5D064616523089EACB588D05BB0D784E570E0DD524"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline-dir", type=Path,
        default=Path(__file__).resolve().parent / "baseline",
    )
    parser.add_argument("--target-dir", type=Path, default=ROOT / "data/role_catalog")
    args = parser.parse_args()
    baseline = args.baseline_dir.expanduser().resolve()
    if read_role_catalog(baseline).sha256 != BASELINE_SHA256:
        raise ValueError("回滚备份哈希不符")
    target = args.target_dir.expanduser().resolve()
    if read_role_catalog(target).sha256 != MODIFIED_SHA256:
        raise ValueError("目标并非本次修复版本")
    result = promote_role_catalog(
        {"database_path": str(baseline / "game_static.sqlite3")}, target
    )
    if read_role_catalog(target).sha256 != BASELINE_SHA256:
        raise ValueError("回滚后哈希不符")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
