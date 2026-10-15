"""music.py - a music bed for a reel, made from nothing but arithmetic (so there is no licence to worry about), and the mix under the voice.

`bed(seconds)` writes a short loop-free electronic track (minor-key pad, bass, kick, hat, a soft pluck line). It is simple on purpose: it sits under a voice.
`mix(voice_wav, music_wav, out_wav)` lowers the music while the voice speaks (side-chain compression, done by ffmpeg).
A track of your own (one you are allowed to use) can replace the bed: pass its path as the script's "music".
"""
import subprocess
import wave
from pathlib import Path

SR = 24000


def _midi(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def bed(seconds, bpm=104, seed=7):
    """float32 mono samples, `seconds` long: Am - F - C - G, two bars each"""
    import numpy as np
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    out = np.zeros(n + SR, dtype="float64")
    beat = 60.0 / bpm
    chords = [(57, (0, 3, 7)), (53, (0, 4, 7)), (60, (0, 4, 7)), (55, (0, 4, 7))]
    clen = 8 * beat

    def add(start, sig):
        a = int(start * SR)
        if a < n:
            out[a:a + len(sig)] += sig[:max(0, len(out) - a)]

    k = 0
    while k * clen < seconds:                                               # pad: a few detuned sines with overtones, slow attack and release
        root, iv = chords[k % 4]
        L = int(clen * SR)
        t = np.arange(L) / SR
        env = np.minimum(1, t / 0.9) * np.minimum(1, (clen - t) / 1.2).clip(0, 1)
        pad = np.zeros(L)
        for i in iv:
            for det in (-0.4, 0.4):
                f = _midi(root + 12 + i) * 2 ** (det / 100)
                pad += np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t) + 0.12 * np.sin(2 * np.pi * 3 * f * t)
        add(k * clen, pad * env * 0.045)
        bt = np.arange(int(beat * 0.5 * SR)) / SR                               # bass: eighth notes on the root
        for j in range(16):
            f = _midi(root - 12)
            note = np.sin(2 * np.pi * f * bt) * np.exp(-bt * 7) + 0.4 * np.sin(2 * np.pi * 2 * f * bt) * np.exp(-bt * 12)
            add(k * clen + j * beat * 0.5, note * (0.16 if j % 2 == 0 else 0.1))
        pl = np.arange(int(beat * 0.5 * SR)) / SR                                # pluck line: chord tones going up and down
        seq = [0, 1, 2, 1, 0, 2, 1, 2]
        for j in range(16):
            f = _midi(root + 24 + iv[seq[j % 8] % len(iv)])
            note = (np.sin(2 * np.pi * f * pl) + 0.4 * np.sin(4 * np.pi * f * pl)) * np.exp(-pl * 9)
            add(k * clen + j * beat * 0.5, note * 0.035)
        k += 1
    kt = np.arange(int(0.22 * SR)) / SR                                          # kick on every beat
    kick = np.sin(2 * np.pi * (85 * kt + 70 * (1 - np.exp(-kt * 40)) / 40)) * np.exp(-kt * 20) * 0.4
    ht = np.arange(int(0.05 * SR)) / SR                                          # hat on the off-beats
    for b in range(int(seconds / beat) + 1):
        add(b * beat, kick)
        hat = np.diff(rng.standard_normal(len(ht) + 1)) * np.exp(-ht * 70) * 0.05
        add(b * beat + beat / 2, hat)
    out = out[:n]
    fade = np.minimum(1, np.arange(n) / (SR * 1.2)) * np.minimum(1, (n - np.arange(n)) / (SR * 1.6))
    out = out * fade
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak * 0.9).astype("float32")


def write_wav(path, x):
    import numpy as np
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("int16").tobytes())


def mix(voice_wav, music, out_wav, volume=0.38):
    """voice + music, the music ducked while the voice speaks; `music` is a wav/mp3 path (looped if shorter than the voice). Returns True on success"""
    fc = (f"[1:a]aresample=24000,highpass=f=120,lowpass=f=9000,volume={volume}[m];[m][0:a]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=600[duck];"
          "[0:a][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(voice_wav), "-stream_loop", "-1", "-i", str(music), "-filter_complex", fc,
                        "-map", "[a]", "-ac", "1", "-ar", "24000", str(out_wav)], capture_output=True, text=True)
    return r.returncode == 0 and Path(out_wav).exists()
