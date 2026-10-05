# eggbot-atc

캡스톤 디자인 2 · 3조 **CNC ATC 다색 에그봇**의 소프트웨어입니다.
그림 파일(SVG)을 기계 명령(G-code)으로 바꾸고, 색이 바뀌는 지점마다 **자동 펜 교체(ATC) 6단계 동작**을 끼워 넣습니다.

> 코드 수정 요청, 측정값 전달, 버그 신고는 모두 GitHub **이슈**로 올려 주세요.
> GitHub가 처음이면 [docs/깃허브-사용법.md](docs/깃허브-사용법.md)부터 읽으면 됩니다.

## 전체 흐름

```
Inkscape (색마다 레이어 하나, 레이어 이름 "T1 빨강", "T2 파랑" …)
   │  파일 → 저장 (.svg)
   ▼
eggbot-atc convert 그림.svg -o 출력.gcode      ← 이 저장소
   │  SVG → G-code 변환 + 색 바뀔 때 ATC 매크로 삽입
   ▼
Universal Gcode Sender (UGS)                    ← 파일 열고 ▶ 누르면 됨
   │  USB 시리얼
   ▼
Arduino Mega 2560 + RAMPS 1.4 + grbl-Mega-5X    ← 모터 5축 구동
```

축 배정 (설정 파일 기준, 바꿀 수 있음):

| 축 | 역할 | 단위 |
|---|---|---|
| X | 펜 암 좌우 회전 (모터 직결) | 도(°) |
| Y | 계란 회전 | 도(°) |
| Z | 매거진(펜 보관통) 회전 | 도(°) |
| A | ATC 슬라이드 이송 (T8 리드스크루, Ø8 샤프트 2개) | mm |
| 서보 | 펜 올리기/내리기 | `M3 S…` PWM |

## 설치

