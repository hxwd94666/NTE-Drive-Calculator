# 验证独立分析进程的真实差分、协议拒绝和取消回收。
from __future__ import annotations

from copy import deepcopy
import json
import hashlib
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from src.integrations.nte_analysis_core import (
    ENGINE_VERSION, NativeAnalysisCancelled, NativeAnalysisError, NteAnalysisCoreClient,
)
from src.services.battle_report.replay.direct_numeric import calculate_python_direct_formula
from src.integrations.analysis_core_release import create_bundled_analysis_client


EXE = Path(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE") or (
    Path(__file__).resolve().parents[1] / "third_party/analysis-core/bin/nte-analysis-core.exe"
))


def public_job():
    return {
        "values": [["AtkBase", 1000.0], ["AtkUp", 0.2], ["CritBase", 0.35], ["CritDamageBase", 0.54]],
        "scaling_property_id": "Atk", "scaling_multiplier": 2.0, "multiplier_coefficient": 1.0,
        "character_level": 80.0, "damage_attribute": "nature", "critical_policy": "character",
        "fixed_crit_rate": 0.0, "skill_final_multiplier": 1.0, "dot_final_multiplier": 1.0,
        "state_multiplier": 1.0, "state_multiplier_label": False,
        "target": {"scene": "outer_realm", "enemy_level": 80.0, "enemy_defense_base": 3600.0,
                   "enemy_defense_up": 0.0, "enemy_defense_add": 0.0, "defense_reduction": 0.0,
                   "vulnerability": 0.0, "resistance": 0.2},
        "target_resistance_delta": 0.0,
    }


class ProcessStub:
    def __init__(self):
        self.returncode = None
        self.killed = False
        self.reaped = False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -1

    def communicate(self, input=None, timeout=None):
        self.reaped = True
        return b"{}", b""


class NativeAnalysisLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "nte-analysis-core.exe"
        self.path.touch()

    def test_capture_binary_cannot_be_selected(self):
        with self.assertRaises(ValueError):
            NteAnalysisCoreClient(self.path.with_name("nte-core.exe"), "fixture")

    def test_cancel_before_launch_does_not_spawn(self):
        client = NteAnalysisCoreClient(self.path, "fixture", cancelled=lambda: True)
        with patch("src.integrations.nte_analysis_core.subprocess.Popen") as spawn:
            with self.assertRaises(NativeAnalysisCancelled):
                client.calculate_batch([public_job()])
            spawn.assert_not_called()

    def test_checkpoint_cancel_reaps_only_its_child(self):
        child = ProcessStub()
        checkpoints = []

        def checkpoint():
            checkpoints.append(1)
            if len(checkpoints) == 2:
                raise NativeAnalysisCancelled("cancel")

        client = NteAnalysisCoreClient(self.path, "fixture")
        with patch("src.integrations.nte_analysis_core.subprocess.Popen", return_value=child):
            with self.assertRaises(NativeAnalysisCancelled):
                client.calculate_batch([public_job()], checkpoint=checkpoint)
        self.assertTrue(child.killed)
        self.assertTrue(child.reaped)

    def test_timeout_reaps_child(self):
        child = ProcessStub()
        client = NteAnalysisCoreClient(self.path, "fixture", timeout=1)
        with patch("src.integrations.nte_analysis_core.subprocess.Popen", return_value=child):
            with patch("src.integrations.nte_analysis_core.time.monotonic", side_effect=[0, 2]):
                with self.assertRaisesRegex(NativeAnalysisError, "超时"):
                    client.calculate_batch([public_job()])
        self.assertTrue(child.killed and child.reaped)

    def test_dataset_mismatch_cannot_be_consumed(self):
        response = {"schema_version": "nte-analysis-response-v1", "engine_version": ENGINE_VERSION,
                    "dataset_version": "other", "results": []}
        client = NteAnalysisCoreClient(self.path, "fixture")
        with patch.object(client, "_run", return_value=json.dumps(response).encode()):
            with self.assertRaisesRegex(NativeAnalysisError, "数据集"):
                client.calculate_batch([public_job()])

    def test_request_nan_rejected_before_spawn(self):
        job = public_job()
        job["scaling_multiplier"] = float("nan")
        client = NteAnalysisCoreClient(self.path, "fixture")
        with patch.object(client, "_run") as run:
            with self.assertRaises(ValueError):
                client.calculate_batch([job])
            run.assert_not_called()

    def test_release_locator_separates_capture_and_analysis_paths(self):
        self.path.unlink()
        root = Path(self.directory.name)
        (root / "nte-core.exe").write_bytes(b"capture must stay unchanged")
        binary = root / "third_party/analysis-core/bin/nte-analysis-core.exe"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"public test placeholder")
        manifest = {"engine": "nte-analysis-core", "engine_version": ENGINE_VERSION,
                    "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
        (binary.parent.parent / "component.json").write_text(json.dumps(manifest), encoding="utf-8")
        data = root / "data"
        data.mkdir()
        (data / "manifest.json").write_text(json.dumps({"database": {"dataset_id": "frozen"}}), encoding="utf-8")
        with patch("src.integrations.analysis_core_release.bundled_root", return_value=root):
            client = create_bundled_analysis_client(static_database_path=data / "game_static.sqlite3")
            self.assertEqual(client.executable, binary.resolve())
            self.assertEqual(client.dataset_version, "frozen")
            binary.write_bytes(b"changed")
            with self.assertRaisesRegex(NativeAnalysisError, "哈希"):
                create_bundled_analysis_client(static_database_path=data / "game_static.sqlite3")
        self.assertEqual((root / "nte-core.exe").read_bytes(), b"capture must stay unchanged")

    def test_source_checkout_without_component_keeps_explicit_python_backend(self):
        with patch("src.integrations.analysis_core_release.bundled_root", return_value=Path(self.directory.name)):
            # The root file here has no manifest and is intentionally an incomplete bundle.
            self.path.unlink()
            self.assertIsNone(create_bundled_analysis_client(static_database_path=Path("unused.sqlite3")))


@unittest.skipUnless(EXE.is_file(), "build and package independent analysis-core first")
class NativeAnalysisBinaryTests(unittest.TestCase):
    def setUp(self):
        self.client = NteAnalysisCoreClient.from_executable(EXE, "public-differential-v1")

    def test_binary_identity(self):
        version = self.client.version()
        self.assertIn("direct_candidate_pair_v1", version["capabilities"])

    def test_seeded_direct_formula_differential(self):
        rng = random.Random(20260906)
        jobs = []
        elements = ("Chaos", "Cosmos", "Incantation", "Lakshana", "Nature", "Psyche", "Psychically")
        for index in range(512):
            job = public_job()
            element = elements[index % len(elements)]
            scaling = ("Atk", "HPMax", "Def")[index % 3]
            job["damage_attribute"] = element.lower()
            job["scaling_property_id"] = scaling
            job["values"] = [
                [scaling + "Base", rng.uniform(1, 50000)], [scaling + "Up", rng.uniform(-0.9, 3)],
                [scaling + "Add", rng.uniform(0, 1000)], ["CritBase", rng.uniform(-0.5, 1.5)],
                ["CritDamageBase", rng.uniform(-0.3, 4)], ["DamageUpGeneralBase", rng.uniform(-1, 2)],
                ["DamageUp" + element + "Base", rng.uniform(-0.3, 2)],
                ["DamagePenetrate" + element, rng.uniform(0, 0.8)], ["DefIgnore", rng.uniform(-1, 1.3)],
                ["FinalDamageA", 0.17], ["FinalDamageB", 0.33],
            ]
            job["critical_policy"] = ("character", "fixed", "disabled", "unknown")[index % 4]
            job["fixed_crit_rate"] = rng.uniform(-0.5, 1.5)
            job["state_multiplier"] = 1 + index % 5
            job["skill_final_multiplier"] = rng.uniform(0.8, 3)
            job["dot_final_multiplier"] = rng.uniform(0.8, 2)
            job["multiplier_coefficient"] = rng.uniform(0.1, 2)
            job["scaling_multiplier"] = rng.uniform(0.1, 9)
            job["target"]["scene"] = "open_world" if index % 2 else "outer_realm"
            job["target"]["enemy_defense_base"] = None if index % 3 else rng.uniform(0, 9000)
            job["target"]["resistance"] = rng.uniform(-0.6, 1.5)
            job["target"]["defense_reduction"] = rng.uniform(-1, 1.5)
            job["target_resistance_delta"] = rng.uniform(-0.4, 0.3)
            jobs.append(job)
        actual = self.client.calculate_batch(jobs)
        for index, (job, numeric) in enumerate(zip(jobs, actual, strict=True)):
            expected = calculate_python_direct_formula(job)
            for key in ("raw_non_critical", "non_critical_damage", "critical_damage", "critical_rate", "expected_damage"):
                with self.subTest(job=index, key=key):
                    if expected[key] is None:
                        self.assertIsNone(numeric[key])
                    elif key in {"non_critical_damage", "critical_damage"}:
                        self.assertEqual(expected[key], numeric[key])
                    else:
                        self.assertAlmostEqual(expected[key], numeric[key], delta=max(1e-9, abs(expected[key]) * 1e-12))
            for key, value in expected["factors"].items():
                self.assertAlmostEqual(value, numeric["factors"][key], delta=max(1e-9, abs(value) * 1e-12))

    def test_pairs_use_uncorrected_expectation_and_unknown_stays_unavailable(self):
        original = public_job()
        candidate = deepcopy(original)
        candidate["values"][1][1] = 0.0
        response = self.client.evaluate([{"job_id": "public:1", "original": original, "candidate": candidate}])
        left = calculate_python_direct_formula(original)
        right = calculate_python_direct_formula(candidate)
        self.assertAlmostEqual(response["results"][0]["ratio"], right["expected_damage"] / left["expected_damage"])
        original["critical_policy"] = "unknown"
        response = self.client.evaluate([{"job_id": "public:1", "original": original, "candidate": candidate}])
        self.assertEqual(response["results"][0]["ratio_status"], "unavailable")
        self.assertIsNone(response["results"][0]["ratio"])


if __name__ == "__main__":
    unittest.main()
