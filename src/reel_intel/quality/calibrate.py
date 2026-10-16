"""calibrate.py - thresholds from DATA instead of from blog posts: measure real recordings, write qa_profile.json.

  reel-intel make calibrate FOLDER... [--reels FOLDER...] [--out qa_profile.json]

Every FOLDER is searched for audio.wav / *.wav / *.mp4 (a folder of TED talks, of reels of the reference accounts, of your own voice). What is measured, per
recording, from the SOUND alone (no transcript needed):
  utterances   stretches of speech between pauses of 0.25 s or more, at least 1.5 s long (the energy based voice activity detector of mix.py)
  pace         syllables a second of every utterance (audio.nuclei, de Jong & Wempe, simplified)
  f0 std       standard deviation of the pitch in semitones of every 3 s window inside the utterances (speech.f0_track, normalised autocorrelation of Boersma)
  loudness     integrated LUFS, loudness range and true peak of the whole file (mix.loudness_report, BS.1770-4); ONLY for the folders given after --reels,
               because the loudness of a conference talk says nothing about what a reel should sound like
and what it sets (only when there are enough measurements; the report says which keys were NOT set and why):
  pace.fast     p98 of the utterance rates + 5%, never above the default (6.8)      pace.slow    p2 - 10%, never above the default (3.2)
  (pace.target is a product choice, not a measurement: the default stays)
  intonation.f0_std_semitones_min   p5 of the pitch spreads of 3 s windows - 15% (a voice flatter than 95% of real sentences is flagged; 3 s because a reel's
                                    sentence is that long and the spread grows with the length of the stretch)
  loudness.target_lufs   median of the reels; tolerance = half the p10-p90 spread, between 2 and 4 LU; `uncertain` is cleared from 10 reels on

Limits, said plainly: real speakers are not the voice the generator makes (a Chatterbox voice may legitimately sit at the edge of the human range); the numbers
are only as good as the folders given (TED talks are English and rehearsed; the reference accounts are the target, not the truth); an utterance the voice
activity detector merges or splits changes the rate by a few percent. Nothing here judges whether a voice is pleasant.
"""
import json
import math
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

from . import audio, mix, profile as _profile, speech

SR = 24000
MIN_UTT_S = 1.5
JOIN_S = 0.25                     # a pause shorter than this is inside an utterance
MIN_RATES = 20                    # utterances needed before pace is calibrated
MIN_F0 = 30                       # 3 s windows needed before intonation is calibrated
WINDOW_S = 3.0                    # the pitch spread depends on how long the stretch is (measured on 4 talks: p10 of the std is 1.44 st at 2 s, 1.67 at 3 s, 1.83 at 4 s),
                                  # so it is measured on windows as long as a reel's sentence, not on whole utterances
FLAT_PERCENTILE = 5               # a voice flatter than 95% of real sentences, minus the margin, is flagged
MIN_REELS = 3                     # recordings needed before loudness is calibrated
SOLID_REELS = 10                  # ... and before `uncertain` is cleared


# ---------------------------------------------------------------- measuring
def decode(path, sr=SR):
    """a file (wav or mp4) as mono float32 at `sr`; (None, sr) when ffmpeg cannot read it"""
    path = Path(path)
    with tempfile.TemporaryDirectory(prefix="calib_") as d:
        wav = Path(d) / "x.wav"
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(wav)], capture_output=True)
        if r.returncode != 0 or not wav.exists():
            return None, sr
        with wave.open(str(wav)) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768.0
    return x, sr


def utterances(x, sr, min_s=MIN_UTT_S, join_s=JOIN_S):
    """[(t0, t1)] of the stretches of speech of at least `min_s`, from the level of the track itself"""
    return [(a, b) for a, b in mix._vad_spans(x, sr, join_s=join_s, min_s=min_s) if b - a >= min_s]


