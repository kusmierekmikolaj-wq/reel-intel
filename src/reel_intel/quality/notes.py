"""notes.py - the checker learns from a person's ears.

  reel-intel make note rolka.mp4 13.2 oddech "w przerwie po scenie 1"     remember what a person heard (time, kind, free text)
  reel-intel make learn                                                    tune the thresholds so the notes that were MISSED would be caught

What happens (this is the "it should learn from the faults it missed" of the project, in four steps):
  1. add_note       the note is stored in ~/.reel-intel/qa_notes.json (or $REEL_QA_NOTES) with its time, kind and scene.
  2. match          a note is DETECTED when a finding (ERROR or WARN) of a code that belongs to its kind sits within `tol_s` seconds of it (or in the same scene).
                    Notes with no such finding are MISSED; recall = detected / all. Findings with no note are not counted as false alarms: a person who writes
                    three notes did not necessarily hear everything (precision is only reported when the caller says the reel was reviewed completely).
  3. features_at    for a missed note the numbers are measured at that moment from the reel itself (the ears score, the length of the soft tail, the speaking
                    rate, the last word's length, the pause ...): what the detector saw and why it was under its threshold. They are stored with the note.
  4. suggest        the threshold of the detector that SHOULD have fired is moved to just catch the case (10% past the measured value), never beyond a floor
                    that keeps the false alarms of clean reels away; when the floor would be crossed the note says "this detector cannot separate it: it needs a
                    new detector", not a number. apply() writes the change to qa_profile.json. regression() re-checks every stored case against a profile,
                    so tuning for one note cannot silently lose another.
What it cannot do: invent a detector for a fault it has no measurement for (a note of kind "inne"), or know that a person was wrong.
"""
import json
import os
import re
import time
import unicodedata
from pathlib import Path

from . import audio, profile as _profile
from .repair import scene_at

# kind -> the codes that count as having caught it
KINDS = {
    "oddech": ("VOICE_BREATH", "EAR_BREATH"),
    "szum": ("VOICE_HISS", "VOICE_NOISE", "GAP_RUMBLE", "GAP_CLICK"),
    "tempo": ("VOICE_FAST", "VOICE_BURST", "VOICE_SLOW"),
    "wymowa": ("VOICE_SUB", "VOICE_DEL", "VOICE_INS"),
    "ucięte": ("VOICE_CUT",),
    "przeciągnięte": ("VOICE_DRONE",),
    "pauza": ("VOICE_PAUSE_INNER", "GAP_LONG", "GAP_SHORT"),
    "muzyka": ("MIX_PUMPING", "MIX_VOICE_TO_MUSIC", "GAP_LOUD", "GAP_RUMBLE"),
    "głośność": ("LOUDNESS", "VOICE_LEVEL", "TRUE_PEAK", "MIX_CLIP"),
    "monoton": ("VOICE_MONOTONE",),
    "głos": ("VOICE_DRIFT", "VOICE_PITCH_JUMP", "VOICE_MONOTONE"),
    "napisy": ("CAPTION_SPEED", "CAPTION_SYNC", "TEXT_LEGIBILITY", "CONTRAST"),
    "obraz": ("SAFE_ZONE", "TEXT_OVERLAP", "TRANSITION_GHOST", "FIRST_FRAME", "PICTURE_BLACK", "PICTURE_FREEZE", "FLICKER", "LIPSYNC"),
    "inne": (),
}
ALIASES = {
    "oddech": "oddech", "oddechy": "oddech", "oddychanie": "oddech", "breath": "oddech", "wdech": "oddech", "westchnienie": "oddech",
    "szum": "szum", "syk": "szum", "trzask": "szum", "burczenie": "szum", "dudnienie": "szum", "noise": "szum", "hiss": "szum",
    "tempo": "tempo", "szybko": "tempo", "zaszybko": "tempo", "wolno": "tempo", "zawolno": "tempo", "pedzi": "tempo", "fast": "tempo", "slow": "tempo",
    "wymowa": "wymowa", "zlewymawia": "wymowa", "slowo": "wymowa", "pronunciation": "wymowa",
    "ucieta": "ucięte", "uciete": "ucięte", "ucina": "ucięte", "cut": "ucięte",
    "przeciagniete": "przeciągnięte", "przeciaga": "przeciągnięte", "samogloska": "przeciągnięte", "drone": "przeciągnięte",
    "pauza": "pauza", "przerwa": "pauza", "cisza": "pauza", "pause": "pauza",
    "muzyka": "muzyka", "muzyke": "muzyka", "tlo": "muzyka", "pompuje": "muzyka", "music": "muzyka",
    "glosnosc": "głośność", "cicho": "głośność", "glosno": "głośność", "loudness": "głośność",
    "monoton": "monoton", "monotonny": "monoton", "monotonia": "monoton", "plasko": "monoton",
    "glos": "głos", "voice": "głos", "robotyczny": "głos",
    "napisy": "napisy", "napis": "napisy", "caption": "napisy", "captions": "napisy",
    "obraz": "obraz", "ekran": "obraz", "uklad": "obraz", "picture": "obraz",
    "inne": "inne", "other": "inne",
}


