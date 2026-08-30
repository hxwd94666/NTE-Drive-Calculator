# 编排版本化游戏静态 SQLite 数据库的构建与报告。
"""Command-line composition root for the static game database builder."""

from __future__ import annotations

import sys
from contextlib import closing
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.game_data.static_database_build_support import *
from tools.game_data.static_database_character_imports import CharacterImportMixin
from tools.game_data.static_database_blueprint_imports import BlueprintImportMixin
from tools.game_data.static_database_buff_imports import BuffImportMixin
from tools.game_data.static_database_equipment_imports import EquipmentImportMixin
from tools.game_data.static_database_combat_imports import CombatImportMixin
from tools.game_data.static_database_catalog_imports import CatalogImportMixin
from tools.game_data.static_database_encounter_imports import EncounterImportMixin
from tools.game_data.static_database_progression_imports import ProgressionImportMixin


RELEASE_DATABASE_PATH = PROJECT_ROOT / "data" / "game_static.sqlite3"
PREVIOUS_RELEASE_DATABASE_PATH = (
    PROJECT_ROOT / "build" / "previous" / "data" / "game_static.sqlite3"
)


class StaticDatabaseBuilder(
    CharacterImportMixin,
    BlueprintImportMixin,
    BuffImportMixin,
    EquipmentImportMixin,
    CombatImportMixin,
    CatalogImportMixin,
    EncounterImportMixin,
    ProgressionImportMixin,
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
        self.combat_blueprint_root = content_root
        self.include_source_payloads = include_source_payloads
        self.rows: dict[str, dict[str, Any]] = {}
        self.source_row_ids: dict[tuple[str, str], int] = {}
        self.awaken_rows: dict[int, tuple[dict[str, Any], int]] = {}
        self.character_effect_curve_rows: list[tuple[str, str, dict[str, Any], int]] = []

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
        self.connection.execute("INSERT INTO schema_migration VALUES (18, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (19, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (20, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (21, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (22, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (23, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (24, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (25, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (26, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (27, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (28, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (29, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (30, ?)", (now,))
        self.connection.execute(
            "INSERT INTO dataset VALUES (?, ?, ?)",
            (self.dataset_id, IMPORTER_VERSION, now),
        )
        self._mirror_sources()
        self._mirror_awaken_sources()
        self._mirror_character_effect_curve_sources()
        self._import_characters()
        self._import_character_awakens()
        self._import_character_panel_growth()
        self._import_character_skills()
        self._import_skill_damage()
        self._import_combat_context()
        self._import_enemy_combat_profiles()
        self._import_roguelike_modifiers()
        self._import_monster_instance_profiles()
        self._import_abyss_bindings()
        self._import_equipment_attributes()
        self._import_official_character_shape_bonuses()
        self._import_character_likeability_bonuses()
        self._import_equipment_shapes()
        self._import_equipment_suits()
        self._import_equipment_items()
        self._import_equipment_progression()
        self._import_equipment_plans()
        self._import_default_character_weights()
        self._import_forks()
        self._import_combat_catalogs()
        self._import_combat_curves()
        self._import_combat_blueprints()
        self._import_buff_definitions()
        self._import_encounter_catalogs()
        self._import_progression_catalog()
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


def backup_existing_release_database(output: Path, backup: Path) -> None:
    """Atomically preserve the release database before a rebuild replaces it."""

    output = output.expanduser().resolve()
    backup = backup.expanduser().resolve()
    if backup == output:
        raise ValueError("静态数据库备份路径不能与输出路径相同")
    if not output.is_file():
        return
    try:
        with closing(
            sqlite3.connect(f"{output.as_uri()}?mode=ro", uri=True)
        ) as connection:
            attributed_count = int(connection.execute(
                """SELECT COUNT(*) FROM character_weight_recommendation
                   WHERE source_kind IN ('workshop_api', 'workshop_cache')"""
            ).fetchone()[0])
    except sqlite3.Error as exc:
        raise RuntimeError(f"无法审计待备份的发行静态库：{output}") from exc
    if attributed_count <= 0:
        return
    backup.parent.mkdir(parents=True, exist_ok=True)
    handle, backup_name = tempfile.mkstemp(
        prefix=f".{backup.name}.", suffix=".tmp", dir=backup.parent
    )
    os.close(handle)
    backup_temporary = Path(backup_name)
    try:
        with (
            closing(sqlite3.connect(f"{output.as_uri()}?mode=ro", uri=True)) as source,
            closing(sqlite3.connect(backup_temporary)) as destination,
        ):
            source.backup(destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise RuntimeError(f"发行静态库备份完整性检查失败：{integrity}")
        os.replace(backup_temporary, backup)
    except BaseException:
        backup_temporary.unlink(missing_ok=True)
        raise


def build_database(
    source: Path,
    output: Path,
    report_dir: Path,
    *,
    dataset_id: str,
    as_of: date,
    overrides_path: Path = DEFAULT_OVERRIDES,
    config_dir: Path = PROJECT_ROOT / "config",
    backup_existing_to: Path | None = None,
    include_source_payloads: bool = True,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    content_root = resolve_content_root(source)
    output = output.expanduser().resolve()
    report_dir = report_dir.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    if backup_existing_to is not None:
        backup_existing_release_database(output, backup_existing_to)
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
                    populate_graduation_templates,
                )
            except ImportError:
                from build_graduation_templates import (
                    populate_graduation_templates,
                )
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
        "--backup-existing-to",
        type=Path,
        help=(
            "覆盖输出前原子备份现有发行静态库；无工坊 API Key 时用于继承旧权重"
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
    output = args.output.expanduser().resolve()
    backup_existing_to = args.backup_existing_to
    if backup_existing_to is None and output == RELEASE_DATABASE_PATH.resolve():
        backup_existing_to = PREVIOUS_RELEASE_DATABASE_PATH
    report = build_database(
        args.source,
        args.output,
        args.report_dir,
        dataset_id=args.dataset_id,
        as_of=args.as_of,
        overrides_path=args.overrides,
        config_dir=args.config_dir,
        backup_existing_to=backup_existing_to,
        include_source_payloads=not args.omit_source_payloads,
        manifest_path=args.manifest,
    )
    print(f"SQLite: {Path(args.output).resolve()}")
    print(f"Report: {Path(args.report_dir).resolve()}")
    print(json.dumps(report["database_counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
