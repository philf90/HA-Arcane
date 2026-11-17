"""Tests fuer 0.14.1: Entitaeten abgeschalteter Optionen werden entfernt.

Live gefunden am 17.11.2026: Nach dem Abschalten der Option Redeploy-Buttons
blieben 54 Buttons als "nicht verfuegbar" in der Registry. HA-Arcane raeumte
keine Entitaeten ab. Jetzt entfernt jede Plattform beim ersten Auftauchen
einer Umgebung, eines Containers, Projekts oder Jobs die Eintraege, die sie
dafuer nicht mehr erzeugt. Was Arcane gerade nicht meldet (etwa die Container
eines Projekts nach ``down``), behaelt seine Entitaeten.
"""

from __future__ import annotations

import copy
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arcane import (
    binary_sensor,
    button,
    sensor,
    switch,
    update,
)
from custom_components.arcane.const import (
    CONF_ALLOW_CONTROL,
    CONF_AUTO_UPDATE_SWITCHES,
    CONF_CONTAINER_RESOURCES,
    CONF_DISK_USAGE,
    CONF_HEALTH_SENSORS,
    CONF_HOST_STATS,
    CONF_JOBS,
    CONF_PORTS,
    CONF_PRUNE_BUTTON,
    CONF_REDEPLOY_BUTTONS,
    CONF_UPDATE_ENTITIES,
    CONF_UPDATER,
    CONF_VOLUME_SIZES,
    DOMAIN,
    PRUNE_UNUSED,
)
from custom_components.arcane.helpers import description_keys
from tests.conftest import CONTAINERS_PAYLOAD
from tests.test_jobs import AUTO_UPDATE

PLATFORM_KEYS = {
    "binary_sensor": description_keys(vars(binary_sensor)),
    "button": description_keys(vars(button)),
    "sensor": description_keys(vars(sensor)),
    "switch": description_keys(vars(switch)),
    "update": description_keys(vars(update)),
}


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, options: dict[str, Any]
) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _change(
    hass: HomeAssistant, entry: MockConfigEntry, options: dict[str, Any]
) -> None:
    """Aendert die Optionen; die Integration laedt dann neu."""
    hass.config_entries.async_update_entry(entry, options={**entry.options, **options})
    await hass.async_block_till_done()


def _entity(
    hass: HomeAssistant, entry: MockConfigEntry, domain: str, suffix: str
) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, f"{entry.entry_id}_{suffix}"
    )


async def test_redeploy_buttons_go_with_their_option(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry, {CONF_REDEPLOY_BUTTONS: True})
    entry = mock_config_entry
    container = "0_container_homeassistant_redeploy"
    project = "0_project_smarthome_redeploy"
    assert _entity(hass, entry, "button", container)
    assert _entity(hass, entry, "button", project)

    await _change(hass, entry, {CONF_REDEPLOY_BUTTONS: False})

    assert _entity(hass, entry, "button", container) is None
    assert _entity(hass, entry, "button", project) is None
    # Was die Optionen weiter erzeugen, bleibt.
    assert _entity(hass, entry, "button", "0_container_homeassistant_restart")
    assert _entity(hass, entry, "button", "0_project_smarthome_restart")


