"""slate-rename 웹 데모 (Gradio).

    python app.py              # http://127.0.0.1:7860
    SHARE=1 python app.py      # 72시간짜리 임시 공개 링크도 함께 생성 (남에게 보여줄 때)

Hugging Face Spaces 에 올릴 때는 이 파일과 slate_rename/ 폴더, requirements.txt 를 그대로 올리면 됩니다.
환경변수:
    SLATE_BACKEND   faster-whisper(기본) / sherpa-onnx
    SLATE_MODEL     small(기본) / base / medium ...
    SLATE_MODEL_DIR sherpa-onnx 모델 폴더
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import gradio as gr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from slate_rename.asr import Transcriber, DEFAULT_PROMPT  # noqa: E402
from slate_rename.audio import load_audio_segment, media_duration  # noqa: E402
from slate_rename.cli import recognize_slate, build_new_name, VIDEO_EXTS, AUDIO_EXTS  # noqa: E402
from slate_rename.parser import detect_ok_call  # noqa: E402

_TR: Transcriber | None = None


def get_transcriber() -> Transcriber:
    global _TR
    if _TR is None:
        _TR = Transcriber(
            backend=os.environ.get("SLATE_BACKEND", "auto"),
            model=os.environ.get("SLATE_MODEL", "small"),
            model_dir=os.environ.get("SLATE_MODEL_DIR") or None,
            language="ko", prompt=DEFAULT_PROMPT)
    return _TR


def run(files: list[str] | None, seconds: float, detect_ok: bool, template: str,
        progress=gr.Progress()):
    if not files:
        raise gr.Error("촬영본 파일을 먼저 올려 주세요.")
    tr = get_transcriber()
    args = SimpleNamespace(max_scene=999, max_take=99, verbose=False)
    workdir = Path(tempfile.mkdtemp(prefix="slate_demo_"))
    rows, renamed = [], []
    t_start = time.time()
    for i, f in enumerate(files):
        src = Path(f)
        progress((i, len(files)), desc=f"{src.name} 듣는 중...")
        if src.suffix.lower() not in VIDEO_EXTS | AUDIO_EXTS:
            rows.append([src.name, "-", "", "", "", "지원하지 않는 형식"])
            continue
        local = workdir / src.name
        shutil.copy(src, local)
        try:
            audio = load_audio_segment(str(local), 0, seconds)
            info, t, heard = recognize_slate(audio, tr, args)
            ok = ""
            if detect_ok:
                dur = media_duration(str(local))
                if dur and dur > seconds + 2:
                    tail = load_audio_segment(str(local), max(0.0, dur - 8), 8)
                    ok = {True: "OK", False: "NG", None: ""}[detect_ok_call(tr.transcribe(tail).text)]
            if info.complete:
                new_path = build_new_name(local, info, template, "", True, "OK" if ok == "OK" else "")
                local.rename(new_path)
                renamed.append(new_path)
                rows.append([src.name, heard, f"{info.scene}{info.scene_suffix}", str(info.take), ok,
                             new_path.name])
            else:
                renamed.append(local)
                rows.append([src.name, heard, "?", "?", ok, "(그대로 둠 — 확인 필요)"])
        except Exception as e:  # noqa: BLE001
            rows.append([src.name, "", "", "", "", f"오류: {e}"])

    # 결과 zip (이름 바뀐 사본 + 정리표)
    csv_path = workdir / "slate_index.csv"
    with csv_path.open("w", encoding="utf-8-sig") as fp:
        fp.write("원본,들린 말,씬,테이크,OK,새 이름\n")
        for r in rows:
            fp.write(",".join('"' + str(c).replace('"', '""') + '"' for c in r) + "\n")
    zip_path = workdir / "renamed.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
        z.write(csv_path, csv_path.name)
        for p in renamed:
            z.write(p, p.name)

    n_ok = sum(1 for r in rows if r[2] not in ("?", "", "-"))
    summary = (f"**{len(rows)}개 중 {n_ok}개 인식**, {len(rows) - n_ok}개 확인 필요 · "
               f"{time.time() - t_start:.1f}초 소요 ({tr.backend})")
    return rows, summary, str(zip_path), str(csv_path)


DESC = """
촬영본 앞 15초에서 슬레이트 콜(**"12의 3"**, **"씬 12 테이크 3"**)을 듣고 파일명을 `S12_T03_원본명` 으로 바꿔 줍니다.
못 알아들은 파일은 이름을 건드리지 않고 "확인 필요"로 표시합니다.

**써보기**: 슬레이트 콜이 들어 있는 짧은 클립(MP4/MOV/WAV) 몇 개를 올리고 실행을 누르세요.
데모 서버에는 파일이 저장되지 않으며, 결과는 이름이 바뀐 사본 zip 으로 받을 수 있습니다.
"""

with gr.Blocks(title="slate-rename 데모") as demo:
    gr.Markdown("# 🎬 slate-rename — 슬레이트 음성으로 촬영본 파일명 자동 정리")
    gr.Markdown(DESC)
    with gr.Row():
        with gr.Column(scale=1):
            files = gr.File(label="촬영본 (여러 개 가능)", file_count="multiple", type="filepath")
            seconds = gr.Slider(5, 30, value=15, step=1, label="앞에서 몇 초를 들을까요")
            detect_ok = gr.Checkbox(label="클립 끝부분에서 OK/NG 콜도 판별 (시간 2배)", value=False)
            template = gr.Textbox(value="S{scene:02d}{suffix}_T{take:02d}", label="이름 형식")
            btn = gr.Button("실행", variant="primary")
        with gr.Column(scale=2):
            summary = gr.Markdown()
            table = gr.Dataframe(headers=["원본", "들린 말", "씬", "테이크", "OK", "새 이름"],
                                 datatype=["str"] * 6, interactive=False, wrap=True,
                                 column_widths=["15%", "33%", "8%", "9%", "7%", "28%"])
            with gr.Row():
                zip_out = gr.File(label="이름 바뀐 사본 + 정리표 (zip)")
                csv_out = gr.File(label="정리표만 (CSV)")
    btn.click(run, [files, seconds, detect_ok, template], [table, summary, zip_out, csv_out])
    gr.Markdown(
        "실제 현장에서는 zip 을 받는 게 아니라 PC 에서 `python slate_rename.py 촬영폴더` 로 "
        "원본 파일명을 바로 바꾸고, `--watch` 로 카드 복사와 동시에 처리합니다. "
        "[GitHub 저장소 →](https://github.com/jinsung-kim-ykb/slate-rename)")

if __name__ == "__main__":
    demo.launch(share=os.environ.get("SHARE") == "1",
                server_name=os.environ.get("HOST", "127.0.0.1"),
                server_port=int(os.environ.get("PORT", "7860")))
