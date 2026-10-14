# Benchmarks

What was measured, how, and what it does **not** show. Reproduce with `python benchmarks/run_benchmark.py` (Windows, needs the voices that ship with Windows; about 6 minutes on a GPU). Raw per-clip results and the generated table: [benchmarks/RESULTS.md](../benchmarks/RESULTS.md).

## Accuracy: synthetic corpus with a known reference

24 short scripts written for this test (12 Polish, 12 English, no third-party content) are spoken by Windows voices, so the reference text is exact. Each clip is also tested with pink noise mixed in. Names and jargon (Zapier, Notion, Meta Ads, ROAS, CPM, Midjourney, Canva, Shopify, Claude Code, ...) are included on purpose; the scripts avoid numerals because Whisper writes "50" where a script says "fifty", which would count as an error although the word was heard.

| configuration | WER pl (clean / noisy) | WER en (clean / noisy) | name & jargon recall |
|---|---|---|---|
| tiny, no prompt | 15.4% / 25.5% | 4.9% / 6.2% | 47% / 41% |
| **clipscribe 0.1.1 defaults** (tiny, CPU, no voice filter, no prompt; reproduced from its source) | 16.9% / 23.1% | 5.1% / 6.2% | 44% / 44% |
| small, no prompt | 4.2% / 5.6% | 2.8% / 3.1% | 59% / 56% |
| large-v3, no prompt | 2.1% / 2.7% | 1.0% / 0.8% | 82% / 79% |
| **large-v3 + glossary and caption names (reel-intel)** | **1.2% / 1.2%** | **0.3% / 0.5%** | **91% / 94%** |

Reading it:
- **The prompt matters most for names and jargon**: 71% -> 93% for terms the default glossary lists, but only 88% -> 92% for names that come from the caption alone. The caption mechanism helps a little; a glossary tuned to your niche helps a lot (copy `glossary.yaml` into your data folder).
- **The model size matters most for Polish**: tiny is unusable for Polish (15-26% WER); `small` is a reasonable CPU choice.
- **Language detection was right 24/24** in every configuration (clean and noisy).
- **About clipscribe.** The open-source tool `clipscribe` (v0.1.1, MIT) transcribes TikTok and Instagram audio with exactly `WhisperModel("tiny", device="cpu", compute_type="int8")` and `transcribe(audio, beam_size=5)`. We reproduced those settings from its source (we read its code; we did **not** run the package), so the row shows what its defaults do on this corpus, not a measurement of the package itself. It can be pointed at a larger model; with `small` or `large-v3` the rows above apply. The difference to reel-intel comes from the model size, the per-language glossary prompt and the caption names, not from anything exotic.

## What this does not show

- **Real speakers.** Synthetic voices are clean and evenly paced. Real reels have fast speech, accents, music and overlapping sound, so absolute WER will be higher than the table. A benchmark on real reels needs a human-checked reference transcript for each one; that has not been done, and until it is, the honest statement is: *on clean synthetic speech the pipeline is at 1% WER, and the glossary mechanism is the measurable gain; real-world accuracy is not yet measured.*
- **Real-data checks we do have**: the verifier (`reel-intel qa`) is run on every reel in six real accounts (262 videos). It found a genuine loss of speech and led to a fix for English clips detected as Welsh (see [QUALITY.md](QUALITY.md)); the synthetic benchmark could not have found either.

## Real speakers: what we could and could not measure

**No human-made reference was available.** The 6 accounts' YouTube videos list 30 "subtitle" tracks, but every one is YouTube's own automatic speech recognition (the track URLs carry `caps=asr`), not text typed by a person. Hand-checked transcripts of 16 real videos were prepared (`benchmarks/make_wer_pack.py`) but nobody has had time to correct them, and the assistant that wrote the tool cannot listen to audio, so **real-speaker word error is still not measured**.

What was done instead is a cross-check, which is not accuracy: the tool's transcript against YouTube's automatic captions on 21 videos before the fixes below: 15.4% word disagreement overall, median 11.4%, 4 videos at 0-1% (clean, close-up speech), the worst two at 43% and 110%. Two systems disagreeing says nothing about which is right, but a large gap is a lead worth checking: the 43% video turned out to be **our** failure (about 60 spoken words skipped inside one segment, see [QUALITY.md](QUALITY.md)), now detected and recovered. Reproduce with `benchmarks/caption_check.py` (it now refuses automatic tracks, so it needs videos with real uploaded subtitles).

