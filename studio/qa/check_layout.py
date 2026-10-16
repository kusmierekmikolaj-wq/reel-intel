"""check_layout.py - measures where the pixels of real Remotion stills are, and compares them with the safe zone and with each other.

It does not trust the layout code: it renders stills with `npx remotion still` (props in qa/props, made by make_props.py) and measures the PNGs.

  python qa/check_layout.py all     --tag after       render every still, measure, print the table, write qa/results/after.json
  python qa/check_layout.py render  --tag before      only render (stills go to out/<tag>/)
  python qa/check_layout.py measure --tag after       only measure what is already in out/<tag>/
  (tag "before" = the old composition: it has no qaNoMascotArt prop, so its mascot is measured on an empty reel with the mascot on and off)

What is measured (pixels of the 1080x1920 frame):
  * text lines: bands of rows holding pixels with the three text colours (paper, signal yellow, muted blue), gap <= 5 px. A line inside the caption card
    belongs to the caption; above the card a mostly-yellow line is a chip, any other line is the slogan (or the pulse, for the scene without a slogan).
  * the caption card: its 2 px outer ring (a known colour) gives the exact outer box.
  * the progress bar: a short yellow stripe that starts at the bar's left end, x = 90.
  * the mascot: the difference between a still and the same still with the mascot's drawing off (prop qaNoMascotArt).
  * overlap: any two of slogan, chips, pulse, caption card, mascot, progress bar whose boxes intersect.
  * safe zone: text and card inside x 90..990 and y 270..1480 (qa/profile.py safe_zone: top 270, bottom 440, side 90; sources disagree, so it is a profile).
  * ghost: for the scene transition, the text that belongs only to scene A and only to scene B, frame by frame; both present = two texts at once.
Exit code 0 when no violation was found, 1 otherwise (2: rendering failed).
"""
import argparse
import concurrent.futures as cf
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
STUDIO = HERE.parent
PROPS = HERE / "props"
GEN = PROPS / "_gen"
W, H = 1080, 1920
SAFE = {"top": 270, "bottom": 440, "side": 90}
X0, X1, Y0, Y1 = SAFE["side"], W - SAFE["side"], SAFE["top"], H - SAFE["bottom"]          # 90..990, 270..1480

PAPER, SIGNAL, MUTE = (242, 238, 227), (255, 194, 75), (159, 176, 214)
RING_LO, RING_HI = (45, 65, 105), (82, 100, 148)                                              # the 2 px outer ring of the glass card, over the dark background
OLD_MASCOT_BOTTOM = 700               # the old mascot ends at y 670 (+ bob): above this row only the mascot is looked at, not the text

TEXT_FRAME = 60                       # all words are in, the scan line is still above the picture
MASCOT_FRAMES = (27, 80)              # the bob of the mascot is at its lowest (+9 px) and its highest (-9 px)
TRANSITION_FRAMES = [70, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 90, 94, 112]
TRANSITION_A, TRANSITION_B = 70, 112  # frames where scene A and scene B are settled
START_FRAMES = [0, 4, 9, 12, 16, 24]
# name -> (has slogan, has chips, has mascot)
CASES = {
    "s1c1": (True, False, True), "s1c5": (True, False, True), "s3c1": (True, False, True), "s3c5": (True, False, True),
    "s4c1": (True, False, True), "s4c5": (True, False, True), "s3c3chips": (True, True, True), "s4c5chips": (True, True, True),
    "cta5": (False, False, True), "s3c5_nomascot": (True, False, False), "s4c5_nomascot": (True, False, False),
}


# ---------------------------------------------------------------- rendering

def npx():
    return shutil.which("npx.cmd") or shutil.which("npx")


