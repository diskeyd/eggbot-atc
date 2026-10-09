"""Automatic pen change macro. grbl-Mega-5X has no M6, so the host expands it.

Axes: X pen arm angle (deg), Y egg rotation (deg), Z magazine index (deg), A slide (mm).
"""

import re

from .config import get, require


def fmt(v):
    return f"{float(v):.3f}".rstrip("0").rstrip(".")


def slot_deg(cfg, tool):
    # ponytail: always absolute angle, no shortest-direction rotation; add if slot_count grows past 6
    count = int(require(cfg, "atc.slot_count"))
    if not 1 <= tool <= count:
        raise ValueError(f"tool T{tool} outside magazine (slot_count={count})")
    return float(require(cfg, "atc.slot0_deg")) + (tool - 1) * 360.0 / count


def tool_change(cfg, old, new, resume_xy=None):
    """G-code lines that swap pen `old` for pen `new`; 0 means no pen (start or end of job).

    Twist method: the magazine turns `twist_deg` off a slot so the fork clears the pen,
    then turns back onto the slot angle so the fork closes around the pen holder.
    """
    a = lambda k: fmt(require(cfg, "atc." + k))
    f = f" F{fmt(get(cfg, 'atc.atc_feed', 600))}"
    dwell = fmt(get(cfg, "drawing.pen_dwell_ms", 300) / 1000)
    twist = float(require(cfg, "atc.twist_deg"))
    lines = [
        f"; ATC T{old} -> T{new}",
        require(cfg, "drawing.pen_up_cmd"),
        f"G4 P{dwell}",
        f"G0 X{a('park_x')}",
    ]
    if old:  # return the current pen to its slot
        z = slot_deg(cfg, old)
        lines += [
            f"G1 Z{fmt(z + twist)}{f}",  # fork turned clear of the pen
            f"G1 A{a('slide_in_mm')}{f}",
            f"G1 Z{fmt(z)}{f}",  # fork closes on the pen holder
            f"G1 X{a('release_x')}{f}",  # arm pulls off the magnets, pen stays in the fork
            f"G1 A{a('slide_out_mm')}{f}",
        ]
        if new == 0:  # end of job: pen returned, nothing to pick up
            lines.append("; ATC end")
            return lines
        lines.append(f"G0 X{a('park_x')}")
    z = slot_deg(cfg, new)
    lines += [
        f"G1 Z{fmt(z)}{f}",  # fork holding the new pen faces the arm (slide still out)
        f"G1 A{a('slide_in_mm')}{f}",  # pen holder seats on the arm magnets
        f"G1 Z{fmt(z + twist)}{f}",  # fork opens, pen stays on the arm
        f"G1 A{a('slide_out_mm')}{f}",
    ]
    if resume_xy:
        lines.append(f"G0 X{fmt(resume_xy[0])} Y{fmt(resume_xy[1])}")
    lines.append("; ATC end")
    return lines


# Also CAM forms: "M06 T2", "M6T2", "N10 T2 M6".
_TOOL = re.compile(r"^\s*(?:N\d+\s*)?(?:M0?6\s*)?T(\d+)\b", re.IGNORECASE)
_X = re.compile(r"\bX(-?\d*\.?\d+)", re.IGNORECASE)
_Y = re.compile(r"\bY(-?\d*\.?\d+)", re.IGNORECASE)
_END = re.compile(r"^\s*(?:N\d+\s*)?M0?(?:2|30)\b", re.IGNORECASE)


def insert_tool_changes(cfg, lines):
    """Replace `T<n>` / `M6 T<n>` lines of existing G-code with the ATC macro."""
    out, tool, last = [], int(get(cfg, "atc.initial_tool", 0)), {}
    for line in lines:
        m = _TOOL.match(line)
        if not m:
            if line.lstrip().upper().startswith(("G0", "G1")):
                for axis, rx in (("X", _X), ("Y", _Y)):
                    hit = rx.search(line)
                    if hit:
                        last[axis] = float(hit.group(1))
            out.append(line.rstrip("\n"))
            continue
        new = int(m.group(1))
        if new != tool:
            resume = (last["X"], last["Y"]) if {"X", "Y"} <= last.keys() else None
            out += tool_change(cfg, tool, new, resume)
            tool = new
    if tool:  # return the last pen and go to the zero pose, before a trailing M2/M30
        end = next((i for i in range(len(out) - 1, -1, -1) if _END.match(out[i])), len(out))
        out[end:end] = tool_change(cfg, tool, 0) + ["G0 X0 Y0 Z0 A0"]
    return out
