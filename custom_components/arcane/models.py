"""Data models describing an Arcane instance snapshot."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    ENVIRONMENT_STATUS_ONLINE,
    HEALTH_HEALTHY,
    HEALTH_STARTING,
    HEALTH_UNHEALTHY,
    LABEL_FALSE_VALUES,
    LABEL_UPDATER,
    PROJECT_STATE_PARTIALLY_RUNNING,
    PROJECT_STATE_RUNNING,
    STATE_RUNNING,
)

# Docker puts the health of a container and the exit code of a stopped one into
# the status line, and the container list carries nothing else about either.
_HEALTH_PATTERN = re.compile(r"\((healthy|unhealthy|health: starting)\)")
_EXIT_CODE_PATTERN = re.compile(r"^Exited \((\d+)\)")

# Stands in for the newest version of an image Arcane found an update for
# without naming a version or a digest.
NEWER_IMAGE = "newer image"


def _container_name(payload: dict[str, Any]) -> str:
    """Return the primary container name without the Docker slash prefix."""
    names = payload.get("names") or []
    for name in names:
        if isinstance(name, str) and name.strip("/"):
            return name.strip("/")
    return str(payload.get("id", ""))[:12]


def _short_digest(value: Any) -> str | None:
    """Return a readable short form of an image digest."""
    if not isinstance(value, str) or not value.strip():
        return None
    digest = value.strip().rpartition("@")[2]
    return digest.removeprefix("sha256:")[:12] or None


def _health(status: str) -> str | None:
    """Return the health Docker reports in a status line, if it reports one."""
    if (match := _HEALTH_PATTERN.search(status)) is None:
        return None
    found = match.group(1)
    if found == "health: starting":
        return HEALTH_STARTING
    return HEALTH_UNHEALTHY if found == HEALTH_UNHEALTHY else HEALTH_HEALTHY


def _exit_code(status: str) -> int | None:
    """Return the exit code of a stopped container, if the status carries one."""
    if (match := _EXIT_CODE_PATTERN.match(status.strip())) is None:
        return None
    return int(match.group(1))


def _version(value: Any) -> str | None:
    """Return a non-empty version string, or None."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _split_image(image: str) -> tuple[str, str | None]:
    """Split an image reference into its name and tag.

    A pinned digest is dropped, and a colon in front of a slash belongs to a
    registry port rather than to a tag.
    """
    reference = image.partition("@")[0]
    name, separator, tag = reference.rpartition(":")
    if not separator or "/" in tag:
        return reference, None
    return name, tag or None


def image_name(image: str) -> str | None:
    """Return an image reference without its tag and digest."""
    return _split_image(image)[0] or None


def _image_tag(image: str) -> str | None:
    """Return the tag of an image reference."""
    return _split_image(image)[1]


def _image_versions(
    update_info: dict[str, Any], image: str
) -> tuple[str | None, str | None]:
    """Return the installed and the newest version of a container image.

    Arcane decides whether there is an update, and the update entity has to
    agree with it. A moving tag such as ``latest`` is updated by publishing a
    new digest under the same tag, so Arcane reports the tag as both versions.
    Home Assistant only lists an update whose two versions differ, so the
    digests stand in for the versions then.
    """
    installed = _version(update_info.get("currentVersion")) or _image_tag(image)
    latest = _version(update_info.get("latestVersion"))
    if not update_info.get("hasUpdate"):
        # A newer version Arcane does not offer, for example because the update
        # policy of the container ignores it, is no update either.
        return installed, installed
    if latest is not None and latest != installed:
        return installed, latest

    current_digest = _short_digest(update_info.get("currentDigest"))
    latest_digest = _short_digest(update_info.get("latestDigest"))
    if latest_digest is not None and latest_digest != current_digest:
        return current_digest or installed, latest_digest
    # Arcane named neither a version nor a digest, but still found an update.
    return installed, f"{installed} ({NEWER_IMAGE})" if installed else NEWER_IMAGE


def _label_disables_updater(labels: Any) -> bool:
    """Return whether the container's labels opt it out of Arcane's updater.

    Mirrors Arcane's own check (labels.IsUpdateDisabled): the label name in any
    case, a value Arcane reads as false.
    """
    if not isinstance(labels, dict):
        return False
    for key, value in labels.items():
        if isinstance(key, str) and key.lower() == LABEL_UPDATER:
            return str(value).strip().lower() in LABEL_FALSE_VALUES
    return False


