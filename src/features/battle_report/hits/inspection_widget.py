# 逐击概览按乘区、参与方和实际贡献打开详情，只展示采集与核心已有结果。
from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import TextActionButton, plain_label, resolve_text_catalog
import json

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLayout,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.services.battle_report.buffs.hit_explanation import battle_buff_property_label
from .formula_overview import HitFormulaOverview


_LOCALE_PREFIX = "battle_report.hit_inspection."


def _text(text_catalog, suffix, fallback, **values):
    """本文件用户可见文案统一挂在本模块前缀下；目录缺该 key 时回退原中文。"""
    return localized_text(text_catalog, f"{_LOCALE_PREFIX}{suffix}", fallback, **values)


_ATTRIBUTE_TEXT = {
    "hp": ("current_hp", "当前生命"),
    "maxHp": ("max_hp", "最大生命"),
    "shield": ("shield", "护盾"),
    "attack": ("attack", "攻击力"),
    "defense": ("defense", "防御力"),
    "crit": ("crit_rate", "暴击率"),
    "critDamage": ("crit_damage", "暴击伤害"),
    "mag": ("mag", "环合强度"),
    "damageUpGeneral": ("damage_up_general", "通用增伤"),
    "chargeCurrent": ("charge_current", "当前充能"),
    "chargeMax": ("charge_max", "充能上限"),
    "unbalCurrent": ("unbal_current", "当前倾陷"),
    "unbalMax": ("unbal_max", "倾陷上限"),
    "unbalIntensity": ("unbal_intensity", "倾陷强度"),
    "unbalAccrueEfficiency": ("unbal_accrue_efficiency", "倾陷积累效率"),
    "unbalAntiAccrueEfficiency": ("unbal_anti_accrue_efficiency", "倾陷抗积累效率"),
    "unbalSpeed": ("unbal_speed", "倾陷速度"),
    "unbalBonus": ("unbal_bonus", "倾陷加成"),
    "unbalReduceNatur": ("unbal_reduce_natur", "倾陷自然减少"),
    "unbalValueAdd": ("unbal_value_add", "倾陷值附加"),
    "isBalancedingState": ("unbal_state", "倾陷相关状态"),
    "resistNormal": ("resist_normal", "普通抗性"),
    "resistCosmos": ("resist_cosmos", "光抗性"),
    "resistNature": ("resist_nature", "灵抗性"),
    "resistIncantation": ("resist_incantation", "咒抗性"),
    "resistChaos": ("resist_chaos", "暗抗性"),
    "resistPsyche": ("resist_psyche", "魂抗性"),
    "resistLakshana": ("resist_lakshana", "相抗性"),
    "resistPsychically": ("resist_psychically", "心灵抗性"),
    "damageUpNormal": ("damage_up_normal", "普通增伤"),
    "damageUpCosmos": ("damage_up_cosmos", "光增伤"),
    "damageUpNature": ("damage_up_nature", "灵增伤"),
    "damageUpIncantation": ("damage_up_incantation", "咒增伤"),
    "damageUpChaos": ("damage_up_chaos", "暗增伤"),
    "damageUpPsyche": ("damage_up_psyche", "魂增伤"),
    "damageUpLakshana": ("damage_up_lakshana", "相增伤"),
    "damageUpPsychically": ("damage_up_psychically", "心灵增伤"),
}


def attribute_text(property_id, *, text_catalog=None):
    """属性表行标题；未登记的属性仍显示原始属性标识。"""
    suffix, fallback = _ATTRIBUTE_TEXT.get(property_id, ("", property_id))
    return _text(text_catalog, f"attribute.{suffix}", fallback) if suffix else fallback


def shown(value, *, text_catalog=None):
    if value is None:
        return _text(text_catalog, "value.unavailable", "未取得")
    if isinstance(value, bool):
        return ui_enum_text(text_catalog, "是" if value else "否")
    return f"{value:.8g}" if isinstance(value, (int, float)) else str(value)


def native_hit(hit):
    if hit.native_evidence is None:
        return {}
    try:
        value = json.loads(hit.native_evidence.payload_json)
        raw = value.get("rawHit", {})
        return raw if isinstance(raw, dict) else {}
    except (ValueError, TypeError, AttributeError):
        return {}


