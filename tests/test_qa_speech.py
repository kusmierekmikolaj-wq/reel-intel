"""quality/speech.py: word faults (SUB/DEL/INS), pauses inside a sentence, pitch (F0 tracker, MONOTONE, PITCH_JUMP), speaker drift, engine and renderer fallback.

Synthetic speech (seeded) for every detector: a clean reel must give no ERROR/WARN, and every injected fault must be reported with its own code. The F0
tracker is checked on glides and vibrato of known pitch. Tests on real recordings are skipped when the files are not on this machine."""
import os
import json
import wave
from pathlib import Path

import numpy as np
import pytest

from reel_intel.quality import speech as S
from reel_intel.quality.base import REGISTRY, Ctx
from reel_intel.quality.profile import load

SR = 24000
TED = Path(r"C:\Users\Jeff\Documents\reel-intel\_dane\wer-real\data")
ROLKA = Path(os.environ.get("REEL_TEST_ROLKI", "no-such-folder"))
REPAIRS = {"retake_scene", "respell", "slow_scene", "retrim_scene", "remix", "master", "relayout", "rerender", None}


def loud(findings):
    """the findings that make a reel not clean: ERROR and WARN"""
    return [f for f in findings if f.sev in ("ERROR", "WARN")]


def codes(findings):
    return {f.code for f in loud(findings)}


@pytest.fixture(scope="module")
def reel():
    return S.synth_reel(seed=1, sr=SR)


def run(side, voice, profile=None, sr=SR):
    return S.run_detectors(side, voice, sr, profile if profile is not None else {"intonation": {"f0_std_semitones_min": 1.0}})


def uncalibrated():
    """the profile as it is before `make calibrate` has run: no pitch limit"""
    p = load()
    p["intonation"] = {**p["intonation"], "f0_std_semitones_min": None}
    return p


def ctx_of(side, voice=None, profile=None, sr=SR):
    return Ctx(side=side, voice=voice, sr=sr, profile=profile if profile is not None else {})


# ---------------------------------------------------------------- the registry and the plumbing

def test_every_code_is_registered_with_a_known_repair():
    mine = {c: v for c, v in REGISTRY.items() if v["module"] == "speech"}
    assert set(mine) == {"VOICE_SUB", "VOICE_DEL", "VOICE_INS", "VOICE_PAUSE_INNER", "VOICE_MONOTONE", "VOICE_PITCH_JUMP", "VOICE_DRIFT", "ENGINE_FALLBACK", "RENDER_FALLBACK"}
    for code, v in mine.items():
        assert v["desc"] and v["sev"] in ("ERROR", "WARN", "INFO") and v["repair"] in REPAIRS, code
    assert REGISTRY["ENGINE_FALLBACK"]["sev"] == "ERROR"
    assert set(S.INJECTIONS.values()) <= set(mine)                                  # every injection names a registered code
    assert len(S.DETECTORS) == 7 and all(callable(d) for d in S.DETECTORS)


