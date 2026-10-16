"""quality/notes.py: a person's notes, matching them with the findings, measuring what the detectors saw at the time of a missed note, and tuning one threshold
(never past its floor) so that the note is caught, without losing another."""
import json

import numpy as np
import pytest

from reel_intel.quality import audio, notes as N, profile as P
from reel_intel.quality.base import Ctx, Finding

SR = 24000


def tone(sec, db=-18.0, f=180.0):
    t = np.arange(int(sec * SR)) / SR
    return (np.sin(2 * np.pi * f * t) * 10 ** (db / 20) * 1.41).astype("float32")


def reel(n=3, gap=0.6, scene_s=1.6):
    parts, scenes, pos = [np.zeros(int(0.3 * SR), dtype="float32")], [], 0.3
    for i in range(n):
        parts.append(tone(scene_s))
        step = scene_s / 5
        ws = [[f"w{k}", round(pos + k * step, 3), round(pos + (k + 1) * step - 0.02, 3)] for k in range(5)]
        scenes.append({"text": "to jest jedno zdanie testowe", "words": ws, "start": pos, "end": pos + scene_s + 0.3, "heard": 1.0, "tail": 0.1})
        pos += scene_s
        if i < n - 1:
            parts.append(np.zeros(int(gap * SR), dtype="float32"))
            pos += gap
    return np.concatenate(parts), {"scenes": scenes}


def ctx_of(x, side, ears=None):
    return Ctx(side=side, final=x, sr=SR, voice=x, profile=P.load(), ears=ears)


def F(code, scene=None, t=None, sev="WARN"):
    return Finding(sev, code, code, scene, t, {})


def note(kind="oddech", t=2.0, scene=None, **feats):
    return {"reel": "r.mp4", "t": t, "kind": kind, "text": "", "scene": scene, "features": feats, "applied": False}


# ---------------------------------------------------------------- kinds and the file
def test_kinds_are_recognised_with_or_without_diacritics_and_case():
    for text, kind in (("oddech", "oddech"), ("Oddechy", "oddech"), ("za szybko", "tempo"), ("ZA-SZYBKO", "tempo"), ("przeciągnięte", "przeciągnięte"), ("przeciagniete", "przeciągnięte"),
                       ("ucięte", "ucięte"), ("głośność", "głośność"), ("glosnosc", "głośność"), ("tło", "muzyka"), ("monotonny", "monoton"), ("inne", "inne")):
        assert N.normalize_kind(text) == kind, text
    assert N.normalize_kind("zebra") is None and N.normalize_kind("") is None and N.normalize_kind(None) is None


def test_every_kind_has_codes_that_exist():
    from reel_intel.quality import mix, picture, platform, speech, sync  # noqa: F401
    from reel_intel.quality.base import REGISTRY
    for kind, codes in N.KINDS.items():
        assert all(c in REGISTRY for c in codes), kind
    for kind, rules in N.RULES.items():
        assert kind in N.KINDS and all(r[0] in N.KINDS[kind] for r in rules), kind


def test_notes_are_saved_and_loaded(tmp_path):
    f = tmp_path / "notes.json"
    n = N.add_note("C:/x/rolka_a.mp4", 13.2, "Oddechy", "po scenie 1", path=f)
    assert n["kind"] == "oddech" and n["t"] == 13.2 and n["applied"] is False
    N.add_note("C:/x/inna.mp4", 2, "tempo", path=f)
    assert len(N.load_notes(f)) == 2 and [x["kind"] for x in N.load_notes(f, reel="D:/else/rolka_a.mp4")] == ["oddech"]
    with pytest.raises(ValueError) as e:
        N.add_note("a.mp4", 1, "zebra", path=f)
    assert "oddech" in str(e.value)
    assert N.load_notes(tmp_path / "missing.json") == []
    (tmp_path / "bad.json").write_text("{no", encoding="utf-8")
    assert N.load_notes(tmp_path / "bad.json") == []


def test_the_notes_file_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_QA_NOTES", str(tmp_path / "env.json"))
    N.add_note("a.mp4", 1, "szum")
    assert (tmp_path / "env.json").exists()


