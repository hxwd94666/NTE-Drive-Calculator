# 为打包行为测试补齐公开合成运行资源，保留原生组件夹具已核对的字节。
from __future__ import annotations

import hashlib
import json

from tools.release.app_bundle_validation import CONFIG_RELEASE_FILES, REQUIRED_INTERNAL_FILES, REQUIRED_NOTICES


def add_app_requirements(app_exe, internal):
    app_exe.parent.mkdir(parents=True, exist_ok=True)
    app_exe.write_bytes(b"synthetic application executable")
    for relative in (*REQUIRED_INTERNAL_FILES.values(), *REQUIRED_NOTICES,
                     *("config/" + name for name in CONFIG_RELEASE_FILES), "third_party/presentmon/PresentMon.exe"):
        path = internal / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(b"synthetic public release resource")
    digest = hashlib.sha256((internal / "nte-analysis-core.exe").read_bytes()).hexdigest()
    (internal / "analysis-core-meta/component.json").write_text(json.dumps({
        "engine": "nte-analysis-core", "engine_version": "0.3.0", "sha256": digest,
        "capabilities": ["battle_page_v1", "main_static_catalog_v1", "allocation_v2", "allocation_blueprints_v1", "damage_allocation_v1", "equipment_scoring_v1", "battle_axis_team_v1", "battle_axis_fixed_team_v1"],
    }), encoding="utf-8")
    (internal / "third_party/presentmon/component.json").write_text(json.dumps({
        "sha256": hashlib.sha256((internal / "third_party/presentmon/PresentMon.exe").read_bytes()).hexdigest(),
    }), encoding="utf-8")
