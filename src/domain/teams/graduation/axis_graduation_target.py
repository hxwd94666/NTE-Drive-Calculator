# 毕业目标冻结显式养成、有限预设和理论图纸；这里只验证契约结构，游戏合法性仍由独立分析核验。
from dataclasses import dataclass
import hashlib
import json
from math import isfinite
import struct
import unicodedata

from src.domain.teams.graduation.axis_graduation_rules import RULE_POLICY, TheoryEquipmentRules


TARGET_FORMAT = "team_axis_graduation_target_v1"
MAX_TARGET_BYTES = 512 * 1024
MAX_INTEGER = 2 ** 63 - 1


def _integer(value, minimum=0, maximum=MAX_INTEGER):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("毕业目标整数缺失或越界")


def _text(value):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value.encode("utf-8")) > 256
            or any(unicodedata.category(char) == "Cc" for char in value)):
        raise ValueError("毕业目标身份文本无效")


def _hash(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("毕业目标来源摘要无效")


def _sequence(value, maximum, *, minimum=0):
    if not isinstance(value, (tuple, list)) or not minimum <= len(value) <= maximum:
        raise ValueError("毕业目标集合缺失或超限")
    return tuple(value)


def _typed(values, kind, maximum, *, minimum=0):
    result = _sequence(values, maximum, minimum=minimum)
    if any(not isinstance(value, kind) for value in result):
        raise ValueError("毕业目标成员格式无效")
    return result


@dataclass(frozen=True, slots=True)
class TargetFork:
    fork_id: str
    level: int
    breakthrough_stage: int
    refinement_level: int

    def __post_init__(self):
        _text(self.fork_id)
        _integer(self.level, 1)
        _integer(self.breakthrough_stage, 0, 64)
        _integer(self.refinement_level, 1, 64)

    def to_payload(self):
        return {"fork_id": self.fork_id, "level": self.level,
                "breakthrough_stage": self.breakthrough_stage, "refinement_level": self.refinement_level}


@dataclass(frozen=True, slots=True)
class TargetMember:
    character_id: int
    character_level: int
    breakthrough_stage: int
    awakening_level: int
    selected_awaken_effect_ids: tuple[str, ...]
    likeability_level_10_enabled: bool
    fork: TargetFork | None
    skill_levels: tuple[tuple[str, int], ...]

    def __post_init__(self):
        _integer(self.character_id, 1)
        _integer(self.character_level, 1)
        _integer(self.breakthrough_stage, 0, 6)
        _integer(self.awakening_level, 0, 6)
        selected = _sequence(self.selected_awaken_effect_ids, self.awakening_level)
        for effect_id in selected:
            _text(effect_id)
        if len(set(selected)) != len(selected):
            raise ValueError("毕业目标觉醒选择重复")
        object.__setattr__(self, "selected_awaken_effect_ids", tuple(sorted(selected)))
        if type(self.likeability_level_10_enabled) is not bool or (self.fork is not None and not isinstance(self.fork, TargetFork)):
            raise ValueError("毕业目标好感或弧盘状态缺失")
        skills = []
        for entry in _sequence(self.skill_levels, 64, minimum=1):
            pair = _sequence(entry, 2, minimum=2)
            _text(pair[0])
            _integer(pair[1], 1, 64)
            skills.append(pair)
        if len({key for key, _ in skills}) != len(skills):
            raise ValueError("毕业目标基础技能重复")
        object.__setattr__(self, "skill_levels", tuple(sorted(skills)))

    def to_payload(self):
        return {"character_id": self.character_id, "character_level": self.character_level,
                "breakthrough_stage": self.breakthrough_stage, "awakening_level": self.awakening_level,
                "selected_awaken_effect_ids": list(self.selected_awaken_effect_ids),
                "likeability_level_10_enabled": self.likeability_level_10_enabled,
                "fork": self.fork.to_payload() if self.fork else None,
                "skill_levels": [{"skill_id": key, "level": level} for key, level in self.skill_levels]}


@dataclass(frozen=True, slots=True)
class TargetPiece:
    piece_id: str
    item_id: str
    row: int
    column: int

    def __post_init__(self):
        _text(self.piece_id)
        _text(self.item_id)
        # 锚点只是明确坐标；真实形状的负偏移、越界和重叠由正式静态几何校验，不在此猜形状。
        _integer(self.row, -64, 64)
        _integer(self.column, -64, 64)

    def to_payload(self):
        return {"piece_id": self.piece_id, "item_id": self.item_id, "row": self.row, "column": self.column}


@dataclass(frozen=True, slots=True)
class TargetLayout:
    character_id: int
    core_piece_id: str
    core_item_id: str
    pieces: tuple[TargetPiece, ...]
    minimum_set_count: int
    extra_shape_ids: tuple[str, ...]

    def __post_init__(self):
        _integer(self.character_id, 1)
        _text(self.core_piece_id)
        _text(self.core_item_id)
        pieces = _typed(self.pieces, TargetPiece, 20, minimum=1)
        identities = [self.core_piece_id, *(piece.piece_id for piece in pieces)]
        if len(set(identities)) != len(identities):
            raise ValueError("毕业目标理论装备身份重复")
        object.__setattr__(self, "pieces", tuple(sorted(pieces, key=lambda piece: piece.piece_id)))
        _integer(self.minimum_set_count, 0, 20)
        shapes = _sequence(self.extra_shape_ids, 20)
        for shape in shapes:
            _text(shape)
        # 同形状的多个额外实例允许保留；消费数量仍由图纸域核验，不能用set去重。
        object.__setattr__(self, "extra_shape_ids", tuple(sorted(shapes)))

    def to_payload(self):
        return {"character_id": self.character_id, "core_piece_id": self.core_piece_id,
                "core_item_id": self.core_item_id, "pieces": [piece.to_payload() for piece in self.pieces],
                "minimum_set_count": self.minimum_set_count, "extra_shape_ids": list(self.extra_shape_ids)}


@dataclass(frozen=True, slots=True)
class TargetPreset:
    preset_id: str
    members: tuple[TargetMember, ...]
    layouts: tuple[TargetLayout, ...]

    def __post_init__(self):
        _text(self.preset_id)
        members = _typed(self.members, TargetMember, 4, minimum=4)
        layouts = _typed(self.layouts, TargetLayout, 4, minimum=4)
        ids = tuple(member.character_id for member in members)
        if len(set(ids)) != 4 or ids != tuple(layout.character_id for layout in layouts):
            raise ValueError("毕业目标预设的四人阵容与图纸顺序不一致")
        identities = [key for layout in layouts for key in (layout.core_piece_id, *(p.piece_id for p in layout.pieces))]
        if len(identities) != len(set(identities)):
            raise ValueError("毕业目标预设跨角色重复消费理论装备身份")
        object.__setattr__(self, "members", members)
        object.__setattr__(self, "layouts", layouts)

    def to_payload(self):
        return {"preset_id": self.preset_id, "members": [member.to_payload() for member in self.members],
                "layouts": [layout.to_payload() for layout in self.layouts]}


@dataclass(frozen=True, slots=True)
class TargetWorldBonus:
    yaodao_attack_add: float
    quantao_crit_damage: float

    def __post_init__(self):
        for key, maximum in (("yaodao_attack_add", 20), ("quantao_crit_damage", .04)):
            value = getattr(self, key)
            try:
                valid = type(value) in (int, float) and isfinite(value) and 0 <= value <= maximum
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError("毕业目标家具状态缺失或越界")
            object.__setattr__(self, key, float(value) if value else 0.0)

    def to_payload(self):
        return {"yaodao_attack_add": self.yaodao_attack_add, "quantao_crit_damage": self.quantao_crit_damage}


@dataclass(frozen=True, slots=True)
class GraduationTarget:
    target_id: str
    team_id: str
    revision: int
    character_ids: tuple[int, ...]
    dataset_id: str
    static_schema: int
    static_sha256: str
    scoring_rule_sha256: str
    world_bonus: TargetWorldBonus
    presets: tuple[TargetPreset, ...]
    assumption_codes: tuple[str, ...] = ()
    format: str = TARGET_FORMAT
    equipment_policy: str = RULE_POLICY

    def __post_init__(self):
        for value in (self.target_id, self.team_id, self.dataset_id):
            _text(value)
        _integer(self.revision, 1)
        _integer(self.static_schema, 1, 2 ** 32 - 1)
        _hash(self.static_sha256)
        _hash(self.scoring_rule_sha256)
        ids = _sequence(self.character_ids, 4, minimum=4)
        for key in ids:
            _integer(key, 1)
        if len(set(ids)) != 4:
            raise ValueError("毕业目标阵容重复")
        object.__setattr__(self, "character_ids", ids)
        if not isinstance(self.world_bonus, TargetWorldBonus):
            raise ValueError("毕业目标必须明确家具输入")
        presets = _typed(self.presets, TargetPreset, 16, minimum=1)
        if (len({preset.preset_id for preset in presets}) != len(presets)
                or any(tuple(member.character_id for member in preset.members) != ids for preset in presets)):
            raise ValueError("毕业目标预设身份重复或改变阵容")
        object.__setattr__(self, "presets", tuple(sorted(presets, key=lambda preset: preset.preset_id)))
        codes = _sequence(self.assumption_codes, 64)
        for code in codes:
            _text(code)
        if len(set(codes)) != len(codes):
            raise ValueError("毕业目标假设重复")
        object.__setattr__(self, "assumption_codes", tuple(sorted(codes)))
        if self.format != TARGET_FORMAT or self.equipment_policy != RULE_POLICY:
            raise ValueError("毕业目标版本或词条口径不支持")
        if len(_canonical(self.to_payload()).encode("utf-8")) > MAX_TARGET_BYTES:
            raise ValueError("毕业目标正文超限")

    def to_payload(self):
        return {"format": self.format, "target_id": self.target_id, "team_id": self.team_id,
                "revision": self.revision, "character_ids": list(self.character_ids), "dataset_id": self.dataset_id,
                "static_schema": self.static_schema, "static_sha256": self.static_sha256,
                "scoring_rule_sha256": self.scoring_rule_sha256, "equipment_policy": self.equipment_policy,
                "world_bonus": self.world_bonus.to_payload(), "presets": [preset.to_payload() for preset in self.presets],
                "assumption_codes": list(self.assumption_codes)}

    @property
    def sha256(self):
        payload = self.to_payload()
        # JSON浮点数的指数/小数拼写跨语言不同；摘要用规范IEEE-754大端位串，正文仍是数值。
        payload["world_bonus"] = {key: struct.pack(">d", value).hex() for key, value in self.world_bonus.to_payload().items()}
        return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()

    def verify_rules(self, rules):
        if not isinstance(rules, TheoryEquipmentRules) or any(getattr(self, key) != getattr(rules, key) for key in (
                "dataset_id", "static_schema", "static_sha256", "scoring_rule_sha256")) or rules.policy_id != self.equipment_policy:
            raise ValueError("毕业目标与冻结静态/评分规则不一致")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected.split()):
        raise ValueError("毕业目标存在缺失或未知字段")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("毕业目标JSON字段重复")
        result[key] = value
    return result


