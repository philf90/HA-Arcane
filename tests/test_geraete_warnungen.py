"""Tests fuer 0.6.1: Geraete ohne via_device, Zertifikatswarnung nur bei https.

Home Assistant 2026.11 meldet ``via_device`` in der Geraeteinfo als veraltet
(entfaellt in 2027.8) und ``async_get_device`` ebenso. Die Integration
verknuepft Geraete deshalb nachtraeglich ueber ``via_device_id``.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arcane.const import DOMAIN
from custom_components.arcane.helpers import find_device as _find_device


async def test_no_deprecated_via_device(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Keine Geraeteinfo uebergibt das veraltete via_device."""
    seen: list[dict] = []
    original = dr.DeviceRegistry.async_get_or_create

    def record(self, **kwargs):
        seen.append(kwargs)
        return original(self, **kwargs)

    with patch.object(dr.DeviceRegistry, "async_get_or_create", record):
        mock_config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert len(seen) > 3, "keine Geraete angelegt"
    assert not [kwargs for kwargs in seen if "via_device" in kwargs]

    # Die Hierarchie steht trotzdem: Projekt unter Umgebung.
    registry = dr.async_get(hass)
    devices = {device.name: device for device in registry.devices.values()}
    assert devices["Project smarthome"].via_device_id == devices["Local"].id


def test_lookup_prefers_the_new_registry_call() -> None:
    """Gibt es async_get_device_by_identifier, wird async_get_device nicht genutzt."""
    registry = MagicMock()
    registry.async_get_device_by_identifier.return_value = "neu"

    assert _find_device(registry, "abc", "entry") == "neu"
    registry.async_get_device_by_identifier.assert_called_once_with(
        (DOMAIN, "abc"), "entry"
    )
    registry.async_get_device.assert_not_called()


def test_lookup_falls_back_on_older_home_assistant() -> None:
    """Home Assistant vor 2026.11 kennt nur async_get_device."""
    registry = MagicMock(spec=["async_get_device"])
    registry.async_get_device.return_value = "alt"

    assert _find_device(registry, "abc", "entry") == "alt"
    registry.async_get_device.assert_called_once_with(identifiers={(DOMAIN, "abc")})


@pytest.mark.parametrize(
    ("url", "warned"),
    [
        ("http://192.168.1.10:3552", False),
        ("https://arcane.example:3552", True),
    ],
)
async def test_certificate_warning_only_for_https(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    caplog: pytest.LogCaptureFixture,
    url: str,
    warned: bool,
) -> None:
    """Ohne Zertifikatspruefung warnt die Integration nur bei https."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="arcane",
        data={"url": url, "api_key": "test-key", "verify_ssl": False},
    )
    entry.add_to_hass(hass)
    with caplog.at_level(logging.WARNING, logger="custom_components.arcane"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ("Certificate verification is disabled" in caplog.text) is warned
