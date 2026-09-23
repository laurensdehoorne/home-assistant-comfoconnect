"""Select for the ComfoConnect integration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Coroutine
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Any, Callable, cast

from aiocomfoconnect.const import (
    ComfoCoolMode,
    VentilationBalance,
    VentilationMode,
    VentilationSetting,
    VentilationTemperatureProfile,
)
from aiocomfoconnect.exceptions import (
    AioComfoConnectNotConnected,
    AioComfoConnectTimeout,
    ComfoConnectError,
)
from aiocomfoconnect.sensors import (
    SENSOR_BYPASS_ACTIVATION_STATE,
    SENSOR_OPERATING_MODE,
    SENSOR_OPERATING_MODE_2,
    SENSOR_PROFILE_TEMPERATURE,
    SENSORS,
)
from aiocomfoconnect.sensors import Sensor as AioComfoConnectSensor
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN, SIGNAL_COMFOCONNECT_AVAILABLE, SIGNAL_COMFOCONNECT_UPDATE_RECEIVED, ComfoConnectBridge
from .pdo import BOOST_TIMERS, SENSOR_COMFOCOOL_MODE, SENSOR_EXHAUST_FAN_TIMER, SENSOR_SUPPLY_FAN_TIMER, TEMPERATURE_PASSIVE_PRESETS
from .pdo import SENSORS as EXTRA_SENSORS

_LOGGER = logging.getLogger(__name__)

# Number of consecutive polling failures tolerated before a polled select is
# marked unavailable in Home Assistant.
MAX_UPDATE_FAILURES = 3

# Poll the authoritative RMI value on a slow interval. The bridge pushes
# (sensor-backed) values for responsiveness, but pushes a stale value on
# reconnect; the periodic RMI read corrects any drift even when a reconnect
# went unnoticed by the keepalive.
SCAN_INTERVAL = timedelta(minutes=5)


@dataclass
class ComfoconnectSelectDescriptionMixin:
    """Mixin for required keys."""

    set_value_fn: Callable[[ComfoConnectBridge, str], Awaitable[Any]]
    get_value_fn: Callable[[ComfoConnectBridge], Awaitable[Any]]


@dataclass
class ComfoconnectSelectEntityDescription(SelectEntityDescription, ComfoconnectSelectDescriptionMixin):
    """Describes ComfoConnect select entity."""

    # Sensors (PDOs) that push the current value. sensor_value_fn receives the
    # latest value of each of them (by sensor id) once all have been received,
    # and returns the option, or None when the values don't map to an option.
    sensors: tuple[AioComfoConnectSensor, ...] = ()
    sensor_value_fn: Callable[[dict[int, Any]], str | None] = None
    # Only add the entity when the unit supports it (reading it doesn't fail).
    probe: bool = False


async def _get_boost_option(ccb: ComfoConnectBridge) -> str | None:
    """Map get_boost() bool to a select option string."""
    return None if await ccb.get_boost() else "Off"


def _balance_from_fan_timers(values: dict[int, Any]) -> str | None:
    """
    Map the exhaust (F12) and supply (F22) fan timer values to a balance mode.

    A value of 1 means that the fan is switched off by its timer.
    """
    exhaust_off = values[SENSOR_EXHAUST_FAN_TIMER] == 1
    supply_off = values[SENSOR_SUPPLY_FAN_TIMER] == 1
    if exhaust_off and supply_off:
        return None
    if exhaust_off:
        return VentilationBalance.SUPPLY_ONLY
    if supply_off:
        return VentilationBalance.EXHAUST_ONLY
    return VentilationBalance.BALANCE


async def _get_temperature_passive_preset(ccb: ComfoConnectBridge) -> str | None:
    """Map the temperature passive preset (0/1/2) to a select option."""
    return TEMPERATURE_PASSIVE_PRESETS.get(await ccb.get_temperature_passive_preset())


async def _set_temperature_passive_preset(ccb: ComfoConnectBridge, option: str) -> None:
    """Set the temperature passive preset from a select option."""
    value = next(value for value, name in TEMPERATURE_PASSIVE_PRESETS.items() if name == option)
    await ccb.set_temperature_passive_preset(value)


async def _get_comfocool_option(ccb: ComfoConnectBridge) -> str:
    """Map get_comfocool_mode() bool to a select option string."""
    return ComfoCoolMode.AUTO if await ccb.get_comfocool_mode() else ComfoCoolMode.OFF


SELECT_TYPES = (
    ComfoconnectSelectEntityDescription(
        key="select_mode",
        name="Ventilation Mode",
        icon="mdi:fan-auto",
        entity_category=EntityCategory.CONFIG,
        get_value_fn=lambda ccb: cast(Coroutine, ccb.get_mode()),
        set_value_fn=lambda ccb, option: cast(Coroutine, ccb.set_mode(option)),
        options=[VentilationMode.AUTO, VentilationMode.MANUAL],
        sensors=(SENSORS.get(SENSOR_OPERATING_MODE),),
        sensor_value_fn=lambda values: {-1: VentilationMode.AUTO, 1: VentilationMode.MANUAL}.get(values[SENSOR_OPERATING_MODE]),
    ),
    ComfoconnectSelectEntityDescription(
        key="bypass_mode",
        name="Bypass Mode",
        icon="mdi:camera-iris",
        entity_category=EntityCategory.CONFIG,
        get_value_fn=lambda ccb: cast(Coroutine, ccb.get_bypass()),
        set_value_fn=lambda ccb, option: cast(Coroutine, ccb.set_bypass(option)),
        options=[VentilationSetting.AUTO, VentilationSetting.ON, VentilationSetting.OFF],
        sensors=(SENSORS.get(SENSOR_BYPASS_ACTIVATION_STATE),),
        sensor_value_fn=lambda values: {0: VentilationSetting.AUTO, 1: VentilationSetting.ON, 2: VentilationSetting.OFF}.get(
            values[SENSOR_BYPASS_ACTIVATION_STATE]
        ),
    ),
    ComfoconnectSelectEntityDescription(
        key="balance_mode",
        name="Balance Mode",
        entity_category=EntityCategory.CONFIG,
        get_value_fn=lambda ccb: cast(Coroutine, ccb.get_balance_mode()),
        set_value_fn=lambda ccb, option: cast(Coroutine, ccb.set_balance_mode(option)),
        options=[VentilationBalance.BALANCE, VentilationBalance.SUPPLY_ONLY, VentilationBalance.EXHAUST_ONLY],
        sensors=(EXTRA_SENSORS.get(SENSOR_EXHAUST_FAN_TIMER), EXTRA_SENSORS.get(SENSOR_SUPPLY_FAN_TIMER)),
        sensor_value_fn=_balance_from_fan_timers,
    ),
    ComfoconnectSelectEntityDescription(
        key="temperature_profile",
        name="Temperature Profile",
        icon="mdi:thermometer-auto",
        entity_category=EntityCategory.CONFIG,
        get_value_fn=lambda ccb: cast(Coroutine, ccb.get_temperature_profile()),
        set_value_fn=lambda ccb, option: cast(Coroutine, ccb.set_temperature_profile(option)),
        options=[VentilationTemperatureProfile.WARM, VentilationTemperatureProfile.NORMAL, VentilationTemperatureProfile.COOL],
        sensors=(SENSORS.get(SENSOR_PROFILE_TEMPERATURE),),
        sensor_value_fn=lambda values: {
            0: VentilationTemperatureProfile.NORMAL,
            1: VentilationTemperatureProfile.COOL,
            2: VentilationTemperatureProfile.WARM,
        }.get(values[SENSOR_PROFILE_TEMPERATURE]),
    ),
    ComfoconnectSelectEntityDescription(
        key="comfocool",
        name="ComfoCool Mode",
        entity_category=EntityCategory.CONFIG,
        get_value_fn=_get_comfocool_option,
        set_value_fn=lambda ccb, option: cast(Coroutine, ccb.set_comfocool_mode(option)),
        options=[ComfoCoolMode.AUTO, ComfoCoolMode.OFF],
        # The ComfoCool-off timer value (0 = auto, 1 = off), not the compressor state.
        sensors=(EXTRA_SENSORS.get(SENSOR_COMFOCOOL_MODE),),
        sensor_value_fn=lambda values: {0: ComfoCoolMode.AUTO, 1: ComfoCoolMode.OFF}.get(values[SENSOR_COMFOCOOL_MODE]),
    ),
    # Boost mode with Off option added
    ComfoconnectSelectEntityDescription(
        key="boost_timeout",
        name="Boost Mode",
        icon="mdi:fan-plus",
        get_value_fn=_get_boost_option,
        set_value_fn=lambda ccb, option: (
            cast(Coroutine, ccb.set_boost(False)) if option == "Off" else cast(Coroutine, ccb.set_boost(True, int(option.split()[0]) * 60))
        ),
        options=["Off", "10 Minutes", "20 Minutes", "30 Minutes", "40 Minutes", "50 Minutes", "60 Minutes"],
        # The active preset timer: while a boost timer is active the chosen
        # duration is unknown, so keep the current option (None).
        sensors=(SENSORS.get(SENSOR_OPERATING_MODE_2),),
        sensor_value_fn=lambda values: None if values[SENSOR_OPERATING_MODE_2] in BOOST_TIMERS else "Off",
    ),
    ComfoconnectSelectEntityDescription(
        key="sensor_ventilation_temperature_passive",
        name="Sensor ventilation temperature passive",
        icon="mdi:thermometer-auto",
        entity_category=EntityCategory.CONFIG,
        get_value_fn=lambda ccb: cast(Coroutine, ccb.get_sensor_ventmode_temperature_passive()),
        set_value_fn=lambda ccb, option: cast(Coroutine, ccb.set_sensor_ventmode_temperature_passive(option)),
        options=[VentilationSetting.AUTO, VentilationSetting.ON, VentilationSetting.OFF],
    ),
    ComfoconnectSelectEntityDescription(
        key="sensor_ventilation_temperature_passive_preset",
        name="Sensor ventilation temperature passive reaction",
        icon="mdi:speedometer",
        entity_category=EntityCategory.CONFIG,
        get_value_fn=_get_temperature_passive_preset,
        set_value_fn=_set_temperature_passive_preset,
        options=list(TEMPERATURE_PASSIVE_PRESETS.values()),
        # Firmware R1.9.0 and newer.
        probe=True,
    ),
    ComfoconnectSelectEntityDescription(
        key="sensor_ventilation_humidity_comfort",
        name="Sensor ventilation humidity comfort",
        icon="mdi:water-percent",
        entity_category=EntityCategory.CONFIG,
        get_value_fn=lambda ccb: cast(Coroutine, ccb.get_sensor_ventmode_humidity_comfort()),
        set_value_fn=lambda ccb, option: cast(Coroutine, ccb.set_sensor_ventmode_humidity_comfort(option)),
        options=[VentilationSetting.AUTO, VentilationSetting.ON, VentilationSetting.OFF],
    ),
    ComfoconnectSelectEntityDescription(
        key="sensor_ventilation_humidity_protection",
        name="Sensor ventilation humidity protection",
        icon="mdi:water-alert",
        entity_category=EntityCategory.CONFIG,
        get_value_fn=lambda ccb: cast(Coroutine, ccb.get_sensor_ventmode_humidity_protection()),
        set_value_fn=lambda ccb, option: cast(Coroutine, ccb.set_sensor_ventmode_humidity_protection(option)),
        options=[VentilationSetting.AUTO, VentilationSetting.ON, VentilationSetting.OFF],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ComfoConnect selects."""
    ccb = hass.data[DOMAIN][config_entry.entry_id]

    selects = []
    for description in SELECT_TYPES:
        if description.probe:
            try:
                await description.get_value_fn(ccb)
            except ComfoConnectError as err:
                _LOGGER.debug("The unit doesn't support %s: %s", description.key, err)
                continue
            except (AioComfoConnectNotConnected, AioComfoConnectTimeout):
                # Can't tell now; add it and let it become unavailable if unsupported.
                pass
        selects.append(ComfoConnectSelect(ccb=ccb, config_entry=config_entry, description=description))

    async_add_entities(selects, True)


