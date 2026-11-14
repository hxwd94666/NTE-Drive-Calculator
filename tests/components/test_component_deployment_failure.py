# 验证自动部署故障跨重启保留、损坏记录拒绝自动部署及显式解除。
import json

from src.integrations.components.component_deployment_failure import ComponentDeploymentFailure


def test_failure_record_survives_reopen_and_explicit_clear(tmp_path):
    store = ComponentDeploymentFailure(tmp_path)
    assert store.load() == ""
    store.save("Windows 已阻止组件写入")
    reopened = ComponentDeploymentFailure(tmp_path)
    assert reopened.load() == "Windows 已阻止组件写入"
    reopened.clear()
    assert store.load() == ""


def test_invalid_failure_record_blocks_until_explicit_clear(tmp_path):
    store = ComponentDeploymentFailure(tmp_path)
    for value in ("broken", json.dumps({"version": 1, "detail": []})):
        store.path.write_text(value, encoding="utf-8")
        assert "已暂停自动重试" in store.load()
    store.clear()
    assert store.load() == ""


def test_failed_atomic_save_retains_previous_fault(tmp_path, monkeypatch):
    store = ComponentDeploymentFailure(tmp_path)
    store.save("previous")
    def fail(*args):
        raise OSError("write denied")
    monkeypatch.setattr("src.integrations.components.component_deployment_failure.os.replace", fail)
    import pytest
    with pytest.raises(OSError):
        store.save("new")
    assert store.load() == "previous"
    assert list(tmp_path.glob("*.tmp")) == []
