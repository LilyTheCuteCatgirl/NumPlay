#!/usr/bin/env python3
"""Live NumWorks emulator for macOS, Linux and Windows: a calculator you can click, around tools/emu.py.

Put this file beside tools/emu.py. Requires Pillow, numpy, unicorn,
pyelftools and nwlink (tkinterdnd2 for dropping files, if you like). It shows the
NumWorks keyboard artwork of the k-kuroguro VS Code extension (CC BY-NC-SA) when
that extension is installed, read in place and never copied; otherwise it draws a
calculator of its own.

Runs an ARM .nwa through the project's existing emu.Calculator, shows the
320x240 display, supports 46 clickable keys, keyboard, .nwa drops and reload.

To run:
    python tools/emu_live.py path/to/NumPlay.nwa

The calculator skin does not depend on emu_live_skin.py; emu.py is still needed.

What it does:
- Single file / Live development: it builds again when you save, and reloads
  (tools/watch.py on macOS and Linux, tools/build_windows.py on Windows);
- calculator skin / pixel-exact 320x240 screen, keyboard highlights;
- tear-reduced frame presentation, usable Epsilon firmware text;
- N0120 display-transfer timings calibrated using physical NumWorks results;
- fixed 550 MHz N0120 / 216 MHz older-model baseline (approximate timing);
- optional host-adaptive clock and opt-in speed diagnostics;
- manual confirmed USB upload with a live progress bar and USB activity log;
- installer percentages are shown only when nwlink actually reports them.

This is not a cycle-accurate Cortex-M7 emulator: demanding NumDrive scenes can
still be host-CPU-limited. This release excludes the experimental SRAM hooks and
LCD scanout simulation. Saves and crash logs remain outside build/ and dist/.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import signal
import tempfile
from collections import deque
import shutil
import struct
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageTk, ImageDraw, ImageFont, ImageEnhance

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None

# emu.py is a sibling file when installed in the repository's tools folder.
try:
    import emu
except ImportError as exc:
    raise SystemExit("Put emu_live.py inside NumPlay/tools/, beside emu.py. "
                     f"Import failed: {exc}") from exc

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME_NWA = Path.home() / "Downloads" / "nwa"
SAVE_DIR = Path.home() / ".numplay-emulator" / "saves"
LIVE_NWA = REPO_ROOT / "dist" / "live" / "NumPlay.nwa"
BUILD_SCRIPT = REPO_ROOT / "tools" / ("build_windows.py" if os.name == "nt" else "watch.py")
BUILTIN_SKIN = Path.home() / ".numplay-emulator" / "calculator.png"
LCD_WIDTH = 320
LCD_HEIGHT = 240
FRAME_VIRTUAL_MS = 32
STEP_VIRTUAL_MS = 2  # Bound application execution between worker checks.
UI_POLL_MS = 20  # Keep the interface responsive to finished frames.
LCD_UPDATE_INTERVAL_S = 1.0 / 40.0  # At most 40 displayed frames per second.
LCD_QUIET_INTERVAL_S = 0.045  # Fallback: don't capture in the middle of a drawing burst.
LCD_STALE_FRAME_S = 0.35  # Fallback for games with no obvious end-of-frame signal.
WATCH_MS = 1000
# First-pass N0120 measurements from the same Speed Probe on real hardware.
# Solid fills: 24 * 320 * 152 pixels took 85 ms.
# Pixel pushes: 120 * 320 * 16 pixels took 45 ms.
# These two independent tests agree to within 0.6%: ~13.7 Mpix/s.
# Charge a single shared LCD transfer time without sleeping the host thread.
LCD_MS_PER_PIXEL = (85.0 / (24 * 320 * 152) + 45.0 / (120 * 320 * 16)) / 2
# 120 18-character 7x14-pixel Epsilon text draws took 44 ms.
# Partition approximate per-call and per-character cost; this is not a
# replacement for a firmware-specific glyph rasterizer cycle model.
FONT_CALL_MS = 0.030
FONT_CHAR_MS = (44.0 / 120.0 - FONT_CALL_MS) / 18
# NUMPLAY_DARK_MODE_V1
# Keep both palettes here so the theme can be changed while a game runs.
# The calculator LCD image and calculator artwork are deliberately untouched.
THEME_SETTINGS_FILE = Path.home() / ".numplay-emulator" / "settings.json"
THEME_PALETTES = {
    "light": {
        "BG": "#f5f5f5", "PANEL": "#ffffff", "PANEL_2": "#ececec",
        "HOVER": "#d9d9d9", "LINE": "#d9d9d9", "FG": "#414147",
        "MUTED": "#8c8c8c", "ACCENT": "#ffb734", "SELECT": "#d4d7e0",
        "ERROR_BG": "#fde3e3",
    },
    "dark": {
        "BG": "#131821", "PANEL": "#1c232f", "PANEL_2": "#2b3544",
        "HOVER": "#394759", "LINE": "#394759", "FG": "#edf1f9",
        "MUTED": "#aab8cd", "ACCENT": "#ffb734", "SELECT": "#3c4b60",
        "ERROR_BG": "#4c303a",
    },
}


def _load_theme_preference():
    try:
        data = json.loads(THEME_SETTINGS_FILE.read_text(encoding="utf-8"))
        return data.get("theme") if isinstance(data, dict) and data.get("theme") in THEME_PALETTES else "light"
    except (OSError, ValueError, TypeError):
        return "light"


def _save_theme_preference(name):
    """Preserve other settings and atomically save the chosen theme."""
    try:
        data = json.loads(THEME_SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError, TypeError):
        data = {}
    data["theme"] = name
    THEME_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = THEME_SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, THEME_SETTINGS_FILE)


def _activate_theme_palette(name):
    global CURRENT_THEME
    CURRENT_THEME = name if name in THEME_PALETTES else "light"
    globals().update(THEME_PALETTES[CURRENT_THEME])


_activate_theme_palette(_load_theme_preference())
UI_FAMILY = ("Helvetica Neue" if sys.platform == "darwin" else "Segoe UI" if os.name == "nt" else "DejaVu Sans")
MONO_FAMILY = ("Menlo" if sys.platform == "darwin" else "Consolas" if os.name == "nt" else "DejaVu Sans Mono")
FONT_GROW = 3 if sys.platform == "darwin" else 0   # (Tk's points are smaller on a Mac)


def font(size, weight="normal"):
    return (UI_FAMILY, size + FONT_GROW, weight)


def mono(size):
    return (MONO_FAMILY, size + FONT_GROW)

# All 46 named keys in emu.KEYS. Blank cells below are intentional spacers.
KEY_ROWS = [
    ["home", "back", "onoff", "shift", "alpha", "backspace"],
    ["xnt", "var", "toolbox", "up", "ok", "exe"],
    ["exp", "ln", "log", "left", "down", "right"],
    ["i", "comma", "power", "sin", "cos", "tan"],
    ["pi", "sqrt", "square", "lparen", "rparen", None],
    ["7", "8", "9", "div", "mul", "minus"],
    ["4", "5", "6", "plus", "ee", "ans"],
    ["1", "2", "3", "0", "dot", None],
]
LABEL = {
    "home": "Home", "back": "Back", "onoff": "Power", "shift": "Shift",
    "alpha": "Alpha", "backspace": "⌫", "xnt": "x,n,t", "var": "var",
    "toolbox": "Toolbox", "up": "↑", "down": "↓", "left": "←", "right": "→",
    "ok": "OK", "exe": "EXE", "exp": "eˣ", "ln": "ln", "log": "log",
    "i": "i", "comma": ",", "power": "^", "sin": "sin", "cos": "cos",
    "tan": "tan", "pi": "π", "sqrt": "√", "square": "x²",
    "lparen": "(", "rparen": ")", "div": "÷", "mul": "×",
    "minus": "−", "plus": "+", "ee": "×10ⁿ", "ans": "ans", "dot": ".",
}

# Every calculator function can also be clicked on the on-screen keyboard.
# PC bindings emphasize unambiguous shortcuts: F1-F12 + familiar keys.
PC_KEYSYM = {
    "left": "left", "right": "right", "up": "up", "down": "down",
    "return": "ok", "kp_enter": "exe", "escape": "back", "home": "home",
    "delete": "backspace", "backspace": "backspace", "shift_l": "shift", "shift_r": "shift",
    "alt_l": "alpha", "alt_r": "alpha", "space": "ok",
    "f1": "home", "f2": "back", "f3": "shift", "f4": "alpha",
    "f5": "toolbox", "f6": "var", "f7": "xnt", "f8": "onoff",
    "f9": "ans", "f10": "exe", "f11": "sqrt", "f12": "square",
    "plus": "plus", "kp_add": "plus", "minus": "minus", "kp_subtract": "minus",
    "asterisk": "mul", "kp_multiply": "mul", "slash": "div", "kp_divide": "div",
    "period": "dot", "kp_decimal": "dot", "comma": "comma", "parenleft": "lparen",
    "parenright": "rparen", "asciicircum": "power",
}
PC_KEYSYM.update({str(n): str(n) for n in range(10)})
PC_KEYSYM.update({f"kp_{n}": str(n) for n in range(10)})
PC_KEYSYM.update({
    "x": "xnt", "v": "var", "b": "toolbox", "e": "exp", "l": "ln",
    "g": "log", "i": "i", "s": "sin", "c": "cos", "t": "tan",
    "p": "pi", "q": "sqrt", "w": "square", "r": "power", "n": "ee",
    "a": "ans",
})


def validate_layout():
    placed = [k for row in KEY_ROWS for k in row if k is not None]
    if set(placed) != set(emu.KEYS) or len(placed) != len(set(placed)):
        missing = set(emu.KEYS) - set(placed)
        extras = set(placed) - set(emu.KEYS)
        raise RuntimeError(f"Keypad does not match emu.KEYS: missing={missing}, extras={extras}")


def find_nwas() -> list[Path]:
    """Quick bounded discovery: local builds and the user's normal download folder."""
    patterns = (
        REPO_ROOT / "dist" / "live" / "*.nwa",
        REPO_ROOT / "dist" / "*.nwa",
        REPO_ROOT / "dist" / "games" / "*.nwa",
        REPO_ROOT / "dist" / "variants" / "*.nwa",
        REPO_ROOT / "dist" / "languages" / "*.nwa",
        REPO_ROOT / "build" / "*.nwa",
        REPO_ROOT / "build" / "apps" / "*.nwa",
        REPO_ROOT / "games" / "*" / "output" / "*.nwa",
        REPO_ROOT / "games" / "*" / "build" / "*.nwa",
        HOME_NWA / "*.nwa",
        HOME_NWA / "apps" / "*.nwa",
    )
    # Path.glob does not accept absolute pattern strings; use glob.glob.
    import glob
    found: dict[str, Path] = {}
    for pattern in patterns:
        for name in glob.glob(str(pattern)):
            path = Path(name).resolve()
            if path.is_file():
                found[str(path).lower()] = path
    return sorted(found.values(), key=lambda p: p.stat().st_mtime_ns, reverse=True)


def nwlink_command() -> str | None:
    if os.name == "nt":
        return shutil.which("nwlink.cmd") or shutil.which("nwlink.exe")
    for p in (shutil.which("nwlink"), Path.home() / ".numplay" / "node_modules" / ".bin" / "nwlink",
              REPO_ROOT / "games" / "numdash" / "node_modules" / ".bin" / "nwlink"):
        if p and Path(p).exists():
            return str(p)
    return None


def fingerprint(path: Path) -> tuple[int, int] | None:
    try:
        s = path.stat()
        return (s.st_size, s.st_mtime_ns)
    except OSError:
        return None


def rgb565_to_photo_bytes(pixels: bytes) -> Image.Image:
    a = np.frombuffer(pixels, dtype="<u2").reshape(240, 320)
    r = ((a >> 11) & 31) * 255 // 31
    g = ((a >> 5) & 63) * 255 // 63
    b = (a & 31) * 255 // 31
    rgb = np.stack((r, g, b), axis=-1).astype(np.uint8)
    return Image.fromarray(rgb, "RGB")


