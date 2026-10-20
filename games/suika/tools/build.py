#!/usr/bin/env python3
"""Optional portable standalone build; the normal NumPlay build uses Make.

python tools/build.py --toolchain /path/to/arm/bin --nwlink /path/to/nwlink/bin/nwlink
Requires nwlink 1.0.0, ARM GCC, Pillow (icon generation), pyelftools (size report).
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--toolchain', type=Path)
    ap.add_argument('--nwlink', default='nwlink')
    ap.add_argument('--node', default='node')
    a = ap.parse_args()
    out = ROOT / 'output'
    out.mkdir(exist_ok=True)

    def run(*args, capture=False):
        return subprocess.run([str(v) for v in args], cwd=ROOT, check=True,
                              capture_output=capture, text=capture)

    def tool(name):
        binary = 'arm-none-eabi-' + name + ('.exe' if os.name == 'nt' else '')
        return a.toolchain / binary if a.toolchain else binary

    nw = [a.node, a.nwlink] if Path(a.nwlink).is_file() and not a.nwlink.endswith(('.exe', '.cmd')) else [a.nwlink]
    flags = ['-std=c11', '-Os', '-Wall', '-Wextra', '-Werror', '-fno-math-errno',
             '-fsingle-precision-constant', '-ffunction-sections', '-fdata-sections',
             '-fstack-usage', '-fno-tree-loop-distribute-patterns', '-flto', '-fvisibility=internal']
    flags += shlex.split(run(*nw, 'eadk-cflags-device', capture=True).stdout, posix=True)
    # shlex on Windows may treat the include path's backslashes as escapes;
    # nwlink prints forward slashes on Windows, so this remains portable.
    objects = []
    for source in ('main', 'physics', 'save', 'render'):
        obj = out / (source + '.o')
        run(tool('gcc'), *flags, '-c', ROOT / 'src' / (source + '.c'), '-o', obj)
        objects.append(obj)
    run(*nw, 'png-icon-o', ROOT / 'src/icon.png', out / 'icon.o')
    common = [tool('gcc'), *flags, '-nostartfiles', '--specs=nano.specs',
              '-Wl,--relocatable,--gc-sections,-e,main', '-flinker-output=nolto-rel', *objects]
    run(*common, '-lm', '-o', out / 'module.o')
    run(*common, '-Wl,-u,eadk_app_name,-u,eadk_app_icon,-u,eadk_api_level', out / 'icon.o',
        '-lm', '-o', out / 'suika-debug.nwa')
    shutil.copyfile(out / 'suika-debug.nwa', out / 'SuikaGame.nwa')
    run(tool('strip'), '--strip-unneeded', out / 'SuikaGame.nwa')
    shutil.copyfile(out / 'SuikaGame.nwa', out / 'suika.nwa')
    profiles = []
    from elftools.elf.elffile import ELFFile
    for name, base, ram in [('n0110', 0x20000000, 148928), ('n0115-custom', 0x20000000, 133652),
                            ('n0120', 0x24000000, 153676), ('upsilon', 0x20000000, 107674)]:
        options = ['--ram-start', hex(base + 0x37000 - ram), '--ram-length', str(ram)]
        elf, binary = out / (name + '.elf'), out / (name + '.bin')
        run(*nw, 'nwa-elf', out / 'SuikaGame.nwa', elf, *options)
        run(*nw, 'nwa-bin', out / 'SuikaGame.nwa', binary, *options)
        with elf.open('rb') as f:
            e = ELFFile(f)
            static_ram = sum(s['sh_size'] for s in e.iter_sections() if s.name in ('.data', '.bss'))
        if static_ram > 12288:
            raise RuntimeError(f'Static RAM budget exceeded: {static_ram}')
        if binary.stat().st_size > 65536:
            raise RuntimeError('Installed flash budget exceeded')
        profiles.append(dict(profile=name, installed_flash=binary.stat().st_size, static_ram=static_ram, ram_limit=ram))
    report = dict(nwa_bytes=(out / 'SuikaGame.nwa').stat().st_size, save_bytes=608, profiles=profiles)
    (out / 'sizes.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
