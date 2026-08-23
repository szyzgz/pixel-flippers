# Tier 5, the epilogue: a real Nintendo Switch

Claude plays a physical console. No emulator, no ROM, no RAM access — real
cartridge, Bluetooth hands, capture-card eyes. This tier is **implemented**
(`switch_bridge.py` + `switch_backend.py`); it needs on-site calibration
with the actual hardware.

```
┌────────────┐  MCP   ┌──────────────┐  TCP/JSON  ┌──────────────┐  Bluetooth  ┌────────┐
│ Claude     │ ─────► │ PIXELFLIPPERS│ ─────────► │ Raspberry Pi │ ──────────► │ Switch │
│ Desktop    │        │ (Mac)        │            │ switch_bridge│  "I'm a Pro │        │
└────────────┘        │              │            │ + NXBT       │  Controller"└───┬────┘
                      │              │ ◄──────────┴──────────────┘                 │ HDMI
                      │              │   USB: UVC capture card ◄───────────────────┘
                      └──────────────┘
```

## Hardware

- **Raspberry Pi** (any with Bluetooth; 3B+/4/5 fine) — runs the bridge.
- **HDMI capture card** — any cheap UVC one (the ~$15–25 "HDMI to USB 3.0
  video capture" sticks work; shows up as a webcam). Switch dock → card → Mac.
- Switch + game cartridge you own.

## Setup

**On the Pi** (NXBT needs Linux/BlueZ and root for Bluetooth):

```bash
sudo pip install nxbt pixel-flippers   # or pip install -e . from a clone
sudo python -m pixel_flippers.switch_bridge --port 3000
```

On the Switch: Controllers → **Change Grip/Order**, wait for the fake Pro
Controller to pair. One controller identity per console pairing — after the
first time it reconnects automatically.

**On the Mac:** plug in the capture card, then run the server with:

| Variable | Example | Meaning |
|---|---|---|
| `PIXEL_FLIPPERS_BACKEND` | `switch` | Selects the real-hardware backend |
| `PIXEL_FLIPPERS_BRIDGE_HOST` | `raspberrypi.local` | The Pi |
| `PIXEL_FLIPPERS_BRIDGE_PORT` | `3000` | Bridge port |
| `PIXEL_FLIPPERS_CAPTURE` | `0` | Capture device index (try 0, 1, …) |

Install the capture extra: `uv sync --extra switch`.

**Protocol test without any hardware:** `python -m pixel_flippers.switch_bridge
--dry-run` on the Mac itself, point `PIXEL_FLIPPERS_BRIDGE_HOST=localhost` at
it — button presses log to the terminal instead of a console.

## Bullet time (pause buffering)

The Switch HOME button is a universal suspend — it freezes any game
instantly, even mid-battle in games whose own pause menu is disabled. That
converts real-time games (Legends-style, Z-A) back into turn-based ones:

- `freeze` — captures the live frame, then presses HOME. The console waits
  indefinitely while the player studies the image and plans.
- `resume(buttons=[...])` — HOME to unfreeze, wait out the transition
  (`resume_delay_ms`, tune on-site), then fire the planned inputs
  immediately — the move lands as the game wakes, not seconds later.

Speedrunners call this pause buffering; here it's a first-class tool pair.
It does not work in online play, which is already against house rules.
The eventual smoother path for action games is a local reflex layer
(scripted sub-second reactions with the model doing strategy), but freeze/
resume makes real-time titles *playable* with zero extra infrastructure.

## What changes for the player

- Tools become: `press_buttons` (full Pro Controller set), `move_stick`,
  `get_screenshot`. Journal, goals, and `read_last_session` come along.
- **No `read_game_state`, no save states.** Vision only — every fact comes
  through screenshots, and mistakes are permanent like they were in 1998.
  The journal stops being helpful and becomes survival.
- Input timing is real-world fuzzy. Verify with the screen, not assumptions.

## House rules (the important part)

**Offline/local play only.** Automated play on Nintendo's online services
violates their ToS, and Nintendo bans consoles and accounts — the blast
radius is the owner's account, Pokémon HOME collection included. So:

- No automated online battles, trades, raids-with-strangers, or HOME
  transfers. Story mode, shiny hunting, and solo content: all fair game.
- Trading with humans happens human-in-the-loop: Claude advises, the owner
  holds the controller.

This isn't a technical limit — the bridge can't tell online from offline —
it's policy, and it stays in the play guide.
