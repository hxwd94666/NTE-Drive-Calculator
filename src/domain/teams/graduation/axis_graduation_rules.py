# 理论词条复用Calc评分目录、品质和逐件静态条数；这里只冻结规则，不进行伤害评分或装备搜索。
from dataclasses import asdict, dataclass
import hashlib
import json
from math import isfinite

from src.domain.allocation.scoring_rules import AllocationScoringRules
from src.domain.equipment.properties.equipment_normalizer import calculate_drive_main_stats, equipment_quality_coefficient
from src.domain.characters.recommended_weights import WORKSHOP_STAT_PROPERTY_IDS


RULE_POLICY = "calc_full_value_distinct_substats_v1"
QUALITY_NAMES = {"gold": "Gold", "orange": "Gold", "purple": "Purple", "blue": "Blue"}


def _number(value):
    try:
        valid = type(value) in (int, float) and isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("理论词条满值或品质系数无效")


def _digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("理论词条规则缺少规范来源哈希")


@dataclass(frozen=True, slots=True)
class StatReference:
    property_id: str
    display_value: float
    is_percent: bool

    def __post_init__(self):
        if not isinstance(self.property_id, str) or not self.property_id or type(self.is_percent) is not bool:
            raise ValueError("理论词条属性或单位无效")
        _number(self.display_value)

    @property
    def value(self):
        return self.display_value / 100 if self.is_percent else self.display_value


@dataclass(frozen=True, slots=True)
class TheoryEquipmentSpec:
    item_id: str
    kind: str
    quality: str
    grid_count: int | None
    geometry_id: str | None
    suit_id: str | None
    max_level: int
    sub_stat_count: int
    intrinsic_main_stats: tuple[StatReference, ...] = ()

    def __post_init__(self):
        if not isinstance(self.item_id, str) or not self.item_id or self.kind not in {"core", "module"}:
            raise ValueError("理论装备正式身份无效")
        if self.quality not in {"Gold", "Purple", "Blue"}:
            raise ValueError("理论装备品质未知")
        if (type(self.max_level) is not int or self.max_level < 0
                or type(self.sub_stat_count) is not int or self.sub_stat_count < 1):
            raise ValueError("理论装备强化或副词条条数未知")
        if self.kind == "module" and (type(self.grid_count) is not int or self.grid_count < 1
                                      or not isinstance(self.geometry_id, str) or not self.geometry_id):
            raise ValueError("理论驱动缺少真实形状和格数")
        if self.kind == "core" and (not isinstance(self.suit_id, str) or not self.suit_id):
            raise ValueError("理论卡带缺少正式套装")
        if any(not isinstance(value, StatReference) for value in self.intrinsic_main_stats):
            raise ValueError("理论驱动固定主属性格式无效")
        object.__setattr__(self, "intrinsic_main_stats", tuple(self.intrinsic_main_stats))


