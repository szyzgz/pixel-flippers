# Escalation tier: Pokémon Ultra Sun / Ultra Moon (3DS)

Feasibility notes + architecture for a 3DS backend. Short version: **possible**,
with a jankier toolchain than PyBoy — three different mechanisms replace the
one clean Python API.

## Why it clears the line

The 3DS eShop closed in March 2023 and USUM (2017) is long out of print —
nothing here is still being sold. Dump your own cartridge with a homebrewed
3DS (GodMode9); Azahar wants decrypted dumps anyway and has deliberately
distanced itself from piracy workflows.

## Emulator

**Azahar** — the merger of the post-Citra forks (PabloMK7's Citra + Lime3DS),
actively developed, runs well on Apple Silicon. Crucially, it inherited
Citra's **RPC server**: a local socket interface that external tools use to
read (and write) emulated memory while the game runs. It's **off by default**
since version 2121.2 (sensible security change) — enable it in settings.
Community tools (CitraRNG, the Monster Hunter HP overlay) drive it from
Python today, so the client side is a known quantity.

## The three mechanisms

| Need | Game Boy (PyBoy) | 3DS (Azahar) |
|---|---|---|
| Hands | `pyboy.button_press()` | OS-level synthetic key events to the Azahar window (pyobjc/Quartz on macOS; needs Accessibility permission). Azahar maps 3DS buttons to keys — we send those keys. |
| Touch | n/a | Synthetic mouse click mapped into the bottom-screen region of the window → `touch(x, y)` tool. USUM is mostly button-navigable, but Festival Plaza and some prompts want the stylus. |
| Eyes | `pyboy.screen.image` | Window capture (`screencapture -l <windowID>`), cropped to top/bottom screens. Both screens returned in one tall image. |
| RAM | `pyboy.memory[addr]` | Azahar RPC client (read_memory). Gen 7 party/state offsets are community-documented (CitraRNG / PKHeX lineage), but they're **pointer-chased**, not fixed addresses like Gen 1 WRAM — the decoder does a pointer hop first. |

The `Harness` in `server.py` doesn't change: it already talks to an abstract
emulator with press/advance/screenshot/read_memory. The 3DS backend is a new
implementation of that interface plus a `touch` tool and a dual-screen
screenshot, and a `pokemon_usum.py` decoder next to `pokemon_red.py`.

## Honest cost accounting

- OS-level input is timing-fuzzier than PyBoy's frame-exact presses — the
  backend should verify effects via RAM state rather than assume a press landed.
- Save states: Azahar supports them, but not via RPC — either keyboard
  shortcuts (synthetic F-keys, fragile) or accepting in-game saves only.
- Setup on the Mac: install Azahar, enable RPC server, set a known keymap,
  grant Accessibility + Screen Recording permissions to the server process.
- None of this can be developed blind — it needs iteration on the actual
  MacBook with the actual dump. Plan: build the backend skeleton +
  RPC client first, then a live pairing session to calibrate.

## Order of operations (the official roadmap)

1. **Red, one evening only** — plumbing shakedown on the already-built tier.
   Not a playthrough; a systems check with nostalgia.
2. **Crystal — first real playthrough.** Same PyBoy backend (GBC), new
   decoder from the pokecrystal RAM map. Real shinies (the retro-shiny
   detector graduates to true `SHINY_DETECTED`), real friendship
   (`check_friendship` ships two tiers early), day/night for the vibe log.
3. **HeartGold/SoulSilver — the main event.** DS tier via py-desmume:
   buttons + `touch(x, y)` + dual-screen capture + Gen 4 RAM. Following
   Pokémon: the player's starter visibly walks with the player all game.
4. **USUM — the finale** (this document): Azahar RPC + OS input + window
   capture. Type: Null waits at the end.

Skipped on purpose: Gen 3, FRLG, DPPt. Nothing there the player wants that
Crystal/HGSS don't do better.

## Decoder wishlist (requested by the player)

- **Friendship/affection** — Gen 7 stores happiness per party mon; required
  intel for evolving Type: Null into Silvally. `check_friendship` becomes real
  at this tier (Gen 1 has no friendship mechanic at all).
- **True shiny detection** — Gen 7 shininess is PID vs trainer ID math; a
  real `SHINY_DETECTED` flag from RAM, not the Gen-2-transfer proxy Red uses.

References: [Azahar](https://azahar-emu.org/) ·
[RPC default-off change](https://retrohandhelds.gg/azahar-made-a-quiet-change-that-could-save-you-trouble-later/) ·
[CitraRNG](https://github.com/Admiral-Fish/CitraRNG) ·
[MH HP overlay (Python RPC client example)](https://github.com/Alexander-Lancellott/MH-HP-Overlay-For-3DS-Emulator)