class FidelityFont:
    """Draw EADK firmware text with real glyphs, even without EPSILON_FONTS.

    emu.Font used empty glyph dictionaries whenever EPSILON_FONTS was unset,
    and only populated ASCII 32..126 when it was set. That rendered valid
    firmware draw_string calls as blank foreground-free rectangles. We prefer
    locally supplied Epsilon TTFs and use installed system fonts otherwise.
    No fonts are downloaded or redistributed.
    """

    def __init__(self):
        configured = Path(emu.FONT_DIR).expanduser() if emu.FONT_DIR else None
        repo_fonts = (REPO_ROOT / "tools" / "fonts", REPO_ROOT / "fonts",
                      Path.home() / ".numplay-emulator" / "fonts")
        system_fonts = (
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf",
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/Library/Fonts/Arial.ttf"),
            Path("/System/Library/Fonts/Helvetica.ttc"),
        )
        self.cells = {}
        self.cache = {}
        self.sources = []
        for large, (filename, size, width, height) in (
            (False, ("SmallFont.ttf", 12, 7, 14)),
            (True, ("LargeFont.ttf", 16, 10, 18)),
        ):
            candidates = []
            if configured:
                candidates.append(configured / filename)
            candidates.extend(folder / filename for folder in repo_fonts)
            candidates.extend(system_fonts)
            # Pillow ships/locates DejaVu Sans on most installations. Prefer
            # this over the 1-bit PIL fallback if no system font can be found.
            candidates.append("DejaVuSans.ttf")
            font = None
            source = None
            for location in candidates:
                try:
                    font = ImageFont.truetype(str(location), size)
                    source = str(location)
                    break
                except (OSError, ValueError):
                    pass
            if font is None:
                try:
                    font = ImageFont.load_default(size=size)
                except TypeError:
                    font = ImageFont.load_default()
                source = "Pillow default fallback"
            self.cells[large] = (width, height, font)
            self.cache[large] = {}
            self.sources.append(f"{'large' if large else 'small'}: {source}")
        print("[NumPlay fonts] " + " | ".join(self.sources), file=sys.stderr, flush=True)
        if not configured:
            print("[NumPlay fonts] Exact Epsilon fonts are optional: set EPSILON_FONTS "
                  "to a directory containing SmallFont.ttf and LargeFont.ttf.",
                  file=sys.stderr, flush=True)

    def _glyph(self, char: str, large: bool) -> bytes:
        cache = self.cache[large]
        if char not in cache:
            w, h, f = self.cells[large]
            img = Image.new("L", (w, h), 0)
            asc = f.getmetrics()[0] if hasattr(f, "getmetrics") else h - 2
            # Maintain the baseline used by emu.Font; match fixed EADK cells.
            y = h - 3 - asc + (1 if large else 0)
            if char not in ("\n", "\r", "\t"):
                ImageDraw.Draw(img).text((0, y), char, font=f, fill=255)
            if len(cache) >= 512:
                cache.clear()
            cache[char] = img.tobytes()
        return cache[char]

    def draw(self, screen: bytearray, text: str, x: int, y: int,
             large: bool, fg: int, bg: int) -> int:
        w, h, _ = self.cells[large]
        fg &= 0xffff
        bg &= 0xffff
        # Precalculate the 256 RGB565 alpha blends once per call, not once
        # per pixel; retain the backend's own interpolation semantics.
        palette = []
        for t in range(256):
            out = 0
            for shift, mask in ((11, 31), (5, 63), (0, 31)):
                a = (bg >> shift) & mask
                b = (fg >> shift) & mask
                out |= ((a * (255 - t) + b * t) // 255) << shift
            palette.append(struct.pack("<H", out))
        cx = int(x)
        for char in text:
            if cx >= 320:
                break
            x0, x1 = max(0, -cx), min(w, 320 - cx)
            y0, y1 = max(0, -y), min(h, 240 - y)
            if x0 < x1 and y0 < y1:
                glyph = self._glyph(char, large)
                for j in range(y0, y1):
                    beg = j * w + x0
                    end = j * w + x1
                    dst = ((y + j) * 320 + cx + x0) * 2
                    screen[dst:dst + (end - beg) * 2] = b"".join(palette[v] for v in glyph[beg:end])
            cx += w
        return cx


class TracedCalculator(emu.Calculator):
    """Keep the headless emulator intact, but yield on time-changing syscalls.

    In emu.py, Calculator.run() requests a full millisecond of ARM *instructions*
    at once. A program can call eadk_timing_msleep(16) hundreds of times before
    that instruction budget expires. Each call immediately advances now_ms, so
    a single emu_start() can advance several *seconds* of virtual time. A 2ms
    outer frontend slice cannot interrupt that work.

    End the current Unicorn batch when a system call advances virtual time.
    Calculator.run() then checks its until_ms limit and the frontend gets back
    control between ordinary app sleeps, preserving keyboard responsiveness.
    """

    # EADK event_get with no key (23), flash erase/write (30/32), and the
    # millisecond/microsecond sleep calls (49/50) can advance emulated time.
    TIME_ADVANCING_SVCS = (23, 30, 32, 49, 50)

    def __init__(self, *args, max_sleep_ms: int = 0, **kwargs):
        self.max_sleep_ms = max_sleep_ms
        self.last_long_sleep = None   # (requested_ms, used_ms, svc, pc, symbol)
        self.long_sleep_count = 0
        self.last_svc_yield = None    # (svc, pc, requested_or_delta_ms)
        self.svc_yield_count = 0
        self._sleep_warning_at = {}  # SVC site and duration -> last diagnostic timestamp
        # NumDrive draws 320-pixel-wide strips. Record completed bottom strips
        # as an estimate of actual game frames, independent of UI refreshes.
        self.fullwidth_blits = 0
        self.fullwidth_bytes = 0
        self.complete_display_frames = 0
        self.fullwidth_copy_seconds = 0.0
        # Keep a single CPU-visible framebuffer, just like V13. Unlike V13,
        # publishing waits for a *frame boundary* instead of copying a random
        # partial strip every 25 ms. No V14 LCD scanout or simulated tearing.
        self.lcd_revision = 0
        self.lcd_last_write_wall = time.monotonic()
        self.lcd_last_published_revision = -1
        self.on_lcd_write = None
        self._next_lcd_notice_wall = 0.0
        super().__init__(*args, **kwargs)
        self.font = FidelityFont()
        self.clipped_display_rects = 0
        self.lcd_bus_ms = 0.0
        self._lcd_unyielded_ms = 0.0

    def _charge_lcd(self, uc, ms: float, svc: int):
        """Account for real LCD transfers in virtual time, not host wall time.

        The backend adds one virtual millisecond at the end of each Unicorn
        instruction batch. If a display transfer exhausts the front-end's
        2-ms time slice, exit the batch at the drawing call boundary so input,
        reset and window events stay responsive. The -1 compensates for the
        backend's unconditional +1 immediately after the Unicorn stop.
        """
        if ms <= 0:
            return
        self.now_ms += ms
        self.lcd_bus_ms += ms
        self._lcd_unyielded_ms += ms
        if self._lcd_unyielded_ms >= 1.0:
            elapsed = self._lcd_unyielded_ms
            self._lcd_unyielded_ms = 0.0
            self.last_svc_yield = (svc, uc.reg_read(emu.UC_ARM_REG_PC), elapsed)
            self.svc_yield_count += 1
            self.now_ms -= 1.0
            uc.emu_stop()

    def _lcd_changed(self, complete_frame: bool = False):
        """Record a completed EADK write, but don't expose a partial redraw.

        Full-width pixel strips reaching the bottom of the screen are the
        launcher's natural frame boundary. For games with another drawing
        scheme, event/sleep calls and the worker's quiet-period fallback
        provide other opportunities to publish a complete screen.
        """
        self.lcd_revision += 1
        self.lcd_last_write_wall = time.monotonic()
        if complete_frame:
            self.publish_lcd_if_ready()

    def publish_lcd_if_ready(self, *, force: bool = False) -> bool:
        """Publish at most one stable framebuffer copy per presentation tick.

        This does not touch the app-facing framebuffer, keyboard, emulated time
        or the physical LCD's imagined scan position. It only controls which
        *completed snapshots* reach Tk. Must run on the emulator worker thread.
        """
        callback = self.on_lcd_write
        if callback is None or self.lcd_last_published_revision == self.lcd_revision:
            return False
        now = time.monotonic()
        if not force and now < self._next_lcd_notice_wall:
            return False
        self._next_lcd_notice_wall = now + LCD_UPDATE_INTERVAL_S
        self.lcd_last_published_revision = self.lcd_revision
        callback(self.screen, self.now_ms, self.lcd_revision)
        return True

    def _fast_fullwidth_display(self, uc, svc: int) -> bool:
        """Byte-copy a whole screen-width strip in one operation.

        The original emu.Calculator._intr handles SVC 18/19/20 correctly, but
        copies every row individually in Python. NumDrive's 3D renderer calls
        push_rect(0, strip_y, 320, strip_h) for every rendered strip. Such
        strips are contiguous in *both* source and framebuffer and can be
        transferred exactly with a single Unicorn read/write and slice.
        All partial-width and out-of-bounds rectangles use the original path.
        """
        r0 = uc.reg_read(emu.UC_ARM_REG_R0)
        r1 = uc.reg_read(emu.UC_ARM_REG_R1)
        x, y = r0 & 0xFFFF, r0 >> 16
        w, h = r1 & 0xFFFF, r1 >> 16
        if x != 0 or w != 320 or h == 0 or y + h > 240:
            return False
        started = time.perf_counter()
        offset = y * 320 * 2
        length = w * h * 2
        if svc == 19:   # eadk_display_push_rect
            self.screen[offset:offset + length] = uc.mem_read(
                uc.reg_read(emu.UC_ARM_REG_R2), length)
            if y + h == 240:
                self.complete_display_frames += 1
        elif svc == 18:  # eadk_display_pull_rect
            uc.mem_write(uc.reg_read(emu.UC_ARM_REG_R2),
                         bytes(self.screen[offset:offset + length]))
        elif svc == 20:  # eadk_display_push_rect_uniform
            color = uc.reg_read(emu.UC_ARM_REG_R2) & 0xFFFF
            self.screen[offset:offset + length] = color.to_bytes(2, "little") * (w * h)
        else:
            return False
        # Match the backend's accounting. Do not suppress an EADK syscall.
        self.svc_counts[svc] = self.svc_counts.get(svc, 0) + 1
        self.insns += w * h // 4
        self.fullwidth_blits += 1
        self.fullwidth_bytes += length
        self.fullwidth_copy_seconds += time.perf_counter() - started
        self._charge_lcd(uc, w * h * LCD_MS_PER_PIXEL, svc)
        if svc in (19, 20):
            # Drawing one final full-width image strip is an actual application
            # frame boundary. A uniform color fill may be an initial clear,
            # so don't publish a blank screen as a completed frame.
            self._lcd_changed(complete_frame=(svc == 19 and y + h == LCD_HEIGHT))
        return True

    def _draw_string(self, uc, addr, size, data):
        """Render firmware EADK strings; include Unicode and longer labels.

        The emulator base class read 256 bytes regardless of the string's
        length and silently blanked any non-ASCII glyphs. Respect the NUL
        terminator while limiting input to 2 KiB for a faulty application.
        """
        regs = (emu.UC_ARM_REG_R0, emu.UC_ARM_REG_R1,
                emu.UC_ARM_REG_R2, emu.UC_ARM_REG_R3)
        ptr, point, large, fg = (uc.reg_read(r) for r in regs)
        sp = uc.reg_read(emu.UC_ARM_REG_SP)
        bg = int.from_bytes(uc.mem_read(sp, 2), "little")
        raw = bytearray()
        for offset in range(0, 2048, 32):
            block = bytes(uc.mem_read(ptr + offset, 32))
            nul = block.find(b"\x00")
            raw.extend(block if nul < 0 else block[:nul])
            if nul >= 0:
                break
        text = raw.decode("utf-8", "replace")
        self.font.draw(self.screen, text, point & 0xFFFF, point >> 16,
                       bool(large & 0xFF), fg & 0xFFFF, bg)
        uc.reg_write(emu.UC_ARM_REG_PC, uc.reg_read(emu.UC_ARM_REG_LR))
        self._lcd_changed()
        chars = len(text)
        w, h = (10, 18) if (large & 0xFF) else (7, 14)
        self._charge_lcd(uc, (FONT_CALL_MS + chars * FONT_CHAR_MS *
                              (w * h) / (7 * 14)), -1)

    def _clipped_display(self, uc, svc: int) -> bool:
        """Keep original input-buffer row stride when an EADK rect is clipped.

        The backend reduces w before stepping across source rows. With a
        clipped rectangle, that mistakenly pulls pixels from the next row,
        looking like wrong colors/corrupt graphics at the screen edges.
        """
        r0 = uc.reg_read(emu.UC_ARM_REG_R0)
        r1 = uc.reg_read(emu.UC_ARM_REG_R1)
        x, y = r0 & 0xFFFF, r0 >> 16
        src_w, src_h = r1 & 0xFFFF, r1 >> 16
        if x + src_w <= 320 and y + src_h <= 240:
            return False   # fast path or the backend already handles this.
        self.clipped_display_rects += 1
        if self.clipped_display_rects <= 4:
            print(f"[NumPlay display] Clipped SVC {svc}: rect "
                  f"({x}, {y}, {src_w}, {src_h})", file=sys.stderr)
        if x >= 320 or y >= 240 or src_w == 0 or src_h == 0:
            self.svc_counts[svc] = self.svc_counts.get(svc, 0) + 1
            return True
        w, h = min(src_w, 320 - x), min(src_h, 240 - y)
        pointer = uc.reg_read(emu.UC_ARM_REG_R2)
        for row in range(h):
            dst = ((y + row) * 320 + x) * 2
            length = w * 2
            if svc == 19:  # Push rows, preserving the ORIGINAL rectangle width.
                self.screen[dst:dst + length] = uc.mem_read(pointer + row * src_w * 2, length)
            elif svc == 18:  # Pull rows, likewise preserve destination stride.
                uc.mem_write(pointer + row * src_w * 2, bytes(self.screen[dst:dst + length]))
            elif svc == 20:
                color = pointer & 0xFFFF
                self.screen[dst:dst + length] = color.to_bytes(2, "little") * w
        self.svc_counts[svc] = self.svc_counts.get(svc, 0) + 1
        self.insns += w * h // 4
        self._charge_lcd(uc, w * h * LCD_MS_PER_PIXEL, svc)
        if svc in (19, 20) and w and h:
            self._lcd_changed(complete_frame=(svc == 19 and x == 0 and
                                              w == LCD_WIDTH and y + h == LCD_HEIGHT))
        return True

    def _intr(self, uc, intno, data):
        # The backend registers self._intr during Calculator.__init__.
        if intno != 2:
            return super()._intr(uc, intno, data)
        pc = uc.reg_read(emu.UC_ARM_REG_PC)
        op = int.from_bytes(uc.mem_read(pc - 2, 2), "little")
        svc = op & 0xFF
        if svc in (18, 19, 20):
            if self._fast_fullwidth_display(uc, svc) or self._clipped_display(uc, svc):
                return
        start_ms = float(self.now_ms)
        requested_ms = None
        used_ms = None
        if svc in (49, 50):
            raw = uc.reg_read(emu.UC_ARM_REG_R0)
            requested_ms = float(raw if svc == 49 else raw / 1000.0)
            used_ms = requested_ms
            if self.max_sleep_ms and requested_ms > self.max_sleep_ms:
                limit = self.max_sleep_ms * (1000 if svc == 50 else 1)
                uc.reg_write(emu.UC_ARM_REG_R0, int(limit))
                used_ms = float(self.max_sleep_ms)

        # Run the backend's real SVC implementation: preserves keyboard,
        # flash, display, register returns, and all other NumPlay semantics.
        result = super()._intr(uc, intno, data)
        if svc in (18, 19, 20):
            # The backend performed an in-bounds partial-width transfer.
            r1 = uc.reg_read(emu.UC_ARM_REG_R1)
            pixels = (r1 & 0xFFFF) * (r1 >> 16)
            self._charge_lcd(uc, pixels * LCD_MS_PER_PIXEL, svc)
        if svc in (19, 20):
            # The backend handles ordinary partial-width display writes.
            # Don't send Tk a half-drawn framebuffer at this SVC boundary.
            self._lcd_changed()
        elif svc in (23, 49, 50):
            # Games commonly check events or sleep between complete frames.
            # Prefer that natural boundary to a timer-driven partial snapshot.
            self.publish_lcd_if_ready()
        delta_ms = float(self.now_ms) - start_ms

        if requested_ms is not None:
            if requested_ms >= 250 or used_ms != requested_ms:
                where = pc - 2
                symbol = "(stripped/unknown symbol)"
                for begin, end, name in getattr(self, "functions", ()):
                    if begin <= where < end:
                        symbol = name
                        break
                self.last_long_sleep = (requested_ms, used_ms, svc, where, symbol)
                self.long_sleep_count += 1
            # Trace normal 16ms calls too (an earlier version only traced >=250ms),
            # which is why sleep_svc=None even for a 3808ms step.
            if delta_ms >= 1:
                signature = (svc, pc - 2, round(requested_ms, 1))
                now = time.monotonic()
                if now - self._sleep_warning_at.get(signature, -1e9) >= 30:
                    self._sleep_warning_at[signature] = now
                    extra = (f" (guarded to {used_ms:.0f}ms)"
                             if used_ms != requested_ms else "")
                    print(f"[NumPlay sleep] svc={svc} PC=0x{pc-2:08x}; "
                          f"requested={requested_ms:.1f}ms{extra}; "
                          "yielding at SVC boundary",
                          file=sys.stderr, flush=True)

        if svc == 23 and delta_ms >= 1:
            # emu.py's 'no event' path adds 10ms, but does not mark the
            # interval as idle. Correct the worker's CPU-load measurement.
            self.slept_ms += delta_ms

        if svc in self.TIME_ADVANCING_SVCS and delta_ms >= 0.5:
            self.last_svc_yield = (svc, pc - 2, delta_ms)
            self.svc_yield_count += 1
            # emu.py unconditionally adds +1ms after emu_start() returns,
            # assuming its entire instruction budget ran. We deliberately
            # stopped before the budget was exhausted. Cancel that extra ms
            # now; the backend adds it back immediately after emu_stop().
            # (CPU cycles spent before the SVC remain an approximation.)
            self.now_ms -= 1.0
            uc.emu_stop()
        return result


class EmulatorWorker(threading.Thread):
    def __init__(self, app_path: Path, nwlink: str, events: queue.Queue,
                 stop: threading.Event, paused: threading.Event,
                 pressed: set[str], pressed_lock: threading.Lock, save_dir: Path,
                 model: str = "n0120", system: str = "epsilon",
                 clock_mhz: list[int] | None = None,
                 auto_clock: list[bool] | None = None,
                 max_sleep_ms: int = 0,
                 profile_speed: bool = False):
        super().__init__(daemon=True)
        self.app_path, self.nwlink, self.events = app_path, nwlink, events
        self.stop_requested, self.paused = stop, paused
        self.pressed, self.pressed_lock = pressed, pressed_lock
        self.save_dir = save_dir
        self.model, self.system = model, system
        self.max_sleep_ms = max_sleep_ms
        self.profile_speed = profile_speed
        # The UI may change this list's sole int while the worker runs.
        # Access is atomic under Python's GIL; only this worker calls emu.run().
        self.clock_mhz = clock_mhz if clock_mhz is not None else [216]
        self.auto_clock = auto_clock if auto_clock is not None else [True]
        self.end_reason: str | None = None
        self.last_sleep_delta_ms = 0.0
        self.last_svc_detail = None
        self.last_sleep_detail = None
        # Plain Python attributes: GUI only reads them; the worker only writes.
        # These distinguish an overloaded Unicorn call from a legitimate EADK sleep.
        self.phase = "initializing"
        self.phase_since_wall = time.monotonic()
        self.last_progress_wall = self.phase_since_wall
        self.last_progress_ms = 0.0
        self.last_cpu_seconds = 0.0
        self.last_virtual_delta = 0.0

    def set_phase(self, name: str):
        if name != self.phase:
            self.phase = name
            self.phase_since_wall = time.monotonic()

    def emit(self, kind: str, *payload):
        if kind == "frame":
            try:
                self.events.put_nowait((kind, *payload))
            except queue.Full:
                # Drop an old screen rather than lagging behind the real app.
                try:
                    self.events.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.events.put_nowait((kind, *payload))
                except queue.Full:
                    pass
        else:
            # A small event queue is enough, even if frame production is fast.
            while True:
                try:
                    self.events.put_nowait((kind, *payload))
                    break
                except queue.Full:
                    try:
                        self.events.get_nowait()
                    except queue.Empty:
                        pass

    def storage_path(self) -> Path:
        # Keep saves for separate .nwa names in a stable user directory.
        import hashlib
        unique = hashlib.sha256(str(self.app_path).lower().encode("utf-8")).hexdigest()[:10]
        return self.save_dir / f"{self.app_path.stem}-{unique}.bin"

    def read_keyboard(self) -> int:
        with self.pressed_lock:
            names = tuple(self.pressed)
        state = 0
        for k in names:
            state |= 1 << emu.KEYS[k]
        return state

    def run(self):
        app = None
        save_path = self.storage_path()
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            self.emit("status", f"Linking {self.app_path.name} with nwlink")
            emu.configure(self.model, self.system)
            app = TracedCalculator(str(self.app_path), storage_file=str(save_path),
                                   nwlink=self.nwlink, max_sleep_ms=self.max_sleep_ms)
            # Avoid rewriting a save file 30 times per second; save on stop.
            app.storage_file = None
            app.keyboard_state = self.read_keyboard
            self.emit("status", f"Running {self.app_path.name}")
            # Run ARM instructions in *small* virtual-time slices. A full 33ms
            # emu.Calculator.run() was blocking the only CPU worker for seconds
            # in expensive games and at module transitions. A 2ms slice limits
            # the time before the next keyboard/frame/stop check. It preserves
            # the backend's instruction-count and EADK msleep clock model.
            was_paused = False
            clock_anchor = time.monotonic()
            last_frame_wall = clock_anchor
            last_frame_virtual = float(app.now_ms)
            last_frame_revision = -1

            def publish_lcd(pixels, virtual_ms, revision):
                # Called synchronously from the ARM worker's display hook.
                # The event carries a *copy*; the emulator can continue drawing
                # while Tk consumes it asynchronously. No fabricated swipe.
                nonlocal last_frame_wall, last_frame_virtual, last_frame_revision
                if self.stop_requested.is_set():
                    return
                self.emit("frame", bytes(pixels), int(virtual_ms))
                last_frame_wall = time.monotonic()
                last_frame_virtual = float(virtual_ms)
                last_frame_revision = revision

            app.on_lcd_write = publish_lcd
            rate_wall = clock_anchor
            rate_virtual = float(app.now_ms)
            busy_wall = busy_virtual = 0.0
            last_auto_adjust = clock_anchor
            smoothed_load = None
            self.last_progress_wall = clock_anchor
            self.last_progress_ms = float(app.now_ms)
            profile_start = clock_anchor
            profile_start_virtual = float(app.now_ms)
            profile_start_frames = app.complete_display_frames
            profile_start_blits = app.fullwidth_blits
            profile_start_copy_seconds = app.fullwidth_copy_seconds
            profile_cpu_seconds = 0.0
            profile_wait_seconds = 0.0
            while not self.stop_requested.is_set() and not app.exited:
                if self.paused.is_set():
                    self.set_phase("paused")
                    was_paused = True
                    self.stop_requested.wait(0.035)
                    continue
                if was_paused:
                    # Re-anchor; never run faster to pay back paused/late frames.
                    clock_anchor = time.monotonic()
                    last_auto_adjust = rate_wall = clock_anchor
                    app._next_lcd_notice_wall = 0.0
                    # Show the last completed image when resuming from pause.
                    app.publish_lcd_if_ready(force=True)
                    rate_virtual = float(app.now_ms)
                    busy_wall = busy_virtual = 0.0
                    smoothed_load = None
                    was_paused = False
                    profile_start = clock_anchor
                    profile_start_virtual = float(app.now_ms)
                    profile_start_frames = app.complete_display_frames
                    profile_start_blits = app.fullwidth_blits
                    profile_start_copy_seconds = app.fullwidth_copy_seconds
                    profile_cpu_seconds = profile_wait_seconds = 0.0

                emu.CPU_HZ = max(27, min(550, int(self.clock_mhz[0]))) * 1_000_000
                started = time.monotonic()
                self.set_phase("running ARM CPU")
                before_ms = float(app.now_ms)
                before_sleep_ms = float(app.slept_ms)
                before_sleep_count = app.long_sleep_count
                before_yield_count = app.svc_yield_count
                app.run(app.now_ms + STEP_VIRTUAL_MS)
                work_seconds = max(0.0, time.monotonic() - started)
                profile_cpu_seconds += work_seconds
                delta_ms = max(0.0, float(app.now_ms) - before_ms)
                slept_ms = max(0.0, float(app.slept_ms) - before_sleep_ms)
                active_ms = max(0.0, delta_ms - min(delta_ms, slept_ms))
                self.last_cpu_seconds = work_seconds
                self.last_virtual_delta = delta_ms
                self.last_sleep_delta_ms = slept_ms
                self.last_sleep_detail = (app.last_long_sleep
                                          if app.long_sleep_count != before_sleep_count else None)
                self.last_svc_detail = (app.last_svc_yield
                                        if app.svc_yield_count != before_yield_count else None)
                self.last_progress_wall = time.monotonic()
                self.last_progress_ms = float(app.now_ms)
                if self.stop_requested.is_set() or app.exited:
                    break

                # A large emulated sleep is not evidence that the CPU is slow.
                # In particular, don't downclock because a single emulated
                # millisecond of ARM work accompanied a six-second SVC sleep.
                if self.auto_clock[0] and active_ms >= 0.5 and slept_ms < 250:
                    busy_wall += work_seconds
                    busy_virtual += active_ms

                # Fallback for apps that paint isolated patches but don't
                # sleep, request events or end with a full-width bottom strip.
                # Avoid showing intermediate top-to-bottom redraw strips:
                # require a quiet window after the most recent LCD write.
                # The stale timeout guarantees that even unconventional games
                # eventually show progress, at the cost of an occasional tear.
                wall_now = time.monotonic()
                if (app.lcd_revision != app.lcd_last_published_revision and
                        wall_now - last_frame_wall >= LCD_UPDATE_INTERVAL_S and
                        (wall_now - app.lcd_last_write_wall >= LCD_QUIET_INTERVAL_S or
                         wall_now - last_frame_wall >= LCD_STALE_FRAME_S)):
                    app.publish_lcd_if_ready()

                # One simulated millisecond must never take *less* than one
                # actual millisecond. Sleep overestimated virtual time, but
                # stop waiting as soon as Reload, Pause or Power is requested.
                # Keep a continuous timeline when on time; erase lateness
                # beyond 40ms so there are NO catch-up bursts.
                clock_anchor = max(clock_anchor, started - 0.040)
                clock_anchor += delta_ms / 1000.0
                wait_started = time.monotonic()
                while not self.stop_requested.is_set() and not self.paused.is_set():
                    remaining = clock_anchor - time.monotonic()
                    if remaining <= 0:
                        break
                    self.set_phase("waiting for app sleep" if slept_ms >= 250
                                   else "waiting for emulated time")
                    self.stop_requested.wait(min(remaining, 0.015))
                profile_wait_seconds += max(0.0, time.monotonic() - wait_started)
                if self.stop_requested.is_set():
                    break
                if self.paused.is_set():
                    was_paused = True
                    continue

                self.set_phase("running ARM CPU")
                wall_now = time.monotonic()
                if self.profile_speed and wall_now - profile_start >= 5.0:
                    seconds = wall_now - profile_start
                    frames = app.complete_display_frames - profile_start_frames
                    blits = app.fullwidth_blits - profile_start_blits
                    simulated = float(app.now_ms) - profile_start_virtual
                    # One full-width bottom strip generally completes one
                    # NumDrive 3D image. Report graphics throughput *separately*
                    # from how quickly the emulated virtual clock advances.
                    copied_ms = (app.fullwidth_copy_seconds -
                                 profile_start_copy_seconds) * 1000.0
                    avg_frame_ms = (f"{1000*profile_cpu_seconds/frames:.1f}"
                                    if frames else "n/a")
                    print(f"[NumPlay speed profile] "
                          f"render_fps~{frames/seconds:.1f}; "
                          f"virtual_rate={simulated/(1000*seconds):.2f}x; "
                          f"ARM_work={100*profile_cpu_seconds/seconds:.0f}%; "
                          f"wall_wait={100*profile_wait_seconds/seconds:.0f}%; "
                          f"CPU={int(self.clock_mhz[0])}MHz "
                          f"({'auto' if self.auto_clock[0] else 'manual'}); "
                          f"fullwidth_blits={blits}; "
                          f"copy_ms={copied_ms:.1f}; "
                          f"avg_ARM_ms_per_frame={avg_frame_ms}",
                          file=sys.stderr, flush=True)
                    profile_start = wall_now
                    profile_start_virtual = float(app.now_ms)
                    profile_start_frames = app.complete_display_frames
                    profile_start_blits = app.fullwidth_blits
                    profile_start_copy_seconds = app.fullwidth_copy_seconds
                    profile_cpu_seconds = profile_wait_seconds = 0.0
                # Calibrate based on actual Unicorn throughput, separately
                # from time spent inside EADK sleep. Earlier 1.25s sampling
                # intervals could take far longer in a busy scene. Faster
                # sampling and bounded steps make this responsive without a
                # constantly oscillating MHz value.
                if self.auto_clock[0] and wall_now - last_auto_adjust >= 0.45:
                    if busy_virtual >= 8.0:
                        load = busy_wall / max(0.001, busy_virtual / 1000.0)
                        smoothed_load = (load if smoothed_load is None else
                                         0.65 * smoothed_load + 0.35 * load)
                        old = int(self.clock_mhz[0])
                        if smoothed_load > 1.05:
                            new = max(27, int(old * 0.80))
                        elif smoothed_load < 0.58 and old < 216:
                            new = min(216, max(old + 1, int(old * 1.05)))
                        else:
                            new = old
                        self.clock_mhz[0] = new
                    busy_wall = busy_virtual = 0.0
                    last_auto_adjust = wall_now
                elif not self.auto_clock[0]:
                    busy_wall = busy_virtual = 0.0
                    smoothed_load = None
                    last_auto_adjust = wall_now

                if wall_now - rate_wall >= 1.0:
                    rate = (float(app.now_ms) - rate_virtual) / (1000.0 * (wall_now - rate_wall))
                    self.emit("pace", round(rate, 2), int(self.clock_mhz[0]), bool(self.auto_clock[0]))
                    rate_wall, rate_virtual = wall_now, float(app.now_ms)
            if app.exited:
                self.set_phase("exited")
                # The caller must blank the screen; the final framebuffer is
                # NOT a valid representation of a powered-off calculator.
                self.end_reason = "exited"
                self.emit("ended", "Application exited — press Power or Reload to restart")
        except Exception:
            self.set_phase("error")
            error_report = traceback.format_exc()
            # Preserve the complete traceback even if the GUI dialog is closed.
            # Never allow a diagnostic logging error to hide the original fault.
            try:
                error_log = SAVE_DIR.parent / "last-error.txt"
                error_log.parent.mkdir(parents=True, exist_ok=True)
                error_log.write_text(error_report, encoding="utf-8")
            except OSError:
                error_log = None
            print(error_report, file=sys.stderr, flush=True)
            if error_log is not None:
                print(f"[NumPlay emulator] Full traceback: {error_log}",
                      file=sys.stderr, flush=True)
            self.emit("error", error_report)
        finally:
            if not app or not app.exited:
                if self.phase != "error":
                    self.set_phase("stopped")
            if app is not None:
                try:
                    save_path.write_bytes(app.storage_bytes())
                except Exception as exc:
                    self.emit("error", f"Could not save emulated storage:\n{exc}")


class LiveApp:
    def __init__(self, root: tk.Tk, initial: Path | None, nwlink: str,
                 model: str, system: str, auto_follow: bool,
                 max_sleep_ms: int = 0, profile_speed: bool = False):
        validate_layout()
        self.root, self.nwlink = root, nwlink
        self.model, self.system = model, system
        self.max_sleep_ms = max_sleep_ms
        self.profile_speed = profile_speed
        # Match the requested calculator model by default. For N0120 the
        # measured integer test was 58ms at 550MHz (52ms on physical hardware).
        # This is a stable virtual-clock baseline, not a cycle-accurate model.
        self.clock_mhz = [550 if model == "n0120" else 216]
        # Host-adaptive mode dynamically changes the virtual clock and can
        # distort game timing; make it opt-in, not the release default.
        self.auto_clock = [False]
        self.powered_off = False
        self._power_key_held = False
        self.root.title("NumPlay emulator")
        self.root.configure(bg=BG)
        self.root.minsize(690, 820)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.worker: EmulatorWorker | None = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.messages: queue.Queue = queue.Queue(maxsize=3)
        self.pressed: set[str] = set()
        self.pressed_pc: dict[str, str] = {}
        self.mouse_pressed: str | None = None
        self.pressed_lock = threading.Lock()
        self.key_widgets: dict[str, tk.Label] = {}
        self.current: Path | None = None
        self.pending: Path | None = None
        self.current_stamp: tuple[int, int] | None = None
        self.watch_candidate: tuple[tuple[str, tuple[int, int]], int] | None = None
        self.watch_var = tk.BooleanVar(value=True)
        self.follow_var = tk.BooleanVar(value=auto_follow)
        self.status = tk.StringVar(value="Ready — drop or open a .nwa")
        self.file_label = tk.StringVar(value="No application loaded")
        self.time_label = tk.StringVar(value="0 ms")
        self._screen_rgb: Image.Image | None = None
        self._photo = None
        self._closing = False
        self._last_frame_wall = time.monotonic()
        self._last_stall_log_wall = 0.0
        self.input_status = tk.StringVar(value="Keys: ready")
        self._build_ui()
        self.root.bind_all("<KeyPress>", self.on_pc_down, add="+")
        self.root.bind_all("<KeyRelease>", self.on_pc_up, add="+")
        self.root.bind_all("<ButtonRelease-1>", self.on_mouse_up, add="+")
        self.root.bind("<FocusOut>", self.on_focus_out, add="+")
        self.root.bind("<FocusIn>", self.on_focus_in, add="+")
        self.root.bind("<Map>", self.on_window_map, add="+")
        if DND_FILES is not None:
            for widget in (self.root, self.screen_label, self.drop_line):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self.on_drop)
        self._poll_id = self.root.after(UI_POLL_MS, self.poll)
        self._watch_id = self.root.after(WATCH_MS, self.watch)
        if initial is not None:
            self.root.after(150, lambda: self.load(initial))
        elif not getattr(self, "_simple_mode_ui", False):
            latest = find_nwas()
            if latest:
                self.root.after(150, lambda: self.load(latest[0]))
            else:
                self.root.after(250, self.open_file)

    def _build_ui(self):
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=12, pady=(12, 4))
        tk.Label(top, text="NumPlay emulator", bg=BG, fg=FG,
                 font=font(17, "bold")).pack(side="left")
        tk.Label(top, textvariable=self.time_label, bg=BG, fg=MUTED,
                 font=font(10)).pack(side="right")
        actions = tk.Frame(self.root, bg=BG)
        actions.pack(fill="x", padx=10, pady=6)
        for title, cb in (("Open .nwa", self.open_file), ("Find newest", self.find_latest),
                          ("Reload", self.reset), ("Pause", self.toggle_pause),
                          ("Screenshot", self.screenshot), ("Keymap", self.show_keymap)):
            button = tk.Button(actions, text=title, command=cb, bg=PANEL_2, fg=FG,
                               activebackground=HOVER, activeforeground=FG,
                               relief="flat", bd=0, padx=9, pady=7, font=font(9))
            button.pack(side="left", padx=2)
            if title == "Pause":
                self.pause_button = button
        choices = tk.Frame(self.root, bg=BG)
        choices.pack(fill="x", padx=12, pady=(0, 6))
        for var, label in ((self.watch_var, "Auto-reload changed .nwa"),
                           (self.follow_var, "Follow newest local build")):
            tk.Checkbutton(choices, text=label, variable=var, bg=BG, fg=MUTED,
                           activebackground=BG, activeforeground=ACCENT,
                           selectcolor=PANEL, font=font(9)).pack(side="left", padx=4)
        path_panel = tk.Frame(self.root, bg=PANEL, padx=10, pady=7)
        path_panel.pack(fill="x", padx=12, pady=2)
        tk.Label(path_panel, textvariable=self.file_label, bg=PANEL, fg=FG,
                 anchor="w", font=font(9)).pack(fill="x")
        center = tk.Frame(self.root, bg=BG)
        center.pack(fill="x", padx=12, pady=8)
        self._screen_rgb = Image.new("RGB", (320, 240), (235, 235, 235))
        self._photo = ImageTk.PhotoImage(self._screen_rgb.resize((640, 480), Image.Resampling.NEAREST))
        self.screen_label = tk.Label(center, image=self._photo, bg="#050505", bd=7, relief="flat")
        self.screen_label.pack(anchor="center")
        self.drop_line = tk.Label(center, text=("Drop a .nwa here, " if DND_FILES else "") +
                                  "click a key or use your PC keyboard", bg=BG, fg=MUTED,
                                  font=font(9), pady=4)
        self.drop_line.pack()
        keyboard = tk.Frame(self.root, bg=BG)
        keyboard.pack(fill="both", expand=True, padx=12, pady=5)
        for col in range(6):
            keyboard.grid_columnconfigure(col, weight=1, uniform="numworks")
        for i, row in enumerate(KEY_ROWS):
            keyboard.grid_rowconfigure(i, weight=1)
            for j, key in enumerate(row):
                if key is None:
                    continue
                bg = ACCENT if key in ("ok", "exe") else (SELECT if key in
                        ("left", "right", "up", "down") else PANEL_2)
                if key in ("home", "onoff", "shift", "alpha"):
                    bg = HOVER
                w = tk.Label(keyboard, text=LABEL.get(key, key), bg=bg, fg=FG,
                             relief="flat", bd=0, font=font(10, "bold"),
                             padx=5, pady=5, cursor="hand2")
                w.grid(row=i, column=j, sticky="nsew", padx=3, pady=3)
                w.bind("<ButtonPress-1>", lambda e, k=key: self.on_mouse_down(k))
                w.bind("<ButtonRelease-1>", self.on_mouse_up)
                self.key_widgets[key] = w
        status = tk.Label(self.root, textvariable=self.status, bg=PANEL, fg=ACCENT,
                          anchor="w", padx=12, pady=7, font=font(9))
        status.pack(fill="x", side="bottom")
        self.root.focus_force()

    def set_status(self, msg: str):
        self.status.set(msg)

    def open_file(self):
        path = filedialog.askopenfilename(parent=self.root, title="Open a NumWorks application",
                                          filetypes=[("NumWorks apps", "*.nwa"), ("All files", "*.*")],
                                          initialdir=str(HOME_NWA if HOME_NWA.exists() else REPO_ROOT))
        if path:
            self.follow_var.set(False)
            self.load(Path(path))

    def on_drop(self, event):
        for s in self.root.tk.splitlist(event.data):
            if s.lower().endswith(".nwa"):
                self.follow_var.set(False)
                self.load(Path(s))
                return
        self.set_status("Drop a .nwa file from File Explorer.")

    def find_latest(self):
        found = find_nwas()
        if found:
            self.load(found[0])
        else:
            messagebox.showinfo("No .nwa found", "Build a .nwa or drop one here.\n"
                                "Searches build/, games/*/output/ and Downloads/nwa/.")

    def load(self, path: Path):
        path = path.expanduser().resolve()
        if path.suffix.lower() != ".nwa" or not path.is_file():
            self.set_status(f"Not a valid .nwa file: {path}")
            return
        self.pending = path
        self.pause_event.clear()
        self.pause_button.configure(text="Pause")
        self.clear_keys()
        if self.worker is not None and self.worker.is_alive():
            self.stop_event.set()
            self.set_status(f"Reloading {path.name}")
        else:
            self.start_pending()

    def start_pending(self):
        path = self.pending
        if path is None:
            return
        self.pending = None
        self.current = path
        self._last_frame_wall = time.monotonic()
        self._last_stall_log_wall = 0.0
        self.current_stamp = fingerprint(path)
        self.watch_candidate = None
        self.file_label.set(str(path))
        self.time_label.set("0 ms")
        self.messages = queue.Queue(maxsize=3)
        self.stop_event = threading.Event()
        self.powered_off = False
        self.worker = EmulatorWorker(path, self.nwlink, self.messages, self.stop_event,
                                     self.pause_event, self.pressed, self.pressed_lock,
                                     SAVE_DIR, self.model, self.system,
                                     self.clock_mhz, self.auto_clock,
                                     self.max_sleep_ms, self.profile_speed)
        self.worker.start()

    def reset(self):
        if self.current:
            self.load(self.current)
        else:
            self.find_latest()

    def present_screen(self):
        """Display the current RGB screen; overridden by the skinned GUI."""
        if self._screen_rgb is None:
            return
        self._photo = ImageTk.PhotoImage(self._screen_rgb.resize((640, 480), Image.Resampling.NEAREST))
        self.screen_label.configure(image=self._photo)

    def black_screen(self, reason: str):
        self.powered_off = True
        self._screen_rgb = Image.new("RGB", (320, 240), (0, 0, 0))
        self.present_screen()
        self.time_label.set("Powered off")
        self.set_status(reason)

    def power_button(self):
        """Power button is a frontend power toggle, not a stuck ARM key.

        Epsilon's power-off firmware isn't emulated. Sending the raw on/off
        key can leave the app waiting forever for firmware wakeup, so blank
        the screen and stop the worker. Click again to boot the .nwa anew.
        """
        if self.powered_off:
            self.reset()
        else:
            self.pending = None
            self.stop_event.set()
            self.pause_event.clear()
            self.pause_button.configure(text="Pause")
            self.clear_keys()
            self.black_screen("Powered off — press Power or Reload to restart")

    def toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_button.configure(text="Pause")
            self.set_status("Resumed")
        else:
            self.pause_event.set()
            self.pause_button.configure(text="Resume")
            self.set_status("Paused (emulated clock stopped)")

    def screenshot(self):
        if self._screen_rgb is None:
            return
        directory = REPO_ROOT / "build" / "emu-live"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.current.stem if self.current else 'screen'}-{int(time.time())}.png"
        self._screen_rgb.save(path)
        self.set_status(f"Screenshot saved: {path}")

    def on_mouse_down(self, key: str):
        if key == "onoff":
            self.power_button()
            return
        if self.powered_off:
            return
        if self.mouse_pressed is not None:
            self.on_mouse_up()
        self.mouse_pressed = key
        self.input_status.set(f"Keys: {key}")
        self.refresh_pressed()

    def on_mouse_up(self, event=None):
        if self.mouse_pressed is not None:
            self.mouse_pressed = None
            self.refresh_pressed()

    def on_pc_down(self, event):
        # Don't send keyboard shortcuts from another Toplevel (e.g. Keymap).
        if event.widget.winfo_toplevel() is not self.root:
            return None
        sym = event.keysym.lower()
        key = PC_KEYSYM.get(sym)
        if key == "onoff":
            if not self._power_key_held:
                self._power_key_held = True
                self.power_button()
            return "break"
        if self.powered_off:
            return "break" if key else None
        if key:
            self.pressed_pc[sym] = key
            self.input_status.set(f"Keys: {key}")
            self.refresh_pressed()
            # ALL NumWorks keys must be intercepted, not just Space. Otherwise
            # Tk buttons/dropdowns can steal arrow keys after a focus change.
            return "break"
        return None

    def on_pc_up(self, event):
        if event.widget.winfo_toplevel() is not self.root:
            return None
        sym = event.keysym.lower()
        if PC_KEYSYM.get(sym) == "onoff":
            self._power_key_held = False
            return "break"
        if sym in self.pressed_pc:
            self.pressed_pc.pop(sym, None)
            self.refresh_pressed()
            return "break"
        return None

    def on_focus_out(self, event):
        # Releasing Alt, Shift or directions in another window does not produce
        # KeyRelease events here. Prevent ghost keys when returning to the game.
        if event.widget is self.root:
            self.clear_keys()
            self.input_status.set("Keys: released")

    def on_focus_in(self, event):
        if event.widget is self.root:
            self.root.after(60, self.refocus_input)

    def on_window_map(self, event):
        if event.widget is self.root:
            self.root.after(80, self.refocus_input)

    def refocus_input(self):
        """Give keyboard events to the calculator after a dialog or Alt-Tab.

        Use focus_set (not focus_force), to avoid grabbing focus away from
        other Windows applications when we're in the background.
        """
        if self._closing or not hasattr(self, "canvas"):
            return
        try:
            if self.root.focus_displayof() is not None:
                self.canvas.focus_set()
                self.input_status.set("Keys: ready")
        except tk.TclError:
            pass

    def clear_keys(self):
        self.pressed_pc.clear()
        self.mouse_pressed = None
        self._power_key_held = False
        self.refresh_pressed()

    def refresh_pressed(self):
        names = set(self.pressed_pc.values())
        if self.mouse_pressed is not None:
            names.add(self.mouse_pressed)
        with self.pressed_lock:
            self.pressed.clear()
            self.pressed.update(names)
        for k, widget in self.key_widgets.items():
            if k in names:
                widget.configure(bg=ACCENT, fg=FG)
            else:
                widget.configure(fg=FG, bg=(ACCENT if k in ("ok", "exe") else
                       SELECT if k in ("left", "right", "up", "down") else
                       HOVER if k in ("home", "onoff", "shift", "alpha") else PANEL_2))

    def show_keymap(self):
        w = tk.Toplevel(self.root)
        w.title("PC keyboard shortcuts")
        w.configure(bg=BG)
        w.geometry("615x430")
        lines = [
            "ALL calculator keys are clickable on the main window.",
            "Hold mouse button for continuous keypress (e.g. duck).",
            "", "Arrows   = ← ↑ ↓ →        Enter / Space = OK       Numpad Enter = EXE",
            "Esc = Back    Home / F1 = Home     Backspace / Delete = ⌫",
            "Shift = Shift     Alt = Alpha        F8 = Power",
            "", "F2 Back     F3 Shift     F4 Alpha     F5 Toolbox",
            "F6 var        F7 x,n,t        F9 ans         F10 EXE",
            "F11 = Fullscreen in skinned UI (√ in plain UI)     F12 = x²", "",
            "Digits 0–9, numpad, + - * / , . ( ) ^ work as expected.",
            "", "x=x,n,t    v=var     b=Toolbox    e=eˣ    l=ln     g=log",
            "i=i       s=sin     c=cos      t=tan   p=π      q=√",
            "w=x²      r=^       n=×10ⁿ      a=ans", "",
            "Drag any .nwa into the window or click Open .nwa.",
            "Single file: no automatic builds or reloads.",
            "Live development: compiles sources and reloads after a successful build.",
            "Emulated saves persist in ~/.numplay-emulator/saves/.",
        ]
        tk.Label(w, text="\n".join(lines), justify="left", bg=BG, fg=FG,
                 font=mono(10), padx=18, pady=14).pack(anchor="nw")

    def poll(self):
        if self._closing:
            return
        newest_frame = None
        last_status = None
        ended = None
        error = None
        while True:
            try:
                m = self.messages.get_nowait()
            except queue.Empty:
                break
            if m[0] == "frame":
                newest_frame = m
            elif m[0] == "status":
                last_status = m[1]
            elif m[0] == "ended":
                ended = m[1]
            elif m[0] == "error":
                error = m[1]
        if last_status and not self.powered_off:
            self.set_status(last_status)
        if not self.powered_off and newest_frame and not ended:
            _, raw, elapsed = newest_frame
            try:
                self._screen_rgb = rgb565_to_photo_bytes(raw)
                self.present_screen()
                self.time_label.set(f"{elapsed:,} ms (virtual)")
            except Exception as exc:
                self.set_status(f"Display error: {exc}")
        if ended and not self.powered_off:
            self.black_screen(ended)
        elif (self.worker is not None and self.worker.end_reason == "exited"
              and not self.powered_off):
            # Also handle a full message queue dropping an end notification.
            self.black_screen("Application exited — press Reload to restart")
        if error and not self.powered_off:
            self.set_status("Emulator error — see dialog")
            messagebox.showerror("NumPlay emulator", error)
        if self.pending is not None and (self.worker is None or not self.worker.is_alive()):
            self.start_pending()
        self._poll_id = self.root.after(UI_POLL_MS, self.poll)

    def watch(self):
        if self._closing:
            return
        try:
            if self.follow_var.get():
                latest = find_nwas()
                if latest and (self.current is None or latest[0] != self.current):
                    # Switch only when a new build is stable across checks.
                    self._maybe_reload(latest[0])
                elif self.current is not None and self.watch_var.get():
                    self._maybe_reload(self.current)
            elif self.current is not None and self.watch_var.get():
                self._maybe_reload(self.current)
        except OSError as exc:
            self.set_status(f"Auto-reload check: {exc}")
        self._watch_id = self.root.after(WATCH_MS, self.watch)

    def _maybe_reload(self, path: Path):
        stamp = fingerprint(path)
        if stamp is None:
            return
        if path == self.current and stamp == self.current_stamp:
            self.watch_candidate = None
            return
        marker = (str(path), stamp)
        if self.watch_candidate is None or self.watch_candidate[0] != marker:
            self.watch_candidate = (marker, 1)
            return
        self.watch_candidate = None
        self.load(path)

    def on_close(self):
        self._closing = True
        self.stop_event.set()
        self.clear_keys()
        # Worker is a daemon; allow a short non-blocking poll to save storage.
        self._close_deadline = time.monotonic() + 3.0
        self._finish_close()

    def _finish_close(self):
        if self.worker is None or not self.worker.is_alive() or time.monotonic() > self._close_deadline:
            for callback in (self._poll_id, self._watch_id):
                try:
                    self.root.after_cancel(callback)
                except tk.TclError:
                    pass
            self.root.destroy()
        else:
            self.root.after(100, self._finish_close)


