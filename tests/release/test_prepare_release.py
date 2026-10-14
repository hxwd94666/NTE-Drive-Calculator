# 验证正式发行入口的完整测试门禁、失败阻断与显式调试跳过，不执行外部副作用。
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools.release import prepare_release as release


@pytest.fixture
def isolated_release_main(tmp_path, monkeypatch):
    events = []
    commands = []
    # 仅供真实 main 的文件存在判断，不是可执行安装包，也不会送进任何进程。
    installer = tmp_path / "synthetic-installer.exe"
    installer.write_bytes(b"synthetic release marker")
    monkeypatch.setattr(release, "INSTALLER_PATH", installer)

    def record(name, result=None):
        def callback(*_args, **_kwargs):
            events.append(name)
            return result
        return callback

    # main 仍实际解析参数和派发命令；组件握手、数据库、Git、构建与写入均在端口阻断。
    monkeypatch.setattr(release, "ensure_clean_worktree", record("worktree"))
    monkeypatch.setattr(release, "validate_static_database", record("static", {"fixture": True}))
    monkeypatch.setattr(release, "validate_static_manifest", record("manifest"))
    monkeypatch.setattr(release, "validate_static_dataset_against_local_config", record("dataset"))
    monkeypatch.setattr(release, "validate_components", record("components"))
    monkeypatch.setattr(release, "validate_packaged_component_bundle", record("packaged-components"))
    monkeypatch.setattr(release, "validate_packaged_release_artifacts", record("packaged-artifacts"))
    monkeypatch.setattr(release, "write_checksum", record("checksum", tmp_path / "synthetic.sha256"))
    monkeypatch.setattr(release, "print_manual_commands", record("manual"))
    state = SimpleNamespace(fail_tests=False)

    def run(command):
        frozen = list(command)
        commands.append(frozen)
        if any(str(arg).endswith("run_tests.py") for arg in frozen):
            events.append("tests")
            if state.fail_tests:
                raise subprocess.CalledProcessError(1, frozen)
        elif "compileall" in frozen:
            events.append("compile")
        else:
            events.append("build")

    monkeypatch.setattr(release, "run", run)
    # 即使意外漏接 run 端口，也不允许测试执行真实进程或握手。
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("unexpected external process"))
    monkeypatch.setattr(release, "NteCoreClient", lambda *_a, **_k: pytest.fail("unexpected Core handshake"))
    args = ["--tag", release.__version__, "--local-config", str(tmp_path / "local.paths.json")]
    return args, commands, events, state


def test_default_release_gate_uses_complete_official_runner(isolated_release_main):
    args, commands, events, _state = isolated_release_main
    assert release.main(args) == 0
    assert commands[0] == [
        sys.executable, "-X", "utf8", str(release.ROOT / "tools/quality/run_tests.py"),
        "full", "--jobs", "1",
    ]
    assert events.index("tests") < events.index("compile") < events.index("build")
    assert events[-3:] == ["packaged-artifacts", "checksum", "manual"]


def test_failed_test_gate_blocks_compile_build_and_artifact_claims(isolated_release_main, capsys):
    args, commands, events, state = isolated_release_main
    state.fail_tests = True
    assert release.main(args) == 1
    assert len(commands) == 1
    assert events[-1] == "tests"
    assert not {"compile", "build", "packaged-components", "packaged-artifacts", "checksum", "manual"}.intersection(events)
    captured = capsys.readouterr()
    assert "[失败]" in captured.err
    assert "[通过] 安装包" not in captured.out


def test_explicit_skip_is_visible_debugging_not_test_pass(isolated_release_main, capsys):
    args, commands, events, _state = isolated_release_main
    assert release.main([*args, "--skip-tests"]) == 0
    assert "tests" not in events
    assert not any(str(arg).endswith("run_tests.py") for command in commands for arg in command)
    output = capsys.readouterr().out
    assert "跳过测试" in output and "本地调试" in output
    assert "不构成发布测试通过证据" in output
