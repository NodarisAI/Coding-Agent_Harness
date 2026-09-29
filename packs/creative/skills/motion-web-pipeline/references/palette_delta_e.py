#!/usr/bin/env python3
"""Compare the dominant palette of a built page against a reference screenshot.

Usage: python3 palette_delta_e.py reference.png built.png [--colours 8] [--mean 5] [--max 20]
Exit 0 when mean and max CIE delta E 2000 are within the thresholds, 2 when not.
Needs Pillow. Idea from Design DNA's verification script (zanwei/design-dna, MIT), rebuilt.
"""
import argparse
import math
import sys

from PIL import Image


def palette(path, n):
    im = Image.open(path).convert("RGB")
    im.thumbnail((400, 400))
    q = im.quantize(colors=n, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[: n * 3]
    counts = sorted(q.getcolors(), reverse=True)
    total = sum(c for c, _ in counts)
    return [(tuple(pal[i * 3 : i * 3 + 3]), c / total) for c, i in counts]


def to_lab(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(v) for v in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e_2000(lab1, lab2):
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cm = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cm**7 / (Cm**7 + 25**7)))
    a1p, a2p = a1 * (1 + G), a2 * (1 + G)
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    dh = h2p - h1p
    if C1p * C2p == 0:
        dh = 0
    elif dh > 180:
        dh -= 360
    elif dh < -180:
        dh += 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dh / 2))
    Lpm, Cpm = (L1 + L2) / 2, (C1p + C2p) / 2
    hsum = h1p + h2p
    if C1p * C2p == 0:
        hpm = hsum
    elif abs(h1p - h2p) <= 180:
        hpm = hsum / 2
    else:
        hpm = (hsum + 360) / 2 if hsum < 360 else (hsum - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hpm - 30)) + 0.24 * math.cos(math.radians(2 * hpm))
         + 0.32 * math.cos(math.radians(3 * hpm + 6)) - 0.20 * math.cos(math.radians(4 * hpm - 63)))
    dtheta = 30 * math.exp(-(((hpm - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cpm**7 / (Cpm**7 + 25**7))
    Sl = 1 + 0.015 * (Lpm - 50) ** 2 / math.sqrt(20 + (Lpm - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cpm, 1 + 0.015 * Cpm * T
    Rt = -math.sin(math.radians(2 * dtheta)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("reference")
    p.add_argument("built")
    p.add_argument("--colours", type=int, default=8)
    p.add_argument("--mean", type=float, default=5.0)
    p.add_argument("--max", type=float, default=20.0)
    a = p.parse_args()
    ref, built = palette(a.reference, a.colours), palette(a.built, a.colours)
    built_lab = [to_lab(c) for c, _ in built]
    rows, weighted = [], 0.0
    for rgb, share in ref:
        d = min(delta_e_2000(to_lab(rgb), bl) for bl in built_lab)
        rows.append((rgb, share, d))
        weighted += share * d
    worst = max(d for _, _, d in rows)
    for rgb, share, d in rows:
        print(f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}  {share:6.1%}  delta E {d:5.1f}")
    ok = weighted <= a.mean and worst <= a.max
    print(f"Weighted mean delta E {weighted:.1f} (limit {a.mean}), max {worst:.1f} (limit {a.max}): "
          f"{'pass' if ok else 'fail'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
