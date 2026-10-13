# Monitoring: notice when a competitor's video pops

`reel-intel watch SOURCE` scans only what is new, compares each new video with the typical video around it and appends a digest to `<data>/digest.md`. A video is flagged when it has at least `--alert` times the typical views (default 5x). The first run only records a baseline.

`SOURCE` can be a YouTube channel (`https://www.youtube.com/@name`), a links file, or a `grid.tsv` that you refresh with `reel-intel snippet` (Instagram, TikTok and Facebook profiles cannot be listed by a program).

```bash
reel-intel --data D:\watch\competitor watch https://www.youtube.com/@name --alert 5
```

## Schedule it

**Windows** (every day at 08:00):
```
schtasks /create /sc daily /st 08:00 /tn "reel-intel watch" /tr "reel-intel --data D:\watch\competitor watch https://www.youtube.com/@name"
```
**macOS / Linux** (`crontab -e`):
```
0 8 * * *  reel-intel --data ~/watch/competitor watch https://www.youtube.com/@name
```
Keep the rate gentle: one run a day is enough for most profiles. Read `docs/LEGAL.md` before monitoring accounts that are not yours.
