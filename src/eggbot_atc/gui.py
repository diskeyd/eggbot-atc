"""Windows desktop front end: open SVG or G-code, edit machine.toml, convert, preview, save."""

import copy
import re
import shutil
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import config
from .atc import insert_tool_changes
from .svg2gcode import convert

# Values shown in the settings window: (key, label). Order follows docs/측정-체크리스트.md.
FIELDS = [
    ("machine.x_max_deg", "펜 암 최대 각도 (도)"),
    ("drawing.x_offset_deg", "그림 왼쪽 끝 X (도)"),
    ("drawing.svg_width_deg", "그림 가로 폭 (도)"),
    ("atc.slot0_deg", "슬롯 1 매거진 각도 (도)"),
    ("atc.slide_out_mm", "슬라이드 후퇴 A (mm)"),
    ("atc.park_x", "교체 위치 X (도)"),
    ("atc.slide_in_mm", "슬라이드 밀착 A (mm)"),
    ("atc.release_x", "팔 후퇴 X (도)"),
    ("atc.twist_deg", "갈퀴 비켜나는 회전 (도)"),
    ("drawing.pen_up_cmd", "펜 올림 명령"),
    ("drawing.pen_down_cmd", "펜 내림 명령"),
    ("drawing.pen_dwell_ms", "서보 대기 (ms)"),
    ("atc.atc_feed", "교체 속도"),
    ("atc.initial_tool", "시작 시 물고 있는 펜 (0 = 없음)"),
]

# Stand-in numbers so a drawing can be previewed before the machine is measured. Never saved.
PREVIEW_FALLBACK = {
    "machine.x_max_deg": 80,
    "drawing.svg_width_deg": 60,
    "drawing.x_offset_deg": 0,
    "atc.slot0_deg": 0,
    "atc.park_x": 75,
    "atc.release_x": 70,
    "atc.slide_in_mm": 20,
    "atc.slide_out_mm": 0,
}

TOOL_COLORS = ["#d32f2f", "#1565c0", "#2e7d32", "#f9a825", "#6a1b9a", "#00838f"]
SPEEDS = {"0.5배속": 40, "1배속": 20, "2배속": 10, "5배속": 4, "10배속": 1}

_WORD = re.compile(r"([XYG])\s*(-?\d*\.?\d+)", re.IGNORECASE)


def app_dir():
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()


def config_path():
    """machine.toml next to the exe; created from the bundled template on first run."""
    path = app_dir() / "machine.toml"
    if not path.exists():
        bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "machine.toml"
        shutil.copy(bundled, path)
    return path


def set_toml_value(text, key, value):
    """Replace `name = ...` under `[section]` in machine.toml text, keeping the line's comment."""
    section, name = key.split(".")
    out, current, done = [], None, False
    for line in text.splitlines(keepends=True):
        head = re.match(r"\s*\[(\w+)\]", line)
        if head:
            current = head.group(1)
        m = re.match(rf"(\s*{name}\s*=\s*)(\"[^\"]*\"|[^#\s]+)(.*)", line, re.DOTALL)
        if m and current == section and not done:
            new = f'"{value}"' if isinstance(value, str) else str(value)
            line, done = m.group(1) + new + m.group(3), True
        out.append(line)
    if not done:
        raise KeyError(key)
    return "".join(out)


def parse_value(raw):
    raw = raw.strip()
    if raw.upper() == config.TODO:
        return config.TODO
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def preview_segments(lines, cfg):
    """Per line: (x0, y0, x1, y1, pen_down, tool) for drawing moves, else None. ATC moves are skipped."""
    up, down = config.get(cfg, "drawing.pen_up_cmd"), config.get(cfg, "drawing.pen_down_cmd")
    x = y = 0.0
    mode, pen, tool, in_atc = 0, False, 1, False
    segs = []
    for line in lines:
        s = line.split(";")[0].strip()
        seg = None
        if line.startswith("; ATC T"):
            in_atc, tool = True, int(line.split("-> T")[1]) or tool
        elif line.startswith("; ATC end"):
            in_atc = False
        elif s == down:
            pen = True
        elif s in (up, "M5"):
            pen = False
        elif s and not in_atc:
            words = {k.upper(): float(v) for k, v in _WORD.findall(s)}
            if "G" in words and words["G"] in (0, 1):
                mode = int(words["G"])
            if "X" in words or "Y" in words:
                nx, ny = words.get("X", x), words.get("Y", y)
                seg = (x, y, nx, ny, pen and mode == 1, tool)
                x, y = nx, ny
        segs.append(seg)
    return segs


