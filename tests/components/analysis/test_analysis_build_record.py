# 验证分析交付拒绝源码变化、旧程序和缺失构建记录，且不构建采集工程。
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.counterfactual import rust_core_build as build


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "analysis-core"
    (root / "src").mkdir(parents=True)
    for name in ("Cargo.toml", "Cargo.lock", "AGENTS.md", "README.md", "THIRD_PARTY_NOTICES.txt"):
        (root / name).write_text("synthetic input", encoding="utf-8")
    (root / "src/main.rs").write_text("fn main() {}", encoding="utf-8")
    (root.parent / "LICENSE").write_text("synthetic license", encoding="utf-8")
    return root


def fake_build(monkeypatch, source, *, during_build=None):
    def run(command, *, cwd, check, **_kwargs):
        assert check is True and Path(cwd) == source
        assert command[command.index("--manifest-path") + 1] == str(source / "Cargo.toml")
        assert command[command.index("--target-dir") + 1] == str(source / "target")
        executable = source / "target/release/nte-analysis-core.exe"
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(b"synthetic compiled binary")
        if during_build is not None:
            during_build()
        return SimpleNamespace(stdout=json.dumps({
            "reason": "compiler-artifact", "target": {"name": "nte-analysis-core"},
            "manifest_path": str(source / "Cargo.toml"), "executable": str(executable),
        }))

    def output(command, **_kwargs):
        if command[:2] == ["rustc", "--version"]:
            return "rustc synthetic\n"
        if command[1] == "status":
            return " M src/main.rs\n"
        return "a" * 40 + "\n"

    monkeypatch.setattr(build.subprocess, "run", run)
    monkeypatch.setattr(build.subprocess, "check_output", output)
    return build.build_analysis_core(source)


def test_explicit_build_binds_binary_to_source_and_original_revision(monkeypatch, source):
    executable = fake_build(monkeypatch, source)
    record = build.verify_build_record(source, executable)
    assert record["source_base_commit"] == "a" * 40
    assert record["source_worktree_modified"] is True
    assert record["sha256"] == build.sha256(executable)
    assert record["source_files"]["src/main.rs"] == build.sha256(source / "src/main.rs")


@pytest.mark.parametrize("change", ["source", "binary", "license", "missing", "record"])
def test_delivery_rejects_stale_or_missing_build_evidence(monkeypatch, source, change):
    executable = fake_build(monkeypatch, source)
    if change == "source":
        (source / "src/main.rs").write_text("fn main() { /* awakening fix */ }", encoding="utf-8")
    elif change == "binary":
        executable.write_bytes(b"older executable")
    elif change == "license":
        (source.parent / "LICENSE").write_text("changed license", encoding="utf-8")
    elif change == "missing":
        build.build_record_path(executable).unlink()
    else:
        build.build_record_path(executable).write_text(json.dumps({"schema": "other"}), encoding="utf-8")
    with pytest.raises(ValueError):
        build.verify_build_record(source, executable)


def test_source_change_during_build_does_not_create_a_valid_record(monkeypatch, source):
    with pytest.raises(RuntimeError, match="构建期间"):
        fake_build(monkeypatch, source, during_build=lambda: (source / "src/main.rs").write_text(
            "fn main() { /* changed during build */ }", encoding="utf-8",
        ))
    assert not build.build_record_path(source / "target/release/nte-analysis-core.exe").exists()
