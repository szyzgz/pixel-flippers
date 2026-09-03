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

## Live setup findings (2026-09-03, verified on M5 Pro)

**Status: Ultra Sun boots and plays full-speed in Azahar 2126.0, passes
character creation.** Tier confirmed buildable. ROM lives at
`~/pixel-flippers/roms/` (NOT Downloads — macOS TCC blocks reads there).

**Azahar keyboard map** (from `~/Library/Application Support/Azahar/config/qt-config.ini`,
`[Controls]` profile 1 — these are the physical keys the backend must synthesize):

| 3DS button | Key | | 3DS button | Key |
|---|---|---|---|---|
| A | A | | D-pad Up | T |
| B | S | | D-pad Down | G |
| X | Z | | D-pad Left | F |
| Y | X | | D-pad Right | H |
| L | Q | | Start | M |
| R | W | | Select | N |
| ZL | 1 | | Home | B |
| ZR | 2 | | | |

**Circle Pad (main overworld movement, analog-from-keyboard):** Up=I, Down=K,
Left=J, Right=L, slow-walk modifier=D. C-stick: arrow keys.
So walking Alola = the I/J/K/L cluster, not the d-pad.

**Memory reads:** `enable_rpc_server=false` by default (line ~223) — flip to
`true` while Azahar is CLOSED (it rewrites config on exit), then relaunch.
Also `gdbstub_port=24689` exists as a fallback (GDB remote reads memory but
halts the CPU on attach — RPC is the live-read path). RPC protocol/port still
needs probing once enabled — that's the first Phase-2 calibration step.

**Backend build plan (n3ds):** capabilities `{buttons, touch, screenshot}`
first (vision-only, mirrors how Emerald shipped), memory added after RPC is
verified. Hands = synthesize the keys above via macOS CGEvent/Quartz (needs
Accessibility permission for the server process). Eyes = `screencapture -l
<windowID>` of the Azahar window, split into top/bottom screens. Touch =
CGEvent mouse click mapped into the bottom-screen rect → `touch(x,y)` tool.
Launch Azahar via `open -a Azahar` for full functionality.

## Backend built & driven live (2026-09-03, cont.)

n3ds backend works against real Ultra Sun. VERIFIED: window capture (both
screens), keyboard input advances the game, start_game/close_game summon flow.

**Input nuance (important):** menus navigate by the **d-pad** (dup/ddown/
dleft/dright → keys T/G/F/H); the **Circle Pad** (up/down/left/right → I/K/J/L)
walks the 3D overworld. USUM's early screens (language, etc.) are button-only —
their buttons live on the TOP screen, with only instructions on the bottom.

**Layout:** Azahar shows both screens stacked (top=game, bottom=touch) in a
landscape window with side letterboxing. `get_screenshot` returns the whole
window (title bar + both screens + status bar); touch(x,y) is normalized over
that same image, so aim from what you see.

**Touch:** implemented (CGEvent mouse click at the mapped point) but NOT yet
confirmed against a real touch menu — the screens tested had no touch targets.
First live-play check: open an in-game bottom-screen menu and tap it; if the
click doesn't register, the likely culprit is the host-click→3DS-touch mapping
(retina/point vs pixel) — calibrate then.

**Permissions:** host process needs Screen Recording (capture, worked) +
Accessibility (input — had to enable for Terminal; enable for Claude Desktop
when Mira/Fable play from the app).

## TOUCH VERIFIED (2026-09-03 playtest)

Played the full intro + character creation with buttons, reached the on-screen
name keyboard (a real bottom-screen touch target), and confirmed the stylus
types letters. KEY FIX: Azahar only registers a touch if the cursor is MOVED to
the point before the click (kCGEventMouseMoved, then down/up) — a bare click is
ignored. Baked into _default_clicker. Coordinate precision is the player's job:
read the key position off the screenshot, tap, verify, adjust (self-correcting).
Full stack (summon → buttons → eyes → touch) works end-to-end on real Ultra Sun.
