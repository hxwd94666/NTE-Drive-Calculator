# 通过只读 DAO 查看 schema v11 静态游戏数据库。
"""通过只读 DAO 查看 schema v11 静态游戏数据库。"""

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
    if view == "topple-curve":
        return dao.get_combat_level_curve("topple:character_level")
    if view == "reaction-curve":
        if item_id is None:
            raise SystemExit("查看环合曲线时必须传入 --id <effect_id>")
        return dao.get_reaction_damage_curve(item_id)
    if view == "reactions":
        return dao.list_reaction_definitions()
    if view == "combat-constants":
        return dao.list_combat_effect_constants()
    if view == "skill-damage":
        if item_id is None:
            raise SystemExit("查看技能伤害时必须传入 --id <effect_id>")
        return dao.get_skill_damage(item_id)
    if view == "enemy-profile":
        if item_id is None or ":" not in item_id:
            raise SystemExit(
                "查看敌方属性包时必须传入 --id <standard|night_999>:<pack_id>"
            )
        profile_set, pack_id = item_id.split(":", 1)
        return dao.get_enemy_combat_profile(profile_set, pack_id)
    raise AssertionError(f"unhandled view: {view}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    parser.add_argument(
        "view",
        nargs="?",
        choices=(
            "summary",
            "characters",
            "shapes",
            "suits",
            "equipment",
            "forks",
            "plan",
            "topple-curve",
            "reaction-curve",
            "reactions",
            "combat-constants",
            "skill-damage",
            "enemy-profile",
        ),
        default="summary",
    )
    parser.add_argument(
        "--id",
        help="按查询类型填写对象 ID；敌方属性包使用 profile_set:pack_id",
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
