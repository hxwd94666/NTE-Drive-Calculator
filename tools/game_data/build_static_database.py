# 编排版本化游戏静态 SQLite 数据库的构建与报告。
"""Command-line composition root for the static game database builder."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.game_data.static_database_build_support import *
from tools.game_data.static_database_character_imports import CharacterImportMixin
from tools.game_data.static_database_equipment_imports import EquipmentImportMixin
from tools.game_data.static_database_combat_imports import CombatImportMixin
from tools.game_data.static_database_catalog_imports import CatalogImportMixin


class StaticDatabaseBuilder(
    CharacterImportMixin,
    EquipmentImportMixin,
    CombatImportMixin,
    CatalogImportMixin,
):
    def __init__(
        self,
        connection: sqlite3.Connection,
        content_root: Path,
        *,
        dataset_id: str,
        as_of: date,
        overrides_path: Path,
        include_source_payloads: bool = True,
    ) -> None:
        self.connection = connection
        self.content_root = content_root
        self.dataset_id = dataset_id
        self.as_of = as_of
        self.overrides_path = overrides_path
        self.include_source_payloads = include_source_payloads
        self.rows: dict[str, dict[str, Any]] = {}
        self.source_row_ids: dict[tuple[str, str], int] = {}
        self.awaken_rows: dict[int, tuple[dict[str, Any], int]] = {}

    def build(self) -> dict[str, Any]:
        for schema_path in SCHEMA_PATHS:
            self.connection.executescript(schema_path.read_text(encoding="utf-8"))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.connection.execute(
            "INSERT INTO schema_migration VALUES (2, ?)",
            (now,),
        )
        self.connection.execute("INSERT INTO schema_migration VALUES (3, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (4, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (5, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (6, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (7, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (8, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (9, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (10, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (11, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (12, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (13, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (14, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (15, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (16, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (17, ?)", (now,))
        self.connection.execute(
            "INSERT INTO dataset VALUES (?, ?, ?)",
            (self.dataset_id, IMPORTER_VERSION, now),
        )
        self._mirror_sources()
        self._mirror_awaken_sources()
        self._import_characters()
        self._import_character_awakens()
        self._import_character_panel_growth()
        self._import_character_skills()
        self._import_skill_damage()
        self._import_combat_context()
        self._import_enemy_combat_profiles()
        self._import_monster_instance_profiles()
        self._import_abyss_bindings()
        self._import_equipment_attributes()
        self._import_equipment_shapes()
        self._import_equipment_suits()
        self._import_equipment_items()
        self._import_equipment_progression()
        self._import_equipment_plans()
        self._import_default_character_weights()
        self._import_forks()
        self._import_combat_catalogs()
        violations = [tuple(row) for row in self.connection.execute("PRAGMA foreign_key_check")]
        if violations:
            raise StaticDatabaseError(f"发现外键错误：{violations[:10]}")
        self.connection.commit()
        return self._database_counts()

def render_report(report: dict[str, Any]) -> str:
    counts = report["database_counts"]
    lines = [
        "# NTE 静态数据库构建报告",
        "",
        f"数据集：`{report['dataset_id']}`；构建时间：`{report['built_at_utc']}`。",
        "",
        "## 数据库数量",
        "",
    ]
    lines.extend(f"- `{table}`：{count}" for table, count in counts.items())
    return "\n".join(lines)


def build_database(
    source: Path,
    output: Path,
    report_dir: Path,
    *,
    dataset_id: str,
    as_of: date,
    overrides_path: Path = DEFAULT_OVERRIDES,
    config_dir: Path = PROJECT_ROOT / "config",
    release_shape_defaults_database: Path | None = None,
    include_source_payloads: bool = True,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    content_root = resolve_content_root(source)
    output = output.expanduser().resolve()
    report_dir = report_dir.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            builder = StaticDatabaseBuilder(
                connection,
                content_root,
                dataset_id=dataset_id,
                as_of=as_of,
                overrides_path=overrides_path,
                include_source_payloads=include_source_payloads,
            )
            counts = builder.build()
            try:
                from .build_graduation_templates import (
                    populate_logical_character_shape_bonuses,
                    populate_graduation_templates,
                )
            except ImportError:
                from build_graduation_templates import (
                    populate_logical_character_shape_bonuses,
                    populate_graduation_templates,
                )
            counts["logical_character_shape_bonus"] = populate_logical_character_shape_bonuses(
                connection,
                config_dir=config_dir.expanduser().resolve(),
                release_defaults_database=release_shape_defaults_database,
            )
            counts["logical_character_shape_bonus_property"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM logical_character_shape_bonus_property"
                ).fetchone()[0]
            )
            counts["character_shape_bonus"] = 0
            counts["character_shape_bonus_property"] = 0
            connection.commit()
            counts["character_graduation_template"] = populate_graduation_templates(
                connection,
                database_path=temporary,
                config_dir=config_dir.expanduser().resolve(),
            )
        finally:
            connection.close()
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    report = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "database_path": str(output),
        "database_sha256": file_sha256(output),
        "source_payloads_included": include_source_payloads,
        "database_counts": counts,
        "foreign_key_violations": [],
    }
    (report_dir / "static_database_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (report_dir / "static_database_report.md").write_text(
        render_report(report), encoding="utf-8"
    )
    if manifest_path is not None:
        write_static_manifest(output, manifest_path)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument(
        "--release-shape-defaults-database",
        type=Path,
        help=(
            "从确认的上一发行静态库继承仍可映射的角色额外形状默认值；"
            "不会读取账号或共享覆盖"
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="同时为发行数据库生成机器可读清单，例如 data/manifest.json",
    )
    parser.add_argument(
        "--omit-source-payloads",
        action="store_true",
        help="发行数据库不保存来源行原文，只保留行键和 SHA-256",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_database(
        args.source,
        args.output,
        args.report_dir,
        dataset_id=args.dataset_id,
        as_of=args.as_of,
        overrides_path=args.overrides,
        config_dir=args.config_dir,
        release_shape_defaults_database=args.release_shape_defaults_database,
        include_source_payloads=not args.omit_source_payloads,
        manifest_path=args.manifest,
    )
    print(f"SQLite: {Path(args.output).resolve()}")
    print(f"Report: {Path(args.report_dir).resolve()}")
    print(json.dumps(report["database_counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
