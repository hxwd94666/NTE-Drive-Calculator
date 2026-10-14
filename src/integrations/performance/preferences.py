# 原子保存本机性能显示偏好，首次关闭且不随账号导出。
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


class PerformancePreferences:
    def __init__(self, path: Path):
        self.path = path

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("invalid_preferences")
        except (OSError, ValueError):
            data = {}
        return {"overlay": data.get("overlay") is True, "linked": data.get("linked", True) is True}

    def save(self, values):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                    suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(values, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
