# 用临时资源验证拒写、目录身份固定、请求结束释放及组件逐任务哈希核验。
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil

import pytest

from src.integrations.analysis_core.component_lease import AnalysisResourceLease
from src.integrations.analysis_core.nte_analysis_core import NteAnalysisCoreClient, NativeAnalysisCancelled, NativeAnalysisError

NTE_TEST_TIER = "core"


@pytest.mark.skipif(os.name != "nt", reason="Windows file binding")
def test_lease_rejects_write_replace_parent_rename_and_releases_on_failure(tmp_path):
    directory = tmp_path / "resources"
    directory.mkdir()
    resource = directory / "data.json"
    resource.write_bytes(b"original")
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b"changed!")
    with pytest.raises(ValueError, match="fixture"):
        with AnalysisResourceLease((resource,)) as lease:
            first = lease.identities((resource,))
            assert lease.identities((resource,)) == first
            (directory / "unrelated.json").write_bytes(b"sibling write remains allowed")
            stamp = resource.stat()
            try:
                os.utime(resource, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))
            except PermissionError:
                pass  # 平台拒绝元数据写入时，文件身份保持不变。
            else:
                assert lease.identities((resource,)) != first
            with pytest.raises(OSError):
                resource.write_bytes(b"changed!")
            with pytest.raises(OSError):
                os.replace(replacement, resource)
            with pytest.raises(OSError):
                directory.rename(tmp_path / "renamed")
            raise ValueError("fixture")
    resource.write_bytes(b"after release")
    directory.rename(tmp_path / "renamed")


@pytest.mark.skipif(os.name != "nt", reason="Windows component lease")
def test_component_hash_is_once_per_session_not_shared_across_threads_and_still_cancellable(tmp_path, monkeypatch):
    executable = tmp_path / "nte-analysis-core.exe"
    shutil.copyfile(Path(__file__).resolve().parents[3] / "third_party/analysis-core/bin/nte-analysis-core.exe", executable)
    client = NteAnalysisCoreClient(executable, "fixture")
    identity = type(client._component_identity)
    actual = identity.verify
    calls = []

    def verify(value, check):
        calls.append(1)
        return actual(value, check)

    monkeypatch.setattr(identity, "verify", verify)
    with client.verified_session():
        client._verify_component(None)
        with client.verified_session():
            client._verify_component(None)
        assert len(calls) == 1
        assert client.resource_identities((executable,)) is not None
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(client._verify_component, None).result()
        assert len(calls) == 2
        client.cancelled = lambda: True
        with pytest.raises(NativeAnalysisCancelled):
            client._verify_component(None)
        client.cancelled = None
    assert client.resource_identities((executable,)) is None
    with client.verified_session():
        assert len(calls) == 3
    stamp = executable.stat()
    raw = executable.read_bytes()
    executable.write_bytes(raw[:-1] + bytes((raw[-1] ^ 1,)))
    os.utime(executable, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(NativeAnalysisError, match="哈希"):
        with client.verified_session():
            pytest.fail("changed executable accepted")
    executable.write_bytes(raw)
    with client.verified_session():
        client._verify_component(None)
