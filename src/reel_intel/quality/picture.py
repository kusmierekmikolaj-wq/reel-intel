"""picture.py - faults in the PICTURE of a reel: dead first frame, black and frozen stretches, flicker, text outside the safe area, text running into
other text or into the caption card, a ghost of the previous scene, text too small or too faint, captions that go by too fast.

Every detector is a pure function of numbers (numpy only). What it needs from the Ctx (see base.py):
  ctx.frames   callable(t_seconds) -> HxWx3 uint8 RGB frame, or None. frame_provider(mp4) builds one with ffmpeg (the only place that starts a process).
               A provider may also have  small_range(t0, t1, hz) -> (times, uint8 array n x h x w x 3)  which makes the black / freeze / flicker scans cheap;
               a plain callable works too, it is simply sampled frame by frame.
  ctx.ocr      callable(frame) -> [TextBox]; default_ocr() wraps RapidOCR. ctx.ocr is NOT a field of Ctx yet: set it as an attribute
               (ctx.ocr = picture.default_ocr()). None means the text checks are skipped with an INFO line (OCR_OFF).
  ctx.scenes   the sidecar scenes: start, end, text, screen, words [[word, t0, t1], ...]. Optional: side["total"] (seconds), side["renderer"] (unused here).
  ctx.profile  thresholds: picture.*, safe_zone.*, captions.*, platform.width/height. The keys this module adds are listed in PICTURE_DEFAULTS and are
               used when the profile has no such key (merge them into profile.DEFAULT with with_picture_defaults()).
  ctx.fps      frames a second of the video.
Without frames every detector returns [] and check_available() says so once (PICTURE_OFF); without OCR the detectors that need text positions return [].

Pixel sizes in the profile are for a 1080x1920 frame; a detector scales them by the real frame height, so thumbnails and the 540x960 frames of the
reference reels work too.

What it cannot judge (said plainly):
  - Text positions come from an OCR; text that is blurred, half transparent or tiny can be missed, and a missed ghost is not a clean transition.
  - The font size is estimated from the height of the OCR box (measured: the box is 0.58 em for lines with only x-height letters, 1.17 em for lines with
    ascenders and descenders, n=150); the estimate is lenient, so it flags only text that is certainly small. Error about +-20%.
  - find_ui() reads panels and bars from long straight edges. It is tuned to the flat, rendered graphics of our own renderer; on photographic footage a
    long edge (a table, a horizon) can be reported as a bar. It is a layout check of generated reels, not of arbitrary video.
  - SAFE_ZONE numbers come from sources that disagree (top 200-270, bottom 350-672, sides 65-130 px); the finding says so.
  - FLICKER is the WCAG "general flash" test (10% of the maximum relative luminance, more than 3 a second) on the mean of the frame and of its four
    quadrants (25% of the area each). It does not model the red-flash rule or the visual angle.
  - PICTURE_FREEZE flags every stretch of identical frames while someone speaks, so a static slide with a voice over is flagged too (in the 31 clips
    measured, 12 are such slides). That is the rule, not a bug of the reel.
  - CONTRAST is measured for the dominant bright (or dark) colour of the text; dimmer words in the same line (unlit karaoke words) are not measured
    separately and the WCAG relaxation for large text is not applied.
  - CAPTION_SPEED counts the characters of the sentence against the time the caption card stays on screen; it cannot tell how fast a person reads.
"""
from __future__ import annotations

import difflib
import math
import re
import shutil
import subprocess
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from .base import Ctx, Finding, finding, register

MODULE = "picture"

# ---------------------------------------------------------------- the fault codes
PICTURE_OFF = register("PICTURE_OFF", "picture checks skipped: no frames of the video are available", "INFO", None, MODULE)
OCR_OFF = register("OCR_OFF", "text position checks skipped: no OCR is available", "INFO", None, MODULE)
PICTURE_ERROR = register("PICTURE_ERROR", "a picture detector crashed (a bug in the detector, not in the reel)", "WARN", None, MODULE)
FIRST_FRAME = register("FIRST_FRAME", "the first frame is black, flat or empty: a dead hook", "ERROR", "relayout", MODULE)
PICTURE_BLACK = register("PICTURE_BLACK", "black frames for longer than picture.black_s", "ERROR", "rerender", MODULE)
PICTURE_FREEZE = register("PICTURE_FREEZE", "identical frames for longer than picture.freeze_s while speech is running", "WARN", "rerender", MODULE)
FLICKER = register("FLICKER", "luminance flashes more than picture.flash_per_s times a second", "WARN", "relayout", MODULE)
SAFE_ZONE = register("SAFE_ZONE", "text, a panel or a bar outside the safe rectangle of the reel (uncertain: sources disagree)", "WARN", "relayout", MODULE)
TEXT_OVERLAP = register("TEXT_OVERLAP", "two pieces of text overlap, or a text runs over the edge of a panel (the slogan running into the caption card)", "ERROR", "relayout", MODULE)
TRANSITION_GHOST = register("TRANSITION_GHOST", "the text of two scenes is on screen at once around a scene change", "WARN", "relayout", MODULE)
TEXT_LEGIBILITY = register("TEXT_LEGIBILITY", "text smaller than captions.min_px", "WARN", "relayout", MODULE)
CONTRAST = register("CONTRAST", "WCAG contrast ratio of text against its background below captions.min_contrast", "WARN", "relayout", MODULE)
CAPTION_SPEED = register("CAPTION_SPEED", "caption block with more characters a second than captions.max_cps, or shown shorter than captions.min_show_s", "WARN", "slow_scene", MODULE)

# ---------------------------------------------------------------- thresholds this module adds to the profile
# Merge into profile.DEFAULT (with_picture_defaults does it for a copy). Where a number was measured it says so; the rest are the standards' numbers.
PICTURE_DEFAULTS = {
    "picture": {
        "black_level": 0.05,            # luma/255 under which a pixel is black (ffmpeg blackdetect pix_th, as reel_qa.media uses it)
        "black_share": 0.98,            # share of black pixels that makes a frame black (blackdetect pic_th)
        "first_frame_flat_std": 0.02,   # luma std/255 under which the first frame is one flat colour
        "first_frame_min_edge": 0.03,   # share of strong edges (= something drawn) the first frame needs; measured: 18 real first frames min 0.062, the empty frame of our renderer 0.0 (n=18+1, 108 px wide, step >= 30)
        "content_edge_delta": 30,       # luma steps (0-255) of the 108 px wide thumbnail that count as an edge
        "hook_s": 0.8,                  # something must be on screen by then (plan: the first word at 0.8 s at the latest)
        "probe_hz": 10,                 # sampling rate of the black and freeze scans
        "freeze_mad": 0.1,              # mean |luma difference| (0-255) of samples 0.1 s apart under which two frames are identical; measured: our renderer min 0.284, real frozen stretches 0.000-0.005 (31 clips, 108x192, 10 Hz)
        "flicker_hz": 30,               # sampling rate of the flash scan (every frame: a slower scan aliases)
        "flash_delta": 0.10,            # WCAG 2.3.1: opposing changes of 10% of the maximum relative luminance
        "flash_dark_max": 0.80,         # ... where the darker image is under 0.80
        "flash_window_s": 1.0,
        "overlap_share": 0.15,          # plan: overlap of more than 15% of the smaller box, boxes of different lines
        "overlap_same_line": 0.5,       # OCR splits one line into fragments that overlap by up to 20%: only more than this counts
        "straddle_min": 0.15,           # a text box that is between 15% and 85% inside a panel runs over its edge
        "straddle_max": 0.85,
        "ghost_frames": [-4, -3, -2, -1, 0, 1, 2],      # frames around the first frame of the next scene that are read
        "ocr_min_conf": 0.5,
        "safe_tol_px": 6,               # OCR boxes are padded: this much outside the safe rectangle is not reported (1080x1920 pixels)
        "ui_detect": True,
        "ui_delta": 18,                 # luma step (0-255) of a panel or bar edge; measured on the reel: borders 25-35, progress track 31, background grid 14
        "ui_min_w": 0.5,                # an edge must run this share of the frame width
        "ui_thin_px": 4,                # edges up to this thick are lines (panel borders), thicker ones are bars (1080x1920 pixels)
        "ui_bar_max_px": 24,
        "ui_panel_min_px": 80,
        "ui_fill_delta": 8,             # a panel's inside differs from the outside by this much (luma 0-255)
        "ui_persist": 2,                # a panel or bar counts when it is seen in this many frames at the same place (a moving scan line is not)
        "ui_tol_px": 12,
    },
    "captions": {
        "hold_s": 0.28,                 # the card stays this long after the last word (Reel.tsx GAP)
        # OCR box height / font size by what the line contains: measured with RapidOCR on PIL text, 4 fonts, 10 sizes 24-138 px (n=150), the p10 of each
        # class, so the estimate is lenient (median: x 0.58, asc 0.88, desc 0.875, both 1.17)
        "em_ratio": {"x": 0.55, "asc": 0.82, "desc": 0.79, "both": 1.01},
    },
}

SMALL_W = 108                           # width of the thumbnails every scan works on


def with_picture_defaults(profile):
    """a copy of `profile` with the keys this module adds filled in (profile.DEFAULT is not changed)"""
    import copy
    out = copy.deepcopy(profile)
    for sec, vals in PICTURE_DEFAULTS.items():
        d = out.setdefault(sec, {})
        for k, v in vals.items():
            d.setdefault(k, copy.deepcopy(v))
    return out


def _prof(ctx):
    return (ctx if isinstance(ctx, dict) else getattr(ctx, "profile", None)) or {}


