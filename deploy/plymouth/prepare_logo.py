#!/usr/bin/env python3
"""
TD5 Dash — Plymouth logo preparation

Trims the transparent border from the source Land Rover logo PNG,
resizes it to fit the display, optionally rotates to compensate for an
unrotated framebuffer (Plymouth runs before X11 rotation is applied),
and generates a shimmer bar masked to the logo's non-transparent pixels.

Usage:
    python3 prepare_logo.py <source.png> <logo_dest.png> <shimmer_dest.png> [rotation]

    rotation: 0, 90, 180, 270 (default 270) — matches DISPLAY_ROTATION in .env.
              The images are counter-rotated so they appear correct on the
              native (portrait) framebuffer.

Called automatically by deploy/setup.sh during Pi first-time setup.
Requires: python3-pil (apt) or Pillow (pip)
"""

import sys
from PIL import Image


# Logo will be sized relative to the LANDSCAPE display (1280x400).
# After counter-rotation the images are stored in portrait orientation
# but will look correct when the panel displays them.
LOGO_HEIGHT_PCT = 60   # % of display short axis (400px) = 240px


def _counter_rotation(display_rotation: int) -> int:
    """Return the PIL rotation angle needed to compensate for the display
    rotation.  Plymouth sees the raw panel (400x1280 portrait).  If the
    display is rotated 270° by xrandr/KMS, we need to rotate the image
    270° (or equivalently -90°) so it appears upright."""
    return display_rotation


def make_logo(src_path: str, dest_path: str, rotation: int) -> tuple[int, int]:
    """Trim, resize for landscape display, then counter-rotate for Plymouth."""
    img = Image.open(src_path).convert("RGBA")

    # Auto-crop non-transparent pixels
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)

    # Size for the landscape view (1280x400).
    # Target height = percentage of the short axis (400px).
    landscape_h = int(400 * LOGO_HEIGHT_PCT / 100)
    w, h = img.size
    scale = landscape_h / h
    new_w = int(w * scale)
    img = img.resize((new_w, landscape_h), Image.LANCZOS)

    # Counter-rotate so the image appears correct on the unrotated panel
    if rotation:
        img = img.rotate(rotation, expand=True)

    img.save(dest_path, "PNG")
    return img.size[0], img.size[1]


def make_shimmer(logo_path: str, logo_w: int, logo_h: int,
                 dest_path: str, rotation: int) -> None:
    """
    Create a shimmer bar masked to the logo's non-transparent pixels.

    The shimmer gradient is built in landscape orientation (matching the
    logo before rotation), masked against the logo alpha, then counter-
    rotated to match the stored logo orientation.
    """
    logo = Image.open(logo_path).convert("RGBA")

    # Work in the pre-rotation (landscape) coordinate space
    if rotation:
        # Undo the counter-rotation to get back to landscape
        logo_landscape = logo.rotate(-rotation, expand=True)
    else:
        logo_landscape = logo

    lw, lh = logo_landscape.size
    shimmer_w = max(40, lw // 4)

    # Build raw shimmer gradient bar
    bar = Image.new("RGBA", (shimmer_w, lh), (0, 0, 0, 0))
    bar_px = bar.load()
    shimmer_alpha = 90
    for x in range(shimmer_w):
        t = x / shimmer_w
        alpha = int(min(t, 1.0 - t) * 2 * shimmer_alpha)
        for y in range(lh):
            bar_px[x, y] = (255, 255, 255, alpha)

    # Mask shimmer to logo alpha (crop logo alpha to shimmer width)
    logo_alpha = logo_landscape.split()[3]
    mask_crop = logo_alpha.crop((0, 0, min(shimmer_w, lw), lh))

    # If shimmer is wider than the logo alpha crop, pad the mask
    if shimmer_w > lw:
        padded = Image.new("L", (shimmer_w, lh), 0)
        padded.paste(mask_crop, (0, 0))
        mask_crop = padded

    canvas = Image.new("RGBA", (shimmer_w, lh), (0, 0, 0, 0))
    canvas.paste(bar, (0, 0))

    # Zero out shimmer where logo is transparent
    canvas_alpha = canvas.split()[3]
    masked_alpha = Image.composite(
        canvas_alpha,
        Image.new("L", (shimmer_w, lh), 0),
        mask_crop,
    )
    canvas.putalpha(masked_alpha)

    # Counter-rotate to match the stored logo
    if rotation:
        canvas = canvas.rotate(rotation, expand=True)

    canvas.save(dest_path, "PNG")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <source.png> <logo_dest.png> <shimmer_dest.png> [rotation]")
        sys.exit(1)

    src, logo_dest, shimmer_dest = sys.argv[1], sys.argv[2], sys.argv[3]
    rotation = int(sys.argv[4]) if len(sys.argv) > 4 else 270

    logo_w, logo_h = make_logo(src, logo_dest, rotation)
    make_shimmer(logo_dest, logo_w, logo_h, shimmer_dest, rotation)

    print(f"  Logo    : {logo_w}x{logo_h}px (rotation={rotation}) -> {logo_dest}")
    print(f"  Shimmer : masked to logo alpha -> {shimmer_dest}")
