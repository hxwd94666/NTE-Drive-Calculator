# 验证统一入口不漏混合及嵌套用例，串并行不重复，并传播失败与空选择。
from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
import tempfile

import pytest


NTE_TEST_TIER = "core"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_collection_root(tmp_path):
    # cwd 在仓库而夹具可能在另一磁盘；自有配置避免 pytest 向上扫描整个 Temp，
    # 否则其它 shard 正常删除其目录时会污染本例的收集生命周期。
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")


def run_quality(directory: Path, *arguments: str):
    command = (
        "from pathlib import Path; from tools.quality.run_tests import main; "
        "import sys; raise SystemExit(main(sys.argv[2:], tests_root=Path(sys.argv[1])))"
    )
    # 子 pytest 不继承父用例的显式 basetemp，避免子进程清理父进程仍在使用的夹具。
    with tempfile.TemporaryDirectory(prefix="nte-quality-child-", dir=directory.parent) as temporary:
        environment = os.environ | {"PYTEST_ADDOPTS": "", "PYTEST_DEBUG_TEMPROOT": temporary}
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-c", command, str(directory), *arguments],
            cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60,
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
        "def test_projection(value, expected, pytestconfig):\n"
        "    assert value == expected\n"
        f"    assert str(pytestconfig.rootpath) == {str(tmp_path)!r}\n",
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


def test_large_python_selection_uses_bounded_pytest_directory_arguments(tmp_path, monkeypatch):
    from tools.quality import run_tests
    files = [tmp_path / f"test_case_{index}_service.py" for index in range(200)]
    for path in files:
        path.write_text("def test_ok(): pass\n", encoding="utf-8")
    calls = []
    def collect(arguments, *, plugins):
        calls.append(arguments)
        return 5
    monkeypatch.setattr(run_tests.pytest, "main", collect)
    assert run_tests.main(["core", "--collect-only", "-j", "1"], tests_root=tmp_path) == 5
    # Windows 对每个显式文件参数匹配子节点时会反复查询文件 ID；文件数不应放大 CLI 目标数。
    assert len([value for value in calls[0] if str(value).endswith(".py")]) <= 1
    assert str(tmp_path) in calls[0]


def test_frozen_files_include_legacy_names_hidden_build_and_ignore_late_files(tmp_path):
    root = tmp_path / "cases"
    root.mkdir()
    for relative in ("testLegacy.py", "build/test_nested.py", ".hidden/test_hidden.py"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_kept(): assert True\n", encoding="utf-8")
    (root / "conftest.py").write_text(
        "from pathlib import Path\ncollect_ignore=['testLegacy.py']\n"
        "Path(__file__).with_name('test_late.py').write_text('def test_late(): assert False\\n')\n",
        encoding="utf-8",
    )
    collected = run_quality(root, "full", "--collect-only", "-j", "1")
    assert collected.returncode == 0, collected.stdout + collected.stderr
    assert "3 tests collected" in collected.stdout and "test_late.py::" not in collected.stdout
    (root / "test_late.py").unlink()
    result = run_quality(root, "full", "-j", "1")
    assert result.returncode == 0 and "3 passed" in result.stdout, result.stdout + result.stderr


def test_deleted_frozen_file_is_error_not_silent_empty_success(tmp_path):
    target = tmp_path / "test_deleted_service.py"
    target.write_text("def test_kept(): assert True\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        "from pathlib import Path\nPath(__file__).with_name('test_deleted_service.py').unlink(missing_ok=True)\n",
        encoding="utf-8",
    )
    result = run_quality(tmp_path, "core", "--collect-only", "-j", "1")
    assert result.returncode == 4 and target.name in result.stdout + result.stderr


def test_collection_error_in_noncore_file_still_prevents_shards(tmp_path):
    (tmp_path / "test_core_service.py").write_text("def test_kept(): assert True\n", encoding="utf-8")
    (tmp_path / "test_optional_ui.py").write_text("raise RuntimeError('noncore import failed')\n", encoding="utf-8")
    result = run_quality(tmp_path, "core", "-j", "2")
    assert result.returncode != 0 and "noncore import failed" in result.stdout
    assert "[core shard" not in result.stdout
