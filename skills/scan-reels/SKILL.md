---
name: scan-reels
description: Get the exact content of Instagram reels (verbatim speech with timestamps, caption, metrics) plus a short version with the essentials. Use when the user gives reel links or a profile and wants transcripts, "tresc" of reels, a scan, or material to create similar content.
---

# scan-reels

Goal: for every reel produce `content_full.md` (verbatim, automatic) and `content_short.md` (the essentials, written by you).
Everything runs locally; `reel.py` is the only entry point. Data goes to `./reel-data` (or `$REEL_DATA`).

```
PY="python"   # on Windows `py` also works
R="${CLAUDE_PLUGIN_ROOT}/scripts/reel.py"
```


**Shortcut:** `python "$R" run urls.txt --open` (or `run grid.tsv --profile HANDLE`) does scan, quality check and page in one command; use the steps below when you need to intervene. `python "$R" doctor` explains any setup problem.

## Steps
1. **Setup (once per machine):** `python "$R" setup`. Needs Python 3.10+ and ffmpeg on PATH. Takes a few minutes (installs Whisper).
2. **Get the reel links.** If the user pasted links, put one per line in `urls.txt`. If they gave a profile, use the `collect-reel-links` skill (yt-dlp cannot list a profile by itself).
3. **Scan:** `python "$R" scan urls.txt` (add `--grid grid.tsv` if the collect step produced profile-grid view counts). ~5 s per reel.
4. **Verify:** `python "$R" qa` and read `reel-data/qa_report.md`. Fix every ERROR (the table says how). WARNs about music-only reels are expected. Run `python "$R" qa --selftest` after changing any script.
5. **Short versions.** `python "$R" shorten` first writes an automatic extract (hook, key sentences, CTA, on-screen text; the file carries a `<!-- auto -->` line) for every reel that has none: that is already usable. **Then write a better `content_short.md` for each reel** (template below); a file without the `<!-- auto -->` line is never overwritten by the tool. Never run `shorten --overwrite-manual`. Source of truth is `reel-data/data/<id>/content_full.md`, never memory or guesses.
   - Reels with speech: read the transcript + caption only. Do **not** open images (slow and unnecessary).
   - Reels flagged `V_VISUAL_REEL` (no usable speech): `content_full.md` section 2b already holds the OCR text of 12 frames (Polish diacritics are missing in OCR: restore them and say so). Open `contact_sheet_1.jpg` only when the OCR is unclear and the reel is among the top ones; the caption often carries the message too.
6. **Index:** `python "$R" report` writes `reel-data/INDEX.md` (sorted by views, x median, x rolling median, links). `python "$R" report clean` lists reel folders from failed scans (add `--yes` to delete).
7. **Readable page for anyone:** `python "$R" page` builds `reel-data/Reele.html`, a searchable, sortable page of all reels (headline numbers, topic chart if `topics.csv` exists, advice, full text per reel). It is computed from the data, so it works for any profile or any list of links. Offer to publish it as a private web page (Artifact) with `page.fragment.html` so others can open it without installing anything.
8. Tell the user the counts (scanned / failed / visual reels) and anything uncertain.

## Accuracy rules (the point of this plugin)
- Quote speech and captions **verbatim**. Never fix wording silently: glossary corrections are shown in `content_full.md` section 3.
- Words with confidence < 0.5 are listed there: mark them "(niepewne)" if you quote them.
- A reel whose speech is flagged unreliable (music) must not be quoted from the transcript.
- Do not claim to have watched a video. For reels where you did not open the sheet, say so.
- Hidden or unknown views stay unknown; never invent a number.

## content_short.md template
```
# <id> - wersja skrócona
**<views> wyświetleń (<x>x mediana) | <likes> lajków | <comments> komentarzy | <s> s | <data>**
Pełna treść dosłowna: `content_full.md`

**O czym:** <one sentence>
**Treść:** <the actual content: steps / list / argument, in the author's own words where possible>
**Hook (0-3 s):** <verbatim first sentence>
**CTA:** <keyword + what is promised, or "brak">
```
