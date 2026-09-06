"""3DS backend — drives Azahar (the Citra successor) on macOS.

Unlike PyBoy/GBA there's no in-process emulator: Azahar runs as its own app
and we puppet it from outside, three ways:

- Hands: synthesize the keyboard keys Azahar maps to 3DS buttons (from its
  qt-config.ini), via macOS CGEvent. Needs Accessibility permission.
- Eyes: screencapture the Azahar window. Needs Screen Recording permission.
- Stylus: a `touch(x, y)` tool that clicks into the window; x,y are normalized
  0..1 over the captured image, so the player picks coordinates from the same
  screenshot it sees — no screen-layout math to calibrate.

Vision-only for now (no RAM). Azahar's RPC server can add memory reads later;
this backend declares no "memory" capability, so those tools stay hidden.

Hardware calls (Quartz, screencapture, activation) are injected so the pure
logic is testable without a Mac or a running emulator.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from PIL import Image

APP_NAME = "Azahar"          # for `open -a` and window matching
PROC_MATCH = "azahar"        # CGWindowOwnerName substring

# Save states: the emulator window grabs keyboard for game input, so Qt menu
# HOTKEYS never fire from synthetic keys. Instead we click Emulation > Save/Load
# State > Slot N via AppleScript (reliable), giving each player their own slot
# (Sol=1, Mira=2, ...). Azahar writes states/<title>.0N.cst; we also copy that
# out to the player's saves folder for named, off-Azahar backups.
import os as _os
STATES_DIR = pathlib.Path(_os.path.expanduser(
    "~/Library/Application Support/Azahar/states"))

# 3DS button -> macOS virtual keycode. Movement (up/down/left/right) maps to
# the Circle Pad (Azahar default I/K/J/L) because that's what walks in the
# 3D overworld; the real d-pad is d-prefixed for menus that want it.
KEYCODES: dict[str, int] = {
    "a": 0, "b": 1, "x": 6, "y": 7,
    "l": 12, "r": 13, "zl": 18, "zr": 19,
    "start": 46, "select": 45, "home": 11,
    # Overworld movement = Circle Pad, which Azahar maps to the ARROW keys.
    "up": 126, "down": 125, "left": 123, "right": 124,  # circle pad = arrows
    "dup": 17, "ddown": 5, "dleft": 3, "dright": 4,     # d-pad T G F H (menus)
    "cup": 34, "cdown": 40, "cleft": 38, "cright": 37,  # C-stick I K J L (camera)
}
N3DS_BUTTONS = tuple(KEYCODES)


class N3dsError(Exception):
    pass


def validate_buttons(buttons: list[str]) -> list[str]:
    normalized = [b.strip().lower() for b in buttons]
    bad = [b for b in normalized if b not in KEYCODES]
    if bad:
        raise N3dsError(f"Unknown button(s): {bad}. Valid: {list(N3DS_BUTTONS)}")
    return normalized


def clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else float(v)


# --- default macOS hardware implementations (lazy Quartz import) -----------
def _activate_app() -> None:
    subprocess.run(["open", "-a", APP_NAME], check=False)


def _window_bounds() -> tuple[int, int, int, int, int]:
    """(x, y, w, h, window_id) of Azahar's largest on-screen window."""
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGNullWindowID,
        kCGWindowListOptionOnScreenOnly,
    )

    wins = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []
    cands = [w for w in wins if PROC_MATCH in (w.get("kCGWindowOwnerName", "") or "").lower()]
    cands = [w for w in cands if w.get("kCGWindowBounds")]
    if not cands:
        raise N3dsError("No Azahar window found — is Azahar running with a game loaded?")
    cands.sort(key=lambda w: w["kCGWindowBounds"]["Width"] * w["kCGWindowBounds"]["Height"], reverse=True)
    b = cands[0]["kCGWindowBounds"]
    return int(b["X"]), int(b["Y"]), int(b["Width"]), int(b["Height"]), int(cands[0]["kCGWindowNumber"])


def _default_key_sender(keycode: int, down: bool) -> None:
    import Quartz

    ev = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _default_capturer() -> Image.Image:
    x, y, w, h, wid = _window_bounds()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        out = f.name
    subprocess.run(["screencapture", "-x", "-o", "-l", str(wid), out], check=True)
    img = Image.open(out).convert("RGB")
    Path(out).unlink(missing_ok=True)
    return img


def _default_menu_click(action: str, slot: int) -> None:
    """Click Emulation > <action> > Slot N in Azahar's menu bar via AppleScript.
    Needs Accessibility permission (same one game input uses)."""
    # Escape first: a menu left open from a prior op breaks the AX reference.
    subprocess.run(["osascript", "-e", 'tell application "System Events" to key code 53'],
                   capture_output=True)
    subprocess.run(["osascript", "-e", 'tell application "System Events" to key code 53'],
                   capture_output=True)
    time.sleep(0.2)
    idx = 3 + slot  # submenu: 1=special, 2=Quick, 3=separator, 4=Slot 1, ...
    script = (
        'tell application "System Events" to tell process "azahar" to '
        f'click menu item {idx} of menu 1 of menu item "{action}" '
        'of menu "Emulation" of menu bar 1'
    )
    # -1728 "can't get menu item" happens when a menu is left open; Escape + retry.
    last = None
    for attempt in range(4):
        r = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10, text=True)
        if r.returncode == 0:
            return
        last = r.stderr.strip()
        subprocess.run(["osascript", "-e", 'tell application "System Events" to key code 53'],
                       capture_output=True)
        time.sleep(0.4)
    raise N3dsError(f"Azahar menu click failed after retries: {last}")


