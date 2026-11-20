# -*- coding: utf-8 -*-
"""Render examples/meeting_demo.py into assets/demo.gif.

The frames replay the REAL beats produced by the demo script (real library
calls, real assertions) — the GIF is a recording, not a mock-up.

Usage:
    python tools/render_demo_gif.py            # writes assets/demo.gif
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))

from PIL import Image, ImageDraw, ImageFont

from meeting_demo import beats

W, H = 960, 580
TITLE_H = 32
PAD_X, PAD_TOP = 20, 14
LINE_H = 25
FONT_SIZE = 16
FPS = 8
FRAME_MS = 1000 // FPS
CURSOR_PERIOD = 1.0  # seconds per blink cycle

BG = (13, 17, 23)        # #0d1117
TITLE_BG = (22, 27, 34)  # #161b22
COLORS = {
    "cmd": (126, 231, 135),   # green
    "head": (210, 153, 34),   # yellow
    "say": (201, 209, 217),   # light gray
    "ok": (63, 185, 80),      # green
    "hit": (88, 214, 235),    # cyan
    "dim": (139, 148, 158),   # gray
}

FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"


def load_font():
    return ImageFont.truetype(FONT_PATH, FONT_SIZE)


def draw_frame(font, visible, t):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # title bar with the three dots
    d.rectangle([0, 0, W, TITLE_H], fill=TITLE_BG)
    for i, c in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        x = 16 + i * 20
        d.ellipse([x, 11, x + 10, 21], fill=c)
    d.text((W // 2, TITLE_H // 2), "artl-memory — meeting demo",
           font=font, fill=(139, 148, 158), anchor="mm")
    # body
    y = TITLE_H + PAD_TOP
    last_end = None
    for style, text, _pause in visible:
        color = COLORS[style]
        d.text((PAD_X, y), text, font=font, fill=color)
        last_end = PAD_X + d.textlength(text, font=font)
        y += LINE_H
    # blinking block cursor after the last visible line
    if last_end is not None and (t % CURSOR_PERIOD) < CURSOR_PERIOD / 2:
        cy = y - LINE_H
        d.rectangle([last_end + 4, cy + 4, last_end + 12, cy + LINE_H - 4],
                    fill=(201, 209, 217))
    return img


def main():
    font = load_font()
    seq = list(beats())
    # width sanity check — a clipped line in the README GIF would be shameful
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for _style, text, _p in seq:
        w = probe.textlength(text, font=font)
        assert w <= W - 2 * PAD_X, "line too wide (%.0fpx): %r" % (w, text)

    timeline = []  # (start_time, beat)
    t = 0.0
    for beat in seq:
        timeline.append((t, beat))
        t += beat[2]
    total = t

    frames, durations = [], []
    n_frames = int(total * FPS)
    for f in range(n_frames):
        tf = f / FPS
        visible = [b for start, b in timeline if start <= tf]
        frames.append(draw_frame(font, visible, tf))
        durations.append(FRAME_MS)

    out = ROOT / "assets" / "demo.gif"
    out.parent.mkdir(exist_ok=True)
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True)
    size = out.stat().st_size
    print("wrote %s — %d frames, %.1fs, %.2f MB"
          % (out, len(frames), total, size / 1048576))


if __name__ == "__main__":
    main()
