"""Minimal async client for the Arcane REST API."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Any, Final
from urllib.parse import quote, urlsplit

from aiohttp import (
    ClientError,
    ClientResponse,
    ClientSession,
    ClientTimeout,
    ClientWebSocketResponse,
    WSMsgType,
    WSServerHandshakeError,
)
from homeassistant.util.json import json_loads
from yarl import URL

from .const import DEFAULT_TIMEOUT

API_PATH: Final = "/api"
HEADER_API_KEY: Final = "X-Api-Key"

ALLOWED_SCHEMES: Final = ("http", "https")

# Arcane 3 addresses environments under /api/agents/; /api/environments/ is
# gone (Arcane 3.0.0, backend/internal/environment/module.go).
AGENTS: Final = "agents"

# Only these actions may be sent to Arcane.
CONTAINER_ACTIONS: Final = ("start", "stop", "restart", "redeploy")
PROJECT_ACTIONS: Final = ("up", "down", "restart", "redeploy")

# Deploying a project goes to ``projects/{id}/deploy?action=`` and is answered
# with a newline delimited log stream instead of a JSON envelope. Every other
# project action goes to ``projects/{id}?action=``.
STREAMING_PROJECT_ACTIONS: Final = ("up", "redeploy")

# Arcane 2 served the version here; it only answers on an instance that is too
# old for this integration.
LEGACY_VERSION_PATH: Final = "app-version"

# Arcane paginates every list endpoint; ``-1`` asks for the full set.
_ALL_ITEMS: Final = "-1"

# Container actions block until Docker is done, and deploying a project can
# additionally pull images, so both get more headroom than a plain read.
ACTION_TIMEOUT: Final = 300
DEPLOY_TIMEOUT: Final = 900
# A Docker disk usage report sizes every volume, and an update check asks the
# registry of every image, so neither fits the default timeout of a read.
DISK_USAGE_TIMEOUT: Final = 120
# Arcane gives one container update up to 15 minutes and a whole updater run up
# to 30 (DefaultAutoUpdateOperation, DefaultAutoUpdateApply); a minute more here
# lets Arcane's own limit decide.
UPDATE_TIMEOUT: Final = 16 * 60
UPDATER_RUN_TIMEOUT: Final = 31 * 60

# Refuse to buffer a reply larger than this. Every endpoint used here returns a
# small JSON document, so anything bigger means the URL does not point at an
# Arcane API and should not be allowed to exhaust memory.
MAX_RESPONSE_BYTES: Final = 32 * 1024 * 1024

# A host statistics sample is a few hundred bytes.
MAX_WS_MESSAGE_BYTES: Final = 256 * 1024
HOST_STATS_TIMEOUT: Final = 20
# Asked for but never used: the socket is closed after the first sample.
HOST_STATS_INTERVAL: Final = 60

# Only the first part of an error body is quoted back, with control characters
# removed so a hostile reply cannot forge log lines.
_ERROR_BODY_CHARS: Final = 200
_ERROR_BODY_BYTES: Final = 4096
_READ_CHUNK_BYTES: Final = 64 * 1024
_MAX_STREAM_LINE_BYTES: Final = 1024 * 1024


class ArcaneError(Exception):
    """Base error for all Arcane API failures."""


class ArcaneConnectionError(ArcaneError):
    """Raised when the Arcane instance cannot be reached."""


class ArcaneAuthenticationError(ArcaneError):
    """Raised when Arcane does not accept the API key at all."""


class ArcanePermissionError(ArcaneError):
    """Raised when the API key is valid but lacks a permission.

    This is kept apart from an authentication failure: asking the user for a
    new key does not help, they have to widen the permissions of the one they
    have.
    """


class ArcaneResponseError(ArcaneError):
    """Raised when Arcane answers with an unexpected payload or status."""


class ArcaneNotFoundError(ArcaneResponseError):
    """Raised when Arcane does not know the requested path (HTTP 404)."""


class ArcaneUnsupportedVersionError(ArcaneError):
    """Raised when the instance runs an Arcane older than 3.0.

    Arcane 3 renamed its API paths, so an older instance answers every call
    with 404. The version it reports on its old path is kept for the message.
    """

    def __init__(self, version: str) -> None:
        """Remember the version the instance reported."""
        super().__init__(f"Arcane {version} is not supported, 3.0 or newer is needed")
        self.version = version


def validate_url(url: str) -> str:
    """Return a normalized instance URL, or raise ``ValueError``.

    Only plain http and https addresses are accepted. Userinfo in the URL is
    rejected: it would end up in log messages, and Arcane authenticates with
    the API key header, never with basic auth.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ALLOWED_SCHEMES:
        raise ValueError("Only http:// and https:// addresses are supported")
    if not parts.hostname:
        raise ValueError("The address is missing a host name")
    if parts.username or parts.password:
        raise ValueError("Credentials in the address are not supported")
    if parts.query or parts.fragment:
        raise ValueError("The address must not contain a query or fragment")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def _stream_frame(line: bytes) -> dict[str, Any] | None:
    """Return one decoded frame of a log stream, or None if it is not one."""
    if not line.strip():
        return None
    try:
        frame = json_loads(line)
    except ValueError:
        return None
    return frame if isinstance(frame, dict) else None


