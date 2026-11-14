# 队伍匹配交互专项只使用合成历史和可取消查询，不触碰个人账号或游戏连接。
from concurrent.futures import CancelledError
from time import monotonic, sleep

from tests.support.auto_sync_ui_fixture import application
from src.domain.teams.report_matching import MatchPage, ReportDiscovery, classify_evidence, discover_candidates


def wait_for(predicate, timeout=5):
    deadline = monotonic() + timeout
    while not predicate() and monotonic() < deadline:
        application().processEvents()
        # 原生组件核验在 Python worker 中；用释放 GIL 的等待，避免 qWait 循环饿死后台读取。
        sleep(.005)
    assert predicate(), "匹配 owner 未在本例时限内完成"


def drain_dialog(dialog):
    dialog.reject()
    wait_for(lambda: not dialog.is_running())
    application().processEvents()


class MatchingFixture:
    def __init__(self, entries=(), teams=()):
        self.entries, self.teams = tuple(entries), tuple(teams)
        self.calls = []
        self.current = True
        self.different = set()

    def discover(self, *, offset=0, cancelled=lambda: False):
        if cancelled() or not self.current:
            raise CancelledError
        return ReportDiscovery(self.teams, discover_candidates(self.entries))

    def candidate(self, scope, *, cancelled=lambda: False):
        from src.domain.teams.report_matching import ReportCandidate
        return ReportCandidate(next(entry for entry in self.entries if entry.battle_record_id == scope.record_id), scope)

    def match_page(self, team, candidates, *, offset=0, limit=20, cancelled=lambda: False, publish=None):
        results = []
        for candidate in candidates[offset:offset + limit]:
            if cancelled() or not self.current:
                raise CancelledError
            self.calls.append((team.team_id, candidate.range))
            ids = tuple(sorted(team.character_ids)) if team.team_id not in self.different else (901, 902, 903, 904)
            result = classify_evidence(team, candidate, dict(ready=team.team_id not in self.different,
                team_character_ids=ids, source_input_digest="a" * 64, reason=None))
            results.append(result)
            if publish:
                publish(result)
        return MatchPage(tuple(results), offset + len(results), len(candidates))
