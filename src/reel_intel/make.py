"""make.py - from the data of scanned videos to a NEW short vertical video (a reel): brief -> script (rolka.json) -> check -> render.

  reel-intel make brief            BRIEF_ROLKI.md / REEL_BRIEF.md: what the data says works + the exact JSON an agent (Claude, Jarvis) must return
  reel-intel make check FILE       is the script usable (length, hook, nothing copied from the scanned videos)
  reel-intel make qa FILE.mp4      checks a rendered reel (voice, sound, music, picture, captions, the file) and gives a verdict: gotowe / z uwagami / niegotowe
                                   (exit code 0 / 10 / 1); --selftest puts known faults into synthetic reels and shows that each one is found
  reel-intel make render FILE      FILE.mp4 (1080x1920, H.264 + AAC) + FILE_opis.txt + FILE_kontrola.md, voice-over + animated words, all on this computer:
                                   the reel is checked, what can be repaired is repaired and checked again (--rounds N, default 3; --no-check; --quick)
                                   animated version needs `npm install` in studio/ (Remotion); otherwise a simpler renderer runs (--simple forces it)
  reel-intel make calibrate DIR... [--reels DIR...]   thresholds from real recordings -> quality/qa_profile.json
  reel-intel make note FILE.mp4 SECONDS KIND [text]   write down what you heard (oddech, tempo, szum, ucięte ...); reel-intel make learn tunes the checker to it

The tool never writes the script itself (no API keys): an agent or a person does. It also never reuses another account's frames or speech:
`check` warns when a script repeats a run of words from a scanned video. Nothing is published anywhere.
"""
import difflib
import json
import math
import os
import re
import shutil
import statistics as st
import subprocess
import uuid
import sys
import tempfile
import wave
from pathlib import Path

from .common import CFG, DATA, WORK, jload

W, H, FPS = 1080, 1920, 30
NAVY, PAPER, SIGNAL, MUTE, LINE = (10, 23, 51), (242, 238, 227), (255, 194, 75), (159, 176, 214), (143, 180, 255)
WPS = 2.4                                               # spoken words per second, same constant the script scorer uses
GAP = 0.28                                              # pause between scenes, seconds

SCHEMA = """{
  "title": "short working title",
  "lang": "pl" | "en",
  "scenes": [
    {"text": "one spoken sentence (the voice-over)", "screen": "2-6 big words shown on screen", "emphasis": ["optional", "words"]}
  ],
  "cta": "one closing sentence that tells the viewer exactly what to do (comment, save, follow)",
  "caption": "post caption, 1-3 sentences",
  "hashtags": ["#tag", "#tag"]
}"""

BRIEF = {
    "pl": {"file": "BRIEF_ROLKI.md", "h1": "Brief: napisz scenariusz nowej rolki",
           "rules": ["Długość mówionego tekstu 20-40 sekund (ok. 50-95 słów razem z CTA).",
                     "Pierwsze zdanie to hook (najwyżej 14 słów), mówi widzowi, co zyska, w pierwszych 3 sekundach.",
                     "Jeden pomysł na rolkę. 4-8 scen, jedno krótkie zdanie na scenę.",
                     "Na ekranie tylko 2-6 słów na scenę (hasło), nie przepisuj zdania lektora.",
                     "Pisz od zera własnymi słowami. Nie cytuj ani nie parafrazuj blisko cudzej mowy z danych (to tylko źródło wzorców); `make check` ostrzeże o powtórzeniach.",
                     "Nie obiecuj niczego, czego dane nie pokazują. Nazwy skilli i narzędzi podawaj tylko takie, które widać w sekcji „Z ekranów”.",
                     "Zakończenie (cta) ma skłonić do wysłania rolki komuś albo do komentarza ze słowem-kluczem (np. „napisz TEST”): wysłania i komentarze liczą się dla zasięgu bardziej niż polubienia.",
                     "Opis (caption): słowa kluczowe tematu w pierwszych zdaniach; hashtagów 3-5, bez masy tagów.",
                     "Rolka jest oznaczona jako stworzona z użyciem AI (maskotka i lektor są syntetyczne): nie udawaj człowieka."],
           "data": "Co pokazują dane", "hooks": "Typy hooków i mediana wyświetleń", "feat": "Cechy wyraźnie powiązane z wyświetleniami (99% przedział)",
           "names": "Z ekranów (do wykorzystania jako temat, nie do przepisywania)", "none": "brak", "out": "Format odpowiedzi",
           "outtxt": "Odpowiedz wyłącznie JSON-em w tym kształcie, zapisz do pliku `rolka.json` w folderze `rolki`:", "next": "Potem: `reel-intel make check rolki\\rolka.json` i `reel-intel make render rolki\\rolka.json`.",
           "src": "Pełny materiał do wglądu: `DLA_AI.md` (mowa, tekst z ekranu, opisy)."},
    "en": {"file": "REEL_BRIEF.md", "h1": "Brief: write the script of a new reel",
           "rules": ["Spoken text of 20-40 seconds (about 50-95 words including the call to action).",
                     "The first sentence is the hook (14 words at most) and tells the viewer what they gain within 3 seconds.",
                     "One idea per reel. 4-8 scenes, one short sentence each.",
                     "Only 2-6 words on screen per scene (a slogan); do not repeat the voice-over sentence.",
                     "Write from scratch in your own words. Do not quote or closely paraphrase other people's speech from the data (it is a source of patterns only); `make check` warns about repeats.",
                     "Promise nothing the data does not show. Name only skills and tools that appear in the \"From the screens\" section.",
                     "The call to action (cta) should make people send the reel to someone or comment a keyword (e.g. \"comment TEST\"): sends and comments count for reach more than likes.",
                     "Caption: the topic's keywords in the first sentences; 3-5 hashtags, not a wall of tags.",
                     "The reel is labelled as made with AI (the mascot and the voice are synthetic): do not pretend to be a person."],
           "data": "What the data shows", "hooks": "Hook types and median views", "feat": "Features clearly tied to views (99% interval)",
           "names": "From the screens (use as a topic, do not copy)", "none": "none", "out": "Answer format",
           "outtxt": "Answer with JSON only, in this shape, saved as `rolka.json` in the `rolki` folder:", "next": "Then: `reel-intel make check rolki\\rolka.json` and `reel-intel make render rolki\\rolka.json`.",
           "src": "Full material to read: `FOR_AI.md` (speech, on-screen text, captions)."},
}


def _lang():
    return "pl" if CFG.get("ui_language") == "pl" else "en"


def brief():
    """BRIEF_ROLKI.md / REEL_BRIEF.md in the data folder: patterns from the scanned videos + the answer format"""
    from . import analysis, report, score
    K = BRIEF[_lang()]
    F = report.with_views(report.load())
    L = [f"# {K['h1']}", "", *[f"- {r}" for r in K["rules"]], "", f"## {K['data']}", ""]
    by = {}
    for f in F.values():
        h = report.hook_type(f.get("hook_spoken"), f.get("language")) if f.get("speech_reliable", True) and f.get("word_count", 0) >= 10 else None
        if h:
            by.setdefault(h, []).append(f["views"])
    L += [f"**{K['hooks']}:**", ""] + ([f"- {h}: n={len(v)}, {st.median(v):,.0f}" + ("" if len(v) >= 5 else " (n<5)") for h, v in sorted(by.items(), key=lambda kv: -st.median(kv[1]))] or [f"- {K['none']}"]) + [""]
    reels, _docs = score.load_corpus()
    clear = [r for r in analysis.feature_table(reels, _lang()) if r["status"] == "clear"]
    L += [f"**{K['feat']}:**", ""] + ([f"- {r['label']}: {r['ratio']:.1f}x ({r['lo']:.1f}-{r['hi']:.1f}), n={r['n_yes']}/{r['n_no']}" for r in clear] or [f"- {K['none']}"]) + [""]
    names = []
    for d in DATA.iterdir():
        p = d / "screen_text.json"
        if p.exists():
            from . import screenmine
            m = screenmine.mine([(x["t"], x["text"]) for x in jload(p)["lines"]])
            names += [x["token"] for x in m["tokens"]] + [r["repo"] for r in m["repos"]]
    seen = list(dict.fromkeys(names))[:40]
    L += [f"**{K['names']}:** " + (", ".join(f"`{n}`" for n in seen) or K["none"]), "", K["src"], "", f"## {K['out']}", "", K["outtxt"], "", "```json", SCHEMA, "```", "", K["next"], ""]
    (WORK / K["file"]).write_text("\n".join(L), encoding="utf-8")
    (WORK / "rolki").mkdir(exist_ok=True)
    print(f"{K['file']}: {len(by)} hook types, {len(clear)} clear features, {len(seen)} names from screens")


