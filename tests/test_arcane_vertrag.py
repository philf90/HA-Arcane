"""Vertrag mit Arcane 3: der Client ruft nur Pfade auf, die Arcane anbietet.

Die Routen stammen aus dem Quelltext von Arcane (tests/fixtures/, erzeugt mit
.github/scripts/arcane_routen.py). Der Stub-Server antwortet wie Arcane 3.0.0:
unbekannte Pfade mit 404 "API endpoint not found", fehlende oder unbekannte
?action= mit 400. Die Mocks in conftest.py pruefen keine Pfade; ohne diesen
Test waren die Tests von 0.5.2 gegen Arcane 3 gruen, obwohl nichts lief.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from aiohttp import web

from custom_components.arcane.api import (
    CONTAINER_ACTIONS,
    PROJECT_ACTIONS,
    ArcaneClient,
    ArcaneNotFoundError,
    ArcaneUnsupportedVersionError,
)

FIXTURE = Path(__file__).parent / "fixtures" / "arcane_routen_v3.0.0.json"
ROUTEN = json.loads(FIXTURE.read_text(encoding="utf-8"))
STATS = {"cpuUsage": 3.5, "memoryUsage": 1, "memoryTotal": 2, "hostname": "h"}
VERSION = {"currentVersion": "3.0.0", "updateAvailable": False}


def _antwort(route: dict, request: web.Request) -> web.StreamResponse:
    """Antwort in der Form, die Arcane fuer diese Route schickt."""
    path = route["path"]
    if path == "/app/version":
        return web.json_response(VERSION)
    if path.endswith("/system/docker/info"):
        return web.json_response({"success": True, "ServerVersion": "28.0.0"})
    if path == "/agents/{id}/jobs":
        # Die Job-Liste kommt ohne Huelle {"success", "data"} (JobListResponse).
        return web.json_response(
            {"observedAt": "2026-11-16T09:00:00Z", "jobs": [], "durableRuns": True}
        )
    if path == "/agents/{id}/activities/{activityId}":
        return web.json_response(
            {
                "success": True,
                "data": {"activity": {"id": "a1", "status": "success"}, "messages": []},
            }
        )
    if path == "/agents/{id}/jobs/{jobId}":
        return web.json_response(
            {"success": True, "message": "Job accepted", "status": "queued"},
            status=202,
        )
    if path.endswith(("/counts", "/version")):
        return web.json_response({"success": True, "data": {}})
    if request.method == "GET":
        return web.json_response({"success": True, "data": []})
    return web.json_response({"success": True, "data": {"message": "ok"}})


async def _stream(request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse()
    response.content_type = "application/x-json-stream"
    await response.prepare(request)
    for line in ('{"type":"activity","activityId":"a1"}', '{"done":true}'):
        await response.write(line.encode() + b"\n")
    await response.write_eof()
    return response


async def _socket(request: web.Request) -> web.WebSocketResponse:
    socket = web.WebSocketResponse()
    await socket.prepare(request)
    await socket.send_json(STATS)
    return socket


@pytest.fixture
async def arcane3(aiohttp_client, socket_enabled):
    """Stub, der genau die Routen von Arcane 3.0.0 kennt."""
    app = web.Application()
    calls: list[tuple[str, str, str | None]] = []

    def handler(route: dict):
        async def handle(request: web.Request) -> web.StreamResponse:
            action = request.query.get("action")
            if "actions" in route and action not in route["actions"]:
                return web.json_response({"success": False, "error": "bad"}, status=400)
            calls.append((request.method, route["path"], action))
            if route.get("websocket"):
                return await _socket(request)
            if route["path"].endswith("/deploy"):
                return await _stream(request)
            return _antwort(route, request)

        return handle

    for route in ROUTEN["routen"]:
        if route["method"] in ("GET", "POST", "PUT"):
            app.router.add_route(
                route["method"], "/api" + route["path"], handler(route)
            )

    async def not_found(request: web.Request) -> web.Response:
        return web.json_response(
            {"success": False, "error": f"API endpoint not found: {request.path}"},
            status=404,
        )

    app.router.add_route("*", "/{tail:.*}", not_found)
    http = await aiohttp_client(app)
    client = ArcaneClient(http.session, str(http.make_url("")), "arc_test")
    return client, calls


async def _alle_aufrufe(client: ArcaneClient) -> set[str]:
    """Ruft jede oeffentliche Methode des Clients auf; liefert ihre Namen."""
    await client.async_get_version()
    await client.async_get_environments()
    await client.async_get_environment_version("1")
    await client.async_upgrade("0")
    await client.async_get_containers("0", include_internal=True, include_hidden=True)
    await client.async_get_projects("0")
    await client.async_get_image_counts("0")
    await client.async_get_volume_counts("0")
    await client.async_get_network_counts("0")
    await client.async_get_docker_info("0")
    for action in CONTAINER_ACTIONS:
        await client.async_container_action("0", "c1", action)
    for action in PROJECT_ACTIONS:
        await client.async_project_action("0", "p1", action)
    await client.async_prune("0", unused_images=True)
    await client.async_get_host_stats("0")
    await client.async_get_disk_usage("0")
    await client.async_get_release_notes(
        "0", "nginx:1.27", version="1.28", digest="sha256:" + "a" * 64
    )
    await client.async_check_image_updates("0")
    await client.async_wait_for_changes("0", epoch="e1", since=4, wait=1)
    await client.async_get_events(limit=100)
    await client.async_update_container("0", "c1")
    await client.async_set_auto_update("0", "c1", enabled=False)
    await client.async_run_updater("0")
    await client.async_get_updater_status("0")
    await client.async_get_updater_history("0", limit=1)
    await client.async_get_jobs("0")
    await client.async_run_job("0", "auto-update")
    await client.async_get_activity("0", "a1")
    return {
        "async_get_version",
        "async_get_environments",
        "async_get_environment_version",
        "async_upgrade",
        "async_get_containers",
        "async_get_projects",
        "async_get_image_counts",
        "async_get_volume_counts",
        "async_get_network_counts",
        "async_get_docker_info",
        "async_container_action",
        "async_project_action",
        "async_prune",
        "async_get_host_stats",
        "async_get_disk_usage",
        "async_get_release_notes",
        "async_check_image_updates",
        "async_wait_for_changes",
        "async_get_events",
        "async_update_container",
        "async_set_auto_update",
        "async_run_updater",
        "async_get_updater_status",
        "async_get_updater_history",
        "async_get_jobs",
        "async_run_job",
        "async_get_activity",
    }


def test_fixture_is_arcane_3() -> None:
    assert ROUTEN["arcane"] == "v3.0.0"
    assert ROUTEN["commit"] == "73b54f7837b9e55f25036b617df74a37b3b09151"
    assert len(ROUTEN["routen"]) > 300


async def test_every_call_hits_an_arcane_3_route(arcane3) -> None:
    """Kein Aufruf endet im 404 oder 400 des Stubs."""
    client, calls = arcane3
    called = await _alle_aufrufe(client)

    public = {
        name
        for name, member in inspect.getmembers(ArcaneClient)
        if name.startswith("async_") and inspect.iscoroutinefunction(member)
    }
    assert called == public, "neue Client-Methode ohne Vertragstest"
    assert ("POST", "/agents/{id}/containers/{containerId}", "start") in calls
    assert ("POST", "/agents/{id}/containers/{containerId}/redeploy", None) in calls
    assert ("POST", "/agents/{id}/projects/{projectId}/deploy", "up") in calls
    assert ("POST", "/agents/{id}/projects/{projectId}", "down") in calls
    assert ("GET", "/agents/{id}/ws/system/stats", None) in calls
    assert ("GET", "/agents/{id}/system/df", None) in calls
    assert ("GET", "/agents/{id}/images/updates/releasenotes", None) in calls
    assert ("POST", "/agents/{id}/images/updates/check", "all") in calls
    assert ("GET", "/agents/{id}/events/changes", None) in calls
    assert ("GET", "/events", None) in calls
    assert ("POST", "/agents/{id}/containers/{containerId}/update", None) in calls
    assert ("PUT", "/agents/{id}/containers/{containerId}/autoupdate", None) in calls
    assert ("POST", "/agents/{id}/updater/run", None) in calls
    assert ("GET", "/agents/{id}/updater/status", None) in calls
    assert ("GET", "/agents/{id}/updater/history", None) in calls
    assert ("GET", "/agents/{id}/jobs", None) in calls
    assert ("POST", "/agents/{id}/jobs/{jobId}", "run") in calls
    assert ("GET", "/agents/{id}/activities/{activityId}", None) in calls


async def test_actions_match_arcane(arcane3) -> None:
    """Jede Aktion des Clients steht in der Aktionsliste der Arcane-Route."""
    client, calls = arcane3
    await _alle_aufrufe(client)
    sent = {(path, action) for _method, path, action in calls if action}
    assert sent == {
        ("/agents/{id}/containers/{containerId}", "start"),
        ("/agents/{id}/containers/{containerId}", "stop"),
        ("/agents/{id}/containers/{containerId}", "restart"),
        ("/agents/{id}/projects/{projectId}/deploy", "up"),
        ("/agents/{id}/projects/{projectId}/deploy", "redeploy"),
        ("/agents/{id}/projects/{projectId}", "down"),
        ("/agents/{id}/projects/{projectId}", "restart"),
        ("/agents/{id}/images/updates/check", "all"),
        ("/agents/{id}/jobs/{jobId}", "run"),
    }


@pytest.fixture
async def arcane2(aiohttp_client, socket_enabled):
    """Stub einer Arcane-2-Instanz: kennt nur den alten Versionspfad."""
    app = web.Application()

    async def legacy(request: web.Request) -> web.Response:
        return web.json_response({"currentVersion": "2.17.3"})

    async def not_found(request: web.Request) -> web.Response:
        return web.json_response(
            {"success": False, "error": f"API endpoint not found: {request.path}"},
            status=404,
        )

    app.router.add_get("/api/app-version", legacy)
    app.router.add_route("*", "/{tail:.*}", not_found)
    http = await aiohttp_client(app)
    return ArcaneClient(http.session, str(http.make_url("")), "arc_test")


async def test_arcane_2_is_named(arcane2) -> None:
    """Gegen Arcane 2 meldet der Client die Version statt eines rohen 404."""
    with pytest.raises(ArcaneUnsupportedVersionError) as err:
        await arcane2.async_get_version()
    assert err.value.version == "2.17.3"


async def test_unknown_host_keeps_the_404(aiohttp_client, socket_enabled) -> None:
    """Antwortet auch der alte Pfad nicht, bleibt es beim 404."""
    app = web.Application()

    async def not_found(request: web.Request) -> web.Response:
        return web.json_response({"success": False}, status=404)

    app.router.add_route("*", "/{tail:.*}", not_found)
    http = await aiohttp_client(app)
    client = ArcaneClient(http.session, str(http.make_url("")), "arc_test")

    with pytest.raises(ArcaneNotFoundError):
        await client.async_get_version()