class ComfoConnectSelect(SelectEntity):
    """Representation of a ComfoConnect select entity."""

    _attr_has_entity_name = True
    entity_description: ComfoconnectSelectEntityDescription

    def __init__(
        self,
        ccb: ComfoConnectBridge,
        config_entry: ConfigEntry,
        description: ComfoconnectSelectEntityDescription,
    ) -> None:
        """Initialize the ComfoConnect select entity."""
        self._ccb = ccb
        self.entity_description = description
        self._fail_count = 0
        self._sensor_values: dict[int, Any] = {}
        # Always poll the authoritative RMI value (slowly, see SCAN_INTERVAL).
        # Sensor-backed selects additionally receive push updates for
        # responsiveness; the poll corrects stale values pushed on reconnect.
        self._attr_should_poll = True
        self._attr_unique_id = f"{self._ccb.uuid}-{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._ccb.uuid)},
        )

    async def async_added_to_hass(self) -> None:
        """Register for sensor updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_COMFOCONNECT_AVAILABLE.format(self._ccb.uuid),
                self._handle_availability_update,
            )
        )

        for sensor in self.entity_description.sensors:
            _LOGGER.debug("Registering for sensor %s (%d)", sensor.name, sensor.id)
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    SIGNAL_COMFOCONNECT_UPDATE_RECEIVED.format(self._ccb.uuid, sensor.id),
                    partial(self._handle_update, sensor),
                )
            )
            await self._ccb.register_sensor(sensor)

    def _handle_availability_update(self, available: bool) -> None:
        """Handle availability updates."""
        was_available = self._attr_available
        self._attr_available = available
        if available and was_available is False:
            # After a reconnect the bridge can push stale initial values
            # (e.g. temperature profile reverting to "normal"); re-read the
            # authoritative value via the RMI getter to correct it.
            self.schedule_update_ha_state(force_refresh=True)
        else:
            self.schedule_update_ha_state()

    def _handle_update(self, sensor: AioComfoConnectSensor, value):
        """Handle update callbacks."""
        _LOGGER.debug("Handle update for sensor %s (%s): %s", sensor.name, sensor.id, value)
        self._sensor_values[sensor.id] = value
        if len(self._sensor_values) < len(self.entity_description.sensors):
            return

        option = self.entity_description.sensor_value_fn(self._sensor_values)
        if option is None:
            # Unknown or transient value: keep the current option.
            return
        self._attr_current_option = option
        self.schedule_update_ha_state()

    async def async_update(self) -> None:
        """Update the state."""
        try:
            value = await self.entity_description.get_value_fn(self._ccb)
        except (AioComfoConnectTimeout, AioComfoConnectNotConnected, ComfoConnectError, AttributeError, ValueError) as err:
            # Bridge did not (properly) answer the polled RMI request. Tolerate a
            # few transient failures (keeping the last value), but mark the entity
            # unavailable once they persist.
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

        # Successful poll: clear the failure streak and restore availability.
        self._fail_count = 0
        self._sensor_values: dict[int, Any] = {}
        self._attr_available = True
        if value is not None:
            self._attr_current_option = value

    async def async_select_option(self, option: str) -> None:
        """Set the selected option."""
        try:
            await self.entity_description.set_value_fn(self._ccb, option)
        except (AioComfoConnectNotConnected, AioComfoConnectTimeout) as err:
            raise HomeAssistantError(f"Not connected to ComfoConnect bridge: {err}") from err
        except ComfoConnectError as err:
            raise HomeAssistantError(f"Failed to set {self.entity_description.name}: {err}") from err
        self._attr_current_option = option
        self.schedule_update_ha_state()
