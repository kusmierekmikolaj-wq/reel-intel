"""quality/picture.py: faults in the picture of a reel. Every fault code gets a clean reel that must stay silent and a drawn reel with that one fault in it
that must be found with the right code (and with nothing else reported). The frames are drawn with Pillow and the OCR is the one that knows what was
drawn (FakeOCR), so the tests need neither ffmpeg nor RapidOCR; the ffmpeg path (FrameProvider) has its own tests that are skipped without ffmpeg."""
import os
import copy
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PIL")

from reel_intel.quality import picture as P  # noqa: E402
from reel_intel.quality import profile as PROFILE  # noqa: E402
from reel_intel.quality.base import REGISTRY, Ctx  # noqa: E402

FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
need_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed on this machine")
SCALE = 0.5                                                             # 540x960 frames: the scans are four times cheaper


def loud(findings):
    return [f for f in findings if f.sev in ("ERROR", "WARN")]


def codes(findings):
    return {f.code for f in loud(findings)}


@lru_cache(maxsize=None)
def scenario(kind, scale=SCALE):
    sc = P.inject(kind, scale=scale)
    return sc, tuple(P.run_all(sc.ctx))


def ctx_of(reel, **kw):
    ctx = Ctx(side=reel.side, profile=P.with_picture_defaults(copy.deepcopy(PROFILE.DEFAULT)), frames=reel.frame, fps=reel.fps)
    ctx.ocr = reel.ocr
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


# ---------------------------------------------------------------- the register
def test_every_code_is_registered_and_described():
    for code in ("PICTURE_OFF", "OCR_OFF", "PICTURE_ERROR", "FIRST_FRAME", "PICTURE_BLACK", "PICTURE_FREEZE", "FLICKER", "SAFE_ZONE", "TEXT_OVERLAP",
                 "TRANSITION_GHOST", "TEXT_LEGIBILITY", "CONTRAST", "CAPTION_SPEED"):
        assert code in REGISTRY, code
        assert REGISTRY[code]["desc"], code


def test_every_detector_has_a_docstring_and_every_fault_has_an_injection():
    for det in P.DETECTORS:
        assert (det.__doc__ or "").strip(), det.__name__
    reported = {"FIRST_FRAME", "PICTURE_BLACK", "PICTURE_FREEZE", "FLICKER", "SAFE_ZONE", "TEXT_OVERLAP", "TRANSITION_GHOST", "TEXT_LEGIBILITY", "CONTRAST", "CAPTION_SPEED"}
    assert reported <= set(P.INJECT_KINDS.values())


def test_unknown_injection_is_refused():
    with pytest.raises(KeyError):
        P.inject("nonsense")


# ---------------------------------------------------------------- the matrix: clean stays silent, each fault is found, and only it
def test_clean_reel_is_silent():
    sc, out = scenario("clean")
    assert sc.expect is None
    assert loud(out) == [], [f.msg for f in out]


@pytest.mark.parametrize("kind", list(P.INJECT_KINDS))
def test_fault_is_found_with_the_right_code_and_nothing_else(kind):
    sc, out = scenario(kind)
    assert sc.expect == P.INJECT_KINDS[kind]
    assert codes(out) == {sc.expect}, [(f.code, f.msg) for f in out]


@pytest.mark.parametrize("kind", ["safe_bottom", "overlap_card", "ghost", "first_black"])
def test_fault_is_found_at_full_size_too(kind):
    sc, out = scenario(kind, 1.0)
    assert codes(out) == {sc.expect}, [(f.code, f.msg) for f in out]


def test_clean_reel_is_silent_at_full_size():
    sc, out = scenario("clean", 1.0)
    assert loud(out) == [], [f.msg for f in out]


# ---------------------------------------------------------------- what each finding says
def test_findings_carry_time_scene_and_numbers():
    for kind, key in (("black_run", "duration"), ("freeze", "duration"), ("flicker", "flashes"), ("safe_top", "overflow_px"), ("overlap_text", "share"),
                      ("small_text", "est_px"), ("low_contrast", "ratio"), ("fast_caption", "cps")):
        f = [x for x in scenario(kind)[1] if x.code == P.INJECT_KINDS[kind]][0]
        assert key in f.data, (kind, f.data)
    f = [x for x in scenario("safe_top")[1] if x.code == "SAFE_ZONE"][0]
    assert f.scene == 1 and f.data["side"] == "top" and f.data["overflow_px"] > 100


