"""Tests fuer 0.7.0: Update-Sperre, Versionshinweise, Button "Nach Updates suchen".

Arcane meldet in der Container-Liste, ob ein Update zurueckgehalten wird
(updateInfo.blocked, imageupdate/model.go:91 in Arcane v3.0.0). Die
Versionshinweise holt die Integration wie Arcanes eigene Update-Ansicht
(image-update-item.svelte:222): Image, Digest des neueren Images und die
neuere Version nur bei updateType "tag".
"""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.update import UpdateEntityFeature
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arcane.api import ArcaneConnectionError, ArcanePermissionError
from custom_components.arcane.const import (
    CONF_ALLOW_CONTROL,
    CONF_MONITOR_CONTAINERS,
    CONF_UPDATE_ENTITIES,
    DOMAIN,
)
from custom_components.arcane.models import ArcaneContainer
from tests.conftest import CONTAINERS_PAYLOAD

UPDATE = "update.container_homeassistant_image_update"
CHECK_BUTTON = "button.local_check_for_updates"
IMAGE = "ghcr.io/home-assistant/home-assistant:stable"
DIGEST = "sha256:" + "b" * 64


def _containers(**update_info: Any) -> list[dict[str, Any]]:
    """Container-Liste, in der homeassistant die angegebene updateInfo hat."""
    containers = copy.deepcopy(CONTAINERS_PAYLOAD)
    containers[0]["updateInfo"] = {**containers[0]["updateInfo"], **update_info}
    return containers


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, options: dict | None = None
) -> None:
    entry.add_to_hass(hass)
    if options:
        hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _release_notes(hass: HomeAssistant, hass_ws_client) -> Any:
    """Versionshinweise ueber denselben Weg wie der Update-Dialog."""
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "update/release_notes", "entity_id": UPDATE}
    )
    result = await client.receive_json()
    assert result["success"], result
    return result["result"]


def test_block_is_read_from_update_info() -> None:
    container = ArcaneContainer.from_api(
        _containers(
            blocked=True,
            blockedReason="2 critical vulnerabilities",
            updateType="digest",
            latestDigest=DIGEST,
        )[0]
    )
    assert container.update_blocked is True
    assert container.update_blocked_reason == "2 critical vulnerabilities"
    assert container.update_type == "digest"
    assert container.latest_digest == DIGEST


def test_block_without_update_holds_nothing_back() -> None:
    container = ArcaneContainer.from_api(
        _containers(hasUpdate=False, blocked=True, blockedReason="alt")[0]
    )
    assert container.update_blocked is False


