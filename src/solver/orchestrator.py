# 统筹库存、评分和求解器生成最终配装。
"""End-to-end pipeline for blueprints, scoring, dispatch, and output."""
from src.observability.stage_timings import timed, timed_call

import copy
import json
import time
from concurrent.futures import CancelledError
from pathlib import Path
from typing import Callable, List, Dict

from src.domain.equipment.properties.stat_catalog import StatCatalog
from src.integrations.runtime.bundled_resources import bundled_config_dir
from src.optimizer.contracts import PLAN_CUSTOM_WEAPON
from src.domain.equipment.properties.equipment_normalizer import normalize_equipment_item
from src.models.equipment import DriveShape, Drive, Tape
from src.solver.combinatorics import PuzzleCombinatorics
from src.solver.dfs_puzzle import DFSPuzzleSolver
from src.solver.blueprint_utils import dedupe_blueprints_by_piece_signature
from src.solver.set_effects import normalize_set_effect_mode, set_piece_options_for_mode
from src.optimizer.scoring import ScoringEngine
from src.optimizer.allocation_kernel import AllocationKernelRequest, estimate_candidate_pool_limits
from src.utils.logger import logger
from src.utils.name_resolver import resolve_name
from src.utils.set_name import normalize_set_display_name
from src.services.allocation.execution.legacy_allocation_static_catalog import build_legacy_allocation_static_catalog


