"""Shared helpers: paths, config, glossary, yt-dlp, Whisper. Imported by scan.py, qa.py, report.py."""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

try:
    sys.stdout.reconfigure(encoding="utf-8")                          # Windows consoles (cp1250) crash on emoji otherwise
except Exception:
    pass
CODE = Path(__file__).parent / "data"          # bundled package data (glossary, page template)
WORK = Path(os.environ.get("REEL_DATA", Path.cwd() / "reel-data"))   # all user data lives here, never inside the plugin
DATA = WORK / "data"                                                 # one folder per reel: data/<id>/
DEFAULTS = {"whisper_model": None, "language": None, "workers": 4, "cookies_file": None, "cookies_from_browser": None, "ui_language": "pl"}


def _yaml(name):
    p = WORK / name if (WORK / name).exists() else CODE / name       # a copy in the data folder overrides the default
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


CFG = {**DEFAULTS, **_yaml("config.yaml")} if (WORK / "config.yaml").exists() else dict(DEFAULTS)
if os.environ.get("REEL_UI_LANG") in ("pl", "en"):                   # command line --lang wins over config.yaml
    CFG["ui_language"] = os.environ["REEL_UI_LANG"]
GLOSS = _yaml("glossary.yaml")


def glossary_prompt(lang=None):
    """glossary prompt for a language: `prompt` may be one string or {default, pl, en, ...}"""
    p = GLOSS.get("prompt", "")
    if isinstance(p, dict):
        p = p.get(lang) or p.get("default", "")
    return " ".join(str(p).split())


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def reel_id(url):
    """stable folder id for a video URL. Instagram ids stay as they are (older data); other platforms get a prefix so ids never collide."""
    m = re.search(r"instagram\.com/(?:[\w.]+/)?(?:reel|reels|p)/([\w-]+)", url) or re.search(r"^/?(?:reel|reels|p)/([\w-]+)", url)
    if m:
        return m.group(1)
    for prefix, pat in (("tt", r"tiktok\.com/.*?/video/(\d+)"), ("yt", r"youtube\.com/shorts/([\w-]{6,})"), ("yt", r"youtu\.be/([\w-]{6,})"),
                        ("yt", r"youtube\.com/watch\?(?:.*&)?v=([\w-]{6,})"), ("x", r"(?:twitter|x)\.com/.*?/status/(\d+)"),
                        ("fb", r"facebook\.com/(?:reel/|watch/?\?v=|.*?/videos/)(\d+)"), ("fb", r"fb\.watch/([\w-]+)")):
        m = re.search(pat, url)
        if m:
            return f"{prefix}_{m.group(1)}"
    import hashlib
    return "v_" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:11]


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def ytdlp():
    a = [sys.executable, "-m", "yt_dlp"]
    if CFG["cookies_file"]:
        a += ["--cookies", CFG["cookies_file"]]
    elif CFG["cookies_from_browser"]:
        a += ["--cookies-from-browser", CFG["cookies_from_browser"]]
    return a


def duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)])
    return float(r.stdout.strip())


def parse_views(raw):
    """'1,2 mln' -> 1200000, '40,2 tys.' -> 40200, '9249' -> 9249"""
    m = re.match(r"\s*([\d\s.,]+?)\s*(tys\.?|mln|k|m)?\s*$", raw.lower())
    n = float(m.group(1).replace(" ", "").replace(",", "."))
    return round(n * {"tys": 1e3, "tys.": 1e3, "k": 1e3, "mln": 1e6, "m": 1e6, None: 1}[m.group(2)])


def views_table():
    p = WORK / "views.csv"
    if not p.exists():
        return {}
    return {l.split(",")[0]: float(l.split(",")[1]) for l in p.read_text(encoding="utf-8").splitlines()[1:] if "," in l and l.split(",")[1]}


_model = None


