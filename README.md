# slate-rename — 슬레이트 음성 콜로 촬영본 파일명 자동 변경

촬영본 앞부분(기본 15초)에서 "12의 3", "씬 12 테이크 3" 같은 슬레이트 콜을 음성 인식으로 듣고,
`C0001.MP4` → `S12_T03_C0001.MP4` 처럼 파일명을 바꿔 줍니다.
카메라 영상(MP4/MOV/MXF…)과 외부 녹음기 파일(WAV)을 같이 처리하므로, 같은 테이크는 이름 앞부분이 같아져 나란히 정렬됩니다.

```
S12_T03_C0001.MP4        ← 카메라
S12_T03_ZOOM0001.WAV     ← 녹음기
S12_T04_C0002.MP4
S12_T04_ZOOM0002.WAV
```

인식에 실패한 파일은 **이름을 건드리지 않고** "확인 필요" 목록에 띄웁니다. 모든 변경은 로그로 남아 한 줄로 되돌릴 수 있습니다.

---

## 1. 설치 (윈도우 / 맥 공통)

1. Python 3.10 이상 설치 (https://www.python.org/downloads/ — 윈도우는 설치 시 "Add python.exe to PATH" 체크)
2. 이 폴더에서 터미널(윈도우: PowerShell, 맥: 터미널)을 열고:

```bash
pip install -r requirements.txt
```

ffmpeg 설치는 필요 없습니다(PyAV 내장). 첫 실행 때 Whisper 모델(small, 약 500MB)을 자동으로 내려받습니다.

- 맥 Apple Silicon: CPU 모드로 동작합니다. 클립 1개당 몇 초 수준.
- 윈도우 + NVIDIA GPU: CUDA가 잡히면 자동으로 GPU를 씁니다(`--device cuda` 로 강제 가능).
- GPU 없는 윈도우 노트북: CPU 모드. `--model base` 로 낮추면 빨라지지만 인식률이 떨어집니다.

## 2. 사용법

```bash
# 먼저 바꾸지 않고 결과만 본다 (권장: 첫 촬영분은 꼭 이걸로 확인)
python slate_rename.py "D:\촬영\1101_DAY1" --dry-run

# 실제로 파일명 변경
python slate_rename.py "D:\촬영\1101_DAY1"

# 클립 끝부분도 들어서 OK/NG 콜 판별 → OK컷은 이름에 _OK 가 붙음 (처리 시간 약 2배)
python slate_rename.py "D:\촬영\1101_DAY1" --ok

# 카드 복사가 들어오는 폴더를 감시하다가 새 파일마다 자동 처리 (Ctrl+C 로 종료)
python slate_rename.py "D:\촬영\1101_DAY1" --watch

# 카메라 2대: 폴더별로 카메라 표시 → S12_T03_A_C0001.MP4 / S12_T03_B_C0001.MP4
python slate_rename.py "D:\촬영\A캠" --cam A
python slate_rename.py "D:\촬영\B캠" --cam B

# 되돌리기 (해당 폴더의 로그 파일 지정)
python slate_rename.py --undo "D:\촬영\1101_DAY1\slate_rename_log.csv"
```

맥에서는 경로를 `"/Volumes/카드/DCIM/100MSDCF"` 처럼 적으면 됩니다. 폴더를 터미널 창에 끌어다 놓으면 경로가 입력됩니다.

### 실행 후 생기는 파일

| 파일 | 내용 |
|---|---|
| `slate_rename_log.csv` | 변경 전/후 이름, 들린 말, 상태. `--undo` 가 이 파일을 봅니다 |
| `slate_index.csv` | 씬·테이크 순으로 정렬된 정리표(OK 여부, 원본명, 들린 말). 엑셀로 열어 편집 때 옆에 두고 보세요 |

### 주요 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--seconds` | 15 | 앞에서 몇 초를 들을지 |
| `--offset` | 0 | 앞을 몇 초 건너뛸지 (카메라 켜고 한참 뒤에 슬레이트 치면 늘리기) |
| `--ok` | 꺼짐 | 끝부분(`--tail-seconds` 8초)에서 "오케이/다시 갈게요" 판별 |
| `--template` | `S{scene:02d}{suffix}_T{take:02d}` | 이름 앞부분 형식. `{shot}` 도 사용 가능 (예: `S{scene}_C{shot}_T{take}`) |
| `--no-keep-original` | 꺼짐 | 원본 파일명을 뒤에 안 붙임 (`S12_T03.MP4`) — 추적이 어려워져 비권장 |
| `--model` | small | `base`(빠름) / `small` / `medium`(정확, 느림) / `large-v3` |
| `-v` | 꺼짐 | 인식 후보를 전부 출력 (튜닝용) |
| `-r` | 꺼짐 | 하위 폴더까지 |

## 2-1. 웹 데모 (남에게 보여줄 때)

```bash
python app.py              # 브라우저에서 http://127.0.0.1:7860 열림
SHARE=1 python app.py      # 72시간짜리 공개 링크(https://xxxx.gradio.live)도 같이 생성 → 링크만 보내면 됨
```

클립을 드래그해 올리고 실행을 누르면 "들린 말 / 씬 / 테이크 / 새 이름" 표가 뜨고, 이름 바뀐 사본을 zip으로 받을 수 있습니다.

**상시 접속되는 데모 페이지 (무료)** — Hugging Face Spaces:
1. https://huggingface.co/new-space 에서 Space 생성 (SDK: Gradio, 하드웨어: CPU basic 무료)
2. 이 폴더의 `app.py`, `requirements.txt`, `slate_rename/` 을 그대로 업로드
3. 몇 분 뒤 `https://huggingface.co/spaces/계정/이름` 에서 동작. 첫 실행 때 모델을 받느라 1~2분 걸리고 그 뒤로는 클립당 수 초.

## 3. 인식되는 콜 형태

전부 지원합니다. 숫자는 아라비아 숫자("12"), 한자어("십이"), 고유어("열둘") 모두 됩니다.

| 현장에서 말하는 것 | 결과 |
|---|---|
| "12의 3", "12에 3", "12 - 3", "12, 3", "12 3", "12 다시 3" | S12_T03 |
| "씬 12 테이크 3", "씬 12의 3", "12번 씬 3번 테이크" | S12_T03 |
| "씬 12 컷 2 테이크 3" | S12_T03 (`{shot}`=2, 템플릿에 넣으면 표시) |
| "12A의 3", "12에이의 3" | S12A_T03 |
| "자 갑니다, 12의 3, 마크" | 앞뒤 말은 무시하고 S12_T03 |

**권장 콜 방식: "씬 십이, 테이크 삼"처럼 단어를 붙여서 말하기.**
"12의 3"만 짧게 말하면 음성 인식기가 문맥 없이 1~2초짜리 소리에서 숫자를 뽑아야 해서 오인식이 늘어납니다. "씬"·"테이크" 단어가 들어가면 인식기에도 파서에도 기준점이 생겨 훨씬 안정적입니다. 한 번 정해서 팀 전체가 같은 방식으로 말하는 게 가장 큰 정확도 향상입니다.

## 4. 한계 / 알아둘 것

- **카메라가 저장하는 순간**에 파일명을 바꾸는 건 어떤 카메라도 허용하지 않습니다. 이 도구는 카드를 PC에 복사한 뒤(또는 `--watch` 로 복사되는 즉시) 처리합니다.
- **OK컷 판별(`--ok`)은 보조 수단**입니다. 감독 목소리가 멀거나 여러 명이 겹치면 못 잡습니다. 못 잡으면 빈칸으로 두지, 틀린 값을 쓰지는 않습니다. 스크립터 기록이 있으면 그게 우선입니다.
- 슬레이트 콜이 없거나(바로 액션), 콜 중에 다른 사람이 말하거나, 바람/현장음이 크면 인식에 실패합니다 → 이름 그대로 두고 목록에 표시됩니다.
- Blackmagic RAW(.braw)는 PyAV가 열지 못해 처리 대상에서 빠집니다. 블랙매직은 카메라 자체 슬레이트 메타데이터를 쓰는 편이 낫습니다.
- 같은 씬/테이크가 두 번 나오면(콜 실수, 재촬영인데 테이크 안 올림) 두 번째 파일에 `_dup1` 이 붙습니다.
- 이미 `S12_T03_` 형태인 파일은 다시 실행해도 건너뜁니다(`--force` 로 강제).

## 5. 첫 촬영 뒤에 해 볼 것

실제 촬영본 5~10개로 아래를 실행해 출력(특히 "들린 말")을 그대로 보내 주세요. 그걸 기준으로 파서와 인식 유도 문구(`--prompt`)를 맞추면 인식률이 올라갑니다.

```bash
python slate_rename.py "촬영폴더" --dry-run -v
```

## 6. 대안 백엔드 (선택)

기본은 faster-whisper 입니다. 인터넷 제한 환경이나 더 가벼운 모델이 필요하면 sherpa-onnx 백엔드를 쓸 수 있습니다
(`pip install "sherpa-onnx<2"`, 모델은 GitHub k2-fsa/sherpa-onnx 릴리스에서 다운로드).

```bash
# SenseVoice (한·중·일·영 지원, CPU에서 클립당 약 2초, 숫자를 아라비아 숫자로 바로 출력)
python slate_rename.py "촬영폴더" --backend sherpa-onnx --model-dir ./models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17
```

## 7. 개발 메모

- `slate_rename/parser.py` — 콜 텍스트 → 씬/테이크 (단위 테스트: `python tests/test_parser.py`, 39케이스 통과)
- `slate_rename/audio.py` — PyAV로 구간 오디오 추출, 말소리 구간 탐지
- `slate_rename/asr.py` — 인식 백엔드(faster-whisper / sherpa-onnx)
- `slate_rename/cli.py` — 파일 처리, 로그, 되돌리기, 감시 모드
- `tests/make_fixtures.py` — 합성 음성으로 테스트 클립 생성 (sherpa-onnx TTS 필요)

검증 상태: 파이프라인 전체(추출→인식→파싱→변경→정리표→재실행 건너뜀→되돌리기)는 합성 클립으로 동작 확인됨.
단, 사용한 한국어 합성 음성 품질이 낮아("삼"이 "사"처럼 발음됨) **실제 사람 목소리에 대한 인식률은 아직 측정되지 않음.** 5번 항목대로 실측 필요.
