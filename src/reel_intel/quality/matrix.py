"""matrix.py - the fault-injection matrix: every fault the checker claims to find is put into a clean synthetic reel and the checker must report exactly
that code; every clean reel must pass without an ERROR or a WARN.

  reel-intel make qa --selftest          run the matrix, print one line per case and the score

A case is  (module, name, expected code or None for a clean reel, function that returns the findings).  A fault case is CAUGHT when its code is among the
ERRORs and WARNs (for a code that is only ever INFO, among all findings); a clean case is SILENT when it reports no ERROR and no WARN, and a clean case that
is not silent is a FALSE ALARM. A fault case that also reports other codes is listed with them ("also"): a clipped mix is also loud and over the peak limit, a
gap squeezed to 50 ms also makes the scene end inside the next sentence; these are consequences, not mistakes, and are counted apart. The result is
recorded in docs/QA.md; the project's target is 95% caught over at least 40 cases and no false alarm on a clean reel.

What the matrix proves and what it does not: it proves that each detector finds the fault it was written for, in sound and pictures made to contain it. It
does not prove that real reels have no other faults, and a synthetic voice is not a human's ear (see docs/QA.md, "Limits").
"""
import time

import numpy as np

from . import audio, engine, mix, picture, platform, profile as _profile, speech, sync
from .base import REGISTRY, Ctx

SR = 24000


def loud(findings):
    return [f for f in findings if f.sev in ("ERROR", "WARN")]


def codes(findings):
    return {f.code for f in loud(findings)}


def _run(module, ctx, skip=()):
    out = []
    for d in module.DETECTORS:
        if d.__name__ in skip:
            continue
        out += d(ctx)
    return out


# ---------------------------------------------------------------- audio: a drawn voice-over with a fault in it
def _tone(sec, db=-18.0, f=180.0):
    t = np.arange(int(sec * SR)) / SR
    return (np.sin(2 * np.pi * f * t) * 10 ** (db / 20) * 1.41).astype("float32")


def _speechlike(sec, db=-18.0, syl_s=0.19, seed=0):
    rng = np.random.default_rng(seed)
    x = np.zeros(int(sec * SR), dtype="float32")
    for i in range(int(sec / syl_s)):
        n = int(syl_s * 0.75 * SR)
        a = int(i * syl_s * SR)
        f = 150 + 40 * rng.random()
        k = np.arange(n) / SR
        x[a:a + n] += ((np.sin(2 * np.pi * f * k) + 0.6 * np.sin(2 * np.pi * 2.3 * f * k) + 0.3 * np.sin(2 * np.pi * 5.1 * f * k)) * np.hanning(n)).astype("float32")
    return x / (np.sqrt(np.mean(x ** 2)) + 1e-9) * 10 ** (db / 20)


def audio_reel(n=3, gap=0.6, scene_s=1.6, db=-18.0, gap_sound=None, level_step=0.0, words_each=5):
    """(voice == final, sidecar): `n` tone 'sentences' with word times, separated by gaps"""
    parts, scenes, pos = [np.zeros(int(0.3 * SR), dtype="float32")], [], 0.3
    for i in range(n):
        parts.append(_tone(scene_s, db + level_step * i))
        step = scene_s / words_each
        ws = [[f"w{k}", round(pos + k * step, 3), round(pos + (k + 1) * step - 0.02, 3)] for k in range(words_each)]
        scenes.append({"text": "to jest jedno zdanie testowe", "words": ws, "start": pos, "end": pos + scene_s + min(0.15, gap * 0.5), "heard": 1.0, "tail": 0.1})
        pos += scene_s
        if i < n - 1:
            parts.append(np.zeros(int(gap * SR), dtype="float32") if gap_sound is None else gap_sound(gap))
            pos += gap
    return np.concatenate(parts), {"scenes": scenes}


def _paced(syl_s, words=7, sec=2.4, n=2):
    parts, scenes, pos = [np.zeros(int(0.3 * SR), dtype="float32")], [], 0.3
    for i in range(n):
        parts.append(_speechlike(sec, -18, syl_s, seed=i))
        step = sec / words
        scenes.append({"text": "to jest zdanie " + " ".join(["ma"] * (words - 3)), "words": [[f"w{k}", pos + k * step, pos + (k + 1) * step - 0.02] for k in range(words)],
                       "start": pos, "end": pos + sec + 0.1})
        pos += sec
        parts.append(np.zeros(int(0.3 * SR), dtype="float32"))
        pos += 0.3
    return np.concatenate(parts), {"scenes": scenes}


