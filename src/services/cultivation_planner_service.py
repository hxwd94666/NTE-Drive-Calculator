# 编排账号养成目标的材料计算计划。
"""Account-aware, Qt-free material planning for the toolbox cultivation calculator."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.domain.progression_material_conversion import allocate_owned, lower_tier_ids
from src.domain.progression_stamina import (
    FarmingStage,
    ProgressionStaminaResult,
)
from src.domain.role_name_order import role_name_sort_key

from src.services.character_progression_requirements import (
    CharacterLevelMaterialProjection,
    CharacterMaterialRequirement,
    MaterialSummaryStatus,
    ProgressionRequirementGap,
    project_character_level_requirements,
    project_skill_level_requirements,
)
from src.services.fork_progression_requirements import (
    ForkLevelMaterialProjection,
    project_fork_level_requirements,
)
from src.services.cultivation_stamina_planner import (
    calculate_stamina_result,
    normalize_owned_quantities,
    stamina_material_ids,
)
from src.services.cultivation_legacy_gold_projection import (
    correct_legacy_costs,
    correct_legacy_stages,
    has_legacy_gold_alias,
)
from src.services.static_catalog_character_models import (
    CharacterBreakthroughRequirement,
    CharacterDetail,
    CharacterSkill,
)
from src.services.static_catalog_character_service import StaticCatalogCharacterService
from src.services.static_catalog_fork_service import (
    ForkCatalogDetail,
    StaticCatalogForkService,
)
from src.services.static_catalog_terminology_service import StaticCatalogTerminologyService
from src.storage.sqlite.static_catalog_character_queries import (
    StaticCatalogCharacterQueries,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.native_role_profile_projection import load_template_growth_defaults, project_native_role_profile


@dataclass(frozen=True, slots=True)
class CultivationRole:
    character_id: int
    name: str


@dataclass(frozen=True, slots=True)
class CultivationFork:
    fork_id: str
    name: str
    quality: str


@dataclass(frozen=True, slots=True)
class CultivationForkSeed:
    fork_id: str
    fork_name: str
    current_level: int
    current_breakthrough_stage: int


@dataclass(frozen=True, slots=True)
class CultivationSkill:
    skill_id: str
    category: str
    name: str
    current_level: int
    maximum_level: int


@dataclass(frozen=True, slots=True)
class CultivationSeed:
    character_id: int
    character_name: str
    current_level: int
    current_breakthrough_stage: int
    skills: tuple[CultivationSkill, ...]
    fork: CultivationForkSeed | None


@dataclass(frozen=True, slots=True)
class CultivationSkillTarget:
    skill_id: str
    current_level: int
    target_level: int


@dataclass(frozen=True, slots=True)
class CultivationForkTarget:
    fork_id: str
    current_level: int
    current_breakthrough_stage: int
    target_level: int
    target_breakthrough_stage: int


@dataclass(frozen=True, slots=True)
class CultivationRequest:
    character_id: int
    current_level: int
    current_breakthrough_stage: int
    target_level: int
    target_breakthrough_stage: int
    skills: tuple[CultivationSkillTarget, ...]
    include_character_progression: bool = True
    include_skills: bool = True
    fork: CultivationForkTarget | None = None


@dataclass(frozen=True, slots=True)
class CultivationMaterial:
    item_id: str
    name: str
    quantity: int
    quality: str | None = None
    icon_path: str | None = None


@dataclass(frozen=True, slots=True)
class CultivationSection:
    label: str
    materials: tuple[CultivationMaterial, ...]
    description: str | None = None


@dataclass(frozen=True, slots=True)
class CultivationPlan:
    character_name: str
    status: MaterialSummaryStatus
    sections: tuple[CultivationSection, ...]
    totals: tuple[CultivationMaterial, ...]
    required_experience: int
    experience_overflow: int
    included_breakthrough_stages: tuple[int, ...]
    gaps: tuple[ProgressionRequirementGap, ...]
    fork_required_experience: int = 0
    fork_experience_overflow: int = 0
    fork_included_breakthrough_stages: tuple[int, ...] = ()
    owned_inputs: tuple[CultivationMaterial, ...] = ()


@dataclass(frozen=True, slots=True)
class CultivationSectionStamina:
    label: str
    result: ProgressionStaminaResult


@dataclass(frozen=True, slots=True)
class CultivationStaminaPlan:
    total: ProgressionStaminaResult
    sections: tuple[CultivationSectionStamina, ...]
    stamina_item_ids: frozenset[str] = frozenset()


class CultivationPlannerService:
    """Read formal material costs and current role-page values without persisting a plan."""

    def __init__(
        self,
        *,
        static_database_path: str | Path,
        user_database_path: str | Path,
        character_queries_factory: Callable[[Path], StaticCatalogCharacterQueries] = StaticCatalogCharacterQueries,
        terminology_dao_factory: Callable[[Path], StaticGameDataDao] = StaticGameDataDao,
        user_dao_factory: Callable[[Path], UserDataDao] = UserDataDao,
    ) -> None:
        self._static_database_path = Path(static_database_path).resolve()
        self._user_database_path = Path(user_database_path).resolve()
        self._character_queries_factory = character_queries_factory
        self._terminology_dao_factory = terminology_dao_factory
        self._user_dao_factory = user_dao_factory

    def list_roles(self) -> tuple[CultivationRole, ...]:
        """Return formal roles in first-character A–Z order for both pickers."""

        queries = self._character_queries_factory(self._static_database_path)
        try:
            page = StaticCatalogCharacterService(queries).list_characters(limit=200)
            return _deduplicate_roles(
                CultivationRole(item.character_id, item.name_zh)
                for item in page.items
            )
        finally:
            queries.close()

    def list_forks(self) -> tuple[CultivationFork, ...]:
        """Return every formal fork so the UI can present image-card selection."""

        service = StaticCatalogForkService.from_database(self._static_database_path)
        try:
            page = service.list_forks(page_size=200)
            return tuple(
                CultivationFork(item.fork_id, item.name_zh, item.quality)
                for item in page.items
            )
        finally:
            service.close()

    def load_seed(self, character_id: int) -> CultivationSeed:
        """Combine static skill limits with the current account's saved role-page state."""

        detail = self._load_detail(character_id)
        profile = self._load_profile(character_id) or {}
        saved_levels = profile.get("skill_levels")
        level_by_skill = saved_levels if isinstance(saved_levels, dict) else {}
        skills = tuple(
            CultivationSkill(
                skill_id=skill.skill_id,
                category=_skill_category(skill),
                name=skill.name_zh or skill.skill_id,
                current_level=_skill_level(level_by_skill.get(skill.skill_id), skill),
                maximum_level=_skill_maximum_level(skill),
            )
            for skill in detail.skills
            if skill.levels
        )
        return CultivationSeed(
            character_id=detail.character.character_id,
            character_name=detail.character.name_zh,
            current_level=_integer_in_range(profile.get("character_level"), 1, 80, 1),
            current_breakthrough_stage=_integer_in_range(
                profile.get("breakthrough_stage"), 0, 6, 0
            ),
            skills=skills,
            fork=self._saved_fork_seed(profile),
        )

    def load_fork_seed(
        self,
        fork_id: str,
        *,
        character_id: int | None = None,
    ) -> CultivationForkSeed:
        """Load formal limits, prefilling only the selected role's matching fork."""

        profile = self._load_profile(character_id) if character_id is not None else None
        if str((profile or {}).get("fork_id") or "") != str(fork_id):
            profile = None
        return self._fork_seed(fork_id, profile)

    def calculate(self, request: CultivationRequest) -> CultivationPlan:
        """Merge requested character and skill upgrades into a single material list."""

        detail = self._load_detail(request.character_id)
        progression = detail.progression
        if progression is None:
            return CultivationPlan(
                character_name=detail.character.name_zh,
                status=MaterialSummaryStatus.UNAVAILABLE,
                sections=(),
                totals=(),
                required_experience=0,
                experience_overflow=0,
                included_breakthrough_stages=(),
                gaps=(ProgressionRequirementGap("character_progression_unavailable"),),
            )
        if request.include_character_progression:
            self._validate_character_interval(request, progression.breakthrough_stages)
        terminology_dao = self._terminology_dao_factory(self._static_database_path)
        try:
            terminology = StaticCatalogTerminologyService(terminology_dao)
            level_projection = (
                project_character_level_requirements(
                    progression,
                    from_level=request.current_level,
                    to_level=request.target_level,
                    include_breakthroughs=False,
                )
                if request.include_character_progression
                and request.current_level < request.target_level
                else CharacterLevelMaterialProjection(
                    status=MaterialSummaryStatus.COMPLETE,
                    required_experience=0,
                    experience_overflow=0,
                    experience_books=(),
                    breakthrough_materials=(),
                    additional_costs=(),
                    included_breakthrough_stages=(),
                    gaps=(),
                )
            )
            breakthrough_requirements, stages = (
                _breakthrough_requirements(
                    progression.breakthrough_stages,
                    current_level=request.current_level,
                    current_stage=request.current_breakthrough_stage,
                    target_level=request.target_level,
                    target_stage=request.target_breakthrough_stage,
                )
                if request.include_character_progression else ((), ())
            )
            sections: list[
                tuple[str, tuple[CharacterMaterialRequirement, ...], str | None]
            ] = []
            level_requirements = (*level_projection.experience_books, *level_projection.additional_costs)
            if level_requirements:
                sections.append(("角色升级", level_requirements, None))
            if breakthrough_requirements:
                sections.append(("角色突破", breakthrough_requirements, None))
            gaps = list(level_projection.gaps)
            by_skill = {skill.skill_id: skill for skill in detail.skills}
            for target in (request.skills if request.include_skills else ()):
                skill = by_skill.get(target.skill_id)
                if skill is None:
                    gaps.append(ProgressionRequirementGap(
                        reason_code="skill_not_available", item_id=target.skill_id,
                    ))
                    continue
                maximum = _skill_maximum_level(skill)
                if not 1 <= target.current_level <= maximum:
                    raise ValueError(f"{skill.name_zh or skill.skill_id} 当前等级无效")
                if not target.current_level <= target.target_level <= maximum:
                    raise ValueError(f"{skill.name_zh or skill.skill_id} 目标等级无效")
                if target.current_level == target.target_level:
                    continue
                projection = project_skill_level_requirements(
                    skill,
                    from_level=target.current_level,
                    to_level=target.target_level,
                    terminology=terminology,
                )
                gaps.extend(projection.gaps)
                if projection.requirements:
                    sections.append((
                        f"{_skill_category(skill)} · {skill.name_zh or skill.skill_id}",
                        projection.requirements,
                        None,
                    ))
            fork_required_experience = 0
            fork_experience_overflow = 0
            fork_included_stages: tuple[int, ...] = ()
            if request.fork is not None:
                fork_sections, fork_projection = self._fork_sections(request.fork)
                sections.extend(fork_sections)
                gaps.extend(fork_projection.gaps)
                fork_required_experience = fork_projection.required_experience
                fork_experience_overflow = fork_projection.experience_overflow
                fork_included_stages = (
                    fork_projection.included_breakthrough_stages
                )
            if has_legacy_gold_alias(terminology_dao):
                sections = [
                    (label, correct_legacy_costs(requirements), description)
                    for label, requirements, description in sections
                ]
            item_ids = tuple(dict.fromkeys(
                item.item_id
                for _label, requirements, _description in sections
                for item in requirements
            ))
            input_ids = tuple(dict.fromkeys((
                *item_ids,
                *(lower for item_id in item_ids for lower in lower_tier_ids(item_id)),
            )))
            item_metadata = {
                str(row["item_id"]): row
                for row in terminology_dao.list_progression_items(input_ids)
            }
            rendered_sections = tuple(
                CultivationSection(
                    label,
                    self._materials(requirements, terminology, item_metadata),
                    description,
                )
                for label, requirements, description in sections
            )
            merged: dict[str, int] = defaultdict(int)
            for _label, requirements, _description in sections:
                for material in requirements:
                    merged[material.item_id] += material.required_quantity
            totals = self._materials(
                tuple(CharacterMaterialRequirement(item_id, quantity) for item_id, quantity in merged.items()),
                terminology,
                item_metadata,
            )
            owned_inputs = self._materials(
                tuple(
                    CharacterMaterialRequirement(item_id, merged.get(item_id, 0))
                    for item_id in input_ids
                    if item_id in item_metadata or item_id in merged
                ),
                terminology,
                item_metadata,
            )
            status = _plan_status(gaps, totals)
            return CultivationPlan(
                character_name=detail.character.name_zh,
                status=status,
                sections=rendered_sections,
                totals=totals,
                required_experience=level_projection.required_experience,
                experience_overflow=level_projection.experience_overflow,
                included_breakthrough_stages=stages,
                gaps=tuple(gaps),
                fork_required_experience=fork_required_experience,
                fork_experience_overflow=fork_experience_overflow,
                fork_included_breakthrough_stages=fork_included_stages,
                owned_inputs=owned_inputs,
            )
        finally:
            terminology_dao.close()

    def calculate_stamina(
        self,
        plan: CultivationPlan,
        *,
        owned_quantities: Mapping[str, int],
        hunter_level: int,
        effective_identification_level: int | None,
    ) -> CultivationStaminaPlan:
        """Calculate merged and per-section stamina from one frozen static dataset."""

        stages = self.load_farming_stages()
        normalized_owned = normalize_owned_quantities(owned_quantities)
        total = calculate_stamina_result(
            plan.totals,
            normalized_owned,
            stages,
            hunter_level=hunter_level,
            effective_identification_level=effective_identification_level,
        )
        available = dict(normalized_owned)
        section_results: list[CultivationSectionStamina] = []
        for section in plan.sections:
            allocated = allocate_owned(
                {material.item_id: material.quantity for material in section.materials},
                available,
            )
            section_results.append(CultivationSectionStamina(
                section.label,
                calculate_stamina_result(
                    section.materials,
                    allocated,
                    stages,
                    hunter_level=hunter_level,
                    effective_identification_level=effective_identification_level,
                ),
            ))
        return CultivationStaminaPlan(
            total, tuple(section_results), stamina_material_ids(stages)
        )

    def load_farming_stages(self) -> tuple[FarmingStage, ...]:
        """Freeze the deterministic farming-stage dataset for one calculation."""

        dao = self._terminology_dao_factory(self._static_database_path)
        try:
            stages = tuple(dao.list_progression_farming_stages())
            return correct_legacy_stages(stages) if has_legacy_gold_alias(dao) else stages
        finally:
            dao.close()

    def _load_detail(self, character_id: int) -> CharacterDetail:
        queries = self._character_queries_factory(self._static_database_path)
        try:
            detail = StaticCatalogCharacterService(queries).get_character_detail(int(character_id))
            if detail is None:
                raise ValueError("角色不在当前静态库中")
            return detail
        finally:
            queries.close()

    def _load_profile(self, character_id: int) -> dict[str, object] | None:
        dao = self._user_dao_factory(self._user_database_path)
        try:
            profile = dao.get_character_profile(int(character_id))
            observation = dao.get_native_character_profile_observation(int(character_id))
            if observation is None:
                return profile
            base = profile if profile is not None else load_template_growth_defaults(
                (int(character_id),), static_database_path=self._static_database_path,
            )[int(character_id)]
            return project_native_role_profile(base, observation, persisted=profile is not None)
        finally:
            dao.close()

    def _saved_fork_seed(self, profile: dict[str, object]) -> CultivationForkSeed | None:
        fork_id = str(profile.get("fork_id") or "").strip()
        return self._fork_seed(fork_id, profile) if fork_id else None

    def _fork_seed(
        self,
        fork_id: str,
        profile: dict[str, object] | None,
    ) -> CultivationForkSeed:
        detail = self._load_fork_detail(fork_id)
        return CultivationForkSeed(
            fork_id=detail.summary.fork_id,
            fork_name=detail.summary.name_zh,
            current_level=_integer_in_range(
                (profile or {}).get("fork_level"), 1, 80, 1
            ),
            current_breakthrough_stage=_integer_in_range(
                (profile or {}).get("fork_breakthrough_stage"), 0, 6, 0
            ),
        )

    def _load_fork_detail(self, fork_id: str) -> ForkCatalogDetail:
        service = StaticCatalogForkService.from_database(self._static_database_path)
        try:
            detail = service.get_fork(str(fork_id))
            if detail is None:
                raise ValueError("弧盘不在当前静态库中")
            return detail
        finally:
            service.close()

    def _fork_sections(
        self,
        target: CultivationForkTarget,
    ) -> tuple[
        list[tuple[str, tuple[CharacterMaterialRequirement, ...], str | None]],
        ForkLevelMaterialProjection,
    ]:
        detail = self._load_fork_detail(target.fork_id)
        _validate_fork_state(
            target.current_level,
            target.current_breakthrough_stage,
            detail,
        )
        _validate_fork_state(
            target.target_level,
            target.target_breakthrough_stage,
            detail,
        )
        if (target.target_level, target.target_breakthrough_stage) < (
            target.current_level, target.current_breakthrough_stage,
        ):
            raise ValueError("弧盘目标养成不能低于当前养成")
        projection = project_fork_level_requirements(
            detail,
            current_level=target.current_level,
            current_stage=target.current_breakthrough_stage,
            target_level=target.target_level,
            target_stage=target.target_breakthrough_stage,
        )
        sections: list[tuple[str, tuple[CharacterMaterialRequirement, ...], str | None]] = []
        upgrade_requirements = (
            *projection.experience_materials,
            *projection.experience_costs,
        )
        if projection.required_experience:
            sections.append((
                f"弧盘 · {detail.summary.name_zh} · 升级",
                upgrade_requirements,
                (
                    f"升级经验 {projection.required_experience:,}；"
                    f"材料溢出经验 {projection.experience_overflow:,}。"
                ),
            ))
        breakthrough_requirements = (
            *projection.breakthrough_materials,
            *projection.breakthrough_costs,
        )
        if breakthrough_requirements:
            sections.append((
                f"弧盘 · {detail.summary.name_zh} · 突破",
                breakthrough_requirements,
                None,
            ))
        return sections, projection

    @staticmethod
    def _validate_character_interval(
        request: CultivationRequest,
        stages: tuple[CharacterBreakthroughRequirement, ...],
    ) -> None:
        _validate_state(request.current_level, request.current_breakthrough_stage, stages)
        _validate_state(request.target_level, request.target_breakthrough_stage, stages)
        if (request.target_level, request.target_breakthrough_stage) < (
            request.current_level, request.current_breakthrough_stage,
        ):
            raise ValueError("目标养成不能低于当前养成")

    @staticmethod
    def _materials(
        requirements: tuple[CharacterMaterialRequirement, ...],
        terminology: StaticCatalogTerminologyService,
        metadata: dict[str, dict[str, object]],
    ) -> tuple[CultivationMaterial, ...]:
        totals: dict[str, int] = defaultdict(int)
        for item in requirements:
            totals[item.item_id] += int(item.required_quantity)
        items = []
        for item_id, quantity in totals.items():
            term = terminology.resolve("item", item_id, context="progression")
            item = metadata.get(item_id, {})
            items.append(CultivationMaterial(
                item_id=item_id,
                name=term.display_name or str(item.get("name_zh") or item_id),
                quantity=quantity,
                quality=(str(item["quality"]) if item.get("quality") else None),
                icon_path=(str(item["icon_path"]) if item.get("icon_path") else None),
            ))
        return tuple(sorted(items, key=lambda item: (item.name, item.item_id)))


