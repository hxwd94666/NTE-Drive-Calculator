# 用已审查的冻结效果构造最小只读测试来源；不重签效果证据、不依赖当前发行版本。
from collections import defaultdict
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3


def build_review_source(directory):
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads((root / "tests/fixtures/equipment_suit_review_evidence.json").read_text(encoding="utf-8"))
    policy = json.loads((root / "tools/game_data/equipment_suit_permanent_reviews.json").read_text(encoding="utf-8"))
    tables = defaultdict(list)
    for evidence in fixture["effects"]:
        effect = deepcopy(evidence["effect"])
        tables["equipment_suit"].append({"suit_id": effect["suit_id"], "name_zh": effect.pop("name_zh")})
        tables["equipment_suit_effect"].append(effect)
        tables["combat_effect_definition"].append(evidence["definition"])
        for key, table in [
            ("pack", "equipment_modify_pack"),
            ("values", "equipment_modify_value"),
            ("links", "combat_effect_buff_link"),
            ("buffs", "buff_definition"),
            ("modifiers", "buff_modifier"),
            ("triggers", "buff_trigger_effect"),
            ("source_rows", "source_row"),
        ]:
            tables[table].extend(evidence[key])
        for curve in evidence["curves"].values():
            tables["equipment_buff_curve"].extend(curve["definition"])
            tables["equipment_buff_curve_point"].extend(curve["points"])
    database = directory / "source.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        for table, rows in tables.items():
            columns = tuple(dict.fromkeys(key for row in rows for key in row))
            assert columns, table
            connection.execute(f'CREATE TABLE "{table}" (' + ",".join(f'"{key}"' for key in columns) + ")")
            unique = {json.dumps(row, sort_keys=True, ensure_ascii=False): row for row in rows}
            connection.executemany(
                f'INSERT INTO "{table}" VALUES (' + ",".join("?" for _ in columns) + ")",
                [tuple(row.get(key) for key in columns) for row in unique.values()],
            )
        connection.executescript(
            "CREATE TABLE dataset(dataset_id); INSERT INTO dataset VALUES ('synthetic-review-evidence');"
            "CREATE TABLE dataset_scope(scope); INSERT INTO dataset_scope VALUES ('game');"
            "CREATE TABLE schema_migration(version); INSERT INTO schema_migration VALUES (43);"
        )
    # 只为合成来源绑定文件身份；每条效果的审查摘要仍是原先独立审核的固定金样。
    policy["source_dataset_id"] = "synthetic-review-evidence"
    policy["source_static_schema"] = 43
    policy["source_database_sha256"] = hashlib.sha256(database.read_bytes()).hexdigest()
    reviews = directory / "reviews.json"
    reviews.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
    return database, reviews, policy
