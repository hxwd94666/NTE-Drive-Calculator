# 验证主角基础权重只归属1046且不改变角色身份、边际权重或历史评分。
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.domain.recommended_weights import base_weight_character_id, workshop_weight_source_ids
from src.optimizer.scoring import ScoringEngine
from src.services.allocation_comparison_scoring import FrozenComparisonScorer
from src.services.character_weight_service import (
    ensure_account_character_weights, reset_account_character_weights,
    resolve_character_base_weights, save_account_character_weights,
)
from src.services.official_role_equipment_scoring_service import score_official_role_equipment
from src.services.official_role_page_service import load_official_role_detail
from src.services.official_role_scoring_service import calculate_official_role_final_weights
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "data/game_static.sqlite3"
PROPERTIES = [
    {"property_id": pid, "weight": weight, "main_weight": main}
    for pid, weight, main in (
        ("DamageUpCosmosBase", .7, .7), ("CritBase", .7, .7),
        ("CritDamageBase", .7, .7), ("AtkUp", .5, .5),
        ("MagBase", 1, 1), ("DamageUpGeneralBase", 0, .5), ("AtkAdd", 0, 0),
    )
]


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.delenv("NTE_WORKSHOP_WEIGHT_TEMPLATE_FILE", raising=False)
    monkeypatch.setenv("NTE_GAME_STATIC_DB", str(STATIC))
    database = tmp_path / "user.sqlite3"
    with UserDataDao(database, account_id="fixture") as dao:
        dao.seed_character_weight_preferences(
            1046, properties=PROPERTIES, source_dataset_id="fixture", source_kind="account",
        )
        legacy = deepcopy(PROPERTIES)
        for row in legacy:
            if row["property_id"] == "AtkAdd":
                row["weight"] = .4
        dao.seed_character_weight_preferences(
            1051, properties=legacy, source_dataset_id="fixture", source_kind="account",
        )
    return database


def test_only_base_weight_identity_is_canonical():
    assert base_weight_character_id(1046) == base_weight_character_id(1051) == 1046
    assert workshop_weight_source_ids(1051) == (1046,)
    assert base_weight_character_id(1003) == 1003


def test_read_ignores_legacy_1051_even_when_it_is_explicit(account):
    with UserDataDao(account) as user, StaticGameDataDao(STATIC) as static:
        before = {cid: user.get_character_weight_preferences(cid) for cid in (1046, 1051)}
        for cid in (1046, 1051):
            record = resolve_character_base_weights(user, static, cid)
            assert record["character_id"] == cid
            assert record["weight_source_character_id"] == 1046
            assert record["property_weights"].get("AtkAdd", 0) == 0
        after = {cid: user.get_character_weight_preferences(cid) for cid in (1046, 1051)}
        assert before == after


def test_female_editor_saves_and_reads_1046_without_touching_1051(account):
    with UserDataDao(account) as user:
        legacy = user.get_character_weight_preferences(1051)
    saved = save_account_character_weights(
        account, 1051, {"CritBase": 0}, main_property_weights={"CritBase": 0},
        static_database_path=STATIC,
    )
    assert saved["character_id"] == 1051
    assert saved["weight_source_character_id"] == 1046
    read = ensure_account_character_weights(
        account, (1046, 1051), static_database_path=STATIC, persist_defaults=False,
    )
    assert read[1046]["property_weights"] == read[1051]["property_weights"] == {}
    assert read[1046]["main_property_weights"] == read[1051]["main_property_weights"] == {}
    with UserDataDao(account) as user:
        assert user.get_character_weight_preferences(1051) == legacy