def _breakthrough_requirements(
    stages: tuple[CharacterBreakthroughRequirement, ...],
    *,
    current_level: int,
    current_stage: int,
    target_level: int,
    target_stage: int,
) -> tuple[tuple[CharacterMaterialRequirement, ...], tuple[int, ...]]:
    """Include a gate only when the selected before/after state crosses it."""

    totals: dict[str, int] = defaultdict(int)
    included: list[int] = []
    ordered = tuple(sorted(stages, key=lambda item: item.stage))
    previous_cap: int | None = None
    current = (current_level, current_stage)
    target = (target_level, target_stage)
    for stage in ordered:
        if stage.stage == 0:
            previous_cap = stage.max_character_level
            continue
        if previous_cap is None:
            continue
        gate = (previous_cap, stage.stage)
        if current < gate <= target:
            included.append(stage.stage)
            for cost in stage.costs:
                totals[cost.item_id] += cost.quantity
        previous_cap = stage.max_character_level
    return (
        tuple(CharacterMaterialRequirement(item_id, quantity) for item_id, quantity in totals.items()),
        tuple(included),
    )


def _fork_breakthrough_requirements(
    detail: ForkCatalogDetail,
    *,
    current_level: int,
    current_stage: int,
    target_level: int,
    target_stage: int,
) -> tuple[tuple[CharacterMaterialRequirement, ...], tuple[int, ...]]:
    totals: dict[str, int] = defaultdict(int)
    included: list[int] = []
    previous_cap: int | None = None
    current = (current_level, current_stage)
    target = (target_level, target_stage)
    for stage in sorted(detail.breakthroughs, key=lambda item: item.stage):
        if stage.stage == 0:
            previous_cap = stage.max_fork_level
            continue
        if previous_cap is not None and current < (previous_cap, stage.stage) <= target:
            included.append(stage.stage)
            for cost in (*stage.item_costs, *stage.gold_costs):
                if cost.amount is not None and cost.amount > 0:
                    totals[_canonical_fork_item_id(cost.item_id)] += cost.amount
        previous_cap = stage.max_fork_level
    return (
        tuple(CharacterMaterialRequirement(item_id, quantity) for item_id, quantity in totals.items()),
        tuple(included),
    )


