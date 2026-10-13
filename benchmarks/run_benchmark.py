"""Accuracy and speed benchmark on a corpus with a KNOWN reference text (synthetic speech, Windows SAPI voices).

  python benchmarks/run_benchmark.py [--out benchmarks/RESULTS.md] [--work DIR]

Why synthetic: the reference is exactly the script that was spoken, so there is no transcriber's error in the ground truth.
Why it is not enough: synthetic voices are cleaner than people, so absolute WER is optimistic. Read the table for DIFFERENCES between
configurations and for term recall, not as "the tool is 99% accurate on real reels".

Configurations (all use faster-whisper, language auto-detected, beam 5, VAD):
  tiny / small / large-v3 with no prompt = what a plain "download and transcribe" tool does
  large-v3 + prompt                      = reel-intel: per-language glossary + names taken from the caption as initial_prompt
Each configuration is run on clean audio and on audio with pink noise mixed in (about 12 dB below the speech peak).
"""
import json
import re
import statistics as st
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "src"))

CONFIGS = [("tiny", False), ("small", False), ("large-v3", False), ("large-v3", True)]


def norm(t):
    t = t.lower().replace("’", "'")
    t = re.sub(r"[^\w\s']", " ", t)
    return t.split()


def wer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(1, len(r)), len(r)


def term_hit(term, hyp):
    """a term counts when its words appear in order, ignoring case, spacing and punctuation (Meta Ads == meta-ads == MetaAds)"""
    squash = lambda s: re.sub(r"[^\w]", "", s.lower())
    return squash(term) in squash(hyp)


def synth(item, out):
    txt = item["text"].replace("'", "''")
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SelectVoice('{item['voice']}'); $s.SetOutputToWaveFile('{out}'); $s.Speak('{txt}'); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)


def add_noise(clean, noisy):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(clean), "-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.06:r=22050",
                    "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:normalize=0", str(noisy)], check=True)


CLIPSCRIBE = "clipscribe defaults (tiny, CPU, no VAD, no prompt)"


