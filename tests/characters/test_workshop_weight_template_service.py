# 验证工坊权重模板服务。
"""Public Workshop weight template refresh and resolution contracts."""

from __future__ import annotations

import json
from threading import Event
from time import monotonic
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.services.characters.workshop_weight_template_service import (
    WorkshopWeightRefreshResult,
    WorkshopWeightTemplateService,
    effective_workshop_recommended_weights,
    start_workshop_weight_template_refresh,
)

NTE_TEST_TIER = "core"


def test_public_refresh_omits_api_key_and_atomically_replaces_template() -> None:
    body = json.dumps({
        "code": 200,
        "msg": "success",
        "data": [{
            "itemId": "1003",
            "name": "角色一",
            "weightConfig": {"weights": [
                {"name": "暴击率", "value": 1.25, "main_value": 0.75},
            ]},
        }],
    }).encode("utf-8")
    with TemporaryDirectory() as directory:
        template_file = Path(directory) / "workshop_weight_template.json"
        service = WorkshopWeightTemplateService(template_file)
        with patch(
            "src.integrations.workshop_weight_http.fetch_workshop_payload",
            return_value=body,
        ) as urlopen:
            result = service.refresh(known_character_ids=(1003, 1004))

        headers = urlopen.call_args.kwargs["headers"]
        persisted = json.loads(template_file.read_text(encoding="utf-8"))

    assert result.updated is True
    assert all(key.casefold() != "x-api-key" for key in headers)
    assert persisted["characters"]["1003"]["property_weights"] == {"CritBase": 1.25}
    assert result.character_ids == (1003,)
    assert "1004" not in persisted["characters"]


def test_effective_weights_prefer_runtime_template_and_fall_back_to_static() -> None:
    static = {
        "character_id": 1003,
        "source_kind": "workshop_api",
        "properties": [{"property_id": "CritBase", "weight": 1.0, "main_weight": 1.0}],
        "property_weights": {"CritBase": 1.0},
        "main_property_weights": {"CritBase": 1.0},
    }
    with TemporaryDirectory() as directory:
        template_file = Path(directory) / "workshop_weight_template.json"
        template_file.write_text(json.dumps({
            "schema_version": 1,
            "characters": {
                "1003": {
                    "character_id": 1003,
                    "source_kind": "workshop_runtime",
                    "properties": [{"property_id": "CritBase", "weight": 1.4, "main_weight": 0.6}],
                    "property_weights": {"CritBase": 1.4},
                    "main_property_weights": {"CritBase": 0.6},
                },
            },
        }), encoding="utf-8")

        runtime = effective_workshop_recommended_weights(template_file, 1003, static)
        fallback = effective_workshop_recommended_weights(template_file, 1004, static)

    assert runtime["source_kind"] == "workshop_runtime"
    assert runtime["property_weights"] == {"CritBase": 1.4}
    assert fallback is static


def test_startup_refresh_returns_while_network_work_is_still_pending() -> None:
    started = Event()
    release = Event()

    def slow_refresh(*, known_character_ids, cancel_token=None):
        assert tuple(known_character_ids) == (1003,)
        started.set()
        assert release.wait(timeout=2)
        return WorkshopWeightRefreshResult(True, 1, "digest")

    with TemporaryDirectory() as directory:
        with (
            patch(
                "src.services.characters.workshop_weight_template_service.StaticGameDataDao"
            ) as static_dao_class,
            patch(
                "src.services.characters.workshop_weight_template_service.WorkshopWeightTemplateService.refresh",
                side_effect=slow_refresh,
            ),
        ):
            static_dao_class.return_value.__enter__.return_value.list_role_template_characters.return_value = [
                {"character_id": 1003},
            ]
            began_at = monotonic()
            worker = start_workshop_weight_template_refresh(
                Path(directory) / "workshop_weight_template.json",
                Path(directory) / "game_static.sqlite3",
            )
            elapsed = monotonic() - began_at
            assert elapsed < 0.2
            assert started.wait(timeout=1)
            assert worker.is_alive()
            release.set()
            worker.join(timeout=2)

    assert not worker.is_alive()