def _validate_state(
    level: int,
    stage: int,
    stages: tuple[CharacterBreakthroughRequirement, ...],
) -> None:
    raw_level = int(level)
    raw_stage = int(stage)
    if not 1 <= raw_level <= 80:
        raise ValueError("角色等级必须在 1 到 80 之间")
    allowed = {item.stage for item in stages}
    if raw_stage not in allowed:
        raise ValueError("角色突破阶段不在当前正式成长数据中")
    minimum = 1 if raw_stage == 0 else (raw_stage + 1) * 10
    maximum = (raw_stage + 2) * 10
    if not minimum <= raw_level <= maximum:
        raise ValueError("角色等级与突破阶段不匹配")


def _validate_fork_state(
    level: int,
    stage: int,
    detail: ForkCatalogDetail,
) -> None:
    raw_level = int(level)
    raw_stage = int(stage)
    if not 1 <= raw_level <= 80:
        raise ValueError("弧盘等级必须在 1 到 80 之间")
    allowed = {item.stage for item in detail.breakthroughs}
    if raw_stage not in allowed:
        raise ValueError("弧盘突破阶段不在当前正式成长数据中")
    minimum = 1 if raw_stage == 0 else (raw_stage + 1) * 10
    maximum = (raw_stage + 2) * 10
    if not minimum <= raw_level <= maximum:
        raise ValueError("弧盘等级与突破阶段不匹配")


