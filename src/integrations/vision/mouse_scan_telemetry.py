# 保存账号内、最小化隐私字段的鼠标扫描实机验收诊断。
"""Account-local, privacy-minimal diagnostics for real mouse scan validation."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class MouseScanPageMetric:
    page: int
    item_range: tuple[int, int]
    captured: int
    capture_elapsed_ms: float
    wheel_amounts: tuple[int, ...] = ()
    scroll_elapsed_ms: float = 0.0
    overlap_row: int | None = None
    row_offset_px: int = 0
    reached_bottom: bool = False
    occupied_slots: int | None = None
    planned_rows: int = 0
    planned_slots: int = 0


@dataclass
class MouseScanTelemetry:
    """One successful run; excludes paths, HWND, OCR text and equipment data."""

    expected: int
    width: int
    height: int
    preflight_checked: int
    preflight_matched: int
    preflight_minimum_warm_fraction: float
    pages: list[MouseScanPageMetric] = field(default_factory=list)

    def append_page(self, metric: MouseScanPageMetric) -> None:
        self.pages.append(metric)

    def write(
        self,
        output_dir: str | os.PathLike[str],
        *,
        status: str,
        captured: int,
        elapsed_ms: float,
        failure_type: str | None = None,
    ) -> Path:
        payload = {
            "schema": "mouse-visual-scan-report-v1",
            "status": str(status),
            "resolution": {"width": int(self.width), "height": int(self.height)},
            "inventory": {"expected": int(self.expected), "captured": int(captured)},
            "preflight": {
                "checked": int(self.preflight_checked),
                "matched": int(self.preflight_matched),
                "minimum_warm_fraction": round(float(self.preflight_minimum_warm_fraction), 6),
            },
            "elapsed_ms": round(float(elapsed_ms), 3),
            "wheel_commands": sum(len(page.wheel_amounts) for page in self.pages),
            "pages": [self._page_payload(page) for page in self.pages],
        }
        if failure_type:
            payload["failure_type"] = str(failure_type)
        target = Path(output_dir) / "mouse_scan_last_report.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
        return target

    def write_complete(self, output_dir: str | os.PathLike[str], *, captured: int, elapsed_ms: float) -> Path:
        return self.write(
            output_dir,
            status="complete",
            captured=captured,
            elapsed_ms=elapsed_ms,
        )

    @staticmethod
    def _page_payload(metric: MouseScanPageMetric) -> dict[str, object]:
        payload = asdict(metric)
        payload["item_range"] = list(metric.item_range)
        payload["wheel_amounts"] = list(metric.wheel_amounts)
        payload["capture_elapsed_ms"] = round(float(metric.capture_elapsed_ms), 3)
        payload["scroll_elapsed_ms"] = round(float(metric.scroll_elapsed_ms), 3)
        return payload
