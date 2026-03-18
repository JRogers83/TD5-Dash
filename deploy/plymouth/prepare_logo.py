#!/usr/bin/env python3
"""
TD5 Dash — Plymouth logo preparation
Trims the transparent border from the source Land Rover logo PNG,
resizes it to fit the target height, and generates a matching shimmer bar.

Usage:
    python3 prepare_logo.py <source.png> <logo_dest.png> <shimmer_dest.png>

Called automatically by deploy/setup.sh during Pi first-time setup.
Requires: python3-pil (apt) or Pillow (pip)
"""

import sys
from PIL import Image


TARGET_HEIGHT = 130   # px — roughly 1/3 of the 400px display height
SHIMMER_ALPHA =  80   # 0–255; 80 ≈ 31% opacity — subtle, not blinding


def make_logo(src_path: str, dest_path: str) -> tuple[int, int]:
    """Trim transparent border, resize to TARGET_HEIGHT, save PNG."""
    img = Image.open(src_path).convert("RGBA")

    # Auto-crop: bounding box of non-transparent pixels
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)

    w, h = img.size
    scale = TARGET_HEIGHT / h
    new_w = int(w * scale)
    new_h = TARGET_HEIGHT
    img = img.resize((new_w, new_h), Image.LANCZOS)
    img.save(dest_path, "PNG")
    return new_w, new_h


def make_shimmer(logo_w: int, logo_h: int, dest_path: str) -> None:
    """
    Create a vertical gradient bar for the sweep shimmer effect.
    Width = 1/4 of logo width, height = logo height.
    Alpha profile: triangle wave — transparent at edges, SHIMMER_ALPHA at centre.
    White (255,255,255) RGB so it lightens whatever it overlays.
    """
    shimmer_w = max(40, logo_w // 4)
    img = Image.new("RGBA", (shimmer_w, logo_h), (0, 0, 0, 0))
    pixels = img.load()

    for x in range(shimmer_w):
        t = x / shimmer_w                           # 0.0 → 1.0
        alpha = int(min(t, 1.0 - t) * 2 * SHIMMER_ALPHA)  # triangle: 0 → peak → 0
        for y in range(logo_h):
            pixels[x, y] = (255, 255, 255, alpha)

    img.save(dest_path, "PNG")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <source.png> <logo_dest.png> <shimmer_dest.png>")
        sys.exit(1)

    src, logo_dest, shimmer_dest = sys.argv[1], sys.argv[2], sys.argv[3]

    logo_w, logo_h = make_logo(src, logo_dest)
    make_shimmer(logo_w, logo_h, shimmer_dest)

    print(f"  Logo    : {logo_w}×{logo_h}px → {logo_dest}")
    print(f"  Shimmer : {logo_w // 4}×{logo_h}px → {shimmer_dest}")
