# 冻结多队配装来源并关联增强历史，发布前复核来源；简报不运行毕业率或理论搜索。
from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from src.domain.teams.overview import TeamSourceConfig
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
from src.services.teams.source_inputs import freeze_team_sources
from src.services.teams.enhancement_history import associate_enhancement, current_component_provenance, latest_enhancement
from src.storage.sqlite.user_data_dao import UserDataDao


class TeamOverviewService:
    def __init__(self, *, account_id, generation, database_path, static_database_path, context_is_current):
        self.account_id, self.generation = account_id, generation
        self.database_path, self.static_database_path = Path(database_path), static_database_path
        self._current = context_is_current

    def checkpoint(self, cancelled=lambda: False):
        if cancelled() or not self._current():
            raise CancelledError("队伍简报已取消或账号代次已变化")

    @contextmanager
    def _database(self, *, cancelled=lambda: False, write=False):
        self.checkpoint(cancelled)
        if not self.database_path.is_file():
            raise ValueError("当前账号库不存在")
        with UserDataDao(self.database_path) as dao:
            with dao.write_transaction() if write else dao.read_consistent_state():
                if dao.profile()["account_id"] != self.account_id:
                    raise CancelledError("队伍简报账号身份不一致")
                yield dao
                self.checkpoint(cancelled)

    def source_choices(self, team, *, cancelled=lambda: False):
        with self._database(cancelled=cancelled) as dao:
            if team not in dao.list_teams():
                raise ValueError("队伍已变化，请重新打开")
            return dao.get_team_source(team.team_id), tuple(row for row in dao.list_visible_loadout_slots()
                                                          if row["character_id"] in team.character_ids)

    def save_source(self, team, expected, *, mode, bindings=(), cancelled=lambda: False):
        with self._database(cancelled=cancelled, write=True) as dao:
            return dao.save_team_source(team, expected, mode=mode, bindings=bindings,
                                        checkpoint=lambda: self.checkpoint(cancelled))

    def preview_source(self, team, expected, *, mode, bindings=(), cancelled=lambda: False):
        """草稿只在一致读内解析；不保存来源、不启动同步、理论求解或分析组件。"""
        draft = TeamSourceConfig(team.team_id, mode, expected.revision, tuple(bindings)).validated()
        if not {key for key, _ in draft.bindings} <= set(team.character_ids):
            raise ValueError("草稿含队伍外成员")
        if self.static_database_path is None:
            raise ValueError("配装预览缺少明确静态资料")
        with static_catalog_read_unit(self.static_database_path) as unit:
            with self._database(cancelled=cancelled) as dao:
                if team not in dao.list_teams() or dao.get_team_source(team.team_id) != expected:
                    raise ValueError("队伍或来源已变化，请重新打开队伍配装")
                value, = freeze_team_sources(dao, unit.dao, (team,),
                    checkpoint=lambda: self.checkpoint(cancelled), source_overrides={team.team_id: draft})
                return value

    def load(self, teams=None, *, cancelled=lambda: False, publish=None):
        self.checkpoint(cancelled)
        if self.static_database_path is None:
            raise ValueError("简报缺少明确静态资料身份；已保存队伍仍保留")
        check = lambda: self.checkpoint(cancelled)
        with static_catalog_read_unit(self.static_database_path) as unit:
            with self._database(cancelled=cancelled) as dao:
                current = dao.list_teams()
                selected = tuple(teams) if teams is not None else current
                if not set(selected) <= set(current):
                    raise ValueError("队伍已变化，请刷新")
                frozen = freeze_team_sources(dao, unit.dao, selected, checkpoint=check)
                has_history = any(dao.list_team_enhancements(item.team.team_id, limit=1) for item in frozen)
            component = current_component_provenance(unit.release.database_path, cancelled) if has_history else None
            results = []
            for overview in frozen:
                check()
                result = replace(overview, static_release=unit.release)
                # 每队完成即复核并增量发布；无关账号写入不使冻结来源失效。
                with self._database(cancelled=cancelled) as dao:
                    if overview.team not in dao.list_teams():
                        raise ValueError("队伍在读取期间变化，请刷新")
                    current_source, = freeze_team_sources(dao, unit.dao, (overview.team,), checkpoint=check)
                    if current_source != overview:
                        raise ValueError("配装来源在读取期间变化，请刷新")
                    result = replace(result, latest_enhancement=latest_enhancement(dao, result, release=unit.release, component=component))
                results.append(result)
                check()
                if publish is not None:
                    publish(result)
            return tuple(results)

    def enhancement_history(self, team, *, offset=0, limit=20, cancelled=lambda: False):
        self.checkpoint(cancelled)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("增强历史分页范围无效")
        if self.static_database_path is None:
            raise ValueError("历史来源核对缺少明确静态身份")
        with static_catalog_read_unit(self.static_database_path) as unit:
            component = current_component_provenance(unit.release.database_path, cancelled)
            with self._database(cancelled=cancelled) as dao:
                if team not in dao.list_teams():
                    raise ValueError("队伍已变化，请重新打开历史")
                overview, = freeze_team_sources(dao, unit.dao, (team,), checkpoint=lambda: self.checkpoint(cancelled))
                rows = dao.list_team_enhancements(team.team_id, offset=offset, limit=limit+1)
                result = []
                for saved in rows[:limit]:
                    self.checkpoint(cancelled)
                    item = associate_enhancement(dao, saved, release=unit.release, component=component, overview=overview)
                    partners = tuple(associate_enhancement(dao, partner, release=unit.release, component=component)
                                     for partner in dao.team_run_results(saved.run_id) if partner.team_id != team.team_id)
                    result.append(replace(item, joint_results=partners))
                return tuple(result), offset+limit if len(rows) > limit else None
