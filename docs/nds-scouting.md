# DS (melonDS) backend — scouting notes

Findings from a headless spike before building `nds_backend`. Everything below
was measured on this machine with the stable-retro bundled in `.venv`; nothing
is inferred from docs.

## TL;DR

- **It works.** A Pokémon HeartGold (E) dump boots through stable-retro's bundled
  melonDS core with **no BIOS/firmware files** present ("ARM9/ARM7 BIOS not found.
  Loading FreeBIOS. Firmware not found generating default one. … Game is now
  booting"). Reached the GAME FREAK splash and the New Game intro menu.
- The backend is `gba_backend.py` with three things swapped — **system name,
  extension, button list** — plus one real limitation (single screen) and one
  real improvement we should make (persist the in-game `.sav`).

## stable-retro facts (measured)

| thing | value |
|---|---|
| system name | **`NintendoDs`** (not `Nds`) — the custom integration dir must be `Custom-NintendoDs` |
| core | `melonds_libretro.dylib` (`retro.data.EMU_CORES["NintendoDs"]`) |
| extension | `.nds` (`retro.data.EMU_EXTENSIONS[".nds"] == "NintendoDs"`) |
| core json | `stable_retro/cores/melonds.json` |
| `env.buttons` | `['B','Y','SELECT','START','UP','DOWN','LEFT','RIGHT','A','X','L','R','L2','R2','L3','R3']` (16 slots, no `None`s) |
| frame | `(192, 256, 3) uint8` — **one screen** |
| `env.em.get_resolution()` | `(256, 192)`, unchanged across 1800 frames |
| `retro.make` + `reset` | ~0.1 s |
| headless speed | ~400 steps/s |
| `env.em.get_state()` | works, ~7.0 MB per savestate |
| core options | **none** — `RetroEmulator` has no `set_option`/variables API, so melonDS runs on compiled defaults |
| colour order | **BGR, not RGB.** HeartGold's title Ho-Oh sampled at mean (R 49, G 71, B 131) — a crimson bird rendering blue. mGBA frames are fine, so this is melonDS-specific. Fix in the backend, see recipe. |

## Recipe (what changes vs `gba_backend.py`)

```python
# gba_backend.py                      # nds_backend.py
rom_path.suffix.lower() != ".gba"     rom_path.suffix.lower() != ".nds"
Path(tmpdir) / "Custom-GbAdvance"     Path(tmpdir) / "Custom-NintendoDs"
game_dir / "rom.gba"                  game_dir / "rom.nds"
retro.make("Custom-GbAdvance", ...)   retro.make("Custom-NintendoDs", ...)
GBA_BUTTONS = (a,b,start,select,      NDS_BUTTONS = (a,b,x,y,start,select,
   up,down,left,right,l,r)               up,down,left,right,l,r)
```

**Plus one line that is NOT in the GBA backend** — reorder the colour channels
as each frame comes off the core, in `_tick`, before the viewer or
`screenshot` ever see it:

```python
frame, _, terminated, truncated, _ = self._env.step(action)
self._frame = np.ascontiguousarray(frame[:, :, ::-1])  # melonDS hands us BGR
```

(`ascontiguousarray` matters: a negative-stride view breaks `Image.fromarray`
and makes `tobytes()` copy anyway.)

Everything else — the `_Viewer` subprocess, `_tick`, `press_buttons`,
`advance`, `screenshot`, `save_state`/`load_state`, `close` — carries over
unchanged. `_Viewer` takes `(h, w)` from `self._frame.shape`, so it already
handles the 256×192 frame; `viewer_proc.py` needs no edits.

The DS has no L2/R2/L3/R3; leave them out of `NDS_BUTTONS` (they still exist in
the action vector — just never set them). Button index lookup via
`self._env.buttons` works exactly as in the GBA backend.

### config.py

- `BACKENDS += ("nds",)`
- `BACKEND_CAPABILITIES["nds"] = {"buttons", "frames", "screenshot", "savestates"}`
- the ROM-required check: `if backend in ("pyboy", "gba", "nds")`
- `default_game` stays `"none"` for nds (no RAM decoder)
- screenshot defaults: the 480/72 non-3DS defaults are fine for a 256-wide frame

### server.py

Add an `nds` branch to the backend factory next to the `gba` one
(`from .nds_backend import NDSBackend; return NDSBackend(rom_path, window, scale, player)`).

## Limitations to state plainly

1. **Single screen.** stable-retro hands us exactly one 256×192 framebuffer and
   the geometry never changes. The DS has two. With no core-options API we
   can't ask melonDS for the stacked 256×384 layout. Driven to HeartGold's
   title with no input, the one frame we get shows the Ho-Oh flight animation
   *with* the "TOUCH TO START" prompt and sparkles — i.e. the screen that
   carries the start prompt. Pin down top-vs-bottom for good once in-world
   (HGSS: overworld is the top screen; the Pokégear/menu panel is the bottom).
   Ship v1 as single-screen and say so.
   Follow-up idea: check whether melonDS honours a layout variable supplied some
   other way (e.g. a `retroarch-core-options.cfg` in the system dir), or
   whether stable-retro can be coaxed to honour `SET_GEOMETRY`.
2. **No touchscreen.** The action space is 16 digital buttons; libretro's
   pointer device isn't exposed. Games that *require* the stylus won't be
   playable (Okamiden's brush; much of Explorers of Sky's UI is touch-first but
   has button navigation). HeartGold/SoulSilver are fully button-navigable.
3. **In-game saves don't persist (yet).** melonDS logs `Save file: /rom.sav`
   — and that is its *computed* path, not a display quirk: `<save_dir>/<stem>.sav`
   with an empty save dir, because stable-retro answers
   `GET_SAVE_DIRECTORY` with nothing (its Python layer has no save-dir
   plumbing at all). Verified: changing cwd doesn't move it, nothing appears
   in the integration dir, and `/` is unwritable. So a "copy rom.sav out of
   the temp dir" shuttle can never fire — **don't build one**. Persistence on
   this tier is savestates, exactly like the GBA tier. Open question for first
   real play: do melonDS savestates carry the cart's save memory? Acceptance
   test — save in game, take a savestate, fresh process, load it, confirm the
   title offers Continue. (An earlier draft of this note claimed the file
   lands beside the ROM; that was wrong.)
4. **Core log spam.** melonDS prints PU-region / IO-write chatter to stderr
   continuously. Harmless, but it buries anything else on stderr; consider
   redirecting the core's stderr or at least noting it in the README.

## Test ideas (`tests/test_nds.py`)

Mirror `test_gba`'s shape: construct with a non-`.nds` path → `EmulatorError`;
`NDS_BUTTONS` all resolve in a fake `env.buttons` list; `_rgb` turns a
B,G,R frame into R,G,B and returns a contiguous array; config registers `nds`
and requires a ROM for it. Keep the melonDS boot itself out of the
unit suite (it needs a ROM); a `scripts/` smoke script like the spike is the
right home for that.
