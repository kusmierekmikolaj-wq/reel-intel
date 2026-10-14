"""visuals.py - what the video shows and sounds like, not only what is said.

  reel-intel visuals [--limit N]        writes data/<id>/visual.json for every scanned video that has none

Why: a transcript is one signal out of several. Short-form research points at the picture (text overlays, cover, close-ups, editing pace) and
the sound (speech rate, brightness of the voice) as well. This step downloads a small copy of each video (480p at most, deleted never: it stays in
the video's folder as video_small.*) and measures, with plain OpenCV and the OCR already used for visual reels:

  cuts_per_10s        picture changes per 10 seconds (colour-histogram jumps between frames sampled 4 times a second)
  motion              average frame-to-frame change, 0..1
  face_first_3s       a frontal face in one of the frames sampled in the first 3 seconds (OpenCV Haar detector: fast, but misses profiles
                      and can fire on posters, so treat it as "probably a face on screen")
  text_first_2s       text on screen in the first 2 seconds
  burned_captions     most sampled frames carry changing text in the lower half: captions burned into the picture (a repeated logo does not count)
  ocr_asr_overlap     of the 5-letter pieces of the text read from the picture, the share found in the transcript's letters. With burned-in
                      captions this is an INDEPENDENT second reading of the speech, so a low value flags a transcript (or an OCR) worth a look.
                      It is agreement, not accuracy: captions are often edited, and OCR makes mistakes too.
  rms_db, centroid_hz loudness and brightness of the sound (average spectral centroid)
  speech_share        share of the clip in which words are spoken
"""
import json
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .common import CFG, DATA, jload, run, ytdlp

VIDEO_EXT = (".mp4", ".webm", ".mkv")


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def find_video(d):
    return next((p for p in sorted(d.glob("video*")) if p.suffix in VIDEO_EXT), None)


def fetch_video(d):
    """a small copy of the video (best up to 480p). Reuses video_small.* / video.* when the scan already made one."""
    v = find_video(d)
    if v:
        return v
    url = jload(d / "features.json").get("url")
    for fmt in ("bv*[height<=480]/wv*", "w"):                      # video-only first (audio is already in audio.wav); plain worst as a fallback
        run(ytdlp() + ["-f", fmt, "--no-warnings", "-o", str(d / "video_small.%(ext)s"), url])
        v = find_video(d)
        if v:
            return v
    return None


def sample(video, rate=4.0):
    """(duration, list of (t, small gray frame, hist), {t: frame at working size}) sampled at `rate` per second"""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, round(fps / rate))
    frames, i = [], 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % step == 0:
            ok, img = cap.retrieve()
            if ok:
                h, w = img.shape[:2]
                scale = 480.0 / max(h, w)
                work = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale)))) if scale < 1 else img
                small = cv2.resize(work, (64, 112))
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 8, 8], [0, 180, 0, 256, 0, 256])
                cv2.normalize(hist, hist)
                frames.append((i / fps, cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype("float32"), hist, work))
        i += 1
    cap.release()
    return (i / fps if fps else 0), frames


def cut_and_motion(frames, threshold=0.55):
    import cv2
    import numpy as np
    cuts, diffs = 0, []
    for a, b in zip(frames, frames[1:]):
        if cv2.compareHist(a[2], b[2], cv2.HISTCMP_CORREL) < threshold:
            cuts += 1
        diffs.append(float(np.abs(a[1] - b[1]).mean()) / 255.0)
    return cuts, (sum(diffs) / len(diffs) if diffs else 0.0)


_FACE = None
_OCR = None


