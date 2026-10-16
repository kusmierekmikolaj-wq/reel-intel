"""sync.py - timing faults between sound, picture and captions. Every detector is a pure function of numbers (numpy + difflib): it never decodes,
renders or recognises speech itself; the finished mp4's facts and the re-recognised words arrive through ctx.side.

Codes
  CAPTION_SYNC  the word times the captions were drawn with (sidecar) against the same words re-recognised from the FINISHED mp4
  LIPSYNC       the mascot's mouth against the loudness of the dry voice: correlation AND lag (a lag is read off a cross-correlation of the two envelopes)
  AV_OFFSET     where the sound track and the picture track start in the container (ffprobe start times)

Sign convention for every offset in this module (EBU R37 / ITU-R BT.1359): offset_ms > 0  means the SOUND comes BEFORE the picture (audio early,
audio leads); offset_ms < 0 means the sound comes AFTER the picture (audio late, audio lags). The one exception is CAPTION_SYNC, where the shift is
heard_time - caption_time: > 0 means the caption word is on screen BEFORE it is heard.

Optional sidecar / ctx fields used (a detector that does not find its field emits nothing, never an error)
  CAPTION_SYNC  side["scenes"][i]["words"] and side["rehear"]  (list of [word, t0, t1] recognised from the finished mp4)
  LIPSYNC       side["mouth"] (0..1 per frame at ctx.fps), ctx.voice (dry voice, same sample rate ctx.sr); ctx.final (the mix as heard) is used,
                when present, to find a delay added by the mix/encode; side["params"]["mascot"] == False switches the check off
  AV_OFFSET     side["video"]["video_start"] and side["video"]["audio_start"] in seconds (platform.probe() writes them)

What these cannot judge
  * Whisper word times are not exact: on real speech re-recognised after a music mix and an AAC encode the median word start differs from the clean
    recognition by a few tens of ms (numbers in docs/QA.md). CAPTION_SYNC therefore reports a median first and treats the maximum as a hint.
  * LIPSYNC compares the mouth with the LOUDNESS of the voice, not with the mouth shapes (visemes); it cannot see a wrong viseme, only a mouth that
    opens at the wrong time. A mouth is built from the dry voice, so the lag against the dry voice is near zero by construction; the part that can
    really go wrong is the delay of the finished audio (needs ctx.final) and the container start times (AV_OFFSET).
  * make.mouth_track cuts the voice into frames of int(sr / fps) samples. At 24 kHz (Chatterbox) and 22.05 kHz that is exact; at 16 kHz it is 533 instead of
    533.33 samples, the mouth drifts 0.6 ms per second against the voice (16 ms after 27 s) and the measured bias grows with the length of the reel. The
    calibration of sync.mouth_bias_ms was therefore made on speech resampled to 24 kHz. A 16 kHz or 11.025 kHz voice engine would show that drift as a lag.
  * The EBU R37 and ITU-R BT.1359 windows are quoted from secondary sources (and an NTIA report uses the opposite sign): marked uncertain.
"""
import copy
import difflib
import re
import tempfile
import wave
from pathlib import Path

import numpy as np

from .base import Ctx, finding, register
from .profile import DEFAULT as _PROFILE_DEFAULT

CAPTION_SYNC = register("CAPTION_SYNC", "the caption word times differ from the words heard in the finished mp4 (median or maximum shift above the profile)",
                        "WARN", "rerender", "sync")
LIPSYNC = register("LIPSYNC", "the mascot's mouth does not follow the loudness of the voice (weak correlation, flat) or follows it at the wrong time (lag beyond EBU R37)",
                   "WARN", "rerender", "sync")
AV_OFFSET = register("AV_OFFSET", "the sound track starts earlier or later than the picture track in the container by more than the sync window",
                     "WARN", "rerender", "sync")

