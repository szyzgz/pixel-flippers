"""The MCP server: hands, eyes, and a diary.

Tool logic lives in Harness (plain methods, easy to test); the FastMCP tool
functions are thin wrappers. Runs over stdio for Claude Desktop.
"""

from __future__ import annotations

import io
import logging
import re
import sys
import time

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer

from . import pokemon_red
from .config import BACKEND_CAPABILITIES, Config
from .emulator import BUTTONS, MockEmulator, PyBoyEmulator
from .vault import Vault, VaultError

logger = logging.getLogger("pixel_flippers")


class Harness:
    def __init__(self, config: Config, emulator=None, vault: Vault | None = None,
                 emulator_factory=None):
        self.config = config
        self._emulator = emulator          # None until summoned (lazy)
        self._factory = emulator_factory   # builds the emulator on demand
        self.vault = vault
        self.decoder = pokemon_red if config.game == "pokemon_red" else None
        # Capabilities come from the backend TYPE, so tools register without
        # launching a window. An eagerly-injected emulator (tests) wins.
        self.caps = (
            set(getattr(emulator, "capabilities", set()))
            if emulator is not None
            else set(BACKEND_CAPABILITIES.get(config.backend, set()))
        )
        self.config.saves_dir.mkdir(parents=True, exist_ok=True)

    @property
    def emulator(self):
        """The emulator, built (and its window opened) on first real use."""
        if self._emulator is None:
            if self._factory is None:
                raise RuntimeError("No emulator and no factory configured")
            logger.info("Summoning emulator for %s (%s)", self.config.player, self.config.backend)
            self._emulator = self._factory(self.config)
        return self._emulator

    @property
    def is_running(self) -> bool:
        return self._emulator is not None

    @property
    def game_label(self) -> str:
        if self.decoder:
            return "Pokemon (Game Boy)"
        if self.config.rom_path:
            return self.config.rom_path.stem
        return self.config.backend

    def start_game(self) -> str:
        who = self.config.player or "you"
        if self.is_running:
            return f"{who}'s {self.game_label} is already running. Take a screenshot to see it."
        _ = self.emulator  # triggers construction + window
        return (f"Summoned {who}'s window: {self.game_label}. It's open now — "
                "take a screenshot to see where you are, then play.")

    def close_game(self) -> str:
        if not self.is_running:
            return "No game is running."
        try:
            self._emulator.close()
        finally:
            self._emulator = None
        return f"Closed {self.config.player or 'your'} game window."

    def _log(self, text: str) -> None:
        if self.vault:
            try:
                self.vault.log_action(text)
            except OSError:
                logger.exception("auto-log failed")

    def _brief(self) -> str:
        if not self.is_running:
            return "(game not summoned yet — call start_game to open your window)"
        if not self.decoder or "memory" not in self.caps:
            return "(no RAM state on this backend — take a screenshot to see the world)"
        return self.decoder.brief(self._emulator.read_memory)

    # --- game tools ---
    def press_buttons(self, buttons: list[str], hold_frames: int, wait_frames: int) -> str:
        self.emulator.press_buttons(buttons, hold_frames, wait_frames)
        self._log(f"pressed {' '.join(buttons)}")
        return f"Pressed: {' '.join(buttons)}\nNow: {self._brief()}"

    def press_buttons_ms(self, buttons: list[str], hold_ms: int, gap_ms: int) -> str:
        self.emulator.press_buttons(buttons, hold_ms, gap_ms)
        self._log(f"pressed {' '.join(buttons)}")
        return f"Pressed: {' '.join(buttons)} (no RAM on real hardware — take a screenshot to see the result)"

    def move_stick(self, stick: str, x: int, y: int, duration_ms: int) -> str:
        self.emulator.move_stick(stick, x, y, duration_ms)
        self._log(f"stick {stick} -> ({x}, {y}) for {duration_ms}ms")
        return f"Moved {stick} stick to ({x}, {y}) for {duration_ms}ms"

    def touch(self, x: float, y: float, hold_ms: int) -> str:
        self.emulator.touch(x, y, hold_ms)
        self._log(f"touched ({x:.2f}, {y:.2f})")
        return f"Tapped the touch screen at ({x:.2f}, {y:.2f}). Take a screenshot to see the result."

    def wait_seconds(self, seconds: float) -> str:
        self.emulator.advance(int(max(0.0, min(seconds, 60.0)) * 60))
        return f"Waited {seconds:.1f}s (real time). Take a screenshot to see the current screen."

    # Pause buffering: HOME is a universal suspend on the Switch, which turns
    # any real-time game back into a turn-based one.
    def freeze(self) -> bytes:
        png = self.screenshot()
        self.emulator.press_buttons(["home"], 100, 80)
        self._log("froze game (HOME suspend)")
        return png

    def resume(self, buttons: list[str] | None, hold_ms: int, gap_ms: int, resume_delay_ms: int) -> str:
        self.emulator.press_buttons(["home"], 100, 80)
        time.sleep(max(0, min(resume_delay_ms, 5000)) / 1000)
        if buttons:
            self.emulator.press_buttons(buttons, hold_ms, gap_ms)
            self._log(f"resumed and pressed {' '.join(buttons)}")
            return f"Resumed, then pressed: {' '.join(buttons)}. Freeze again to see the result."
        self._log("resumed game")
        return "Resumed — the game is LIVE and running in real time now."

    def wait(self, frames: int) -> str:
        self.emulator.advance(max(1, min(frames, 3600)))
        return f"Waited {frames} frames.\nNow: {self._brief()}"

    def screenshot(self) -> bytes:
        img = self.emulator.screenshot()
        img = img.resize((img.width * 2, img.height * 2))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def game_state(self) -> str:
        if not self.decoder:
            return "RAM decoding is disabled (PIXEL_FLIPPERS_GAME=none). Use get_screenshot."
        return self.decoder.describe(self.emulator.read_memory)

    def read_memory(self, address: str, length: int) -> str:
        addr = int(address, 16)
        if not 0 <= addr <= 0xFFFF or not 1 <= length <= 256 or addr + length > 0x10000:
            raise ValueError("address must be 0x0000-0xFFFF and length 1-256, within bounds")
        data = self.emulator.read_range(addr, length)
        return " ".join(f"{b:02X}" for b in data)

    # --- save states ---
    def _state_path(self, name: str):
        if not re.fullmatch(r"[\w][\w\- ]{0,60}", name):
            raise ValueError("State names: letters, digits, spaces, - and _ only")
        return self.config.saves_dir / f"{name}.state"

    def save_state(self, name: str) -> str:
        self.emulator.save_state(self._state_path(name))
        self._log(f"saved state '{name}'")
        return f"Saved state '{name}'."

    def load_state(self, name: str) -> str:
        path = self._state_path(name)
        if not path.exists():
            return f"No state named '{name}'. Available: {self.list_states()}"
        self.emulator.load_state(path)
        self._log(f"loaded state '{name}'")
        return f"Loaded state '{name}'.\nNow: {self._brief()}"

    def list_states(self) -> str:
        states = sorted(p.stem for p in self.config.saves_dir.glob("*.state"))
        return ", ".join(states) if states else "(no saved states)"

    # --- journal ---
    def _require_vault(self) -> Vault:
        if not self.vault:
            raise VaultError("No vault configured (set PIXEL_FLIPPERS_VAULT)")
        return self.vault

    # --- goals (a checklist note, so it also renders nicely in Obsidian) ---
    def set_goal(self, text: str) -> str:
        self._require_vault().append("Goals", f"- [ ] {text}")
        self._log(f"new goal: {text}")
        return self.current_goals()

    def complete_goal(self, text: str) -> str:
        vault = self._require_vault()
        try:
            lines = vault.read("Goals").splitlines()
        except VaultError:
            return "No goals set yet."
        needle = text.lower()
        for i, line in enumerate(lines):
            if line.startswith("- [ ]") and needle in line.lower():
                lines[i] = line.replace("- [ ]", "- [x]", 1)
                vault.write("Goals", "\n".join(lines) + "\n")
                self._log(f"completed goal: {line[6:].strip()}")
                return f"Done: {line[6:].strip()} 🎉\n\n" + self.current_goals()
        return f"No open goal matches {text!r}.\n\n" + self.current_goals()

    def current_goals(self) -> str:
        try:
            return self._require_vault().read("Goals").strip() or "(no goals yet)"
        except VaultError:
            return "(no goals yet — set one with set_goal)"

    def read_last_session(self) -> str:
        """Everything needed to reorient after compaction, in one call."""
        vault = self._require_vault()
        sections = []
        try:
            sections.append("## Status\n" + vault.read("Status").strip())
        except VaultError:
            sections.append("## Status\n(no Status note yet — write one!)")
        sections.append("## Goals\n" + self.current_goals())
        logs = [n for n in vault.list_notes() if n.startswith("Journal/Log ")]
        if logs:
            latest = logs[-1]
            tail = (vault.root / latest).read_text(encoding="utf-8").splitlines()[-15:]
            sections.append(f"## Recent actions ({latest})\n" + "\n".join(tail))
        else:
            sections.append("## Recent actions\n(no journal logs yet)")
        sections.append("## Game right now\n" + self._brief())
        return "\n\n".join(sections)


