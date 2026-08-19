# 验证战报采集在活动期间分页拉取逐击，并在停止后提交最终 record。
from __future__ import annotations

import threading
import unittest
from collections.abc import Mapping
from typing import Any

from src.domain.battle_report import BattleSummaryPersistenceOutcome
from src.observability import OperationContext
from src.services.battle_capture_service import BattleCaptureService


def _summary_payload() -> dict[str, Any]:
    return {
        "duration_seconds": 1.0,
        "dps_time_mode": "subtract_time_stop",
        "total_damage": 10.0,
        "total_dps": 10.0,
        "total_damage_taken": 0.0,
        "total_hits": 1,
        "characters": [
            {
                "char_id": 1072,
                "name": "灵可",
                "hits": 1,
                "damage": 10.0,
                "dps": 10.0,
                "damage_share_percent": 100.0,
            }
        ],
        "skills": [],
        "abyss": {"detected": False},
        "quality": {},
    }


class _Core:
    def __init__(self) -> None:
        self.capture_started = threading.Event()
        self.finalized = False
        self.handlers: list[Any] = []

    def start(self) -> None:
        return None

    def add_event_handler(self, _method: str | None, handler: Any) -> None:
        self.handlers.append(handler)

    def remove_event_handler(self, _method: str | None, handler: Any) -> None:
        self.handlers.remove(handler)

    def start_capture(self, **_kwargs: Any) -> Mapping[str, Any]:
        self.capture_started.set()
        return {"started": True}

    def stop_capture(self) -> Mapping[str, Any]:
        self.finalized = True
        return {"stopped": True}

    def get_battle_summary(self, **_kwargs: Any) -> Mapping[str, Any] | None:
        return _summary_payload()

    def get_battle_record(self, **_kwargs: Any) -> Mapping[str, Any] | None:
        return {
            "contract_version": 1,
            "battle_record_id": "battle-1",
            "capture_operation_id": "capture-1",
            "team_snapshot_id": None,
            "generation": "2" if self.finalized else "1",
            "state": "finalized" if self.finalized else "live",
            "source": "capture",
            "started_at_unix": 100.0,
            "ended_at_unix": 101.0 if self.finalized else None,
            "finalized_at_unix_ms": 101000 if self.finalized else None,
            "axis_complete": True,
            "axis_first_sequence": "1",
            "axis_total_hits": "1",
            "time_stop_intervals": [],
            "abyss": {},
            "summary": _summary_payload(),
            "quality": {},
        }

    def get_battle_axis(
        self,
        *,
        battle_record_id: str,
        cursor: str | None = None,
        limit: int = 500,
    ) -> Mapping[str, Any] | None:
        assert battle_record_id == "battle-1" and limit == 500
        rows = [] if cursor == "2" else [
            {
                "battle_record_id": "battle-1",
                "sequence": "1",
                "timestamp_unix": 100.5,
                "relative_time_seconds": 0.5,
                "character_id": 1072,
                "character_name": "灵可",
                "character_known": True,
                "direction": "outgoing",
                "damage": 10.0,
                "follow_up_damage": 0.0,
                "total_damage": 10.0,
                "follow_up_labels": [],
            }
        ]
        return {
            "contract_version": 1,
            "battle_record_id": "battle-1",
            "generation": "2" if self.finalized else "1",
            "finalized": self.finalized,
            "complete": True,
            "first_available_cursor": "1",
            "cursor": cursor or "1",
            "next_cursor": "2",
            "total_hits": "1",
            "retained_hits": 1,
            "rows": rows,
        }

    def close(self) -> None:
        return None


class _Writer:
    def __init__(self) -> None:
        self.begun = False
        self.pages: list[Mapping[str, Any]] = []
        self.record: Mapping[str, Any] | None = None
        self.discarded = False

    def begin_capture(self, **_kwargs: Any) -> None:
        self.begun = True

    def append_axis_page(self, *, page: Mapping[str, Any], **_kwargs: Any) -> None:
        self.pages.append(page)

    def discard_capture(self, **_kwargs: Any) -> None:
        self.discarded = True

    def finalize_summary(
        self,
        *,
        raw_record_payload: Mapping[str, Any] | None = None,
        **_kwargs: Any,
    ) -> BattleSummaryPersistenceOutcome:
        self.record = raw_record_payload
        return BattleSummaryPersistenceOutcome(
            status="saved",
            battle_record_id=7,
            retention_kind="auto",
        )


class BattleCaptureAxisServiceTests(unittest.TestCase):
    def test_capture_polls_axis_and_finalizes_with_record(self) -> None:
        core = _Core()
        writer = _Writer()
        states = []
        service = BattleCaptureService(
            client_factory=lambda: core,
            operation_context=OperationContext.create("battle_report"),
            summary_writer=writer,
        )
        service.add_state_handler(states.append)

        service.start()
        self.assertTrue(core.capture_started.wait(1.0))
        service.request_stop()
        service.close(timeout=2.0)

        self.assertTrue(writer.begun)
        self.assertEqual("1", writer.pages[0]["rows"][0]["sequence"])
        self.assertEqual("finalized", writer.record["state"])
        self.assertFalse(writer.discarded)
        self.assertEqual("saved", states[-1].persistence_status)
        self.assertEqual(7, states[-1].battle_record_id)


if __name__ == "__main__":
    unittest.main()
