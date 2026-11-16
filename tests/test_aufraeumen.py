"""Tests fuer 0.10.1: der Prune-Button wartet, bis Arcane das Aufraeumen beendet hat.

Belege im Quelltext von Arcane v3.0.1 (Commit c614e0d2):
- ``POST agents/{id}/system/prune`` startet das Aufraeumen als Hintergrund-
  Aktivitaet und antwortet sofort mit ``activityId`` (backend/internal/system/
  children/prune/handler.go PruneAll, service.go StartPruneAll: Goroutine mit
  runSystemPrune).
- ``GET agents/{id}/activities/{activityId}`` liefert ``{"activity": {...},
  "messages": [...]}`` in der ueblichen Huelle; Recht ``activities:read``
  (backend/internal/activity/module.go, handler.go GetActivity).
- Endzustaende ``success``, ``failed``, ``cancelled``; ``failed`` heisst
  "System prune completed with errors" mit den Fehlern in ``error``
  (prune/service.go completeSystemPruneActivity, types/activity.go).

Live am 16.11.2026 gefunden: Der Speicherbericht nach dem Prune begann 2 ms
nach dem Druck und zeigte den Stand davor; Images 31 -> 26 erst in der
naechsten Abfrage.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arcane.api import (
    ArcaneConnectionError,
    ArcanePermissionError,
    ArcaneResponseError,
)
from custom_components.arcane.const import (
    ACTIVITY_POLL_INTERVAL,
    CONF_DISK_USAGE,
    CONF_PRUNE_BUTTON,
    PRUNE_WAIT,
)
from custom_components.arcane.permissions import required_permissions

PRUNE_BUTTON = "button.local_prune_unused"
IMAGE_SPACE = "sensor.local_reclaimable_image_space"
BEFORE = {
    "unusedImagesBytes": 1_189_683_086,
    "stoppedContainersBytes": 0,
    "unusedVolumesBytes": 13_000_000,
    "buildCacheBytes": 0,
}
AFTER = {**BEFORE, "unusedImagesBytes": 0}


@pytest.fixture
def log(mock_client: AsyncMock) -> list[str]:
    """Reihenfolge der Aufrufe, die fuer das Warten zaehlen."""
    calls: list[str] = []
    state = {"pruned": False}

    async def prune(environment_id: str, *, unused_images: bool) -> dict:
        calls.append("prune")
        return {"activityId": "act-1", "success": True}

    async def report(environment_id: str) -> dict:
        calls.append("report")
        return AFTER if state["pruned"] else BEFORE

    async def containers(*args: Any, **kwargs: Any) -> list:
        calls.append("containers")
        return []

    mock_client.async_prune.side_effect = prune
    mock_client.async_get_disk_usage.side_effect = report
    mock_client.async_get_containers.side_effect = containers
    mock_client.pruned = state
    return calls


@pytest.fixture
def pauses() -> list[float]:
    """Ersetzt die Wartezeit zwischen zwei Blicken auf die Aktivitaet."""
    waited: list[float] = []

    async def pause(seconds: float) -> None:
        waited.append(seconds)
        # Ein Endzustand, der nicht als Ende erkannt wird, liefe sonst bis
        # PRUNE_WAIT weiter, ohne echte Pause.
        if len(waited) > 20:
            raise AssertionError("wartet weiter, obwohl die Aktivitaet vorbei ist")

    with patch("custom_components.arcane.button._pause", pause):
        yield waited


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_DISK_USAGE: True, CONF_PRUNE_BUTTON: "unused"}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _press(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: PRUNE_BUTTON}, blocking=True
    )


def _activity(mock_client: AsyncMock, log: list[str], *statuses: dict) -> None:
    """Arcane meldet die Zustaende nacheinander; der letzte gilt danach weiter."""
    remaining = list(statuses)

    async def get(environment_id: str, activity_id: str) -> dict:
        assert activity_id == "act-1"
        log.append("activity")
        current = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if current.get("status") == "success":
            mock_client.pruned["pruned"] = True
        return current

    mock_client.async_get_activity.side_effect = get


async def test_report_and_counts_only_after_the_prune_is_over(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    log: list[str],
    pauses: list[float],
) -> None:
    _activity(
        mock_client,
        log,
        {"status": "queued"},
        {"status": "running"},
        {"status": "success"},
    )
    await _setup(hass, mock_config_entry)
    log.clear()

    await _press(hass)

    # Bericht und Zaehler erst nach dem Ende, und beides bevor der Druck
    # zurueckkehrt: ein Skript, das auf den Druck wartet, sieht das Ergebnis.
    assert log[:4] == ["prune", "activity", "activity", "activity"]
    assert set(log[4:]) == {"report", "containers"}
    assert pauses == [ACTIVITY_POLL_INTERVAL, ACTIVITY_POLL_INTERVAL]
    await hass.async_block_till_done()
    assert float(hass.states.get(IMAGE_SPACE).state) == 0.0


@pytest.mark.parametrize(
    ("status", "error", "shown"),
    [
        ("failed", "Image pruning failed: conflict", "Image pruning failed: conflict"),
        ("cancelled", None, "-"),
    ],
)
async def test_prune_not_completed_is_reported_after_the_refresh(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    log: list[str],
    pauses: list[float],
    status: str,
    error: str | None,
    shown: str,
) -> None:
    _activity(mock_client, log, {"status": status, "error": error})
    await _setup(hass, mock_config_entry)
    log.clear()

    with pytest.raises(HomeAssistantError) as err:
        await _press(hass)

    assert err.value.translation_key == "prune_not_completed"
    assert err.value.translation_placeholders == {
        "name": "Local",
        "status": status,
        "error": shown,
    }
    # Teilweise geloescht sein kann trotzdem: Bericht und Zaehler kommen vorher.
    assert "report" in log
    assert "containers" in log
    assert log.count("activity") == 1


async def test_counts_are_fresh_even_during_the_refresh_cooldown(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    log: list[str],
    pauses: list[float],
) -> None:
    """Kurz vorher angeforderte Aktualisierung (etwa durch eine Aenderung)."""
    _activity(mock_client, log, {"status": "success"})
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()
    log.clear()

    await _press(hass)

    # Ohne eigene Aktualisierung kaemen die Zaehler erst nach der Abklingzeit.
    assert log.index("containers") > log.index("activity")


async def test_answer_without_activity_keeps_the_old_way(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    log: list[str],
) -> None:
    async def prune(environment_id: str, *, unused_images: bool) -> dict:
        log.append("prune")
        return {"spaceReclaimed": 1024}

    mock_client.async_prune.side_effect = prune
    await _setup(hass, mock_config_entry)
    log.clear()

    await _press(hass)

    mock_client.async_get_activity.assert_not_awaited()
    assert log[0] == "prune"
    assert "report" in log


async def test_missing_permission_is_named_and_the_press_goes_on(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    log: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_client.async_get_activity.side_effect = ArcanePermissionError("nope")
    await _setup(hass, mock_config_entry)
    log.clear()

    await _press(hass)

    assert "activities:read" in mock_config_entry.runtime_data.missing_permissions
    assert "activities:read" in caplog.text
    assert "report" in log


@pytest.mark.parametrize(
    "error", [ArcaneConnectionError("weg"), ArcaneResponseError("kaputt")]
)
async def test_lost_activity_does_not_fail_the_press(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    log: list[str],
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    mock_client.async_get_activity.side_effect = error
    await _setup(hass, mock_config_entry)
    log.clear()

    await _press(hass)

    assert "Lost track of the prune of Local" in caplog.text
    assert "report" in log


async def test_waiting_ends_after_the_limit(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    log: list[str],
    pauses: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _activity(mock_client, log, {"status": "running"})
    await _setup(hass, mock_config_entry)
    log.clear()

    clock = iter([1000.0, 1000.0 + PRUNE_WAIT / 2, 1000.0 + PRUNE_WAIT])
    with patch(
        "custom_components.arcane.button.monotonic",
        side_effect=lambda: next(clock),
    ):
        await _press(hass)

    assert log.count("activity") == 2
    assert len(pauses) == 1
    assert f"still running after {PRUNE_WAIT} seconds" in caplog.text
    assert "report" in log


def test_prune_button_needs_activities_read() -> None:
    needed = required_permissions({CONF_PRUNE_BUTTON: "dangling"})
    assert {"system:prune", "activities:read"} <= set(needed.control)
    assert "activities:read" not in required_permissions({}).all


async def test_client_reads_the_activity(aiohttp_client, socket_enabled) -> None:
    """Gegen einen HTTP-Stub: Pfad, limit=1 und die Huelle wie bei Arcane."""
    from aiohttp import web

    from custom_components.arcane.api import ArcaneClient

    seen: list[tuple[str, dict]] = []
    body: dict = {
        "success": True,
        "data": {
            "activity": {"id": "act-1", "type": "system_prune", "status": "running"},
            "messages": [{"message": "Pruning images"}],
        },
    }

    async def record(request: web.Request) -> web.Response:
        seen.append((request.path, dict(request.query)))
        return web.json_response(body)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", record)
    http = await aiohttp_client(app)
    client = ArcaneClient(http.session, str(http.make_url("")), "arc_test")

    activity = await client.async_get_activity("0", "act-1")
    assert activity["status"] == "running"
    assert seen == [("/api/agents/0/activities/act-1", {"limit": "1"})]

    body["data"] = {"messages": []}
    with pytest.raises(ArcaneResponseError):
        await client.async_get_activity("0", "act-1")
