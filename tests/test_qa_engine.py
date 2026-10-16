"""quality/engine.py on a real (tiny) mp4 made by make.render: the files next to it are found, the container is probed, every module runs, a crash of one detector does
not hide the others, and a sidecar that does not belong to the file is refused. The models (sound-event, OCR, recogniser) are left out."""
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from reel_intel import make, music
from reel_intel.quality import engine, verdict
from reel_intel.quality.base import Ctx, finding

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg is not installed on this machine")


def good():
    return {"lang": "en", "scenes": [{"text": "Thirty reels and you watched none of them.", "screen": "30 reels"}, {"text": "The program listens and writes it down.", "screen": "Speech to text"}],
            "cta": "Comment which account is next.", "caption": "c", "hashtags": ["#a"], "speed": 1.0}


@pytest.fixture()
def reel(tmp_path, monkeypatch):
    monkeypatch.setattr(make, "chatterbox_ready", lambda script: False)
    monkeypatch.setattr(make, "engine_for", lambda script: "piper")
    monkeypatch.setattr(make, "align_words", lambda *a, **k: (None, None, None, set()))
    monkeypatch.setattr(make, "FPS", 4)
    monkeypatch.setattr(make, "studio_dir", lambda: None)

    def tts(text, lang, out, rate=0, engine="sapi"):
        t = np.arange(int(1.2 * music.SR)) / music.SR
        music.write_wav(out, np.sin(2 * np.pi * 180 * t) * (0.4 + 0.4 * np.sin(2 * np.pi * 3 * t)))
        return True
    monkeypatch.setattr(make, "tts", tts)
    return make.render(good(), tmp_path / "r.mp4", simple=True)


def quick(mp4, **kw):
    return engine.check_reel(mp4, ears=False, ocr=False, do_rehear=False, **kw)


def test_the_files_next_to_the_reel_are_found_and_every_module_runs(reel):
    r = quick(reel)
    ctx, side = r["ctx"], r["side"]
    assert ctx.final is not None and len(ctx.final) > 24000 and ctx.voice is not None and ctx.music is not None and ctx.frames is not None and ctx.ocr is None and ctx.ears is None
    assert side["video"]["width"] == 1080 and side["video"]["has_audio"] is True and side["version"] == 2
    codes = {f.code for f in r["findings"]}
    assert "QA_ERROR" not in codes and "SIDECAR_STALE" not in codes and "SIDECAR_OLD" not in codes
    assert {"VIDEO_FACTS", "LOUDNESS", "OCR_OFF", "EARS_OFF"} <= codes                          # what each module said, and what it could not do


def test_a_reel_that_is_mastered_has_its_loudness_in_the_window(reel):
    f = [x for x in quick(reel)["findings"] if x.code == "LOUDNESS"]
    assert f and all(x.sev == "INFO" for x in f), [x.msg for x in f]                          # the file is stereo (dual mono) like the Remotion one, so the meter reads what the master aimed at
    side = json.loads(reel.with_suffix(".qa.json").read_text(encoding="utf-8"))
    assert side["master"]["after_lufs"] == pytest.approx(-14.3, abs=0.3) and quick(reel)["side"]["video"]["audio_channels"] == 2


def test_the_verdict_of_a_tiny_reel_is_computable(reel):
    fs = quick(reel)["findings"]
    v = verdict.decide(fs)
    assert v == "niegotowe"                                                                   # the voice is a tone that never stops: the last word is "chopped" (an ERROR), and the picture is 4 fps
    s = verdict.summarize(fs)
    json.dumps(s)
    assert s["exit"] == 1 and s["codes"]["VOICE_CUT"] >= 1 and "PLATFORM_FPS" in s["codes"]


def test_a_missing_file_is_a_finding_not_an_exception(tmp_path):
    r = engine.check_reel(tmp_path / "nope.mp4")
    assert [f.code for f in r["findings"]] == ["FILE_BAD"] and r["ctx"] is None and r["side"] is None


def test_a_missing_sidecar_is_an_error_and_the_file_is_still_checked(reel):
    reel.with_suffix(".qa.json").unlink()
    r = quick(reel)
    codes = {f.code: f.sev for f in r["findings"]}
    assert codes["SIDECAR_MISSING"] == "ERROR" and "VIDEO_FACTS" in codes
    assert verdict.decide(r["findings"]) == "niegotowe"


def test_a_sidecar_of_another_version_of_the_file_stops_the_timing_checks(reel):
    reel.write_bytes(reel.read_bytes() + b"\x00")                                           # the file changed after the sidecar was written
    r = quick(reel)
    codes = {f.code for f in r["findings"]}
    assert "SIDECAR_STALE" in codes and "VIDEO_FACTS" in codes and "LOUDNESS" in codes
    assert not (codes & {"VOICE_FAST", "GAP_LONG", "VOICE_SUB", "CAPTION_SYNC", "LIPSYNC"})   # nothing that trusts the timeline


def test_an_old_sidecar_without_a_checksum_is_only_information(reel):
    side = json.loads(reel.with_suffix(".qa.json").read_text(encoding="utf-8"))
    side.pop("mp4_sha256")
    reel.with_suffix(".qa.json").write_text(json.dumps(side), encoding="utf-8")
    f = [x for x in quick(reel)["findings"] if x.code == "SIDECAR_OLD"]
    assert f and f[0].sev == "INFO"


def test_one_detector_that_crashes_does_not_hide_the_others(reel, monkeypatch):
    from reel_intel.quality import audio

    def boom(ctx):
        raise RuntimeError("bug in a detector")
    boom.__name__ = "check_boom"
    monkeypatch.setattr(audio, "DETECTORS", [boom] + list(audio.DETECTORS))
    codes = [f for f in quick(reel)["findings"]]
    qa = [f for f in codes if f.code == "QA_ERROR"]
    assert len(qa) == 1 and "check_boom" in qa[0].msg and "bug in a detector" in qa[0].msg
    assert any(f.code == "LOUDNESS" for f in codes)


def test_modules_can_be_chosen_and_skipped(reel):
    ctx, _side = engine.build_ctx(reel, ears=False, ocr=False, do_rehear=False)
    only = engine.run_detectors(ctx, ["platform"])
    assert {f.code for f in only} <= {"VIDEO_FACTS", "PLATFORM_FPS", "PLATFORM_DURATION", "PLATFORM_CODEC", "PLATFORM_PIXFMT", "PLATFORM_SIZE", "PLATFORM_ASPECT", "AUDIO_RATE",
                                     "PLATFORM_FASTSTART", "AV_LENGTH", "NO_AUDIO", "FILE_BAD"}
    assert not [f for f in engine.run_detectors(ctx, ["mix"], skip=("check_loudness",)) if f.code == "LOUDNESS"]


def test_sha256_and_sidecar_path(tmp_path):
    p = tmp_path / "a.mp4"
    p.write_bytes(b"abc")
    assert engine.sha256(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert engine.sidecar_path(p) == tmp_path / "a.qa.json" and engine.load_sidecar(p) is None


def test_the_voice_of_another_rate_is_resampled_to_the_checkers_rate(tmp_path):
    p = tmp_path / "v.wav"
    t = np.arange(22050) / 22050
    music.SR, old = 22050, music.SR
    try:
        music.write_wav(p, np.sin(2 * np.pi * 220 * t) * 0.5)
    finally:
        music.SR = old
    x = engine.decode(p)
    assert abs(len(x) - 24000) < 50
