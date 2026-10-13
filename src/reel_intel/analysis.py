"""analysis.py - label-free analysis: which measurable features go with more views, with honest uncertainty.

For every yes/no feature of a video (short, speech, question in the hook, ...) compare the median views of the videos that have it with
those that do not, and put a bootstrap confidence interval on the ratio. A difference is called "clear" only when the 99% interval
excludes 1 and both groups have at least MIN_N videos. Pure functions: no files, no network (unit tested).
"""
import random
import re
import statistics as st

MIN_N = 5
CTA_RE = re.compile(r"(?:skomentuj|napisz|zostaw|zapisz|comment|write|type|drop|send|dm|reply|tell me)\b", re.I)

# key, label (pl), label (en), function(reel) -> True / False / None (unknown: video skipped for this feature)
FEATURES = [
    ("short", "krótki reel (do 15 s)", "short video (up to 15 s)", lambda r: r["dur"] <= 15 if r.get("dur") else None),
    ("long", "długi reel (ponad 45 s)", "long video (over 45 s)", lambda r: r["dur"] > 45 if r.get("dur") else None),
    ("speech", "z mową (nie sam tekst/obraz)", "with speech (not only text/picture)", lambda r: r.get("mode") == "speech" if r.get("mode") in ("speech", "visual") else None),
    ("question", "pytanie w pierwszym zdaniu", "question in the first sentence", lambda r: ("?" in r["hook"]) if r.get("hook") else None),
    ("number", "liczba w pierwszym zdaniu", "number in the first sentence", lambda r: bool(re.search(r"\d", r["hook"])) if r.get("hook") else None),
    ("cta", "prośba o komentarz/akcję w opisie", "ask for a comment/action in the caption", lambda r: bool(CTA_RE.search(r.get("caption") or "")) if r.get("caption") is not None else None),
    ("long_caption", "długi opis (ponad 300 znaków)", "long caption (over 300 characters)", lambda r: len(r.get("caption") or "") > 300),
    ("evening", "publikacja po 17:00", "posted after 17:00", lambda r: r["hour"] >= 17 if r.get("hour") is not None else None),
    ("weekend", "publikacja w weekend", "posted on a weekend", lambda r: r["weekday"] >= 5 if r.get("weekday") is not None else None),
    # not from the transcript: speech pace from the words, the rest from `reel-intel visuals` (None until that has run)
    ("fast_speech", "szybka mowa (ponad 170 słów/min)", "fast speech (over 170 words/min)", lambda r: r["wpm"] > 170 if r.get("wpm") else None),
    ("fast_cuts", "szybki montaż (ponad 4 cięcia na 10 s)", "fast editing (over 4 cuts per 10 s)", lambda r: r["cuts10"] > 4 if r.get("cuts10") is not None else None),
    ("face_early", "twarz w pierwszych 3 s", "a face in the first 3 s", lambda r: r["face3"] if r.get("face3") is not None else None),
    ("text_early", "tekst na ekranie w pierwszych 2 s", "text on screen in the first 2 s", lambda r: r["text2"] if r.get("text2") is not None else None),
    ("burned", "napisy wypalone w obrazie", "captions burned into the picture", lambda r: r["burned"] if r.get("burned") is not None else None),
]


CONF = 0.99          # stricter than 95%: fourteen features are tested at once, so some "clear" results would be chance at 95%


def ratio_ci(a, b, n_boot=4000, seed=1, conf=CONF):
    """median(a)/median(b) with a percentile bootstrap interval (default 99%)"""
    rnd = random.Random(seed)
    base = st.median(a) / st.median(b)
    rs = []
    for _ in range(n_boot):
        ra = st.median(rnd.choices(a, k=len(a)))
        rb = st.median(rnd.choices(b, k=len(b)))
        rs.append(ra / rb)
    rs.sort()
    tail = (1 - conf) / 2
    return base, rs[int(tail * n_boot)], rs[int((1 - tail) * n_boot) - 1]


def feature_table(reels, lang="pl", n_boot=4000):
    """rows sorted by effect size; each row says whether the difference is clear, unclear or untestable (small groups)"""
    V = [r for r in reels if r.get("views") and r.get("mode") != "blocked"]
    rows = []
    for key, pl, en, fn in FEATURES:
        yes, no = [], []
        for r in V:
            v = fn(r)
            if v is None:
                continue
            (yes if v else no).append(r["views"])
        row = {"key": key, "label": en if lang == "en" else pl, "n_yes": len(yes), "n_no": len(no)}
        if len(yes) < MIN_N or len(no) < MIN_N:
            row.update(status="small", ratio=None, lo=None, hi=None)
        else:
            ratio, lo, hi = ratio_ci(yes, no, n_boot)
            row.update(status="clear" if (lo > 1 or hi < 1) else "unclear", ratio=ratio, lo=lo, hi=hi,
                       med_yes=st.median(yes), med_no=st.median(no))
        rows.append(row)
    rows.sort(key=lambda r: (r["status"] == "small", -abs(__import__("math").log(r["ratio"])) if r["ratio"] else 0))
    return rows
