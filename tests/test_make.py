"""reel-intel make: script check, timeline and a tiny render without a voice (the voice engine is stubbed)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from reel_intel import make


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
