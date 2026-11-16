"""Button platform for the Arcane integration."""

from __future__ import annotations

import logging

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ArcaneConfigEntry
from .api import ArcaneError, ArcanePermissionError
from .const import (
    CONF_ALLOW_CONTROL,
    CONF_JOBS,
    CONF_MONITOR_CONTAINERS,
    CONF_PRUNE_BUTTON,
    CONF_REDEPLOY_BUTTONS,
    CONF_UPDATE_ENTITIES,
    CONF_UPDATER,
    DOMAIN,
    LOCAL_ENVIRONMENT_ID,
    PRUNE_OFF,
    PRUNE_UNUSED,
)
from .entity import (
    ArcaneContainerEntity,
    ArcaneEnvironmentEntity,
    ArcaneJobEntity,
    ArcaneProjectEntity,
)
from .helpers import action_error, async_setup_entities
from .permissions import (
    CONTAINER_ACTION_PERMISSIONS,
    PERM_IMAGE_UPDATES_APPLY,
    PERM_IMAGE_UPDATES_CHECK,
    PERM_JOBS_RUN,
    PERM_SYSTEM_PRUNE,
    PROJECT_ACTION_PERMISSIONS,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

RESTART_BUTTON = ButtonEntityDescription(
    key="restart",
    translation_key="restart",
    device_class=ButtonDeviceClass.RESTART,
)

REDEPLOY_BUTTON = ButtonEntityDescription(
    key="redeploy",
    translation_key="redeploy",
)

PRUNE_BUTTON = ButtonEntityDescription(
    key="prune",
    translation_key="prune",
)

CHECK_UPDATES_BUTTON = ButtonEntityDescription(
    key="check_updates",
    translation_key="check_updates",
)

APPLY_UPDATES_BUTTON = ButtonEntityDescription(
    key="apply_updates",
    translation_key="apply_updates",
)

RUN_JOB_BUTTON = ButtonEntityDescription(
    key="run",
    translation_key="run_job",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArcaneConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Arcane buttons."""
    coordinator = entry.runtime_data
    if not coordinator.option(CONF_ALLOW_CONTROL):
        return

    redeploy = coordinator.option(CONF_REDEPLOY_BUTTONS)
    prune = coordinator.option(CONF_PRUNE_BUTTON) != PRUNE_OFF
    # Checking for updates only matters where the container update entities
    # show what the check found.
    check_updates = coordinator.option(CONF_UPDATE_ENTITIES) and coordinator.option(
        CONF_MONITOR_CONTAINERS
    )

    apply_updates = coordinator.option(CONF_UPDATER) and coordinator.option(
        CONF_MONITOR_CONTAINERS
    )

    def job_buttons(environment_id: str, job_id: str) -> list[ButtonEntity]:
        job = coordinator.data.environments[environment_id].jobs[job_id]
        # Arcane runs only jobs it marks as runnable by hand, and a job only the
        # manager runs is never started in a remote environment.
        if not job.can_run_manually or (
            job.manager_only and environment_id != LOCAL_ENVIRONMENT_ID
        ):
            return []
        return [ArcaneJobButton(coordinator, environment_id, job_id, RUN_JOB_BUTTON)]

    def environment_buttons(environment_id: str) -> list[ButtonEntity]:
        buttons: list[ButtonEntity] = []
        if apply_updates:
            buttons.append(
                ArcaneApplyUpdatesButton(
                    coordinator, environment_id, APPLY_UPDATES_BUTTON
                )
            )
        if prune:
            buttons.append(ArcanePruneButton(coordinator, environment_id, PRUNE_BUTTON))
        if check_updates:
            buttons.append(
                ArcaneCheckUpdatesButton(
                    coordinator, environment_id, CHECK_UPDATES_BUTTON
                )
            )
        return buttons

    async_setup_entities(
        coordinator,
        async_add_entities,
        environments=(
            environment_buttons if prune or check_updates or apply_updates else None
        ),
        containers=lambda environment_id, name: [
            ArcaneContainerButton(coordinator, environment_id, name, RESTART_BUTTON),
            *(
                [
                    ArcaneContainerButton(
                        coordinator, environment_id, name, REDEPLOY_BUTTON
                    )
                ]
                if redeploy
                else []
            ),
        ],
        projects=lambda environment_id, project_id: [
            ArcaneProjectButton(
                coordinator, environment_id, project_id, RESTART_BUTTON
            ),
            *(
                [
                    ArcaneProjectButton(
                        coordinator, environment_id, project_id, REDEPLOY_BUTTON
                    )
                ]
                if redeploy
                else []
            ),
        ],
        jobs=job_buttons if coordinator.option(CONF_JOBS) else None,
    )


class ArcaneContainerButton(ArcaneContainerEntity, ButtonEntity):
    """Run a container action that has no state of its own."""

    async def async_press(self) -> None:
        """Run the action this button stands for."""
        action = self.entity_description.key
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


class ArcaneProjectButton(ArcaneProjectEntity, ButtonEntity):
    """Run a compose project action that has no state of its own."""

    async def async_press(self) -> None:
        """Run the action this button stands for."""
        action = self.entity_description.key
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


class ArcanePruneButton(ArcaneEnvironmentEntity, ButtonEntity):
    """Remove unused images, networks and build cache from an environment.

    Containers and volumes are never pruned. Both hold state a user cannot get
    back, which is not something a single button press should be able to do.
    """

    async def async_press(self) -> None:
        """Run the prune."""
        aggressive = self.coordinator.option(CONF_PRUNE_BUTTON) == PRUNE_UNUSED
        try:
            result = await self.coordinator.client.async_prune(
                self.environment_id, unused_images=aggressive
            )
        except ArcaneError as err:
            raise action_error(
                self.coordinator,
                err,
                permission=PERM_SYSTEM_PRUNE,
                action_key="prune",
                placeholders={
                    "name": self.environment.name if self.environment else ""
                },
            ) from err
        _LOGGER.debug(
            "Prune of environment %s returned %s", self.environment_id, result
        )
        # The reclaimable space the last report counted is gone now.
        self.coordinator.async_disk_usage_now(self.environment_id)
        await self.coordinator.async_request_refresh()


class ArcaneCheckUpdatesButton(ArcaneEnvironmentEntity, ButtonEntity):
    """Have Arcane check every image of an environment for an update now.

    Arcane checks on its own schedule as well; this only saves the wait. The
    image update entities show the result once the check is done.
    """

    async def async_press(self) -> None:
        """Run the update check."""
        try:
            await self.coordinator.client.async_check_image_updates(self.environment_id)
        except ArcaneError as err:
            raise action_error(
                self.coordinator,
                err,
                permission=PERM_IMAGE_UPDATES_CHECK,
                action_key="check_updates",
                placeholders={
                    "name": self.environment.name if self.environment else ""
                },
            ) from err
        await self.coordinator.async_request_refresh()


class ArcaneApplyUpdatesButton(ArcaneEnvironmentEntity, ButtonEntity):
    """Have Arcane install every pending update of an environment.

    The run is the one Arcane's own scheduled updates make: containers left out
    of automatic updates and those opting out by label stay as they are. It can
    take up to half an hour, so the press returns at once and the run goes on
    in the background; the "Updates being installed" sensor shows it, and the
    last update sensor shows how it went.
    """

    async def async_press(self) -> None:
        """Start the updater run."""
        coordinator = self.coordinator
        environment_id = self.environment_id
        name = self.environment.name if self.environment else environment_id
        if environment_id in coordinator.updater_runs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="apply_updates_running",
                translation_placeholders={"name": name},
            )
        coordinator.updater_runs.add(environment_id)
        coordinator.config_entry.async_create_background_task(
            self.hass,
            self._async_run(name),
            f"{DOMAIN} updater run {environment_id}",
        )
        await coordinator.async_request_refresh()

    async def _async_run(self, name: str) -> None:
        coordinator = self.coordinator
        environment_id = self.environment_id
        try:
            result = await coordinator.client.async_run_updater(environment_id)
        except ArcanePermissionError:
            coordinator.async_note_denied(PERM_IMAGE_UPDATES_APPLY)
            _LOGGER.warning(
                "Arcane refused to install the updates of %s: the API key lacks %s",
                name,
                PERM_IMAGE_UPDATES_APPLY,
            )
        except ArcaneError as err:
            _LOGGER.warning("Installing the updates of %s failed: %s", name, err)
        else:
            _LOGGER.info(
                "Arcane installed the updates of %s: %s updated, %s skipped, %s failed",
                name,
                result.get("updated"),
                result.get("skipped"),
                result.get("failed"),
            )
        finally:
            coordinator.updater_runs.discard(environment_id)
        await coordinator.async_request_refresh()


class ArcaneJobButton(ArcaneJobEntity, ButtonEntity):
    """Have Arcane run one of its background jobs now.

    Arcane queues the run and answers at once; the job's sensor shows the run
    under way and then how it went.
    """

    @property
    def available(self) -> bool:
        """Return whether Arcane would accept a run of the job now.

        Arcane refuses a job that is switched off or whose prerequisites, such
        as a configured scanner, are not met; the button follows that.
        """
        job = self.job
        return (
            super().available
            and job is not None
            and job.enabled
            and job.prerequisites_met
        )

    async def async_press(self) -> None:
        """Queue a run of the job."""
        name = self.job.name if self.job else self.job_id
        try:
            result = await self.coordinator.client.async_run_job(
                self.environment_id, self.job_id
            )
        except ArcaneError as err:
            raise action_error(
                self.coordinator,
                err,
                permission=PERM_JOBS_RUN,
                action_key="run_job",
                placeholders={"name": name},
            ) from err
        _LOGGER.debug("Arcane accepted a run of job %s: %s", self.job_id, result)
        await self.coordinator.async_request_refresh()
