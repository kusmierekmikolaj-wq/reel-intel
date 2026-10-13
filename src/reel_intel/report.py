"""report.py - library index and the numbers behind "why did it go viral".

  reel.py report                  # INDEX.md: reels by views, x overall median, x rolling median, links
  reel.py report compare <id>     # one reel vs the rest (metrics, rank)
  reel.py report groups           # median views per topic group (topics.csv) + robustness (drop the top reel / top 3)
  reel.py report timing           # posting hour / weekday vs views (rank correlation + permutation p-value)
  reel.py report hooks            # hook type (from the first 3 s of speech) vs views
  reel.py report texts            # TEKSTY_REELI.md (exact text of every reel) + SKROTY_REELI.md (all short versions)
  reel-intel report content        # rewrite every content_full.md from stored data (after a language or glossary change)
  reel-intel report csv            # reels.csv for Excel
  reel.py report clean [--yes]    # list (or delete) reel folders without audio = failed/partial scans
topics.csv (id,group) is written by Claude when it labels the reels (see skill why-it-went-viral).
"""
import random
import shutil
import statistics as st
import sys

from .common import DATA, WORK, jload, visual_fields


def load():
    return {p.name: jload(p / "features.json") for p in DATA.iterdir() if (p / "features.json").exists()}


def with_views(F):
    return {k: f for k, f in F.items() if f.get("views")}


def rolling_median(F, k=10):
    """median views of up to k reels before and after (by post time), excluding the reel itself"""
    rows = sorted(((f["timestamp"], rid) for rid, f in with_views(F).items() if f.get("timestamp")))
    out = {}
    for i, (_, rid) in enumerate(rows):
        nb = [F[r]["views"] for _, r in rows[max(0, i - k):i] + rows[i + 1:i + 1 + k]]
        if len(nb) >= 5:
            out[rid] = st.median(nb)
    return out


def index(F):
    V = with_views(F)
    med = st.median(f["views"] for f in V.values()) if V else None
    roll = rolling_median(F)
    rows = sorted(((f.get("views") or 0, rid, f) for rid, f in F.items()), reverse=True)
    head = f"mediana wyświetleń: {med:,.0f}" if med else "brak liczb wyświetleń (dodaj: `reel.py scan --grid grid.tsv urls.txt`)"
    L = ["# Indeks reeli (od najpopularniejszego)", "", f"Reeli: {len(rows)} | {head} | x kroczącej = względem mediany 10 reeli przed i 10 po (wg daty)", "",
         "| reel | wyśw. | x mediana | x krocząca | lajki | koment. | czas | data | treść |", "|---|---|---|---|---|---|---|---|---|"]
    for v, rid, f in rows:
        short = f"[krótka](data/{rid}/content_short.md) / " if (DATA / rid / "content_short.md").exists() else ""
        vs, xs = (f"{v:,.0f}", f"{v / med:.1f}x") if v and med else ("-", "-")
        xr = f"{v / roll[rid]:.1f}x" if v and rid in roll else "-"
        L.append(f"| {rid} | {vs} | {xs} | {xr} | {f.get('likes')} | {f.get('comments')} | {f['duration_s']:.0f}s | {f.get('upload_date')} | {short}[pełna](data/{rid}/content_full.md) |")
    (WORK / "INDEX.md").write_text("\n".join(L), encoding="utf-8")
    print(f"INDEX.md: {len(rows)} reels, {head}")


def compare(F, rid):
    t = F[rid]
    others = [f for k, f in F.items() if k != rid and f.get("views")]
    if not t.get("views") or len(others) < 3:
        print("need views for this reel and at least 3 other reels with views (reel.py scan --grid grid.tsv urls.txt)")
        return
    print(f"{rid}: {t['views']:,.0f} views = {t['views'] / st.median(f['views'] for f in others):.1f}x median of {len(others)} others\n")
    print(f"{'metric':18s} {'this reel':>11s} {'median others':>14s} {'min':>9s} {'max':>9s}")
    for k in ("duration_s", "words_per_min", "first_word_at_s", "hook_words_3s", "views", "likes", "comments"):
        v = [f[k] for f in others if f.get(k) is not None]
        if v and t.get(k) is not None:
            print(f"{k:18s} {t[k]:>11,.1f} {st.median(v):>14,.1f} {min(v):>9,.0f} {max(v):>9,.0f}")
    for name, a in (("likes/views %", "likes"), ("comments/views %", "comments")):
        vals = sorted(f[a] / f["views"] * 100 for f in others if f.get(a) is not None)
        me = t[a] / t["views"] * 100
        print(f"{name:18s} {me:>11.2f} {st.median(vals):>14.2f} {min(vals):>9.2f} {max(vals):>9.2f}   rank {sum(1 for x in vals if x > me) + 1}/{len(vals) + 1}")


