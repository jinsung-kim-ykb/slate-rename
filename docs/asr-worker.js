// 음성 인식 워커: transformers.js 로 Whisper 를 브라우저 안에서 실행한다. (서버로 아무것도 보내지 않음)
// 모델 파일은 첫 사용 때 Hugging Face 에서 내려받아 브라우저 캐시에 저장된다.
let transformers = null;
let pipe = null;
let loadedKey = '';

async function loadLib() {
  if (transformers) return transformers;
  transformers = await import('https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.7.0/dist/transformers.min.js');
  transformers.env.allowLocalModels = false;
  return transformers;
}

async function pickDevice(pref) {
  if (pref === 'wasm') return 'wasm';
  if (typeof navigator !== 'undefined' && navigator.gpu) {
    try { const a = await navigator.gpu.requestAdapter(); if (a) return 'webgpu'; } catch (e) { /* fall through */ }
  }
  return 'wasm';
}

self.onmessage = async (e) => {
  const msg = e.data;
  try {
    if (msg.type === 'load') {
      const { pipeline } = await loadLib();
      const device = await pickDevice(msg.device || 'auto');
      const key = `${msg.model}|${device}`;
      if (pipe && loadedKey === key) { self.postMessage({ type: 'ready', device, model: msg.model, cached: true }); return; }
      if (pipe) { await pipe.dispose?.(); pipe = null; }
      const dtype = device === 'webgpu'
        ? { encoder_model: 'fp32', decoder_model_merged: 'q4' }
        : 'q8';
      pipe = await pipeline('automatic-speech-recognition', msg.model, {
        device, dtype,
        progress_callback: (p) => self.postMessage({ type: 'progress', ...p }),
      });
      loadedKey = key;
      self.postMessage({ type: 'ready', device, model: msg.model });
    } else if (msg.type === 'transcribe') {
      if (!pipe) throw new Error('모델이 아직 로드되지 않음');
      const out = await pipe(msg.audio, {
        language: msg.language || 'ko', task: 'transcribe',
        return_timestamps: false, chunk_length_s: 30,
      });
      self.postMessage({ type: 'result', id: msg.id, text: (out.text || '').trim() });
    }
  } catch (err) {
    self.postMessage({ type: 'error', id: msg.id, message: err && err.message ? err.message : String(err) });
  }
};
