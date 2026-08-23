"""Emulator backends.

Two implementations of the same small interface:

- PyBoyEmulator: real Game Boy emulation via PyBoy, with a background thread
  that keeps ticking at real-time speed so the SDL window stays alive and
  watchable between tool calls.
- MockEmulator: no emulator at all — canned RAM and a generated screenshot,
  used for tests and for verifying Claude Desktop plumbing without a ROM.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw

BUTTONS = ("a", "b", "start", "select", "up", "down", "left", "right")


class EmulatorError(Exception):
    pass


def validate_buttons(buttons: list[str]) -> list[str]:
    normalized = [b.strip().lower() for b in buttons]
    bad = [b for b in normalized if b not in BUTTONS]
    if bad:
        raise EmulatorError(f"Unknown button(s): {bad}. Valid: {list(BUTTONS)}")
    return normalized


EMULATOR_CAPABILITIES = {"buttons", "frames", "screenshot", "memory", "savestates"}


class PyBoyEmulator:
    capabilities = EMULATOR_CAPABILITIES

    def __init__(self, rom_path: Path, window: str = "SDL2", scale: int = 3, speed: int = 1):
        from pyboy import PyBoy  # imported lazily so mock mode needs no pyboy

        self._pyboy = PyBoy(str(rom_path), window=window, scale=scale)
        self._pyboy.set_emulation_speed(speed)
        self._headless = window == "null"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        # Idle ticking keeps the window responsive and lets the spectator see
        # idle animations; game logic in Pokémon waits for input regardless.
        self._thread = threading.Thread(target=self._idle_loop, daemon=True)
        self._thread.start()

    def _idle_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                self._pyboy.tick(1, True)
            if self._headless:
                time.sleep(1 / 60)

    def press_buttons(self, buttons: list[str], hold_frames: int = 10, wait_frames: int = 30) -> None:
        buttons = validate_buttons(buttons)
        with self._lock:
            for button in buttons:
                self._pyboy.button_press(button)
                self._pyboy.tick(hold_frames, True)
                self._pyboy.button_release(button)
                self._pyboy.tick(8, True)
            self._pyboy.tick(wait_frames, True)

    def advance(self, frames: int) -> None:
        with self._lock:
            self._pyboy.tick(frames, True)

    def screenshot(self) -> Image.Image:
        with self._lock:
            return self._pyboy.screen.image.copy().convert("RGB")

    def read_memory(self, addr: int) -> int:
        with self._lock:
            return self._pyboy.memory[addr]

    def read_range(self, addr: int, length: int) -> bytes:
        with self._lock:
            return bytes(self._pyboy.memory[addr : addr + length])

    def save_state(self, path: Path) -> None:
        with self._lock, open(path, "wb") as f:
            self._pyboy.save_state(f)

    def load_state(self, path: Path) -> None:
        with self._lock, open(path, "rb") as f:
            self._pyboy.load_state(f)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        with self._lock:
            self._pyboy.stop(save=False)


class MockEmulator:
    """Fake Game Boy: 64 KiB of RAM you can preseed, plus a drawn screenshot.

    Ships with just enough Pokémon Red state for the decoder to produce a
    plausible report, so the whole toolchain can be exercised ROM-free.
    """

    capabilities = EMULATOR_CAPABILITIES

    def __init__(self) -> None:
        self.memory = bytearray(0x10000)
        self.frame = 0
        self.presses: list[str] = []
        self._seed_pokemon_red()

    def _seed_pokemon_red(self) -> None:
        def poke_text(addr: int, text: str) -> None:
            from .pokemon_red import encode_text

            data = encode_text(text)
            self.memory[addr : addr + len(data)] = data

        m = self.memory
        poke_text(0xD158, "PIXEL")  # player name
        m[0xD163] = 1  # party count
        m[0xD164] = 0x99  # species (internal index, unused by decoder)
        m[0xD165] = 0xFF  # party species terminator
        mon = 0xD16B
        m[mon + 0x01 : mon + 0x03] = (18).to_bytes(2, "big")  # current HP
        m[mon + 0x21] = 6  # level
        m[mon + 0x22 : mon + 0x24] = (20).to_bytes(2, "big")  # max HP
        poke_text(0xD2B5, "SPARKY")  # nickname
        m[0xD347:0xD34A] = bytes([0x00, 0x30, 0x00])  # ¥3000 in BCD
        m[0xD356] = 0b00000001  # Boulder Badge
        m[0xD35E] = 0x02  # Pewter City
        m[0xD361] = 5  # Y
        m[0xD362] = 10  # X
        m[0xD31D] = 2  # item count
        m[0xD31E:0xD322] = bytes([0x04, 5, 0x14, 3])  # 5 Poké Balls, 3 Potions
        m[0xD322] = 0xFF

    def press_buttons(self, buttons: list[str], hold_frames: int = 10, wait_frames: int = 30) -> None:
        self.presses.extend(validate_buttons(buttons))
        self.frame += len(buttons) * (hold_frames + 8) + wait_frames

    def advance(self, frames: int) -> None:
        self.frame += frames

    def screenshot(self) -> Image.Image:
        img = Image.new("RGB", (160, 144), (155, 188, 15))  # DMG green
        draw = ImageDraw.Draw(img)
        draw.rectangle([4, 4, 155, 139], outline=(15, 56, 15))
        draw.text((10, 10), "MOCK GAME BOY", fill=(15, 56, 15))
        draw.text((10, 30), f"frame {self.frame}", fill=(15, 56, 15))
        draw.text((10, 50), f"last: {self.presses[-3:]}", fill=(15, 56, 15))
        return img

    def read_memory(self, addr: int) -> int:
        return self.memory[addr]

    def read_range(self, addr: int, length: int) -> bytes:
        return bytes(self.memory[addr : addr + length])

    def save_state(self, path: Path) -> None:
        path.write_bytes(bytes(self.memory))

    def load_state(self, path: Path) -> None:
        data = path.read_bytes()
        self.memory[: len(data)] = data

    def close(self) -> None:
        pass
