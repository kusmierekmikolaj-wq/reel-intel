"""reel-intel make: script check, timeline and a tiny render without a voice (the voice engine is stubbed)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from reel_intel import make


@pytest.fixture(autouse=True)
def no_cloned_voice(monkeypatch):
    """the machine running the tests may have a cloned voice installed: tests must not call it"""
    monkeypatch.setattr(make, "chatterbox_ready", lambda script: False)


def good():
    return {"lang": "en", "scenes": [{"text": "Thirty reels and you watched none of them.", "screen": "30 reels"},
                                     {"text": "The program listens and writes it down.", "screen": "Speech to text"},
                                     {"text": "It reads the screen too.", "screen": "Reads the screen"}],
            "cta": "Comment which account is next.", "caption": "c", "hashtags": ["#a"]}


def test_check_accepts_a_good_script_and_rejects_bad_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(make, "DATA", tmp_path / "none")
    assert make.check(good()) == []
    bad = good()
    bad["lang"] = "xx"
    assert ("ERROR", 'lang must be "pl" or "en"') in make.check(bad)
    few = good()
    few["scenes"] = few["scenes"][:1]
    assert any(s == "ERROR" and "scenes" in m for s, m in make.check(few))
    long = good()
    long["scenes"][0]["text"] = " ".join(["word"] * 200)
    assert any(s == "ERROR" and "too long" in m for s, m in make.check(long))
    hook = good()
    hook["scenes"][0]["text"] = " ".join(["w"] * 20)
    assert any("hook" in m for _s, m in make.check(hook))


def test_check_warns_about_a_run_copied_from_a_scanned_video(tmp_path, monkeypatch):
    d = tmp_path / "data" / "r1"
    d.mkdir(parents=True)
    (d / "transcript.json").write_text(json.dumps({"segments": [{"text": "install this skill and then paste the command into your terminal"}]}), encoding="utf-8")
    monkeypatch.setattr(make, "DATA", tmp_path / "data")
    s = good()
    s["scenes"][1]["text"] = "Install this skill and then paste the command quickly."
    assert any("repeats 6 words" in m for _sev, m in make.check(s))
    assert not any("repeats 6 words" in m for _sev, m in make.check(good()))


def test_timeline_without_a_voice_uses_the_reading_speed(tmp_path, monkeypatch):
    monkeypatch.setattr(make, "tts", lambda *a, **k: False)
    scenes, total, audio = make.timeline(good(), tmp_path)
    assert audio is None and len(scenes) == 4                                   # three scenes + the call to action
    assert all(b["start"] > a["end"] for a, b in zip(scenes, scenes[1:]))     # a pause between scenes
    assert abs(scenes[0]["words"][-1][2] - scenes[0]["end"]) < 1e-6          # the last word ends with the sentence
    assert total > scenes[-1]["end"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed on this machine")
def test_render_makes_a_vertical_h264_file(tmp_path, monkeypatch):
    monkeypatch.setattr(make, "tts", lambda *a, **k: False)
    monkeypatch.setattr(make, "FPS", 4)
    monkeypatch.setattr(make, "studio_dir", lambda: None)                          # the simple renderer, whether or not Remotion is installed here
    out = make.render(good(), tmp_path / "r.mp4")
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert probe.stdout.strip() == "h264,1080,1920"
    assert (tmp_path / "r_opis.txt").read_text(encoding="utf-8").startswith("c")


def test_app_serves_a_reel_only_from_a_listed_account(tmp_path, monkeypatch):
    import threading
    import urllib.error
    import urllib.request
    from reel_intel import server
    (tmp_path / "bmw" / "rolki").mkdir(parents=True)
    (tmp_path / "bmw" / "Reele.html").write_text("<title>x</title>", encoding="utf-8")
    (tmp_path / "bmw" / "rolki" / "a.mp4").write_bytes(bytes(range(256)) * 40)
    (tmp_path / "bmw" / "rolki" / "secret.py").write_text("x", encoding="utf-8")
    monkeypatch.setattr(server, "WORK", tmp_path)
    monkeypatch.setattr(server, "LIBRARY", None)
    srv = server.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        r = urllib.request.urlopen(base + "/rolki/scan:bmw/a.mp4")
        assert r.status == 200 and len(r.read()) == 10240
        req = urllib.request.Request(base + "/rolki/scan:bmw/a.mp4", headers={"Range": "bytes=10-19"})
        r = urllib.request.urlopen(req)
        assert r.status == 206 and r.read() == bytes(range(10, 20))
        for bad in ("/rolki/scan:bmw/secret.py", "/rolki/scan:nope/a.mp4", "/rolki/scan:bmw/..%2Fa.mp4"):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + bad)
            assert e.value.code == 404
    finally:
        srv.shutdown()


def test_mouth_shapes_follow_the_letters_and_rest_in_silence():
    words = [("mama", 0.0, 0.4), ("tak", 0.5, 0.8)]
    scenes = [{"words": words}]
    mouth = [0.9] * 40                                                    # a loud voice the whole time
    vis = make.viseme_track(scenes, mouth, 1.2)
    assert vis[0] == 6 and 1 in vis[:13] and vis[len(vis) - 1] == 0      # m closes the lips, a opens, the end is at rest
    assert make.viseme_track(scenes, [0.0] * 40, 1.2) == [0] * 37        # no sound, no moving mouth


def test_mouth_track_without_a_voice_moves_only_during_words():
    scenes = [{"words": [("hello", 0.2, 0.6)]}]
    m = make.mouth_track(None, scenes, 1.0)
    assert max(m[6:18]) > 0.3 and m[-1] < 0.05


def test_voice_engine_choice(monkeypatch, tmp_path):
    monkeypatch.delenv("REEL_VOICE", raising=False)
    assert make.engine_for({"voice": "edge"}) == "edge"
    monkeypatch.setenv("REEL_VOICE", "sapi")
    assert make.engine_for({"lang": "pl"}) == "sapi"
    monkeypatch.delenv("REEL_VOICE")
    monkeypatch.setattr(make, "VOICES_DIR", tmp_path)                           # no model in this folder: the Windows voice is the fallback
    monkeypatch.delenv("REEL_PIPER_MODEL", raising=False)
    assert make.engine_for({"lang": "pl"}) == "sapi"
    (tmp_path / "pl_PL-darkman-medium.onnx").write_bytes(b"x")
    try:
        import piper  # noqa: F401
        assert make.engine_for({"lang": "pl"}) == "piper"
    except ImportError:
        assert make.engine_for({"lang": "pl"}) == "sapi"


def test_music_bed_has_the_right_length_and_is_not_silent():
    from reel_intel import music
    x = music.bed(6.0)
    assert len(x) == int(6.0 * music.SR) and abs(x).max() > 0.5 and x[:200].std() < x[len(x) // 2:len(x) // 2 + 200].std()   # it fades in


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed on this machine")
def test_music_is_mixed_under_the_voice(tmp_path):
    from reel_intel import music
    import numpy as np
    voice = tmp_path / "v.wav"
    music.write_wav(voice, np.sin(np.linspace(0, 400, int(2 * music.SR))) * 0.5)
    bed = tmp_path / "b.wav"
    music.write_wav(bed, music.bed(2.0))
    out = tmp_path / "m.wav"
    assert music.mix(voice, bed, out) and out.stat().st_size > 1000


def test_reel_qa_selftest_catches_every_injected_fault():
    from reel_intel import reel_qa
    caught, total = reel_qa.selftest()
    assert caught == total


def test_timeline_without_voice_still_reports_the_script_words(tmp_path, monkeypatch):
    monkeypatch.setattr(make, "tts", lambda *a, **k: False)
    scenes, _t, _a = make.timeline(good(), tmp_path)
    assert scenes[0]["heard"] is None and scenes[0]["tail"] == 0.0


def test_pronunciation_lexicon_and_token_mapping():
    text, toks, owner = make.spoken_form("Zainstaluj skilli z AI.", "pl", {"zainstaluj": "zainstaluj"})
    assert text == "Zainstaluj skili z Ej aj."
    assert toks[-2:] == ["Ej", "aj."] and owner[-2:] == [3, 3]                 # one script word, two spoken tokens
    assert make.say_word("Pliku,", "pl", {"pliku": "pli-ku"}) == "Pli-ku,"
    assert make.spoken_form("Hello AI", "en")[0] == "Hello AI"                 # no Polish respelling in English


def test_pronunciation_is_learned_and_reused(tmp_path, monkeypatch):
    from reel_intel import pronounce
    monkeypatch.setattr(pronounce, "STORE", tmp_path / "wymowa.json")
    c = pronounce.candidates("pliku,")
    assert "pli-ku" in c                                                       # syllables split for a clearer reading
    assert pronounce.candidates("AI")[0] == "a i"                               # an acronym spelled out
    assert "skil" in pronounce.candidates("skill")                              # English letter groups written the Polish way
    pronounce.record("pl", "pliku", "pli-ku", ok=True)
    assert make.say_word("pliku.", "pl") == "pli-ku."                         # the next reel uses what worked
    pronounce.record("pl", "wyślę", "wy-ślę", ok=False)
    assert "wy-ślę" not in pronounce.candidates("wyślę")                        # what failed is not tried again
    assert pronounce.syllables("komentarzu") == "ko-men-ta-rzu"


def test_speech_end_finds_a_last_word_the_recognizer_missed(tmp_path):
    from reel_intel import music
    import numpy as np
    sr = music.SR
    x = np.concatenate([np.sin(np.linspace(0, 900, sr)) * 0.5, np.zeros(sr // 2), np.sin(np.linspace(0, 300, sr // 3)) * 0.4, np.zeros(sr)])
    p = tmp_path / "v.wav"
    music.write_wav(p, x)
    assert abs(make.speech_end(p) - (1.0 + 0.5 + 1 / 3)) < 0.05            # the quiet gap does not end the speech, the last "word" does
