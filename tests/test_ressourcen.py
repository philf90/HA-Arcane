"""Tests fuer 0.13.0: CPU und Arbeitsspeicher je Container (B5), Volume-Groessen (C2).

Belege im Quelltext von Arcane v3.0.1 (Commit c614e0d2):
- ``GET agents/{id}/containers?sort=cpuUsage`` fuellt ``resourceSample`` je
  laufendem Container (container/service.go, IsContainerResourceSort;
  types/container.go ContainerResourceSample). Ohne ``containers:read`` lehnt
  Arcane die Sortierung mit 403 ab (container/children/stats/service.go,
  ContainerResourceSortPermissionDenied).
- ``GET agents/{id}/volumes/sizes`` liefert je Volume ``name``, ``size`` und
  ``refCount`` (volume/handler.go VolumeSizeInfo); Docker misst dafuer jedes
  Volume aus (Docker meldet -1, wenn es eines nicht messen konnte).
- Container nennen ihre Volumes in ``mounts`` mit ``type: volume`` und
  ``name`` (types/container.go ContainerMount).
"""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arcane.api import ArcanePermissionError
from custom_components.arcane.const import (
    CONF_CONTAINER_RESOURCES,
    CONF_DISK_USAGE,
    CONF_MONITOR_RESOURCES,
    CONF_PRUNE_BUTTON,
    CONF_VOLUME_SIZES,
    DEFAULT_OPTIONS,
    DISK_USAGE_INTERVAL,
    DOMAIN,
)
from custom_components.arcane.models import (
    ArcaneContainer,
    ArcaneResourceSample,
    ArcaneVolumeSize,
)
from custom_components.arcane.permissions import required_permissions
from custom_components.arcane.sensor import (
    RESOURCE_COUNTER_ATTRIBUTES,
    ArcaneContainerResourceSensor,
)
from tests.conftest import CONTAINERS_PAYLOAD

SAMPLE = {
    "sampleTime": "2026-11-16T22:00:00Z",
    "cpuPercent": 12.34,
    "memoryUsageBytes": 268435456,
    "memoryLimitBytes": 2147483648,
    "networkRxBytes": 1000,
    "networkTxBytes": 2000,
    "blockReadBytes": 3000,
    "blockWriteBytes": 4000,
    "pids": 17,
}

VOLUME_SIZES = [
    {"name": "smarthome_config", "size": 104857600, "refCount": 1},
    {"name": "smarthome_db", "size": 524288000, "refCount": 1},
    {"name": "kuma_data", "size": -1, "refCount": 1},
    {"name": "verwaist", "size": 1048576, "refCount": 0},
]


def _containers(resources: bool = False) -> list[dict[str, Any]]:
    containers = copy.deepcopy(CONTAINERS_PAYLOAD)
    containers[0]["mounts"] = [
        {"type": "volume", "name": "smarthome_config", "destination": "/config"},
        {"type": "volume", "name": "smarthome_db", "destination": "/db"},
        {"type": "bind", "source": "/etc/localtime", "destination": "/etc/localtime"},
    ]
    containers[1]["mounts"] = [
        {"type": "volume", "name": "kuma_data", "destination": "/app/data"}
    ]
    containers[2]["mounts"] = []
    if resources:
        containers[0]["resourceSample"] = SAMPLE
        containers[1]["resourceSample"] = {**SAMPLE, "cpuPercent": 0.5}
    return containers


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    client: AsyncMock,
    options: dict[str, Any],
    resources: bool = False,
) -> None:
    client.async_get_containers.return_value = _containers(resources)
    client.async_get_volume_sizes.return_value = VOLUME_SIZES
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _missing(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"missing_permissions_{entry.entry_id}"
    )
    return issue.translation_placeholders["permissions"] if issue else ""


# --- B5: Modell ---------------------------------------------------------------


def test_resource_sample_reads_what_arcane_sends() -> None:
    sample = ArcaneResourceSample.from_api(SAMPLE)
    assert sample == ArcaneResourceSample(
        cpu_percent=12.34,
        memory_usage=268435456,
        memory_limit=2147483648,
        network_rx=1000,
        network_tx=2000,
        block_read=3000,
        block_write=4000,
        pids=17,
    )
    odd = ArcaneResourceSample.from_api(
        {
            "cpuPercent": True,
            "memoryUsageBytes": -5,
            "pids": "17",
            "networkRxBytes": 1.5,
        }
    )
    assert odd == ArcaneResourceSample()
    assert ArcaneResourceSample.from_api(None) is None
    assert ArcaneResourceSample.from_api([]) is None
    assert ArcaneContainer.from_api(CONTAINERS_PAYLOAD[0]).resources is None


# --- B5: Abfrage und Sensoren ---------------------------------------------------


