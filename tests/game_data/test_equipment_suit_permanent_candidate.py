# 验证十二套人工审查的候选录入、证据绑定和常驻/条件隔离，不改正式静态。
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from tests.game_data.equipment_suit_review_fixture import build_review_source

import pytest

from tools.game_data.build_equipment_suit_permanent_candidate import build_candidate, file_hash
from tools.game_data import build_equipment_suit_permanent_candidate as builder
from tools.game_data.equipment_suit_permanent import digest, effect_evidence, project_review

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "data/game_static.sqlite3"
REVIEWS = ROOT / "tools/game_data/equipment_suit_permanent_reviews.json"


@pytest.fixture
def tmp_path():
    # 工具的输出域是 build；夹具在该域建立独立临时目录，不绕过生产路径门禁。
    directory = ROOT / "build" / "test-fixtures"
    directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="suit-case-", dir=directory) as temporary:
        yield Path(temporary)


@pytest.fixture(scope="module")
def reviewed():
    directory = ROOT / "build" / "test-fixtures"
    directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="suit-source-", dir=directory) as temporary:
        database, reviews, policy = build_review_source(Path(temporary))
        before = file_hash(database)
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            entries = {(r["suit_id"], r["required_count"]):
                       (effect_evidence(connection, r["suit_id"], r["required_count"]), r)
                       for r in policy["reviews"]}
        yield policy, entries, database, reviews
        assert file_hash(database) == before


def test_all_24_effects_produce_exact_17_unconditional_properties(reviewed):
    _, entries, _database, _reviews = reviewed
    actual = {}
    classes = {}
    for key, (evidence, policy) in entries.items():
        row, values = project_review(evidence, policy)
        classes[key] = row["classification"]
        for value in values:
            actual[(*key, value["property_id"])] = value["value"]
    expected = {
        ("Suit1", 2, "DamageUpChaosBase"): .1, ("Suit1", 4, "DamagePenetrateChaos"): .12,
        ("Suit2", 2, "DamageUpNatureBase"): .1,
        ("Suit3", 2, "DamageUpPsycheBase"): .1, ("Suit3", 4, "DamageUpGeneralBase"): .18,
        ("Suit4", 2, "DamageUpIncantationBase"): .1,
        ("Suit5", 2, "DamageUpLakshanaBase"): .1, ("Suit5", 4, "CritBase"): .14,
        ("Suit6", 2, "DamageUpCosmosBase"): .1,
        ("Suit7", 2, "DefUp"): .15, ("Suit7", 4, "ShieldEfficiency"): .2,
        ("Suit8", 2, "AtkUp"): .1,
        ("Suit9", 2, "HPMaxUp"): .1, ("Suit9", 4, "HealUp"): .2,
        ("Suit10", 2, "HPMaxUp"): .1, ("Suit11", 2, "ChargeGetEfficiencyBase"): .12,
        ("Suit12", 2, "DamageUpPsychicallyBase"): .1,
    }
    assert actual == expected and len(entries) == 24
    assert list(classes.values()).count("permanent_only") == 14
    assert list(classes.values()).count("mixed") == 3
    assert list(classes.values()).count("conditional_only") == 7


def test_candidate_reopens_with_foreign_keys_provenance_and_explicit_nonrelease_status(tmp_path, reviewed):
    output = tmp_path / "suits.sqlite3"
    result = build_candidate(reviewed[2], reviewed[3], output)
    assert result["release_ready"] is False and result["runtime_integration"] == "pending"
    with closing(sqlite3.connect(output.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM equipment_suit_effect_review").fetchone()[0] == 24
        assert connection.execute("SELECT COUNT(*) FROM equipment_suit_permanent_property").fetchone()[0] == 17
        assert connection.execute("SELECT COUNT(*) FROM equipment_suit_effect_review WHERE has_conditional=1").fetchone()[0] == 10
        for evidence, expected in connection.execute("SELECT evidence_json,evidence_sha256 FROM equipment_suit_effect_review"):
            assert digest(json.loads(evidence)) == expected
        metadata = {key: json.loads(value) for key, value in connection.execute("SELECT * FROM candidate_metadata")}
        assert metadata["source_database_sha256"] == file_hash(reviewed[2])
        assert metadata["artifact_kind"] != "game"


@pytest.mark.parametrize("change", ["source", "duplicate", "missing", "format"])
def test_changed_identity_or_incomplete_review_never_creates_output(tmp_path, reviewed, change):
    policy = deepcopy(reviewed[0])
    if change == "source":
        policy["source_database_sha256"] = "0" * 64
    elif change == "duplicate":
        policy["reviews"].append(deepcopy(policy["reviews"][0]))
    elif change == "missing":
        policy["reviews"].pop()
    else:
        policy["format"] = "other"
    path = tmp_path / "reviews.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    output = tmp_path / "candidate.sqlite3"
    with pytest.raises(ValueError):
        build_candidate(reviewed[2], path, output)
    assert not output.exists()


def test_existing_output_and_formal_input_are_never_overwritten(tmp_path):
    output = tmp_path / "existing.sqlite3"
    output.write_bytes(b"existing")
    for path in [output, DATABASE]:
        before = path.read_bytes()
        with pytest.raises(ValueError):
            build_candidate(DATABASE, REVIEWS, path)
        assert path.read_bytes() == before


def test_review_file_changing_during_projection_is_not_bound_to_wrong_hash(tmp_path, reviewed, monkeypatch):
    path = tmp_path / "reviews.json"
    path.write_text(json.dumps(reviewed[0]), encoding="utf-8")
    original = builder.effect_evidence

    def changed(*args):
        value = original(*args)
        path.write_text(path.read_text("utf-8") + " ", encoding="utf-8")
        return value

    monkeypatch.setattr(builder, "effect_evidence", changed)
    output = tmp_path / "candidate.sqlite3"
    with pytest.raises(ValueError, match="输入变化"):
        build_candidate(reviewed[2], path, output)
    assert not output.exists()


@pytest.mark.parametrize("change", ["duration", "requirement", "tags", "missing_curve", "varying_curve", "operation"])
def test_conditional_or_incomplete_evidence_stays_rejected_even_after_resigning(reviewed, change):
    evidence, policy = deepcopy(reviewed[1][("Suit5", 4)])
    root = evidence["links"][0]["target_asset_path"]
    modifier = next(row for row in evidence["modifiers"] if row["asset_path"] == root)
    curve = next(iter(evidence["curves"].values()))
    if change == "duration":
        next(row for row in evidence["buffs"] if row["asset_path"] == root)["duration_policy"] = "HasDuration"
    elif change == "requirement":
        modifier["application_requirement_asset_path"] = "/Game/Condition"
    elif change == "tags":
        modifier["source_require_tags_json"] = '["RequiresTrigger"]'
    elif change == "missing_curve":
        curve["points"] = []
    elif change == "varying_curve":
        curve["points"].append({**curve["points"][0], "value": .99})
    else:
        modifier["modifier_operation"] = "Override"
    policy["evidence_sha256"] = digest(evidence)
    with pytest.raises(ValueError):
        project_review(evidence, policy)


def test_child_buff_needs_initial_unconditional_link_and_old_evidence_digest(reviewed):
    evidence, policy = deepcopy(reviewed[1][("Suit1", 4)])
    evidence["triggers"] = []
    with pytest.raises(ValueError, match="来源已经变化"):
        project_review(evidence, policy)
    policy["evidence_sha256"] = digest(evidence)
    with pytest.raises(ValueError, match="开始关联"):
        project_review(evidence, policy)
