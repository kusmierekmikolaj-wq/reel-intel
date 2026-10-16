"""make_props.py - writes the props files the layout check renders (qa/props/*.json).

Every case is one scene (or two, for the transition) with a known number of slogan and caption lines. The text was chosen so the line counts are
the ones in the name (s3c5 = a three-line slogan over a five-line caption); check_layout.py measures the real count from the picture and says
when a case does not have the lines its name promises.

Run:  python qa/make_props.py        (from the studio folder; plain Python, no extra packages)
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "props"

SHORT = "Try it today."
LONG5 = "You made thirty reels this month and honestly you watched none of them, not even your best ones, so who were they for?"
MID3 = "You made thirty reels this month and honestly you watched none of them."
SLOGAN = {
    1: "Free tool",
    3: "QUALITY CONTROL REPORT",                                    # 22 characters: the big size (138 px) and three lines, the case a reviewer measured
    4: "Misunderstanding Communication Infrastructure Transformation Implementation",   # five long words: four lines even at the smallest size
}
CHIPS = ["/human", "SKILL.md", "ffprobe", "remotion"]


def scene(screen, text, start, end, chips=None, emphasis=None):
    ws = text.split()
    step = (end - start) / len(ws)
    words = [[w, round(start + i * step, 3), round(start + (i + 1) * step, 3)] for i, w in enumerate(ws)]
    return {"text": text, "screen": screen, "emphasis": emphasis or [], "chips": chips or [], "start": start, "end": end, "words": words}


def props(scenes, total, mascot=True, mouth_open=False):
    n = int(total * 30) + 1
    mouth = [0.0] * n
    vis = [0] * n
    if mouth_open:                                               # a talking mouth, so the widest mouth shapes are on screen too
        mouth = [0.6 + 0.3 * ((i // 4) % 2) for i in range(n)]
        vis = [1 + (i // 6) % 4 for i in range(n)]
    return {"total": total, "audio": "", "mascot": mascot, "mouth": mouth, "vis": vis, "scenes": scenes}


def write(name, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def main():
    # one long scene per case (0.4 s .. 4.4 s): frame 60 is settled and the moving scan line is still above the picture
    cases = {
        "s1c1": (SLOGAN[1], SHORT, None),
        "s1c5": (SLOGAN[1], LONG5, None),
        "s3c1": (SLOGAN[3], SHORT, None),
        "s3c5": (SLOGAN[3], LONG5, None),
        "s4c1": (SLOGAN[4], SHORT, None),
        "s4c5": (SLOGAN[4], LONG5, None),
        "s3c3chips": (SLOGAN[3], MID3, CHIPS),
        "s4c5chips": (SLOGAN[4], LONG5, CHIPS),                  # the overload: chips, the longest slogan and the longest caption together
        "cta5": ("", LONG5, None),                               # the call to action scene: a pulse instead of a slogan
    }
    for name, (screen, text, chips) in cases.items():
        write(name, props([scene(screen, text, 0.4, 4.4, chips, ["none"])], 5.0, mouth_open=True))
    write("s3c5_nomascot", props([scene(SLOGAN[3], LONG5, 0.4, 4.4)], 5.0, mascot=False))
    write("s4c5_nomascot", props([scene(SLOGAN[4], LONG5, 0.4, 4.4)], 5.0, mascot=False))
    # a transition: scene A ends at 2.6 s, scene B starts 0.28 s later (the generator's GAP). Both have text, so a ghost shows as two texts at once.
    a = scene("QUALITY CONTROL REPORT","You made thirty reels this month.", 0.4, 2.6)
    b = scene("Zero reels watched", "Not one of them was watched, not even by you.", 2.88, 5.2, ["/human", "SKILL.md"])
    write("transition", props([a, b], 6.0, mouth_open=True))
    # the very start: scene one begins at 0.4 s, the mascot enters from below
    write("start", props([scene(SLOGAN[3], MID3, 0.4, 3.4)], 4.0, mouth_open=True))
    print("wrote", len(list(OUT.glob("*.json"))), "props files to", OUT)


if __name__ == "__main__":
    main()