def _cfg(ctx, section, key):
    v = (_prof(ctx).get(section) or {}).get(key)
    if v is None:
        v = (PICTURE_DEFAULTS.get(section) or {}).get(key)
    if v is None:
        from . import profile as _p
        v = (_p.DEFAULT.get(section) or {}).get(key)
    return v


# ---------------------------------------------------------------- text boxes and the OCR
@dataclass
class TextBox:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str = ""
    kind: str = "text"                  # "text" (read by the OCR) | "panel" | "bar" (found from the pixels by find_ui)
    conf: float = 1.0
    block: int = None                   # the line this fragment belongs to (see group_blocks)
    est_px: float = None                # font size in 1080x1920 pixels, from the box height
    contrast: float = None              # WCAG ratio of the text colour against its background

    @property
    def w(self):
        return max(0.0, self.x1 - self.x0)

    @property
    def h(self):
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self):
        return self.w * self.h

    def box(self):
        return [round(float(self.x0), 1), round(float(self.y0), 1), round(float(self.x1), 1), round(float(self.y1), 1)]


def boxes_from_ocr(result, min_conf=0.5):
    """RapidOCR output [(box4pts, text, conf), ...] (or the (result, elapse) pair it returns, or None) -> [TextBox]. Confidence may be a string."""
    if isinstance(result, tuple) and len(result) == 2 and (result[0] is None or isinstance(result[0], list)):
        result = result[0]
    out = []
    for item in (result or []):
        try:
            pts, text, conf = item[0], str(item[1]).strip(), float(item[2])
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
        except Exception:
            continue
        if not text or conf < min_conf:
            continue
        out.append(TextBox(min(xs), min(ys), max(xs), max(ys), text, "text", conf))
    return out


_OCR_ENGINE = None


def default_ocr(min_conf=0.5):
    """callable(frame) -> [TextBox] on RapidOCR (loaded on the first call), or None when rapidocr_onnxruntime is not installed"""
    import importlib.util
    if importlib.util.find_spec("rapidocr_onnxruntime") is None:
        return None

    def ocr(frame):
        global _OCR_ENGINE
        if _OCR_ENGINE is None:
            from rapidocr_onnxruntime import RapidOCR
            _OCR_ENGINE = RapidOCR()
        res, _ = _OCR_ENGINE(np.ascontiguousarray(np.asarray(frame)[..., ::-1]))      # RapidOCR wants BGR
        return boxes_from_ocr(res, min_conf)
    return ocr


def _inter(a, b):
    w = min(a.x1, b.x1) - max(a.x0, b.x0)
    h = min(a.y1, b.y1) - max(a.y0, b.y0)
    return w * h if w > 0 and h > 0 else 0.0


