"""Update platform for the Arcane integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.update import (
    UpdateEntity,
    UpdateEntityDescription,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ArcaneConfigEntry
from .api import ArcaneError, ArcanePermissionError
from .const import (
    CONF_ALLOW_CONTROL,
    CONF_UPDATE_ENTITIES,
    DOMAIN,
    MANUFACTURER,
    UPDATE_TYPE_NOT_PULLED,
    UPDATE_TYPE_TAG,
    UPDATER_APPLIED_STATUSES,
)
from .entity import ArcaneContainerEntity, ArcaneEnvironmentEntity
from .helpers import action_error, async_setup_entities
from .models import ArcaneVersion, image_name
from .permissions import (
    PERM_IMAGE_UPDATES_APPLY,
    PERM_IMAGE_UPDATES_READ,
    PERM_SYSTEM_UPGRADE,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

# Home Assistant stores the release summary as a state attribute, which is
# capped at 255 characters.
MAX_RELEASE_SUMMARY = 255

CONTAINER_UPDATE = UpdateEntityDescription(
    key="image_update",
    translation_key="image_update",
)

ARCANE_UPDATE = UpdateEntityDescription(
    key="arcane_update",
    translation_key="arcane_update",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArcaneConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Arcane update entities."""
    coordinator = entry.runtime_data
    if not coordinator.option(CONF_UPDATE_ENTITIES):
        return

    async_setup_entities(
        coordinator,
        async_add_entities,
        environments=lambda environment_id: [
            ArcaneInstanceUpdate(coordinator, environment_id, ARCANE_UPDATE)
        ],
        containers=lambda environment_id, name: [
            ArcaneContainerUpdate(coordinator, environment_id, name, CONTAINER_UPDATE)
        ],
    )


def _summary(notes: str | None) -> str | None:
    """Return release notes short enough to live in a state attribute."""
    if not notes:
        return None
    text = notes.strip()
    if len(text) <= MAX_RELEASE_SUMMARY:
        return text
    return f"{text[: MAX_RELEASE_SUMMARY - 1].rstrip()}…"


def _raise_unless_applied(result: dict[str, Any], container_id: str, name: str) -> None:
    """Raise when Arcane's updater did not bring the container onto a new image.

    The updater answers a skipped or failed update with success and says so per
    resource; a skip comes with its reason, for example an image whose digest
    did not change after the pull.
    """
    items = [item for item in result.get("items") or [] if isinstance(item, dict)]
    own = next(
        (item for item in items if item.get("resourceId") == container_id),
        items[0] if items else None,
    )
    status = own.get("status") if own else None
    if status in UPDATER_APPLIED_STATUSES:
        return
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="update_not_applied",
        translation_placeholders={
            "name": name,
            "status": str(status or "-"),
            "reason": str((own or {}).get("error") or "-"),
        },
    )


def _release_notes_text(notes: dict[str, Any]) -> str | None:
    """Return Arcane's release notes reply as markdown for the update dialog."""
    text = notes.get("notes")
    url = notes.get("releaseUrl")
    text = text.strip() if isinstance(text, str) else ""
    url = url.strip() if isinstance(url, str) and url.startswith("https://") else ""
    if not url:
        return text or None
    name = notes.get("releaseName")
    label = name.strip() if isinstance(name, str) and name.strip() else url
    link = f"[{label}]({url})"
    return f"{text}\n\n{link}" if text else link


