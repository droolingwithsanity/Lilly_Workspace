#!/usr/bin/env python3
"""Generate clay-style animal avatars matching the lilly_ai.py canvas code.

Produces PNG images for all 9 hive-mind avatars:
  puppy, fox, cat, bear, bunny, owl, deer, wolf, raccoon
"""

import math
import io
import os
import sys
from PIL import Image, ImageDraw

# ── Palettes (matches lilly_ai.py PAL exactly) ──────────────────────
PAL = {
    "puppy":   {"bg":"#e8e4f0","head":"#d4d0de","muzzle":"#c8c4d2","ear":"#c4c0cc","earInner":"#f0d8e8","nose":"#d4a0b8"},
    "fox":     {"bg":"#f5e4d0","head":"#e8905a","muzzle":"#f8e0c0","ear":"#e07840","earInner":"#f8c080","nose":"#c05030"},
    "cat":     {"bg":"#ece8f4","head":"#c8c0d4","muzzle":"#f0dce8","ear":"#b8b0c4","earInner":"#f0c8dc","nose":"#d898b0"},
    "bear":    {"bg":"#ede0d8","head":"#b09080","muzzle":"#cdb8a8","ear":"#a08070","earInner":"#c8a890","nose":"#7a5a4a"},
    "bunny":   {"bg":"#eeeaf6","head":"#d4cce0","muzzle":"#c8c0d4","ear":"#ccc4d8","earInner":"#f0cce0","nose":"#e8a8c0"},
    "owl":     {"bg":"#e8e0d0","head":"#8a7a60","muzzle":"#c8b898","ear":"#7a6a50","earInner":"#c0a878","nose":"#5a4a38"},
    "deer":    {"bg":"#f0e8d8","head":"#c8a888","muzzle":"#e8d8c0","ear":"#b89870","earInner":"#e0c8a8","nose":"#5a4838"},
    "wolf":    {"bg":"#e0dde4","head":"#706878","muzzle":"#a898a8","ear":"#605868","earInner":"#a090a8","nose":"#383040"},
    "raccoon": {"bg":"#e0e0e4","head":"#808080","muzzle":"#c0c0c0","ear":"#686868","earInner":"#a8a8a8","nose":"#404040"},
}

CHAR_NAMES = {
    "puppy":"Lilly","fox":"Fox","cat":"Cat","bear":"Bear","bunny":"Bunny",
    "owl":"Owl","deer":"Deer","wolf":"Wolf","raccoon":"Raccoon",
}

CHAR_ROLES = {
    "puppy":"Alpha Assistant","fox":"Creative Strategist","cat":"Precision Analyst",
    "bear":"Steadfast Guardian","bunny":"Energetic Scout","owl":"Wisdom Keeper",
    "deer":"Gentle Healer","wolf":"Fierce Protector","raccoon":"Tech Tinkerer",
}


def _hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _lighten(hex_color, amount=25):
    r, g, b = _hex_to_rgb(hex_color)
    return (min(255, r + amount), min(255, g + amount), min(255, b + amount))


def _alpha_color(hex_color, alpha):
    r, g, b = _hex_to_rgb(hex_color)
    return (r, g, b, int(alpha * 255))


def _blend_gradient(draw, cx, cy, r, base_hex, lighten_amt=28):
    """Approximate the clay gradient fill: lighter top-left, darker bottom-right."""
    base = _hex_to_rgb(base_hex)
    light = _lighten(base_hex, lighten_amt)
    # Simple radial-ish: draw concentric ellipses blending from light (center) to base (edge)
    for i in range(5):
        t = i / 4.0
        rx = r * (1.0 - t * 0.6)
        ry = r * (1.0 - t * 0.6)
        cr = int(light[0] + (base[0] - light[0]) * t)
        cg = int(light[1] + (base[1] - light[1]) * t)
        cb = int(light[2] + (base[2] - light[2]) * t)
        draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=(cr, cg, cb))


def draw_clay_bg(draw, W, H, bg_hex):
    """Clay background: circle with gradient sheen."""
    draw.rectangle([0, 0, W, H], fill=_hex_to_rgb(bg_hex))
    r = int(W * 0.47)
    cx, cy = W // 2, H // 2
    # Base circle
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_hex_to_rgb(bg_hex))
    # Light sheen (top-left highlight)
    for i in range(8):
        t = i / 7.0
        sr = int(r * (1.0 - t * 0.5))
        alpha = int(148 * (1.0 - t))  # bright at center, fading
        col = (255, 255, 255, alpha)
        draw.ellipse([cx - sr, cy - sr, cx + sr, cy + sr], fill=col)


def draw_clay_eyes(draw, cx, cy, r, blink=False):
    """Shared clay eyes with pupil, iris, and highlights."""
    eye_r = int(r * 0.12)
    lx, rx = cx - int(r * 0.26), cx + int(r * 0.26)
    ey = cy
    for ex in (lx, rx):
        if blink:
            # Blink: just a line
            draw.line([(ex - eye_r, ey), (ex + eye_r, ey)],
                      fill=(60, 40, 80, 140), width=max(1, int(r * 0.045)))
        else:
            # White
            draw.ellipse([ex - eye_r, ey - eye_r, ex + eye_r, ey + eye_r],
                         fill=(255, 255, 255, 255))
            # Shadow ring
            sr = int(eye_r * 0.82)
            draw.ellipse([ex - sr, ey - sr, ex + sr, ey + sr],
                         fill=(90, 60, 120, 38))
            # Iris
            ir = int(eye_r * 0.46)
            draw.ellipse([ex - ir, ey - ir, ex + ir, ey + ir],
                         fill=_hex_to_rgb('#3d2e52'))
            # Highlight 1
            hr1 = int(eye_r * 0.22)
            draw.ellipse([ex - int(eye_r * 0.12) - hr1, ey - int(eye_r * 0.14) - hr1,
                          ex - int(eye_r * 0.12) + hr1, ey - int(eye_r * 0.14) + hr1],
                         fill=(255, 255, 255, 224))
            # Highlight 2
            hr2 = int(eye_r * 0.09)
            draw.ellipse([ex + int(eye_r * 0.12) - hr2, ey + int(eye_r * 0.10) - hr2,
                          ex + int(eye_r * 0.12) + hr2, ey + int(eye_r * 0.10) + hr2],
                         fill=(255, 255, 255, 140))


