# 验证跨源码审计保留零覆盖边界、聚合语义差异并识别异常退出。
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.battle_report.compare_frozen_analysis_sources import _run_case, _save, _source_digest


class FrozenAnalysisSourceValidationTests(unittest.TestCase):
    def test_zero_coverage_is_equal_but_global_zero_jobs_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(output=Path(directory))
            report = {"cases": [{
                "record_id": 1, "mode": "original", "status": "no_native_coverage",
                "results": {"candidate": {
                    "native_stats": {"jobs": 0}, "comparisons": {"baseline": {
                        "equal": True, "compared_float_count": 3,
                        "maximum_absolute_float_difference": 0.0,
                    }},
                }},
            }]}
            _save(args, report)
            self.assertEqual(report["summary"]["equal_cases"], 1)
            self.assertEqual(report["summary"]["error_cases"], 0)
            self.assertEqual(report["validation_exit_code"], 1)
            report["cases"].append({
                "record_id": 2, "mode": "original", "status": "passed",
                "results": {"candidate": {"native_stats": {"jobs": 10, "batch_calls": 1}}},
            })
            _save(args, report)
            self.assertEqual(report["summary"]["no_native_coverage_cases"], 1)
            self.assertEqual(report["validation_exit_code"], 0)
            report["cases"].append({"record_id": 3, "mode": "effective", "status": "mismatch", "results": {}})
            _save(args, report)
            self.assertEqual(report["validation_exit_code"], 1)

    def test_nonzero_worker_exit_cannot_be_reported_passed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resultdir = root / "baseline/history-1-original"
            resultdir.mkdir(parents=True)
            (resultdir / "run-1.json").write_text(json.dumps({"status": "passed"}), encoding="utf8")
            args = SimpleNamespace(output=root, baseline_source=root, candidate_source=root,
                                   user_database=root / "user.db", static_database=root / "static.db",
                                   executable=root / "engine.exe", baseline_backend="python", baseline_executable=None)
            with patch("tools.battle_report.compare_frozen_analysis_sources.subprocess.run",
                       return_value=SimpleNamespace(returncode=7)) as run:
                result = _run_case(args, {"record_id": 1}, "original")
            self.assertEqual(result["status"], "error")
            self.assertEqual(run.call_count, 1)

    def test_native_baseline_and_candidate_use_their_own_executables(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for label in ("baseline", "candidate"):
                resultdir = root / label / "history-1-original"
                resultdir.mkdir(parents=True)
                (resultdir / "run-1.json").write_text(json.dumps({"status": "passed", "native_stats": {"jobs": 2}}), encoding="utf8")
            args = SimpleNamespace(output=root, baseline_source=root, candidate_source=root,
                                   user_database=root / "user.db", static_database=root / "static.db",
                                   executable=root / "new-engine.exe", baseline_backend="native",
                                   baseline_executable=root / "old-engine.exe")
            with patch("tools.battle_report.compare_frozen_analysis_sources.subprocess.run",
                       return_value=SimpleNamespace(returncode=0)) as run:
                result = _run_case(args, {"record_id": 1}, "original")
            self.assertEqual(result["status"], "passed")
            old_command, new_command = [call.args[0] for call in run.call_args_list]
            self.assertEqual(old_command[old_command.index("--executable") + 1], str(args.baseline_executable))
            self.assertEqual(new_command[new_command.index("--executable") + 1], str(args.executable))
            self.assertNotIn("--reference-dir", old_command)
            self.assertIn("--reference-dir", new_command)

    def test_source_digest_tracks_python_source_and_ignores_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src/__pycache__").mkdir(parents=True)
            source = root / "src/example.py"
            source.write_text("value = 1", encoding="utf8")
            first = _source_digest(root)
            (root / "src/__pycache__/ignored.py").write_text("cache", encoding="utf8")
            self.assertEqual(_source_digest(root), first)
            source.write_text("value = 2", encoding="utf8")
            self.assertNotEqual(_source_digest(root), first)

    def test_projection_coverage_is_separate_from_numeric_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(output=Path(directory))
            report = {"require_projection": True, "cases": [{
                "record_id": 1, "mode": "original", "status": "passed",
                "results": {"candidate": {"native_stats": {"jobs": 10, "batch_calls": 2}}},
            }]}
            _save(args, report)
            self.assertEqual(report["validation_exit_code"], 1)
            report["cases"].append({
                "record_id": 2, "mode": "original", "status": "passed",
                "results": {"candidate": {"native_stats": {"projection_jobs": 7, "projection_batch_calls": 1}}},
            })
            _save(args, report)
            self.assertEqual(report["validation_exit_code"], 0)
            self.assertEqual(report["summary"]["native_jobs"], 10)
            self.assertEqual(report["summary"]["native_projection_jobs"], 7)
            self.assertEqual(report["summary"]["native_numeric_covered_cases"], 1)
            self.assertEqual(report["summary"]["native_projection_covered_cases"], 1)
            self.assertEqual(report["summary"]["no_numeric_native_coverage_cases"], 1)
            report["cases"] = report["cases"][1:]
            _save(args, report)
            self.assertEqual(report["validation_exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
