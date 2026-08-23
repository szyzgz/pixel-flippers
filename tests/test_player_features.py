"""Tests for player-requested features: retro-shiny, goals, wake-up ritual, music."""

import pytest

from pixel_flippers import pokemon_red
from pixel_flippers.config import Config
from pixel_flippers.emulator import MockEmulator
from pixel_flippers.pokemon_red import decode_state, is_retro_shiny
from pixel_flippers.server import Harness
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


def _enter_battle(emu, atk_dv, def_dv, spe_dv, spc_dv):
    emu.memory[pokemon_red.IN_BATTLE] = 1
    emu.memory[pokemon_red.ENEMY_LEVEL] = 5
    emu.memory[pokemon_red.ENEMY_DVS] = (atk_dv << 4) | def_dv
    emu.memory[pokemon_red.ENEMY_DVS + 1] = (spe_dv << 4) | spc_dv


def test_retro_shiny_rule():
    assert is_retro_shiny({"atk": 10, "def": 10, "spe": 10, "spc": 10})
    assert is_retro_shiny({"atk": 2, "def": 10, "spe": 10, "spc": 10})
    assert not is_retro_shiny({"atk": 0, "def": 10, "spe": 10, "spc": 10})
    assert not is_retro_shiny({"atk": 10, "def": 9, "spe": 10, "spc": 10})


def test_shiny_alert_in_battle_report():
    emu = MockEmulator()
    _enter_battle(emu, atk_dv=10, def_dv=10, spe_dv=10, spc_dv=10)
    state = decode_state(emu.read_memory)
    assert state["enemy"]["retro_shiny"]
    assert "RETRO-SHINY DETECTED" in pokemon_red.describe(emu.read_memory)
    assert "✨SHINY✨" in pokemon_red.brief(emu.read_memory)


def test_normal_mon_is_not_shiny():
    emu = MockEmulator()
    _enter_battle(emu, atk_dv=7, def_dv=9, spe_dv=3, spc_dv=12)
    assert not decode_state(emu.read_memory)["enemy"]["retro_shiny"]
    assert "RETRO-SHINY" not in pokemon_red.describe(emu.read_memory)


def test_music_in_state():
    emu = MockEmulator()
    emu.memory[pokemon_red.MAP_MUSIC_ID] = 0xB9
    state = decode_state(emu.read_memory)
    assert state["music_id"] == 0xB9
    assert "Music track: 0xB9" in pokemon_red.describe(emu.read_memory)


def test_goal_lifecycle(harness):
    harness.set_goal("Beat Brock")
    harness.set_goal("Catch a Pikachu")
    goals = harness.current_goals()
    assert "- [ ] Beat Brock" in goals and "- [ ] Catch a Pikachu" in goals

    result = harness.complete_goal("brock")
    assert "🎉" in result
    goals = harness.current_goals()
    assert "- [x] Beat Brock" in goals and "- [ ] Catch a Pikachu" in goals


def test_complete_goal_no_match(harness):
    harness.set_goal("Beat Brock")
    assert "No open goal matches" in harness.complete_goal("misty")


def test_complete_goal_before_any_goals(harness):
    assert harness.complete_goal("anything") == "No goals set yet."


def test_read_last_session_assembles_everything(harness):
    harness.vault.write("Status", "In Pewter City, about to fight Brock.")
    harness.set_goal("Beat Brock")
    harness.press_buttons(["up", "a"], 10, 30)

    report = harness.read_last_session()
    assert "## Status" in report and "about to fight Brock" in report
    assert "## Goals" in report and "- [ ] Beat Brock" in report
    assert "## Recent actions" in report and "pressed up a" in report
    assert "## Game right now" in report and "Pewter City" in report


def test_read_last_session_empty_vault(harness):
    report = harness.read_last_session()
    assert "no Status note yet" in report
    assert "no goals yet" in report
    assert "Pewter City" in report