async def test_switching_off_control_removes_switches_and_buttons(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(
        hass,
        mock_config_entry,
        {CONF_PRUNE_BUTTON: PRUNE_UNUSED, CONF_AUTO_UPDATE_SWITCHES: True},
    )
    entry = mock_config_entry
    gone = [
        ("switch", "0_container_homeassistant_container"),
        ("switch", "0_container_homeassistant_auto_update"),
        ("switch", "0_project_smarthome_project"),
        ("button", "0_container_whoami_restart"),
        ("button", "0_prune"),
    ]
    for domain, suffix in gone:
        assert _entity(hass, entry, domain, suffix), suffix

    await _change(hass, entry, {CONF_ALLOW_CONTROL: False})

    for domain, suffix in gone:
        assert _entity(hass, entry, domain, suffix) is None, suffix
    assert _entity(hass, entry, "sensor", "0_container_homeassistant_status")


async def test_sensor_options_take_their_sensors_along(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    on = {CONF_PORTS: True, CONF_CONTAINER_RESOURCES: True, CONF_VOLUME_SIZES: True}
    await _setup(hass, mock_config_entry, on)
    entry = mock_config_entry
    sensors = [
        "0_container_homeassistant_ports",
        "0_container_homeassistant_cpu_usage",
        "0_container_homeassistant_memory_usage",
        "0_container_homeassistant_volume_size",
        "0_host_ports",
        "0_volumes_size_total",
    ]
    for suffix in sensors:
        assert _entity(hass, entry, "sensor", suffix), suffix

    await _change(hass, entry, dict.fromkeys(on, False))

    for suffix in sensors:
        assert _entity(hass, entry, "sensor", suffix) is None, suffix


async def test_job_entities_go_with_the_jobs_option(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Ohne die Option fragt die Integration keine Jobs ab; sie gehen trotzdem."""
    mock_client.async_get_jobs.return_value = [copy.deepcopy(AUTO_UPDATE)]
    await _setup(hass, mock_config_entry, {CONF_JOBS: True})
    entry = mock_config_entry
    assert _entity(hass, entry, "sensor", "0_job_auto-update_job")
    assert _entity(hass, entry, "button", "0_job_auto-update_run")

    await _change(hass, entry, {CONF_JOBS: False})

    assert _entity(hass, entry, "sensor", "0_job_auto-update_job") is None
    assert _entity(hass, entry, "button", "0_job_auto-update_run") is None


async def test_update_entities_go_with_their_option(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry, {})
    entry = mock_config_entry
    assert _entity(hass, entry, "update", "0_arcane_update")
    assert _entity(hass, entry, "update", "0_container_homeassistant_image_update")

    await _change(hass, entry, {CONF_UPDATE_ENTITIES: False})

    assert _entity(hass, entry, "update", "0_arcane_update") is None
    assert (
        _entity(hass, entry, "update", "0_container_homeassistant_image_update") is None
    )


async def test_health_sensor_of_a_stopped_container_stays(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Docker meldet die Gesundheit nur laufender Container."""
    await _setup(hass, mock_config_entry, {})
    entry = mock_config_entry
    health = "0_container_uptime-kuma_healthy"
    assert _entity(hass, entry, "binary_sensor", health)

    containers = copy.deepcopy(CONTAINERS_PAYLOAD)
    containers[1].update(state="exited", status="Exited (0) 1 minute ago")
    mock_client.async_get_containers.return_value = containers
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _entity(hass, entry, "binary_sensor", health)

    await _change(hass, entry, {CONF_HEALTH_SENSORS: False})
    assert _entity(hass, entry, "binary_sensor", health) is None


async def test_absent_containers_keep_their_entities_until_they_return(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry, {CONF_REDEPLOY_BUTTONS: True})
    entry = mock_config_entry
    whoami = "0_container_whoami_redeploy"
    assert _entity(hass, entry, "button", whoami)

    # Projekt "down": Arcane meldet den Container nicht mehr.
    mock_client.async_get_containers.return_value = copy.deepcopy(
        CONTAINERS_PAYLOAD[:2]
    )
    await _change(hass, entry, {CONF_REDEPLOY_BUTTONS: False})
    assert _entity(hass, entry, "button", whoami)
    assert _entity(hass, entry, "button", "0_container_homeassistant_redeploy") is None

    # Kommt er zurueck, geht auch sein Button.
    mock_client.async_get_containers.return_value = copy.deepcopy(CONTAINERS_PAYLOAD)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _entity(hass, entry, "button", whoami) is None
    assert _entity(hass, entry, "button", "0_container_whoami_restart")


async def test_a_similar_container_name_is_not_touched(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Nur genaue unique_ids zaehlen, kein Praefix eines anderen Namens."""
    mock_config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    other = registry.async_get_or_create(
        "button",
        DOMAIN,
        f"{mock_config_entry.entry_id}_0_container_homeassistant_2_redeploy",
        config_entry=mock_config_entry,
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(other.entity_id) is not None


async def test_a_plain_reload_removes_nothing(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Auch nichts, was gleich wieder angelegt wuerde.

    Home Assistant gaebe einer entfernten Entitaet beim Wiederanlegen ihre ID
    zurueck; daran laesst sich das nicht sehen, nur am Protokoll.
    """
    caplog.set_level(logging.DEBUG, logger="custom_components.arcane.helpers")
    await _setup(hass, mock_config_entry, {CONF_REDEPLOY_BUTTONS: True})
    registry = er.async_get(hass)
    before = {
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, mock_config_entry.entry_id)
    }

    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    after = {
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, mock_config_entry.entry_id)
    }
    assert after == before
    assert "Removing" not in caplog.text


async def test_cleanup_stays_on_its_own_platform(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Ohne Steuerung raeumen Buttons und Schalter ab, Sensoren nicht."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_ALLOW_CONTROL: False}
    )
    registry = er.async_get(hass)
    # Ein Sensor, dessen unique_id auf einen Button-Schluessel endet.
    other = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{mock_config_entry.entry_id}_0_container_ghost_restart",
        config_entry=mock_config_entry,
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(other.entity_id) is not None


async def test_cleanup_can_reach_every_entity(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Jede Entitaet endet auf einen Schluessel ihrer Plattform.

    Sonst koennte die Plattform sie nach dem Abschalten ihrer Option nicht
    finden. Alle Optionen an, dazu eine entfernte Umgebung und ein Job.
    """
    mock_client.async_get_environments.return_value = [
        {"id": "0", "name": "Local", "status": "online", "enabled": True},
        {"id": "7", "name": "Remote", "status": "online", "enabled": True},
    ]
    mock_client.async_get_jobs.return_value = [copy.deepcopy(AUTO_UPDATE)]
    await _setup(
        hass,
        mock_config_entry,
        {
            CONF_REDEPLOY_BUTTONS: True,
            CONF_PRUNE_BUTTON: PRUNE_UNUSED,
            CONF_AUTO_UPDATE_SWITCHES: True,
            CONF_UPDATER: True,
            CONF_JOBS: True,
            CONF_PORTS: True,
            CONF_CONTAINER_RESOURCES: True,
            CONF_VOLUME_SIZES: True,
            CONF_HOST_STATS: True,
            CONF_DISK_USAGE: True,
        },
    )

    entries = er.async_entries_for_config_entry(
        er.async_get(hass), mock_config_entry.entry_id
    )
    assert len(entries) > 50
    unreachable = [
        e.entity_id
        for e in entries
        if not any(e.unique_id.endswith(f"_{key}") for key in PLATFORM_KEYS[e.domain])
    ]
    assert not unreachable
