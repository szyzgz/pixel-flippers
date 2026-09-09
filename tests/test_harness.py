import pytest

from pixel_flippers.config import Config
from pixel_flippers.emulator import EmulatorError, MockEmulator
from pixel_flippers.server import Harness, build_server
from pixel_flippers.vault import Vault


@pytest.fixture
def harness(tmp_path):
    config = Config.from_env(
        {
            "PIXEL_FLIPPERS_MOCK": "1",
            "PIXEL_FLIPPERS_SAVES": str(tmp_path / "saves"),
            "PIXEL_FLIPPERS_VAULT": str(tmp_path / "vault"),
        }
    )
    return Harness(config, MockEmulator(), Vault(config.vault_path))


def test_press_buttons_returns_brief(harness):
    result = harness.press_buttons(["up", "up", "a"], 10, 30)
    assert "Pressed: up up a" in result
    assert "Pewter City" in result
    assert harness.emulator.presses == ["up", "up", "a"]


def test_press_buttons_rejects_garbage(harness):
    with pytest.raises(EmulatorError):
        harness.press_buttons(["konami"], 10, 30)


def test_actions_are_auto_logged(harness):
    harness.press_buttons(["a"], 10, 30)
    harness.save_state("test")
    (log,) = harness.vault.list_notes()
    text = (harness.vault.root / log).read_text()
    assert "pressed a" in text and "saved state 'test'" in text


def test_screenshot_is_png(harness):
    data = harness.screenshot()
    assert data.startswith(b"\xff\xd8")


def test_game_state_report(harness):
    assert "Boulder" in harness.game_state()


def test_read_memory(harness):
    assert harness.read_memory("D35E", 1) == "02"
    with pytest.raises(ValueError):
        harness.read_memory("D35E", 9999)


def test_save_load_roundtrip(harness):
    harness.save_state("before-brock")
    harness.emulator.memory[0xD35E] = 0x03  # wander to Cerulean
    assert "Cerulean" in harness.game_state()
    assert "Loaded" in harness.load_state("before-brock")
    assert "Pewter" in harness.game_state()
    assert "before-brock" in harness.list_states()


def test_load_missing_state_lists_options(harness):
    harness.save_state("only-one")
    assert "only-one" in harness.load_state("nope")


def test_bad_state_name(harness):
    with pytest.raises(ValueError):
        harness.save_state("../../etc/passwd")


def test_server_builds_with_all_tools(harness):
    import anyio

    server = build_server(harness)
    tools = {t.name for t in anyio.run(server.list_tools)}
    assert {
        "press_buttons", "wait", "get_screenshot", "read_game_state", "read_memory",
        "save_state", "load_state", "list_states",
        "write_note", "append_note", "read_note", "list_notes", "search_notes",
        "read_last_session", "set_goal", "complete_goal", "current_goals",
    } <= tools