# ---------- Calculator artwork + interface (merged from V4 skin) ----------
ART_SOURCE = "https://github.com/k-kuroguro/vscode-numworks/blob/master/resources/webview/background.png"
ART_LICENSE = "https://github.com/k-kuroguro/vscode-numworks/blob/master/resources/LICENSE"

# Matches resources/webview/index.css and index.ejs in the extension.
# Coordinates are normalized against the background.png image.
SCREEN = (0.1625, 0.085, 0.675, 0.263)
NAV = {
    "left": (0.020, 0.370, 0.150, 0.280),
    "right": (0.170, 0.370, 0.150, 0.280),
    "up": (0.120, 0.090, 0.100, 0.400),
    "down": (0.120, 0.530, 0.100, 0.400),
    "home": (0.410, 0.150, 0.165, 0.310),
    "onoff": (0.410, 0.554, 0.165, 0.310),
    "ok": (0.670, 0.310, 0.135, 0.380),
    "back": (0.833, 0.310, 0.135, 0.380),
}
# Extension's HTML ordering, grouped by the extension's CSS rows.
FUNCTIONS = (
    "shift", "alpha", "xnt", "var", "toolbox", "backspace",
    "exp", "ln", "log", "i", "comma", "power",
    "sin", "cos", "tan", "pi", "sqrt", "square",
)
DIGITS = (
    "7", "8", "9", "lparen", "rparen",
    "4", "5", "6", "mul", "div",
    "1", "2", "3", "plus", "minus",
    "0", "dot", "ee", "ans", "exe",
)


