"""pronounce.py - the voice engine's pronunciation, learned from its own mistakes.

When speech recognition does not hear a script word in the generated voice, the word was probably mispronounced. The generator then tries
respellings of that word (syllables split with hyphens, English letter groups written the Polish way, acronyms spelled out), keeps the one that
is heard correctly and REMEMBERS it in ~/.reel-intel/voices/wymowa.json, so the next reel says it right the first time. Respellings that did not
help are remembered too, so they are not tried again. Captions on screen always keep the original spelling.
"""
import json
import re
from pathlib import Path

STORE = Path.home() / ".reel-intel" / "voices" / "wymowa.json"
VOWELS = "aeiouyąęó"
LETTERS = {"pl": dict(zip("abcdefghijklmnopqrstuvwxyz", ["a", "be", "ce", "de", "e", "ef", "gie", "ha", "i", "jot", "ka", "el", "em", "en", "o", "pe", "ku", "er",
                                                        "es", "te", "u", "fał", "wu", "iks", "igrek", "zet"]))}
ANGLO = [("ll", "l"), ("ss", "s"), ("tt", "t"), ("th", "t"), ("sh", "sz"), ("ee", "i"), ("oo", "u"), ("ck", "k"), ("ph", "f"), ("q", "k"), ("x", "ks"), ("v", "w")]


def key(word):
    return re.sub(r"[\W_]", "", word.lower())


def load():
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(d):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def learned(lang):
    """{word: respelling} that were heard correctly at least once and more often fixed it than not"""
    out = {}
    for w, v in load().get(lang, {}).items():
        if v.get("say") and v.get("ok", 0) >= 1 and v.get("ok", 0) >= v.get("bad", 0):
            out[w] = v["say"]
    return out


def record(lang, word, say, ok):
    """remember that `say` did (ok=True) or did not fix `word`"""
    d = load()
    w = d.setdefault(lang, {}).setdefault(key(word), {"say": None, "ok": 0, "bad": 0, "failed": []})
    if ok:
        if w.get("say") != say:
            w.update(say=say, ok=0, bad=0)
        w["ok"] += 1
    else:
        if w.get("say") == say:
            w["bad"] += 1
        if say not in w["failed"]:
            w["failed"].append(say)
    save(d)


def syllable_count(text):
    return len(re.findall(r"[aeiouyąęó]+", text.lower()))


def syllables(word, before_last=True):
    """the word cut into syllables with hyphens (a slower, clearer reading for the voice engine)"""
    w = word.lower()
    digraphs = {"dż": "①", "dź": "②", "rz": "③", "sz": "④", "cz": "⑤", "ch": "⑥", "dz": "⑦"}
    for a, b in digraphs.items():                                              # one sound written with two letters is never cut
        w = w.replace(a, b)
    back = lambda s: "".join({v: k for k, v in digraphs.items()}.get(c, c) for c in s)
    pos = [i for i, c in enumerate(w) if c in VOWELS]
    cuts = []
    for a, b in zip(pos, pos[1:]):
        gap = b - a - 1
        if gap == 0:
            continue
        cuts.append(b - 1 if (before_last or gap == 1) else a + 2)
    out, last = [], 0
    for c in cuts:
        if 0 < c < len(w) and c > last:
            out.append(w[last:c])
            last = c
    out.append(w[last:])
    return "-".join(back(p) for p in out if p)


def candidates(word, lang="pl"):
    """respellings to try for a mispronounced word, best guesses first, without ones that already failed"""
    core = re.sub(r"^\W+|\W+$", "", word)
    k = key(core)
    if not k or lang != "pl":
        return []
    c = []
    if core.isupper() and len(core) <= 5 and core.isalpha():
        c.append(" ".join(LETTERS["pl"].get(ch, ch) for ch in core.lower()))
    de = k
    for a, b in ANGLO:
        de = de.replace(a, b)
    if de != k:
        c.append(de)
    c.append(syllables(de))
    c.append(syllables(de, before_last=False))
    failed = set(load().get(lang, {}).get(k, {}).get("failed", []))
    seen, out = set(), []
    for x in c:
        if x and x != k and x not in seen and x not in failed:
            seen.add(x)
            out.append(x)
    return out[:3]
