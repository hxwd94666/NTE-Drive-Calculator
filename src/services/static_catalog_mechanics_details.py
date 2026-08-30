# 构建公式与反事实模型详情，隔离于目录查询和效果关系编排。
"""Pure detail projections for formula and counterfactual mechanics."""

from __future__ import annotations

from src.services.static_catalog_formula_presenters import (
    CounterfactualMatrixRow,
    FormulaDetailView,
)
from src.services.static_catalog_mechanics_models import (
    CatalogLink,
    EvidenceStage,
    FORMULA_CHAPTER_BY_KEY,
    FORMULA_FAMILY_BY_KEY,
    MODEL_FAMILY_BY_KEY,
    MechanicsDetail,
    PLACEHOLDER_NAME,
    PlayerField,
    PlayerSection,
    encode_record,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


_PLAYER_FORMULAS: dict[
    str,
    tuple[str, str, tuple[tuple[str, str], ...]],
] = {
    "panel_attribute": (
        "角色面板属性",
        "面板值 = 基础值 ×（1 + 百分比加成）+ 固定加成",
        (("基础值", "取当前角色的人物、弧盘和其他正式基础值；不取受击目标。"),
         ("百分比加成", "取当前角色装备、养成与命中时有效 Buff 的同属性百分比项。"),
         ("固定加成", "取当前角色装备、账号世界加成与命中时有效 Buff 的同属性固定项。")),
    ),
    "skill_multiplier": (
        "技能倍率",
        "技能倍率 = 等级倍率 ×（1 + 倍率修正合计）",
        (("等级倍率", "取攻击方本条正式伤害项；按该技能当前有效等级读取倍率档。"),
         ("倍率修正", "取攻击方当前养成与 Buff 中，正式作用于该技能标签的倍率修正。")),
    ),
    "direct_damage": (
        "直伤计算",
        "最终直伤 = 向上取整[max(0, 技能倍率 × 对应面板 × 增伤 × 暴击 × 防御 × 抗性 × 易伤 × Π独立增伤)]",
        (("技能倍率", "取攻击方本条正式伤害项与当前有效技能等级。"),
         ("对应面板", "取攻击方命中时的攻击、生命或防御面板；具体哪一种由伤害项指定。"),
         ("增伤区", "取攻击方命中时适用于本击的通用、属性、技能与状态增伤。"),
         ("暴击", "暴击率/固定策略与暴击伤害取攻击方或本机制正式规则。"),
         ("防御区", "角色等级和防御穿透取攻击方；防御与防御降低取受击目标。"),
         ("抗性区", "属性穿透取攻击方；对应属性抗性与减抗取受击目标。"),
         ("易伤区", "取受击目标在本击时点承受的伤害提升。"),
         ("独立增伤", "取所有明确作用于攻击方本击的独立最终乘数，逐项相乘。")),
    ),
    "damage_increase": (
        "增伤",
        "增伤倍率 = 1 + 各类增伤合计",
        (("增伤合计", "取攻击方命中时适用于本击的通用、属性、技能和状态增伤；同区相加。"),),
    ),
    "vulnerability": (
        "易伤",
        "易伤倍率 = 1 + 目标受伤提升合计",
        (("受伤提升", "只取本次受击目标在命中时有效的受伤提升 Debuff；不取攻击方增伤。"),),
    ),
    "critical": (
        "暴击",
        "未暴击 = 向上取整(完整精度伤害)；暴击 = 向上取整[完整精度伤害 × (1 + 暴击伤害)]；期望 = (1-r)×未暴击 + r×暴击",
        (("暴击率", "普通直伤取攻击方命中时暴击率；固定 50% 或不可暴击机制以正式策略覆盖。"),
         ("暴击伤害", "取攻击方命中时暴击伤害；受击目标不提供此变量。")),
    ),
    "defense": (
        "防御",
        "目标有效防御 = [防御基础×(1+防御提升)+固定防御]÷6×(1-防御穿透)×(1-防御降低)；防御倍率 = (角色等级+100)÷(目标有效防御+角色等级+100)",
        (("角色等级", "取本次伤害来源角色等级。"),
         ("防御穿透", "取本次伤害来源角色命中时的防御穿透或防御无视。"),
         ("目标防御", "优先取本次受击目标冻结属性包的基础、百分比和固定防御。"),
         ("防御降低", "取本次受击目标命中时有效的防御降低。")),
    ),
    "resistance": (
        "抗性",
        "有效抗性 X = 基础抗性-减抗-属性穿透；X≥0 时抗性倍率=1-X；X<0 时抗性倍率=1-X÷1.10",
        (("伤害属性", "取本条正式伤害项或逐击已确认的实际属性。"),
         ("基础抗性", "取本次受击目标对该伤害属性的冻结战前抗性。"),
         ("减抗", "取受击目标命中时有效、且匹配该属性的减抗。"),
         ("属性穿透", "取伤害来源角色命中时匹配该属性的穿透。")),
    ),
    "independent_final_damage": (
        "独立最终增伤",
        "独立增伤 = 每个明确独立最终增伤逐项相乘",
        (("独立最终增伤", "取正式效果中同时满足攻击方、受击目标、逐击标签与触发条件的最终增伤；逐项相乘。"),),
    ),
    "dot_damage": (
        "持续伤害",
        "单次持续伤害 = 直伤同类乘区 × 状态层数 × 持续伤害专属增伤",
        (("直伤同类乘区", "攻击方提供技能倍率、面板、增伤、穿透和暴伤；受击目标提供防御、抗性、减益和易伤。"),
         ("状态层数", "取同半场、同一受击目标在本跳前的正式状态层数或受限结算系数。"),
         ("持续伤害种类", "取同一受击目标在本跳前仍有效、带正式 DOT 身份的种类数。")),
    ),
    "topple_damage": (
        "倾陷伤害",
        "倾陷伤害 = 等级曲线 × 倾陷强度 × 倾陷上限 × 防御 × 抗性",
        (("等级曲线", "团队中每名同半场角色分别取自己的等级对应倾陷曲线。"),
         ("倾陷强度", "每名角色分别取自己的倾陷强度和倾陷伤害提高。"),
         ("倾陷上限", "全队各格共同取本次受击目标的倾陷上限或冻结档位覆盖。"),
         ("防御/抗性", "每格穿透取该角色；防御、对应属性抗性和减益取受击目标。")),
    ),
    "weave_followup": (
        "覆纹追加伤害",
        "覆纹追加 = 被记录原伤害 × 环合强度修正 × 特殊增伤",
        (("原伤害实际值", "取被覆纹记录的攻击方真实伤害；正式 DOT 等可记录来源同样保留，不重新计算预计值。"),
         ("环合强度", "取被记录原伤害的实际来源角色；不比较环合双方，也不固定取 QTE 触发者。"),
         ("伤害属性", "继承每条原伤害的实际属性；受击目标提供该属性抗性。")),
    ),
    "settlement_rounding": (
        "最终伤害取整",
        "最终伤害 = 向上取整（不小于零的完整精度伤害）",
        (("完整精度伤害", "取该公式所有适用乘区相乘后的结果；中间过程不取整。"),),
    ),
    "max_hp_settlement": (
        "生命上限下降结算",
        "有效伤害 = 本次伤害 + 按当前生命比例折算的生命上限下降",
        (("生命上限变化", "取同半场、同一受击目标已确认的新旧最大生命前沿。"),
         ("结算前生命", "取该目标生命上限下降前附近逐击的最小可靠当前生命。"),
         ("来源归属", "只在正式事件或已审计机制能绑定来源时归到角色；目标数据本身不证明来源。")),
    ),
}


_PLAYER_FORMULA_STEPS: dict[str, tuple[str, ...]] = {
    "panel_attribute": (
        "汇总当前角色的人物、弧盘等正式基础值。",
        "把同属性百分比来源同区相加，再乘基础值。",
        "最后加入同属性固定值来源。",
    ),
    "skill_multiplier": (
        "用攻击方本条伤害项绑定的技能与有效技能等级选倍率档。",
        "筛选正式作用于本技能标签的倍率修正并相加。",
        "用等级倍率乘以一加修正合计。",
    ),
    "direct_damage": (
        "确认本击伤害项、缩放面板、属性、暴击策略与攻击方。",
        "依次计算技能倍率、面板、增伤、暴击、防御、抗性和易伤。",
        "把每个适用的独立最终增伤逐项相乘。",
        "全程保留完整精度，只在最终出口向上取整。",
    ),
    "damage_increase": (
        "按本击来源、属性、技能和状态标签筛选攻击方增伤。",
        "同一增伤区内先相加。",
        "合计加 1 得到增伤倍率。",
    ),
    "vulnerability": (
        "绑定本击的受击目标。",
        "筛选命中时仍有效且适用于本击的目标受伤提升。",
        "同区相加后加 1 得到易伤倍率。",
    ),
    "critical": (
        "先完成除暴击外的完整精度伤害。",
        "按普通、固定暴击率、不可暴击或未知策略选择分支。",
        "未暴击与暴击候选分别在最终出口取整，再按暴击率计算期望。",
    ),
    "defense": (
        "从受击目标冻结属性包重建原始防御。",
        "应用攻击方防御穿透和目标防御降低。",
        "用伤害来源角色等级计算最终防御倍率。",
    ),
    "resistance": (
        "按本击伤害属性读取受击目标对应基础抗性。",
        "减去目标动态减抗与攻击方对应属性穿透。",
        "根据有效抗性正负选择分段公式。",
    ),
    "independent_final_damage": (
        "逐项核对正式效果的来源、目标、标签和触发条件。",
        "每个满足条件的最终增伤先转为一加增量。",
        "所有独立项逐项相乘，不并入通用增伤区。",
    ),
    "dot_damage": (
        "确认正式 DOT 身份、来源角色和本次受击目标。",
        "从同半场、同目标的命中前状态读取层数或受限系数。",
        "按持续直伤乘区和正式暴击策略计算本跳，再应用 DOT 专属最终区。",
        "只在本跳最终出口取整。",
    ),
    "topple_damage": (
        "固定同半场完整阵容与同一受击目标。",
        "每名角色分别读取等级曲线、倾陷强度、穿透和固有伤害属性。",
        "逐角色计算后求和，并在最终出口取整。",
    ),
    "weave_followup": (
        "记录每条符合条件的真实直伤及其属性。",
        "读取每条被记录原伤害实际来源角色的环合强度。",
        "12 秒结束时逐条追加并最终取整。",
    ),
    "settlement_rounding": (
        "保留公式中间乘区的完整精度。",
        "把最终结果限制为不小于零。",
        "只在机制定义的最终出口向上取整。",
    ),
    "max_hp_settlement": (
        "绑定同半场、同目标连续的旧/新最大生命前沿。",
        "用结算前当前生命占旧最大生命的比例折算有效损失。",
        "只把已确认来源的结算归到对应角色。",
    ),
}


class StaticCatalogMechanicsDetailProjector:
    """Project formula/model domain facts without owning catalog lookup state."""

    def __init__(self, terminology_service: StaticCatalogTerminologyService) -> None:
        self._terminology = terminology_service

    def formula_detail(self, formula: FormulaDetailView) -> MechanicsDetail:
        title, expression, player_variables = self.player_formula(formula)
        variables = tuple(
            PlayerField(label, meaning, "accent")
            for label, meaning in player_variables
        )
        steps = _PLAYER_FORMULA_STEPS.get(formula.key, ())
        sections = (
            PlayerSection("完整公式", (PlayerField("公式", expression, "formula"),)),
            PlayerSection("计算顺序", (PlayerField(
                "逐步过程",
                "\n".join(
                    f"{index}. {step}"
                    for index, step in enumerate(steps, start=1)
                ),
                "accent",
            ),)),
            PlayerSection("变量来源", variables),
            PlayerSection(
                "判定与限制",
                tuple(
                    PlayerField("适用条件", value, "accent")
                    for value in formula.applicable_when
                ) + tuple(
                    PlayerField("边界", value, "warning")
                    for value in formula.limitations
                ),
            ),
        )
        return MechanicsDetail(
            record_id=encode_record("formula", formula.key),
            card_kind="formula",
            title=title,
            subtitle=expression,
            family_key=FORMULA_FAMILY_BY_KEY[formula.key],
            badges=(FORMULA_CHAPTER_BY_KEY.get(formula.key, "公式"),),
            status=None,
            owner_label="全局公式",
            owner_link=None,
            redirect_only=False,
            sections=sections,
            identity_fields=(),
            evidence_stages=(),
            related_links=(),
            audit_references=tuple(
                f"{source.location}::{source.symbol}" for source in formula.sources
            ),
        )

    @staticmethod
    def player_formula(
        formula: FormulaDetailView,
    ) -> tuple[str, str, tuple[tuple[str, str], ...]]:
        """Return the curated Chinese projection; internal symbols never reach Qt."""
        try:
            return _PLAYER_FORMULAS[formula.key]
        except KeyError as exc:
            raise LookupError(f"公式缺少玩家中文投影：{formula.key}") from exc

    def model_detail(self, model: CounterfactualMatrixRow) -> MechanicsDetail:
        consumer = (
            "已接入战报反事实"
            if model.consumer_entries
            else "尚未接入生产反事实"
        )
        if model.key == "native_counterfactual_core":
            consumer = "差分验证组件，未接入生产"
        covered_entities = (
            (model.covered_entities,)
            if isinstance(model.covered_entities, str)
            else model.covered_entities
        )
        sections = (
            PlayerSection("怎么做的", (PlayerField(
                "建模方案",
                self.player_model_text(model.modeling_scheme),
            ),)),
            PlayerSection(
                "当前覆盖",
                tuple(
                    PlayerField(
                        "覆盖",
                        self.player_model_text(value),
                        "success",
                    )
                    for value in covered_entities
                ) or (PlayerField("覆盖", "尚无可确认对象", "warning"),),
            ),
            PlayerSection(
                "缺口与限制",
                tuple(
                    PlayerField("缺口", self._human_gap(code), "warning")
                    for code in model.gap_codes
                ) + tuple(
                    PlayerField(
                        "限制",
                        self.player_model_text(value),
                        "warning",
                    )
                    for value in model.limitations
                ),
            ),
        )
        return MechanicsDetail(
            record_id=encode_record("model", model.key),
            card_kind="model",
            title=model.mechanism,
            subtitle=model.scope,
            family_key=MODEL_FAMILY_BY_KEY[model.key],
            badges=(model.status_label, model.category),
            status=model.status,
            owner_label="公共机制",
            owner_link=None,
            redirect_only=False,
            sections=sections,
            identity_fields=(),
            evidence_stages=self._evidence_stages(model, consumer),
            related_links=self._model_links(model),
            audit_references=model.evidence,
            notice=consumer,
        )

    def player_model_text(self, value: str) -> str:
        text = str(value)
        for property_id in ("DefIgnore", "UnbalMax"):
            if property_id not in text:
                continue
            label = self._formal_attribute_name(property_id)
            replacement = (
                label
                if label == PLACEHOLDER_NAME
                else f"{label}（{property_id}）"
            )
            text = text.replace(property_id, replacement)
        replacements = (
            ("(scope_half,target_id)", "（半场、目标身份）"),
            ("Core v4 max_hp_reduction", "Core v4 最大生命下降事件"),
            ("8 Buff / 56 逐击公开差分", "8 个 Buff、56 次逐击公开差分"),
            (
                "complete/partial/unavailable/not_applicable",
                "完整、部分、不可用、不适用",
            ),
            ("not_applicable", "不适用"),
            ("unavailable", "不可用"),
            ("partial", "部分覆盖"),
            ("complete", "完整"),
            ("unknown", "未知"),
            ("nullable", "未量化状态"),
            ("ratio=1", "倍率 1"),
            ("contract", "契约"),
            ("UI 文案", "界面文案"),
        )
        for source, target in replacements:
            text = text.replace(source, target)
        return text

    def _formal_attribute_name(self, property_id: str) -> str:
        term = self._terminology.resolve("equipment_attribute", property_id)
        if term.name_available and term.display_name:
            return term.display_name
        return PLACEHOLDER_NAME

    @staticmethod
    def _model_links(
        model: CounterfactualMatrixRow,
    ) -> tuple[tuple[str, CatalogLink], ...]:
        formula_by_model = {
            "buff_ge_attributes": "damage_increase",
            "formal_dot_classification": "dot_damage",
            "dot_state_replay": "dot_damage",
            "topple_base_formula": "topple_damage",
            "topple_special_states": "topple_damage",
            "max_hp_settlement": "max_hp_settlement",
            "fixed_axis_replay": "direct_damage",
        }
        key = formula_by_model.get(model.key)
        if key is None:
            return ()
        return ((
            "查看相关公式",
            CatalogLink(
                "combat_mechanics",
                encode_record("formula", key),
                "formula",
            ),
        ),)

    @staticmethod
    def _human_gap(code: str) -> str:
        translations = {
            "native_production_consumer_unavailable": "尚未接入生产反事实",
            "native_stateful_mechanics_unavailable": "状态机制尚未迁移",
            "summon_lifecycle_axis_unavailable": "缺少召唤物生命周期轴",
            "summon_spatial_state_unavailable": "缺少召唤物空间状态",
            "summon_resource_state_unavailable": "缺少召唤物资源状态",
            "player_shield_axis_unavailable": "缺少玩家护盾状态轴",
            "dot_application_event_missing": "缺少正式 DOT 施加事件",
            "dot_state_kind_unmodeled": "仍有 DOT 类型尚未建模",
            "dot_axis_incomplete": "逐击轴不完整",
            "attachment_lifecycle_unobserved": "附着物生命周期尚未观测完整",
            "attachment_owner_unresolved": "附着物来源归属尚未确认",
            "buff_property_consumer_missing": "Buff 属性缺少已确认的公式消费者",
            "buff_target_condition_unresolved": "Buff 目标条件尚未确认",
            "buff_trigger_unresolved": "Buff 触发条件尚未确认",
            "max_hp_axis_continuity_unavailable": "最大生命状态轴不连续",
            "max_hp_source_attribution_unresolved": "最大生命变化来源尚未确认",
            "topple_duration_unreliable": "倾陷持续时间证据不可靠",
            "topple_special_settlement_unobserved": "倾陷特殊结算尚未观测",
            "treatment_event_evidence_missing": "缺少正式治疗事件证据",
            "treatment_formula_source_unresolved": "治疗公式来源尚未确认",
        }
        return translations.get(code, "缺口说明暂未本地化")

    @staticmethod
    def _evidence_stages(
        model: CounterfactualMatrixRow,
        consumer_summary: str,
    ) -> tuple[EvidenceStage, ...]:
        gaps = " ".join(model.gap_codes)
        unavailable = model.status == "unavailable"
        partial = model.status == "partial"

        def state(*tokens: str) -> str:
            if any(token in gaps for token in tokens):
                return "unavailable" if unavailable else "partial"
            return "complete" if not partial else "partial"

        return (
            EvidenceStage(
                "definition",
                "正式定义",
                "complete",
                "机制身份与适用范围已登记",
            ),
            EvidenceStage(
                "trigger",
                "触发证据",
                state("trigger", "event"),
                "按正式逐击或已审计事件判断，不从文案补猜",
            ),
            EvidenceStage(
                "state",
                "状态轴",
                state("state", "axis", "lifecycle", "duration"),
                "层数、持续时间与目标范围按当前证据传播",
            ),
            EvidenceStage(
                "hit",
                "逐击投影",
                state("owner", "target", "spatial"),
                "保留真实逐击，不生成缺失动作或命中",
            ),
            EvidenceStage(
                "formula",
                "公式消费",
                state("formula", "consumer"),
                "只进入已确认的伤害乘区或专用结算",
            ),
            EvidenceStage(
                "production",
                "生产反事实",
                "complete" if model.status == "complete" and model.consumer_entries
                else "partial" if model.consumer_entries
                else "unavailable",
                consumer_summary,
            ),
        )
