# Quality: how the verifier is tested

`reel-intel qa` checks every scanned video (transcript, numbers, files, glossary) and the set as a whole. This page says what it catches, how that was tested, what it got wrong, and what it cannot see.

## 1. It catches the faults we know how to inject

`reel-intel qa --selftest` copies one real scanned video, breaks it in 15 known ways and checks that the verifier raises the matching code. It must say `ALL CAUGHT`.

| injected fault | expected code |
|---|---|
| transcript file deleted / content file missing | `MISSING_transcript`, `MISSING_content_full` |
| transcript cut off (speech missing at the end) | `T_TAIL_GAP` |
| Whisper loop (the same segment repeated) | `T_REPEAT` |
| Cyrillic text in a Polish/English reel | `T_FOREIGN_SCRIPT` |
| low word confidence | `T_LOW_CONF` |
| known mishearing (glossary) / a Polish fix wrongly hitting an English reel (must **not** fire) | `T_GLOSSARY` |
| transcript made without the current glossary + caption names | `T_PROMPT_STALE` |
| likes > views, views differ from the table, stale features | `X_LIKES_GT_VIEWS`, `X_VIEWS_MISMATCH`, `X_STALE` |
| unreadable audio | `A_UNREADABLE` |
| reel without speech and without a contact sheet | `V_NO_SHEET` |

Run on the data of all six profiles (Polish and English, Instagram and YouTube): **14/14 caught on each**. The run on the third profile first showed 13/14: the "cut off" fault left fewer than 10 words, which the verifier by design treats as a reel without speech. That was a flaw in the test (it now keeps at least 12 words), not in the verifier, but it is the reason the test is run on more than one dataset.

## 2. What it said on real data, and which alarms were false

Real videos: 262 in six accounts. The first full run on the first 103 produced warnings, and the 159 videos added later produced more; each warning type was checked by hand.

| warning | first run | verdict | change |
|---|---|---|---|
| `T_TAIL_GAP` (transcript ends over 6 s before the audio) | 9 videos | **1 real loss**: on one Short, ~20 s of narration over music had been dropped by the voice-activity filter. 8 were false alarms (silence, music, or a sign-off that Whisper invents over silence, such as "Thanks for watching") | the transcription now re-examines such a gap **without** the voice filter and keeps only segments that look like speech (low no-speech probability, decent confidence); a gap that survives is reported as `T_TAIL_SILENT` (info) |
| language detection (not a warning: found by reading the languages table of `reel-intel compare`) | 4 videos | **real errors**: English clips detected as Welsh (3) or Khmer (1) with 31-63% confidence, so the "transcript" was nonsense in the wrong language | when detection is unsure (under 80%), the language of the account (most of its other videos) is used if the model gives it at least 10%; speech in an odd language with under 50% confidence is marked unreliable. A real Welsh talk (92% confidence) stays Welsh. Later a UK English TikTok was heard as Welsh with 83% confidence: when the caption is clearly English or Polish (function words) and the model gives that language at least 10%, the caption wins below 95% confidence |
| `T_REPEAT` (same segment 3+ times) | 2 videos | both were **speakers repeating a word** (a "true or false" round, an emphatic "disregard"), one hidden by the wrong language above | a phrase repeated is still an error; one or two words repeated are a warning, an error only if said 6+ times with shaky confidence |
| speech skipped **inside** a segment (found by comparing with YouTube's automatic captions, not by a warning) | 1 video first, then 9 flagged by the new check | **real losses**: on a NASA Short one segment held "We have" at 47 s and "a lot of people" at 73 s, with about 60 spoken words missing between them (115 words instead of 186). 8 other videos showed the same signature; rescanned, they gained 0 to 26 words (one Polish video went from 34 to 49 words) | `T_HOLE`: more than 6 s without a word inside one segment. Transcription now cuts out each such stretch, transcribes it on its own (no voice filter, no carry-over from earlier text), splices it back in order, and collapses a recovered stretch that merely repeats one phrase (Whisper looping on music). `T_HOLE_SILENT` (info) when the re-check finds nothing. Autotest case added (15 injected faults now) |
| `T_WPM` (implausible speaking rate) | 2 | 1 false: one spoken sentence in a 70 s clip; rate was computed over the clip length | the rate is computed over the speaking span |
| `T_EMPTY` (no speech) | 22 | expected: music/visual reels, content comes from the contact sheet | info when a contact sheet exists, warning when it does not |
| `MISSING_content_short` | 70 | not a fault: short versions are written by the Claude skill, absent in CLI-only use | info |
| `T_GLOSSARY` | 1 | true: a known mishearing, corrected in the shown text | none |

After the changes: 0 errors on all six profiles, 4 warnings in total (a true glossary hit and three low-confidence or speaking-rate notes worth a look). **Be careful reading that number**: part of the drop comes from relabelling alarms that were noise, which is justified above, but it is not a measurement of "fewer faults".

## 3. What it cannot see

- **A wrong word that Whisper is confident about.** The verifier looks at confidence, glossary mishearings, structure and numbers; it cannot know that "Midjourney" was heard as "Midiornej" unless it is in the glossary. The synthetic benchmark ([BENCHMARKS.md](BENCHMARKS.md)) puts name/jargon recall at 91-94% with the prompt, so some errors of this kind remain.
- **Numbers that are wrong at the source.** It checks internal consistency (likes vs views, table vs file), not that the platform's number is true. Views from a logged-out session can be stale; the tool reports the date of each reading.
- **Whether a conclusion is right.** Statistics carry their own safeguards (99% intervals, robustness without the top videos, "unclear" labels); see the case study.

## 4. Reproduce

```bash
reel-intel qa --selftest     # needs a data folder with at least one scanned video that has speech
reel-intel qa                # the real check
```