# Thresholds this module needs that profile.py does not carry yet. A value in ctx.profile["sync"] wins over this table, then profile.DEFAULT, then this table.
# Move them into profile.DEFAULT["sync"]; a key kept here has the source and how sure it is in PROVENANCE.
EXTRA = {"sync": {
    "caption_scene_factor": 2.0,      # a scene shifted as a block warns above this many times sync.caption_ms (measured: 0 of 207 clean scenes, 92-96% of shifted ones at 200+ ms)
    "caption_scene_min_words": 3,     # a scene needs this many matched words to be judged on its own
    "caption_min_words": 5,           # fewer re-recognised words than this: the shift is not judged, an INFO line says so
    "caption_fuzzy_ratio": 0.75,      # two unmatched words of the same block count as the same word above this spelling similarity
    "lipsync_corr_min": 0.5,          # correlation of mouth and voice loudness per frame, lag 0: the meaning of the old MOUTH_SYNC
    "lag_range_ms": 300,              # the lag is searched in +-this many ms
    "lag_step_ms": 5,                 # resolution of the search (a parabola through the peak refines it below this)
    "lag_corr_min": 0.5,              # a lag is only trusted when the best correlation reaches this
    "mouth_bias_ms": 7.0,             # systematic lag of make.mouth_track against the raw voice envelope (its slower release); subtracted from the lag. MEASURED at 24 kHz:
                                      # 7.0 ms median over 70 windows (TED 40 x 30 s: std 0.5; AI-account reels 29: std 1.9; 32.6 min) and 7.3 ms on our generated Polish reel
    "mix_lag_corr_min": 0.6,          # the delay added by the mix is judged only when dry voice and final envelopes correlate at least this much
}}
PROVENANCE = {
    "sync.audio_early_ms": "EBU R37 +40 ms audio before picture, ITU-R BT.1359 detectability +45 ms (secondary sources): uncertain",
    "sync.audio_late_ms": "EBU R37 -60 ms audio after picture; BT.1359 detectability -125 ms, acceptability -185 ms (secondary sources): uncertain",
    "sync.caption_ms": "from the task text (80 ms); whisper word-time noise on real speech measured in docs/QA.md",
    "sync.lipsync_corr_min": "kept from reel_qa.mouth() (0.5)",
    "sync.mouth_bias_ms": "measured on real speech with make.mouth_track at 24 kHz: 7.0 ms, std 1.3 ms, n = 70 windows (32.6 min); at 16 kHz make.mouth_track drifts (see the module docstring)",
}


def _th(ctx, key, section="sync"):
    """threshold `key`: ctx.profile, else profile.DEFAULT, else EXTRA"""
    for src in (getattr(ctx, "profile", None) or {}, _PROFILE_DEFAULT, EXTRA):
        v = src.get(section)
        if isinstance(v, dict) and key in v and v[key] is not None:
            return v[key]
    raise KeyError(f"no threshold {section}.{key}")


# ---------------------------------------------------------------- caption sync

def _norm(w):
    return re.sub(r"[\W_]+", "", str(w).lower())


def caption_words(side):
    """[(word, t0, t1, scene)] of the sidecar in reading order (scene is 1-based)"""
    out = []
    for i, s in enumerate(side.get("scenes", []), 1):
        for w in s.get("words") or []:
            try:
                out.append((str(w[0]), float(w[1]), float(w[2]), i))
            except (TypeError, ValueError, IndexError):
                continue
    return out


def heard_words(rehear):
    """[(word, t0, t1)] from side["rehear"], malformed entries dropped"""
    out = []
    for w in rehear or []:
        try:
            out.append((str(w[0]), float(w[1]), float(w[2])))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def match_words(cap, heard, fuzzy=0.75):
    """[(i, j)]: caption word i is the re-heard word j. Words are compared by their text without case and punctuation (difflib); inside a block where
    both sides have the same number of unmatched words, two words with a spelling similarity >= `fuzzy` are paired too (a recogniser writes 'skili'
    for 'skilli')"""
    ci = [(k, _norm(w[0])) for k, w in enumerate(cap) if _norm(w[0])]
    hi = [(k, _norm(w[0])) for k, w in enumerate(heard) if _norm(w[0])]
    a, b = [t for _k, t in ci], [t for _k, t in hi]
    pairs = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            pairs += [(ci[i1 + k][0], hi[j1 + k][0]) for k in range(i2 - i1)]
        elif tag == "replace" and i2 - i1 == j2 - j1:
            pairs += [(ci[i1 + k][0], hi[j1 + k][0]) for k in range(i2 - i1)
                      if difflib.SequenceMatcher(None, a[i1 + k], b[j1 + k]).ratio() >= fuzzy]
    return pairs


