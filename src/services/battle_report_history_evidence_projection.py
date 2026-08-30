# 把逐击轴的正式 ID 投影为本地化展示字段，保持历史服务只负责编排。
"""Evidence-localization support for battle report history loading."""

from __future__ import annotations

from typing import Any

from src.services.skill_name_rendering_service import (
    SkillNameRenderingService,
    project_immediate_nightmare_source_names,
)


class BattleReportHistoryEvidenceProjectionMixin:
    """Localize mutable DAO evidence before it enters the immutable analysis."""

    def _localize_axis_evidence(
        self,
        evidence: dict[str, Any] | None,
    ) -> None:
        static_path = self._dependencies.static_database_path
        if evidence is None or static_path is None:
            return
        renderer = self._skill_name_renderer
        if renderer is None:
            renderer = SkillNameRenderingService.from_static_database(static_path)
            self._skill_name_renderer = renderer

        for hit in evidence.get("hits") or ():
            ability_id = str(hit.get("ability_name") or "")
            damage_id = str(hit.get("damage_name") or "")
            follow_up_damage_id = str(hit.get("follow_up_damage_name") or "")
            identity = renderer.render_axis_identity(
                ability_id=ability_id,
                damage_id=damage_id,
                gameplay_effect_index=hit.get("gameplay_effect_index"),
                gameplay_effect_name=hit.get("gameplay_effect_name"),
                damage_component=hit.get("damage_component"),
                attack_type=hit.get("attack_type"),
            )
            incoming = (
                str(hit.get("direction") or "").strip().casefold() == "incoming"
            )
            hit["ability_display_name"] = (
                "敌方攻击"
                if incoming
                and identity.skill_name in {"未识别技能", "未归因伤害"}
                else identity.skill_name
            )
            hit["damage_display_name"] = (
                "受击"
                if incoming
                and identity.damage_name in {"未识别伤害", "来源字段缺失"}
                else identity.damage_name
            )
            if identity.gameplay_effect_id:
                hit["gameplay_effect_name"] = identity.gameplay_effect_id
            resolved_damage_id = identity.gameplay_effect_id or damage_id
            resolved_ability_id = renderer.resolve_ability_id(
                ability_id,
                resolved_damage_id,
                fallback_damage_id=damage_id,
            )
            if resolved_ability_id:
                hit["ability_name"] = resolved_ability_id
                ability_id = resolved_ability_id
            hit["attack_type"] = renderer.resolve_attack_type(
                resolved_damage_id,
                captured=hit.get("attack_type"),
            )
            damage_attribute = renderer.resolve_damage_attribute(
                resolved_damage_id,
                captured=hit.get("damage_attribute"),
            )
            if (
                damage_attribute in {"", "unknown", "none"}
                and damage_id
                and damage_id.casefold() != resolved_damage_id.casefold()
            ):
                damage_attribute = renderer.resolve_damage_attribute(
                    damage_id,
                    captured=damage_attribute,
                )
            hit["damage_attribute"] = damage_attribute
            if follow_up_damage_id:
                follow_up = renderer.render_axis_identity(
                    ability_id=ability_id,
                    damage_id=follow_up_damage_id,
                    gameplay_effect_index=hit.get("gameplay_effect_index"),
                    gameplay_effect_name=hit.get("gameplay_effect_name"),
                    damage_component=hit.get("follow_up_damage_component"),
                    attack_type=hit.get("follow_up_attack_type"),
                )
                hit["follow_up_damage_display_name"] = follow_up.damage_name
                hit["follow_up_damage_attribute"] = renderer.resolve_damage_attribute(
                    follow_up.gameplay_effect_id or follow_up_damage_id,
                    captured=hit.get("follow_up_damage_attribute"),
                )
        project_immediate_nightmare_source_names(evidence.get("hits") or ())


__all__ = ["BattleReportHistoryEvidenceProjectionMixin"]
