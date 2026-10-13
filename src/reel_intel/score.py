"""score.py - check your own script against what your data actually shows.

  reel-intel score script.txt [--caption "text"] [--seconds N]
Reads the draft, estimates its length, and compares it with the scanned videos in the data folder:
(1) which features are CLEARLY linked with views in your data (99% interval) and whether your draft has them,
(2) the most similar scanned videos (to learn from, and to warn when a draft is too close to someone else's text).
It does not rate "quality" and does not invent rules: with no clear feature it says so.
"""
import math
import re
import sys
from collections import Counter
from pathlib import Path

from .analysis import FEATURES, feature_table
from .common import DATA, jload, visual_fields

WPS = 2.4          # spoken words per second (about 145 words per minute)
WORDS = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def tokens(text):
    return [w.lower() for w in WORDS.findall(text)]


def cosine(a, b, idf):
    va = {w: c * idf.get(w, 1) for w, c in a.items()}
    vb = {w: c * idf.get(w, 1) for w, c in b.items()}
    num = sum(va[w] * vb[w] for w in va.keys() & vb.keys())
    den = math.sqrt(sum(x * x for x in va.values())) * math.sqrt(sum(x * x for x in vb.values()))
    return num / den if den else 0.0


def load_corpus():
    reels, docs = [], {}
    for d in DATA.iterdir() if DATA.exists() else []:
        if not (d / "features.json").exists():
            continue
        f = jload(d / "features.json")
        tr = jload(d / "transcript.json")
        speech = " ".join(s["text"] for s in tr["segments"])
        ok = f.get("word_count", 0) >= 10 and f.get("speech_reliable", True)
        reels.append({"id": f["id"], "views": f.get("views"), "dur": f.get("duration_s"), "mode": "speech" if ok else "visual", "hook": f.get("hook_spoken") or "",
                      "caption": f.get("caption") or "", "hour": f.get("hour"), "weekday": f.get("weekday"), "url": f.get("url"), **visual_fields(f)})
        docs[f["id"]] = Counter(tokens(speech + " " + (f.get("caption") or "")))
    return reels, docs


def score(text, caption="", seconds=None):
    reels, docs = load_corpus()
    words = text.split()
    est = seconds or round(len(words) / WPS)
    first = re.split(r"(?<=[.?!])\s", text.strip())[0] if text.strip() else ""
    draft = {"views": None, "dur": est, "mode": "speech", "hook": first, "caption": caption, "hour": None, "weekday": None}
    out = [f"# Script check", "", f"- words: {len(words)}, estimated length: about {est} s (at {WPS * 60:.0f} words/min)", f"- first sentence ({len(first.split())} words): {first!r}", ""]
    scored = [r for r in reels if r.get("views")]
    if len(scored) < 10:
        out.append("Fewer than 10 scanned videos with views in this data folder: no feature can be tested yet. Scan more videos first.")
    else:
        out += ["## What your own data says (only clear differences, 99% interval)", ""]
        clear = [r for r in feature_table(scored, "en", n_boot=3000) if r["status"] == "clear"]
        lab = {k: fn for k, _, _, fn in FEATURES}
        for r in clear:
            has = lab[r["key"]](draft)
            good = (r["ratio"] > 1) == bool(has)
            tip = "keep it" if good else ("consider adding it" if r["ratio"] > 1 else "consider avoiding it")
            out.append(f"- **{r['label']}**: videos with it have {r['ratio']:.1f}x the typical views of those without ({r['lo']:.1f}-{r['hi']:.1f}). Your draft: {'yes' if has else 'no' if has is not None else 'unknown'} -> {tip}.")
        if not clear:
            out.append("- No feature is clearly linked with views in this data. Do not tune the draft to a feature; topic and idea matter more.")
        out += ["", "Features overlap (short videos are usually the ones without speech), so read them together, not as separate rules.", ""]
    if docs:
        idf = {}
        n = len(docs)
        for c in docs.values():
            for w in c:
                idf[w] = idf.get(w, 0) + 1
        idf = {w: math.log((1 + n) / (1 + df)) + 1 for w, df in idf.items()}
        mine = Counter(tokens(text + " " + caption))
        sims = sorted(((cosine(mine, c, idf), rid) for rid, c in docs.items()), reverse=True)[:3]
        byid = {r["id"]: r for r in reels}
        out += ["## Most similar scanned videos", ""]
        for sc, rid in sims:
            r = byid[rid]
            warn = "  <- very close to this text: rewrite in your own words" if sc >= 0.6 else ""
            out.append(f"- {sc:.2f} similarity, {int(r['views']) if r['views'] else '?'} views: {r['url']}{warn}")
    return "\n".join(out) + "\n"


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)

    def pop(name):
        if name in args:
            i = args.index(name)
            v = args[i + 1]
            del args[i:i + 2]
            return v
    caption, seconds = pop("--caption") or "", pop("--seconds")
    if not args:
        print(__doc__)
        return
    print(score(Path(args[0]).read_text(encoding="utf-8"), caption, int(seconds) if seconds else None))


if __name__ == "__main__":
    main()
