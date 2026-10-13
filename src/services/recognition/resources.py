# 一次读取中文识别资料并核对实际数据库身份，禁止跨阶段重绑最新规则。
from __future__ import annotations

import hashlib
from pathlib import Path

from src.integrations.bundled_resources import bundled_config_dir
from src.integrations.vision.recognition.resources import (
    RecognitionResources, RecognitionResourceError, recognition_file_versions,
)
from src.services.allocation.inventory import legacy_shape_id
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao, resolve_static_database


def _dataset(metadata: dict) -> tuple[str, int, str]:
    return str(metadata["dataset_id"]), int(metadata["importer_version"]), str(metadata["built_at_utc"])


def check_recognition_resources(resources: RecognitionResources) -> None:
    try:
        if resources.image_profile is not None:
            resources.image_profile.verify()
        before = recognition_file_versions(resources.config_dir, resources.static_database_path)
        if before != resources.file_versions:
            raise RecognitionResourceError("识别资料已更新，请重新解析；当前背包保留。")
        if hashlib.sha256((resources.config_dir / "stats.json").read_bytes()).hexdigest() != resources.stats_sha256:
            raise RecognitionResourceError("识别词条规则已更新，请重新解析。")
        # 不复用按路径缓存的旧连接；同时检查真正被打开的 dataset，不把文件探针当内容哈希。
        with StaticGameDataDao(resources.static_database_path, reuse_connection=False) as dao:
            if _dataset(dao.dataset_metadata()) != resources.dataset:
                raise RecognitionResourceError("识别静态资料身份已更新，请重新解析。")
        if recognition_file_versions(resources.config_dir, resources.static_database_path) != before:
            raise RecognitionResourceError("识别资料检查期间发生变化，请重新解析。")
    except RecognitionResourceError:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        raise RecognitionResourceError("识别资料检查失败，请重新解析；当前背包保留。") from error


def load_recognition_resources(config_dir: str | Path | None = None,
                               static_database_path: str | Path | None = None) -> RecognitionResources:
    root = Path(config_dir if config_dir is not None else bundled_config_dir()).resolve()
    database = resolve_static_database(static_database_path)
    try:
        with StaticGameDataDao(database, reuse_connection=False) as dao:
            before = recognition_file_versions(root, database)
            stats_bytes = (root / "stats.json").read_bytes()
            # 多查询位于同一个只读事务；DAO 相同本身并不保证一致快照。
            with dao.read_consistent_state():
                dataset = _dataset(dao.dataset_metadata())
                suits = tuple((str(row["suit_id"]), str(row["name_zh"])) for row in dao.list_suits())
                shapes = frozenset(legacy_shape_id(row["shape_id"]) for row in dao.list_shapes())
            resources = RecognitionResources(root, database, dataset, stats_bytes,
                hashlib.sha256(stats_bytes).hexdigest(), suits, shapes, before)
            if not resources.stat_catalog().gold_base_values or not suits or not shapes:
                raise RecognitionResourceError("识别资料缺少词条、套装或形状定义。")
        check_recognition_resources(resources)
        return resources
    except RecognitionResourceError:
        raise
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        raise RecognitionResourceError("识别资料获取失败，请检查配置并重新解析。") from error
