"""Прогон на UI.png: прямоугольник, сетка, ячейки, два кадра, сохранение."""

from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from tilemapper.window import MainWindow, grab_preview

UI_PNG = Path(r"C:\projects\Bounce.Net.Net\Bounce.Net.Net\Content\Interface\UI.png")


def setUpModule() -> None:
    global APP
    APP = QApplication.instance() or QApplication([])


def _viewport_point(window: MainWindow, x: int, y: int) -> QPointF:
    approx = window.canvas.mapFromScene(QPointF(x + 0.5, y + 0.5))
    ax, ay = int(approx.x()), int(approx.y())
    for dy in range(-6, 7):
        for dx in range(-6, 7):
            view = QPoint(ax + dx, ay + dy)
            back = window.canvas.mapToScene(view)
            if math.floor(back.x()) == x and math.floor(back.y()) == y:
                return QPointF(view)
    raise AssertionError(f"нет пикселя вида для сцены {x},{y}, рядом {approx}")


def _drag(window: MainWindow, x0: int, y0: int, x1: int, y1: int) -> None:
    window.canvas.centerOn((x0 + x1) / 2, (y0 + y1) / 2)
    QApplication.processEvents()
    viewport = window.canvas.viewport()
    start = _viewport_point(window, x0, y0)
    end = _viewport_point(window, x1, y1)
    for scene, view in (((x0, y0), start), ((x1, y1), end)):
        back = window.canvas.mapToScene(view.toPoint())
        if math.floor(back.x()) != scene[0] or math.floor(back.y()) != scene[1]:
            raise AssertionError(
                f"точка {scene} уехала в {back.x()}, {back.y()} / вид {view}"
            )
    QApplication.sendEvent(
        viewport,
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(start),
            viewport.mapToGlobal(start.toPoint()),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    QApplication.sendEvent(
        viewport,
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(end),
            viewport.mapToGlobal(end.toPoint()),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    QApplication.sendEvent(
        viewport,
        QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(end),
            viewport.mapToGlobal(end.toPoint()),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    QApplication.processEvents()


class SmokeTest(unittest.TestCase):
    def test_ui_png_markup_roundtrip(self) -> None:
        self.assertTrue(UI_PNG.is_file(), "нет примера UI.png")
        window = MainWindow()
        window.resize(1400, 900)
        window.show()
        QApplication.processEvents()
        self.assertTrue(window.open_image(UI_PNG, confirm=False))
        QApplication.processEvents()
        self.assertEqual(window._pixmap.width(), 800)
        self.assertEqual(window._pixmap.height(), 600)
        self.assertGreater(window.canvas.viewport().width(), 50)

        window.canvas.zoom_actual()
        window.canvas.horizontalScrollBar().setValue(0)
        window.canvas.verticalScrollBar().setValue(0)
        QApplication.processEvents()

        window.set_tool("rect")
        _drag(window, 8, 8, 39, 23)
        self.assertEqual(len(window.doc.tiles), 1)
        button = window.doc.tiles[0]
        self.assertEqual((button.x, button.y, button.w, button.h), (8, 8, 32, 16))
        self.assertIsNotNone(window.canvas._sel_black)
        self.assertTrue(window.canvas._sel_black.isVisible())
        window.name_edit.setText("panel")
        self.assertEqual(button.name, "panel")

        window.canvas.grid_ready.emit(0, 520, 64, 32)
        QApplication.processEvents()
        self.assertEqual(len(window.doc.grids), 1)
        grid = window.doc.grids[0]
        self.assertEqual((grid.x, grid.y, grid.cols, grid.rows), (0, 520, 2, 1))
        window.grid_cw.setValue(8)
        window.grid_ch.setValue(8)
        self.assertEqual((grid.cols, grid.rows, grid.cell_w, grid.cell_h), (8, 4, 8, 8))

        window.set_tool("cell")
        _drag(window, 1, 521, 20, 530)
        names = [tile.name for tile in window.doc.tiles if tile.name.startswith("tile_")]
        self.assertGreaterEqual(len(names), 2)
        self.assertIn("tile_0", names)

        window._select(window.doc.tiles[0].id)
        window._assign_anim("a")
        window._select(window.doc.tiles[1].id)
        window._assign_anim("b")
        window.opacity_slider.setValue(40)
        window.blink_btn.setChecked(True)
        shown = window._blink_show_a
        from PySide6.QtTest import QTest

        QTest.qWait(400)
        self.assertNotEqual(window._blink_show_a, shown)
        self.assertFalse(window.preview.grab().isNull())
        window.blink_btn.setChecked(False)

        window._append_prop_row("any_if_use", "4")
        window._on_props_changed()
        self.assertEqual(window._selected_tile().extra["any_if_use"], 4)

        shot = grab_preview(window)
        self.assertFalse(shot.isNull())
        self.assertTrue(_has_ink(shot), "холст пустой")

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            work = root / "ui.json"
            clean = root / "ui.clean.json"
            self.assertTrue(window.save_document(work))
            self.assertTrue(window.export_document(clean))
            saved = json.loads(work.read_text(encoding="utf-8"))
            exported = json.loads(clean.read_text(encoding="utf-8"))
            self.assertIn("editor", saved)
            self.assertEqual(saved["editor"]["grids"][0]["cell_w"], 8)
            self.assertNotIn("editor", exported)
            self.assertEqual(exported["nametileset"], "UI")
            self.assertIsInstance(exported["tiles"], list)
            self.assertTrue(any(tile.get("any_if_use") == 4 for tile in exported["tiles"]))

            again = MainWindow()
            again.show()
            self.assertTrue(again.open_document(work, confirm=False))
            QApplication.processEvents()
            self.assertEqual(again._pixmap.size(), window._pixmap.size())
            self.assertEqual(len(again.doc.tiles), len(window.doc.tiles))
            self.assertEqual(again.doc.grids[0].cell_h, 8)
            self.assertEqual(again.doc.nametileset, "UI")
            named = again.doc.find(button.id)
            self.assertIsNotNone(named)
            assert named is not None
            self.assertEqual(named.name, "panel")
            again.close()

        window.close()


def _has_ink(image) -> bool:
    step_x = max(1, image.width() // 40)
    step_y = max(1, image.height() // 40)
    for y in range(0, image.height(), step_y):
        for x in range(0, image.width(), step_x):
            color = image.pixelColor(x, y)
            if color.red() > 200 and color.green() > 200 and color.blue() > 200:
                return True
    return False


if __name__ == "__main__":
    unittest.main()
