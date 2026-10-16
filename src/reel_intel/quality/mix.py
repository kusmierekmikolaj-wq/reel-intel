"""mix.py - loudness, mastering, mix and noise checks. Pure numpy: every detector is arithmetic on arrays, nothing here renders, decodes or starts a model.

Loudness is ITU-R BS.1770-4 / EBU R128 (K-weighting, 400 ms blocks with 75% overlap, absolute gate -70 LUFS, relative gate -10 LU), loudness range follows
EBU Tech 3342 (3 s windows, relative gate -20 LU, 10th-95th percentile) and the true peak is read from an oversampled signal (4x at 48 kHz, enough
oversampling to reach 192 kHz below that, as ffmpeg's ebur128 does). `loudness_report(x, sr)` was checked against ffmpeg's ebur128 filter (numbers in
tests/test_qa_mix.py and in the lead's report). `master(x, sr, target_lufs, true_peak_max)` is a linear gain plus a look-ahead true-peak limiter: no
compression, so it changes the level and nothing else.

What the detectors read (everything else is optional and the detector is silent when it is missing):
  LOUDNESS, TRUE_PEAK, MIX_CLIP   ctx.final, ctx.sr; optional side["video"]["audio_channels"] (a 1 there means the file is real mono, anything else
                                  means the mono decode in ctx.final stands for a dual-mono stereo file and gets the +3.01 LU a stereo meter shows)
  MIX_PUMPING                     ctx.music (the stem as mixed) and the word times of the scenes (or, without words, the dry voice for a voice activity guess)
  MIX_VOICE_TO_MUSIC              ctx.music, plus ctx.voice (the dry voice); without ctx.voice the voice is estimated as final - music
  MIX_DROPOUT                     ctx.final, the word times of the scenes (or ctx.voice to see where speech should be)
  VOICE_NOISE                     ctx.voice and the word times (the pauses are found from the words)

Thresholds come from ctx.profile (quality/profile.py). The keys this module needs that profile.py does not carry yet are in PROFILE_ADDITIONS (with the way each
number was obtained in PROFILE_NOTES): a detector falls back to them when the profile has no such key. DETECTORS lists the detectors; inject(kind, ...)
puts each fault into sound for the fault-injection matrix.
"""
import math
from functools import lru_cache

import numpy as np

from .base import finding, register
from .profile import DEFAULT as _PROFILE_DEFAULT

MODULE = "mix"

# ---------------------------------------------------------------------------------------------------------------------------------------------------
# fault codes
# ---------------------------------------------------------------------------------------------------------------------------------------------------
register("LOUDNESS", "integrated loudness (LUFS) outside the target of the profile; an INFO line gives the number every time", "WARN", "master", MODULE)
register("TRUE_PEAK", "true peak (oversampled) above the ceiling: the sound distorts after lossy encoding", "ERROR", "master", MODULE)
register("MIX_CLIP", "samples at full scale (clipping): the sound distorts", "ERROR", "master", MODULE)
register("MIX_PUMPING", "the music swells in every speech pause and drops when the voice returns (a ducker that releases too fast)", "WARN", "remix", MODULE)
register("MIX_VOICE_TO_MUSIC", "the music is too close to the voice level while the voice speaks", "WARN", "remix", MODULE)
register("MIX_DROPOUT", "digital silence inside speech in the final mix: part of the sentence is missing", "ERROR", "remix", MODULE)
register("VOICE_NOISE", "hiss under the voice (noise floor in the pauses) or clicks in the voice track", "WARN", "retake_scene", MODULE)

# thresholds this module needs that quality/profile.py does not carry yet (merge into profile.DEFAULT; until then a detector falls back to these)
PROFILE_ADDITIONS = {
    "loudness": {"mono_as_stereo": True},
    "mix": {"clip_level": 0.985, "pump_window_s": 0.1, "pump_min_pause_s": 0.25, "pump_min_events": 2, "pump_min_share": 0.5, "pump_ref_s": 1.0,
            "v2m_min_span_s": 0.6, "dropout_s": 0.3, "dropout_floor_db": -80.0, "dropout_speech_overlap_s": 0.15},
    "voice": {"hiss_error_over_db": 12.0, "hiss_min_frames_s": 0.1, "click_min_jump": 0.04, "click_z": 12.0, "click_flux_z": 6.0, "click_isolation_s": 0.004},
}
PROFILE_NOTES = {
    "loudness.mono_as_stereo": "a mono decode of a dual-mono stereo mp4 is 3.01 LU quieter than the stereo meter on the same file (BS.1770 adds the channel powers); measured on rolka_a.mp4",
    "mix.clip_level": "0.985 is the level reel_qa.mix() already uses",
    "mix.pump_window_s": "100 ms windows, as the task fixes them",
    "mix.pump_min_pause_s": "uncertain: a pause shorter than this is too short to see the music swell (a ducker releasing over 600 ms rises only part of the way in 0.3 s)",
    "mix.pump_min_events": "uncertain: 'repeatedly' read as at least 2 pauses",
    "mix.pump_min_share": "uncertain: at least half of the measurable pauses, so a beat that happens to fall in one pause does not make a finding",
    "mix.pump_ref_s": "how far into the neighbouring speech the reference level is taken (1 s, more than one beat of the bed at 104 bpm)",
    "mix.dropout_s": "0.3 s is the task's definition of a dropout; shorter holes are word gaps",
    "mix.dropout_floor_db": "digital silence = every sample under -80 dBFS (an AAC decode of silence is not always exactly zero)",
    "voice.hiss_error_over_db": "uncertain: ERROR when the noise floor is this far above voice.hiss_db (a floor 12 dB over the limit leaves speech only about 20 dB of margin)",
    "voice.click_min_jump": "measured, see tests/test_qa_mix.py and the calibration numbers in the lead's report",
    "voice.click_z": "measured, see tests/test_qa_mix.py and the calibration numbers in the lead's report",
}

# ---------------------------------------------------------------------------------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------------------------------------------------------------------------------


def _next_pow2(n):
    return 1 << max(0, int(n) - 1).bit_length()


