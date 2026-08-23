"""Real-hardware tier tests: dry-run bridge + backend over a real TCP socket."""

import threading

import pytest
from PIL import Image

from pixel_flippers.config import Config
from pixel_flippers.server import Harness, build_server
from pixel_flippers.switch_backend import SwitchBackend, SwitchError
from pixel_flippers.switch_bridge import DryRunController, press_macro, serve, stick_macro
from pixel_flippers.vault import Vault


@pytest.fixture
def bridge():
    server = serve(DryRunController(), host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address
    server.shutdown()


def fake_capture():
    return Image.new("RGB", (1280, 720), (40, 40, 200))


@pytest.fixture
def backend(bridge):
    host, port = bridge
    b = SwitchBackend(host, port, capture=fake_capture)
    yield b
    b.close()


def test_macros():
    assert press_macro(["a", "up"], 100, 80).splitlines() == [
        "A 0.100s", "0.080s", "DPAD_UP 0.100s", "0.080s",
    ]
    assert stick_macro("left", -100, 0, 500) == "L_STICK@-100+000 0.500s"
    assert stick_macro("right", 5, 100, 300) == "R_STICK@+005+100 0.300s"


def test_press_and_stick_roundtrip(backend):
    backend.press_buttons(["a", "b", "zl", "home"])
    backend.move_stick("left", 0, 100, duration_ms=500)


def test_bad_button_rejected_locally(backend):
    with pytest.raises(SwitchError, match="konami"):
        backend.press_buttons(["konami"])


def test_bad_stick_rejected(backend):
    with pytest.raises(SwitchError):
        backend.move_stick("middle", 0, 0)


def test_screenshot_uses_capture(backend):
    img = backend.screenshot()
    assert img.size == (1280, 720)


def test_unreachable_bridge_fails_fast():
    with pytest.raises(SwitchError, match="unreachable"):
        SwitchBackend("127.0.0.1", 1, capture=fake_capture)


def test_switch_harness_tool_surface(bridge, tmp_path):
    host, port = bridge
    config = Config.from_env(
        {
            "PIXEL_FLIPPERS_BACKEND": "switch",
            "PIXEL_FLIPPERS_BRIDGE_HOST": host,
            "PIXEL_FLIPPERS_BRIDGE_PORT": str(port),
            "PIXEL_FLIPPERS_SAVES": str(tmp_path / "saves"),
            "PIXEL_FLIPPERS_VAULT": str(tmp_path / "vault"),
        }
    )
    assert config.game == "none"
    harness = Harness(config, SwitchBackend(host, port, capture=fake_capture), Vault(config.vault_path))

    import anyio

    tools = {t.name for t in anyio.run(build_server(harness).list_tools)}
    assert {"press_buttons", "move_stick", "get_screenshot", "read_last_session", "set_goal"} <= tools
    # no RAM, no save states on real hardware
    assert not {"read_game_state", "read_memory", "save_state", "load_state", "wait"} & tools

    result = harness.press_buttons_ms(["a"], 100, 80)
    assert "no RAM on real hardware" in result
    assert "screenshot" in harness.read_last_session()


class RecordingController:
    def __init__(self):
        self.macros = []

    def macro(self, text):
        self.macros.append(text)


@pytest.fixture
def recording_setup(tmp_path):
    controller = RecordingController()
    server = serve(controller, host="127.0.0.1", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    config = Config.from_env(
        {
            "PIXEL_FLIPPERS_BACKEND": "switch",
            "PIXEL_FLIPPERS_BRIDGE_HOST": host,
            "PIXEL_FLIPPERS_BRIDGE_PORT": str(port),
            "PIXEL_FLIPPERS_SAVES": str(tmp_path / "saves"),
            "PIXEL_FLIPPERS_VAULT": str(tmp_path / "vault"),
        }
    )
    harness = Harness(config, SwitchBackend(host, port, capture=fake_capture), Vault(config.vault_path))
    yield harness, controller
    server.shutdown()


def test_freeze_screenshots_then_presses_home(recording_setup):
    harness, controller = recording_setup
    png = harness.freeze()
    assert png.startswith(b"\x89PNG")
    assert len(controller.macros) == 1 and "HOME" in controller.macros[0]


def test_resume_presses_home_then_queued_buttons(recording_setup):
    harness, controller = recording_setup
    result = harness.resume(["zr"], 100, 80, resume_delay_ms=0)
    assert "zr" in result
    assert len(controller.macros) == 2
    assert "HOME" in controller.macros[0]
    assert "ZR" in controller.macros[1]


def test_resume_without_buttons(recording_setup):
    harness, controller = recording_setup
    result = harness.resume(None, 100, 80, resume_delay_ms=0)
    assert "LIVE" in result
    assert len(controller.macros) == 1 and "HOME" in controller.macros[0]


def test_freeze_resume_tools_registered_in_switch_mode(recording_setup):
    import anyio

    harness, _ = recording_setup
    tools = {t.name for t in anyio.run(build_server(harness).list_tools)}
    assert {"freeze", "resume"} <= tools
