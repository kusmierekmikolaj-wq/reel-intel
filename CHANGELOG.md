# Changelog

## Unreleased
- Quality control of generated reels (`reel-intel make qa`, run after every `make render`): a register of 65 codes for the known faults of text-to-speech and short video (words said wrong, skipped or repeated, breaths, a dragged or chopped last word, pauses inside a sentence, pace, pitch, loudness to EBU R128, true peak, clipping, pumping music, dropouts, dead first frame, black and frozen picture, flicker, text outside the safe area or over other text, captions too fast, caption and mouth sync, file format), each with its own detector, severity and repair; a verdict (gotowe / z uwagami / niegotowe, exit code 0 / 10 / 1) and a Polish report `<reel>_kontrola.md`. What can be repaired is repaired and checked again, up to 3 rounds. Thresholds come from real recordings (`make calibrate`); `make note` and `make learn` tune the checker to what a person heard. Fault-injection matrix: 72 injected faults, all reported, no false alarm on a clean reel. See docs/QA.md.
- Reels are mastered to the loudness of the reference account (-14.3 LUFS, true peak under -1 dBTP); the sidecar `<reel>.qa.json` is version 2 (voice engine and renderer used, words heard, checksum of the mp4); a silent fall-back to another voice engine or to the simple renderer is reported.
- Layout of the animated reel computed from the safe area (studio/src/layout.ts): the caption card no longer runs into the slogan, nothing stands in the bottom interface of Reels, two scenes are never on screen at once, the first frame (the cover) carries the mascot, the slogan and the caption. Measured on 81 real stills: 42 violations before, 0 after.
- Voice engines (Chatterbox with voice cloning, Piper, edge-tts, Windows), pronunciation that learns from speech recognition, automatic pace fix, music bed with ducking, reel check (`make qa`) with a self-test of 8 injected faults.
- Mascot for `make render` (Remotion): mouth shapes from the sounds, word times from the voice, eyebrows, waving, gestures on emphasised words.
- `reel-intel make` (brief, check, render): new reels from the data, voice-over and animated captions; app section "Reels"; optional Remotion project in `studio/`. See docs/MAKE.md.

## 0.4.1 (2026-10-14)
- `reel-intel report digest`: one file for an AI agent (speech, screen text, caption, names to check); `run` writes it and the app links it.
- Screen reading samples two frames a second plus the last frame (a list scrolling past in a second was missed).
- `reel-intel run` and the local app now also write the on-screen report; the app links it under the result ("What was on screen").
- Text on screen for every video: GitHub repositories, skill and tool names, commands and files (`reel-intel visuals`, `reel-intel report screen`).
- Long recordings tested (37 minutes, audio only); the repeat check no longer flags a phrase that merely recurs in a long talk.
- Benchmarks for both.

## 0.4.0 (2026-10-13)
- `reel-intel shorten`: automatic short version of every video (hook, key sentences, call to action, on-screen text), never overwrites a hand-written one.
- Content files and text exports follow the interface language; word timestamps replace stretched segment ends.
- Transcription: recovers speech lost to the voice filter, language detection prefers the account's language and the caption's language when unsure.
- Quality check: short confident repeats are warnings, speaking rate over the speaking span, tail gaps re-checked.
- OCR: Polish accents restored from a built-in word list, suffix rules and the account's own words.
- Transcription: finds and recovers speech skipped inside one segment (`T_HOLE`); the autotest injects 15 faults.
- `reel-intel visuals`: picture and sound features (cuts, face, text on screen, burned-in captions, loudness, brightness) in the feature table; QA note when burned-in captions and transcript disagree; `run` does it by default (`--no-visuals` skips).
- Local app: `serve --library DIR` shows finished analyses and the account comparison next to the scan form.
- Logo (`assets/`): vector master, PNGs, extension icons, social preview; shown in the app and the READMEs.
- Browser extension (`extension/`): one click on a profile page sends links and view counts to the local app; the app accepts extension requests only.
- MCP server: `shorten` tool, clear errors for missing arguments.
- Benchmarks (`benchmarks/`), case study on 6 accounts, platform matrix on institutional accounts.

## 0.3.0
Platform matrix, local app, feature table with bootstrap intervals, `compare`, `score`, `watch`, `insights`, MCP server.
## 0.2.0
Package, CLI, demo data, tests and CI.