def variant(name, noart=False, empty=False, mascot=None, tint=False):
    """the props file for a still: the base file, optionally with the mascot's drawing off, the scenes removed, or the mascot switched"""
    base = json.loads((PROPS / f"{name}.json").read_text(encoding="utf-8"))
    tag = name
    if noart:
        base["qaNoMascotArt"] = True
        tag += "_noart"
    if tint:
        base["qaTint"] = True
        tag += "_tint"
    if empty:
        base["scenes"] = []
        tag += "_empty"
    if mascot is not None:
        base["mascot"] = mascot
        tag += "_m1" if mascot else "_m0"
    GEN.mkdir(parents=True, exist_ok=True)
    p = GEN / f"{tag}.json"
    p.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    return p


def png(tag, name, frame, suffix=""):
    return STUDIO / "out" / tag / f"{name}_f{frame}{suffix}.png"


def plan(tag):
    """[(props path, frame, png path)] for every still of a run"""
    jobs = []
    new = tag != "before"
    for name, (_, _, has_mascot) in CASES.items():
        if new and has_mascot:
            jobs.append((variant(name, noart=True), TEXT_FRAME, png(tag, name, TEXT_FRAME, "_noart")))
            for f in MASCOT_FRAMES:
                jobs.append((variant(name), f, png(tag, name, f)))
                jobs.append((variant(name, noart=True), f, png(tag, name, f, "_noart")))
        else:
            jobs.append((PROPS / f"{name}.json", TEXT_FRAME, png(tag, name, TEXT_FRAME)))
    for f in TRANSITION_FRAMES:
        jobs.append((variant("transition", noart=True, tint=True) if new else PROPS / "transition.json", f, png(tag, "transition", f, "_tint" if new else "")))
    for f in TRANSITION_FRAMES if not new else (TRANSITION_A, 82, 83, 84, 85, TRANSITION_B):      # untinted, with the mascot drawn: to look at
        jobs.append((PROPS / "transition.json", f, png(tag, "transition", f, "_look" if new else "")))
    for f in START_FRAMES:
        if new:
            jobs.append((variant("start", noart=True), f, png(tag, "start", f, "_noart")))
        jobs.append((PROPS / "start.json", f, png(tag, "start", f)))
    if not new:                                                       # the old mascot does not depend on the scenes: an empty reel on/off gives its pixels
        for f in sorted(set(START_FRAMES) | {27, 60, 80}):
            jobs.append((variant("start", empty=True, mascot=True), f, png(tag, "mascot", f, "_m1")))
            jobs.append((variant("start", empty=True, mascot=False), f, png(tag, "mascot", f, "_m0")))
    seen, out = set(), []
    for j in jobs:
        if str(j[2]) not in seen:
            seen.add(str(j[2]))
            out.append(j)
    return out


def render_one(job):
    props, frame, out = job
    out.parent.mkdir(parents=True, exist_ok=True)
    rel = Path(props).resolve().relative_to(STUDIO)
    for attempt in range(3):                                           # a still sometimes fails to start when several run at once
        r = subprocess.run([npx(), "remotion", "still", "src/index.ts", "Reel", str(out), f"--frame={frame}", f"--props={rel}", "--log=error"],
                           cwd=str(STUDIO), capture_output=True, text=True, timeout=600)
        if r.returncode == 0 and out.exists():
            return None
    return f"FAILED {out.name}: {(r.stdout + r.stderr)[-300:]}"


def render(tag, workers=4, force=False):
    jobs = [j for j in plan(tag) if force or not j[2].exists()]
    print(f"rendering {len(jobs)} stills with {workers} workers", flush=True)
    with cf.ThreadPoolExecutor(workers) as ex:
        bad = [m for m in ex.map(render_one, jobs) if m]
    for m in bad:
        print(m)
    return not bad


# ---------------------------------------------------------------- measuring

def load(path):
    return np.array(Image.open(path).convert("RGB")).astype(np.int16)


def near(im, rgb, tol):
    return np.abs(im - np.array(rgb, dtype=np.int16)).max(axis=2) <= tol


