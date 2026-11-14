# 原子保存全局插件偏好，不读写账号数据库。
from dataclasses import asdict
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from src.domain.preferences.plugin_settings import PluginSettings


class PluginSettingsStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> PluginSettings:
        if not self.path.exists():
            return PluginSettings()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("插件设置格式无效")
        defaults = asdict(PluginSettings())  # 旧 ready_cue 字段不再参与有效配置。
        return PluginSettings(**{key: data[key] if isinstance(data.get(key), bool) else value
                                 for key, value in defaults.items()})

    def save(self, settings: PluginSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                    suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(asdict(settings), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