def draw_raccoon_eyes(draw, cx, cy, r, blink=False):
    """Raccoon-specific larger eyes."""
    eye_r = int(r * 0.15)
    lx, rx = cx - int(r * 0.26), cx + int(r * 0.26)
    ey = cy
    for ex in (lx, rx):
        if blink:
            draw.line([(ex - eye_r, ey), (ex + eye_r, ey)],
                      fill=(40, 40, 40, 140), width=max(1, int(r * 0.045)))
        else:
            draw.ellipse([ex - eye_r, ey - eye_r, ex + eye_r, ey + eye_r],
                         fill=(255, 255, 255, 255))
            sr = int(eye_r * 0.82)
            draw.ellipse([ex - sr, ey - sr, ex + sr, ey + sr],
                         fill=(40, 40, 40, 31))
            ir = int(eye_r * 0.48)
            draw.ellipse([ex - ir, ey - ir, ex + ir, ey + ir],
                         fill=_hex_to_rgb('#2a2a2a'))
            hr1 = int(eye_r * 0.24)
            draw.ellipse([ex - int(eye_r * 0.14) - hr1, ey - int(eye_r * 0.16) - hr1,
                          ex - int(eye_r * 0.14) + hr1, ey - int(eye_r * 0.16) + hr1],
                         fill=(255, 255, 255, 235))
            hr2 = int(eye_r * 0.10)
            draw.ellipse([ex + int(eye_r * 0.12) - hr2, ey + int(eye_r * 0.10) - hr2,
                          ex + int(eye_r * 0.12) + hr2, ey + int(eye_r * 0.10) + hr2],
                         fill=(255, 255, 255, 153))


def draw_clay_smile(draw, cx, cy, r):
    """Shared clay smile arcs."""
    sw = max(1, int(r * 0.05))
    col = (120, 80, 100, 107)
    for side in (-1, 1):
        sx = cx + side * int(r * 0.11)
        sy = cy + int(r * 0.28)
        sr = int(r * 0.11)
        if side == -1:
            draw.arc([sx - sr, sy - sr, sx + sr, sy + sr],
                     start=4, end=math.degrees(math.pi * 0.78),
                     fill=col, width=sw)
        else:
            draw.arc([sx - sr, sy - sr, sx + sr, sy + sr],
                     start=math.degrees(math.pi * 0.22),
                     end=math.degrees(math.pi * 0.92),
                     fill=col, width=sw)


def draw_blush(draw, cx, cy, r, blush_hex):
    """Rosy cheek blush."""
    col = _alpha_color(blush_hex, 0.30)
    for side in (-1, 1):
        bx = cx + side * int(r * 0.38)
        by = cy + int(r * 0.18)
        draw.ellipse([bx - int(r * 0.12), by - int(r * 0.08),
                       bx + int(r * 0.12), by + int(r * 0.08)],
                     fill=col)


# ── Per-animal drawing ──────────────────────────────────────────────

def draw_puppy(draw, cx, cy, r, W, H):
    p = PAL["puppy"]
    draw_clay_bg(draw, W, H, p["bg"])
    # Floppy ears
    ear_rx, ear_ry = int(r * 0.26), int(r * 0.52)
    ear_inner_rx, ear_inner_ry = int(r * 0.14), int(r * 0.36)
    for side in (-1, 1):
        ex = cx + side * int(r * 0.60)
        ey = cy - int(r * 0.38)
        draw.ellipse([ex - ear_rx, ey - ear_ry, ex + ear_rx, ey + ear_ry],
                     fill=_hex_to_rgb(p["ear"]))
        draw.ellipse([ex - ear_inner_rx, ey + int(r * 0.06) - ear_inner_ry,
                       ex + ear_inner_rx, ey + int(r * 0.06) + ear_inner_ry],
                     fill=_hex_to_rgb(p["earInner"]))
    # Head
    hw, hh = int(r * 0.70), int(r * 0.54)
    head_r = int(r * 0.38)
    draw.rounded_rectangle([cx - hw, cy - hh, cx + hw, cy + hh],
                           radius=head_r, fill=_hex_to_rgb(p["head"]))
    # Muzzle
    draw.ellipse([cx - int(r * 0.40), cy + int(r * 0.30) - int(r * 0.24),
                   cx + int(r * 0.40), cy + int(r * 0.30) + int(r * 0.24)],
                 fill=_hex_to_rgb(p["muzzle"]))
    # Nose
    nose_rx, nose_ry = int(r * 0.11), int(r * 0.08)
    draw.ellipse([cx - nose_rx, cy + int(r * 0.14) - nose_ry,
                   cx + nose_rx, cy + int(r * 0.14) + nose_ry],
                 fill=_hex_to_rgb(p["nose"]))
    # Nose highlight
    nrh_rx = int(nose_rx * 0.4)
    nrh_ry = int(nose_ry * 0.35)
    draw.ellipse([cx - nrh_rx, cy + int(r * 0.14) - int(nose_ry * 0.5) - nrh_ry,
                   cx + nrh_rx, cy + int(r * 0.14) - int(nose_ry * 0.5) + nrh_ry],
                 fill=_hex_to_rgb('#e8bccf'))
    # Eyes
    draw_clay_eyes(draw, cx, cy, r)
    # Smile
    draw_clay_smile(draw, cx, cy, r)
    # Blush
    draw_blush(draw, cx, cy, r, '#dcc0b4')
    # Brow lines
    brow_w = max(1, int(r * 0.025))
    for side in (-1, 1):
        bx1 = cx + side * int(r * 0.38)
        bx2 = cx + side * int(r * 0.18)
        by = cy - int(r * 0.10)
        draw.line([(bx1, by), (bx2, by)], fill=(80, 60, 100, 100), width=brow_w)