def runs(flags, gap):
    """[(first, last)] of the True stretches of a 1-D array, stretches closer than `gap` merged"""
    idx = np.where(flags)[0]
    if not len(idx):
        return []
    out, a, b = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - b > gap:
            out.append((int(a), int(b)))
            a = i
        b = i
    out.append((int(a), int(b)))
    return out


def find_bar(sig):
    """the progress bar: a yellow stripe whose run touches the bar's left end (x 90), at most 12 rows tall. -> (mask without the bar, [box])"""
    out = sig.copy()
    rows = []
    for y in range(H):
        row = sig[y]
        if not row[98]:
            continue
        a = 98
        while a > 0 and row[a - 1]:
            a -= 1
        if a > 93 or a < 80:
            continue
        b = 98
        while b < W - 1 and row[b + 1]:
            b += 1
        if b - a >= 8:
            rows.append((y, a, b))
    groups, cur = [], []
    for r in rows:
        if cur and r[0] - cur[-1][0] > 1:
            groups.append(cur)
            cur = []
        cur.append(r)
    if cur:
        groups.append(cur)
    bars = []
    for g in groups:
        if g[-1][0] - g[0][0] + 1 > 12:
            continue
        for y, a, b in g:
            out[y, a:b + 1] = False
        bars.append([min(r[1] for r in g), g[0][0], max(r[2] for r in g), g[-1][0]])
    return out, bars


