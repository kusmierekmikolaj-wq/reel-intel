"""audio.py - faults of the SOUND of a reel that are judged from the samples and the word times: the pauses between scenes, a sentence cut off or dragged
out, breaths and murmurs, level jumps, hiss, the speaking rate, and what the sound-event model hears. (The recogniser based checks, the monotone voice and
the voice drift are in speech.py; loudness, clipping, pumping and the mix are in mix.py.)

Every detector is a pure function  check(ctx) -> [Finding]  of ctx.final (the mix as heard), ctx.voice (the dry voice, the final mix when there is none),
ctx.sr, the sidecar scenes (start, end, words [[word, t0, t1], ...], text) and ctx.ears (output of ears.py). Thresholds come from ctx.profile
(quality/profile.py); the keys profile.py does not carry are in PROFILE_ADDITIONS.

The numbers behind the defaults were measured, not guessed (see docs/QA.md):
  - pace: real speakers in our data (4 TED talks, 10 AI-account reels, 20 marketing reels, 8 NASA videos) speak 2.9-6.5 syllables a second, median about 5.
    The syllable detector (de Jong & Wempe, simplified) was validated on synthetic speech of 3.5-8 syllables a second.
  - soft tail: a breathy exhale after the last word measured -36...-53 dB (up to 0.9-2.7 s) and was counted by the recogniser as part of the last word.
  - ears: the AudioSet label "Gasp" at 0.07 or more marked the breaths that a person heard at the end of scenes 1 and 5 of the first reel.
What it cannot judge: whether a pause feels natural, whether a breath is wanted (a documentary voice breathes on purpose), whether the music is pleasant.
"""
import re

import numpy as np

from .base import finding, register
from .profile import DEFAULT as _PROFILE_DEFAULT

MODULE = "audio"

GAP_LONG = register("GAP_LONG", "more than gap.long_s of nothing said between two sentences", "WARN", "retrim_scene", MODULE)
GAP_SHORT = register("GAP_SHORT", "less than gap.short_s between two sentences: they run together", "WARN", None, MODULE)
GAP_LOUD = register("GAP_LOUD", "the sound between two sentences is almost as loud as the voice (music too loud, something is playing)", "WARN", "remix", MODULE)
GAP_RUMBLE = register("GAP_RUMBLE", "the sound between two sentences is mostly very low tones (rumble)", "WARN", "remix", MODULE)
GAP_CLICK = register("GAP_CLICK", "a click or a jump in the sound between two sentences", "WARN", "remix", MODULE)
VOICE_CUT = register("VOICE_CUT", "the voice is still speaking at full level when the scene's sound ends: the last word is chopped", "ERROR", "retake_scene", MODULE)
VOICE_DRONE = register("VOICE_DRONE", "the last word lasts longer than voice.last_word_max_s: a vowel dragged on", "WARN", "retrim_scene", MODULE)
VOICE_BREATH = register("VOICE_BREATH", "a breath, a mouth noise or a breathy tail inside the voice track (before, between or after the words)", "WARN", "retrim_scene", MODULE)
VOICE_LEVEL = register("VOICE_LEVEL", "the voice level jumps by more than voice.level_jump_db between two scenes", "WARN", "level_scenes", MODULE)
VOICE_HISS = register("VOICE_HISS", "noise under the voice louder than voice.hiss_db: audible hiss", "WARN", "retake_scene", MODULE)
VOICE_TAIL = register("VOICE_TAIL", "silence after the last word in the voice file (trimmed in the render)", "INFO", None, MODULE)
VOICE_FAST = register("VOICE_FAST", "a scene spoken faster than pace.fast syllables a second (heard or by the words)", "WARN", "slow_scene", MODULE)
VOICE_SLOW = register("VOICE_SLOW", "a scene spoken slower than pace.slow syllables a second: dragging", "WARN", "retake_scene", MODULE)
VOICE_BURST = register("VOICE_BURST", "a rushed stretch inside a sentence of normal pace (more than pace.burst syllables in one second)", "WARN", "slow_scene", MODULE)
PACE = register("PACE", "the average speaking rate of the reel, for information", "INFO", None, MODULE)
EAR_BREATH = register("EAR_BREATH", "the sound-event model hears a breath or a body sound (AudioSet labels such as Gasp, Breathing, Sigh)", "WARN", "retrim_scene", MODULE)
EARS_OFF = register("EARS_OFF", "the sound-event model did not run: breaths are judged by level only", "INFO", None, MODULE)

