# Benchmark results

Corpus: 24 scripts (12 Polish, 12 English), spoken by Windows SAPI voices, reference = the script itself. Clean audio and the same audio with pink noise (about 12 dB below the speech peak). WER = word error rate after lower-casing and removing punctuation (lower is better); term recall = share of product names / jargon that appear correctly. **Synthetic speech is cleaner than real people: absolute numbers are optimistic, differences between rows are the point.**

| configuration | audio | WER pl | WER en | term recall | language detected right |
|---|---|---|---|---|---|
| tiny | clean | 15.4% | 4.9% | 47% (16/34) | 24/24 |
| tiny | noisy | 25.5% | 6.2% | 41% (14/34) | 24/24 |
| small | clean | 4.2% | 2.8% | 59% (20/34) | 24/24 |
| small | noisy | 5.6% | 3.1% | 56% (19/34) | 24/24 |
| large-v3 | clean | 2.1% | 1.0% | 82% (28/34) | 24/24 |
| large-v3 | noisy | 2.7% | 0.8% | 79% (27/34) | 24/24 |
| large-v3 + prompt | clean | 1.2% | 0.3% | 91% (31/34) | 24/24 |
| large-v3 + prompt | noisy | 1.2% | 0.5% | 94% (32/34) | 24/24 |
| clipscribe defaults (tiny, CPU, no VAD, no prompt) | clean | 16.9% | 5.1% | 44% (15/34) | 24/24 |
| clipscribe defaults (tiny, CPU, no VAD, no prompt) | noisy | 23.1% | 6.2% | 44% (15/34) | 24/24 |

### Term recall: terms the default glossary already knows vs names that only the caption supplies

| configuration | in glossary | only in caption |
|---|---|---|
| tiny | 46% (13/28) | 42% (17/40) |
| small | 57% (16/28) | 57% (23/40) |
| large-v3 | 71% (20/28) | 88% (35/40) |
| large-v3 + prompt | 93% (26/28) | 92% (37/40) |
| clipscribe defaults (tiny, CPU, no VAD, no prompt) | 43% (12/28) | 45% (18/40) |

## Speed

Seconds of processing per clip (language detection + transcription, model already loaded), on this machine (see `reel-intel doctor`). Clips are 10-20 s of speech, so this is a lower bound for the model's cost, not the full scan time per video (download dominates).

| configuration | median s / clip | audio s per clip |
|---|---|---|
| tiny | 0.59 | 12.6 |
| small | 0.81 | 12.6 |
| large-v3 | 1.70 | 12.6 |
| large-v3 + prompt | 1.69 | 12.6 |
| clipscribe defaults (tiny, CPU, no VAD, no prompt) | 1.23 | 12.6 |

## Worst clips (large-v3 + prompt)

| clip | audio | WER | heard |
|---|---|---|---|
| pl09 | noisy | 44% | Dobry w magnę rozwiązuje jeden mały problem kilka minut. Nieróg długie go poradnika, z ruch hec kliste na jedną stronę, którą ktoś naprawdę  |
| pl07 | noisy | 40% | całkut wystarczy jedną kliknięcie, żeby dodać napisy. Ale prawdziwa różnicatorytm, nic często usuwaj każdą pału ze i został tylko to, co nap |
| pl07 | clean | 36% | w całkut wystarczy jedno kliknięcie, żeby dodać napisy. Ale prawdziwa różnicatorytm, tniczęsto, usławaj każdą pału ze i zostaw tylko to, co  |
| pl09 | clean | 36% | Dobry lid magne rozwiązuje jeden mały problem w kilka minut. Nie rób długie go poradnika, zrób chec kliste na jedną stronę, którą ktoś napra |
| pl01 | noisy | 31% | Cześć, w tym filmie w okarzeci, jak kilka minut połączyć za pierzną uszę. Każdy nowy lid z formularza Londuje automatycznie w swojej wazie,  |
