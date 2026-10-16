"""reel_qa.py - finds faults in a rendered reel before anybody posts it.

  reel-intel make qa rolka.mp4        checks the file against the timeline saved next to it (rolka.qa.json), prints ERROR / WARN / INFO
  reel-intel make qa --selftest       injects known faults into synthetic audio and shows that every one is caught

What it looks at (the picture is checked only where a number can tell, never by "it looks fine"):
  gaps      the time between two scenes: too long, music louder than it should be, low rumble, a click
  voice     a sentence cut off or garbled (the voice engine's words do not match the script), too fast or too slow, level jumps between scenes,
            dead air after the last word, hiss under the voice
  mix       loudness of the whole reel, clipping, music too loud compared with the voice
  video     video and audio of different length, black frames
  mouth     the mascot's mouth does not follow the loudness of the voice
Every check is a pure function of numbers, so it is tested without a render.
"""
import json
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

SR = 24000


def _db(x):
    import numpy as np
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-9)) if len(x) else -120.0


def read_wav(path):
    import numpy as np
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768.0
        if w.getnchannels() > 1:
            x = x[::w.getnchannels()]
        return x, w.getframerate()


def lf_share(x, sr, cut=120.0):
    """share of the energy below `cut` Hz (rumble)"""
    import numpy as np
    if len(x) < 64:
        return 0.0
    s = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / sr)
    return float(s[f < cut].sum() / (s.sum() + 1e-12))


