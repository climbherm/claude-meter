"""240x240 JPEG for the GeeKmagic clock's Photo-mode full-screen slot.

Shows larger percentages with progress bars and reset countdowns.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from claude_meter.renderers import (
    COLOR_BG, COLOR_DIM, COLOR_TEXT, COLOR_TRACK, bar_color, load_font,
)

DISPLAY_SIZE = (240, 240)


class Photo240Renderer:
    def render(self, five_pct: float, five_reset: str,
               week_pct: float, week_reset: str, scoped=None) -> bytes:
        img  = Image.new("RGB", DISPLAY_SIZE, COLOR_BG)
        draw = ImageDraw.Draw(img)

        font_title = load_font(20)
        font_pct   = load_font(34)
        font_small = load_font(14)

        draw.text((12, 8), "Claude usage", font=font_title, fill=COLOR_TEXT)

        def draw_section(y: int, label: str, pct: float, reset: str):
            pct_clamped = max(0.0, min(pct, 999.0))
            bar_pct     = min(pct_clamped, 100.0)
            color       = bar_color(pct_clamped)

            draw.text((12, y), label, font=font_small, fill=COLOR_DIM)
            pct_text = f"{pct_clamped:.0f}%"
            draw.text((216 - int(font_pct.getlength(pct_text)), y - 4),
                      pct_text, font=font_pct, fill=color)

            bar_x, bar_y, bar_w, bar_h = 12, y + 38, 216, 14
            draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], fill=COLOR_TRACK)
            filled = int(bar_w * bar_pct / 100)
            if filled > 0:
                draw.rectangle([bar_x, bar_y, bar_x + filled, bar_y + bar_h], fill=color)

            draw.text((12, bar_y + bar_h + 4), f"resets {reset}",
                      font=font_small, fill=COLOR_DIM)

        if scoped is None:
            draw_section(40,  "5h session", five_pct, five_reset)
            draw_section(140, "7d weekly",  week_pct, week_reset)
        else:
            name, s_pct, s_reset = scoped
            font_pct_c = load_font(28)

            def draw_compact(y: int, label: str, pct: float, reset: str):
                pct_clamped = max(0.0, min(pct, 999.0))
                bar_pct     = min(pct_clamped, 100.0)
                color       = bar_color(pct_clamped)
                draw.text((12, y), label, font=font_small, fill=COLOR_DIM)
                pct_text = f"{pct_clamped:.0f}%"
                draw.text((216 - int(font_pct_c.getlength(pct_text)), y - 4),
                          pct_text, font=font_pct_c, fill=color)
                bar_x, bar_y, bar_w, bar_h = 12, y + 30, 216, 10
                draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h],
                               fill=COLOR_TRACK)
                filled = int(bar_w * bar_pct / 100)
                if filled > 0:
                    draw.rectangle([bar_x, bar_y, bar_x + filled, bar_y + bar_h],
                                   fill=color)
                draw.text((12, bar_y + bar_h + 2), f"resets {reset}",
                          font=font_small, fill=COLOR_DIM)

            draw_compact(36,  "5h session", five_pct, five_reset)
            draw_compact(100, "7d weekly",  week_pct, week_reset)
            draw_compact(164, f"7d {name}", s_pct, s_reset)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
