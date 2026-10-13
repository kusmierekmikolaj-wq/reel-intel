"""watch.py - monitoring: scan what is new, flag videos that pop, write a digest.

  reel-intel watch SOURCE [--alert 5] [--limit N]
SOURCE: YouTube channel URL, links file, or a grid.tsv you refreshed with `reel-intel snippet`. Only new videos are fetched (the scan is cached).
A new video is flagged when its views are at least ALERT times the typical video of the surrounding posts (rolling median by date; overall median
when there are too few neighbours). The digest is appended to <data>/digest.md and printed; run it from a scheduler (docs/MONITORING.md).
"""
import datetime as dt
import json
import subprocess
import sys

from .common import DATA, WORK, jload
from .report import load, rolling_median, with_views


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    alert = 5.0
    if "--alert" in args:
        i = args.index("--alert")
        alert = float(args[i + 1])
        del args[i:i + 2]
    if not args:
        print(__doc__)
        return
    state_file = WORK / "watch_state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {"seen": []}
    before = {p.name for p in DATA.iterdir() if (p / "features.json").exists()} if DATA.exists() else set()
    first_run = not state["seen"]
    subprocess.call([sys.executable, "-m", "reel_intel.cli", "scan", *args])
    F = load()
    roll = rolling_median(F)
    V = with_views(F)
    med = sorted(f["views"] for f in V.values())[len(V) // 2] if V else None
    known = set(state["seen"]) | (before if not first_run else set())
    new = [rid for rid in F if rid not in known]
    lines = [f"## {dt.datetime.now():%Y-%m-%d %H:%M}: {len(new)} new video(s), {len(F)} in total", ""]
    if first_run:
        lines += ["First run: everything is recorded as the baseline, nothing is flagged.", ""]
    flagged = 0
    for rid in sorted(new, key=lambda r: -(F[r].get("views") or 0)):
        f = F[rid]
        base = roll.get(rid) or med
        x = f["views"] / base if f.get("views") and base else None
        hot = bool(x and x >= alert and not first_run)
        flagged += hot
        lines.append(f"- {'**POPPING** ' if hot else ''}{rid}: {int(f['views']) if f.get('views') else '?'} views" + (f" ({x:.1f}x typical)" if x else "") + f" | {f.get('url')}")
        if f.get("hook_spoken"):
            lines.append(f"  - first sentence: {f['hook_spoken'][:140]}")
    lines += ["", f"Flagged: {flagged} (threshold {alert:g}x typical).", ""]
    digest = "\n".join(lines)
    with open(WORK / "digest.md", "a", encoding="utf-8") as fh:
        fh.write(digest + "\n")
    state["seen"] = sorted(set(state["seen"]) | set(F))
    state_file.write_text(json.dumps(state), encoding="utf-8")
    print(digest)


if __name__ == "__main__":
    main()