def draw_fox(draw, cx, cy, r, W, H):
    p = PAL["fox"]
    draw_clay_bg(draw, W, H, p["bg"])
    # Pointed ears
    for side in (-1, 1):
        ear_cx = cx + side * int(r * 0.38)
        ear_cy = cy - int(r * 0.52)
        pts = [
            (cx + side * int(r * 0.14), cy - int(r * 0.40)),
            (ear_cx, ear_cy - int(r * 0.40)),
            (cx + side * int(r * 0.64), cy - int(r * 0.38)),
        ]
        draw.polygon(pts, fill=_hex_to_rgb(p["ear"]))
        inner_pts = [
            (cx + side * int(r * 0.20), cy - int(r * 0.42)),
            (cx + side * int(r * 0.37), ear_cy - int(r * 0.24)),
            (cx + side * int(r * 0.54), cy - int(r * 0.40)),
        ]
        draw.polygon(inner_pts, fill=_hex_to_rgb(p["earInner"]))
    # Head
    hw, hh = int(r * 0.70), int(r * 0.51)
    head_r = int(r * 0.34)
    draw.rounded_rectangle([cx - hw, cy - hh, cx + hw, cy + hh],
                           radius=head_r, fill=_hex_to_rgb(p["head"]))
    # White muzzle
    draw.ellipse([cx - int(r * 0.44), cy + int(r * 0.18) - int(r * 0.38),
                   cx + int(r * 0.44), cy + int(r * 0.18) + int(r * 0.38)],
                 fill=_hex_to_rgb(p["muzzle"]))
    # Nose
    nose_rx, nose_ry = int(r * 0.13), int(r * 0.09)
    draw.ellipse([cx - nose_rx, cy + int(r * 0.10) - nose_ry,
                   cx + nose_rx, cy + int(r * 0.10) + nose_ry],
                 fill=_hex_to_rgb(p["nose"]))
    # Whisker dots
    for wx, wy in [(-r*0.28, r*0.16), (-r*0.38, r*0.22), (-r*0.42, r*0.30),
                    (r*0.28, r*0.16), (r*0.38, r*0.22), (r*0.42, r*0.30)]:
        draw.ellipse([int(cx + wx) - 1, int(cy + wy) - 1,
                       int(cx + wx) + 1, int(cy + wy) + 1],
                     fill=(100, 60, 30, 89))
    draw_clay_eyes(draw, cx, cy, r)
    draw_clay_smile(draw, cx, cy, r)
    draw_blush(draw, cx, cy, r, '#e69664')


def draw_cat(draw, cx, cy, r, W, H):
    p = PAL["cat"]
    draw_clay_bg(draw, W, H, p["bg"])
    # Pointed ears
    for side in (-1, 1):
        ear_cx = cx + side * int(r * 0.40)
        ear_cy = cy - int(r * 0.50)
        pts = [
            (cx + side * int(r * 0.20), cy - int(r * 0.38)),
            (ear_cx, ear_cy - int(r * 0.40)),
            (cx + side * int(r * 0.62), cy - int(r * 0.36)),
        ]
        draw.polygon(pts, fill=_hex_to_rgb(p["ear"]))
        inner_pts = [
            (cx + side * int(r * 0.26), cy - int(r * 0.40)),
            (cx + side * int(r * 0.45), ear_cy - int(r * 0.24)),
            (cx + side * int(r * 0.56), cy - int(r * 0.38)),
        ]
        draw.polygon(inner_pts, fill=_hex_to_rgb(p["earInner"]))
    # Head
    hw, hh = int(r * 0.68), int(r * 0.52)
    head_r = int(r * 0.32)
    draw.rounded_rectangle([cx - hw, cy - hh, cx + hw, cy + hh],
                           radius=head_r, fill=_hex_to_rgb(p["head"]))
    # Muzzle
    draw.ellipse([cx - int(r * 0.34), cy + int(r * 0.28) - int(r * 0.22),
                   cx + int(r * 0.34), cy + int(r * 0.28) + int(r * 0.22)],
                 fill=_hex_to_rgb(p["muzzle"]))
    # Nose
    nose_rx, nose_ry = int(r * 0.10), int(r * 0.07)
    draw.ellipse([cx - nose_rx, cy + int(r * 0.13) - nose_ry,
                   cx + nose_rx, cy + int(r * 0.13) + nose_ry],
                 fill=_hex_to_rgb(p["nose"]))
    # Whiskers
    ww = max(1, int(r * 0.025))
    for s in (-1, 1):
        draw.line([(s * int(r * 0.08) + cx, int(r * 0.20) + cy),
                    (s * int(r * 0.52) + cx, int(r * 0.13) + cy)],
                  fill=(80, 60, 100, 71), width=ww)
        draw.line([(s * int(r * 0.08) + cx, int(r * 0.26) + cy),
                    (s * int(r * 0.52) + cx, int(r * 0.26) + cy)],
                  fill=(80, 60, 100, 71), width=ww)
    draw_clay_eyes(draw, cx, cy, r)
    draw_clay_smile(draw, cx, cy, r)
    draw_blush(draw, cx, cy, r, '#d2aabe')


