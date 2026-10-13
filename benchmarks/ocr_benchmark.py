"""OCR benchmark on synthetic frames with a known text (text over a noisy photo-like background, white with a dark outline like Reels captions).

  python benchmarks/ocr_benchmark.py [--out benchmarks/OCR_RESULTS.md] [--real-vocab]

Frames are 540x960 like the ones the scanner stores, and are read exactly as `scan.ocr_frames` reads them (2x upscale, confidence > 0.6).
Polish diacritics: OCR often drops them, so the scanner restores them from the account's own vocabulary; here that vocabulary is built from
the data folder ($REEL_DATA) plus a small built-in word list. Run it with an EMPTY data folder for "built-in words only" (the worst case, a new
account) and with --real-vocab on a real account's folder for what the scanner does in practice. The PL_B lines were written after the word
list was finished and are the fair test; the PL lines were seen while it was written, so read them as optimistic.
Synthetic text is cleaner than real video (no motion blur, no stylised fonts): read the numbers as an upper bound.
"""
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "src"))

PL = [
    "Zapisz ten film, żeby nie zgubić instrukcji", "Pierwsze sekundy decydują o wszystkim", "Jak zarobić więcej bez nadgodzin",
    "Błąd, który kosztuje Cię klientów", "Trzy sposoby na lepszą organizację dnia", "Dlaczego reklamy przestają działać",
    "Ściągawka dla początkujących w marketingu", "Zrób to zanim zaczniesz kampanię", "Nie wierz w mity o algorytmie",
    "Napisz w komentarzu słowo poradnik", "Łatwy sposób na oszczędność czasu", "Czego uczą się najlepsi sprzedawcy",
    "Jak pisać opisy, które sprzedają", "Więcej wyświetleń dzięki mocnemu początkowi", "Źle ustawiona reklama to stracone pieniądze",
    "Krok po kroku: pierwszy film w tygodniu", "Co zmieniło się w ostatnim tygodniu", "Zostań ze mną, a pokażę Ci jak to ogarnąć",
    "Prosty plan na miesiąc treści", "Przestań wyrzucać pieniądze w błoto",
]
EN = [
    "Save this video so you don't lose the steps", "The first seconds decide everything", "How to earn more without overtime",
    "The mistake that costs you customers", "Three ways to organise your day better", "Why your ads stop working",
    "A cheat sheet for marketing beginners", "Do this before you launch a campaign", "Don't believe the algorithm myths",
    "Comment the word guide and I will send it", "An easy way to save time", "What the best salespeople learn",
    "How to write descriptions that sell", "More views thanks to a strong opening", "A badly set up ad is wasted money",
    "Step by step: your first video this week", "What changed in the last week", "Stay with me and I'll show you how",
    "A simple plan for a month of content", "Stop throwing money away",
]

# held-out Polish lines written AFTER the built-in word list (src/reel_intel/data/pl_words.txt) was finished, not used to build it
PL_B = [
    "Jak przygotować się do wyjazdu w góry", "Dlaczego warto czytać książki wieczorem", "Pięć nawyków, które zmieniły mój poranek",
    "Zdrowe śniadanie w pięć minut", "Tego błędu nie popełniaj podczas rozmowy kwalifikacyjnej", "Sekret dobrej kawy w domu",
    "Co robić, gdy brakuje motywacji", "Najczęstsze pytania naszych klientów", "Zmień swoje przyzwyczajenia od jutra",
    "Mała zmiana, która robi dużą różnicę", "Gdzie schować klucze, żeby ich nie zgubić", "Poradnik dla osób zaczynających treningi",
    "Ile naprawdę kosztuje własny ogródek", "Wybierz mądrze zanim cokolwiek kupisz", "Nauczyłem się tego dopiero po latach",
    "Ten trik oszczędza godzinę dziennie", "Zapytaj mnie o cokolwiek w komentarzach", "Skąd wiedzieć, że to się opłaca",
    "Każdy początek jest trudny, ale warto", "Obejrzyj do końca, a zrozumiesz wszystko",
]


