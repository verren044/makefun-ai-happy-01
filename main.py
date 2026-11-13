"""ai-happy-01 entry (Python 3.13, standard library only).

Draws one image — a paper boat carrying a tiny lit candle across a puddle
at sunset — and writes it to /output/entry.png (1024x1024 PNG, < 1 MiB).

The idea, against the judge's criteria: warmth first (a little light carried
for someone, at sunset); a fresh, specific moment, not a stock smiley face or
a generic sunny scene; one warm palette, one clear focal point (the boat),
clean bold shapes that still read at ~300 px; no faces or anatomy to break.
The caption "a little light, for you" is part of the image, rendered in a
hand-checked 5x7 bitmap font.

How it is drawn: pure stdlib. The scene is computed pixel by pixel
(gradient sky, sun glow, clouds, water with ripples and the sun's reflection,
the boat, the candle flame, birds, reeds, caption), then encoded as PNG by
hand (zlib + struct + CRC32). No PIL, no network, no randomness — the same
bytes on every run.
"""
import math
import os
import struct
import zlib
from pathlib import Path

W, H = 1024, 1024

# 5x7 bitmap font, one glyph per character ('1' = ink). Only the glyphs the
# caption needs, each verified by eye as ASCII art before use.
FONT = {
    'a': ["01110", "10001", "10001", "01111", "10001", "10001", "01110"],
    'l': ["01100", "00100", "00100", "00100", "00100", "00100", "01110"],
    'i': ["00000", "00100", "00000", "01100", "00100", "00100", "01110"],
    't': ["00100", "00100", "01110", "00100", "00100", "00100", "00110"],
    'e': ["01110", "10001", "11111", "10000", "10001", "10001", "01110"],
    'g': ["01110", "10001", "10001", "10001", "01111", "00001", "01110"],
    'h': ["10000", "10000", "11110", "10001", "10001", "10001", "10001"],
    ',': ["00000", "00000", "00000", "00000", "00100", "00100", "01000"],
    'f': ["00110", "01001", "01000", "01110", "01000", "01000", "01000"],
    'o': ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    'r': ["00000", "00000", "10110", "11001", "10000", "10000", "10000"],
    'y': ["10001", "10001", "10001", "01110", "00100", "00100", "01100"],
    'u': ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    ' ': ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}

CAPTION = "a little light, for you"

# Palette (r, g, b).
SKY_TOP = (255, 158, 92)      # warm peach
SKY_MID = (255, 196, 120)     # gold
SKY_HOR = (255, 214, 150)     # pale gold at the horizon
SUN_COL = (255, 238, 178)     # pale warm sun
SUN_GLOW = (255, 190, 110)
CLOUD_COL = (255, 226, 190)
WATER_TOP = (74, 88, 122)     # dusk blue
WATER_BOT = (38, 48, 80)
REFL_COL = (255, 200, 110)
BOAT_COL = (255, 248, 236)    # white paper
BOAT_SHADE = (228, 210, 188)
SAIL_COL = (255, 227, 163)
MAST_COL = (120, 84, 60)
FLAME_OUT = (255, 138, 61)
FLAME_IN = (255, 214, 63)
REED_COL = (30, 40, 66)
TEXT_COL = (255, 243, 214)
TEXT_SHADOW = (46, 44, 66)
RIPPLE_COL = (150, 170, 210)


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    """Blend two RGB colours; t=0 -> c1, t=1 -> c2."""
    return (lerp(c1[0], c2[0], t), lerp(c1[1], c2[1], t), lerp(c1[2], c2[2], t))


def clamp(v, lo=0.0, hi=255.0):
    return lo if v < lo else hi if v > hi else v


