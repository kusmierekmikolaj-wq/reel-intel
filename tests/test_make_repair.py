"""make.py: the pieces the repair loop stands on: the recipe, scenes recorded again one by one, a timeline that starts every round from the pristine take, mastering,
the sidecar (version 2, with the checksum of the mp4), the repair actions and the loop with a fake checker. The voice engine is stubbed."""
import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from reel_intel import make, music
from reel_intel.quality import engine as qengine
from reel_intel.quality.base import Finding
from reel_intel.quality.repair import Action

need_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed on this machine")


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    monkeypatch.setattr(make, "chatterbox_ready", lambda script: False)
    monkeypatch.setattr(make, "engine_for", lambda script: "piper")
    monkeypatch.setattr(make, "align_words", lambda *a, **k: (None, None, None, set()))


def good():
    return {"lang": "en", "scenes": [{"text": "Thirty reels and you watched none of them.", "screen": "30 reels"},
                                     {"text": "The program listens and writes it down.", "screen": "Speech to text"},
                                     {"text": "It reads the screen too.", "screen": "Reads the screen"}],
            "cta": "Comment which account is next.", "caption": "c", "hashtags": ["#a"], "speed": 1.0}


class FakeVoice:
    """a voice engine that says every sentence as a tone of its own loudness and records what it was asked"""

    def __init__(self, seconds=1.0, levels=None):
        self.calls, self.seconds, self.levels = [], seconds, levels or [0.4, 0.4, 0.4, 0.4]

    def __call__(self, text, lang, out, rate=0, engine="sapi"):
        i = len(self.calls)
        self.calls.append(text)
        scene = next((k for k, t in enumerate(self.texts) if t == text.strip()), 0)
        music.write_wav(out, np.sin(np.linspace(0, 1500, int(self.seconds * music.SR))) * self.levels[min(scene, len(self.levels) - 1)])
        return True

    texts = []


def voice_for(script, **kw):
    v = FakeVoice(**kw)
    v.texts = [s["text"].strip() for s in script["scenes"]] + ([script["cta"].strip()] if script.get("cta") else [])
    return v


# ---------------------------------------------------------------- recipe
def test_a_new_recipe_is_empty_and_json_able():
    r = make.new_recipe()
    json.dumps(r)
    assert r["retake"] == {} and r["made"] == {} and r["level"] is False and r["music_mult"] == 1.0 and r["hp"] == 150


def test_respellings_of_a_scene_are_added_to_the_scripts_own():
    r = make.new_recipe()
    r["respell"][1] = {"skill": "skil"}
    assert make.say_for({"say": {"AI": "ej aj"}}, r, 1) == {"AI": "ej aj", "skill": "skil"}
    assert make.say_for({"say": {"AI": "ej aj"}}, r, 0) == {"AI": "ej aj"}
    assert make.say_for({}, None, 0) == {}


# ---------------------------------------------------------------- scenes recorded again one by one
@need_ffmpeg
def test_each_round_starts_from_the_pristine_take_and_only_retaken_scenes_are_recorded_again(tmp_path, monkeypatch):
    s = good()
    v = voice_for(s)
    monkeypatch.setattr(make, "tts", v)
    r = make.new_recipe()
    meta = {}
    scenes, total, audio = make.timeline(s, tmp_path, r, meta)
    assert len(v.calls) == 4 and all((tmp_path / f"raw_{i}.wav").exists() for i in range(4)) and meta["engine_used"] == "piper"
    first = [(sc["start"], sc["end"]) for sc in scenes]
    scenes2, _t, _a = make.timeline(s, tmp_path, r, {})
    assert len(v.calls) == 4                                                   # nothing was recorded again
    assert [(sc["start"], sc["end"]) for sc in scenes2] == first                # and the timeline is the same
    r["retake"][1] = 1
    make.timeline(s, tmp_path, r, {})
    assert len(v.calls) == 5 and v.calls[-1].startswith("The program")          # only scene 2
    assert r["made"][1] == 1
    make.timeline(s, tmp_path, r, {})
    assert len(v.calls) == 5


@need_ffmpeg
def test_a_respelling_changes_what_the_engine_is_asked_to_say(tmp_path, monkeypatch):
    s = good()
    s["scenes"][1]["text"] = "The skill listens."
    v = voice_for(s)
    monkeypatch.setattr(make, "tts", v)
    r = make.new_recipe()
    make.timeline(s, tmp_path, r, {})
    r["respell"][1] = {"skill": "skil"}
    r["retake"][1] = 1
    make.timeline(s, tmp_path, r, {})
    assert "skil" in v.calls[-1] and "skill" not in v.calls[-1]