def caption_offsets(side, fuzzy=0.75):
    """(cap, heard, pairs, offsets_s): offsets_s[k] = heard start - caption start of the k-th matched word"""
    cap, heard = caption_words(side), heard_words(side.get("rehear"))
    pairs = match_words(cap, heard, fuzzy)
    return cap, heard, pairs, np.array([heard[j][1] - cap[i][1] for i, j in pairs], dtype=float)


def check_caption_sync(ctx: Ctx):
    """CAPTION_SYNC. Matches the caption words with side["rehear"] by text and measures heard start - caption start per word. Reports the median and the
    maximum absolute shift in ms (an INFO line with the numbers whenever side["rehear"] is present). WARN when
      1. the median |shift| over all words is above profile sync.caption_ms (all captions early or late, or drifting), or
      2. one scene (>= caption_scene_min_words matched words) is shifted as a block: |median of its signed shifts| above caption_scene_factor times
         sync.caption_ms AND the words of that scene agree with each other (median distance from their own median below sync.caption_ms). One WARN per such scene.
    The MAXIMUM is reported but NOT judged: the task asked for a warning above 2x sync.caption_ms, but on real speech a single word's recognised start
    jumps by hundreds of ms between two recognitions of the same sound (see the measurements in the module docstring and docs/QA.md), so a maximum rule
    warns on clean reels. A single wrong word cannot be told from the recogniser's own jitter; a block of words shifted the same way can.
    Nothing when side["rehear"] is absent; too few matched words (< caption_min_words): an INFO line, no judgement."""
    if not ctx.side.get("rehear"):
        return []
    lim = float(_th(ctx, "caption_ms"))
    cap, heard, pairs, off = caption_offsets(ctx.side, float(_th(ctx, "caption_fuzzy_ratio")))
    if not cap:
        return []
    if len(pairs) < int(_th(ctx, "caption_min_words")):
        return [finding("CAPTION_SYNC", f"only {len(pairs)} of {len(cap)} caption words were found in the re-recognised sound of the mp4: shift not judged", "INFO",
                        n_matched=len(pairs), n_words=len(cap))]
    ms = off * 1000.0
    med_abs, mx, shift = float(np.median(np.abs(ms))), float(np.max(np.abs(ms))), float(np.median(ms))
    k = int(np.argmax(np.abs(ms)))
    w_cap = cap[pairs[k][0]]
    by_scene = {}
    for (i, _j), v in zip(pairs, ms):
        by_scene.setdefault(cap[i][3], []).append(float(v))
    scene_med = {s: float(np.median(v)) for s, v in by_scene.items()}
    data = dict(median_abs_ms=med_abs, max_abs_ms=mx, shift_ms=shift, n_matched=len(pairs), n_words=len(cap), worst_word=w_cap[0], worst_t=w_cap[1],
                scene_median_ms=scene_med, limit_ms=lim)
    side_of = lambda v: "before" if v > 0 else "after"                    # shift > 0: the caption word is on screen before it is heard
    stats = (f"median shift {med_abs:.0f} ms (limit {lim:.0f} ms), maximum {mx:.0f} ms (not judged), over {len(pairs)} of {len(cap)} words; "
             f"on average the caption word is {side_of(shift)} the voice by {abs(shift):.0f} ms")
    if med_abs > lim:
        bad = max(scene_med, key=lambda s: abs(scene_med[s]))
        return [finding("CAPTION_SYNC", f"captions are out of step with the voice: {stats}", "WARN", scene=bad, t=w_cap[1], **data)]
    out = []
    smin, sfac = int(_th(ctx, "caption_scene_min_words")), float(_th(ctx, "caption_scene_factor"))
    for s, v in sorted(by_scene.items()):
        v = np.array(v)
        m = float(np.median(v))
        if len(v) >= smin and abs(m) > sfac * lim and float(np.median(np.abs(v - m))) < lim:
            t0 = next(c[1] for c in cap if c[3] == s)
            out.append(finding("CAPTION_SYNC", f"scene {s}: its captions are shifted as a block, the caption words are {side_of(m)} the voice by {abs(m):.0f} ms "
                               f"(limit {sfac * lim:.0f} ms; {len(v)} words agree)", "WARN", scene=s, t=t0, scene_shift_ms=m, **data))
    return out or [finding("CAPTION_SYNC", f"captions follow the voice: {stats}", "INFO", **data)]


