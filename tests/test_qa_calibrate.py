"""quality/calibrate.py: thresholds from measured recordings. Synthetic speech with a known rate and pitch spread, and the rules that turn a spread into a limit."""
import json
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from reel_intel.quality import calibrate as C
from reel_intel.quality import profile as P

SR = 24000
FFMPEG = shutil.which("ffmpeg") is not None


def voiced(sec, syl_s=0.19, f0=130.0, wobble_st=2.0, db=-20.0, seed=0):
    """a vowel-like signal with a syllable-rate envelope and a pitch that wanders by about `wobble_st` semitones"""
    rng = np.random.default_rng(seed)
    n = int(sec * SR)
    tt = np.arange(n) / SR
    ph = 2 * np.pi * np.cumsum(f0 * 2 ** ((wobble_st * np.sin(2 * np.pi * 0.7 * tt + rng.random() * 6)) / 12.0)) / SR
    carrier = np.sin(ph) + 0.55 * np.sin(2 * ph) + 0.3 * np.sin(3 * ph) + 0.15 * np.sin(5 * ph)
    env = 0.5 - 0.5 * np.cos(2 * np.pi * tt / syl_s)
    x = (carrier * (0.1 + 0.9 * env)).astype("float32")
    return x / (np.sqrt(np.mean(x ** 2)) + 1e-9) * 10 ** (db / 20)


def talk(n=4, syl_s=0.19, wobble_st=2.0, utt_s=3.0, pause_s=0.6):
    parts = [np.zeros(int(0.5 * SR), dtype="float32")]
    for i in range(n):
        parts += [voiced(utt_s, syl_s, wobble_st=wobble_st, seed=i), np.zeros(int(pause_s * SR), dtype="float32")]
    return np.concatenate(parts)


def write_wav(path, x, sr=SR):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


# ---------------------------------------------------------------- measuring
def test_utterances_are_the_stretches_between_pauses():
    x = talk(n=4)
    u = C.utterances(x, SR)
    assert len(u) == 4 and all(2.7 <= b - a <= 3.3 for a, b in u)


def test_short_bursts_and_short_pauses_are_not_utterances():
    short = np.concatenate([voiced(1.0), np.zeros(int(0.6 * SR), dtype="float32"), voiced(3.0)])
    assert len(C.utterances(short, SR)) == 1                           # the 1 s burst is under 1.5 s
    joined = np.concatenate([voiced(2.0), np.zeros(int(0.1 * SR), dtype="float32"), voiced(2.0)])
    assert len(C.utterances(joined, SR)) == 1                          # a 0.1 s pause is inside one utterance


def test_the_measured_rate_follows_the_real_rate():
    for syl_s, lo, hi in ((0.25, 3.3, 4.8), (0.17, 5.2, 6.6), (0.13, 6.8, 8.6)):
        m = C.measure_audio(talk(n=3, syl_s=syl_s), SR, with_f0=False)
        rates = [u["rate"] for u in m["utt"]]
        assert len(rates) == 3 and all(lo <= r <= hi for r in rates), (syl_s, rates)


def test_the_measured_pitch_spread_follows_the_real_spread():
    flat = C.measure_audio(talk(n=3, wobble_st=0.2), SR)
    lively = C.measure_audio(talk(n=3, wobble_st=4.0), SR)
    f = [u["f0_std"] for u in flat["utt"] if u["f0_std"] is not None]
    l = [u["f0_std"] for u in lively["utt"] if u["f0_std"] is not None]
    assert f and l and max(f) < 1.0 and min(l) > 1.8
    assert len(flat["f0_windows"]) == 3 and len(lively["f0_windows"]) == 3          # one 3 s window in each 3 s utterance
    assert max(flat["f0_windows"]) < 1.0 and min(lively["f0_windows"]) > 1.8


def test_loudness_is_measured_only_for_reels():
    x = talk(n=3)
    assert C.measure_audio(x, SR, reel=False, with_f0=False)["loudness"] is None
    m = C.measure_audio(x, SR, reel=True, with_f0=False)["loudness"]
    assert m["integrated"] is not None and -40 < m["integrated"] < -5 and m["true_peak_db"] is not None


