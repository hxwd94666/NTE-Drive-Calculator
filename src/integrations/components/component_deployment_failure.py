# 保存本机自动部署故障，避免后台轮询或程序重启反复写入被阻止的组件。
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


class ComponentDeploymentFailure:
    def __init__(self, config_dir: Path):
        self.path = config_dir / "component_deployment_failure.json"

    def load(self) -> str:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1
                    or not isinstance(value.get("detail"), str) or not value["detail"].strip()):
                raise ValueError("invalid_failure_record")
            return value["detail"][:4096]
        except FileNotFoundError:
            return ""
        except (OSError, ValueError):
            return "自动部署故障记录无法读取，已暂停自动重试；请核查组件后点击“检测并处理”重试。"

    def save(self, detail: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                    suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"version": 1, "detail": detail[:4096]}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
