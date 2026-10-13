# 用真实构建入口的大小表达式核对文件与目录，不导入或执行 PyInstaller。
from pathlib import Path
import ast

import pytest
from tools import build_cli


@pytest.mark.parametrize("mode,expected", [("onefile", 4096), ("onedir", 5120)])
def test_build_output_reports_exact_logical_bytes(tmp_path, mode, expected):
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
    source = Path(__file__).resolve().parents[2] / "build_exe.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    assignment = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id in {"size_mb", "size_mib"} for target in node.targets))
    value = eval(compile(ast.Expression(assignment.value), "isolated-size-expression", "eval"),
                 {"output": output, "build_cli": build_cli})
    assert value * 1048576 == expected


def test_missing_artifact_is_not_reported_as_zero(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_cli.artifact_size_bytes(tmp_path / "missing.exe")