def test_find_files_prefers_audio_wav_and_skips_voice_and_sidecars(tmp_path):
    (tmp_path / "a").mkdir()
    write_wav(tmp_path / "a" / "audio.wav", talk(1))
    write_wav(tmp_path / "a" / "other.wav", talk(1))
    (tmp_path / "b").mkdir()
    write_wav(tmp_path / "b" / "reel.wav", talk(1))
    write_wav(tmp_path / "b" / "reel_voice.wav", talk(1))
    names = sorted(p.relative_to(tmp_path).as_posix() for p in C.find_files(tmp_path))
    assert names == ["a/audio.wav", "b/reel.wav"]
    assert C.find_files(tmp_path / "a" / "audio.wav") == [tmp_path / "a" / "audio.wav"]


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed on this machine")
def test_measure_files_decodes_and_skips_what_is_unreadable(tmp_path):
    write_wav(tmp_path / "ok.wav", talk(3))
    (tmp_path / "bad.wav").write_bytes(b"not audio at all")
    write_wav(tmp_path / "short.wav", voiced(1.0))
    log = []
    ms = C.measure_files([tmp_path / "ok.wav", tmp_path / "bad.wav", tmp_path / "short.wav"], log=log.append)
    assert len(ms) == 1 and ms[0]["file"].endswith("ok.wav") and len(ms[0]["utt"]) == 3
    assert sum("skipped" in l for l in log) == 2


# ---------------------------------------------------------------- from measurements to limits
def fake(rates, stds=None, lufs=None, reel=False):
    """a measurement: one utterance per rate; `stds` are the pitch spreads of the 3 s windows"""
    return {"file": "x", "reel": reel, "utt": [{"t0": 0, "t1": 2, "rate": r, "f0_std": None} for r in rates], "f0_windows": list(stds or []),
            "loudness": None if lufs is None else {"integrated": lufs, "lra": 5.0, "true_peak_db": -2.0}}


def test_too_little_data_sets_nothing_and_says_why():
    over, rep = C.build_profile([fake([5.0] * 5, [3.0] * 5)])
    assert "pace" not in over and "intonation" not in over and "loudness" not in over
    assert set(rep["not_set"]) == {"pace", "intonation", "loudness"} and "needed" in rep["not_set"]["pace"]


def test_fast_is_never_loosened_by_data_that_speaks_faster_than_the_default():
    rates = list(np.linspace(5.5, 7.1, 60))                              # fast talkers: p98 + 5% is about 7.4
    over, rep = C.build_profile([fake(rates)])
    assert over["pace"]["fast"] == P.DEFAULT["pace"]["fast"] == 6.8
    assert rep["set"]["pace"]["measured_fast"] > 6.8


def test_fast_is_tightened_by_data_that_speaks_slower_than_the_default():
    rates = list(np.linspace(3.5, 5.5, 60))
    over, rep = C.build_profile([fake(rates)])
    assert 5.4 <= over["pace"]["fast"] < 6.8
    assert over["pace"].get("burst", P.DEFAULT["pace"]["burst"]) >= over["pace"]["fast"] + 1.5          # a burst limit stays clear of the fast limit


def test_slow_is_never_raised_above_the_default():
    over, _ = C.build_profile([fake(list(np.linspace(5.0, 7.0, 60)))])
    assert over["pace"]["slow"] == P.DEFAULT["pace"]["slow"] == 3.2
    over, _ = C.build_profile([fake(list(np.linspace(1.9, 5.0, 60)))])
    assert over["pace"]["slow"] < 3.2


def test_pace_target_is_not_calibrated():
    over, _ = C.build_profile([fake(list(np.linspace(4.0, 7.0, 60)))])
    assert "target" not in over["pace"]


def test_a_failed_rate_measurement_is_ignored():
    rates = [0.0, 0.2, 15.0, 40.0] + list(np.linspace(4.0, 6.0, 40))
    over, rep = C.build_profile([fake(rates)])
    assert rep["spread"]["rate"]["n"] == 40 + 0


def test_the_pitch_limit_is_below_19_in_20_real_sentences():
    stds = list(np.linspace(1.5, 7.0, 100))
    over, rep = C.build_profile([fake([5.0] * 100, stds)])
    lim = over["intonation"]["f0_std_semitones_min"]
    assert lim == pytest.approx(np.percentile(stds, 5) * 0.85, abs=0.06)
    assert sum(s < lim for s in stds) <= 2                             # at most about one in twenty real sentences would be called flat