@need_ffmpeg
def test_a_slower_scene_lasts_longer_by_the_factor(tmp_path, monkeypatch):
    s = good()
    v = voice_for(s)
    monkeypatch.setattr(make, "tts", v)
    r = make.new_recipe()
    base, _t, _a = make.timeline(s, tmp_path, r, {})
    r["speed"][0] = 0.8
    slow, _t, _a = make.timeline(s, tmp_path, r, {})
    d0, d1 = base[0]["end"] - base[0]["start"], slow[0]["end"] - slow[0]["start"]
    assert d1 == pytest.approx(d0 / 0.8, abs=0.1)
    assert (slow[1]["end"] - slow[1]["start"]) == pytest.approx(base[1]["end"] - base[1]["start"], abs=0.05)      # the others are untouched


@need_ffmpeg
def test_levelling_brings_every_scene_to_the_same_loudness(tmp_path, monkeypatch):
    def spread(sc):
        d = [make._speech_db(x["wav"], 0.05, x["end"] - x["start"] - 0.05) for x in sc]
        return max(d) - min(d)

    def made(levels, folder, level):
        s = good()
        monkeypatch.setattr(make, "tts", voice_for(s, levels=levels))
        r = make.new_recipe()
        r["level"] = level
        (tmp_path / folder).mkdir()
        scenes, _t, _a = make.timeline(s, tmp_path / folder, r, {})
        return spread(scenes)
    assert made([0.3, 0.1, 0.2, 0.15], "a", False) > 8 and made([0.3, 0.1, 0.2, 0.15], "b", True) < 2.0
    wide, narrowed = made([0.5, 0.1, 0.3, 0.05], "c", False), made([0.5, 0.1, 0.3, 0.05], "d", True)
    assert wide > 15 and narrowed < wide - 6                                     # the gain is limited to 6 dB a scene: a 20 dB spread shrinks, it does not vanish


@need_ffmpeg
def test_a_failed_voice_falls_back_to_the_reading_speed_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(make, "tts", lambda *a, **k: False)
    meta = {}
    scenes, total, audio = make.timeline(good(), tmp_path, make.new_recipe(), meta)
    assert audio is None and meta["engine_wanted"] == "piper" and all(sc["wav"] is None for sc in scenes)


def test_heard_words_are_kept_in_the_order_they_were_heard(tmp_path, monkeypatch):
    def fake_align(wav, lang, words, heard_out=None):
        if heard_out is not None:
            heard_out[:] = [["Thirty", 0.0, 0.3], ["reels", 0.3, 0.6]]
        return None, None, None, set()
    monkeypatch.setattr(make, "align_words", fake_align)
    s = good()
    monkeypatch.setattr(make, "tts", voice_for(s))
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed on this machine")
    scenes, _t, _a = make.timeline(s, tmp_path, make.new_recipe(), {})
    assert scenes[0]["heard_words"] == ["Thirty", "reels"]


# ---------------------------------------------------------------- mastering
def _speechy(seconds=6.0, db=-30.0):
    rng = np.random.default_rng(2)
    t = np.arange(int(seconds * music.SR)) / music.SR
    x = np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)) + 0.05 * rng.standard_normal(len(t))
    return x / np.sqrt(np.mean(x ** 2)) * 10 ** (db / 20)


def test_master_wav_reaches_the_target_and_keeps_the_peak_under_the_ceiling(tmp_path):
    src = tmp_path / "in.wav"
    music.write_wav(src, _speechy(db=-30.0))
    info = make.master_wav(src, tmp_path / "out.wav", -14.3, -1.0)
    assert info["before_lufs"] < -24 and info["after_lufs"] == pytest.approx(-14.3, abs=0.3)
    assert info["true_peak_db"] <= -0.9 and info["gain_db"] > 8 and info["reached"] is True
    assert (tmp_path / "out.wav").stat().st_size > 1000
    again = make.master_wav(tmp_path / "out.wav", tmp_path / "out2.wav", -14.3, -1.0)
    assert abs(again["gain_db"]) < 0.3                                         # mastering twice changes nothing


