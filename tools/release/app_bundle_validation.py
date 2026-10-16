# 共用目录式发行包的必要资源、组件、OCR 与许可证门禁，不执行构建或外部进程。
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.integrations.vision.recognition.ocr.model_resources import validate_packaged_ocr_models
from tools.release.game_component_bundle_build import validate_packaged_component_bundle


CONFIG_RELEASE_FILES = (
    "gameplay_effect_semantics.json", "global_ui_preferences.json", "stats.json", "workshop_weight_template.json",
)
CONFIG_RELEASE_DIRECTORIES = ("github", "templates")
REQUIRED_INTERNAL_FILES = {
    "nte-core 本地组件": "nte-core.exe",
    "战报分析本地组件": "nte-analysis-core.exe",
    "战报分析组件清单": "analysis-core-meta/component.json",
    "游戏组件整包清单": "component-bundle.json",
    "用户数据库结构": "src/storage/sqlite/schema/001_user_data.sql",
    "发行版静态数据库": "data/game_static.sqlite3",
    "发行版静态数据库清单": "data/manifest.json",
    "公共额外形状默认库": "data/app_shared.sqlite3",
    "旧版额外形状迁移基线": "data/migrations/shape_bonus_defaults_2.0.2.json",
}
REQUIRED_NOTICES = (
    "licenses/LICENSE", "licenses/NOTICE",
    "licenses/analysis-core/LICENSE", "licenses/analysis-core/SOURCE.md",
    "licenses/analysis-core/THIRD_PARTY_NOTICES.txt",
    "third_party/presentmon/LICENSE.txt", "third_party/presentmon/SOURCE.md",
)


def validate_analysis_component(binary: Path, manifest_path: Path) -> None:
    """采集组件不能代替分析组件；来源清单和成品均使用同一校验。"""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("独立分析组件清单无效") from error
    capabilities = manifest.get("capabilities") if isinstance(manifest, dict) else None
    with binary.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if (not isinstance(manifest, dict) or manifest.get("engine") != "nte-analysis-core"
            or manifest.get("engine_version") != "0.3.0" or manifest.get("sha256") != digest
            or not isinstance(capabilities, list) or "battle_page_v1" not in capabilities
            or not {"main_static_catalog_v1", "allocation_v2", "allocation_blueprints_v1", "damage_allocation_v1", "equipment_scoring_v1", "battle_axis_team_v1", "battle_axis_fixed_team_v1"}.issubset(capabilities)):
        raise RuntimeError("独立分析组件缺少战报、分配、Rust 图纸或队伍预检能力或哈希不匹配")


def validate_app_bundle(app_exe: Path, internal: Path, *, source_manifest_path: Path | None = None) -> None:
    """目录构建、安装器和 ZIP 共用门禁；发行文件身份仍由各正式组件清单决定。"""
    required = {"主程序": app_exe, **{label: internal / path for label, path in REQUIRED_INTERNAL_FILES.items()}}
    required.update({f"许可声明 {path}": internal / path for path in REQUIRED_NOTICES})
    required.update({f"默认配置 {name}": internal / "config" / name for name in CONFIG_RELEASE_FILES})
    missing = [f"{label}：{path}" for label, path in required.items() if not path.is_file()]
    if not internal.is_dir():
        missing.append(f"PyInstaller 运行目录：{internal}")
    if missing:
        raise RuntimeError("PyInstaller 产物不完整，缺少：\n" + "\n".join(missing))
    validate_packaged_component_bundle(internal, source_manifest_path=source_manifest_path)
    validate_packaged_ocr_models(internal)
    validate_analysis_component(internal / "nte-analysis-core.exe", internal / "analysis-core-meta/component.json")
    presentmon = internal / "third_party/presentmon"
    try:
        manifest = json.loads((presentmon / "component.json").read_text(encoding="utf-8"))
        with (presentmon / "PresentMon.exe").open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if manifest["sha256"] != actual:
            raise ValueError("hash mismatch")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError("PresentMon 组件清单或哈希不符") from error
