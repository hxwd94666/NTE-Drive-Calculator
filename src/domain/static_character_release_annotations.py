# 定义角色发行注释的稳定领域记录。
"""Reviewed character release annotations used as v30 build inputs.

Official ``character.mainland_show_time`` remains authoritative for release dates.
These records only fill fields absent from the official export and retain their
reviewed-fallback provenance instead of presenting community evidence as official.
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


RELEASE_EVIDENCE: dict[str, tuple[str, str]] = {
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
