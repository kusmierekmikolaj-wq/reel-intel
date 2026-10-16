"""quality/platform.py: probe() and the container/platform detectors. The detectors are tested on plain facts dicts (no ffmpeg); probe() and the whole chain on small
mp4s that ffmpeg builds with each fault (skipped when ffmpeg/ffprobe are missing); a real generated reel when it is on this machine.

The fault list lives in platform.FAULTS (kind -> ffmpeg arguments, expected code) and platform.inject(kind) (the same faults as a facts dict), so the lead's
fault-injection matrix can import them.
"""
import os
import shutil
from pathlib import Path

import pytest

from reel_intel.quality import platform as P
from reel_intel.quality.base import REGISTRY, Ctx
from reel_intel.quality.profile import load

REPAIRS = {None, "retake_scene", "respell", "slow_scene", "retrim_scene", "remix", "master", "relayout", "rerender"}
CODES = ["FILE_BAD", "VIDEO_FACTS", "PLATFORM_ASPECT", "PLATFORM_SIZE", "PLATFORM_FPS", "PLATFORM_CODEC", "PLATFORM_PIXFMT", "PLATFORM_DURATION",
         "PLATFORM_FASTSTART", "AV_LENGTH", "AUDIO_RATE", "NO_AUDIO"]
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed")
ROLKA = Path(os.environ.get("REEL_TEST_ROLKI", "no-such-folder")) / "rolka_a.mp4"


def ctx_of(v, profile=None):
    return Ctx(side={"video": v}, profile=profile if profile is not None else load())


def run(v, profile=None):
    c = ctx_of(v, profile)
    return [f for d in P.DETECTORS for f in d(c)]


def bad(res):
    return [f for f in res if f.sev in ("ERROR", "WARN")]


def codes(res, sev=("ERROR", "WARN")):
    return {f.code for f in res if f.sev in sev}


# ---------------------------------------------------------------- registry

def test_codes_are_registered_with_a_known_repair():
    for code in CODES:
        r = REGISTRY[code]
        assert r["module"] == "platform" and r["desc"] and r["repair"] in REPAIRS and r["sev"] in ("ERROR", "WARN", "INFO")
    assert REGISTRY["NO_AUDIO"]["sev"] == "ERROR" and REGISTRY["PLATFORM_DURATION"]["sev"] == "ERROR" and REGISTRY["AUDIO_RATE"]["sev"] == "INFO"
    assert REGISTRY["PLATFORM_SIZE"]["sev"] == "WARN" and REGISTRY["PLATFORM_PIXFMT"]["sev"] == "WARN"


def test_every_fault_has_a_facts_injection():
    for kind in P.FAULTS:
        assert P.inject(kind)
    with pytest.raises(KeyError):
        P.inject("no_such_fault")


# ---------------------------------------------------------------- detectors on facts dicts (no ffmpeg)

EXPECT = {"clean": None, "size": "PLATFORM_SIZE", "aspect": "PLATFORM_ASPECT", "no_audio": "NO_AUDIO", "fps": "PLATFORM_FPS", "pix_fmt": "PLATFORM_PIXFMT",
          "faststart": "PLATFORM_FASTSTART", "too_short": "PLATFORM_DURATION", "too_long": "PLATFORM_DURATION", "av_length": "AV_LENGTH",
          "audio_rate": "AUDIO_RATE", "codec": "PLATFORM_CODEC", "unreadable": "FILE_BAD"}


@pytest.mark.parametrize("kind", sorted(EXPECT))
def test_injected_fact_fault_is_caught(kind):
    res = run(P.inject(kind))
    want = EXPECT[kind]
    if want is None:
        assert not bad(res), [f.msg for f in bad(res)]
    else:
        hit = [f for f in res if f.code == want]
        assert hit and hit[0].sev == REGISTRY[want]["sev"], [f.msg for f in res]