class NTEPipelineOrchestrator:
    _blueprint_cache: dict[str, List[Dict]] = {}
    _blueprint_cache_limit = 256

    def __init__(
        self, config_dir: str | Path | None = None, *, user_database_path=None,
        static_database_path: str | Path | None = None,
        expected_dataset: tuple[str, int, str] | None = None, stat_catalog: StatCatalog | None = None,
        static_read_unit=None, blueprint_executor=None, timings=None,
    ):
        self.config_dir = str(Path(config_dir) if config_dir is not None else bundled_config_dir())
        self.user_database_path = user_database_path
        self.static_database_path = static_database_path
        self._expected_dataset = expected_dataset
        self._stat_catalog = stat_catalog
        self._static_read_unit = static_read_unit
        self._blueprint_executor = blueprint_executor
        self._timings = timings
        self.roles_db = {}
        self.sets_db = {}
        self.shapes_db = {}
        self._board_matrices = {}
        self._fork_ids_by_name = {}
        self._load_configs()

    @classmethod
    def from_frozen_inputs(
        cls,
        *,
        roles_db: dict,
        sets_db: dict,
        shapes_db: dict[str, DriveShape],
        config_dir: str | Path | None = None,
        fork_ids_by_name: dict[str, str] | None = None,
        board_matrices=None, static_database_path=None, blueprint_executor=None, timings=None,
    ) -> "NTEPipelineOrchestrator":
        """Create a puzzle-only orchestrator without rereading mutable config.

        Context callers already copied every role, suit and shape input.  This
        named constructor keeps that boundary explicit instead of having an
        adapter bypass ``__init__`` and patch private attributes afterwards.
        """

        instance = cls.__new__(cls)
        instance.config_dir = str(
            Path(config_dir) if config_dir is not None else bundled_config_dir()
        )
        instance.user_database_path = None
        instance.static_database_path = static_database_path
        instance._blueprint_executor = blueprint_executor
        instance._timings = timings
        instance.roles_db = roles_db
        instance.sets_db = sets_db
        instance.shapes_db = shapes_db
        instance._fork_ids_by_name = dict(fork_ids_by_name or {})
        instance._board_matrices = board_matrices if board_matrices is not None else {
            role_name: role_data["board_matrix"]
            for role_name, role_data in roles_db.items()
        }
        # 纯几何原生缓存仍沿用现有完整事实 key；账号权重和评分从不进入此共享缓存。
        instance._blueprint_cache = cls._blueprint_cache if blueprint_executor is not None else {}
        return instance

    def _load_configs(self):
        catalog = build_legacy_allocation_static_catalog(
            config_dir=self.config_dir,
            user_database_path=self.user_database_path,
            static_database_path=self.static_database_path,
            expected_dataset=self._expected_dataset, stat_catalog=self._stat_catalog,
            static_read_unit=self._static_read_unit,
        )
        self.roles_db = catalog.roles_db
        self.sets_db = catalog.sets_db
        self.shapes_db = catalog.shapes_db
        self._board_matrices = catalog.board_matrices
        self._fork_ids_by_name = catalog.fork_ids_by_name

    def _resolve_set_name(self, set_name: str) -> str:
        normalized_name = normalize_set_display_name(set_name)
        resolved = resolve_name(normalized_name, self.sets_db.keys(), cutoff=0.78)
        if not resolved:
            available = "、".join(self.sets_db.keys())
            raise ValueError(f"错误：指定的套装 {set_name} 不存在于官方 SQLite 数据中！可用套装：{available}")  # ui-text: internal
        return resolved

    def _canonicalize_role_sets(self):
        for role_name, role_data in self.roles_db.items():
            if "default_set" not in role_data:
                continue
            raw_set = role_data["default_set"]
            resolved = self._resolve_set_name(raw_set)
            if resolved != raw_set:
                logger.warning(f"角色 [{role_name}] 默认套装名已自动修正: {raw_set} -> {resolved}")
                role_data["default_set"] = resolved

    def _canonicalize_custom_sets(self, custom_sets: Dict[str, str] | None) -> Dict[str, str]:
        resolved_sets = {}
        for role_name, set_name in (custom_sets or {}).items():
            if set_name:
                resolved_sets[role_name] = self._resolve_set_name(set_name)
        return resolved_sets

    def solve_blueprints(self, target_roles: List[str], custom_sets: Dict[str, str] = None,
                         set_effect_modes: Dict[str, str] = None,
                         include_layout_variants: bool = False,
                         cancel_check: Callable[[], bool] | None = None) -> Dict[str, List[Dict]]:
        custom_sets = self._canonicalize_custom_sets(custom_sets)
        set_effect_modes = set_effect_modes or {}
        if self._blueprint_executor is not None:
            return self._solve_native_blueprints(target_roles, custom_sets, set_effect_modes,
                                                 include_layout_variants, cancel_check)
        logger.info(f"\n[阶段 2] 求解 {target_roles} 的合法底盘图纸...")
        combinatorics = PuzzleCombinatorics(self.shapes_db)
        dfs_solver = DFSPuzzleSolver(self.shapes_db)
        real_blueprints_db = {}

        for role_name in target_roles:
            if cancel_check is not None and cancel_check():
                raise CancelledError("分配图纸生成已取消")
            role_data = self.roles_db[role_name]
            set_name = self._resolve_set_name(custom_sets.get(role_name, role_data["default_set"]))

            set_shapes = self.sets_db[set_name]["shapes"]
            extra_label = role_data["extra_shape_label"]
            board_matrix = self._board_matrices[role_name]
            set_effect_mode = normalize_set_effect_mode(set_effect_modes.get(role_name))
            set_piece_options = set_piece_options_for_mode(set_shapes, set_effect_mode)
            cache_key = self._blueprint_cache_key(
                role_name,
                set_name,
                set_shapes,
                extra_label,
                board_matrix,
                set_effect_mode,
                include_layout_variants,
            )

            logger.info(f"  -> [{role_name}] 套装: {set_name} | 套装效果: {set_effect_mode} | 求解中...")
            _t0 = time.perf_counter()
            if self._timings is not None:
                self._timings.note("blueprints.python_cache", cache_hit_count=int(cache_key in self._blueprint_cache),
                                   cache_miss_count=int(cache_key not in self._blueprint_cache))
            if cache_key in self._blueprint_cache:
                real_blueprints_db[role_name] = copy.deepcopy(self._blueprint_cache[cache_key])
                logger.info(f"  [{role_name}] 图纸缓存命中，共 {len(real_blueprints_db[role_name])} 套合法方案。")
                continue
            role_blueprints = []
            layout_cache: dict[tuple[str, ...], list] = {}
            fill_cache: dict[int, list[list[str]]] = {}

            for set_pieces in set_piece_options:
                if cancel_check is not None and cancel_check():
                    raise CancelledError("分配图纸生成已取消")
                set_area = sum(self.shapes_db[shape].area for shape in set_pieces)
                if set_area not in fill_cache:
                    fill_cache[set_area] = combinatorics.generate_piece_combinations(
                        set_pieces, extra_label,
                        only_max_extra=set_effect_mode != "two_piece",
                        cancel_check=cancel_check,
                    )
                combos = fill_cache[set_area]
                logger.info(f"     套装形状: {len(set_pieces)} | 组合数: {len(combos)} | 耗时: {time.perf_counter()-_t0:.2f}s")

                for combo in combos:
                    if cancel_check is not None and cancel_check():
                        raise CancelledError("分配图纸生成已取消")
                    pieces_to_place = set_pieces + combo
                    geometry = tuple(sorted(pieces_to_place))
                    if geometry not in layout_cache:
                        board_copy = [row[:] for row in board_matrix]
                        results = []
                        dfs_solver.solve(
                            board_copy,
                            pieces_to_place,
                            results,
                            max_solutions=0 if include_layout_variants else 1,
                            cancel_check=cancel_check,
                        )
                        layout_cache[geometry] = results

                    for result_board in layout_cache[geometry]:
                        if cancel_check is not None and cancel_check():
                            raise CancelledError("分配图纸生成已取消")
                        role_blueprints.append({
                            "set_pieces": list(set_pieces),
                            "extra_pieces": list(combo),
                            "set_effect_mode": set_effect_mode,
                            "board": copy.deepcopy(result_board),
                        })

            if not include_layout_variants:
                role_blueprints = dedupe_blueprints_by_piece_signature(role_blueprints)
            if cancel_check is not None and cancel_check():
                raise CancelledError("分配图纸生成已取消")
            real_blueprints_db[role_name] = role_blueprints
            self._remember_blueprint_cache(cache_key, role_blueprints)
            logger.success(f"  [{role_name}] 图纸求解完成，共 {len(role_blueprints)} 套合法方案。(总耗时 {time.perf_counter()-_t0:.2f}s)")

        return real_blueprints_db

    def blueprint_request(self, roles, custom_sets, modes, include_layout_variants=False):
        """只投影冻结盘面和正式形状；搜索、排序和合法摆盘由注入的计算核拥有。"""
        return {
            "version": 1, "include_layout_variants": include_layout_variants,
            "roles": [{"role": name, "board": copy.deepcopy(self._board_matrices[name]),
                       "set_shapes": list(self.sets_db[self._resolve_set_name(
                           custom_sets.get(name, self.roles_db[name]["default_set"]))]["shapes"]),
                       "extra_label": self.roles_db[name]["extra_shape_label"],
                       "set_effect_mode": normalize_set_effect_mode(modes.get(name))} for name in roles],
            "shapes": [shape.model_dump() for shape in self.shapes_db.values() if shape.shape_id != "TAPE_15"],
        }

    def _solve_native_blueprints(self, roles, custom_sets, modes, variants, cancelled, *, executor=None):
        if cancelled is not None and cancelled():
            raise CancelledError("分配图纸生成已取消")
        request = self.blueprint_request(roles, custom_sets, modes, variants)
        result, missing, keys = {}, [], {}
        for row in request["roles"]:
            if cancelled is not None and cancelled():
                raise CancelledError("分配图纸生成已取消")
            # key 含形状顺序、盘面和模式；与 Python 缓存隔离，不接受截断或失败的响应。
            name = row["role"]
            keys[name] = "native:" + json.dumps({"role": row, "shapes": request["shapes"],
                                               "variants": variants}, ensure_ascii=False)
            if self._timings is not None:
                self._timings.note("blueprints.native_cache", cache_hit_count=int(keys[name] in self._blueprint_cache),
                                   cache_miss_count=int(keys[name] not in self._blueprint_cache))
            if keys[name] in self._blueprint_cache:
                result[name] = timed_call(self._timings, "blueprints.cache_copy", copy.deepcopy, self._blueprint_cache[keys[name]])
            else:
                missing.append(row)
        if missing:
            generated = timed_call(self._timings, "blueprints.native_generate", executor or self._blueprint_executor,
                                   {**request, "roles": missing})
            if cancelled is not None and cancelled():
                raise CancelledError("分配图纸生成已取消")
            for row in missing:
                name = row["role"]
                result[name] = copy.deepcopy(generated[name])
                self._remember_blueprint_cache(keys[name], result[name])
        return {name: result[name] for name in roles}

    def _blueprint_cache_key(
        self,
        role_name: str,
        set_name: str,
        set_shapes: List[str],
        extra_label: str,
        board_matrix: List[List[int]],
        set_effect_mode: str,
        include_layout_variants: bool,
    ) -> str:
        shape_payload = {
            shape_id: {"area": shape.area, "label": shape.label}
            for shape_id, shape in sorted(self.shapes_db.items())
        }
        payload = {
            "role": role_name,
            "set": set_name,
            "set_shapes": list(set_shapes or []),
            "extra_label": extra_label,
            "board_matrix": board_matrix,
            "set_effect_mode": set_effect_mode,
            "include_layout_variants": include_layout_variants,
            "shapes": shape_payload,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _remember_blueprint_cache(self, cache_key: str, blueprints: List[Dict]) -> None:
        if len(self._blueprint_cache) >= self._blueprint_cache_limit:
            self._blueprint_cache.pop(next(iter(self._blueprint_cache)))
        self._blueprint_cache[cache_key] = copy.deepcopy(blueprints)

    def _max_priority_group_size(self, priority_list: List[str], priority_groups: List[List[str]] | None) -> int:
        selected = set(priority_list or [])
        max_size = 1
        for group in priority_groups or []:
            size = len([role for role in group or [] if role in selected])
            max_size = max(max_size, size)
        return max_size

    @timed("allocation.pipeline")
    def run_full_allocation(self, inventory: List[Dict], priority_list: List[str],
                            custom_sets: Dict[str, str] = None, mode: str = "role_priority",
                            locked_uids: set = None, tape_main_filters: Dict[str, List[str]] = None,
                            crit_priority_modes: Dict[str, str] = None, set_effect_modes: Dict[str, str] = None,
                            priority_groups: List[List[str]] = None, crit_rate_caps: Dict[str, float | None] = None,
                            crit_rate_baselines: Dict[str, float] = None,
                            custom_weapons: Dict[str, str] = None,
                            blueprint_combo_limit: int = 2000,
                            cancel_check=None, *, allocation_executor, blueprint_executor=None, timings=None):
        locked_uids = locked_uids or set()
        tape_main_filters = tape_main_filters or {}
        crit_priority_modes = crit_priority_modes or {}
        crit_rate_caps = crit_rate_caps or {}
        crit_rate_baselines = crit_rate_baselines or {}
        set_effect_modes = set_effect_modes or {}
        custom_weapons = custom_weapons or {}
        priority_groups = priority_groups or None
        if mode != "role_priority":
            tape_main_filters = {}
            crit_priority_modes = {}
            crit_rate_caps = {}
        for role_name in priority_list:
            if role_name not in self.roles_db:
                continue
            role = dict(self.roles_db[role_name])
            selected_fork_name = custom_weapons.get(role_name)
            if selected_fork_name:
                fork_id = self._fork_ids_by_name.get(selected_fork_name)
                if not fork_id:
                    raise ValueError(
                        f"角色 [{role_name}] 选择的弧盘名称不能唯一对应正式弧盘 ID"
                    )  # ui-text: internal
            else:
                fork_id = str(role.get("default_fork_id") or "")
            role["effective_fork_id"] = fork_id
            role.pop("fork_crit_rate", None)
            role.pop("non_equipment_crit_rate", None)
            role["crit_source_known"] = False
            role["automatic_crit_cap_source_unknown"] = (
                role_name in crit_rate_caps and crit_rate_caps[role_name] is None
            )
            if role_name in crit_rate_baselines:
                try:
                    role["non_equipment_crit_rate"] = max(
                        0.0, float(crit_rate_baselines[role_name])
                    )
                    role["crit_source_known"] = True
                except (TypeError, ValueError):
                    pass
            self.roles_db[role_name] = role
        custom_sets = self._canonicalize_custom_sets(custom_sets)
        total_t0 = time.perf_counter()
        logger.info(f"\n[阶段 1] 开始完整分配流程 | 库存: {len(inventory)} | 角色: {priority_list} | 模式: {mode}")
        stage_t0 = time.perf_counter()
        if cancel_check is not None and cancel_check():
            raise CancelledError("分配图纸生成已取消")
        if blueprint_executor is None:
            # 非伤害策略与尚未迁移的独立调用继续使用原入口；伤害策略显式注入 Rust，无失败回退。
            blueprints_db = timed_call(timings, "allocation.python_blueprints", self.solve_blueprints,
                priority_list, custom_sets, set_effect_modes, cancel_check=cancel_check,
            )
        elif getattr(blueprint_executor, "deferred", False):
            # 这里只冻结身份、盘面和形状事实；组合、排序、摆盘与预算由 Rust 执行。
            blueprints_db = timed_call(timings, "allocation.deferred_blueprints", blueprint_executor,
                self.blueprint_request(priority_list, custom_sets, set_effect_modes))
        else:
            # 普通模式复用现有有界原生图纸缓存；失败/预算截断的结果不入缓存，
            # 不能让正式接线退化成每次重复启动一个完整图纸作业。
            blueprints_db = timed_call(
                timings, "allocation.native_blueprints", self._solve_native_blueprints,
                priority_list, custom_sets, set_effect_modes, False, cancel_check,
                executor=blueprint_executor,
            )
        if cancel_check is not None and cancel_check():
            raise CancelledError("分配图纸生成已取消")
        logger.info(f"[计时] 图纸求解阶段: {time.perf_counter() - stage_t0:.2f}s")

        logger.info(f"\n[阶段 3] 接收到 {len(inventory)} 个资产，正在过滤与类型转换...")
        stage_t0 = time.perf_counter()
        parsed_inventory = []
        filtered_count = 0

        for item in inventory:
            item = normalize_equipment_item(item)
            obj = Drive(**item) if item.get("item_type") == "drive" else Tape(**item)

            # Skip equipment already worn by other characters
            if obj.uid in locked_uids:
                filtered_count += 1
                continue

            parsed_inventory.append(obj)

        if locked_uids:
            logger.info(
                f"[模式四] 已屏蔽 {filtered_count} 件锁定装备，使用剩余 {len(parsed_inventory)} 件进行分配。")
        if timings is not None:
            timings.record("allocation.inventory_convert", (time.perf_counter() - stage_t0) * 1000,
                           item_count=len(parsed_inventory))
        logger.info(f"[计时] 库存转换阶段: {time.perf_counter() - stage_t0:.2f}s")

        stage_t0 = time.perf_counter()
        scoring_engine = timed_call(timings, "allocation.scorer_prepare", ScoringEngine,
            config_dir=self.config_dir,
            user_database_path=self.user_database_path,
            roles_db=self.roles_db,
            # 评分器必须绑定本次分配冻结的静态发行库；缺省路径可能落到随附主库，
            # 在 reference/role_page 目录下会把同名 ID 解释成另一套数据。
            static_database_path=self.static_database_path,
        )
        # 完整 Rust 作业还未生成图纸，临时值不参与求解；需求上限在原生生成后统一计算。
        drive_screen_limit, tape_screen_limit = (15, 6) if getattr(blueprint_executor, "deferred", False) else estimate_candidate_pool_limits(
            blueprints_db, priority_list, priority_groups or (),
        )
        if drive_screen_limit > 15:
            logger.info(f"  候选驱动筛选上限已按当前角色需求提升到 Top {drive_screen_limit}/形状/角色。")
        kernel_request = AllocationKernelRequest(
            inventory=tuple(parsed_inventory), roles_db=self.roles_db, sets_db=self.sets_db,
            shapes_db=self.shapes_db, blueprints_db=blueprints_db, role_order=tuple(priority_list),
            strategy=mode, module_set_targets=custom_sets, set_effect_modes=set_effect_modes,
            core_main_filters={key: tuple(value) for key, value in tape_main_filters.items()},
            core_set_targets={}, stat_priority_configs=crit_priority_modes, property_limits={},
            priority_groups=tuple(tuple(group) for group in (priority_groups or ())),
            crit_rate_caps=crit_rate_caps, drive_screen_limit=drive_screen_limit,
            tape_screen_limit=tape_screen_limit,
            # 缺少卡带只表示本次推荐没有核心，不能否定已经完整填满图纸的驱动方案。
            # 自动/极速装配仍会拒绝缺少核心的保存方案。
            allow_missing_core=True,
            blueprint_combo_limit=int(blueprint_combo_limit),
            cancel_check=cancel_check,
        )
        if tape_main_filters:
            logger.info("  已按角色优先级配置提前过滤卡带主词条。")

        logger.success("  筛选输入已准备完成。")
        logger.info(f"     - 卡带分桶: 各角色每套装 Top {tape_screen_limit} 已锁定")
        logger.info(f"[计时] 评分筛选阶段: {time.perf_counter() - stage_t0:.2f}s")

        logger.info(f"\n[阶段 4] 启动调度模式: [{mode}]...")
        stage_t0 = time.perf_counter()
        final_plan = timed_call(timings, "allocation.dispatch", allocation_executor, kernel_request, scoring_engine)
        logger.info(f"[计时] 调度阶段: {time.perf_counter() - stage_t0:.2f}s")

        # 日志只消费已生成摘要，逐件评级会额外启动评分进程，且不能成为求解完成的前置步骤。
        logger.info("分配结果已生成：角色数={}", len(final_plan))
        for role_name, plan in final_plan.items():
            if isinstance(plan, dict) and custom_weapons.get(role_name):
                plan[PLAN_CUSTOM_WEAPON] = custom_weapons[role_name]
        logger.info(f"[计时] 完整分配流程总耗时: {time.perf_counter() - total_t0:.2f}s")

        return final_plan
