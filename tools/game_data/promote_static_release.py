# 将完成全部后处理的候选静态数据库安全晋升为正式发行数据。
"""校验候选静态数据库、最终报告和 manifest，再成对替换 ``data`` 产物。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

try:
    from .static_database_build_support import (
        IMPORTER_VERSION,
        SCHEMA_VERSION,
        write_static_manifest,
    )
except ImportError:  # 支持直接运行
    from static_database_build_support import (
        IMPORTER_VERSION,
        SCHEMA_VERSION,
        write_static_manifest,
    )


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGET_DIR = ROOT / "data"
DATABASE_FILENAME = "game_static.sqlite3"
MANIFEST_FILENAME = "manifest.json"
REPORT_RELATIVE_PATH = Path("report") / "static_database_report.json"
MIB_BYTES = 1024 * 1024
DATABASE_WARNING_BYTES = 95 * MIB_BYTES
DATABASE_REPOSITORY_BUDGET_BYTES = 96 * MIB_BYTES
DATABASE_HARD_LIMIT_BYTES = 100 * MIB_BYTES
SIZE_MANIFEST_REQUIRED_SCHEMA_VERSION = 30


class StaticReleasePromotionError(RuntimeError):
    """候选静态库不满足发行晋升条件。"""


def format_size(size_bytes: int) -> str:
    """同时显示无歧义的字节数和二进制 MiB。"""

    return f"{size_bytes} bytes ({size_bytes / MIB_BYTES:.2f} MiB)"


def validate_database_size(
    size_bytes: int,
    *,
    allow_size_warning: bool = False,
    repository_bound: bool = True,
) -> None:
    """执行项目体积预算与 GitHub 100 MiB 单文件硬边界。"""

    if size_bytes >= DATABASE_HARD_LIMIT_BYTES:
        raise StaticReleasePromotionError(
            "发行静态数据库达到 GitHub 单文件永久硬上限："
            f"实际={format_size(size_bytes)}，"
            f"上限<{format_size(DATABASE_HARD_LIMIT_BYTES)}"
        )
    if repository_bound and size_bytes > DATABASE_REPOSITORY_BUDGET_BYTES:
        raise StaticReleasePromotionError(
            "发行静态数据库超过仓库绝对预算："
            f"实际={format_size(size_bytes)}，"
            f"预算<={format_size(DATABASE_REPOSITORY_BUDGET_BYTES)}；"
            "请改为 Release 分发或拆分只读库"
        )
    if size_bytes >= DATABASE_WARNING_BYTES and not allow_size_warning:
        raise StaticReleasePromotionError(
            "发行静态数据库达到默认阻断边界："
            f"实际={format_size(size_bytes)}，"
            f"边界={format_size(DATABASE_WARNING_BYTES)}；"
            "审计增量后可显式使用 --allow-size-warning"
        )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StaticReleasePromotionError(f"无法读取{label}：{path}") from exc
    if not isinstance(value, dict):
        raise StaticReleasePromotionError(f"{label}必须是 JSON 对象：{path}")
    return value


def load_local_config(path: Path) -> dict[str, Any]:
    config = _read_json_object(path.expanduser().resolve(), "本机静态数据配置")
    for key in ("dataset_id", "official_content_root"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise StaticReleasePromotionError(f"本机静态数据配置缺少 {key}")
    return config


def _readonly_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _database_summary(path: Path) -> dict[str, Any]:
    try:
        with closing(_readonly_connection(path)) as connection:
            dataset_rows = connection.execute(
                "SELECT dataset_id, importer_version, built_at_utc FROM dataset"
            ).fetchall()
            if len(dataset_rows) != 1:
                raise StaticReleasePromotionError(
                    f"候选数据库必须且只能包含一个 dataset，实际为 {len(dataset_rows)}"
                )
            schema_row = connection.execute(
                "SELECT MAX(version) FROM schema_migration"
            ).fetchone()
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            foreign_key_violations = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            payload_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM source_row WHERE payload_json IS NOT NULL"
                ).fetchone()[0]
            )
            source_files = connection.execute(
                "SELECT relative_path, sha256 FROM source_file ORDER BY relative_path"
            ).fetchall()
    except sqlite3.Error as exc:
        raise StaticReleasePromotionError(f"无法审计候选数据库：{path}") from exc

    if quick_check != ("ok",):
        raise StaticReleasePromotionError(f"候选数据库 quick_check 失败：{quick_check!r}")
    if foreign_key_violations:
        raise StaticReleasePromotionError(
            f"候选数据库存在外键错误：{foreign_key_violations[:10]!r}"
        )
    if payload_count:
        raise StaticReleasePromotionError(
            f"发行候选仍含 {payload_count} 条 source_row.payload_json"
        )
    dataset_id, importer_version, built_at_utc = dataset_rows[0]
    return {
        "dataset_id": str(dataset_id),
        "importer_version": int(importer_version),
        "built_at_utc": str(built_at_utc),
        "schema_version": int(schema_row[0] if schema_row else 0),
        "source_files": [(str(row[0]), str(row[1])) for row in source_files],
    }


def _source_path(content_root: Path, relative_path: str) -> Path:
    normalized = relative_path
    prefix = "combat_blueprint/"
    if normalized.startswith(prefix):
        normalized = normalized[len(prefix) :]
    return content_root.joinpath(*PurePosixPath(normalized).parts)


def validate_source_files(
    source_files: Sequence[tuple[str, str]],
    content_root: Path,
) -> None:
    missing: list[str] = []
    changed: list[str] = []
    for relative_path, expected_hash in source_files:
        source_path = _source_path(content_root, relative_path)
        if not source_path.is_file():
            missing.append(relative_path)
            continue
        if sha256(source_path) != expected_hash.upper():
            changed.append(relative_path)
    if missing or changed:
        raise StaticReleasePromotionError(
            "候选数据库与本机官方来源不一致："
            f"缺失={len(missing)}，哈希变化={len(changed)}；"
            f"示例={missing[:5] + changed[:5]}"
        )


def _write_manifest_atomic(database_path: Path, manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{manifest_path.name}.", suffix=".tmp", dir=manifest_path.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        write_static_manifest(database_path, temporary)
        os.replace(temporary, manifest_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def validate_manifest(
    database_path: Path,
    manifest_path: Path,
    summary: dict[str, Any],
) -> dict[str, Any]:
    manifest = _read_json_object(manifest_path, "候选 manifest")
    database = manifest.get("database")
    build_tool = manifest.get("build_tool")
    if not isinstance(database, dict) or not isinstance(build_tool, dict):
        raise StaticReleasePromotionError("候选 manifest 缺少 database/build_tool")
    recorded_size = database.get("size_bytes")
    if recorded_size is not None and (
        isinstance(recorded_size, bool) or not isinstance(recorded_size, int)
    ):
        raise StaticReleasePromotionError(
            "候选 manifest 的 database.size_bytes 必须是整数"
        )
    expected = {
        "filename": DATABASE_FILENAME,
        "dataset_id": summary["dataset_id"],
        "schema_version": summary["schema_version"],
        "size_bytes": database_path.stat().st_size,
        "sha256": sha256(database_path),
        "source_payloads_omitted": True,
    }
    if (
        summary["schema_version"] < SIZE_MANIFEST_REQUIRED_SCHEMA_VERSION
        and recorded_size is None
    ):
        expected.pop("size_bytes")
    if (
        summary["schema_version"] >= SIZE_MANIFEST_REQUIRED_SCHEMA_VERSION
        and recorded_size is None
    ):
        raise StaticReleasePromotionError("候选 manifest 缺少 database.size_bytes")
    actual = {key: database.get(key) for key in expected}
    actual["sha256"] = str(actual.get("sha256") or "").upper()
    if actual != expected:
        raise StaticReleasePromotionError(
            f"候选 manifest 与数据库不一致：清单={actual}，实际={expected}"
        )
    if int(build_tool.get("importer_version") or 0) != summary["importer_version"]:
        raise StaticReleasePromotionError("候选 manifest 的 importer_version 不一致")
    return manifest


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    table_names = [
        str(row[0])
        for row in connection.execute(
            """SELECT name FROM sqlite_master
               WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
               ORDER BY name"""
        )
    ]
    counts: dict[str, int] = {}
    for table_name in table_names:
        quoted = '"' + table_name.replace('"', '""') + '"'
        counts[table_name] = int(
            connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
        )
    return counts


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def refresh_final_report(
    database_path: Path,
    report_path: Path,
    summary: dict[str, Any],
) -> dict[str, Any]:
    with closing(_readonly_connection(database_path)) as connection:
        counts = _table_counts(connection)
    finalized_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = {
        "schema_version": summary["schema_version"],
        "dataset_id": summary["dataset_id"],
        "built_at_utc": summary["built_at_utc"],
        "finalized_at_utc": finalized_at,
        "database_path": str(database_path.resolve()),
        "database_sha256": sha256(database_path).lower(),
        "database_size_bytes": database_path.stat().st_size,
        "database_size_mib": round(database_path.stat().st_size / MIB_BYTES, 2),
        "size_thresholds_bytes": {
            "default_block": DATABASE_WARNING_BYTES,
            "repository_budget": DATABASE_REPOSITORY_BUDGET_BYTES,
            "permanent_hard_limit": DATABASE_HARD_LIMIT_BYTES,
        },
        "localization_storage_policy": {
            "included": ["localization_keys", "current_language_projection"],
            "excluded": ["full_multilingual_source_payloads"],
        },
        "source_payloads_included": False,
        "database_counts": counts,
        "foreign_key_violations": [],
    }
    _write_text_atomic(
        report_path,
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    markdown_path = report_path.with_suffix(".md")
    lines = [
        "# NTE 静态数据库最终发行报告",
        "",
        f"数据集：`{summary['dataset_id']}`；最终化时间：`{finalized_at}`。",
        "",
        f"数据库 SHA-256：`{report['database_sha256'].upper()}`。",
        "",
        f"数据库大小：`{format_size(report['database_size_bytes'])}`。",
        "",
        "本地化只保留键与当前语言投影；完整多语言原始 payload 不进入发行库。",
        "",
        "## 数据库数量",
        "",
    ]
    lines.extend(f"- `{table}`：{count}" for table, count in counts.items())
    _write_text_atomic(markdown_path, "\n".join(lines) + "\n")
    return report


def validate_final_report(
    database_path: Path,
    report_path: Path,
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Validate existing final evidence without rewriting candidate files."""

    report = _read_json_object(report_path, "候选最终报告")
    with closing(_readonly_connection(database_path)) as connection:
        counts = _table_counts(connection)
    expected = {
        "schema_version": summary["schema_version"],
        "dataset_id": summary["dataset_id"],
        "built_at_utc": summary["built_at_utc"],
        "database_path": str(database_path.resolve()),
        "database_sha256": sha256(database_path).lower(),
        "database_size_bytes": database_path.stat().st_size,
        "database_size_mib": round(database_path.stat().st_size / MIB_BYTES, 2),
        "source_payloads_included": False,
        "database_counts": counts,
        "foreign_key_violations": [],
    }
    actual = {key: report.get(key) for key in expected}
    if actual != expected:
        raise StaticReleasePromotionError(
            f"候选最终报告与数据库不一致：报告={actual}，实际={expected}"
        )
    finalized_at = report.get("finalized_at_utc")
    if not isinstance(finalized_at, str) or not finalized_at.strip():
        raise StaticReleasePromotionError("候选最终报告缺少 finalized_at_utc")
    if not report_path.with_suffix(".md").is_file():
        raise StaticReleasePromotionError("候选最终报告缺少 Markdown 审计副本")
    return report


