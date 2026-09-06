"""3DS/Azahar backend tests — pure logic with injected fake hardware."""

import pytest
from PIL import Image

from pixel_flippers.config import Config
from pixel_flippers.n3ds_backend import (
    KEYCODES,
    N3DS_BUTTONS,
    N3dsBackend,
    N3dsError,
    clamp01,
    validate_buttons,
)
from pixel_flippers.server import Harness, build_server
from pixel_flippers.vault import Vault


class FakeHW:
    """Records key/click events; serves a fixed window geometry + image."""

    def __init__(self, bounds=(100, 50, 800, 600, 42)):
        self.keys = []       # (keycode, down)
        self.clicks = []      # (abs_x, abs_y, hold_ms)
        self.activated = 0
        self._bounds = bounds

    def key(self, code, down):
        self.keys.append((code, down))

    def click(self, x, y, hold):
        self.clicks.append((x, y, hold))

    def bounds(self):
        return self._bounds

    def activate(self):
        self.activated += 1

    def capture(self):
        return Image.new("RGB", (1600, 1200), (20, 30, 40))


def make_backend(hw):
    return N3dsBackend(
        key_sender=hw.key, capturer=hw.capture, clicker=hw.click,
        bounds_fn=hw.bounds, activator=hw.activate,
    )


def test_keymap_is_complete_and_distinct():
    for b in ("a", "b", "x", "y", "l", "r", "zl", "zr", "start", "select",
              "home", "up", "down", "left", "right", "dup", "ddown", "dleft", "dright"):
        assert b in KEYCODES
    assert len(set(KEYCODES.values())) == len(KEYCODES)  # no key collisions


def test_validate_buttons():
    assert validate_buttons(["A", " b ", "Start"]) == ["a", "b", "start"]
    with pytest.raises(N3dsError, match="konami"):
        validate_buttons(["konami"])


def test_press_sequence_sends_down_then_up_per_button():
    hw = FakeHW()
    b = make_backend(hw)
    b.press_buttons(["a", "up"], hold_ms=1, gap_ms=1)
    assert hw.activated == 1
    assert hw.keys == [
        (KEYCODES["a"], True), (KEYCODES["a"], False),
        (KEYCODES["up"], True), (KEYCODES["up"], False),
    ]


def test_movement_maps_to_circle_pad():
    # Overworld movement = Circle Pad, which Azahar maps to the ARROW keys
    # (macOS keycodes: left=123, right=124, down=125, up=126). NOT I/J/K/L
    # (those are the C-stick / camera).
    assert (KEYCODES["up"], KEYCODES["down"], KEYCODES["left"], KEYCODES["right"]) == (126, 125, 123, 124)
    assert (KEYCODES["cup"], KEYCODES["cdown"], KEYCODES["cleft"], KEYCODES["cright"]) == (34, 40, 38, 37)
    assert KEYCODES["dup"] != KEYCODES["up"]  # d-pad distinct from circle pad


def test_touch_maps_normalized_to_window_pixels():
    hw = FakeHW(bounds=(100, 50, 800, 600, 42))
    b = make_backend(hw)
    b.touch(0.5, 0.5)                 # center → (100+400, 50+300)
    b.touch(0.0, 0.0)                 # top-left → window origin
    b.touch(1.0, 1.0)                 # bottom-right → far corner
    assert hw.clicks[0][:2] == (500, 350)
    assert hw.clicks[1][:2] == (100, 50)
    assert hw.clicks[2][:2] == (900, 650)


def test_touch_clamps_out_of_range():
    assert clamp01(-3) == 0.0 and clamp01(9) == 1.0 and clamp01(0.3) == 0.3
    hw = FakeHW()
    make_backend(hw).touch(5.0, -1.0)
    assert hw.clicks[0][:2] == (900, 50)  # clamped to (1.0, 0.0)


def test_screenshot_downscaled():
    hw = FakeHW()
    img = make_backend(hw).screenshot()
    assert img.width == 480  # 1600 -> capped at max_width


def test_config_selects_n3ds_backend():
    cfg = Config.from_env({"PIXEL_FLIPPERS_BACKEND": "n3ds", "PIXEL_FLIPPERS_VAULT": "/tmp/v_n3ds"})
    assert cfg.backend == "n3ds"
    assert cfg.game == "none"


def test_n3ds_tool_surface(tmp_path):
    import anyio

    hw = FakeHW()
    cfg = Config.from_env({
        "PIXEL_FLIPPERS_BACKEND": "n3ds",
        "PIXEL_FLIPPERS_SAVES": str(tmp_path / "s"),
        "PIXEL_FLIPPERS_VAULT": str(tmp_path / "v"),
    })
    harness = Harness(cfg, make_backend(hw), Vault(cfg.vault_path))
    tools = {t.name for t in anyio.run(build_server(harness).list_tools)}
    assert {"press_buttons", "touch", "wait", "get_screenshot"} <= tools
    # vision-only: no RAM or save states on the 3DS tier yet
    assert {"save_state", "load_state"} <= tools  # 3DS now HAS save states
    assert not {"read_game_state", "read_memory", "move_stick", "freeze"} & tools

    assert "Tapped" in harness.touch(0.5, 0.8, 120)
    assert "Pressed: a up" in harness.press_buttons_ms(["a", "up"], 1, 1)


