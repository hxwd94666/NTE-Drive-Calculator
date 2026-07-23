# 统筹库存、评分和求解器生成最终配装。
"""End-to-end pipeline for blueprints, scoring, dispatch, and output."""

import copy
import json
import os
import time
from typing import List, Dict

from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.optimizer.contracts import PLAN_CUSTOM_WEAPON
from src.domain.equipment_normalizer import normalize_equipment_item
from src.models.equipment import DriveShape, Drive, Tape
from src.solver.combinatorics import PuzzleCombinatorics
from src.solver.dfs_puzzle import DFSPuzzleSolver
from src.solver.blueprint_utils import dedupe_blueprints_by_piece_signature
from src.solver.set_effects import normalize_set_effect_mode, set_piece_options_for_mode
from src.optimizer.scoring import ScoringEngine
from src.optimizer.allocation_kernel import AllocationKernel, AllocationKernelRequest, estimate_candidate_pool_limits
from src.utils.visualizer import BoardVisualizer
from src.utils.logger import logger
from src.utils.name_resolver import resolve_name
from src.utils.set_name import normalize_set_display_name
from src.services.sqlite_allocation_inventory import legacy_shape_id
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


_LEGACY_SHAPE_LABELS = {
    "H_2": "Type-2", "V_2": "Type-2",
    "H_3": "Type-3", "V_3": "Type-3",
    "L_3_BL": "Type-3", "L_3_TL": "Type-3", "L_3_TR": "Type-3", "L_3_BR": "Type-3",
    "H_4": "Type-4", "V_4": "Type-4", "Trap_4_H": "Type-4", "Trap_4_V": "Type-4",
}


