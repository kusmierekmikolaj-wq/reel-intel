"""insights.py - add the numbers only the account owner can see (reach, saves, shares, watch time) to scanned videos.

  reel-intel insights EXPORT.csv
EXPORT.csv: a per-post export from the platform's own analytics (e.g. Meta Business Suite, TikTok Studio, YouTube Studio) saved as CSV.
Columns are recognised by name (English or Polish synonyms); rows are matched to scanned videos by the link or id found in any cell.
Result: <data>/data/<id>/insights.json and a summary with the signals the algorithm is said to weigh (watch time, shares per reach, saves per reach).
Only checked on synthetic exports so far: report a mismatching header and it will be added to SYN.
"""
import csv
import json
import re
import statistics as st
import sys
from pathlib import Path

from .common import DATA, jload, reel_id
from .report import perm_p, spearman

SYN = {
    "reach": ["reach", "accounts reached", "unique viewers", "zasieg", "zasięg", "dotarcie", "konta ktore zobaczyly"],
    "plays": ["plays", "views", "video views", "wyswietlenia", "wyświetlenia", "odtworzenia"],
    "likes": ["likes", "polubienia", "reactions", "reakcje"],
    "comments": ["comments", "komentarze"],
    "shares": ["shares", "sends", "reposts", "udostepnienia", "udostępnienia", "wyslania", "wysłania"],
    "saves": ["saves", "saved", "zapisy", "zapisania", "zapisane"],
    "avg_watch_s": ["average watch time", "avg watch time", "average view duration", "sredni czas ogladania", "średni czas oglądania"],
    "watch_total_s": ["watch time", "total watch time", "laczny czas ogladania", "łączny czas oglądania"],
    "link": ["permalink", "link", "url", "post url", "post id", "reel id", "media id", "id"],
}


def norm(h):
    return re.sub(r"\s+", " ", re.sub(r"[^\w ]", "", h.lower())).strip()


def number(s):
    """'1 234' / '1,234' / '12,5%' / '0:23' -> float (seconds for m:ss, percent as 12.5)"""
    s = (s or "").strip().replace("\xa0", " ")
    m = re.fullmatch(r"(\d+):(\d{2})(?::(\d{2}))?", s)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
        return a * 3600 + b * 60 + int(c) if c else a * 60 + b
    s = s.replace("%", "").replace(" ", "")
    if re.fullmatch(r"\d{1,3}(,\d{3})+", s):
        s = s.replace(",", "")
    elif re.fullmatch(r"\d+,\d+", s):
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def column_map(header):
    cols, hn = {}, [norm(h) for h in header]
    for key, names in SYN.items():
        for i, h in enumerate(hn):
            if i not in cols.values() and h in [norm(n) for n in names]:
                cols[key] = i
                break
    return cols


def find_id(row, known):
    for cell in row:
        c = cell.strip()
        if c in known:
            return c
        if c.startswith("http"):
            rid = reel_id(c)
            if rid in known:
                return rid
    return None


def run(path):
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(raw[:4000], delimiters=",;\t")
    rows = list(csv.reader(raw.splitlines(), dialect))
    header, body = rows[0], rows[1:]
    cols = column_map(header)
    known = {d.name for d in DATA.iterdir() if (d / "features.json").exists()}
    matched, per = 0, {}
    for r in body:
        rid = find_id(r, known)
        if not rid:
            continue
        rec = {k: number(r[i]) for k, i in cols.items() if k != "link" and i < len(r)}
        f = jload(DATA / rid / "features.json")
        if rec.get("avg_watch_s") is None and rec.get("watch_total_s") and rec.get("plays"):
            rec["avg_watch_s"] = rec["watch_total_s"] / rec["plays"]
        base = rec.get("reach") or rec.get("plays")
        if base:
            for k in ("saves", "shares", "likes", "comments"):
                if rec.get(k) is not None:
                    rec[f"{k}_per_reach"] = rec[k] / base
        if rec.get("avg_watch_s") and f.get("duration_s"):
            rec["watch_ratio"] = min(rec["avg_watch_s"] / f["duration_s"], 1.5)
        (DATA / rid / "insights.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
        per[rid] = (rec, f)
        matched += 1
    print(f"columns recognised: {sorted(k for k in cols if k != 'link')} | link column: {'yes' if 'link' in cols else 'no'}")
    print(f"matched {matched} of {len(body)} rows to scanned videos ({len(known)} in the data folder)")
    if matched < 10:
        print("fewer than 10 matched videos: summary skipped")
        return per
    print("\nsignal                      median      rank correlation with public views")
    for k in ("watch_ratio", "shares_per_reach", "saves_per_reach", "likes_per_reach", "comments_per_reach"):
        pairs = [(rec[k], f["views"]) for rec, f in per.values() if rec.get(k) is not None and f.get("views")]
        if len(pairs) < 10:
            continue
        x, y = [p[0] for p in pairs], [p[1] for p in pairs]
        rho = spearman(x, y)
        print(f"{k:26s} {st.median(x):>9.4f}   rho={rho:+.2f}  p={perm_p(x, y, rho):.3f}  (n={len(pairs)})")
    print("\nA strong link between a hidden signal (watch ratio, shares per reach) and public views is evidence for WHY a video travelled; no link means the public numbers hide it.")
    return per


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return
    run(args[0])


if __name__ == "__main__":
    main()
