"""Look of the widget, copied from the Android widget: palette, round 3D buttons joined by lines, the four icons.
Pure functions (no tkinter, no Windows) so the drawing can be checked anywhere."""
from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple


def hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(c: Sequence[float]) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(v)))) for v in c)


def blend(a: str, b: str, t: float) -> str:
    """a at t=0 .. b at t=1."""
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex([ra[i] + (rb[i] - ra[i]) * t for i in range(3)])


# values from the Android app's res/values(-night)/colors.xml
LIGHT: Dict[str, str] = dict(
    card="#FFFFFF", text="#1A1A1A", sub="#4A4A4A", accent="#3E91FF", line="#D0D0D0", circle="#D0D0D0",
    disabled="#E6E6E6", c3_top="#EFEFEF", c3_bottom="#C6C6C6", c3_shadow="#9C9C9C",
    a3_top="#74B1FF", a3_shadow="#2563B8", icon="#FFFFFF", bad="#B00020", pill="#EBEBEB")
DARK: Dict[str, str] = dict(
    card="#1F1F1F", text="#FFFFFF", sub="#D8D8D8", accent="#3E91FF", line="#444444", circle="#4A4A4A",
    disabled="#2E2E2E", c3_top="#606060", c3_bottom="#3A3A3A", c3_shadow="#161616", a3_top="#74B1FF",
    a3_shadow="#1F54A0", icon="#FFFFFF", bad="#FF6B6B", pill="#343434")

# Android sizes (dp); the UI multiplies them by the display scale.
CARD_W = 330
CARD_R = 28
PAD_X = 16
HIT = 60          # touch/click area of a button
CIRCLE = 52
ICON_PAD = 9
LINE_H = 4
ROW_TOP = 66      # top of the button row (below title + battery)
CARD_H_BASE = 160


def button_row(n: int) -> List[Tuple[float, float]]:
    """(hit_left, hit_right) for n buttons across the card, plus the lines between them are the gaps."""
    inner = CARD_W - 2 * PAD_X
    gap = (inner - n * HIT) / (n - 1)
    return [(PAD_X + i * (HIT + gap), PAD_X + i * (HIT + gap) + HIT) for i in range(n)]


