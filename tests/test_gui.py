import tomllib
from pathlib import Path

from eggbot_atc import config
from eggbot_atc.gui import parse_value, preview_segments, set_toml_value
from eggbot_atc.svg2gcode import convert

HERE = Path(__file__).parent
ROOT = HERE.parent


def test_settings_write_keeps_comments_and_other_sections():
    text = (ROOT / "machine.toml").read_text(encoding="utf-8")
    text = set_toml_value(text, "atc.park_x", parse_value("75.5"))
    text = set_toml_value(text, "drawing.pen_up_cmd", parse_value("M3 S100"))
    cfg = tomllib.loads(text)
    assert cfg["atc"]["park_x"] == 75.5
    assert cfg["drawing"]["pen_up_cmd"] == "M3 S100"
    assert cfg["atc"]["release_x"] == "TODO"  # untouched keys stay TODO
    assert "# X where the arm (pen on its magnets) meets the magazine fork" in text


def test_preview_draws_only_pen_down_moves_outside_atc():
    cfg = config.load(HERE / "machine_test.toml")
    lines = convert(HERE / "sample.svg", cfg)
    segs = preview_segments(lines, cfg)
    drawn = [s for s in segs if s and s[4]]
    assert {s[5] for s in drawn} == {1, 2, 3}  # one colour per pen
    for i, line in enumerate(lines):  # nothing inside a pen change is drawn
        if line.startswith(("G0 X75", "G1 A", "G1 Z")):
            assert segs[i] is None
