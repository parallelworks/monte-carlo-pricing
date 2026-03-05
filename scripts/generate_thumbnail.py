#!/usr/bin/env python3
"""Generate a 512x512 thumbnail for the Monte Carlo Pricing workflow.

Produces a convergence chart with narrowing confidence band and a histogram
overlay, rendered in the dashboard color scheme. Uses pure-Python PNG output
(no Pillow required).
"""

import math
import random
import struct
import zlib
import os

WIDTH = HEIGHT = 512
BG = (10, 14, 23)         # #0a0e17
CYAN = (34, 211, 238)     # #22d3ee
BAND_COLOR = (34, 100, 180)
HIST_TOP = (34, 211, 238)
HIST_BOT = (100, 60, 220)
GRID_COLOR = (30, 40, 60)
LABEL_CYAN = CYAN


def lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def blend(bg, fg, alpha):
    return tuple(int(bg[i] * (1 - alpha) + fg[i] * alpha) for i in range(3))


def draw_circle_mask(cx, cy, r, x, y):
    """Return True if (x,y) is inside the circle."""
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def write_png(pixels, width, height, filepath):
    """Write pixels as a PNG file without Pillow (pure Python)."""
    def make_chunk(chunk_type, data):
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    header = b"\x89PNG\r\n\x1a\n"
    # RGBA (color type 6)
    ihdr = make_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))

    raw = b""
    for y in range(height):
        raw += b"\x00"
        for x in range(width):
            r, g, b, a = pixels[y * width + x]
            raw += struct.pack("BBBB", r, g, b, a)

    idat = make_chunk(b"IDAT", zlib.compress(raw, 9))
    iend = make_chunk(b"IEND", b"")

    with open(filepath, "wb") as f:
        f.write(header + ihdr + idat + iend)


def generate():
    random.seed(42)
    cx, cy, radius = WIDTH // 2, HEIGHT // 2, WIDTH // 2 - 1

    # --- Generate convergence data ---
    # Simulate MC convergence: price estimate narrowing toward true value
    true_price = 0.55  # normalized 0..1 in chart space
    n_points = 200
    prices = []
    ci_upper = []
    ci_lower = []
    for i in range(n_points):
        t = (i + 1) / n_points
        noise = random.gauss(0, 1) * 0.15 / math.sqrt(i + 1)
        p = true_price + noise + 0.12 * math.exp(-3 * t)
        half_ci = 0.25 / math.sqrt(i + 1) * 3
        prices.append(p)
        ci_upper.append(p + half_ci)
        ci_lower.append(p - half_ci)

    # --- Generate histogram data ---
    n_bins = 40
    hist = [0] * n_bins
    for _ in range(5000):
        v = random.gauss(0.55, 0.12)
        b = int((v - 0.1) / 0.8 * n_bins)
        if 0 <= b < n_bins:
            hist[b] += 1
    max_hist = max(hist)

    # --- Chart layout ---
    margin_left = 45
    margin_right = 30
    margin_top = 40
    chart_height = 260
    hist_top_y = 320
    hist_height = 150
    chart_width = WIDTH - margin_left - margin_right

    # --- Initialize pixel buffer (RGBA) ---
    pixels = [BG + (255,)] * (WIDTH * HEIGHT)

    def set_pixel(x, y, color, alpha=1.0):
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            if not draw_circle_mask(cx, cy, radius, x, y):
                pixels[y * WIDTH + x] = (0, 0, 0, 0)
                return
            if alpha < 1.0:
                bg_c = pixels[y * WIDTH + x][:3]
                color = blend(bg_c, color, alpha)
            pixels[y * WIDTH + x] = color + (255,)

    # --- Draw grid lines ---
    for i in range(5):
        gy = margin_top + int(i * chart_height / 4)
        for x in range(margin_left, margin_left + chart_width):
            set_pixel(x, gy, GRID_COLOR, 0.5)

    for i in range(5):
        gx = margin_left + int(i * chart_width / 4)
        for y in range(margin_top, margin_top + chart_height):
            set_pixel(gx, y, GRID_COLOR, 0.5)

    # --- Draw confidence band ---
    for i in range(n_points):
        x = margin_left + int(i * chart_width / n_points)
        y_upper = margin_top + int((1 - ci_upper[i]) * chart_height)
        y_lower = margin_top + int((1 - ci_lower[i]) * chart_height)
        y_upper = max(margin_top, min(margin_top + chart_height, y_upper))
        y_lower = max(margin_top, min(margin_top + chart_height, y_lower))
        for y in range(y_upper, y_lower + 1):
            set_pixel(x, y, BAND_COLOR, 0.25)

    # --- Draw convergence line ---
    for i in range(1, n_points):
        x0 = margin_left + int((i - 1) * chart_width / n_points)
        x1 = margin_left + int(i * chart_width / n_points)
        y0 = margin_top + int((1 - prices[i - 1]) * chart_height)
        y1 = margin_top + int((1 - prices[i]) * chart_height)
        # Bresenham-ish thick line
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for s in range(steps + 1):
            t = s / steps
            lx = int(x0 + (x1 - x0) * t)
            ly = int(y0 + (y1 - y0) * t)
            for dy in range(-1, 2):
                set_pixel(lx, ly + dy, CYAN)

    # --- Draw true price dashed line ---
    true_y = margin_top + int((1 - true_price) * chart_height)
    for x in range(margin_left, margin_left + chart_width):
        if (x // 6) % 2 == 0:
            set_pixel(x, true_y, (180, 180, 180), 0.4)

    # --- Draw histogram ---
    bar_w = max(1, chart_width // n_bins - 1)
    for i in range(n_bins):
        bar_h = int(hist[i] / max_hist * hist_height * 0.9)
        bx = margin_left + int(i * chart_width / n_bins)
        by = hist_top_y + hist_height - bar_h
        t = i / (n_bins - 1)
        bar_color = lerp_color(HIST_BOT, HIST_TOP, t)
        for dy in range(bar_h):
            alpha = 0.5 + 0.4 * (dy / max(bar_h, 1))
            for dx in range(bar_w):
                set_pixel(bx + dx, by + dy, bar_color, alpha)

    # --- Draw circle border ---
    for angle_step in range(int(2 * math.pi * radius * 2)):
        a = angle_step / (radius * 2)
        for dr in range(-1, 2):
            bx = int(cx + (radius + dr) * math.cos(a))
            by = int(cy + (radius + dr) * math.sin(a))
            if 0 <= bx < WIDTH and 0 <= by < HEIGHT:
                pixels[by * WIDTH + bx] = CYAN + (200,)

    # --- Apply circular crop (transparent outside) ---
    for y in range(HEIGHT):
        for x in range(WIDTH):
            if not draw_circle_mask(cx, cy, radius, x, y):
                pixels[y * WIDTH + x] = (0, 0, 0, 0)

    # --- Write PNG ---
    out_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_path = os.path.join(out_dir, "thumbnail.png")
    write_png(pixels, WIDTH, HEIGHT, out_path)
    print(f"Wrote {out_path} ({WIDTH}x{HEIGHT})")


if __name__ == "__main__":
    generate()