Python 3.12 이상과 [uv](https://docs.astral.sh/uv/)가 필요합니다. uv 설치:

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
```bash
# Mac
curl -LsSf https://astral.sh/uv/install.sh | sh
```

그다음 이 폴더에서:

```bash
uv sync
uv run eggbot-atc check          # 아직 안 잰 값 목록
```

## 명령 4개

```bash
uv run eggbot-atc check                                   # machine.toml에서 TODO로 남은 값 나열
uv run eggbot-atc convert 그림.svg -o 출력.gcode            # 변환 + ATC 삽입 (TODO가 남아 있으면 거부)
uv run eggbot-atc settings > grbl_settings.txt            # GRBL $ 설정값 생성 (UGS 콘솔에 붙여넣기)
uv run eggbot-atc send 출력.gcode --port COM3              # 디버그용 직접 전송 (평소엔 UGS 사용). Mac은 /dev/tty.usbmodemXXXX
```

`--config 다른파일.toml`을 명령 앞에 붙이면 다른 설정 파일을 씁니다. 기계 없이 돌려 보려면:

```bash
uv run eggbot-atc --config tests/machine_test.toml convert tests/sample.svg -o out.gcode
```

`tests/machine_test.toml`의 숫자는 **측정값이 아니라** 테스트용 자리표시자입니다.

## 그림 그리는 규칙 (Inkscape)

- **색 하나 = 레이어 하나.** 레이어 이름에 펜 번호를 넣습니다: `T1 빨강`, `T2 파랑`, `T3 초록`. `T` 번호가 없으면 레이어 순서대로 1, 2, 3이 됩니다.
- 펜 번호는 매거진 슬롯 번호입니다. `slot_count`보다 큰 번호는 오류.
- **캔버스 세로 = 계란 한 바퀴(360°)**, 캔버스 가로 = `svg_width_deg`(펜 암이 좌우로 도는 각도). 지름이 다른 물체(골프공 등)는 Inkscape에서 캔버스 비율을 그에 맞게 잡고 `svg_width_deg`만 바꾸면 됩니다.
- 선(stroke)만 그려집니다. 면 채우기(fill)는 무시됩니다. 곡선은 `flatten_mm` 간격으로 잘게 쪼개 직선으로 보냅니다.
- 회전 방향이 반대면 `y_invert = true`.

## 전원 켤 때마다: 영점 잡기

모든 측정값은 이 영점 기준의 절대 좌표입니다. 엔드스탑을 아직 안 달았으므로 손으로 맞춥니다.

1. UGS 연결 → 조그(Jog) 화면에서 X(펜 암)를 왼쪽 끝 각도, A(슬라이드)를 완전 후퇴 위치로 옮깁니다.
2. Z(매거진)를 돌려 **슬롯 1**이 팔과 마주 보게 맞춥니다.
3. Y(계란)는 아무 위치나.
4. UGS 콘솔에 `G92 X0 Y0 Z0 A0` 입력. (변환된 G-code 첫머리에도 같은 줄이 들어 있으니, 실행 직전 위치가 곧 영점입니다.)
5. 엔드스탑 배선이 끝나면 `machine.toml`의 `home_cmd`를 `"$H"`로 바꾸면 자동 원점복귀로 대체됩니다.

## 측정 체크리스트 (`machine.toml`의 TODO)

전체 설정값(추정치·튜닝값 포함)은 [docs/설정값-목록.md](docs/설정값-목록.md)에 정리돼 있습니다.
부품 구매 목록과 빠진 부품은 [docs/부품-목록.md](docs/부품-목록.md)에 있습니다.
도면(2026-10-02)에서 옮긴 부품 치수와 도면 확인 사항은 [docs/기구-치수.md](docs/기구-치수.md)에 있습니다.
공유받은 `변환 프로그램.exe`와의 차이는 [docs/변환프로그램-비교.md](docs/변환프로그램-비교.md)에 있습니다.

UGS 조그로 축을 움직이고, 화면에 보이는 기계 좌표를 읽어 적습니다. 다 채우면 `uv run eggbot-atc check`가 `all measured`를 출력합니다.

| 키 | 무엇 | 어떻게 재나 |
|---|---|---|
| `machine.x_max_deg` | 펜 암 최대 회전각 | 왼쪽 끝 영점에서 오른쪽 끝까지 조그 |
| `drawing.svg_width_deg` | 그림 가로가 대응될 X 각도 | 계란 위에서 펜이 그릴 수 있는 좌우 범위를 펜 암 각도로 |
| `drawing.x_offset_deg` | 그림 왼쪽 끝이 놓일 X | 펜이 계란 그림 영역 왼쪽 끝에 닿는 X. `x_offset_deg + svg_width_deg ≤ x_max_deg` |
| `atc.park_x` | 교체 대기 X | 슬라이드가 들어와도 펜/팔과 안 부딪히는 X |
| `atc.dock_x` | 펜이 슬롯에 완전히 물리는 X | 슬라이드 `slide_in_mm` 상태에서 X를 천천히 돌려 자석+핀이 딱 맞는 곳 |
| `atc.release_x` | 펜을 두고 팔만 빠진 X | dock_x에서 뒤로 빼서 자석이 완전히 떨어지는 곳 |
| `atc.slide_in_mm` | 매거진이 팔에 밀착된 A | 조그로 A를 밀어 밀착 |
| `atc.slide_index_mm` | 매거진이 회전해도 안 걸리는 A | slide_in에서 살짝 뒤로 |
| `atc.slide_out_mm` | 완전 후퇴 A (그리는 동안) | 보통 0 |
| `atc.slot0_deg` | 슬롯 1의 Z 각도 | 영점을 슬롯 1에 맞췄으면 0. 슬롯 n = slot0 + (n−1)·360/slot_count |

값을 잰 뒤에는 `machine.toml`을 직접 고쳐도 되고, 이슈 양식 "측정값 전달"로 올려도 됩니다.

## ATC 6단계 (변환기가 넣는 G-code)

`park_x`·`dock_x`·`release_x`는 펜 암 각도(°)입니다. 펜 고정부를 붙이고 떼는 동작을 X(펜 암 회전)가 하는지 Z(매거진 회전)가 하는지는 아직 정해지지 않았습니다. 지금 순서는 X 방식이고, 시운전에서 확인합니다.

```
; ATC T1 -> T2
M3 S90          ; 1 펜 올림
G4 P0.3
G0 X{park_x}    ; 2 교체 위치로
G1 A{slide_in}  ;   슬라이드 전진 (매거진 밀착)
G1 X{dock_x}    ; 3 기존 펜을 슬롯에 넣고
G1 X{release_x} ;   팔만 후퇴
G1 A{slide_index} ; 4 살짝 후퇴
G1 Z{슬롯 각도}   ;   매거진 인덱싱
G1 A{slide_in}  ; 5 다시 밀착
G1 X{dock_x}    ;   새 펜 도킹 (자석+핀)
G1 A{slide_out} ; 6 슬라이드 후퇴
; ATC end
G0 X.. Y..      ;   그리던 곳으로 복귀 후 펜 내림
```

파일 끝에서는 마지막 펜을 매거진에 반납하고 `G0 X0 Y0 Z0 A0`으로 영점 자세로 돌아갑니다. 그래서 연속으로 여러 도안을 돌릴 때 다시 영점을 잡을 필요가 없습니다. **중간에 멈췄다면** 펜을 손으로 슬롯에 돌려놓고 영점을 다시 잡거나, 다음 실행 전에 `initial_tool`을 지금 물고 있는 펜 번호로 바꾸세요.

시작할 때(펜 없음, `initial_tool = 0`)는 반납 단계를 건너뛰고 **Z 인덱싱을 먼저** 한 뒤 슬라이드를 밀어 넣습니다. 부팅 직후 매거진 각도가 슬롯에 안 맞은 채로 슬라이드부터 밀면 부딪히기 때문입니다.

## 서보(펜 승하강) 주의

- grbl-Mega-5X 기본 펌웨어에는 서보 명령이 없어 **스핀들 PWM(`M3 S…`)** 으로 대신합니다. `S`는 각도가 아니라 0~`$30`(=255) 범위의 듀티 값입니다. `M3 S90`/`M3 S30`은 출발점일 뿐, 실제 올라감/내려감 값은 튜닝해서 `pen_up_cmd`/`pen_down_cmd`에 넣습니다.
- **RAMPS의 기본 스핀들 PWM 핀 D8은 12 V MOSFET 출력입니다. 서보 신호선을 절대 연결하지 마세요.** 펌웨어 `config.h`에서 `SPINDLE_PWM_ON_D6`(RAMPS 서보 헤더의 D6, 5 V 로직)로 옮긴 뒤 그 핀에 연결합니다. 펌웨어가 고를 수 있는 핀은 D8/D6/D9뿐이고, D9도 RAMPS에서는 12 V MOSFET(팬) 출력이라 쓸 수 없습니다. 서보 전원은 Mega 5 V가 아니라 5 V DC-DC에서 받습니다 ([부품 목록](docs/부품-목록.md) 4장).
- MG90S가 PWM으로 안 움직이면(관련 이슈: [fra589/grbl-Mega-5X#343](https://github.com/fra589/grbl-Mega-5X/issues/343)) 서보 전용 포크 [alnwlsn/grbl-Mega-5X-servos](https://github.com/alnwlsn/grbl-Mega-5X-servos)를 올리고 `pen_up_cmd = "M96 A400"` 처럼 두 줄만 바꾸면 됩니다.
- 변환된 G-code 끝에 `M5`를 넣지 않습니다. `M5`는 듀티 0이라 펜이 한쪽 끝으로 튈 수 있습니다.

## GRBL 설정 적용

```bash
uv run eggbot-atc settings
```

출력된 `$100=…` 줄을 UGS 콘솔에 한 줄씩 붙여넣습니다. `; $111=TODO` 처럼 주석 처리된 줄은 값을 정한 뒤 `machine.toml`의 `[grbl]` 표에 적으면 채워집니다.

- `$100`, `$101 = 200×32/360 = 17.778` : X 1 단위 = 펜 암 1도, Y 1 단위 = 계란 1도.
- `$103 = 200×32/8 = 800` : A 1 단위 = 슬라이드 1 mm (T8 리드 8 mm 기준).
- `$132`(Z 최대 이동)는 `slot0_deg + 360` 이상이어야 마지막 슬롯까지 돌아갑니다.

## 개발

```bash
uv run pytest        # 기계 없이 돌아가는 테스트
```

기계·서보·시리얼 전송은 하드웨어가 없어 **검증되지 않았습니다.** 조립 후 첫 시운전은 펜 없이, 속도(`atc_feed`)를 낮춰서 하세요.

## 참고한 저장소

| 저장소 | 어디에 쓰나 |
|---|---|
| [fra589/grbl-Mega-5X](https://github.com/fra589/grbl-Mega-5X) | 보드에 올리는 펌웨어. [설정 문서](https://github.com/fra589/grbl-Mega-5X/blob/edge/doc/markdown/settings.md), [핀아웃](https://github.com/fra589/grbl-Mega-5X/wiki/grbl-Mega-5X-pinout) |
| [alnwlsn/grbl-Mega-5X-servos](https://github.com/alnwlsn/grbl-Mega-5X-servos) | 서보 전용 명령(`M96`)이 있는 포크. PWM 방식이 안 되면 대안 |
| [winder/Universal-G-Code-Sender](https://github.com/winder/Universal-G-Code-Sender) | G-code 전송 프로그램 |
| [cocktailyogi/EggDuino](https://github.com/cocktailyogi/EggDuino) | 기존 단색 에그봇 펌웨어(아두이노 우노). 메모리·핀 한계로 미채택 |
| [bartebor/eggbot_extensions](https://github.com/bartebor/eggbot_extensions) | EggDuino용 Inkscape 확장. 레이어 모드 펜 교체 방식 참고 |
| [evil-mad/EggBot](https://github.com/evil-mad/EggBot) | 원조 EggBot Inkscape 확장. G-code가 아니라 EBB 명령을 내보내서 직접 쓰지 않음 |
| [evomotors/ESPEggBot](https://github.com/evomotors/ESPEggBot) | 다색 시 멈추고 펜 교체를 요청하는 방식. 우리는 이걸 ATC로 자동화 |
| [meerk40t/svgelements](https://github.com/meerk40t/svgelements) | SVG 파싱·변환·곡선 평탄화 라이브러리 (의존성) |
