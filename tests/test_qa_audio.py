"""quality/audio.py: the pauses between scenes, a sentence cut off or dragged out, breaths, level jumps, hiss, speaking rate and the sound-event model.
Every detector gets a clean reel that must stay silent and a reel with that one fault in it that must be found with the right code."""
import numpy as np
import pytest

from reel_intel.quality import audio as A
from reel_intel.quality.base import REGISTRY, Ctx
from reel_intel.quality.profile import load

SR = 24000


def t(sec):
    return np.arange(int(sec * SR)) / SR


def tone(sec, db=-18.0, f=180.0):
    return (np.sin(2 * np.pi * f * t(sec)) * 10 ** (db / 20) * 1.41).astype("float32")


def speechlike(sec, db=-18.0, syl_s=0.19, seed=0):
    """a voice-ish signal: a 160 Hz carrier with a syllable-rate envelope (one bump every syl_s), so the nuclei detector counts it"""
    rng = np.random.default_rng(seed)
    x = np.zeros(int(sec * SR), dtype="float32")
    k = 0
    for i in range(int(sec / syl_s)):
        n = int(syl_s * 0.75 * SR)
        a = int(i * syl_s * SR)
        env = np.hanning(n)
        f = 150 + 40 * rng.random()
        seg = (np.sin(2 * np.pi * f * np.arange(n) / SR) + 0.6 * np.sin(2 * np.pi * 2.3 * f * np.arange(n) / SR) + 0.3 * np.sin(2 * np.pi * 5.1 * f * np.arange(n) / SR)) * env
        x[a:a + n] += seg.astype("float32")
        k += 1
    x = x / (np.sqrt(np.mean(x ** 2)) + 1e-9) * 10 ** (db / 20)
    return x


def reel(n=3, gap=0.3, scene_s=1.6, db=-18.0, gap_sound=None, level_step=0.0, words_each=5):
    """voice track, final mix and sidecar of `n` scenes: tone 'sentences' separated by gaps"""
    parts, scenes, pos = [np.zeros(int(0.3 * SR), dtype="float32")], [], 0.3
    for i in range(n):
        parts.append(tone(scene_s, db + level_step * i))
        step = scene_s / words_each
        ws = [[f"w{k}", round(pos + k * step, 3), round(pos + (k + 1) * step - 0.02, 3)] for k in range(words_each)]
        scenes.append({"text": "to jest jedno zdanie testowe", "words": ws, "start": pos, "end": pos + scene_s + min(0.15, gap * 0.5), "heard": 1.0, "tail": 0.1})
        pos += scene_s
        if i < n - 1:
            parts.append(np.zeros(int(gap * SR), dtype="float32") if gap_sound is None else gap_sound(gap))
            pos += gap
    x = np.concatenate(parts)
    return x, {"scenes": scenes}


def ctx_of(x, side, voice=None, ears=None, profile=None):
    return Ctx(side=side, final=x, sr=SR, voice=x if voice is None else voice, profile=profile if profile is not None else load(), ears=ears)


def codes(findings, sev=("ERROR", "WARN")):
    return {f.code for f in findings if f.sev in sev}


def run(ctx):
    out = []
    for d in A.DETECTORS:
        out += d(ctx)
    return out


# ---------------------------------------------------------------- the register
def test_every_audio_code_is_registered():
    for code in ("GAP_LONG", "GAP_SHORT", "GAP_LOUD", "GAP_RUMBLE", "GAP_CLICK", "VOICE_CUT", "VOICE_DRONE", "VOICE_BREATH", "VOICE_LEVEL", "VOICE_HISS", "VOICE_TAIL",
                 "VOICE_FAST", "VOICE_SLOW", "VOICE_BURST", "PACE", "EAR_BREATH", "EARS_OFF"):
        assert code in REGISTRY and REGISTRY[code]["desc"] and REGISTRY[code]["module"] == "audio", code


def test_every_detector_has_a_docstring():
    for d in A.DETECTORS:
        assert (d.__doc__ or "").strip(), d.__name__


# ---------------------------------------------------------------- clean reel
def test_a_clean_reel_is_silent():
    x, side = reel()
    out = run(ctx_of(x, side))
    assert codes(out) == set(), [(f.code, f.msg) for f in out]


