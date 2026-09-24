#!/usr/bin/env python3
"""Generate all Android launcher + notification icons from icon.png.

Source `icon.png` is the Jenny mascot: an opaque white face with black line-art
details on a transparent background. On a black background the mascot reads as-is
(white face floats on black, dark features sit on top of the white fill), so the
launcher/large icons need no colour inversion.

Outputs (all under app/src/main/res/):
  A) mipmap-<dpi>/ic_launcher_foreground.png  adaptive foreground (mascot only)
  C) drawable-nodpi/ic_notification_large.png  black bg + mascot (expanded notif)

B is gone: the status-bar icon is no longer the mascot but the flower, a
hand-kept vector in drawable/ic_stat_jenny.xml (see the comment there). At 24dp
the mascot's silhouette read as noise. Do not bring the PNGs back: a
drawable-<dpi> PNG would win over the vector and restore the old icon.

No legacy raster launcher icons (ic_launcher.png / ic_launcher_round.png): with
minSdk 26 every device uses the adaptive icon, so legacy PNGs would only add a
second, square-cropped rendering in some surfaces and clash with the adaptive
(masked) one. Adaptive icon = single source of truth.

Re-run after tweaking any tuning constant; it is idempotent.
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "icon.png"
RES = ROOT.parent / "app" / "src" / "main" / "res"

BLACK = (0, 0, 0, 255)

# density bucket -> px for a 108dp adaptive canvas
FOREGROUND = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}


def load_mascot() -> Image.Image:
    """Load icon.png and tightly crop to its non-transparent bounding box."""
    im = Image.open(SRC).convert("RGBA")
    bbox = im.getbbox()
    return im.crop(bbox)


def fit(mascot: Image.Image, size: int, fraction: float) -> Image.Image:
    """Return a size x size transparent canvas with mascot scaled+centered."""
    target = max(1, int(round(size * fraction)))
    w, h = mascot.size
    scale = target / max(w, h)
    resized = mascot.resize(
        (max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS
    )
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(resized, ((size - resized.width) // 2, (size - resized.height) // 2), resized)
    return canvas


def save(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    print(f"  {path.relative_to(ROOT.parent)}  ({img.width}x{img.height})")


def main() -> None:
    mascot = load_mascot()
    print(f"source mascot cropped to {mascot.size}")

    # 0.54: the whole mascot (antenna + hair included) sits fully inside the
    # launcher's circular mask with an even black margin — nothing is clipped.
    # The mask circle measures ~0.76 of the canvas, whose inscribed square caps
    # the mascot's max dimension at ~0.54 of the canvas.
    print("A) adaptive foreground")
    for dpi, size in FOREGROUND.items():
        save(fit(mascot, size, 0.54), RES / f"mipmap-{dpi}" / "ic_launcher_foreground.png")

    print("C) notification large icon")
    big = Image.new("RGBA", (256, 256), BLACK)
    big.alpha_composite(fit(mascot, 256, 0.80))
    save(big, RES / "drawable-nodpi" / "ic_notification_large.png")

    print("done.")


if __name__ == "__main__":
    main()