async def test_blocked_update_is_shown_but_not_installable(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_containers.return_value = _containers(
        blocked=True, blockedReason="2 critical vulnerabilities"
    )
    await _setup(hass, mock_config_entry)

    state = hass.states.get(UPDATE)
    assert state.state == "on"
    assert state.attributes["update_blocked"] is True
    assert state.attributes["blocked_reason"] == "2 critical vulnerabilities"
    assert not state.attributes["supported_features"] & UpdateEntityFeature.INSTALL

    # Auch direkt aufgerufen installiert die Entitaet nichts.
    entity = hass.data["update"].get_entity(UPDATE)
    with pytest.raises(HomeAssistantError) as err:
        await entity.async_install(None, False)
    assert err.value.translation_key == "update_blocked"
    assert err.value.translation_placeholders["reason"] == "2 critical vulnerabilities"
    mock_client.async_container_action.assert_not_awaited()


async def test_unblocked_update_stays_installable(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)

    state = hass.states.get(UPDATE)
    assert state.attributes["update_blocked"] is False
    assert "blocked_reason" not in state.attributes
    assert state.attributes["supported_features"] & UpdateEntityFeature.INSTALL


async def test_release_notes_for_a_new_digest(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_ws_client,
) -> None:
    mock_client.async_get_containers.return_value = _containers(
        updateType="digest", latestDigest=DIGEST
    )
    mock_client.async_get_release_notes.return_value = {
        "status": "found",
        "notes": "## Neu\n\nSchneller.",
        "releaseName": "2025.2.0",
        "releaseUrl": "https://github.com/home-assistant/core/releases/tag/2025.2.0",
    }
    await _setup(hass, mock_config_entry)

    state = hass.states.get(UPDATE)
    assert state.attributes["supported_features"] & UpdateEntityFeature.RELEASE_NOTES

    notes = await _release_notes(hass, hass_ws_client)
    assert notes == (
        "## Neu\n\nSchneller.\n\n"
        "[2025.2.0](https://github.com/home-assistant/core/releases/tag/2025.2.0)"
    )
    # Ohne neuen Tag zaehlt nur der Digest, wie in Arcanes Oberflaeche.
    mock_client.async_get_release_notes.assert_awaited_once_with(
        "0", IMAGE, version=None, digest=DIGEST
    )


async def test_release_notes_for_a_new_tag_name_the_version(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_ws_client,
) -> None:
    mock_client.async_get_containers.return_value = _containers(
        updateType="tag", latestDigest=DIGEST
    )
    mock_client.async_get_release_notes.return_value = {
        "status": "link",
        "releaseUrl": "https://example.org/releases",
    }
    await _setup(hass, mock_config_entry)

    notes = await _release_notes(hass, hass_ws_client)
    # Ohne Text bleibt der Link, damit der Dialog nicht leer ist.
    assert notes == "[https://example.org/releases](https://example.org/releases)"
    mock_client.async_get_release_notes.assert_awaited_once_with(
        "0", IMAGE, version="2025.2.0", digest=DIGEST
    )


async def test_release_notes_ignore_links_that_are_not_https(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_ws_client,
) -> None:
    mock_client.async_get_release_notes.return_value = {
        "status": "link",
        "releaseUrl": "javascript:alert(1)",
    }
    await _setup(hass, mock_config_entry)

    assert await _release_notes(hass, hass_ws_client) is None


async def test_refused_release_notes_name_the_permission(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_ws_client,
) -> None:
    mock_client.async_get_release_notes.side_effect = ArcanePermissionError("nope")
    await _setup(hass, mock_config_entry)

    assert await _release_notes(hass, hass_ws_client) is None
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"missing_permissions_{mock_config_entry.entry_id}"
    )
    assert issue is not None
    assert "`image-updates:read`" in issue.translation_placeholders["permissions"]


async def test_failed_release_notes_leave_the_dialog_empty(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_ws_client,
) -> None:
    mock_client.async_get_release_notes.side_effect = ArcaneConnectionError("weg")
    await _setup(hass, mock_config_entry)

    assert await _release_notes(hass, hass_ws_client) is None


async def test_no_release_notes_for_an_image_that_is_not_pulled(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_containers.return_value = _containers(updateType="not_pulled")
    await _setup(hass, mock_config_entry)

    state = hass.states.get(UPDATE)
    assert not state.attributes["supported_features"] & (
        UpdateEntityFeature.RELEASE_NOTES
    )


async def test_check_for_updates_button(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    calls_before = mock_client.async_get_containers.await_count

    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: CHECK_BUTTON}, blocking=True
    )
    await hass.async_block_till_done()

    mock_client.async_check_image_updates.assert_awaited_once_with("0")
    # Danach wird neu abgefragt, damit das Ergebnis sichtbar wird.
    assert mock_client.async_get_containers.await_count > calls_before


async def test_refused_check_names_the_permission(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.async_check_image_updates.side_effect = ArcanePermissionError("nope")
    await _setup(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "button", "press", {ATTR_ENTITY_ID: CHECK_BUTTON}, blocking=True
        )
    assert err.value.translation_key == "check_updates_forbidden"
    assert err.value.translation_placeholders["permission"] == "image-updates:check"
    assert "image-updates:check" in mock_config_entry.runtime_data.missing_permissions


@pytest.mark.parametrize(
    "options",
    [
        {CONF_ALLOW_CONTROL: False},
        {CONF_UPDATE_ENTITIES: False},
        {CONF_MONITOR_CONTAINERS: False},
    ],
)
async def test_check_button_only_with_update_entities_and_control(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    options: dict,
) -> None:
    await _setup(hass, mock_config_entry, options)

    assert hass.states.get(CHECK_BUTTON) is None
