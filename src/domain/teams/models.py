# 队伍以稳定身份保存零到四个正式角色，未满员草稿不伪装成可优化阵容。
from dataclasses import dataclass


@dataclass(frozen=True)
class TeamDraft:
    name: str
    character_ids: tuple[int, ...] = ()

    def validated(self):
        name = self.name.strip()
        if not name or len(name) > 80:
            raise ValueError("请输入 1–80 个字符的队伍名称")
        ids = tuple(self.character_ids)
        if len(ids) > 4 or len(set(ids)) != len(ids):
            raise ValueError("每支队伍最多选择四名不同的角色")
        if any(type(value) is not int or value <= 0 for value in ids):
            raise ValueError("队伍中存在无效角色身份")
        return TeamDraft(name, ids)


@dataclass(frozen=True)
class Team:
    team_id: str
    name: str
    character_ids: tuple[int, ...]
    revision: int


@dataclass(frozen=True)
class TeamRole:
    character_id: int
    name: str
    icon_path: str = ""
    custom: bool = False
    icon_note: str = ""
