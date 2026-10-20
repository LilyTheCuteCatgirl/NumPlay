#!/usr/bin/env python3
"""Suika's actual ARM app: pause/reload, low-RAM profiles and full-storage exits.

python tests/test_suika.py games/suika/output/suika-debug.nwa --nwlink nwlink
Unstripped builds also report emulator render timings (not hardware benchmarks).
"""
import argparse
import json
import os
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_games import records, presses  # noqa: E402
import emu  # noqa: E402


def storage(path, items):
    data = bytearray(emu.STORAGE_SIZE)
    offset = 0
    for name, value in items:
        record = name.encode('ascii') + b'\0' + value
        size = 2 + len(record)
        struct.pack_into('<H', data, offset, size)
        data[offset + 2:offset + size] = record
        offset += size
    assert offset + 2 <= len(data)
    path.write_bytes(data)


def live_records(c):
    return dict(records(bytes(c.uc.mem_read(emu.STORAGE + 4, emu.STORAGE_SIZE))))


def check_exit(c, upsilon=False):
    assert c.exited, 'App did not exit'
    assert not c.violations, sorted(set(c.violations))
    assert not c.firmware_ram_changes(), 'Firmware RAM changed'
    assert c.app_ram_clean(), 'Application RAM was not cleared'
    assert c.locks == 0 and c.max_locks == (0 if upsilon else 1), 'Home lock imbalance'
    assert c.stack_used() < 8192, c.stack_used()
    if upsilon:
        assert not any(c.svc_counts.get(n) for n in (10, 13, 15, 16, 30, 32))


def seed_basket():
    p = bytearray(608)
    p[:4] = b'SKA1'
    struct.pack_into('<4I', p, 4, 123, 777, 73, 12)
    struct.pack_into('<HH', p, 20, 97 * 64, 0)
    p[25:29] = bytes([3, 1, 2, 4])
    p[29] = 2
    p[31] = 6
    p[33:35] = bytes([1, 4])
    p[35:43] = bytes([1, 1, 1, 2, 2, 3, 3, 4])
    p[45] = 48
    for i in range(48):
        struct.pack_into('<BhhhhH', p, 48 + 11 * i, 1, (14 + i % 8 * 22) * 64,
                         (76 + i // 8 * 16) * 64, 0, 0, 240)
    h = 2166136261
    for b in p[:-4]:
        h = ((h ^ b) * 16777619) & 0xffffffff
    struct.pack_into('<I', p, 604, h)
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('nwa')
    ap.add_argument('--nwlink', default='nwlink')
    ap.add_argument('--out', type=Path, default=Path('build/test_suika'))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    report = []
    sentinel = b'\x01print(42)\n\0'
    for name, model, system, ram in [('n0110', 'n0110', 'epsilon', 148928),
                                      ('n0115-custom', 'n0110', 'epsilon', 133652),
                                      ('n0120', 'n0120', 'epsilon', 153676),
                                      ('upsilon', 'n0110', 'upsilon', 107674)]:
        emu.configure(model, system, ram)
        st = a.out / (name + '.bin')
        storage(st, [('keep.py', sentinel)])
        c = emu.Calculator(a.nwa, storage_file=str(st), nwlink=a.nwlink)
        c.enable_checks()
        c.keys = presses((500, 'ok', 60), (1000, 'ok', 60), (1150, 'back', 60),
                         (3400, 'back', 60), (3550, 'back', 60), (4700, 'home', 200))
        c.run(2200)
        first = live_records(c)['suika.sav']
        age0 = struct.unpack_from('<H', first, 57)[0]
        c.run(3300)
        assert live_records(c)['suika.sav'] == first, 'Pause changed the saved basket'
        c.run(4100)
        second = live_records(c)['suika.sav']
        age1 = struct.unpack_from('<H', second, 57)[0]
        assert 5 <= age1 - age0 <= 15, ('Paused time caught up', age0, age1)
        c.save_png(str(a.out / (name + '-pause.png')))
        c.run(5500)
        check_exit(c, system == 'upsilon')
        recs = dict(records(st.read_bytes()))
        assert recs['keep.py'] == sentinel
        kept = recs['suika.sav']
        # Continue from disk and immediately pause: no drop was fabricated.
        d = emu.Calculator(a.nwa, storage_file=str(st), nwlink=a.nwlink)
        d.enable_checks()
        d.keys = presses((500, 'ok', 60), (650, 'back', 60), (1100, 'onoff', 200))
        d.run(2000)
        check_exit(d, system == 'upsilon')
        resumed = dict(records(st.read_bytes()))['suika.sav']
        assert resumed[45] == kept[45] and resumed[4:20] == kept[4:20]
        report.append(dict(profile=name, pause_reload_exit='pass', stack_bytes=max(c.stack_used(), d.stack_used())))

    emu.configure('n0110', 'epsilon', 148928)
    st = a.out / 'full.bin'
    filler = bytes([0x5a]) * (emu.STORAGE_SIZE - 32)
    storage(st, [('full.dat', filler)])
    c = emu.Calculator(a.nwa, storage_file=str(st), nwlink=a.nwlink)
    c.enable_checks()
    c.keys = presses((500, 'ok', 60), (800, 'ok', 60), (1200, 'home', 200),
                     (1900, '1', 60), (2400, 'home', 200), (3000, 'back', 100))
    c.run(1800)
    assert not c.exited, 'Save failure was silently discarded'
    assert 'suika.sav' not in live_records(c)
    c.save_png(str(a.out / 'save-failed.png'))
    error_screen = bytes(c.screen)
    c.run(2200)
    assert not c.exited and bytes(c.screen) != error_screen, 'Could not cancel a failed exit'
    c.run(4000)
    check_exit(c)
    assert dict(records(st.read_bytes()))['full.dat'] == filler

    # Dense 48-body basket, to exercise bounded physics and rendering in ARM.
    st = a.out / 'dense.bin'
    storage(st, [('keep.py', sentinel), ('suika.sav', seed_basket())])
    c = emu.Calculator(a.nwa, storage_file=str(st), nwlink=a.nwlink)
    c.enable_checks()
    c.keys = presses((500, 'ok', 60), (3000, 'home', 200))
    c.run(2500)
    timing = {}
    for key in ('perf_frames', 'perf_max', 'perf_slow'):
        if key in c.symbols:
            timing[key] = struct.unpack('<I', bytes(c.uc.mem_read(c.symbols[key], 4)))[0]
    if timing:
        assert timing['perf_max'] <= 32 and timing['perf_slow'] == 0, timing
    c.save_png(str(a.out / 'dense.png'))
    c.run(4000)
    check_exit(c)
    assert dict(records(st.read_bytes()))['keep.py'] == sentinel
    report.append(dict(dense_basket='pass', full_storage='pass', emulator_timing=timing, stack_bytes=c.stack_used()))
    (a.out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
