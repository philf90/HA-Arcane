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
    LOCAL_ENVIRONMENT_ID,
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


#: Host addresses that mean "every address". Docker lists a port published on
#: all addresses once for IPv4 and once for IPv6.
_ANY_ADDRESS = frozenset({"", "0.0.0.0", "::"})


@dataclass(frozen=True, slots=True)
class ArcanePort:
    """A port of a container, published on the host or only exposed."""

    container_port: int
    protocol: str
    #: Port on the host; None when the port is only exposed, not published.
    host_port: int | None = None
    #: Host address the port is bound to; None for every address.
    host_ip: str | None = None

    @property
    def published(self) -> bool:
        """Return whether the port is reachable on the host."""
        return self.host_port is not None

    @property
    def host(self) -> str | None:
        """Return the host side as Docker writes it, e.g. 127.0.0.1:8080."""
        if self.host_port is None:
            return None
        if self.host_ip is None:
            return str(self.host_port)
        address = f"[{self.host_ip}]" if ":" in self.host_ip else self.host_ip
        return f"{address}:{self.host_port}"

    def __str__(self) -> str:
        """Return the mapping as host→container/protocol, e.g. 8080→80/tcp."""
        target = f"{self.container_port}/{self.protocol}"
        return target if self.host is None else f"{self.host}→{target}"


def _port_number(value: Any) -> int | None:
    """Return a port number from Arcane, or None for anything else."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 65536:
        return value
    return None


def _ports(payload: Any) -> tuple[ArcanePort, ...]:
    """Read the ports of an Arcane container summary.

    Each entry carries ``privatePort``, ``type`` and, once published,
    ``publicPort`` and ``ip`` (types/container.go ContainerPort). The same
    port published on every IPv4 and every IPv6 address becomes one entry.
    """
    ports: set[ArcanePort] = set()
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        if (container_port := _port_number(item.get("privatePort"))) is None:
            continue
        protocol = _version(item.get("type")) or "tcp"
        host_port = _port_number(item.get("publicPort"))
        address = _version(item.get("ip")) or ""
        ports.add(
            ArcanePort(
                container_port=container_port,
                protocol=protocol,
                host_port=host_port,
                host_ip=(
                    None if host_port is None or address in _ANY_ADDRESS else address
                ),
            )
        )
    return tuple(
        sorted(
            ports,
            key=lambda port: (
                port.host_port is None,
                port.host_port or 0,
                port.container_port,
                port.protocol,
                port.host_ip or "",
            ),
        )
    )


def _counter(value: Any) -> int | None:
    """Return a non-negative whole number from Arcane, or None for anything else."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


@dataclass(frozen=True, slots=True)
class ArcaneResourceSample:
    """CPU, memory and I/O of a running container, as Arcane sampled them.

    Arcane takes the CPU share against its previous sample of the container,
    kept up to a minute (container/children/stats/service.go), so it is the
    mean since the last refresh rather than a single moment.
    """

    cpu_percent: float | None = None
    memory_usage: int | None = None
    memory_limit: int | None = None
    network_rx: int | None = None
    network_tx: int | None = None
    block_read: int | None = None
    block_write: int | None = None
    pids: int | None = None

    @classmethod
    def from_api(cls, payload: Any) -> ArcaneResourceSample | None:
        """Read ``resourceSample`` of a container summary; None without one."""
        if not isinstance(payload, dict):
            return None
        cpu = payload.get("cpuPercent")
        return cls(
            cpu_percent=(
                float(cpu)
                if isinstance(cpu, (int, float)) and not isinstance(cpu, bool)
                else None
            ),
            memory_usage=_counter(payload.get("memoryUsageBytes")),
            memory_limit=_counter(payload.get("memoryLimitBytes")),
            network_rx=_counter(payload.get("networkRxBytes")),
            network_tx=_counter(payload.get("networkTxBytes")),
            block_read=_counter(payload.get("blockReadBytes")),
            block_write=_counter(payload.get("blockWriteBytes")),
            pids=_counter(payload.get("pids")),
        )


