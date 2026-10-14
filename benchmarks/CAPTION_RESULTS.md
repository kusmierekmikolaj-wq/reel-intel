# Real speakers vs human-made subtitles

4 YouTube videos with an uploaded (not automatic) subtitle track, 8553 reference words. WER of the tool's transcript against the subtitles: **8.1% overall**, median per video 6.3%, best 4.5%, worst 13.0%.

Subtitles are edited text, not verbatim (fillers, repetitions and false starts are usually dropped, numbers are written their own way), so this is an **upper bound** on the true error. Languages: en, pl.

Second measure, **subtitle words the tool did not reproduce** (extra words the tool has are not counted, so dropped fillers do not hurt; a wrongly heard or missing word does): **4.0% overall**. Digits written as words in the subtitles (and the reverse) still count as misses.

| video | language | words (tool) | words (subtitles) | WER | subtitle words missed |
|---|---|---|---|---|---|
| yt_eIho2S0ZahI | en | 1638 | 1661 | 4.5% | 3.4% |
| yt_-FOCpMAww28 | en | 1237 | 1231 | 5.7% | 3.2% |
| yt_MYH2qGScEVk | pl | 2838 | 2759 | 6.3% | 3.0% |
| yt_ktjMz7c3ke4 | pl | 3040 | 2902 | 13.0% | 5.7% |
