"""Prepare a pack of real reels for a hand-checked accuracy measurement, and score it afterwards.

  python benchmarks/make_wer_pack.py pack OUT_DIR FOLDER [FOLDER ...]   # pick 16 videos (8 pl, 8 other), write mp3 + editable text files
  python benchmarks/make_wer_pack.py score OUT_DIR                      # word error rate of the tool's text against YOUR corrected text

The pack is for local use only: it contains other people's audio. Do not commit it or publish it; publish only the numbers `score` prints.

How to check one file (about 2 minutes per reel): open `<id>.txt`, play `<id>.mp3`, and change the text until it is exactly what is said
(keep one sentence per line; write numbers as digits, as the tool does; keep names as spelled in the caption at the top). Do not fix style or
grammar, only what was actually said. Put `DONE` in the first line when a file is finished; `score` ignores the others.
"""
import json
import random
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def norm(t):
    t = t.lower().replace("’", "'")
    return re.sub(r"[^\w\s']", " ", t).split()


def wer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)], len(r)


def pack(out, folders):
    from reel_intel.common import apply_fixes
    cands = {"pl": [], "other": []}
    for folder in folders:
        for p in sorted(Path(folder, "data").glob("*/features.json")):
            f = json.loads(p.read_text(encoding="utf-8"))
            tr = json.loads((p.parent / "transcript.json").read_text(encoding="utf-8"))
            if f.get("word_count", 0) >= 60 and f.get("speech_reliable") and 20 <= f["duration_s"] <= 75 and (p.parent / "audio.wav").exists():
                cands["pl" if tr["language"] == "pl" else "other"].append((p.parent, f, tr))
    rng = random.Random(11)
    chosen = rng.sample(cands["pl"], min(8, len(cands["pl"]))) + rng.sample(cands["other"], min(8, len(cands["other"])))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for d, f, tr in chosen:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(d / "audio.wav"), "-ac", "1", "-b:a", "48k", str(out / f"{f['id']}.mp3")], check=True)
        lines = [apply_fixes(s["text"], tr["language"])[0] for s in tr["segments"]]
        (out / f"{f['id']}.txt").write_text(
            f"TODO  (replace with DONE when you have corrected this file)\n# {f['id']} | language {tr['language']} | {f['duration_s']:.0f} s | {f['url']}\n"
            f"# caption: {(f.get('caption') or '')[:200]!r}\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
        (out / f"{f['id']}.original.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")        # the tool's text, never edit
    (out / "README.txt").write_text(__doc__, encoding="utf-8")
    print(f"{len(chosen)} videos in {out} (pl: {sum(1 for c in chosen if c[2]['language'] == 'pl')})")


def score(out):
    out = Path(out)
    rows = []
    for p in sorted(out.glob("*.txt")):
        if p.name.endswith(".original.txt") or p.name == "README.txt":
            continue
        text = p.read_text(encoding="utf-8").splitlines()
        if not text or text[0].strip() != "DONE":
            continue
        ref = " ".join(l for l in text[1:] if not l.startswith("#"))
        hyp = (out / (p.stem + ".original.txt")).read_text(encoding="utf-8")
        lang = re.search(r"language (\w+)", text[1]).group(1) if len(text) > 1 else "?"
        e, n = wer(ref, hyp)
        rows.append((p.stem, "pl" if lang == "pl" else "other", e, n))
    if not rows:
        print("no file marked DONE yet")
        return
    print("| group | videos | words | WER |\n|---|---|---|---|")
    for g in ("pl", "other"):
        rs = [r for r in rows if r[1] == g]
        if rs:
            print(f"| {g} | {len(rs)} | {sum(r[3] for r in rs)} | {sum(r[2] for r in rs) / sum(r[3] for r in rs):.1%} |")
    print(f"| all | {len(rows)} | {sum(r[3] for r in rows)} | {sum(r[2] for r in rows) / sum(r[3] for r in rows):.1%} |")
    worst = sorted(rows, key=lambda r: -r[2] / r[3])[:3]
    print("\nworst:", ", ".join(f"{r[0]} {r[2] / r[3]:.0%}" for r in worst))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["pack"] and len(a) >= 3:
        pack(a[1], a[2:])
    elif a[:1] == ["score"] and len(a) == 2:
        score(a[1])
    else:
        print(__doc__)
