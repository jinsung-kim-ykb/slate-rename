"""slate-rename 명령줄 도구.

사용 예:
    python slate_rename.py /경로/촬영폴더                 # 폴더 안 촬영본을 S12_T03_원본명 으로 변경
    python slate_rename.py /경로/촬영폴더 --dry-run       # 바꾸지 않고 결과만 보기
    python slate_rename.py /경로/촬영폴더 --watch         # 새 파일이 들어올 때마다 자동 처리
    python slate_rename.py --undo /경로/촬영폴더/slate_rename_log.csv   # 되돌리기
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from . import __version__
from .audio import load_audio_segment, media_duration, rms_db, speech_bursts, pad_silence
from .asr import Transcriber, DEFAULT_PROMPT
from .parser import parse_slate, detect_ok_call, SlateInfo

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mxf", ".mts", ".m2ts", ".avi", ".mkv", ".insv"}
AUDIO_EXTS = {".wav", ".bwf", ".mp3", ".m4a", ".flac", ".aif", ".aiff"}
LOG_NAME = "slate_rename_log.csv"
INDEX_NAME = "slate_index.csv"
ALREADY_RE = re.compile(r"^S\d{2,3}[A-Z]?_T\d{2}", re.IGNORECASE)

LOG_FIELDS = ["time", "status", "old_path", "new_path", "scene", "suffix", "shot", "take",
              "ok_call", "text", "tail_text", "avg_logprob", "no_speech_prob", "note"]


@dataclass
class Result:
    old_path: str
    new_path: str = ""
    status: str = ""          # renamed / dry-run / unrecognized / skipped / error
    scene: Optional[int] = None
    suffix: str = ""
    shot: Optional[int] = None
    take: Optional[int] = None
    ok_call: str = ""
    text: str = ""
    tail_text: str = ""
    avg_logprob: Optional[float] = None
    no_speech_prob: Optional[float] = None
    note: str = ""


# ---------------------------------------------------------------------------

def iter_media(paths: Iterable[str], recursive: bool, exts: set[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_file():
            if path.suffix.lower() in exts:
                files.append(path)
        elif path.is_dir():
            it = path.rglob("*") if recursive else path.iterdir()
            for f in it:
                if f.is_file() and f.suffix.lower() in exts and not f.name.startswith("."):
                    files.append(f)
    return sorted(set(files))


def build_new_name(path: Path, info: SlateInfo, template: str, cam: str, keep_original: bool,
                   ok_suffix: str) -> Path:
    label = info.label(template)
    parts = [label]
    if cam:
        parts.append(cam)
    if ok_suffix:
        parts.append(ok_suffix)
    if keep_original:
        parts.append(path.stem)
    new_name = "_".join(parts) + path.suffix
    candidate = path.with_name(new_name)
    n = 1
    while candidate.exists() and candidate.resolve() != path.resolve():
        candidate = path.with_name(f"{'_'.join(parts)}_dup{n}{path.suffix}")
        n += 1
    return candidate


def recognize_slate(audio, tr: Transcriber, args):
    """앞부분 전체 + 말소리 구간만 잘라낸 짧은 창을 차례로 들어 보고, 먼저 씬/테이크가 나오는 결과를 쓴다.

    짧은 콜("12의 3")은 15초짜리 긴 창 안에서는 잘 안 잡히고, 말소리 주변 3~4초만 잘라 주면 훨씬 잘 잡힌다.
    """
    candidates = []
    full = tr.transcribe(pad_silence(audio))
    candidates.append(("full", full))
    bursts = speech_bursts(audio)
    sr = 16000
    for i, (s, e) in enumerate(bursts[:3]):   # 앞쪽 말소리 최대 3군데
        a, b = max(0.0, s - 0.4), min(len(audio) / sr, max(e + 0.6, s + 2.5))
        clip = audio[int(a * sr): int(b * sr)]
        if clip.size < sr * 0.5:
            continue
        candidates.append((f"burst{i}@{s:.1f}s", tr.transcribe(pad_silence(clip))))
        # 연속된 두 구간("12의" ... "3")이 끊겨 들렸을 수 있으니 다음 구간까지 합친 창도 시도
        if i + 1 < len(bursts):
            s2, e2 = bursts[i + 1]
            if s2 - e < 1.5:
                clip2 = audio[int(a * sr): int(min(len(audio) / sr, e2 + 0.6) * sr)]
                candidates.append((f"burst{i}+{i+1}", tr.transcribe(pad_silence(clip2))))
    heard_parts = []
    best_info, best_t = None, full
    for name, t in candidates:
        heard_parts.append(f"{name}: {t.text!r}")
        if best_info is None:
            info = parse_slate(t.text, max_scene=args.max_scene, max_take=args.max_take)
            if info.complete:
                best_info, best_t = info, t
                if not args.verbose:
                    break
    if best_info is None:
        best_info = parse_slate(full.text, max_scene=args.max_scene, max_take=args.max_take)
    heard = best_t.text if (best_info.complete and not args.verbose) else " | ".join(heard_parts)
    return best_info, best_t, heard


def process_file(path: Path, tr: Transcriber, args, results: list[Result]) -> Result:
    res = Result(old_path=str(path))
    if ALREADY_RE.match(path.name) and not args.force:
        res.status, res.note = "skipped", "이미 씬/테이크 이름"
        return res
    try:
        audio = load_audio_segment(str(path), start=args.offset, duration=args.seconds)
        level = rms_db(audio)
        if audio.size == 0:
            res.status, res.note = "unrecognized", "오디오 트랙 없음"
            return res
        info, t, heard = recognize_slate(audio, tr, args)
        res.text, res.avg_logprob, res.no_speech_prob = heard, t.avg_logprob, t.no_speech_prob

        if args.ok:
            dur = media_duration(str(path))
            if dur and dur > args.seconds + 2:
                tail = load_audio_segment(str(path), start=max(0.0, dur - args.tail_seconds),
                                          duration=args.tail_seconds)
                tt = tr.transcribe(tail)
                res.tail_text = tt.text
                ok = detect_ok_call(tt.text)
                res.ok_call = {True: "OK", False: "NG", None: ""}[ok]

        if not info.complete:
            res.status = "unrecognized"
            res.note = f"파싱 실패 (레벨 {level:.0f} dBFS)" + (
                "; 말소리 없음 가능성" if (t.no_speech_prob or 0) > 0.6 else "")
            return res

        res.scene, res.suffix, res.shot, res.take = info.scene, info.scene_suffix, info.shot, info.take
        new_path = build_new_name(path, info, args.template, args.cam, not args.no_keep_original,
                                  res.ok_call if args.ok and res.ok_call == "OK" else "")
        res.new_path = str(new_path)
        if args.dry_run:
            res.status = "dry-run"
        else:
            os.rename(path, new_path)
            res.status = "renamed"
    except Exception as e:  # noqa: BLE001
        res.status, res.note = "error", f"{type(e).__name__}: {e}"
    return res


# ---------------------------------------------------------------------------

def append_log(log_path: Path, rows: list[Result]):
    new = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        for r in rows:
            d = asdict(r)
            d["time"] = datetime.now().isoformat(timespec="seconds")
            w.writerow({k: d.get(k, "") for k in LOG_FIELDS})


def write_index(folder: Path, rows: list[Result]):
    """씬/테이크 순으로 정렬된 정리표. 편집 프로그램 옆에 띄워두고 보는 용도."""
    index_path = folder / INDEX_NAME
    existing: dict[str, dict] = {}
    if index_path.exists():
        with index_path.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                existing[row["file"]] = row
    for r in rows:
        if r.status in ("renamed", "dry-run"):
            existing[Path(r.new_path).name] = {
                "scene": f"{r.scene}{r.suffix}", "shot": r.shot or "", "take": r.take,
                "ok": r.ok_call, "file": Path(r.new_path).name, "original": Path(r.old_path).name,
                "heard": r.text}
        elif r.status == "unrecognized":
            existing[Path(r.old_path).name] = {
                "scene": "?", "shot": "", "take": "?", "ok": r.ok_call, "file": Path(r.old_path).name,
                "original": Path(r.old_path).name, "heard": r.text}

    def sort_key(row):
        m = re.match(r"(\d+)", str(row["scene"]))
        return (0 if m else 1, int(m.group(1)) if m else 0, str(row["scene"]),
                int(row["take"]) if str(row["take"]).isdigit() else 999, row["file"])

    with index_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["scene", "shot", "take", "ok", "file", "original", "heard"])
        w.writeheader()
        for row in sorted(existing.values(), key=sort_key):
            w.writerow(row)


def undo(log_path: Path) -> int:
    rows = list(csv.DictReader(log_path.open(encoding="utf-8-sig")))
    done = 0
    for row in reversed(rows):
        if row["status"] != "renamed":
            continue
        new, old = Path(row["new_path"]), Path(row["old_path"])
        if new.exists() and not old.exists():
            os.rename(new, old)
            print(f"  되돌림: {new.name}  ->  {old.name}")
            row["status"] = "undone"
            done += 1
    with log_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return done


def print_table(rows: list[Result]):
    for r in rows:
        old = Path(r.old_path).name
        if r.status in ("renamed", "dry-run"):
            mark = "✔" if r.status == "renamed" else "·"
            ok = f" [{r.ok_call}]" if r.ok_call else ""
            print(f"  {mark} {old:28} -> {Path(r.new_path).name}{ok}    (들린 말: {r.text!r})")
        elif r.status == "skipped":
            print(f"  - {old:28}    건너뜀: {r.note}")
        else:
            print(f"  ✘ {old:28}    {r.status}: {r.note}    (들린 말: {r.text!r})")


def run_once(paths: list[str], tr: Transcriber, args, seen: set[str]) -> list[Result]:
    exts = set()
    if not args.audio_only:
        exts |= VIDEO_EXTS
    if not args.video_only:
        exts |= AUDIO_EXTS
    files = [f for f in iter_media(paths, args.recursive, exts) if str(f) not in seen]
    results: list[Result] = []
    for f in files:
        seen.add(str(f))
        r = process_file(f, tr, args, results)
        results.append(r)
        print_table([r])
        if r.status == "renamed":
            seen.add(r.new_path)
    return results


def wait_until_stable(path: Path, interval: float = 2.0, rounds: int = 2) -> bool:
    """복사 중인 파일인지 확인: 크기가 rounds 번 연속 같으면 안정된 것으로 본다."""
    last = -1
    same = 0
    for _ in range(60):
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == last:
            same += 1
            if same >= rounds:
                return True
        else:
            same = 0
        last = size
        time.sleep(interval)
    return False


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="slate_rename",
        description="촬영본 앞부분의 슬레이트 음성 콜(\"12의 3\")을 듣고 파일명을 S12_T03_원본명 으로 바꿔줍니다.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("paths", nargs="*", help="촬영본 폴더 또는 파일")
    p.add_argument("--dry-run", action="store_true", help="파일명을 바꾸지 않고 결과만 출력")
    p.add_argument("--watch", action="store_true", help="폴더를 감시하다가 새 파일이 들어오면 자동 처리")
    p.add_argument("--undo", metavar="LOG_CSV", help="해당 로그 파일 기준으로 이름 변경을 되돌림")
    p.add_argument("-r", "--recursive", action="store_true", help="하위 폴더까지 처리")
    p.add_argument("--seconds", type=float, default=15.0, help="앞에서 몇 초를 들을지")
    p.add_argument("--offset", type=float, default=0.0, help="앞에서 몇 초를 건너뛰고 들을지")
    p.add_argument("--ok", action="store_true", help="클립 끝부분도 들어서 OK/NG 콜을 판별 (시간 2배)")
    p.add_argument("--tail-seconds", type=float, default=8.0, help="--ok 사용 시 끝에서 몇 초를 들을지")
    p.add_argument("--template", default="S{scene:02d}{suffix}_T{take:02d}",
                   help="이름 앞부분 형식. 사용 가능: {scene} {suffix} {shot} {take}")
    p.add_argument("--cam", default="", help="카메라 표시 (예: A, B). 여러 대 촬영 시 폴더별로 지정")
    p.add_argument("--no-keep-original", action="store_true", help="원본 파일명을 뒤에 붙이지 않음")
    p.add_argument("--force", action="store_true", help="이미 씬/테이크 이름인 파일도 다시 처리")
    p.add_argument("-v", "--verbose", action="store_true", help="모든 인식 후보를 출력 (디버그)")
    p.add_argument("--video-only", action="store_true")
    p.add_argument("--audio-only", action="store_true")
    p.add_argument("--max-scene", type=int, default=999)
    p.add_argument("--max-take", type=int, default=99)
    p.add_argument("--model", default="small", help="Whisper 모델 크기: tiny/base/small/medium/large-v3 (faster-whisper)")
    p.add_argument("--backend", default="auto", choices=["auto", "faster-whisper", "sherpa-onnx"])
    p.add_argument("--model-dir", default=None, help="모델 저장/탐색 폴더")
    p.add_argument("--device", default="auto", help="cpu / cuda / auto")
    p.add_argument("--language", default="ko")
    p.add_argument("--prompt", default=DEFAULT_PROMPT, help="인식 유도 문구")
    p.add_argument("--log", default=None, help=f"로그 CSV 경로 (기본: 첫 폴더/{LOG_NAME})")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.undo:
        n = undo(Path(args.undo))
        print(f"{n}개 파일을 되돌렸습니다.")
        return 0
    if not args.paths:
        build_parser().print_help()
        return 1

    first_dir = Path(args.paths[0])
    first_dir = first_dir if first_dir.is_dir() else first_dir.parent
    log_path = Path(args.log) if args.log else first_dir / LOG_NAME

    print(f"모델 불러오는 중 ({args.backend}, {args.model_dir or args.model}) ...", flush=True)
    t0 = time.time()
    tr = Transcriber(backend=args.backend, model=args.model, device=args.device,
                     model_dir=args.model_dir, language=args.language, prompt=args.prompt)
    print(f"준비 완료 ({tr.backend}, {time.time() - t0:.1f}s). {'[미리보기 모드]' if args.dry_run else ''}\n")

    seen: set[str] = set()
    results = run_once(args.paths, tr, args, seen)
    if results:
        append_log(log_path, results)
        write_index(first_dir, results)
    summarize(results, log_path, first_dir)

    if args.watch:
        print(f"\n감시 중... (새 파일이 들어오면 자동 처리, 종료는 Ctrl+C)")
        try:
            while True:
                time.sleep(3)
                exts = (set() if args.audio_only else VIDEO_EXTS) | (set() if args.video_only else AUDIO_EXTS)
                new_files = [f for f in iter_media(args.paths, args.recursive, exts) if str(f) not in seen]
                for f in new_files:
                    if not wait_until_stable(f):
                        continue
                    batch = run_once([str(f)], tr, args, seen)
                    if batch:
                        append_log(log_path, batch)
                        write_index(first_dir, batch)
        except KeyboardInterrupt:
            print("\n감시 종료.")
    return 0


def summarize(results: list[Result], log_path: Path, folder: Path):
    n = len(results)
    ok = sum(r.status in ("renamed", "dry-run") for r in results)
    bad = [r for r in results if r.status in ("unrecognized", "error")]
    print(f"\n총 {n}개 중 {ok}개 인식, {len(bad)}개 확인 필요, "
          f"{sum(r.status == 'skipped' for r in results)}개 건너뜀")
    if bad:
        print("확인 필요한 파일 (이름 그대로 둠):")
        for r in bad:
            print(f"  - {Path(r.old_path).name}: {r.note}  / 들린 말: {r.text!r}")
    if n:
        print(f"\n로그: {log_path}\n정리표: {folder / INDEX_NAME}")
        print(f"되돌리기: python slate_rename.py --undo \"{log_path}\"")
