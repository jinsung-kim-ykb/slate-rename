// 슬레이트 콜 텍스트 → 씬/테이크 (slate_rename/parser.py 와 동일한 규칙)
// 브라우저와 Node 양쪽에서 쓸 수 있게 전역 객체 SlateParser 로 노출한다.
(function (root) {
  const SINO = { 영: 0, 공: 0, 일: 1, 이: 2, 삼: 3, 사: 4, 오: 5, 육: 6, 륙: 6, 칠: 7, 팔: 8, 구: 9 };
  const SINO_UNITS = { 십: 10, 백: 100, 천: 1000 };
  const NATIVE_ONES = { 하나: 1, 한: 1, 둘: 2, 두: 2, 셋: 3, 세: 3, 석: 3, 넷: 4, 네: 4, 넉: 4,
    다섯: 5, 여섯: 6, 일곱: 7, 여덟: 8, 여덜: 8, 아홉: 9 };
  const NATIVE_TENS = { 열: 10, 스물: 20, 스무: 20, 서른: 30, 마흔: 40, 쉰: 50, 예순: 60, 일흔: 70, 여든: 80, 아흔: 90 };
  const PARTICLES = ['번째', '에서', '이요', '번', '의', '에', '이', '요'];

  function sinoToInt(w) {
    if (!w) return null;
    let total = 0, cur = 0;
    for (const ch of w) {
      if (ch in SINO) cur = SINO[ch];
      else if (ch in SINO_UNITS) { total += (cur || 1) * SINO_UNITS[ch]; cur = 0; }
      else return null;
    }
    return total + cur;
  }
  function nativeToInt(w) {
    if (!w) return null;
    let total = 0, rest = w;
    for (const t of Object.keys(NATIVE_TENS).sort((a, b) => b.length - a.length)) {
      if (rest.startsWith(t)) { total += NATIVE_TENS[t]; rest = rest.slice(t.length); break; }
    }
    if (rest) {
      for (const o of Object.keys(NATIVE_ONES).sort((a, b) => b.length - a.length)) {
        if (rest === o) { total += NATIVE_ONES[o]; rest = ''; break; }
      }
    }
    return rest ? null : total;
  }
  function tokenToDigits(tok) {
    let core = tok, tail = '';
    for (const p of PARTICLES) {
      if (tok.endsWith(p) && tok.length > p.length) { core = tok.slice(0, -p.length); tail = p; break; }
    }
    let v = nativeToInt(core);
    if (v === null) {
      v = sinoToInt(core);
      if (v !== null && core.length === 1 && !tail) return tok; // 단독 '이','사' 는 숫자로 안 봄
    }
    return v === null ? tok : `${v}${tail}`;
  }
  const SINGLE = Object.keys(SINO).join('');
  function normalizeNumbers(text) {
    text = text.normalize('NFKC');
    text = text.split(/(\s+)/).map(t => (t.trim() ? tokenToDigits(t) : t)).join('');
    text = text.replace(new RegExp(`(\\d+[a-zA-Z]?\\s*(?:의|에|-|–|—|/|,|대|다시)\\s*)([${SINGLE}])(?=\\s|$|[.,!?])`, 'g'),
      (m, a, b) => a + SINO[b]);
    text = text.replace(new RegExp(`((?:씬|신|테이크|테잌|컷)\\s*)([${SINGLE}])(?=\\s|$|[.,!?])`, 'g'),
      (m, a, b) => a + SINO[b]);
    return text;
  }

  const SCENE = '(?:씬|신|씬넘버|scene|sc|s)';
  const SHOT = '(?:컷|샷|shot|cut|c)';
  const TAKE = '(?:테이크|테잌|테익|테이끄|테크|take|tk|t)';
  const SEP = '\\s*(?:의|에|-|–|—|/|,|\\.|:|다시|대|에서|번|번의|\\s)\\s*';
  const NUM = '(\\d{1,3})';
  const SUF = '\\s*([a-zA-Z가-힣]?)';
  const LETTERS = { 에이: 'A', 비: 'B', 씨: 'C', 디: 'D', 에프: 'F' };

  const PATTERNS = [
    ['scene-shot-take', new RegExp(`${SCENE}\\s*${NUM}${SUF}\\s*(?:번)?\\s*${SHOT}\\s*${NUM}\\s*(?:번)?\\s*${TAKE}\\s*${NUM}`, 'i')],
    ['scene-take', new RegExp(`${SCENE}\\s*${NUM}${SUF}\\s*(?:번)?(?:${SEP}|\\s*${TAKE}\\s*)${NUM}`, 'i')],
    ['num-scene-num-take', new RegExp(`(?<!\\d)${NUM}${SUF}\\s*(?:번|번째)?\\s*${SCENE}\\s*,?\\s*${NUM}\\s*(?:번|번째)?\\s*${TAKE}`, 'i')],
    ['num-shot-take', new RegExp(`(?<!\\d)${NUM}${SUF}\\s*(?:번)?\\s*${SHOT}\\s*${NUM}\\s*(?:번)?\\s*${TAKE}\\s*${NUM}`, 'i')],
    ['num-sep-num', new RegExp(`(?<!\\d)${NUM}${SUF}(?:${SEP}|\\s*${TAKE}\\s*)${NUM}(?!\\d)`, 'i')],
  ];
  const OK_RE = /(오케이|오케|okay|ok|오키|좋아요|좋았어|굿|good)/i;
  const NG_RE = /(다시\s*(갈게|가자|갑니다|한\s*번)|엔지|ng|컷\s*컷|다시요)/i;

  function cleanSuffix(s) {
    if (!s) return '';
    s = s.trim();
    if (LETTERS[s]) return LETTERS[s];
    if (/^[a-zA-Z]$/.test(s)) return s.toUpperCase();
    return '';
  }

  function parseSlate(text, maxScene = 999, maxTake = 99) {
    const info = { scene: null, suffix: '', shot: null, take: null, matched: '', pattern: '', raw: text || '', notes: [] };
    if (!text || !text.trim()) { info.notes.push('빈 전사'); return info; }
    let norm = normalizeNumbers(text);
    for (const [w, L] of Object.entries(LETTERS)) {
      norm = norm.replace(new RegExp(`(\\d)\\s*${w}(?=\\s|의|에|$|-)`, 'g'), `$1${L}`);
    }
    for (const [name, re] of PATTERNS) {
      const m = re.exec(norm);
      if (!m) continue;
      let scene, suf, shot = null, take;
      if (name === 'scene-shot-take' || name === 'num-shot-take') { [, scene, suf, shot, take] = m; }
      else { [, scene, suf, take] = m; }
      const s = parseInt(scene, 10), t = parseInt(take, 10);
      if (s > maxScene || t > maxTake || t === 0) { info.notes.push(`범위 밖 숫자 무시 (${s}/${t})`); continue; }
      info.scene = s; info.suffix = cleanSuffix(suf); info.take = t;
      info.shot = shot === null ? null : parseInt(shot, 10);
      info.matched = m[0].trim(); info.pattern = name;
      break;
    }
    if (info.scene === null || info.take === null) info.notes.push('씬/테이크 패턴을 찾지 못함');
    info.complete = info.scene !== null && info.take !== null;
    return info;
  }

  function detectOkCall(text) {
    if (!text) return null;
    if (NG_RE.test(text)) return false;
    if (OK_RE.test(text)) return true;
    return null;
  }

  // 템플릿: S{scene:02d}{suffix}_T{take:02d}  (파이썬 포맷 문법의 부분집합)
  function label(info, template = 'S{scene:02d}{suffix}_T{take:02d}') {
    const vals = { scene: info.scene ?? 0, suffix: info.suffix || '', shot: info.shot ?? 0, take: info.take ?? 0 };
    return template.replace(/\{(\w+)(?::0(\d)d)?\}/g, (m, key, pad) => {
      let v = vals[key]; if (v === undefined) return m;
      v = String(v);
      return pad ? v.padStart(parseInt(pad, 10), '0') : v;
    });
  }

  root.SlateParser = { parseSlate, detectOkCall, normalizeNumbers, label };
})(typeof self !== 'undefined' ? self : globalThis);
