# 加权配装页面兼容入口。
"""Thin composition entry point for weighted allocation."""

from __future__ import annotations

from . import weighted_preferences as _preferences
from . import weighted_result_view as _result_view
from . import weighted_workflow as _workflow
from .weighted_shell import build_weighted_allocation_page, refresh_weighted_allocation_page

for _module in (_preferences, _workflow, _result_view):
    for _name, _value in vars(_module).items():
        if callable(_value) and (not _name.startswith("__")):
            globals().setdefault(_name, _value)
