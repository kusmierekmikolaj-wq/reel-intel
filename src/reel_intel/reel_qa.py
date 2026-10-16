"""reel_qa.py - finds faults in a rendered reel before anybody posts it. The work is done in the package quality/ (a register of known faults, detectors that are
pure functions of numbers, a repair loop, a verdict); this file is the command line and a few helpers.

  reel-intel make qa rolka.mp4            checks the file against the timeline saved next to it (rolka.qa.json), prints the findings and the verdict,
                                          writes rolka_kontrola.md. Exit code 0 gotowe, 10 z uwagami, 1 niegotowe, 2 the check could not run.
        --quick                           leave out the slow checks (the sound-event model, the OCR, the recogniser)
  reel-intel make qa --selftest           puts known faults into synthetic reels: every one must be found, a clean reel must pass (about a minute)
  reel-intel make calibrate DIR... [--reels DIR...]
                                          measures real recordings and writes thresholds to quality/qa_profile.json
  reel-intel make note rolka.mp4 13.2 oddech "after scene 1"
                                          write down what you heard at that second (kinds: oddech, szum, tempo, wymowa, ucięte, przeciągnięte, pauza, muzyka,
                                          głośność, monoton, głos, napisy, obraz, inne); the checker says whether it had found it
  reel-intel make learn [--dry]           tune the thresholds so the notes it missed would be caught (never past a floor; it says when it cannot)

What is judged: the voice (wrong, skipped, repeated words; dragged or chopped last word; pauses; breaths; pace; pitch), the sound (loudness, peaks, clipping,
pumping, music against voice, dropouts, noise, clicks), the picture (dead first frame, black and frozen stretches, flicker, text outside the safe area or over
other text, small or faint text, captions too fast), the file (size, codec, length, streaming layout) and the timing (captions, mouth, container). The list of codes
with what each one means and how sure it is: docs/QA.md. What is NOT judged: taste.
"""
import json
import sys
from pathlib import Path

SR = 24000


# ---------------------------------------------------------------- helpers kept for callers of the old API
def hear(ears_json, scenes):
    """[(severity, code, message)] for what the sound-event model heard (windows from ears.py): breaths and body sounds, with the time and the scene"""
    from .quality import audio, profile
    from .quality.base import Ctx
    ctx = Ctx(side={"scenes": scenes}, ears=ears_json, profile=profile.load())
    return [f.as_tuple() for f in audio.check_ears(ctx) if f.sev != "INFO"]


def run(mp4, **opts):
    """all findings of a reel (a list of quality.base.Finding)"""
    from .quality import engine
    return engine.check_reel(mp4, **opts)["findings"]


def selftest(groups=None, log=print):
    """the fault-injection matrix (quality/matrix.py): (caught, total) counting every injected fault found and every clean reel silent"""
    from .quality import matrix
    res = matrix.run_matrix(groups, log=log)
    log(matrix.format_matrix(res))
    return res["caught"] + res["clean_ok"], res["faults"] + res["clean"]


# ---------------------------------------------------------------- commands
def cmd_qa(args):
    from .quality import engine, notes, verdict
    quick = "--quick" in args
    files = [a for a in args if not a.startswith("--")]
    if "--selftest" in args:
        caught, total = selftest()
        return 0 if caught == total else 1
    if not files:
        print(__doc__)
        return 2
    mp4 = Path(files[0])
    r = engine.check_reel(mp4, ears=not quick, ocr=not quick, do_rehear=not quick)
    findings, side = r["findings"], r["side"]
    if side is None:
        print(verdict.format_console(findings))
        return verdict.exit_code("error")
    rep = None
    nts = notes.load_notes(reel=mp4)
    if nts:
        rep = notes.recall_report(notes.match(nts, findings, scenes=side.get("scenes", [])))
    print(verdict.format_console(findings))
    if rep:
        print("\n" + rep)
    mp4.with_name(mp4.stem + "_kontrola.md").write_text(verdict.report_pl(findings, title=f"Kontrola rolki {mp4.stem}", notes_report=rep), encoding="utf-8")
    sidecar = engine.sidecar_path(mp4)
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            summary = verdict.summarize(findings, checked=list(engine.ORDER), notes_report=rep)
            data["qa"] = {k: v for k, v in summary.items() if k != "findings"}
            data["qa"]["findings"] = summary["findings"][:60]
            for k in ("video", "rehear"):
                if side.get(k):
                    data[k] = side[k]
            sidecar.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as e:                                                 # the report is the result; a sidecar that cannot be updated is not a reason to fail
            print(f"sidecar not updated: {e}")
    return verdict.exit_code(verdict.decide(findings))