def _member(raw):
    value = dict(_keys(raw, "character_id character_level breakthrough_stage awakening_level selected_awaken_effect_ids likeability_level_10_enabled fork skill_levels"))
    fork = value["fork"]
    if fork is not None:
        value["fork"] = TargetFork(**_keys(fork, "fork_id level breakthrough_stage refinement_level"))
    value["skill_levels"] = tuple((entry["skill_id"], entry["level"]) for entry in (
        _keys(entry, "skill_id level") for entry in _sequence(value["skill_levels"], 64, minimum=1)))
    return TargetMember(**value)


def _layout(raw):
    value = dict(_keys(raw, "character_id core_piece_id core_item_id pieces minimum_set_count extra_shape_ids"))
    value["pieces"] = tuple(TargetPiece(**_keys(piece, "piece_id item_id row column"))
                            for piece in _sequence(value["pieces"], 20, minimum=1))
    return TargetLayout(**value)


def decode_graduation_target(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_TARGET_BYTES:
        raise ValueError("毕业目标正文缺失或超限")
    try:
        value = dict(_keys(json.loads(raw, object_pairs_hook=_pairs), "format target_id team_id revision character_ids dataset_id static_schema static_sha256 scoring_rule_sha256 equipment_policy world_bonus presets assumption_codes"))
        value["world_bonus"] = TargetWorldBonus(**_keys(value["world_bonus"], "yaodao_attack_add quantao_crit_damage"))
        presets = []
        for preset in _sequence(value["presets"], 16, minimum=1):
            preset = _keys(preset, "preset_id members layouts")
            presets.append(TargetPreset(preset["preset_id"],
                tuple(_member(member) for member in _sequence(preset["members"], 4, minimum=4)),
                tuple(_layout(layout) for layout in _sequence(preset["layouts"], 4, minimum=4))))
        value["presets"] = tuple(presets)
        return GraduationTarget(**value)
    except (TypeError, OverflowError, RecursionError) as error:
        raise ValueError("毕业目标正文无效") from error


def encode_graduation_target(target):
    if not isinstance(target, GraduationTarget):
        raise ValueError("只编码正式毕业目标")
    raw = _canonical(target.to_payload())
    decode_graduation_target(raw)
    return raw
