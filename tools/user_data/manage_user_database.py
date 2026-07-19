# 创建、导入并查看分账号的用户 SQLite 数据库。
"""创建并查看分账号的 NTE 用户 SQLite 数据库。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.sqlite.user_data_dao import UserDataDao


def _print_json(value: Any) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="创建空的账号数据库")
    init_parser.add_argument("--account-id", required=True)
    init_parser.add_argument("--account-name")

    subparsers.add_parser("summary", help="显示数据库元信息和数量")
    subparsers.add_parser("check", help="执行 SQLite 完整性和外键检查")
    subparsers.add_parser("snapshots", help="列出已导入的背包快照")

    import_parser = subparsers.add_parser("import-snapshot", help="导入 nte-core 原始快照")
    import_parser.add_argument("snapshot", type=Path)
    import_parser.add_argument("--protocol-version", type=int, default=1)

    inventory_parser = subparsers.add_parser("inventory", help="列出当前背包装备")
    inventory_parser.add_argument("--kind", choices=("module", "core"))
    inventory_parser.add_argument("--equipped", choices=("yes", "no"))
    inventory_parser.add_argument("--character-id", type=int)
    inventory_parser.add_argument("--limit", type=int, default=20)

    subparsers.add_parser("settings", help="显示同步和装配设置")
    set_parser = subparsers.add_parser("set-settings", help="更新同步和装配设置")
    set_parser.add_argument("--inventory-method", choices=("nte_core", "gamepad"))
    set_parser.add_argument("--apply-method", choices=("nte_core", "gamepad"))
    set_parser.add_argument("--capture-device")
    set_parser.add_argument("--raw-capture", choices=("enabled", "disabled"))
    set_parser.add_argument("--settle-seconds", type=float)
    set_parser.add_argument("--auto-start", choices=("yes", "no"))

    plans_parser = subparsers.add_parser("plans", help="列出已保存的装配方案")
    plans_parser.add_argument("--character-id", type=int)
    prune_parser = subparsers.add_parser(
        "prune-snapshots",
        help="安全清理未被当前快照或装配方案引用的历史快照",
    )
    prune_parser.add_argument(
        "--retain-recent",
        type=int,
        help="至少保留最近的稳定快照数量；省略时读取数据库设置",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "init":
        with UserDataDao(
            args.database,
            account_id=args.account_id,
            account_name=args.account_name,
        ) as dao:
            _print_json(dao.summary())
        return 0

    with UserDataDao(args.database) as dao:
        if args.command == "summary":
            result = dao.summary()
        elif args.command == "check":
            result = dao.integrity_check()
        elif args.command == "snapshots":
            result = dao.list_inventory_snapshots()
        elif args.command == "import-snapshot":
            raw = json.loads(args.snapshot.read_text(encoding="utf-8"))
            snapshot_id = dao.import_inventory_snapshot(
                raw, source="nte_core", protocol_version=args.protocol_version
            )
            result = {
                "imported_snapshot_id": snapshot_id,
                "inventory": dao.current_inventory_summary(),
                "retention": dao.prune_inventory_snapshots(),
            }
        elif args.command == "inventory":
            equipped = None if args.equipped is None else args.equipped == "yes"
            result = dao.list_current_inventory_items(
                kind=args.kind,
                equipped=equipped,
                character_id=args.character_id,
                limit=args.limit,
            )
        elif args.command == "settings":
            result = dao.get_sync_settings()
        elif args.command == "set-settings":
            raw_capture = None
            if args.raw_capture is not None:
                raw_capture = args.raw_capture == "enabled"
            result = dao.update_sync_settings(
                inventory_sync_method=args.inventory_method,
                equipment_apply_method=args.apply_method,
                capture_device_id=args.capture_device,
                raw_capture_enabled=raw_capture,
                inventory_settle_seconds=args.settle_seconds,
                auto_start_inventory_sync=(args.auto_start == "yes")
                if args.auto_start is not None else None,
            )
        elif args.command == "plans":
            result = dao.list_loadout_plans(args.character_id)
        elif args.command == "prune-snapshots":
            result = dao.prune_inventory_snapshots(
                retain_recent=args.retain_recent
            )
        else:
            raise AssertionError(args.command)
    _print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