def _default_clicker(abs_x: int, abs_y: int, hold_ms: int) -> None:
    import Quartz

    pt = Quartz.CGPointMake(abs_x, abs_y)
    # Azahar only registers a stylus touch if the cursor is MOVED to the point
    # first (it tracks hover); a bare down/up is ignored. Move, then tap.
    Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                       Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, pt, 0))
    time.sleep(0.04)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                       Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, pt, 0))
    time.sleep(max(1, hold_ms) / 1000)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                       Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, pt, 0))


class N3dsBackend:
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
        states_dir: Path | None = None,
        slot: int = 1,
        max_width: int = 480,
    ):
        self._send_key = key_sender or _default_key_sender
        self._menu = menu_click or _default_menu_click
        self._states_dir = Path(states_dir) if states_dir else STATES_DIR
        self.slot = int(slot)
        self._capture = capturer or _default_capturer
        self._click = clicker or _default_clicker
        self._bounds = bounds_fn or _window_bounds
        self._activate = activator or _activate_app
        self._max_width = max_width
        self.player = player
        self._rom = Path(rom_path) if rom_path else None
        # Summon: if a ROM is configured, open Azahar on it (unless already up).
        if self._rom is not None:
            self._ensure_azahar()

    def _ensure_azahar(self) -> None:
        try:
            self._bounds()  # already have a window? attach to it.
            return
        except Exception:
            pass
        if not self._rom or not self._rom.is_file():
            raise N3dsError(f"3DS ROM not found: {self._rom}")
        subprocess.run(["open", "-a", APP_NAME, str(self._rom)], check=False)
        for _ in range(90):  # wait up to ~90s for the window to appear
            time.sleep(1)
            try:
                self._bounds()
                return
            except Exception:
                continue
        raise N3dsError("Azahar did not open a game window in time")

    def press_buttons(self, buttons: list[str], hold_ms: int = 90, gap_ms: int = 90) -> None:
        buttons = validate_buttons(buttons)
        self._activate()
        for name in buttons:
            code = KEYCODES[name]
            self._send_key(code, True)
            time.sleep(max(1, hold_ms) / 1000)
            self._send_key(code, False)
            time.sleep(max(0, gap_ms) / 1000)

    def touch(self, x: float, y: float, hold_ms: int = 120) -> None:
        """Tap the screen at normalized (x, y) over the captured window image."""
        x, y = clamp01(x), clamp01(y)
        self._activate()
        bx, by, bw, bh, _ = self._bounds()
        self._click(int(bx + x * bw), int(by + y * bh), hold_ms)

    def _refocus_game(self) -> None:
        """AppleScript menu ops steal the render widget's keyboard focus; click
        the TOP screen (non-touch) to give it back so game input works again."""
        try:
            bx, by, bw, bh, _ = self._bounds()
            self._click(int(bx + bw * 0.5), int(by + bh * 0.16), 40)
        except Exception:
            pass

    def _slot_file(self):
        """The Azahar slot file for THIS player's slot (states/<title>.0N.cst),
        newest if several titles exist."""
        matches = list(self._states_dir.glob(f"*.{self.slot:02d}.cst"))
        return max(matches, key=lambda f: f.stat().st_mtime) if matches else None

    def save_state(self, path: Path) -> None:
        """Save to this player's Azahar slot, then copy the slot file to `path`."""
        path = Path(path)
        self._activate()
        time.sleep(0.3)
        self._menu("Save State", self.slot)
        slotfile = None
        for _ in range(60):
            time.sleep(0.1)
            f = self._slot_file()
            if f and time.time() - f.stat().st_mtime < 8:
                slotfile = f
                break
        if not slotfile:
            raise N3dsError(
                "Save State produced no slot file — is Azahar running a game and "
                f"is Accessibility granted? (watching {self._states_dir})")
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(slotfile, path)
        path.with_suffix(".slot.json").write_text(
            json.dumps({"slotfile": slotfile.name, "slot": self.slot}))
        self._refocus_game()

    def load_state(self, path: Path) -> None:
        """Copy this player's saved file into their Azahar slot, then load it."""
        path = Path(path)
        sidecar = path.with_suffix(".slot.json")
        if not path.exists() or not sidecar.exists():
            raise N3dsError(f"No 3DS save state at {path}")
        meta = json.loads(sidecar.read_text())
        slot = int(meta.get("slot", self.slot))
        self._states_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, self._states_dir / meta["slotfile"])
        self._activate()
        time.sleep(0.3)
        self._menu("Load State", slot)
        time.sleep(1.2)
        self._refocus_game()

    def advance(self, frames: int) -> None:
        # Real-time: there is no frame stepping. Approximate "frames" as time.
        time.sleep(max(1, min(frames, 3600)) / 60)

    def screenshot(self) -> Image.Image:
        img = self._capture()
        if img.width > self._max_width:
            scale = self._max_width / img.width
            img = img.resize((self._max_width, int(img.height * scale)))
        return img

    def close(self) -> None:
        pass