def test_black_picture_over_speech_is_an_error_and_reports_the_stretch():
    f = [x for x in scenario("black_run")[1] if x.code == "PICTURE_BLACK"][0]
    assert f.sev == "ERROR"
    assert 2.8 <= f.data["start"] <= 3.2 and 3.8 <= f.data["end"] <= 4.2
    assert f.data["speech_s"] > 0.25


def test_black_picture_after_the_voice_is_only_a_warning():
    reel = P.SyntheticReel(scale=SCALE, fault="black_run", sentences=P._SENTENCES[:2], scene_s=0.8, gap_s=0.3, tail_s=3.0)   # the voice is over by 1.9 s; the black stretch is 3.0-4.0 s
    f = [x for x in P.check_black(ctx_of(reel)) if x.code == "PICTURE_BLACK"]
    assert f and all(x.sev == "WARN" for x in f)


def test_dead_first_frame_kinds():
    kinds = {k: [x for x in scenario(k)[1] if x.code == "FIRST_FRAME"][0] for k in ("first_black", "first_flat", "first_empty")}
    assert kinds["first_black"].data["kind"] == "black" and kinds["first_flat"].data["kind"] == "flat" and kinds["first_empty"].data["kind"] == "empty"
    assert all(f.sev == "ERROR" for f in kinds.values())


def test_an_empty_first_frame_that_fills_up_at_once_is_only_a_warning():
    reel = P.SyntheticReel(scale=SCALE, fault="first_empty_short")
    f = P.check_first_frame(ctx_of(reel))
    assert len(f) == 1 and f[0].sev == "WARN" and f[0].data["content_at"] is not None and f[0].data["content_at"] <= 0.5


def test_freeze_names_the_stretch():
    f = [x for x in scenario("freeze")[1] if x.code == "PICTURE_FREEZE"][0]
    assert 2.8 <= f.data["start"] <= 3.3 and f.data["duration"] > 2.0 and f.data["speech_share"] >= 0.5


def test_freeze_after_the_last_word_is_not_reported():
    reel = P.SyntheticReel(scale=SCALE, fault=None, tail_s=0.3)
    ctx = ctx_of(reel)
    base = reel.frame
    end_of_speech = reel.scenes[-1]["words"][-1][2]
    ctx.frames = lambda t: base(min(t, end_of_speech + 0.01))        # everything after the last word is one still frame
    ctx.side = {**reel.side, "total": reel.total + 3.0}
    assert P.check_freeze(ctx) == []


def test_safe_zone_names_the_side():
    sides = {k: [x for x in scenario(k)[1] if x.code == "SAFE_ZONE"][0].data["side"] for k in ("safe_top", "safe_bottom", "safe_side")}
    assert sides == {"safe_top": "top", "safe_bottom": "bottom", "safe_side": "left"}


def test_safe_zone_finding_says_the_sources_disagree():
    f = [x for x in scenario("safe_top")[1] if x.code == "SAFE_ZONE"][0]
    assert "uncertain" in f.msg


def test_safe_zone_uncertainty_follows_the_profile():
    reel = P.SyntheticReel(scale=SCALE, fault="safe_top")
    ctx = ctx_of(reel)
    ctx.profile["safe_zone"]["uncertain"] = False
    assert all("uncertain" not in f.msg for f in P.check_safe_zone(ctx))


def test_current_layout_puts_the_progress_bar_and_the_card_into_the_bottom_ui():
    sc, out = scenario("safe_current_layout")
    kinds = {f.data.get("kind") for f in out if f.code == "SAFE_ZONE"}
    assert "bar" in kinds                                              # the progress bar at y 1690 is under the reel's own bottom interface
    assert any(f.data.get("side") == "bottom" for f in out if f.code == "SAFE_ZONE")


def test_a_progress_bar_in_the_top_margin_is_decoration_but_one_in_the_bottom_interface_is_a_warning():
    top = P.inject("clean", scale=SCALE, lay={"bar_y": 150})
    f = [x for x in P.check_safe_zone(top.ctx) if x.data.get("kind") == "bar"]
    assert f and all(x.sev == "INFO" and x.data["decoration"] and x.data["side"] == "top" for x in f) and "decoration" in f[0].msg
    assert codes(P.run_all(top.ctx)) == set()                                                  # a clean reel with the bar there is silent
    low = P.inject("clean", scale=SCALE, lay={"bar_y": 1690})
    g = [x for x in P.check_safe_zone(low.ctx) if x.data.get("kind") == "bar"]
    assert g and all(x.sev == "WARN" and not x.data["decoration"] and x.data["side"] == "bottom" for x in g)