def _fold(s):
    s = unicodedata.normalize("NFKD", s.lower().replace("ł", "l"))
    return re.sub(r"[^a-z]", "", "".join(c for c in s if not unicodedata.combining(c)))


def normalize_kind(text):
    """a kind of KINDS from what the person typed ('Oddechy', 'za szybko', 'przeciągnięte'); None when it is not recognised"""
    t = _fold(text or "")
    if t in ALIASES:
        return ALIASES[t]
    for k in KINDS:
        if _fold(k) == t:
            return k
    return None


def notes_path(path=None):
    return Path(path or os.environ.get("REEL_QA_NOTES") or Path.home() / ".reel-intel" / "qa_notes.json")


def load_notes(path=None, reel=None):
    p = notes_path(path)
    try:
        notes = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        notes = []
    if reel is not None:
        notes = [n for n in notes if Path(n.get("reel", "")).name == Path(reel).name]
    return notes


def save_notes(notes, path=None):
    p = notes_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8")


def add_note(reel, t, kind, text="", scene=None, path=None, features=None):
    """store a note; `kind` is normalised (a ValueError lists the known kinds). Returns the note."""
    k = normalize_kind(kind)
    if k is None:
        raise ValueError(f"unknown kind {kind!r}; use one of: {', '.join(KINDS)}")
    note = {"reel": str(reel), "t": round(float(t), 2), "kind": k, "text": text or "", "scene": scene, "ts": int(time.time()), "features": features or {}, "applied": False}
    notes = load_notes(path)
    notes.append(note)
    save_notes(notes, path)
    return note


# ---------------------------------------------------------------- matching
def match(notes, findings, tol_s=1.5, scenes=None, complete=False):
    """{detected: [(note, finding)], missed: [note], recall, precision}: see the module docstring for what counts. `complete` says that the person listened to
    the whole reel and wrote down everything, which is the only case where findings without a note are false alarms."""
    hits = [f for f in findings if f.sev in ("ERROR", "WARN")]
    detected, missed, used = [], [], set()
    for n in notes:
        codes = KINDS.get(n["kind"], ())
        sc = n.get("scene") or (scene_at(scenes, n["t"]) if scenes else None)
        got = None
        for i, f in enumerate(hits):
            if f.code not in codes:
                continue
            near = f.t is not None and abs(float(f.t) - n["t"]) <= tol_s
            same = sc is not None and f.scene == sc
            if near or same:
                got = (i, f)
                break
        if got:
            used.add(got[0])
            detected.append((n, got[1]))
        else:
            missed.append(n)
    out = {"detected": detected, "missed": missed, "recall": (len(detected) / len(notes)) if notes else None, "precision": None}
    if complete and hits:
        out["precision"] = len(used) / len(hits)
    return out


def recall_report(res):
    """the result of match() in a few lines of Polish"""
    n = len(res["detected"]) + len(res["missed"])
    if not n:
        return "Brak uwag człowieka do tej rolki."
    lines = [f"Skaner znalazł {len(res['detected'])} z {n} rzeczy, które usłyszał człowiek (czułość {res['recall'] * 100:.0f}%)."]
    for note, f in res["detected"]:
        lines.append(f"- znalazł: {note['kind']} przy {note['t']} s → {f.code}")
    for note in res["missed"]:
        lines.append(f"- PRZEGAPIŁ: {note['kind']} przy {note['t']} s" + (f" ({note['text']})" if note.get("text") else ""))
    if res.get("precision") is not None:
        lines.append(f"Trafność ostrzeżeń: {res['precision'] * 100:.0f}% (człowiek przesłuchał całość).")
    return "\n".join(lines)


# ---------------------------------------------------------------- what the detectors saw at the time of a note
def _voice(ctx):
    v = getattr(ctx, "voice", None)
    return v if v is not None else getattr(ctx, "final", None)


