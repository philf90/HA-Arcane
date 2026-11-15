"""Konsistenz von Version, Doku, Uebersetzungen und Endpunkten.

Nach dem Muster von HA-WOW: jede Abweichung faellt hier auf, bevor ein
Release entsteht.
"""

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
BASE = ROOT / "custom_components" / "arcane"
README = (ROOT / "README.md").read_text(encoding="utf-8")
VERSION = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))["version"]
MINOR = ".".join(VERSION.split(".")[:2])


def test_version_in_readme_and_release_notes() -> None:
    assert f"Version {VERSION}." in README, "README nennt die Version nicht"
    notes = ROOT / ".github" / "release-notes" / f"v{VERSION}.md"
    assert notes.is_file(), f"{notes.name} fehlt"
    text = notes.read_text(encoding="utf-8")
    assert "## Neu" in text or "## Behoben" in text or "## Geändert" in text
    assert "## Teststand" in text, f"{notes.name}: Abschnitt Teststand fehlt"


def test_readme_test_count_matches(request: pytest.FixtureRequest) -> None:
    """Die Zahl im Teststand muss stimmen; nur beim vollen Lauf pruefbar."""
    items = request.session.items
    if len({Path(str(i.fspath)).name for i in items}) < len(
        list((ROOT / "tests").glob("test_*.py"))
    ):
        pytest.skip("nur beim Lauf aller Testdateien")
    match = re.search(
        rf"\*\*{re.escape(MINOR)}\.x:\*\* (\d+) automatische Tests", README
    )
    assert match, f"Teststand fuer {MINOR}.x fehlt im README"
    assert int(match.group(1)) == len(items), (
        f"README nennt {match.group(1)} Tests, gesammelt sind {len(items)}"
    )


def _flatten(data, prefix=""):
    if isinstance(data, dict):
        for key, value in data.items():
            yield from _flatten(value, f"{prefix}{key}.")
    else:
        yield prefix.rstrip("."), data


def test_translation_files_match() -> None:
    """strings.json = en.json; de.json hat dieselben Schluessel und Platzhalter."""
    strings = json.loads((BASE / "strings.json").read_text(encoding="utf-8"))
    en = json.loads((BASE / "translations/en.json").read_text(encoding="utf-8"))
    de = dict(
        _flatten(
            json.loads((BASE / "translations/de.json").read_text(encoding="utf-8"))
        )
    )
    assert strings == en, "strings.json und en.json weichen ab"
    en_flat = dict(_flatten(en))
    assert set(en_flat) == set(de), set(en_flat) ^ set(de)
    for key, text in en_flat.items():
        wanted = set(re.findall(r"\{(\w+)\}", text))
        assert wanted == set(re.findall(r"\{(\w+)\}", de[key])), key


def _constants(tree: ast.Module) -> dict[str, str]:
    """Textkonstanten auf Modulebene, z. B. AGENTS: Final = "agents"."""
    found = {}
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            found[node.target.id] = node.value.value
    return found


def _segment(node: ast.expr, constants: dict[str, str]) -> str:
    """Pfadteil eines _url()-Aufrufs: Text und Konstanten bleiben, Variable wird
    {name}."""
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Name):
        if node.id in constants:
            return constants[node.id]
        return "{" + node.id.removesuffix("_id") + "}"
    raise AssertionError(f"unerwarteter Pfadteil: {ast.dump(node)}")


def client_paths() -> set[str]:
    """Alle Pfade, die der API-Client unter /api/ aufbaut."""
    tree = ast.parse((BASE / "api.py").read_text(encoding="utf-8"))
    constants = _constants(tree)
    paths = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_url"
            and node.args
        ):
            paths.add("/".join(_segment(arg, constants) for arg in node.args))
    return paths


def test_every_endpoint_documented() -> None:
    """Jeder Pfad des Clients steht im README-Abschnitt "Abfragen"."""
    section = README.split("\n## Abfragen", 1)[1].split("\n## ", 1)[0]
    paths = client_paths()
    assert len(paths) >= 10, paths
    missing = {p for p in paths if f"`{p}`" not in section}
    assert not missing, f"README nennt nicht: {sorted(missing)}"