def skin_hotspots() -> dict[str, tuple[float, float, float, float]]:
    """Recover key rectangles from the extension's CSS, in image fractions."""
    kx, ky, kw, kh = 0.10, 0.385, 0.81, 0.565
    boxes = {}
    for key, (x, y, w, h) in NAV.items():
        # .nav itself is only 27% of the keyboard height.
        boxes[key] = (kx + x * kw, ky + y * kh * .27, w * kw, h * kh * .27)

    # CSS percentage top/bottom margins use the containing block WIDTH.
    for keys, top, left, width, per_row, cell_w, cell_h, mx, my in (
        (FUNCTIONS, .2675, .005, .98, 6, .1465, .10, .01, .017),
        (DIGITS, .565, .005, .98, 5, .16, .113, .02, .018),
    ):
        cw = kw * width       # child group width as a fraction of image width
        for index, key in enumerate(keys):
            row, col = divmod(index, per_row)
            x = kx + kw * left + cw * (col * (cell_w + 2 * mx) + mx)
            # Vertical CSS %-margins/paddings are percentages of group width
            # (pixel width), so convert image-width units into image-height units.
            # Exact source dimensions: background.png is 1160x2221.
            y = ky + kh * top + (cw * 1160 / 2221) * (row * (cell_h + 2 * my) + my)
            w = cw * cell_w
            h = (cw * 1160 / 2221) * cell_h
            boxes[key] = (x, y, w, h)
    return boxes