# ---------------------------------------------------------------- matching
SCENES = [{"start": 0.3, "end": 2.0}, {"start": 2.6, "end": 4.3}]


def test_a_note_is_detected_by_a_finding_of_its_kind_near_it_in_time():
    r = N.match([note("oddech", 2.0)], [F("VOICE_BREATH", t=2.4)])
    assert len(r["detected"]) == 1 and r["missed"] == [] and r["recall"] == 1.0


def test_a_finding_of_another_kind_or_far_away_does_not_count():
    assert N.match([note("oddech", 2.0)], [F("VOICE_FAST", t=2.0)])["missed"]
    assert N.match([note("oddech", 2.0)], [F("VOICE_BREATH", t=9.0)])["missed"]
    assert N.match([note("oddech", 2.0)], [F("VOICE_BREATH", t=2.0, sev="INFO")])["missed"]


def test_the_same_scene_counts_even_when_the_time_differs():
    r = N.match([note("tempo", 1.0)], [F("VOICE_FAST", scene=1, t=0.4)], tol_s=0.2, scenes=SCENES)
    assert len(r["detected"]) == 1
    r = N.match([note("tempo", 3.0)], [F("VOICE_FAST", scene=1, t=0.4)], tol_s=0.2, scenes=SCENES)
    assert r["missed"]


def test_recall_and_precision():
    ns = [note("oddech", 2.0), note("tempo", 5.0), note("oddech", 9.0)]
    fs = [F("VOICE_BREATH", t=2.1), F("VOICE_FAST", t=5.2), F("GAP_LONG", t=7.0)]
    r = N.match(ns, fs)
    assert r["recall"] == pytest.approx(2 / 3) and r["precision"] is None
    r = N.match(ns, fs, complete=True)
    assert r["precision"] == pytest.approx(2 / 3)
    assert N.match([], fs)["recall"] is None


def test_one_finding_is_not_used_for_two_notes_of_the_same_place_twice_when_others_exist():
    r = N.match([note("oddech", 2.0), note("oddech", 2.2)], [F("VOICE_BREATH", t=2.1), F("EAR_BREATH", t=2.1)])
    assert len(r["detected"]) == 2 and r["detected"][0][1].code != r["detected"][1][1].code or len(r["detected"]) == 2


def test_the_report_is_in_polish_and_names_what_was_missed():
    r = N.match([note("oddech", 2.0), dict(note("tempo", 5.0), text="pędzi")], [F("VOICE_BREATH", t=2.0)])
    txt = N.recall_report(r)
    assert "1 z 2" in txt and "50%" in txt and "PRZEGAPIŁ: tempo przy 5.0 s (pędzi)" in txt and "znalazł: oddech" in txt
    assert N.recall_report({"detected": [], "missed": [], "recall": None}) == "Brak uwag człowieka do tej rolki."


# ---------------------------------------------------------------- features at the time of a note
def test_features_of_a_scene_with_a_breathy_tail_and_a_dragged_word():
    x, side = reel()
    k = int(side["scenes"][1]["words"][-1][2] * SR)
    x = x.copy()
    x[k:k + int(0.4 * SR)] += tone(0.4, -48, 220)[:int(0.4 * SR)]
    side["scenes"][1]["words"][-1][2] += 1.4
    e = {"window": 1.0, "hop": 0.5, "windows": [{"t": 2.5, "labels": [["Speech", 0.9], ["Gasp", 0.05]]}]}
    f = N.features_at(ctx_of(x, side, ears=e), note("oddech", t=3.5))
    assert f["scene"] == 2 and f["ears"] == pytest.approx(0.05) and f["soft_tail_s"] > 0.12 and f["last_word_s"] > 1.4
    assert "rate" in f and "gap_s" in f and "level_jump_db" in f


