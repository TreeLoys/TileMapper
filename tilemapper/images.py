"""Картинка для холста: обычные форматы и .aseprite."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage, QPixmap

from tilemapper.aseprite import AsepriteError, decode_aseprite

IMAGE_SUFFIXES = {".png", ".bmp", ".gif", ".jpg", ".jpeg", ".webp", ".aseprite", ".ase"}
IMAGE_FILTER = "Изображения (*.png *.bmp *.gif *.jpg *.jpeg *.webp *.aseprite *.ase)"


def load_pixmap(path: Path) -> tuple[QPixmap, str]:
    suffix = path.suffix.lower()
    if suffix in {".aseprite", ".ase"}:
        image = decode_aseprite(path.read_bytes())
        qimage = _qimage_from_rgba(image.rgba, image.width, image.height)
        note = ""
        if image.frames > 1:
            note = f"Показан первый кадр из {image.frames}"
        return QPixmap.fromImage(qimage), note
    return QPixmap(str(path)), ""


def _qimage_from_rgba(rgba: bytes, width: int, height: int) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    buffer = image.bits()
    stride = image.bytesPerLine()
    row = width * 4
    for y in range(height):
        start = y * stride
        buffer[start : start + row] = rgba[y * row : (y + 1) * row]
    return image


__all__ = ["AsepriteError", "IMAGE_FILTER", "IMAGE_SUFFIXES", "load_pixmap"]
