"""Shared platform setup helpers."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    async_get_current_platform,
)

from .api import ArcaneError, ArcanePermissionError
from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import ArcaneCoordinator

_LOGGER = logging.getLogger(__name__)


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


def description_keys(namespace: Mapping[str, Any]) -> frozenset[str]:
    """Return the keys of every entity description a platform module defines.

    Descriptions stand alone or in tuples at module level; the keys are what
    the stale entity cleanup looks for. Home Assistant builds descriptions
    such as ``ButtonEntityDescription(...)`` from a compatibility class that
    ``isinstance`` does not match against ``EntityDescription``, so the class
    hierarchy is checked by name.
    """
    keys: set[str] = set()
    for value in namespace.values():
        for item in value if isinstance(value, (tuple, list)) else (value,):
            if isinstance(item, EntityDescription) or any(
                cls.__name__ == "EntityDescription" for cls in type(item).__mro__
            ):
                keys.add(item.key)
    return frozenset(keys)


def async_setup_entities(
    coordinator: ArcaneCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    *,
    stale_keys: Iterable[str] = (),
    environments: EnvironmentFactory | None = None,
    containers: ContainerFactory | None = None,
    projects: ProjectFactory | None = None,
    jobs: JobFactory | None = None,
) -> None:
    """Add entities now and whenever Arcane reports new resources.

    Containers, projects and jobs come and go while Home Assistant runs, so
    the platforms keep listening instead of only reading the first snapshot.

    The first time a resource shows up after loading, entities of this
    platform that the registry holds for it but that the current options no
    longer create are removed: their ``unique_id`` is the resource's prefix
    plus one of ``stale_keys``, and no entity with it was just created. A
    platform that offers no entities of a kind at all removes that kind's
    entities in every environment Arcane reports. Resources Arcane does not
    report right now, such as the containers of a project that is down, keep
    their entities.
    """
    known: set[tuple[str, ...]] = set()
    keys = frozenset(stale_keys)
    domain = async_get_current_platform().domain if keys else ""
    entry_id = coordinator.config_entry.entry_id
    registry = er.async_get(coordinator.hass) if keys else None

    def _remove(entity_id: str) -> None:
        assert registry is not None
        _LOGGER.debug("Removing %s: the options no longer create it", entity_id)
        registry.async_remove(entity_id)

    def _clean(prefix: str, created: list[Entity]) -> None:
        """Remove what the registry holds for one resource beyond ``created``."""
        if registry is None:
            return
        made = {entity.unique_id for entity in created}
        for key in keys:
            unique_id = f"{prefix}{key}"
            if unique_id in made:
                continue
            if entity_id := registry.async_get_entity_id(domain, DOMAIN, unique_id):
                _remove(entity_id)

    def _sweep(prefix: str) -> None:
        """Remove every entity of a kind this platform no longer offers."""
        if registry is None:
            return
        for entry in er.async_entries_for_config_entry(registry, entry_id):
            if (
                entry.domain == domain
                and entry.unique_id.startswith(prefix)
                and any(entry.unique_id.endswith(f"_{key}") for key in keys)
            ):
                _remove(entry.entity_id)

    def _async_add_new() -> None:
        new_entities: list[Entity] = []
        for environment_id, environment in coordinator.data.environments.items():
            base = f"{entry_id}_{environment_id}_"
            key = ("environment", environment_id)
            if key not in known:
                known.add(key)
                created = environments(environment_id) if environments else []
                _clean(base, created)
                new_entities.extend(created)
                # Kinds this platform does not offer at all, once per
                # environment.
                for kind, factory in (
                    ("container", containers),
                    ("project", projects),
                    ("job", jobs),
                ):
                    if factory is None:
                        _sweep(f"{base}{kind}_")
            # Projects come before containers so a project device already
            # exists when a container of that project points at it.
            if projects is not None:
                for project_id in environment.projects:
                    key = ("project", environment_id, project_id)
                    if key not in known:
                        known.add(key)
                        created = projects(environment_id, project_id)
                        _clean(f"{base}project_{project_id}_", created)
                        new_entities.extend(created)
            if containers is not None:
                for name in environment.containers:
                    key = ("container", environment_id, name)
                    if key not in known:
                        known.add(key)
                        created = containers(environment_id, name)
                        _clean(f"{base}container_{name}_", created)
                        new_entities.extend(created)
            if jobs is not None:
                for job_id in environment.jobs:
                    key = ("job", environment_id, job_id)
                    if key not in known:
                        known.add(key)
                        created = jobs(environment_id, job_id)
                        _clean(f"{base}job_{job_id}_", created)
                        new_entities.extend(created)
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
