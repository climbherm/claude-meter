"""80x80 JPEG that lives in the GeeKmagic clock's Customization-GIF slot.

Fits alongside the device's stock clock + weather display. Percentages
shown as numbers with thin progress bars under each row.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from claude_meter.renderers import (
    COLOR_BG, COLOR_DIM, COLOR_TRACK, bar_color, encode_device_jpeg, load_font,
)

DISPLAY_SIZE = (80, 80)


class Gif80Renderer:
    """Renders a single 80x80 usage frame (transport wraps it for the device)."""

    def render(self, five_pct: float, five_reset: str,
               week_pct: float, week_reset: str) -> list[bytes]:
        img  = Image.new("RGB", DISPLAY_SIZE, COLOR_BG)
        draw = ImageDraw.Draw(img)

        font_lbl = load_font(12)
        font_pct = load_font(20)

        def draw_row(y: int, label: str, pct: float):
            pct_clamped = max(0.0, min(pct, 999.0))
            bar_pct     = min(pct_clamped, 100.0)
            color       = bar_color(pct_clamped)
            pct_text    = f"{pct_clamped:.0f}%"

            draw.text((4, y), label, font=font_lbl, fill=COLOR_DIM)
            pct_w = int(font_pct.getlength(pct_text))
            draw.text((76 - pct_w, y - 2), pct_text, font=font_pct, fill=color)

            bar_y = y + 22
            draw.rectangle([4, bar_y, 76, bar_y + 6], fill=COLOR_TRACK)
            filled = int(72 * bar_pct / 100)
            if filled > 0:
                draw.rectangle([4, bar_y, 4 + filled, bar_y + 6], fill=color)

        draw_row(2,  "5h", five_pct)
        draw_row(42, "7d", week_pct)

        return [encode_device_jpeg(img)]