def test_reset_both_variants_writes_once_and_projects_each_requested_id(account, tmp_path, monkeypatch):
    template = tmp_path / "weights.json"
    template.write_text(json.dumps({"schema_version": 1, "payload_sha256": "fixture", "characters": {
        "1046": {"source_kind": "workshop_runtime", "properties": [
            {"property_id": "CritBase", "weight": .8, "main_weight": .6},
        ], "property_weights": {"CritBase": .8}, "main_property_weights": {"CritBase": .6}},
        "1051": {"property_weights": {"CritBase": 9}},
    }}), encoding="utf-8")
    monkeypatch.setenv("NTE_WORKSHOP_WEIGHT_TEMPLATE_FILE", str(template))
    with UserDataDao(account) as user:
        legacy = user.get_character_weight_preferences(1051)
    original = UserDataDao.reset_character_weight_preferences_to_default
    with patch.object(UserDataDao, "reset_character_weight_preferences_to_default",
                      autospec=True, side_effect=original) as reset:
        restored = reset_account_character_weights(account, (1051, 1046), static_database_path=STATIC)
    assert reset.call_count == 1
    assert reset.call_args.args[1] == 1046
    for cid in (1046, 1051):
        assert restored[cid]["character_id"] == cid
        assert restored[cid]["property_weights"] == {"CritBase": .8}
    with UserDataDao(account) as user:
        assert user.get_character_weight_preferences(1051) == legacy


def test_role_page_and_frozen_comparison_share_base_score_and_keep_actual_id(account):
    engine = ScoringEngine(ROOT / "config", user_database_path=account, static_database_path=STATIC)
    role = engine.roles_db["「零」"]
    old = {"kind": "core", "quality": "gold", "main_stats": [
        {"property_id": "CritBase", "value": 30, "percent": True},
    ], "sub_stats": [{"property_id": pid, "value": 80} for pid in (
        "DefAdd", "HPMaxUp", "DefUp", "AtkAdd",
    )]}
    for cid in (1046, 1051):
        detail = load_official_role_detail(
            account, cid, static_database_path=STATIC, include_inventory_contexts=False,
        )
        assert detail["character"]["character_id"] == cid
        assert detail["weight_source_character_id"] == 1046
        assert score_official_role_equipment(engine, detail=detail, item=old, shape_areas={}) == 35
        scorer = FrozenComparisonScorer.from_engine(
            engine, character_id=cid, weights=role["weights"], main_weights=role["main_weights"],
            attributes=detail["attributes"].values(), dataset_id="fixture",
        )
        assert scorer.score_inventory_item(old, main_value=30) == 35
        assert scorer.metadata()["weight_source_character_id"] == 1046
        assert scorer.metadata()["character_id"] == cid
        new = {"kind": "core", "quality": "blue", "main_stats": [
            {"property_id": "AtkUp", "value": 22.5},
        ], "sub_stats": [{"property_id": pid, "value": 1} for pid in (
            "DamageUpGeneralBase", "CritDamageBase", "AtkUp", "HPMaxAdd",
        )]}
        assert score_official_role_equipment(engine, detail=detail, item=new, shape_areas={}) == 39.83
        assert scorer.score_inventory_item(new, main_value=22.5) == 39.83
    frozen = scorer.metadata()
    save_account_character_weights(account, 1051, {"CritBase": 2}, static_database_path=STATIC)
    assert scorer.metadata() == frozen
    assert scorer.score_inventory_item(old, main_value=30) == 35


def test_marginal_weights_still_follow_panel_gains_not_1046_constants():
    detail = {"character": {"character_id": 1051}, "weight_source_character_id": 1046,
              "property_weights": {"CritBase": .7, "AtkUp": .5, "MagBase": 1},
              "main_property_weights": {"CritBase": .7, "AtkUp": .5}}
    before = deepcopy(detail)
    calculated = calculate_official_role_final_weights(detail, "saved", margins={"rows": [
        {"property_id": "CritBase", "gain_percent": 2},
        {"property_id": "AtkUp", "gain_percent": 4},
    ]})
    assert calculated["property_weights"] == {"CritBase": .5, "AtkUp": 1, "MagBase": 1}
    assert detail == before
