"""GBA backend tests — run against the REAL mGBA core, no mocks.

The ROM is hand-assembled in-test: a header stub plus a few ARM instructions
that set video mode 3 and fill part of VRAM, so we can verify the emulator
actually boots, renders, steps, and save-states.
"""

import struct

import pytest

retro = pytest.importorskip("stable_retro")

from pixel_flippers.config import Config
from pixel_flippers.emulator import EmulatorError
from pixel_flippers.gba_backend import GBABackend
from pixel_flippers.server import Harness, build_server
from pixel_flippers.vault import Vault


def make_test_rom(path):
    rom = bytearray(0xC0)
    rom[0:4] = struct.pack("<I", 0xEA00002E)  # b past the header
    rom[0xA0:0xAC] = b"PIXELFLIPPER"
    code = [
        0xE3A00301,  # mov r0, #0x04000000 (REG_DISPCNT)
        0xE3A01B01,  # mov r1, #0x400
        0xE3811003,  # orr r1, r1, #3 (mode 3 | BG2)
        0xE5801000,  # str r1, [r0]
        0xE3A02406,  # mov r2, #0x06000000 (VRAM)
        0xE3A030FF,  # mov r3, #0xFF
        0xE3A04901,  # mov r4, #0x4000
        0xE4823004,  # str r3, [r2], #4
        0xE2544001,  # subs r4, r4, #1
        0x1AFFFFFC,  # bne loop
        0xEAFFFFFE,  # b .
    ]
    for ins in code:
        rom += struct.pack("<I", ins)
    rom += b"\x00" * (0x1000 - len(rom))
    path.write_bytes(bytes(rom))
    return path


@pytest.fixture(scope="module")
def rom(tmp_path_factory):
    return make_test_rom(tmp_path_factory.mktemp("rom") / "test.gba")


@pytest.fixture(scope="module")
def backend(rom):
    b = GBABackend(rom, window="null")
    yield b
    b.close()


def test_boot_renders_pixels(backend):
    backend.advance(120)
    img = backend.screenshot()
    assert img.size == (240, 160)  # GBA resolution
    assert any(px != (0, 0, 0) for px in img.getdata())


def test_buttons_press_and_reject(backend):
    backend.press_buttons(["a", "start", "l"], hold_frames=2, wait_frames=2)
    with pytest.raises(EmulatorError, match="konami"):
        backend.press_buttons(["konami"])


def test_savestate_roundtrip(backend, tmp_path):
    state = tmp_path / "s.state"
    backend.save_state(state)
    assert state.stat().st_size > 0
    backend.advance(30)
    backend.load_state(state)


def test_memory_tools_absent(backend):
    with pytest.raises(EmulatorError):
        backend.read_memory(0x02000000)


def test_gba_harness_tool_surface(rom, backend, tmp_path):
    # stable-retro allows one emulator per process, so reuse the module fixture
    config = Config.from_env(
        {
            "PIXEL_FLIPPERS_BACKEND": "gba",
            "PIXEL_FLIPPERS_ROM": str(rom),
            "PIXEL_FLIPPERS_WINDOW": "null",
            "PIXEL_FLIPPERS_SAVES": str(tmp_path / "saves"),
            "PIXEL_FLIPPERS_VAULT": str(tmp_path / "vault"),
        }
    )
    assert config.game == "none"
    harness = Harness(config, backend, Vault(config.vault_path))

    import anyio

    tools = {t.name for t in anyio.run(build_server(harness).list_tools)}
    assert {"press_buttons", "wait", "get_screenshot", "save_state", "load_state"} <= tools
    assert not {"read_game_state", "read_memory", "move_stick", "freeze"} & tools

    result = harness.press_buttons(["a"], 2, 2)
    assert "Pressed: a" in result
    assert harness.screenshot().startswith(b"\x89PNG")
    assert "Saved" in harness.save_state("gba-test")


def test_start_button_not_filtered(backend):
    """stable-retro's default FILTERED mode silently drops START; we must use ALL."""
    import stable_retro as retro

    assert backend._env.unwrapped.use_restricted_actions == retro.Actions.ALL
    assert "start" in backend._button_index


def test_viewer_respawns_after_death(monkeypatch):
    """A dead spectator window must be respawned on the next frame, not abandoned."""
    import numpy as np

    from pixel_flippers.gba_backend import _Viewer

    spawned = []

    class FakeStdin:
        def __init__(self, broken):
            self.broken = broken
        def write(self, data):
            if self.broken:
                raise BrokenPipeError
        def flush(self):
            pass
        def close(self):
            pass

    class FakeProc:
        def __init__(self, broken):
            self.stdin = FakeStdin(broken)
        def terminate(self):
            pass

    def fake_spawn(self):
        broken = len(spawned) == 0  # first process is dead, respawn is healthy
        proc = FakeProc(broken)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(_Viewer, "_spawn", fake_spawn)
    v = _Viewer(240, 160, 3)
    frame = np.zeros((160, 240, 3), dtype=np.uint8)
    v.update(frame)  # hits the broken pipe → respawn
    assert len(spawned) == 2 and v._proc is spawned[1]
    v.update(frame)  # healthy now, no further respawn
    assert len(spawned) == 2
    v.close()
