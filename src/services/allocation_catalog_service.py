# 读取计算目录并按实际业务依赖校验有界缓存。
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from src.domain.stat_catalog import StatCatalog
from src.domain.recommended_weights import base_weight_character_id
from src.domain.role_name_order import role_name_sort_key
from src.optimizer.scoring import ScoringEngine
from src.services.character_weight_service import is_unmodified_account_weight_cache
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.legacy_allocation_static_catalog import build_legacy_allocation_static_catalog
from src.services.role_fork_template_service import fork_templates_as_weapon_models, load_official_role_fork_templates
from src.services.workshop_weight_template_service import configured_workshop_weight_template_file
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger
from src.utils.perf import log_perf


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def _content(value):
    if isinstance(value, dict):
        return {str(key): _content(item) for key, item in value.items()
                if not str(key).endswith("_at_utc")}
    if isinstance(value, (list, tuple)):
        return [_content(item) for item in value]
    return value


def _stamp(path):
    try:
        stat = path.stat()
        return str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns
    except OSError:
        return str(path), None


@dataclass(frozen=True)
class CatalogDependencies:
    account_id: str
    generation: int
    config_dir: Path
    user_database_path: Path
    static_database_path: Path
    asset_root: Path

    @classmethod
    def from_context(cls, context):
        return cls(context.account.active_account_id, context.generation,
                   context.paths.config_dir, context.account.user_database_path,
                   context.paths.equipment_allocation_database_path,
                   context.paths.equipment_allocation_asset_root)


class AllocationCatalogService:
    """Worker-only probes; unrelated account writes don't rebuild the catalog."""

    def __init__(self, dependencies, reader=None):
        self.dependencies = dependencies
        self.reader = reader or read_allocation_catalog
        self._cached = self._probe = self._semantic = None

    def clear(self):
        self._cached = self._probe = self._semantic = None

    def probe(self):
        deps = self.dependencies
        template = configured_workshop_weight_template_file()
        files = [deps.config_dir / "stats.json", deps.asset_root / "manifest.json"]
        for database in (deps.user_database_path, deps.static_database_path):
            files.extend((database, Path(str(database) + "-wal")))
        if template is not None:
            files.append(template)
        return tuple(_stamp(path) for path in files)

    def identity(self):
        deps = self.dependencies
        with StaticGameDataDao(deps.static_database_path) as static:
            official_ids = [int(row["character_id"]) for row in static.list_role_template_characters()]
        # Only these account domains are used by read_allocation_catalog. Inventory
        # contributes observed role identities, not its UID contents or history rows.
        with UserDataDao(deps.user_database_path) as dao, dao.read_consistent_state():
            custom = dao.list_custom_characters()
            ids = sorted({base_weight_character_id(cid) for cid in official_ids}
                         | {int(row["character_id"]) for row in custom})
            weights = {}
            for character_id in ids:
                record = dao.get_character_weight_preferences(character_id)
                weights[character_id] = (None if record is None else {
                    "uses_template": is_unmodified_account_weight_cache(record),
                    "source_kind": record.get("source_kind"),
                    "source_dataset_id": record.get("source_dataset_id"),
                    "property_weights": record.get("property_weights"),
                    "main_property_weights": record.get("main_property_weights"),
                    "seeded_at_utc": record.get("seeded_at_utc"),
                    "updated_at_utc": record.get("updated_at_utc"),
                })
            account = {
                "weights": weights, "custom": sorted(custom, key=lambda row: row["character_id"]),
                "profiles": sorted(dao.list_character_profiles(include_inactive=True), key=lambda row: row["character_id"]),
                "native": dao.list_native_character_profile_observations(),
                "observed_ids": dao.list_observed_character_ids(),
                "world_bonus": dao.list_application_setting_copies().get("world_bonus"),
            }
        template = configured_workshop_weight_template_file()
        files = {}
        for path in (deps.config_dir / "stats.json", template):
            if path is None or not path.is_file():
                continue
            try:
                files[str(path)] = _content(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, UnicodeError):
                if path != template:
                    raise
                files[str(path)] = "invalid_template"  # Preserve the existing static fallback.
        immutable = (_stamp(deps.static_database_path), _stamp(Path(str(deps.static_database_path) + "-wal")),
                     _stamp(deps.asset_root / "manifest.json"))
        semantic = _digest((_content(account), files, immutable))
        return semantic, _digest((account, files, immutable))

    def read(self, *, verify=False):
        start = perf_counter()
        probe = self.probe()
        if self._cached is not None and not verify and probe == self._probe:
            return self._cached, self._semantic, False
        for _ in range(2):
            semantic, stable = self.identity()
            if self._cached is not None and semantic == self._semantic:
                self._probe = probe
                log_perf(logger, "allocation.catalog_validate", elapsed_ms=(perf_counter()-start)*1000, reused=True)
                return self._cached, semantic, False
            deps = self.dependencies
            loaded = self.reader(deps.config_dir, deps.user_database_path, deps.static_database_path, deps.asset_root)
            if self.identity() != (semantic, stable):
                continue  # Bounded retry on relevant changes, not every WAL write.
            self._cached, self._semantic = loaded, semantic
            self._probe = probe if probe == self.probe() else None
            return loaded, semantic, True
        raise RuntimeError("计算配置在读取期间发生变化，请稍后重试。")

    def inventory_summary(self):
        with UserDataDao(self.dependencies.user_database_path) as dao:
            return dao.current_inventory_summary()


def read_allocation_catalog(config_dir, user_database_path, allocation_database_path, asset_root):
    """Read SQLite/config inputs without touching Qt widgets."""
    started = perf_counter()
    catalog = StatCatalog.from_config_dir(config_dir)
    stats_config = {
        "gold_base_values": catalog.gold_base_values,
        "tape_main_stats_pool": catalog.tape_main_stats,
        "tape_main_stat_values": catalog.tape_main_values,
        "tape_stat_values": catalog.tape_stat_values,
        "main_only_keywords": catalog.main_only_keywords,
        "stat_alias_mapping": catalog.stat_alias_mapping,
        "benefit_one": catalog.benefit_one,
        "benefit_alias_mapping": catalog.benefit_alias_mapping,
        "weight_pool": catalog.weight_pool,
    }
    weapons_db = fork_templates_as_weapon_models(
        load_official_role_fork_templates(allocation_database_path)
    )
    static_catalog = build_legacy_allocation_static_catalog(
        config_dir=config_dir,
        user_database_path=user_database_path,
        static_database_path=allocation_database_path,
    )
    roles_db = static_catalog.roles_db
    # Warm the pure presentation key on this worker, including first pinyin
    # dictionary import; widget construction must not pay that cold-start cost.
    for name in roles_db:
        role_name_sort_key(name)
    shape_areas = {
        shape_id: int(shape.area)
        for shape_id, shape in static_catalog.shapes_db.items()
    }
    scoring_engine = ScoringEngine(
        str(config_dir), user_database_path=user_database_path, roles_db=roles_db,
    )
    character_art = GameUiAssetCatalog(asset_root)
    character_icon_paths = {
        name: icon
        for name, role in roles_db.items()
        if isinstance(role, dict) and role.get("character_id") is not None
        if (icon := character_art.character_icon(int(role["character_id"]))) is not None
    }
    log_perf(logger, "allocation.catalog_read", elapsed_ms=(perf_counter() - started) * 1000)
    return (stats_config, catalog.tape_main_stats,
            list(catalog.gold_base_values.keys()), weapons_db, roles_db,
            static_catalog.sets_db, shape_areas, scoring_engine,
            character_icon_paths)