HOTSPOTS = skin_hotspots()
assert set(HOTSPOTS) == set(emu.KEYS), (set(emu.KEYS) - set(HOTSPOTS))
assert all(0 <= x < 1 and 0 <= y < 1 and x + w <= 1.01 and y + h <= 1.01
           for x, y, w, h in HOTSPOTS.values())


def installed_artwork() -> Path | None:
    """Use the user's own VS Code extension installation, without copying art."""
    locations = (
        Path.home() / ".vscode" / "extensions",
        Path.home() / ".vscode-insiders" / "extensions",
        Path.home() / ".vscode-oss" / "extensions",
    )
    files = []
    for root in locations:
        files.extend(root.glob("k-kuroguro.numworks-simulator-*/resources/webview/background.png"))
    files = [p for p in files if p.is_file()]
    return max(files, key=lambda x: x.stat().st_mtime_ns) if files else None


class FlatButton(tk.Label):
    """A plain button that keeps its colours on every system (a Mac's tk.Button ignores them): grey, or NumWorks
    yellow for the one main action; greyed out when disabled. Takes text=, state= and command= like tk.Button."""

    def __init__(self, parent, text, command, primary=False):
        self._bg = ACCENT if primary else PANEL_2
        super().__init__(parent, text=text, bg=self._bg, fg=("#34343a" if primary else FG), font=font(10, "bold" if primary else "normal"),
                         pady=8, cursor="hand2", disabledforeground="#b5b5b5")
        self._command = command
        self.bind("<Enter>", lambda e: self._enabled() and super(FlatButton, self).configure(
            bg="#ffc85c" if self._bg == ACCENT else HOVER))
        self.bind("<Leave>", lambda e: super(FlatButton, self).configure(bg=self._bg))
        self.bind("<ButtonRelease-1>", lambda e: self._enabled() and self._command and self._command())

    def _enabled(self):
        return str(self.cget("state")) != "disabled"

    def configure(self, cnf=None, **kw):
        if "command" in kw:
            self._command = kw.pop("command")
        return super().configure(cnf, **kw)

    config = configure


