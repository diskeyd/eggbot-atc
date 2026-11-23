"""Build the submission PDF: report.html (content) + style.css (design) -> Chrome headless -> PDF.

Two passes: the first finds each chapter's page, the second prints them into the table of contents.
Needs macOS Chrome, poppler (pdfinfo, pdftotext) and network access for the first font download.
"""
import base64, re, subprocess, tempfile, urllib.request
from pathlib import Path

B = Path(__file__).parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FONT_URL = "https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/variable/woff2/PretendardVariable.woff2"  # SIL OFL 1.1
TMP = Path(tempfile.gettempdir())
FONT = TMP / "PretendardVariable.woff2"
if not FONT.exists():
    urllib.request.urlretrieve(FONT_URL, FONT)

src = (B / "report.html").read_text(encoding="utf-8")
css = (B / "style.css").read_text(encoding="utf-8")
css = css.replace("__FONT__", base64.b64encode(FONT.read_bytes()).decode())

# ---- 본문 분리: 1장~9장 = manual, 10장~부록 = analysis
i1 = src.index("<!-- ============ 1 ============ -->")
i10 = src.index("<!-- ============ 10 구조 ============ -->")
iend = src.index("</body>")
manual, analysis = src[i1:i10], src[i10:iend]

REQUIRED = {"설치", "실행", "의존성", "기계 연결 및 제어", "문제 해결"}

def h1_fix(m):
    num, title = m.group(2), m.group(3)
    n = "A" if num == "부록" else f"{int(num):02d}"
    tag = '<span class="tag">필수</span>' if title in REQUIRED else ""
    cls = "pb" if num == "부록" else (m.group(1) or "")
    return f'<h1 class="{cls}"><span class="num">{n}</span><span class="ttl">{title}</span>{tag}</h1>'

def fix_part(s):
    s = re.sub(r'<h1(?: class="([^"]*)")?>(\d+|부록)\. (.*?)</h1>', h1_fix, s)
    s = s.replace('<h1 class="">', '<h1 class="first">', 1)  # 파트 첫 제목: 위 여백 0
    # SVG 색·글꼴
    for a, b in {"#0b3d6e": "#14304f", "#f3f6fa": "#ffffff", "#586069": "#6b7280", "#fff4e5": "#fdf0e7", "#e8a33d": "#e8683a",
                 "#e9f6ec": "#e4f2f1", "#2e8b57": "#1f7a78", "#dbe7f3": "#dbe7f3",
                 "font-family=\"Apple SD Gothic Neo, sans-serif\"": "font-family=\"Pretendard Variable, sans-serif\""}.items():
        s = s.replace(a, b)
    return s

manual, analysis = fix_part(manual), fix_part(analysis)
# 시스템 구성도: 연결선을 하나의 버스로 정리
manual = re.sub(r'(?:<path d="M406[^"]*"/>)+',
    '<path d="M406 115 L470 115" marker-end="none"/><path d="M470 28 L470 220" marker-end="none"/>'
    '<path d="M470 28 L518 28"/><path d="M470 76 L518 76"/><path d="M470 124 L518 124"/><path d="M470 172 L518 172"/><path d="M470 220 L518 220"/>', manual)

