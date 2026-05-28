"""80x80 animated spinning-fan card for the GeeKmagic clock's GIF slot.

Shows a fan whose blades genuinely spin on-device (the firmware animates a
multi-frame container) with the current speed in RPM underneath. The spin
rate scales with the real RPM: an idle fan turns slowly, a fan under load
whirls faster.

How the spin works: we emit PHYS still frames covering exactly one turn,
frame j rotated by `j * step * (360/PHYS)` degrees. The transport cycles
these frames over the device's fixed 33-slot animation. PHYS is 11 and 33
is a multiple of 11, so the cycle loops seamlessly; `step` (derived from
RPM) sets how far the fan jumps per frame, i.e. the apparent speed.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from claude_meter.renderers import (
    COLOR_BG, bar_color, encode_device_jpeg, load_font,
)

DISPLAY_SIZE = (80, 80)

PHYS = 11            # still frames per full turn (must divide the 33 slots)
_SUPERSAMPLE = 4     # draw the fan big, then downscale for clean edges
_FAN_DIAMETER = 42   # fan diameter in final pixels
_FAN_CENTER = (40, 23)
# Curved, swept teardrop blades (the macOS "fanblades" look). Each blade is
# fat and rounded at the tip, narrow at the hub, and curved for the pinwheel
# sweep. A gentle bright->dim fade keeps the spin direction/speed legible
# without any blade looking missing.
_LEAD_COLOR = (155, 218, 255)   # brightest (leading) blade
_TAIL_COLOR = (70, 132, 178)    # dimmest blade — still clearly cyan
_HUB_COLOR = (215, 238, 255)
_LABEL_COLOR = (190, 190, 190)  # "RPM" caption — bright enough to read small
_BLADES = 5
_BLADE_SWEEP = 26.0  # degrees the blade curves from hub to tip

# Apparent-speed tiers. `step` is how many of the PHYS sub-frames the fan
# advances per device frame; bigger = faster spin. Capped low enough that
# the blades read as a forward spin rather than a strobing wagon-wheel.
_MIN_STEP = 1
_MAX_STEP = 3


def _blade_color(b: int):
    t = b / (_BLADES - 1)
    return tuple(round(_LEAD_COLOR[i] + (_TAIL_COLOR[i] - _LEAD_COLOR[i]) * t)
                 for i in range(3))


def _draw_blade(draw: ImageDraw.ImageDraw, c: float, base_deg: float,
                r0: float, r1: float, w0: float, w1: float, color) -> None:
    """One swept teardrop blade, built by stamping overlapping circles along a
    curved spine: narrow near the hub, fattening to a rounded tip, sweeping
    sideways for the pinwheel curve. Circle stamping rounds the ends for free.
    """
    steps = 28
    for i in range(steps + 1):
        t = i / steps
        ang = math.radians(base_deg + _BLADE_SWEEP * t)
        rr = r0 + (r1 - r0) * t
        # Width swells then tapers slightly at the very tip for a teardrop.
        width = w0 + (w1 - w0) * math.sin(min(t * 1.15, 1.0) * math.pi / 2)
        cx = c + rr * math.cos(ang)
        cy = c + rr * math.sin(ang)
        draw.ellipse([cx - width, cy - width, cx + width, cy + width], fill=color)


def _fan_sprite() -> Image.Image:
    """An RGBA fan at angle 0, ready to be rotated. Center at sprite center."""
    d = _FAN_DIAMETER * _SUPERSAMPLE
    spr = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    draw = ImageDraw.Draw(spr)
    c = d / 2.0
    r0 = d * 0.12   # blade root, just outside the hub
    r1 = d * 0.46   # blade tip
    w0 = d * 0.058  # half-width at the root
    w1 = d * 0.135  # half-width near the tip
    span = 360.0 / _BLADES
    for b in range(_BLADES):
        _draw_blade(draw, c, b * span, r0, r1, w0, w1, _blade_color(b))
    hub = d * 0.135
    draw.ellipse([c - hub, c - hub, c + hub, c + hub], fill=_HUB_COLOR)
    return spr


def _speed_step(rpm: float, rpm_min: float, rpm_max: float) -> int:
    if rpm_max > rpm_min:
        frac = (rpm - rpm_min) / (rpm_max - rpm_min)
    else:
        frac = 0.0
    frac = max(0.0, min(frac, 1.0))
    return _MIN_STEP + round(frac * (_MAX_STEP - _MIN_STEP))


class Fan80Renderer:
    def render(self, rpm: float, rpm_min: float = 0.0,
               rpm_max: float = 0.0) -> list[bytes]:
        sprite = _fan_sprite()
        step = _speed_step(rpm, rpm_min, rpm_max)

        # Color the readout green->red by how hard the fan is working.
        if rpm_max > rpm_min:
            frac = max(0.0, min((rpm - rpm_min) / (rpm_max - rpm_min), 1.0))
        else:
            frac = 0.0
        color = bar_color(frac * 100)

        font_rpm = load_font(22)
        font_lbl = load_font(13)
        rpm_text = f"{rpm:.0f}"

        frames: list[bytes] = []
        for j in range(PHYS):
            angle = (j * step * 360.0 / PHYS) % 360.0
            frames.append(self._frame(sprite, angle, rpm_text, color,
                                       font_rpm, font_lbl))
        return frames

    def _frame(self, sprite: Image.Image, angle: float, rpm_text: str,
               color, font_rpm, font_lbl) -> bytes:
        img = Image.new("RGB", DISPLAY_SIZE, COLOR_BG)

        rot = sprite.rotate(-angle, resample=Image.BICUBIC, expand=False)
        rot = rot.resize((_FAN_DIAMETER, _FAN_DIAMETER), Image.LANCZOS)
        cx, cy = _FAN_CENTER
        img.paste(rot, (cx - _FAN_DIAMETER // 2, cy - _FAN_DIAMETER // 2), rot)

        draw = ImageDraw.Draw(img)
        rpm_w = int(font_rpm.getlength(rpm_text))
        draw.text(((80 - rpm_w) // 2, 43), rpm_text, font=font_rpm, fill=color)
        lbl = "RPM"
        lbl_w = int(font_lbl.getlength(lbl))
        draw.text(((80 - lbl_w) // 2, 65), lbl, font=font_lbl, fill=_LABEL_COLOR)

        return encode_device_jpeg(img)
