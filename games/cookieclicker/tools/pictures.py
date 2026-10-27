#!/usr/bin/env python3
"""How data.bin keeps its pictures (tools/pack.py lays them out with this), and the pictures of src/data.bin laid
out again in place, every pixel drawn the same:

    python3 -I tools/pictures.py

A picture is its colours (RGB565) and opacities, pixel by pixel; a clear pixel's colour does not matter. Kept:
- only the part of it that isn't clear (outside it, clear);
- a palette of its colours and opacities, each once (a palette seen before is not stored again);
- its pixels as indices to it: none (one colour), half a byte a pixel (16 colours or fewer, the first pixel
  lowest) or a byte, each row a whole number of bytes. The pixels of each group of pictures (by name, ROW_*,
  MILK_*..., the icons by rows of the sheet) are laid out with tools/share.py: a string seen before, or inside
  another, is not stored again; tails overlap heads.

Its record (IMAGES, 16 bytes; res.h's ImageRec): w, h, then the part kept: x, y, w, h (u8 each), the bits a pixel
(0, 4, 8), colours - 1 (u8 each), the pixels' offset, the palette's (u32): its colours (RGB565), then their
opacities (u8 each)."""
import os
import re
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "tools"))
from share import share  # noqa: E402

MAGIC = b"CCK2"


def plane(colours, opacities):
    """a picture as one array: RGB565 << 8 | opacity, the clear pixels 0"""
    c = np.asarray(colours, np.uint32)
    a = np.asarray(opacities, np.uint32)
    return np.where(a > 0, c << 8 | a, 0).astype(np.uint32)


def lay_out(pictures, groups, put):
    """pictures: (h x w) planes (plane()); groups: a name for each, the pictures that share their pixels
    (tools/share.py, slow on many); put(bytes, align) adds bytes to data.bin, giving their offset.
    Returns the records (IMAGES), in order."""
    kept, pals, recs = [], {}, []
    for p in pictures:
        h, w = p.shape
        if not (0 < w < 256 and 0 < h < 256):
            raise SystemExit(f"pictures: {w} x {h}: past 255 pixels")
        ys, xs = np.nonzero(p)
        if not len(xs):   # (all clear: nothing to read)
            kept.append(None)
            continue
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
        part = p[y0:y1, x0:x1]
        flat = part.ravel()
        _, first, inv = np.unique(flat, return_index=True, return_inverse=True)
        order = np.argsort(first)                # (the colours in the order they come)
        rank = np.empty(len(order), np.int64)
        rank[order] = np.arange(len(order))
        idx = rank[inv].reshape(part.shape).astype(np.uint8)
        colours = flat[first[order]]
        n = len(colours)
        if n > 256:
            raise SystemExit(f"pictures: a picture of {n} colours")
        bits = 0 if n == 1 else 4 if n <= 16 else 8
        if bits == 4:
            if idx.shape[1] % 2:
                idx = np.concatenate([idx, np.zeros((idx.shape[0], 1), np.uint8)], 1)
            px = (idx[:, 0::2] | idx[:, 1::2] << 4).astype(np.uint8).tobytes()
        else:
            px = idx.tobytes() if bits else b""
        pal = b"".join(struct.pack("<H", int(c) >> 8) for c in colours) + bytes(int(c) & 255 for c in colours)
        if pal not in pals:
            pals[pal] = put(pal, 2)
        kept.append((w, h, x0, y0, x1 - x0, y1 - y0, bits, n, px, pals[pal]))
    at = {}   # picture -> its pixels' offset
    for g in dict.fromkeys(groups):
        mine = [i for i, k in enumerate(kept) if k and k[8] and groups[i] == g]
        if mine:
            blob, offs = share([kept[i][8] for i in mine])
            base = put(blob, 1)
            at.update((i, base + o) for i, o in zip(mine, offs))
    for i, (p, k) in enumerate(zip(pictures, kept)):
        if k is None:
            recs.append((p.shape[1], p.shape[0], 0, 0, 0, 0, 0, 1, 0, 0))
        else:
            recs.append(k[:8] + (at.get(i, 0), k[9]))
    return recs


def group(name):
    """the group a picture's pixels are shared in: by its name (ROW_FARM: ROW), or icons by their row"""
    return re.sub(r"(_.*|\d+)$", "", name)


def groups_of(data, h):
    g = ["?"] * h["N_IMAGES"]
    cols = h["ICON_COLS"]
    for k, i in enumerate(struct.unpack_from(f"<{cols * h['ICON_ROWS']}H", data, h["OFF_ICONS"])):
        if i != 0xFFFF:
            g[i] = f"icons{k // cols}"
    for k, v in h.items():
        if k.startswith("IM_"):
            g[v] = group(k[3:])
    return g


