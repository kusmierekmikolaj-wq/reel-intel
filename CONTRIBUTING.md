# Contributing

Thanks for helping. The project is small and tested; the quickest way in:

```bash
git clone https://github.com/kusmierekmikolaj-wq/reel-intel
cd reel-intel
pip install -e ".[test]"
pytest                       # about 60 tests, no network, no models
reel-intel demo              # a page from synthetic data
```

## Good first contributions
- **A glossary for your niche or language** (`src/reel_intel/data/glossary.yaml`): names and jargon that Whisper mishears, with a test in `tests/test_core.py`.
- **Platform links** for `docs/platform_urls.txt` (public posts of institutions or media, never private people), then `reel-intel platforms` to refresh `docs/PLATFORMS.md`.
- **A hand-checked transcript** of a public video you may share (see `benchmarks/make_wer_pack.py`): the number this project still lacks is the word error rate on short, noisy reels (long clear talks are measured, see `docs/BENCHMARKS.md`).
- **Bug reports** with the output of `reel-intel doctor` and, if a scan failed, the log line.

## Rules of the house
- Numbers in documents come from the data and say how they were measured; "not measured" is a valid answer.
- Never commit recordings, `cookies.txt`, data folders or anything that identifies a private person (see `docs/LEGAL.md`).
- Every behaviour change gets a test; `reel-intel qa --selftest` must still catch every injected fault.
