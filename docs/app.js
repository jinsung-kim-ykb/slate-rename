// slate-rename 브라우저 앱 본체
(() => {
  const $ = (s) => document.querySelector(s);
  const MEDIA = /\.(mp4|mov|m4v|m4a|wav|bwf|mxf|mts|m2ts|mp3|flac|aif|aiff)$/i;
  const BROWSER_OK = /\.(mp4|mov|m4v|m4a|wav|bwf|mp3|flac|aif|aiff)$/i;
  const ALREADY = /^S\d{2,3}[A-Z]?_T\d{2}/i;
  const MOCK = new URLSearchParams(location.search).get('mock') === '1';

  const state = { items: [], dirHandle: null, undo: [], busy: false };
  const { parseSlate, detectOkCall, label } = SlateParser;
  const { extractAudio, getDuration, speechBursts, padSilence, rmsDb, SR } = SlateAudio;

  // ---------------- 음성 인식 (워커 / 모의) ----------------
  const asr = {
    worker: null, ready: false, pending: new Map(), seq: 0, loadedModel: '',
    async load(model, onProgress) {
      if (MOCK) { this.ready = true; return { device: 'mock' }; }
      if (this.ready && this.loadedModel === model) return { device: this.device };
      if (!this.worker) {
        this.worker = new Worker('asr-worker.js', { type: 'module' });
        this.worker.onmessage = (e) => {
          const m = e.data;
          if (m.type === 'progress') onProgress && onProgress(m);
          else if (m.type === 'ready') { this.ready = true; this.device = m.device; this.loadedModel = model; this._resolveLoad && this._resolveLoad(m); }
          else if (m.type === 'result') { const p = this.pending.get(m.id); if (p) { p.resolve(m.text); this.pending.delete(m.id); } }
          else if (m.type === 'error') {
            if (m.id !== undefined && this.pending.has(m.id)) { this.pending.get(m.id).reject(new Error(m.message)); this.pending.delete(m.id); }
            else this._rejectLoad && this._rejectLoad(new Error(m.message));
          }
        };
        this.worker.onerror = (e) => { this._rejectLoad && this._rejectLoad(new Error(e.message || '워커 오류')); };
      }
      this.ready = false;
      return new Promise((resolve, reject) => {
        this._resolveLoad = resolve; this._rejectLoad = reject;
        this.worker.postMessage({ type: 'load', model, device: 'auto' });
      });
    },
    transcribe(audio, hint) {
      if (MOCK) return Promise.resolve(mockTranscript(hint, audio));
      const id = ++this.seq;
      return new Promise((resolve, reject) => {
        this.pending.set(id, { resolve, reject });
        this.worker.postMessage({ type: 'transcribe', id, audio, language: 'ko' }, [audio.buffer]);
      });
    },
  };
  // 모의 인식: 테스트용 (?mock=1). 파일명 숫자에서 가짜 콜을 만든다.
  function mockTranscript(hint, audio) {
    if (!hint) return '';
    if (hint.kind === 'tail') return hint.idx % 2 === 0 ? '오케이 컷' : '컷, 다시 갈게요';
    if (/0007/.test(hint.name)) return '레디 액션';
    const n = parseInt((hint.name.match(/(\d+)/) || [0, 1])[1], 10);
    return `${10 + (n % 5)}의 ${1 + (n % 3)}`;
  }

  async function recognizeSlate(audio, hint) {
    // 전체 창 → 말소리 구간만 자른 창 순서로 들어 보고, 먼저 씬/테이크가 나오는 결과 사용 (파이썬 버전과 동일)
    const heard = [];
    const tryText = async (name, clip) => {
      const text = await asr.transcribe(padSilence(clip.slice ? clip.slice() : new Float32Array(clip)), hint);
      heard.push(`${name}: ${text}`);
      const info = parseSlate(text);
      return info.complete ? { info, text } : null;
    };
    let r = await tryText('전체', audio);
    if (!r) {
      const bursts = speechBursts(audio).slice(0, 3);
      for (let i = 0; i < bursts.length && !r; i++) {
        const [s, e] = bursts[i];
        const a = Math.max(0, s - 0.4), b = Math.min(audio.length / SR, Math.max(e + 0.6, s + 2.5));
        if ((b - a) * SR < SR * 0.5) continue;
        r = await tryText(`구간${i + 1}`, audio.subarray(Math.floor(a * SR), Math.floor(b * SR)));
        if (!r && i + 1 < bursts.length && bursts[i + 1][0] - e < 1.5) {
          const b2 = Math.min(audio.length / SR, bursts[i + 1][1] + 0.6);
          r = await tryText(`구간${i + 1}+${i + 2}`, audio.subarray(Math.floor(a * SR), Math.floor(b2 * SR)));
        }
      }
    }
    if (r) return { info: r.info, heard: r.text };
    return { info: parseSlate(heard[0] ? heard[0].replace(/^전체: /, '') : ''), heard: heard.join(' | ') };
  }

  // ---------------- 파일 고르기 ----------------
  const supportsFS = 'showDirectoryPicker' in window;
  if (!supportsFS) {
    $('#pickDir').disabled = true;
    $('#fsHint').innerHTML = '이 브라우저는 폴더 선택·그 자리 이름 변경을 지원하지 않습니다 (Chrome 또는 Edge 를 쓰세요). 파일 선택 후 <b>이름 변경 스크립트</b>를 내려받아 실행하면 됩니다.';
  }
  $('#pickDir').onclick = async () => {
    try {
      const dir = await showDirectoryPicker({ mode: 'readwrite' });
      const items = [];
      for await (const [name, h] of dir.entries()) {
        if (h.kind === 'file' && MEDIA.test(name) && !name.startsWith('.')) items.push({ name, handle: h, file: await h.getFile() });
      }
      state.dirHandle = dir;
      setItems(items, `${dir.name} 폴더 · 파일 ${items.length}개`);
    } catch (e) { if (e.name !== 'AbortError') setStatus('폴더를 열지 못했습니다: ' + e.message, 'bad'); }
  };
  $('#pickFiles').onchange = (e) => {
    const files = Array.from(e.target.files).filter(f => MEDIA.test(f.name));
    state.dirHandle = null;
    setItems(files.map(f => ({ name: f.name, file: f, handle: null })), `파일 ${files.length}개 (스크립트 방식)`);
  };
  function setItems(items, info) {
    items.sort((a, b) => a.name.localeCompare(b.name, 'en', { numeric: true }));
    state.items = items.map((it, i) => ({ ...it, idx: i, selected: true, heard: '', info: null, ok: '', newName: '', status: '', note: '' }));
    state.undo = [];
    $('#pickInfo').textContent = info;
    $('#run').disabled = state.items.length === 0;
    $('#apply').disabled = $('#script').disabled = $('#csv').disabled = $('#undo').disabled = true;
    render();
  }

  // ---------------- 실행 ----------------
  $('#run').onclick = async () => {
    if (state.busy) return;
    state.busy = true; $('#run').disabled = true;
    const seconds = Math.max(3, Math.min(60, parseFloat($('#seconds').value) || 15));
    const detectOk = $('#detectOk').checked;
    try {
      showProgress(true, 0, '음성 인식 모델 준비 중...');
      const loaded = await asr.load($('#model').value, (p) => {
        if (p.status === 'progress' && p.file) showProgress(true, (p.progress || 0) / 100, `모델 내려받는 중 ${p.file.split('/').pop()} ${Math.round(p.progress || 0)}%`);
        else if (p.status === 'initiate') showProgress(true, 0, `모델 파일 준비: ${p.file || ''}`);
      });
      setStatus(`인식 엔진 준비 완료 (${loaded.device === 'webgpu' ? 'GPU 가속' : loaded.device === 'mock' ? '모의 모드' : 'CPU'}). 듣기 시작합니다.`);
      const t0 = performance.now();
      for (let i = 0; i < state.items.length; i++) {
        const it = state.items[i];
        showProgress(true, i / state.items.length, `${it.name} 듣는 중... (${i + 1}/${state.items.length})`);
        it.heard = ''; it.info = null; it.ok = ''; it.newName = ''; it.note = '';
        if (ALREADY.test(it.name)) { it.status = 'skip'; it.note = '이미 씬/테이크 이름'; render(); continue; }
        if (!BROWSER_OK.test(it.name)) { it.status = 'bad'; it.note = '브라우저에서 열 수 없는 형식 (명령줄 버전 사용)'; render(); continue; }
        try {
          const audio = await extractAudio(it.file, 0, seconds);
          if (!audio.length) throw new Error('오디오 트랙 없음');
          const level = rmsDb(audio);
          const { info, heard } = await recognizeSlate(audio, { name: it.name, idx: i, kind: 'head' });
          it.heard = heard;
          if (detectOk) {
            const dur = await getDuration(it.file);
            if (dur && dur > seconds + 2) {
              const tail = await extractAudio(it.file, Math.max(0, dur - 8), 8);
              const t = await asr.transcribe(padSilence(tail), { name: it.name, idx: i, kind: 'tail' });
              it.ok = { true: 'OK', false: 'NG', null: '' }[detectOkCall(t)];
            }
          }
          if (info.complete) { it.info = info; it.status = 'ok'; }
          else { it.status = 'warn'; it.note = `못 알아들음 (소리 크기 ${level.toFixed(0)} dBFS)`; }
        } catch (e) {
          it.status = 'bad'; it.note = '오류: ' + e.message;
        }
        computeNames(); render();
      }
      computeNames(); render();
      const n = state.items.filter(x => x.status === 'ok').length;
      setStatus(`${state.items.length}개 중 ${n}개 인식 · ${((performance.now() - t0) / 1000).toFixed(1)}초. 표에서 확인하고 "이름 바꾸기" 또는 "스크립트 받기"를 누르세요.`, 'ok');
      $('#apply').disabled = !state.dirHandle || n === 0;
      $('#script').disabled = n === 0; $('#csv').disabled = false;
    } catch (e) {
      setStatus('실패: ' + e.message + (MOCK ? '' : ' — 인터넷 연결(모델 다운로드)과 Chrome/Edge 사용 여부를 확인하세요.'), 'bad');
    } finally {
      showProgress(false); state.busy = false; $('#run').disabled = false;
    }
  };

  function computeNames() {
    const template = $('#template').value || 'S{scene:02d}{suffix}_T{take:02d}';
    const cam = ($('#cam').value || '').trim();
    const keep = $('#keepOriginal').checked;
    const taken = new Set(state.items.map(x => x.name.toLowerCase()));
    for (const it of state.items) {
      if (!it.info || !it.info.complete) { it.newName = ''; continue; }
      const ext = it.name.includes('.') ? it.name.slice(it.name.lastIndexOf('.')) : '';
      const stem = it.name.slice(0, it.name.length - ext.length);
      const parts = [label(it.info, template)];
      if (cam) parts.push(cam);
      if (it.ok === 'OK') parts.push('OK');
      if (keep) parts.push(stem);
      let base = parts.join('_'), cand = base + ext, n = 1;
      while (taken.has(cand.toLowerCase())) cand = `${base}_dup${n++}${ext}`;
      taken.add(cand.toLowerCase());
      it.newName = cand;
    }
  }
  ['#template', '#cam', '#keepOriginal'].forEach(s => $(s).addEventListener('input', () => { computeNames(); render(); }));

  // ---------------- 표 ----------------
  function render() {
    const tb = $('#table tbody'); tb.innerHTML = '';
    for (const it of state.items) {
      const tr = document.createElement('tr');
      tr.className = (it.status || '') + (it.renamed ? ' done' : '');
      const sc = it.info ? `${it.info.scene}${it.info.suffix || ''}` : '', tk = it.info ? it.info.take : '';
      tr.innerHTML = `
        <td><input type="checkbox" ${it.selected ? 'checked' : ''} data-i="${it.idx}" class="sel"></td>
        <td title="${esc(it.name)}">${esc(it.renamed ? it.renamedFrom : it.name)}</td>
        <td class="heard">${esc(it.heard)}</td>
        <td><input class="num" data-i="${it.idx}" data-k="scene" value="${esc(sc)}" placeholder="?"></td>
        <td><input class="num" data-i="${it.idx}" data-k="take" value="${esc(tk)}" placeholder="?"></td>
        <td>${esc(it.ok)}</td>
        <td><code>${esc(it.newName)}</code></td>
        <td class="state">${esc(it.renamed ? '✔ 변경됨' : it.note || (it.status === 'ok' ? '인식됨' : ''))}</td>`;
      tb.appendChild(tr);
    }
    tb.querySelectorAll('input.sel').forEach(cb => cb.onchange = () => { state.items[+cb.dataset.i].selected = cb.checked; });
    tb.querySelectorAll('input.num').forEach(inp => inp.onchange = () => {
      const it = state.items[+inp.dataset.i];
      const cur = it.info || { scene: null, suffix: '', shot: null, take: null };
      if (inp.dataset.k === 'scene') { const m = inp.value.trim().match(/^(\d+)([a-zA-Z]?)$/); if (m) { cur.scene = +m[1]; cur.suffix = m[2].toUpperCase(); } else { cur.scene = null; } }
      else { const v = parseInt(inp.value, 10); cur.take = Number.isFinite(v) ? v : null; }
      cur.complete = cur.scene !== null && cur.take !== null;
      it.info = cur; it.status = cur.complete ? 'ok' : 'warn'; it.note = cur.complete ? '직접 입력' : it.note;
      computeNames(); render();
      const n = state.items.filter(x => x.status === 'ok').length;
      $('#apply').disabled = !state.dirHandle || n === 0; $('#script').disabled = n === 0;
    });
  }
  $('#checkAll').onchange = (e) => { state.items.forEach(it => it.selected = e.target.checked); render(); };

  // ---------------- 이름 바꾸기 ----------------
  $('#apply').onclick = async () => {
    const targets = state.items.filter(it => it.selected && it.newName && it.handle && !it.renamed);
    if (!targets.length) { setStatus('바꿀 파일이 없습니다.', 'warn'); return; }
    if (!confirm(`${targets.length}개 파일의 이름을 바꿉니다. 계속할까요?`)) return;
    let done = 0, failed = 0;
    for (const it of targets) {
      try {
        if (typeof it.handle.move === 'function') await it.handle.move(it.newName);
        else throw new Error('이 브라우저는 그 자리 이름 변경(move)을 지원하지 않습니다 — 스크립트를 받아 실행하세요');
        state.undo.push({ handle: it.handle, from: it.name, to: it.newName });
        it.renamedFrom = it.name; it.name = it.newName; it.renamed = true; done++;
      } catch (e) { it.status = 'bad'; it.note = '변경 실패: ' + e.message; failed++; }
    }
    render();
    setStatus(`${done}개 변경 완료${failed ? `, ${failed}개 실패` : ''}.`, failed ? 'warn' : 'ok');
    $('#undo').disabled = state.undo.length === 0;
  };
  $('#undo').onclick = async () => {
    let n = 0;
    while (state.undo.length) {
      const u = state.undo.pop();
      try { await u.handle.move(u.from); const it = state.items.find(x => x.handle === u.handle); if (it) { it.name = u.from; it.renamed = false; } n++; } catch (e) { /* skip */ }
    }
    render(); setStatus(`${n}개 되돌림.`, 'ok'); $('#undo').disabled = true;
  };

  // ---------------- 스크립트 / CSV ----------------
  $('#script').onclick = () => {
    const rows = state.items.filter(it => it.selected && it.newName && !it.renamed);
    const win = /Win/i.test(navigator.platform) || /Windows/i.test(navigator.userAgent);
    let text;
    if (win) {
      text = '@echo off\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\n' +
        rows.map(it => `if exist "${it.name}" ren "${it.name}" "${it.newName}"`).join('\r\n') +
        `\r\necho ${rows.length}개 파일 이름을 바꿨습니다.\r\npause\r\n`;
      download('slate_rename.bat', text, 'application/x-bat');
    } else {
      text = '#!/bin/sh\ncd "$(dirname "$0")"\n' +
        rows.map(it => `[ -e "${it.name}" ] && mv -n "${it.name}" "${it.newName}"`).join('\n') +
        `\necho "${rows.length}개 파일 이름을 바꿨습니다."\n`;
      download('slate_rename.sh', text, 'text/x-sh');
    }
    setStatus(win ? '받은 slate_rename.bat 을 촬영 폴더에 넣고 더블클릭하면 이름이 바뀝니다.' : '받은 slate_rename.sh 를 촬영 폴더에 넣고 터미널에서 `sh slate_rename.sh` 로 실행하세요.', 'ok');
  };
  $('#csv').onclick = () => {
    const q = (s) => '"' + String(s ?? '').replace(/"/g, '""') + '"';
    const lines = ['씬,테이크,OK,새 이름,원본,들린 말,상태'];
    const sorted = [...state.items].sort((a, b) => (a.info?.scene ?? 9999) - (b.info?.scene ?? 9999) || (a.info?.take ?? 999) - (b.info?.take ?? 999));
    for (const it of sorted) lines.push([it.info ? `${it.info.scene}${it.info.suffix || ''}` : '?', it.info ? it.info.take : '?', it.ok, it.newName, it.renamed ? it.renamedFrom : it.name, it.heard, it.note].map(q).join(','));
    download('slate_index.csv', '﻿' + lines.join('\r\n'), 'text/csv');
  };
  function download(name, text, type) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  // ---------------- 유틸 ----------------
  function esc(s) { return String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
  function setStatus(msg, kind = '') { const el = $('#status'); el.textContent = msg; el.style.color = kind === 'bad' ? '#b91c1c' : kind === 'warn' ? '#b45309' : kind === 'ok' ? '#15803d' : ''; }
  function showProgress(show, frac = 0, text = '') {
    $('#progress').classList.toggle('hidden', !show);
    $('#bar').style.width = `${Math.round(frac * 100)}%`; $('#progressText').textContent = text;
  }
  if (MOCK) setStatus('모의 인식 모드(테스트용): 실제 음성 인식 대신 파일명으로 가짜 결과를 만듭니다.', 'warn');
})();