@dataclass(frozen=True, slots=True)
class TheoryEquipmentRules:
    dataset_id: str
    static_schema: int
    static_sha256: str
    scoring_source_sha256: str
    scoring_rule_sha256: str
    quality_coefficients: tuple[tuple[str, float], ...]
    drive_sub_stats: tuple[StatReference, ...]
    core_sub_stats: tuple[StatReference, ...]
    core_main_stats: tuple[StatReference, ...]
    items: tuple[TheoryEquipmentSpec, ...]
    unavailable_items: tuple[tuple[str, str], ...] = ()
    main_sub_overlap_allowed: bool = True
    policy_id: str = RULE_POLICY

    def __post_init__(self):
        if not isinstance(self.dataset_id, str) or not self.dataset_id or type(self.static_schema) is not int or self.static_schema < 1:
            raise ValueError("理论词条规则静态身份无效")
        for name in ("static_sha256", "scoring_source_sha256", "scoring_rule_sha256"):
            _digest(getattr(self, name))
        quality = tuple(tuple(pair) for pair in self.quality_coefficients)
        if len(quality) != 3 or {key for key, _ in quality} != {"Gold", "Purple", "Blue"}:
            raise ValueError("理论词条品质系数必须明确，不采用未知品质默认满值")
        for key, value in quality:
            _number(value)
            if value != equipment_quality_coefficient(key):
                raise ValueError("理论词条品质系数与Calc公共属性口径不一致")
        object.__setattr__(self, "quality_coefficients", quality)
        for name in ("drive_sub_stats", "core_sub_stats", "core_main_stats"):
            values = tuple(getattr(self, name))
            if (not values or any(not isinstance(value, StatReference) for value in values)
                    or len({value.property_id for value in values}) != len(values)):
                raise ValueError("理论词条属性池为空或包含重复正式属性")
            object.__setattr__(self, name, values)
        items = tuple(self.items)
        if not items or any(not isinstance(item, TheoryEquipmentSpec) for item in items) or len({item.item_id for item in items}) != len(items):
            raise ValueError("理论装备目录为空或正式身份重复")
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "unavailable_items", tuple(tuple(row) for row in self.unavailable_items))
        if self.policy_id != RULE_POLICY or self.main_sub_overlap_allowed is not True:
            raise ValueError("理论规则与当前Calc主副词条关系口径不一致")

    @property
    def sha256(self):
        encoded = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _references(values, names, attributes):
    result = {}
    for name in names:
        property_id = WORKSHOP_STAT_PROPERTY_IDS.get(name)
        attribute = attributes.get(property_id)
        if attribute is None or attribute.get("show_percent") not in (0, 1, False, True):
            raise ValueError("理论词条属性缺少正式映射或单位：" + str(name))
        value = StatReference(property_id, values[name], bool(attribute["show_percent"]))
        if property_id in result and result[property_id] != value:
            raise ValueError("理论词条别名对应同一正式属性但数值冲突")
        result[property_id] = value
    return tuple(result[key] for key in sorted(result))


def compile_theory_equipment_rules(*, scoring_rules, quality_coefficients, attributes, equipment_items,
                                   dataset_id, static_schema, static_sha256, checkpoint=lambda: None):
    if not isinstance(scoring_rules, AllocationScoringRules):
        raise ValueError("理论词条必须复用已冻结的Calc评分目录")
    _digest(scoring_rules.source_sha256)
    catalog = scoring_rules.to_stat_catalog()
    attributes = {row["attribute_id"]: row for row in attributes}
    drive = _references(catalog.gold_base_values, catalog.gold_base_values, attributes)
    core = _references(catalog.tape_stat_values, catalog.tape_sub_stat_pool(), attributes)
    main = _references(catalog.tape_main_values, catalog.tape_main_stat_pool(), attributes)
    quality = dict(quality_coefficients)
    items, unavailable = [], []
    for row in equipment_items:
        checkpoint()
        item_id = row["item_id"]
        if row.get("is_guide_item"):
            unavailable.append((item_id, "guide_item_not_theoretical_target"))
            continue
        try:
            kind = row["kind"]
            name = QUALITY_NAMES.get(str(row["quality"]).casefold())
            if name is None or name not in quality:
                raise ValueError("装备品质不支持")
            count = row["random_sub_attribute_max_count"]
            if type(count) is not int or not 1 <= count <= len(core if kind == "core" else drive):
                raise ValueError("装备副词条条数不支持")
            if kind == "core" and row["random_base_attribute_count"] != 1:
                raise ValueError("卡带主词条数量不支持")
            intrinsic = ()
            if kind == "module":
                if type(row["grid_count"]) is not int or row["grid_count"] < 1:
                    raise ValueError("驱动格数未知")
                # 固定驱动主属性直接消费已有公共计算入口，不另抄21/280等系数。
                values = calculate_drive_main_stats(row["grid_count"], name)
                if row["random_base_attribute_count"] != len(values):
                    raise ValueError("驱动固定主属性数量不支持")
                intrinsic = _references(values, values, attributes)
            items.append(TheoryEquipmentSpec(item_id, kind, name, row["grid_count"], row["geometry_id"],
                                            row["suit_id"], row["max_level"], count, intrinsic))
        except (KeyError, ValueError, TypeError):
            unavailable.append((item_id, "equipment_theoretical_rules_incomplete"))
    return TheoryEquipmentRules(
        dataset_id, static_schema, static_sha256, scoring_rules.source_sha256, scoring_rules.rule_sha256,
        tuple(quality.items()), drive, core, main, tuple(items), tuple(unavailable),
    )
