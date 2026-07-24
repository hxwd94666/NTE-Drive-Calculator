# 角色页面兼容入口。
"""Thin composition entry point for official role feature."""

from __future__ import annotations

from . import role_calculation as _calculation
from . import role_equipment as _equipment
from . import role_growth as _growth
from . import role_shell as _shell
from . import role_weights as _weights

for _module in (_calculation, _growth, _equipment, _weights, _shell):
    for _name, _value in vars(_module).items():
        if callable(_value) and not _name.startswith("__"):
            globals().setdefault(_name, _value)

__all__ = ["_page_my_role", "_refresh_my_role", "confirm_pending_my_role_changes"]