def _volume_names(payload: Any) -> tuple[str, ...]:
    """Return the names of the named volumes a container mounts.

    Bind mounts and tmpfs have no volume and no size Arcane could report.
    """
    names: list[str] = []
    for mount in payload if isinstance(payload, list) else []:
        if (
            isinstance(mount, dict)
            and mount.get("type") == "volume"
            and (name := _version(mount.get("name")))
            and name not in names
        ):
            names.append(name)
    return tuple(names)


@dataclass(frozen=True, slots=True)
class ArcaneVolumeSize:
    """The disk space of one volume, as Docker measured it."""

    name: str
    size: int | None
    ref_count: int | None

    @classmethod
    def from_api(cls, payload: Any) -> ArcaneVolumeSize | None:
        """Read one entry of ``volumes/sizes``; None without a name.

        Docker reports -1 when it could not measure a volume, which is kept as
        unknown instead of a negative size.
        """
        if not isinstance(payload, dict) or not (name := _version(payload.get("name"))):
            return None
        return cls(
            name=name,
            size=_counter(payload.get("size")),
            ref_count=_counter(payload.get("refCount")),
        )


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
    #: Published and exposed ports, published ones first by host port.
    ports: tuple[ArcanePort, ...] = ()
    #: CPU and memory with the option for them; None when Arcane sent no
    #: sample, which it never does for a container that is not running.
    resources: ArcaneResourceSample | None = None
    #: Names of the volumes the container mounts.
    volume_names: tuple[str, ...] = ()

    @property
    def published_ports(self) -> tuple[ArcanePort, ...]:
        """Return the ports reachable on the host."""
        return tuple(port for port in self.ports if port.published)

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
            ports=_ports(payload.get("ports")),
            resources=ArcaneResourceSample.from_api(payload.get("resourceSample")),
            volume_names=_volume_names(payload.get("mounts")),
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
class ArcaneAgent:
    """Connection and host of the Arcane agent behind a remote environment.

    Arcane fills these fields in its environment list (``GET agents``) from
    the agent's tunnel, so they cost no request of their own. While the agent
    is disconnected only ``last_seen`` and the last disconnect remain.
    """

    connected: bool = False
    connected_at: datetime | None = None
    last_heartbeat: datetime | None = None
    last_seen: datetime | None = None
    round_trip_ms: float | None = None
    transport: str | None = None
    security_mode: str | None = None
    hostname: str | None = None
    os: str | None = None
    kernel_version: str | None = None
    architecture: str | None = None
    docker_version: str | None = None
    agent_version: str | None = None
    cpus: int | None = None
    memory_bytes: int | None = None
    last_disconnect_at: datetime | None = None
    last_disconnect_reason: str | None = None
    #: When the agent was last heard from: its last heartbeat while the tunnel
    #: is up, else ``last_seen``. Arcane writes ``last_seen`` at most every
    #: 30 s (environment/service.go UpdateEnvironmentHeartbeat), so right
    #: after a disconnect it can lie before the last heartbeat; the
    #: coordinator keeps the later value (``keep_later_contact``).
    last_contact: datetime | None = None

    def __post_init__(self) -> None:
        """Derive the last contact unless it was given."""
        if self.last_contact is None:
            self.last_contact = self.last_heartbeat or self.last_seen

    def keep_later_contact(self, previous: ArcaneAgent | None) -> None:
        """Never let the last contact go back behind one already reported."""
        if (
            previous is not None
            and previous.last_contact is not None
            and (self.last_contact is None or previous.last_contact > self.last_contact)
        ):
            self.last_contact = previous.last_contact

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> ArcaneAgent:
        """Create the agent part of an entry of Arcane's environment list."""
        host = payload.get("agentHost")
        host = host if isinstance(host, dict) else {}
        stats = payload.get("agentTunnelStats")
        stats = stats if isinstance(stats, dict) else {}
        round_trip = payload.get("agentRoundTripMs")

        def whole(value: Any) -> int | None:
            return (
                value
                if isinstance(value, int) and not isinstance(value, bool)
                else None
            )

        return cls(
            connected=payload.get("connected") is True,
            connected_at=_timestamp(payload.get("connectedAt")),
            last_heartbeat=_timestamp(payload.get("lastHeartbeat")),
            last_seen=_timestamp(payload.get("lastSeen")),
            round_trip_ms=(
                float(round_trip)
                if isinstance(round_trip, (int, float))
                and not isinstance(round_trip, bool)
                else None
            ),
            transport=_text(payload.get("agentTransport"))
            or _text(payload.get("lastAgentTransport")),
            security_mode=_text(payload.get("agentSecurityMode")),
            hostname=_text(host.get("hostname")),
            os=_text(host.get("os")),
            kernel_version=_text(host.get("kernelVersion")),
            architecture=_text(host.get("architecture")),
            docker_version=_text(host.get("dockerVersion")),
            agent_version=_text(host.get("agentVersion")),
            cpus=whole(host.get("cpus")),
            memory_bytes=whole(host.get("memoryBytes")),
            last_disconnect_at=_timestamp(stats.get("lastDisconnectAt")),
            last_disconnect_reason=_text(stats.get("lastDisconnectReason")),
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
    #: Size of every volume by name, with the option; empty until the first
    #: measurement is in. Fetched in the background with the disk usage report.
    volume_sizes: dict[str, ArcaneVolumeSize] = field(default_factory=dict)
    #: Arcane's background jobs by ID, with the option for them. Jobs Arcane
    #: lists below another one, such as one per GitOps sync, are left out.
    jobs: dict[str, ArcaneJob] = field(default_factory=dict)
    #: The Arcane agent serving a remote environment; None for the local one.
    agent: ArcaneAgent | None = None

    @property
    def is_remote(self) -> bool:
        """Return whether an Arcane agent on another host serves the environment."""
        return self.id != LOCAL_ENVIRONMENT_ID

    @property
    def is_online(self) -> bool:
        """Return whether Arcane considers the environment online."""
        return self.reachable and self.status == ENVIRONMENT_STATUS_ONLINE

    @property
    def agent_disconnected(self) -> bool:
        """Return whether a remote environment's agent is not connected now.

        Arcane reports a remote environment ``online`` exactly while the
        agent's tunnel is up (environment/lister.go ApplyRuntimeState).
        """
        return self.is_remote and self.status != ENVIRONMENT_STATUS_ONLINE

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

    def container_volume_size(self, container: ArcaneContainer) -> int | None:
        """Return the disk space of the volumes a container mounts.

        None until the volumes have been measured, and when none of the
        container's volumes could be; a volume Docker did not measure (another
        driver, or a size of -1) counts as unknown and is left out of the sum.
        A container without volumes takes no space in them.
        """
        if not self.volume_sizes:
            return None
        sizes = [
            item.size
            for name in container.volume_names
            if (item := self.volume_sizes.get(name)) is not None
            and item.size is not None
        ]
        if container.volume_names and not sizes:
            return None
        return sum(sizes)

    @property
    def volumes_size_total(self) -> int | None:
        """Return the disk space of all measured volumes; None before a report."""
        if not self.volume_sizes:
            return None
        return sum(item.size or 0 for item in self.volume_sizes.values())

    @property
    def host_ports(self) -> list[tuple[ArcanePort, str]]:
        """Return every published port with the container holding it.

        Docker only lists the ports of running containers as published, so
        this is what is bound on the host right now, sorted by host port.
        """
        return sorted(
            (
                (port, container.name)
                for container in self.containers.values()
                for port in container.published_ports
            ),
            key=lambda item: (
                item[0].host_port or 0,
                item[0].protocol,
                item[0].host_ip or "",
                item[1],
            ),
        )

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