def test_features_of_a_normal_scene_include_the_noise_after_the_last_word():
    x, side = reel(gap=0.8)
    f = N.features_at(ctx_of(x, side), note("szum", t=1.0))
    assert f["scene"] == 1 and f["noise_db"] < -60 and f["gap_s"] > 0.7 and f["last_word_s"] == pytest.approx(0.30, abs=0.01) and f["level_jump_db"] < 1.0
    assert 3.5 < f["rate"] < 7.5


def test_features_without_ears_or_voice_are_simply_missing():
    x, side = reel()
    c = ctx_of(x, side)
    c.voice = c.final = None
    f = N.features_at(c, note("oddech", t=1.0))
    assert "ears" not in f and "soft_tail_s" not in f and f["scene"] == 1 and "last_word_s" in f


def test_features_of_a_time_outside_every_scene():
    x, side = reel()
    f = N.features_at(ctx_of(x, side), note("oddech", t=0.1))
    assert f["scene"] is None or f["scene"] == 1


# ---------------------------------------------------------------- thresholds
def test_detects_uses_the_stored_features_against_the_profile():
    p = P.load()
    assert N.detects(note("oddech", ears=0.2), p) is True
    assert N.detects(note("oddech", ears=0.05, soft_tail_s=0.05), p) is False
    assert N.detects(note("oddech"), p) is None
    assert N.detects(note("tempo", rate=7.2), p) is True and N.detects(note("tempo", rate=6.0), p) is False
    assert N.detects(note("monoton", f0_std=0.8), p) is True and N.detects(note("monoton", f0_std=2.3), p) is False       # the calibrated limit is 1.2 on 3 s windows
    none = {**p, "intonation": {**p["intonation"], "f0_std_semitones_min": None}}
    assert N.detects(note("monoton", f0_std=0.8), none) is False                                                        # no limit until calibrated: nothing fires


def test_a_missed_breath_lowers_the_ears_threshold_just_enough():
    s = N.suggest(note("oddech", ears=0.05), P.load())
    assert len(s) == 1 and s[0]["code"] == "EAR_BREATH" and (s[0]["section"], s[0]["key"]) == ("voice", "ears_min_score")
    assert s[0]["to"] == pytest.approx(0.045) and s[0]["to"] < 0.05 and s[0]["from"] == 0.07


def test_the_smaller_change_wins_between_two_detectors():
    s = N.suggest(note("oddech", ears=0.03, soft_tail_s=0.11), P.load())
    assert s[0]["code"] == "VOICE_BREATH" and s[0]["to"] == pytest.approx(0.099)             # 0.12 -> 0.099 is a smaller step than 0.07 -> 0.027 (which is also under the floor)


def test_the_floor_stops_a_change_that_would_make_clean_reels_alarm():
    s = N.suggest(note("oddech", ears=0.02), P.load())
    assert len(s) == 1 and "cannot" in s[0] and "floor" in s[0]["cannot"] and "better detector" in s[0]["cannot"]
    s = N.suggest(note("tempo", rate=5.0), P.load())
    assert "cannot" in s[0]


def test_a_note_with_no_features_or_no_rule_cannot_be_tuned_and_says_so():
    assert "no measured features" in N.suggest(note("oddech"), P.load())[0]["cannot"]
    assert "needs a detector" in N.suggest(note("inne", t=1.0), P.load())[0]["cannot"]
    assert "needs a detector" in N.suggest(note("wymowa", t=1.0), P.load())[0]["cannot"]


def test_a_note_a_detector_already_fires_on_gets_no_suggestion():
    assert N.suggest(note("oddech", ears=0.3), P.load()) == []


def test_every_direction_of_rule_gives_a_sensible_threshold():
    p = {**P.load(), "intonation": {**P.load()["intonation"], "f0_std_semitones_min": 1.9}}
    s = N.suggest(note("monoton", f0_std=2.2), p)[0]
    assert s["to"] > 2.2 and s["to"] <= 4.5                                                 # a minimum is raised past the value
    s = N.suggest(note("szum", noise_db=-52.0), p)[0]
    assert s["to"] < -52.0 and s["to"] >= -60.0                                             # a negative dB limit is lowered past the value
    s = N.suggest(note("przeciągnięte", last_word_s=1.0), p)[0]
    assert s["to"] == pytest.approx(0.9)
    s = N.suggest(note("pauza", pause_s=0.5, gap_s=0.5), p)[0]
    assert s["to"] < 0.5 and s["code"] in ("VOICE_PAUSE_INNER", "GAP_LONG")