def test_ghost_names_the_scene_change():
    f = [x for x in scenario("ghost")[1] if x.code == "TRANSITION_GHOST"]
    assert f and all(x.scene in (2, 3, 4) for x in f)
    assert all(-4 <= x.data["frames_from_start"] <= 2 for x in f)


def test_ghost_check_needs_the_texts_of_both_scenes():
    reel = P.SyntheticReel(scale=SCALE, fault="ghost")
    ctx = ctx_of(reel)
    for sc in ctx.scenes:
        sc["text"], sc["screen"] = "", ""
    assert P.check_ghost(ctx) == []


def test_text_overlap_tells_text_from_card_edge():
    what = {k: [x for x in scenario(k)[1] if x.code == "TEXT_OVERLAP"][0].data["what"] for k in ("overlap_text", "overlap_card")}
    assert what["overlap_text"] == "text" and what["overlap_card"] == "panel"


def test_fast_and_short_captions_are_told_apart():
    fast = [x for x in scenario("fast_caption")[1] if x.code == "CAPTION_SPEED"]
    short = [x for x in scenario("short_caption")[1] if x.code == "CAPTION_SPEED"]
    assert {f.data["what"] for f in fast} == {"fast"}
    assert "short" in {f.data["what"] for f in short}                    # a 0.4 s scene with a whole sentence on it is also too fast to read
    assert fast[0].scene == 2 and short[0].scene == 2


def test_caption_speed_works_without_any_frames():
    reel = P.SyntheticReel(scale=SCALE, fault="fast_caption")
    ctx = ctx_of(reel, frames=None)
    ctx.ocr = None
    assert [f.code for f in P.check_caption_speed(ctx)] == ["CAPTION_SPEED"]


# ---------------------------------------------------------------- degraded modes say so instead of failing silently
def test_without_frames_it_says_once_that_the_picture_was_not_checked():
    reel = P.SyntheticReel(scale=SCALE)
    ctx = ctx_of(reel, frames=None)
    out = P.run_all(ctx)
    assert [f.code for f in out if f.code != "CAPTION_SPEED"] == ["PICTURE_OFF"]
    assert out[0].sev == "INFO"


def test_without_ocr_it_says_so_and_still_checks_what_needs_no_text():
    reel = P.SyntheticReel(scale=SCALE, fault="black_run")
    ctx = ctx_of(reel)
    ctx.ocr = None
    out = P.run_all(ctx)
    assert "OCR_OFF" in {f.code for f in out}
    assert "PICTURE_BLACK" in codes(out)
    assert not (codes(out) & {"TEXT_OVERLAP", "TRANSITION_GHOST", "TEXT_LEGIBILITY", "CONTRAST"})


def test_a_frame_provider_that_returns_nothing_is_not_a_crash():
    reel = P.SyntheticReel(scale=SCALE)
    ctx = ctx_of(reel, frames=lambda t: None)
    out = P.run_all(ctx)
    assert "PICTURE_ERROR" not in {f.code for f in out}
    assert loud(out) == []


def test_a_frame_provider_that_raises_is_not_a_crash():
    def boom(t):
        raise RuntimeError("decoder died")
    out = P.run_all(ctx_of(P.SyntheticReel(scale=SCALE), frames=boom))
    assert "PICTURE_ERROR" not in {f.code for f in out}


def test_a_crashing_detector_becomes_a_finding_and_the_others_still_run(monkeypatch):
    def bad(ctx):
        raise ValueError("boom")
    monkeypatch.setattr(P, "DETECTORS", [bad, P.check_caption_speed])
    reel = P.SyntheticReel(scale=SCALE, fault="fast_caption")
    out = P.run_all(ctx_of(reel))
    assert [f.code for f in out] == ["PICTURE_ERROR", "CAPTION_SPEED"]
    assert "boom" in out[0].msg and out[0].sev == "WARN"


def test_a_scene_with_no_usable_times_is_skipped():
    reel = P.SyntheticReel(scale=SCALE)
    ctx = ctx_of(reel)
    ctx.side = {"total": reel.total, "scenes": [{"text": "x"}] + reel.scenes}
    assert "PICTURE_ERROR" not in {f.code for f in P.run_all(ctx)}


