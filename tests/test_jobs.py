"""Tests fuer 0.10.0: Arcanes Hintergrund-Jobs (C1).

Belege im Quelltext von Arcane v3.0.1 (Commit c614e0d2):
- ``GET agents/{id}/jobs`` antwortet ohne Huelle mit ``JobListResponse``
  (backend/internal/job/handler.go ListJobs, types/jobschedule.go); Recht
  ``jobs:list`` (backend/internal/job/module.go).
- ``lastRun`` ist der neueste beendete Lauf, ``currentRun`` ein wartender oder
  laufender, ``lastError`` die Meldung eines fehlgeschlagenen, teilweisen oder
  wiederholten Laufs (backend/internal/job/children/runtime/service.go
  ApplyRunStatus); einen Lauf, der Aufmerksamkeit braucht, zeigt Arcane als
  ``failed`` (ProjectRunOutcome).
- ``POST agents/{id}/jobs/{jobId}?action=run`` stellt den Lauf ein und antwortet
  mit 202 (handler.go runJob); Recht ``jobs:run``. Arcane lehnt Jobs ab, die
  nicht von Hand laufen duerfen, ausgeschaltet sind oder deren Voraussetzungen
  fehlen (service.go validateLocalJob); nur vom Manager ausgefuehrte Jobs nie in
  einer entfernten Umgebung (service.go Submit).
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arcane.api import (
    ArcanePermissionError,
    ArcaneResponseError,
)
from custom_components.arcane.const import (
    CONF_ALLOW_CONTROL,
    CONF_JOBS,
    DEFAULT_OPTIONS,
    JOB_RUN_STATUSES,
)
from custom_components.arcane.models import ArcaneJob
from custom_components.arcane.permissions import required_permissions

SENSOR = "sensor.local_job_auto_update"
BUTTON = "button.local_run_job_auto_update"
WATCHER_SENSOR = "sensor.local_job_image_update_watcher"
WATCHER_BUTTON = "button.local_run_job_image_update_watcher"

AUTO_UPDATE: dict[str, Any] = {
    "id": "auto-update",
    "name": "Auto Update",
    "description": "Automatically update containers",
    "category": "updates",
    "schedule": "0 * * * *",
    "settingsKey": "autoUpdateInterval",
    "prerequisites": [],
    "enabled": True,
    "managerOnly": False,
    "isContinuous": False,
    "canRunManually": True,
    "nextRun": "2026-11-16T10:00:00.000000000+01:00",
    "lastSuccess": "2026-11-16T08:00:04.512Z",
    "lastRun": {
        "id": "9b3f6c2e-0000-4000-8000-000000000001",
        "jobId": "auto-update",
        "environmentId": "0",
        "trigger": "schedule",
        "status": "succeeded",
        "createdAt": "2026-11-16T08:59:59.9Z",
        "updatedAt": "2026-11-16T09:00:03Z",
        "startedAt": "2026-11-16T09:00:00.123456789Z",
        "finishedAt": "2026-11-16T09:00:03Z",
        "attemptCount": 1,
        "remoteDeliveryAttempted": False,
        "remoteAccepted": False,
        "remoteSettled": False,
        "outcome": {"status": "succeeded", "message": "No updates to apply"},
    },
}

WATCHER: dict[str, Any] = {
    "id": "image-polling",
    "name": "Image Update Watcher",
    "description": "Watch registries for image updates",
    "category": "updates",
    "schedule": "*/30 * * * *",
    "prerequisites": [],
    "enabled": True,
    "managerOnly": False,
    "isContinuous": True,
    "canRunManually": True,
    "workerHealth": {
        "status": "needs_attention",
        "lastError": "registry unreachable",
        "updatedAt": "2026-11-16T09:10:00Z",
    },
}


def _job(base: dict[str, Any] = AUTO_UPDATE, **fields: Any) -> dict[str, Any]:
    job = copy.deepcopy(base)
    job.update(fields)
    return job


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, options: dict | None = None
) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_JOBS: True, **(options or {})}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def _press(hass: HomeAssistant, entity_id: str = BUTTON) -> None:
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


# --- Abfrage ------------------------------------------------------------------


async def test_jobs_off_by_default(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_jobs.return_value = [_job()]
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_client.async_get_jobs.assert_not_awaited()
    assert hass.states.get(SENSOR) is None
    assert hass.states.get(BUTTON) is None


async def test_job_sensor_reports_the_last_run(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_jobs.return_value = [_job()]
    await _setup(hass, mock_config_entry)

    mock_client.async_get_jobs.assert_awaited_with("0")
    state = hass.states.get(SENSOR)
    assert state.state == "succeeded"
    attributes = state.attributes
    assert attributes["options"] == JOB_RUN_STATUSES
    assert attributes["job_id"] == "auto-update"
    assert attributes["category"] == "updates"
    assert attributes["enabled"] is True
    assert attributes["schedule"] == "0 * * * *"
    assert attributes["next_run"] == datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
    # Gestartet zaehlt vor angelegt; Nanosekunden von Go werden gekuerzt.
    assert attributes["last_run"] == datetime(2026, 11, 16, 9, 0, 0, 123456, tzinfo=UTC)
    assert attributes["last_finished"] == datetime(2026, 11, 16, 9, 0, 3, tzinfo=UTC)
    assert attributes["last_success"] == datetime(
        2026, 11, 16, 8, 0, 4, 512000, tzinfo=UTC
    )
    assert attributes["trigger"] == "schedule"
    assert attributes["message"] == "No updates to apply"
    assert attributes["last_error"] is None
    assert attributes["running"] is False


async def test_failed_run_shows_its_error(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    job = _job(lastError="pull failed: registry unreachable")
    job["lastRun"]["status"] = "failed"
    job["lastRun"]["outcome"] = {
        "status": "failed",
        "message": "pull failed: registry unreachable",
    }
    mock_client.async_get_jobs.return_value = [job]
    await _setup(hass, mock_config_entry)

    state = hass.states.get(SENSOR)
    assert state.state == "failed"
    assert state.attributes["last_error"] == "pull failed: registry unreachable"
    assert state.attributes["message"] == "pull failed: registry unreachable"


async def test_run_under_way_keeps_the_last_outcome(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    job = _job(currentRun={"id": "r2", "status": "running", "trigger": "manual"})
    mock_client.async_get_jobs.return_value = [job]
    await _setup(hass, mock_config_entry)

    state = hass.states.get(SENSOR)
    assert state.state == "succeeded"
    assert state.attributes["running"] is True


async def test_job_that_never_ran_is_unknown(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    job = _job()
    del job["lastRun"], job["lastSuccess"]
    mock_client.async_get_jobs.return_value = [job]
    await _setup(hass, mock_config_entry)

    state = hass.states.get(SENSOR)
    assert state.state == STATE_UNKNOWN
    assert state.attributes["last_run"] is None
    assert state.attributes["next_run"] is not None


async def test_status_outside_the_options_is_not_a_state(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Ein Enum-Sensor darf nur Werte aus options melden."""
    job = _job()
    job["lastRun"]["status"] = "needs_attention"
    mock_client.async_get_jobs.return_value = [job]
    await _setup(hass, mock_config_entry)

    assert hass.states.get(SENSOR).state == STATE_UNKNOWN