def test_final_audio_masters_the_mix_and_leaves_the_music_stem(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed on this machine")
    voice = tmp_path / "voice.wav"
    music.write_wav(voice, _speechy(db=-28.0))
    meta = {}
    scenes = [{"words": [("a", 0.5, 5.0)]}]
    out = make.final_audio({"music": "bed"}, voice, 6.0, tmp_path, scenes, make.new_recipe(), meta)
    assert out == tmp_path / "final.wav" and meta["master"]["after_lufs"] == pytest.approx(-14.3, abs=0.4)
    assert Path(meta["music_stem"]).exists() and meta["master"]["true_peak_db"] <= -0.9


def test_final_audio_without_music_still_masks_the_voice_to_the_target(tmp_path):
    voice = tmp_path / "voice.wav"
    music.write_wav(voice, _speechy(db=-31.0))
    meta = {}
    out = make.final_audio({"music": "off"}, voice, 6.0, tmp_path, [], make.new_recipe(), meta)
    assert out.name == "final.wav" and "music_stem" not in meta and meta["master"]["after_lufs"] == pytest.approx(-14.3, abs=0.4)


def test_mastering_can_be_switched_off_and_a_target_given(tmp_path):
    voice = tmp_path / "voice.wav"
    music.write_wav(voice, _speechy(db=-31.0))
    assert make.final_audio({"music": "off", "master": False}, voice, 6.0, tmp_path, [], make.new_recipe(), {}) == voice
    meta = {}
    make.final_audio({"music": "off", "loudness": -18.0}, voice, 6.0, tmp_path, [], make.new_recipe(), meta)
    assert meta["master"]["after_lufs"] == pytest.approx(-18.0, abs=0.4)


def test_no_voice_means_no_audio_and_no_crash(tmp_path):
    assert make.final_audio({}, None, 5.0, tmp_path, [], make.new_recipe(), {}) is None


# ---------------------------------------------------------------- the sidecar
@need_ffmpeg
def test_render_writes_a_version_2_sidecar_with_the_checksum_of_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(make, "tts", lambda *a, **k: False)
    monkeypatch.setattr(make, "FPS", 4)
    monkeypatch.setattr(make, "studio_dir", lambda: None)
    meta = {}
    out = make.render(good(), tmp_path / "r.mp4", simple=True, meta=meta)
    side = json.loads((tmp_path / "r.qa.json").read_text(encoding="utf-8"))
    assert side["version"] == 2 and side["renderer"] == "pil" and side["renderer_wanted"] == "pil" and side["engine_wanted"] == "piper"
    assert side["params"]["lang"] == "en" and side["params"]["simple"] is True and side["params"]["master"] is True
    assert side["mp4_sha256"] == qengine.sha256(out) and side["mp4_bytes"] == out.stat().st_size and len(side["scenes"]) == 4
    assert qengine.check_sidecar(out, side) == []
    out.write_bytes(out.read_bytes() + b"x")
    assert [f.code for f in qengine.check_sidecar(out, side)] == ["SIDECAR_STALE"]
    assert [f.code for f in qengine.check_sidecar(out, {"scenes": []})] == ["SIDECAR_OLD"]


@need_ffmpeg
def test_a_render_that_falls_back_to_the_simple_renderer_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(make, "tts", lambda *a, **k: False)
    monkeypatch.setattr(make, "FPS", 4)
    monkeypatch.setattr(make, "studio_dir", lambda: tmp_path)                    # a studio that exists...
    monkeypatch.setattr(make, "render_remotion", lambda *a, **k: False)          # ...and fails
    out = make.render(good(), tmp_path / "r.mp4")
    side = json.loads((tmp_path / "r.qa.json").read_text(encoding="utf-8"))
    assert side["renderer_wanted"] == "remotion" and side["renderer"] == "pil"
    from reel_intel.quality import speech
    from reel_intel.quality.base import Ctx
    assert [f.code for f in speech.check_render_fallback(Ctx(side=side))] == ["RENDER_FALLBACK"]


@need_ffmpeg
def test_the_voice_and_the_music_stem_are_kept_next_to_the_reel(tmp_path, monkeypatch):
    s = good()
    monkeypatch.setattr(make, "tts", voice_for(s))
    monkeypatch.setattr(make, "FPS", 4)
    monkeypatch.setattr(make, "studio_dir", lambda: None)
    make.render(s, tmp_path / "r.mp4", simple=True)
    assert (tmp_path / "r_voice.wav").exists() and (tmp_path / "r_music.wav").exists() and (tmp_path / "r_opis.txt").exists()
    side = json.loads((tmp_path / "r.qa.json").read_text(encoding="utf-8"))
    assert side["master"]["after_lufs"] is not None and side["has_music_stem"] is True


@need_ffmpeg
def test_a_work_folder_keeps_the_takes_between_renders(tmp_path, monkeypatch):
    s = good()
    v = voice_for(s)
    monkeypatch.setattr(make, "tts", v)
    monkeypatch.setattr(make, "FPS", 4)
    monkeypatch.setattr(make, "studio_dir", lambda: None)
    work, r = tmp_path / "work", make.new_recipe()
    make.render(s, tmp_path / "r.mp4", simple=True, workdir=work, recipe=r)
    make.render(s, tmp_path / "r.mp4", simple=True, workdir=work, recipe=r)
    assert len(v.calls) == 4 and work.exists()


@need_ffmpeg
def test_audio_only_keeps_the_picture_and_changes_the_sound(tmp_path, monkeypatch):
    s = good()
    monkeypatch.setattr(make, "tts", voice_for(s))
    monkeypatch.setattr(make, "FPS", 4)
    monkeypatch.setattr(make, "studio_dir", lambda: None)
    work, r = tmp_path / "work", make.new_recipe()
    out = make.render(s, tmp_path / "r.mp4", simple=True, workdir=work, recipe=r)
    video_before = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-map", "0:v", "-c", "copy", "-f", "md5", "-"], capture_output=True, text=True).stdout
    frames_before = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout
    r["music_mult"] = 0.3
    make.render(s, out, simple=True, workdir=work, recipe=r, audio_only=True)
    frames_after = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout
    assert frames_before == frames_after != ""                                            # not a frame of the picture is lost at the end
    video_after = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-map", "0:v", "-c", "copy", "-f", "md5", "-"], capture_output=True, text=True).stdout
    assert video_before == video_after != ""
    side = json.loads((tmp_path / "r.qa.json").read_text(encoding="utf-8"))
    assert side["mp4_sha256"] == qengine.sha256(out) and side["recipe"]["music_mult"] == 0.3


@need_ffmpeg
def test_mux_audio_puts_another_sound_under_the_same_picture(tmp_path, monkeypatch):
    s = good()
    monkeypatch.setattr(make, "tts", voice_for(s))
    monkeypatch.setattr(make, "FPS", 4)
    monkeypatch.setattr(make, "studio_dir", lambda: None)
    out = make.render(s, tmp_path / "r.mp4", simple=True)
    wav = tmp_path / "new.wav"
    music.write_wav(wav, _speechy(seconds=4.0))
    assert make.mux_audio(out, wav, tmp_path / "m.mp4") and (tmp_path / "m.mp4").stat().st_size > 1000
    assert not make.mux_audio(tmp_path / "nope.mp4", wav, tmp_path / "x.mp4")


# ---------------------------------------------------------------- the repair actions change the recipe
def act(kind, scene=None, **data):
    return Action(kind, scene, ["X"], {"findings": [data] if data else []})


def test_retake_needs_a_voice_engine_that_can_say_it_differently():
    r = make.new_recipe()
    assert make.apply_repair({}, r, act("retake_scene", 2), {"engine_used": "chatterbox"}, log=lambda *_: None) is True and r["retake"] == {1: 1}
    assert make.apply_repair({}, r, act("retake_scene", 2), {"engine_used": "chatterbox"}, log=lambda *_: None) and r["retake"] == {1: 2}
    r2 = make.new_recipe()
    assert make.apply_repair({}, r2, act("retake_scene", 2), {"engine_used": "piper"}, log=lambda *_: None) is False and r2["retake"] == {}


def test_respell_takes_the_next_candidate_and_falls_back_to_a_retake(monkeypatch):
    from reel_intel import pronounce
    monkeypatch.setattr(pronounce, "candidates", lambda w, lang="pl": ["skil", "ski-l"])
    monkeypatch.setattr(pronounce, "STORE", Path("nonexistent-store.json"))
    r = make.new_recipe()
    a = act("respell", 1, words=["skill"])
    assert make.apply_repair({"lang": "pl"}, r, a, {"engine_used": "chatterbox"}, log=lambda *_: None) and r["respell"][0] == {"skill": "skil"} and r["retake"][0] == 1
    assert make.apply_repair({"lang": "pl"}, r, a, {"engine_used": "chatterbox"}, log=lambda *_: None) and r["respell"][0] == {"skill": "ski-l"} and r["retake"][0] == 2
    assert make.apply_repair({"lang": "pl"}, r, a, {"engine_used": "chatterbox"}, log=lambda *_: None) and r["retake"][0] == 3 and r["respell"][0] == {"skill": "ski-l"}   # none left: a plain retake
    r2 = make.new_recipe()
    assert make.apply_repair({"lang": "pl"}, r2, a, {"engine_used": "piper"}, log=lambda *_: None) is True and r2["respell"][0] == {"skill": "skil"}                   # a deterministic engine reads the new spelling


def test_slow_scene_slows_by_what_its_rate_asks_and_stops_at_the_floor():
    r = make.new_recipe()
    assert make.apply_repair({}, r, act("slow_scene", 3, heard=7.8), {}, log=lambda *_: None) and r["speed"][2] == pytest.approx(0.78, abs=0.01)
    assert make.apply_repair({}, r, act("slow_scene", 3, heard=7.0), {}, log=lambda *_: None) is False            # 0.78 * 0.83 is under the floor of 0.72
    r2 = make.new_recipe()
    make.apply_repair({}, r2, act("slow_scene", 1, by_words=6.9), {}, log=lambda *_: None)
    assert 0.8 < r2["speed"][0] < 0.9
    r3 = make.new_recipe()
    assert make.apply_repair({}, r3, act("slow_scene", 1), {}, log=lambda *_: None) and r3["speed"][0] < 0.95                 # no rate in the finding: a default of 7


def test_retrim_cuts_harder_each_time_until_there_is_nothing_left():
    r = make.new_recipe()
    results = [make.apply_repair({}, r, act("retrim_scene", 1), {}, log=lambda *_: None) for _ in range(5)]
    assert results[:3] == [True, True, True] and results[-1] is False
    assert r["cut"][0] == 18.0 and r["cap"][0] == 0.45


def test_remix_lowers_the_music_and_stops_at_the_floor():
    r = make.new_recipe()
    n = 0
    while make.apply_repair({}, r, act("remix"), {}, log=lambda *_: None):
        n += 1
        assert n < 20
    assert n >= 3 and r["music_mult"] == 0.25 and r["hp"] == 200


def test_level_scenes_once_master_only_when_it_was_off_layout_never():
    r = make.new_recipe()
    assert make.apply_repair({}, r, act("level_scenes"), {}, log=lambda *_: None) and r["level"] is True
    assert make.apply_repair({}, r, act("level_scenes"), {}, log=lambda *_: None) is False
    s = {"master": False}
    assert make.apply_repair(s, r, act("master"), {}, log=lambda *_: None) and s["master"] is True
    assert make.apply_repair(s, r, act("master"), {}, log=lambda *_: None) is False
    log = []
    assert make.apply_repair({}, r, act("relayout"), {}, log=log.append) is False and "layout" in log[0]
    assert make.apply_repair({}, r, act("rerender"), {}, log=lambda *_: None) is True


# ---------------------------------------------------------------- the loop, with a fake checker
def test_render_checked_repairs_checks_again_and_writes_the_report(tmp_path, monkeypatch):
    out = tmp_path / "r.mp4"
    calls = {"render": [], "check": 0}

    def fake_render(script, o, simple=False, workdir=None, recipe=None, meta=None, audio_only=False):
        calls["render"].append(audio_only)
        meta["engine_used"] = "chatterbox"
        Path(o).write_bytes(b"mp4")
        Path(o).with_suffix(".qa.json").write_text(json.dumps({"version": 2, "scenes": [{"start": 0.4, "end": 2.0}, {"start": 2.3, "end": 4.0}], "recipe": recipe}), encoding="utf-8")
        return Path(o)

    def fake_check(mp4, **opts):
        calls["check"] += 1
        bad = [Finding("ERROR", "VOICE_CUT", "chopped", 2, 3.0, {}), Finding("WARN", "LOUDNESS", "quiet", None, None, {})] if calls["check"] == 1 else []
        return {"findings": bad, "ctx": None, "side": {"video": {"width": 1080}}}
    monkeypatch.setattr(make, "render", fake_render)
    monkeypatch.setattr(qengine, "check_reel", fake_check)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)
    res = make.render_checked(good(), out, rounds=3, quick=True, log=lambda *_: None)
    assert res["verdict"] == "gotowe" and res["exit"] == 0 and calls["check"] == 2
    assert calls["render"] == [False, False]                                  # the first render, then one render again (a new take changes timing: no audio-only shortcut)
    assert res["history"][0]["before"] == (1, 1) and res["history"][0]["after"] == (0, 0)
    md = (tmp_path / "r_kontrola.md").read_text(encoding="utf-8")
    assert "Werdykt: gotowe" in md and "nagrano zdanie od nowa" in md
    side = json.loads((tmp_path / "r.qa.json").read_text(encoding="utf-8"))
    assert side["qa"]["verdict"] == "gotowe" and side["qa"]["rounds"][0]["actions"][0]["kind"] == "retake_scene" and side["video"] == {"width": 1080}
    assert side["recipe"]["retake"] == {"1": 1}


