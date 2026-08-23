from pixel_flippers import pokemon_red
from pixel_flippers.emulator import MockEmulator
from pixel_flippers.pokemon_red import decode_state, decode_text, encode_text


def test_text_roundtrip():
    assert decode_text(encode_text("PIXEL")) == "PIXEL"
    assert decode_text(encode_text("Nidoran-2")) == "Nidoran-2"


def test_decode_state_from_mock():
    emu = MockEmulator()
    state = decode_state(emu.read_memory)
    assert state["player"] == "PIXEL"
    assert state["map"] == "Pewter City"
    assert (state["x"], state["y"]) == (10, 5)
    assert state["money"] == 3000
    assert state["badges"] == ["Boulder"]
    assert not state["in_battle"]

    (mon,) = state["party"]
    assert mon == {"name": "SPARKY", "level": 6, "hp": 18, "max_hp": 20, "status": 0}

    assert state["items"] == [
        {"name": "Poké Ball", "qty": 5},
        {"name": "Potion", "qty": 3},
    ]


def test_battle_state():
    emu = MockEmulator()
    emu.memory[pokemon_red.IN_BATTLE] = 1
    emu.memory[pokemon_red.ENEMY_LEVEL] = 12
    emu.memory[pokemon_red.ENEMY_MON + 1 : pokemon_red.ENEMY_MON + 3] = (30).to_bytes(2, "big")
    emu.memory[pokemon_red.ENEMY_MAX_HP : pokemon_red.ENEMY_MAX_HP + 2] = (41).to_bytes(2, "big")

    state = decode_state(emu.read_memory)
    assert state["in_battle"]
    enemy = state["enemy"]
    assert (enemy["level"], enemy["hp"], enemy["max_hp"]) == (12, 30, 41)
    assert "IN BATTLE" in pokemon_red.describe(emu.read_memory)


def test_describe_and_brief_render():
    emu = MockEmulator()
    full = pokemon_red.describe(emu.read_memory)
    assert "PIXEL" in full and "Pewter City" in full and "¥3000" in full
    assert "SPARKY Lv6 — 18/20 HP" in full

    line = pokemon_red.brief(emu.read_memory)
    assert "Pewter City" in line and "SPARKY 18/20 HP" in line


def test_status_flags():
    emu = MockEmulator()
    emu.memory[0xD16B + pokemon_red.MON_STATUS] = 0x40  # paralyzed
    assert "[PAR]" in pokemon_red.describe(emu.read_memory)
