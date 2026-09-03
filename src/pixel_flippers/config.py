"""Configuration, loaded from environment variables.

Claude Desktop passes env vars through the `env` block of its MCP server
config, so everything is configured that way — no config file to locate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

TRUTHY = {"1", "true", "yes", "on"}


BACKENDS = ("pyboy", "gba", "mock", "switch", "n3ds")


@dataclass
class Config:
    backend: str  # "pyboy" | "mock" | "switch"
    rom_path: Path | None
    vault_path: Path | None
    saves_dir: Path
    game: str  # "pokemon_red" or "none"
    window: str  # "SDL2" (visible, watchable) or "null" (headless)
    scale: int
    speed: int  # emulation speed multiplier; 1 = real time, 0 = unbounded
    bridge_host: str  # switch backend: the Raspberry Pi running switch_bridge
    bridge_port: int
    capture_index: int  # switch backend: UVC capture card device index
    transport: str = "stdio"  # "stdio" (Claude Desktop) or "http" (local service; use the `pf` CLI)
    port: int = 8765  # http transport port (binds 127.0.0.1 only)
    player: str = ""  # optional player name — shown in the spectator window title

    @property
    def mock(self) -> bool:
        return self.backend == "mock"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env

        backend = env.get("PIXEL_FLIPPERS_BACKEND", "").lower()
        if env.get("PIXEL_FLIPPERS_MOCK", "").lower() in TRUTHY:
            backend = backend or "mock"
        backend = backend or "pyboy"
        if backend not in BACKENDS:
            raise SystemExit(f"PIXEL_FLIPPERS_BACKEND must be one of {BACKENDS}, got {backend!r}")

        rom = env.get("PIXEL_FLIPPERS_ROM")
        vault = env.get("PIXEL_FLIPPERS_VAULT")
        saves = env.get("PIXEL_FLIPPERS_SAVES", "")

        rom_path = Path(rom).expanduser() if rom else None
        if backend in ("pyboy", "gba"):
            if rom_path is None:
                raise SystemExit(
                    "PIXEL_FLIPPERS_ROM is not set. Point it at your own ROM dump, "
                    "or set PIXEL_FLIPPERS_MOCK=1 to run without an emulator."
                )
            if not rom_path.is_file():
                raise SystemExit(f"ROM not found: {rom_path}")

        saves_dir = (
            Path(saves).expanduser()
            if saves
            else (rom_path.parent / "saves" if rom_path else Path("saves"))
        )

        # gba has no RAM decoder yet (Gen 3 is encrypted + pointer-chased)
        default_game = "pokemon_red" if backend in ("pyboy", "mock") else "none"
        return cls(
            backend=backend,
            rom_path=rom_path,
            vault_path=Path(vault).expanduser() if vault else None,
            saves_dir=saves_dir,
            game=env.get("PIXEL_FLIPPERS_GAME", default_game),
            window=env.get("PIXEL_FLIPPERS_WINDOW", "SDL2"),
            scale=int(env.get("PIXEL_FLIPPERS_SCALE", "3")),
            speed=int(env.get("PIXEL_FLIPPERS_SPEED", "1")),
            bridge_host=env.get("PIXEL_FLIPPERS_BRIDGE_HOST", "raspberrypi.local"),
            bridge_port=int(env.get("PIXEL_FLIPPERS_BRIDGE_PORT", "3000")),
            capture_index=int(env.get("PIXEL_FLIPPERS_CAPTURE", "0")),
            transport=env.get("PIXEL_FLIPPERS_TRANSPORT", "stdio").lower(),
            port=int(env.get("PIXEL_FLIPPERS_PORT", "8765")),
            player=env.get("PIXEL_FLIPPERS_PLAYER", ""),
        )