# ---------------------------------------------------------------- gaps
def test_a_long_gap_is_found():
    x, side = reel(gap=1.5)
    f = [f for f in A.check_gaps(ctx_of(x, side)) if f.code == "GAP_LONG"]
    assert len(f) == 2 and f[0].scene == 1 and f[0].data["gap_s"] == pytest.approx(1.5 + 0.02, abs=0.05)


def test_a_gap_too_short_is_found():
    x, side = reel(gap=0.05)
    assert "GAP_SHORT" in codes(A.check_gaps(ctx_of(x, side)))


def test_music_as_loud_as_the_voice_in_the_gap_is_found():
    x, side = reel(gap=0.5, gap_sound=lambda g: tone(g, -20, 900))
    f = [f for f in A.check_gaps(ctx_of(x, side)) if f.code == "GAP_LOUD"]
    assert f and f[0].data["under_voice_db"] < 8


def test_a_quiet_bed_in_the_gap_is_normal():
    x, side = reel(gap=0.5, gap_sound=lambda g: tone(g, -38, 900))
    assert "GAP_LOUD" not in codes(A.check_gaps(ctx_of(x, side)))


def test_rumble_in_the_gap_is_found():
    x, side = reel(gap=0.5, gap_sound=lambda g: tone(g, -22, 50))
    assert "GAP_RUMBLE" in codes(A.check_gaps(ctx_of(x, side)))


