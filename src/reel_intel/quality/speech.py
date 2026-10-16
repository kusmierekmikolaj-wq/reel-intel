"""speech.py - detectors for GENERATED speech (the voice-over of a reel): which words came out wrong, pauses in the middle of a sentence, a flat or
jumping pitch, a voice that changes between scenes, a silent switch to another voice engine or another renderer.

Every detector is a plain function  check(ctx) -> list[Finding]  of numbers only (numpy; no network, no GPU, no ffmpeg). What each one reads from the
sidecar (ctx.side) is listed in its docstring; every field beyond the version-1 sidecar is optional and a detector that lacks it says nothing.

Honest limits of this module (also in docs/QA.md):
  - the word-level faults (SUB, DEL, INS) are only as good as the speech recogniser that produced `heard_words`: on real speakers it disagrees with
    human subtitles on 4.5-13% of the words (our own measurement, upper bound), it often misses the first word of a file and invents "thank you" on
    silence. A finding is a candidate to retake, not a verdict;
  - the F0 tracker is validated on synthetic glides and vibrato and on real speech, not on every voice; MONOTONE has no threshold until it is calibrated;
  - "the voice changed between scenes" (DRIFT) depends on an embedding model that is not part of this module; its threshold is a guess;
  - nothing here judges taste: whether a voice is pleasant, a pause is dramatic on purpose, a flat delivery is a style.
"""
import copy
import difflib
import re
import unicodedata

import numpy as np

from .base import Ctx, finding, register

MODULE = "speech"

# ---------------------------------------------------------------- the registry

register("VOICE_SUB", "a script word was said as another word (mispronounced) or not heard as written", "WARN", "respell", MODULE)
register("VOICE_DEL", "a script word was skipped: the voice track does not contain it", "WARN", "retake_scene", MODULE)
register("VOICE_INS", "a word the script does not have: a repeated word or phrase, or an invented one", "WARN", "retake_scene", MODULE)
register("VOICE_PAUSE_INNER", "a silence longer than the limit between two words of one sentence", "WARN", "retake_scene", MODULE)
register("VOICE_MONOTONE", "flat intonation: the pitch of a sentence varies less than the limit (a limit exists only after calibration)", "WARN", "retake_scene", MODULE)
register("VOICE_PITCH_JUMP", "the pitch jumps by more than the limit from one frame to the next and stays there: a voice glitch", "WARN", "retake_scene", MODULE)
register("VOICE_DRIFT", "the voice of one scene sounds like another speaker (speaker embedding far from the median of the reel; threshold is a guess)", "WARN", "retake_scene", MODULE)
register("ENGINE_FALLBACK", "the voice engine that was used is not the one that was asked for (a cloned voice silently replaced by a stock voice is an ERROR)", "ERROR", "rerender", MODULE)
register("RENDER_FALLBACK", "the picture was drawn by the simple PIL renderer although the animated Remotion one was expected (no mascot)", "WARN", "rerender", MODULE)

# Used only when ctx.profile lacks the key (a profile file older than this module). The default profile in quality/profile.py is the source of truth;
# these mirror it and say how sure we are. "measured" = from our own data (n in docs/QA.md), "guess" = no source.
FALLBACK = {
    ("voice", "heard_error"): 0.5,                       # profile
    ("voice", "inner_pause_warn_s"): 0.9,                # a pause between 0.6 and 0.9 s only informs: a human TEDx speaker made one of 0.78 s in 45 s of clean speech
    ("voice", "inner_pause_s"): 0.6,                     # plan (pause > 0.6 s inside a sentence); human TED speakers pause this long too, see docs/QA.md
    ("voice", "sub_near_ratio"): 0.8,                    # guess: a substitution this similar to the script word is a spelling/diacritic variant, INFO only
    ("voice", "drift_cos_min"): 0.80,                    # guess (UNCERTAIN): no calibration of the speaker encoder on our voices yet
    ("voice", "pause_silence_db"): 30.0,                 # guess: a frame this far under the sentence's speech level is silence
    ("voice", "pause_sound_db"): 12.0,                   # guess: a gap whose level is within this of the speech is sound, not a pause
    ("intonation", "f0_std_semitones_min"): None,        # None until calibrated on real speech: then the detector only reports INFO
    ("intonation", "pitch_jump_semitones"): 12,          # profile
    ("intonation", "pitch_jump_frames"): 8,              # measured: 3 frames ("more than 2") gave 6.1 false jumps a minute on 60.7 min of TED talks (4 speakers), 8 gives 0.7, with strength 0.4
    ("intonation", "pitch_jump_min_strength"): 0.7,      # measured with the above: both stretches must be this periodic (autocorrelation peak, mean) or it is noise, not voice
    ("intonation", "pitch_jump_bridge_frames"): 3,       # the tracker loses 1-3 frames at a big jump: unvoiced frames that may sit between the two levels
    ("intonation", "min_voiced_s"): 0.6,                 # a sentence with less voiced sound than this is not judged
}


def _get(ctx, group, key):
    """profile value, or the FALLBACK when the profile has no such key (a present key with value None stays None)"""
    g = (ctx.profile or {}).get(group)
    if isinstance(g, dict) and key in g:
        return g[key]
    return FALLBACK[(group, key)]


# ---------------------------------------------------------------- small shared helpers

def _word_of(h):
    """the text of a heard word that may be given as a string or as [word, t0, t1]"""
    if isinstance(h, (list, tuple)):
        return str(h[0]) if h else ""
    if isinstance(h, dict):
        return str(h.get("word") or h.get("w") or "")
    return str(h)


def _scene_words(sc):
    """[[word, t0, t1], ...] of a scene, or []"""
    return [list(w) for w in (sc.get("words") or []) if len(w) >= 3]


def _span(sc):
    """(start, end) of the speech of a scene in seconds: first word start to last word end, else the scene's own start/end"""
    w = _scene_words(sc)
    if w:
        return float(w[0][1]), float(w[-1][2])
    return float(sc.get("start", 0.0)), float(sc.get("end", 0.0))


def _frame_db(x, sr, frame_s=0.02):
    """loudness in dB of consecutive frames of `x`"""
    hop = max(1, int(sr * frame_s))
    n = len(x) // hop
    if n < 1:
        return np.zeros(0)
    fr = np.asarray(x[:n * hop], dtype=np.float64).reshape(n, hop)
    return 20 * np.log10(np.sqrt(np.mean(fr * fr, axis=1)) + 1e-9)


# ---------------------------------------------------------------- F0 (pitch) tracker, numpy only

F0_MIN, F0_MAX, F0_HOP_S = 70.0, 400.0, 0.01


def f0_times(n, hop_s=F0_HOP_S):
    """centre time in seconds of each of n F0 frames"""
    return np.arange(n) * hop_s