def test_clean_facts_say_what_was_checked():
    res = run(P.inject("clean"))
    assert codes(res, ("INFO",)) == {"VIDEO_FACTS"} and not bad(res)
    assert "1080x1920" in res[0].msg and "30.00 fps" in res[0].msg and "h264 yuv420p" in res[0].msg and "moov first" in res[0].msg


def test_no_facts_gives_one_info_line_and_no_verdict():
    for side in ({}, {"video": {}}):
        res = [f for d in P.DETECTORS for f in d(Ctx(side=side, profile=load()))]
        assert [f.code for f in res] == ["VIDEO_FACTS"] and res[0].sev == "INFO"


def test_unreadable_file_is_file_bad_and_nothing_else():
    res = run(P.inject("unreadable"))
    assert [(f.code, f.sev) for f in res] == [("FILE_BAD", "ERROR")]


def test_aspect_and_size_are_independent_detectors():
    wrong_ratio = run(P.inject("aspect"))
    assert codes(wrong_ratio) == {"PLATFORM_ASPECT", "PLATFORM_SIZE"} and {f.code: f.sev for f in wrong_ratio}["PLATFORM_ASPECT"] == "ERROR"
    assert codes(run(P.inject("size"))) == {"PLATFORM_SIZE"}                           # 720x1280 is 9:16: only the size is wrong


@pytest.mark.parametrize("w,h,ok", [(1080, 1920, True), (1080, 1925, True), (1080, 1940, False), (1100, 1920, False), (1080, 1080, False)])
def test_aspect_tolerance_is_one_percent(w, h, ok):
    v = P.inject("clean")
    v.update(width=w, height=h, display_width=w, display_height=h)
    assert (not [f for f in run(v) if f.code == "PLATFORM_ASPECT"]) is ok


@pytest.mark.parametrize("fps,ok", [(30.0, True), (29.97, True), (30.4, True), (30.6, False), (25.0, False), (60.0, False), (24.0, False)])
def test_fps_window(fps, ok):
    v = P.inject("clean")
    v.update(fps=fps, fps_nominal=fps)
    assert (not [f for f in run(v) if f.code == "PLATFORM_FPS"]) is ok


def test_variable_frame_rate_is_reported_under_the_fps_code():
    v = P.inject("clean")
    v.update(fps=30.0, fps_nominal=60.0)
    (f,) = [f for f in run(v) if f.code == "PLATFORM_FPS"]
    assert "variable" in f.msg


@pytest.mark.parametrize("d,ok", [(2.9, False), (3.0, True), (21.0, True), (90.0, True), (90.5, False), (95.0, False)])
def test_duration_window_is_the_profile_window(d, ok):
    v = P.inject("clean")
    v.update(duration=d, video_duration=d, audio_duration=d)
    assert (not [f for f in run(v) if f.code == "PLATFORM_DURATION"]) is ok


def test_profile_overrides_every_threshold():
    v = P.inject("clean")
    v.update(duration=120.0, video_duration=120.0, audio_duration=120.0)
    assert codes(run(v, {"platform": {"max_s": 180}})) == set()
    v = P.inject("size")
    assert codes(run(v, {"platform": {"width": 720, "height": 1280}})) == set()
    v = P.inject("av_length")
    assert codes(run(v, {"platform": {"av_length_s": 5.0}})) == set()
    v = P.inject("fps")
    assert codes(run(v, {"platform": {"fps": 25}})) == set()
    assert codes(run(P.inject("clean"), {})) == set()                                  # an empty profile falls back to profile.DEFAULT and the module's EXTRA


