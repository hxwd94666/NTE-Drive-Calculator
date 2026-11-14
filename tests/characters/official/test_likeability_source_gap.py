# 验证新版好感缺项保留来源、未知不补零，真实观测与账号选择不被改写。
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from tools.game_data.build_static_database import StaticDatabaseBuilder
from tools.game_data.static_database_build_support import DEFAULT_OVERRIDES, StaticDatabaseError


NTE_TEST_TIER = "core"
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def inputs():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE character(character_id INTEGER PRIMARY KEY, actor_path TEXT);
        CREATE TABLE source_row(source_row_id INTEGER PRIMARY KEY, row_key TEXT);
        CREATE TABLE equipment_attribute(attribute_id TEXT PRIMARY KEY);
        INSERT INTO character VALUES (1090,'/Game/Player/player_090_elyms.player_090_elyms_C');
        INSERT INTO source_row VALUES (1,'1090'),(2,'likeatt_1090');
        INSERT INTO equipment_attribute VALUES ('CritBase');
    """)
    db.executescript((ROOT / "src/storage/sqlite/schema/018_game_static_character_likeability.sql").read_text(encoding="utf-8"))
    db.executescript((ROOT / "src/storage/sqlite/schema/043_game_static_likeability_gap.sql").read_text(encoding="utf-8"))
    builder = object.__new__(StaticDatabaseBuilder)
    builder.connection = db
    builder.overrides_path = DEFAULT_OVERRIDES
    builder.rows = {"likeability_roles": {"1090": {
        "SoftActorClass": {"AssetPathName": "/Game/PlayerUIShow/PlayerUIShow_090.PlayerUIShow_090_C"},
        "MapModifyData": [{"Key": "10", "Value": "likeatt_1090"}],
    }}, "likeability_modify": {}}
    builder.source_row_ids = {("likeability_roles", "1090"): 1, ("likeability_modify", "likeatt_1090"): 2}
    yield builder, db
    db.close()


def reader(db, *, schema=40):
    from src.storage.sqlite.catalog.static_game_data_extended_queries import StaticGameDataExtendedQueriesMixin

    class ReadOnlyQueries(StaticGameDataExtendedQueriesMixin):
        schema_version = schema

        def _one(self, sql, parameters=()):
            row = db.execute(sql, parameters).fetchone()
            return dict(row) if row else None

        def _rows(self, sql, parameters=()):
            return [dict(row) for row in db.execute(sql, parameters)]

    return ReadOnlyQueries()


def gap():
    return dict(character_id=1090, required_level=10, modify_data_id="likeatt_1090",
                source_row_id=1, availability="unavailable", reason="missing_modifier_source", properties=None)


def test_importer_keeps_character_and_persists_exact_missing_reference(inputs):
    builder, db = inputs
    builder._import_character_likeability_bonuses()
    row = dict(db.execute("SELECT * FROM character_likeability_gap").fetchone())
    assert row == {key: value for key, value in gap().items() if key not in {"availability", "properties"}}
    assert db.execute("SELECT COUNT(*) FROM character").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM character_likeability_bonus_property").fetchone()[0] == 0
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_query_exposes_unknown_and_native_identity_without_fake_properties(inputs):
    builder, db = inputs
    builder._import_character_likeability_bonuses()
    dao = reader(db)
    assert dao.get_character_likeability_bonus(1090) == gap()
    assert dao.get_character_likeability_identity(1090) == {"likeability_id": "1090", "required_level": 10}
    from src.services.official_role.profile_data.native_profiles import validate_native_cultivation_patch
    observed = {"character_id": 1090, "likeability_levels": {"1090": 10}}
    validate_native_cultivation_patch(observed, dao, set())
    assert observed == {"character_id": 1090, "likeability_level": 10, "likeability_level_10_enabled": True}


def test_legacy_schema_does_not_query_new_gap_table(inputs):
    _, db = inputs
    db.execute("DROP TABLE character_likeability_gap")
    dao = reader(db, schema=39)
    assert dao.get_character_likeability_bonus(1090) is None
    assert dao.get_character_likeability_identity(1090) is None


def test_valid_modifier_remains_exact_and_has_no_gap(inputs):
    builder, db = inputs
    from tools.game_data.static_database_build_support import ADDITIVE_MODIFIER_OPERATION
    builder.rows["likeability_modify"]["likeatt_1090"] = {
        "ConditionArray": [], "ModifyData": [{"PropName": "CritBase", "PropValue": .07,
                                               "ModifierOp": ADDITIVE_MODIFIER_OPERATION}],
    }
    builder._import_character_likeability_bonuses()
    assert reader(db).get_character_likeability_bonus(1090)["properties"][0]["value"] == .07
    assert db.execute("SELECT COUNT(*) FROM character_likeability_gap").fetchone()[0] == 0


@pytest.mark.parametrize("modifier", [None, {}, {"ConditionArray": ["conditional"], "ModifyData": []}])
def test_malformed_present_modifier_still_fails_instead_of_becoming_gap(inputs, modifier):
    builder, _ = inputs
    builder.rows["likeability_modify"]["likeatt_1090"] = modifier
    with pytest.raises(StaticDatabaseError):
        builder._import_character_likeability_bonuses()


def test_enabled_unknown_is_not_zero_but_disabled_unknown_is_known_omission():
    from src.services.official_role.panel.attributes import _likeability_property_stats, _role_panel_damage_inputs
    from src.services.official_role.equipment.marginal_scoring import calculate_official_role_margins
    from src.services.official_role.panel.panel_explanation import calculate_official_role_damage_breakdown
    detail = {"likeability_bonus": gap(), "profile": {"likeability_level_10_enabled": True}}
    with pytest.raises(ValueError, match="好感"):
        _likeability_property_stats(detail)
    assert _role_panel_damage_inputs(detail, "current") == ()
    assert calculate_official_role_margins(detail, "current") is None
    assert calculate_official_role_damage_breakdown(detail, "current") is None
    detail["profile"]["likeability_level_10_enabled"] = False
    assert _likeability_property_stats(detail) == {}


def test_legacy_scoring_blocks_explicit_unknown_without_changing_default_or_saved_choice():
    from src.services.allocation.execution.legacy_allocation_static_catalog import _likeability_crit_rate_bonus
    dao = SimpleNamespace(get_character_likeability_bonus=lambda _identity: gap())
    assert _likeability_crit_rate_bonus(dao, None, 1090) == 0
    assert _likeability_crit_rate_bonus(dao, None, 1090, enabled_override=False) == 0
    account = SimpleNamespace(get_character_profile=lambda _: {"likeability_level_10_enabled": True})
    with pytest.raises(ValueError, match="好感"):
        _likeability_crit_rate_bonus(dao, account, 1090)


def test_known_and_unknown_cannot_both_claim_same_character(inputs):
    builder, db = inputs
    builder._import_character_likeability_bonuses()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO character_likeability_bonus VALUES (1090,10,'likeatt_1090',1,2)")
    assert reader(db).get_character_likeability_bonus(1090)["availability"] == "unavailable"


def test_append_migration_preserves_existing_known_bonus_and_all_properties():
    with sqlite3.connect(":memory:") as db:
        db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE character(character_id INTEGER PRIMARY KEY);
            CREATE TABLE source_row(source_row_id INTEGER PRIMARY KEY);
            CREATE TABLE equipment_attribute(attribute_id TEXT PRIMARY KEY);
            INSERT INTO character VALUES(1003);
            INSERT INTO source_row VALUES(1);
            INSERT INTO equipment_attribute VALUES('CritBase');
        """)
        db.executescript((ROOT / "src/storage/sqlite/schema/018_game_static_character_likeability.sql").read_text(encoding="utf-8"))
        db.execute("INSERT INTO character_likeability_bonus VALUES(1003,10,'likeatt_1003',1,1)")
        db.execute("INSERT INTO character_likeability_bonus_property VALUES(1003,0,'CritBase',.05,'Additive',1)")
        before = db.execute("SELECT * FROM character_likeability_bonus_property").fetchall()
        db.executescript((ROOT / "src/storage/sqlite/schema/043_game_static_likeability_gap.sql").read_text(encoding="utf-8"))
        assert db.execute("SELECT * FROM character_likeability_bonus_property").fetchall() == before
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO character_likeability_gap VALUES(1003,10,'likeatt_1003',1,'missing_modifier_source')")


