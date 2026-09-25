# 从同一候选主静态生成战报目录读模型并存回该候选，不使用独立发行目录。
from __future__ import annotations

import hashlib
import json
import lzma
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

from tools.native_analysis.export_target_catalog import export_axis_catalog, export_catalog

ROOT = Path(__file__).resolve().parents[2]
STATIC_RULE_KEYS = ("effect_rules", "skill_bound_rules", "source_attack_rules")


def populate_analysis_catalogs(database: Path) -> int:
    database = database.resolve()
    with tempfile.TemporaryDirectory(prefix="analysis-catalog-", dir=database.parent) as temporary:
        subprocess.run([
            sys.executable, str(ROOT / "tools/game_data/export_native_analysis_declarations.py"),
            "--static", str(database), "--output", temporary,
        ], check=True, cwd=ROOT)
        declarations = json.loads((Path(temporary) / "public_static_declarations.json").read_text(encoding="utf-8"))
    target, axis = export_catalog(database), export_axis_catalog(database)
    for document in (target, axis):
        for key in ("source_sha256", "dataset_metadata", "dataset_id", "format_version"):
            document.pop(key, None)
    payload = {
        "target": target, "axis": axis,
        "rules": {key: declarations[key] for key in STATIC_RULE_KEYS},
    }
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    compressed = lzma.compress(raw, preset=9)
    connection = sqlite3.connect(database)
    try:
        with connection:
            connection.execute("DELETE FROM battle_analysis_catalog")
            connection.execute("INSERT INTO battle_analysis_catalog VALUES(1,1,'xz-json',?,?,?)",
                               (len(raw), hashlib.sha256(raw).hexdigest(), compressed))
    finally:
        connection.close()
    return 1


def validate_catalog_inputs(database: Path) -> None:
    """Verify the catalog stored in the selected main static database."""
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT format_version,codec,decoded_bytes,decoded_sha256,payload "
            "FROM battle_analysis_catalog WHERE singleton=1"
        ).fetchone()
    finally:
        connection.close()
    if row is None or row[0] != 1 or row[1] != "xz-json" or not 0 < row[2] <= 64 * 1024 * 1024:
        raise ValueError("主静态战报目录缺失或格式无效")
    decoder = lzma.LZMADecompressor(memlimit=256 * 1024 * 1024)
    raw = decoder.decompress(row[4], max_length=64 * 1024 * 1024 + 1)
    if not decoder.eof or decoder.unused_data or len(raw) != row[2] or hashlib.sha256(raw).hexdigest() != row[3]:
        raise ValueError("主静态战报目录完整性无效")
    value = json.loads(raw)
    if not all(isinstance(value.get(key), dict) for key in ("target", "axis", "rules")):
        raise ValueError("主静态战报目录缺少必需内容")
