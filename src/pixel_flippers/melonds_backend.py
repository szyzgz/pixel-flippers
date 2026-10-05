"""DS backend #2 — drives standalone melonDS on macOS (window-attach, dual-screen).

Why this exists alongside the in-process `nds` tier: stable-retro's bundled
melonDS core hands us only ONE 256x192 screen, with no core-options API and
locked geometry, so there's no way to ask it for both screens. But HGSS puts the
bag, the main menu, battle commands, AND the name-entry keyboard on the BOTTOM
screen — invisible on a single top screen. Standalone melonDS shows both screens
stacked (256x384), so THIS tier can actually play the DS library.

Puppeted from outside, exactly like the 3DS (Azahar) backend:

- Hands: synthesize the keyboard keys melonDS maps DS buttons to, via macOS
  CGEvent. Needs Accessibility permission. The mapping is READ from melonDS's own
  config (so a rebind doesn't break us), and each key is resolved against the
  ACTIVE keyboard layout — German QWERTZ moves keys, so a fixed US table would
  silently press the wrong button (the exact scar the 3DS backend wears).
- Eyes: screencapture the melonDS window — both screens in one shot. Needs Screen
  Recording permission. Works even when melonDS is NOT the focused window.

Foreground-only hands: CGEvent keystrokes land in the FOCUSED window, so only one
window-attach backend can hold the controls at a time. While this plays, the
Azahar/3DS backend can't, and vice versa — yield the window between players.
(Eyes are unaffected: screencapture by window id needs no focus.)

Display must be awake: macOS stops delivering synthetic KEY events to apps when
the display sleeps (laptop lid closed, even with the system kept awake) — touch
and screencapture still work, keys don't. Matters over Remote Control: keep the
display on to drive it.

Save states use melonDS's own slots (1-8), driven through the File menu, then the
produced state file is copied out to the harness path with a sidecar recording
the slot. In attach mode (no rom_path — pointing at an already-open melonDS) the
state file is named for whatever ROM melonDS currently has open (RecentROM[0]).
Upside over the `nds` tier: standalone melonDS also writes real .sav files beside
the ROM, so IN-GAME saves persist — the thing the stable-retro tier can't do.

Credit: the window-attach skeleton — layout-aware keycodes, _window_bounds,
screencapture capturer, CGEvent key sender, the Escape+retry menu-click save
states — is lifted from Sol's n3ds_backend.py and adapted for melonDS.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import tomllib
from pathlib import Path
from typing import Callable

from PIL import Image

APP_NAME = "melonDS"       # for `open -a` and window matching
PROC_MATCH = "melon"       # CGWindowOwnerName substring
BUTTONS = ("a", "b", "x", "y", "l", "r", "start", "select", "up", "down", "left", "right")

_CONFIG = Path("~/Library/Preferences/melonDS/melonDS.toml").expanduser()

# macOS virtual keycodes that don't move between layouts (arrows + a few keys).
_ARROW_MAC = {"left": 123, "right": 124, "down": 125, "up": 126}
_ARROW_KEYCODES = {123, 124, 125, 126}  # left, right, down, up
# Qt::Key codes melonDS stores for the non-letter keys we care about -> macOS kc.
_QT_ARROWS = {0x01000012: "left", 0x01000013: "up", 0x01000014: "right", 0x01000015: "down"}
_QT_SPECIAL_MAC = {
    0x01000004: 36,   # Return
    0x01000005: 76,   # Enter (keypad)
    0x01000003: 51,   # Backspace
    0x20: 49,         # Space
    0x01000001: 48,   # Tab
}
# US-QWERTY letter/digit keycodes — fallback when the active layout can't be read.
_US_CHAR_KC = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29,
    "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
}

# CGEvent synthetic-key flags: NumericPad + SecondaryFn. See _synthetic_flags.
_NUMPAD_FLAG = 0x00200000
_FN_FLAG = 0x00800000


class MelonDSError(Exception):
    pass


def _layout_char_keycodes() -> dict[str, int]:
    """char -> macOS virtual keycode for the ACTIVE keyboard layout, by asking
    CoreGraphics what each keycode 0..127 types right now. A fresh event source
    per keycode keeps dead-key state from bleeding into the next translation.
    (Borrowed from n3ds_backend.)"""
    import Quartz

    table: dict[str, int] = {}
    for kc in range(128):
        src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        ev = Quartz.CGEventCreateKeyboardEvent(src, kc, True)
        if ev is None:
            continue
        _, s = Quartz.CGEventKeyboardGetUnicodeString(ev, 4, None, None)
        if s and len(s) == 1 and s.isprintable():
            table.setdefault(s.lower(), kc)
    return table


def _qt_to_macos(code: int, char2kc: dict[str, int]) -> int | None:
    """Translate a melonDS Qt::Key binding to a macOS virtual keycode."""
    code &= ~0x20000000  # drop Qt::KeypadModifier (arrows are stored Key_x|Keypad)
    if 0x41 <= code <= 0x5A:            # A-Z
        ch = chr(code).lower()
        return char2kc.get(ch, _US_CHAR_KC.get(ch))
    if 0x30 <= code <= 0x39:            # 0-9
        ch = chr(code)
        return char2kc.get(ch, _US_CHAR_KC.get(ch))
    if code in _QT_ARROWS:
        return _ARROW_MAC[_QT_ARROWS[code]]
    if code in _QT_SPECIAL_MAC:
        return _QT_SPECIAL_MAC[code]
    return None


def _synthetic_flags(keycode: int, flags: int) -> int:
    """The CGEvent flags a synthetic key should carry to look like a real press.

    CoreGraphics stamps every synthetic key with NumericPad (0x200000) + Fn
    (0x800000); Qt reads NumericPad as KeypadModifier, so melonDS otherwise sees
    "NumL/NumI/…" and the stock bindings never match. Real Apple keyboards set
    those bits on the ARROW keys but not on letters — so keep them for arrows,
    clear them for everything else, and synthetic events become indistinguishable
    from real keypresses. (Decoded with Sol.)"""
    if keycode in _ARROW_KEYCODES:
        return flags
    return flags & ~(_NUMPAD_FLAG | _FN_FLAG)


def _read_keymap(config_path: Path = _CONFIG) -> dict[str, int]:
    """Read melonDS's keyboard bindings and resolve each DS button to a macOS
    virtual keycode (layout-aware). Raises if a button is unbound (-1)."""
    try:
        char2kc = _layout_char_keycodes()
    except Exception:
        char2kc = {}
    data = tomllib.loads(config_path.read_text())
    kb = data.get("Instance0", {}).get("Keyboard", {})
    codes: dict[str, int] = {}
    unbound: list[str] = []
    for btn in BUTTONS:
        qt = kb.get(btn.capitalize(), -1)
        mac = _qt_to_macos(qt, char2kc) if isinstance(qt, int) and qt >= 0 else None
        if mac is None:
            unbound.append(btn)
        else:
            codes[btn] = mac
    if unbound:
        raise MelonDSError(
            f"melonDS has no usable key binding for: {unbound}. Open melonDS > "
            "Config > Input and hotkeys, bind the DS buttons, then retry."
        )
    return codes


def _read_config(config_path: Path = _CONFIG) -> dict:
    try:
        return tomllib.loads(config_path.read_text())
    except Exception:
        return {}


# --- default macOS hardware implementations (lazy Quartz import) -----------
def _activate_app() -> None:
    subprocess.run(["open", "-a", APP_NAME], check=False)


def _window_bounds() -> tuple[int, int, int, int, int]:
    """(x, y, w, h, window_id) of melonDS's largest on-screen window."""
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGNullWindowID,
        kCGWindowListOptionOnScreenOnly,
    )

    wins = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []
    cands = [w for w in wins if PROC_MATCH in (w.get("kCGWindowOwnerName", "") or "").lower()]
    cands = [w for w in cands if w.get("kCGWindowBounds")]
    if not cands:
        raise MelonDSError("No melonDS window found — is melonDS running with a game loaded?")
    cands.sort(key=lambda w: w["kCGWindowBounds"]["Width"] * w["kCGWindowBounds"]["Height"], reverse=True)
    b = cands[0]["kCGWindowBounds"]
    return int(b["X"]), int(b["Y"]), int(b["Width"]), int(b["Height"]), int(cands[0]["kCGWindowNumber"])