def test_render_checked_uses_a_cheap_mix_for_audio_only_repairs(tmp_path, monkeypatch):
    out = tmp_path / "r.mp4"
    modes = []

    def fake_render(script, o, simple=False, workdir=None, recipe=None, meta=None, audio_only=False):
        modes.append(audio_only)
        Path(o).write_bytes(b"mp4")
        Path(o).with_suffix(".qa.json").write_text(json.dumps({"scenes": []}), encoding="utf-8")
    state = {"n": 0}

    def fake_check(mp4, **opts):
        state["n"] += 1
        return {"findings": [Finding("WARN", "LOUDNESS", "quiet")] if state["n"] == 1 else [], "ctx": None, "side": {}}
    monkeypatch.setattr(make, "render", fake_render)
    monkeypatch.setattr(qengine, "check_reel", fake_check)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)
    s = good()
    s["master"] = False
    res = make.render_checked(s, out, rounds=2, quick=True, log=lambda *_: None)
    assert modes == [False, True] and res["verdict"] == "gotowe"


def test_render_checked_with_zero_rounds_only_checks(tmp_path, monkeypatch):
    out = tmp_path / "r.mp4"

    def fake_render(script, o, simple=False, **kw):
        Path(o).write_bytes(b"mp4")
        Path(o).with_suffix(".qa.json").write_text(json.dumps({"scenes": []}), encoding="utf-8")
    monkeypatch.setattr(make, "render", fake_render)
    monkeypatch.setattr(qengine, "check_reel", lambda mp4, **o: {"findings": [Finding("ERROR", "VOICE_CUT", "x", 1, 1.0, {})], "ctx": None, "side": {}})
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)
    res = make.render_checked(good(), out, rounds=0, quick=True, log=lambda *_: None)
    assert res["verdict"] == "niegotowe" and res["exit"] == 1 and res["history"] == []
    assert "niegotowe" in (tmp_path / "r_kontrola.md").read_text(encoding="utf-8")


