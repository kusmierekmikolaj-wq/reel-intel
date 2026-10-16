"""platform.py - is the finished file one a short-video platform takes as it is: size, frame rate, codec, pixel format, length, streaming layout, sound track.

probe(mp4) asks ffprobe and reads the file's top-level atoms (pure Python) and returns ONE dict of facts; the detectors only look at that dict, which the
caller stores in side["video"]. A detector never starts ffprobe itself, so it is tested with plain dicts; probe() is tested with small mp4s built by ffmpeg.

Facts (None / missing when the file does not say):
  width, height        coded size in pixels; display_width, display_height after the rotation flag and the pixel aspect ratio
  fps                  average frame rate as a float;  fps_nominal  the container's nominal rate (differs from fps for variable frame rate)
  codec, pix_fmt, color_range, profile
  duration             container duration, s;  video_duration, audio_duration  the streams' own durations, s
  video_start, audio_start   start time of each stream in the container, s (side["video"] feeds sync.check_av_offset)
  has_audio, audio_codec, audio_rate, audio_channels
  bit_rate, video_bit_rate, audio_bit_rate (bit/s), size (bytes)
  faststart            True: the moov atom comes before the mdat atom (the file can start playing while it downloads); False: moov at the end; None: unknown
  error                set (and nothing else) when ffprobe could not read the file

Where the numbers come from, and how sure they are: every platform value in profile.DEFAULT["platform"] is taken from third-party spec sheets (Instagram
Reels: 1080x1920, 9:16, 30 fps, H.264, AAC); none of them is official Meta documentation, and the sources disagree on the maximum length (90 s in the
app's recorder for years, 3 minutes now, uploads reported up to 15 minutes). That is why the length limit is ERROR only outside the PROFILE's window, not
outside "what Instagram takes". Nothing found names yuv420p, the position of the moov atom or a 48 kHz sound track as a requirement, they are encoder
conventions: the detectors for them say so. What the detectors cannot judge: whether the platform re-encodes the file anyway (the guides say it does), and
what the picture looks like.
"""
import json
import os
import subprocess
from fractions import Fraction
from pathlib import Path

from .base import Ctx, finding, register
from .profile import DEFAULT as _PROFILE_DEFAULT

FILE_BAD = register("FILE_BAD", "ffprobe cannot read the file", "ERROR", "rerender", "platform")
VIDEO_FACTS = register("VIDEO_FACTS", "the facts about the finished file that the platform checks were run on (or that there were none)", "INFO", None, "platform")
PLATFORM_ASPECT = register("PLATFORM_ASPECT", "the picture is not 9:16 (within the tolerance): the platform crops it or adds bars", "ERROR", "rerender", "platform")
PLATFORM_SIZE = register("PLATFORM_SIZE", "the picture is not 1080x1920", "WARN", "rerender", "platform")
PLATFORM_FPS = register("PLATFORM_FPS", "the frame rate is not 30 fps (within the tolerance) or varies", "WARN", "rerender", "platform")
PLATFORM_CODEC = register("PLATFORM_CODEC", "the video codec is not H.264/HEVC or the sound codec is not AAC", "WARN", "rerender", "platform")
PLATFORM_PIXFMT = register("PLATFORM_PIXFMT", "the pixel format is not yuv420p (yuvj420p, the same layout flagged full range, is only an INFO line)", "WARN", "rerender", "platform")
PLATFORM_DURATION = register("PLATFORM_DURATION", "the reel is shorter or longer than the profile's window (ERROR)", "ERROR", None, "platform")
PLATFORM_FASTSTART = register("PLATFORM_FASTSTART", "the moov atom is at the end of the file: playback cannot start while it is still downloading", "WARN", "rerender", "platform")
AV_LENGTH = register("AV_LENGTH", "the picture and the sound track differ in length by more than the tolerance", "WARN", "rerender", "platform")
AUDIO_RATE = register("AUDIO_RATE", "the sound track is not 44.1 or 48 kHz (the reel is made at 24 kHz and encoded as AAC)", "INFO", None, "platform")
NO_AUDIO = register("NO_AUDIO", "the file has no sound track", "ERROR", "rerender", "platform")