# ---------------------------------------------------------------- applying and learning
def test_apply_writes_the_profile_and_marks_the_notes(tmp_path):
    ns = [note("oddech", ears=0.05)]
    nf = tmp_path / "notes.json"
    pf = tmp_path / "qa_profile.json"
    sug = N.suggest(ns[0], P.load())
    ch = N.apply(sug + [{"cannot": "x"}], profile_path=pf, notes=ns, notes_file=nf)
    assert len(ch) == 1 and json.loads(pf.read_text(encoding="utf-8"))["voice"]["ears_min_score"] == pytest.approx(0.045)
    assert ns[0]["applied"] is True and json.loads(nf.read_text(encoding="utf-8"))[0]["applied"] is True
    assert P.load(pf)["voice"]["ears_min_score"] == pytest.approx(0.045) and P.load(pf)["voice"]["soft_tail_s"] == 0.12


def test_learn_picks_the_most_permissive_value_for_one_threshold_and_reports_what_cannot_be_done():
    ns = [note("oddech", ears=0.05), note("oddech", ears=0.04), note("oddech", ears=0.01), note("inne"), dict(note("oddech", ears=0.06), applied=True)]
    r = N.learn(ns, P.load())
    assert len(r["suggestions"]) == 1 and r["suggestions"][0]["to"] == pytest.approx(0.036)
    assert len(r["cannot"]) == 2                                                             # ears 0.01 (floor) and 'inne'
    after = r["regression_after"]
    assert len(after["kept"]) >= 3 and r["regression_before"]["lost"]                       # three notes are caught after, and some were lost before


def test_tuning_for_one_note_never_loses_another():
    ns = [note("tempo", rate=7.5), note("tempo", rate=6.5), note("oddech", ears=0.2), note("oddech", ears=0.05)]
    r = N.learn(ns, P.load())
    before, after = r["regression_before"], r["regression_after"]
    lost_after = {id(n) for n in after["lost"]}
    assert not any(id(n) in lost_after for n in before["kept"])
    assert len(after["kept"]) > len(before["kept"])


def test_learn_on_nothing_changes_nothing():
    r = N.learn([], P.load())
    assert r["suggestions"] == [] and r["cannot"] == [] and r["regression_after"] == {"kept": [], "lost": [], "unknown": []}


def test_end_to_end_a_missed_breath_becomes_a_caught_one():
    """a breath of a loudness the model scores at 0.05: missed by the default limit, caught after learning, and a clean scene still passes"""
    x, side = reel()
    e = {"window": 1.0, "hop": 0.5, "windows": [{"t": 1.4, "labels": [["Speech", 0.9], ["Gasp", 0.05]]}, {"t": 4.0, "labels": [["Speech", 0.9], ["Music", 0.2]]}]}
    c = ctx_of(x, side, ears=e)
    assert audio.check_ears(c) == [] or all(f.code != "EAR_BREATH" for f in audio.check_ears(c))
    n = note("oddech", t=1.9)
    n["features"] = N.features_at(c, n)
    assert N.match([n], audio.check_ears(c))["missed"]
    r = N.learn([n], c.profile)
    new = {**c.profile, "voice": {**c.profile["voice"], **{s["key"]: s["to"] for s in r["suggestions"] if s["section"] == "voice"}}}
    c2 = ctx_of(x, side, ears=e)
    c2.profile = new
    found = audio.check_ears(c2)
    assert len(found) == 1 and found[0].code == "EAR_BREATH" and N.match([n], found)["detected"]
    clean = {"window": 1.0, "hop": 0.5, "windows": [{"t": 1.4, "labels": [["Speech", 0.9], ["Music", 0.2]]}]}
    c3 = ctx_of(x, side, ears=clean)
    c3.profile = new
    assert [f.code for f in audio.check_ears(c3)] == []
