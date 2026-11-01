"""음성 인식 백엔드. 기본은 faster-whisper, 대안으로 sherpa-onnx(whisper ONNX)를 지원한다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import numpy as np

# 전사 결과를 숫자 형태("12의 3")로 유도하기 위한 프롬프트
DEFAULT_PROMPT = "슬레이트. 12의 3. 7의 1. 씬 12 테이크 3. 5의 2. 마크."


@dataclass
class Transcript:
    text: str
    avg_logprob: Optional[float] = None     # 높을수록(0에 가까울수록) 확신
    no_speech_prob: Optional[float] = None  # 1에 가까우면 말소리가 없다고 판단
    backend: str = ""


class Transcriber:
    def __init__(self, backend: str = "auto", model: str = "small", device: str = "auto",
                 compute_type: str = "auto", model_dir: Optional[str] = None,
                 language: str = "ko", prompt: str = DEFAULT_PROMPT):
        self.language = language
        self.prompt = prompt
        self.model_name = model
        self.backend = backend
        self._impl = None

        if backend == "auto":
            backend = "faster-whisper"
            try:
                import faster_whisper  # noqa: F401
            except ImportError:
                backend = "sherpa-onnx"
        self.backend = backend

        if backend == "faster-whisper":
            from faster_whisper import WhisperModel
            if device == "auto":
                device = "cuda" if _cuda_available() else "cpu"
            if compute_type == "auto":
                compute_type = "float16" if device == "cuda" else "int8"
            self._impl = WhisperModel(model, device=device, compute_type=compute_type,
                                      download_root=model_dir)
        elif backend == "sherpa-onnx":
            import sherpa_onnx
            if not model_dir:
                raise ValueError("sherpa-onnx 백엔드는 --model-dir (모델 폴더)가 필요합니다")
            threads = max(1, (os.cpu_count() or 2) // 2)
            tok = _find(model_dir, "tokens.txt", exact=True)
            names = os.listdir(model_dir)
            if any("joiner" in n for n in names):            # zipformer transducer (예: 한국어 전용 모델)
                self.sherpa_kind = "transducer"
                self._impl = sherpa_onnx.OfflineRecognizer.from_transducer(
                    encoder=_find(model_dir, "encoder"), decoder=_find(model_dir, "decoder"),
                    joiner=_find(model_dir, "joiner"), tokens=tok, num_threads=threads)
            elif any("encoder" in n for n in names):         # whisper onnx
                self.sherpa_kind = "whisper"
                self._impl = sherpa_onnx.OfflineRecognizer.from_whisper(
                    encoder=_find(model_dir, "encoder"), decoder=_find(model_dir, "decoder"),
                    tokens=tok, language=language, task="transcribe", num_threads=threads)
            else:                                             # sense-voice (model.onnx 하나)
                self.sherpa_kind = "sense-voice"
                self._impl = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=_find(model_dir, "model"), tokens=tok, language=language,
                    use_itn=True, num_threads=threads)
        else:
            raise ValueError(f"알 수 없는 백엔드: {backend}")

    def transcribe(self, audio: np.ndarray, sr: int = 16000) -> Transcript:
        if audio.size == 0:
            return Transcript(text="", no_speech_prob=1.0, backend=self.backend)
        if self.backend == "faster-whisper":
            segments, _info = self._impl.transcribe(
                audio, language=self.language, beam_size=5, best_of=5,
                initial_prompt=self.prompt, condition_on_previous_text=False,
                vad_filter=False, temperature=[0.0, 0.2, 0.4],
                suppress_blank=True)
            texts, lps, nsp = [], [], []
            for seg in segments:
                texts.append(seg.text.strip())
                lps.append(seg.avg_logprob)
                nsp.append(seg.no_speech_prob)
            return Transcript(
                text=" ".join(t for t in texts if t),
                avg_logprob=float(np.mean(lps)) if lps else None,
                no_speech_prob=float(np.min(nsp)) if nsp else 1.0,
                backend=self.backend)
        else:  # sherpa-onnx (whisper 는 30초 제한)
            stream = self._impl.create_stream()
            limit = sr * 30 if getattr(self, "sherpa_kind", "") == "whisper" else len(audio)
            stream.accept_waveform(sr, audio[:limit].astype(np.float32))
            self._impl.decode_stream(stream)
            return Transcript(text=stream.result.text.strip(), backend=self.backend)


def _cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _find(folder: str, key: str, exact: bool = False) -> str:
    names = sorted(os.listdir(folder))
    if exact:
        hits = [n for n in names if n.endswith(key)]
    else:
        hits = [n for n in names if key in n and n.endswith(".onnx") and "decoder" not in n.replace(key, "")
                and "encoder" not in n.replace(key, "") and "joiner" not in n.replace(key, "")]
        # int8 보다 일반(float) 모델을 우선, 하나뿐이면 그걸 쓴다
        hits.sort(key=lambda n: ("int8" in n, n))
    if not hits:
        raise FileNotFoundError(f"{folder} 안에 {key} 파일이 없습니다")
    return os.path.join(folder, hits[0])