def test_render_checked_undoes_a_round_that_makes_things_worse(tmp_path, monkeypatch):
    out = tmp_path / "r.mp4"
    state = {"version": 0, "n": 0}

    def fake_render(script, o, simple=False, workdir=None, recipe=None, meta=None, audio_only=False):
        state["version"] += 1
        meta["engine_used"] = "chatterbox"
        Path(o).write_bytes(f"mp4 v{state['version']}".encode())
        Path(o).with_suffix(".qa.json").write_text(json.dumps({"scenes": [{"start": 0, "end": 3}], "v": state["version"]}), encoding="utf-8")

    def fake_check(mp4, **opts):
        v = Path(mp4).read_bytes()
        bad = [Finding("WARN", "VOICE_FAST", "fast", 1, 1.0, {"heard": 7.5})] if v == b"mp4 v1" else [Finding("ERROR", "VOICE_CUT", "worse", 1, 1.0, {}), Finding("WARN", "VOICE_BREATH", "b", 1, 1.0, {"where": "inside"})]
        return {"findings": bad, "ctx": None, "side": {}}
    monkeypatch.setattr(make, "render", fake_render)
    monkeypatch.setattr(qengine, "check_reel", fake_check)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)
    res = make.render_checked(good(), out, rounds=3, quick=True, log=lambda *_: None)
    assert out.read_bytes() == b"mp4 v1"                                      # the first, better file is back
    assert res["verdict"] == "z uwagami" and res["history"][0]["restored"] is True
    assert json.loads((tmp_path / "r.qa.json").read_text(encoding="utf-8"))["v"] == 1


