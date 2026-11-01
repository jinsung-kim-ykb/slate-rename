"""영상/오디오 파일에서 특정 구간의 오디오만 16kHz 모노로 뽑아낸다 (PyAV 사용, ffmpeg 설치 불필요)."""

from __future__ import annotations

from typing import Optional

import av
import numpy as np

TARGET_SR = 16000


def media_duration(path: str) -> Optional[float]:
    """파일 길이(초). 알 수 없으면 None."""
    try:
        with av.open(path) as container:
            if container.duration is not None:
                return container.duration / av.time_base
            for s in container.streams:
                if s.duration is not None and s.time_base is not None:
                    return float(s.duration * s.time_base)
    except Exception:
        return None
    return None


def load_audio_segment(path: str, start: float = 0.0, duration: float = 15.0,
                       sr: int = TARGET_SR) -> np.ndarray:
    """path 의 start초부터 duration초 만큼을 float32 모노 [-1,1] 배열로 돌려준다.

    오디오 스트림이 없으면 빈 배열을 돌려준다.
    """
    chunks: list[np.ndarray] = []
    got = 0.0
    with av.open(path) as container:
        if not container.streams.audio:
            return np.zeros(0, dtype=np.float32)
        stream = container.streams.audio[0]
        stream.thread_type = "AUTO"
        if start > 0:
            # container.seek 은 av.time_base(마이크로초) 단위
            container.seek(int(start * av.time_base), any_frame=False, backward=True)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sr)
        started = False
        for frame in container.decode(stream):
            if not started:
                # seek 이 키프레임 앞으로 떨어질 수 있으므로 start 이전 프레임은 버림
                if frame.time is not None and frame.time < start - 0.05:
                    continue
                started = True
            for out in resampler.resample(frame):
                arr = out.to_ndarray()  # shape (1, n) int16
                pcm = arr.reshape(-1).astype(np.float32) / 32768.0
                chunks.append(pcm)
                got += len(pcm) / sr
            if got >= duration:
                break
        # resampler flush
        for out in resampler.resample(None):
            arr = out.to_ndarray()
            chunks.append(arr.reshape(-1).astype(np.float32) / 32768.0)
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    audio = np.concatenate(chunks)
    return audio[: int(duration * sr)]


def speech_bursts(audio: np.ndarray, sr: int = TARGET_SR, frame_ms: int = 30,
                  min_len: float = 0.3, gap: float = 0.6) -> list[tuple[float, float]]:
    """에너지 기준으로 말소리 구간(시작, 끝)을 찾는다. 슬레이트 콜 주변만 잘라 다시 듣기 위한 용도."""
    if audio.size == 0:
        return []
    n = int(sr * frame_ms / 1000)
    frames = audio[: len(audio) // n * n].reshape(-1, n)
    energy = 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-9)
    floor = np.percentile(energy, 20)
    thresh = max(floor + 12, -45.0)
    active = energy > thresh
    bursts: list[list[float]] = []
    for i, a in enumerate(active):
        t = i * frame_ms / 1000
        if a:
            if bursts and t - bursts[-1][1] <= gap:
                bursts[-1][1] = t + frame_ms / 1000
            else:
                bursts.append([t, t + frame_ms / 1000])
    return [(s, e) for s, e in bursts if e - s >= min_len]


def pad_silence(audio: np.ndarray, sr: int = TARGET_SR, seconds: float = 0.8) -> np.ndarray:
    z = np.zeros(int(sr * seconds), dtype=np.float32)
    return np.concatenate([z, audio, z])


def rms_db(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    return 20 * np.log10(rms + 1e-12)
