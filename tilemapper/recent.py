"""Список всего, что открывали: картинки и JSON."""

from __future__ import annotations

import json
import os
from pathlib import Path

_LIMIT = 200


def store_path() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home()
    return root / "TileMapper" / "recent.json"


def load_recent(store: Path | None = None) -> list[str]:
    path = store or store_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item]


def remember(path: Path, store: Path | None = None) -> list[str]:
    target = store or store_path()
    key = _key(Path(path))
    items = [item for item in load_recent(target) if _key(Path(item)) != key]
    items.insert(0, key)
    del items[_LIMIT:]
    _write(target, items)
    return items


def forget(path: Path, store: Path | None = None) -> list[str]:
    target = store or store_path()
    key = _key(Path(path))
    items = [item for item in load_recent(target) if _key(Path(item)) != key]
    _write(target, items)
    return items


def _key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _write(path: Path, items: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