# ---------------------------------------------------------------- Chatterbox: only the scenes asked for, other seeds for a retake
def _fake_chatterbox(monkeypatch, tmp_path, fail=False):
    """a stand-in for the Chatterbox environment: records the job file it was given and writes a tone for every job (and take)"""
    seen = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    def run(cmd, *a, **k):
        spec = json.loads(Path(cmd[2]).read_text(encoding="utf-8"))
        seen.append(spec)
        if not fail:
            for j in spec["jobs"]:
                for take in range(int(spec.get("takes", 1))):
                    out = j["out"] if int(spec.get("takes", 1)) == 1 else j["out"].replace(".wav", f"_t{take}.wav")
                    music.write_wav(out, np.sin(np.linspace(0, 900 + take, int(0.5 * music.SR))) * 0.3)
        return Done()
    monkeypatch.setattr(make.subprocess, "run", run)
    monkeypatch.setattr(make, "voice_sample", lambda script: tmp_path / "voice.wav")
    monkeypatch.setattr(make, "pick_take", lambda takes, lang, toks: next((p for p in takes if Path(p).exists()), None))
    return seen


def test_chatterbox_records_only_the_scenes_asked_for_with_other_seeds_and_keeps_the_pristine_take(tmp_path, monkeypatch):
    seen = _fake_chatterbox(monkeypatch, tmp_path)
    s = {**good(), "learn": False, "fix_pace": False, "takes": 2}
    lines = [x["text"] for x in s["scenes"]] + [s["cta"]]
    r = make.new_recipe()
    assert make.chatterbox_batch(s, lines, tmp_path, recipe=r) is True
    assert [j["out"].endswith(f"s{i}.wav") for i, j in enumerate(seen[0]["jobs"])] == [True] * 4 and seen[0]["seed"] == 1000
    assert all((tmp_path / f"raw_{i}.wav").exists() for i in range(4)) and r["made"] == {0: 0, 1: 0, 2: 0, 3: 0}
    first = (tmp_path / "raw_2.wav").read_bytes()
    r["retake"][1] = 2
    assert make.chatterbox_batch(s, lines, tmp_path, only={1}, recipe=r) is True
    assert len(seen) == 2 and len(seen[1]["jobs"]) == 1 and seen[1]["jobs"][0]["out"].endswith("s1.wav") and seen[1]["seed"] == 1200
    assert r["made"][1] == 2 and (tmp_path / "raw_2.wav").read_bytes() == first             # the other scenes are untouched