def measure_audio(x, sr, reel=False, with_f0=True):
    """{duration, utt: [{t0, t1, rate, f0_std}], loudness: {integrated, lra, true_peak_db} | None}"""
    spans = utterances(x, sr)
    peaks = audio.nuclei(x, sr)
    st = None
    if with_f0 and spans:
        f0 = speech.f0_track(x, sr)
        st = speech.f0_semitones(f0)
    utt = []
    for a, b in spans:
        u = {"t0": round(a, 3), "t1": round(b, 3), "rate": round(audio.rate_in(peaks, a, b), 3), "f0_std": None}
        if st is not None:
            s = speech.scene_f0_std(st, a, b)
            u["f0_std"] = None if s is None else round(s, 3)
        utt.append(u)
    windows = []
    if st is not None:
        for a, b in spans:
            t = a
            while t + WINDOW_S <= b + 1e-6:
                w = speech.scene_f0_std(st, t, t + WINDOW_S)
                if w is not None:
                    windows.append(round(w, 3))
                t += WINDOW_S
    out = {"duration": round(len(x) / float(sr), 2), "utt": utt, "f0_windows": windows, "loudness": None}
    if reel:
        rep = mix.loudness_report(x, sr, dual_mono=True)
        out["loudness"] = {k: rep.get(k) for k in ("integrated", "lra", "true_peak_db")}
    return out


def find_files(folder):
    """one audio file per recording in a folder: audio.wav if there is one (a data folder of this project), else every wav and mp4 under it"""
    folder = Path(folder)
    if folder.is_file():
        return [folder]
    found = []
    for sub in sorted([folder] + [p for p in folder.iterdir() if p.is_dir()]):
        if (sub / "audio.wav").is_file():
            found.append(sub / "audio.wav")
            continue
        for ext in ("*.mp4", "*.wav"):
            for p in sorted(sub.glob(ext)):
                if not p.stem.endswith("_voice") and not p.name.endswith(".qa.json"):
                    found.append(p)
    return found


def measure_files(paths, reel=False, with_f0=True, log=None):
    out = []
    for p in paths:
        x, sr = decode(p)
        if x is None or len(x) < sr * 3:
            if log:
                log(f"skipped {p}: unreadable or shorter than 3 s")
            continue
        m = measure_audio(x, sr, reel=reel, with_f0=with_f0)
        m.update({"file": str(p), "reel": bool(reel)})
        out.append(m)
        if log:
            rates = [u["rate"] for u in m["utt"]]
            log(f"{p.parent.name + '/' if p.name == 'audio.wav' else ''}{p.name}: {len(rates)} utterances, median {float(np.median(rates)) if rates else 0:.1f} syll/s")
    return out


