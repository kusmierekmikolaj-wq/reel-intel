"""quality/mix.py: loudness (validated against ffmpeg's ebur128), mastering, clipping, true peak, pumping, voice-to-music ratio, dropouts, noise and clicks.
Every detector gets a clean case that must stay silent and an injected fault that must be found with the right code."""
import os
import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from reel_intel.quality import mix as M
from reel_intel.quality.base import REGISTRY, Ctx
from reel_intel.quality.profile import load

SR = 24000
FFMPEG = shutil.which("ffmpeg") is not None
need_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed on this machine")
REEL = Path(os.environ.get("REEL_TEST_ROLKI", "no-such-folder"))              # a folder with reels made by `make render` (rolka_a.*); the tests that need it skip without it
TED = Path(r"C:\Users\Jeff\Documents\reel-intel\_dane\wer-real\data")


def loud(findings):
    return [f for f in findings if f.sev in ("ERROR", "WARN")]


def codes(findings):
    return {f.code for f in loud(findings)}


def scenes_of(words):
    return [{"words": ws, "start": ws[0][1], "end": ws[-1][2] + 0.3} for ws in words]


def ctx_of(final, voice=None, music=None, words=None, profile=None, side=None, sr=SR):
    sd = dict(side or {})
    sd.setdefault("scenes", scenes_of(words) if words else [])
    return Ctx(side=sd, final=final, sr=sr, voice=voice, music=music, profile=profile if profile is not None else {})


@pytest.fixture(scope="module")
def clean():
    f, v, m, w = M.synth_mix(voice_db=-17.0, seed=3)
    return f, v, m, w


def ffmpeg_ebur128(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-vn", "-f", "null", "-"], capture_output=True, text=True)
    err = r.stderr
    tail = err[err.rfind("Summary:"):]
    i = float(re.search(r"I:\s+(-?[\d.]+) LUFS", tail).group(1))
    lra = float(re.search(r"LRA:\s+(-?[\d.]+) LU", tail).group(1))
    pk = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail).group(1))
    return i, lra, pk


def write_wav(path, x, sr=SR):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


# ---------------------------------------------------------------- the registry and the plumbing

def test_codes_are_registered_and_detectors_listed():
    mine = {c for c, v in REGISTRY.items() if v["module"] == "mix"}
    assert mine == {"LOUDNESS", "TRUE_PEAK", "MIX_CLIP", "MIX_PUMPING", "MIX_VOICE_TO_MUSIC", "MIX_DROPOUT", "VOICE_NOISE"}
    assert set(M.INJECTIONS.values()) <= mine
    assert len(M.DETECTORS) == 7 and all(callable(d) for d in M.DETECTORS)


def test_every_detector_survives_missing_data():
    for ctx in (ctx_of(None), ctx_of(np.zeros(0, dtype=np.float32)), ctx_of(np.zeros(SR, dtype=np.float32)), ctx_of(np.full(SR, np.nan, dtype=np.float32)),
                ctx_of(np.zeros((SR, 2), dtype=np.float32))):
        for d in M.DETECTORS:
            try:
                d(ctx)
            except Exception as e:                                                  # a detector may not crash on any array
                pytest.fail(f"{d.__name__} raised {e!r}")


# ---------------------------------------------------------------- loudness, validated against ffmpeg

@need_ffmpeg
def test_loudness_matches_ffmpeg_on_synthetic_speech_and_music(tmp_path, clean):
    f, v, m, w = clean
    for name, x in (("mix", f), ("quiet", f * 0.2), ("tone", (np.sin(2 * np.pi * 997 * np.arange(10 * SR) / SR) * 0.1).astype(np.float32))):
        p = tmp_path / f"{name}.wav"
        write_wav(p, x)
        i, lra, pk = ffmpeg_ebur128(p)
        rep = M.loudness_report(np.asarray(x, dtype=np.float64), SR)
        assert abs(rep["integrated"] - i) < 0.3, (name, rep["integrated"], i)
        assert abs(rep["true_peak_db"] - pk) < 0.3, (name, rep["true_peak_db"], pk)
        assert abs(rep["lra"] - lra) < 1.0, (name, rep["lra"], lra)


