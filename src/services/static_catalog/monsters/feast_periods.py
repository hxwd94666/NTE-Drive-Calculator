# 游戏资料库争锋赏宴活动期与挑战的 Qt 无关投影。
"""将活动期证据与挑战投影归为同一流程，正式战斗数值仍来自只读静态查询。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

from src.services.static_catalog.monsters.display import NAME_UNAVAILABLE
from src.services.static_catalog.monsters.feast import (
    apply_feast_options,
    build_feast_setup,
    selected_feast_options,
)
from src.services.static_catalog.monsters.models import (
    CatalogDetail,
    CatalogRelation,
    CatalogSection,
    FeastPeriod,
    FeastSetup,
)


@dataclass(frozen=True)
class _FeastPeriodDefinition:
    period_id: str
    display_label: str
    starts_at_mainland: str
    ends_at_mainland: str
    schedule_label: str
    challenge_ids: tuple[str, ...]
    evidence_label: str
    evidence_url: str


_PERIODS = (
    _FeastPeriodDefinition(
        period_id="cn_1_3_20260813",
        display_label="1.3 期",
        starts_at_mainland="2026-08-13T11:00:00",
        ends_at_mainland="2026-09-24T05:59:00",
        schedule_label="2026-08-13 版本更新后—2026-09-24 05:59",
        challenge_ids=(
            "DiyBossStage1", "DiyBossStage2", "DiyBossStage3",
            "DiyBossStage4", "DiyBossStage5", "DiyBossStage6",
            "DiyBossStage7", "DiyBossStage8",
        ),
        evidence_label="《异环》1.3 版本「雾中朔望星回」更新公告",
        evidence_url="https://yh.wanmei.com/news/gamebroad/20260812/263538.html",
    ),
    _FeastPeriodDefinition(
        period_id="cn_1_1_20260612",
        display_label="1.1 往期",
        starts_at_mainland="2026-06-12T10:00:00",
        ends_at_mainland="2026-07-02T05:59:00",
        schedule_label="2026-06-12 10:00—2026-07-02 05:59",
        challenge_ids=(
            "DiyBossStage1", "DiyBossStage111", "DiyBossStage3",
            "DiyBossStage2", "DiyBossStage5", "DiyBossStage6",
            "DiyBossStage4",
        ),
        evidence_label="《异环》1.1 版本「游梦洄廊」更新公告",
        evidence_url="https://yh.wanmei.com/m/news/gamebroad/20260527/262372.html",
    ),
)

_HISTORICAL_BOSS_NAMES = {
    "DiyBossStage111": "随心泥",
}


def feast_periods(mainland_now: datetime) -> tuple[FeastPeriod, ...]:
    """当前与未来排期优先，已结束期次按新到旧排列；不猜测缺失的挑战条件。"""

    periods = tuple(
        FeastPeriod(
            period_id=row.period_id,
            display_label=row.display_label,
            release_state=_release_state(row, mainland_now),
            schedule_label=row.schedule_label,
            challenge_ids=row.challenge_ids,
            evidence_label=row.evidence_label,
            evidence_url=row.evidence_url,
        )
        for row in _PERIODS
    )
    order = {"current": 0, "next": 1, "scheduled": 2, "historical": 3}
    return tuple(sorted(
        periods,
        key=lambda row: (
            order.get(row.release_state, 9),
            -_timestamp(row.period_id),
        ),
    ))


def feast_period(period_id: str, mainland_now: datetime) -> FeastPeriod | None:
    return next(
        (row for row in feast_periods(mainland_now) if row.period_id == period_id),
        None,
    )


def historical_boss_name(stage_id: str) -> str:
    return _HISTORICAL_BOSS_NAMES.get(str(stage_id), "")


def _release_state(row: _FeastPeriodDefinition, mainland_now: datetime) -> str:
    start = datetime.fromisoformat(row.starts_at_mainland)
    end = datetime.fromisoformat(row.ends_at_mainland)
    if start <= mainland_now <= end:
        return "current"
    return "historical" if end < mainland_now else "scheduled"


def _timestamp(period_id: str) -> int:
    tail = period_id.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else 0


DERIVED = "project_annotation"


def _key(kind: str, *parts: object) -> str:
    encoded = [quote(str(part), safe="") for part in parts]
    return "|".join((kind, *encoded))


def _text(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    return text if text and "\ufffd" not in text else fallback


class StaticCatalogFeastPeriodMixin:
    """项目排期注解只决定期次成员与顺序，正式挑战数值仍读取同一静态来源。"""

    def list_feast_periods(self) -> tuple[FeastPeriod, ...]:
        return feast_periods(self._mainland_now)

    def get_feast_setup(
        self, period_id: str, stage_id: str,
    ) -> FeastSetup | None:
        period = feast_period(period_id, self._mainland_now)
        if period is None or stage_id not in period.challenge_ids:
            return None
        row = self._queries.feast_setup(stage_id)
        condition_note = ""
        if row is None:
            row = self._queries.historical_feast_setup(stage_id)
            if row is None:
                return None
            boss_name = historical_boss_name(stage_id)
            for difficulty in row.get("difficulties", ()):
                difficulty["boss_name_zh"] = boss_name
            condition_note = "往期挑战条件成员关系未保留；不套用当前期条件。"
        return build_feast_setup(
            row,
            period_id=period.period_id,
            period_label=period.display_label,
            period_state=period.release_state,
            schedule_label=period.schedule_label,
            challenge_ordinal=period.challenge_ids.index(stage_id) + 1,
            condition_note=condition_note,
            project_option=self._gameplay.feast_option,
        )

    def get_feast_detail(
        self,
        period_id: str,
        stage_id: str,
        difficulty_id: int,
        *,
        selected_option_ids: tuple[str, ...] = (),
    ) -> CatalogDetail | None:
        period = feast_period(period_id, self._mainland_now)
        if period is None or stage_id not in period.challenge_ids:
            return None
        row = self._queries.feast_encounter(stage_id, difficulty_id)
        if row is None:
            row = self._queries.historical_feast_encounter(
                stage_id, difficulty_id
            )
            if row is None:
                return None
            row["boss_name_zh"] = historical_boss_name(stage_id)
        return self._build_feast_detail(
            row,
            selected_option_ids=selected_option_ids,
            period=period,
            challenge_ordinal=period.challenge_ids.index(stage_id) + 1,
        )

    def _feast_detail(
        self,
        stage_id: str,
        difficulty_id: int,
        *,
        selected_option_ids: tuple[str, ...] = (),
    ) -> CatalogDetail | None:
        row = self._queries.feast_encounter(stage_id, difficulty_id)
        if row is None:
            return None
        return self._build_feast_detail(
            row,
            selected_option_ids=selected_option_ids,
        )

    def _build_feast_detail(
        self,
        row: dict[str, Any],
        *,
        selected_option_ids: tuple[str, ...],
        period: FeastPeriod | None = None,
        challenge_ordinal: int = 0,
    ) -> CatalogDetail:
        stage_id = str(row.get("stage_id") or "")
        difficulty_id = int(row.get("difficulty_id") or 0)
        selected = selected_feast_options(row.get("options", []), selected_option_ids)
        profile = apply_feast_options(row.get("profile"), selected)
        difficulty_name = _text(row.get("difficulty_name_zh"), NAME_UNAVAILABLE)
        entry_key = (
            _key("feast_period", period.period_id, stage_id, difficulty_id)
            if period is not None
            else _key("feast", stage_id, difficulty_id)
        )
        subtitle = (
            f"争锋赏宴 · {period.display_label} · {difficulty_name}"
            if period is not None
            else f"争锋赏宴 · {difficulty_name}"
        )
        entry = self._entry(
            entry_key, domain="encounter", play_mode="feast",
            title_value=row.get("name_zh"), fallback=NAME_UNAVAILABLE,
            subtitle=subtitle, primary_id=stage_id,
            secondary_id=str(row.get("boss_monster_id") or ""),
            resource_path=str(row.get("boss_icon_path") or ""),
        )
        sections = []
        if period is not None:
            sections.append(CatalogSection("活动期", (
                self._value("活动期", period.display_label, DERIVED),
                self._value("挑战顺序", challenge_ordinal, DERIVED),
                self._value("大陆服排期", period.schedule_label, DERIVED),
                self._value(
                    "排期证据", period.evidence_label, DERIVED,
                    note=period.evidence_url,
                ),
            ), (
                "活动期及成员顺序是公开排期与同期实机证据的项目注解；"
                "敌方数值仍来自发行静态资源。"
            )))
        sections.append(CatalogSection("正式玩法配置", (
            self._value("挑战对象 ID", stage_id, copyable=True),
            self._localized_value("挑战名", row.get("name_zh")),
            self._value("Boss 模板 ID", row.get("boss_monster_id"), copyable=True),
            self._localized_value("Boss 中文名", row.get("boss_name_zh")),
            self._localized_value("难度", row.get("difficulty_name_zh")),
            self._value("怪物等级", row.get("monster_level")),
            self._value("基础分", row.get("base_score")),
            self._value("得分倍率", row.get("score_rate")),
            self._value("特殊高难", bool(row.get("special_high_difficulty"))),
        )))
        sections.append(self._combat_profile_section(
            profile, level=row.get("monster_level"), title="当前选择画像"
        ))
        if selected:
            option_values = []
            for option in selected:
                category = _text(
                    option.get("category_name_zh"), NAME_UNAVAILABLE
                )
                option_values.append(self._gameplay.feast_option(category, option))
            sections.append(CatalogSection(
                "已选挑战条件",
                tuple(option_values),
                (
                    "已选攻击提升；当前正式画像没有敌方攻击数值。"
                    if any(
                        option.get("effect_kind") == "attack_up"
                        for option in selected
                    )
                    else ""
                ),
            ))
        relations = tuple(dict.fromkeys(
            CatalogRelation(
                f"查看 Boss 模板：{candidate['static_table']}",
                _key(
                    "profile_monster",
                    candidate["static_table"],
                    candidate["monster_id"],
                ),
                "exact_official_template_id",
            )
            for candidate in self._queries.template_profile_candidates(
                row["boss_monster_id"]
            )
        ))
        sections.append(self._source_section(row.get("source")))
        return CatalogDetail(entry, tuple(sections), relations)
