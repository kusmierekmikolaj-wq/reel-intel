"""quality/sync.py: caption sync, lip sync and the container's start times. Pure arithmetic on a synthetic reel, plus real speech where the files exist.

Every detector has (a) a clean signal that must give no ERROR/WARN and (b) injected faults that must be caught with the right code. The injections are
sync.inject(kind, case); the expected code for each kind is the EXPECT table below, which the lead's fault matrix can import.
"""
import os
import json
import wave
from pathlib import Path

import numpy as np
import pytest

from reel_intel.quality import sync
from reel_intel.quality.base import REGISTRY, Ctx
from reel_intel.quality.profile import load

REPAIRS = {None, "retake_scene", "respell", "slow_scene", "retrim_scene", "remix", "master", "relayout", "rerender"}

# a real Polish reel made by `reel-intel make` (rolka_a): the words the captions were drawn with, and the words faster-whisper (large-v3) recognised from the
# finished mp4 (voice + music, AAC 48 kHz), recorded once; "30" for "Trzydzieści" is a real recognition difference
SCENES = json.loads('''[[["Trzydzieści",0.4,0.76],["rolek",0.76,1.28],["konkurencji,",1.28,1.88],["a",1.88,1.98],["ty",1.98,2.08],["nie",2.08,2.24],["obejrzałeś",2.24,2.7],["żadnej.",2.7,3.22]],[["Program",3.6,3.92],["słucha,",3.92,4.42],["co",4.42,4.6],["autor",4.6,4.88],["mówi,",4.88,5.18],["i",5.18,5.42],["zamienia",5.42,5.9],["to",5.9,6.0],["na",6.0,6.14],["tekst.",6.14,6.99]],[["Czyta",7.37,7.63],["też",7.63,7.75],["ekran,",7.75,8.17],["więc",8.17,8.43],["łapie",8.43,8.73],["nazwy",8.73,9.03],["skilli",9.03,9.39],["i",9.39,9.44],["komendy.",9.43,9.99]],[["Wszystko",10.37,10.81],["ląduje",10.81,11.19],["w",11.19,11.27],["jednym",11.27,11.59],["pliku",11.59,11.93],["dla",11.93,12.07],["twojego",12.07,12.49],["agenta",12.49,12.99],["AI.",12.99,13.67]],[["Ty",14.05,14.17],["pytasz,",14.17,14.59],["co",14.59,14.73],["z",14.73,14.89],["tego",14.89,15.03],["da",15.03,15.19],["się",15.19,15.33],["wykorzystać",15.33,15.93],["u",15.93,15.99],["ciebie.",15.99,16.84]],[["Napisz",17.22,17.58],["w",17.58,17.63],["komentarzu",17.62,18.12],["TEST,",18.12,18.28],["a",18.48,18.62],["wyślę",18.62,18.92],["ci",18.92,19.0],["cały",19.0,19.2],["schemat.",19.2,20.05]]]''')
REHEAR = json.loads('''[["30",0.0,0.72],["rolek",0.72,1.28],["konkurencji,",1.28,1.88],["a",1.9,2.02],["Ty",2.02,2.08],["nie",2.08,2.24],["obejrzałeś",2.24,2.7],["żadnej.",2.7,3.12],["Program",3.28,3.84],["słucha",3.84,4.38],["co",4.38,4.58],["autor",4.58,4.9],["mówi",4.9,5.18],["i",5.18,5.46],["zamienia",5.46,5.86],["to",5.86,5.98],["na",5.98,6.14],["tekst.",6.14,6.56],["Czyta",7.08,7.64],["też",7.64,7.74],["ekran,",7.74,8.16],["więc",8.18,8.44],["łapie",8.44,8.72],["nazwy",8.72,9.04],["skilli",9.04,9.38],["i",9.38,9.44],["komendy.",9.44,9.88],["Wszystko",10.22,10.78],["ląduje",10.78,11.2],["w",11.2,11.3],["jednym",11.3,11.6],["pliku",11.6,11.92],["dla",11.92,12.08],["Twojego",12.08,12.48],["agenta",12.48,12.98],["AI.",12.98,13.26],["Ty",13.66,14.12],["pytasz",14.12,14.58],["co",14.58,14.74],["z",14.74,14.88],["tego",14.88,15.02],["da",15.02,15.18],["się",15.18,15.32],["wykorzystać",15.32,15.96],["u",15.96,16.0],["Ciebie?",16.0,16.3],["Napisz",17.02,17.58],["w",17.58,17.64],["komentarzu",17.64,18.12],["test,",18.12,18.3],["a",18.5,18.64],["wyślę",18.64,18.9],["Ci",18.9,19.0],["cały",19.0,19.2],["schemat.",19.2,19.6]]''')