def gaps(final, sr, scenes):
    """[(severity, code, message)] for the time between the last word of a scene and the first word of the next one"""
    out = []
    speech = [_db(final[int(s["words"][0][1] * sr):int(s["words"][-1][2] * sr)]) for s in scenes if s["words"]]
    ref = sorted(speech)[len(speech) // 2] if speech else -20.0
    for i in range(len(scenes) - 1):
        a, b = scenes[i]["words"][-1][2], scenes[i + 1]["words"][0][1]
        n = f"{i + 1}->{i + 2}"
        if b - a > 0.9:
            out.append(("WARN", "GAP_LONG", f"scenes {n}: {b - a:.2f} s of nothing said between the sentences (aim at 0.25-0.6 s)"))
        if b - a < 0.12:
            out.append(("WARN", "GAP_SHORT", f"scenes {n}: only {b - a:.2f} s between the sentences, they run together"))
        seg = final[int((a + 0.05) * sr):int((b - 0.02) * sr)]
        if len(seg) < sr * 0.08:
            continue
        lvl = _db(seg)
        if lvl > ref - 8:                                                  # music audible in a pause is normal; nearly as loud as the voice is not
            out.append(("WARN", "GAP_LOUD", f"scenes {n}: sound between the sentences is {lvl:.0f} dB, only {ref - lvl:.0f} dB under the voice (the music is too loud or something is playing)"))
        if lf_share(seg, sr) > 0.45 and lvl > -42:
            out.append(("WARN", "GAP_RUMBLE", f"scenes {n}: the sound between the sentences is mostly very low tones ({lf_share(seg, sr) * 100:.0f}% under 120 Hz): rumble"))
        if len(seg) > 8 and float(abs(seg[1:] - seg[:-1]).max()) > 0.35:
            out.append(("WARN", "GAP_CLICK", f"scenes {n}: a click or a jump in the sound between the sentences"))
    return out


def flatness(x):
    """0 = a pure tone, 1 = white noise (spectral flatness)"""
    import numpy as np
    if len(x) < 256:
        return 0.0
    s = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2 + 1e-12
    return float(np.exp(np.mean(np.log(s))) / np.mean(s))


def soft_tail(x, sr):
    """seconds of quiet-but-audible sound after the last speech in `x`: frames between 24 and 45 dB under the usual speech level, after the last speech frame"""
    import numpy as np
    hop = max(1, int(sr * 0.02))
    n = (len(x) - hop) // hop
    if n < 5:
        return 0.0
    db = np.array([_db(x[i * hop:(i + 1) * hop]) for i in range(n)])
    p = float(np.percentile(db, 90))
    speech = np.where(db > p - 24)[0]
    if not len(speech):
        return 0.0
    after = db[speech[-1] + 1:]
    return float(np.sum((after > p - 45)) * hop / sr)


def breaths(scenes, voice_x, sr):
    """sound that is neither silence nor speech inside the voice track: before the first word, between words, after the last word.
    A breath, a mouth noise or a trailing murmur is quiet but noisy (high spectral flatness) and louder than the room"""
    out = []
    for i, s in enumerate(scenes, 1):
        w = s["words"]
        if not w:
            continue
        st = soft_tail(voice_x[int(s["start"] * sr):int(s["end"] * sr)], sr)
        if st > 0.12:
            out.append(("WARN", "VOICE_BREATH", f"scene {i}: {st:.2f} s of a quiet breathy tail after the last word (a breath or a trailing murmur)"))
        speech = _db(voice_x[int(w[0][1] * sr):int(w[-1][2] * sr)])
        spots = [("before the first word", s["start"], w[0][1] - 0.04), ("after the last word", w[-1][2] + 0.04, s["end"])]
        spots += [(f"between '{a[0]}' and '{b[0]}'", a[2] + 0.04, b[1] - 0.04) for a, b in zip(w, w[1:]) if b[1] - a[2] > 0.25]
        for where, a, b in spots:
            seg = voice_x[int(max(0, a) * sr):int(b * sr)]
            if len(seg) < sr * 0.1:
                continue
            lvl = _db(seg)
            if lvl > speech - 32 and lvl > -52 and flatness(seg) > 0.12:
                out.append(("WARN", "VOICE_BREATH", f"scene {i}: a breath or noise {where} ({lvl:.0f} dB, {lvl - speech:.0f} dB under the speech, noisy)"))
    return out


def voice(scenes, voice_x, sr):
    """checks on the voice track alone"""
    out = []
    levels = []
    for i, s in enumerate(scenes, 1):
        if not s["words"]:
            continue
        a, b = s["words"][0][1], s["words"][-1][2]
        syl = len(re.findall(r"[aeiouyąęó]+", s["text"].lower()))              # syllables, not words: Polish has many one-letter words
        rate = syl / max(b - a, 0.2)
        if rate < 2.5 or rate > 8.5:
            out.append(("WARN", "VOICE_PACE", f"scene {i}: {rate:.1f} syllables a second (natural speech is about 4-7.5)"))
        h = s.get("heard")
        if h is not None and h < 0.8:
            miss = ", ".join(s.get("missed") or [])
            out.append(("ERROR" if h < 0.5 else "WARN", "VOICE_WORDS", f"scene {i}: only {h * 100:.0f}% of the script's words were heard in the voice track"
                        + (f"; not heard as written: {miss} (fix the spelling for the voice in \"say\", e.g. \"say\": {{\"{(s.get('missed') or ['word'])[0]}\": \"how it sounds\"}})" if miss else "")))
        if s.get("tail", 0) > 0.5:
            out.append(("INFO", "VOICE_TAIL", f"scene {i}: {s['tail']:.2f} s of silence after the last word in the voice file (trimmed in the render)"))
        lv = _db(voice_x[int(a * sr):int(b * sr)])
        levels.append((i, lv))
        end_part = voice_x[max(0, int(s["end"] * sr) - int(0.04 * sr)):int(s["end"] * sr)]
        if len(end_part) and _db(end_part) > lv - 10:                         # still speaking at full level when the scene's sound ends
            out.append(("ERROR", "VOICE_CUT", f"scene {i}: the voice is cut off mid-sound at the end of the scene (last word chopped)"))
    for (i, x), (j, y) in zip(levels, levels[1:]):
        if abs(x - y) > 6:
            out.append(("WARN", "VOICE_LEVEL", f"scenes {i}->{j}: the voice level jumps by {abs(x - y):.0f} dB"))
    quiet = [_db(voice_x[int(s["words"][-1][2] * sr + 0.1 * sr):int((s["end"]) * sr)]) for s in scenes if s["words"] and s["end"] - s["words"][-1][2] > 0.3]
    if quiet and sorted(quiet)[len(quiet) // 2] > -46:
        out.append(("WARN", "VOICE_HISS", f"noise under the voice is about {sorted(quiet)[len(quiet) // 2]:.0f} dB: audible hiss"))
    return out


def mix(final, sr, voice_x):
    out = []
    import numpy as np
    clip = float(np.mean(np.abs(final) > 0.985))
    if clip > 0.0005:
        out.append(("ERROR", "MIX_CLIP", f"{clip * 100:.2f}% of the samples are clipped: the sound distorts"))
    lvl = _db(final)
    if lvl < -30:
        out.append(("WARN", "MIX_QUIET", f"the whole reel is quiet ({lvl:.0f} dB RMS)"))
    # music against voice is judged in the gaps (GAP_LOUD): separating the two inside the encoded mix is not reliable, so it is not attempted
    return out


def mouth(mouth_env, voice_x, sr, fps=30):
    """does the mascot's mouth follow the loudness of the voice"""
    import numpy as np
    n = min(len(mouth_env), len(voice_x) // int(sr / fps))
    if n < 10:
        return []
    hop = int(sr / fps)
    env = np.array([np.sqrt(np.mean(voice_x[i * hop:(i + 1) * hop] ** 2)) for i in range(n)])
    m = np.array(mouth_env[:n])
    if env.std() < 1e-6 or m.std() < 1e-6:
        return [("WARN", "MOUTH_FLAT", "the mouth does not move or the voice is flat")]
    c = float(np.corrcoef(env, m)[0, 1])
    return [("WARN", "MOUTH_SYNC", f"the mouth follows the voice only weakly (correlation {c:.2f})")] if c < 0.5 else []


def media(mp4):
    """facts from ffprobe / ffmpeg: duration of picture and sound, black frames, loudness"""
    out = []
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(mp4)], capture_output=True, text=True)
    try:
        d = {s["codec_type"]: float(s.get("duration") or 0) for s in json.loads(p.stdout)["streams"]}
    except Exception:
        return [("ERROR", "FILE_BAD", "ffprobe cannot read the file")]
    if "audio" not in d:
        out.append(("ERROR", "NO_AUDIO", "the file has no sound track"))
    elif abs(d.get("video", 0) - d["audio"]) > 0.15:
        out.append(("WARN", "AV_LENGTH", f"picture {d.get('video', 0):.2f} s, sound {d['audio']:.2f} s: they differ"))
    b = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(mp4), "-vf", "blackdetect=d=0.4:pix_th=0.05", "-an", "-f", "null", "-"], capture_output=True, text=True)
    if "black_start" in b.stderr:
        out.append(("WARN", "BLACK_FRAMES", "there are black frames in the picture"))
    e = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(mp4), "-af", "ebur128=peak=true", "-vn", "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", e.stderr)
    if m:
        lufs = float(m[-1])
        if lufs < -23:
            out.append(("WARN", "LOUDNESS_LOW", f"integrated loudness {lufs:.1f} LUFS: quiet next to other reels (they sit around -14)"))
        elif lufs > -9:
            out.append(("WARN", "LOUDNESS_HIGH", f"integrated loudness {lufs:.1f} LUFS: very loud"))
        else:
            out.append(("INFO", "LOUDNESS", f"integrated loudness {lufs:.1f} LUFS"))
    return out


