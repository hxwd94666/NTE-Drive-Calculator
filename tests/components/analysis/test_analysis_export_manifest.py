# 验证独立组件导出只携带整体输入摘要、执行物及许可，不外发私库逐文件冻结清单或源码。
import hashlib
import json
import sys
import zipfile
from types import SimpleNamespace

from tools.counterfactual import package_rust_core as packaging
from src.integrations.analysis_core.nte_analysis_core import ENGINE_VERSION


NTE_TEST_TIER = "core"


def test_export_keeps_detailed_receipt_private_and_binds_public_manifest(tmp_path, monkeypatch):
    source = tmp_path / "analysis-core"
    source.mkdir()
    (source / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (source / "src").mkdir()
    (source / "src/private_rule.rs").write_text("// 私有测试占位，不进入导出包。\n", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("fixture-license\n", encoding="utf-8")
    (source / "THIRD_PARTY_NOTICES.txt").write_text("fixture-notices\n", encoding="utf-8")
    executable = source / "fixture.exe"
    executable.write_bytes(b"fixture-executable-not-a-real-engine")
    receipt = {"sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
               "source_files": {"src/private_rule.rs": "a" * 64}, "source_base_commit": "b" * 40,
               "source_worktree_modified": True, "rustc": "fixture-rustc", "source_input_sha256": "c" * 64}
    # 打包门禁要求的完整能力集合（含家具属性、worker、直伤与分段耗时）。
    version = {"engine_version": ENGINE_VERSION, "capabilities": [
        "battle_page_v1", "furniture_attributes_v1", "main_static_catalog_v1", "allocation_v2", "damage_allocation_v1",
        "equipment_scoring_v1", "allocation_blueprints_v1", "battle_axis_team_v1", "battle_axis_fixed_team_v1",
        "analysis_worker_v1", "direct_damage_v1", "analysis_stage_timings_v1"]}
    monkeypatch.setattr(packaging, "verify_build_record", lambda *_args: receipt)
    monkeypatch.setattr(packaging, "validate_catalog_inputs", lambda _path: None)
    monkeypatch.setattr(packaging, "NteAnalysisCoreClient", lambda *_a: SimpleNamespace(version=lambda: version))
    destination, output = tmp_path / "component", tmp_path / "export"
    monkeypatch.setattr(sys, "argv", ["package_rust_core.py", "--source", str(source),
        "--executable", str(executable), "--destination", str(destination), "--output", str(output)])
    assert packaging.main() == 0
    manifest = json.loads((destination / "component.json").read_text("utf-8"))
    assert "source_files" not in manifest
    assert manifest["source_input_sha256"] == receipt["source_input_sha256"]
    assert manifest["source_base_commit"] == receipt["source_base_commit"]
    assert receipt["source_files"] == {"src/private_rule.rs": "a" * 64}
    with zipfile.ZipFile(output / "nte-analysis-core-windows-x64.zip") as archive:
        assert set(archive.namelist()) == {"nte-analysis-core.exe", "component.json", "LICENSE",
                                          "THIRD_PARTY_NOTICES.txt", "SOURCE.md"}
        assert json.loads(archive.read("component.json")) == manifest