def _created_at(value: Any) -> datetime | None:
    """Return the creation timestamp of a container as an aware datetime."""
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, tz=UTC)
    return None


@dataclass(slots=True)
class ArcaneContainer:
    """A single Docker container as reported by Arcane."""

    id: str
    name: str
    state: str
    status: str
    image: str
    created: datetime | None
    update_available: bool
    installed_version: str | None
    latest_version: str | None
    health: str | None
    exit_code: int | None
    project: str | None
    service: str | None
    #: Arcane found an update but holds it back, for example because the newer
    #: image exceeds a vulnerability limit.
    update_blocked: bool = False
    update_blocked_reason: str | None = None
    #: How the newer image differs: a new tag, a new digest under the same tag,
    #: or not pulled yet. Decides how its release notes are looked up.
    update_type: str | None = None
    #: Full digest of the newer image, as Arcane needs it for release notes.
    latest_digest: str | None = None
    #: Whether Arcane's automatic updates include the container; None when
    #: Arcane does not say.
    auto_update_enabled: bool | None = None
    #: The container opts out of Arcane's updater by label, which no switch
    #: in Arcane or here can override.
    auto_update_label_disabled: bool = False

    @property
    def is_running(self) -> bool:
        """Return whether the container is currently running."""
        return self.state == STATE_RUNNING

    @property
    def is_healthy(self) -> bool | None:
        """Return the health of the container, or None if it reports none."""
        if self.health is None:
            return None
        return self.health == HEALTH_HEALTHY

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> ArcaneContainer:
        """Build a container from an Arcane container summary."""
        labels = payload.get("labels") or {}
        update_info = payload.get("updateInfo") or {}
        image = str(payload.get("image") or "")
        status = str(payload.get("status") or "")
        installed, latest = _image_versions(update_info, image)
        return cls(
            id=str(payload.get("id", "")),
            name=_container_name(payload),
            state=str(payload.get("state") or "unknown"),
            status=status,
            image=image,
            created=_created_at(payload.get("created")),
            update_available=bool(update_info.get("hasUpdate")),
            installed_version=installed,
            latest_version=latest,
            health=_health(status),
            exit_code=_exit_code(status),
            project=labels.get("com.docker.compose.project"),
            service=labels.get("com.docker.compose.service"),
            # Arcane only reports a block together with an update, but a stale
            # flag on an image without one must not hold anything back.
            update_blocked=bool(update_info.get("hasUpdate"))
            and bool(update_info.get("blocked")),
            update_blocked_reason=_version(update_info.get("blockedReason")),
            update_type=_version(update_info.get("updateType")),
            latest_digest=_version(update_info.get("latestDigest")),
            auto_update_enabled=(
                auto_update
                if isinstance(auto_update := payload.get("autoUpdateEnabled"), bool)
                else None
            ),
            auto_update_label_disabled=_label_disables_updater(labels),
        )


@dataclass(slots=True)
class ArcaneProject:
    """A compose project (stack) as reported by Arcane."""

    id: str
    name: str
    status: str
    running_count: int
    service_count: int
    update_available: bool

    @property
    def is_running(self) -> bool:
        """Return whether at least one service of the project runs."""
        return self.status in (PROJECT_STATE_RUNNING, PROJECT_STATE_PARTIALLY_RUNNING)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> ArcaneProject:
        """Build a project from an Arcane project details payload."""
        update_info = payload.get("updateInfo") or {}
        return cls(
            id=str(payload.get("id", "")),
            name=str(payload.get("name") or payload.get("id") or ""),
            status=str(payload.get("status") or "unknown"),
            running_count=int(payload.get("runningCount") or 0),
            service_count=int(payload.get("serviceCount") or 0),
            update_available=bool(update_info.get("hasUpdate")),
        )


