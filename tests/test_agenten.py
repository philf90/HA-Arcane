"""Tests fuer 0.11.0 und 0.11.1: mehrere Hosts mit Arcane-Agenten.

Belege im Quelltext von Arcane v3.0.1 (Commit c614e0d2):
- ``GET agents`` liefert je Umgebung die Agent-Felder ``connected``,
  ``connectedAt``, ``lastHeartbeat``, ``lastSeen``, ``agentRoundTripMs``,
  ``agentTransport``/``lastAgentTransport``, ``agentSecurityMode``,
  ``agentHost`` und ``agentTunnelStats`` (types/environment.go:18-78,
  environment/lister.go:39 und 186-223).
- Eine entfernte Umgebung ist genau dann ``online``, wenn der Tunnel des
  Agenten steht; sonst ``offline`` oder bis zur ersten Verbindung ``pending``
  (lister.go:197-205). Ohne Tunnel wartet Arcane bis 4 s und antwortet 503
  (middleware/environment.go:150-159).
- Jobs mit ``managerOnly`` laufen nur auf der Hauptinstanz (types/meta.go).
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arcane.const import (
    CONF_DISK_USAGE,
    CONF_ENVIRONMENT_IN_NAME,
    CONF_ENVIRONMENTS,
    CONF_HOST_STATS,
    CONF_INSTANT_UPDATES,
    CONF_PRUNE_BUTTON,
    DOMAIN,
    MODEL_AGENT,
    MODEL_ENVIRONMENT,
)
from custom_components.arcane.models import ArcaneAgent
from tests.conftest import VERSION_PAYLOAD

LOCAL = {"id": "0", "name": "Local", "status": "online", "enabled": True}
REMOTE: dict[str, Any] = {
    "id": "2",
    "name": "Remote",
    "apiUrl": "agent://2",
    "status": "online",
    "enabled": True,
    "connected": True,
    "connectedAt": "2026-11-16T08:00:00Z",
    "lastHeartbeat": "2026-11-16T11:59:30.123456789Z",
    "lastSeen": "2026-11-16T11:59:00Z",
    "agentRoundTripMs": 1.734,
    "agentTransport": "websocket",
    "agentSecurityMode": "token",
    "agentHost": {
        "hostname": "host-2",
        "os": "linux",
        "kernelVersion": "6.8.0",
        "architecture": "amd64",
        "dockerVersion": "29.0.1",
        "agentVersion": "v3.0.2",
        "cpus": 4,
        "memoryBytes": 8_000_000_000,
    },
    "agentTunnelStats": {
        "lastDisconnectAt": "2026-11-15T22:10:00Z",
        "lastDisconnectReason": "manager restart",
        "bytesIn": 1,
        "bytesOut": 2,
        "streamsOpened": 3,
        "sessions": 2,
    },
}


def _remote(**fields: Any) -> dict[str, Any]:
    remote = copy.deepcopy(REMOTE)
    remote.update(fields)
    return remote


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    client: AsyncMock,
    environments: list[dict[str, Any]],
    options: dict | None = None,
) -> None:
    client.async_get_environments.return_value = environments
    entry.add_to_hass(hass)
    if options:
        hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _environment_ids(call: AsyncMock) -> set[str]:
    return {args.args[0] for args in call.await_args_list}


def _device(hass: HomeAssistant, entry: MockConfigEntry, environment_id: str):
    return dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_{environment_id}")}
    )


# --- Phase 1: Zustand des Agenten -------------------------------------------


async def test_agent_sensors_only_for_remote_environments(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry, mock_client, [LOCAL, REMOTE])

    contact = hass.states.get("sensor.remote_agent_last_contact")
    # Heartbeat vor lastSeen; Home Assistant zeigt Zeitstempel auf die Sekunde.
    assert datetime.fromisoformat(contact.state) == datetime(
        2026, 11, 16, 11, 59, 30, tzinfo=UTC
    )
    assert datetime.fromisoformat(
        hass.states.get("sensor.remote_agent_connected_since").state
    ) == datetime(2026, 11, 16, 8, 0, tzinfo=UTC)
    latency = hass.states.get("sensor.remote_agent_latency")
    assert float(latency.state) == pytest.approx(1.734)
    assert latency.attributes["unit_of_measurement"] == "ms"
    disconnect = hass.states.get("sensor.remote_agent_last_disconnect")
    assert datetime.fromisoformat(disconnect.state) == datetime(
        2026, 11, 15, 22, 10, tzinfo=UTC
    )
    assert disconnect.attributes["reason"] == "manager restart"

    for key in ("last_contact", "connected_since", "latency", "last_disconnect"):
        assert hass.states.get(f"sensor.local_agent_{key}") is None


async def test_online_sensor_names_host_and_connection(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry, mock_client, [LOCAL, REMOTE])

    remote = hass.states.get("binary_sensor.remote_online")
    assert remote.state == STATE_ON
    assert remote.attributes["hostname"] == "host-2"
    assert remote.attributes["agent_version"] == "v3.0.2"
    assert remote.attributes["docker_version"] == "29.0.1"
    assert remote.attributes["architecture"] == "amd64"
    assert remote.attributes["cpus"] == 4
    assert remote.attributes["transport"] == "websocket"
    assert remote.attributes["security_mode"] == "token"
    assert "hostname" not in hass.states.get("binary_sensor.local_online").attributes


async def test_devices_show_who_serves_the_environment(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry, mock_client, [LOCAL, REMOTE])

    local = _device(hass, mock_config_entry, "0")
    remote = _device(hass, mock_config_entry, "2")
    assert (local.model, local.sw_version) == (
        MODEL_ENVIRONMENT,
        VERSION_PAYLOAD["currentVersion"],
    )
    # Bis 0.10.1 stand hier die Version der Hauptinstanz.
    assert (remote.model, remote.sw_version) == (MODEL_AGENT, "v3.0.2")


async def test_device_version_follows_an_agent_upgrade(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry, mock_client, [LOCAL, REMOTE])

    upgraded = _remote()
    upgraded["agentHost"]["agentVersion"] = "v3.1.0"
    mock_client.async_get_environments.return_value = [LOCAL, upgraded]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert _device(hass, mock_config_entry, "2").sw_version == "v3.1.0"


def test_agent_model_reads_what_arcane_sends() -> None:
    agent = ArcaneAgent.from_api(
        {
            "lastSeen": "2026-11-16T11:00:00Z",
            "lastAgentTransport": "grpc",
            "agentHost": {"cpus": True, "memoryBytes": "viel"},
            "agentRoundTripMs": False,
        }
    )
    assert agent.connected is False
    assert agent.last_contact == datetime(2026, 11, 16, 11, 0, tzinfo=UTC)
    # Getrennt: der letzte Transport bleibt sichtbar.
    assert agent.transport == "grpc"
    assert agent.cpus is None
    assert agent.memory_bytes is None
    assert agent.round_trip_ms is None
    assert ArcaneAgent.from_api({}).last_contact is None


# --- Phase 2: getrennte Agenten nicht abfragen -------------------------------


@pytest.mark.parametrize("status", ["offline", "pending"])
async def test_disconnected_agent_is_not_asked(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    status: str,
) -> None:
    offline = _remote(status=status, connected=False, lastHeartbeat=None)
    with patch(
        "custom_components.arcane.changes.ArcaneChangeWatcher.async_sync"
    ) as sync:
        await _setup(
            hass,
            mock_config_entry,
            mock_client,
            [LOCAL, offline],
            {CONF_DISK_USAGE: True, CONF_INSTANT_UPDATES: True},
        )

    for call in (
        mock_client.async_get_containers,
        mock_client.async_get_projects,
        mock_client.async_get_image_counts,
        mock_client.async_get_disk_usage,
    ):
        assert _environment_ids(call) == {"0"}, call
    mock_client.async_get_environment_version.assert_not_awaited()
    assert sync.call_args.args[0] == {"0"}

    assert hass.states.get("binary_sensor.remote_online").state == STATE_OFF
    assert hass.states.get("binary_sensor.local_online").state == STATE_ON
    # Getrennt bleiben letzter Kontakt (lastSeen) und letzte Trennung.
    assert datetime.fromisoformat(
        hass.states.get("sensor.remote_agent_last_contact").state
    ) == datetime(2026, 11, 16, 11, 59, tzinfo=UTC)
    assert hass.states.get("sensor.remote_agent_last_disconnect").attributes[
        "reason"
    ] == ("manager restart")
    # Verbindungsdauer und Latenz gibt es ohne Verbindung nicht (0.11.1).
    assert hass.states.get("sensor.remote_agent_connected_since").state == (
        STATE_UNAVAILABLE
    )
    assert hass.states.get("sensor.remote_agent_latency").state == STATE_UNAVAILABLE


# Live am 16.11.2026 mit gestopptem Agent-Container gesehen (0.11.0): Zaehler
# der Umgebung standen auf 0, Speicherwerte auf dem alten Stand und die Knoepfe
# blieben bedienbar, obwohl nichts abgefragt wurde. Ab 0.11.1 nicht verfuegbar.
STALE_WHILE_DISCONNECTED = (
    "sensor.remote_containers",
    "sensor.remote_containers_running",
    "sensor.remote_containers_stopped",
    "sensor.remote_projects_running",
    "sensor.remote_container_updates_available",
    "sensor.remote_unhealthy_containers",
    "sensor.remote_images",
    "sensor.remote_host_cpu",
    "sensor.remote_reclaimable_image_space",
    "sensor.remote_agent_connected_since",
    "sensor.remote_agent_latency",
    "button.remote_prune_unused",
)


async def test_entities_of_a_disconnected_agent_become_unavailable(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(
        hass,
        mock_config_entry,
        mock_client,
        [LOCAL, REMOTE],
        {CONF_DISK_USAGE: True, CONF_HOST_STATS: True, CONF_PRUNE_BUTTON: "unused"},
    )
    for entity_id in STALE_WHILE_DISCONNECTED:
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.state != STATE_UNAVAILABLE, entity_id

    mock_client.async_get_environments.return_value = [
        LOCAL,
        _remote(status="offline", connected=False, lastHeartbeat=None),
    ]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    for entity_id in STALE_WHILE_DISCONNECTED:
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE, entity_id
    # Was die Verbindung selbst beschreibt, bleibt.
    assert hass.states.get("binary_sensor.remote_online").state == STATE_OFF
    for entity_id in (
        "sensor.remote_agent_last_contact",
        "sensor.remote_agent_last_disconnect",
    ):
        assert hass.states.get(entity_id).state != STATE_UNAVAILABLE, entity_id
    # Die lokale Umgebung ist nicht betroffen.
    for key in ("containers", "host_cpu", "reclaimable_image_space"):
        assert hass.states.get(f"sensor.local_{key}").state != STATE_UNAVAILABLE
    assert hass.states.get("button.local_prune_unused").state != STATE_UNAVAILABLE

    mock_client.async_get_environments.return_value = [LOCAL, REMOTE]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    for entity_id in STALE_WHILE_DISCONNECTED:
        assert hass.states.get(entity_id).state != STATE_UNAVAILABLE, entity_id


async def test_last_contact_does_not_go_back_after_a_disconnect(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Live gesehen: Heartbeat 13:37:25, danach lastSeen 13:36:59 (0.11.0)."""
    await _setup(hass, mock_config_entry, mock_client, [LOCAL, REMOTE])

    def contact() -> datetime:
        return datetime.fromisoformat(
            hass.states.get("sensor.remote_agent_last_contact").state
        )

    assert contact() == datetime(2026, 11, 16, 11, 59, 30, tzinfo=UTC)

    # lastSeen (11:59:00) liegt vor dem letzten Heartbeat.
    mock_client.async_get_environments.return_value = [
        LOCAL,
        _remote(status="offline", connected=False, lastHeartbeat=None),
    ]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert contact() == datetime(2026, 11, 16, 11, 59, 30, tzinfo=UTC)

    # Wieder verbunden: ein neuerer Heartbeat gilt.
    mock_client.async_get_environments.return_value = [
        LOCAL,
        _remote(lastHeartbeat="2026-11-16T12:05:00Z"),
    ]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert contact() == datetime(2026, 11, 16, 12, 5, tzinfo=UTC)


