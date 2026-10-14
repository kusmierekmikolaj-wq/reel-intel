<p align="center"><img src="assets/logo.svg" width="112" alt="reel-intel logo"></p>

<h1 align="center">reel-intel</h1>

**Local-first short-video intelligence.** Paste links (Instagram Reels, TikTok, YouTube Shorts, X, Facebook) and get the exact words said in every video, verified numbers, and an honest answer to *why some videos pop*, as a page anyone can read. Free. No API keys. Everything stays on your computer.

*Po polsku: [README.pl.md](README.pl.md)*

```bash
pipx install git+https://github.com/kusmierekmikolaj-wq/reel-intel   # or: pip install .
reel-intel demo            # a page built from synthetic data: no account, no network
reel-intel doctor          # checks Python, ffmpeg, GPU, yt-dlp, network
reel-intel serve           # local app: paste links, watch progress, read the page
reel-intel run links.txt --open
```

## What you get
- **Exact content of every video**: speech with timestamps (Whisper on your GPU or CPU), caption, views / likes / comments. Videos without speech are read from their frames.
- **Quality you can check**: a separate verifier (`reel-intel qa`) catches cut-off transcripts, Whisper loops, wrong numbers and stale data, and proves it with a self-test that injects 15 known faults (`reel-intel qa --selftest`). See [docs/QUALITY.md](docs/QUALITY.md).
- **An honest "why did it pop"**: median-based outliers; topic and format groups with robustness checks (drop the top video, drop the top three); a label-free feature table with **99% bootstrap intervals** (a difference is "clear" only when the interval excludes 1); posting-time and comment tests with permutation p-values; a plain-language page (Polish or English) with a search box and the full text of every video.
- **Not only the transcript**: `reel-intel visuals` measures the picture and sound (editing pace, a face in the first seconds, text on screen, captions burned into the picture, speech pace, loudness) and adds them to the same feature table; burned-in captions double as an independent check of the speech.
- **Everything written on the screen** (`reel-intel visuals`, `reel-intel report screen`): GitHub repositories, skill and tool names, commands and files are read from the video frames of every video, not only the silent ones, and listed with the minute they appear (candidates: OCR errs, so check them).
- **Long recordings**: a 30-60 minute video is transcribed from its audio only, never from the platform's automatic captions (a 37-minute test took about 13 minutes on a mid-range GPU).
- **Compare accounts**: `reel-intel compare` shows which patterns repeat across profiles and which are one account's style. In our test on 6 accounts (262 videos) no format feature repeated, which is itself the point: see [docs/CASE_STUDY.md](docs/CASE_STUDY.md).
- **Fast**: about 5 s per video for the transcript (only audio is downloaded), plus a few seconds for the picture analysis (a small copy of the video, 480p at most; `--no-visuals` skips it), 4 in parallel. On clean synthetic speech the pipeline reaches about 1% word error and 91-94% recall of names and jargon; real-speaker accuracy is not yet measured: [docs/BENCHMARKS.md](docs/BENCHMARKS.md).
- **Works with Claude, does not need Claude**: a Claude Code plugin, an MCP server and a standalone CLI share the same code.

## Commands
| command | what it does |
|---|---|
| `run SOURCE [--profile H] [--limit N] [--lang pl\|en] [--open]` | scan, quality check and page in one go. `SOURCE`: links file, URLs, a YouTube channel, or a `grid.tsv` |
| `serve [--library DIR]` | local app in your browser (localhost only): links in, progress, result page; with `--library`, a panel of finished analyses (every sub-folder of DIR that has a `Reele.html`, plus `COMPARE.md`), optionally named by `DIR/labels.json` |
| `extension/` | a Chrome/Edge extension: one click on a profile page collects the links and view counts and starts the scan in the local app ([extension/README.md](extension/README.md)) |
| `snippet` | prints a browser-console snippet that copies a profile's links and view counts (`grid.tsv`) from your logged-in browser |
| `demo`, `doctor` | try it with synthetic data; health check with one-line fixes |
| `scan`, `shorten`, `qa`, `report`, `page` | the individual steps. `shorten`: an automatic short version of every video (hook, key sentences, call to action, on-screen text; every line is quoted from the recording). `report`: `groups`, `timing`, `hooks`, `features`, `texts`, `content`, `screen`, `csv`, `clean` |
| `compare FOLDER FOLDER ...` | several profiles side by side: what repeats |
| `score script.txt` | check your own script against what your data clearly shows and against the most similar scanned videos |
| `watch SOURCE [--alert 5]` | scan what is new, flag videos that pop, append to `digest.md` ([docs/MONITORING.md](docs/MONITORING.md)) |
| `insights EXPORT.csv` | add what only the account owner sees (reach, saves, shares, watch time) from the platform's own analytics export |
| `platforms` | re-test which platforms work today on real public links |
| `mcp` / `reel-intel-mcp` | MCP server (stdio) for Claude Desktop, Cursor: `{"mcpServers": {"reel-intel": {"command": "reel-intel-mcp"}}}` |

Data lives in `--data DIR` (or `$REEL_DATA`, default `./reel-data`): one folder per video with `content_full.md` (verbatim record), `content_short.md`, `features.json`, `transcript.json`; `report texts` joins everything into one readable file (`TEXTS.md` / `SHORTS.md`). Interface language: `--lang pl|en` or `ui_language` in `<data>/config.yaml`. Your own names and jargon: copy `glossary.yaml` into the data folder.

## Platforms (tested on real public links, not assumed)
| platform | single links | whole profile |
|---|---|---|
| Instagram Reels | 3/3 | `snippet` in your logged-in browser |
| YouTube Shorts | 3/3 | channel URL works directly |
| X (Twitter) | 3/3 | links or `snippet` |
| TikTok | 1/1 (needs `curl-cffi`, installed with the package) | `snippet` |
| Facebook | 1/2 (plain videos work, one reel did not) | `snippet` |

Details and the date of the last test: [docs/PLATFORMS.md](docs/PLATFORMS.md) (`reel-intel platforms` regenerates it). Some posts need a logged-in session (`cookies.txt`, `cookies_file` in `config.yaml`).

## Use with Claude Code
```
/plugin marketplace add kusmierekmikolaj-wq/reel-intel
/plugin install reel-intel@reel-intel
```
Skills: `scan-reels` (exact content + short versions), `collect-reel-links` (reads a profile's links from your logged-in Chrome), `why-it-went-viral` (analysis and "make a similar one").

## Honest limits
- Platforms change often; `yt-dlp` fixes follow within days (`reel-intel doctor` and the automatic one-time update help).
- Retention, saves and shares are not public; conclusions are **hypotheses with confidence labels**, not proof. Samples of 30-40 videos per profile are small and the page says so.
- Platform terms restrict automated collection. Read [docs/LEGAL.md](docs/LEGAL.md). Use public profiles, gentle rates, and do not republish other people's recordings or texts.

## Development
`pip install -e ".[test]" && pytest`. Demo data is regenerated by `scripts/make_demo.py`. CI runs the tests on Ubuntu and Windows (Python 3.10 and 3.12). Changes: [CHANGELOG.md](CHANGELOG.md). Contributing: [CONTRIBUTING.md](CONTRIBUTING.md).

MIT licensed.
