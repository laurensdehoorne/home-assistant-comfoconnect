"""Number for the ComfoConnect integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from aiocomfoconnect.exceptions import (
    AioComfoConnectNotConnected,
    AioComfoConnectTimeout,
    ComfoConnectError,
)
from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN, SIGNAL_COMFOCONNECT_AVAILABLE, SIGNAL_COMFOCONNECT_RMOT_LIMIT, ComfoConnectBridge
from .pdo import PROPERTY_RMOT_LIMIT_COOLING, PROPERTY_RMOT_LIMIT_HEATING

_LOGGER = logging.getLogger(__name__)

# Number of consecutive polling failures tolerated before the entity is
# marked unavailable in Home Assistant.
MAX_UPDATE_FAILURES = 3

# These settings only change when someone changes them, so poll slowly.
SCAN_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True, kw_only=True)
class ComfoconnectNumberEntityDescription(NumberEntityDescription):
    """Describes ComfoConnect number entity."""

    property_id: int


NUMBER_TYPES = (
    ComfoconnectNumberEntityDescription(
        key="rmot_limit_heating",
        name="Heating season RMOT limit",
        icon="mdi:sun-snowflake-variant",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        property_id=PROPERTY_RMOT_LIMIT_HEATING,
    ),
    ComfoconnectNumberEntityDescription(
        key="rmot_limit_cooling",
        name="Cooling season RMOT limit",
        icon="mdi:sun-snowflake-variant",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        property_id=PROPERTY_RMOT_LIMIT_COOLING,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ComfoConnect numbers."""
    ccb = hass.data[DOMAIN][config_entry.entry_id]

    async_add_entities([ComfoConnectNumber(ccb=ccb, description=description) for description in NUMBER_TYPES], True)


class ComfoConnectNumber(NumberEntity):
    """Representation of a ComfoConnect number entity."""

    _attr_has_entity_name = True
    _attr_should_poll = True
    entity_description: ComfoconnectNumberEntityDescription

    def __init__(self, ccb: ComfoConnectBridge, description: ComfoconnectNumberEntityDescription) -> None:
        """Initialize the ComfoConnect number entity."""
        self._ccb = ccb
        self.entity_description = description
        self._fail_count = 0
        self._attr_unique_id = f"{self._ccb.uuid}-{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._ccb.uuid)},
        )

    async def async_added_to_hass(self) -> None:
        """Register for updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_COMFOCONNECT_AVAILABLE.format(self._ccb.uuid),
                self._handle_availability_update,
            )
        )
        # Also changed by the "start season now" buttons.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_COMFOCONNECT_RMOT_LIMIT.format(self._ccb.uuid, self.entity_description.property_id),
                self._handle_value_update,
            )
        )

    def _handle_availability_update(self, available: bool) -> None:
        """Handle availability updates."""
        self._attr_available = available
        self.schedule_update_ha_state()

    def _handle_value_update(self, value: float) -> None:
        """Handle a new value that was written to the unit."""
        self._attr_native_value = value
        self.schedule_update_ha_state()

    async def async_update(self) -> None:
        """Read the value and its allowed range from the unit."""
        try:
            limit = await self._ccb.get_rmot_limit(self.entity_description.property_id)
        except (AioComfoConnectTimeout, AioComfoConnectNotConnected, ComfoConnectError) as err:
            self._fail_count += 1
            if self._fail_count >= MAX_UPDATE_FAILURES:
                self._attr_available = False
            _LOGGER.debug(
                "Update for %s failed (%d/%d): %s",
                self.entity_description.key,
                self._fail_count,
                MAX_UPDATE_FAILURES,
                err,
            )
            return

        self._fail_count = 0
        self._attr_available = True
        self._attr_native_value = limit.value
        self._attr_native_min_value = limit.minimum
        self._attr_native_max_value = limit.maximum
        if limit.step > 0:
            self._attr_native_step = limit.step

    async def async_set_native_value(self, value: float) -> None:
        """Set the value on the unit."""
        try:
            await self._ccb.set_rmot_limit(self.entity_description.property_id, value)
        except (AioComfoConnectNotConnected, AioComfoConnectTimeout) as err:
            raise HomeAssistantError(f"Not connected to ComfoConnect bridge: {err}") from err
        except ComfoConnectError as err:
            raise HomeAssistantError(f"Failed to set {self.entity_description.name}: {err}") from err
