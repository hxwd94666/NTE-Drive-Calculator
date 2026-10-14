# 验证公开产物大小入口的文件、目录与缺失边界，不导入或执行打包器。

import pytest
from tools import build_cli


@pytest.mark.parametrize("mode,expected", [("onefile", 4096), ("onedir", 5120)])
def test_artifact_size_reports_exact_logical_bytes(tmp_path, mode, expected):
    binary = tmp_path / "synthetic.exe"
    binary.write_bytes(b"x" * 4096)
    output = binary
    if mode == "onedir":
        output = tmp_path / "bundle"
        output.mkdir()
        (output / "synthetic.exe").write_bytes(binary.read_bytes())
        nested = output / "data"
        nested.mkdir()
        (nested / "resource").write_bytes(b"y" * 1024)
    assert build_cli.artifact_size_bytes(output) == expected


def test_missing_artifact_is_not_reported_as_zero(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_cli.artifact_size_bytes(tmp_path / "missing.exe")