EXPECT = {                      # fault kind -> the code that must be reported (None: must NOT be reported, a documented limit)
    "caption_late": "CAPTION_SYNC", "caption_early": "CAPTION_SYNC", "caption_drift": "CAPTION_SYNC", "caption_scene": "CAPTION_SYNC",
    "caption_outlier": None,
    "audio_early": "LIPSYNC", "audio_late": "LIPSYNC", "mix_delay": "LIPSYNC", "mouth_flat": "LIPSYNC", "mouth_noise": "LIPSYNC",
    "av_audio_early": "AV_OFFSET", "av_audio_late": "AV_OFFSET",
}
TED = sorted(Path(r"C:\Users\Jeff\Documents\reel-intel\_dane\wer-real\data").glob("*/audio.wav"))
ROLKA = Path(os.environ.get("REEL_TEST_ROLKI", "no-such-folder"))


def run(case, profile=None):
    ctx = sync.as_ctx(case, profile)
    return [f for d in sync.DETECTORS for f in d(ctx)]


def bad(res):
    return [f for f in res if f.sev in ("ERROR", "WARN")]


@pytest.fixture(scope="module")
def clean():
    return sync.synthetic_case(0)


# ---------------------------------------------------------------- registry

def test_codes_are_registered_with_a_known_repair():
    for code in ("CAPTION_SYNC", "LIPSYNC", "AV_OFFSET"):
        r = REGISTRY[code]
        assert r["module"] == "sync" and r["desc"] and r["repair"] in REPAIRS and r["sev"] in ("ERROR", "WARN", "INFO")


def test_every_injection_kind_has_an_expected_code():
    assert set(EXPECT) == set(sync.KINDS)


# ---------------------------------------------------------------- clean signals give nothing

@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_clean_synthetic_reel_gives_no_error_or_warn(seed):
    res = run(sync.synthetic_case(seed))
    assert not bad(res), [f.msg for f in bad(res)]
    assert {f.code for f in res} == {"CAPTION_SYNC", "LIPSYNC", "AV_OFFSET"}         # each detector said something (INFO) with its numbers


def test_clean_reel_info_lines_carry_the_numbers(clean):
    res = {f.code: f for f in run(clean)}
    assert res["CAPTION_SYNC"].data["median_abs_ms"] < 40 and res["CAPTION_SYNC"].data["n_matched"] == res["CAPTION_SYNC"].data["n_words"]
    assert res["LIPSYNC"].data["corr0"] > 0.8 and abs(res["LIPSYNC"].data["offset_ms"]) < 20
    assert res["AV_OFFSET"].data["offset_ms"] == 0


# ---------------------------------------------------------------- injected faults

@pytest.mark.parametrize("kind", sorted(EXPECT))
def test_injected_fault_is_caught_with_the_right_code(kind, clean):
    res = run(sync.inject(kind, clean))
    got = {f.code for f in bad(res)}
    assert got == ({EXPECT[kind]} if EXPECT[kind] else set()), [f.msg for f in res]


def test_inject_does_not_touch_its_input(clean):
    before = json.dumps(clean["side"])
    for kind in sync.KINDS:
        sync.inject(kind, clean)
    assert json.dumps(clean["side"]) == before


def test_inject_rejects_an_unknown_kind(clean):
    with pytest.raises(KeyError):
        sync.inject("no_such_fault", clean)


# ---------------------------------------------------------------- CAPTION_SYNC

def test_caption_sync_is_silent_without_rehear(clean):
    case = {**clean, "side": {k: v for k, v in clean["side"].items() if k != "rehear"}}
    assert sync.check_caption_sync(sync.as_ctx(case)) == []