def builtin_artwork() -> Path:
    """A calculator of its own, for when the VS Code extension isn't installed: the same layout (HOTSPOTS,
    SCREEN) on a 1160 x 2221 picture, drawn once and kept in ~/.numplay-emulator."""
    if BUILTIN_SKIN.is_file():
        return BUILTIN_SKIN
    w, h = 1160, 2221
    im = Image.new("RGB", (w, h), (232, 234, 238))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((6, 6, w - 7, h - 7), radius=96, fill=(250, 250, 251), outline=(200, 204, 211), width=6)
    sx, sy, sw, sh = SCREEN[0] * w, SCREEN[1] * h, SCREEN[2] * w, SCREEN[3] * h
    d.rounded_rectangle((sx - 36, sy - 36, sx + sw + 36, sy + sh + 36), radius=28, fill=(38, 40, 46))
    d.rectangle((sx, sy, sx + sw, sy + sh), fill=(0, 0, 0))
    font = None
    for name in ("DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf",
                 str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf")):
        try:
            font = ImageFont.truetype(name, 42)
            break
        except OSError:
            pass
    font = font or ImageFont.load_default()
    nav = {"left", "right", "up", "down", "home", "onoff", "ok", "back"}
    for key, (x, y, kw, kh) in HOTSPOTS.items():
        box = (x * w, y * h, (x + kw) * w, (y + kh) * h)
        fill = (212, 216, 222) if key in nav else (225, 228, 233) if key in FUNCTIONS else (255, 255, 255)
        d.rounded_rectangle(box, radius=min(box[2] - box[0], box[3] - box[1]) * 0.22, fill=fill,
                            outline=(178, 183, 192), width=4)
        label = "DEL" if key == "backspace" else LABEL.get(key, key)  # (⌫ isn't in most fonts)
        tw, th = d.textbbox((0, 0), label, font=font)[2:]
        d.text(((box[0] + box[2] - tw) / 2, (box[1] + box[3] - th) / 2), label, fill=(48, 52, 60), font=font)
    BUILTIN_SKIN.parent.mkdir(parents=True, exist_ok=True)
    im.save(BUILTIN_SKIN)
    return BUILTIN_SKIN


# NUMPLAY_WINDOW_MEMORY_V1
WINDOW_SETTINGS_FILE = Path.home() / ".numplay-emulator" / "settings.json"


def _window_settings_read():
    """Use the same JSON as Dark mode; tolerate an absent or damaged file."""
    try:
        settings = json.loads(WINDOW_SETTINGS_FILE.read_text(encoding="utf-8"))
        return settings if isinstance(settings, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _window_settings_save(placement):
    """Preserve unrelated preferences (especially the theme)."""
    settings = _window_settings_read()
    settings["window"] = dict(placement)
    WINDOW_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = WINDOW_SETTINGS_FILE.with_suffix(".window-tmp")
    try:
        tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, WINDOW_SETTINGS_FILE)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _window_workareas(root):
    """Visible desktop work areas. Windows includes secondary monitors on the left."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class RECT(ctypes.Structure):
                _fields_ = [(name, ctypes.c_long) for name in ("left", "top", "right", "bottom")]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                            ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR,
                                                wintypes.HDC, ctypes.POINTER(RECT),
                                                wintypes.LPARAM)
            areas = []

            def each_monitor(handle, _hdc, _rect, _data):
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                if ctypes.windll.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                    rc = info.rcWork
                    areas.append((int(rc.left), int(rc.top), int(rc.right), int(rc.bottom)))
                return True

            ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_type(each_monitor), 0)
            if areas:
                return areas
        except (AttributeError, OSError, ValueError):
            pass
    return [(0, 0, max(1, root.winfo_screenwidth()),
             max(1, root.winfo_screenheight()))]


def _window_safe_geometry(placement, workareas):
    """Keep a saved window reachable after a monitor is disconnected."""
    if not isinstance(placement, dict):
        return None
    values = {}
    for key in ("width", "height", "x", "y"):
        value = placement.get(key)
        if type(value) is not int or abs(value) > 100000:
            return None
        values[key] = value
    w, h = values["width"], values["height"]
    x, y = values["x"], values["y"]
    if not (400 <= w <= 12000 and 250 <= h <= 12000):
        return None
    if not workareas:
        return None

    def intersection(area):
        l, t, r, b = area
        return max(0, min(x+w, r)-max(x, l)) * max(0, min(y+h, b)-max(y, t))

    area = max(workareas, key=intersection)
    if intersection(area) < 120 * 45:
        # The saved display is gone; centre the window on the primary display.
        area = workareas[0]
        l, t, r, b = area
        w = min(w, max(770, r-l))
        h = min(h, max(510, b-t))
        x = l + max(0, (r-l-w)//2)
        y = t + max(0, (b-t-h)//2)
    else:
        l, t, r, b = area
        w = min(w, max(770, r-l))
        h = min(h, max(510, b-t))
        # Keep the title bar and enough of the window visible to grab/resize.
        x = max(l- w + 160, min(x, r-160))
        y = max(t, min(y, b-65))
    return (w, h, x, y, bool(placement.get("maximized", False)))


def _window_move_absolute_on_windows(root, x, y):
    """Tk's negative geometry offsets mean 'from right/bottom', not screen X/Y.

    Move the outer window frame with Win32 when a second monitor sits at X<0.
    For normal positive coordinates Tk's own geometry is sufficient.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        handle = int(root.wm_frame(), 0)
        SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
        ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(handle), None,
                                           int(x), int(y), 0, 0,
                                           SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
    except (AttributeError, OSError, ValueError, TypeError):
        pass  # Tk still restores the chosen width and height.

class CalculatorSkinApp(LiveApp):
    """NumWorks skin with mutually exclusive file and live-development modes.

    Only this GUI runs the watcher; the existing Makefiles/build script and
    emu.Calculator backend are deliberately untouched.
    """

    def __init__(self, root, initial, nwlink, model, system, auto_follow,
                 art_path: Path, max_sleep_ms: int = 0,
                 profile_speed: bool = False, start_live: bool = False):
        self.art_path = art_path
        self.art = Image.open(art_path).convert("RGB")
        self._theme_art_original = self.art.copy()
        self.art_px = self.art.size
        self._canvas_image = None
        self._canvas_screen = None
        self._bg_photo = None
        self._art_geometry = (0, 0, 0, 0)
        self._hover = None
        self._last_canvas_size = None
        self.fullscreen = False
        self.f11_down = False
        self.rate_label = tk.StringVar(master=root, value="Emulator ready")
        # Display choice and build choice are independent: neither view switch
        # restarts Unicorn nor starts/stops the compiler.
        self.view_var = tk.StringVar(master=root, value="calculator")
        self._native_geometry = None
        self.mode_var = tk.StringVar(master=root, value="live" if start_live else "file")
        self.build_status = tk.StringVar(master=root, value="Choose a .nwa to play")
        self.build_detail = tk.StringVar(master=root, value="No compiler process running")
        self.build_process = None
        self._builder_generation = 0
        self._builder_events = queue.Queue()
        self._builder_log = deque(maxlen=850)
        self._build_log_window = None
        self._build_log_text = None
        self._builder_has_error = False
        self._builder_ready = False
        self._builder_busy = False
        self._builder_stopping = False
        self._simple_mode_ui = True
        # A physical USB flash must never be triggered by auto-build/reload.
        self._upload_running = False
        self._upload_events = queue.Queue()
        self._upload_log = deque(maxlen=120)
        self._upload_start_wall: float | None = None
        self._upload_progress_is_reported = False
        self._upload_log_window = None
        self._upload_log_text = None
        self.upload_status = tk.StringVar(master=root, value="USB upload: idle")
        self.upload_activity = tk.StringVar(master=root, value="")
        self.upload_elapsed = tk.StringVar(master=root, value="")
        super().__init__(root, initial, nwlink, model, system, False,
                         max_sleep_ms=max_sleep_ms, profile_speed=profile_speed)
        # Avoid the original fallback's ambiguous 'newest local file' behavior.
        self.watch_var.set(False)
        self.follow_var.set(False)
        self._builder_poll_id = self.root.after(120, self._poll_builder)
        self._upload_poll_id = self.root.after(160, self._poll_upload)
        self._apply_interface_theme(initial=True)
        self._restore_window_placement()
        self.root.bind("<Configure>", self._remember_window_geometry, add="+")
        if start_live:
            self.root.after(260, self._start_builder)

    # NUMPLAY_WINDOW_MEMORY_V1: restore on launch, save only on an actual close.
    def _restore_window_placement(self):
        saved = _window_settings_read().get("window")
        placement = _window_safe_geometry(saved, _window_workareas(self.root))
        self._window_last_normal = None
        self._window_restoring = False
        if placement is None:
            return
        w, h, x, y, maximized = placement
        self._window_last_normal = (w, h, x, y)
        self._window_restoring = True
        # On Windows negative X means a display left of the primary one.
        # Always position the actual Win32 frame, rather than interpreting
        # negative X as a distance measured from the right screen edge.
        if os.name == "nt":
            self.root.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
            self.root.after(45, lambda: _window_move_absolute_on_windows(self.root, x, y))
        else:
            self.root.geometry(f"{w}x{h}{x:+d}{y:+d}")
        if maximized:
            self.root.after(110, self._restore_window_maximized)
        self.root.after(200, self._finish_window_restoring)

    def _restore_window_maximized(self):
        try:
            self.root.state("zoomed")
        except tk.TclError:
            try:
                self.root.attributes("-zoomed", True)
            except tk.TclError:
                pass

    def _finish_window_restoring(self):
        self._window_restoring = False

    def _remember_window_geometry(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        if getattr(self, "_window_restoring", False) or self.fullscreen:
            return
        try:
            if self.root.state() != "normal":
                return
            w, h = self.root.winfo_width(), self.root.winfo_height()
            x, y = self.root.winfo_x(), self.root.winfo_y()
            if w >= 400 and h >= 250:
                self._window_last_normal = (w, h, x, y)
        except tk.TclError:
            pass

    def _save_window_placement(self):
        try:
            state = self.root.state()
            maximized = state == "zoomed"
            if self.fullscreen:
                maximized = False  # Fullscreen is intentionally not persistent.
            if state == "normal" and not self.fullscreen:
                self._remember_window_geometry()
            geom = self._window_last_normal
            if geom is None:
                return
            w, h, x, y = geom
            _window_settings_save({"width": w, "height": h, "x": x, "y": y,
                                   "maximized": maximized})
        except (OSError, ValueError, tk.TclError) as exc:
            print(f"[NumPlay] Could not save window placement: {exc}", file=sys.stderr)

    def _build_ui(self):
        self.root.title("NumPlay emulator")
        self.root.geometry("1050x800")
        self.root.minsize(770, 510)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=8, pady=8)
        sidebar = tk.Frame(body, bg=PANEL, width=310)
        sidebar.pack(side="right", fill="y", padx=(8, 0))
        sidebar.pack_propagate(False)
        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(left, bg=BG, bd=0, highlightthickness=0, takefocus=1)
        self.canvas.pack(fill="both", expand=True)
        self.screen_label = self.canvas
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind("<ButtonPress-1>", self._mouse_press)
        self.canvas.bind("<Motion>", self._mouse_move)
        self.canvas.bind("<Leave>", self._mouse_leave)

        tk.Frame(body, bg=LINE, width=1).pack(side="right", fill="y")   # (the panel's edge)
        inner = tk.Frame(sidebar, bg=PANEL)
        inner.pack(fill="both", expand=True, padx=18, pady=(16, 10))
        titlebar = tk.Frame(inner, bg=PANEL)
        titlebar.pack(fill="x")
        tk.Label(titlebar, text="NumPlay emulator", bg=PANEL, fg=FG, font=font(13, "bold"),
                 anchor="w").pack(side="left")
        options_button = tk.Menubutton(titlebar, text="Options", bg=PANEL, fg=FG, relief="flat", bd=0,
                                       font=font(10), cursor="hand2", activebackground=PANEL_2,
                                       activeforeground=FG)
        options_button.pack(side="right")
        self._options_menu = tk.Menu(options_button, tearoff=0)
        self.dark_mode_var = tk.BooleanVar(master=self.root, value=(CURRENT_THEME == "dark"))
        self._options_menu.add_checkbutton(label="Dark mode", variable=self.dark_mode_var,
                                           command=self._toggle_dark_mode)
        self._options_menu.add_separator()
        options_button.configure(menu=self._options_menu)
        self._options_menu.add_command(label="Screenshot", command=self._focus_after(self.screenshot))
        self._options_menu.add_command(label="Keyboard shortcuts", command=self._focus_after(self.show_keymap))
        self._options_menu.add_command(label="Full screen (F11)", command=self._focus_after(self.toggle_fullscreen))
        self._options_menu.add_separator()
        self._options_menu.add_command(label="Build log", command=self._show_build_log)
        self._options_menu.add_command(label="USB log", command=self._show_upload_log)
        self._options_menu.add_command(label="Open the built files", command=self._open_dist)
        self._options_menu.add_command(label="NumWorks web installer",
                                       command=lambda: webbrowser.open("https://my.numworks.com/apps"))
        self._options_menu.add_separator()
        self.auto_clock_var = tk.BooleanVar(master=self.root, value=self.auto_clock[0])
        self.clock_option = tk.StringVar(master=self.root, value=(
            "550 MHz  (N0120 calibrated)" if self.model == "n0120"
            else "216 MHz  (N0110 / N0115)"))
        clock_submenu = tk.Menu(self._options_menu, tearoff=0)
        clock_submenu.add_checkbutton(label="Follow this computer's speed (less exact)",
                                      variable=self.auto_clock_var, command=self.toggle_auto_clock)
        clock_submenu.add_separator()
        self._manual_clock_menu = tk.Menu(clock_submenu, tearoff=0)
        for clock in ("550 MHz  (N0120 calibrated)", "216 MHz  (N0110 / N0115)",
                      "108 MHz", "54 MHz", "27 MHz"):
            self._manual_clock_menu.add_radiobutton(label=clock, variable=self.clock_option,
                                                    value=clock, command=lambda value=clock: self.change_clock(value))
        clock_submenu.add_cascade(label="Clock", menu=self._manual_clock_menu, state="normal")
        self._clock_submenu = clock_submenu
        self._options_menu.add_cascade(label="Processor", menu=clock_submenu)
        if self.art_path != BUILTIN_SKIN:
            self._options_menu.add_command(label="Calculator picture license",
                                           command=lambda: webbrowser.open(ART_LICENSE))

        self._section(inner, "View")
        self._segmented(inner, self.view_var, (("Calculator", "calculator"), ("Screen only", "screen")),
                        self._switch_view)
        self.view_detail = tk.Label(inner, text="", bg=PANEL, fg=MUTED, font=font(9), anchor="w")
        self.view_detail.pack(fill="x", pady=(4, 0))

        self._section(inner, "Run")
        self._segmented(inner, self.mode_var, (("One file", "file"), ("Live", "live")), self._switch_mode)

        # the two modes' panels, one at a time
        self.file_panel = tk.Frame(inner, bg=PANEL)
        self.file_panel.pack(fill="x", pady=(10, 0))
        self.pick_button = self._button(self.file_panel, "Open a .nwa", self.open_file, primary=True)
        self.pick_button.pack(fill="x")
        tk.Label(self.file_panel, text="Runs that file. Open it again after building.", bg=PANEL, fg=MUTED,
                 font=font(9), wraplength=270, anchor="w", justify="left").pack(fill="x", pady=(6, 0))

        self.live_panel = tk.Frame(inner, bg=PANEL)
        self.live_button = self._button(self.live_panel, "Start live build", self._toggle_builder, primary=True)
        self.live_button.pack(fill="x", pady=(10, 0))
        tk.Label(self.live_panel, text="Builds when you save a file, then reloads.", bg=PANEL, fg=MUTED,
                 font=font(9), wraplength=270, anchor="w", justify="left").pack(fill="x", pady=(6, 0))

        self.build_info = tk.Frame(inner, bg=PANEL_2, padx=10, pady=8)
        self.build_info.pack(fill="x", pady=(10, 0))
        tk.Label(self.build_info, textvariable=self.build_status, bg=PANEL_2, fg=FG, font=font(10, "bold"),
                 anchor="w", justify="left", wraplength=260).pack(fill="x")
        tk.Label(self.build_info, textvariable=self.build_detail, bg=PANEL_2, fg=MUTED, font=font(9),
                 anchor="w", justify="left", wraplength=260).pack(fill="x", pady=(2, 0))
        self.progress = ttk.Progressbar(self.build_info, mode="indeterminate")

        self.running_header = self._section(inner, "App")
        self.file_title = tk.Label(inner, text="No file open", bg=PANEL, fg=FG, font=font(11, "bold"),
                                   anchor="w", wraplength=270, justify="left")
        self.file_title.pack(fill="x")
        tk.Label(inner, textvariable=self.file_label, bg=PANEL, fg=MUTED, font=font(8), anchor="w",
                 wraplength=270, justify="left").pack(fill="x", pady=(2, 8))
        action_panel = tk.Frame(inner, bg=PANEL)
        action_panel.pack(fill="x")
        for col in (0, 1):
            action_panel.grid_columnconfigure(col, weight=1, uniform="actions")
        self.reload_button = self._button(action_panel, "Restart", self.reset)
        self.reload_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.pause_button = self._button(action_panel, "Pause", self.toggle_pause)
        self.pause_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        # sending to a real calculator: only when asked, never part of the live builds
        self.upload_button = self._button(inner, "Send to the calculator", self._push_current_build)
        self.upload_button.pack(fill="x", pady=(8, 0))
        self.upload_status_label = tk.Label(inner, textvariable=self.upload_status, bg=PANEL, fg=MUTED,
                                            font=font(9), wraplength=270, justify="left", anchor="w")
        self.upload_status_label.pack(fill="x", pady=(4, 0))
        # (nwlink's own percentage when it gives one, else a bar that only says it is still running)
        self.upload_progress_panel = tk.Frame(inner, bg=PANEL)
        self.upload_progress = ttk.Progressbar(self.upload_progress_panel, mode="indeterminate", maximum=100)
        self.upload_progress.pack(fill="x", pady=(5, 3))
        tk.Label(self.upload_progress_panel, textvariable=self.upload_elapsed, bg=PANEL, fg=MUTED,
                 font=font(8), anchor="w").pack(fill="x")
        tk.Label(self.upload_progress_panel, textvariable=self.upload_activity, bg=PANEL, fg=MUTED,
                 font=font(8), anchor="w", justify="left", wraplength=265).pack(fill="x", pady=(2, 0))

        self._section(inner, "Emulator")
        for var in (self.rate_label, self.input_status, self.time_label):
            tk.Label(inner, textvariable=var, bg=PANEL, fg=MUTED, font=font(9), anchor="w").pack(fill="x")

        tk.Frame(inner, bg=PANEL).pack(fill="both", expand=True)
        self.drop_line = tk.Label(inner, text="Drop a .nwa on the calculator to open it", bg=PANEL, fg=MUTED,
                                  font=font(8), wraplength=270, justify="left", anchor="w")
        self.drop_line.pack(fill="x")
        tk.Frame(sidebar, bg=LINE, height=1).pack(fill="x", side="bottom")
        tk.Label(sidebar, textvariable=self.status, bg=PANEL, fg=MUTED, anchor="w", justify="left",
                 wraplength=282, padx=18, pady=8, font=font(9)).pack(fill="x", side="bottom")
        self._configure_mode_panel()
        self._update_upload_controls()
        self.root.focus_force()
        self.root.after(150, self.refocus_input)

    # NUMPLAY_DARK_MODE_V1: immediate, persistent user-selected interface theme.
    def _toggle_dark_mode(self):
        requested = "dark" if self.dark_mode_var.get() else "light"
        self._apply_interface_theme(requested)
        try:
            _save_theme_preference(requested)
        except OSError as exc:
            messagebox.showwarning("NumPlay theme",
                                   f"Theme changed, but could not save it for next time:\n{exc}",
                                   parent=self.root)
        self.set_status("Dark mode" if requested == "dark" else "Light mode")
        self.root.after(75, self.refocus_input)

    def _apply_interface_theme(self, requested=None, *, initial=False):
        """Repaint Tk widgets, including log windows, without restarting Unicorn.

        Colours are replaced by their palette values rather than rebuilding
        the window: doing so would interrupt the active calculator/game and
        live builder. FlatButton stores its own background for mouse hovering.
        """
        previous = THEME_PALETTES[CURRENT_THEME]
        if requested is not None:
            _activate_theme_palette(requested)
        updated = THEME_PALETTES[CURRENT_THEME]
        mapping = {old.lower(): updated[key]
                   for key, old in previous.items()}
        tk_options = (
            "background", "foreground", "activebackground", "activeforeground",
            "selectcolor", "disabledforeground", "highlightbackground",
            "highlightcolor", "insertbackground", "troughcolor",
        )
        visited = set()

        def paint(widget):
            # Tk menus and Toplevels can appear elsewhere in the widget tree.
            if str(widget) in visited:
                return
            visited.add(str(widget))
            try:
                options = set(widget.keys())
                changes = {}
                for option in tk_options:
                    if option not in options:
                        continue
                    value = str(widget.cget(option)).lower()
                    if value in mapping and mapping[value] != value:
                        changes[option] = mapping[value]
                if changes:
                    widget.configure(**changes)
                if isinstance(widget, FlatButton):
                    # FlatButton remembers the hover/leave base colour.
                    primary = widget._bg.lower() == previous["ACCENT"].lower()
                    widget._bg = ACCENT if primary else PANEL_2
                    widget.configure(bg=widget._bg,
                                     fg="#34343a" if primary else FG)
                for child in widget.winfo_children():
                    paint(child)
            except tk.TclError:
                pass

        paint(self.root)
        self.root.configure(bg=BG)
        for menu in (self._options_menu, self._clock_submenu, self._manual_clock_menu):
            try:
                menu.configure(bg=PANEL, fg=FG,
                               activebackground=SELECT, activeforeground=FG,
                               selectcolor=ACCENT)
            except (tk.TclError, AttributeError):
                pass  # Native system menus may ignore these colours on macOS.

        # ttk progress bars use the system theme unless styled explicitly.
        try:
            style = ttk.Style(self.root)
            style.configure("NumPlay.Horizontal.TProgressbar",
                            background=ACCENT, troughcolor=PANEL_2, borderwidth=0)
            for bar in (self.progress, self.upload_progress):
                bar.configure(style="NumPlay.Horizontal.TProgressbar")
        except (tk.TclError, AttributeError):
            pass

        # Mason's segmented labels have their own selection-colour closure.
        # Re-running the existing variable trace repaints them without calling
        # the mode-switch commands or restarting the compiler/emulator.
        for var in (self.view_var, self.mode_var):
            var.set(var.get())
        # Make the large calculator illustration comfortable at night too.
        # Only the static shell image is dimmed; the actual 320x240 LCD pixels
        # are always presented at their exact original brightness and colours.
        self.art = (ImageEnhance.Brightness(self._theme_art_original).enhance(0.54)
                    if CURRENT_THEME == "dark" else self._theme_art_original)
        self._art_geometry = (0, 0, 0, 0)
        try:
            self._resize()
            self.canvas.itemconfigure("native-border", outline=LINE)
            self._draw_highlight()
        except (tk.TclError, AttributeError):
            pass

    def _section(self, parent, title):
        label = tk.Label(parent, text=title, bg=PANEL, fg=MUTED, font=font(9), anchor="w")
        label.pack(fill="x", pady=(16, 6))
        return label

    def _segmented(self, parent, var, choices, command):
        """Two or three choices side by side, the chosen one shaded (radio buttons can't be coloured on a Mac)."""
        row = tk.Frame(parent, bg=LINE)
        row.pack(fill="x")
        cells = []

        def paint(*_):
            for cell, value in cells:
                on = var.get() == value
                cell.configure(bg=SELECT if on else PANEL, font=font(10, "bold" if on else "normal"))

        for i, (text, value) in enumerate(choices):
            row.grid_columnconfigure(i, weight=1, uniform="seg")
            cell = tk.Label(row, text=text, fg=FG, pady=7, cursor="hand2")
            cell.grid(row=0, column=i, sticky="ew", padx=(1 if i == 0 else 0, 1), pady=1)

            def choose(_e, value=value):
                var.set(value)
                command()
                self.root.after(75, self.refocus_input)

            cell.bind("<ButtonRelease-1>", choose)
            cells.append((cell, value))
        var.trace_add("write", paint)
        paint()
        return row

    def _button(self, parent, label, callback, primary=False):
        return FlatButton(parent, label, self._focus_after(callback), primary=primary)

    def _focus_after(self, action):
        def call():
            try:
                action()
            finally:
                self.root.after(75, self.refocus_input)
        return call

    def _configure_mode_panel(self):
        if self.mode_var.get() == "live":
            self.file_panel.pack_forget()
            self.live_panel.pack(before=self.running_header, fill="x")
            self.build_info.pack(before=self.running_header, fill="x", pady=(10, 0))
            self.drop_line.configure(text="Save a file to build again. Dropping a .nwa opens it on its own.")
        else:
            self.live_panel.pack_forget()
            self.build_info.pack_forget()
            self.file_panel.pack(before=self.running_header, fill="x", pady=(10, 0))
            self.drop_line.configure(text="Drop a .nwa on the calculator to open it")

    def _switch_mode(self):
        self._configure_mode_panel()
        self.watch_candidate = None
        self.follow_var.set(False)
        if self.mode_var.get() == "live":
            self.watch_var.set(True)
            self._start_builder()
        else:
            self.watch_var.set(False)
            self._stop_builder()
            self._set_build_state("Single file mode", "No background compile or auto-reload")
        self._update_upload_controls()
        self.root.after(75, self.refocus_input)

    def open_file(self):
        if self.mode_var.get() != "file":
            self.mode_var.set("file")
            self._switch_mode()
        # Calling the existing picker preserves file handling and focus.
        super().open_file()

    def on_drop(self, event):
        if self.mode_var.get() != "file":
            self.mode_var.set("file")
            self._switch_mode()
        super().on_drop(event)

    def watch(self):
        """Never search for a vague newest build or reload single-file mode."""
        if self._closing:
            return
        if (self.mode_var.get() == "live" and self.watch_var.get() and
                self._builder_ready and self.current == LIVE_NWA):
            try:
                self._maybe_reload(LIVE_NWA)
            except OSError as exc:
                self.build_status.set("Live reload check failed")
                self.build_detail.set(str(exc)[:140])
        self._watch_id = self.root.after(WATCH_MS, self.watch)

    def _set_build_state(self, status, detail="", *, busy=False, error=False):
        self.build_status.set(status)
        self.build_detail.set(detail)
        if error:
            self.build_info.configure(bg=ERROR_BG)
            for child in self.build_info.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=ERROR_BG)
        else:
            self.build_info.configure(bg=PANEL_2)
            for child in self.build_info.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=PANEL_2)
        if busy != self._builder_busy:
            self._builder_busy = busy
            if busy:
                self.progress.pack(fill="x", pady=(9, 1))
                self.progress.start(11)
            else:
                self.progress.stop()
                self.progress.pack_forget()

    def _toggle_builder(self):
        if self.build_process and self.build_process.poll() is None:
            self._stop_builder()
            self._set_build_state("Watcher stopped", "Click Start live build to resume")
        else:
            self._start_builder()

    def _start_builder(self):
        if self._closing or self.mode_var.get() != "live":
            return
        if self.build_process is not None and self.build_process.poll() is None:
            return
        if not BUILD_SCRIPT.is_file():
            self._set_build_state("Builder not found", f"Expected tools/{BUILD_SCRIPT.name}", error=True)
            messagebox.showerror("Live build", f"Missing build script:\n{BUILD_SCRIPT}")
            return
        # A previous process may still be shutting down. Never run concurrent
        # builds of the same directory.
        if self._builder_stopping:
            self.root.after(200, self._start_builder)
            return
        self._builder_generation += 1
        generation = self._builder_generation
        self._builder_ready = False
        self._builder_has_error = False
        self._builder_log.clear()
        self.watch_candidate = None
        self.watch_var.set(True)
        cmd = [sys.executable, "-u", str(BUILD_SCRIPT), "--watch",
               "--launcher-only", "--skip-languages", "-j2"]
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            self.build_process = subprocess.Popen(cmd, cwd=str(REPO_ROOT),
                                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                  text=True, encoding="utf-8", errors="replace",
                                                  bufsize=1, creationflags=flags,
                                                  start_new_session=(os.name != "nt"))
        except OSError as exc:
            self._set_build_state("Cannot start compiler", str(exc), error=True)
            self.build_process = None
            return
        self.live_button.configure(text="Stop live build")
        self._set_build_state("Starting the compiler", "Checking dependencies", busy=True)
        thread = threading.Thread(target=self._read_builder_output,
                                  args=(generation, self.build_process), daemon=True)
        thread.start()

    def _read_builder_output(self, generation, process):
        """Read compiler output off Tk's thread; never block the calculator UI."""
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    self._builder_events.put((generation, "line", line.rstrip("\r\n")))
        except (OSError, ValueError) as exc:
            self._builder_events.put((generation, "line", f"Compiler output error: {exc}"))
        finally:
            rc = process.wait()
            self._builder_events.put((generation, "ended", rc))

    def _stop_builder(self):
        process = self.build_process
        self._builder_generation += 1  # Ignore stale lines from a stopped watch.
        self._builder_ready = False
        self.watch_var.set(False)
        self.watch_candidate = None
        self.build_process = None
        if hasattr(self, "live_button"):
            self.live_button.configure(text="Start live build")
        self._set_build_state("Watcher stopped" if self.mode_var.get() == "live" else "Single file mode",
                              "No background compilation", busy=False)
        if process is None or process.poll() is not None:
            return
        self._builder_stopping = True
        # Start terminating the process tree *before* closing Tk. If shutdown
        # were deferred entirely to a daemon thread, the Python process could
        # exit first and leave an orphaned Windows watcher running.
        try:
            if os.name == "nt":
                subprocess.Popen(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            try:
                process.terminate()
            except OSError:
                pass
        def wait_for_stopped():
            try:
                process.wait(timeout=7)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            finally:
                self._builder_events.put((None, "stopped", None))
        threading.Thread(target=wait_for_stopped, daemon=True).start()

    def _poll_builder(self):
        if self._closing:
            return
        count = 0
        while count < 240:
            try:
                generation, kind, value = self._builder_events.get_nowait()
            except queue.Empty:
                break
            count += 1
            if kind == "stopped":
                self._builder_stopping = False
                continue
            if generation != self._builder_generation:
                continue
            if kind == "ended":
                self.live_button.configure(text="Start live build")
                self.build_process = None
                self.watch_var.set(False)
                if value != 0:
                    self._set_build_state("Compiler stopped unexpectedly",
                                          f"Exit code {value}. See Options, Build log", error=True)
                else:
                    self._set_build_state("Watcher stopped", "No longer monitoring edits")
                continue
            line = value
            self._builder_log.append(line)
            self._append_log(line)
            if "Detected edits (" in line:
                self._builder_has_error = False
                self._set_build_state("Changes detected", "Rebuilding NumPlay", busy=True)
            elif "Building:" in line:
                self._set_build_state("Compiling NumPlay", "Source files and game modules", busy=True)
            elif "Preparing temporary Windows nwlink" in line:
                self._set_build_state("Preparing compiler", "Rust and ARM tools", busy=True)
            elif "SUCCESS: organized" in line:
                self._set_build_state("Publishing compiled app", "Finishing the live .nwa", busy=True)
            elif "Stable live-emulator input:" in line:
                self._builder_has_error = False
                self._builder_ready = True
                self._set_build_state("Built", "Reloading dist/live/NumPlay.nwa", busy=False)
                if self.mode_var.get() == "live" and LIVE_NWA.is_file():
                    # Emulation runs in its own thread and will stop gracefully
                    # before loading the atomically replaced file.
                    self.load(LIVE_NWA)
            elif ("Build failed;" in line or "Initial build failed" in line or
                  "Windows build failed:" in line):
                self._builder_has_error = True
                self._set_build_state("Build failed", "The last good app stays. See Options, Build log",
                                      busy=False, error=True)
            elif "Watching for the next edit" in line or "Watching NumPlay source files" in line:
                if self._builder_has_error:
                    self._set_build_state("Build failed, watching", "Fix and save code to retry",
                                          busy=False, error=True)
                else:
                    self._set_build_state("Watching for changes", "Edit and save a game to rebuild",
                                          busy=False)
        self._update_upload_controls()
        self._builder_poll_id = self.root.after(120, self._poll_builder)

    def _append_log(self, line):
        box = self._build_log_text
        if box is not None:
            try:
                box.configure(state="normal")
                box.insert("end", line + "\n")
                # Trim long-running logs to avoid unbounded Tk memory usage.
                if int(box.index("end-1c").split(".")[0]) > 870:
                    box.delete("1.0", "120.0")
                box.see("end")
                box.configure(state="disabled")
            except tk.TclError:
                self._build_log_text = None

    def _show_build_log(self):
        if self._build_log_window is not None:
            try:
                self._build_log_window.lift()
                self._build_log_window.focus_set()
                return
            except tk.TclError:
                self._build_log_window = None
        win = tk.Toplevel(self.root)
        win.title("Build log")
        win.geometry("790x480")
        win.configure(bg=BG)
        frame = tk.Frame(win, bg=BG)
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        scroll = tk.Scrollbar(frame)
        scroll.pack(side="right", fill="y")
        box = tk.Text(frame, bg=PANEL, fg=FG, relief="flat", bd=0,
                      font=mono(9), padx=10, pady=10, wrap="none",
                      yscrollcommand=scroll.set)
        box.pack(side="left", fill="both", expand=True)
        scroll.configure(command=box.yview)
        box.insert("end", "\n".join(self._builder_log) or "No compilation output yet.")
        box.configure(state="disabled")
        self._build_log_window = win
        self._build_log_text = box
        def close():
            self._build_log_window = None
            self._build_log_text = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", close)

    def _open_dist(self):
        path = REPO_ROOT / "dist"
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(path))
        else:
            self.set_status(f"Compiled files: {path}")

    def _upload_source(self):
        """Choose exactly what the user is looking at, never a random newest app."""
        if self.mode_var.get() == "live":
            if self._builder_busy or not self._builder_ready:
                return None
            return LIVE_NWA
        return self.current

    def _update_upload_controls(self):
        if not hasattr(self, "upload_button"):
            return
        source = self._upload_source()
        available = (not self._upload_running and source is not None
                     and source.is_file() and source.suffix.lower() == ".nwa")
        self.upload_button.configure(state="normal" if available else "disabled",
                                     text=("Sending to the calculator" if self._upload_running else
                                           "Send this build to the calculator" if self.mode_var.get() == "live" else
                                           "Send this .nwa to the calculator"))

    def _show_upload_log(self):
        """A small live log for USB details that nwlink prints to stdout."""
        if self._upload_log_window is not None:
            try:
                self._upload_log_window.lift()
                self._upload_log_window.focus_force()
                return
            except tk.TclError:
                self._upload_log_window = None
        win = tk.Toplevel(self.root)
        win.title("USB log")
        win.geometry("680x350")
        win.configure(bg=BG)
        frame = tk.Frame(win, bg=BG)
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        scroll = tk.Scrollbar(frame)
        scroll.pack(side="right", fill="y")
        box = tk.Text(frame, bg=PANEL, fg=FG, relief="flat", bd=0,
                      font=mono(9), padx=10, pady=10, wrap="word",
                      yscrollcommand=scroll.set)
        box.pack(side="left", fill="both", expand=True)
        scroll.configure(command=box.yview)
        box.insert("end", "\n".join(self._upload_log) or "No USB install activity yet.")
        box.configure(state="disabled")
        self._upload_log_window = win
        self._upload_log_text = box

        def close():
            self._upload_log_window = None
            self._upload_log_text = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", close)

    def _append_upload_log(self, line: str):
        self._upload_log.append(line)
        box = self._upload_log_text
        if box is not None:
            try:
                box.configure(state="normal")
                box.insert("end", line + "\n")
                box.see("end")
                box.configure(state="disabled")
            except tk.TclError:
                self._upload_log_window = None
                self._upload_log_text = None

    def _push_current_build(self):
        """Physical USB install: deliberately NEVER called by the build watcher."""
        if self._upload_running:
            return
        source = self._upload_source()
        if source is None or not source.is_file() or source.suffix.lower() != ".nwa":
            messagebox.showwarning("Push to NumWorks", "No finished .nwa is ready.\n\n"
                                   "Choose a file or finish a successful Live build first.",
                                   parent=self.root)
            return
        try:
            source = source.resolve(strict=True)
            if source.stat().st_size < 1024:
                raise ValueError("The .nwa file is unusually small")
            # Snapshot BEFORE confirmation; a concurrent compiler can publish a
            # new file without changing the exact bytes chosen for this push.
            snapshot_dir = tempfile.TemporaryDirectory(prefix="numplay-usb-push-")
            snapshot = Path(snapshot_dir.name) / source.name
            shutil.copy2(source, snapshot)
        except (OSError, ValueError) as exc:
            try:
                snapshot_dir.cleanup()
            except (NameError, OSError):
                pass
            messagebox.showerror("Push to NumWorks", f"Cannot prepare .nwa:\n{exc}",
                                 parent=self.root)
            return
        warning = (
            f"Send {source.name} ({snapshot.stat().st_size:,} bytes) to your real NumWorks?\n\n"
            "WARNING: nwlink install-nwa REPLACES the calculator's installed "
            "third-party applications; it does not just patch NumPlay. "
            "Saved application data may be affected.\n\n"
            "Back up important data and keep copies of any other apps you want to reinstall.\n\n"
            "Connect the calculator with USB and leave it on its CONNECTED screen. "
            "Close any NumWorks browser tab that is using the USB connection. "
            "Do NOT unplug the cable until installation finishes.\n\n"
            "Proceed with this potentially destructive USB installation?"
        )
        if not messagebox.askyesno("Confirm calculator installation", warning,
                                   icon="warning", parent=self.root, default="no"):
            snapshot_dir.cleanup()
            return
        self._upload_running = True
        self._upload_start_wall = time.monotonic()
        self._upload_progress_is_reported = False
        self._upload_log.clear()
        self._append_upload_log(f"Installing {source.name} ({snapshot.stat().st_size:,} bytes)")
        self.upload_status.set("Starting USB installer")
        self.upload_activity.set("Waiting for nwlink to connect to the calculator")
        self.upload_elapsed.set("Elapsed: 0 s, Keep USB connected")
        self.upload_progress.configure(mode="indeterminate")
        self.upload_progress_panel.pack(fill="x", pady=(2, 7), after=self.upload_status_label)
        self.upload_progress.start(12)
        self.set_status("Installing on physical NumWorks — do not unplug")
        self._update_upload_controls()
        threading.Thread(target=self._upload_worker,
                         args=(snapshot, snapshot_dir), daemon=True).start()

    def _upload_worker(self, snapshot: Path, snapshot_dir):
        """Run the installer off Tk's thread, STREAMING its output to the UI.

        nwlink's CLI may suppress numeric progress when stdout isn't a TTY;
        in that case the UI uses a genuine indeterminate activity bar instead
        of inventing completion percentages.
        """
        try:
            cmd = [self.nwlink, "install-nwa", str(snapshot)]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            with subprocess.Popen(cmd, cwd=str(REPO_ROOT),
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace",
                                  stdin=subprocess.DEVNULL, creationflags=flags,
                                  bufsize=1) as proc:
                self._upload_events.put(("started", None))
                if proc.stdout is not None:
                    for raw in proc.stdout:
                        # Strip ANSI sequences and control chars. Progress
                        # tools sometimes redraw a line with CR rather than NL.
                        for part in re.split(r"[\r\n]+", raw):
                            part = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", part).strip()
                            if part:
                                self._upload_events.put(("line", part[-450:]))
                returncode = proc.wait()
            self._upload_events.put(("done", returncode))
        except Exception as exc:
            self._upload_events.put(("error", str(exc)))
        finally:
            snapshot_dir.cleanup()

    def _poll_upload(self):
        if self._closing:
            return
        # A clock is honest even if nwlink doesn't print transfer progress.
        if self._upload_running and self._upload_start_wall is not None:
            elapsed = int(max(0, time.monotonic() - self._upload_start_wall))
            self.upload_elapsed.set(f"Elapsed: {elapsed} s, Keep USB connected")
        while True:
            try:
                kind, value = self._upload_events.get_nowait()
            except queue.Empty:
                break
            if kind == "started":
                self.upload_status.set("USB installer running")
                self.upload_activity.set("Connecting / transferring, Waiting for nwlink")
                continue
            if kind == "line":
                line = str(value)
                self._append_upload_log(line)
                # Honor a real percentage IF nwlink prints one. It describes
                # the CURRENT CLI operation, not necessarily whole-file upload.
                match = re.search(r"(?<!\d)(100|[1-9]?\d)(?:\.\d+)?\s*%", line)
                if match:
                    pct = int(match.group(1))
                    if not self._upload_progress_is_reported:
                        self.upload_progress.stop()
                        self.upload_progress.configure(mode="determinate", value=pct)
                        self._upload_progress_is_reported = True
                    else:
                        self.upload_progress.configure(value=pct)
                    self.upload_activity.set(f"nwlink reports {pct}% for its current operation")
                else:
                    self.upload_activity.set(f"nwlink: {line[:130]}")
                continue
            self._upload_running = False
            self._upload_start_wall = None
            self.upload_progress.stop()
            self.upload_progress_panel.pack_forget()
            if kind == "done":
                code = int(value)
                if code == 0:
                    self.upload_status.set("Installed on the calculator")
                    self.set_status("NumWorks installation reported success")
                    self._append_upload_log("nwlink exited with code 0 (success)")
                    messagebox.showinfo("NumWorks", "nwlink reported a successful upload.\n\n"
                                        "Check the calculator for the newly installed app.",
                                        parent=self.root)
                else:
                    self.upload_status.set(f"USB install failed (exit {code})")
                    self._append_upload_log(f"nwlink exited with code {code}")
                    self._upload_failure("\n".join(list(self._upload_log)[-14:]) or
                                         f"nwlink exited with code {code}")
            elif kind == "error":
                self.upload_status.set("USB install could not start")
                self._append_upload_log(f"Error: {value}")
                self._upload_failure(str(value))
            self._update_upload_controls()
        self._upload_poll_id = self.root.after(160, self._poll_upload)

    def _upload_failure(self, detail):
        self.set_status("USB upload failed — current .nwa is safe on disk")
        msg = ("The USB installer reported a problem:\n\n" + detail[-1700:] +
               "\n\nCheck the cable and calculator connection. "
               "The NumWorks website is an alternative.\n\n"
               "Open https://my.numworks.com/apps now?")
        if messagebox.askyesno("USB upload failed", msg, parent=self.root):
            webbrowser.open("https://my.numworks.com/apps")

    def on_close(self):
        if self._upload_running:
            messagebox.showwarning("USB installation in progress",
                                   "Please let nwlink finish before closing the emulator. "
                                   "Do NOT unplug the NumWorks during installation.",
                                   parent=self.root)
            return
        self._save_window_placement()
        try:
            self.root.after_cancel(self._upload_poll_id)
        except (tk.TclError, AttributeError):
            pass
        self._stop_builder()
        try:
            self.root.after_cancel(self._builder_poll_id)
        except (tk.TclError, AttributeError):
            pass
        super().on_close()

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)
        # Force the image to refit after the window manager changes its size.
        self.root.after(50, self._resize)

    def on_pc_down(self, event):
        if event.keysym.lower() == "f11":
            if not self.f11_down:
                self.f11_down = True
                self.toggle_fullscreen()
            return "break"
        return super().on_pc_down(event)

    def on_pc_up(self, event):
        if event.keysym.lower() == "f11":
            self.f11_down = False
            return "break"
        return super().on_pc_up(event)

    def toggle_auto_clock(self):
        self.auto_clock[0] = bool(self.auto_clock_var.get())
        self._clock_submenu.entryconfigure("Manual clock", state="disabled" if self.auto_clock[0] else "normal")
        if self.auto_clock[0]:
            # The adaptive heuristic was tuned around 216MHz. Start it there
            # to avoid a disruptive 550-to-216 jump on its first adjustment.
            self.clock_mhz[0] = 216
            self.set_status("Adaptive clock enabled; game timing may differ from hardware.")
        else:
            self.clock_mhz[0] = int(self.clock_option.get().split()[0])
            self.set_status(f"Manual CPU clock: {self.clock_mhz[0]} MHz")

    def change_clock(self, selected):
        if self.auto_clock[0]:
            return
        self.clock_mhz[0] = int(selected.split()[0])
        self.rate_label.set("Measuring real-time speed")
        self.set_status(f"Manual CPU clock set to {self.clock_mhz[0]} MHz")

    def _switch_view(self):
        """Switch the viewport only; the running game/keyboard/build are untouched."""
        screen_only = self.view_var.get() == "screen"
        self.view_detail.configure(text=("The screen alone, 320 x 240, pixel for pixel" if screen_only else
                                         "Click the keys, or use your keyboard"))
        self._hover = None
        # A held mouse key was pressed on a key that disappears in screen view.
        # Release it to avoid a stuck calculator button.
        self.on_mouse_up()
        self.canvas.configure(cursor="arrow")
        self._native_geometry = None
        self._art_geometry = (0, 0, 0, 0)
        self._canvas_screen = None
        self._canvas_image = None
        self._bg_photo = None
        self.canvas.delete("all")
        self._resize()
        self.refocus_input()

    def present_screen(self):
        self._display_screen()

    def _resize(self, event=None):
        ww = self.canvas.winfo_width()
        hh = self.canvas.winfo_height()
        if ww < 20 or hh < 20:
            return
        if self.view_var.get() == "screen":
            # A native 320x240 LCD. Its bitmap is NEVER resized, even if the
            # window is larger, smaller, or maximized. Only its position moves.
            x = (ww - LCD_WIDTH) // 2
            y = (hh - LCD_HEIGHT) // 2
            geom = (x, y)
            if geom != self._native_geometry:
                self._native_geometry = geom
                # A one-pixel surround is outside the image; the image itself
                # contains exactly 320x240 framebuffer pixels.
                self.canvas.delete("native-border")
                self.canvas.create_rectangle(x-1, y-1, x+LCD_WIDTH, y+LCD_HEIGHT,
                                             outline=LINE, width=1,
                                             tags="native-border")
                self._display_screen()
            return
        # Calculator POV: preserve the existing artwork proportions.
        ratio = self.art_px[0] / self.art_px[1]
        art_w = max(1, int(min(ww - 8, (hh - 8) * ratio)))
        art_h = max(1, int(art_w / ratio))
        x0 = (ww - art_w) // 2
        y0 = (hh - art_h) // 2
        geom = (x0, y0, art_w, art_h)
        if geom == self._art_geometry:
            return
        self._art_geometry = geom
        self._bg_photo = ImageTk.PhotoImage(self.art.resize((art_w, art_h), Image.Resampling.LANCZOS))
        self.canvas.delete("all")
        self._canvas_image = self.canvas.create_image(x0, y0, image=self._bg_photo, anchor="nw", tags="art")
        self._canvas_screen = None
        self._display_screen()
        self._draw_highlight()

    def _rect(self, rect):
        x0, y0, aw, ah = self._art_geometry
        x, y, w, h = rect
        return (round(x0 + x * aw), round(y0 + y * ah),
                max(1, round(w * aw)), max(1, round(h * ah)))

    def _display_screen(self):
        if self._screen_rgb is None:
            return
        if self.view_var.get() == "screen":
            # No PIL resize (not even NEAREST): one bitmap pixel maps to one
            # Tk image pixel. The native framebuffer must be exactly 320x240.
            if self._screen_rgb.size != (LCD_WIDTH, LCD_HEIGHT):
                raise ValueError(f"Expected 320x240 LCD; got {self._screen_rgb.size}")
            if self._native_geometry is None:
                return
            x, y = self._native_geometry
            photo = ImageTk.PhotoImage(self._screen_rgb)
        else:
            x, y, w, h = self._rect(SCREEN)
            photo = ImageTk.PhotoImage(self._screen_rgb.resize((w, h), Image.Resampling.NEAREST))
        self._photo = photo
        if self._canvas_screen is None or not self.canvas.find_withtag("screen"):
            self._canvas_screen = self.canvas.create_image(x, y, image=photo, anchor="nw", tags="screen")
        else:
            self.canvas.itemconfigure(self._canvas_screen, image=photo)
            self.canvas.coords(self._canvas_screen, x, y)

    def _key_at(self, x, y) -> str | None:
        if self.view_var.get() == "screen" or self._art_geometry[2] == 0:
            return None
        # A few nav keys' rectangular touch regions overlap: choose the closest
        # *center* of a containing key, consistent with the visible button art.
        matched = []
        for key, bounds in HOTSPOTS.items():
            bx, by, bw, bh = self._rect(bounds)
            if bx <= x <= bx + bw and by <= y <= by + bh:
                center_distance = ((x - bx - bw / 2) / bw) ** 2 + ((y - by - bh / 2) / bh) ** 2
                matched.append((center_distance, key))
        return min(matched)[1] if matched else None

    def _draw_highlight(self):
        self.canvas.delete("key-light")
        if self.view_var.get() == "screen" or not self._art_geometry[2]:
            return
        # V12 only drew the hovered/mouse-clicked key, so PC arrow presses
        # were invisible unless the mouse happened to hover over that button.
        # Paint *all* held keyboard/mouse buttons; combinations are supported.
        held = set(self.pressed_pc.values())
        if self.mouse_pressed:
            held.add(self.mouse_pressed)
        for key in sorted(held):
            if key not in HOTSPOTS:
                continue
            x, y, w, h = self._rect(HOTSPOTS[key])
            self.canvas.create_oval(x + 2, y + 2, x + w - 2, y + h - 2,
                                    outline=ACCENT, width=3, tags="key-light")
        # Hover is a subtle white outline only when the key is not held.
        if self._hover and self._hover in HOTSPOTS and self._hover not in held:
            x, y, w, h = self._rect(HOTSPOTS[self._hover])
            self.canvas.create_oval(x + 2, y + 2, x + w - 2, y + h - 2,
                                    outline="#ffffff", width=2, tags="key-light")
        self.canvas.tag_raise("key-light")

    def _mouse_press(self, event):
        # Crucial on Windows: clicking the calculator after using a sidebar
        # button must direct the NEXT real keyboard press to the calculator.
        self.canvas.focus_set()
        key = self._key_at(event.x, event.y)
        if key:
            self.on_mouse_down(key)
            return "break"
        return None

    def _mouse_move(self, event):
        self._hover = self._key_at(event.x, event.y)
        self.canvas.configure(cursor="hand2" if self._hover else "arrow")
        self._draw_highlight()

    def _mouse_leave(self, event):
        self._hover = None
        self._draw_highlight()

    def refresh_pressed(self):
        names = set(self.pressed_pc.values())
        if self.mouse_pressed is not None:
            names.add(self.mouse_pressed)
        with self.pressed_lock:
            self.pressed.clear()
            self.pressed.update(names)
        if hasattr(self, "canvas"):
            self._draw_highlight()

    def poll(self):
        if self._closing:
            return
        newest_frame = None
        last_status = None
        latest_pace = None
        ended = None
        error = None
        while True:
            try:
                m = self.messages.get_nowait()
            except queue.Empty:
                break
            if m[0] == "frame":
                newest_frame = m
            elif m[0] == "status":
                last_status = m[1]
            elif m[0] == "pace":
                latest_pace = m
            elif m[0] == "ended":
                ended = m[1]
            elif m[0] == "error":
                error = m[1]
        if last_status and not self.powered_off:
            self.set_status(last_status)
        if latest_pace and not self.powered_off:
            _, factor, mhz, auto = latest_pace
            self.rate_label.set(f"Speed {factor:.2f}x, CPU: {mhz} MHz" +
                                (" (auto)" if auto else " (manual)"))
        if self.current:
            self.file_title.configure(text=self.current.name)
        if (self.worker is not None and self.worker.is_alive() and
            not self.powered_off and not self.pause_event.is_set() and
            time.monotonic() - self._last_frame_wall > 3.0):
            phase = self.worker.phase
            age = time.monotonic() - self.worker.phase_since_wall
            if phase == "running ARM CPU":
                self.rate_label.set(f"Slow ARM emulation: {age:.1f}s in CPU step")
            elif phase == "waiting for app sleep":
                detail = self.worker.last_svc_detail
                if detail:
                    svc, where, duration = detail
                    self.rate_label.set(f"Pacing {duration:.0f}ms SVC {svc} at 0x{where:x}")
                else:
                    self.rate_label.set("Waiting for emulated app timing")
            elif phase == "waiting for emulated time":
                self.rate_label.set("Pacing virtual clock (not an ARM crash)")
            else:
                self.rate_label.set(f"Worker: {phase} ({age:.1f}s)")
            # If a real .nwa still wedges, this tells us *where*, without
            # printing private save data or flooding PowerShell each frame.
            now = time.monotonic()
            if ((self.profile_speed or (phase == "running ARM CPU" and age >= 3.0))
                    and now - self._last_stall_log_wall > 5.0):
                self._last_stall_log_wall = now
                print(f"[NumPlay emulator diagnostic] no frame for "
                      f"{now-self._last_frame_wall:.1f}s; phase={phase}; "
                      f"phase_age={age:.1f}s; virtual_ms={self.worker.last_progress_ms:.0f}; "
                      f"last_cpu_step={self.worker.last_cpu_seconds:.3f}s; "
                      f"last_virtual_step={self.worker.last_virtual_delta:.1f}ms; "
                      f"app_sleep_step={self.worker.last_sleep_delta_ms:.1f}ms; "
                      f"last_svc={self.worker.last_svc_detail}; "
                      f"CPU={self.clock_mhz[0]}MHz; auto={self.auto_clock[0]}",
                      file=sys.stderr, flush=True)
        if newest_frame and not self.powered_off and not ended:
            self._last_frame_wall = time.monotonic()
            _, raw, elapsed = newest_frame
            try:
                self._screen_rgb = rgb565_to_photo_bytes(raw)
                self.present_screen()
                self.time_label.set(f"{elapsed:,} ms (virtual)")
            except Exception as exc:
                self.set_status(f"Display error: {exc}")
        if ended and not self.powered_off:
            self.black_screen(ended)
        elif (self.worker is not None and self.worker.end_reason == "exited"
              and not self.powered_off):
            self.black_screen("Application exited — press Power or Reload to restart")
        if error and not self.powered_off:
            self.set_status("Emulator error — see dialog")
            messagebox.showerror("NumPlay emulator", error)
        if self.pending is not None and (self.worker is None or not self.worker.is_alive()):
            self.start_pending()
        self._poll_id = self.root.after(UI_POLL_MS, self.poll)