def test_a_click_in_the_gap_is_found():
    def clicky(g):
        s = np.zeros(int(g * SR), dtype="float32")
        s[len(s) // 2:len(s) // 2 + 3] = [0.7, -0.7, 0.7]
        return s
    x, side = reel(gap=0.5, gap_sound=clicky)
    assert "GAP_CLICK" in codes(A.check_gaps(ctx_of(x, side)))


def test_gaps_need_two_scenes_and_a_mix():
    x, side = reel(n=1)
    assert A.check_gaps(ctx_of(x, side)) == []
    c = ctx_of(x, side)
    c.final = None
    assert A.check_gaps(c) == []


# ---------------------------------------------------------------- the end of a scene
def test_a_sentence_chopped_at_the_end_is_an_error():
    x, side = reel()
    for sc in side["scenes"]:
        sc["end"] -= 0.4                                              # the scene's sound ends while the voice is still going
    f = A.check_voice_cut(ctx_of(x, side))
    assert f and all(q.sev == "ERROR" and q.code == "VOICE_CUT" for q in f)


def test_a_sentence_that_ends_in_silence_is_not_cut():
    x, side = reel()
    assert A.check_voice_cut(ctx_of(x, side)) == []


def test_a_dragged_last_word_is_found_and_names_the_word():
    x, side = reel()
    w = side["scenes"][0]["words"][-1]
    w[2] = w[1] + 2.6
    f = A.check_voice_drone(ctx_of(x, side))
    assert len(f) == 1 and f[0].scene == 1 and f[0].data["word"] == w[0] and f[0].data["duration"] == pytest.approx(2.6)


def test_the_drone_limit_comes_from_the_profile():
    x, side = reel()
    w = side["scenes"][0]["words"][-1]
    w[2] = w[1] + 1.3
    assert A.check_voice_drone(ctx_of(x, side))
    p = load()
    p["voice"]["last_word_max_s"] = 2.0
    assert A.check_voice_drone(ctx_of(x, side, profile=p)) == []


# ---------------------------------------------------------------- breaths
def test_a_breath_after_the_last_word_is_found():
    x, side = reel(gap=0.6)
    rng = np.random.default_rng(3)
    word_end = side["scenes"][0]["words"][-1][2]
    k = int((word_end + 0.1) * SR)
    x = x.copy()
    x[k:k + int(0.25 * SR)] += (rng.standard_normal(int(0.25 * SR)) * 10 ** (-34 / 20)).astype("float32")
    side["scenes"][0]["end"] = word_end + 0.45
    f = [q for q in A.check_breath(ctx_of(x, side)) if q.scene == 1]
    assert f and f[0].code == "VOICE_BREATH" and f[0].data["where"] in ("after", "tail")


def test_a_breathy_tail_trailing_off_the_last_word_is_found():
    x, side = reel(gap=0.6)
    k = int(side["scenes"][1]["words"][-1][2] * SR)
    x = x.copy()
    x[k:k + int(0.4 * SR)] += tone(0.4, -48, 220)[:int(0.4 * SR)]
    side["scenes"][1]["end"] = side["scenes"][1]["words"][-1][2] + 0.5
    f = [q for q in A.check_breath(ctx_of(x, side)) if q.scene == 2]
    assert f and f[0].data["where"] == "tail" and f[0].data["seconds"] > 0.12


def test_a_breath_between_words_is_found():
    x, side = reel(gap=0.5, scene_s=2.4, words_each=4)
    ws = side["scenes"][0]["words"]
    ws[2][1] += 0.5                                                    # a half second hole between words 2 and 3
    ws[3][1] += 0.5
    ws[2][2] += 0.5 - 0.02
    rng = np.random.default_rng(5)
    a = int((ws[1][2] + 0.1) * SR)
    x = x.copy()
    x[a:a + int(0.3 * SR)] = (rng.standard_normal(int(0.3 * SR)) * 10 ** (-33 / 20)).astype("float32")
    f = [q for q in A.check_breath(ctx_of(x, side)) if q.scene == 1 and q.data["where"] == "inside"]
    assert f


def test_tones_with_clean_silence_between_are_not_breaths():
    x, side = reel(gap=0.6)
    assert A.check_breath(ctx_of(x, side)) == []


# ---------------------------------------------------------------- level and hiss
def test_a_level_jump_between_scenes_is_found():
    x, side = reel(level_step=-9.0)
    f = A.check_voice_level(ctx_of(x, side))
    assert f and f[0].scene == 2 and f[0].data["jump_db"] > 8


def test_small_level_differences_pass():
    x, side = reel(level_step=-2.0)
    assert A.check_voice_level(ctx_of(x, side)) == []


def test_hiss_under_the_voice_is_found():
    x, side = reel(gap=0.8)
    rng = np.random.default_rng(1)
    noise = (rng.standard_normal(len(x)) * 10 ** (-38 / 20)).astype("float32")
    for sc in side["scenes"]:
        sc["end"] = sc["words"][-1][2] + 0.6
    f = A.check_voice_hiss(ctx_of(x, side, voice=x + noise))
    assert f and f[0].data["level_db"] > -46


def test_a_silent_background_has_no_hiss():
    x, side = reel(gap=0.8)
    for sc in side["scenes"]:
        sc["end"] = sc["words"][-1][2] + 0.6
    assert A.check_voice_hiss(ctx_of(x, side)) == []


def test_a_long_silent_tail_is_only_information():
    x, side = reel()
    side["scenes"][0]["tail"] = 0.9
    f = A.check_voice_tail(ctx_of(x, side))
    assert [(q.code, q.sev) for q in f] == [("VOICE_TAIL", "INFO")]


# ---------------------------------------------------------------- the speaking rate
def paced(n_scenes, syl_s, words=7, sec=2.4):
    parts, scenes, pos = [np.zeros(int(0.3 * SR), dtype="float32")], [], 0.3
    for i in range(n_scenes):
        parts.append(speechlike(sec, -18, syl_s, seed=i))
        step = sec / words
        scenes.append({"text": "to jest zdanie " + " ".join(["ma"] * (words - 3)), "words": [[f"w{k}", pos + k * step, pos + (k + 1) * step - 0.02] for k in range(words)],
                       "start": pos, "end": pos + sec + 0.1})
        pos += sec
        parts.append(np.zeros(int(0.3 * SR), dtype="float32"))
        pos += 0.3
    return np.concatenate(parts), {"scenes": scenes}


def test_the_syllable_counter_follows_the_real_rate():
    for syl_s, lo, hi in ((0.25, 3.3, 4.7), (0.18, 4.8, 6.3), (0.125, 7.0, 9.0)):
        x, _ = paced(1, syl_s)
        peaks = A.nuclei(x, SR)
        r = A.rate_in(peaks, 0.3, 2.7)
        assert lo <= r <= hi, (syl_s, r)


def test_normal_pace_is_silent_and_reports_the_average():
    x, side = paced(2, 0.19)
    out = A.check_pace(ctx_of(x, side))
    assert codes(out) == set() and [f.code for f in out] == ["PACE"]


def test_a_rushed_scene_is_found():
    x, side = paced(2, 0.1)
    f = [q for q in A.check_pace(ctx_of(x, side)) if q.code == "VOICE_FAST"]
    assert f and f[0].data["heard"] > A._PROFILE_DEFAULT["pace"]["fast"]


def test_a_rushed_scene_by_the_words_alone_is_found():
    x, side = paced(1, 0.19)
    sc = side["scenes"][0]
    sc["words"] = [[w, 0.3 + (a - 0.3) * 0.4, 0.3 + (b - 0.3) * 0.4] for w, a, b in sc["words"]]       # the same sentence squeezed into 40% of the time
    assert "VOICE_FAST" in codes(A.check_pace(ctx_of(x, side)))


def test_a_dragging_scene_needs_both_counts_under_the_limit():
    x, side = paced(1, 0.5, words=5, sec=3.0)
    out = A.check_pace(ctx_of(x, side))
    assert "VOICE_SLOW" in codes(out)
    x2, side2 = paced(1, 0.19, words=5, sec=3.0)
    side2["scenes"][0]["text"] = "a"                                                                    # the words say slow, the sound says normal
    assert "VOICE_SLOW" not in codes(A.check_pace(ctx_of(x2, side2)))


def test_a_rushed_stretch_inside_a_normal_sentence_is_found():
    a, side = paced(1, 0.19, sec=3.0)
    fast = speechlike(1.2, -18, 0.09, seed=9)
    x = a.copy()
    s0 = int(1.2 * SR)
    x[s0:s0 + len(fast)] = fast[:len(x) - s0]
    out = A.check_pace(ctx_of(x, side))
    assert "VOICE_BURST" in codes(out) or "VOICE_FAST" in codes(out)


def test_pace_limits_come_from_the_profile():
    x, side = paced(1, 0.1)
    p = load()
    p["pace"]["fast"] = 20.0
    assert "VOICE_FAST" not in codes(A.check_pace(ctx_of(x, side, profile=p)))


# ---------------------------------------------------------------- the sound-event model
def ears(*wins):
    return {"window": 1.0, "hop": 0.5, "windows": [{"t": t0, "labels": labs} for t0, labs in wins]}


def test_ears_flag_a_breath_inside_a_scene_and_between_scenes():
    x, side = reel(gap=1.5)                                             # scene 1 ends at 2.05 s, scene 2 starts at 3.4 s
    e = ears((0.5, [["Speech", 0.9], ["Gasp", 0.2]]), (1.0, [["Speech", 0.9], ["Music", 0.5]]), (1.9, [["Music", 0.6], ["Breathing", 0.09]]))
    f = A.check_ears(ctx_of(x, side, ears=e))
    assert [q.code for q in f] == ["EAR_BREATH", "EAR_BREATH"]
    assert f[0].scene == 1 and f[0].data["label"] == "Gasp" and f[1].scene is None and f[1].data["where"] == "between"


def test_ears_with_only_speech_and_music_are_silent():
    x, side = reel()
    assert A.check_ears(ctx_of(x, side, ears=ears((0.0, [["Speech", 0.9], ["Music", 0.5]])))) == []


def test_without_ears_it_says_so_once():
    x, side = reel()
    f = A.check_ears(ctx_of(x, side))
    assert [(q.code, q.sev) for q in f] == [("EARS_OFF", "INFO")]


def test_two_windows_of_one_breath_make_one_finding():
    x, side = reel()
    e = ears((0.2, [["Gasp", 0.3]]), (0.7, [["Gasp", 0.4]]))
    assert len(A.check_ears(ctx_of(x, side, ears=e))) == 1


# ---------------------------------------------------------------- degraded input
def test_scenes_without_word_times_are_skipped_not_a_crash():
    x, side = reel()
    side["scenes"].insert(0, {"text": "x", "start": 0.0, "end": 0.2, "words": []})
    side["scenes"].append({"text": "y"})
    out = run(ctx_of(x, side))
    assert codes(out) == set()


def test_no_voice_track_falls_back_to_the_final_mix():
    x, side = reel(level_step=-9.0)
    c = ctx_of(x, side)
    c.voice = None
    assert A.check_voice_level(c)


def test_no_audio_at_all_returns_nothing():
    x, side = reel()
    c = ctx_of(x, side)
    c.voice = c.final = None
    for d in (A.check_voice_cut, A.check_voice_level, A.check_voice_hiss, A.check_breath, A.check_pace, A.check_gaps):
        assert d(c) == []