def test_caption_sync_needs_enough_matched_words(clean):
    case = sync.inject("caption_late", clean)
    case["side"]["rehear"] = case["side"]["rehear"][:3]
    (f,) = sync.check_caption_sync(sync.as_ctx(case))
    assert f.sev == "INFO" and f.data["n_matched"] == 3


def test_caption_shift_is_reported_with_its_sign(clean):
    late = sync.check_caption_sync(sync.as_ctx(sync.inject("caption_late", clean, ms=150)))[0]
    early = sync.check_caption_sync(sync.as_ctx(sync.inject("caption_early", clean, ms=150)))[0]
    assert late.data["shift_ms"] < -100 < 100 < early.data["shift_ms"]            # shift = heard - caption: captions late -> negative
    assert "after the voice" in late.msg and "before the voice" in early.msg


def test_caption_scene_block_names_the_scene(clean):
    res = sync.check_caption_sync(sync.as_ctx(sync.inject("caption_scene", clean, scene=3, ms=300)))
    assert [f.scene for f in res if f.sev == "WARN"] == [3]


def test_a_single_wrong_word_is_reported_but_not_judged(clean):
    (f,) = sync.check_caption_sync(sync.as_ctx(sync.inject("caption_outlier", clean, ms=400)))
    assert f.sev == "INFO" and f.data["max_abs_ms"] > 350


def test_caption_median_limit_comes_from_the_profile(clean):
    case = sync.inject("caption_late", clean, ms=150)
    assert not bad(sync.check_caption_sync(Ctx(side=case["side"], profile={"sync": {"caption_ms": 400}})))
    assert bad(sync.check_caption_sync(Ctx(side=case["side"], profile={"sync": {"caption_ms": 100}})))
    assert bad(sync.check_caption_sync(Ctx(side=case["side"], profile={})))             # no profile: profile.DEFAULT (80 ms)


def test_match_words_ignores_case_punctuation_and_pairs_near_spellings():
    cap = [["Trzydzieści", 0, 1], ["ROLEK,", 1, 2], ["skilli", 2, 3], ["—", 3, 3.1], ["koniec.", 3.1, 4]]
    heard = [["rolek", 1.1, 2], ["skili", 2.1, 3], ["koniec", 3.2, 4]]
    pairs = sync.match_words(cap, heard)
    assert pairs == [(1, 0), (2, 1), (4, 2)]
    assert sync.match_words(cap, []) == [] and sync.match_words([], heard) == []


def test_real_polish_reel_recognition_gives_no_warn():
    side = {"scenes": [{"words": w} for w in SCENES], "rehear": REHEAR}
    (f,) = sync.check_caption_sync(Ctx(side=side, profile=load()))
    assert f.sev == "INFO", f.msg
    assert f.data["n_matched"] >= 50 and f.data["median_abs_ms"] <= 40 and f.data["max_abs_ms"] > 200       # real recogniser jitter: big maximum, small median


def test_real_polish_reel_faults_are_caught():
    side = {"scenes": [{"words": w} for w in SCENES], "rehear": [[w, a - 0.2, b - 0.2] for w, a, b in REHEAR]}
    assert sync.check_caption_sync(Ctx(side=side, profile=load()))[0].sev == "WARN"
    blk = [[w, a - 0.3, b - 0.3] if 7.3 <= a <= 10.0 else [w, a, b] for w, a, b in REHEAR]            # scene 3 only
    res = sync.check_caption_sync(Ctx(side={"scenes": [{"words": w} for w in SCENES], "rehear": blk}, profile=load()))
    assert [f.scene for f in res if f.sev == "WARN"] == [3]


# ---------------------------------------------------------------- LIPSYNC

def test_lipsync_sign_and_size_of_the_lag(clean):
    early = [f for f in run(sync.inject("audio_early", clean, ms=100)) if f.code == "LIPSYNC"][0]
    late = [f for f in run(sync.inject("audio_late", clean, ms=150)) if f.code == "LIPSYNC"][0]
    assert early.sev == late.sev == "WARN" and early.data["kind"] == late.data["kind"] == "lag"
    assert abs(early.data["offset_ms"] - 100) < 15 and abs(late.data["offset_ms"] + 150) < 15          # positive = audio early
    assert "BEFORE the mouth" in early.msg and "AFTER the mouth" in late.msg and "positive = audio early" in early.msg