# thresholds this module needs that profile.py does not carry yet; ctx.profile["platform"] wins, then profile.DEFAULT, then this table.
# Move them into profile.DEFAULT["platform"].
EXTRA = {"platform": {
    "aspect_tol": 0.01,                      # 9:16 within 1 % (the task)
    "fps_tol": 0.5,                          # 30 +- 0.5 fps (the task); 29.97 passes
    "av_length_s": 0.15,                     # the old reel_qa.media() value; a real Remotion reel differs by 0.002 s
    "audio_codec": ["aac"],
    "audio_rates": [44100, 48000],
    "pix_fmt_tolerated": ["yuvj420p"],       # yuv420p flagged full range: what our own Remotion render writes; INFO instead of WARN (set [] to make it a WARN)
}}
PROVENANCE = {
    "platform.width/height/fps/codec": "third-party Reels spec sheets (argil.ai, wyzowl, socialbu, postfa.st ...), not Meta documentation: uncertain",
    "platform.min_s/max_s": "profile policy; sources say 3 min cap for Reels (some: uploads up to 15 min): uncertain",
    "platform.pix_fmt": "ffmpeg/H.264 convention (4:2:0, 8 bit); no source names it as a platform requirement",
    "platform.aspect_tol/fps_tol": "from the task text",
    "platform.av_length_s": "kept from reel_qa.media()",
}


def _th(ctx, key, section="platform"):
    for src in (getattr(ctx, "profile", None) or {}, _PROFILE_DEFAULT, EXTRA):
        v = src.get(section)
        if isinstance(v, dict) and key in v and v[key] is not None:
            return v[key]
    raise KeyError(f"no threshold {section}.{key}")


# ---------------------------------------------------------------- probe

def moov_before_mdat(path):
    """True when the moov atom comes before the mdat atom (the file is laid out for streaming, ffmpeg's -movflags +faststart), False when mdat comes first,
    None when it cannot be told (no moov/mdat atom found, not an ISO base media file, unreadable). Reads only the top-level atom headers."""
    try:
        total = os.path.getsize(path)
        with open(path, "rb") as f:
            pos = 0
            while pos + 8 <= total:
                f.seek(pos)
                h = f.read(16)
                if len(h) < 8:
                    return None
                size, typ = int.from_bytes(h[:4], "big"), h[4:8]
                if not all(32 <= c < 127 or c == 0xA9 for c in typ):          # not an atom type: this is not an mp4
                    return None
                if size == 1:
                    if len(h) < 16:
                        return None
                    size = int.from_bytes(h[8:16], "big")
                elif size == 0:
                    size = total - pos
                if size < 8:
                    return None
                if typ == b"moov":
                    return True
                if typ == b"mdat":
                    return False
                pos += size
    except OSError:
        return None
    return None


def _num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _rate(x):
    """'30000/1001' -> 29.97; '0/0' or junk -> None"""
    try:
        v = float(Fraction(str(x)))
        return v if v > 0 else None
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def facts_from_ffprobe(js, size=None):
    """the facts dict (see the module docstring) from ffprobe's -show_format -show_streams JSON; pure, so it is tested with canned JSON"""
    streams = js.get("streams") or []
    fmt = js.get("format") or {}
    v = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    out = {"has_audio": a is not None, "size": size if size is not None else _num(fmt.get("size")), "duration": _num(fmt.get("duration")),
           "bit_rate": _num(fmt.get("bit_rate"))}
    if v is not None:
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        rot = 0
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                rot = int(round(float(sd["rotation"]))) % 360
        rot = rot or int(round(_num((v.get("tags") or {}).get("rotate")) or 0)) % 360
        sar = 1.0
        try:
            n, d = str(v.get("sample_aspect_ratio") or "1:1").split(":")
            sar = float(n) / float(d) if float(n) > 0 and float(d) > 0 else 1.0       # "0:1" means "not set"
        except (ValueError, ZeroDivisionError):
            sar = 1.0
        cw, ch = w * sar, float(h)                                                    # what the picture is wide and high before the rotation flag
        dw, dh = (ch, cw) if rot in (90, 270) else (cw, ch)
        dw, dh = (round(dw) if abs(dw - round(dw)) < 0.01 else round(dw, 2)), (round(dh) if abs(dh - round(dh)) < 0.01 else round(dh, 2))
        fps, nominal = _rate(v.get("avg_frame_rate")), _rate(v.get("r_frame_rate"))
        vdur = _num(v.get("duration"))
        if vdur is None and _num(v.get("nb_frames")) and (fps or nominal):
            vdur = _num(v.get("nb_frames")) / (fps or nominal)
        out.update(width=w, height=h, display_width=dw, display_height=dh, rotation=rot, sar=sar, fps=fps or nominal, fps_nominal=nominal or fps,
                   codec=(v.get("codec_name") or "").lower() or None, pix_fmt=v.get("pix_fmt"), color_range=v.get("color_range"), profile=v.get("profile"),
                   video_duration=vdur if vdur is not None else out["duration"], video_start=_num(v.get("start_time")), video_bit_rate=_num(v.get("bit_rate")),
                   nb_frames=int(v["nb_frames"]) if str(v.get("nb_frames", "")).isdigit() else None)
    if a is not None:
        adur = _num(a.get("duration"))
        out.update(audio_codec=(a.get("codec_name") or "").lower() or None, audio_rate=int(_num(a.get("sample_rate")) or 0) or None, audio_channels=a.get("channels"),
                   audio_duration=adur if adur is not None else out["duration"], audio_start=_num(a.get("start_time")), audio_bit_rate=_num(a.get("bit_rate")))
    if out["duration"] is None:
        out["duration"] = max([d for d in (out.get("video_duration"), out.get("audio_duration")) if d is not None], default=None)
    return out


