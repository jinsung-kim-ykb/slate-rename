// node tests/test_parser.mjs  — docs/parser.js 가 파이썬 파서와 같은 결과를 내는지 확인
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const here = path.dirname(fileURLToPath(import.meta.url));
new Function(readFileSync(path.join(here, '../docs/parser.js'), 'utf8'))();
const { parseSlate, detectOkCall, label } = globalThis.SlateParser;

const CASES = [
  ['12의 3', 12, 3, null, ''], ['12 의 3', 12, 3, null, ''], ['12-3', 12, 3, null, ''], ['12 - 3', 12, 3, null, ''],
  ['12에 3', 12, 3, null, ''], ['12 3', 12, 3, null, ''], ['12, 3', 12, 3, null, ''], ['12대 3', 12, 3, null, ''],
  ['12 다시 3', 12, 3, null, ''], ['씬 12 테이크 3', 12, 3, null, ''], ['씬12 테이크3', 12, 3, null, ''],
  ['신 12의 3', 12, 3, null, ''], ['씬 12 컷 2 테이크 3', 12, 3, 2, ''], ['12 컷 2 테이크 3', 12, 3, 2, ''],
  ['12번 신 3번째 테이크', 12, 3, null, ''], ['12번 씬, 3번 테이크', 12, 3, null, ''],
  ['십이의 삼', 12, 3, null, ''], ['십이에 삼', 12, 3, null, ''], ['열둘에 셋', 12, 3, null, ''],
  ['이십삼의 일', 23, 1, null, ''], ['7의 1', 7, 1, null, ''], ['칠의 일', 7, 1, null, ''],
  ['12A의 3', 12, 3, null, 'A'], ['12에이의 3', 12, 3, null, 'A'], ['12번의 3번', 12, 3, null, ''],
  ['자 갑니다, 12의 3, 레디 액션', 12, 3, null, ''], ['슬레이트. 5의 2. 액션!', 5, 2, null, ''],
  ['씬 3 테이크 1 마크', 3, 1, null, ''], ['Scene 12 Take 3', 12, 3, null, ''], ['S12 T3', 12, 3, null, ''],
  ['12/3', 12, 3, null, ''], ['백일의 이', 101, 2, null, ''], ['3의 12', 3, 12, null, ''],
];
const NEG = ['레디 액션', '', '   ', '오늘 날씨 좋네요', '이거 사람이 많네'];
let fails = 0;
for (const [text, scene, take, shot, suffix] of CASES) {
  const i = parseSlate(text);
  const ok = i.scene === scene && i.take === take && i.shot === shot && i.suffix === suffix;
  if (!ok) fails++;
  console.log(`[${ok ? 'OK ' : 'FAIL'}] ${JSON.stringify(text).padEnd(36)} -> scene=${i.scene} suffix=${i.suffix} shot=${i.shot} take=${i.take} (${i.pattern})`);
}
for (const t of NEG) {
  const i = parseSlate(t); const ok = !i.complete; if (!ok) fails++;
  console.log(`[${ok ? 'OK ' : 'FAIL'}] NEG ${JSON.stringify(t)} complete=${i.complete}`);
}
for (const [t, exp] of [['오케이 컷', true], ['컷, 다시 갈게요', false], ['컷', null], ['좋았어 오케이', true]]) {
  const got = detectOkCall(t); const ok = got === exp; if (!ok) fails++;
  console.log(`[${ok ? 'OK ' : 'FAIL'}] OK-call ${JSON.stringify(t)} -> ${got}`);
}
const lbl = label({ scene: 12, suffix: 'A', take: 3, shot: null });
console.log(`[${lbl === 'S12A_T03' ? 'OK ' : 'FAIL'}] label -> ${lbl}`);
if (lbl !== 'S12A_T03') fails++;
console.log(fails ? `${fails} FAILURES` : 'ALL PASS');
process.exit(fails ? 1 : 0);
