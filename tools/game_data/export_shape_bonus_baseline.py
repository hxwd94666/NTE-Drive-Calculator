# 从确认未修改的发行静态库导出额外形状差异迁移基线。
"""生成升级时识别旧版用户额外形状修改所需的发行基线。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.shared_data_migration_service import (
    write_shape_bonus_baseline,
)


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
