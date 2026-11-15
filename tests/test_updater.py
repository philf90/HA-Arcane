"""Tests fuer 0.9.0: Updates ueber Arcanes Updater, Auto-Update-Schalter, Updater.

Belege im Quelltext von Arcane v3.0.1 und kit.getarcane.app/updater v0.12.3:
- ``POST containers/{id}/update`` laeuft durch den Updater (updater/handler.go,
  UpdateSingleContainer); der folgt der Tag-Regel, ueberspringt unveraenderte
  Images mit Grund in ``error`` und zieht ueber Arcanes Schwachstellen-Pruefung
  (container.go, updater/service.go scanBeforeUpdate). Ein Redeploy zieht die
  alte Referenz erneut (container/service.go pullRedeployImage).
- ``PUT containers/{id}/autoupdate`` aendert nur die Liste
  ``autoUpdateExcludedContainers`` (container/handler.go SetAutoUpdate);
  ``autoUpdateEnabled`` ist falsch bei Label ``com.getarcaneapp.arcane.updater``
  mit einem Wert, den kit.ParseBool als falsch liest.
- ``POST updater/run?automatic=true`` beachtet Ausnahmen wie geplante Laeufe.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arcane.api import ArcaneConnectionError, ArcanePermissionError
from custom_components.arcane.const import (
    CONF_ALLOW_CONTROL,
    CONF_AUTO_UPDATE_SWITCHES,
    CONF_UPDATER,
)
from custom_components.arcane.models import ArcaneContainer
from custom_components.arcane.permissions import required_permissions
from tests.conftest import CONTAINERS_PAYLOAD

UPDATE = "update.container_homeassistant_image_update"
AUTO_UPDATE = "switch.container_homeassistant_auto_update"
RUNNING = "binary_sensor.local_updates_being_installed"
LAST = "sensor.local_last_image_update"
APPLY = "button.local_install_all_updates"


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, options: dict | None = None
) -> None:
    entry.add_to_hass(hass)
    if options:
        hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _containers(**fields: Any) -> list[dict[str, Any]]:
    containers = copy.deepcopy(CONTAINERS_PAYLOAD)
    containers[0].update(fields)
    return containers


async def _install(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "update", "install", {ATTR_ENTITY_ID: UPDATE}, blocking=True
    )


# --- A5: Installieren ueber den Updater -------------------------------------


async def test_install_goes_through_the_updater(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_update_container.return_value = {
        "items": [{"resourceId": "c1ffee", "status": "updated"}]
    }
    await _setup(hass, mock_config_entry)
    calls_before = mock_client.async_get_containers.await_count

    await _install(hass)
    await hass.async_block_till_done()

    mock_client.async_update_container.assert_awaited_once_with("0", "c1ffee")
    mock_client.async_container_action.assert_not_awaited()
    assert mock_client.async_get_containers.await_count > calls_before


async def test_skipped_update_is_reported_with_its_reason(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_update_container.return_value = {
        "items": [
            {
                "resourceId": "c1ffee",
                "status": "skipped",
                "error": "image digest unchanged after pull",
            }
        ]
    }
    await _setup(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await _install(hass)
    assert err.value.translation_key == "update_not_applied"
    assert err.value.translation_placeholders == {
        "name": "homeassistant",
        "status": "skipped",
        "reason": "image digest unchanged after pull",
    }


async def test_gate_refusal_is_reported(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    reason = "pull failed: update blocked by vulnerability gate: critical 2 > 0"
    mock_client.async_update_container.return_value = {
        "items": [{"resourceId": "c1ffee", "status": "failed", "error": reason}],
        "failed": 1,
    }
    await _setup(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await _install(hass)
    assert err.value.translation_placeholders["reason"] == reason


async def test_own_result_decides_among_several(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Ein Compose-Update kann Nachbarn neu starten; zaehlt nur der eigene."""
    mock_client.async_update_container.return_value = {
        "items": [
            {"resourceId": "other", "status": "failed", "error": "x"},
            {"resourceId": "c1ffee", "status": "updated"},
        ]
    }
    await _setup(hass, mock_config_entry)

    await _install(hass)


