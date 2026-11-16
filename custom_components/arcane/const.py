"""Constants for the Arcane integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "arcane"

MANUFACTURER: Final = "Arcane"

DEFAULT_TIMEOUT: Final = 30

# Options
CONF_ENVIRONMENTS: Final = "environments"
CONF_MONITOR_CONTAINERS: Final = "monitor_containers"
CONF_MONITOR_PROJECTS: Final = "monitor_projects"
CONF_MONITOR_RESOURCES: Final = "monitor_resources"
CONF_INCLUDE_INTERNAL: Final = "include_internal"
CONF_INCLUDE_HIDDEN: Final = "include_hidden"
CONF_ALLOW_CONTROL: Final = "allow_control"
CONF_UPDATE_ENTITIES: Final = "update_entities"
CONF_HOST_STATS: Final = "host_stats"
CONF_HEALTH_SENSORS: Final = "health_sensors"
CONF_ENTITY_PREFIX: Final = "entity_prefix"
CONF_NEST_CONTAINERS: Final = "nest_containers"
CONF_EVENTS: Final = "events"
CONF_REDEPLOY_BUTTONS: Final = "redeploy_buttons"
CONF_PRUNE_BUTTON: Final = "prune_button"
CONF_DISK_USAGE: Final = "disk_usage"
CONF_INSTANT_UPDATES: Final = "instant_updates"
CONF_ARCANE_EVENTS: Final = "arcane_events"
CONF_AUTO_UPDATE_SWITCHES: Final = "auto_update_switches"
CONF_UPDATER: Final = "updater"
CONF_JOBS: Final = "jobs"

# Values of the prune button option. Containers and volumes are deliberately
# never pruned: both destroy data that cannot be pulled back.
PRUNE_OFF: Final = "off"
PRUNE_DANGLING: Final = "dangling"
PRUNE_UNUSED: Final = "unused"
PRUNE_MODES: Final = [PRUNE_OFF, PRUNE_DANGLING, PRUNE_UNUSED]

DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 10
MAX_SCAN_INTERVAL: Final = 3600

# Reclaimable disk space is asked for at most this often, in seconds. Arcane
# answers it with a Docker disk usage report that sizes every volume, which can
# take a while on a host with large volumes, and the figures change slowly.
DISK_USAGE_INTERVAL: Final = 900

# Arcane holds a request to ``events/changes`` open until something changes in
# the environment, for at most 55 seconds (60 s proxy timeout minus 5 s). The
# integration asks for a little less, so Arcane always answers before the
# request times out on this side.
CHANGES_WAIT: Final = 50
# Seconds to wait before asking again after a failed request, doubled on every
# further failure up to the maximum.
CHANGES_RETRY_MIN: Final = 5
CHANGES_RETRY_MAX: Final = 300
# A reply that comes back sooner than this without news is followed by a pause
# of that length, so a misbehaving server cannot make the loop spin.
CHANGES_MIN_LOOP: Final = 1.0
# Kinds of change that do not show up in any entity. Arcane reports the progress
# of background activities as changes too, many per deploy.
CHANGES_IGNORED_KINDS: Final = frozenset({"activity"})
# Arcane events fetched per refresh and environment; Arcane's maximum page size.
ARCANE_EVENTS_PAGE: Final = 100
# Event IDs remembered per environment, so an event is never announced twice.
ARCANE_EVENTS_SEEN: Final = 1000

DEFAULT_OPTIONS: Final[dict[str, object]] = {
    "scan_interval": DEFAULT_SCAN_INTERVAL,
    CONF_ENVIRONMENTS: [],
    CONF_MONITOR_CONTAINERS: True,
    CONF_MONITOR_PROJECTS: True,
    CONF_MONITOR_RESOURCES: True,
    CONF_INCLUDE_INTERNAL: False,
    CONF_INCLUDE_HIDDEN: False,
    CONF_ALLOW_CONTROL: True,
    CONF_UPDATE_ENTITIES: True,
    CONF_HOST_STATS: False,
    CONF_HEALTH_SENSORS: True,
    CONF_ENTITY_PREFIX: True,
    CONF_NEST_CONTAINERS: True,
    CONF_EVENTS: True,
    CONF_REDEPLOY_BUTTONS: False,
    CONF_PRUNE_BUTTON: PRUNE_OFF,
    CONF_DISK_USAGE: False,
    CONF_INSTANT_UPDATES: False,
    CONF_ARCANE_EVENTS: False,
    CONF_AUTO_UPDATE_SWITCHES: False,
    CONF_UPDATER: False,
    CONF_JOBS: False,
}

# Reserved ID of the environment Arcane manages directly.
LOCAL_ENVIRONMENT_ID: Final = "0"

# Container states reported by the Docker daemon.
STATE_RUNNING: Final = "running"
STATE_PAUSED: Final = "paused"

CONTAINER_STATES: Final = [
    "created",
    "dead",
    "exited",
    "paused",
    "removing",
    "restarting",
    "running",
]

# Project (compose stack) states reported by Arcane.
PROJECT_STATE_RUNNING: Final = "running"
PROJECT_STATE_PARTIALLY_RUNNING: Final = "partially running"

PROJECT_STATES: Final = [
    "deploying",
    "partially_running",
    "restarting",
    "running",
    "stopped",
    "stopping",
    "unknown",
]

ENVIRONMENT_STATUS_ONLINE: Final = "online"

# Health as Docker reports it in the status line of a container.
HEALTH_HEALTHY: Final = "healthy"
HEALTH_UNHEALTHY: Final = "unhealthy"
HEALTH_STARTING: Final = "starting"

# Events fired on the Home Assistant bus.
EVENT_CONTAINER_STATE: Final = "arcane_container_state_changed"
EVENT_CONTAINER_HEALTH: Final = "arcane_container_health_changed"
EVENT_PROJECT_STATE: Final = "arcane_project_state_changed"
# Every new entry of Arcane's own event log, with the option of the same name.
EVENT_ARCANE: Final = "arcane_event"

# Exit code Docker reports when the kernel OOM killer or a SIGKILL ended the
# main process. It is the usual fingerprint of an out of memory kill.
EXIT_CODE_SIGKILL: Final = 137

# Arcane's update type for an image whose newer version is a different tag.
# Only then does the newer version name a tag the release notes can be found
# under; otherwise the digest identifies the newer image.
UPDATE_TYPE_TAG: Final = "tag"
# Arcane's update type for an image that is referenced but not pulled yet.
UPDATE_TYPE_NOT_PULLED: Final = "not_pulled"

# Statuses of Arcane's updater for a resource it brought onto the newer image.
UPDATER_APPLIED_STATUSES: Final = frozenset({"updated", "restarted"})
# Label with which a container opts out of Arcane's updater; matched without
# regard to case, with the values Arcane reads as false (kit.ParseBool).
LABEL_UPDATER: Final = "com.getarcaneapp.arcane.updater"
LABEL_FALSE_VALUES: Final = frozenset({"false", "0", "no", "off"})

# Outcomes of a finished run of one of Arcane's background jobs, as the job
# list reports the last one. Arcane shows a run that needs attention as failed.
JOB_RUN_STATUSES: Final = ["succeeded", "partial", "skipped", "failed", "canceled"]

# Arcane 3 runs a prune as a background activity and answers before it starts
# deleting. The prune button follows the activity until it is over, asking
# every ACTIVITY_POLL_INTERVAL seconds for at most PRUNE_WAIT seconds, so the
# disk usage report and the counts it asks for afterwards show the result.
ACTIVITY_POLL_INTERVAL: Final = 2.0
PRUNE_WAIT: Final = 900
ACTIVITY_SUCCESS: Final = "success"
ACTIVITY_DONE_STATUSES: Final = frozenset({"success", "failed", "cancelled"})
