"""Tests fuer 0.12.0: veroeffentlichte Ports (C6).

Belege im Quelltext von Arcane v3.0.1 (Commit c614e0d2):
- ``GET agents/{id}/containers`` liefert ``ContainerSummary`` mit ``ports``,
  je Eintrag ``ip``, ``privatePort``, ``publicPort`` und ``type``
  (types/container.go ContainerSummary und ContainerPort; ``ip`` und
  ``publicPort`` mit omitempty, fehlen also bei nur freigegebenen Ports).
- ``GET agents/{id}/ports`` baut seine Liste aus genau diesen Eintraegen
  (backend/internal/port/service.go ListPortsPaginated); die Integration
  braucht die Route daher nicht.
"""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arcane.const import (
    CONF_MONITOR_CONTAINERS,
    CONF_PORTS,
    DEFAULT_OPTIONS,
)
from custom_components.arcane.models import ArcaneContainer, ArcanePort
from custom_components.arcane.permissions import required_permissions
from custom_components.arcane.sensor import NO_PUBLISHED_PORTS
from tests.conftest import CONTAINERS_PAYLOAD

# Wie Docker ihn meldet: derselbe Port fuer IPv4 und IPv6 auf allen Adressen.
HOMEASSISTANT_PORTS = [
    {"ip": "0.0.0.0", "privatePort": 8123, "publicPort": 8123, "type": "tcp"},
    {"ip": "::", "privatePort": 8123, "publicPort": 8123, "type": "tcp"},
    {"ip": "127.0.0.1", "privatePort": 5683, "publicPort": 5683, "type": "udp"},
    {"privatePort": 1883, "type": "tcp"},
]
UPTIME_PORTS = [
    {"ip": "0.0.0.0", "privatePort": 3001, "publicPort": 3001, "type": "tcp"},
]


def _containers() -> list[dict[str, Any]]:
    containers = copy.deepcopy(CONTAINERS_PAYLOAD)
    containers[0]["ports"] = HOMEASSISTANT_PORTS
    containers[1]["ports"] = UPTIME_PORTS
    # Ein gestoppter Container hat bei Docker keine veroeffentlichten Ports.
    containers[2]["ports"] = []
    return containers


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    client: AsyncMock,
    options: dict[str, Any],
) -> None:
    client.async_get_containers.return_value = _containers()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


# --- Modell -----------------------------------------------------------------


def test_model_reads_ports_and_merges_ipv4_and_ipv6() -> None:
    container = ArcaneContainer.from_api(
        {**CONTAINERS_PAYLOAD[0], "ports": HOMEASSISTANT_PORTS}
    )

    assert container.ports == (
        ArcanePort(5683, "udp", 5683, "127.0.0.1"),
        ArcanePort(8123, "tcp", 8123),
        ArcanePort(1883, "tcp"),
    )
    assert [str(port) for port in container.published_ports] == [
        "127.0.0.1:5683→5683/udp",
        "8123→8123/tcp",
    ]
    assert str(container.ports[-1]) == "1883/tcp"


def test_model_keeps_specific_addresses_and_skips_what_is_not_a_port() -> None:
    container = ArcaneContainer.from_api(
        {
            **CONTAINERS_PAYLOAD[0],
            "ports": [
                {"ip": "::1", "privatePort": 80, "publicPort": 8080, "type": "tcp"},
                {"ip": "192.0.2.1", "privatePort": 80, "publicPort": 8080},
                {"privatePort": True, "type": "tcp"},
                {"privatePort": "80", "type": "tcp"},
                {"privatePort": 70000, "type": "tcp"},
                {"privatePort": 443, "publicPort": 0, "ip": "0.0.0.0"},
                "80/tcp",
                None,
            ],
        }
    )

    assert [str(port) for port in container.ports] == [
        "192.0.2.1:8080→80/tcp",
        "[::1]:8080→80/tcp",
        "443/tcp",
    ]
    assert ArcaneContainer.from_api(CONTAINERS_PAYLOAD[0]).ports == ()

    # Sortiert nach Host-Port, nicht nach Container-Port.
    mapped = ArcaneContainer.from_api(
        {
            **CONTAINERS_PAYLOAD[0],
            "ports": [
                {"ip": "0.0.0.0", "privatePort": 80, "publicPort": 9000},
                {"ip": "0.0.0.0", "privatePort": 90, "publicPort": 8000},
            ],
        }
    )
    assert [str(port) for port in mapped.ports] == ["8000→90/tcp", "9000→80/tcp"]
    assert (
        ArcaneContainer.from_api({**CONTAINERS_PAYLOAD[0], "ports": None}).ports == ()
    )


# --- Entitaeten ---------------------------------------------------------------


