"""reel_qa.py: the command line (qa, note, learn, calibrate, --selftest). The checker itself is faked: what is tested is what the commands do with its answer."""
import json

import numpy as np
import pytest

from reel_intel import reel_qa
from reel_intel.quality import engine, profile
from reel_intel.quality.base import Ctx, Finding

SR = 24000


@pytest.fixture(autouse=True)
def private_files(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_QA_NOTES", str(tmp_path / "notes.json"))
    monkeypatch.setenv("REEL_QA_PROFILE", str(tmp_path / "qa_profile.json"))


def tone(sec, db=-18.0):
    t = np.arange(int(sec * SR)) / SR
    return (np.sin(2 * np.pi * 180 * t) * 10 ** (db / 20) * 1.41).astype("float32")


def reel_ctx(ears=None):
    parts, scenes, pos = [np.zeros(int(0.3 * SR), dtype="float32")], [], 0.3
    for i in range(3):
        parts += [tone(1.6), np.zeros(int(0.6 * SR), dtype="float32")]
        scenes.append({"text": "to jest zdanie", "start": pos, "end": pos + 1.9, "words": [[f"w{k}", pos + k * 0.3, pos + k * 0.3 + 0.28] for k in range(5)]})
        pos += 2.2
    x = np.concatenate(parts)
    side = {"scenes": scenes}
    return Ctx(side=side, final=x, sr=SR, voice=x, profile=profile.load(), ears=ears), side


def fake_check(monkeypatch, findings, ctx=None, side=None):
    monkeypatch.setattr(engine, "check_reel", lambda mp4, **k: {"findings": findings, "ctx": ctx, "side": side})


def reel_file(tmp_path, side=None):
    mp4 = tmp_path / "r.mp4"
    mp4.write_bytes(b"mp4")
    if side is not None:
        mp4.with_suffix(".qa.json").write_text(json.dumps(side), encoding="utf-8")
    return mp4


# ---------------------------------------------------------------- qa
def test_qa_exit_codes_follow_the_verdict(tmp_path, monkeypatch, capsys):
    mp4 = reel_file(tmp_path, {"scenes": []})
    for findings, code in (([], 0), ([Finding("INFO", "PACE", "5.8")], 0), ([Finding("WARN", "VOICE_FAST", "fast", 1, 1.0, {})], 10), ([Finding("ERROR", "VOICE_CUT", "cut", 1, 1.0, {})], 1)):
        fake_check(monkeypatch, findings, None, {"scenes": []})
        assert reel_qa.main(["qa", str(mp4), "--quick"]) == code
    out = capsys.readouterr().out
    assert "werdykt: NIEGOTOWE" in out and "VOICE_CUT" in out


def test_qa_writes_the_report_and_puts_the_summary_into_the_sidecar(tmp_path, monkeypatch):
    mp4 = reel_file(tmp_path, {"version": 2, "scenes": []})
    fake_check(monkeypatch, [Finding("WARN", "LOUDNESS", "quiet")], None, {"scenes": [], "video": {"width": 1080}, "rehear": [["a", 0.0, 0.2]]})
    assert reel_qa.main(["qa", str(mp4)]) == 10
    md = (tmp_path / "r_kontrola.md").read_text(encoding="utf-8")
    assert "Werdykt: z uwagami" in md and "głośność całości poza celem" in md
    side = json.loads((tmp_path / "r.qa.json").read_text(encoding="utf-8"))
    assert side["version"] == 2 and side["qa"]["verdict"] == "z uwagami" and side["video"] == {"width": 1080} and side["rehear"]
    assert side["qa"]["findings"][0]["code"] == "LOUDNESS"


def test_qa_on_a_file_that_is_not_there_is_exit_2(tmp_path, capsys):
    assert reel_qa.main(["qa", str(tmp_path / "nope.mp4")]) == 2
    assert "FILE_BAD" in capsys.readouterr().out


def test_qa_without_arguments_prints_the_help_and_exits_2(capsys):
    assert reel_qa.main([]) == 2 and "reel-intel make qa" in capsys.readouterr().out


def test_qa_reports_recall_against_the_notes_of_the_reel(tmp_path, monkeypatch, capsys):
    mp4 = reel_file(tmp_path, {"scenes": []})
    reel_qa.main(["note", str(tmp_path / "other.mp4"), "1", "tempo"])                         # a note of another reel is not counted
    from reel_intel.quality import notes
    notes.add_note(mp4, 2.0, "oddech")
    fake_check(monkeypatch, [Finding("WARN", "VOICE_BREATH", "breath", 1, 2.2, {})], None, {"scenes": []})
    assert reel_qa.main(["qa", str(mp4)]) == 10
    out = capsys.readouterr().out
    assert "1 z 1" in out and "znalazł: oddech" in out
    assert "Zgodność z uwagami człowieka" in (tmp_path / "r_kontrola.md").read_text(encoding="utf-8")


def test_selftest_exit_code(monkeypatch):
    monkeypatch.setattr(reel_qa, "selftest", lambda *a, **k: (9, 10))
    assert reel_qa.main(["qa", "--selftest"]) == 1
    monkeypatch.setattr(reel_qa, "selftest", lambda *a, **k: (10, 10))
    assert reel_qa.main(["--selftest"]) == 0


# ---------------------------------------------------------------- note and learn
def test_note_is_stored_with_a_comma_for_a_decimal_point_and_a_kind_with_or_without_diacritics(tmp_path, capsys):
    assert reel_qa.main(["note", str(tmp_path / "nope.mp4"), "13,2", "Oddechy", "po", "scenie", "1"]) == 0
    from reel_intel.quality import notes
    n = notes.load_notes()
    assert len(n) == 1 and n[0]["t"] == 13.2 and n[0]["kind"] == "oddech" and n[0]["text"] == "po scenie 1"
    assert "saved: oddech at 13.2 s" in capsys.readouterr().out


def test_note_with_an_unknown_kind_or_too_few_arguments_is_refused(tmp_path, capsys):
    assert reel_qa.main(["note", str(tmp_path / "r.mp4"), "3", "zebra"]) == 2
    assert reel_qa.main(["note", str(tmp_path / "r.mp4")]) == 2
    from reel_intel.quality import notes
    assert notes.load_notes() == []
    assert "oddech" in capsys.readouterr().out


def test_a_missed_note_gets_its_numbers_and_a_suggestion(tmp_path, monkeypatch, capsys):
    ears = {"window": 1.0, "hop": 0.5, "windows": [{"t": 1.2, "labels": [["Speech", 0.9], ["Gasp", 0.05]]}]}
    ctx, side = reel_ctx(ears)
    mp4 = reel_file(tmp_path, side)
    fake_check(monkeypatch, [], ctx, side)
    assert reel_qa.main(["note", str(mp4), "1.8", "oddech", "w przerwie"]) == 0
    out = capsys.readouterr().out
    assert "PRZEGAPIŁ: oddech" in out and "measured at that moment" in out and "ears=0.05" in out and "voice.ears_min_score" in out
    from reel_intel.quality import notes
    assert notes.load_notes()[0]["features"]["ears"] == pytest.approx(0.05)


def test_a_detected_note_needs_no_suggestion(tmp_path, monkeypatch, capsys):
    ctx, side = reel_ctx()
    mp4 = reel_file(tmp_path, side)
    fake_check(monkeypatch, [Finding("WARN", "VOICE_BREATH", "b", 1, 1.9, {})], ctx, side)
    reel_qa.main(["note", str(mp4), "1.8", "oddech"])
    out = capsys.readouterr().out
    assert "znalazł: oddech" in out and "measured at that moment" not in out
    from reel_intel.quality import notes
    assert notes.load_notes()[0]["features"] == {}


def test_learn_tunes_the_profile_file_and_marks_the_notes(tmp_path, monkeypatch, capsys):
    ears = {"window": 1.0, "hop": 0.5, "windows": [{"t": 1.2, "labels": [["Speech", 0.9], ["Gasp", 0.05]]}]}
    ctx, side = reel_ctx(ears)
    mp4 = reel_file(tmp_path, side)
    fake_check(monkeypatch, [], ctx, side)
    reel_qa.main(["note", str(mp4), "1.8", "oddech"])
    capsys.readouterr()
    assert reel_qa.main(["learn", "--dry"]) == 0
    assert not (tmp_path / "qa_profile.json").exists() and "voice.ears_min_score: 0.07 -> 0.045" in capsys.readouterr().out
    assert reel_qa.main(["learn"]) == 0
    assert profile.load()["voice"]["ears_min_score"] == pytest.approx(0.045)
    from reel_intel.quality import notes
    assert notes.load_notes()[0]["applied"] is True
    capsys.readouterr()
    assert reel_qa.main(["learn"]) == 0
    assert "after" in capsys.readouterr().out and profile.load()["voice"]["ears_min_score"] == pytest.approx(0.045)


def test_learn_says_when_it_cannot_and_when_there_is_nothing(tmp_path, capsys):
    assert reel_qa.main(["learn"]) == 0 and "no notes yet" in capsys.readouterr().out
    reel_qa.main(["note", str(tmp_path / "r.mp4"), "3", "inne", "coś dziwnego"])
    capsys.readouterr()
    assert reel_qa.main(["learn"]) == 0
    out = capsys.readouterr().out
    assert "cannot: inne at 3.0 s" in out and not (tmp_path / "qa_profile.json").exists()


def test_calibrate_without_folders_prints_the_help(capsys):
    assert reel_qa.main(["calibrate"]) == 2 and "calibrate" in capsys.readouterr().out
