# 核对构建记录后打包独立分析程序，仅显式指定时构建，不覆盖采集 Core。
"""Deliver verified analysis binaries and licenses; record source hashes without source files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from tools.game_data.build_analysis_catalogs import validate_catalog_inputs
from tools.counterfactual.rust_core_build import build_analysis_core, verify_build_record


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--build", action="store_true", help="先构建独立分析工程并生成源码/产物核对记录")
    parser.add_argument("--output", type=Path, default=ROOT / "output/nte-analysis-core")
    parser.add_argument("--destination", type=Path, default=ROOT / "third_party/analysis-core")
    parser.add_argument("--static-database", type=Path, default=ROOT / "data/game_static.sqlite3")
    args = parser.parse_args()
    source = args.source.resolve()
    if not (source / "Cargo.toml").is_file() or source.name != "analysis-core":
        raise ValueError("source must be the independent analysis-core crate")
    if args.build and args.executable is not None:
        parser.error("--build 不可与 --executable 同时使用")
    validate_catalog_inputs(args.static_database)
    executable = (build_analysis_core(source) if args.build else
                  (args.executable or source / "target/release/nte-analysis-core.exe").resolve())
    build_record = verify_build_record(source, executable)
    client = NteAnalysisCoreClient(executable, "packaging-validation")
    version = client.version()
    capabilities = version.get("capabilities")
    if (
        version.get("engine_version") != "0.3.0"
        or not isinstance(capabilities, list)
        or "battle_page_v1" not in capabilities
        or "main_static_catalog_v1" not in capabilities
        or "allocation_v2" not in capabilities
    ):
        raise RuntimeError("分析组件缺少战报数据库直读或空幕分配能力")
    license_path = source.parent / "LICENSE"
    manifest = {
        **version,
        "sha256": build_record["sha256"],
        "size_bytes": executable.stat().st_size,
        "source_files": build_record["source_files"],
        "source_base_commit": build_record["source_base_commit"],
        "source_worktree_modified": build_record["source_worktree_modified"],
        "rustc": build_record["rustc"],
        "license_sha256": sha256(license_path),
        "dependency_notices_sha256": sha256(source / "THIRD_PARTY_NOTICES.txt"),
        "scope": "native read-only account and static database loading, frozen battle analysis, replay, robust target fitting, buff and equipment counterfactuals, marginal panel, frozen drive allocation; Python owns user input, blueprint generation, process lifecycle and rendering",
    }
    manifest["source_input_sha256"] = build_record["source_input_sha256"]
    # 可执行文件探测后、修改目标前再次核对整套构建记录，防止配给旧产物。
    if verify_build_record(source, executable) != build_record:
        raise RuntimeError("分析组件构建记录在核对期间发生变化，请重新打包")
    destination = args.destination.resolve()
    (destination / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(executable, destination / "bin/nte-analysis-core.exe")
    shutil.copy2(license_path, destination / "LICENSE")
    shutil.copy2(source / "THIRD_PARTY_NOTICES.txt", destination / "THIRD_PARTY_NOTICES.txt")
    source_notice = ROOT / "third_party/analysis-core/SOURCE.md"
    if source_notice.resolve() != (destination / "SOURCE.md").resolve():
        shutil.copy2(source_notice, destination / "SOURCE.md")
    text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    (destination / "component.json").write_text(text, encoding="utf-8", newline="\n")
    if sha256(destination / "bin/nte-analysis-core.exe") != manifest["sha256"]:
        raise RuntimeError("deployed binary hash mismatch")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "nte-analysis-core-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(executable, "nte-analysis-core.exe")
        bundle.writestr("component.json", text)
        bundle.write(license_path, "LICENSE")
        bundle.write(source / "THIRD_PARTY_NOTICES.txt", "THIRD_PARTY_NOTICES.txt")
        bundle.write(destination / "SOURCE.md", "SOURCE.md")
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise RuntimeError("archive verification failed")
    print(json.dumps({"engine_version": version["engine_version"],
                      "sha256": manifest["sha256"], "size_bytes": manifest["size_bytes"],
                      "source_file_count": len(manifest["source_files"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
