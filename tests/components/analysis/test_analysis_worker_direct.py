# 验证分析工作进程隔离、管道取消与未取整直伤和既有角色消费者的一致性。
from __future__ import annotations

from dataclasses import asdict
from io import BytesIO
import json
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from src.integrations.analysis_core.nte_analysis_core import (
    ENGINE_VERSION, NteAnalysisCoreClient, NativeAnalysisCancelled, NativeAnalysisError,
)
from src.integrations.analysis_core.worker_session import _Worker
from src.services.damage.damage_calculation_service import (
    DamageCalculationService, DamageScalingStat, DamageScene, DirectDamageInput, EnemyDefenseProfileInput,
)
from src.services.official_role.native_damage import RoleDamageCalculator


ROOT = Path(__file__).resolve().parents[3]
EXE = Path(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE") or ROOT / "third_party/analysis-core/bin/nte-analysis-core.exe")


@pytest.fixture
def client():
    value = NteAnalysisCoreClient.from_executable(EXE, "raw-role-differential")
    if not value.supports_analysis_worker or not value.supports_direct_damage:
        pytest.skip("requires the candidate worker/direct-damage component")
    return value


def input_matrix(count=768):
    rng = random.Random(20261113)
    rows = []
    for index in range(count):
        rows.append(DirectDamageInput(
            skill_multiplier=rng.uniform(0, 9), scaling_stat=tuple(DamageScalingStat)[index % 3],
            attack_base=rng.uniform(0, 8000), attack_up=rng.uniform(-.9, 5), attack_add=rng.uniform(-20, 600),
            health_base=rng.uniform(0, 50000), health_up=rng.uniform(-.9, 5), health_add=rng.uniform(-20, 3000),
            defense_base=rng.uniform(0, 2000), defense_up=rng.uniform(-.9, 5), defense_add=rng.uniform(-20, 800),
            character_level=rng.uniform(0, 80), enemy_level=rng.uniform(0, 100),
            crit_rate=rng.uniform(-.2, 1.6), crit_damage=rng.uniform(-.5, 4),
            defense_penetration=rng.uniform(-.1, .8), defense_reduction=rng.uniform(-.1, .8),
            damage_increases=tuple(rng.uniform(-.1, 2) for _ in range(index % 4)),
            vulnerability_increases=(rng.uniform(-.2, .7),), boss_resistance=rng.uniform(-.8, 1.5),
            enemy_resistance_reductions=(rng.uniform(0, .7),), resistance_penetrations=(rng.uniform(0, .7),),
            independent_damage_bonuses=(rng.uniform(-.5, .8), rng.uniform(0, .3)),
            scene=tuple(DamageScene)[index % 2],
            enemy_defense_profile=EnemyDefenseProfileInput(600, .3, 7) if index % 3 else None,
        ))
    return rows


def test_raw_damage_all_fields_matches_reference_and_one_process_owns_many_batches(client):
    rows = input_matrix()
    processes = []
    with client.worker_session():
        actual = client.calculate_direct_batch(rows)
        processes.append(client._active_worker().process)
        again = client.calculate_direct_batch(rows[:8])
        assert again == actual[:8]
    assert all(process.poll() is not None for process in processes)
    assert client.stats["process_launches"] == 1
    assert client.stats["worker_requests"] == 2
    for source, result in zip(rows, actual, strict=True):
        assert asdict(result) == pytest.approx(asdict(DamageCalculationService.calculate_direct(source)), rel=2e-14, abs=1e-9)


def test_dataset_error_closes_scope_and_never_replays(client):
    with client.worker_session():
        client.calculate_direct_batch(input_matrix(1))
        child = client._active_worker().process
        with pytest.raises(NativeAnalysisError):
            client._run(json.dumps({"dataset_version": "other"}).encode())
        assert child.poll() is not None
        with pytest.raises(NativeAnalysisError):
            client.calculate_direct_batch(input_matrix(1))
    assert client.stats["process_launches"] == 1


def test_owner_cancel_kills_and_joins_only_its_child(client):
    def cancelled_after_dispatch():
        active = client._active_worker()
        if active is not None and active.process is not None:
            raise NativeAnalysisCancelled("fixture")
    with client.worker_session():
        with pytest.raises(NativeAnalysisCancelled):
            client.calculate_direct_batch(input_matrix(2), checkpoint=cancelled_after_dispatch)
        child = client._active_worker().process
        assert child is not None and child.poll() is not None
    assert client.stats["process_launches"] == 1


def test_oversize_frame_rejected_before_body_read():
    with pytest.raises(ValueError):
        _Worker._receive(BytesIO(struct.pack("<I", 100000)), 2048)
    with pytest.raises(ValueError):
        _Worker._receive(BytesIO(struct.pack("<I", 8) + b"x"), 2048)


def test_blocked_stdin_obeys_timeout_and_reaps_io_thread(client):
    spawn = subprocess.Popen
    children = []
    def fixture_child(*_args, **_kwargs):
        child = spawn([sys.executable, "-c", "import time; time.sleep(30)"],
                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        children.append(child)
        return child
    client.timeout = .15
    start = time.monotonic()
    with patch("src.integrations.analysis_core.worker_session.subprocess.Popen", fixture_child), client.worker_session():
        with pytest.raises(NativeAnalysisError, match="超时"):
            client._run(b" " * 1000000)
    assert time.monotonic() - start < 5
    assert len(children) == 1 and children[0].poll() is not None


def test_role_cache_uses_value_not_object_address_and_is_bounded(client):
    calculator = RoleDamageCalculator(client)
    rows = input_matrix(4200)
    with client.worker_session():
        first = calculator.calculate(rows[:5])
        repeated = calculator.calculate(rows[:5])
        calculator.calculate(rows)
        assert first == repeated
        assert len(calculator._values) <= 4096
        assert calculator.calculate(rows[:5]) == first


def test_raw_invalid_request_does_not_enter_legacy_replay(client):
    row = asdict(input_matrix(1)[0])
    row["skill_multiplier"] = -1
    payload = {"schema_version": "nte-analysis-request-v1", "dataset_version": client.dataset_version,
               "batch_kind": "direct_damage_v1", "inputs": [row]}
    with pytest.raises(NativeAnalysisError):
        client._run(json.dumps(payload).encode())


def test_old_components_stay_one_shot_and_never_claim_raw_capability(tmp_path):
    exe = tmp_path / "nte-analysis-core.exe"
    exe.touch()
    client = NteAnalysisCoreClient(exe, "fixture", engine_version=ENGINE_VERSION, capabilities=frozenset())
    with client.worker_session():
        assert client._active_worker() is None
    assert not client.supports_direct_damage


def test_role_margins_graduation_breakdown_and_item_gains_share_native_values(client):
    from copy import deepcopy
    from tests.characters.official.test_role_equipment_cards import role_detail
    from src.services.official_role.equipment.marginal_scoring import calculate_official_role_margins
    from src.services.official_role.equipment.graduation import graduation_rate
    from src.services.official_role.panel.panel_explanation import calculate_official_role_damage_breakdown
    from src.services.official_role.equipment.replacement import calculate_official_role_item_gain
    detail = role_detail()
    detail["graduation_template"] = {"profile": detail["profile"],
        "equipment": detail["equipment_contexts"]["saved"]["items"], "drive_area": 20, "extra_shape_count": 0}
    before = deepcopy(detail)
    def evaluate(calculator):
        return (calculate_official_role_margins(detail, "current", calculator=calculator),
                graduation_rate(detail, "current", calculator=calculator),
                calculate_official_role_damage_breakdown(detail, "current", calculator=calculator),
                calculate_official_role_item_gain(detail, "current", detail["equipment_contexts"]["current"]["items"][0], calculator=calculator))
    with client.verified_session():
        actual = evaluate(RoleDamageCalculator(client))
    assert actual == evaluate(RoleDamageCalculator())
    assert detail == before
    assert client.stats["process_launches"] == 1
    assert 1 <= client.stats["worker_requests"] <= 5


def test_long_axis_keeps_every_event_and_readonly_facts(tmp_path, client):
    import hashlib
    from tests.battle_report.analysis.axis_evaluation_catalog_fixture import create_axis_catalog_case
    from src.services.battle_report.analysis.native_page import BattleNativePageService
    from src.services.battle_report.analysis.load import BattleReportAnalysisLoadRequest
    case = create_axis_catalog_case(tmp_path, core_suit_id="Suit1", hit_count=128)
    client.dataset_version = case["dataset_id"]
    service = BattleNativePageService(client=client, dependencies=case["dependencies"],
        semantics_path=ROOT / "config/gameplay_effect_semantics.json", context_is_current=lambda _: True)
    frozen = service.freeze_axis_evaluation(BattleReportAnalysisLoadRequest(case["battle_record_id"]),
        anchor_mode="original", selected_character_id=1072,
        baseline_equipment_by_character=[{"character_id": 1072, "equipment": case["baseline_equipment"]}],
        candidates=[{"candidate_id": "strong", "equipment": case["candidate_equipment"]}])
    before = hashlib.sha256(case["dependencies"].user_database_path.read_bytes()).hexdigest()
    result = service.evaluate_axis(frozen)
    assert result["scope"]["outgoing_event_count"] == 128
    assert result["baseline"]["status"] == "complete"
    assert result["candidates"][0]["candidate_value"] > result["baseline"]["candidate_value"]
    assert hashlib.sha256(case["dependencies"].user_database_path.read_bytes()).hexdigest() == before


def test_runtime_role_rejects_missing_capability_instead_of_python_fallback(client):
    from src.integrations.static_catalog.static_catalog_release import StaticCatalogReleaseReader
    from src.services.official_role.native_damage import role_damage_calculator
    release = StaticCatalogReleaseReader().freeze(ROOT / "data/game_static.sqlite3")
    detail = {"static_release": release, "static_database_path": str(release.database_path)}
    with patch("src.services.official_role.native_damage.create_bundled_analysis_client", return_value=None), \
            patch.object(DamageCalculationService, "calculate_direct", side_effect=AssertionError("unexpected fallback")):
        with pytest.raises(NativeAnalysisError), role_damage_calculator(detail):
            pass
