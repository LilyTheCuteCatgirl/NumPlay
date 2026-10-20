#!/usr/bin/env python3
"""Builds NumPlay (or one game) again each time a source file is saved, for the live emulator on macOS and Linux
(tools/emu_live.py --live; on Windows it uses tools/build_windows.py). Each good build is copied to
dist/live/NumPlay.nwa in one step, so the emulator never reads half a file; a failed build keeps the last good one.

  python3 tools/watch.py                 NumPlay (build/NumPlay.nwa)
  python3 tools/watch.py --game sokoban  one game on its own (games/sokoban's Makefile)
  python3 tools/watch.py --once          build once and stop

Needs what `make` needs (python3 tools/setup.py gets it). It prints the lines the live emulator follows:
"Detected edits (...)", "Building: ...", "Stable live-emulator input: ...", "Build failed; ...",
"Watching for the next edit".
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = os.path.join(ROOT, "dist", "live", "NumPlay.nwa")
WATCHED = (".c", ".h", ".S", ".s", ".rs", ".py", ".json", ".txt", ".png", ".inc", "Makefile")
SKIP = {"build", "output", "dist", "node_modules", "target", ".git", "__pycache__"}


def stamp(folders):
    """The newest change time among the sources, and how many there are."""
    newest, n = 0.0, 0
    for top in folders:
        for d, dirs, files in os.walk(top):
            dirs[:] = [x for x in dirs if x not in SKIP]
            for f in files:
                if f.endswith(WATCHED):
                    try:
                        newest = max(newest, os.stat(os.path.join(d, f)).st_mtime)
                        n += 1
                    except OSError:
                        pass
    return newest, n


def nwlink():
    for p in (shutil.which("nwlink"), os.path.join(os.path.expanduser("~"), ".numplay", "node_modules", ".bin", "nwlink"),
              os.path.join(ROOT, "games", "numdash", "node_modules", ".bin", "nwlink")):
        if p and os.path.exists(p):
            return p
    return "npx --yes -- nwlink@1.0.0"


def build(game, jobs):
    if game:
        target = ["make", "-C", os.path.join(ROOT, "games", game), "-j%d" % jobs, "NWLINK=" + nwlink()]
        out = None
    else:
        target = ["make", "-C", ROOT, "-j%d" % jobs, "build/NumPlay.nwa", "NWLINK=" + nwlink()]
        out = os.path.join(ROOT, "build", "NumPlay.nwa")
    print("Building:", " ".join(target), flush=True)
    r = subprocess.run(target, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in r.stdout.splitlines()[-25:]:
        print("  " + line, flush=True)
    if game:  # (where each game's Makefile puts its app)
        found = [os.path.join(ROOT, "games", game, n) for n in ("output/%s.nwa" % game, "build/%s.nwa" % game,
                                                              "output/device/%s.nwa" % game)]
        found = [p for p in found if os.path.exists(p)]
        out = max(found, key=os.path.getmtime) if found else None
    if r.returncode or not out or not os.path.exists(out):
        print("Build failed; the last good app stays in dist/live/NumPlay.nwa", flush=True)
        return False
    os.makedirs(os.path.dirname(LIVE), exist_ok=True)
    tmp = LIVE + ".part"
    shutil.copyfile(out, tmp)
    os.replace(tmp, LIVE)
    print("Stable live-emulator input:", os.path.relpath(LIVE, ROOT), flush=True)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", help="a game's folder in games/, built on its own")
    ap.add_argument("--once", action="store_true", help="build once, don't watch")
    ap.add_argument("-j", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="make's jobs")
    # (what tools/build_windows.py takes, so tools/emu_live.py can start either: these change nothing here)
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--launcher-only", action="store_true")
    ap.add_argument("--skip-languages", action="store_true")
    a, _ = ap.parse_known_args()
    folders = [os.path.join(ROOT, "games", a.game)] if a.game else [os.path.join(ROOT, d) for d in ("launcher", "games")]
    folders.append(os.path.join(ROOT, "games", "common"))
    build(a.game, a.j)
    if a.once:
        return
    last, count = stamp(folders)
    print("Watching NumPlay source files (%d). Save a file to build again; Ctrl+C stops." % count, flush=True)
    try:
        while True:
            time.sleep(1)
            now, n = stamp(folders)
            if now != last or n != count:
                time.sleep(0.3)  # (editors write in several steps)
                last, count = stamp(folders)
                print("Detected edits (%d files watched)" % count, flush=True)
                build(a.game, a.j)
                print("Watching for the next edit", flush=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