def whisper_model():
    global _model
    if _model is None:
        import ctranslate2
        for sub in ("cublas", "cudnn", "cuda_nvrtc"):                # pip-installed CUDA libs on Windows
            d = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / sub / "bin"
            if d.exists():
                os.add_dll_directory(str(d))
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
        gpu = ctranslate2.get_cuda_device_count() > 0
        from faster_whisper import WhisperModel
        _model = WhisperModel(CFG["whisper_model"] or ("large-v3" if gpu else "small"),
                              device="cuda" if gpu else "cpu", compute_type="float16" if gpu else "int8")
    return _model


def visual_fields(f):
    """the non-transcript signals of one video, as keys the feature table understands (None when `reel-intel visuals` has not run for it)"""
    v = f.get("_visual")                                           # set by `compare`, which reads several data folders
    p = DATA / str(f.get("id")) / "visual.json"
    if v is None and p.exists():
        try:
            v = jload(p)
        except Exception:
            v = {}
    v = v or {}
    speech = f.get("word_count", 0) >= 10 and f.get("speech_reliable", True)
    return {"wpm": f.get("words_per_min") if speech else None, "cuts10": v.get("cuts_per_10s"), "face3": v.get("face_first_3s"),
            "text2": v.get("text_first_2s"), "burned": v.get("burned_captions")}


def caption_names(caption):
    """Proper names / keywords from a caption: @handles, ALL-CAPS, mixed-case (ChatGPT), words in quotes,
    and capitalised words that do not start a sentence. These are exactly the words Whisper mishears."""
    names = []
    for m in re.finditer(r"@(\w+)|[„\"“]([^”\"„“]{2,25})[”\"“]|\b([A-ZĄĆĘŁŃÓŚŹŻ][\w-]{2,})\b", caption):
        t = m.group(1) or m.group(2) or m.group(3)
        if m.group(3):
            before = caption[:m.start()].rstrip(" \t")[-1:]
            sentence_start = before in ("", ".", "!", "?", "\n", "…", ":") or caption[:m.start()].endswith("\n")
            if sentence_start and not (t.isupper() or re.search(r"[a-ząćęłńóśźż][A-Z]", t)):
                continue
        if t.lower() not in {n.lower() for n in names}:
            names.append(t)
    return names[:40]


def reel_prompt(meta, lang=None):
    """glossary (for this language) + names from this reel's caption -> (prompt, hash). The hash is stored in transcript.json and re-checked by qa."""
    extra = " ".join(caption_names((meta or {}).get("description") or ""))
    p = (glossary_prompt(lang) + " " + extra).strip()
    return p, hashlib.md5(p.encode("utf-8")).hexdigest()[:8]


_STOP = {"en": set("the and is to of in you that it for with on this are was be your how what why not have from at as but they their there when who can will just about".split()),
         "pl": set("i w na się nie to że z do jak jest co dla czy po ale który ten są tak już tylko jeśli oraz przez bardzo może ma mi ci go".split())}


def caption_language(text):
    """"en" or "pl" when a caption clearly is, else None (hashtags and names alone say nothing)"""
    words = re.findall(r"[^\W\d_]+", (text or "").lower())
    hits = {l: sum(w in s for w in words) for l, s in _STOP.items()}
    hits["pl"] += len(re.findall(r"[ąćęłńóśźż]", (text or "").lower())) // 2
    for l, o in (("en", "pl"), ("pl", "en")):
        if hits[l] >= 3 and hits[l] >= 2 * hits[o]:
            return l
    return None


