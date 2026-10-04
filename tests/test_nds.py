"""DS backend tests, in two layers.

Core-free unit tests run always: the extension guard, the BGR->RGB channel fix,
the button set lining up with the melonDS core, and the config wiring for `nds`.

A real-core boot test runs only when PIXEL_FLIPPERS_NDS_TEST_ROM points at a
.nds dump (no commercial ROM ships in CI), mirroring test_gba's behaviours. A
hand-assembled synthetic .nds that renders a pixel — so this boots with no env
var, like the GBA suite — is a planned follow-up.
"""

import json
import os
import pathlib

import numpy as np
import pytest

retro = pytest.importorskip("stable_retro")

from pixel_flippers.config import BACKEND_CAPABILITIES, BACKENDS, Config
from pixel_flippers.emulator import EmulatorError
from pixel_flippers.nds_backend import NDS_BUTTONS, NDSBackend


def _core_buttons():
    """The button list straight from the bundled melonDS core definition."""
    core = pathlib.Path(retro.__file__).parent / "cores" / "melonds.json"
    return json.loads(core.read_text())["NintendoDs"]["buttons"]


# --- core-free unit tests -------------------------------------------------


def test_rejects_non_nds_rom(tmp_path):
    rom = tmp_path / "game.gba"
    rom.write_bytes(b"\x00")
    with pytest.raises(EmulatorError, match="needs a .nds"):
        NDSBackend(rom)


def test_rgb_swaps_bgr_and_is_contiguous():
    # one pixel carrying B,G,R = (10, 20, 30); after the fix it must read R,G,B.
    bgr = np.array([[[10, 20, 30]]], dtype=np.uint8)
    rgb = NDSBackend._rgb(bgr)
    assert list(rgb[0, 0]) == [30, 20, 10]
    assert rgb.flags["C_CONTIGUOUS"]  # negative-stride views break Image.fromarray


def test_nds_buttons_resolve_against_core():
    core = {name.lower() for name in _core_buttons()}
    missing = [b for b in NDS_BUTTONS if b not in core]
    assert not missing, f"NDS_BUTTONS not in the melonDS core: {missing}"
    # and we deliberately leave the DS-less L2/R2/L3/R3 out of the player set
    assert not ({"l2", "r2", "l3", "r3"} & set(NDS_BUTTONS))


def test_config_registers_nds():
    assert "nds" in BACKENDS
    assert BACKEND_CAPABILITIES["nds"] == {"buttons", "frames", "screenshot", "savestates"}


def test_config_requires_rom_for_nds():
    with pytest.raises(SystemExit, match="PIXEL_FLIPPERS_ROM"):
        Config.from_env({"PIXEL_FLIPPERS_BACKEND": "nds"})


def test_config_nds_defaults(tmp_path):
    rom = tmp_path / "hg.nds"
    rom.write_bytes(b"\x00")
    config = Config.from_env(
        {"PIXEL_FLIPPERS_BACKEND": "nds", "PIXEL_FLIPPERS_ROM": str(rom)}
    )
    assert config.game == "none"  # no RAM decoder on this tier
    assert config.shot_width == 480  # falls to the non-3DS default


# --- real-core boot test, env-gated ---------------------------------------

_BOOT_ROM = os.environ.get("PIXEL_FLIPPERS_NDS_TEST_ROM")


@pytest.fixture(scope="module")
def backend():
    if not _BOOT_ROM or not pathlib.Path(_BOOT_ROM).is_file():
        pytest.skip("set PIXEL_FLIPPERS_NDS_TEST_ROM to a .nds dump to run the boot test")
    b = NDSBackend(pathlib.Path(_BOOT_ROM), window="null")
    yield b
    b.close()


def test_boot_renders_pixels(backend):
    backend.advance(600)  # DS BIOS/intro takes longer than the GBA's
    img = backend.screenshot()
    assert img.size == (256, 192)  # one DS screen
    assert any(px != (0, 0, 0) for px in img.getdata())


def test_buttons_press_and_reject(backend):
    backend.press_buttons(["a", "start", "x"], hold_frames=2, wait_frames=2)
    with pytest.raises(EmulatorError, match="konami"):
        backend.press_buttons(["konami"])


def test_savestate_roundtrip(backend, tmp_path):
    state = tmp_path / "s.state"
    backend.save_state(state)
    assert state.stat().st_size > 0
    backend.advance(30)
    backend.load_state(state)


def test_start_button_not_filtered(backend):
    """stable-retro's default FILTERED mode silently drops START; we must use ALL."""
    assert backend._env.unwrapped.use_restricted_actions == retro.Actions.ALL
    assert "start" in backend._button_index
