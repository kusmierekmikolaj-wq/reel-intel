# From data to a new reel (`reel-intel make`)

What the scanned videos show (what works, which tools get mentioned) can feed **new** short vertical videos. The tool does the mechanical parts; the idea and the words come from an AI agent or a person. Nothing is published anywhere.

## The four steps

1. **Brief.** `reel-intel make brief` writes `BRIEF_ROLKI.md` (`REEL_BRIEF.md` in English) into the data folder: rules for a reel (20-40 s, a hook in 3 s, one idea), what your data says (hook types with median views, features clearly tied to views, names read from the screens) and the exact JSON to answer with.
2. **Script.** Paste the brief into your agent (Claude, Jarvis, any model) together with `DLA_AI.md` / `FOR_AI.md` if it should read the material. Save its answer as `rolki/rolka.json`. A script is 3-12 scenes, each a spoken sentence plus 2-6 words for the screen, an optional `emphasis` list, optional `chips` (names shown as detections, e.g. `"/human"`), a closing `cta`, a `caption` and `hashtags`.
3. **Check.** `reel-intel make check rolki\rolka.json` rejects a script that is too long or malformed and warns when it repeats a run of six words from a scanned video.
4. **Render.** `reel-intel make render rolki\rolka.json` writes `rolka.mp4` (1080x1920, H.264 + AAC) and `rolka_opis.txt` (caption and hashtags). The same steps are in the local app, section "Reels".

## The mascot

With the Remotion version every reel has a small robot whose face is a screen (turn it off with `"mascot": false` in the script). It blinks, breathes, waves at the start, raises its eyebrows and makes a hand gesture on every word listed in `emphasis`, and shows scanning brackets in scenes that have `chips`. The mouth takes the shape of the sounds: each word's real start and end are read off the voice file (Whisper word timestamps), the letters share that time (vowels count double) and the loudness of the voice opens or closes the mouth, so it works for Polish and English spelling alike. It is a cartoon on purpose: research on virtual influencers finds stylised figures avoid the "eerie" reaction that near-human ones can trigger. The reel is a synthetic video: label it as made with AI.

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
