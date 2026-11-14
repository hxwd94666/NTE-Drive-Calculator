# 当前构筑只保存有限且不可变的角色/装备证据，账号代次用于撤权而不混入持久内容身份。
from dataclasses import asdict, dataclass
import hashlib
import json
from math import isfinite

from src.domain.teams.models import Team
from src.domain.teams.overview import TeamSourceConfig


PROFILE_FIELDS = frozenset({
    "character_level", "breakthrough_stage", "awakening_level", "selected_awaken_effect_ids",
    "awakening_selection_initialized", "likeability_level", "likeability_level_10_enabled",
    "fork_observed", "fork_id", "fork_level", "fork_breakthrough_stage", "fork_refinement_level",
    "skill_levels", "skill_level_sources", "field_sources",
})
WORLD_BONUS_FIELDS = frozenset({"yaodao_attack_add", "quantao_crit_damage", "kaijia_defense_add"})
MAX_MEMBER_BYTES = 256 * 1024


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, OverflowError, RecursionError) as error:
        raise ValueError("当前构筑包含无效冻结数据") from error


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("当前构筑包含重复JSON字段")
        result[key] = value
    return result


def _json(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_MEMBER_BYTES:
        raise ValueError("当前构筑正文超限或格式无效")
    try:
        value = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
        _canonical(value)  # 拒绝NaN/Infinity，包括未知嵌套数据，不让缺项伪装成有效面板。
        return value
    except (TypeError, OverflowError, RecursionError) as error:
        raise ValueError("当前构筑冻结数据格式无效") from error


def _digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("当前构筑缺少规范冻结摘要")


def _positive(value, *, allow_none=False):
    if value is None and allow_none:
        return
    if type(value) is not int or not 1 <= value <= 2 ** 63 - 1:
        raise ValueError("当前构筑正式身份无效")


def _codes(values):
    if (not isinstance(values, (tuple, list)) or len(values) > 128
            or any(not isinstance(value, str) or not value or len(value) > 256 for value in values)):
        raise ValueError("当前构筑缺口格式无效")
    return tuple(dict.fromkeys(values))


def _profile_shape(profile):
    integers = {"character_level", "fork_level", "fork_refinement_level", "breakthrough_stage",
                "fork_breakthrough_stage", "awakening_level", "likeability_level"}
    positive = {"character_level", "fork_level", "fork_refinement_level"}
    for key in integers & profile.keys():
        value = profile[key]
        if value is not None and (type(value) is not int or not (1 if key in positive else 0) <= value <= 2 ** 63 - 1):
            raise ValueError("当前养成整数证据无效")
    for key in {"fork_observed", "awakening_selection_initialized", "likeability_level_10_enabled"} & profile.keys():
        if type(profile[key]) is not bool:
            raise ValueError("当前养成确认状态不是布尔值")
    if "fork_id" in profile and profile["fork_id"] is not None:
        if not isinstance(profile["fork_id"], str) or not profile["fork_id"] or len(profile["fork_id"]) > 256:
            raise ValueError("当前弧盘身份无效")
    if "skill_levels" in profile:
        skills = profile["skill_levels"]
        if (not isinstance(skills, dict) or len(skills) > 64 or any(
            not isinstance(key, str) or not key or len(key) > 256
            or type(value) is not int or not 1 <= value <= 2 ** 63 - 1 for key, value in skills.items()
        )):
            raise ValueError("当前基础技能证据无效")
    if "skill_level_sources" in profile:
        sources = profile["skill_level_sources"]
        if (not isinstance(sources, dict) or set(sources) != set(profile.get("skill_levels") or {})
                or any(not isinstance(value, str) or value not in {"account", "native_observed"} for value in sources.values())):
            raise ValueError("当前技能来源必须逐正式SkillID对应")
    if "selected_awaken_effect_ids" in profile:
        effects = profile["selected_awaken_effect_ids"]
        if (not isinstance(effects, list) or len(effects) > 6
                or any(not isinstance(value, str) or not value or len(value) > 256 for value in effects)
                or len(set(effects)) != len(effects)):
            raise ValueError("当前觉醒选择证据无效")


@dataclass(frozen=True, slots=True)
class CurrentMemberBuild:
    character_id: int
    slot_id: int | None
    plan_id: int | None
    snapshot_id: int | None
    source_fingerprint: str
    profile_evidence_json: str
    equipment_json: str
    missing_fields: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    source_issues: tuple[str, ...] = ()

    def __post_init__(self):
        _positive(self.character_id)
        for value in (self.slot_id, self.plan_id, self.snapshot_id):
            _positive(value, allow_none=True)
        _digest(self.source_fingerprint)
        evidence = _json(self.profile_evidence_json)
        if not isinstance(evidence, dict) or set(evidence) != {"account", "native", "effective"}:
            raise ValueError("当前养成证据必须区分账号、原生与有效投影")
        for name, profile in evidence.items():
            if name == "effective" and profile is None:
                continue
            if not isinstance(profile, dict) or set(profile) - PROFILE_FIELDS:
                raise ValueError("当前养成证据含非正式字段")
            _profile_shape(profile)
            sources = profile.get("field_sources", {})
            if (not isinstance(sources, dict) or set(sources) - PROFILE_FIELDS
                    or any(not isinstance(value, str) or value not in {"account", "native_observed", "mixed"}
                           or value == "mixed" and key != "skill_levels" for key, value in sources.items())):
                raise ValueError("当前养成不接受模板来源冒充实际进度")
        equipment = _json(self.equipment_json)
        if not isinstance(equipment, list) or len(equipment) > 17 or any(not isinstance(item, dict) for item in equipment):
            raise ValueError("当前成员只冻结自身装备，不接纳完整背包候选")
        object.__setattr__(self, "profile_evidence_json", _canonical(evidence))
        object.__setattr__(self, "equipment_json", _canonical(equipment))
        for name in ("missing_fields", "gaps", "source_issues"):
            object.__setattr__(self, name, _codes(getattr(self, name)))

    @property
    def profile_evidence(self):
        return json.loads(self.profile_evidence_json)

    @property
    def equipment(self):
        return json.loads(self.equipment_json)


@dataclass(frozen=True, slots=True)
class CurrentBuild:
    account_id: str
    generation: int
    team: Team
    source: TeamSourceConfig
    dataset_id: str
    static_schema: int
    static_sha256: str
    members: tuple[CurrentMemberBuild, ...]
    world_bonus_json: str
    gaps: tuple[str, ...] = ()
    source_issues: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.account_id, str) or not self.account_id or len(self.account_id) > 256:
            raise ValueError("当前构筑账号身份无效")
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("当前构筑账号代次无效")
        if not isinstance(self.team, Team) or not isinstance(self.source, TeamSourceConfig):
            raise ValueError("当前构筑队伍或来源格式无效")
        config = self.source.validated()
        if self.team.team_id != config.team_id:
            raise ValueError("当前构筑来源不属于该队伍")
        if not isinstance(self.dataset_id, str) or not self.dataset_id or len(self.dataset_id) > 256:
            raise ValueError("当前构筑静态身份无效")
        _positive(self.static_schema)
        _digest(self.static_sha256)
        if (not isinstance(self.members, (tuple, list)) or len(self.members) > 4
                or any(not isinstance(member, CurrentMemberBuild) for member in self.members)
                or tuple(member.character_id for member in self.members) != self.team.character_ids):
            raise ValueError("当前构筑成员与冻结队伍顺序不一致")
        world = _json(self.world_bonus_json)
        if world is not None:
            if (not isinstance(world, dict) or set(world) - WORLD_BONUS_FIELDS
                    or any(type(value) not in (int, float) or not isfinite(value) or value < 0 for value in world.values())):
                raise ValueError("当前家具证据格式或数值无效")
        object.__setattr__(self, "members", tuple(self.members))
        object.__setattr__(self, "source", config)
        object.__setattr__(self, "world_bonus_json", _canonical(world))
        for name in ("gaps", "source_issues"):
            object.__setattr__(self, name, _codes(getattr(self, name)))

    @property
    def world_bonus(self):
        return json.loads(self.world_bonus_json)

    @property
    def source_ready(self):
        # 只表示来源完整；不把它提升为轴伤可量化、组件就绪或理论合法域已闭合。
        return (len(self.members) == 4 and not self.source_issues
                and not any(member.source_issues or not member.equipment for member in self.members))

    @property
    def has_gaps(self):
        return bool(not self.source_ready or self.gaps or self.source_issues or any(member.gaps or member.missing_fields or member.source_issues
                                                         for member in self.members))

    @property
    def sha256(self):
        value = asdict(self)
        value.pop("account_id")
        value.pop("generation")
        value["team"].pop("name")
        # 代次与账号由派发/保存门禁核对，不作为跨会话内容摘要；显示名和读取时间同样不参与。
        return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
