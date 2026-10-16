# From data to a new reel (`reel-intel make`)

What the scanned videos show (what works, which tools get mentioned) can feed **new** short vertical videos. The tool does the mechanical parts; the idea and the words come from an AI agent or a person. Nothing is published anywhere.

## The four steps

1. **Brief.** `reel-intel make brief` writes `BRIEF_ROLKI.md` (`REEL_BRIEF.md` in English) into the data folder: rules for a reel (20-40 s, a hook in 3 s, one idea), what your data says (hook types with median views, features clearly tied to views, names read from the screens) and the exact JSON to answer with.
2. **Script.** Paste the brief into your agent (Claude, Jarvis, any model) together with `DLA_AI.md` / `FOR_AI.md` if it should read the material. Save its answer as `rolki/rolka.json`. A script is 3-12 scenes, each a spoken sentence plus 2-6 words for the screen, an optional `emphasis` list, optional `chips` (names shown as detections, e.g. `"/human"`), a closing `cta`, a `caption` and `hashtags`.
3. **Check.** `reel-intel make check rolki\rolka.json` rejects a script that is too long or malformed and warns when it repeats a run of six words from a scanned video.
4. **Render.** `reel-intel make render rolki\rolka.json` writes `rolka.mp4` (1080x1920, H.264 + AAC) and `rolka_opis.txt` (caption and hashtags). The same steps are in the local app, section "Reels".

## The mascot

With the Remotion version every reel has a small robot whose face is a screen (turn it off with `"mascot": false` in the script). It blinks, breathes, waves at the start, raises its eyebrows and makes a hand gesture on every word listed in `emphasis`, and shows scanning brackets in scenes that have `chips`. The mouth takes the shape of the sounds: each word's real start and end are read off the voice file (Whisper word timestamps), the letters share that time (vowels count double) and the loudness of the voice opens or closes the mouth, so it works for Polish and English spelling alike. It is a cartoon on purpose: research on virtual influencers finds stylised figures avoid the "eerie" reaction that near-human ones can trigger. The reel is a synthetic video: label it as made with AI.

## Voice, music and the checks

- **Voice engines** (`"voice"` in the script): `chatterbox` (a natural neural voice, optionally cloned from 10-15 s of a recording in `~/.reel-intel/voices/moj_glos.wav`, `"voice_sample": "none"` for the model's own voice; runs in its own environment `~/.reel-intel/venv-tts`, MIT licence, needs a GPU with about 6 GB), `piper` (local, robotic), `edge` (Microsoft's neural voices through an unofficial route: for trying, not for a product), `sapi` (Windows). Without a choice: Chatterbox when a sample is installed, then Piper, then Windows. `exaggeration` and `cfg` set how lively and how steady Chatterbox sounds.
- **Pronunciation that learns:** every sentence is recorded three times and the take whose words speech recognition hears best is kept. A word still not heard is tried again respelled (syllables split, English letter groups written the Polish way, acronyms spelled out); a respelling that fixes it is remembered in `~/.reel-intel/voices/wymowa.json` and used from then on, one that does not help is never tried again. Manual fixes: `"say": {"pliku": "pli-ku"}`. Captions keep the real spelling.
- **Pace:** a sentence faster than 6.8 syllables a second (real speakers in our data: 4.4-7.1, median 6.0 by the sound) is recorded again, steadier, and the take with a natural pace replaces it.
- **Gaps:** the silence the voice engine leaves after a sentence (measured: up to 2.7 s) is cut 0.18 s after the voice really ends, judged by the sound itself so a word the recognizer missed is not chopped.
- **Music:** a bed generated from arithmetic (no licence involved), kept above 150 Hz so it does not rumble, at a steady level under the voice (no side-chain compressor: its release made the music swell in every pause);
  `"music": "off"`, `"music": "your-track.mp3"`, `"music_volume"`.
- **Loudness:** the finished mix is mastered to the loudness of the reference account (-14.3 LUFS, true peak under -1 dBTP) unless the script says `"master": false`; `"loudness": -16` sets another target.
- **Reel check and repair** (every `render`, and `reel-intel make qa rolka.mp4`): the voice, the sound, the music, the picture, the captions and the file are checked against a register of known faults; what can be repaired (a sentence
  recorded again, slowed down, cut harder, the music lowered, the loudness set) is repaired and checked again, up to 3 rounds (`--rounds N`, `--no-check`, `--quick`). The result is a verdict (**gotowe**, **z uwagami**, **niegotowe**), the
  report `rolka_kontrola.md` and the same summary in `rolka.qa.json`. `make render` exits with the verdict (0 gotowe, 10 z uwagami, 1 niegotowe, 2 the check could not run), so a script that publishes only on 0 or 10 can rely on it. Everything about it, including what it cannot judge: [QA.md](QA.md).

## What it uses

- **Voice-over:** the speech engine of Windows (`System.Speech`, Polish and English voices, nothing to install). On other systems the video is made silent, with the same captions. The voice sounds synthetic.
- **Picture, animated version:** [Remotion](https://www.remotion.dev) (React to video) in `studio/`. One time: `cd studio` and `npm install`; the first render also downloads a headless browser (a few hundred MB). A render of 30 s takes about 1.5 minutes. Remotion has its own licence: free for individuals and small companies, a paid licence above a size threshold ([check the current terms](https://www.remotion.dev/license) before commercial use).
- **Picture, simple version:** without Node the tool draws the same layout with Pillow and ffmpeg (about 25 s for 30 s of video, plainer animation). `--simple` forces it.

## Limits, said plainly

- The video is **text on an animated background**, not filmed footage and not an AI-generated video clip.
- The words of the voice-over are timed by sentence length, so a highlighted word can be a fraction of a second off.
- The quality of the script depends on the agent that writes it. `check` finds length and copying, not whether the idea is good.
- **Do not reuse other people's recordings or texts.** The generator never takes frames or speech from a scanned video; the data only supplies patterns. See [LEGAL.md](LEGAL.md).
- Publishing to Instagram is not built. The official route (Instagram Graph API) needs a Business account, a Meta app with approved permissions and the video at a public address; that is a decision for whoever owns the account.