def groups(F):
    p = WORK / "topics.csv"
    if not p.exists():
        print("topics.csv missing: Claude labels each reel (id,group) first, see skill why-it-went-viral")
        return
    topic = dict(l.split(",", 1) for l in p.read_text(encoding="utf-8").splitlines()[1:] if "," in l)
    V = {k: f["views"] for k, f in with_views(F).items() if k in topic}
    ordered = sorted(V, key=lambda k: -V[k])

    def table(drop, label):
        keep = {k: v for k, v in V.items() if k not in ordered[:drop]}
        med = st.median(keep.values())
        print(f"\n{label}: {len(keep)} reels, overall median {med:,.0f}")
        print(f"{'group':44s} {'n':>3s} {'median':>10s} {'xmedian':>8s} {'min':>9s} {'max':>10s}")
        by = {}
        for k, v in keep.items():
            by.setdefault(topic[k], []).append(v)
        for g, v in sorted(by.items(), key=lambda kv: -st.median(kv[1])):
            print(f"{g:44s} {len(v):>3d} {st.median(v):>10,.0f} {st.median(v) / med:>7.1f}x {min(v):>9,.0f} {max(v):>10,.0f}")

    table(0, "ALL")
    table(1, f"WITHOUT TOP REEL ({ordered[0]})")
    table(3, "WITHOUT TOP 3")
    unl = [k for k in with_views(F) if k not in topic]
    if unl:
        print(f"\nunlabelled reels (not in topics.csv): {unl}")


def ranks(x):
    order = sorted(range(len(x)), key=lambda i: x[i])
    r, i = [0.0] * len(x), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x, y):
    a, b = ranks(x), ranks(y)
    ma, mb = st.mean(a), st.mean(b)
    num = sum((p - ma) * (q - mb) for p, q in zip(a, b))
    den = (sum((p - ma) ** 2 for p in a) * sum((q - mb) ** 2 for q in b)) ** 0.5
    return num / den if den else 0.0


def perm_p(x, y, rho, n=5000):
    rnd = random.Random(1)
    y = list(y)
    hits = 0
    for _ in range(n):
        rnd.shuffle(y)
        hits += abs(spearman(x, y)) >= abs(rho)
    return (hits + 1) / (n + 1)


def timing(F):
    V = [f for f in with_views(F).values() if f.get("hour") is not None]
    if len(V) < 10:
        print(f"need >=10 reels with views and post time (have {len(V)}); rescan old reels to add 'hour'")
        return
    views = [f["views"] for f in V]
    print(f"{len(V)} reels with views + post time (machine-local time zone)")
    for name, key in (("hour", "hour"), ("weekday (0=Mon)", "weekday")):
        x = [f[key] for f in V]
        rho = spearman(x, views)
        print(f"{name:16s} rho={rho:+.2f}  permutation p={perm_p(x, views, rho):.3f}")
    lo = [f["views"] for f in V if f["hour"] < 17]
    hi = [f["views"] for f in V if f["hour"] >= 17]
    if lo and hi:
        print(f"posted before 17:00: n={len(lo)} median {st.median(lo):,.0f} | from 17:00: n={len(hi)} median {st.median(hi):,.0f}")
    print("Note: a small sample and one outlier can dominate; treat p > 0.05 as 'no evidence', not as 'no effect'.")


def hook_type(text, lang="pl"):
    """rule-based hook class from the first spoken sentence; only for languages we have rules for (pl, en), else None"""
    t = (text or "").lower().strip()
    if not t or lang not in ("pl", "en"):
        return None
    en = lang == "en"
    if "?" in t[:160]:
        return "pytanie"
    if t.startswith(("never", "stop ", "don't", "do not", "dont", "forget", "avoid") if en else ("nigdy", "przestań", "nie ", "uważaj", "zapomnij")):
        return "zakaz/ostrzeżenie"
    if any(c.isdigit() for c in t[:120]):
        return "liczba/wynik"
    if any(w in t[:100] for w in (("just ", "announced", "released", "launched", "breaking") if en else ("właśnie", "wypuścił", "udostępnił"))):
        return "news"
    starts = ("how to", "here's", "here is", "this is", "if you", "the one", "one thing", "the secret", "you can") if en else ("jak ", "oto ", "jeśli", "jeżeli", "usuwasz", "jedno")
    if t.startswith(starts) or (("trick" if en else "trik") in t[:100]):
        return "obietnica/trik"
    return "stwierdzenie/inne"


