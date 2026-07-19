# 通过只读 DAO 查看 schema v2 静态游戏数据库。
"""通过只读 DAO 查看 schema v2 静态游戏数据库。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


DATABASE_ENV = "NTE_GAME_STATIC_DB"


def resolve_database(argument: Path | None) -> Path:
    if argument is not None:
        return argument
    configured = os.environ.get(DATABASE_ENV)
    if configured:
        return Path(configured)
    raise SystemExit(
        f"必须指定数据库路径：请传入 --database 或设置 {DATABASE_ENV}"
    )


def select_view(dao: StaticGameDataDao, view: str, item_id: str | None) -> Any:
    if view == "summary":
        return dao.summary()
    if view == "characters":
        return dao.list_characters()
    if view == "shapes":
        return dao.list_shapes()
    if view == "suits":
        return dao.get_suit(item_id) if item_id else dao.list_suits()
    if view == "equipment":
        return dao.list_equipment_items(item_id)
    if view == "forks":
        return dao.list_forks()
    if view == "plan":
        if item_id is None:
            raise SystemExit("查看装配方案时必须传入 --id <character_id>")
        return dao.get_equipment_plan(int(item_id))
    raise AssertionError(f"unhandled view: {view}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    parser.add_argument(
        "view",
        nargs="?",
        choices=("summary", "characters", "shapes", "suits", "equipment", "forks", "plan"),
        default="summary",
    )
    parser.add_argument(
        "--id",
        help="按查询类型填写空幕 ID、角色 ID 或装备类型（module/core）",
    )
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    with StaticGameDataDao(resolve_database(args.database)) as dao:
        result = select_view(dao, args.view, args.id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
