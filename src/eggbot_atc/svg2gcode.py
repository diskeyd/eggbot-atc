"""SVG (one Inkscape layer per pen, label "T1 red") -> G-code with ATC macros.

Canvas convention: full SVG height = one egg revolution (360 deg on Y),
full SVG width = drawing.svg_width_mm on X.
"""

import re

from svgelements import SVG, Group, Path, Shape

from .atc import fmt, tool_change
from .config import get, require

_INK = "{http://www.inkscape.org/namespaces/inkscape}"
_LABEL_TOOL = re.compile(r"\bT(\d+)\b")


def _flatten(group, step_px):
    """Yield polylines (px) for every shape under `group`; curves sampled every step_px."""
    for e in group.select():
        if not isinstance(e, Shape):
            continue
        path = e if isinstance(e, Path) else Path(e)
        path.reify()
        cur = []
        for seg in path.segments():
            kind = type(seg).__name__
            if kind == "Move":
                if len(cur) > 1:
                    yield cur
                cur = [(seg.end.x, seg.end.y)]
            elif kind in ("Line", "Close"):
                cur.append((seg.end.x, seg.end.y))
            else:  # CubicBezier, QuadraticBezier, Arc
                n = max(2, int(seg.length(error=1e-3) / step_px) + 1)  # default 1e-12 takes seconds per curve
                cur.extend((p.x, p.y) for p in (seg.point(i / n) for i in range(1, n + 1)))
        if len(cur) > 1:
            yield cur


def load_layers(svg_path, cfg):
    """Return [(tool_no, [polyline_in_machine_units, ...]), ...] in drawing order."""
    svg = SVG.parse(svg_path, reify=True)
    width = float(require(cfg, "drawing.svg_width_mm"))
    x0 = float(require(cfg, "drawing.x_offset_mm"))
    if x0 < 0 or x0 + width > float(require(cfg, "machine.x_max_mm")):
        raise ValueError("x_offset_mm + svg_width_mm must fit inside 0..x_max_mm")
    sx = width / svg.width
    sy = 360.0 / svg.height
    invert = bool(get(cfg, "drawing.y_invert", False))
    step_px = float(get(cfg, "drawing.flatten_mm", 0.5)) / sx
    layers = [g for g in svg if isinstance(g, Group) and g.values.get(_INK + "groupmode") == "layer"]
    if not layers:
        layers = [svg]  # plain SVG without Inkscape layers: everything is pen 1
    blocks = []
    for i, layer in enumerate(layers, 1):
        m = _LABEL_TOOL.search(layer.values.get(_INK + "label", ""))
        tool = int(m.group(1)) if m else i
        polylines = [
            [(x0 + x * sx, (svg.height - y if invert else y) * sy) for x, y in pl]
            for pl in _flatten(layer, step_px)
        ]
        if polylines:
            blocks.append((tool, polylines))
    return blocks


def emit_block(polylines, cfg):
    up, down = require(cfg, "drawing.pen_up_cmd"), require(cfg, "drawing.pen_down_cmd")
    dwell = f"G4 P{fmt(get(cfg, 'drawing.pen_dwell_ms', 300) / 1000)}"
    feed = fmt(get(cfg, "drawing.draw_feed", 1500))
    lines = []
    for pl in polylines:
        x, y = pl[0]
        lines += [f"G0 X{fmt(x)} Y{fmt(y)}", down, dwell]
        lines += [f"G1 X{fmt(x)} Y{fmt(y)} F{feed}" for x, y in pl[1:]]
        lines += [up, dwell]
    return lines


def assemble(cfg, blocks):
    up = require(cfg, "drawing.pen_up_cmd")
    lines = ["; eggbot-atc", "G21", "G90", "G17", get(cfg, "drawing.home_cmd", "G92 X0 Y0 Z0 A0"), up]
    tool = int(get(cfg, "atc.initial_tool", 0))
    for new, polylines in blocks:
        if new != tool:
            lines += tool_change(cfg, tool, new)
            tool = new
        lines += emit_block(polylines, cfg)
    # Return the pen and go back to the zero pose so the next run's G92 and
    # initial_tool = 0 are true again. No M5: on spindle PWM it would slam the servo to duty 0.
    if tool:
        lines += tool_change(cfg, tool, 0)
    lines += [up, "G0 X0 Y0 Z0 A0", "M2"]
    return lines


def convert(svg_path, cfg):
    return assemble(cfg, load_layers(svg_path, cfg))
