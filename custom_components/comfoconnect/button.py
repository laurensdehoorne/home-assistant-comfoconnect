"""Button for the ComfoConnect integration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Coroutine
from dataclasses import dataclass
from typing import Any, Callable, cast

from aiocomfoconnect.exceptions import (
    AioComfoConnectNotConnected,
    AioComfoConnectTimeout,
    ComfoConnectError,
)
from aiocomfoconnect.sensors import SENSOR_RMOT, SENSORS
from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN, ComfoConnectBridge
from .pdo import PROPERTY_RMOT_LIMIT_COOLING, PROPERTY_RMOT_LIMIT_HEATING

_LOGGER = logging.getLogger(__name__)


@dataclass
class ComfoconnectRequiredKeysMixin:
    """Mixin for required keys."""

    press_fn: Callable[[ComfoConnectBridge, str], Awaitable[Any]]


@dataclass
class ComfoconnectButtonEntityDescription(ButtonEntityDescription, ComfoconnectRequiredKeysMixin):
    """Describes ComfoConnect button entity."""

    # The button needs the current RMOT.
    needs_rmot: bool = False


BUTTON_TYPES = (
    ComfoconnectButtonEntityDescription(
        key="reset_errors",
        press_fn=lambda ccb, option: cast(Coroutine, ccb.clear_errors()),
        name="Reset errors",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ComfoconnectButtonEntityDescription(
        key="start_heating_season",
        press_fn=lambda ccb, option: cast(Coroutine, ccb.start_season_now(PROPERTY_RMOT_LIMIT_HEATING)),
        name="Start heating season now",
        icon="mdi:radiator",
        entity_category=EntityCategory.CONFIG,
        needs_rmot=True,
    ),
    ComfoconnectButtonEntityDescription(
        key="start_cooling_season",
        press_fn=lambda ccb, option: cast(Coroutine, ccb.start_season_now(PROPERTY_RMOT_LIMIT_COOLING)),
        name="Start cooling season now",
        icon="mdi:snowflake",
        entity_category=EntityCategory.CONFIG,
        needs_rmot=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ComfoConnect binary sensors."""
    ccb = hass.data[DOMAIN][config_entry.entry_id]

    sensors = [ComfoConnectButton(ccb=ccb, config_entry=config_entry, description=description) for description in BUTTON_TYPES]

    async_add_entities(sensors, True)


class ComfoConnectButton(ButtonEntity):
    """Representation of a ComfoConnect button."""

    _attr_has_entity_name = True
    entity_description: ComfoconnectButtonEntityDescription

    def __init__(
        self,
        ccb: ComfoConnectBridge,
        config_entry: ConfigEntry,
        description: ComfoconnectButtonEntityDescription,
    ) -> None:
        """Initialize the ComfoConnect sensor."""
        self._ccb = ccb
        self.entity_description = description
        self._attr_unique_id = f"{self._ccb.uuid}-{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._ccb.uuid)},
        )

    async def async_added_to_hass(self) -> None:
        """Register the sensors the button needs."""
        if self.entity_description.needs_rmot:
            await self._ccb.register_sensor(SENSORS[SENSOR_RMOT])

    async def async_press(self) -> None:
        """Press the button."""
        try:
            await self.entity_description.press_fn(self._ccb, self._attr_unique_id)
        except (AioComfoConnectNotConnected, AioComfoConnectTimeout) as err:
            raise HomeAssistantError(f"Not connected to ComfoConnect bridge: {err}") from err
        except ComfoConnectError as err:
            raise HomeAssistantError(f"{self.entity_description.name} failed: {err}") from err