def probe(mp4):
    """facts about a finished file (module docstring); {"error": ...} when ffprobe is missing or cannot read it"""
    mp4 = str(mp4)
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", mp4], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
    except FileNotFoundError:
        return {"error": "ffprobe is not installed or not on PATH"}
    except subprocess.TimeoutExpired:
        return {"error": "ffprobe timed out"}
    if r.returncode != 0:
        return {"error": (r.stderr or "ffprobe failed").strip()[-300:]}
    try:
        js = json.loads(r.stdout)
    except ValueError:
        return {"error": "ffprobe printed no JSON"}
    if not js.get("streams"):
        return {"error": "the file has no streams"}
    out = facts_from_ffprobe(js, size=os.path.getsize(mp4))
    out["faststart"] = moov_before_mdat(mp4)
    return out


# ---------------------------------------------------------------- detectors (pure functions of the facts dict)

def _facts(ctx):
    v = ctx.side.get("video")
    return v if isinstance(v, dict) and v else None


def _usable(ctx):
    v = _facts(ctx)
    return v if v and not v.get("error") else None


def _size(v):
    return (v.get("display_width") or v.get("width")), (v.get("display_height") or v.get("height"))


def _has_audio(v):
    return bool(v["has_audio"]) if "has_audio" in v else bool(v.get("audio_codec"))


def check_video_facts(ctx: Ctx):
    """VIDEO_FACTS and FILE_BAD. FILE_BAD (ERROR) when side["video"] holds an error from probe(); otherwise one INFO line that says what the platform checks
    were run on; when side["video"] is absent, an INFO line says the container checks were skipped."""
    v = _facts(ctx)
    if v is None:
        return [finding("VIDEO_FACTS", "no ffprobe facts in the sidecar (side['video']): the platform checks were skipped")]
    if v.get("error"):
        return [finding("FILE_BAD", f"ffprobe cannot read the file: {v['error']}")]
    w, h = _size(v)
    fs = {True: "moov first (streams)", False: "moov at the end", None: "moov position unknown"}[v.get("faststart")]
    aud = f"{v.get('audio_codec')} {v.get('audio_rate')} Hz {v.get('audio_channels')} ch" if _has_audio(v) else "no sound"
    return [finding("VIDEO_FACTS", f"{w}x{h}, {v.get('fps') or 0:.2f} fps, {v.get('codec')} {v.get('pix_fmt')}, {aud}, {v.get('duration') or 0:.2f} s, {fs}", **{k: x for k, x in v.items()})]