@dataclass(slots=True)
class ArcaneVersion:
    """Version and update state of an Arcane instance."""

    installed: str | None
    latest: str | None
    update_available: bool
    release_url: str | None
    release_notes: str | None
    released_at: str | None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> ArcaneVersion:
        """Build the version state from an Arcane version payload."""
        installed = _version(payload.get("currentVersion"))
        latest = _version(payload.get("newestVersion"))
        update_available = bool(payload.get("updateAvailable"))
        if not update_available:
            # Arcane decides whether it offers an update, so a newer release
            # it does not offer is not shown as one either.
            latest = installed
        elif latest is None or latest == installed:
            # An instance that tracks a digest rather than a release reports an
            # update without naming a newer version, or names the one it runs
            # when a new build of it is out. The digest is what changed, so it
            # stands in as the newer version; Home Assistant needs the two to
            # differ for the entity to read as "update available".
            latest = _short_digest(payload.get("newestDigest")) or latest
        return cls(
            installed=installed,
            # Home Assistant compares the two, so an unknown newest version has
            # to read as "same as installed" rather than as an update.
            latest=latest or installed,
            update_available=update_available,
            release_url=_version(payload.get("releaseUrl")),
            release_notes=_version(payload.get("releaseNotes")),
            released_at=_version(payload.get("releasedAt")),
        )


@dataclass(slots=True)
class ArcaneHostStats:
    """Host resource usage of the machine an environment runs on."""

    cpu_percent: float | None
    cpu_count: int | None
    memory_used: int | None
    memory_total: int | None
    disk_used: int | None
    disk_total: int | None
    hostname: str | None

    @staticmethod
    def _percent(used: int | None, total: int | None) -> float | None:
        """Return used as a percentage of total."""
        if not used or not total:
            return None
        return round(used / total * 100, 1)

    @property
    def memory_percent(self) -> float | None:
        """Return the share of host memory in use."""
        return self._percent(self.memory_used, self.memory_total)

    @property
    def disk_percent(self) -> float | None:
        """Return the share of host disk space in use."""
        return self._percent(self.disk_used, self.disk_total)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> ArcaneHostStats:
        """Build host statistics from one WebSocket sample."""

        def number(key: str) -> Any:
            value = payload.get(key)
            return value if isinstance(value, (int, float)) else None

        cpu = number("cpuUsage")
        return cls(
            cpu_percent=round(float(cpu), 1) if cpu is not None else None,
            cpu_count=payload.get("cpuCount") or None,
            memory_used=number("memoryUsage"),
            memory_total=number("memoryTotal"),
            disk_used=number("diskUsage"),
            disk_total=number("diskTotal"),
            hostname=payload.get("hostname") or None,
        )


def _timestamp(value: Any) -> datetime | None:
    """Return an RFC 3339 timestamp from the API as an aware datetime."""
    if not isinstance(value, str) or not value:
        return None
    parsed = dt_util.parse_datetime(value)
    return parsed if parsed is not None and parsed.tzinfo is not None else None


def _text(value: Any) -> str | None:
    """Return a non-empty string from the API, or None."""
    return value if isinstance(value, str) and value else None


@dataclass(slots=True)
class ArcaneJob:
    """One of Arcane's background jobs, such as its automatic updates.

    Built from an entry of ``GET agents/{id}/jobs``. Arcane fills ``lastRun``
    with the newest finished run and ``currentRun`` with one that is queued or
    running; ``lastError`` carries the message of the newest run that failed,
    went partly wrong or is being retried.
    """

    id: str
    name: str
    category: str | None = None
    schedule: str | None = None
    enabled: bool = False
    can_run_manually: bool = False
    manager_only: bool = False
    #: Whether every setting the job needs, such as a configured scanner, is on.
    prerequisites_met: bool = True
    next_run: datetime | None = None
    last_success: datetime | None = None
    last_error: str | None = None
    #: Status of the newest finished run, one of ``JOB_RUN_STATUSES``.
    last_status: str | None = None
    last_trigger: str | None = None
    last_started: datetime | None = None
    last_finished: datetime | None = None
    last_message: str | None = None
    #: Status of a run that is queued, waiting, running or being retried.
    current_status: str | None = None
    #: Health of the worker of a continuous job, such as the image watcher.
    worker_status: str | None = None
    worker_error: str | None = None

    @property
    def running(self) -> bool:
        """Return whether a run of the job is queued or under way."""
        return self.current_status is not None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> ArcaneJob:
        """Create a job from an entry of Arcane's job list."""
        last = payload.get("lastRun")
        last = last if isinstance(last, dict) else {}
        outcome = last.get("outcome")
        outcome = outcome if isinstance(outcome, dict) else {}
        current = payload.get("currentRun")
        current = current if isinstance(current, dict) else {}
        worker = payload.get("workerHealth")
        worker = worker if isinstance(worker, dict) else {}
        prerequisites = payload.get("prerequisites")
        return cls(
            id=str(payload.get("id") or ""),
            name=str(payload.get("name") or payload.get("id") or ""),
            category=_text(payload.get("category")),
            schedule=_text(payload.get("schedule")),
            enabled=payload.get("enabled") is True,
            can_run_manually=payload.get("canRunManually") is True,
            manager_only=payload.get("managerOnly") is True,
            prerequisites_met=all(
                isinstance(item, dict) and item.get("isMet") is True
                for item in (prerequisites if isinstance(prerequisites, list) else [])
            ),
            next_run=_timestamp(payload.get("nextRun")),
            last_success=_timestamp(payload.get("lastSuccess")),
            last_error=_text(payload.get("lastError")),
            last_status=_text(last.get("status")),
            last_trigger=_text(last.get("trigger")),
            last_started=_timestamp(last.get("startedAt"))
            or _timestamp(last.get("createdAt")),
            last_finished=_timestamp(last.get("finishedAt")),
            last_message=_text(outcome.get("message")),
            current_status=_text(current.get("status")) if current else None,
            worker_status=_text(worker.get("status")),
            worker_error=_text(worker.get("lastError")),
        )


