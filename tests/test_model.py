"""Круговой JSON: массив, словарь, свои поля, сетки, чистый экспорт."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tilemapper.recent import forget, load_projects, load_recent, remember
from tilemapper.model import (
    Document,
    Grid,
    Tile,
    format_prop_value,
    inclusive_rect,
    parse_prop_value,
    snapped_rect,
    unique_name,
)


class ModelTest(unittest.TestCase):
    def test_array_roundtrip_keeps_custom_field(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "tileset.png"
            image.write_bytes(b"png")
            doc = Document.from_image(image)
            doc.nametileset = "ground"
            doc.tiles.append(Tile(0, "grass", 0, 0, 32, 32, {"any_if_use": 4}))
            doc.tiles.append(Tile(1, "stone", 32, 0, 32, 32))
            doc.grids.append(
                Grid("font", 0, 0, 8, 12, 16, 6, "#44aaff", True, 128, 72)
            )
            doc.extra_root["comment"] = "keep"
            path = root / "ground.json"
            doc.save(path, include_editor=True)

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["image"], "tileset.png")
            self.assertEqual(raw["nametileset"], "ground")
            self.assertEqual(raw["tiles"][0]["any_if_use"], 4)
            self.assertEqual(raw["tiles"][0]["name"], "grass")
            self.assertEqual(raw["comment"], "keep")
            self.assertEqual(raw["editor"]["grids"][0]["cell_w"], 8)

            loaded = Document.load(path)
            self.assertEqual(loaded.mode, "array")
            self.assertEqual(loaded.tiles[0].extra["any_if_use"], 4)
            self.assertEqual(loaded.tiles[1].name, "stone")
            self.assertEqual(loaded.grids[0].cols, 16)
            self.assertEqual(loaded.grids[0].color, "#44aaff")
            self.assertEqual(loaded.image_path, image.resolve())
            self.assertEqual(loaded.extra_root["comment"], "keep")

    def test_dict_roundtrip_name_is_key(self) -> None:
        sample = {
            "image": "UI.png",
            "nametileset": "ui",
            "tiles": {
                "pause": {"id": 3, "x": 400, "y": 520, "w": 48, "h": 48},
            },
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ui.json"
            path.write_text(json.dumps(sample), encoding="utf-8")
            loaded = Document.load(path)
            self.assertEqual(loaded.mode, "dict")
            self.assertEqual(loaded.tiles[0].name, "pause")
            self.assertEqual(loaded.tiles[0].id, 3)
            self.assertNotIn("name", loaded.tiles[0].extra)

            loaded.save(path, include_editor=True)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("name", raw["tiles"]["pause"])
            self.assertEqual(raw["tiles"]["pause"]["x"], 400)

    def test_export_strips_editor_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            doc = Document(image="a.png", mode="array")
            doc.nametileset = "ground"
            doc.tiles.append(Tile(0, "grass", 0, 0, 8, 8, {"any_if_use": 4}))
            doc.grids.append(Grid("g", 0, 0, 8, 8, 2, 2))
            doc.extra_root["pack"] = 1
            path = root / "out.json"
            doc.save(path, include_editor=False)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("editor", raw)
            self.assertEqual(raw["pack"], 1)
            self.assertEqual(raw["tiles"][0]["any_if_use"], 4)
            self.assertIsNone(doc.path)

    def test_dict_save_rejects_empty_and_duplicate_names(self) -> None:
        doc = Document(mode="dict")
        doc.tiles = [
            Tile(0, "", 0, 0, 1, 1),
            Tile(1, "stone", 1, 0, 1, 1),
            Tile(2, "stone", 2, 0, 1, 1),
        ]
        problems = doc.dict_problems()
        self.assertTrue(any("без имени" in item for item in problems))
        self.assertTrue(any("stone" in item for item in problems))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                doc.save(Path(folder) / "bad.json")

    def test_grid_refit_and_cells(self) -> None:
        grid = Grid("font", 10, 20, 32, 32, 1, 1, region_w=128, region_h=64)
        grid.set_cell_size(8, 8)
        self.assertEqual(grid.cols, 16)
        self.assertEqual(grid.rows, 8)
        self.assertEqual(grid.cell_rect(1, 0), (18, 20, 8, 8))
        self.assertEqual(grid.cell_index(18, 20), (1, 0))
        self.assertIsNone(grid.cell_index(9, 20))
        cells = grid.cells_between(0, 0, 1, 0)
        self.assertEqual(len(cells), 2)
        self.assertEqual(grid.next_cell(15, 0), (0, 1))
        self.assertIsNone(grid.next_cell(15, 7))
        grid.set_cols_rows(4, 2)
        self.assertEqual(grid.region_w, 32)
        self.assertEqual(grid.region_h, 16)

    def test_snap_and_inclusive_rect(self) -> None:
        self.assertEqual(inclusive_rect(0, 0, 31, 15), (0, 0, 32, 16))
        grid = Grid("g", 0, 0, 32, 32, 4, 4)
        self.assertEqual(snapped_rect(1, 1, 40, 50, grid), (0, 0, 32, 64))
        self.assertIsNone(snapped_rect(1, 1, 2, 2, grid))

    def test_parse_prop_value(self) -> None:
        self.assertEqual(parse_prop_value("4"), 4)
        self.assertEqual(parse_prop_value("4.5"), 4.5)
        self.assertEqual(parse_prop_value("true"), True)
        self.assertEqual(parse_prop_value("grass"), "grass")
        self.assertEqual(format_prop_value(True), "true")
        self.assertEqual(format_prop_value(4), "4")

    def test_unique_name_and_duplicate(self) -> None:
        self.assertEqual(unique_name(["grass"], "grass"), "grass_2")
        self.assertEqual(unique_name([], ""), "tile")
        doc = Document()
        tile = doc.add_tile(0, 0, 16, 8, "grass", {"any_if_use": 4})
        copy = doc.duplicate_tile(tile.id)
        self.assertIsNotNone(copy)
        assert copy is not None
        self.assertEqual(copy.name, "grass_2")
        self.assertEqual(copy.x, 16)
        self.assertEqual(copy.extra["any_if_use"], 4)
        copy.extra["any_if_use"] = 1
        self.assertEqual(tile.extra["any_if_use"], 4)

    def test_relative_image_across_folders(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "art" / "UI.png"
            image.parent.mkdir()
            image.write_bytes(b"png")
            doc = Document.from_image(image)
            doc.nametileset = "ui"
            path = root / "data" / "ui.json"
            doc.save(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["image"], "../art/UI.png")
            loaded = Document.load(path)
            self.assertEqual(loaded.image_path, image.resolve())

    def test_several_tilesets_roundtrip_and_single_stays_flat(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            doc = Document(image="UI.png", mode="array")
            doc.nametileset = "font"
            doc.tiles.append(Tile(0, "a", 0, 0, 8, 8))
            buttons = doc.add_tileset("buttons")
            buttons.tiles.append(Tile(0, "pause", 10, 10, 16, 16))
            doc.tilesets[0].visible = False
            path = root / "ui.json"
            doc.save(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("nametileset", raw)
            self.assertEqual([item["nametileset"] for item in raw["tilesets"]], ["font", "buttons"])
            self.assertEqual(raw["editor"]["visible"], [False, True])
            self.assertEqual(raw["editor"]["active"], 1)

            loaded = Document.load(path)
            self.assertEqual(loaded.active_index, 1)
            self.assertFalse(loaded.tilesets[0].visible)
            self.assertEqual(loaded.tilesets[1].tiles[0].name, "pause")
            self.assertEqual(loaded.nametileset, "buttons")
            self.assertTrue(loaded.remove_tileset(0))
            self.assertEqual(len(loaded.tilesets), 1)
            self.assertFalse(loaded.remove_tileset(0))
            flat = root / "flat.json"
            loaded.save(flat)
            flat_raw = json.loads(flat.read_text(encoding="utf-8"))
            self.assertEqual(flat_raw["nametileset"], "buttons")
            self.assertIn("tiles", flat_raw)
            self.assertNotIn("tilesets", flat_raw)

    def test_user_array_sample(self) -> None:
        sample = {
            "image": "tileset.png",
            "nametileset": "ground",
            "tiles": [
                {"id": 0, "name": "grass", "x": 0, "y": 0, "w": 32, "h": 32, "any_if_use": 4},
                {"id": 1, "name": "stone", "x": 32, "y": 0, "w": 32, "h": 32},
            ],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ground.json"
            path.write_text(json.dumps(sample), encoding="utf-8")
            loaded = Document.load(path)
            self.assertEqual([tile.name for tile in loaded.tiles], ["grass", "stone"])
            self.assertEqual(loaded.tiles[0].extra["any_if_use"], 4)
            self.assertEqual(loaded.nametileset, "ground")

    def test_label_modes_stay_in_editor_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            doc = Document(image="UI.png")
            doc.tile_labels = "name"
            doc.grid_labels = "name"
            path = root / "ui.json"
            doc.save(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["editor"]["tile_labels"], "name")
            self.assertEqual(raw["editor"]["grid_labels"], "name")
            loaded = Document.load(path)
            self.assertEqual(loaded.tile_labels, "name")
            self.assertEqual(loaded.grid_labels, "name")
            clean = root / "clean.json"
            doc.save(clean, include_editor=False)
            exported = json.loads(clean.read_text(encoding="utf-8"))
            self.assertNotIn("editor", exported)
            bogus = dict(raw)
            bogus["editor"] = {"tile_labels": "nope", "grid_labels": 3}
            path.write_text(json.dumps(bogus), encoding="utf-8")
            fallback = Document.load(path)
            self.assertEqual(fallback.tile_labels, "none")
            self.assertEqual(fallback.grid_labels, "none")

    def test_recent_keeps_opened_paths(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = root / "recent.json"
            first = root / "a.png"
            second = root / "b.json"
            first.write_bytes(b"x")
            second.write_text("{}", encoding="utf-8")
            remember(first, store)
            remember(second, store)
            remember(first, store)
            self.assertEqual(load_projects(store), [str(second.resolve())])
            third = root / "c.json"
            third.write_text("{}", encoding="utf-8")
            remember(third, store)
            recent = load_recent(store)
            self.assertEqual(recent[0], str(third.resolve()))
            self.assertEqual(recent[1], str(second.resolve()))
            self.assertNotIn(str(first.resolve()), recent)
            forget(second, store)
            self.assertEqual(load_recent(store), [str(third.resolve())])


if __name__ == "__main__":
    unittest.main()
