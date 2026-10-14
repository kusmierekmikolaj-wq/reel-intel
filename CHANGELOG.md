# Changelog

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
