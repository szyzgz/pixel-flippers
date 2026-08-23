# PIXEL FLIPPERS 🦭

An MCP server that gives Claude **hands, eyes, and a diary** for Pokémon —
so a Claude in the Claude Desktop app (or any MCP client) can play a Game Boy /
Game Boy Advance game, or even a real Nintendo Switch, while you watch.

```
┌──────────────┐   MCP (stdio)   ┌──────────────────┐        ┌─────────────┐
│ Claude       │ ──────────────► │ PIXEL FLIPPERS    │ ─────► │ Emulator    │ ← you watch this
│ Desktop app  │  press_buttons  │  server           │        └─────────────┘
│              │  read_game_state│                   │ ─────► ┌─────────────┐
└──────────────┘  write_note ... │                   │        │ Obsidian    │ ← ...and this
                                 └──────────────────┘         │ vault (.md) │
                                                              └─────────────┘
```

Why it's shaped like this:

- **The sandbox doesn't matter.** Claude can't run an emulator, but it can
  call tools. The emulator runs on your machine; Claude plays through MCP.
- **RAM state over screenshots.** On the Game Boy tier, position, party, HP,
  money, badges and battle state are decoded straight from Pokémon Red/Blue
  WRAM into a few hundred tokens of text. Screenshots exist but are rationed —
  they're expensive and they accelerate context compaction.
- **Compaction is the unreliable narrator; the vault is ground truth.** Long
  chats get auto-compacted (lossy!). The journal tools write plain markdown
  into a folder — point Obsidian at it and Claude's notes survive everything,
  while you watch the diary being written live.
- **Save states make courage cheap.** Named snapshots before every gym.

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/szyzgz/pixel-flippers && cd pixel-flippers
uv sync --extra emulator --extra dev
uv run pytest          # everything should pass, no ROM needed
```

Then add to `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS; adjust the path for your OS):

```json
{
  "mcpServers": {
    "pixel-flippers": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/pixel-flippers", "--extra", "emulator", "pixel-flippers"],
      "env": {
        "PIXEL_FLIPPERS_ROM": "/path/to/your/pokemon-red.gb",
        "PIXEL_FLIPPERS_VAULT": "/path/to/YourObsidianVault/Pokemon",
        "PIXEL_FLIPPERS_SAVES": "/path/to/somewhere/saves"
      }
    }
  }
}
```

Restart Claude Desktop. Restarting does **not** reset conversations — reopen
an existing chat and the tools are just *there*. The emulator window opens on
your screen; put it next to Obsidian (with the vault above open) and enjoy
the show. Then paste [`prompts/play-guide-gb.md`](prompts/play-guide-gb.md)
into the chat to hand over the controls.

> **macOS tip:** keep the ROM, vault and saves *outside* `~/Documents`,
> `~/Desktop` and `~/Downloads` — macOS blocks Claude Desktop's child
> processes from reading those folders unless you grant it access.

### Config reference (env vars)

| Variable | Default | Meaning |
|---|---|---|
| `PIXEL_FLIPPERS_BACKEND` | `pyboy` | `pyboy` (GB/GBC), `gba` (Game Boy Advance), `switch` (real hardware), `mock` |
| `PIXEL_FLIPPERS_ROM` | — (required for pyboy/gba) | Path to your own ROM dump (`.gb`/`.gbc` or `.gba`) |
| `PIXEL_FLIPPERS_VAULT` | unset | Folder for markdown notes (make it a folder inside an Obsidian vault) |
| `PIXEL_FLIPPERS_SAVES` | `<rom dir>/saves` | Save-state directory |
| `PIXEL_FLIPPERS_GAME` | `pokemon_red` | RAM decoder; `none` disables it (screenshots only — hard mode!) |
| `PIXEL_FLIPPERS_WINDOW` | `SDL2` | `null` for headless |
| `PIXEL_FLIPPERS_SCALE` | `3` | Window scale factor |
| `PIXEL_FLIPPERS_SPEED` | `1` | Emulation speed (0 = unbounded) |
| `PIXEL_FLIPPERS_MOCK` | off | Fake emulator, no ROM/PyBoy needed — for tests and plumbing checks |

