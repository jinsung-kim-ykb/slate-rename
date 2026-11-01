"""슬레이트 콜 텍스트에서 씬/컷/테이크 번호를 뽑아내는 파서.

입력 예 (Whisper 전사 결과):
    "12의 3"            -> scene=12, take=3
    "12 - 3"            -> scene=12, take=3
    "씬 12 테이크 3"     -> scene=12, take=3
    "씬 12 컷 2 테이크 3" -> scene=12, shot=2, take=3
    "십이의 삼"          -> scene=12, take=3
    "열둘에 셋"          -> scene=12, take=3
    "12A의 3"           -> scene=12, suffix=A, take=3
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# 한국어 수사 -> 숫자
# ---------------------------------------------------------------------------

_SINO_DIGITS = {"영": 0, "공": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5,
                "육": 6, "륙": 6, "칠": 7, "팔": 8, "구": 9}
_SINO_UNITS = {"십": 10, "백": 100, "천": 1000}

_NATIVE_ONES = {"하나": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3, "석": 3,
                "넷": 4, "네": 4, "넉": 4, "다섯": 5, "여섯": 6, "일곱": 7,
                "여덟": 8, "여덜": 8, "아홉": 9}
_NATIVE_TENS = {"열": 10, "스물": 20, "스무": 20, "서른": 30, "마흔": 40,
                "쉰": 50, "예순": 60, "일흔": 70, "여든": 80, "아흔": 90}

# 수사 뒤에 붙을 수 있는 조사/접미사 (떼어내고 숫자만 본다)
_TRAILING_PARTICLES = ("번째", "번", "의", "에", "이", "에서", "이요", "요")


def sino_to_int(word: str) -> Optional[int]:
    """'십이' -> 12, '이십삼' -> 23, '백일' -> 101. 실패 시 None."""
    if not word or any(ch not in _SINO_DIGITS and ch not in _SINO_UNITS for ch in word):
        return None
    total, current = 0, 0
    for ch in word:
        if ch in _SINO_DIGITS:
            current = _SINO_DIGITS[ch]
        else:  # unit
            unit = _SINO_UNITS[ch]
            total += (current if current else 1) * unit
            current = 0
    return total + current


def native_to_int(word: str) -> Optional[int]:
    """'열둘' -> 12, '스물셋' -> 23, '다섯' -> 5. 실패 시 None."""
    if not word:
        return None
    total = 0
    rest = word
    for tens, val in sorted(_NATIVE_TENS.items(), key=lambda kv: -len(kv[0])):
        if rest.startswith(tens):
            total += val
            rest = rest[len(tens):]
            break
    if rest:
        for ones, val in sorted(_NATIVE_ONES.items(), key=lambda kv: -len(kv[0])):
            if rest == ones:
                total += val
                rest = ""
                break
    return total if not rest else None


def korean_number_token_to_digits(token: str) -> str:
    """토큰 하나가 '수사(+조사)'이면 '숫자(+조사)'로 바꿔 돌려준다. 아니면 그대로."""
    for particle in sorted(_TRAILING_PARTICLES, key=len, reverse=True):
        if token.endswith(particle) and len(token) > len(particle):
            core, tail = token[: -len(particle)], particle
            break
    else:
        core, tail = token, ""

    # '이의' 처럼 수사 '이' + 조사 '의' 같은 짧은 경우는 오인식 위험이 커서
    # 1글자 한자어 수사는 조사가 붙어 있을 때만 숫자로 본다.
    val = native_to_int(core)
    if val is None:
        val = sino_to_int(core)
        if val is not None and len(core) == 1 and not tail:
            return token  # 단독 '이', '사' 등은 숫자로 보지 않음
    if val is None:
        return token
    return f"{val}{tail}"


def normalize_numbers(text: str) -> str:
    """전각 숫자 통일 + 한국어 수사를 숫자로 변환."""
    text = unicodedata.normalize("NFKC", text)
    # 수사와 조사가 붙어 나오는 경우를 위해 조사 앞에 공백 없이도 처리
    tokens = re.split(r"(\s+)", text)
    text = "".join(korean_number_token_to_digits(t) if t.strip() else t for t in tokens)
    # 2차: '12의 삼' 처럼 숫자+구분자 바로 뒤에 오는 한 글자 한자어 수사는 문맥상 숫자가 확실하므로 변환
    single = "".join(_SINO_DIGITS.keys())
    text = re.sub(
        r"(\d+[a-zA-Z]?\s*(?:의|에|-|–|—|/|,|대|다시)\s*)([" + single + r"])(?=\s|$|[.,!?])",
        lambda m: m.group(1) + str(_SINO_DIGITS[m.group(2)]), text)
    # '씬 이 테이크 삼' 처럼 키워드 뒤의 한 글자 수사도 변환
    text = re.sub(
        r"((?:씬|신|테이크|테잌|컷)\s*)([" + single + r"])(?=\s|$|[.,!?])",
        lambda m: m.group(1) + str(_SINO_DIGITS[m.group(2)]), text)
    return text


# ---------------------------------------------------------------------------
# 슬레이트 정보 추출
# ---------------------------------------------------------------------------

@dataclass
class SlateInfo:
    scene: Optional[int] = None
    scene_suffix: str = ""          # 12A 의 'A'
    shot: Optional[int] = None      # 컷 번호 (있을 때만)
    take: Optional[int] = None
    matched: str = ""               # 매칭된 원문 조각
    pattern: str = ""               # 어떤 규칙으로 잡았는지
    raw_text: str = ""              # 전사 원문
    is_ok: Optional[bool] = None    # 'OK' 콜이 들렸는지 (끝부분 분석용)
    notes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.scene is not None and self.take is not None

    def label(self, template: str = "S{scene:02d}{suffix}_T{take:02d}") -> str:
        return template.format(
            scene=self.scene if self.scene is not None else 0,
            suffix=self.scene_suffix,
            shot=self.shot if self.shot is not None else 0,
            take=self.take if self.take is not None else 0,
        )


_SCENE_WORDS = r"(?:씬|신|씬넘버|scene|sc|s)"
_SHOT_WORDS = r"(?:컷|샷|shot|cut|c)"
_TAKE_WORDS = r"(?:테이크|테잌|테익|테이끄|테크|take|tk|t)"
_SEP = r"\s*(?:의|에|-|–|—|/|,|\.|:|다시|대|에서|번|번의|\s)\s*"
_NUM = r"(\d{1,3})"
_SUF = r"\s*([a-zA-Z가-힣]?)"   # 12A, 12에이 (에이→A 변환은 별도)

_LETTER_WORDS = {"에이": "A", "비": "B", "씨": "C", "디": "D", "이": "E", "에프": "F"}

_PATTERNS: list[tuple[str, re.Pattern]] = [
    # 씬 12 컷 2 테이크 3
    ("scene-shot-take", re.compile(
        _SCENE_WORDS + r"\s*" + _NUM + _SUF + r"\s*(?:번)?\s*" + _SHOT_WORDS + r"\s*" + _NUM
        + r"\s*(?:번)?\s*" + _TAKE_WORDS + r"\s*" + _NUM, re.IGNORECASE)),
    # 씬 12 테이크 3  /  씬 12의 3
    ("scene-take", re.compile(
        _SCENE_WORDS + r"\s*" + _NUM + _SUF + r"\s*(?:번)?" + r"(?:" + _SEP + r"|\s*" + _TAKE_WORDS + r"\s*)" + _NUM,
        re.IGNORECASE)),
    # 12번 씬 3번째 테이크  (숫자가 단어 앞에 오는 어순)
    ("num-scene-num-take", re.compile(
        r"(?<!\d)" + _NUM + _SUF + r"\s*(?:번|번째)?\s*" + _SCENE_WORDS + r"\s*,?\s*" + _NUM
        + r"\s*(?:번|번째)?\s*" + _TAKE_WORDS, re.IGNORECASE)),
    # 12 컷 2 테이크 3  (씬 단어 없음)
    ("num-shot-take", re.compile(
        r"(?<!\d)" + _NUM + _SUF + r"\s*(?:번)?\s*" + _SHOT_WORDS + r"\s*" + _NUM
        + r"\s*(?:번)?\s*" + _TAKE_WORDS + r"\s*" + _NUM, re.IGNORECASE)),
    # 12의 3 / 12 - 3 / 12 3 / 12대 3 / 12 테이크 3
    ("num-sep-num", re.compile(
        r"(?<!\d)" + _NUM + _SUF + r"(?:" + _SEP + r"|\s*" + _TAKE_WORDS + r"\s*)" + _NUM + r"(?!\d)",
        re.IGNORECASE)),
]

_OK_RE = re.compile(r"(오케이|오케|okay|ok|오키|좋아요|좋았어|굿|good)", re.IGNORECASE)
_NG_RE = re.compile(r"(다시\s*(갈게|가자|갑니다|한\s*번)|엔지|ng|컷\s*컷|다시요)", re.IGNORECASE)


def _clean_suffix(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    if s in _LETTER_WORDS:
        return _LETTER_WORDS[s]
    if re.fullmatch(r"[a-zA-Z]", s):
        return s.upper()
    return ""  # 한글 조사 등은 접미사가 아님


def parse_slate(text: str, max_scene: int = 999, max_take: int = 99) -> SlateInfo:
    """전사 텍스트에서 씬/테이크를 뽑는다. 못 찾으면 scene/take가 None인 SlateInfo."""
    info = SlateInfo(raw_text=text)
    if not text or not text.strip():
        info.notes.append("빈 전사")
        return info

    norm = normalize_numbers(text)
    # '에이' 같은 글자 이름을 A로 치환 (12에이의 3 -> 12A의 3)
    for word, letter in _LETTER_WORDS.items():
        if word == "이":   # '이'는 조사/수사와 겹쳐 위험하므로 제외
            continue
        norm = re.sub(r"(\d)\s*" + word + r"(?=\s|의|에|$|-)", r"\1" + letter, norm)

    for name, pat in _PATTERNS:
        m = pat.search(norm)
        if not m:
            continue
        groups = m.groups()
        if name in ("scene-shot-take", "num-shot-take"):
            scene, suf, shot, take = groups
            info.shot = int(shot)
        else:
            scene, suf, take = groups
        scene_i, take_i = int(scene), int(take)
        if scene_i > max_scene or take_i > max_take or take_i == 0:
            info.notes.append(f"범위 밖 숫자 무시 ({scene_i}/{take_i})")
            continue
        info.scene = scene_i
        info.scene_suffix = _clean_suffix(suf)
        info.take = take_i
        info.matched = m.group(0).strip()
        info.pattern = name
        break

    if not info.complete:
        info.notes.append("씬/테이크 패턴을 찾지 못함")
    return info


def detect_ok_call(text: str) -> Optional[bool]:
    """클립 끝부분 전사에서 OK/NG 콜을 판별. 판단 불가면 None."""
    if not text:
        return None
    if _NG_RE.search(text):
        return False
    if _OK_RE.search(text):
        return True
    return None