def draw_bear(draw, cx, cy, r, W, H):
    p = PAL["bear"]
    draw_clay_bg(draw, W, H, p["bg"])
    # Round ears
    for side in (-1, 1):
        ear_cx = cx + side * int(r * 0.58)
        ear_cy = cy - int(r * 0.50)
        ear_r = int(r * 0.28)
        draw.ellipse([ear_cx - ear_r, ear_cy - ear_r, ear_cx + ear_r, ear_cy + ear_r],
                     fill=_hex_to_rgb(p["ear"]))
        inner_r = int(r * 0.17)
        draw.ellipse([ear_cx - inner_r, ear_cy - inner_r, ear_cx + inner_r, ear_cy + inner_r],
                     fill=_hex_to_rgb(p["earInner"]))
    # Head
    hw, hh = int(r * 0.70), int(r * 0.55)
    head_r = int(r * 0.42)
    draw.rounded_rectangle([cx - hw, cy - hh, cx + hw, cy + hh],
                           radius=head_r, fill=_hex_to_rgb(p["head"]))
    # Muzzle snout
    draw.ellipse([cx - int(r * 0.40), cy + int(r * 0.28) - int(r * 0.26),
                   cx + int(r * 0.40), cy + int(r * 0.28) + int(r * 0.26)],
                 fill=_hex_to_rgb(p["muzzle"]))
    # Nose
    nose_rx, nose_ry = int(r * 0.15), int(r * 0.10)
    draw.ellipse([cx - nose_rx, cy + int(r * 0.12) - nose_ry,
                   cx + nose_rx, cy + int(r * 0.12) + nose_ry],
                 fill=_hex_to_rgb(p["nose"]))
    draw_clay_eyes(draw, cx, cy, r)
    draw_clay_smile(draw, cx, cy, r)
    draw_blush(draw, cx, cy, r, '#c8a08c')


def draw_bunny(draw, cx, cy, r, W, H):
    p = PAL["bunny"]
    draw_clay_bg(draw, W, H, p["bg"])
    # Tall ears
    for side in (-1, 1):
        ear_cx = cx + side * int(r * 0.30)
        ear_cy = cy - int(r * 0.44)
        ear_rx, ear_ry = int(r * 0.15), int(r * 0.38)
        draw.ellipse([ear_cx - ear_rx, ear_cy - ear_ry - int(r * 0.38),
                       ear_cx + ear_rx, ear_cy + ear_ry - int(r * 0.38)],
                     fill=_hex_to_rgb(p["ear"]))
        inner_rx, inner_ry = int(r * 0.07), int(r * 0.28)
        draw.ellipse([ear_cx - inner_rx, ear_cy - inner_ry - int(r * 0.38),
                       ear_cx + inner_rx, ear_cy + inner_ry - int(r * 0.38)],
                     fill=_hex_to_rgb(p["earInner"]))
    # Head
    hw, hh = int(r * 0.68), int(r * 0.54)
    head_r = int(r * 0.40)
    draw.rounded_rectangle([cx - hw, cy - hh, cx + hw, cy + hh],
                           radius=head_r, fill=_hex_to_rgb(p["head"]))
    # Chubby muzzle
    draw.ellipse([cx - int(r * 0.36), cy + int(r * 0.30) - int(r * 0.22),
                   cx + int(r * 0.36), cy + int(r * 0.30) + int(r * 0.22)],
                 fill=_hex_to_rgb(p["muzzle"]))
    # Nose
    nose_rx, nose_ry = int(r * 0.10), int(r * 0.07)
    draw.ellipse([cx - nose_rx, cy + int(r * 0.14) - nose_ry,
                   cx + nose_rx, cy + int(r * 0.14) + nose_ry],
                 fill=_hex_to_rgb(p["nose"]))
    draw_clay_eyes(draw, cx, cy, r)
    draw_clay_smile(draw, cx, cy, r)
    draw_blush(draw, cx, cy, r, '#dcb4c8')


def draw_owl(draw, cx, cy, r, W, H):
    p = PAL["owl"]
    draw_clay_bg(draw, W, H, p["bg"])
    # Ear tufts
    for side in (-1, 1):
        ear_cx = cx + side * int(r * 0.32)
        ear_cy = cy - int(r * 0.40)
        pts = [
            (cx + side * int(r * 0.18), cy - int(r * 0.38)),
            (ear_cx, ear_cy - int(r * 0.42)),
            (cx + side * int(r * 0.50), cy - int(r * 0.36)),
        ]
        draw.polygon(pts, fill=_hex_to_rgb(p["ear"]))
        inner_pts = [
            (cx + side * int(r * 0.24), cy - int(r * 0.40)),
            (cx + side * int(r * 0.32), ear_cy - int(r * 0.28)),
            (cx + side * int(r * 0.44), cy - int(r * 0.38)),
        ]
        draw.polygon(inner_pts, fill=_hex_to_rgb(p["earInner"]))
    # Round head
    head_r = int(r * 0.68)
    head_cy = cy - int(r * 0.06)
    draw.ellipse([cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r],
                 fill=_hex_to_rgb(p["head"]))
    # Facial disc
    disc_rx, disc_ry = int(r * 0.50), int(r * 0.46)
    draw.ellipse([cx - disc_rx, head_cy - disc_ry, cx + disc_rx, head_cy + disc_ry],
                 fill=_hex_to_rgb(p["muzzle"]))
    # Beak
    pts = [
        (cx - int(r * 0.06), cy + int(r * 0.12)),
        (cx, cy + int(r * 0.24)),
        (cx + int(r * 0.06), cy + int(r * 0.12)),
    ]
    draw.polygon(pts, fill=_hex_to_rgb('#c8a050'))
    draw_clay_eyes(draw, cx, cy, r)
    draw_clay_smile(draw, cx, cy, r)
    draw_blush(draw, cx, cy, r, '#d2be9f')