def _fft_filter(x, h):
    """causal FIR  y[n] = sum_k h[k] x[n-k]  (len(y) == len(x)) by overlap-add FFT; exact up to float64 rounding"""
    n, lh = len(x), len(h)
    if n == 0:
        return np.zeros(0)
    if n + lh - 1 <= (1 << 15):
        m = _next_pow2(n + lh - 1)
        return np.fft.irfft(np.fft.rfft(x, m) * np.fft.rfft(h, m), m)[:n]
    nfft = _next_pow2(max(4 * lh, 1 << 14))
    hf = np.fft.rfft(h, nfft)
    blk = nfft - lh + 1
    y = np.zeros(n + lh - 1)
    for s in range(0, n, blk):
        seg = x[s:s + blk]
        yy = np.fft.irfft(np.fft.rfft(seg, nfft) * hf, nfft)
        y[s:s + len(seg) + lh - 1] += yy[:len(seg) + lh - 1]
    return y[:n]


def _as_channels(x):
    """float64 array (channels, samples) from a mono array or a (samples, channels) array"""
    a = np.asarray(x, dtype=np.float64)
    if a.ndim == 1:
        return a[None, :]
    if a.ndim != 2:
        raise ValueError("audio must be 1-D (mono) or 2-D (samples, channels)")
    if a.shape[0] <= 8 < a.shape[1]:                  # given as (channels, samples)
        return a
    return a.T


def _db_amp(a):
    return 20.0 * math.log10(max(float(a), 1e-12))


def _frames_ms(x, win, hop):
    """mean square of every window of `win` samples taken every `hop` samples (cumulative sums: O(n))"""
    n = len(x)
    if n < win:
        return np.zeros(0)
    cs = np.concatenate([[0.0], np.cumsum(np.square(x, dtype=np.float64))])
    starts = np.arange(0, n - win + 1, hop)
    return (cs[starts + win] - cs[starts]) / win


def _to_db(ms):
    return 10.0 * np.log10(np.maximum(ms, 1e-12))


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# BS.1770-4 loudness
# ---------------------------------------------------------------------------------------------------------------------------------------------------


def k_filters(sr):
    """the two K-weighting biquads as ((b, a) shelf, (b, a) high-pass) for any sample rate: the analogue prototype of BS.1770 by bilinear transform.
    At 48 kHz this reproduces the coefficients published in the standard to 1e-13."""
    f0, g, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / sr)
    vh = 10.0 ** (g / 20.0)
    vb = vh ** 0.4996667741545416
    a0 = 1.0 + k / q + k * k
    shelf = ([(vh + vb * k / q + k * k) / a0, 2.0 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0],
             [1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0])
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / sr)
    d = 1.0 + k / q + k * k
    rlb = ([1.0, -2.0, 1.0], [1.0, 2.0 * (k * k - 1.0) / d, (1.0 - k / q + k * k) / d])
    return shelf, rlb


def _biquad(x, b, a):
    y = np.empty(len(x))
    x1 = x2 = y1 = y2 = 0.0
    b0, b1, b2 = b
    a1, a2 = a[1], a[2]
    for i in range(len(x)):
        v = b0 * x[i] + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        y[i] = v
        x2, x1, y2, y1 = x1, x[i], y1, v
    return y


@lru_cache(maxsize=16)
def _k_ir(sr):
    """impulse response of the K-weighting cascade, cut where it has decayed below 1e-12 (the filter is stable, the tail is the 38 Hz high-pass)"""
    shelf, rlb = k_filters(sr)
    r = max(float(np.abs(np.roots(shelf[1])).max()), float(np.abs(np.roots(rlb[1])).max()))
    length = int(min(max(math.log(1e-12) / math.log(min(r, 0.999999)), 2048), 1 << 18))
    imp = np.zeros(length)
    imp[0] = 1.0
    return _biquad(_biquad(imp, *shelf), *rlb)


def k_weighted(x, sr):
    """x (1-D float64) through the K-weighting filter"""
    return _fft_filter(np.asarray(x, dtype=np.float64), _k_ir(int(sr)))


def _oversample_factor(sr):
    return max(4, int(math.ceil(192000.0 / sr)))


@lru_cache(maxsize=16)
def _tp_taps(factor, half=16, beta=8.0):
    """polyphase interpolation filters: windowed sinc with 2*half taps per phase; row p-1 gives the point p/factor of the way between two samples"""
    size = 2 * half * factor + 1
    c = half * factor
    h = np.sinc((np.arange(size) - c) / factor) * np.kaiser(size, beta)
    return np.array([h[np.arange(-half, half) * factor + p + c] for p in range(1, factor)]), half


def _tp_local(x, factor, margin_db=12.0):
    """per-sample local true peak of a 1-D signal: max(|x[n]|, the interpolated points between n and n+1). Only the neighbourhoods of the samples within
    `margin_db` of the largest sample are interpolated (an overshoot of 12 dB over every sample next to it is not something a band-limited signal does)"""
    env = np.abs(x)
    n = len(x)
    if n == 0 or factor < 2:
        return env
    top = float(env.max())
    if top <= 0:
        return env
    cand = np.flatnonzero(env >= top * 10.0 ** (-margin_db / 20.0))
    iv = np.unique(np.concatenate([cand - 1, cand]))
    iv = iv[(iv >= 0) & (iv < n)]
    taps, half = _tp_taps(factor)
    xp = np.concatenate([np.zeros(half), x, np.zeros(half + 1)])
    off = np.arange(half, -half, -1)                                   # x[n - j] for j = -half .. half-1  ->  offsets n + half ... n - half + 1
    for s in range(0, len(iv), 100000):
        ids = iv[s:s + 100000]
        win = xp[(ids[:, None] + half) + off[None, :]]                 # (m, 2*half)
        loc = np.abs(win @ taps.T).max(axis=1)
        env[ids] = np.maximum(env[ids], loc)
    return env


def true_peak(x, sr, oversample=None):
    """true peak in dBTP (None for an empty or silent signal)"""
    ch = _as_channels(x)
    f = oversample or _oversample_factor(sr)
    top = max((float(_tp_local(c, f).max()) if len(c) else 0.0) for c in ch)
    return _db_amp(top) if top > 0 else None