class ArcaneContainerUpdate(ArcaneContainerEntity, UpdateEntity):
    """Report and install a newer image for a container.

    Installing runs Arcane's updater for the one container, the way Arcane's
    own "Update" does: it follows the container's tag policy onto a newer tag,
    pulls through Arcane's vulnerability gate when that is on, and recreates
    the container. A redeploy would pull the old reference again and skip the
    gate. An update Arcane holds back is not offered for installation.
    """

    @property
    def supported_features(self) -> UpdateEntityFeature:
        """Return what this entity offers beyond reporting a version."""
        features = UpdateEntityFeature(0)
        container = self.container
        if container is None:
            return features
        if (
            container.update_available
            and container.update_type != UPDATE_TYPE_NOT_PULLED
        ):
            # Arcane's own update view offers release notes on the same terms.
            features |= UpdateEntityFeature.RELEASE_NOTES
        if self.coordinator.option(CONF_ALLOW_CONTROL) and not container.update_blocked:
            features |= UpdateEntityFeature.INSTALL
        return features

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return whether Arcane holds the update back, and why."""
        if (container := self.container) is None:
            return None
        attributes: dict[str, Any] = {"update_blocked": container.update_blocked}
        if container.update_blocked:
            attributes["blocked_reason"] = container.update_blocked_reason
        return attributes

    @property
    def title(self) -> str | None:
        """Return the image this update is about."""
        if (container := self.container) is None:
            return None
        return image_name(container.image)

    @property
    def installed_version(self) -> str | None:
        """Return the version the container currently runs."""
        return self.container.installed_version if self.container else None

    @property
    def latest_version(self) -> str | None:
        """Return the newest version Arcane found for the image."""
        return self.container.latest_version if self.container else None

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Return whether Arcane found an update for the image.

        The versions are often digests, which cannot be ordered, so Arcane's
        own verdict decides instead of a comparison of the two.
        """
        return self.container is not None and self.container.update_available

    async def async_release_notes(self) -> str | None:
        """Return the release notes Arcane finds for the newer image.

        Arcane looks them up in the GitHub releases of the image's source. When
        it finds no notes but knows where they would be, the link is returned
        instead, so the dialog still points somewhere useful.
        """
        if (container := self.container) is None or not container.image:
            return None
        try:
            notes = await self.coordinator.client.async_get_release_notes(
                self.environment_id,
                container.image,
                version=(
                    container.latest_version
                    if container.update_type == UPDATE_TYPE_TAG
                    else None
                ),
                digest=container.latest_digest,
            )
        except ArcanePermissionError:
            self.coordinator.async_note_denied(PERM_IMAGE_UPDATES_READ)
            return None
        except ArcaneError as err:
            _LOGGER.debug("No release notes for %s: %s", container.image, err)
            return None
        return _release_notes_text(notes)

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Have Arcane's updater bring the container onto the newer image."""
        if (container := self.container) is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="container_not_found",
                translation_placeholders={"name": self.container_name},
            )
        if container.update_blocked:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="update_blocked",
                translation_placeholders={
                    "name": self.container_name,
                    "reason": container.update_blocked_reason or "-",
                },
            )
        # Home Assistant shows the update as in progress while this runs.
        try:
            result = await self.coordinator.client.async_update_container(
                self.environment_id, container.id
            )
        except ArcaneError as err:
            raise action_error(
                self.coordinator,
                err,
                permission=PERM_IMAGE_UPDATES_APPLY,
                action_key="image_update",
                placeholders={"name": self.container_name},
            ) from err
        await self.coordinator.async_request_refresh()
        _raise_unless_applied(result, container.id, self.container_name)


class ArcaneInstanceUpdate(ArcaneEnvironmentEntity, UpdateEntity):
    """Report and install a newer version of Arcane itself.

    Installing asks Arcane to pull its own newer image and restart, so the
    instance is briefly unreachable afterwards.
    """

    _attr_title = MANUFACTURER

    @property
    def supported_features(self) -> UpdateEntityFeature:
        """Return what this entity offers beyond reporting a version."""
        features = UpdateEntityFeature(0)
        version = self._version
        if version is not None and version.release_notes:
            features |= UpdateEntityFeature.RELEASE_NOTES
        if self.coordinator.option(CONF_ALLOW_CONTROL):
            features |= UpdateEntityFeature.INSTALL
        return features

    @property
    def _version(self) -> ArcaneVersion | None:
        """Return the version state of this environment's Arcane instance."""
        return self.environment.arcane_version if self.environment else None

    @property
    def available(self) -> bool:
        """Return whether Arcane reported a version for this environment."""
        return super().available and self._version is not None

    @property
    def installed_version(self) -> str | None:
        """Return the version Arcane currently runs."""
        return self._version.installed if self._version else None

    @property
    def latest_version(self) -> str | None:
        """Return the newest Arcane version available."""
        return self._version.latest if self._version else None

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Return whether Arcane offers an update of itself.

        A digest standing in for the newest version cannot be ordered against
        a release number, so Arcane's own verdict decides.
        """
        return self._version is not None and self._version.update_available

    @property
    def release_url(self) -> str | None:
        """Return the release page of the newest version."""
        return self._version.release_url if self._version else None

    @property
    def release_summary(self) -> str | None:
        """Return the beginning of the release notes."""
        return _summary(self._version.release_notes if self._version else None)

    async def async_release_notes(self) -> str | None:
        """Return the full release notes for the more info dialog."""
        return self._version.release_notes if self._version else None

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Ask Arcane to upgrade itself."""
        try:
            await self.coordinator.client.async_upgrade(self.environment_id)
        except ArcaneError as err:
            raise action_error(
                self.coordinator,
                err,
                permission=PERM_SYSTEM_UPGRADE,
                action_key="upgrade",
                placeholders={
                    "name": self.environment.name if self.environment else ""
                },
            ) from err
        await self.coordinator.async_request_refresh()
