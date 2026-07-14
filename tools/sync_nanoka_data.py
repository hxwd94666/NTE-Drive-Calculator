# 从 nanoka.cc 静态 JSON 同步角色/武器基础属性。
"""Sync character/weapon base stats from nanoka static data.

Not a scraper: detects the live dataset version from nte.nanoka.cc, then reads
versioned JSON from https://static.nanoka.cc/nte/{version}/...
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import build_cli
from src.features.settings.nanoka_base_stats import sync_nanoka_base_stats
from src.features.settings.nanoka_client import (
    DEFAULT_LEVELS,
    NANOKA_DEFAULT_LOCALE,
    resolve_version,
)
from src.features.settings.nanoka_weapon_stats import sync_nanoka_weapon_stats


def _parse_levels(raw: str) -> tuple[int, ...]:
    levels = tuple(int(part.strip()) for part in str(raw).split(",") if part.strip())
    if not levels:
        raise ValueError("levels must contain at least one level")
    return levels


def _print_stat_diffs(title: str, names: list[str], diffs: dict) -> None:
    for name in names:
        items = diffs.get(name) or []
        if not items:
            build_cli.info(f"{title} {name}: sub_stats refreshed from current level")
            continue
        build_cli.info(f"{title} {name}:")
        for item in items:
            build_cli.info(
                f"  Lv{item['level']} {item['stat']}: "
                f"{item['local']} -> {item['remote']}"
            )


def _report_sync_summary(
    *,
    kind: str,
    summary: dict[str, Any],
    missing_key: str,
    added_key: str,
    skipped_key: str,
    updated_key: str,
    add_missing: bool,
    show_diffs: bool,
) -> int:
    action = "dry-run" if summary.get("dry_run") else ("wrote" if summary.get("wrote") else "no-write")
    build_cli.ok(
        f"{kind} ({action}): version={summary.get('version')}, "
        f"matched={summary.get('matched_count', 0)}, "
        f"updated={summary.get('updated_count', 0)}, "
        f"unchanged={summary.get('unchanged_count', 0)}, "
        f"added={summary.get('added_count', 0)}, "
        f"missing_remote={summary.get('missing_remote_count', 0)}"
    )

    missing = summary.get(missing_key) or []
    if missing and not add_missing:
        build_cli.warn(
            f"Remote {kind} not in local config (pass --add-missing to create stubs): "
            + ", ".join(map(str, missing))
        )
    added = summary.get(added_key) or []
    if added:
        build_cli.info(f"Added {kind}: " + ", ".join(map(str, added)))
    skipped = summary.get(skipped_key) or []
    if skipped:
        build_cli.warn(f"Skipped local {kind} without nanoka match: " + ", ".join(map(str, skipped)))

    exit_code = 0
    fetch_errors = summary.get("fetch_errors") or []
    if fetch_errors:
        exit_code = 1
        build_cli.warn(f"{kind.capitalize()} fetch errors:")
        for item in fetch_errors:
            build_cli.warn(f"  - {item}")

    if show_diffs or summary.get("dry_run"):
        _print_stat_diffs(kind.rstrip("s"), summary.get(updated_key) or [], summary.get("diffs") or {})
    return exit_code


def _run_sync(
    *,
    kind: str,
    sync_fn: Callable[..., dict[str, Any]],
    config_dir: Path,
    version: str,
    locale: str,
    levels: tuple[int, ...],
    dry_run: bool,
    add_missing: bool,
    show_diffs: bool,
    missing_key: str,
    added_key: str,
    skipped_key: str,
    updated_key: str,
) -> int:
    try:
        summary = sync_fn(
            config_dir,
            version=version,
            locale=locale,
            levels=levels,
            dry_run=dry_run,
            add_missing=add_missing,
        )
    except Exception as exc:
        build_cli.fail(f"nanoka {kind} sync failed: {exc}")
        return 1
    return _report_sync_summary(
        kind=kind,
        summary=summary,
        missing_key=missing_key,
        added_key=added_key,
        skipped_key=skipped_key,
        updated_key=updated_key,
        add_missing=add_missing,
        show_diffs=show_diffs,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sync character/weapon base stats from nanoka.cc into "
            "config/my_roles_model.json and config/weapons.json."
        )
    )
    parser.add_argument(
        "--config-dir",
        default=str(ROOT / "config"),
        help="Config directory containing my_roles_model.json / roles.json / weapons.json.",
    )
    parser.add_argument(
        "--version",
        default="latest",
        help="nanoka data version, or 'latest' to auto-detect the live site version (default: latest).",
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
        "--characters-only",
        action="store_true",
        help="Only sync character base stats.",
    )
    parser.add_argument(
        "--weapons-only",
        action="store_true",
        help="Only sync weapon base stats.",
    )
    parser.add_argument(
        "--add-missing",
        action="store_true",
        help="Add characters/weapons that exist on nanoka but are missing locally.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compare and print diffs without writing config files.",
    )
    parser.add_argument(
        "--show-diffs",
        action="store_true",
        help="Print per-stat diffs for updated entries.",
    )
    args = parser.parse_args()

    if args.characters_only and args.weapons_only:
        build_cli.fail("Use only one of --characters-only / --weapons-only.")
        return 2

    try:
        levels = _parse_levels(args.levels)
    except ValueError as exc:
        build_cli.fail(f"Invalid --levels value: {args.levels} ({exc})")
        return 2

    try:
        resolved_version = resolve_version(str(args.version))
    except Exception as exc:
        build_cli.fail(f"nanoka version resolve failed: {exc}")
        return 1
    if str(args.version).strip().lower() in {"", "latest", "auto", "current", "live"}:
        build_cli.info(f"Detected nanoka live version: {resolved_version}")

    exit_code = 0
    common = dict(
        config_dir=Path(args.config_dir),
        version=resolved_version,
        locale=str(args.locale),
        levels=levels,
        dry_run=bool(args.dry_run),
        add_missing=bool(args.add_missing),
        show_diffs=bool(args.show_diffs),
    )

    if not args.weapons_only:
        code = _run_sync(
            kind="characters",
            sync_fn=sync_nanoka_base_stats,
            missing_key="missing_remote_roles",
            added_key="added_roles",
            skipped_key="skipped_roles",
            updated_key="updated_roles",
            **common,
        )
        exit_code = max(exit_code, code)

    if not args.characters_only:
        code = _run_sync(
            kind="weapons",
            sync_fn=sync_nanoka_weapon_stats,
            missing_key="missing_remote_weapons",
            added_key="added_weapons",
            skipped_key="skipped_weapons",
            updated_key="updated_weapons",
            **common,
        )
        exit_code = max(exit_code, code)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