def test_a_failed_chatterbox_run_keeps_the_earlier_take_and_reports_it(tmp_path, monkeypatch):
    seen = _fake_chatterbox(monkeypatch, tmp_path)
    s = {**good(), "learn": False, "fix_pace": False, "takes": 1}
    lines = [x["text"] for x in s["scenes"]] + [s["cta"]]
    r = make.new_recipe()
    make.chatterbox_batch(s, lines, tmp_path, recipe=r)
    before = (tmp_path / "raw_1.wav").read_bytes()
    _fake_chatterbox(monkeypatch, tmp_path, fail=True)
    r["retake"][1] = 1
    assert make.chatterbox_batch(s, lines, tmp_path, only={1}, recipe=r) is False
    assert (tmp_path / "raw_1.wav").read_bytes() == before and r["made"][1] == 0


@need_ffmpeg
def test_timeline_with_chatterbox_uses_the_takes_and_a_failed_retake_is_reported(tmp_path, monkeypatch):
    seen = _fake_chatterbox(monkeypatch, tmp_path)
    monkeypatch.setattr(make, "engine_for", lambda script: "chatterbox")
    s = {**good(), "learn": False, "fix_pace": False, "takes": 1}
    r, meta = make.new_recipe(), {}
    scenes, _t, audio = make.timeline(s, tmp_path, r, meta)
    assert meta["engine_used"] == "chatterbox" and audio is not None and len(seen) == 1
    r["retake"][0] = 1
    _fake_chatterbox(monkeypatch, tmp_path, fail=True)
    meta2 = {}
    make.timeline(s, tmp_path, r, meta2)
    assert meta2["engine_used"] == "chatterbox" and meta2["retake_failed"] == [0]            # the earlier take is used, and the loop is told
    monkeypatch.setattr(make, "tts", lambda *a, **k: False)                                   # the local voices are not what is tested here
    r2, meta3 = make.new_recipe(), {}
    (tmp_path / "fresh").mkdir()
    make.timeline(s, tmp_path / "fresh", r2, meta3)
    assert meta3["engine_used"] == "piper" or meta3["engine_used"] == "sapi"                  # a first run with nothing recorded falls back to a local voice, and says so


