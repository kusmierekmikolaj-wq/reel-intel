"""screenmine.py - what is written ON the screen: GitHub repositories, skill / tool names, files. Pure functions (no files, no models).

The text comes from OCR of the video's frames (`visuals.screen_text`). OCR is noisy (it drops spaces and confuses l/I/1), so everything found here
is a CANDIDATE with the time it appeared; the report says so and links the time so a person can check it on the video.

  repos(lines)   "github.com/owner/name" anywhere, and a bare "owner/name" only when the same moment also shows the word github / repo / git clone
  tokens(lines)  slash commands (/plugin), hyphenated identifiers (frontend-design), files (SKILL.md, CLAUDE.md, package.json)
  known(lines, names)  tools from a list (the glossary): "Claude Code", "n8n", "Cursor" ...
"""
import re

GH_URL = re.compile(r"git\s*hub(?:\s*\.?\s*com)?\s*[/\\|]\s*([A-Za-z0-9][\w.-]{0,38})\s*[/\\|]\s*([A-Za-z0-9][\w.-]{0,80})", re.I)
BARE = re.compile(r"(?<![\w./@:-])([A-Za-z0-9][\w-]{1,38})/([A-Za-z][\w.-]{1,60})(?![\w/])")
GH_HINT = re.compile(r"git\s*hub|git\s+clone|\brepo(?:sitory|zytorium)?\b|npx\s|npm\s+i|plugin\s+marketplace|marketplace\s+add|skills?\s+add|\bgh\s+repo", re.I)
SLASH = re.compile(r"(?<![\w/.:-])(/[a-z][a-z0-9]*(?:-[a-z0-9]+){0,3})(?![\w/])")
HYPHEN = re.compile(r"(?<![\w-])([a-z][a-z0-9]{1,}(?:-[a-z0-9]{2,}){1,4})(?![\w-])")
FILE = re.compile(r"(?<![\w/-])([A-Za-z0-9_-]{2,30}\.(?:md|json|ya?ml|py|js|ts|tsx|sh|toml))\b")
NOT_REPO_WORDS = {"and", "or", "on", "off", "yes", "no", "km", "kg", "mb", "gb", "tb", "24", "7", "w", "i", "a"}
COMMON_HYPHENATED = {"well-known", "built-in", "open-source", "real-time", "up-to-date", "e-mail", "non-stop", "one-time", "step-by-step",
                     "so-called", "long-term", "short-term", "high-quality", "low-cost", "full-time", "first-class", "ai-powered", "no-code",
                     "low-code", "co-founder", "x-ray", "t-shirt", "check-in", "follow-up", "how-to", "must-have", "easy-to-use"}


def _plausible_repo(owner, name):
    o, n = owner.lower().rstrip(".-"), name.lower().rstrip(".-")
    if o in NOT_REPO_WORDS or n in NOT_REPO_WORDS or len(n) < 2:
        return False
    if not re.search(r"[a-z]", o) or not re.search(r"[a-z]", n):                       # dates, scores, prices: "12/10", "3/4"
        return False
    return not re.fullmatch(r"\d+", o) and not n.endswith((".com", ".pl", ".org"))


def repos(lines):
    """lines: [(t, text)] -> [{"repo": "owner/name", "t": seconds, "how": "url" | "bare"}], first sighting of each"""
    found, seen = [], set()
    by_time = {}
    for t, text in lines:
        by_time.setdefault(round(t), []).append(text)
    for t, text in lines:
        for m in GH_URL.finditer(text):
            name = f"{m.group(1)}/{m.group(2)}".rstrip(".-")
            if _plausible_repo(m.group(1), m.group(2)) and name.lower() not in seen:
                seen.add(name.lower())
                found.append({"repo": name, "t": t, "how": "url"})
    for t, text in lines:
        near = " ".join(x for s in (round(t) - 3, round(t) - 2, round(t) - 1, round(t), round(t) + 1, round(t) + 2, round(t) + 3) for x in by_time.get(s, []))
        if not GH_HINT.search(near):
            continue
        for m in BARE.finditer(text):
            name = f"{m.group(1)}/{m.group(2)}".rstrip(".-")
            if _plausible_repo(m.group(1), m.group(2)) and name.lower() not in seen and "git" not in m.group(1).lower():
                seen.add(name.lower())
                found.append({"repo": name, "t": t, "how": "bare"})
    return found


SKILL_HINT = re.compile(r"skills?\b|plugins?\b|\bmcp\b|agents?\b|claude|cursor|codex|git\s*hub|\bnpx\b|\bnpm\b|\bpip\b|marketplace|\bcli\b|prompt|workflow|\bn8n\b|install|instal|repo|zainstaluj", re.I)


def _near_text(lines, t, radius=3):
    return " ".join(x for tt, x in lines if abs(tt - t) <= radius)


def tokens(lines):
    """slash commands and file names always; a hyphenated identifier (frontend-design) only when a word like skill / plugin / MCP / agent / GitHub /
    npx is on the screen within 3 seconds, because OCR of ordinary text produces plenty of random hyphenated words -> [{"token", "kind", "t"}]"""
    out, seen = [], set()
    for t, text in lines:
        for rx, kind in ((SLASH, "command"), (FILE, "file"), (HYPHEN, "name")):
            for m in rx.finditer(text):
                tok = m.group(1)
                key = tok.lower()
                if key in seen or key in COMMON_HYPHENATED or (kind == "name" and len(tok) < 6):
                    continue
                if kind == "name" and not SKILL_HINT.search(_near_text(lines, t)):
                    continue
                seen.add(key)
                out.append({"token": tok, "kind": kind, "t": t})
    return out


def known(lines, names):
    """names from a list (the glossary) that appear on screen, ignoring case and spaces: 'Claude Code' also matches 'ClaudeCode'"""
    squash = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    texts = [(t, squash(x)) for t, x in lines]
    out = []
    for name in names:
        k = squash(name)
        if len(k) < 3:
            continue
        for t, s in texts:
            if k in s:
                out.append({"name": name, "t": t})
                break
    return out


def mine(lines, names=()):
    return {"repos": repos(lines), "tokens": tokens(lines), "known": known(lines, names)}
