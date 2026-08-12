"""测试辅助工具函数。"""

from __future__ import annotations

import struct
import zlib


def _make_minimal_png(width: int, height: int) -> bytes:
    """生成最小有效 PNG 文件（指定宽高）。"""
    # PNG signature
    sig = b"\x89PNG\r\n\x1a\n"
    # IHDR chunk
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_len = struct.pack(">I", len(ihdr_data))
    ihdr_type = b"IHDR"
    ihdr_crc = struct.pack(">I", zlib.crc32(ihdr_type + ihdr_data) & 0xFFFFFFFF)
    ihdr = ihdr_len + ihdr_type + ihdr_data + ihdr_crc
    # IDAT chunk (minimal raw image data)
    raw = b""
    for _ in range(height):
        raw += b"\x00" + b"\x00" * (width * 3)
    compressed = zlib.compress(raw)
    idat_len = struct.pack(">I", len(compressed))
    idat_type = b"IDAT"
    idat_crc = struct.pack(">I", zlib.crc32(idat_type + compressed) & 0xFFFFFFFF)
    idat = idat_len + idat_type + compressed + idat_crc
    # IEND chunk
    iend = b"\x00\x00\x00\x00IEND\xaeB`\x82"
    return sig + ihdr + idat + iend