def group_blocks(boxes):
    """number the text boxes by line: fragments the OCR split out of one line (same row, close together) get the same `block`, in reading order.
    Adjacent lines are NOT merged on purpose: the lines of a text that is running into another one must stay apart. Returns the boxes."""
    texts = [b for b in boxes if b.kind == "text"]
    parent = list(range(len(texts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, a in enumerate(texts):
        for j in range(i + 1, len(texts)):
            b = texts[j]
            vov = min(a.y1, b.y1) - max(a.y0, b.y0)
            if vov >= 0.4 * min(a.h, b.h) and (max(a.x0, b.x0) - min(a.x1, b.x1)) <= 2.0 * max(a.h, b.h):
                parent[find(i)] = find(j)
    roots = {}
    for i in sorted(range(len(texts)), key=lambda k: (texts[k].y0 + texts[k].y1, texts[k].x0)):
        roots.setdefault(find(i), len(roots))
    for i, b in enumerate(texts):
        b.block = roots[find(i)]
    return boxes


def _dedupe(boxes):
    """drop a box that sits on another box with the same text (an OCR that reports a line twice)"""
    out = []
    for b in boxes:
        if any(b.text == o.text and _inter(b, o) > 0.8 * min(b.area, o.area) for o in out):
            continue
        out.append(b)
    return out


# ---------------------------------------------------------------- pixels
def _rgb(frame):
    f = np.asarray(frame)
    if f.ndim != 3 or f.shape[2] < 3:
        raise ValueError("a frame must be HxWx3")
    return f[..., :3]


def small_frame(frame, width=SMALL_W):
    """block mean of a frame down to about `width` pixels wide: uint8 h x w x 3 (what every scan looks at, as ffmpeg's area scaling gives it)"""
    f = _rgb(frame)
    H, W = f.shape[:2]
    k = max(1, int(round(W / width)))
    h, w = H // k, W // k
    if k == 1:
        return np.ascontiguousarray(f[:h, :w]).astype(np.uint8)
    r = np.ascontiguousarray(f[:h * k, :w * k]).reshape(h, k, w * k * 3)
    s = r.sum(1, dtype=np.uint32).reshape(h, w, k, 3).sum(2)
    return (s / (k * k)).astype(np.uint8)


def _luma(a):
    """gamma luma 0..255 (float32) of an RGB array of any shape (..., 3)"""
    a = np.asarray(a, dtype=np.float32)
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def _linear(c):
    c = np.asarray(c, dtype=np.float32) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _rel_lum(a):
    """WCAG relative luminance 0..1 of an RGB array (..., 3)"""
    lin = _linear(a)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def contrast_ratio(l1, l2):
    hi, lo = max(l1, l2), min(l1, l2)
    return float((hi + 0.05) / (lo + 0.05))


def frame_stats(small, edge_delta=30):
    """mean and std of the luma (0..1) and the share of strong edges of a small frame: how much is drawn on it"""
    g = _luma(small)
    if g.shape[0] < 3 or g.shape[1] < 3:
        return {"mean": float(g.mean() / 255), "std": float(g.std() / 255), "edge": 0.0}
    gx = np.abs(g[:, 2:] - g[:, :-2])[1:-1]
    gy = np.abs(g[2:] - g[:-2])[:, 1:-1]
    return {"mean": float(g.mean() / 255), "std": float(g.std() / 255), "edge": float((np.maximum(gx, gy) > edge_delta).mean())}


_ASC = set("bdfhijklt0123456789()[]/|'\"!?&%$#@")
_DESC = set("gjpqy,;()[]/|@_")


def glyph_class(text):
    """what the line contains: 'x' only x-height letters, 'asc' ascenders (capitals, digits, b d f h i j k l t, accents), 'desc' descenders, 'both'"""
    t = unicodedata.normalize("NFKD", text)
    asc = any(c.isupper() or c in _ASC or unicodedata.combining(c) for c in t)
    desc = any(c in _DESC for c in t)
    return "both" if asc and desc else "asc" if asc else "desc" if desc else "x"


def est_font_px(box, ratio, scale=1.0):
    """font size (1080x1920 pixels) from the OCR box height; `ratio` is captions.em_ratio, `scale` the frame height over 1920"""
    k = ratio.get(glyph_class(box.text), 1.0)
    return float(box.h / max(scale, 1e-6) / max(k, 1e-6))


def text_contrast(frame, box):
    """WCAG contrast ratio between the text colour (the dominant colour of the pixels that differ most from the box's border) and the local background
    (the brightest background when the text is light, the darkest when it is dark: the worst case). None when the box is too small to say."""
    f = _rgb(frame)
    H, W = f.shape[:2]
    x0, y0, x1, y1 = max(0, int(box.x0)), max(0, int(box.y0)), min(W, int(math.ceil(box.x1))), min(H, int(math.ceil(box.y1)))
    if x1 - x0 < 6 or y1 - y0 < 6:
        return None
    patch = f[y0:y1, x0:x1]
    G = _luma(patch) / 255.0
    ring = np.concatenate([G[:2].ravel(), G[-2:].ravel(), G[:, :2].ravel(), G[:, -2:].ravel()])
    bg = float(np.median(ring))
    dev = np.abs(G - bg)
    dmax = float(dev.max())
    Y = _rel_lum(patch)
    if dmax < 0.02:                                                     # nothing in the box differs from its surroundings: invisible text
        y = float(np.median(Y))
        return contrast_ratio(y, y)
    core = dev >= 0.7 * dmax
    light = float(np.median(G[core])) > bg
    text_y = float(np.median(Y[core]))
    rest = dev < 0.25 * dmax
    bgY = Y[rest] if rest.any() else Y.ravel()
    bg_y = float(np.percentile(bgY, 90 if light else 10))
    return contrast_ratio(text_y, bg_y)


# ---------------------------------------------------------------- panels and bars from the pixels
def _consecutive(rows, gap=2):
    groups = []
    for r in rows:
        if groups and r - groups[-1][-1] <= gap:
            groups[-1].append(int(r))
        else:
            groups.append([int(r)])
    return groups


def _xov(a, b):
    w = min(a[1], b[1]) - max(a[0], b[0])
    return w / max(1e-6, min(a[1] - a[0], b[1] - b[0])) if w > 0 else 0.0


def _xeq(a, b, tol):
    """two (x0, x1) extents are the same one: both ends within `tol` pixels (a panel's top and bottom edge, not a full-width line next to it)"""
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def find_ui(frame, delta=18.0, min_w=0.5, thin=4, bar_max=24, panel_min=80, fill_delta=8.0, scale=None):
    """panels and bars drawn on the frame, found from long straight horizontal edges: [TextBox(kind="panel" | "bar")] sorted from the top.
    A bar is a band thicker than `thin` px and up to `bar_max` px (a progress bar); a panel is a box whose top and bottom are thin border lines (or one
    fill step each) with a different inside (a glass caption card). Sizes are 1080x1920 pixels, scaled by the frame height. Tuned to flat rendered
    graphics: see the module docstring."""
    f = _rgb(frame)
    H, W = f.shape[:2]
    s = scale if scale else H / 1920.0
    k = max(2, int(round(4 * s)))
    gap = max(2, int(round(8 * s)))
    min_len = min_w * W
    g = _luma(f)
    d = g[k:] - g[:-k]
    edges = []                                                          # (y of the edge, sign, x0, x1)
    for sign in (1, -1):
        m = (d * sign) > delta
        rows = np.flatnonzero(m.sum(1) >= 0.9 * min_len)
        for group in _consecutive(rows, 2):
            best = None
            for r in group:
                xs = np.flatnonzero(m[r])
                br = np.flatnonzero(np.diff(xs) > gap)
                st = np.r_[0, br + 1]
                en = np.r_[br, len(xs) - 1]
                ln = xs[en] - xs[st] + 1
                j = int(ln.argmax())
                if best is None or ln[j] > best[0]:
                    best = (int(ln[j]), int(xs[st[j]]), int(xs[en[j]]))
            if best and best[0] >= min_len:
                edges.append((float(np.mean(group)) + k / 2.0, sign, best[1], best[2]))
    edges.sort()
    used = [False] * len(edges)
    lines, bars = [], []                                                # (y0, y1, x0, x1)
    for i, e in enumerate(edges):
        if used[i]:
            continue
        for j in range(i + 1, len(edges)):
            o = edges[j]
            if o[0] - e[0] > bar_max * s:
                break
            if not used[j] and o[1] == -e[1] and _xov((e[2], e[3]), (o[2], o[3])) >= 0.8:
                used[i] = used[j] = True
                item = (e[0], o[0], min(e[2], o[2]), max(e[3], o[3]))
                (lines if o[0] - e[0] <= thin * s + 1 else bars).append(item)
                break
    out = []

    def inside_differs(a, b):
        ya, yb = int(a[1]) + 3, int(b[0]) - 3
        if yb - ya < 4:
            return False
        w = max(a[3], b[3]) - min(a[2], b[2])
        x0 = min(a[2], b[2])
        cols = np.r_[np.arange(int(x0 + 0.02 * w), int(x0 + 0.07 * w)), np.arange(int(x0 + w - 0.07 * w), int(x0 + w - 0.02 * w))]
        cols = cols[(cols >= 0) & (cols < W)]
        if not len(cols):
            return False
        inner = float(g[ya:yb][:, cols].mean())
        pad = max(4, int(12 * s))
        diffs = []
        for rows in (g[max(0, int(a[0]) - 4 - pad):max(0, int(a[0]) - 4)], g[min(H, int(b[1]) + 4):min(H, int(b[1]) + 4 + pad)]):
            if rows.size:
                diffs.append(abs(inner - float(rows[:, cols].mean())))
        return bool(diffs) and max(diffs) >= fill_delta
    lines.sort()
    taken = [False] * len(lines)
    for i, a in enumerate(lines):
        if taken[i]:
            continue
        for j in range(i + 1, len(lines)):
            b = lines[j]
            if b[0] - a[1] < panel_min * s or taken[j] or not _xeq((a[2], a[3]), (b[2], b[3]), 0.04 * W):
                continue
            if inside_differs(a, b):
                out.append(TextBox(min(a[2], b[2]), a[0], max(a[3], b[3]), b[1], "", "panel"))
                for q in range(i, j + 1):
                    if lines[q][0] >= a[0] and lines[q][1] <= b[1] and _xeq((lines[q][2], lines[q][3]), (a[2], a[3]), 0.06 * W):
                        taken[q] = True
                break
    rest = [edges[i] for i in range(len(edges)) if not used[i]]         # a single fill step each: a solid panel
    for i, e in enumerate(rest):
        for o in rest[i + 1:]:
            if o[1] == -e[1] and bar_max * s < o[0] - e[0] <= 0.6 * H and _xeq((e[2], e[3]), (o[2], o[3]), 0.04 * W):
                out.append(TextBox(min(e[2], o[2]), e[0], max(e[3], o[3]), o[0], "", "panel"))
                break
    out += [TextBox(b[2], b[0], b[3], b[1], "", "bar") for b in bars]
    return sorted(out, key=lambda b: (b.y0, b.x0))


# ---------------------------------------------------------------- frames from a file (the only part that starts a process)
class FrameProvider:
    """frames of an mp4, one ffmpeg call per frame (cached, last `cache` frames) and one call for a whole scan (small_range)"""

    def __init__(self, mp4, fps=30, cache=12):
        self.mp4 = str(mp4)
        self.fps = fps
        self._max = cache
        self._cache = OrderedDict()
        self._ranges = {}
        self._size = None
        self.ffmpeg = shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")

    @property
    def size(self):
        """(width, height) of the video, or None"""
        if self._size is None and self.ffprobe:
            r = subprocess.run([self.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", self.mp4],
                               capture_output=True, text=True)
            try:
                w, h = (int(x) for x in r.stdout.strip().split(",")[:2])
                self._size = (w, h)
            except Exception:
                pass
        return self._size

    def __call__(self, t):
        """the frame at t seconds as a writable uint8 RGB array, or None (past the end, ffmpeg failed)"""
        n = max(0, int(round(float(t) * self.fps)))
        if n in self._cache:
            self._cache.move_to_end(n)
            return self._cache[n]
        size = self.size
        if not self.ffmpeg or not size:
            return None
        w, h = size
        ss = max(0.0, (n - 0.25) / self.fps)                            # the first frame at or after this time is frame n
        r = subprocess.run([self.ffmpeg, "-v", "error", "-ss", f"{ss:.4f}", "-i", self.mp4, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           capture_output=True)
        if r.returncode != 0 or len(r.stdout) != w * h * 3:
            return None
        frame = np.frombuffer(r.stdout, dtype=np.uint8).reshape(h, w, 3).copy()
        self._cache[n] = frame
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)
        return frame

    def small_range(self, t0, t1, hz, width=SMALL_W):
        """(times, uint8 n x h x w x 3): the video from t0 to t1 at `hz` frames a second, scaled down by area averaging, in one ffmpeg call"""
        key = (round(float(t0), 3), round(float(t1), 3), float(hz), width)
        if key in self._ranges:
            return self._ranges[key]
        size = self.size
        if not self.ffmpeg or not size:
            return None
        w, h = size
        tw = width
        th = max(2, int(round(h * tw / w / 2.0)) * 2)
        r = subprocess.run([self.ffmpeg, "-v", "error", "-ss", f"{max(0.0, t0):.3f}", "-t", f"{max(0.01, t1 - t0):.3f}", "-i", self.mp4, "-vf",
                            f"fps={hz},scale={tw}:{th}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
        n = len(r.stdout) // (tw * th * 3)
        if r.returncode != 0 or n < 1:
            return None
        arr = np.frombuffer(r.stdout[:n * tw * th * 3], dtype=np.uint8).reshape(n, th, tw, 3)
        out = (float(t0) + np.arange(n) / float(hz), arr)
        self._ranges[key] = out
        return out


def frame_provider(mp4, fps=30, cache=12):
    """FrameProvider for an mp4 (use it as ctx.frames), or None when the file, ffmpeg or ffprobe is missing"""
    if not Path(mp4).is_file() or not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return None
    return FrameProvider(mp4, fps, cache)


# ---------------------------------------------------------------- what the detectors look at
@dataclass
class Shot:
    """one frame, analysed: the text boxes (with font size and contrast), the panels and bars"""
    t: float
    w: int
    h: int
    s: float                            # frame height over the reference height
    texts: list = field(default_factory=list)
    ui: list = field(default_factory=list)


def _cache(ctx):
    d = getattr(ctx, "__dict__", None)
    if d is None:
        return {}
    return d.setdefault("_picture_cache", {})


def _total(ctx):
    side = getattr(ctx, "side", None) or {}
    if side.get("total"):
        return float(side["total"])
    ends = [float(s["end"]) for s in side.get("scenes", []) if "end" in s]
    return (max(ends) + 0.6) if ends else 0.0


def _ref(ctx):
    return float(_cfg(ctx, "platform", "width") or 1080), float(_cfg(ctx, "platform", "height") or 1920)


def _frame_at(ctx, t):
    fr = getattr(ctx, "frames", None)
    if fr is None:
        return None
    try:
        f = fr(max(0.0, float(t)))
    except Exception:
        return None
    return None if f is None else f


def _small_at(ctx, t):
    c = _cache(ctx)
    key = ("small", round(float(t), 4))
    if key not in c:
        f = _frame_at(ctx, t)
        c[key] = None if f is None else small_frame(f)
    return c[key]


def _probe_times(ctx):
    """one time per scene (None for a scene without usable times): late enough for the slogan to have risen, before the scene ends"""
    out = []
    for sc in ctx.scenes:
        try:
            a, b = float(sc["start"]), float(sc["end"])
        except Exception:
            out.append(None)
            continue
        t = max(a + 0.6, (a + b) / 2) if b - a > 0.7 else (a + b) / 2
        out.append(min(t, max(a, b - 0.05)))
    return out


def _speech_spans(ctx):
    spans = []
    for sc in ctx.scenes:
        w = sc.get("words") or []
        try:
            if w:
                spans.append((float(w[0][1]), float(w[-1][2])))
            else:
                spans.append((float(sc["start"]), float(sc["end"])))
        except Exception:
            continue
    return spans


def _speech_mask(ctx, times):
    spans = _speech_spans(ctx)
    if not spans:
        return np.ones(len(times), dtype=bool)                          # no speech information: assume it is speaking all the time
    m = np.zeros(len(times), dtype=bool)
    for a, b in spans:
        m |= (times >= a) & (times <= b)
    return m


def _shot(ctx, t, with_ocr=True):
    """Shot at time t (cached), or None when there is no frame. OCR boxes only when ctx.ocr is set."""
    c = _cache(ctx)
    key = ("shot", round(float(t), 4), bool(with_ocr))
    if key in c:
        return c[key]
    frame = _frame_at(ctx, t)
    if frame is None:
        c[key] = None
        return None
    H, W = _rgb(frame).shape[:2]
    ref_w, ref_h = _ref(ctx)
    s = H / ref_h
    texts = []
    ocr = getattr(ctx, "ocr", None)
    if with_ocr and ocr is not None:
        try:
            res = ocr(frame)
        except Exception:
            res = []
        if res and not isinstance(res[0], TextBox):
            res = boxes_from_ocr(res, _cfg(ctx, "picture", "ocr_min_conf"))
        texts = [replace(b) for b in (res or []) if getattr(b, "kind", "text") == "text"]
        group_blocks(texts)
        ratio = _cfg(ctx, "captions", "em_ratio")
        for b in texts:
            b.est_px = est_font_px(b, ratio, s)
            b.contrast = text_contrast(frame, b)
    ui = []
    if _cfg(ctx, "picture", "ui_detect"):
        ui = find_ui(frame, _cfg(ctx, "picture", "ui_delta"), _cfg(ctx, "picture", "ui_min_w"), _cfg(ctx, "picture", "ui_thin_px"),
                     _cfg(ctx, "picture", "ui_bar_max_px"), _cfg(ctx, "picture", "ui_panel_min_px"), _cfg(ctx, "picture", "ui_fill_delta"), s)
    shot = Shot(float(t), W, H, s, texts, ui)
    c[key] = shot
    return shot


def _scene_shots(ctx):
    """[(scene number, scene, Shot)] for the scenes whose probe frame exists"""
    out = []
    for i, (sc, t) in enumerate(zip(ctx.scenes, _probe_times(ctx)), 1):
        if t is None:
            continue
        shot = _shot(ctx, t)
        if shot is not None:
            out.append((i, sc, shot))
    return out


def _series(ctx, hz):
    """(times, uint8 n x h x w x 3 thumbnails) of the whole reel at `hz`: one ffmpeg call with a provider that has small_range, else frame by frame"""
    c = _cache(ctx)
    key = ("series", float(hz))
    if key in c:
        return c[key]
    total = _total(ctx)
    fr = getattr(ctx, "frames", None)
    res = None
    rng = getattr(fr, "small_range", None)
    if callable(rng) and total > 0:
        try:
            res = rng(0.0, total, hz)
        except Exception:
            res = None
    if res is None and fr is not None and total > 0:
        times, frames = [], []
        for k in range(int(total * hz + 1e-6)):
            t = k / float(hz)
            sm = _small_at(ctx, t)
            if sm is None:
                continue
            times.append(t)
            frames.append(sm)
        if frames:
            shape = frames[0].shape
            ok = [i for i, a in enumerate(frames) if a.shape == shape]
            res = (np.array([times[i] for i in ok]), np.stack([frames[i] for i in ok]))
    c[key] = res
    return res


def _runs(mask):
    """[(first, last)] index pairs of the consecutive True values"""
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(mask) - 1))
    return out


def _is_black(arr, level, share):
    g = _luma(arr) / 255.0
    return (g < level).mean(axis=(1, 2)) >= share


def _speech_overlap(a, b, spans):
    return float(sum(max(0.0, min(b, y) - max(a, x)) for x, y in spans))


# ---------------------------------------------------------------- the detectors
def check_available(ctx):
    """INFO lines about what could not be checked: no frames at all, or frames but no OCR (the text checks)"""
    if getattr(ctx, "frames", None) is None:
        return [finding(PICTURE_OFF, "the picture is not checked: no frames of the video are available (frame_provider needs the mp4 and ffmpeg)")]
    if getattr(ctx, "ocr", None) is None:
        return [finding(OCR_OFF, "text positions are not checked: no OCR is available (picture.default_ocr() needs rapidocr_onnxruntime); "
                                 "black, freeze, flicker, panels, bars and caption speed are still checked")]
    return []


def check_first_frame(ctx):
    """FIRST_FRAME: a black, flat or empty first frame. Black and flat are ERRORs; an empty frame (nothing drawn: no text, no picture) is an ERROR when
    nothing appears within picture.hook_s and a WARN when it fills up sooner (the feed preview and the cover show the first frame).
    Uses ctx.frames at t=0 and then every 0.1 s until something is drawn; no sidecar field."""
    first = _small_at(ctx, 0.0)
    if first is None:
        return []
    ed = _cfg(ctx, "picture", "content_edge_delta")
    st = frame_stats(first, ed)
    dark, flat, min_edge, hook = (_cfg(ctx, "picture", "first_frame_dark"), _cfg(ctx, "picture", "first_frame_flat_std"),
                                  _cfg(ctx, "picture", "first_frame_min_edge"), _cfg(ctx, "picture", "hook_s"))
    data = {"mean": round(st["mean"], 4), "std": round(st["std"], 4), "edge": round(st["edge"], 4)}
    if st["mean"] < dark:
        return [finding(FIRST_FRAME, f"dead hook: the first frame is black (brightness {st['mean'] * 100:.1f}%), the reel starts on nothing", t=0.0, kind="black", **data)]
    if st["std"] < flat:
        return [finding(FIRST_FRAME, f"dead hook: the first frame is one flat colour (variation {st['std'] * 100:.1f}%)", t=0.0, kind="flat", **data)]
    if st["edge"] >= min_edge:
        return []
    tc, t = None, 0.1
    while t <= hook + 0.2 + 1e-9:
        sm = _small_at(ctx, t)
        if sm is None:
            break
        if frame_stats(sm, ed)["edge"] >= min_edge:
            tc = round(t, 2)
            break
        t += 0.1
    if tc is None or tc > hook:
        return [finding(FIRST_FRAME, f"dead hook: nothing is drawn on screen for the first {hook + 0.2:.1f} s (no text, no picture; edge share {st['edge']:.3f}, "
                                     f"a real first frame has 0.06 or more)", t=0.0, kind="empty", content_at=tc, **data)]
    return [finding(FIRST_FRAME, f"the first frame is empty (edge share {st['edge']:.3f}, a real first frame has 0.06 or more): content appears at {tc:.1f} s; "
                                 f"the feed preview and the cover show nothing", sev="WARN", t=0.0, kind="empty", content_at=tc, **data)]


def check_black(ctx):
    """PICTURE_BLACK: a run of black frames longer than picture.black_s (a pixel is black under picture.black_level, a frame when picture.black_share of
    its pixels are). ERROR when speech runs under it for 0.25 s or more, WARN otherwise. Uses the sidecar words for the speech spans."""
    hz = float(_cfg(ctx, "picture", "probe_hz"))
    ser = _series(ctx, hz)
    if ser is None:
        return []
    times, arr = ser
    black = _is_black(arr, _cfg(ctx, "picture", "black_level"), _cfg(ctx, "picture", "black_share"))
    spans, out = _speech_spans(ctx), []
    for a, b in _runs(black):
        t0, t1 = float(times[a]), float(times[b]) + 1.0 / hz
        if t1 - t0 > _cfg(ctx, "picture", "black_s"):
            ov = _speech_overlap(t0, t1, spans)
            out.append(finding(PICTURE_BLACK, f"black picture from {t0:.2f} s to {t1:.2f} s ({t1 - t0:.2f} s, limit {_cfg(ctx, 'picture', 'black_s')} s)"
                                              + (f" while the voice speaks ({ov:.2f} s of it)" if ov > 0 else ""),
                               sev="ERROR" if ov >= 0.25 else "WARN", t=t0, start=t0, end=t1, duration=round(t1 - t0, 3), speech_s=round(ov, 3)))
    return out


def check_freeze(ctx):
    """PICTURE_FREEZE: identical frames (mean luma difference of samples 0.1 s apart <= picture.freeze_mad) for longer than picture.freeze_s, with speech
    running during at least half of that stretch (a frozen end card after the last word is not reported). Black frames are PICTURE_BLACK's, not counted
    here. Uses the sidecar words for the speech spans."""
    hz = float(_cfg(ctx, "picture", "probe_hz"))
    ser = _series(ctx, hz)
    if ser is None or len(ser[0]) < 3:
        return []
    times, arr = ser
    g = _luma(arr)
    mad = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    black = _is_black(arr, _cfg(ctx, "picture", "black_level"), _cfg(ctx, "picture", "black_share"))
    frozen = (mad <= _cfg(ctx, "picture", "freeze_mad")) & ~black[1:]
    speech = _speech_mask(ctx, times)[1:]
    out = []
    for a, b in _runs(frozen):
        t0, t1 = float(times[a]), float(times[b + 1])
        share = float(speech[a:b + 1].mean())
        if t1 - t0 > _cfg(ctx, "picture", "freeze_s") and share >= 0.5:
            out.append(finding(PICTURE_FREEZE, f"the picture is frozen from {t0:.1f} s to {t1:.1f} s ({t1 - t0:.1f} s of identical frames, the voice speaks during "
                                               f"{share * 100:.0f}% of it; limit {_cfg(ctx, 'picture', 'freeze_s')} s)", t=t0, start=t0, end=t1, duration=round(t1 - t0, 3),
                               speech_share=round(share, 3), mad_max=round(float(mad[a:b + 1].max()), 4)))
    return out


def count_flashes(lum, hz, delta=0.10, dark_max=0.80, window_s=1.0):
    """(most flashes in any window_s, number of changes, time of that window) for a series of relative luminance (0..1). A change is a rise or a fall
    of at least `delta` from the last extreme, taken when the darker side is under `dark_max`; a flash is a pair of opposing changes (WCAG 2.3.1)."""
    n = len(lum)
    if n < 2:
        return 0, 0, 0.0
    state, lo, hi = None, float(lum[0]), float(lum[0])
    trans = []
    for i in range(1, n):
        v = float(lum[i])
        if state is None:
            lo, hi = min(lo, v), max(hi, v)
            if v - lo >= delta:
                state, hi = "up", v
                trans.append((i, lo))
            elif hi - v >= delta:
                state, lo = "down", v
                trans.append((i, v))
        elif state == "up":
            if v > hi:
                hi = v
            elif hi - v >= delta:
                state, lo = "down", v
                trans.append((i, v))
        else:
            if v < lo:
                lo = v
            elif v - lo >= delta:
                state, hi = "up", v
                trans.append((i, lo))
    tt = np.array([i / float(hz) for i, dark in trans if dark < dark_max])
    best, at = 0, 0.0
    for t in tt:
        k = int(((tt >= t) & (tt < t + window_s)).sum()) // 2
        if k > best:
            best, at = k, float(t)
    return best, len(tt), at


def check_flicker(ctx):
    """FLICKER: more than picture.flash_per_s flashes in any second (WCAG general flash on the frame mean and on each quadrant, the 25% area rule).
    Scans every frame when the provider has small_range."""
    hz = float(min(_cfg(ctx, "picture", "flicker_hz"), getattr(ctx, "fps", 30) or 30))
    ser = _series(ctx, hz)
    if ser is None or len(ser[0]) < 4:
        return []
    times, arr = ser
    L = _rel_lum(arr)                                                    # n x h x w
    h2, w2 = L.shape[1] // 2, L.shape[2] // 2
    regions = {"frame": L.mean(axis=(1, 2)), "top-left": L[:, :h2, :w2].mean(axis=(1, 2)), "top-right": L[:, :h2, w2:].mean(axis=(1, 2)),
               "bottom-left": L[:, h2:, :w2].mean(axis=(1, 2)), "bottom-right": L[:, h2:, w2:].mean(axis=(1, 2))}
    worst = None
    for name, series in regions.items():
        k, n, at = count_flashes(series, hz, _cfg(ctx, "picture", "flash_delta"), _cfg(ctx, "picture", "flash_dark_max"), _cfg(ctx, "picture", "flash_window_s"))
        if worst is None or k > worst[0]:
            worst = (k, n, float(times[0]) + at, name)
    limit = _cfg(ctx, "picture", "flash_per_s")
    if worst and worst[0] > limit:
        return [finding(FLICKER, f"the picture flashes {worst[0]} times in one second at {worst[2]:.1f} s ({worst[3]}; limit {limit} a second, WCAG general flash: "
                                 f"changes of {_cfg(ctx, 'picture', 'flash_delta') * 100:.0f}% of the maximum luminance)", t=worst[2], flashes=worst[0], changes=worst[1],
                        region=worst[3])]
    return []


def _safe_rect(ctx, shot):
    """the safe rectangle in pixels of this shot's frame"""
    top, bottom, side = _cfg(ctx, "safe_zone", "top"), _cfg(ctx, "safe_zone", "bottom"), _cfg(ctx, "safe_zone", "side")
    ref_w, ref_h = _ref(ctx)
    sx, sy = shot.w / ref_w, shot.h / ref_h
    return side * sx, top * sy, shot.w - side * sx, shot.h - bottom * sy


def _overflow(ctx, shot, b):
    """(px past the safe rectangle in 1080x1920 pixels, name of the side) for a box; (0, None) when it is inside"""
    zx0, zy0, zx1, zy1 = _safe_rect(ctx, shot)
    ref_w, ref_h = _ref(ctx)
    sx, sy = shot.w / ref_w, shot.h / ref_h
    over = {"top": (zy0 - b.y0) / sy, "bottom": (b.y1 - zy1) / sy, "left": (zx0 - b.x0) / sx, "right": (b.x1 - zx1) / sx}
    side = max(over, key=over.get)
    return (max(0.0, over[side]), side if over[side] > 0 else None)


def _uncertain(ctx):
    if _cfg(ctx, "safe_zone", "uncertain"):
        return " (uncertain: sources disagree on the safe area, top 200-270, bottom 350-672, sides 65-130 px)"
    return ""


def _label(b):
    return b.kind if b.kind != "text" else f"text '{b.text[:30]}'"


def _persistent_ui(ctx, shots):
    """panels and bars that stand at the same place in several frames: [{kind, box (x0,y0,x1,y1 in frame px of the largest frame), frames, y1s, s}]"""
    tol = _cfg(ctx, "picture", "ui_tol_px")
    clusters = []
    for fi, shot in enumerate(shots):
        for e in shot.ui:
            ex = (e.x0 / shot.s, e.y0 / shot.s, e.x1 / shot.s, e.y1 / shot.s)           # 1080x1920 pixels
            for c in clusters:
                if c["kind"] == e.kind and abs(c["box"][1] - ex[1]) <= tol and (e.kind == "panel" or abs(c["box"][3] - ex[3]) <= tol) \
                        and _xov((c["box"][0], c["box"][2]), (ex[0], ex[2])) >= 0.8:
                    c["frames"].add(fi)
                    c["y1s"].append(ex[3])
                    c["box"] = (min(c["box"][0], ex[0]), c["box"][1], max(c["box"][2], ex[2]), max(c["box"][3], ex[3]))
                    break
            else:
                clusters.append({"kind": e.kind, "box": ex, "frames": {fi}, "y1s": [ex[3]]})
    need = 1 if len(shots) < 2 else max(int(_cfg(ctx, "picture", "ui_persist")), int(math.ceil(0.3 * len(shots))))
    return [c for c in clusters if len(c["frames"]) >= need]


def _ui_shots(ctx):
    """the scene probe frames plus two frames at 20% and 80% of the reel (panels and bars only, no OCR), so that a moving decoration is told from furniture"""
    shots = [sh for _i, _sc, sh in _scene_shots(ctx)]
    total = _total(ctx)
    for frac in (0.2, 0.8):
        if total > 0:
            sh = _shot(ctx, total * frac, with_ocr=False)
            if sh is not None:
                shots.append(sh)
    return shots


def check_safe_zone(ctx):
    """SAFE_ZONE: text boxes (OCR), panels and bars outside the safe rectangle (profile safe_zone.top/bottom/side, in 1080x1920 pixels; safe_zone.uncertain
    adds the warning that sources disagree). A panel or bar counts when it stands at the same place in at least picture.ui_persist frames (a moving
    scan line is not furniture). Text needs ctx.ocr; panels and bars need only frames."""
    tol = _cfg(ctx, "picture", "safe_tol_px")
    out = []
    for i, sc, shot in _scene_shots(ctx):
        bad = []
        for b in shot.texts:
            ov, side = _overflow(ctx, shot, b)
            if ov > tol:
                bad.append((ov, side, b))
        if bad:
            bad.sort(key=lambda r: -r[0])
            ov, side, b = bad[0]
            what = "; ".join(f"'{x.text[:30]}' {o:.0f} px past the {sd}" for o, sd, x in bad[:3])
            out.append(finding(SAFE_ZONE, f"scene {i}: text outside the safe area: {what}" + _uncertain(ctx), scene=i, t=shot.t, overflow_px=round(ov, 1), side=side,
                               boxes=[[round(x.x0 / shot.s), round(x.y0 / shot.s), round(x.x1 / shot.s), round(x.y1 / shot.s), x.text] for _o, _s, x in bad[:5]]))
    shots = _ui_shots(ctx)
    if shots:
        ref_w, ref_h = _ref(ctx)
        top, bottom, side_px = _cfg(ctx, "safe_zone", "top"), _cfg(ctx, "safe_zone", "bottom"), _cfg(ctx, "safe_zone", "side")
        limits = {"top": top, "bottom": ref_h - bottom, "left": side_px, "right": ref_w - side_px}
        for c in _persistent_ui(ctx, shots):
            x0, y0, x1, y1 = c["box"]
            over = {"top": top - y0, "bottom": y1 - (ref_h - bottom), "left": side_px - x0, "right": x1 - (ref_w - side_px)}
            worst = max(over, key=over.get)
            if over[worst] > tol:
                # a thin bar that is only too high (inside the side margins, clear of the bottom) is decoration in the free margin, where the platform's own top bar may cover it
                # without a loss (our progress bar stands there on purpose): information, not a warning
                decoration = c["kind"] == "bar" and y0 >= 0 and all(over[k] <= tol for k in ("bottom", "left", "right"))
                res = ", ".join(f"{over[k]:.0f} px past the {k} limit (y {limits[k]:.0f})" if k in ("top", "bottom") else f"{over[k]:.0f} px past the {k} margin"
                                for k in over if over[k] > tol)
                out.append(finding(SAFE_ZONE, f"a {c['kind']} at y {y0:.0f}-{y1:.0f}, x {x0:.0f}-{x1:.0f} (seen in {len(c['frames'])} frames) is {res}"
                                   + (": decoration in the top margin, the platform may cover it" if decoration else _uncertain(ctx)), sev="INFO" if decoration else None,
                                   kind=c["kind"], box=[round(x0), round(y0), round(x1), round(y1)], overflow_px=round(over[worst], 1), side=worst, frames=len(c["frames"]),
                                   decoration=decoration))
    return out


def check_text_overlap(ctx):
    """TEXT_OVERLAP: two text boxes of different lines whose overlap is more than picture.overlap_share of the smaller box (more than
    picture.overlap_same_line for fragments of one line), or a text box that is between picture.straddle_min and picture.straddle_max inside a panel (it runs
    over the panel's edge: the slogan into the caption card). Needs ctx.ocr; the panels come from the pixels."""
    share, same, smin, smax = (_cfg(ctx, "picture", k) for k in ("overlap_share", "overlap_same_line", "straddle_min", "straddle_max"))
    out = []
    for i, sc, shot in _scene_shots(ctx):
        boxes = _dedupe(shot.texts)
        hits = []
        for a_i, a in enumerate(boxes):
            for b in boxes[a_i + 1:]:
                inter = _inter(a, b)
                if inter <= 0:
                    continue
                sh = inter / min(a.area, b.area)
                if sh > (same if a.block == b.block else share):
                    hits.append((sh, f"'{a.text[:28]}' overlaps '{b.text[:28]}' by {sh * 100:.0f}% of the smaller box", "text"))
        for p in (e for e in shot.ui if e.kind == "panel"):
            for a in boxes:
                inside = _inter(a, p) / a.area if a.area else 0.0
                if smin < inside < smax:
                    hits.append((inside, f"'{a.text[:28]}' runs over the edge of the panel at y {p.y0 / shot.s:.0f}-{p.y1 / shot.s:.0f} ({inside * 100:.0f}% of it is inside)", "panel"))
        if hits:
            hits.sort(key=lambda r: -r[0])
            out.append(finding(TEXT_OVERLAP, f"scene {i}: " + "; ".join(h[1] for h in hits[:3]), scene=i, t=shot.t, share=round(hits[0][0], 3), n=len(hits), what=hits[0][2]))
    return out


def _fold(s):
    s = unicodedata.normalize("NFKD", s.replace("ł", "l").replace("Ł", "L"))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _words(s):
    return [w for w in re.findall(r"[a-z0-9]+", _fold(s)) if len(w) >= 3 or (w.isdigit() and len(w) >= 2)]


def _word_in(w, vocab):
    return any(w == v or (len(w) >= 4 and len(v) >= 4 and difflib.SequenceMatcher(None, w, v).ratio() >= 0.8) for v in vocab)


def _owner(text, va, vb):
    """0 when the OCR text is the first scene's, 1 when it is the second's, None when it is neither or cannot be told"""
    ws = _words(text)
    if not ws:
        return None
    sa = sum(1 for w in ws if _word_in(w, va)) / len(ws)
    sb = sum(1 for w in ws if _word_in(w, vb)) / len(ws)
    if sa >= 0.5 and sa > sb:
        return 0
    if sb >= 0.5 and sb > sa:
        return 1
    return None


def check_ghost(ctx):
    """TRANSITION_GHOST: at the frames just before and after the start of a scene (picture.ghost_frames, in frames) the OCR reads text of the previous AND of
    the next scene in one frame. Words are matched to the two scenes' text and slogan (accents folded, because OCR drops them). Needs ctx.ocr and the scenes'
    start/end/text/screen. A ghost the OCR cannot read (blurred, nearly transparent) is not found."""
    if getattr(ctx, "ocr", None) is None or getattr(ctx, "frames", None) is None:
        return []
    scenes = ctx.scenes
    fps = float(getattr(ctx, "fps", 30) or 30)
    offs = _cfg(ctx, "picture", "ghost_frames")
    out = []
    for i in range(len(scenes) - 1):
        a, b = scenes[i], scenes[i + 1]
        try:
            tb = float(b["start"])
        except Exception:
            continue
        va = set(_words((a.get("text") or "") + " " + (a.get("screen") or "")))
        vb = set(_words((b.get("text") or "") + " " + (b.get("screen") or "")))
        common = va & vb
        va, vb = va - common, vb - common
        if not va or not vb:
            continue
        for off in offs:
            t = tb + off / fps
            if t < 0:
                continue
            shot = _shot(ctx, t, with_ocr=True)
            if shot is None:
                continue
            owners = {_owner(x.text, va, vb) for x in shot.texts} - {None}
            if owners == {0, 1}:
                out.append(finding(TRANSITION_GHOST, f"scenes {i + 1}->{i + 2}: at {t:.2f} s ({off:+d} frames from the start of scene {i + 2}) the text of both scenes is on screen at once",
                                   scene=i + 2, t=t, frames_from_start=int(off)))
                break
    return out


def check_legibility(ctx):
    """TEXT_LEGIBILITY: a line of text whose estimated font size (OCR box height / the measured box-to-font ratio of what the line contains, scaled to
    1080x1920) is under captions.min_px. Needs ctx.ocr. The estimate is lenient (+-20%); see the module docstring."""
    mn = _cfg(ctx, "captions", "min_px")
    out = []
    for i, sc, shot in _scene_shots(ctx):
        rows = {}
        for b in shot.texts:
            rows.setdefault(b.block, []).append(b)
        small = []
        for _blk, bs in rows.items():
            est = max(b.est_px for b in bs)
            if est < mn:
                small.append((est, " ".join(b.text for b in sorted(bs, key=lambda q: q.x0))))
        if small:
            small.sort()
            out.append(finding(TEXT_LEGIBILITY, f"scene {i}: text about {small[0][0]:.0f} px tall ('{small[0][1][:40]}'), under the {mn} px that stays readable on a phone"
                                                + (f" ({len(small)} lines)" if len(small) > 1 else ""), scene=i, t=shot.t, est_px=round(small[0][0], 1), lines=len(small)))
    return out


def check_contrast(ctx):
    """CONTRAST: the WCAG contrast ratio of a text line's dominant colour against the brightest (light text) or darkest (dark text) background pixels around
    it is under captions.min_contrast. Needs ctx.ocr and the pixels of the frame."""
    mn = _cfg(ctx, "captions", "min_contrast")
    out = []
    for i, sc, shot in _scene_shots(ctx):
        low = [b for b in shot.texts if b.contrast is not None and b.contrast < mn]
        if low:
            low.sort(key=lambda b: b.contrast)
            w = low[0]
            out.append(finding(CONTRAST, f"scene {i}: contrast {w.contrast:.1f}:1 for '{w.text[:40]}', under the {mn}:1 of WCAG for readable text"
                                         + (f" ({len(low)} lines)" if len(low) > 1 else ""), scene=i, t=shot.t, ratio=round(w.contrast, 2), lines=len(low)))
    return out


def caption_speeds(ctx):
    """[{scene, chars, shown_s, cps}] per scene: the characters of the sentence over the time its caption card is on screen (start to end plus captions.hold_s)"""
    hold = float(_cfg(ctx, "captions", "hold_s"))
    out = []
    for i, sc in enumerate(ctx.scenes, 1):
        text = (sc.get("text") or " ".join(str(w[0]) for w in (sc.get("words") or []))).strip()
        try:
            shown = float(sc["end"]) - float(sc["start"]) + hold
        except Exception:
            continue
        if text:
            out.append({"scene": i, "chars": len(text), "shown_s": round(shown, 3), "cps": round(len(text) / max(shown, 1e-3), 2)})
    return out


def check_caption_speed(ctx):
    """CAPTION_SPEED: characters a second of a scene's caption block above captions.max_cps, or the block shown shorter than captions.min_show_s. Needs only
    the sidecar scenes (text or words, start, end); no frames."""
    mx, mn = _cfg(ctx, "captions", "max_cps"), _cfg(ctx, "captions", "min_show_s")
    out = []
    for r in caption_speeds(ctx):
        if r["cps"] > mx:
            out.append(finding(CAPTION_SPEED, f"scene {r['scene']}: {r['chars']} characters in {r['shown_s']:.1f} s is {r['cps']:.0f} characters a second, over the {mx} "
                                              f"that can be read", scene=r["scene"], cps=r["cps"], chars=r["chars"], shown_s=r["shown_s"], what="fast"))
        if r["shown_s"] < mn:
            out.append(finding(CAPTION_SPEED, f"scene {r['scene']}: the caption is on screen for {r['shown_s']:.2f} s, under the {mn} s needed to notice it", scene=r["scene"],
                               shown_s=r["shown_s"], what="short"))
    return out


DETECTORS = [check_available, check_first_frame, check_black, check_freeze, check_flicker, check_safe_zone, check_text_overlap, check_ghost, check_legibility,
             check_contrast, check_caption_speed]


def run_all(ctx):
    """every detector, in order; one that crashes becomes a PICTURE_ERROR finding and the others still run"""
    out = []
    for det in DETECTORS:
        try:
            out += det(ctx)
        except Exception as e:                                           # a bug in a detector must not hide the findings of the others
            out.append(finding(PICTURE_ERROR, f"{det.__name__} crashed: {type(e).__name__}: {e}"))
    return out


# ---------------------------------------------------------------- the summary of the layout
def timeline_stats(ctx):
    """numbers behind the timeline detectors: {min_mad, p1_mad, black_longest_s, flashes_per_s, frozen_longest_s}, or None without frames"""
    hz = float(_cfg(ctx, "picture", "probe_hz"))
    ser = _series(ctx, hz)
    if ser is None or len(ser[0]) < 3:
        return None
    times, arr = ser
    g = _luma(arr)
    mad = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    black = _is_black(arr, _cfg(ctx, "picture", "black_level"), _cfg(ctx, "picture", "black_share"))
    bl = max([(b - a + 1) / hz for a, b in _runs(black)] or [0.0])
    fz = max([(b - a + 1) / hz for a, b in _runs(mad <= _cfg(ctx, "picture", "freeze_mad"))] or [0.0])
    fhz = float(min(_cfg(ctx, "picture", "flicker_hz"), getattr(ctx, "fps", 30) or 30))
    fser = _series(ctx, fhz)
    flashes = 0
    if fser is not None and len(fser[0]) >= 4:
        flashes = count_flashes(_rel_lum(fser[1]).mean(axis=(1, 2)), fhz, _cfg(ctx, "picture", "flash_delta"), _cfg(ctx, "picture", "flash_dark_max"),
                                _cfg(ctx, "picture", "flash_window_s"))[0]
    return {"min_mad": round(float(mad.min()), 4), "p1_mad": round(float(np.percentile(mad, 1)), 4), "black_longest_s": round(bl, 2), "frozen_longest_s": round(fz, 2),
            "flashes_per_s": int(flashes), "duration_s": round(float(times[-1] - times[0] + 1 / hz), 2)}


def layout_report(ctx):
    """where everything is, in 1080x1920 pixels, against the safe rectangle: per scene the text lines (box, font size, contrast, overflow), the panels and
    bars, and the furniture that persists over the reel. Plain dicts and lists (JSON-able). Needs ctx.frames; text needs ctx.ocr."""
    ref_w, ref_h = _ref(ctx)
    top, bottom, side = _cfg(ctx, "safe_zone", "top"), _cfg(ctx, "safe_zone", "bottom"), _cfg(ctx, "safe_zone", "side")
    rep = {"safe": {"x0": side, "y0": top, "x1": ref_w - side, "y1": ref_h - bottom, "uncertain": bool(_cfg(ctx, "safe_zone", "uncertain"))},
           "scenes": [], "furniture": [], "captions": caption_speeds(ctx), "first_frame": None, "ocr": getattr(ctx, "ocr", None) is not None}
    first = _small_at(ctx, 0.0)
    if first is not None:
        rep["first_frame"] = {k: round(v, 4) for k, v in frame_stats(first, _cfg(ctx, "picture", "content_edge_delta")).items()}
    tol = _cfg(ctx, "picture", "safe_tol_px")
    for i, sc, shot in _scene_shots(ctx):
        lines = []
        rows = {}
        for b in shot.texts:
            rows.setdefault(b.block, []).append(b)
        for blk, bs in sorted(rows.items()):
            x0, y0, x1, y1 = min(b.x0 for b in bs), min(b.y0 for b in bs), max(b.x1 for b in bs), max(b.y1 for b in bs)
            tb = TextBox(x0, y0, x1, y1, " ".join(b.text for b in sorted(bs, key=lambda q: q.x0)))
            ov, sd = _overflow(ctx, shot, tb)
            cons = [b.contrast for b in bs if b.contrast is not None]
            lines.append({"text": tb.text, "box": [round(x0 / shot.s), round(y0 / shot.s), round(x1 / shot.s), round(y1 / shot.s)], "est_px": round(max(b.est_px for b in bs)),
                          "contrast": round(min(cons), 1) if cons else None, "overflow_px": round(ov, 1) if ov > tol else 0, "side": sd if ov > tol else None})
        ui = []
        for e in shot.ui:
            ov, sd = _overflow(ctx, shot, e)
            ui.append({"kind": e.kind, "box": [round(e.x0 / shot.s), round(e.y0 / shot.s), round(e.x1 / shot.s), round(e.y1 / shot.s)], "overflow_px": round(ov, 1) if ov > tol else 0,
                       "side": sd if ov > tol else None})
        rep["scenes"].append({"scene": i, "t": round(shot.t, 2), "lines": lines, "ui": ui})
    shots = _ui_shots(ctx)
    for c in _persistent_ui(ctx, shots) if shots else []:
        x0, y0, x1, y1 = c["box"]
        over = {"top": top - y0, "bottom": y1 - (ref_h - bottom), "left": side - x0, "right": x1 - (ref_w - side)}
        rep["furniture"].append({"kind": c["kind"], "box": [round(x0), round(y0), round(x1), round(y1)], "frames": len(c["frames"]), "of": len(shots),
                                 "bottoms": sorted({round(v) for v in c["y1s"]}), "overflow_px": {k: round(v, 1) for k, v in over.items() if v > tol}})
    return rep


def format_layout(rep):
    """the layout report as a few readable lines"""
    s = rep["safe"]
    out = [f"safe area x {s['x0']}-{s['x1']}, y {s['y0']}-{s['y1']}" + (" (uncertain: sources disagree)" if s["uncertain"] else "")]
    if rep.get("first_frame"):
        f = rep["first_frame"]
        out.append(f"first frame: brightness {f['mean']:.3f}, variation {f['std']:.3f}, edge share {f['edge']:.4f}")
    for sc in rep["scenes"]:
        out.append(f"scene {sc['scene']} (t={sc['t']}s):")
        for ln in sc["lines"]:
            out.append(f"  text {ln['box']} ~{ln['est_px']}px contrast {ln['contrast']} '{ln['text'][:40]}'" + (f"  OUTSIDE: {ln['overflow_px']} px past {ln['side']}" if ln["side"] else ""))
        for e in sc["ui"]:
            out.append(f"  {e['kind']} {e['box']}" + (f"  OUTSIDE: {e['overflow_px']} px past {e['side']}" if e["side"] else ""))
    for e in rep["furniture"]:
        out.append(f"furniture: {e['kind']} {e['box']} in {e['frames']}/{e['of']} frames, bottoms {e['bottoms']}, outside by {e['overflow_px']}")
    for c in rep["captions"]:
        out.append(f"caption {c['scene']}: {c['chars']} chars in {c['shown_s']} s = {c['cps']} cps")
    return "\n".join(out)


# ---------------------------------------------------------------- fault injection: a drawn reel with a fake OCR (needs Pillow)
class DrawnFrame(np.ndarray):
    """a frame that remembers the text boxes drawn on it, so the fake OCR can answer without reading pixels"""

    def __array_finalize__(self, obj):
        self.boxes = getattr(obj, "boxes", ())


NAVY, PAPER, SIGNAL, MUTE, LINE = (10, 23, 51), (242, 238, 227), (255, 194, 75), (159, 176, 214), (143, 180, 255)
_FONTS = {}

LAYOUTS = {
    # everything inside the safe rectangle (x 90-990, y 270-1480)
    "ok": {"slogan_top": 400, "slogan_font": 118, "card_top": 880, "card_x": (100, 980), "caption_font": 68, "bar_y": 1420, "card_pad": 46},
    # what the renderer draws today (studio/src/Reel.tsx): slogan about 775-1050, card from 1128, progress bar at 1690, card 940 px wide
    "current": {"slogan_top": 775, "slogan_font": 118, "card_top": 1128, "card_x": (70, 1010), "caption_font": 68, "bar_y": 1690, "card_pad": 46},
}

_SENTENCES = [("Thirty reels, none watched.", "30 reels, zero views"), ("The program listens, then writes it.", "Speech to text"),
              ("It reads the screen as well.", "Reads the screen"), ("Comment TEST and get the map.", "")]


def _font(size):
    from PIL import ImageFont
    size = max(8, int(size))
    if size not in _FONTS:
        for name in ("bahnschrift.ttf", "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"):
            try:
                _FONTS[size] = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        else:
            try:
                _FONTS[size] = ImageFont.load_default(size)
            except TypeError:
                _FONTS[size] = ImageFont.load_default()
    return _FONTS[size]


def _background(w, h):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    glow = np.exp(-(((xx - w * 0.8) / (w * 0.65)) ** 2 + ((yy - h * 0.12) / (h * 0.34)) ** 2))
    base = np.zeros((h, w, 3), dtype=np.float32)
    for k in range(3):
        base[:, :, k] = NAVY[k] + glow * (np.array([23, 58, 122])[k] - NAVY[k])
    return base.astype(np.uint8)


class FakeOCR:
    """the OCR of a drawn reel: it returns the boxes that were drawn on the frame it is given (frames from SyntheticReel carry them), accents dropped like RapidOCR does"""

    def __call__(self, frame):
        return [replace(b, text=_fold(b.text)) for b in getattr(frame, "boxes", ())]


class SyntheticReel:
    """a drawn reel: frames(t) are 1080x1920 (times `scale`) uint8 RGB made with Pillow: navy background, a slogan, a glass caption card with the sentence, a
    progress bar and a moving scan line; side is the matching sidecar; ocr is a FakeOCR. `fault` puts one fault in (see inject / INJECT_KINDS)."""

    def __init__(self, scale=1.0, layout="ok", fault=None, sentences=None, scene_s=1.6, gap_s=0.3, tail_s=0.3, lead_s=0.0, hold_s=0.28, fps=30, **over):
        self.scale, self.fault, self.fps, self.hold, self.lead = scale, fault, fps, hold_s, lead_s
        self.W, self.H = int(round(1080 * scale)), int(round(1920 * scale))
        self.lay = dict(LAYOUTS[layout])
        self.lay.update(over.pop("lay", {}))
        self.opt = over
        sents = list(sentences or _SENTENCES)
        self.scenes, t = [], 0.0
        for k, (text, screen) in enumerate(sents):
            dur = scene_s
            if fault == "short_caption" and k == 1:
                dur = 0.4
            if fault == "fast_caption" and k == 1:
                text = "This sentence is far too long to read in the short time that the card stays on screen, really"
            start, end = t, t + dur
            ws = text.split()
            n = max(1, sum(len(w) + 1 for w in ws))
            words, acc = [], 0
            for w in ws:
                words.append([w, round(start + (end - start) * acc / n, 3), round(start + (end - start) * (acc + len(w)) / n, 3)])
                acc += len(w) + 1
            self.scenes.append({"text": text, "screen": screen, "start": round(start, 3), "end": round(end, 3), "words": words})
            t = end + gap_s
        self.total = round(t - gap_s + tail_s, 3)
        self.side = {"total": self.total, "scenes": self.scenes}
        self._bg = None
        self._layers = {}
        self.ocr = FakeOCR()

    # -- drawing
    def _draw_scene(self, k):
        if k in self._layers:
            return self._layers[k]
        from PIL import Image, ImageDraw
        S, lay, sc = self.scale, self.lay, self.scenes[k]
        if self._bg is None:
            self._bg = _background(self.W, self.H)
        img = Image.fromarray(self._bg.copy())
        d = ImageDraw.Draw(img, "RGBA")
        boxes = []

        def text(x, y, s, font, fill, anchor="ma", pad=0.1):
            d.text((x, y), s, font=font, fill=fill, anchor=anchor)
            x0, y0, x1, y1 = d.textbbox((x, y), s, font=font, anchor=anchor)
            p = (y1 - y0) * pad
            boxes.append(TextBox(x0 - p * 0.3, y0 - p, x1 + p * 0.3, y1 + p, s))

        def wrap(s, font, width):
            lines, cur = [], []
            for w in s.split():
                if cur and d.textlength(" ".join(cur + [w]), font=font) > width:
                    lines.append(" ".join(cur))
                    cur = []
                cur.append(w)
            return lines + ([" ".join(cur)] if cur else [])
        f = self.fault
        cap_font = _font((30 if f == "small_text" else lay["caption_font"]) * S)
        cx0, cx1 = lay["card_x"]
        cap_color = (58, 82, 130) if f == "low_contrast" else PAPER
        lines = wrap(sc["text"], cap_font, (cx1 - cx0 - 2 * 52) * S)
        lh = int(round(lay["caption_font"] * 1.28 * S)) if f != "small_text" else int(round(30 * 1.28 * S))
        card_top = lay["card_top"]
        if f == "safe_bottom":
            card_top = 1500
        card_h = 12 + 46 + len(lines) * lay["caption_font"] * 1.28 + 46 + 12
        card_bottom = card_top + card_h
        # slogan
        if sc["screen"]:
            sf = _font(lay["slogan_font"] * S)
            sl = wrap(sc["screen"], sf, 900 * S)
            top = lay["slogan_top"]
            if f == "safe_top":
                top = 90
            if f == "overlap_text":
                top = card_top + 12 + 46 - 80
            if f == "overlap_card":
                top = card_top - 100 - (len(sl) - 1) * lay["slogan_font"] * 1.1
            if f == "safe_side":
                for n_, s_ in enumerate(sl):
                    text(20 * S, (top + n_ * lay["slogan_font"] * 1.1) * S, s_, sf, PAPER, anchor="la")
            else:
                for n_, s_ in enumerate(sl):
                    text(self.W / 2, (top + n_ * lay["slogan_font"] * 1.1) * S, s_, sf, PAPER)
        # card: a thin bright border (2 px, like the glass card), a translucent fill
        d.rounded_rectangle([cx0 * S, card_top * S, cx1 * S, card_bottom * S], radius=56 * S, fill=(23, 58, 122, 175), outline=LINE + (95,), width=max(1, int(round(2 * S))))
        d.rounded_rectangle([(cx0 + 12) * S, (card_top + 12) * S, (cx1 - 12) * S, (card_bottom - 12) * S], radius=48 * S, outline=LINE + (70,), width=max(1, int(round(2 * S))))
        y = (card_top + 12 + 46) * S
        for n_, ln in enumerate(lines):
            text(self.W / 2, y, ln, cap_font, cap_color if n_ != len(lines) - 1 else (cap_color if f == "low_contrast" else MUTE))
            y += lh
        # progress bar: a track and a fill of the first 40%
        by = lay["bar_y"] * S
        d.rectangle([90 * S, by, 990 * S, by + 8 * S], fill=LINE + (60,))
        d.rectangle([90 * S, by, (90 + 360) * S, by + 8 * S], fill=SIGNAL + (255,))
        arr = np.asarray(img).copy()
        self._layers[k] = (arr, boxes)
        return arr, boxes

    def _scene_at(self, t):
        for k, sc in enumerate(self.scenes):
            if sc["start"] - self.lead <= t < sc["end"] + self.hold:
                return k
        return None

    def frame(self, t):
        """the frame at time t as a DrawnFrame (HxWx3 uint8)"""
        S = self.scale
        f = self.fault
        if f == "freeze" and 3.0 <= t < 5.7:
            t = 3.0
        if self._bg is None:
            self._bg = _background(self.W, self.H)
        if f == "first_black" and t < 0.2:
            return self._wrap(np.zeros((self.H, self.W, 3), np.uint8), [])
        if f == "first_flat" and t < 0.2:
            return self._wrap(np.full((self.H, self.W, 3), 70, np.uint8), [])
        if f == "black_run" and 3.0 <= t < 4.0:
            return self._wrap(np.zeros((self.H, self.W, 3), np.uint8), [])
        if f == "flicker" and 2.0 <= t < 3.5:
            v = 235 if int(t * 16) % 2 == 0 else 8
            return self._wrap(np.full((self.H, self.W, 3), v, np.uint8), [])
        k = self._scene_at(t)
        hidden = (f == "first_empty" and t < 1.2) or (f == "first_empty_short" and t < 0.3)
        if k is None or hidden:
            arr, boxes = self._bg.copy(), []
            by = int(self.lay["bar_y"] * S)
            arr[by:by + max(1, int(8 * S)), int(90 * S):int(990 * S)] = (np.array(LINE) * 0.2 + np.array(NAVY) * 0.8).astype(np.uint8)
        else:
            layer, boxes = self._draw_scene(k)
            arr = layer.copy()
            sc = self.scenes[k]
            ends = sc["end"] + self.hold
            fade = 0.2
            if t > ends - fade and k < len(self.scenes) - 1:                 # fade out the last 0.2 s
                a = max(0.0, (ends - t) / fade)
                arr = (self._bg.astype(np.float32) * (1 - a) + arr.astype(np.float32) * a).astype(np.uint8)
                boxes = boxes if a > 0.3 else []
        if f == "ghost":                                                      # around each scene change the next scene's text is already on screen over the old one
            for j in range(1, len(self.scenes)):
                st = self.scenes[j]["start"]
                if st - 0.25 <= t < st + 0.1:
                    la, bx0 = self._draw_scene(j - 1)
                    lb, bx1 = self._draw_scene(j)
                    arr = np.maximum(la, lb).copy()
                    boxes = list(bx0) + list(bx1)
        y = int(((t * 0.11) % 1.0) * self.H)                                  # the scan line: a moving decoration, not furniture
        row = arr[y:y + max(1, int(4 * S))]
        ramp = np.sin(np.linspace(0, np.pi, self.W))[None, :, None]
        row[:] = (row * (1 - 0.7 * ramp) + np.array(SIGNAL) * 0.7 * ramp).astype(np.uint8)
        return self._wrap(arr, boxes)

    @staticmethod
    def _wrap(arr, boxes):
        out = np.ascontiguousarray(arr).view(DrawnFrame)
        out.boxes = tuple(boxes)
        return out

    __call__ = frame


@dataclass
class Scenario:
    kind: str
    expect: str                         # the code that must be reported (None for a clean reel)
    ctx: Ctx
    reel: SyntheticReel
    sev: str = None                     # the severity the expected finding must have (None: any)


INJECT_KINDS = {"first_black": FIRST_FRAME, "first_flat": FIRST_FRAME, "first_empty": FIRST_FRAME, "black_run": PICTURE_BLACK, "freeze": PICTURE_FREEZE,
                "flicker": FLICKER, "safe_top": SAFE_ZONE, "safe_bottom": SAFE_ZONE, "safe_side": SAFE_ZONE, "safe_current_layout": SAFE_ZONE,
                "overlap_text": TEXT_OVERLAP, "overlap_card": TEXT_OVERLAP, "ghost": TRANSITION_GHOST, "small_text": TEXT_LEGIBILITY, "low_contrast": CONTRAST,
                "fast_caption": CAPTION_SPEED, "short_caption": CAPTION_SPEED}


def inject(kind, scale=1.0, profile=None, **over):
    """a drawn reel with one fault in it, ready for the detectors: inject("overlap_card").ctx. `kind` is a key of INJECT_KINDS, or "clean" for the
    same reel without a fault (expect None). `scale` shrinks the frames (0.5 is 540x960) to make the timeline scans cheaper. Needs Pillow."""
    import copy
    from . import profile as _profile
    if kind != "clean" and kind not in INJECT_KINDS:
        raise KeyError(f"unknown picture fault {kind!r}; known: clean, {', '.join(INJECT_KINDS)}")
    layout = "current" if kind == "safe_current_layout" else over.pop("layout", "ok")
    reel = SyntheticReel(scale=scale, layout=layout, fault=None if kind in ("clean", "safe_current_layout") else kind, **over)
    prof = with_picture_defaults(profile if profile is not None else copy.deepcopy(_profile.DEFAULT))
    ctx = Ctx(side=reel.side, profile=prof, frames=reel.frame, fps=reel.fps)
    ctx.ocr = reel.ocr
    return Scenario(kind, None if kind == "clean" else INJECT_KINDS[kind], ctx, reel)
