"""docs.py - the tables of docs/QA.md are made from the code, so the document cannot say something the checker does not do.

  python -m reel_intel.quality.docs            rewrites the generated parts of docs/QA.md (between the <!-- BEGIN name --> and <!-- END name --> lines)
  python -m reel_intel.quality.docs --check    exits 1 when the file is out of date (a test runs this)
"""
import re
import sys
from pathlib import Path

from . import audio, engine, mix, picture, platform, profile, speech, sync, verdict  # noqa: F401  (importing registers every code)
from .base import REGISTRY

MODULE_ORDER = ("speech", "audio", "mix", "picture", "platform", "sync", "engine")
REPAIR_TEXT = {None: "a person", "retake_scene": "record the sentence again", "respell": "another spelling for the voice, then record again", "slow_scene": "slow the sentence down",
               "retrim_scene": "cut the end of the sentence harder", "level_scenes": "bring every scene to the same level", "remix": "mix the music again (lower, filtered)",
               "master": "set the loudness (limiter on the peaks)", "relayout": "compute the layout again", "rerender": "render again"}


def register_table():
    """the register of fault codes as a Markdown table: module, code, severity, what it means, what repairs it, the Polish name"""
    rows = ["| module | code | severity | what it finds | repair | po polsku |", "|---|---|---|---|---|---|"]
    for mod in MODULE_ORDER:
        for code, v in sorted(REGISTRY.items()):
            if v["module"] != mod:
                continue
            rows.append(f"| {mod} | `{code}` | {v['sev']} | {v['desc'].replace('|', '/')} | {REPAIR_TEXT.get(v['repair'], v['repair'])} | {verdict.label(code)} |")
    return "\n".join(rows)


def thresholds_table():
    """every threshold the detectors read: the default of profile.py or of the module that adds it, and the value the calibrated qa_profile.json sets"""
    default = profile.DEFAULT
    cal = profile.load()
    extra = {}
    for m in (audio, mix, picture, platform, sync):
        for src in (getattr(m, "PROFILE_ADDITIONS", None), getattr(m, "PICTURE_DEFAULTS", None), getattr(m, "EXTRA", None)):
            for sec, kv in (src or {}).items():
                for k, v in kv.items():
                    extra.setdefault((sec, k), v)
    rows = ["| threshold | default | in use |", "|---|---|---|"]
    keys = {}
    for sec, kv in default.items():
        if isinstance(kv, dict):
            for k, v in kv.items():
                keys[(sec, k)] = v
    for kk, v in extra.items():
        keys.setdefault(kk, v)
    for (sec, k), v in sorted(keys.items()):
        if isinstance(v, dict):
            continue
        now = (cal.get(sec) or {}).get(k, v)
        rows.append(f"| `{sec}.{k}` | {v} | {now if now != v else 'same'} |")
    return "\n".join(rows)


GENERATED = {"register": register_table, "thresholds": thresholds_table}


def render(text):
    """the document with every generated block replaced by a fresh one; a block that is missing is an error"""
    for name, fn in GENERATED.items():
        pat = re.compile(rf"(<!-- BEGIN {name} -->\n)(.*?)(\n<!-- END {name} -->)", re.S)
        if not pat.search(text):
            raise ValueError(f"docs/QA.md has no <!-- BEGIN {name} --> ... <!-- END {name} --> block")
        text = pat.sub(lambda m: m.group(1) + fn() + m.group(3), text)
    return text


def path():
    return Path(__file__).resolve().parents[3] / "docs" / "QA.md"


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    p = path()
    old = p.read_text(encoding="utf-8")
    new = render(old)
    if "--check" in a:
        if new != old:
            print(f"{p} is out of date: run python -m reel_intel.quality.docs")
            return 1
        return 0
    p.write_text(new, encoding="utf-8")
    print(f"written {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