def detect_language(audio, caption=None):
    """language + probability from the first 30 s, detected BEFORE transcribing so the glossary prompt cannot bias it.
    Whisper sometimes hears English with a regional accent as Welsh, or a short clip as some rare language, so two checks follow:
    unsure (< 80%) -> prefer the account's language / English / Polish if the model gives it at least 10%;
    sure but not certain (< 95%) and the caption is clearly English or Polish -> that language, if the model gives it at least 10%."""
    from faster_whisper.audio import decode_audio
    lang, prob, allp = whisper_model().detect_language(decode_audio(str(audio), sampling_rate=16000))
    probs = dict(allp)
    if prob < 0.8:
        prefs = list(CFG.get("languages") or []) + [l for l in [account_language()] if l] + ["en", "pl"]    # en/pl: the languages this tool is tuned for
        best = max(prefs, key=lambda l: probs.get(l, 0), default=None)
        if best and best != lang and probs.get(best, 0) >= 0.10:
            lang, prob = best, probs[best]
    cl = caption_language(caption)
    if cl and cl != lang and prob < 0.95 and probs.get(cl, 0) >= 0.10:
        lang, prob = cl, probs[cl]
    return lang, float(prob)


_ACCOUNT_LANG = {}


def account_language():
    """the language most of the transcripts already in the data folder are in (needs 5 reels and a 60% share), else None"""
    key = str(DATA)
    if key not in _ACCOUNT_LANG:
        langs = []
        for p in sorted(DATA.glob("*/transcript.json"))[:40]:
            try:
                t = jload(p)
                if t.get("language_probability", 0) >= 0.8:
                    langs.append(t["language"])
            except Exception:
                pass
        top = max(set(langs), key=langs.count) if langs else None
        _ACCOUNT_LANG[key] = top if top and len(langs) >= 5 and langs.count(top) / len(langs) >= 0.6 else None
    return _ACCOUNT_LANG[key]


def recover_tail(audio, segments, duration, lang, prompt):
    """The voice-activity filter sometimes drops real speech that sits over music (seen on a NASA Short: ~20 s lost). When the transcript
    ends more than 6 s before the audio does, transcribe again without the filter and keep only segments after the gap that look like speech
    (low no-speech probability, decent confidence). Sign-offs Whisper invents over silence ("Thanks for watching") fail these tests."""
    if not segments or duration - segments[-1]["end"] <= 6:
        return []
    end = segments[-1]["end"]
    segs, _ = whisper_model().transcribe(str(audio), word_timestamps=True, vad_filter=False, language=lang, beam_size=5,
                                         condition_on_previous_text=False, initial_prompt=prompt or None)
    out = []
    for s in segs:
        if s.start >= end + 1 and s.no_speech_prob < 0.5 and s.avg_logprob > -1.0 and s.words:
            out.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
                        "words": [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2), "p": round(w.probability, 2)} for w in s.words]})
    return out


def word_holes(segments, min_gap=6.0):
    """(segment index, word index, gap start, gap end) where one segment has no words for more than min_gap seconds: speech the model skipped
    (seen on a NASA Short: "We have" at 47 s, "a lot of people" at 73 s, and ~60 spoken words in between missing). Gaps BETWEEN segments are
    the voice filter's silences and are normal."""
    out = []
    for si, s in enumerate(segments):
        w = s.get("words") or []
        for wi in range(len(w) - 1):
            if w[wi + 1]["s"] - w[wi]["e"] > min_gap:
                out.append((si, wi, w[wi]["e"], w[wi + 1]["s"]))
    return out