def draw_deer(draw, cx, cy, r, W, H):
    p = PAL["deer"]
    draw_clay_bg(draw, W, H, p["bg"])
    # Elegant ears
    for side in (-1, 1):
        ear_cx = cx + side * int(r * 0.32)
        ear_cy = cy - int(r * 0.42)
        pts = [
            (cx + side * int(r * 0.16), cy - int(r * 0.36)),
            (ear_cx, ear_cy - int(r * 0.46)),
            (cx + side * int(r * 0.54), cy - int(r * 0.34)),
        ]
        draw.polygon(pts, fill=_hex_to_rgb(p["ear"]))
        inner_pts = [
            (cx + side * int(r * 0.22), cy - int(r * 0.38)),
            (cx + side * int(r * 0.34), ear_cy - int(r * 0.30)),
            (cx + side * int(r * 0.48), cy - int(r * 0.36)),
        ]
        draw.polygon(inner_pts, fill=_hex_to_rgb(p["earInner"]))
    # Oval head
    hw, hh = int(r * 0.58), int(r * 0.54)
    head_r = int(r * 0.38)
    draw.rounded_rectangle([cx - hw, cy - hh, cx + hw, cy + hh],
                           radius=head_r, fill=_hex_to_rgb(p["head"]))
    # Muzzle
    draw.ellipse([cx - int(r * 0.30), cy + int(r * 0.22) - int(r * 0.24),
                   cx + int(r * 0.30), cy + int(r * 0.22) + int(r * 0.24)],
                 fill=_hex_to_rgb(p["muzzle"]))
    # Nose
    nose_rx, nose_ry = int(r * 0.08), int(r * 0.06)
    draw.ellipse([cx - nose_rx, cy + int(r * 0.10) - nose_ry,
                   cx + nose_rx, cy + int(r * 0.10) + nose_ry],
                 fill=_hex_to_rgb('#3a2a18'))
    # Antler nubs
    for side in (-1, 1):
        antler_r = int(r * 0.08)
        antler_cx = cx + side * int(r * 0.30)
        antler_cy = cy - int(r * 0.56)
        draw.ellipse([antler_cx - antler_r, antler_cy - antler_r,
                       antler_cx + antler_r, antler_cy + antler_r],
                     fill=_hex_to_rgb('#a08860'))
    draw_clay_eyes(draw, cx, cy, r)
    draw_clay_smile(draw, cx, cy, r)
    draw_blush(draw, cx, cy, r, '#dcab96')


def draw_wolf(draw, cx, cy, r, W, H):
    p = PAL["wolf"]
    draw_clay_bg(draw, W, H, p["bg"])
    # Pointed ears
    for side in (-1, 1):
        ear_cx = cx + side * int(r * 0.36)
        ear_cy = cy - int(r * 0.42)
        pts = [
            (cx + side * int(r * 0.12), cy - int(r * 0.38)),
            (ear_cx, ear_cy - int(r * 0.48)),
            (cx + side * int(r * 0.60), cy - int(r * 0.36)),
        ]
        draw.polygon(pts, fill=_hex_to_rgb(p["ear"]))
        inner_pts = [
            (cx + side * int(r * 0.20), cy - int(r * 0.40)),
            (cx + side * int(r * 0.36), ear_cy - int(r * 0.32)),
            (cx + side * int(r * 0.52), cy - int(r * 0.38)),
        ]
        draw.polygon(inner_pts, fill=_hex_to_rgb(p["earInner"]))
    # Elongated head
    hw, hh = int(r * 0.68), int(r * 0.53)
    head_r = int(r * 0.34)
    draw.rounded_rectangle([cx - hw, cy - hh, cx + hw, cy + hh],
                           radius=head_r, fill=_hex_to_rgb(p["head"]))
    # Muzzle
    draw.ellipse([cx - int(r * 0.36), cy + int(r * 0.22) - int(r * 0.22),
                   cx + int(r * 0.36), cy + int(r * 0.22) + int(r * 0.22)],
                 fill=_hex_to_rgb(p["muzzle"]))
    # Nose (large, dark)
    nose_rx, nose_ry = int(r * 0.12), int(r * 0.09)
    draw.ellipse([cx - nose_rx, cy + int(r * 0.10) - nose_ry,
                   cx + nose_rx, cy + int(r * 0.10) + nose_ry],
                 fill=_hex_to_rgb(p["nose"]))
    # Fangs
    for side in (-1, 1):
        fang_pts = [
            (cx + side * int(r * 0.16), cy + int(r * 0.28)),
            (cx + side * int(r * 0.11), cy + int(r * 0.28)),
            (cx + side * int(r * 0.13), cy + int(r * 0.40)),
        ]
        draw.polygon(fang_pts, fill=(248, 246, 250, 255))
    # Forehead marking
    mark_pts = [
        (cx - int(r * 0.18), cy - int(r * 0.32)),
        (cx, cy - int(r * 0.18)),
        (cx + int(r * 0.18), cy - int(r * 0.32)),
    ]
    draw.polygon(mark_pts, fill=(80, 70, 90, 31))
    draw_clay_eyes(draw, cx, cy, r)
    draw_clay_smile(draw, cx, cy, r)
    draw_blush(draw, cx, cy, r, '#b4aabf')