def _integer_in_range(value: object, minimum: int, maximum: int, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        result = int(value)
    except (TypeError, ValueError):
        return fallback
    return result if minimum <= result <= maximum else fallback


def _skill_maximum_level(skill: CharacterSkill) -> int:
    return min(10, max(level.level for level in skill.levels) + 1)


def _skill_category(skill: CharacterSkill) -> str:
    tag = str(skill.gameplay_tag or "")
    identities = (tag, skill.skill_id)
    if any(identity.endswith("Melee") for identity in identities):
        return "A"
    if any(identity.endswith("UltraSkill") for identity in identities):
        return "Q"
    if any(identity.endswith("Skill") for identity in identities):
        return "E"
    if skill.skill_id.endswith("_QTE"):
        return "QTE"
    return "技能"


def _canonical_fork_item_id(item_id: str) -> str:
    return "Gold" if str(item_id) == "gold" else str(item_id)


def _deduplicate_roles(roles: Iterable[CultivationRole]) -> tuple[CultivationRole, ...]:
    """Keep the catalog's first identity for each role shown in the picker."""

    result: list[CultivationRole] = []
    character_ids: set[int] = set()
    names: set[str] = set()
    for role in roles:
        name = role.name.strip()
        if role.character_id in character_ids or not name or name in names:
            continue
        character_ids.add(role.character_id)
        names.add(name)
        result.append(role)
    return tuple(sorted(result, key=lambda role: (role_name_sort_key(role.name), role.character_id)))


def _skill_level(value: object, skill: CharacterSkill) -> int:
    return _integer_in_range(value, 1, _skill_maximum_level(skill), 1)


def _plan_status(
    gaps: list[ProgressionRequirementGap],
    totals: tuple[CultivationMaterial, ...],
) -> MaterialSummaryStatus:
    if gaps:
        return MaterialSummaryStatus.PARTIAL if totals else MaterialSummaryStatus.UNAVAILABLE
    return MaterialSummaryStatus.COMPLETE


__all__ = [
    "CultivationFork", "CultivationForkSeed", "CultivationForkTarget", "CultivationMaterial",
    "CultivationPlan", "CultivationPlannerService", "CultivationRequest", "CultivationRole",
    "CultivationSectionStamina", "CultivationStaminaPlan",
    "CultivationSection", "CultivationSeed", "CultivationSkill", "CultivationSkillTarget",
]
