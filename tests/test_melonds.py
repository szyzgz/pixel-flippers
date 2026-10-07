"""melonDS (window-attach DS) backend tests — pure logic, all hardware faked.

No real melonDS, no Quartz, no live config: keycode translation, the keymap
reader, the synthetic-flag rule, and press/touch/screenshot/save-load driven
through injected fakes. Mirrors test_n3ds's shape.
"""

import json

import pytest

from pixel_flippers import melonds_backend as m
from pixel_flippers.melonds_backend import (
    MelonDSBackend,
    MelonDSError,
    _qt_to_macos,
    _read_keymap,
    _synthetic_flags,
)

NUMPAD = 0x00200000
FN = 0x00800000
KEYPAD = 0x20000000


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Keep the suite instant — the backend sleeps between inputs and while
    polling for save-state files; none of that matters against fakes."""
    monkeypatch.setattr(m.time, "sleep", lambda *a, **k: None)


# --- the synthetic-flag rule (pure) ---------------------------------------
def test_synthetic_flags_clears_for_letters_and_return_keeps_for_arrows():
    base = NUMPAD | FN | 0x1  # 0x1 is an unrelated bit that must survive
    assert _synthetic_flags(37, base) == 0x1   # 'l' (a letter) -> numpad+fn dropped
    assert _synthetic_flags(36, base) == 0x1   # Return -> dropped too
    for kc in (123, 124, 125, 126):            # arrows keep every bit
        assert _synthetic_flags(kc, base) == base


# --- Qt::Key -> macOS keycode ---------------------------------------------
def test_qt_to_macos_arrows_strip_keypad_modifier():
    assert _qt_to_macos(0x01000013, {}) == 126            # Key_Up
    assert _qt_to_macos(0x01000013 | KEYPAD, {}) == 126   # Key_Up | Keypad
    assert _qt_to_macos(0x01000015 | KEYPAD, {}) == 125   # Down
    assert _qt_to_macos(0x01000012 | KEYPAD, {}) == 123   # Left
    assert _qt_to_macos(0x01000014 | KEYPAD, {}) == 124   # Right


def test_qt_to_macos_letters_prefer_active_layout_then_us_fallback():
    assert _qt_to_macos(0x4C, {"l": 99}) == 99   # 'L' resolved via live layout table
    assert _qt_to_macos(0x4C, {}) == 37          # falls back to US keycode for 'l'
    assert _qt_to_macos(0x41 | KEYPAD, {}) == 0  # 'A' | keypad -> 'a' US (0)


def test_qt_to_macos_special_and_unknown():
    assert _qt_to_macos(0x01000004, {}) == 36    # Return
    assert _qt_to_macos(0x20, {}) == 49          # Space
    assert _qt_to_macos(0x01000099, {}) is None  # unmapped -> None


# --- reading the keymap from a melonDS.toml --------------------------------
FULL = {
    "A": 76, "B": 75, "X": 73, "Y": 74, "L": 81, "R": 87,
    "Start": 0x01000004, "Select": 72,
    "Up": 0x01000013 | KEYPAD, "Down": 0x01000015 | KEYPAD,
    "Left": 0x01000012 | KEYPAD, "Right": 0x01000014 | KEYPAD,
}


def _toml_with(bindings: dict) -> str:
    lines = ["[Instance0]", "", "[Instance0.Keyboard]"]
    lines += [f"{k} = {v}" for k, v in bindings.items()]
    return "\n".join(lines) + "\n"


def test_read_keymap_full_set_resolves(tmp_path):
    cfg = tmp_path / "melonDS.toml"
    cfg.write_text(_toml_with(FULL))
    km = _read_keymap(cfg)
    assert set(km) == set(m.BUTTONS)
    assert km["up"] == 126 and km["down"] == 125
    assert km["left"] == 123 and km["right"] == 124
    assert km["a"] == 37       # 'l'
    assert km["start"] == 36   # Return


def test_read_keymap_unbound_button_raises_naming_it(tmp_path):
    partial = dict(FULL)
    partial["X"] = -1
    cfg = tmp_path / "melonDS.toml"
    cfg.write_text(_toml_with(partial))
    with pytest.raises(MelonDSError, match=r"\bx\b"):
        _read_keymap(cfg)


# --- backend methods, with injected fakes ---------------------------------
def test_press_buttons_records_down_up_order_and_rejects_unknown():
    events = []
    b = MelonDSBackend(
        rom_path=None, activator=lambda: None,
        keymap={"a": 37, "up": 126},
        key_sender=lambda kc, down: events.append((kc, down)),
    )
    b.press_buttons(["a", "up"], hold_ms=1, gap_ms=1)
    assert events == [(37, True), (37, False), (126, True), (126, False)]
    with pytest.raises(MelonDSError, match="konami"):
        b.press_buttons(["konami"])


def test_touch_maps_normalized_to_absolute_and_clamps():
    clicks = []
    b = MelonDSBackend(
        rom_path=None, activator=lambda: None, keymap={},
        bounds_fn=lambda: (100, 200, 400, 600, 7),
        clicker=lambda x, y, hold: clicks.append((x, y)),
    )
    b.touch(0.5, 0.75)
    assert clicks[-1] == (300, 650)
    b.touch(-1.0, 2.0)                 # out of range -> clamped to (0, 1)
    assert clicks[-1] == (100, 800)


def test_screenshot_downscales_to_max_width():
    from PIL import Image
    b = MelonDSBackend(
        rom_path=None, activator=lambda: None, keymap={},
        capturer=lambda: Image.new("RGB", (1024, 768)), max_width=512,
    )
    out = b.screenshot()
    assert out.size == (512, 384)


# --- attach-mode ROM-identity guard (two players, one melonDS) -------------
def _attach_backend(rom, player, monkeypatch, recent):
    """Construct a backend that ATTACHES (a window is already up) with a faked
    melonDS recent-ROM list — exercising _verify_open_rom at construction."""
    monkeypatch.setattr(m, "_read_config", lambda *a, **k: ({"RecentROM": recent} if recent is not None else {}))
    return MelonDSBackend(
        rom_path=rom, player=player, activator=lambda: None, keymap={},
        bounds_fn=lambda: (0, 0, 620, 960, 1),  # a window exists -> attach path
    )


def test_attach_refuses_when_a_different_rom_is_open(tmp_path, monkeypatch):
    rom = tmp_path / "Ours.nds"
    rom.write_bytes(b"\x00")
    with pytest.raises(MelonDSError, match="SomeoneElse"):
        _attach_backend(rom, "Fabby", monkeypatch, ["/Users/jackie/roms/SomeoneElse.nds"])


def test_attach_allows_when_our_own_rom_is_open(tmp_path, monkeypatch):
    rom = tmp_path / "Ours.nds"
    rom.write_bytes(b"\x00")
    _attach_backend(rom, "Claudie", monkeypatch, [str(rom)])  # no raise


def test_attach_allows_when_open_rom_is_unknown(tmp_path, monkeypatch):
    rom = tmp_path / "Ours.nds"
    rom.write_bytes(b"\x00")
    _attach_backend(rom, "Claudie", monkeypatch, [])   # empty recent list -> don't block
    _attach_backend(rom, "Claudie", monkeypatch, None)  # no config at all -> don't block


def test_save_and_load_state_roundtrip(tmp_path, monkeypatch):
    # no real config: SavestatePath empty -> state lives beside the ROM (tmp)
    monkeypatch.setattr(m, "_read_config", lambda *a, **k: {})
    rom = tmp_path / "MyGame.nds"
    rom.write_bytes(b"\x00")
    live = rom.parent / "MyGame.ml1"

    menu_calls = []

    def fake_menu(action, slot):
        menu_calls.append((action, slot))
        if action == "Save state":          # simulate melonDS writing the slot file
            (rom.parent / f"MyGame.ml{slot}").write_bytes(b"STATE")

    b = MelonDSBackend(
        rom_path=rom, activator=lambda: None, keymap={},
        bounds_fn=lambda: (0, 0, 620, 960, 1),   # so _ensure attaches, never launches
        menu_click=fake_menu, slot=1,
    )

    out = tmp_path / "review.state"
    b.save_state(out)
    assert out.read_bytes() == b"STATE"
    meta = json.loads(out.with_suffix(".slot.json").read_text())
    assert meta == {"slotfile": "MyGame.ml1", "slot": 1}
    assert ("Save state", 1) in menu_calls

    live.unlink()                           # prove load re-creates the live slot file
    b.load_state(out)
    assert live.read_bytes() == b"STATE"
    assert ("Load state", 1) in menu_calls
