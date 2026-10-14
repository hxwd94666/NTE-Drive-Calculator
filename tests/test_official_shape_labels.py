# 验证正式形状别名身份及标签入口在新进程内不加载求解器或数据库。
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SHAPES_OWNER = _PROJECT_ROOT / "src" / "domain" / "equipment" / "shapes.py"
_EXPECTED_LABELS = {
    "EquipmentGeometry_Hen2": "H_2",
    "EquipmentGeometry_Hen3": "H_3",
    "EquipmentGeometry_Hen4": "H_4",
    "EquipmentGeometry_Shu2": "V_2",
    "EquipmentGeometry_Shu3": "V_3",
    "EquipmentGeometry_Shu4": "V_4",
    "EquipmentGeometry_Z3": "Trap_4_H",
    "EquipmentGeometry_Z4": "Trap_4_V",
    "EquipmentGeometry_ZhiJiao1": "L_3_BL",
    "EquipmentGeometry_ZhiJiao2": "L_3_TL",
    "EquipmentGeometry_ZhiJiao3": "L_3_TR",
    "EquipmentGeometry_ZhiJiao4": "L_3_BR",
}


def test_formal_shape_aliases_import_without_solver_or_database():
    # 子进程只绑定本测试所属的真实 src；不借用当前 pytest 的导入缓存或 F 盘临时路径。
    code = (
        "import json, pathlib, sys; "
        f"sys.path.insert(0, {str(_PROJECT_ROOT)!r}); "
        "from src.domain.equipment import shapes; "
        "blocked = ('src.solver', 'src.storage', 'pydantic', 'PySide6', "
        "'src.services.equipment.blueprints', 'src.services.blueprint_service'); "
        "print(json.dumps({'owner': str(pathlib.Path(shapes.__file__).resolve()), "
        "'labels': shapes.OFFICIAL_SHAPE_LABELS, "
        "'heavy_loaded': sorted(name for name in sys.modules "
        "if any(name == prefix or name.startswith(prefix + '.') for prefix in blocked))}))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-X", "utf8", "-c", code],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["owner"]) == _SHAPES_OWNER.resolve()
    assert payload["labels"] == _EXPECTED_LABELS
    assert payload["heavy_loaded"] == []
