# 验证战报采集在活动期间分页拉取逐击，并在停止后提交最终 record。
from __future__ import annotations

import threading
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
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
    def __init__(self, *, contract_version: int = 4) -> None:
        self.capture_started = threading.Event()
        self.finalized = False
        self.final_axis_complete = True
        self.handlers: list[Any] = []
        self.capture_params: dict[str, Any] = {}
        self.contract_version = contract_version
        self.axis_requests: list[dict[str, Any]] = []

    def start(self) -> None:
        return None

    def add_event_handler(self, _method: str | None, handler: Any) -> None:
        self.handlers.append(handler)

    def remove_event_handler(self, _method: str | None, handler: Any) -> None:
        self.handlers.remove(handler)

    def start_capture(self, **kwargs: Any) -> Mapping[str, Any]:
        self.capture_params = dict(kwargs)
        self.capture_started.set()
        return {"started": True}

    def stop_capture(self) -> Mapping[str, Any]:
        self.finalized = True
        return {"stopped": True}

    def get_battle_summary(self, **_kwargs: Any) -> Mapping[str, Any] | None:
        return _summary_payload()

    def get_battle_record(self, **_kwargs: Any) -> Mapping[str, Any] | None:
        return {
            "contract_version": self.contract_version,
            "battle_record_id": "battle-1",
            "capture_operation_id": "capture-1",
            "team_snapshot_id": None,
            "generation": "2" if self.finalized else "1",
            "state": "finalized" if self.finalized else "live",
            "source": "capture",
            "started_at_unix": 100.0,
            "ended_at_unix": 101.0 if self.finalized else None,
            "finalized_at_unix_ms": 101000 if self.finalized else None,
            "axis_complete": self.final_axis_complete,
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
        generation = "2" if self.finalized else "1"
        self.axis_requests.append({
            "cursor": cursor,
            "finalized": self.finalized,
        })
        rows = [
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
                "overkill_damage": 0.0,
                "max_hp_reduction": 0.0,
                "follow_up_damage": 0.0,
                "total_damage": 10.0,
                "follow_up_labels": [],
            }
        ]
        return {
            "contract_version": self.contract_version,
            "battle_record_id": "battle-1",
            "generation": generation,
            "finalized": self.finalized,
            "complete": self.final_axis_complete if self.finalized else True,
            "first_available_cursor": "1",
            "cursor": cursor or "1",
            "next_cursor": None,
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
        self.final_pages: list[Mapping[str, Any]] = []
        self.final_generation: str | None = None
        self.final_incomplete_reason: str | None = None

    def begin_capture(self, **_kwargs: Any) -> None:
        self.begun = True

    def append_axis_page(self, *, page: Mapping[str, Any], **_kwargs: Any) -> None:
        self.pages.append(page)

    def replace_axis_pages(
        self,
        *,
        pages: Sequence[Mapping[str, Any]],
        source_generation: str,
        incomplete_reason: str | None = None,
        **_kwargs: Any,
    ) -> None:
        self.final_pages = list(pages)
        self.final_generation = source_generation
        self.final_incomplete_reason = incomplete_reason

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
        self.assertEqual("2", writer.final_generation)
        self.assertEqual("1", writer.final_pages[0]["rows"][0]["sequence"])
        self.assertTrue(any(
            request["cursor"] is None
            and request["finalized"]
            for request in core.axis_requests
        ))
        self.assertEqual("finalized", writer.record["state"])
        self.assertFalse(writer.discarded)
        self.assertEqual("saved", states[-1].persistence_status)
        self.assertEqual(7, states[-1].battle_record_id)
        self.assertEqual("disabled", core.capture_params["raw_capture"])

    def test_capture_enables_raw_packets_in_the_frozen_account_directory(self) -> None:
        core = _Core()
        writer = _Writer()
        with tempfile.TemporaryDirectory() as temp_dir:
            capture_directory = Path(temp_dir) / "account" / "raw_capture"
            service = BattleCaptureService(
                client_factory=lambda: core,
                operation_context=OperationContext.create("battle_report"),
                summary_writer=writer,
                raw_capture_enabled=True,
                raw_capture_directory=capture_directory,
            )

            service.start()
            self.assertTrue(core.capture_started.wait(1.0))
            service.request_stop()
            service.close(timeout=2.0)

            self.assertTrue(capture_directory.is_dir())
            self.assertEqual("enabled", core.capture_params["raw_capture"])

    def test_new_capture_rejects_v3_core_contract(self) -> None:
        core = _Core(contract_version=3)
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

        self.assertEqual("error", states[-1].phase)
        self.assertIn("低于 v4", states[-1].error)
        self.assertTrue(writer.discarded)

    def test_incomplete_final_axis_is_marked_without_final_replacement(self) -> None:
        core = _Core()
        core.finalized = True
        core.final_axis_complete = False
        writer = _Writer()
        service = BattleCaptureService(
            client_factory=lambda: core,
            operation_context=OperationContext.create("battle_report"),
            summary_writer=writer,
        )
        service._source_battle_record_id = "battle-1"

        record = service._read_final_axis(core)

        assert record is not None
        self.assertFalse(record["axis_complete"])
        self.assertEqual([], writer.final_pages)
        self.assertEqual("2", writer.final_generation)
        self.assertEqual("final_axis_incomplete", writer.final_incomplete_reason)

    def test_enabled_raw_capture_requires_an_explicit_account_directory(self) -> None:
        with self.assertRaisesRegex(ValueError, "账号抓包目录"):
            BattleCaptureService(
                client_factory=_Core,
                operation_context=OperationContext.create("battle_report"),
                raw_capture_enabled=True,
            )


if __name__ == "__main__":
    unittest.main()
