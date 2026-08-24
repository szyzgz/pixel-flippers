"""GBA backend via stable-retro's bundled mGBA libretro core.

stable-retro normally wants games pre-registered in its integration library;
we generate a throwaway custom integration around the user's ROM at startup
instead, so any .gba file works. Rendering happens into a small optional
pygame window so the playthrough is watchable; emulation only advances when
tools are called (Pokémon waits — that's the whole trick).

No RAM decoding yet: Gen 3 party data is encrypted and save blocks are
pointer-chased, so Emerald starts as a vision-only tier with save states.
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

GBA_BUTTONS = ("a", "b", "start", "select", "up", "down", "left", "right", "l", "r")


class _Viewer:
    """Spectator window in a subprocess (macOS demands GUI on a main thread,
    and ours is busy being an MCP server). Frames stream over stdin.

    If the window dies (system sleep, user closes it, crash), the next update
    respawns it — up to a few times, so a genuinely broken display setup
    degrades to headless instead of a respawn loop. The game never stops."""

    MAX_RESPAWNS = 5

    def __init__(self, width: int, height: int, scale: int, title: str = "PIXEL FLIPPERS 🦭 — GBA"):
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


class GBABackend:
    capabilities = {"buttons", "frames", "screenshot", "savestates"}

    def __init__(self, rom_path: Path, window: str = "SDL2", scale: int = 3, player: str = ""):
        import stable_retro as retro

        if rom_path.suffix.lower() != ".gba":
            raise EmulatorError(f"GBA backend needs a .gba file, got {rom_path.name}")

        self._tmpdir = tempfile.mkdtemp(prefix="pixel-flippers-gba-")
        game_dir = Path(self._tmpdir) / "Custom-GbAdvance"
        game_dir.mkdir()
        shutil.copy(rom_path, game_dir / "rom.gba")
        (game_dir / "data.json").write_text(json.dumps({"info": {}}))
        (game_dir / "scenario.json").write_text(json.dumps({"reward": {}, "done": {}}))
        retro.data.Integrations.add_custom_path(self._tmpdir)

        self._env = retro.make(
            "Custom-GbAdvance",
            state=retro.State.NONE,
            inttype=retro.data.Integrations.CUSTOM_ONLY,
            # stable-retro defaults to FILTERED actions — an RL-friendly whitelist
            # from the core JSON that omits START (bots shouldn't pause). We're a
            # player, not a bot: take the raw 12-button MultiBinary space.
            use_restricted_actions=retro.Actions.ALL,
            render_mode="rgb_array",
        )
        self._frame, _ = self._env.reset()
        self._button_index = {
            name.lower(): i for i, name in enumerate(self._env.buttons) if name
        }
        self._num_buttons = self._env.num_buttons

        self._viewer = None
        if window != "null":
            try:
                h, w, _ = self._frame.shape
                title = "PIXEL FLIPPERS 🦭 — GBA" + (f" — {player}" if player else "")
                self._viewer = _Viewer(w, h, scale, title)
            except Exception:  # pygame missing or no display — play headless
                self._viewer = None

        self.advance(60)  # let the BIOS/intro get going

    def _tick(self, frames: int, action: list[int] | None = None) -> None:
        action = action or [0] * self._num_buttons
        for _ in range(frames):
            self._frame, _, terminated, truncated, _ = self._env.step(action)
            if terminated or truncated:
                self._env.reset()
            if self._viewer:
                self._viewer.update(self._frame)
                time.sleep(1 / 60)  # watchable speed only when someone's watching

    def press_buttons(self, buttons: list[str], hold_frames: int = 10, wait_frames: int = 30) -> None:
        buttons = [b.strip().lower() for b in buttons]
        bad = [b for b in buttons if b not in self._button_index]
        if bad:
            raise EmulatorError(f"Unknown button(s): {bad}. Valid: {list(GBA_BUTTONS)}")
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
        raise EmulatorError("No RAM access on the GBA tier yet — vision only")

    def read_range(self, addr: int, length: int) -> bytes:
        raise EmulatorError("No RAM access on the GBA tier yet — vision only")

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