def clipscribe_rows(items, work):
    """What the open-source tool `clipscribe` (v0.1.1) does for TikTok/Instagram audio, reproduced from its source (utils/whisper_backend.py,
    extractor.py): WhisperModel("tiny", device="cpu", compute_type="int8"), model.transcribe(audio, beam_size=5). The package itself was not run."""
    from faster_whisper import WhisperModel
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    rows = []
    for it in items:
        for cond in ("clean", "noisy"):
            audio = work / f"{it['id']}_{cond}.wav"
            t0 = time.time()
            segs, info = model.transcribe(str(audio), beam_size=5)
            hyp = " ".join(s.text.strip() for s in segs)
            sec = time.time() - t0
            w, n = wer(it["text"], hyp)
            rows.append((CLIPSCRIBE, cond, it["lang"], it["id"], w, n, sum(term_hit(x, hyp) for x in it["terms"]), len(it["terms"]), sec, info.duration, info.language == it["lang"], hyp))
    return rows


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    out = Path(args[args.index("--out") + 1]) if "--out" in args else HERE / "RESULTS.md"
    work = Path(args[args.index("--work") + 1]) if "--work" in args else HERE / "_work"
    work.mkdir(parents=True, exist_ok=True)
    items = json.loads((HERE / "corpus.json").read_text(encoding="utf-8"))["items"]
    if "--render-only" in args:                                   # re-render RESULTS.md from a finished run
        out.write_text(render(json.loads((work / "raw_results.json").read_text(encoding="utf-8")), items), encoding="utf-8")
        return

    from reel_intel import common
    if "--add-clipscribe" in args:                                  # append the clipscribe-default row to a finished run and re-render
        rows = json.loads((work / "raw_results.json").read_text(encoding="utf-8"))
        rows = [r for r in rows if r[0] != CLIPSCRIBE] + clipscribe_rows(items, work)
        (work / "raw_results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        out.write_text(render(rows, items), encoding="utf-8")
        print(f"written {out}")
        return
    for it in items:
        for cond in ("clean", "noisy"):
            p = work / f"{it['id']}_{cond}.wav"
            if not p.exists():
                if cond == "clean":
                    synth(it, p)
                else:
                    add_noise(work / f"{it['id']}_clean.wav", p)

    rows = []          # (config, cond, lang, item id, wer, nref, term hits, term total, seconds, audio seconds)
    for model, prompt in CONFIGS:
        common._model = None
        common.CFG["whisper_model"] = model
        common.CFG["language"] = None
        label = f"{model}{' + prompt' if prompt else ''}"
        common.whisper_model()                                   # load once, outside the timing
        for it in items:
            for cond in ("clean", "noisy"):
                audio = work / f"{it['id']}_{cond}.wav"
                t0 = time.time()
                lang, _ = common.detect_language(audio)
                p = common.reel_prompt({"description": it["caption"]}, lang)[0] if prompt else None
                segs, info = common.whisper_model().transcribe(str(audio), language=lang, beam_size=5, vad_filter=True, initial_prompt=p or None)
                hyp = " ".join(s.text.strip() for s in segs)
                sec = time.time() - t0
                w, n = wer(it["text"], hyp)
                hits = sum(term_hit(t, hyp) for t in it["terms"])
                rows.append((label, cond, it["lang"], it["id"], w, n, hits, len(it["terms"]), sec, info.duration, lang == it["lang"], hyp))
        print(f"done {label}", flush=True)

    (work / "raw_results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    out.write_text(render(rows, items), encoding="utf-8")
    print(f"written {out}")


def agg(rs):
    words = sum(r[5] for r in rs)
    errs = sum(r[4] * r[5] for r in rs)
    th, tt = sum(r[6] for r in rs), sum(r[7] for r in rs)
    return errs / words, (th / tt if tt else float("nan")), th, tt


def render(rows, items):
    n_items = len(items)
    from reel_intel import common
    by_id = {it['id']: it for it in items}
    labels = list(dict.fromkeys(r[0] for r in rows))
    L = ["# Benchmark results", "",
         f"Corpus: {n_items} scripts ({sum(1 for r in rows if r[0] == labels[0] and r[1] == 'clean' and r[2] == 'pl')} Polish, "
         f"{sum(1 for r in rows if r[0] == labels[0] and r[1] == 'clean' and r[2] == 'en')} English), spoken by Windows SAPI voices, "
         "reference = the script itself. Clean audio and the same audio with pink noise (about 12 dB below the speech peak). "
         "WER = word error rate after lower-casing and removing punctuation (lower is better); term recall = share of product names / "
         "jargon that appear correctly. **Synthetic speech is cleaner than real people: absolute numbers are optimistic, differences between rows are the point.**", "",
         "| configuration | audio | WER pl | WER en | term recall | language detected right |", "|---|---|---|---|---|---|"]
    for lab in labels:
        for cond in ("clean", "noisy"):
            rs = [r for r in rows if r[0] == lab and r[1] == cond]
            wp = agg([r for r in rs if r[2] == "pl"])[0]
            we = agg([r for r in rs if r[2] == "en"])[0]
            _, rec, th, tt = agg(rs)
            L.append(f"| {lab} | {cond} | {wp:.1%} | {we:.1%} | {rec:.0%} ({th}/{tt}) | {sum(r[10] for r in rs)}/{len(rs)} |")
    L += ["", "### Term recall: terms the default glossary already knows vs names that only the caption supplies", "",
          "| configuration | in glossary | only in caption |", "|---|---|---|"]
    for lab in labels:
        cells = []
        for known in (True, False):
            hit = tot = 0
            for r in rows:
                if r[0] != lab:
                    continue
                it = by_id[r[3]]
                gl = common.glossary_prompt(it["lang"]).lower()
                for term in it["terms"]:
                    if (term.lower() in gl) == known:
                        tot += 1
                        hit += term_hit(term, r[11])
            cells.append(f"{hit / tot:.0%} ({hit}/{tot})" if tot else "-")
        L.append(f"| {lab} | {cells[0]} | {cells[1]} |")
    L += ["", "## Speed", "", "Seconds of processing per clip (language detection + transcription, model already loaded), on this machine "
          "(see `reel-intel doctor`). Clips are 10-20 s of speech, so this is a lower bound for the model's cost, not the full scan time per video (download dominates).", "",
          "| configuration | median s / clip | audio s per clip |", "|---|---|---|"]
    for lab in labels:
        rs = [r for r in rows if r[0] == lab]
        L.append(f"| {lab} | {st.median(r[8] for r in rs):.2f} | {st.median(r[9] for r in rs):.1f} |")
    L += ["", "## Worst clips (large-v3 + prompt)", "", "| clip | audio | WER | heard |", "|---|---|---|---|"]
    best = [r for r in rows if r[0] == labels[-1]]
    for r in sorted(best, key=lambda r: -r[4])[:5]:
        L.append(f"| {r[3]} | {r[1]} | {r[4]:.0%} | {r[11][:140]} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
