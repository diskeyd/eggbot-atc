"""Windows front end. The window is the team's EggBot Rotary ATC Simulator layout; G-code comes
from the tested eggbot_atc pipeline and settings live in machine.toml next to the exe."""

import copy
import os
import re
import shutil
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import config
from .atc import _TOOL, insert_tool_changes
from .cam import machine_lines, uses_z_for_pen
from .svg2gcode import load_layers, plain

# Settings window rows: (label, machine.toml key). Order follows docs/측정-체크리스트.md.
SETTINGS = [
    ("펜 암 최대 각도 X (Max)", "machine.x_max_deg"),
    ("그림 왼쪽 끝 X (Offset)", "drawing.x_offset_deg"),
    ("그림 가로 폭 X (Width)", "drawing.svg_width_deg"),
    ("슬롯 1 매거진 각도 Z", "atc.slot0_deg"),
    ("교체 대기 X (Park)", "atc.park_x"),
    ("팔 후퇴 X (Release)", "atc.release_x"),
    ("슬라이드 밀착 A (In)", "atc.slide_in_mm"),
    ("슬라이드 후퇴 A (Out)", "atc.slide_out_mm"),
    ("갈퀴 회전각 Z (Twist)", "atc.twist_deg"),
    ("펜 올림 명령", "drawing.pen_up_cmd"),
    ("펜 내림 명령", "drawing.pen_down_cmd"),
    ("이송 속도 (Feed)", "atc.atc_feed"),
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


def preview_cfg(cfg):
    """cfg with unmeasured values replaced by PREVIEW_FALLBACK, plus the list of replaced keys."""
    todo = config.missing(cfg)
    cfg = copy.deepcopy(cfg)
    for key in todo:
        section, name = key.split(".")
        cfg.setdefault(section, {})[name] = PREVIEW_FALLBACK[key]
    return cfg, todo


def map_tools(lines, change_points, tools):
    """Rewrite the tool line at each change point to the chosen pen; a file with no tool line gets
    one before its first move."""
    lines = list(lines)
    if not change_points:
        first = next((i for i, l in enumerate(lines) if re.match(r"\s*G0?[01]\b", l, re.IGNORECASE)), 0)
        return lines[:first] + [f"T{tools[0]}"] + lines[first:]
    for idx, tool in zip(change_points, tools):
        lines[idx] = f"T{tool}"
    return lines


class UltimateATCGCodeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("EggBot Rotary ATC Master Simulator PRO (Intuitive UI)")
        self.root.geometry("1550x920")
        self.root.config(bg="#F1F5F9")

        self.style = ttk.Style()
        self.style.theme_use('clam')

        self.input_filepath = ""
        self.gcode_lines = []
        self.source_lines = []
        self.change_points = []
        self.layer_names = []
        self.layer_tools = []
        self.layer_combos = []
        self.applied_colors = []
        self.is_converted = False
        self.preview_only = False

        # Pen n sits in magazine slot n; the colour list is just the slot order.
        self.color_list = ["빨강색", "파랑색", "초록색"]

        self.cfg_path = config_path()
        self.cfg = config.load(self.cfg_path)

        self.path_coords = []
        self.current_preview_dot = None
        self.atc_effect_text = None
        self.is_playing = False
        self.sim_index = 0
        self.sim_current_x = 0.0
        self.sim_current_y = 0.0
        self.sim_last_cx = None
        self.sim_last_cy = None
        self.sim_in_atc = False

        self.create_main_ui()

    def load_file_dialog(self):
        filepath = filedialog.askopenfilename(title="G-code / SVG 파일 선택", filetypes=[("G-code / SVG Files", "*.gcode *.nc *.ngc *.txt *.svg"), ("All Files", "*.*")])
        if not filepath: return
        self.load_path(filepath)

    def load_path(self, filepath):
        # SVG and CNC G-code (pen on Z, e.g. Inkscape gcodetools) are re-drawn in this machine's
        # degrees with one T line per layer / object; machine-ready G-code is kept as is.
        cfg, _ = preview_cfg(self.cfg)
        try:
            if filepath.lower().endswith(".svg"):
                lines = plain(cfg, load_layers(filepath, cfg))
            else:
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    raw = f.read().splitlines()
                if uses_z_for_pen(raw):
                    messagebox.showinfo("CNC G-code 변환", "Z로 펜을 올리고 내리는 CNC G-code입니다.\n에그봇 좌표(도)로 다시 그리고, Z는 서보 명령으로 바꿉니다.\n그림 크기는 [그림 가로 폭]과 계란 한 바퀴(360°)에 맞춥니다.\n\n객체(path id)마다 레이어 하나로 나눕니다.")
                lines = machine_lines(raw, cfg)
        except (ValueError, config.MeasurementNeeded) as e:
            messagebox.showerror("파일 열기 실패", str(e))
            return

        self.input_filepath = filepath
        self.source_lines = lines
        self.gcode_lines = [l + '\n' for l in lines]

        filename = os.path.basename(filepath)
        self.lbl_file_info.config(text=f"📂 현재 파일: {filename}", fg="#0F172A")

        self.is_converted = False
        self.process_loaded_data()

    def create_main_ui(self):
        self.main_container = tk.Frame(self.root, bg="#F1F5F9")
        self.main_container.pack(fill="both", expand=True, padx=15, pady=15)


        left_frame = tk.Frame(self.main_container, bg="#FFFFFF", width=360, highlightthickness=1, highlightbackground="#E2E8F0")
        left_frame.pack(side="left", fill="y", padx=(0, 10))
        left_frame.pack_propagate(False)


        self.lbl_file_info = tk.Label(left_frame, text="📂 현재 열린 파일: 없음", font=("맑은 고딕", 9, "bold"), bg="#FFFFFF", fg="#64748B", anchor="w")
        self.lbl_file_info.pack(fill="x", padx=20, pady=(15, 5))

        file_btn_frame = tk.Frame(left_frame, bg="#FFFFFF")
        file_btn_frame.pack(fill="x", padx=20, pady=(0, 15))

        btn_open = tk.Button(file_btn_frame, text="📁 파일 열기", bg="#2563EB", fg="white", font=("맑은 고딕", 9, "bold"), relief="flat", cursor="hand2", command=self.load_file_dialog, pady=7)
        btn_open.pack(side="left", fill="x", expand=True, padx=(0, 4))
        btn_change = tk.Button(file_btn_frame, text="🔄 파일 변경", bg="#475569", fg="white", font=("맑은 고딕", 9, "bold"), relief="flat", cursor="hand2", command=self.load_file_dialog, pady=7)
        btn_change.pack(side="right", fill="x", expand=True, padx=(4, 0))


        tk.Label(left_frame, text="⚙️ 기계 물리 설정", font=("맑은 고딕", 11, "bold"), bg="#FFFFFF", fg="#1E293B").pack(anchor="w", padx=20, pady=(10, 2))
        tk.Label(left_frame, text="기계 물리 설정은 아래 버튼을 눌러 변경하세요.", font=("맑은 고딕", 8), bg="#FFFFFF", fg="#64748B").pack(anchor="w", padx=20, pady=(0, 5))

        btn_machine_settings = tk.Button(left_frame, text="⚙️ 기계 물리 설정 열기", bg="#475569", fg="white", font=("맑은 고딕", 9, "bold"), relief="flat", cursor="hand2", command=self.open_machine_settings_popup, pady=7)
        btn_machine_settings.pack(fill="x", padx=20, pady=(0, 15))


        tk.Label(left_frame, text="🎨 레이어(색상) 매칭", font=("맑은 고딕", 11, "bold"), bg="#FFFFFF", fg="#1E293B").pack(anchor="w", padx=20, pady=(10, 5))

        frame_layer = tk.Frame(left_frame, bg="#FFFFFF", highlightthickness=1, highlightbackground="#E2E8F0")
        frame_layer.pack(fill="both", expand=True, padx=20, pady=5)

        self.canvas_layer = tk.Canvas(frame_layer, bg="#FFFFFF", highlightthickness=0)
        self.scroll_layer = ttk.Scrollbar(frame_layer, orient="vertical", command=self.canvas_layer.yview)
        self.inner_layer_frame = tk.Frame(self.canvas_layer, bg="#FFFFFF")

        self.canvas_layer.configure(yscrollcommand=self.scroll_layer.set)
        self.scroll_layer.pack(side="right", fill="y")
        self.canvas_layer.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        self.canvas_window = self.canvas_layer.create_window((0, 0), window=self.inner_layer_frame, anchor="nw")

        self.inner_layer_frame.bind("<Configure>", lambda e: self.canvas_layer.configure(scrollregion=self.canvas_layer.bbox("all")))
        self.canvas_layer.bind("<Configure>", lambda e: self.canvas_layer.itemconfig(self.canvas_window, width=e.width))

        frame_btns = tk.Frame(left_frame, bg="#FFFFFF")
        frame_btns.pack(fill="x", padx=20, pady=15)
        self.btn_apply_atc = tk.Button(frame_btns, text="✨ ATC 6단계 변환", bg="#3B82F6", fg="white", font=("맑은 고딕", 10, "bold"), relief="flat", cursor="hand2", command=self.apply_atc_conversion, pady=10)
        self.btn_apply_atc.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.btn_save = tk.Button(frame_btns, text="💾 G-code 저장", bg="#10B981", fg="white", font=("맑은 고딕", 10, "bold"), relief="flat", cursor="hand2", command=self.save_file, pady=10)
        self.btn_save.pack(side="right", fill="x", expand=True, padx=(5, 0))


        right_container = tk.Frame(self.main_container, bg="#F1F5F9")
        right_container.pack(side="left", fill="both", expand=True)

        mid_frame = tk.Frame(right_container, bg="#F1F5F9")
        mid_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))

        frame_editor = tk.Frame(mid_frame, bg="#FFFFFF", highlightthickness=1, highlightbackground="#E2E8F0")
        frame_editor.pack(fill="both", expand=True, pady=(0, 10))

        header_edit_frame = tk.Frame(frame_editor, bg="#FFFFFF")
        header_edit_frame.pack(fill="x", padx=15, pady=(15, 5))

        tk.Label(header_edit_frame, text="📄 G-Code 스크립트 에디터", font=("맑은 고딕", 12, "bold"), bg="#FFFFFF", fg="#1E293B").pack(side="left")
        btn_code_table = tk.Button(header_edit_frame, text="📖 실무 코드표", font=("맑은 고딕", 9, "bold"), bg="#E2E8F0", fg="#334155", relief="flat", cursor="hand2", command=self.open_code_table_popup, padx=8, pady=2)
        btn_code_table.pack(side="right")

        listbox_wrap = tk.Frame(frame_editor, bg="#FFFFFF")
        listbox_wrap.pack(fill="both", expand=True, padx=15, pady=5)

        self.scrollbar_list = ttk.Scrollbar(listbox_wrap, orient="vertical")
        self.listbox_gcode = tk.Listbox(listbox_wrap, yscrollcommand=self.scrollbar_list.set, font=("Consolas", 11), bg="#F8FAFC", fg="#0F172A", selectbackground="#3B82F6", selectforeground="#FFFFFF", relief="flat", highlightthickness=1, highlightbackground="#CBD5E1", activestyle="none")
        self.scrollbar_list.config(command=self.listbox_gcode.yview)

        self.scrollbar_list.pack(side="right", fill="y")
        self.listbox_gcode.pack(side="left", fill="both", expand=True)
        self.listbox_gcode.bind('<<ListboxSelect>>', self.on_listbox_select)

        frame_edit_input = tk.Frame(frame_editor, bg="#F8FAFC", pady=8, padx=10, highlightthickness=1, highlightbackground="#E2E8F0")
        frame_edit_input.pack(fill="x", padx=15, pady=(5, 15))
        tk.Label(frame_edit_input, text="라인 편집:", font=("맑은 고딕", 9, "bold"), bg="#F8FAFC", fg="#475569").pack(side="left", padx=(0, 5))
        self.entry_edit = ttk.Entry(frame_edit_input, font=("Consolas", 10))
        self.entry_edit.pack(side="left", fill="x", expand=True, padx=5)
        tk.Button(frame_edit_input, text="수정 적용", bg="#475569", fg="white", font=("맑은 고딕", 9, "bold"), relief="flat", cursor="hand2", command=self.update_single_line, padx=10, pady=3).pack(side="right")

        frame_desc = tk.Frame(mid_frame, bg="#FFFFFF", height=280, highlightthickness=1, highlightbackground="#E2E8F0")
        frame_desc.pack(fill="x", expand=False)
        frame_desc.pack_propagate(False)

        tk.Label(frame_desc, text="💡 실시간 코드 분석", font=("맑은 고딕", 12, "bold"), bg="#FFFFFF", fg="#1E293B").pack(anchor="w", padx=15, pady=(12, 5))
        self.txt_desc = tk.Text(frame_desc, height=11, font=("맑은 고딕", 10), wrap="word", bg="#F8FAFC", fg="#334155", relief="flat", state="disabled", padx=10, pady=5)
        self.txt_desc.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        right_frame = tk.Frame(right_container, bg="#F1F5F9", width=420)
        right_frame.pack(side="right", fill="both", expand=False)
        right_frame.pack_propagate(False)

        frame_preview = tk.Frame(right_frame, bg="#FFFFFF", highlightthickness=1, highlightbackground="#E2E8F0")
        frame_preview.pack(fill="both", expand=True, pady=(0, 10))

        tk.Label(frame_preview, text="🖼️ 2D 시각화 프리뷰", font=("맑은 고딕", 12, "bold"), bg="#FFFFFF", fg="#1E293B").pack(anchor="w", padx=15, pady=(15, 0))
        self.canvas_preview = tk.Canvas(frame_preview, bg="#F8FAFC", highlightthickness=1, highlightbackground="#CBD5E1")
        self.canvas_preview.pack(fill="both", expand=True, padx=15, pady=15)

        frame_play = tk.Frame(right_frame, bg="#FFFFFF", highlightthickness=1, highlightbackground="#E2E8F0", pady=12, padx=15)
        frame_play.pack(fill="x", side="bottom")

        self.btn_play = tk.Button(frame_play, text="▶ 재생", font=("맑은 고딕", 10, "bold"), bg="#10B981", fg="white", relief="flat", command=self.toggle_play, width=7, cursor="hand2")
        self.btn_play.pack(side="left")

        self.combo_speed = ttk.Combobox(frame_play, values=["0.5배속 (느리게)", "1배속", "2배속", "5배속", "10배속"], state="readonly", width=14, font=("맑은 고딕", 9))
        self.combo_speed.current(1)
        self.combo_speed.pack(side="left", padx=8)

        self.lbl_sim_code = tk.Label(frame_play, text="대기 중", font=("Consolas", 10, "bold"), bg="#FFFFFF", fg="#334155", anchor="w")
        self.lbl_sim_code.pack(side="left", fill="x", expand=True, padx=(5, 0))


    def on_listbox_select(self, event):
        if self.is_playing: return
        selection = self.listbox_gcode.curselection()
        if not selection: return
        index = selection[0]
        line_text = self.gcode_lines[index].strip('\n')
        self.entry_edit.delete(0, tk.END)
        self.entry_edit.insert(0, line_text)
        self.update_explanation(line_text)
        self.highlight_on_preview_manual(line_text)


    def update_single_line(self):
        selection = self.listbox_gcode.curselection()
        if not selection: return
        index = selection[0]
        self.gcode_lines[index] = self.entry_edit.get() + '\n'
        if not self.is_converted:
            self.source_lines[index] = self.entry_edit.get()
        self.refresh_listbox()
        self.listbox_gcode.selection_set(index)
        self.draw_preview(faint=False)

    def open_machine_settings_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("기계 물리 설정")
        popup.geometry("420x640")
        popup.config(bg="#FFFFFF")
        popup.transient(self.root)
        popup.grab_set()

        tk.Label(popup, text="⚙️ 기계 물리 설정", font=("맑은 고딕", 13, "bold"), bg="#FFFFFF", fg="#1E293B").pack(pady=(20, 5))
        tk.Label(popup, text="설정값을 변경한 후 저장 버튼을 누르세요.\nTODO = 아직 안 잰 값 (저장은 machine.toml에 됩니다)", font=("맑은 고딕", 9), bg="#FFFFFF", fg="#64748B").pack(pady=(0, 15))

        frame = tk.Frame(popup, bg="#F8FAFC", highlightthickness=1, highlightbackground="#E2E8F0", padx=15, pady=15)
        frame.pack(fill="both", expand=True, padx=20, pady=5)

        entries = {}
        for idx, (label_text, key) in enumerate(SETTINGS):
            tk.Label(frame, text=f"{label_text}:", font=("맑은 고딕", 9, "bold"), bg="#F8FAFC", fg="#334155").grid(row=idx, column=0, sticky="w", pady=6)
            entry = ttk.Entry(frame, width=12, font=("Consolas", 10))
            entry.insert(0, str(config.get(self.cfg, key, "")))
            entry.grid(row=idx, column=1, sticky="e", pady=6, padx=(10, 0))
            entries[key] = entry

        def save_settings():
            text = self.cfg_path.read_text(encoding="utf-8")
            try:
                for key, entry in entries.items():
                    text = set_toml_value(text, key, parse_value(entry.get()))
            except KeyError as k:
                messagebox.showerror("입력 오류", f"machine.toml에 {k} 항목이 없습니다.", parent=popup)
                return
            self.cfg_path.write_text(text, encoding="utf-8")
            self.cfg = config.load(self.cfg_path)
            todo = config.missing(self.cfg)
            note = "모든 값을 쟀습니다." if not todo else f"아직 안 잰 값: {len(todo)}개 (미리보기만 가능)"
            messagebox.showinfo("저장 완료", f"machine.toml에 저장했습니다.\n{note}", parent=popup)
            popup.destroy()
            if self.input_filepath: self.load_path(self.input_filepath)  # redraw with the new values

        btn_save = tk.Button(popup, text="💾 설정 저장 및 닫기", bg="#10B981", fg="white", font=("맑은 고딕", 10, "bold"), relief="flat", cursor="hand2", command=save_settings, pady=10)
        btn_save.pack(fill="x", padx=20, pady=(15, 20))

    def open_code_table_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("실무 핵심 G/M 코드 레퍼런스")
        popup.geometry("1100x580")
        popup.config(bg="#FFFFFF")

        tk.Label(popup, text="📖 실무 핵심 G코드 및 M코드 완벽 가이드", font=("맑은 고딕", 13, "bold"), bg="#FFFFFF", fg="#1E293B").pack(pady=15)

        tables_frame = tk.Frame(popup, bg="#FFFFFF")
        tables_frame.pack(fill="both", expand=True, padx=20, pady=5)

        left_box = tk.Frame(tables_frame, bg="#FFFFFF")
        left_box.pack(side="left", fill="both", expand=True, padx=(0, 10))

        tree_all = ttk.Treeview(left_box, columns=("code", "name", "desc"), show="headings", height=14)
        tree_all.heading("code", text="코드")
        tree_all.heading("name", text="명령 이름")
        tree_all.heading("desc", text="실무 설명 및 에그봇 연동")
        tree_all.column("code", width=70, anchor="center")
        tree_all.column("name", width=130, anchor="center")
        tree_all.column("desc", width=290, anchor="w")
        tree_all.pack(fill="both", expand=True)

        practical_codes = [
            ("G00", "🟦 G00 (빠른 위치 결정)", "펜을 든 상태로 지정 좌표로 고속 이동"),
            ("G01", "🟦 G01 (직선 보간 가공)", "펜을 내리고 지정 속도로 선을 그으며 이동"),
            ("G02", "G02 (시계방향 원호)", "CW 방향으로 원호 또는 호 가공"),
            ("G03", "G03 (반시계 원호)", "CCW 방향으로 원호 또는 호 가공"),
            ("G04", "🟦 G04 (Dwell / 일시정지)", "지정 시간 대기. GRBL은 초 단위 (예: P0.3 = 0.3초)"),
            ("G28", "🟦 G28 (원점 복귀)", "기계 기준 홈(Home) 위치로 복귀"),
            ("G90", "🟦 G90 (절대 좌표계)", "절대값 지정 방식 (Absolute Programming)"),
            ("M03", "🟦 M03 (서보 PWM)", "S값(0~255)으로 펜 올리기/내리기. 값은 기계 설정의 펜 올림/내림 명령"),
            ("M05", "🟦 M05 (PWM 끄기)", "듀티 0이라 서보가 끝까지 튐. 에그봇에서는 쓰지 않음"),
            ("M30", "🟦 M30 (프로그램 종료 및 복귀)", "프로그램 종료 후 처음으로 되감기")
        ]

        for item in practical_codes:
            tree_all.insert("", tk.END, values=item)

        right_box = tk.Frame(tables_frame, bg="#FFFFFF")
        right_box.pack(side="right", fill="both", expand=True, padx=(10, 0))
        tk.Label(right_box, text="🔍 현재 파일에 사용된 실무 코드 분석", font=("맑은 고딕", 10, "bold"), bg="#FFFFFF", fg="#2563EB").pack(anchor="w", pady=(0, 27))

        tree_file = ttk.Treeview(right_box, columns=("code", "count", "desc"), show="headings", height=14)
        tree_file.heading("code", text="발견된 코드")
        tree_file.heading("count", text="사용 횟수")
        tree_file.heading("desc", text="파일 내 의미 요약")
        tree_file.column("code", width=80, anchor="center")
        tree_file.column("count", width=70, anchor="center")
        tree_file.column("desc", width=210, anchor="w")

        found_stats = {}
        code_desc_map = {
            "G00": "고속 위치 결정 (G0/G00)", "G01": "직선 가공 (G1/G01)", "G02": "시계방향 원호", "G03": "반시계방향 원호",
            "G04": "일시 정지 (G4)", "G28": "원점 복귀", "G90": "절대 좌표계", "M03": "펜 상승/하강 제어",
            "M05": "그리기 정지", "M30": "프로그램 종료", "T": "펜 번호 지정", "A": "슬라이드 전후진", "Z": "매거진 회전"
        }

        for line in self.gcode_lines:
            upper = line.upper().strip()
            if not upper or upper.startswith(';'): continue
            match = re.search(r'([GM])\s*(\d+)', upper)
            if match:
                prefix, num_str = match.groups()
                normalized = f"{prefix}{int(num_str):02d}"
                if normalized in code_desc_map:
                    found_stats[normalized] = found_stats.get(normalized, 0) + 1
            else:
                if 'A' in upper: found_stats['A'] = found_stats.get('A', 0) + 1
                if 'Z' in upper: found_stats['Z'] = found_stats.get('Z', 0) + 1
                if 'T' in upper: found_stats['T'] = found_stats.get('T', 0) + 1

        if found_stats:
            for k, cnt in found_stats.items():
                tree_file.insert("", tk.END, values=(k, f"{cnt}회", code_desc_map.get(k, "기타 제어 명령어")))
        else:
            tree_file.insert("", tk.END, values=("없음", "0회", "파싱된 유효 코드가 없습니다."))

        tree_file.pack(fill="both", expand=True)
        tk.Button(popup, text="닫기", bg="#475569", fg="white", font=("맑은 고딕", 9, "bold"), relief="flat", command=popup.destroy, width=12, pady=5).pack(pady=15)

    def process_loaded_data(self):
        self.refresh_listbox()
        self.analyze_layers()
        self.draw_preview(faint=False)

    def refresh_listbox(self):
        self.listbox_gcode.delete(0, tk.END)
        for line in self.gcode_lines:
            self.listbox_gcode.insert(tk.END, line.strip('\n'))

    def analyze_layers(self):
        # A layer starts at each tool line (T<n>, M6 T<n>, N10 T2 M6). SVG files get one per Inkscape
        # layer when opened. Path ids and comment words are not used: they change on every path.
        self.change_points = []
        self.layer_names = []
        self.layer_tools = []

        for idx, line in enumerate(self.source_lines):
            match = _TOOL.match(line)
            if match:
                self.change_points.append(idx)
                self.layer_tools.append(int(match.group(1)))
                self.layer_names.append(f"T{match.group(1)}")

        self.update_layer_ui()

    def update_layer_ui(self):
        for widget in self.inner_layer_frame.winfo_children(): widget.destroy()
        self.layer_combos = []

        if self.is_converted:

            tk.Label(self.inner_layer_frame, text="✅ 변환 적용 완료" if not self.preview_only else "👀 미리보기 (안 잰 값 있음, 저장 불가)", fg="#10B981" if not self.preview_only else "#EF4444", bg="#FFFFFF", font=("맑은 고딕", 11, "bold")).pack(pady=(15, 10))

            for i, color in enumerate(self.applied_colors):
                lname = self.layer_names[i] if i < len(self.layer_names) else f"Layer {i+1}"
                row = tk.Frame(self.inner_layer_frame, bg="#F8FAFC", padx=10, pady=5, highlightthickness=1, highlightbackground="#E2E8F0")
                row.pack(fill="x", padx=10, pady=3)
                tk.Label(row, text=f"[{lname}]", font=("Consolas", 9, "bold"), fg="#475569", bg="#F8FAFC").pack(side="left")
                tk.Label(row, text=f"➔ {color}", font=("맑은 고딕", 9, "bold"), fg="#2563EB", bg="#F8FAFC").pack(side="right")

            btn_revert = tk.Button(self.inner_layer_frame, text="🔄 레이어를 다시 변동하시겠습니까?", bg="#F8FAFC", fg="#EF4444", font=("맑은 고딕", 9, "bold", "underline"), relief="flat", cursor="hand2", command=self.revert_layer_ui)
            btn_revert.pack(pady=(15, 10))

        else:
            if len(self.change_points) <= 1:
                tk.Label(self.inner_layer_frame, text="✅ 단일 레이어 파일입니다.\n(시작 시 자동 장착/반납)", fg="#10B981", bg="#FFFFFF", font=("맑은 고딕", 9, "bold"), justify="center").pack(pady=10)
                row = tk.Frame(self.inner_layer_frame, bg="#FFFFFF")
                row.pack(fill="x", padx=5, pady=8)
                lname = self.layer_names[0] if self.layer_names else "T1"
                tk.Label(row, text=f"{lname} 색상:", bg="#FFFFFF", font=("맑은 고딕", 9, "bold"), fg="#475569").pack(side="left")
                combo = ttk.Combobox(row, values=self.color_list, state="readonly", width=8, font=("맑은 고딕", 9))
                combo.current((self.layer_tools[0] - 1) % len(self.color_list) if self.layer_tools else 0)
                combo.pack(side="right")
                self.layer_combos.append(combo)
            else:
                for i in range(len(self.change_points)):
                    row = tk.Frame(self.inner_layer_frame, bg="#FFFFFF")
                    row.pack(fill="x", padx=5, pady=8)
                    lname = self.layer_names[i] if i < len(self.layer_names) else f"Layer {i+1}"
                    tk.Label(row, text=f"[{lname}] 색상:", bg="#FFFFFF", font=("맑은 고딕", 9, "bold"), fg="#475569").pack(side="left")
                    combo = ttk.Combobox(row, values=self.color_list, state="readonly", width=8, font=("맑은 고딕", 9))
                    combo.current((self.layer_tools[i] - 1) % len(self.color_list))
                    combo.pack(side="right")
                    self.layer_combos.append(combo)

        self.inner_layer_frame.update_idletasks()
        self.canvas_layer.configure(scrollregion=self.canvas_layer.bbox("all"))

    def revert_layer_ui(self):

        self.is_converted = False
        self.preview_only = False
        self.gcode_lines = [l + '\n' for l in self.source_lines]
        self.process_loaded_data()

    def apply_atc_conversion(self):
        if not self.source_lines:
            messagebox.showwarning("파일 없음", "먼저 [파일 열기] 버튼을 눌러 G-code 또는 SVG 파일을 불러주세요.")
            return

        # Read the colour boxes as the user left them (re-analysing first would reset them).
        selected_colors = self.applied_colors if self.is_converted else [combo.get() for combo in self.layer_combos]
        tools = [self.color_list.index(c) + 1 for c in selected_colors]

        cfg, todo = preview_cfg(self.cfg)
        try:
            lines = insert_tool_changes(cfg, map_tools(self.source_lines, self.change_points, tools))
        except (ValueError, config.MeasurementNeeded) as e:
            messagebox.showerror("변환 실패", str(e))
            return

        self.gcode_lines = [l + '\n' for l in lines]
        self.applied_colors = selected_colors
        self.is_converted = True
        self.preview_only = bool(todo)

        self.refresh_listbox()
        self.update_layer_ui()
        self.draw_preview(faint=False)
        if todo:
            messagebox.showwarning("미리보기 전용", f"아직 안 잰 값이 {len(todo)}개 있어 임시값으로 변환했습니다.\n미리보기·재생은 되지만 저장은 막힙니다.\n\n[⚙️ 기계 물리 설정]에서 채우세요:\n" + "\n".join(todo))
        else:
            messagebox.showinfo("변환 완료", "설정된 색상으로 펜 교체 동작을 넣었습니다.\n우측 하단의 [▶ 재생] 버튼으로 확인해보세요.")

    def save_file(self):
        if not self.gcode_lines:
            messagebox.showwarning("파일 없음", "저장할 G-code 내용이 없습니다.")
            return
        if not self.is_converted:
            messagebox.showwarning("변환 전", "먼저 [✨ ATC 6단계 변환]을 눌러 펜 교체 동작을 넣으세요.")
            return
        if self.preview_only:
            messagebox.showerror("저장 불가", "아직 안 잰 값이 있어 미리보기만 됩니다.\n[⚙️ 기계 물리 설정]에서 채운 뒤 다시 변환하세요:\n\n" + "\n".join(config.missing(self.cfg)))
            return
        out_path = filedialog.asksaveasfilename(defaultextension=".gcode", filetypes=[("G-code", "*.gcode")])
        if out_path:
            with open(out_path, 'w', encoding='utf-8') as f: f.writelines(self.gcode_lines)
            messagebox.showinfo("저장 완료", "성공적으로 저장되었습니다.")

    def update_explanation(self, line):
        self.txt_desc.config(state="normal")
        self.txt_desc.delete(1.0, tk.END)
        stripped = line.strip()
        upper = stripped.upper()
        exp = f"📌 원본 코드: {stripped}\n\n"

        if upper.startswith(';') or (stripped.startswith('(') and stripped.endswith(')')):
            exp += "▶ 주석(Comment) 또는 메타데이터 구간입니다.\n"
            if upper.startswith("; ATC T"):
                exp += "▶ 펜 교체 시작: 펜 올림 → 기존 펜 반납 → 새 펜 장착 → 그리던 자리로 복귀\n"
        else:
            if 'G00' in upper or 'G0 ' in upper: exp += "▶ 명령어 [G00]: 펜을 든 상태로 지정 좌표까지 고속 이동\n"
            elif 'G01' in upper or 'G1 ' in upper: exp += "▶ 명령어 [G01]: 지정 속도로 직선 이동 (펜이 내려가 있으면 선을 그림)\n"
            elif 'G02' in upper or re.search(r'\bG2\b', upper): exp += "▶ 명령어 [G02]: 시계 방향(CW) 원호 가공\n"
            elif 'G03' in upper or re.search(r'\bG3\b', upper): exp += "▶ 명령어 [G03]: 반시계 방향(CCW) 원호 가공\n"
            elif 'G04' in upper or re.search(r'\bG4\b', upper): exp += "▶ 명령어 [G04]: 지정 시간 일시 정지 (Dwell, P는 초 단위)\n"
            elif 'M03' in upper or 'M3' in upper: exp += "▶ 명령어 [M03]: 펜 올리기/내리기 (PWM 제어)\n"
            elif 'M30' in upper: exp += "▶ 명령어 [M30]: 프로그램 종료 및 처음으로 되감기\n"

            coord_parts = []
            x_m, y_m, z_m = re.search(r'X([-\d.]+)', upper), re.search(r'Y([-\d.]+)', upper), re.search(r'Z([-\d.]+)', upper)
            a_m, f_m, s_m = re.search(r'A([-\d.]+)', upper), re.search(r'F([-\d.]+)', upper), re.search(r'S([-\d.]+)', upper)
            p_m = re.search(r'P([-\d.]+)', upper)

            if x_m: coord_parts.append(f"X = {x_m.group(1)} (펜 암 회전, 도)")
            if y_m: coord_parts.append(f"Y = {y_m.group(1)} (계란 회전, 도)")
            if z_m: coord_parts.append(f"Z = {z_m.group(1)} (매거진 펜 보관통 회전, 도)")
            if a_m: coord_parts.append(f"A = {a_m.group(1)} (슬라이드 전후진, mm)")
            if f_m: coord_parts.append(f"F = {f_m.group(1)} (이송 속도)")
            if s_m: coord_parts.append(f"S = {s_m.group(1)} (서보 PWM 듀티)")
            if p_m and ('G4' in upper or 'G04' in upper): coord_parts.append(f"P = {p_m.group(1)} (대기 시간, 초)")

            if coord_parts:
                exp += "▶ 세부 파라미터 및 공식 축 분석:\n  - " + "\n  - ".join(coord_parts) + "\n"

        self.txt_desc.insert(tk.END, exp)
        self.txt_desc.config(state="disabled")

    def draw_preview(self, faint=False):
        # Moves inside a pen change (park, slide, magazine) are left out so they don't stretch the drawing.
        self.canvas_preview.delete("all")
        self.path_coords = []
        current_x, current_y = 0.0, 0.0
        min_x, max_x, min_y, max_y = float('inf'), float('-inf'), float('inf'), float('-inf')

        has_coord = False
        in_atc = False
        for line in self.gcode_lines:
            if line.startswith("; ATC T"): in_atc = True
            elif line.startswith("; ATC end"): in_atc = False
            upper = line.upper()
            if in_atc or upper.startswith(";"): continue
            if 'G0' in upper or 'G1' in upper or 'G2' in upper or 'G3' in upper:
                if 'G92' in upper: continue
                x_match = re.search(r'X([-\d.]+)', upper)
                y_match = re.search(r'Y([-\d.]+)', upper)
                if x_match: current_x = float(x_match.group(1))
                if y_match: current_y = float(y_match.group(1))
                self.path_coords.append((current_x, current_y))
                min_x, max_x = min(min_x, current_x), max(max_x, current_x)
                min_y, max_y = min(min_y, current_y), max(max_y, current_y)
                has_coord = True

        if not has_coord or not self.path_coords: return

        self.canvas_preview.update()
        cw, ch = max(self.canvas_preview.winfo_width(), 350), max(self.canvas_preview.winfo_height(), 350)
        pad = 40
        width, height = max(max_x - min_x, 1e-5), max(max_y - min_y, 1e-5)

        # X (arm deg) and Y (egg deg) scale separately: an unrolled egg map, not true shape.
        self.scale_x = (cw - pad * 2) / width
        self.scale_y = (ch - pad * 2) / height
        self.data_center_x, self.data_center_y = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
        self.canvas_center_x, self.canvas_center_y = cw / 2.0, ch / 2.0

        line_color = "#E2E8F0" if faint else "#0F172A"
        for i in range(len(self.path_coords) - 1):
            x1, y1 = self.transform_coord(self.path_coords[i])
            x2, y2 = self.transform_coord(self.path_coords[i + 1])
            self.canvas_preview.create_line(x1, y1, x2, y2, fill=line_color, width=1.5, tags="base_path")

    def transform_coord(self, coord):
        x, y = coord
        cx = self.canvas_center_x + (x - self.data_center_x) * self.scale_x
        cy = self.canvas_center_y - (y - self.data_center_y) * self.scale_y
        return cx, cy

    def highlight_on_preview_manual(self, line):
        x_match, y_match = re.search(r'X([-\d.]+)', line.upper()), re.search(r'Y([-\d.]+)', line.upper())
        if x_match or y_match:
            try:
                x = float(x_match.group(1)) if x_match else 0
                y = float(y_match.group(1)) if y_match else 0
                cx, cy = self.transform_coord((x, y))
                if self.current_preview_dot: self.canvas_preview.delete(self.current_preview_dot)
                self.current_preview_dot = self.canvas_preview.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill="#EF4444", outline="#B91C1C", width=2)
            except: pass

    def toggle_play(self):
        if not self.gcode_lines:
            messagebox.showwarning("파일 없음", "시뮬레이션할 파일이 없습니다.")
            return
        if self.is_playing:
            self.is_playing = False
            self.btn_play.config(text="▶ 재생", bg="#10B981")
            self.lbl_sim_code.config(text="시뮬레이션 일시 정지됨.")
        else:
            self.is_playing = True
            self.btn_play.config(text="⏸ 정지", bg="#EF4444")
            if self.sim_index == 0 or self.sim_index >= len(self.gcode_lines):
                self.sim_index = 0
                self.sim_current_x, self.sim_current_y = 0.0, 0.0
                self.sim_last_cx, self.sim_last_cy = None, None
                self.sim_in_atc = False
                self.draw_preview(faint=True)
                if self.current_preview_dot: self.canvas_preview.delete(self.current_preview_dot)
            self.run_simulation_step()

    def run_simulation_step(self):
        if not self.is_playing: return
        if self.sim_index >= len(self.gcode_lines):
            self.toggle_play()
            self.lbl_sim_code.config(text="🎉 시뮬레이션 완료!")
            self.sim_index = 0
            return

        line = self.gcode_lines[self.sim_index].strip()
        upper = line.upper()

        self.listbox_gcode.selection_clear(0, tk.END)
        self.listbox_gcode.selection_set(self.sim_index)
        self.listbox_gcode.see(self.sim_index)
        self.update_explanation(line)

        display_line = line if len(line) < 35 else line[:32] + "..."

        if line.startswith("; ATC T"): self.sim_in_atc = True
        if self.sim_in_atc:
            self.lbl_sim_code.config(text=f"🔄 펜 교체 중 ({display_line})")
            self.show_atc_effect("🔄 장착/교체/반납 로직 실행 중...")
        else:
            self.lbl_sim_code.config(text=f"동작 중 ⚡ {display_line}")
        if line.startswith("; ATC end"): self.sim_in_atc = False

        x_match, y_match = re.search(r'X([-\d.]+)', upper), re.search(r'Y([-\d.]+)', upper)
        moved = False
        if not self.sim_in_atc and not upper.startswith(";") and 'G92' not in upper:
            if x_match:
                self.sim_current_x = float(x_match.group(1))
                moved = True
            if y_match:
                self.sim_current_y = float(y_match.group(1))
                moved = True

        if moved:
            cx, cy = self.transform_coord((self.sim_current_x, self.sim_current_y))
            if self.sim_last_cx is not None and (self.sim_last_cx != cx or self.sim_last_cy != cy):
                if 'G1' in upper or 'G2' in upper or 'G3' in upper:
                    self.canvas_preview.create_line(self.sim_last_cx, self.sim_last_cy, cx, cy, fill="#0F172A", width=2, tags="sim_path")
                elif 'G0' in upper:
                    self.canvas_preview.create_line(self.sim_last_cx, self.sim_last_cy, cx, cy, fill="#94A3B8", width=1, dash=(2, 2), tags="sim_path")
            self.sim_last_cx, self.sim_last_cy = cx, cy
            if self.current_preview_dot: self.canvas_preview.delete(self.current_preview_dot)
            self.current_preview_dot = self.canvas_preview.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill="#EF4444", outline="#B91C1C", width=2)

        self.sim_index += 1
        speed_str = self.combo_speed.get()
        delay = 100
        if "0.5배속" in speed_str: delay = 300
        elif "1배속" in speed_str: delay = 100
        elif "2배속" in speed_str: delay = 40
        elif "5배속" in speed_str: delay = 15
        elif "10배속" in speed_str: delay = 5

        self.root.after(delay, self.run_simulation_step)

    def show_atc_effect(self, text):
        if self.atc_effect_text: self.canvas_preview.delete(self.atc_effect_text)
        cw, ch = self.canvas_preview.winfo_width(), self.canvas_preview.winfo_height()
        if cw <= 1: cw, ch = 350, 350
        self.atc_effect_text = self.canvas_preview.create_text(cw / 2, 40, text=text, font=("맑은 고딕", 11, "bold"), fill="#3B82F6")
        self.root.after(1000, lambda: self.canvas_preview.delete(self.atc_effect_text) if self.atc_effect_text else None)


def main():
    root = tk.Tk()
    UltimateATCGCodeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