def recover_holes(audio, segments, lang, prompt):
    """Re-transcribe each hole on its own (cut out with ffmpeg, no voice filter, no carry-over from earlier text) and splice what looks like speech
    back in, splitting the segment at the hole so the text stays in order. Returns the new segment list and the number of recovered segments."""
    holes = word_holes(segments)
    if not holes:
        return segments, 0
    import subprocess
    import tempfile
    added = 0
    out = [dict(s) for s in segments]
    for si, wi, a, b in sorted(holes, key=lambda h: (-h[0], -h[1])):            # from the back so indexes stay valid
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "hole.wav"
            lo = max(a - 0.3, 0)
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{lo:.2f}", "-t", f"{b - lo + 0.3:.2f}", "-i", str(audio), "-ac", "1", "-ar", "16000", str(clip)],
                               capture_output=True)
            if r.returncode or not clip.exists():
                continue
            segs, _ = whisper_model().transcribe(str(clip), word_timestamps=True, vad_filter=False, language=lang, beam_size=5,
                                                 condition_on_previous_text=False, initial_prompt=prompt or None)
            new = []
            for s in segs:
                if s.no_speech_prob < 0.5 and s.avg_logprob > -1.0 and s.words:
                    ws = [{"w": w.word.strip(), "s": round(w.start + lo, 2), "e": round(w.end + lo, 2), "p": round(w.probability, 2)} for w in s.words
                          if a - 0.05 <= w.start + lo <= b + 0.05]
                    if ws:
                        new.append({"start": ws[0]["s"], "end": ws[-1]["e"], "text": " ".join(x["w"] for x in ws), "words": ws})
        run, kept = 0, []                                             # a recovered stretch that repeats one phrase is Whisper looping on music: keep one copy
        for n_ in new:
            key = re.sub(r"\W+", " ", n_["text"].lower()).strip()
            run = run + 1 if kept and key == re.sub(r"\W+", " ", kept[-1]["text"].lower()).strip() else 0
            if run < 2:
                kept.append(n_)
        new = kept
        if not new:
            continue
        seg = out[si]
        w = seg["words"]
        first, rest = w[:wi + 1], w[wi + 1:]
        pieces = [{"start": seg["start"], "end": first[-1]["e"], "text": " ".join(x["w"] for x in first), "words": first}] + new +                  [{"start": rest[0]["s"], "end": seg["end"], "text": " ".join(x["w"] for x in rest), "words": rest}]
        out[si:si + 1] = pieces
        added += len(new)
    return out, added


