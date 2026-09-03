# 验证弧盘无条件常驻属性的识别、排除与歧义审计。
"""Regression coverage for imported unconditional fork panel properties."""

from __future__ import annotations

import json
import sqlite3
import unittest

from tools.game_data.static_database_catalog_imports import CatalogImportMixin


class _ForkPermanentImportProbe(CatalogImportMixin):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE fork_item (fork_id TEXT PRIMARY KEY, star_pack_id TEXT NOT NULL);
        CREATE TABLE fork_star_level (
            star_pack_id TEXT NOT NULL, star_level INTEGER NOT NULL,
            PRIMARY KEY (star_pack_id, star_level)
        );
        CREATE TABLE fork_star_parameter (
            star_pack_id TEXT NOT NULL, star_level INTEGER NOT NULL,
            ordinal INTEGER NOT NULL, name_id TEXT NOT NULL
        );
        CREATE TABLE fork_refinement_parameter_value (
            name_id TEXT NOT NULL, refinement_level INTEGER NOT NULL,
            value REAL NOT NULL, source_row_id INTEGER NOT NULL
        );
        CREATE TABLE combat_effect_definition (effect_definition_id TEXT PRIMARY KEY);
        CREATE TABLE combat_effect_buff_link (
            effect_definition_id TEXT NOT NULL, target_asset_path TEXT NOT NULL
        );
        CREATE TABLE buff_definition (asset_path TEXT PRIMARY KEY);
        CREATE TABLE combat_blueprint_reference (
            source_asset_path TEXT NOT NULL, property_path TEXT NOT NULL,
            target_asset_path TEXT NOT NULL, target_available INTEGER NOT NULL
        );
        CREATE TABLE buff_modifier (
            asset_path TEXT NOT NULL, ordinal INTEGER NOT NULL,
            property_id TEXT, modifier_operation TEXT,
            calculation_asset_path TEXT,
            application_requirement_asset_path TEXT,
            source_require_tags_json TEXT NOT NULL DEFAULT '[]',
            source_ignore_tags_json TEXT NOT NULL DEFAULT '[]',
            target_require_tags_json TEXT NOT NULL DEFAULT '[]',
            target_ignore_tags_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE fork_permanent_property (
            fork_id TEXT NOT NULL, refinement_level INTEGER NOT NULL,
            property_id TEXT NOT NULL, modifier_operation TEXT NOT NULL,
            property_value REAL NOT NULL, source_parameter_name_id TEXT NOT NULL,
            source_effect_definition_id TEXT NOT NULL,
            source_calculation_asset_path TEXT NOT NULL, source_row_id INTEGER NOT NULL,
            PRIMARY KEY (fork_id, refinement_level)
        );
    """)
    return connection


def _add_fork(
    connection: sqlite3.Connection,
    *,
    fork_id: str,
    property_id: str,
    parameter_id: str,
    values: tuple[float, ...] = (0.16, 0.20),
    requirement: str | None = None,
    source_tags: tuple[str, ...] = (),
    calculation_suffix: str = "Main",
) -> None:
    star_pack_id = f"star_{fork_id}"
    asset_path = f"buff:{fork_id}"
    connection.execute("INSERT INTO fork_item VALUES (?, ?)", (fork_id, star_pack_id))
    for refinement, value in enumerate(values, start=1):
        connection.execute(
            "INSERT INTO fork_star_level VALUES (?, ?)", (star_pack_id, refinement)
        )
        connection.execute(
            "INSERT INTO fork_star_parameter VALUES (?, ?, 0, ?)",
            (star_pack_id, refinement, parameter_id),
        )
        connection.execute(
            "INSERT INTO fork_refinement_parameter_value VALUES (?, ?, ?, ?)",
            (parameter_id, refinement, value, refinement),
        )
        effect_id = f"fork_star:{star_pack_id}:{refinement}"
        connection.execute("INSERT INTO combat_effect_definition VALUES (?)", (effect_id,))
        connection.execute(
            "INSERT INTO combat_effect_buff_link VALUES (?, ?)", (effect_id, asset_path)
        )
    connection.execute(
        """INSERT INTO buff_modifier(
               asset_path, ordinal, property_id, modifier_operation,
               calculation_asset_path, application_requirement_asset_path,
               source_require_tags_json
           ) VALUES (?, 0, ?, 'EGameplayModOp::Additive', ?, ?, ?)""",
        (
            asset_path,
            property_id,
            f"/Game/Calculation/{fork_id}/{calculation_suffix}",
            requirement,
            json.dumps(source_tags),
        ),
    )


class StaticForkPermanentImportTests(unittest.TestCase):
    def test_uses_inherited_modifier_when_refinement_buff_has_no_local_modifier(self) -> None:
        connection = _connection()
        self.addCleanup(connection.close)
        fork_id = "fork_Inherited"
        star_pack_id = f"star_{fork_id}"
        parent_asset = f"buff:{fork_id}:parent"
        connection.execute("INSERT INTO fork_item VALUES (?, ?)", (fork_id, star_pack_id))
        connection.execute("INSERT INTO buff_definition VALUES (?)", (parent_asset,))
        connection.execute(
            """INSERT INTO buff_modifier(
                   asset_path, ordinal, property_id, modifier_operation,
                   calculation_asset_path
               ) VALUES (?, 0, 'AtkUp', 'EGameplayModOp::Additive', ?)""",
            (parent_asset, f"/Game/Calculation/{fork_id}/AtkUp"),
        )
        parameter_id = "buff_Inherited_AtkUp"
        for refinement, value in enumerate((0.16, 0.20), start=1):
            child_asset = f"buff:{fork_id}:lv{refinement}"
            effect_id = f"fork_star:{star_pack_id}:{refinement}"
            connection.execute("INSERT INTO buff_definition VALUES (?)", (child_asset,))
            connection.execute(
                "INSERT INTO combat_blueprint_reference VALUES (?, '$[1].Super', ?, 1)",
                (child_asset, parent_asset),
            )
            connection.execute("INSERT INTO fork_star_level VALUES (?, ?)", (star_pack_id, refinement))
            connection.execute(
                "INSERT INTO fork_star_parameter VALUES (?, ?, 0, ?)",
                (star_pack_id, refinement, parameter_id),
            )
            connection.execute(
                "INSERT INTO fork_refinement_parameter_value VALUES (?, ?, ?, ?)",
                (parameter_id, refinement, value, refinement),
            )
            connection.execute("INSERT INTO combat_effect_definition VALUES (?)", (effect_id,))
            connection.execute(
                "INSERT INTO combat_effect_buff_link VALUES (?, ?)",
                (effect_id, child_asset),
            )

        probe = _ForkPermanentImportProbe(connection)
        probe._import_fork_permanent_properties()

        self.assertEqual(
            [(1, "AtkUp", 0.16), (2, "AtkUp", 0.20)],
            connection.execute(
                "SELECT refinement_level, property_id, property_value "
                "FROM fork_permanent_property ORDER BY refinement_level"
            ).fetchall(),
        )
        self.assertEqual(
            "resolved_permanent", probe.fork_permanent_property_audit[0]["status"]
        )

    def test_resolves_direct_stats_by_structure_and_full_property_identity(self) -> None:
        connection = _connection()
        self.addCleanup(connection.close)
        fixtures = (
            ("fork_DemonBlade", "CritBase", "buff_DemonBlade_Crit", (0.16, 0.20)),
            ("fork_GoldRecord", "AtkUp", "buff_GoldRecord_AtkUp", (0.24, 0.30)),
            (
                "fork_Butterfly",
                "DamageUpNatureBase",
                "buff_Butterfly_DamageUpNatureBase",
                (0.15, 0.175),
            ),
            (
                "fork_mofeikesi",
                "ChargeGetEfficiencyBase",
                "buff_mofeikesi_ChargeGetEfficiency",
                (0.18, 0.21),
            ),
            (
                "fork_GoldWool",
                "DamageUpLakshanaBase",
                "buff_GoldWool_Up",
                (0.20, 0.25),
            ),
        )
        for fork_id, property_id, parameter_id, values in fixtures:
            _add_fork(
                connection,
                fork_id=fork_id,
                property_id=property_id,
                parameter_id=parameter_id,
                values=values,
            )

        probe = _ForkPermanentImportProbe(connection)
        probe._import_fork_permanent_properties()

        rows = connection.execute(
            "SELECT fork_id, refinement_level, property_id, property_value "
            "FROM fork_permanent_property ORDER BY fork_id, refinement_level"
        ).fetchall()
        self.assertEqual(10, len(rows))
        self.assertIn(("fork_Butterfly", 1, "DamageUpNatureBase", 0.15), rows)
        self.assertIn(("fork_GoldRecord", 2, "AtkUp", 0.30), rows)
        self.assertIn(("fork_GoldWool", 1, "DamageUpLakshanaBase", 0.20), rows)
        self.assertIn(("fork_mofeikesi", 1, "ChargeGetEfficiencyBase", 0.18), rows)
        self.assertTrue(
            all(
                item["status"] == "resolved_permanent"
                for item in probe.fork_permanent_property_audit
            )
        )

    def test_excludes_requirement_and_tag_gated_modifiers(self) -> None:
        connection = _connection()
        self.addCleanup(connection.close)
        _add_fork(
            connection,
            fork_id="fork_PoliceRat",
            property_id="DamageUpGeneralBase",
            parameter_id="buff_PoliceRat_Up",
            requirement="/Game/Condition/Con_IsBoss",
        )
        _add_fork(
            connection,
            fork_id="fork_GoldRecord",
            property_id="CritDamageBase",
            parameter_id="buff_GoldRecord_QteCritDamage",
            source_tags=("State.Damage.QTE",),
        )

        probe = _ForkPermanentImportProbe(connection)
        probe._import_fork_permanent_properties()

        self.assertEqual(
            0,
            connection.execute("SELECT COUNT(*) FROM fork_permanent_property").fetchone()[0],
        )
        self.assertEqual(
            {"conditional_only"},
            {item["status"] for item in probe.fork_permanent_property_audit},
        )

    def test_ambiguous_direct_mapping_is_audited_without_guessing(self) -> None:
        connection = _connection()
        self.addCleanup(connection.close)
        _add_fork(
            connection,
            fork_id="fork_Ambiguous",
            property_id="AtkUp",
            parameter_id="buff_Ambiguous_AtkUp",
        )
        connection.execute(
            """INSERT INTO buff_modifier(
                   asset_path, ordinal, property_id, modifier_operation,
                   calculation_asset_path
               ) VALUES (
                   'buff:fork_Ambiguous', 1, 'AtkUp',
                   'EGameplayModOp::Additive', '/Game/Calculation/Ambiguous/Other'
               )"""
        )

        probe = _ForkPermanentImportProbe(connection)
        probe._import_fork_permanent_properties()

        self.assertEqual(
            0,
            connection.execute("SELECT COUNT(*) FROM fork_permanent_property").fetchone()[0],
        )
        self.assertEqual(
            "ambiguous", probe.fork_permanent_property_audit[0]["status"]
        )


if __name__ == "__main__":
    unittest.main()