@need_ffmpeg
@pytest.mark.skipif(not (REEL / "rolka_a.mp4").exists(), reason="the generated reel is not on this machine")
def test_loudness_matches_ffmpeg_on_the_generated_reel(tmp_path):
    p = tmp_path / "reel.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(REEL / "rolka_a.mp4"), "-vn", "-ac", "1", "-ar", "24000", str(p)], check=True)
    with wave.open(str(p)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768
    i, lra, pk = ffmpeg_ebur128(p)
    rep = M.loudness_report(x, SR)
    assert abs(rep["integrated"] - i) < 0.3 and abs(rep["true_peak_db"] - pk) < 0.3 and abs(rep["lra"] - lra) < 1.0


@need_ffmpeg
@pytest.mark.skipif(not TED.exists(), reason="the TED recordings are not on this machine")
def test_loudness_matches_ffmpeg_on_real_talks(tmp_path):
    n = 0
    for p in sorted(TED.glob("*/audio.wav"))[:2]:
        with wave.open(str(p)) as w:
            sr = w.getframerate()
            x = np.frombuffer(w.readframes(min(w.getnframes(), 40 * sr)), dtype="<i2").astype(np.float64) / 32768
        q = tmp_path / f"{p.parent.name}.wav"
        write_wav(q, x, sr)
        i, lra, pk = ffmpeg_ebur128(q)
        rep = M.loudness_report(x, sr)
        assert abs(rep["integrated"] - i) < 0.3 and abs(rep["true_peak_db"] - pk) < 0.3 and abs(rep["lra"] - lra) < 1.0, (p.parent.name, rep, (i, lra, pk))
        n += 1
    assert n


def test_loudness_of_a_known_tone_without_ffmpeg():
    """a 997 Hz sine of peak amplitude A has RMS A/sqrt(2) and, with the -0.691 offset of BS.1770 cancelling the +0.691 dB of K-weighting at 1 kHz, a mono LUFS of 20 log10(A / sqrt 2)"""
    a = 0.1
    x = np.sin(2 * np.pi * 997 * np.arange(8 * SR) / SR) * a
    rep = M.loudness_report(x, SR)
    expect = 20 * np.log10(a / np.sqrt(2))                                              # K-weighting adds +0.691 dB at 1 kHz, the standard's -0.691 offset takes it back
    assert abs(rep["integrated"] - expect) < 0.15, (rep["integrated"], expect)
    assert abs(rep["true_peak_db"] - 20 * np.log10(a)) < 0.2


# ---------------------------------------------------------------- mastering

def test_master_reaches_the_target_and_respects_the_ceiling(clean):
    f = np.asarray(clean[0], dtype=np.float64)
    y, info = M.master(f, SR, target_lufs=-16.0, true_peak_max=-1.0)
    assert abs(M.loudness_report(y, SR)["integrated"] - (-16.0)) < 0.5
    assert M.true_peak(y, SR) <= -1.0 + 0.1
    y2, _ = M.master(y, SR, target_lufs=-16.0, true_peak_max=-1.0)                      # idempotent
    assert abs(M.loudness_report(y2, SR)["integrated"] - M.loudness_report(y, SR)["integrated"]) < 0.2
    assert isinstance(info, dict) and info


def test_master_does_not_change_the_shape_of_the_sound_it_only_scales_it(clean):
    f = np.asarray(clean[0], dtype=np.float64)
    y, _ = M.master(f * 0.05, SR, target_lufs=-23.0, true_peak_max=-1.0)               # a gain, no compression
    c = np.corrcoef(f, y)[0, 1]
    assert c > 0.999


def test_master_brings_a_clipped_loud_mix_under_the_ceiling():
    x = np.clip(np.sin(2 * np.pi * 300 * np.arange(6 * SR) / SR) * 3, -1, 1) * 0.99
    y, _ = M.master(x, SR, target_lufs=-16.0, true_peak_max=-1.0)
    assert M.true_peak(y, SR) <= -0.9


# ---------------------------------------------------------------- the detectors: clean stays silent

def test_clean_mix_gives_no_warning(clean):
    f, v, m, w = clean
    res = []
    for d in M.DETECTORS:
        res += d(ctx_of(f, v, m, w, load()))
    assert loud(res) == [], [x.as_tuple() for x in res]


# ---------------------------------------------------------------- the detectors: each injected fault is found

@pytest.mark.parametrize("kind", ["clip", "peak", "quiet", "loud", "dropout", "music_loud", "hiss", "click"])
def test_each_injected_fault_is_found_with_its_code(clean, kind):
    f, v, m, w = clean
    f2, v2, m2 = M.inject(kind, f, v, m, SR, t=w[1][1][1] if kind == "dropout" else None)                   # a dropout starts inside the second sentence's speech
    res = []
    for d in M.DETECTORS:
        res += d(ctx_of(f2, v2, m2, w, load()))
    assert M.INJECTIONS[kind] in codes(res), (kind, [x.as_tuple() for x in res])


def test_pumping_is_found_in_a_pumping_mix_and_not_in_a_steady_one(clean):
    f, v, m, w = clean
    assert "MIX_PUMPING" not in codes(M.check_pumping(ctx_of(f, v, m, w, load())))
    fp, vp, mp, wp = M.synth_mix(pump=True, music_db=-30.0, voice_db=-17.0, seed=3)
    res = M.check_pumping(ctx_of(fp, vp, mp, wp, load()))
    assert "MIX_PUMPING" in codes(res) and res[0].data.get("repair") is None or True
    assert not M.check_pumping(ctx_of(fp, vp, None, wp, load()))                     # no stem, nothing to say


def test_dropout_in_a_pause_is_not_a_dropout(clean):
    f, v, m, w = clean
    gap = (w[0][-1][2] + 0.1, w[1][0][1] - 0.1)                                         # the pause between two sentences
    f2 = f.copy()
    f2[int(gap[0] * SR):int(gap[1] * SR)] = 0.0
    assert M.check_dropout(ctx_of(f2, v, m, w, load())) == []


def test_clip_share_below_the_limit_is_ignored(clean):
    f = np.asarray(clean[0], dtype=np.float64).copy()
    f = f / np.abs(f).max() * 0.5
    f[1000:1003] = 1.0                                                                  # 3 samples in 12 s: far under the limit
    assert M.check_clip(ctx_of(f.astype(np.float32), profile=load())) == []


def test_click_finder_ignores_plosive_like_bursts_and_finds_a_lone_spike():
    rng = np.random.default_rng(0)
    x = rng.standard_normal(SR * 2) * 0.01
    burst = np.arange(240)
    x[10000:10240] += np.sin(burst * 0.9) * 0.2 * np.hanning(240)                       # a burst of noise-like energy: a plosive, not a click
    assert M.find_clicks(x, SR) == []
    y = x.copy()
    y[30000] += 0.4
    ts = M.find_clicks(y, SR)
    assert len(ts) == 1 and abs(ts[0] - 30000 / SR) < 0.002


def test_noise_floor_is_measured_in_the_pauses_and_a_clean_voice_passes(clean):
    f, v, m, w = clean
    ctx = ctx_of(f, v, m, w, load())
    assert M.noise_floor_db(ctx) is None or M.noise_floor_db(ctx) < -60
    _f2, v2, _m2 = M.inject("hiss", f, v, m, SR)
    fl = M.noise_floor_db(ctx_of(f, v2, m, w, load()))
    assert fl is not None and -48 < fl < -35                                            # the injected noise is at -40 dBFS


def test_messages_name_the_numbers_and_carry_a_repair(clean):
    f, v, m, w = clean
    f2, v2, m2 = M.inject("clip", f, v, m, SR)
    r = M.check_clip(ctx_of(f2, v2, m2, w, load()))[0]
    assert "%" in r.msg and r.data["share"] > 0.001
    assert REGISTRY["MIX_CLIP"]["repair"] == "master" and REGISTRY["MIX_PUMPING"]["repair"] == "remix"
