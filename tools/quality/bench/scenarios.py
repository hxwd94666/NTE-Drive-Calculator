# 定义四项端到端基准场景：目录重建、静态库开连、评分与快照写入。
"""End-to-end benchmark scenarios used to justify performance changes."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from src.models.equipment import Drive, Tape
from src.optimizer.scoring import ScoringEngine
from src.storage.sqlite.user_data_dao import UserDataDao
from src.ui.main_window_data_mixin import MainWindowDataMixin

ITEMS = 600
TAPES = 100
ROLES = 8
_SUB_STATS = ("暴击率", "暴击伤害", "攻击力%", "攻击力", "生命值%", "防御力%")
_SHAPES = ("S1", "S2", "S3", "S4", "S5", "S6")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


class _AccountDatabase:
    """创建并持有一个一次性账号库，供多个场景复用同一输入。"""

    def __init__(self) -> None:
        self._temporary = tempfile.mkdtemp(prefix="nte-bench-")
        self.path = Path(self._temporary) / "user_data.sqlite3"
        UserDataDao(self.path, account_id="bench", account_name="bench").close()

    def close(self) -> None:
        shutil.rmtree(self._temporary, ignore_errors=True)


def _roles_db() -> dict[str, Any]:
    return {
        f"角色{index}": {
            "character_id": 1000 + index,
            "weights": {
                "暴击率": 1.0, "暴击伤害": 0.9, "攻击力%": 0.8, "攻击力": 0.4,
                "生命值%": 0.2, "防御力%": 0.1, "生命值": 0.05, "防御力": 0.05,
            },
            "main_weights": {"攻击力": 1.0, "生命值": 0.5},
        }
        for index in range(ROLES)
    }


def _inventory() -> list[Drive | Tape]:
    items: list[Drive | Tape] = []
    for index in range(ITEMS):
        items.append(Drive(
            uid=f"d-{index}", item_type="drive", quality="Gold",
            area=(index % 4) + 1, shape_id=_SHAPES[index % len(_SHAPES)],
            set_name=f"套装{index % 6}",
            main_stats={"攻击力": 100.0 + index, "生命值": 200.0 + index},
            sub_stats={name: 3.0 + (index % 5) for name in _SUB_STATS[:4]},
        ))
    for index in range(TAPES):
        items.append(Tape(
            uid=f"t-{index}", item_type="tape", quality="Gold", area=15,
            set_name=f"套装{index % 6}", main_stats="暴击率",
            sub_stats={name: 2.0 + (index % 3) for name in _SUB_STATS[:3]},
        ))
    return items


def _snapshot(generation: int, items: list[dict]) -> dict:
    return {
        "method": "event.inventory.snapshot",
        "params": {
            "complete": True,
            "generation": generation,
            "sequence": generation,
            "observed_at_unix_ms": 1_800_000_000_000 + generation,
            "item_count": len(items),
            "items": items,
        },
    }


def _snapshot_items() -> list[dict]:
    return [
        {
            "uid": {"serial": index + 1, "slot": (index % 7) + 1},
            "kind": "module" if index % 2 == 0 else "core",
            "item_id": "cell3_style1_1_Orange" if index % 2 == 0 else "Nature_orange",
            "suit_id": "Suit1",
            "geometry": "ZhiJiao1" if index % 2 == 0 else "Core",
            "grid": 3 if index % 2 == 0 else None,
            "quality": "orange",
            "level": 20,
            "max_level": 20,
            "locked": False,
            "equipped": False,
            "names": {"zh_cn": "基准装备"},
            "suit_names": {"zh_cn": "基准套装"},
            "main_stats": [
                {"property_id": "attack_percent", "value": 12.5, "percent": True},
            ],
            "sub_stats": [
                {"property_id": f"sub_{offset}", "value": 3.0 + offset,
                 "percent": offset % 2 == 0}
                for offset in range(4)
            ],
        }
        for index in range(ITEMS)
    ]


def build_scenarios() -> dict[str, Any]:
    """Return the benchmark scenarios keyed by a stable label."""

    root = _repo_root()
    database = _AccountDatabase()
    engine = ScoringEngine(roles_db=_roles_db())
    inventory = _inventory()
    snapshot_items = _snapshot_items()
    counter = {"generation": 0}

    def catalog_rebuild() -> None:
        MainWindowDataMixin._read_allocation_catalog(
            root / "config",
            database.path,
            root / "data" / "game_static.sqlite3",
            root / "data" / "role_catalog" / "game_ui",
        )

    def static_dao_open() -> None:
        from src.storage.sqlite.static_game_data_dao import StaticGameDataDao

        with StaticGameDataDao(root / "data" / "game_static.sqlite3") as dao:
            dao.list_equipment_attributes()

    def scoring_pass() -> None:
        engine.evaluate_global_inventory(list(inventory))

    def snapshot_write() -> None:
        counter["generation"] += 1
        with UserDataDao(database.path) as dao:
            dao.import_inventory_snapshot(
                _snapshot(counter["generation"], snapshot_items),
                protocol_version=1,
            )

    def cleanup() -> None:
        database.close()

    return {
        "catalog_rebuild": catalog_rebuild,
        "static_dao_open": static_dao_open,
        "scoring_pass": scoring_pass,
        "snapshot_write": snapshot_write,
        "cleanup": cleanup,
    }
