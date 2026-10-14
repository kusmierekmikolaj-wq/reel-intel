"""Does the OCR + mining chain find GitHub repositories, skill names and commands written on a screen?

  python benchmarks/screen_benchmark.py

Synthetic frames (540x960, like the stored videos): a terminal / browser / slide look with KNOWN content, in light and dark, three sizes.
Each frame is read with the same functions the scanner uses (visuals.read_text -> screenmine.mine). A repository counts as found when
`owner/name` comes back exactly (case-insensitive); skills and commands the same. Real recordings are harder (compression, motion, scrolling),
so this is an upper bound; it shows whether the chain can work at all.
"""
import random
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "src"))

CASES = [
    # (lines drawn on the frame, repos expected, tokens expected)
    (["Install the skill:", "github.com/anthropics/claude-code"], ["anthropics/claude-code"], []),
    (["git clone https://github.com/obra/superpowers"], ["obra/superpowers"], []),
    (["npx skills add vercel-labs/agent-skills", "github.com/vercel-labs/agent-skills"], ["vercel-labs/agent-skills"], []),
    (["Claude Code skills", "frontend-design", "brand-guidelines", "SKILL.md"], [], ["frontend-design", "brand-guidelines", "SKILL.md"]),
    (["/plugin marketplace add", "anthropics/skills"], ["anthropics/skills"], ["/plugin"]),
    (["GitHub", "openai/codex", "star this repo"], ["openai/codex"], []),
    (["CLAUDE.md", "mcp-server-fetch", "install via npx"], [], ["CLAUDE.md", "mcp-server-fetch"]),
    (["github.com/n8n-io/n8n", "workflow templates"], ["n8n-io/n8n"], []),
]


def frame(lines, dark, size, rng, font_path):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    base = np.full((960, 540, 3), 22 if dark else 244, dtype="float32") + np.random.default_rng(rng.randrange(10 ** 6)).normal(0, 4, (960, 540, 3))
    img = Image.fromarray(np.clip(base, 0, 255).astype("uint8"))
    d = ImageDraw.Draw(img)
    y = rng.randrange(260, 520)
    for ln in lines:
        s = size
        f = ImageFont.truetype(font_path, s)
        while d.textlength(ln, font=f) > 500 and s > 14:             # a line must fit the frame: a cut-off name is not an OCR error
            s -= 1
            f = ImageFont.truetype(font_path, s)
        d.text((20, y), ln, font=f, fill=(235, 235, 235) if dark else (25, 25, 25))
        y += int(size * 1.7)
    return np.array(img)[:, :, ::-1].copy()


def main():
    from reel_intel import screenmine
    from reel_intel.visuals import read_text
    rng = random.Random(3)
    font = r"C:\Windows\Fonts\consola.ttf"
    rows = []
    for dark in (True, False):
        for size in (26, 32, 40):
            for lines, repos, toks in CASES:
                img = frame(lines, dark, size, rng, font)
                seen = [(0.0, t) for t, _y in read_text(img)]
                m = screenmine.mine(seen)
                got_r = {r["repo"].lower() for r in m["repos"]}
                got_t = {x["token"].lower() for x in m["tokens"]}
                rows.append((dark, size, [r for r in repos if r.lower() in got_r], repos, [x for x in toks if x.lower() in got_t], toks, sorted(got_r)))
    exp_r = sum(len(r[3]) for r in rows)
    hit_r = sum(len(r[2]) for r in rows)
    exp_t = sum(len(r[5]) for r in rows)
    hit_t = sum(len(r[4]) for r in rows)
    wrong = sorted({x for r in rows for x in r[6]} - {e.lower() for r in rows for e in r[3]})
    L = ["# On-screen reading benchmark (synthetic frames)", "",
         f"{len(rows)} frames (8 contents x 3 text sizes x dark/light), monospace text on a plain background. Same OCR and mining code as the scanner.", "",
         "| | found | expected | share |", "|---|---|---|---|",
         f"| GitHub repositories | {hit_r} | {exp_r} | {hit_r / max(exp_r, 1):.0%} |",
         f"| skill names, commands, files | {hit_t} | {exp_t} | {hit_t / max(exp_t, 1):.0%} |", "",
         f"Repositories reported that were NOT on the screen (false alarms): {wrong or 'none'}", "",
         "| mode | size | repos found | tokens found |", "|---|---|---|---|"]
    for dark in (True, False):
        for size in (26, 32, 40):
            rs = [r for r in rows if r[0] == dark and r[1] == size]
            L.append(f"| {'dark' if dark else 'light'} | {size}px | {sum(len(r[2]) for r in rs)}/{sum(len(r[3]) for r in rs)} | {sum(len(r[4]) for r in rs)}/{sum(len(r[5]) for r in rs)} |")
    (HERE / "SCREEN_RESULTS.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:12]))


if __name__ == "__main__":
    main()
