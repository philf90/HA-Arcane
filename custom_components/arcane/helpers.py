"""Shared platform setup helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ArcaneError, ArcanePermissionError
from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import ArcaneCoordinator


def find_device(
    registry: dr.DeviceRegistry, identifier: str, config_entry_id: str
) -> dr.DeviceEntry | None:
    """Return the device of this config entry with the given identifier.

    Home Assistant 2026.11 deprecates ``async_get_device`` in favour of
    ``async_get_device_by_identifier``, which older releases lack. The newer
    lookup is used where it exists, so neither version reports a deprecation.
    """
    lookup = getattr(registry, "async_get_device_by_identifier", None)
    if lookup is not None:
        return lookup((DOMAIN, identifier), config_entry_id)
    return registry.async_get_device(identifiers={(DOMAIN, identifier)})


type EnvironmentFactory = Callable[[str], list[Entity]]
type ContainerFactory = Callable[[str, str], list[Entity]]
type ProjectFactory = Callable[[str, str], list[Entity]]
type JobFactory = Callable[[str, str], list[Entity]]


def async_setup_entities(
    coordinator: ArcaneCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    *,
    environments: EnvironmentFactory | None = None,
    containers: ContainerFactory | None = None,
    projects: ProjectFactory | None = None,
    jobs: JobFactory | None = None,
) -> None:
    """Add entities now and whenever Arcane reports new resources.

    Containers, projects and jobs come and go while Home Assistant runs, so
    the platforms keep listening instead of only reading the first snapshot.
    """
    known: set[tuple[str, ...]] = set()

    def _async_add_new() -> None:
        new_entities: list[Entity] = []
        for environment_id, environment in coordinator.data.environments.items():
            if environments is not None:
                key = ("environment", environment_id)
                if key not in known:
                    known.add(key)
                    new_entities.extend(environments(environment_id))
            # Projects come before containers so a project device already
            # exists when a container of that project points at it.
            if projects is not None:
                for project_id in environment.projects:
                    key = ("project", environment_id, project_id)
                    if key not in known:
                        known.add(key)
                        new_entities.extend(projects(environment_id, project_id))
            if containers is not None:
                for name in environment.containers:
                    key = ("container", environment_id, name)
                    if key not in known:
                        known.add(key)
                        new_entities.extend(containers(environment_id, name))
            if jobs is not None:
                for job_id in environment.jobs:
                    key = ("job", environment_id, job_id)
                    if key not in known:
                        known.add(key)
                        new_entities.extend(jobs(environment_id, job_id))
        if new_entities:
            async_add_entities(new_entities)

    _async_add_new()
    coordinator.config_entry.async_on_unload(
        coordinator.async_add_listener(_async_add_new)
    )


def action_error(
    coordinator: ArcaneCoordinator,
    err: ArcaneError,
    *,
    permission: str,
    action_key: str,
    placeholders: dict[str, str],
) -> HomeAssistantError:
    """Return the error to raise for an action that did not go through.

    An action Arcane refused is reported with the permission the API key is
    missing, rather than with a bare 403, and is remembered so the repair issue
    lists it next to the permissions the polling is missing. Every action has a
    ``<action_key>_failed`` and a ``<action_key>_forbidden`` message.
    """
    if isinstance(err, ArcanePermissionError):
        coordinator.async_note_denied(permission)
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=f"{action_key}_forbidden",
            translation_placeholders={**placeholders, "permission": permission},
        )
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=f"{action_key}_failed",
        translation_placeholders={**placeholders, "error": str(err)},
    )
