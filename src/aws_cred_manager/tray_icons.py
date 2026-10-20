"""Tray-icon image generation (Windows/Linux only -- see gui/app.py; macOS
runs as a regular Dock app with no menu-bar icon).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

STATUS_COLORS = {
    "ok": (46, 160, 67),          # green
    "refreshing": (66, 133, 244),  # blue
    "needs_login": (219, 90, 27),  # amber
    "partial_error": (219, 90, 27),  # amber
    "idle": (140, 140, 140),       # grey
    "all_paused": (140, 140, 140),  # grey -- intentional, not an error
    "error": (200, 40, 40),        # red
}

# Tray icons are tiny (Windows ~16-32px, Linux similar) -- icons/icon.png is
# 512x512, drawn for the app icon / .icns / .ico. Handing a tray backend a
# full-resolution image for the status slot is unnecessary and, on some
# backends, can misrender -- always resize down first.
TRAY_ICON_SIZE = 32


def base_icon_image() -> Image.Image:
    icon_path = Path(__file__).parent / "icons" / "icon.png"
    if icon_path.exists():
        img = Image.open(icon_path).convert("RGBA")
    else:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((8, 20, 56, 52), fill=(255, 153, 0, 255))
        draw.ellipse((4, 12, 36, 44), fill=(255, 153, 0, 255))
        draw.ellipse((28, 8, 60, 40), fill=(255, 153, 0, 255))

    if max(img.size) != TRAY_ICON_SIZE:
        img = img.resize((TRAY_ICON_SIZE, TRAY_ICON_SIZE), Image.LANCZOS)
    return img


def status_badge(image: Image.Image, status_key: str) -> Image.Image:
    color = STATUS_COLORS.get(status_key, STATUS_COLORS["error"])
    badged = image.copy()
    draw = ImageDraw.Draw(badged)
    w, h = badged.size
    r = max(int(w * 0.32), 6)
    draw.ellipse((w - r - 1, h - r - 1, w - 1, h - 1), fill=(*color, 255), outline=(255, 255, 255, 255), width=1)
    return badged


def status_key_for(status: str) -> str:
    if status.startswith("error"):
        return "error"
    return status