def loudness_report(x, sr, dual_mono=False):
    """BS.1770-4 / EBU R128 numbers for `x` (1-D mono, or (samples, channels); all channels weigh 1.0 as for L, R and C).
    dual_mono=True treats a mono signal as the same sound on two channels (+3.01 LU): that is what the stereo mp4 of a reel contains and what a stereo meter reads.
    Returns {"integrated": LUFS, "momentary_max": LUFS, "short_term_max": LUFS, "lra": LU, "true_peak_db": dBTP, "sample_peak_db": dBFS, "duration": s, "blocks": n,
    "channels": n, "dual_mono": bool}; a value is None when the signal is too short or too quiet to measure it (under 0.4 s for loudness, under 3 s for the range,
    nothing above -70 LUFS)."""
    ch = _as_channels(x)
    n = ch.shape[1]
    rep = {"integrated": None, "momentary_max": None, "short_term_max": None, "lra": None, "true_peak_db": None, "sample_peak_db": None,
           "duration": n / float(sr), "blocks": 0, "channels": int(ch.shape[0]), "dual_mono": bool(dual_mono)}
    if n == 0:
        return rep
    sp = float(np.abs(ch).max())
    rep["sample_peak_db"] = _db_amp(sp) if sp > 0 else None
    rep["true_peak_db"] = true_peak(ch.T if ch.shape[0] > 1 else ch[0], sr)
    power = np.zeros(n)
    for c in ch:
        power += np.square(k_weighted(c, sr))
    if dual_mono and ch.shape[0] == 1:
        power *= 2.0
    cs = np.concatenate([[0.0], np.cumsum(power)])
    blk, hop = int(round(0.4 * sr)), int(round(0.1 * sr))
    if n < blk:
        return rep
    starts = np.arange(0, n - blk + 1, hop)
    e = (cs[starts + blk] - cs[starts]) / blk
    loud = -0.691 + 10.0 * np.log10(np.maximum(e, 1e-30))
    rep["blocks"] = int(len(e))
    ok = loud > -70.0
    if ok.any():
        rep["momentary_max"] = float(loud.max())
        rel = -0.691 + 10.0 * math.log10(float(e[ok].mean())) - 10.0
        sel = ok & (loud > rel)
        rep["integrated"] = -0.691 + 10.0 * math.log10(float(e[sel].mean()))
    win3 = int(round(3.0 * sr))
    if n >= win3 and ok.any():
        st = np.arange(0, n - win3 + 1, hop)
        es = (cs[st + win3] - cs[st]) / win3
        ls = -0.691 + 10.0 * np.log10(np.maximum(es, 1e-30))
        oks = ls > -70.0
        if oks.any():
            rep["short_term_max"] = float(ls.max())
            rel = -0.691 + 10.0 * math.log10(float(es[oks].mean())) - 20.0
            vals = ls[oks & (ls > rel)]
            if len(vals):
                rep["lra"] = float(np.percentile(vals, 95) - np.percentile(vals, 10))
    return rep


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# mastering: a linear gain to the target, then a look-ahead true-peak limiter
# ---------------------------------------------------------------------------------------------------------------------------------------------------


