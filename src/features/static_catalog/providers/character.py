# 将角色资料域映射到公共只读提供器契约。
"""Adapt the character catalog domain to the common read-only provider contract."""

from __future__ import annotations

from pathlib import Path

from src.features.static_catalog.contracts import (
    CatalogDetail,
    CatalogDomain,
    CatalogItem,
    CatalogPage,
    CatalogReference,
    CatalogSection,
    StaticCatalogRelease,
)
from src.features.static_catalog.providers._shared import (
    annotation,
    derived,
    ensure_release_metadata,
    ensure_release_path,
    lines,
    official,
)
from src.services.static_catalog_character_models import CharacterDetail, CharacterSummary
from src.services.static_catalog_character_service import StaticCatalogCharacterService
from src.storage.sqlite.static_catalog_character_queries import (
    StaticCatalogCharacterQueries,
)


CHARACTER_DOMAIN = CatalogDomain(
    key="character",
    label="角色数据",
    description="等级面板、突破状态、技能消耗、觉醒、好感度、培养与毕业模板",
    order=10,
)


class CharacterCatalogProvider:
    """Own one read-only character DAO and expose only common catalog DTOs."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path).resolve()
        self._queries = StaticCatalogCharacterQueries(self._database_path)
        self._service = StaticCatalogCharacterService(self._queries)
        self._closed = False

    @property
    def domain(self) -> CatalogDomain:
        return CHARACTER_DOMAIN

    def close(self) -> None:
        if not self._closed:
            self._queries.close()
            self._closed = True

    def search(
        self,
        release: StaticCatalogRelease,
        *,
        query: str,
        offset: int,
        limit: int,
    ) -> CatalogPage:
        ensure_release_path(release, self._database_path)
        page = self._service.list_characters(query=query, offset=offset, limit=limit)
        self._ensure_dataset(release, page.dataset)
        return CatalogPage(
            items=tuple(self._item(item) for item in page.items),
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        )

    def detail(
        self, release: StaticCatalogRelease, record_id: str,
    ) -> CatalogDetail | None:
        ensure_release_path(release, self._database_path)
        try:
            character_id = int(record_id)
        except (TypeError, ValueError):
            return None
        detail = self._service.get_character_detail(character_id)
        if detail is None:
            return None
        self._ensure_dataset(release, detail.dataset)
        growth = self._service.list_growth(character_id, limit=200)
        combat = self._service.list_combat_links(character_id, limit=500)
        sections = [
            self._identity(detail),
            self._availability(detail),
            CatalogSection(
                title="1–80 级面板（含临界等级突破前后）",
                fields=tuple(
                    official(
                        f"Lv.{point.level} · 阶段 {point.breakthrough_stage} · {point.state}",
                        f"生命 {point.hp_base:g} / 攻击 {point.atk_base:g} / 防御 {point.def_base:g}",
                    )
                    for point in growth.items
                ) or (derived("面板状态", "unavailable：该角色没有成长行"),),
            ),
        ]
        sections.extend(self._detail_sections(detail))
        if combat.items:
            sections.append(CatalogSection(
                title="GA、GE 与 Buff 正式关系",
                fields=tuple(
                    official(
                        f"{link.relationship_kind} · {link.binding_kind}",
                        " / ".join(filter(None, (
                            link.ability_id,
                            link.event_tag,
                            link.gameplay_effect_id,
                            link.buff_definition_id,
                            link.effect_asset_path,
                        ))),
                        copyable=True,
                    )
                    for link in combat.items
                ),
            ))
        return CatalogDetail(
            item=self._item(detail.character),
            sections=tuple(sections),
            notes=(
                "人物升级和突破材料缺失时保持 unavailable，不从文本、同名角色或账号数据猜测。",
                "技能升级消耗保留正式物品 ID 与数量；发行库没有通用材料中文名目录。",
            ),
        )

    @staticmethod
    def _item(summary: CharacterSummary) -> CatalogItem:
        subtitle = (
            f"{summary.element_label} · 技能 {summary.skill_count} · "
            f"觉醒 {summary.awakening_count} · 成长状态 {summary.growth_count}"
        )
        return CatalogItem(
            domain_key=CHARACTER_DOMAIN.key,
            record_id=str(summary.character_id),
            title=summary.name_zh,
            subtitle=subtitle,
        )

    @staticmethod
    def _ensure_dataset(release: StaticCatalogRelease, dataset: object) -> None:
        ensure_release_metadata(
            release,
            dataset_id=str(getattr(dataset, "dataset_id")),
            schema_version=int(getattr(dataset, "schema_version")),
            importer_version=int(getattr(dataset, "importer_version")),
            built_at_utc=str(getattr(dataset, "built_at_utc")),
        )

    @staticmethod
    def _identity(detail: CharacterDetail) -> CatalogSection:
        item = detail.character
        return CatalogSection(
            title="角色身份与目录",
            fields=(
                official("character_id", item.character_id, copyable=True),
                official("中文名", item.name_zh),
                official("属性", f"{item.element_label} · {item.element_type or '未保留'}"),
                official("组别", item.group_type),
                official("Actor 路径", item.actor_path, copyable=True),
                official("大陆服展示时间", item.mainland_show_time),
                annotation("逻辑角色键", item.logical_character_key, copyable=True),
                annotation("规范角色 ID", item.canonical_character_id, copyable=True),
                annotation("分类", item.classification),
                annotation("注解来源", detail.annotation_source),
            ),
        )

    @staticmethod
    def _availability(detail: CharacterDetail) -> CatalogSection:
        return CatalogSection(
            title="数据可用性",
            fields=(
                derived("成长状态数", detail.growth_count),
                derived("战斗关系数", detail.combat_link_count),
                *(derived(gap.label, f"{gap.status}：{gap.reason}") for gap in detail.gaps),
            ),
        )

    def _detail_sections(self, detail: CharacterDetail) -> list[CatalogSection]:
        sections: list[CatalogSection] = []
        if detail.likeability is not None:
            likeability = detail.likeability
            sections.append(CatalogSection(
                title=f"好感度 {likeability.required_level} 级加成",
                fields=(
                    official("modify_data_id", likeability.modify_data_id, copyable=True),
                    *(official(
                        prop.display_name,
                        f"{prop.value:g} · {prop.modifier_operation}",
                    ) for prop in likeability.properties),
                ),
            ))
        for awakening in detail.awakenings:
            sections.append(CatalogSection(
                title=f"觉醒 {awakening.ordinal} · {awakening.title_zh or awakening.effect_id}",
                fields=(
                    official("effect_id", awakening.effect_id, copyable=True),
                    official("类型", awakening.awaken_type),
                    official("说明", awakening.description_zh),
                    official("Gameplay Effects", lines(list(awakening.gameplay_effect_ids)), copyable=True),
                    official(
                        "技能等级修改",
                        lines([f"{row.skill_id} {row.level_delta:+d}" for row in awakening.skill_level_bonuses]),
                    ),
                    official(
                        "结构化效果",
                        lines([f"{row.path} = {row.value_json}" for row in awakening.structured_effects]),
                    ),
                ),
            ))
        for skill in detail.skills:
            sections.append(CatalogSection(
                title=f"技能 · {skill.name_zh or skill.skill_id}",
                fields=(
                    official("skill_id", skill.skill_id, copyable=True),
                    official("类型 / 序号", f"{skill.ability_type} / {skill.ability_index}"),
                    official("Gameplay Tag", skill.gameplay_tag, copyable=True),
                    official("GA 路径", skill.gameplay_ability_path, copyable=True),
                    official("GE 路径", skill.gameplay_effect_path, copyable=True),
                    official(
                        "等级、解锁与消耗",
                        lines([
                            f"Lv.{level.level} · 突破 {level.required_breakthrough_stage} · "
                            f"觉醒 {level.required_awaken_level} · "
                            + (", ".join(
                                f"{cost.item_id} × {cost.quantity:g}" for cost in level.costs
                            ) or "无材料")
                            for level in skill.levels
                        ]),
                    ),
                    official(
                        "正式说明",
                        lines([
                            " · ".join(filter(None, (row.title_zh, row.description_zh, row.unlock_description_zh)))
                            for row in skill.descriptions
                        ]),
                    ),
                    official(
                        "等级提示与伤害索引",
                        lines([
                            f"{row.name_id or row.ordinal} · {row.value_description_zh or row.description_zh or '未保留'}"
                            f" · damage={','.join(row.damage_effect_ids) or '无'}"
                            for row in skill.level_hints
                        ]),
                    ),
                ),
            ))
        if detail.cultivation is not None:
            guide = detail.cultivation
            references = tuple(
                CatalogReference("查看推荐弧盘", "fork", fork_id)
                for fork_id, _name, _description in guide.fork_recommendations
            )
            sections.append(CatalogSection(
                title="养成指南",
                fields=(
                    official("评分阈值", f"S {guide.s_score:g} / A {guide.a_score:g}"),
                    official("推荐属性", lines([f"{name} ({property_id})" for property_id, name in guide.attribute_recommendations])),
                    official("推荐弧盘", lines([f"{name} ({fork_id})" for fork_id, name, _description in guide.fork_recommendations])),
                    official(
                        "阶段路线",
                        lines([
                            f"阶段 {row.ordinal}：角色 {row.character_level} / 弧盘 {row.fork_level} / "
                            f"空幕 {row.core_item_id} Lv.{row.core_level} / 驱动 Lv.{row.equipment_level} / "
                            + "技能："
                            + (
                                ", ".join(
                                    f"{sex_kind}:{ability_id} Lv.{recommended_level}"
                                    for sex_kind, ability_id, recommended_level
                                    in row.recommended_skills
                                )
                                or "无正式阶段技能"
                            )
                            for row in guide.stages
                        ]),
                    ),
                ),
                references=references,
            ))
        if detail.graduation is not None:
            graduation = detail.graduation
            references = (
                (CatalogReference("查看毕业弧盘", "fork", graduation.fork_id),)
                if graduation.fork_id else ()
            )
            sections.append(CatalogSection(
                title="毕业模板（项目派生）",
                fields=(
                    annotation("来源类型", graduation.source_kind),
                    annotation("弧盘", f"{graduation.fork_name_zh or '未保留'} ({graduation.fork_id or '无'})"),
                    annotation("弧盘等级 / 精炼", f"{graduation.fork_level} / {graduation.fork_refinement_level}"),
                    annotation("空幕套装", f"{graduation.core_suit_name_zh or '未保留'} ({graduation.core_suit_id or '无'})"),
                    annotation("卡带主属性", graduation.core_main_property_name_zh or graduation.core_main_property_id),
                    annotation("基准伤害", graduation.benchmark_damage),
                ),
                references=references,
            ))
        return sections
