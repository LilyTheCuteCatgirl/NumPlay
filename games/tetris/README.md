> **Tetris is part of [NumPlay](../../README.md)**, every NumWorks game in one app. Get it with all the others, or on its own as `Tetris.nwa` from the [latest release](https://github.com/Mason363/NumPlay/releases/latest).

# Tetris

The classic, for the NumWorks calculator, written in Rust by **[Tatone26](https://github.com/Tatone26)** (A. Sainero) in [Numworks-games](https://github.com/Tatone26/Numworks-games/tree/main/apps/tetris). It plays by the modern rules: the 7-piece bag, hold, ghost piece, wall kicks, hard and soft drop, and a save that lets you quit mid-game and continue later.

The original is released into the public domain ([UNLICENSE](UNLICENSE)). This copy, made for NumPlay, changes a few things:

- **Full screen**: 12-pixel blocks, so the playfield fills the whole height of the screen, with the panels resized to match.
- **Number pad arrows**: 8, 4, 6 and 2 work like the arrow keys, in the game and in the menus.
- Settings are saved as `tetris.set` (the name used to lack an extension and a terminator), and a full storage no longer stops the game.
- A smaller build (size-optimized, link-time optimization), and an entry point for the NumPlay launcher.

## Controls

| Key | Action |
| --- | --- |
| Left / Right, or 4 / 6 | Move |
| Down, or 2 | Soft drop |
| Up, or 8 | Rotate left / right |
| OK / Toolbox | Hard drop |
| ⌫ | Hold |
| Back | Pause, with Quit game |

## Build

`cargo build --release` (Rust with the `thumbv7em-none-eabihf` target, `arm-none-eabi-gcc` and `nwlink` in the path) writes the app to `target/thumbv7em-none-eabihf/release/tetris`. From the NumPlay root, `make apps` copies it to `build/apps/Tetris.nwa`.

`numworks_utils` and `nppm_decoder` are Tatone26's libraries from the same repository.
