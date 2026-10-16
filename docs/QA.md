# Reel quality control (`reel-intel make qa`)

A generated reel can be wrong in ways nobody sees in a file listing: a breath after a sentence, a vowel dragged on, music that swells in every pause, a word said as another word,
a caption card running into the slogan. This system looks for all of those, says which one it found, where, and (for most of them) repairs it and checks again.
It was built **after** a person heard faults that an earlier checker reported as "0 errors", so the list of faults comes from how text-to-speech and short video fail in practice, not from what
was easy to measure.

```
reel-intel make render rolka.json        render, check, repair what can be repaired (up to 3 rounds), check again; writes rolka_kontrola.md
reel-intel make qa rolka.mp4             check an existing file (--quick leaves out the sound-event model, the OCR and the recogniser)
reel-intel make qa --selftest            put known faults into synthetic reels; every one must be found, clean reels must pass (about a minute)
reel-intel make calibrate DIR... [--reels DIR...]   thresholds from real recordings -> quality/qa_profile.json
reel-intel make note rolka.mp4 13.2 oddech "after scene 1"    write down what you heard; the checker says whether it had found it
reel-intel make learn [--dry]            tune the thresholds so the notes it missed would be caught
```

## The verdict

| verdict | meaning | exit code |
|---|---|---|
| **gotowe** | nothing above INFO was found; a person still listens before it goes out | 0 |
| **z uwagami** | only WARNs: nothing is broken, but something should be looked at | 10 |
| **niegotowe** | at least one ERROR: do not publish this file | 1 |
| (could not run) | no file, no sidecar, a crash | 2 |

Ten warnings are still "z uwagami": a verdict is never made by counting. The report `rolka_kontrola.md` (Polish) lists the findings, what was repaired, what was not, what could not be checked and what
this system does not judge. The same summary is stored in `rolka.qa.json` under `qa`.

## How a reel is checked

`make render` writes, next to the mp4: `rolka.qa.json` (the **sidecar**: scenes with word times, the words the recogniser heard in each recording, parameters, the voice engine wanted and used, the renderer wanted and
used, the mastering numbers, the checksum of the mp4), `rolka_voice.wav` (the dry voice), `rolka_music.wav` (the music alone, as mixed) and `rolka_opis.txt`. The check reads those files, decodes the mp4, and runs every
detector of every module. A detector is a plain function of numbers (`check(ctx) -> [Finding]`): it renders and records nothing and starts no model; what a model produced arrives through the context. A detector that crashes becomes
a `QA_ERROR` finding and the others still run. A sidecar whose checksum is not the file's (`SIDECAR_STALE`) stops every timing based check; one without a checksum is reported as `SIDECAR_OLD`.

