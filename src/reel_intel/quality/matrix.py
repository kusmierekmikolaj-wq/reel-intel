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