def test_loudness_comes_from_reels_only_and_is_uncertain_until_ten():
    ms = [fake([5.0], lufs=l, reel=True) for l in (-13.0, -14.0, -15.0, -14.5)] + [fake([5.0], lufs=-25.0, reel=False)]
    over, rep = C.build_profile(ms)
    assert over["loudness"]["target_lufs"] == pytest.approx(-14.2, abs=0.3) and over["loudness"]["uncertain"] is True
    assert 2.0 <= over["loudness"]["tolerance"] <= 4.0
    many = [fake([5.0], lufs=-14.0 + 0.2 * i, reel=True) for i in range(12)]
    assert C.build_profile(many)[0]["loudness"]["uncertain"] is False


def test_tolerance_is_clamped():
    tight = [fake([5.0], lufs=-14.0, reel=True) for _ in range(5)]
    assert C.build_profile(tight)[0]["loudness"]["tolerance"] == 2.0
    wide = [fake([5.0], lufs=l, reel=True) for l in (-8, -12, -16, -20, -24, -28)]
    assert C.build_profile(wide)[0]["loudness"]["tolerance"] == 4.0


def test_the_overlay_records_where_it_came_from():
    over, _ = C.build_profile([fake(list(np.linspace(4, 6, 40)), [3.0] * 40)])
    assert over["calibration"] == {"files": 1, "utterances": 40, "reels": 0}


# ---------------------------------------------------------------- the file
def test_write_profile_merges_with_an_earlier_file_and_load_lays_it_over_the_defaults(tmp_path):
    path = tmp_path / "qa_profile.json"
    C.write_profile(path, {"pace": {"fast": 6.0, "slow": 3.0}, "intonation": {"f0_std_semitones_min": 1.9}})
    C.write_profile(path, {"pace": {"fast": 5.9}, "loudness": {"target_lufs": -14.3}})
    cur = json.loads(path.read_text(encoding="utf-8"))
    assert cur["pace"] == {"fast": 5.9, "slow": 3.0} and cur["intonation"]["f0_std_semitones_min"] == 1.9 and cur["loudness"]["target_lufs"] == -14.3
    prof = P.load(path)
    assert prof["pace"]["fast"] == 5.9 and prof["pace"]["target"] == P.DEFAULT["pace"]["target"] and prof["loudness"]["tolerance"] == P.DEFAULT["loudness"]["tolerance"]
    assert P.DEFAULT["pace"]["fast"] == 6.8                           # the defaults are untouched
    C.write_profile(path, {"pace": {"fast": 5.0}}, merge=False)
    assert json.loads(path.read_text(encoding="utf-8")) == {"pace": {"fast": 5.0}}


def test_a_broken_profile_file_falls_back_to_the_defaults(tmp_path):
    path = tmp_path / "qa_profile.json"
    path.write_text("{ nope", encoding="utf-8")
    assert P.load(path) == P.DEFAULT
    C.write_profile(path, {"pace": {"fast": 6.0}})                     # and writing over it works
    assert P.load(path)["pace"]["fast"] == 6.0


def test_the_report_lists_what_was_and_was_not_set():
    over, rep = C.build_profile([fake(list(np.linspace(4, 6, 40)), [3.0] * 40)])
    text = C.format_report(rep)
    assert "set      pace" in text and "NOT set  loudness" in text and "syllables a second" in text


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed on this machine")
def test_main_end_to_end_writes_a_profile(tmp_path, capsys):
    for k in range(3):
        d = tmp_path / "talks" / f"t{k}"
        d.mkdir(parents=True)
        write_wav(d / "audio.wav", talk(n=14, syl_s=0.2, wobble_st=2.5))
    out = tmp_path / "qa_profile.json"
    assert C.main([str(tmp_path / "talks"), "--out", str(out)]) == 0
    cur = json.loads(out.read_text(encoding="utf-8"))
    assert cur["calibration"]["utterances"] >= 30 and "pace" in cur and "intonation" in cur and "loudness" not in cur
    assert C.main([]) == 2