# ---------------------------------------------------------------- from measurements to thresholds
def pct(values, q):
    v = [float(x) for x in values if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return float(np.percentile(v, q)) if v else None


def spread(values):
    return {"n": len([v for v in values if v is not None]), **{f"p{q}": (None if pct(values, q) is None else round(pct(values, q), 3)) for q in (2, 10, 50, 90, 98)}}


def build_profile(measures, base=None):
    """(profile overlay, report) from a list of measurements of measure_audio (with "reel" and "file" added). The overlay holds ONLY the keys it could
    calibrate; load() lays it over the defaults. The report says what was set, from how many measurements, and what was left alone and why."""
    base = base or _profile.DEFAULT
    rates = [u["rate"] for m in measures for u in m["utt"] if u.get("rate") and 1.0 <= u["rate"] <= 12.0]
    stds = [w for m in measures for w in m.get("f0_windows", []) if w is not None]
    reels = [m for m in measures if m.get("reel") and m.get("loudness") and m["loudness"].get("integrated") is not None]
    over, rep = {}, {"files": len(measures), "set": {}, "not_set": {}, "spread": {"rate": spread(rates), "f0_std": spread(stds)}}
    if len(rates) >= MIN_RATES:
        cal_slow = round(pct(rates, 2) * 0.90, 1)
        cal_fast = round(pct(rates, 98) * 1.05, 1)
        # the data may only TIGHTEN the fast limit and may not raise the slow one: professional speakers talk fast and a person who listened to the generated
        # voice called 6.8+ rushed, so a measured 7.5 does not loosen it; "too slow" is a matter of taste and nobody complained, so 3.9 does not tighten it
        fast = min(cal_fast, float(base.get("pace", {}).get("fast", cal_fast)))
        slow = min(cal_slow, float(base.get("pace", {}).get("slow", cal_slow)))
        over["pace"] = {"fast": fast, "slow": slow}
        rep["set"]["pace"] = {"fast": fast, "slow": slow, "measured_fast": cal_fast, "measured_slow": cal_slow, "from": f"{len(rates)} utterances of {len(measures)} recordings"}
        if base.get("pace", {}).get("burst", 9.0) < fast + 1.5:
            over["pace"]["burst"] = round(fast + 2.2, 1)
    else:
        rep["not_set"]["pace"] = f"only {len(rates)} utterances measured, {MIN_RATES} needed"
    if len(stds) >= MIN_F0:
        lim = round(pct(stds, FLAT_PERCENTILE) * 0.85, 1)
        over["intonation"] = {"f0_std_semitones_min": lim}
        rep["set"]["intonation"] = {"f0_std_semitones_min": lim, "from": f"{len(stds)} windows of {WINDOW_S:g} s"}
    else:
        rep["not_set"]["intonation"] = f"only {len(stds)} windows of {WINDOW_S:g} s with a pitch measured, {MIN_F0} needed"
    if len(reels) >= MIN_REELS:
        lufs = [m["loudness"]["integrated"] for m in reels]
        tol = round(min(4.0, max(2.0, (pct(lufs, 90) - pct(lufs, 10)) / 2.0)), 1)
        over["loudness"] = {"target_lufs": round(float(np.median(lufs)), 1), "tolerance": tol, "uncertain": len(reels) < SOLID_REELS}
        rep["set"]["loudness"] = {**over["loudness"], "from": f"{len(reels)} reels"}
        rep["spread"]["lufs"] = spread(lufs)
    else:
        rep["not_set"]["loudness"] = f"only {len(reels)} reels measured (folders after --reels), {MIN_REELS} needed"
    over["calibration"] = {"files": len(measures), "utterances": len(rates), "reels": len(reels)}
    return over, rep


def write_profile(path, overlay, merge=True):
    """write the overlay to `path` (default: next to profile.py), keeping keys of an earlier file that this run did not set"""
    path = Path(path)
    cur = {}
    if merge and path.is_file():
        try:
            cur = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            cur = {}
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(cur.get(k), dict):
            cur[k].update(v)
        else:
            cur[k] = v
    path.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    return cur


def format_report(rep):
    out = [f"calibration from {rep['files']} recording(s)"]
    for k, v in rep["set"].items():
        out.append(f"  set      {k}: " + ", ".join(f"{a}={b}" for a, b in v.items()))
    for k, v in rep["not_set"].items():
        out.append(f"  NOT set  {k}: {v}")
    sp = rep["spread"]["rate"]
    if sp["n"]:
        out.append(f"  syllables a second: p2 {sp['p2']}, p10 {sp['p10']}, median {sp['p50']}, p90 {sp['p90']}, p98 {sp['p98']} (n={sp['n']})")
    sp = rep["spread"]["f0_std"]
    if sp["n"]:
        out.append(f"  pitch spread of 3 s windows (semitones): p2 {sp['p2']}, p10 {sp['p10']}, median {sp['p50']}, p90 {sp['p90']} (n={sp['n']})")
    return "\n".join(out)


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    out = _profile.profile_path()
    folders, reels, mode = [], [], "speech"
    i = 0
    while i < len(a):
        if a[i] == "--out" and i + 1 < len(a):
            out = Path(a[i + 1])
            i += 2
            continue
        if a[i] == "--reels":
            mode = "reel"
        else:
            (reels if mode == "reel" else folders).append(a[i])
        i += 1
    if not folders and not reels:
        print(__doc__)
        return 2
    measures = []
    for group, is_reel in ((folders, False), (reels, True)):
        for f in group:
            files = find_files(f)
            if not files:
                print(f"nothing to measure in {f}")
            measures += measure_files(files, reel=is_reel, log=print)
    if not measures:
        return 1
    over, rep = build_profile(measures)
    print(format_report(rep))
    write_profile(out, over)
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
