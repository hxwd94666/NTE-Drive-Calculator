# 单队快捷准备只读取账号队伍修订；准备和确认边界复核，绝不保存配置或运行分配。
from pathlib import Path

from src.domain.teams.calculation import TeamCalculationError, TeamCalculationRequest


class TeamCalculationService:
    def __init__(self, team_service, *, generation):
        self._teams, self._generation = team_service, generation

    def freeze(self, team, *, draft_revision):
        request = TeamCalculationRequest(self._teams.account_id, self._generation,
            str(self._teams.database_path.resolve()), team, draft_revision)
        if len(team.character_ids) != 4 or len(set(team.character_ids)) != 4:
            raise TeamCalculationError('incomplete')
        self.verify(request)
        return request

    def verify(self, request):
        self._teams.checkpoint()
        if (request.account_id != self._teams.account_id or request.generation != self._generation
                or Path(request.database_path) != self._teams.database_path.resolve()
                or request.team not in self._teams.list_teams()):
            raise TeamCalculationError('team_changed')
        self._teams.checkpoint()