def test_every_detector_survives_missing_data():
    """a version-1 sidecar, no voice, no scenes: nothing to say, nothing raised"""
    for side in ({}, {"scenes": []}, {"scenes": [{"text": "a b c"}]}, {"scenes": [{"words": []}], "scene_embeddings": [], "engine_used": None}):
        for voice in (None, np.zeros(0, dtype=np.float32), np.zeros(SR // 10, dtype=np.float32)):
            ctx = ctx_of(side, voice, load())
            for d in S.DETECTORS:
                assert d(ctx) == [], d.__name__


def test_clean_synthetic_reel_has_no_error_or_warn(reel):
    side, voice = reel
    res = run(side, voice)
    assert loud(res) == [], [f.as_tuple() for f in res]
    assert loud(run(side, voice, load())) == []                                   # with the real default profile: monotone only informs


# ---------------------------------------------------------------- F0 tracker

def harmonic(f0, sr, harmonics=20, skip_fundamental=False, noise_db=-50.0, seed=0):
    ph = 2 * np.pi * np.cumsum(f0) / sr
    y = np.zeros(len(f0))
    for h in range(2 if skip_fundamental else 1, harmonics + 1):
        y += np.sin(h * ph) / h * (h * f0 < 0.45 * sr)
    y = y / np.abs(y).max() * 0.5
    return y + np.random.default_rng(seed).standard_normal(len(y)) * 10 ** (noise_db / 20)


def f0_error(f0_true, sr, x):
    est = S.f0_track(x, sr)
    ref = np.interp(S.f0_times(len(est)), np.arange(len(f0_true)) / sr, f0_true)
    ok = ~np.isnan(est)
    return ok.mean(), np.abs(est[ok] / ref[ok] - 1)


@pytest.mark.parametrize("sr", [16000, 22050, 24000])
def test_f0_tracks_glides_and_vibrato_within_three_percent(sr):
    t = np.arange(int(1.5 * sr)) / sr
    truth = {"rising glide": 100 * 2 ** (t / 1.5), "falling glide": 320 * 2 ** (-t / 1.5), "vibrato": 150 * 2 ** (np.sin(2 * np.pi * 5 * t) / 12),
             "low steady": np.full(len(t), 85.0), "high steady": np.full(len(t), 380.0), "fast glide": 90 * 2 ** (t / 1.5 * 1.98)}
    for name, f in truth.items():
        for skip in (False, True):                                               # a voice without energy at the fundamental is still that pitch
            voiced, err = f0_error(f, sr, harmonic(f, sr, skip_fundamental=skip))
            assert voiced > 0.97, (name, skip, voiced)
            assert np.median(err) < 0.01 and np.percentile(err, 95) < 0.03 and err.max() < 0.03, (name, skip, np.median(err), err.max())


def test_f0_ignores_noise_silence_hum_and_clicks():
    rng = np.random.default_rng(0)
    sr = 16000
    for x in (rng.standard_normal(sr) * 0.1, np.zeros(sr), 0.3 * np.sin(2 * np.pi * 50 * np.arange(sr) / sr), np.full(sr, 0.5),
              np.concatenate([np.zeros(8000), [1.0], np.zeros(7999)])):
        f = S.f0_track(x, sr)
        assert len(f) == sr // 160 + 1 and np.isnan(f).all()
    assert len(S.f0_track(np.zeros(50), sr)) == 1                                  # shorter than one window: no crash
    assert S.f0_track(np.zeros((sr, 1)), sr).shape == (101,) and S.f0_track([0.0] * sr, sr).shape == (101,)


def test_f0_semitones_is_relative_to_the_speakers_median():
    f = np.array([100.0, 200.0, np.nan, 50.0, 100.0])
    st = S.f0_semitones(f)
    assert np.isnan(st[2]) and np.allclose(st[[0, 4]], 0.0) and np.allclose(st[[1, 3]], [12.0, -12.0])
    assert np.allclose(S.f0_semitones(np.array([110.0]), ref=100.0), 12 * np.log2(1.1))
    assert np.isnan(S.f0_semitones(np.array([np.nan, np.nan]))).all()


def test_f0_of_synthetic_voice_matches_the_contour_that_made_it(reel):
    side, voice = reel
    f0 = S.f0_track(voice, SR)
    ok = ~np.isnan(f0)
    assert 0.3 < ok.mean() < 0.9 and 80 < np.nanmedian(f0) < 160                   # speech and gaps; the voice was made at 120 Hz
    assert np.nanmax(f0) < 250 and np.nanmin(f0) > 60                                # no octave errors


# ---------------------------------------------------------------- words: classify_ops

def ops(script, heard, say=None):
    return S.classify_ops(script.split(), heard.split() if isinstance(heard, str) else heard, say)


def test_classify_ops_a_perfect_reading_has_no_ops():
    r = ops("Trzydzieści rolek konkurencji, a ty nie obejrzałeś żadnej.", "trzydziesci rolek konkurencji a ty nie obejrzales zadnej")      # case, punctuation, diacritics
    assert r["sub"] == [] and r["del"] == [] and r["ins"] == [] and r["eq"] == 8 and r["n_script"] == 8


def test_classify_ops_substitution_deletion_insertion():
    r = ops("Program słucha, co autor mówi", "program słuha co aktor mówi")
    assert r["sub"] == [("słucha", "słuha"), ("autor", "aktor")] and r["del"] == [] and r["ins"] == []
    r = ops("Program słucha, co autor mówi", "program słucha autor mówi")
    assert r["del"] == ["co"] and r["sub"] == [] and r["ins"] == []
    r = ops("Program słucha, co autor mówi", "program słucha co autor mówi dziękuję za oglądanie")
    assert r["ins"] == ["dziękuję", "za", "oglądanie"] and r["hallucination"] == r["ins"] and r["repeat"] == []


def test_classify_ops_repeats_are_split_from_hallucinations():
    r = ops("Program słucha, co autor mówi", "program słucha co autor mówi mówi")
    assert r["repeat"] == ["mówi"] and r["hallucination"] == [] and r["ins"] == ["mówi"]                    # a word heard twice
    r = ops("Program słucha, co autor mówi", "program słucha co autor mówi co autor mówi")
    assert r["repeat"] == ["co", "autor", "mówi"] and r["hallucination"] == []                              # a 3-word phrase heard twice
    r = ops("Program słucha, co autor mówi", "program słucha program słucha co autor mówi")
    assert r["repeat"] == ["program", "słucha"] and r["hallucination"] == []                                # a 2-word phrase heard twice, the first time
    r = ops("bardzo bardzo dobry", "bardzo bardzo dobry")
    assert r["ins"] == []                                                                                       # the script repeats it: not a fault
    r = ops("a b c d", "a b x b c d")
    assert sorted(r["ins"]) == ["b", "x"] and r["hallucination"] == ["x"] and r["repeat"] == ["b"]                  # one of each
    for r in (ops("a b c", "a x y b c"), ops("a b c", "a b b b c")):
        assert sorted(r["ins"]) == sorted(r["repeat"] + r["hallucination"])                                  # repeat + hallucination = ins


def test_classify_ops_respelling_digits_and_empty_inputs():
    assert ops("Dla twojego agenta AI.", "dla twojego agenta ej aj", {"AI": "ej aj"})["ins"] == []
    r = ops("Dla twojego agenta AI.", "dla twojego agenta ej aj")
    assert r["sub"] and r["ins"]                                                                                 # without the table it is a false alarm: that is why the table exists
    r = ops("W 2026 roku", "w dwa tysiące dwadzieścia sześć roku")
    assert r["sub"] == [] and r["del"] == [] and r["ins"] == []                                              # a number may be spoken as several words
    r = ops("a b c", [])
    assert r["del"] == ["a", "b", "c"] and r["ins"] == [] and r["n_heard"] == 0
    r = ops("", "a b")
    assert r["ins"] == ["a", "b"] and r["n_script"] == 0
    r = ops("a b", [["a", 0.0, 0.1], ["b", 0.1, 0.2]])                                                       # heard words with times
    assert r["eq"] == 2
    assert S.classify_ops(["—", "a"], ["a"])["del"] == []                                                      # a word that is only punctuation says nothing


# ---------------------------------------------------------------- words: the detector

def scene_side(text, heard=None, **extra):
    words = [[w, 0.4 + 0.3 * i, 0.4 + 0.3 * i + 0.25] for i, w in enumerate(text.split())]
    sc = {"text": text, "start": 0.4, "end": words[-1][2] + 0.1, "words": words}
    if heard is not None:
        sc["heard_words"] = heard.split() if isinstance(heard, str) else heard
    sc.update(extra)
    return {"scenes": [sc]}


def test_check_words_clean_and_each_fault(reel):
    side, voice = reel
    assert S.check_words(ctx_of(side)) == []
    for kind, code in (("sub", "VOICE_SUB"), ("del", "VOICE_DEL"), ("ins_repeat", "VOICE_INS"), ("ins_hallucination", "VOICE_INS")):
        s2, _ = S.inject(kind, side, voice, SR, scene=3)
        res = S.check_words(ctx_of(s2))
        assert [(f.code, f.sev, f.scene) for f in res] == [(code, "WARN", 3)], (kind, [f.as_tuple() for f in res])
        assert res[0].t is not None and abs(res[0].t - s2["scenes"][2]["words"][2][1]) < 1e-6 or kind.startswith("ins")


def test_check_words_severity_follows_how_much_is_wrong():
    text = "jeden dwa trzy cztery pięć sześć"
    one = S.check_words(ctx_of(scene_side(text, "jeden dwa trzy cztery pięć szesc")))                        # only a diacritic: not a fault
    assert [f.sev for f in one] == [] or all(f.sev == "INFO" for f in one)
    wrong = S.check_words(ctx_of(scene_side(text, "jeden dwa trzy kot pies ryba")))                          # three of six words are other words: half
    assert {f.code for f in wrong} == {"VOICE_SUB"} and wrong[0].sev == "WARN"
    most = S.check_words(ctx_of(scene_side(text, "jeden kot pies ryba ser mleko")))                           # five of six wrong: more than half
    assert most[0].sev == "ERROR"
    none = S.check_words(ctx_of(scene_side(text, [])))                                                          # nothing was heard
    assert [(f.code, f.sev) for f in none] == [("VOICE_DEL", "ERROR")]
    ins = S.check_words(ctx_of(scene_side(text, "jeden dwa trzy cztery pięć sześć " + "ala ma kota " * 3)))   # nine invented words
    assert ins and ins[0].code == "VOICE_INS" and ins[0].sev == "ERROR"


def test_check_words_a_near_miss_is_only_info_and_the_first_word_is_flagged():
    res = S.check_words(ctx_of(scene_side("napisz komentarz teraz", "napisz komentarze teraz")))
    assert [(f.code, f.sev) for f in res] == [("VOICE_SUB", "INFO")]
    res = S.check_words(ctx_of(scene_side("Trzydzieści rolek konkurencji", "dwadzieścia rolek konkurencji")))
    assert res[0].sev == "WARN" and "first word" in res[0].msg


def test_check_words_uses_the_respelling_table_and_the_builtin_lexicon():
    side = scene_side("Wszystko ląduje w pliku dla agenta AI", "wszystko ląduje w pliku dla agenta ej aj")
    assert S.check_words(ctx_of(side)) == []                                                                   # make.LEXICON knows "ai" -> "ej aj" (language pl)
    side = scene_side("Czytam skille", "czytam skile")
    side["params"] = {"lang": "pl", "say": {"skille": "skile"}}
    assert S.check_words(ctx_of(side)) == []
    side = scene_side("Czytam skille", "czytam skile")
    side["params"] = {"lang": "en"}                                                                            # English: no built-in respelling for "skille"
    assert S.check_words(ctx_of(side))                                   # no table, a near miss at least informs


def test_check_words_falls_back_to_the_missed_list_of_a_version_1_sidecar():
    side = scene_side("Trzydzieści rolek konkurencji, a ty nie obejrzałeś żadnej", heard=None, missed=["Trzydzieści"], heard_ratio=None)
    side["scenes"][0]["heard"] = 0.875
    res = S.check_words(ctx_of(side))
    assert [(f.code, f.sev, f.scene) for f in res] == [("VOICE_SUB", "WARN", 1)] and res[0].data["candidate"] is True
    side["scenes"][0]["heard"] = 0.4
    assert S.check_words(ctx_of(side))[0].sev == "ERROR"
    side["scenes"][0]["missed"] = []
    assert S.check_words(ctx_of(side)) == []                                                                    # nothing missed, nothing heard_words: nothing to say


# ---------------------------------------------------------------- pause inside a sentence

def test_pause_inner_clean_and_injected(reel):
    side, voice = reel
    assert S.check_pause_inner(ctx_of(side, voice)) == []
    s2, v2 = S.inject("pause_inner", side, voice, SR, scene=2, word=2)
    res = S.check_pause_inner(ctx_of(s2, v2))
    assert len(res) == 1 and res[0].code == "VOICE_PAUSE_INNER" and res[0].scene == 2 and res[0].data["confirmed"] is True
    w = side["scenes"][1]["words"]
    assert res[0].data["left"] == w[2][0] and res[0].data["right"] == w[3][0]                                 # the words around the pause
    assert 1.05 < res[0].data["dur"] < 1.4 and w[2][2] - 0.1 < res[0].t < w[3][1] + 1.0


def test_pause_inner_reads_the_limit_from_the_profile(reel):
    side, voice = reel
    s2, v2 = S.inject("pause_inner", side, voice, SR, scene=2, word=2, pause_s=0.9)
    assert S.check_pause_inner(ctx_of(s2, v2, {"voice": {"inner_pause_s": 1.2}})) == []
    assert len(S.check_pause_inner(ctx_of(s2, v2, {"voice": {"inner_pause_s": 0.6}}))) == 1
    s3, v3 = S.inject("pause_inner", side, voice, SR, scene=2, word=2, pause_s=0.4)
    assert S.check_pause_inner(ctx_of(s3, v3, {"voice": {"inner_pause_s": 0.6}})) == []                       # a breath-long gap is normal


def test_pause_inner_without_the_voice_track_uses_word_times_and_says_so(reel):
    side, voice = reel
    s2, _ = S.inject("pause_inner", side, voice, SR, scene=2, word=2)
    res = S.check_pause_inner(ctx_of(s2, None))
    assert len(res) == 1 and res[0].data["confirmed"] is False and "not confirmed" in res[0].msg


def test_pause_inner_needs_silence_in_the_sound_not_just_a_gap_in_the_times(reel):
    side, voice = reel
    s2 = json.loads(json.dumps(side))
    del s2["scenes"][1]["words"][2:4]                                                                            # the aligner lost two words that are clearly audible
    gap = s2["scenes"][1]["words"][2][1] - s2["scenes"][1]["words"][1][2]
    assert gap > 0.6
    assert S.check_pause_inner(ctx_of(s2, voice)) == []                                                         # the sound is loud there: not a pause


def test_pause_inner_finds_a_silence_the_word_times_swallowed():
    side, voice = S.synth_reel(seed=4, sr=SR)
    s2, v2 = S.inject("pause_inner", side, voice, SR, scene=2, word=2, pause_s=1.0)
    w = s2["scenes"][1]["words"]
    w[2][2] = w[3][1] - 0.02                                                                                     # the aligner stretched word 3 over the silence
    res = S.check_pause_inner(ctx_of(s2, v2))
    assert len(res) == 1 and "sound" in res[0].data["source"] and res[0].data["dur"] > 0.8


# ---------------------------------------------------------------- monotone

def test_monotone_without_a_limit_only_informs(reel):
    side, voice = reel
    flat_side, flat_voice = S.synth_reel(seed=2, sr=SR, intonation_st=0.0)
    for sd, vc in ((side, voice), (flat_side, flat_voice)):
        res = S.check_monotone(ctx_of(sd, vc, uncalibrated()))                                                   # no limit until calibrated: f0_std_semitones_min = None
        assert len(res) == 1 and res[0].sev == "INFO" and res[0].code == "VOICE_MONOTONE" and len(res[0].data["std_semitones"]) == 4
        assert "calibrate" in res[0].msg


def test_monotone_flat_voice_is_caught_and_lively_voice_is_not(reel):
    side, voice = reel
    prof = {"intonation": {"f0_std_semitones_min": 1.0}}
    assert S.check_monotone(ctx_of(side, voice, prof)) == []
    flat_side, flat_voice = S.synth_reel(seed=2, sr=SR, intonation_st=0.0)
    res = S.check_monotone(ctx_of(flat_side, flat_voice, prof))
    assert [f.scene for f in res] == [1, 2, 3, 4] and all(f.code == "VOICE_MONOTONE" and f.sev == "WARN" and f.data["std_semitones"] < 0.5 for f in res)
    s2, v2 = S.inject("monotone", side, voice, SR, scene=3)
    res = S.check_monotone(ctx_of(s2, v2, prof))
    assert [f.scene for f in res] == [3] and len(v2) == len(voice)                                              # only the flattened scene
    assert S.check_monotone(ctx_of(side, voice, {"intonation": {"f0_std_semitones_min": 6.0}}))                 # the limit is read from the profile


def test_monotone_skips_scenes_with_too_little_voiced_sound(reel):
    side, voice = reel
    assert S.check_monotone(ctx_of(side, None, {"intonation": {"f0_std_semitones_min": 99.0}})) == []
    quiet = np.random.default_rng(0).standard_normal(len(voice)).astype(np.float32) * 1e-4
    assert S.check_monotone(ctx_of(side, quiet, {"intonation": {"f0_std_semitones_min": 99.0}})) == []         # no voiced frames at all


# ---------------------------------------------------------------- pitch jump

def test_pitch_jump_clean_none_injected_found_at_the_right_time(reel):
    side, voice = reel
    assert S.check_pitch_jump(ctx_of(side, voice, load())) == []
    high_side, high_voice = S.synth_reel(seed=1, sr=SR, f0_hz=240.0)           # a downward jump must stay above the tracker's 70 Hz floor to be visible
    for semis, scene in ((14.0, 2), (-14.0, 3), (20.0, 4)):
        sd, vc = (high_side, high_voice) if semis < 0 else (side, voice)
        s2, v2 = S.inject("pitch_jump", sd, vc, SR, scene=scene, semitones=semis)
        voice_ref = vc
        res = S.check_pitch_jump(ctx_of(s2, v2, load()))
        assert len(res) >= 1 and res[0].code == "VOICE_PITCH_JUMP" and res[0].scene == scene, (semis, [f.as_tuple() for f in res])
        changed = np.nonzero(v2 != voice_ref)[0]
        assert abs(res[0].t - changed[0] / SR) < 0.15 and abs(res[0].data["semitones"]) > 12


def test_pitch_jump_limit_and_length_come_from_the_profile(reel):
    side, voice = reel
    s2, v2 = S.inject("pitch_jump", side, voice, SR, scene=2, semitones=14.0)
    assert S.check_pitch_jump(ctx_of(s2, v2, {"intonation": {"pitch_jump_semitones": 18}})) == []              # a 14-semitone step is under an 18 limit
    assert S.check_pitch_jump(ctx_of(s2, v2, {"intonation": {"pitch_jump_semitones": 12}}))
    short, vs = S.inject("pitch_jump", side, voice, SR, scene=2, semitones=14.0, dur_s=0.04)                    # a 40 ms glitch is shorter than the 8 frames asked for
    assert S.check_pitch_jump(ctx_of(short, vs, {"intonation": {"pitch_jump_frames": 8}})) == []


def test_pitch_jumps_function_on_a_track():
    st = np.concatenate([np.zeros(30), np.full(30, 14.0), np.zeros(30)])
    ev = S.pitch_jumps(st, 12.0, 8)
    assert [round(t, 2) for t, _a, _b in ev] == [0.30, 0.60] or len(ev) == 1                                      # up and back; events closer than 0.4 s count once
    assert S.pitch_jumps(np.concatenate([np.zeros(30), np.full(5, 14.0), np.zeros(30)]), 12.0, 8) == []           # too short a stretch
    assert S.pitch_jumps(np.concatenate([np.zeros(30), np.full(30, 8.0)]), 12.0, 8) == []                         # a step under the limit
    gap = np.concatenate([np.zeros(30), [np.nan] * 2, np.full(30, 14.0)])
    assert len(S.pitch_jumps(gap, 12.0, 8, bridge=3)) == 1 and S.pitch_jumps(gap, 12.0, 8, bridge=1) == []         # an unvoiced hole of 2 frames is bridged only when allowed
    weak = np.concatenate([np.zeros(30), np.full(30, 14.0)])
    assert S.pitch_jumps(weak, 12.0, 8, strength=np.full(60, 0.4)) == [] and len(S.pitch_jumps(weak, 12.0, 8, strength=np.full(60, 0.9))) == 1
    glide = np.linspace(0, 40, 120)                                                                                # a fast but continuous glide is not a jump
    assert S.pitch_jumps(glide, 12.0, 8) == []


# ---------------------------------------------------------------- drift

def test_drift_scores_and_detector(reel):
    side, voice = reel
    assert S.check_drift(ctx_of(side)) == []
    s2, _ = S.inject("drift", side, voice, SR, scene=2)
    res = S.check_drift(ctx_of(s2))
    assert [(f.code, f.sev, f.scene) for f in res] == [("VOICE_DRIFT", "WARN", 2)] and res[0].data["cos"] < 0.5 and res[0].data["uncertain"] is True
    assert "guess" in res[0].msg
    assert S.check_drift(ctx_of(s2, profile={"voice": {"drift_cos_min": 0.2}})) == []                         # the limit is read from the profile
    no_emb = {k: v for k, v in s2.items() if k != "scene_embeddings"}
    assert S.check_drift(ctx_of(no_emb)) == []                                                                   # absent -> nothing


def test_drift_scores_edge_cases():
    e = np.eye(4)
    sc = S.drift_scores([e[0], e[0], e[0], e[1]])
    assert sc[:3] == [pytest.approx(1.0)] * 3 and sc[3] == pytest.approx(0.0)
    two = S.drift_scores([e[0], e[1]])                                                                           # no median of two: both get their cosine to each other
    assert two == [pytest.approx(0.0), pytest.approx(0.0)]
    assert S.drift_scores([e[0]]) == [None] and S.drift_scores([]) == [] and S.drift_scores(None) == []
    bad = S.drift_scores([e[0], e[0], [0, 0, 0, 0], [np.nan, 1, 0, 0], [1, 0], e[0]])                           # zero, NaN and wrong-length vectors are skipped
    assert bad[2] is None and bad[3] is None and bad[4] is None and bad[0] == pytest.approx(1.0) and bad[5] == pytest.approx(1.0)
    assert S.drift_scores([[2.0, 0, 0, 0], [5.0, 0, 0, 0], [1.0, 0, 0, 0]]) == [pytest.approx(1.0)] * 3        # scale does not matter
    ctx = ctx_of({"scenes": [{}, {}], "scene_embeddings": [[1, 0], [0, 1]]})
    assert len(S.check_drift(ctx)) == 2                                                                          # two scenes that disagree: both reported (no median to blame one)


# ---------------------------------------------------------------- engine and renderer

def test_engine_fallback():
    base = {"scenes": []}
    assert S.check_engine_fallback(ctx_of({**base, "engine_used": "chatterbox", "engine_wanted": "chatterbox"})) == []
    assert S.check_engine_fallback(ctx_of(base)) == [] and S.check_engine_fallback(ctx_of({**base, "engine_used": "piper"})) == []              # one of the two fields only
    res = S.check_engine_fallback(ctx_of({**base, "engine_used": "piper", "engine_wanted": "chatterbox"}))
    assert [(f.code, f.sev) for f in res] == [("ENGINE_FALLBACK", "ERROR")] and "NOT in your cloned voice" in res[0].msg
    res = S.check_engine_fallback(ctx_of({**base, "engine_used": "sapi", "engine_wanted": "piper"}))
    assert [(f.code, f.sev) for f in res] == [("ENGINE_FALLBACK", "WARN")]                                      # a stock voice for a stock voice: the voice differs
    res = S.check_engine_fallback(ctx_of({**base, "engine_used": "Piper ", "engine_wanted": " CHATTERBOX"}))
    assert res and res[0].sev == "ERROR"                                                                         # names are compared without case and spaces
    res = S.check_engine_fallback(ctx_of({**base, "engine_used": ["chatterbox", "piper", "chatterbox"], "engine_wanted": "chatterbox"}))
    assert len(res) == 1 and res[0].scene == 2 and res[0].sev == "ERROR"                                         # per scene
    assert S.check_engine_fallback(ctx_of({**base, "engine_used": ["chatterbox", "chatterbox"], "engine_wanted": "chatterbox"})) == []


def test_render_fallback():
    assert S.check_render_fallback(ctx_of({"renderer": "remotion"})) == [] and S.check_render_fallback(ctx_of({})) == []
    res = S.check_render_fallback(ctx_of({"renderer": "pil"}))
    assert [(f.code, f.sev) for f in res] == [("RENDER_FALLBACK", "WARN")] and "Remotion" in res[0].msg
    assert S.check_render_fallback(ctx_of({"renderer": "pil", "simple": True})) == []                           # the author asked for the simple renderer
    assert S.check_render_fallback(ctx_of({"renderer": "pil", "params": {"simple": True}})) == []
    assert S.check_render_fallback(ctx_of({"renderer": "pil", "renderer_wanted": "pil"})) == []
    assert S.check_render_fallback(ctx_of({"renderer": "pil", "renderer_wanted": "remotion"}))
    assert S.check_render_fallback(ctx_of({"renderer": "PIL"}))


# ---------------------------------------------------------------- the whole matrix on synthetic speech

@pytest.mark.parametrize("seed,scene", [(1, 2), (5, 3)])
def test_every_injected_fault_is_reported_with_its_code_and_nothing_else(seed, scene):
    side, voice = S.synth_reel(seed=seed, sr=SR)
    assert loud(run(side, voice)) == []
    for kind, code in S.INJECTIONS.items():
        s2, v2 = S.inject(kind, side, voice, SR, scene=scene, seed=seed)
        res = run(s2, v2)
        hit = [f for f in loud(res) if f.code == code]
        assert hit, (kind, [f.as_tuple() for f in res])
        assert codes(res) == {code}, (kind, [f.as_tuple() for f in loud(res)])                                    # no collateral error from another detector
        if kind not in ("engine_fallback", "render_fallback"):
            assert any(f.scene == scene for f in hit), (kind, [(f.code, f.scene) for f in hit])
    assert S.inject.__doc__ and set(S.INJECTIONS) == {"sub", "del", "ins_repeat", "ins_hallucination", "pause_inner", "monotone", "pitch_jump", "drift",
                                                       "engine_fallback", "render_fallback"}


def test_inject_does_not_touch_its_inputs(reel):
    side, voice = reel
    before, vbefore = json.dumps(side, sort_keys=True), voice.copy()
    for kind in S.INJECTIONS:
        S.inject(kind, side, voice, SR)
    assert json.dumps(side, sort_keys=True) == before and np.array_equal(voice, vbefore)
    with pytest.raises(KeyError):
        S.inject("nonsense", side, voice, SR)


def test_flatten_pitch_keeps_length_and_maps_times(reel):
    side, voice = reel
    a, b = S._span(side["scenes"][0])
    seg = voice[int(a * SR):int(b * SR)]
    y, warp = S.flatten_pitch(seg, SR)
    assert len(y) == len(seg) and 0.0 <= float(warp(0.0)) < 0.05 and abs(float(warp(len(seg) / SR)) - len(seg) / SR) < 0.1
    assert np.all(np.diff(warp(np.linspace(0, len(seg) / SR, 50))) > 0)                                      # time still runs forward
    f_in, f_out = S.f0_semitones(S.f0_track(seg, SR)), S.f0_semitones(S.f0_track(y, SR))
    assert np.nanstd(f_out) < 0.4 * np.nanstd(f_in)
    z, w2 = S.flatten_pitch(np.zeros(SR // 2), SR)                                                               # nothing voiced: unchanged
    assert len(z) == SR // 2 and float(w2(0.25)) == 0.25


# ---------------------------------------------------------------- real material

def excerpt(path, a, b):
    with wave.open(str(path)) as w:
        sr = w.getframerate()
        w.setpos(int(a * sr))
        x = np.frombuffer(w.readframes(int((b - a) * sr)), dtype=np.int16).astype("float32") / 32768.0
    return x, sr


def ted_scenes(folder, a, b, limit=8):
    """scenes (whole sentences with word times, relative to a) from the speech recogniser's transcript of a TED talk"""
    tr = json.loads((TED / folder / "transcript.json").read_text(encoding="utf-8"))
    out, cur = [], []
    for seg in tr["segments"]:
        for w in seg.get("words", []):
            if w["s"] < a or w["e"] > b:
                cur = []
                continue
            cur.append([w["w"], round(w["s"] - a, 3), round(w["e"] - a, 3)])
            if w["w"].rstrip().endswith((".", "?", "!")):
                if len(cur) >= 4 and 1.2 <= cur[-1][2] - cur[0][1] <= 14:
                    out.append({"text": " ".join(x[0] for x in cur), "start": cur[0][1], "end": cur[-1][2], "words": cur, "heard_words": [x[0] for x in cur]})
                cur = []
    return out[:limit]


REAL = [("yt_MYH2qGScEVk", 300.0, 345.0), ("yt_ktjMz7c3ke4", 200.0, 245.0)]
need_ted = pytest.mark.skipif(not all((TED / f / "audio.wav").is_file() and (TED / f / "transcript.json").is_file() for f, _a, _b in REAL), reason="the TED recordings are not on this machine")


@need_ted
@pytest.mark.parametrize("folder,a,b", REAL)
def test_real_speech_f0_is_plausible_and_gives_no_false_alarm(folder, a, b):
    x, sr = excerpt(TED / folder / "audio.wav", a, b)
    scenes = ted_scenes(folder, a, b)
    assert len(scenes) >= 3
    side = {"scenes": scenes}
    ctx = ctx_of(side, x, load(), sr)
    f0, st, strength = S._f0_of(ctx)
    assert 80 < np.nanmedian(f0) < 200 and 0.3 < np.mean(~np.isnan(f0)) < 0.8                                 # a man's voice, speech and pauses
    res = []
    for d in S.DETECTORS:
        res += d(ctx)
    assert not [f for f in res if f.sev == "ERROR"], [f.as_tuple() for f in res]
    minutes = (b - a) / 60
    assert len([f for f in res if f.sev == "WARN"]) <= minutes, [f.as_tuple() for f in res]                    # at most one warning a minute on clean real speech
    assert not [f for f in res if f.code == "VOICE_PITCH_JUMP"]
    stds = [S.scene_f0_std(st, *S._span(sc)) for sc in scenes]
    assert all(s is not None and 0.8 < s < 9.0 for s in stds), stds                                           # real speakers: about 1.3-7 semitones a sentence (docs/QA.md)


@need_ted
def test_real_speech_injected_faults_are_found_and_flat_real_speech_is_flagged():
    folder, a, b = REAL[0]
    x, sr = excerpt(TED / folder / "audio.wav", a, b)
    side = {"scenes": ted_scenes(folder, a, b, limit=5)}
    prof = {"intonation": {"f0_std_semitones_min": 2.0}}                                                      # flattening leaves ~1.8 semitones of tracker residue; clean scenes are 2.3-5.3
    assert loud(S.run_detectors(side, x, sr, prof)) == []
    for kind in ("pause_inner", "monotone", "pitch_jump", "sub", "del", "ins_repeat", "ins_hallucination"):
        s2, v2 = S.inject(kind, side, x, sr, scene=3)
        res = S.run_detectors(s2, v2, sr, prof)
        assert codes(res) == {S.INJECTIONS[kind]}, (kind, [f.as_tuple() for f in loud(res)])
        assert all(f.scene == 3 for f in loud(res)), kind


ROLKA_OK = (ROLKA / "rolka_a.qa.json").is_file() and (ROLKA / "rolka_a_voice.wav").is_file()


@pytest.mark.skipif(not ROLKA_OK, reason="the generated reel is not on this machine")
def test_real_generated_reel_gets_no_voice_fault():
    side = json.loads((ROLKA / "rolka_a.qa.json").read_text(encoding="utf-8"))
    with wave.open(str(ROLKA / "rolka_a_voice.wav")) as w:
        sr = w.getframerate()
        v = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768.0
    res = S.run_detectors(side, v, sr, load())
    bad = loud(res)
    assert [(f.code, f.scene) for f in bad] == []                                                              # "Trzydzieści" was heard as "30": a recogniser's way of writing a number, not a mispronunciation
    assert [f for f in res if f.code == "VOICE_MONOTONE"] == []                                                 # the calibrated limit (1.2 st on 3 s windows of real speech) does not flag the cloned voice
    mono = [f for f in S.run_detectors(side, v, sr, uncalibrated()) if f.code == "VOICE_MONOTONE"]              # without a limit it only informs: the measured numbers
    assert len(mono) == 1 and mono[0].sev == "INFO" and len(mono[0].data["std_semitones"]) == 6
    assert all(2.0 < s < 9.0 for s in mono[0].data["std_semitones"].values())                                  # the cloned voice is lively: 4-6 semitones a sentence


def test_a_number_written_in_digits_is_not_a_mispronounced_numeral():
    r = S.classify_ops(["Trzydzieści", "rolek", "konkurencji"], ["30", "rolek", "konkurencji"])
    assert r["sub"] == [] and r["del"] == [] and r["ins"] == [] and r["eq"] == 3
    r = S.classify_ops(["thirty", "reels"], ["thirty", "reels"])
    assert r["eq"] == 2
    r = S.classify_ops(["Trzydzieści", "rolek"], ["31", "rolek"])                       # another number is another word
    assert [s[0] for s in r["sub"]] == ["Trzydzieści"]