## Text on screen (OCR, for videos without speech)

40 synthetic frames (white text with a dark outline over a noisy photo-like background, 540x960 like the stored frames; [benchmarks/ocr_benchmark.py](../benchmarks/ocr_benchmark.py), [OCR_RESULTS.md](../benchmarks/OCR_RESULTS.md)). CER = character error rate.

| text | raw OCR | + Polish letters restored |
|---|---|---|
| English (20 lines) | 0.3% (19/20 lines exact) | not needed |
| Polish, held-out lines (20), new account (built-in word list only) | 7.1% | 4.4% (5/20 exact) |
| Polish, held-out lines, account with 33 videos of its own vocabulary | 7.1% | 3.7% (6/20 exact) |

The OCR model reads English perfectly and Polish **without its accents** ("zeby", "moze"); the tool restores them from the account's own words, a small built-in list of common Polish words and suffix rules. That roughly halves the error but does not remove it: about one Polish line in four comes out exact, and the rest differ by a missing accent. A recognition model that knows Latin accents would fix it at the source (not done: it is an extra download). Real video frames are harder than this synthetic text (motion blur, stylised fonts), so these figures are an upper bound. Videos with speech are not affected: their text comes from the transcript.

## Text on screen: repositories, skill names, commands

For videos that show a terminal, a slide or a browser, `reel-intel visuals` reads two frames a second (OCR) and `reel-intel report screen` lists what looks like a GitHub repository (`github.com/owner/name`, or a bare `owner/name` next to words such as GitHub, repo, npx, plugin marketplace), a skill or tool name (a hyphenated identifier only when a word like skill, plugin, MCP, agent or npx is on the screen at the same moment), a slash command or a file name (`SKILL.md`, `CLAUDE.md`).

Test ([benchmarks/screen_benchmark.py](../benchmarks/screen_benchmark.py)): 48 synthetic frames (8 contents x 3 text sizes x dark and light), monospace text on a plain background, read with the same code as the scanner.

| | found | expected |
|---|---|---|
| GitHub repositories | 36 | 36 |
| skill names, commands, files | 36 | 36 |
| false alarms (reported but not on screen) | 0 | |

That is clean synthetic text, so it shows that the chain can work, not how well it works on real recordings (compression, motion, scrolling, small fonts). Everything it reports is a **candidate with a time stamp** to check in the video. On the first real account (33 marketing reels) it found no repository, which is correct for that content. Cost: about 36 seconds per one-minute video, on the CPU.

## Long recordings

One public 37-minute video (a space-agency series episode) transcribed from its audio only: 2,202 s of audio, 6,226 words, 770 s in total including the download (about 3x faster than real time on an RTX 2080 Ti). Words per 5-minute stretch were even (780-900), so no stretch was skipped, and the verifier reported no error after a fix (it had flagged a phrase that simply occurs three times in 37 minutes; a Whisper loop is the same segment repeated one after another, which is what it checks now). **Not verified:** accuracy on this recording (there is no human reference), a one-hour recording, and a Polish long video.

## Speed

Machine: NVIDIA GeForce RTX 2080 Ti (11 GB), faster-whisper 1.2.1, CTranslate2 4.8.2, Python 3.12, Windows 11.

| measurement | result |
|---|---|
| model time per clip (language detection + transcription, 12.6 s of audio) | tiny 0.6 s, small 0.8 s, large-v3 1.7 s |
| whole scan, 30 YouTube Shorts (26.5 min of video), 4 parallel downloads | 215 s (7 s per video, 7x faster than real time) |
| one Instagram reel with speech, download + transcription + write-up | about 5 s (measured on the first profile) |

Most of the time per video is the download, not the model; only the audio track is fetched for the transcript (the optional picture analysis, `reel-intel visuals`, also fetches a small copy of the video). Without a GPU the default is `small` with int8 on the CPU.
