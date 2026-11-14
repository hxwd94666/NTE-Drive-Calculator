# 只读发现战报并通过正式分析组件核验四人队伍，缓存逐次复核输入摘要而非数据库时间。
from collections import OrderedDict
from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import sqlite3
from pathlib import Path
from threading import Lock

from src.domain.teams.report_matching import (
    MatchPage, MatchStatus, ReportDiscovery, TeamReportMatch, classify_evidence, discover_candidates,
)
from src.integrations.analysis_core.analysis_core_release import create_bundled_analysis_client
from src.integrations.runtime.bundled_resources import bundled_config_dir
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisCancelled
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
from src.services.battle_report.history.access import list_history_entries
from src.storage.sqlite.user_data_dao import UserDataDao


class TeamReportMatchingService:
    def __init__(self, *, dependencies, context_is_current, semantics_path=None,
                 client_factory=create_bundled_analysis_client):
        self.dependencies = dependencies
        self._current = context_is_current
        self.semantics_path = Path(semantics_path or bundled_config_dir() / "gameplay_effect_semantics.json").resolve()
        self._factory = client_factory
        self._serial = Lock()
        self._cache = OrderedDict()

    def checkpoint(self, cancelled=lambda: False):
        if cancelled() or not self._current(self.dependencies):
            raise CancelledError("战报匹配已取消或账号代次已变化")

    @contextmanager
    def _database(self, cancelled):
        self.checkpoint(cancelled)
        deps = self.dependencies
        if not Path(deps.user_database_path).is_file():
            raise ValueError("当前账号数据库不存在，请重新打开账号")
        with UserDataDao(deps.user_database_path) as dao, dao.read_consistent_state():
            if dao.profile()["account_id"] != deps.account_id:
                raise CancelledError("战报匹配账号身份已变化")
            yield dao
            self.checkpoint(cancelled)

    def discover(self, *, offset=0, limit=20, cancelled=lambda: False):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("战报分页范围无效")
        with self._database(cancelled) as dao:
            teams = dao.list_teams()
            entries = list_history_entries(user_dao=dao, limit=limit + 1, offset=offset)
        return ReportDiscovery(teams, discover_candidates(entries[:limit]),
                               offset + limit if len(entries) > limit else None)

    def verify_team(self, team, *, cancelled=lambda: False):
        with self._database(cancelled) as dao:
            if team not in dao.list_teams():
                raise ValueError("队伍已变更或删除，请关闭匹配窗口并重新选择队伍")

    def candidate(self, scope, *, cancelled=lambda: False):
        """显式时段只绑定已有历史元数据，不从逐击猜分段。"""
        with self._database(cancelled) as dao:
            row = dao.load_battle_record(scope.record_id)
            if row is None:
                raise ValueError("所选战报已删除，请刷新列表")
            from src.services.battle_report.history.projection import history_entry
            from src.domain.teams.report_matching import ReportCandidate
            return ReportCandidate(history_entry(row), scope)

    def _selector(self, candidate):
        deps = self.dependencies
        return dict(account_id=deps.account_id, generation=deps.generation,
                    user_database_path=str(Path(deps.user_database_path).resolve()),
                    static_database_path=str(Path(deps.static_database_path).resolve()),
                    semantics_path=str(self.semantics_path), detail_level="hit", **candidate.range.selector_fields())

    def match_page(self, team, candidates, *, offset=0, limit=20, cancelled=lambda: False, publish=None):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("核验分页范围无效")
        self.verify_team(team, cancelled=cancelled)
        # 一个服务只派发一条原生核验；等锁时也能取消，不在 GUI 上等待。
        while not self._serial.acquire(timeout=.05):
            self.checkpoint(cancelled)
        try:
            selected = tuple(candidates[offset:offset + limit])
            results = self._match_batch(team, selected, cancelled, publish)
            self.verify_team(team, cancelled=cancelled)
            return MatchPage(results, offset + len(selected), len(candidates))
        except NativeAnalysisCancelled:
            raise CancelledError from None
        finally:
            self._serial.release()

    def _match_batch(self, team, candidates, cancelled, publish):
        results = []

        def emit(result):
            self.verify_team(team, cancelled=cancelled)
            results.append(result)
            if publish is not None:
                publish(result)

        try:
            if self.dependencies.static_database_path is None:
                raise LookupError("缺少明确的静态资料身份；历史仍可浏览")
            with static_catalog_read_unit(self.dependencies.static_database_path) as unit:
                client = self._factory(static_database_path=unit.release.database_path, cancelled=cancelled)
                if client is None or not client.supports_battle_axis_team:
                    raise LookupError("分析组件缺少正式队伍核验能力，请更新配套组件")
                if client.dataset_version != unit.release.dataset_id:
                    raise LookupError("分析组件与冻结静态资料身份不一致")
                client.timeout = min(client.timeout, 15.0)
                identity = (self.dependencies, unit.release,
                    hashlib.sha256(self.semantics_path.read_bytes()).hexdigest(),
                    client.engine_version, tuple(sorted(client.component_provenance.items())))
                formal_ids = {row["character_id"] for row in unit.dao.list_characters()}
                for candidate in candidates:
                    self.checkpoint(cancelled)
                    if not set(team.character_ids) <= formal_ids or len(team.character_ids) != 4:
                        result = TeamReportMatch(team, candidate, MatchStatus.INSUFFICIENT,
                                                 "请补齐四名正式成员；自建或缺目录身份的成员保留未知")
                    else:
                        result = self._match_one(team, candidate, client, identity, cancelled)
                    emit(result)
        except (CancelledError, NativeAnalysisCancelled):
            raise
        except (OSError, ValueError, RuntimeError, LookupError, sqlite3.Error) as error:
            # 环境故障保留候选，绝不发布零条匹配；候选错误不进入缓存。
            for candidate in candidates[len(results):]:
                emit(TeamReportMatch(team, candidate, MatchStatus.UNAVAILABLE, str(error)))
        return tuple(results)

    def _match_one(self, team, candidate, client, identity, cancelled):
        def check():
            self.checkpoint(cancelled)
        try:
            with self._database(cancelled) as dao:
                if dao.load_battle_record(candidate.range.record_id) is None:
                    return TeamReportMatch(team, candidate, MatchStatus.INSUFFICIENT, "战报已删除，请刷新列表")
            selector = self._selector(candidate)
            members = tuple(sorted(team.character_ids))
            digest = (client.load_battle_page_identity(selector, checkpoint=check)
                      if client.supports_battle_page_identity else None)
            key = identity, candidate.range, members, digest
            if digest is not None and key in self._cache:
                self._cache.move_to_end(key)
                return replace(self._cache[key], team=team, candidate=candidate)
            evidence = client.resolve_battle_axis_team(selector, list(members), checkpoint=check)
            check()
            if hashlib.sha256(self.semantics_path.read_bytes()).hexdigest() != identity[2]:
                return TeamReportMatch(team, candidate, MatchStatus.ERROR, "核验期间语义配置已变化，请重新核验")
            if digest is not None and evidence["source_input_digest"] != digest:
                return TeamReportMatch(team, candidate, MatchStatus.ERROR, "核验期间战报输入已变化，请重试")
            result = classify_evidence(team, candidate, evidence, input_identity=identity)
            if digest is not None:
                self._cache[key] = result
                while len(self._cache) > 128:
                    self._cache.popitem(last=False)
            return result
        except (CancelledError, NativeAnalysisCancelled):
            raise
        except (OSError, ValueError, RuntimeError, LookupError, sqlite3.Error) as error:
            return TeamReportMatch(team, candidate, MatchStatus.ERROR, str(error))