def features_at(ctx, note):
    """numbers measured at the note's time, by kind (see RULES for which are used): {ears, soft_tail_s, rate, burst_rate, last_word_s, pause_s, gap_s, noise_db,
    level_jump_db, f0_std}. A feature is missing when it cannot be measured (no voice track, no ears)."""
    t = float(note["t"])
    sc_no = note.get("scene") or scene_at(ctx.scenes, t)
    sc = ctx.scenes[sc_no - 1] if sc_no and sc_no <= len(ctx.scenes) else None
    out = {"scene": sc_no}
    x, sr = _voice(ctx), ctx.sr
    ears = getattr(ctx, "ears", None)
    if ears and ears.get("windows"):
        from ..ears import BREATH_LABELS, breath_score
        w = float(ears.get("window", 1.0))
        near = [breath_score(e["labels"], BREATH_LABELS)[0] for e in ears["windows"] if float(e["t"]) - 0.6 <= t <= float(e["t"]) + w + 0.6]
        if near:
            out["ears"] = round(max(near), 3)
    words = (sc or {}).get("words") or []
    if sc and words:
        a, b = float(words[0][1]), float(words[-1][2])
        out["last_word_s"] = round(float(words[-1][2]) - float(words[-1][1]), 3)
        gaps = [float(q[1]) - float(p[2]) for p, q in zip(words, words[1:])]
        if gaps:
            out["pause_s"] = round(max(gaps), 3)
        if x is not None:
            seg = x[int(float(sc["start"]) * sr):int(float(sc["end"]) * sr)]
            out["soft_tail_s"] = round(audio.soft_tail(seg, sr), 3)
            peaks = audio.nuclei(x, sr)
            text = audio.syllable_count(sc.get("text") or " ".join(str(q[0]) for q in words)) / max(b - a, 0.3)
            out["rate"] = round(max(audio.rate_in(peaks, a, b), text), 2)
            bursts = [audio.rate_in(peaks, a + k * 0.5, a + k * 0.5 + 1.0) for k in range(int(max(0.0, b - a - 1.0) / 0.5) + 1)]
            if bursts:
                out["burst_rate"] = round(max(bursts), 2)
            tail_a, tail_b = b + 0.1, float(sc["end"])
            if tail_b - b > 0.3:
                out["noise_db"] = round(audio.db(x[int(tail_a * sr):int(tail_b * sr)]), 1)
            lv = []
            for q in ctx.scenes:
                qw = q.get("words") or []
                if qw:
                    lv.append(audio.db(x[int(float(qw[0][1]) * sr):int(float(qw[-1][2]) * sr)]))
            if len(lv) > 1:
                out["level_jump_db"] = round(max(abs(p - q) for p, q in zip(lv, lv[1:])), 1)
        # the pause between this scene and the next
        nxt = ctx.scenes[sc_no] if sc_no < len(ctx.scenes) else None
        if nxt and nxt.get("words"):
            out["gap_s"] = round(float(nxt["words"][0][1]) - b, 3)
    return out


# ---------------------------------------------------------------- from a missed note to a threshold
# kind -> [(code, (section, key), feature, direction, floor)]: direction "gt": the detector fires when the feature is ABOVE the threshold (lower the threshold to catch
# more), "ge" likewise with >=, "lt": it fires BELOW the threshold (raise it). `floor` is how far the threshold may be moved: past it a clean reel starts to alarm.
RULES = {
    "oddech": [("EAR_BREATH", ("voice", "ears_min_score"), "ears", "ge", 0.03), ("VOICE_BREATH", ("voice", "soft_tail_s"), "soft_tail_s", "gt", 0.05)],
    "tempo": [("VOICE_FAST", ("pace", "fast"), "rate", "gt", 5.6), ("VOICE_BURST", ("pace", "burst"), "burst_rate", "gt", 7.5)],
    "przeciągnięte": [("VOICE_DRONE", ("voice", "last_word_max_s"), "last_word_s", "gt", 0.7)],
    "pauza": [("VOICE_PAUSE_INNER", ("voice", "inner_pause_s"), "pause_s", "gt", 0.35), ("GAP_LONG", ("gap", "long_s"), "gap_s", "gt", 0.6)],
    "szum": [("VOICE_HISS", ("voice", "hiss_db"), "noise_db", "gt", -60.0)],
    "głośność": [("VOICE_LEVEL", ("voice", "level_jump_db"), "level_jump_db", "gt", 2.5)],
    "monoton": [("VOICE_MONOTONE", ("intonation", "f0_std_semitones_min"), "f0_std", "lt", 4.5)],
}
MARGIN = 0.10


def threshold(profile, section, key, default=None):
    v = (profile.get(section) or {}).get(key)
    if v is None:
        for src in (audio.PROFILE_ADDITIONS, _profile.DEFAULT):
            v = (src.get(section) or {}).get(key)
            if v is not None:
                break
    return default if v is None else v


def fires(direction, value, thr):
    if thr is None:
        return False
    return {"gt": value > thr, "ge": value >= thr, "lt": value < thr}[direction]