def test_profile_defaults_disable_unknown_but_preserve_saved_and_observed_enabled():
    from src.services.official_role.panel.profile_projection import resolve_official_role_profile
    character = {"character_id": 1090}
    growth = [{"level": 80, "breakthrough_stage": 6, "hp_base": 1000, "atk_base": 100, "def_base": 100}]

    def resolve(saved=None, observed=None):
        return resolve_official_role_profile(character, growth, [], [], [], saved_profile=saved,
                                             observation=observed, likeability_bonus=gap())

    assert resolve()["likeability_level_10_enabled"] is False
    saved = {"character_id": 1090, "character_level": 80, "breakthrough_stage": 6,
             "likeability_level_10_enabled": True, "awakening_level": 0}
    assert resolve(saved)["likeability_level_10_enabled"] is True
    assert resolve(observed={"likeability_level": 10, "likeability_level_10_enabled": True})["likeability_level_10_enabled"] is True
    assert saved["likeability_level_10_enabled"] is True


def test_graduation_generation_does_not_invent_full_progression_or_close_owner(tmp_path, monkeypatch):
    from tools.game_data import build_graduation_templates as templates
    dao = SimpleNamespace(
        list_equipment_attributes=lambda: [], list_role_template_characters=lambda: [{"character_id": 1090}],
        get_character_default_suit=lambda _identity: None, get_equipment_plan=lambda _identity: None,
        list_equipment_items=lambda: [], list_character_recommended_weights=lambda: [],
        get_character_likeability_bonus=lambda _identity: gap(),
    )
    monkeypatch.setattr(templates, "StaticGameDataBuildDao", lambda *_args, **_kwargs: dao)
    monkeypatch.setattr(templates, "_stats", lambda _path: {})
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE character_graduation_template(character_id INTEGER)")
        count = templates.populate_graduation_templates(db, database_path=tmp_path / 'build.sqlite3', config_dir=tmp_path)
        assert count == 0 and db.execute("SELECT COUNT(*) FROM character_graduation_template").fetchone()[0] == 0
        assert db.execute("SELECT 1").fetchone()[0] == 1
    from src.services.official_role.equipment.graduation import graduation_tooltip
    assert '资料缺项' in graduation_tooltip({'likeability_bonus': gap()})


def test_old_mainline_missing_affinity_evidence_remains_unknown(inputs):
    _, db = inputs
    db.executescript("DROP TRIGGER character_likeability_gap_no_complete; DROP TRIGGER character_likeability_bonus_no_gap; DROP TABLE character_likeability_gap; CREATE TABLE feast_stage_member(stage_id TEXT)")
    bonus = reader(db, schema=42).get_character_likeability_bonus(1090)
    assert bonus["availability"] == "unavailable" and bonus["properties"] is None
    assert bonus["reason"] == "legacy_source_unverified"
    from src.domain.official_role.likeability import enabled_likeability_properties
    with pytest.raises(ValueError, match="好感"):
        enabled_likeability_properties(bonus, enabled=True)