# ---------------------------------------------------------------- the pure pieces
def test_contrast_ratio_matches_wcag():
    assert P.contrast_ratio(1.0, 0.0) == pytest.approx(21.0)
    assert P.contrast_ratio(0.0, 1.0) == pytest.approx(21.0)
    assert P.contrast_ratio(0.5, 0.5) == pytest.approx(1.0)
    # black on white from pixels: relative luminance 1 and 0
    assert P.contrast_ratio(float(P._rel_lum(np.array([255, 255, 255]))), float(P._rel_lum(np.array([0, 0, 0])))) == pytest.approx(21.0, rel=1e-3)
    # the WCAG example: #767676 on white is 4.54:1
    assert P.contrast_ratio(float(P._rel_lum(np.array([255, 255, 255]))), float(P._rel_lum(np.array([0x76, 0x76, 0x76])))) == pytest.approx(4.54, abs=0.02)


def test_text_contrast_of_drawn_text():
    from PIL import Image, ImageDraw
    for fg, bg, lo, hi in (((255, 255, 255), (0, 0, 0), 15, 22), ((0, 0, 0), (255, 255, 255), 15, 22), ((60, 60, 60), (50, 50, 50), 1.0, 1.3)):
        img = Image.new("RGB", (400, 120), bg)
        d = ImageDraw.Draw(img)
        d.text((200, 60), "Contrast", font=P._font(60), fill=fg, anchor="mm")
        x0, y0, x1, y1 = d.textbbox((200, 60), "Contrast", font=P._font(60), anchor="mm")
        r = P.text_contrast(np.asarray(img), P.TextBox(x0 - 6, y0 - 6, x1 + 6, y1 + 6, "Contrast"))
        assert lo <= r <= hi, (fg, bg, r)


def test_text_contrast_of_a_box_too_small_to_say_is_none():
    assert P.text_contrast(np.zeros((50, 50, 3), np.uint8), P.TextBox(0, 0, 4, 4, "x")) is None


def test_count_flashes():
    hz = 30
    t = np.arange(0, 2.0, 1 / hz)
    flat = np.full(len(t), 0.4)
    assert P.count_flashes(flat, hz)[0] == 0
    square = np.where((t * 8).astype(int) % 2 == 0, 0.7, 0.05)           # 4 flashes a second
    k, n, at = P.count_flashes(square, hz)
    assert k >= 3
    slow = np.where((t * 2).astype(int) % 2 == 0, 0.7, 0.05)              # 1 flash a second
    assert P.count_flashes(slow, hz)[0] <= 1
    tiny = 0.4 + 0.02 * np.sign(np.sin(t * 40))                           # swings of 4% of the range: under the 10% rule
    assert P.count_flashes(tiny, hz)[0] == 0
    bright = np.where((t * 8).astype(int) % 2 == 0, 0.98, 0.9)            # flashes between two bright levels: the darker side is over 0.80
    assert P.count_flashes(bright, hz, delta=0.05)[0] == 0
    assert P.count_flashes([0.5], hz) == (0, 0, 0.0)


def test_est_font_px_follows_the_glyph_class():
    r = P.PICTURE_DEFAULTS["captions"]["em_ratio"]
    assert P.glyph_class("aceon") == "x" and P.glyph_class("Hello") == "asc" and P.glyph_class("gpq") == "desc" and P.glyph_class("Hag") == "both"
    assert P.glyph_class("łódź") == "asc"                                 # accents count as ascenders
    big = P.TextBox(0, 0, 100, 68, "Hag")
    assert P.est_font_px(big, r) == pytest.approx(68 / r["both"])
    assert P.est_font_px(big, r, scale=0.5) == pytest.approx(2 * 68 / r["both"])


def test_boxes_from_ocr_reads_rapidocr_output():
    res = [([[10, 20], [110, 20], [110, 60], [10, 60]], "Hello", "0.97"), ([[0, 0], [5, 0], [5, 5], [0, 5]], "noise", 0.2), ([[0, 0], [1, 1]], "", 0.9), "junk"]
    out = P.boxes_from_ocr(res, 0.5)
    assert [b.text for b in out] == ["Hello"] and out[0].box() == [10.0, 20.0, 110.0, 60.0]
    assert P.boxes_from_ocr((res, 0.1), 0.5)[0].text == "Hello"            # RapidOCR returns (result, elapse)
    assert P.boxes_from_ocr((None, 0.1)) == [] and P.boxes_from_ocr(None) == []


