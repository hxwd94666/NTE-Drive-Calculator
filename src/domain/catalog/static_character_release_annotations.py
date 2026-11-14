# 定义角色发行注释的稳定领域记录。
"""已审阅角色发行注解，仅补充正式导出缺项。

正式角色字段默认优先；经官方版本公告明确确认的开池日期可纠正提前填入的展示时间。
社区补充保留审阅回退来源，不标作正式发行事实。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CharacterReleaseSeed:
    quality: str
    acquisition_type: str
    release_date: str
    quality_evidence_keys: tuple[str, ...]
    acquisition_evidence_keys: tuple[str, ...]
    release_evidence_keys: tuple[str, ...]
    official_release_override: bool = False


RELEASE_EVIDENCE: dict[str, tuple[str, str]] = {
    "official_1_4_release_2026_09_24": (
        "official", "https://yh.wanmei.com/news/gamebroad/20260923/264278.html",
    ),
    "official_1_4_second_release_2026_10_15": (
        "official", "https://yh.wanmei.com/news/gamebroad/20261014/264537.html",
    ),
    "official_1_5_release_2026_11_05": (
        "official", "https://yh.wanmei.com/news/gamebroad/20261104/264869.html",
    ),
    "community_character_archive_2026_08_16": (
        "reviewed_fallback",
        "https://wiki.mysqil.com/characters/",
    ),
    "mainland_launch_2026_04_23": (
        "official",
        "https://www.taptap.cn/app/714119",
    ),
    "standard_six_selector_2026_04_23": (
        "reviewed_fallback",
        "https://news.17173.com/content/04232026/121358842.shtml",
    ),
    "official_taptap_banner_history_2026_08_13": (
        "official",
        "https://www.taptap.cn/game-event/11406",
    ),
    "playstation_launch_welfare_2026_04_30": (
        "official",
        "https://blog.zh-hant.playstation.com/?p=28832",
    ),
}

_LAUNCH = "2026-04-23"
_QUALITY = "community_character_archive_2026_08_16"
_LAUNCH_EVIDENCE = "mainland_launch_2026_04_23"
_STANDARD = "standard_six_selector_2026_04_23"
_BANNER = "official_taptap_banner_history_2026_08_13"
_FREE = "playstation_launch_welfare_2026_04_30"


def _seed(
    quality: str,
    acquisition_type: str,
    release_date: str,
    acquisition_evidence: tuple[str, ...],
    release_evidence: tuple[str, ...],
) -> CharacterReleaseSeed:
    return CharacterReleaseSeed(
        quality=quality,
        acquisition_type=acquisition_type,
        release_date=release_date,
        quality_evidence_keys=(_QUALITY,),
        acquisition_evidence_keys=acquisition_evidence,
        release_evidence_keys=release_evidence,
    )


CHARACTER_RELEASE_SEEDS: dict[int, CharacterReleaseSeed] = {
    **{
        character_id: _seed(
            "A", "permanent", _LAUNCH, (_QUALITY,), (_LAUNCH_EVIDENCE,)
        )
        for character_id in (1008, 1019, 1020, 1021, 1033, 1070)
    },
    **{
        character_id: _seed(
            "S", "permanent", _LAUNCH, (_STANDARD,), (_LAUNCH_EVIDENCE,)
        )
        for character_id in (1003, 1023, 1025, 1039, 1054, 1055)
    },
    **{
        character_id: _seed(
            "S", "free", _LAUNCH, (_FREE,), (_LAUNCH_EVIDENCE,)
        )
        for character_id in (1046, 1051, 1073)
    },
    **{
        character_id: _seed("S", "limited", release_date, (_BANNER,), (_BANNER,))
        for character_id, release_date in {
            1010: _LAUNCH,
            1052: "2026-05-07",
            1004: "2026-05-28",
            1071: "2026-06-18",
            1076: "2026-07-02",
            1075: "2026-07-23",
            1036: "2026-08-13",
            1072: "2026-09-03",
        }.items()
    },
}

# 明音凛的正式展示字段提前填入六月，不等于开池；只用官方更新公告覆盖该发行注解。
for _character_id, _date, _evidence in (
    (1042, "2026-09-24", "official_1_4_release_2026_09_24"),
    (1057, "2026-10-15", "official_1_4_second_release_2026_10_15"),
    (1090, "2026-11-05", "official_1_5_release_2026_11_05"),
):
    CHARACTER_RELEASE_SEEDS[_character_id] = CharacterReleaseSeed(
        "S", "limited", _date, (_evidence,), (_evidence,), (_evidence,), True)