def f0_track(x, sr, fmin=F0_MIN, fmax=F0_MAX, hop_s=F0_HOP_S, voicing_thr=0.45, silence_thr=0.03, octave_cost=0.01,
             octave_jump_cost=1.0, voiced_cost=0.14, max_cand=6, smooth=5, sub_margin=0.12, return_strength=False):
    """fundamental frequency of a voice in Hz, one value per `hop_s` (frame i is centred on i * hop_s), NaN where the sound is not voiced.
    With `return_strength` it returns (f0, strength): the height of the autocorrelation peak of the chosen candidate, 0..1, how periodic the frame is.

    The method is the normalised autocorrelation of Boersma (1993, the one Praat uses), in numpy only: a Hann-windowed frame of three periods of `fmin`,
    its autocorrelation divided by the autocorrelation of the window, peaks with parabolic interpolation, then ONE best path through all frames (dynamic
    programming) that charges `octave_jump_cost` per octave of change and `voiced_cost` per voiced/unvoiced switch and gives a small bonus to higher
    candidates (`octave_cost`). Octave errors are handled twice: a candidate that has a nearly-as-strong partner at 2x or 3x its frequency is dropped as
    a subharmonic, and the path pulls a frame that is better explained as another harmonic back to the pitch of its neighbours. Praat's default jump
    cost (0.35) left 3 sustained jumps of more than an octave a minute on four real TED talks, nearly all of them octave errors of 3-4 frames; 1.0 leaves
    none (measured on 20 minutes). A median of `smooth` frames inside every voiced stretch removes single-frame glitches; a stretch shorter than 3 frames
    is dropped, and a glitch that lasts 3 frames or more survives, because that is what the pitch-jump detector looks for."""
    x = np.asarray(x, dtype=np.float64).ravel()
    win = int(round(3.0 / fmin * sr))
    hop = max(1, int(round(hop_s * sr)))
    n_fr = len(x) // hop + 1
    if len(x) < win or n_fr < 3:
        empty = np.full(max(n_fr, 0), np.nan)
        return (empty, empty.copy()) if return_strength else empty
    tmin, tmax = int(np.floor(sr / fmax)), int(np.ceil(sr / fmin))
    pad = win // 2
    xp = np.concatenate([np.zeros(pad), x - np.mean(x), np.zeros(pad + win + hop)])
    view = np.lib.stride_tricks.sliding_window_view(xp, win)
    hann = np.hanning(win + 2)[1:-1]
    nfft = 1 << int(np.ceil(np.log2(win + tmax + 4)))
    rw = np.fft.irfft(np.abs(np.fft.rfft(hann, nfft)) ** 2, nfft)[:tmax + 3]
    K = max_cand + 1
    freq = np.zeros((n_fr, K))
    stren = np.full((n_fr, K), -1e9)
    rawp = np.zeros((n_fr, K))
    unv = np.zeros(n_fr)
    for c0 in range(0, n_fr, 1024):
        c1 = min(n_fr, c0 + 1024)
        fr = view[c0 * hop:(c1 - 1) * hop + 1:hop][:c1 - c0].astype(np.float64)
        fr = fr - fr.mean(axis=1, keepdims=True)
        peak = np.abs(fr).max(axis=1)
        spec = np.fft.rfft(fr * hann, nfft, axis=1)
        r = np.fft.irfft(spec.real ** 2 + spec.imag ** 2, nfft, axis=1)[:, :tmax + 3]
        r0 = r[:, :1] / rw[0]
        rn = np.where(r0 > 1e-20, (r / (rw + 1e-12)) / np.maximum(r0, 1e-20), 0.0)
        seg = rn[:, tmin - 1:tmax + 2]
        cen = seg[:, 1:-1]
        ismax = (cen > seg[:, :-2]) & (cen >= seg[:, 2:]) & (cen > 0.0)
        score = np.where(ismax, cen, -np.inf)
        kk = min(max_cand, score.shape[1])
        top = np.argpartition(-score, kk - 1, axis=1)[:, :kk]
        rows = np.arange(c1 - c0)[:, None]
        sc = score[rows, top]
        y0, y1, y2 = seg[rows, top], seg[rows, top + 1], seg[rows, top + 2]
        den = y0 - 2 * y1 + y2
        d = np.where(np.abs(den) > 1e-12, 0.5 * (y0 - y2) / np.where(np.abs(den) > 1e-12, den, 1.0), 0.0)
        d = np.clip(d, -1.0, 1.0)
        tau = (tmin + top) + d
        peakv = np.minimum(y1 - 0.25 * (y0 - y2) * d, 1.0)
        f = sr / tau
        good = np.isfinite(sc)
        good &= ~_subharmonic(f, peakv, good, sub_margin)
        st = peakv + octave_cost * np.log2(np.maximum(f, 1e-9) / fmin)
        freq[c0:c1, 1:1 + kk] = np.where(good, f, 0.0)
        stren[c0:c1, 1:1 + kk] = np.where(good, st, -1e9)
        rawp[c0:c1, 1:1 + kk] = np.where(good, peakv, 0.0)
        unv[c0:c1] = peak
    glob_peak = float(np.percentile(unv, 99.5)) if len(unv) > 8 else float(unv.max())       # robust "loudest the voice gets" (one click must not set it)
    inten = unv / max(glob_peak, 1e-12)
    stren[:, 0] = voicing_thr + np.maximum(0.0, 2.0 - inten / (silence_thr / (1.0 + voicing_thr)))
    path = _best_path(freq, stren, voiced_cost, octave_jump_cost)
    rows = np.arange(n_fr)
    f0 = freq[rows, path]
    f0 = np.where(f0 > 0, f0, np.nan)
    f0s = _smooth_voiced(f0, smooth)
    if not return_strength:
        return f0s
    return f0s, np.where(np.isnan(f0s), np.nan, rawp[rows, path])


def _subharmonic(f, r, good, margin):
    """mask of candidates that are a subharmonic: another candidate of the same frame sits at 2x or 3x their frequency and is nearly as strong
    (peak value within `margin`). The autocorrelation of a voiced sound peaks at every multiple of the period; the shortest lag that is
    nearly as strong as the strongest is the period, the others are echoes of it."""
    fj, fk = f[:, :, None], f[:, None, :]                                    # j: the higher candidate, k: the one that may be its subharmonic
    ratio = fj / np.maximum(fk, 1e-9)
    near = (np.abs(ratio - 2.0) < 0.12) | (np.abs(ratio - 3.0) < 0.18)
    strong = r[:, :, None] >= r[:, None, :] - margin
    both = good[:, :, None] & good[:, None, :]
    return np.any(near & strong & both, axis=1)


def _best_path(freq, stren, voiced_cost, octave_jump_cost):
    """the sequence of candidates (one per frame) with the highest total strength minus transition costs (Viterbi)"""
    n, K = freq.shape
    isv = freq > 0
    lf = np.log2(np.where(isv, freq, 1.0))
    best = stren[0].copy()
    back = np.zeros((n, K), dtype=np.int16)
    for t in range(1, n):
        vv = isv[t - 1][:, None] & isv[t][None, :]
        vu = isv[t - 1][:, None] != isv[t][None, :]
        cost = np.where(vv, np.abs(lf[t][None, :] - lf[t - 1][:, None]) * octave_jump_cost, np.where(vu, voiced_cost, 0.0))
        sc = best[:, None] - cost
        back[t] = sc.argmax(axis=0)
        best = sc.max(axis=0) + stren[t]
    path = np.zeros(n, dtype=np.int64)
    path[-1] = int(np.argmax(best))
    for t in range(n - 1, 0, -1):
        path[t - 1] = back[t, path[t]]
    return path