def has_face(img):
    import cv2
    global _FACE
    if _FACE is None:
        _FACE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    return len(_FACE.detectMultiScale(gray, 1.1, 6, minSize=(max(24, img.shape[1] // 12),) * 2)) > 0


def read_text(img):
    """[(text, centre y as a share of the height)] with confidence > 0.6 and at least 3 characters"""
    import cv2
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    h = img.shape[0]
    up = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC) if max(img.shape[:2]) < 600 else img
    r, _ = _OCR(up)
    out = []
    for box, text, conf in (r or []):
        if float(conf) > 0.6 and len(text.strip()) >= 3:
            ys = [p[1] for p in box]
            out.append((text.strip(), (sum(ys) / len(ys)) / up.shape[0]))
    return out


def audio_stats(d):
    import wave
    import numpy as np
    p = d / "audio.wav"
    if not p.exists():
        return {}
    try:
        with wave.open(str(p)) as w:
            sr, n = w.getframerate(), w.getnframes()
            x = np.frombuffer(w.readframes(n), dtype=np.int16).astype("float32") / 32768.0
            if w.getnchannels() > 1:
                x = x.reshape(-1, w.getnchannels()).mean(axis=1)
    except Exception:
        return {}
    if len(x) < sr:
        return {}
    rms = float(np.sqrt(np.mean(x ** 2)) + 1e-9)
    win, cents = 1024, []
    freqs = np.fft.rfftfreq(win, 1 / sr)
    for s in range(0, len(x) - win, win * 8):                        # every 8th window is plenty for an average
        seg = x[s:s + win] * np.hanning(win)
        mag = np.abs(np.fft.rfft(seg))
        if mag.sum() > 1e-3:
            cents.append(float((freqs * mag).sum() / mag.sum()))
    return {"rms_db": round(20 * float(np.log10(rms)), 1), "centroid_hz": round(sum(cents) / len(cents)) if cents else None}


def screen_text(d, every=1.0, max_frames=300):
    """Everything written on the screen, for videos WITH speech too (names of skills and tools, GitHub repositories, commands).
    One frame a second; a frame that looks like the last one read is skipped; identical lines are kept once, with the time of the first sighting.
    Saved as screen_text.json: {"lines": [{"t", "text"}], "mentions": {repos, tokens, known}}"""
    import cv2
    import numpy as np
    from .common import glossary_prompt
    from .common import near_duplicate
    from . import screenmine
    video = find_video(d)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    dur = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps
    if dur <= 0:
        raise ValueError("video could not be read")
    step = max(every, dur / max_frames)
    prev, lines = None, []
    for t in np.arange(0.3, dur, step):
        cap.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000)
        ok, img = cap.read()
        if not ok:
            continue
        small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (54, 96)).astype("int16")
        if prev is not None and abs(small - prev).mean() < 3:
            continue
        prev = small
        for text, _y in read_text(img):
            if not any(near_duplicate(text, x["text"]) for x in lines):
                lines.append({"t": round(float(t), 1), "text": text})
    cap.release()
    f = jload(d / "features.json")
    names = [n.strip() for n in glossary_prompt(f.get("language")).split(",") if n.strip()]
    mentions = screenmine.mine([(x["t"], x["text"]) for x in lines], names)
    return {"lines": lines, "mentions": mentions, "frames_step_s": round(float(step), 2)}


def words_of(texts, minlen=4):
    return [w for t in texts for w in re.findall(r"[^\W\d_]+", fold(t)) if len(w) >= minlen]


def close(a, b):
    import difflib
    return a == b or (len(a) >= 5 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.85)


def analyse(d):
    video = find_video(d)
    dur, frames = sample(video)
    if len(frames) < 4 or dur <= 0:
        raise ValueError("video could not be read")
    f = jload(d / "features.json")
    tr = jload(d / "transcript.json")
    cuts, motion = cut_and_motion(frames)
    first3 = [fr for fr in frames if fr[0] <= 3.0]
    face = any(has_face(fr[3]) for fr in first3[:6])
    texts_early = [t for fr in frames if fr[0] <= 2.0 for t, _ in read_text(fr[3])] if frames else []
    idx = sorted({round(k * (len(frames) - 1) / 7) for k in range(8)})                    # 8 frames spread over the whole video
    lower_lines, all_text = [], []
    for k in idx:
        tx = read_text(frames[k][3])
        all_text += [t for t, _ in tx]
        lower_lines.append({fold(re.sub(r"\s+", "", t)) for t, y in tx if y >= 0.45 and len(t) >= 8})
    with_text = [s for s in lower_lines if s]
    distinct = len(set().union(*with_text)) if with_text else 0
    # captions CHANGE from frame to frame; a logo or watermark repeats the same text in every frame and must not count
    burned = len(with_text) / len(idx) >= 0.5 and distinct >= max(3, 0.6 * len(with_text))
    words = [w for s in tr["segments"] for w in s["words"]]
    out = {"cuts_per_10s": round(cuts / dur * 10, 2), "motion": round(motion, 3), "face_first_3s": bool(face), "text_first_2s": bool(texts_early),
           "burned_captions": bool(burned), "ocr_chars": sum(len(re.sub(r"\W", "", x)) for x in all_text), "duration_s": round(dur, 1)}
    stream = "".join(re.sub(r"[^a-z]", "", fold(w["w"])) for w in words)
    ocr_stream = [re.sub(r"[^a-z]", "", fold(x)) for x in all_text]
    grams = [g for s in ocr_stream for g in (s[i:i + 5] for i in range(len(s) - 4))]
    if burned and len(words) >= 10 and len(grams) >= 20:
        # spaces are unreliable in OCR output ("czylidzisiajpokazemywam"), so compare 5-letter pieces of the two letter streams
        out["ocr_asr_overlap"] = round(sum(g in stream for g in grams) / len(grams), 2)
        unmatched = [x[:60] for x in all_text if len(x) >= 12 and sum(g in stream for g in (re.sub(r"[^a-z]", "", fold(x))[i:i + 5] for i in range(max(0, len(re.sub(r"[^a-z]", "", fold(x))) - 4)))) == 0]
        if unmatched:
            out["ocr_unmatched"] = unmatched[:4]
    if words:
        out["speech_share"] = round(min(1.0, sum(w["e"] - w["s"] for w in words) / max(f.get("duration_s") or dur, 1)), 2)
    out.update(audio_stats(d))
    return out


def run_all(limit=None):
    ids = [d for d in sorted(DATA.iterdir()) if (d / "features.json").exists() and (d / "transcript.json").exists()
           and not ((d / "visual.json").exists() and (d / "screen_text.json").exists())]
    if limit:
        ids = ids[:limit]
    if not ids:
        print("visuals: nothing to do")
        return
    t0 = time.time()
    print(f"visuals: {len(ids)} videos (download a small copy, then measure)", flush=True)
    with ThreadPoolExecutor(max_workers=CFG.get("workers", 4)) as ex:
        videos = list(ex.map(fetch_video, ids))
    ok = failed = 0
    for d, v in zip(ids, videos):
        if not v:
            print(f"[{d.name}] no video: skipped")
            failed += 1
            continue
        try:
            if not (d / "visual.json").exists():
                (d / "visual.json").write_text(json.dumps(analyse(d), ensure_ascii=False, indent=1), encoding="utf-8")
            if not (d / "screen_text.json").exists():
                (d / "screen_text.json").write_text(json.dumps(screen_text(d), ensure_ascii=False, indent=1), encoding="utf-8")
            ok += 1
        except Exception as e:
            print(f"[{d.name}] failed: {e}")
            failed += 1
    print(f"visuals done in {time.time() - t0:.0f}s: {ok} measured, {failed} skipped")


def main():
    a = sys.argv[1:]
    run_all(int(a[a.index("--limit") + 1]) if "--limit" in a else None)


if __name__ == "__main__":
    main()
