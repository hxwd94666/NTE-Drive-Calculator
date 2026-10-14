# 验证整批角色权重只读一次工坊模板，并保持数值、版本和返回副本一致。
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.optimizer.scoring import ScoringEngine
from src.services.characters import workshop_weight_template_service as workshop
from src.services.characters.character_weight_service import ensure_account_character_weights
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


NTE_TEST_TIER = "core"
STATIC = Path(__file__).resolve().parents[2] / "data/game_static.sqlite3"


@pytest.mark.parametrize("consumer", ["editor", "scoring"])
def test_batch_reads_one_template_and_keeps_matching_revision(tmp_path, monkeypatch, consumer):
    database = tmp_path / "account.sqlite3"
    template = tmp_path / "weights.json"
    with UserDataDao(database, account_id="snapshot-fixture"):
        pass
    def content(revision, weight):
        return {"schema_version": 1, "payload_sha256": revision, "characters": {
            str(cid): {"character_id": cid, "properties": [
                {"property_id": "CritBase", "weight": weight, "main_weight": weight},
            ], "property_weights": {"CritBase": weight}, "main_property_weights": {"CritBase": weight}}
            for cid in (1003, 1046)
        }}
    template.write_text(json.dumps(content("first", 1.25)), encoding="utf-8")
    monkeypatch.setenv(workshop.WORKSHOP_WEIGHT_TEMPLATE_ENV, str(template))
    read = workshop._read_template
    def replaced_after_read(path):
        frozen = read(path)
        # 复用正式刷新提交路径；整个文件原子换版，不借半写 JSON 制造失败。
        workshop.WorkshopWeightTemplateService(template)._write_template(content("second", 1.75))
        return frozen
    with patch.object(workshop, "_read_template", side_effect=replaced_after_read) as reads:
        if consumer == "editor":
            batch = ensure_account_character_weights(
                database, (1003, 1051), static_database_path=STATIC, persist_defaults=False,
            )
            assert all(row["source_dataset_id"] == "workshop-runtime:first" for row in batch.values())
            assert all(row["property_weights"] == {"CritBase": 1.25} for row in batch.values())
        else:
            with StaticGameDataDao(STATIC) as dao:
                characters = [dao.get_character(cid) for cid in (1003, 1051)]
            with patch.object(StaticGameDataDao, "list_role_template_characters", return_value=characters):
                engine = ScoringEngine(
                    STATIC.parents[1] / "config", user_database_path=database, static_database_path=STATIC,
                )
            # 消费角色仍是实际正式 ID；主角只映射权重来源，不混用批中更新后的模板。
            assert {row["character_id"]: row["weights"] for row in engine.roles_db.values()} == {
                1003: {"暴击率%": 1.25}, 1051: {"暴击率%": 1.25},
            }
    assert reads.call_count == 1
    current = workshop.load_workshop_weight_snapshot()
    assert current.revision == "workshop-runtime:second"
    projected = current.resolve(1051, None)
    assert projected["character_id"] == 1051 and projected["weight_source_character_id"] == 1046
    projected["properties"][0]["weight"] = 99
    assert current.resolve(1051, None)["properties"][0]["weight"] == 1.75
    with UserDataDao(database) as dao:
        assert dao.get_character_weight_preferences(1003) is None