def load_script(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def spoken(script):
    return [s["text"].strip() for s in script.get("scenes", [])] + ([script["cta"].strip()] if script.get("cta") else [])


def _tokens(t):
    return re.findall(r"[^\W_]+", t.lower())


def check(script):
    """[(severity, message)]: ERROR = do not render, WARN = look at it"""
    out = []
    if script.get("lang") not in ("pl", "en"):
        out.append(("ERROR", "lang must be \"pl\" or \"en\""))
    scenes = script.get("scenes") or []
    if not 3 <= len(scenes) <= 12:
        out.append(("ERROR", f"{len(scenes)} scenes: use 3-12"))
    for i, s in enumerate(scenes, 1):
        if not str(s.get("text", "")).strip():
            out.append(("ERROR", f"scene {i} has no text"))
        if len(str(s.get("screen", "")).split()) > 8:
            out.append(("WARN", f"scene {i}: the on-screen text has more than 8 words"))
    lines = spoken(script)
    words = sum(len(l.split()) for l in lines)
    secs = words / WPS
    if secs > 75:
        out.append(("ERROR", f"about {secs:.0f} s of speech: too long for a reel (aim at 20-40 s)"))
    elif secs > 45:
        out.append(("WARN", f"about {secs:.0f} s of speech: long (aim at 20-40 s)"))
    elif secs < 8:
        out.append(("WARN", f"about {secs:.0f} s of speech: very short"))
    if lines and len(lines[0].split()) > 14:
        out.append(("WARN", "the hook (first sentence) has more than 14 words"))
    # nothing copied: a run of 6 words that also occurs in a scanned video's transcript
    grams = set()
    if DATA.exists():
        for d in DATA.iterdir():
            p = d / "transcript.json"
            if p.exists():
                try:
                    tk = _tokens(" ".join(s["text"] for s in jload(p)["segments"]))
                except Exception:
                    continue
                grams.update(tuple(tk[i:i + 6]) for i in range(len(tk) - 5))
    for i, l in enumerate(lines, 1):
        tk = _tokens(l)
        if any(tuple(tk[j:j + 6]) in grams for j in range(len(tk) - 5)):
            out.append(("WARN", f"line {i} repeats 6 words in a row from a scanned video: rewrite it in your own words"))
    return out


# ---------------------------------------------------------------- voice

VOICES_DIR = Path.home() / ".reel-intel" / "voices"
PIPER_MODELS = {"pl": "pl_PL-darkman-medium.onnx", "en": "en_US-ryan-high.onnx"}               # male voices
EDGE_VOICES = {"pl": "pl-PL-MarekNeural", "en": "en-US-GuyNeural"}                          # male voices


def _piper_model(lang):
    p = Path(os.environ.get("REEL_PIPER_MODEL") or VOICES_DIR / PIPER_MODELS.get(lang, ""))
    return p if p.is_file() else None


TTS_PYTHON = Path(os.environ.get("REEL_TTS_PYTHON") or Path.home() / ".reel-intel" / "venv-tts" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"))


def voice_sample(script):
    """the reference recording for a cloned voice: script["voice_sample"], $REEL_VOICE_SAMPLE or ~/.reel-intel/voices/moj_glos.wav"""
    if script.get("voice_sample") == "none":                                  # the model's own voice, no cloning
        return None
    p = Path(script.get("voice_sample") or os.environ.get("REEL_VOICE_SAMPLE") or VOICES_DIR / "moj_glos.wav")
    return p if p.is_file() else None


def chatterbox_ready(script):
    return TTS_PYTHON.is_file() and voice_sample(script) is not None


def new_recipe():
    """what the repair loop has changed so far, on top of the script (JSON-able; scene numbers are 0-based): retake = how many times a scene was recorded again,
    made = how many of those recordings exist, respell = {scene: {word: spelling}}, speed = extra slowing factor, cap = longest last word (s), cut = how much harder
    the end of the sentence is trimmed (dB), level = bring every scene to the same level, music_mult / hp = the music's volume factor and high-pass (Hz)"""
    return {"retake": {}, "made": {}, "respell": {}, "speed": {}, "cap": {}, "cut": {}, "level": False, "music_mult": 1.0, "hp": 150}


def say_for(script, recipe, i):
    """the respelling table for scene i: the script's own, then what the repair loop found for that scene"""
    return {**(script.get("say") or {}), **((recipe or {}).get("respell", {}).get(i) or {})}


def chatterbox_batch(script, lines, tmp, only=None, recipe=None):
    """the sentences `only` (default: all) in ONE run of the Chatterbox environment (the model loads once), `takes` recordings of each; the best heard one becomes
    tmp/s<i>.wav and is kept as tmp/raw_<i>.wav (the pristine take every later round starts from). A scene recorded again (recipe["retake"]) gets other seeds.
    Returns True when every file asked for exists"""
    takes = int(script.get("takes", 4))
    recipe = recipe if recipe is not None else new_recipe()
    idx = sorted(range(len(lines)) if only is None else only)
    spoken = [spoken_form(text, script["lang"], say_for(script, recipe, i)) for i, text in enumerate(lines)]
    for i in idx:
        Path(tmp, f"s{i}.wav").unlink(missing_ok=True)                           # what exists afterwards is new
    seed = 1000 + 100 * max([recipe["retake"].get(i, 0) for i in idx] or [0])
    spec = {"lang": script["lang"], "ref": str(voice_sample(script) or ""), "exaggeration": script.get("exaggeration", 0.85), "cfg": script.get("cfg", 0.3), "takes": takes,
            "seed": seed, "jobs": [{"text": spoken[i][0], "out": str(Path(tmp) / f"s{i}.wav")} for i in idx]}
    jf = Path(tmp) / "chatterbox_jobs.json"
    jf.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    try:
        r = subprocess.run([str(TTS_PYTHON), str(Path(__file__).with_name("chatterbox_cli.py")), str(jf)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        if r.returncode != 0:
            print("chatterbox:", (r.stderr or r.stdout)[-400:])
    except Exception as e:
        print(f"chatterbox: {e}")
    if takes > 1:
        for i in idx:
            best = pick_take([Path(tmp) / f"s{i}_t{k}.wav" for k in range(takes)], script["lang"], spoken[i][1])
            if best:
                shutil.copy(best, Path(tmp) / f"s{i}.wav")
    only_set = set(idx)
    if script.get("learn", True):
        learn_pronunciation(script, lines, spoken, tmp, spec, only=only_set)
    if script.get("fix_pace", True):
        fix_pace(script, lines, spoken, tmp, spec, only=only_set)
    done = True
    for i in idx:
        if Path(tmp, f"s{i}.wav").exists():
            shutil.copy(Path(tmp, f"s{i}.wav"), Path(tmp, f"raw_{i}.wav"))
            recipe["made"][i] = recipe["retake"].get(i, 0)
        else:
            done = False
    return done


LAST_WORD_MAX = 1.1                                                             # seconds: a longer last word is a dragged vowel
PACE_MAX = 6.8                                                                  # syllables a second: real speakers in our data stay between 2.9 and 6.5


def speech_rate(wav, lang, text, toks):
    """syllables a second between the first and the last heard word, and how much of the sentence was heard"""
    from . import pronounce
    al, r, _m, _h = align_words(wav, lang, toks)
    if not al:
        return None, None
    if al[-1][1] - al[-1][0] > LAST_WORD_MAX:                                 # a dragged vowel is not a slower sentence
        return None, None
    return pronounce.syllable_count(text) / max(al[-1][1] - al[0][0], 0.2), r or 0.0


def fix_pace(script, lines, spoken, tmp, spec, only=None):
    """a sentence spoken too fast is recorded again, steadier (higher cfg) and calmer (lower exaggeration); the take with a natural pace and the
    words heard best replaces it"""
    lang = script["lang"]
    jobs, plan = [], []
    for i, (say_text, toks, _owner) in enumerate(spoken):
        wav = Path(tmp) / f"s{i}.wav"
        if not wav.exists() or (only is not None and i not in only):
            continue
        rate, heard = speech_rate(wav, lang, lines[i], toks)
        if rate is None or rate <= PACE_MAX:
            continue
        for k in range(2):
            out = Path(tmp) / f"s{i}_p{k}.wav"
            jobs.append({"text": say_text, "out": str(out)})
            plan.append((i, out, rate, heard))
        print(f"pace: scene {i + 1} at {rate:.1f} syllables/s, recording it again slower")
    if not jobs:
        return
    slow = {**spec, "takes": 1, "jobs": jobs, "cfg": min(0.8, float(spec.get("cfg", 0.3)) + 0.25), "exaggeration": max(0.3, float(spec.get("exaggeration", 0.85)) - 0.15)}
    jf = Path(tmp) / "chatterbox_pace.json"
    jf.write_text(json.dumps(slow, ensure_ascii=False), encoding="utf-8")
    try:
        subprocess.run([str(TTS_PYTHON), str(Path(__file__).with_name("chatterbox_cli.py")), str(jf)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1800, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except Exception as e:
        print(f"chatterbox pace: {e}")
        return
    chosen = {}
    for i, out, rate0, heard0 in plan:
        if not out.exists():
            continue
        rate, heard = speech_rate(out, lang, lines[i], spoken[i][1])
        if rate is None or heard < heard0 - 0.01:                              # never trade a correct sentence for a slower wrong one
            continue
        score = (rate <= PACE_MAX, heard, -abs(rate - 6.0))
        if score > chosen.get(i, (rate0 <= PACE_MAX, heard0, -abs(rate0 - 6.0))):  # only a take better than what is there
            chosen[i] = score
            shutil.copy(out, Path(tmp) / f"s{i}.wav")
            print(f"pace: scene {i + 1} now {rate:.1f} syllables/s")


def learn_pronunciation(script, lines, spoken, tmp, spec, only=None):
    """words still not heard after the best take: try respellings in one more Chatterbox run, keep the take that fixes them and remember what worked"""
    from . import pronounce
    lang = script["lang"]
    plan, jobs = [], []
    for i, (_text, toks, owner) in enumerate(spoken):
        wav = Path(tmp) / f"s{i}.wav"
        if not wav.exists() or (only is not None and i not in only):
            continue
        disp = lines[i].split()
        al, r, m, hn = align_words(wav, lang, toks)
        if al is None:
            continue
        bad = [j for j, h in enumerate(word_hits(disp, owner, m, hn)) if not h][:3]
        cands = {j: pronounce.candidates(disp[j], lang) for j in bad}
        for k in range(2):
            extra, used = dict(script.get("say") or {}), {}
            for j in bad:
                if len(cands[j]) > k:
                    extra[pronounce.key(disp[j])] = cands[j][k]
                    used[j] = cands[j][k]
            if not used:
                continue
            sp = spoken_form(lines[i], lang, extra)
            out = Path(tmp) / f"s{i}_r{k}.wav"
            jobs.append({"text": sp[0], "out": str(out)})
            plan.append((i, used, sp, out, r or 0.0))
    if not jobs:
        return
    jf = Path(tmp) / "chatterbox_retry.json"
    jf.write_text(json.dumps({**spec, "takes": 1, "jobs": jobs}, ensure_ascii=False), encoding="utf-8")
    try:
        subprocess.run([str(TTS_PYTHON), str(Path(__file__).with_name("chatterbox_cli.py")), str(jf)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1800, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except Exception as e:
        print(f"chatterbox retry: {e}")
        return
    best = {}
    for i, used, sp, out, r0 in plan:
        if not out.exists():
            continue
        disp = lines[i].split()
        _al, r, m, hn = align_words(out, lang, sp[1])
        hits = word_hits(disp, sp[2], m or [False] * len(sp[1]), hn)
        for j, say in used.items():
            pronounce.record(lang, disp[j], say, hits[j])
            print(f"pronunciation: {disp[j]!r} as {say!r}: {'fixed, remembered' if hits[j] else 'did not help'}")
        if r is not None and r > max(r0, best.get(i, 0.0)):
            best[i] = r
            shutil.copy(out, Path(tmp) / f"s{i}.wav")


def engine_for(script):
    """which voice engine: script["voice"] ("piper", "edge", "sapi"), else $REEL_VOICE, else the first that is ready: Piper (local), then Windows"""
    want = (script.get("voice") or os.environ.get("REEL_VOICE") or "").lower()
    if want in ("chatterbox", "piper", "edge", "sapi"):
        return want
    if chatterbox_ready(script):                                              # a cloned voice of your own beats the stock ones
        return "chatterbox"
    try:
        import piper  # noqa: F401
        if _piper_model(script.get("lang", "pl")):
            return "piper"
    except ImportError:
        pass
    return "sapi"


def tts_piper(text, lang, out_wav):
    """a local neural voice (Piper, MIT licence): free, offline, no account"""
    model = _piper_model(lang)
    if not model:
        return False
    try:
        from piper import PiperVoice
        v = _PIPER.get(str(model)) or _PIPER.setdefault(str(model), PiperVoice.load(str(model)))
        with wave.open(str(out_wav), "wb") as w:
            v.synthesize_wav(text, w)
        return Path(out_wav).stat().st_size > 1000
    except Exception as e:
        print(f"piper: {e}")
        return False


_PIPER = {}


def tts_edge(text, lang, out_wav):
    """Microsoft's neural voice through the Edge browser's read-aloud service (edge-tts). It is an UNOFFICIAL route: fine to try, not to build a product on"""
    try:
        import asyncio
        import edge_tts
        mp3 = str(out_wav) + ".mp3"
        asyncio.run(edge_tts.Communicate(text, EDGE_VOICES.get(lang, EDGE_VOICES["en"])).save(mp3))
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-ac", "1", "-ar", "24000", str(out_wav)], capture_output=True)
        Path(mp3).unlink(missing_ok=True)
        return r.returncode == 0 and Path(out_wav).exists()
    except Exception as e:
        print(f"edge-tts: {e}")
        return False


def tts(text, lang, out_wav, rate=0, engine="sapi"):
    """voice-over: Piper, edge-tts or the Windows speech engine; a failing engine falls back to Windows. False where nothing works"""
    if engine == "piper" and tts_piper(text, lang, out_wav):
        return True
    if engine == "edge" and tts_edge(text, lang, out_wav):
        return True
    return tts_sapi(text, lang, out_wav, rate)


def tts_sapi(text, lang, out_wav, rate=0):
    """voice-over with the Windows speech engine (System.Speech, no extra install); returns False where it is not available"""
    if sys.platform != "win32":
        return False
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
        txt = f.name
    cult = "pl" if lang == "pl" else "en"
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$v = $s.GetInstalledVoices() | Where-Object {{ $_.Enabled -and $_.VoiceInfo.Culture.Name -like '{cult}*' }} | Select-Object -First 1; "
          f"if ($v) {{ $s.SelectVoice($v.VoiceInfo.Name) }}; $s.Rate = {int(rate)}; $s.SetOutputToWaveFile('{out_wav}'); "
          f"$s.Speak([IO.File]::ReadAllText('{txt}', [Text.Encoding]::UTF8)); $s.Dispose()")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=120)
        return r.returncode == 0 and Path(out_wav).exists() and Path(out_wav).stat().st_size > 1000
    except Exception:
        return False
    finally:
        Path(txt).unlink(missing_ok=True)


def _wav_info(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate(), w.getframerate(), w.getnchannels(), w.getsampwidth()


def align_words(wav, lang, words, heard_out=None):
    """[(start, end)] of every script word inside one voice file, read off the speech itself (Whisper word timestamps), matched to the script's words;
    None when the model is not available. Words it did not hear get evenly spaced times between their neighbours. `heard_out`: a list that gets every word the
    recogniser heard, in order, as [word, start, end] (the sidecar keeps them: the quality check tells a skipped word from a mispronounced one with them)."""
    try:
        from .common import whisper_model
        segs, _ = whisper_model().transcribe(str(wav), language=lang, word_timestamps=True, beam_size=1, vad_filter=False, condition_on_previous_text=False)
        heard = [(w.word, w.start, w.end) for s in segs for w in (s.words or [])]
    except Exception:
        return None, None, None, set()
    if heard_out is not None:
        heard_out[:] = [[str(w).strip(), round(float(a), 3), round(float(b), 3)] for w, a, b in heard]
    if not heard:
        return None, None, None, set()
    from . import pronounce
    norm = lambda s: re.sub(r"[\W_]", "", s.lower())
    nw = [norm(w) for w in words]

    def hnorm(h):                                                              # "30" heard for the script's "trzydzieści" is the same word
        n = norm(h[0])
        return next((w for w in nw if n.isdigit() and pronounce.same_number(w, n)), n)
    hn = [hnorm(h) for h in heard]
    sm = difflib.SequenceMatcher(None, nw, hn, autojunk=False)
    out = [None] * len(words)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                out[i1 + k] = (heard[j1 + k][1], heard[j1 + k][2])
    matched = [o is not None for o in out]
    ratio = sum(matched) / max(len(words), 1)                                   # how much of the script the voice engine really said
    end_all = heard[-1][2]
    i = 0
    while i < len(out):                                                      # fill the gaps
        if out[i] is None:
            j = i
            while j < len(out) and out[j] is None:
                j += 1
            lo = out[i - 1][1] if i > 0 else 0.0
            hi = out[j][0] if j < len(out) else end_all
            for k in range(i, j):
                out[k] = (lo + (hi - lo) * (k - i) / (j - i), lo + (hi - lo) * (k - i + 1) / (j - i))
            i = j
        else:
            i += 1
    return out, ratio, matched, set(hn) | {norm(h[0]) for h in heard}


LEXICON = {"pl": {"ai": "ej aj", "skill": "skil", "skille": "skile", "skilli": "skili", "skilla": "skila", "skillami": "skilami", "skillach": "skilach",
                   "claude": "klołd", "chatgpt": "czat dżi pi ti", "gpt": "dżi pi ti", "github": "git hab", "n8n": "en osiem en", "mcp": "em ce pe",
                   "api": "a pe i", "cta": "ce te a", "ugc": "u gie ce", "ux": "ju eks", "ui": "ju aj", "seo": "es i o", "saas": "sas", "prompt": "prompt",
                   "prompty": "prompty", "cowork": "kołork", "gemini": "dżemini", "youtube": "jutub", "tiktok": "tiktok", "instagram": "instagram"},
           "en": {}}


def say_word(word, lang, extra=None):
    """how a word should be spoken (script["say"] first, then the built-in lexicon): punctuation around the word is kept"""
    m = re.match(r"^(\W*)(.*?)(\W*)$", word)
    pre, core, post = m.group(1), m.group(2), m.group(3)
    key = core.lower()
    from . import pronounce
    rep = (extra or {}).get(core) or (extra or {}).get(key) or pronounce.learned(lang).get(re.sub(r"[\W_]", "", key)) or LEXICON.get(lang, {}).get(key)
    if rep and core[:1].isupper() and rep[:1].islower():
        rep = rep[:1].upper() + rep[1:]                                       # keep a capital at the start of a sentence
    return pre + rep + post if rep else word


def spoken_form(text, lang, extra=None):
    """the sentence as the voice engine should read it, the tokens it is made of, and which script word each token belongs to"""
    disp = text.split()
    parts = [say_word(w, lang, extra) for w in disp]
    toks, owner = [], []
    for i, p in enumerate(parts):
        for tk in p.split():
            toks.append(tk)
            owner.append(i)
    return " ".join(parts), toks, owner


def word_hits(disp, owner, matched, heard_norms):
    """per script word: was it heard (one of its spoken tokens matched, or the word itself as written was recognised)"""
    norm = lambda s: re.sub(r"[\W_]", "", s.lower())
    return [any(matched[k] for k in range(len(owner)) if owner[k] == i) or norm(w) in heard_norms for i, w in enumerate(disp)]


def ears_score(paths):
    """{path: {"breath": worst breath-like score, ...}} from the sound-event model, or {} when it is not installed (it lives in the Chatterbox environment)"""
    if not TTS_PYTHON.is_file() or not paths:
        return {}
    with tempfile.TemporaryDirectory(prefix="ears_") as d:
        lst, out = Path(d) / "list.json", Path(d) / "out.json"
        lst.write_text(json.dumps([str(p) for p in paths]), encoding="utf-8")
        try:
            subprocess.run([str(TTS_PYTHON), str(Path(__file__).with_name("ears.py")), "--score", str(lst), str(out)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            return json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        except Exception as e:
            print(f"ears: {e}")
            return {}


def pick_take(takes, lang, toks):
    """of several recordings of one sentence keep the one whose words are heard best (a mispronounced word is heard as another word)"""
    from . import pronounce
    rated = []
    for p in takes:
        if not Path(p).exists():
            continue
        al, r, _m, _h = align_words(p, lang, toks)
        r = r if r is not None else 0.0
        if al:
            span = max(al[-1][1] - al[0][0], 0.3)
            rate = pronounce.syllable_count(" ".join(toks)) / span
            if al[-1][1] - al[-1][0] > LAST_WORD_MAX or rate < 3.0 or rate > 9.0:   # a vowel dragged on, or a rushed or crawling take: a different fault each
                r -= 0.5
        rated.append((r, p))
    if not rated:
        return None
    top = max(r for r, _p in rated)
    good = [p for r, p in rated if r >= top - 0.01]                           # the takes whose words are heard as well as the best one
    if len(good) > 1:
        sc = ears_score(good)                                                  # of those, the one with the least breath in it
        if sc:
            return min(good, key=lambda p: (sc.get(str(p), {}).get("breath", 1.0), sc.get(str(p), {}).get("mean", 1.0)))
    return good[0]


def speech_end(path, rel_db=-24.0):
    """seconds at which the voice file last rises above `rel_db` relative to its usual speech level (the 90th percentile of its 20 ms frames).
    The voice engine ends a sentence with a quiet breathy tail, 20-35 dB under the speech; speech recognition counts it as part of the last word,
    the level does not."""
    import numpy as np
    try:
        with wave.open(str(path)) as w:
            sr = w.getframerate()
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768.0
    except Exception:
        return None
    hop = max(1, int(sr * 0.02))
    e = np.array([np.sqrt(np.mean(x[i:i + hop] ** 2)) + 1e-9 for i in range(0, len(x) - hop, hop)])
    if not len(e):
        return None
    db = 20 * np.log10(e)
    loud = np.where(db > np.percentile(db, 90) + rel_db)[0]
    return float((loud[-1] + 1) * hop / sr) if len(loud) else None


def slow_wav(path, speed):
    """the voice at `speed` times its pace (0.93 = 7% slower) with the pitch unchanged"""
    tmp = Path(str(path) + ".tempo.wav")
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-filter:a", f"atempo={speed}", "-c:a", "pcm_s16le", str(tmp)], capture_output=True)
    if r.returncode == 0 and tmp.exists():
        tmp.replace(path)
    tmp.unlink(missing_ok=True)


def trim_wav(path, seconds, fade=0.06):
    """cut a voice file to `seconds` with a short fade-out: the silence the voice engine leaves after the last word would only lengthen the gap"""
    import numpy as np
    with wave.open(str(path)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    n = int(seconds * sr) * ch
    x = x[:n].astype("float32")
    f = min(len(x), int(fade * sr) * ch)
    if f:
        x[-f:] *= np.linspace(1, 0, f)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(sw)
        w.setframerate(sr)
        w.writeframes(x.astype("int16").tobytes())


def _gain_wav(path, db):
    """the voice file `db` louder (negative: quieter), in place; samples are clipped, not wrapped"""
    import numpy as np
    with wave.open(str(path)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32")
    x = np.clip(x * (10 ** (db / 20)), -32768, 32767)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(sw)
        w.setframerate(sr)
        w.writeframes(x.astype("int16").tobytes())


def _speech_db(path, a, b):
    import numpy as np
    with wave.open(str(path)) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768.0
        if w.getnchannels() > 1:
            x = x[::w.getnchannels()]
    seg = x[int(a * sr):int(b * sr)]
    return float(20 * np.log10(np.sqrt(np.mean(np.square(seg))) + 1e-9)) if len(seg) else -120.0


def timeline(script, tmp, recipe=None, meta=None):
    """[{text, screen, start, end, words:[(word, t0, t1)], wav}] and the combined audio path (None when no voice is available).
    `recipe` (new_recipe()) is what the repair loop changed; the pristine take of every scene stays in tmp/raw_<i>.wav, so a later round starts from it and only the
    scenes the recipe asks for are recorded again. `meta` (a dict) is filled with engine_wanted and engine_used."""
    import numpy as np
    recipe = recipe if recipe is not None else new_recipe()
    meta = meta if meta is not None else {}
    tmp = Path(tmp)
    lines = [(s["text"].strip(), s.get("screen", ""), s.get("emphasis") or [], s.get("chips") or []) for s in script["scenes"]]
    if script.get("cta"):
        lines.append((script["cta"].strip(), "", [], []))
    scenes, t, chunks, sr = [], 0.4, [], 22050
    engine = engine_for(script)
    meta["engine_wanted"] = engine
    redo = {i for i in range(len(lines)) if not (tmp / f"raw_{i}.wav").exists() or recipe["retake"].get(i, 0) != recipe["made"].get(i, 0)}
    prebuilt = False
    if engine == "chatterbox":
        prebuilt = (chatterbox_batch(script, [l[0] for l in lines], tmp, only=redo, recipe=recipe) if redo else True) and all((tmp / f"raw_{i}.wav").exists() for i in range(len(lines)))
        if not prebuilt and all((tmp / f"raw_{i}.wav").exists() for i in range(len(lines))):
            prebuilt = True                                                  # a retake failed: the earlier takes are still there
            meta["retake_failed"] = sorted(i for i in redo if recipe["retake"].get(i, 0) != recipe["made"].get(i, 0))
        if not prebuilt:
            engine = "piper" if _piper_model(script.get("lang", "pl")) else "sapi"          # the cloned voice failed: say so and use a local one
    meta["engine_used"] = engine
    have_voice = True
    for i, (text, screen, emph, chips) in enumerate(lines):
        wav, raw = tmp / f"s{i}.wav", tmp / f"raw_{i}.wav"
        dur = None
        say_text, toks, owner = spoken_form(text, script["lang"], say_for(script, recipe, i))
        ok = False
        if have_voice:
            if engine == "chatterbox":
                ok = prebuilt and raw.exists()
            elif i in redo or not raw.exists():
                ok = tts(say_text, script["lang"], str(wav), script.get("rate", 0), engine)
                if ok and wav.exists():
                    shutil.copy(wav, raw)
                    recipe["made"][i] = recipe["retake"].get(i, 0)
            else:
                ok = raw.exists()
            if ok and raw.exists():
                shutil.copy(raw, wav)                                          # every round starts from the pristine take
        if have_voice and ok:
            speed = float(script.get("speed", 0.93)) * float(recipe["speed"].get(i, 1.0))
            if 0.5 <= speed < 0.999:                                           # a calmer pace without a deeper voice
                slow_wav(wav, speed)
            dur, sr, ch, sw = _wav_info(wav)
        else:
            have_voice = False
            dur = max(1.6, len(text.split()) / WPS)
        ws = text.split()
        tot = sum(len(w) + 1 for w in ws)
        heard_out = []
        tal, theard, tmatch, theardset = align_words(wav, script["lang"], toks, heard_out) if have_voice else (None, None, None, set())     # the real word times, when the voice exists
        al = heard = None
        missed = []
        if tal:                                                                # tokens back to script words (a respelled word may be several tokens)
            al = [(min(tal[k][0] for k in range(len(toks)) if owner[k] == i) , max(tal[k][1] for k in range(len(toks)) if owner[k] == i)) for i in range(len(ws))]
            hit = word_hits(ws, owner, tmatch, theardset)
            heard = sum(hit) / max(len(ws), 1)
            missed = [w for w, h in zip(ws, hit) if not h]
        if al and heard >= 0.5:
            from . import pronounce
            rate = pronounce.syllable_count(text) / max(al[-1][1] - al[0][0], 0.3)
            if rate > PACE_MAX:                                                # slower by the exact factor, the word times follow
                f = max(0.78, 5.8 / rate)
                slow_wav(wav, f)
                al = [(a / f, b / f) for a, b in al]
                dur = _wav_info(wav)[0]
                print(f"pace: scene {i + 1} was {rate:.1f} syllables a second, slowed to {rate * f:.1f}")
        tail = 0.0
        if al and heard >= 0.5:
            cut = float(recipe["cut"].get(i, 0.0))
            cap = float(recipe["cap"].get(i, 0.85))
            end_words = al[-1][1]
            end_sound = speech_end(wav, -24.0 + cut)                           # where the voice really stops being speech: a word the recognizer
            end = end_sound if end_sound else end_words                        # missed ("AI") is kept, the breathy tail it counted is not
            dragged = al[-1][1] - al[-1][0] > LAST_WORD_MAX + 0.1 or end - al[-1][0] > LAST_WORD_MAX + 0.1
            if dragged:                                                        # the last word is dragged out: cut it short
                end = al[-1][0] + cap
                print(f"voice: scene {i + 1}: the sound after the last word started ran on for {(end_sound or end_words) - al[-1][0]:.1f} s, cut to {cap:.2f} s")
            tail = max(0.0, dur - end)
            keep = end + (0.1 if not cut else 0.05)
            if dur - keep > 0.05:                                              # breath, murmur and dead air after the last word: cut it
                trim_wav(wav, keep, fade=0.15 if dragged else 0.08)
                dur = keep
            al[-1] = (al[-1][0], min(max(end, al[-1][0] + 0.05), al[-1][1]) if end < end_words else end)
        c, words = 0, []
        for k, w in enumerate(ws):
            if al:
                words.append((w, t + al[k][0], t + max(al[k][1], al[k][0] + 0.05)))
            else:                                                            # otherwise a word's share of the sentence, by its length
                words.append((w, t + dur * c / tot, t + dur * (c + len(w) + 1) / tot))
            c += len(w) + 1
        scenes.append({"text": text, "screen": screen, "heard": heard, "missed": missed, "heard_words": [h[0] for h in heard_out], "tail": round(tail, 2),
                       "emphasis": [re.sub(r"\W", "", e.lower()) for e in emph], "chips": [str(c) for c in chips][:4], "start": t, "end": t + dur, "words": words,
                       "wav": wav if have_voice else None})
        t += dur + GAP
    if have_voice and recipe.get("level") and len(scenes) > 1:                  # the same speech level in every scene
        lv = [(s, _speech_db(s["wav"], s["words"][0][1] - s["start"], s["words"][-1][2] - s["start"])) for s in scenes if s["wav"] and s["words"]]
        if len(lv) > 1:
            med = sorted(v for _s, v in lv)[len(lv) // 2]
            for s, v in lv:
                g = max(-6.0, min(6.0, med - v))
                if abs(g) > 0.5:
                    _gain_wav(s["wav"], g)
    total = t + 0.6
    audio = None
    if have_voice:
        buf = np.zeros(int(total * sr), dtype=np.int16)
        for s in scenes:
            with wave.open(str(s["wav"])) as w:
                x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
                if w.getnchannels() > 1:
                    x = x[::w.getnchannels()]
            a = int(s["start"] * sr)
            buf[a:a + len(x)] = x[:max(0, len(buf) - a)]
        audio = tmp / "voice.wav"
        with wave.open(str(audio), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(buf.tobytes())
    return scenes, total, audio


# ---------------------------------------------------------------- picture (PIL, no extra install)

_FONTS = {}


def _font(size):
    from PIL import ImageFont
    if size not in _FONTS:
        for name in ("bahnschrift.ttf", "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
            try:
                f = ImageFont.truetype(name, size)
                try:
                    f.set_variation_by_name("SemiBold")
                except Exception:
                    pass
                _FONTS[size] = f
                break
            except Exception:
                continue
        else:
            _FONTS[size] = ImageFont.load_default()
    return _FONTS[size]


def _wrap(draw, words, font, width):
    lines, cur = [], []
    for w in words:
        if cur and draw.textlength(" ".join(cur + [w]), font=font) > width:
            lines.append(cur)
            cur = []
        cur.append(w)
    return lines + ([cur] if cur else [])


def _ease(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 4


def make_background():
    import numpy as np
    yy, xx = np.mgrid[0:H, 0:W].astype("float32")
    glow = np.exp(-(((xx - W * 0.8) / 700) ** 2 + ((yy - H * 0.12) / 650) ** 2))
    base = np.zeros((H, W, 3), dtype="float32")
    for k in range(3):
        base[:, :, k] = NAVY[k] + glow * (np.array([23, 58, 122])[k] - NAVY[k])
    return base.astype("uint8")


def frame(t, scenes, total, base):
    """one 1080x1920 RGB frame at time t"""
    import numpy as np
    from PIL import Image, ImageDraw
    bg = base.copy()
    shift = int(t * 14) % 120                                                 # slow sideways drift of the film grid
    cols = (np.arange(W) + shift) % 120 == 0
    bg[:, cols] = (bg[:, cols] * 0.6 + np.array(LINE) * 0.4).astype("uint8")
    y = int((t * 0.11 % 1.0) * H)                                             # the scan line
    for dy in range(-3, 4):
        if 0 <= y + dy < H:
            bg[y + dy] = (bg[y + dy] * (1 - 0.55 * (1 - abs(dy) / 4)) + np.array(SIGNAL) * 0.55 * (1 - abs(dy) / 4)).astype("uint8")
    img = Image.fromarray(bg)
    d = ImageDraw.Draw(img, "RGBA")
    cur = next((s for s in scenes if s["start"] - 0.05 <= t <= s["end"] + GAP), None)
    if cur:
        p = _ease((t - cur["start"]) / 0.55)
        if cur["screen"]:                                                     # the big slogan, upper third
            f = _font(132)
            lines = _wrap(d, cur["screen"].split(), f, 900)
            yy = 330 + int((1 - p) * 60)
            for ln in lines:
                txt = " ".join(ln)
                d.text((W / 2, yy), txt, font=f, fill=PAPER + (int(255 * p),), anchor="ma")
                yy += 150
            d.rectangle([W / 2 - 60, yy + 10, W / 2 + 60, yy + 16], fill=SIGNAL + (int(255 * p),))
        f = _font(66)                                                         # the spoken words, lower middle, current word lit
        words = cur["words"]
        lines = _wrap(d, [w for w, _a, _b in words], f, 900)
        yy = 1080 + (0 if cur["screen"] else -200)
        i = 0
        for ln in lines:
            widths = [d.textlength(w + " ", font=f) for w in ln]
            x = W / 2 - sum(widths) / 2
            for w, wd in zip(ln, widths):
                a, b = words[i][1], words[i][2]
                on = a <= t < b
                done = t >= b
                emph = re.sub(r"\W", "", w.lower()) in cur["emphasis"]
                col = SIGNAL if (on or emph and done) else (PAPER if done else MUTE)
                d.text((x, yy), w, font=f, fill=col + (255,))
                x += wd
                i += 1
            yy += 88
    d.rectangle([90, 1690, 90 + int(900 * min(1.0, t / total)), 1696], fill=SIGNAL + (255,))   # progress, inside the safe area
    d.rectangle([90, 1690, 990, 1696], outline=LINE + (70,))
    return np.asarray(img)


def mouth_track(audio, scenes, total):
    """how open the mascot's mouth is at every video frame, 0..1: the loudness of the voice, so it works in any language.
    Without a voice file the mouth moves during each spoken word instead."""
    import numpy as np
    n = int(total * FPS) + 1
    v = np.zeros(n)
    if audio:
        with wave.open(str(audio)) as w:
            sr = w.getframerate()
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768.0
        hop = max(1, int(sr / FPS))
        for i in range(n):
            seg = x[i * hop:(i + 1) * hop]
            v[i] = math.sqrt(float(np.mean(seg ** 2))) if len(seg) else 0.0
        loud = v[v > 1e-3]
        ref = float(np.percentile(loud, 90)) if len(loud) else 1.0
        v = np.clip(v / max(ref, 1e-6), 0, 1) ** 0.8
        v[v < 0.05] = 0
    else:
        for s in scenes:
            for _w, a, b in s["words"]:
                for k in range(int(a * FPS), min(n, int(b * FPS))):
                    v[k] = 0.35 + 0.45 * abs(math.sin(k * 1.3))
    out, prev = [], 0.0
    for q in v:                                                              # opens fast, closes a little slower: it looks like a mouth
        prev = float(q) if q > prev else prev * 0.55 + float(q) * 0.45
        out.append(round(prev, 3))
    return out


_VIS = {}
for _ch, _v in (("aą", 1), ("eę", 2), ("iy", 3), ("oó", 4), ("u", 5), ("mbp", 6), ("fvw", 7), ("szźżśćc", 8)):
    for _c in _ch:
        _VIS[_c] = _v


def viseme_track(scenes, mouth, total):
    """mouth shape per frame (0 rest, 1 A, 2 E, 3 I, 4 O, 5 U, 6 M B P, 7 F V, 8 S Z, 9 other): the letters of each word share the word's time
    (vowels count double), and a frame where the voice is silent stays at rest. Works for Polish and English spelling alike."""
    n = int(total * FPS) + 1
    vis = [0] * n
    for s in scenes:
        for w, a, b in s["words"]:
            letters = [c for c in w.lower() if c.isalpha()]
            if not letters:
                continue
            wts = [2.0 if _VIS.get(c) in (1, 2, 3, 4, 5) else 1.0 for c in letters]
            tot, acc = sum(wts), 0.0
            for c, wt in zip(letters, wts):
                lo = a + (b - a) * acc / tot
                acc += wt
                hi = a + (b - a) * acc / tot
                for k in range(int(lo * FPS), min(n, max(int(lo * FPS) + 1, int(hi * FPS)))):
                    vis[k] = _VIS.get(c, 9)
    return [v if (i < len(mouth) and mouth[i] > 0.05) else 0 for i, v in enumerate(vis)]


def studio_dir():
    """the Remotion project (studio/ next to the source checkout, or $REEL_STUDIO) when `npm install` was run there"""
    d = Path(os.environ.get("REEL_STUDIO") or Path(__file__).resolve().parents[2] / "studio")
    return d if (d / "node_modules" / ".bin").is_dir() and (d / "src" / "Reel.tsx").exists() else None


def master_wav(src, out, target_lufs, ceiling=-1.0):
    """the mix brought to the loudness a short-video platform expects: a linear gain to `target_lufs` (BS.1770-4, read as the dual-mono stereo file the mp4 holds)
    and a look-ahead true-peak limiter at `ceiling` dBTP (quality/mix.py master). Written as a two-channel wav with the same sound in both. Returns the numbers of the step"""
    import numpy as np
    from .quality import mix as qmix
    with wave.open(str(src)) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768.0
        if w.getnchannels() > 1:
            x = x[::w.getnchannels()]
    y, info = qmix.master(x, sr, target_lufs, ceiling, dual_mono=True)
    pcm = (np.clip(y, -1, 1) * 32767).astype("int16")
    with wave.open(str(out), "wb") as w:                                       # the same sound on both channels, written as two: an encoder that is given a mono file and asked for
        w.setnchannels(2)                                                      # stereo lowers each channel by 3 dB (power is kept), and then the file reads 3 LU under the target
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.repeat(pcm, 2).tobytes())
    num = lambda r, k: None if r.get(k) is None else round(float(r[k]), 2)
    return {"target_lufs": target_lufs, "ceiling_db": ceiling, "before_lufs": num(info["before"], "integrated"), "after_lufs": num(info["after"], "integrated"),
            "true_peak_db": num(info["after"], "true_peak_db"), "gain_db": round(float(info["gain_db"]), 2), "limited": bool(info["limited"]), "reached": bool(info["reached"]),
            "reason": info["reason"]}


def final_audio(script, voice, total, tmp, scenes=None, recipe=None, meta=None):
    """the voice with a music bed under it (unless the script says "music": "off"), then mastered to the loudness target of the quality profile (unless "master": false);
    the voice alone when there is no music or the mix fails. The music alone, as mixed, is left in tmp/music_stem.wav. Returns the wav to put under the picture."""
    recipe = recipe if recipe is not None else new_recipe()
    meta = meta if meta is not None else {}
    tmp = Path(tmp)
    mode = script.get("music", "bed")
    meta.pop("music_stem", None)
    if not voice:
        return voice
    mixed = voice
    if mode != "off":
        from . import music
        src = mode if mode not in ("bed", True) else None
        if src and not Path(src).is_file():
            src = None
        if not src:
            src = tmp / "bed.wav"
            music.write_wav(src, music.bed(total + 1))
        out = tmp / "mixed.wav"
        spans = [(s["words"][0][1], s["words"][-1][2]) for s in (scenes or []) if s["words"]]
        vol = float(script.get("music_volume", 0.2)) * float(recipe.get("music_mult", 1.0))
        stem = tmp / "music_stem.wav"
        stem.unlink(missing_ok=True)
        ok = music.mix_steady(voice, src, out, spans, vol, stem_wav=stem, hp=int(recipe.get("hp", 150))) if spans else music.mix(voice, src, out, vol)
        if ok:
            mixed = out
            if stem.exists():
                meta["music_stem"] = str(stem)
    if script.get("master", True):
        from .quality import profile as qprofile
        prof = qprofile.load()
        target = float(script.get("loudness") or prof["loudness"]["target_lufs"])
        try:
            info = master_wav(mixed, tmp / "final.wav", target, float(prof["loudness"].get("true_peak_max", -1.0)))
        except Exception as e:                                                 # a failed master never blocks the reel: the check will say that it is too quiet
            print(f"master: {e}")
            info = None
        if info:
            meta["master"] = info
            return tmp / "final.wav"
    return mixed


def render_remotion(script, scenes, total, audio, out, studio, play=None, mouth=None):
    """the animated version: the timeline and the voice are made here, Remotion draws the pictures"""
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if not npx:
        return False
    work = studio / "public" / "_work"
    work.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:8]
    mouth = mouth if mouth is not None else mouth_track(audio, scenes, total)
    props = {"total": total, "audio": "", "mascot": bool(script.get("mascot", True)), "mouth": mouth, "vis": viseme_track(scenes, mouth, total), "scenes": [{"text": s["text"], "screen": s["screen"], "emphasis": s["emphasis"], "chips": s.get("chips", []),
                                                        "start": s["start"], "end": s["end"], "words": [[w, a, b] for w, a, b in s["words"]]} for s in scenes]}
    if audio:
        shutil.copy(play or audio, work / f"{tag}.wav")
        props["audio"] = f"_work/{tag}.wav"
    pf = work / f"{tag}.json"
    pf.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    try:
        r = subprocess.run([npx, "remotion", "render", "src/index.ts", "Reel", str(Path(out).resolve()), f"--props={pf}", "--codec=h264", "--crf=18", "--log=error"],
                           cwd=str(studio), capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            print((r.stdout + r.stderr)[-600:])
        return r.returncode == 0 and Path(out).exists()
    finally:
        for p in work.glob(f"{tag}.*"):
            p.unlink(missing_ok=True)


def _sidecar(script, scenes, total, audio, meta, recipe, simple, mouth):
    """the sidecar (<reel>.qa.json), version 2: the timeline the quality check reads, and everything it needs to tell a fault of the reel from a fault of how it was
    made (which voice engine, which renderer, with which parameters, mastered to what)"""
    return {"version": 2, "total": total, "mouth": mouth,
            "scenes": [{"text": s["text"], "screen": s["screen"], "start": s["start"], "end": s["end"], "heard": s.get("heard"), "missed": s.get("missed", []),
                        "heard_words": s.get("heard_words", []), "tail": s.get("tail", 0), "words": [[w, a, b] for w, a, b in s["words"]]} for s in scenes],
            "params": {"lang": script.get("lang"), "say": script.get("say") or {}, "fps": FPS, "mascot": bool(script.get("mascot", True)), "simple": bool(simple),
                       "voice": script.get("voice"), "takes": script.get("takes", 4), "speed": script.get("speed", 0.93), "exaggeration": script.get("exaggeration", 0.85),
                       "cfg": script.get("cfg", 0.3), "music": script.get("music", "bed"), "music_volume": script.get("music_volume", 0.2), "master": script.get("master", True)},
            "engine_wanted": meta.get("engine_wanted"), "engine_used": meta.get("engine_used"), "renderer_wanted": "pil" if simple else "remotion", "renderer": None,
            "master": meta.get("master"), "has_music_stem": bool(meta.get("music_stem")), "recipe": json.loads(json.dumps(recipe))}


def _write_sidecar(out, side):
    out.with_suffix(".qa.json").write_text(json.dumps(side, ensure_ascii=False), encoding="utf-8")


def mux_audio(video_mp4, wav, out_mp4):
    """the picture of an mp4 kept as it is (no new render), with another sound track: how a repair that only changes the sound is applied in seconds"""
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_mp4), "-i", str(wav), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
                        "-b:a", "192k", "-ac", "2", "-ar", "48000", "-movflags", "+faststart", "-shortest", str(out_mp4)], capture_output=True, text=True)
    return r.returncode == 0 and Path(out_mp4).exists()


def render(script, out_mp4, simple=False, workdir=None, recipe=None, meta=None, audio_only=False):
    """script dict -> out_mp4 (+ the caption file, the sidecar <reel>.qa.json, the dry voice <reel>_voice.wav and the music stem <reel>_music.wav); returns the output path.
    `workdir` keeps the takes between rounds of the repair loop and `recipe` what the loop changed (both given: only the scenes the recipe names are recorded
    again); `audio_only`: the picture of the existing out_mp4 is kept and only the sound is made again and put under it (the repair actions that change no timing)."""
    out = Path(out_mp4)
    out.parent.mkdir(parents=True, exist_ok=True)
    recipe = recipe if recipe is not None else new_recipe()
    meta = meta if meta is not None else {}
    own = workdir is None
    holder = tempfile.TemporaryDirectory(prefix="reelmake_") if own else None
    tmp = Path(holder.name if own else workdir)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        scenes, total, audio = timeline(script, tmp, recipe, meta)
        studio = None if simple else studio_dir()
        play = final_audio(script, audio, total, tmp, scenes, recipe, meta)
        mouth = mouth_track(audio, scenes, total) if audio else []
        side = _sidecar(script, scenes, total, audio, meta, recipe, simple, mouth)
        if audio:
            shutil.copy(audio, out.with_name(out.stem + "_voice.wav"))
        stem = out.with_name(out.stem + "_music.wav")
        if meta.get("music_stem") and Path(meta["music_stem"]).exists():
            shutil.copy(meta["music_stem"], stem)
        else:
            stem.unlink(missing_ok=True)
        cap = (script.get("caption") or "").strip() + "\n\n" + " ".join(script.get("hashtags") or []) + "\n"
        if audio_only and out.exists() and (play or audio) and mux_audio(out, play or audio, tmp / "remux.mp4"):
            shutil.copy(tmp / "remux.mp4", out)
            side["renderer"] = meta.get("renderer") or side["renderer_wanted"]
        else:
            _write_sidecar(out, side)                                          # the timeline is on disk while the picture is being drawn
            if studio and render_remotion(script, scenes, total, audio, out, studio, play, mouth):
                side["renderer"] = "remotion"
            else:
                side["renderer"] = "pil"
                base = make_background()
                cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
                if audio:
                    cmd += ["-i", str(play or audio)]
                cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-t", f"{total:.2f}"]
                cmd += (["-c:a", "aac", "-b:a", "160k", "-ac", "2", "-shortest"] if audio else ["-an"]) + ["-movflags", "+faststart", str(out)]
                pr = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                try:
                    for k in range(int(total * FPS)):
                        pr.stdin.write(frame(k / FPS, scenes, total, base).tobytes())
                finally:
                    pr.stdin.close()
                    pr.wait()
        meta["renderer"] = side["renderer"]
        out.with_name(out.stem + "_opis.txt").write_text(cap, encoding="utf-8")
        if out.exists():
            from .quality import engine as qengine
            side["mp4_sha256"] = qengine.sha256(out)                           # the check refuses a sidecar that does not belong to the file next to it
            side["mp4_bytes"] = out.stat().st_size
        _write_sidecar(out, side)
    finally:
        if holder is not None:
            holder.cleanup()
    return out


def apply_repair(script, recipe, a, meta, log=print):
    """change the recipe for ONE repair action of quality/repair.py; True when that changed something, False when the action does not apply here (a deterministic voice
    engine has nothing to record again; a layout has no lever yet). Nothing is rendered here: render() does that once for the whole round."""
    i = None if a.scene is None else a.scene - 1
    k = a.kind
    findings = a.data.get("findings", [])
    if k == "retake_scene":
        if meta.get("engine_used") != "chatterbox":
            log(f"repair: scene {a.scene} cannot be recorded again: the voice engine ({meta.get('engine_used')}) says the same thing every time")
            return False
        recipe["retake"][i] = recipe["retake"].get(i, 0) + 1
        return True
    if k == "respell":
        from . import pronounce
        lang = script.get("lang", "pl")
        used = recipe["respell"].setdefault(i, {})
        tried = recipe.setdefault("respell_tried", {}).setdefault(i, {})
        changed = False
        for w in [w for f in findings for w in (f.get("words") or [])]:
            key = pronounce.key(w)
            seen = tried.setdefault(key, [])
            for c in pronounce.candidates(w, lang):
                if c not in seen:
                    seen.append(c)
                    used[key] = c
                    changed = True
                    break
        if not changed:                                                        # no spelling left to try: a new take is the next best thing
            return apply_repair(script, recipe, type(a)("retake_scene", a.scene, a.codes, a.data), meta, log)
        recipe["retake"][i] = recipe["retake"].get(i, 0) + 1
        return True
    if k == "slow_scene":
        rate = max([max(f.get("heard") or 0, f.get("by_words") or 0, f.get("rate") or 0) for f in findings] or [0]) or 7.0
        new = float(recipe["speed"].get(i, 1.0)) * max(0.78, min(0.95, 5.8 / rate))
        if new < 0.72:
            return False
        recipe["speed"][i] = round(new, 3)
        return True
    if k == "retrim_scene":
        cut, cap = float(recipe["cut"].get(i, 0.0)), float(recipe["cap"].get(i, 0.85))
        if cut >= 18.0 and cap <= 0.45:
            return False
        recipe["cut"][i] = min(18.0, cut + 6.0)
        recipe["cap"][i] = round(max(0.45, cap - 0.25), 2)
        return True
    if k == "level_scenes":
        if recipe.get("level"):
            return False
        recipe["level"] = True
        return True
    if k == "remix":
        if float(recipe.get("music_mult", 1.0)) <= 0.26 and int(recipe.get("hp", 150)) >= 200:
            return False
        recipe["music_mult"] = round(max(0.25, float(recipe.get("music_mult", 1.0)) * 0.7), 3)
        recipe["hp"] = 200
        return True
    if k == "master":
        if script.get("master", True):
            return False                                                       # it was mastered already: the target is out of reach (the limiter protects the peaks)
        script["master"] = True
        return True
    if k == "relayout":
        log("repair: the layout has no automatic lever (the safe-zone layout is computed in studio/src/layout.ts); a person has to look at this")
        return False
    return k == "rerender"


def render_checked(script, out_mp4, simple=False, rounds=3, quick=False, log=print):
    """render the reel, check it, repair what can be repaired and check again (at most `rounds` times), then write <reel>_kontrola.md and put the verdict into
    <reel>.qa.json. Returns {out, verdict, exit, findings, summary, history}. `quick` leaves out the slow checks (the sound-event model, the OCR, the recogniser)."""
    from .quality import engine as qengine, notes as qnotes, repair, verdict
    out = Path(out_mp4)
    work = Path(tempfile.mkdtemp(prefix="reelwork_"))
    recipe, meta = new_recipe(), {}
    opts = {"ears": not quick, "ocr": not quick, "do_rehear": not quick}
    state = {}
    try:
        render(script, out, simple, workdir=work, recipe=recipe, meta=meta)

        def check():
            r = qengine.check_reel(out, **opts)
            state["last"] = r
            return r["findings"]

        def apply(a):
            ok = apply_repair(script, recipe, a, meta, log)
            log(f"repair: {a.kind}" + (f" scene {a.scene}" if a.scene else "") + f" ({', '.join(a.codes)}): {'done' if ok else 'not possible'}")
            return ok

        def rebuild(p):
            log("rendering again" if not p.audio_only else "mixing again (the picture stays)")
            render(script, out, simple, workdir=work, recipe=recipe, meta=meta, audio_only=p.audio_only)

        def snapshot():
            snap = Path(tempfile.mkdtemp(prefix="reelsnap_"))
            for name in (out.name, out.with_suffix(".qa.json").name, out.stem + "_voice.wav", out.stem + "_music.wav", out.stem + "_opis.txt"):
                if (out.parent / name).is_file():
                    shutil.copy(out.parent / name, snap / name)
            import copy
            return {"dir": snap, "recipe": copy.deepcopy(recipe), "script_master": script.get("master", True)}

        def restore(snap):
            for f in Path(snap["dir"]).iterdir():
                shutil.copy(f, out.parent / f.name)
            import copy
            recipe.clear()
            recipe.update(copy.deepcopy(snap["recipe"]))
            script["master"] = snap["script_master"]
            shutil.rmtree(snap["dir"], ignore_errors=True)

        side_now = lambda: (json.loads(out.with_suffix(".qa.json").read_text(encoding="utf-8")) if out.with_suffix(".qa.json").exists() else {"scenes": []})
        res = repair.run_loop(check, apply, rebuild, scenes_of=lambda: side_now().get("scenes", []), max_rounds=rounds, snapshot=snapshot, restore=restore, log=log)
        findings = res.findings
        last = state.get("last") or {}
        rep_notes = None
        nts = qnotes.load_notes(reel=out)
        if nts:
            rep_notes = qnotes.recall_report(qnotes.match(nts, findings, scenes=side_now().get("scenes", [])))
        summary = verdict.summarize(findings, res.history, checked=list(qengine.ORDER), notes_report=rep_notes)
        summary["manual"] = res.manual
        summary["stopped"] = res.stopped
        side = side_now()
        for k in ("video", "rehear"):
            if (last.get("side") or {}).get(k):
                side[k] = last["side"][k]
        side["qa"] = {k: v for k, v in summary.items() if k != "findings"}
        side["qa"]["findings"] = summary["findings"][:60]
        out.with_suffix(".qa.json").write_text(json.dumps(side, ensure_ascii=False), encoding="utf-8")
        md = verdict.report_pl(findings, res.history, title=f"Kontrola rolki {out.stem}", notes_report=rep_notes)
        if res.manual:
            md += "\n## Czego system nie naprawił sam\n\n" + "\n".join(f"- `{m['code']}`" + (f", scena {m['scene']}" if m.get("scene") else "") + f": {m['why']}" for m in res.manual) + "\n"
        out.with_name(out.stem + "_kontrola.md").write_text(md, encoding="utf-8")
        print(verdict.format_console(findings, res.history))
        return {"out": out, "verdict": summary["verdict"], "exit": summary["exit"], "findings": findings, "summary": summary, "history": res.history}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    sub = a.pop(0) if a else ""
    if sub == "brief":
        return brief() or 0
    if sub in ("qa", "calibrate", "note", "learn"):
        from . import reel_qa
        return reel_qa.main([sub, *a])
    if sub in ("check", "render") and a:
        script = load_script(a[0])
        probs = check(script)
        for sev, msg in probs:
            print(f"{sev}: {msg}")
        if sub == "check":
            print("OK" if not probs else f"{len(probs)} remark(s)")
            return 1 if any(s == "ERROR" for s, _ in probs) else 0
        if any(s == "ERROR" for s, _ in probs):
            return 1
        out = Path(a[0]).with_suffix(".mp4")
        if "--no-check" in a:
            render(script, out, simple="--simple" in a)
            print(f"{out} ({out.stat().st_size // 1024} KB)")
            return 0
        rounds = 3
        if "--rounds" in a and a.index("--rounds") + 1 < len(a):
            rounds = max(0, int(a[a.index("--rounds") + 1]))
        res = render_checked(script, out, simple="--simple" in a, rounds=rounds, quick="--quick" in a)
        print(f"{out} ({out.stat().st_size // 1024} KB)  werdykt: {res['verdict']}  raport: {out.with_name(out.stem + '_kontrola.md').name}")
        return res["exit"]
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