def _validate_candidate_database(
    candidate_dir: Path,
    local_config_path: Path,
    *,
    allow_size_warning: bool,
) -> dict[str, Any]:
    candidate_dir = candidate_dir.expanduser().resolve()
    database_path = candidate_dir / DATABASE_FILENAME
    config = load_local_config(local_config_path)
    content_root = Path(str(config["official_content_root"])).expanduser().resolve()
    if not content_root.is_dir():
        raise StaticReleasePromotionError(f"官方 Content 目录不存在：{content_root}")
    if not database_path.is_file():
        raise StaticReleasePromotionError(f"发行候选数据库不存在：{database_path}")

    database_size = database_path.stat().st_size
    validate_database_size(
        database_size,
        allow_size_warning=allow_size_warning,
    )
    summary = _database_summary(database_path)
    configured_dataset = str(config["dataset_id"])
    if summary["dataset_id"] != configured_dataset:
        raise StaticReleasePromotionError(
            "本机配置与候选数据库 dataset 不一致："
            f"配置={configured_dataset}，候选={summary['dataset_id']}"
        )
    if summary["schema_version"] != SCHEMA_VERSION:
        raise StaticReleasePromotionError(
            f"候选 schema={summary['schema_version']}，当前代码 schema={SCHEMA_VERSION}"
        )
    if summary["importer_version"] != IMPORTER_VERSION:
        raise StaticReleasePromotionError(
            "候选 importer 与当前代码不一致："
            f"候选={summary['importer_version']}，代码={IMPORTER_VERSION}"
        )
    validate_source_files(summary["source_files"], content_root)
    return {
        "candidate_dir": candidate_dir,
        "database_path": database_path,
        "manifest_path": candidate_dir / MANIFEST_FILENAME,
        "report_path": candidate_dir / REPORT_RELATIVE_PATH,
        "summary": summary,
        "database_size_bytes": database_size,
    }