@dataclass(slots=True)
class ArcaneEnvironment:
    """Everything the integration knows about one Arcane environment."""

    id: str
    name: str
    status: str
    enabled: bool
    reachable: bool = False
    docker_version: str | None = None
    docker_info: dict[str, Any] = field(default_factory=dict)
    containers: dict[str, ArcaneContainer] = field(default_factory=dict)
    projects: dict[str, ArcaneProject] = field(default_factory=dict)
    image_counts: dict[str, Any] = field(default_factory=dict)
    volume_counts: dict[str, Any] = field(default_factory=dict)
    network_counts: dict[str, Any] = field(default_factory=dict)
    host_stats: ArcaneHostStats | None = None
    arcane_version: ArcaneVersion | None = None
    #: What Arcane's updater is busy with (``updater/status``), and the newest
    #: record of an update it applied (``updater/history``), with the option.
    updater_status: dict[str, Any] = field(default_factory=dict)
    updater_last: dict[str, Any] | None = None
    #: Reclaimable disk space by kind, in bytes, as Arcane's ``system/df``
    #: reports it. Empty until the first report is in.
    disk_usage: dict[str, Any] = field(default_factory=dict)
    #: When the report in ``disk_usage`` was asked for; None until one is in.
    disk_usage_at: datetime | None = None
    #: Arcane's background jobs by ID, with the option for them. Jobs Arcane
    #: lists below another one, such as one per GitOps sync, are left out.
    jobs: dict[str, ArcaneJob] = field(default_factory=dict)

    @property
    def is_online(self) -> bool:
        """Return whether Arcane considers the environment online."""
        return self.reachable and self.status == ENVIRONMENT_STATUS_ONLINE

    @property
    def containers_running(self) -> int:
        """Return the number of running containers."""
        return sum(1 for item in self.containers.values() if item.is_running)

    @property
    def containers_stopped(self) -> int:
        """Return the number of containers that are not running."""
        return len(self.containers) - self.containers_running

    @property
    def containers_total(self) -> int:
        """Return the total number of containers."""
        return len(self.containers)

    @property
    def containers_with_update(self) -> int:
        """Return the number of containers running an outdated image."""
        return sum(1 for item in self.containers.values() if item.update_available)

    @property
    def projects_running(self) -> int:
        """Return the number of projects with at least one running service."""
        return sum(1 for item in self.projects.values() if item.is_running)

    @property
    def projects_total(self) -> int:
        """Return the total number of projects."""
        return len(self.projects)

    @property
    def containers_unhealthy(self) -> int:
        """Return the number of containers reporting an unhealthy check."""
        return sum(1 for item in self.containers.values() if item.is_healthy is False)

    def project_id_for(self, name: str | None) -> str | None:
        """Return the ID of the project with this compose name, if tracked."""
        if not name:
            return None
        for project in self.projects.values():
            if project.name == name:
                return project.id
        return None


@dataclass(slots=True)
class ArcaneData:
    """The full snapshot handed to the entities on every refresh."""

    version: str | None
    environments: dict[str, ArcaneEnvironment] = field(default_factory=dict)
