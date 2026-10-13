# Case study: do "viral" patterns repeat across accounts?

Six public accounts, 262 short videos, one question: **is there a format feature that predicts more views on more than one account?** Only aggregate numbers are published here; no recordings, transcripts or captions of other people's videos. Accounts are anonymised.

## Method

| | A | B | C | D | E | F |
|---|---|---|---|---|---|---|
| platform, language | Instagram, Polish | Instagram, English | YouTube Shorts, English | YouTube Shorts, Polish | YouTube Shorts, English (a few Spanish, Welsh) | YouTube Shorts, English |
| kind of account | marketing and tech tips (a person) | paid advertising (a person) | a space agency | an online marketplace | a talks organisation | design software |
| videos with known views | 33 | 40 | 41 | 28 | 60 | 60 |
| typical video (median views) | 30.8k | 2.4k | 680k | 103k | 42k | 1.4k |
| best video, as a multiple of typical | 39x | 88x | 8x | 14x | 11x | 699x |
| videos at 5x typical or more | 6 | 6 | 3 | 7 | 2 | 12 |
| share of all views held by the top 10% of videos | 54% | 72% | 38% | 40% | 35% | 77% |

Every video was scanned with `reel-intel run` (speech to text, caption, numbers), checked with `reel-intel qa` (0 errors), and compared with `reel-intel compare`. For each feature the tool reports the ratio of the typical (median) views of videos with it to those without, with a **99% bootstrap interval**; a feature is "clear" only when the interval excludes 1. A feature needs at least 5 videos on each side to be tested ("too few" otherwise). Features are computed from the data, not labelled by hand. From the transcript and caption: length, speech or text only, a question or a number in the first sentence, a call to action, caption length, speech pace, posting hour and weekday. **From the picture and sound, not the transcript** (`reel-intel visuals`, measured on a small copy of each video): editing pace (cuts per 10 seconds), a face in the first 3 seconds, text on screen in the first 2 seconds, captions burned into the picture. Accounts C-F are the first 28-60 Shorts the channel lists, which is all that C and D have.

## Result

**No feature is clear in two or more accounts.**

| feature | A | B | C | D | E | F |
|---|---|---|---|---|---|---|
| short video (up to 15 s) | unclear | **2.5x** (1.4-9.9) | too few | too few | too few | too few |
| long video (over 45 s) | unclear | **0.5x** (0.2-0.8) | unclear | too few | too few | unclear |
| with speech (not only text/picture) | unclear | **0.5x** (0.1-0.7) | unclear | too few | too few | too few |
| question in the first sentence | unclear | unclear | too few | unclear | unclear | unclear |
| call to action in the caption | unclear | unclear | too few | too few | too few | unclear |
| long caption (over 300 characters) | unclear | **2.2x** (1.05-9.5) | unclear | unclear | too few | **0.5x** (0.02-0.9) |
| posted after 17:00 | unclear | unclear | unclear | too few | too few | unclear |
| fast speech (over 170 words/min) | unclear | too few | unclear | too few | unclear | unclear |
| fast editing (over 4 cuts per 10 s) | unclear | too few | unclear | too few | unclear | unclear |
| a face in the first 3 s | unclear | unclear | unclear | unclear | unclear | unclear |
| text on screen in the first 2 s | too few | too few | unclear | too few | too few | unclear |
| captions burned into the picture | unclear | unclear | unclear | unclear | too few | unclear |
| posting hour vs views (p) | 0.87 | 0.22 | 0.54 | 0.79 | 0.59 | 0.31 |
| weekday vs views (p) | 0.78 | 0.11 | 0.26 | 0.98 | 0.15 | 0.52 |

The sharpest example is **caption length**: clear and positive on B (2.2x), clear and negative on F (0.5x). The same feature, opposite effects, on two accounts that both post in English. Account B shows four clear features that agree with each other (short, no speech, long caption); the other five accounts show one clear feature in total. Account A's one strong pattern is a **topic**, not a format: videos about "technology for everyone" had 7.8x the typical views (7.1x without the best video, 5.4x without the best three).

**The picture and sound did not change the answer.** Five signals that do not come from the transcript were added after a reviewer warned that a transcript alone is not enough. None is clear on any of the six accounts (a face in the first 3 seconds, for instance, has ratios between 0.66x and 1.12x, every interval including 1). Across the 259 videos measured: 69% carry burned-in captions, 58% show a face in the first 3 seconds, and 92% have some text on screen in the first 2 seconds (including logos and watermarks), so the last feature separates few videos and tells little. On the 166 videos with burned-in captions the text read from the picture shares a median of about 64% of its letters with the transcript, an independent check of the speech; the low cases were captions in another language (a Welsh talk with English subtitles) or edited text.

## What this means

1. **Success is concentrated.** In every account a few videos hold most of the views (35-77% in the top 10%); on F one video has 699 times the typical views. Averages hide this; the tool reports medians.
2. **Format rules do not travel.** What worked on B did not show up on the others, and long captions flipped sign. A pattern from one account is that account's style or audience until it repeats elsewhere. Six accounts still found none that repeats.
3. **Topic looked stronger than format** where topics were labelled: A's best topic group had 7.8x the typical views and survived dropping the top videos; B's best group had 3.0x; the YouTube accounts were not labelled.
4. **Posting time did not matter** in any account (smallest p-value 0.11), even though it is the most repeated advice.

## Limits

- Fourteen features were tested on six accounts (84 tests at a 99% level), so about one "clear" result would be expected by chance alone; the rule that a pattern must repeat in two accounts guards against that. The face detector is a basic OpenCV one (frontal faces, some false alarms) and cut counting samples four frames a second, so both are approximate.
- 28-60 videos per account is a small sample; "unclear" often means "not enough videos", and the intervals are wide (B's short-video effect is anywhere from 1.4x to 9.9x). "Too few" appears often because features with fewer than 5 videos on a side are not tested.
- Six accounts of different size, platform and language is not a random sample of the internet; four are institutions or brands, two are individual creators.
- Public numbers are views, likes and comments. Watch time, saves and shares, the signals platforms say matter most, are not public; `reel-intel insights` adds them for your own account.
- A feature that predicts views does not prove that copying it causes views.

Reproduce on your own accounts: `reel-intel compare FOLDER FOLDER ...`.