def finalize_candidate(
    candidate_dir: Path,
    local_config_path: Path,
    *,
    allow_size_warning: bool = False,
) -> dict[str, Any]:
    candidate = _validate_candidate_database(
        candidate_dir,
        local_config_path,
        allow_size_warning=allow_size_warning,
    )
    database_path = Path(candidate["database_path"])
    manifest_path = Path(candidate["manifest_path"])
    report_path = Path(candidate["report_path"])
    summary = dict(candidate["summary"])
    _write_manifest_atomic(database_path, manifest_path)
    manifest = validate_manifest(database_path, manifest_path, summary)
    refresh_final_report(database_path, report_path, summary)
    report = validate_final_report(database_path, report_path, summary)
    return {
        "database_path": database_path,
        "manifest_path": manifest_path,
        "report_path": report_path,
        "summary": summary,
        "manifest": manifest,
        "report": report,
        "database_size_bytes": candidate["database_size_bytes"],
    }


def verify_candidate(
    candidate_dir: Path,
    local_config_path: Path,
    *,
    allow_size_warning: bool = False,
) -> dict[str, Any]:
    """Fully verify an already finalized candidate without filesystem writes."""

    candidate = _validate_candidate_database(
        candidate_dir,
        local_config_path,
        allow_size_warning=allow_size_warning,
    )
    database_path = Path(candidate["database_path"])
    manifest_path = Path(candidate["manifest_path"])
    report_path = Path(candidate["report_path"])
    summary = dict(candidate["summary"])
    validate_manifest(database_path, manifest_path, summary)
    validate_final_report(database_path, report_path, summary)
    return {
        "verified": True,
        "dataset_id": summary["dataset_id"],
        "schema_version": summary["schema_version"],
        "importer_version": summary["importer_version"],
        "source_file_count": len(summary["source_files"]),
        "database_sha256": sha256(database_path),
        "database_size_bytes": candidate["database_size_bytes"],
        "candidate_database": str(database_path),
        "candidate_manifest": str(manifest_path),
        "final_report": str(report_path),
    }


