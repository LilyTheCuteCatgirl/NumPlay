# Validation

Standalone build with ARM GCC 15.2.1 and nwlink 1.0.0:

| Metric | Result |
| --- | ---: |
| Stripped `.nwa` file | 31,324 bytes |
| Installed code, icon and constants | 17,520 bytes |
| Static RAM (`.data` + `.bss`) | 7,245 bytes |
| Stack high-water mark in ARM scenarios | 372 bytes |
| Save payload | 608 bytes |
| Original supplied `.nwa` file, for comparison | 316,788 bytes |

The original artifact bundled catalogue code and other games. The file-size
comparison includes that removal as well as the new implementation; it is not
a comparison of identical engines. File size and installed flash are distinct.

- Host C suite: 28,800 physics ticks across 12 seeds, repeated save round trips,
  every single-byte save corruption, invalid-but-checksummed payload, collisions,
  spawning, reserve, hint, overflow and deterministic generation. Partial and
  full drawing compared pixel-for-pixel over 120 changing frames.
- Actual ARM standalone: pause freezes the basket, resuming does not catch up
  paused time, restart continues the same run, Home and On-Off save and exit.
  Passed N0110/Epsilon, N0115 custom 133,652-byte RAM, N0120/Epsilon and
  N0110/Upsilon 107,674-byte RAM profiles.
- Full record storage: failed exit stays in the app, allows returning to play,
  retries save and permits an explicit exit without overwriting existing files.
- Dense 48-fruit ARM scene: 77 presentation cycles in 2.5 simulated seconds,
  worst instrumented drawing time 9 ms, zero draws over 32 ms. These are the
  repository emulator's instruction/time model, not a hardware FPS measurement.
- Repository `test_games.py --game suika`: passed both exit/reload sessions,
  untouched unrelated files, cleared app RAM and balanced Home locking.
- Launcher integration: compiled the real launcher and generated Suika module
  through `module.ld`, `npmodule.py`, `gen_games.py` and `shots.py`. It linked at
  107,674 / 133,652 / 148,928-byte RAM limits and passed the same ARM smoke test;
  launcher stack high-water mark 984 bytes. Its progress backup included
  `suika.sav` in `numplay_saves.py`.

The launcher integration run used a manifest filtered to Suika. The complete
all-game NumPlay build, translated builds, full flash capacity and unrelated
game suites still need the repository Linux CI. The CI workflow now includes
both the Suika host suite and ARM scenarios. A physical NumWorks session has
not been performed. This validates the tested cases, not an absence of all bugs.
