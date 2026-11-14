# 验证卡带与驱动共用副词条黑名单，硬过滤不被预分配、同级或暴击回退绕过。
import pytest
from types import SimpleNamespace

from src.models.equipment import Tape
from src.optimizer.strategies.role_priority_strategy import RolePriorityStrategy
from src.optimizer.scoring import ScoringEngine
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
from src.services.allocation.results.postprocess_facts import verify_postprocess_hard_constraints


def tapes():
    return [Tape(uid=uid, quality="Gold", area=15, set_name="Set",
                 main_stats="攻击力%", sub_stats=stats, role_scores={"A": score, "B": score})
            for uid, stats, score in [("blocked", {"环合强度": 60}, 1000),
                                      ("allowed", {"暴击率%": 10}, 10)]]


def strategy():
    return RolePriorityStrategy({"A": {"default_set": "Set"}, "B": {"default_set": "Set"}},
                                {"Set": {"shapes": []}}, {})


@pytest.mark.parametrize("method", ["_pre_allocate_tapes", "_pre_allocate_tapes_optimal"])
@pytest.mark.parametrize("zero", [False, True])
def test_card_preallocation_obeys_blacklist_even_without_substat_preferences(method, zero):
    chosen = getattr(strategy(), method)(["A"], {"A": "Set"}, {"A": tapes()},
                                        {"A": {"blacklist": ["环合强度"], "blacklist_zero_weight": zero}})
    assert chosen["A"].uid == ("blocked" if zero else "allowed")


def test_card_blacklist_remains_role_scoped_in_equal_priority_group():
    cards = tapes()
    chosen = strategy()._pre_allocate_tapes_for_groups([["A", "B"]], {"A": "Set", "B": "Set"},
        {"A": cards, "B": cards}, {"A": {"blacklist": ["环合强度"]}})
    assert chosen["A"].uid == "allowed"
    assert chosen["B"].uid == "blocked"


def test_critical_cap_fallback_rejects_a_previously_reserved_blacklisted_card():
    cards = tapes()
    result = strategy()._tape_candidates_for_capped_role("A", {"A": cards[0]},
        {"A": cards}, set(), {"A": "Set"}, {"blacklist": ["环合强度"]})
    assert [card.uid for card in result] == ["allowed"]


def test_card_zero_weight_ignores_only_blacklisted_substat_not_matching_main_stat():
    engine = ScoringEngine(roles_db={})
    card = tapes()[0]
    card.sub_stats = {"攻击力%": 12.5, "暴击率%": 10}
    weights = {"攻击力%": 1, "暴击率%": 1}
    value = engine.calculate_cartridge_score(card, weights, 1, {"攻击力%": 1},
                                            zero_weight_stats=("攻击力%",))
    assert value == 150  # 主词条50分仍保留，暴击副词条100分；攻击副词条为0。
    # 同一归一化值也必须区分屏蔽集合；整数/浮点最大值不能令批量预热后的缓存失配。
    assert engine.calculate_cartridge_score(card, weights, 1.0, {"攻击力%": 1}) == 250
    assert engine.calculate_cartridge_score(card, weights, 1.0, {"攻击力%": 1},
                                            zero_weight_stats=("攻击力%",)) == 150


def test_all_cards_blacklisted_returns_missing_card_instead_of_silent_relaxation():
    chosen = strategy()._pre_allocate_tapes(["A"], {"A": "Set"}, {"A": tapes()[:1]},
                                           {"A": {"blacklist": ["环合强度"]}})
    assert chosen["A"] is None


def test_blacklist_does_not_filter_matching_card_main_stat():
    chosen = strategy()._pre_allocate_tapes(["A"], {"A": "Set"}, {"A": tapes()},
                                           {"A": {"blacklist": ["攻击力%"]}})
    assert chosen["A"].uid == "blocked"


def test_postprocess_rejects_blacklisted_card_before_accepting_numeric_proof():
    request = SimpleNamespace(stat_priority_configs={"A": {"blacklist": ["环合强度"]}})
    scorer = SimpleNamespace(stat_catalog=SimpleNamespace(normalize_stat_name=lambda raw, **kw: raw))
    plan = {"assigned_tape": tapes()[0], "assigned_set_drives": [], "assigned_extra_drives": []}
    with pytest.raises(NativeAnalysisError, match="副词条黑名单"):
        verify_postprocess_hard_constraints(request, scorer, {}, {"A": plan}, "A",
                                           base_scores={}, native_verified=True)
