"""RAM map and state decoder for Pokémon Red/Blue (English).

Addresses are from the community-documented WRAM map (pokered disassembly /
Data Crystal). Everything reads through a `read(addr) -> int` callable so the
decoder is trivially testable and emulator-agnostic.

Instead of shipping the 190-entry internal species index table, we decode
party nicknames — unrenamed Pokémon carry their species name by default, so
names come for free via the text charmap.
"""

from __future__ import annotations

from typing import Callable

Read = Callable[[int], int]

# --- WRAM addresses -------------------------------------------------------
PLAYER_NAME = 0xD158  # 11 bytes, text
PARTY_COUNT = 0xD163
PARTY_MON_DATA = 0xD16B  # 44 bytes per mon
PARTY_MON_SIZE = 44
PARTY_NICKNAMES = 0xD2B5  # 11 bytes per mon, text
ITEM_COUNT = 0xD31D  # followed by (id, qty) pairs, 0xFF terminated
MONEY = 0xD347  # 3 bytes, binary-coded decimal
BADGES = 0xD356  # bitfield, Boulder = bit 0 … Earth = bit 7
CURRENT_MAP = 0xD35E
PLAYER_Y = 0xD361
PLAYER_X = 0xD362
IN_BATTLE = 0xD057  # nonzero while a battle is running
ENEMY_MON = 0xCFE5  # battle struct: species, HP hi/lo at +1/+2
ENEMY_DVS = 0xCFF1  # 2 bytes: Atk<<4|Def, Spe<<4|Spc
ENEMY_LEVEL = 0xCFF3
ENEMY_MAX_HP = 0xCFF4  # 2 bytes
MAP_MUSIC_ID = 0xD35B
MAP_MUSIC_BANK = 0xD35C

# Shininess doesn't exist in Gen 1, but DVs do — and they decide whether a
# mon is shiny when transferred to Gen 2. Same rule Gold/Silver applies:
# Def/Spe/Spc DVs all exactly 10, Atk DV in this set.
SHINY_ATK_DVS = {2, 3, 6, 7, 10, 11, 14, 15}

# Party mon struct offsets
MON_HP = 0x01  # 2 bytes big-endian
MON_STATUS = 0x04
MON_LEVEL = 0x21
MON_MAX_HP = 0x22  # 2 bytes big-endian

BADGE_NAMES = ["Boulder", "Cascade", "Thunder", "Rainbow", "Soul", "Marsh", "Volcano", "Earth"]

STATUS_BITS = [(0x08, "PSN"), (0x10, "BRN"), (0x20, "FRZ"), (0x40, "PAR")]

# Overworld map IDs. Interiors (caves, buildings) fall back to hex — the player can
# name those in the journal.
MAP_NAMES = {
    0x00: "Pallet Town",
    0x01: "Viridian City",
    0x02: "Pewter City",
    0x03: "Cerulean City",
    0x04: "Lavender Town",
    0x05: "Vermilion City",
    0x06: "Celadon City",
    0x07: "Fuchsia City",
    0x08: "Cinnabar Island",
    0x09: "Indigo Plateau",
    0x0A: "Saffron City",
    **{0x0C + i: f"Route {i + 1}" for i in range(25)},
    0x33: "Viridian Forest",
}

ITEM_NAMES = {
    0x01: "Master Ball",
    0x02: "Ultra Ball",
    0x03: "Great Ball",
    0x04: "Poké Ball",
    0x05: "Town Map",
    0x06: "Bicycle",
    0x0B: "Antidote",
    0x0C: "Burn Heal",
    0x0D: "Ice Heal",
    0x0E: "Awakening",
    0x0F: "Parlyz Heal",
    0x10: "Full Restore",
    0x11: "Max Potion",
    0x12: "Hyper Potion",
    0x13: "Super Potion",
    0x14: "Potion",
    0x1D: "Escape Rope",
    0x1E: "Repel",
    0x28: "Rare Candy",
}

# --- Gen 1 text charmap (the subset that appears in names) ----------------
_CHARMAP: dict[int, str] = {0x50: "", 0x7F: " ", 0xE3: "-", 0xE8: "."}
_CHARMAP.update({0x80 + i: chr(ord("A") + i) for i in range(26)})
_CHARMAP.update({0xA0 + i: chr(ord("a") + i) for i in range(26)})
_CHARMAP.update({0xF6 + i: chr(ord("0") + i) for i in range(10)})
_REVERSE_CHARMAP = {v: k for k, v in _CHARMAP.items() if v}


def decode_text(data: bytes) -> str:
    out = []
    for byte in data:
        if byte == 0x50:  # terminator
            break
        out.append(_CHARMAP.get(byte, "?"))
    return "".join(out)


def encode_text(text: str) -> bytes:
    return bytes([_REVERSE_CHARMAP.get(c, 0x7F) for c in text]) + b"\x50"


# --- decoding -------------------------------------------------------------
def _read_range(read: Read, addr: int, length: int) -> bytes:
    return bytes(read(addr + i) for i in range(length))


def _read_u16(read: Read, addr: int) -> int:
    return (read(addr) << 8) | read(addr + 1)


def _read_bcd_money(read: Read) -> int:
    total = 0
    for i in range(3):
        byte = read(MONEY + i)
        total = total * 100 + ((byte >> 4) * 10 + (byte & 0x0F))
    return total


