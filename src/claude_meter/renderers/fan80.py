"""80x80 animated spinning-fan card for the GeeKmagic clock's GIF slot.

Shows a fan whose blades genuinely spin on-device (the firmware animates a
multi-frame container) with the current speed in RPM underneath. The spin
rate scales with the real RPM: an idle fan turns slowly, a fan under load
whirls faster.

How the spin works: the device loops the frames at a fixed cadence we can't
change, so we set the apparent speed with the FRAME COUNT — n frames each
rotated `j * 360/n` degrees make one seamless turn, and fewer frames mean a
bigger jump per frame, i.e. a faster spin. The per-frame jump must stay under
half the blade pitch (360/_BLADES, halved) or the spin aliases backwards
(wagon-wheel), so the fast end uses few frames and the slow end more. A
stopped fan emits a single frame, so it shows static.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from claude_meter.renderers import (
    COLOR_BG, bar_color, encode_device_jpeg, load_font,
)

DISPLAY_SIZE = (80, 80)

_SUPERSAMPLE = 4     # draw the fan big, then downscale for clean edges
_FAN_DIAMETER = 40   # fan tip-to-tip diameter in final pixels
_FAN_CENTER = (40, 23)
_PAD = 1             # final-px breathing room around the fan, so the rotated
                     # blade tips never clip the sprite canvas
# Curved, swept teardrop blades (the macOS "fanblades" look). Each blade is
# fat and rounded at the tip, narrow at the hub, and curved for the pinwheel
# sweep. A gentle bright->dim fade keeps the spin direction/speed legible
# without any blade looking missing.
_LEAD_COLOR = (155, 218, 255)   # brightest (leading) blade
_TAIL_COLOR = (70, 132, 178)    # dimmest blade — still clearly cyan
_HUB_COLOR = (215, 238, 255)
_LABEL_COLOR = (190, 190, 190)  # "RPM" caption — bright enough to read small
_BLADES = 3
_BLADE_SWEEP = 48.0  # degrees the blade curves from hub to tip (crescent sweep)

# Spin speed is set by the frame count: the device loops frames at a cadence
# we can't change, so fewer frames = a bigger angle jump per frame = a faster
# spin. The jump must stay under half the blade pitch (360/_BLADES, halved) or
# the spin aliases backwards (wagon-wheel) — for 3 blades that ceiling is
# 60 deg, so 8 frames (45 deg) is the fast end and 18 frames (20 deg) slow.
_FRAMES_FAST = 8     # full load: big per-frame jump => fast spin
_FRAMES_SLOW = 18    # idle: small per-frame jump => gentle spin


def _blade_color(b: int):
    t = b / (_BLADES - 1)
    return tuple(round(_LEAD_COLOR[i] + (_TAIL_COLOR[i] - _LEAD_COLOR[i]) * t)
                 for i in range(3))


def _draw_blade(draw: ImageDraw.ImageDraw, c: float, base_deg: float,
                r0: float, r1: float, phi0: float, phi1: float, color) -> None:
    """One broad swept fan paddle (the household-fan look). Worked in
    (radius, angle) space: the blade is a tapered arc sector — a narrow
    angular width `phi0` at the hub widening to a broad `phi1` at the tip,
    reaching out near the rim, with a curved centreline (`_BLADE_SWEEP`) for
    the pinwheel sweep and a rounded broad tip.
    """
    steps = 28

    def pt(rad: float, ang_deg: float):
        a = math.radians(ang_deg)
        return (c + rad * math.cos(a), c + rad * math.sin(a))

    left, right = [], []
    for i in range(steps + 1):
        t = i / steps
        center = base_deg + _BLADE_SWEEP * (t ** 0.85)   # curved centreline
        rr = r0 + (r1 - r0) * t
        half = phi0 + (phi1 - phi0) * math.sin(min(t * 1.1, 1.0) * math.pi / 2)
        left.append(pt(rr, center + half))
        right.append(pt(rr, center - half))

    # Broad rounded outer edge: an arc swung across the tip's angular span,
    # bulged slightly past r1 so the tip reads round, not chopped flat.
    center = base_deg + _BLADE_SWEEP
    tip = []
    for k in range(9):
        f = k / 8
        tip.append(pt(r1 * (1.0 + 0.05 * math.sin(f * math.pi)),
                      (center + phi1) - 2 * phi1 * f))

    draw.polygon(left + tip + right[::-1], fill=color)
    # Round the narrow root so it blends into the hub without a corner.
    hx, hy = pt(r0, base_deg)
    rw = math.radians(phi0) * r0
    draw.ellipse([hx - rw, hy - rw, hx + rw, hy + rw], fill=color)


def _fan_sprite() -> Image.Image:
    """An RGBA fan at angle 0, ready to be rotated. Center at sprite center."""
    R = _FAN_DIAMETER * _SUPERSAMPLE / 2.0      # fan outer radius in sprite px
    d = int(round(2 * (R + _PAD * _SUPERSAMPLE)))
    spr = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    draw = ImageDraw.Draw(spr)
    c = d / 2.0
    # Radii as fractions of the fan radius R, so the tip (r1 + w1) lands on R
    # and nothing spills past it — the _PAD then keeps R off the canvas edge.
    r0 = R * 0.18   # blade root, just outside the hub
    r1 = R * 0.86   # blade tip reaches out near the rim
    phi0 = 8.0      # root angular half-width (deg) — narrow
    phi1 = 30.0     # tip angular half-width (deg) — broad paddle
    span = 360.0 / _BLADES
    for b in range(_BLADES):
        _draw_blade(draw, c, b * span, r0, r1, phi0, phi1, _blade_color(b))
    hub = R * 0.26
    draw.ellipse([c - hub, c - hub, c + hub, c + hub], fill=_HUB_COLOR)
    return spr


def _frame_count(rpm: float, rpm_min: float, rpm_max: float) -> int:
    """How many frames to emit: 1 for a stopped fan (static), otherwise more
    frames for a slow spin and fewer for a fast one, mapped linearly by RPM."""
    if rpm <= 0:
        return 1  # fan stopped (e.g. asleep): a single frame -> static
    if rpm_max > rpm_min:
        frac = (rpm - rpm_min) / (rpm_max - rpm_min)
    else:
        frac = 0.0
    frac = max(0.0, min(frac, 1.0))
    return round(_FRAMES_SLOW + frac * (_FRAMES_FAST - _FRAMES_SLOW))


class Fan80Renderer:
    def render(self, rpm: float, rpm_min: float = 0.0,
               rpm_max: float = 0.0) -> list[bytes]:
        sprite = _fan_sprite()
        n_frames = _frame_count(rpm, rpm_min, rpm_max)

        # Color the readout green->red by how hard the fan is working.
        if rpm_max > rpm_min:
            frac = max(0.0, min((rpm - rpm_min) / (rpm_max - rpm_min), 1.0))
        else:
            frac = 0.0
        color = bar_color(frac * 100)

        font_rpm = load_font(22)
        font_lbl = load_font(13)
        rpm_text = f"{rpm:.0f}"

        # n_frames frames, each advanced 360/n => one seamless turn; fewer
        # frames spin faster. A stopped fan is a single static frame.
        frames: list[bytes] = []
        for j in range(n_frames):
            angle = j * 360.0 / n_frames
            frames.append(self._frame(sprite, angle, rpm_text, color,
                                       font_rpm, font_lbl))
        return frames

    def _frame(self, sprite: Image.Image, angle: float, rpm_text: str,
               color, font_rpm, font_lbl) -> bytes:
        img = Image.new("RGB", DISPLAY_SIZE, COLOR_BG)

        target = round(sprite.width / _SUPERSAMPLE)  # fan + _PAD, in final px
        rot = sprite.rotate(-angle, resample=Image.BICUBIC, expand=False)
        rot = rot.resize((target, target), Image.LANCZOS)
        cx, cy = _FAN_CENTER
        img.paste(rot, (cx - target // 2, cy - target // 2), rot)

        draw = ImageDraw.Draw(img)
        rpm_w = int(font_rpm.getlength(rpm_text))
        draw.text(((80 - rpm_w) // 2, 43), rpm_text, font=font_rpm, fill=color)
        lbl = "RPM"
        lbl_w = int(font_lbl.getlength(lbl))
        draw.text(((80 - lbl_w) // 2, 65), lbl, font=font_lbl, fill=_LABEL_COLOR)

        return encode_device_jpeg(img)
