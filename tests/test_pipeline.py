from pathlib import Path

import pytest

from eggbot_atc import config
from eggbot_atc.atc import insert_tool_changes, tool_change
from eggbot_atc.grbl_settings import settings
from eggbot_atc.svg2gcode import convert, load_layers

HERE = Path(__file__).parent
ROOT = HERE.parent


@pytest.fixture
def cfg():
    return config.load(HERE / "machine_test.toml")


def atc_blocks(lines):
    blocks, cur = [], None
    for line in lines:
        if line.startswith("; ATC T"):
            cur = [line]
        elif line == "; ATC end":
            blocks.append(cur)
            cur = None
        elif cur is not None:
            cur.append(line)
    return blocks


def test_three_layers_three_tool_changes(cfg):
    lines = convert(HERE / "sample.svg", cfg)
    blocks = atc_blocks(lines)
    assert [b[0] for b in blocks] == ["; ATC T0 -> T1", "; ATC T1 -> T2", "; ATC T2 -> T3", "; ATC T3 -> T0"]
    assert lines[:5] == ["; eggbot-atc", "G21", "G90", "G17", "G92 X0 Y0 Z0 A0"]
    assert lines[-2:] == ["G0 X0 Y0 Z0 A0", "M2"]
    assert "M5" not in lines


def test_job_ends_with_pen_returned(cfg):
    last = atc_blocks(convert(HERE / "sample.svg", cfg))[-1]
    assert "G1 X70 F600" in last  # release_x: pen left in its slot
    assert not any(l.startswith("G1 Z") for l in last)  # no indexing, nothing to pick up
    assert last[-1] == "G1 A0 F600"  # slide fully out (helper strips "; ATC end")


def test_first_change_indexes_before_slide_and_skips_release(cfg):
    first, second = atc_blocks(convert(HERE / "sample.svg", cfg))[:2]
    assert not any("X70" in l for l in first)  # release_x never used without a pen
    z = next(i for i, l in enumerate(first) if l.startswith("G1 Z"))
    a = next(i for i, l in enumerate(first) if l.startswith("G1 A"))
    assert z < a
    assert "G1 X70 F600" in second
    assert "G4 P0.3" in first


def test_slot_angles(cfg):
    blocks = atc_blocks(convert(HERE / "sample.svg", cfg))
    zs = [next(l for l in b if l.startswith("G1 Z")) for b in blocks[:3]]
    assert zs == ["G1 Z10 F600", "G1 Z130 F600", "G1 Z250 F600"]


def test_resume_at_first_point_then_pen_down(cfg):
    blocks = load_layers(HERE / "sample.svg", cfg)
    lines = convert(HERE / "sample.svg", cfg)
    for (_, polylines), block in zip(blocks, atc_blocks(lines)[:3]):
        i = lines.index("; ATC end", lines.index(block[0]))
        x, y = polylines[0][0]
        assert lines[i + 1] == f"G0 X{x:.3f}".rstrip("0").rstrip(".") + f" Y{y:.3f}".rstrip("0").rstrip(".")
        assert lines[i + 2] == "M3 S30"


def test_scale_and_transform(cfg):
    blocks = dict(load_layers(HERE / "sample.svg", cfg))
    x, y = blocks[1][0][0]  # (10,10) on a 120x60 canvas -> 60 deg wide, 360 deg tall
    assert (round(x, 3), round(y, 3)) == (5.0, 60.0)
    assert len(blocks[1][0]) > 3  # bezier got flattened
    ys = [p[1] for p in blocks[3][0]]  # circle translated by 5 units: centre 45 -> 270 deg
    assert round((max(ys) + min(ys)) / 2, 1) == 270.0


def test_x_offset_shifts_drawing_and_must_fit(cfg):
    cfg["drawing"]["x_offset_deg"] = 15
    x, _ = dict(load_layers(HERE / "sample.svg", cfg))[1][0][0]
    assert round(x, 3) == 20.0  # 15 deg offset + 5 deg from the SVG
    cfg["drawing"]["x_offset_deg"] = 25  # 25 + 60 > x_max_deg 80
    with pytest.raises(ValueError):
        load_layers(HERE / "sample.svg", cfg)


def test_template_refuses_until_measured():
    cfg = config.load(ROOT / "machine.toml")
    assert config.missing(cfg) == config.REQUIRED_FOR_CONVERT
    with pytest.raises(config.MeasurementNeeded):
        tool_change(cfg, 0, 1)


def test_grbl_settings(cfg):
    out = settings(cfg)
    assert "$100=17.778  ; X steps/deg (1 unit = 1 deg of pen arm)" in out
    assert any(l.startswith("$103=800") for l in out)
    assert any(l.startswith("$101=17.778") for l in out)
    assert any(l.startswith("$104=17.778") for l in out)  # second egg motor mirrors Y
    assert any(l.startswith("$110=3000") for l in out)
    assert any(l.startswith("; $111=TODO") for l in out)
    template = settings(config.load(ROOT / "machine.toml"))
    assert any(l.startswith("; $130=TODO") for l in template)


def test_existing_gcode_tool_lines_replaced(cfg):
    src = ["G0 X5 Y6", "T1", "G1 X8 Y9 F100", "M6 T2", "G1 X1 Y1"]
    out = insert_tool_changes(cfg, src)
    assert out[:2] == ["G0 X5 Y6", "; ATC T0 -> T1"]
    assert "G0 X8 Y9" in out  # resumes where the T2 line was reached
    assert not any(l.strip().startswith(("T", "M6")) for l in out)
    assert out[-1] == "G1 X1 Y1"


def test_cam_tool_line_forms(cfg):
    for line in ["M06 T2", "M6T2", "N10 T2 M6"]:
        out = insert_tool_changes(cfg, ["G0 X5 Y6", line, "G1 X1 Y1"])
        assert "; ATC T0 -> T2" in out, line
    assert insert_tool_changes(cfg, ["G0 X1 T2"]) == ["G0 X1 T2"]  # T mid-line is not a change