def _stream_failure(line: bytes) -> str | None:
    """Return the error a stream frame reports, if it reports one."""
    frame = _stream_frame(line)
    if frame is None or (failure := frame.get("error")) is None:
        return None
    return _sanitize(str(failure))


def _stream_done(line: bytes) -> bool:
    """Return whether this frame is the one that closes a successful stream."""
    frame = _stream_frame(line)
    return frame is not None and frame.get("done") is True


def _sanitize(text: str) -> str:
    """Return text that is safe to put into an error message or log line."""
    return "".join(
        char if char.isprintable() else " " for char in text[:_ERROR_BODY_CHARS]
    ).strip()


def _reject_redirected_socket(socket: ClientWebSocketResponse, url: URL) -> None:
    """Refuse a WebSocket that was reached through a redirect.

    aiohttp offers no way to turn redirects off for the WebSocket handshake, so
    the hop is detected afterwards: a redirect would already have replayed the
    API key header against the host it pointed at, which is exactly what the
    REST side refuses. The private attribute is read defensively; the test
    suite covers this path so a rename in aiohttp fails loudly there.
    """
    response = getattr(socket, "_response", None)
    if response is not None and getattr(response, "history", ()):
        raise ArcaneResponseError(
            f"{url} redirected to another address. Configure the address "
            f"Arcane is actually served on."
        )