def records(recs):
    return b"".join(struct.pack("<BBBBBBBBII", w, h, x, y, cw, ch, bits, n - 1, px, pal)
                    for w, h, x, y, cw, ch, bits, n, px, pal in recs)


def pictures_of(data, h):
    """the pictures of a data.bin (and its data.h's numbers), as planes"""
    out = []
    old = data[:4] == b"CCK1"   # (before: no part, pictures of a byte, half a byte or RGB565 a pixel)
    for i in range(h["N_IMAGES"]):
        at = h["OFF_IMAGES"] + 16 * i
        if old:
            w, hh, fmt, n1, _, px, pal = struct.unpack_from("<HHBBHII", data, at)
            if fmt == 0:
                c = np.frombuffer(data, "<u2", w * hh, px).reshape(hh, w)
                out.append(plane(c, np.full((hh, w), 255)))
                continue
            x, y, cw, ch, bits = 0, 0, w, hh, 8 if fmt == 1 else 4
        else:
            w, hh, x, y, cw, ch, bits, n1, px, pal = struct.unpack_from("<BBBBBBBBII", data, at)
        p = np.zeros((hh, w), np.uint32)
        if cw:
            n = n1 + 1
            cols = np.frombuffer(data, "<u2", n, pal).astype(np.uint32)
            opa = np.frombuffer(data, np.uint8, n, pal + 2 * n).astype(np.uint32)
            if bits == 8:
                idx = np.frombuffer(data, np.uint8, cw * ch, px).reshape(ch, cw)
            elif bits == 4:
                b = np.frombuffer(data, np.uint8, (cw + 1) // 2 * ch, px).reshape(ch, (cw + 1) // 2)
                idx = np.stack([b & 15, b >> 4], 2).reshape(ch, -1)[:, :cw]
            else:
                idx = np.zeros((ch, cw), np.uint8)
            p[y:y + ch, x:x + cw] = plane(cols[idx], opa[idx])
        out.append(p)
    return out


def check(pictures, data, h):
    for i, (a, b) in enumerate(zip(pictures, pictures_of(data, h))):
        if a.shape != b.shape or (a != b).any():
            raise SystemExit(f"pictures: picture {i} not read back as it was")


def numbers(path):
    h = {}
    for line in open(path):
        m = re.match(r"#define (\w+) (-?\d+)$", line.strip())
        if m:
            h[m.group(1)] = int(m.group(2))
    return h


def main():
    """src/data.bin's pictures laid out again: the pictures first, then the rest (from the icons' table to the
    texts) moved as it is, then the pictures' table; data.h's offsets moved with them"""
    bin_path, h_path = os.path.join(HERE, "..", "src", "data.bin"), os.path.join(HERE, "..", "src", "data.h")
    data, h = open(bin_path, "rb").read(), numbers(h_path)
    pics = pictures_of(data, h)
    out = bytearray(MAGIC)

    def put(b, align):
        while len(out) % align:
            out.append(0)
        out.extend(b)
        return len(out) - len(b)
    recs = lay_out(pics, groups_of(data, h), put)
    start, end = h["OFF_ICONS"], h["OFF_IMAGES"]   # (the rest: what isn't pictures)
    while (len(out) - start) % 8:   # (its tables of doubles stay 8-aligned)
        out.append(0)
    moved = len(out) - start
    rest = bytearray(data[start:end])
    fonts = h["OFF_FONTS"] - start   # (the fonts' offsets, from data.bin's start)
    for f in range(len([k for k in h if k.startswith("F_")])):
        o = struct.unpack_from("<I", rest, fonts + 4 * f)[0]
        struct.pack_into("<I", rest, fonts + 4 * f, o + moved)
    out += rest
    while len(out) % 4:
        out.append(0)
    images = len(out)
    out += records(recs)
    check(pics, bytes(out), dict(h, OFF_IMAGES=images))
    lines = []
    for line in open(h_path).read().split("\n"):
        m = re.match(r"#define (OFF_\w+|DATA_SIZE) (\d+)$", line)
        if m:
            v = int(m.group(2))
            v = images if m.group(1) == "OFF_IMAGES" else len(out) if m.group(1) == "DATA_SIZE" else v + moved
            line = f"#define {m.group(1)} {v}"
        lines.append(line)
    open(bin_path, "wb").write(out)
    open(h_path, "w").write("\n".join(lines))
    print(f"data.bin: {len(data)} -> {len(out)} bytes, pictures {start} -> {start + moved}")


if __name__ == "__main__":
    main()