def test_pixel_format_cases():
    v = P.inject("clean")
    v.update(pix_fmt="yuvj420p", color_range="pc")
    (f,) = [f for f in run(v) if f.code == "PLATFORM_PIXFMT"]
    assert f.sev == "INFO" and "full range" in f.msg                                   # what our own Remotion renders write
    (f,) = [f for f in run(v, {"platform": {"pix_fmt_tolerated": []}}) if f.code == "PLATFORM_PIXFMT"]
    assert f.sev == "WARN"
    for fmt in ("yuv444p", "yuv422p", "yuv420p10le", "rgb24"):
        v.update(pix_fmt=fmt)
        assert codes(run(v)) == {"PLATFORM_PIXFMT"}


def test_codec_checks_video_and_sound():
    v = P.inject("clean")
    v.update(codec="hevc")
    assert not bad(run(v))
    v.update(codec="av1")
    assert codes(run(v)) == {"PLATFORM_CODEC"}
    v = P.inject("clean")
    v.update(audio_codec="mp3")
    (f,) = [f for f in run(v) if f.code == "PLATFORM_CODEC"]
    assert "mp3" in f.msg


def test_audio_rate_is_information_only():
    for rate, flag in ((44100, False), (48000, False), (24000, True), (22050, True), (16000, True)):
        v = P.inject("clean")
        v.update(audio_rate=rate)
        res = run(v)
        assert (("AUDIO_RATE" in codes(res, ("INFO",))) is flag) and not bad(res)
    assert "not known" in [f for f in run(P.inject("audio_rate")) if f.code == "AUDIO_RATE"][0].msg


def test_no_audio_is_an_error_and_does_not_also_report_a_length_difference():
    res = run(P.inject("no_audio"))
    assert codes(res) == {"NO_AUDIO"} and [f.sev for f in res if f.code == "NO_AUDIO"] == ["ERROR"]
    v = P.inject("clean")
    del v["has_audio"]
    v.update(audio_codec=None)                                                         # no has_audio key: decided by the audio codec
    assert codes(run(v)) == {"NO_AUDIO"}


def test_faststart_unknown_is_not_a_fault():
    v = P.inject("clean")
    v.update(faststart=None)
    assert not bad(run(v))


def test_av_length_tolerance():
    for extra, ok in ((0.0, True), (0.1, True), (0.14, True), (0.2, False), (-0.3, False)):
        v = P.inject("clean")
        v.update(audio_duration=v["video_duration"] + extra)
        assert (not [f for f in run(v) if f.code == "AV_LENGTH"]) is ok


# ---------------------------------------------------------------- facts from ffprobe JSON (canned)

REAL_JSON = {"streams": [
    {"codec_type": "video", "codec_name": "h264", "profile": "High", "width": 1080, "height": 1920, "pix_fmt": "yuvj420p", "color_range": "pc",
     "sample_aspect_ratio": "1:1", "r_frame_rate": "30/1", "avg_frame_rate": "30/1", "start_time": "0.000000", "duration": "21.033333", "bit_rate": "3486098", "nb_frames": "631"},
    {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2, "start_time": "0.000000", "duration": "21.035000", "bit_rate": "317369"}],
    "format": {"duration": "21.035000", "size": "10026398", "bit_rate": "3813224"}}


def test_facts_from_the_real_reels_ffprobe_output():
    v = P.facts_from_ffprobe(REAL_JSON)
    assert (v["width"], v["height"], v["fps"], v["codec"], v["pix_fmt"]) == (1080, 1920, 30.0, "h264", "yuvj420p")
    assert (v["audio_codec"], v["audio_rate"], v["audio_channels"], v["has_audio"]) == ("aac", 48000, 2, True)
    assert v["duration"] == pytest.approx(21.035) and v["video_duration"] == pytest.approx(21.033333) and v["audio_start"] == 0.0 and v["size"] == 10026398
    res = run({**v, "faststart": True})
    assert not bad(res) and "PLATFORM_PIXFMT" in codes(res, ("INFO",))


