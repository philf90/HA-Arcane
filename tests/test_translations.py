"""Jeder Schluessel aus Dialogen, Entitaeten und Fehlermeldungen ist uebersetzt.

Nach dem Muster von HA-WOW: Was im Code als Schluessel steht, muss in
strings.json, en.json und de.json stehen. Sonst zeigt Home Assistant den rohen
Schluessel statt eines Textes.
"""

import json
import re
from pathlib import Path

import pytest

from custom_components.arcane import binary_sensor, button, sensor, update

BASE = Path(__file__).parents[1] / "custom_components" / "arcane"
FILES = ["strings.json", "translations/en.json", "translations/de.json"]


def _load(path: str) -> dict:
    return json.loads((BASE / path).read_text(encoding="utf-8"))


def _used_keys(source: str) -> tuple[set[str], set[str]]:
    errors = set(re.findall(r'errors\["base"\] = "(\w+)"', source))
    aborts = set(re.findall(r'async_abort\(reason="(\w+)"\)', source))
    if "_abort_entries_match" in source or "_abort_if_unique_id_configured" in source:
        aborts.add("already_configured")
    if "async_update_reload_and_abort" in source:
        aborts |= {"reauth_successful", "reconfigure_successful"}
    return errors, aborts


@pytest.mark.parametrize("path", FILES)
def test_flow_keys_translated(path: str) -> None:
    source = (BASE / "config_flow.py").read_text(encoding="utf-8")
    main_src, options_src = source.split("class ArcaneOptionsFlow")
    strings = _load(path)

    errors, aborts = _used_keys(main_src)
    assert errors and aborts, "keine Schluessel gefunden"
    assert errors <= set(strings["config"]["error"]), errors - set(
        strings["config"]["error"]
    )
    assert aborts <= set(strings["config"]["abort"]), aborts - set(
        strings["config"]["abort"]
    )
    # Schritte des Optionsdialogs: Menue und jede Seite haben einen Titel.
    steps = set(re.findall(r'step_id="(\w+)"', options_src))
    steps |= set(re.findall(r'_form\(\s*"(\w+)"', options_src))
    steps |= set(
        re.findall(r"menu_options=\[([^\]]*)\]", options_src)[0].split('"')[1::2]
    )
    assert steps <= set(strings["options"]["step"]), steps - set(
        strings["options"]["step"]
    )


def _entity_keys() -> dict[str, set[str]]:
    return {
        "sensor": {
            d.translation_key
            for d in (
                *sensor.ENVIRONMENT_SENSORS,
                *sensor.CONTAINER_SENSORS,
                *sensor.PROJECT_SENSORS,
                sensor.ARCANE_VERSION_SENSOR,
            )
        },
        "binary_sensor": {
            d.translation_key
            for d in (
                *binary_sensor.ENVIRONMENT_BINARY_SENSORS,
                *binary_sensor.CONTAINER_BINARY_SENSORS,
                *binary_sensor.PROJECT_BINARY_SENSORS,
                binary_sensor.HEALTH_BINARY_SENSOR,
            )
        },
        "button": {
            d.translation_key
            for d in (
                button.RESTART_BUTTON,
                button.REDEPLOY_BUTTON,
                button.PRUNE_BUTTON,
            )
        },
        "update": {
            d.translation_key for d in (update.CONTAINER_UPDATE, update.ARCANE_UPDATE)
        },
    }


@pytest.mark.parametrize("path", FILES)
def test_entity_keys_translated(path: str) -> None:
    entity = _load(path)["entity"]
    for platform, wanted in _entity_keys().items():
        assert None not in wanted, f"{platform}: Beschreibung ohne translation_key"
        missing = wanted - set(entity.get(platform, {}))
        assert not missing, f"{path} {platform}: {missing}"


def _exception_keys() -> set[str]:
    """translation_key aller HomeAssistantError, auch die aus action_error()."""
    keys: set[str] = set()
    for module in ("button.py", "switch.py", "update.py"):
        source = (BASE / module).read_text(encoding="utf-8")
        keys |= set(re.findall(r'translation_key="(\w+)"', source))
        for action_key in re.findall(r'action_key="(\w+)"', source):
            keys |= {f"{action_key}_failed", f"{action_key}_forbidden"}
    # Schluessel von Entitaetsbeschreibungen sind keine Fehlermeldungen.
    descriptions = set().union(*_entity_keys().values())
    return keys - descriptions


@pytest.mark.parametrize("path", FILES)
def test_exception_and_issue_keys_translated(path: str) -> None:
    strings = _load(path)
    exceptions = _exception_keys()
    assert "container_action_forbidden" in exceptions, "keine Schluessel gefunden"
    assert exceptions <= set(strings["exceptions"]), exceptions - set(
        strings["exceptions"]
    )
    source = (BASE / "coordinator.py").read_text(encoding="utf-8")
    issues = set(re.findall(r'translation_key="(\w+)"', source))
    assert issues == {"missing_permissions"}
    assert issues <= set(strings["issues"]), issues - set(strings["issues"])


@pytest.mark.parametrize("path", FILES)
def test_selector_keys_translated(path: str) -> None:
    source = (BASE / "config_flow.py").read_text(encoding="utf-8")
    selectors = set(re.findall(r'translation_key="(\w+)"', source))
    assert selectors == {"prune_button"}
    assert selectors <= set(_load(path)["selector"])