class ArcaneClient:
    """Thin wrapper around the endpoints the integration needs."""

    def __init__(
        self,
        session: ClientSession,
        url: str,
        api_key: str,
        *,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the client for a single Arcane instance."""
        api_key = api_key.strip()
        if not api_key:
            raise ValueError("The API key must not be empty")
        if any(char in api_key for char in "\r\n\x00"):
            raise ValueError("The API key contains invalid characters")

        self._session = session
        base = validate_url(url)
        scheme, _, rest = base.partition("://")
        netloc, slash, path = rest.partition("/")
        self._base_url = (
            f"{scheme}://{netloc}{slash}{quote(path, safe='/%')}{API_PATH}/"
        )
        self._api_key = api_key
        self._timeout = ClientTimeout(total=timeout)

    def _url(self, *segments: str) -> URL:
        """Build an API URL from fully escaped path segments.

        Container names, project IDs and environment IDs come from the Arcane
        API, so they are escaped rather than trusted: no segment can add a path
        of its own or walk out of ``/api/``. The URL is marked as encoded so
        the escaping is not undone before the request goes out.
        """
        path = "/".join(quote(str(segment), safe="") for segment in segments)
        return URL(self._base_url + path, encoded=True)

    async def _request(
        self,
        method: str,
        url: URL,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        timeout: int | None = None,
    ) -> Any:
        """Perform a request and return the decoded JSON body."""
        try:
            response = await self._session.request(
                method,
                url,
                params=params,
                json=json,
                headers={HEADER_API_KEY: self._api_key},
                timeout=ClientTimeout(total=timeout) if timeout else self._timeout,
                # Redirects are not followed: aiohttp would replay the API key
                # header against whatever host the redirect points at.
                allow_redirects=False,
            )
        except TimeoutError as err:
            raise ArcaneConnectionError(f"Timeout while calling {url}") from err
        except ClientError as err:
            raise ArcaneConnectionError(f"Error while calling {url}: {err}") from err

        async with response:
            return await self._decode(response, url)

    @staticmethod
    async def _decode(response: ClientResponse, url: URL) -> Any:
        """Turn a response into JSON, mapping HTTP errors onto exceptions."""
        if response.status == HTTPStatus.UNAUTHORIZED:
            raise ArcaneAuthenticationError(f"Arcane rejected the API key for {url}")
        if response.status == HTTPStatus.FORBIDDEN:
            raise ArcanePermissionError(f"The API key is not allowed to call {url}")
        if HTTPStatus.MULTIPLE_CHOICES <= response.status < HTTPStatus.BAD_REQUEST:
            raise ArcaneResponseError(
                f"{url} redirected to another address. Configure the address "
                f"Arcane is actually served on."
            )
        if response.status >= HTTPStatus.BAD_REQUEST:
            # Only the first chunk is read: the body of an error reply is quoted
            # back to the user and is not trusted to be small.
            body = _sanitize(
                (await response.content.read(_ERROR_BODY_BYTES)).decode(
                    "utf-8", "replace"
                )
            )
            error = (
                ArcaneNotFoundError
                if response.status == HTTPStatus.NOT_FOUND
                else ArcaneResponseError
            )
            raise error(f"Arcane returned HTTP {response.status} for {url}: {body}")

        if (length := response.content_length) is not None and (
            length > MAX_RESPONSE_BYTES
        ):
            raise ArcaneResponseError(f"Reply from {url} is too large ({length} bytes)")
        chunks: list[bytes] = []
        size = 0
        try:
            async for chunk in response.content.iter_chunked(_READ_CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ArcaneResponseError(f"Reply from {url} is too large")
                chunks.append(chunk)
        except ClientError as err:
            raise ArcaneConnectionError(f"Error while reading {url}: {err}") from err
        raw = b"".join(chunks)

        if not raw.strip():
            # Some action endpoints answer without a body.
            return None
        try:
            return json_loads(raw)
        except ValueError as err:
            raise ArcaneResponseError(f"Invalid JSON from {url}") from err

    async def _stream(
        self, url: URL, *, params: dict[str, Any] | None = None, timeout: int
    ) -> None:
        """Run an operation that answers with a newline delimited log stream.

        The stream ends with ``{"done": true}`` on success or carries an
        ``{"error": ...}`` line on failure. Log lines in between are dropped:
        Home Assistant only needs to know whether the operation worked.
        """
        try:
            response = await self._session.request(
                "POST",
                url,
                params=params,
                headers={HEADER_API_KEY: self._api_key},
                timeout=ClientTimeout(total=timeout),
                allow_redirects=False,
            )
        except TimeoutError as err:
            raise ArcaneConnectionError(f"Timeout while calling {url}") from err
        except ClientError as err:
            raise ArcaneConnectionError(f"Error while calling {url}: {err}") from err

        async with response:
            if response.status >= HTTPStatus.MULTIPLE_CHOICES:
                await self._decode(response, url)
                return

            done = False
            size = 0
            buffer = b""
            try:
                async for chunk in response.content.iter_chunked(_READ_CHUNK_BYTES):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ArcaneResponseError(f"Reply from {url} is too large")
                    buffer += chunk
                    lines = buffer.split(b"\n")
                    # Lines are split here rather than read with readline so a
                    # reply without newlines cannot grow the buffer unbounded.
                    buffer = lines.pop()
                    if len(buffer) > _MAX_STREAM_LINE_BYTES:
                        raise ArcaneResponseError(f"Reply from {url} is malformed")
                    for line in lines:
                        if (failure := _stream_failure(line)) is not None:
                            raise ArcaneResponseError(failure)
                        done = done or _stream_done(line)
            except ClientError as err:
                raise ArcaneConnectionError(
                    f"Error while reading {url}: {err}"
                ) from err
            if (failure := _stream_failure(buffer)) is not None:
                raise ArcaneResponseError(failure)
            done = done or _stream_done(buffer)

        if not done:
            raise ArcaneResponseError(f"{url} ended without reporting that it finished")

    async def _get_data(
        self,
        url: URL,
        params: dict[str, Any] | None = None,
        *,
        timeout: int | None = None,
    ) -> Any:
        """Return the ``data`` member of an Arcane API envelope."""
        payload = await self._request("GET", url, params=params, timeout=timeout)
        if not isinstance(payload, dict) or "data" not in payload:
            raise ArcaneResponseError(f"Unexpected payload from {url}")
        return payload["data"]

    async def _get_post_data(
        self,
        url: URL,
        *,
        params: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> Any:
        """POST without a body and return the ``data`` member of the reply."""
        payload = await self._request("POST", url, params=params, timeout=timeout)
        if not isinstance(payload, dict) or "data" not in payload:
            raise ArcaneResponseError(f"Unexpected payload from {url}")
        return payload["data"]

    async def async_get_version(self) -> dict[str, Any]:
        """Return version information about the Arcane instance."""
        try:
            payload = await self._request("GET", self._url("app", "version"))
        except ArcaneNotFoundError:
            await self._raise_if_legacy()
            raise
        if not isinstance(payload, dict):
            raise ArcaneResponseError("Unexpected payload for the version endpoint")
        return payload

    async def _raise_if_legacy(self) -> None:
        """Raise ArcaneUnsupportedVersionError if this is an Arcane 2 instance.

        Only called after ``app/version`` answered 404, so a working setup never
        pays for the extra request. Anything but a version reply on the old
        path leaves the original 404 to be reported.
        """
        try:
            legacy = await self._request("GET", self._url(LEGACY_VERSION_PATH))
        except ArcaneError:
            return
        if isinstance(legacy, dict) and (version := legacy.get("currentVersion")):
            raise ArcaneUnsupportedVersionError(_sanitize(str(version)))

    async def async_get_environment_version(
        self, environment_id: str
    ) -> dict[str, Any]:
        """Return the Arcane version and update state of one environment."""
        data = await self._get_data(self._url(AGENTS, environment_id, "version"))
        return data if isinstance(data, dict) else {}

    async def async_upgrade(self, environment_id: str) -> None:
        """Upgrade the Arcane instance of an environment.

        Arcane pulls its own newer image and restarts itself, so the call is
        accepted and then carried out in the background.
        """
        await self._request(
            "POST",
            self._url(AGENTS, environment_id, "system", "upgrade"),
            timeout=ACTION_TIMEOUT,
        )

    async def async_get_environments(self) -> list[dict[str, Any]]:
        """Return every environment the API key may see."""
        data = await self._get_data(self._url(AGENTS), {"limit": _ALL_ITEMS})
        return data if isinstance(data, list) else []

    async def async_get_containers(
        self,
        environment_id: str,
        *,
        include_internal: bool = False,
        include_hidden: bool = False,
        resources: bool = False,
    ) -> list[dict[str, Any]]:
        """Return all containers of an environment.

        With ``resources`` Arcane sorts the list by CPU usage, and only then
        fills ``resourceSample`` (CPU, memory, network and block I/O) for every
        running container (container/service.go, IsContainerResourceSort).
        The order does not matter here; the sample does.
        """
        params = {
            "limit": _ALL_ITEMS,
            "includeInternal": str(include_internal).lower(),
            "includeHidden": str(include_hidden).lower(),
        }
        if resources:
            params["sort"] = "cpuUsage"
        data = await self._get_data(
            self._url(AGENTS, environment_id, "containers"), params
        )
        return data if isinstance(data, list) else []

    async def async_get_volume_sizes(self, environment_id: str) -> list[dict[str, Any]]:
        """Return the size of every volume of an environment.

        Docker measures each volume for this (Arcane calls it a slow
        operation), so it is fetched in the background like the disk usage
        report, not on every refresh.
        """
        data = await self._get_data(
            self._url(AGENTS, environment_id, "volumes", "sizes"),
            timeout=DISK_USAGE_TIMEOUT,
        )
        return data if isinstance(data, list) else []

    async def async_get_projects(self, environment_id: str) -> list[dict[str, Any]]:
        """Return all compose projects of an environment."""
        data = await self._get_data(
            self._url(AGENTS, environment_id, "projects"),
            {"limit": _ALL_ITEMS},
        )
        return data if isinstance(data, list) else []

    async def async_get_image_counts(self, environment_id: str) -> dict[str, Any]:
        """Return image usage counts of an environment."""
        data = await self._get_data(
            self._url(AGENTS, environment_id, "images", "counts")
        )
        return data if isinstance(data, dict) else {}

    async def async_get_volume_counts(self, environment_id: str) -> dict[str, Any]:
        """Return volume usage counts of an environment."""
        data = await self._get_data(
            self._url(AGENTS, environment_id, "volumes", "counts")
        )
        return data if isinstance(data, dict) else {}

    async def async_get_network_counts(self, environment_id: str) -> dict[str, Any]:
        """Return network usage counts of an environment."""
        data = await self._get_data(
            self._url(AGENTS, environment_id, "networks", "counts")
        )
        return data if isinstance(data, dict) else {}

    async def async_get_docker_info(self, environment_id: str) -> dict[str, Any]:
        """Return Docker daemon information of an environment."""
        payload = await self._request(
            "GET", self._url(AGENTS, environment_id, "system", "docker", "info")
        )
        return payload if isinstance(payload, dict) else {}

    async def async_get_disk_usage(self, environment_id: str) -> dict[str, Any]:
        """Return the disk space unused images, volumes and the like hold."""
        data = await self._get_data(
            self._url(AGENTS, environment_id, "system", "df"),
            timeout=DISK_USAGE_TIMEOUT,
        )
        return data if isinstance(data, dict) else {}

    async def async_get_release_notes(
        self,
        environment_id: str,
        image_ref: str,
        *,
        version: str | None = None,
        digest: str | None = None,
    ) -> dict[str, Any]:
        """Return the release notes Arcane finds for the newer image.

        The newer version only names a tag when the update is a new tag; for a
        moving tag the digest identifies the newer image. Arcane's own update
        view asks the same way.
        """
        params = {"imageRef": image_ref}
        if version:
            params["version"] = version
        if digest:
            params["digest"] = digest
        data = await self._get_data(
            self._url(AGENTS, environment_id, "images", "updates", "releasenotes"),
            params,
        )
        return data if isinstance(data, dict) else {}

    async def async_wait_for_changes(
        self,
        environment_id: str,
        *,
        epoch: str | None,
        since: int,
        wait: int,
    ) -> dict[str, Any]:
        """Wait until something changes in an environment, then say what.

        Arcane answers at once when ``epoch`` differs from its own (it was
        restarted, or this is the first call) or a kind of resource has a
        revision above ``since``; otherwise it holds the request for up to
        ``wait`` seconds and answers with the revisions as they are.
        """
        params = {"since": str(since), "wait": str(wait)}
        if epoch:
            params["epoch"] = epoch
        data = await self._get_data(
            self._url(AGENTS, environment_id, "events", "changes"),
            params,
            timeout=wait + 20,
        )
        return data if isinstance(data, dict) else {}

    async def async_get_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return the newest entries of Arcane's event log, of every environment.

        The whole log rather than ``events/agent/{id}``: Arcane files logins,
        logouts and its own upgrades without an environment, and the
        per-environment list only returns entries filed under that one.
        """
        data = await self._get_data(
            self._url("events"),
            {"sort": "timestamp", "order": "desc", "limit": str(limit)},
        )
        return data if isinstance(data, list) else []

    async def async_update_container(
        self, environment_id: str, container_id: str
    ) -> dict[str, Any]:
        """Bring a container onto its newer image through Arcane's updater.

        Unlike a redeploy, the updater follows the container's tag policy onto a
        newer tag, skips a container whose image did not change, and pulls
        through Arcane's vulnerability gate when that is on. Arcane answers once
        the update is done, with one result per resource it touched.
        """
        data = await self._get_post_data(
            self._url(AGENTS, environment_id, "containers", container_id, "update"),
            timeout=UPDATE_TIMEOUT,
        )
        return data if isinstance(data, dict) else {}

    async def async_set_auto_update(
        self, environment_id: str, container_id: str, *, enabled: bool
    ) -> None:
        """Include a container in Arcane's automatic updates, or leave it out.

        Arcane keeps a list of containers its automatic updates skip; this adds
        or removes the container's name. The container itself is not touched.
        """
        await self._request(
            "PUT",
            self._url(AGENTS, environment_id, "containers", container_id, "autoupdate"),
            json={"enabled": enabled},
        )

    async def async_run_updater(self, environment_id: str) -> dict[str, Any]:
        """Install every pending update of an environment, as Arcane would itself.

        ``automatic=true`` makes the run honour the containers left out of
        automatic updates and the opt-out label, like Arcane's scheduled runs.
        """
        data = await self._get_post_data(
            self._url(AGENTS, environment_id, "updater", "run"),
            params={"automatic": "true"},
            timeout=UPDATER_RUN_TIMEOUT,
        )
        return data if isinstance(data, dict) else {}

    async def async_get_updater_status(self, environment_id: str) -> dict[str, Any]:
        """Return which containers and projects Arcane is updating right now."""
        data = await self._get_data(
            self._url(AGENTS, environment_id, "updater", "status")
        )
        return data if isinstance(data, dict) else {}

    async def async_get_updater_history(
        self, environment_id: str, *, limit: int = 1
    ) -> list[dict[str, Any]]:
        """Return the newest records of updates Arcane applied, newest first."""
        data = await self._get_data(
            self._url(AGENTS, environment_id, "updater", "history"),
            {"limit": str(limit)},
        )
        return data if isinstance(data, list) else []

    async def async_get_jobs(self, environment_id: str) -> list[dict[str, Any]]:
        """Return Arcane's background jobs with their last and next run.

        Unlike most of the API, Arcane sends the job list without the usual
        ``{"success", "data"}`` envelope (``JobListResponse``).
        """
        url = self._url(AGENTS, environment_id, "jobs")
        payload = await self._request("GET", url)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ArcaneResponseError(f"Unexpected payload from {url}")
        return [job for job in payload["jobs"] if isinstance(job, dict)]

    async def async_run_job(self, environment_id: str, job_id: str) -> dict[str, Any]:
        """Queue a run of one of Arcane's background jobs.

        Arcane accepts the run and answers at once (202), before the job runs;
        the job list shows it running and then its outcome.
        """
        payload = await self._request(
            "POST",
            self._url(AGENTS, environment_id, "jobs", job_id),
            params={"action": "run"},
        )
        return payload if isinstance(payload, dict) else {}

    async def async_check_image_updates(self, environment_id: str) -> None:
        """Ask Arcane to check every image for an update right now.

        Arcane answers once it has asked the registry of every image, which
        takes longer than an ordinary request.
        """
        await self._request(
            "POST",
            self._url(AGENTS, environment_id, "images", "updates", "check"),
            params={"action": "all"},
            timeout=ACTION_TIMEOUT,
        )

    async def async_container_action(
        self, environment_id: str, container_id: str, action: str
    ) -> None:
        """Run start, stop, restart or redeploy on a container."""
        if action not in CONTAINER_ACTIONS:
            raise ValueError(f"Unsupported container action: {action}")
        if action == "redeploy":
            url = self._url(
                AGENTS, environment_id, "containers", container_id, "redeploy"
            )
            await self._request("POST", url, timeout=DEPLOY_TIMEOUT)
            return
        await self._request(
            "POST",
            self._url(AGENTS, environment_id, "containers", container_id),
            params={"action": action},
            timeout=ACTION_TIMEOUT,
        )

    async def async_project_action(
        self, environment_id: str, project_id: str, action: str
    ) -> None:
        """Bring a compose project up or down, restart or redeploy it."""
        if action not in PROJECT_ACTIONS:
            raise ValueError(f"Unsupported project action: {action}")
        params = {"action": action}
        if action in STREAMING_PROJECT_ACTIONS:
            url = self._url(AGENTS, environment_id, "projects", project_id, "deploy")
            await self._stream(url, params=params, timeout=DEPLOY_TIMEOUT)
            return
        url = self._url(AGENTS, environment_id, "projects", project_id)
        await self._request("POST", url, params=params, timeout=ACTION_TIMEOUT)

    async def async_prune(
        self, environment_id: str, *, unused_images: bool
    ) -> dict[str, Any]:
        """Remove unused images, networks and build cache.

        Containers and volumes are never included: pruning those destroys data
        that cannot be pulled again. Arcane 3 starts the prune as a background
        activity and answers at once with its ``activityId``, before anything
        is deleted; ``async_get_activity`` tells when it is over.
        """
        payload = {
            "images": {"mode": "all" if unused_images else "dangling"},
            "networks": {"mode": "unused"},
            "buildCache": {"mode": "unused"},
        }
        result = await self._request(
            "POST",
            self._url(AGENTS, environment_id, "system", "prune"),
            json=payload,
            timeout=DEPLOY_TIMEOUT,
        )
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            return result["data"]
        return {}

    async def async_get_activity(
        self, environment_id: str, activity_id: str
    ) -> dict[str, Any]:
        """Return one of Arcane's background activities, such as a prune.

        Its ``status`` is ``queued`` or ``running`` while it goes on and
        ``success``, ``failed`` or ``cancelled`` once it is over. Only the
        newest progress message is asked for; the status is what counts.
        """
        url = self._url(AGENTS, environment_id, "activities", activity_id)
        data = await self._get_data(url, {"limit": "1"})
        activity = data.get("activity") if isinstance(data, dict) else None
        if not isinstance(activity, dict):
            raise ArcaneResponseError(f"Unexpected payload from {url}")
        return activity

    async def async_get_host_stats(
        self, environment_id: str, *, timeout: int = HOST_STATS_TIMEOUT
    ) -> dict[str, Any]:
        """Return one host statistics sample.

        Arcane only offers host CPU, memory and disk usage over a WebSocket. It
        sends the current sample immediately after the handshake, so a single
        sample is taken and the socket is closed again rather than held open.
        """
        url = self._url(AGENTS, environment_id, "ws", "system", "stats").with_query(
            {"interval": str(HOST_STATS_INTERVAL)}
        )
        try:
            async with (
                asyncio.timeout(timeout),
                # The handshake and the single receive are bounded by the
                # timeout around this block, which keeps the call free of
                # aiohttp's shifting per-socket timeout parameters.
                self._session.ws_connect(
                    url,
                    headers={HEADER_API_KEY: self._api_key},
                    max_msg_size=MAX_WS_MESSAGE_BYTES,
                    autoclose=True,
                ) as socket,
            ):
                _reject_redirected_socket(socket, url)
                message = await socket.receive()
                if message.type is not WSMsgType.TEXT:
                    raise ArcaneResponseError(
                        f"Host statistics stream at {url} sent no sample"
                    )
                sample = json_loads(message.data)
        except TimeoutError as err:
            raise ArcaneConnectionError(f"Timeout while calling {url}") from err
        except WSServerHandshakeError as err:
            if err.status == HTTPStatus.UNAUTHORIZED:
                raise ArcaneAuthenticationError(
                    f"Arcane rejected the API key for {url}"
                ) from err
            if err.status == HTTPStatus.FORBIDDEN:
                raise ArcanePermissionError(
                    f"The API key is not allowed to call {url}"
                ) from err
            raise ArcaneConnectionError(f"Error while calling {url}: {err}") from err
        except ClientError as err:
            raise ArcaneConnectionError(f"Error while calling {url}: {err}") from err
        except ValueError as err:
            raise ArcaneResponseError(f"Invalid JSON from {url}") from err

        return sample if isinstance(sample, dict) else {}
