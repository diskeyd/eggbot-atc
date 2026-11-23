#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
에그봇 G코드 생성기  (단일 파일 Tkinter 앱)

필요 라이브러리
    Pillow    (이미지 열기)       python -m pip install pillow
    pyserial  (GRBL 기계 연결)   python -m pip install pyserial   (선택)

실행
    python eggbot_app.py

주의
    * M3 S(서보) 값과 축 배치는 기계/펌웨어마다 다릅니다.
      '축 설정'과 펜 올림/내림 값을 실제 기계에 맞는지 꼭 확인한 뒤 그리세요.
    * 3D 창의 딤플 무늬는 표시용 근사이며 G코드에는 들어가지 않습니다.
      '딤플 보정'(펜 더 누르기/속도/반복/채우기 간격)만 G코드에 반영됩니다.
"""
from __future__ import annotations

import base64
import bisect
import io
import ast
import json
import operator
import math
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict, fields, replace as dc_replace

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox, colorchooser

try:
    from PIL import Image, ImageTk, ImageDraw, ImageFilter, ImageChops
    HAS_PIL = True
except ImportError:                       # pragma: no cover
    HAS_PIL = False

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:                       # pragma: no cover
    HAS_SERIAL = False


# =============================================================================
# 0. 설정값 묶음
# =============================================================================
MECH_DEFAULT_SCRIPT = """\
; MECH_V6  ===== 펜 교환 메커니즘 (위에서부터 차례로 실행) =====
; 기본값은 1->2 든 2->3 이든 전부 같은 공식(슬롯 각도 A/B, 접근 보정각 H, 도킹/대기 위치 DOCK/PARK 등)으로 계산해서,
; 위 'ATC 메커니즘 설정' 값을 바꾸면 아래 두 블록 모두 바로 그 값으로 다시 계산됩니다.
; 1->2 만 다르게 움직여야 하면 'when PA == 1 and PB == 2' 블록의 숫자만 직접 고치세요(그러면 그 줄만 고정됩니다).
; 변수: A=뗄 펜 슬롯각  B=끼울 펜 슬롯각  H=접근 보정각  HOME=홈각  DOCK/PARK=ATC 도킹/대기 위치
;       PA/PB=뗄/끼울 펜 번호(1~)  D=회전 방향(+1/-1)
carriage                                        ; G0 X0 Y0 (원점에서 교환)
; ---- 1번 -> 2번 (기본: 아래 공식과 동일, 필요하면 숫자로 바꿔 이 짝만 고정 가능) ----
when PA == 1 and PB == 2: turret A+H F 1000
when PA == 1 and PB == 2: atc DOCK F 500
when PA == 1 and PB == 2: wait 600
when PA == 1 and PB == 2: mount none
when PA == 1 and PB == 2: turret A F 1000
when PA == 1 and PB == 2: wait 600
when PA == 1 and PB == 2: atc DOCK/2 F 500
when PA == 1 and PB == 2: turret B F 1000
when PA == 1 and PB == 2: wait 600
when PA == 1 and PB == 2: atc DOCK F 500
when PA == 1 and PB == 2: wait 600
when PA == 1 and PB == 2: mount to
when PA == 1 and PB == 2: turret B+H F 1000
when PA == 1 and PB == 2: atc PARK F 500
when PA == 1 and PB == 2: turret HOME F 1000
when PA == 1 and PB == 2: wait 600
; ---- 그 밖의 교환 (같은 순서를 계산) ----
when PA != 1 or PB != 2: turret A+H F 1000
when PA != 1 or PB != 2: atc DOCK F 500
when PA != 1 or PB != 2: wait 600
when PA != 1 or PB != 2: mount none
when PA != 1 or PB != 2: turret A F 1000
when PA != 1 or PB != 2: wait 600
when PA != 1 or PB != 2: atc DOCK/2 F 500
when PA != 1 or PB != 2: turret B F 1000
when PA != 1 or PB != 2: wait 600
when PA != 1 or PB != 2: atc DOCK F 500
when PA != 1 or PB != 2: wait 600
when PA != 1 or PB != 2: mount to
when PA != 1 or PB != 2: turret B+H F 1000
when PA != 1 or PB != 2: atc PARK F 500
when PA != 1 or PB != 2: turret HOME F 1000
when PA != 1 or PB != 2: wait 600
"""


@dataclass
class Params:
    # 물체
    kind: str = "달걀"
    length_mm: float = 57.0
    diameter_mm: float = 43.0
    pointiness: float = 0.12
    has_dimples: bool = False       # 표면에 딤플이 있음 (3D 표시용)
    dimple_comp: bool = False       # 딤플 보정 사용 (G코드에 반영)
    dimple_press: int = 12
    dimple_speed: int = 60
    dimple_repeat: int = 1
    dimple_fill: int = 80
    # 선 추출
    threshold: int = 128
    min_region: int = 30
    simplify_mm: float = 0.03
    # 위치 / 크기
    pos_x_deg: float = 180.0
    pos_y_mm: float = 0.0
    height_mm: float = 25.0
    stretch_x: float = 1.0          # 그림 가로 배율 (1 = 원래 비율, 가로 핸들로만 바뀜)
    rot_deg: float = 0.0
    x_max_deg: float = 315.0
    # 펜 / 속도
    pen_up: int = 90
    pen_down: int = 30
    dwell_ms: int = 300
    draw_feed: int = 1500
    travel_feed: int = 3000
    # 채우기
    outline: bool = True
    return_home: bool = True         # 다 그린 뒤 펜을 올리고 원점(X0 Y0)으로 돌아옴
    zigzag: bool = True
    fill_gap_mm: float = 0.5
    fill_angle: float = 45.0
    # 선 추출 방식 (컬러 이미지도 자동으로 끝선을 딴다)
    extract_mode: str = "자동(배경 제거)"
    bg_tol: int = 45                 # 배경과 달라 보이는 정도(작을수록 예민)
    color_count: int = 6             # '색 영역 나누기' 색 개수
    color_merge: int = 40             # 비슷한 색(그라데이션 경계)을 합쳐서 외곽선이 잘게 끊기지 않게 함(0=끄기)
    # 펜 교환 (ATC: 회전식 펜 매거진)
    pen_count: int = 1
    pen_colors: str = "#1D4ED8,#DC2626,#16A34A,#111827,#F59E0B,#7C3AED"
    pen_outline: int = 1             # 테두리를 그릴 펜 번호(1~)
    pen_fill: int = 1
    pen_draw: int = 1
    pen_by_color: bool = False       # 채우기 영역 색과 가장 가까운 펜 자동 배정
    change_mode: str = "회전 매거진(ATC)"
    turret_axis: str = "Z"
    turret_step: float = 5.4         # 슬롯 간 각도(도, 소수 가능. 0=360/펜수)
    turret_off: float = 0.0
    turret_feed: int = 1000          # 도/분
    change_wait_ms: int = 600
    change_pre: str = ""             # 교환 직전/직후에 넣을 사용자 G코드 ({from} {to} {angle})
    change_post: str = ""
    # 보정 (G코드 출력에만 적용)
    cal_rot_scale: float = 1.0
    cal_h_scale: float = 1.0
    cal_rot_off: float = 0.0
    cal_h_off: float = 0.0
    # 펜 팔 '스윙' 모드 (진짜 에그봇 방식): 높이(mm) -> 펜 팔 각도(도)로 바꿔서 출력.
    # 에그봇 기본: 모터 3200스텝/바퀴(=8.889스텝/도), 펜 팔은 800스텝(=90°, 중심 기준 ±45°)만 흔들림.
    arm_swing: bool = True
    arm_len_mm: float = 40.0         # 펜 팔 회전축~펜 끝 길이(mm). 달걀 높이 ≈ 팔길이*sin(각도)
    arm_scale: float = 1.0           # 펜 팔 출력 배율 (3바퀴 돌면 0.333 처럼 1/바퀴수)
    arm_max_deg: float = 45.0        # 중심 기준 최대 스윙(±도). 에그봇 = 45
    # 원점 자동 조정
    home_use: bool = False           # 리밋 스위치 홈잉($H) 사용
    home_off_x: float = 0.0          # 홈 위치 → 달걀 원점까지 이동량(기계 X)
    home_off_y: float = 0.0
    home_feed: int = 800
    # ATC 도킹: 오른쪽 직선 레일로 좌우 이동 + 회전 턴테이블(펜 슬롯)
    atc_axis: str = "A"              # ATC 직선 이동 축 글자
    atc_park: float = 0.0            # ATC 대기(오른쪽) 위치 mm
    atc_dock: float = 50.0           # ATC 가 펜 홀더에 닿는(도킹) 위치 mm
    atc_feed: int = 500              # ATC 직선 이동 속도 mm/분
    atc_pen_h: float = 34.0          # 펜 교환할 때 펜 캐리지가 가는 위치(달걀 축 방향 mm)
    outline_follow: bool = True      # 테두리를 그 영역에 칠한 색 펜으로 그림
    dimple_auto: bool = True         # 딤플 보정 자동(딤플 위치를 알아서 피해 속도/압력/겹침 조절)

    # 펜 교환 메커니즘(스크립트 방식): 슬롯 각도는 소수점까지, 순서는 스크립트로 수정
    mech_use: bool = True
    mech_half: float = 0.0           # 접근 보정각 H (0 = 슬롯 간 각도 / 2)
    mech_home: float = 0.0           # Z 홈(대기) 각도
    mech_script: str = MECH_DEFAULT_SCRIPT
    exch_at_origin: bool = True      # 펜 교환은 항상 기계 원점(X0 Y0, 펜 팔 0도)에서 한다
    return_first_pen: bool = True    # 모든 그림이 끝나면 처음(시작) 펜으로 다시 교체해 둔다
    pen_order_area: bool = False     # False: 1번 색부터 번호 순서 / True: 칠할 면적이 큰 색부터

    def pen_hex(self):
        defaults = ["#1D4ED8", "#DC2626", "#16A34A", "#111827", "#F59E0B", "#7C3AED"]
        got = [c.strip() for c in self.pen_colors.split(",") if c.strip()]
        got = (got + defaults)[:6]
        return [c if len(c) == 7 and c.startswith("#") else defaults[i] for i, c in enumerate(got)]

    def pen_rgb(self):
        return [tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in self.pen_hex()]

    def npen(self):
        return max(1, min(3, int(self.pen_count)))   # 에그봇 1 + ATC 2

    def step_deg(self):
        return self.turret_step if self.turret_step > 0 else 360.0 / self.npen()

    def turret_deg(self, slot):
        return slot * self.step_deg() + self.turret_off


DIMPLE_R = 1.9          # 딤플 반지름 mm
DIMPLE_PITCH = 4.6      # 딤플 간격 mm
DIMPLE_DEPTH = 0.45     # 표시/계산용 깊이 mm (실제 0.2mm 안팎을 눈에 보이게 조금 과장)

EXTRACT_MODES = ("자동(배경 제거)", "색 영역 나누기", "어두운 선(밝기)")
CHANGE_MODES = ("회전 매거진(ATC)", "수동 교환(M0)")


@dataclass
class AxisMap:
    x_is_rot: bool = False      # False: Y=회전각(달걀 홀더), X=펜 팔(직선/각도)
    x_invert: bool = False
    y_invert: bool = False


# =============================================================================
# 1. 물체(달걀/공) 모양
# =============================================================================
class Shape:
    """회전축이 세로(Y)인 물체. 표면 위치는 (회전각 deg, 높이 mm)로 표현한다.

    3D 좌표 규칙:  deg=0 이 정면(+Z),  deg 가 커지면 오른쪽(+X)으로 돈다.
    """

    def __init__(self, kind, length_mm, diameter_mm, pointiness):
        self.kind = kind
        self.R = diameter_mm / 2.0
        self.half = (length_mm if kind == "달걀" else diameter_mm) / 2.0
        self.p = pointiness if kind == "달걀" else 0.0
        self.dimples = None          # enable_dimples() 하면 골프공 딤플 배치가 들어간다

    # ---- 골프공 딤플 (오목한 홈) -------------------------------------------
    def enable_dimples(self, on=True):
        if not on:
            self.dimples = None
            return
        dh = DIMPLE_PITCH * 0.866
        K = max(1, int(self.half * 0.93 / dh))
        rows = []
        for k in range(-K, K + 1):
            hr = k * dh
            n = max(4, int(self.circumference(hr) / DIMPLE_PITCH))
            step = 360.0 / n
            rows.append((hr, n, step, (k % 2) * step / 2.0))
        self.dimples = dict(rows=rows, dh=dh, K=K)

    def dimple_centers(self):
        """[(deg, h)] 딤플 중심 목록 (3D 표시용)."""
        out = []
        if self.dimples:
            for hr, n, step, off in self.dimples["rows"]:
                out += [((off + i * step) % 360.0, hr) for i in range(n)]
        return out

    def _dimple_dist(self, deg, h):
        L = self.dimples
        k0 = int(round(h / L["dh"]))
        best = 1e9
        for k in (k0 - 1, k0, k0 + 1):
            if -L["K"] <= k <= L["K"]:
                hr, n, step, off = L["rows"][k + L["K"]]
                cd = off + round((deg - off) / step) * step
                dd = math.radians((deg - cd + 180.0) % 360.0 - 180.0) * self.radius(hr)
                best = min(best, math.hypot(dd, h - hr))
        return best

    def dimple_depth(self, deg, h):
        """표면 위치 (deg,h)에서 딤플이 파인 깊이(mm). 딤플이 없으면 0."""
        if not self.dimples:
            return 0.0
        d = self._dimple_dist(deg, h)
        if d >= DIMPLE_R:
            return 0.0
        u = d / DIMPLE_R
        return DIMPLE_DEPTH * (1.0 - u * u) ** 2

    def dimple_zone(self, deg, h):
        """딤플 안쪽 또는 가장자리(펜이 걸리기 쉬운 곳)인가."""
        return bool(self.dimples) and self._dimple_dist(deg, h) < DIMPLE_R * 1.25

    def radius(self, h):
        v = max(-0.999, min(0.999, h / self.half))
        return self.R * math.sqrt(1.0 - v * v) * (1.0 + self.p * v)

    def circumference(self, h=0.0):
        return 2.0 * math.pi * self.radius(h)

    def max_radius(self):
        return self.R * (1.0 + abs(self.p) * 0.45)

    def surface(self, deg, h):
        h = max(-self.half * 0.999, min(self.half * 0.999, h))
        r = self.radius(h)
        if self.dimples:
            r -= self.dimple_depth(deg, h)
        a = math.radians(deg)
        return (r * math.sin(a), h, r * math.cos(a))


# =============================================================================
# 2. 이미지 → 마스크 → 선
# =============================================================================
def flood_label(mask, w, h, min_px):
    """4-연결 영역 라벨링. 반환: (labels[w*h] (-1=없음), 영역 크기 리스트).
    min_px 보다 작은 덩어리는 mask 에서 지워버린다(노이즈 제거)."""
    labels = [-1] * (w * h)
    sizes = []
    for start in range(w * h):
        if not mask[start] or labels[start] != -1:
            continue
        rid = len(sizes)
        stack = [start]
        labels[start] = rid
        members = []
        while stack:
            i = stack.pop()
            members.append(i)
            x = i % w
            if x > 0 and mask[i - 1] and labels[i - 1] == -1:
                labels[i - 1] = rid
                stack.append(i - 1)
            if x < w - 1 and mask[i + 1] and labels[i + 1] == -1:
                labels[i + 1] = rid
                stack.append(i + 1)
            if i >= w and mask[i - w] and labels[i - w] == -1:
                labels[i - w] = rid
                stack.append(i - w)
            if i + w < w * h and mask[i + w] and labels[i + w] == -1:
                labels[i + w] = rid
                stack.append(i + w)
        if len(members) < min_px:
            for i in members:
                labels[i] = -1
                mask[i] = 0
            # rid 재사용
        else:
            sizes.append(len(members))
    return labels, sizes


def trace_outlines(mask, w, h):
    """잉크 픽셀과 배경 사이 경계를 따라가는 닫힌 루프들(꼭짓점 좌표)."""
    W1 = w + 1
    nxt = {}          # 꼭짓점 -> [다음 꼭짓점...]

    def add(a, b):
        nxt.setdefault(a, []).append(b)

    for y in range(h):
        base = y * w
        for x in range(w):
            if not mask[base + x]:
                continue
            tl = y * W1 + x
            tr = tl + 1
            bl = tl + W1
            br = bl + 1
            if y == 0 or not mask[base - w + x]:
                add(tl, tr)                      # 위쪽 면 (→)
            if x == w - 1 or not mask[base + x + 1]:
                add(tr, br)                      # 오른쪽 면 (↓)
            if y == h - 1 or not mask[base + w + x]:
                add(br, bl)                      # 아래쪽 면 (←)
            if x == 0 or not mask[base + x - 1]:
                add(bl, tl)                      # 왼쪽 면 (↑)

    def vec(a, b):
        return (b % W1 - a % W1, b // W1 - a // W1)

    loops = []
    for s in list(nxt.keys()):
        while nxt.get(s):
            loop = [s]
            cur = s
            prev_dir = None
            while True:
                outs = nxt.get(cur)
                if not outs:
                    break
                if len(outs) == 1 or prev_dir is None:
                    nb = outs.pop()
                else:
                    # 갈림길(대각선 접점): 오른쪽으로 꺾는 쪽을 우선
                    best_i, best_c = 0, None
                    for k, cand in enumerate(outs):
                        d = vec(cur, cand)
                        cr = prev_dir[0] * d[1] - prev_dir[1] * d[0]
                        if best_c is None or cr > best_c:
                            best_i, best_c = k, cr
                    nb = outs.pop(best_i)
                prev_dir = vec(cur, nb)
                if nb == s:
                    break
                loop.append(nb)
                cur = nb
            if len(loop) >= 4:
                loops.append([(v % W1, v // W1) for v in loop])
    return loops


def dedupe_overlap(strokes, tol, step=None):
    """윤곽선끼리 겹치는 구간(맞닿은 색 영역의 공유 경계, 굵은 선의 안/밖 가장자리)을 한 번만 그리게 잘라낸다.
    strokes: [(kind, [(x,y)...], pen)]  tol: 같은 자리로 보는 거리(좌표 단위).  선 전체가 안 겹치면 원본 그대로 둔다."""
    step = step or max(0.5, tol * 0.6)
    cell = max(tol, 1e-6)
    grid = {}

    def near(x, y):
        cx, cy = int(math.floor(x / cell)), int(math.floor(y / cell))
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for (qx, qy) in grid.get((gx, gy), ()):
                    if (qx - x) ** 2 + (qy - y) ** 2 <= tol * tol:
                        return True
        return False

    def reg(x, y):
        grid.setdefault((int(math.floor(x / cell)), int(math.floor(y / cell))), []).append((x, y))

    out = []
    for kind, pl, pen in strokes:
        if kind != "outline" or len(pl) < 3:
            out.append((kind, pl, pen))
            continue
        samples = []
        for i in range(1, len(pl)):
            ax, ay = pl[i - 1]
            bx, by = pl[i]
            n = max(1, int(math.ceil(math.hypot(bx - ax, by - ay) / step)))
            for j in range(n):
                samples.append((ax + (bx - ax) * j / n, ay + (by - ay) * j / n))
        samples.append(pl[-1])
        dup = [near(x, y) for x, y in samples]
        for (x, y) in samples:
            reg(x, y)
        if not any(dup):
            out.append((kind, pl, pen))
            continue
        run = []
        pieces = []
        for s, d in zip(samples, dup):
            if d:
                if len(run) >= 2:
                    pieces.append(run)
                run = []
            else:
                run.append(s)
        if len(run) >= 2:
            pieces.append(run)
        # 닫힌 선이 시작점을 지나며 이어지는 경우 앞뒤 조각을 합친다
        if len(pieces) >= 2 and not dup[0] and not dup[-1]:
            pieces[0] = pieces[-1] + pieces[0]
            pieces.pop()
        for pc in pieces:
            if len(pc) >= 3 or math.hypot(pc[-1][0] - pc[0][0], pc[-1][1] - pc[0][1]) > 2 * step:
                out.append((kind, pc, pen))
    return out


def smooth_loop(pts, rounds=2):
    n = len(pts)
    if n < 6:
        return list(pts)
    for _ in range(rounds):
        pts = [((pts[i - 1][0] + 2 * pts[i][0] + pts[(i + 1) % n][0]) * 0.25,
                (pts[i - 1][1] + 2 * pts[i][1] + pts[(i + 1) % n][1]) * 0.25)
               for i in range(n)]
    return pts


def rdp(pts, tol):
    """Ramer-Douglas-Peucker (열린 폴리선)."""
    n = len(pts)
    if n < 3:
        return list(pts)
    keep = bytearray(n)
    keep[0] = keep[-1] = 1
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        ln = math.hypot(dx, dy) or 1e-9
        far, fi = -1.0, a
        for i in range(a + 1, b):
            d = abs((pts[i][0] - ax) * dy - (pts[i][1] - ay) * dx) / ln
            if d > far:
                far, fi = d, i
        if far > tol:
            keep[fi] = 1
            stack.append((a, fi))
            stack.append((fi, b))
    return [p for p, k in zip(pts, keep) if k]


def simplify_loop(pts, tol):
    if len(pts) < 8 or tol <= 0:
        return list(pts)
    p0 = pts[0]
    far = max(range(len(pts)), key=lambda i: (pts[i][0] - p0[0]) ** 2 + (pts[i][1] - p0[1]) ** 2)
    if far == 0:
        return list(pts)
    a = rdp(pts[:far + 1], tol)
    b = rdp(pts[far:] + [pts[0]], tol)
    return a[:-1] + b[:-1]


def hatch_runs(fill, w, h, angle_deg, gap_px):
    """해칭 선분들. 반환: {라인번호: [(s0, s1, (x0,y0), (x1,y1)), ...]}"""
    a = math.radians(angle_deg)
    ux, uy = math.cos(a), math.sin(a)          # 선 방향
    vx, vy = -uy, ux                           # 선과 수직
    cs = [(0, 0), (w, 0), (0, h), (w, h)]
    tmin = min(x * vx + y * vy for x, y in cs)
    tmax = max(x * vx + y * vy for x, y in cs)
    smin = min(x * ux + y * uy for x, y in cs)
    smax = max(x * ux + y * uy for x, y in cs)
    step = 0.6
    out = {}
    k = 0
    t = tmin + gap_px * 0.5
    while t <= tmax:
        runs = []
        s = smin
        start = None
        last = None
        while s <= smax:
            px = s * ux + t * vx
            py = s * uy + t * vy
            ix, iy = int(px), int(py)
            if 0 <= ix < w and 0 <= iy < h and fill[iy * w + ix]:
                if start is None:
                    start = s
                last = s
            elif start is not None:
                if last - start >= 1.0:
                    runs.append((start, last))
                start = None
            s += step
        if start is not None and last - start >= 1.0:
            runs.append((start, last))
        if runs:
            out[k] = [(s0, s1, (s0 * ux + t * vx, s0 * uy + t * vy),
                       (s1 * ux + t * vx, s1 * uy + t * vy)) for s0, s1 in runs]
        k += 1
        t += gap_px
    return out


def _inside_line(fill, w, h, p, q):
    n = max(2, int(math.hypot(q[0] - p[0], q[1] - p[1])) + 1)
    for i in range(n + 1):
        f = i / n
        ix = int(round(p[0] + (q[0] - p[0]) * f))
        iy = int(round(p[1] + (q[1] - p[1]) * f))
        if not (0 <= ix < w and 0 <= iy < h and fill[iy * w + ix]):
            return False
    return True


def hatch_strokes(fill, w, h, angle_deg, gap_px, zigzag):
    """해칭 선분 → 펜 한 번에 그릴 폴리선들. zigzag 면 이웃 줄을 끊지 않고 이어 붙인다."""
    lines = hatch_runs(fill, w, h, angle_deg, gap_px)
    strokes = []
    cur = None
    flip = False
    for k in sorted(lines):
        runs = sorted(lines[k], key=lambda r: r[0])
        if flip:
            runs = [(r[1], r[0], r[3], r[2]) for r in reversed(runs)]
        for r in runs:
            p, q = r[2], r[3]
            if cur is not None and zigzag and \
                    math.hypot(cur[-1][0] - p[0], cur[-1][1] - p[1]) <= gap_px * 3.0 + 3.0 and \
                    _inside_line(fill, w, h, cur[-1], p):
                cur.extend([p, q])
            else:
                if cur:
                    strokes.append(cur)
                cur = [p, q]
        flip = not flip
    if cur:
        strokes.append(cur)
    return strokes


def _rot_closed(pl, cx, cy, sx):
    """닫힌 선(처음=끝)이면 현재 위치에서 가장 가까운 꼭짓점부터 시작하도록 돌린다."""
    if len(pl) < 4 or abs(pl[0][0] - pl[-1][0]) > 1e-9 or abs(pl[0][1] - pl[-1][1]) > 1e-9:
        return pl
    body = pl[:-1]
    k = min(range(len(body)), key=lambda i: ((body[i][0] - cx) * sx) ** 2 + (body[i][1] - cy) ** 2)
    if k == 0:
        return pl
    body = body[k:] + body[:k]
    return body + [body[0]]


def order_strokes(strokes, start=(0.0, 0.0), deg_mm=1.0):
    """가까운 곳부터 이어 그리도록 순서 정렬(필요하면 선을 뒤집는다).
    strokes: [(kind, [(deg,h),...], pen)]   deg_mm: 1도가 몇 mm 인지(거리를 mm 로 맞춰 계산)"""
    n = len(strokes)
    if n < 3:
        return list(strokes)
    sx = float(deg_mm)
    if n > 2500:                       # 너무 많으면 띠 단위 훑기로 근사
        def key(s):
            p = s[1][0]
            band = int(p[1] // 3)
            return (band, p[0] if band % 2 == 0 else -p[0])
        return sorted(strokes, key=key)
    heads = [s[1][0] for s in strokes]
    tails = [s[1][-1] for s in strokes]
    left = set(range(n))
    out = []
    cx, cy = start
    while left:
        best, bi, brev = 1e18, -1, False
        for i in left:
            hx, hy = heads[i]
            d = ((hx - cx) * sx) ** 2 + (hy - cy) ** 2
            if d < best:
                best, bi, brev = d, i, False
            tx, ty = tails[i]
            d = ((tx - cx) * sx) ** 2 + (ty - cy) ** 2
            if d < best:
                best, bi, brev = d, i, True
        left.discard(bi)
        st = strokes[bi]
        pl = st[1][::-1] if brev else st[1]
        if st[0] == "outline":
            pl = _rot_closed(pl, cx, cy, sx)
        out.append((st[0], pl, st[2]))
        cx, cy = pl[-1]
    return out


def order_by_pen(strokes, npen, area=None, deg_mm=1.0, by_area=False):
    """펜(색) 단위로 묶어서 그린다. 기본은 1번 색 → 2번 색 → 3번 색 순서로, 한 색을 완전히 끝낸 뒤 다음 색으로 간다.
    (by_area=True 이면 칠할 면적이 큰 색부터.) 한 색 안에서는 채우기 → 테두리 → 직접 그린 선 순서로 끝낸다.
    각 단계 안에서는 가까운 선부터 그려 이동을 줄인다."""
    area = area or {}
    if by_area:
        pens = sorted({s[2] for s in strokes}, key=lambda k: (-area.get(k, 0.0), k))
    else:
        pens = sorted({s[2] for s in strokes})
    out = []
    start = (0.0, 0.0)
    for pen in pens:
        for kinds in (("fill",), ("outline",), ("draw",)):
            grp = [s for s in strokes if s[2] == pen and s[0] in kinds]
            if not grp:
                continue
            o = order_strokes(grp, start, deg_mm)
            out += o
            start = o[-1][1][-1]
        rest = [s for s in strokes if s[2] == pen and s[0] not in ("fill", "outline", "draw")]
        if rest:
            o = order_strokes(rest, start, deg_mm)
            out += o
            start = o[-1][1][-1]
    return out


# =============================================================================
# 3. 그림 데이터 (불러온 이미지 + 채우기 선택 + 선 생성)
# =============================================================================
def label_values(vals, w, h):
    """같은 값끼리 4-연결 덩어리로 라벨링.
    반환: (labels[w*h], [(값, 크기, (x0,y0,x1,y1)), ...])"""
    n_all = w * h
    labels = [-1] * n_all
    comps = []
    for start in range(n_all):
        if labels[start] != -1:
            continue
        v = vals[start]
        rid = len(comps)
        labels[start] = rid
        stack = [start]
        n = 0
        x0 = x1 = start % w
        y0 = y1 = start // w
        while stack:
            i = stack.pop()
            n += 1
            y, x = divmod(i, w)
            if x < x0:
                x0 = x
            elif x > x1:
                x1 = x
            if y > y1:
                y1 = y
            if x > 0 and labels[i - 1] == -1 and vals[i - 1] == v:
                labels[i - 1] = rid
                stack.append(i - 1)
            if x < w - 1 and labels[i + 1] == -1 and vals[i + 1] == v:
                labels[i + 1] = rid
                stack.append(i + 1)
            if y > 0 and labels[i - w] == -1 and vals[i - w] == v:
                labels[i - w] = rid
                stack.append(i - w)
            if y < h - 1 and labels[i + w] == -1 and vals[i + w] == v:
                labels[i + w] = rid
                stack.append(i + w)
        comps.append((v, n, (x0, y0, x1, y1)))
    return labels, comps


class Artwork:
    """불러온 그림 + 자동 추출한 영역/외곽선 + 직접 그린 선 → 달걀 표면 선."""
    MAX_SIDE = 1400
    MIN_SIDE = 1000      # 이보다 작은 그림은 이 크기까지 부드럽게 키워서 윤곽을 딴다 (각져 보이는 것 방지)
    up = 1.0

    def __init__(self):
        self.img = None
        self.alpha = None          # 투명 PNG 의 알파(L) 또는 None
        self.w = self.h = 0
        self.mask = None
        self.labels = None
        self.sizes = []
        self.region_rgb = []       # 영역 평균색
        self.loops = []
        self.selected = set()
        self.dirty = True
        self.note = ""             # 추출 결과 안내(앱 로그에 표시)
        self.manual = []           # 직접 그린 선 [[(x,y),...]] (그림 픽셀 좌표)
        self.region_pen = {}       # 영역 번호 -> 칠할 펜 번호(0부터). 색 버튼으로 직접 지정한 것
        self.outline_pen = {}      # 영역 번호 -> 그 영역 테두리(윤곽선)만 칠할 펜 번호 (안쪽 채우기와 무관)
        self.loop_region = []      # loops 와 같은 순서: 그 윤곽선이 속한 영역 번호(-1 = 모름)
        self.pen_area = {}         # 펜 번호 -> 칠할 면적(픽셀). 큰 색부터 그린다
        self.px_strokes = []       # [(kind, [(x,y)], pen)]  미리보기용
        self.strokes = []          # [(kind, [(deg,h)], pen)]  정렬 전

    # ---- 불러오기 -----------------------------------------------------------
    def load(self, pil_img):
        im = pil_img
        alpha = None
        if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            a = rgba.getchannel("A")
            if a.getextrema()[0] < 250:
                alpha = a
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, rgba)
        im = im.convert("RGB")
        if max(im.size) > self.MAX_SIDE:
            im.thumbnail((self.MAX_SIDE, self.MAX_SIDE), Image.LANCZOS)
            if alpha is not None:
                alpha = alpha.resize(im.size, Image.LANCZOS)
        self.up = 1.0
        if max(im.size) < self.MIN_SIDE:
            self.up = self.MIN_SIDE / float(max(im.size))
            nsz = (max(1, int(round(im.size[0] * self.up))), max(1, int(round(im.size[1] * self.up))))
            im = im.resize(nsz, Image.BICUBIC)
            if alpha is not None:
                alpha = alpha.resize(nsz, Image.BICUBIC)
        self.img = im
        self.alpha = alpha
        self.w, self.h = im.size
        self.selected = set()
        self.manual = []
        self.region_pen = {}
        self.outline_pen = {}
        self.dirty = True

    def blank(self, w=480, h=360):
        """직접 그리기용 빈 흰 캔버스."""
        self.img = Image.new("RGB", (w, h), (255, 255, 255))
        self.alpha = None
        self.w, self.h = w, h
        self.selected = set()
        self.manual = []
        self.region_pen = {}
        self.outline_pen = {}
        self.dirty = True

    # ---- 자동 추출 ----------------------------------------------------------
    def _bg_color(self):
        px = self.img.load()
        w, h = self.w, self.h
        pts = [px[x, 0] for x in range(w)] + [px[x, h - 1] for x in range(w)] + \
              [px[0, y] for y in range(h)] + [px[w - 1, y] for y in range(h)]
        return tuple(sorted(c[i] for c in pts)[len(pts) // 2] for i in range(3))

    @staticmethod
    def _close_and_clean(m, close=True):
        """이진 마스크의 작은 틈(1~2px, 안티에일리어싱/그라데이션 경계에서 생기는 끊김)을 메우고
        잡티를 지운다. 바깥 윤곽선이 끊어져서 '외곽선이 잘 안 따지는' 원인을 줄여준다."""
        if close:
            m = m.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
        return m.filter(ImageFilter.MedianFilter(3))

    def _foreground(self, tol):
        """배경과 다른 부분(=그림)을 255 로 표시한 L 이미지. 투명 PNG 는 알파를 사용."""
        if self.alpha is not None:
            return self.alpha.point(lambda v: 255 if v >= 128 else 0)
        bg = Image.new("RGB", self.img.size, self._bg_color())
        rr, gg, bb = ImageChops.difference(self.img, bg).split()
        # 단순 최대 채널차 대신, 세 채널을 합친 색 거리로 판단해 그라데이션/저채도 배경 차이도 잘 잡는다.
        dr, dg, db = rr.tobytes(), gg.tobytes(), bb.tobytes()
        dist = bytes(min(255, int(math.sqrt(a * a + b * b + c * c))) for a, b, c in zip(dr, dg, db))
        d = Image.frombytes("L", self.img.size, dist)
        m = d.point(lambda v: 255 if v > tol else 0)
        return self._close_and_clean(m)

    def _dark(self, threshold):
        m = self.img.convert("L").point(lambda v: 255 if v < threshold else 0)
        return self._close_and_clean(m)

    @staticmethod
    def _otsu(img_l):
        """L 이미지의 Otsu 자동 임계값 (0~255). 밝기 분포가 한쪽으로 쏠려도 그림/바탕을 가르는 값을 찾는다."""
        hist = img_l.histogram()
        total = float(sum(hist)) or 1.0
        sum_all = sum(i * c for i, c in enumerate(hist))
        wb = sb = 0.0
        best, thr = -1.0, 128
        for i in range(256):
            wb += hist[i]
            if wb <= 0:
                continue
            wf = total - wb
            if wf <= 0:
                break
            sb += i * hist[i]
            mb, mf = sb / wb, (sum_all - sb) / wf
            var = wb * wf * (mb - mf) ** 2
            if var > best:
                best, thr = var, i
        return thr

    @staticmethod
    def _frac(m):
        return sum(1 for v in m.tobytes() if v) / float(max(1, m.size[0] * m.size[1]))

    def _best_mask(self, p):
        """여러 방법으로 '그림 부분'을 뽑아 보고 가장 그럴듯한 것을 고른다.
        (배경과의 색 차이 → 차이의 자동 임계값 → 어두운 선 → 밝은 선) 반환: (마스크, 안내문)"""
        lo, hi = 0.002, 0.85
        # 1) 배경색과의 차이 (사용자 민감도 그대로)
        m = self._foreground(p.bg_tol)
        f = self._frac(m)
        if lo <= f <= hi:
            return m, ""
        if self.alpha is not None:
            return m, ""
        # 2) 차이 이미지에 Otsu - 연하게 그려진 그림/흐린 스캔
        bg = Image.new("RGB", self.img.size, self._bg_color())
        r, g, b = ImageChops.difference(self.img, bg).split()
        d = ImageChops.lighter(ImageChops.lighter(r, g), b)
        t = max(8, min(int(p.bg_tol), self._otsu(d)))
        m2 = self._close_and_clean(d.point(lambda v, t=t: 255 if v > t else 0))
        f2 = self._frac(m2)
        if lo <= f2 <= hi:
            return m2, "배경과 색 차이가 작아 민감도를 자동으로 낮춰서 추출했어요."
        # 3) 어두운 선 (Otsu), 4) 밝은 선 (Otsu 반전)
        gray = self.img.convert("L")
        to = self._otsu(gray)
        m3 = self._close_and_clean(gray.point(lambda v, t=to: 255 if v <= t else 0))
        f3 = self._frac(m3)
        m4 = self._close_and_clean(gray.point(lambda v, t=to: 255 if v > t else 0))
        f4 = self._frac(m4)
        cands = []
        if lo <= f3 <= 0.6:
            cands.append((f3, m3, "배경을 구분하기 어려워 밝기(자동 기준)로 어두운 부분을 추출했어요."))
        if lo <= f4 <= 0.6:
            cands.append((f4, m4, "바탕이 어두운 그림으로 보여 밝은 부분을 추출했어요."))
        if cands:
            # 그림은 보통 화면의 작은 쪽이므로 면적이 작은 후보를 택한다
            cands.sort(key=lambda c: c[0])
            return cands[0][1], cands[0][2]
        m5 = self._dark(p.threshold)
        return m5, "배경을 구분하기 어려워 '어두운 선(밝기)' 방식으로 대신 추출했어요."

    def analyze(self, p: Params):
        self.note = ""
        self.loops = []
        w, h = self.w, self.h
        mode = p.extract_mode
        if mode == EXTRACT_MODES[1]:
            self._analyze_color(p)
        else:
            if mode == EXTRACT_MODES[0]:
                m, self.note = self._best_mask(p)
            else:
                m = self._dark(p.threshold)
            mask = bytearray(1 if v else 0 for v in m.tobytes())
            self.labels, self.sizes = flood_label(mask, w, h, int(p.min_region * self.up * self.up))
            self.mask = mask
            self.loops = trace_outlines(mask, w, h)
            self._loop_regions()
        self._region_colors()
        self.selected = {r for r in self.selected if r < len(self.sizes)}
        self.region_pen = {r: k for r, k in self.region_pen.items() if r < len(self.sizes)}
        self.outline_pen = {r: k for r, k in self.outline_pen.items() if r < len(self.sizes)}
        if not self.sizes and not self.note and not self.manual:
            self.note = "추출된 영역이 없어요. '선 추출' 탭에서 방식이나 민감도를 바꿔 보세요."
        self.dirty = False

    def _analyze_color(self, p: Params):
        """색깔별로 영역을 나누고, 영역마다 윤곽선을 딴다 (내부 선/무늬 포함)."""
        w, h = self.w, self.h
        k = max(2, min(16, int(p.color_count)))
        q = getattr(Image, "Quantize", Image)
        dth = getattr(Image, "Dither", Image)
        qi = self.img.quantize(colors=k, method=q.MEDIANCUT, dither=dth.NONE)
        idx = Image.frombytes("L", qi.size, qi.tobytes()).filter(ImageFilter.ModeFilter(3))
        # 그라데이션처럼 비슷한 팔레트 색끼리는 같은 영역으로 합쳐서, 외곽선이 계단처럼
        # 잘게 쪼개지거나 안쪽에 불필요한 줄무늬 선이 생기지 않게 한다.
        merge_tol = max(0, int(getattr(p, "color_merge", 0)))
        if merge_tol > 0:
            pal = qi.getpalette() or []
            pal += [0] * max(0, k * 3 - len(pal))
            pal_rgb = [tuple(pal[i * 3:i * 3 + 3]) for i in range(k)]
            parent = list(range(k))

            def find(a):
                while parent[a] != a:
                    parent[a] = parent[parent[a]]
                    a = parent[a]
                return a

            for i in range(k):
                for j in range(i + 1, k):
                    dr = pal_rgb[i][0] - pal_rgb[j][0]
                    dg = pal_rgb[i][1] - pal_rgb[j][1]
                    db = pal_rgb[i][2] - pal_rgb[j][2]
                    if math.sqrt(dr * dr + dg * dg + db * db) <= merge_tol:
                        ra, rb = find(i), find(j)
                        if ra != rb:
                            parent[ra] = rb
            lut = [find(v) if v < k else v for v in range(256)]
            idx = idx.point(lut)
        vals = idx.tobytes()
        border = [vals[x] for x in range(w)] + [vals[(h - 1) * w + x] for x in range(w)] + \
                 [vals[y * w] for y in range(h)] + [vals[y * w + w - 1] for y in range(h)]
        bgv = max(set(border), key=border.count)
        raw, comps = label_values(vals, w, h)
        newid, sizes, boxes = {}, [], []
        for cid, (v, n, box) in enumerate(comps):
            if v != bgv and n >= int(p.min_region * self.up * self.up):
                newid[cid] = len(sizes)
                sizes.append(n)
                boxes.append(box)
        self.labels = [newid.get(l, -1) for l in raw]
        self.sizes = sizes
        self.mask = bytearray(1 if l >= 0 else 0 for l in self.labels)
        loops = []
        lreg = []
        for r, (x0, y0, x1, y1) in enumerate(boxes):
            bw, bh = x1 - x0 + 1, y1 - y0 + 1
            cm = bytearray(bw * bh)
            for yy in range(bh):
                base = (y0 + yy) * w + x0
                row = self.labels[base:base + bw]
                o = yy * bw
                for xx in range(bw):
                    if row[xx] == r:
                        cm[o + xx] = 1
            for lp in trace_outlines(cm, bw, bh):
                loops.append([(x + x0, y + y0) for x, y in lp])
                lreg.append(r)
        # 맞닿은 두 색 영역은 같은 경계를 각각 따서 선이 겹쳐 두 줄로 나온다.
        # 거의 같은 자리에 있는 윤곽선은 하나만 남긴다.
        keep_l, keep_r, seen = [], [], []
        for lp, r in zip(loops, lreg):
            xs = [q[0] for q in lp]
            ys = [q[1] for q in lp]
            sig = (min(xs), max(xs), min(ys), max(ys), len(lp))
            tol = max(2.0, 0.004 * max(w, h))
            dup = False
            for t in seen:
                if (abs(t[0] - sig[0]) <= tol and abs(t[1] - sig[1]) <= tol and
                        abs(t[2] - sig[2]) <= tol and abs(t[3] - sig[3]) <= tol and
                        abs(t[4] - sig[4]) <= max(6, 0.08 * sig[4])):
                    dup = True
                    break
            if dup:
                continue
            seen.append(sig)
            keep_l.append(lp)
            keep_r.append(r)
        self.loops = keep_l
        self.loop_region = keep_r

    def _loop_regions(self):
        """각 윤곽선이 어느 영역의 것인지(윤곽 꼭짓점 주변 픽셀의 영역 번호)."""
        w, h = self.w, self.h
        out = []
        for lp in self.loops:
            r = -1
            for (x, y) in lp[:8]:
                for xx, yy in ((x - 1, y - 1), (x, y - 1), (x - 1, y), (x, y)):
                    if 0 <= xx < w and 0 <= yy < h and self.labels[yy * w + xx] >= 0:
                        r = self.labels[yy * w + xx]
                        break
                if r >= 0:
                    break
            out.append(r)
        self.loop_region = out

    def _region_colors(self):
        data = self.img.tobytes()
        n = len(self.sizes)
        sr, sg, sb = [0] * n, [0] * n, [0] * n
        for i, l in enumerate(self.labels):
            if l >= 0:
                j = i * 3
                sr[l] += data[j]
                sg[l] += data[j + 1]
                sb[l] += data[j + 2]
        self.region_rgb = [(sr[i] // max(1, self.sizes[i]), sg[i] // max(1, self.sizes[i]),
                            sb[i] // max(1, self.sizes[i])) for i in range(n)]

    # ---- 영역 선택 ----------------------------------------------------------
    def region_at(self, x, y):
        if self.labels and 0 <= x < self.w and 0 <= y < self.h:
            return self.labels[y * self.w + x]
        return -1

    def select_all(self):
        self.selected = set(range(len(self.sizes)))

    def clear_selection(self):
        self.selected = set()
        self.region_pen = {}

    def assign_pen(self, region, pen):
        """색 버튼으로 고른 펜(0부터)으로 영역을 칠하도록 지정."""
        if 0 <= region < len(self.sizes):
            self.region_pen[region] = int(pen)
            self.selected.add(region)

    def set_outline_pen(self, region, pen):
        """영역 region 의 테두리(윤곽선)만 이 펜 색으로 바꿔 그린다. 안쪽 채우기(selected/region_pen)는 건드리지 않는다."""
        if region is not None and 0 <= region < len(self.sizes):
            self.outline_pen[region] = int(pen)

    def outline_region_near(self, x, y, radius):
        """(x,y) 근처를 지나는 자동 추출 윤곽선(테두리)이 속한 영역 번호를 찾는다. 없으면 -1."""
        r2 = radius * radius
        for li, lp in enumerate(self.loops):
            for (a, b) in lp:
                if (a - x) ** 2 + (b - y) ** 2 <= r2:
                    return self.loop_region[li] if li < len(self.loop_region) else -1
        return -1

    def auto_assign(self, rgbs):
        """영역 평균색과 가장 가까운 펜 색으로 전부 배정."""
        for r in range(len(self.sizes)):
            c = self.region_rgb[r]
            self.region_pen[r] = min(range(len(rgbs)), key=lambda i: sum((c[j] - rgbs[i][j]) ** 2 for j in range(3)))
        self.selected = set(range(len(self.sizes)))

    def fill_mask(self, regions=None):
        regs = self.selected if regions is None else regions
        flags = [False] * (len(self.sizes) + 1)       # 마지막 칸 = 라벨 -1
        for r in regs:
            if 0 <= r < len(self.sizes):
                flags[r] = True
        return bytearray(1 if flags[l] else 0 for l in self.labels)

    # ---- 직접 그린 선 -------------------------------------------------------
    def add_line(self, pts, pen=0):
        """직접 그린 선 하나를 저장한다. pen = 그 선을 그릴 펜 번호(0부터, 색 칩 선택값)."""
        if len(pts) >= 2:
            self.manual.append({"pts": [(float(x), float(y)) for x, y in pts], "pen": int(pen)})

    def undo_line(self):
        if self.manual:
            self.manual.pop()

    def clear_lines(self):
        self.manual = []

    def erase_near(self, x, y, radius):
        """(x,y) 근처를 지나는 직접 그린 선을 지운다. 지운 개수 반환."""
        r2 = radius * radius
        keep = [ln for ln in self.manual if not any((a - x) ** 2 + (b - y) ** 2 <= r2 for a, b in ln["pts"])]
        n = len(self.manual) - len(keep)
        self.manual = keep
        return n

    def recolor_near(self, x, y, radius, pen):
        """(x,y) 근처를 지나는 직접 그린 선의 펜 색을 바꾼다. 바뀐 선 개수 반환."""
        r2 = radius * radius
        n = 0
        for ln in self.manual:
            if any((a - x) ** 2 + (b - y) ** 2 <= r2 for a, b in ln["pts"]):
                ln["pen"] = int(pen)
                n += 1
        return n

    # ---- 작업 저장/불러오기용 (펜 색 포함, 예전 저장본도 읽을 수 있게 호환) -----------------
    @staticmethod
    def manual_to_json(manual):
        return [{"pts": [[round(x, 2), round(y, 2)] for x, y in ln["pts"]], "pen": int(ln.get("pen", 0))}
                for ln in manual]

    @staticmethod
    def manual_from_json(raw):
        out = []
        for ln in raw or []:
            if isinstance(ln, dict):
                pts, pen = ln.get("pts", []), int(ln.get("pen", 0))
            else:
                pts, pen = ln, 0          # 예전 저장본: 펜 번호 없이 점 목록만 있었음
            if len(pts) >= 2:
                out.append({"pts": [(float(x), float(y)) for x, y in pts], "pen": pen})
        return out

    # ---- 선 만들기 ----------------------------------------------------------
    def pen_of_region(self, r, p: Params):
        """영역 r 을 칠할 펜 번호(0부터): 색 버튼 지정 > 색 자동 맞춤 > 기본 채우기 펜."""
        npen = p.npen()
        if r in self.region_pen:
            return max(0, min(npen - 1, self.region_pen[r]))
        if p.pen_by_color and npen > 1 and 0 <= r < len(self.region_rgb):
            cols = p.pen_rgb()[:npen]
            c = self.region_rgb[r]
            return min(range(npen), key=lambda i: sum((c[j] - cols[i][j]) ** 2 for j in range(3)))
        return max(0, min(npen - 1, int(p.pen_fill) - 1))

    def build(self, p: Params, shape: Shape):
        self.px_strokes = []
        self.strokes = []
        self.pen_area = {}
        if self.img is None:
            return
        mmpp = p.height_mm / float(self.h)
        npen = p.npen()
        area = self.pen_area

        def pen_of(v):
            return max(0, min(npen - 1, int(v) - 1))

        def add_area(pen, v):
            area[pen] = area.get(pen, 0.0) + v

        if self.mask is not None:
            if p.outline:
                tol = max(0.5, p.simplify_mm / mmpp)
                po = pen_of(p.pen_outline)
                for li, lp in enumerate(self.loops):
                    pts = simplify_loop(smooth_loop([(float(x), float(y)) for x, y in lp], 3 + int(round(2 * self.up))), tol)
                    if len(pts) < 3:
                        continue
                    r = self.loop_region[li] if li < len(self.loop_region) else -1
                    if r in self.outline_pen:
                        pen = self.outline_pen[r]
                    else:
                        follow = p.outline_follow and r >= 0 and (r in self.region_pen or (p.pen_by_color and npen > 1))
                        pen = self.pen_of_region(r, p) if follow else po
                    self.px_strokes.append(("outline", pts + [pts[0]], pen))
                    add_area(pen, 0.01 * len(pts))
                # 같은 경계를 두 번 그려 선이 겹쳐 보이는 문제 방지 (펜 굵기 정도 안쪽은 같은 선으로 봄)
                _ol = [s for s in self.px_strokes if s[0] == "outline"]
                _ot = [s for s in self.px_strokes if s[0] != "outline"]
                self.px_strokes = _ot + dedupe_overlap(_ol, max(1.5, OVERLAP_MM / mmpp))
            if self.selected:
                gap_mm = p.fill_gap_mm * (p.dimple_fill / 100.0 if (p.dimple_comp and not p.dimple_auto) else 1.0)
                gap_px = max(1.2, gap_mm / mmpp)
                groups = {}
                for r in self.selected:
                    if 0 <= r < len(self.sizes):
                        pen = self.pen_of_region(r, p)
                        groups.setdefault(pen, set()).add(r)
                        add_area(pen, float(self.sizes[r]))
                for pen, regs in sorted(groups.items()):
                    for st in hatch_strokes(self.fill_mask(regs), self.w, self.h, p.fill_angle, gap_px, p.zigzag):
                        if len(st) >= 2:
                            self.px_strokes.append(("fill", st, pen))
        pd = pen_of(p.pen_draw)
        for ln in self.manual:
            raw_pts = ln["pts"] if isinstance(ln, dict) else ln
            pen = max(0, min(npen - 1, int(ln.get("pen", pd)))) if isinstance(ln, dict) else pd
            pts = rdp(raw_pts, max(0.4, 0.2 / mmpp))
            if len(pts) >= 2:
                self.px_strokes.append(("draw", pts, pen))
                add_area(pen, 0.5 * len(pts))
        # 펜이 닿을 수 있는 높이 한계: 물체 높이와 (스윙 모드면) 팔 스윙 한계 중 작은 쪽.
        # 한계를 넘는 점은 그대로 두면 모두 같은 높이(한계선)로 눌려 선이 한 줄로 겹쳐 그려진다.
        hlim = shape.half * 0.995
        if getattr(p, "arm_swing", False):
            hlim = min(hlim, max(1.0, p.arm_len_mm) * math.sin(math.radians(p.arm_max_deg)) * 0.999)
        self.hlim = hlim
        self.clipped = 0
        out = []
        for k, pl, pen in self.px_strokes:
            pts = [self.to_surface(x, y, p, shape) for x, y in pl]
            piece = []
            for q in pts:
                if abs(q[1]) <= hlim:
                    piece.append(q)
                else:
                    self.clipped += 1
                    if len(piece) >= 2:
                        out.append((k, piece, pen))
                    piece = []
            if len(piece) >= 2:
                out.append((k, piece, pen))
        self.strokes = out

    def to_surface(self, x, y, p: Params, shape: Shape):
        mmpp = p.height_mm / float(self.h)
        cx, cy = self.w / 2.0, self.h / 2.0
        a = math.radians(p.rot_deg)
        c, s = math.cos(a), math.sin(a)
        dx, dy = (x - cx) * getattr(p, "stretch_x", 1.0), y - cy
        rx = dx * c - dy * s
        ry = dx * s + dy * c
        deg_per_mm = 360.0 / shape.circumference(p.pos_y_mm)
        return (p.pos_x_deg + rx * mmpp * deg_per_mm, p.pos_y_mm - ry * mmpp)

    def drawing_size_mm(self, p: Params):
        mmpp = p.height_mm / float(self.h)
        return self.w * mmpp * getattr(p, "stretch_x", 1.0), self.h * mmpp


# =============================================================================
# 4. G코드 / 애니메이션 타임라인
# =============================================================================
# ---- 서보(펜 올림/내림) 명령 변환 ------------------------------------------
# grbl-Mega-5X-servos 펌웨어는 서보를 M3 S가 아니라 'M96 A값'(0~499, 채널 A = RAMPS SERVO0/D11)으로 움직인다.
# 앱 내부 G코드는 그대로 M3 S(각도 0~180 기준)로 만들고, 기계로 보내거나 저장할 때만 M96 으로 바꾼다.
SERVO_M96 = True          # False 로 두면 예전처럼 M3 S 그대로 보냄
SERVO_CHANNEL = "A"       # A=D11, B=D6, C=D5, D=D4
SERVO_INVERT = True       # True: 서보 방향 반대 (A = 499 - A). 펜 올림/내림이 거꾸로면 이 값을 바꾸세요
_M3S_RE = re.compile(r"^\s*M0?3\s*S\s*(-?\d+(?:\.\d+)?)\s*$", re.I)


def to_servo(line):
    """'M3 S90' -> 'M96 A250' (S 를 서보 각도 0~180 으로 보고 0~499 로 환산). 그 외 줄은 그대로."""
    if not SERVO_M96 or not isinstance(line, str):
        return line
    m = _M3S_RE.match(line)
    if not m:
        return line
    a = int(round(max(0.0, min(180.0, float(m.group(1)))) * 499.0 / 180.0))
    if SERVO_INVERT:
        a = 499 - a
    return f"M96 {SERVO_CHANNEL}{a}"


SERVO_REFRESH_LINES = 0   # 0 = 끔. 그리는 중 서보 값을 다시 보내는 기능 (켜면 M96 때문에 움직임이 끊길 수 있음)


def to_servo_lines(lines):
    """G코드 줄 목록을 기계로 보내거나 저장할 때 쓰는 형태로 바꾼다.
    1) 서보 명령(M3 S -> M96) 앞에 'G4 P0'(이동이 끝날 때까지 대기)을 붙인다.
       M96 은 GRBL 이동 큐와 동기화되지 않아서, 안 붙이면 큐에 남은 이동보다 펜 올림이 먼저 실행된다.
    2) 그리는 중(G1)에는 SERVO_REFRESH_LINES 줄마다 직전 서보 값을 다시 보낸다.
       스텝모터가 움직이는 동안 서보 신호가 흔들려 명령 없이 펜이 들리는 경우, 값을 계속 되잡아 준다."""
    out = []
    last_servo = None
    cnt = 0
    for l in lines:
        if not isinstance(l, str):
            continue
        c = to_servo(l)
        if SERVO_M96 and c != l:
            out.append("G4 P0")
            out.append(c)
            last_servo, cnt = c, 0
            continue
        out.append(l)
        if SERVO_M96 and SERVO_REFRESH_LINES > 0 and last_servo and l.upper().startswith("G1"):
            cnt += 1
            if cnt >= SERVO_REFRESH_LINES:
                out.append(last_servo)
                cnt = 0
    return out


GRBL_LAG = 12     # GRBL 은 'ok' 를 명령이 계획 버퍼에 들어갈 때 보낸다 -> 실제 움직임은 보통 이만큼 뒤쪽 줄


def line_cum_times(lines, default_feed=1000.0):
    """G코드 줄마다 '그 줄이 끝나는 누적 시간(초)'을 돌려준다. (실시간 연동에서 줄 번호 -> 시간 환산용)"""
    cum = []
    t = 0.0
    pos = {}
    feed = float(default_feed)
    for ln in lines:
        s = re.sub(r"\(.*?\)", "", str(ln).split(";")[0]).upper().strip()
        mf = re.search(r"F(-?[\d.]+)", s)
        if mf:
            try:
                feed = max(1.0, float(mf.group(1)))
            except ValueError:
                pass
        if re.match(r"G0?4\b", s):
            mp = re.search(r"P(-?[\d.]+)", s)
            if mp:
                try:
                    t += max(0.0, float(mp.group(1)))
                except ValueError:
                    pass
        elif re.match(r"G0?[01]\b", s):
            d2 = 0.0
            for ax, v in re.findall(r"([XYZEABC])(-?[\d.]+)", s):
                try:
                    v = float(v)
                except ValueError:
                    continue
                if ax in pos:
                    d2 += (v - pos[ax]) ** 2
                pos[ax] = v
            t += math.sqrt(d2) / feed * 60.0
        cum.append(t)
    return cum


def arm_out(h_mm, p):
    """표면 높이(mm) -> 펜 팔 출력값. 스윙 모드면 각도(도, 중심 0 기준, ±arm_max_deg 로 제한)."""
    if not getattr(p, "arm_swing", False):
        return h_mm
    lim = math.sin(math.radians(p.arm_max_deg))
    r = max(1.0, p.arm_len_mm)
    return math.degrees(math.asin(max(-lim, min(lim, h_mm / r)))) * getattr(p, "arm_scale", 1.0)


def arm_in(v, p):
    """arm_out 의 역변환 (각도 -> mm)."""
    if not getattr(p, "arm_swing", False):
        return v
    return max(1.0, p.arm_len_mm) * math.sin(math.radians(v / (getattr(p, "arm_scale", 1.0) or 1.0)))


def _f(v):
    s = f"{float(v):.3f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


# ---- 딤플 자동 보정 상수 (펜에 고무줄이 달려 아래로 당겨지는 구조를 가정) ----
OVERLAP_MM = 0.45        # 이 거리보다 가까운 윤곽선은 한 줄로 합침 (펜 굵기 정도)
AUTO_PRESS = 8           # 펜을 더 내리는 서보 값: 고무줄이 딤플 안으로 따라 내려갈 여유
AUTO_ZONE_SPEED = 45     # 딤플 안/가장자리 구간 속도(%)  (펜 끝이 림에 걸려 튀는 것 방지)
AUTO_REPEAT = 1          # 1 = 겹쳐 그리지 않음 (2 로 바꾸면 딤플 많이 지나는 선을 2번 겹침)
AUTO_CROSS_FRAC = 0.15   # 선 길이의 이만큼 이상이 딤플 구간이면 겹쳐 그림
SUBSEG_MM = 1.0          # 딤플 구간 판단을 위해 선을 쪼개는 간격


def pen_values(p: Params):
    """(펜 올림 S, 펜 내림 S, 그리기 속도, 반복 횟수)."""
    down = p.pen_down
    feed = float(p.draw_feed)
    repeat = 1
    if p.dimple_comp:
        if p.dimple_auto:
            down = max(0, down - AUTO_PRESS)
        else:
            down = max(0, down - p.dimple_press)
            feed *= p.dimple_speed / 100.0
            repeat = max(1, int(p.dimple_repeat))
    return int(p.pen_up), int(down), max(1.0, feed), repeat


def plan_stroke(pl, p: Params, shape: Shape, feed, repeat):
    """선 하나를 실제로 그릴 점 목록으로 바꾼다. 반환: (points, 반복 횟수)

    points = [(deg, h, F, k)]  (pl[0] 다음 점부터).  k = 직전에 지난 '원래 점'의 번호.
    딤플 자동 보정이 켜져 있으면 1mm 로 쪼개 딤플 안/가장자리에서는 천천히 그린다."""
    if not (p.dimple_comp and p.dimple_auto and shape.dimples):
        return [(q[0], q[1], feed, ki) for ki, q in enumerate(pl) if ki > 0], repeat
    zone_feed = max(1.0, feed * AUTO_ZONE_SPEED / 100.0)
    out = []
    zone_len = tot = 0.0
    for ki in range(1, len(pl)):
        a, b = pl[ki - 1], pl[ki]
        dmm = math.hypot((b[0] - a[0]) * shape.circumference(0.5 * (a[1] + b[1])) / 360.0, b[1] - a[1])
        n = max(1, int(math.ceil(dmm / SUBSEG_MM)))
        for i in range(1, n + 1):
            f, fm = i / n, (i - 0.5) / n
            z = shape.dimple_zone(a[0] + (b[0] - a[0]) * fm, a[1] + (b[1] - a[1]) * fm)
            tot += dmm / n
            if z:
                zone_len += dmm / n
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f,
                        zone_feed if z else feed, ki if i == n else ki - 1))
    reps = AUTO_REPEAT if tot > 0 and zone_len / tot >= AUTO_CROSS_FRAC else 1
    return out, reps


# ---- ATC 펜 교환 ---------------------------------------------------------------
def _near_angle(target, cur):
    """target 과 같은 각도(±360° 배수) 중 cur 에 가장 가까운 값 (턴테이블이 짧은 쪽으로 돌게)."""
    return target + 360.0 * round((cur - target) / 360.0)


_MECH_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
             ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.Pow: operator.pow}
_MECH_CMP = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
             ast.Eq: operator.eq, ast.NotEq: operator.ne}


def _mech_eval(expr, env):
    """스크립트 수식 계산 (+ - * / % ** 비교 and or not min max abs 만 허용)"""
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.Name):
            if n.id in env:
                return env[n.id]
            raise ValueError(f"모르는 이름 '{n.id}'")
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            if isinstance(n.op, ast.USub):
                return -v
            if isinstance(n.op, ast.UAdd):
                return v
            if isinstance(n.op, ast.Not):
                return not v
        if isinstance(n, ast.BinOp) and type(n.op) in _MECH_BIN:
            return _MECH_BIN[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.BoolOp):
            vals = [ev(v) for v in n.values]
            return all(vals) if isinstance(n.op, ast.And) else any(vals)
        if isinstance(n, ast.Compare):
            left = ev(n.left)
            for op, comp in zip(n.ops, n.comparators):
                right = ev(comp)
                if type(op) not in _MECH_CMP or not _MECH_CMP[type(op)](left, right):
                    return False
                left = right
            return True
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("min", "max", "abs")
                and not n.keywords):
            return {"min": min, "max": max, "abs": abs}[n.func.id](*[ev(x) for x in n.args])
        raise ValueError("쓸 수 없는 수식")
    return ev(ast.parse(expr.strip(), mode="eval"))


def mech_env(p, a, b):
    step = p.step_deg()
    half = p.mech_half if p.mech_half > 0 else step / 2.0
    return dict(A=p.turret_deg(a), B=p.turret_deg(b), H=half, STEP=step, HOME=p.mech_home, OFF=p.turret_off,
                DOCK=p.atc_dock, PARK=p.atc_park, PENH=p.atc_pen_h, PA=a + 1, PB=b + 1, N=p.npen(),
                D=(1 if b >= a else -1))


def turret_start(p, slot):
    """시작할 때 턴테이블(Z) 각도: 메커니즘 방식이면 홈 각도, 아니면 그 슬롯 각도"""
    if p.change_mode == CHANGE_MODES[0] and getattr(p, "mech_use", False):
        return p.mech_home
    return p.turret_deg(slot)


def mech_compile(p, a, b, cur_deg=0.0, script=None):
    """펜 a -> b 교환 스크립트를 단계 목록으로 바꾼다. 반환: (steps, 경고목록, 끝 Z 각도)"""
    text = p.mech_script if script is None else script
    env = mech_env(p, a, b)
    steps, warns = [], []
    tur, atc = p.mech_home, p.atc_park
    limit = max(env["H"], env["STEP"] / 2.0) + 1e-6
    mounted = False
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line[0] in ";#":
            continue
        try:
            m = re.match(r"^(?:when|if)\s+(.+?)\s*:\s*(.*)$", line, re.I)
            if m:
                if not _mech_eval(m.group(1), env):
                    continue
                line = m.group(2).strip()
            parts = line.split(None, 1)
            cmd, rest = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
            if cmd == "gcode":
                steps.append(("gcode", re.sub(r"\{(\w+)\}", lambda mm: _f(env[mm.group(1)]) if mm.group(1) in env
                                              else mm.group(0), rest.strip())))
                continue
            rest = re.split(r"[;#]", rest)[0].strip()
            feed = None
            fm = re.search(r"(?:^|\s)F\s+(\S.*)$", rest)
            if fm:
                feed = float(_mech_eval(fm.group(1), env))
                rest = rest[:fm.start()].strip()
            if cmd == "carriage":
                if rest:
                    steps.append(("carriage", cur_deg, float(_mech_eval(rest, env))))
                elif getattr(p, "exch_at_origin", True):
                    steps.append(("carriage", 0.0, 0.0, "origin"))      # 펜 팔 0도, 회전 0 (기계 원점)
                else:
                    steps.append(("carriage", cur_deg, p.atc_pen_h))
            elif cmd in ("turret", "z"):
                v = float(_mech_eval(rest, env))
                if atc > p.atc_dock / 2.0 + 0.5 and abs(v - tur) > limit:
                    warns.append(f"{ln}줄: ATC가 도킹한 채 Z가 {abs(v - tur):.2f}도 크게 회전 (충돌 주의)")
                steps.append(("turret", v, feed) if feed else ("turret", v))
                tur = v
            elif cmd in ("atc", "e"):
                v = float(_mech_eval(rest, env))
                steps.append(("atc", v, feed) if feed else ("atc", v))
                atc = v
            elif cmd == "wait":
                steps.append(("wait", float(_mech_eval(rest, env)) / 1000.0) if rest else ("wait",))
            elif cmd == "mount":
                r = rest.lower()
                if r in ("none", "-1", "없음"):
                    n = -1
                elif r in ("from", "a"):
                    n = a
                elif r in ("to", "b"):
                    n = b
                else:
                    n = int(float(_mech_eval(rest, env))) - 1
                mounted = mounted or n == b
                steps.append(("mount", n))
            else:
                warns.append(f"{ln}줄: 알 수 없는 명령 '{parts[0]}'")
        except Exception as e:  # 한 줄이 틀려도 나머지는 계속
            warns.append(f"{ln}줄 오류: {raw.strip()}  →  {e}")
    if not mounted:
        warns.append("새 펜을 장착하는 'mount to' 줄이 없어요.")
    if atc > p.atc_park + 0.5:
        warns.append("교환이 끝났는데 ATC가 대기 위치(PARK)로 돌아가지 않았어요.")
    return steps, warns, tur


def mech_seconds(p, a, b):
    steps, _w, _t = mech_compile(p, a, b, 0.0)
    tur, atc, t = p.mech_home, p.atc_park, 0.0
    for st in steps:
        if st[0] == "turret":
            t += abs(st[1] - tur) / max(1.0, st[2] if len(st) > 2 else p.turret_feed) * 60.0
            tur = st[1]
        elif st[0] == "atc":
            t += abs(st[1] - atc) / max(1.0, st[2] if len(st) > 2 else p.atc_feed) * 60.0
            atc = st[1]
        elif st[0] == "wait":
            t += st[1] if len(st) > 1 else p.change_wait_ms / 1000.0
    return t + p.dwell_ms / 1000.0


def exchange_steps(p: Params, a, b, cur_deg, tur_cur):
    if p.change_mode == CHANGE_MODES[0] and getattr(p, "mech_use", False):
        steps, _w, tur = mech_compile(p, a, b, cur_deg)
        return steps, tur
    return _exchange_steps_classic(p, a, b, cur_deg, tur_cur)


def _exchange_steps_classic(p: Params, a, b, cur_deg, tur_cur):
    """펜 a → b 교환 동작 순서. G코드와 3D 재생이 똑같은 순서를 쓴다. 반환: (단계 목록, 끝났을 때 턴테이블 각도)

    1) 펜 캐리지가 교환 위치로 가고  2) 빈 슬롯(a)을 도킹 위치로 돌리고
    3) ATC 가 접근해 펜 a 를 물고  4) 뒤로 빠지며 홀더에서 펜을 뽑아 슬롯 a 에 꽂는다
    5) 턴테이블을 돌려 새 펜 b 를 도킹 위치로  6) ATC 가 접근해 홀더에 펜 b 를 끼우고  7) 복귀."""
    ta = _near_angle(p.turret_deg(a), tur_cur)
    tb = _near_angle(p.turret_deg(b), ta)
    steps = [(("carriage", 0.0, 0.0, "origin") if getattr(p, "exch_at_origin", True)
              else ("carriage", cur_deg, p.atc_pen_h)),
             ("turret", ta), ("atc", p.atc_dock), ("wait",), ("mount", -1), ("atc", p.atc_park),
             ("turret", tb), ("atc", p.atc_dock), ("wait",), ("mount", b), ("atc", p.atc_park)]
    return steps, tb


def _fmt_change(text, p, a, b):
    return (text.replace("{from}", str(a + 1)).replace("{to}", str(b + 1))
            .replace("{angle}", _f(p.turret_deg(b))))


def change_lines(p: Params, a, b, up_cmd, cur_deg, tur_cur, xy):
    """펜 a → b 교환 G코드(0부터 시작하는 슬롯 번호). 반환: (줄 목록, 턴테이블 각도)"""
    dwell = _f(p.dwell_ms / 1000.0)
    out = [up_cmd, f"G4 P{dwell}", f"; 펜 교환: {a + 1}번 -> {b + 1}번"]
    out += [l for l in _fmt_change(p.change_pre, p, a, b).splitlines() if l.strip()]
    tur = tur_cur
    if p.change_mode == CHANGE_MODES[0]:
        steps, tur = exchange_steps(p, a, b, cur_deg, tur_cur)
        for st in steps:
            op = st[0]
            if op == "carriage":
                if len(st) > 3 and st[3] == "origin":
                    out.append(f"G0 X0 Y0 F{int(p.travel_feed)}")      # 교환은 원점(펜 팔 0도)에서
                else:
                    X, Y = xy(st[1], st[2])
                    out.append(f"G0 X{_f(X)} Y{_f(Y)} F{int(p.travel_feed)}")
                out.append("M3 S160")  # 펜 교환 시 항상 들어가야 하는 고정 값
            elif op == "turret":
                out.append(f"G0 {p.turret_axis}{_f(st[1])} F{int(max(1, st[2] if len(st) > 2 else p.turret_feed))}")
            elif op == "atc":
                out.append(f"G0 {p.atc_axis}{_f(st[1])} F{int(max(1, st[2] if len(st) > 2 else p.atc_feed))}")
            elif op == "wait":
                out.append(f"G4 P{_f(st[1] if len(st) > 1 else p.change_wait_ms / 1000.0)}")
            elif op == "gcode":
                out.append(st[1])
            elif op == "mount":
                pass  # 펜 장착 단계(주석 없음)
    else:
        out.append("M0")
    out += [l for l in _fmt_change(p.change_post, p, a, b).splitlines() if l.strip()]
    return out, tur


def change_seconds(p: Params, a, b):
    if p.change_mode == CHANGE_MODES[0] and getattr(p, "mech_use", False):
        return mech_seconds(p, a, b)
    if p.change_mode == CHANGE_MODES[0]:
        ta = p.turret_deg(a)
        tb = _near_angle(p.turret_deg(b), ta)
        return (abs(tb - ta) / max(1.0, p.turret_feed) * 60.0 + 4.0 * abs(p.atc_dock - p.atc_park) / max(1.0, p.atc_feed) * 60.0
                + 2.0 * p.change_wait_ms / 1000.0)
    return 5.0


def build_gcode(ordered, p: Params, shape: Shape, axes: AxisMap):
    up_s, down_s, feed, repeat = pen_values(p)
    travel = float(p.travel_feed)
    dwell = p.dwell_ms / 1000.0

    def xy(deg, h):
        d = deg * p.cal_rot_scale + p.cal_rot_off          # 보정: 배율/오프셋
        v = arm_out(h * p.cal_h_scale + p.cal_h_off, p)   # 펜 팔: mm -> 스윙 각도
        X, Y = (d, v) if axes.x_is_rot else (v, d)
        return (-X if axes.x_invert else X), (-Y if axes.y_invert else Y)

    up_cmd = f"M3 S{up_s}"
    down_cmd = f"M3 S{down_s}"
    used = sorted({s[2] for s in ordered})
    first = ordered[0][2] if ordered else 0
    lines = ["; 에그봇 G코드 생성기",
             f"; 물체: {p.kind} ({p.length_mm:.0f} x {p.diameter_mm:.0f} mm)",
             f"; 선 {len(ordered)}개 / 사용 펜: " + ", ".join(str(i + 1) for i in used)]
    if len(used) > 1:
        lines.append(f"; 시작 펜: {first + 1}번 ({p.pen_hex()[first]}) - 에그봇에 미리 달아 두세요")
        lines.append("; 색 순서: " + " -> ".join(f"{i + 1}번({p.pen_hex()[i]})" for i in
                                              dict.fromkeys(s[2] for s in ordered)))
    if (p.cal_rot_scale, p.cal_h_scale, p.cal_rot_off, p.cal_h_off) != (1.0, 1.0, 0.0, 0.0):
        lines.append(f"; 보정: 회전 x{p.cal_rot_scale:g} {p.cal_rot_off:+g}도 / 높이 x{p.cal_h_scale:g} {p.cal_h_off:+g}mm")
    if p.dimple_comp:
        lines.append("; 딤플 자동 보정: 딤플 안/가장자리 구간 감속, 펜 더 누름" + (f", 많이 지나는 선은 {AUTO_REPEAT}번 겹침" if AUTO_REPEAT > 1 else "") + "" if p.dimple_auto
                     else "; 딤플 보정: 수동 값 사용")
    lines += ["G21", "G90", up_cmd]
    if len(used) > 1 and p.change_mode == CHANGE_MODES[0]:
        # 초기 상태를 명시: 턴테이블은 시작 각도, ATC 는 대기 위치 (교환 동작이 항상 같은 출발점에서 시작되도록)
        lines.append("; 초기 상태: 턴테이블 시작 각도 / ATC 대기 위치")
        lines.append(f"G0 {p.turret_axis}{_f(turret_start(p, first))} {p.atc_axis}{_f(p.atc_park)} F{int(p.turret_feed)}")
    n_pts = 0
    n_change = 0
    secs = 0.0
    prev = (0.0, 0.0)
    cur_pen = first
    tur = turret_start(p, first)
    last_deg = 0.0
    lines.append(f"; ===== {cur_pen + 1}번 색 구간 시작 ({p.pen_hex()[cur_pen]}) =====")
    for _kind, pl, pen in ordered:
        if pen != cur_pen:
            lines.append(f"; ----- {cur_pen + 1}번 색 구간 끝 -----")
            chg, tur = change_lines(p, cur_pen, pen, up_cmd, last_deg, tur, xy)
            lines += chg
            secs += change_seconds(p, cur_pen, pen)
            if p.change_mode == CHANGE_MODES[0]:
                prev = (0.0, 0.0) if getattr(p, "exch_at_origin", True) else (last_deg, p.atc_pen_h)
            cur_pen = pen
            n_change += 1
            lines.append(f"; ===== {pen + 1}번 색 구간 시작 ({p.pen_hex()[pen]}) =====")
        pts, reps = plan_stroke(pl, p, shape, feed, repeat)
        for _ in range(reps):
            X, Y = xy(*pl[0])
            lines.append(f"G0 X{_f(X)} Y{_f(Y)} F{int(travel)}")
            lines.append(down_cmd)
            lines.append(f"G4 P{_f(dwell)}")
            secs += abs(pl[0][1] - prev[1]) / travel * 60 + dwell * 2
            last = pl[0]
            last_f = None
            for (d, h, f_, _k) in pts:
                X, Y = xy(d, h)
                if f_ != last_f:
                    lines.append(f"G1 X{_f(X)} Y{_f(Y)} F{int(f_)}")
                    last_f = f_
                else:
                    lines.append(f"G1 X{_f(X)} Y{_f(Y)}")
                secs += math.hypot(d - last[0], h - last[1]) / f_ * 60
                last = (d, h)
            lines.append(up_cmd)
            lines.append(f"G4 P{_f(dwell)}")
            n_pts += len(pl)
            prev = last
            last_deg = last[0]
    lines.append(up_cmd)
    if len(used) > 1 and cur_pen != first and getattr(p, "return_first_pen", True):
        # 모든 그림이 끝났다 -> 처음(시작) 펜으로 다시 교체
        lines.append(f"; ----- {cur_pen + 1}번 색 구간 끝 -----")
        lines.append(f"; 마무리: 모든 그림 완료 - 처음 펜({first + 1}번)으로 되돌려 교체")
        chg, tur = change_lines(p, cur_pen, first, up_cmd, last_deg, tur, xy)
        lines += chg
        secs += change_seconds(p, cur_pen, first)
        if p.change_mode == CHANGE_MODES[0]:
            prev = (0.0, 0.0) if getattr(p, "exch_at_origin", True) else (last_deg, p.atc_pen_h)
        cur_pen = first
        n_change += 1
        lines.append(up_cmd)
    if getattr(p, "return_home", True):
        lines.append(f"G4 P{_f(dwell)}")                       # 펜이 완전히 올라간 뒤 이동
        lines.append(f"G0 X0 Y0 F{int(travel)}")               # 원점으로 복귀
        lines.append("G4 P0")
    lines.append("M2")

    dpm = shape.circumference(0.0) / 360.0
    degs = [q[0] for _k, pl, _p in ordered for q in pl]
    hs = [q[1] for _k, pl, _p in ordered for q in pl]
    stats = dict(
        n_strokes=len(ordered),
        n_outline=sum(1 for s in ordered if s[0] == "outline"),
        n_fill=sum(1 for s in ordered if s[0] == "fill"),
        n_draw=sum(1 for s in ordered if s[0] == "draw"),
        n_changes=n_change, n_pens=len(used),
        n_points=n_pts, n_lines=len(lines), minutes=secs / 60.0,
        w_mm=(max(degs) - min(degs)) * dpm if degs else 0.0,
        h_mm=(max(hs) - min(hs)) if hs else 0.0,
    )
    return lines, stats


def test_strokes(p: Params, shape: Shape, size_mm=20.0):
    """보정용 시험 패턴: 한 변이 size_mm 인 정사각형 + 가운데 십자."""
    cx, cy = p.pos_x_deg, p.pos_y_mm
    dpm = 360.0 / shape.circumference(cy)
    hw, hh = size_mm / 2.0 * dpm, size_mm / 2.0
    sq = [(cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh), (cx - hw, cy + hh), (cx - hw, cy - hh)]
    return [("draw", sq, 0),
            ("draw", [(cx - hw * 0.4, cy), (cx + hw * 0.4, cy)], 0),
            ("draw", [(cx, cy - hh * 0.4), (cx, cy + hh * 0.4)], 0)]


# =============================================================================
# 5. G코드 → 선 (사용자가 G코드를 고친 뒤 3D 미리보기에 반영할 때)
# =============================================================================
_TOK = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")


def parse_gcode(lines, p: Params, shape: Shape, axes: AxisMap):
    """G코드를 읽어 (ordered strokes, 경고 목록)으로 되돌린다. 펜 교환 구간의 ATC/턴테이블 이동은
    건너뛰고, '펜 교환: a -> b' 주석으로 펜 번호를 따라간다."""
    warns = []
    mid = (p.pen_up + p.pen_down) / 2.0
    down_low = p.pen_down < p.pen_up
    xyz = {"X": 0.0, "Y": 0.0}
    absolute = True
    pen_down = False
    cur_pen = 0
    strokes, cur = [], None
    n_range = 0
    skip_axes = {p.turret_axis.strip().upper(), p.atc_axis.strip().upper()} - {"X", "Y"}

    def surf(X, Y):
        if axes.x_invert:
            X = -X
        if axes.y_invert:
            Y = -Y
        d, v = (X, Y) if axes.x_is_rot else (Y, X)
        return ((d - p.cal_rot_off) / (p.cal_rot_scale or 1.0), (arm_in(v, p) - p.cal_h_off) / (p.cal_h_scale or 1.0))

    def end_stroke():
        nonlocal cur
        if cur is not None and len(cur) >= 2:
            strokes.append(("draw", cur, cur_pen))
        cur = None

    for no, raw in enumerate(lines, 1):
        text = raw
        m = re.search(r"시작 펜:\s*(\d+)번", text)
        if m:
            cur_pen = max(0, min(p.npen() - 1, int(m.group(1)) - 1))
        m = re.search(r"펜 교환:\s*(\d+)번\s*->\s*(\d+)번", text)
        if m:
            end_stroke()
            cur_pen = max(0, min(p.npen() - 1, int(m.group(2)) - 1))
            continue
        text = re.sub(r"\(.*?\)", "", text.split(";")[0]).strip()
        if not text:
            continue
        words = {k.upper(): float(v) for k, v in _TOK.findall(text)}
        junk = _TOK.sub(" ", text).strip()
        if junk:
            warns.append(f"{no}줄: 해석할 수 없는 값 '{junk[:20]}' (예: X12.5 처럼 글자 뒤에 숫자가 와야 해요)")
        if "F" in words and not (0 < words["F"] <= 12000):
            warns.append(f"{no}줄: 이송 속도 F{words['F']:g} 가 이상해요 (1~12000 사이 권장)")
        if "S" in words and not (0 <= words["S"] <= 255):
            warns.append(f"{no}줄: 서보 값 S{words['S']:g} 가 범위(0~255)를 벗어났어요")
        head = text.split()[0].upper()
        g = int(words["G"]) if "G" in words else None
        mcode = int(words["M"]) if "M" in words else None
        if g == 90:
            absolute = True
        elif g == 91:
            absolute = False
            warns.append(f"{no}줄: G91(상대 좌표)는 미리보기에서 정확하지 않을 수 있어요.")
        elif g in (0, 1):
            moved = False
            for ax in ("X", "Y"):
                if ax in words:
                    xyz[ax] = words[ax] if absolute else xyz[ax] + words[ax]
                    moved = True
            if moved and not (set(words) & skip_axes):
                pt = surf(xyz["X"], xyz["Y"])
                if not (-0.5 <= pt[0] <= p.x_max_deg + 0.5) or abs(pt[1]) > shape.half + 1.0:
                    n_range += 1
                    if n_range <= 6:
                        warns.append(f"{no}줄: 달걀 밖 좌표예요 (회전 {pt[0]:.1f}° / 높이 {pt[1]:.1f}mm, "
                                     f"허용 0~{p.x_max_deg:g}° / ±{shape.half:.1f}mm)")
                if pen_down:
                    if cur is None:
                        cur = [pt]
                    else:
                        cur.append(pt)
                elif cur is not None:
                    end_stroke()
        elif mcode in (3, 4, 5) or head in ("M3", "M5"):
            s_val = words.get("S")
            if mcode == 5:
                now_down = False
            elif s_val is None:
                continue
            else:
                now_down = (s_val <= mid) if down_low else (s_val >= mid)
            if now_down and not pen_down:
                cur = [surf(xyz["X"], xyz["Y"])]
            elif not now_down and pen_down:
                end_stroke()
            pen_down = now_down
        elif g == 4 or g in (21, 20, 92, 28, 54) or mcode in (2, 30, 0):
            continue
        elif g is None and mcode is None and not (set(words) <= {"F", "S"}):
            warns.append(f"{no}줄: 알 수 없는 명령 '{raw.strip()[:30]}'")
    end_stroke()
    if n_range > 6:
        warns.append(f"... 달걀 밖 좌표가 모두 {n_range}곳이에요 (위에는 처음 6곳만 표시)")
    return strokes, warns


# =============================================================================
# 6. 3D 재생용 타임라인
# =============================================================================
class Timeline:
    """기계가 시간에 따라 어떤 자세인지 알려준다.

    waypoint 배열: t(초), deg, h, pen(0=올림..1=내림), stroke(-1이면 이동/대기), k(선 안의 점 번호),
                  tur(턴테이블 각도), atc(ATC 직선 위치 mm), mount(펜 홀더에 끼워진 펜, -1=없음), done
    plain=True 이면 G코드에서 읽은 선처럼 반복/딤플 분할 없이 한 번만 그린다."""

    def __init__(self, ordered, p: Params, shape: Shape = None, plain=False):
        if shape is None:
            shape = Shape(p.kind, p.length_mm, p.diameter_mm, p.pointiness)
        _up, _dn, feed, repeat = pen_values(p)
        travel = float(p.travel_feed)
        dwell = max(0.02, p.dwell_ms / 1000.0)
        self.t, self.deg, self.h, self.pen, self.stroke, self.k = [], [], [], [], [], []
        self.tur, self.atc, self.mount = [], [], []
        self.done = []                  # 해당 waypoint 까지 '완전히 그려진' 선 개수
        self.changes = []               # (시작 t, 끝 t, 이전 펜, 새 펜)
        t = 0.0
        cur = (0.0, 0.0)               # G코드와 같이 기계 원점(0,0)에서 출발
        finished = 0
        mount = ordered[0][2] if ordered else 0
        self.first_pen = mount
        tur = turret_start(p, mount)
        atc = p.atc_park

        def push(tt, pos, pen, st, k):
            self.t.append(tt)
            self.deg.append(pos[0])
            self.h.append(pos[1])
            self.pen.append(pen)
            self.stroke.append(st)
            self.k.append(k)
            self.tur.append(tur)
            self.atc.append(atc)
            self.mount.append(mount)
            self.done.append(finished)

        push(0.0, cur, 0.0, -1, 0)

        def do_change(pen_slot):
            nonlocal t, cur, tur, atc, mount
            t += dwell
            push(t, cur, 0.0, -1, 0)
            t0, old = t, mount
            if p.change_mode == CHANGE_MODES[0]:
                steps, _tb = exchange_steps(p, old, pen_slot, cur[0], tur)
                for st in steps:
                    op = st[0]
                    if op == "carriage":
                        t += abs(st[2] - cur[1]) / travel * 60.0
                        cur = (st[1], st[2])
                    elif op == "turret":
                        t += abs(st[1] - tur) / max(1.0, st[2] if len(st) > 2 else p.turret_feed) * 60.0
                        tur = st[1]
                    elif op == "atc":
                        t += abs(st[1] - atc) / max(1.0, st[2] if len(st) > 2 else p.atc_feed) * 60.0
                        atc = st[1]
                    elif op == "wait":
                        t += max(0.05, st[1] if len(st) > 1 else p.change_wait_ms / 1000.0)
                    elif op == "mount":
                        mount = st[1]
                    push(t, cur, 0.0, -1, 0)
            else:
                t += 5.0
                mount = pen_slot
                push(t, cur, 0.0, -1, 0)
            self.changes.append((t0, t, old, pen_slot))

        for si, (_kind, pl, pen_slot) in enumerate(ordered):
            if pen_slot != mount:                                 # ---- 펜 교환
                do_change(pen_slot)
            if plain:
                pts, reps = [(q[0], q[1], feed, ki) for ki, q in enumerate(pl) if ki > 0], 1
            else:
                pts, reps = plan_stroke(pl, p, shape, feed, repeat)
            for rep in range(reps):
                t += abs(pl[0][1] - cur[1]) / travel * 60.0 + abs(pl[0][0] - cur[0]) * shape.circumference(pl[0][1]) / 360.0 / travel * 60.0
                push(t, pl[0], 0.0, -1, 0)                  # 이동 (펜 올림)
                t += dwell
                push(t, pl[0], 1.0, si if rep == 0 else -1, 0)       # 펜 내림
                cur = pl[0]
                for (d, h, f_, k) in pts:
                    t += math.hypot((d - cur[0]) * shape.circumference(h) / 360.0, h - cur[1]) / f_ * 60.0
                    push(t, (d, h), 1.0, si if rep == 0 else -1, k)
                    cur = (d, h)
                if rep == 0:
                    finished += 1
                t += dwell
                push(t, cur, 0.0, -1, 0)                    # 펜 올림
        if (not plain and ordered and self.first_pen != mount
                and len({s[2] for s in ordered}) > 1 and getattr(p, "return_first_pen", True)):
            do_change(self.first_pen)                             # 모든 그림이 끝나면 처음 펜으로 되돌려 교체
        self.total = t
        self.count = len(self.t)

    def state(self, t):
        """시간 t 의 (deg, h, pen, 마지막으로 지난 waypoint 번호, 보간비)."""
        if self.count == 0:
            return 0.0, 0.0, 0.0, -1, 0.0
        if t >= self.total:
            j = self.count - 1
            return self.deg[j], self.h[j], self.pen[j], j, 0.0
        j = bisect.bisect_right(self.t, t) - 1
        j = max(0, min(j, self.count - 2))
        t0, t1 = self.t[j], self.t[j + 1]
        f = 0.0 if t1 <= t0 else max(0.0, min(1.0, (t - t0) / (t1 - t0)))
        return (self.deg[j] + (self.deg[j + 1] - self.deg[j]) * f,
                self.h[j] + (self.h[j + 1] - self.h[j]) * f,
                self.pen[j] + (self.pen[j + 1] - self.pen[j]) * f, j, f)

    def pose(self, t):
        """3D 재생용 전체 자세(dict)."""
        deg, h, pen, j, f = self.state(t)
        if j < 0:
            return dict(deg=0.0, h=0.0, pen=0.0, j=-1, f=0.0, tur=0.0, atc=0.0, mount=0, stroke=-1, k=0, done=0)
        j2 = min(j + 1, self.count - 1)
        return dict(deg=deg, h=h, pen=pen, j=j, f=f,
                    tur=self.tur[j] + (self.tur[j2] - self.tur[j]) * f,
                    atc=self.atc[j] + (self.atc[j2] - self.atc[j]) * f,
                    mount=self.mount[j], stroke=self.stroke[j], k=self.k[j], done=self.done[j])

    def aux(self, j, f):
        """(턴테이블 각도, 홀더에 끼워진 펜) - 예전 호환용."""
        if self.count == 0 or j < 0:
            return 0.0, 0
        j2 = min(j + 1, self.count - 1)
        return self.tur[j] + (self.tur[j2] - self.tur[j]) * f, self.mount[j]


# =============================================================================
# 4-1. GRBL 오류/알람 코드 설명 (GRBL 1.1 표준)
# =============================================================================
GRBL_ERRORS = {
    1: "G코드 줄에 명령 글자가 있어야 하는데 없어요", 2: "숫자 형식이 잘못됐어요",
    3: "'$' 설정 명령이 올바르지 않아요", 4: "음수를 쓸 수 없는 값에 음수가 들어갔어요",
    5: "이 설정은 꺼져 있어요(비활성)", 6: "스텝 펄스 시간이 너무 짧아요",
    7: "설정값을 EEPROM에서 읽는 데 실패했어요(기본값 사용)", 8: "대기(Idle) 상태에서만 되는 명령이에요",
    9: "알람/잠금 상태라 G코드가 막혀 있어요 → 먼저 '잠금해제 $X'", 10: "소프트 리밋 범위를 벗어난 값이에요",
    11: "한 줄 글자수가 너무 길어요", 12: "설정값이 허용 범위를 넘었어요", 13: "세이프티 도어가 열려 있어요",
    14: "빌드 정보 줄이 너무 길어요", 15: "조그 목표가 기계 이동 범위를 벗어났어요",
    16: "조그 명령이 없거나 G코드 오류예요", 17: "레이저 모드는 PWM 출력(변속)이 필요해요",
    20: "지원하지 않거나 잘못된 G코드 명령이에요", 21: "같은 모달 그룹의 명령이 한 줄에 중복됐어요",
    22: "이송 속도(F)가 지정 안 됐어요", 23: "G코드 ID 값이 잘못됐어요",
    24: "같은 축(X/Y/Z) 글자를 요구하는 명령이 두 개 겹쳤어요", 25: "같은 G코드 단어가 중복됐어요",
    26: "이 명령에 필요한 축(X/Y/Z) 값이 없어요", 27: "줄번호(N) 값이 잘못됐어요",
    28: "이 명령에 꼭 있어야 하는 값이 빠졌어요", 29: "G59.x 좌표계는 지원 안 해요",
    30: "G53은 G0/G1에서만 써야 해요", 31: "쓸 데 없는 축 값이 있어요(해당 명령과 안 맞음)",
    32: "G2/G3 원호에 축 값이 없어요", 33: "이동 목표가 잘못됐어요(예: 반지름 오류)",
    34: "원호(arc) 정의가 잘못됐어요", 35: "G2/G3 원호에 오프셋 값이 없어요",
    36: "안 쓰는 값 단어가 남아 있어요", 37: "G43.1 오프셋이 해당 축에 지정되지 않았어요",
    38: "공구 번호가 최대값을 넘었어요",
}
GRBL_ALARMS = {
    1: "하드 리밋(리밋 스위치)에 닿았어요 — 기계가 멈췄어요", 2: "이동 목표가 기계 범위를 벗어났어요(소프트 리밋)",
    3: "움직이는 도중 리셋돼서 위치를 잃었어요 → 다시 원점을 맞추세요", 4: "프로브 실패(시작 상태 이상)",
    5: "프로브가 닿지 않았어요", 6: "홈 찾기 중 리셋돼서 실패했어요", 7: "홈 찾기 중 도어가 열렸어요",
    8: "홈 찾기 실패: 리밋 스위치에서 못 빠져나왔어요", 9: "홈 찾기 실패: 리밋 스위치를 못 찾았어요",
}


def grbl_error_desc(txt):
    """'error:9' / 'ALARM:2' 같은 기계 응답을 한국어 설명으로 바꾼다."""
    low = (txt or "").strip().lower()
    m = re.match(r"error\s*:?\s*(\d+)", low)
    if m:
        n = int(m.group(1))
        return f"오류 {n}: " + GRBL_ERRORS.get(n, "알 수 없는 오류 코드예요")
    m = re.match(r"alarm\s*:?\s*(\d+)", low)
    if m:
        n = int(m.group(1))
        return f"알람 {n}: " + GRBL_ALARMS.get(n, "알 수 없는 알람 코드예요")
    return txt or "알 수 없는 오류"


def _fmt_secs(s):
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


# =============================================================================
# 5. GRBL 기계 연결
# =============================================================================
class GrblLink:
    """시리얼로 GRBL 에 G코드를 보낸다. (작업 스레드 1개가 송·수신을 모두 담당)

    UI 와는 `events` 큐로만 소통한다. (Tk 위젯은 메인 스레드에서만 만진다)
        ("log", 문자열) / ("progress", 보낸줄, 전체) / ("done", "ok"|"error"|"abort") /
        ("state", 문자열) / ("prepared", 태그, 성공여부) - 그리기 전 자동 준비 끝남 /
        ("error_detail", dict) - 오류/알람이 난 줄 번호·내용·설명
    """
    RX_LIMIT = 118            # GRBL 수신 버퍼(127바이트)를 넘기지 않기 위한 한도

    def __init__(self):
        import queue
        self.ser = None
        self.events = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._manual = deque()
        self._job = None
        self._run = False
        self._paused = False
        self._abort = False
        self.job_active = False
        self.status = {}                 # 마지막 상태 보고: state / mpos / wpos / pins / raw / t
        self.settings = {}               # `$$` 응답: {100: 250.0, ...}
        self.info = ""                   # 버전 정보 한 줄
        self.alarm = ""                  # 마지막 알람/오류 문구
        self.last_rx = 0.0               # 마지막으로 무언가 받은 시각
        self.rx_log = deque(maxlen=200)  # 최근 수신 줄 (자가 점검 리포트용)
        self.error_log = deque(maxlen=200)  # 오류/알람 발생 기록: {idx, line, code, desc, t}
        self.last_error = None
        self._wco = None

    # ---- 연결 ----------------------------------------------------------------
    @staticmethod
    def list_ports():
        if not HAS_SERIAL:
            return []
        return [p.device for p in serial.tools.list_ports.comports()]

    @property
    def connected(self):
        return self.ser is not None

    @property
    def paused(self):
        return self._paused

    def emit(self, *ev):
        self.events.put(ev)

    def connect(self, port, baud=115200):
        if not HAS_SERIAL:
            self.emit("log", "pyserial 이 없습니다.  python -m pip install pyserial")
            return False
        if self.ser:
            return True
        try:
            self.ser = serial.Serial(port, baud, timeout=0.05)
            time.sleep(1.6)                       # 보드 리셋 대기
            hello = self.ser.read(self.ser.in_waiting or 1).decode("utf-8", "replace")
            for ln in hello.splitlines():
                if ln.lower().startswith("grbl"):
                    self.info = ln.strip()
            self.ser.reset_input_buffer()
        except Exception as e:
            self.ser = None
            self.emit("log", f"연결 실패: {e}")
            return False
        self._run = True
        self._paused = self._abort = False
        self.status, self.settings, self.alarm = {}, {}, ""
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.emit("log", f"{port} 연결됨 ({baud}bps)")
        self.emit("state", "연결됨")
        return True

    def disconnect(self):
        self._run = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        with self._lock:
            self._manual.clear()
            self._job = None
        self.job_active = False
        self._paused = False
        self.emit("log", "연결 끊김")
        self.emit("state", "연결 안 됨")

    # ---- 명령 ----------------------------------------------------------------
    def send(self, line):
        """수동 명령 한 줄. 그리는 중이거나 일시정지 중에는 받지 않는다."""
        if not self.ser:
            self.emit("log", "먼저 기계에 연결하세요.")
            return False
        if self.job_active or self._paused:
            self.emit("log", "그리는 중(또는 일시정지)에는 수동 명령을 보낼 수 없어요.")
            return False
        with self._lock:
            self._manual.append(to_servo(line))
        return True

    def start(self, lines):
        if not self.ser:
            self.emit("log", "먼저 기계에 연결하세요.")
            return False
        with self._lock:
            if self.job_active or self._job is not None or self._manual:
                return False
            self._job = to_servo_lines([l for l in lines if l and not l.startswith(";")])
            self._abort = False
            self.job_active = True
        return True

    def pause(self):
        if self.ser and self.job_active and not self._paused:
            self._raw(b"!")
            self._paused = True
            self.emit("log", "일시정지 ('!')")
            self.emit("state", "일시정지")

    def resume(self):
        if self.ser and self._paused:
            self._raw(b"~")
            self._paused = False
            self.emit("log", "재개 ('~')")
            self.emit("state", "그리는 중")

    def estop(self):
        """비상 정지: 홀드('!') 후 소프트 리셋(Ctrl-X)하고 남은 작업을 모두 버린다.
        (소프트웨어 정지입니다. 위험하면 기계의 전원을 직접 끄세요.)"""
        if not self.ser:
            return
        self._abort = True
        self._raw(b"!")
        time.sleep(0.05)
        self._raw(b"\x18")
        with self._lock:
            self._manual.clear()
            self._job = None
        self._paused = False
        self.emit("log", "비상 정지: 홀드 + 소프트 리셋 전송. 다시 쓰려면 '잠금해제 $X' 를 누르세요.")
        self.emit("state", "정지됨")

    def cancel_job(self):
        """'초기화': 소프트 리셋 없이 진행 중인 작업만 깨끗이 지운다.
        (이미 기계에 보낸 마지막 몇 개 이동은 그대로 끝나요. 완전히 즉시 멈추려면 '비상 정지'를 쓰세요.)"""
        was_paused = self._paused
        with self._lock:
            self._manual.clear()
            self._job = None
        self._abort = True
        if was_paused and self.ser:
            self._raw(b"~")
        self._paused = False

    def _parse_status(self, txt):
        parts = txt.strip("<>").split("|")
        st = {"state": parts[0], "raw": txt, "t": time.time()}
        for pr in parts[1:]:
            if ":" not in pr:
                continue
            k, v = pr.split(":", 1)
            if k in ("MPos", "WPos", "WCO"):
                try:
                    st[k.lower()] = tuple(float(x) for x in v.split(","))
                except ValueError:
                    pass
            elif k == "Pn":
                st["pins"] = v
        if "wpos" not in st and "mpos" in st and hasattr(self, "_wco") and self._wco:
            st["wpos"] = tuple(m - o for m, o in zip(st["mpos"], self._wco))
        if "wco" in st:
            self._wco = st["wco"]
        self.status = st

    def clear_alarm(self):
        self.alarm = ""

    def in_alarm(self):
        """기계가 알람(ALARM) 상태인가."""
        st = str(self.status.get("state", "")).lower()
        return st.startswith("alarm") or "alarm" in self.alarm.lower()

    def _prepare_sync(self):
        """그리기 직전 자동 준비(블로킹용):

        1) 기계가 알람 상태면 소프트 리셋(Ctrl-X) + '$X' 로 잠금을 푼다.
        2) 소프트 리밋($20)이 켜져 있으면 끈다. 이 앱의 G코드는 회전각 X가
           0~360° 양수로 나가는데, GRBL 소프트 리밋은 좌표가 전부 0 이하(음수
           영역)에 있다고 가정하므로 $20=1 인 채로는 양수 이동마다 ALARM:2 가 뜬다.
           (다시 켜려면 '$20=1' 을 직접 명령창에 치면 돼요.)"""
        if not self.ser:
            self.emit("log", "먼저 기계에 연결하세요.")
            return False
        if self.job_active:
            self.emit("log", "이미 그리는 중이에요.")
            return False
        with self._lock:
            self._manual.clear()
            self._job = None
        self._paused = False
        if self.in_alarm() or self.alarm:
            self.emit("log", "기계가 알람 상태예요 → 소프트 리셋 + $X 로 자동 해제합니다...")
            self._raw(b"\x18")
            time.sleep(2.4)                    # GRBL 재시작 + 시작 메시지 대기
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
            self.status, self.alarm = {}, ""
            self._raw(b"$X\n")
            time.sleep(0.4)
        self.settings = {}
        self._raw(b"$$\n")                     # 설정을 다시 읽는다
        for _ in range(40):                    # 응답을 최대 4초 기다림
            time.sleep(0.1)
            if len(self.settings) >= 20:
                break
        if self.settings.get(20) == 1:
            self.emit("log", "소프트 리밋($20)이 켜져 있어 ALARM:2 의 원인이 돼요 → $20=0 으로 끕니다.")
            self._raw(b"$20=0\n")
            time.sleep(0.3)
            self.emit("log", "소프트 리밋($20)=0 저장 완료. (다시 켜려면 직접 명령창에 $20=1)")
        elif not self.settings:
            self.emit("log", "GRBL 설정($$)을 못 읽었어요. 연결 상태를 확인하세요.")
        self._raw(b"$X\n")                     # 마지막으로 한 번 더 잠금 해제
        time.sleep(0.2)
        return True

    def prepare(self, tag):
        """_prepare_sync 를 백그라운드 스레드에서 돌리고, 끝나면 ('prepared', tag, ok) 이벤트를 올린다."""
        def work():
            ok = False
            try:
                ok = self._prepare_sync()
            except Exception as e:
                self.emit("log", f"자동 준비 중 오류: {e}")
            self.emit("prepared", tag, ok)
        threading.Thread(target=work, daemon=True).start()

    def _raw(self, data):
        try:
            with self._wlock:
                self.ser.write(data)
        except Exception as e:
            self.emit("log", f"전송 오류: {e}")

    # ---- 작업 스레드 -----------------------------------------------------------
    def _loop(self):
        pending = deque()       # 아직 ok 를 못 받은 줄들의 바이트 수
        job, idx, total, acked = None, 0, 0, 0
        buf = b""
        last_poll = 0.0
        while self._run and self.ser:
            now = time.time()
            with self._lock:
                if self._abort:
                    if job is not None or pending:
                        self.emit("done", "abort")
                    job, idx, acked = None, 0, 0
                    pending.clear()
                    self.job_active = False
                    self._abort = False
                if job is None and self._job is not None:
                    job, self._job = self._job, None
                    idx, acked, total = 0, 0, len(job)
                    pending.clear()
                    self.emit("log", f"그리기 시작: {total}줄")
                    self.emit("state", "그리는 중")
            # --- 보내기 --- (pending 항목: (보낸 바이트 수, 줄 번호 또는 None, 보낸 글 내용))
            try:
                if not self._paused:
                    if job is not None:
                        while idx < total and sum(x[0] for x in pending) + len(job[idx]) + 1 <= self.RX_LIMIT:
                            self._raw((job[idx] + "\n").encode("utf-8"))
                            pending.append((len(job[idx]) + 1, idx, job[idx]))
                            idx += 1
                    elif not pending:
                        with self._lock:
                            line = self._manual.popleft() if self._manual else None
                        if line:
                            self._raw((line + "\n").encode("utf-8"))
                            pending.append((len(line) + 1, None, line))
                if now - last_poll > 0.6:
                    self._raw(b"?")
                    last_poll = now
                # --- 받기 ---
                chunk = self.ser.read(256)
            except Exception as e:
                self.emit("log", f"통신 오류: {e}")
                if job is not None:
                    self.emit("done", "error")
                self.job_active = False
                break
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    txt = raw.decode("utf-8", "replace").strip()
                    if not txt:
                        continue
                    low = txt.lower()
                    self.last_rx = time.time()
                    if not txt.startswith("<"):
                        self.rx_log.append(txt)
                    if txt.startswith("<"):
                        self._parse_status(txt)
                        self.emit("state", txt.strip("<>").split("|")[0])
                    elif re.match(r"^\$\d+=", txt):
                        k, v = txt[1:].split("=", 1)
                        try:
                            self.settings[int(k)] = float(v.split()[0])
                        except ValueError:
                            pass
                    elif low.startswith("grbl") or txt.startswith("[VER:"):
                        self.info = txt
                        self.emit("log", f"[기계] {txt}")
                    elif low.startswith("ok"):
                        popped_ok = pending.popleft() if pending else None
                        if job is not None:
                            acked += 1
                            self.emit("progress", acked, total, popped_ok[2] if popped_ok else "")
                    elif low.startswith("error") or "alarm" in low:
                        self.alarm = txt
                        popped = pending.popleft() if pending else None
                        detail = {
                            "idx": (popped[1] + 1) if popped and popped[1] is not None else None,
                            "line": popped[2] if popped else "",
                            "code": txt,
                            "desc": grbl_error_desc(txt),
                            "t": time.strftime("%H:%M:%S"),
                        }
                        self.error_log.append(detail)
                        self.last_error = detail
                        self.emit("log", f"[기계] {txt} — {detail['desc']}"
                                   + (f" (G코드 {detail['idx']}번째 줄: {detail['line']})" if detail["idx"] else ""))
                        self.emit("error_detail", detail)
                        if job is not None:
                            self.emit("done", "error")
                            job, pending = None, deque()
                            self.job_active = False
                    else:
                        self.emit("log", f"[기계] {txt}")
            if job is not None and idx >= total and not pending:
                self.emit("progress", total, total)
                self.emit("done", "ok")
                self.emit("state", "연결됨")
                job = None
                self.job_active = False


# =============================================================================
# 6. 3D 미리보기 창  (누운 달걀 + 펜 캐리지(서보) + 오른쪽 ATC 펜 교환)
#
#   좌표(mm):  X = 달걀 축(높이 h 가 +X 방향),  Y = 위,  Z = 보는 사람 쪽
#   달걀은 가로로 누워 있고, 펜은 위에서 수직으로 내려와 달걀 맨 위에 닿는다.
#   두 가지 보기:  'wire' = 손그림 같은 선 도식 / 'asm' = 색칠한 어셈블리(CATIA 스틸컷 느낌)
# =============================================================================
def _vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _vcross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _vunit(v):
    n = math.sqrt(_vdot(v, v)) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


_LIGHT = _vunit((-0.42, 0.80, 0.43))
_SHADE_CACHE = {}


def shade_hex(rgb, k):
    """색(rgb)에 밝기 k 를 곱한 '#rrggbb' (캐시)."""
    key = (rgb, int(k * 40))
    s = _SHADE_CACHE.get(key)
    if s is None:
        kk = key[1] / 40.0
        s = "#%02x%02x%02x" % tuple(max(0, min(255, int(c * kk))) for c in rgb)
        _SHADE_CACHE[key] = s
    return s


def hex_to_rgb(h):
    try:
        return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError, TypeError):
        return (128, 128, 128)


class Solid:
    """꼭짓점 V / 면 F(꼭짓점 번호들, 바깥 법선) / 모서리 E 묶음. 볼록한 도형만 쓴다."""
    __slots__ = ("V", "F", "E", "rgb", "lvl", "tag")

    def __init__(self, V, faces, E, rgb, lvl=2, tag=""):
        self.V, self.E, self.rgb, self.lvl, self.tag = V, E, rgb, lvl, tag
        n = float(len(V))
        ctr = (sum(v[0] for v in V) / n, sum(v[1] for v in V) / n, sum(v[2] for v in V) / n)
        self.F = []
        for idx in faces:
            nrm = _vcross(_vsub(V[idx[1]], V[idx[0]]), _vsub(V[idx[2]], V[idx[0]]))
            fc = (sum(V[i][0] for i in idx) / len(idx), sum(V[i][1] for i in idx) / len(idx),
                  sum(V[i][2] for i in idx) / len(idx))
            if _vdot(nrm, _vsub(fc, ctr)) < 0:
                nrm = (-nrm[0], -nrm[1], -nrm[2])
            self.F.append((idx, _vunit(nrm)))


_BOX_FACES = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 3, 7, 5))
_BOX_EDGES = ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7))


def box_solid(c, size, rgb, lvl=2, tag="", rot=0.0):
    """가운데 c, 크기 (sx,sy,sz) 인 상자. rot: Y축 둘레 회전(도)."""
    hx, hy, hz = size[0] / 2.0, size[1] / 2.0, size[2] / 2.0
    cs, sn = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    V = []
    for a in (-1, 1):
        for b in (-1, 1):
            for d in (-1, 1):
                x, z = a * hx, d * hz
                V.append((c[0] + x * cs + z * sn, c[1] + b * hy, c[2] - x * sn + z * cs))
    return Solid(V, _BOX_FACES, _BOX_EDGES, rgb, lvl, tag)


def cyl_solid(axis, c, length, r0, r1=None, rgb=(200, 200, 200), n=12, lvl=2, tag=""):
    """원기둥/원뿔대. axis 'x'|'y'|'z' 방향, 가운데 c, 한쪽 반지름 r0, 반대쪽 r1."""
    r1 = r0 if r1 is None else r1

    def at(u, v, w):
        if axis == "x":
            return (c[0] + u, c[1] + v, c[2] + w)
        if axis == "y":
            return (c[0] + v, c[1] + u, c[2] + w)
        return (c[0] + v, c[1] + w, c[2] + u)

    V = []
    for end, r in ((-length / 2.0, r0), (length / 2.0, r1)):
        for i in range(n):
            t = 2.0 * math.pi * i / n
            V.append(at(end, r * math.cos(t), r * math.sin(t)))
    F = [tuple(range(n)), tuple(range(n, 2 * n))]
    F += [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    E = [(i, (i + 1) % n) for i in range(n)] + [(n + i, n + (i + 1) % n) for i in range(n)]
    E += [(i, n + i) for i in range(0, n, max(1, n // 4))]
    return Solid(V, F, E, rgb, lvl, tag)


def egg_solid(shape, nl=14, nu=30):
    """달걀(공) 몸통 그물. 축은 월드 X. 몸통은 축 대칭이라 달걀이 돌아도 모양이 같다."""
    V = []
    hh = shape.half * 0.999
    for i in range(nl + 1):
        h = -hh + 2.0 * hh * i / nl
        r = shape.radius(h)
        for j in range(nu):
            t = 2.0 * math.pi * j / nu
            V.append((h, r * math.cos(t), r * math.sin(t)))
    F = []
    for i in range(nl):
        for j in range(nu):
            a, b = i * nu + j, i * nu + (j + 1) % nu
            F.append((a, b, b + nu, a + nu))
    return Solid(V, F, [], (240, 224, 150), 1, "egg")


def dimple_rings(shape, npts=10):
    """딤플마다 (가운데 법선(nx,nz), [(h,x,z) 테두리 점들]) - 달걀 안쪽 좌표. 딤플 없으면 []."""
    out = []
    if not shape.dimples:
        return out
    for deg, h in shape.dimple_centers():
        r = shape.radius(h)
        if r < 1.0 or abs(h) > shape.half * 0.985:
            continue
        a = math.radians(deg)
        ring = []
        for k in range(npts):
            t = 2.0 * math.pi * k / npts
            hh = h + DIMPLE_R * math.sin(t)
            if abs(hh) > shape.half * 0.995:
                break
            rr = shape.radius(hh)
            ang = a + DIMPLE_R * math.cos(t) / max(1.0, r)
            ring.append((hh, rr * math.sin(ang), rr * math.cos(ang)))
        if len(ring) == npts:
            out.append(((math.sin(a), math.cos(a)), (h, r * math.sin(a), r * math.cos(a)), ring))
    return out


class Layout:
    """기계 부품의 위치(mm). 달걀 크기와 설정(ATC 위치, 서보 값)에서 계산한다."""

    def __init__(self, shape, p):
        R, half = shape.max_radius(), shape.half
        self.R, self.half = R, half
        self.lift = max(3.0, min(14.0, 8.0 * (p.pen_up - p.pen_down) / 60.0))   # 서보가 펜을 들어 올리는 높이
        self.y_up = R + self.lift                    # 펜이 올라갔을 때 펜 끝 높이
        self.y_base = -R - 40.0
        self.xL = -half - 34.0                       # 왼쪽 고정부(회전 모터 쪽) 기둥
        self.xR = half + 34.0                        # 오른쪽 고정부(꼬리 쪽) 기둥
        self.xd = float(p.atc_pen_h)                 # 펜 교환할 때 펜 홀더 위치
        self.Rm = 30.0                               # 턴테이블 반지름(슬롯까지)
        self.dock = float(p.atc_dock)
        self.park = float(p.atc_park)
        self.y_g = self.y_up + 24.0                  # 홀더/턴테이블이 펜을 잡는 높이
        self.y_r = self.y_base + 26.0                # ATC 레일 높이
        self.beam_y = self.y_up + 56.0               # 펜 캐리지 레일(보)
        self.beam_z = -26.0
        self.pen_len = 44.0
        self.x_park = self.xd + self.Rm + self.dock - self.park    # atc=park 일 때 턴테이블 중심 x
        self.x_end = self.xd + self.Rm + self.dock + 70.0          # 레일 오른쪽 끝

    def turntable_x(self, atc):
        """ATC 위치 atc(mm)일 때 턴테이블 중심 x. atc=dock 이면 도킹 슬롯 펜이 홀더 펜 위치(xd)에 온다."""
        return self.xd + self.Rm + (self.dock - atc)


def pen_solids(x, y_tip, z, rgb, length, lvl=1, n=10, tag="pen", cap=True):
    """수직 펜(끝이 아래) 부품 목록."""
    out = [cyl_solid("y", (x, y_tip + 3.0, z), 6.0, 0.8, 3.4, tuple(int(c * 0.55) for c in rgb), n, lvl, tag),
           cyl_solid("y", (x, y_tip + 6.0 + (length - 6.0) / 2.0, z), length - 6.0, 3.4, 3.4, rgb, n, lvl, tag)]
    if cap:
        out.append(cyl_solid("y", (x, y_tip + length + 1.5, z), 3.0, 3.8, 3.8, (200, 200, 205), n, 2, tag))
    return out


def static_solids(shape, L: Layout):
    """움직이지 않는 부품(베이스, 고정부, 레일, 모터...). lvl 1 = 도식에도 나옴, 2 = 어셈블리에서만."""
    CY, GR, PU, BK, GY, BL = (0, 200, 205), (40, 215, 40), (122, 44, 240), (34, 34, 38), (176, 180, 186), (40, 90, 200)
    S = []
    half, R = L.half, L.R
    x0, x1 = L.xL - 70.0, L.x_end + 50.0
    S.append(box_solid(((x0 + x1) / 2, L.y_base - 3.0, 0), (x1 - x0, 6.0, 130.0), CY, 1, "base"))
    # --- 왼쪽 L자 고정부 + 달걀 회전 모터(NEMA17) + 척(원뿔)
    S.append(box_solid((L.xL, (L.y_base + 4.5) / 2, 0), (10, 4.5 - L.y_base, 18), CY, 1, "fix"))
    S.append(box_solid(((L.xL - half - 8) / 2 - 0.0, 0, 0), (-half - 8 - L.xL, 9, 14), CY, 1, "fix"))
    S.append(cyl_solid("x", (-half - 4.5, 0, 0), 9.0, 5.0, 1.2, GY, 12, 1, "chuck"))
    S.append(box_solid((L.xL - 28, 0, 0), (40, 42, 42), BK, 2, "motor"))
    S.append(cyl_solid("x", (L.xL - 5.0, 0, 0), 8.0, 2.6, 2.6, GY, 8, 2, "shaft"))
    # --- 오른쪽 L자 고정부 + 꼬리 척
    S.append(box_solid((L.xR, (L.y_base + 4.5) / 2, 0), (10, 4.5 - L.y_base, 18), CY, 1, "fix"))
    S.append(box_solid(((half + 8 + L.xR) / 2, 0, 0), (L.xR - half - 8, 9, 14), CY, 1, "fix"))
    S.append(cyl_solid("x", (half + 4.5, 0, 0), 9.0, 1.2, 5.0, GY, 12, 1, "chuck"))
    # --- 펜 캐리지 레일(보) + 뒤 기둥 + 모터
    bx0, bx1 = L.xL - 6.0, L.xd + 22.0
    S.append(box_solid(((bx0 + bx1) / 2, L.beam_y, L.beam_z), (bx1 - bx0, 8, 10), GY, 1, "beam"))
    for bx in (L.xL, bx1 - 6.0):
        S.append(box_solid((bx, (L.y_base + L.beam_y) / 2, L.beam_z - 4.0), (9, L.beam_y - L.y_base, 8), CY, 2, "post"))
    S.append(box_solid((bx0 - 20, L.beam_y, L.beam_z), (36, 42, 42), BK, 2, "motor"))
    S.append(cyl_solid("x", ((bx0 + bx1) / 2, L.beam_y - 8.0, L.beam_z), bx1 - bx0, 1.6, 1.6, (60, 60, 66), 8, 2, "screw"))
    # --- ATC 직선 레일 2개 + 리드스크류 + 양끝 브래킷 + 모터
    xa0, xa1 = L.xd - 34.0, L.x_end
    for zz in (-30.0, 30.0):
        S.append(cyl_solid("x", ((xa0 + xa1) / 2, L.y_r, zz), xa1 - xa0, 3.0, 3.0, GY, 10, 1, "rail"))
    S.append(cyl_solid("x", ((xa0 + xa1) / 2, L.y_r - 10.0, 0), xa1 - xa0, 2.4, 2.4, (70, 70, 78), 8, 2, "screw"))
    S.append(box_solid((xa0, (L.y_r + L.y_base) / 2, 0), (8, L.y_r - L.y_base + 14, 74), GR, 2, "brk"))
    S.append(box_solid((xa1, (L.y_r + L.y_base) / 2, 0), (8, L.y_r - L.y_base + 14, 74), GR, 2, "brk"))
    S.append(box_solid((xa1 + 25, L.y_r - 6.0, 0), (42, 42, 42), BK, 2, "motor"))
    # --- 제어 보드(어셈블리 느낌)
    S.append(box_solid((xa1 + 70, L.y_base + 5.0, -10), (80, 4, 56), (36, 140, 60), 2, "board"))
    for k in range(3):
        S.append(box_solid((xa1 + 48 + 22 * k, L.y_base + 11.0, -10), (14, 9, 14), BK, 2, "board"))
    return S


def egg_pose_pt(h, x, z, ca, sa):
    """달걀 안쪽 표면점 (h, x, z) 을 달걀 회전각(ca=cos,sa=sin)만큼 돌린 월드 좌표."""
    return (h, z * ca + x * sa, x * ca - z * sa)


THEMES = {
    "wire": dict(bg="#FAFAF7", line="#1F2937", dim="#C9CED6", grid="#8E98A8", red="#E11D48", text="#111827",
                 hint="#475569", blue="#2563EB", band="#D97706"),
    "asm": dict(bg="#4B4A86", line="#12122A", dim="#7473B0", grid="#B49A3A", red="#FF6B7D", text="#F1F5F9",
                hint="#D5D7F2", blue="#8FB4FF", band="#FFB84D"),
}


class Preview3D(tk.Toplevel):
    BG = "#2B3036"
    EGG_RGB = (240, 224, 150)

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("3D 미리보기 - " + app.params.kind)
        self.configure(bg=self.BG)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._place_window()

        self.yaw, self.pitch, self.zoom = -24.0, 18.0, 3.0
        self.pan = [0.0, 0.0]
        self.focus = (40.0, 0.0, 0.0)
        self.t = None                    # 재생 시간(초). None = 끝(전체 표시)
        self.playing = False
        self._last_tick = 0.0
        self._drag = None
        self._job_all = self._job_auto = self._job_play = None
        self._local = []                 # 선별 표면 좌표
        self._rings = []                 # 딤플 테두리
        self._egg = None
        self._static = []
        self._L = None
        self._vd = (0.0, 0.0, 1.0)
        self._pen_cols = app.params.pen_hex()
        self._setting = False
        self._panel_on = True

        self.style = tk.StringVar(value="wire")
        self.v_machine = tk.BooleanVar(value=True)
        self.v_live = tk.BooleanVar(value=True)
        self._live_on = False
        self._live_job = None
        self._live_cum = []
        self._live_rng = (0.0, 0.0)
        self._live_last = 0.0
        self.v_auto = tk.BooleanVar(value=False)
        self.v_back = tk.BooleanVar(value=False)
        self.v_dimple = tk.BooleanVar(value=False)
        self.v_speed = tk.StringVar(value="자동")
        self.v_status = tk.StringVar(value="")
        self.v_issue = tk.StringVar(value="")

        self._build_ui()
        self.refresh()
        self.bind("<Configure>", self._on_configure)
        self.after(120, self.fit_view)

    # ------------------------------------------------------------------ UI
    def _place_window(self):
        try:
            self.update_idletasks()
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            w, h = min(1000, sw - 80), min(900, sh - 90)
            self.geometry(f"{w}x{h}+{max(0, sw - w - 30)}+30")
        except tk.TclError:
            self.geometry("1000x860")

    def _btn(self, parent, text, cmd, bg="#4C566A", bold=False, **kw):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg="white", relief="flat", activebackground=bg,
                         activeforeground="white", font=self.app.font_b if bold else self.app.font, **kw)

    def _build_ui(self):
        fg = "#E2E8F0"
        top = tk.Frame(self, bg=self.BG)
        top.pack(fill="x", padx=8, pady=(8, 2))
        self.lbl_kind = tk.Label(top, text="", bg=self.BG, fg=fg, font=self.app.font_b)
        self.lbl_kind.pack(side="left", padx=(0, 10))

        def chk(parent, text, var, cmd):
            return tk.Checkbutton(parent, text=text, variable=var, command=cmd, bg=self.BG, fg=fg,
                                  selectcolor="#3B4252", activebackground=self.BG,
                                  activeforeground=fg, font=self.app.font)

        chk(top, "에그봇 기계 보기", self.v_machine, self.schedule_all).pack(side="left")
        chk(top, "실제 작동과 실시간 연동", self.v_live, lambda: None).pack(side="left", padx=(8, 0))
        chk(top, "딤플 강조", self.v_dimple, self.schedule_all).pack(side="left", padx=(6, 0))
        chk(top, "자동 회전", self.v_auto, self._on_auto).pack(side="left", padx=(6, 0))
        chk(top, "뒷면 선 비치기", self.v_back, self.schedule_all).pack(side="left", padx=(6, 0))
        self._btn(top, "그림 정리", self.app.tidy).pack(side="right")

        top2 = tk.Frame(self, bg=self.BG)
        top2.pack(fill="x", padx=8, pady=(2, 2))
        tk.Label(top2, text="보기:", bg=self.BG, fg="#94A3B8", font=self.app.font).pack(side="left")
        for txt, val in (("도식(선)", "wire"), ("어셈블리", "asm")):
            tk.Radiobutton(top2, text=txt, value=val, variable=self.style, command=self._on_style, bg=self.BG,
                           fg=fg, selectcolor="#3B4252", activebackground=self.BG, activeforeground=fg,
                           font=self.app.font).pack(side="left", padx=(6, 0))
        self._btn(top2, "전체 어셈블리 보기", lambda: self.show_assembly(), bg="#7C3AED", bold=True).pack(
            side="left", padx=(12, 0))
        for txt, key in (("정면", "front"), ("위", "top"), ("옆", "side"), ("기본", "home")):
            self._btn(top2, txt, lambda k=key: self.set_view(k)).pack(side="right", padx=(3, 0))
        tk.Label(top2, text="시점:", bg=self.BG, fg="#94A3B8", font=self.app.font).pack(side="right", padx=(8, 3))

        bar = tk.Frame(self, bg=self.BG)
        bar.pack(fill="x", padx=8, pady=(2, 2))
        self.btn_play = self._btn(bar, "▶ 그리기 재생", self.toggle_play, bg="#2563EB", bold=True, padx=8)
        self.btn_play.pack(side="left")
        self._btn(bar, "⏮", self.rewind).pack(side="left", padx=(4, 0))
        tk.Label(bar, text="그리기 진행", bg=self.BG, fg="#94A3B8", font=self.app.font).pack(
            side="left", padx=(10, 4))
        self.var_prog = tk.DoubleVar(value=1000.0)
        self.slider = tk.Scale(bar, from_=0, to=1000, orient="horizontal", variable=self.var_prog,
                               showvalue=False, bg=self.BG, fg=fg, troughcolor="#3B4252",
                               highlightthickness=0, command=self._on_slider)
        self.slider.pack(side="left", fill="x", expand=True)
        tk.Label(bar, text="속도", bg=self.BG, fg="#94A3B8", font=self.app.font).pack(side="left", padx=(8, 2))
        ttk.Combobox(bar, textvariable=self.v_speed, width=6, state="readonly",
                     values=["자동", "1배", "5배", "10배", "30배", "100배", "300배"]).pack(side="left")

        self.canvas = tk.Canvas(self, bg=THEMES["wire"]["bg"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=4)

        tk.Label(self, textvariable=self.v_status, bg=self.BG, fg="#A5D6A7", anchor="w",
                 font=self.app.font).pack(fill="x", padx=10)

        bot = tk.Frame(self, bg=self.BG)
        bot.pack(fill="x", padx=8, pady=(4, 2))
        self._btn(bot, "G코드 확인·수정", self.app.open_gcode_editor, bg="#10B981", bold=True, padx=10).pack(side="left")
        self.btn_panel = self._btn(bot, "값 수정 ▼", self._toggle_panel, bg="#475569", padx=10)
        self.btn_panel.pack(side="left", padx=6)
        tk.Label(bot, textvariable=self.v_issue, bg=self.BG, fg="#FCA5A5", anchor="w", justify="left",
                 font=self.app.font, wraplength=640).pack(side="left", fill="x", expand=True, padx=6)

        self.panel = tk.Frame(self, bg=C_PANEL, highlightthickness=1, highlightbackground=C_LINE)
        self._build_panel()
        self.panel.pack(fill="x", padx=8, pady=(2, 4))

        tk.Label(self, text="드래그: 시점 돌리기 | 휠: 확대/축소 | Shift+드래그: 화면 옮기기 | "
                            "펜 올림/내림은 서보모터(M3 S값)로 펜을 들었다 놓는 거예요",
                 bg=self.BG, fg="#8B95A5", font=self.app.font_s, anchor="w").pack(fill="x", padx=10, pady=(0, 6))

        c = self.canvas
        for b in ("<Button-1>", "<Button-3>"):
            c.bind(b, self._press)
        for b in ("<B1-Motion>", "<B3-Motion>"):
            c.bind(b, self._motion)
        c.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        c.bind("<MouseWheel>", self._wheel)
        c.bind("<Button-4>", lambda e: self._zoom_by(1.12))
        c.bind("<Button-5>", lambda e: self._zoom_by(1 / 1.12))

    def _build_panel(self):
        """3D 창 안에서 바로 값 고치기 (앱 설정과 같은 값)."""
        specs = (
            ("그림 위치 회전(°)", "pos_x_deg", 0, 359, 1, "{:g}"),
            ("그림 위치 높이(mm)", "pos_y_mm", -40, 40, 0.5, "{:g}"),
            ("그림 세로 크기(mm)", "height_mm", 5, 80, 0.5, "{:g}"),
            ("그림 가로 배율", "stretch_x", 0.2, 4.0, 0.05, "{:g}"),
            ("그림 기울기(°)", "rot_deg", -180, 180, 5, "{:g}"),
            ("펜 올림 서보(S)", "pen_up", 0, 180, 1, "{:g}"),
            ("펜 내림 서보(S)", "pen_down", 0, 180, 1, "{:g}"),
            ("그리기 속도(F)", "draw_feed", 300, 6000, 50, "{:g}"),
            ("이동 속도(F)", "travel_feed", 500, 8000, 100, "{:g}"),
            ("펜 교환 위치(mm)", "atc_pen_h", 20, 70, 0.5, "{:g}"),
            ("ATC 도킹 거리(mm)", "atc_dock", 10, 90, 1, "{:g}"),
        )
        self.panel.columnconfigure(0, weight=1, uniform="c")
        self.panel.columnconfigure(1, weight=1, uniform="c")
        for i, (lab, attr, lo, hi, step, fmt) in enumerate(specs):
            sl = Slider(self.panel, self.app, lab, attr, lo, hi, step, fmt)
            sl.grid(row=i // 2, column=i % 2, sticky="ew", padx=6, pady=0)
            sl.scale.bind("<ButtonRelease-1>", lambda e: self.after(160, self.app.sync_controls))

    def _toggle_panel(self):
        self._panel_on = not self._panel_on
        if self._panel_on:
            self.panel.pack(fill="x", padx=8, pady=(2, 4), before=self._hint_widget())
            self.btn_panel.config(text="값 수정 ▼")
        else:
            self.panel.pack_forget()
            self.btn_panel.config(text="값 수정 ▲")
        self.after(50, self.schedule_all)

    def _hint_widget(self):
        return self.pack_slaves()[-1]

    # ------------------------------------------------------------ 데이터 갱신
    @property
    def timeline(self):
        return self.app.timeline

    def refresh(self):
        """앱에서 선/물체/설정이 바뀌었을 때 호출."""
        app = self.app
        shape, p = app.shape, app.params
        self.title("3D 미리보기 - " + p.kind)
        self.lbl_kind.config(text="물체: " + p.kind)
        self._pen_cols = p.pen_hex()
        self._L = Layout(shape, p)
        self._static = static_solids(shape, self._L)
        self._egg = egg_solid(shape)
        self._rings = dimple_rings(shape)
        self._local = []
        for _kind, pl, pen in app.ordered:
            H, X, Z, NX, NZ = [], [], [], [], []
            for deg, h in pl:
                x, y, z = shape.surface(deg, h)
                ln = math.hypot(x, z) or 1.0
                H.append(y)
                X.append(x)
                Z.append(z)
                NX.append(x / ln)
                NZ.append(z / ln)
            self._local.append((H, X, Z, NX, NZ, pen))
        tl = self.timeline
        if self.t is not None and tl is not None:
            self.t = min(self.t, tl.total)
        self._update_issues()
        self.schedule_all()

    def _update_issues(self):
        app, p, L = self.app, self.app.params, self._L
        msg = []
        if app.out_of_range:
            msg.append("그림이 달걀 범위 밖이에요 → '그림 정리'를 누르거나 위치·크기를 줄이세요")
        if p.pen_down >= p.pen_up:
            msg.append("펜 내림 값이 올림 값 이상이에요 → 서보 값을 확인하세요")
        elif p.pen_up - p.pen_down < 20:
            msg.append("서보 올림/내림 차이가 작아요(펜이 안 떨어질 수 있어요)")
        if p.change_mode == CHANGE_MODES[0] and p.npen() > 1:
            if not getattr(p, "exch_at_origin", True) and p.atc_pen_h < L.half + 4:
                msg.append("펜 교환 위치가 달걀에 너무 가까워요")
            if p.atc_dock <= p.atc_park:
                msg.append("ATC 도킹 위치가 대기 위치보다 작아요")
        if app.shape.dimples and not (p.dimple_auto or p.dimple_comp):
            msg.append("딤플이 있는데 딤플 보정이 꺼져 있어요")
        if getattr(app, "gcode_edited", False):
            msg.append("편집한 G코드를 보고 있어요(설정을 바꿔도 이 G코드는 그대로예요)")
        self.v_issue.set("⚠ " + " / ".join(msg) if msg else "")

    # ------------------------------------------------------------ 카메라
    def _make_proj(self, W, H):
        yw, pt = math.radians(self.yaw), math.radians(self.pitch)
        cy_, sy_, cp, sp = math.cos(yw), math.sin(yw), math.cos(pt), math.sin(pt)
        fx, fy, fz = self.focus
        S, D = self.zoom, 900.0
        ox, oy = W / 2.0 + self.pan[0], H / 2.0 + self.pan[1]

        def P(x, y, z):
            x -= fx
            y -= fy
            z -= fz
            x1 = x * cy_ + z * sy_
            z1 = -x * sy_ + z * cy_
            y2 = y * cp - z1 * sp
            z2 = y * sp + z1 * cp
            k = D / (D - z2)
            return (ox + x1 * S * k, oy - y2 * S * k, z2)

        self._vd = (-sy_ * cp, sp, cy_ * cp)
        return P

    def _scene_box(self):
        L = self._L
        if self.style.get() == "wire":
            return (L.xL - 10, L.x_park + L.Rm + 14), (L.y_base - 6, L.beam_y + 12), (-40, 46)
        return (L.xL - 62, L.x_end + 95), (L.y_base - 8, L.beam_y + 14), (-62, 62)

    def fit_view(self):
        if self._L is None:
            return
        W, H = max(300, self.canvas.winfo_width()), max(200, self.canvas.winfo_height())
        (x0, x1), (y0, y1), (z0, z1) = self._scene_box()
        self.focus = ((x0 + x1) / 2, (y0 + y1) / 2, 0.0)
        self.pan = [0.0, 0.0]
        self.zoom = 1.0
        P = self._make_proj(W, H)
        pts = [P(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
        bw = max(p_[0] for p_ in pts) - min(p_[0] for p_ in pts)
        bh = max(p_[1] for p_ in pts) - min(p_[1] for p_ in pts)
        self.zoom = max(0.5, min((W - 50) / max(1.0, bw), (H - 40) / max(1.0, bh)))
        self.schedule_all()

    def set_view(self, key):
        self.yaw, self.pitch = {"front": (0.0, 0.0), "top": (0.0, 88.0), "side": (-90.0, 0.0),
                                "home": (-24.0, 18.0) if self.style.get() == "wire" else (-34.0, 24.0)}[key]
        self.fit_view()

    def show_assembly(self):
        self.style.set("asm")
        self.yaw, self.pitch = -34.0, 24.0
        self._on_style(refit=True)

    def _on_style(self, refit=True):
        self.canvas.config(bg=THEMES[self.style.get()]["bg"])
        if refit:
            self.fit_view()
        else:
            self.schedule_all()

    def _on_configure(self, e):
        if e.widget is self:
            self.schedule_all()

    def schedule_all(self, *_):
        if self._job_all is None:
            self._job_all = self.after(16, self._do_all)

    def _do_all(self):
        self._job_all = None
        try:
            self._render()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 자세 / 움직이는 부품
    def _pose(self):
        tl, p = self.timeline, self.app.params
        if tl is None or tl.count == 0:
            return dict(deg=0.0, h=0.0, pen=0.0, j=-1, f=0.0, tur=turret_start(p, 0), atc=p.atc_park, mount=0,
                        stroke=-1, k=0, done=0)
        ps = tl.pose(tl.total if self.t is None else self.t)
        if self.t is None:
            ps = dict(ps)
            ps["done"] = len(self._local)
            ps["stroke"] = -1
        return ps

    def _in_change(self, t):
        tl = self.timeline
        if tl is None:
            return None
        for ch in tl.changes:
            if ch[0] <= t <= ch[1]:
                return ch
        return None

    def _dynamic(self, pose):
        """움직이는 부품. 반환: (Solid 목록, 선 목록[(점들, 색키, 굵기, 점선, 모드)], 글자 목록)"""
        app, p, shape, L = self.app, self.app.params, self.app.shape, self._L
        cols = self._pen_cols
        CY, GR, PU, BK, BL = (0, 200, 205), (40, 215, 40), (122, 44, 240), (34, 34, 38), (40, 90, 200)
        S, lines, labels = [], [], []
        xh = max(L.xL + 8.0, min(L.xd + 10.0, pose["h"]))
        deg, h, pen = pose["deg"], pose["h"], max(0.0, min(1.0, pose["pen"]))
        if abs(h) < shape.half:
            y_down = max(0.0, shape.radius(h)) - shape.dimple_depth(deg, h)    # 딤플이면 펜이 파고듦(고무줄 덕분)
        else:
            y_down = L.y_up
        y_tip = L.y_up + (y_down - L.y_up) * pen
        mount = pose["mount"]
        # --- 펜 캐리지 / 홀더 / 서보
        S.append(box_solid((xh, L.beam_y, L.beam_z), (26, 14, 16), CY, 1, "carriage"))
        S.append(box_solid((xh, (L.beam_y + L.y_g) / 2, L.beam_z), (8, L.beam_y - L.y_g + 6, 8), PU, 1, "holder"))
        S.append(box_solid((xh, L.y_g, L.beam_z / 2.0), (8, 8, -L.beam_z), PU, 1, "holder"))
        for sx in (-5.4, 5.4):
            S.append(box_solid((xh + sx, L.y_g, 0), (3, 12, 8), PU, 1, "holder"))
        S.append(box_solid((xh, L.y_g, -5.4), (14, 12, 3), PU, 1, "holder"))
        S.append(box_solid((xh, L.y_g + 10, L.beam_z / 2.0), (14, 12, 20), BL, 1, "servo"))
        S.append(box_solid((xh + 5.4, L.y_g - 8, 3.5), (3, 4, 6), PU, 2, "holder"))
        top = y_tip + L.pen_len
        if mount >= 0:
            S += pen_solids(xh, y_tip, 0.0, hex_to_rgb(cols[mount % len(cols)]), L.pen_len, 1, 10, "pen%d" % mount)
            labels.append((xh, top + 9, 0, str(mount + 1), "pen%d" % mount, "b", "s"))
        lines.append(([(xh, L.y_g + 10, -3), (xh, top + 3, 0)], "blue", 2.5, None, "wa"))        # 서보 → 펜 연결 막대
        lines.append(([(xh + 5.4, top - 4, 1.5), (xh + 5.4, L.y_g - 8, 3.5)], "band", 2.2, None, "wa"))  # 고무줄
        labels.append((xh, L.y_g + 22, L.beam_z / 2.0 - 14, "서보", "blue", "s", "n"))
        labels.append((xh + 14, L.y_g - 12, 6, "고무줄", "band", "s", "w"))
        # --- ATC (직선 레일 위 캐리지 + 회전 턴테이블)
        if p.change_mode == CHANGE_MODES[0]:
            xc = L.turntable_x(pose["atc"])
            ns, y0 = max(1, p.npen()), L.y_r - 14.0
            S.append(box_solid((xc, (y0 + L.y_g - 3) / 2, 0), (50, L.y_g - 3 - y0, 64), GR, 1, "atc"))
            S.append(box_solid((xc, L.y_g - 22, -48), (30, 30, 30), BK, 2, "motor"))
            S.append(cyl_solid("y", (xc, L.y_g, 0), 6.0, 12, 12, GR, 14, 2, "hub"))
            ring = [(xc + (L.Rm + 9) * math.cos(2 * math.pi * k / 36), L.y_g, (L.Rm + 9) * math.sin(2 * math.pi * k / 36))
                    for k in range(37)]
            lines.append((ring, "line", 2.0, None, "w"))
            for i in range(ns):
                phi = math.radians(p.turret_deg(i) - pose["tur"])
                sx_, sz_ = xc - L.Rm * math.cos(phi), L.Rm * math.sin(phi)
                S.append(box_solid((xc - (L.Rm / 2) * math.cos(phi), L.y_g, (L.Rm / 2) * math.sin(phi)),
                                   (L.Rm, 5, 14), GR, 2, "arm", rot=math.degrees(phi) + 180.0))
                lines.append(([(xc, L.y_g, 0), (sx_, L.y_g, sz_)], "line", 2.0, None, "w"))
                lines.append(([(sx_ + 6 * math.cos(2 * math.pi * k / 14), L.y_g, sz_ + 6 * math.sin(2 * math.pi * k / 14))
                               for k in range(15)], "line", 1.4, None, "w"))
                if i != mount:
                    S += pen_solids(sx_, L.y_up, sz_, hex_to_rgb(cols[i % len(cols)]), L.pen_len, 1, 10, "pen%d" % i)
                    labels.append((sx_, L.y_up + L.pen_len + 9, sz_, str(i + 1), "pen%d" % i, "b", "s"))
            labels.append((xc, L.y_r - 34, 40, "ATC", "text", "b", "n"))
            # 직선 이동 화살표 (양방향)
            ya, za = L.y_r - 22.0, 52.0
            xa, xb = L.xd - 14.0, L.x_end - 6.0
            lines.append(([(xa, ya, za), (xb, ya, za)], "line", 1.6, None, "wa"))
            for sgn, xe in ((1, xa), (-1, xb)):
                lines.append(([(xe + sgn * 9, ya + 4, za), (xe, ya, za), (xe + sgn * 9, ya - 4, za)], "line", 1.6, None, "wa"))
            labels.append(((xa + xb) / 2, ya - 7, za, "이동", "text", "b", "n"))
            # 펜 교환 중이면 빨간 화살표(손그림의 '교체')
            t_now = (self.timeline.total if self.t is None else self.t) if self.timeline else 0.0
            if self._in_change(t_now) is not None:
                A = (xh, L.y_g + 34, 0.0)
                B = (xc, L.y_g + 34, 0.0)
                Cc = ((A[0] + B[0]) / 2, L.y_g + 34 + 18 + abs(A[0] - B[0]) * 0.18, 0.0)
                arc = []
                for k in range(21):
                    u = k / 20.0
                    arc.append(((1 - u) ** 2 * A[0] + 2 * u * (1 - u) * Cc[0] + u * u * B[0],
                                (1 - u) ** 2 * A[1] + 2 * u * (1 - u) * Cc[1] + u * u * B[1], 0.0))
                dx = B[0] - arc[-2][0]
                dy = B[1] - arc[-2][1]
                ln = math.hypot(dx, dy) or 1.0
                ux, uy = dx / ln, dy / ln
                lines.append((arc, "red", 3.0, None, "wa"))
                lines.append(([(B[0] - 7 * ux + 4 * uy, B[1] - 7 * uy - 4 * ux, 0), B, (B[0] - 7 * ux - 4 * uy, B[1] - 7 * uy + 4 * ux, 0)],
                              "red", 3.0, None, "wa"))
                labels.append((Cc[0], Cc[1] + 10, 0, "교체", "red", "b", "n"))
        return S, lines, labels

    # ------------------------------------------------------------ 그리기
    def _face_list(self, solids, P, vd):
        faces = []
        for s in solids:
            pv = [P(*v) for v in s.V]
            rgb = s.rgb
            for idx, n in s.F:
                if _vdot(n, vd) <= 0.0:
                    continue
                k = 0.40 + 0.60 * max(0.0, _vdot(n, _LIGHT))
                flat = []
                d = 0.0
                for i in idx:
                    q = pv[i]
                    flat.append(q[0])
                    flat.append(q[1])
                    d += q[2]
                faces.append((d / len(idx), flat, shade_hex(rgb, k), s.tag))
        faces.sort(key=lambda f: f[0])
        return faces

    def _flush(self, c, pts, front, color, width, want=True, dash=None):
        """점 목록을 앞면(want=True) 또는 뒷면 구간으로 나눠 선으로 그린다."""
        run = []
        for q, f in zip(pts, front):
            if f == want:
                run.append(q[0])
                run.append(q[1])
            else:
                if len(run) >= 4:
                    c.create_line(*run, fill=color, width=width, dash=dash, capstyle="round", joinstyle="round")
                run = []
        if len(run) >= 4:
            c.create_line(*run, fill=color, width=width, dash=dash, capstyle="round", joinstyle="round")

    def _egg_grid(self, c, P, a, th):
        """달걀 위 가로/세로 격자선. 세로선은 달걀과 같이 돌아서 회전이 보인다. 빨간 점선 = X=0 시작선."""
        shape = self.app.shape
        hh, vd1, vd2 = shape.half * 0.999, self._vd[1], self._vd[2]
        for k in range(5):
            hk = (-0.8 + 0.4 * k) * shape.half
            r = shape.radius(hk)
            ang = [2 * math.pi * j / 40 for j in range(41)]
            pts = [P(hk, r * math.cos(t), r * math.sin(t)) for t in ang]
            self._flush(c, pts, [math.cos(t) * vd1 + math.sin(t) * vd2 > 0.02 for t in ang], th["grid"], 1)
        for i in range(12):
            phi = math.radians(30.0 * i) - a
            cp_, sp_ = math.cos(phi), math.sin(phi)
            pts, fr = [], []
            for j in range(19):
                hk = -hh + 2.0 * hh * j / 18
                r = shape.radius(hk)
                pts.append(P(hk, r * cp_, r * sp_))
                fr.append(cp_ * vd1 + sp_ * vd2 > 0.02)
            if i == 0:
                self._flush(c, pts, fr, th["red"], 2, True, (5, 3))
            else:
                self._flush(c, pts, fr, th["grid"], 1)

    def _draw_dimples(self, c, P, ca, sa, mode):
        """골프공 딤플: 테두리를 어둡게 칠하고, 빛이 오는 쪽 반대로 밀린 밝은 안쪽 면을 겹쳐 '오목하게' 보이게 한다."""
        if not self._rings:
            return
        vd1, vd2 = self._vd[1], self._vd[2]
        hi = self.v_dimple.get()
        base = self.EGG_RGB if mode == "asm" else (236, 232, 214)
        lx, ly = -0.62, -0.78                      # 화면에서 빛이 오는 방향(왼쪽 위)
        for (nx, nz), (hc, xc_, zc_), ring in self._rings:
            nwy, nwz = nz * ca + nx * sa, nx * ca - nz * sa
            fac = nwy * vd1 + nwz * vd2
            if fac < 0.10:
                continue
            kb = 0.42 + 0.58 * max(0.0, _vdot((0.0, nwy, nwz), _LIGHT))
            pts = [P(hk, z * ca + x * sa, x * ca - z * sa) for hk, x, z in ring]
            cx = sum(q[0] for q in pts) / len(pts)
            cy = sum(q[1] for q in pts) / len(pts)
            rad = sum(math.hypot(q[0] - cx, q[1] - cy) for q in pts) / len(pts)
            rim = (235, 100, 100) if hi else base
            c.create_polygon([v for q in pts for v in (q[0], q[1])], fill=shade_hex(rim, kb * 0.62),
                             outline=shade_hex(rim, kb * 0.45))
            sh = 0.30 * rad
            inner = []
            for q in pts:
                inner += [cx + (q[0] - cx) * 0.74 - lx * sh, cy + (q[1] - cy) * 0.74 - ly * sh]
            c.create_polygon(inner, fill=shade_hex(rim, min(1.18, kb * 1.08)), outline="")

    def _draw_strokes(self, c, P, ca, sa, pose, th):
        cols = self._pen_cols
        vd1, vd2 = self._vd[1], self._vd[2]
        done, cur, kk = pose["done"], pose["stroke"], pose["k"]
        show_back = self.v_back.get()
        total = 0
        for si, st in enumerate(self._local):
            if si < done:
                total += len(st[0])
            elif si == cur:
                total += kk + 1
        step = 1 + total // 9000
        lw = 2.4 if self.style.get() == "wire" else 2.0
        for si, (H, X, Z, NX, NZ, pen) in enumerate(self._local):
            if si < done:
                n = len(H)
            elif si == cur:
                n = min(len(H), kk + 1)
            else:
                continue
            idx = list(range(0, n, step))
            if idx and idx[-1] != n - 1:
                idx.append(n - 1)
            pts, fr = [], []
            for i in idx:
                x, z = X[i], Z[i]
                pts.append(P(H[i], z * ca + x * sa, x * ca - z * sa))
                fr.append((NZ[i] * ca + NX[i] * sa) * vd1 + (NX[i] * ca - NZ[i] * sa) * vd2 > 0.03)
            if si == cur and pose["pen"] > 0.3:                       # 지금 펜이 닿은 위치까지 이어 그림
                sx_, sy_, sz_ = self.app.shape.surface(pose["deg"], pose["h"])
                pts.append(P(sy_, sz_ * ca + sx_ * sa, sx_ * ca - sz_ * sa))
                fr.append(True)
            col = cols[pen % len(cols)]
            if show_back:
                self._flush(c, pts, fr, th["dim"], 1, False, (2, 3))
            self._flush(c, pts, fr, col, lw, True)

    def _render(self):
        c = self.canvas
        c.delete("all")
        W, H = c.winfo_width(), c.winfo_height()
        if W < 60 or H < 60 or self._L is None or self._egg is None:
            return
        mode = self.style.get()
        th = THEMES[mode]
        c.config(bg=th["bg"])
        P = self._make_proj(W, H)
        vd = self._vd
        pose = self._pose()
        a = math.radians(pose["deg"])
        ca, sa = math.cos(a), math.sin(a)
        machine = self.v_machine.get()
        dyn, lines, labels = self._dynamic(pose) if machine else ([], [], [])
        solids = ([s for s in self._static] if machine else []) + dyn
        if mode == "wire":
            solids = [s for s in solids if s.lvl <= 1]

        def overlay():
            self._draw_dimples(c, P, ca, sa, mode)
            self._egg_grid(c, P, a, th)
            self._draw_strokes(c, P, ca, sa, pose, th)

        if mode == "asm":
            faces = self._face_list(solids + [self._egg], P, vd)
            last_egg = max((i for i, f in enumerate(faces) if f[3] == "egg"), default=-1)
            for i, (_d, flat, fill, tag) in enumerate(faces):
                c.create_polygon(flat, fill=fill, outline=fill if tag == "egg" else th["line"], width=1)
                if i == last_egg:
                    overlay()
            if last_egg < 0:
                overlay()
        else:
            for _d, flat, fill, _tag in self._face_list([self._egg], P, vd):
                c.create_polygon(flat, fill=fill, outline=fill)
            overlay()
            for s in solids:
                pv = [P(*v) for v in s.V]
                if s.tag.startswith("pen"):
                    col, wd = shade_hex(s.rgb, 0.85), 2.2
                elif s.tag == "base":
                    col, wd = th["dim"], 1.4
                else:
                    col, wd = th["line"], 1.6
                for i, j in s.E:
                    c.create_line(pv[i][0], pv[i][1], pv[j][0], pv[j][1], fill=col, width=wd)
        # 선(서보 막대, 고무줄, 레일 화살표, 교체 화살표...)
        for pts, ck, wd, dash, md in lines:
            if (mode == "wire" and "w" not in md) or (mode == "asm" and "a" not in md):
                continue
            q = [P(*pt) for pt in pts]
            c.create_line(*[v for z in q for v in z[:2]], fill=th.get(ck, ck), width=wd, dash=dash,
                          capstyle="round", joinstyle="round")
        for x, y, z, text, ck, size, md in labels:
            if (md == "w" and mode != "wire") or (md == "a" and mode != "asm"):
                continue
            q = P(x, y, z)
            if ck.startswith("pen"):
                col = self._pen_cols[int(ck[3:]) % len(self._pen_cols)]
                if mode == "asm":
                    col = "#FFFFFF"
            else:
                col = th.get(ck, th["text"])
            c.create_text(q[0], q[1], text=text, fill=col, font=self.app.font_b if size == "b" else self.app.font_s)
        self._update_status(pose)

    def _update_status(self, pose):
        tl, p = self.timeline, self.app.params
        tot = tl.total if tl else 0.0
        t = tot if self.t is None else self.t
        mount = pose["mount"]
        txt = "회전 %6.1f°   높이 %+5.1f mm   %s   펜 %s   %5.1f / %5.1f 초" % (
            pose["deg"] % 360.0, pose["h"], "펜 내림(그리는 중)" if pose["pen"] > 0.5 else "펜 올림(서보)",
            ("%d번" % (mount + 1)) if mount >= 0 else "없음(교환 중)", t, tot)
        ch = self._in_change(t) if tl else None
        if ch is not None:
            a_, b_ = ch[2], ch[3]
            atc, near = pose["atc"], pose["atc"] > p.atc_park + 0.5
            tb = abs(((pose["tur"] - p.turret_deg(b_) + 180.0) % 360.0) - 180.0) < 1.0
            if mount == a_ and near:
                what = "ATC가 다가와 쓰던 펜을 떼는 중"
            elif mount == -1 and near and tb:
                what = "ATC가 새 펜을 끼우는 중"
            elif mount == -1 and near:
                what = "ATC가 물러나며 쓰던 펜을 슬롯에 넣는 중"
            elif mount == -1:
                what = "턴테이블 회전: 새 펜 찾는 중"
            elif mount == b_:
                what = "새 펜 장착 완료 → 복귀"
            else:
                what = "펜 캐리지 교환 위치로 이동 / 턴테이블 맞추는 중"
            txt += "   ▶ 펜 교환 %d→%d: %s" % (a_ + 1, b_ + 1, what)
        self.v_status.set(txt)

    # ------------------------------------------------------------ 재생
    def _render_dyn(self):
        self._render()

    def schedule_dyn(self):
        self.schedule_all()

    def _speed(self):
        v = self.v_speed.get()
        tot = self.timeline.total if self.timeline else 1.0
        if v == "자동":
            return max(1.0, tot / 25.0)            # 약 25초 안에 끝나는 속도
        try:
            return float(v.replace("배", ""))
        except ValueError:
            return 1.0

    def toggle_play(self):
        tl = self.timeline
        if tl is None or tl.count < 2:
            return
        self.live_off()
        self.playing = not self.playing
        self.btn_play.config(text="■ 재생 정지" if self.playing else "▶ 그리기 재생")
        if self.playing:
            if self.t is None or self.t >= tl.total - 1e-6:
                self.t = 0.0
            if not self.v_machine.get():
                self.v_machine.set(True)
                self.fit_view()
            self._last_tick = time.perf_counter()
            self._tick()

    def rewind(self):
        self.live_off()
        self.t = 0.0
        self._sync_slider()
        self.schedule_all()

    # ---------------------------------------------------- 기계와 실시간 연동
    def live_off(self):
        self._live_on = False
        self._live_job = None

    def live_begin(self, cum):
        """그리기 시작: 줄 누적 시간표를 받고 3D 영상을 처음으로 되감는다."""
        self.live_off()
        self._live_cum = list(cum or [])
        self._live_rng = (0.0, 0.0)
        if not self.v_live.get() or not self.timeline or not self._live_cum or self._live_cum[-1] <= 0:
            return
        if self.playing:
            self.playing = False
            self.btn_play.config(text="▶ 그리기 재생")
        if not self.v_machine.get():
            self.v_machine.set(True)
            self.fit_view()
        self.t = 0.0
        self._live_on = True
        self._live_last = time.perf_counter()
        self._sync_slider()
        self.schedule_all()
        self._live_tick()

    def live_progress(self, acked, total):
        """기계가 보낸 진행(줄)을 받아 영상이 움직일 수 있는 시간 범위(lo~hi, 0~1)를 갱신."""
        cum = getattr(self, "_live_cum", None)
        if not self._live_on or not cum:
            return
        n = len(cum)
        tot = cum[-1]
        hi = cum[min(n - 1, max(0, acked - 1))] / tot
        lo = cum[min(n - 1, max(0, acked - 1 - GRBL_LAG))] / tot
        self._live_rng = (lo, hi)

    def _live_tick(self):
        if not self._live_on or not self.winfo_exists():
            return
        tl = self.timeline
        cum = getattr(self, "_live_cum", None)
        if not tl or not cum or tl.total <= 0:
            self._live_on = False
            return
        now = time.perf_counter()
        dt = min(0.25, now - self._live_last)
        self._live_last = now
        lo, hi = self._live_rng
        cur = (self.t or 0.0) / tl.total
        paused = bool(getattr(self.app.grbl, "_paused", False))
        if not paused:
            # 실제 시간 속도로 흘러가되, 기계가 알려준 범위(lo~hi)를 벗어나지 않게 붙잡는다
            cur += dt / max(1e-6, cum[-1])
        cur = max(lo, min(hi, cur)) if hi > 0 else cur
        self.t = min(cur, 0.9999) * tl.total
        self._sync_slider()
        try:
            self._render()
        except tk.TclError:
            return
        self._live_job = self.after(33, self._live_tick)

    def live_end(self, status):
        if not self._live_on:
            return
        self._live_on = False
        if status == "ok":
            self.t = None
        self._sync_slider()
        try:
            self.schedule_all()
        except tk.TclError:
            pass

    def _tick(self):
        self._job_play = None
        if not self.playing or not self.winfo_exists():
            return
        tl = self.timeline
        now = time.perf_counter()
        dt = min(0.25, now - self._last_tick)
        self._last_tick = now
        if tl is None:
            self.playing = False
            return
        self.t = (self.t or 0.0) + dt * self._speed()
        if self.t >= tl.total:
            self.t = None
            self.playing = False
            self.btn_play.config(text="▶ 그리기 재생")
        self._sync_slider()
        try:
            self._render()
        except tk.TclError:
            return
        if self.playing:
            self._job_play = self.after(22, self._tick)

    def _sync_slider(self):
        tl = self.timeline
        self._setting = True
        frac = 1.0 if (self.t is None or not tl or tl.total <= 0) else self.t / tl.total
        self.var_prog.set(frac * 1000.0)
        self._setting = False

    def _on_slider(self, _v):
        if self._setting:
            return
        tl = self.timeline
        if not tl or tl.total <= 0:
            return
        self.live_off()
        frac = self.var_prog.get() / 1000.0
        self.t = None if frac >= 0.999 else frac * tl.total
        self.schedule_all()

    # ------------------------------------------------------------ 마우스
    def _press(self, e):
        self._drag = (e.x, e.y, bool(e.state & 0x0001) or e.num == 3)

    def _motion(self, e):
        if not self._drag:
            return
        x0, y0, pan = self._drag
        dx, dy = e.x - x0, e.y - y0
        self._drag = (e.x, e.y, pan)
        if pan:
            self.pan[0] += dx
            self.pan[1] += dy
        else:
            self.yaw = (self.yaw + dx * 0.4) % 360.0
            self.pitch = max(-30.0, min(89.0, self.pitch + dy * 0.35))
        self.schedule_all()

    def _wheel(self, e):
        self._zoom_by(1.12 if e.delta > 0 else 1 / 1.12)

    def _zoom_by(self, k):
        self.zoom = max(0.5, min(60.0, self.zoom * k))
        self.schedule_all()

    def _on_auto(self):
        if self.v_auto.get():
            self._auto()

    def _auto(self):
        self._job_auto = None
        if not self.v_auto.get() or not self.winfo_exists():
            return
        self.yaw = (self.yaw + 1.4) % 360.0
        self.schedule_all()
        self._job_auto = self.after(40, self._auto)

    def close(self):
        self.playing = False
        self.v_auto.set(False)
        for j in (self._job_all, self._job_auto, self._job_play):
            if j:
                try:
                    self.after_cancel(j)
                except Exception:
                    pass
        self.app.preview = None
        self.destroy()


# =============================================================================
# 7. 작은 UI 부품
# =============================================================================
FONT_CANDIDATES = ("맑은 고딕", "Malgun Gothic", "Noto Sans CJK KR", "Noto Sans KR",
                   "NanumGothic", "Apple SD Gothic Neo", "Gulim", "UnDotum")

C_BG = "#F1F5F9"
C_PANEL = "#FFFFFF"
C_LINE = "#E2E8F0"
C_TEXT = "#1E293B"
C_SUB = "#64748B"
C_BLUE = "#2563EB"
AUTOSAVE_MS = 20000          # 자동 저장 주기(밀리초)
SESSION_DIR = os.path.join(os.path.expanduser("~"), ".eggbot_studio")
SESSION_FILE = os.path.join(SESSION_DIR, "autosave.json")
ATC_SETTINGS_FILE = os.path.join(SESSION_DIR, "atc_settings.json")   # 펜 교환(ATC) 설정 전용 - 프로젝트/새로 시작과 무관하게 유지
ATC_FIELDS = ("change_mode", "turret_axis", "turret_step", "turret_off", "turret_feed", "change_wait_ms",
              "change_pre", "change_post", "atc_axis", "atc_park", "atc_dock", "atc_feed", "atc_pen_h",
              "mech_use", "mech_half", "mech_home", "mech_script", "exch_at_origin", "pen_order_area",
              "return_first_pen")


def load_atc_settings():
    try:
        with open(ATC_SETTINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_atc_settings(params):
    """ATC 설정만 따로 저장 (임시파일에 쓴 뒤 교체 -> 도중에 꺼져도 파일이 깨지지 않음)."""
    d = {k: getattr(params, k) for k in ATC_FIELDS if hasattr(params, k)}
    d["_saved"] = time.strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(SESSION_DIR, exist_ok=True)
    tmp = ATC_SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ATC_SETTINGS_FILE)
    return ATC_SETTINGS_FILE




class ProgressPopup(tk.Toplevel):
    """'그리기 시작' 누르면 오른쪽에 뜨는 작은 진행도 창.
    보낸 줄/전체, 퍼센트, 경과/예상 남은 시간과 함께 그려지는 선을 미니 캔버스에 실시간으로 앱업한다."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("그리는 중")
        self.geometry("260x560")
        self.resizable(False, False)
        self.configure(bg=C_PANEL)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.withdraw)

        tk.Label(self, text="현재 그리기 진행도", bg=C_PANEL, fg=C_TEXT, font=app.font_b).pack(pady=(12, 4))
        self.canvas = tk.Canvas(self, width=224, height=170, bg="#FFFFFF",
                                 highlightthickness=1, highlightbackground=C_LINE)
        self.canvas.pack(padx=14, pady=4)
        _big_font = (app.font_b.cget("family"), app.font_b.cget("size") + 4, "bold")
        self.lbl_pct = tk.Label(self, text="0%", bg=C_PANEL, fg=C_TEXT, font=_big_font)
        self.lbl_pct.pack(pady=(4, 0))
        self.pbar = ttk.Progressbar(self, length=224, mode="determinate")
        self.pbar.pack(padx=14, pady=6)
        self.lbl_lines = tk.Label(self, text="0 / 0 줄", bg=C_PANEL, fg=C_SUB, font=app.font)
        self.lbl_lines.pack()
        self.lbl_time = tk.Label(self, text="", bg=C_PANEL, fg=C_SUB, font=app.font)
        self.lbl_time.pack(pady=(0, 8))

        btns = tk.Frame(self, bg=C_PANEL)
        btns.pack(pady=(0, 12))
        self.btn_pause = app._btn(btns, "⏸ 일시정지", app.pause_resume)
        self.btn_pause.pack(side="left", padx=3)
        app._btn(btns, "↺ 초기화", app.reset_job, bg="#64748B", fg="white").pack(side="left", padx=3)
        app._btn(btns, "■ 정지", app.estop, bg="#EF4444", fg="white").pack(side="left", padx=3)

        self.path = []          # [(x, y, 펜내림여부), ...]  그릴 경로(도/mm 공간)
        self._to_px = None
        self.total = 1
        self.start_t = time.time()

    # ---- 경로 준비 --------------------------------------------------------
    def set_path(self, path, total, path_idx=None):
        self.path = path
        self.path_idx = path_idx
        self.total = max(1, total)
        self.start_t = time.time()
        self.lbl_pct.config(text="0%")
        self.lbl_lines.config(text=f"0 / {self.total} 줄")
        self.lbl_time.config(text="")
        self._drawn = 0
        self._draw_bg()
        self.deiconify()
        self.lift()

    def _draw_bg(self):
        self.canvas.delete("all")
        self._to_px = None
        if not self.path:
            self.canvas.create_text(112, 85, text="(미리보기 없음)", fill=C_SUB)
            return
        xs = [p[0] for p in self.path]
        ys = [p[1] for p in self.path]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        w, h = 224, 170
        sx = (w - 20) / max(1e-6, x1 - x0)
        sy = (h - 20) / max(1e-6, y1 - y0)
        s = min(sx, sy)
        ox = (w - (x1 - x0) * s) / 2 - x0 * s
        oy = (h - (y1 - y0) * s) / 2 - y0 * s
        self._to_px = lambda x, y: (ox + x * s, h - (oy + y * s))
        prev = None
        for x, y, down in self.path:
            px, py = self._to_px(x, y)
            if prev is not None and down:
                self.canvas.create_line(*prev, px, py, fill="#CBD5E1", width=1, tags="bg")
            prev = (px, py)

    # ---- 진행 갱신 --------------------------------------------------------
    def show_line(self, idx, line):
        """지금 보내는 G코드 줄과 그 줄의 역할, 최근 보낸 줄 목록을 표시한다."""
        if not hasattr(self, "lbl_now"):
            self._recent = deque(maxlen=7)
            self.lbl_now = tk.Label(self, anchor="w", justify="left", wraplength=380,
                                    font=("맑은 고딕", 9, "bold"), fg="#1D4ED8")
            self.lbl_now.pack(side="bottom", fill="x", padx=8, pady=(2, 6))
            tk.Label(self, text="최근 보낸 줄 (아래가 최신)", anchor="w", fg="#555").pack(
                side="bottom", fill="x", padx=8)
            self.lst_recent = tk.Listbox(self, height=7, font=("Consolas", 9), activestyle="none")
            self.lst_recent.pack(side="bottom", fill="x", padx=8, pady=(0, 2))
        text = line.strip()
        self.lbl_now.config(text=f"현재 {idx}번째 줄: {text}\n→ {self._describe_line(text)}")
        self._recent.append(f"{idx:>5}  {text}")
        self.lst_recent.delete(0, "end")
        for t in self._recent:
            self.lst_recent.insert("end", t)
        self.lst_recent.see("end")

    @staticmethod
    def _describe_line(line):
        s = line.split(";")[0].strip().upper()
        if s.startswith("M3") or s.startswith("M96"):
            return "펜 올림/내림 (서보)"
        if re.match(r"G[01]\s+Z", s):
            return "턴테이블 회전 (펜 교환)"
        if re.match(r"G[01]\s+A", s):
            return "ATC 동작 (펜 집기/놓기)"
        if s.startswith("G4"):
            return "대기"
        if s.startswith("G1"):
            return "그리는 중"
        if s.startswith("G0"):
            return "빈 이동"
        return "기타"

    def update_progress(self, acked, total):
        self.total = max(1, total)
        frac = max(0.0, min(1.0, acked / self.total))
        self.pbar.config(maximum=self.total, value=acked)
        self.lbl_pct.config(text=f"{frac * 100:.0f}%")
        self.lbl_lines.config(text=f"{acked} / {self.total} 줄")
        elapsed = time.time() - self.start_t
        if frac > 0.01:
            remain = elapsed / frac * (1 - frac)
            self.lbl_time.config(text=f"경과 {_fmt_secs(elapsed)} · 남은 시간 약 {_fmt_secs(remain)}")
        else:
            self.lbl_time.config(text=f"경과 {_fmt_secs(elapsed)}")
        if self.path and self._to_px:
            if getattr(self, "path_idx", None):
                # 줄 번호 -> 경로 점 (GRBL 'ok' 는 실제 움직임보다 앞서므로 GRBL_LAG 만큼 뒤로)
                idx = max(1, bisect.bisect_right(self.path_idx, max(0, acked - GRBL_LAG)))
            else:
                idx = max(1, int(frac * len(self.path)))
            self._draw_progress(idx)

    def _draw_progress(self, idx):
        """경로의 앞 idx 점까지 초록색으로 그린다. 새로 생긴 구간만 덧그려서 실시간으로 빠르게 갱신."""
        idx = max(1, min(idx, len(self.path)))
        done = getattr(self, "_drawn", 0)
        if idx < done:                      # 뒤로 갔다 -> 처음부터 다시
            self.canvas.delete("prog")
            done = 0
        i0 = max(1, done)
        for i in range(i0, idx):
            x0, y0, _d0 = self.path[i - 1]
            x1, y1, d1 = self.path[i]
            if d1:
                a = self._to_px(x0, y0)
                b = self._to_px(x1, y1)
                self.canvas.create_line(*a, *b, fill="#10B981", width=2, tags="prog", capstyle="round")
        self._drawn = idx
        # 지금 펜 위치 표시 (빨간 점: 그리는 중 / 회색 점: 이동 중)
        x, y, d = self.path[idx - 1]
        px, py = self._to_px(x, y)
        self.canvas.delete("cur")
        self.canvas.create_oval(px - 3, py - 3, px + 3, py + 3, fill="#EF4444" if d else "#94A3B8",
                                outline="", tags="cur")

    def mark_done(self, status):
        txt = {"ok": "✅ 완료", "error": "❌ 오류로 중단됨", "abort": "⏹ 중단됨"}.get(status, status)
        self.lbl_pct.config(text=txt)

    def reset_view(self):
        self._drawn = 0
        self.canvas.delete("prog")
        self.canvas.delete("cur")
        self.pbar.config(value=0)
        self.lbl_pct.config(text="0%")
        self.lbl_lines.config(text=f"0 / {self.total} 줄")
        self.lbl_time.config(text="")
        self.start_t = time.time()


class Slider(tk.Frame):
    """[이름] [──●──] [값]  한 줄짜리 슬라이더. 값을 바꾸면 Params 에 바로 반영한다."""

    def __init__(self, master, app, label, attr, lo, hi, step, fmt="{:g}", dirty=False, bg=C_PANEL,
                 on_change=None):
        super().__init__(master, bg=bg)
        self.app, self.attr, self.lo, self.hi, self.step, self.fmt, self.dirty = app, attr, lo, hi, step, fmt, dirty
        self.on_change = on_change       # 값이 바뀔 때마다 추가로 부를 콜백(해당 창의 코드 미리보기 새로고침 등)
        self._quiet = False
        self.columnconfigure(1, weight=1)
        tk.Label(self, text=label, bg=bg, fg=C_TEXT, font=app.font, anchor="w", width=17).grid(row=0, column=0)
        self.scale = ttk.Scale(self, from_=lo, to=hi, orient="horizontal", command=self._moved)
        self.scale.grid(row=0, column=1, sticky="ew", padx=4)
        self.val = tk.Label(self, text="", bg=bg, fg=C_TEXT, font=app.font, width=6, anchor="e")
        self.val.grid(row=0, column=2)
        app.sliders.setdefault(attr, []).append(self)
        self.pull()

    def _snap(self, v):
        v = round(float(v) / self.step) * self.step
        v = max(self.lo, min(self.hi, v))
        return int(round(v)) if isinstance(getattr(self.app.params, self.attr), int) else round(v, 4)

    def _moved(self, raw):
        if self._quiet:
            return
        v = self._snap(raw)
        if v == getattr(self.app.params, self.attr):
            return
        setattr(self.app.params, self.attr, v)
        self.val.config(text=self.fmt.format(v))
        if self.dirty:
            self.app.art.dirty = True
            self.app.art.clear_selection()
        self.app.schedule_recompute()
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    def pull(self):
        """Params 값을 화면에 반영."""
        v = getattr(self.app.params, self.attr)
        self._quiet = True
        self.scale.set(v)
        self._quiet = False
        self.val.config(text=self.fmt.format(v))


# =============================================================================
# 8. 메인 앱
# =============================================================================
class App(tk.Tk):
    def __init__(self, restore=True):
        super().__init__()
        self.title("에그봇 G코드 생성기")
        self.geometry("1380x900")
        self.minsize(1100, 720)
        self.configure(bg=C_BG)
        self._init_fonts()

        self.params = Params()
        self.apply_atc_settings()
        self.shape = Shape("달걀", 57, 43, 0.12)
        self.axes = AxisMap()
        self.art = Artwork()
        self.ordered = []
        self.timeline = None
        self.gcode = []
        self.stats = {}
        self.out_of_range = False
        self.preview = None
        self.progress_win = None        # 그리는 진행도 미니 창
        self.grbl = GrblLink()
        self.sliders = {}
        self._recompute_job = None
        self._syncing = False
        self._tkimg = None
        self._img_view = (1.0, 0.0, 0.0)
        self._uw = None                  # 펼친 모습 변환 정보
        self._uw_drag = None
        self.image_path = ""
        self._origin_set = False         # 이번에 원점을 맞췄는지(자가 점검용)
        self._tools = {}                 # 열려 있는 도구 창 (보정/원점/자가 점검/펜 교환)
        self._stroke = None              # 직접 그리는 중인 선
        self._last_note = ""
        self.pen_chips = []
        self.paint_chips = []            # 이미지 위 '색 칠하기' 색 버튼
        self.v_paint = tk.IntVar(value=0)
        self.gcode_edited = False        # G코드 편집창에서 '3D에 적용'한 편집본을 쓰는 중인가
        self.origin = dict(saved=False, when="", g54=False, parked=False, home_use=False)

        self._style()
        self._build()
        self.after(60, self._poll_machine)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.refresh_ports(quiet=True)
        self.recompute()
        self.log("준비됨. '1. 이미지 열기'로 그림을 불러오세요.")
        if restore:
            self._restore_autosave()
        self.after(AUTOSAVE_MS, self._autosave_tick)

    # ------------------------------------------------------------ 폰트/스타일
    def _init_fonts(self):
        fam = "TkDefaultFont"
        try:
            have = set(tkfont.families(self))
            for cand in FONT_CANDIDATES:
                if cand in have:
                    fam = cand
                    break
        except tk.TclError:
            pass
        self.font = tkfont.Font(family=fam, size=9)
        self.font_b = tkfont.Font(family=fam, size=10, weight="bold")
        self.font_s = tkfont.Font(family=fam, size=8)
        self.font_mono = tkfont.Font(family="Consolas" if os.name == "nt" else "DejaVu Sans Mono", size=9)
        self.option_add("*Font", self.font)

    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", font=self.font, background=C_PANEL)
        st.configure("TNotebook", background=C_PANEL, borderwidth=0)
        st.configure("TNotebook.Tab", padding=(10, 4), font=self.font)
        st.configure("TScale", background=C_PANEL, troughcolor="#CBD5E1")
        st.configure("TCombobox", padding=3)
        st.configure("Horizontal.TProgressbar", troughcolor="#E2E8F0", background="#10B981")

    def _btn(self, parent, text, cmd, bg="#E2E8F0", fg="#334155", bold=False, **kw):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, relief="flat", bd=0,
                         activebackground=bg, activeforeground=fg, cursor="hand2",
                         font=self.font_b if bold else self.font, padx=kw.pop("padx", 10),
                         pady=kw.pop("pady", 5), **kw)

    # ------------------------------------------------------------------ 레이아웃
    def _build(self):
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=3)
        self.columnconfigure(2, weight=3)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)

        # ---------- 왼쪽: 버튼 + 설정 탭 + 통계
        left = tk.Frame(self, bg=C_PANEL, highlightthickness=1, highlightbackground=C_LINE)
        left.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(12, 6), pady=12)
        self._btn(left, "1. 이미지 열기", self.open_image, bg=C_BLUE, fg="white", bold=True).pack(
            fill="x", padx=10, pady=(10, 4))
        grow = tk.Frame(left, bg=C_PANEL)
        grow.pack(fill="x", padx=10, pady=4)
        grow.columnconfigure((0, 1), weight=1)
        self._btn(grow, "G코드 저장", self.save_gcode, bg="#10B981", fg="white", bold=True).grid(
            row=0, column=0, sticky="ew", padx=(0, 2))
        self._btn(grow, "G코드 확인·수정", self.open_gcode_editor, bg="#0EA5E9", fg="white", bold=True).grid(
            row=0, column=1, sticky="ew", padx=(2, 0))
        self._btn(left, "3D 미리보기 창 열기", self.open_preview, bg="#7C3AED", fg="white", bold=True).pack(
            fill="x", padx=10, pady=4)
        row = tk.Frame(left, bg=C_PANEL)
        row.pack(fill="x", padx=10, pady=(0, 2))
        for i, (txt, cmd) in enumerate((("작업 저장", self.save_project), ("작업 불러오기", self.open_project),
                                        ("새로 시작", self.new_project))):
            row.columnconfigure(i, weight=1)
            self._btn(row, txt, cmd).grid(row=0, column=i, sticky="ew", padx=2)
        row = tk.Frame(left, bg=C_PANEL)
        row.pack(fill="x", padx=10, pady=(0, 2))
        for i, (txt, name) in enumerate((("원점 자동 조정", "origin"), ("보정", "calib"), ("자가 점검", "check"))):
            row.columnconfigure(i, weight=1)
            self._btn(row, txt, lambda n=name: self.open_tool(n), bg="#FEF3C7", fg="#92400E").grid(
                row=0, column=i, sticky="ew", padx=2)
        self.lbl_file = tk.Label(left, text="📂 현재 파일: 없음", bg=C_PANEL, fg=C_SUB, font=self.font,
                                 anchor="w", wraplength=360, justify="left")
        self.lbl_file.pack(fill="x", padx=12, pady=(2, 4))

        self.nb = ttk.Notebook(left)
        self.nb.pack(fill="both", expand=True, padx=8, pady=4)
        self._tab_object()
        self._tab_extract()
        self._tab_position()
        self._tab_pen()
        self._tab_atc()

        self.lbl_stats = tk.Label(left, text="", bg=C_PANEL, fg="#334155", font=self.font,
                                  justify="left", anchor="w", wraplength=380)
        self.lbl_stats.pack(fill="x", padx=12, pady=(4, 10))

        # ---------- 가운데: 이미지 + 채우기
        center = tk.Frame(self, bg=C_PANEL, highlightthickness=1, highlightbackground=C_LINE)
        center.grid(row=0, column=1, sticky="nsew", padx=6, pady=(12, 6))
        self.v_imgtitle = tk.StringVar(value="")
        tk.Label(center, textvariable=self.v_imgtitle, bg=C_PANEL, fg=C_TEXT, font=self.font_b,
                 anchor="w").pack(fill="x", padx=12, pady=(10, 2))
        tb = tk.Frame(center, bg=C_PANEL)
        tb.pack(fill="x", padx=12, pady=(0, 2))
        self.v_tool = tk.StringVar(value="fill")
        for txt, val in (("영역 채우기", "fill"), ("🎨 색 칠하기", "paint"), ("✏ 직접 그리기", "draw"), ("지우개", "erase")):
            tk.Radiobutton(tb, text=txt, value=val, variable=self.v_tool, indicatoron=False,
                           command=self._on_tool, bg="#E2E8F0", selectcolor="#BFDBFE", relief="flat", bd=0,
                           padx=9, pady=3, font=self.font, activebackground="#CBD5E1").pack(side="left", padx=(0, 3))
        self._btn(tb, "되돌리기", self.undo_line, pady=2).pack(side="left", padx=(8, 2))
        self._btn(tb, "직접 선 모두 지우기", self.clear_lines, pady=2).pack(side="left", padx=2)
        self._btn(tb, "빈 캔버스", self.new_blank, pady=2).pack(side="left", padx=2)
        pal = tk.Frame(center, bg=C_PANEL)
        pal.pack(fill="x", padx=12, pady=(0, 2))
        tk.Label(pal, text="펜 색(RGB):", bg=C_PANEL, fg=C_TEXT, font=self.font).pack(side="left")
        self.paint_chips = []
        for i in range(3):
            b = tk.Radiobutton(pal, text=str(i + 1), value=i, variable=self.v_paint, indicatoron=False,
                               command=self._select_paint, width=4, font=self.font_b, relief="raised", bd=2,
                               cursor="hand2")
            b.pack(side="left", padx=2)
            b.bind("<Double-Button-1>", lambda e, i=i: self._pick_pen_color(i))
            self.paint_chips.append(b)
        self._btn(pal, "색 편집", self._edit_paint_color, pady=2).pack(side="left", padx=(6, 2))
        self._btn(pal, "기본 RGB 3색", self.default_rgb_pens, pady=2).pack(side="left", padx=2)
        self._btn(pal, "자동 색 배정", self.auto_assign_colors, pady=2).pack(side="left", padx=2)
        self.v_order = tk.StringVar(value="")
        tk.Label(center, textvariable=self.v_order, bg=C_PANEL, fg="#475569", font=self.font_s, anchor="w",
                 justify="left", wraplength=560).pack(fill="x", padx=12)
        self.img_canvas = tk.Canvas(center, bg="#F8FAFC", highlightthickness=0, height=300)
        self.img_canvas.pack(fill="both", expand=True, padx=12, pady=4)
        self.img_canvas.bind("<Button-1>", self._image_press)
        self.img_canvas.bind("<B1-Motion>", self._image_motion)
        self.img_canvas.bind("<ButtonRelease-1>", self._image_release)
        self.img_canvas.bind("<Configure>", lambda e: self._draw_image())
        self._on_tool()

        box = tk.Frame(center, bg=C_PANEL, highlightthickness=1, highlightbackground=C_LINE)
        box.pack(fill="x", padx=12, pady=(2, 10))
        tk.Label(box, text="영역 채우기", bg=C_PANEL, fg=C_TEXT, font=self.font_b).pack(
            anchor="w", padx=8, pady=(6, 2))
        row = tk.Frame(box, bg=C_PANEL)
        row.pack(fill="x", padx=8)
        row.columnconfigure((0, 1), weight=1)
        self._btn(row, "그림 영역 모두 채우기", self.fill_all).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self._btn(row, "채우기 모두 지우기", self.clear_fill).grid(row=0, column=1, sticky="ew", padx=(3, 0))
        self.v_outline = tk.BooleanVar(value=True)
        self.v_zigzag = tk.BooleanVar(value=True)
        self.v_home_end = tk.BooleanVar(value=self.params.return_home)
        tk.Checkbutton(box, text="다 그리면 원점(X0 Y0)으로 돌아오기", variable=self.v_home_end,
                       command=self._on_flags, bg=C_PANEL, font=self.font,
                       activebackground=C_PANEL).pack(anchor="w", padx=6)
        tk.Checkbutton(box, text="테두리 선 그리기", variable=self.v_outline, command=self._on_flags,
                       bg=C_PANEL, font=self.font, activebackground=C_PANEL).pack(anchor="w", padx=6)
        tk.Checkbutton(box, text="지그재그로 이어 그리기(펜 덜 듦)", variable=self.v_zigzag,
                       command=self._on_flags, bg=C_PANEL, font=self.font,
                       activebackground=C_PANEL).pack(anchor="w", padx=6)
        Slider(box, self, "채우기 선 간격(mm)", "fill_gap_mm", 0.3, 2.0, 0.05, "{:.2f}").pack(fill="x", padx=8)
        Slider(box, self, "채우기 각도(도)", "fill_angle", 0, 90, 5, "{:g}").pack(fill="x", padx=8, pady=(0, 6))

        # ---------- 오른쪽: 펼친 모습
        right = tk.Frame(self, bg=C_PANEL, highlightthickness=1, highlightbackground=C_LINE)
        right.grid(row=0, column=2, sticky="nsew", padx=(6, 12), pady=(12, 6))
        tk.Label(right, text="달걀 펼친 모습 (드래그: 이동 / 모서리: 크기 / 위쪽 동그라미: 회전 / 휠: 크기 / Shift+휠: 회전)",
                 bg=C_PANEL, fg=C_TEXT, font=self.font_b, wraplength=520, justify="left").pack(
            anchor="w", padx=12, pady=(10, 2))
        self.uw_canvas = tk.Canvas(right, bg="#F8FAFC", highlightthickness=0, cursor="fleur")
        self.uw_canvas.pack(fill="both", expand=True, padx=12, pady=4)
        c = self.uw_canvas
        c.bind("<Configure>", lambda e: self._draw_unwrap())
        c.bind("<Button-1>", self._uw_press)
        c.bind("<B1-Motion>", self._uw_motion)
        c.bind("<ButtonRelease-1>", lambda e: setattr(self, "_uw_drag", None))
        c.bind("<Motion>", self._uw_hover)
        c.bind("<MouseWheel>", self._uw_wheel)
        c.bind("<Button-4>", lambda e: self._uw_zoom(1, e.state))
        c.bind("<Button-5>", lambda e: self._uw_zoom(-1, e.state))
        self.v_zoom = tk.DoubleVar(value=2.0)
        self.v_auto = tk.BooleanVar(value=True)
        tk.Checkbutton(right, text="펼친 화면 자동으로 크게 (세로 꽉 채움)", variable=self.v_auto,
                       command=self._draw_unwrap, bg=C_PANEL, fg=C_TEXT, font=self.font_s).pack(anchor="w", padx=12)
        zf = tk.Frame(right, bg=C_PANEL)
        zf.pack(anchor="w", fill="x", padx=12)
        tk.Label(zf, text="펼친 화면 확대", bg=C_PANEL, fg=C_TEXT, font=self.font_s).pack(side="left")
        ttk.Scale(zf, from_=1.0, to=6.0, orient="horizontal", variable=self.v_zoom,
                  command=lambda v: self._draw_unwrap()).pack(side="left", fill="x", expand=True, padx=6)
        self.v_fill = tk.BooleanVar(value=False)
        tk.Checkbutton(right, text="화면 꽉 채우기(비율 무시)", variable=self.v_fill, command=self._draw_unwrap,
                       bg=C_PANEL, fg=C_TEXT, font=self.font_s).pack(anchor="w")
        self.v_travel = tk.BooleanVar(value=True)
        tk.Checkbutton(right, text="이동 경로 보기", variable=self.v_travel, command=self._draw_unwrap,
                       bg=C_PANEL, font=self.font, activebackground=C_PANEL).pack(anchor="w", padx=12, pady=(0, 6))

        # ---------- 아래: 기계 제어 + 로그
        self._build_machine_panel()

    # ------------------------------------------------------------------ 탭들
    def _tab(self, title):
        f = tk.Frame(self.nb, bg=C_PANEL)
        self.nb.add(f, text=title)
        return f

    def _tab_object(self):
        f = self._tab("물체")
        tk.Label(f, text="그릴 물체", bg=C_PANEL, font=self.font_b).pack(anchor="w", padx=10, pady=(10, 2))
        self.v_kind = tk.StringVar(value=self.params.kind)
        cb = ttk.Combobox(f, textvariable=self.v_kind, values=["달걀", "탁구공", "골프공"], state="readonly")
        cb.pack(fill="x", padx=10)
        cb.bind("<<ComboboxSelected>>", self._on_kind)
        Slider(f, self, "길이(mm, 회전축)", "length_mm", 20, 120, 1).pack(fill="x", padx=8, pady=(6, 0))
        Slider(f, self, "지름(mm, 굵은 곳)", "diameter_mm", 10, 100, 1).pack(fill="x", padx=8)
        Slider(f, self, "뾰족함(0=공)", "pointiness", 0, 0.3, 0.01, "{:.2f}").pack(fill="x", padx=8)
        self.v_has_dimples = tk.BooleanVar(value=False)
        tk.Checkbutton(f, text="표면에 딤플(오목한 홈) 있음", variable=self.v_has_dimples, command=self._on_flags,
                       bg=C_PANEL, font=self.font, activebackground=C_PANEL).pack(anchor="w", padx=8, pady=(4, 0))
        self.lbl_geo = tk.Label(f, text="", bg=C_PANEL, fg="#475569", font=self.font, justify="left", anchor="w")
        self.lbl_geo.pack(fill="x", padx=12, pady=(2, 6))

        df = tk.LabelFrame(f, text=" 딤플 보정 (골프공) ", bg="#F8FAFC", font=self.font_b, fg=C_TEXT)
        df.pack(fill="x", padx=8, pady=4)
        self.v_comp = tk.BooleanVar(value=False)
        tk.Checkbutton(df, text="딤플 보정 사용", variable=self.v_comp, command=self._on_flags,
                       bg="#F8FAFC", font=self.font, activebackground="#F8FAFC").pack(anchor="w", padx=6)
        self.v_dauto = tk.BooleanVar(value=self.params.dimple_auto)
        tk.Checkbutton(df, text="딤플 보정 자동 (딤플 위치를 알아서 감속·압력·겹침)", variable=self.v_dauto,
                       command=self._on_flags, bg="#F8FAFC", font=self.font,
                       activebackground="#F8FAFC").pack(anchor="w", padx=6)
        tk.Label(df, text="아래 값은 자동 보정의 '미세 조정'이에요.", bg="#F8FAFC", fg=C_SUB, font=self.font_s).pack(
            anchor="w", padx=8)
        for lab, attr, lo, hi, st in (("펜 더 누르기(S)", "dimple_press", 0, 40, 1),
                                      ("속도(%)", "dimple_speed", 20, 100, 5),
                                      ("같은 선 반복(회)", "dimple_repeat", 1, 4, 1),
                                      ("채우기 간격(%)", "dimple_fill", 40, 120, 5)):
            Slider(df, self, lab, attr, lo, hi, st, bg="#F8FAFC").pack(fill="x", padx=4)
        tk.Label(df, text="펜에 고무줄이 달려 아래로 당겨지니, 펜이 홈 안으로 따라 들어갔다 나와요.\n"
                          "그래서 딤플 안·가장자리는 천천히 지나고 필요하면 겹쳐 그려요.",
                 bg="#F8FAFC", fg="#94A3B8", font=self.font_s, justify="left").pack(anchor="w", padx=6, pady=(2, 6))

    def _tab_extract(self):
        f = self._tab("선 추출")
        tk.Label(f, text="끝선(윤곽) 따는 방식", bg=C_PANEL, font=self.font_b).pack(anchor="w", padx=10, pady=(10, 2))
        self.v_mode = tk.StringVar(value=self.params.extract_mode)
        cb = ttk.Combobox(f, textvariable=self.v_mode, values=list(EXTRACT_MODES), state="readonly")
        cb.pack(fill="x", padx=10)
        cb.bind("<<ComboboxSelected>>", self._on_mode)
        self.lbl_mode = tk.Label(f, text="", bg=C_PANEL, fg="#475569", font=self.font_s, justify="left",
                                 anchor="w", wraplength=330)
        self.lbl_mode.pack(fill="x", padx=12, pady=(2, 6))
        Slider(f, self, "배경 민감도(작을수록 예민)", "bg_tol", 5, 150, 5, dirty=True).pack(fill="x", padx=8)
        Slider(f, self, "색 개수(색 영역 방식)", "color_count", 2, 16, 1, dirty=True).pack(fill="x", padx=8)
        Slider(f, self, "그라데이션 색 합치기(인접 색 차이)", "color_merge", 0, 150, 5, dirty=True).pack(fill="x", padx=8)
        Slider(f, self, "밝기 임계값(어두운 선)", "threshold", 10, 245, 5, dirty=True).pack(fill="x", padx=8)
        Slider(f, self, "최소 영역 크기(px)", "min_region", 0, 500, 10, dirty=True).pack(fill="x", padx=8)
        Slider(f, self, "외곽선 단순화(mm)", "simplify_mm", 0.02, 2.0, 0.01, "{:.2f}").pack(fill="x", padx=8)
        tk.Label(f, text="※ 추출 설정을 바꾸면 채우기 선택이 초기화돼요.\n   (그 뒤 '그림 영역 모두 채우기'를 다시 누르세요)\n"
                         "※ 끝선이 안 잡히면 방식을 바꾸거나 '✏ 직접 그리기'로 선을 그려 보세요.",
                 bg=C_PANEL, fg="#94A3B8", font=self.font_s, justify="left").pack(anchor="w", padx=12, pady=8)
        self.lbl_mode.config(text=self.MODE_HELP.get(self.params.extract_mode, ""))

    MODE_HELP = {
        EXTRACT_MODES[0]: "배경색(가장자리 색)과 다른 부분을 그림으로 보고 끝선을 땁니다. 색이 있는 그림·밝은 색 그림도 OK. (투명 PNG는 투명 부분이 배경)",
        EXTRACT_MODES[1]: "그림을 색깔별로 나눠 각 색 덩어리의 끝선을 땁니다. 그림 안의 무늬·경계선도 따로 잡혀요.",
        EXTRACT_MODES[2]: "밝기가 임계값보다 어두운 부분만 선으로 봅니다. 흰 종이에 검은 선 그림에 적합해요.",
    }

    def _on_mode(self, _e=None):
        self.params.extract_mode = self.v_mode.get()
        self.art.dirty = True
        self.art.clear_selection()
        self.lbl_mode.config(text=self.MODE_HELP.get(self.params.extract_mode, ""))
        self.recompute(force=True)

    def _tab_position(self):
        f = self._tab("위치·크기")
        Slider(f, self, "그림 위치 X(도)", "pos_x_deg", 0, 359, 5).pack(fill="x", padx=8, pady=(12, 0))
        Slider(f, self, "그림 세로 위치(mm)", "pos_y_mm", -40, 40, 0.5).pack(fill="x", padx=8)
        Slider(f, self, "그림 세로 크기(mm)", "height_mm", 5, 80, 0.5).pack(fill="x", padx=8)
        Slider(f, self, "그림 가로 배율", "stretch_x", 0.2, 4.0, 0.05).pack(fill="x", padx=8)
        Slider(f, self, "그림 회전(도)", "rot_deg", -180, 180, 5).pack(fill="x", padx=8)
        Slider(f, self, "펜 팔 최대 각도(도)", "x_max_deg", 90, 359, 5).pack(fill="x", padx=8)

    def _tab_pen(self):
        f = self._tab("펜·속도")
        Slider(f, self, "펜 올림 서보(S)", "pen_up", 0, 180, 1).pack(fill="x", padx=8, pady=(12, 0))
        Slider(f, self, "펜 내림 서보(S)", "pen_down", 0, 180, 1).pack(fill="x", padx=8)
        Slider(f, self, "펜 딜레이(ms)", "dwell_ms", 0, 1500, 50).pack(fill="x", padx=8)
        Slider(f, self, "그리기 속도(F)", "draw_feed", 300, 6000, 50).pack(fill="x", padx=8)
        Slider(f, self, "이동 속도(F)", "travel_feed", 300, 6000, 50).pack(fill="x", padx=8)
        tk.Label(f, text="※ M3 S 서보 값은 펌웨어/서보마다 달라요.\n   실제 기계에 맞는지 꼭 확인하세요.",
                 bg=C_PANEL, fg="#94A3B8", font=self.font_s, justify="left").pack(anchor="w", padx=12, pady=8)

    def _tab_atc(self):
        f = self._tab("펜 교환(ATC)")
        Slider(f, self, "펜 개수(에그봇1+ATC2)", "pen_count", 1, 3, 1).pack(fill="x", padx=8, pady=(10, 0))
        row = tk.Frame(f, bg=C_PANEL)
        row.pack(fill="x", padx=10, pady=2)
        tk.Label(row, text="펜 색", bg=C_PANEL, font=self.font, width=6, anchor="w").pack(side="left")
        self.pen_chips = []
        for i in range(3):
            b = tk.Button(row, text=str(i + 1), width=3, relief="flat", bd=1, font=self.font_b,
                          command=lambda i=i: self._pick_pen_color(i), cursor="hand2")
            b.pack(side="left", padx=2)
            self.pen_chips.append(b)
        self._btn(f, "그림에서 펜 색 자동으로 뽑기", self.auto_pen_colors, pady=3).pack(fill="x", padx=10, pady=(2, 4))
        Slider(f, self, "테두리 그릴 펜 번호", "pen_outline", 1, 3, 1).pack(fill="x", padx=8)
        Slider(f, self, "채우기 그릴 펜 번호", "pen_fill", 1, 3, 1).pack(fill="x", padx=8)
        Slider(f, self, "직접 선 그릴 펜 번호", "pen_draw", 1, 3, 1).pack(fill="x", padx=8)
        self.v_follow = tk.BooleanVar(value=self.params.outline_follow)
        tk.Checkbutton(f, text="테두리도 그 영역을 칠한 색 펜으로", variable=self.v_follow, command=self._on_flags,
                       bg=C_PANEL, font=self.font, activebackground=C_PANEL).pack(anchor="w", padx=8)
        self.v_bycolor = tk.BooleanVar(value=self.params.pen_by_color)
        tk.Checkbutton(f, text="채우기를 영역 색과 가장 가까운 펜으로 자동 배정", variable=self.v_bycolor,
                       command=self._on_flags, bg=C_PANEL, font=self.font, activebackground=C_PANEL).pack(
            anchor="w", padx=8)
        tk.Label(f, text="펜 교환 방식", bg=C_PANEL, font=self.font_b).pack(anchor="w", padx=10, pady=(8, 0))
        self.v_chgmode = tk.StringVar(value=self.params.change_mode)
        cb = ttk.Combobox(f, textvariable=self.v_chgmode, values=list(CHANGE_MODES), state="readonly")
        cb.pack(fill="x", padx=10)
        cb.bind("<<ComboboxSelected>>", self._on_flags)
        Slider(f, self, "슬롯 간 각도(0=자동)", "turret_step", 0, 180, 0.1, "{:.1f}").pack(fill="x", padx=8, pady=(4, 0))
        Slider(f, self, "교환 회전 속도(도/분)", "turret_feed", 100, 6000, 100).pack(fill="x", padx=8)
        Slider(f, self, "교환 대기(ms)", "change_wait_ms", 0, 3000, 100).pack(fill="x", padx=8)
        self._btn(f, "⚙ ATC 메커니즘 설정 (저장됨)…", lambda: self.open_tool("atcset"), bg="#2563EB", fg="white",
                  bold=True, pady=4).pack(fill="x", padx=10, pady=(6, 2))
        self._btn(f, "교환 설정·매크로·시험…", lambda: self.open_tool("atc"), pady=3).pack(fill="x", padx=10, pady=(2, 2))
        tk.Label(f, text="※ 로터리 펜 매거진의 축(기본 Z)·각도·매크로는 기계마다 달라요.\n   3D 창에서 교환 동작을 먼저 확인하고, 실제 기계에서 시험하세요.",
                 bg=C_PANEL, fg="#94A3B8", font=self.font_s, justify="left").pack(anchor="w", padx=12, pady=4)

    def _refresh_chips(self):
        p = self.params
        hexes = p.pen_hex()
        rgbs = p.pen_rgb()
        sel = self.v_paint.get()
        if sel >= p.npen():
            self.v_paint.set(0)
            sel = 0
        for i, b in enumerate(getattr(self, "pen_chips", [])):
            on = i < p.npen()
            r, g, bl = rgbs[i]
            fg = "white" if (r * 299 + g * 587 + bl * 114) / 1000 < 150 else "black"
            b.config(bg=hexes[i], fg=fg, activebackground=hexes[i], activeforeground=fg,
                     state="normal" if on else "disabled", relief="solid" if on else "flat")
        for i, b in enumerate(getattr(self, "paint_chips", [])):
            on = i < p.npen()
            r, g, bl = rgbs[i]
            fg = "white" if (r * 299 + g * 587 + bl * 114) / 1000 < 150 else "black"
            b.config(bg=hexes[i], fg=fg, selectcolor=hexes[i], activebackground=hexes[i], activeforeground=fg,
                     state="normal" if on else "disabled", text=("▶%d" % (i + 1)) if i == sel else str(i + 1),
                     relief="sunken" if i == sel else "raised")

    def _select_paint(self):
        # 직접 그리기 중에 색만 바꾸려고 클릭했을 때는 도구를 색칠하기로 바꾸지 않고
        # 선택된 펜 번호만 바꾼다 (이 정보가 다음에 그릴 선의 색이 된다).
        if self.v_tool.get() != "draw":
            self.v_tool.set("paint")
            self._on_tool()
        self._refresh_chips()

    def _edit_paint_color(self):
        self._pick_pen_color(self.v_paint.get())

    def default_rgb_pens(self):
        """펜 색을 기본 RGB 3색(빨강/초록/파랑)으로. 에그봇에 1개 + ATC 에 2개."""
        p = self.params
        cur = p.pen_hex()
        cur[0:3] = ["#DC2626", "#16A34A", "#1D4ED8"]
        p.pen_colors = ",".join(cur)
        p.pen_count = 3
        self.sync_controls()
        self.log("펜 색을 기본 RGB 3색으로 맞췄어요: 1번 빨강 / 2번 초록 / 3번 파랑 (색 편집으로 바꿀 수 있어요)")
        self.recompute()

    def auto_assign_colors(self):
        """그림 속 영역 색과 가장 가까운 펜으로 모든 영역을 자동 배정."""
        a, p = self.art, self.params
        if a.img is None or not a.sizes:
            self.log("먼저 그림을 불러오세요.")
            return
        a.auto_assign(p.pen_rgb()[:p.npen()])
        self.log(f"영역 {len(a.sizes)}개를 가장 가까운 펜 색으로 배정했어요.")
        self.recompute()

    def _pick_pen_color(self, i):
        cur = self.params.pen_hex()
        res = colorchooser.askcolor(color=cur[i], title=f"{i + 1}번 펜 색")
        if not res or not res[1]:
            return
        cur[i] = res[1].upper()
        self.params.pen_colors = ",".join(cur)
        self._refresh_chips()
        self.schedule_recompute()
        self._draw_image()

    def auto_pen_colors(self):
        """그림 속 색을 펜 개수만큼 대표색으로 뽑아 펜 색으로 설정."""
        a, p = self.art, self.params
        if a.img is None or a.mask is None or not any(a.mask):
            self.log("펜 색을 뽑으려면 먼저 그림을 불러오세요.")
            return
        n = p.npen()
        px = a.img.tobytes()
        cols = b"".join(px[i * 3:i * 3 + 3] for i, m in enumerate(a.mask) if m)
        sub = Image.frombytes("RGB", (len(cols) // 3, 1), cols)
        q = getattr(Image, "Quantize", Image)
        dth = getattr(Image, "Dither", Image)
        qi = sub.quantize(colors=max(1, n), method=q.MEDIANCUT, dither=dth.NONE)
        pal = qi.getpalette()
        found = sorted(qi.getcolors(), reverse=True)
        picked = ["#%02X%02X%02X" % tuple(pal[idx * 3:idx * 3 + 3]) for _cnt, idx in found][:n]
        cur = p.pen_hex()
        for i, c in enumerate(picked):
            cur[i] = c
        p.pen_colors = ",".join(cur)
        if n > 1:
            p.pen_by_color = True
            self.v_bycolor.set(True)
        self._refresh_chips()
        self.log(f"그림에서 펜 색 {len(picked)}개를 뽑았어요: {', '.join(picked)}")
        self.recompute()

    # --------------------------------------------------------- 기계 제어 패널
    def _build_machine_panel(self):
        bot = tk.Frame(self, bg=C_PANEL, highlightthickness=1, highlightbackground=C_LINE)
        bot.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(6, 12), pady=(0, 12))
        tk.Label(bot, text="5. 기계 연결 / 제어 (GRBL)", bg=C_PANEL, fg=C_TEXT, font=self.font_b).pack(
            anchor="w", padx=12, pady=(8, 2))

        r1 = tk.Frame(bot, bg=C_PANEL)
        r1.pack(fill="x", padx=12, pady=2)
        tk.Label(r1, text="포트:", bg=C_PANEL, font=self.font).pack(side="left")
        self.v_port = tk.StringVar()
        self.port_cb = ttk.Combobox(r1, textvariable=self.v_port, width=12)
        self.port_cb.pack(side="left", padx=4)
        self._btn(r1, "새로고침", self.refresh_ports).pack(side="left", padx=2)
        self.btn_conn = self._btn(r1, "연결", self.connect, bg=C_BLUE, fg="white", bold=True)
        self.btn_conn.pack(side="left", padx=2)
        self._btn(r1, "끊기", self.disconnect, bg="#64748B", fg="white").pack(side="left", padx=2)
        self._btn(r1, "펜 올림", lambda: self.manual(f"M3 S{self.params.pen_up}")).pack(side="left", padx=(12, 2))
        self._btn(r1, "펜 내림", lambda: self.manual(f"M3 S{self.params.pen_down}")).pack(side="left", padx=2)
        self._btn(r1, "여기를 원점(0,0)으로", self.set_origin_here).pack(side="left", padx=2)
        self._btn(r1, "원점으로 이동", self.go_to_origin, bg="#F59E0B", fg="white", bold=True).pack(side="left", padx=2)
        self._btn(r1, "잠금해제 $X", lambda: self.manual("$X")).pack(side="left", padx=2)
        self._btn(r1, "축 설정", self.axis_dialog).pack(side="left", padx=2)
        self._btn(r1, "회전 보정", self.calib_rotation).pack(side="left", padx=2)
        self._btn(r1, "GRBL 설정", self.grbl_settings_dialog).pack(side="left", padx=2)
        self._btn(r1, "⚠ 오류 기록", self.open_error_log).pack(side="left", padx=2)

        r2 = tk.Frame(bot, bg=C_PANEL)
        r2.pack(fill="x", padx=12, pady=4)
        tk.Label(r2, text="조그(도):", bg=C_PANEL, font=self.font).pack(side="left")
        self.v_jog = tk.StringVar(value="10")
        ttk.Combobox(r2, textvariable=self.v_jog, values=["1", "5", "10"], width=4, state="readonly").pack(
            side="left", padx=4)
        for txt, rot, sg in (("X-", True, -1), ("X+", True, 1), ("Y-", False, -1), ("Y+", False, 1)):
            self._btn(r2, txt, lambda a=rot, b=sg: self.jog(a, b), padx=8).pack(side="left", padx=2)
        self.btn_plot = self._btn(r2, "▶ 그리기 시작", self.start_plot, bg="#10B981", fg="white", bold=True)
        self.btn_plot.pack(side="left", padx=(14, 3))
        self.btn_estop = self._btn(r2, "■ 비상 정지", self.estop, bg="#EF4444", fg="white", bold=True)
        self.btn_estop.pack(side="left", padx=3)
        self.btn_pause = self._btn(r2, "⏸ 일시정지", self.pause_resume, bg="#F59E0B", fg="white", bold=True)
        self.btn_pause.pack(side="left", padx=3)
        self.btn_reset = self._btn(r2, "↺ 초기화", self.reset_job, bg="#64748B", fg="white", bold=True)
        self.btn_reset.pack(side="left", padx=3)
        self.pbar = ttk.Progressbar(r2, length=140, mode="determinate")
        self.pbar.pack(side="left", padx=8)
        self.v_state = tk.StringVar(value="연결 안 됨")
        tk.Label(r2, textvariable=self.v_state, bg=C_PANEL, fg=C_SUB, font=self.font, width=14,
                 anchor="w").pack(side="left")

        r3 = tk.Frame(bot, bg=C_PANEL)
        r3.pack(fill="x", padx=12, pady=(2, 4))
        tk.Label(r3, text="직접 명령:", bg=C_PANEL, font=self.font).pack(side="left")
        self.entry = tk.Entry(r3, font=self.font_mono, relief="solid", bd=1)
        self.entry.pack(side="left", fill="x", expand=True, padx=6)
        self.entry.bind("<Return>", lambda e: self.send_entry())
        self._btn(r3, "보내기", self.send_entry).pack(side="left")

        self.txt = tk.Text(bot, height=6, font=self.font, wrap="word", bg="#F8FAFC", fg="#334155",
                           relief="flat", state="disabled")
        self.txt.pack(fill="x", padx=12, pady=(2, 10))

    # ================================================================ 동작
    def log(self, msg):
        self.txt.config(state="normal")
        self.txt.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.txt.see("end")
        self.txt.config(state="disabled")

    def open_image(self):
        if not HAS_PIL:
            messagebox.showerror("라이브러리 없음", "Pillow 가 필요해요.\npython -m pip install pillow")
            return
        path = filedialog.askopenfilename(title="이미지 열기",
                                          filetypes=[("이미지", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                                                     ("모든 파일", "*.*")])
        if path:
            self.load_image(path)

    def load_image(self, path):
        try:
            im = Image.open(path)
            im.load()
        except Exception as e:
            messagebox.showerror("열기 실패", str(e))
            return
        self.art.load(im)
        self.image_path = path
        self.lbl_file.config(text="📂 현재 파일: " + os.path.basename(path), fg="#0F172A")
        self.log(f"이미지 열기: {path}")
        self.recompute(force=True)

    TOOL_TITLE = {"fill": "이미지 (클릭: 그 칸 채우기 / 다시 클릭: 취소)",
                  "paint": "이미지 (위에서 펜 색을 고르고, 선(테두리 포함)을 클릭하면 그 선 색만 바뀜 / 안쪽을 클릭하면 영역 전체를 칠해요)",
                  "draw": "이미지 (드래그: 선 그리기 - 직접 그린 선도 G코드에 들어가요)",
                  "erase": "이미지 (드래그: 직접 그린 선 지우기)"}

    def _on_tool(self):
        self.v_imgtitle.set(self.TOOL_TITLE[self.v_tool.get()])
        self.img_canvas.config(cursor={"fill": "arrow", "paint": "hand2", "draw": "pencil",
                                       "erase": "dotbox"}[self.v_tool.get()])

    def _img_xy(self, e):
        s, ox, oy = self._img_view
        return (e.x - ox) / s, (e.y - oy) / s

    def _clamp_px(self, x, y):
        return max(0.0, min(float(self.art.w), x)), max(0.0, min(float(self.art.h), y))

    def _image_press(self, e):
        a = self.art
        if a.img is None:
            return
        tool = self.v_tool.get()
        if tool == "fill":
            self._image_click(e)
        elif tool == "paint":
            self._paint_click(e)
        elif tool == "draw":
            self._stroke = [self._clamp_px(*self._img_xy(e))]
        else:
            self._erase_at(e)

    def _image_motion(self, e):
        tool = self.v_tool.get()
        if self.art.img is None:
            return
        if tool == "draw" and self._stroke:
            x, y = self._clamp_px(*self._img_xy(e))
            lx, ly = self._stroke[-1]
            if math.hypot(x - lx, y - ly) >= 1.5:
                s, ox, oy = self._img_view
                col = self.params.pen_hex()[max(0, min(self.params.npen() - 1, self.v_paint.get()))]
                self.img_canvas.create_line(ox + lx * s, oy + ly * s, ox + x * s, oy + y * s, fill=col, width=2,
                                            capstyle="round", tags="live")
                self._stroke.append((x, y))
        elif tool == "erase":
            self._erase_at(e)

    def _image_release(self, _e):
        if self.v_tool.get() == "draw" and self._stroke is not None:
            pts, self._stroke = self._stroke, None
            if len(pts) >= 2:
                self.art.add_line(pts, self.v_paint.get())
                self.recompute()
            else:
                self.img_canvas.delete("live")

    def _erase_at(self, e):
        s = self._img_view[0]
        x, y = self._img_xy(e)
        if self.art.erase_near(x, y, max(3.0, 9.0 / s)):
            self.recompute()

    def _image_click(self, e):
        a = self.art
        if a.img is None or a.mask is None:
            return
        s, ox, oy = self._img_view
        x, y = int((e.x - ox) / s), int((e.y - oy) / s)
        r = a.region_at(x, y)
        if r < 0:
            return
        if r in a.selected:
            a.selected.discard(r)
            a.region_pen.pop(r, None)
            self.log(f"영역 {r} 채우기 해제")
        else:
            a.selected.add(r)
            self.log(f"영역 {r} 채우기")
        self.recompute()

    def _paint_click(self, e):
        """고른 펜 색으로 선 또는 영역을 칠함.
        직접 그린 선이나 자동 추출된 테두리(윤곽선)를 클릭하면 그 선 색만 바뀌고
        (안쪽은 그대로 — 영역 채우기처럼 안이 채워지지 않음),
        선이 없는 안쪽 자리를 클릭하면 영역을 칠함(같은 색으로 다시 누르면 영역 칠하기 취소)."""
        a, p = self.art, self.params
        if a.img is None:
            return
        s, ox, oy = self._img_view
        x, y = self._img_xy(e)
        pen = max(0, min(p.npen() - 1, self.v_paint.get()))
        radius = max(3.0, 9.0 / s)
        if a.manual and a.recolor_near(x, y, radius, pen):
            self.log(f"선 색을 {pen + 1}번 펜({p.pen_hex()[pen]})으로 바꿨어요")
            self.recompute()
            return
        r_outline = a.outline_region_near(x, y, radius)
        if r_outline >= 0:
            a.set_outline_pen(r_outline, pen)
            self.log(f"테두리 선 색을 {pen + 1}번 펜({p.pen_hex()[pen]})으로 바꿨어요 (안쪽은 그대로)")
            self.recompute()
            return
        if a.mask is None:
            return
        r = a.region_at(int(x), int(y))
        if r < 0:
            return
        if r in a.selected and a.region_pen.get(r) == pen:
            a.selected.discard(r)
            a.region_pen.pop(r, None)
            self.log(f"영역 {r} 칠하기 취소")
        else:
            a.assign_pen(r, pen)
            self.log(f"영역 {r} → {pen + 1}번 펜({p.pen_hex()[pen]}) 으로 칠해요")
        self.recompute()

    def undo_line(self):
        if self.art.manual:
            self.art.undo_line()
            self.log("직접 그린 선 되돌리기")
            self.recompute()

    def clear_lines(self):
        if self.art.manual:
            self.art.clear_lines()
            self.log("직접 그린 선을 모두 지웠어요")
            self.recompute()

    def new_blank(self):
        if self.art.img is not None and (self.art.manual or self.art.selected) and not messagebox.askyesno(
                "빈 캔버스", "지금 그림을 지우고 빈 캔버스에서 시작할까요?"):
            return
        self.art.blank()
        self.image_path = ""
        self.lbl_file.config(text="📂 현재 파일: 빈 캔버스 (직접 그리기)", fg="#0F172A")
        self.v_tool.set("draw")
        self._on_tool()
        self.log("빈 캔버스: 이미지 위에서 드래그해 선을 그리세요.")
        self.recompute(force=True)

    def fill_all(self):
        self.art.select_all()
        self.log(f"그림 영역 모두 채우기 ({len(self.art.selected)}개 영역)")
        self.recompute()

    def clear_fill(self):
        self.art.clear_selection()
        self.log("채우기 모두 지우기")
        self.recompute()

    def _on_flags(self):
        p = self.params
        p.outline, p.zigzag = self.v_outline.get(), self.v_zigzag.get()
        p.return_home = self.v_home_end.get()
        p.has_dimples, p.dimple_comp = self.v_has_dimples.get(), self.v_comp.get()
        p.pen_by_color, p.change_mode = self.v_bycolor.get(), self.v_chgmode.get()
        p.dimple_auto, p.outline_follow = self.v_dauto.get(), self.v_follow.get()
        self.schedule_recompute()

    def _on_kind(self, _e=None):
        p = self.params
        kind = self.v_kind.get()
        p.kind = kind
        p.length_mm, p.diameter_mm = {"탁구공": (40.0, 40.0), "골프공": (43.0, 43.0)}.get(kind, (57.0, 43.0))
        golf = kind == "골프공"
        p.has_dimples = p.dimple_comp = golf
        self.log(f"[물체] {kind}" + (" - 딤플 보정을 켰어요" if golf else ""))
        self.sync_controls()
        self.recompute(force=True)

    def sync_controls(self):
        self._syncing = True
        try:
            for lst in self.sliders.values():
                for sl in lst:
                    if sl.winfo_exists():
                        sl.pull()
            p = self.params
            self.v_kind.set(p.kind)
            self.v_outline.set(p.outline)
            self.v_home_end.set(p.return_home)
            self.v_zigzag.set(p.zigzag)
            self.v_has_dimples.set(p.has_dimples)
            self.v_comp.set(p.dimple_comp)
            self.v_mode.set(p.extract_mode)
            self.lbl_mode.config(text=self.MODE_HELP.get(p.extract_mode, ""))
            self.v_bycolor.set(p.pen_by_color)
            self.v_chgmode.set(p.change_mode)
            self.v_dauto.set(p.dimple_auto)
            self.v_follow.set(p.outline_follow)
            self._refresh_chips()
        finally:
            self._syncing = False
        # 열려 있는 도구 창(ATC/메커니즘 등)도 최신 설정값으로 새로고침해서
        # "값을 바꿔도 아래 코드가 그대로"인 문제를 막는다.
        for win in list(self._tools.values()):
            if win.winfo_exists():
                refresh = getattr(win, "refresh_from_params", None)
                if callable(refresh):
                    try:
                        refresh()
                    except Exception:
                        pass

    def open_preview(self):
        if self.preview is not None and self.preview.winfo_exists():
            self.preview.lift()
            self.preview.focus_set()
            return
        self.preview = Preview3D(self)
        self.log("3D 미리보기 창 열림")

    def origin_lines(self, persist=None):
        """원점으로 정하는 G코드. persist=True 이면 G54 에도 저장(GRBL 은 G10 L20 을 EEPROM 에 기록)."""
        if persist is None:
            persist = bool(self.origin.get("g54"))
        return ["G92.1", "G10 L20 P1 X0 Y0", "G54"] if persist else ["G92 X0 Y0"]

    def _mark_origin(self, g54=False, parked=None):
        """원점을 맞췄다는 기록을 앱(자동 저장)에 남긴다."""
        self._origin_set = True
        o = self.origin
        o.update(saved=True, when=time.strftime("%Y-%m-%d %H:%M:%S"), g54=bool(g54),
                 home_use=bool(self.params.home_use))
        o["parked"] = bool(parked) if parked is not None else False
        self._autosave()

    def set_origin(self, persist=None):
        lines = self.origin_lines(persist)
        for ln in lines:
            if not self.grbl.send(ln):
                return False
        self._mark_origin(g54=len(lines) > 1)
        return True

    def go_to_origin(self):
        """펜을 올린 뒤, 저장해 둔 원점(G54 또는 현재 G92 원점)으로 펜 팔과 달걀을 함께 이동한다."""
        if not self.grbl.connected:
            self.log("먼저 기계에 연결하세요.")
            return
        if self.grbl.job_active or self.grbl._paused:
            self.log("그리는 중(또는 일시정지)에는 원점 이동을 할 수 없어요.")
            return
        o = self.origin
        if not o.get("saved"):
            self.log("저장된 원점이 없어요. 먼저 '원점 자동 조정'에서 원점을 맞추세요.")
            return
        if not o.get("g54") and not self._origin_set:
            self.log("이 원점은 전원을 껐다 켜면 사라져요. '원점 자동 조정 > 저장된 원점 복원'을 먼저 하세요.")
            return
        self.manual(f"M3 S{int(self.params.pen_up)}")
        self.manual("G4 P0")
        if o.get("g54"):
            self.manual("G54")
        self.manual(f"G0 X0 Y0 F{int(self.params.travel_feed)}")
        self.manual("G4 P0")
        self.log("펜을 올리고 저장된 원점으로 이동합니다.")

    def set_origin_here(self):
        if self.set_origin():
            self.log("현재 위치를 원점(0,0)으로 설정했어요." + (" (G54 에도 저장)" if self.origin.get("g54") else ""))

    def origin_summary(self):
        o = self.origin
        if not o.get("saved"):
            return "저장된 원점 없음"
        how = []
        if o.get("g54"):
            how.append("G54 저장")
        if o.get("home_use"):
            how.append("홈 스위치")
        if o.get("parked"):
            how.append("파킹됨")
        return f"{o.get('when', '')} 원점 저장" + (f" ({', '.join(how)})" if how else "")

    def restore_origin(self):
        """저장된 원점을 기계에 다시 잡는다(원점 창의 복원 단계 실행)."""
        win = self.open_tool("origin")
        win.run_restore()
        return win

    def _offer_restore(self):
        if not self.grbl.connected or not self.origin.get("saved") or self._origin_set:
            return
        if messagebox.askyesno("원점 복원", f"저장된 원점이 있어요.\n{self.origin_summary()}\n\n"
                                          "지금 기계에 원점을 다시 잡을까요?\n(전원을 끄는 동안 손으로 움직였다면 위치가 틀릴 수 있어요)"):
            self.restore_origin()

    TOOL_CLASSES = {}

    def open_tool(self, name):
        """도구 창(보정/원점/자가 점검/펜 교환)을 연다. 이미 열려 있으면 앞으로 가져온다."""
        win = self._tools.get(name)
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            return win
        cls = {"origin": OriginDialog, "calib": CalibrationDialog, "check": SelfCheckDialog,
               "atc": AtcDialog, "gcode": GcodeEditor, "mech": MechDialog,
               "atcset": AtcSettingsDialog}[name]
        self._tools[name] = win = cls(self)
        return win

    def open_gcode_editor(self):
        """G코드 확인·수정 창 (3D 미리보기 창의 버튼도 이걸 부른다)."""
        return self.open_tool("gcode")

    def apply_edited_gcode(self, lines):
        """편집한 G코드를 읽어 3D 미리보기/저장/기계 전송에 반영. 반환: (strokes, warns)"""
        p = self.params
        strokes, warns = parse_gcode(list(lines), p, self.shape, self.axes)
        self.gcode = [ln.rstrip("\r\n") for ln in lines]
        self.ordered = list(strokes)
        self.timeline = Timeline(self.ordered, p, self.shape, plain=True) if strokes else None
        self.gcode_edited = True
        tl = self.timeline
        st = dict(self.stats or {})
        st.update(n_lines=len(self.gcode), n_strokes=len(strokes), n_outline=0, n_fill=0, n_draw=len(strokes),
                  n_points=sum(len(x[1]) for x in strokes), minutes=(tl.total / 60.0 if tl else 0.0),
                  n_changes=len(tl.changes) if tl else 0, n_pens=len({x[2] for x in strokes}))
        self.stats = st
        self.out_of_range = self._calc_out_of_range()
        self._update_stats()
        self._draw_unwrap()
        if self.preview is not None and self.preview.winfo_exists():
            self.preview.refresh()
        self.log(f"편집한 G코드를 적용했어요: {len(self.gcode)}줄, 선 {len(strokes)}개"
                 + (f", 경고 {len(warns)}개" if warns else ""))
        return strokes, warns

    def revert_gcode(self):
        """편집본을 버리고 설정/그림에서 자동 생성한 G코드로 되돌린다."""
        was = self.gcode_edited
        self.gcode_edited = False
        self.recompute()
        if was:
            self.log("편집한 G코드를 버리고 자동 생성 G코드로 되돌렸어요.")

    def _calc_out_of_range(self):
        p = self.params
        pts = [q for _k, pl, _pen in self.ordered for q in pl]
        half = self.shape.half
        return bool(pts) and (min(q[0] for q in pts) < 0 or max(q[0] for q in pts) > p.x_max_deg
                              or min(q[1] for q in pts) < -half or max(q[1] for q in pts) > half)

    def _notify_gcode_editor(self):
        ed = self._tools.get("gcode")
        if ed is not None and ed.winfo_exists():
            ed.on_app_changed()

    def tidy(self):
        self.recompute(force=True)
        self.log("그림 정리: 그리는 순서를 다시 정렬했어요.")

    # ============================================================ 재계산
    def schedule_recompute(self):
        if self._syncing:
            return
        if self._recompute_job:
            self.after_cancel(self._recompute_job)
        self._recompute_job = self.after(100, self.recompute)

    def recompute(self, force=False):
        if self._recompute_job:
            try:
                self.after_cancel(self._recompute_job)
            except Exception:
                pass
            self._recompute_job = None
        p = self.params
        self.shape = Shape(p.kind, p.length_mm, p.diameter_mm, p.pointiness)
        self.shape.enable_dimples(bool(p.has_dimples))
        self._update_geo_label()
        if self.gcode_edited:
            self.gcode_edited = False
            self.log("설정이 바뀌어서 편집한 G코드는 자동 생성 G코드로 대체됐어요.")
        if self.art.img is not None:
            if self.art.dirty or force:
                self.art.analyze(p)
                if self.art.note and self.art.note != self._last_note and not self.art.manual:
                    self.log("[선 추출] " + self.art.note)
                self._last_note = self.art.note
            self.art.build(p, self.shape)
            self.ordered = order_by_pen(self.art.strokes, p.npen(), self.art.pen_area,
                                        self.shape.circumference(p.pos_y_mm) / 360.0,
                                        by_area=getattr(p, "pen_order_area", False))
        else:
            self.ordered = []
        self.gcode, self.stats = build_gcode(self.ordered, p, self.shape, self.axes) if self.ordered else ([], {})
        self.timeline = Timeline(self.ordered, p, self.shape) if self.ordered else None
        self.out_of_range = self._calc_out_of_range()
        self._update_stats()
        self._refresh_chips()
        self._draw_image()
        self._draw_unwrap()
        if self.preview is not None and self.preview.winfo_exists():
            self.preview.refresh()
        self._notify_gcode_editor()

    def _update_geo_label(self):
        p = self.params
        circ = self.shape.circumference(0.0)
        self.lbl_geo.config(text=f"{p.kind}형 {p.length_mm:g} × {p.diameter_mm:g} mm\n"
                                 f"가운데에서 1° ≈ {circ / 360:.2f} mm, 한 바퀴 ≈ {circ:.0f} mm")

    def _update_stats(self):
        st = self.stats or {}
        warn = (f"\n⚠ 그림이 그릴 수 있는 범위(펜 팔 0 ~ {self.params.x_max_deg:.0f}°, 물체 높이)를 벗어났어요!"
                if self.out_of_range else "")
        extra = f" + 직접 {st['n_draw']}" if st.get("n_draw") else ""
        pens = (f"\n펜 {st.get('n_pens', 0)}개 사용 / 펜 교환 {st.get('n_changes', 0)}회"
                if self.params.npen() > 1 else "")
        if self.gcode_edited:
            pens += "\n✎ 편집한 G코드를 쓰는 중 (설정을 바꾸면 자동 생성으로 돌아가요)"
        self._update_order_label()
        self.lbl_stats.config(
            text=(f"선 {st.get('n_strokes', 0)}개 (테두리 {st.get('n_outline', 0)} + 채우기 {st.get('n_fill', 0)}{extra}) "
                  f"/ 점 {st.get('n_points', 0)}개{pens}\nG코드 {st.get('n_lines', 0)}줄\n"
                  f"예상 시간 약 {st.get('minutes', 0):.1f}분\n"
                  f"그림 크기 약 {st.get('w_mm', 0):.0f} × {st.get('h_mm', 0):.0f} mm ({self.params.kind})" + warn),
            fg="#B91C1C" if self.out_of_range else "#334155")

    def _update_order_label(self):
        """펜별 칠할 면적 비율과 그리는 순서(큰 색부터, 한 색을 다 칠한 뒤 다음 색)."""
        p, a = self.params, self.art
        seq = list(dict.fromkeys(s[2] for s in self.ordered))
        if not seq:
            self.v_order.set("")
            return
        tot = sum(a.pen_area.get(k, 0.0) for k in seq) or 1.0
        if len(seq) == 1:
            self.v_order.set(f"그리는 펜: {seq[0] + 1}번 ({p.pen_hex()[seq[0]]}) - 펜 교환 없음")
            return
        parts = [f"{k + 1}번 {100.0 * a.pen_area.get(k, 0.0) / tot:.0f}%" for k in seq]
        self.v_order.set("그리는 순서(큰 색부터, 한 색 다 칠하고 다음 색): " + " → ".join(parts)
                         + f"   ※ 에그봇에 먼저 달 펜: {seq[0] + 1}번")

    # ============================================================ 이미지 그리기
    def _draw_image(self):
        c = self.img_canvas
        c.delete("all")
        a = self.art
        if a.img is None:
            c.create_text(c.winfo_width() / 2, c.winfo_height() / 2,
                          text="'1. 이미지 열기'로 그림을 불러오거나\n'빈 캔버스'로 직접 그려 보세요",
                          fill="#94A3B8", font=self.font_b, justify="center")
            return
        base = Image.blend(a.img, Image.new("RGB", a.img.size, "white"), 0.5)
        if a.selected and a.labels:
            rgbs = self.params.pen_rgb()
            by_pen = {}
            for r in a.selected:
                if r < len(a.sizes):
                    by_pen.setdefault(a.pen_of_region(r, self.params), []).append(r)
            multi = self.params.npen() > 1 or bool(a.region_pen)
            for pen, regs in by_pen.items():
                flags = [0] * (len(a.sizes) + 1)
                for r in regs:
                    flags[r] = 255
                sel = Image.frombytes("L", a.img.size, bytes(flags[l] for l in a.labels))
                col = rgbs[pen % 6] if multi else (147, 187, 250)
                tint = Image.blend(base, Image.new("RGB", base.size, col), 0.6)
                base = Image.composite(tint, base, sel)
        cw, ch = max(c.winfo_width() - 4, 10), max(c.winfo_height() - 4, 10)
        s = min(cw / base.width, ch / base.height)
        disp = base.resize((max(1, int(base.width * s)), max(1, int(base.height * s))))
        self._tkimg = ImageTk.PhotoImage(disp)
        ox, oy = (cw - disp.width) / 2 + 2, (ch - disp.height) / 2 + 2
        self._img_view = (s, ox, oy)
        c.create_image(ox, oy, anchor="nw", image=self._tkimg)
        hexes = self.params.pen_hex()
        for kind, pl, pen in a.px_strokes:
            flat = [v for x, y in pl for v in (ox + x * s, oy + y * s)]
            if len(flat) >= 4:
                c.create_line(flat, fill=hexes[pen % 6], width=2 if kind == "draw" else 1,
                              capstyle="round", joinstyle="round")

    # ======================================================= 펼친 모습 (편집 핸들)
    def _uw_geometry(self):
        c = self.uw_canvas
        W, H = max(c.winfo_width(), 80), max(c.winfo_height(), 80)
        mx, my = 34, 22
        pw, ph = W - mx - 12, H - my - 22
        half = self.shape.half
        # 가로: 0~360°, 세로: 물체 위(+half) ~ 아래(-half)
        # 가로(도)와 세로(mm)를 같은 mm 배율로 그려서 그림 비율이 찌그러져 보이지 않게 한다.
        try:
            circ = float(self.shape.circumference(self.params.pos_y_mm))
        except Exception:
            circ = 0.0
        # 확대: 가로로 보이는 범위(span)를 줄여서 그림이 화면에 더 크게 보이게 한다.
        try:
            zoom = max(1.0, float(self.v_zoom.get()))
        except Exception:
            zoom = 1.0
        span = 360.0 / zoom
        if self.v_auto.get() and circ > 1e-6 and half > 1e-6:
            # 자동 맞춤: 세로를 화면 높이에 꽉 채우고, 그 배율로 가로에 들어가는 만큼만 보여 준다.
            k_auto = ph / (2.0 * half)
            span = max(30.0, min(360.0, pw / (k_auto * circ) * 360.0))
        v0 = 0.0 if zoom <= 1.0 else max(0.0, min(360.0 - span, self.params.pos_x_deg - span / 2.0))
        if circ > 1e-6 and half > 1e-6 and not self.v_fill.get():
            k = min(pw / (circ * span / 360.0), ph / (2.0 * half))   # px per mm (가로·세로 공통)
            pw2, ph2 = circ * span / 360.0 * k, 2.0 * half * k
            mx += (pw - pw2) / 2.0
            my += (ph - ph2) / 2.0
            pw, ph = pw2, ph2
        fx = lambda deg: mx + (deg - v0) / span * pw
        fy = lambda h: my + (half - h) / (2 * half) * ph
        inv = lambda px, py: (v0 + (px - mx) / pw * span, half - (py - my) / ph * 2 * half)
        return dict(W=W, H=H, mx=mx, my=my, pw=pw, ph=ph, fx=fx, fy=fy, inv=inv, half=half,
                    span=span, v0=v0)

    def _uw_box(self):
        """그림 사각형의 네 모서리(펼친 좌표 deg,h)."""
        a, p, sh = self.art, self.params, self.shape
        if a.img is None:
            return None
        pts = [a.to_surface(x, y, p, sh) for x, y in ((0, 0), (a.w, 0), (a.w, a.h), (0, a.h))]
        return pts

    def _draw_unwrap(self):
        c = self.uw_canvas
        c.delete("all")
        g = self._uw_geometry()
        self._uw = g
        fx, fy, half, p = g["fx"], g["fy"], g["half"], self.params
        # 격자
        for d in range(0, 361, 45 if g["span"] > 200 else 15):
            if d < g["v0"] - 1e-6 or d > g["v0"] + g["span"] + 1e-6:
                continue
            c.create_line(fx(d), g["my"], fx(d), g["my"] + g["ph"], fill="#E2E8F0")
            if d < 360:
                c.create_text(fx(d), g["my"] + g["ph"] + 10, text=f"{d}°", fill="#94A3B8", font=self.font_s)
        for h in range(-int(half // 10) * 10, int(half) + 1, 10):
            c.create_line(g["mx"], fy(h), g["mx"] + g["pw"], fy(h), fill="#EEF2F6")
            c.create_text(g["mx"] - 4, fy(h), text=f"{h}", fill="#B0BAC8", font=self.font_s, anchor="e")
        # 펜 팔 범위 밖 표시
        if p.x_max_deg < 360:
            if p.x_max_deg < g["v0"] + g["span"]:
                c.create_rectangle(max(fx(p.x_max_deg), g["mx"]), g["my"], g["mx"] + g["pw"], g["my"] + g["ph"],
                                   fill="#FEE2E2", outline="", stipple="gray25")
        if g["v0"] <= 0:
            c.create_line(fx(0), g["my"], fx(0), g["my"] + g["ph"], fill="#EF4444", width=2)
        if g["v0"] <= p.x_max_deg <= g["v0"] + g["span"]:
            c.create_line(fx(p.x_max_deg), g["my"], fx(p.x_max_deg), g["my"] + g["ph"], fill="#EF4444", dash=(4, 3))
        c.create_line(g["mx"], g["my"], g["mx"] + g["pw"], g["my"], fill="#EF4444", dash=(4, 3))
        c.create_line(g["mx"], g["my"] + g["ph"], g["mx"] + g["pw"], g["my"] + g["ph"], fill="#EF4444", dash=(4, 3))
        c.create_text(g["mx"] + 4, g["my"] - 10, text="가로·세로 같은 mm 배율로 표시 (실제 모양은 3D 창에서 확인)",
                      fill="#A8B3C2", font=self.font_s, anchor="w")
        if self.out_of_range:
            c.create_text(g["mx"] + g["pw"] / 2, g["my"] + 14, text="⚠ 그림이 펜 팔 범위를 벗어났어요",
                          fill="#DC2626", font=self.font_b)
        # 그림
        prev = None
        hexes = p.pen_hex()
        for kind, pl, pen in self.ordered:
            if len(pl) < 2:
                continue
            flat = [v for d_, h_ in pl for v in (fx(d_), fy(h_))]
            c.create_line(flat, fill=hexes[pen % 6], width=2 if kind == "draw" else 1)
            if self.v_travel.get() and prev is not None:
                c.create_line(prev[0], prev[1], flat[0], flat[1], fill="#F4A3A3", dash=(2, 3))
            prev = (flat[-2], flat[-1])
        # 조절 상자
        box = self._uw_box()
        if box:
            poly = [v for d_, h_ in box for v in (fx(d_), fy(h_))]
            c.create_polygon(poly, outline="#2563EB", fill="", dash=(4, 3), width=1)
            hs = 5
            for d_, h_ in box:
                x, y = fx(d_), fy(h_)
                c.create_rectangle(x - hs, y - hs, x + hs, y + hs, fill="white", outline="#2563EB")
            mids = [((box[i][0] + box[(i + 1) % 4][0]) / 2, (box[i][1] + box[(i + 1) % 4][1]) / 2) for i in range(4)]
            for i_, (d_, h_) in enumerate(mids):
                x, y = fx(d_), fy(h_)
                col = "#EF4444" if i_ in (0, 2) else "#06B6D4"     # 위·아래 = 세로만(빨강), 좌·우 = 가로만(청록)
                c.create_oval(x - 4, y - 4, x + 4, y + 4, fill=col, outline="white")
            rx, ry = self._uw_rot_handle(box)
            c.create_line(fx(mids[0][0]), fy(mids[0][1]), rx, ry, fill="#2563EB")
            c.create_oval(rx - 6, ry - 6, rx + 6, ry + 6, fill="white", outline="#2563EB", width=2)
            cx_, cy_ = fx(p.pos_x_deg), fy(p.pos_y_mm)
            c.create_line(cx_ - 5, cy_, cx_ + 5, cy_, fill="#2563EB")
            c.create_line(cx_, cy_ - 5, cx_, cy_ + 5, fill="#2563EB")
            wmm, hmm = self.art.drawing_size_mm(p)
            wdeg = wmm * 360.0 / self.shape.circumference(p.pos_y_mm)
            c.create_text(cx_ + 8, cy_ + 12, anchor="w", fill="#2563EB", font=self.font_s,
                          text=f"({p.pos_x_deg:.0f}°, {p.pos_y_mm:.1f}mm): {wdeg:.0f}°×{hmm:.0f}mm")

    def _uw_rot_handle(self, box):
        g = self._uw
        mid = ((box[0][0] + box[1][0]) / 2, (box[0][1] + box[1][1]) / 2)
        cen = (self.params.pos_x_deg, self.params.pos_y_mm)
        # 윗변 중점에서 바깥(중심 반대쪽)으로 22px
        mx_, my_ = g["fx"](mid[0]), g["fy"](mid[1])
        cx_, cy_ = g["fx"](cen[0]), g["fy"](cen[1])
        dx, dy = mx_ - cx_, my_ - cy_
        ln = math.hypot(dx, dy) or 1.0
        return mx_ + dx / ln * 22, my_ + dy / ln * 22

    def _uw_hit(self, x, y):
        box = self._uw_box()
        if not box or not self._uw:
            return None
        g = self._uw
        rx, ry = self._uw_rot_handle(box)
        if math.hypot(x - rx, y - ry) <= 9:
            return "rotate"
        for d_, h_ in box:
            if abs(x - g["fx"](d_)) <= 8 and abs(y - g["fy"](h_)) <= 8:
                return "scale"
        for i_ in range(4):
            md, mh = (box[i_][0] + box[(i_ + 1) % 4][0]) / 2, (box[i_][1] + box[(i_ + 1) % 4][1]) / 2
            if abs(x - g["fx"](md)) <= 8 and abs(y - g["fy"](mh)) <= 8:
                return "scale_h" if i_ in (0, 2) else "scale_w"
        poly = [(g["fx"](d_), g["fy"](h_)) for d_, h_ in box]
        inside = False
        j = len(poly) - 1
        for i in range(len(poly)):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi:
                inside = not inside
            j = i
        return "move" if inside else None

    def _uw_hover(self, e):
        hit = self._uw_hit(e.x, e.y)
        self.uw_canvas.config(cursor={"rotate": "exchange", "scale": "sizing", "scale_h": "sb_v_double_arrow", "scale_w": "sb_h_double_arrow", "move": "fleur"}.get(hit, "arrow"))

    def _uw_mm(self, x, y):
        """화면 좌표 → 중심 기준 mm 벡터 (오른쪽 +, 위 +)."""
        d, h = self._uw["inv"](x, y)
        p = self.params
        return ((d - p.pos_x_deg) * self.shape.circumference(p.pos_y_mm) / 360.0, h - p.pos_y_mm)

    def _uw_press(self, e):
        hit = self._uw_hit(e.x, e.y)
        if not hit:
            return
        p = self.params
        vx, vy = self._uw_mm(e.x, e.y)
        a_ = math.radians(p.rot_deg)
        ca, sa = math.cos(a_), math.sin(a_)
        px0 = vx * ca - vy * sa              # 그림 가로 방향 성분
        py0 = -vx * sa - vy * ca             # 그림 세로 방향 성분
        self._uw_drag = dict(mode=hit, x=e.x, y=e.y, h0=p.height_mm, rot0=p.rot_deg, sx0=p.stretch_x,
                             r0=math.hypot(vx, vy) or 1.0, a0=math.atan2(-vy, vx),
                             px0=px0 or 1e-6, py0=py0 or 1e-6)

    def _uw_motion(self, e):
        d = self._uw_drag
        if not d:
            return
        p, g = self.params, self._uw
        if d["mode"] == "move":
            dd = (e.x - d["x"]) / g["pw"] * g["span"]
            dh = -(e.y - d["y"]) / g["ph"] * 2 * g["half"]
            p.pos_x_deg = (p.pos_x_deg + dd) % 360.0
            p.pos_y_mm = max(-g["half"], min(g["half"], p.pos_y_mm + dh))
            d["x"], d["y"] = e.x, e.y
        elif d["mode"] == "scale":
            vx, vy = self._uw_mm(e.x, e.y)
            p.height_mm = max(5.0, min(80.0, round(d["h0"] * (math.hypot(vx, vy) / d["r0"]), 1)))
        elif d["mode"] in ("scale_h", "scale_w"):
            vx, vy = self._uw_mm(e.x, e.y)
            a_ = math.radians(p.rot_deg)
            ca, sa = math.cos(a_), math.sin(a_)
            if d["mode"] == "scale_h":        # 세로만: 가로 mm 는 그대로 둔다
                ratio = abs((-vx * sa - vy * ca) / d["py0"])
                nh = max(5.0, min(80.0, round(d["h0"] * ratio, 1)))
                p.height_mm = nh
                p.stretch_x = max(0.2, min(4.0, round(d["sx0"] * d["h0"] / nh, 3)))
            else:                             # 가로만
                ratio = abs((vx * ca - vy * sa) / d["px0"])
                p.stretch_x = max(0.2, min(4.0, round(d["sx0"] * ratio, 3)))
        elif d["mode"] == "rotate":
            vx, vy = self._uw_mm(e.x, e.y)
            ang = math.degrees(math.atan2(-vy, vx) - d["a0"])
            r = (d["rot0"] + ang + 180.0) % 360.0 - 180.0
            if not (e.state & 0x0004):                 # Ctrl 안 누르면 5° 단위
                r = round(r / 5.0) * 5.0
            p.rot_deg = r
        self.sync_controls()
        self._draw_unwrap()
        self.schedule_recompute()

    def _uw_wheel(self, e):
        self._uw_zoom(1 if e.delta > 0 else -1, e.state)

    def _uw_zoom(self, direction, state):
        if self.art.img is None:
            return
        p = self.params
        if state & 0x0001:                              # Shift+휠: 회전
            p.rot_deg = (p.rot_deg + 5 * direction + 180.0) % 360.0 - 180.0
        else:
            p.height_mm = max(5.0, min(80.0, round(p.height_mm * (1.06 if direction > 0 else 1 / 1.06), 1)))
        self.sync_controls()
        self._draw_unwrap()
        self.schedule_recompute()

    # ============================================================ 기계
    def refresh_ports(self, quiet=False):
        ports = self.grbl.list_ports()
        self.port_cb["values"] = ports
        if ports and not self.v_port.get():
            self.v_port.set(ports[0])
        if not quiet:
            self.log(f"포트 {len(ports)}개 발견" + (": " + ", ".join(ports) if ports else ""))
        if not HAS_SERIAL and not quiet:
            self.log("pyserial 이 없어요.  python -m pip install pyserial")

    def connect(self):
        if not self.v_port.get():
            messagebox.showwarning("포트 없음", "포트를 고르고 '새로고침'을 눌러보세요.")
            return
        if self.grbl.connect(self.v_port.get()) is not False:
            self.after(2500, self._offer_restore)

    def disconnect(self):
        self.grbl.disconnect()

    def manual(self, line):
        # 기계가 알람 상태면 알아서 잠금 해제한 뒤 명령을 보낸다 (직접 명령/조그/펜 등 전부 이 길을 지난다)
        if self.grbl.connected and (self.grbl.in_alarm() or self.grbl.alarm):
            self.log("기계가 알람 상태 → 잠금 해제 후 명령을 보냅니다...")
            self._queue_prepare(lambda ok: self.grbl.send(line) if ok else None)
            return
        self.grbl.send(line)

    def _queue_prepare(self, cb):
        """백그라운드 자동 준비(알람 해제 / $20 확인)가 끝나면 불릴 콜백을 등록한다."""
        if not hasattr(self, "_pending_prepare"):
            self._pending_prepare = {}
        tag = "p%d" % int(time.time() * 1000)
        self._pending_prepare[tag] = cb
        self.grbl.prepare(tag)

    def send_raw(self, line: str) -> bool:
        return self.sendline(line)

    def send_entry(self):
        line = self.entry.get().strip()
        if line:
            self.manual(line)
            self.entry.delete(0, "end")

    def jog(self, rot_axis, sign):
        try:
            step = abs(float(self.v_jog.get()))
        except ValueError:
            return
        step = max(0.01, min(step, 10.0))

        x_is_rot = bool(self.axes.x_is_rot)
        if rot_axis:
            axis = "X" if x_is_rot else "Y"
        else:
            axis = "Y" if x_is_rot else "X"

        # 회전축(달걀 롤러)은 느리게(F300), 펜 팔 이동축은 F1500
        is_rotary = (axis == "X" and x_is_rot) or (axis == "Y" and not x_is_rot)
        feed = 300 if is_rotary else 1500

        # GRBL 1.1 전용 jog 명령
        self.grbl.send_raw(f"$J={axis}{sign * step:.3f} F{feed}")

    def start_plot(self):
        if not self.gcode:
            messagebox.showwarning("G코드 없음", "먼저 이미지를 열어 그림을 만드세요.")
            return
        if not self.grbl.connected:
            self.log("먼저 기계에 연결하세요.")
            return
        if not self.gcode_edited:
            self.recompute(force=True)
        if self.out_of_range:
            messagebox.showwarning("범위 초과", f"그림이 펜 팔 각도 범위(0 ~ {self.params.x_max_deg:.0f}°)나 물체 높이를 "
                                              "벗어났어요.\n크기/위치를 조절해 주세요.")
            return
        a, p = self.axes, self.params
        # 이 그림이 몇 도를 돌며 그려지는지 G코드에서 계산해 알려준다.
        ax = "X" if a.x_is_rot else "Y"
        vals = [float(m.group(1)) for ln in self.gcode if not ln.startswith(";")
                for m in re.finditer(ax + r"(-?[\d.]+)", ln)]
        if vals:
            lo, hi = min(vals), max(vals)
            self._rot_span = f"그림 회전 범위: {lo:.0f}°~{hi:.0f}° (약 {hi - lo:.0f}° 회전)"
            rot_txt = self._rot_span + "\n"
        else:
            self._rot_span, rot_txt = "", ""
        ok = messagebox.askokcancel(
            "그리기 시작", f"{len(self.gcode)}줄 / 약 {self.stats.get('minutes', 0):.1f}분"
                        + (" (편집한 G코드)" if self.gcode_edited else "") + "\n\n"
                        + rot_txt
                        + f"축: {'X=회전각, Y=높이' if a.x_is_rot else 'Y=회전각, X=높이'}"
                        f"  (X반전 {'O' if a.x_invert else 'X'}, Y반전 {'O' if a.y_invert else 'X'})\n"
                        f"서보: 올림 S{p.pen_up} / 내림 S{p.pen_down}\n\n"
                        "달걀을 끼우고 펜 위치(원점)를 맞췄나요?")
        if not ok:
            return
        # 진행도 창에 쓸 경로를 미리 뽑아 둔다 (실제로 기계에 보낼 줄과 같은 필터링 기준)
        self._plot_lines = to_servo_lines([l for l in self.gcode if l and not l.startswith(";")])
        self._plot_path, self._plot_path_idx = self._build_plot_path(self._plot_lines, with_index=True)
        self._plot_cum = line_cum_times(self._plot_lines, self.params.travel_feed)
        # 그리기 직전 자동 준비: 알람이면 리셋+$X 로 풀고, 소프트 리밋($20)이 켜져 있으면 끈다.
        # 둘 다 문제없으면 바로 시작한다.
        need = (not self.grbl.settings) or self.grbl.settings.get(20) == 1 \
               or self.grbl.in_alarm() or self.grbl.alarm
        if not need:
            self._start_prepared(True)
        else:
            self.log("그리기 전 자동 준비: 알람 해제 / 소프트 리밋($20) 확인...")
            self._queue_prepare(self._start_prepared)

    def _start_prepared(self, ok):
        if not ok:
            self.log("자동 준비에 실패했어요. 연결 상태를 확인하세요.")
            return
        if not self.grbl.start(self.gcode):
            self.log("그리기를 시작할 수 없어요. (이미 진행 중이거나 명령 처리 중)")
            return
        if getattr(self, "_rot_span", ""):
            self.log(self._rot_span + " — 이 정도만 돌아야 정상이에요. "
                    "그보다 많이 돌면 기계 $100(스텝/도)이 안 맞는 거예요. '회전 보정'으로 맞추세요.")
        # 진행도 미니 창 열기
        if self.progress_win is None or not self.progress_win.winfo_exists():
            self.progress_win = ProgressPopup(self)
        self.progress_win.set_path(getattr(self, "_plot_path", []), len(getattr(self, "_plot_lines", self.gcode)),
                                   getattr(self, "_plot_path_idx", None))
        # 3D 영상도 기계 진행과 실시간으로 연동 (창이 없으면 자동으로 연다)
        try:   # 3D 창을 일부러 띄우지는 않는다. 이미 열려 있을 때만 같이 연동한다.
            if self.preview is not None and self.preview.winfo_exists():
                self.preview.live_begin(getattr(self, "_plot_cum", []))
        except Exception:
            pass

    def _build_plot_path(self, lines, with_index=False):
        """G코드 줄들에서 (x, y, 펜내림여부) 경로를 뿑아 진행도 미니 창 미리보기용으로 쓴다."""
        path = []
        x = y = 0.0
        down = False
        pd, pu = self.params.pen_down, self.params.pen_up
        idxs = []
        for li, ln in enumerate(lines):
            ms = re.search(r"M0?3\s*S(-?[\d.]+)", ln)
            m96 = re.match(r"\s*M96\s*[A-D](-?[\d.]+)", ln, re.I)
            if ms:
                s = float(ms.group(1))
                down = abs(s - pd) < abs(s - pu)
            elif m96:
                a = float(m96.group(1))
                if SERVO_INVERT:
                    a = 499.0 - a
                s = a * 180.0 / 499.0          # to_servo() 의 역변환 -> 서보 각도(M3 S 값)
                down = abs(s - pd) < abs(s - pu)
            mx = re.search(r"X(-?[\d.]+)", ln)
            my = re.search(r"Y(-?[\d.]+)", ln)
            if mx:
                x = float(mx.group(1))
            if my:
                y = float(my.group(1))
            if mx or my:
                path.append((x, y, down))
                idxs.append(li)
        return (path, idxs) if with_index else path

    def estop(self):
        self.grbl.estop()

    def reset_job(self):
        """'초기화' 버튼: 리셋 없이 지금 보내는 중인 작업 큐만 깨끗이 비우고, 진행도 창도 0으로 되돌린다."""
        if not self.grbl.job_active and not self.grbl.paused:
            self.log("초기화할 진행 중인 작업이 없어요.")
            return
        self.grbl.cancel_job()
        self.pbar.config(value=0)
        self.btn_pause.config(text="⏸ 일시정지")
        if self.progress_win is not None and self.progress_win.winfo_exists():
            self.progress_win.reset_view()
        self.log("작업을 초기화했어요. (기계 자체는 리셋하지 않았어요 — 완전히 멈추려면 '비상 정지')")

    def pause_resume(self):
        if self.grbl.paused:
            self.grbl.resume()
            self.btn_pause.config(text="⏸ 일시정지")
            if self.progress_win is not None and self.progress_win.winfo_exists():
                self.progress_win.btn_pause.config(text="⏸ 일시정지")
        else:
            self.grbl.pause()
            if self.grbl.paused:
                self.btn_pause.config(text="▶ 재개")
                if self.progress_win is not None and self.progress_win.winfo_exists():
                    self.progress_win.btn_pause.config(text="▶ 재개")

    def open_error_log(self):
        """오류/알람이 언제, 몇 번째 줄에서, 어떤 G코드 명령에 대해, 무슨 뜻으로 났는지 보여주는 창."""
        win = tk.Toplevel(self)
        win.title("오류 기록")
        win.geometry("520x360")
        win.configure(bg=C_PANEL)
        tk.Label(win, text="기계가 보낸 오류 / 알람 기록", bg=C_PANEL, fg=C_TEXT, font=self.font_b).pack(
            anchor="w", padx=12, pady=(10, 4))
        frame = tk.Frame(win, bg=C_PANEL)
        frame.pack(fill="both", expand=True, padx=12, pady=4)
        cols = ("time", "line_no", "code", "gcode", "desc")
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=12)
        heads = {"time": ("시각", 70), "line_no": ("줄 번호", 60), "code": ("코드", 70),
                 "gcode": ("G코드 내용", 140), "desc": ("설명", 160)}
        for c in cols:
            tree.heading(c, text=heads[c][0])
            tree.column(c, width=heads[c][1], anchor="w")
        vs = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vs.set)
        tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        if not self.grbl.error_log:
            tree.insert("", "end", values=("-", "-", "-", "-", "아직 발생한 오류가 없어요"))
        for d in self.grbl.error_log:
            tree.insert("", "end", values=(d.get("t", ""), d.get("idx") or "-", d.get("code", ""),
                                            d.get("line", ""), d.get("desc", "")))
        btns = tk.Frame(win, bg=C_PANEL)
        btns.pack(fill="x", padx=12, pady=(4, 10))

        def _clear():
            self.grbl.error_log.clear()
            self.grbl.last_error = None
            win.destroy()
            self.open_error_log()

        self._btn(btns, "기록 지우기", _clear).pack(side="left")
        self._btn(btns, "닫기", win.destroy).pack(side="right")

    def _poll_machine(self):
        try:
            while True:
                ev = self.grbl.events.get_nowait()
                if ev[0] == "log":
                    self.log(ev[1])
                elif ev[0] == "state":
                    self.v_state.set(ev[1])
                elif ev[0] == "progress":
                    self.pbar.config(maximum=max(1, ev[2]), value=ev[1])
                    try:
                        if self.preview is not None and self.preview.winfo_exists():
                            self.preview.live_progress(ev[1], ev[2])
                    except Exception:
                        pass
                    if self.progress_win is not None and self.progress_win.winfo_exists():
                        self.progress_win.update_progress(ev[1], ev[2])
                        if len(ev) > 3 and ev[3]:
                            self.progress_win.show_line(ev[1], ev[3])
                elif ev[0] == "done":
                    self.log({"ok": "그리기 완료", "error": "그리기 실패: 기계 오류/알람. 로그를 확인하세요.",
                              "abort": "그리기 중단됨"}.get(ev[1], ev[1]))
                    self.btn_pause.config(text="⏸ 일시정지")
                    try:
                        if self.preview is not None and self.preview.winfo_exists():
                            self.preview.live_end(ev[1])
                    except Exception:
                        pass
                    if self.progress_win is not None and self.progress_win.winfo_exists():
                        self.progress_win.mark_done(ev[1])
                elif ev[0] == "error_detail":
                    pass   # 이미 'log' 이벤트로 알림/설명을 보여줬고, '오류 기록' 창에서 전체 내역을 볼 수 있다
                elif ev[0] == "prepared":
                    cb = getattr(self, "_pending_prepare", {}).pop(ev[1], None)
                    if cb:
                        cb(ev[2])
        except Exception:
            pass
        self.after(60, self._poll_machine)

    def axis_dialog(self):
        win = tk.Toplevel(self)
        win.title("축 설정")
        win.configure(bg=C_PANEL)
        win.transient(self)
        win.grab_set()
        a = self.axes
        v1, v2, v3 = tk.BooleanVar(value=a.x_is_rot), tk.BooleanVar(value=a.x_invert), tk.BooleanVar(value=a.y_invert)
        tk.Label(win, text="기계 축 연결 (G코드 출력 직전에 적용)", bg=C_PANEL, font=self.font_b).pack(padx=16, pady=(14, 6))
        for txt, v in (("X축 = 회전각(도)   (해제 시 Y축이 회전각)", v1), ("X축 방향 반전", v2), ("Y축 방향 반전", v3)):
            tk.Checkbutton(win, text=txt, variable=v, bg=C_PANEL, font=self.font, anchor="w").pack(fill="x", padx=16)

        def save():
            a.x_is_rot, a.x_invert, a.y_invert = v1.get(), v2.get(), v3.get()
            self.recompute()
            self.log("축 설정 저장")
            win.destroy()

        self._btn(win, "저장", save, bg="#10B981", fg="white", bold=True).pack(pady=14)

    # GRBL 설정 창에 보여주는 항목: (설정번호, 이름, 도움말)
    GRBL_ROWS = (
        (20, "소프트 리밋", "0=끔 / 1=켬 — 이 앱은 0 권장 (ALARM:2 방지)"),
        (21, "하드 리밋(스위치)", "0=끔 / 1=켬 — 리밋 스위치 없으면 0"),
        (22, "홈잉 사용", "0=끔 / 1=켬 — 리밋 스위치 없으면 0"),
        (24, "홈잉 반복 횟수", "보통 1~3"),
        (30, "최대 S(서보)", "255 권장"),
        (100, "X 스텝 수", "X=회전각(도) → 스텝/도 (직결 16μ스텝 ≈ 8.889)"),
        (101, "Y 스텝 수", "Y=높이(mm) → 스텝/mm"),
        (110, "X 최대 속도", "도/분 — 그리기 F보다 크게 (예: 3000)"),
        (111, "Y 최대 속도", "mm/분 (예: 3000)"),
        (120, "X 가속도", "예: 200 — 낮으면 부드럽고 느림"),
        (121, "Y 가속도", "예: 200"),
        (130, "X 최대 이동거리", "도 — 소프트 리밋 켤 때만 의미"),
        (131, "Y 최대 이동거리", "mm — 소프트 리밋 켤 때만 의미"),
    )

    def grbl_settings_dialog(self):
        """기계 GRBL 설정($$)을 읽어와서 앱 안에서 바로 고칠 수 있는 창."""
        if not self.grbl.connected:
            self.log("먼저 기계에 연결하세요.")
            return
        win = tk.Toplevel(self)
        win.title("GRBL 설정")
        win.configure(bg=C_PANEL)
        win.transient(self)
        win.grab_set()
        tk.Label(win, text="기계 설정($$). 값을 고치고 [변경 저장]을 누르면 기계에 저장돼요.",
                 bg=C_PANEL, fg=C_TEXT, font=self.font_b).pack(padx=14, pady=(12, 4), anchor="w")

        body = tk.Frame(win, bg=C_PANEL)
        body.pack(padx=14, pady=4)
        entries, loaded = {}, {}
        for r, (num, name, hint) in enumerate(self.GRBL_ROWS):
            tk.Label(body, text=f"${num}", bg=C_PANEL, fg=C_SUB, font=self.font_s,
                     width=5, anchor="w").grid(row=r, column=0, sticky="w")
            tk.Label(body, text=name, bg=C_PANEL, fg=C_TEXT, font=self.font,
                     width=15, anchor="w").grid(row=r, column=1, sticky="w")
            v = tk.StringVar(value="…")
            tk.Entry(body, textvariable=v, width=10, font=self.font).grid(row=r, column=2, padx=6)
            tk.Label(body, text=hint, bg=C_PANEL, fg=C_SUB, font=self.font_s,
                     anchor="w").grid(row=r, column=3, sticky="w")
            entries[num] = v

        def fill():
            S = self.grbl.settings
            for num, v in entries.items():
                if num in S:
                    loaded[num] = S[num]
                    v.set(f"{S[num]:g}")

        def poll(n=0):
            if not win.winfo_exists():
                return
            if len(self.grbl.settings) >= 10:
                fill()
                return
            if n < 25:
                win.after(240, lambda: poll(n + 1))
            else:
                self.log("GRBL 설정을 못 읽었어요. [다시 읽기]를 눌러보세요.")

        def reread():
            for v in entries.values():
                v.set("…")
            loaded.clear()
            self.grbl.settings.clear()
            if self.grbl.send("$$"):
                self.log("GRBL 설정($$)을 읽는 중...")
                poll()

        def apply():
            changed = []
            for num, v in entries.items():
                t = v.get().strip()
                if not t or t == "…":
                    continue
                try:
                    val = float(t.replace(",", "."))
                except ValueError:
                    messagebox.showwarning("입력 오류", f"${num} 값이 숫자가 아니에요: {t}", parent=win)
                    return
                if num in loaded and abs(val - loaded[num]) < 1e-9:
                    continue                      # 안 바꾼 항목은 안 보낸다
                changed.append((num, val))
            if not changed:
                self.log("바뀐 GRBL 설정이 없어요.")
                return
            for num, val in changed:
                self.grbl.send(f"${num}={val:g}")
                self.log(f"GRBL ${num}={val:g} 기계에 저장했어요.")
            win.after(700, reread)                # 저장 확인용으로 다시 읽기

        # X(회전) 스텝 수 프리셋: 마이크로스텝과 감속비만 고르면 계산해 넣는다
        pre = tk.Frame(win, bg=C_PANEL)
        pre.pack(fill="x", padx=14, pady=(4, 0))
        tk.Label(pre, text=("X" if self.axes.x_is_rot else "Y") + "(회전) 스텝 프리셋 — 마이크로스텝:", bg=C_PANEL, fg=C_TEXT,
                 font=self.font).pack(side="left")
        v_ms = tk.StringVar(value="16")
        ttk.Combobox(pre, textvariable=v_ms, values=["1", "2", "4", "8", "16", "32"],
                     width=4, state="readonly").pack(side="left", padx=4)
        tk.Label(pre, text="감속비:", bg=C_PANEL, fg=C_TEXT, font=self.font).pack(side="left", padx=(8, 2))
        v_gr = tk.StringVar(value="1")
        tk.Entry(pre, textvariable=v_gr, width=6).pack(side="left")
        tk.Label(pre, text="(직결이면 1)", bg=C_PANEL, fg=C_SUB, font=self.font_s).pack(side="left", padx=3)

        def preset():
            try:
                ms = float(v_ms.get())
                gr = float(v_gr.get().strip().replace(",", ".") or "1")
            except ValueError:
                return
            if ms <= 0 or gr <= 0:
                return
            # 회전축(달걀 홀더)의 스텝 번호: X가 회전이면 $100, Y가 회전이면 $101
            rot_num = 100 if self.axes.x_is_rot else 101
            val = f"{200.0 * ms * gr / 360.0:.4g}"
            entries[rot_num].set(val)
            self.log(f"프리셋: ${rot_num} = 200 × {ms:g} × {gr:g} ÷ 360 = {val} 을 넣었어요. "
                     "[변경 저장]을 누르면 저장돼요.")

        self._btn(pre, "계산해서 넣기", preset).pack(side="left", padx=6)

        br = tk.Frame(win, bg=C_PANEL)
        br.pack(pady=10)
        self._btn(br, "다시 읽기($$)", reread).pack(side="left", padx=3)
        self._btn(br, "변경 저장", apply, bg="#10B981", fg="white", bold=True).pack(side="left", padx=3)
        reread()

    def calib_rotation(self):
        """기계가 실제로 몇 바퀴 도는지 사용자가 알려주면 회전 배율(cal_rot_scale)을 자동 계산한다.

        GRBL $100(스텝/도)을 고치기 싫거나 어려워도 이걸로 앱 쪽에서 맞출 수 있어요.
        원리: '1바퀴(360°) 돌려라' 고 명령했는데 실제로 R바퀴 돌았다면 배율을 1/R 로 줄인다."""
        if not self.grbl.connected:
            self.log("먼저 기계에 연결하세요.")
            return
        a, p = self.axes, self.params
        ax = "X" if a.x_is_rot else "Y"
        win = tk.Toplevel(self)
        win.title("회전 자동 보정")
        win.configure(bg=C_PANEL)
        win.transient(self)
        win.grab_set()
        tk.Label(win, text=f"현재 회전 배율: x{p.cal_rot_scale:g}\n\n"
                           "1. 달걀(또는 척)에 펜으로 표시를 하세요.\n"
                           f"2. [1바퀴 돌리기] 를 누르면 360° 명령을 보내요.\n"
                           "3. 표시가 실제로 몇 바퀴 돌았는지 아래에 적고 [배율 적용] 을 누르세요.\n"
                           "(예: 10바퀴 돌았으면 10, 반 바퀴면 0.5)",
                  bg=C_PANEL, fg=C_TEXT, font=self.font, justify="left").pack(padx=14, pady=(12, 8), anchor="w")

        v = tk.StringVar(value="1")
        row = tk.Frame(win, bg=C_PANEL)
        row.pack(padx=14, pady=4)
        tk.Label(row, text="실제 바퀴 수:", bg=C_PANEL, fg=C_TEXT, font=self.font).pack(side="left")
        tk.Entry(row, textvariable=v, width=7).pack(side="left", padx=6)

        def spin():
            for ln in ("G91", f"G0 {ax}360 F1000", "G90"):
                self.manual(ln)
            self.log("회전 보정: 360° 명령을 보냈어요. 실제로 몇 바퀴 돌았는지 확인하세요.")

        def apply():
            try:
                revs = float(v.get().strip().replace(",", "."))
            except ValueError:
                messagebox.showwarning("입력 오류", "숫자로 적어주세요 (예: 10 또는 0.5)", parent=win)
                return
            if revs <= 0:
                messagebox.showwarning("입력 오류", "0보다 큰 숫자를 적어주세요.", parent=win)
                return
            old = p.cal_rot_scale
            p.cal_rot_scale = round(old / revs, 4)
            self.sync_controls()
            self.recompute()
            self.log(f"회전 보정: 실제 {revs:g}바퀴 → 배율 x{old:g} ÷ {revs:g} = x{p.cal_rot_scale:g} 적용했어요.")
            win.destroy()

        br = tk.Frame(win, bg=C_PANEL)
        br.pack(pady=10)
        self._btn(br, "1바퀴 돌리기", spin).pack(side="left", padx=3)
        self._btn(br, "배율 적용", apply, bg="#10B981", fg="white", bold=True).pack(side="left", padx=3)

    # ============================================================ 작업 저장 / 이어하기
    def _project_dict(self):
        d = {"app": "eggbot-studio", "version": 1, "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
             "params": asdict(self.params), "axes": asdict(self.axes),
             "image_name": os.path.basename(self.image_path) if self.image_path else "",
             "selected": sorted(self.art.selected), "image_png": None,
             "region_pen": {str(r): int(k) for r, k in self.art.region_pen.items()},
             "outline_pen": {str(r): int(k) for r, k in self.art.outline_pen.items()},
             "origin": dict(self.origin),
             "manual": Artwork.manual_to_json(self.art.manual),
             "origin_set": self._origin_set}
        if self.art.img is not None:
            buf = io.BytesIO()
            self.art.img.save(buf, "PNG")
            d["image_png"] = base64.b64encode(buf.getvalue()).decode("ascii")
        return d

    @staticmethod
    def _write_json(path, data):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)               # 쓰다가 꺼져도 기존 파일이 깨지지 않게

    def _autosave(self):
        if self.art.img is None and not self.origin.get("saved"):
            return
        try:
            self._write_json(SESSION_FILE, self._project_dict())
        except Exception as e:
            self.log(f"자동 저장 실패: {e}")

    def _autosave_tick(self):
        self._autosave()
        try:
            self.after(AUTOSAVE_MS, self._autosave_tick)
        except tk.TclError:
            pass

    def _apply_values(self, obj, values):
        for f in fields(obj):
            if f.name not in values:
                continue
            cur, v = getattr(obj, f.name), values[f.name]
            try:
                setattr(obj, f.name, bool(v) if isinstance(cur, bool) else type(cur)(v))
            except (TypeError, ValueError):
                pass

    def apply_atc_settings(self):
        """저장해 둔 ATC 설정 파일이 있으면 현재 Params 에 덮어쓴다. 반환: 적용 여부"""
        d = load_atc_settings()
        d = {k: v for k, v in d.items() if k in ATC_FIELDS}
        if d.get("mech_script") is not None and "MECH_V6" not in d["mech_script"]:
            d.pop("mech_script")     # 예전(숫자 고정) 스크립트는 새 기본값(공식으로 자동 계산)으로 교체
        if not d:
            return False
        self._apply_values(self.params, d)
        return True

    def _migrate_mech(self):
        """옛 저장본(autosave/프로젝트)에 들어 있던 옛 교환 스크립트·값을 새 메커니즘으로 강제 교체한다.
        새 스크립트에는 'MECH_V3' 표식이 있다. 표식이 없으면 옛 것이므로 스크립트와 ATC 숫자값을 새 기본값으로 되돌린다."""
        p = self.params
        if "MECH_V6" in (p.mech_script or ""):
            return False
        d = Params()
        if "MECH_V5" in (p.mech_script or ""):      # V5 -> V6: 1->2 블록을 숫자 고정에서 공식(자동 계산)으로 교체
            p.mech_script = d.mech_script
            self.log("펜 교환 스크립트를 최신(V6)으로 교체했어요. 이제 1→2 교환도 위 설정값을 바꾸면 바로 반영돼요.")
            return True
        if "MECH_V3" in (p.mech_script or "") or "MECH_V4" in (p.mech_script or ""):      # V3/V4 -> V6: 스크립트만 교체 (숫자 설정은 유지)
            p.mech_script = d.mech_script
            self.log("펜 교환 스크립트를 최신(V6)으로 교체했어요.")
            return True
        for k in ("mech_script", "mech_use", "atc_axis", "atc_park", "atc_dock", "atc_feed", "turret_axis",
                  "turret_feed", "turret_step", "turret_off", "mech_half", "mech_home", "change_wait_ms",
                  "exch_at_origin"):
            setattr(p, k, getattr(d, k))
        self.log("옛 펜 교환 설정을 새 메커니즘(공식 자동 계산 방식)으로 교체했어요.")
        return True

    def _apply_project(self, d):
        self._apply_values(self.params, d.get("params", {}))
        self._migrate_mech()           # 옛 저장본의 교환 설정은 새 메커니즘으로 교체
        self.apply_atc_settings()      # ATC 설정은 전용 파일이 우선
        self._apply_values(self.axes, d.get("axes", {}))
        # 예전 저장본의 거친 외곽선 단순화 값(0.4mm 등)이 새 기본값을 덮어쓰지 않게 한다.
        if self.params.simplify_mm > 0.1:
            self.params.simplify_mm = 0.03
        b64 = d.get("image_png")
        if b64 and HAS_PIL:
            self.art.load(Image.open(io.BytesIO(base64.b64decode(b64))))
            self.image_path = d.get("image_name") or ""
            self.lbl_file.config(text="📂 현재 파일: " + (self.image_path or "(저장된 그림)"), fg="#0F172A")
            self.art.manual = Artwork.manual_from_json(d.get("manual", []))
        else:
            self.art = Artwork()
            self.image_path = ""
            self.lbl_file.config(text="📂 현재 파일: 없음", fg=C_SUB)
        self.sync_controls()
        self.recompute(force=True)                       # 영역 번호가 만들어진 뒤에
        self.art.selected = {int(r) for r in d.get("selected", []) if 0 <= int(r) < len(self.art.sizes)}
        self.art.region_pen = {int(r): int(k) for r, k in d.get("region_pen", {}).items()
                               if 0 <= int(r) < len(self.art.sizes)}
        self.art.outline_pen = {int(r): int(k) for r, k in d.get("outline_pen", {}).items()
                                if 0 <= int(r) < len(self.art.sizes)}
        if isinstance(d.get("origin"), dict):
            self.origin.update({k: d["origin"][k] for k in self.origin if k in d["origin"]})
        self.gcode_edited = False
        self.recompute()

    def save_project(self):
        if self.art.img is None:
            messagebox.showwarning("저장할 작업 없음", "먼저 이미지를 열어 그림을 만드세요.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".eggbot", title="작업 저장",
                                            filetypes=[("에그봇 작업", "*.eggbot"), ("모든 파일", "*.*")])
        if not path:
            return
        try:
            self._write_json(path, self._project_dict())
        except Exception as e:
            messagebox.showerror("저장 실패", str(e))
            return
        self.log(f"작업 저장: {path}")

    def open_project(self):
        path = filedialog.askopenfilename(title="작업 불러오기",
                                          filetypes=[("에그봇 작업", "*.eggbot *.json"), ("모든 파일", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                self._apply_project(json.load(f))
        except Exception as e:
            messagebox.showerror("불러오기 실패", str(e))
            return
        self.log(f"작업 불러오기: {path}")

    def _restore_autosave(self):
        if not os.path.exists(SESSION_FILE):
            return
        try:
            with open(SESSION_FILE, encoding="utf-8") as f:
                d = json.load(f)
            self._apply_project(d)
        except Exception as e:
            self.log(f"이전 작업을 복원하지 못했어요: {e}")
            return
        self.log(f"이전 작업을 이어서 불러왔어요 ({d.get('saved', '')}). 처음부터 하려면 '새로 시작'을 누르세요.")

    def new_project(self):
        if self.art.img is not None and not messagebox.askyesno(
                "새로 시작", "지금 작업을 지우고 처음부터 시작할까요?\n(자동 저장된 내용도 지워져요)"):
            return
        self.params = Params()
        self.apply_atc_settings()
        self.axes = AxisMap()
        self.art = Artwork()
        self.image_path = ""
        self._origin_set = False
        self.lbl_file.config(text="📂 현재 파일: 없음", fg=C_SUB)
        self.gcode_edited = False
        try:
            os.remove(SESSION_FILE)
        except OSError:
            pass
        self.sync_controls()
        self.recompute(force=True)
        self._autosave()                  # 원점 기록은 새로 시작해도 남긴다
        self.log("새로 시작했어요.")

    # ============================================================ 저장/종료
    def save_gcode(self):
        if not self.gcode:
            messagebox.showwarning("G코드 없음", "먼저 이미지를 열어 그림을 만드세요.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".gcode", title="G코드 저장",
                                            filetypes=[("G-code", "*.gcode *.nc *.ngc"), ("모든 파일", "*.*")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(to_servo_lines(self.gcode)) + "\n")
        self.log(f"G코드 저장: {path} ({len(self.gcode)}줄)")
        messagebox.showinfo("저장 완료", f"{path}\n{len(self.gcode)}줄을 저장했어요.")

    def _on_close(self):
        self._autosave()
        if self._recompute_job:
            try:
                self.after_cancel(self._recompute_job)
            except Exception:
                pass
        try:
            self.grbl.disconnect()
        except Exception:
            pass
        if self.preview is not None and self.preview.winfo_exists():
            self.preview.close()
        self.destroy()


# =============================================================================
# 9. 도구 창: 보정 / 원점 자동 조정 / 자가 점검 / 펜 교환 설정
# =============================================================================
class ToolWindow(tk.Toplevel):
    """도구 창 공통 틀: 제목/크기/배경, 메인 창 옆에 띄움. (모달 아님 - 열어둔 채 메인 조작 가능)"""

    def __init__(self, app, title, w, h):
        super().__init__(app)
        self.app = app
        self.title(title)
        self.configure(bg=C_PANEL)
        try:
            x = max(0, app.winfo_x() + 60)
            y = max(0, app.winfo_y() + 40)
            self.geometry(f"{w}x{h}+{x}+{y}")
        except tk.TclError:
            self.geometry(f"{w}x{h}")
        self.minsize(w - 80, 300)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def close(self):
        self.destroy()

    def section(self, text):
        lf = tk.LabelFrame(self, text=f" {text} ", bg=C_PANEL, fg=C_TEXT, font=self.app.font_b)
        lf.pack(fill="x", padx=12, pady=(10, 0))
        return lf

    def note(self, parent, text, fg="#94A3B8"):
        tk.Label(parent, text=text, bg=parent.cget("bg"), fg=fg, font=self.app.font_s, justify="left",
                 anchor="w", wraplength=470).pack(fill="x", padx=8, pady=(2, 6))


# -----------------------------------------------------------------------------
# 자가 점검
# -----------------------------------------------------------------------------
def run_selfcheck(app):
    """현재 설정/그림/기계 상태를 점검해 [(등급, 제목, 설명)] 을 돌려준다.
    등급: ok(통과) / warn(주의) / err(문제) / info(참고)"""
    p, g, a = app.params, app.grbl, app.art
    R = []

    def add(level, title, detail=""):
        R.append((level, title, detail))

    # ---- 1. 환경 -------------------------------------------------------------
    add("ok" if HAS_PIL else "err", "이미지 라이브러리(Pillow)",
        "설치됨" if HAS_PIL else "python -m pip install pillow")
    add("ok" if HAS_SERIAL else "warn", "시리얼 라이브러리(pyserial)",
        "설치됨" if HAS_SERIAL else "기계 연결에 필요해요: python -m pip install pyserial  (G코드 저장만 할 거면 괜찮아요)")
    fam = app.font.actual("family")
    add("ok" if fam in FONT_CANDIDATES else "warn", "한글 글꼴", f"사용 중: {fam}"
        + ("" if fam in FONT_CANDIDATES else "  (한글이 깨져 보이면 '맑은 고딕' 설치 필요)"))
    try:
        os.makedirs(SESSION_DIR, exist_ok=True)
        probe = os.path.join(SESSION_DIR, ".write_test")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
        add("ok", "자동 저장 폴더", f"쓰기 가능: {SESSION_DIR}")
    except Exception as e:                                  # pragma: no cover
        add("err", "자동 저장 폴더", f"쓸 수 없어요 - 진행 상황이 저장되지 않아요 ({e})")

    # ---- 2. 그림 -------------------------------------------------------------
    if a.img is None:
        add("warn", "그림", "이미지를 열거나 '빈 캔버스'로 직접 그려 보세요.")
    elif not app.ordered:
        add("warn", "그릴 선이 없어요", (a.note or "'선 추출' 탭에서 방식/민감도를 바꾸거나, '✏ 직접 그리기'로 선을 그려 보세요."))
    else:
        st = app.stats
        add("ok", "그림", f"선 {st.get('n_strokes', 0)}개 (테두리 {st.get('n_outline', 0)} / 채우기 {st.get('n_fill', 0)} / "
                         f"직접 {st.get('n_draw', 0)}), 점 {st.get('n_points', 0)}개")
        if a.note:
            add("info", "선 추출 안내", a.note)

    # ---- 3. 범위 -------------------------------------------------------------
    if app.ordered:
        degs = [q[0] for _k, pl, _p in app.ordered for q in pl]
        hs = [q[1] for _k, pl, _p in app.ordered for q in pl]
        half = app.shape.half
        problems = []
        if min(degs) < 0 or max(degs) > p.x_max_deg:
            problems.append(f"회전 {min(degs):.0f}~{max(degs):.0f}° (가능 0~{p.x_max_deg:.0f}°)")
        if min(hs) < -half or max(hs) > half:
            problems.append(f"높이 {min(hs):.1f}~{max(hs):.1f}mm (물체 ±{half:.1f}mm)")
        if problems:
            add("err", "그림이 범위를 벗어남", " / ".join(problems) + "  → 위치·크기를 줄이거나 옮겨 주세요.")
        else:
            add("ok", "그림 범위", f"회전 {min(degs):.0f}~{max(degs):.0f}° / 높이 {min(hs):.1f}~{max(hs):.1f}mm 안에 있어요.")

    # ---- 4. 펜/속도 ----------------------------------------------------------
    d = abs(p.pen_up - p.pen_down)
    add("ok" if d >= 10 else "warn", "펜 서보 값", f"올림 S{p.pen_up} / 내림 S{p.pen_down} (차이 {d})"
        + ("" if d >= 10 else "  → 차이가 너무 작아 펜이 안 떨어질 수 있어요. '보정' 창에서 시험해 보세요."))
    if p.draw_feed > p.travel_feed:
        add("warn", "속도", f"그리기 속도({p.draw_feed})가 이동 속도({p.travel_feed})보다 빨라요.")
    elif p.dwell_ms < 100:
        add("warn", "펜 딜레이", f"{p.dwell_ms}ms 는 짧아요. 서보가 다 내려가기 전에 움직일 수 있어요.")
    else:
        add("ok", "속도/딜레이", f"그리기 F{p.draw_feed} / 이동 F{p.travel_feed} / 딜레이 {p.dwell_ms}ms")

    # ---- 5. 보정 -------------------------------------------------------------
    cal = (p.cal_rot_scale, p.cal_h_scale, p.cal_rot_off, p.cal_h_off)
    if cal == (1.0, 1.0, 0.0, 0.0):
        add("info", "보정", "보정 안 함(기본값). '보정' 창에서 시험 패턴을 그려 실제 크기를 맞춰 보세요.")
    elif not (0.8 <= p.cal_rot_scale <= 1.25 and 0.8 <= p.cal_h_scale <= 1.25):
        add("warn", "보정 배율이 커요", f"회전 x{p.cal_rot_scale:g}, 높이 x{p.cal_h_scale:g} - 측정값을 다시 확인하세요.")
    else:
        add("ok", "보정", f"회전 x{p.cal_rot_scale:g} {p.cal_rot_off:+g}° / 높이 x{p.cal_h_scale:g} {p.cal_h_off:+g}mm")

    # ---- 6. 펜 교환(ATC) -------------------------------------------------------
    used = sorted({s[2] for s in app.ordered})
    if p.npen() == 1:
        add("info", "펜 교환", "펜 1개 사용 - 교환 없음")
    else:
        hexes = p.pen_hex()[:p.npen()]
        add("ok", "펜 교환", f"펜 {p.npen()}개 중 {len(used)}개 사용, 교환 {app.stats.get('n_changes', 0)}회")
        if len(set(hexes)) < len(hexes):
            add("warn", "펜 색이 겹쳐요", "같은 색의 펜이 있어요. 색 자동 배정이 헷갈릴 수 있어요.")
        else:
            rg = p.pen_rgb()[:p.npen()]
            close = [(i + 1, j + 1) for i in range(len(rg)) for j in range(i + 1, len(rg))
                     if sum((rg[i][k] - rg[j][k]) ** 2 for k in range(3)) < 60 ** 2]
            if close:
                add("warn", "펜 색이 너무 비슷해요", f"{close[0][0]}번과 {close[0][1]}번 색이 비슷해서 영역 구분이 어려울 수 있어요.")
            else:
                add("ok", "펜 색", " / ".join(f"{i + 1}번 {h}" for i, h in enumerate(hexes)))
        if p.change_mode == CHANGE_MODES[0]:
            ax = p.turret_axis.strip().upper()
            if ax in ("X", "Y") or len(ax) != 1:
                add("err", "펜 매거진 축", f"'{p.turret_axis}' 는 쓸 수 없어요. 회전/높이 축과 다른 글자(Z, A, B, C)를 고르세요.")
            else:
                add("ok", "펜 매거진 축", f"{ax}축, 슬롯 간 {p.step_deg():g}°, 교환 {p.turret_feed}도/분")
            if p.step_deg() * (p.npen() - 1) > 360.0:
                add("warn", "슬롯 각도", "슬롯을 다 돌면 한 바퀴를 넘어요. 간격을 줄여 주세요.")
            ax2 = p.atc_axis.strip().upper()
            if ax2 in ("X", "Y") or len(ax2) != 1 or ax2 == ax:
                add("err", "ATC 접근 축", f"'{p.atc_axis}' 는 쓸 수 없어요. X/Y/매거진 축({ax})과 다른 글자(A, B, C, Z)를 고르세요.")
            else:
                add("ok", "ATC 접근 축", f"{ax2}축: 대기 {p.atc_park:g} → 도킹 {p.atc_dock:g} mm, 속도 F{p.atc_feed}")
            if abs(p.atc_pen_h) > app.shape.half:
                add("warn", "펜 교환 위치", f"교환 위치 {p.atc_pen_h:g}mm 는 달걀 끝(±{app.shape.half:g}mm) 바깥이에요. ATC 가 달걀과 부딪치지 않게 "
                    "일부러 바깥에 두는 값이라면 괜찮아요. 다만 펜 팔이 그 위치까지 갈 수 있는지(기계 이동 범위)는 꼭 확인하세요.")
            add("info", "펌웨어 축 개수", f"매거진({ax})과 ATC({ax2}) 축을 쓰려면 5축 이상 펌웨어(grbl-Mega 5X, FluidNC, Marlin/RAMPS 등)가 필요해요. "
                                     "기본 GRBL 1.1 은 X/Y/Z 3축뿐이라 'error:20' 이 날 수 있어요.")
            if used and len(used) > 1 and not any(l.startswith(f"G0 {ax}") for l in app.gcode) and not app.gcode_edited:
                add("warn", "G코드에 교환 동작이 없어요", "다시 계산해 보세요.")
        else:
            add("info", "수동 교환(M0)", "펜 바꿀 때 기계가 멈춰요. 펜을 바꾼 뒤 '▶ 재개'를 눌러야 이어서 그려요.")

    # ---- 6a. 그림이 펜이 닿는 높이를 넘는지 ---------------------------------------
    try:
        dh = p.height_mm / 2.0 + abs(p.pos_y_mm)
        hl = getattr(app.art, "hlim", None)
        if hl is not None and dh > hl:
            add("warn", "그림이 너무 커요", f"그림 높이가 펜이 닿는 범위(±{hl:.1f}mm)를 넘어요. 넘는 부분은 그리지 않고 잘라내요. "
                f"'그림 높이'를 {2 * (hl - abs(p.pos_y_mm)):.0f}mm 이하로 줄이면 전체가 그려져요.")
    except Exception:
        pass

    # ---- 6b. 딤플 보정 ---------------------------------------------------------
    if p.has_dimples or p.dimple_comp:
        if p.has_dimples and not p.dimple_comp:
            add("warn", "딤플 보정이 꺼져 있어요", "표면에 딤플이 있는데 보정이 꺼져 있으면 펜이 홈에 걸려 선이 끊겨요.")
        elif p.dimple_comp and not p.has_dimples:
            add("info", "딤플 보정", "'표면에 딤플 있음'이 꺼져 있어서 보정이 적용되지 않아요.")
        else:
            n = len(app.shape.dimple_centers()) if app.shape.dimples else 0
            add("ok", "딤플 보정", f"딤플 {n}개 / {'자동 (가장자리·안쪽 감속, 압력, 겹침)' if p.dimple_auto else '수동 값 사용'}")
    if app.gcode_edited:
        add("warn", "편집한 G코드 사용 중", "G코드 편집창에서 '3D에 적용'한 편집본이에요. 설정을 바꾸면 자동 생성으로 돌아가요. "
                                      "기계에서 그리기 전에 한 번 더 확인하세요.")

    # ---- 7. G코드 ------------------------------------------------------------
    if app.gcode:
        L = app.gcode
        bad = []
        axis_vals = {"X": [], "Y": []}
        for i, ln in enumerate(L):
            if ln.startswith(";"):
                continue
            for m in re.finditer(r"([XY])(-?[\d.]+)", ln):
                try:
                    v = float(m.group(2))
                    if math.isnan(v) or math.isinf(v):
                        bad.append(i + 1)
                    axis_vals[m.group(1)].append(v)
                except ValueError:
                    bad.append(i + 1)
        up = f"M3 S{int(p.pen_up)}"
        last_m3 = [l for l in L if l.startswith("M3 S")]
        if bad:
            add("err", "G코드 숫자 오류", f"{len(bad)}줄에 이상한 값이 있어요 (예: {bad[:3]}줄)")
        elif not L[-1].startswith("M2") or not last_m3 or last_m3[-1] != up:
            add("warn", "G코드 마무리", "끝에서 펜을 올리고 M2 로 끝나야 해요.")
        else:
            xs, ys = axis_vals["X"], axis_vals["Y"]
            rng = (f"X {min(xs):.1f}~{max(xs):.1f} / Y {min(ys):.1f}~{max(ys):.1f}" if xs and ys else "")
            add("ok", "G코드", f"{len(L)}줄, 예상 약 {app.stats.get('minutes', 0):.1f}분, {rng}")
    else:
        add("info", "G코드", "아직 만들어진 G코드가 없어요.")

    # ---- 8. 기계 연결 ----------------------------------------------------------
    if not g.connected:
        ports = g.list_ports()
        add("info", "기계 연결", "연결 안 됨" + (f" (포트 {len(ports)}개 보임: {', '.join(ports)})" if ports else " (포트가 안 보여요)"))
    else:
        age = time.time() - g.last_rx if g.last_rx else 999.0
        st = g.status or {}
        state = str(st.get("state", "?"))
        if age > 3.0:
            add("err", "기계가 응답하지 않아요", f"마지막 수신 {age:.0f}초 전 - 케이블/전원/포트를 확인하세요.")
        elif state.lower().startswith("alarm") or g.alarm:
            add("err", "기계 알람", f"{g.alarm or state} → '그리기 시작'을 누르면 자동으로 잠금 해제한 뒤 그려요.")
        elif state.lower().startswith("hold"):
            add("warn", "기계 일시정지(Hold)", "재개('~') 하거나 비상 정지 후 다시 시작하세요.")
        else:
            add("ok", "기계 연결", f"상태 {state}" + (f" / {g.info}" if g.info else ""))
        S = g.settings
        if not S:
            add("info", "GRBL 설정", "아직 읽지 않았어요. '자가 점검' 창의 [GRBL 설정 읽기($$)] 를 눌러 보세요.")
        else:
            smax = S.get(30)
            if smax is not None and max(p.pen_up, p.pen_down) > smax:
                add("err", "서보 S 값이 너무 커요", f"GRBL $30(최대 S)={smax:g} 인데 펜 값은 S{max(p.pen_up, p.pen_down)} 예요.")
            else:
                add("ok", "서보 S 범위", f"$30={smax:g}" if smax is not None else "정보 없음")
            rot_x = app.axes.x_is_rot
            # 회전축 스텝 수($100/$101)가 '도(°)'용 값인지 확인.
            # 리드스크류 mm용 값(예: 250)이면 1도 명령이 수십 도로 늘어나 드릴처럼 돈다.
            steps_rot = S.get(100 if rot_x else 101)
            if steps_rot is not None and steps_rot > 30:
                add("err", "회전축 스텝 수 안 맞음",
                    f"${100 if rot_x else 101}={steps_rot:g} 은 리드스크류(mm)용 값처럼 보여요. "
                    f"X를 도(°)로 쓰는 이 기계는 보통 한 자릿수(예: 직결+16마이크로스텝이면 약 8.9)예요. "
                    "달걀이 드릴처럼 돌면 '회전 보정' 버튼으로 맞추거나 $100=8.889 를 직접 넣어보세요.")
            elif steps_rot is not None:
                add("ok", "회전축 스텝 수", f"${100 if rot_x else 101}={steps_rot:g} (도 단위로 쓰기에 알맞아 보여요)")
            rate_rot, rate_h = S.get(110 if rot_x else 111), S.get(111 if rot_x else 110)
            trav_rot, trav_h = S.get(130 if rot_x else 131), S.get(131 if rot_x else 130)
            fmax = max(p.draw_feed, p.travel_feed)
            if rate_rot and fmax > rate_rot * 1.001:
                add("warn", "속도가 기계 한계보다 빨라요", f"회전축 최대 속도({110 if rot_x else 111}번 설정) {rate_rot:g} < F{fmax} (기계가 알아서 줄여요)")
            need_rot = p.x_max_deg * max(p.cal_rot_scale, 1.0)
            need_h = app.shape.half * max(p.cal_h_scale, 1.0)
            if trav_rot and trav_rot < need_rot:
                add("warn", "회전축 이동 한계", f"GRBL 이동 한계 {trav_rot:g} < 필요 {need_rot:.0f}")
            if trav_h and trav_h < need_h * 2:
                add("warn", "높이축 이동 한계", f"GRBL 이동 한계 {trav_h:g} < 필요 {need_h * 2:.0f}")
            if p.home_use and not S.get(22):
                add("warn", "홈잉이 꺼져 있어요", "GRBL $22=0 이라 $H 가 동작하지 않아요. 홈 스위치 설정을 확인하세요.")
            if S.get(32):
                add("info", "레이저 모드($32=1)", "서보를 M3 S 로 움직이는 설정이면 $32=0 이어야 해요.")

    # ---- 9. 원점 -------------------------------------------------------------
    wpos = (g.status or {}).get("wpos") if g.connected else None
    if app._origin_set:
        add("ok", "원점", "이번 실행에서 원점을 맞췄어요." + (f" 현재 작업 좌표 X{wpos[0]:.1f} Y{wpos[1]:.1f}" if wpos else ""))
    else:
        add("warn", "원점", "아직 원점을 맞추지 않았어요. '원점 자동 조정'으로 달걀 가운데(0°)에 맞추세요.")
    o = app.origin
    if o.get("saved"):
        if app._origin_set:
            add("ok", "원점 저장 상태", app.origin_summary())
        else:
            add("warn", "원점 저장 상태", app.origin_summary() + " - 이번에는 아직 복원하지 않았어요. '원점 자동 조정' 창의 [저장된 원점 복원]을 누르세요.")
        if not (o.get("g54") or o.get("home_use") or o.get("parked")):
            add("info", "원점 복원 방법", "홈 스위치/G54 저장/파킹 중 하나도 없어서 전원을 껐다 켜면 원점을 직접 다시 맞춰야 해요.")
    else:
        add("info", "원점 저장 상태", "저장된 원점이 없어요. 원점을 맞추면 앱이 자동으로 기록해요(전원을 꺼도 앱에는 남아요).")
    return R


class SelfCheckDialog(ToolWindow):
    ICON = {"ok": "✔", "warn": "▲", "err": "✖", "info": "●"}
    COLOR = {"ok": "#15803D", "warn": "#B45309", "err": "#B91C1C", "info": "#475569"}

    def __init__(self, app):
        super().__init__(app, "자가 점검", 640, 640)
        top = tk.Frame(self, bg=C_PANEL)
        top.pack(fill="x", padx=12, pady=(12, 4))
        self.lbl_sum = tk.Label(top, text="", bg=C_PANEL, font=app.font_b, anchor="w")
        self.lbl_sum.pack(side="left")
        bar = tk.Frame(self, bg=C_PANEL)
        bar.pack(fill="x", padx=12, pady=2)
        app._btn(bar, "다시 점검", self.refresh, bg=C_BLUE, fg="white", bold=True).pack(side="left")
        app._btn(bar, "GRBL 설정 읽기($$)", self.read_grbl).pack(side="left", padx=6)
        app._btn(bar, "결과 복사", self.copy).pack(side="left")
        app._btn(bar, "파일로 저장", self.save).pack(side="left", padx=6)
        self.txt = tk.Text(self, font=app.font, wrap="word", bg="#F8FAFC", relief="flat", padx=10, pady=8)
        self.txt.pack(fill="both", expand=True, padx=12, pady=10)
        for lv, col in self.COLOR.items():
            self.txt.tag_configure(lv, foreground=col, font=app.font_b)
        self.txt.tag_configure("detail", foreground="#475569", lmargin1=22, lmargin2=22)
        self.results = []
        self.refresh()

    def refresh(self):
        self.results = run_selfcheck(self.app)
        t = self.txt
        t.config(state="normal")
        t.delete("1.0", "end")
        order = {"err": 0, "warn": 1, "ok": 2, "info": 3}
        for lv, title, detail in sorted(self.results, key=lambda r: order[r[0]]):
            t.insert("end", f"{self.ICON[lv]} {title}\n", lv)
            if detail:
                t.insert("end", detail + "\n", "detail")
            t.insert("end", "\n")
        t.config(state="disabled")
        n = {k: sum(1 for r in self.results if r[0] == k) for k in self.ICON}
        self.lbl_sum.config(text=f"✔ 통과 {n['ok']}   ▲ 주의 {n['warn']}   ✖ 문제 {n['err']}   ● 참고 {n['info']}",
                            fg=self.COLOR["err"] if n["err"] else self.COLOR["warn"] if n["warn"] else self.COLOR["ok"])

    def read_grbl(self):
        g = self.app.grbl
        if not g.connected:
            messagebox.showinfo("연결 필요", "먼저 기계에 연결하세요.", parent=self)
            return
        g.settings.clear()
        if g.send("$$"):
            self.after(1800, self.refresh)
            self.app.log("GRBL 설정($$)을 읽는 중...")

    def report_text(self):
        order = {"err": 0, "warn": 1, "ok": 2, "info": 3}
        lines = [f"에그봇 G코드 생성기 자가 점검 ({time.strftime('%Y-%m-%d %H:%M:%S')})", ""]
        for lv, title, detail in sorted(self.results, key=lambda r: order[r[0]]):
            lines.append(f"[{ {'ok': '통과', 'warn': '주의', 'err': '문제', 'info': '참고'}[lv] }] {title}" + (f" - {detail}" if detail else ""))
        return "\n".join(lines)

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.report_text())
        self.app.log("자가 점검 결과를 복사했어요.")

    def save(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".txt", title="점검 결과 저장",
                                            filetypes=[("텍스트", "*.txt")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.report_text() + "\n")
            self.app.log(f"자가 점검 결과 저장: {path}")


# -----------------------------------------------------------------------------
# 보정
# -----------------------------------------------------------------------------
class CalibrationDialog(ToolWindow):
    def __init__(self, app):
        super().__init__(app, "보정", 540, 820)
        p = app.params

        s1 = self.section("1. 펜 서보 시험 (펜이 달걀에 닿는 값 찾기)")
        r = tk.Frame(s1, bg=C_PANEL)
        r.pack(fill="x", padx=8, pady=4)
        tk.Label(r, text="서보 S", bg=C_PANEL, font=app.font).pack(side="left")
        self.v_s = tk.IntVar(value=p.pen_down)
        tk.Spinbox(r, from_=0, to=180, width=5, textvariable=self.v_s, font=app.font).pack(side="left", padx=4)
        app._btn(r, "이 값으로 시험", self.test_s, pady=2).pack(side="left", padx=2)
        app._btn(r, "→ 펜 내림 값으로", lambda: self.set_pen("pen_down"), pady=2).pack(side="left", padx=2)
        app._btn(r, "→ 펜 올림 값으로", lambda: self.set_pen("pen_up"), pady=2).pack(side="left", padx=2)
        r = tk.Frame(s1, bg=C_PANEL)
        r.pack(fill="x", padx=8, pady=(0, 4))
        app._btn(r, "펜 올림 시험", lambda: app.manual(f"M3 S{app.params.pen_up}"), pady=2).pack(side="left")
        app._btn(r, "펜 내림 시험", lambda: app.manual(f"M3 S{app.params.pen_down}"), pady=2).pack(side="left", padx=4)
        Slider(s1, app, "펜 올림 서보(S)", "pen_up", 0, 180, 1).pack(fill="x", padx=4)
        Slider(s1, app, "펜 내림 서보(S)", "pen_down", 0, 180, 1).pack(fill="x", padx=4)
        self.note(s1, "숫자를 바꿔 '시험'을 눌러 펜이 달걀 표면에 살짝 닿는 값을 찾으세요. (기계에 연결돼 있어야 움직여요)")

        s2 = self.section("2. 달걀 실제 크기")
        r = tk.Frame(s2, bg=C_PANEL)
        r.pack(fill="x", padx=8, pady=4)
        self.v_len, self.v_dia = tk.StringVar(value=f"{p.length_mm:g}"), tk.StringVar(value=f"{p.diameter_mm:g}")
        for txt, var in (("길이(mm)", self.v_len), ("지름(mm)", self.v_dia)):
            tk.Label(r, text=txt, bg=C_PANEL, font=app.font).pack(side="left")
            tk.Entry(r, textvariable=var, width=7, font=app.font).pack(side="left", padx=(3, 10))
        app._btn(r, "실측값 적용", self.apply_size, pady=2).pack(side="left")
        self.note(s2, "자로 잰 달걀의 길이(회전축 방향)와 가장 굵은 곳의 지름을 넣으면 그림 크기가 더 정확해져요.")

        s3 = self.section("3. 축 보정 (G코드 출력에만 적용)")
        Slider(s3, app, "회전 배율", "cal_rot_scale", 0.8, 1.2, 0.001, "{:.3f}").pack(fill="x", padx=4)
        Slider(s3, app, "높이 배율", "cal_h_scale", 0.8, 1.2, 0.001, "{:.3f}").pack(fill="x", padx=4)
        Slider(s3, app, "펜 팔 길이(mm)", "arm_len_mm", 15, 80, 0.5, "{:.1f}").pack(fill="x", padx=4)
        Slider(s3, app, "펜 팔 출력 배율", "arm_scale", 0.05, 1.5, 0.001, "{:.3f}").pack(fill="x", padx=4)
        Slider(s3, app, "펜 팔 최대 스윙 ±(도)", "arm_max_deg", 10, 90, 1, "{:.0f}").pack(fill="x", padx=4)
        Slider(s3, app, "회전 오프셋(도)", "cal_rot_off", -30, 30, 0.5, "{:+.1f}").pack(fill="x", padx=4)
        Slider(s3, app, "높이 오프셋(mm)", "cal_h_off", -15, 15, 0.1, "{:+.1f}").pack(fill="x", padx=4)
        Slider(s3, app, "펜 매거진 오프셋(도)", "turret_off", -30, 30, 0.5, "{:+.1f}").pack(fill="x", padx=4)
        app._btn(s3, "보정값 초기화", self.reset_cal, pady=2).pack(anchor="w", padx=8, pady=(2, 6))

        s4 = self.section("4. 시험 패턴으로 자동 보정")
        r = tk.Frame(s4, bg=C_PANEL)
        r.pack(fill="x", padx=8, pady=4)
        tk.Label(r, text="사각형 한 변", bg=C_PANEL, font=app.font).pack(side="left")
        self.v_size = tk.StringVar(value="20")
        ttk.Combobox(r, textvariable=self.v_size, values=["10", "20", "30"], width=4, state="readonly").pack(
            side="left", padx=4)
        tk.Label(r, text="mm", bg=C_PANEL, font=app.font).pack(side="left")
        app._btn(r, "시험 패턴 그리기", self.draw_test, bg="#10B981", fg="white", bold=True, pady=2).pack(
            side="left", padx=(10, 2))
        app._btn(r, "G코드 저장…", self.save_test, pady=2).pack(side="left", padx=2)
        r = tk.Frame(s4, bg=C_PANEL)
        r.pack(fill="x", padx=8, pady=4)
        self.v_mw, self.v_mh = tk.StringVar(), tk.StringVar()
        for txt, var in (("측정 가로(mm)", self.v_mw), ("측정 세로(mm)", self.v_mh)):
            tk.Label(r, text=txt, bg=C_PANEL, font=app.font).pack(side="left")
            tk.Entry(r, textvariable=var, width=7, font=app.font).pack(side="left", padx=(3, 10))
        app._btn(r, "배율 자동 계산", self.apply_measure, bg=C_BLUE, fg="white", bold=True, pady=2).pack(side="left")
        self.note(s4, "① 달걀을 끼우고 원점을 맞춘 뒤 시험 패턴(사각형+십자)을 그립니다.  ② 실제로 그려진 가로·세로를 자로 재서 "
                      "넣고  ③ '배율 자동 계산'을 누르면 회전/높이 배율이 맞춰져요.  ④ 한 번 더 그려 확인하세요.")
        self.lbl_res = tk.Label(s4, text="", bg=C_PANEL, fg="#15803D", font=app.font, anchor="w")
        self.lbl_res.pack(fill="x", padx=8, pady=(0, 6))

    # ---- 동작 ----------------------------------------------------------------
    def _s(self):
        try:
            return max(0, min(180, int(self.v_s.get())))
        except (tk.TclError, ValueError):
            return self.app.params.pen_down

    def test_s(self):
        self.app.manual(f"M3 S{self._s()}")

    def set_pen(self, attr):
        setattr(self.app.params, attr, self._s())
        self.app.sync_controls()
        self.app.schedule_recompute()
        self.app.log(f"{'펜 내림' if attr == 'pen_down' else '펜 올림'} 서보 값을 S{self._s()} 로 설정했어요.")

    def apply_size(self):
        try:
            ln, dia = float(self.v_len.get()), float(self.v_dia.get())
            assert 10 <= ln <= 150 and 10 <= dia <= 120
        except (ValueError, AssertionError):
            messagebox.showwarning("값 확인", "길이(10~150)와 지름(10~120)을 숫자로 넣어 주세요.", parent=self)
            return
        p = self.app.params
        p.length_mm, p.diameter_mm = ln, dia
        self.app.sync_controls()
        self.app.recompute(force=True)
        self.app.log(f"달걀 실측 적용: 길이 {ln:g} × 지름 {dia:g} mm")

    def reset_cal(self):
        p = self.app.params
        p.cal_rot_scale = p.cal_h_scale = 1.0
        p.cal_rot_off = p.cal_h_off = 0.0
        self.app.sync_controls()
        self.app.schedule_recompute()
        self.app.log("보정값을 초기화했어요.")

    def _test_lines(self):
        a = self.app
        strokes = test_strokes(a.params, a.shape, float(self.v_size.get()))
        lines, _st = build_gcode(strokes, a.params, a.shape, a.axes)
        return lines

    def draw_test(self):
        a = self.app
        if not a.grbl.connected:
            messagebox.showinfo("기계 연결 필요", "시험 패턴을 그리려면 먼저 기계에 연결하세요.\n"
                                                "(연결 없이 G코드만 저장하려면 'G코드 저장…')", parent=self)
            return
        lines = self._test_lines()
        if messagebox.askokcancel("시험 패턴", f"{len(lines)}줄을 보내 시험 패턴을 그립니다.\n달걀을 끼우고 원점을 맞췄나요?",
                                  parent=self):
            if not a.grbl.start(lines):
                a.log("시험 패턴을 시작할 수 없어요. (이미 진행 중이거나 명령 처리 중)")

    def save_test(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".gcode", title="시험 패턴 G코드 저장",
                                            filetypes=[("G-code", "*.gcode *.nc")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(self._test_lines()) + "\n")
            self.app.log(f"시험 패턴 G코드 저장: {path}")

    def apply_measure(self):
        try:
            mw, mh = float(self.v_mw.get()), float(self.v_mh.get())
            size = float(self.v_size.get())
            assert mw > 1 and mh > 1
        except (ValueError, AssertionError):
            messagebox.showwarning("값 확인", "측정한 가로·세로를 mm 숫자로 넣어 주세요.", parent=self)
            return
        p = self.app.params
        p.cal_rot_scale = round(max(0.8, min(1.2, p.cal_rot_scale * size / mw)), 4)
        p.cal_h_scale = round(max(0.8, min(1.2, p.cal_h_scale * size / mh)), 4)
        self.app.sync_controls()
        self.app.schedule_recompute()
        self.lbl_res.config(text=f"→ 회전 배율 {p.cal_rot_scale:.4f} / 높이 배율 {p.cal_h_scale:.4f} 로 보정했어요. 다시 그려 확인하세요.")
        self.app.log(f"자동 보정: 회전 x{p.cal_rot_scale:.4f}, 높이 x{p.cal_h_scale:.4f}")


# -----------------------------------------------------------------------------
# 원점 자동 조정
# -----------------------------------------------------------------------------
ORIGIN_NOTE = ("전원을 끄면 컨트롤러는 모터가 어디 있었는지 잊어요(엔코더가 없어서 위치를 알 수 없어요). 그래서\n"
               "① 홈(리밋) 스위치가 있으면: $H 로 기계 기준점을 다시 찾고, 저장해 둔 G54 원점(G10 L20 P1, GRBL 이 EEPROM 에 기록)을 "
               "불러오면 같은 자리로 돌아와요.\n"
               "② 스위치가 없으면: 끄기 전에 [원점 복귀 + 파킹]으로 원점에 세워 두고 전원을 끄세요. 다시 켜면 그 자리를 원점으로 잡아요. "
               "전원이 꺼진 사이 손으로 돌렸다면 틀리니 '십자 시험'으로 확인하세요.\n"
               "앱은 원점 기록을 자동 저장해서 다음 연결 때 복원을 제안해요. (펌웨어에 따라 G54 저장 방식은 다를 수 있어요)")


class OriginDialog(ToolWindow):
    def __init__(self, app):
        super().__init__(app, "원점 자동 조정", 620, 900)
        p = app.params
        self.v_persist = tk.BooleanVar(value=bool(app.origin.get("g54")))
        self.running = False
        self.steps, self.i, self.sent_t, self.n_done = [], -1, 0.0, 0
        self.v_home = tk.BooleanVar(value=p.home_use)
        self.v_step = tk.StringVar(value="1")

        s1 = self.section("방식")
        tk.Checkbutton(s1, text="홈(리밋) 스위치가 있어요  →  $H 로 자동 홈잉 후 달걀 원점으로 이동",
                       variable=self.v_home, command=self._mode, bg=C_PANEL, font=app.font,
                       activebackground=C_PANEL).pack(anchor="w", padx=8, pady=(4, 0))
        self.f_home = tk.Frame(s1, bg=C_PANEL)
        self.f_home.pack(fill="x")
        Slider(self.f_home, app, "홈→원점 이동 X", "home_off_x", -200, 200, 0.5, "{:+.1f}").pack(fill="x", padx=4)
        Slider(self.f_home, app, "홈→원점 이동 Y", "home_off_y", -200, 200, 0.5, "{:+.1f}").pack(fill="x", padx=4)
        Slider(self.f_home, app, "홈 이동 속도(F)", "home_feed", 100, 3000, 50).pack(fill="x", padx=4)
        self.note(s1, "홈 스위치가 없으면 체크를 끄세요. 펜을 올린 채 직접 조그로 '달걀 가운데·0°'에 맞춘 뒤 원점으로 정해요.")

        s2 = self.section("진행 단계")
        self.lst = tk.Text(s2, height=8, font=app.font, bg="#F8FAFC", relief="flat", wrap="word", state="disabled")
        self.lst.pack(fill="x", padx=8, pady=6)
        self.lst.tag_configure("done", foreground="#15803D")
        self.lst.tag_configure("now", foreground="#1D4ED8", font=app.font_b)
        self.lst.tag_configure("fail", foreground="#B91C1C", font=app.font_b)
        r = tk.Frame(s2, bg=C_PANEL)
        r.pack(fill="x", padx=8, pady=(0, 6))
        self.btn_run = app._btn(r, "▶ 자동 조정 시작", self.run, bg="#10B981", fg="white", bold=True)
        self.btn_run.pack(side="left")
        app._btn(r, "중지", self.stop, bg="#EF4444", fg="white").pack(side="left", padx=6)
        self.lbl_state = tk.Label(r, text="", bg=C_PANEL, fg=C_SUB, font=app.font)
        self.lbl_state.pack(side="left", padx=8)

        self.s3 = self.section("직접 맞추기 (홈 스위치 없을 때 / 미세 조정)")
        r = tk.Frame(self.s3, bg=C_PANEL)
        r.pack(fill="x", padx=8, pady=6)
        tk.Label(r, text="조그 간격", bg=C_PANEL, font=app.font).pack(side="left")
        ttk.Combobox(r, textvariable=self.v_step, values=["0.1", "0.5", "1", "5", "10"], width=5,
                     state="readonly").pack(side="left", padx=4)
        for txt, rot, sg in (("회전 -", True, -1), ("회전 +", True, 1), ("팔 위 +", False, 1), ("팔 아래 -", False, -1)):
            app._btn(r, txt, lambda a=rot, b=sg: self.jog(a, b), padx=8, pady=3).pack(side="left", padx=2)
        r = tk.Frame(self.s3, bg=C_PANEL)
        r.pack(fill="x", padx=8, pady=(0, 6))
        app._btn(r, "펜 올림", lambda: app.manual(f"M3 S{app.params.pen_up}"), pady=3).pack(side="left")
        app._btn(r, "펜 내림", lambda: app.manual(f"M3 S{app.params.pen_down}"), pady=3).pack(side="left", padx=4)
        app._btn(r, "여기가 원점(달걀 가운데·0°) → G92 X0 Y0", self.set_here, bg=C_BLUE, fg="white", bold=True,
                 pady=3).pack(side="left", padx=8)
        r = tk.Frame(self.s3, bg=C_PANEL)
        r.pack(fill="x", padx=8, pady=(0, 6))
        app._btn(r, "십자 시험(원점 확인)", self.cross_test, pady=3).pack(side="left")
        app._btn(r, "원점으로 복귀", lambda: app.manual("G0 X0 Y0"), pady=3).pack(side="left", padx=6)
        self.note(self.s3, "'십자 시험'은 원점에 작은 십자(±3 단위)를 그려요. 달걀 가운데에 오는지 눈으로 확인하세요.")

        s5 = self.section("원점 저장 · 복원 (전원을 껐다 켜도 이어서)")
        self.note(s5, ORIGIN_NOTE, fg="#475569")
        tk.Checkbutton(s5, text="원점을 기계(G54)에도 저장  (G92.1 → G10 L20 P1 X0 Y0 → G54)", variable=self.v_persist,
                       command=self._mode, bg=C_PANEL, font=app.font, activebackground=C_PANEL).pack(anchor="w", padx=8)
        r = tk.Frame(s5, bg=C_PANEL)
        r.pack(fill="x", padx=8, pady=6)
        app._btn(r, "원점 복귀 + 파킹 (전원 끄기 전)", self.park, pady=3).pack(side="left")
        app._btn(r, "저장된 원점 복원", self.run_restore, bg="#F59E0B", fg="white", bold=True, pady=3).pack(
            side="left", padx=6)
        self.lbl_saved = tk.Label(s5, text="", bg=C_PANEL, fg="#1D4ED8", font=app.font, anchor="w", justify="left")
        self.lbl_saved.pack(fill="x", padx=10, pady=(0, 6))

        s4 = self.section("보낼 G코드 미리보기")
        self.pre = tk.Text(s4, height=7, font=app.font_mono, bg="#0F172A", fg="#CBD5E1", relief="flat",
                           state="disabled")
        self.pre.pack(fill="x", padx=8, pady=6)
        self._mode()

    # ---- 단계 계획 --------------------------------------------------------------
    def plan(self):
        p = self.app.params
        up = f"M3 S{int(p.pen_up)}"
        rot = "X" if self.app.axes.x_is_rot else "Y"
        ht = "Y" if self.app.axes.x_is_rot else "X"
        steps = [dict(label="알람 해제 ($X)", line="$X"), dict(label="펜 올림", line=up)]
        if self.v_home.get():
            offs = {"X": p.home_off_x, "Y": p.home_off_y}
            steps += [dict(label="홈잉 ($H) - 기계가 스위치를 찾아가요", line="$H", timeout=120),
                      dict(label="상대 좌표로 전환 (G91)", line="G91"),
                      dict(label=f"달걀 원점으로 이동 (X{offs['X']:+g}, Y{offs['Y']:+g})",
                           line=f"G0 X{_f(offs['X'])} Y{_f(offs['Y'])} F{int(p.home_feed)}", timeout=90),
                      dict(label="절대 좌표로 복귀 (G90)", line="G90")]
            steps += self._origin_steps()
        else:
            steps += [dict(label="조그로 위치를 맞춘 뒤 아래 '여기가 원점' 을 누르세요", line=None, manual=True)]
        return steps

    def _origin_steps(self):
        if self.v_persist.get():
            return [dict(label="G92 임시 오프셋 지우기 (G92.1)", line="G92.1"),
                    dict(label="여기를 G54 원점으로 저장 (G10 L20 P1 X0 Y0) - 전원을 꺼도 기계에 남아요",
                         line="G10 L20 P1 X0 Y0"),
                    dict(label="G54 좌표계 선택", line="G54", mark=True, g54=True)]
        return [dict(label="여기를 작업 원점으로 설정 (G92 X0 Y0)", line="G92 X0 Y0", mark=True, g54=False)]

    def plan_restore(self):
        """저장된 원점을 다시 잡는 단계. 홈 스위치(+G54) > 파킹 > 직접 맞추기 순."""
        a = self.app
        p, o = a.params, a.origin
        up = f"M3 S{int(p.pen_up)}"
        steps = [dict(label="알람 해제 ($X)", line="$X"), dict(label="펜 올림", line=up)]
        if o.get("home_use") or p.home_use:
            steps.append(dict(label="홈잉 ($H) - 기계가 스위치를 찾아가요", line="$H", timeout=120))
            if o.get("g54"):
                steps += [dict(label="저장된 G54 원점 불러오기 (G54)", line="G54"),
                          dict(label="원점으로 이동 (G0 X0 Y0)", line=f"G0 X0 Y0 F{int(p.home_feed)}", timeout=90,
                               mark=True, g54=True)]
            else:
                steps += [dict(label="상대 좌표로 전환 (G91)", line="G91"),
                          dict(label=f"달걀 원점으로 이동 (X{p.home_off_x:+g}, Y{p.home_off_y:+g})",
                               line=f"G0 X{_f(p.home_off_x)} Y{_f(p.home_off_y)} F{int(p.home_feed)}", timeout=90),
                          dict(label="절대 좌표로 복귀 (G90)", line="G90"),
                          dict(label="여기를 작업 원점으로 설정 (G92 X0 Y0)", line="G92 X0 Y0", mark=True, g54=False)]
        elif o.get("parked"):
            steps.append(dict(label="파킹했던 자리를 원점으로 (G92 X0 Y0) - 전원 끈 사이 손으로 돌렸다면 십자 시험으로 확인",
                              line="G92 X0 Y0", mark=True, g54=False, parked=True))
        else:
            steps.append(dict(label="홈 스위치도 파킹도 없어서 자동 복원 불가 - 조그로 맞춘 뒤 '여기가 원점'을 누르세요",
                              line=None, manual=True))
        return steps

    def _mode(self):
        self.app.params.home_use = self.v_home.get()
        self.steps = self.plan()
        self.i, self.n_done = -1, 0
        self._render(None)
        self.pre.config(state="normal")
        self.pre.delete("1.0", "end")
        self.pre.insert("end", "\n".join(s["line"] for s in self.steps if s.get("line")) + "\n")
        self.pre.config(state="disabled")
        self.lbl_saved.config(text="저장된 원점: " + self.app.origin_summary())

    def _render(self, fail):
        t = self.lst
        t.config(state="normal")
        t.delete("1.0", "end")
        for k, st in enumerate(self.steps):
            if fail == k:
                t.insert("end", f"✖ {k + 1}. {st['label']}\n", "fail")
            elif k < self.n_done:
                t.insert("end", f"✔ {k + 1}. {st['label']}\n", "done")
            elif k == self.n_done and (self.running or st.get("manual")) and self.i >= 0:
                t.insert("end", f"▶ {k + 1}. {st['label']}\n", "now")
            else:
                t.insert("end", f"○ {k + 1}. {st['label']}\n")
        t.config(state="disabled")

    # ---- 실행 -----------------------------------------------------------------
    def _idle(self):
        st = self.app.grbl.status or {}
        return st.get("t", 0.0) > self.sent_t + 0.7 and str(st.get("state", "")).lower().startswith("idle")

    def run(self):
        a = self.app
        if not a.grbl.connected:
            messagebox.showinfo("기계 연결 필요", "먼저 메인 창에서 기계에 연결하세요.\n(연결 없이도 위의 G코드 미리보기로 순서를 확인할 수 있어요.)",
                                parent=self)
            return
        if self.v_home.get() and not messagebox.askokcancel(
                "홈잉", "기계가 홈 스위치를 찾아 움직입니다. 달걀과 펜이 걸리지 않게 확인했나요?", parent=self):
            return
        a.grbl.clear_alarm()
        self.steps = self.plan()
        self.i, self.n_done = -1, 0
        self.running = True
        self._next()

    def run_restore(self):
        a = self.app
        if not a.origin.get("saved"):
            messagebox.showinfo("저장된 원점 없음", "아직 저장된 원점이 없어요. 먼저 원점을 맞추면 자동으로 저장돼요.", parent=self)
            return
        self.steps = self.plan_restore()
        self.i, self.n_done = -1, 0
        self._render(None)
        if not a.grbl.connected:
            messagebox.showinfo("기계 연결 필요", "먼저 메인 창에서 기계에 연결하세요.\n(위 '진행 단계'에서 복원 순서를 미리 볼 수 있어요.)",
                                parent=self)
            return
        if not messagebox.askokcancel("원점 복원", "저장된 원점을 다시 잡습니다.\n" + a.origin_summary()
                                    + "\n\n달걀과 펜이 걸리지 않게 확인했나요?", parent=self):
            return
        a.grbl.clear_alarm()
        self.running = True
        if a.grbl.in_alarm() or a.grbl.alarm:
            self.lbl_state.config(text="알람 자동 해제 중...", fg=C_SUB)
            a._queue_prepare(lambda ok: self._next() if ok and self.winfo_exists() else None)
            return
        self._next()

    def park(self):
        a, p = self.app, self.app.params
        if not a.grbl.connected:
            messagebox.showinfo("기계 연결 필요", "먼저 기계에 연결하세요.", parent=self)
            return
        for ln in (f"M3 S{int(p.pen_up)}", f"G0 X0 Y0 F{int(p.travel_feed)}"):
            if not a.grbl.send(ln):
                return
        self.sent_t = time.time()
        self.lbl_state.config(text="원점으로 복귀 중...", fg=C_SUB)
        self.after(400, lambda: self._park_poll(0))

    def _park_poll(self, n):
        if not self.winfo_exists():
            return
        if self._idle():
            self.app.origin.update(parked=True, saved=True, home_use=bool(self.app.params.home_use))
            self.app.origin.setdefault("when", time.strftime("%Y-%m-%d %H:%M:%S"))
            self.app._autosave()
            self.lbl_state.config(text="파킹 완료 - 이제 전원을 꺼도 돼요", fg="#15803D")
            self.lbl_saved.config(text="저장된 원점: " + self.app.origin_summary())
            self.app.log("원점 복귀 + 파킹 완료. 전원을 꺼도 다음에 이 자리를 원점으로 복원할 수 있어요.")
        elif n > 200:
            self.lbl_state.config(text="파킹 시간 초과 - 기계 상태를 확인하세요", fg="#B91C1C")
        else:
            self.after(300, lambda: self._park_poll(n + 1))

    def stop(self):
        if self.running:
            self.running = False
            self.lbl_state.config(text="중지됨", fg="#B45309")
            self._render(None)

    def _next(self):
        if not self.running:
            return
        self.n_done = self.i + 1 if self.i >= 0 else 0
        self.i += 1
        if self.i >= len(self.steps):
            self.running = False
            self.i = len(self.steps) - 1
            self.lbl_state.config(text="완료", fg="#15803D")
            self._render(None)
            return
        st = self.steps[self.i]
        self._render(None)
        self.lbl_state.config(text=f"{self.i + 1}/{len(self.steps)} 진행 중...", fg=C_SUB)
        if st.get("manual"):                       # 사용자가 직접 맞추는 단계: 여기서 멈춤
            self.running = False
            self.lbl_state.config(text="조그로 맞춘 뒤 '여기가 원점'을 누르세요", fg="#1D4ED8")
            self._render(None)
            return
        if not self.app.grbl.send(st["line"]):
            self._fail("명령을 보낼 수 없어요 (그리는 중이거나 일시정지 중)")
            return
        self.sent_t = time.time()
        self.after(300, self._poll)

    def _poll(self):
        if not self.running or not self.winfo_exists():
            return
        st = self.steps[self.i]
        g = self.app.grbl
        if g.alarm:
            self._fail(g.alarm)
            return
        if self._idle():
            if st.get("mark"):
                self.app._mark_origin(g54=st.get("g54", self.v_persist.get()), parked=st.get("parked"))
                self.lbl_saved.config(text="저장된 원점: " + self.app.origin_summary())
                self.app.log("원점 자동 조정 완료 (" + (st["line"] if st.get("line") else "") + ")")
            self._next()
        elif time.time() - self.sent_t > st.get("timeout", 20):
            self._fail("시간 초과 - 기계 상태를 확인하세요")
        else:
            self.after(250, self._poll)

    def _fail(self, why):
        self.running = False
        self.lbl_state.config(text=f"실패: {why}", fg="#B91C1C")
        self.app.log(f"원점 조정 실패: {why}")
        self._render(self.i)

    # ---- 직접 맞추기 --------------------------------------------------------------
    def jog(self, rot_axis, sign):
        a = self.app
        try:
            step = float(self.v_step.get())
        except ValueError:
            return
        axis = ("X" if a.axes.x_is_rot else "Y") if rot_axis else ("Y" if a.axes.x_is_rot else "X")
        a.origin["parked"] = False                       # 움직였으니 파킹 상태는 해제
        for line in ("G91", f"G0 {axis}{sign * step:g} F1000", "G90"):
            a.manual(line)

    def set_here(self):
        a = self.app
        persist = self.v_persist.get()
        if a.set_origin(persist=persist):
            self.lbl_state.config(text="원점 설정 완료 (" + ("G54 저장" if persist else "G92 X0 Y0") + ")", fg="#15803D")
            self.lbl_saved.config(text="저장된 원점: " + a.origin_summary())
            a.log("현재 위치를 원점(0,0)으로 설정했어요." + (" (G54 에도 저장)" if persist else ""))
            if self.steps and self.steps[-1].get("manual"):
                self.i = self.n_done = len(self.steps)
                self._render(None)

    def cross_test(self):
        a, p = self.app, self.app.params
        d = 3.0
        lines = [f"M3 S{p.pen_up}", f"G0 X{-d:g} Y0 F1500", f"M3 S{p.pen_down}", "G4 P0.4",
                 f"G1 X{d:g} Y0 F800", f"M3 S{p.pen_up}", "G4 P0.3", f"G0 X0 Y{-d:g} F1500", f"M3 S{p.pen_down}",
                 "G4 P0.4", f"G1 X0 Y{d:g} F800", f"M3 S{p.pen_up}", "G4 P0.3", "G0 X0 Y0 F1500"]
        if not a.grbl.connected:
            messagebox.showinfo("기계 연결 필요", "먼저 기계에 연결하세요.", parent=self)
            return
        if not a.grbl.start(lines):
            a.log("십자 시험을 시작할 수 없어요.")


# -----------------------------------------------------------------------------
# 펜 교환 설정
# -----------------------------------------------------------------------------
class AtcDialog(ToolWindow):
    def __init__(self, app):
        super().__init__(app, "펜 교환(ATC) 설정", 640, 960)
        p = app.params
        s1 = self.section("교환 방식 / 매거진 축")
        r = tk.Frame(s1, bg=C_PANEL)
        r.pack(fill="x", padx=8, pady=6)
        tk.Label(r, text="방식", bg=C_PANEL, font=app.font).pack(side="left")
        self.v_mode = tk.StringVar(value=p.change_mode)
        cb = ttk.Combobox(r, textvariable=self.v_mode, values=list(CHANGE_MODES), state="readonly", width=18)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", self._save)
        tk.Label(r, text="매거진 축", bg=C_PANEL, font=app.font).pack(side="left", padx=(12, 0))
        self.v_axis = tk.StringVar(value=p.turret_axis)
        cb2 = ttk.Combobox(r, textvariable=self.v_axis, values=["Z", "A", "B", "C"], width=4)
        cb2.pack(side="left", padx=4)
        cb2.bind("<<ComboboxSelected>>", self._save)
        cb2.bind("<FocusOut>", self._save)
        tk.Label(r, text="ATC 접근 축", bg=C_PANEL, font=app.font).pack(side="left", padx=(12, 0))
        self.v_atcax = tk.StringVar(value=p.atc_axis)
        cb3 = ttk.Combobox(r, textvariable=self.v_atcax, values=["E", "A", "B", "C", "Z"], width=4)
        cb3.pack(side="left", padx=4)
        cb3.bind("<<ComboboxSelected>>", self._save)
        cb3.bind("<FocusOut>", self._save)
        self.note(s1, "Z = 회전 턴테이블(펜 3슬롯, 단위: 도) / A = ATC 직선 접근(오른쪽 레일, 단위: mm). "
                      "'수동 교환'은 M0 으로 멈추니 펜을 직접 바꾼 뒤 '재개'를 누르세요.")
        self.note(s1, "⚠ 이 두 축까지 쓰려면 5축 이상 펌웨어(RAMPS+Marlin, grbl-Mega 5X, FluidNC 등)가 필요해요. "
                      "기본 GRBL 1.1 은 X/Y/Z 3축뿐이라 Z/A 명령에서 error 가 나요. 축 글자는 보드 설정에 맞게 바꾸세요.", fg="#B45309")

        s1b = self.section("ATC 위치 · 속도 (펜 교환 동작)")
        Slider(s1b, app, "ATC 대기 위치(mm)", "atc_park", -300, 300, 1,
               on_change=self.preview).pack(fill="x", padx=4)
        Slider(s1b, app, "ATC 도킹 위치(mm)", "atc_dock", -300, 300, 1,
               on_change=self.preview).pack(fill="x", padx=4)
        Slider(s1b, app, "ATC 이동 속도(mm/분)", "atc_feed", 50, 3000, 50,
               on_change=self.preview).pack(fill="x", padx=4)
        Slider(s1b, app, "펜 교환 위치(높이 mm)", "atc_pen_h", -80, 80, 0.5, "{:+.1f}",
               on_change=self.preview).pack(fill="x", padx=4)
        Slider(s1b, app, "슬롯 0번 각도 오프셋(도)", "turret_off", -180, 180, 0.1, "{:.1f}",
               on_change=self.preview).pack(fill="x", padx=4)
        self.v_follow2 = tk.BooleanVar(value=p.outline_follow)
        self.v_dauto2 = tk.BooleanVar(value=p.dimple_auto)
        tk.Checkbutton(s1b, text="테두리도 그 영역을 칠한 색 펜으로", variable=self.v_follow2, command=self._save,
                       bg=C_PANEL, font=app.font, activebackground=C_PANEL).pack(anchor="w", padx=6)
        tk.Checkbutton(s1b, text="딤플 보정 자동 (골프공: 딤플 안·가장자리 감속/압력/겹침)", variable=self.v_dauto2,
                       command=self._save, bg=C_PANEL, font=app.font, activebackground=C_PANEL).pack(anchor="w", padx=6)
        self.note(s1b, "교환 순서: 펜 캐리지가 교환 위치로 → 턴테이블이 빈 슬롯을 돌려 맞춤 → ATC 가 접근(도킹)해 펜을 뽑아 슬롯에 꽂음 → "
                       "새 펜 슬롯을 돌려 맞춤 → 다시 접근해 홀더에 끼움 → 복귀. 아래 '교환 시험'에서 G코드를 확인하세요.")

        s2 = self.section("교환 매크로 (직접 G코드 추가)")
        self.note(s2, "{from} 이전 펜 번호, {to} 새 펜 번호, {angle} 새 펜 매거진 각도.  예) 펜을 도킹하는 동작을 넣을 수 있어요.")
        tk.Label(s2, text="교환 직전에 실행", bg=C_PANEL, font=app.font).pack(anchor="w", padx=8)
        self.t_pre = tk.Text(s2, height=3, font=app.font_mono, bg="#F8FAFC", relief="solid", bd=1)
        self.t_pre.pack(fill="x", padx=8, pady=(0, 4))
        self.t_pre.insert("1.0", p.change_pre)
        tk.Label(s2, text="교환 직후에 실행", bg=C_PANEL, font=app.font).pack(anchor="w", padx=8)
        self.t_post = tk.Text(s2, height=3, font=app.font_mono, bg="#F8FAFC", relief="solid", bd=1)
        self.t_post.pack(fill="x", padx=8, pady=(0, 6))
        self.t_post.insert("1.0", p.change_post)
        for t in (self.t_pre, self.t_post):
            t.bind("<KeyRelease>", self._save)

        s3 = self.section("교환 시험")
        r = tk.Frame(s3, bg=C_PANEL)
        r.pack(fill="x", padx=8, pady=6)
        self.v_from, self.v_to = tk.IntVar(value=1), tk.IntVar(value=min(2, p.npen()))
        tk.Label(r, text="펜", bg=C_PANEL, font=app.font).pack(side="left")
        tk.Spinbox(r, from_=1, to=6, width=3, textvariable=self.v_from, font=app.font,
                   command=self.preview).pack(side="left", padx=3)
        tk.Label(r, text="번  →", bg=C_PANEL, font=app.font).pack(side="left")
        tk.Spinbox(r, from_=1, to=6, width=3, textvariable=self.v_to, font=app.font,
                   command=self.preview).pack(side="left", padx=3)
        tk.Label(r, text="번", bg=C_PANEL, font=app.font).pack(side="left")
        app._btn(r, "G코드 보기", self.preview, pady=2).pack(side="left", padx=(10, 2))
        app._btn(r, "기계로 보내 시험", self.send, bg="#10B981", fg="white", bold=True, pady=2).pack(side="left", padx=2)
        app._btn(r, "3D 창에서 보기", self.app.open_preview, pady=2).pack(side="left", padx=2)
        self.out = tk.Text(s3, height=9, font=app.font_mono, bg="#0F172A", fg="#CBD5E1", relief="flat", state="disabled")
        self.out.pack(fill="x", padx=8, pady=(0, 8))
        self.preview()

    def _save(self, _e=None):
        p = self.app.params
        p.change_mode = self.v_mode.get()
        ax = self.v_axis.get().strip().upper()
        p.turret_axis = ax if ax else "Z"
        ax2 = self.v_atcax.get().strip().upper()
        p.atc_axis = ax2 if ax2 else "A"
        p.outline_follow, p.dimple_auto = self.v_follow2.get(), self.v_dauto2.get()
        self.app.sync_controls()
        p.change_pre = self.t_pre.get("1.0", "end").strip("\n")
        p.change_post = self.t_post.get("1.0", "end").strip("\n")
        self.app.v_chgmode.set(p.change_mode)
        self.app.schedule_recompute()
        self.preview()

    def _lines(self):
        app = self.app
        p, ax = app.params, app.axes
        try:
            pa = max(0, min(p.npen() - 1, int(self.v_from.get()) - 1))
            pb = max(0, min(p.npen() - 1, int(self.v_to.get()) - 1))
        except (tk.TclError, ValueError):
            pa, pb = 0, 0

        def xy(deg, h):
            d = deg * p.cal_rot_scale + p.cal_rot_off
            v = arm_out(h * p.cal_h_scale + p.cal_h_off, p)
            X, Y = (d, v) if ax.x_is_rot else (v, d)
            return (-X if ax.x_invert else X), (-Y if ax.y_invert else Y)

        lines, _tur = change_lines(p, pa, pb, f"M3 S{int(p.pen_up)}", p.pos_x_deg, turret_start(p, pa), xy)
        return lines

    def preview(self):
        self.out.config(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("end", "\n".join(self._lines()) + "\n")
        self.out.config(state="disabled")

    def refresh_from_params(self):
        """다른 창(ATC 메커니즘 설정/메크로 시험 등)에서 값을 바꾼 뒤에도 이 창의 콤보/체크박스와
        '교환 시험' 미리보기 코드를 최신 설정값으로 새로고침한다. (App.sync_controls 가 호출)"""
        p = self.app.params
        self.v_mode.set(p.change_mode)
        self.v_axis.set(p.turret_axis)
        self.v_atcax.set(p.atc_axis)
        self.v_follow2.set(p.outline_follow)
        self.v_dauto2.set(p.dimple_auto)
        self.preview()

    def send(self):
        a = self.app
        if not a.grbl.connected:
            messagebox.showinfo("기계 연결 필요", "먼저 기계에 연결하세요.", parent=self)
            return
        lines = [l for l in self._lines() if not l.startswith(";")]
        if messagebox.askokcancel("교환 시험", f"{len(lines)}줄을 보내 펜 교환 동작을 시험합니다.\n"
                                             "달걀과 펜 매거진 주변이 비어 있나요?", parent=self):
            if not a.grbl.start(lines):
                a.log("교환 시험을 시작할 수 없어요.")


# -----------------------------------------------------------------------------
# G코드 확인 · 수정
class AtcSettingsDialog(ToolWindow):
    """ATC 메커니즘 설정 창. 값을 고치고 [저장] 을 누르면 설정 파일에 기록되어 프로그램을 껐다 켜도,
    '새로 시작'을 해도 그대로 유지된다. (프로젝트 파일과 별개)"""
    # (속성, 이름, 종류, 설명)   종류: str / int / float / bool / choice:a|b / text
    GROUPS = (
        ("교환 위치 / 순서", (
            ("exch_at_origin", "원점(펜 팔 0도)에서 교환", "bool",
             "켜면 펜 교환 때 항상 X0 Y0(기계 원점)로 가서 바꿉니다. 펜 팔이 기울지 않아요."),
            ("return_first_pen", "다 그리면 처음 펜으로 되돌려 교체", "bool",
             "켜면 마지막 색까지 다 그린 뒤, 시작 펜(보통 1번)으로 다시 교체해 두고 끝냅니다."),
            ("pen_order_area", "면적 큰 색부터 그리기", "bool",
             "끄면 1번 색 → 2번 색 → 3번 색 순서로, 한 색을 다 끝낸 뒤 다음 색으로 넘어갑니다."),
            ("atc_pen_h", "교환 높이 mm (원점 교환 끄면 사용)", "float", "원점 교환을 껐을 때 펜 캐리지가 가는 위치(달걀 축 방향 mm)"),
        )),
        ("교환 방식 / 축", (
            ("change_mode", "방식", "choice:" + "|".join(CHANGE_MODES), ""),
            ("turret_axis", "매거진(턴테이블) 축", "choice:Z|A|B|C", "펜 슬롯을 돌리는 축"),
            ("atc_axis", "ATC 접근 축", "choice:E|A|B|C|Z", "펜 홀더에 다가가는 직선 축"),
        )),
        ("매거진(회전)", (
            ("turret_step", "슬롯 간 각도 (도)", "float", "0 = 360/펜 개수"),
            ("turret_off", "회전 오프셋 (도)", "float", ""),
            ("turret_feed", "회전 속도 (도/분)", "int", ""),
            ("mech_half", "접근 보정각 H (도)", "float", "0 = 슬롯 간 각도 / 2"),
            ("mech_home", "홈(대기) 각도 (도)", "float", ""),
        )),
        ("ATC 직선 이동", (
            ("atc_park", "대기(오른쪽) 위치 mm", "float", ""),
            ("atc_dock", "도킹 위치 mm", "float", "펜 홀더에 닿는 위치"),
            ("atc_feed", "이동 속도 (mm/분)", "int", ""),
            ("change_wait_ms", "교환 대기 (ms)", "int", "각 동작 사이 멈춤 시간"),
        )),
        ("스크립트 / 추가 G코드", (
            ("mech_use", "메커니즘 스크립트 사용", "bool", "끄면 기본 교환 순서를 씁니다."),
            ("change_pre", "교환 직전 G코드", "text", "{from} {to} {angle} 사용 가능"),
            ("change_post", "교환 직후 G코드", "text", ""),
            ("mech_script", "메커니즘 스크립트", "script", "세부 편집은 '교환 설정·매크로·시험…' 창에서도 가능"),
        )),
    )

    def __init__(self, app):
        super().__init__(app, "ATC 메커니즘 설정", 620, 780)
        self.vars = {}
        self.texts = {}
        self.dirty = False
        outer = tk.Frame(self, bg=C_PANEL)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=C_PANEL, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas, bg=C_PANEL)
        wid = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(wid, width=e.width))
        p = app.params
        for title, items in self.GROUPS:
            lf = tk.LabelFrame(body, text=f" {title} ", bg=C_PANEL, fg=C_TEXT, font=app.font_b)
            lf.pack(fill="x", padx=12, pady=(10, 0))
            for attr, label, kind, tip in items:
                self._row(lf, p, attr, label, kind, tip)
        bar = tk.Frame(self, bg=C_PANEL)
        bar.pack(fill="x", padx=12, pady=8)
        app._btn(bar, "💾 저장", self.save, bg="#2563EB", fg="white", bold=True, padx=14, pady=4).pack(side="left")
        app._btn(bar, "되돌리기", self.reload_from_params, padx=10, pady=4).pack(side="left", padx=6)
        app._btn(bar, "기본값", self.defaults, padx=10, pady=4).pack(side="left")
        self.lbl = tk.Label(bar, text="", bg=C_PANEL, fg=C_SUB, font=app.font_s, anchor="e")
        self.lbl.pack(side="right")
        self._status_from_file()
        self.dirty = False

    def _row(self, parent, p, attr, label, kind, tip):
        app = self.app
        r = tk.Frame(parent, bg=C_PANEL)
        r.pack(fill="x", padx=8, pady=(4, 0))
        cur = getattr(p, attr)
        if kind == "bool":
            v = tk.BooleanVar(value=bool(cur))
            tk.Checkbutton(r, text=label, variable=v, bg=C_PANEL, font=app.font, anchor="w",
                           command=self._mark).pack(side="left")
        elif kind in ("text", "script"):
            tk.Label(r, text=label, bg=C_PANEL, fg=C_TEXT, font=app.font, anchor="w").pack(side="top", fill="x")
            h = 8 if kind == "script" else 2
            t = tk.Text(r, height=h, font=app.font_mono, wrap="none", undo=True)
            t.insert("1.0", str(cur))
            t.pack(fill="x", pady=(0, 2))
            t.bind("<<Modified>>", lambda e, w=t: (self._mark() if w.edit_modified() else None, w.edit_modified(False)))
            self.texts[attr] = t
            v = None
        elif kind.startswith("choice:"):
            tk.Label(r, text=label, bg=C_PANEL, fg=C_TEXT, font=app.font, anchor="w", width=26).pack(side="left")
            v = tk.StringVar(value=str(cur))
            cb = ttk.Combobox(r, textvariable=v, values=kind.split(":", 1)[1].split("|"), width=22)
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda e: self._mark())
            v.trace_add("write", lambda *a: self._mark())
        else:
            tk.Label(r, text=label, bg=C_PANEL, fg=C_TEXT, font=app.font, anchor="w", width=26).pack(side="left")
            v = tk.StringVar(value=str(cur))
            tk.Entry(r, textvariable=v, width=12).pack(side="left")
            v.trace_add("write", lambda *a: self._mark())
        if v is not None:
            self.vars[attr] = (v, kind)
        if tip:
            tk.Label(parent, text=tip, bg=C_PANEL, fg="#94A3B8", font=app.font_s, justify="left", anchor="w",
                     wraplength=540).pack(fill="x", padx=12)

    def _mark(self):
        self.dirty = True
        self.lbl.config(text="● 저장 안 됨", fg="#DC2626")

    def _status_from_file(self):
        d = load_atc_settings()
        self.lbl.config(text=f"저장됨: {d['_saved']}" if d.get("_saved") else "아직 저장 안 함", fg=C_SUB)

    def _collect(self):
        """입력값 -> dict. 잘못된 값이 있으면 ValueError(항목 이름 포함)."""
        out = {}
        for attr, (v, kind) in self.vars.items():
            raw = v.get()
            try:
                if kind == "bool":
                    out[attr] = bool(raw)
                elif kind == "int":
                    out[attr] = int(float(str(raw).strip()))
                elif kind == "float":
                    out[attr] = float(str(raw).strip())
                else:
                    out[attr] = str(raw).strip()
            except ValueError:
                raise ValueError(f"'{attr}' 값이 올바르지 않아요: {raw!r}")
        for attr, t in self.texts.items():
            out[attr] = t.get("1.0", "end-1c")
        if out.get("change_mode") not in CHANGE_MODES:
            raise ValueError("교환 방식을 목록에서 골라 주세요.")
        for k in ("turret_axis", "atc_axis"):
            if k in out:
                out[k] = out[k].upper()
                if len(out[k]) != 1 or not out[k].isalpha():
                    raise ValueError(f"축 글자는 한 글자(예: Z, E)여야 해요: {k}")
        for k in ("turret_feed", "atc_feed"):
            if out.get(k, 1) <= 0:
                raise ValueError("이동 속도는 0보다 커야 해요.")
        if out.get("change_wait_ms", 0) < 0:
            raise ValueError("교환 대기 시간은 0 이상이어야 해요.")
        return out

    def apply_to_app(self, vals):
        self.app._apply_values(self.app.params, vals)
        self.app.schedule_recompute()
        try:
            self.app.sync_controls()
        except Exception:
            pass

    def save(self):
        try:
            vals = self._collect()
        except ValueError as e:
            messagebox.showwarning("ATC 설정", str(e), parent=self)
            return False
        self.apply_to_app(vals)
        try:
            path = save_atc_settings(self.app.params)
        except OSError as e:
            messagebox.showerror("ATC 설정", f"저장하지 못했어요: {e}", parent=self)
            return False
        self.dirty = False
        self._status_from_file()
        self.app.log(f"ATC 설정을 저장했어요 → {path}")
        return True

    def reload_from_params(self):
        p = self.app.params
        for attr, (v, kind) in self.vars.items():
            v.set(bool(getattr(p, attr)) if kind == "bool" else str(getattr(p, attr)))
        for attr, t in self.texts.items():
            t.delete("1.0", "end")
            t.insert("1.0", str(getattr(p, attr)))
        self.dirty = False
        self._status_from_file()

    def defaults(self):
        if not messagebox.askyesno("ATC 설정", "모든 항목을 기본값으로 되돌릴까요? (저장 버튼을 눌러야 파일에 반영돼요)", parent=self):
            return
        d = Params()
        for attr, (v, kind) in self.vars.items():
            v.set(bool(getattr(d, attr)) if kind == "bool" else str(getattr(d, attr)))
        for attr, t in self.texts.items():
            t.delete("1.0", "end")
            t.insert("1.0", str(getattr(d, attr)))
        self._mark()

    def close(self):
        if self.dirty:
            a = messagebox.askyesnocancel("ATC 설정", "저장하지 않은 변경이 있어요. 저장할까요?", parent=self)
            if a is None:
                return
            if a and not self.save():
                return
        self.destroy()


class MechDialog(ToolWindow):
    """펜 교환 메커니즘 수정창: 추가된 교환 동작(Z 회전 + E 접근)만 보여 주고, 적용하면 G코드·3D에 바로 반영."""

    FIELDS = (("슬롯 간 각도 (도, 0=자동)", "turret_step"),
              ("접근 보정각 H (도, 0=슬롯간각÷2)", "mech_half"),
              ("홈(대기) 각도 (도)", "mech_home"),
              ("슬롯 0번(1번 펜) 각도 (도)", "turret_off"))

    def __init__(self, app):
        super().__init__(app, "펜 교환 메커니즘 수정", 720, 940)
        p = app.params
        self._job = None
        s1 = self.section("기본 값 (소수점 가능)")
        self.v_use = tk.BooleanVar(value=p.mech_use)
        tk.Checkbutton(s1, text="이 메커니즘으로 펜 교환 (끄면 예전 방식)", variable=self.v_use, command=self._changed,
                       bg=C_PANEL, font=app.font, activebackground=C_PANEL).pack(anchor="w", padx=6)
        grid = tk.Frame(s1, bg=C_PANEL)
        grid.pack(fill="x", padx=8, pady=4)
        self.vars = {}
        for i, (label, attr) in enumerate(self.FIELDS):
            tk.Label(grid, text=label, bg=C_PANEL, font=app.font).grid(row=i, column=0, sticky="w", pady=2)
            v = tk.StringVar(value=f"{getattr(p, attr):g}")
            e = tk.Entry(grid, textvariable=v, width=10, font=app.font_mono, relief="solid", bd=1)
            e.grid(row=i, column=1, padx=8)
            e.bind("<KeyRelease>", self._changed)
            self.vars[attr] = v
        self.lbl_slots = tk.Label(s1, text="", bg=C_PANEL, fg=C_TEXT, font=app.font_b, anchor="w", justify="left")
        self.lbl_slots.pack(fill="x", padx=10, pady=(0, 6))

        s2 = self.section("교환 순서 (스크립트)")
        self.t_script = tk.Text(s2, height=17, font=app.font_mono, wrap="none", relief="solid", bd=1, undo=True)
        self.t_script.pack(fill="x", padx=8, pady=6)
        self.t_script.insert("1.0", p.mech_script)
        self.t_script.bind("<KeyRelease>", self._changed)
        self.note(s2, "한 줄에 한 동작. ; 뒤는 설명. 수식 가능(예: turret A+H, atc DOCK F 800). "
                      "펜별로 다르게 하려면  when PA==2 and PB==3: turret B-H  처럼 조건을 앞에 붙이세요.")
        bf = tk.Frame(s2, bg=C_PANEL)
        bf.pack(fill="x", padx=8, pady=(0, 8))
        app._btn(bf, "적용 (G코드·3D 즉시 반영)", self.apply, bg="#16A34A", bold=True, pady=4).pack(side="left")
        app._btn(bf, "기본 순서로 복원", self.reset, pady=4).pack(side="left", padx=6)
        self.v_auto = tk.BooleanVar(value=False)
        tk.Checkbutton(bf, text="입력하는 대로 자동 적용", variable=self.v_auto, bg=C_PANEL, font=app.font,
                       activebackground=C_PANEL).pack(side="left", padx=8)

        s3 = self.section("시험 (적용 전에도 미리 보임)")
        r = tk.Frame(s3, bg=C_PANEL)
        r.pack(fill="x", padx=8, pady=4)
        tk.Label(r, text="펜", bg=C_PANEL, font=app.font).pack(side="left")
        self.v_from, self.v_to = tk.StringVar(value="1"), tk.StringVar(value="2")
        for v in (self.v_from, self.v_to):
            sp = tk.Spinbox(r, from_=1, to=max(1, p.npen()), width=3, textvariable=v, command=self._refresh,
                            font=app.font)
            sp.pack(side="left", padx=3)
            sp.bind("<KeyRelease>", self._refresh)
            if v is self.v_from:
                tk.Label(r, text="번  →", bg=C_PANEL, font=app.font).pack(side="left")
        tk.Label(r, text="번 으로 교환", bg=C_PANEL, font=app.font).pack(side="left")
        self.t_warn = tk.Label(s3, text="", bg=C_PANEL, fg="#DC2626", font=app.font, justify="left", anchor="w",
                               wraplength=640)
        self.t_warn.pack(fill="x", padx=8)
        self.out = tk.Text(s3, height=15, font=app.font_mono, bg="#0F172A", fg="#E2E8F0", relief="flat")
        self.out.pack(fill="both", expand=True, padx=8, pady=6)
        self._refresh()

    # ---------------------------------------------------------------- 값 읽기
    def _read(self):
        vals = {}
        for label, attr in self.FIELDS:
            txt = self.vars[attr].get().strip().replace(",", ".")
            try:
                vals[attr] = float(txt) if txt not in ("", "-", ".") else 0.0
            except ValueError:
                raise ValueError(f"'{label}' 에 숫자를 넣어 주세요.")
        if vals["turret_step"] < 0 or vals["mech_half"] < 0:
            raise ValueError("슬롯 간 각도/보정각은 0 이상이어야 해요.")
        return vals

    def _trial(self):
        vals = self._read()
        return dc_replace(self.app.params, mech_use=True, mech_script=self.t_script.get("1.0", "end").rstrip("\n"),
                          **vals)

    def _changed(self, _e=None):
        self._refresh()
        if self.v_auto.get():
            if self._job:
                self.after_cancel(self._job)
            self._job = self.after(500, lambda: self.apply(quiet=True))

    def _pair(self, n):
        try:
            a = max(0, min(n - 1, int(self.v_from.get()) - 1))
            b = max(0, min(n - 1, int(self.v_to.get()) - 1))
        except ValueError:
            a, b = 0, min(1, n - 1)
        return a, b

    @staticmethod
    def _brief(p, steps):
        out = []
        for st in steps:
            if st[0] == "turret":
                out.append(f"{p.turret_axis} {st[1]:g}°")
            elif st[0] == "atc":
                out.append(f"{p.atc_axis} {'접근' if st[1] > p.atc_park + 0.5 else '후진'}")
        return " → ".join(out)

    def _refresh(self, _e=None):
        app = self.app
        try:
            tp = self._trial()
        except ValueError as e:
            self.t_warn.config(text=str(e))
            return
        n = tp.npen()
        slots = "   /   ".join(f"{i + 1}번 {tp.turret_deg(i):g}°" for i in range(n))
        half = tp.mech_half if tp.mech_half > 0 else tp.step_deg() / 2.0
        self.lbl_slots.config(text=f"슬롯 각도:  {slots}      (H = {half:g}°, 홈 = {tp.mech_home:g}°)")
        a, b = self._pair(n)
        txt = []
        _s, warns, _t = mech_compile(tp, a, b, tp.pos_x_deg)
        ax = app.axes

        def xy(deg, h):
            d = deg * tp.cal_rot_scale + tp.cal_rot_off
            v = arm_out(h * tp.cal_h_scale + tp.cal_h_off, tp)
            X, Y = (d, v) if ax.x_is_rot else (v, d)
            return (-X if ax.x_invert else X), (-Y if ax.y_invert else Y)

        if a == b:
            txt.append("; 같은 펜끼리는 교환하지 않아요. 다른 번호를 고르세요.")
        else:
            lines, _tur = change_lines(tp, a, b, f"M3 S{int(tp.pen_up)}", tp.pos_x_deg, tp.mech_home, xy)
            txt += lines
        txt += ["", "; ---- 모든 교환 한눈에 ----"]
        for x in range(n):
            for y in range(n):
                if x != y:
                    st, _w, _t = mech_compile(tp, x, y, tp.pos_x_deg)
                    txt.append(f"; {x + 1}→{y + 1}:  {self._brief(tp, st)}")
        self.t_warn.config(text="\n".join(("⚠ " + w) for w in warns[:6]))
        self.out.config(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("end", "\n".join(txt) + "\n")
        self.out.config(state="disabled")

    def reset(self):
        self.t_script.delete("1.0", "end")
        self.t_script.insert("1.0", MECH_DEFAULT_SCRIPT)
        self._changed()

    def refresh_from_params(self):
        """다른 창(ATC 설정 등)에서 atc_park/atc_dock/축 같은 값을 바꾼 뒤에도
        이 창을 열어 둔 채 '시험' 미리보기를 최신 값으로 새로고침한다. (App.sync_controls 가 호출)
        여기 입력창(슬롯 각도/보정각/홈각/스크립트)에 입력 중인 값은 그대로 두고 건드리지 않는다."""
        self._refresh()

    def apply(self, quiet=False):
        app = self.app
        try:
            tp = self._trial()
        except ValueError as e:
            messagebox.showwarning("메커니즘", str(e), parent=self)
            return
        p = app.params
        if getattr(app, "gcode_edited", False) and not quiet:
            if not messagebox.askyesno("메커니즘", "편집한 G코드가 있어요. 적용하면 자동 생성 G코드로 대체됩니다. 계속할까요?",
                                       parent=self):
                return
        p.mech_use = bool(self.v_use.get())
        p.mech_script = tp.mech_script
        for _label, attr in self.FIELDS:
            setattr(p, attr, getattr(tp, attr))
        app.sync_controls()
        app.recompute()
        app.log("펜 교환 메커니즘을 적용했어요. G코드·3D에 반영됐습니다.")
        self._refresh()


# -----------------------------------------------------------------------------
class GcodeEditor(ToolWindow):
    """자동 생성된 G코드를 보고 고친 뒤 '3D에 적용'하면 미리보기·저장·기계 전송에 그대로 쓴다."""

    def __init__(self, app):
        super().__init__(app, "G코드 확인·수정", 800, 780)
        self.dirty = False
        self._loading = False
        self._find_pos = "1.0"
        self._nlines = 0
        top = tk.Frame(self, bg=C_PANEL)
        top.pack(fill="x", padx=12, pady=(10, 2))
        self.lbl_sum = tk.Label(top, text="", bg=C_PANEL, fg=C_TEXT, font=app.font_b, anchor="w")
        self.lbl_sum.pack(side="left")
        self.lbl_state = tk.Label(top, text="", bg=C_PANEL, font=app.font_b, anchor="e")
        self.lbl_state.pack(side="right")
        bar = tk.Frame(self, bg=C_PANEL)
        bar.pack(fill="x", padx=12, pady=2)
        app._btn(bar, "3D에 적용", self.apply, bg=C_BLUE, fg="white", bold=True).pack(side="left")
        app._btn(bar, "자동 생성으로 되돌리기", self.revert).pack(side="left", padx=6)
        app._btn(bar, "G코드 저장", self.save).pack(side="left")
        app._btn(bar, "복사", self.copy).pack(side="left", padx=6)
        app._btn(bar, "3D 창 열기", app.open_preview).pack(side="left")
        app._btn(bar, "펜 교환 메커니즘 수정", lambda: app.open_tool("mech"), bg="#F59E0B", fg="white",
                 bold=True).pack(side="left", padx=(6, 0))
        fr = tk.Frame(self, bg=C_PANEL)
        fr.pack(fill="x", padx=12, pady=(4, 2))
        tk.Label(fr, text="찾기", bg=C_PANEL, font=app.font).pack(side="left")
        self.v_find = tk.StringVar()
        e = tk.Entry(fr, textvariable=self.v_find, width=18, font=app.font_mono, relief="solid", bd=1)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda _e: self.find_next())
        app._btn(fr, "다음 찾기", self.find_next, pady=2).pack(side="left")
        tk.Label(fr, text="줄 이동", bg=C_PANEL, font=app.font).pack(side="left", padx=(14, 0))
        self.v_goto = tk.StringVar()
        g = tk.Entry(fr, textvariable=self.v_goto, width=7, font=app.font_mono, relief="solid", bd=1)
        g.pack(side="left", padx=4)
        g.bind("<Return>", lambda _e: self.goto())
        app._btn(fr, "이동", self.goto, pady=2).pack(side="left")
        app._btn(fr, "펜 교환 줄 찾기", lambda: self._search("펜 교환"), pady=2).pack(side="left", padx=(14, 0))

        self.banner = tk.Frame(self, bg="#FEF3C7")
        self.lbl_banner = tk.Label(self.banner, text="", bg="#FEF3C7", fg="#92400E", font=app.font, anchor="w",
                                   justify="left", wraplength=560)
        self.lbl_banner.pack(side="left", padx=8, pady=4)
        app._btn(self.banner, "자동 생성본 불러오기(편집 버림)", self.reload, bg="#FDE68A", pady=2).pack(side="right", padx=6)

        wf = tk.LabelFrame(self, text=" 적용 결과 / 경고 ", bg=C_PANEL, fg=C_TEXT, font=app.font_b)
        wf.pack(side="bottom", fill="x", padx=12, pady=(4, 10))
        self.warn = tk.Text(wf, height=4, font=app.font, bg="#F8FAFC", relief="flat", wrap="word", state="disabled")
        self.warn.pack(fill="x", padx=6, pady=4)
        self.warn.tag_configure("bad", foreground="#B91C1C")
        self.warn.tag_configure("good", foreground="#15803D")

        body = tk.Frame(self, bg=C_PANEL)
        body.pack(fill="both", expand=True, padx=12, pady=4)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        self.ln = tk.Text(body, width=6, font=app.font_mono, bg="#E2E8F0", fg="#64748B", relief="flat", bd=0,
                          state="disabled", takefocus=0, cursor="arrow", padx=4)
        self.ln.grid(row=0, column=0, sticky="ns")
        self.txt = tk.Text(body, font=app.font_mono, wrap="none", undo=True, bg="#0F172A", fg="#E2E8F0",
                           insertbackground="white", relief="flat", padx=6)
        self.txt.grid(row=0, column=1, sticky="nsew")
        vs = ttk.Scrollbar(body, orient="vertical", command=self._yview)
        vs.grid(row=0, column=2, sticky="ns")
        hs = ttk.Scrollbar(body, orient="horizontal", command=self.txt.xview)
        hs.grid(row=1, column=1, sticky="ew")
        self._vs = vs
        self.txt.config(yscrollcommand=self._on_yscroll, xscrollcommand=hs.set)
        for tag, kw in (("cmt", dict(foreground="#94A3B8")), ("pen", dict(foreground="#C4B5FD")),
                        ("atc", dict(foreground="#FDBA74")), ("rapid", dict(foreground="#7DD3FC")),
                        ("sys", dict(foreground="#FCA5A5")), ("hit", dict(background="#FACC15", foreground="black"))):
            self.txt.tag_configure(tag, **kw)
        self.txt.tag_raise("hit")
        self.txt.bind("<<Modified>>", self._changed)
        self.load(app.gcode)

    # ---- 스크롤/줄 번호 -------------------------------------------------------
    def _yview(self, *a):
        self.txt.yview(*a)
        self.ln.yview(*a)

    def _on_yscroll(self, first, last):
        self._vs.set(first, last)
        self.ln.yview_moveto(first)

    def _update_numbers(self):
        n = int(self.txt.index("end-1c").split(".")[0])
        if n == self._nlines:
            return
        self._nlines = n
        self.ln.config(state="normal")
        self.ln.delete("1.0", "end")
        self.ln.insert("1.0", "\n".join(str(i) for i in range(1, n + 1)))
        self.ln.config(state="disabled")
        self.ln.yview_moveto(self.txt.yview()[0])

    def _highlight(self):
        t = self.txt
        for tag in ("cmt", "pen", "atc", "rapid", "sys"):
            t.tag_remove(tag, "1.0", "end")
        p = self.app.params
        axes = {p.turret_axis.strip().upper(), p.atc_axis.strip().upper()} - {"X", "Y"}
        for i, raw in enumerate(t.get("1.0", "end-1c").split("\n"), 1):
            ln = raw.strip()
            tag = None
            if ln.startswith(";"):
                tag = "cmt"
            elif ln.startswith(("M3", "M0", "M2", "M5")):
                tag = "pen"
            elif ln.startswith(("$", "G92", "G10", "G54")):
                tag = "sys"
            elif ln.startswith("G0") and any(re.search(r"\b" + a + r"-?\d", ln) for a in axes):
                tag = "atc"
            elif ln.startswith("G0"):
                tag = "rapid"
            if tag:
                t.tag_add(tag, f"{i}.0", f"{i}.end")

    # ---- 읽기/상태 ---------------------------------------------------------------
    def load(self, lines):
        self._loading = True
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", "\n".join(lines))
        self.txt.edit_reset()
        self.txt.edit_modified(False)
        self._loading = False
        self.dirty = False
        self._nlines = 0
        self._update_numbers()
        self._highlight()
        self._find_pos = "1.0"
        self.banner.pack_forget()
        self.update_summary()
        self._update_state()

    def reload(self):
        self.load(self.app.gcode)

    def update_summary(self):
        st = self.app.stats or {}
        n = int(self.txt.index("end-1c").split(".")[0]) if self.txt.get("1.0", "end-1c").strip() else 0
        self.lbl_sum.config(text=f"{n}줄 · 예상 약 {st.get('minutes', 0):.1f}분 · 선 {st.get('n_strokes', 0)}개 · "
                                 f"펜 교환 {st.get('n_changes', 0)}회")

    def _update_state(self):
        if self.dirty:
            self.lbl_state.config(text="● 적용 안 한 변경이 있어요", fg="#B45309")
            self.title("G코드 확인·수정 *")
        elif self.app.gcode_edited:
            self.lbl_state.config(text="✎ 편집본 사용 중", fg=C_BLUE)
            self.title("G코드 확인·수정")
        else:
            self.lbl_state.config(text="자동 생성 G코드", fg=C_SUB)
            self.title("G코드 확인·수정")

    def _changed(self, _e=None):
        if self._loading or not self.txt.edit_modified():
            return
        self.txt.edit_modified(False)
        self.dirty = True
        self._update_numbers()
        self._update_state()

    def on_app_changed(self):
        """앱이 G코드를 다시 만들었을 때(설정 변경 등)."""
        if not self.dirty:
            self.load(self.app.gcode)
        else:
            self.lbl_banner.config(text="설정이 바뀌어 자동 생성 G코드가 달라졌어요. 지금 편집 내용은 그대로 두었어요.")
            self.banner.pack(fill="x", padx=12, pady=2, before=self.warn.master)
            self._update_state()

    # ---- 동작 --------------------------------------------------------------------
    def _show_warns(self, lines, good=False):
        w = self.warn
        w.config(state="normal")
        w.delete("1.0", "end")
        for ln in lines:
            w.insert("end", ("✔ " if good else "▲ ") + ln + "\n", "good" if good else "bad")
        w.config(state="disabled")

    def apply(self):
        lines = self.txt.get("1.0", "end-1c").splitlines()
        strokes, warns = self.app.apply_edited_gcode(lines)
        warns = list(warns)
        if not strokes:
            warns.append("그릴 선(펜 내림 + G1 이동)을 하나도 찾지 못했어요. 3D 미리보기에 아무것도 그려지지 않아요.")
        self.dirty = False
        self.banner.pack_forget()
        self._highlight()
        self.update_summary()
        self._update_state()
        if warns:
            self._show_warns(warns)
        else:
            self._show_warns([f"적용했어요: 선 {len(strokes)}개. 3D 미리보기·G코드 저장·기계 전송에 이 G코드가 쓰여요."], good=True)

    def revert(self):
        self.dirty = False
        self.app.revert_gcode()
        self.load(self.app.gcode)
        self._show_warns(["자동 생성 G코드로 되돌렸어요."], good=True)

    def save(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".gcode", title="G코드 저장",
                                            filetypes=[("G-code", "*.gcode *.nc *.ngc"), ("모든 파일", "*.*")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.txt.get("1.0", "end-1c").rstrip("\n") + "\n")
            self.app.log(f"G코드 저장(편집창): {path}")

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.txt.get("1.0", "end-1c"))
        self.app.log("G코드를 클립보드에 복사했어요.")

    def _search(self, pat):
        self.v_find.set(pat)
        self.find_next()

    def find_next(self):
        pat = self.v_find.get()
        t = self.txt
        t.tag_remove("hit", "1.0", "end")
        if not pat:
            return
        idx = t.search(pat, self._find_pos, stopindex="end", nocase=True) or t.search(pat, "1.0", stopindex="end", nocase=True)
        if not idx:
            self._show_warns([f"'{pat}' 를 찾지 못했어요."])
            self._find_pos = "1.0"
            return
        end = f"{idx}+{len(pat)}c"
        t.tag_add("hit", idx, end)
        t.see(idx)
        t.mark_set("insert", idx)
        self._find_pos = end
        self.ln.yview_moveto(t.yview()[0])

    def goto(self):
        try:
            n = int(self.v_goto.get())
        except ValueError:
            return
        n = max(1, min(self._nlines, n))
        self.txt.see(f"{n}.0")
        self.txt.mark_set("insert", f"{n}.0")
        self.txt.tag_remove("hit", "1.0", "end")
        self.txt.tag_add("hit", f"{n}.0", f"{n}.end")
        self.ln.yview_moveto(self.txt.yview()[0])

    def close(self):
        if self.dirty and not messagebox.askyesno("G코드 확인·수정", "적용하지 않은 변경이 있어요. 그래도 닫을까요?", parent=self):
            return
        self.destroy()


# =============================================================================
# 10. 실행
# =============================================================================
def main():
    if not HAS_PIL:
        print("Pillow 가 필요해요:  python -m pip install pillow")
    App().mainloop()


if __name__ == "__main__":
    main()