class NTEPipelineOrchestrator:
    _blueprint_cache: dict[str, List[Dict]] = {}
    _blueprint_cache_limit = 256

    def __init__(self, config_dir: str = "config"):
        self.config_dir = config_dir
        self.roles_db = {}
        self.sets_db = {}
        self.shapes_db = {}
        self._board_matrices = {}
        self._load_configs()

    @classmethod
    def from_frozen_inputs(
        cls, *, roles_db: dict, sets_db: dict, shapes_db: dict[str, DriveShape], config_dir: str = "config",
    ) -> "NTEPipelineOrchestrator":
        """Create a puzzle-only orchestrator without rereading mutable config.

        Context callers already copied every role, suit and shape input.  This
        named constructor keeps that boundary explicit instead of having an
        adapter bypass ``__init__`` and patch private attributes afterwards.
        """

        instance = cls.__new__(cls)
        instance.config_dir = config_dir
        instance.roles_db = roles_db
        instance.sets_db = sets_db
        instance.shapes_db = shapes_db
        instance._board_matrices = {
            role_name: role_data["board_matrix"]
            for role_name, role_data in roles_db.items()
        }
        instance._blueprint_cache = {}
        return instance

    def _load_configs(self):
        with open(os.path.join(self.config_dir, "roles.json"), "r", encoding="utf-8") as f:
            self.roles_db = json.load(f)
        with open(os.path.join(self.config_dir, "sets.json"), "r", encoding="utf-8") as f:
            self.sets_db = json.load(f)["sets"]
        with StaticGameDataDao() as static_dao:
            for shape in static_dao.list_shapes():
                cells = list(shape.get("cells") or [])
                xs = [int(cell["x"]) for cell in cells]
                ys = [int(cell["y"]) for cell in cells]
                if not xs or not ys:
                    continue
                legacy_id = legacy_shape_id(shape["shape_id"])
                matrix = [[0] * (max(ys) - min(ys) + 1) for _ in range(max(xs) - min(xs) + 1)]
                for cell in cells:
                    matrix[int(cell["x"]) - min(xs)][int(cell["y"]) - min(ys)] = 1
                self.shapes_db[legacy_id] = DriveShape(
                    shape_id=legacy_id,
                    label=_LEGACY_SHAPE_LABELS[legacy_id],
                    matrix=matrix,
                    area=int(shape["cell_count"]),
                    description=str(shape["shape_id"]),
                )
            characters = {
                str(row.get("name_zh")): int(row.get("canonical_character_id") or row["character_id"])
                for row in static_dao.list_characters()
            }
            for role_name in self.roles_db:
                character_id = characters.get(str(role_name))
                if str(role_name) == "主角":
                    plan = next(
                        (static_dao.get_equipment_plan(candidate_id) for candidate_id in (1046, 1051)
                         if static_dao.get_equipment_plan(candidate_id) is not None),
                        None,
                    )
                else:
                    plan = static_dao.get_equipment_plan(character_id) if character_id is not None else None
                if plan is None:
                    raise ValueError(f"角色 [{role_name}] 缺少官方 SQLite 底盘图纸")
                default_suit = static_dao.get_character_default_suit(
                    int(plan["character_id"])
                )
                if default_suit is not None:
                    self.roles_db[role_name]["default_set"] = self._resolve_set_name(
                        str(default_suit["suit_name_zh"])
                    )
                board = [[-1] * 5 for _ in range(5)]
                for cell in plan.get("cells") or []:
                    board[int(cell["row"]) - 1][int(cell["column"]) - 1] = 0
                self._board_matrices[role_name] = board
        self._canonicalize_role_sets()

    def _resolve_set_name(self, set_name: str) -> str:
        normalized_name = normalize_set_display_name(set_name)
        resolved = resolve_name(normalized_name, self.sets_db.keys(), cutoff=0.78)
        if not resolved:
            available = "、".join(self.sets_db.keys())
            raise ValueError(f"错误：指定的套装 {set_name} 不存在于 sets.json 中！可用套装：{available}")
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
                         include_layout_variants: bool = False) -> Dict[str, List[Dict]]:
        custom_sets = self._canonicalize_custom_sets(custom_sets)
        set_effect_modes = set_effect_modes or {}
        logger.info(f"\n[阶段 2] 求解 {target_roles} 的合法底盘图纸...")
        combinatorics = PuzzleCombinatorics(self.shapes_db)
        dfs_solver = DFSPuzzleSolver(self.shapes_db)
        real_blueprints_db = {}

        for role_name in target_roles:
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
            if cache_key in self._blueprint_cache:
                real_blueprints_db[role_name] = copy.deepcopy(self._blueprint_cache[cache_key])
                logger.info(f"  [{role_name}] 图纸缓存命中，共 {len(real_blueprints_db[role_name])} 套合法方案。")
                continue
            role_blueprints = []

            for set_pieces in set_piece_options:
                combos = combinatorics.generate_piece_combinations(set_pieces, extra_label)
                logger.info(f"     套装形状: {len(set_pieces)} | 组合数: {len(combos)} | 耗时: {time.perf_counter()-_t0:.2f}s")

                for combo in combos:
                    pieces_to_place = set_pieces + combo
                    board_copy = [row[:] for row in board_matrix]
                    results = []
                    dfs_solver.solve(
                        board_copy,
                        pieces_to_place,
                        results,
                        max_solutions=0 if include_layout_variants else 1,
                    )

                    for result_board in results:
                        role_blueprints.append({
                            "set_pieces": list(set_pieces),
                            "extra_pieces": combo,
                            "set_effect_mode": set_effect_mode,
                            "board": result_board,
                        })

            if not include_layout_variants:
                role_blueprints = dedupe_blueprints_by_piece_signature(role_blueprints)
            real_blueprints_db[role_name] = role_blueprints
            self._remember_blueprint_cache(cache_key, role_blueprints)
            logger.success(f"  [{role_name}] 图纸求解完成，共 {len(role_blueprints)} 套合法方案。(总耗时 {time.perf_counter()-_t0:.2f}s)")

        return real_blueprints_db

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

    def run_full_allocation(self, inventory: List[Dict], priority_list: List[str],
                            custom_sets: Dict[str, str] = None, mode: str = "role_priority",
                            locked_uids: set = None, tape_main_filters: Dict[str, List[str]] = None,
                            crit_priority_modes: Dict[str, str] = None, set_effect_modes: Dict[str, str] = None,
                            priority_groups: List[List[str]] = None, crit_rate_caps: Dict[str, float] = None,
                            custom_weapons: Dict[str, str] = None):
        locked_uids = locked_uids or set()
        tape_main_filters = tape_main_filters or {}
        crit_priority_modes = crit_priority_modes or {}
        crit_rate_caps = crit_rate_caps or {}
        set_effect_modes = set_effect_modes or {}
        custom_weapons = custom_weapons or {}
        priority_groups = priority_groups or None
        if mode != "role_priority":
            tape_main_filters = {}
            crit_priority_modes = {}
            crit_rate_caps = {}
        custom_sets = self._canonicalize_custom_sets(custom_sets)
        total_t0 = time.perf_counter()
        logger.info(f"\n[阶段 1] 开始完整分配流程 | 库存: {len(inventory)} | 角色: {priority_list} | 模式: {mode}")
        stage_t0 = time.perf_counter()
        blueprints_db = self.solve_blueprints(priority_list, custom_sets, set_effect_modes)
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
        logger.info(f"[计时] 库存转换阶段: {time.perf_counter() - stage_t0:.2f}s")

        stage_t0 = time.perf_counter()
        scoring_engine = ScoringEngine(config_dir=self.config_dir)
        drive_screen_limit, tape_screen_limit = estimate_candidate_pool_limits(
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
        )
        if tape_main_filters:
            logger.info("  已按角色优先级配置提前过滤卡带主词条。")

        logger.success("  筛选输入已准备完成。")
        logger.info(f"     - 卡带分桶: 各角色每套装 Top {tape_screen_limit} 已锁定")
        logger.info(f"[计时] 评分筛选阶段: {time.perf_counter() - stage_t0:.2f}s")

        logger.info(f"\n[阶段 4] 启动调度模式: [{mode}]...")
        stage_t0 = time.perf_counter()
        final_plan = AllocationKernel(scoring_engine).execute(kernel_request)
        logger.info(f"[计时] 调度阶段: {time.perf_counter() - stage_t0:.2f}s")

        stage_t0 = time.perf_counter()
        self._render_results(final_plan, scoring_engine, custom_sets)
        logger.info(f"[计时] 日志渲染阶段: {time.perf_counter() - stage_t0:.2f}s")
        for role_name, plan in final_plan.items():
            if isinstance(plan, dict) and custom_weapons.get(role_name):
                plan[PLAN_CUSTOM_WEAPON] = custom_weapons[role_name]
        logger.info(f"[计时] 完整分配流程总耗时: {time.perf_counter() - total_t0:.2f}s")

        return final_plan

    def _render_results(self, final_plan: Dict, scoring_engine: ScoringEngine, custom_sets: Dict[str, str]):
        custom_sets = custom_sets or {}
        for role, plan in final_plan.items():
            if not plan or not plan.get("valid", True):
                logger.error(f"角色 [{role}] 分配失败: 无法凑齐合法图纸。\n")
                continue

            grade = scoring_engine.get_grade_tag(plan['score'], area=ALLOCATION_TOTAL_SCORE_AREA)
            used_set = custom_sets.get(role, self.roles_db[role]["default_set"])

            BoardVisualizer.display_final_plan(role_name=role, plan=plan, default_set=used_set, grade=grade)

            logger.opt(raw=True).info("  [卡带分配]\n")
            assigned_tape: Tape = plan.get("assigned_tape")
            if assigned_tape:
                t_score = assigned_tape.role_scores.get(role, 0.0)
                t_grade = scoring_engine.get_grade_tag(t_score, area=15)
                logger.opt(raw=True).info(f"     - {assigned_tape.set_name.ljust(8)} | "
                                          f"评级:[{t_grade.ljust(3)}] | "
                                          f"总得分:{str(t_score).ljust(6)} | "
                                          f"品质:{assigned_tape.quality.ljust(5)} |\n"
                                          f"       主词条: {assigned_tape.main_stats}\n"
                                          f"       副词条: {assigned_tape.sub_stats}\n")
            else:
                logger.warning("     - 未为此角色分配合法卡带。")

            for category, key in [("\n  [套装效果驱动]\n", 'assigned_set_drives'),
                                  ("  [额外散件]\n", 'assigned_extra_drives')]:
                logger.opt(raw=True).info(f"{category}")
                for d in plan.get(key, []):
                    score = d.role_scores.get(role, 0.0)
                    d_grade = scoring_engine.get_grade_tag(score, d.area)

                    mvp_tag = f" [先选: 第 {d.pick_order} 顺位]" if d.is_mvp else ""

                    logger.opt(raw=True).info(f"     - {d.shape_id.ljust(10)} | "
                                              f"评级:[{d_grade.ljust(3)}] | "
                                              f"得分:{str(score).ljust(5)} | "
                                              f"品质:{d.quality.ljust(5)} | "
                                              f"数值:{d.sub_stats}{mvp_tag}\n")
            logger.opt(raw=True).info("\n" + "=" * 60 + "\n")
