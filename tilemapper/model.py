"""Документ тайлсета: тайлы, временные сетки, JSON массивом или словарём."""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT_FIELDS = ("image", "nametileset", "tiles", "tilesets", "editor")
TILE_FIELDS = ("id", "name", "x", "y", "w", "h")
GRID_COLORS = ("#3d8bfd", "#e85d4c", "#3cba7a", "#e6a23c", "#a06cd5", "#2bbbad")

_INT_RE = re.compile(r"-?\d+")
_FLOAT_RE = re.compile(r"-?\d+\.\d+")


def parse_prop_value(text: str) -> Any:
    """Число остаётся числом, true/false/null и JSON — как в JSON, остальное строка."""
    stripped = text.strip()
    if stripped == "":
        return ""
    if stripped in ("true", "false", "null"):
        return json.loads(stripped)
    if stripped[0] in "[{":
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    if _INT_RE.fullmatch(stripped):
        return int(stripped)
    if _FLOAT_RE.fullmatch(stripped):
        return float(stripped)
    return text


def format_prop_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False)


def unique_name(names: list[str], base: str) -> str:
    base = base.strip() or "tile"
    taken = set(names)
    if base not in taken:
        return base
    index = 2
    while f"{base}_{index}" in taken:
        index += 1
    return f"{base}_{index}"


def relative_image(json_path: Path, image_path: Path) -> str:
    try:
        return Path(os.path.relpath(image_path.resolve(), json_path.resolve().parent)).as_posix()
    except ValueError:
        return str(image_path)


def resolve_image(json_path: Path, image_field: str) -> Path:
    path = Path(image_field)
    if path.is_absolute():
        return path
    return (json_path.parent / path).resolve()


def _choice(value: Any, allowed: set[str], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def inclusive_rect(x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
    x, y = min(x0, x1), min(y0, y1)
    return x, y, abs(x1 - x0) + 1, abs(y1 - y0) + 1


@dataclass
class Tile:
    id: int
    name: str
    x: int
    y: int
    w: int
    h: int
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self, *, include_name: bool) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id}
        if include_name:
            data["name"] = self.name
        data["x"] = self.x
        data["y"] = self.y
        data["w"] = self.w
        data["h"] = self.h
        for key, value in self.extra.items():
            if key not in TILE_FIELDS:
                data[key] = value
        return data