PROFILE_ADDITIONS = {
    "gap": {"click_jump": 0.35, "min_seg_s": 0.08},
    "voice": {"cut_db_under_speech": 10.0, "tail_warn_s": 0.5, "breath_db_under_speech": 32.0, "breath_floor_db": -52.0, "breath_flatness": 0.12,
              "breath_min_gap_s": 0.25, "breath_min_seg_s": 0.1, "ears_min_score": 0.07},
    "pace": {"min_scene_s": 1.0, "burst_window_s": 1.0},
}


def _thr(ctx, section, key):
    for src in (getattr(ctx, "profile", None) or {}, PROFILE_ADDITIONS, _PROFILE_DEFAULT):
        sec = src.get(section) if isinstance(src, dict) else None
        if isinstance(sec, dict) and sec.get(key) is not None:
            return sec[key]
    raise KeyError(f"no threshold {section}.{key}")


# ---------------------------------------------------------------- pure helpers on samples
def db(x):
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-9)) if len(x) else -120.0


def lf_share(x, sr, cut=120.0):
    """share of the energy below `cut` Hz (rumble)"""
    if len(x) < 64:
        return 0.0
    s = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / sr)
    return float(s[f < cut].sum() / (s.sum() + 1e-12))


def flatness(x):
    """0 = a pure tone, 1 = white noise (spectral flatness)"""
    if len(x) < 256:
        return 0.0
    s = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2 + 1e-12
    return float(np.exp(np.mean(np.log(s))) / np.mean(s))


def soft_tail(x, sr):
    """seconds of quiet-but-audible sound after the last speech in `x`: frames between 24 and 45 dB under the usual speech level, after the last speech frame"""
    hop = max(1, int(sr * 0.02))
    n = (len(x) - hop) // hop
    if n < 5:
        return 0.0
    d = np.array([db(x[i * hop:(i + 1) * hop]) for i in range(n)])
    p = float(np.percentile(d, 90))
    speech = np.where(d > p - 24)[0]
    if not len(speech):
        return 0.0
    after = d[speech[-1] + 1:]
    return float(np.sum(after > p - 45) * hop / sr)


def syllable_count(text):
    return len(re.findall(r"[aeiouyąęó]+", text.lower()))


def nuclei(x, sr):
    """times (s) of syllable nuclei counted from the sound itself: peaks of the energy in the 300-3000 Hz band that stand out from their surroundings, at
    least 80 ms apart (the method of de Jong & Wempe, simplified). Independent of speech recognition."""
    fl, hop = int(sr * 0.025), int(sr * 0.010)
    if len(x) < fl * 4:
        return []
    win = np.hanning(fl)
    f = np.fft.rfftfreq(fl, 1 / sr)
    band = (f >= 300) & (f <= 3000)
    e = np.array([np.sum(np.abs(np.fft.rfft(x[i:i + fl] * win))[band] ** 2) for i in range(0, len(x) - fl, hop)]) + 1e-12
    d = 10 * np.log10(e)
    d = np.convolve(d, np.ones(5) / 5, mode="same")                          # 50 ms smoothing: one bump per syllable
    floor = np.percentile(d, 90) - 28
    peaks, last = [], -99
    for i in range(3, len(d) - 3):
        if d[i] > floor and d[i] >= d[i - 1] and d[i] > d[i + 1]:
            dip = min(d[max(0, i - 12):i].min(), d[i + 1:i + 13].min()) if i + 13 < len(d) else d[max(0, i - 12):i].min()
            if d[i] - dip >= 2.5 and i - last >= 8:                          # rises at least 2.5 dB above the valley next to it
                peaks.append(i * hop / sr)
                last = i
    return peaks


def rate_in(peaks, a, b):
    """syllables a second between a and b seconds"""
    return sum(1 for t in peaks if a <= t <= b) / max(b - a, 0.3)


# ---------------------------------------------------------------- what a detector reads
def _voice(ctx):
    v = getattr(ctx, "voice", None)
    return v if v is not None else getattr(ctx, "final", None)


def _sc(ctx):
    """[(1-based number, scene)] of the scenes that have word times; a scene without start or end gets the first and last word's time"""
    out = []
    for i, s in enumerate(ctx.scenes, 1):
        w = s.get("words") or []
        try:
            a, b = float(w[0][1]), float(w[-1][2])
            start, end = float(s.get("start", a)), float(s.get("end", b))
        except (IndexError, KeyError, TypeError, ValueError):
            continue
        out.append((i, {**s, "start": start, "end": end}))
    return out


