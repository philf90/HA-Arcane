"""Tests fuer 0.8.0: sofort aktualisieren (events/changes), Arcane-Ereignisse.

Arcane beantwortet ``GET agents/{id}/events/changes`` sofort, wenn die Epoche
abweicht oder eine Art eine hoehere Revision als ``since`` hat, und haelt die
Anfrage sonst bis zu 55 Sekunden (backend/internal/event/service.go,
WaitForChanges, Arcane v3.0.1). Das Ereignisprotokoll liefert
``GET events?sort=timestamp&order=desc``, neueste zuerst; Arcane schreibt dort
auch Ereignisse des Docker-Daemons hinein (RecordDockerEvent). Anmeldungen,
Abmeldungen und Arcane-Upgrades legt Arcane ohne Umgebung ab (LogUserEvent);
``events/agent/{id}`` liefert nur Eintraege genau dieser Umgebung und damit
diese nie (bis 0.8.0 so gelesen, live gefunden).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arcane.api import (
    ArcaneConnectionError,
    ArcaneNotFoundError,
    ArcanePermissionError,
)
from custom_components.arcane.const import (
    CONF_ARCANE_EVENTS,
    CONF_INSTANT_UPDATES,
    DOMAIN,
    EVENT_ARCANE,
)
from custom_components.arcane.diagnostics import async_get_config_entry_diagnostics


class Arcane:
    """Antworten von events/changes, eine nach der anderen, wie vorgegeben."""

    def __init__(self) -> None:
        self.replies: asyncio.Queue[Any] = asyncio.Queue()
        self.calls: list[dict[str, Any]] = []

    async def wait_for_changes(self, environment_id: str, **kwargs: Any) -> Any:
        self.calls.append({"environment_id": environment_id, **kwargs})
        reply = await self.replies.get()
        if isinstance(reply, Exception):
            raise reply
        return reply


def _recorder(hass: HomeAssistant) -> list[Event]:
    """Gefeuerte arcane_event in der Reihenfolge, in der sie gefeuert wurden."""
    fired: list[Event] = []

    @callback
    def record(event: Event) -> None:
        fired.append(event)

    hass.bus.async_listen(EVENT_ARCANE, record)
    return fired


def _changes(epoch: str, **kinds: int) -> dict[str, Any]:
    return {"environmentId": "0", "epoch": epoch, "kinds": kinds}


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, options: dict | None = None
) -> None:
    entry.add_to_hass(hass)
    if options:
        hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _settle(hass: HomeAssistant) -> None:
    """Die Hintergrundaufgabe und eine angestossene Aktualisierung laufen lassen."""
    for _ in range(5):
        await asyncio.sleep(0)
    await hass.async_block_till_done()


@pytest.fixture
def arcane(mock_client: AsyncMock) -> Arcane:
    fake = Arcane()
    mock_client.async_wait_for_changes.side_effect = fake.wait_for_changes
    return fake


@pytest.fixture(autouse=True)
def pauses():
    """Pausen der Ueberwachung aufzeichnen, ohne wirklich zu warten."""
    recorded: list[float] = []

    async def pause(seconds: float) -> None:
        recorded.append(seconds)
        await asyncio.sleep(0)

    with patch("custom_components.arcane.changes._pause", pause):
        yield recorded


@pytest.fixture(autouse=True)
def no_refresh_cooldown():
    """Ohne die 10 s Abklingzeit des Coordinators zaehlt jede Aktualisierung."""
    with patch(
        "homeassistant.helpers.update_coordinator.REQUEST_REFRESH_DEFAULT_COOLDOWN", 0
    ):
        yield


async def test_instant_updates_off_by_default(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)

    mock_client.async_wait_for_changes.assert_not_awaited()
    mock_client.async_get_events.assert_not_awaited()


async def test_change_triggers_a_refresh(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    arcane: Arcane,
) -> None:
    await _setup(hass, mock_config_entry, {CONF_INSTANT_UPDATES: True})
    refreshes = mock_client.async_get_containers.await_count

    # Die erste Antwort sagt nur, wo Arcane steht: keine Aktualisierung.
    await arcane.replies.put(_changes("e1", container=7, project=3))
    await _settle(hass)
    assert mock_client.async_get_containers.await_count == refreshes
    assert arcane.calls[0] == {
        "environment_id": "0",
        "epoch": None,
        "since": 0,
        "wait": 50,
    }
    assert arcane.calls[1]["epoch"] == "e1"
    assert arcane.calls[1]["since"] == 7

    # Eine neue Revision stoesst sofort eine Aktualisierung an.
    await arcane.replies.put(_changes("e1", container=9, project=3))
    await _settle(hass)
    assert mock_client.async_get_containers.await_count == refreshes + 1
    assert arcane.calls[2]["since"] == 9

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_activity_progress_alone_does_not_refresh(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    arcane: Arcane,
) -> None:
    await _setup(hass, mock_config_entry, {CONF_INSTANT_UPDATES: True})
    refreshes = mock_client.async_get_containers.await_count

    await arcane.replies.put(_changes("e1", container=7))
    await arcane.replies.put(_changes("e1", container=7, activity=8))
    await _settle(hass)

    assert mock_client.async_get_containers.await_count == refreshes
    # Trotzdem wartet die naechste Anfrage ab der neuen Revision.
    assert arcane.calls[-1]["since"] == 8

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_restart_of_arcane_triggers_a_refresh(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    arcane: Arcane,
) -> None:
    await _setup(hass, mock_config_entry, {CONF_INSTANT_UPDATES: True})
    refreshes = mock_client.async_get_containers.await_count

    await arcane.replies.put(_changes("e1", container=7))
    # Nach einem Neustart beginnen die Revisionen von vorn.
    await arcane.replies.put(_changes("e2", container=1))
    await _settle(hass)

    assert mock_client.async_get_containers.await_count == refreshes + 1
    assert arcane.calls[-1]["epoch"] == "e2"
    assert arcane.calls[-1]["since"] == 1

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_refused_watch_stops_and_names_the_permission(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    arcane: Arcane,
) -> None:
    await _setup(hass, mock_config_entry, {CONF_INSTANT_UPDATES: True})

    await arcane.replies.put(ArcanePermissionError("nope"))
    await _settle(hass)

    assert len(arcane.calls) == 1
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"missing_permissions_{mock_config_entry.entry_id}"
    )
    assert issue is not None
    assert "`events:list`" in issue.translation_placeholders["permissions"]

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_missing_endpoint_stops_the_watch(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    arcane: Arcane,
) -> None:
    await _setup(hass, mock_config_entry, {CONF_INSTANT_UPDATES: True})

    await arcane.replies.put(ArcaneNotFoundError("404"))
    await _settle(hass)

    assert len(arcane.calls) == 1
    state = mock_config_entry.runtime_data.change_watcher.states["0"]
    assert state.stopped is True

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_failed_watch_retries_with_growing_pause(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    arcane: Arcane,
    pauses: list[float],
) -> None:
    await _setup(hass, mock_config_entry, {CONF_INSTANT_UPDATES: True})
    await arcane.replies.put(ArcaneConnectionError("weg"))
    await arcane.replies.put(ArcaneConnectionError("weg"))
    # Ohne Epoche kaeme jede Anfrage sofort zurueck: wie ein Fehler behandelt.
    await arcane.replies.put({"kinds": {"container": 1}})
    await arcane.replies.put(_changes("e1", container=7))
    await _settle(hass)

    assert pauses[:3] == [5, 10, 20]
    assert len(arcane.calls) == 5
    assert mock_config_entry.runtime_data.change_watcher.states["0"].error is None

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_quick_reply_without_news_does_not_spin(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    arcane: Arcane,
    pauses: list[float],
) -> None:
    await _setup(hass, mock_config_entry, {CONF_INSTANT_UPDATES: True})
    await arcane.replies.put(_changes("e1", container=7))
    await arcane.replies.put(_changes("e1", container=7))
    await _settle(hass)

    # Beide Antworten kamen sofort und ohne Neues: jeweils eine Pause.
    assert pauses == [1.0, 1.0]

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_unload_stops_the_watch(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    arcane: Arcane,
) -> None:
    await _setup(hass, mock_config_entry, {CONF_INSTANT_UPDATES: True})
    await _settle(hass)
    assert len(arcane.calls) == 1

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await arcane.replies.put(_changes("e1", container=7))
    await _settle(hass)

    # Die offene Anfrage wurde abgebrochen, keine neue gestellt.
    assert len(arcane.calls) == 1


async def test_watch_follows_the_environments(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    arcane: Arcane,
) -> None:
    await _setup(hass, mock_config_entry, {CONF_INSTANT_UPDATES: True})
    watcher = mock_config_entry.runtime_data.change_watcher
    assert set(watcher.states) == {"0"}

    mock_client.async_get_environments.return_value = [
        {"id": "0", "name": "Local", "status": "online", "enabled": True},
        {"id": "3", "name": "Remote", "status": "online", "enabled": True},
    ]
    await mock_config_entry.runtime_data.async_refresh()
    await _settle(hass)
    assert set(watcher.states) == {"0", "3"}
    assert {call["environment_id"] for call in arcane.calls} == {"0", "3"}

    mock_client.async_get_environments.return_value = [
        {"id": "3", "name": "Remote", "status": "online", "enabled": True},
    ]
    await mock_config_entry.runtime_data.async_refresh()
    await _settle(hass)
    assert set(watcher.states) == {"3"}

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_diagnostics_show_the_watch(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    arcane: Arcane,
) -> None:
    await _setup(hass, mock_config_entry, {CONF_INSTANT_UPDATES: True})
    await arcane.replies.put(_changes("e1", container=7))
    await arcane.replies.put(_changes("e1", container=8))
    await _settle(hass)

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    watch = diagnostics["instant_updates"]["0"]
    assert watch["epoch"] == "e1"
    assert watch["since"] == 8
    assert watch["refresh_requests"] == 1
    assert watch["last_kinds"] == ["container"]
    assert watch["last_change"] is not None

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


def _event(event_id: str, timestamp: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": event_id,
        "timestamp": timestamp,
        "type": "container.oom",
        "severity": "error",
        "title": "Docker container oom: webapp",
        "description": "container 'webapp': oom observed from the Docker daemon",
        "resourceType": "container",
        "resourceId": "c0ffee",
        "resourceName": "webapp",
        "environmentId": "0",
        "metadata": {"source": "docker", "action": "oom", "composeProject": "webapp"},
        **extra,
    }


OLD = _event("e-old", "2026-11-15T17:00:00.123456789Z", type="container.start")


async def test_arcane_events_off_by_default(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)

    mock_client.async_get_events.assert_not_awaited()


async def test_new_arcane_events_are_fired_once_and_in_order(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    fired = _recorder(hass)
    mock_client.async_get_events.return_value = [OLD]
    await _setup(hass, mock_config_entry, {CONF_ARCANE_EVENTS: True})

    mock_client.async_get_events.assert_awaited_with(limit=100)
    # Der erste Abruf markiert nur, was schon da ist.
    assert fired == []

    second = _event("e-2", "2026-11-15T17:05:00Z", type="container.die")
    third = _event("e-3", "2026-11-15T17:06:00Z", username="admin")
    mock_client.async_get_events.return_value = [third, second, OLD]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert [event.data["event_id"] for event in fired] == ["e-2", "e-3"]
    data = fired[1].data
    assert data == {
        "environment_id": "0",
        "environment": "Local",
        "event_id": "e-3",
        "type": "container.oom",
        "severity": "error",
        "title": "Docker container oom: webapp",
        "description": "container 'webapp': oom observed from the Docker daemon",
        "resource_type": "container",
        "resource_id": "c0ffee",
        "resource_name": "webapp",
        "username": "admin",
        "timestamp": "2026-11-15T17:06:00+00:00",
        "metadata": {
            "source": "docker",
            "action": "oom",
            "composeProject": "webapp",
        },
    }

    # Derselbe Stand noch einmal: nichts Neues.
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert len(fired) == 2


async def test_late_old_events_are_not_announced(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    fired = _recorder(hass)
    newer = _event("e-new", "2026-11-15T18:00:00Z")
    mock_client.async_get_events.return_value = [newer]
    await _setup(hass, mock_config_entry, {CONF_ARCANE_EVENTS: True})

    # Ein Eintrag, der aelter ist als der neueste bekannte, ist kein neues
    # Ereignis, etwa nach dem Loeschen eines neueren in Arcane.
    mock_client.async_get_events.return_value = [newer, OLD]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert fired == []


async def test_first_events_after_an_empty_log_are_announced(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    fired = _recorder(hass)
    mock_client.async_get_events.return_value = []
    await _setup(hass, mock_config_entry, {CONF_ARCANE_EVENTS: True})

    mock_client.async_get_events.return_value = [OLD]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert [event.data["event_id"] for event in fired] == ["e-old"]


async def test_malformed_entries_are_skipped(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    fired = _recorder(hass)
    mock_client.async_get_events.return_value = []
    await _setup(hass, mock_config_entry, {CONF_ARCANE_EVENTS: True})

    mock_client.async_get_events.return_value = [
        "kein Objekt",
        {"id": "ohne-zeit"},
        {"timestamp": "2026-11-15T17:00:00Z"},
        _event("e-ok", "2026-11-15T17:00:00Z", metadata="kein Objekt"),
    ]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert [event.data["event_id"] for event in fired] == ["e-ok"]
    assert fired[0].data["metadata"] == {}


async def test_failed_read_announces_nothing_and_loses_nothing(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    fired = _recorder(hass)
    mock_client.async_get_events.return_value = [OLD]
    await _setup(hass, mock_config_entry, {CONF_ARCANE_EVENTS: True})

    mock_client.async_get_events.side_effect = ArcaneConnectionError("weg")
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert fired == []

    mock_client.async_get_events.side_effect = None
    mock_client.async_get_events.return_value = [
        _event("e-2", "2026-11-15T17:05:00Z"),
        OLD,
    ]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert [event.data["event_id"] for event in fired] == ["e-2"]


async def test_refused_event_log_names_the_permission(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_events.side_effect = ArcanePermissionError("nope")
    await _setup(hass, mock_config_entry, {CONF_ARCANE_EVENTS: True})

    assert "events:list" in mock_config_entry.runtime_data.missing_permissions


async def test_change_brings_the_new_event_right_away(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    arcane: Arcane,
) -> None:
    """Zusammenspiel: Arcane meldet eine Aenderung, das Ereignis folgt sofort."""
    fired = _recorder(hass)
    mock_client.async_get_events.return_value = [OLD]
    await _setup(
        hass, mock_config_entry, {CONF_INSTANT_UPDATES: True, CONF_ARCANE_EVENTS: True}
    )
    await arcane.replies.put(_changes("e1", container=7))
    await _settle(hass)

    mock_client.async_get_events.return_value = [
        _event("e-oom", "2026-11-15T17:10:00Z"),
        OLD,
    ]
    await arcane.replies.put(_changes("e1", container=8))
    await _settle(hass)

    assert [event.data["event_id"] for event in fired] == ["e-oom"]

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


LOGIN = {
    "id": "e-login",
    "timestamp": "2026-11-15T20:09:54.95Z",
    "type": "user.login",
    "severity": "info",
    "title": "User logged in: admin",
    "description": "User 'admin' has logged in",
    "userId": "u1",
    "username": "admin",
    "metadata": {"method": "password"},
}


async def test_entries_without_environment_are_announced(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    fired = _recorder(hass)
    mock_client.async_get_events.return_value = [OLD]
    await _setup(hass, mock_config_entry, {CONF_ARCANE_EVENTS: True})

    mock_client.async_get_events.return_value = [LOGIN, OLD]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert len(fired) == 1
    assert fired[0].data["type"] == "user.login"
    assert fired[0].data["environment_id"] is None
    assert fired[0].data["environment"] is None
    assert fired[0].data["username"] == "admin"


async def test_entries_of_unfollowed_environments_are_skipped(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    fired = _recorder(hass)
    mock_client.async_get_events.return_value = [OLD]
    await _setup(hass, mock_config_entry, {CONF_ARCANE_EVENTS: True})

    elsewhere = _event("e-remote", "2026-11-15T17:07:00Z", environmentId="9")
    mock_client.async_get_events.return_value = [elsewhere, OLD]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert fired == []

    # Auch spaeter nicht, wenn die Umgebung dazukommt: der Eintrag ist alt.
    mock_client.async_get_environments.return_value = [
        {"id": "0", "name": "Local", "status": "online", "enabled": True},
        {"id": "9", "name": "Remote", "status": "online", "enabled": True},
    ]
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert fired == []


async def test_event_log_is_read_once_per_refresh(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_environments.return_value = [
        {"id": "0", "name": "Local", "status": "online", "enabled": True},
        {"id": "3", "name": "Remote", "status": "online", "enabled": True},
    ]
    await _setup(hass, mock_config_entry, {CONF_ARCANE_EVENTS: True})

    # Zwei Umgebungen, ein Abruf.
    assert mock_client.async_get_events.await_count == 1


async def test_diagnostics_show_the_event_log(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_events.return_value = [LOGIN, OLD]
    await _setup(hass, mock_config_entry, {CONF_ARCANE_EVENTS: True})

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diagnostics["arcane_events"] == {
        "read": True,
        "seen": 2,
        "newest": "2026-11-15T20:09:54.950000+00:00",
    }


async def test_client_reads_the_log_that_holds_logins(
    aiohttp_client, socket_enabled
) -> None:
    """Gegen einen Stub, der wie Arcane nach Umgebung filtert (handler.go:115)."""
    from aiohttp import web

    from custom_components.arcane.api import ArcaneClient

    log = [LOGIN, OLD]

    async def all_events(request: web.Request) -> web.Response:
        return web.json_response({"success": True, "data": log})

    async def environment_events(request: web.Request) -> web.Response:
        wanted = request.match_info["environment_id"]
        data = [item for item in log if item.get("environmentId") == wanted]
        return web.json_response({"success": True, "data": data})

    app = web.Application()
    app.router.add_get("/api/events", all_events)
    app.router.add_get("/api/events/agent/{environment_id}", environment_events)
    http = await aiohttp_client(app)
    client = ArcaneClient(http.session, str(http.make_url("")), "arc_test")

    events = await client.async_get_events(limit=100)

    assert [item["id"] for item in events] == ["e-login", "e-old"]
