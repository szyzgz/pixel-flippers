import pytest

from pixel_flippers.vault import Vault, VaultError


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "vault")


def test_write_and_read(vault):
    rel = vault.write("Cerulean City", "# Cerulean\nMisty uses water types.")
    assert rel == "Cerulean City.md"
    assert "Misty" in vault.read("Cerulean City")


def test_append_creates_and_extends(vault):
    vault.append("Status", "objective: beat Brock")
    vault.append("Status", "objective: beat Misty")
    text = vault.read("Status")
    assert text.splitlines() == ["objective: beat Brock", "objective: beat Misty"]


def test_folders_and_listing(vault):
    vault.write("Brock", "rock types", folder="Gyms")
    vault.write("Top", "root note")
    assert vault.list_notes() == ["Gyms/Brock.md", "Top.md"]


def test_search(vault):
    vault.write("Route 4", "hidden Ether already collected")
    vault.write("Route 9", "nothing here")
    hits = vault.search("ether")
    assert len(hits) == 1
    assert hits[0]["note"] == "Route 4.md"


def test_read_missing_raises(vault):
    with pytest.raises(VaultError):
        vault.read("nope")


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", "", ".hidden", "x\\y"])
def test_path_traversal_blocked(vault, bad):
    with pytest.raises(VaultError):
        vault.write(bad, "nope")


def test_log_action_appends_to_daily_note(vault):
    vault.log_action("pressed a")
    notes = vault.list_notes()
    assert len(notes) == 1 and notes[0].startswith("Journal/Log ")
    assert "pressed a" in vault.read(notes[0].removeprefix("Journal/").removesuffix(".md"), folder="Journal")