def _smooth_voiced(f0, k):
    """median of k frames (in log-frequency) inside each voiced stretch; stretches shorter than 3 frames are dropped"""
    out = np.full(len(f0), np.nan)
    v = ~np.isnan(f0)
    i, n = 0, len(f0)
    h = k // 2
    while i < n:
        if not v[i]:
            i += 1
            continue
        j = i
        while j < n and v[j]:
            j += 1
        if j - i >= 3:
            lg = np.log2(f0[i:j])
            if k > 1 and len(lg) > 1:
                pad = np.pad(lg, h, mode="edge")
                win = np.lib.stride_tricks.sliding_window_view(pad, 2 * h + 1)
                lg = np.median(win, axis=1)
            out[i:j] = 2.0 ** lg
        i = j
    return out


def f0_semitones(f0, ref=None):
    """F0 in semitones relative to `ref` Hz, by default the median of the voiced frames (the speaker's own median); NaN stays NaN"""
    f0 = np.asarray(f0, dtype=np.float64)
    ok = f0[~np.isnan(f0)]
    if ref is None:
        if not len(ok):
            return np.full(len(f0), np.nan)
        ref = float(np.median(ok))
    with np.errstate(invalid="ignore", divide="ignore"):
        return 12.0 * np.log2(f0 / ref)


# ---------------------------------------------------------------- words: substitution / deletion / insertion

def norm_word(w):
    """a word reduced for comparison: lower case, no punctuation, no diacritics ("że" and "ze" sound alike to every recogniser's language model, and
    the voice cannot be blamed for which one it printed)"""
    w = str(w).lower().replace("ł", "l").replace("đ", "d")
    w = "".join(c for c in unicodedata.normalize("NFKD", w) if not unicodedata.combining(c))
    return re.sub(r"[\W_]", "", w)


def _clean(w):
    return str(w).strip(" \t\n.,;:!?\"'()[]{}…„”’-")


MAX_WILD = 6                     # a written number ("2026") may be spoken as several words


