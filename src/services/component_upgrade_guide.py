# 只读核对旧组件证据并生成升级引导状态，不修改游戏或账号数据。
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from src.integrations.legacy_game_proxy import legacy_game_proxy_present
from src.services.deployed_plugin_inspection import inspect_deployed_native_plugin
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError, mod_workspace_registry_snapshot,
)


@dataclass(frozen=True)
class UpgradeEvidence:
    kind: str
    reason: str
    detail: str
    method: str

    @property
    def requires_attention(self) -> bool:
        return self.kind in {"legacy", "update", "path_unknown"}


def inspect_upgrade_evidence(*, application_root: Path, game_path: str,
                             deployment: Mapping, loader=None) -> UpgradeEvidence:
    """Inspect only the configured/recorded game and registered workspace."""
    method = "loader" if deployment.get("loading_method") == "loader" else "native-capture"
    recorded_path = str(deployment.get("game_executable") or "")
    raw_path = (recorded_path or game_path).strip().strip('"')
    path = Path(raw_path).expanduser() if raw_path else None
    has_record = any(deployment.get(key) for key in (
        "deployed_sha256", "managed_files", "workspace_path", "native_workspace_root",
    ))
    if path is None or not path.is_absolute() or path.name.casefold() != "htgame.exe":
        if has_record:
            return UpgradeEvidence("path_unknown", "旧部署记录中的游戏位置尚未确认。",
                                   "请在环境设置中重新选择游戏主程序；不会改用搜索到的其他目录清理。", method)
        return UpgradeEvidence("none", "尚无旧插件部署证据。", "", method)
    game_directory = path.parent
    try:
        proxy_present = legacy_game_proxy_present(game_directory)
    except OSError as error:
        return UpgradeEvidence("path_unknown", "读取旧组件状态失败。", type(error).__name__, method)
    old_workspace = str(deployment.get("workspace_path") or "")
    old_record = has_record and deployment.get("deployment_layout") != "native-capture-v1"
    if proxy_present or old_record:
        try:
            registered, registry_path = mod_workspace_registry_snapshot()
        except (OSError, EquipmentPluginDeploymentError) as error:
            return UpgradeEvidence("path_unknown", "读取旧工作区登记失败。", type(error).__name__, method)
        reasons = []
        if proxy_present:
            reasons.append("游戏目录存在旧加载文件 dwmapi.dll")
        if old_record:
            reasons.append("保留着旧版部署记录")
        if old_workspace and registered and registry_path:
            try:
                if Path(old_workspace).expanduser().resolve() != Path(registry_path).expanduser().resolve():
                    reasons.append("Mod 工作区登记与旧记录不同")
            except OSError:
                reasons.append("Mod 工作区登记需要重新核对")
        return UpgradeEvidence("legacy", "；".join(reasons) + "。",
                               "旧组件需要先清理，再按当前加载方式部署。", method)
    if not path.is_file():
        if has_record:
            return UpgradeEvidence("path_unknown", "原游戏主程序已不在记录位置。",
                                   "请先在环境设置中确认游戏位置；旧记录仍按原目录处理。", method)
        return UpgradeEvidence("none", "尚无旧插件部署证据。", "", method)
    if method == "loader":
        if not deployment.get("native_workspace_root"):
            return UpgradeEvidence("none", "尚无 Loader 部署证据。", "", method)
        try:
            compatible = loader.inspect_native_workspace().files_compatible
        except (OSError, ValueError, AttributeError, EquipmentPluginDeploymentError) as error:
            return UpgradeEvidence("path_unknown", "Loader 工作区核对失败。", type(error).__name__, method)
    else:
        if not (has_record or (game_directory / "d3d12.dll").is_file()
                or (game_directory / "NTE_Capture.dll").is_file()):
            return UpgradeEvidence("none", "尚无旧插件部署证据。", "", method)
        inspection = inspect_deployed_native_plugin(
            application_root=application_root, game_executable_path=path,
            recorded_files=deployment.get("managed_files") or {},
        )
        compatible = inspection.files_compatible
    if not compatible:
        return UpgradeEvidence("update", "当前组件与本版本配套文件不一致。",
                               "先关闭游戏和启动器，清理旧组件，再部署当前版本。", method)
    return UpgradeEvidence("ready", "组件文件已与当前版本匹配。",
                           "文件核对不等于游戏内连接和同步就绪。", method)
