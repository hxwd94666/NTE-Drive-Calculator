# 原子读写本机工作模式设置，不读取账号授权。
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep

from src.domain.work_mode import WorkMode, WorkModeSettings


class WorkModeSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.load_error = ""

    def load(self) -> WorkModeSettings:
        if not self.path.exists():
            return WorkModeSettings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != 1:
                raise ValueError("unsupported work mode settings")
            mode = WorkMode(raw.get("mode", "offline"))
            confirmed = raw.get("risk_confirmed") is True
            if not confirmed:
                mode = WorkMode.OFFLINE
            deployment = raw.get("deployment", {})
            if not isinstance(deployment, dict):
                raise ValueError("invalid deployment record")
            guided = type(raw.get("sync_guidance_version")) is int and raw["sync_guidance_version"] == 1
            return WorkModeSettings(
                mode=mode,
                risk_confirmed=confirmed and mode != WorkMode.OFFLINE,
                paused=raw.get("paused") is True,
                # 首次引导版本默认关闭同步，即使旧版本曾保存开启偏好，也不自动续启。
                auto_sync_enabled=guided and raw.get("auto_sync_enabled") is True,
                sync_guidance_version=1,
                component_auto_ready=guided and raw.get("component_auto_ready") is True,
                pending_cleanup=(raw.get("pending_cleanup") is not False),
                game_executable=str(raw.get("game_executable", "")),
                deployment_json=json.dumps(deployment, ensure_ascii=False),
                revision=max(0, int(raw.get("revision", 0))),
            )
        except (OSError, ValueError, TypeError):
            self.load_error = "工作模式配置无法读取，已安全退回离线，请重新选择模式。"
            return WorkModeSettings()

    def save(self, settings: WorkModeSettings) -> None:
        raw = asdict(settings)
        raw["schema_version"] = 1
        raw["deployment"] = json.loads(raw.pop("deployment_json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".work-mode-", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(raw, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            # Windows 短暂只读占用也会报拒绝访问；只重试同一已落盘文件，新增等待合计 100ms。
            # 永久权限错误仍抛出，保留旧配置；此处不重放模式切换或外部操作。
            for delay in (0.01, 0.03, 0.06, None):
                try:
                    os.replace(temporary, self.path)
                    break
                except PermissionError as error:
                    if os.name != "nt" or delay is None or getattr(error, "winerror", None) not in {5, 32}:
                        raise
                    sleep(delay)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
