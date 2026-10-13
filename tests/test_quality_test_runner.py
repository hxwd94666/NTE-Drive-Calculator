# 验证统一入口不漏混合及嵌套用例，串并行不重复，并传播失败与空选择。
from __future__ import annotations

from pathlib import Path
import subprocess
import sys


NTE_TEST_TIER = "core"
ROOT = Path(__file__).resolve().parents[1]


def run_quality(directory: Path, *arguments: str):
    command = (
        "from pathlib import Path; from tools.quality.run_tests import main; "
        "import sys; raise SystemExit(main(sys.argv[2:], tests_root=Path(sys.argv[1])))"
    )
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-c", command, str(directory), *arguments],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


def test_mixed_nested_cases_execute_once_in_serial_and_parallel(tmp_path):
    domain = tmp_path / "quality_fixture" / "characters"
    domain.mkdir(parents=True)
    (domain / "__init__.py").write_text("# 合成嵌套测试包。\n", encoding="utf-8")
    (domain / "test_mixed_service.py").write_text(
        "import unittest, pytest\nNTE_TEST_TIER='core'\n"
        "class AccountCase(unittest.TestCase):\n"
        "    def test_committed(self): self.assertTrue(True)\n"
        "@pytest.fixture\ndef value(): return 2\n"
        "@pytest.mark.parametrize('expected', [2, 2])\n"
        "def test_projection(value, expected): assert value == expected\n",
        encoding="utf-8",
    )
    (domain / "test_other_service.py").write_text("def test_other(): assert True\n", encoding="utf-8")
    for jobs in (1, 2):
        result = run_quality(domain.parent, "core", "--jobs", str(jobs))
        assert result.returncode == 0, result.stdout + result.stderr
        if jobs == 1:
            assert "4 passed" in result.stdout
        else:
            assert "4 collected cases; 2 shards" in result.stdout
            assert result.stdout.count("3 passed") == 1
            assert result.stdout.count("1 passed") == 1


def test_failure_collection_error_and_empty_selection_are_not_success(tmp_path):
    test = tmp_path / "test_quality_fixture_service.py"
    test.write_text("def test_failed(): assert False\n", encoding="utf-8")
    assert run_quality(tmp_path, "core", "--jobs", "2").returncode == 1
    test.write_text("raise RuntimeError('synthetic collection error')\n", encoding="utf-8")
    failure = run_quality(tmp_path, "core", "--jobs", "2")
    assert failure.returncode != 0 and "synthetic collection error" in failure.stdout
    empty = run_quality(tmp_path, "full", "--pattern", "test_missing.py")
    assert empty.returncode == 5
    test.write_text("value = 1\n", encoding="utf-8")
    assert run_quality(tmp_path, "core", "--jobs", "2").returncode == 5
