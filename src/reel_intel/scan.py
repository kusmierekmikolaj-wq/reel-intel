"""scan.py - fastest reliable path to the exact content of reels: speech + caption + metrics.

One yt-dlp call per reel fetches metadata AND the tiny audio-only stream; downloads run in parallel,
Whisper runs once per reel. Reels with (almost) no speech get a contact sheet of frames instead.

  reel.py scan urls.txt            # one reel URL per line
  reel.py scan --grid grid.tsv     # also store profile-grid view counts (id<TAB>raw text such as '40,2 tys.')
Output per reel in <data>/data/<id>/: meta.json, audio.wav, transcript.json/.md, features.json,
content_full.md (verbatim record), contact_sheet_1.jpg (visual reels only).  content_short.md is written by Claude.
"""
import json
import random
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .common import CFG, DATA, account_language, WORK, apply_fixes, duration, jload, near_duplicate, parse_views, reel_id, restore_diacritics, run, transcribe, views_table, ytdlp


PERMANENT = ("isn't available", "login", "private", "not available", "removed", "age-restricted", "sign in")
OUTDATED = ("unable to extract", "unsupported url", "http error 4", "nonetype", "extractor error", "failed to parse")
_updated = False


def update_ytdlp():
    """platforms change their pages and yt-dlp ships fixes within days: update once per run when an extractor fails"""
    global _updated
    if not _updated:
        _updated = True
        print("yt-dlp seems outdated for this site: updating it once ...", flush=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp"], capture_output=True)


def hint(err):
    e = err.lower()
    if any(k in e for k in ("login", "isn't available", "private", "sign in")):
        return err + "  -> needs a logged-in session: export cookies.txt (see README) and set cookies_file in config.yaml"
    if "http error 429" in e or "rate" in e:
        return err + "  -> rate limited: wait a few minutes and lower `workers` in config.yaml"
    return err + "  -> run `reel-intel doctor`"


def fetch(url):
    """metadata + audio-only stream in a single request -> (id, error|None)"""
    rid = reel_id(url)
    d = DATA / rid
    d.mkdir(parents=True, exist_ok=True)
    if (d / "meta.json").exists() and (d / "audio.wav").exists():
        return rid, None
    time.sleep(random.uniform(0.3, 1.5))                               # gentle on Instagram
    r = None
    for attempt in range(3):                                           # transient failures are common: retry with a growing pause
        r = run(ytdlp() + ["-f", "ba/b", "-j", "--no-simulate", "--no-warnings", "-o", str(d / "audio_src") + ".%(ext)s", url])
        if r.returncode == 0 and r.stdout.strip():
            break
        err = (r.stderr or "").lower()
        if any(k in err for k in PERMANENT):                           # needs login / private / removed: retrying will not help
            break
        if attempt == 0 and any(k in err for k in OUTDATED):
            update_ytdlp()
        time.sleep(2 * (attempt + 1))
    if r.returncode != 0 or not r.stdout.strip():
        return rid, hint((r.stderr or "no output")[-300:])
    meta = json.loads(r.stdout.splitlines()[-1])
    for k in ("formats", "thumbnails", "requested_formats", "requested_downloads"):
        meta.pop(k, None)
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    src = list(d.glob("audio_src.*"))
    if not src:
        return rid, "audio missing after download"
    c = run(["ffmpeg", "-y", "-i", str(src[0]), "-vn", "-ac", "1", "-ar", "16000", str(d / "audio.wav")])
    for p in src:
        p.unlink()
    return rid, None if (d / "audio.wav").exists() else c.stderr[-300:]


def needs_sheet(f):
    return f.get("word_count", 99) < 10 or not f.get("speech_reliable", True)


def post_time(meta, what):
    import datetime as dt
    if not meta.get("timestamp"):
        return None
    t = dt.datetime.fromtimestamp(meta["timestamp"])               # machine-local time zone
    return t.hour if what == "hour" else t.weekday()                # weekday: 0 = Monday


def finish(rid):
    d = DATA / rid
    meta = jload(d / "meta.json")
    tr = transcribe(d / "audio.wav", d, meta)
    dur = duration(d / "audio.wav")
    segs = tr["segments"]
    words = [w for s in segs for w in s["words"]]
    text = " ".join(s["text"] for s in segs)
    avgp = sum(w["p"] for w in words) / len(words) if words else 1.0
    f = {
        "id": rid, "url": meta.get("webpage_url"), "uploader": meta.get("uploader"), "upload_date": meta.get("upload_date"),
        "timestamp": meta.get("timestamp"), "hour": post_time(meta, "hour"), "weekday": post_time(meta, "weekday"),   # local time
        "duration_s": round(dur, 2), "views": meta.get("view_count") or views_table().get(rid), "likes": meta.get("like_count"),
        "comments": meta.get("comment_count"), "caption": meta.get("description"), "hashtags": re.findall(r"#\w+", meta.get("description") or ""),
        "language": tr["language"], "word_count": len(words), "words_per_min": round(len(words) / (dur / 60), 1) if dur else None,
        "first_word_at_s": words[0]["s"] if words else None, "hook_spoken": " ".join(s["text"] for s in segs if s["start"] < 3.0),
        "hook_words_3s": sum(1 for w in words if w["s"] < 3.0), "question_marks": text.count("?"),
        "digits_in_speech": len(re.findall(r"\d+", text)),
        # music-only audio: Whisper guesses a language and invents words -> not reliable speech
        "speech_reliable": not (words and ((tr["language_probability"] < 0.6 and avgp < 0.6)
                                           or (tr["language_probability"] < 0.5 and account_language() not in (None, tr["language"])))),   # an odd language, unsure: noise
    }
    (d / "features.json").write_text(json.dumps(f, ensure_ascii=False, indent=1), encoding="utf-8")
    write_content(d, tr, f)
    return f


def mmss(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


CONTENT_LABELS = {
    "pl": {"title": "treść dosłowna", "author": "Autor", "date": "data", "length": "długość", "lang": "język", "views": "Wyświetlenia", "likes": "lajki",
           "comments": "komentarze", "none": "brak", "speech": "1. Mowa (dosłownie, z czasami)",
           "nospeech": "**BRAK MOWY: treść jest wizualna: odczytaj z contact_sheet_1.jpg.**",
           "unreliable": "**MOWA NIEWIARYGODNA (prawdopodobnie sama muzyka; poniżej zgadywanie Whispera, NIE cytować): odczytaj z contact_sheet_1.jpg.**",
           "screen": "2b. Tekst na ekranie (OCR z 12 klatek, surowy; potwierdź na contact_sheet_1.jpg)", "caption": "2. Opis posta (caption, dosłownie)",
           "reliab": "3. Wiarygodność", "low": "Słowa o niskiej pewności (<0.5)", "fixes": "Korekty ze słownika (tylko tu; surowy zapis w transcript.json)"},
    "en": {"title": "verbatim content", "author": "Author", "date": "date", "length": "length", "lang": "language", "views": "Views", "likes": "likes",
           "comments": "comments", "none": "none", "speech": "1. Speech (verbatim, with timestamps)",
           "nospeech": "**NO SPEECH: the content is visual: read it from contact_sheet_1.jpg.**",
           "unreliable": "**SPEECH NOT RELIABLE (probably music only; what follows is Whisper guessing, do NOT quote): read contact_sheet_1.jpg.**",
           "screen": "2b. On-screen text (OCR of 12 frames, raw; confirm on contact_sheet_1.jpg)", "caption": "2. Post caption (verbatim)",
           "reliab": "3. Reliability", "low": "Low-confidence words (<0.5)", "fixes": "Glossary corrections (this file only; raw text stays in transcript.json)"},
}


def write_content(d, tr, f):
    from .common import CFG
    K = CONTENT_LABELS.get(CFG.get("ui_language"), CONTENT_LABELS["en"])
    fixes = []
    lines = []
    for s in tr["segments"]:
        t, log = apply_fixes(s["text"], tr["language"])
        fixes += log
        a, b = (s["words"][0]["s"], s["words"][-1]["e"]) if s.get("words") else (s["start"], s["end"])   # word times: segment ends can stretch over silence
        lines.append(f"[{mmss(a)}-{mmss(b)}] {t}")
    words = [w for s in tr["segments"] for w in s["words"]]
    low = [w for w in words if w["p"] < 0.5]
    v = f"{f['views']:,.0f}" if f.get("views") else K["none"]
    L = [f"# {f['id']} - {K['title']}", "",
         f"- {K['author']}: {f['uploader']} | {K['date']}: {f['upload_date']} | {K['length']}: {f['duration_s']} s | {K['lang']}: {tr['language']}",
         f"- {K['views']}: {v} | {K['likes']}: {f['likes']} | {K['comments']}: {f['comments']}", f"- URL: {f['url']}", "", f"## {K['speech']}", ""]
    if not words:
        L += [K["nospeech"], ""]
    elif not f["speech_reliable"]:
        L += [K["unreliable"], ""]
    shown = []
    if (d / "screen_text.json").exists():                              # every video: one frame a second, see visuals.screen_text
        for x in jload(d / "screen_text.json")["lines"]:
            t = restore_diacritics(x["text"], tr["language"])
            if not any(near_duplicate(t, s) for _, s in shown):
                shown.append((x["t"], t))
    elif (d / "onscreen.json").exists():                               # visual reels: OCR of the 12 contact-sheet frames
        for o in jload(d / "onscreen.json"):
            for t in o["text"]:
                t = restore_diacritics(t, tr["language"])
                if not any(near_duplicate(t, s) for _, s in shown):
                    shown.append((o["t"], t))
    screen = ["", f"## {K['screen']}", ""] +              [f"- [{mmss(t)}] {s}" for t, s in shown] if shown or (d / "onscreen.json").exists() else []
    L += lines + screen + ["", f"## {K['caption']}", "", f.get("caption") or f"({K['none']})", "", f"## {K['reliab']}", "",
                  f"- {K['low']}: {len(low)}/{len(words)}" + (": " + ", ".join(f"{w['w']} ({w['s']:.1f}s)" for w in low[:25]) if low else ""),
                  f"- {K['fixes']}: {'; '.join(fixes) or K['none']}"]
    (d / "content_full.md").write_text("\n".join(L), encoding="utf-8")


def ocr_frames(d):
    """OCR only the ~12 contact-sheet frames of a visual reel (2x upscale keeps word spaces; near-identical frames reused)"""
    out = d / "onscreen.json"
    if out.exists() or not (d / "frames").exists():
        return
    import cv2
    from rapidocr_onnxruntime import RapidOCR
    ocr, res, prev = RapidOCR(), [], None
    for p in sorted((d / "frames").glob("f_*.jpg")):
        img = cv2.imread(str(p))
        small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (54, 96)).astype("int16")
        if prev is not None and abs(small - prev).mean() < 3 and res:
            texts = res[-1]["text"]
        else:
            r, _ = ocr(cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
            texts = [x[1] for x in (r or []) if float(x[2]) > 0.6]
        prev = small
        res.append({"t": float(p.stem[2:]), "text": list(texts)})
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


def contact_sheet(url):
    """smallest video stream + 12 labelled frames in one image (no OCR: Claude reads the image)"""
    import cv2
    import numpy as np
    rid = reel_id(url)
    d = DATA / rid
    if (d / "contact_sheet_1.jpg").exists():
        return rid, None
    found = sorted(d.glob("video_small.*"))
    vid = next((p for p in found if p.suffix in (".mp4", ".webm", ".mkv")), None)
    if vid is None:
        # video only (the sheet needs no sound): a merged video+audio download is blocked (HTTP 403) on some sites, the bare stream is not
        r = None
        for fmt in ("wv*", "w"):
            r = run(ytdlp() + ["-f", fmt, "--no-warnings", "-o", str(d / "video_small.%(ext)s"), url])
            vid = next((p for p in sorted(d.glob("video_small.*")) if p.suffix in (".mp4", ".webm", ".mkv")), None)
            if r.returncode == 0 and vid:
                break
        if vid is None:
            return rid, hint((r.stderr or "no video")[-200:])
    dur = duration(vid)
    (d / "frames").mkdir(exist_ok=True)
    w, h, cols = 240, 427, 6
    ts = sorted({round(min(dur - 0.3, i * dur / 11), 2) for i in range(12)} | {0.3})
    sheet = np.zeros((((len(ts) + cols - 1) // cols) * h, cols * w, 3), np.uint8)
    for k, t in enumerate(ts):
        p = d / "frames" / f"f_{t:07.2f}.jpg"
        run(["ffmpeg", "-y", "-ss", str(max(t, 0)), "-i", str(vid), "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "3", str(p)])
        img = cv2.imread(str(p))
        if img is None:
            continue
        img = cv2.resize(img, (w, h))
        cv2.rectangle(img, (0, 0), (74, 22), (0, 0, 0), -1)
        cv2.putText(img, f"{t:.1f}s", (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
        r_, c_ = divmod(k, cols)
        sheet[r_ * h:(r_ + 1) * h, c_ * w:(c_ + 1) * w] = img
    cv2.imwrite(str(d / "contact_sheet_1.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return rid, None


def read_grid(path):
    """grid.tsv: optional '#profile<TAB>handle' header, then `id<TAB>views text` lines (copied from a logged-in browser)"""
    handle, rows, links = None, [], {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("#profile"):
            handle = line.split("\t")[1].strip()
        elif "\t" in line and not line.startswith("#"):
            i, v = line.split("\t", 1)
            i = i.strip()
            if i.startswith("http"):                                    # full link (TikTok, YouTube, Facebook ...): the folder id comes from the link
                links[reel_id(i)] = i
                i = reel_id(i)
            try:
                rows.append((i, parse_views(v)))
            except Exception:
                rows.append((i, None))                                  # tile showed something else than views (e.g. likes | comments)
    return handle, rows, links


def expand_channel(url, limit):
    """YouTube channel -> its Shorts links (yt-dlp can list these; Instagram, TikTok and Facebook profiles it cannot: use `reel-intel snippet`)"""
    import re as _re
    if not _re.search(r"youtube\.com/(@[\w.-]+|channel/[\w-]+|c/[\w-]+|user/[\w-]+)/?(shorts|videos)?/?$", url):
        return [url]
    target = url.rstrip("/") + ("" if url.rstrip("/").endswith(("/shorts", "/videos")) else "/shorts")
    r = run(ytdlp() + ["--flat-playlist", "--playlist-end", str(limit), "--print", "%(url)s", target])
    found = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("http")]
    print(f"{target}: {len(found)} links")
    return found or [url]


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)

    def pop(name):
        if name in args:
            i = args.index(name)
            v = args[i + 1]
            del args[i:i + 2]
            return v
    grid, profile, limit = pop("--grid"), pop("--profile"), int(pop("--limit") or CFG.get("limit", 50))
    handle, rows, links = (None, [], {})
    if grid is None:                                                    # a .tsv passed as a plain source is a grid
        tsv = [a for a in args if a.endswith(".tsv")]
        if tsv:
            grid = tsv[0]
            args.remove(grid)
    if grid:
        handle, rows, links = read_grid(grid)
        WORK.mkdir(parents=True, exist_ok=True)
        (WORK / "views.csv").write_text("id,views\n" + "\n".join(f"{i},{int(v)}" for i, v in rows if v) + "\n", encoding="utf-8")
        print(f"views.csv: {sum(1 for _, v in rows if v)} of {len(rows)} reels have a view count")
    urls = []
    for a in args:
        if a.startswith("http"):
            urls += expand_channel(a, limit)
        elif Path(a).exists():
            urls += [l.strip() for l in Path(a).read_text(encoding="utf-8").splitlines() if l.strip().startswith("http")]
    if not urls and rows:
        h = profile or handle
        if links:                                                       # grid with full links (any platform)
            urls = [links[i] for i, _ in rows if i in links]
        elif not h:
            print("grid has no profile name: add --profile HANDLE (or use `reel-intel snippet` output, which includes it)")
            return
        else:
            urls = [f"https://www.instagram.com/{h}/reel/{i}/" for i, _ in rows]
    if not urls:
        print("no links: pass a links file (one URL per line), URLs, or a grid.tsv")
        return
    if len(urls) > limit:
        print(f"{len(urls)} links: scanning the first {limit} (raise with --limit N; gentle limits protect your account and the platform)")
        urls = urls[:limit]
    t0 = time.time()
    todo = [u for u in urls if not (DATA / reel_id(u) / "content_full.md").exists()]
    print(f"{len(urls)} reels, {len(todo)} new, {CFG['workers']} parallel downloads", flush=True)
    failed = []
    with ThreadPoolExecutor(CFG["workers"]) as ex:
        for fu in as_completed([ex.submit(fetch, u) for u in todo]):
            rid, err = fu.result()
            if err:
                failed.append(rid)
                print(f"[{rid}] FAILED: {err[:300]}", flush=True)
                continue
            f = finish(rid)
            print(f"[{rid}] ok: {f['duration_s']:.0f}s, {f['word_count']} words ({time.time() - t0:.0f}s)", flush=True)
    quiet = [u for u in urls if (DATA / reel_id(u) / "features.json").exists() and needs_sheet(jload(DATA / reel_id(u) / "features.json"))]
    if quiet:
        print(f"{len(quiet)} reels without usable speech -> contact sheets", flush=True)
        with ThreadPoolExecutor(CFG["workers"]) as ex:
            for rid, err in ex.map(contact_sheet, quiet):
                print(f"[{rid}] {'sheet ok' if not err else 'sheet FAILED: ' + err}", flush=True)
        for u in quiet:                                                # OCR sequentially (one model), then refresh the content file
            d = DATA / reel_id(u)
            if (d / "contact_sheet_1.jpg").exists():
                ocr_frames(d)
                write_content(d, jload(d / "transcript.json"), jload(d / "features.json"))
    print(f"done in {time.time() - t0:.0f}s, failed {len(failed)}", flush=True)


if __name__ == "__main__":
    main()