def draw_raccoon(draw, cx, cy, r, W, H):
    p = PAL["raccoon"]
    draw_clay_bg(draw, W, H, p["bg"])
    # Round ears
    for side in (-1, 1):
        ear_cx = cx + side * int(r * 0.52)
        ear_cy = cy - int(r * 0.46)
        ear_r = int(r * 0.22)
        draw.ellipse([ear_cx - ear_r, ear_cy - ear_r, ear_cx + ear_r, ear_cy + ear_r],
                     fill=_hex_to_rgb(p["ear"]))
        inner_r = int(r * 0.13)
        draw.ellipse([ear_cx - inner_r, ear_cy - inner_r, ear_cx + inner_r, ear_cy + inner_r],
                     fill=_hex_to_rgb(p["earInner"]))
    # Round head
    hw, hh = int(r * 0.68), int(r * 0.52)
    head_r = int(r * 0.36)
    draw.rounded_rectangle([cx - hw, cy - hh, cx + hw, cy + hh],
                           radius=head_r, fill=_hex_to_rgb(p["head"]))
    # Eye mask
    draw.ellipse([cx - int(r * 0.52), cy - int(r * 0.02) - int(r * 0.18),
                   cx + int(r * 0.52), cy - int(r * 0.02) + int(r * 0.18)],
                 fill=(40, 40, 40, 89))
    # Muzzle
    draw.ellipse([cx - int(r * 0.32), cy + int(r * 0.22) - int(r * 0.20),
                   cx + int(r * 0.32), cy + int(r * 0.22) + int(r * 0.20)],
                 fill=_hex_to_rgb(p["muzzle"]))
    # Nose
    nose_rx, nose_ry = int(r * 0.10), int(r * 0.07)
    draw.ellipse([cx - nose_rx, cy + int(r * 0.12) - nose_ry,
                   cx + nose_rx, cy + int(r * 0.12) + nose_ry],
                 fill=_hex_to_rgb(p["nose"]))
    # Raccoon eyes
    draw_raccoon_eyes(draw, cx, cy, r)
    draw_clay_smile(draw, cx, cy, r)
    draw_blush(draw, cx, cy, r, '#beb8c8')


ANIMAL_DRAW = {
    "puppy": draw_puppy, "fox": draw_fox, "cat": draw_cat,
    "bear": draw_bear, "bunny": draw_bunny, "owl": draw_owl,
    "deer": draw_deer, "wolf": draw_wolf, "raccoon": draw_raccoon,
}


# ── Flat 2D emoji-style thumbnails ──────────────────────────────────
# Simple, bold shapes for compact UI spots (node lists, detected-by, hamburger dot)

EMOJI_PALETTE = {
    "puppy":   {"bg":"#e8e4f0","face":"#d4d0de","ear":"#c4c0cc","nose":"#d4a0b8","eye":"#3d2e52"},
    "fox":     {"bg":"#f5e4d0","face":"#e8905a","ear":"#e07840","nose":"#c05030","eye":"#3d2e52"},
    "cat":     {"bg":"#ece8f4","face":"#c8c0d4","ear":"#b8b0c4","nose":"#d898b0","eye":"#3d2e52"},
    "bear":    {"bg":"#ede0d8","face":"#b09080","ear":"#a08070","nose":"#7a5a4a","eye":"#3d2e52"},
    "bunny":   {"bg":"#eeeaf6","face":"#d4cce0","ear":"#ccc4d8","nose":"#e8a8c0","eye":"#3d2e52"},
    "owl":     {"bg":"#e8e0d0","face":"#8a7a60","ear":"#7a6a50","nose":"#c8a050","eye":"#3d2e52"},
    "deer":    {"bg":"#f0e8d8","face":"#c8a888","ear":"#b89870","nose":"#5a4838","eye":"#3d2e52"},
    "wolf":    {"bg":"#e0dde4","face":"#706878","ear":"#605868","nose":"#383040","eye":"#3d2e52"},
    "raccoon": {"bg":"#e0e0e4","face":"#808080","ear":"#686868","nose":"#404040","eye":"#2a2a2a"},
}


