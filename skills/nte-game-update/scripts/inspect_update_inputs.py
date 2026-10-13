# 只读定位解包来源与旧发行来源差异，报告仅写仓库 build 内的新文件。
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import sqlite3
from pathlib import Path


def digest(path: Path) -> str:
    before = path.stat()
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("读取期间来源发生变化：" + path.name)
    return h.hexdigest().upper()


def content_root(source: Path) -> Path:
    source = source.resolve()
    if not source.is_dir():
        raise ValueError("解包目录不存在")
    candidates = [source, source/"Content", source/"HT/Content"]
    # 只检查已知层次与直接地区目录，不扫描整盘或任意深度找版本。
    candidates.extend(child/"HT/Content" for child in source.iterdir()
                      if child.is_dir() and not child.name.startswith("."))
    valid = sorted({p.resolve() for p in candidates
                    if (p/"DataTable").is_dir() and p.resolve().is_relative_to(source)})
    if len(valid) != 1:
        raise ValueError(f"Content 候选为 {len(valid)} 个，请指定唯一地区/版本 Content 根")
    return valid[0]


def inspect(repo: Path, source: Path) -> dict:
    repo = repo.resolve()
    if not (repo/"AGENTS.md").is_file() or not (repo/"tools/game_data").is_dir():
        raise ValueError("需指定当前计算器仓库")
    root = content_root(source)
    tables = []
    for p in (root/"DataTable").rglob("*.json"):
        if not p.resolve().is_relative_to(root):
            raise ValueError("数据表路径越过 Content 根")
        if p.is_file(): tables.append(p.relative_to(root).as_posix())
    report = {"format_version": 1, "content_root": str(root),
              "table_files": sorted(tables), "table_file_count": len(tables),
              "available_directories": {name: (root/name).is_dir() for name in
                                        ["DataTable", "Blueprints", "Characters", "Localization", "UI"]},
              "comparison_scope": "previously imported source_file entries only; new files/rows, skills, assets and functional support require catalogue/builder review",
              "baseline": None, "source_comparison": None}
    database, manifest = repo/"data/game_static.sqlite3", repo/"data/manifest.json"
    if not database.exists() and not manifest.exists():
        return report  # 新 fork 没有发行基线，不把它解释成零差异。
    if not database.is_file() or not manifest.is_file():
        raise ValueError("发行库与 manifest 必须成对核对")
    identity = json.loads(manifest.read_text(encoding="utf-8"))
    original_hash = digest(database)
    if original_hash != str(identity.get("database", {}).get("sha256", "")).upper():
        raise ValueError("旧发行数据库与 manifest 哈希不一致")
    with closing(sqlite3.connect(database.as_uri()+"?mode=ro&immutable=1", uri=True)) as connection:
        scopes = connection.execute("SELECT dataset_id, scope FROM dataset_scope").fetchall()
        if len(scopes) != 1 or scopes[0][1] != "game":
            raise ValueError("基线必须为完整 game 静态库，不把图鉴当主库")
        if scopes[0][0] != identity["database"].get("dataset_id"):
            raise ValueError("旧库与 manifest dataset 不一致")
        sources = connection.execute("SELECT relative_path, sha256 FROM source_file ORDER BY relative_path").fetchall()
    comparison = {"changed": [], "missing": [], "unchanged": 0}
    for relative, expected in sources:
        # 与当前晋升入口一致：combat_blueprint 是登记命名空间，不是磁盘目录。
        normalized = relative.split("#", 1)[0]
        if normalized.startswith("combat_blueprint/"):
            normalized = normalized[len("combat_blueprint/"):]
        p = (root/normalized).resolve()
        if not p.is_relative_to(root):
            raise ValueError("登记来源路径越过 Content 根")
        if not p.is_file(): comparison["missing"].append(relative)
        elif digest(p) != str(expected).upper(): comparison["changed"].append(relative)
        else: comparison["unchanged"] += 1
    if digest(database) != original_hash:
        raise ValueError("审计期间发行基线已被替换")
    report["baseline"] = {"dataset_id": scopes[0][0], "database_sha256": original_hash,
                          "registered_source_files": len(sources)}
    report["source_comparison"] = comparison
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = (args.report if args.report.is_absolute() else repo/args.report).resolve()
    if not output.is_relative_to((repo/"build").resolve()) or output == repo/"build":
        parser.error("报告必须位于当前仓库 build 内")
    if output.exists():
        parser.error("报告已存在，请使用本次新的批次文件，不覆盖旧证据")
    try:
        report = inspect(repo, args.source)
    except (ValueError, OSError, sqlite3.Error, KeyError) as exc:
        parser.error(str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    counts = report["source_comparison"]
    print(json.dumps({"report": str(output), "table_file_count": report["table_file_count"],
                      "changed_sources": len(counts["changed"]) if counts else None,
                      "missing_sources": len(counts["missing"]) if counts else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