def term_side(term):
    """Use source identity, not the factor containing both participants."""
    if (term.term_id.startswith("target:") or term.source_group == "target"
            or term.property_id.startswith(("Resistance:", "DamageResist"))):
        return "victim"
    return "formula"


_SOURCE_TEXT = {
    "native_panel": ("measured_attributes", "实测属性"),
    "native_contribution": ("measured_contribution", "实测贡献"),
    "counterfactual_panel": ("evidence_inferred", "证据推导"),
    "panel_inclusion_inferred": ("inclusion_inferred", "推断（包含关系）"),
    "character": ("frozen_character", "冻结角色配置"),
    "resolved": ("core_resolved", "核心解析值"),
    "target": ("target_config", "目标配置／推断"),
    "buff": ("buff_contribution", "Buff 贡献（来源见本击 Buff）"),
}


def term_source(term, projection=None, *, text_catalog=None):
    if term.source_group == "buff" and projection is not None:
        decisions = [d for d in projection.decisions if d.interval_id in term.term_id]
        return contribution_source(any(d.interval_id.startswith("native-applied:") for d in decisions), decisions,
                                   text_catalog=text_catalog)
    suffix, fallback = _SOURCE_TEXT.get(term.source_group, ("frozen", "冻结来源／核心结果"))
    return _text(text_catalog, f"source.{suffix}", fallback)


def contribution_source(direct, decisions, *, text_catalog=None):
    if direct:
        return _text(text_catalog, "contribution.native_applied", "采用实测写入值")
    applied = [d for d in decisions if d.status == "applied"]
    observed = [d for d in applied if d.observed_stacks is not None]
    if observed:
        return (_text(text_catalog, "contribution.observed", "状态有证据／贡献模型")
                if len(observed) == len(applied)
                else _text(text_catalog, "contribution.partial_observed", "部分状态有证据／贡献模型"))
    return _text(text_catalog, "contribution.inferred", "模型推断贡献")


