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


class StaticCatalogMechanicsDetailProjector:
    """Project formula/model domain facts without owning catalog lookup state."""

    def __init__(self, terminology_service: StaticCatalogTerminologyService) -> None:
        self._terminology = terminology_service

    def formula_detail(self, formula: FormulaDetailView) -> MechanicsDetail:
        variables = tuple(
            PlayerField(symbol, meaning, "accent")
            for symbol, meaning in formula.variables
        )
        sections = (
            PlayerSection("公式", (PlayerField("表达式", formula.expression, "formula"),)),
            PlayerSection("变量", variables),
            PlayerSection(
                "适用范围",
                tuple(PlayerField("条件", row) for row in formula.applicable_when),
            ),
            PlayerSection(
                "限制",
                tuple(
                    PlayerField("边界", row, "warning")
                    for row in formula.limitations
                ),
            ),
        )
        links = tuple(
            (
                f"查找提供 {symbol} 的机制",
                CatalogLink(
                    "combat_mechanics",
                    encode_record("search", symbol),
                    "variable",
                ),
            )
            for symbol, _meaning in formula.variables
        )
        return MechanicsDetail(
            record_id=encode_record("formula", formula.key),
            card_kind="formula",
            title=formula.title,
            subtitle=formula.expression,
            family_key="formula",
            badges=(
                FORMULA_CHAPTER_BY_KEY.get(formula.key, "公式"),
                formula.boundary_label,
            ),
            status=None,
            owner_label="全局公式",
            owner_link=None,
            redirect_only=False,
            sections=sections,
            identity_fields=(),
            evidence_stages=(),
            related_links=links,
            audit_references=tuple(
                f"{source.location}::{source.symbol}" for source in formula.sources
            ),
        )

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