def _actx(x, side, ears=None, voice=None, profile=None):
    return Ctx(side=side, final=x, sr=SR, voice=x if voice is None else voice, profile=profile if profile is not None else _profile.load(), ears=ears)


def audio_cases():
    """[(name, expected code, findings function)] for audio.py"""
    def case(build, expect, **kw):
        def fn():
            x, side, extra = build()
            return _run(audio, _actx(x, side, **{**extra, **kw}))
        return fn

    def clean():
        x, s = audio_reel()
        return x, s, {}

    def gap_long():
        x, s = audio_reel(gap=1.5)
        return x, s, {}

    def gap_short():
        x, s = audio_reel(gap=0.05)
        return x, s, {}

    def gap_loud():
        x, s = audio_reel(gap=0.5, gap_sound=lambda g: _tone(g, -20, 900))
        return x, s, {}

    def gap_rumble():
        x, s = audio_reel(gap=0.5, gap_sound=lambda g: _tone(g, -22, 50))
        return x, s, {}

    def gap_click():
        def clicky(g):
            z = np.zeros(int(g * SR), dtype="float32")
            z[len(z) // 2:len(z) // 2 + 3] = [0.7, -0.7, 0.7]
            return z
        x, s = audio_reel(gap=0.5, gap_sound=clicky)
        return x, s, {}

    def cut():
        x, s = audio_reel()
        for sc in s["scenes"]:
            sc["end"] -= 0.4
        return x, s, {}

    def drone():
        x, s = audio_reel()
        w = s["scenes"][0]["words"][-1]
        w[2] = w[1] + 2.6
        return x, s, {}

    def breath_tail():
        x, s = audio_reel(gap=0.6)
        k = int(s["scenes"][1]["words"][-1][2] * SR)
        x = x.copy()
        x[k:k + int(0.4 * SR)] += _tone(0.4, -48, 220)[:int(0.4 * SR)]
        s["scenes"][1]["end"] = s["scenes"][1]["words"][-1][2] + 0.5
        return x, s, {}

    def breath_after():
        x, s = audio_reel(gap=0.6)
        rng = np.random.default_rng(3)
        we = s["scenes"][0]["words"][-1][2]
        k = int((we + 0.1) * SR)
        x = x.copy()
        x[k:k + int(0.25 * SR)] += (rng.standard_normal(int(0.25 * SR)) * 10 ** (-34 / 20)).astype("float32")
        s["scenes"][0]["end"] = we + 0.45
        return x, s, {}

    def level_jump():
        x, s = audio_reel(level_step=-9.0)
        return x, s, {}

    def hiss():
        x, s = audio_reel(gap=0.8)
        for sc in s["scenes"]:
            sc["end"] = sc["words"][-1][2] + 0.6
        noise = (np.random.default_rng(1).standard_normal(len(x)) * 10 ** (-38 / 20)).astype("float32")
        return x, s, {"voice": x + noise}

    def fast():
        x, s = _paced(0.1)
        return x, s, {}

    def ears_breath():
        x, s = audio_reel(gap=0.6)
        e = {"window": 1.0, "hop": 0.5, "windows": [{"t": 0.5, "labels": [["Speech", 0.9], ["Gasp", 0.2]]}]}
        return x, s, {"ears": e}

    def clean_pace():
        x, s = _paced(0.19)
        return x, s, {}
    return [("clean voice-over", None, case(clean, None)), ("clean speaking rate", None, case(clean_pace, None)),
            ("gap too long", "GAP_LONG", case(gap_long, "GAP_LONG")), ("gap too short", "GAP_SHORT", case(gap_short, "GAP_SHORT")),
            ("music as loud as the voice in a gap", "GAP_LOUD", case(gap_loud, "GAP_LOUD")), ("rumble in a gap", "GAP_RUMBLE", case(gap_rumble, "GAP_RUMBLE")),
            ("click in a gap", "GAP_CLICK", case(gap_click, "GAP_CLICK")), ("last word chopped", "VOICE_CUT", case(cut, "VOICE_CUT")),
            ("last vowel dragged on", "VOICE_DRONE", case(drone, "VOICE_DRONE")), ("breathy tail after the last word", "VOICE_BREATH", case(breath_tail, "VOICE_BREATH")),
            ("breath after a sentence", "VOICE_BREATH", case(breath_after, "VOICE_BREATH")), ("voice level jumps between scenes", "VOICE_LEVEL", case(level_jump, "VOICE_LEVEL")),
            ("hiss under the voice", "VOICE_HISS", case(hiss, "VOICE_HISS")), ("a sentence spoken too fast", "VOICE_FAST", case(fast, "VOICE_FAST")),
            ("the sound model hears a breath", "EAR_BREATH", case(ears_breath, "EAR_BREATH"))]


# ---------------------------------------------------------------- the other modules
def speech_cases(seed=1):
    side, voice = speech.synth_reel(seed=seed, sr=SR)
    prof = _profile.load()
    out = [("clean generated speech", None, lambda: speech.run_detectors(side, voice, SR, prof))]
    for kind, code in speech.INJECTIONS.items():
        def fn(kind=kind):
            s2, v2 = speech.inject(kind, side, voice, SR)
            return speech.run_detectors(s2, v2, SR, prof)
        out.append((f"speech: {kind}", code, fn))
    return out


def mix_cases(seed=3):
    f, v, m, w = mix.synth_mix(voice_db=-17.0, seed=seed)
    prof = _profile.load()
    scenes = [{"words": ws, "start": ws[0][1], "end": ws[-1][2] + 0.3} for ws in w]

    def run(f2, v2, m2, side_words=scenes):
        return _run(mix, Ctx(side={"scenes": side_words}, final=f2, sr=SR, voice=v2, music=m2, profile=prof))
    out = [("clean mix", None, lambda: run(f, v, m))]
    for kind, code in mix.INJECTIONS.items():
        if kind == "pumping":                                                  # not an injection into a given mix: synth_mix(pump=True) makes that reel, below
            continue

        def fn(kind=kind):
            f2, v2, m2 = mix.inject(kind, f, v, m, SR, t=w[1][1][1] if kind == "dropout" else None)
            return run(f2, v2, m2)
        out.append((f"mix: {kind}", code, fn))

    def pumping():
        fp, vp, mp, wp = mix.synth_mix(pump=True, music_db=-30.0, voice_db=-17.0, seed=seed)
        return run(fp, vp, mp, [{"words": ws, "start": ws[0][1], "end": ws[-1][2] + 0.3} for ws in wp])
    out.append(("mix: music pumps with the voice", "MIX_PUMPING", pumping))
    return out


def platform_cases():
    prof = _profile.load()
    out = [("clean file facts", None, lambda: _run(platform, Ctx(side={"video": platform.inject("clean"), "scenes": []}, profile=prof)))]
    expect = {k: c for k, (_kw, c) in platform.FAULTS.items() if c}
    expect.update({"too_long": "PLATFORM_DURATION", "unreadable": "FILE_BAD"})
    for kind, code in expect.items():
        out.append((f"platform: {kind}", code, lambda kind=kind: _run(platform, Ctx(side={"video": platform.inject(kind), "scenes": []}, profile=prof))))
    return out


def sync_cases(seed=0):
    case = sync.synthetic_case(seed)
    expect = {"caption_late": "CAPTION_SYNC", "caption_early": "CAPTION_SYNC", "caption_drift": "CAPTION_SYNC", "caption_scene": "CAPTION_SYNC",
              "audio_early": "LIPSYNC", "audio_late": "LIPSYNC", "mix_delay": "LIPSYNC", "mouth_flat": "LIPSYNC", "mouth_noise": "LIPSYNC",
              "av_audio_early": "AV_OFFSET", "av_audio_late": "AV_OFFSET"}
    out = [("clean timing", None, lambda: _run(sync, sync.as_ctx(case)))]
    for kind, code in expect.items():
        out.append((f"sync: {kind}", code, lambda kind=kind: _run(sync, sync.as_ctx(sync.inject(kind, case)))))
    return out


def picture_cases(scale=0.5):
    out = [("clean picture", None, lambda: picture.run_all(picture.inject("clean", scale=scale).ctx))]
    for kind, code in picture.INJECT_KINDS.items():
        out.append((f"picture: {kind}", code, lambda kind=kind: picture.run_all(picture.inject(kind, scale=scale).ctx)))
    return out


GROUPS = {"audio": audio_cases, "speech": speech_cases, "mix": mix_cases, "platform": platform_cases, "sync": sync_cases, "picture": picture_cases}


def run_matrix(groups=None, log=None):
    """{cases: [{group, name, expect, ok, got, seconds}], caught, faults, false_alarms, clean, score}"""
    rows = []
    for g in (groups or GROUPS):
        if g == "picture":
            try:
                import PIL  # noqa: F401
            except ImportError:
                if log:
                    log("picture cases skipped: Pillow is not installed")
                continue
        for name, expect, fn in GROUPS[g]():
            t0 = time.time()
            try:
                found = fn()
            except Exception as e:
                rows.append({"group": g, "name": name, "expect": expect, "ok": False, "got": [f"CRASH {type(e).__name__}: {e}"], "seconds": round(time.time() - t0, 1)})
                continue
            got = sorted(codes(found))
            seen = {f.code for f in found}
            if expect is None:
                ok, extra = not got, []
            else:
                info_only = REGISTRY.get(expect, {}).get("sev") == "INFO"
                ok = expect in got or (info_only and expect in seen)
                extra = [c for c in got if c != expect]
            rows.append({"group": g, "name": name, "expect": expect, "ok": ok, "got": got, "extra": extra, "seconds": round(time.time() - t0, 1)})
            if log:
                log(f"{'ok    ' if ok else 'FAILED'} {name}" + (f" -> {expect}" if expect else " (clean)") + (f"   also {extra}" if extra else "") + ("" if ok or not expect else f"   got {got}")
                    + ("" if ok or expect else f"   FALSE ALARM {got}"))
    faults = [r for r in rows if r["expect"]]
    clean = [r for r in rows if not r["expect"]]
    caught = sum(1 for r in faults if r["ok"])
    return {"cases": rows, "faults": len(faults), "caught": caught, "exact": sum(1 for r in faults if r["ok"] and not r["extra"]), "also": sum(1 for r in faults if r["extra"]),
            "clean": len(clean), "clean_ok": sum(1 for r in clean if r["ok"]), "false_alarms": sum(1 for r in clean if not r["ok"]),
            "score": (caught / len(faults)) if faults else None}


def format_matrix(res):
    lines = [f"{res['caught']}/{res['faults']} injected faults reported ({res['score'] * 100:.0f}%); {res['exact']} with nothing else reported, {res['also']} with other codes as well; "
             f"{res['clean_ok']}/{res['clean']} clean reels silent; false alarms on clean reels: {res['false_alarms']}"]
    for r in res["cases"]:
        if not r["ok"]:
            lines.append(f"  MISSED: {r['group']}: {r['name']}  expected {r['expect']}, got {r['got']}" if r["expect"] else f"  FALSE ALARM: {r['group']}: {r['name']}  {r['got']}")
        elif r["extra"]:
            lines.append(f"  also reported: {r['name']}: {r['extra']}")
    return "\n".join(lines)


def selftest(log=print):
    """run the matrix; (caught, total) for the exit code of `make qa --selftest`: total counts the faults and the clean reels, caught the faults found and the clean ones silent"""
    res = run_matrix(log=log)
    log(format_matrix(res))
    return res["caught"] + res["clean_ok"], res["faults"] + res["clean"], res


# ---------------------------------------------------------------- the same faults in REAL speech
# A synthetic reel proves that a detector finds the fault it was written for. These cases put faults into clean excerpts of real human speech (the data folders of
# reel-intel: audio.wav + transcript.json, sentences with word times; every sentence is a scene). A real excerpt has findings of its own (a fast talker, applause,
# a clipped recording), so a fault counts as FOUND only when the number of findings with its code goes UP against the same excerpt without the fault.
def real_excerpts(root, per_talk=2, seconds=25.0, first_s=60.0, step_s=240.0):
    """[(name, x float32 at SR, side)]: clean excerpts of real speech cut at sentence boundaries, from every folder under `root` that has audio.wav and transcript.json"""
    import json
    from pathlib import Path
    from .calibrate import decode
    out = []
    for d in sorted(p for p in Path(root).iterdir() if p.is_dir()):
        if not (d / "audio.wav").is_file() or not (d / "transcript.json").is_file():
            continue
        x, sr = decode(d / "audio.wav", SR)
        if x is None:
            continue
        segs = [s for s in json.loads((d / "transcript.json").read_text(encoding="utf-8")).get("segments", []) if s.get("words") and s["end"] - s["start"] > 1.2]
        n, t = 0, first_s
        while n < per_talk:
            grp = [s for s in segs if s["start"] >= t]
            if not grp:
                break
            sel, a = [], grp[0]["start"]
            for s in grp:
                if s["end"] - a > seconds:
                    break
                sel.append(s)
            if len(sel) >= 4:
                off = max(0.0, a - 0.3)
                xs = x[int(off * SR):int((sel[-1]["end"] + 0.3) * SR)]
                scenes = []
                for s in sel:
                    ws = [[w["w"].strip(), round(w["s"] - off, 3), round(w["e"] - off, 3)] for w in s["words"]]
                    scenes.append({"text": " ".join(w[0] for w in ws), "start": round(s["start"] - off, 3), "end": round(s["end"] - off, 3), "words": ws,
                                   "heard_words": [w[0] for w in ws], "heard": 1.0, "missed": [], "tail": 0.0})
                out.append((f"{d.name[-6:]}@{int(a)}s", xs.astype("float32"), {"scenes": scenes, "total": len(xs) / SR}))
                n += 1
            t = (sel[-1]["end"] if sel else t) + step_s
    return out


def _shift_times(side, t, dt):
    for s in side["scenes"]:
        if s["start"] >= t:
            s["start"] += dt
        if s["end"] >= t:
            s["end"] += dt
        for w in s["words"]:
            if w[1] >= t:
                w[1] += dt
            if w[2] >= t:
                w[2] += dt
    side["total"] = side.get("total", 0.0) + dt


def _splice(x, side, t, seg):
    """`seg` put into the sound at time t; everything after it moves later"""
    import copy
    i = int(t * SR)
    side = copy.deepcopy(side)
    _shift_times(side, t, len(seg) / SR)
    return np.concatenate([x[:i], seg.astype("float32"), x[i:]]), side


def _replace(x, side, a, b, y):
    """the sound between a and b replaced by `y` (another length); later times move"""
    import copy
    side = copy.deepcopy(side)
    ia, ib = int(a * SR), int(b * SR)
    _shift_times(side, b, len(y) / SR - (b - a))
    return np.concatenate([x[:ia], y.astype("float32"), x[ib:]]), side


def _resample(seg, factor):
    """the segment played `factor` times faster (shorter) or slower (longer, factor < 1), pitch following the speed"""
    n = max(8, int(len(seg) / factor))
    return np.interp(np.linspace(0, len(seg) - 1, n), np.arange(len(seg)), seg).astype("float32")


def _speech_rms_db(x, side, k):
    s = side["scenes"][k]
    seg = x[int(s["words"][0][1] * SR):int(s["words"][-1][2] * SR)]
    return float(20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9))