# ---------------------------------------------------------------- lip sync

def frame_rms(x, sr, fps, n):
    """RMS of the voice per video frame (hop int(sr/fps)): the envelope the old reel_qa.mouth() correlated with"""
    hop = int(sr / fps)
    return np.array([np.sqrt(np.mean(np.square(x[i * hop:(i + 1) * hop]))) for i in range(n)])


def fine_env(x, sr, win_s, hop_s):
    """sliding RMS: (values, centre_times in s); window `win_s` long, one value every `hop_s`"""
    x = np.asarray(x, dtype=np.float64)
    win, hop = max(1, int(round(sr * win_s))), max(1, int(round(sr * hop_s)))
    if len(x) < win:
        return np.zeros(0), np.zeros(0)
    c = np.concatenate(([0.0], np.cumsum(x * x)))
    st = np.arange(0, len(x) - win + 1, hop)
    return np.sqrt(np.maximum(c[st + win] - c[st], 0.0) / win), (st + win / 2.0) / sr


def lag_curve(a, b, step_s, max_s):
    """Pearson correlation of b(t) with a(t - d) for d in [-max_s, max_s] (arrays on one grid of `step_s`): d > 0 means b happens LATER than a.
    Returns (lags_s, corr); corr is nan where a lag leaves too little overlap or a signal is flat"""
    n = min(len(a), len(b))
    a, b = np.asarray(a[:n], dtype=float), np.asarray(b[:n], dtype=float)
    k = int(round(max_s / step_s))
    lags = np.arange(-k, k + 1)
    out = np.full(len(lags), np.nan)
    for q, d in enumerate(lags):
        x, y = (a[:n - d], b[d:]) if d >= 0 else (a[-d:], b[:n + d])
        if len(x) < 20:
            continue
        sx, sy = x.std(), y.std()
        if sx < 1e-12 or sy < 1e-12:
            continue
        out[q] = float(np.mean((x - x.mean()) * (y - y.mean())) / (sx * sy))
    return lags * step_s, out


def peak_lag(lags, corr):
    """(lag_s, peak_corr, at_edge): the highest correlation, its position refined with a parabola through its neighbours"""
    if not np.isfinite(corr).any():
        return 0.0, float("nan"), False
    i = int(np.nanargmax(corr))
    lag = float(lags[i])
    edge = i in (0, len(corr) - 1)
    if not edge and np.isfinite(corr[i - 1]) and np.isfinite(corr[i + 1]):
        den = corr[i - 1] - 2 * corr[i] + corr[i + 1]
        if den < -1e-12:
            lag += float(0.5 * (corr[i - 1] - corr[i + 1]) / den) * float(lags[1] - lags[0])
    return lag, float(corr[i]), edge