def test_facts_handle_missing_audio_ntsc_rate_rotation_and_anamorphic_pixels():
    import copy
    j = copy.deepcopy(REAL_JSON)
    j["streams"] = j["streams"][:1]
    v = P.facts_from_ffprobe(j)
    assert v["has_audio"] is False and "audio_codec" not in v and codes(run({**v, "faststart": True})) == {"NO_AUDIO"}
    j = copy.deepcopy(REAL_JSON)
    j["streams"][0].update(r_frame_rate="30000/1001", avg_frame_rate="30000/1001")
    assert P.facts_from_ffprobe(j)["fps"] == pytest.approx(29.97, abs=0.001)
    j = copy.deepcopy(REAL_JSON)                                                       # 1920x1080 stored sideways, flagged to turn 90 degrees: a 1080x1920 picture
    j["streams"][0].update(width=1920, height=1080, side_data_list=[{"side_data_type": "Display Matrix", "rotation": -90}])
    v = P.facts_from_ffprobe(j)
    assert (v["display_width"], v["display_height"]) == (1080, 1920) and not [f for f in run({**v, "faststart": True}) if f.code in ("PLATFORM_ASPECT", "PLATFORM_SIZE")]
    j = copy.deepcopy(REAL_JSON)                                                       # 540x1920 with pixels twice as wide: a 1080x1920 picture
    j["streams"][0].update(width=540, height=1920, sample_aspect_ratio="2:1")
    assert (P.facts_from_ffprobe(j)["display_width"]) == 1080
    j["streams"][0].update(sample_aspect_ratio="0:1")                                  # "not set" is square pixels
    assert (P.facts_from_ffprobe(j)["display_width"]) == 540
    j = copy.deepcopy(REAL_JSON)
    del j["streams"][0]["duration"]                                                    # a container without per-stream durations: frames / rate
    assert P.facts_from_ffprobe(j)["video_duration"] == pytest.approx(631 / 30.0)


# ---------------------------------------------------------------- faststart from the atoms (no ffmpeg)

def atom(typ, payload=b""):
    return (8 + len(payload)).to_bytes(4, "big") + typ + payload


def test_moov_position_from_hand_built_files(tmp_path):
    def put(name, data):
        p = tmp_path / name
        p.write_bytes(data)
        return p
    ftyp = atom(b"ftyp", b"isom\0\0\2\0isomiso2avc1mp41")
    assert P.moov_before_mdat(put("fast.mp4", ftyp + atom(b"moov", b"x" * 40) + atom(b"mdat", b"y" * 500))) is True
    assert P.moov_before_mdat(put("slow.mp4", ftyp + atom(b"free") + atom(b"mdat", b"y" * 500) + atom(b"moov", b"x" * 40))) is False
    big = (1).to_bytes(4, "big") + b"mdat" + (16 + 300).to_bytes(8, "big") + b"z" * 300                  # 64-bit size
    assert P.moov_before_mdat(put("big.mp4", ftyp + big + atom(b"moov", b"x" * 8))) is False
    to_eof = (0).to_bytes(4, "big") + b"mdat" + b"z" * 300                                              # size 0: runs to the end of the file
    assert P.moov_before_mdat(put("eof.mp4", ftyp + to_eof)) is False
    assert P.moov_before_mdat(put("frag.mp4", ftyp + atom(b"moov", b"x" * 8) + atom(b"moof", b"x") + atom(b"mdat", b"y"))) is True
    assert P.moov_before_mdat(put("onlyftyp.mp4", ftyp)) is None
    assert P.moov_before_mdat(put("text.txt", b"hello world, this is a text file and not a video")) is None
    assert P.moov_before_mdat(put("cut.mp4", ftyp + b"\0\0")) is None
    assert P.moov_before_mdat(put("empty.mp4", b"")) is None
    assert P.moov_before_mdat(put("badsize.mp4", ftyp + (3).to_bytes(4, "big") + b"moov")) is None
    assert P.moov_before_mdat(tmp_path / "missing.mp4") is None