# ---- 표지
cover = """
<section class="cover">
  <div class="band"></div>
  <div class="course"><b>CAPSTONE DESIGN 2</b>13분반 · 3조</div>
  <svg class="art" viewBox="0 0 360 420" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <clipPath id="egg"><path d="M180 30 C262 30 318 150 318 240 C318 328 258 384 180 384 C102 384 42 328 42 240 C42 150 98 30 180 30 Z"/></clipPath>
      <linearGradient id="eg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fffaf0"/><stop offset="1" stop-color="#eadfc8"/></linearGradient>
    </defs>
    <ellipse cx="180" cy="398" rx="110" ry="9" fill="#0b2239" opacity=".35"/>
    <path d="M180 30 C262 30 318 150 318 240 C318 328 258 384 180 384 C102 384 42 328 42 240 C42 150 98 30 180 30 Z" fill="url(#eg)"/>
    <g clip-path="url(#egg)" fill="none" stroke="#14304f" stroke-opacity=".20" stroke-width="1">
      <ellipse cx="180" cy="100" rx="170" ry="15"/><ellipse cx="180" cy="150" rx="170" ry="15"/><ellipse cx="180" cy="200" rx="170" ry="15"/>
      <ellipse cx="180" cy="250" rx="170" ry="15"/><ellipse cx="180" cy="300" rx="170" ry="15"/><ellipse cx="180" cy="350" rx="170" ry="15"/>
      <ellipse cx="180" cy="207" rx="45" ry="180"/><ellipse cx="180" cy="207" rx="95" ry="180"/><ellipse cx="180" cy="207" rx="145" ry="180"/>
    </g>
    <g clip-path="url(#egg)" fill="none" stroke-linecap="round" stroke-linejoin="round">
      <path d="M62 268 C92 206 126 204 154 250 S214 308 246 238 S292 208 306 250" stroke="#e8683a" stroke-width="4"/>
      <path d="M104 160 C140 122 220 122 258 160" stroke="#1f7a78" stroke-width="3.6"/>
      <path d="M128 322 C160 346 206 346 236 322" stroke="#14304f" stroke-width="3.2" stroke-opacity=".8"/>
    </g>
    <g stroke-linecap="round">
      <path d="M338 14 L250 204" stroke="#cfe0ef" stroke-width="7"/>
      <circle cx="338" cy="14" r="10" fill="#cfe0ef"/><circle cx="338" cy="14" r="4" fill="#14304f"/>
      <circle cx="250" cy="206" r="6" fill="#e8683a" stroke="#fff" stroke-width="2.4"/>
      <path d="M338 14 m-62 28 a68 68 0 0 0 -2 36" fill="none" stroke="#cfe0ef" stroke-width="1.4" stroke-dasharray="3 4" opacity=".7"/>
    </g>
  </svg>
  <h1 class="title"><small>이미지 → 달걀 표면 → G-code</small>에그봇<br>G-code 생성기</h1>
  <div class="subtitle">이미지 벡터화 · 3D 시뮬레이션<br>회전식 펜 매거진(ATC) · GRBL 시리얼 통합 제어<br><em>설치·실행·연결 매뉴얼 및 프로그램 분석 보고서</em></div>
  <pre class="gcode" style="background:none;border:0;padding:0;margin:0;color:rgba(255,255,255,.34);font-size:7.6pt;position:absolute;left:22mm;top:150mm;">M3 S90
G4 P0.3
; 펜 교환: 1번 -&gt; 2번
G0 X0 Y0 F3000</pre>
  <div class="meta"><div class="grid">
    <div class="card"><div class="k">과목</div><div class="v">캡스톤 디자인 2</div></div>
    <div class="card"><div class="k">분반 / 조</div><div class="v">13분반 / 3조</div></div>
    <div class="card wide"><div class="k">대상 프로그램</div><div class="v">캡스톤 디자인2_13분반_3조_프로그램.py <span style="font-weight:400;color:#6b7280;font-size:9pt">(7,283줄 · Python / Tkinter)</span></div></div>
    <div class="card wide"><div class="k">관련 저장소</div><div class="v" style="font-size:9.6pt">https://github.com/diskeyd/eggbot-atc</div></div>
    <div class="card"><div class="k">작성일</div><div class="v">2026-11-23</div></div>
    <div class="card"><div class="k">환경</div><div class="v" style="font-size:9.6pt">Windows EXE · Python 소스</div></div>
  </div></div>
  <div class="foot">이 문서의 수치는 소스 코드, 설정 화면, 저장소 문서에서 옮긴 값이다. 기계 실측으로 안전성을 확인한 값이 아니다. 자세한 내용은 13장 참고.</div>
</section>
"""

MAN = [(1, "프로그램 개요"), (2, "설치"), (3, "실행"), (4, "의존성"), (5, "기본 사용 순서"), (6, "기계 연결 및 제어"),
       (7, "설정값 안내"), (8, "문제 해결"), (9, "안전 주의사항")]
