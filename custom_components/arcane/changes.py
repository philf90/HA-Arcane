"""Refresh as soon as Arcane reports a change, instead of on the next poll.

Arcane keeps a revision per kind of resource and environment and lets a client
wait for the next one: ``GET agents/{id}/events/changes`` answers at once when
the client's revisions are behind, and otherwise holds the request until
something changes, for at most 55 seconds. One background task per
environment keeps such a request open and asks the coordinator for a refresh
whenever a revision moves. The regular polling stays as it is; this only
shortens the wait for a change.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from .api import ArcaneError, ArcaneNotFoundError, ArcanePermissionError
from .const import (
    CHANGES_IGNORED_KINDS,
    CHANGES_MIN_LOOP,
    CHANGES_RETRY_MAX,
    CHANGES_RETRY_MIN,
    CHANGES_WAIT,
    DOMAIN,
)
from .permissions import PERM_EVENTS_LIST

if TYPE_CHECKING:
    from .coordinator import ArcaneCoordinator

_LOGGER = logging.getLogger(__name__)

# What Arcane reports when its revisions were reset, for example by a restart.
KIND_RESTARTED = "all"


async def _pause(seconds: float) -> None:
    """Wait between requests; its own function so tests can skip the wait."""
    await asyncio.sleep(seconds)


@dataclass(slots=True)
class WatchState:
    """What the watch of one environment has seen, for the diagnostics."""

    epoch: str | None = None
    since: int = 0
    refreshes: int = 0
    last_change: datetime | None = None
    last_kinds: list[str] = field(default_factory=list)
    error: str | None = None
    stopped: bool = False


def _revisions(changes: dict[str, Any]) -> dict[str, int]:
    """Return the revision of each kind in an ``events/changes`` reply."""
    kinds = changes.get("kinds")
    if not isinstance(kinds, dict):
        return {}
    return {
        str(kind): revision
        for kind, revision in kinds.items()
        if isinstance(revision, int) and not isinstance(revision, bool)
    }


class ArcaneChangeWatcher:
    """Keep one ``events/changes`` request open per environment."""

    def __init__(self, coordinator: ArcaneCoordinator) -> None:
        """Initialize the watcher; nothing runs until environments are given."""
        self._coordinator = coordinator
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self.states: dict[str, WatchState] = {}

    @callback
    def async_sync(self, environment_ids: set[str]) -> None:
        """Watch exactly these environments, starting and stopping as needed."""
        for environment_id in list(self._tasks):
            if environment_id not in environment_ids:
                self._tasks.pop(environment_id).cancel()
                self.states.pop(environment_id, None)
        coordinator = self._coordinator
        for environment_id in sorted(environment_ids - set(self._tasks)):
            self.states[environment_id] = WatchState()
            # A background task of the entry: it is cancelled when the entry
            # unloads, and Home Assistant does not wait for it at startup.
            self._tasks[environment_id] = (
                coordinator.config_entry.async_create_background_task(
                    coordinator.hass,
                    self._async_watch(environment_id),
                    f"{DOMAIN} changes {environment_id}",
                )
            )

    def diagnostics(self) -> dict[str, Any]:
        """Return the state of every watch."""
        return {
            environment_id: {
                **asdict(state),
                "last_change": state.last_change.isoformat()
                if state.last_change
                else None,
            }
            for environment_id, state in self.states.items()
        }

    async def _async_watch(self, environment_id: str) -> None:
        """Wait for changes in one environment until the entry unloads."""
        state = self.states[environment_id]
        client = self._coordinator.client
        delay = CHANGES_RETRY_MIN
        while True:
            started = time.monotonic()
            try:
                changes = await client.async_wait_for_changes(
                    environment_id,
                    epoch=state.epoch,
                    since=state.since,
                    wait=CHANGES_WAIT,
                )
                epoch = changes.get("epoch")
                if not isinstance(epoch, str) or not epoch:
                    # Without an epoch every request would come back at once.
                    raise ArcaneError("Arcane sent changes without an epoch")
            except ArcanePermissionError:
                # Asking again would only be refused again; the repair names
                # the permission, and a reload starts the watch afresh.
                self._coordinator.async_note_denied(PERM_EVENTS_LIST)
                state.error = f"missing permission {PERM_EVENTS_LIST}"
                state.stopped = True
                return
            except ArcaneNotFoundError as err:
                # An Arcane without the endpoint; polling still works.
                state.error = str(err)
                state.stopped = True
                return
            except ArcaneError as err:
                state.error = str(err)
                _LOGGER.debug(
                    "Waiting for changes in environment %s failed, retrying in "
                    "%s s: %s",
                    environment_id,
                    delay,
                    err,
                )
                await _pause(delay)
                delay = min(delay * 2, CHANGES_RETRY_MAX)
                continue
            delay = CHANGES_RETRY_MIN
            state.error = None

            changed = self._changed(state, epoch, _revisions(changes))
            if changed:
                state.refreshes += 1
                state.last_change = dt_util.utcnow()
                state.last_kinds = sorted(changed)
                # Debounced by the coordinator: a burst of changes ends in one
                # refresh, and a refresh already running is not interrupted.
                await self._coordinator.async_request_refresh()
            elif time.monotonic() - started < CHANGES_MIN_LOOP:
                await _pause(CHANGES_MIN_LOOP)

    @staticmethod
    def _changed(state: WatchState, epoch: str, revisions: dict[str, int]) -> set[str]:
        """Remember a reply and return the kinds that warrant a refresh.

        The first reply only tells where Arcane stands. A new epoch means
        Arcane's revisions started over, so everything may have changed.
        """
        first = state.epoch is None
        restarted = not first and epoch != state.epoch
        changed = {
            kind for kind, revision in revisions.items() if revision > state.since
        }
        state.epoch = epoch
        state.since = max(revisions.values(), default=0)
        if first:
            return set()
        if restarted:
            return {KIND_RESTARTED}
        return changed - CHANGES_IGNORED_KINDS
