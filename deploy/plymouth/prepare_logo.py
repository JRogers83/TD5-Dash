#!/usr/bin/env python3
"""
TD5 Dash — Plymouth logo preparation

Trims, resizes, composites onto solid black (no transparency — Plymouth
doesn't render transparent PNGs correctly), and counter-rotates to
compensate for the unrotated framebuffer.

Usage:
    python3 prepare_logo.py <source.png> <logo_dest.png> [rotation]

    rotation: DISPLAY_ROTATION from .env (0, 90, 180, 270; default 270).

Called automatically by deploy/setup.sh during Pi first-time setup.
Requires: python3-pil (apt) or Pillow (pip)
"""

import sys
from PIL import Image


LOGO_HEIGHT_PCT = 80   # % of 400px display height = 320px (10% margin top + bottom)


def make_logo(src_path: str, dest_path: str, rotation: int) -> None:
    img = Image.open(src_path).convert("RGBA")

    # Auto-crop non-transparent pixels
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)

    # Resize for the landscape display (1280x400)
    target_h = int(400 * LOGO_HEIGHT_PCT / 100)
    w, h = img.size
    scale = target_h / h
    img = img.resize((int(w * scale), target_h), Image.LANCZOS)

    # Composite onto solid black — no transparency
    black = Image.new("RGBA", img.size, (0, 0, 0, 255))
    black.paste(img, (0, 0), img)  # img's alpha used as mask
    img = black.convert("RGB")

    # Pre-rotate for the raw (portrait) framebuffer so the logo appears upright
    # after the kernel applies its display rotation.
    # For a CW kernel rotation of R degrees, the required pre-rotation is
    # (360 - R) degrees CW — which simplifies to R CW for R<=180 but to
    # (360-R) CW for R>180 (e.g. 270° kernel needs 90° CW pre-rotation,
    # same as 90° kernel, because 90+270=360).
    if rotation:
        effective = rotation if rotation <= 180 else 360 - rotation
        img = img.rotate(-effective, expand=True)

    img.save(dest_path, "PNG")
    print(f"  Logo: {img.size[0]}x{img.size[1]}px (rotation={rotation}) -> {dest_path}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <source.png> <logo_dest.png> [rotation]")
        sys.exit(1)

    rotation = int(sys.argv[3]) if len(sys.argv) > 3 else 270
    make_logo(sys.argv[1], sys.argv[2], rotation)
