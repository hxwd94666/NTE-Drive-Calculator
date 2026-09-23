# 为只读静态库生成物理压缩候选，并逐表证明业务数据未变化。
"""VACUUM a published static release without changing its logical dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path


PROVENANCE_FILENAME = "storage_repack_provenance.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _schema(connection: sqlite3.Connection) -> list[tuple[str, str, str, str | None]]:
    return connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "ORDER BY type, name, tbl_name"
    ).fetchall()


def _table_fingerprint(connection: sqlite3.Connection, table: str) -> tuple[int, str]:
    """Hash a multiset of complete rows, independent of VACUUM rowid changes."""
    quoted = '"' + table.replace('"', '""') + '"'
    rows = (
        hashlib.sha256(repr(row).encode("utf-8")).digest()
        for row in connection.execute(f"SELECT * FROM {quoted}")
    )
    fingerprints = sorted(rows)
    digest = hashlib.sha256()
    for fingerprint in fingerprints:
        digest.update(fingerprint)
    return len(fingerprints), digest.hexdigest().upper()


def validate_logical_equivalence(baseline: Path, candidate: Path) -> dict[str, int]:
    """Reject changed schema, row values/counts, identities or corrupt databases."""
    with closing(_readonly(baseline)) as old, closing(_readonly(candidate)) as new:
        if _schema(old) != _schema(new):
            raise ValueError("物理压缩候选的 schema 或索引发生变化")
        for pragma in ("application_id", "user_version", "encoding"):
            if old.execute(f"PRAGMA {pragma}").fetchone() != new.execute(
                f"PRAGMA {pragma}"
            ).fetchone():
                raise ValueError(f"物理压缩候选的 {pragma} 发生变化")
        for label, connection in (("原库", old), ("候选", new)):
            if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise ValueError(f"{label} quick_check 失败")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError(f"{label} 外键校验失败")
        counts: dict[str, int] = {}
        tables = [name for kind, name, _, _ in _schema(old) if kind == "table"]
        for table in tables:
            old_result = _table_fingerprint(old, table)
            new_result = _table_fingerprint(new, table)
            if old_result != new_result:
                raise ValueError(f"物理压缩候选表内容变化：{table}")
            counts[table] = old_result[0]
        return counts


def validate_repack_provenance(
    candidate: Path,
    provenance: Path,
    baseline: Path,
    baseline_manifest: Path,
) -> dict[str, object]:
    """Bind the candidate to one already-verified published release."""
    data = json.loads(provenance.read_text(encoding="utf-8"))
    manifest = json.loads(baseline_manifest.read_text(encoding="utf-8"))
    expected = {
        "format_version": 1,
        "kind": "published_static_storage_repack",
        "baseline_database_sha256": _sha256(baseline),
        "baseline_manifest_sha256": _sha256(baseline_manifest),
        "candidate_database_sha256": _sha256(candidate),
    }
    if not isinstance(data, dict) or any(data.get(key) != value for key, value in expected.items()):
        raise ValueError("物理压缩候选来源哈希与当前文件不匹配")
    metadata = manifest.get("database", {})
    if metadata.get("sha256", "").upper() != expected["baseline_database_sha256"]:
        raise ValueError("原库与已发行清单哈希不匹配")
    if metadata.get("size_bytes") != baseline.stat().st_size:
        raise ValueError("原库与已发行清单大小不匹配")
    if candidate.stat().st_size >= baseline.stat().st_size:
        raise ValueError("物理压缩候选未减小体积")
    original_assets = baseline.parent / "game_ui"
    new_assets = candidate.parent / "game_ui"
    if original_assets.is_dir() != new_assets.is_dir():
        raise ValueError("物理压缩候选角色图片目录变化")
    if original_assets.is_dir():
        old_files = {
            file.relative_to(original_assets)
            for file in original_assets.rglob("*") if file.is_file()
        }
        new_files = {
            file.relative_to(new_assets)
            for file in new_assets.rglob("*") if file.is_file()
        }
        if old_files != new_files:
            raise ValueError("物理压缩候选角色图片文件集合变化")
        for relative in old_files - {Path("manifest.json")}:
            if _sha256(original_assets / relative) != _sha256(new_assets / relative):
                raise ValueError(f"物理压缩候选角色图片内容变化：{relative}")
        old_assets_manifest = json.loads(
            (original_assets / "manifest.json").read_text(encoding="utf-8")
        )
        new_assets_manifest = json.loads(
            (new_assets / "manifest.json").read_text(encoding="utf-8")
        )
        old_assets_manifest["database_sha256"] = expected["candidate_database_sha256"]
        if old_assets_manifest != new_assets_manifest:
            raise ValueError("物理压缩候选角色图片清单变化")
    counts = validate_logical_equivalence(baseline, candidate)
    return {**expected, "table_count": len(counts)}


def create_repack_candidate(baseline_dir: Path, candidate_dir: Path) -> Path:
    """Build in a fresh directory; never write to the published release."""
    source = baseline_dir.resolve(strict=True)
    candidate = candidate_dir.resolve()
    if candidate.exists() or candidate == source or source in candidate.parents:
        raise ValueError("候选目录必须是独立且不存在的新目录")
    database = source / "game_static.sqlite3"
    manifest = source / "manifest.json"
    if not database.is_file() or not manifest.is_file():
        raise ValueError("原库或已发行清单缺失")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))["database"]
    if metadata["sha256"].upper() != _sha256(database):
        raise ValueError("原库与已发行清单哈希不匹配")
    candidate.mkdir(parents=True)
    output = candidate / database.name
    try:
        with closing(_readonly(database)) as connection:
            connection.execute("VACUUM INTO ?", (str(output),))
        # SQLite's VACUUM INTO can retain a different packing from an in-place
        # VACUUM. The second pass is isolated to the candidate and removes it.
        with closing(sqlite3.connect(output)) as connection:
            connection.execute("VACUUM")
        if (source / "game_ui" / "manifest.json").is_file():
            shutil.copytree(source / "game_ui", candidate / "game_ui")
            assets_manifest = candidate / "game_ui" / "manifest.json"
            assets = json.loads(assets_manifest.read_text(encoding="utf-8"))
            if assets.get("database_sha256") != _sha256(database):
                raise ValueError("原角色图片清单与原库不匹配")
            assets["database_sha256"] = _sha256(output)
            assets_manifest.write_text(
                json.dumps(assets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        provenance = {
            "format_version": 1,
            "kind": "published_static_storage_repack",
            "baseline_database_sha256": _sha256(database),
            "baseline_manifest_sha256": _sha256(manifest),
            "candidate_database_sha256": _sha256(output),
        }
        (candidate / PROVENANCE_FILENAME).write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        validate_repack_provenance(output, candidate / PROVENANCE_FILENAME, database, manifest)
        return output
    except BaseException:
        # A failed candidate is retained for inspection; it is never promoted.
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="从已发行静态库生成无语义变化的压缩候选")
    parser.add_argument("--baseline-dir", required=True, type=Path)
    parser.add_argument("--candidate-dir", required=True, type=Path)
    args = parser.parse_args()
    output = create_repack_candidate(args.baseline_dir, args.candidate_dir)
    print(json.dumps({"candidate_database": str(output), "size_bytes": output.stat().st_size}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
