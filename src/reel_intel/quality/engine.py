"""engine.py - runs the whole check on a finished reel: builds the Ctx from the mp4 and the files made next to it, runs every detector, never lets one crash
hide the others, and refuses to trust a sidecar that does not belong to the file.

Files next to  rolka.mp4  (written by make.py):
  rolka.qa.json      the sidecar: scenes with word times, params, engine used, renderer, mouth track, sha256 of the mp4 ...
  rolka_voice.wav    the dry voice (the voice track alone, before the music)
  rolka_music.wav    the music stem as mixed (optional: with it the music can be judged apart from the voice)
What it adds by itself (each optional, each says so when it could not run):
  video facts    platform.probe(mp4) into side["video"]
  ears           the sound-event model (ears.py in the Chatterbox environment) on the mp4's sound
  rehear         the words recognised from the FINISHED mp4's sound (faster-whisper) into side["rehear"], for CAPTION_SYNC
  frames, OCR    frames of the mp4 (ffmpeg) and RapidOCR, for the picture checks
"""
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from . import audio, mix, picture, platform, profile as _profile, speech, sync
from .base import Ctx, finding, register

SR = 24000
MODULES = {"audio": audio, "speech": speech, "mix": mix, "picture": picture, "platform": platform, "sync": sync}
ORDER = ("platform", "speech", "audio", "mix", "picture", "sync")

QA_ERROR = register("QA_ERROR", "a detector crashed (a bug in the checker, not in the reel)", "WARN", None, "engine")
SIDECAR_MISSING = register("SIDECAR_MISSING", "there is no <reel>.qa.json next to the file: the timeline is unknown, only the file itself can be checked", "ERROR", None, "engine")
SIDECAR_STALE = register("SIDECAR_STALE", "the sidecar was written for another version of this file (its checksum differs): every timing based check would be wrong", "ERROR", "rerender", "engine")
SIDECAR_OLD = register("SIDECAR_OLD", "the sidecar has no checksum (it was made by an older version): it cannot be verified to belong to this file", "INFO", None, "engine")


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sidecar_path(mp4):
    return Path(mp4).with_suffix(".qa.json")


def load_sidecar(mp4):
    p = sidecar_path(mp4)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def decode(path, sr=SR):
    """mono float32 at `sr` from any audio or video file, None when there is nothing to read"""
    from .calibrate import decode as _decode
    x, _sr = _decode(path, sr)
    return x


