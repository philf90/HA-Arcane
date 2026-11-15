"""Switch platform for the Arcane integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ArcaneConfigEntry
from .api import ArcaneError
from .const import (
    CONF_ALLOW_CONTROL,
    CONF_AUTO_UPDATE_SWITCHES,
    CONF_MONITOR_CONTAINERS,
    DOMAIN,
    LABEL_UPDATER,
)
from .entity import ArcaneContainerEntity, ArcaneProjectEntity
from .helpers import action_error, async_setup_entities
from .permissions import (
    CONTAINER_ACTION_PERMISSIONS,
    PERM_CONTAINERS_AUTOUPDATE,
    PROJECT_ACTION_PERMISSIONS,
)

PARALLEL_UPDATES = 1

# Both switches are the primary entity of their device, so they inherit the
# device name instead of carrying one of their own.
CONTAINER_SWITCH = SwitchEntityDescription(
    key="container",
    device_class=SwitchDeviceClass.SWITCH,
)

PROJECT_SWITCH = SwitchEntityDescription(
    key="project",
    device_class=SwitchDeviceClass.SWITCH,
)

AUTO_UPDATE_SWITCH = SwitchEntityDescription(
    key="auto_update",
    translation_key="auto_update",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArcaneConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Arcane switches."""
    coordinator = entry.runtime_data
    if not coordinator.option(CONF_ALLOW_CONTROL):
        return

    auto_update = coordinator.option(CONF_AUTO_UPDATE_SWITCHES) and coordinator.option(
        CONF_MONITOR_CONTAINERS
    )

    async_setup_entities(
        coordinator,
        async_add_entities,
        containers=lambda environment_id, name: [
            ArcaneContainerSwitch(coordinator, environment_id, name, CONTAINER_SWITCH),
            *(
                [
                    ArcaneAutoUpdateSwitch(
                        coordinator, environment_id, name, AUTO_UPDATE_SWITCH
                    )
                ]
                if auto_update
                else []
            ),
        ],
        projects=lambda environment_id, project_id: [
            ArcaneProjectSwitch(coordinator, environment_id, project_id, PROJECT_SWITCH)
        ],
    )


class ArcaneContainerSwitch(ArcaneContainerEntity, SwitchEntity):
    """Start and stop a container."""

    _attr_name = None

    @property
    def is_on(self) -> bool | None:
        """Return whether the container is running."""
        if (container := self.container) is None:
            return None
        return container.is_running

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start the container."""
        await self._async_action("start")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the container."""
        await self._async_action("stop")

    async def _async_action(self, action: str) -> None:
        """Run a container action and refresh the coordinator."""
        if (container := self.container) is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="container_not_found",
                translation_placeholders={"name": self.container_name},
            )
        try:
            await self.coordinator.client.async_container_action(
                self.environment_id, container.id, action
            )
        except ArcaneError as err:
            raise action_error(
                self.coordinator,
                err,
                permission=CONTAINER_ACTION_PERMISSIONS[action],
                action_key="container_action",
                placeholders={"action": action, "name": self.container_name},
            ) from err
        await self.coordinator.async_request_refresh()


class ArcaneProjectSwitch(ArcaneProjectEntity, SwitchEntity):
    """Deploy and stop a compose project."""

    _attr_name = None

    @property
    def is_on(self) -> bool | None:
        """Return whether at least one service of the project runs."""
        if (project := self.project) is None:
            return None
        return project.is_running

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Deploy the project."""
        await self._async_action("up")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the project."""
        await self._async_action("down")

    async def _async_action(self, action: str) -> None:
        """Run a project action and refresh the coordinator."""
        try:
            await self.coordinator.client.async_project_action(
                self.environment_id, self.project_id, action
            )
        except ArcaneError as err:
            raise action_error(
                self.coordinator,
                err,
                permission=PROJECT_ACTION_PERMISSIONS[action],
                action_key="project_action",
                placeholders={
                    "action": action,
                    "name": self.project.name if self.project else self.project_id,
                },
            ) from err
        await self.coordinator.async_request_refresh()


class ArcaneAutoUpdateSwitch(ArcaneContainerEntity, SwitchEntity):
    """Include a container in Arcane's automatic updates, or leave it out.

    Arcane keeps a list of containers its automatic updates skip, and this
    switch edits it. The automatic updates themselves only run when they are
    switched on in Arcane's settings. A container that opts out by label stays
    out whatever the list says.
    """

    @property
    def available(self) -> bool:
        """Return whether Arcane reports the setting for this container."""
        return (
            super().available
            and self.container is not None
            and self.container.auto_update_enabled is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Return whether Arcane's automatic updates include the container."""
        return self.container.auto_update_enabled if self.container else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Include the container in Arcane's automatic updates."""
        if self.container is not None and self.container.auto_update_label_disabled:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="auto_update_label",
                translation_placeholders={
                    "name": self.container_name,
                    "label": LABEL_UPDATER,
                },
            )
        await self._async_set(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Leave the container out of Arcane's automatic updates."""
        await self._async_set(enabled=False)

    async def _async_set(self, *, enabled: bool) -> None:
        if (container := self.container) is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="container_not_found",
                translation_placeholders={"name": self.container_name},
            )
        try:
            await self.coordinator.client.async_set_auto_update(
                self.environment_id, container.id, enabled=enabled
            )
        except ArcaneError as err:
            raise action_error(
                self.coordinator,
                err,
                permission=PERM_CONTAINERS_AUTOUPDATE,
                action_key="auto_update",
                placeholders={"name": self.container_name},
            ) from err
        await self.coordinator.async_request_refresh()
