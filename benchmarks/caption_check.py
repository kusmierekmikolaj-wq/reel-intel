"""Accuracy on REAL speakers against HUMAN-MADE subtitles (YouTube videos whose uploader supplied a caption track, not auto-generated).

  python benchmarks/caption_check.py FOLDER [FOLDER ...] [--out benchmarks/CAPTION_RESULTS.md]

Each FOLDER is a data folder. For every YouTube video there whose metadata lists an uploaded (manual) subtitle track in the video's language,
the track is downloaded and compared with the tool's transcript. This is a real-speaker measurement with a human reference, but the reference is
NOT a verbatim transcript: subtitle editors drop fillers ("um", "you know"), repetitions and false starts, merge sentences, and spell numbers
their own way. So the WER here is an UPPER bound on the true error; the gap to the real value is unknown, and the list of worst videos shows
what kind of difference it is. Only numbers are published, never the subtitle texts.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from make_wer_pack import wer  # noqa: E402  (errors, reference words; same normalisation as the other benchmarks)


def vtt_text(p):
    out, last = [], None
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or "-->" in line or re.fullmatch(r"\d+", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\[[^\]]*\]|\([^)]*\)|♪[^♪]*♪|>>", " ", line)          # [Music], (applause), speaker marks
        line = re.sub(r"\s+", " ", line).strip()
        if line and line != last:
            out.append(line)
            last = line
    return " ".join(out)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    out = Path(args[args.index("--out") + 1]) if "--out" in args else Path(__file__).parent / "CAPTION_RESULTS.md"
    if "--out" in args:
        i = args.index("--out")
        del args[i:i + 2]
    from reel_intel.common import apply_fixes, ytdlp
    rows = []
    tmp = Path(tempfile.mkdtemp(prefix="captions_"))
    for folder in args:
        for mp in sorted(Path(folder, "data").glob("yt_*/meta.json")):
            meta = json.loads(mp.read_text(encoding="utf-8"))
            d = mp.parent
            if not (d / "features.json").exists():
                continue
            f = json.loads((d / "features.json").read_text(encoding="utf-8"))
            tr = json.loads((d / "transcript.json").read_text(encoding="utf-8"))
            lang = tr["language"]
            track = (meta.get("subtitles") or {}).get(lang) or []
            # yt-dlp lists some AUTOMATIC captions under "subtitles"; their URL carries caps=asr. A human reference must not be one of those.
            if not track or any("caps=asr" in (x.get("url") or "") for x in track) or f.get("word_count", 0) < 30 or not f.get("speech_reliable"):
                continue
            base = tmp / f["id"]
            subprocess.run(ytdlp() + ["--skip-download", "--write-subs", "--sub-langs", lang, "--sub-format", "vtt", "--no-warnings", "-o", str(base), f["url"]],
                           capture_output=True, text=True)
            vtts = list(tmp.glob(f"{f['id']}*.vtt"))
            if not vtts:
                continue
            ref = vtt_text(vtts[0])
            hyp = " ".join(apply_fixes(s["text"], lang)[0] for s in tr["segments"])
            e, n = wer(ref, hyp)
            rows.append((f["id"], lang, f["word_count"], n, e / max(n, 1)))
    if not rows:
        print("no video with an uploaded subtitle track found")
        return
    tot_e = sum(r[4] * r[3] for r in rows)
    tot_n = sum(r[3] for r in rows)
    srt = sorted(r[4] for r in rows)
    L = ["# Real speakers vs human-made subtitles", "",
         f"{len(rows)} YouTube videos with an uploaded (not automatic) subtitle track, {tot_n} reference words. WER of the tool's transcript against the subtitles: "
         f"**{tot_e / tot_n:.1%} overall**, median per video {srt[len(srt) // 2]:.1%}, best {srt[0]:.1%}, worst {srt[-1]:.1%}.", "",
         "Subtitles are edited text, not verbatim (fillers, repetitions and false starts are usually dropped, numbers are written their own way), so this is an "
         "**upper bound** on the true error. Languages: " + ", ".join(sorted({r[1] for r in rows})) + ".", "",
         "| video | words (tool) | words (subtitles) | WER |", "|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: r[4]):
        L.append(f"| {r[0]} | {r[2]} | {r[3]} | {r[4]:.1%} |")
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:6]))


if __name__ == "__main__":
    main()