# ---- icons (48x48 view box, white, from ic_nc / ic_ambient / ic_auto / ic_off) ---------------------------------------
def _cubic(p0, p1, p2, p3, n=10):
    pts = []
    for i in range(1, n + 1):
        t = i / n
        x = (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t * t * p2[0] + t ** 3 * p3[0]
        y = (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t * t * p2[1] + t ** 3 * p3[1]
        pts.append((x, y))
    return pts


def _body(x0: float) -> List[Tuple[float, float]]:
    """Shoulders: M x0,36 C x0,30.5 x0+5.5,27.5 x0+11,27.5 C x0+16.5,27.5 x0+22,30.5 x0+22,36 z"""
    s = (x0, 36.0)
    return [s] + _cubic(s, (x0, 30.5), (x0 + 5.5, 27.5), (x0 + 11, 27.5)) + \
        _cubic((x0 + 11, 27.5), (x0 + 16.5, 27.5), (x0 + 22, 30.5), (x0 + 22, 36.0))


def icon_ops(kind: str) -> List[tuple]:
    """Ops in view-box units: ('circle', cx, cy, r, filled, stroke_w) | ('poly', pts) | ('arc', cx, cy, r, start, extent, w)
    | ('rect', x, y, w, h)."""
    if kind == "auto":
        ops: List[tuple] = [("circle", 18.5, 19.5, 5.5, True, 0), ("poly", _body(9.0))]
        for x, y, w, h in ((32.5, 25.5, 2, 9), (36, 21, 2, 18), (39.5, 23.5, 2, 13), (43, 26.5, 2, 7)):
            ops.append(("rect", x, y, w, h))
        return ops
    ops = [("circle", 24, 19.5, 5.5, True, 0), ("poly", _body(13.0))]
    if kind == "nc":
        ops.append(("circle", 24, 24, 20, False, 2.5))
    elif kind == "ambient":
        for i in range(14):
            a = math.radians(i * 360 / 14)
            ops.append(("circle", 24 + 20 * math.cos(a), 24 + 20 * math.sin(a), 1.6, True, 0))
    elif kind == "off":
        cy = 17 + math.sqrt(18 ** 2 - 16 ** 2)
        a = math.degrees(math.atan2(cy - 17, 16))
        ops.append(("arc", 24, cy, 18, a, 180 - 2 * a, 2.2))
    return ops


def draw_icon(cv, kind: str, cx: float, cy: float, size: float, color: str, tag: str) -> None:
    """Draws an icon on a tkinter Canvas (or anything with the same create_* calls)."""
    s = size / 48.0
    ox, oy = cx - size / 2, cy - size / 2
    for op in icon_ops(kind):
        if op[0] == "circle":
            _, x, y, r, filled, w = op
            box = (ox + (x - r) * s, oy + (y - r) * s, ox + (x + r) * s, oy + (y + r) * s)
            if filled:
                cv.create_oval(*box, fill=color, outline=color, width=1, tags=tag)
            else:
                cv.create_oval(*box, outline=color, width=max(1.0, w * s), tags=tag)
        elif op[0] == "poly":
            flat = [v for x, y in op[1] for v in (ox + x * s, oy + y * s)]
            cv.create_polygon(*flat, fill=color, outline=color, tags=tag)
        elif op[0] == "rect":
            _, x, y, w, h = op
            cv.create_rectangle(ox + x * s, oy + y * s, ox + (x + w) * s, oy + (y + h) * s, fill=color, outline=color, tags=tag)
        elif op[0] == "arc":
            _, x, y, r, start, extent, w = op
            cv.create_arc(ox + (x - r) * s, oy + (y - r) * s, ox + (x + r) * s, oy + (y + r) * s, start=start,
                          extent=extent, style="arc", outline=color, width=max(1.0, w * s), tags=tag)


def draw_button(cv, cx: float, cy: float, r: float, state: str, three_d: bool, pal: Dict[str, str], k: float,
                tag: str) -> None:
    """state: 'off' | 'active' | 'disabled'. 3D = darker disc peeking out below a shaded face (Android bg_circle_3d)."""
    if state == "disabled":
        cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=pal["disabled"], outline="", tags=tag)
        return
    active = state == "active"
    if not three_d:
        cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=pal["accent"] if active else pal["circle"], outline="", tags=tag)
        return
    shadow = pal["a3_shadow"] if active else pal["c3_shadow"]
    top = pal["a3_top"] if active else pal["c3_top"]
    bottom = pal["accent"] if active else pal["c3_bottom"]
    dy = 3 * k
    r2 = r - 1.5 * k
    cv.create_oval(cx - r2, cy - r2 + dy, cx + r2, cy + r2 + dy, fill=shadow, outline="", tags=tag)
    n = 9
    for i in range(n):  # fake vertical gradient: shrinking lighter discs stacked towards the top
        t = i / (n - 1)
        rr = r2 * (1 - 0.30 * t)
        yy = cy - dy / 2 - (r2 - rr) * 0.9
        cv.create_oval(cx - rr, yy - rr, cx + rr, yy + rr, fill=blend(bottom, top, t), outline="", tags=tag)
    cv.create_oval(cx - r2, cy - dy / 2 - r2, cx + r2, cy - dy / 2 + r2, outline=blend(bottom, "#FFFFFF", 0.4), width=1, tags=tag)


def rounded_points(x1: float, y1: float, x2: float, y2: float, r: float) -> List[float]:
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r,
            x1, y1 + r, x1, y1]


def rounded_poly(x1: float, y1: float, x2: float, y2: float, r: float, steps: int = 10) -> List[float]:
    """Outline of a rounded rectangle as a plain polyline (use with smooth=False; Tk's spline smoothing cuts corners)."""
    r = max(1.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts: List[float] = []
    for cx, cy, a0 in ((x2 - r, y1 + r, -90), (x2 - r, y2 - r, 0), (x1 + r, y2 - r, 90), (x1 + r, y1 + r, 180)):
        for i in range(steps + 1):
            a = math.radians(a0 + 90 * i / steps)
            pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
    return pts


def corner_inset(y: float, r: float) -> float:
    """How far a horizontal line at distance y from the top edge is inset by a corner of radius r."""
    if y >= r:
        return 0.0
    return r - math.sqrt(max(0.0, r * r - (r - y) ** 2))