def check(sidecar, final, sr, voice_x, mouth_env=None):
    """all the audio checks on samples already in memory"""
    scenes = sidecar["scenes"]
    res = gaps(final, sr, scenes) + voice(scenes, voice_x, sr) + breaths(scenes, voice_x, sr) + mix(final, sr, voice_x)
    if mouth_env:
        res += mouth(mouth_env, voice_x, sr)
    return res


def run(mp4):
    mp4 = Path(mp4)
    side = json.loads(mp4.with_suffix(".qa.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "final.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-vn", "-ac", "1", "-ar", str(SR), str(wav)], check=True)
        final, sr = read_wav(wav)
    vp = mp4.with_name(mp4.stem + "_voice.wav")
    voice_x, vsr = read_wav(vp) if vp.exists() else (final, sr)
    res = check(side, final, sr, voice_x, side.get("mouth"))
    return media(mp4) + res


def report(res):
    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    for sev, code, msg in sorted(res, key=lambda r: order[r[0]]):
        print(f"{sev:5s} {code:13s} {msg}")
    e = sum(1 for r in res if r[0] == "ERROR")
    w = sum(1 for r in res if r[0] == "WARN")
    print(f"-> {e} error(s), {w} warning(s)" if res else "-> nothing found")
    return e


def selftest():
    """known faults put into synthetic sound: every one must be reported, and a clean reel must pass. Returns (caught, total)"""
    import numpy as np
    sr = SR
    t = lambda s: np.arange(int(s * sr)) / sr
    def tone(sec, db=-18, f=180):
        return (np.sin(2 * np.pi * f * t(sec)) * 10 ** (db / 20) * 1.41).astype("float32")
    def build(gap, gap_sound=None, voice_gain=0.0):
        parts, scenes, pos = [], [], 0.3
        parts.append(np.zeros(int(0.3 * sr), dtype="float32"))
        for i in range(3):
            v = tone(1.6, -18 + voice_gain * i)
            parts.append(v)
            scenes.append({"text": "to jest jedno zdanie testowe", "words": [("to", pos, pos + 0.3), ("jest", pos + 0.3, pos + 0.7), ("jedno", pos + 0.7, pos + 1.1), ("zdanie", pos + 1.1, pos + 1.4), ("testowe", pos + 1.4, pos + 1.6)],
                           "start": pos, "end": pos + 1.6 + min(0.15, gap * 0.5), "heard": 1.0, "tail": 0.1})
            pos += 1.6
            if i < 2:
                g = np.zeros(int(gap * sr), dtype="float32") if gap_sound is None else gap_sound(gap)
                parts.append(g)
                pos += gap
        return np.concatenate(parts), {"scenes": scenes}
    cases = []
    x, s = build(0.3)
    cases.append(("clean reel", x, s, None))
    x, s = build(1.5)
    cases.append(("long gap", x, s, "GAP_LONG"))
    x, s = build(0.4, lambda g: tone(g, -22, 50))
    cases.append(("rumble in the gap", x, s, "GAP_RUMBLE"))
    x, s = build(0.4, lambda g: tone(g, -20, 900))
    cases.append(("music as loud as the voice in the gap", x, s, "GAP_LOUD"))
    x, s = build(0.3, voice_gain=-9)
    cases.append(("voice level jumps", x, s, "VOICE_LEVEL"))
    x, s = build(0.3)
    s["scenes"][1]["heard"] = 0.4
    cases.append(("cut-off sentence", x, s, "VOICE_WORDS"))
    x, s = build(0.3)
    for sc in s["scenes"]:
        sc["end"] = sc["end"] - 0.4                                             # the scene ends while the voice is still going
    cases.append(("last word chopped", x, s, "VOICE_CUT"))
    x, s = build(0.6)
    rng = np.random.default_rng(3)
    word_end = s["scenes"][0]["words"][-1][2]
    k = int((word_end + 0.1) * sr)
    x = x.copy()
    x[k:k + int(0.25 * sr)] += (rng.standard_normal(int(0.25 * sr)) * 10 ** (-34 / 20)).astype("float32")      # a breath right after the first sentence
    s["scenes"][0]["end"] = word_end + 0.45
    cases.append(("breath after a sentence", x, s, "VOICE_BREATH"))
    x, s = build(0.6)
    k = int(s["scenes"][1]["words"][-1][2] * sr)
    x = x.copy()
    x[k:k + int(0.4 * sr)] += tone(0.4, -48, 220)[:int(0.4 * sr)]                       # the voice trails off into a long soft breath
    s["scenes"][1]["end"] = s["scenes"][1]["words"][-1][2] + 0.5
    cases.append(("breathy tail after the last word", x, s, "VOICE_BREATH"))
    x, s = build(0.3)
    x = np.clip(x * 12, -1, 1)
    cases.append(("clipping", x, s, "MIX_CLIP"))
    caught, total = 0, 0
    for name, x, s, want in cases:
        res = check(s, x, sr, x)
        codes = {c for _sev, c, _m in res}
        ok = (want in codes) if want else not any(sev in ("ERROR", "WARN") for sev, _c, _m in res)
        print(f"{'caught' if ok else 'MISSED':6s} {name}" + (f" ({want})" if want else ""))
        total += 1
        caught += ok
    print(f"{caught}/{total} injected faults reported correctly")
    return caught, total


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in a:
        c, t = selftest()
        return 0 if c == t else 1
    if not a:
        print(__doc__)
        return 2
    return 1 if report(run(a[0])) else 0


if __name__ == "__main__":
    sys.exit(main())
