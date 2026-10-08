#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 APK  launcher 图标：深色圆角底 + 足球 + 金色描边（与 PWA manifest 配色一致）"""
import math, os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "res")
BG = (11, 15, 20, 255)        # #0b0f14
WHITE = (255, 255, 255, 255)
GOLD = (255, 213, 77, 255)    # #ffd54d

SIZE = 512


def rounded_rect(size, radius, color):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=color)
    return img


def pentagon_points(cx, cy, r, rot=-math.pi / 2):
    return [(cx + r * math.cos(rot + i * 2 * math.pi / 5),
             cy + r * math.sin(rot + i * 2 * math.pi / 5)) for i in range(5)]


def make_icon(size=512):
    img = rounded_rect(size, int(size * 0.22), BG)
    d = ImageDraw.Draw(img)
    c = size / 2

    # 金色外描边
    d.ellipse([c - size * 0.34, c - size * 0.34, c + size * 0.34, c + size * 0.34],
              outline=GOLD, width=max(2, int(size * 0.018)))

    # 白色足球主体
    ball_r = size * 0.29
    d.ellipse([c - ball_r, c - ball_r, c + ball_r, c + ball_r], fill=WHITE)

    # 中心深色五边形
    pent = pentagon_points(c, c, size * 0.10)
    d.polygon(pent, fill=BG)

    # 五边形顶点向外的深色辐条（足球块）
    w = max(2, int(size * 0.028))
    for px, py in pent:
        ang = math.atan2(py - c, px - c)
        x1 = c + math.cos(ang) * size * 0.105
        y1 = c + math.sin(ang) * size * 0.105
        x2 = c + math.cos(ang) * size * 0.285
        y2 = c + math.sin(ang) * size * 0.285
        d.line([x1, y1, x2, y2], fill=BG, width=w)

    # 重新盖一次五边形，保证中心块完整
    d.polygon(pentagon_points(c, c, size * 0.098), fill=BG)
    return img


DENS = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
base = make_icon(SIZE)
for name, px in DENS.items():
    folder = os.path.join(OUT, f"mipmap-{name}")
    os.makedirs(folder, exist_ok=True)
    base.resize((px, px), Image.LANCZOS).save(os.path.join(folder, "ic_launcher.png"))
    print(f"  mipmap-{name}/ic_launcher.png ({px}px)")

os.makedirs(os.path.join(OUT, "drawable"), exist_ok=True)
base.resize((512, 512), Image.LANCZOS).save(os.path.join(OUT, "drawable", "ic_launcher_playstore.png"))
print("  drawable/ic_launcher_playstore.png (512px)")
print("图标生成完成")
