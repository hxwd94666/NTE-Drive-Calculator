# 解析显式发行身份；缺失标识沿用安装版，不从路径猜测平台。
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re


class DistributionError(RuntimeError):
    """发行身份或数据位置需要用户修复。"""


@dataclass(frozen=True, slots=True)
class Distribution:
    schema_version: int = 1
    product: str = "nte-drive-calc"
    distribution: str = "installed"
    update_channel: str = "installer"
    version: str = ""
    build_id: str = "legacy"
    architecture: str = "x64"
    entrypoint: str = "NTE_Drive_Calc.exe"
    release_manifest: str = "release-files.json"
    data_policy: str = "userdata"
    data_directory: str = "userdata"
    data_root_key: str = ""

    @property
    def portable(self) -> bool:
        return self.distribution == "portable"


def parse_distribution(value: object) -> Distribution:
    if not isinstance(value, dict):
        raise DistributionError("发行标识格式错误，请重新解压完整程序包。")
    required = {"schema_version", "product", "distribution", "update_channel", "version",
                "build_id", "architecture", "entrypoint", "release_manifest"}
    if not required.issubset(value) or set(value) - set(Distribution.__dataclass_fields__):
        raise DistributionError("发行标识字段缺失或不受支持。")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise DistributionError("发行标识版本不受支持。")
    if any(not isinstance(v, str) for k, v in value.items() if k != "schema_version"):
        raise DistributionError("发行标识字段类型错误。")
    result = Distribution(**value)
    if (result.product != "nte-drive-calc" or result.distribution not in {"installed", "portable"}
            or result.architecture != "x64" or not result.version or not result.build_id
            or result.build_id == "legacy" or result.entrypoint != "NTE_Drive_Calc.exe"
            or result.release_manifest != "release-files.json"
            or result.data_policy not in {"userdata", "external"}
            or result.data_directory != "userdata"):
        raise DistributionError("发行身份或目录策略不受支持。")
    if result.update_channel not in ({"manual", "platform"} if result.portable else {"installer"}):
        raise DistributionError("发行类型与更新渠道不一致。")
    if result.data_policy == "external":
        if (not result.portable or result.update_channel != "platform"
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", result.data_root_key)):
            raise DistributionError("外部数据根需要平台便携标识和稳定 data_root_key。")
    elif result.data_root_key:
        raise DistributionError("内置 userdata 策略不接受外部数据根标识。")
    return result


def load_distribution(app_dir: Path) -> Distribution:
    path = Path(app_dir) / "distribution.json"
    if not path.exists():
        maintenance = Path(app_dir) / "userdata" / "maintenance"
        if any((maintenance / name).exists() for name in
               ("accepted-release.json", "data-root.json", "pending-upgrade.json", "last-import.json")):
            raise DistributionError("已有便携维护身份但发行标识丢失；请重新解压完整程序包，不初始化其他数据根。")
        return Distribution()
    try:
        if path.is_symlink() or path.stat().st_size > 64 * 1024:
            raise ValueError("invalid metadata file")
        return parse_distribution(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as error:
        raise DistributionError(f"发行标识读取失败：{path}；请修复完整程序包。") from error