def find_card(im):
    """outer box of the glass caption card from its 2 px ring: rows where more than half of the row's middle has the ring colour"""
    ring = ((im >= np.array(RING_LO)) & (im <= np.array(RING_HI))).all(axis=2)
    frac = ring[:, 200:880].mean(axis=1)
    frac[:600] = 0
    rs = runs(frac > 0.5, 2)
    if len(rs) < 2:
        return None
    top, bottom = rs[0][0], rs[-1][1]
    r0, r1 = (top + 70, bottom - 70) if bottom - top > 150 else ((top + bottom) // 2, (top + bottom) // 2 + 1)
    need = 0.6 * max(1, r1 - r0)
    left = np.where(ring[r0:r1, :200].sum(axis=0) >= need)[0]
    right = np.where(ring[r0:r1, 880:].sum(axis=0) >= need)[0]
    if not len(left) or not len(right):
        return None
    return [int(left.min()), int(top), int(right.max() + 880), int(bottom)]


def diff_box(a, b, thr=24):
    d = np.abs(a - b).max(axis=2) > thr
    ys, xs = np.where(d)
    if not len(ys):
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def inter(a, b):
    return a is not None and b is not None and a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def inside(box, x0=X0, x1=X1, y0=Y0, y1=Y1):
    return box[0] >= x0 and box[2] <= x1 - 1 and box[1] >= y0 and box[3] <= y1 - 1


def analyse_still(im, has_slogan, has_chips, ignore_above=0):
    """text blocks (slogan / chips / pulse / caption), card and bar of one still -> dict"""
    sig = near(im, SIGNAL, 28)
    paper, mute = near(im, PAPER, 14), near(im, MUTE, 16)
    for m in (sig, paper, mute):
        m[:ignore_above] = False
    sig2, bars = find_bar(sig)
    text = paper | sig2 | mute
    card = find_card(im)
    bands = []
    for a, b in runs(text.sum(axis=1) >= 2, 5):
        cols = np.where(text[a:b + 1].sum(axis=0) > 0)[0]
        n_sig = int(sig2[a:b + 1].sum())
        bands.append({"box": [int(cols.min()), a, int(cols.max()), b], "sig_share": n_sig / max(1, int(text[a:b + 1].sum()))})
    groups = {"slogan": [], "chips": [], "pulse": [], "caption": []}
    for bd in bands:
        cy = (bd["box"][1] + bd["box"][3]) / 2
        if card and card[1] <= cy <= card[3]:
            groups["caption"].append(bd)
        elif not has_slogan:
            groups["pulse"].append(bd)
        elif has_chips and bd["sig_share"] > 0.6:
            groups["chips"].append(bd)
        else:
            groups["slogan"].append(bd)
    blocks = []
    for kind, lines in groups.items():
        if not lines:
            continue
        tops = [ln["box"][1] for ln in lines]
        blocks.append({"kind": kind, "lines": len(lines), "pitch": round(float(np.mean(np.diff(tops))), 1) if len(lines) > 1 else None,
                       "box": [min(ln["box"][0] for ln in lines), min(tops), max(ln["box"][2] for ln in lines), max(ln["box"][3] for ln in lines)]})
    return {"blocks": blocks, "bars": bars, "card": card}


def case_violations(res, mascot_boxes, expect_card=True):
    """the faults of one still: SAFE_ZONE and TEXT_OVERLAP"""
    v = []
    boxes = {}
    for b in res["blocks"]:
        if b["kind"] != "caption":                                   # the caption text is judged through its card below, and by its own box here
            boxes[b["kind"]] = b["box"]
        if b["kind"] != "pulse" and not inside(b["box"]):
            v.append(f"SAFE_ZONE {b['kind']} text {b['box']} leaves x {X0}..{X1} y {Y0}..{Y1}")
        if b["kind"] == "pulse" and not inside(b["box"]):
            v.append(f"SAFE_ZONE pulse {b['box']} leaves x {X0}..{X1} y {Y0}..{Y1}")
    if res["card"]:
        boxes["card"] = res["card"]
        if not inside(res["card"]):
            v.append(f"SAFE_ZONE caption card edge {res['card']} leaves x {X0}..{X1} y {Y0}..{Y1} (the text inside it may still be inside)")
    elif expect_card:
        v.append("CARD_NOT_FOUND the caption card's ring was not found")
    for k, bar in enumerate(res["bars"]):
        boxes[f"bar{k}"] = list(bar)
        if bar[3] >= Y1:
            v.append(f"SAFE_ZONE progress bar y {bar[1]}..{bar[3]} is in the bottom {SAFE['bottom']} px, where the platform's own buttons and caption are")
    for k, mb in enumerate(mascot_boxes):
        if mb:
            boxes[f"mascot{k}"] = mb
    names = list(boxes)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            if a.startswith("mascot") and b.startswith("mascot"):
                continue
            if inter(boxes[a], boxes[b]):
                v.append(f"TEXT_OVERLAP {a} {boxes[a]} and {b} {boxes[b]}")
    return v


def lum(im):
    return 0.299 * im[..., 0] + 0.587 * im[..., 1] + 0.114 * im[..., 2]


def dilate(mask, n):
    out = mask.copy()
    for _ in range(n):
        m = out.copy()
        m[1:] |= out[:-1]
        m[:-1] |= out[1:]
        m[:, 1:] |= out[:, :-1]
        m[:, :-1] |= out[:, 1:]
        out = m
    return out


def old_mounted():
    """BEFORE: the frames in which the old Reel.tsx has both scenes of props/transition.json mounted, from its own formulas: a Sequence from
    floor((start - 0.1) * fps), as long as ceil((end + 0.28 - start + 0.1) * fps), the last 8 frames fading (opacity (len - f) / 8). Analytic, not measured."""
    import math
    fps, gap = 30, 0.28
    sc = json.loads((PROPS / "transition.json").read_text(encoding="utf-8"))["scenes"]
    wins = [(max(0, math.floor((s["start"] - 0.1) * fps)), math.ceil((s["end"] + gap - s["start"] + 0.1) * fps)) for s in sc]
    rows, ghosts = [], []
    for f in TRANSITION_FRAMES:
        la, lb = f - wins[0][0], f - wins[1][0]
        a_on, b_on = 0 <= la < wins[0][1], 0 <= lb < wins[1][1]
        a_op = max(0.0, min(1.0, (wins[0][1] - la) / 8)) if a_on else 0.0
        both = a_on and b_on and a_op > 0.05
        rows.append({"frame": f, "A_mounted": a_on, "A_opacity": round(a_op, 2), "B_mounted": b_on, "both": both})
        if both:
            ghosts.append(f)
    return {"kind": "analytic", "windows": wins, "rows": rows, "ghost_frames": ghosts}


def green_like(im):
    return (im[..., 1] - im[..., 0] > 40) & (im[..., 1] - im[..., 2] > 10) & (im[..., 1] > 80)


def ghost_series(tag):
    """AFTER: stills where scene B is drawn in green (prop qaTint). Frame by frame: text pixels of scene A (bright, not green) and everything of scene
    B (green: its text and its card). Both present at once = two scenes on the picture together."""
    rows, ghosts = [], []
    for f in TRANSITION_FRAMES:
        sub = load(png(tag, "transition", f, "_tint"))[Y0:Y1]
        g = green_like(sub)
        rows.append({"frame": f, "ink_A": int(((lum(sub) > 90) & ~g).sum()), "ink_B": int(g.sum())})
    # the mascot is not tinted and stands partly inside the safe zone: its yellow and white pixels are counted as "A" in every frame (about 1000 px of the
    # ~96000 of scene A's text), so "present" means at least 3 % of what the scene has when it is settled, not a fixed pixel count
    set_a = max([r["ink_A"] for r in rows if r["frame"] == TRANSITION_A] or [1])
    set_b = max([r["ink_B"] for r in rows if r["frame"] == TRANSITION_B] or [1])
    for r in rows:
        r["both"] = r["ink_A"] > 0.03 * set_a and r["ink_B"] > 0.03 * set_b
        if r["both"]:
            ghosts.append(r["frame"])
    return {"kind": "pixels", "rows": rows, "ghost_frames": ghosts, "settled": {"A": set_a, "B": set_b}}


def measure(tag):
    new = tag != "before"
    report = {"tag": tag, "safe": {"x": [X0, X1], "y": [Y0, Y1]}, "cases": {}, "start": {}, "transition": {}}
    for name, (has_slogan, has_chips, has_mascot) in CASES.items():
        entry = {"has_slogan": has_slogan, "has_chips": has_chips, "has_mascot": has_mascot}
        mboxes = []
        if new and has_mascot:
            res = analyse_still(load(png(tag, name, TEXT_FRAME, "_noart")), has_slogan, has_chips)
            for f in MASCOT_FRAMES:
                mboxes.append(diff_box(load(png(tag, name, f)), load(png(tag, name, f, "_noart"))))
            entry["mascot_boxes"] = {str(f): mb for f, mb in zip(MASCOT_FRAMES, mboxes)}
        else:
            res = analyse_still(load(png(tag, name, TEXT_FRAME)), has_slogan, has_chips, ignore_above=OLD_MASCOT_BOTTOM if (not new and has_mascot) else 0)
            if not new and has_mascot:
                for f in (27, 60, 80):
                    mboxes.append(diff_box(load(png(tag, "mascot", f, "_m1")), load(png(tag, "mascot", f, "_m0"))))
                entry["mascot_boxes"] = {str(f): mb for f, mb in zip((27, 60, 80), mboxes)}
        entry.update(res)
        entry["violations"] = case_violations(res, mboxes)
        report["cases"][name] = entry
    for f in START_FRAMES:                                            # scene one begins at frame 9; the mascot enters from below
        if new:
            im = load(png(tag, "start", f, "_noart"))
            mb = diff_box(load(png(tag, "start", f)), im)
        else:
            im = load(png(tag, "start", f))
            mb = diff_box(load(png(tag, "mascot", f, "_m1")), load(png(tag, "mascot", f, "_m0")))
        res = analyse_still(im, True, False, ignore_above=0 if new else OLD_MASCOT_BOTTOM)
        v = case_violations(res, [mb], expect_card=False)
        report["start"][str(f)] = {"blocks": res["blocks"], "card": res["card"], "bars": res["bars"], "mascot": mb, "violations": v}
    report["transition"] = ghost_series(tag) if new else old_mounted()
    return report


# ---------------------------------------------------------------- report

def fmt(box):
    return "-" if not box else f"x{box[0]}..{box[2]} y{box[1]}..{box[3]}"


def font_hint(b):
    """font size guessed from the line pitch (slogan: 1.04 line height + 0.13 padding; caption: 1.28), only a cross-check of the layout code"""
    if not b["pitch"]:
        return ""
    if b["kind"] == "slogan":
        return f"  (font ~{b['pitch'] / 1.17:.0f} px)"
    if b["kind"] == "caption":
        return f"  (font ~{b['pitch'] / 1.28:.0f} px)"
    return ""


def show(rep):
    print(f"\n=== layout check '{rep['tag']}'   safe zone: x {X0}..{X1}  y {Y0}..{Y1}")
    total = 0
    for name, c in rep["cases"].items():
        print(f"\n[{name}]  lines: " + ", ".join(f"{b['kind']} {b['lines']}" for b in c["blocks"]))
        for b in c["blocks"]:
            print(f"   {b['kind']:8s} {fmt(b['box'])}   h={b['box'][3] - b['box'][1] + 1}" + (f"  pitch {b['pitch']}" if b["pitch"] else "") + font_hint(b))
        print(f"   card     {fmt(c['card'])}")
        for bar in c["bars"]:
            print(f"   bar      {fmt(bar)}")
        for f, mb in (c.get("mascot_boxes") or {}).items():
            print(f"   mascot@{f:>2} {fmt(mb)}")
        for v in c["violations"]:
            print("   !!", v)
        total += len(c["violations"])
    print("\n[start frames]  (scene one begins at frame 9)")
    for f, s in rep["start"].items():
        print(f"   frame {f:>2}: mascot {fmt(s['mascot'])}   text: " + ("; ".join(f"{b['kind']} {fmt(b['box'])}" for b in s["blocks"]) or "none")
              + ("   bar " + fmt(s["bars"][0]) if s["bars"] else ""))
        for v in s["violations"]:
            print("      !!", v)
        total += len(s["violations"])
    t = rep["transition"]
    if t["kind"] == "pixels":
        print("\n[transition]  scene B drawn green; A = bright text pixels that are not green, B = green pixels (its text and card), inside the safe zone:")
        for r in t["rows"]:
            print(f"   frame {r['frame']:>3}: A {r['ink_A']:6d}   B {r['ink_B']:7d}" + ("   <-- TRANSITION_GHOST: two scenes at once" if r["both"] else ""))
    else:
        print(f"\n[transition]  analytic, from the old code's formulas: scene A mounted frames {t['windows'][0][0]}..{t['windows'][0][0] + t['windows'][0][1] - 1}, scene B from {t['windows'][1][0]}")
        for r in t["rows"]:
            print(f"   frame {r['frame']:>3}: A {'mounted, opacity ' + str(r['A_opacity']) if r['A_mounted'] else 'gone'}, B {'mounted' if r['B_mounted'] else 'not yet'}" + ("   <-- both scenes on the picture" if r["both"] else ""))
    if t["ghost_frames"]:
        total += 1
    print(f"\nTRANSITION_GHOST frames: {t['ghost_frames'] or 'none'}")
    print(f"violations: {total}")
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["all", "render", "measure"])
    ap.add_argument("--tag", default="after", help="folder name under out/ (before = the old composition, anything else = the new one)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="render stills that already exist again")
    a = ap.parse_args()
    if a.cmd in ("all", "render"):
        if not render(a.tag, a.workers, a.force):
            return 2
    if a.cmd in ("all", "measure"):
        rep = measure(a.tag)
        (HERE / "results").mkdir(exist_ok=True)
        (HERE / "results" / f"{a.tag}.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
        n = show(rep)
        return 1 if n else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
