"""Холст: картинка, тайлы, временные сетки, зум без сглаживания."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsScene, QGraphicsView

from tilemapper.model import Grid, Tile, inclusive_rect, snapped_rect

ZOOM_LEVELS = (0.125, 0.25, 0.5, 1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 24, 32)


class Canvas(QGraphicsView):
    rect_ready = Signal(int, int, int, int)
    grid_ready = Signal(int, int, int, int)
    cells_ready = Signal(object)
    selection_changed = Signal(int)
    background_picked = Signal(int, int)
    tile_moved = Signal(int, int, int)
    cursor_moved = Signal(int, int)
    zoom_changed = Signal(float)
    hint = Signal(str)
    tool_hotkey = Signal(str)
    snap_hotkey = Signal()
    fit_hotkey = Signal()
    duplicate_pressed = Signal()
    delete_pressed = Signal()
    nudge_pressed = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.scene().setBackgroundBrush(QColor("#e8e8e8"))
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)

        self._pixmap = QPixmap()
        self._tiles: list[Tile] = []
        self._background: list[tuple[int, Tile]] = []
        self._grids: list[Grid] = []
        self._selected_id = -1
        self._active_index = -1
        self._tool = "select"
        self._snap = False
        self._space = False
        self._panning = False
        self._pan_last: QPointF | None = None
        self._press: tuple[int, int] | None = None
        self._drag_kind: str | None = None
        self._moved = False
        self._drag_tile_id: int | None = None
        self._drag_origin: tuple[int, int] | None = None
        self._cell_anchor: tuple[int, int] | None = None
        self._pix_item = None
        self._tile_items: list[tuple[int, QGraphicsRectItem]] = []
        self._rubber: QGraphicsRectItem | None = None
        self._hover: QGraphicsRectItem | None = None
        self._sel_black: QGraphicsRectItem | None = None
        self._sel_white: QGraphicsRectItem | None = None
        self._cell_black: QGraphicsRectItem | None = None
        self._cell_white: QGraphicsRectItem | None = None
        self._ants_phase = 0.0
        self._ants_timer = QTimer(self)
        self._ants_timer.timeout.connect(self._tick_ants)
        self._ants_timer.start(70)
        self._apply_cursor()

    def set_world(
        self,
        pixmap: QPixmap,
        tiles: list[Tile],
        grids: list[Grid],
        selected_id: int,
        active_index: int,
        background: list[tuple[int, Tile]] | None = None,
    ) -> None:
        self._pixmap = pixmap
        self._tiles = tiles
        self._background = list(background or [])
        self._grids = grids
        self._selected_id = selected_id
        self._active_index = active_index
        self.rebuild()

    def set_selected(self, tile_id: int) -> None:
        self._selected_id = tile_id
        self._restyle()
        self._sync_selection_ants()

    def set_active_grid(self, index: int) -> None:
        self._active_index = index

    def set_tool(self, tool: str) -> None:
        self._tool = tool
        self._drag_kind = None
        if self._rubber is not None:
            self._rubber.setVisible(False)
        if self._hover is not None and tool != "cell":
            self._hover.setVisible(False)
        if tool != "cell":
            self._show_ants(self._cell_black, self._cell_white, None)
        self._apply_cursor()

    def set_snap(self, enabled: bool) -> None:
        self._snap = enabled

    @property
    def snap(self) -> bool:
        return self._snap

    def current_zoom(self) -> float:
        return self.transform().m11()

    def zoom_actual(self) -> None:
        self.resetTransform()
        self.zoom_changed.emit(1.0)

    def zoom_set(self, zoom: float) -> None:
        self.resetTransform()
        if zoom and zoom > 0:
            self.scale(zoom, zoom)
        self.zoom_changed.emit(self.transform().m11())

    def zoom_fit(self) -> None:
        if self._pix_item is None:
            return
        self.fitInView(self._pix_item, Qt.AspectRatioMode.KeepAspectRatio)
        self.zoom_changed.emit(self.transform().m11())

    def center_on_rect(self, x: int, y: int, w: int, h: int) -> None:
        self.centerOn(x + w / 2, y + h / 2)

    def rebuild(self) -> None:
        transform = self.transform()
        center = self.mapToScene(self.viewport().rect().center())
        self._pix_item = None
        self._tile_items = []
        self._rubber = None
        self._hover = None
        self._sel_black = None
        self._sel_white = None
        self._cell_black = None
        self._cell_white = None
        self.scene().clear()

        bounds = QRectF(0, 0, 8, 8)
        if not self._pixmap.isNull():
            self._pix_item = self.scene().addPixmap(self._pixmap)
            self._pix_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
            self._pix_item.setZValue(0)
            bounds = QRectF(self._pix_item.boundingRect())

        taken = {(tile.x, tile.y, tile.w, tile.h) for tile in self._tiles}
        taken.update((tile.x, tile.y, tile.w, tile.h) for _layer, tile in self._background)
        for _layer, tile in self._background:
            item = self.scene().addRect(tile.x, tile.y, tile.w, tile.h)
            pen = QPen(QColor("#6b7280"))
            pen.setCosmetic(True)
            pen.setStyle(Qt.PenStyle.DashLine)
            item.setPen(pen)
            item.setBrush(QBrush(QColor(107, 114, 128, 28)))
            item.setZValue(4)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            bounds = bounds.united(QRectF(tile.x, tile.y, tile.w, tile.h))
        for index, grid in enumerate(self._grids):
            self._draw_grid(grid, index == self._active_index, taken)
            bounds = bounds.united(
                QRectF(
                    grid.x,
                    grid.y,
                    grid.cols * grid.cell_w,
                    grid.rows * grid.cell_h,
                )
            )

        for tile in self._tiles:
            item = self.scene().addRect(tile.x, tile.y, tile.w, tile.h)
            item.setZValue(10)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._style_tile(item, tile.id == self._selected_id)
            self._tile_items.append((tile.id, item))
            bounds = bounds.united(QRectF(tile.x, tile.y, tile.w, tile.h))

        self._hover = self._make_overlay("#1a73e8", QColor(26, 115, 232, 36))
        self._rubber = self._make_overlay("#1a73e8", QColor(26, 115, 232, 40))
        self._sel_black, self._sel_white = self._make_ant_pair()
        self._cell_black, self._cell_white = self._make_ant_pair()
        self._sync_selection_ants()
        pad = 80.0
        self.scene().setSceneRect(bounds.adjusted(-pad, -pad, pad, pad))
        self.setTransform(transform)
        self.centerOn(center)

    def _make_overlay(self, color: str, fill: QColor) -> QGraphicsRectItem:
        item = self.scene().addRect(0, 0, 0, 0)
        pen = QPen(QColor(color))
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.DashLine)
        item.setPen(pen)
        item.setBrush(QBrush(fill))
        item.setZValue(30)
        item.setVisible(False)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        return item

    def _make_ant_pair(self) -> tuple[QGraphicsRectItem, QGraphicsRectItem]:
        black = self.scene().addRect(0, 0, 0, 0)
        white = self.scene().addRect(0, 0, 0, 0)
        for item in (black, white):
            item.setZValue(40)
            item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            item.setVisible(False)
        return black, white

    def _tick_ants(self) -> None:
        self._ants_phase = (self._ants_phase + 1) % 8
        self._paint_ants(self._sel_black, self._sel_white)
        self._paint_ants(self._cell_black, self._cell_white)

    def _paint_ants(
        self, black: QGraphicsRectItem | None, white: QGraphicsRectItem | None
    ) -> None:
        if black is None or white is None or not black.isVisible():
            return
        black.setPen(self._ant_pen("#111111", 0))
        white.setPen(self._ant_pen("#ffffff", 4))

    def _ant_pen(self, color: str, shift: float) -> QPen:
        pen = QPen(QColor(color))
        pen.setCosmetic(True)
        pen.setWidth(1)
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([4, 4])
        pen.setDashOffset(self._ants_phase + shift)
        return pen

    def _show_ants(
        self,
        black: QGraphicsRectItem | None,
        white: QGraphicsRectItem | None,
        rect: tuple[int, int, int, int] | None,
    ) -> None:
        if black is None or white is None:
            return
        if rect is None:
            black.setVisible(False)
            white.setVisible(False)
            return
        x, y, w, h = rect
        for item in (black, white):
            item.setRect(x, y, max(1, w), max(1, h))
            item.setVisible(True)
        self._paint_ants(black, white)

    def _sync_selection_ants(self) -> None:
        tile = next((item for item in self._tiles if item.id == self._selected_id), None)
        if tile is None:
            self._show_ants(self._sel_black, self._sel_white, None)
            return
        self._show_ants(self._sel_black, self._sel_white, (tile.x, tile.y, tile.w, tile.h))

    def _draw_grid(self, grid: Grid, active: bool, taken: set[tuple[int, int, int, int]]) -> None:
        if not grid.visible:
            return
        color = QColor(grid.color)
        if not color.isValid():
            color = QColor("#3d8bfd")
        width = grid.cols * grid.cell_w
        height = grid.rows * grid.cell_h
        path = QPainterPath()
        for col in range(grid.cols + 1):
            x = grid.x + col * grid.cell_w
            path.moveTo(x, grid.y)
            path.lineTo(x, grid.y + height)
        for row in range(grid.rows + 1):
            y = grid.y + row * grid.cell_h
            path.moveTo(grid.x, y)
            path.lineTo(grid.x + width, y)
        pen = QPen(color)
        pen.setCosmetic(True)
        pen.setWidth(2 if active else 1)
        if not active:
            faded = QColor(color)
            faded.setAlpha(150)
            pen.setColor(faded)
        item = self.scene().addPath(path, pen)
        item.setZValue(2 if active else 1)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        if not active or grid.cols * grid.rows > 20000:
            return
        fill = QColor(color)
        fill.setAlpha(42)
        for row in range(grid.rows):
            for col in range(grid.cols):
                rect = grid.cell_rect(col, row)
                if rect not in taken:
                    continue
                cell = self.scene().addRect(*rect)
                cell.setPen(QPen(Qt.PenStyle.NoPen))
                cell.setBrush(QBrush(fill))
                cell.setZValue(3)
                cell.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def _style_tile(self, item: QGraphicsRectItem, selected: bool) -> None:
        if selected:
            pen = QPen(QColor("#0b57d0"))
            pen.setCosmetic(True)
            pen.setWidth(2)
            item.setPen(pen)
            item.setBrush(QBrush(QColor(26, 115, 232, 48)))
        else:
            pen = QPen(QColor("#c2410c"))
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setBrush(QBrush(QColor(194, 65, 12, 28)))

    def _restyle(self) -> None:
        for tile_id, item in self._tile_items:
            self._style_tile(item, tile_id == self._selected_id)

    def _active_grid(self) -> Grid | None:
        if self._active_index < 0 or self._active_index >= len(self._grids):
            return None
        return self._grids[self._active_index]

    def _tile_at(self, x: int, y: int) -> Tile | None:
        picked = self._pick_tile(x, y)
        if picked is None or picked[0] != "active":
            return None
        return picked[2]

    def _pick_tile(self, x: int, y: int) -> tuple[str, int, Tile] | None:
        for tile in reversed(self._tiles):
            if tile.x <= x < tile.x + tile.w and tile.y <= y < tile.y + tile.h:
                return ("active", -1, tile)
        for layer, tile in reversed(self._background):
            if tile.x <= x < tile.x + tile.w and tile.y <= y < tile.y + tile.h:
                return ("bg", layer, tile)
        return None

    def _item_for(self, tile_id: int) -> QGraphicsRectItem | None:
        for ident, item in self._tile_items:
            if ident == tile_id:
                return item
        return None

    def _scene_pixel(self, event) -> tuple[int, int]:
        point = self.mapToScene(event.position().toPoint())
        return math.floor(point.x() + 1e-4), math.floor(point.y() + 1e-4)

    def _drag_rect(self, x0: int, y0: int, x1: int, y1: int, *, snap: bool):
        if snap:
            grid = self._active_grid()
            if grid is not None and grid.visible:
                return snapped_rect(x0, y0, x1, y1, grid)
        return inclusive_rect(x0, y0, x1, y1)

    def _place(self, item: QGraphicsRectItem | None, x: int, y: int, w: int, h: int) -> None:
        if item is None:
            return
        item.setRect(x, y, max(1, w), max(1, h))
        item.setVisible(True)

    def _apply_cursor(self) -> None:
        if self._panning:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self._space:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif self._tool in ("rect", "grid", "cell"):
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def wheelEvent(self, event) -> None:
        current = self.transform().m11()
        if event.angleDelta().y() > 0:
            nxt = next((level for level in ZOOM_LEVELS if level > current * 1.01), ZOOM_LEVELS[-1])
        else:
            nxt = next(
                (level for level in reversed(ZOOM_LEVELS) if level < current * 0.99),
                ZOOM_LEVELS[0],
            )
        if abs(nxt - current) > 1e-6:
            factor = nxt / current
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
            self.scale(factor, factor)
            self.zoom_changed.emit(self.transform().m11())
        event.accept()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space = True
            self._apply_cursor()
            event.accept()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 8 if mods & Qt.KeyboardModifier.ShiftModifier else 1
            dx = dy = 0
            if key == Qt.Key.Key_Left:
                dx = -step
            elif key == Qt.Key.Key_Right:
                dx = step
            elif key == Qt.Key.Key_Up:
                dy = -step
            else:
                dy = step
            self.nudge_pressed.emit(dx, dy)
            event.accept()
            return
        if key == Qt.Key.Key_Delete:
            self.delete_pressed.emit()
            event.accept()
            return
        if key == Qt.Key.Key_D and mods & Qt.KeyboardModifier.ControlModifier:
            self.duplicate_pressed.emit()
            event.accept()
            return
        if key == Qt.Key.Key_G and not mods:
            self.snap_hotkey.emit()
            event.accept()
            return
        if key == Qt.Key.Key_F and not mods:
            self.fit_hotkey.emit()
            event.accept()
            return
        if key == Qt.Key.Key_1 and mods & Qt.KeyboardModifier.ControlModifier:
            self.zoom_actual()
            event.accept()
            return
        tools = {
            Qt.Key.Key_1: "select",
            Qt.Key.Key_2: "rect",
            Qt.Key.Key_3: "grid",
            Qt.Key.Key_4: "cell",
        }
        if key in tools and not (mods & Qt.KeyboardModifier.ControlModifier):
            self.tool_hotkey.emit(tools[key])
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space = False
            if not self._panning:
                self._apply_cursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton or (
            self._space and event.button() == Qt.MouseButton.LeftButton
        ):
            self._panning = True
            self._pan_last = event.position()
            self._apply_cursor()
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        x, y = self._scene_pixel(event)
        self._press = (x, y)
        self._moved = False
        self._drag_kind = self._tool
        if self._tool == "select":
            picked = self._pick_tile(x, y)
            if picked is None:
                self._drag_kind = None
                self._drag_tile_id = None
                self.set_selected(-1)
                self.selection_changed.emit(-1)
            elif picked[0] == "bg":
                self._drag_kind = None
                self._drag_tile_id = None
                self.background_picked.emit(picked[1], picked[2].id)
            else:
                tile = picked[2]
                self._drag_tile_id = tile.id
                self._drag_origin = (tile.x, tile.y)
                self.set_selected(tile.id)
                self.selection_changed.emit(tile.id)
            event.accept()
            return
        if self._tool == "cell":
            grid = self._active_grid()
            if grid is None or not grid.visible:
                self.hint.emit("Сначала выбери видимую сетку")
                self._drag_kind = None
                event.accept()
                return
            hit = grid.cell_index(x, y)
            if hit is None:
                self._drag_kind = None
                event.accept()
                return
            self._cell_anchor = hit
            rect = grid.cell_rect(*hit)
            self._place(self._hover, *rect)
            self._show_ants(self._cell_black, self._cell_white, rect)
            event.accept()
            return
        self._place(self._rubber, x, y, 1, 1)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        x, y = self._scene_pixel(event)
        self.cursor_moved.emit(x, y)
        if self._panning and self._pan_last is not None:
            delta = event.position() - self._pan_last
            self._pan_last = event.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - delta.y()))
            event.accept()
            return
        if self._tool == "cell" and self._drag_kind != "cell":
            self._hover_cell(x, y)
        if not (event.buttons() & Qt.MouseButton.LeftButton) or self._press is None:
            super().mouseMoveEvent(event)
            return
        if self._drag_kind == "select" and self._drag_tile_id is not None and self._drag_origin:
            if abs(x - self._press[0]) + abs(y - self._press[1]) >= 2:
                self._moved = True
                ox, oy = self._drag_origin
                nx = ox + (x - self._press[0])
                ny = oy + (y - self._press[1])
                grid = self._active_grid()
                if self._snap and grid is not None and grid.visible:
                    nx, ny = grid.snap_point(nx, ny)
                item = self._item_for(self._drag_tile_id)
                if item is not None:
                    rect = item.rect()
                    item.setRect(nx, ny, rect.width(), rect.height())
                    self._show_ants(
                        self._sel_black,
                        self._sel_white,
                        (nx, ny, int(rect.width()), int(rect.height())),
                    )
                self.hint.emit(f"{nx}, {ny}")
            event.accept()
            return
        if self._drag_kind in ("rect", "grid"):
            self._moved = True
            rect = self._drag_rect(*self._press, x, y, snap=self._snap and self._drag_kind == "rect")
            if rect is not None:
                self._place(self._rubber, *rect)
                self.hint.emit(f"{rect[2]}×{rect[3]}")
            event.accept()
            return
        if self._drag_kind == "cell" and self._cell_anchor is not None:
            grid = self._active_grid()
            if grid is not None:
                hit = grid.cell_index(x, y) or self._cell_anchor
                cells = grid.cells_between(self._cell_anchor[0], self._cell_anchor[1], hit[0], hit[1])
                if cells:
                    xs = [cell[0] for cell in cells]
                    ys = [cell[1] for cell in cells]
                    span = (
                        min(xs),
                        min(ys),
                        max(cell[0] + cell[2] for cell in cells) - min(xs),
                        max(cell[1] + cell[3] for cell in cells) - min(ys),
                    )
                    self._place(self._hover, *span)
                    self._show_ants(self._cell_black, self._cell_white, span)
                    self.hint.emit(f"{len(cells)} яч.")
            event.accept()
            return
        super().mouseMoveEvent(event)

    def _hover_cell(self, x: int, y: int) -> None:
        grid = self._active_grid()
        if grid is None or not grid.visible or self._hover is None:
            return
        hit = grid.cell_index(x, y)
        if hit is None:
            self._hover.setVisible(False)
            self._show_ants(self._cell_black, self._cell_white, None)
            return
        rect = grid.cell_rect(*hit)
        self._place(self._hover, *rect)
        self._show_ants(self._cell_black, self._cell_white, rect)

    def mouseReleaseEvent(self, event) -> None:
        if self._panning and (
            event.button() == Qt.MouseButton.MiddleButton
            or event.button() == Qt.MouseButton.LeftButton
        ):
            self._panning = False
            self._pan_last = None
            self._apply_cursor()
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        x, y = self._scene_pixel(event)
        kind = self._drag_kind
        press = self._press
        self._drag_kind = None
        self._press = None
        if self._rubber is not None:
            self._rubber.setVisible(False)
        if kind == "select" and self._moved and self._drag_tile_id is not None:
            item = self._item_for(self._drag_tile_id)
            if item is not None:
                rect = item.rect()
                self.tile_moved.emit(self._drag_tile_id, int(rect.x()), int(rect.y()))
        elif kind == "rect" and press is not None:
            rect = self._drag_rect(*press, x, y, snap=self._snap)
            if rect is not None and rect[2] >= 2 and rect[3] >= 2:
                self.rect_ready.emit(*rect)
        elif kind == "grid" and press is not None:
            rect = self._drag_rect(*press, x, y, snap=False)
            if rect is not None and rect[2] >= 2 and rect[3] >= 2:
                self.grid_ready.emit(*rect)
        elif kind == "cell" and self._cell_anchor is not None:
            grid = self._active_grid()
            if grid is not None:
                hit = grid.cell_index(x, y) or self._cell_anchor
                cells = grid.cells_between(
                    self._cell_anchor[0], self._cell_anchor[1], hit[0], hit[1]
                )
                if cells:
                    self.cells_ready.emit(cells)
            if self._hover is not None:
                self._hover.setVisible(False)
        self._cell_anchor = None
        self._drag_tile_id = None
        self._moved = False
        self.hint.emit("")
        event.accept()