def _default_key_sender(keycode: int, down: bool) -> None:
    import Quartz

    ev = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
    Quartz.CGEventSetFlags(ev, _synthetic_flags(keycode, Quartz.CGEventGetFlags(ev)))
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _default_clicker(abs_x: int, abs_y: int, hold_ms: int) -> None:
    """Tap the DS touch screen at absolute screen coords via CGEvent. melonDS,
    like Azahar, registers a touch only if the cursor is MOVED there first."""
    import Quartz

    pt = Quartz.CGPointMake(abs_x, abs_y)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                       Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, pt, 0))
    time.sleep(0.04)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                       Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, pt, 0))
    time.sleep(max(1, hold_ms) / 1000)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                       Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, pt, 0))


def _default_capturer() -> Image.Image:
    import tempfile

    _, _, _, _, wid = _window_bounds()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        out = f.name
    subprocess.run(["screencapture", "-x", "-o", "-l", str(wid), out], check=True)
    img = Image.open(out).convert("RGB")
    Path(out).unlink(missing_ok=True)
    return img


def _default_menu_click(action: str, slot: int) -> None:
    """Click File > <action> > <slot> in melonDS's menu bar via AppleScript
    (action is "Save state" or "Load state"; slot 1-8). Needs Accessibility.
    Escape twice first — a menu left open breaks the AX reference — and retry on
    error -1728 with an Escape between (the dance Sol hit constantly on Azahar)."""
    esc = 'tell application "System Events" to key code 53'
    subprocess.run(["osascript", "-e", esc], capture_output=True)
    subprocess.run(["osascript", "-e", esc], capture_output=True)
    time.sleep(0.2)
    script = (
        f'tell application "System Events" to tell process "{APP_NAME}" to '
        f'click menu item "{slot}" of menu 1 of menu item "{action}" '
        f'of menu "File" of menu bar 1'
    )
    last = None
    for _ in range(4):
        r = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10, text=True)
        if r.returncode == 0:
            return
        last = r.stderr.strip()
        subprocess.run(["osascript", "-e", esc], capture_output=True)
        time.sleep(0.4)
    raise MelonDSError(f"melonDS menu click (File > {action} > {slot}) failed after retries: {last}")