async def test_empty_result_is_not_taken_as_success(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_update_container.return_value = {"items": []}
    await _setup(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await _install(hass)
    assert err.value.translation_placeholders["status"] == "-"


async def test_refused_install_names_the_permission(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_update_container.side_effect = ArcanePermissionError("nope")
    await _setup(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await _install(hass)
    assert err.value.translation_key == "image_update_forbidden"
    assert err.value.translation_placeholders["permission"] == "image-updates:apply"
    assert "image-updates:apply" in mock_config_entry.runtime_data.missing_permissions


# --- B1: Auto-Update-Schalter -----------------------------------------------


@pytest.mark.parametrize(
    ("labels", "disabled"),
    [
        ({"com.getarcaneapp.arcane.updater": "false"}, True),
        ({"COM.GetArcaneApp.Arcane.Updater": " OFF "}, True),
        ({"com.getarcaneapp.arcane.updater": "0"}, True),
        ({"com.getarcaneapp.arcane.updater": "no"}, True),
        ({"com.getarcaneapp.arcane.updater": "true"}, False),
        ({"com.getarcaneapp.arcane.updater": "vielleicht"}, False),
        ({}, False),
    ],
)
def test_label_opt_out_is_read_like_arcane(labels: dict, disabled: bool) -> None:
    container = ArcaneContainer.from_api(_containers(labels=labels)[0])
    assert container.auto_update_label_disabled is disabled


async def test_auto_update_switches_off_by_default(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)

    assert hass.states.get(AUTO_UPDATE) is None


async def test_auto_update_switch(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_containers.return_value = _containers(autoUpdateEnabled=True)
    await _setup(hass, mock_config_entry, {CONF_AUTO_UPDATE_SWITCHES: True})

    assert hass.states.get(AUTO_UPDATE).state == STATE_ON

    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: AUTO_UPDATE}, blocking=True
    )
    mock_client.async_set_auto_update.assert_awaited_once_with(
        "0", "c1ffee", enabled=False
    )


async def test_label_opt_out_cannot_be_switched_on(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_containers.return_value = _containers(
        autoUpdateEnabled=False,
        labels={"com.getarcaneapp.arcane.updater": "false"},
    )
    await _setup(hass, mock_config_entry, {CONF_AUTO_UPDATE_SWITCHES: True})
    assert hass.states.get(AUTO_UPDATE).state == STATE_OFF

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "switch", "turn_on", {ATTR_ENTITY_ID: AUTO_UPDATE}, blocking=True
        )
    assert err.value.translation_key == "auto_update_label"
    mock_client.async_set_auto_update.assert_not_awaited()


async def test_auto_update_switch_unavailable_without_the_field(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry, {CONF_AUTO_UPDATE_SWITCHES: True})

    assert hass.states.get(AUTO_UPDATE).state == STATE_UNAVAILABLE


async def test_refused_auto_update_names_the_permission(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_containers.return_value = _containers(autoUpdateEnabled=False)
    mock_client.async_set_auto_update.side_effect = ArcanePermissionError("nope")
    await _setup(hass, mock_config_entry, {CONF_AUTO_UPDATE_SWITCHES: True})

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "switch", "turn_on", {ATTR_ENTITY_ID: AUTO_UPDATE}, blocking=True
        )
    assert err.value.translation_key == "auto_update_forbidden"
    assert err.value.translation_placeholders["permission"] == "containers:autoupdate"


async def test_auto_update_switch_needs_control(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(
        hass,
        mock_config_entry,
        {CONF_AUTO_UPDATE_SWITCHES: True, CONF_ALLOW_CONTROL: False},
    )

    assert hass.states.get(AUTO_UPDATE) is None


# --- B4: Updater -------------------------------------------------------------


async def test_updater_off_by_default(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)

    mock_client.async_get_updater_status.assert_not_awaited()
    mock_client.async_get_updater_history.assert_not_awaited()
    for entity_id in (RUNNING, LAST, APPLY):
        assert hass.states.get(entity_id) is None


async def test_updater_status_and_last_update(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_updater_status.return_value = {
        "updatingContainers": 2,
        "updatingProjects": 0,
        "containerIds": ["a", "b"],
        "projectIds": [],
    }
    mock_client.async_get_updater_history.return_value = [
        {
            "id": "r1",
            "resourceName": "webapp",
            "resourceType": "container",
            "status": "completed",
            "updateApplied": True,
            "startTime": "2026-11-15T20:00:00Z",
            "endTime": "2026-11-15T20:01:30Z",
        }
    ]
    await _setup(hass, mock_config_entry, {CONF_UPDATER: True})

    mock_client.async_get_updater_history.assert_awaited_with("0", limit=1)
    assert hass.states.get(RUNNING).state == STATE_ON
    last = hass.states.get(LAST)
    assert datetime.fromisoformat(last.state) == datetime.fromisoformat(
        "2026-11-15T20:01:30+00:00"
    )
    assert last.attributes["resource_name"] == "webapp"
    assert last.attributes["status"] == "completed"
    assert last.attributes["update_applied"] is True


async def test_last_update_falls_back_to_its_start(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_updater_history.return_value = [
        {"resourceName": "webapp", "startTime": "2026-11-15T20:00:00Z"}
    ]
    await _setup(hass, mock_config_entry, {CONF_UPDATER: True})

    assert hass.states.get(RUNNING).state == STATE_OFF
    assert datetime.fromisoformat(
        hass.states.get(LAST).state
    ) == datetime.fromisoformat("2026-11-15T20:00:00+00:00")


async def test_apply_updates_runs_in_the_background(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    release = asyncio.Event()

    async def run(environment_id: str) -> dict:
        await release.wait()
        return {"updated": 1, "skipped": 0, "failed": 0, "items": []}

    mock_client.async_run_updater.side_effect = run
    # Ohne die 10 s Abklingzeit des Coordinators zaehlt jede Aktualisierung.
    cooldown = patch(
        "homeassistant.helpers.update_coordinator.REQUEST_REFRESH_DEFAULT_COOLDOWN", 0
    )
    cooldown.start()
    await _setup(hass, mock_config_entry, {CONF_UPDATER: True})

    # Der Druck kehrt sofort zurueck, waehrend Arcane noch installiert.
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: APPLY}, blocking=True
    )
    mock_client.async_run_updater.assert_awaited_once_with("0")
    assert "0" in mock_config_entry.runtime_data.updater_runs

    # Ein zweiter Druck startet keinen zweiten Lauf.
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "button", "press", {ATTR_ENTITY_ID: APPLY}, blocking=True
        )
    assert err.value.translation_key == "apply_updates_running"

    calls_before = mock_client.async_get_updater_status.await_count
    release.set()
    await hass.async_block_till_done()
    assert mock_config_entry.runtime_data.updater_runs == set()
    assert mock_client.async_get_updater_status.await_count > calls_before
    cooldown.stop()


async def test_refused_run_names_the_permission(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_client.async_run_updater.side_effect = ArcanePermissionError("nope")
    await _setup(hass, mock_config_entry, {CONF_UPDATER: True})

    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: APPLY}, blocking=True
    )
    await hass.async_block_till_done()

    assert "image-updates:apply" in mock_config_entry.runtime_data.missing_permissions
    assert "image-updates:apply" in caplog.text
    assert mock_config_entry.runtime_data.updater_runs == set()


async def test_failed_run_is_logged_and_frees_the_button(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_client.async_run_updater.side_effect = ArcaneConnectionError("weg")
    await _setup(hass, mock_config_entry, {CONF_UPDATER: True})

    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: APPLY}, blocking=True
    )
    await hass.async_block_till_done()

    assert "Installing the updates of Local failed" in caplog.text
    assert mock_config_entry.runtime_data.updater_runs == set()


async def test_updater_button_needs_control(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(
        hass, mock_config_entry, {CONF_UPDATER: True, CONF_ALLOW_CONTROL: False}
    )

    assert hass.states.get(APPLY) is None
    assert hass.states.get(RUNNING) is not None


def test_permissions_of_the_new_options() -> None:
    needed = required_permissions({CONF_UPDATER: True, CONF_AUTO_UPDATE_SWITCHES: True})
    assert {"image-updates:read", "image-updates:list"} <= set(needed.monitoring)
    assert {"image-updates:apply", "containers:autoupdate"} <= set(needed.control)


async def test_client_sends_what_arcane_expects(aiohttp_client, socket_enabled) -> None:
    """Gegen einen HTTP-Stub: automatic=true beim Lauf, Body beim Schalter."""
    from aiohttp import web

    from custom_components.arcane.api import ArcaneClient

    seen: list[tuple[str, str, dict, Any]] = []

    async def record(request: web.Request) -> web.Response:
        body = await request.json() if request.can_read_body else None
        seen.append((request.method, request.path, dict(request.query), body))
        return web.json_response({"success": True, "data": {"items": []}})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", record)
    http = await aiohttp_client(app)
    client = ArcaneClient(http.session, str(http.make_url("")), "arc_test")

    await client.async_run_updater("0")
    await client.async_set_auto_update("0", "c1", enabled=False)
    await client.async_update_container("0", "c1")

    assert seen == [
        ("POST", "/api/agents/0/updater/run", {"automatic": "true"}, None),
        ("PUT", "/api/agents/0/containers/c1/autoupdate", {}, {"enabled": False}),
        ("POST", "/api/agents/0/containers/c1/update", {}, None),
    ]