def _slide_min(a, radius):
    """out[k] = min(a[k-radius .. k+radius]) for an array whose values are all <= 1 (outside the array counts as 1); O(n), van Herk / Gil-Werman"""
    n, w = len(a), 2 * radius + 1
    if radius <= 0 or n == 0:
        return a.copy()
    total = n + w - 1
    blocks = -(-total // w)
    pad = np.ones(blocks * w)
    pad[radius:radius + n] = a
    b = pad.reshape(blocks, w)
    pre = np.minimum.accumulate(b, axis=1).ravel()
    suf = np.minimum.accumulate(b[:, ::-1], axis=1)[:, ::-1].ravel()
    return np.minimum(suf[:n], pre[w - 1:w - 1 + n])


def _limiter_gain(req, sr, lookahead_ms, release_ms):
    """smooth gain curve g <= req everywhere: a centred sliding minimum smoothed by a Hann window of the same width (so the smoothed curve cannot rise
    above the requirement at any sample), then an exponential release that only ever lowers the gain further"""
    la = max(1, int(round(sr * lookahead_ms / 1000.0)))
    m = _slide_min(req, la)
    w = np.hanning(2 * la + 3)[1:-1]
    w /= w.sum()
    padded = np.concatenate([np.full(la, m[0]), m, np.full(la, m[-1])])
    s = np.convolve(padded, w, mode="valid")[:len(m)]
    deficit = 1.0 - s
    tau = max(1.0, sr * release_ms / 1000.0 / 3.0)
    tail = int(6 * tau)
    out = deficit.copy()
    peaks = np.flatnonzero((deficit[1:-1] >= deficit[:-2]) & (deficit[1:-1] > deficit[2:]) & (deficit[1:-1] > 1e-7)) + 1
    if len(deficit) > 1 and deficit[-1] > 1e-7 and deficit[-1] >= deficit[-2]:
        peaks = np.append(peaks, len(deficit) - 1)
    decay = np.exp(-np.arange(tail) / tau)
    for k in peaks:
        seg = out[k:k + tail]
        np.maximum(seg, deficit[k] * decay[:len(seg)], out=seg)
    return 1.0 - out


def limit_true_peak(x, sr, ceiling_db=-1.0, lookahead_ms=5.0, release_ms=60.0):
    """look-ahead limiter keeping the true peak of `x` (1-D, or (samples, channels): linked, one gain for all) under `ceiling_db`. Returns (y, max_reduction_db).
    The gain is a smooth curve (about 5 ms down, 60 ms back up) computed from the oversampled peaks, so nothing is clipped and nothing is compressed that
    does not reach the ceiling."""
    a = np.asarray(x, dtype=np.float64)
    ch = _as_channels(a)
    f = _oversample_factor(sr)
    ceil = 10.0 ** (ceiling_db / 20.0)
    env = np.max([_tp_local(c, f) for c in ch], axis=0)
    req = np.minimum(1.0, ceil / np.maximum(env, 1e-12))
    if req.min() >= 1.0:
        return a, 0.0
    g = _limiter_gain(req, sr, lookahead_ms, release_ms)
    if a.ndim == 1:
        y = a * g
    else:
        y = a * (g[None, :] if a.shape == ch.shape else g[:, None])
    return y, float(-20.0 * math.log10(max(float(g.min()), 1e-12)))


def master(x, sr, target_lufs=-16.0, true_peak_max=-1.0, dual_mono=False, max_gain_db=24.0, min_plr_db=9.0, lookahead_ms=5.0, release_ms=60.0):
    """loudness mastering for the repair action "master": a LINEAR gain that brings the integrated loudness (BS.1770-4) to `target_lufs`, then a look-ahead
    true-peak limiter so the true peak stays at or under `true_peak_max` dBTP. No compression, no EQ. The gain is solved with the limiter in the loop, so the
    loudness is measured on what comes out; applying master twice therefore changes nothing (the second pass finds the target met and the peaks under the ceiling).
    The limiter is not allowed to squeeze the peak-to-loudness ratio below `min_plr_db` (ceiling minus loudness): a target louder than `true_peak_max - min_plr_db`
    is lowered to that figure and info["reached"] is False, because a limiter pushed harder would distort rather than get louder.
    x: 1-D mono or (samples, channels) float array; dual_mono as in loudness_report. Returns (y float32 with the shape of x, info) where
    info = {"before": loudness_report, "after": loudness_report, "gain_db", "max_reduction_db", "limited": bool, "reached": bool, "iterations": n, "reason": str or None}."""
    a = np.asarray(x, dtype=np.float64)
    before = loudness_report(a, sr, dual_mono)
    info = {"before": before, "after": before, "gain_db": 0.0, "max_reduction_db": 0.0, "limited": False, "reached": False, "iterations": 0, "reason": None}
    if before["integrated"] is None:
        info["reason"] = "nothing above -70 LUFS to measure (silent or shorter than 0.4 s): left as it was"
        return a.astype("float32"), info
    ceiling = float(true_peak_max)
    goal = min(float(target_lufs), ceiling - min_plr_db)                  # the loudest this ceiling allows without squeezing the peaks flat
    lo, hi = -max_gain_db, max_gain_db
    g = min(max(goal - before["integrated"], lo), hi)
    prev = None
    y, red, rep = a, 0.0, before
    for it in range(1, 13):
        y, red = limit_true_peak(a * 10.0 ** (g / 20.0), sr, ceiling - 0.05, lookahead_ms, release_ms)
        rep = loudness_report(y, sr, dual_mono)
        info["iterations"] = it
        got = rep["integrated"] if rep["integrated"] is not None else -70.0
        err = goal - got
        if abs(err) <= 0.02 or (g >= hi - 1e-9 and err > 0) or (g <= lo + 1e-9 and err < 0):
            break
        slope = 1.0
        if prev is not None and abs(g - prev[0]) > 1e-6:
            slope = min(1.0, max(0.2, (got - prev[1]) / (g - prev[0])))
        prev = (g, got)
        g = min(max(g + err / slope, lo), hi)
    for _ in range(3):                                                    # the limiter works on an interpolated estimate: check, and tighten if a peak is still over
        tp = rep["true_peak_db"]
        if tp is None or tp <= ceiling:
            break
        y, extra = limit_true_peak(y, sr, ceiling - 0.05 - (tp - ceiling), lookahead_ms, release_ms)
        red = max(red, extra)
        rep = loudness_report(y, sr, dual_mono)
    got = rep["integrated"]
    info.update(after=rep, gain_db=float(g), max_reduction_db=float(red), limited=red > 0.05, reached=got is not None and abs(got - target_lufs) <= 0.1)
    if not info["reached"]:
        info["reason"] = (f"target {target_lufs:.1f} LUFS not reached: {got:.1f} LUFS is the most a {ceiling:.1f} dBTP ceiling allows while keeping {min_plr_db:.0f} dB between "
                          "peak and loudness (a harder limiter would distort)") if got is not None else "result is silent"
    return y.astype("float32"), info


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# what the detectors share: thresholds, word times, speech spans
# ---------------------------------------------------------------------------------------------------------------------------------------------------


def _thr(ctx, section, key):
    """a threshold: the profile of the context, else PROFILE_ADDITIONS (keys profile.py does not carry yet), else the profile defaults"""
    for src in (getattr(ctx, "profile", None) or {}, PROFILE_ADDITIONS, _PROFILE_DEFAULT):
        sec = src.get(section) if isinstance(src, dict) else None
        if isinstance(sec, dict) and sec.get(key) is not None:
            return sec[key]
    raise KeyError(f"no threshold {section}.{key} in the profile or in mix.PROFILE_ADDITIONS")


def _words(ctx):
    """[(t0, t1, scene_no)] of every word of every scene, in time order (scenes without word times are skipped)"""
    out = []
    for i, s in enumerate(ctx.scenes, 1):
        for w in s.get("words") or []:
            try:
                out.append((float(w[1]), float(w[2]), i))
            except (TypeError, ValueError, IndexError):
                continue
    out.sort()
    return out


def _merge(spans, join):
    """[(t0, t1)] with neighbours closer than `join` seconds merged"""
    out = []
    for a, b in sorted(spans):
        if out and a - out[-1][1] < join:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _frame_db(x, sr, ms=20):
    n = max(1, int(sr * ms / 1000.0))
    return _to_db(_frames_ms(np.asarray(x, dtype=np.float64), n, n)), n


def _vad_spans(x, sr, rel_db=28.0, floor_db=-60.0, join_s=0.15, min_s=0.1):
    """where a track is speaking, from its own level: frames within `rel_db` of the 95th percentile (and above `floor_db`), gaps under `join_s` closed,
    stretches under `min_s` dropped. Only used when the scenes carry no word times."""
    db, n = _frame_db(x, sr)
    if len(db) < 5:
        return []
    on = db > max(float(np.percentile(db, 95)) - rel_db, floor_db)
    idx = np.flatnonzero(on)
    if not len(idx):
        return []
    spans = _merge([(i * n / sr, (i + 1) * n / sr) for i in idx], join_s)
    return [(a, b) for a, b in spans if b - a >= min_s]


def _scene_spans(ctx):
    """one (t0, t1, scene_no) per scene with words: first word start to last word end"""
    out = []
    for i, s in enumerate(ctx.scenes, 1):
        w = [x for x in (s.get("words") or []) if len(x) >= 3]
        if w:
            out.append((float(w[0][1]), float(w[-1][2]), i))
    return out


def _speech_phrases(ctx, join):
    """stretches of speech with gaps shorter than `join` closed: from the words, else from the dry voice's own level, else []"""
    words = _words(ctx)
    if words:
        return _merge([(a, b) for a, b, _i in words], join)
    if ctx.voice is not None:
        return _merge(_vad_spans(ctx.voice, ctx.sr), join)
    return []


def _cached(ctx, key, arrays, fn):
    """compute fn() once per context and inputs: the result is kept on the context together with the arrays it was made from (identity is checked, so a
    replaced array is never mistaken for the old one)"""
    store = ctx.__dict__.setdefault("_mix_cache", {})
    hit = store.get(key)
    if hit is not None and len(hit[0]) == len(arrays) and all(a is b for a, b in zip(hit[0], arrays)):
        return hit[1]
    val = fn()
    store[key] = (tuple(arrays), val)
    return val


def _dual_mono(ctx):
    """does ctx.final (a mono array) stand for a dual-mono stereo file? side["video"]["audio_channels"] decides when the sidecar has it, else the profile"""
    video = ctx.side.get("video") if isinstance(ctx.side, dict) else None
    ch = video.get("audio_channels", video.get("channels")) if isinstance(video, dict) else None
    if ch is not None:
        try:
            return int(ch) != 1
        except (TypeError, ValueError):
            pass
    return bool(_thr(ctx, "loudness", "mono_as_stereo"))


def _final_report(ctx):
    final = ctx.final
    dual = _dual_mono(ctx) and np.ndim(final) == 1
    return _cached(ctx, ("loud", ctx.sr, dual), (final,), lambda: loudness_report(final, ctx.sr, dual)), dual


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# detectors: loudness, peak, clipping
# ---------------------------------------------------------------------------------------------------------------------------------------------------


def check_loudness(ctx):
    """LOUDNESS: integrated loudness against profile loudness.target_lufs +- tolerance (WARN, repair "master"), and an INFO line with the numbers every time.
    The target is uncertain (sources say -10 to -16; the reference reels of our own data sit near -14 as a stereo meter reads them), so the INFO line says so."""
    if ctx.final is None or not len(ctx.final):
        return []
    rep, dual = _final_report(ctx)
    target, tol = float(_thr(ctx, "loudness", "target_lufs")), float(_thr(ctx, "loudness", "tolerance"))
    ceiling = float(_thr(ctx, "loudness", "true_peak_max"))
    uncertain = bool(_thr(ctx, "loudness", "uncertain"))
    i = rep["integrated"]
    if i is None:
        if rep["duration"] < 0.4:
            return [finding("LOUDNESS", f"the sound is only {rep['duration']:.2f} s long: loudness cannot be measured (it needs 0.4 s)", sev="INFO", **rep)]
        return [finding("LOUDNESS", "nothing above -70 LUFS in the final mix: it is silent", sev="ERROR", **rep)]
    parts = [f"integrated {i:.1f} LUFS" + (" (read as dual-mono stereo, +3 LU over the mono decode)" if dual else "")]
    if rep["lra"] is not None:
        parts.append(f"range {rep['lra']:.1f} LU")
    if rep["true_peak_db"] is not None:
        parts.append(f"true peak {rep['true_peak_db']:.1f} dBTP")
    if rep["momentary_max"] is not None:
        parts.append(f"loudest 400 ms {rep['momentary_max']:.1f} LUFS")
    note = f"; target {target:.1f} +- {tol:.1f}" + (" (uncertain: sources differ, calibrate on the reference account)" if uncertain else "")
    out = [finding("LOUDNESS", ", ".join(parts) + note, sev="INFO", target=target, tolerance=tol, **rep)]
    if abs(i - target) > tol:
        word = "quiet" if i < target else "loud"
        out.append(finding("LOUDNESS", f"integrated loudness {i:.1f} LUFS is {abs(i - target):.1f} LU too {word} for the target {target:.1f} +- {tol:.1f} LUFS",
                           master={"target_lufs": target, "true_peak_max": ceiling, "dual_mono": dual}, integrated=i, target=target, tolerance=tol,
                           gain_db=target - i, dual_mono=dual))
    return out


def check_true_peak(ctx):
    """TRUE_PEAK: the oversampled peak above profile loudness.true_peak_max (ERROR, repair "master"). It is measured on ctx.final at ctx.sr: a decode at 24 kHz
    has lost everything above 12 kHz and the AAC encode of the delivered file can add a few tenths of a dB, so a peak within 0.5 dB of the ceiling is not safe."""
    if ctx.final is None or not len(ctx.final):
        return []
    rep, dual = _final_report(ctx)
    tp = rep["true_peak_db"]
    ceiling = float(_thr(ctx, "loudness", "true_peak_max"))
    if tp is None or tp <= ceiling:
        return []
    return [finding("TRUE_PEAK", f"true peak {tp:.2f} dBTP is {tp - ceiling:.2f} dB over the {ceiling:.1f} dBTP ceiling (measured at {ctx.sr / 1000:g} kHz; "
                    "encoding to AAC can raise it further)", true_peak_db=tp, ceiling=ceiling, over_db=tp - ceiling,
                    master={"target_lufs": None, "true_peak_max": ceiling, "dual_mono": dual})]


def check_clip(ctx):
    """MIX_CLIP: the share of samples above profile mix.clip_level (0.985) over profile mix.clip_share (ERROR, repair "master")"""
    if ctx.final is None or not len(ctx.final):
        return []
    x = np.abs(np.asarray(ctx.final, dtype=np.float64))
    level, limit = float(_thr(ctx, "mix", "clip_level")), float(_thr(ctx, "mix", "clip_share"))
    over = x > level
    share = float(over.mean())
    if share <= limit:
        return []
    first = np.flatnonzero(over)[:5] / ctx.sr
    return [finding("MIX_CLIP", f"{share * 100:.2f}% of the samples ({int(over.sum())}) are at full scale (above {level}): the sound distorts; first at "
                    + ", ".join(f"{t:.2f} s" for t in first), t=float(first[0]), share=share, count=int(over.sum()), level=level, limit=limit,
                    master={"target_lufs": None, "true_peak_max": float(_thr(ctx, "loudness", "true_peak_max")), "dual_mono": _dual_mono(ctx)})]


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# detectors: music against voice
# ---------------------------------------------------------------------------------------------------------------------------------------------------


def check_pumping(ctx):
    """MIX_PUMPING: needs ctx.music (the stem as it was mixed). Its level is measured in 100 ms windows (50% overlap); for every speech pause long enough to see
    it (profile mix.pump_min_pause_s) the energy mean of the windows inside the pause is compared with the energy mean of the windows in the speech next to it
    (up to mix.pump_ref_s on each side). A rise over profile mix.pumping_db_max in at least mix.pump_min_events pauses and in at least mix.pump_min_share of
    the measurable ones is pumping (WARN, repair "remix"); a single pause that rises is an INFO line. Pauses come from the word times of the scenes (or the dry
    voice's own level). Cannot tell a ducker from music that happens to swell in the pauses; a stem with strong dynamics of its own can give false rises, which
    is what the repeat and share rules are for."""
    if ctx.music is None or not len(ctx.music):
        return []
    sr = ctx.sr
    win_s = float(_thr(ctx, "mix", "pump_window_s"))
    min_pause, ref_s = float(_thr(ctx, "mix", "pump_min_pause_s")), float(_thr(ctx, "mix", "pump_ref_s"))
    limit = float(_thr(ctx, "mix", "pumping_db_max"))
    win = max(1, int(round(win_s * sr)))
    hop = max(1, win // 2)
    ms = _frames_ms(np.asarray(ctx.music, dtype=np.float64), win, hop)
    phrases = _speech_phrases(ctx, min_pause)
    if len(ms) < 4 or len(phrases) < 2:
        return []
    t0 = np.arange(len(ms)) * hop / sr
    t1 = t0 + win / sr
    rises = []
    for (a0, a1), (b0, b1) in zip(phrases, phrases[1:]):
        p0, p1 = a1 + 0.05, b0 - 0.02
        inside = (t0 >= p0 - 1e-9) & (t1 <= p1 + 1e-9)
        near = ((t0 >= max(a0, a1 - ref_s)) & (t1 <= a1 - 0.03)) | ((t0 >= b0 + 0.03) & (t1 <= min(b1, b0 + ref_s)))
        if inside.sum() < 2 or near.sum() < 2:
            continue
        lp, lr = float(_to_db(ms[inside].mean())), float(_to_db(ms[near].mean()))
        if lr < -80.0:                                              # no music there
            continue
        rises.append((a1, lp - lr))
    if not rises:
        return []
    events = [(t, r) for t, r in rises if r > limit]
    share = len(events) / len(rises)
    detail = ", ".join(f"{t:.1f} s +{r:.1f} dB" for t, r in events[:6])
    data = dict(rises=[(round(t, 2), round(r, 2)) for t, r in rises], events=len(events), pauses=len(rises), share=share, limit=limit)
    if len(events) >= int(_thr(ctx, "mix", "pump_min_events")) and share >= float(_thr(ctx, "mix", "pump_min_share")):
        worst = max(r for _t, r in events)
        return [finding("MIX_PUMPING", f"the music swells in {len(events)} of {len(rises)} speech pauses (by up to {worst:.1f} dB, limit {limit:.1f} dB) and drops when the voice "
                        f"returns: {detail}; the ducker lets go too fast, keep the music level steady through the speech", t=events[0][0], worst_db=worst, **data)]
    if events:
        return [finding("MIX_PUMPING", f"the music rises {events[0][1]:.1f} dB in one pause ({detail}), not repeated ({len(events)} of {len(rises)}): not pumping", sev="INFO",
                        t=events[0][0], **data)]
    return []


def _voice_for_ratio(ctx):
    """(voice samples, music samples, source name) of equal length, or None; the dry voice when there is one, else final - music"""
    music = np.asarray(ctx.music, dtype=np.float64)
    if ctx.voice is not None:
        voice = np.asarray(ctx.voice, dtype=np.float64)
        src = "dry voice"
    elif ctx.final is not None:
        final = np.asarray(ctx.final, dtype=np.float64)
        k = min(len(final), len(music))
        voice = final[:k] - music[:k]
        src = "final minus music"
    else:
        return None
    n = min(len(voice), len(music))
    return voice[:n], music[:n], src


def check_voice_to_music(ctx):
    """MIX_VOICE_TO_MUSIC: needs ctx.music (absent: nothing). In every scene the level of the voice minus the level of the music stem, taken over the same 20 ms
    frames, the frames where the voice speaks (within 20 dB of the scene's 90th percentile), energy mean. Below profile mix.voice_to_music_db_min is a WARN
    (repair "remix", data["needed_db"] says by how much the music must go down); an INFO line gives the median. The voice is ctx.voice, or final - music when
    there is no dry voice (the master gain scales both, so the ratio survives it). A scene needs mix.v2m_min_span_s of speech to be judged. The profile threshold
    (14 dB) is a rule of thumb from mixing guides, not measured: it says what is clearly intelligible, not what sounds good."""
    if ctx.music is None or not len(ctx.music):
        return []
    got = _voice_for_ratio(ctx)
    if got is None:
        return []
    voice, music, src = got
    sr = ctx.sr
    spans = [(a, b, i) for a, b, i in _scene_spans(ctx)]
    if not spans:
        spans = [(a, b, None) for a, b in _speech_phrases(ctx, 0.5)]
    fr = max(1, int(round(0.02 * sr)))
    vms, mms = _frames_ms(voice, fr, fr), _frames_ms(music, fr, fr)
    vdb = _to_db(vms)
    need = float(_thr(ctx, "mix", "voice_to_music_db_min"))
    min_span = float(_thr(ctx, "mix", "v2m_min_span_s"))
    out, ratios = [], []
    for a, b, i in spans:
        if b - a < min_span:
            continue
        lo, hi = int(a / 0.02), min(int(b / 0.02), len(vms), len(mms))
        if hi - lo < 10:
            continue
        seg = vdb[lo:hi]
        act = seg >= float(np.percentile(seg, 90)) - 20.0
        if act.sum() < 5:
            continue
        v_ms, m_ms = float(vms[lo:hi][act].mean()), float(mms[lo:hi][act].mean())
        if m_ms < 1e-12:                                            # no music in this stretch
            continue
        ratio = float(_to_db(v_ms) - _to_db(m_ms))
        ratios.append(ratio)
        if ratio < need:
            where = f"scene {i}" if i else f"speech at {a:.1f} s"
            out.append(finding("MIX_VOICE_TO_MUSIC", f"{where}: the voice is only {ratio:.1f} dB above the music (at least {need:.0f} dB wanted): "
                               f"lower the music by about {need - ratio:.1f} dB", scene=i, t=a, ratio_db=ratio, needed_db=need - ratio, min_db=need, voice_source=src))
    if ratios:
        out.append(finding("MIX_VOICE_TO_MUSIC", f"voice {float(np.median(ratios)):.1f} dB above the music (median of {len(ratios)} scenes, lowest {min(ratios):.1f} dB, wanted "
                           f"at least {need:.0f} dB; voice taken from the {src})", sev="INFO", median_db=float(np.median(ratios)), lowest_db=float(min(ratios)), min_db=need,
                           voice_source=src))
    return out


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# detector: dropouts
# ---------------------------------------------------------------------------------------------------------------------------------------------------


def _runs(mask, min_len):
    """[(start, end)] sample ranges where `mask` is True for at least `min_len` samples in a row"""
    if not len(mask):
        return []
    m = np.concatenate([[0], mask.astype(np.int8), [0]])
    d = np.diff(m)
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    return [(int(s), int(e)) for s, e in zip(starts, ends) if e - s >= min_len]


def check_dropout(ctx):
    """MIX_DROPOUT: digital silence (every sample under profile mix.dropout_floor_db) for at least mix.dropout_s inside speech of the final mix (ERROR,
    repair "remix"). Where the speech is: the dry voice's level when ctx.voice exists (the run must overlap at least mix.dropout_speech_overlap_s of
    speaking voice, so a comma pause that is silent in the voice track too is no dropout; data["voice_silent"] is False for a dropout the mix made); with no
    dry voice the word times of the scenes are used and the finding is only a WARN, because TTS pauses at commas are digital silence in a reel without music and
    the word times do not say whether the voice was speaking there."""
    if ctx.final is None or not len(ctx.final):
        return []
    sr = ctx.sr
    x = np.asarray(ctx.final, dtype=np.float64)
    if x.ndim == 2:
        x = np.abs(x).max(axis=1 if x.shape[1] <= 8 else 0)
    floor = 10.0 ** (float(_thr(ctx, "mix", "dropout_floor_db")) / 20.0)
    min_s, overlap = float(_thr(ctx, "mix", "dropout_s")), float(_thr(ctx, "mix", "dropout_speech_overlap_s"))
    runs = _runs(np.abs(x) <= floor, int(round(min_s * sr)))
    if not runs:
        return []
    words = _words(ctx)
    voice_spans = _vad_spans(ctx.voice, sr) if ctx.voice is not None else None
    out = []
    for s, e in runs:
        a, b = s / sr, e / sr
        if voice_spans is not None:
            talk = sum(max(0.0, min(b, v1) - max(a, v0)) for v0, v1 in voice_spans)
            sure = True
        else:
            talk = sum(max(0.0, min(b, w1) - max(a, w0)) for w0, w1, _i in words)
            sure = False
        if talk < overlap:
            continue
        scene = next((i for w0, w1, i in words if w0 <= (a + b) / 2 <= w1), None)
        out.append(finding("MIX_DROPOUT", f"{b - a:.2f} s of digital silence at {a:.2f} s" + (f" in scene {scene}" if scene else "") + f", {talk:.2f} s of it where the voice speaks: "
                           "part of the sentence is missing from the mix" + ("" if sure else " (no dry voice to confirm it was speaking there)"),
                           sev=None if sure else "WARN", scene=scene, t=a, duration=b - a, speech_s=talk, confirmed=sure))
    return out


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# detector: noise and clicks in the dry voice
# ---------------------------------------------------------------------------------------------------------------------------------------------------


def _pause_frames(ctx, db, n):
    """indices of the 20 ms frames of ctx.voice that lie in a pause inside a scene: between two words (gaps over 0.12 s), before the first word, after the last
    word (the 50 ms next to a word is left out: it still holds the word). Digital silence (the zero padding between scenes) is never a pause here."""
    sr = ctx.sr
    keep = np.zeros(len(db), dtype=bool)
    for sc in ctx.scenes:
        w = [x for x in (sc.get("words") or []) if len(x) >= 3]
        if not w:
            continue
        starts = [float(x[1]) for x in w]
        ends = [float(x[2]) for x in w]
        spans = [(float(sc.get("start", starts[0])), starts[0] - 0.05), (ends[-1] + 0.05, float(sc.get("end", ends[-1])))]
        spans += [(ends[k] + 0.05, starts[k + 1] - 0.05) for k in range(len(w) - 1) if starts[k + 1] - ends[k] > 0.12]
        for a, b in spans:
            i0, i1 = int(math.ceil(a * sr / n)), int(b * sr / n)
            if i1 > i0:
                keep[max(0, i0):min(len(db), i1)] = True
    return np.flatnonzero(keep & (db > -110.0))


def noise_floor_db(ctx):
    """the noise floor of the dry voice in dBFS: the median of the quietest 30% of the pause frames, or None when no pause can be measured"""
    if ctx.voice is None or not len(ctx.voice):
        return None
    db, n = _frame_db(ctx.voice, ctx.sr)
    idx = _pause_frames(ctx, db, n)
    if len(idx) < max(3, int(float(_thr(ctx, "voice", "hiss_min_frames_s")) * 1000 / 20)):
        return None
    lo = np.sort(db[idx])[:max(2, int(len(idx) * 0.3))]
    return float(np.median(lo))


def find_clicks(x, sr, min_jump=0.04, z=12.0, isolation_s=0.004):
    """times (s) of clicks in a voice track: a sample-to-sample jump of at least `min_jump` that is also `z` times the median jump of the 20 ms around it and
    is isolated (the jumps within `isolation_s` around it stay under a quarter of it). Speech has big jumps too, but they come with a run of large jumps
    around them; a click is a lone spike."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1 if x.shape[1] <= 8 else 0)
    if len(x) < 64:
        return []
    d = np.abs(np.diff(x))
    w = max(8, int(0.02 * sr))
    pad = np.pad(d, (w // 2, w // 2), mode="edge")
    base = np.median(np.lib.stride_tricks.sliding_window_view(pad, w)[:len(d)], axis=1) + 1e-6
    cand = np.flatnonzero((d >= min_jump) & (d >= z * base))
    k = max(2, int(isolation_s * sr))
    out, last = [], -10 ** 9
    for i in cand:
        around = np.concatenate([d[max(0, i - k):max(0, i - 2)], d[i + 3:i + 1 + k]])               # a spike is an up AND a down jump: the two neighbours are part of it
        if len(around) and float(around.max()) > 0.25 * d[i]:
            continue
        if i - last > int(0.02 * sr):
            out.append(float(i / sr))
            last = i
    return out


def check_voice_noise(ctx):
    """VOICE_NOISE: (1) the noise floor of the dry voice in the pauses inside scenes (see noise_floor_db) above profile voice.hiss_db is a WARN, more than
    voice.hiss_error_over_db above it an ERROR (repair "retake_scene"); (2) a click: a lone spike of the waveform (find_clicks) is a WARN with its time and scene.
    Needs ctx.voice and the word times; without pauses to measure there is no hiss verdict. Cannot judge: a hiss that is part of the voice's own character
    (a breathy voice), only that the floor is high."""
    out = []
    if ctx.voice is None or not len(ctx.voice):
        return out
    lim = float(_thr(ctx, "voice", "hiss_db"))
    floor = noise_floor_db(ctx)
    if floor is not None and floor > lim:
        err = float(_thr(ctx, "voice", "hiss_error_over_db"))
        out.append(finding("VOICE_NOISE", f"noise under the voice: the floor in the pauses is {floor:.0f} dBFS, {floor - lim:.0f} dB over the {lim:.0f} dBFS limit (audible hiss)",
                           "ERROR" if floor > lim + err else "WARN", floor_db=floor, limit_db=lim))
    words = _words(ctx)
    for t in find_clicks(ctx.voice, ctx.sr, float(_thr(ctx, "voice", "click_min_jump")), float(_thr(ctx, "voice", "click_z")), float(_thr(ctx, "voice", "click_isolation_s")))[:5]:
        scene = next((i for a, b, i in words if a - 0.2 <= t <= b + 0.2), None)
        out.append(finding("VOICE_NOISE", f"a click in the voice at {t:.2f} s" + (f" (scene {scene})" if scene else ""), scene=scene, t=t, click=True))
    return out


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# the registry of this module and the fault injection
# ---------------------------------------------------------------------------------------------------------------------------------------------------

DETECTORS = [check_loudness, check_true_peak, check_clip, check_pumping, check_voice_to_music, check_dropout, check_voice_noise]
INJECTIONS = {"clip": "MIX_CLIP", "peak": "TRUE_PEAK", "quiet": "LOUDNESS", "loud": "LOUDNESS", "dropout": "MIX_DROPOUT", "pumping": "MIX_PUMPING",
              "music_loud": "MIX_VOICE_TO_MUSIC", "hiss": "VOICE_NOISE", "click": "VOICE_NOISE"}


def synth_mix(sr=24000, seconds=12.0, pump=False, music_db=-34.0, voice_db=-17.0, seed=0):
    """(final, voice, music, words): a voice made of syllable bursts in four sentences with pauses, a music bed under it, the mix. With pump=True the music is
    ducked with a fast attack and a slow release, so it swells in every pause (the fault); otherwise it sits at a steady level (the fix: nothing for the pumping detector to see)."""
    from .. import music as _music
    rng = np.random.default_rng(seed)
    n = int(sr * seconds)
    voice = np.zeros(n)
    words, pos = [], 0.5
    while pos < seconds - 1.5 and len(words) < 4:
        ws = []
        for _ in range(5):
            d = 0.28 + 0.1 * rng.random()
            a, b = pos, pos + d
            f0 = 110 * 2 ** (rng.normal(0, 0.15))
            seg = np.arange(int(a * sr), int(b * sr))
            env = np.sin(np.pi * (seg / sr - a) / d) ** 2
            voice[seg] += sum(np.sin(2 * np.pi * f0 * k * seg / sr) / k for k in range(1, 12)) * env
            ws.append([f"w{sum(len(x) for x in words) + len(ws)}", round(a, 3), round(b, 3)])
            pos = b + 0.06
        words.append(ws)
        pos += 0.9                                                              # a pause between sentences
    voice *= 10 ** (voice_db / 20) / (np.sqrt(np.mean(voice[voice != 0] ** 2)) + 1e-9)
    bed = _music.bed(seconds + 0.5)[:n].astype(np.float64)
    bed = bed / (np.sqrt(np.mean(bed ** 2)) + 1e-9) * 10 ** (music_db / 20)
    k = int(0.05 * sr)
    smooth = np.convolve(np.abs(voice), np.ones(k) / k, mode="same")
    talking = (smooth > 0.02 * smooth.max()).astype(np.float64)
    if pump:
        duck = np.empty(n)
        g = 1.0
        a_att, a_rel = math.exp(-1 / (0.02 * sr)), math.exp(-1 / (0.9 * sr))
        for i in range(0, n, 16):
            target = 0.15 if talking[i] > 0.5 else 1.0
            g = target + (g - target) * (a_att if target < g else a_rel) ** 16
            duck[i:i + 16] = g
        music = bed * duck
    else:
        music = bed                                                             # a steady level: the same under the voice and in the pauses
    return (voice + music).astype(np.float32), voice.astype(np.float32), music.astype(np.float32), words


def inject(kind, final, voice=None, music=None, sr=24000, **kw):
    """(final, voice, music) copies with one known fault: clip (gain until samples hit full scale), peak (an inter-sample overshoot), quiet / loud (gain
    -10 / +12 dB), dropout (0.5 s of zeros inside speech), music_loud (the music 10 dB up), hiss (white noise at -40 dBFS under the voice),
    click (a lone spike in the voice)"""
    f = np.array(final, dtype=np.float64)
    v = None if voice is None else np.array(voice, dtype=np.float64)
    m = None if music is None else np.array(music, dtype=np.float64)
    rng = np.random.default_rng(kw.get("seed", 1))
    if kind == "clip":
        f = np.clip(f * (1.0 / max(np.abs(f).max(), 1e-9)) * 2.5, -1, 1)
    elif kind == "peak":
        f = f / max(np.abs(f).max(), 1e-9) * 0.97
        i = len(f) // 2
        f[i - 2:i + 4] += np.array([0.0, 0.02, 0.05, 0.05, 0.02, 0.0])
    elif kind == "quiet":
        f = f * 10 ** (-10 / 20)
    elif kind == "loud":
        f = np.clip(f * 10 ** (12 / 20), -0.99, 0.99)
    elif kind == "dropout":
        t0 = kw.get("t", 2.0)
        f[int(t0 * sr):int((t0 + 0.5) * sr)] = 0.0
    elif kind == "music_loud" and m is not None and v is not None:
        f = f + m * (10 ** (10 / 20) - 1)
        m = m * 10 ** (10 / 20)
    elif kind == "hiss" and v is not None:
        noise = rng.standard_normal(len(v)) * 10 ** (-40 / 20)
        v = v + noise
        f = f + noise
    elif kind == "click" and v is not None:
        i = kw.get("i", len(v) // 3)
        v[i] += 0.35
        f[i] += 0.35
    else:
        raise ValueError(f"unknown or unusable injection {kind}")
    return f.astype(np.float32), None if v is None else v.astype(np.float32), None if m is None else m.astype(np.float32)