def _align(a, b):
    """word-level edit alignment of the expected tokens `a` to the heard tokens `b` (lists of normalised words).
    Returns [("eq"|"sub"|"del"|"ins", i or None, j or None)]. Cost 1 for sub/del/ins, so the result is a minimal WER alignment; between equal-cost
    alignments the one that pairs the most similar words wins. A token with a digit is a wildcard that may be spoken as 1..MAX_WILD words."""
    n, m = len(a), len(b)
    wild = [any(c.isdigit() for c in t) for t in a]
    sim = {}

    def s(i, j):
        if (i, j) not in sim:
            sim[(i, j)] = difflib.SequenceMatcher(None, a[i], b[j]).ratio() if a[i] and b[j] else 0.0
        return sim[(i, j)]

    INF = 1e9
    D = np.full((n + 1, m + 1), INF)
    D[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        D[i, 0] = i
    mv = {}
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best, how = INF, None
            if a[i - 1] == b[j - 1]:
                best, how = D[i - 1, j - 1], ("eq", 1)
            else:
                c = D[i - 1, j - 1] + 1.0 - 0.01 * s(i - 1, j - 1)
                best, how = c, ("sub", 1)
            if wild[i - 1]:
                for k in range(1, min(MAX_WILD, j) + 1):
                    if D[i - 1, j - k] < best:
                        best, how = D[i - 1, j - k], ("wild", k)
            c = D[i - 1, j] + 1.0
            if c < best:
                best, how = c, ("del", 0)
            c = D[i, j - 1] + 1.0
            if c < best:
                best, how = c, ("ins", 0)
            D[i, j] = best
            mv[(i, j)] = how
    ops, i, j = [], n, m
    while i > 0 or j > 0:
        if i == 0:
            ops.append(("ins", None, j - 1))
            j -= 1
        elif j == 0:
            ops.append(("del", i - 1, None))
            i -= 1
        else:
            kind, k = mv[(i, j)]
            if kind in ("eq", "sub"):
                ops.append((kind, i - 1, j - 1))
                i, j = i - 1, j - 1
            elif kind == "wild":
                for q in range(k):
                    ops.append(("eq", i - 1, j - 1 - q))
                i, j = i - 1, j - k
            elif kind == "del":
                ops.append(("del", i - 1, None))
                i -= 1
            else:
                ops.append(("ins", None, j - 1))
                j -= 1
    return ops[::-1]


def _repeats(ins_idx, hn):
    """which of the inserted heard words (indices into the heard list) are a repeat: equal to a neighbour, or part of a 2-3 word phrase that was
    heard twice in a row. Everything else that was inserted is a hallucination."""
    ins = sorted(ins_idx)
    rep, taken = set(), set()
    runs, cur = [], []
    for j in ins:
        if cur and j == cur[-1] + 1:
            cur.append(j)
        else:
            if cur:
                runs.append(cur)
            cur = [j]
    if cur:
        runs.append(cur)
    for run in runs:
        L = len(run)
        if 2 <= L <= 3:
            s, e = run[0], run[-1]
            ph = hn[s:e + 1]
            if (s - L >= 0 and hn[s - L:s] == ph) or hn[e + 1:e + 1 + L] == ph:
                rep.update(run)
                taken.update(run)
    for j in ins:
        if j in taken:
            continue
        if any(hn[k] == hn[j] for k in range(max(0, j - 2), min(len(hn), j + 3)) if k != j):      # the same word within two words: a stutter around another word
            rep.add(j)
    return rep


def classify_ops(script_words, heard_words, say=None):
    """split what a speech recogniser heard in a voice-over, compared with the script, into the three faults of a text-to-speech system:
        sub   a script word was heard as another word       -> it was mispronounced (or the recogniser got it wrong)
        del   a script word was not heard at all            -> it was skipped
        ins   a heard word the script does not have         -> a repeat (stutter) or a hallucination
    `script_words` and `heard_words` are lists of words (a heard word may also be [word, t0, t1]); `say` is the script's respelling table
    ({"AI": "ej aj"}): a respelled word is expected as what it is respelled to. Words are compared without case, punctuation and diacritics; a script
    word with a digit matches whatever was said, and a heard number written in digits matches the numeral it stands for ("trzydzieści" and 30). Returns
        {"sub": [(script, heard)], "del": [script], "ins": [heard], "repeat": [heard], "hallucination": [heard],     # repeat + hallucination = ins
         "n_script": N, "n_heard": M, "eq": matched script words, "detail": {"sub": [(i, j)], "del": [i], "ins": [j], "repeat": [j]}}
    where i indexes script_words and j indexes the heard words that were not empty after normalisation."""
    say_n = {norm_word(k): v for k, v in (say or {}).items() if norm_word(k)}
    exp, owner = [], []
    for i, w in enumerate(script_words):
        rep = say_n.get(norm_word(w))
        toks = [norm_word(t) for t in str(rep).split()] if rep else [norm_word(w)]
        for t in [t for t in toks if t]:                       # a word that is only punctuation has nothing to say
            exp.append(t)
            owner.append(i)
    h_raw, hn = [], []
    for h in heard_words or []:
        for part in _word_of(h).split():
            n = norm_word(part)
            if n:
                hn.append(n)
                h_raw.append(_clean(part))
    from .. import pronounce
    hn = [next((t for t in exp if n.isdigit() and pronounce.same_number(t, n)), n) for n in hn]       # a recogniser writes "trzydzieści" as 30
    ops = _align(exp, hn)
    per = {}                                                  # script word -> its tokens' outcomes
    ins_idx = []
    for kind, i, j in ops:
        if kind == "ins":
            ins_idx.append(j)
        else:
            per.setdefault(owner[i], []).append((kind, j))
    sub, dele, sub_i, del_i, eq = [], [], [], [], 0
    for i, w in enumerate(script_words):
        outs = per.get(i, [])
        if not outs or any(k == "eq" for k, _ in outs):        # said (one of its tokens matched), or nothing to say
            eq += 1
            continue
        subs = [j for k, j in outs if k == "sub"]
        if subs:
            sub.append((_clean(w), " ".join(h_raw[j] for j in subs)))
            sub_i.append((i, subs[0]))
        else:
            dele.append(_clean(w))
            del_i.append(i)
    rep = _repeats(ins_idx, hn)
    ins_idx.sort()
    return {"sub": sub, "del": dele, "ins": [h_raw[j] for j in ins_idx],
            "repeat": [h_raw[j] for j in ins_idx if j in rep], "hallucination": [h_raw[j] for j in ins_idx if j not in rep],
            "n_script": len(script_words), "n_heard": len(hn), "eq": eq,
            "detail": {"sub": sub_i, "del": del_i, "ins": ins_idx, "repeat": sorted(rep)}}


def _lexicon(side):
    """the respelling table of the script ("say") plus the built-in one of make.py for its language; {} when neither is known"""
    params = side.get("params") or {}
    say = {}
    try:
        from .. import make
        say.update(make.LEXICON.get(params.get("lang") or "pl", {}))
    except Exception:
        pass
    say.update({k: v for k, v in (params.get("say") or {}).items() if isinstance(v, str)})
    return say


def _first_index(script, word):
    n = norm_word(word)
    return next((k for k, w in enumerate(script) if norm_word(w) == n), 0)


def check_words(ctx):
    """VOICE_SUB / VOICE_DEL / VOICE_INS: what the recogniser heard in each scene, against the script.
    Reads per scene: text (else the words), words [[w, t0, t1]] for the time of a finding, heard_words (optional: the words heard, in order, as strings or
    [w, t0, t1]), and as a fallback missed + heard (the version-1 fields: script words not found in what was heard). Side: params.say and params.lang
    (respellings, so that "AI" said as "ej aj" is not a fault). Without heard_words a missed word is reported as a SUB candidate (a skipped and a
    mispronounced word cannot be told apart then). Profile: voice.heard_error, voice.sub_near_ratio.
    Severity: a scene where more than half of the words are wrong is an ERROR, otherwise WARN; a substitution that is nearly the script word (a spelling or
    diacritic variant) is INFO. Cannot judge: whether the recogniser or the voice was wrong, and it often misses the first word of a file."""
    out = []
    say = _lexicon(ctx.side)
    err_below = float(_get(ctx, "voice", "heard_error"))
    near = float(_get(ctx, "voice", "sub_near_ratio"))
    for i, sc in enumerate(ctx.scenes, 1):
        words = _scene_words(sc)
        script = str(sc.get("text") or "").split() or [w[0] for w in words]
        if not script:
            continue
        n = len(script)
        t_at = lambda k: float(words[min(max(k, 0), len(words) - 1)][1]) if words else sc.get("start")
        hw = sc.get("heard_words")
        if hw is None:
            miss = [str(m) for m in (sc.get("missed") or [])]
            if miss:
                ok = sc["heard"] if sc.get("heard") is not None else 1.0 - len(miss) / n
                k0 = _first_index(script, miss[0])
                out.append(finding("VOICE_SUB", f"scene {i}: not heard as written: {', '.join(miss)} ({ok * 100:.0f}% of the words heard); a skipped and a mispronounced word "
                                   "cannot be told apart here. Try another spelling for the voice, e.g. \"say\": {\"" + miss[0] + "\": \"how it sounds\"}",
                                   "ERROR" if ok < err_below else "WARN", scene=i, t=t_at(k0), words=miss, candidate=True, ok=ok))
            continue
        r = classify_ops(script, hw, say)
        det = r["detail"]
        real, minor = [], []
        for (sw, hh), (k, _j) in zip(r["sub"], det["sub"]):
            sim = difflib.SequenceMatcher(None, norm_word(sw), norm_word(hh)).ratio()
            (minor if sim >= near else real).append((sw, hh, k, sim))
        wrong = len(real) + len(r["del"])
        ok = 1.0 - wrong / n
        sev_sd = "ERROR" if ok < err_below else "WARN"
        edge = " (the recogniser often misses the first word of a file: confirm by ear)"
        if real:
            first = any(k == 0 for _s, _h, k, _x in real)
            out.append(finding("VOICE_SUB", f"scene {i}: " + "; ".join(f"'{s}' was said as '{h}'" for s, h, _k, _x in real) + " (mispronounced, or misheard by the recogniser)"
                               + (edge if first else ""), sev_sd, scene=i, t=t_at(real[0][2]), subs=[(s, h) for s, h, _k, _x in real], ok=ok))
        if minor:
            out.append(finding("VOICE_SUB", f"scene {i}: close to the script, probably a spelling variant: " + "; ".join(f"'{s}' as '{h}'" for s, h, _k, _x in minor),
                               "INFO", scene=i, t=t_at(minor[0][2]), subs=[(s, h) for s, h, _k, _x in minor], minor=True))
        if r["del"]:
            first = 0 in det["del"]
            out.append(finding("VOICE_DEL", f"scene {i}: not said at all: {', '.join(repr(d) for d in r['del'])}" + (edge if first else ""), sev_sd, scene=i,
                               t=t_at(det["del"][0]), words=r["del"], ok=ok))
        sev_ins = "ERROR" if (wrong + len(r["ins"])) / n > 1.0 - err_below else "WARN"
        if r["repeat"]:
            out.append(finding("VOICE_INS", f"scene {i}: a word or phrase said twice that the script says once: {' '.join(r['repeat'])}", sev_ins, scene=i,
                               t=t_at(0), words=r["repeat"], kind="repeat"))
        if r["hallucination"]:
            out.append(finding("VOICE_INS", f"scene {i}: words the script does not have: {' '.join(r['hallucination'])} (made up by the voice, or by the recogniser)",
                               sev_ins, scene=i, t=t_at(0), words=r["hallucination"], kind="hallucination"))
    return out


# ---------------------------------------------------------------- pause in the middle of a sentence

def inner_pauses(sc, voice, sr, min_s, sil_db=30.0, sound_db=12.0):
    """pauses longer than `min_s` seconds between two words of one scene: [{"t0", "t1", "dur", "left", "right", "confirmed", "source"}].
    Two sources, merged: (1) a gap between the times of two words; with the voice track it must be quiet (the median 10 ms level of the gap's interior
    more than `sound_db` under the sentence's speech level, else something is sounding there and it is not a pause); (2) a stretch of the voice track
    itself, inside the first-to-last word span, more than `sil_db` under the speech level for `min_s`: that finds a silence the recogniser's word times
    have swallowed into a word. The speech level is the 90th percentile of the 10 ms frame levels of the sentence."""
    w = _scene_words(sc)
    if len(w) < 2:
        return []
    a, b = w[0][1], w[-1][2]
    hop = 0.01
    lvl = None
    if voice is not None and len(voice):
        i0, i1 = int(max(a, 0.0) * sr), min(int(b * sr), len(voice))
        db = _frame_db(voice[i0:i1], sr, hop)
        if len(db) >= 5:
            lvl = (db, float(np.percentile(db, 90)))
    cands = []
    for k in range(len(w) - 1):
        g0, g1 = w[k][2], w[k + 1][1]
        if g1 - g0 <= min_s:
            continue
        confirmed = False
        if lvl is not None:
            db, ref = lvl
            f0, f1 = int(max(0.0, g0 + 0.05 - a) / hop), int(max(0.0, g1 - 0.05 - a) / hop)
            inner = db[f0:f1]
            if len(inner):
                if float(np.median(inner)) > ref - sound_db:
                    continue
                confirmed = True
        cands.append((g0, g1, "words", confirmed))
    if lvl is not None:
        db, ref = lvl
        quiet = db < ref - sil_db
        k, n = 0, len(quiet)
        while k < n:
            if not quiet[k]:
                k += 1
                continue
            e = k
            while e < n and quiet[e]:
                e += 1
            t0, t1 = a + k * hop, a + e * hop
            if (e - k) * hop > min_s and t0 - a > 0.03 and b - t1 > 0.03:
                cands.append((t0, t1, "sound", True))
            k = e
    cands.sort()
    merged = []
    for t0, t1, src, conf in cands:
        if merged and t0 < merged[-1][1]:
            m = merged[-1]
            merged[-1] = (m[0], max(m[1], t1), m[2] + [src], m[3] or conf)
        else:
            merged.append((t0, t1, [src], conf))
    out = []
    for t0, t1, srcs, conf in merged:
        mid = 0.5 * (t0 + t1)
        k = max((q for q in range(len(w)) if w[q][1] <= mid), default=0)
        if k >= len(w) - 1:
            continue
        out.append({"t0": t0, "t1": t1, "dur": t1 - t0, "left": str(w[k][0]), "right": str(w[k + 1][0]), "confirmed": bool(conf), "source": "+".join(sorted(set(srcs)))})
    return out


def check_pause_inner(ctx):
    """VOICE_PAUSE_INNER: a silence longer than profile voice.inner_pause_s between two words of one scene, with the words around it.
    Reads per scene: words [[w, t0, t1]]; ctx.voice (optional: confirms the gap in the sound and finds silences the word times swallowed; without it the
    word times alone decide and the finding says it was not confirmed). Profile: voice.inner_pause_s, voice.pause_silence_db, voice.pause_sound_db.
    Cannot judge: a pause that is a deliberate dramatic beat (human speakers pause this long too, see docs/QA.md for the rate)."""
    out = []
    lim = float(_get(ctx, "voice", "inner_pause_s"))
    sil, snd = float(_get(ctx, "voice", "pause_silence_db")), float(_get(ctx, "voice", "pause_sound_db"))
    for i, sc in enumerate(ctx.scenes, 1):
        for p in inner_pauses(sc, ctx.voice, ctx.sr, lim, sil, snd):
            sev = "WARN" if p["dur"] >= float(_get(ctx, "voice", "inner_pause_warn_s")) else "INFO"
            out.append(finding("VOICE_PAUSE_INNER", f"scene {i}: {p['dur']:.2f} s of silence inside the sentence, between '{p['left']}' and '{p['right']}' (limit {lim:.1f} s)"
                               + ("" if p["confirmed"] else "; from the word times only, not confirmed in the sound"), sev, scene=i, t=p["t0"], **p))
    return out


# ---------------------------------------------------------------- pitch: monotone and jumps

def _f0_of(ctx):
    """(f0 Hz, f0 in semitones re the speaker's median, periodicity strength) of ctx.voice, computed once per Ctx; None when there is no usable voice track"""
    v = ctx.voice
    if v is None or len(v) < int(ctx.sr * 0.3):
        return None
    key = (id(v), len(v), ctx.sr)
    c = ctx.__dict__.get("_speech_f0")
    if c is None or c[0] != key:
        f0, strength = f0_track(v, ctx.sr, return_strength=True)
        c = (key, f0, f0_semitones(f0), strength)
        ctx.__dict__["_speech_f0"] = c
    return c[1], c[2], c[3]


def scene_f0_std(st, a, b, hop_s=F0_HOP_S, min_voiced_s=0.6):
    """standard deviation in semitones of the voiced frames of an F0 track `st` between a and b seconds; None when less than `min_voiced_s` of it is voiced"""
    seg = np.asarray(st[int(a / hop_s):int(b / hop_s) + 1], dtype=np.float64)
    v = seg[~np.isnan(seg)]
    if len(v) * hop_s < min_voiced_s:
        return None
    return float(np.std(v))


def check_monotone(ctx):
    """VOICE_MONOTONE: the pitch of a scene (F0 of ctx.voice in semitones against the speaker's median, voiced frames between its first and last word)
    varies less than profile intonation.f0_std_semitones_min. While that limit is None (not calibrated) nothing is a fault: one INFO line gives the measured
    standard deviation of every scene instead, which is the data to calibrate with. Reads per scene: words (else start/end). Needs ctx.voice.
    Profile: intonation.f0_std_semitones_min, intonation.min_voiced_s. Cannot judge: a flat delivery chosen on purpose; a language or a speaker whose normal
    range is narrow (the limit must come from speech like the voice used); a scene with less than min_voiced_s of voiced sound is skipped."""
    f = _f0_of(ctx)
    if f is None:
        return []
    _f0, st, _r = f
    lim = _get(ctx, "intonation", "f0_std_semitones_min")
    mv = float(_get(ctx, "intonation", "min_voiced_s"))
    stds = []
    for i, sc in enumerate(ctx.scenes, 1):
        a, b = _span(sc)
        s = scene_f0_std(st, a, b, min_voiced_s=mv)
        if s is not None:
            stds.append((i, s))
    if not stds:
        return []
    if lim is None:
        return [finding("VOICE_MONOTONE", "pitch variation per scene (std of F0 in semitones): " + ", ".join(f"{i}: {s:.1f}" for i, s in stds)
                        + "; no limit yet, calibrate intonation.f0_std_semitones_min on real speech", "INFO", std_semitones=dict(stds))]
    return [finding("VOICE_MONOTONE", f"scene {i}: the pitch varies by only {s:.1f} semitones (std; limit {float(lim):.1f}): flat, like reading a list", scene=i, t=_span(ctx.scenes[i - 1])[0],
                    std_semitones=s, limit=float(lim)) for i, s in stds if s < float(lim)]


def pitch_jumps(st, jump=12.0, frames=8, strength=None, min_strength=0.7, bridge=3, hop_s=F0_HOP_S, gap_s=0.4):
    """[(t, from_semitones, to_semitones)] where the F0 track `st` steps by more than `jump` semitones from one stable stretch to the next. A stretch is
    voiced frames in a row with no neighbouring step above `jump`; the two stretches may be `bridge` unvoiced frames apart (the tracker loses a frame or
    two at a big jump), each must last `frames` frames and stay within half the jump of its end value there, and, when `strength` (the periodicity of
    every frame) is given, each must be periodic on average (>= `min_strength`): noise such as applause makes short, weak, voiced-looking stretches
    all over the range and jumps between them are not voice glitches. Events less than `gap_s` apart count once."""
    st = np.asarray(st, dtype=np.float64)
    n = len(st)
    segs, i = [], 0
    while i < n:
        if np.isnan(st[i]):
            i += 1
            continue
        j = i + 1
        while j < n and not np.isnan(st[j]) and abs(st[j] - st[j - 1]) <= jump:
            j += 1
        segs.append((i, j))
        i = j
    out = []
    for (a, b), (c, d) in zip(segs, segs[1:]):
        if c - b > bridge or b - a < frames or d - c < frames or abs(st[c] - st[b - 1]) <= jump:
            continue
        if np.any(np.abs(st[b - frames:b] - st[b - 1]) > jump / 2) or np.any(np.abs(st[c:c + frames] - st[c]) > jump / 2):
            continue
        if strength is not None and (np.nanmean(strength[a:b]) < min_strength or np.nanmean(strength[c:d]) < min_strength):
            continue
        t = c * hop_s
        if out and t - out[-1][0] < gap_s:
            continue
        out.append((t, float(st[b - 1]), float(st[c])))
    return out


def check_pitch_jump(ctx):
    """VOICE_PITCH_JUMP: the F0 of ctx.voice steps by more than profile intonation.pitch_jump_semitones from one stable stretch to the next (see pitch_jumps):
    each stretch lasts profile intonation.pitch_jump_frames voiced frames of 10 ms and is periodic (intonation.pitch_jump_min_strength); intonation.
    pitch_jump_bridge_frames unvoiced frames may lie between them. A voice glitch: the pitch breaks or flips. Needs ctx.voice; reads the scenes only to say
    which scene the time belongs to. The two frame limits were measured on human speech, where "more than 2 frames" fired 6 times a minute (tracker
    artefacts and creaky voice) and 8 frames with the strength gate 0.4 times a minute; how many real glitches of a voice engine they miss is NOT measured.
    Jumps outside 70-400 Hz and jumps of a stretch that is too short or too noisy are invisible."""
    f = _f0_of(ctx)
    if f is None:
        return []
    f0, st, strength = f
    jump = float(_get(ctx, "intonation", "pitch_jump_semitones"))
    frames = int(_get(ctx, "intonation", "pitch_jump_frames"))
    ref = float(np.nanmedian(f0)) if np.any(~np.isnan(f0)) else 1.0
    out = []
    for t, a, b in pitch_jumps(st, jump, frames, strength, float(_get(ctx, "intonation", "pitch_jump_min_strength")), int(_get(ctx, "intonation", "pitch_jump_bridge_frames"))):
        sc = next((i for i, s in enumerate(ctx.scenes, 1) if _span(s)[0] - 0.05 <= t <= _span(s)[1] + 0.05), None)
        fa, fb = ref * 2.0 ** (a / 12.0), ref * 2.0 ** (b / 12.0)
        out.append(finding("VOICE_PITCH_JUMP", (f"scene {sc} at {t:.2f} s" if sc else f"at {t:.2f} s") + f": the pitch jumps {b - a:+.0f} semitones ({fa:.0f} Hz to {fb:.0f} Hz) and stays "
                           f"there for {frames * 10} ms or more (limit {jump:.0f} semitones)", scene=sc, t=t, semitones=b - a, hz_from=float(fa), hz_to=float(fb)))
    return out


# ---------------------------------------------------------------- the speaker changes between scenes

def drift_scores(embs):
    """cosine similarity of every speaker embedding to the median embedding of the reel (the per-dimension median of the unit vectors, normalised again);
    None for a vector that cannot be used (empty, zero, not finite, wrong length). With only two usable embeddings there is no median to speak of: both
    get their cosine to each other."""
    vecs = []
    for e in embs or []:
        try:
            v = np.asarray(e, dtype=np.float64).ravel()
        except Exception:
            v = np.zeros(0)
        vecs.append(v if len(v) and np.all(np.isfinite(v)) and np.linalg.norm(v) > 1e-12 else None)
    dims = [len(v) for v in vecs if v is not None]
    if not dims:
        return [None] * len(vecs)
    d = max(set(dims), key=dims.count)
    ok = [k for k, v in enumerate(vecs) if v is not None and len(v) == d]
    res = [None] * len(vecs)
    if len(ok) < 2:
        return res
    U = np.stack([vecs[k] / np.linalg.norm(vecs[k]) for k in ok])
    if len(ok) == 2:
        c = float(U[0] @ U[1])
        res[ok[0]] = res[ok[1]] = c
        return res
    med = np.median(U, axis=0)
    med = med / (np.linalg.norm(med) + 1e-12)
    for k, c in zip(ok, U @ med):
        res[k] = float(c)
    return res


def check_drift(ctx):
    """VOICE_DRIFT: a scene whose speaker embedding is less similar than profile voice.drift_cos_min (0.80, a GUESS, not calibrated) to the median
    embedding of the reel. Reads side["scene_embeddings"] (one vector per scene, from the voice encoder of the cloning engine); absent -> nothing.
    Cannot judge: the encoder's own noise (a short scene gives a noisier embedding), or whether two voices that differ are still acceptable."""
    embs = ctx.side.get("scene_embeddings")
    if not embs:
        return []
    lim = float(_get(ctx, "voice", "drift_cos_min"))
    cos = drift_scores(embs)
    out = []
    for i, c in enumerate(cos, 1):
        if c is not None and c < lim:
            out.append(finding("VOICE_DRIFT", f"scene {i}: the voice is {c:.2f} similar to the median voice of the reel (limit {lim:.2f}, a guess, not calibrated): it may sound like "
                               "another speaker", scene=i if i <= len(ctx.scenes) else None, cos=c, limit=lim, uncertain=True))
    return out


# ---------------------------------------------------------------- the generator silently used something else

CLONED_ENGINES = ("chatterbox",)                 # engines that speak in the user's own voice; every other engine is a stock voice


def _eng(x):
    return str(x).strip().lower() if x not in (None, "") else None


def check_engine_fallback(ctx):
    """ENGINE_FALLBACK: side["engine_used"] differs from side["engine_wanted"]. ERROR when a cloned voice was wanted and a stock voice spoke (the reel is not
    in the voice the author chose), WARN for any other switch. engine_used may be one name or one name per scene. Both fields absent -> nothing."""
    used, want = ctx.side.get("engine_used"), _eng(ctx.side.get("engine_wanted"))
    if used in (None, "", []) or want is None:
        return []
    per = [_eng(u) for u in used] if isinstance(used, (list, tuple)) else [_eng(used)]
    bad = [(k + 1, u) for k, u in enumerate(per) if u is not None and u != want]
    if not bad:
        return []
    cloned = want in CLONED_ENGINES
    lost_clone = cloned and any(u not in CLONED_ENGINES for _k, u in bad)               # a stock voice instead of the cloned one
    names = sorted({u for _k, u in bad})
    scenes = [k for k, _u in bad] if isinstance(used, (list, tuple)) else None
    where = f" in scene(s) {', '.join(str(k) for k in scenes)}" if scenes else ""
    return [finding("ENGINE_FALLBACK", f"the voice engine '{want}' was wanted but '{', '.join(names)}' spoke{where}"
                    + (": the reel is NOT in your cloned voice (the engine failed or is not installed; see the log of the render)" if lost_clone else ": the voice differs from the one asked for"),
                    "ERROR" if lost_clone else "WARN", scene=scenes[0] if scenes else None, wanted=want, used=names, cloned=cloned, lost_clone=lost_clone)]


def check_render_fallback(ctx):
    """RENDER_FALLBACK: side["renderer"] is "pil" although the animated Remotion renderer was expected (no mascot, plainer picture). Remotion is expected
    unless side["renderer_wanted"], side["simple"], params["simple"] or params["renderer"] says the simple renderer was asked for. renderer absent -> nothing."""
    got = _eng(ctx.side.get("renderer"))
    if got is None:
        return []
    params = ctx.side.get("params") or {}
    want = _eng(ctx.side.get("renderer_wanted")) or _eng(params.get("renderer"))
    if want is None:
        want = "pil" if (ctx.side.get("simple") or params.get("simple")) else "remotion"
    if got == "pil" and want != "pil":
        return [finding("RENDER_FALLBACK", "the picture was drawn by the simple renderer (PIL) because the animated one (Remotion) failed or is not installed: no mascot, "
                        "plainer layout; run `npm install` in studio/ and render again", wanted=want, used=got)]
    return []


DETECTORS = [check_words, check_pause_inner, check_monotone, check_pitch_jump, check_drift, check_engine_fallback, check_render_fallback]


# ---------------------------------------------------------------- synthetic speech and fault injection (for tests and the lead's fault matrix)

_VOCAB = ["to", "jest", "jedno", "zdanie", "testowe", "program", "słucha", "co", "autor", "mówi", "i", "zamienia", "tekst", "czyta", "ekran", "wszystko",
          "ląduje", "plik", "agenta", "napisz", "komentarz", "schemat", "rolki", "konkurencji", "obejrzałeś", "żadnej", "mocny", "temat", "dzisiaj"]


def _syll(w):
    return max(1, len(re.findall(r"[aeiouyąęó]+", w.lower())))


def synth_reel(n_scenes=4, sr=24000, seed=0, words_per_scene=7, intonation_st=2.6, f0_hz=120.0, scene_gap_s=0.28, syllable_s=0.19, word_gap_s=0.05):
    """(side, voice): a synthetic dry voice-over and its sidecar, in the version-2 shape. The voice is a harmonic source with formant-like shaping, a pitch
    contour that wanders around `f0_hz` with a standard deviation of about `intonation_st` semitones (0 = flat), a syllable rhythm, short gaps between the
    words and a faint noise floor; the sidecar carries words with times, heard_words equal to the script, one embedding per scene and the engine fields
    of a clean render. Deterministic for a seed."""
    rng = np.random.default_rng(seed)
    scenes, syl, pos = [], [], 0.4
    for _ in range(n_scenes):
        ws = [str(_VOCAB[int(k)]) for k in rng.integers(0, len(_VOCAB), words_per_scene)]
        t, words = pos, []
        for w in ws:
            d = _syll(w) * syllable_s
            words.append([w, round(t, 4), round(t + d, 4)])
            for q in range(_syll(w)):
                syl.append((t + q * syllable_s, t + (q + 1) * syllable_s, pos))
            t += d + word_gap_s
        end = words[-1][2] + 0.1
        scenes.append({"text": " ".join(ws), "screen": "", "start": pos, "end": end, "heard": 1.0, "missed": [], "tail": 0.1, "words": words, "heard_words": list(ws)})
        pos = end + scene_gap_s
    total = pos + 0.3
    n = int(total * sr)
    tt = np.arange(n) / sr
    cen = np.array([(a + b) / 2 for a, b, _p in syl])
    ref = np.array([p for _a, _b, p in syl])
    st = (intonation_st * np.clip(rng.standard_normal(len(syl)), -2.0, 2.0) if intonation_st > 0 else np.zeros(len(syl))) - 0.5 * (cen - ref)
    contour = np.interp(tt, cen, st) + (0.12 * np.sin(2 * np.pi * 4.7 * tt) if intonation_st <= 0 else 0.0)
    f0 = f0_hz * 2.0 ** (contour / 12.0)
    env = np.zeros(n)
    for (a, b, _p), amp in zip(syl, rng.uniform(0.7, 1.0, len(syl))):
        i0, i1 = int(a * sr), min(int(b * sr), n)
        if i1 > i0:
            env[i0:i1] = np.maximum(env[i0:i1], amp * np.sin(np.pi * np.arange(i1 - i0) / (i1 - i0)) ** 0.7)
    phase = 2 * np.pi * np.cumsum(f0) / sr
    x = np.zeros(n)
    for h in range(1, 31):
        fh = h * f0
        gain = (1.0 + 3.0 * np.exp(-((fh - 700) / 150) ** 2) + 2.0 * np.exp(-((fh - 1200) / 200) ** 2) + 1.5 * np.exp(-((fh - 2500) / 300) ** 2)) / h ** 1.2
        x += gain * np.sin(h * phase) * (fh < 0.45 * sr)
    x = x * env
    x = x / max(np.abs(x).max(), 1e-9) * 0.45 + rng.standard_normal(n) * 10 ** (-66 / 20)
    base = rng.standard_normal(32)
    base /= np.linalg.norm(base)
    embs = []
    for _ in range(n_scenes):
        e = base + 0.2 * rng.standard_normal(32) / np.sqrt(32)
        embs.append((e / np.linalg.norm(e)).tolist())
    side = {"total": total, "mouth": [], "scenes": scenes, "engine_used": "chatterbox", "engine_wanted": "chatterbox", "renderer": "remotion",
            "params": {"lang": "pl"}, "scene_embeddings": embs}
    return side, x.astype(np.float32)


def flatten_pitch(x, sr, f0=None, amount=1.0):
    """(y, warp): `x` with its pitch made (nearly) constant by variable-speed playback, kept at the same length, plus warp(t) that maps a time in `x` to
    the time of the same sound in `y`. The playback speed in every voiced frame is target/F0 (target = the median F0), so the pitch contour is divided
    out; that also bends the time axis a little and moves the formants, which is fine for making a flat test voice out of a real one."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    f0 = f0_track(x, sr) if f0 is None else f0
    v = ~np.isnan(f0)
    if v.sum() < 5:
        return x.astype(np.float32), (lambda t: np.asarray(t, dtype=np.float64))
    target = float(np.nanmedian(f0))
    idx = np.arange(len(f0))
    s = np.ones(len(f0))
    s[v] = (target / f0[v]) ** amount
    s = np.interp(idx, idx[v], s[v])                                    # across unvoiced frames: carry the neighbouring speeds
    s = np.convolve(np.pad(s, 1, mode="edge"), np.ones(3) / 3, mode="valid")
    s = np.clip(s, 0.5, 2.0)
    t_in = idx * F0_HOP_S
    tau = np.concatenate([[0.0], np.cumsum(F0_HOP_S / s[:-1])])           # output time of each input frame
    out_len = tau[-1] + (n / sr - t_in[-1]) / s[-1]
    k = np.arange(int(out_len * sr)) / sr
    src = np.interp(k, tau, t_in) * sr
    y = np.interp(src, np.arange(n), x)
    scale = n / max(len(y), 1)                                           # back to the original length with one constant speed change
    y = np.interp(np.arange(n) * (len(y) - 1) / max(n - 1, 1), np.arange(len(y)), y)
    return y.astype(np.float32), (lambda t: np.interp(np.asarray(t, dtype=np.float64), t_in, tau) * scale)


INJECTIONS = {"sub": "VOICE_SUB", "del": "VOICE_DEL", "ins_repeat": "VOICE_INS", "ins_hallucination": "VOICE_INS", "pause_inner": "VOICE_PAUSE_INNER",
              "monotone": "VOICE_MONOTONE", "pitch_jump": "VOICE_PITCH_JUMP", "drift": "VOICE_DRIFT", "engine_fallback": "ENGINE_FALLBACK",
              "render_fallback": "RENDER_FALLBACK"}


def _heard(sc):
    return list(sc["heard_words"]) if sc.get("heard_words") is not None else str(sc.get("text", "")).split()


def inject(kind, side, voice, sr, scene=2, word=2, seed=0, **kw):
    """(side, voice) copies with ONE known fault put in; `kind` is a key of INJECTIONS (the value is the code that must be reported). `scene` is 1-based,
    `word` a 0-based word index. Works on any sidecar/voice pair, synthetic or real, as long as the scene has words:
      sub / del / ins_repeat / ins_hallucination   edit the scene's heard_words (created from the text when missing)
      pause_inner      `pause_s` (1.2) seconds of near silence spliced into the voice after word `word`; every later time moves
      monotone         the pitch of the scene is flattened (flatten_pitch); the voice keeps its length, the word times follow the warp
      pitch_jump       the voice between `t` and `t + dur_s` (0.25) is replayed `semitones` (14; negative = down) higher in pitch; t defaults to the
                       middle of the longest voiced stretch of the scene
      drift            the scene's embedding is replaced by one at about cosine 0.3 with the others
      engine_fallback  engine_wanted "chatterbox", engine_used "piper"
      render_fallback  renderer "pil" (no renderer_wanted)"""
    if kind not in INJECTIONS:
        raise KeyError(kind)
    side = copy.deepcopy(side)
    v = None if voice is None else np.array(voice, dtype=np.float32)
    rng = np.random.default_rng(seed)
    k = max(1, min(int(scene), len(side["scenes"]))) - 1
    sc = side["scenes"][k] if side.get("scenes") else {}
    if kind in ("sub", "del", "ins_repeat", "ins_hallucination"):
        hw = _heard(sc)
        j = min(int(word), len(hw) - 1)
        if kind == "sub":
            w = str(_word_of(hw[j]))
            hw[j] = (w[::-1] if len(w) > 2 and w[::-1] != w else "xyz") + "ka"
        elif kind == "del":
            del hw[j]
        elif kind == "ins_repeat":
            hw.insert(j, hw[j])
        else:
            hw += ["dziękuję", "za", "oglądanie"]
        sc["heard_words"] = hw
    elif kind == "pause_inner":
        w = sc["words"]
        j = min(int(word), len(w) - 2)
        t = 0.5 * (w[j][2] + w[j + 1][1])
        dt = float(kw.get("pause_s", 1.2))
        i = int(t * sr)
        v = np.concatenate([v[:i], (rng.standard_normal(int(dt * sr)) * 10 ** (-66 / 20)).astype(np.float32), v[i:]])
        for s in side["scenes"]:
            if s["start"] >= t:
                s["start"] += dt
                s["end"] += dt
                for q in s["words"]:
                    q[1] += dt
                    q[2] += dt
            elif s["start"] < t < s["end"]:
                s["end"] += dt
                for q in s["words"]:
                    if q[1] >= t:
                        q[1] += dt
                        q[2] += dt
        side["total"] = side.get("total", 0.0) + dt
    elif kind == "monotone":
        a, b = _span(sc)
        a0, b0 = max(0.0, a - 0.1), min(len(v) / sr, b + 0.1)
        y, warp = flatten_pitch(v[int(a0 * sr):int(b0 * sr)], sr)
        v[int(a0 * sr):int(a0 * sr) + len(y)] = y
        for q in sc["words"]:
            q[1] = float(a0 + warp(q[1] - a0))
            q[2] = float(a0 + warp(q[2] - a0))
    elif kind == "pitch_jump":
        semis = float(kw.get("semitones", 14.0))
        dur = float(kw.get("dur_s", 0.25))
        t = kw.get("t")
        if t is None:
            a, b = _span(sc)
            seg = f0_track(v[int(a * sr):int(b * sr)], sr)
            ok = ~np.isnan(seg)
            best, run, i = (0, 0), 0, 0
            while i < len(ok):
                if ok[i]:
                    j = i
                    while j < len(ok) and ok[j]:
                        j += 1
                    if j - i > best[1] - best[0]:
                        best = (i, j)
                    i = j
                else:
                    i += 1
            t = a + 0.5 * (best[0] + best[1]) * F0_HOP_S - dur / 2
        r = 2.0 ** (semis / 12.0)
        i0, L = int(t * sr), int(dur * sr)
        src = i0 + np.arange(L) * r
        src = np.clip(src, 0, len(v) - 1)
        v[i0:i0 + L] = np.interp(src, np.arange(len(v)), v).astype(np.float32)
    elif kind == "drift":
        embs = side.get("scene_embeddings") or []
        if embs:
            e = np.asarray(embs[k], dtype=np.float64)
            o = rng.standard_normal(len(e))
            o -= (o @ e) / (e @ e) * e                                    # orthogonal to the original
            o /= np.linalg.norm(o)
            side["scene_embeddings"][k] = (0.3 * e / np.linalg.norm(e) + 0.95 * o).tolist()
    elif kind == "engine_fallback":
        side["engine_wanted"], side["engine_used"] = "chatterbox", "piper"
    elif kind == "render_fallback":
        side["renderer"] = "pil"
        side.pop("renderer_wanted", None)
    return side, v


def run_detectors(side, voice, sr, profile=None):
    """every detector of this module on a sidecar and a voice track: the findings as a list"""
    ctx = Ctx(side=side, voice=voice, sr=sr, profile=profile or {})
    out = []
    for d in DETECTORS:
        out += d(ctx)
    return out