@pytest.mark.parametrize("kind,ms", [("audio_early", 30), ("audio_late", 40)])
def test_lag_inside_the_window_is_not_a_warn(kind, ms, clean):
    assert not bad(run(sync.inject(kind, clean, ms=ms)))


@pytest.mark.parametrize("kind,ms", [("audio_early", 80), ("audio_late", 100)])
def test_lag_outside_the_window_is_a_warn(kind, ms, clean):
    assert {f.code for f in bad(run(sync.inject(kind, clean, ms=ms)))} == {"LIPSYNC"}


def test_a_delay_added_by_the_mix_is_found_against_the_dry_voice(clean):
    f = [f for f in run(sync.inject("mix_delay", clean, ms=150)) if f.code == "LIPSYNC"][0]
    assert f.sev == "WARN" and abs(f.data["mix_part_ms"] + 150) < 15 and abs(f.data["mouth_part_ms"]) < 20


def test_lipsync_without_the_dry_voice_uses_the_final_mix(clean):
    case = {**clean, "voice": None}
    (f,) = [f for f in run(case) if f.code == "LIPSYNC"]
    assert f.sev == "INFO" and f.data["mix_corr"] is None
    case = sync.inject("audio_late", {**clean, "voice": None}, ms=150)
    assert [f.sev for f in run(case) if f.code == "LIPSYNC"] == ["WARN"]


def test_lipsync_kinds(clean):
    assert [f.data["kind"] for f in run(sync.inject("mouth_flat", clean)) if f.code == "LIPSYNC"] == ["flat"]
    assert [f.data["kind"] for f in run(sync.inject("mouth_noise", clean)) if f.code == "LIPSYNC"] == ["corr"]
    flat_voice = {**clean, "voice": np.zeros_like(clean["voice"]), "final": np.zeros_like(clean["final"])}
    assert [f.data["kind"] for f in run(flat_voice) if f.code == "LIPSYNC"] == ["flat"]


def test_lipsync_correlation_limit_and_bias_come_from_the_profile(clean):
    case = sync.inject("mouth_noise", clean)
    assert not bad(sync.check_lipsync(Ctx(side=case["side"], voice=case["voice"], sr=case["sr"], profile={"sync": {"lipsync_corr_min": -1.0, "lag_corr_min": 2.0}})))
    late = sync.inject("audio_late", clean, ms=100)
    wide = {"sync": {"audio_late_ms": 300}}
    assert not bad(sync.check_lipsync(Ctx(side=late["side"], voice=late["voice"], final=late["final"], sr=late["sr"], profile=wide)))
    shifted = {"sync": {"mouth_bias_ms": -93.0}}                             # a bias of -93 ms instead of +7 moves a clean reel's offset by +100 ms: audio early beyond +45
    assert bad(sync.check_lipsync(Ctx(side=clean["side"], voice=clean["voice"], sr=clean["sr"], profile=shifted)))


def test_lipsync_is_silent_when_it_cannot_judge(clean):
    side = dict(clean["side"])
    assert sync.check_lipsync(Ctx(side={k: v for k, v in side.items() if k != "mouth"}, voice=clean["voice"], sr=clean["sr"])) == []
    assert sync.check_lipsync(Ctx(side=side)) == []                                                     # no voice, no final
    assert sync.check_lipsync(Ctx(side={**side, "mouth": [0.1] * 5}, voice=clean["voice"], sr=clean["sr"])) == []
    assert sync.check_lipsync(Ctx(side={**side, "params": {"mascot": False}, "mouth": [0.0] * len(side["mouth"])}, voice=clean["voice"], sr=clean["sr"])) == []


def test_lag_search_helpers_recover_a_known_shift():
    t = np.arange(0, 20, 0.005)
    a = np.clip(np.sin(2 * np.pi * 0.7 * t) + 0.5 * np.sin(2 * np.pi * 1.9 * t), 0, None)
    b = np.interp(t - 0.123, t, a)                                        # b is a delayed by 123 ms
    lags, c = sync.lag_curve(a, b, 0.005, 0.3)
    lag, peak, edge = sync.peak_lag(lags, c)
    assert abs(lag - 0.123) < 0.004 and peak > 0.99 and not edge
    assert np.isnan(sync.peak_lag(np.array([0.0]), np.array([np.nan]))[1])                                # nan peak when nothing correlates


