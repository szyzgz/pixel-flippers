"""Obsidian vault integration — plain markdown files on disk.

The vault directory IS the integration: Obsidian just watches the folder, so
there's no plugin, REST API, or second MCP server involved. Notes written
here survive compaction, new chats, and everything else.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

MAX_SEARCH_RESULTS = 20


class VaultError(Exception):
    pass


def _sanitize(name: str) -> str:
    name = name.strip().removesuffix(".md")
    if not name:
        raise VaultError("Empty note name")
    if re.search(r"[\\/]|\.\.|^\.", name):
        raise VaultError(f"Note names can't contain path separators or dots-prefixes: {name!r}")
    return name


class Vault:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, title: str, folder: str = "") -> Path:
        parts = [_sanitize(p) for p in folder.split("/") if p.strip()] if folder else []
        path = self.root.joinpath(*parts, _sanitize(title) + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write(self, title: str, content: str, folder: str = "") -> str:
        path = self._path(title, folder)
        path.write_text(content, encoding="utf-8")
        return str(path.relative_to(self.root))

    def append(self, title: str, content: str, folder: str = "") -> str:
        path = self._path(title, folder)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        path.write_text(existing + content + "\n", encoding="utf-8")
        return str(path.relative_to(self.root))

    def read(self, title: str, folder: str = "") -> str:
        path = self._path(title, folder)
        if not path.exists():
            raise VaultError(f"No note named {title!r}" + (f" in {folder!r}" if folder else ""))
        return path.read_text(encoding="utf-8")

    def list_notes(self) -> list[str]:
        return sorted(
            str(p.relative_to(self.root)) for p in self.root.rglob("*.md") if p.is_file()
        )

    def search(self, query: str) -> list[dict]:
        query_lower = query.lower()
        results = []
        for rel in self.list_notes():
            for line_no, line in enumerate((self.root / rel).read_text(encoding="utf-8").splitlines(), 1):
                if query_lower in line.lower():
                    results.append({"note": rel, "line": line_no, "text": line.strip()})
                    if len(results) >= MAX_SEARCH_RESULTS:
                        return results
        return results

    def log_action(self, text: str) -> None:
        """Append to today's auto-log — the spectator's play-by-play."""
        now = dt.datetime.now()
        self.append(f"Log {now:%Y-%m-%d}", f"- `{now:%H:%M:%S}` {text}", folder="Journal")