class App:
    def __init__(self, root):
        self.root = root
        root.title("EggBot ATC 변환기")
        root.geometry("1400x860")
        self.cfg_path = config_path()
        self.cfg = config.load(self.cfg_path)
        self.src = None
        self.lines = []
        self.segs = []
        self.preview_only = False
        self.play_i = None

        bar = tk.Frame(root, padx=8, pady=6)
        bar.pack(fill="x")
        for text, cmd in (
            ("파일 열기 (SVG / G-code)", self.open_file),
            ("기계 설정", self.open_settings),
            ("변환", self.run_convert),
            ("G-code 저장", self.save),
        ):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=4)
        self.status = tk.StringVar(value=f"설정 파일: {self.cfg_path}")
        tk.Label(bar, textvariable=self.status, anchor="w").pack(side="left", padx=12, fill="x", expand=True)

        body = tk.PanedWindow(root, orient="horizontal", sashwidth=6)
        body.pack(fill="both", expand=True)
        left = tk.Frame(body)
        self.listbox = tk.Listbox(left, font=("Consolas", 10), activestyle="none")
        sb = ttk.Scrollbar(left, command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        body.add(left, width=520)

        right = tk.Frame(body)
        self.canvas = tk.Canvas(right, bg="white")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        ctl = tk.Frame(right, pady=4)
        ctl.pack(fill="x")
        self.play_btn = ttk.Button(ctl, text="▶ 재생", command=self.toggle_play)
        self.play_btn.pack(side="left", padx=4)
        self.speed = ttk.Combobox(ctl, values=list(SPEEDS), state="readonly", width=8)
        self.speed.set("1배속")
        self.speed.pack(side="left")
        self.info = tk.StringVar(value="가로 = 펜 암 X(도), 세로 = 계란 Y(도). 색 = 펜 번호")
        tk.Label(ctl, textvariable=self.info, anchor="w").pack(side="left", padx=12)
        body.add(right)

    # ---- files -------------------------------------------------------------
    def open_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("SVG / G-code", "*.svg *.gcode *.nc *.ngc *.txt"), ("모든 파일", "*.*")]
        )
        if not path:
            return
        self.src = Path(path)
        self.show(self.src.read_text(encoding="utf-8", errors="replace").splitlines() if self.src.suffix.lower() != ".svg" else [])
        self.status.set(f"열린 파일: {self.src.name}  →  [변환]을 누르세요")

    def run_convert(self):
        if not self.src:
            messagebox.showwarning("파일 없음", "먼저 [파일 열기]로 SVG나 G-code를 여세요.")
            return
        todo = config.missing(self.cfg)
        cfg = self.cfg
        if todo:
            cfg = copy.deepcopy(self.cfg)
            for key in todo:
                section, name = key.split(".")
                cfg.setdefault(section, {})[name] = PREVIEW_FALLBACK[key]
        try:
            if self.src.suffix.lower() == ".svg":
                lines = convert(self.src, cfg)
            else:
                text = self.src.read_text(encoding="utf-8", errors="replace")
                lines = insert_tool_changes(cfg, text.splitlines())
        except (ValueError, config.MeasurementNeeded) as e:
            messagebox.showerror("변환 실패", str(e))
            return
        self.preview_only = bool(todo)
        self.show(lines, cfg)
        changes = sum(1 for l in lines if l.startswith("; ATC T"))
        if todo:
            self.status.set(f"미리보기 전용 (안 잰 값 {len(todo)}개 → 임시값 사용, 저장 불가). 펜 교체 {changes}번")
        else:
            self.status.set(f"변환 완료: {len(lines)}줄, 펜 교체 {changes}번")

    def save(self):
        if not self.lines:
            messagebox.showwarning("내용 없음", "저장할 G-code가 없습니다. 먼저 [변환]하세요.")
            return
        if self.preview_only:
            todo = "\n".join(config.missing(self.cfg))
            messagebox.showerror("저장 불가", f"아직 안 잰 값이 있어 미리보기만 됩니다.\n[기계 설정]에서 채우세요:\n\n{todo}")
            return
        path = filedialog.asksaveasfilename(defaultextension=".gcode", filetypes=[("G-code", "*.gcode")])
        if path:
            Path(path).write_text("\n".join(self.lines) + "\n", encoding="utf-8")
            self.status.set(f"저장: {path}")

    # ---- settings ----------------------------------------------------------
    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("기계 설정 (machine.toml)")
        win.transient(self.root)
        win.grab_set()
        tk.Label(win, text="TODO = 아직 안 잰 값. 저장하면 machine.toml에 바로 기록됩니다.", pady=8).grid(
            row=0, column=0, columnspan=2
        )
        entries = {}
        for i, (key, label) in enumerate(FIELDS, start=1):
            tk.Label(win, text=label, anchor="w").grid(row=i, column=0, sticky="w", padx=10, pady=2)
            e = ttk.Entry(win, width=22)
            e.insert(0, str(config.get(self.cfg, key, "")))
            e.grid(row=i, column=1, padx=10, pady=2)
            entries[key] = e

        def save_settings():
            text = self.cfg_path.read_text(encoding="utf-8")
            try:
                for key, e in entries.items():
                    text = set_toml_value(text, key, parse_value(e.get()))
            except KeyError as k:
                messagebox.showerror("설정 저장 실패", f"machine.toml에 {k} 줄이 없습니다.", parent=win)
                return
            self.cfg_path.write_text(text, encoding="utf-8")
            self.cfg = config.load(self.cfg_path)
            todo = config.missing(self.cfg)
            note = "모든 값을 쟀습니다." if not todo else f"아직 안 잰 값 {len(todo)}개"
            messagebox.showinfo("저장 완료", f"machine.toml 저장. {note}", parent=win)
            win.destroy()

        ttk.Button(win, text="저장", command=save_settings).grid(row=len(FIELDS) + 1, column=0, columnspan=2, pady=10)

    # ---- list + preview ----------------------------------------------------
    def show(self, lines, cfg=None):
        self.stop()
        self.lines = lines
        self.segs = preview_segments(lines, cfg or self.cfg)
        self.listbox.delete(0, "end")
        in_atc = False
        for i, line in enumerate(lines):
            self.listbox.insert("end", line)
            in_atc = in_atc or line.startswith("; ATC T")
            if in_atc:
                self.listbox.itemconfig(i, bg="#e3f2fd")
            if line.startswith("; ATC end"):
                in_atc = False
        self.redraw()

    def transform(self):
        pts = [(s[0], s[1]) for s in self.segs if s] + [(s[2], s[3]) for s in self.segs if s]
        if not pts:
            return None
        xs, ys = zip(*pts)
        w, h = max(self.canvas.winfo_width(), 50), max(self.canvas.winfo_height(), 50)
        # X (arm deg) and Y (egg deg) are scaled separately: an unrolled egg map, not true shape.
        kx = (w - 40) / max(max(xs) - min(xs), 1e-6)
        ky = (h - 40) / max(max(ys) - min(ys), 1e-6)
        return lambda x, y: (20 + (x - min(xs)) * kx, 20 + (y - min(ys)) * ky)

    def draw_seg(self, t, seg, tag="base"):
        x0, y0, x1, y1, down, tool = seg
        a, b = t(x0, y0), t(x1, y1)
        if down:
            color = TOOL_COLORS[(tool - 1) % len(TOOL_COLORS)]
            self.canvas.create_line(*a, *b, fill=color, width=2, tags=tag)
        else:
            self.canvas.create_line(*a, *b, fill="#cfd8dc", dash=(3, 3), tags=tag)

    def redraw(self, upto=None):
        self.canvas.delete("all")
        t = self.transform()
        if not t:
            return
        for i, seg in enumerate(self.segs):
            if seg and (upto is None or i <= upto):
                self.draw_seg(t, seg)

    def on_select(self, _):
        sel = self.listbox.curselection()
        if not sel or self.play_i is not None:
            return
        self.redraw(upto=sel[0])

    def toggle_play(self):
        if self.play_i is not None:
            self.stop()
            return
        if not self.lines:
            return
        self.play_i = 0
        self.play_btn.config(text="⏸ 정지")
        self.canvas.delete("all")
        self.step()

    def stop(self):
        self.play_i = None
        self.play_btn.config(text="▶ 재생")

    def step(self):
        if self.play_i is None:
            return
        i = self.play_i
        if i >= len(self.lines):
            self.stop()
            return
        t = self.transform()
        if t and self.segs[i]:
            self.draw_seg(t, self.segs[i])
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(i)
        self.listbox.see(i)
        self.info.set(self.lines[i])
        self.play_i = i + 1
        self.root.after(SPEEDS[self.speed.get()], self.step)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
