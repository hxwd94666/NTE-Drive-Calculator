# 保存一次识别的不可变资料，并探测文件版本；不读数据库或绑定界面。
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.integrations.vision.recognition.image_resources import RecognitionImageProfile

from src.domain.stat_catalog import StatCatalog


class RecognitionResourceError(RuntimeError):
    """识别资料变化或获取不完整，本批应重新发起。"""


def recognition_file_versions(config_dir: Path, database: Path) -> tuple[tuple, ...]:
    versions = []
    for path in (config_dir / "stats.json", database, Path(str(database) + "-wal")):
        try:
            state = path.stat()
        except FileNotFoundError:
            if str(path).endswith("-wal"):
                versions.append((str(path), None))
                continue
            raise
        # 空 WAL 不包含资料变更；只读连接生成/回收它不应使同批自行失效。
        version = (state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns)
        versions.append((str(path), None if str(path).endswith("-wal") and state.st_size == 0 else version))
    return tuple(versions)


@dataclass(frozen=True)
class RecognitionResources:
    config_dir: Path
    static_database_path: Path
    dataset: tuple[str, int, str]
    stats_bytes: bytes
    stats_sha256: str
    suits: tuple[tuple[str, str], ...]
    shape_ids: frozenset[str]
    file_versions: tuple[tuple, ...]
    image_profile: RecognitionImageProfile | None = None

    def stat_catalog(self) -> StatCatalog:
        # 每个消费者私有可变映射；字节源不可变，不逐张图片重新创建。
        return StatCatalog.from_data(json.loads(self.stats_bytes))

    def public_identity(self) -> dict:
        identity = {"dataset_id": self.dataset[0], "importer_version": self.dataset[1],
                "built_at_utc": self.dataset[2], "stats_sha256": self.stats_sha256,
                "input_language": "zh-CN"}
        if self.image_profile is not None:
            identity["image_profile"] = self.image_profile.public_identity()
        return identity
