"""compare.py - several scanned profiles side by side: which patterns repeat, which belong to one account.

  reel-intel compare FOLDER FOLDER [FOLDER ...] [--out COMPARE.md]
Each FOLDER is a data folder (data/<id>/features.json, optional topics.csv). Nothing is assumed to be universal: a feature is reported as
"repeats" only when its difference is clear (99% interval) in at least two profiles and points the same way.
"""
import json
import re
import statistics as st
import sys
from pathlib import Path

from .analysis import FEATURES, feature_table
from .common import visual_fields
from .report import perm_p, spearman


def load_profile(folder):
    folder = Path(folder)
    F = {}
    for p in (folder / "data").glob("*/features.json"):
        f = json.loads(p.read_text(encoding="utf-8"))
        vp = p.parent / "visual.json"
        f["_visual"] = json.loads(vp.read_text(encoding="utf-8")) if vp.exists() else {}
        F[f["id"]] = f
    topics = {}
    if (folder / "topics.csv").exists():
        topics = dict(l.split(",", 1) for l in (folder / "topics.csv").read_text(encoding="utf-8").splitlines()[1:] if "," in l)
    handles = re.findall(r"(?:instagram|tiktok)\.com/@?([\w.]+)", " ".join(f.get("url") or "" for f in F.values()))
    who = [f.get("uploader") for f in F.values() if f.get("uploader")]
    name = ("@" + max(set(handles), key=handles.count)) if handles else (max(set(who), key=who.count) if who else folder.name)
    return name, F, topics


def adapt(F):
    return [{"views": f.get("views"), "dur": f.get("duration_s"), "mode": "speech" if f.get("word_count", 0) >= 10 and f.get("speech_reliable", True) else "visual",
             "hook": f.get("hook_spoken") or "", "caption": f.get("caption") or "", "hour": f.get("hour"), "weekday": f.get("weekday"), "id": f.get("id"), **visual_fields(f)} for f in F.values()]


def summary(name, F, topics):
    V = {k: f["views"] for k, f in F.items() if f.get("views")}
    vals = sorted(V.values(), reverse=True)
    med = st.median(vals)
    top10 = max(1, round(len(vals) * 0.1))
    s = {"name": name, "n": len(vals), "median": med, "top": vals[0], "top_x": vals[0] / med, "concentration": sum(vals[:top10]) / sum(vals),
         "n_outliers": sum(1 for v in vals if v >= 5 * med), "langs": sorted({f.get("language") for f in F.values() if f.get("language") and f.get("speech_reliable", True) and f.get("word_count", 0) >= 10})}
    timed = [f for f in F.values() if f.get("views") and f.get("hour") is not None]
    if len(timed) >= 10:
        v = [f["views"] for f in timed]
        s["p_hour"] = perm_p([f["hour"] for f in timed], v, spearman([f["hour"] for f in timed], v))
        s["p_weekday"] = perm_p([f["weekday"] for f in timed], v, spearman([f["weekday"] for f in timed], v))
    groups = {}
    for k, v in V.items():
        if topics.get(k):
            groups.setdefault(topics[k], []).append(v)
    best = sorted(((g, len(x), st.median(x)) for g, x in groups.items() if len(x) >= 3), key=lambda t: -t[2])
    s["best_group"] = (best[0][0], best[0][2] / med, best[0][1]) if len(best) >= 2 else None
    s["features"] = {r["key"]: r for r in feature_table(adapt(F), "en", n_boot=3000)} if len(vals) >= 10 else {}
    return s


def human(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else f"{n:.0f}"


def build(profiles):
    S = [summary(*p) for p in profiles]
    L = ["# Profile comparison", "", "Counts are videos with a known view count. Patterns are hypotheses from small samples; a feature is called clear only when its 99% interval excludes 1.", "",
         "| | " + " | ".join(s["name"] for s in S) + " |", "|---|" + "---|" * len(S)]
    rows = [("videos", lambda s: str(s["n"])), ("languages", lambda s: ", ".join(s["langs"])), ("typical video (median views)", lambda s: human(s["median"])),
            ("best video", lambda s: f"{human(s['top'])} ({s['top_x']:.0f}x typical)"), ("videos >= 5x typical", lambda s: str(s["n_outliers"])),
            ("views held by the top 10% of videos", lambda s: f"{s['concentration']:.0%}"),
            ("posting hour vs views (p)", lambda s: f"{s['p_hour']:.2f}" if "p_hour" in s else "-"), ("weekday vs views (p)", lambda s: f"{s['p_weekday']:.2f}" if "p_weekday" in s else "-"),
            ("best topic group (x typical, n)", lambda s: f"{s['best_group'][0]} ({s['best_group'][1]:.1f}x, {s['best_group'][2]})" if s["best_group"] else "-")]
    for label, fn in rows:
        L.append(f"| {label} | " + " | ".join(fn(s) for s in S) + " |")
    L += ["", "## Features (ratio of typical views with / without the feature)", "", "| feature | " + " | ".join(s["name"] for s in S) + " |", "|---|" + "---|" * len(S)]
    repeats, nowhere = [], []
    for key, pl, en, _ in FEATURES:
        cells, dirs = [], []
        for s in S:
            r = s["features"].get(key)
            if not r or r["status"] == "small":
                cells.append("too few")
            elif r["status"] == "clear":
                cells.append(f"**{r['ratio']:.2f}x** ({r['lo']:.2f}-{r['hi']:.2f})")
                dirs.append(1 if r["ratio"] > 1 else -1)
            else:
                cells.append(f"{r['ratio']:.2f}x (unclear)")
        L.append(f"| {en} | " + " | ".join(cells) + " |")
        if len(dirs) >= 2 and len(set(dirs)) == 1:
            repeats.append((en, "higher" if dirs[0] > 0 else "lower", len(dirs)))
        if not dirs and all(s["features"].get(key) and s["features"][key]["status"] != "small" for s in S):
            nowhere.append(en)
    L += ["", "## What repeats", ""]
    L += [f"- **{en}**: clear in {n} of {len(S)} profiles, views {d}." for en, d, n in repeats] or ["- No feature is clear in two or more profiles."]
    if nowhere:
        L += ["", "No clear effect in any profile: " + ", ".join(nowhere) + "."]
    L += ["", "Reading: a pattern that repeats across unrelated accounts is a better bet than one seen once; a pattern seen in one profile only is that account's style or audience."]
    return "\n".join(L) + "\n", S


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    out = Path(args[args.index("--out") + 1]) if "--out" in args else Path("COMPARE.md")
    if "--out" in args:
        i = args.index("--out")
        del args[i:i + 2]
    folders = [a for a in args if Path(a).exists()]
    if len(folders) < 2:
        print("usage: reel-intel compare FOLDER FOLDER [...] [--out COMPARE.md]")
        return
    md, S = build([load_profile(f) for f in folders])
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"written {out}")


if __name__ == "__main__":
    main()