async def test_resources_off_by_default(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry, mock_client, {})

    assert "resources" not in mock_client.async_get_containers.await_args.kwargs
    assert hass.states.get("sensor.container_homeassistant_cpu") is None
    assert hass.states.get("sensor.container_homeassistant_memory") is None
    assert "containers:read" not in required_permissions(DEFAULT_OPTIONS).all


async def test_cpu_and_memory_per_container(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(
        hass,
        mock_config_entry,
        mock_client,
        {CONF_CONTAINER_RESOURCES: True},
        resources=True,
    )

    assert mock_client.async_get_containers.await_args.kwargs["resources"] is True
    cpu = hass.states.get("sensor.container_homeassistant_cpu")
    assert float(cpu.state) == 12.34
    assert cpu.attributes["unit_of_measurement"] == "%"
    memory = hass.states.get("sensor.container_homeassistant_memory")
    assert float(memory.state) == 268.435456
    assert memory.attributes["unit_of_measurement"] == "MB"
    assert memory.attributes["memory_limit"] == 2147483648
    assert memory.attributes["network_rx"] == 1000
    assert memory.attributes["block_write"] == 4000
    assert memory.attributes["pids"] == 17
    assert float(hass.states.get("sensor.container_uptime_kuma_cpu").state) == 0.5
    # Ein gestoppter Container hat bei Arcane keine Probe.
    assert hass.states.get("sensor.container_whoami_cpu").state == STATE_UNKNOWN
    assert hass.states.get("sensor.container_whoami_memory").state == STATE_UNKNOWN
    assert (
        "containers:read"
        in required_permissions({**DEFAULT_OPTIONS, CONF_CONTAINER_RESOURCES: True}).all
    )


def test_io_counters_are_not_recorded() -> None:
    """Sie aendern sich bei jeder Aktualisierung."""
    assert ArcaneContainerResourceSensor._unrecorded_attributes == (
        RESOURCE_COUNTER_ATTRIBUTES
    )
    assert "memory_limit" not in RESOURCE_COUNTER_ATTRIBUTES
    assert {"network_rx", "network_tx", "block_read", "block_write", "pids"} <= (
        RESOURCE_COUNTER_ATTRIBUTES
    )


async def test_refused_samples_keep_the_list_and_name_the_permission(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    async def containers(environment_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        if kwargs.get("resources"):
            raise ArcanePermissionError("permission denied: containers:read")
        return _containers()

    mock_client.async_get_containers.side_effect = containers
    await _setup(hass, mock_config_entry, mock_client, {CONF_CONTAINER_RESOURCES: True})

    assert hass.states.get("switch.container_homeassistant").state == "on"
    assert hass.states.get("sensor.container_homeassistant_cpu").state == STATE_UNKNOWN
    missing = _missing(hass, mock_config_entry)
    assert "`containers:read`" in missing
    assert "containers:list" not in missing


async def test_refused_list_names_list_not_read(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_containers.side_effect = ArcanePermissionError("nope")
    await _setup(hass, mock_config_entry, mock_client, {CONF_CONTAINER_RESOURCES: True})

    missing = _missing(hass, mock_config_entry)
    assert "`containers:list`" in missing
    assert "containers:read" not in missing


# --- C2: Modell -----------------------------------------------------------------


def test_volume_models() -> None:
    assert ArcaneVolumeSize.from_api(VOLUME_SIZES[0]) == ArcaneVolumeSize(
        "smarthome_config", 104857600, 1
    )
    assert ArcaneVolumeSize.from_api(VOLUME_SIZES[2]).size is None
    assert ArcaneVolumeSize.from_api({"size": 5}) is None
    assert ArcaneVolumeSize.from_api("x") is None
    container = ArcaneContainer.from_api(
        {
            **CONTAINERS_PAYLOAD[0],
            "mounts": [
                {"type": "volume", "name": "a"},
                {"type": "volume", "name": "a"},
                {"type": "bind", "name": "b"},
                {"type": "tmpfs"},
                {"type": "volume", "name": ""},
                "volume",
            ],
        }
    )
    assert container.volume_names == ("a",)
    assert ArcaneContainer.from_api(CONTAINERS_PAYLOAD[0]).volume_names == ()


# --- C2: Abfrage und Sensoren ---------------------------------------------------


async def test_volume_sizes_off_by_default(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry, mock_client, {})

    mock_client.async_get_volume_sizes.assert_not_awaited()
    assert hass.states.get("sensor.local_volume_space_total") is None
    assert hass.states.get("sensor.container_homeassistant_volume_space") is None


async def test_volume_space_per_container_and_environment(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry, mock_client, {CONF_VOLUME_SIZES: True})

    mock_client.async_get_volume_sizes.assert_awaited_once_with("0")
    # Ohne die Option Freigebbarer Speicher kein system/df.
    mock_client.async_get_disk_usage.assert_not_awaited()

    container = hass.states.get("sensor.container_homeassistant_volume_space")
    assert float(container.state) == 629.1456
    assert container.attributes["volumes"] == [
        {"name": "smarthome_config", "size": 104857600},
        {"name": "smarthome_db", "size": 524288000},
    ]
    # Nur ein Volume, das Docker nicht messen konnte: unbekannt, nicht 0.
    assert (
        hass.states.get("sensor.container_uptime_kuma_volume_space").state
        == STATE_UNKNOWN
    )
    # Ohne Volumes belegt ein Container darin keinen Platz.
    assert float(hass.states.get("sensor.container_whoami_volume_space").state) == 0.0

    total = hass.states.get("sensor.local_volume_space_total")
    assert float(total.state) == 0.630194176
    assert total.attributes["volumes"] == [
        {"name": "smarthome_db", "size": 524288000, "ref_count": 1},
        {"name": "smarthome_config", "size": 104857600, "ref_count": 1},
        {"name": "verwaist", "size": 1048576, "ref_count": 0},
        {"name": "kuma_data", "size": None, "ref_count": 1},
    ]
    # Also without the image, volume and network counts, which need it too.
    counts_off = {**DEFAULT_OPTIONS, CONF_MONITOR_RESOURCES: False}
    assert "volumes:list" not in required_permissions(counts_off).monitoring
    assert (
        "volumes:list"
        in required_permissions({**counts_off, CONF_VOLUME_SIZES: True}).monitoring
    )


async def test_volume_sizes_measured_at_most_once_per_interval(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    with patch("custom_components.arcane.coordinator.time.monotonic") as clock:
        clock.return_value = 1000.0
        await _setup(
            hass,
            mock_config_entry,
            mock_client,
            {CONF_VOLUME_SIZES: True, CONF_DISK_USAGE: True},
        )
        coordinator = mock_config_entry.runtime_data
        assert mock_client.async_get_volume_sizes.await_count == 1
        assert mock_client.async_get_disk_usage.await_count == 1

        clock.return_value = 1000.0 + DISK_USAGE_INTERVAL - 1
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert mock_client.async_get_volume_sizes.await_count == 1
        assert hass.states.get("sensor.local_volume_space_total").state != (
            STATE_UNKNOWN
        )

        clock.return_value = 1000.0 + DISK_USAGE_INTERVAL
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert mock_client.async_get_volume_sizes.await_count == 2
        assert mock_client.async_get_disk_usage.await_count == 2


async def test_refused_volume_sizes_name_the_permission(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_volume_sizes.side_effect = ArcanePermissionError("nope")
    await _setup(
        hass,
        mock_config_entry,
        mock_client,
        {CONF_VOLUME_SIZES: True, CONF_DISK_USAGE: True},
    )

    assert "`volumes:list`" in _missing(hass, mock_config_entry)
    # Der Speicherbericht kam trotzdem.
    mock_client.async_get_disk_usage.assert_awaited_once()
    assert hass.states.get("sensor.local_volume_space_total").state == STATE_UNKNOWN


async def test_prune_measures_the_volumes_again(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    with patch("custom_components.arcane.coordinator.time.monotonic") as clock:
        clock.return_value = 1000.0
        await _setup(
            hass,
            mock_config_entry,
            mock_client,
            {CONF_VOLUME_SIZES: True, CONF_PRUNE_BUTTON: "unused"},
        )
        assert mock_client.async_get_volume_sizes.await_count == 1

        clock.return_value = 1060.0
        mock_client.async_get_volume_sizes.return_value = VOLUME_SIZES[:2]
        await hass.services.async_call(
            "button",
            "press",
            {ATTR_ENTITY_ID: "button.local_prune_unused"},
            blocking=True,
        )
        await hass.async_block_till_done()

    mock_client.async_prune.assert_awaited_once()
    assert mock_client.async_get_volume_sizes.await_count == 2
    assert float(hass.states.get("sensor.local_volume_space_total").state) == (
        0.6291456
    )


async def test_options_in_the_polling_step(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "polling"}
    )
    keys = {str(key): key for key in result["data_schema"].schema}
    assert keys[CONF_CONTAINER_RESOURCES].description["suggested_value"] is False
    assert keys[CONF_VOLUME_SIZES].description["suggested_value"] is False
    values = {
        name: DEFAULT_OPTIONS[name] for name in keys if name in DEFAULT_OPTIONS
    } | {CONF_CONTAINER_RESOURCES: True, CONF_VOLUME_SIZES: True}
    values.pop("environments", None)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], values
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_CONTAINER_RESOURCES] is True
    assert mock_config_entry.options[CONF_VOLUME_SIZES] is True