def real_cases(root, per_talk=2):
    """[(name, expected code, findings function)] for the faults that can be told apart from what real speech has anyway; each function returns the findings
    whose code is more frequent with the fault than without it. Left out on purpose: hiss under the voice (VOICE_HISS listens to the quiet stretch after each
    last word, and continuous speech has none), loudness and clipping (a real recording has its own)."""
    from collections import Counter
    prof = _profile.load()
    skip = ("check_ears", "check_drift", "check_engine_fallback", "check_render_fallback", "check_loudness", "check_true_peak", "check_gaps", "check_voice_cut")
    cases = []

    def findings(x, side, voice=None):
        ctx = Ctx(side=side, final=x, sr=SR, voice=x if voice is None else voice, profile=prof)
        out = []
        for m in (speech, audio, mix):
            for det in m.DETECTORS:
                if det.__name__ in skip:
                    continue
                try:
                    out += det(ctx)
                except Exception:
                    continue
        return out

    def more(clean, injected):
        a = Counter(f.code for f in clean if f.sev in ("ERROR", "WARN"))
        b = Counter(f.code for f in injected if f.sev in ("ERROR", "WARN"))
        return [f for f in injected if f.sev in ("ERROR", "WARN") and b[f.code] > a[f.code]]

    def breath_after(x, side, k=1):
        t = side["scenes"][k]["words"][-1][2] + 0.05
        noise = (np.random.default_rng(3).standard_normal(int(0.3 * SR)) * 10 ** (max(_speech_rms_db(x, side, k) - 26.0, -42.0) / 20)).astype("float32")      # a breath louder than -42 dBFS: below the floor of the detector it is not audible
        x2, s2 = _splice(x, side, t, noise)
        s2["scenes"][k]["end"] = t + 0.3 + 0.1
        return x2, s2, None

    def breathy_tail(x, side, k=1):
        t = side["scenes"][k]["words"][-1][2] + 0.05
        tail = (np.random.default_rng(4).standard_normal(int(0.45 * SR)) * 10 ** (max(_speech_rms_db(x, side, k) - 34.0, -46.0) / 20)).astype("float32")
        x2, s2 = _splice(x, side, t, tail)
        s2["scenes"][k]["end"] = t + 0.45 + 0.05
        return x2, s2, None

    def click(x, side):
        f, v, _m = mix.inject("click", x, x, None, SR, i=int(side["scenes"][1]["words"][2][2] * SR))
        return f, side, v

    def dropout(x, side):
        f, _v, _m = mix.inject("dropout", x, x, None, SR, t=side["scenes"][2]["words"][1][1])
        return f, side, x                                                      # the hole is in the mix; the dry voice kept speaking there: that is what makes it a dropout

    def level_jump(x, side, k=2):
        x2 = x.copy()
        s = side["scenes"][k]
        x2[int(s["start"] * SR):int(s["end"] * SR)] *= 10 ** (-12 / 20)
        return x2, side, None

    def fast(x, side, k=2):
        s = side["scenes"][k]
        a, b = s["words"][0][1], s["words"][-1][2]
        x2, s2 = _replace(x, side, a, b, _resample(x[int(a * SR):int(b * SR)], 1.7))
        for w in s2["scenes"][k]["words"]:
            w[1], w[2] = a + (w[1] - a) / 1.7, a + (w[2] - a) / 1.7
        s2["scenes"][k]["end"] = a + (b - a) / 1.7 + (s["end"] - b)
        return x2, s2, None

    def drone(x, side, k=2):
        a, b = side["scenes"][k]["words"][-1][1], side["scenes"][k]["words"][-1][2]
        f = min(0.4, (b - a) / 1.8)
        x2, s2 = _replace(x, side, a, b, _resample(x[int(a * SR):int(b * SR)], f))
        s2["scenes"][k]["words"][-1][2] = a + (b - a) / f
        s2["scenes"][k]["end"] = max(s2["scenes"][k]["end"], a + (b - a) / f + 0.05)
        return x2, s2, None

    def spoken(kind):
        def build(x, side):
            s2, v2 = speech.inject(kind, side, x, SR, scene=3, word=2)
            return v2, s2, None
        return build
    kinds = [("breath after a sentence", "VOICE_BREATH", breath_after), ("breathy tail", "VOICE_BREATH", breathy_tail),
             ("click", "VOICE_NOISE", click), ("dropout inside speech", "MIX_DROPOUT", dropout), ("voice level -12 dB in one scene", "VOICE_LEVEL", level_jump),
             ("a sentence 1.7 times faster", "VOICE_FAST", fast), ("last vowel dragged on", "VOICE_DRONE", drone), ("pause inside a sentence", "VOICE_PAUSE_INNER", spoken("pause_inner")),
             ("pitch jump", "VOICE_PITCH_JUMP", spoken("pitch_jump")), ("word said as another", "VOICE_SUB", spoken("sub")), ("word skipped", "VOICE_DEL", spoken("del")),
             ("word repeated", "VOICE_INS", spoken("ins_repeat")), ("words invented", "VOICE_INS", spoken("ins_hallucination"))]
    for name, x, side in real_excerpts(root, per_talk):
        base = {}

        def clean(x=x, side=side, base=base):
            if "f" not in base:
                base["f"] = findings(x, side)
            return base["f"]
        for label, expect, build in kinds:
            def fn(build=build, x=x, side=side, clean=clean):
                x2, s2, voice = build(x, side)
                return more(clean(), findings(x2, s2, voice))
            cases.append((f"real {name}: {label}", expect, fn))
    return cases


def run_real(root, per_talk=2, log=None):
    """the real-speech matrix: {cases, found, faults, score, by_kind}. A case that crashes is a miss, not an exception."""
    rows = []
    for name, expect, fn in real_cases(root, per_talk):
        try:
            got = sorted(codes(fn()))
        except Exception as e:
            rows.append({"name": name, "expect": expect, "ok": False, "got": [f"CRASH {type(e).__name__}: {e}"]})
            continue
        ok = expect in got
        rows.append({"name": name, "expect": expect, "ok": ok, "got": got})
        if log:
            log(f"{'ok    ' if ok else 'MISSED'} {name} -> {expect}" + ("" if ok else f"   new findings: {got}"))
    found = sum(1 for r in rows if r["ok"])
    by = {}
    for r in rows:
        by.setdefault(r["name"].split(": ", 1)[1], []).append(r["ok"])
    return {"cases": rows, "found": found, "faults": len(rows), "score": found / len(rows) if rows else None, "by_kind": {k: f"{sum(v)}/{len(v)}" for k, v in by.items()}}