class MelonDSBackend:
    capabilities = {"buttons", "touch", "screenshot", "savestates"}

    def __init__(
        self,
        rom_path=None,
        player: str = "",
        key_sender: Callable[[int, bool], None] | None = None,
        capturer: Callable[[], Image.Image] | None = None,
        clicker: Callable[[int, int, int], None] | None = None,
        bounds_fn: Callable[[], tuple[int, int, int, int, int]] | None = None,
        activator: Callable[[], None] | None = None,
        menu_click: Callable[[str, int], None] | None = None,
        keymap: dict[str, int] | None = None,
        slot: int = 1,
        max_width: int = 512,
    ):
        self._send_key = key_sender or _default_key_sender
        self._capture = capturer or _default_capturer
        self._click = clicker or _default_clicker
        self._bounds = bounds_fn or _window_bounds
        self._activate = activator or _activate_app
        self._menu = menu_click or _default_menu_click
        self._keymap = keymap if keymap is not None else _read_keymap()
        self.slot = int(slot)
        self._max_width = max_width
        self.player = player
        self._rom = Path(rom_path) if rom_path else None
        if self._rom is not None:
            self._ensure_melonds()

    def _ensure_melonds(self) -> None:
        try:
            self._bounds()  # already up? attach to it.
            return
        except Exception:
            pass
        if not self._rom or not self._rom.is_file():
            raise MelonDSError(f"DS ROM not found: {self._rom}")
        subprocess.Popen(["open", "-a", APP_NAME, str(self._rom)])
        for _ in range(90):  # wait up to ~90s for the window
            time.sleep(1)
            try:
                self._bounds()
                return
            except Exception:
                continue
        raise MelonDSError("melonDS did not open a game window in time")

    def _state_file(self, slot: int) -> Path:
        """Where melonDS writes save-state slot `slot`: <rom-stem>.ml<slot>, in
        the configured SavestatePath if set, else beside the ROM. In attach mode
        (no rom_path) the ROM is whatever melonDS last opened (RecentROM[0])."""
        data = _read_config()
        rom = self._rom or (data.get("RecentROM") or [None])[0]
        if not rom:
            raise MelonDSError("No ROM known for the save state — pass rom_path or open one in melonDS.")
        rom = Path(rom)
        sp = (data.get("SavestatePath") or "").strip()
        base = Path(sp).expanduser() if sp else rom.parent
        return base / f"{rom.stem}.ml{slot}"

    def press_buttons(self, buttons: list[str], hold_ms: int = 90, gap_ms: int = 90) -> None:
        names = [b.strip().lower() for b in buttons]
        bad = [b for b in names if b not in self._keymap]
        if bad:
            raise MelonDSError(f"Unknown button(s): {bad}. Valid: {list(self._keymap)}")
        self._activate()
        time.sleep(0.15)
        for name in names:
            code = self._keymap[name]
            self._send_key(code, True)
            time.sleep(max(1, hold_ms) / 1000)
            self._send_key(code, False)
            time.sleep(max(0, gap_ms) / 1000)

    def touch(self, x: float, y: float, hold_ms: int = 120) -> None:
        """Tap the screen at normalized (x, y) over the captured window image
        (same frame the caller reads coordinates off). On the DS the touch screen
        is the LOWER half, so y>0.5 hits it; the upper half is the (non-touch)
        top screen and taps there do nothing in-game."""
        x = 0.0 if x < 0 else 1.0 if x > 1 else float(x)
        y = 0.0 if y < 0 else 1.0 if y > 1 else float(y)
        self._activate()
        time.sleep(0.15)
        bx, by, bw, bh, _ = self._bounds()
        self._click(int(bx + x * bw), int(by + y * bh), hold_ms)

    def advance(self, frames: int) -> None:
        # Real-time emulator: no frame stepping; approximate "frames" as time.
        time.sleep(max(1, min(frames, 3600)) / 60)

    def screenshot(self) -> Image.Image:
        img = self._capture()
        if img.width > self._max_width:
            scale = self._max_width / img.width
            img = img.resize((self._max_width, int(img.height * scale)))
        return img

    def save_state(self, path: Path) -> None:
        """Save to melonDS slot `self.slot` via the File menu, then copy the
        produced state file out to the harness `path` and write a `.slot.json`
        sidecar recording which slot it came from. (In-game saving is the
        persistent save on this tier; this is the quick-snapshot layer.)"""
        path = Path(path)
        live = self._state_file(self.slot)
        before = live.stat().st_mtime if live.exists() else 0.0
        self._activate()
        time.sleep(0.3)
        self._menu("Save state", self.slot)
        produced = None
        for _ in range(60):  # poll ~6s for a freshly-written slot file
            time.sleep(0.1)
            if live.exists():
                mt = live.stat().st_mtime
                if mt > before and time.time() - mt < 8:
                    produced = live
                    break
        if not produced:
            raise MelonDSError(
                f"Save state produced no file at {live} — is melonDS running a game "
                "and is Accessibility granted?")
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, path)
        path.with_suffix(".slot.json").write_text(
            json.dumps({"slotfile": produced.name, "slot": self.slot}))

    def load_state(self, path: Path) -> None:
        """Copy a saved state back into melonDS's slot file, then load that slot."""
        path = Path(path)
        if not path.exists():
            raise MelonDSError(f"No melonDS save state at {path}")
        slot = self.slot
        sidecar = path.with_suffix(".slot.json")
        if sidecar.exists():
            try:
                slot = int(json.loads(sidecar.read_text()).get("slot", self.slot))
            except Exception:
                pass
        live = self._state_file(slot)
        live.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, live)
        self._activate()
        time.sleep(0.3)
        self._menu("Load state", slot)
        time.sleep(1.2)

    def close(self) -> None:
        # Leave melonDS running — the player may want to keep watching; the app
        # owns its own lifecycle and its .sav persists regardless.
        pass
