"""CNC-style G-code (Inkscape gcodetools and similar) -> drawing blocks for this machine.

Such files lower the pen with Z (Z-1 down, Z5 up) and use document units for X/Y. Here Z turns the
magazine and X/Y are pen-arm / egg degrees, so the file is re-drawn: Z becomes the servo, arcs become
short lines, and the drawing's bounding box is scaled onto svg_width_deg x 360 like an SVG canvas.
"""

import math
import re

from .config import get
from .svg2gcode import drawing_area, plain

_WORD = re.compile(r"([GXYZIJ])\s*(-?\d*\.?\d+)", re.IGNORECASE)
_PATH_ID = re.compile(r"\(Start cutting path id:\s*(.*?)\)", re.IGNORECASE)
_TOOL = re.compile(r"^\s*(?:N\d+\s*)?(?:M0?6\s*)?T(\d+)\b", re.IGNORECASE)
_ATC = re.compile(r"^; ATC T")


def uses_z_for_pen(lines):
    """True for CNC G-code: a G0/G1 move with a Z word outside our own ATC blocks."""
    if any(_ATC.match(l) for l in lines):
        return False
    return any(re.match(r"\s*G0?[01]\b.*\bZ", l, re.IGNORECASE) for l in lines)


def _strip(line):
    return re.sub(r"\(.*?\)|;.*", "", line)


def _arc(x0, y0, x1, y1, i, j, cw, seg):
    cx, cy = x0 + i, y0 + j
    r = math.hypot(i, j)
    a0, a1 = math.atan2(y0 - cy, x0 - cx), math.atan2(y1 - cy, x1 - cx)
    sweep = a1 - a0
    if cw and sweep >= 0:
        sweep -= 2 * math.pi
    elif not cw and sweep <= 0:
        sweep += 2 * math.pi
    n = max(1, math.ceil(abs(sweep) * r / seg))
    return [(cx + r * math.cos(a0 + sweep * k / n), cy + r * math.sin(a0 + sweep * k / n)) for k in range(1, n + 1)]


def _groups(lines):
    """[(group_key, [polyline_in_file_units, ...]), ...]. A group starts at a new path id or T line."""
    xs, ys = [], []
    for l in lines:
        d = {k.upper(): float(v) for k, v in _WORD.findall(_strip(l))}
        if "X" in d:
            xs.append(d["X"])
        if "Y" in d:
            ys.append(d["Y"])
    if not xs or not ys:
        return []
    seg = math.hypot(max(xs) - min(xs), max(ys) - min(ys)) / 500 or 1.0

    groups, key = [], None
    x = y = z = 0.0
    mode, cur = 0, None

    def close():
        nonlocal cur
        if cur and len(cur) > 1:
            if not groups or groups[-1][0] != key:
                groups.append((key, []))
            groups[-1][1].append(cur)
        cur = None

    for line in lines:
        m, t = _PATH_ID.search(line), _TOOL.match(line)
        new = ("path", m.group(1).strip()) if m else ("tool", int(t.group(1))) if t else None
        if new:
            if new != key:
                close()
                key = new
            continue
        d = {k.upper(): float(v) for k, v in _WORD.findall(_strip(line))}
        if "G" in d and d["G"] in (0, 1, 2, 3):
            mode = int(d["G"])
        elif "G" in d and d["G"] == 91:
            raise ValueError("relative G-code (G91) is not supported")
        if "Z" in d:
            z = d["Z"]
            if z > 0:
                close()
            elif cur is None:
                cur = [(x, y)]
        if "X" not in d and "Y" not in d:
            continue
        nx, ny = d.get("X", x), d.get("Y", y)
        if cur is not None and mode in (2, 3):
            cur += _arc(x, y, nx, ny, d.get("I", 0.0), d.get("J", 0.0), mode == 2, seg)
        elif cur is not None:
            cur.append((nx, ny))
        x, y = nx, ny
    close()
    return groups


def machine_lines(lines, cfg):
    """G-code this machine can run as drawing input: CNC files are re-drawn, others pass through."""
    return plain(cfg, load_blocks(lines, cfg)) if uses_z_for_pen(lines) else lines


def load_blocks(lines, cfg):
    """[(tool, [polyline_in_machine_units, ...]), ...]; groups get pens 1, 2, 3, 1, ... in order."""
    groups = _groups(lines)
    pts = [p for _, pls in groups for pl in pls for p in pl]
    if not pts:
        raise ValueError("no pen-down moves found (expected Z below 0 to draw)")
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, width = drawing_area(cfg)
    # ponytail: bounding box fills the whole egg (Y span -> 360 deg); use SVG input for exact placement
    sx = width / ((max(xs) - min(xs)) or 1.0)
    sy = 360.0 / ((max(ys) - min(ys)) or 1.0)
    invert = bool(get(cfg, "drawing.y_invert", False))
    count = int(get(cfg, "atc.slot_count", 3))
    blocks = []
    for n, (key, pls) in enumerate(groups):
        tool = key[1] if key and key[0] == "tool" else n % count + 1
        # CNC Y grows upward; flip so the egg matches the Inkscape view, as the SVG path does.
        blocks.append((tool, [
            [(x0 + (px - min(xs)) * sx, ((py - min(ys)) if invert else (max(ys) - py)) * sy) for px, py in pl]
            for pl in pls
        ]))
    return blocks