def background(rng, w=540, h=960):
    import numpy as np
    base = np.zeros((h, w, 3), dtype="float32")
    for c in range(3):                                                   # smooth gradient + blobs, like a blurred video frame
        base[:, :, c] = np.linspace(rng.uniform(30, 200), rng.uniform(30, 200), h)[:, None] + np.linspace(0, rng.uniform(-60, 60), w)[None, :]
    for _ in range(25):
        x, y, r = rng.randrange(w), rng.randrange(h), rng.randrange(20, 120)
        yy, xx = np.ogrid[:h, :w]
        mask = (xx - x) ** 2 + (yy - y) ** 2 < r * r
        base[mask] += rng.uniform(-70, 70)
    base += np.random.default_rng(rng.randrange(10 ** 6)).normal(0, 9, base.shape)
    return np.clip(base, 0, 255).astype("uint8")


def render(text, rng, font_path):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.fromarray(background(rng))
    d = ImageDraw.Draw(img)
    size = rng.choice([30, 36, 42, 48])
    font = ImageFont.truetype(font_path, size)
    words, lines, cur = text.split(), [], ""
    for w in words:                                                      # wrap to ~480 px
        if d.textlength((cur + " " + w).strip(), font=font) > 480 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    y = rng.randrange(250, 650)
    for ln in lines:
        wd = d.textlength(ln, font=font)
        d.text(((540 - wd) / 2, y), ln, font=font, fill=(255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0))
        y += int(size * 1.35)
    import numpy as np
    return np.array(img)[:, :, ::-1].copy()                              # BGR for OpenCV


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower())).strip()


def cer(ref, hyp):
    a, b = norm(ref), norm(hyp)
    d = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(b) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
            prev = cur
    return d[len(b)] / max(1, len(a)), len(a)


def vocab_from(lines):
    from reel_intel.common import _PL
    v = defaultdict(Counter)
    for t in lines:
        for w in re.findall(r"[^\W\d_]+", t):
            v[w.lower().translate(_PL)][w.lower()] += 1
    return v


def main(argv=None):
    import cv2
    from rapidocr_onnxruntime import RapidOCR
    from reel_intel.common import restore_diacritics
    args = list(sys.argv[1:] if argv is None else argv)
    out = Path(args[args.index("--out") + 1]) if "--out" in args else HERE / "OCR_RESULTS.md"
    real_vocab = "--real-vocab" in args
    vocab_note = "built-in words + the account's own vocabulary" if real_vocab else "built-in words only"                                  # the data folder's vocabulary ($REEL_DATA) instead of leave-one-out
    font = r"C:\Windows\Fonts\arialbd.ttf"
    ocr = RapidOCR()
    rng = random.Random(7)
    rows = []
    for lang, lines in (("pl", PL), ("pl-heldout", PL_B), ("en", EN)):
        for i, text in enumerate(lines):
            img = render(text, rng, font)
            r, _ = ocr(cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
            hyp = " ".join(x[1] for x in (r or []) if float(x[2]) > 0.6)
            fixed = restore_diacritics(hyp, "pl" if lang.startswith("pl") else lang, None) if lang.startswith("pl") else hyp
            c1, n = cer(text, hyp)
            c2, _ = cer(text, fixed)
            rows.append((lang, text, hyp, fixed, c1, c2, n, norm(text) == norm(fixed)))
    L = ["# OCR benchmark (synthetic frames)", "",
         "40 frames (20 Polish, 20 English), known text over a noisy photo-like background, white text with a dark outline, 30-48 px on a 540x960 frame. "
         "CER = character error rate after lower-casing and removing punctuation (lower is better). Synthetic text is cleaner than real video: an upper bound.", "",
         "| language | step | CER | lines read exactly |", "|---|---|---|---|"]
    for lang in ("pl", "pl-heldout", "en"):
        rs = [r for r in rows if r[0] == lang]
        n = sum(r[6] for r in rs)
        raw = sum(r[4] * r[6] for r in rs) / n
        fixed = sum(r[5] * r[6] for r in rs) / n
        L.append(f"| {lang} | raw OCR | {raw:.1%} | {sum(cer(r[1], r[2])[0] == 0 for r in rs)}/{len(rs)} |")
        if lang.startswith("pl"):
            L.append(f"| {lang} | + diacritics restored ({vocab_note}) | {fixed:.1%} | {sum(r[7] for r in rs)}/{len(rs)} |")
    L += ["", "## Misses", "", "| language | expected | read |", "|---|---|---|"]
    for r in rows:
        if not r[7]:
            L.append(f"| {r[0]} | {r[1]} | {r[3]} |")
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:12]))


if __name__ == "__main__":
    main()
