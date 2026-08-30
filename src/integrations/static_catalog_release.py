# 读取并核对游戏资料库发行元数据。
"""Read-only release identity reader for the bundled static catalog."""

from __future__ import annotations

import json
from pathlib import Path

from src.domain.static_catalog import StaticCatalogRelease
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao, StaticGameDataError


class StaticCatalogReleaseError(RuntimeError):
    """The database and its release manifest do not describe one release."""


class StaticCatalogReleaseReader:
    """Freeze and later revalidate the path/dataset identity of one request."""

    def freeze(self, database_path: str | Path) -> StaticCatalogRelease:
        path = Path(database_path).resolve()
        manifest_path = path.with_name("manifest.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StaticCatalogReleaseError("无法读取静态资料库发行清单") from exc
        database_manifest = manifest.get("database")
        build_tool = manifest.get("build_tool")
        if not isinstance(database_manifest, dict) or not isinstance(build_tool, dict):
            raise StaticCatalogReleaseError("静态资料库发行清单缺少数据库或导入器信息")
        try:
            with StaticGameDataDao(path) as dao:
                summary = dao.summary()
        except StaticGameDataError as exc:
            raise StaticCatalogReleaseError(str(exc)) from exc
        dataset = summary.get("dataset")
        if not isinstance(dataset, dict):
            raise StaticCatalogReleaseError("静态资料库缺少 dataset 元数据")
        release = StaticCatalogRelease(
            database_path=path,
            dataset_id=str(dataset.get("dataset_id") or ""),
            schema_version=int(summary.get("schema_version") or 0),
            importer_version=int(dataset.get("importer_version") or 0),
            built_at_utc=str(dataset.get("built_at_utc") or ""),
            source_payloads_omitted=bool(database_manifest.get("source_payloads_omitted")),
        )
        expected = (
            str(database_manifest.get("dataset_id") or ""),
            int(database_manifest.get("schema_version") or 0),
            int(build_tool.get("importer_version") or 0),
        )
        actual = (release.dataset_id, release.schema_version, release.importer_version)
        if actual != expected:
            raise StaticCatalogReleaseError("静态数据库与发行清单的 dataset/schema/importer 不一致")
        return release

    def ensure_unchanged(self, release: StaticCatalogRelease) -> None:
        current = self.freeze(release.database_path)
        if current != release:
            raise StaticCatalogReleaseError("静态资料库已更新，请刷新页面后重新查询")
