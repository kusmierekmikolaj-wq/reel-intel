# Contributing

Thanks for helping. The project is small and tested; the quickest way in:

```bash
git clone https://github.com/kusmierekmikolaj-wq/reel-intel
cd reel-intel
pip install -e ".[test]"
pytest                       # about 500 tests, no network, no models; the ones that need ffmpeg, Pillow or real reels skip without them
REEL_TEST_ROLKI=path\to\rolki pytest   # optional: a folder with a reel made by `make render` (rolka_a.mp4, .qa.json, _voice.wav) switches on the tests on real files
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
- Every behaviour change gets a test; `reel-intel qa --selftest` must still catch every injected fault, and `reel-intel make qa --selftest` (the fault-injection matrix of the reel check, docs/QA.md) must still report every injected fault with no false alarm on a clean reel. A new fault code of the reel check needs a detector, a Polish label in `quality/verdict.py`, an injection in `quality/matrix.py` and a line in docs/QA.md (`python -m reel_intel.quality.docs` writes the tables).
