"""The http transport + `pf` CLI: start a mock server on a free port, drive it."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import pytest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def http_server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pf")
    port = _free_port()
    env = {
        **os.environ,
        "PIXEL_FLIPPERS_MOCK": "1",
        "PIXEL_FLIPPERS_TRANSPORT": "http",
        "PIXEL_FLIPPERS_PORT": str(port),
        "PIXEL_FLIPPERS_SAVES": str(tmp / "saves"),
        "PIXEL_FLIPPERS_VAULT": str(tmp / "vault"),
    }
    proc = subprocess.Popen([sys.executable, "-m", "pixel_flippers.server"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.time() + 20
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        if proc.poll() is not None:
            raise RuntimeError(proc.stderr.read().decode())
        time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("server never listened")
    yield f"http://127.0.0.1:{port}/mcp", tmp
    proc.terminate()
    proc.wait(timeout=10)


def _pf(url, *args):
    return subprocess.run([sys.executable, "-m", "pixel_flippers.cli", "--url", url, *args],
                          capture_output=True, text=True, timeout=60)


def test_tools_listed(http_server):
    url, _ = http_server
    out = _pf(url, "tools")
    assert out.returncode == 0, out.stderr
    assert "press_buttons" in out.stdout and "get_screenshot" in out.stdout


def test_press_and_state(http_server):
    url, _ = http_server
    assert _pf(url, "press", "up", "a").returncode == 0
    out = _pf(url, "call", "read_game_state")
    assert out.returncode == 0, out.stderr
    assert "PIXEL" in out.stdout  # mock trainer name


def test_screenshot_and_notes(http_server):
    url, tmp = http_server
    png = tmp / "shot.png"
    out = _pf(url, "screenshot", str(png))
    assert out.returncode == 0, out.stderr
    assert png.read_bytes()[:4] == b"\x89PNG"
    assert _pf(url, "call", "write_note", '{"title": "Status", "content": "hi"}').returncode == 0
    out = _pf(url, "call", "read_note", '{"title": "Status"}')
    assert "hi" in out.stdout