def _draw_flat_emoji(draw, animal, cx, cy, r):
    """Draw a flat 2D emoji-style face — simple bold shapes, no gradients."""
    p = EMOJI_PALETTE[animal]
    face = _hex_to_rgb(p["face"])
    ear = _hex_to_rgb(p["ear"])
    nose = _hex_to_rgb(p["nose"])
    eye = _hex_to_rgb(p["eye"])
    bg = _hex_to_rgb(p["bg"])

    # Background circle
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=bg)

    if animal == "puppy":
        # Floppy ears — hang down from sides
        for s in (-1, 1):
            ex = cx + s * int(r * 0.52)
            ey = cy + int(r * 0.02)
            draw.ellipse([ex - int(r*0.16), ey - int(r*0.30),
                           ex + int(r*0.16), ey + int(r*0.38)], fill=ear)
        # Head — taller, narrower
        hw, hh = int(r*0.48), int(r*0.44)
        draw.rounded_rectangle([cx-hw, cy-hh, cx+hw, cy+hh], radius=int(r*0.28), fill=face)
        # Muzzle — small, low
        draw.ellipse([cx-int(r*0.20), cy+int(r*0.06), cx+int(r*0.20), cy+int(r*0.28)],
                     fill=(200, 196, 210, 255))
        # Nose — tiny triangle-ish
        draw.ellipse([cx-int(r*0.06), cy+int(r*0.02), cx+int(r*0.06), cy+int(r*0.12)], fill=nose)

    elif animal == "fox":
        # Pointed ears — tall triangles
        for s in (-1, 1):
            pts = [(cx+s*int(r*0.12), cy-int(r*0.20)),
                    (cx+s*int(r*0.30), cy-int(r*0.74)),
                    (cx+s*int(r*0.48), cy-int(r*0.18))]
            draw.polygon(pts, fill=ear)
        # Head — narrower
        hw, hh = int(r*0.46), int(r*0.40)
        draw.rounded_rectangle([cx-hw, cy-hh, cx+hw, cy+hh], radius=int(r*0.24), fill=face)
        # White muzzle — smaller
        draw.ellipse([cx-int(r*0.22), cy+int(r*0.00), cx+int(r*0.22), cy+int(r*0.26)],
                     fill=(248, 224, 192, 255))
        # Nose
        draw.ellipse([cx-int(r*0.06), cy+int(r*0.00), cx+int(r*0.06), cy+int(r*0.10)], fill=nose)

    elif animal == "cat":
        # Pointed ears — upright triangles
        for s in (-1, 1):
            pts = [(cx+s*int(r*0.14), cy-int(r*0.20)),
                    (cx+s*int(r*0.30), cy-int(r*0.76)),
                    (cx+s*int(r*0.44), cy-int(r*0.18))]
            draw.polygon(pts, fill=ear)
        # Head — oval, narrower
        hw, hh = int(r*0.44), int(r*0.42)
        draw.rounded_rectangle([cx-hw, cy-hh, cx+hw, cy+hh], radius=int(r*0.24), fill=face)
        # Muzzle — small
        draw.ellipse([cx-int(r*0.16), cy+int(r*0.04), cx+int(r*0.16), cy+int(r*0.22)],
                     fill=(240, 220, 232, 255))
        # Nose — tiny
        draw.ellipse([cx-int(r*0.04), cy+int(r*0.02), cx+int(r*0.04), cy+int(r*0.08)], fill=nose)
        # Whiskers — thin, spread
        for s in (-1, 1):
            draw.line([(s*int(r*0.05)+cx, int(r*0.12)+cy),
                        (s*int(r*0.38)+cx, int(r*0.06)+cy)], fill=eye, width=max(1,int(r*0.015)))
            draw.line([(s*int(r*0.05)+cx, int(r*0.16)+cy),
                        (s*int(r*0.38)+cx, int(r*0.16)+cy)], fill=eye, width=max(1,int(r*0.015)))

    elif animal == "bear":
        # Round ears — on top of head
        for s in (-1, 1):
            ear_cx = cx + s * int(r * 0.42)
            ear_cy = cy - int(r * 0.40)
            draw.ellipse([ear_cx-int(r*0.16), ear_cy-int(r*0.16),
                           ear_cx+int(r*0.16), ear_cy+int(r*0.16)], fill=ear)
        # Head — wide but not too squashed
        hw, hh = int(r*0.50), int(r*0.44)
        draw.rounded_rectangle([cx-hw, cy-hh, cx+hw, cy+hh], radius=int(r*0.30), fill=face)
        # Muzzle — smaller
        draw.ellipse([cx-int(r*0.20), cy+int(r*0.04), cx+int(r*0.20), cy+int(r*0.26)],
                     fill=(205, 184, 168, 255))
        # Nose
        draw.ellipse([cx-int(r*0.07), cy+int(r*0.02), cx+int(r*0.07), cy+int(r*0.10)], fill=nose)

    elif animal == "bunny":
        # Tall ears — vertical ovals
        for s in (-1, 1):
            ear_cx = cx + s * int(r * 0.20)
            draw.ellipse([ear_cx-int(r*0.08), cy-int(r*0.76),
                           ear_cx+int(r*0.08), cy-int(r*0.12)], fill=ear)
        # Head — round
        hw, hh = int(r*0.44), int(r*0.44)
        draw.rounded_rectangle([cx-hw, cy-hh, cx+hw, cy+hh], radius=int(r*0.28), fill=face)
        # Muzzle — small
        draw.ellipse([cx-int(r*0.16), cy+int(r*0.04), cx+int(r*0.16), cy+int(r*0.24)],
                     fill=(200, 192, 212, 255))
        # Nose
        draw.ellipse([cx-int(r*0.04), cy+int(r*0.02), cx+int(r*0.04), cy+int(r*0.08)], fill=nose)

    elif animal == "owl":
        # Ear tufts — small spikes
        for s in (-1, 1):
            pts = [(cx+s*int(r*0.12), cy-int(r*0.22)),
                    (cx+s*int(r*0.22), cy-int(r*0.66)),
                    (cx+s*int(r*0.36), cy-int(r*0.20))]
            draw.polygon(pts, fill=ear)
        # Round head
        draw.ellipse([cx-int(r*0.48), cy-int(r*0.44), cx+int(r*0.48), cy+int(r*0.44)], fill=face)
        # Facial disc — lighter ring
        draw.ellipse([cx-int(r*0.34), cy-int(r*0.30), cx+int(r*0.34), cy+int(r*0.30)],
                     fill=(200, 184, 152, 255))
        # Beak — small triangle
        pts = [(cx-int(r*0.04), cy+int(r*0.02)),
                (cx, cy+int(r*0.14)),
                (cx+int(r*0.04), cy+int(r*0.02))]
        draw.polygon(pts, fill=nose)

    elif animal == "deer":
        # Elegant ears — narrow triangles
        for s in (-1, 1):
            pts = [(cx+s*int(r*0.10), cy-int(r*0.18)),
                    (cx+s*int(r*0.24), cy-int(r*0.70)),
                    (cx+s*int(r*0.38), cy-int(r*0.16))]
            draw.polygon(pts, fill=ear)
        # Oval head — slender
        hw, hh = int(r*0.38), int(r*0.44)
        draw.rounded_rectangle([cx-hw, cy-hh, cx+hw, cy+hh], radius=int(r*0.26), fill=face)
        # Muzzle — elongated
        draw.ellipse([cx-int(r*0.16), cy+int(r*0.00), cx+int(r*0.16), cy+int(r*0.26)],
                     fill=(232, 216, 192, 255))
        # Nose
        draw.ellipse([cx-int(r*0.04), cy+int(r*0.00), cx+int(r*0.04), cy+int(r*0.08)], fill=nose)
        # Antler nubs
        for s in (-1, 1):
            draw.ellipse([cx+s*int(r*0.20)-int(r*0.04), cy-int(r*0.52)-int(r*0.04),
                           cx+s*int(r*0.20)+int(r*0.04), cy-int(r*0.52)+int(r*0.04)],
                         fill=(160, 136, 96, 255))

    elif animal == "wolf":
        # Pointed ears — tall
        for s in (-1, 1):
            pts = [(cx+s*int(r*0.10), cy-int(r*0.20)),
                    (cx+s*int(r*0.28), cy-int(r*0.74)),
                    (cx+s*int(r*0.44), cy-int(r*0.18))]
            draw.polygon(pts, fill=ear)
        # Elongated head — narrow
        hw, hh = int(r*0.44), int(r*0.40)
        draw.rounded_rectangle([cx-hw, cy-hh, cx+hw, cy+hh], radius=int(r*0.22), fill=face)
        # Muzzle — elongated
        draw.ellipse([cx-int(r*0.18), cy+int(r*0.00), cx+int(r*0.18), cy+int(r*0.24)],
                     fill=(168, 152, 168, 255))
        # Nose
        draw.ellipse([cx-int(r*0.06), cy+int(r*0.00), cx+int(r*0.06), cy+int(r*0.10)], fill=nose)
        # Fangs — tiny white triangles
        for s in (-1, 1):
            pts = [(cx+s*int(r*0.08), cy+int(r*0.20)),
                    (cx+s*int(r*0.05), cy+int(r*0.20)),
                    (cx+s*int(r*0.06), cy+int(r*0.28))]
            draw.polygon(pts, fill=(248, 246, 250, 255))

    elif animal == "raccoon":
        # Round ears
        for s in (-1, 1):
            ear_cx = cx + s * int(r * 0.40)
            ear_cy = cy - int(r * 0.36)
            draw.ellipse([ear_cx-int(r*0.14), ear_cy-int(r*0.14),
                           ear_cx+int(r*0.14), ear_cy+int(r*0.14)], fill=ear)
        # Round head
        hw, hh = int(r*0.46), int(r*0.40)
        draw.rounded_rectangle([cx-hw, cy-hh, cx+hw, cy+hh], radius=int(r*0.26), fill=face)
        # Eye mask — horizontal band
        draw.ellipse([cx-int(r*0.36), cy-int(r*0.10), cx+int(r*0.36), cy+int(r*0.06)],
                     fill=(60, 60, 60, 90))
        # Muzzle — small
        draw.ellipse([cx-int(r*0.16), cy+int(r*0.04), cx+int(r*0.16), cy+int(r*0.22)],
                     fill=(192, 192, 192, 255))
        # Nose
        draw.ellipse([cx-int(r*0.04), cy+int(r*0.02), cx+int(r*0.04), cy+int(r*0.08)], fill=nose)

    # ── Shared eyes for all animals ──
    eye_r = int(r * 0.08)
    for s in (-1, 1):
        ex = cx + s * int(r * 0.18)
        ey = cy - int(r * 0.10)
        # White
        draw.ellipse([ex-eye_r, ey-eye_r, ex+eye_r, ey+eye_r], fill=(255, 255, 255, 255))
        # Pupil
        pr = int(eye_r * 0.52)
        draw.ellipse([ex-pr, ey-pr, ex+pr, ey+pr], fill=eye)
        # Highlight
        hr = int(eye_r * 0.22)
        draw.ellipse([ex-hr+int(r*0.01), ey-hr-int(r*0.01),
                       ex+hr+int(r*0.01), ey+hr-int(r*0.01)],
                     fill=(255, 255, 255, 230))

    # ── Smile — simple arc ──
    sw = max(1, int(r * 0.03))
    smile_col = (80, 60, 80, 100)
    smile_r = int(r * 0.08)
    smile_cy = cy + int(r * 0.18)
    draw.arc([cx-smile_r, smile_cy-smile_r, cx+smile_r, smile_cy+smile_r],
             start=10, end=170, fill=smile_col, width=sw)


