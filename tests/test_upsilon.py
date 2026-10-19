#!/usr/bin/env python3
"""Checks NumPlay-Upsilon.nwa on an emulated N0110 running Upsilon (tools/emu.py --system upsilon):

1. it makes no system call Upsilon doesn't have (Home, checksums, flash);
2. it finds the file system there: a save already on the calculator goes into its copy,
   numplay_saves.py;
3. Chess, which draws text with the firmware's font, opens and shows its menu (drawn by NumPlay
   there, launcher/src/compat.c);
4. Home quits.

Usage: test_upsilon.py build/NumPlay-Upsilon.nwa [--out DIR]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emu  # noqa: E402

emu.configure("n0110", "upsilon")
from test_progress import copy_lines, presses, records, write_storage  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("nwa")
    ap.add_argument("--out", default="build/test_upsilon")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    st = os.path.join(a.out, "storage.bin")
    save = bytes(range(30))
    write_storage(st, [("pi.py", b"\x01print(3.14159)\n\x00"), ("snake.sav", save)])
    # the home screen: Crossy Road, Block Blast, Portal Returns, Tetris, Chrome Dino, Chess...: Right five times, OK
    c = emu.Calculator(a.nwa, storage_file=st)
    c.enable_checks(reads=True)
    c.keys = presses((1200, "right"), (1600, "right"), (2000, "right"), (2400, "right"), (2800, "right"), (3200, "ok")) + \
        [(7400, 7800, emu.KEYS["home"])]
    menu = os.path.join(a.out, "chess.png")
    c.pending_shots = [(6900, menu)]
    c.run(10400)
    problems = sorted(set(c.violations))
    if not c.exited:
        problems.append("did not quit on Home")
    recs = dict(records(open(st, "rb").read()))
    copy = recs.get("numplay_saves.py")
    if copy is None:
        problems.append("numplay_saves.py not written: the file system was not found")
    elif copy_lines(copy[1:-1].decode("ascii", "replace")).get("snake.sav") != save:
        problems.append("numplay_saves.py doesn't hold snake.sav")
    from PIL import Image
    px = Image.open(menu).convert("RGB")
    if len(px.getcolors(maxcolors=1 << 16) or ()) < 8:
        problems.append("Chess's menu looks empty")
    for p in problems:
        print("   ", p)
    print("PASS" if not problems else "FAIL")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
