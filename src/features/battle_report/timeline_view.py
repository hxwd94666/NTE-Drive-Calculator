# 展示可缩放、可平移且能逐项交互的统一战斗时间轴。
"""Unified interactive timeline for battle input, action and hit evidence."""

from __future__ import annotations

from collections.abc import Sequence
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import QMenu, QToolTip, QWidget

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleInferredAction,
    BattleInferredInput,
    BattleTimelineDamageGroup,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    ELAPSED_TIME_MODE,
    BattleTimelineTimeMode,
    project_timeline_time_us,
)
from src.features.battle_report.timeline_range_mixin import (
    BattleTimelineRangeMixin,
)
from src.features.battle_report.timeline_layout import (
    LABEL_WIDTH,
    RIGHT_MARGIN,
    ROLE_COLORS,
    TOP,
    TimelineLane,
    TimelineLayout,
    TimelinePaintedBar,
    TimelinePaintedHit,
    TimelineSelection,
    build_timeline_layout,
    damage_group_color,
    format_time,
    nice_tick_step,
    timeline_role_index,
)
from src.features.battle_report.timeline_tooltip import build_timeline_tooltip
from src.features.battle_report.timeline_sticky_labels import (
    paint_sticky_lane_labels,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog


_HIT_PICK_TOLERANCE_US = 75_000
_BAR_PICK_PADDING_US = 75_000
_BASE_TIMELINE_PIXELS_PER_SECOND = 33.0
_MIN_TIMELINE_WIDTH = 760


class BattleUnifiedTimelineWidget(BattleTimelineRangeMixin, QWidget):
    """Render one evidence-aware input → action → damage timeline."""

    content_height_changed = Signal(int)
    horizontal_pan_requested = Signal(int)
    range_boundary_requested = Signal(str, int)
    selection_activated = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        game_ui_asset_root: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._analysis: BattleAnalysisSnapshot | None = None
        self._time_mode: BattleTimelineTimeMode = ELAPSED_TIME_MODE
        self._zoom_factor = 1.0
        self._drag_start: QPointF | None = None
        self._drag_end: QPointF | None = None
        self._drag_start_global_x: float | None = None
        self._drag_last_global_x: float | None = None
        self._dragging = False
        self._last_detail_key: str | None = None
        self._hit_heading = "正式逐击"
        self._horizontal_view_offset = 0
        self._lanes: tuple[TimelineLane, ...] = ()
        self._painted_bars: list[TimelinePaintedBar] = []
        self._painted_hits: list[TimelinePaintedHit] = []
        self._layout_cache_key: tuple[int, str, int] | None = None
        self._layout_cache: TimelineLayout | None = None
        self._asset_catalog = (
            GameUiAssetCatalog(game_ui_asset_root)
            if game_ui_asset_root is not None
            else None
        )
        self._avatar_cache: dict[int, QPixmap | None] = {}
        self.setMinimumSize(_MIN_TIMELINE_WIDTH, 300)
        self.setFixedHeight(300)
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:
        return QSize(self._required_content_width(), self.minimumHeight())

    def set_analysis(self, analysis: BattleAnalysisSnapshot | None) -> None:
        self._analysis = analysis
        self._visible_analysis_cache_key = None
        self._visible_analysis_cache = None
        self._layout_cache_key = None
        self._layout_cache = None
        self._drag_start = None
        self._drag_end = None
        self._drag_start_global_x = None
        self._drag_last_global_x = None
        self._dragging = False
        self._last_detail_key = None
        self._refresh_geometry()

    def set_time_mode(self, mode: BattleTimelineTimeMode) -> None:
        if mode not in {ELAPSED_TIME_MODE, ACTIVE_TIME_MODE}:
            raise ValueError(f"unsupported battle timeline mode: {mode}")
        self._time_mode = mode
        self._layout_cache_key = None
        self._refresh_geometry()

    def set_zoom_factor(self, factor: float) -> None:
        normalized = min(8.0, max(0.1, float(factor)))
        if math.isclose(normalized, self._zoom_factor):
            return
        self._zoom_factor = normalized
        self._refresh_geometry()

    def set_horizontal_view_offset(self, value: int) -> None:
        normalized = max(0, int(value))
        if normalized != self._horizontal_view_offset:
            self._horizontal_view_offset = normalized
            self.update()

    def set_hit_heading(self, value: str) -> None:
        self._hit_heading = str(value).strip() or "正式逐击"

    def display_time_at_widget_x(self, x: float) -> int:
        """Return the projected battle time under one widget-local x position."""

        return self._display_time_for_x(x)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh_height()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0d1117"))
        self._painted_bars = []
        self._painted_hits = []
        analysis = self._visible_analysis()
        if analysis is None or not analysis.timeline_hits:
            painter.setPen(QColor("#8b949e"))
            painter.drawText(self.rect(), Qt.AlignCenter, "当前战报没有可展示的正式逐击轴")
            return

        plot_left = float(LABEL_WIDTH)
        plot_right = float(max(LABEL_WIDTH + 1, self.width() - RIGHT_MARGIN))
        plot_width = max(1.0, plot_right - plot_left)
        layout = self._timeline_layout(analysis, plot_left, plot_width)
        lanes = layout.lanes
        input_rows = layout.input_rows
        action_rows = layout.action_rows
        group_rows = layout.group_rows
        self._lanes = lanes
        plot_bottom = float(lanes[-1].top + lanes[-1].height if lanes else TOP)

        self._paint_lane_backgrounds(painter, lanes, plot_right)
        self._paint_time_stops(painter, plot_left, plot_width, plot_bottom)
        self._paint_selected_range(painter, plot_left, plot_width, plot_bottom)
        self._paint_ticks(painter, plot_left, plot_width, plot_bottom)
        visible_rect = self._visible_paint_rect()
        for lane, item, rect in input_rows:
            if not rect.intersects(visible_rect):
                continue
            self._paint_input(painter, lane, item, rect)
        for lane, action, rect in action_rows:
            if not rect.intersects(visible_rect):
                continue
            self._paint_action(painter, lane, action, rect)
        hit_by_id = {hit.event_id: hit for hit in analysis.timeline_hits}
        maximum_group_damage = max(
            (group.damage for group in analysis.timeline_damage_groups),
            default=0.0,
        )
        maximum_hit_damage = max(
            (hit.damage for hit in analysis.timeline_hits),
            default=0.0,
        )
        for lane, group, rect in group_rows:
            if not rect.intersects(visible_rect):
                continue
            self._paint_damage_group(painter, lane, group, rect, maximum_group_damage)
            self._paint_group_hits(
                painter,
                lane,
                group,
                rect,
                hit_by_id,
                maximum_hit_damage,
                plot_left,
                plot_width,
                visible_rect,
            )
        paint_sticky_lane_labels(
            painter,
            lanes,
            sticky_left=float(self._horizontal_view_offset),
            plot_bottom=plot_bottom,
            draw_avatar=self._draw_avatar,
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() != Qt.LeftButton
            or self._analysis is None
            or self._is_in_sticky_label(event.position().x())
        ):
            return
        self._drag_start = event.position()
        self._drag_end = event.position()
        self._drag_start_global_x = event.globalPosition().x()
        self._drag_last_global_x = self._drag_start_global_x
        self._dragging = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        point = event.position()
        if self._drag_start is None:
            candidates = self._selection_candidates(point)
            tooltip = self._selection_tooltip(candidates[0]) if candidates else ""
            self.setToolTip(tooltip)
            return
        self._drag_end = point
        current_global_x = event.globalPosition().x()
        start_global_x = self._drag_start_global_x or current_global_x
        previous_global_x = self._drag_last_global_x or current_global_x
        self._drag_last_global_x = current_global_x
        if abs(current_global_x - start_global_x) >= 5:
            self._dragging = True
        if self._dragging:
            self.horizontal_pan_requested.emit(round(previous_global_x - current_global_x))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton or self._analysis is None or self._drag_start is None:
            return
        self._drag_end = event.position()
        if not self._dragging:
            self._show_detail_at(self._drag_end)
        self._clear_drag()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if self._analysis is None or self._is_in_sticky_label(event.pos().x()):
            return
        menu = QMenu(self)
        set_start = menu.addAction("设为分析开始")
        set_end = menu.addAction("设为分析结束")
        selected = menu.exec(event.globalPos())
        if selected is None:
            return
        if selected == set_start:
            boundary = "start"
        elif selected == set_end:
            boundary = "end"
        else:
            return
        raw_time = self._raw_time_for_x(
            event.pos().x(),
            prefer_interval_end=boundary == "end",
        )
        self.range_boundary_requested.emit(boundary, raw_time)

    def _paint_lane_backgrounds(
        self,
        painter: QPainter,
        lanes: Sequence[TimelineLane],
        plot_right: float,
    ) -> None:
        for index, lane in enumerate(lanes):
            if lane.kind == "input":
                color = QColor(210, 153, 34, 12)
            elif lane.kind == "action":
                color = QColor(47, 129, 247, 11)
            else:
                color = QColor(255, 255, 255, 5 if index % 2 == 0 else 9)
            painter.fillRect(QRectF(0, lane.top, plot_right, lane.height), color)
            painter.setPen(QColor("#30363d"))
            painter.drawLine(LABEL_WIDTH, lane.top, int(plot_right), lane.top)
        if lanes:
            bottom = lanes[-1].top + lanes[-1].height
            painter.setPen(QColor("#30363d"))
            painter.drawLine(LABEL_WIDTH, bottom, int(plot_right), bottom)

    def _paint_input(
        self,
        painter: QPainter,
        lane: TimelineLane,
        item: BattleInferredInput,
        rect: QRectF,
    ) -> None:
        role_index = timeline_role_index(
            self._lanes,
            item.character_id,
            item.character_name,
        )
        color = QColor(ROLE_COLORS[role_index % len(ROLE_COLORS)])
        color.setAlpha(185)
        painter.setBrush(color)
        pen = QPen(color.lighter(140), 1)
        if item.timing_confidence == "低":
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 6, 6)
        if item.is_character_switch:
            avatar_rect = QRectF(
                rect.left() + 2,
                rect.top() + 2,
                rect.height() - 4,
                rect.height() - 4,
            )
            self._draw_avatar(
                painter,
                item.character_id,
                item.character_name,
                avatar_rect,
                color,
            )
        else:
            text_rect = rect.adjusted(2, 0, -2, 0)
            text = painter.fontMetrics().elidedText(
                item.display_text,
                Qt.ElideRight,
                max(1, round(text_rect.width())),
            )
            painter.setPen(QColor("#f0f6fc"))
            painter.drawText(text_rect, Qt.AlignCenter, text)
        self._painted_bars.append(
            TimelinePaintedBar(
                kind="input",
                item_id=item.input_event_id,
                action_id=item.action_id,
                lane_key=lane.key,
                rect=rect,
                start_us=item.start_us,
                end_us=item.end_us,
                payload=item,
            )
        )

    def _paint_action(
        self,
        painter: QPainter,
        lane: TimelineLane,
        action: BattleInferredAction,
        rect: QRectF,
    ) -> None:
        color = QColor(ROLE_COLORS[lane.role_index % len(ROLE_COLORS)])
        color.setAlpha(205 if action.identity_confidence == "中" else 125)
        pen = QPen(color.lighter(135), 1)
        if action.timing_confidence == "低":
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(color)
        painter.drawRoundedRect(rect, 6, 6)
        label = f"{action.input_sequence} · {action.action_name.split('：', 1)[-1]}"
        text_rect = rect.adjusted(7, 0, -7, 0)
        painter.setPen(QColor("#f0f6fc"))
        painter.drawText(
            text_rect,
            Qt.AlignVCenter | Qt.AlignLeft,
            painter.fontMetrics().elidedText(
                label,
                Qt.ElideRight,
                max(1, round(text_rect.width())),
            ),
        )
        self._painted_bars.append(
            TimelinePaintedBar(
                kind="action",
                item_id=action.action_id,
                action_id=action.action_id,
                lane_key=lane.key,
                rect=rect,
                start_us=action.start_us,
                end_us=action.end_us,
                payload=action,
            )
        )

    def _paint_damage_group(
        self,
        painter: QPainter,
        lane: TimelineLane,
        group: BattleTimelineDamageGroup,
        base_rect: QRectF,
        maximum_damage: float,
    ) -> None:
        ratio = group.damage / maximum_damage if maximum_damage > 0 else 0.0
        thickness = 5.0 + 10.0 * math.sqrt(max(0.0, ratio))
        rect = QRectF(
            base_rect.left(),
            base_rect.center().y() - thickness / 2,
            base_rect.width(),
            thickness,
        )
        color = damage_group_color(self._lanes, group)
        color.setAlpha(205)
        painter.setPen(QPen(color.lighter(130), 1))
        painter.setBrush(color)
        painter.drawRoundedRect(rect, thickness / 2, thickness / 2)
        if rect.width() >= 86:
            painter.setPen(QColor("#f0f6fc"))
            painter.drawText(
                rect.adjusted(7, -5, -7, 5),
                Qt.AlignVCenter | Qt.AlignLeft,
                painter.fontMetrics().elidedText(
                    group.damage_name,
                    Qt.ElideRight,
                    max(1, round(rect.width() - 14)),
                ),
            )
        self._painted_bars.append(
            TimelinePaintedBar(
                kind="damage_group",
                item_id=group.group_id,
                action_id=None,
                lane_key=lane.key,
                rect=rect,
                start_us=group.start_us,
                end_us=group.end_us,
                payload=group,
            )
        )

    def _paint_group_hits(
        self,
        painter: QPainter,
        lane: TimelineLane,
        group: BattleTimelineDamageGroup,
        group_rect: QRectF,
        hit_by_id: dict[str, BattleAnalysisHit],
        maximum_damage: float,
        plot_left: float,
        plot_width: float,
        visible_rect: QRectF,
    ) -> None:
        color = damage_group_color(self._lanes, group)
        for event_id in group.evidence_event_ids:
            hit = hit_by_id.get(event_id)
            if hit is None:
                continue
            ratio = hit.damage / maximum_damage if maximum_damage > 0 else 0.0
            radius = 3.0 + 7.0 * math.sqrt(max(0.0, ratio))
            center = QPointF(
                self._x_for_raw_time(hit.relative_time_us, plot_left, plot_width),
                group_rect.center().y(),
            )
            rect = QRectF(
                center.x() - radius,
                center.y() - radius,
                radius * 2,
                radius * 2,
            )
            if not rect.intersects(visible_rect):
                continue
            painter.setPen(QPen(QColor(255, 255, 255, 115), 1))
            painter.setBrush(color.lighter(112))
            painter.drawEllipse(rect)
            self._painted_hits.append(TimelinePaintedHit(lane.key, rect, hit))

    def _paint_ticks(
        self,
        painter: QPainter,
        plot_left: float,
        plot_width: float,
        plot_bottom: float,
    ) -> None:
        span = self._display_span_us()
        origin = self._display_origin_us()
        step = nice_tick_step(span, plot_width)
        tick = 0
        while tick <= span:
            ratio = tick / span
            x = plot_left + plot_width * ratio
            painter.setPen(QColor("#21262d"))
            painter.drawLine(int(x), TOP, int(x), int(plot_bottom))
            painter.setPen(QColor("#8b949e"))
            painter.drawText(
                int(x) - 45,
                8,
                90,
                22,
                Qt.AlignCenter,
                format_time(origin + tick),
            )
            tick += step
        if (tick - step) < span:
            painter.setPen(QColor("#8b949e"))
            painter.drawText(
                round(plot_left + plot_width) - 90,
                8,
                90,
                22,
                Qt.AlignRight | Qt.AlignVCenter,
                format_time(origin + span),
            )

    def _paint_time_stops(
        self,
        painter: QPainter,
        plot_left: float,
        plot_width: float,
        plot_bottom: float,
    ) -> None:
        analysis = self._visible_analysis()
        if analysis is None:
            return
        for start_us, end_us in analysis.time_stop_intervals:
            if start_us is None or end_us is None or end_us <= start_us:
                continue
            if (
                end_us <= analysis.range_start_us
                or start_us >= analysis.range_end_us
            ):
                continue
            start_us = max(start_us, analysis.range_start_us)
            end_us = min(end_us, analysis.range_end_us)
            left = self._x_for_raw_time(start_us, plot_left, plot_width)
            right = self._x_for_raw_time(end_us, plot_left, plot_width)
            if self._time_mode == ELAPSED_TIME_MODE:
                painter.fillRect(
                    QRectF(left, TOP, max(1.0, right - left), max(1.0, plot_bottom - TOP)),
                    QColor(139, 148, 158, 38),
                )
            else:
                painter.setPen(QPen(QColor(139, 148, 158, 120), 1, Qt.DashLine))
                painter.drawLine(int(left), TOP, int(left), int(plot_bottom))

    def _paint_selected_range(
        self,
        painter: QPainter,
        plot_left: float,
        plot_width: float,
        plot_bottom: float,
    ) -> None:
        analysis = self._visible_analysis()
        if analysis is None:
            return
        left = self._x_for_raw_time(analysis.range_start_us, plot_left, plot_width)
        right = self._x_for_raw_time(analysis.range_end_us, plot_left, plot_width)
        painter.fillRect(
            QRectF(left, TOP, max(1.0, right - left), max(1.0, plot_bottom - TOP)),
            QColor(31, 111, 235, 20),
        )

    def _selection_candidates(self, point: QPointF) -> list[TimelineSelection]:
        if self._is_in_sticky_label(point.x()):
            return []
        lane_keys = {
            lane.key
            for lane in self._lanes
            if lane.top <= point.y() <= lane.top + lane.height
        }
        if not lane_keys:
            return []
        clicked_time = self._display_time_for_x(point.x())
        candidates: list[TimelineSelection] = []
        for painted in self._painted_hits:
            if painted.lane_key not in lane_keys:
                continue
            start, end = self._visible_display_time_window(
                painted.rect,
                padding_us=_HIT_PICK_TOLERANCE_US,
            )
            if (
                painted.rect.top() - 3 <= point.y() <= painted.rect.bottom() + 3
                and start <= clicked_time <= end
            ):
                candidates.append(
                    TimelineSelection("hit", painted.hit.event_id, painted.hit)
                )
        for painted in self._painted_bars:
            if (
                painted.lane_key not in lane_keys
                or not painted.rect.top() - 3 <= point.y() <= painted.rect.bottom() + 3
            ):
                continue
            start, end = self._visible_display_time_window(
                painted.rect,
                padding_us=_BAR_PICK_PADDING_US,
            )
            if not start <= clicked_time <= end:
                continue
            candidates.append(
                TimelineSelection(painted.kind, painted.item_id, painted.payload)
            )
        order = {"hit": 0, "damage_group": 1, "action": 2, "input": 3}
        candidates.sort(
            key=lambda item: (
                order[item.kind],
                getattr(
                    item.payload,
                    "start_us",
                    getattr(item.payload, "relative_time_us", 0),
                ),
                item.item_id,
            )
        )
        return candidates

    def _is_in_sticky_label(self, x: float) -> bool:
        left = float(self._horizontal_view_offset)
        return left <= float(x) < left + LABEL_WIDTH

    def _visible_display_time_window(
        self,
        rect: QRectF,
        *,
        padding_us: int,
    ) -> tuple[int, int]:
        """Convert the complete visible object width into display-time bounds."""

        left = self._display_time_for_x(rect.left())
        right = self._display_time_for_x(rect.right())
        return min(left, right) - padding_us, max(left, right) + padding_us

    def _show_detail_at(self, point: QPointF) -> None:
        candidates = self._selection_candidates(point)
        if not candidates:
            return
        current = next(
            (
                index
                for index, item in enumerate(candidates)
                if item.key == self._last_detail_key
            ),
            -1,
        )
        selected = candidates[(current + 1) % len(candidates)]
        self._last_detail_key = selected.key
        tooltip = self._selection_tooltip(selected)
        self.setToolTip(tooltip)
        QToolTip.showText(self.mapToGlobal(point.toPoint()), tooltip, self)
        self.selection_activated.emit(selected)

    def _selection_tooltip(self, selected: TimelineSelection) -> str:
        return build_timeline_tooltip(
            selected,
            projected_time=self._projected_time,
            hit_heading=self._hit_heading,
        )

    def _refresh_geometry(self) -> None:
        self.setMinimumWidth(self._required_content_width())
        self._refresh_height()
        self.updateGeometry()
        self.update()

    def _visible_paint_rect(self) -> QRectF:
        parent_width = self.parentWidget().width() if self.parentWidget() else self.width()
        return QRectF(
            self._horizontal_view_offset - 32,
            0,
            parent_width + 64,
            self.height(),
        )

    def _timeline_layout(
        self,
        analysis: BattleAnalysisSnapshot,
        plot_left: float,
        plot_width: float,
    ) -> TimelineLayout:
        key = (id(analysis), self._time_mode, round(plot_width))
        if key != self._layout_cache_key or self._layout_cache is None:
            self._layout_cache_key = key
            self._layout_cache = build_timeline_layout(
                analysis,
                x_for_time=lambda value: self._x_for_raw_time(
                    value,
                    plot_left,
                    plot_width,
                ),
            )
        return self._layout_cache

    def _required_content_width(self) -> int:
        plot_width = (
            self._display_span_us()
            / 1_000_000.0
            * _BASE_TIMELINE_PIXELS_PER_SECOND
            * self._zoom_factor
        )
        return max(
            _MIN_TIMELINE_WIDTH,
            math.ceil(LABEL_WIDTH + RIGHT_MARGIN + plot_width),
        )

    def _refresh_height(self) -> None:
        analysis = self._visible_analysis()
        if analysis is None or not analysis.timeline_hits:
            desired = 300
        else:
            plot_left = float(LABEL_WIDTH)
            plot_width = max(1.0, self.width() - LABEL_WIDTH - RIGHT_MARGIN)
            lanes = self._timeline_layout(analysis, plot_left, plot_width).lanes
            desired = max(
                300,
                (lanes[-1].top + lanes[-1].height + 18) if lanes else 300,
            )
        if self.height() != desired or self.minimumHeight() != desired:
            self.setFixedHeight(desired)
            self.content_height_changed.emit(desired)

    def _projected_time(self, raw_time_us: int) -> int:
        analysis = self._analysis
        if analysis is None:
            return max(0, int(raw_time_us))
        return project_timeline_time_us(
            raw_time_us,
            battle_start_us=analysis.battle_start_us,
            intervals=analysis.time_stop_intervals,
            mode=self._time_mode,
        )

    def _draw_avatar(
        self,
        painter: QPainter,
        character_id: int,
        character_name: str,
        rect: QRectF,
        fallback_color: QColor,
    ) -> None:
        if character_id not in self._avatar_cache:
            path = (
                self._asset_catalog.character_icon(character_id)
                if self._asset_catalog is not None
                else None
            )
            self._avatar_cache[character_id] = (
                QPixmap(str(path)) if path is not None else None
            )
        pixmap = self._avatar_cache[character_id]
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(rect, 5, 5)
        painter.setClipPath(clip)
        painter.fillRect(rect, fallback_color)
        if pixmap is not None and not pixmap.isNull():
            painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))
        else:
            painter.setPen(QColor("#ffffff"))
            painter.drawText(rect, Qt.AlignCenter, character_name[:1] or "?")
        painter.restore()
        painter.setPen(QPen(fallback_color.lighter(145), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, 5, 5)

    def _clear_drag(self) -> None:
        self._drag_start = None
        self._drag_end = None
        self._drag_start_global_x = None
        self._drag_last_global_x = None
        self._dragging = False
        self.update()
