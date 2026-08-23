"""The Raspberry Pi side: a tiny TCP server that turns JSON commands into
Pro Controller input via NXBT.

Run on the Pi (needs Linux/BlueZ; NXBT wants root for Bluetooth):

    sudo pip install nxbt
    sudo python -m pixel_flippers.switch_bridge --port 3000

Then on the Switch open Controllers → Change Grip/Order so the fake Pro
Controller can pair. Protocol: one JSON object per line, one JSON ack per
line. `--dry-run` skips NXBT entirely and just acks — for testing the wire
protocol without a Pi in the room.
"""

from __future__ import annotations

import argparse
import json
import logging
import socketserver

logger = logging.getLogger("switch_bridge")

# friendly name -> NXBT button constant
NXBT_BUTTONS = {
    "a": "A", "b": "B", "x": "X", "y": "Y",
    "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT",
    "l": "L", "r": "R", "zl": "ZL", "zr": "ZR",
    "plus": "PLUS", "minus": "MINUS", "home": "HOME", "capture": "CAPTURE",
    "lstick": "L_STICK_PRESS", "rstick": "R_STICK_PRESS",
}
STICKS = {"left": "L_STICK", "right": "R_STICK"}


def press_macro(buttons: list[str], hold_ms: int, gap_ms: int) -> str:
    lines = []
    for b in buttons:
        lines.append(f"{NXBT_BUTTONS[b]} {hold_ms / 1000:.3f}s")
        lines.append(f"{gap_ms / 1000:.3f}s")
    return "\n".join(lines)


def stick_macro(stick: str, x: int, y: int, duration_ms: int) -> str:
    return f"{STICKS[stick]}@{x:+04d}{y:+04d} {duration_ms / 1000:.3f}s"


def handle_command(cmd: dict, controller) -> dict:
    kind = cmd.get("cmd")
    if kind == "ping":
        return {"ok": True, "pong": True}
    if kind == "press":
        buttons = [b.lower() for b in cmd["buttons"]]
        bad = [b for b in buttons if b not in NXBT_BUTTONS]
        if bad:
            return {"ok": False, "error": f"unknown buttons {bad}"}
        controller.macro(press_macro(buttons, int(cmd.get("hold_ms", 100)), int(cmd.get("gap_ms", 80))))
        return {"ok": True}
    if kind == "stick":
        stick = cmd.get("stick", "left")
        x, y = int(cmd.get("x", 0)), int(cmd.get("y", 0))
        if stick not in STICKS or not (-100 <= x <= 100 and -100 <= y <= 100):
            return {"ok": False, "error": "stick must be left/right, x/y in -100..100"}
        controller.macro(stick_macro(stick, x, y, int(cmd.get("duration_ms", 300))))
        return {"ok": True}
    return {"ok": False, "error": f"unknown cmd {kind!r}"}


class DryRunController:
    def macro(self, text: str) -> None:
        logger.info("dry-run macro:\n%s", text)


class NxbtController:
    def __init__(self) -> None:
        import nxbt

        self._nxbt = nxbt.Nxbt()
        self._index = self._nxbt.create_controller(nxbt.PRO_CONTROLLER)
        logger.info("Waiting for Switch to pair (open Change Grip/Order)…")
        self._nxbt.wait_for_connection(self._index)
        logger.info("Paired!")

    def macro(self, text: str) -> None:
        self._nxbt.macro(self._index, text, block=True)


def make_handler(controller):
    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            for line in self.rfile:
                line = line.strip()
                if not line:
                    continue
                try:
                    response = handle_command(json.loads(line), controller)
                except (json.JSONDecodeError, KeyError, ValueError) as exc:
                    response = {"ok": False, "error": str(exc)}
                self.wfile.write((json.dumps(response) + "\n").encode())
                self.wfile.flush()

    return Handler


def serve(controller, host: str = "0.0.0.0", port: int = 3000):
    server = socketserver.ThreadingTCPServer((host, port), make_handler(controller))
    server.daemon_threads = True
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="PIXEL FLIPPERS Switch bridge (runs on the Pi)")
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--dry-run", action="store_true", help="no NXBT, just log and ack")
    args = parser.parse_args()

    controller = DryRunController() if args.dry_run else NxbtController()
    server = serve(controller, args.host, args.port)
    logger.info("Bridge listening on %s:%d", args.host, args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
