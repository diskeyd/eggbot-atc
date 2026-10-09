# 펌웨어 (grbl-Mega-5X, 서보 D6)

`grbl-mega-5x-d6.hex`는 Arduino Mega 2560 + RAMPS 1.4에 바로 올리는 펌웨어입니다.

| 항목 | 값 |
|---|---|
| 원본 | [fra589/grbl-Mega-5X](https://github.com/fra589/grbl-Mega-5X) `edge` 브랜치, 커밋 `baaa1be` |
| 바꾼 것 | `grbl/config.h`: `SPINDLE_PWM_ON_D8` 끄고 `SPINDLE_PWM_ON_D6` 켬 (서보 신호를 RAMPS 서보 헤더 D6으로) |
| 그대로 둔 것 | `N_AXIS 5` (E1 소켓은 비어 있어서 5번 축은 안 움직임), 나머지 전부 기본값 |
| 라이선스 | GPLv3 (원본과 같음). 소스 = 위 원본 + "바꾼 것"의 두 줄 |
| 빌드 | arduino-cli, `arduino:avr:mega:cpu=atmega2560` (2026-10-09) |

## 올리는 법 (Windows)

1. Mega를 USB로 연결하고 장치 관리자에서 COM 번호를 확인합니다.
2. 둘 중 하나로 올립니다.
   - **XLoader**: Device `Mega(ATMEGA2560)`, COM 포트 선택, Baud `115200`, hex 파일 선택 → Upload
   - **avrdude** (Arduino IDE에 들어 있음):
     ```
     avrdude -p m2560 -c wiring -P COM3 -b 115200 -D -U flash:w:grbl-mega-5x-d6.hex:i
     ```
3. UGS로 연결해서 `$RST=*`로 설정을 초기화한 뒤 `uv run eggbot-atc settings` 출력을 붙여넣습니다.

## 다시 빌드할 때

원본을 받아 `grbl/config.h`의 위 두 줄만 바꾸고 `grbl/examples/grblUpload`를 Mega 2560으로 컴파일합니다.