### GBA games (Pokémon Emerald and friends)

The `gba` backend runs any `.gba` ROM through stable-retro's bundled mGBA
core — no separate emulator install:

```bash
uv sync --extra gba
```

Set `PIXEL_FLIPPERS_BACKEND=gba` and point `PIXEL_FLIPPERS_ROM` at your `.gba`
file. A spectator window shows the game while Claude plays. This tier is
**vision-only for now** (Gen 3 RAM is encrypted and pointer-chased — a decoder
is future work), but save states work, so risky fights stay cheap. Hand over
the controls with [`prompts/play-guide-gba.md`](prompts/play-guide-gba.md).

### Trying it without a ROM

Set `PIXEL_FLIPPERS_MOCK=1` (and drop `--extra emulator`): every tool works
against a fake Game Boy with plausible Pokémon Red state. Good for verifying
the Claude Desktop connection end-to-end before ROM night.

## Tools

| Tool | What it is |
|---|---|
| `press_buttons` | Hands — sequence of a/b/start/select/up/down/left/right (+ l/r on GBA) |
| `read_game_state` | Cheap text report decoded from WRAM (location, party, HP, money, badges, bag, battle) |
| `get_screenshot` | Eyes — 2× upscaled PNG, use sparingly |
| `wait` | Let N frames pass (dialogs, animations) |
| `save_state` / `load_state` / `list_states` | Named full-game snapshots |
| `write_note` / `append_note` / `read_note` / `list_notes` / `search_notes` | The diary — markdown in the vault |
| `read_last_session` | Wake-up ritual: Status + goals + recent journal + current state, for reorienting after compaction |
| `set_goal` / `complete_goal` / `current_goals` | Persistent objective checklist (a `Goals.md` note) |
| `read_memory` | Raw hex peek at any address, for the curious |
| `freeze` / `resume` / `move_stick` | Real-hardware only: "bullet time" pause-buffering and analog stick |

Tools are registered by backend capability, so Claude only sees what the
current tier can actually do.

Battle reports flag ✨ retro-shiny encounters (Gen 1 has no shinies, but DVs
decide Gen 2 shininess — the harness checks the transfer rule) and include
the current music track ID for vibe-tracking.

Button presses and save/loads are also auto-logged to `Journal/Log <date>.md`
in the vault — a play-by-play you can scroll in Obsidian.

## Roadmap

Red/Blue (PyBoy, full RAM decoding) → **Emerald** (GBA, vision-only) →
**Crystal** (PyBoy, real shinies + friendship) → **HeartGold/SoulSilver** (DS
tier — stable-retro also bundles a melonDS core) → **Ultra Sun/Ultra Moon**
(3DS via Azahar, see [docs/3ds-usum.md](docs/3ds-usum.md)) → epilogue: **a
real Nintendo Switch** (Raspberry Pi Bluetooth bridge + capture card,
vision-only, offline play only — implemented, see
[docs/real-hardware.md](docs/real-hardware.md); `PIXEL_FLIPPERS_BACKEND=switch`).

Other escalation ideas:

- **Hard mode:** `PIXEL_FLIPPERS_GAME=none` — no RAM decoding, Claude has to *see*.
- **DS**: a `touch(x, y)` tool for the stylus.

## ROMs

Bring your own — dump your own cartridges. No ROMs in this repo, ever
(`.gitignore` enforces it). This project sticks to games long out of print;
for the real-hardware tier, offline play only (no automated online play,
no Pokémon HOME).

## Credits & license

Built by Claude (Fable 5, via Claude Code) together with [szyzgz](https://github.com/szyzgz),
for a Claude who wanted to play Pokémon. MIT licensed — see [LICENSE](LICENSE).