| module | what it judges |
|---|---|
| `speech` | wrong, skipped and repeated words (the recogniser's output against the script, split into substitution, deletion and insertion), pauses inside a sentence, flat or jumping pitch, a voice that changes between scenes, a silent switch of voice engine or renderer |
| `audio` | pauses between scenes, a chopped or dragged last word, breaths and murmurs (level based and from the sound-event model), level jumps, hiss, the speaking rate |
| `mix` | loudness (EBU R128 / BS.1770-4, implemented in numpy and checked against ffmpeg), true peak, clipping, music pumping, music against voice, dropouts, noise and clicks |
| `picture` | dead first frame, black and frozen stretches, flicker (WCAG general flash), text outside the safe area, text over text or over the edge of the caption card, two scenes at once, small or faint text, captions too fast |
| `platform` | size, 9:16, frame rate, codec, pixel format, length, streaming layout, the sound track |
| `sync` | caption times against the words heard in the finished mp4, the mascot's mouth against the voice (with lag), the start times of sound and picture in the container |

## The repair loop

`check -> plan -> repair -> check again`, at most 3 rounds (`quality/repair.py`). The plan maps every ERROR and WARN to the repair of the register below; a new take of a scene replaces the smaller repairs of that scene;
a repair that did not help is not tried again but escalated (cut the end harder -> record again), and when the ladder is used up the finding is handed to a person and the report says so. A round that leaves the reel **worse** than before is undone
(the files are snapshotted). Repairs that only change the sound (`remix`, `master`, `level_scenes`) are applied without a new render of the picture (a new sound track is put under the same picture); the others render again.
The pristine take of every scene is kept, so only the scenes that are recorded again cost voice time.

## The register of faults

Generated from the code (`python -m reel_intel.quality.docs`); a test fails when this table and the code differ.

<!-- BEGIN register -->
| module | code | severity | what it finds | repair | po polsku |
|---|---|---|---|---|---|
| speech | `ENGINE_FALLBACK` | ERROR | the voice engine that was used is not the one that was asked for (a cloned voice silently replaced by a stock voice is an ERROR) | render again | cicha zamiana głosu na zapasowy silnik |
| speech | `RENDER_FALLBACK` | WARN | the picture was drawn by the simple PIL renderer although the animated Remotion one was expected (no mascot) | render again | uproszczony renderer zamiast animowanego (bez maskotki) |
| speech | `VOICE_DEL` | WARN | a script word was skipped: the voice track does not contain it | record the sentence again | pominięte słowo ze skryptu |
| speech | `VOICE_DRIFT` | WARN | the voice of one scene sounds like another speaker (speaker embedding far from the median of the reel; threshold is a guess) | record the sentence again | głos zmienia się między scenami |
| speech | `VOICE_INS` | WARN | a word the script does not have: a repeated word or phrase, or an invented one | record the sentence again | dodane lub powtórzone słowo |
| speech | `VOICE_MONOTONE` | WARN | flat intonation: the pitch of a sentence varies less than the limit (a limit exists only after calibration) | record the sentence again | monotonna intonacja |
| speech | `VOICE_PAUSE_INNER` | WARN | a silence longer than the limit between two words of one sentence | record the sentence again | pauza w środku zdania |
| speech | `VOICE_PITCH_JUMP` | WARN | the pitch jumps by more than the limit from one frame to the next and stays there: a voice glitch | record the sentence again | skok wysokości głosu (usterka syntezy) |
| speech | `VOICE_SUB` | WARN | a script word was said as another word (mispronounced) or not heard as written | another spelling for the voice, then record again | słowo wymówione jak inne (zła wymowa) |
| audio | `EARS_OFF` | INFO | the sound-event model did not run: breaths are judged by level only | a person | model słuchu nie działał (oddechy oceniane tylko po poziomie) |
| audio | `EAR_BREATH` | WARN | the sound-event model hears a breath or a body sound (AudioSet labels such as Gasp, Breathing, Sigh) | cut the end of the sentence harder | model słuchu słyszy oddech lub dźwięk ciała |
| audio | `GAP_CLICK` | WARN | a click or a jump in the sound between two sentences | mix the music again (lower, filtered) | trzask lub skok w dźwięku między zdaniami |
| audio | `GAP_LONG` | WARN | more than gap.long_s of nothing said between two sentences | cut the end of the sentence harder | za długa przerwa między zdaniami |
| audio | `GAP_LOUD` | WARN | the sound between two sentences is almost as loud as the voice (music too loud, something is playing) | mix the music again (lower, filtered) | za głośny dźwięk w przerwie między zdaniami |
| audio | `GAP_RUMBLE` | WARN | the sound between two sentences is mostly very low tones (rumble) | mix the music again (lower, filtered) | dudnienie (niskie tony) w przerwie między zdaniami |
| audio | `GAP_SHORT` | WARN | less than gap.short_s between two sentences: they run together | a person | zdania zlewają się, za krótka przerwa |
| audio | `PACE` | INFO | the average speaking rate of the reel, for information | a person | średnie tempo mowy (informacja) |
| audio | `VOICE_BREATH` | WARN | a breath, a mouth noise or a breathy tail inside the voice track (before, between or after the words) | cut the end of the sentence harder | oddech, szmer lub oddechowy ogon w głosie |
| audio | `VOICE_BURST` | WARN | a rushed stretch inside a sentence of normal pace (more than pace.burst syllables in one second) | slow the sentence down | wyścig: nagle za szybki fragment zdania |
| audio | `VOICE_CUT` | ERROR | the voice is still speaking at full level when the scene's sound ends: the last word is chopped | record the sentence again | ucięte ostatnie słowo |
| audio | `VOICE_DRONE` | WARN | the last word lasts longer than voice.last_word_max_s: a vowel dragged on | cut the end of the sentence harder | przeciągnięta ostatnia samogłoska |
| audio | `VOICE_FAST` | WARN | a scene spoken faster than pace.fast syllables a second (heard or by the words) | slow the sentence down | za szybkie tempo mowy |
| audio | `VOICE_HISS` | WARN | noise under the voice louder than voice.hiss_db: audible hiss | record the sentence again | szum pod głosem |
| audio | `VOICE_LEVEL` | WARN | the voice level jumps by more than voice.level_jump_db between two scenes | bring every scene to the same level | skok głośności głosu między scenami |
| audio | `VOICE_SLOW` | WARN | a scene spoken slower than pace.slow syllables a second: dragging | record the sentence again | za wolne tempo mowy |
| audio | `VOICE_TAIL` | INFO | silence after the last word in the voice file (trimmed in the render) | a person | cisza po ostatnim słowie (przycięta) |
| mix | `LOUDNESS` | WARN | integrated loudness (LUFS) outside the target of the profile; an INFO line gives the number every time | set the loudness (limiter on the peaks) | głośność całości poza celem |
| mix | `MIX_CLIP` | ERROR | samples at full scale (clipping): the sound distorts | set the loudness (limiter on the peaks) | przesterowanie dźwięku |
| mix | `MIX_DROPOUT` | ERROR | digital silence inside speech in the final mix: part of the sentence is missing | mix the music again (lower, filtered) | dziura (zanik dźwięku) w trakcie mowy |
| mix | `MIX_PUMPING` | WARN | the music swells in every speech pause and drops when the voice returns (a ducker that releases too fast) | mix the music again (lower, filtered) | muzyka pompuje (głośność faluje z mową) |
| mix | `MIX_VOICE_TO_MUSIC` | WARN | the music is too close to the voice level while the voice speaks | mix the music again (lower, filtered) | muzyka za głośna względem głosu |
| mix | `TRUE_PEAK` | ERROR | true peak (oversampled) above the ceiling: the sound distorts after lossy encoding | set the loudness (limiter on the peaks) | szczyt sygnału (true peak) powyżej limitu |
| mix | `VOICE_NOISE` | WARN | hiss under the voice (noise floor in the pauses) or clicks in the voice track | record the sentence again | szum, trzaski lub kliknięcia pod głosem |
| picture | `CAPTION_SPEED` | WARN | caption block with more characters a second than captions.max_cps, or shown shorter than captions.min_show_s | slow the sentence down | napisy za szybkie lub za krótko na ekranie |
| picture | `CONTRAST` | WARN | WCAG contrast ratio of text against its background below captions.min_contrast | compute the layout again | za mały kontrast tekstu z tłem |
| picture | `FIRST_FRAME` | ERROR | the first frame is black, flat or empty: a dead hook | compute the layout again | martwa pierwsza klatka (pusta, czarna lub jednolita) |
| picture | `FLICKER` | WARN | luminance flashes more than picture.flash_per_s times a second | compute the layout again | błyski obrazu (więcej niż 3 na sekundę) |
| picture | `OCR_OFF` | INFO | text position checks skipped: no OCR is available | a person | nie sprawdzono pozycji tekstu (brak OCR) |
| picture | `PICTURE_BLACK` | ERROR | black frames for longer than picture.black_s | render again | czarne klatki |
| picture | `PICTURE_ERROR` | WARN | a picture detector crashed (a bug in the detector, not in the reel) | a person | błąd detektora obrazu (usterka narzędzia, nie rolki) |
| picture | `PICTURE_FREEZE` | WARN | identical frames for longer than picture.freeze_s while speech is running | render again | zamrożony obraz podczas mowy |
| picture | `PICTURE_OFF` | INFO | picture checks skipped: no frames of the video are available | a person | nie sprawdzono obrazu (brak klatek) |
| picture | `SAFE_ZONE` | WARN | text, a panel or a bar outside the safe rectangle of the reel (uncertain: sources disagree) | compute the layout again | tekst lub pasek poza strefą bezpieczną |
| picture | `TEXT_LEGIBILITY` | WARN | text smaller than captions.min_px | compute the layout again | za małe litery |
| picture | `TEXT_OVERLAP` | ERROR | two pieces of text overlap, or a text runs over the edge of a panel (the slogan running into the caption card) | compute the layout again | teksty nachodzą na siebie lub na brzeg karty |
| picture | `TRANSITION_GHOST` | WARN | the text of two scenes is on screen at once around a scene change | compute the layout again | dwie sceny naraz na ekranie przy przejściu |
| platform | `AUDIO_RATE` | INFO | the sound track is not 44.1 or 48 kHz (the reel is made at 24 kHz and encoded as AAC) | a person | częstotliwość dźwięku inna niż 44,1/48 kHz |
| platform | `AV_LENGTH` | WARN | the picture and the sound track differ in length by more than the tolerance | render again | obraz i dźwięk mają różną długość |
| platform | `FILE_BAD` | ERROR | ffprobe cannot read the file | render again | plik nie daje się odczytać |
| platform | `NO_AUDIO` | ERROR | the file has no sound track | render again | plik bez ścieżki dźwiękowej |
| platform | `PLATFORM_ASPECT` | ERROR | the picture is not 9:16 (within the tolerance): the platform crops it or adds bars | render again | obraz nie jest 9:16 |
| platform | `PLATFORM_CODEC` | WARN | the video codec is not H.264/HEVC or the sound codec is not AAC | render again | kodek inny niż H.264/AAC |
| platform | `PLATFORM_DURATION` | ERROR | the reel is shorter or longer than the profile's window (ERROR) | a person | długość rolki poza dopuszczalnym oknem |
| platform | `PLATFORM_FASTSTART` | WARN | the moov atom is at the end of the file: playback cannot start while it is still downloading | render again | plik nie startuje w trakcie pobierania (moov na końcu) |
| platform | `PLATFORM_FPS` | WARN | the frame rate is not 30 fps (within the tolerance) or varies | render again | klatkaż inny niż 30 fps |
| platform | `PLATFORM_PIXFMT` | WARN | the pixel format is not yuv420p (yuvj420p, the same layout flagged full range, is only an INFO line) | render again | format pikseli inny niż yuv420p |
| platform | `PLATFORM_SIZE` | WARN | the picture is not 1080x1920 | render again | rozdzielczość inna niż 1080x1920 |
| platform | `VIDEO_FACTS` | INFO | the facts about the finished file that the platform checks were run on (or that there were none) | a person | dane o pliku (informacja) |
| sync | `AV_OFFSET` | WARN | the sound track starts earlier or later than the picture track in the container by more than the sync window | render again | dźwięk startuje inaczej niż obraz w pliku |
| sync | `CAPTION_SYNC` | WARN | the caption word times differ from the words heard in the finished mp4 (median or maximum shift above the profile) | render again | napisy rozjechane z mową |
| sync | `LIPSYNC` | WARN | the mascot's mouth does not follow the loudness of the voice (weak correlation, flat) or follows it at the wrong time (lag beyond EBU R37) | render again | usta maskotki nie nadążają za głosem |
| engine | `QA_ERROR` | WARN | a detector crashed (a bug in the checker, not in the reel) | a person | błąd detektora (usterka skanera, nie rolki) |
| engine | `SIDECAR_MISSING` | ERROR | there is no <reel>.qa.json next to the file: the timeline is unknown, only the file itself can be checked | a person | brak pliku .qa.json obok rolki: nie znam osi czasu |
| engine | `SIDECAR_OLD` | INFO | the sidecar has no checksum (it was made by an older version): it cannot be verified to belong to this file | a person | plik .qa.json bez sumy kontrolnej (starsza wersja) |
| engine | `SIDECAR_STALE` | ERROR | the sidecar was written for another version of this file (its checksum differs): every timing based check would be wrong | render again | plik .qa.json pochodzi z innej wersji rolki (suma kontrolna się różni) |
<!-- END register -->

## Thresholds

Every number a detector uses is in `quality/profile.py` (and the keys modules add, listed in each module's `PROFILE_ADDITIONS`), is read from the profile in the context, and can be overridden by
`quality/qa_profile.json`. "in use" is what the calibrated file sets. How sure each number is:

- **measured on our own data** (`make calibrate`): `pace.fast/slow`, `intonation.f0_std_semitones_min`, `loudness.target_lufs/tolerance`, the detector settings of the F0 tracker and of the pitch jump (60 minutes of TED speech), the
  limits of `CAPTION_SYNC` (207 clean scenes), the edge share of a real first frame (18 real first frames), the box-to-font ratio of the OCR (150 measurements);
- **from a standard**: BS.1770-4 / EBU R128 (loudness algorithm), WCAG 2.3.1 (flashes: 10% of the maximum luminance, more than three a second) and 1.4.3 (contrast 4.5:1), EBU R37 (sound before picture by more than 40 ms or after by more than 60 ms);
- **uncertain, said so in the finding**: the safe area of Instagram Reels (the sources disagree: top 200-270, bottom 350-672, sides 65-130 px; the profile uses 270 / 440 / 90), the platform's length and format list (third party spec sheets, not Meta
  documentation), the voice-drift threshold (a guess, no speaker model is wired in), the caption reading speed (12-20 characters a second, Netflix style guides).

<!-- BEGIN thresholds -->
| threshold | default | in use |
|---|---|---|
| `captions.hold_s` | 0.28 | same |
| `captions.max_cps` | 20 | same |
| `captions.max_lines` | 3 | same |
| `captions.min_contrast` | 4.5 | same |
| `captions.min_px` | 48 | same |
| `captions.min_show_s` | 0.8 | same |
| `gap.click_jump` | 0.35 | same |
| `gap.long_s` | 0.9 | same |
| `gap.loud_db_under_speech` | 8 | same |
| `gap.min_seg_s` | 0.08 | same |
| `gap.rumble_floor_db` | -42 | same |
| `gap.rumble_share` | 0.45 | same |
| `gap.short_s` | 0.12 | same |
| `intonation.f0_std_semitones_min` | None | 1.2 |
| `intonation.pitch_jump_semitones` | 12 | same |
| `loudness.mono_as_stereo` | True | same |
| `loudness.target_lufs` | -16.0 | -14.3 |
| `loudness.tolerance` | 2.5 | 2.0 |
| `loudness.true_peak_max` | -1.0 | same |
| `loudness.uncertain` | True | False |
| `mix.clip_level` | 0.985 | same |
| `mix.clip_share` | 0.0005 | same |
| `mix.dropout_floor_db` | -80.0 | same |
| `mix.dropout_s` | 0.3 | same |
| `mix.dropout_speech_overlap_s` | 0.15 | same |
| `mix.pump_min_events` | 2 | same |
| `mix.pump_min_pause_s` | 0.25 | same |
| `mix.pump_min_share` | 0.5 | same |
| `mix.pump_ref_s` | 1.0 | same |
| `mix.pump_window_s` | 0.1 | same |
| `mix.pumping_db_max` | 3.0 | same |
| `mix.v2m_min_span_s` | 0.6 | same |
| `mix.voice_to_music_db_min` | 14 | same |
| `pace.burst` | 9.0 | same |
| `pace.burst_window_s` | 1.0 | same |
| `pace.fast` | 6.8 | same |
| `pace.min_scene_s` | 1.0 | same |
| `pace.slow` | 3.2 | same |
| `pace.target` | 5.8 | same |
| `picture.black_level` | 0.05 | same |
| `picture.black_s` | 0.5 | same |
| `picture.black_share` | 0.98 | same |
| `picture.content_edge_delta` | 30 | same |
| `picture.first_frame_dark` | 0.05 | same |
| `picture.first_frame_flat_std` | 0.02 | same |
| `picture.first_frame_min_edge` | 0.03 | same |
| `picture.flash_dark_max` | 0.8 | same |
| `picture.flash_delta` | 0.1 | same |
| `picture.flash_per_s` | 3 | same |
| `picture.flash_window_s` | 1.0 | same |
| `picture.flicker_hz` | 30 | same |
| `picture.freeze_mad` | 0.1 | same |
| `picture.freeze_s` | 2.0 | same |
| `picture.ghost_frames` | [-4, -3, -2, -1, 0, 1, 2] | same |
| `picture.hook_s` | 0.8 | same |
| `picture.ocr_min_conf` | 0.5 | same |
| `picture.overlap_same_line` | 0.5 | same |
| `picture.overlap_share` | 0.15 | same |
| `picture.probe_hz` | 10 | same |
| `picture.safe_tol_px` | 6 | same |
| `picture.straddle_max` | 0.85 | same |
| `picture.straddle_min` | 0.15 | same |
| `picture.ui_bar_max_px` | 24 | same |
| `picture.ui_delta` | 18 | same |
| `picture.ui_detect` | True | same |
| `picture.ui_fill_delta` | 8 | same |
| `picture.ui_min_w` | 0.5 | same |
| `picture.ui_panel_min_px` | 80 | same |
| `picture.ui_persist` | 2 | same |
| `picture.ui_thin_px` | 4 | same |
| `picture.ui_tol_px` | 12 | same |
| `platform.aspect_tol` | 0.01 | same |
| `platform.audio_codec` | ['aac'] | same |
| `platform.audio_rates` | [44100, 48000] | same |
| `platform.av_length_s` | 0.15 | same |
| `platform.codec` | ['h264', 'hevc'] | same |
| `platform.fps` | 30 | same |
| `platform.fps_tol` | 0.5 | same |
| `platform.height` | 1920 | same |
| `platform.max_s` | 90 | same |
| `platform.min_s` | 3 | same |
| `platform.pix_fmt` | yuv420p | same |
| `platform.pix_fmt_tolerated` | ['yuvj420p'] | same |
| `platform.width` | 1080 | same |
| `safe_zone.bottom` | 440 | same |
| `safe_zone.side` | 90 | same |
| `safe_zone.top` | 270 | same |
| `safe_zone.uncertain` | True | same |
| `sync.audio_early_ms` | 45 | same |
| `sync.audio_late_ms` | 60 | same |
| `sync.caption_fuzzy_ratio` | 0.75 | same |
| `sync.caption_min_words` | 5 | same |
| `sync.caption_ms` | 80 | same |
| `sync.caption_scene_factor` | 2.0 | same |
| `sync.caption_scene_min_words` | 3 | same |
| `sync.lag_corr_min` | 0.5 | same |
| `sync.lag_range_ms` | 300 | same |
| `sync.lag_step_ms` | 5 | same |
| `sync.lipsync_corr_min` | 0.5 | same |
| `sync.mix_lag_corr_min` | 0.6 | same |
| `sync.mouth_bias_ms` | 7.0 | same |
| `voice.breath_db_under_speech` | 32.0 | same |
| `voice.breath_flatness` | 0.12 | same |
| `voice.breath_floor_db` | -52.0 | same |
| `voice.breath_min_gap_s` | 0.25 | same |
| `voice.breath_min_seg_s` | 0.1 | same |
| `voice.breath_score` | 0.07 | same |
| `voice.click_flux_z` | 6.0 | same |
| `voice.click_isolation_s` | 0.004 | same |
| `voice.click_min_jump` | 0.04 | same |
| `voice.click_z` | 12.0 | same |
| `voice.cut_db_under_speech` | 10.0 | same |
| `voice.ears_min_score` | 0.07 | same |
| `voice.heard_error` | 0.5 | same |
| `voice.heard_warn` | 0.8 | same |
| `voice.hiss_db` | -46 | same |
| `voice.hiss_error_over_db` | 12.0 | same |
| `voice.hiss_min_frames_s` | 0.1 | same |
| `voice.inner_pause_s` | 0.6 | same |
| `voice.inner_pause_warn_s` | 0.9 | same |
| `voice.last_word_max_s` | 1.1 | same |
| `voice.level_jump_db` | 6 | same |
| `voice.soft_tail_s` | 0.12 | same |
| `voice.tail_warn_s` | 0.5 | same |
<!-- END thresholds -->

### What calibration found

Calibrated on 33 recordings: 4 TED/TEDx talks (about 60 minutes) and 29 reels of a reference account (`make calibrate DIR --reels DIR`).

- **Speaking rate** (syllable nuclei counted from the sound, de Jong & Wempe simplified; validated on synthetic speech of 3.5-8 syllables a second): 473 utterances, p2 4.4, median 6.0, p90 6.7, p98 7.1 a second. This is faster
  than the 2.9-6.5 an earlier hand count suggested. The limit stays at **6.8**: professional speakers talk fast, and a person who listened to the generated voice called 6.8 and over rushed. Calibration may only *tighten* the fast
  limit and may not raise the slow one (3.2).
- **Pitch spread** depends on how long the stretch is: the standard deviation of F0 (semitones, against the speaker's median) has its p10 at 1.44 on 2 s windows, 1.67 on 3 s and 1.83 on 4 s. A reel's sentence is about 3 s, so the limit
  is measured on **3 s windows**: p5 of 977 windows is 1.39, times 0.85 = **1.2**. (Measured on whole utterances the limit would have been 1.9, which would have flagged more than one real sentence in ten.)
- **Loudness** of the reference account: median **-14.3 LUFS**, spread p10-p90 of about 4 LU, so target -14.3 +- 2.0; the reel is mastered to it. The first generated reel was at -18.9 LUFS.

## What was verified, and how

- **Fault-injection matrix** (`quality/matrix.py`, `make qa --selftest`): 72 faults put into clean synthetic reels, **72 reported with their code (100%)**, 7 of 7 clean reels silent, no false alarm on a clean reel. In 12 cases a second code came with the
  one that was injected (a clipped mix is also over the peak limit, a 50 ms gap also puts the scene's end inside the next sentence): consequences, listed by the matrix, not counted as mistakes. *This proves that each detector finds the fault it was
  written for; it does not prove independence: the faults were made by the same people who wrote the detectors.*
- **The same faults in real speech** (`make qa --selftest --real DIR`): clean excerpts of 4 TED/TEDx talks (25 s, cut at sentence boundaries, every sentence a scene), 13 kinds of fault put in (6 excerpts, 78 cases), a fault counts as found only
  when the number of findings with its code goes **up** against the same excerpt without it (a real excerpt has findings of its own). **71 of 78 found (91%)**: breathy tail 6/6, dropout inside speech 6/6, level jump 6/6, a sentence 1.7 times
  faster 6/6, last vowel dragged on 6/6, pause inside a sentence 6/6, word said as another 6/6, word skipped 6/6, word repeated 6/6, breath after a sentence 5/6, words invented 5/6, click 4/6, **pitch jump 3/6**. Left out because they cannot be
  told apart in continuous speech: hiss under the voice (it is listened for in the quiet stretch after the last word, which a talk does not have), loudness and clipping (a recording has its own). Injected breaths are never quieter than -42 dBFS
  (-46 for a tail): below that they are not audible and the detectors have a floor there. An earlier run of this matrix found two mistakes of mine in the way faults were put in (a dropout in the voice track as well, a breath under the floor), not in the detectors.
- **Real speech, no music** (4 TED/TEDx talks, 14.7 minutes cut at sentence boundaries, words from the talks' own timings, the detectors that apply to a voice-over): per minute VOICE_FAST 5.3, VOICE_LEVEL 0.75, MIX_CLIP 0.5, VOICE_NOISE 0.6,
  VOICE_MONOTONE 0.3, VOICE_PITCH_JUMP 0.3, VOICE_DRONE 0.3, everything else under 0.1. These are properties of those recordings and of how fast people talk (a TED speaker is over 6.8 syllables a second in one utterance in ten; the audio of some
  talks is clipped; there is applause), not faults of a voice-over, and they show that **the thresholds are tuned for a generated voice-over, not for human speech**. The target "at most one warning a minute on reference material" is therefore not met on
  human talks. VOICE_CUT is left out of this audit because a talk cut at a sentence boundary is by construction still speaking there.
- **A real reel, before and after** (the first reel made for the reference account's data): checked by this system when it was first rendered it got *z uwagami* with 5 notes: -18.9 LUFS (too quiet), an empty first frame, the caption card 28 px and
  the progress bar 218 px into the bottom interface, and `Trzydzieści` reported as mispronounced. Three of those were faults of the checker or of the render, found by running it on a real file: the recogniser had written the number as "30" (now
  understood), the file read 3 LU under what the master aimed at (an encoder that upmixes a mono file to stereo lowers each channel by 3 dB, so the mastered sound is now handed over as two channels with the same sound), and the progress bar had been
  moved into the top margin on purpose (now information, not a warning). After the changes, rendered again with `make render`: **gotowe, 0 errors, 0 warnings**, -14.3 LUFS and -1.0 dBTP measured on the finished mp4, one repair round (the music was
  lowered because it was too loud against the voice), the picture kept as it was. That is the verdict of this checker; it says nothing about whether the voice sounds right.
- **Layout of the picture**, measured on real Remotion stills (81 renders, `studio/qa/check_layout.py`, `studio/qa/results/`): 42 violations before the layout was rewritten (the caption card ran into the three-line slogan, the card and the progress bar stood in the bottom
  interface of Reels, two scenes were on screen at once for several frames), **0 after**; the first frame, which is the cover in the feed, was empty and now carries the mascot, the slogan and the caption (edge share 0.10, a real first frame has 0.06 or more).
- **Tests:** the unit tests of every module and of the repair loop run without a GPU; the models (sound-event, OCR, recogniser) are optional and their absence is reported as an INFO finding.

## Learning from a person's ears

`make note rolka.mp4 13.2 oddech` stores a note (time, kind). The checker then says whether it had found something of that kind within 1.5 s (or in the same scene), and the report gives its **recall against the notes**. For a note it missed, the numbers
are measured at that moment (the sound-event score, the soft tail, the rate, the last word's length, the pause ...) and `make learn` moves the threshold of the detector that should have fired to just catch the case (10% past the measured value),
**never past a floor** that keeps clean reels from alarming; when the floor stands in the way it says "this detector cannot separate the case, it needs a better detector" instead of a number. Every stored case is checked again against the new
profile, so tuning for one note cannot silently lose another. A kind with nothing measured behind it (`inne`) cannot be tuned. Findings without a note are not counted as false alarms: a person who writes three notes did not necessarily hear everything.

## Limits, said plainly

- **Taste is not judged.** Whether a voice is pleasant and natural, whether a pronunciation is right throughout, whether the music suits the reel, whether a joke works, stays with a person. The checker finds what can be measured and names candidates.
- **The recogniser is not an ear.** Word faults come from what faster-whisper recognised: on real speakers it disagrees with human subtitles on 4.5-13% of words, it often loses the first word of a file and invents "thank you" on silence. A finding is a candidate to record again.
- **Pitch and monotone** are validated on synthetic glides and on real speech (60 minutes), not on every voice; the limit was measured on English and Polish talks, not on the cloned voice.
- **Voice drift** (a scene that sounds like another speaker) needs a speaker model that is not wired in: the detector is in place and returns nothing without `scene_embeddings`.
- **Safe zone, target loudness, caption speed** are from sources that disagree; they are configurable and marked uncertain in the findings.
- **OCR** misses blurred or very faint text; a missed ghost of the previous scene is not a clean transition. `find_ui` is tuned to the flat graphics of our own renderer, not to arbitrary video.
- **The layout has no automatic repair.** A `SAFE_ZONE` or `TEXT_OVERLAP` finding is reported; the layout itself is computed in `studio/src/layout.ts` and fixed in code.
- **A repair that records a scene again needs the cloned voice engine** (Chatterbox); Piper and the Windows voice say the same thing every time, so "record again" is not possible there and the finding goes to a person.
- Nothing is published anywhere: no Instagram upload, and the AI label of a post is a decision of the company.

## Sources

Standards: ITU-R BS.1770-4 and EBU R128 (loudness), EBU R37 and ITU-R BT.1359 (audio-video synchronisation), WCAG 2.1 (contrast, flashes). Method papers: Boersma 1993 (pitch by autocorrelation), de Jong & Wempe 2009 (syllable nuclei),
the manual error analysis of a text-to-speech system in arXiv 2104.08459 (repeated, skipped and mispronounced words, truncated last words, long pauses, non-speech sounds), arXiv 2604.08562 (mean-opinion-score predictors are not sensitive to word-level
artifacts, so they do not replace a word error rate). Practice: the `reel-qc` project (exit codes and profiles), Netflix timed-text style guides (caption speed), the Instagram safe-zone guides (which disagree). Secondary sources and vendor
pages are marked uncertain wherever they set a number.