def mouth_lag(mouth, voice, sr, fps, max_ms=300, step_ms=5):
    """how the mouth array sits against the voice. Returns {corr0, corr_peak, lag_ms, at_edge}:
    corr0     Pearson correlation of the per-frame RMS of the voice and the mouth at lag 0 (what reel_qa.mouth() computed)
    lag_ms    d > 0: the mouth moves d ms AFTER the sound that drives it (the sound is early); d < 0: the mouth moves before the sound.
              Frame i of the mouth is placed at (i + 0.5) / fps, the middle of the interval the frame is on screen and the interval make.mouth_track
              measured the voice over."""
    n = min(len(mouth), len(voice) // int(sr / fps))
    m = np.asarray(mouth[:n], dtype=float)
    env = frame_rms(voice, sr, fps, n)
    corr0 = float(np.corrcoef(env, m)[0, 1]) if env.std() > 1e-9 and m.std() > 1e-9 else float("nan")
    step = step_ms / 1000.0
    e, tc = fine_env(voice[:int(n * sr / fps)], sr, 1.0 / fps, step)
    if len(e) < 20:
        return {"corr0": corr0, "corr_peak": float("nan"), "lag_ms": 0.0, "at_edge": False}
    t = np.arange(0.0, n / fps, step)
    ev = np.interp(t, tc, e)
    mv = np.interp(t, (np.arange(n) + 0.5) / fps, m)
    lags, c = lag_curve(ev, mv, step, max_ms / 1000.0)
    lag, cp, edge = peak_lag(lags, c)
    return {"corr0": corr0, "corr_peak": cp, "lag_ms": lag * 1000.0, "at_edge": edge}


def envelope_lag(ref, other, sr, fps, max_ms=300, step_ms=5):
    """(lag_ms, corr_peak, at_edge) of `other` against `ref` (two audio tracks of one sample rate); lag > 0: `other` is LATER than `ref`"""
    step = step_ms / 1000.0
    a, _ = fine_env(ref, sr, 1.0 / fps, step)
    b, _ = fine_env(other, sr, 1.0 / fps, step)
    n = min(len(a), len(b))
    if n < 20:
        return 0.0, float("nan"), False
    lags, c = lag_curve(a[:n], b[:n], step, max_ms / 1000.0)
    lag, cp, edge = peak_lag(lags, c)
    return lag * 1000.0, cp, edge


def check_lipsync(ctx: Ctx):
    """LIPSYNC. Needs side["mouth"] and a voice track (ctx.voice; ctx.final stands in when there is no dry voice).
    1. SHAPE: does the mouth follow the loudness of the voice at all? Pearson correlation of the per-frame RMS of the voice with the mouth, taken at the best lag
       within +-sync.lag_range_ms, below sync.lipsync_corr_min: WARN kind="corr" (the old MOUTH_SYNC, 0.5; the old function took lag 0, which is the same for a
       mouth built from the voice, but made a mouth that is merely late look like a wrong mouth, with the timing judged twice. corr0 stays in the data and the INFO line).
       A mouth or voice with no movement at all: WARN kind="flat" (the old MOUTH_FLAT).
    2. TIMING: the lag found by cross-correlating the two envelopes (resolution sync.lag_step_ms, a parabola refines it). offset_ms > 0 = audio early. WARN
       kind="lag" when the audio leads by more than sync.audio_early_ms or lags by more than sync.audio_late_ms. sync.mouth_bias_ms (the known lag of our own mouth
       track) is removed first. When both ctx.voice and ctx.final exist, the delay of the finished audio against the dry voice is added (the mouth was built from
       the dry voice): the judged offset is the audio-vs-picture offset of the file as heard.
    Otherwise an INFO line with the numbers."""
    mouth = ctx.side.get("mouth")
    voice = ctx.voice if ctx.voice is not None else ctx.final
    if not mouth or voice is None or len(voice) == 0:
        return []
    if (ctx.side.get("params") or {}).get("mascot") is False:
        return []
    fps = ctx.fps
    n = min(len(mouth), len(voice) // int(ctx.sr / fps))
    if n < 10:
        return []
    m = np.asarray(mouth[:n], dtype=float)
    env = frame_rms(voice, ctx.sr, fps, n)
    if env.std() < 1e-6 or m.std() < 1e-6:
        return [finding("LIPSYNC", "the mouth does not move or the voice is flat", "WARN", kind="flat", mouth_std=float(m.std()), voice_std=float(env.std()))]
    rng_ms, step_ms = float(_th(ctx, "lag_range_ms")), float(_th(ctx, "lag_step_ms"))
    r = mouth_lag(mouth, voice, ctx.sr, fps, rng_ms, step_ms)
    cmin = float(_th(ctx, "lipsync_corr_min"))
    shape = r["corr_peak"] if np.isfinite(r["corr_peak"]) else r["corr0"]
    if not shape >= cmin:
        return [finding("LIPSYNC", f"the mouth follows the voice only weakly (correlation {shape:.2f} at its best timing, {r['corr0']:.2f} at lag 0; minimum {cmin:.2f})",
                        "WARN", kind="corr", **r)]
    if r["corr_peak"] < float(_th(ctx, "lag_corr_min")):
        return []                                           # no lag worth believing
    bias = float(_th(ctx, "mouth_bias_ms"))
    mouth_part = r["lag_ms"] - bias                       # + = the mouth is later than the dry voice = the sound is early
    mix_part, mix_corr = 0.0, None
    if ctx.voice is not None and ctx.final is not None and len(ctx.final) > ctx.sr:
        lag_f, mix_corr, _e = envelope_lag(ctx.voice, ctx.final, ctx.sr, fps, rng_ms, step_ms)
        if mix_corr >= float(_th(ctx, "mix_lag_corr_min")):
            mix_part = -lag_f                             # the finished audio later than the dry voice = the sound is late
        else:
            mix_corr = None                               # the two tracks do not look alike: no delay is claimed
    off = mouth_part + mix_part
    early, late = float(_th(ctx, "audio_early_ms")), float(_th(ctx, "audio_late_ms"))
    data = dict(r, offset_ms=off, mouth_part_ms=mouth_part, mix_part_ms=mix_part, mix_corr=mix_corr, early_ms=early, late_ms=late, uncertain=True)
    parts = f" (mouth against the dry voice {mouth_part:+.0f} ms" + (f", finished audio against the dry voice {mix_part:+.0f} ms)" if mix_corr is not None else ")")
    edge = f", at the edge of the +-{rng_ms:.0f} ms search so at least that" if r["at_edge"] else ""
    if off > early:
        return [finding("LIPSYNC", f"the sound comes {off:.0f} ms BEFORE the mouth (audio leads; offset {off:+.0f} ms, positive = audio early; window +{early:.0f} ms, "
                        f"EBU R37 / ITU-R BT.1359, uncertain{edge}){parts}", "WARN", kind="lag", **data)]
    if -off > late:
        return [finding("LIPSYNC", f"the sound comes {-off:.0f} ms AFTER the mouth (audio lags; offset {off:+.0f} ms, negative = audio late; window -{late:.0f} ms, "
                        f"EBU R37 / ITU-R BT.1359, uncertain{edge}){parts}", "WARN", kind="lag", **data)]
    return [finding("LIPSYNC", f"the mouth follows the voice: correlation {r['corr_peak']:.2f} at its best timing ({r['corr0']:.2f} at lag 0), offset {off:+.0f} ms (positive = audio early){parts}", "INFO", kind="ok", **data)]


# ---------------------------------------------------------------- container start times

def check_av_offset(ctx: Ctx):
    """AV_OFFSET. offset_ms = (video_start - audio_start) in ms, from side["video"] ("video_start", "audio_start", seconds): > 0 means the audio track
    starts BEFORE the picture (audio early). WARN beyond sync.audio_early_ms / sync.audio_late_ms; otherwise an INFO line. Nothing without both start times."""
    v = ctx.side.get("video") or {}
    vs, au = v.get("video_start"), v.get("audio_start")
    if vs is None or au is None:
        return []
    off = (float(vs) - float(au)) * 1000.0
    early, late = float(_th(ctx, "audio_early_ms")), float(_th(ctx, "audio_late_ms"))
    d = dict(offset_ms=off, video_start=float(vs), audio_start=float(au), early_ms=early, late_ms=late, uncertain=True)
    if off > early:
        return [finding("AV_OFFSET", f"the sound track starts {off:.0f} ms before the picture (offset {off:+.0f} ms, positive = audio early; window +{early:.0f} ms, EBU R37, uncertain)", "WARN", **d)]
    if -off > late:
        return [finding("AV_OFFSET", f"the sound track starts {-off:.0f} ms after the picture (offset {off:+.0f} ms, negative = audio late; window -{late:.0f} ms, EBU R37, uncertain)", "WARN", **d)]
    return [finding("AV_OFFSET", f"sound and picture start together in the container (offset {off:+.0f} ms, positive = audio early)", "INFO", **d)]


DETECTORS = [check_caption_sync, check_lipsync, check_av_offset]


# ---------------------------------------------------------------- fault injection and a synthetic reel (for the lead's matrix and the tests)

def _speech_like(rng, sr, seconds, sentences, t0=0.4):
    """a voice-like track: syllable bursts of a harmonic tone (F0 100-190 Hz, a falling formant-ish slope) with a little aspiration noise, grouped in words
    and sentences. Returns (x float32, scenes) where scenes carry the word times that were drawn"""
    x = np.zeros(int(seconds * sr), dtype=np.float64)
    scenes, t = [], t0
    for s in range(sentences):
        words, start = [], t
        for k in range(int(rng.integers(6, 10))):
            ws = t
            for _syl in range(int(rng.integers(1, 4))):
                d = float(rng.uniform(0.11, 0.19))
                f0 = float(rng.uniform(100, 190))
                tt = np.arange(int(d * sr)) / sr
                burst = np.sin(np.pi * tt / d) ** 1.6
                tone = sum(np.sin(2 * np.pi * f0 * h * tt + rng.uniform(0, 6.28)) / h for h in range(1, 13))
                noise = rng.standard_normal(len(tt)) * 0.15
                seg = burst * (tone + noise) * float(rng.uniform(0.7, 1.0))
                a = int(t * sr)
                x[a:a + len(seg)] += seg[:max(0, len(x) - a)]
                t += d
            words.append([f"slowo{s}{k}", round(ws, 3), round(t, 3)])
            t += float(rng.uniform(0.04, 0.09))
        t -= 0.05
        scenes.append({"text": " ".join(w[0] for w in words), "screen": "", "start": start, "end": t + 0.08, "heard": 1.0, "missed": [], "tail": 0.0, "words": words})
        t += float(rng.uniform(0.3, 0.6))
    return x, scenes, t


def synthetic_case(seed=0, sentences=4, sr=24000, music_db=-26.0):
    """a clean reel for tests and the fault matrix: {side, voice, final, sr}. Voice-like sound at about -20 dB RMS, a steady music bed `music_db` relative
    to the voice in `final`, the sidecar with word times, a mouth built by make.mouth_track from the dry voice, and a faithful re-recognition
    (side["rehear"]: the caption words with +-15 ms of timing noise, standing for a recogniser's own jitter) plus side["video"] start times of 0."""
    from .. import make
    rng = np.random.default_rng(seed)
    x, scenes, t_end = _speech_like(rng, sr, 60.0, sentences)
    total = round(t_end + 0.6, 2)
    x = x[:int(total * sr)]
    voice = (x / (np.sqrt(np.mean(x[np.abs(x) > 1e-3] ** 2)) + 1e-9) * 10 ** (-20 / 20)).astype("float32")
    tt = np.arange(len(voice)) / sr
    bed = sum(np.sin(2 * np.pi * f * tt + p) for f, p in ((110, 0.3), (165, 1.1), (220, 2.0), (330, 0.7))) / 4.0
    rms_v = float(np.sqrt(np.mean(voice[np.abs(voice) > 1e-3] ** 2)))
    final = (voice + (bed / (np.sqrt(np.mean(bed ** 2)) + 1e-9) * rms_v * 10 ** (music_db / 20)) + rng.standard_normal(len(voice)) * 10 ** (-72 / 20)).astype("float32")
    with tempfile.TemporaryDirectory(prefix="syncwav_") as d:
        p = Path(d) / "voice.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((np.clip(voice, -1, 1) * 32767).astype("<i2").tobytes())
        mouth = make.mouth_track(p, scenes, total)
    rehear = [[w[0], round(w[1] + float(rng.normal(0, 0.015)), 3), round(w[2] + float(rng.normal(0, 0.015)), 3)] for s in scenes for w in s["words"]]
    side = {"total": total, "mouth": mouth, "scenes": scenes, "rehear": rehear, "video": {"video_start": 0.0, "audio_start": 0.0}}
    return {"side": side, "voice": voice, "final": final, "sr": sr}


def as_ctx(case, profile=None):
    """a Ctx from a synthetic_case() dict"""
    from .profile import load
    return Ctx(side=case["side"], final=case.get("final"), sr=case.get("sr", 24000), voice=case.get("voice"), profile=profile or load())


def _shift(x, n):
    """x delayed by n samples (n < 0: advanced), same length, zero filled"""
    x = np.asarray(x)
    if n == 0:
        return x.copy()
    z = np.zeros(abs(n), dtype=x.dtype)
    return np.concatenate([z, x[:len(x) - n]]) if n > 0 else np.concatenate([x[-n:], z])


KINDS = {
    "caption_late": "all caption times 150 ms later than the heard words (ms=)",
    "caption_early": "all caption times 150 ms earlier than the heard words (ms=)",
    "caption_scene": "the captions of one scene 300 ms later than the voice (ms=, scene=)",
    "caption_outlier": "one caption word 400 ms out: NOT caught on purpose (ms=, index=), a recogniser's own jitter is as big",
    "caption_drift": "the captions drift away from the voice, 60 ms per second (ms_per_s=)",
    "audio_early": "the audio (dry voice and final) 100 ms before the mouth (ms=)",
    "audio_late": "the audio (dry voice and final) 150 ms after the mouth (ms=)",
    "mix_delay": "only the finished mix 150 ms later than the dry voice (ms=)",
    "mouth_flat": "the mouth never moves",
    "mouth_noise": "the mouth moves, but not with the voice (seeded noise)",
    "av_audio_early": "the container starts the sound 100 ms before the picture (ms=)",
    "av_audio_late": "the container starts the sound 150 ms after the picture (ms=)",
}


def inject(kind, case, **kw):
    """a copy of a synthetic_case() dict with one fault put in; `kind` is a key of KINDS, keyword arguments override the size of the fault.
    Returns a new case; the input is untouched"""
    c = {"side": copy.deepcopy(case["side"]), "voice": None if case.get("voice") is None else case["voice"].copy(),
         "final": None if case.get("final") is None else case["final"].copy(), "sr": case.get("sr", 24000)}
    side, sr = c["side"], c["sr"]
    if "rehear" not in side:
        side["rehear"] = [[w[0], w[1], w[2]] for s in side["scenes"] for w in s["words"]]
    if kind in ("caption_late", "caption_early"):
        ms = kw.get("ms", 150.0) * (1 if kind == "caption_late" else -1)
        for w in side["rehear"]:                                       # the captions are late = the heard words come earlier than the caption times
            w[1], w[2] = w[1] - ms / 1000.0, w[2] - ms / 1000.0
    elif kind == "caption_scene":
        sc = side["scenes"][min(kw.get("scene", 2), len(side["scenes"])) - 1]
        for w in side["rehear"]:
            if sc["start"] - 0.05 <= w[1] <= sc["end"] + 0.05:
                w[1], w[2] = w[1] - kw.get("ms", 300.0) / 1000.0, w[2] - kw.get("ms", 300.0) / 1000.0
    elif kind == "caption_outlier":
        i = kw.get("index", len(side["rehear"]) // 2)
        side["rehear"][i][1] += kw.get("ms", 400.0) / 1000.0
        side["rehear"][i][2] += kw.get("ms", 400.0) / 1000.0
    elif kind == "caption_drift":
        r = kw.get("ms_per_s", 60.0) / 1000.0
        for w in side["rehear"]:
            w[1], w[2] = w[1] + r * w[1], w[2] + r * w[2]
    elif kind in ("audio_early", "audio_late"):
        ms = kw.get("ms", 100.0 if kind == "audio_early" else 150.0) * (-1 if kind == "audio_early" else 1)
        n = int(round(ms / 1000.0 * sr))
        for k in ("voice", "final"):
            if c[k] is not None:
                c[k] = _shift(c[k], n)
    elif kind == "mix_delay":
        if c["final"] is not None:
            c["final"] = _shift(c["final"], int(round(kw.get("ms", 150.0) / 1000.0 * sr)))
    elif kind == "mouth_flat":
        side["mouth"] = [0.0] * len(side["mouth"])
    elif kind == "mouth_noise":
        rng = np.random.default_rng(kw.get("seed", 7))
        raw = np.convolve(rng.random(len(side["mouth"]) + 8), np.ones(5) / 5, mode="same")[4:4 + len(side["mouth"])]
        side["mouth"] = [round(float(v), 3) for v in (raw - raw.min()) / (np.ptp(raw) + 1e-9)]
    elif kind in ("av_audio_early", "av_audio_late"):
        ms = kw.get("ms", 100.0 if kind == "av_audio_early" else 150.0)
        v = side.setdefault("video", {"video_start": 0.0, "audio_start": 0.0})
        v["audio_start"] = v.get("video_start", 0.0) - ms / 1000.0 if kind == "av_audio_early" else v.get("video_start", 0.0) + ms / 1000.0
    else:
        raise KeyError(f"unknown fault kind {kind}; known: {sorted(KINDS)}")
    return c
