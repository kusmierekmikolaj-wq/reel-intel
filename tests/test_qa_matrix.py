"""quality/matrix.py: the fault-injection matrix as a test. The project's targets: at least 40 injected faults, at least 95% reported, no false alarm on a clean
reel. (The picture group is exercised by test_qa_picture.py, which draws the same reels; it is left out here to keep this test fast.)"""
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


def test_selftest_returns_a_pair_for_the_exit_code(monkeypatch):
    monkeypatch.setattr(matrix, "GROUPS", {"platform": matrix.platform_cases})
    caught, total, r = matrix.selftest(log=lambda *_a: None)
    assert caught == total == r["faults"] + r["clean"] and total > 5
    assert verdict.exit_code("gotowe") == 0
