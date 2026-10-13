# 将战报编辑器多角色资料与shape字段从一个已验静态字节单元物化。
from __future__ import annotations

from typing import Any

from src.domain.battle_build_assumption import GRADUATION_ASSUMPTION_TITLE
from src.integrations.static_catalog_release import static_catalog_read_unit
from src.services.battle_report.build.equipment import battle_equipment_items
from src.services.battle_report.history.projection import character_analysis_scopes
from src.services.characters.character_shape_bonus_service import static_character_shape_profile_fields
from src.services.official_role.page import load_official_role_detail


def build_role_editor_details(
    user_database_path, static_path, build, build_edit, evidence, editor_facts,
    battle_replacement_items, equipment_editable, assumed_equipment, seed_from_role_page,
):
    """本场原始事实只作为输入；release token仅随detail运输，绝不写入profile。"""
    with static_catalog_read_unit(static_path) as read_unit:
        static_dao = read_unit.dao
        shape_fields_by_character = {
            int(original["character_id"]): static_character_shape_profile_fields(
                static_dao, int(original["character_id"])
            )
            for original in build.get("characters") or ()
        }
        edited_by_character = {
            int(row["character_id"]): row
            for row in ((build_edit or {}).get("characters") or ())
        }
        scopes_by_character = editor_facts[1] if editor_facts is not None else character_analysis_scopes(evidence)
        details: list[dict[str, Any]] = []
        detail_request_cache: dict[object, Any] = {}
        for original in build.get("characters") or ():
            character_id = int(original["character_id"])
            detail = load_official_role_detail(
                user_database_path,
                character_id,
                include_inventory_contexts=True,
                static_database_path=static_path,
                request_cache=detail_request_cache, static_read_unit=read_unit,
            )
            edited = edited_by_character.get(character_id)
            edited_profile = dict((edited or {}).get("profile") or {})
            if seed_from_role_page:
                profile = dict(detail.get("profile") or {})
                seed_source = "current_role_page"
                if edited_profile:
                    for key in (
                        "equipment_context_key",
                        "equipment_context_title",
                        "equipment_source_kind",
                        "equipment_override",
                    ):
                        if key in edited_profile:
                            profile[key] = edited_profile[key]
            elif edited is not None:
                profile = dict(edited_profile)
                seed_source = "edited_copy"
            else:
                profile = dict(original.get("profile") or {})
                seed_source = "battle_frozen"
            profile.update({
                "character_id": character_id,
                "observed_name": original.get("observed_name"),
                "ordinal": int(original.get("ordinal") or 0),
            })
            profile.update(shape_fields_by_character[character_id])
            frozen_items = battle_equipment_items(original)
            role_contexts = (
                dict(detail.get("equipment_contexts") or {})
                if equipment_editable
                else {}
            )
            for key, context in role_contexts.items():
                if key == "current":
                    context["source_kind"] = "role_page_current"
                elif key == "saved" or key.startswith("saved:"):
                    context["source_kind"] = "role_page_saved"
                context["source_title"] = str(context.get("title") or key)
            source_title = (
                GRADUATION_ASSUMPTION_TITLE if assumed_equipment else
                ("本场原始冻结配装" if equipment_editable else "导入包固化配装")
            )
            equipment_contexts = {
                "battle": {
                    "title": source_title,
                    "source_title": source_title,
                    "source_kind": (
                        "graduation_assumed" if assumed_equipment else
                        ("battle_frozen" if equipment_editable else "imported_locked")
                    ),
                    "items": frozen_items,
                    "calculation_items": frozen_items,
                    "replacement_items": battle_replacement_items,
                    "available": bool(frozen_items),
                }
            }
            if "equipment_override" in profile:
                edited_items = battle_equipment_items(
                    {"equipment": profile.get("equipment_override") or ()}
                )
                source_title = str(
                    profile.get("equipment_context_title") or "修改副本配装"
                )
                equipment_contexts["edited"] = {
                    "title": f"修改副本 · {source_title}",
                    "source_title": source_title,
                    "source_kind": str(
                        profile.get("equipment_source_kind") or "edited_copy"
                    ),
                    "items": edited_items,
                    "calculation_items": edited_items,
                    "replacement_items": battle_replacement_items,
                    "available": bool(edited_items),
                }
            equipment_contexts.update(role_contexts)
            detail["profile"] = profile
            detail["original_profile"] = dict(original.get("profile") or {})
            detail["editor_seed_source"] = seed_source
            detail["analysis_detail_scope"] = scopes_by_character.get(character_id)
            detail["equipment_contexts"] = equipment_contexts
            detail["equipment_editable"] = equipment_editable
            detail["selected_equipment_context_key"] = (
                "edited" if "equipment_override" in profile else "battle"
            )
            details.append(detail)
    return details
