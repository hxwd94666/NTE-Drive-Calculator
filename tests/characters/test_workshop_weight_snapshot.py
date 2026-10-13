# 验证整批角色权重只读一次工坊模板，并保持数值、版本和返回副本一致。
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from src.services.characters import workshop_weight_template_service as workshop
from src.services.characters.character_weight_service import ensure_account_character_weights
from src.storage.sqlite.user_data_dao import UserDataDao


NTE_TEST_TIER = "core"
STATIC = Path(__file__).resolve().parents[2] / "data/game_static.sqlite3"


def test_batch_reads_one_template_and_keeps_matching_revision(tmp_path, monkeypatch):
    database = tmp_path / "account.sqlite3"
    template = tmp_path / "weights.json"
    with UserDataDao(database, account_id="snapshot-fixture"):
        pass
    def content(revision, weight):
        return {"schema_version": 1, "payload_sha256": revision, "characters": {
            str(cid): {"character_id": cid, "properties": [
                {"property_id": "CritBase", "weight": weight, "main_weight": weight},
            ]} for cid in (1003, 1046)
        }}
    template.write_text(json.dumps(content("first", 1.25)), encoding="utf-8")
    monkeypatch.setenv(workshop.WORKSHOP_WEIGHT_TEMPLATE_ENV, str(template))
    read = workshop._read_template
    def replaced_after_read(path):
        frozen = read(path)
        template.write_text(json.dumps(content("second", 1.75)), encoding="utf-8")
        return frozen
    with patch.object(workshop, "_read_template", side_effect=replaced_after_read) as reads:
        batch = ensure_account_character_weights(
            database, (1003, 1051), static_database_path=STATIC, persist_defaults=False,
        )
    assert reads.call_count == 1
    assert all(row["source_dataset_id"] == "workshop-runtime:first" for row in batch.values())
    assert all(row["property_weights"] == {"CritBase": 1.25} for row in batch.values())
    current = workshop.load_workshop_weight_snapshot()
    assert current.revision == "workshop-runtime:second"
    projected = current.resolve(1051, None)
    assert projected["character_id"] == 1051 and projected["weight_source_character_id"] == 1046
    projected["properties"][0]["weight"] = 99
    assert current.resolve(1051, None)["properties"][0]["weight"] == 1.75
    with UserDataDao(database) as dao:
        assert dao.get_character_weight_preferences(1003) is None