def _seg(x, sr, a, b):
    return x[int(max(0.0, a) * sr):int(max(0.0, b) * sr)]


# ---------------------------------------------------------------- the detectors
def check_gaps(ctx):
    """GAP_LONG / GAP_SHORT / GAP_LOUD / GAP_RUMBLE / GAP_CLICK: the time between the last word of a scene and the first word of the next one, in the final mix.
    Loudness is judged against the median level of the speech; music audible in a pause is normal, music nearly as loud as the voice is not."""
    final = getattr(ctx, "final", None)
    sc = _sc(ctx)
    if final is None or len(sc) < 2:
        return []
    sr = ctx.sr
    out = []
    speech = [db(_seg(final, sr, s["words"][0][1], s["words"][-1][2])) for _i, s in sc]
    ref = sorted(speech)[len(speech) // 2]
    long_s, short_s = _thr(ctx, "gap", "long_s"), _thr(ctx, "gap", "short_s")
    under, rumble, rumble_floor = _thr(ctx, "gap", "loud_db_under_speech"), _thr(ctx, "gap", "rumble_share"), _thr(ctx, "gap", "rumble_floor_db")
    jump, min_seg = _thr(ctx, "gap", "click_jump"), _thr(ctx, "gap", "min_seg_s")
    for (i, a_sc), (j, b_sc) in zip(sc, sc[1:]):
        if j != i + 1:
            continue
        a, b = float(a_sc["words"][-1][2]), float(b_sc["words"][0][1])
        n = f"{i}->{j}"
        if b - a > long_s:
            out.append(finding(GAP_LONG, f"scenes {n}: {b - a:.2f} s of nothing said between the sentences (aim at 0.25-0.6 s)", scene=i, t=a, gap_s=round(b - a, 3)))
        if b - a < short_s:
            out.append(finding(GAP_SHORT, f"scenes {n}: only {b - a:.2f} s between the sentences, they run together", scene=i, t=a, gap_s=round(b - a, 3)))
        seg = _seg(final, sr, a + 0.05, b - 0.02)
        if len(seg) < sr * min_seg:
            continue
        lvl = db(seg)
        if lvl > ref - under:
            out.append(finding(GAP_LOUD, f"scenes {n}: sound between the sentences is {lvl:.0f} dB, only {ref - lvl:.0f} dB under the voice (the music is too loud or something is playing)",
                               scene=i, t=a, level_db=round(lvl, 1), under_voice_db=round(ref - lvl, 1)))
        share = lf_share(seg, sr)
        if share > rumble and lvl > rumble_floor:
            out.append(finding(GAP_RUMBLE, f"scenes {n}: the sound between the sentences is mostly very low tones ({share * 100:.0f}% under 120 Hz): rumble",
                               scene=i, t=a, share=round(share, 3), level_db=round(lvl, 1)))
        if len(seg) > 8 and float(np.abs(seg[1:] - seg[:-1]).max()) > jump:
            out.append(finding(GAP_CLICK, f"scenes {n}: a click or a jump in the sound between the sentences", scene=i, t=a))
    return out


def check_voice_cut(ctx):
    """VOICE_CUT: the voice track is still at full level in the last 40 ms of the scene's sound: the last word is chopped"""
    x = _voice(ctx)
    if x is None:
        return []
    sr, under = ctx.sr, _thr(ctx, "voice", "cut_db_under_speech")
    out = []
    for i, s in _sc(ctx):
        a, b = float(s["words"][0][1]), float(s["words"][-1][2])
        lv = db(_seg(x, sr, a, b))
        end = float(s["end"])
        part = x[max(0, int(end * sr) - int(0.04 * sr)):int(end * sr)]
        if len(part) and db(part) > lv - under:
            out.append(finding(VOICE_CUT, f"scene {i}: the voice is cut off mid-sound at the end of the scene (last word chopped)", scene=i, t=end, end_db=round(db(part), 1),
                               speech_db=round(lv, 1)))
    return out


def check_voice_drone(ctx):
    """VOICE_DRONE: the last word of a scene lasts longer than voice.last_word_max_s (the recogniser's word times, which count a breathy tail as the word)"""
    mx = _thr(ctx, "voice", "last_word_max_s")
    out = []
    for i, s in _sc(ctx):
        w = s["words"][-1]
        d = float(w[2]) - float(w[1])
        if d > mx:
            out.append(finding(VOICE_DRONE, f"scene {i}: the last word '{w[0]}' lasts {d:.1f} s: a vowel dragged on", scene=i, t=float(w[1]), duration=round(d, 3), word=str(w[0])))
    return out


def check_voice_level(ctx):
    """VOICE_LEVEL: the level of the speech of two neighbouring scenes differs by more than voice.level_jump_db"""
    x = _voice(ctx)
    if x is None:
        return []
    jump = _thr(ctx, "voice", "level_jump_db")
    levels = [(i, db(_seg(x, ctx.sr, s["words"][0][1], s["words"][-1][2]))) for i, s in _sc(ctx)]
    out = []
    for (i, p), (j, q) in zip(levels, levels[1:]):
        if abs(p - q) > jump:
            out.append(finding(VOICE_LEVEL, f"scenes {i}->{j}: the voice level jumps by {abs(p - q):.0f} dB", scene=j, jump_db=round(abs(p - q), 1), levels=[round(p, 1), round(q, 1)]))
    return out


def check_voice_hiss(ctx):
    """VOICE_HISS: the median level of the voice track in the quiet stretch after each last word (0.1 s later) is above voice.hiss_db"""
    x = _voice(ctx)
    if x is None:
        return []
    sr, lim = ctx.sr, _thr(ctx, "voice", "hiss_db")
    quiet = []
    for _i, s in _sc(ctx):
        a, b = float(s["words"][-1][2]) + 0.1, float(s["end"])
        if b - float(s["words"][-1][2]) > 0.3 and b > a:
            quiet.append(db(_seg(x, sr, a, b)))
    if quiet:
        m = sorted(quiet)[len(quiet) // 2]
        if m > lim:
            return [finding(VOICE_HISS, f"noise under the voice is about {m:.0f} dB: audible hiss", level_db=round(m, 1), limit_db=lim)]
    return []


def check_voice_tail(ctx):
    """VOICE_TAIL (INFO): the voice file has more than voice.tail_warn_s of silence after the last word (the render trims it)"""
    lim = _thr(ctx, "voice", "tail_warn_s")
    return [finding(VOICE_TAIL, f"scene {i}: {float(s['tail']):.2f} s of silence after the last word in the voice file (trimmed in the render)", scene=i, tail_s=float(s["tail"]))
            for i, s in _sc(ctx) if float(s.get("tail") or 0) > lim]


def check_breath(ctx):
    """VOICE_BREATH: (1) a quiet breathy tail after the last word longer than voice.soft_tail_s (24-45 dB under the speech level); (2) a stretch before the first
    word, between words or after the last word that is quieter than the speech but louder than the room and noisy (spectral flatness over voice.breath_flatness)."""
    x = _voice(ctx)
    if x is None:
        return []
    sr = ctx.sr
    lim = _thr(ctx, "voice", "soft_tail_s")
    under, floor, flat = _thr(ctx, "voice", "breath_db_under_speech"), _thr(ctx, "voice", "breath_floor_db"), _thr(ctx, "voice", "breath_flatness")
    gap, min_seg = _thr(ctx, "voice", "breath_min_gap_s"), _thr(ctx, "voice", "breath_min_seg_s")
    out = []
    for i, s in _sc(ctx):
        w = s["words"]
        st = soft_tail(_seg(x, sr, s["start"], s["end"]), sr)
        if st > lim:
            out.append(finding(VOICE_BREATH, f"scene {i}: {st:.2f} s of a quiet breathy tail after the last word (a breath or a trailing murmur)", scene=i, t=float(w[-1][2]),
                               where="tail", seconds=round(st, 3)))
        speech = db(_seg(x, sr, w[0][1], w[-1][2]))
        spots = [("before the first word", "before", float(s["start"]), float(w[0][1]) - 0.04), ("after the last word", "after", float(w[-1][2]) + 0.04, float(s["end"]))]
        spots += [(f"between '{a[0]}' and '{b[0]}'", "inside", float(a[2]) + 0.04, float(b[1]) - 0.04) for a, b in zip(w, w[1:]) if float(b[1]) - float(a[2]) > gap]
        for where, kind, a, b in spots:
            seg = _seg(x, sr, a, b)
            if len(seg) < sr * min_seg:
                continue
            lvl = db(seg)
            if lvl > speech - under and lvl > floor and flatness(seg) > flat:
                out.append(finding(VOICE_BREATH, f"scene {i}: a breath or noise {where} ({lvl:.0f} dB, {lvl - speech:.0f} dB under the speech, noisy)", scene=i, t=a, where=kind,
                                   level_db=round(lvl, 1), under_speech_db=round(speech - lvl, 1)))
    return out


def check_pace(ctx):
    """VOICE_FAST / VOICE_SLOW / VOICE_BURST (+ PACE as INFO): syllables a second counted from the sound (nuclei) and from the words of the script, per scene and
    per second inside a scene, against pace.* (real speakers: 2.9-6.5). A scene is FAST when the faster of the two counts is over pace.fast; SLOW only when BOTH are
    under pace.slow (the recogniser's word times can stretch a breathy tail and make a normal scene look slow)."""
    x = _voice(ctx)
    if x is None:
        return []
    sr = ctx.sr
    fast, slow, burst = _thr(ctx, "pace", "fast"), _thr(ctx, "pace", "slow"), _thr(ctx, "pace", "burst")
    min_s, win = _thr(ctx, "pace", "min_scene_s"), _thr(ctx, "pace", "burst_window_s")
    peaks = nuclei(x, sr)
    out, rates = [], []
    for i, s in _sc(ctx):
        a, b = float(s["words"][0][1]), float(s["words"][-1][2])
        heard = rate_in(peaks, a, b)
        text = syllable_count(s.get("text") or " ".join(str(w[0]) for w in s["words"])) / max(b - a, 0.3)
        worst = max(heard, text)
        rates.append(worst)
        if worst > fast:
            out.append(finding(VOICE_FAST, f"scene {i}: {heard:.1f} syllables a second in the sound ({text:.1f} by the words); real speakers stay under {fast}", scene=i, t=a,
                               heard=round(heard, 2), by_words=round(text, 2), limit=fast))
        elif max(heard, text) < slow and b - a > min_s:
            out.append(finding(VOICE_SLOW, f"scene {i}: {heard:.1f} syllables a second in the sound ({text:.1f} by the words): dragging", scene=i, t=a,
                               heard=round(heard, 2), by_words=round(text, 2), limit=slow))
        for k in range(int(max(0.0, b - a - win) / 0.5) + 1):
            t0 = a + k * 0.5
            r = rate_in(peaks, t0, t0 + win)
            if r > burst and worst <= fast:
                out.append(finding(VOICE_BURST, f"scene {i} at {t0:.1f} s: a rushed stretch, {r:.0f} syllables in one second", scene=i, t=t0, rate=round(r, 2)))
                break
    if rates:
        avg = sum(rates) / len(rates)
        out.append(finding(PACE, f"speaking rate {avg:.1f} syllables a second on average (real speakers in our data: 2.9-6.5, median 5)", rate=round(avg, 2)))
    return out


def check_ears(ctx):
    """EAR_BREATH: windows of the sound-event model (ctx.ears from ears.py) whose best breath-like label (BREATH_LABELS) scores at least voice.ears_min_score.
    One finding per label and per two seconds. Without ctx.ears an INFO line says the model did not run."""
    ears = getattr(ctx, "ears", None)
    if not ears or not ears.get("windows"):
        return [finding(EARS_OFF, "the sound-event model did not run (the Chatterbox environment has it): breaths are judged by level only")]
    from ..ears import BREATH_LABELS, breath_score
    lim = _thr(ctx, "voice", "ears_min_score")
    out, seen = [], set()
    sc = _sc(ctx)
    for w in ears["windows"]:
        p, name = breath_score(w["labels"], BREATH_LABELS)
        if p < lim:
            continue
        t0, t1 = float(w["t"]), float(w["t"]) + float(ears.get("window", 1.0))
        key = (name, int(t0 // 2))
        if key in seen:
            continue
        seen.add(key)
        mid = (t0 + t1) / 2
        no = next((i for i, s in sc if float(s["words"][0][1]) - 0.2 <= mid <= float(s["end"]) + 0.3), None)
        out.append(finding(EAR_BREATH, f"{t0:.1f}-{t1:.1f} s" + (f" (scene {no})" if no else " (between scenes)") + f": the sound model hears '{name}' ({p:.2f})",
                           scene=no, t=t0, label=name, score=round(float(p), 3), where="scene" if no else "between"))
    return out


DETECTORS = [check_gaps, check_voice_cut, check_voice_drone, check_voice_level, check_voice_hiss, check_voice_tail, check_breath, check_pace, check_ears]