def check_aspect(ctx: Ctx):
    """PLATFORM_ASPECT: display aspect ratio (width x pixel aspect, rotation applied) against the profile's width/height, tolerance platform.aspect_tol.
    ERROR: a reel that is not 9:16 is cropped or letterboxed by the platform (the guides agree; the exact behaviour is not documented by Meta)."""
    v = _usable(ctx)
    if not v:
        return []
    w, h = _size(v)
    if not w or not h:
        return []
    target = float(_th(ctx, "width")) / float(_th(ctx, "height"))
    ratio = w / h
    dev = abs(ratio / target - 1.0)
    if dev > float(_th(ctx, "aspect_tol")):
        return [finding("PLATFORM_ASPECT", f"picture is {w:.0f}x{h:.0f} ({ratio:.3f}), not 9:16 ({target:.4f}): off by {dev * 100:.1f}% (tolerance {float(_th(ctx, 'aspect_tol')) * 100:.0f}%)",
                        width=w, height=h, ratio=ratio, deviation=dev)]
    return []


def check_size(ctx: Ctx):
    """PLATFORM_SIZE: the picture is not exactly profile width x height (1080x1920). WARN: sources differ on whether the platform rescales, 1080x1920 is
    what every guide recommends. Fires on a wrong aspect too (a 1920x1080 file is also not 1080x1920); PLATFORM_ASPECT says why it matters."""
    v = _usable(ctx)
    if not v:
        return []
    w, h = _size(v)
    if not w or not h:
        return []
    tw, th = int(_th(ctx, "width")), int(_th(ctx, "height"))
    if (round(w), round(h)) != (tw, th):
        return [finding("PLATFORM_SIZE", f"picture is {w:.0f}x{h:.0f}, the recommended size is {tw}x{th}", width=w, height=h)]
    return []


def check_fps(ctx: Ctx):
    """PLATFORM_FPS: average frame rate within platform.fps_tol of the profile's fps (30), and no variable frame rate (nominal vs average rate differ
    by more than the same tolerance). WARN."""
    v = _usable(ctx)
    if not v or not v.get("fps"):
        return []
    want, tol = float(_th(ctx, "fps")), float(_th(ctx, "fps_tol"))
    out = []
    if abs(v["fps"] - want) > tol:
        out.append(finding("PLATFORM_FPS", f"frame rate is {v['fps']:.2f} fps, the profile wants {want:g} +-{tol:g}", fps=v["fps"]))
    elif v.get("fps_nominal") and abs(v["fps_nominal"] - v["fps"]) > tol:
        out.append(finding("PLATFORM_FPS", f"variable frame rate: average {v['fps']:.2f} fps, nominal {v['fps_nominal']:.2f} fps", fps=v["fps"], fps_nominal=v["fps_nominal"]))
    return out


def check_codec(ctx: Ctx):
    """PLATFORM_CODEC: video codec in profile platform.codec (h264, hevc) and, when there is sound, an audio codec in platform.audio_codec (aac). WARN: the guides
    say other inputs are re-encoded or must be normalised first; none says they are refused."""
    v = _usable(ctx)
    if not v:
        return []
    out = []
    ok = [c.lower() for c in _th(ctx, "codec")]
    if v.get("codec") and v["codec"].lower() not in ok:
        out.append(finding("PLATFORM_CODEC", f"video codec is {v['codec']}, expected one of {', '.join(ok)}", codec=v["codec"]))
    aok = [c.lower() for c in _th(ctx, "audio_codec")]
    if _has_audio(v) and v.get("audio_codec") and v["audio_codec"].lower() not in aok:
        out.append(finding("PLATFORM_CODEC", f"sound codec is {v['audio_codec']}, expected {', '.join(aok)}", audio_codec=v["audio_codec"]))
    return out


def check_pix_fmt(ctx: Ctx):
    """PLATFORM_PIXFMT: pixel format against profile platform.pix_fmt (yuv420p). WARN for any other layout (4:4:4, 4:2:2, 10 bit: many phone players and
    some platforms cannot decode them). A format in platform.pix_fmt_tolerated (yuvj420p: 4:2:0 8 bit flagged full range, what our Remotion render writes) is an
    INFO line: the layout is the same; a player that ignores the range flag shows different contrast, and one web search found anecdotal reports (an Adobe
    feature request, not Meta) of full-range files looking washed out on Instagram: unverified. It stays an INFO because a re-render cannot fix it (the same
    renderer writes yuvj420p again); the fix is in the render's pixel format / color range options."""
    v = _usable(ctx)
    if not v or not v.get("pix_fmt"):
        return []
    want = str(_th(ctx, "pix_fmt"))
    if v["pix_fmt"] == want:
        return []
    if v["pix_fmt"] in _th(ctx, "pix_fmt_tolerated"):
        return [finding("PLATFORM_PIXFMT", f"pixel format is {v['pix_fmt']} (color range {v.get('color_range')}): the same 4:2:0 8 bit layout as {want}, flagged full range. "
                        "A player that reads the flag shows it right; user reports (anecdotal, not verified) say Instagram can show full-range files washed out, "
                        f"so the safe choice is limited-range {want}", "INFO", pix_fmt=v["pix_fmt"])]
    return [finding("PLATFORM_PIXFMT", f"pixel format is {v['pix_fmt']}, expected {want} (4:2:0, 8 bit): many players and platforms cannot decode other layouts", pix_fmt=v["pix_fmt"])]