def _decode_dvs(read: Read, addr: int) -> dict:
    b1, b2 = read(addr), read(addr + 1)
    return {"atk": b1 >> 4, "def": b1 & 0xF, "spe": b2 >> 4, "spc": b2 & 0xF}


def is_retro_shiny(dvs: dict) -> bool:
    return (
        dvs["def"] == 10
        and dvs["spe"] == 10
        and dvs["spc"] == 10
        and dvs["atk"] in SHINY_ATK_DVS
    )


def _status_str(status: int) -> str:
    if status & 0x07:
        return " [SLP]"
    flags = [name for bit, name in STATUS_BITS if status & bit]
    return f" [{'/'.join(flags)}]" if flags else ""


def decode_state(read: Read) -> dict:
    party = []
    count = min(read(PARTY_COUNT), 6)
    for i in range(count):
        base = PARTY_MON_DATA + i * PARTY_MON_SIZE
        party.append(
            {
                "name": decode_text(_read_range(read, PARTY_NICKNAMES + i * 11, 11)) or f"MON {i + 1}",
                "level": read(base + MON_LEVEL),
                "hp": _read_u16(read, base + MON_HP),
                "max_hp": _read_u16(read, base + MON_MAX_HP),
                "status": read(base + MON_STATUS),
            }
        )

    items = []
    item_count = min(read(ITEM_COUNT), 20)
    for i in range(item_count):
        item_id = read(ITEM_COUNT + 1 + i * 2)
        if item_id == 0xFF:
            break
        qty = read(ITEM_COUNT + 2 + i * 2)
        if 0xC4 <= item_id <= 0xC8:
            name = f"HM{item_id - 0xC3:02d}"
        elif item_id >= 0xC9:
            name = f"TM{item_id - 0xC8:02d}"
        else:
            name = ITEM_NAMES.get(item_id, f"Item 0x{item_id:02X}")
        items.append({"name": name, "qty": qty})

    badge_bits = read(BADGES)
    map_id = read(CURRENT_MAP)

    state = {
        "player": decode_text(_read_range(read, PLAYER_NAME, 11)),
        "map_id": map_id,
        "map": MAP_NAMES.get(map_id, f"map 0x{map_id:02X} (interior?)"),
        "x": read(PLAYER_X),
        "y": read(PLAYER_Y),
        "money": _read_bcd_money(read),
        "badges": [name for bit, name in enumerate(BADGE_NAMES) if badge_bits & (1 << bit)],
        "party": party,
        "items": items,
        "in_battle": read(IN_BATTLE) != 0,
        "music_id": read(MAP_MUSIC_ID),
        "music_bank": read(MAP_MUSIC_BANK),
    }
    if state["in_battle"]:
        dvs = _decode_dvs(read, ENEMY_DVS)
        state["enemy"] = {
            "level": read(ENEMY_LEVEL),
            "hp": _read_u16(read, ENEMY_MON + 1),
            "max_hp": _read_u16(read, ENEMY_MAX_HP),
            "dvs": dvs,
            "retro_shiny": is_retro_shiny(dvs),
        }
    return state


def describe(read: Read) -> str:
    """Full game-state report as compact markdown."""
    s = decode_state(read)
    lines = [
        f"**{s['player'] or 'Player'}** — {s['map']} (x={s['x']}, y={s['y']})",
        f"Money: ¥{s['money']}  |  Badges ({len(s['badges'])}/8): {', '.join(s['badges']) or 'none'}",
        "",
        "**Party:**",
    ]
    if s["party"]:
        for mon in s["party"]:
            lines.append(
                f"- {mon['name']} Lv{mon['level']} — {mon['hp']}/{mon['max_hp']} HP{_status_str(mon['status'])}"
            )
    else:
        lines.append("- (no Pokémon yet)")
    if s["items"]:
        lines.append("")
        lines.append("**Bag:** " + ", ".join(f"{i['name']} ×{i['qty']}" for i in s["items"]))
    if s["in_battle"]:
        e = s["enemy"]
        lines.append("")
        lines.append(f"**IN BATTLE** — enemy Lv{e['level']}, {e['hp']}/{e['max_hp']} HP")
        if e["retro_shiny"]:
            lines.append(
                "✨ **RETRO-SHINY DETECTED** — this one's DVs would make it SHINY in "
                "Gen 2. A sparkle baby before sparkles existed. DO NOT RUN. CATCH IT."
            )
    lines.append("")
    lines.append(
        f"Music track: 0x{s['music_id']:02X} (bank {s['music_bank']}) — "
        "map vibes in your [[Music Vibes]] note as you learn them"
    )
    return "\n".join(lines)


def brief(read: Read) -> str:
    """One-line summary, appended to action results."""
    s = decode_state(read)
    lead = s["party"][0] if s["party"] else None
    parts = [f"{s['map']} (x={s['x']}, y={s['y']})"]
    if lead:
        parts.append(f"{lead['name']} {lead['hp']}/{lead['max_hp']} HP")
    if s["in_battle"]:
        parts.append("IN BATTLE ✨SHINY✨" if s["enemy"]["retro_shiny"] else "IN BATTLE")
    return " | ".join(parts)