def detects(note, profile=None):
    """True when, with this profile, the measured features of a stored note would make a detector of its kind fire; None when the note has no features"""
    profile = profile if profile is not None else _profile.load()
    feats = note.get("features") or {}
    rules = RULES.get(note["kind"], [])
    seen = False
    for _code, (sec, key), feat, direction, _floor in rules:
        if feat in feats and feats[feat] is not None:
            seen = True
            if fires(direction, float(feats[feat]), threshold(profile, sec, key)):
                return True
    return False if seen else None


def suggest(note, profile=None):
    """[{code, section, key, from, to, floor, why}] for a MISSED note that has features: the smallest change of one threshold that would have caught it. An
    empty list with a `cannot` entry when the floor stands in the way or when the kind has no measurable rule."""
    profile = profile if profile is not None else _profile.load()
    feats = note.get("features") or {}
    rules = RULES.get(note["kind"])
    if not rules:
        return [{"cannot": f"nothing is measured for notes of kind '{note['kind']}': it needs a detector, not a threshold"}]
    options, blocked = [], []
    for code, (sec, key), feat, direction, floor in rules:
        if feat not in feats or feats[feat] is None:
            continue
        v, thr = float(feats[feat]), threshold(profile, sec, key)
        if thr is None:
            thr = {"lt": -1e9, "gt": 1e9, "ge": 1e9}[direction]
        if fires(direction, v, thr):
            return []                                                        # a detector already fires on these numbers: the note was matched by time, not by number
        if direction in ("gt", "ge"):
            new = v - abs(v) * MARGIN
            ok = new >= floor
        else:
            new = v + abs(v) * MARGIN
            ok = new <= floor
        row = {"code": code, "section": sec, "key": key, "from": thr if abs(thr) < 1e8 else None, "to": round(new, 3), "floor": floor,
               "why": f"{feat} was {v} at {note['t']} s; {code} fires {'above' if direction in ('gt', 'ge') else 'below'} {thr if abs(thr) < 1e8 else 'its limit'}"}
        (options if ok else blocked).append(row)
    if options:
        options.sort(key=lambda r: abs(r["from"] - r["to"]) / (abs(r["from"]) + 1e-9) if r["from"] is not None else 9.0)       # the smallest relative change first
        return [options[0]]
    if blocked:
        return [{"cannot": "the threshold would have to pass " + "; ".join(f"{b['section']}.{b['key']} {b['to']} (floor {b['floor']})" for b in blocked)
                 + ": clean reels would start to alarm. This detector cannot separate the case; it needs a better detector"}]
    return [{"cannot": "the note has no measured features (the reel could not be analysed at that time)"}]


def apply(suggestions, profile_path=None, notes=None, notes_file=None):
    """write the suggested thresholds into qa_profile.json (next to profile.py by default) and mark the notes as applied. Returns the list of changes."""
    from .calibrate import write_profile
    path = Path(profile_path or _profile.profile_path())
    over, changes = {}, []
    for s in suggestions:
        if "cannot" in s:
            continue
        over.setdefault(s["section"], {})[s["key"]] = s["to"]
        changes.append(s)
    if over:
        write_profile(path, over)
    if notes is not None:
        for n in notes:
            n["applied"] = True
        if notes_file is not None:
            save_notes(notes, notes_file)
    return changes


def regression(notes, profile=None):
    """{kept: [...], lost: [...], unknown: [...]} of the stored notes that have features: would `profile` still detect each? A change of a threshold
    must never move a note from kept to lost."""
    res = {"kept": [], "lost": [], "unknown": []}
    for n in notes:
        d = detects(n, profile)
        res["unknown" if d is None else "kept" if d else "lost"].append(n)
    return res


def learn(notes, profile=None, profile_path=None):
    """for every missed, not yet applied note with features: the suggestion; the most permissive per threshold wins when several notes want the same one.
    Returns {suggestions, cannot, regression_before, regression_after} without writing anything (apply() writes)."""
    profile = profile if profile is not None else _profile.load()
    best, cannot = {}, []
    for n in notes:
        if n.get("applied") or detects(n, profile):
            continue
        for s in suggest(n, profile):
            if "cannot" in s:
                cannot.append({"note": n, "why": s["cannot"]})
                continue
            k = (s["section"], s["key"])
            cur = best.get(k)
            direction = next((r[3] for r in RULES[n["kind"]] if r[1] == k), "gt")
            if cur is None or (direction in ("gt", "ge") and s["to"] < cur["to"]) or (direction == "lt" and s["to"] > cur["to"]):
                best[k] = s
    sug = list(best.values())
    after = {**profile, **{sec: dict(profile.get(sec, {})) for sec in {s["section"] for s in sug}}}
    for s in sug:
        after[s["section"]][s["key"]] = s["to"]
    return {"suggestions": sug, "cannot": cannot, "regression_before": regression(notes, profile), "regression_after": regression(notes, after)}
