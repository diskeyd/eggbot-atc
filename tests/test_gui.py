import tkinter as tk
import tomllib
from pathlib import Path

import pytest

from eggbot_atc import config, gui
from eggbot_atc.gui import map_tools, parse_value, set_toml_value

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


def test_layer_colour_sets_pen_number():
    src = ["G21", "T1", "G1 X1 Y1", "M6 T2", "G1 X2 Y2"]
    assert map_tools(src, [1, 3], [3, 1]) == ["G21", "T3", "G1 X1 Y1", "T1", "G1 X2 Y2"]
    assert map_tools(["G21", "G0 X1 Y1"], [], [2]) == ["G21", "T2", "G0 X1 Y1"]  # no tool line


@pytest.fixture
def app(tmp_path, monkeypatch):
    (tmp_path / "machine.toml").write_text((HERE / "machine_test.toml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    shown = []
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(gui.messagebox, name, lambda *a, _n=name, **k: shown.append((_n, a)))
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    a = gui.UltimateATCGCodeApp(root)
    a.shown = shown
    yield a
    root.destroy()


def test_window_converts_svg_with_layer_colours(app, monkeypatch):
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **k: str(HERE / "sample.svg"))
    app.load_file_dialog()
    assert [c.get() for c in app.layer_combos] == ["빨강색", "파랑색", "초록색"]
    app.layer_combos[0].set("초록색")  # layer 1 drawn with pen 3
    app.apply_atc_conversion()
    lines = [l.rstrip("\n") for l in app.gcode_lines]
    assert [l for l in lines if l.startswith("; ATC T")] == [
        "; ATC T0 -> T3", "; ATC T3 -> T2", "; ATC T2 -> T3", "; ATC T3 -> T0"]
    assert "G4 P0.3" in lines and "G4 P300" not in lines
    assert not app.preview_only and app.shown[-1][0] == "showinfo"


def test_window_blocks_save_until_measured(app, monkeypatch):
    text = set_toml_value(app.cfg_path.read_text(encoding="utf-8"), "atc.park_x", "TODO")
    app.cfg_path.write_text(text, encoding="utf-8")
    app.cfg = config.load(app.cfg_path)
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **k: str(HERE / "sample.svg"))
    app.load_file_dialog()
    app.apply_atc_conversion()
    assert app.preview_only
    saved = []
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename", lambda **k: saved.append(1) or "x.gcode")
    app.save_file()
    assert not saved and app.shown[-1][0] == "showerror"


@pytest.mark.parametrize("name, body", [
    ("cnc.ngc", "%\nG21\n(Start cutting path id: path2)\nG00 Z5\nG00 X0 Y0\nG01 Z-1 F100\nG01 X100 Y0 Z-1\n"
                "G02 X100 Y200 Z-1 I0 J100\nG00 Z5\n(Start cutting path id: path4)\nG00 X50 Y50\nG01 Z-1\n"
                "G01 X60 Y60 Z-1\nG00 Z5\nM5\nM2\n%\n"),
    ("ready.gcode", "G21\nG90\nT1\nG0 X5 Y10\nM3 S30\nG1 X10 Y20 F1500\nM3 S90\nT2\nG0 X20 Y30\nM3 S30\nG1 X25 Y40\nM3 S90\nM2\n"),
])
def test_window_opens_gcode(app, monkeypatch, tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **k: str(path))
    app.load_file_dialog()
    assert len(app.layer_combos) == 2 and app.gcode_lines
    app.apply_atc_conversion()
    assert sum(l.startswith("; ATC T") for l in app.gcode_lines) == 3
    assert not any(e[0] == "showerror" for e in app.shown)
