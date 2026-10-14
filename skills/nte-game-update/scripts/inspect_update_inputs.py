# 只读定位同版解包并比较所选静态包的登记来源，报告仅写仓库 build 新文件。
"""定位唯一 Content 根并比较主库或显式图鉴基线；不执行构建或晋升。"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3


def file_identity(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    return info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def digest(path: Path) -> str:
    before = file_identity(path)
    with path.open("rb") as stream:
        value = hashlib.file_digest(stream, "sha256").hexdigest().upper()
    if file_identity(path) != before:
        raise ValueError("读取期间来源发生变化：" + path.name)
    return value


def content_root(source: Path) -> Path:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise ValueError("解包目录不存在")
    candidates = [source, source / "Content", source / "HT/Content"]
    # 仅已知层次与直接地区目录，不递归发现版本或扫描其他工作区。
    candidates.extend(child / "HT/Content" for child in source.iterdir()
                      if child.is_dir() and not child.name.startswith("."))
    valid = sorted({path.resolve() for path in candidates
                    if (path / "DataTable").is_dir()
                    and path.resolve().is_relative_to(source)})
    if len(valid) != 1:
        raise ValueError(f"Content 候选为 {len(valid)} 个，请指定唯一地区/版本 Content 根")
    root = valid[0]
    if not (root / "DataTable").resolve().is_relative_to(root):
        raise ValueError("DataTable 真实路径越过 Content 根")
    return root


def source_path(root: Path, relative: str) -> Path:
    # 对齐 promote_static_release._source_path：命名空间/投影不是磁盘层级。
    normalized = relative.split("#", 1)[0].removeprefix("combat_blueprint/")
    logical = PurePosixPath(normalized)
    if (not normalized or "\\" in normalized or ":" in normalized
            or logical.is_absolute()
            or any(part in {"", ".", ".."} for part in normalized.split("/"))):
        raise ValueError("登记来源包含无效逻辑路径")
    path = root.joinpath(*logical.parts).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("登记来源真实路径越过 Content 根")
    return path


def read_baseline(directory: Path) -> tuple[dict, list[tuple[str, str]]]:
    database, manifest = directory / "game_static.sqlite3", directory / "manifest.json"
    if not database.is_file() or not manifest.is_file():
        raise ValueError("所选基线的发行库与 manifest 必须成对存在")
    if any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("基线有活动或恢复 sidecar，请先提供已冻结静态包")
    with database.open("rb") as stream:
        header = stream.read(20)
    if header[:16] == b"SQLite format 3\x00" and header[18:20] != b"\x01\x01":
        raise ValueError("基线须为无 WAL 的已冻结静态包")
    manifest_hash = digest(manifest)
    identity = json.loads(manifest.read_text(encoding="utf-8"))
    metadata, tool = identity["database"], identity["build_tool"]
    original_hash = digest(database)
    if metadata["filename"] != database.name or original_hash != str(metadata["sha256"]).upper():
        raise ValueError("基线数据库与 manifest 文件或哈希不一致")
    size = metadata.get("size_bytes")
    if size is not None and (type(size) is not int or size != database.stat().st_size):
        raise ValueError("基线数据库与 manifest 大小不一致")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        if connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
            raise ValueError("基线须为无 WAL 的已冻结静态包")
        datasets = connection.execute("SELECT dataset_id, importer_version FROM dataset").fetchall()
        scopes = connection.execute("SELECT dataset_id, scope FROM dataset_scope").fetchall()
        schema = connection.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0]
        if (len(datasets) != 1 or len(scopes) != 1
                or scopes[0][0] != datasets[0][0]
                or scopes[0][1] not in {"game", "role_page", "reference"}):
            raise ValueError("基线必须含唯一正式 dataset 与用途")
        expected = (datasets[0][0], datasets[0][1], scopes[0][1], schema)
        recorded = (metadata["dataset_id"], tool["importer_version"],
                    identity.get("catalog_scope", "game"), metadata["schema_version"])
        if expected != recorded:
            raise ValueError("基线内部 dataset/schema/importer/scope 与 manifest 不一致")
        sources = connection.execute(
            "SELECT relative_path, sha256 FROM source_file ORDER BY relative_path"
        ).fetchall()
    return {"directory": str(directory), "dataset_id": datasets[0][0],
            "catalog_scope": scopes[0][1], "schema_version": schema,
            "importer_version": datasets[0][1], "database_sha256": original_hash,
            "manifest_sha256": manifest_hash, "registered_source_files": len(sources)}, sources


def inspect(repo: Path, source: Path, baseline_dir: Path | None = None) -> dict:
    repo = repo.expanduser().resolve()
    if not (repo / "AGENTS.md").is_file() or not (repo / "tools/game_data").is_dir():
        raise ValueError("需指定当前计算器仓库")
    root = content_root(source)
    tables = []
    for path in (root / "DataTable").rglob("*.json"):
        if not path.resolve().is_relative_to(root):
            raise ValueError("数据表路径越过 Content 根")
        if path.is_file():
            tables.append(path.relative_to(root).as_posix())
    report = {"format_version": 1, "content_root": str(root),
              "table_files": sorted(tables), "table_file_count": len(tables),
              "available_directories": {name: (root / name).is_dir() for name in
                                        ["DataTable", "Blueprints", "Characters", "Localization", "UI"]},
              "comparison_scope": "registered source_file hashes only; unregistered tables are not version additions; rows, skills, assets and consumers need builder review",
              "baseline": None, "source_comparison": None}
    selected = baseline_dir if baseline_dir is not None else repo / "data"
    directory = (selected if selected.is_absolute() else repo / selected).expanduser().resolve()
    if baseline_dir is None and not any((directory / name).exists()
                                       for name in ("game_static.sqlite3", "manifest.json")):
        return report  # 缺主发行基线仍为未知，保持旧调用契约。
    baseline, sources = read_baseline(directory)
    if baseline_dir is None and baseline["catalog_scope"] != "game":
        raise ValueError("默认基线必须为主 game 静态库；图鉴请显式指定 --baseline-dir")
    comparison = {"changed": [], "missing": [], "unchanged": 0, "unregistered_table_files": []}
    # 多个 #namespace/Blueprint 投影共用物理文件哈希，不反复读取聚合文件。
    cache: dict[Path, tuple[str, tuple[int, int, int, int]]] = {}
    registered = set()
    for relative, expected in sources:
        path = source_path(root, relative)
        registered.add(path.relative_to(root).as_posix())
        if not path.is_file():
            comparison["missing"].append(relative)
            continue
        if path not in cache:
            before = file_identity(path)
            value = digest(path)
            if file_identity(path) != before:
                raise ValueError("来源在差异读取期间发生变化")
            cache[path] = value, before
        if cache[path][0] != str(expected).upper():
            comparison["changed"].append(relative)
        else:
            comparison["unchanged"] += 1
    comparison["unregistered_table_files"] = sorted(set(tables) - registered)
    if any(file_identity(path) != info for path, (_value, info) in cache.items()):
        raise ValueError("来源在差异读取期间发生变化")
    if (digest(directory / "game_static.sqlite3") != baseline["database_sha256"]
            or digest(directory / "manifest.json") != baseline["manifest_sha256"]):
        raise ValueError("差异读取期间发行基线已被替换")
    report["baseline"], report["source_comparison"] = baseline, comparison
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--baseline-dir", type=Path, help="已冻结静态包目录；相对仓库，省略时只比较 data 主库")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    output = (args.report if args.report.is_absolute() else repo / args.report).resolve()
    build = (repo / "build").resolve()
    if not build.is_relative_to(repo) or output == build or not output.is_relative_to(build):
        parser.error("报告必须位于当前仓库 build 内")
    if output.exists():
        parser.error("报告已存在，请使用本次新的批次文件，不覆盖旧证据")
    try:
        report = inspect(repo, args.source, args.baseline_dir)
        if output.is_relative_to(Path(report["content_root"])):
            raise ValueError("报告不得写入只读 Content 来源目录")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        parser.error(str(exc))
    counts = report["source_comparison"]
    print(json.dumps({"report": str(output), "table_file_count": report["table_file_count"],
                      "changed_sources": len(counts["changed"]) if counts else None,
                      "missing_sources": len(counts["missing"]) if counts else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
