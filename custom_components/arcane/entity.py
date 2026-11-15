"""Base entities for the Arcane integration."""

from __future__ import annotations

from homeassistant.const import CONF_URL
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ENTITY_PREFIX,
    CONF_NEST_CONTAINERS,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import ArcaneCoordinator
from .models import ArcaneContainer, ArcaneEnvironment, ArcaneProject


def _find_device(
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


class ArcaneEntity(CoordinatorEntity[ArcaneCoordinator]):
    """Common behaviour of every Arcane entity."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ArcaneCoordinator, description: EntityDescription
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description

    @property
    def _configuration_url(self) -> str:
        """Return the URL of the Arcane instance backing this entity."""
        return str(self.coordinator.config_entry.data[CONF_URL]).rstrip("/")

    def _parent_identifier(self) -> str | None:
        """Return the identifier of the device this entity's device hangs below."""
        return None

    async def async_added_to_hass(self) -> None:
        """Link the device to its parent once Home Assistant has created it."""
        await super().async_added_to_hass()
        self._async_link_parent_device()

    @callback
    def _async_link_parent_device(self) -> None:
        """Point the device at its parent device.

        Home Assistant 2026.11 deprecates ``via_device`` in the device info and
        drops it in 2027.8. Its replacement, ``via_device_id``, is the parent's
        registry id, which the device info of 2026.2 does not accept. Updating
        the device after it was created works on both.
        """
        if self.device_entry is None or (parent := self._parent_identifier()) is None:
            return
        registry = dr.async_get(self.hass)
        parent_device = _find_device(
            registry, parent, self.coordinator.config_entry.entry_id
        )
        device = registry.async_get(self.device_entry.id)
        if (
            parent_device is None
            or device is None
            or parent_device.id == device.id
            or device.via_device_id == parent_device.id
        ):
            return
        registry.async_update_device(device.id, via_device_id=parent_device.id)

    def _device_name(self, kind: str, name: str) -> str:
        """Return the device name, prefixed with its kind when asked for.

        A container and a compose project regularly carry the same name. Home
        Assistant builds entity IDs from the device name, so without the prefix
        the second device of that name only gets ``_2`` suffixed entity IDs.
        """
        if not self.coordinator.option(CONF_ENTITY_PREFIX):
            return name
        return f"{kind} {name}"


class ArcaneEnvironmentEntity(ArcaneEntity):
    """An entity describing an Arcane environment as a whole."""

    def __init__(
        self,
        coordinator: ArcaneCoordinator,
        environment_id: str,
        description: EntityDescription,
    ) -> None:
        """Initialize the environment entity."""
        super().__init__(coordinator, description)
        self.environment_id = environment_id
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{environment_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{environment_id}")},
            manufacturer=MANUFACTURER,
            model="Environment",
            name=self.environment.name if self.environment else environment_id,
            sw_version=coordinator.data.version,
            configuration_url=self._configuration_url,
        )

    @property
    def environment(self) -> ArcaneEnvironment | None:
        """Return the environment this entity belongs to."""
        return self.coordinator.data.environments.get(self.environment_id)

    @property
    def available(self) -> bool:
        """Return whether the environment is still known to Arcane."""
        return super().available and self.environment is not None


class ArcaneContainerEntity(ArcaneEntity):
    """An entity describing a single container."""

    def __init__(
        self,
        coordinator: ArcaneCoordinator,
        environment_id: str,
        container_name: str,
        description: EntityDescription,
    ) -> None:
        """Initialize the container entity."""
        super().__init__(coordinator, description)
        self.environment_id = environment_id
        self.container_name = container_name
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = (
            f"{entry_id}_{environment_id}_container_{container_name}_{description.key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={
                (DOMAIN, f"{entry_id}_{environment_id}_container_{container_name}")
            },
            manufacturer=MANUFACTURER,
            model="Container",
            name=self._device_name("Container", container_name),
            configuration_url=f"{self._configuration_url}/containers",
        )

    def _parent_identifier(self) -> str:
        """Return the device this container hangs below.

        Containers of a compose project are nested under the project device so
        the device page shows the stack it belongs to; everything else hangs
        directly below its environment.
        """
        entry_id = self.coordinator.config_entry.entry_id
        environment_device = f"{entry_id}_{self.environment_id}"
        if not self.coordinator.option(CONF_NEST_CONTAINERS):
            return environment_device

        container = self.container
        environment = self.coordinator.data.environments.get(self.environment_id)
        if container is None or environment is None:
            return environment_device
        project_id = environment.project_id_for(container.project)
        if project_id is None:
            return environment_device
        return f"{environment_device}_project_{project_id}"

    @property
    def container(self) -> ArcaneContainer | None:
        """Return the container this entity belongs to."""
        environment = self.coordinator.data.environments.get(self.environment_id)
        if environment is None:
            return None
        return environment.containers.get(self.container_name)

    @property
    def available(self) -> bool:
        """Return whether the container still exists."""
        return super().available and self.container is not None


class ArcaneProjectEntity(ArcaneEntity):
    """An entity describing a compose project."""

    def __init__(
        self,
        coordinator: ArcaneCoordinator,
        environment_id: str,
        project_id: str,
        description: EntityDescription,
    ) -> None:
        """Initialize the project entity."""
        super().__init__(coordinator, description)
        self.environment_id = environment_id
        self.project_id = project_id
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = (
            f"{entry_id}_{environment_id}_project_{project_id}_{description.key}"
        )
        project = self.project
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{environment_id}_project_{project_id}")},
            manufacturer=MANUFACTURER,
            model="Compose project",
            name=self._device_name("Project", project.name if project else project_id),
            configuration_url=f"{self._configuration_url}/projects/{project_id}",
        )

    def _parent_identifier(self) -> str:
        """Return the environment device a project hangs below."""
        return f"{self.coordinator.config_entry.entry_id}_{self.environment_id}"

    @property
    def project(self) -> ArcaneProject | None:
        """Return the project this entity belongs to."""
        environment = self.coordinator.data.environments.get(self.environment_id)
        if environment is None:
            return None
        return environment.projects.get(self.project_id)

    @property
    def available(self) -> bool:
        """Return whether the project still exists."""
        return super().available and self.project is not None