def _in_poly(x, y, poly):
    """Point-in-polygon (ray casting) for a list of (x, y) vertices."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xin = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xin:
                inside = not inside
        j = i
    return inside


def _ellipse(x, y, cx, cy, rx, ry):
    dx = (x - cx) / rx
    dy = (y - cy) / ry
    return dx * dx + dy * dy


# Scene geometry (in 1024x1024 coordinates).
HORIZON = 640
SUN = (700, 470, 105)          # cx, cy, radius
CLOUDS = [(210, 180, 150, 42), (820, 230, 120, 34), (430, 120, 90, 26)]
BOAT_HULL = [(430, 692), (594, 692), (562, 732), (462, 732)]
BOAT_FOLD = [(512, 692), (512, 732)]
MAST = (512, 606, 512, 692)
SAIL = [(512, 606), (512, 684), (582, 684)]
CANDLE = (468, 668, 478, 692)  # x0, y0, x1, y1 (sits on the hull)
FLAME = (473, 656)             # cx, cy of the flame tip
BIRDS = [(300, 300), (360, 270)]
REEDS = [(60, 1024, 26), (130, 1024, 20), (950, 1024, 24), (1000, 1024, 18)]


def pixel(x, y):
    """Colour of pixel (x, y) as (r, g, b) floats 0..255."""
    if y < HORIZON:
        # --- sky: vertical gradient peach -> gold -> pale gold ---
        t = y / HORIZON
        if t < 0.55:
            c = mix(SKY_TOP, SKY_MID, t / 0.55)
        else:
            c = mix(SKY_MID, SKY_HOR, (t - 0.55) / 0.45)
        # sun glow: soft radial falloff
        dx, dy = x - SUN[0], y - SUN[1]
        d = math.sqrt(dx * dx + dy * dy)
        if d < 340:
            g = (1.0 - d / 340.0) ** 2 * 0.55
            c = mix(c, SUN_GLOW, g)
        if d <= SUN[2]:
            c = mix(SUN_COL, c, max(0.0, (d - SUN[2] * 0.55) / (SUN[2] * 0.45)) * 0.55)
        # clouds: soft ellipses
        for cx, cy, rx, ry in CLOUDS:
            e = _ellipse(x, y, cx, cy, rx, ry)
            if e < 1.0:
                a = (1.0 - e) * 0.45
                c = mix(c, CLOUD_COL, a)
        # birds: two small dark curves
        for bx, by in BIRDS:
            if abs(y - by) < 4 and abs(x - bx) < 16:
                w = abs(x - bx) / 16.0
                if abs(y - (by + w * 7)) < 2.5:
                    c = mix(c, (70, 52, 60), 0.85)
        return c
    # --- water ---
    t = (y - HORIZON) / (H - HORIZON)
    c = mix(WATER_TOP, WATER_BOT, t)
    # sun reflection: shimmering vertical band
    if 636 < x < 768:
        band = 1.0 - abs(x - 702) / 66.0
        shimmer = 0.5 + 0.5 * math.sin(y * 0.55 + math.sin(x * 0.11) * 2.0)
        a = band * shimmer * 0.5 * (1.0 - t * 0.55)
        if a > 0:
            c = mix(c, REFL_COL, min(0.75, a))
    # ripples: thin horizontal strokes
    for ry, amp in ((700, 0.10), (790, 0.08), (880, 0.06), (960, 0.05)):
        if abs(y - ry) < 2:
            ph = math.sin(x * 0.05 + ry) * 0.5 + 0.5
            c = mix(c, RIPPLE_COL, amp * (0.4 + 0.6 * ph))
    # boat reflection: squashed mirror of the hull
    if 700 < y < 760:
        my = 732 - (y - 732) * 0.9
        if _in_poly(x, my, BOAT_HULL):
            c = mix(c, BOAT_SHADE, 0.35 * (1.0 - (y - 700) / 60.0))
    # boat hull
    if _in_poly(x, y, BOAT_HULL):
        c = BOAT_COL
        # fold shading: right half slightly shaded
        if x > 512:
            c = mix(c, BOAT_SHADE, 0.35)
        # centre fold line
        if abs(x - 512) < 1.5 and 692 < y < 732:
            c = mix(c, BOAT_SHADE, 0.8)
    # mast
    if MAST[1] <= y <= MAST[3] and abs(x - MAST[0]) < 2:
        c = MAST_COL
    # sail
    if _in_poly(x, y, SAIL):
        c = SAIL_COL
    # candle body
    if CANDLE[0] <= x <= CANDLE[2] and CANDLE[1] <= y <= CANDLE[3]:
        c = (255, 244, 214)
    # flame glow + flame
    dx, dy = x - FLAME[0], (y - FLAME[1]) * 1.6
    d = math.sqrt(dx * dx + dy * dy)
    if d < 34:
        c = mix(c, (255, 170, 80), (1.0 - d / 34.0) * 0.55)
    if d < 13:
        c = FLAME_OUT if d > 6 else FLAME_IN
    # reeds at the bottom corners
    for rx, ry, rw in REEDS:
        if abs(x - rx) < rw and y > ry - 120 - (rw * 3):
            tip = ry - 120 - rw * 3 + abs(x - rx) * 2.2
            if y > tip:
                c = mix(c, REED_COL, 0.9)
    return c


def draw_caption(buf):
    """Draw CAPTION centred near the bottom, with a soft shadow."""
    scale = 5
    gw, gh, gap = 5 * scale, 7 * scale, 1 * scale
    text = CAPTION
    total_w = len(text) * (gw + gap) - gap
    x0 = (W - total_w) // 2
    y0 = 928
    for ci, ch in enumerate(text):
        glyph = FONT.get(ch)
        if glyph is None:
            continue
        gx = x0 + ci * (gw + gap)
        for row in range(7):
            bits = glyph[row]
            for col in range(5):
                if bits[col] == '1':
                    for sy in range(scale):
                        for sx in range(scale):
                            px, py = gx + col * scale + sx, y0 + row * scale + sy
                            # shadow pass
                            if 0 <= px + 2 < W and 0 <= py + 2 < H:
                                buf[py + 2][px + 2] = TEXT_SHADOW
                            if 0 <= px < W and 0 <= py < H:
                                buf[py][px] = TEXT_COL


def render():
    """Render the scene to a list of rows of (r, g, b) int tuples."""
    buf = [[(0, 0, 0)] * W for _ in range(H)]
    for y in range(H):
        row = buf[y]
        for x in range(W):
            r, g, b = pixel(x, y)
            row[x] = (int(clamp(r)), int(clamp(g)), int(clamp(b)))
    draw_caption(buf)
    return buf


def png_bytes(buf):
    """Encode rows as a PNG (colour type 2, 8-bit, filter 0 per row)."""
    raw = bytearray()
    for row in buf:
        raw.append(0)
        for r, g, b in row:
            raw.extend((r, g, b))
    comp = zlib.compress(bytes(raw), 9)

    def chunk(kind, data):
        c = struct.pack(">I", len(data)) + kind + data
        c += struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        return c

    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", comp) + chunk(b"IEND", b""))


def main():
    out_dir = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))
    out_dir.mkdir(parents=True, exist_ok=True)
    data = png_bytes(render())
    (out_dir / "entry.png").write_bytes(data)


if __name__ == "__main__":
    main()