def generate_emoji_thumbnail(animal, size=64):
    """Generate a flat 2D emoji-style thumbnail for compact UI spots."""
    if animal not in EMOJI_PALETTE:
        raise ValueError(f"Unknown animal: {animal}")
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r = size * 0.45
    cx, cy = size // 2, size // 2
    _draw_flat_emoji(draw, animal, cx, cy, r)
    return img


def generate_avatar(animal, size=256):
    """Generate a clay-style avatar PNG for the given animal."""
    if animal not in ANIMAL_DRAW:
        raise ValueError(f"Unknown animal: {animal}")
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r = size * 0.47
    cx, cy = size // 2, size // 2
    ANIMAL_DRAW[animal](draw, cx, cy, r, size, size)
    return img


def generate_all(output_dir, size=256):
    """Generate all 9 avatar PNGs + emoji thumbnails into output_dir."""
    os.makedirs(output_dir, exist_ok=True)
    for animal in ANIMAL_DRAW:
        img = generate_avatar(animal, size)
        path = os.path.join(output_dir, f"{animal}-avatar.png")
        img.save(path, "PNG")
        print(f"  ✓ {animal} ({size}x{size}) → {path}")
    # Flat 2D emoji thumbnails (compact UI)
    emoji_dir = os.path.join(output_dir, "emoji")
    os.makedirs(emoji_dir, exist_ok=True)
    for animal in EMOJI_PALETTE:
        img = generate_emoji_thumbnail(animal, 64)
        path = os.path.join(emoji_dir, f"{animal}-emoji.png")
        img.save(path, "PNG")
    print(f"  ✓ 64x64 emoji thumbnails → {emoji_dir}/")
    # Small clay versions
    small_dir = os.path.join(output_dir, "small")
    os.makedirs(small_dir, exist_ok=True)
    for animal in ANIMAL_DRAW:
        img = generate_avatar(animal, 64)
        path = os.path.join(small_dir, f"{animal}-avatar.png")
        img.save(path, "PNG")
    print(f"  ✓ 64x64 small clay → {small_dir}/")
    print(f"  ✓ 64x64 small versions → {small_dir}/")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "static", "lilly", "avatars")
    sz = int(sys.argv[2]) if len(sys.argv) > 2 else 256
    print("Generating clay-style animal avatars...")
    generate_all(out, sz)
    print("\n✅ All avatars generated!")
