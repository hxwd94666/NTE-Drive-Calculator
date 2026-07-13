# 从 nanoka.cc 静态 JSON 同步角色基础白值到 my_roles_model.json。
"""Sync character base white stats from nanoka static data.

Not a scraper: reads versioned JSON from https://static.nanoka.cc/nte/{version}/...
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import build_cli
from src.features.settings.nanoka_base_stats import (
    DEFAULT_LEVELS,
    NANOKA_DEFAULT_LOCALE,
    NANOKA_DEFAULT_VERSION,
    sync_nanoka_base_stats,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync character base white stats from nanoka.cc into config/my_roles_model.json."
    )
    parser.add_argument(
        "--config-dir",
        default=str(ROOT / "config"),
        help="Config directory containing my_roles_model.json and roles.json.",
    )
    parser.add_argument(
        "--version",
        default=NANOKA_DEFAULT_VERSION,
        help=f"nanoka data version directory (default: {NANOKA_DEFAULT_VERSION}).",
    )
    parser.add_argument(
        "--locale",
        default=NANOKA_DEFAULT_LOCALE,
        help=f"Locale folder under the version path (default: {NANOKA_DEFAULT_LOCALE}).",
    )
    parser.add_argument(
        "--levels",
        default=",".join(str(level) for level in DEFAULT_LEVELS),
        help="Comma-separated levels to sync (default: 1,20,30,40,50,60,70,80).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compare and print diffs without writing my_roles_model.json.",
    )
    parser.add_argument(
        "--show-diffs",
        action="store_true",
        help="Print per-stat diffs for updated roles.",
    )
    args = parser.parse_args()

    try:
        levels = tuple(int(part.strip()) for part in str(args.levels).split(",") if part.strip())
    except ValueError:
        build_cli.fail(f"Invalid --levels value: {args.levels}")
        return 2
    if not levels:
        build_cli.fail("--levels must contain at least one level.")
        return 2

    try:
        summary = sync_nanoka_base_stats(
            Path(args.config_dir),
            version=str(args.version),
            locale=str(args.locale),
            levels=levels,
            dry_run=bool(args.dry_run),
        )
    except Exception as exc:
        build_cli.fail(f"nanoka base-stat sync failed: {exc}")
        return 1

    action = "dry-run" if summary.get("dry_run") else ("wrote" if summary.get("wrote") else "no-write")
    build_cli.ok(
        f"nanoka base stats synced ({action}): "
        f"version={summary.get('version')}, "
        f"matched={summary.get('matched_count', 0)}, "
        f"updated={summary.get('updated_count', 0)}, "
        f"unchanged={summary.get('unchanged_count', 0)}, "
        f"skipped={summary.get('skipped_count', 0)}"
    )

    skipped = summary.get("skipped_roles") or []
    if skipped:
        build_cli.warn("Skipped roles without nanoka id/name match: " + ", ".join(map(str, skipped)))

    fetch_errors = summary.get("fetch_errors") or []
    if fetch_errors:
        build_cli.warn("Fetch errors:")
        for item in fetch_errors:
            build_cli.warn(f"  - {item}")

    if args.show_diffs or args.dry_run:
        diffs = summary.get("diffs") or {}
        for role_name in summary.get("updated_roles") or []:
            role_diffs = diffs.get(role_name) or []
            if not role_diffs:
                build_cli.info(f"{role_name}: sub_stats refreshed from current level")
                continue
            build_cli.info(f"{role_name}:")
            for item in role_diffs:
                build_cli.info(
                    f"  Lv{item['level']} {item['stat']}: "
                    f"{item['local']} -> {item['remote']}"
                )

    if fetch_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