def test_group_blocks_joins_fragments_of_a_line_and_keeps_lines_apart():
    a, b, c = P.TextBox(0, 0, 100, 40, "one"), P.TextBox(110, 2, 220, 42, "two"), P.TextBox(0, 60, 100, 100, "three")
    P.group_blocks([c, b, a])
    assert a.block == b.block != c.block


def test_small_frame_is_a_block_mean():
    f = np.zeros((20, 40, 3), np.uint8)
    f[:, 20:] = 200
    s = P.small_frame(f, width=10)
    assert s.shape == (5, 10, 3) and s[:, :5].max() == 0 and s[:, 5:].min() == 200


def test_find_ui_finds_a_card_and_a_bar_and_nothing_in_a_plain_frame():
    plain = np.full((960, 540, 3), 20, np.uint8)
    assert P.find_ui(plain) == []
    img = plain.copy()
    img[300:302, 40:500] = 70                                              # card border, top (2 px, like the glass card)
    img[560:562, 40:500] = 70                                              # card border, bottom
    img[302:560, 40:500] = 36                                              # card fill
    img[700:712, 45:495] = 90                                              # progress bar, 12 px thick
    kinds = {b.kind: b for b in P.find_ui(img, scale=0.5)}
    assert set(kinds) == {"panel", "bar"}
    assert abs(kinds["panel"].y0 - 301) <= 6 and abs(kinds["panel"].y1 - 561) <= 6
    assert abs(kinds["bar"].y0 - 700) <= 6


def test_frame_stats_tell_an_empty_frame_from_a_drawn_one():
    flat = P.frame_stats(np.full((60, 40, 3), 90, np.uint8))
    assert flat["std"] == 0 and flat["edge"] == 0
    drawn = np.zeros((60, 40, 3), np.uint8)
    drawn[20:30, :] = 255
    assert P.frame_stats(drawn)["edge"] > 0.05


# ---------------------------------------------------------------- the layout summary
def test_layout_report_of_the_current_layout_lists_what_sticks_out():
    sc, _ = scenario("safe_current_layout")
    rep = P.layout_report(sc.ctx)
    assert rep["safe"]["y1"] == 1920 - sc.ctx.profile["safe_zone"]["bottom"] and rep["ocr"] is True
    bar = [e for e in rep["furniture"] if e["kind"] == "bar"]
    assert bar and bar[0]["overflow_px"].get("bottom", 0) > 100 and 1690 in {round(v, -1) for v in bar[0]["bottoms"]} | {1690}
    assert any(e["side"] == "bottom" for s in rep["scenes"] for e in s["ui"])        # the caption card runs past the bottom limit
    text = P.format_layout(rep)
    assert "safe area" in text and "OUTSIDE" in text and "uncertain" in text


def test_layout_report_of_the_clean_layout_has_nothing_outside():
    sc, _ = scenario("clean")
    rep = P.layout_report(sc.ctx)
    assert all(ln["side"] is None for s in rep["scenes"] for ln in s["lines"])
    assert all(not e["overflow_px"] for e in rep["furniture"])
    assert rep["first_frame"]["edge"] > 0.0 and rep["captions"]


def test_timeline_stats():
    sc, _ = scenario("clean")
    st = P.timeline_stats(sc.ctx)
    assert st["black_longest_s"] == 0 and st["flashes_per_s"] == 0 and st["min_mad"] > P.PICTURE_DEFAULTS["picture"]["freeze_mad"]
    bl = P.timeline_stats(scenario("black_run")[0].ctx)
    assert bl["black_longest_s"] >= 0.9
    assert P.timeline_stats(ctx_of(P.SyntheticReel(scale=SCALE), frames=None)) is None


# ---------------------------------------------------------------- the profile
def test_with_picture_defaults_does_not_touch_the_default_profile():
    before = copy.deepcopy(PROFILE.DEFAULT)
    out = P.with_picture_defaults(PROFILE.DEFAULT)
    assert PROFILE.DEFAULT == before
    assert out["picture"]["freeze_mad"] == P.PICTURE_DEFAULTS["picture"]["freeze_mad"] and out["captions"]["em_ratio"]["x"] > 0
    out["picture"]["freeze_mad"] = 99
    assert P.PICTURE_DEFAULTS["picture"]["freeze_mad"] != 99


def test_a_looser_profile_silences_a_finding():
    reel = P.SyntheticReel(scale=SCALE, fault="fast_caption")
    ctx = ctx_of(reel)
    assert P.check_caption_speed(ctx)
    ctx.profile["captions"]["max_cps"] = 200
    assert P.check_caption_speed(ctx) == []


