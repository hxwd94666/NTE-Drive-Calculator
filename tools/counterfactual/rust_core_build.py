# 将独立分析组件的构建输入与实际产物绑定，阻止旧程序冒用当前源码摘要。
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


BUILD_RECORD_SCHEMA = "nte-analysis-build-v1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_inputs(source: Path) -> dict[str, str]:
    """构建、打包和发布来源摘要使用同一显式输入集合，不随意补读其他工程。"""
    files = [source / name for name in (
        "Cargo.toml", "Cargo.lock", "AGENTS.md", "README.md", "THIRD_PARTY_NOTICES.txt",
    )]
    for directory in ("src", "tests"):
        files.extend((source / directory).rglob("*.rs"))
    for directory in ("data", "resources", "tests/fixtures"):
        files.extend(path for path in (source / directory).rglob("*")
                     if path.is_file() and path.suffix in {".json", ".sql"})
    if (source / "build.rs").is_file():
        files.append(source / "build.rs")
    if not all(path.is_file() for path in files) or not (source / "src/main.rs").is_file():
        raise ValueError("分析构建输入不完整")
    return {path.relative_to(source).as_posix(): sha256(path) for path in sorted(set(files))}


def inputs_digest(inputs: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def build_record_path(executable: Path) -> Path:
    return executable.with_suffix(executable.suffix + ".build.json")


def build_analysis_core(source: Path) -> Path:
    """仅显式 --build 调用独立分析工程，不构建根采集 crate。"""
    source = source.resolve()
    if source.name != "analysis-core":
        raise ValueError("必须使用独立 analysis-core 工程")
    before = source_inputs(source)
    license_hash = sha256(source.parent / "LICENSE")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    dirty = bool(subprocess.check_output(
        ["git", "status", "--porcelain", "--", ".", "../LICENSE"], cwd=source, text=True,
    ).strip())
    rustc = subprocess.check_output(["rustc", "--version"], cwd=source, text=True).strip()
    target = source / "target"
    completed = subprocess.run([
        "cargo", "build", "--locked", "--release", "--bin", "nte-analysis-core",
        "--manifest-path", str(source / "Cargo.toml"), "--target-dir", str(target),
        "--message-format=json-render-diagnostics",
    ], cwd=source, check=True, capture_output=True, text=True, encoding="utf-8")
    if source_inputs(source) != before or sha256(source.parent / "LICENSE") != license_hash:
        raise RuntimeError("构建期间分析源码或许可发生变化，请重新构建")
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip() != revision:
        raise RuntimeError("构建期间源码提交发生变化，请重新构建")
    artifacts = []
    for line in completed.stdout.splitlines():
        message = json.loads(line)
        if (message.get("reason") == "compiler-artifact" and message.get("executable")
                and message.get("target", {}).get("name") == "nte-analysis-core"
                and Path(message.get("manifest_path", "")).resolve() == source / "Cargo.toml"):
            artifacts.append(Path(message["executable"]).resolve())
    if len(artifacts) != 1 or artifacts[0].suffix.lower() != ".exe" or not artifacts[0].is_relative_to(target):
        raise RuntimeError("未取得本次独立构建的 Windows EXE，拒绝复用其他目录中的旧程序")
    executable = artifacts[0]
    record = {
        "schema": BUILD_RECORD_SCHEMA, "sha256": sha256(executable),
        "source_files": before, "source_input_sha256": inputs_digest(before),
        "source_base_commit": revision, "source_worktree_modified": dirty,
        "license_sha256": license_hash, "rustc": rustc,
    }
    # 本收据只证明来源，不是备份；不得加入执行物交付包。
    build_record_path(executable).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    return executable


def verify_build_record(source: Path, executable: Path) -> dict:
    """执行或复制候选前核对来源、产物和许可；缺失或旧收据直接拒绝。"""
    path = build_record_path(executable)
    if not path.is_file():
        raise ValueError("分析 EXE 缺少构建记录，请使用 package_rust_core.py --build 重新构建")
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("schema") != BUILD_RECORD_SCHEMA:
        raise ValueError("分析 EXE 构建记录格式无效")
    inputs = source_inputs(source)
    if record.get("source_files") != inputs or record.get("source_input_sha256") != inputs_digest(inputs):
        raise ValueError("分析源码与 EXE 构建记录不一致，禁止把当前源码摘要配给旧 EXE，请重新构建")
    if record.get("sha256") != sha256(executable):
        raise ValueError("分析 EXE 与构建记录哈希不一致")
    if record.get("license_sha256") != sha256(source.parent / "LICENSE"):
        raise ValueError("分析组件许可与构建记录不一致")
    if (not isinstance(record.get("source_base_commit"), str)
            or not record["source_base_commit"] or type(record.get("source_worktree_modified")) is not bool
            or not isinstance(record.get("rustc"), str) or not record["rustc"]):
        raise ValueError("分析构建记录缺少源码或工具链身份")
    return record
