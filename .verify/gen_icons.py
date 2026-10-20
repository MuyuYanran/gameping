"""Generate the PWA icons without third-party imaging libraries.

A solid dark rounded square with the GamePing pulse glyph drawn as filled
rectangles — good enough for a home-screen icon and reproducible from source.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

BG = (11, 15, 23)
FG = (53, 208, 186)
WEB = Path(__file__).resolve().parent


def _png(width: int, pixels: bytearray) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = bytearray()
    stride = width * 4
    for y in range(width):
        raw.append(0)                       # filter type 0 (None)
        raw += pixels[y * stride:(y + 1) * stride]
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, width, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def _rounded_mask(x: int, y: int, size: int, radius: float) -> bool:
    r = radius
    if r <= 0:
        return True
    cx = min(max(x, r), size - 1 - r)
    cy = min(max(y, r), size - 1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def draw(size: int) -> bytearray:
    px = bytearray(size * size * 4)
    radius = size * 0.22
    # 脉冲折线的相对节点（0..1），与 favicon 的造型一致
    nodes = [(0.18, 0.60), (0.33, 0.60), (0.42, 0.30), (0.54, 0.76),
             (0.63, 0.42), (0.70, 0.60), (0.82, 0.60)]
    thickness = max(2, int(size * 0.075))

    def near_line(x: float, y: float) -> bool:
        for (x1, y1), (x2, y2) in zip(nodes, nodes[1:]):
            ax, ay, bx, by = x1 * size, y1 * size, x2 * size, y2 * size
            dx, dy = bx - ax, by - ay
            seg2 = dx * dx + dy * dy
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / seg2))
            cx, cy = ax + t * dx, ay + t * dy
            if (x - cx) ** 2 + (y - cy) ** 2 <= (thickness / 2) ** 2:
                return True
        return False

    for y in range(size):
        for x in range(size):
            i = (y * size + x) * 4
            if not _rounded_mask(x, y, size, radius):
                px[i:i + 4] = bytes((0, 0, 0, 0))
                continue
            r, g, b = FG if near_line(x, y) else BG
            px[i:i + 4] = bytes((r, g, b, 255))
    return px


def main() -> None:
    for size in (192, 512):
        data = _png(size, draw(size))
        out = WEB / f"icon-{size}.png"
        out.write_bytes(data)
        print(f"{out.name}: {len(data)} bytes")


if __name__ == "__main__":
    main()