class HitInspectionWidget(QWidget):
    """One display component for the analysis and counterfactual detail dialogs."""

    technical_requested = Signal()

    def __init__(self, parent=None, *, game_ui_asset_root=None, text_catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.layout = QVBoxLayout(self)
        self.layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(15)
        self.overview = HitFormulaOverview(self, game_ui_asset_root=game_ui_asset_root, text_catalog=text_catalog)
        self.overview.participant_requested.connect(self.participant)
        self.overview.factor_requested.connect(self.factor)
        self.layout.addWidget(self.overview)
        footer = QHBoxLayout()
        self.buff_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.battle_report.init.buff_button'))
        self.buff_button.clicked.connect(self.contributions)
        self.evidence_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.battle_report.init.evidence_button'))
        self.evidence_button.clicked.connect(self.formula_observations)
        self.technical_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.battle_report.init.technical_dialog'))
        self.technical_button.clicked.connect(self.technical_requested)
        for button in (self.buff_button, self.evidence_button):
            footer.addWidget(button)
        footer.addStretch(1)
        footer.addWidget(self.technical_button)
        self.layout.addLayout(footer)
        self._popup = None

    def set_hit(self, hit, replay=None, projection=None, *, participant_names=None, target_resolutions=()):
        self.hit, self.replay, self.projection = hit, replay, projection
        self.raw = native_hit(hit)
        if self._popup is not None:
            self._popup.close()
            self._popup.deleteLater()
            self._popup = None
        self.overview.set_hit(hit, replay, self.raw, participant_names=participant_names, target_resolutions=target_resolutions)
        self.evidence_button.setVisible(bool((self.raw.get("executionEvidence") or {}).get("applicationEvidence")))

    def table(self, title, headers, rows, note=""):
        if self._popup is not None:
            self._popup.close()
            self._popup.deleteLater()
        dialog = QDialog(self.window())
        dialog.setWindowTitle(title)
        root = QVBoxLayout(dialog)
        if note:
            label = plain_label(note)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            root.addWidget(label)
        table = QTableWidget(len(rows), len(headers), dialog)
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                text = shown(value, text_catalog=self.text_catalog)
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                table.setItem(r, c, item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.resizeColumnsToContents()
        limits = (200, 85, 140, 70, 110, 190) if len(headers) == 7 else (300, 130, 170)
        for column, limit in enumerate(limits[:len(headers) - 1]):
            table.setColumnWidth(column, min(table.columnWidth(column), limit))
        table.horizontalHeader().setSectionResizeMode(len(headers) - 1, QHeaderView.ResizeMode.Stretch)
        table.resizeRowsToContents()
        root.addWidget(table)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.hide)
        root.addWidget(close)
        fit_dialog_to_available_screen(dialog, QSize(1050, 580))
        self._popup = dialog
        dialog.show()

    def factor(self, factor):
        rows = [(t.label or t.property_id, t.value, t.source_name or t.source_group,
                 t.evidence_basis) for t in factor.terms]
        result = _text(self.text_catalog, "factor.result_value", "本乘区结果：{value}",
                       value=shown(factor.value, text_catalog=self.text_catalog))
        note = f"{factor.formula}\n{result}\n{factor.evidence_basis}"
        if factor.factor_id == "critical" and self.replay.critical_state in {"non_critical", "not_applicable"}:
            skipped = _text(self.text_catalog, "factor.critical_not_applied",
                            "本击未采用暴击候选，乘区采用 1。以下保留暴击候选的倍率与来源。")
            note = f"{skipped}\n{note}"
        self.table(factor.label, [resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.factor.self'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.factor.self_2'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.init.table'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.factor.self_3')], rows, note)

    def participant(self, side):
        raw = self.raw
        snapshot = raw.get(f"{side}Attributes") or {}
        title = {
            "attacker": _text(self.text_catalog, "participant.title_attacker", "攻击方属性"),
            "victim": _text(self.text_catalog, "participant.title_victim", "受击方属性"),
            "formula": _text(self.text_catalog, "participant.title_formula", "公式属性方"),
        }[side]
        rows = []
        # Identity, status and original getter source are carried with each sample.
        matched = (side != "formula" and snapshot.get("actorIndex") == raw.get(f"{side}ObjectIndex")
                   and snapshot.get("actorSerial", 0) > 0)
        unavailable = _text(self.text_catalog, "value.unavailable", "未取得")
        measured = _text(self.text_catalog, "value.measured", "实测")
        for key, field in snapshot.get("values", {}).items():
            known = matched and field.get("status") == "ok" and field.get("value") is not None
            rows.append((attribute_text(key, text_catalog=self.text_catalog), field.get("value") if known else None,
                         measured if known else unavailable, field.get("source", "")))
        if side == "victim":
            recorded = _text(self.text_catalog, "source.recorded", "记录值")
            life_evidence = _text(self.text_catalog, "evidence.life_snapshot", "战报原始生命证据")
            rows.extend((label, value, recorded, life_evidence) for label, value in (
                (_text(self.text_catalog, "participant.hp_before", "结算前生命"), self.hit.target_hp_before),
                (_text(self.text_catalog, "participant.hp_after", "结算后生命"), self.hit.target_hp_after),
                (_text(self.text_catalog, "participant.hp_max", "生命上限"), self.hit.target_max_hp),
            ) if value is not None)
        if self.replay is not None:
            for factor in self.replay.factors:
                if side == "attacker" and self.replay.formula_panel_character_id not in (0, None, self.hit.character_id):
                    continue
                rows.extend((_text(self.text_catalog, "participant.formula_input", "公式输入 · {term}",
                                   term=t.label or t.property_id), t.value,
                             term_source(t, self.projection, text_catalog=self.text_catalog),
                             t.evidence_basis or t.source_name)
                            for t in factor.terms if (side == "victim") == (term_side(t) == "victim"))
        self.table(title, [resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.participant.self'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.participant.self_2'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.participant.self_3'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.factor.self_3')], rows,
                   _text(self.text_catalog, "participant.note",
                         "观测阶段：{stage}；观测编号：{id}。\n动态观测按游戏原始单位展示；公式输入来自计算核心。未取得第三方属性时不借用攻击方属性。",
                         stage=snapshot.get("stage", unavailable), id=snapshot.get("id", unavailable)))

    def contributions(self):
        execution = self.raw.get("executionEvidence") or {}
        applications = execution.get("applicationEvidence") or {}
        rows = []
        identity = execution.get("identity") or {}
        catalog = self.text_catalog
        unavailable = _text(catalog, "value.unavailable", "未取得")
        measured_write = _text(catalog, "source.measured_write", "实测写入")
        raw_application = _text(catalog, "evidence.native_application", "原始应用证据（与下方采用值不重复相加）")
        relation_text = {"included": _text(catalog, "relation.included", "已包含"),
                         "excluded": _text(catalog, "relation.excluded", "未包含")}
        relation_pending = _text(catalog, "relation.pending", "包含关系待定")
        usage_observed = _text(catalog, "usage.formula_observed", "公式读取已观测")
        usage_unobserved = _text(catalog, "usage.formula_unobserved", "公式读取尚未观测")
        for item in applications.get("items", []):
            if item.get("source") != "native_application":
                continue
            asc = item.get("recipientAsc") or {}
            weak = [asc.get("objectIndex"), asc.get("objectSerial")]
            side = (_text(catalog, "side.attacker", "攻击方") if weak == identity.get("sourceAsc")
                    else _text(catalog, "side.victim", "受击方") if weak == identity.get("targetAsc")
                    else _text(catalog, "side.other", "其他对象"))
            relation = relation_text.get(item.get("inclusion"), relation_pending)
            usage = usage_observed if item.get("formulaUse") == "observed" else usage_unobserved
            buff = item.get("buff") or {}
            property_name = battle_buff_property_label(str(item.get("property") or ""))
            rows.append((_text(catalog, "contributions.native_write", "原生属性写入 · {property}",
                               property=property_name),
                         side, item.get("property"), item.get("value"), measured_write, raw_application,
                         _text(catalog, "contributions.native_detail",
                               "{relation}；{usage}；层数 {stacks}；{operation}；原始效果标识：{effect}",
                               relation=relation, usage=usage,
                               stacks=shown(item.get("stacks"), text_catalog=catalog),
                               operation=item.get("operation", ""),
                               effect=buff.get("className") or buff.get("name") or unavailable)))
        if self.projection is not None:
            terms = [t for f in (() if self.replay is None else self.replay.factors) for t in f.terms]
            usage_measured_extra = _text(catalog, "usage.measured_with_extra", "该属性按实测基准与本击贡献解析；包含关系见乘区")
            usage_measured_covered = _text(catalog, "usage.measured_covered", "实测面板已覆盖该属性；来源拆分用于解释／反事实")
            usage_formula_input = _text(catalog, "usage.formula_input", "作为公式输入")
            usage_model_only = _text(catalog, "usage.model_only", "当前公式未单独使用；仅保留模型关联")
            scope_text = {"self": _text(catalog, "side.formula", "攻击／公式属性方"),
                          "target": _text(catalog, "side.victim", "受击方"),
                          "team": _text(catalog, "side.team", "队伍")}
            for modifier in self.projection.modifiers:
                decisions = [d for d in self.projection.decisions if d.interval_id in modifier.interval_ids]
                direct = any(i.startswith("native-applied:") for i in modifier.interval_ids)
                measured = any(t.source_group == "native_panel" and
                               (t.property_id == modifier.property_id or
                                t.term_id == "native:attack" and modifier.property_id in {"AtkBase", "AtkUp", "AtkAdd"} or
                                t.term_id == "native:maxHp" and modifier.property_id in {"HPMaxBase", "HPMaxUp", "HPMaxAdd"})
                               and (term_side(t) == "victim") == (modifier.target_scope == "target") for t in terms)
                referenced = any(any(i in t.term_id for i in modifier.interval_ids) for t in terms)
                has_extra = any(t.term_id.startswith("native-extra:") and t.value != 0 for t in terms)
                usage = (usage_measured_extra if measured and has_extra else
                         usage_measured_covered if measured else
                         usage_formula_input if referenced else usage_model_only)
                reasons = "；".join(reason for d in decisions for reason in d.reasons)
                rows.append(("、".join(modifier.buff_names),
                             scope_text.get(modifier.target_scope, modifier.target_scope),
                             modifier.property_id, modifier.additive_value,
                             contribution_source(direct, decisions, text_catalog=catalog), usage,
                             reasons or _text(catalog, "contributions.projection_confidence",
                                              "核心投影；置信度 {confidence}", confidence=modifier.confidence)))
        self.table(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.init.buff_button'), ["Buff", resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.init.table_2'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.participant.self'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.contributions.self'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.init.table'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.contributions.self_2'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.contributions.self_3')], rows,
                   _text(catalog, "contributions.note",
                         "上方实测写入与核心采用值是两个视角，不能把两者再次相加。\n"
                         "当前直接证据范围：{coverage}；状态：{status}。\n"
                         "应用列表并不覆盖所有 Buff。实测面板与各 Buff 的来源拆分是不同证据；"
                         "模型关联不代表本击已实测应用，详见计算用途。",
                         coverage=applications.get("coverage", unavailable),
                         status=applications.get("status", unavailable)))

    def formula_observations(self):
        execution = self.raw.get("executionEvidence") or {}
        evidence = execution.get("applicationEvidence") or {}
        bound = (self.raw.get("executionId") is not None
                 and str(self.raw["executionId"]) == str(execution.get("executionId")))
        rows = []
        catalog = self.text_catalog
        used_attack = _text(catalog, "observations.used_attack", "实际读取攻击力")
        base_result = _text(catalog, "observations.base_result", "基础项返回值")
        client_measured = _text(catalog, "source.client_measured", "客户端实测")
        missing_read_evidence = _text(catalog, "source.missing_read_evidence", "缺少读取证据")
        execution_link = _text(catalog, "evidence.execution_link", "计算实例与读取路径关联")
        phase_before = _text(catalog, "observations.phase_before", "进入基础项函数")
        phase_after = _text(catalog, "observations.phase_after", "基础项函数返回")
        aggregate_attack = _text(catalog, "observations.aggregate_attack", "聚合攻击力")
        local_observation = _text(catalog, "source.local_attribute", "本地属性观测")
        base_value = _text(catalog, "observations.base_value", "基础值")
        current_value = _text(catalog, "observations.current_value", "当前值")
        server_unconfirmed = _text(catalog, "observations.server_unconfirmed", "服务器同期值未确认")
        for term in evidence.get("baseTerms", []):
            component = term.get("component") or {}
            identity = _text(catalog, "observations.component", "组件 {index}:{serial}",
                             index=component.get("objectIndex"), serial=component.get("objectSerial"))
            witness = (bound and term.get("returned") is True and term.get("identityStable") is True
                       and term.get("attackReadPathObserved") is True)
            for key, label in (("usedAttack", used_attack), ("result", base_result)):
                field = term.get(key) or {}
                known = field.get("status") == "ok" and (witness if key == "usedAttack" else bound and term.get("returned"))
                rows.append((identity, label, field.get("value") if known else None,
                             client_measured if known else missing_read_evidence, execution_link))
            for phase, title in (("before", phase_before), ("after", phase_after)):
                inputs = term.get(phase) or {}
                attack = inputs.get("attack") or {}
                rows.append((title, aggregate_attack, attack.get("value"), local_observation, identity))
                for name in ("AtkBase", "AtkUp", "AtkAdd", "OnlineAtkRatio"):
                    values = inputs.get(name) or {}
                    for part, caption in (("base", base_value), ("current", current_value)):
                        field = values.get(part) or {}
                        rows.append((title, f"{name} · {caption}", field.get("value"),
                                     local_observation,
                                     server_unconfirmed if name == "OnlineAtkRatio" else identity))
        self.table(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.formula_observations.self'), [resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.formula_observations.self_2'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.formula_observations.self_3'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.init.table_3'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.init.table'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.factor.self_3')], rows,
                   _text(catalog, "observations.note",
                         "这里展示客户端实际函数调用。基础项返回值不是服务器最终伤害；属性写入与面板包含关系仍分别判断。"))
