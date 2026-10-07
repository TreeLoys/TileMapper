"""Чтение .aseprite без установленного редактора."""

from __future__ import annotations

import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsSimpleTextItem  # noqa: E402

from tilemapper.aseprite import decode_aseprite  # noqa: E402
from tilemapper.model import Grid  # noqa: E402
from tilemapper.images import load_pixmap  # noqa: E402
from tilemapper.recent import forget  # noqa: E402
from tilemapper.window import MainWindow, available_json_path, reveal_targets  # noqa: E402

CHUNK_LAYER = 0x2004
CHUNK_CEL = 0x2005
CHUNK_PALETTE = 0x2019


def _chunk(kind: int, payload: bytes) -> bytes:
    return struct.pack("<IH", 6 + len(payload), kind) + payload


def _layer(flags: int = 1, kind: int = 0, level: int = 0, opacity: int = 255, name: bytes = b"L") -> bytes:
    payload = struct.pack("<HHHHB", flags, kind, level, 0, opacity) + b"\x00\x00\x00"
    payload += struct.pack("<H", len(name)) + name
    return _chunk(CHUNK_LAYER, payload)


def _layer_modern(flags: int = 1, opacity: int = 255, name: bytes = b"Background") -> bytes:
    payload = struct.pack("<HHHHHHB", flags, 0, 0, 0, 0, 0, opacity) + b"\x00\x00\x00"
    payload += struct.pack("<H", len(name)) + name
    return _chunk(CHUNK_LAYER, payload)


def _cel(rgba: bytes, width: int, height: int, *, layer: int = 0, x: int = 0, y: int = 0, opacity: int = 255) -> bytes:
    payload = struct.pack("<HhhBHh", layer, x, y, opacity, 2, 0) + b"\x00" * 5
    payload += struct.pack("<HH", width, height) + zlib.compress(rgba)
    return _chunk(CHUNK_CEL, payload)


def _link(frame: int, *, layer: int = 0, x: int = 0, y: int = 0, opacity: int = 255) -> bytes:
    payload = struct.pack("<HhhBHh", layer, x, y, opacity, 1, 0) + b"\x00" * 5
    payload += struct.pack("<H", frame)
    return _chunk(CHUNK_CEL, payload)


def _palette(colors: list[tuple[int, int, int, int]]) -> bytes:
    payload = struct.pack("<III", len(colors), 0, len(colors) - 1) + b"\x00" * 8
    for red, green, blue, alpha in colors:
        payload += struct.pack("<HBBBB", 0, red, green, blue, alpha)
    return _chunk(CHUNK_PALETTE, payload)


def _pack_frame(parts: list[bytes]) -> bytes:
    chunks = b"".join(parts)
    return struct.pack("<IHHH", 16 + len(chunks), 0xF1FA, len(parts), 100) + b"\x00\x00" + struct.pack("<I", len(parts)) + chunks


def _ase_file(width: int, height: int, frames: list[list[bytes]], depth: int = 32, flags: int = 0) -> bytes:
    body = b"".join(_pack_frame(parts) for parts in frames)
    header = bytearray(128)
    struct.pack_into("<IHHHHHI", header, 0, 128 + len(body), 0xA5E0, len(frames), width, height, depth, flags)
    return bytes(header) + body


class AsepriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_modern_layer_header_keeps_pixels(self) -> None:
        data = _ase_file(
            1,
            1,
            [[_layer_modern(), _cel(bytes([10, 20, 30, 255]), 1, 1)]],
            flags=1,
        )
        image = decode_aseprite(data)
        self.assertEqual(image.rgba, bytes([10, 20, 30, 255]))

    def test_user_ui_aseprite_is_not_blank(self) -> None:
        path = Path(r"C:\projects\Bounce.Net.Net\Bounce.Net.Net\Content\Interface\UI.aseprite")
        if not path.is_file():
            self.skipTest("нет UI.aseprite")
        image = decode_aseprite(path.read_bytes())
        self.assertEqual((image.width, image.height), (800, 600))
        self.assertGreater(sum(image.rgba[3::4]), 0)

    def test_flattened_rgba_and_offset_cel(self) -> None:
        data = _ase_file(
            2,
            1,
            [[_layer(), _cel(bytes([255, 0, 0, 255]), 1, 1, x=1)]],
        )
        image = decode_aseprite(data)
        self.assertEqual(image.frames, 1)
        self.assertEqual(image.rgba, bytes([0, 0, 0, 0, 255, 0, 0, 255]))

    def test_hidden_group_skips_child(self) -> None:
        data = _ase_file(
            1,
            1,
            [[
                _layer(flags=0, kind=1, level=0, name=b"G"),
                _layer(flags=1, kind=0, level=1, name=b"C"),
                _cel(bytes([0, 255, 0, 255]), 1, 1, layer=1),
            ]],
        )
        image = decode_aseprite(data)
        self.assertEqual(image.rgba, bytes(4))

    def test_linked_cel_uses_other_frame_pixels(self) -> None:
        red = bytes([255, 0, 0, 255])
        data = _ase_file(
            1,
            1,
            [
                [_layer(), _link(1, x=0, y=0)],
                [_cel(red, 1, 1)],
            ],
        )
        image = decode_aseprite(data)
        self.assertEqual(image.rgba, red)
        self.assertEqual(image.frames, 2)

    def test_indexed_palette(self) -> None:
        data = _ase_file(
            1,
            1,
            [[_palette([(0, 0, 0, 0), (0, 0, 255, 255)]), _layer(), _cel(bytes([1]), 1, 1)]],
            depth=8,
        )
        image = decode_aseprite(data)
        self.assertEqual(image.rgba, bytes([0, 0, 255, 255]))

    def test_pixmap_keeps_straight_alpha(self) -> None:
        data = _ase_file(1, 1, [[_layer(), _cel(bytes([10, 20, 30, 255]), 1, 1)]])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sheet.aseprite"
            path.write_bytes(data)
            pixmap, note = load_pixmap(path)
        self.assertEqual(note, "")
        self.assertFalse(pixmap.isNull())
        color = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888).pixelColor(0, 0)
        self.assertEqual((color.red(), color.green(), color.blue(), color.alpha()), (10, 20, 30, 255))
        self.assertEqual(color, QColor(10, 20, 30, 255))

    def test_window_reloads_aseprite_and_keeps_tiles(self) -> None:
        red = _ase_file(1, 1, [[_layer(), _cel(bytes([255, 0, 0, 255]), 1, 1)]])
        green = _ase_file(1, 1, [[_layer(), _cel(bytes([0, 180, 0, 255]), 1, 1)]])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "font.aseprite"
            path.write_bytes(red)
            window = MainWindow()
            try:
                self.assertTrue(window.open_image(path, confirm=False))
                window.doc.add_tile(0, 0, 1, 1, "A")
                titles = [action.text() for action in window._file_menu.actions()]
                for title in ("Открыть картинку", "Открыть json", "Сохранить", "Сохранить как", "Экспорт чистый", "Показать в папке"):
                    self.assertIn(title, titles)
                window._fill_file_recent()
                texts = [action.text() for action in window._file_menu.actions()]
                self.assertLess(texts.index("Показать в папке"), texts.index("Все недавние"))
                self.assertFalse(any(slot.toolTip() == str(path.resolve()) for slot in window._recent_slots))
                self.assertNotEqual(window._last_project_action.toolTip(), str(path.resolve()))
                path.write_bytes(green)
                window._reload_image_from_disk()
                color = window._pixmap.toImage().pixelColor(0, 0)
                self.assertEqual((color.red(), color.green(), color.blue()), (0, 180, 0))
                self.assertEqual(window.doc.tiles[0].name, "A")
            finally:
                window._reload_timer.stop()
                window.close()
                forget(path)

    def test_reveal_targets_one_folder_for_image_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "UI.png"
            document = root / "UI.json"
            image.write_bytes(b"png")
            document.write_text("{}", encoding="utf-8")
            self.assertEqual(reveal_targets(document, image), [document])
            other = root / "elsewhere"
            other.mkdir()
            sheet = other / "UI.png"
            sheet.write_bytes(b"png")
            self.assertEqual(reveal_targets(document, sheet), [document, sheet])
            self.assertEqual(reveal_targets(None, None), [])

    def test_save_suggestion_skips_existing_json(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "UI.png"
            image.write_bytes(b"png")
            self.assertEqual(available_json_path(image), root / "UI.json")
            (root / "UI.json").write_text("{}", encoding="utf-8")
            (root / "UI_2.json").write_text("{}", encoding="utf-8")
            self.assertEqual(available_json_path(image), root / "UI_3.json")

    def test_grid_mark_reveals_grid_properties(self) -> None:
        window = MainWindow()
        window.resize(900, 520)
        window.show()
        try:
            window._on_grid(8, 16, 64, 40)
            QApplication.processEvents()
            bar = window._side_scroll.verticalScrollBar()
            top = window._grids_box.mapTo(window._side_scroll.widget(), QPoint(0, 0)).y()
            self.assertEqual(bar.value(), min(top, bar.maximum()))
            self.assertIs(QApplication.focusWidget(), window.grid_cw)
            self.assertEqual(window.grid_cw.value(), 32)
        finally:
            window._dirty = False
            window.close()

    def test_rebuild_does_not_drift_the_canvas(self) -> None:
        window = MainWindow()
        window.resize(400, 300)
        window.show()
        try:
            window._pixmap = QPixmap(1800, 1400)
            window._pixmap.fill(QColor("#ffffff"))
            window._push_canvas()
            QApplication.processEvents()
            window.canvas.horizontalScrollBar().setValue(120)
            window.canvas.verticalScrollBar().setValue(80)
            horizontal = window.canvas.horizontalScrollBar().value()
            vertical = window.canvas.verticalScrollBar().value()
            window.canvas.rebuild()
            window.canvas.rebuild()
            self.assertEqual(window.canvas.horizontalScrollBar().value(), horizontal)
            self.assertEqual(window.canvas.verticalScrollBar().value(), vertical)
        finally:
            window.close()

    def test_rect_moves_focus_to_the_name_field(self) -> None:
        window = MainWindow()
        window.resize(900, 520)
        window.show()
        try:
            window._on_rect(4, 4, 32, 16)
            QApplication.processEvents()
            bar = window._side_scroll.verticalScrollBar()
            self.assertIs(QApplication.focusWidget(), window.name_edit)
            self.assertEqual(bar.value(), bar.maximum())
            self.assertGreater(bar.maximum(), 0)
            self.assertEqual(len(window.doc.tiles), 1)
        finally:
            window._dirty = False
            window.close()

    def test_view_menu_labels_tiles_and_grids(self) -> None:
        window = MainWindow()
        window.show()
        try:
            window.doc.add_tile(0, 0, 32, 16, "grass")
            window.doc.grids.append(Grid("digits", 0, 40, 8, 8, 4, 2))
            window.doc.tile_labels = "name"
            window.doc.grid_labels = "name"
            window._sync_view_menu()
            window._push_canvas()
            self.assertEqual(window._tile_label_actions["name"].isChecked(), True)
            self.assertIn("grass", self._captions(window))
            self.assertIn("digits", self._captions(window))
            window._tile_label_actions["id"].trigger()
            QApplication.processEvents()
            self.assertIn("0", self._captions(window))
            self.assertNotIn("grass", self._captions(window))
            window._tile_label_actions["none"].trigger()
            window._grid_label_actions["none"].trigger()
            QApplication.processEvents()
            self.assertEqual(self._captions(window), [])
        finally:
            window._dirty = False
            window.close()

    def _captions(self, window: MainWindow) -> list[str]:
        return [
            item.text()
            for item in window.canvas.scene().items()
            if isinstance(item, QGraphicsSimpleTextItem)
        ]

    def test_animation_order_is_written_into_custom_fields(self) -> None:
        window = MainWindow()
        window.show()
        try:
            first = window.doc.add_tile(0, 0, 8, 8, "a")
            second = window.doc.add_tile(8, 0, 8, 8, "b")
            window._recording_anim = True
            window._append_anim_frame(second.id)
            window._append_anim_frame(first.id)
            window._append_anim_frame(second.id)
            self.assertEqual(window._anim_draft, [second.id, first.id])
            window._apply_animation("walk")
            self.assertEqual(second.extra["animation_name"], "walk")
            self.assertEqual(second.extra["animation_id"], 0)
            self.assertEqual(first.extra["animation_name"], "walk")
            self.assertEqual(first.extra["animation_id"], 1)
            self.assertFalse(window._anim_box.isHidden())
            window._on_blink_tick()
            self.assertEqual(window._sequence_index, 1)
            window._strip_animation("walk")
            window._refresh_anim_panel()
            self.assertTrue(window._anim_box.isHidden())
        finally:
            window.blink_btn.setChecked(False)
            window._dirty = False
            window.close()

    def test_escape_deselects_named_tile_and_deletes_unnamed(self) -> None:
        window = MainWindow()
        window.show()
        try:
            named = window.doc.add_tile(0, 0, 8, 8, "grass")
            window._reload_tile_list()
            window._select(named.id, focus_name=True)
            self._press_escape(window.name_edit)
            self.assertEqual(window._selected_id, -1)
            self.assertEqual(window.doc.find(named.id).name, "grass")

            window._select(named.id)
            window.canvas.setFocus()
            self._press_escape(window.canvas)
            self.assertEqual(window._selected_id, -1)
            self.assertIsNotNone(window.doc.find(named.id))

            blank = window.doc.add_tile(8, 0, 8, 8, "  ")
            window._reload_tile_list()
            window._select(blank.id, focus_name=True)
            self._press_escape(window.name_edit)
            self.assertIsNone(window.doc.find(blank.id))
            self.assertEqual(window._selected_id, -1)
        finally:
            window._dirty = False
            window.close()

    def _press_escape(self, widget) -> None:
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(widget, event)


if __name__ == "__main__":
    unittest.main()
