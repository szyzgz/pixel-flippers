"""DS backend via stable-retro's bundled melonDS libretro core.

Same trick as the GBA backend: stable-retro wants games pre-registered in its
integration library, so we generate a throwaway custom integration around the
user's .nds dump at startup and any DS game boots. No BIOS or firmware files are
needed — melonDS falls back to FreeBIOS and a generated firmware. Emulation only
advances when tools are called, so the game waits for its player.

Two honest limits, documented in docs/nds-scouting.md:

  * Single screen. stable-retro hands us exactly one 256x192 framebuffer and the
    geometry never changes; with no core-options API we can't ask melonDS for
    the stacked 256x384 layout. v1 is single-screen. HeartGold/SoulSilver are
    fully button-navigable, so this is enough to play them.
  * No touchscreen. The action space is 16 digital buttons; libretro's pointer
    device isn't exposed. Stylus-only games (Okamiden's brush) won't work.

In-game saves do NOT persist on this tier. stable-retro answers libretro's
GET_SAVE_DIRECTORY with nothing, so melonDS computes an empty save dir and tries
to write "<stem>.sav" at the filesystem root — an unwritable path that goes
nowhere. Persistence here is save states, exactly like the GBA tier. (Open
question for first real play: do melonDS save states carry the cart's SRAM?
Acceptance test — save in game, save_state, fresh process, load_state, and check
the title screen offers Continue.)

No RAM decoding: Gen 4 party data isn't mapped here, so HeartGold is a
vision-only tier with save states.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .emulator import EmulatorError

# The DS has X/Y that the GBA lacks, and no L2/R2/L3/R3 (those exist in the
# action vector but we never set them). Index lookup is by name via env.buttons.
NDS_BUTTONS = (
    "a", "b", "x", "y", "start", "select", "up", "down", "left", "right", "l", "r",
)


class _Viewer:
    """Spectator window in a subprocess (macOS demands GUI on a main thread,
    and ours is busy being an MCP server). Frames stream over stdin.

    If the window dies (system sleep, user closes it, crash), the next update
    respawns it — up to a few times, so a genuinely broken display setup
    degrades to headless instead of a respawn loop. The game never stops."""

    MAX_RESPAWNS = 5

    def __init__(self, width: int, height: int, scale: int, title: str = "PIXEL FLIPPERS 🦭 — DS"):
        self._args = (width, height, scale, title)
        self._respawns = 0
        self._proc = self._spawn()

    def _spawn(self):
        import subprocess
        import sys

        width, height, scale, title = self._args
        return subprocess.Popen(
            [sys.executable, "-m", "pixel_flippers.viewer_proc",
             str(width), str(height), str(scale), title],
            stdin=subprocess.PIPE,
        )

    def update(self, frame: np.ndarray) -> None:
        if self._proc is None:
            return
        try:
            self._proc.stdin.write(frame.tobytes())
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            if self._respawns < self.MAX_RESPAWNS:
                self._respawns += 1
                try:
                    self._proc.terminate()
                except OSError:
                    pass
                self._proc = self._spawn()
            else:
                self._proc = None  # window keeps dying — play headless

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.terminate()
        except OSError:
            pass


class NDSBackend:
    capabilities = {"buttons", "frames", "screenshot", "savestates"}

    def __init__(self, rom_path: Path, window: str = "SDL2", scale: int = 3, player: str = ""):
        import stable_retro as retro

        if rom_path.suffix.lower() != ".nds":
            raise EmulatorError(f"DS backend needs a .nds file, got {rom_path.name}")

        self._tmpdir = tempfile.mkdtemp(prefix="pixel-flippers-nds-")
        game_dir = Path(self._tmpdir) / "Custom-NintendoDs"
        game_dir.mkdir()
        shutil.copy(rom_path, game_dir / "rom.nds")
        (game_dir / "data.json").write_text(json.dumps({"info": {}}))
        (game_dir / "scenario.json").write_text(json.dumps({"reward": {}, "done": {}}))
        retro.data.Integrations.add_custom_path(self._tmpdir)

        self._env = retro.make(
            "Custom-NintendoDs",
            state=retro.State.NONE,
            inttype=retro.data.Integrations.CUSTOM_ONLY,
            # Player, not bot: take the raw button MultiBinary space including
            # START, not stable-retro's RL-friendly FILTERED whitelist.
            use_restricted_actions=retro.Actions.ALL,
            render_mode="rgb_array",
        )
        frame, _ = self._env.reset()
        self._frame = self._rgb(frame)
        self._button_index = {
            name.lower(): i for i, name in enumerate(self._env.buttons) if name
        }
        self._num_buttons = self._env.num_buttons

        self._viewer = None
        if window != "null":
            try:
                h, w, _ = self._frame.shape
                title = "PIXEL FLIPPERS 🦭 — DS" + (f" — {player}" if player else "")
                self._viewer = _Viewer(w, h, scale, title)
            except Exception:  # pygame missing or no display — play headless
                self._viewer = None

        self.advance(60)  # let FreeBIOS/the intro get going

    @staticmethod
    def _rgb(frame: np.ndarray) -> np.ndarray:
        # melonDS hands us BGR (mGBA was fine); reorder so the viewer and every
        # screenshot see true colour. ascontiguousarray because a negative-stride
        # view breaks Image.fromarray and makes tobytes() copy anyway.
        return np.ascontiguousarray(frame[:, :, ::-1])

    def _tick(self, frames: int, action: list[int] | None = None) -> None:
        action = action or [0] * self._num_buttons
        for _ in range(frames):
            frame, _, terminated, truncated, _ = self._env.step(action)
            self._frame = self._rgb(frame)
            if terminated or truncated:
                self._env.reset()
            if self._viewer:
                self._viewer.update(self._frame)
                time.sleep(1 / 60)  # watchable speed only when someone's watching

    def press_buttons(self, buttons: list[str], hold_frames: int = 10, wait_frames: int = 30) -> None:
        buttons = [b.strip().lower() for b in buttons]
        bad = [b for b in buttons if b not in self._button_index]
        if bad:
            raise EmulatorError(f"Unknown button(s): {bad}. Valid: {list(NDS_BUTTONS)}")
        for button in buttons:
            action = [0] * self._num_buttons
            action[self._button_index[button]] = 1
            self._tick(hold_frames, action)
            self._tick(8)
        self._tick(wait_frames)

    def advance(self, frames: int) -> None:
        self._tick(frames)

    def screenshot(self) -> Image.Image:
        return Image.fromarray(self._frame)

    def read_memory(self, addr: int) -> int:
        raise EmulatorError("No RAM access on the DS tier yet — vision only")

    def read_range(self, addr: int, length: int) -> bytes:
        raise EmulatorError("No RAM access on the DS tier yet — vision only")

    def save_state(self, path: Path) -> None:
        path.write_bytes(self._env.em.get_state())

    def load_state(self, path: Path) -> None:
        self._env.em.set_state(path.read_bytes())
        self._tick(1)

    def close(self) -> None:
        if self._viewer:
            self._viewer.close()
        self._env.close()
        shutil.rmtree(self._tmpdir, ignore_errors=True)
