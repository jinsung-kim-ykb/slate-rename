// 브라우저에서 영상/오디오 파일의 특정 구간만 읽어 16kHz 모노 Float32Array 로 만든다.
// - WAV: 헤더만 읽고 필요한 바이트 구간만 잘라 디코드 (수 GB 파일도 즉시)
// - MP4/MOV/M4A: mp4box.js 로 moov 를 찾아 오디오 샘플 위치를 알아낸 뒤, 그 구간만 읽어 WebCodecs 로 디코드
// - 그 외/실패: AudioContext.decodeAudioData 로 앞부분(또는 전체) 디코드 시도
(function (root) {
  const SR = 16000;

  function resample(samples, from, to = SR) {
    if (from === to) return samples;
    const n = Math.floor(samples.length * to / from);
    const out = new Float32Array(n);
    const ratio = from / to;
    for (let i = 0; i < n; i++) {
      const x = i * ratio, i0 = Math.floor(x), i1 = Math.min(i0 + 1, samples.length - 1);
      out[i] = samples[i0] + (samples[i1] - samples[i0]) * (x - i0);
    }
    return out;
  }
  function downmix(channels) {
    if (channels.length === 1) return channels[0];
    const n = channels[0].length, out = new Float32Array(n);
    for (const ch of channels) for (let i = 0; i < n; i++) out[i] += ch[i] / channels.length;
    return out;
  }
  const readSlice = (file, a, b) => file.slice(a, b).arrayBuffer();

  // ---------------- WAV ----------------
  async function extractWav(file, start, dur) {
    const head = new DataView(await readSlice(file, 0, Math.min(file.size, 1 << 16)));
    const tag = (o) => String.fromCharCode(head.getUint8(o), head.getUint8(o + 1), head.getUint8(o + 2), head.getUint8(o + 3));
    if (tag(0) !== 'RIFF' && tag(0) !== 'RF64') throw new Error('WAV 헤더가 아님');
    let off = 12, fmt = null, dataOff = null, dataLen = null;
    while (off + 8 <= head.byteLength) {
      const id = tag(off), size = head.getUint32(off + 4, true);
      if (id === 'fmt ') {
        fmt = { format: head.getUint16(off + 8, true), channels: head.getUint16(off + 10, true),
          rate: head.getUint32(off + 12, true), bits: head.getUint16(off + 22, true) };
        if (fmt.format === 0xFFFE && size >= 26) fmt.format = head.getUint16(off + 8 + 24, true); // extensible → subformat
      } else if (id === 'data') { dataOff = off + 8; dataLen = size === 0xFFFFFFFF ? file.size - dataOff : size; break; }
      off += 8 + size + (size & 1);
    }
    if (!fmt || dataOff === null) throw new Error('WAV fmt/data 청크를 찾지 못함');
    const bps = fmt.bits / 8, frame = bps * fmt.channels;
    const a = dataOff + Math.floor(start * fmt.rate) * frame;
    const b = Math.min(dataOff + dataLen, a + Math.ceil(dur * fmt.rate) * frame);
    if (b <= a) return new Float32Array(0);
    const buf = new DataView(await readSlice(file, a, b));
    const frames = Math.floor(buf.byteLength / frame);
    const chans = Array.from({ length: fmt.channels }, () => new Float32Array(frames));
    for (let i = 0; i < frames; i++) {
      for (let c = 0; c < fmt.channels; c++) {
        const p = i * frame + c * bps; let v;
        if (fmt.format === 3) v = bps === 4 ? buf.getFloat32(p, true) : buf.getFloat64(p, true);
        else if (bps === 1) v = (buf.getUint8(p) - 128) / 128;
        else if (bps === 2) v = buf.getInt16(p, true) / 32768;
        else if (bps === 3) v = ((buf.getUint8(p) | (buf.getUint8(p + 1) << 8) | (buf.getInt8(p + 2) << 16))) / 8388608;
        else v = buf.getInt32(p, true) / 2147483648;
        chans[c][i] = v;
      }
    }
    return resample(downmix(chans), fmt.rate);
  }

  // ---------------- MP4 / MOV (mp4box + WebCodecs) ----------------
  // 최상위 atom 목록: [{type, start, size}]  (size==1 이면 64비트 확장 크기, size==0 이면 파일 끝까지)
  async function scanTopAtoms(file) {
    const out = []; let pos = 0;
    for (let i = 0; i < 64 && pos + 8 <= file.size; i++) {
      const dv = new DataView(await readSlice(file, pos, Math.min(file.size, pos + 16)));
      let size = dv.getUint32(0);
      const type = String.fromCharCode(dv.getUint8(4), dv.getUint8(5), dv.getUint8(6), dv.getUint8(7));
      if (size === 1 && dv.byteLength >= 16) size = Number(dv.getBigUint64(8));
      else if (size === 0) size = file.size - pos;
      if (!/^[\w ]{4}$/.test(type) || size < 8) break;
      out.push({ type, start: pos, size });
      pos += size;
    }
    return out;
  }

  async function extractMp4(file, start, dur, log = () => {}) {
    if (typeof MP4Box === 'undefined') throw new Error('mp4box.js 가 로드되지 않음');
    const mp4 = MP4Box.createFile();
    let info = null, err = null;
    mp4.onError = (e) => { err = e; };
    mp4.onReady = (i) => { info = i; };
    // 1) 최상위 atom 헤더(16바이트)만 읽어 ftyp/moov 위치를 찾고, mdat 는 헤더만 넘긴다.
    //    → 수십 GB 파일이라도 실제로 읽는 양은 moov(목차) 크기뿐이다.
    const atoms = await scanTopAtoms(file);
    for (const a of atoms) {
      const full = a.type === 'ftyp' || a.type === 'moov' || a.size <= 1024 * 1024;
      const len = full ? a.size : Math.min(a.size, 16);
      const buf = await readSlice(file, a.start, Math.min(file.size, a.start + len));
      buf.fileStart = a.start;
      mp4.appendBuffer(buf);
      if (info || err) break;
    }
    if (!info && !err) {
      // 2) 드물게 위 방식이 안 먹으면 앞에서부터 순차 스캔 (최대 64 x 4MB)
      const CHUNK = 4 * 1024 * 1024; let pos = 0;
      for (let i = 0; i < 64 && !info && !err && pos < file.size; i++) {
        const buf = await readSlice(file, pos, Math.min(file.size, pos + CHUNK));
        buf.fileStart = pos;
        const next = mp4.appendBuffer(buf);
        pos = (typeof next === 'number' && next > pos) ? next : pos + CHUNK;
      }
    }
    if (err) throw new Error('mp4box: ' + err);
    if (!info) throw new Error('moov 를 찾지 못함 (지원하지 않는 컨테이너)');

    const track = info.audioTracks && info.audioTracks[0];
    if (!track) throw new Error('오디오 트랙 없음');
    log(`오디오 트랙: ${track.codec} ${track.audio.sample_rate}Hz ${track.audio.channel_count}ch`);

    // 3) 구간에 해당하는 샘플의 파일 내 위치(offset/size)를 목차에서 읽어, 그 바이트 범위만 한 번에 읽는다.
    const all = (mp4.getTrackById(track.id).samples) || [];
    if (!all.length) throw new Error('샘플 목록이 비어 있음');
    const endT = start + dur;
    const wanted = all.filter(s => (s.cts + s.duration) / s.timescale > start && s.cts / s.timescale < endT);
    if (!wanted.length) throw new Error('구간에 오디오 샘플 없음');
    const lo = Math.min(...wanted.map(s => s.offset)), hi = Math.max(...wanted.map(s => s.offset + s.size));
    if (hi - lo > 256 * 1024 * 1024) throw new Error('오디오 샘플이 파일에 넓게 흩어져 있어 읽을 수 없음');
    const blob = new Uint8Array(await readSlice(file, lo, hi));
    for (const s of wanted) s.data = blob.subarray(s.offset - lo, s.offset - lo + s.size);

    const entry = mp4.getTrackById(track.id).mdia.minf.stbl.stsd.entries[0];
    const codec = track.codec || entry.type;
    let mono, rate;
    if (/^mp4a|^opus|^flac|^ac-3|^ec-3|^alac/i.test(codec)) {
      if (typeof AudioDecoder === 'undefined') throw new Error('이 브라우저는 WebCodecs 를 지원하지 않음 (Chrome/Edge 필요)');
      let description;
      try { description = entry.esds.esd.descs[0].descs[0].data; } catch (e) { description = undefined; }
      const cfg = { codec, sampleRate: track.audio.sample_rate, numberOfChannels: track.audio.channel_count };
      if (description) cfg.description = description;
      const sup = await AudioDecoder.isConfigSupported(cfg).catch(() => ({ supported: false }));
      if (!sup.supported) throw new Error(`브라우저가 ${codec} 디코드를 지원하지 않음`);
      const chunksOut = [];
      await new Promise((resolve, reject) => {
        const dec = new AudioDecoder({
          output: (ad) => {
            const chans = [];
            for (let c = 0; c < ad.numberOfChannels; c++) {
              const buf = new Float32Array(ad.numberOfFrames);
              ad.copyTo(buf, { planeIndex: c, format: 'f32-planar' });
              chans.push(buf);
            }
            chunksOut.push({ t: ad.timestamp / 1e6, rate: ad.sampleRate, mono: downmix(chans) });
            ad.close();
          },
          error: reject,
        });
        dec.configure(cfg);
        for (const s of wanted) {
          dec.decode(new EncodedAudioChunk({
            type: s.is_sync ? 'key' : 'delta',
            timestamp: Math.round(s.cts * 1e6 / s.timescale),
            duration: Math.round(s.duration * 1e6 / s.timescale),
            data: s.data,
          }));
        }
        dec.flush().then(() => { dec.close(); resolve(); }, reject);
      });
      if (!chunksOut.length) throw new Error('디코드 결과 없음');
      rate = chunksOut[0].rate;
      const total = chunksOut.reduce((n, c) => n + c.mono.length, 0);
      const all = new Float32Array(total); let o = 0;
      for (const c of chunksOut) { all.set(c.mono, o); o += c.mono.length; }
      const skip = Math.max(0, Math.round((start - chunksOut[0].t) * rate));
      mono = all.subarray(skip, skip + Math.round(dur * rate));
    } else if (/^(lpcm|sowt|twos|in24|in32|fl32|fl64|raw |NONE)$/i.test(entry.type)) {
      // 비압축 PCM (ProRes MOV 등): 샘플 바이트를 직접 해석
      const ch = track.audio.channel_count || 2;
      let bits = track.audio.sample_size || entry.samplesize || 16;
      let little = /sowt|lpcm|raw /i.test(entry.type), float = /fl32|fl64/i.test(entry.type);
      if (/in24/i.test(entry.type)) { bits = 24; little = false; }
      if (/in32/i.test(entry.type)) { bits = 32; little = false; }
      if (/twos/i.test(entry.type)) { bits = bits || 16; little = false; }
      if (/fl32/i.test(entry.type)) bits = 32;
      if (/fl64/i.test(entry.type)) bits = 64;
      const bps = bits / 8, frame = bps * ch;
      const total = wanted.reduce((n, s) => n + s.data.byteLength, 0);
      const raw = new Uint8Array(total); let o = 0;
      for (const s of wanted) { raw.set(s.data, o); o += s.data.byteLength; }
      const dv = new DataView(raw.buffer), frames = Math.floor(total / frame);
      const chans = Array.from({ length: ch }, () => new Float32Array(frames));
      for (let i = 0; i < frames; i++) for (let c = 0; c < ch; c++) {
        const p = i * frame + c * bps; let v;
        if (float) v = bps === 4 ? dv.getFloat32(p, little) : dv.getFloat64(p, little);
        else if (bps === 2) v = dv.getInt16(p, little) / 32768;
        else if (bps === 3) { const b0 = dv.getUint8(p), b1 = dv.getUint8(p + 1), b2 = dv.getUint8(p + 2);
          v = (little ? ((b2 << 16) | (b1 << 8) | b0) : ((b0 << 16) | (b1 << 8) | b2)); if (v & 0x800000) v -= 0x1000000; v /= 8388608; }
        else if (bps === 4) v = dv.getInt32(p, little) / 2147483648;
        else v = (dv.getUint8(p) - 128) / 128;
        chans[c][i] = v;
      }
      rate = track.audio.sample_rate;
      const skip = Math.max(0, Math.round((start - wanted[0].cts / wanted[0].timescale) * rate));
      mono = downmix(chans).subarray(skip, skip + Math.round(dur * rate));
    } else {
      throw new Error(`지원하지 않는 오디오 코덱: ${codec}`);
    }
    return resample(mono, rate);
  }

  // ---------------- fallback: decodeAudioData ----------------
  async function extractGeneric(file, start, dur) {
    const LIMIT = 300 * 1024 * 1024;
    if (file.size > LIMIT) throw new Error('파일이 커서 범용 디코더로는 열 수 없음');
    const ctx = new (root.AudioContext || root.webkitAudioContext)({ sampleRate: SR });
    try {
      const ab = await file.arrayBuffer();
      const decoded = await ctx.decodeAudioData(ab);
      const chans = Array.from({ length: decoded.numberOfChannels }, (_, c) => decoded.getChannelData(c));
      const mono = downmix(chans);
      const a = Math.floor(start * decoded.sampleRate), b = Math.min(mono.length, a + Math.ceil(dur * decoded.sampleRate));
      return resample(mono.subarray(a, b), decoded.sampleRate);
    } finally { ctx.close(); }
  }

  // 파일 길이(초). 모르면 null
  async function getDuration(file) {
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    try {
      if (['wav', 'bwf'].includes(ext)) {
        const head = new DataView(await readSlice(file, 0, Math.min(file.size, 1 << 16)));
        const tag = (o) => String.fromCharCode(head.getUint8(o), head.getUint8(o + 1), head.getUint8(o + 2), head.getUint8(o + 3));
        let off = 12, rate = 0, frame = 0;
        while (off + 8 <= head.byteLength) {
          const id = tag(off), size = head.getUint32(off + 4, true);
          if (id === 'fmt ') { rate = head.getUint32(off + 12, true); frame = head.getUint16(off + 20, true); }
          if (id === 'data') return rate && frame ? (size === 0xFFFFFFFF ? file.size - off - 8 : size) / frame / rate : null;
          off += 8 + size + (size & 1);
        }
        return null;
      }
      if (typeof MP4Box !== 'undefined') {
        const mp4 = MP4Box.createFile(); let info = null;
        mp4.onReady = (i) => { info = i; }; mp4.onError = () => {};
        for (const a of await scanTopAtoms(file)) {
          const full = a.type === 'ftyp' || a.type === 'moov' || a.size <= 1024 * 1024;
          const buf = await readSlice(file, a.start, Math.min(file.size, a.start + (full ? a.size : 16)));
          buf.fileStart = a.start; mp4.appendBuffer(buf);
          if (info) break;
        }
        return info ? info.duration / info.timescale : null;
      }
    } catch (e) { return null; }
    return null;
  }

  async function extractAudio(file, start = 0, dur = 15, log = () => {}) {
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    const tried = [];
    if (['wav', 'bwf'].includes(ext)) {
      try { return await extractWav(file, start, dur); } catch (e) { tried.push('wav: ' + e.message); }
    }
    if (['mp4', 'mov', 'm4v', 'm4a', 'm4b', '3gp'].includes(ext)) {
      try { return await extractMp4(file, start, dur, log); } catch (e) { tried.push('mp4: ' + e.message); }
    }
    try { return await extractGeneric(file, start, dur); } catch (e) { tried.push('generic: ' + e.message); }
    throw new Error(tried.join(' / '));
  }

  // 말소리 구간 찾기 (파이썬 speech_bursts 와 동일)
  function speechBursts(audio, sr = SR, frameMs = 30, minLen = 0.3, gap = 0.6) {
    if (!audio.length) return [];
    const n = Math.floor(sr * frameMs / 1000), nf = Math.floor(audio.length / n);
    const energy = new Float32Array(nf);
    for (let f = 0; f < nf; f++) {
      let s = 0; for (let i = 0; i < n; i++) { const v = audio[f * n + i]; s += v * v; }
      energy[f] = 20 * Math.log10(Math.sqrt(s / n) + 1e-9);
    }
    const sorted = Array.from(energy).sort((a, b) => a - b);
    const floor = sorted[Math.floor(sorted.length * 0.2)] ?? -90;
    const thresh = Math.max(floor + 12, -45);
    const bursts = [];
    for (let f = 0; f < nf; f++) {
      const t = f * frameMs / 1000;
      if (energy[f] > thresh) {
        if (bursts.length && t - bursts[bursts.length - 1][1] <= gap) bursts[bursts.length - 1][1] = t + frameMs / 1000;
        else bursts.push([t, t + frameMs / 1000]);
      }
    }
    return bursts.filter(([s, e]) => e - s >= minLen);
  }
  function padSilence(audio, sr = SR, sec = 0.8) {
    const z = Math.floor(sr * sec), out = new Float32Array(audio.length + 2 * z);
    out.set(audio, z); return out;
  }
  function rmsDb(audio) {
    if (!audio.length) return -120;
    let s = 0; for (const v of audio) s += v * v;
    return 20 * Math.log10(Math.sqrt(s / audio.length) + 1e-12);
  }

  root.SlateAudio = { extractAudio, getDuration, speechBursts, padSilence, rmsDb, SR };
})(typeof self !== 'undefined' ? self : globalThis);
