"""Entity-IDs, Geraete und Ereignisse bleiben wie in 0.5.2.

Dashboards bauen auf diesen Namen auf: integration_entities('arcane'), die
Endungen der deutschen Entitaetsnamen (_zustand, _neustarten, ...), das
Geraetemodell "Compose project" und die Ereignisnamen. Aendert sich hier
etwas, bekommen bestehende Installationen neue Entity-IDs oder Karten bleiben
leer.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arcane import const

BASE = Path(__file__).parents[1] / "custom_components" / "arcane"


async def test_unique_ids_unchanged(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entry_id = mock_config_entry.entry_id
    unique_ids = {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry_id)
    }
    for suffix in (
        "0_online",
        "0_containers_running",
        "0_arcane_update",
        "0_container_homeassistant_container",
        "0_container_homeassistant_state",
        "0_container_homeassistant_restart",
        "0_container_homeassistant_image_update",
        "0_project_smarthome_project",
        "0_project_smarthome_services_running",
    ):
        assert f"{entry_id}_{suffix}" in unique_ids, suffix

    models = {
        device.model
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry_id)
    }
    assert {"Environment", "Container", "Compose project"} <= models


def test_german_entity_names_unchanged() -> None:
    """Aus diesen Namen bildet eine deutsche Instanz die Entity-IDs."""
    entity = json.loads((BASE / "translations/de.json").read_text("utf-8"))["entity"]
    expected = {
        ("sensor", "container_state"): "Zustand",
        ("sensor", "container_status"): "Status",
        ("sensor", "project_status"): "Status",
        ("sensor", "services_running"): "Laufende Services",
        ("sensor", "services_total"): "Services",
        ("sensor", "containers_running"): "Laufende Container",
        ("sensor", "host_cpu"): "Host CPU",
        ("binary_sensor", "container_healthy"): "Healthy",
        ("binary_sensor", "environment_online"): "Online",
        ("button", "restart"): "Neustarten",
        ("button", "prune"): "Ungenutztes entfernen",
        ("update", "image_update"): "Image Update",
        ("update", "arcane_update"): "Arcane",
    }
    for (platform, key), name in expected.items():
        assert entity[platform][key]["name"] == name, (platform, key)


def test_domain_and_events_unchanged() -> None:
    assert const.DOMAIN == "arcane"
    assert const.EVENT_CONTAINER_STATE == "arcane_container_state_changed"
    assert const.EVENT_CONTAINER_HEALTH == "arcane_container_health_changed"
    assert const.EVENT_PROJECT_STATE == "arcane_project_state_changed"
