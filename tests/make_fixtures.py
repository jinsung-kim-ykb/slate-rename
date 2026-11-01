"""합성 음성으로 테스트용 촬영본을 만든다 (실제 촬영본이 없을 때 파이프라인 검증용).

python tests/make_fixtures.py <tts_model_dir> <out_dir>
"""
import subprocess, sys, wave
from pathlib import Path

import numpy as np
import sherpa_onnx

tts_dir = Path(sys.argv[1])
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)

cfg = sherpa_onnx.OfflineTtsConfig(
    model=sherpa_onnx.OfflineTtsModelConfig(
        vits=sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(tts_dir / "ko_KO-kss_low.onnx"),
            tokens=str(tts_dir / "tokens.txt"),
            data_dir=str(tts_dir / "espeak-ng-data"),
        ),
        num_threads=2,
    ),
)
tts = sherpa_onnx.OfflineTts(cfg)

rng = np.random.default_rng(0)


def synth(text: str) -> tuple[np.ndarray, int]:
    a = tts.generate(text, sid=0, speed=1.0)
    return np.array(a.samples, dtype=np.float32), a.sample_rate


def clap(sr: int) -> np.ndarray:
    n = int(0.08 * sr)
    env = np.exp(-np.linspace(0, 8, n))
    return (rng.standard_normal(n) * env * 0.8).astype(np.float32)


def make_take(call: str, tail: str, total_sec: float, sr_out: int = 48000):
    speech, sr = synth(call)
    tail_speech, _ = synth(tail) if tail else (np.zeros(0, np.float32), sr)
    # 1.5초 정적 -> 콜 -> 0.4초 -> 박수(클랩) -> 본 촬영(약한 소음) -> 끝부분에 OK/NG 콜
    sil = lambda s: np.zeros(int(s * sr), np.float32)
    body_len = int(total_sec * sr) - len(speech) - len(tail_speech) - int(2.3 * sr)
    body = (rng.standard_normal(max(body_len, sr)) * 0.01).astype(np.float32)
    audio = np.concatenate([sil(1.5), speech, sil(0.4), clap(sr), body, tail_speech, sil(0.4)])
    audio += rng.standard_normal(len(audio)).astype(np.float32) * 0.004  # 룸톤
    audio = np.clip(audio, -1, 1)
    # 48k 로 리샘플 (단순 선형 보간) - 카메라 녹음 포맷 흉내
    t_in = np.arange(len(audio)) / sr
    t_out = np.arange(int(len(audio) * sr_out / sr)) / sr_out
    return np.interp(t_out, t_in, audio).astype(np.float32), sr_out


def write_wav(path: Path, audio: np.ndarray, sr: int):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())


TAKES = [
    # (원본 파일명, 콜, 끝부분 말, 길이)
    ("C0001.MP4", "십이의 삼", "오케이 컷", 24),
    ("C0002.MP4", "십이의 사", "컷, 다시 갈게요", 20),
    ("C0003.MP4", "씬 칠, 테이크 일", "컷", 18),
    ("C0004.MP4", "십이의 오", "좋았어, 오케이", 22),
    ("C0005.MP4", "삼의 이", "", 16),
    ("C0006.MP4", "자 갑니다, 오의 일, 마크", "오케이", 19),
    ("C0007.MP4", "레디, 액션", "컷", 17),          # 콜 없음 -> 인식 실패가 정상
    ("C0008.MP4", "씬 십이, 테이크 삼", "오케이", 20),
    ("C0009.MP4", "십이 번 씬, 삼 번 테이크", "컷", 20),
    ("C0010.MP4", "열둘에 셋", "오케이", 18),
    ("ZOOM0001.WAV", "십이의 삼", "오케이 컷", 24),   # 외부 녹음기 파일
]

for name, call, tail, sec in TAKES:
    audio, sr = make_take(call, tail, sec)
    wav = out / (Path(name).stem + "_tmp.wav")
    write_wav(wav, audio, sr)
    target = out / name
    if name.lower().endswith(".wav"):
        wav.rename(target)
    else:
        # 검은 화면 + 오디오 = 가짜 촬영본 (작게)
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black:s=320x180:r=24:d={sec}",
            "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
            str(target)], check=True)
        wav.unlink()
    print("made", target.name, f"({call!r} / {tail!r})")
