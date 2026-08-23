"""The Mac side of the real-hardware tier.

Hands: JSON-over-TCP to the bridge on the Raspberry Pi (switch_bridge.py),
which speaks Pro Controller to the Switch via NXBT.
Eyes: an HDMI capture card read as a UVC webcam (OpenCV), Switch docked
through it. There is no RAM access on real hardware — vision only.
"""

from __future__ import annotations

import json
import socket
from typing import Callable

from PIL import Image

from .switch_bridge import NXBT_BUTTONS, STICKS

SWITCH_BUTTONS = tuple(NXBT_BUTTONS)


class SwitchError(Exception):
    pass


def default_capture(index: int) -> Callable[[], Image.Image]:
    import cv2  # optional dependency: pip install .[switch]

    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise SwitchError(f"No capture device at index {index} — is the card plugged in?")

    def grab() -> Image.Image:
        # flush a couple of buffered frames so we see "now", not 100ms ago
        for _ in range(2):
            cap.grab()
        ok, frame = cap.read()
        if not ok:
            raise SwitchError("Capture device returned no frame")
        return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

    return grab


class SwitchBackend:
    """Same duck-type shape as the emulators, minus RAM and save states."""

    capabilities = {"buttons", "stick", "screenshot"}

    def __init__(
        self,
        bridge_host: str,
        bridge_port: int,
        capture: Callable[[], Image.Image] | None = None,
        capture_index: int = 0,
        timeout: float = 10.0,
    ):
        self._addr = (bridge_host, bridge_port)
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._file = None
        self._capture = capture if capture is not None else default_capture(capture_index)
        self._send({"cmd": "ping"})  # fail fast if the Pi isn't there

    def _connect(self) -> None:
        self._sock = socket.create_connection(self._addr, timeout=self._timeout)
        self._file = self._sock.makefile("rwb")

    def _send(self, cmd: dict) -> dict:
        for attempt in (1, 2):  # one transparent reconnect
            try:
                if self._sock is None:
                    self._connect()
                self._file.write((json.dumps(cmd) + "\n").encode())
                self._file.flush()
                line = self._file.readline()
                if not line:
                    raise ConnectionError("bridge closed the connection")
                break
            except (OSError, ConnectionError) as exc:
                self.close()
                if attempt == 2:
                    raise SwitchError(f"Bridge unreachable at {self._addr[0]}:{self._addr[1]}: {exc}") from exc
        response = json.loads(line)
        if not response.get("ok"):
            raise SwitchError(response.get("error", "bridge error"))
        return response

    def press_buttons(self, buttons: list[str], hold_ms: int = 100, gap_ms: int = 80) -> None:
        buttons = [b.strip().lower() for b in buttons]
        bad = [b for b in buttons if b not in NXBT_BUTTONS]
        if bad:
            raise SwitchError(f"Unknown button(s): {bad}. Valid: {list(SWITCH_BUTTONS)}")
        self._send({"cmd": "press", "buttons": buttons, "hold_ms": hold_ms, "gap_ms": gap_ms})

    def move_stick(self, stick: str, x: int, y: int, duration_ms: int = 300) -> None:
        if stick not in STICKS:
            raise SwitchError("stick must be 'left' or 'right'")
        self._send({"cmd": "stick", "stick": stick, "x": x, "y": y, "duration_ms": duration_ms})

    def screenshot(self) -> Image.Image:
        return self._capture()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock, self._file = None, None