def test_keep_later_contact() -> None:
    later = ArcaneAgent(last_heartbeat=datetime(2026, 11, 16, 12, 0, tzinfo=UTC))
    earlier = ArcaneAgent(last_seen=datetime(2026, 11, 16, 11, 0, tzinfo=UTC))
    earlier.keep_later_contact(later)
    assert earlier.last_contact == datetime(2026, 11, 16, 12, 0, tzinfo=UTC)

    newer = ArcaneAgent(last_heartbeat=datetime(2026, 11, 16, 13, 0, tzinfo=UTC))
    newer.keep_later_contact(later)
    assert newer.last_contact == datetime(2026, 11, 16, 13, 0, tzinfo=UTC)

    unknown = ArcaneAgent()
    unknown.keep_later_contact(later)
    assert unknown.last_contact == datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    unknown = ArcaneAgent()
    unknown.keep_later_contact(None)
    assert unknown.last_contact is None


async def test_reconnected_agent_is_asked_again(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(
        hass,
        mock_config_entry,
        mock_client,
        [LOCAL, _remote(status="offline", connected=False)],
    )
    assert _environment_ids(mock_client.async_get_containers) == {"0"}

    mock_client.async_get_environments.return_value = [LOCAL, REMOTE]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert _environment_ids(mock_client.async_get_containers) == {"0", "2"}
    assert hass.states.get("binary_sensor.remote_online").state == STATE_ON


async def test_local_environment_is_always_asked(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Der Status der lokalen Umgebung kommt aus der Datenbank, nicht vom Tunnel."""
    await _setup(hass, mock_config_entry, mock_client, [{**LOCAL, "status": "error"}])

    assert _environment_ids(mock_client.async_get_containers) == {"0"}


# --- Phase 3: Umgebung im Geraetenamen ----------------------------------------


async def test_same_container_on_two_hosts_without_the_option(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Bisheriges Verhalten: der zweite Host bekommt _2."""
    await _setup(hass, mock_config_entry, mock_client, [LOCAL, REMOTE])

    assert hass.states.get("switch.container_homeassistant") is not None
    assert hass.states.get("switch.container_homeassistant_2") is not None


async def test_environment_in_device_names(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(
        hass,
        mock_config_entry,
        mock_client,
        [LOCAL, REMOTE],
        {CONF_ENVIRONMENT_IN_NAME: True},
    )

    # Die lokale Umgebung behaelt ihre Namen.
    assert hass.states.get("switch.container_homeassistant") is not None
    assert hass.states.get("switch.container_remote_homeassistant") is not None
    assert hass.states.get("switch.container_homeassistant_2") is None
    assert hass.states.get("switch.project_smarthome") is not None
    assert hass.states.get("switch.project_remote_smarthome") is not None
    device = dr.async_get(hass).async_get_device(
        identifiers={
            (DOMAIN, f"{mock_config_entry.entry_id}_2_container_homeassistant")
        }
    )
    assert device.name == "Container Remote homeassistant"


async def test_environment_in_name_without_kind_prefix(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(
        hass,
        mock_config_entry,
        mock_client,
        [LOCAL, REMOTE],
        {CONF_ENVIRONMENT_IN_NAME: True, "entity_prefix": False},
    )

    assert hass.states.get("switch.remote_homeassistant") is not None


# --- Phase 4: Jobs und Hinweise ------------------------------------------------


async def test_manager_only_jobs_stay_in_the_local_environment(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_jobs.return_value = [
        {"id": "environment-health", "name": "Environment Health", "managerOnly": True},
        {"id": "auto-update", "name": "Auto Update", "managerOnly": False},
    ]
    await _setup(hass, mock_config_entry, mock_client, [LOCAL, REMOTE], {"jobs": True})

    environments = mock_config_entry.runtime_data.data.environments
    assert set(environments["0"].jobs) == {"environment-health", "auto-update"}
    assert set(environments["2"].jobs) == {"auto-update"}


async def test_environment_left_out_by_the_options_is_pointed_out(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    third = {**_remote(id="3", name="Third")}
    disabled = {**_remote(id="4", name="Off"), "enabled": False}
    await _setup(
        hass,
        mock_config_entry,
        mock_client,
        [LOCAL, REMOTE, third, disabled],
        {CONF_ENVIRONMENTS: ["0", "2"]},
    )
    issues = ir.async_get(hass)
    entry_id = mock_config_entry.entry_id

    issue = issues.async_get_issue(DOMAIN, f"unselected_environment_{entry_id}_3")
    assert issue is not None
    assert issue.translation_key == "unselected_environment"
    assert issue.translation_placeholders == {
        "name": mock_config_entry.title,
        "environment": "Third",
    }
    # Ausgeschaltete Umgebungen nicht, ausgewaehlte nicht.
    assert (
        issues.async_get_issue(DOMAIN, f"unselected_environment_{entry_id}_4") is None
    )
    assert (
        issues.async_get_issue(DOMAIN, f"unselected_environment_{entry_id}_2") is None
    )

    # Verschwindet die Umgebung, verschwindet der Hinweis.
    mock_client.async_get_environments.return_value = [LOCAL, REMOTE]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert (
        issues.async_get_issue(DOMAIN, f"unselected_environment_{entry_id}_3") is None
    )


async def test_no_hint_without_a_selection_and_none_after_unload(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(
        hass,
        mock_config_entry,
        mock_client,
        [LOCAL, REMOTE],
        {CONF_ENVIRONMENTS: ["0"]},
    )
    issues = ir.async_get(hass)
    issue_id = f"unselected_environment_{mock_config_entry.entry_id}_2"
    assert issues.async_get_issue(DOMAIN, issue_id) is not None

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, issue_id) is None

    # Ohne Auswahl gehoeren alle Umgebungen dazu: kein Hinweis.
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_ENVIRONMENTS: []}
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, issue_id) is None
    assert hass.states.get("binary_sensor.remote_online") is not None