ANA = [(10, "소프트웨어 구조"), (11, "핵심 알고리즘"), (12, "G-code와 ATC 교환 시퀀스"), (13, "한계 및 향후 과제"), ("A", "관련 저장소 문서")]

def toc(items, pages):
    out = ['<ol class="toc">']
    for n, t in items:
        num = n if n == "A" else f"{n:02d}"
        cls = ' class="must"' if t in REQUIRED else ""
        out.append(f'<li{cls}><span class="n">{num}</span><span class="t">{t}</span><span class="p">{pages.get(str(n), "")}</span></li>')
    out.append("</ol>")
    return "\n".join(out)

def front(pages):
    return f"""
<section class="front">
  <h1 class="first"><span class="ttl">문서 한눈에 보기</span></h1>
  <p>이미지에서 선과 영역을 추출해 달걀 또는 공 표면에 그림을 그리는 G-code를 만든다. 생성한 G-code는 USB로 GRBL 컨트롤러에 전송한다. Windows용 EXE로 배포하고, Python 소스로도 실행한다.</p>
  <div class="stats">
    <div class="stat"><span class="n">7,283<small>줄</small></span><span class="l">단일 Python 파일</span></div>
    <div class="stat"><span class="n">3<small>종</small></span><span class="l">달걀 · 탁구공 · 골프공</span></div>
    <div class="stat"><span class="n">1~3<small>개</small></span><span class="l">펜 (회전식 매거진 ATC)</span></div>
    <div class="stat"><span class="n">115200<small>bps</small></span><span class="l">USB 시리얼 (GRBL)</span></div>
  </div>
  <h2>목차</h2>
  <div class="part-label">PART A · 설치·실행·연결 매뉴얼</div>
  {toc(MAN, pages)}
  <div class="part-label">PART B · 프로그램 분석</div>
  {toc(ANA, pages)}
  <div class="note"><b class="t">문서 기준</b>
  기준 프로그램은 <code>캡스톤 디자인2_13분반_3조_프로그램.py</code>이다. 배포 형태는 Windows용 EXE이다. Python 소스를 직접 실행하는 방법도 함께 적었다.<br>
  설정값 중 "사진 기준 입력 예시"는 설정 화면에 표시된 값을 옮긴 것이다. 기계 실측으로 안전성이 확인된 값이 아니고, 프로그램의 초기 기본값도 아니다.</div>
  <div class="colophon">진한 번호와 <b>필수</b> 표시는 과제 필수 항목(설치·실행·의존성·연결·문제 해결)이다. 본문 글꼴은 Pretendard(SIL OFL 1.1)이다.</div>
</section>
"""

def page_html(pages):
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>에그봇 G-code 생성기 – 제출 문서</title>'
            f'<style>{css}</style></head><body>{cover}{front(pages)}<div class="manual">{manual}</div><div class="analysis">{analysis}</div></body></html>')

def render(html_path, pdf_path):
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
                    f"file://{html_path}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=180)

def find_pages(pdf):
    n = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout).group(1))
    found = {}
    for p in range(3, n + 1):  # 표지(1)·앞쪽(2) 제외
        txt = subprocess.run(["pdftotext", "-f", str(p), "-l", str(p), str(pdf), "-"], capture_output=True, text=True).stdout
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        for k, (num, title) in enumerate(MAN + ANA):
            key = str(num); tag = num if num == "A" else f"{num:02d}"
            if key in found: continue
            for j, l in enumerate(lines):
                if l == tag and j + 1 < len(lines) and lines[j + 1].startswith(title[:5]):
                    found[key] = p; break
                if l.startswith(tag + " ") and title[:5] in l:
                    found[key] = p; break
    return found, n

h, p = TMP / "eggbot-final.html", B / "에그봇_G코드_생성기_제출문서.pdf"
h.write_text(page_html({}), encoding="utf-8"); render(h, p)
pages, n = find_pages(p)
h.write_text(page_html(pages), encoding="utf-8"); render(h, p)
pages2, n2 = find_pages(p)
print("pages", n2, "toc", pages2, "stable", pages == pages2)
missing = [str(k) for k, _ in MAN + ANA if str(k) not in pages2]
print("missing", missing)