def check_duration(ctx: Ctx):
    """PLATFORM_DURATION: ERROR when the reel is shorter than platform.min_s or longer than platform.max_s (the profile's window, not a proven platform limit)."""
    v = _usable(ctx)
    if not v or v.get("duration") is None:
        return []
    d, lo, hi = float(v["duration"]), float(_th(ctx, "min_s")), float(_th(ctx, "max_s"))
    if d < lo:
        return [finding("PLATFORM_DURATION", f"the reel is {d:.1f} s, the profile's minimum is {lo:g} s", duration=d)]
    if d > hi:
        return [finding("PLATFORM_DURATION", f"the reel is {d:.1f} s, the profile's maximum is {hi:g} s (sources say Reels now take up to 3 minutes; the profile is stricter)", duration=d)]
    return []


def check_faststart(ctx: Ctx):
    """PLATFORM_FASTSTART: WARN when side["video"]["faststart"] is False (moov after mdat). Nothing when it is True or unknown (None). It does not change what
    a platform shows after upload; it matters for a file played or previewed while it downloads."""
    v = _usable(ctx)
    if not v or v.get("faststart") is not False:
        return []
    return [finding("PLATFORM_FASTSTART", "the moov atom is after the media data: playback cannot start before the whole file is loaded (remux with -movflags +faststart)")]


def check_av_length(ctx: Ctx):
    """AV_LENGTH: WARN when the picture and the sound track differ in length by more than platform.av_length_s. Needs both durations (no sound: NO_AUDIO)."""
    v = _usable(ctx)
    if not v or not _has_audio(v):
        return []
    a, b = v.get("video_duration"), v.get("audio_duration")
    if a is None or b is None:
        return []
    if abs(a - b) > float(_th(ctx, "av_length_s")):
        return [finding("AV_LENGTH", f"picture {a:.2f} s, sound {b:.2f} s: they differ by {abs(a - b):.2f} s (tolerance {float(_th(ctx, 'av_length_s')):g} s)",
                        video_duration=a, audio_duration=b)]
    return []


def check_audio_rate(ctx: Ctx):
    """AUDIO_RATE: INFO when the sound track is not 44.1 or 48 kHz. Our reels are made at 24 kHz and encoded as AAC; whether Instagram resamples a lower
    rate is NOT known (nothing found says), so this is information, not a fault."""
    v = _usable(ctx)
    if not v or not _has_audio(v) or not v.get("audio_rate"):
        return []
    if v["audio_rate"] not in [int(r) for r in _th(ctx, "audio_rates")]:
        return [finding("AUDIO_RATE", f"the sound track is {v['audio_rate']} Hz, not 44.1 or 48 kHz (the reel is made at 24 kHz and encoded as AAC; whether Instagram resamples it is not known)",
                        audio_rate=v["audio_rate"])]
    return []


def check_no_audio(ctx: Ctx):
    """NO_AUDIO: ERROR when the file has no sound track (a voice-over reel without voice). A silent but present track is the audio module's business."""
    v = _usable(ctx)
    if not v or _has_audio(v):
        return []
    return [finding("NO_AUDIO", "the file has no sound track")]


DETECTORS = [check_video_facts, check_aspect, check_size, check_fps, check_codec, check_pix_fmt, check_duration, check_faststart, check_av_length, check_audio_rate,
             check_no_audio]


# ---------------------------------------------------------------- fault injection (for the lead's matrix and the tests)

