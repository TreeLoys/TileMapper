"""Чтение .aseprite: плоская картинка первого кадра, без установленного Aseprite."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

ASE_MAGIC = 0xA5E0
FRAME_MAGIC = 0xF1FA
CHUNK_LAYER = 0x2004
CHUNK_CEL = 0x2005
CHUNK_PALETTE = 0x2019
LAYER_VISIBLE = 1
LAYER_REFERENCE = 64
LAYER_GROUP = 1


class AsepriteError(ValueError):
    pass


@dataclass
class AseImage:
    width: int
    height: int
    rgba: bytes
    frames: int


def decode_aseprite(data: bytes) -> AseImage:
    if len(data) < 128:
        raise AsepriteError("Файл Aseprite слишком короткий")
    _file_size, magic, frames, width, height, depth, header_flags = struct.unpack_from("<IHHHHHI", data, 0)
    if magic != ASE_MAGIC:
        raise AsepriteError("Это не файл Aseprite")
    if frames < 1 or width < 1 or height < 1:
        raise AsepriteError("В файле Aseprite пустой холст")
    if depth not in (8, 16, 32):
        raise AsepriteError(f"Глубина цвета {depth} не поддерживается")
    transparent = data[28]
    palette = [(0, 0, 0, 0)] * 256
    palette[0] = (0, 0, 0, 0)
    layers: list[_Layer] = []
    cels: dict[tuple[int, int], _Cel] = {}
    offset = 128
    for frame_index in range(frames):
        offset = _read_frame(
            data,
            offset,
            frame_index,
            depth,
            palette,
            transparent,
            bool(header_flags & 1),
            layers,
            cels,
        )
    shown = _apply_group_visibility(layers)
    canvas = bytearray(width * height * 4)
    for layer_index, layer in enumerate(layers):
        if layer_index >= len(shown) or not shown[layer_index] or layer.kind == LAYER_GROUP:
            continue
        cel = _resolve_cel(cels, 0, layer_index)
        if cel is None:
            continue
        opacity = layer.opacity * cel.opacity // 255
        _blit(canvas, width, height, cel.rgba, cel.x, cel.y, cel.w, cel.h, opacity)
    return AseImage(width, height, bytes(canvas), frames)


@dataclass
class _Layer:
    flags: int
    kind: int
    level: int
    opacity: int


@dataclass
class _Cel:
    x: int
    y: int
    w: int
    h: int
    opacity: int
    rgba: bytes
    link: int | None = None


def _read_frame(
    data: bytes,
    offset: int,
    frame_index: int,
    depth: int,
    palette: list[tuple[int, int, int, int]],
    transparent: int,
    opacity_valid: bool,
    layers: list[_Layer],
    cels: dict[tuple[int, int], _Cel],
) -> int:
    if offset + 16 > len(data):
        raise AsepriteError("Файл Aseprite обрезан на кадре")
    frame_size, magic, old_chunks, _duration = struct.unpack_from("<IHHH", data, offset)
    if magic != FRAME_MAGIC:
        raise AsepriteError("Повреждён кадр Aseprite")
    new_chunks = struct.unpack_from("<I", data, offset + 12)[0]
    chunk_count = new_chunks or old_chunks
    cursor = offset + 16
    end = offset + frame_size if frame_size else len(data)
    for _ in range(chunk_count):
        if cursor + 6 > len(data):
            break
        chunk_size, chunk_type = struct.unpack_from("<IH", data, cursor)
        if chunk_size < 6:
            raise AsepriteError("Повреждён блок Aseprite")
        payload = data[cursor + 6 : cursor + chunk_size]
        if chunk_type == CHUNK_LAYER:
            layers.append(_parse_layer(payload, opacity_valid=opacity_valid))
        elif chunk_type == CHUNK_CEL:
            layer_index, cel = _parse_cel(payload, depth, palette, transparent)
            if cel is not None:
                cels[(frame_index, layer_index)] = cel
        elif chunk_type == CHUNK_PALETTE:
            _parse_palette(payload, palette)
        cursor += chunk_size
        if frame_size and cursor >= end:
            break
    return offset + frame_size if frame_size else cursor


def _parse_layer(payload: bytes, *, opacity_valid: bool) -> _Layer:
    start = _layer_name_offset(payload)
    if start == 16:
        flags, kind, level, _width, _height, _blend, opacity = struct.unpack_from("<HHHHHHB", payload, 0)
    elif start == 12:
        flags, kind, level, _blend, opacity = struct.unpack_from("<HHHHB", payload, 0)
    else:
        raise AsepriteError("Слой Aseprite обрезан")
    if not opacity_valid:
        opacity = 255
    return _Layer(flags, kind, level, opacity)


def _layer_name_offset(payload: bytes) -> int:
    """16 байт у текущих файлов (перед режимом смешивания есть ширина и высота), 12 у старых."""
    for start in (16, 12):
        if start + 2 > len(payload):
            continue
        length = struct.unpack_from("<H", payload, start)[0]
        end = start + 2 + length
        if end > len(payload):
            continue
        if len(payload) - end in (0, 4, 16, 20):
            return start
    return 0


def _parse_cel(
    payload: bytes,
    depth: int,
    palette: list[tuple[int, int, int, int]],
    transparent: int,
) -> tuple[int, _Cel | None]:
    if len(payload) < 16:
        raise AsepriteError("Ячейка Aseprite обрезана")
    layer_index, x, y, opacity, cel_type = struct.unpack_from("<HhhBH", payload, 0)
    body = payload[16:]
    if cel_type == 1:
        if len(body) < 2:
            raise AsepriteError("Ссылка на кадр Aseprite обрезана")
        link = struct.unpack_from("<H", body, 0)[0]
        return layer_index, _Cel(x, y, 0, 0, opacity, b"", link)
    if cel_type == 3:
        return layer_index, None
    if cel_type not in (0, 2):
        return layer_index, None
    if len(body) < 4:
        raise AsepriteError("В ячейке Aseprite нет картинки")
    cel_w, cel_h = struct.unpack_from("<HH", body, 0)
    raw = body[4:]
    if cel_type == 2:
        try:
            raw = zlib.decompress(raw)
        except zlib.error as exc:
            raise AsepriteError("Не удалось распаковать кадр Aseprite") from exc
    rgba = _to_rgba(raw, depth, cel_w, cel_h, palette, transparent)
    return layer_index, _Cel(x, y, cel_w, cel_h, opacity, rgba)


def _parse_palette(payload: bytes, palette: list[tuple[int, int, int, int]]) -> None:
    if len(payload) < 20:
        return
    _size, first, last = struct.unpack_from("<III", payload, 0)
    cursor = 20
    for index in range(first, last + 1):
        if cursor + 6 > len(payload) or index >= len(palette):
            break
        flags, red, green, blue, alpha = struct.unpack_from("<HBBBB", payload, cursor)
        cursor += 6
        palette[index] = (red, green, blue, alpha)
        if flags & 1:
            if cursor + 2 > len(payload):
                break
            length = struct.unpack_from("<H", payload, cursor)[0]
            cursor += 2 + length


def _to_rgba(
    raw: bytes,
    depth: int,
    width: int,
    height: int,
    palette: list[tuple[int, int, int, int]],
    transparent: int,
) -> bytes:
    count = width * height
    out = bytearray(count * 4)
    if depth == 32:
        need = count * 4
        if len(raw) < need:
            raise AsepriteError("В кадре Aseprite не хватает пикселей")
        out[:] = raw[:need]
        return bytes(out)
    if depth == 16:
        need = count * 2
        if len(raw) < need:
            raise AsepriteError("В кадре Aseprite не хватает пикселей")
        for index in range(count):
            value, alpha = raw[index * 2], raw[index * 2 + 1]
            out[index * 4 : index * 4 + 4] = bytes((value, value, value, alpha))
        return bytes(out)
    if len(raw) < count:
        raise AsepriteError("В кадре Aseprite не хватает пикселей")
    for index in range(count):
        color_index = raw[index]
        if color_index == transparent:
            continue
        red, green, blue, alpha = palette[color_index]
        out[index * 4 : index * 4 + 4] = bytes((red, green, blue, alpha))
    return bytes(out)


def _apply_group_visibility(layers: list[_Layer]) -> list[bool]:
    stack: list[tuple[int, bool]] = []
    shown: list[bool] = []
    for layer in layers:
        while stack and stack[-1][0] >= layer.level:
            stack.pop()
        parent_ok = all(flag for _level, flag in stack) if stack else True
        visible = parent_ok and bool(layer.flags & LAYER_VISIBLE) and not bool(layer.flags & LAYER_REFERENCE)
        shown.append(visible)
        stack.append((layer.level, visible if layer.kind == LAYER_GROUP else parent_ok))
    return shown


def _resolve_cel(
    cels: dict[tuple[int, int], _Cel], frame: int, layer: int, seen: set[tuple[int, int]] | None = None
) -> _Cel | None:
    seen = seen or set()
    key = (frame, layer)
    if key in seen:
        return None
    cel = cels.get(key)
    if cel is None or cel.link is None:
        return cel
    seen.add(key)
    source = _resolve_cel(cels, cel.link, layer, seen)
    if source is None:
        return None
    return _Cel(cel.x, cel.y, source.w, source.h, cel.opacity, source.rgba)


def _blit(
    canvas: bytearray,
    canvas_w: int,
    canvas_h: int,
    rgba: bytes,
    origin_x: int,
    origin_y: int,
    width: int,
    height: int,
    opacity: int,
) -> None:
    if opacity <= 0 or width < 1 or height < 1:
        return
    for row in range(height):
        target_y = origin_y + row
        if target_y < 0 or target_y >= canvas_h:
            continue
        for col in range(width):
            target_x = origin_x + col
            if target_x < 0 or target_x >= canvas_w:
                continue
            source = (row * width + col) * 4
            red, green, blue, alpha = rgba[source : source + 4]
            alpha = alpha * opacity // 255
            if alpha <= 0:
                continue
            dest = (target_y * canvas_w + target_x) * 4
            dest_r, dest_g, dest_b, dest_a = canvas[dest : dest + 4]
            inverse = 255 - alpha
            out_a = alpha + dest_a * inverse // 255
            if out_a <= 0:
                continue
            canvas[dest] = (red * alpha + dest_r * dest_a * inverse // 255) // out_a
            canvas[dest + 1] = (green * alpha + dest_g * dest_a * inverse // 255) // out_a
            canvas[dest + 2] = (blue * alpha + dest_b * dest_a * inverse // 255) // out_a
            canvas[dest + 3] = min(255, out_a)