def main():
    ap = argparse.ArgumentParser(description="NumWorks-shaped .nwa emulator: single-file and live-build modes, on macOS, Linux and Windows")
    ap.add_argument("nwa", nargs="?", type=Path, help="optional .nwa path, or drop it after launching")
    ap.add_argument("--nwlink", default=nwlink_command())
    ap.add_argument("--skin-image", type=Path, help="path to extension's resources/webview/background.png")
    ap.add_argument("--model", choices=("n0120", "n0110"), default="n0120")
    ap.add_argument("--system", choices=("epsilon", "upsilon"), default="epsilon")
    ap.add_argument("--live", action="store_true", help="open Live development mode and start the build watcher inside the GUI")
    ap.add_argument("--follow-newest", action="store_true", help="legacy alias for --live (no ambiguous newest-file scanning)")
    ap.add_argument("--profile-speed", action="store_true",
                    help="print a measured ARM/render/pacing speed report every 5 seconds")
    ap.add_argument("--max-sleep-ms", type=int, default=0, metavar="MS",
                    help="EXPERIMENTAL: cap any EADK app sleep to MS milliseconds; "
                         "0 (default) preserves exact emulator behavior")
    args = ap.parse_args()
    if args.max_sleep_ms < 0 or args.max_sleep_ms > 10000:
        ap.error("--max-sleep-ms must be between 0 and 10000")
    if args.max_sleep_ms:
        print(f"[NumPlay diagnostic] EXPERIMENTAL sleep guard ON: "
              f"sleep calls over {args.max_sleep_ms}ms will be shortened. "
              "Game timing may differ from the calculator.", file=sys.stderr)
    if not args.nwlink:
        ap.error("nwlink not found. Run: npm install -g nwlink@1.0.0")
    art = args.skin_image or installed_artwork()
    if not art or not art.is_file():
        # (k-kuroguro's Numworks Simulator for VS Code gives the real calculator's picture: see ART_SOURCE)
        art = builtin_artwork()
    if os.name == "nt":
        # Avoid Windows silently bitmap-scaling the *whole* Tk window at 125%/
        # 150% display scale, which would defeat the native 1:1 LCD view.
        # Call before Tk creates its first window; keep a safe fallback.
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass
    if TkinterDnD:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
        print("Tip: pip install tkinterdnd2 for .nwa drag-and-drop.")
    start_live = args.live or args.follow_newest
    if start_live and args.nwa:
        print("[NumPlay] --live uses dist/live/NumPlay.nwa; ignoring the supplied single-file path.")
    CalculatorSkinApp(root, None if start_live else args.nwa, args.nwlink,
                      args.model, args.system, False, art,
                      max_sleep_ms=args.max_sleep_ms, profile_speed=args.profile_speed,
                      start_live=start_live)
    root.mainloop()


if __name__ == "__main__":
    main()
