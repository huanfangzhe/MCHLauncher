"""从 Minecraft 皮肤里裁出「头颅」部分，用于账号头像。

只用标准库：zlib 解压 + 手写 PNG 反滤波，避免为了一张小图引入 Pillow。
皮肤布局（64x32 与 64x64 通用）：
    基础头部 (8,8)-(16,16)，帽子/外层 (40,8)-(48,16)
合成时把帽子层按 alpha 叠在基础层上，再最近邻放大到目标尺寸。
"""

from __future__ import annotations

import struct
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"


class SkinError(ValueError):
    """皮肤图片无法解析。"""


def _unfilter(raw: bytes, width: int, height: int, channels: int) -> bytearray:
    """按 PNG 规范还原扫描线，返回去掉过滤字节后的像素数据。"""
    stride = width * channels
    out = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0

    for y in range(height):
        if pos >= len(raw):
            raise SkinError("皮肤数据不完整")
        filter_type = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        if len(line) < stride:
            raise SkinError("皮肤数据不完整")
        pos += stride

        if filter_type == 1:  # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                if pa <= pb and pa <= pc:
                    pred = a
                elif pb <= pc:
                    pred = b
                else:
                    pred = c
                line[i] = (line[i] + pred) & 0xFF
        elif filter_type != 0:
            raise SkinError(f"不支持的 PNG 过滤类型：{filter_type}")

        out[y * stride:(y + 1) * stride] = line
        prev = line

    return out


def decode_png(data: bytes) -> tuple[int, int, bytearray]:
    """解码 8 位非隔行 PNG，返回 (宽, 高, RGBA 像素)。"""
    if not data.startswith(PNG_SIG):
        raise SkinError("不是 PNG 文件")

    pos = len(PNG_SIG)
    width = height = 0
    color_type = 6
    idat = bytearray()

    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", body
            )
            if depth != 8 or interlace != 0 or color_type not in (2, 6):
                raise SkinError("只支持 8 位、非隔行的 RGB/RGBA PNG")
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break

    if width <= 0 or height <= 0 or not idat:
        raise SkinError("PNG 缺少图像数据")

    channels = 4 if color_type == 6 else 3
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise SkinError(f"PNG 数据损坏：{exc}") from None

    pixels = _unfilter(raw, width, height, channels)
    if channels == 4:
        return width, height, pixels

    rgba = bytearray(width * height * 4)
    for index in range(width * height):
        src = index * 3
        dst = index * 4
        rgba[dst:dst + 3] = pixels[src:src + 3]
        rgba[dst + 3] = 255
    return width, height, rgba


def encode_png(width: int, height: int, rgba: bytes) -> bytes:
    """把 RGBA 像素编码成 PNG（过滤类型统一用 0）。"""
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + tag
            + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
        )

    return (
        PNG_SIG
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _pixel_at(pixels: bytearray, width: int, height: int, x: int, y: int) -> tuple:
    if x < 0 or y < 0 or x >= width or y >= height:
        return (0, 0, 0, 0)
    index = (y * width + x) * 4
    return tuple(pixels[index:index + 4])


def _over(dst: tuple, src: tuple) -> tuple:
    """src 叠在 dst 之上（普通 alpha 混合）。"""
    sa = src[3]
    if sa == 0:
        return dst
    if sa == 255:
        return src
    da = dst[3]
    out_a = sa + da * (255 - sa) // 255
    if out_a == 0:
        return (0, 0, 0, 0)
    keep = da * (255 - sa) // 255
    return (
        (src[0] * sa + dst[0] * keep) // out_a,
        (src[1] * sa + dst[1] * keep) // out_a,
        (src[2] * sa + dst[2] * keep) // out_a,
        out_a,
    )


def head_from_skin(data: bytes, size: int = 64) -> bytes:
    """从皮肤 PNG 裁出头部头像，返回新的 PNG 字节。"""
    width, height, pixels = decode_png(data)
    if width < 64 or height < 32:
        raise SkinError("皮肤尺寸不正确（至少 64x32）")

    # 高清皮肤（128x128 等）按倍数取样
    factor = max(1, width // 64)
    head = bytearray(8 * 8 * 4)

    for y in range(8):
        for x in range(8):
            base = _pixel_at(pixels, width, height, (8 + x) * factor, (8 + y) * factor)
            hat = _pixel_at(pixels, width, height, (40 + x) * factor, (8 + y) * factor)
            color = _over(base, hat)
            index = (y * 8 + x) * 4
            head[index:index + 4] = bytes(color)

    # 最近邻放大，保持像素风格
    out = bytearray(size * size * 4)
    for y in range(size):
        sy = min(7, y * 8 // size)
        for x in range(size):
            sx = min(7, x * 8 // size)
            src = (sy * 8 + sx) * 4
            dst = (y * size + x) * 4
            out[dst:dst + 4] = head[src:src + 4]

    return encode_png(size, size, out)