async def test_ports_sensor_per_container(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry, mock_client, {CONF_PORTS: True})

    state = hass.states.get("sensor.container_homeassistant_ports")
    assert state.state == "127.0.0.1:5683→5683/udp, 8123→8123/tcp"
    assert state.attributes["published"] == ["127.0.0.1:5683→5683/udp", "8123→8123/tcp"]
    assert state.attributes["exposed"] == ["1883/tcp"]
    assert hass.states.get("sensor.container_uptime_kuma_ports").state == (
        "3001→3001/tcp"
    )
    stopped = hass.states.get("sensor.container_whoami_ports")
    assert stopped.state == NO_PUBLISHED_PORTS
    assert stopped.attributes["published"] == []


async def test_host_ports_sensor_per_environment(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry, mock_client, {CONF_PORTS: True})

    state = hass.states.get("sensor.local_host_ports_in_use")
    assert state.state == "3"
    assert state.attributes["ports"] == [
        {
            "host_port": 3001,
            "protocol": "tcp",
            "host_ip": None,
            "container": "uptime-kuma",
            "container_port": 3001,
        },
        {
            "host_port": 5683,
            "protocol": "udp",
            "host_ip": "127.0.0.1",
            "container": "homeassistant",
            "container_port": 5683,
        },
        {
            "host_port": 8123,
            "protocol": "tcp",
            "host_ip": None,
            "container": "homeassistant",
            "container_port": 8123,
        },
    ]

    # Ein Container mit neuem Port: beide Sensoren ziehen nach.
    containers = _containers()
    containers[2]["state"] = "running"
    containers[2]["ports"] = [
        {"ip": "0.0.0.0", "privatePort": 80, "publicPort": 8081, "type": "tcp"}
    ]
    mock_client.async_get_containers.return_value = containers
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.local_host_ports_in_use").state == "4"
    assert hass.states.get("sensor.container_whoami_ports").state == "8081→80/tcp"


async def test_long_port_list_is_cut_to_a_valid_state(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    many = [
        {"ip": "0.0.0.0", "privatePort": port, "publicPort": port, "type": "tcp"}
        for port in range(10000, 10040)
    ]
    mock_client.async_get_containers.return_value = [
        {**CONTAINERS_PAYLOAD[1], "ports": many}
    ]
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_PORTS: True}
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.container_uptime_kuma_ports")
    assert len(state.state) == 255
    assert state.state.endswith("…")
    assert len(state.attributes["published"]) == 40


async def test_without_the_option_no_port_sensors(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry, mock_client, {})

    assert hass.states.get("sensor.container_homeassistant_ports") is None
    assert hass.states.get("sensor.local_host_ports_in_use") is None


async def test_host_ports_need_the_containers(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Ohne Container-Abfrage gaebe der Sensor immer 0 aus."""
    await _setup(
        hass,
        mock_config_entry,
        mock_client,
        {CONF_PORTS: True, CONF_MONITOR_CONTAINERS: False},
    )

    assert hass.states.get("sensor.local_host_ports_in_use") is None
    mock_client.async_get_containers.assert_not_awaited()


async def test_ports_cost_no_request_and_no_permission(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    def asked() -> set[str]:
        # Welche Methoden, nicht wie oft: das Neuladen fragt erneut ab.
        return {name for name, _args, _kwargs in mock_client.mock_calls}

    await _setup(hass, mock_config_entry, mock_client, {})
    without = asked()
    mock_client.reset_mock()

    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_PORTS: True}
    )
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.local_host_ports_in_use").state == "3"
    assert asked() == without
    assert required_permissions({**DEFAULT_OPTIONS, CONF_PORTS: True}).all == (
        required_permissions(DEFAULT_OPTIONS).all
    )


async def test_ports_of_a_disconnected_agent_are_unavailable(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    remote = {
        "id": "2",
        "name": "Remote",
        "status": "online",
        "enabled": True,
        "connected": True,
    }
    mock_client.async_get_environments.return_value = [remote]
    await _setup(hass, mock_config_entry, mock_client, {CONF_PORTS: True})
    assert hass.states.get("sensor.remote_host_ports_in_use").state == "3"

    mock_client.async_get_environments.return_value = [
        {**remote, "status": "offline", "connected": False}
    ]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.remote_host_ports_in_use").state == STATE_UNAVAILABLE


async def test_option_in_the_entities_step(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "entities"}
    )
    keys = {str(key): key for key in result["data_schema"].schema}
    assert keys[CONF_PORTS].description["suggested_value"] is False
    values = {name: DEFAULT_OPTIONS[name] for name in keys} | {CONF_PORTS: True}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], values
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_PORTS] is True
    assert hass.states.get("sensor.local_host_ports_in_use") is not None
