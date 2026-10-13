"""Renders assets/logo.svg's artwork to PNG (logo, extension icons, social preview). Pillow + numpy only.  python assets/make_logo.py"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
ROOT = HERE.parent
S = 4                                  # supersampling
STOPS = [(0.0, (0x29, 0x97, 0xFF)), (0.55, (0xBF, 0x5A, 0xF2)), (1.0, (0xFF, 0x37, 0x5F))]


def gradient(n):
    y, x = np.mgrid[0:n, 0:n]
    t = ((x + y) / (2.0 * (n - 1))).astype("float32")                # 0 at top-left, 1 at bottom-right (the SVG's diagonal)
    out = np.zeros((n, n, 3), dtype="float32")
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
        m = (t >= t0) & (t <= t1)
        k = ((t - t0) / (t1 - t0))[m][:, None]
        out[m] = np.array(c0) * (1 - k) + np.array(c1) * k
    return Image.fromarray(out.astype("uint8"), "RGB")


def mark(size):
    n = size * S
    u = n / 1024.0
    img = gradient(n).convert("RGBA")
    d = ImageDraw.Draw(img)
    tri = [(243 * u, 413 * u), (243 * u, 717 * u), (398 * u, 565 * u)]
    r = 30 * u                                                        # rounded corners: fill, thick edges, and a disc on every vertex
    d.polygon(tri, fill="white")
    for a, b in zip(tri, tri[1:] + tri[:1]):
        d.line([a, b], fill="white", width=int(2 * r))
    for x, y in tri:
        d.ellipse([x - r, y - r, x + r, y + r], fill="white")
    for x, y, h in ((498, 557, 190), (616, 427, 320), (734, 277, 470)):
        d.rounded_rectangle([x * u, y * u, (x + 78) * u, (y + h) * u], radius=39 * u, fill="white")
    mask = Image.new("L", (n, n), 0)                                  # the rounded square
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, n - 1, n - 1], radius=232 * u, fill=255)
    img.putalpha(mask)
    return img.resize((size, size), Image.LANCZOS)


def font(size, bold=True):
    for name in ("segoeuib.ttf" if bold else "segoeui.ttf", "arialbd.ttf" if bold else "arial.ttf"):
        try:
            return ImageFont.truetype(rf"C:\Windows\Fonts\{name}", size)
        except OSError:
            continue
    return ImageFont.load_default()


def main():
    mark(1024).save(HERE / "logo.png")
    mark(512).save(HERE / "logo-512.png")
    icons = ROOT / "extension" / "icons"
    icons.mkdir(exist_ok=True)
    for s in (16, 32, 48, 128):
        mark(s).save(icons / f"icon{s}.png")
    # social preview, 1280x640, the size GitHub asks for
    W, H = 1280, 640
    bg = Image.new("RGB", (W, H), (0, 0, 0))
    bg.paste(mark(300), (110, 170), mark(300))
    d = ImageDraw.Draw(bg)
    d.text((470, 195), "reel-intel", font=font(120), fill=(245, 245, 247))
    d.text((474, 340), "Why do short videos pop?", font=font(48, False), fill=(161, 161, 166))
    d.text((474, 410), "Text, picture and sound of every video.", font=font(36, False), fill=(134, 134, 139))
    d.text((474, 460), "Local-first. Free. No API keys.", font=font(36, False), fill=(134, 134, 139))
    bg.save(HERE / "social-preview.png")
    print("written:", ", ".join(sorted(p.name for p in HERE.glob("*.png"))), "and", ", ".join(sorted(p.name for p in icons.glob("*.png"))))


if __name__ == "__main__":
    main()
