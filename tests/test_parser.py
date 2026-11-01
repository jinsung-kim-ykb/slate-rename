import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from slate_rename.parser import parse_slate, normalize_numbers, detect_ok_call

CASES = [
    # (전사 텍스트, 기대 scene, 기대 take, 기대 shot, 기대 suffix)
    ("12의 3", 12, 3, None, ""),
    ("12 의 3", 12, 3, None, ""),
    ("12-3", 12, 3, None, ""),
    ("12 - 3", 12, 3, None, ""),
    ("12에 3", 12, 3, None, ""),
    ("12 3", 12, 3, None, ""),
    ("12, 3", 12, 3, None, ""),
    ("12대 3", 12, 3, None, ""),
    ("12 다시 3", 12, 3, None, ""),
    ("씬 12 테이크 3", 12, 3, None, ""),
    ("씬12 테이크3", 12, 3, None, ""),
    ("신 12의 3", 12, 3, None, ""),
    ("씬 12 컷 2 테이크 3", 12, 2, 3, ""),   # scene, shot, take 순서 주의 -> 아래서 보정
    ("12 컷 2 테이크 3", 12, 2, 3, ""),
    ("12번 신 3번째 테이크", 12, 3, None, ""),
    ("12번 씬, 3번 테이크", 12, 3, None, ""),
    ("십이의 삼", 12, 3, None, ""),
    ("십이에 삼", 12, 3, None, ""),
    ("열둘에 셋", 12, 3, None, ""),
    ("이십삼의 일", 23, 1, None, ""),
    ("7의 1", 7, 1, None, ""),
    ("칠의 일", 7, 1, None, ""),
    ("12A의 3", 12, 3, None, "A"),
    ("12에이의 3", 12, 3, None, "A"),
    ("12번의 3번", 12, 3, None, ""),
    ("자 갑니다, 12의 3, 레디 액션", 12, 3, None, ""),
    ("슬레이트. 5의 2. 액션!", 5, 2, None, ""),
    ("씬 3 테이크 1 마크", 3, 1, None, ""),
    ("Scene 12 Take 3", 12, 3, None, ""),
    ("S12 T3", 12, 3, None, ""),
    ("12/3", 12, 3, None, ""),
    ("백일의 이", 101, 2, None, ""),
    ("3의 12", 3, 12, None, ""),
]

NEGATIVE = [
    "레디 액션",
    "",
    "   ",
    "오늘 날씨 좋네요",
    "이거 사람이 많네",   # '이', '사'가 수사로 오인되면 안 됨
]


def main():
    fails = 0
    for text, scene, a, b, suffix in CASES:
        info = parse_slate(text)
        # 3단계 케이스는 (scene, shot, take) 로 적음
        if b is not None:
            exp_shot, exp_take = a, b
        else:
            exp_shot, exp_take = None, a
        ok = (info.scene == scene and info.take == exp_take and info.shot == exp_shot
              and info.scene_suffix == suffix)
        status = "OK " if ok else "FAIL"
        if not ok:
            fails += 1
        print(f"[{status}] {text!r:40} -> scene={info.scene} suffix={info.scene_suffix!r} "
              f"shot={info.shot} take={info.take}  ({info.pattern}) norm={normalize_numbers(text)!r}")
    for text in NEGATIVE:
        info = parse_slate(text)
        ok = not info.complete
        if not ok:
            fails += 1
        print(f"[{'OK ' if ok else 'FAIL'}] NEG {text!r:36} -> complete={info.complete} "
              f"scene={info.scene} take={info.take}")

    for text, exp in [("오케이 컷", True), ("컷, 다시 갈게요", False), ("컷", None), ("좋았어 오케이", True)]:
        got = detect_ok_call(text)
        ok = got == exp
        if not ok:
            fails += 1
        print(f"[{'OK ' if ok else 'FAIL'}] OK-call {text!r:24} -> {got}")

    print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