def test_a_stricter_safe_zone_finds_more():
    reel = P.SyntheticReel(scale=SCALE)
    ctx = ctx_of(reel)
    assert P.check_safe_zone(ctx) == []
    ctx.profile["safe_zone"]["top"] = 600                              # the slogan of the clean layout starts at y 400
    assert any(f.data.get("side") == "top" for f in P.check_safe_zone(ctx))


# ---------------------------------------------------------------- the ffmpeg path (frames of a real mp4)
def _encode(reel, path, fps=30):
    w, h = reel.W, reel.H
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "libx264",
                          "-pix_fmt", "yuv420p", "-crf", "14", "-preset", "veryfast", str(path)], stdin=subprocess.PIPE)
    for k in range(int(reel.total * fps)):
        p.stdin.write(np.asarray(reel.frame(k / fps)).tobytes())
    p.stdin.close()
    assert p.wait() == 0


@pytest.fixture(scope="module")
def encoded(tmp_path_factory):
    d = tmp_path_factory.mktemp("pic")
    paths = {}
    for kind in ("clean", "black_run", "freeze", "flicker"):
        reel = P.SyntheticReel(scale=0.25, fault=None if kind == "clean" else kind)        # 270x480, even sides
        path = d / f"{kind}.mp4"
        _encode(reel, path)
        paths[kind] = (path, reel)
    return paths


def _mp4_ctx(path, reel):
    ctx = Ctx(side=reel.side, profile=P.with_picture_defaults(copy.deepcopy(PROFILE.DEFAULT)), frames=P.frame_provider(path), fps=30)
    ctx.ocr = None
    return ctx


@need_ffmpeg
def test_frame_provider_reads_size_frames_and_ranges(encoded):
    path, reel = encoded["clean"]
    fp = P.frame_provider(path)
    assert fp.size == (270, 480)
    f = fp(1.0)
    assert f.shape == (480, 270, 3) and f.dtype == np.uint8 and f.flags.writeable
    assert fp(1.0) is f                                                  # cached
    assert fp(500.0) is None                                             # past the end
    times, arr = fp.small_range(0.0, 2.0, 10)
    assert arr.shape[0] in (19, 20, 21) and arr.shape[2] == P.SMALL_W and len(times) == len(arr)
    assert P.frame_provider(path.with_name("nope.mp4")) is None


@need_ffmpeg
def test_frame_provider_frames_match_what_was_drawn(encoded):
    path, reel = encoded["clean"]
    fp = P.frame_provider(path)
    for t in (0.5, 2.0, 4.0):
        a = np.asarray(fp(t)).astype(float)
        b = np.asarray(reel.frame(t)).astype(float)
        assert np.abs(a - b).mean() < 6.0, t                              # lossy video, same picture


@need_ffmpeg
def test_a_clean_encoded_video_is_silent_without_ocr(encoded):
    path, reel = encoded["clean"]
    out = P.run_all(_mp4_ctx(path, reel))
    assert loud(out) == [], [(f.code, f.msg) for f in out]


@need_ffmpeg
@pytest.mark.parametrize("kind,code", [("black_run", "PICTURE_BLACK"), ("freeze", "PICTURE_FREEZE"), ("flicker", "FLICKER")])
def test_faults_survive_video_compression(encoded, kind, code):
    path, reel = encoded[kind]
    assert code in codes(P.run_all(_mp4_ctx(path, reel)))


# ---------------------------------------------------------------- real reels (only when the files are on this machine)
REAL = Path(os.environ.get("REEL_TEST_ROLKI", "no-such-folder")) / "rolka_a.mp4"


@need_ffmpeg
@pytest.mark.skipif(not REAL.exists(), reason="the reference reel is not on this machine")
def test_real_reel_runs_through_every_detector_without_a_crash():
    import json
    side = json.loads(REAL.with_suffix(".qa.json").read_text(encoding="utf-8")) if REAL.with_suffix(".qa.json").exists() else {"scenes": []}
    ctx = Ctx(side=side, profile=P.with_picture_defaults(copy.deepcopy(PROFILE.DEFAULT)), frames=P.frame_provider(REAL), fps=30)
    ctx.ocr = None
    out = P.run_all(ctx)
    assert "PICTURE_ERROR" not in {f.code for f in out}, [f.msg for f in out]
    assert "PICTURE_BLACK" not in {f.code for f in out}