# ---------------------------------------------------------------- choosing the best take
def test_the_take_whose_words_are_heard_best_wins_and_a_dragged_or_rushed_take_loses(tmp_path, monkeypatch):
    takes = [tmp_path / f"t{k}.wav" for k in range(4)]
    for t in takes:
        t.write_bytes(b"x")
    al = {"t0": ([(0.0, 0.3), (0.3, 0.6), (0.6, 0.9)], 0.8), "t1": ([(0.0, 0.3), (0.3, 0.6), (0.6, 2.4)], 1.0),         # t1: the last word is dragged for 1.8 s
          "t2": ([(0.0, 0.5), (0.5, 1.0), (1.0, 1.5)], 1.0), "t3": ([(0.0, 0.05), (0.05, 0.1), (0.1, 0.15)], 1.0)}      # t3: far too fast

    def fake_align(p, lang, toks, heard_out=None):
        a, r = al[Path(p).stem]
        return a, r, [True] * len(toks), set()
    monkeypatch.setattr(make, "align_words", fake_align)
    monkeypatch.setattr(make, "ears_score", lambda paths: {})
    assert make.pick_take(takes, "pl", ["banana", "telefon", "komputer"]) == takes[2]                  # eight syllables: t3 is 50 a second, t2 is five
    assert make.pick_take([tmp_path / "missing.wav"], "pl", ["a"]) is None


def test_among_takes_that_are_heard_equally_well_the_one_with_the_least_breath_wins(tmp_path, monkeypatch):
    takes = [tmp_path / f"t{k}.wav" for k in range(3)]
    for t in takes:
        t.write_bytes(b"x")
    monkeypatch.setattr(make, "align_words", lambda p, lang, toks, heard_out=None: ([(0.0, 0.5), (0.5, 1.0)], 1.0, [True, True], set()))
    breath = {str(takes[0]): {"breath": 0.30, "mean": 0.1}, str(takes[1]): {"breath": 0.02, "mean": 0.01}, str(takes[2]): {"breath": 0.11, "mean": 0.05}}
    monkeypatch.setattr(make, "ears_score", lambda paths: breath)
    assert make.pick_take(takes, "pl", ["a", "b"]) == takes[1]


# ---------------------------------------------------------------- engine: the sound-event model and the recogniser
def test_listen_returns_the_models_windows_or_none(tmp_path, monkeypatch):
    mp4 = tmp_path / "r.mp4"
    mp4.write_bytes(b"x")
    assert qengine.listen(mp4, tts_python=tmp_path / "no-python.exe") is None

    class Done:
        returncode, stdout, stderr = 0, "", ""
    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(cmd)
        if "ears.py" in str(cmd[1]):
            Path(cmd[3]).write_text(json.dumps({"window": 1.0, "hop": 0.5, "windows": [{"t": 0.0, "labels": [["Speech", 0.9]]}]}), encoding="utf-8")
        return Done()
    monkeypatch.setattr(qengine.subprocess, "run", fake_run)
    py = tmp_path / "py.exe"
    py.write_bytes(b"x")
    got = qengine.listen(mp4, tts_python=py)
    assert got["windows"][0]["labels"][0][0] == "Speech" and any("ears.py" in str(c[1]) for c in calls if len(c) > 1)
    monkeypatch.setattr(qengine.subprocess, "run", lambda *a, **k: Done())               # the model ran but wrote nothing
    assert qengine.listen(mp4, tts_python=py) is None


def test_rehear_returns_the_words_of_the_finished_file_or_none(tmp_path, monkeypatch):
    import types

    class W:
        def __init__(self, w, s, e):
            self.word, self.start, self.end = w, s, e

    class Model:
        def transcribe(self, *a, **k):
            return [types.SimpleNamespace(words=[W(" Hello", 0.1, 0.4), W(" world", 0.4, 0.9)])], None

    class Done:
        returncode, stdout, stderr = 0, "", ""
    from reel_intel import common
    monkeypatch.setattr(common, "whisper_model", lambda: Model())
    monkeypatch.setattr(qengine.subprocess, "run", lambda *a, **k: Done())
    assert qengine.rehear(tmp_path / "r.mp4", "en") == [["Hello", 0.1, 0.4], ["world", 0.4, 0.9]]
    monkeypatch.setattr(common, "whisper_model", lambda: (_ for _ in ()).throw(RuntimeError("no model")))
    assert qengine.rehear(tmp_path / "r.mp4", "en") is None