def build_server(harness: Harness) -> MCPServer:
    mcp = MCPServer("pixel-flippers")
    caps = harness.caps

    @mcp.tool()
    def start_game() -> str:
        """Summon YOUR game window. Nothing opens until you call this, so opening
        the app spawns no windows — call this when you're ready to play, then
        take a screenshot. Safe to call again; it won't open a second window."""
        return harness.start_game()

    @mcp.tool()
    def close_game() -> str:
        """Close your game window and free it. Your save state / journal persist."""
        return harness.close_game()

    if "frames" in caps:

        @mcp.tool()
        def press_buttons(buttons: list[str], hold_frames: int = 10, wait_frames: int = 30) -> str:
            """Press Game Boy buttons in sequence, then report a one-line state summary.

            Valid buttons: a, b, start, select, up, down, left, right.
            Movement: one d-pad press moves one tile. hold_frames/wait_frames tune
            how long each press is held and how long to let the game settle after.
            """
            return harness.press_buttons(buttons, hold_frames, wait_frames)

        @mcp.tool()
        def wait(frames: int = 60) -> str:
            """Let the game run for N frames (60 = 1 second) — for dialogs, animations, evolutions."""
            return harness.wait(frames)

    if "stick" in caps:  # real-hardware Switch backend

        @mcp.tool()
        def press_buttons(buttons: list[str], hold_ms: int = 100, gap_ms: int = 80) -> str:
            """Press Switch buttons in sequence on the REAL console via the Pi bridge.

            Valid: a, b, x, y, up, down, left, right, l, r, zl, zr, plus, minus,
            home, capture, lstick, rstick. There is no RAM on real hardware —
            verify results with get_screenshot, and journal what you learn.
            """
            return harness.press_buttons_ms(buttons, hold_ms, gap_ms)

        @mcp.tool()
        def move_stick(stick: str, x: int, y: int, duration_ms: int = 300) -> str:
            """Tilt an analog stick ('left' or 'right'); x and y in -100..100
            (y +100 = up), held for duration_ms. Walking is usually left stick."""
            return harness.move_stick(stick, x, y, duration_ms)

        @mcp.tool()
        def freeze() -> MCPImage:
            """BULLET TIME. Captures the live frame, then presses HOME to suspend the
            game — the whole console pauses and waits for you. Study the returned
            image and plan for as long as you like; real-time games can't rush you
            while frozen. Resume with the `resume` tool. (Does not work in online
            play — which you don't do anyway.)"""
            return MCPImage(data=harness.freeze(), format="png")

        @mcp.tool()
        def resume(buttons: list[str] | None = None, hold_ms: int = 100, gap_ms: int = 80, resume_delay_ms: int = 800) -> str:
            """Unfreeze (HOME again), wait resume_delay_ms for the transition, then
            immediately press the queued buttons — your planned move lands the moment
            the game wakes up, not after another think. Empty buttons = just resume."""
            return harness.resume(buttons, hold_ms, gap_ms, resume_delay_ms)

    if "touch" in caps:  # 3DS (Azahar) — real-time, vision-only, has a stylus

        @mcp.tool()
        def press_buttons(buttons: list[str], hold_ms: int = 90, gap_ms: int = 90) -> str:
            """Press 3DS buttons in sequence on the real Azahar emulator.

            Buttons: a, b, x, y, l, r, zl, zr, start, select, home, and
            up/down/left/right (these walk via the Circle Pad). For menu d-pad
            use dup/ddown/dleft/dright. No RAM yet — screenshot to see results.
            """
            return harness.press_buttons_ms(buttons, hold_ms, gap_ms)

        @mcp.tool()
        def touch(x: float, y: float, hold_ms: int = 120) -> str:
            """Tap the touch screen. x and y are fractions 0..1 of the screenshot
            you see (x=0 left, 1 right; y=0 top, 1 bottom). Look at get_screenshot,
            then aim. This is your stylus."""
            return harness.touch(x, y, hold_ms)

        @mcp.tool()
        def wait(seconds: float = 1.0) -> str:
            """Let the game run for N seconds of real time (cutscenes, animations)."""
            return harness.wait_seconds(seconds)

    @mcp.tool()
    def get_screenshot() -> MCPImage:
        """Current screen as an image. Costs a lot of context — when RAM state is available, prefer read_game_state for plain facts."""
        return MCPImage(data=harness.screenshot(), format="png")

    if "memory" in caps:

        @mcp.tool()
        def read_game_state() -> str:
            """Compact text report straight from game RAM: location, party, HP, money, badges, bag, battle status."""
            return harness.game_state()

        @mcp.tool()
        def read_memory(address: str, length: int = 1) -> str:
            """Read raw bytes from Game Boy memory (hex address like 'D35E'). Escape hatch for the curious."""
            return harness.read_memory(address, length)

    if "savestates" in caps:

        @mcp.tool()
        def save_state(name: str) -> str:
            """Snapshot the entire game to a named save state. Save before risky fights and at milestones."""
            return harness.save_state(name)

        @mcp.tool()
        def load_state(name: str) -> str:
            """Restore a named save state."""
            return harness.load_state(name)

        @mcp.tool()
        def list_states() -> str:
            """List available save states."""
            return harness.list_states()

    @mcp.tool()
    def read_last_session() -> str:
        """Wake-up ritual: your Status note, open goals, last journal entries, and current
        game state in one call. Use this FIRST at session start or whenever your
        conversation history feels thin (freshly compacted)."""
        return harness.read_last_session()

    @mcp.tool()
    def set_goal(text: str) -> str:
        """Add an objective to your persistent goal list (survives compaction and new chats)."""
        return harness.set_goal(text)

    @mcp.tool()
    def complete_goal(text: str) -> str:
        """Check off a goal (matches by substring). Celebrate appropriately."""
        return harness.complete_goal(text)

    @mcp.tool()
    def current_goals() -> str:
        """Your goal checklist."""
        return harness.current_goals()

    @mcp.tool()
    def write_note(title: str, content: str, folder: str = "") -> str:
        """Write (or overwrite) a markdown note in your vault. This is your permanent memory —
        it survives compaction and new conversations. Use [[wiki links]] freely."""
        return "Wrote " + harness._require_vault().write(title, content, folder)

    @mcp.tool()
    def append_note(title: str, content: str, folder: str = "") -> str:
        """Append to a note (created if missing)."""
        return "Appended to " + harness._require_vault().append(title, content, folder)

    @mcp.tool()
    def read_note(title: str, folder: str = "") -> str:
        """Read one of your notes."""
        return harness._require_vault().read(title, folder)

    @mcp.tool()
    def list_notes() -> str:
        """List every note in your vault."""
        notes = harness._require_vault().list_notes()
        return "\n".join(notes) if notes else "(vault is empty — start writing!)"

    @mcp.tool()
    def search_notes(query: str) -> str:
        """Full-text search across your vault. Check here before re-exploring anything."""
        hits = harness._require_vault().search(query)
        if not hits:
            return f"No notes mention {query!r}."
        return "\n".join(f"{h['note']}:{h['line']}: {h['text']}" for h in hits)

    return mcp


