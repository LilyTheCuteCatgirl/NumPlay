#!/usr/bin/env python3
"""Original procedural Suika icon. Run from any directory; requires Pillow."""
from pathlib import Path
from PIL import Image, ImageDraw

S = 4
im = Image.new('RGB', (55*S, 56*S), '#fcf4df')
d = ImageDraw.Draw(im)


def ellipse(box, fill, outline=None, width=1):
    d.ellipse(tuple(int(v*S) for v in box), fill=fill, outline=outline, width=width*S)


def line(points, fill, width=1):
    d.line([(int(x*S), int(y*S)) for x, y in points], fill=fill, width=width*S)


ellipse((6, 11, 50, 55), '#e6d4aa')
ellipse((5, 8, 49, 52), '#64a66c', '#3c784b')
for inset in (8, 15):
    d.arc((int((5+inset)*S), 8*S, int((49-inset)*S), 52*S), 90, 270, fill='#3c784b', width=S)
    d.arc((int((5+inset)*S), 8*S, int((49-inset)*S), 52*S), -90, 90, fill='#3c784b', width=S)
ellipse((13, 16, 23, 24), '#c3dc9d')
line([(27, 11), (28, 3)], '#796043', 2)
ellipse((28, 2, 40, 8), '#477842')
ellipse((18, 29, 21, 34), '#354c34')
ellipse((33, 29, 36, 34), '#354c34')
line([(25, 35), (27, 37), (29, 35)], '#354c34')
ellipse((12, 35, 18, 38), '#e88d83')
ellipse((37, 35, 43, 38), '#e88d83')
im.resize((55, 56), Image.Resampling.LANCZOS).save(Path(__file__).resolve().parents[1] / 'src/icon.png')