def _temporary_path(directory: Path, filename: str, label: str) -> Path:
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{filename}.{label}.", suffix=".tmp", dir=directory
    )
    os.close(handle)
    return Path(temporary_name)


def promote_candidate(
    candidate_dir: Path,
    local_config_path: Path,
    target_dir: Path = DEFAULT_TARGET_DIR,
    *,
    allow_size_warning: bool = False,
) -> dict[str, Any]:
    resolved_candidate_dir = candidate_dir.expanduser().resolve()
    target_dir = target_dir.expanduser().resolve()
    if resolved_candidate_dir == target_dir:
        raise StaticReleasePromotionError("候选目录不得与正式发行目录相同")
    finalized = finalize_candidate(
        resolved_candidate_dir,
        local_config_path,
        allow_size_warning=allow_size_warning,
    )
    source_database = Path(finalized["database_path"])
    source_manifest = Path(finalized["manifest_path"])
    target_dir.mkdir(parents=True, exist_ok=True)
    target_database = target_dir / DATABASE_FILENAME
    target_manifest = target_dir / MANIFEST_FILENAME
    if source_database == target_database or source_manifest == target_manifest:
        raise StaticReleasePromotionError("候选产物不得与正式发行产物相同")

    pending_database = _temporary_path(target_dir, DATABASE_FILENAME, "pending")
    pending_manifest = _temporary_path(target_dir, MANIFEST_FILENAME, "pending")
    rollback_database = _temporary_path(target_dir, DATABASE_FILENAME, "rollback")
    rollback_manifest = _temporary_path(target_dir, MANIFEST_FILENAME, "rollback")
    database_existed = target_database.is_file()
    manifest_existed = target_manifest.is_file()
    database_replaced = False
    manifest_replaced = False
    expected_hash = sha256(source_database)
    try:
        shutil.copy2(source_database, pending_database)
        shutil.copy2(source_manifest, pending_manifest)
        if sha256(pending_database) != expected_hash:
            raise StaticReleasePromotionError("候选数据库复制到 pending 后 SHA-256 变化")
        if database_existed:
            shutil.copy2(target_database, rollback_database)
        if manifest_existed:
            shutil.copy2(target_manifest, rollback_manifest)

        os.replace(pending_database, target_database)
        database_replaced = True
        os.replace(pending_manifest, target_manifest)
        manifest_replaced = True
        final_summary = _database_summary(target_database)
        validate_manifest(target_database, target_manifest, final_summary)
        if final_summary["dataset_id"] != finalized["summary"]["dataset_id"]:
            raise StaticReleasePromotionError("晋升后的 dataset 与候选不一致")
    except BaseException:
        if database_replaced:
            if database_existed:
                os.replace(rollback_database, target_database)
            else:
                target_database.unlink(missing_ok=True)
        if manifest_replaced:
            if manifest_existed:
                os.replace(rollback_manifest, target_manifest)
            else:
                target_manifest.unlink(missing_ok=True)
        raise
    finally:
        for path in (
            pending_database,
            pending_manifest,
            rollback_database,
            rollback_manifest,
        ):
            path.unlink(missing_ok=True)

    return {
        "dataset_id": finalized["summary"]["dataset_id"],
        "schema_version": finalized["summary"]["schema_version"],
        "importer_version": finalized["summary"]["importer_version"],
        "source_file_count": len(finalized["summary"]["source_files"]),
        "database_sha256": expected_hash,
        "database_size_bytes": finalized["database_size_bytes"],
        "target_database": str(target_database),
        "target_manifest": str(target_manifest),
        "final_report": str(finalized["report_path"]),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate-dir",
        type=Path,
        required=True,
        help="包含 game_static.sqlite3、manifest.json 和 report/ 的最终候选目录",
    )
    parser.add_argument(
        "--local-config",
        type=Path,
        required=True,
        help="仓库外 local.paths.json；其 dataset_id 和 official_content_root 为晋升门禁",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=DEFAULT_TARGET_DIR,
        help="正式发行目录；默认是仓库 data 目录",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--finalize-only",
        action="store_true",
        help="只最终化候选 manifest 和报告；不替换正式 data 产物",
    )
    mode.add_argument(
        "--verify-only",
        action="store_true",
        help="只读预检已最终化候选；不改写候选，也不替换正式 data 产物",
    )
    parser.add_argument(
        "--allow-size-warning",
        action="store_true",
        help=(
            "显式放行 95 MiB（99614720 bytes）至 96 MiB "
            "（100663296 bytes）的已审计数据库；"
            "不覆盖项目仓库预算或 GitHub 100 MiB 单文件硬上限"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.finalize_only:
            finalized = finalize_candidate(
                args.candidate_dir,
                args.local_config,
                allow_size_warning=args.allow_size_warning,
            )
            summary = dict(finalized["summary"])
            database_path = Path(finalized["database_path"])
            result = {
                "finalized": True,
                "dataset_id": summary["dataset_id"],
                "schema_version": summary["schema_version"],
                "importer_version": summary["importer_version"],
                "source_file_count": len(summary["source_files"]),
                "database_sha256": sha256(database_path),
                "database_size_bytes": finalized["database_size_bytes"],
                "candidate_database": str(database_path),
                "candidate_manifest": str(finalized["manifest_path"]),
                "final_report": str(finalized["report_path"]),
            }
        elif args.verify_only:
            result = verify_candidate(
                args.candidate_dir,
                args.local_config,
                allow_size_warning=args.allow_size_warning,
            )
        else:
            result = promote_candidate(
                args.candidate_dir,
                args.local_config,
                args.target_dir,
                allow_size_warning=args.allow_size_warning,
            )
    except (OSError, sqlite3.Error, StaticReleasePromotionError) as exc:
        print(f"[失败] {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