def cmd_note(args):
    from .quality import engine, notes
    if len(args) < 3:
        print("usage: reel-intel make note FILE.mp4 SECONDS KIND [text ...]")
        return 2
    mp4, t, kind = Path(args[0]), float(args[1].replace(",", ".")), args[2]
    try:
        note = notes.add_note(mp4, t, kind, " ".join(args[3:]))
    except ValueError as e:
        print(e)
        return 2
    print(f"saved: {note['kind']} at {note['t']} s")
    r = engine.check_reel(mp4, frames=False, do_rehear=False) if mp4.is_file() else None
    if not r or r["ctx"] is None:
        return 0
    found = notes.match([note], r["findings"], scenes=r["side"].get("scenes", []))
    print(notes.recall_report(found))
    if found["missed"]:
        feats = notes.features_at(r["ctx"], note)
        all_notes = notes.load_notes()
        for n in all_notes:
            if n["ts"] == note["ts"] and n["reel"] == note["reel"] and n["t"] == note["t"]:
                n["features"] = feats
        notes.save_notes(all_notes)
        note["features"] = feats
        print("measured at that moment: " + ", ".join(f"{k}={v}" for k, v in feats.items()))
        for s in notes.suggest(note):
            print(("it would be caught if " + f"{s['section']}.{s['key']} went from {s['from']} to {s['to']}") if "cannot" not in s else s["cannot"])
        print("`reel-intel make learn` applies it.")
    return 0


def cmd_learn(args):
    from .quality import notes
    allnotes = notes.load_notes()
    if not allnotes:
        print("no notes yet: reel-intel make note FILE.mp4 SECONDS KIND")
        return 0
    res = notes.learn(allnotes)
    for s in res["suggestions"]:
        print(f"{s['section']}.{s['key']}: {s['from']} -> {s['to']}   ({s['why']})")
    for c in res["cannot"]:
        print(f"cannot: {c['note']['kind']} at {c['note']['t']} s of {Path(c['note']['reel']).name}: {c['why']}")
    lost = [n for n in res["regression_after"]["lost"] if n in res["regression_before"]["kept"]]
    print(f"stored cases the checker would catch: {len(res['regression_before']['kept'])} now, {len(res['regression_after']['kept'])} after")
    if lost:
        print("NOT applied: the change would lose notes that are caught now")
        return 1
    if "--dry" in args or not res["suggestions"]:
        return 0
    todo = [n for n in allnotes if not n.get("applied") and n.get("features") and not notes.detects(n)]
    notes.apply(res["suggestions"], notes=todo, notes_file=None)
    notes.save_notes(allnotes)
    print("written to quality/qa_profile.json")
    return 0


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if a and a[0] in ("qa", "calibrate", "note", "learn"):
        sub = a.pop(0)
    elif a and a[0] == "--selftest":
        sub = "qa"
    else:
        sub = "qa"
    if sub == "calibrate":
        from .quality import calibrate
        return calibrate.main(a)
    if sub == "note":
        return cmd_note(a)
    if sub == "learn":
        return cmd_learn(a)
    return cmd_qa(a)


if __name__ == "__main__":
    sys.exit(main())
