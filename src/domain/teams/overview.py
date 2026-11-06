# 队伍简报只表达正式来源、冻结成员和缺口，不再聚合个人装备毕业率。
from dataclasses import dataclass
from enum import StrEnum


class SourceMode(StrEnum):
    CURRENT = "current"
    SLOTS = "slots"
    GAME = "game"


SOURCE_LABELS = {SourceMode.CURRENT: "当前计算配装", SourceMode.SLOTS: "指定计算槽位", SourceMode.GAME: "当前游戏配装"}


@dataclass(frozen=True)
class TeamSourceConfig:
    team_id: str
    mode: SourceMode
    revision: int
    bindings: tuple[tuple[int, int], ...] = ()

    def validated(self):
        mode = SourceMode(self.mode)
        if not self.team_id or type(self.revision) is not int or self.revision < 1:
            raise ValueError("队伍来源身份无效")
        bindings = tuple(sorted(self.bindings))
        if (len({key for key, _ in bindings}) != len(bindings)
                or any(type(value) is not int or value <= 0 for pair in bindings for value in pair)
                or mode != SourceMode.SLOTS and bindings):
            raise ValueError("成员槽位绑定无效")
        return TeamSourceConfig(self.team_id, mode, self.revision, bindings)


@dataclass(frozen=True)
class MemberOverview:
    character_id: int
    name: str
    items: tuple[dict, ...] = ()
    slot_id: int | None = None
    plan_id: int | None = None
    snapshot_id: int | None = None
    fingerprint: str = ""
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class TeamOverview:
    team: object
    source: TeamSourceConfig
    members: tuple[MemberOverview, ...]
    issues: tuple[str, ...] = ()
    static_release: object = None
    latest_enhancement: object = None

    @property
    def ready_count(self):
        return sum(not member.issues for member in self.members)