def test_lazy_no_emulator_until_summoned(tmp_path):
    """Server must build tools + not construct the emulator until start_game."""
    import anyio
    from pixel_flippers.config import Config
    from pixel_flippers.server import Harness, build_server

    built = {"n": 0}
    def factory(cfg):
        built["n"] += 1
        return make_backend(FakeHW())

    cfg = Config.from_env({"PIXEL_FLIPPERS_BACKEND": "n3ds",
                           "PIXEL_FLIPPERS_SAVES": str(tmp_path / "s"),
                           "PIXEL_FLIPPERS_VAULT": str(tmp_path / "v")})
    h = Harness(cfg, vault=Vault(cfg.vault_path), emulator_factory=factory)
    server = build_server(h)  # tools resolve from static caps
    tools = {tt.name for tt in anyio.run(server.list_tools)}
    assert {"start_game", "close_game", "press_buttons", "touch"} <= tools
    assert built["n"] == 0 and not h.is_running          # nothing launched yet

    msg = h.start_game()
    assert "Summoned" in msg and h.is_running and built["n"] == 1
    assert "already running" in h.start_game() and built["n"] == 1  # no 2nd window

    assert "Closed" in h.close_game() and not h.is_running


def test_read_last_session_does_not_summon(tmp_path):
    from pixel_flippers.config import Config
    from pixel_flippers.server import Harness

    cfg = Config.from_env({"PIXEL_FLIPPERS_BACKEND": "n3ds",
                           "PIXEL_FLIPPERS_SAVES": str(tmp_path / "s"),
                           "PIXEL_FLIPPERS_VAULT": str(tmp_path / "v")})
    h = Harness(cfg, vault=Vault(cfg.vault_path), emulator_factory=lambda c: make_backend(FakeHW()))
    h.read_last_session()          # must not open a window
    assert not h.is_running


def test_save_and_load_state(tmp_path):
    """Simulate Azahar's menu Save/Load writing a slot file; verify per-player
    copy-out and copy-back-then-load, keyed by the player's slot."""
    from pixel_flippers.n3ds_backend import N3dsBackend

    states = tmp_path / "azahar_states"
    states.mkdir()
    events = {"loaded": 0}
    TITLE = "00040000001B5000"

    def fake_menu(action, slot):
        f = states / f"{TITLE}.{slot:02d}.cst"
        if action == "Save State":
            f.write_bytes(b"SAVESTATE-DATA-v1")
        elif action == "Load State":
            events["loaded"] += 1
            assert f.read_bytes() == b"SAVESTATE-DATA-v1"

    hw = FakeHW()
    b = N3dsBackend(
        key_sender=hw.key, capturer=hw.capture, clicker=hw.click,
        bounds_fn=hw.bounds, activator=hw.activate,
        menu_click=fake_menu, states_dir=states, slot=2,  # Mira-style slot
    )

    dest = tmp_path / "saves" / "mira" / "outside.state"
    b.save_state(dest)
    assert dest.read_bytes() == b"SAVESTATE-DATA-v1"
    meta = dest.with_suffix(".slot.json")
    assert meta.exists() and '"slot": 2' in meta.read_text()

    (states / f"{TITLE}.02.cst").unlink()      # wipe emulator slot
    b.load_state(dest)                          # copies back + clicks Load Slot 2
    assert (states / f"{TITLE}.02.cst").read_bytes() == b"SAVESTATE-DATA-v1"
    assert events["loaded"] == 1


def test_save_state_errors_without_azahar(tmp_path):
    from pixel_flippers.n3ds_backend import N3dsBackend, N3dsError
    hw = FakeHW()
    b = N3dsBackend(
        key_sender=hw.key, capturer=hw.capture, clicker=hw.click,
        bounds_fn=hw.bounds, activator=hw.activate,
        menu_click=lambda a, sl: None,  # no slot file ever appears
        states_dir=tmp_path / "empty_states",
    )
    import pytest
    with pytest.raises(N3dsError, match="no slot file"):
        b.save_state(tmp_path / "x.state")


def test_savestates_now_in_tool_surface(tmp_path):
    import anyio
    hw = FakeHW()
    cfg = Config.from_env({
        "PIXEL_FLIPPERS_BACKEND": "n3ds",
        "PIXEL_FLIPPERS_SAVES": str(tmp_path / "s"),
        "PIXEL_FLIPPERS_VAULT": str(tmp_path / "v"),
    })
    harness = Harness(cfg, make_backend(hw), Vault(cfg.vault_path))
    tools = {t.name for t in anyio.run(build_server(harness).list_tools)}
    assert {"save_state", "load_state", "list_states"} <= tools