def listen(mp4, tts_python=None):
    """the sound-event model on the reel's sound: its JSON (windows with labels) or None when its environment is not installed or it failed"""
    if tts_python is None:
        from .. import make
        tts_python = make.TTS_PYTHON
    tts_python = Path(tts_python)
    if not tts_python.is_file():
        return None
    ears_py = Path(__file__).resolve().parents[1] / "ears.py"
    with tempfile.TemporaryDirectory(prefix="ears_") as d:
        wav, js = Path(d) / "f.wav", Path(d) / "e.json"
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-vn", "-ac", "1", "-ar", "16000", str(wav)], capture_output=True)
        if r.returncode != 0:
            return None
        try:
            subprocess.run([str(tts_python), str(ears_py), str(wav), str(js), "1.0", "0.5"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
            return json.loads(js.read_text(encoding="utf-8")) if js.exists() else None
        except Exception:
            return None


def rehear(mp4, lang):
    """[[word, t0, t1]] recognised from the finished mp4's sound (faster-whisper, word timestamps); None when the recogniser is not available"""
    try:
        from ..common import whisper_model
        with tempfile.TemporaryDirectory(prefix="rehear_") as d:
            wav = Path(d) / "f.wav"
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-vn", "-ac", "1", "-ar", "16000", str(wav)], capture_output=True)
            if r.returncode != 0:
                return None
            segs, _ = whisper_model().transcribe(str(wav), language=lang, word_timestamps=True, beam_size=1, vad_filter=False, condition_on_previous_text=False)
            return [[w.word.strip(), round(float(w.start), 3), round(float(w.end), 3)] for s in segs for w in (s.words or [])]
    except Exception:
        return None


def build_ctx(mp4, profile=None, ears=True, frames=True, ocr=True, do_rehear=True, side=None, probe=True):
    """(Ctx, side) for a reel. `side` may be passed (the sidecar already in memory); it is changed in place with the facts this adds."""
    mp4 = Path(mp4)
    side = side if side is not None else (load_sidecar(mp4) or {"scenes": []})
    prof = profile if profile is not None else _profile.load()
    if probe and not side.get("video"):
        side["video"] = platform.probe(mp4)
    final = decode(mp4)
    vp, mp = mp4.with_name(mp4.stem + "_voice.wav"), mp4.with_name(mp4.stem + "_music.wav")
    voice = decode(vp) if vp.exists() else None
    music = decode(mp) if mp.exists() else None
    if final is None:
        final = np.zeros(0, dtype="float32")
    ctx = Ctx(side=side, final=final if len(final) else None, sr=SR, voice=voice, music=music, profile=prof, mp4=mp4, fps=int((side.get("params") or {}).get("fps") or 30))
    if ears:
        ctx.ears = listen(mp4)
    if do_rehear and not side.get("rehear") and side.get("scenes"):
        lang = (side.get("params") or {}).get("lang") or "pl"
        rh = rehear(mp4, lang)
        if rh:
            side["rehear"] = rh
    if frames:
        ctx.frames = picture.frame_provider(mp4, fps=ctx.fps)
        ctx.ocr = picture.default_ocr() if (ocr and ctx.frames is not None) else None
    return ctx, side


def check_sidecar(mp4, side):
    """SIDECAR_STALE when the sidecar's checksum is not the file's, SIDECAR_OLD (INFO) when it has none"""
    want = side.get("mp4_sha256")
    if not want:
        return [finding(SIDECAR_OLD, "the sidecar has no checksum of the mp4 (older version): it cannot be verified to belong to this file")]
    try:
        have = sha256(mp4)
    except OSError:
        return []
    if have != want:
        return [finding(SIDECAR_STALE, "the sidecar was written for another version of this file (the checksum differs): the file was re-rendered or replaced without it")]
    return []


def run_detectors(ctx, modules=None, skip=()):
    """every detector of the given modules (default: all, in ORDER), a crash becomes a QA_ERROR finding and the others still run"""
    out = []
    for name in (modules or ORDER):
        for det in MODULES[name].DETECTORS:
            if det.__name__ in skip:
                continue
            try:
                out += det(ctx)
            except Exception as e:
                out.append(finding(QA_ERROR, f"{name}.{det.__name__} crashed: {type(e).__name__}: {e}"))
    return out


def check_reel(mp4, profile=None, modules=None, **opts):
    """{findings, ctx, side}: the whole check of a finished reel. A missing file or sidecar is a finding, not an exception."""
    mp4 = Path(mp4)
    if not mp4.is_file():
        return {"findings": [finding("FILE_BAD", f"{mp4} does not exist")], "ctx": None, "side": None}
    raw = load_sidecar(mp4)
    findings = []
    if raw is None:
        findings.append(finding(SIDECAR_MISSING, "there is no .qa.json next to the file: only the file itself is checked, not the voice, captions or timeline"))
        raw = {"scenes": []}
    else:
        findings += check_sidecar(mp4, raw)
    ctx, side = build_ctx(mp4, profile=profile, side=raw, **opts)
    if any(f.code == "SIDECAR_STALE" for f in findings):
        mods = [m for m in (modules or ORDER) if m in ("platform", "mix", "picture")]       # only what does not depend on the sidecar's timeline
    else:
        mods = modules
    findings += run_detectors(ctx, mods)
    return {"findings": findings, "ctx": ctx, "side": side}
