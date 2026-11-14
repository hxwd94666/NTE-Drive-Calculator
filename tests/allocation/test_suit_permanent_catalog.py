# 核对运行时套装投影与十二套人工审查金样一致，并拒绝条件和操作污染。
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from src.storage.sqlite.catalog.static_game_data_suit_queries import StaticGameDataSuitQueriesMixin
from tests.game_data.equipment_suit_review_fixture import build_review_source
from tools.game_data.equipment_suit_permanent import project_review


class Reader(StaticGameDataSuitQueriesMixin):
    def __init__(self, connection):
        self.connection = connection
        connection.row_factory = sqlite3.Row

    def _rows(self, sql, args=()):
        return [dict(row) for row in self.connection.execute(sql, args)]


def test_runtime_catalog_matches_all_17_reviewed_properties_without_writing_source(tmp_path):
    source, _, _ = build_review_source(tmp_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as connection:
        actual = {(sid, row["required_count"], row["property_id"]): row["value"]
                  for sid, rows in Reader(connection).list_suit_permanent_properties().items() for row in rows}
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads((root / "tests/fixtures/equipment_suit_review_evidence.json").read_text(encoding="utf8"))
    policy = json.loads((root / "tools/game_data/equipment_suit_permanent_reviews.json").read_text(encoding="utf8"))
    reviews = {(r["suit_id"], r["required_count"]): r for r in policy["reviews"]}
    expected = {}
    for evidence in fixture["effects"]:
        effect = evidence["effect"]
        identity = (effect["suit_id"], effect["required_count"])
        _, properties = project_review(evidence, reviews[identity])
        expected.update({(*identity, row["property_id"]): row["value"] for row in properties})
    assert actual == expected and len(actual) == 17
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("update", [
    "UPDATE buff_modifier SET modifier_operation='EGameplayModOp::Multiplicitive' WHERE property_id='CritBase'",
    "UPDATE buff_modifier SET target_require_tags_json='[\"condition\"]' WHERE property_id='CritBase'",
    "UPDATE buff_definition SET duration_policy='EGameplayEffectDurationType::HasDuration' WHERE asset_path LIKE '%Lakshana2_4'",
    "DELETE FROM equipment_buff_curve_point WHERE curve_id='Lakshana_4_1'",
])
def test_runtime_catalog_does_not_promote_conditional_or_unresolved_crit(tmp_path, update):
    source, _, _ = build_review_source(tmp_path)
    with closing(sqlite3.connect(source)) as connection:
        connection.execute(update)
        connection.commit()
        rows = Reader(connection).list_suit_permanent_properties().get("Suit5", [])
        assert not any(row["property_id"] == "CritBase" for row in rows)
