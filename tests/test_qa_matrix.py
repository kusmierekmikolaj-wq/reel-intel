"""quality/matrix.py: the fault-injection matrix as a test. The project's targets: at least 40 injected faults, at least 95% reported, no false alarm on a clean
reel. (The picture group is exercised by test_qa_picture.py, which draws the same reels; it is left out here to keep this test fast.)"""
import os

import numpy as np
import pytest

from reel_intel.quality import matrix, verdict
from reel_intel.quality.base import REGISTRY

GROUPS = ["audio", "speech", "mix", "platform", "sync"]


@pytest.fixture(scope="module")
def res():
    return matrix.run_matrix(GROUPS)


def test_the_matrix_has_enough_cases(res):
    assert res["faults"] >= 40 and res["clean"] >= 5


def test_at_least_95_percent_of_the_injected_faults_are_reported(res):
    missed = [(r["group"], r["name"], r["expect"], r["got"]) for r in res["cases"] if r["expect"] and not r["ok"]]
    assert res["score"] >= 0.95, missed
    assert missed == []


def test_no_clean_reel_raises_an_alarm(res):
    assert res["false_alarms"] == 0, [(r["name"], r["got"]) for r in res["cases"] if not r["expect"] and not r["ok"]]


def test_no_case_crashes(res):
    assert not [r for r in res["cases"] if any(str(g).startswith("CRASH") for g in r["got"])]


def test_the_expected_codes_are_registered(res):
    assert all(r["expect"] in REGISTRY for r in res["cases"] if r["expect"])


def test_the_matrix_covers_the_faults_a_person_complained_about(res):
    caught = {r["expect"] for r in res["cases"] if r["expect"] and r["ok"]}
    for code in ("VOICE_BREATH", "EAR_BREATH", "VOICE_DRONE", "VOICE_CUT", "VOICE_FAST", "VOICE_SUB", "VOICE_PAUSE_INNER", "MIX_PUMPING", "GAP_RUMBLE", "LOUDNESS"):
        assert code in caught, code


def test_the_report_names_what_was_missed_and_what_came_with_it(res):
    text = matrix.format_matrix(res)
    assert "injected faults reported" in text and "clean reels silent" in text
    fake = {"cases": [{"group": "x", "name": "a", "expect": "VOICE_CUT", "ok": False, "got": [], "extra": []},
                      {"group": "x", "name": "b", "expect": None, "ok": False, "got": ["VOICE_FAST"], "extra": []},
                      {"group": "x", "name": "c", "expect": "VOICE_CUT", "ok": True, "got": ["VOICE_CUT", "VOICE_HISS"], "extra": ["VOICE_HISS"]}],
            "faults": 2, "caught": 1, "exact": 0, "also": 1, "clean": 1, "clean_ok": 0, "false_alarms": 1, "score": 0.5}
    t = matrix.format_matrix(fake)
    assert "MISSED: x: a" in t and "FALSE ALARM: x: b" in t and "also reported: c" in t


@pytest.mark.skipif(not os.environ.get("REEL_TEST_REAL_MATRIX"), reason="slow (minutes): set REEL_TEST_REAL_MATRIX=<data folder with audio.wav + transcript.json> to run")
def test_the_faults_are_found_in_real_speech_too():
    res = matrix.run_real(os.environ["REEL_TEST_REAL_MATRIX"], per_talk=1)
    assert res["faults"] >= 40 and res["score"] >= 0.85, res["by_kind"]


def test_real_speech_excerpts_are_cut_at_sentences_and_the_injections_keep_the_timeline_consistent(tmp_path):
    side = {"scenes": [{"start": 0.0, "end": 1.0, "words": [["a", 0.1, 0.5], ["b", 0.5, 0.9]]}, {"start": 1.2, "end": 2.0, "words": [["c", 1.3, 1.9]]}], "total": 2.5}
    x = np.zeros(int(2.5 * matrix.SR), dtype="float32")
    x2, s2 = matrix._splice(x, side, 0.95, np.ones(int(0.3 * matrix.SR), dtype="float32"))
    assert len(x2) == len(x) + int(0.3 * matrix.SR) and s2["total"] == pytest.approx(2.8) and s2["scenes"][1]["start"] == pytest.approx(1.5) and s2["scenes"][0]["words"][1][2] == 0.9
    assert side["scenes"][1]["start"] == 1.2                                          # the input is not changed
    y = matrix._resample(np.arange(1000, dtype="float32"), 2.0)
    assert len(y) == 500 and y[-1] == pytest.approx(999.0)
    x3, s3 = matrix._replace(x, side, 1.2, 2.0, np.zeros(int(0.4 * matrix.SR), dtype="float32"))
    assert len(x3) == len(x) - int(0.4 * matrix.SR) and s3["total"] == pytest.approx(2.1)


def test_selftest_returns_a_pair_for_the_exit_code(monkeypatch):
    monkeypatch.setattr(matrix, "GROUPS", {"platform": matrix.platform_cases})
    caught, total, r = matrix.selftest(log=lambda *_a: None)
    assert caught == total == r["faults"] + r["clean"] and total > 5
    assert verdict.exit_code("gotowe") == 0
