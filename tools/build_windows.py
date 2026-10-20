#!/usr/bin/env python3
"""Build NumPlay .nwa files on native Windows (no WSL/Linux required).

Requirements: Git for Windows (Git Bash), GNU Make 4.4.1 for Windows, Node.js/npm,
Python, Rust/rustup and a native Windows arm-none-eabi-gcc toolchain.

Run from PowerShell in the repository root:
    python tools/build_windows.py --doctor
    python tools/build_windows.py --launcher-only --skip-languages -j2
    python tools/build_windows.py --organize-only --tidy-legacy
    python tools/build_windows.py --watch --launcher-only --skip-languages -j2

It keeps Make's intermediate outputs untouched under build/ and atomically
exports finished calculator apps into top-level dist/, including the stable
live-emulator input dist/live/NumPlay.nwa. `--watch` monitors source edits and
rebuilds incrementally, publishing only after successful builds. `--organize-only`
exports an existing successful build with no toolchain necessary.

It also finds an existing Rustup installation under the current user's
.cargo/bin even when VS Code did not refresh PATH; no permanent environment
changes are needed. The Tetris nwlink.exe bridge and Windows-specific
dependency installers are preserved unchanged. This script does not modify emulator code.
"""
from __future__ import annotations

import argparse
import hashlib
import time
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
TOOL_VERSION = "15.2.1-1.1"  # Same xPack GCC version as CI on Oct 19, 2026.
TOOL_NAME = f"xpack-arm-none-eabi-gcc-{TOOL_VERSION}-win32-x64.zip"
TOOL_URL = ("https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases/"
            f"download/v{TOOL_VERSION}/{TOOL_NAME}")
TOOL_SHA256 = "bae6a3d1667697ce750c3b13d6d26d80973ecedc2cc87bf04869e83447fd93ea"
ARM_BASE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NumPlay" / "toolchains"
OUTPUT = ROOT / "dist"
RUST_TARGET = "thumbv7em-none-eabihf"

# GnuWin32 Make 3.81 (often installed by winget) can crash with 0xc0000005
# before running any recipe on Windows 11. Prefer portable GNU Make 4.4.1.
MAKE_VERSION = "4.4.1"
MAKE_ARCHIVE = "make-4.4.1-without-guile-w32-bin.zip"
MAKE_URL = ("https://sourceforge.net/projects/ezwinports/files/"
            f"{MAKE_ARCHIVE}/download")
# Published by SourceForge and Chocolatey's verified GNU Make 4.4.1 package.
MAKE_SHA256 = "fb66a02b530f7466f6222ce53c0b602c5288e601547a034e4156a512dd895ee7"
MAKE_BASE = ARM_BASE.parent / "make"
MAKE_ROOT = MAKE_BASE / f"make-{MAKE_VERSION}"



def where(name: str) -> Path | None:
    candidate = shutil.which(name)
    # Rustup's Windows command proxies (rustc.exe, cargo.exe, etc.) may be
    # links to rustup.exe. Their invoked file name is important: rustup.exe
    # behaves differently from rustc.exe. Never resolve proxy links here.
    return Path(os.path.abspath(candidate)) if candidate else None


def ensure_rust_on_path() -> Path | None:
    """Use the current user's *installed* Windows Rust tools despite a stale PATH.

    The Rustup installer normally places cargo.exe and rustup.exe in
    %USERPROFILE%\\.cargo\\bin. VS Code's integrated terminal can inherit an
    older process environment after the user PATH was updated. Prepending the
    verified installation to this script's process PATH also makes it visible
    to Cargo and Git Bash subprocesses. This does not modify the registry or
    require administrative rights. Never replace the rustup proxy filenames
    with their resolved filesystem targets.
    """
    if os.name != "nt":
        return None
    if (where("cargo.exe") or where("cargo")) and (where("rustup.exe") or where("rustup")):
        return None
    cargo_home = Path(os.environ.get("CARGO_HOME", str(Path.home() / ".cargo"))).expanduser()
    cargo_bin = cargo_home / "bin"
    if not (cargo_bin / "rustup.exe").is_file() or not (cargo_bin / "cargo.exe").is_file():
        return None
    # PATH here is only changed inside this running Python process (and its
    # children), never for PowerShell, Windows or other apps.
    existing = os.environ.get("PATH", "")
    normalized = lambda p: os.path.normcase(os.path.normpath(p.strip('"')))
    if normalized(str(cargo_bin)) not in {normalized(p) for p in existing.split(os.pathsep) if p}:
        os.environ["PATH"] = str(cargo_bin) + os.pathsep + existing
    return cargo_bin


