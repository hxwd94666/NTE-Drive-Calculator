# 从游戏 locres 导出生成 locales/gametext.<lang>.json，供界面展示长文本。
"""Build the game-text display catalogue from a locres export.

The static database stores every long-form game string in Chinese and, for most
of them, the string-table key the game itself uses. This tool joins those keys
against a locres export and writes one flat ``Namespace::Key -> text`` file.

Only the generated JSON is committed; the locres export stays outside the repo.

Usage::

    python tools/game_data/build_game_text_locale.py --language en \\
        --locres path/to/game_pakchunk0-Windows.csv \\
        --locres path/to/game_pakchunk0-Windows_0_P.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STATIC_DATABASE = ROOT / "data" / "game_static.sqlite3"
LOCALES_DIR = ROOT / "locales"

# (table, text-table column, key column, Chinese column)
KEYED_SOURCES = (
    ("equipment_suit_effect", "description_text_table", "description_text_key", "description_zh"),
    ("character_awaken_effect", "title_text_table", "title_text_key", "title_zh"),
    ("character_awaken_effect", "description_text_table", "description_text_key", "description_zh"),
)
# Tables whose Chinese text has no key column but follows a naming convention.
DERIVED_SOURCES = (
    ("fork_item", "fork_id", "ST_Fork", ("_des", "_context")),
)
# fork_star_level has neither a key column nor a matching id: its pack id is
# ``upgradestar_pack_<fork_id>`` while the string table uses ``buff_<fork_id>``.
STAR_PACK_PREFIX = "upgradestar_pack_"
STAR_SUFFIXES = ("_effect", "_name")

def _namespace(text_table: object) -> str:
    """``/Game/Text/ST_Fork.ST_Fork`` -> ``ST_Fork``."""
    return str(text_table or "").rsplit(".", 1)[-1]


def load_locres(paths: list[Path]) -> dict[str, str]:
    """Merge locres exports; later files win so patch chunks override the base."""
    merged: dict[str, str] = {}
    for path in paths:
        with io.open(path, encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                text = (row.get("source") or "").strip()
                if text:
                    merged[row["key"]] = row["source"]
    return merged


def collect(connection: sqlite3.Connection, locres: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    entries: dict[str, str] = {}
    missing: list[str] = []

    for table, table_column, key_column, _zh_column in KEYED_SOURCES:
        query = f'select {table_column}, {key_column} from "{table}" where {key_column} is not null'
        for text_table, key in connection.execute(query):
            full_key = f"{_namespace(text_table)}::{key}"
            if full_key in locres:
                entries[full_key] = locres[full_key]
            else:
                missing.append(full_key)

    for table, id_column, namespace, suffixes in DERIVED_SOURCES:
        for (identifier,) in connection.execute(f'select {id_column} from "{table}"'):
            for suffix in suffixes:
                full_key = f"{namespace}::{identifier}{suffix}"
                if full_key in locres:
                    entries[full_key] = locres[full_key]

    lowered = {key.lower(): value for key, value in locres.items()}
    packs = connection.execute(
        "select distinct star_pack_id from fork_star_level where star_pack_id is not null"
    )
    for (pack_id,) in packs:
        fork_id = str(pack_id).replace(STAR_PACK_PREFIX, "")
        for suffix in STAR_SUFFIXES:
            full_key = f"ST_Fork::buff_{fork_id}{suffix}"
            text = lowered.get(full_key.lower())
            if text:
                entries[full_key] = text

    # Keys are stored lower-cased; the runtime lower-cases its lookup too, so
    # casing differences between the database and the string table cannot miss.
    return {key.lower(): value for key, value in entries.items()}, missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default="en")
    parser.add_argument("--locres", action="append", required=True, type=Path)
    parser.add_argument("--database", default=STATIC_DATABASE, type=Path)
    arguments = parser.parse_args()

    locres = load_locres(arguments.locres)
    with sqlite3.connect(arguments.database) as connection:
        dataset_id = connection.execute("select dataset_id from dataset").fetchone()[0]
        entries, missing = collect(connection, locres)

    payload = {
        "_meta": {
            "purpose": "Game long-form text keyed by the string table the static database references.",
            "dataset_id": dataset_id,
            "entry_count": len(entries),
        },
        "entries": dict(sorted(entries.items())),
    }
    LOCALES_DIR.mkdir(parents=True, exist_ok=True)
    output = LOCALES_DIR / f"gametext.{arguments.language}.json"
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {output.relative_to(ROOT).as_posix()}: {len(entries)} entries")
    if missing:
        print(f"unresolved keys: {len(missing)}")
        for key in missing[:10]:
            print(f"  {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
