"""Renderers produce displayable frames from live data.

A renderer returns a list of JPEG frames. Static cards (usage) return a
single frame; animated cards (the spinning fan) return several. The
transport decides how to lay them out for the device.
"""
from __future__ import annotations

import io
from typing import Protocol

from PIL import Image, ImageFont

# Shared palette
COLOR_BG     = (0, 0, 0)
COLOR_TEXT   = (235, 235, 235)
COLOR_DIM    = (140, 140, 140)
COLOR_TRACK  = (40, 40, 40)
COLOR_GREEN  = (26, 166, 75)
COLOR_YELLOW = (228, 184, 26)
COLOR_RED    = (217, 58, 58)

# 80x80 device-JPEG encoding, shared by every gif-slot renderer.
#
# JFIF APP0 segment from the vendor converter's output (96x96 DPI density).
# Firmware silently rejects frames using Pillow's default (0x00 01 01 00).
APP0_BYTES = bytes.fromhex("ffe000104a46494600010101006000600000")

# Baseline JPEG quantization tables extracted from converter output.
# The hardware decoder on this device only accepts these values.
LUMA_QTABLE = [
    3, 2, 2, 3, 2, 2, 3, 3, 3, 3, 4, 3, 3, 4, 5, 8,
    5, 5, 4, 4, 5, 10, 7, 7, 6, 8, 12, 10, 12, 12, 11, 10,
    11, 11, 13, 14, 18, 16, 13, 14, 17, 14, 11, 11, 16, 22, 16, 17,
    19, 20, 21, 21, 21, 12, 15, 23, 24, 22, 20, 24, 18, 20, 21, 20,
]
CHROMA_QTABLE = [
    3, 4, 4, 5, 4, 5, 9, 5, 5, 9, 20, 13, 11, 13, 20, 20,
    20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20,
    20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20,
    20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20,
]


def encode_device_jpeg(img: Image.Image) -> bytes:
    """Encode an 80x80 image as a JPEG the GeeKmagic gif slot accepts."""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", qtables=[LUMA_QTABLE, CHROMA_QTABLE], subsampling=2)
    frame = buf.getvalue()
    # Patch APP0 (bytes [2..20]) to the firmware-expected density.
    return frame[:2] + APP0_BYTES + frame[20:]


def bar_color(pct: float):
    if pct >= 90:
        return COLOR_RED
    if pct >= 70:
        return COLOR_YELLOW
    return COLOR_GREEN


def load_font(size: int) -> ImageFont.ImageFont:
    for path in [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


class Renderer(Protocol):
    """A Renderer turns live data into one or more JPEG frames."""

    def render(self, *args, **kwargs) -> list[bytes]: ...


def get(mode: str) -> Renderer:
    """Factory: resolve a mode name to a Renderer instance."""
    if mode == "gif80":
        from claude_meter.renderers.gif80 import Gif80Renderer
        return Gif80Renderer()
    if mode == "photo240":
        from claude_meter.renderers.photo240 import Photo240Renderer
        return Photo240Renderer()
    if mode == "fan80":
        from claude_meter.renderers.fan80 import Fan80Renderer
        return Fan80Renderer()
    raise ValueError(
        f"unknown render mode: {mode!r} (expected 'gif80', 'photo240' or 'fan80')")