# ---------------------------------------------------------------- AV_OFFSET

def test_av_offset_edges_and_missing_facts():
    def ctx(vs, au):
        return Ctx(side={"video": {"video_start": vs, "audio_start": au}}, profile=load())
    assert sync.check_av_offset(Ctx(side={})) == [] and sync.check_av_offset(Ctx(side={"video": {"video_start": 0.0}})) == []
    assert sync.check_av_offset(ctx(0.0, 0.021))[0].sev == "INFO"                    # AAC priming: 21 ms late is inside the window
    assert sync.check_av_offset(ctx(0.0, 0.07))[0].sev == "WARN" and sync.check_av_offset(ctx(0.0, 0.07))[0].data["offset_ms"] == pytest.approx(-70)
    assert sync.check_av_offset(ctx(0.05, 0.0))[0].sev == "WARN"                     # audio 50 ms before the picture: beyond +45
    assert sync.check_av_offset(ctx(0.04, 0.0))[0].sev == "INFO"


# ---------------------------------------------------------------- real material

def _ted_excerpt(path, start, seconds):
    with wave.open(str(path)) as w:
        sr = w.getframerate()
        w.setpos(int(start * sr))
        x = np.frombuffer(w.readframes(int(seconds * sr)), dtype=np.int16).astype("float32") / 32768.0
    n = int(round(len(x) * 24000 / sr))                                     # the voice of a reel is 24 kHz; make.mouth_track is only exact at such rates
    return np.interp(np.arange(n) / 24000, np.arange(len(x)) / sr, x).astype("float32"), 24000


def _mouth_of(x, sr, tmp_path):
    from reel_intel import make
    p = tmp_path / "v.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return make.mouth_track(p, [], len(x) / sr)


@pytest.mark.skipif(len(TED) < 4, reason="the TED talks are not on this machine")
@pytest.mark.parametrize("talk", range(4))
def test_real_speech_mouth_is_clean_and_delays_are_measured(talk, tmp_path):
    x, sr = _ted_excerpt(TED[talk], 100.0 + 37 * talk, 20.0)
    mouth = _mouth_of(x, sr, tmp_path)
    ctx = Ctx(side={"mouth": mouth}, voice=x, sr=sr, profile=load())
    (f,) = sync.check_lipsync(ctx)
    assert f.sev == "INFO" and f.data["corr0"] > 0.8 and abs(f.data["offset_ms"]) < 10, f.msg
    for ms in (120, -120):                                                  # the audio 120 ms late / early against the mouth
        y = sync._shift(x, int(round(ms / 1000 * sr)))
        (g,) = sync.check_lipsync(Ctx(side={"mouth": mouth}, voice=y, sr=sr, profile=load()))
        assert g.sev == "WARN" and g.data["kind"] == "lag" and abs(g.data["offset_ms"] + ms) < 10, g.msg


@pytest.mark.skipif(not (ROLKA / "rolka_a.qa.json").is_file() or not (ROLKA / "rolka_a_voice.wav").is_file(), reason="the real reel is not on this machine")
def test_real_generated_reel_gives_no_warn():
    side = json.loads((ROLKA / "rolka_a.qa.json").read_text(encoding="utf-8"))
    with wave.open(str(ROLKA / "rolka_a_voice.wav")) as w:
        sr = w.getframerate()
        voice = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768.0
    ctx = Ctx(side=side, voice=voice, sr=sr, profile=load())
    res = sync.check_lipsync(ctx)
    assert [f.sev for f in res] == ["INFO"] and abs(res[0].data["offset_ms"]) < 10 and res[0].data["corr0"] > 0.85
    res = sync.check_av_offset(ctx)                                           # the sidecar of a checked reel has the container's facts: sound and picture start together
    assert [f.sev for f in res] in ([], ["INFO"]) and all(abs(f.data["offset_ms"]) < 10 for f in res)
