"""Tests fuer 0.7.0 und 0.7.1: freigebbarer Speicher aus Arcanes system/df.

Arcane laesst Docker dafuer die Groesse jedes Volumes ermitteln
(DiskUsage mit Volumes und Verbose, backend/internal/docker/service.go:392 in
Arcane v3.0.0). Die Integration fragt deshalb nur auf Wunsch, im Hintergrund
und hoechstens alle DISK_USAGE_INTERVAL Sekunden. Ab 0.7.1 fragt sie nach einem
Prune sofort neu und nennt im Attribut report_started_at, wann der gezeigte
Bericht angefragt wurde.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arcane.api import ArcaneConnectionError, ArcanePermissionError
from custom_components.arcane.const import (
    CONF_DISK_USAGE,
    CONF_PRUNE_BUTTON,
    DISK_USAGE_INTERVAL,
    DOMAIN,
)
from custom_components.arcane.sensor import (
    ATTR_REPORT_STARTED_AT,
    ArcaneEnvironmentSensor,
)

DISK_USAGE = {
    "unusedImagesBytes": 3221225472,
    "stoppedContainersBytes": 1048576,
    "unusedVolumesBytes": 536870912,
    "buildCacheBytes": 0,
}

SENSORS = {
    "sensor.local_reclaimable_image_space": "3.221225472",
    "sensor.local_reclaimable_container_space": "0.001048576",
    "sensor.local_reclaimable_volume_space": "0.536870912",
    "sensor.local_reclaimable_build_cache": "0.0",
}


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, options: dict | None = None
) -> None:
    entry.add_to_hass(hass)
    if options:
        hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_off_by_default(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)

    mock_client.async_get_disk_usage.assert_not_awaited()
    for entity_id in SENSORS:
        assert hass.states.get(entity_id) is None


async def test_sensors_report_reclaimable_space(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_disk_usage.return_value = DISK_USAGE
    await _setup(hass, mock_config_entry, {CONF_DISK_USAGE: True})

    mock_client.async_get_disk_usage.assert_awaited_once_with("0")
    for entity_id, value in SENSORS.items():
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert float(state.state) == float(value), entity_id
        assert state.attributes["unit_of_measurement"] == "GB"


async def test_asked_at_most_once_per_interval(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_disk_usage.return_value = DISK_USAGE
    with patch("custom_components.arcane.coordinator.time.monotonic") as clock:
        clock.return_value = 1000.0
        await _setup(hass, mock_config_entry, {CONF_DISK_USAGE: True})
        coordinator = mock_config_entry.runtime_data

        clock.return_value = 1000.0 + DISK_USAGE_INTERVAL - 1
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert mock_client.async_get_disk_usage.await_count == 1
        # Der letzte Bericht bleibt sichtbar, bis ein neuer kommt.
        assert hass.states.get("sensor.local_reclaimable_image_space").state != (
            "unknown"
        )

        clock.return_value = 1000.0 + DISK_USAGE_INTERVAL
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert mock_client.async_get_disk_usage.await_count == 2


async def test_slow_report_does_not_hold_up_a_refresh(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    release = asyncio.Event()

    async def slow(environment_id: str) -> dict:
        await release.wait()
        return DISK_USAGE

    mock_client.async_get_disk_usage.side_effect = slow
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_DISK_USAGE: True}
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)

    # Die Einrichtung ist fertig, obwohl der Bericht noch aussteht.
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.local_containers").state == "3"
    assert hass.states.get("sensor.local_reclaimable_image_space").state == "unknown"

    release.set()
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.local_reclaimable_image_space").state) == (
        3.221225472
    )


async def test_failed_report_leaves_the_rest_alone(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_disk_usage.side_effect = ArcaneConnectionError("zu langsam")
    await _setup(hass, mock_config_entry, {CONF_DISK_USAGE: True})

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.local_reclaimable_image_space").state == "unknown"
    assert hass.states.get("sensor.local_containers").state == "3"


async def test_refused_report_names_the_permission(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_disk_usage.side_effect = ArcanePermissionError("nope")
    await _setup(hass, mock_config_entry, {CONF_DISK_USAGE: True})

    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"missing_permissions_{mock_config_entry.entry_id}"
    )
    assert issue is not None
    assert "`system:read`" in issue.translation_placeholders["permissions"]


# --- 0.7.1: neuer Bericht nach dem Prune, Zeitpunkt des Berichts ---

PRUNE_BUTTON = "button.local_prune_unused"
IMAGE_SPACE = "sensor.local_reclaimable_image_space"
AFTER_PRUNE = {**DISK_USAGE, "unusedImagesBytes": 0}


async def _press_prune(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: PRUNE_BUTTON}, blocking=True
    )
    await hass.async_block_till_done()


async def test_prune_asks_for_a_new_report_at_once(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_disk_usage.return_value = DISK_USAGE
    with patch("custom_components.arcane.coordinator.time.monotonic") as clock:
        clock.return_value = 1000.0
        await _setup(
            hass,
            mock_config_entry,
            {CONF_DISK_USAGE: True, CONF_PRUNE_BUTTON: "unused"},
        )
        assert mock_client.async_get_disk_usage.await_count == 1

        # Eine Minute spaeter, weit innerhalb des Intervalls.
        clock.return_value = 1060.0
        mock_client.async_get_disk_usage.return_value = AFTER_PRUNE
        await _press_prune(hass)

        mock_client.async_prune.assert_awaited_once()
        assert mock_client.async_get_disk_usage.await_count == 2
        assert float(hass.states.get(IMAGE_SPACE).state) == 0.0

        # Danach gilt wieder das Intervall, gezaehlt ab dem neuen Bericht.
        coordinator = mock_config_entry.runtime_data
        clock.return_value = 1060.0 + DISK_USAGE_INTERVAL - 1
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert mock_client.async_get_disk_usage.await_count == 2


async def test_report_running_during_prune_is_followed_by_another(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    release = asyncio.Event()
    calls: list[str] = []

    async def report(environment_id: str) -> dict:
        calls.append(environment_id)
        if len(calls) == 1:
            # Der erste Bericht laeuft noch, als der Prune kommt.
            await release.wait()
            return DISK_USAGE
        return AFTER_PRUNE

    mock_client.async_get_disk_usage.side_effect = report
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_DISK_USAGE: True, CONF_PRUNE_BUTTON: "unused"},
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert len(calls) == 1

    pressed = dt_util.utcnow()
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: PRUNE_BUTTON}, blocking=True
    )
    # Kein zweiter Bericht, solange der erste laeuft.
    assert len(calls) == 1

    release.set()
    await hass.async_block_till_done()

    assert len(calls) == 2
    state = hass.states.get(IMAGE_SPACE)
    assert float(state.state) == 0.0
    started = dt_util.parse_datetime(state.attributes[ATTR_REPORT_STARTED_AT])
    assert started >= pressed


async def test_report_time_is_when_the_report_was_asked_for(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    release = asyncio.Event()

    async def slow(environment_id: str) -> dict:
        await release.wait()
        return DISK_USAGE

    mock_client.async_get_disk_usage.side_effect = slow
    before = dt_util.utcnow()
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_DISK_USAGE: True}
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    asked = dt_util.utcnow()

    # Noch kein Bericht: kein Zeitpunkt.
    assert ATTR_REPORT_STARTED_AT not in hass.states.get(IMAGE_SPACE).attributes

    await asyncio.sleep(0.01)
    arrived = dt_util.utcnow()
    release.set()
    await hass.async_block_till_done()

    started = dt_util.parse_datetime(
        hass.states.get(IMAGE_SPACE).attributes[ATTR_REPORT_STARTED_AT]
    )
    # Beginn des Berichts, nicht seine Ankunft.
    assert before <= started <= asked < arrived
    for entity_id in SENSORS:
        assert ATTR_REPORT_STARTED_AT in hass.states.get(entity_id).attributes


async def test_prune_without_the_option_asks_for_no_report(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry, {CONF_PRUNE_BUTTON: "unused"})
    await _press_prune(hass)

    mock_client.async_prune.assert_awaited_once()
    mock_client.async_get_disk_usage.assert_not_awaited()


async def test_failed_prune_asks_for_no_report(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_disk_usage.return_value = DISK_USAGE
    mock_client.async_prune.side_effect = ArcaneConnectionError("weg")
    with patch("custom_components.arcane.coordinator.time.monotonic") as clock:
        clock.return_value = 1000.0
        await _setup(
            hass,
            mock_config_entry,
            {CONF_DISK_USAGE: True, CONF_PRUNE_BUTTON: "unused"},
        )
        clock.return_value = 1060.0
        with pytest.raises(HomeAssistantError):
            await _press_prune(hass)
        await hass.async_block_till_done()

    assert mock_client.async_get_disk_usage.await_count == 1


def test_report_time_is_not_recorded() -> None:
    assert ATTR_REPORT_STARTED_AT in ArcaneEnvironmentSensor._unrecorded_attributes