def make_mp4(path, size="1080x1920", fps=30, pix_fmt="yuv420p", audio=True, faststart=True, seconds=3.2, audio_seconds=None, vcodec="libx264", ar=48000,
             audio_codec="aac", channels=2):
    """a small mp4 built by ffmpeg from synthetic sources (a flat colour with a box, a sine); every argument is one fault. Returns the path, or None
    when ffmpeg is missing or fails (callers skip). `audio_seconds` longer than `seconds` makes the sound outlast the picture (AV_LENGTH)."""
    w, h = size.split("x")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=0x203040:s={w}x{h}:r={fps}:d={seconds},drawbox=x=iw/4:y=ih/4:w=iw/2:h=ih/2:color=0xe0a030:t=fill"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=220:r={ar}:d={audio_seconds or seconds}"]
    cmd += ["-c:v", vcodec]
    if vcodec == "libx264":
        cmd += ["-preset", "ultrafast", "-crf", "30"]
    cmd += ["-pix_fmt", pix_fmt]
    if audio:
        cmd += ["-c:a", audio_codec, "-b:a", "96k", "-ac", str(channels)]
    else:
        cmd += ["-an"]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    cmd += [str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return Path(path) if r.returncode == 0 and Path(path).exists() else None


# kind -> (make_mp4 keyword arguments, the code that must be reported). "clean" must report no ERROR and no WARN.
FAULTS = {
    "clean": ({}, None),
    "size": ({"size": "720x1280"}, "PLATFORM_SIZE"),
    "aspect": ({"size": "1280x720"}, "PLATFORM_ASPECT"),
    "no_audio": ({"audio": False}, "NO_AUDIO"),
    "fps": ({"fps": 25}, "PLATFORM_FPS"),
    "pix_fmt": ({"pix_fmt": "yuv444p"}, "PLATFORM_PIXFMT"),
    "faststart": ({"faststart": False}, "PLATFORM_FASTSTART"),
    "too_short": ({"seconds": 1.2}, "PLATFORM_DURATION"),
    "av_length": ({"audio_seconds": 5.2}, "AV_LENGTH"),
    "audio_rate": ({"ar": 24000}, "AUDIO_RATE"),
    "codec": ({"vcodec": "mpeg4", "pix_fmt": "yuv420p"}, "PLATFORM_CODEC"),
}


def inject(kind, video=None):
    """a copy of a facts dict (default: a clean 1080x1920 reel) with one fault put in, for tests that need no ffmpeg. Kinds: the keys of FAULTS plus
    'too_long' and 'unreadable'."""
    v = dict(video) if video else {
        "width": 1080, "height": 1920, "display_width": 1080, "display_height": 1920, "fps": 30.0, "fps_nominal": 30.0, "codec": "h264", "pix_fmt": "yuv420p",
        "duration": 21.03, "video_duration": 21.033, "audio_duration": 21.035, "video_start": 0.0, "audio_start": 0.0, "has_audio": True, "audio_codec": "aac",
        "audio_rate": 48000, "audio_channels": 2, "faststart": True, "size": 10_000_000}
    if kind == "clean":
        pass
    elif kind == "size":
        v.update(width=720, height=1280, display_width=720, display_height=1280)
    elif kind == "aspect":
        v.update(width=1280, height=720, display_width=1280, display_height=720)
    elif kind == "no_audio":
        v.update(has_audio=False, audio_codec=None, audio_rate=None, audio_duration=None, audio_start=None)
    elif kind == "fps":
        v.update(fps=25.0, fps_nominal=25.0)
    elif kind == "pix_fmt":
        v.update(pix_fmt="yuv444p")
    elif kind == "faststart":
        v.update(faststart=False)
    elif kind == "too_short":
        v.update(duration=1.2, video_duration=1.2, audio_duration=1.2)
    elif kind == "too_long":
        v.update(duration=95.0, video_duration=95.0, audio_duration=95.0)
    elif kind == "av_length":
        v.update(audio_duration=v["video_duration"] + 2.0)
    elif kind == "audio_rate":
        v.update(audio_rate=24000)
    elif kind == "codec":
        v.update(codec="vp9")
    elif kind == "unreadable":
        v = {"error": "Invalid data found when processing input"}
    else:
        raise KeyError(f"unknown fault kind {kind}")
    return v
