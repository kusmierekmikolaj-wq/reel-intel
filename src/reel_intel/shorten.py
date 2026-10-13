"""shorten.py - an automatic short version of every video (no AI service needed).

  reel-intel shorten                 writes data/<id>/content_short.md where it is missing, refreshes the automatic ones

It is an EXTRACT, not a summary: the first sentence (hook), the few sentences that carry the most of the video's own distinctive words,
the call to action if there is one, and for videos without speech the on-screen text. Every sentence is quoted from the transcript, so
nothing can be invented. A short version written by a person or by the Claude skill is never overwritten (only files carrying the
`<!-- auto -->` marker are refreshed. `--overwrite-manual` replaces those too (there is no undo).
"""
import collections
import math
import re
import sys

from .analysis import CTA_RE
from .common import CFG, DATA, apply_fixes, jload, near_duplicate, restore_diacritics

AUTO = "<!-- auto -->"
T = {
    "pl": {"title": "wersja skrócona (automatyczny wyciąg)", "stats": "{views} wyświetleń | {likes} lajków | {comments} komentarzy | {dur:.0f} s | {date}",
           "full": "Pełna treść dosłowna: `content_full.md`", "hook": "Hook (0-3 s)", "key": "Najważniejsze zdania", "cta": "CTA", "none": "brak",
           "screen": "Treść", "caption": "Opis", "form": "Forma", "no_speech": "bez mowy: treść na ekranie", "speech": "mowa, {wpm:.0f} słów/min",
           "note": "Wyciąg zdań z nagrania, nie streszczenie: nic tu nie jest dopisane."},
    "en": {"title": "short version (automatic extract)", "stats": "{views} views | {likes} likes | {comments} comments | {dur:.0f} s | {date}",
           "full": "Full verbatim text: `content_full.md`", "hook": "Hook (0-3 s)", "key": "Key sentences", "cta": "CTA", "none": "none",
           "screen": "Content", "caption": "Caption", "form": "Format", "no_speech": "no speech: content is on screen", "speech": "speech, {wpm:.0f} words/min",
           "note": "An extract of sentences from the recording, not a summary: nothing is added."},
}
WORD = re.compile(r"[^\W\d_]{4,}", re.U)


def sentences(text):
    parts = [s.strip() for s in re.split(r"(?<=[.?!…])\s+", text.strip()) if s.strip()]
    out = []
    for s in parts:                                                       # glue tiny fragments ("Get it!") to their neighbour
        if out and len(s.split()) < 3:
            out[-1] += " " + s
        else:
            out.append(s)
    return out


def key_sentences(sents, caption_names, k=3):
    """score = how many of the video's own repeated or distinctive words a sentence carries, plus numbers and names from the caption"""
    freq = collections.Counter(w.lower() for s in sents for w in WORD.findall(s))
    names = {n.lower() for n in caption_names}
    scores = []
    for i, s in enumerate(sents):
        ws = [w.lower() for w in WORD.findall(s)]
        if not ws:
            scores.append(0)
            continue
        sc = sum(math.log(1 + freq[w]) for w in ws) / math.sqrt(len(ws))
        sc += (0.8 if re.search(r"\d", s) else 0) + 0.6 * sum(w in names for w in ws)
        scores.append(sc)
    top = sorted(range(len(sents)), key=lambda i: -scores[i])[:k]
    return sorted(top)                                                    # keep the order of the recording


def clip(s, n=200):
    return s if len(s) <= n else s[:n - 1].rsplit(" ", 1)[0] + "…"


def build(d, lang_ui=None):
    from .common import caption_names
    f = jload(d / "features.json")
    tr = jload(d / "transcript.json")
    t = T.get(lang_ui or CFG.get("ui_language"), T["en"])
    speech_ok = f.get("word_count", 0) >= 10 and f.get("speech_reliable", True)
    date = f.get("upload_date") or ""
    date = f"{date[6:8]}.{date[4:6]}.{date[:4]}" if len(date) == 8 else date
    fmt = lambda n: f"{n:,.0f}" if n is not None else "?"
    L = [f"# {f['id']} - {t['title']}", AUTO,
         f"**{t['stats'].format(views=fmt(f.get('views')), likes=fmt(f.get('likes')), comments=fmt(f.get('comments')), dur=f['duration_s'], date=date)}**",
         t["full"], ""]
    cap = re.sub(r"\s+", " ", f.get("caption") or "").strip()
    cap_sents = sentences(cap)
    if cap:
        L.append(f"**{t['caption']}:** „{clip(cap_sents[0], 160)}”")
    if speech_ok:
        text = apply_fixes(" ".join(s["text"] for s in tr["segments"]), tr["language"])[0]
        sents = sentences(text)
        L.append(f"**{t['hook']}:** „{clip(sents[0])}”")
        middle = sents[1:-1]
        if len(sents) > 3:
            L.append(f"**{t['key']}:** " + " / ".join(f"„{clip(middle[i])}”" for i in key_sentences(middle, caption_names(cap))))
        elif len(sents) > 1:
            L.append(f"**{t['key']}:** " + " / ".join(f"„{clip(s)}”" for s in sents[1:]))
        last = sents[-1] if len(sents) > 1 else ""
        cta = last if CTA_RE.search(last) else next((s for s in cap_sents if CTA_RE.search(s)), "")
        L.append(f"**{t['cta']}:** " + (f"„{clip(cta, 160)}”" if cta else t["none"]))
        L.append(f"**{t['form']}:** " + t["speech"].format(wpm=f.get("words_per_min") or 0))
    else:
        seen = []
        if (d / "onscreen.json").exists():
            for o in jload(d / "onscreen.json"):
                for line in o["text"]:
                    line = restore_diacritics(line, tr["language"])
                    if len(line) > 2 and not any(near_duplicate(line, x) for x in seen):
                        seen.append(line)
        L.append(f"**{t['screen']}:** " + (f"„{clip(' '.join(seen), 400)}”" if seen else t["none"]))
        L.append(f"**{t['form']}:** {t['no_speech']}")
    L += ["", f"_{t['note']}_"]
    return "\n".join(L) + "\n"


def run(overwrite_manual=False):
    n = skipped = 0
    for d in sorted(DATA.iterdir()):
        if not (d / "features.json").exists() or not (d / "transcript.json").exists():
            continue
        p = d / "content_short.md"
        if p.exists() and AUTO not in p.read_text(encoding="utf-8") and not overwrite_manual:
            skipped += 1                                                  # written by a person or by Claude: keep
            continue
        p.write_text(build(d), encoding="utf-8")
        n += 1
    print(f"short versions written: {n}, kept (not automatic): {skipped}")


def main():
    run("--overwrite-manual" in sys.argv[1:])


if __name__ == "__main__":
    main()