def git_bash() -> Path | None:
    # Never accidentally launch the WSL bash.exe from System32.
    candidates = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates += [Path(base) / "Git" / "bin" / "bash.exe",
                           Path(base) / "Programs" / "Git" / "bin" / "bash.exe"]
    g = where("git.exe") or where("git")
    if g:
        # git.exe normally lives in Git/cmd/, Git/bin/ or Git/mingw64/bin/.
        for folder in (g.parent, *list(g.parents)[:3]):
            candidates += [folder / "bin" / "bash.exe", folder / "usr" / "bin" / "bash.exe"]
    b = where("bash.exe")
    if b and "system32" not in str(b).lower():
        candidates.append(b)
    return next((x for x in candidates if x.is_file()), None)


def make_version(exe: Path) -> tuple[int, int, int] | None:
    """Probe actual GNU Make version; PATH presence alone is not enough."""
    try:
        p = subprocess.run([str(exe), "--version"], capture_output=True,
                           text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode:
        return None
    import re
    m = re.search(r"GNU Make\s+(\d+)\.(\d+)(?:\.(\d+))?", p.stdout)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def make_supported(exe: Path | None) -> bool:
    version = make_version(exe) if exe else None
    return version is not None and version >= (4, 0, 0)


def find_make() -> tuple[Path | None, str]:
    # Prefer the verified portable version even when GnuWin32 make.exe appears
    # earlier on PATH (e.g., C:\Program Files (x86)\GnuWin32\bin).
    candidates: list[Path] = []
    preferred = os.environ.get("NUMPLAY_MAKE_EXE")
    if preferred:
        candidates.append(Path(preferred).expanduser())
    candidates.extend([MAKE_ROOT / "bin" / "make.exe",
                       MAKE_ROOT / "make.exe",
                       *(list(MAKE_ROOT.rglob("make.exe")) if MAKE_ROOT.is_dir() else []),
                       Path.home() / "scoop/apps/make/current/bin/make.exe",
                       Path.home() / "scoop/shims/make.exe"])
    for name in ("make.exe", "make", "mingw32-make.exe", "mingw32-make"):
        p = where(name)
        if p:
            candidates.append(p)
    candidates.extend([
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "GnuWin32/bin/make.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GnuWin32/bin/make.exe",
    ])
    seen: set[str] = set()
    old: Path | None = None
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        version = make_version(candidate)
        if version and version >= (4, 0, 0):
            return candidate, candidate.stem
        if version and old is None:
            old = candidate
    return (old, old.stem) if old else (None, "make")


def install_make(archive_override: Path | None = None) -> Path:
    """Install verified portable Windows GNU Make; never touch system PATH."""
    if os.name != "nt":
        raise RuntimeError("The Windows Make installer must run on Windows.")
    existing = MAKE_ROOT / "bin" / "make.exe"
    if make_supported(existing):
        print("Modern GNU Make is already installed:", existing)
        return existing
    MAKE_BASE.mkdir(parents=True, exist_ok=True)
    archive = MAKE_BASE / MAKE_ARCHIVE
    if archive_override is not None:
        archive = archive_override.expanduser().resolve()
        if not archive.is_file():
            raise RuntimeError(f"GNU Make ZIP not found: {archive}")
    if not archive.is_file():
        print("Downloading portable GNU Make 4.4.1 for Windows (~400 KB):")
        print(MAKE_URL)
        # SourceForge responds with a mirror redirect. A verified SHA-256 guards
        # against a mirror returning HTML or unexpected/untrusted bytes.
        request = urllib.request.Request(MAKE_URL,
                                          headers={"User-Agent": "NumPlay-Windows-Builder/3"})
        try:
            with urllib.request.urlopen(request, timeout=90) as response, archive.open("wb") as f:
                total = 0
                while block := response.read(64 * 1024):
                    total += len(block)
                    if total > 16 * 1024 * 1024:
                        raise RuntimeError("Unexpectedly large GNU Make archive; aborted")
                    f.write(block)
        except Exception:
            archive.unlink(missing_ok=True)
            raise
    print("Verifying GNU Make archive SHA-256...")
    with archive.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    if digest != MAKE_SHA256:
        if archive_override is None:
            archive.unlink(missing_ok=True)
        raise RuntimeError("GNU Make ZIP SHA-256 mismatch (or HTML mirror page). "
                           "Try downloading the ZIP from SourceForge in your browser "
                           "and passing --make-zip PATH.")
    # Reject arbitrary path traversal as a defense in depth.
    with zipfile.ZipFile(archive) as z:
        members = z.infolist()
        if any(name.filename.startswith(("/", "\\")) or ".." in Path(name.filename).parts
               or ":" in name.filename for name in members):
            raise RuntimeError("Unexpected paths in the GNU Make ZIP")
        z.extractall(MAKE_ROOT)
    found = [p for p in MAKE_ROOT.rglob("make.exe") if make_supported(p)]
    if not found:
        raise RuntimeError("GNU Make executable was not found in the archive")
    exe = found[0]
    print("Installed portable GNU Make:", exe)
    return exe


def find_arm() -> Path | None:
    hint = os.environ.get("NUMPLAY_ARM_GCC_BIN")
    if hint:
        p = Path(hint).expanduser()
        if (p / "arm-none-eabi-gcc.exe").is_file():
            return p
    gcc = where("arm-none-eabi-gcc.exe") or where("arm-none-eabi-gcc")
    if gcc:
        return gcc.parent
    candidates = [ARM_BASE / f"xpack-arm-none-eabi-gcc-{TOOL_VERSION}" / "bin"]
    candidates += list(ARM_BASE.glob("*/bin")) if ARM_BASE.is_dir() else []
    roaming = Path(os.environ.get("APPDATA", Path.home())) / "xPacks" / "arm-none-eabi-gcc"
    candidates += list(roaming.glob("*/bin")) if roaming.is_dir() else []
    return next((p for p in candidates if (p / "arm-none-eabi-gcc.exe").is_file()), None)


def rust_target_installed() -> bool:
    if not where("rustup.exe") and not where("rustup"):
        return False
    try:
        result = subprocess.run(["rustup", "target", "list", "--installed"],
                                text=True, capture_output=True, timeout=20, check=False)
        return result.returncode == 0 and RUST_TARGET in result.stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return False


def python_packages() -> list[str]:
    missing = []
    for name, module in (("pillow", "PIL"), ("numpy", "numpy"),
                         ("zopfli", "zopfli"), ("pyelftools", "elftools")):
        try:
            __import__(module)
        except ImportError:
            missing.append(name)
    return missing


def doctor() -> bool:
    print("\nNumPlay Windows build tool check (native Windows; no WSL)")
    print("Repository:", ROOT)
    b = git_bash()
    make, _ = find_make()
    version = make_version(make) if make else None
    make_ok = make_supported(make)
    arm = find_arm()
    node = where("node.exe") or where("node")
    npx = where("npx.cmd") or where("npx")
    cargo = where("cargo.exe") or where("cargo")
    rustup = where("rustup.exe") or where("rustup")
    tools = [("Git Bash", b), ("GNU Make 4+", make if make_ok else None),
             ("ARM GCC (Windows)", arm / "arm-none-eabi-gcc.exe" if arm else None),
             ("Node.js", node), ("npx", npx), ("Cargo", cargo), ("rustup", rustup)]
    for name, value in tools:
        if name == "GNU Make 4+" and not value and make is not None:
            print(f"  OLD     {name:<18} {make} (version {'.'.join(map(str, version)) if version else 'broken'})")
        else:
            print(f"  {'OK     ' if value else 'MISSING'} {name:<18} {value or ''}")
    rt = rust_target_installed()
    print(f"  {'OK     ' if rt else 'MISSING'} Rust target         {RUST_TARGET}")
    missing = python_packages()
    print(f"  {'MISSING' if missing else 'OK     '} Python packages    {', '.join(missing) if missing else 'all installed'}")
    show_instructions = not all(v for _, v in tools) or not rt or bool(missing)
    print("\nMissing-tool instructions:" if show_instructions else "\nAll required tools detected.")
    if not b:
        print("  Git for Windows: https://git-scm.com/downloads/win (includes Git Bash; NOT Linux)")
    if not make_ok:
        print("  GNU Make 4.4.1: python tools/build_windows.py --install-make")
        print("  (Portable Windows binary; ~400 KB; no administrator privileges or PATH change)")
    if not arm:
        print("  ARM compiler: python tools/build_windows.py --install-arm")
        print("  (Downloads official xPack Windows compiler, about 320 MB, SHA-256 verified)")
    if not node or not npx:
        print("  Node.js: winget install --id OpenJS.NodeJS.LTS -e")
    if not rustup or not cargo:
        print("  Rust: https://rustup.rs/ (choose the Windows toolchain)")
    elif not rt:
        print(f"  rustup target add {RUST_TARGET}")
    if missing:
        print(f"  {sys.executable} -m pip install {' '.join(missing)}")
    ok = (os.name == "nt" and all(v for _, v in tools) and rt and not missing)
    if os.name != "nt":
        print("  This helper intentionally runs only on native Windows.")
    print("\n" + ("Ready to build!" if ok else "Complete the missing requirements above, then rerun --doctor."))
    return ok


def install_arm() -> Path:
    existing = find_arm()
    if existing:
        print("ARM GCC is already installed:", existing)
        return existing
    if os.name != "nt":
        raise RuntimeError("The Windows compiler installer must run on Windows.")
    ARM_BASE.mkdir(parents=True, exist_ok=True)
    archive = ARM_BASE / TOOL_NAME
    if not archive.is_file():
        print("Downloading the official Windows ARM compiler (~320 MB):")
        print(TOOL_URL)
        with urllib.request.urlopen(TOOL_URL, timeout=120) as response, open(archive, "wb") as out:
            total = 0
            while block := response.read(1024 * 1024):
                out.write(block)
                total += len(block)
                if total % (32 * 1024 * 1024) == 0:
                    print(f"  {total // (1024 * 1024)} MB received", flush=True)
    print("Checking the official archive SHA-256...")
    digest = hashlib.sha256()
    with archive.open("rb") as src:
        for block in iter(lambda: src.read(4 * 1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != TOOL_SHA256:
        archive.unlink(missing_ok=True)
        raise RuntimeError("ARM compiler SHA-256 mismatch: archive removed; installation aborted.")
    print("Extracting ARM compiler into", ARM_BASE)
    with zipfile.ZipFile(archive) as z:
        z.extractall(ARM_BASE)
    installed = find_arm()
    if not installed:
        raise RuntimeError("Extracted compiler not found. Check the archive layout.")
    print("ARM compiler ready:", installed)
    return installed


def ensure_build_directories(build: Path | None = None) -> tuple[Path, ...]:
    """Repair an incomplete build tree without deleting any existing outputs.

    The root Makefile creates build/arm, build/modules, etc. inside the recipe
    for the `build` directory. If build/ *already exists* (for example after
    an interrupted attempt, or after a previous failed Make invocation), Make
    skips that recipe. Parallel ARM compiler jobs then fail with an interleaved
    `can't create build/arm/*.o: No such file or directory` error.

    These directories are always safe to create ahead of Make, and the rest
    remain governed by the repo's Makefile. Creating them before starting
    any parallel build jobs also removes a directory-creation race.
    """
    build = ROOT / "build" if build is None else build
    relative = ("modules", "gen", "arm", "apps", "sim", "variant",
                "tetris", "gen-upsilon", "arm-upsilon", "lang")
    created = []
    build.mkdir(parents=True, exist_ok=True)
    for name in relative:
        folder = build / name
        if not folder.is_dir():
            folder.mkdir(parents=True, exist_ok=True)
            created.append(folder)
    return tuple(created)


# The Rust Tetris build script calls Command::new("nwlink") at build.rs:16.
# Unlike a shell, Rust's Windows process launcher does not find npm's .cmd
# shims from that extensionless name. Supply an actual nwlink.exe on PATH.
# We compile it with the Windows rustc the project already requires.
# The adapter delegates to the exact pinned nwlink package used by Make.
NWLINK_BRIDGE_RS = r"""
use std::env;
use std::process::{Command, Stdio};

fn main() {
    let mut child = Command::new("npx.cmd");
    child.args(["--yes", "--", "nwlink@1.0.0"]);
    child.args(env::args_os().skip(1));
    child.stdin(Stdio::inherit())
         .stdout(Stdio::inherit())
         .stderr(Stdio::inherit());
    match child.status() {
        Ok(status) => std::process::exit(status.code().unwrap_or(1)),
        Err(e) => {
            eprintln!("NumPlay nwlink bridge could not launch npx.cmd: {e}");
            std::process::exit(1);
        }
    }
}
"""


def create_nwlink_bridge(shims: Path) -> Path:
    """Make a *Windows* nwlink.exe for Cargo's direct process invocation.

    The source is fixed, generated locally and built with the user's own
    installed Rust compiler; it has no external dependencies or downloads.
    Both source and exe live in the temporary helper folder and are removed
    when the build ends. It never edits games/tetris/tetris/build.rs.
    """
    # Don't use Path.resolve() on ~/.cargo/bin/rustc.exe: it can resolve
    # Rustup's rustc proxy to rustup.exe. Rustup then receives --edition as
    # its own argument and fails with "unexpected argument '--edition'".
    # `rustup which rustc` instead locates the real host compiler binary.
    rustup = shutil.which("rustup.exe") or shutil.which("rustup")
    if not rustup:
        raise RuntimeError("Rustup was not found in PATH; ensure .cargo\\bin is on PATH")
    located = subprocess.run([rustup, "which", "rustc"],
                             text=True, capture_output=True, check=False)
    if located.returncode:
        raise RuntimeError("Rustup could not locate the host Rust compiler. "
                           "Try: rustup default stable\n"
                           + (located.stderr or located.stdout))
    rustc = Path(located.stdout.strip().strip('"'))
    if not rustc.is_file():
        raise RuntimeError(f"Rustup reported a missing Rust compiler: {rustc}")
    src = shims / "numplay_nwlink_bridge.rs"
    exe = shims / "nwlink.exe"
    src.write_text(NWLINK_BRIDGE_RS, encoding="utf-8", newline="\n")
    print("Preparing temporary Windows nwlink.exe bridge for Tetris...", flush=True)
    try:
        completed = subprocess.run([str(rustc), "--edition=2021",
                                    "--crate-name", "numplay_nwlink_bridge",
                                    "-C", "opt-level=0", str(src), "-o", str(exe)],
                                   cwd=ROOT, text=True, capture_output=True, check=False)
    except OSError as exc:
        raise RuntimeError(f"Could not start Rust compiler {rustc}: {exc}") from exc
    if completed.returncode or not exe.is_file():
        raise RuntimeError("Could not compile Windows nwlink bridge using rustc.\n"
                           + (completed.stderr or completed.stdout or "unknown error"))
    return exe


def run_bash(bash: Path, make: Path, make_name: str, arm: Path, jobs: int,
             *, apps: bool, check: bool, langs: bool) -> None:
    # Ensure make's directories exist even when build/ was created by an
    # interrupted earlier run. Keep all existing compiled files for a retry.
    created = ensure_build_directories()
    if created:
        print(f"Prepared {len(created)} missing build directories "
              f"(including build/arm); keeping existing build files.", flush=True)
    # Git Bash supplies sh/cp/mkdir/wc/etc., but every compiler and runtime
    # launched by it is the normal Windows executable, not a Linux program.
    with tempfile.TemporaryDirectory(prefix="numplay-win-") as td:
        shims = Path(td)
        python3 = shims / "python3"
        python3.write_text('#!/bin/sh\nexec "$NUMPLAY_PYTHON_POSIX" "$@"\n', encoding="utf-8", newline="\n")
        # 'nwlink' in Tetris's Rust build.rs is launched without a .cmd
        # extension, so give Cargo a real executable to find in PATH.
        create_nwlink_bridge(shims)
        env = os.environ.copy()
        env.update({"NUMPLAY_MAKE_BIN": str(make.parent), "NUMPLAY_ARM_BIN": str(arm),
                    "NUMPLAY_PYTHON_EXE": sys.executable, "NUMPLAY_SHIMS": str(shims)})
        prefix = r'''set -e
export NUMPLAY_PYTHON_POSIX="$(cygpath -u "$NUMPLAY_PYTHON_EXE")"
export PATH="$(cygpath -u "$NUMPLAY_SHIMS"):$(cygpath -u "$NUMPLAY_ARM_BIN"):$(cygpath -u "$NUMPLAY_MAKE_BIN"):$PATH"
chmod +x "$(cygpath -u "$NUMPLAY_SHIMS")/python3"
command -v sh >/dev/null || { echo 'Git Bash sh is unavailable'; exit 1; }
command -v arm-none-eabi-gcc >/dev/null || { echo 'ARM GCC is unavailable from Git Bash'; exit 1; }
command -v node >/dev/null || { echo 'Node.js is unavailable from Git Bash'; exit 1; }
command -v python3 >/dev/null || { echo 'Python is unavailable from Git Bash'; exit 1; }
command -v nwlink >/dev/null || { echo 'Tetris nwlink bridge is unavailable from Git Bash'; exit 1; }
'''
        common = ["NWLINK=npx --yes -- nwlink@1.0.0", "SHELL=sh"]

        def invoke(args: list[str]) -> None:
            import shlex
            command = " ".join(shlex.quote(arg) for arg in [make_name, *args, *common])
            print("\nBuilding: ", command, flush=True)
            completed = subprocess.run([str(bash), "--noprofile", "--norc", "-c", prefix + "\n" + command],
                                       cwd=ROOT, env=env, check=False)
            if completed.returncode:
                raise RuntimeError(f"GNU Make exited with code {completed.returncode} (build stopped)")

        targets = ["nwa"]
        if apps or check:
            targets.append("apps")
        if check:
            targets.append("check")
        invoke([f"-j{jobs}", *targets])
        if langs:
            invoke(["lang"])


# Publish end-user .nwa files outside Make's internal build/ directory.
# build/ is intentionally left alone: NumPlay's Makefile hard-codes B = build.
# The stable live path can later be supplied to emu_live.py for auto-reload.
LIVE_DIR = "live"
VARIANT_DIR = "variants"
GAME_DIR = "games"
LANGUAGE_DIR = "languages"
LANGUAGE_VARIANTS = ("NumPlay-French.nwa", "NumPlay-Chinese.nwa")


def destination_for(name: str, output: Path) -> Path:
    if name == "NumPlay.nwa":
        return output / name
    if name in LANGUAGE_VARIANTS:
        return output / LANGUAGE_DIR / name
    if name.startswith("NumPlay-") and name.endswith(".nwa"):
        return output / VARIANT_DIR / name
    return output / GAME_DIR / name


def atomic_copy(src: Path, dest: Path) -> None:
    """Do not expose partly written files to a future live emulator.

    Windows can temporarily block os.replace when another process has the file
    open. Keep the old export on such failures and retry briefly.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Unique temporary file in the destination directory ensures atomic rename.
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(prefix="." + dest.name + ".",
                                         suffix=".tmp", dir=dest.parent,
                                         delete=False) as temp:
            tmp_name = Path(temp.name)
            with src.open("rb") as source:
                shutil.copyfileobj(source, temp, length=1024 * 1024)
            temp.flush()
            os.fsync(temp.fileno())
        for attempt in range(10):
            try:
                os.replace(tmp_name, dest)
                tmp_name = None
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.2)
    finally:
        if tmp_name:
            tmp_name.unlink(missing_ok=True)


def collect_sources(*, apps: bool, langs: bool) -> list[Path]:
    build = ROOT / "build"
    # Never include existing dist/ files: only fresh Makefile output sources.
    sources = list(sorted(build.glob("NumPlay*.nwa")))
    if not langs:
        sources = [p for p in sources if p.name not in LANGUAGE_VARIANTS]
    if apps:
        sources.extend(sorted((build / "apps").glob("*.nwa")))
    return sources


def publish_outputs(*, apps: bool, langs: bool, output: Path = OUTPUT,
                    verbose: bool = True) -> list[Path]:
    """Organize completed binaries, leaving compiler artifacts in build/.

    If a build fails this function isn't called, so the last successful live
    binary remains untouched. No .nwa source or compiler object is deleted.
    """
    sources = collect_sources(apps=apps, langs=langs)
    if not sources:
        raise RuntimeError("No .nwa files found inside build/. Run a build first.")
    # Keep these generated binary exports out of git status without touching
    # the project's tracked root .gitignore. A lone '*' ignores this file too.
    output.mkdir(parents=True, exist_ok=True)
    ignore = output / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    published: list[Path] = []
    # Publish user-facing variants and standalone games first.
    # Publish the stable live file *last*, marking the completed build.
    main_src = None
    for src in sources:
        if src.name == "NumPlay.nwa":
            main_src = src
        dest = destination_for(src.name, output)
        atomic_copy(src, dest)
        published.append(dest)
    if main_src is not None:
        live = output / LIVE_DIR / "NumPlay.nwa"
        atomic_copy(main_src, live)
        published.append(live)
    if verbose:
        print(f"\nSUCCESS: organized {len(sources)} calculator app(s) in:", output)
        for path in published:
            print("  " + str(path.relative_to(output)))
        if main_src:
            print("\nStable live-emulator input: " + str(output / LIVE_DIR / "NumPlay.nwa"))
    return published


def safe_remove_legacy_duplicates(output: Path = OUTPUT) -> None:
    """Opt-in cleanup of only byte-identical earlier exports in build/windows-nwa.

    Do not delete anything that wasn't safely copied into dist/. No recursive
    deletion; unknown or changed files are left exactly as they were.
    """
    legacy = ROOT / "build" / "windows-nwa"
    if not legacy.is_dir():
        print("No old build/windows-nwa/ export folder to tidy.")
        return
    count = 0
    for path in sorted(legacy.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".nwa":
            print("Kept unknown legacy entry:", path)
            continue
        newer = destination_for(path.name, output)
        if not newer.is_file() or newer.stat().st_size != path.stat().st_size:
            print("Kept unmatched legacy file:", path.name)
            continue
        def sha256(p: Path) -> str:
            with p.open("rb") as f:
                return hashlib.file_digest(f, "sha256").hexdigest()
        if sha256(path) != sha256(newer):
            print("Kept changed legacy file:", path.name)
            continue
        path.unlink()
        count += 1
    if not any(legacy.iterdir()):
        legacy.rmdir()
    print(f"Tidied {count} byte-identical old .nwa export(s) from build/windows-nwa/.")


# Watch *inputs* only. Ignore intermediate compiler output, Rust target/, npm
# install directories, exported files, and emulator screenshots. Otherwise a
# rebuild could detect its own outputs and create an infinite rebuild loop.
WATCH_IGNORED_DIRS = frozenset({
    ".git", ".venv", "venv", "__pycache__", "build", "dist", "target",
    "node_modules", "output", ".idea", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".next", ".cache", ".vs", "windows-nwa",
})
WATCH_EXTS = frozenset({
    ".c", ".h", ".s", ".rs", ".py", ".json", ".toml", ".lock",
    ".mk", ".ld", ".txt", ".png", ".ppm", ".jpg", ".jpeg",
    ".svg", ".webp", ".gif", ".bin", ".ttf", ".otf", ".sh",
    ".js", ".ts", ".yml", ".yaml", ".ini", ".csv",
})
WATCH_NAMES = frozenset({"Makefile", "makefile", "GNUmakefile", "VERSION"})


def is_source_file(name: str) -> bool:
    if name.startswith(("~", ".#")) or name.endswith(("~", ".swp", ".tmp")):
        return False
    if name in WATCH_NAMES:
        return True
    if name.startswith("."):
        return False
    # .nwi/.nppm are generated by Rust's build.rs; avoid output-trigger loops.
    if name.lower().endswith((".nwi", ".nppm")):
        return False
    return Path(name).suffix.lower() in WATCH_EXTS


def source_snapshot(root: Path = ROOT) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    # Exclude the build helper while it is running; edits to it require restart.
    own = Path(__file__).resolve()
    for folder, directories, files in os.walk(root):
        directories[:] = [d for d in directories
                          if d not in WATCH_IGNORED_DIRS and
                          (not d.startswith(".") or d == ".cargo")]
        for name in files:
            if not is_source_file(name):
                continue
            p = Path(folder) / name
            if p == own:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            result[p.relative_to(root).as_posix()] = (st.st_mtime_ns, st.st_size)
    return result


def differences(a: dict, b: dict) -> list[str]:
    return sorted(k for k in (a.keys() | b.keys()) if a.get(k) != b.get(k))


def run_watch(build_once, *, interval: float = 1.0,
              settle: float = 1.0, root: Path = ROOT) -> None:
    """Debounce editor save bursts; rebuild and publish only on success.

    No extra watchdog package needed. Capture the baseline after a build so
    files generated inside source folders don't retrigger it immediately.
    """
    if interval <= 0 or settle <= 0:
        raise ValueError("watch interval and settle time must be positive")
    print("\nWatching NumPlay source files. Save an edit to rebuild; Ctrl+C to stop.")
    print("Build failures keep the previous dist/live/NumPlay.nwa untouched.\n")
    baseline = source_snapshot(root)
    pending: dict[str, tuple[int, int]] | None = None
    last_change = 0.0
    while True:
        time.sleep(interval)
        current = source_snapshot(root)
        now = time.monotonic()
        if current == baseline:
            pending = None
            continue
        if pending != current:
            pending = current
            last_change = now
            continue
        if now - last_change < settle:
            continue
        changed = differences(baseline, current)
        print("\nDetected edits ({} file(s)):".format(len(changed)), flush=True)
        for name in changed[:12]:
            print("  " + name)
        if len(changed) > 12:
            print(f"  ... plus {len(changed) - 12} more")
        try:
            build_once()
        except (OSError, RuntimeError) as exc:
            print("\nBuild failed; keeping last good dist/live/NumPlay.nwa:", exc,
                  file=sys.stderr, flush=True)
        # Rescan after build. Generated files, if any, become the new baseline.
        # A human edit made during the build may need a further save to trigger.
        baseline = source_snapshot(root)
        pending = None
        print("\nWatching for the next edit...", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build NumPlay .nwa apps on native Windows (no WSL).")
    ap.add_argument("--doctor", action="store_true", help="check prerequisites, don't build")
    ap.add_argument("--install-arm", action="store_true", help="download/verify official Windows ARM GCC")
    ap.add_argument("--install-make", action="store_true", help="download/verify portable Windows GNU Make 4.4.1")
    ap.add_argument("--make-zip", type=Path, metavar="ZIP", help="with --install-make, use a manually downloaded ZIP if SourceForge blocks Python")
    ap.add_argument("--launcher-only", action="store_true", help="build only combined NumPlay variants")
    ap.add_argument("--skip-check", action="store_true", help="skip linker/RAM checks")
    ap.add_argument("--skip-languages", action="store_true", help="skip French/Chinese variants")
    ap.add_argument("--organize-only", action="store_true", help="organize existing build outputs into dist/ without compiling")
    ap.add_argument("--tidy-legacy", action="store_true", help="remove only verified duplicate .nwa files in build/windows-nwa/")
    ap.add_argument("--watch", action="store_true", help="rebuild when source files change (Ctrl+C to stop)")
    ap.add_argument("--watch-interval", type=float, default=1.0,
                    help="seconds between source scans in watch mode (default 1.0)")
    ap.add_argument("-j", "--jobs", type=int, default=4, help="parallel make jobs (default 4)")
    a = ap.parse_args()
    rust_bin = ensure_rust_on_path()
    if rust_bin:
        print(f"Found installed Rust at {rust_bin}; using it for this command "
              "(no permanent PATH changes).", flush=True)
    if a.jobs < 1 or a.jobs > 32:
        ap.error("--jobs must be between 1 and 32")
    if a.make_zip and not a.install_make:
        ap.error("--make-zip requires --install-make")
    if a.watch_interval <= 0:
        ap.error("--watch-interval must be positive")
    if a.watch and (a.organize_only or a.doctor or a.install_arm or a.install_make):
        ap.error("--watch requires a normal build, not --doctor / --organize-only / installation")
    if a.install_make:
        try:
            install_make(a.make_zip)
        except (OSError, RuntimeError, zipfile.BadZipFile, urllib.error.URLError) as exc:
            print("GNU Make installation failed:", exc, file=sys.stderr)
            return 1
        if not a.doctor and not a.install_arm:
            print("Next: python tools/build_windows.py --doctor")
            return 0
    if a.install_arm:
        try:
            install_arm()
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            print("Compiler installation failed:", exc, file=sys.stderr)
            return 1
        if not a.doctor:
            print("Next: python tools/build_windows.py --doctor")
            return 0
    if a.doctor:
        return 0 if doctor() else 1
    if a.organize_only:
        try:
            publish_outputs(apps=True, langs=True)
            if a.tidy_legacy:
                safe_remove_legacy_duplicates()
            return 0
        except (OSError, RuntimeError) as exc:
            print("Could not organize build outputs:", exc, file=sys.stderr)
            return 1
    if not doctor():
        return 1
    make, make_name = find_make()
    assert make and make_name and make_supported(make)
    bash, arm = git_bash(), find_arm()
    assert bash and arm

    def build_once():
        run_bash(bash, make, make_name, arm, a.jobs,
                 apps=not a.launcher_only,
                 check=not a.launcher_only and not a.skip_check,
                 langs=not a.skip_languages)
        publish_outputs(apps=not a.launcher_only, langs=not a.skip_languages)
        if a.tidy_legacy:
            safe_remove_legacy_duplicates()

    if a.watch:
        try:
            # Initial build then repeat on changes; failed initial build doesn't
            # stop observation, and existing exports remain intact.
            try:
                build_once()
            except (OSError, RuntimeError) as exc:
                print("\nInitial build failed; old exported files were kept:", exc,
                      file=sys.stderr, flush=True)
            run_watch(build_once, interval=a.watch_interval)
        except KeyboardInterrupt:
            print("\nStopped watching. Compiled files remain in dist/.")
        return 0
    try:
        build_once()
        return 0
    except (OSError, RuntimeError) as exc:
        print("\nWindows build failed:", exc, file=sys.stderr)
        print("The original source files and your emulator have not been modified.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