# ---------------------------------------------------------------- real files built by ffmpeg

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("mp4s")
    cache = {}

    def get(kind):
        if kind not in cache:
            p = P.make_mp4(d / f"{kind}.mp4", **P.FAULTS[kind][0])
            if p is None:
                pytest.skip("ffmpeg could not build the test file")
            cache[kind] = P.probe(p)
        return cache[kind]
    return get


@needs_ffmpeg
def test_probe_reads_the_clean_file(built):
    v = built("clean")
    assert (v["width"], v["height"], v["codec"], v["pix_fmt"]) == (1080, 1920, "h264", "yuv420p")
    assert v["fps"] == pytest.approx(30.0) and v["has_audio"] and v["audio_codec"] == "aac" and v["audio_rate"] == 48000
    assert v["duration"] == pytest.approx(3.2, abs=0.1) and abs(v["video_duration"] - v["audio_duration"]) < 0.1
    assert v["faststart"] is True and v["size"] > 1000 and v["video_start"] is not None and v["audio_start"] is not None
    assert not bad(run(v)), [f.msg for f in bad(run(v))]


@needs_ffmpeg
@pytest.mark.parametrize("kind", [k for k in P.FAULTS if k != "clean"])
def test_fault_in_a_real_mp4_is_caught(kind, built):
    v = built(kind)
    want = P.FAULTS[kind][1]
    hit = [f for f in run(v) if f.code == want]
    assert hit and hit[0].sev == REGISTRY[want]["sev"], (kind, {k: v.get(k) for k in ("width", "height", "fps", "codec", "pix_fmt", "duration", "faststart", "audio_rate")})
    if kind == "no_audio":
        assert "AV_LENGTH" not in codes(run(v))
    if kind == "faststart":
        assert v["faststart"] is False


@needs_ffmpeg
def test_other_pixel_formats_in_real_files(tmp_path):
    p = P.make_mp4(tmp_path / "j.mp4", pix_fmt="yuvj420p")
    if p is None:
        pytest.skip("this ffmpeg cannot write yuvj420p")
    v = P.probe(p)
    assert v["pix_fmt"] == "yuvj420p" and [f.sev for f in run(v) if f.code == "PLATFORM_PIXFMT"] == ["INFO"] and not bad(run(v))


@needs_ffmpeg
def test_probe_of_something_that_is_not_a_video(tmp_path):
    p = tmp_path / "x.mp4"
    p.write_text("not a video at all", encoding="utf-8")
    v = P.probe(p)
    assert list(v) == ["error"]
    assert [(f.code, f.sev) for f in run(v)] == [("FILE_BAD", "ERROR")]
    assert list(P.probe(tmp_path / "missing.mp4")) == ["error"]


def test_probe_without_ffprobe_reports_it(monkeypatch, tmp_path):
    import subprocess

    def boom(*a, **k):
        raise FileNotFoundError("ffprobe")
    monkeypatch.setattr(subprocess, "run", boom)
    assert "not installed" in P.probe(tmp_path / "x.mp4")["error"]


@needs_ffmpeg
@pytest.mark.skipif(not ROLKA.is_file(), reason="the real reel is not on this machine")
def test_a_real_generated_reel_passes_with_only_information():
    v = P.probe(ROLKA)
    res = run(v)
    assert not bad(res), [f.msg for f in bad(res)]
    assert (v["width"], v["height"]) == (1080, 1920) and v["fps"] == pytest.approx(30.0) and v["faststart"] is True and v["audio_rate"] == 48000
    assert "PLATFORM_PIXFMT" in codes(res, ("INFO",))                                  # Remotion writes yuvj420p
    assert abs(v["video_duration"] - v["audio_duration"]) < 0.05 and v["video_start"] == 0.0 and v["audio_start"] == 0.0
    from reel_intel.quality import sync
    assert sync.check_av_offset(ctx_of(v))[0].sev == "INFO"                            # the start times feed the sync module
