# nanoka 同步测试共用的角色、武器和配置夹具。
from __future__ import annotations

import json
from pathlib import Path


def fake_character(
    *,
    character_id: str = "1004",
    element: str = "Chaos",
    hp: list[int] | None = None,
    atk: list[int] | None = None,
    defense: list[int] | None = None,
) -> dict:
    hp = hp if hp is not None else [1000 + i for i in range(80)]
    atk = atk if atk is not None else [50 + i for i in range(80)]
    defense = defense if defense is not None else [60 + i for i in range(80)]
    return {
        "id": character_id,
        "name": "Lacrimosa",
        "desc": "desc",
        "element": element,
        "equip_slots": {
            "slots": [
                [-1] * 7,
                [-1, 0, 0, 0, 0, 0, -1],
                [-1, 0, 0, 0, 0, 0, -1],
                [-1, 0, 0, -1, 0, 0, -1],
                [-1, 0, 0, -1, -1, 0, -1],
                [-1, 0, 0, 0, 0, -1, -1],
                [-1] * 7,
            ]
        },
        "stats": [
            {"id_stats": "HPMaxBase", "values": hp},
            {"id_stats": "AtkBase", "values": atk},
            {"id_stats": "DefBase", "values": defense},
            {"id_stats": "CritBase", "values": [5] * 80},
            {"id_stats": "CritDamageBase", "values": [50] * 80},
        ],
    }


def fake_weapon(*, weapon_id: str = "fork_LunarPhase") -> dict:
    return {
        "id": weapon_id,
        "name": "穿过胭红蜃景",
        "type_name": "聚合",
        "description": "desc",
        "stats": [
            {"id_stats": "AtkBase", "values": [37 + i for i in range(80)]},
            {"id_stats": "CritBase", "values": [9.6] * 80},
        ],
    }


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
