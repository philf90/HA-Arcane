"""Tests fuer 0.14.0: Arcane-Update ueber Upgrade-Jobs mit Fortschritt (B7).

Belege im Quelltext von Arcane v3.0.1 (Commit c614e0d2),
backend/internal/system/children/upgrade/:
- ``GET agents/{id}/system/upgrade/check`` liefert ``canUpgrade``, ``error``
  und ``message`` ohne Huelle (handler.go CheckUpgradeAvailable); falsch ist
  ``canUpgrade`` ohne Docker-Socket, ausserhalb eines Containers oder mit
  Quadlet (service.go CanUpgrade), nicht bei fehlender neuer Version.
- ``POST agents/0/system/upgrade/jobs`` mit ``environmentIds`` startet einen
  Job nur auf dem Manager (StartUpgradeJob, auf einem Agenten 400), zuerst
  die Agenten, zuletzt der Manager; ``GET .../jobs/current`` liefert den
  neuesten Job, 404 vor dem ersten.
- Job-Status ``running``, ``pending_restart``, ``completed``, ``failed``; je
  Umgebung ``status`` (``updated``, ``up_to_date``, ``triggered``,
  ``skipped_offline``, ``failed``), ``stage``, ``percent``, ``error``
  (model.go). Ein Job mit gescheiterter Umgebung endet trotzdem als
  ``completed`` (finalizeUpgradeJob).
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from homeassistant.components.update import UpdateEntityFeature
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arcane.api import (
    ArcaneClient,
    ArcaneConnectionError,
    ArcanePermissionError,
    ArcaneResponseError,
)
from custom_components.arcane.const import (
    CONF_ALLOW_CONTROL,
    DEFAULT_OPTIONS,
    UPGRADE_POLL_INTERVAL,
)
from custom_components.arcane.permissions import required_permissions

ENTITY = "update.local_arcane"


def _job(status: str, row_status: str, percent: int = 0, **row: Any) -> dict:
    return {
        "id": "job-1",
        "status": status,
        "results": [
            {
                "environmentId": "0",
                "environmentName": "Local",
                "status": row_status,
                "percent": percent,
                **row,
            }
        ],
    }


@pytest.fixture
def seen(hass: HomeAssistant) -> list[tuple[bool, Any]]:
    """Ersetzt die Wartezeit und merkt sich den Zustand bei jedem Blick."""
    states: list[tuple[bool, Any]] = []

    async def pause(seconds: float) -> None:
        assert seconds == UPGRADE_POLL_INTERVAL
        state = hass.states.get(ENTITY)
        states.append(
            (state.attributes["in_progress"], state.attributes["update_percentage"])
        )
        if len(states) > 20:
            raise AssertionError("wartet weiter, obwohl der Job vorbei ist")

    with patch("custom_components.arcane.update._pause", pause):
        yield states


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, options: dict | None = None
) -> None:
    entry.add_to_hass(hass)
    if options:
        hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _install(hass: HomeAssistant, entity_id: str = ENTITY) -> None:
    await hass.services.async_call(
        "update", "install", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


async def test_install_follows_the_job_and_shows_progress(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    seen: list,
) -> None:
    mock_client.async_start_upgrade_job.return_value = _job("running", "pending")
    mock_client.async_get_upgrade_job.side_effect = [
        _job("running", "updating", 40, stage="pulling"),
        # Der Manager startet sich neu: kurz nicht erreichbar, kein Fehler.
        ArcaneConnectionError("connection refused"),
        _job("pending_restart", "updating", 80, stage="upgrading"),
        _job("completed", "updated", 100, toVersion="1.5.0"),
    ]
    await _setup(hass, mock_config_entry)
    state = hass.states.get(ENTITY)
    assert state.attributes["supported_features"] & UpdateEntityFeature.PROGRESS

    await _install(hass)

    mock_client.async_check_upgrade.assert_awaited_once_with("0")
    mock_client.async_start_upgrade_job.assert_awaited_once_with(["0"])
    assert mock_client.async_get_upgrade_job.await_count == 4
    # Erst ohne Prozentangabe, dann der Stand aus Arcane; die Zeit ohne
    # Antwort behaelt den letzten Stand.
    assert seen == [(True, None), (True, 40), (True, 40), (True, 80)]
    state = hass.states.get(ENTITY)
    assert state.attributes["in_progress"] is False
    assert state.attributes["update_percentage"] is None


async def test_a_job_of_someone_else_is_not_taken_for_ours(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    seen: list,
) -> None:
    mock_client.async_start_upgrade_job.return_value = _job("running", "pending")
    other = {**_job("failed", "failed", error="old"), "id": "job-0"}
    mock_client.async_get_upgrade_job.side_effect = [
        None,
        other,
        _job("completed", "up_to_date", 100),
    ]
    await _setup(hass, mock_config_entry)

    await _install(hass)

    assert mock_client.async_get_upgrade_job.await_count == 3


async def test_arcane_that_cannot_upgrade_itself_is_not_started(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    seen: list,
) -> None:
    mock_client.async_check_upgrade.return_value = {
        "canUpgrade": False,
        "error": True,
        "message": "Failed to check for updates: docker socket is not accessible",
    }
    await _setup(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await _install(hass)

    assert err.value.translation_key == "upgrade_not_possible"
    assert err.value.translation_placeholders == {
        "name": "Local",
        "reason": "Failed to check for updates: docker socket is not accessible",
    }
    mock_client.async_start_upgrade_job.assert_not_awaited()
    assert hass.states.get(ENTITY).attributes["in_progress"] is False


async def test_a_failed_check_does_not_stop_the_upgrade(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    seen: list,
) -> None:
    mock_client.async_check_upgrade.side_effect = ArcanePermissionError("403")
    await _setup(hass, mock_config_entry)

    await _install(hass)

    mock_client.async_start_upgrade_job.assert_awaited_once_with(["0"])


@pytest.mark.parametrize(
    ("job", "shown"),
    [
        (
            _job("completed", "failed", 30, error="pull access denied"),
            "pull access denied",
        ),
        (_job("completed", "skipped_offline"), "skipped_offline"),
        (
            {
                **_job("failed", "updating", 10),
                "error": "interrupted by manager restart",
            },
            "interrupted by manager restart",
        ),
    ],
)
async def test_a_failed_upgrade_is_reported_with_arcanes_reason(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    seen: list,
    job: dict,
    shown: str,
) -> None:
    mock_client.async_start_upgrade_job.return_value = _job("running", "pending")
    mock_client.async_get_upgrade_job.return_value = job
    await _setup(hass, mock_config_entry)
    refreshes = mock_client.async_get_version.await_count

    with pytest.raises(HomeAssistantError) as err:
        await _install(hass)

    assert err.value.translation_key == "upgrade_failed"
    assert err.value.translation_placeholders == {"name": "Local", "error": shown}
    # Der Stand nach dem Upgrade wird trotzdem abgefragt.
    assert mock_client.async_get_version.await_count > refreshes
    assert hass.states.get(ENTITY).attributes["in_progress"] is False


async def test_an_unconfirmed_upgrade_is_only_logged(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    seen: list,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_client.async_start_upgrade_job.return_value = _job("completed", "triggered")
    await _setup(hass, mock_config_entry)

    with caplog.at_level(logging.WARNING):
        await _install(hass)

    assert "did not confirm the new version" in caplog.text
    mock_client.async_get_upgrade_job.assert_not_awaited()


async def test_a_refused_start_names_the_permission(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    seen: list,
) -> None:
    mock_client.async_start_upgrade_job.side_effect = ArcanePermissionError("403")
    await _setup(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await _install(hass)

    assert err.value.translation_key == "upgrade_forbidden"
    assert err.value.translation_placeholders["permission"] == "system:upgrade"


async def test_a_job_already_running_is_reported(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    seen: list,
) -> None:
    mock_client.async_start_upgrade_job.side_effect = ArcaneResponseError(
        "HTTP 409: an upgrade job is already in progress"
    )
    await _setup(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await _install(hass)

    assert err.value.translation_key == "upgrade_failed"
    assert "already in progress" in err.value.translation_placeholders["error"]


async def test_an_unreadable_job_lets_the_upgrade_go_on(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    seen: list,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_client.async_start_upgrade_job.return_value = _job("running", "pending")
    mock_client.async_get_upgrade_job.side_effect = ArcanePermissionError("403")
    await _setup(hass, mock_config_entry)

    with caplog.at_level(logging.WARNING):
        await _install(hass)

    assert "lacks system:read" in caplog.text
    assert mock_client.async_get_upgrade_job.await_count == 1
    coordinator = mock_config_entry.runtime_data
    assert "system:read" in coordinator.missing_permissions


async def test_an_upgrade_that_does_not_end_times_out(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    seen: list,
) -> None:
    mock_client.async_start_upgrade_job.return_value = _job("running", "pending")
    mock_client.async_get_upgrade_job.return_value = _job("running", "updating", 50)
    await _setup(hass, mock_config_entry)

    with (
        patch("custom_components.arcane.update.UPGRADE_WAIT", 1800),
        patch(
            "custom_components.arcane.update.monotonic", side_effect=[0, 0, 900, 1800]
        ),
        pytest.raises(HomeAssistantError) as err,
    ):
        await _install(hass)

    assert err.value.translation_key == "upgrade_timeout"
    assert err.value.translation_placeholders == {"name": "Local", "minutes": "30"}
    assert mock_client.async_get_upgrade_job.await_count == 2


async def test_a_remote_environment_is_upgraded_by_its_id(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    seen: list,
) -> None:
    mock_client.async_get_environments.return_value = [
        {"id": "0", "name": "Local", "status": "online", "enabled": True},
        {"id": "7", "name": "Remote", "status": "online", "enabled": True},
    ]
    mock_client.async_get_environment_version.return_value = {
        "currentVersion": "1.3.0",
        "newestVersion": "1.5.0",
        "updateAvailable": True,
    }
    mock_client.async_start_upgrade_job.return_value = {
        "id": "job-1",
        "status": "completed",
        "results": [
            # Eine andere Umgebung im selben Job zaehlt nicht.
            {"environmentId": "0", "status": "failed", "error": "nicht meins"},
            {"environmentId": "7", "status": "updated", "percent": 100},
        ],
    }
    await _setup(hass, mock_config_entry)

    await _install(hass, "update.remote_arcane")

    mock_client.async_check_upgrade.assert_awaited_once_with("7")
    mock_client.async_start_upgrade_job.assert_awaited_once_with(["7"])


async def test_progress_needs_control_and_reads_are_requested(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    control = required_permissions(DEFAULT_OPTIONS).control
    assert "system:upgrade" in control
    assert "system:read" in control

    await _setup(hass, mock_config_entry, {CONF_ALLOW_CONTROL: False})
    features = hass.states.get(ENTITY).attributes["supported_features"]
    assert not features & UpdateEntityFeature.INSTALL
    assert not features & UpdateEntityFeature.PROGRESS


async def test_client_sends_jobs_to_the_manager(aiohttp_client, socket_enabled) -> None:
    """Jobs gehen immer an Umgebung 0, die Pruefung an die eigene Umgebung."""
    seen: list[tuple[str, str, Any]] = []
    answers = {"current": 404}

    async def check(request: web.Request) -> web.Response:
        seen.append((request.method, request.path, None))
        return web.json_response({"canUpgrade": True, "error": False, "message": "ok"})

    async def jobs(request: web.Request) -> web.Response:
        seen.append((request.method, request.path, await request.json()))
        return web.json_response(
            {"success": True, "data": {"id": "job-1", "status": "running"}},
            status=202,
        )

    async def current(request: web.Request) -> web.Response:
        seen.append((request.method, request.path, None))
        if answers["current"] == 404:
            return web.json_response({"error": "no upgrade job found"}, status=404)
        return web.json_response({"success": True, "data": {"id": "job-1"}})

    app = web.Application()
    app.router.add_get("/api/agents/7/system/upgrade/check", check)
    app.router.add_post("/api/agents/0/system/upgrade/jobs", jobs)
    app.router.add_get("/api/agents/0/system/upgrade/jobs/current", current)
    http = await aiohttp_client(app)
    client = ArcaneClient(http.session, str(http.make_url("")), "arc_test")

    assert (await client.async_check_upgrade("7"))["canUpgrade"] is True
    assert (await client.async_start_upgrade_job(["7"]))["id"] == "job-1"
    assert await client.async_get_upgrade_job() is None
    answers["current"] = 200
    assert await client.async_get_upgrade_job() == {"id": "job-1"}
    assert seen == [
        ("GET", "/api/agents/7/system/upgrade/check", None),
        ("POST", "/api/agents/0/system/upgrade/jobs", {"environmentIds": ["7"]}),
        ("GET", "/api/agents/0/system/upgrade/jobs/current", None),
        ("GET", "/api/agents/0/system/upgrade/jobs/current", None),
    ]
