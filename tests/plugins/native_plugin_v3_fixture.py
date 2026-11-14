# 生成明确标记的合成 v3 清单，仅验证组件门禁，不代表真实 DLL 或 VMP 成功。
import hashlib
import json
from pathlib import Path

from src.integrations.components.native_plugin_bundle import SPLIT_PLUGIN_LAYOUT, native_deployment_paths


def add_split_roles(root, payload, *, prefix="native/"):
    old = payload["roles"]["capture_plugin"]
    payload["files"].pop(old)
    payload["file_sizes"].pop(old)
    for role, relative in native_deployment_paths(SPLIT_PLUGIN_LAYOUT).items():
        source = prefix + relative
        path = root / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("synthetic v3 fixture: " + role).encode())
        payload["roles"][role] = source
        payload["files"][source] = hashlib.sha256(path.read_bytes()).hexdigest()
        payload["file_sizes"][source] = path.stat().st_size
    payload.update(layout=SPLIT_PLUGIN_LAYOUT, plugin_policy="calc-publisher-rsa3072-sha256-v1")
    binaries = {}
    for role in ("host", "user_plugin", "capture_plugin", "hud_plugin", "performance_plugin"):
        name = Path(payload["roles"][role]).name
        binaries[name] = {
            "sha256": payload["files"][payload["roles"][role]], "functions": 1,
            "profile": {"name": "selected-functions-v1", "options": ["StripDebugInfo"]},
            "notice": {"schema": "nte.component-notice/1", "component": Path(name).stem,
                       "purpose": "synthetic fixture", "authorization": "fixture only",
                       "prohibited_use": "never treat as runtime evidence", "license_boundary": "fixture only"},
        }
    metadata = {"layout": SPLIT_PLUGIN_LAYOUT, "sourceTreeSha256": payload["input_digests"]["capture"],
                "protection": {"tool": "VMProtect", "policy_sha256": "f" * 64, "binaries": binaries}}
    write_metadata(root, payload, metadata)
    return metadata


def write_metadata(root, payload, metadata):
    relative = Path(payload["roles"]["capture_source"]).with_name("capture-component.json").as_posix()
    path = root / relative
    path.write_text(json.dumps(metadata), encoding="utf-8")
    payload["files"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload["file_sizes"][relative] = path.stat().st_size