@dataclass
class Grid:
    name: str
    x: int
    y: int
    cell_w: int
    cell_h: int
    cols: int
    rows: int
    color: str = "#3d8bfd"
    visible: bool = True
    region_w: int = 0
    region_h: int = 0

    def __post_init__(self) -> None:
        self.cell_w = max(1, int(self.cell_w))
        self.cell_h = max(1, int(self.cell_h))
        self.cols = max(1, int(self.cols))
        self.rows = max(1, int(self.rows))
        if self.region_w <= 0:
            self.region_w = self.cols * self.cell_w
        if self.region_h <= 0:
            self.region_h = self.rows * self.cell_h

    def set_cell_size(self, cell_w: int, cell_h: int) -> None:
        self.cell_w = max(1, int(cell_w))
        self.cell_h = max(1, int(cell_h))
        self.cols = max(1, self.region_w // self.cell_w)
        self.rows = max(1, self.region_h // self.cell_h)

    def set_cols_rows(self, cols: int, rows: int) -> None:
        self.cols = max(1, int(cols))
        self.rows = max(1, int(rows))
        self.region_w = self.cols * self.cell_w
        self.region_h = self.rows * self.cell_h

    def snap_point(self, x: int, y: int) -> tuple[int, int]:
        col = round((x - self.x) / self.cell_w)
        row = round((y - self.y) / self.cell_h)
        return self.x + col * self.cell_w, self.y + row * self.cell_h

    def cell_index(self, px: int, py: int) -> tuple[int, int] | None:
        if px < self.x or py < self.y:
            return None
        col = (px - self.x) // self.cell_w
        row = (py - self.y) // self.cell_h
        if col < 0 or row < 0 or col >= self.cols or row >= self.rows:
            return None
        if px >= self.x + col * self.cell_w + self.cell_w:
            return None
        if py >= self.y + row * self.cell_h + self.cell_h:
            return None
        return int(col), int(row)

    def cell_rect(self, col: int, row: int) -> tuple[int, int, int, int]:
        return (
            self.x + col * self.cell_w,
            self.y + row * self.cell_h,
            self.cell_w,
            self.cell_h,
        )

    def cells_between(
        self, c0: int, r0: int, c1: int, r1: int
    ) -> list[tuple[int, int, int, int]]:
        ca, cb = sorted((c0, c1))
        ra, rb = sorted((r0, r1))
        cells: list[tuple[int, int, int, int]] = []
        for row in range(ra, rb + 1):
            for col in range(ca, cb + 1):
                cells.append(self.cell_rect(col, row))
        return cells

    def next_cell(self, col: int, row: int) -> tuple[int, int] | None:
        col += 1
        if col >= self.cols:
            col = 0
            row += 1
        if row >= self.rows:
            return None
        return col, row

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "cell_w": self.cell_w,
            "cell_h": self.cell_h,
            "cols": self.cols,
            "rows": self.rows,
            "color": self.color,
            "visible": self.visible,
            "region_w": self.region_w,
            "region_h": self.region_h,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Grid:
        return cls(
            name=str(data.get("name") or "Сетка"),
            x=int(data.get("x", 0)),
            y=int(data.get("y", 0)),
            cell_w=int(data.get("cell_w", 32)),
            cell_h=int(data.get("cell_h", 32)),
            cols=int(data.get("cols", 1)),
            rows=int(data.get("rows", 1)),
            color=str(data.get("color") or "#3d8bfd"),
            visible=bool(data.get("visible", True)),
            region_w=int(data.get("region_w", 0)),
            region_h=int(data.get("region_h", 0)),
        )


def snapped_rect(
    x0: int, y0: int, x1: int, y1: int, grid: Grid
) -> tuple[int, int, int, int] | None:
    ax, ay = grid.snap_point(x0, y0)
    bx, by = grid.snap_point(x1, y1)
    x, y = min(ax, bx), min(ay, by)
    w, h = abs(bx - ax), abs(by - ay)
    if w < 1 or h < 1:
        return None
    return x, y, w, h


@dataclass
class Tileset:
    """Именованный набор тайлов на общей картинке. Прячется, как слой."""

    name: str = "tiles"
    tiles: list[Tile] = field(default_factory=list)
    visible: bool = True


@dataclass
class Document:
    image: str = ""
    tilesets: list[Tileset] = field(default_factory=list)
    active_index: int = 0
    mode: str = "array"
    grids: list[Grid] = field(default_factory=list)
    zoom: float | None = None
    tile_labels: str = "none"
    grid_labels: str = "none"
    path: Path | None = None
    image_path: Path | None = None
    extra_root: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tilesets:
            self.tilesets.append(Tileset())

    @property
    def nametileset(self) -> str:
        return self.active_set().name

    @nametileset.setter
    def nametileset(self, value: str) -> None:
        self.active_set().name = value

    @property
    def tiles(self) -> list[Tile]:
        return self.active_set().tiles

    @tiles.setter
    def tiles(self, value: list[Tile]) -> None:
        self.active_set().tiles = list(value)

    def active_set(self) -> Tileset:
        if not self.tilesets:
            self.tilesets.append(Tileset())
        self.active_index = max(0, min(self.active_index, len(self.tilesets) - 1))
        return self.tilesets[self.active_index]

    def add_tileset(self, name: str = "") -> Tileset:
        base = name.strip() or f"Набор {len(self.tilesets) + 1}"
        tileset = Tileset(unique_name([item.name for item in self.tilesets], base))
        self.tilesets.append(tileset)
        self.active_index = len(self.tilesets) - 1
        return tileset

    def remove_tileset(self, index: int) -> bool:
        if len(self.tilesets) <= 1 or not 0 <= index < len(self.tilesets):
            return False
        del self.tilesets[index]
        if self.active_index >= len(self.tilesets):
            self.active_index = len(self.tilesets) - 1
        elif self.active_index > index:
            self.active_index -= 1
        return True

    def background_tiles(self) -> list[tuple[int, Tile]]:
        rows: list[tuple[int, Tile]] = []
        for index, tileset in enumerate(self.tilesets):
            if index == self.active_index or not tileset.visible:
                continue
            for tile in tileset.tiles:
                rows.append((index, tile))
        return rows

    @classmethod
    def from_image(cls, image_path: Path) -> Document:
        image_path = Path(image_path)
        document = cls(image=image_path.name, image_path=image_path)
        document.nametileset = image_path.stem
        return document

    def find(self, tile_id: int) -> Tile | None:
        for tile in self.tiles:
            if tile.id == tile_id:
                return tile
        return None

    def tile_at(self, x: int, y: int, w: int, h: int) -> Tile | None:
        for tile in self.tiles:
            if tile.x == x and tile.y == y and tile.w == w and tile.h == h:
                return tile
        return None

    def next_id(self) -> int:
        if not self.tiles:
            return 0
        return max(tile.id for tile in self.tiles) + 1

    def add_tile(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        name: str = "",
        extra: dict[str, Any] | None = None,
    ) -> Tile:
        self.active_set().visible = True
        tile = Tile(
            self.next_id(),
            name,
            int(x),
            int(y),
            int(w),
            int(h),
            copy.deepcopy(extra) if extra else {},
        )
        self.tiles.append(tile)
        return tile

    def remove_tile(self, tile_id: int) -> None:
        self.tiles = [tile for tile in self.tiles if tile.id != tile_id]

    def duplicate_tile(self, tile_id: int) -> Tile | None:
        source = self.find(tile_id)
        if source is None:
            return None
        name = unique_name([tile.name for tile in self.tiles], source.name)
        return self.add_tile(
            source.x + source.w,
            source.y,
            source.w,
            source.h,
            name,
            source.extra,
        )

    def dict_problems(self) -> list[str]:
        problems: list[str] = []
        several = len(self.tilesets) > 1
        for tileset in self.tilesets:
            seen: dict[str, int] = {}
            prefix = f"«{tileset.name}»: " if several else ""
            for tile in tileset.tiles:
                name = tile.name.strip()
                if not name:
                    problems.append(f"{prefix}Тайл {tile.id} без имени")
                    continue
                if name in seen:
                    problems.append(
                        f"{prefix}Имя «{name}» повторяется (id {seen[name]} и {tile.id})"
                    )
                else:
                    seen[name] = tile.id
        return problems

    def image_field_for(self, json_path: Path) -> str:
        if self.image_path is not None:
            return relative_image(json_path, self.image_path)
        return self.image

    def _tiles_payload(self, tiles: list[Tile]) -> Any:
        if self.mode == "dict":
            return {tile.name.strip(): tile.to_json(include_name=False) for tile in tiles}
        return [tile.to_json(include_name=True) for tile in tiles]

    def to_data(self, image_field: str, *, include_editor: bool) -> dict[str, Any]:
        data: dict[str, Any] = {"image": image_field}
        if len(self.tilesets) == 1:
            data["nametileset"] = self.tilesets[0].name
            data["tiles"] = self._tiles_payload(self.tilesets[0].tiles)
        else:
            data["tilesets"] = [
                {"nametileset": tileset.name, "tiles": self._tiles_payload(tileset.tiles)}
                for tileset in self.tilesets
            ]
        for key, value in self.extra_root.items():
            if key not in ROOT_FIELDS:
                data[key] = value
        if include_editor:
            data["editor"] = {
                "tiles_mode": self.mode,
                "zoom": self.zoom,
                "active": self.active_index,
                "visible": [tileset.visible for tileset in self.tilesets],
                "grids": [grid.to_json() for grid in self.grids],
                "tile_labels": self.tile_labels,
                "grid_labels": self.grid_labels,
            }
        return data

    def save(self, path: Path, *, include_editor: bool = True) -> None:
        if self.mode == "dict":
            for tileset in self.tilesets:
                for tile in tileset.tiles:
                    tile.name = tile.name.strip()
            problems = self.dict_problems()
            if problems:
                raise ValueError("\n".join(problems))
        path = Path(path)
        image_field = self.image_field_for(path)
        payload = self.to_data(image_field, include_editor=include_editor)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if include_editor:
            self.path = path
            self.image = image_field

    @classmethod
    def load(cls, path: Path) -> Document:
        path = Path(path)
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("Корень JSON должен быть объектом")
        editor = raw.get("editor") or {}
        if not isinstance(editor, dict):
            editor = {}
        tilesets, mode = _load_tilesets(raw)
        visible = editor.get("visible")
        if isinstance(visible, list):
            for tileset, flag in zip(tilesets, visible):
                tileset.visible = bool(flag)
        grids = [
            Grid.from_json(item)
            for item in editor.get("grids", [])
            if isinstance(item, dict)
        ]
        zoom = editor.get("zoom", None)
        if zoom is not None:
            zoom = float(zoom)
        active = editor.get("active", 0)
        try:
            active_index = int(active)
        except (TypeError, ValueError):
            active_index = 0
        tile_labels = _choice(editor.get("tile_labels"), {"none", "id", "name"}, "none")
        grid_labels = _choice(editor.get("grid_labels"), {"none", "name"}, "none")
        image_field = str(raw.get("image", ""))
        extra_root = {key: value for key, value in raw.items() if key not in ROOT_FIELDS}
        return cls(
            image=image_field,
            tilesets=tilesets,
            active_index=active_index,
            mode=mode,
            grids=grids,
            zoom=zoom,
            tile_labels=tile_labels,
            grid_labels=grid_labels,
            path=path,
            image_path=resolve_image(path, image_field) if image_field else None,
            extra_root=extra_root,
        )


def _load_tilesets(raw: dict[str, Any]) -> tuple[list[Tileset], str]:
    packed = raw.get("tilesets")
    if isinstance(packed, list) and packed:
        tilesets: list[Tileset] = []
        mode = "array"
        for index, item in enumerate(packed):
            if not isinstance(item, dict):
                raise ValueError(f"Набор #{index} должен быть объектом")
            tiles_raw = item.get("tiles", [])
            item_mode = "dict" if isinstance(tiles_raw, dict) else "array" if isinstance(tiles_raw, list) else None
            if item_mode is None:
                raise ValueError(f"У набора #{index} поле tiles должно быть массивом или объектом")
            if index == 0:
                mode = item_mode
            tilesets.append(
                Tileset(
                    name=str(item.get("nametileset") or item.get("name") or f"Набор {index + 1}"),
                    tiles=_load_tiles(tiles_raw, item_mode),
                )
            )
        return tilesets, mode
    tiles_raw = raw.get("tiles", [])
    if isinstance(tiles_raw, dict):
        mode = "dict"
    elif isinstance(tiles_raw, list):
        mode = "array"
    else:
        raise ValueError("Поле tiles должно быть массивом или объектом")
    return [Tileset(name=str(raw.get("nametileset", "")), tiles=_load_tiles(tiles_raw, mode))], mode


def _load_tiles(tiles_raw: Any, mode: str) -> list[Tile]:
    tiles: list[Tile] = []
    if mode == "dict":
        for name, obj in tiles_raw.items():
            if not isinstance(obj, dict):
                raise ValueError(f"Тайл «{name}» должен быть объектом")
            tiles.append(_tile_from_mapping(obj, str(name), len(tiles)))
        return tiles
    for index, obj in enumerate(tiles_raw):
        if not isinstance(obj, dict):
            raise ValueError(f"Тайл #{index} должен быть объектом")
        tiles.append(_tile_from_mapping(obj, None, index))
    return tiles


def _tile_from_mapping(obj: dict[str, Any], name: str | None, fallback_id: int) -> Tile:
    missing = [key for key in ("x", "y", "w", "h") if key not in obj]
    if missing:
        raise ValueError("У тайла нет полей: " + ", ".join(missing))
    width = int(obj["w"])
    height = int(obj["h"])
    if width < 1 or height < 1:
        raise ValueError("Ширина и высота тайла должны быть больше нуля")
    extra = {key: value for key, value in obj.items() if key not in TILE_FIELDS}
    tile_name = name if name is not None else str(obj.get("name", ""))
    tile_id = int(obj.get("id", fallback_id))
    return Tile(
        tile_id,
        tile_name,
        int(obj["x"]),
        int(obj["y"]),
        width,
        height,
        extra,
    )