async def test_worker_health_of_a_continuous_job(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_jobs.return_value = [_job(), _job(WATCHER)]
    await _setup(hass, mock_config_entry)

    state = hass.states.get(WATCHER_SENSOR)
    assert state.attributes["worker_status"] == "needs_attention"
    assert state.attributes["worker_error"] == "registry unreachable"


async def test_child_jobs_get_no_entities(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    parent = _job(
        id="gitops-sync",
        name="gitops-sync",
        category="sync",
        canRunManually=False,
        children=[_job(id="gitops-sync:abc", name="gitops-sync:abc")],
    )
    del parent["lastRun"]
    mock_client.async_get_jobs.return_value = [parent]
    await _setup(hass, mock_config_entry)

    jobs = mock_config_entry.runtime_data.data.environments["0"].jobs
    assert set(jobs) == {"gitops-sync"}
    assert hass.states.get("sensor.local_job_gitops_sync") is not None
    assert hass.states.get("sensor.local_job_gitops_sync_abc") is None


async def test_jobs_come_and_go(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_jobs.return_value = [_job()]
    await _setup(hass, mock_config_entry)
    assert hass.states.get(WATCHER_SENSOR) is None

    mock_client.async_get_jobs.return_value = [_job(WATCHER)]
    await _refresh(hass, mock_config_entry)

    assert hass.states.get(WATCHER_SENSOR) is not None
    assert hass.states.get(SENSOR).state == STATE_UNAVAILABLE


async def test_refused_job_list_names_the_permission(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_jobs.side_effect = ArcanePermissionError("nope")
    await _setup(hass, mock_config_entry)

    coordinator = mock_config_entry.runtime_data
    assert "jobs:list" in coordinator.missing_permissions
    # Der Rest der Umgebung laeuft weiter.
    assert coordinator.data.environments["0"].containers
    assert coordinator.data.environments["0"].jobs == {}


# --- Button "Job ausfuehren" ----------------------------------------------------


async def test_run_button_queues_a_run_and_refreshes(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_jobs.return_value = [_job()]
    mock_client.async_run_job.return_value = {
        "success": True,
        "message": "Job accepted",
        "status": "queued",
    }
    await _setup(hass, mock_config_entry)
    calls_before = mock_client.async_get_jobs.await_count

    await _press(hass)
    await hass.async_block_till_done()

    mock_client.async_run_job.assert_awaited_once_with("0", "auto-update")
    assert mock_client.async_get_jobs.await_count > calls_before


async def test_button_only_for_jobs_arcane_runs_by_hand(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_jobs.return_value = [
        _job(),
        _job(WATCHER, canRunManually=False),
    ]
    await _setup(hass, mock_config_entry)

    assert hass.states.get(BUTTON) is not None
    assert hass.states.get(WATCHER_SENSOR) is not None
    assert hass.states.get(WATCHER_BUTTON) is None


async def test_manager_only_job_has_no_button_in_a_remote_environment(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_environments.return_value = [
        {"id": "0", "name": "Local", "status": "online", "enabled": True},
        {"id": "2", "name": "Remote", "status": "online", "enabled": True},
    ]
    mock_client.async_get_jobs.return_value = [
        _job(id="environment-health", name="Environment Health", managerOnly=True)
    ]
    await _setup(hass, mock_config_entry)

    assert hass.states.get("button.local_run_job_environment_health") is not None
    # 0.11.0: Jobs, die nur die Hauptinstanz ausfuehrt, fehlen in entfernten
    # Umgebungen ganz; dort laufen sie nie.
    assert hass.states.get("sensor.remote_job_environment_health") is None
    assert hass.states.get("button.remote_run_job_environment_health") is None


@pytest.mark.parametrize(
    "fields",
    [
        {"enabled": False},
        {
            "prerequisites": [
                {"settingKey": "trivyEnabled", "label": "Scanner", "isMet": False}
            ]
        },
    ],
    ids=["ausgeschaltet", "voraussetzung-fehlt"],
)
async def test_button_unavailable_when_arcane_would_refuse(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    fields: dict[str, Any],
) -> None:
    mock_client.async_get_jobs.return_value = [_job(**fields)]
    await _setup(hass, mock_config_entry)

    assert hass.states.get(BUTTON).state == STATE_UNAVAILABLE
    # Der Sensor zeigt den Job trotzdem.
    assert hass.states.get(SENSOR).state == "succeeded"


async def test_refused_run_names_the_permission(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_jobs.return_value = [_job()]
    mock_client.async_run_job.side_effect = ArcanePermissionError("nope")
    await _setup(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await _press(hass)
    assert err.value.translation_key == "run_job_forbidden"
    assert err.value.translation_placeholders == {
        "name": "Auto Update",
        "permission": "jobs:run",
    }
    assert "jobs:run" in mock_config_entry.runtime_data.missing_permissions


async def test_rejected_run_quotes_arcane(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_jobs.return_value = [_job()]
    mock_client.async_run_job.side_effect = ArcaneResponseError(
        "Arcane returned HTTP 400: job is disabled"
    )
    await _setup(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await _press(hass)
    assert err.value.translation_key == "run_job_failed"
    assert "job is disabled" in err.value.translation_placeholders["error"]


async def test_run_button_needs_control(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_jobs.return_value = [_job()]
    await _setup(hass, mock_config_entry, {CONF_ALLOW_CONTROL: False})

    assert hass.states.get(BUTTON) is None
    assert hass.states.get(SENSOR) is not None


# --- Optionen, Rechte, Modell, Client ------------------------------------------


def test_permissions_of_the_option() -> None:
    needed = required_permissions({CONF_JOBS: True})
    assert "jobs:list" in needed.monitoring
    assert "jobs:run" in needed.control
    read_only = required_permissions({CONF_JOBS: True, CONF_ALLOW_CONTROL: False})
    assert "jobs:run" not in read_only.all
    assert "jobs:list" not in required_permissions({}).all


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
    assert keys[CONF_JOBS].description["suggested_value"] is False
    values = {name: DEFAULT_OPTIONS[name] for name in keys} | {CONF_JOBS: True}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], values
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_JOBS] is True


def test_model_reads_prerequisites_and_missing_fields() -> None:
    job = ArcaneJob.from_api(
        _job(
            prerequisites=[
                {"settingKey": "a", "label": "A", "isMet": True},
                {"settingKey": "b", "label": "B", "isMet": False},
            ]
        )
    )
    assert job.prerequisites_met is False
    assert ArcaneJob.from_api(_job()).prerequisites_met is True

    bare = ArcaneJob.from_api({"id": "event-cleanup"})
    assert bare.name == "event-cleanup"
    assert bare.enabled is False
    assert bare.can_run_manually is False
    assert bare.last_status is None
    assert bare.running is False
    # Ohne startedAt zaehlt der Zeitpunkt, zu dem der Lauf angelegt wurde.
    queued_only = _job()
    del queued_only["lastRun"]["startedAt"]
    assert ArcaneJob.from_api(queued_only).last_started == datetime(
        2026, 11, 16, 8, 59, 59, 900000, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "path",
    ["strings.json", "translations/en.json", "translations/de.json"],
)
def test_states_and_attributes_translated(path: str) -> None:
    base = Path(__file__).parent.parent / "custom_components" / "arcane"
    sensor = json.loads((base / path).read_text(encoding="utf-8"))["entity"]["sensor"][
        "job"
    ]
    assert set(sensor["state"]) == set(JOB_RUN_STATUSES)
    attributes = ArcaneJob.from_api(_job())
    from custom_components.arcane.sensor import ArcaneJobSensor

    keys = set(ArcaneJobSensor.extra_state_attributes.fget(_Fake(attributes)))
    assert keys == set(sensor["state_attributes"])


class _Fake:
    """Steht fuer den Sensor, damit die Attributnamen ohne hass lesbar sind."""

    def __init__(self, job: ArcaneJob) -> None:
        self.job = job


async def test_client_reads_the_list_without_envelope(
    aiohttp_client, socket_enabled
) -> None:
    """Gegen einen HTTP-Stub: Liste ohne Huelle, Lauf mit action=run ohne Body."""
    from aiohttp import web

    from custom_components.arcane.api import ArcaneClient

    seen: list[tuple[str, str, dict, bytes]] = []
    envelope = False

    async def record(request: web.Request) -> web.Response:
        seen.append(
            (
                request.method,
                request.raw_path.split("?")[0],
                dict(request.query),
                await request.read(),
            )
        )
        if request.method == "POST":
            return web.json_response(
                {"success": True, "message": "Job accepted", "status": "queued"},
                status=202,
            )
        if envelope:
            return web.json_response({"success": True, "data": []})
        return web.json_response(
            {
                "observedAt": "2026-11-16T09:00:00Z",
                "jobs": [AUTO_UPDATE, "kein Job"],
                "durableRuns": True,
                "isAgent": False,
            }
        )

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", record)
    http = await aiohttp_client(app)
    client = ArcaneClient(http.session, str(http.make_url("")), "arc_test")

    assert await client.async_get_jobs("0") == [AUTO_UPDATE]
    assert (await client.async_run_job("0", "gitops-sync:a/b"))["status"] == "queued"
    envelope = True
    with pytest.raises(ArcaneResponseError):
        await client.async_get_jobs("0")

    assert seen[:2] == [
        ("GET", "/api/agents/0/jobs", {}, b""),
        ("POST", "/api/agents/0/jobs/gitops-sync%3Aa%2Fb", {"action": "run"}, b""),
    ]
