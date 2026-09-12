# 验证原生停止诊断保留既有原因并只展示已知安全的触发事件。
from src.services.battle_capture_metadata import native_capture_end_warning


def test_source_change_trigger_is_explained_from_frozen_record():
    record = {"native_capture": {
        "reason": "source_changed", "sourceChangeTrigger": "player_state_replication",
    }}
    warning = native_capture_end_warning({"reason": "user_stop"}, record)
    assert "玩家状态复制通知" in warning
    assert "游戏采集上下文已失效" in warning


def test_old_provider_and_untrusted_trigger_keep_safe_warning():
    old = native_capture_end_warning({"reason": "source_changed"})
    assert old == native_capture_end_warning({"reason": "source_changed", "sourceChangeTrigger": "private-input"})
    assert old == native_capture_end_warning({"reason": "source_changed", "sourceChangeTrigger": {}})
    assert native_capture_end_warning({"reason": "user_stop", "sourceChangeTrigger": "local_end_play"}) == ""


def test_callback_exception_is_distinct_from_replication():
    warning = native_capture_end_warning({"reason": "source_changed", "sourceChangeTrigger": "game_thread_pulse_exception"})
    assert "游戏线程采集回调异常" in warning