def hooks(F):
    by = {}
    for f in with_views(F).values():
        h = hook_type(f.get("hook_spoken"), f.get("language")) if f.get("speech_reliable", True) and f.get("word_count", 0) >= 10 else None
        if h:
            by.setdefault(h, []).append(f["views"])
    print(f"{'hook type':24s} {'n':>3s} {'median views':>13s}   (conclusion only if n >= 5)")
    for h, v in sorted(by.items(), key=lambda kv: -st.median(kv[1])):
        print(f"{h:24s} {len(v):>3d} {st.median(v):>13,.0f}{'' if len(v) >= 5 else '   n<5: no conclusion'}")


TEXT_LABELS = {
    "pl": {"files": ("TEKSTY_REELI.md", "SKROTY_REELI.md"), "h1": "Teksty z reeli (dosłownie)", "h2": "Skróty reeli (najważniejsze)",
           "intro": "Reeli: {n}, od najpopularniejszego. Mowa: transkrypcja Whisper (korekty słownika, np. „huk” → „hook”, są zastosowane tylko tutaj; surowy zapis w `transcript.json`). "
                    "Reele bez mowy mają tekst z ekranu (OCR, polskie znaki przywrócone w skrótach). Czasy i niepewne słowa: `data/<id>/content_full.md`.",
           "stats": "{views} wyświetleń | {likes} lajków | {comments} komentarzy | {dur:.0f} s | {date} | {url}", "none": "brak",
           "speech": "Mowa", "nospeech": "brak użytecznej mowy (muzyka lub reel wizualny).", "screen": "Tekst na ekranie (OCR, surowy)", "caption": "Opis posta",
           "noscan": "(bez transkrypcji)"},
    "en": {"files": ("TEXTS.md", "SHORTS.md"), "h1": "Video texts (verbatim)", "h2": "Short versions (the essentials)",
           "intro": "Videos: {n}, most viewed first. Speech: Whisper transcript (glossary corrections are applied only here; the raw text is in `transcript.json`). "
                    "Videos without speech show their on-screen text (OCR). Timestamps and uncertain words: `data/<id>/content_full.md`.",
           "stats": "{views} views | {likes} likes | {comments} comments | {dur:.0f} s | {date} | {url}", "none": "none",
           "speech": "Speech", "nospeech": "no usable speech (music or a visual video).", "screen": "On-screen text (OCR, raw)", "caption": "Post caption",
           "noscan": "(no transcript)"},
}


def texts(F):
    """one readable file with the exact text of every video (TEKSTY_REELI.md / TEXTS.md) + all short versions (SKROTY_REELI.md / SHORTS.md)"""
    from .common import CFG, apply_fixes
    K = TEXT_LABELS.get(CFG.get("ui_language"), TEXT_LABELS["en"])
    rows = sorted(F.items(), key=lambda kv: -(kv[1].get("views") or 0))
    L = [f"# {K['h1']}", "", K["intro"].format(n=len(rows)), ""]
    S = [f"# {K['h2']}", ""]
    for n, (rid, f) in enumerate(rows, 1):
        d = DATA / rid
        tr = jload(d / "transcript.json")
        views = f"{f['views']:,.0f}" if f.get("views") else K["none"]
        L += [f"## {n}. {rid}", K["stats"].format(views=views, likes=f.get("likes"), comments=f.get("comments"), dur=f["duration_s"], date=f.get("upload_date"), url=f.get("url")), ""]
        if f.get("word_count", 0) >= 10 and f.get("speech_reliable", True):
            L += [f"**{K['speech']}:**", "", " ".join(apply_fixes(s["text"], tr["language"])[0] for s in tr["segments"]), ""]
        else:
            L += [f"**{K['speech']}:** {K['nospeech']}", ""]
        if (d / "onscreen.json").exists():
            seen = []
            for o in jload(d / "onscreen.json"):
                for t in o["text"]:
                    if t not in seen:
                        seen.append(t)
            L += [f"**{K['screen']}:**", "", " / ".join(seen) or f"({K['none']})", ""]
        L += [f"**{K['caption']}:**", "", f.get("caption") or f"({K['none']})", "", "---", ""]
        if (d / "content_short.md").exists():
            S += (d / "content_short.md").read_text(encoding="utf-8").splitlines()[1:] + ["", "---", ""]
    for d in sorted(DATA.iterdir()):                                  # reels that could not be downloaded: page text only
        if (d / "page_scrape.md").exists() and not (d / "features.json").exists():
            L += [f"## {K['noscan']} {d.name}", (d / "page_scrape.md").read_text(encoding="utf-8"), "", "---", ""]
    (WORK / K["files"][0]).write_text("\n".join(L), encoding="utf-8")
    (WORK / K["files"][1]).write_text("\n".join(S), encoding="utf-8")
    print(f"{K['files'][0]} + {K['files'][1]}: {len(rows)} videos")


