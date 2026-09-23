"""Number for the ComfoConnect integration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from aiocomfoconnect.exceptions import (
    AioComfoConnectNotConnected,
    AioComfoConnectTimeout,
    ComfoConnectError,
)
from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import (
    DOMAIN,
    SIGNAL_COMFOCONNECT_AVAILABLE,
    SIGNAL_COMFOCONNECT_RMOT_LIMIT,
    SIGNAL_COMFOCONNECT_UPDATE_RECEIVED,
    ComfoConnectBridge,
    PropertyRange,
)
from .pdo import (
    FILTER_LIFE_DAYS_MAX,
    FILTER_LIFE_DAYS_MIN,
    FILTER_LIFE_DAYS_STEP,
    FLOW_UNITS,
    PROPERTY_PRESET_FLOWS,
    PROPERTY_RMOT_LIMIT_COOLING,
    PROPERTY_RMOT_LIMIT_HEATING,
    SENSOR_FLOW_UNIT,
)
from .pdo import SENSORS as EXTRA_SENSORS

_LOGGER = logging.getLogger(__name__)

# Number of consecutive polling failures tolerated before the entity is
# marked unavailable in Home Assistant.
MAX_UPDATE_FAILURES = 3

# These settings only change when someone changes them, so poll slowly.
SCAN_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True, kw_only=True)
class ComfoconnectNumberEntityDescription(NumberEntityDescription):
    """Describes ComfoConnect number entity."""

    get_value_fn: Callable[[ComfoConnectBridge], Awaitable[PropertyRange]]
    set_value_fn: Callable[[ComfoConnectBridge, float], Awaitable[Any]]
    # Use the range and step reported by the unit (otherwise the ones above).
    unit_range: bool = True
    # Signal that is sent when the value is changed elsewhere (e.g. a button).
    value_signal: str | None = None
    # The value is an airflow in the unit configured on the unit (PDO 224).
    flow_unit: bool = False
    # Only available with the installer PIN (see CONF_INSTALLER_PIN).
    installer: bool = False


def _rmot_limit(key: str, name: str, property_id: int) -> ComfoconnectNumberEntityDescription:
    return ComfoconnectNumberEntityDescription(
        key=key,
        name=name,
        icon="mdi:sun-snowflake-variant",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        get_value_fn=lambda ccb: ccb.get_rmot_limit(property_id),
        set_value_fn=lambda ccb, value: ccb.set_rmot_limit(property_id, value),
        value_signal=SIGNAL_COMFOCONNECT_RMOT_LIMIT.format("{}", property_id),
    )


def _preset_flow(preset: str, property_id: int) -> ComfoconnectNumberEntityDescription:
    return ComfoconnectNumberEntityDescription(
        key=f"preset_flow_{preset}",
        name=f"Airflow {preset}",
        icon="mdi:fan",
        native_unit_of_measurement="m³/h",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        get_value_fn=lambda ccb: ccb.get_preset_flow(property_id),
        set_value_fn=lambda ccb, value: ccb.set_preset_flow(property_id, value),
        flow_unit=True,
        installer=True,
    )


NUMBER_TYPES = (
    _rmot_limit("rmot_limit_heating", "Heating season RMOT limit", PROPERTY_RMOT_LIMIT_HEATING),
    _rmot_limit("rmot_limit_cooling", "Cooling season RMOT limit", PROPERTY_RMOT_LIMIT_COOLING),
    ComfoconnectNumberEntityDescription(
        key="filter_life_days",
        name="Filter replacement interval",
        icon="mdi:air-filter",
        native_unit_of_measurement=UnitOfTime.DAYS,
        native_min_value=FILTER_LIFE_DAYS_MIN,
        native_max_value=FILTER_LIFE_DAYS_MAX,
        native_step=FILTER_LIFE_DAYS_STEP,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        get_value_fn=lambda ccb: ccb.get_filter_life_days(),
        set_value_fn=lambda ccb, value: ccb.set_filter_life_days(value),
        unit_range=False,
    ),
    *(_preset_flow(preset, property_id) for preset, property_id in PROPERTY_PRESET_FLOWS.items()),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ComfoConnect numbers."""
    ccb = hass.data[DOMAIN][config_entry.entry_id]

    async_add_entities(
        [ComfoConnectNumber(ccb=ccb, description=description) for description in NUMBER_TYPES if ccb.installer_mode or not description.installer],
        True,
    )


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
        if self.entity_description.value_signal:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    self.entity_description.value_signal.format(self._ccb.uuid),
                    self._handle_value_update,
                )
            )
        if self.entity_description.flow_unit:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    SIGNAL_COMFOCONNECT_UPDATE_RECEIVED.format(self._ccb.uuid, SENSOR_FLOW_UNIT),
                    self._handle_flow_unit_update,
                )
            )
            await self._ccb.register_sensor(EXTRA_SENSORS[SENSOR_FLOW_UNIT])

    def _handle_availability_update(self, available: bool) -> None:
        """Handle availability updates."""
        self._attr_available = available
        self.schedule_update_ha_state()

    def _handle_value_update(self, value: float) -> None:
        """Handle a new value that was written to the unit."""
        self._attr_native_value = value
        self.schedule_update_ha_state()

    def _handle_flow_unit_update(self, value: int) -> None:
        """Use the airflow unit that is configured on the unit."""
        if (unit := FLOW_UNITS.get(value)) is None or unit == self.native_unit_of_measurement:
            return
        self._attr_native_unit_of_measurement = unit
        self.schedule_update_ha_state()

    async def async_update(self) -> None:
        """Read the value (and its allowed range) from the unit."""
        try:
            prop = await self.entity_description.get_value_fn(self._ccb)
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
        self._attr_native_value = prop.value
        if self.entity_description.unit_range:
            self._attr_native_min_value = prop.minimum
            self._attr_native_max_value = prop.maximum
            if prop.step > 0:
                self._attr_native_step = prop.step

    async def async_set_native_value(self, value: float) -> None:
        """Set the value on the unit."""
        try:
            await self.entity_description.set_value_fn(self._ccb, value)
        except (AioComfoConnectNotConnected, AioComfoConnectTimeout) as err:
            raise HomeAssistantError(f"Not connected to ComfoConnect bridge: {err}") from err
        except ComfoConnectError as err:
            raise HomeAssistantError(f"Failed to set {self.entity_description.name}: {err}") from err
        self._attr_native_value = value
        self.async_write_ha_state()