def make_emulator(config: Config):
    """Construct the backend for `config` — called lazily, on first play."""
    if config.backend == "mock":
        logger.info("MOCK mode (no ROM, no PyBoy)")
        return MockEmulator()
    if config.backend == "gba":
        from .gba_backend import GBABackend

        logger.info("Booting GBA %s (window=%s)", config.rom_path, config.window)
        return GBABackend(config.rom_path, config.window, config.scale, config.player)
    if config.backend == "switch":
        from .switch_backend import SwitchBackend

        return SwitchBackend(config.bridge_host, config.bridge_port, capture_index=config.capture_index)
    if config.backend == "n3ds":
        from .n3ds_backend import N3dsBackend

        logger.info("3DS mode: launching/attaching Azahar")
        return N3dsBackend(rom_path=config.rom_path, player=config.player)
    logger.info("Booting %s (window=%s)", config.rom_path, config.window)
    return PyBoyEmulator(config.rom_path, config.window, config.scale, config.speed)


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)  # stdout belongs to MCP
    config = Config.from_env()
    vault = Vault(config.vault_path) if config.vault_path else None
    if vault is None:
        logger.warning("No PIXEL_FLIPPERS_VAULT set — journal tools will error")
    # NB: the emulator is built lazily (when a game tool / start_game runs), so
    # launching the server opens NO window until this Claude asks to play.
    harness = Harness(config, vault=vault, emulator_factory=make_emulator)
    try:
        if config.transport == "http":
            # Local service for terminal play (Claude Code, the `pf` CLI, anything
            # that speaks MCP over streamable HTTP). Loopback only.
            logger.info("Serving MCP over HTTP at http://127.0.0.1:%d/mcp", config.port)
            build_server(harness).run(transport="streamable-http", host="127.0.0.1", port=config.port)
        else:
            build_server(harness).run()
    finally:
        emulator.close()


if __name__ == "__main__":
    main()