def transcribe(audio, d, meta=None):
    """Whisper with the language's glossary + caption names as initial_prompt. Cached in d/transcript.json."""
    out = d / "transcript.json"
    if out.exists():
        return jload(out)
    lang, lprob = (CFG["language"], 1.0) if CFG["language"] else detect_language(audio, (meta or {}).get("description"))
    prompt, phash = reel_prompt(meta, lang)
    segs, info = whisper_model().transcribe(str(audio), word_timestamps=True, vad_filter=True,
                                            language=lang, beam_size=5, initial_prompt=prompt or None)
    pack = lambda s: {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
                      "words": [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2), "p": round(w.probability, 2)}
                                for w in (s.words or [])]}
    segments = [pack(s) for s in segs]
    tr = {"prompt_hash": phash, "language": lang, "language_probability": round(lprob, 3),
          "duration": round(info.duration, 2), "segments": segments}
    recovered = recover_tail(audio, segments, info.duration, lang, prompt)
    tr["segments"], n_holes = recover_holes(audio, tr["segments"], lang, prompt)
    if n_holes:
        tr["recovered_hole_segments"] = n_holes
    tr["holes_checked"] = True
    if segments and info.duration - segments[-1]["end"] > 6:
        tr["tail_checked"] = True                                  # qa: the gap was re-examined without the voice filter
    if recovered:
        tr["segments"] += recovered
        tr["recovered_tail_segments"] = len(recovered)
    out.write_text(json.dumps(tr, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "transcript.md").write_text("\n".join(f"[{s['start']:6.1f}-{s['end']:6.1f}] {s['text']}" for s in tr["segments"]), encoding="utf-8")
    return tr


def apply_fixes(text, lang=None):
    """Glossary fixes for DISPLAY text only (raw transcript.json is never edited). A fix with `lang:` applies only to that language.
    Returns (text, [log])."""
    log = []
    for fx in GLOSS.get("fixes", []):
        if fx.get("lang") and lang and fx["lang"] != lang:
            continue
        text, n = re.subn(fx["wrong"], fx["right"], text, flags=re.I)
        if n:
            log.append(f"{fx['wrong']} -> {fx['right']} x{n}")
    return text, log


# ---- Polish diacritics for OCR text ---------------------------------------------------------------------------------
_VOCAB = None
_PL = str.maketrans("ąćęłńóśźż", "acelnoszz")


def diacritic_vocab():
    """folded word -> {real forms: count}, built from captions and transcripts of this data folder (the profile's own vocabulary)"""
    global _VOCAB
    if _VOCAB is None:
        from collections import Counter, defaultdict
        v = defaultdict(Counter)
        for d in (DATA.iterdir() if DATA.exists() else []):
            texts = []
            if (d / "features.json").exists():
                texts.append(jload(d / "features.json").get("caption") or "")
            if (d / "transcript.json").exists():
                texts += [seg["text"] for seg in jload(d / "transcript.json")["segments"]]
            for t in texts:
                for w in re.findall(r"[^\W\d_]+", t):
                    v[w.lower().translate(_PL)][w.lower()] += 1
        for w in (Path(__file__).parent / "data" / "pl_words.txt").read_text(encoding="utf-8").splitlines():     # weak built-in prior (counts as 3 uses)
            w = w.strip().lower()
            if w and not w.startswith("#") and w != w.translate(_PL):
                v[w.translate(_PL)][w] += 3
        _VOCAB = v
    return _VOCAB


_SUFFIX = [(r"(?<=.{3})ajacych$", "ających"), (r"(?<=.{3})ajacego$", "ającego"), (r"(?<=.{3})ajacym$", "ającym"), (r"(?<=.{3})ajacy$", "ający"),
           (r"(?<=.{3})ajaca$", "ająca"), (r"(?<=.{3})ajace$", "ające"), (r"(?<=.{3})ajac$", "ając"),
           (r"(?<=.{3})osc$", "ość"), (r"(?<=.{3})oscia$", "ością"), (r"(?<=.{2})([iya])(l)(em|am|ismy|ysmy|es|as)$", None),
           (r"(?<=.{3})[a]c$", "ać"), (r"(?<=.{3})ic$", "ić")]


def _by_suffix(w):
    """words the vocabulary does not know: endings that are almost never written without their accent ("przygotowac" -> "przygotować",
    "nauczylem" -> "nauczyłem", "zaczynajacych" -> "zaczynających"). Longer words only; anything else is left alone."""
    lw = w.lower()
    if len(lw) < 6:
        return w
    for rx, rep in _SUFFIX:
        m = re.search(rx, lw)
        if not m:
            continue
        new = lw[:m.start()] + (m.group(1) + "ł" + m.group(3) if rep is None else rep)
        return new.upper() if w.isupper() else new.capitalize() if w[0].isupper() else new
    return w


def restore_diacritics(text, lang, vocab=None):
    """OCR models often drop Polish letters ("moze"). Restore a word only if the profile uses the accented form at least twice as often as
    the plain one (so "ze"/"że" and "te"/"tę" are decided by real usage). Other languages and unknown words are left untouched."""
    if lang != "pl" or not text:
        return text
    v = diacritic_vocab() if vocab is None else vocab

    def fix(m):
        w = m.group(0)
        if w.lower() != w.lower().translate(_PL):                         # already has Polish letters
            return w
        forms = v.get(w.lower())
        if not forms:
            return _by_suffix(w)
        best, n = forms.most_common(1)[0]
        if best == w.lower() or best == best.translate(_PL) or n < 2 * max(forms.get(w.lower(), 0), 1):
            return w
        return best.upper() if w.isupper() and len(w) > 1 else best.capitalize() if w[0].isupper() else best
    return re.sub(r"[^\W\d_]+", fix, text)


def near_duplicate(a, b, cutoff=0.72):
    """OCR returns one overlay in several garbled variants across frames; keep only the first"""
    import difflib
    a, b = a.lower().strip(), b.lower().strip()
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= cutoff or (len(a) > 6 and (a in b or b in a))