def rebuild_content(F):
    """rewrite every content_full.md from the stored transcript and features (no network): after a label/language change or a glossary update"""
    from .scan import write_content
    n = 0
    for rid, f in F.items():
        d = DATA / rid
        if (d / "transcript.json").exists():
            write_content(d, jload(d / "transcript.json"), f)
            n += 1
    print(f"content_full.md rewritten: {n}")


def features_report(F):
    """label-free feature table (same numbers as the page) on the terminal"""
    from .analysis import feature_table
    reels = [{"views": f.get("views"), "dur": f.get("duration_s"), "mode": "speech" if f.get("word_count", 0) >= 10 and f.get("speech_reliable", True) else "visual",
              "hook": f.get("hook_spoken") or "", "caption": f.get("caption") or "", "hour": f.get("hour"), "weekday": f.get("weekday"), **visual_fields(f)} for f in F.values()]
    rows = feature_table(reels, "en")
    print(f"{'feature':44s} {'with':>16s} {'without':>16s}  ratio (99% range)        verdict")
    for r in rows:
        if r["status"] == "small":
            print(f"{r['label']:44s} {'-':>16s} {'-':>16s}  -                        too few ({r['n_yes']}/{r['n_no']})")
        else:
            print(f"{r['label']:44s} {r['med_yes']:>12,.0f} ({r['n_yes']:>2d}) {r['med_no']:>12,.0f} ({r['n_no']:>2d})  {r['ratio']:.2f} ({r['lo']:.2f} - {r['hi']:.2f})".ljust(100) + r["status"])


def export_csv(F):
    """reels.csv (opens in Excel): one row per reel with the numbers and the hook"""
    import csv
    topics = {}
    if (WORK / "topics.csv").exists():
        topics = dict(l.split(",", 1) for l in (WORK / "topics.csv").read_text(encoding="utf-8").splitlines()[1:] if "," in l)
    cols = ["id", "url", "date", "duration_s", "views", "likes", "comments", "language", "words", "hour", "weekday", "group", "hook"]
    with open(WORK / "reels.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for rid, f in sorted(F.items(), key=lambda kv: -(kv[1].get("views") or 0)):
            w.writerow([rid, f.get("url"), f.get("upload_date"), f.get("duration_s"), f.get("views"), f.get("likes"), f.get("comments"),
                        f.get("language"), f.get("word_count"), f.get("hour"), f.get("weekday"), topics.get(rid, ""), f.get("hook_spoken")])
    print(f"reels.csv: {len(F)} reels")


def clean(yes):
    bad = [p for p in DATA.iterdir() if p.is_dir() and not (p / "audio.wav").exists()
           and not (p / "page_scrape.md").exists() and not (p / "page_scrape.json").exists()]
    for p in bad:
        print(("deleting " if yes else "would delete ") + p.name)
        if yes:
            shutil.rmtree(p)
    print(f"{len(bad)} folder(s) without audio" + ("" if yes else " (add --yes to delete)"))


def main():
    a = sys.argv[1:]
    if a[:1] == ["clean"]:
        return clean("--yes" in a)
    F = load()
    if a[:1] == ["compare"] and len(a) > 1:
        compare(F, a[1])
    elif a[:1] == ["groups"]:
        groups(F)
    elif a[:1] == ["timing"]:
        timing(F)
    elif a[:1] == ["hooks"]:
        hooks(F)
    elif a[:1] == ["texts"]:
        texts(F)
    elif a[:1] == ["content"]:
        rebuild_content(F)
    elif a[:1] == ["csv"]:
        export_csv(F)
    elif a[:1] == ["features"]:
        features_report(F)
    else:
        index(F)


if __name__ == "__main__":
    main()
