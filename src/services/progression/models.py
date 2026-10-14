# 定义养成规划的不可变输入、材料与体力结果模型。
from __future__ import annotations

from dataclasses import dataclass

from src.domain.progression.stamina import FarmingStage, ProgressionStaminaResult
from src.services.progression.character_requirements import MaterialSummaryStatus, ProgressionRequirementGap


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


@dataclass(frozen=True, slots=True)
class CultivationPreparedTarget:
    request: CultivationRequest
    plan: CultivationPlan
    farming_stages: tuple[FarmingStage, ...]
    stamina_item_ids: frozenset[str]
