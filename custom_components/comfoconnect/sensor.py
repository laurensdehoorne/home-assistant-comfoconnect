"""Sensor for the ComfoConnect integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from aiocomfoconnect.sensors import (
    SENSOR_AIRFLOW_CONSTRAINTS,
    SENSOR_ANALOG_INPUT_1,
    SENSOR_ANALOG_INPUT_2,
    SENSOR_ANALOG_INPUT_3,
    SENSOR_ANALOG_INPUT_4,
    SENSOR_AVOIDED_COOLING,
    SENSOR_AVOIDED_COOLING_TOTAL,
    SENSOR_AVOIDED_COOLING_TOTAL_YEAR,
    SENSOR_AVOIDED_HEATING,
    SENSOR_AVOIDED_HEATING_TOTAL,
    SENSOR_AVOIDED_HEATING_TOTAL_YEAR,
    SENSOR_BYPASS_STATE,
    SENSOR_COMFOCOOL_CONDENSOR_TEMP,
    SENSOR_COMFOFOND_GHE_STATE,
    SENSOR_COMFOFOND_TEMP_GROUND,
    SENSOR_COMFOFOND_TEMP_OUTDOOR,
    SENSOR_COMFORTCONTROL_MODE,
    SENSOR_DAYS_TO_REPLACE_FILTER,
    SENSOR_DEVICE_STATE,
    SENSOR_FAN_EXHAUST_DUTY,
    SENSOR_FAN_EXHAUST_FLOW,
    SENSOR_FAN_EXHAUST_SPEED,
    SENSOR_FAN_SPEED_MODE,
    SENSOR_FAN_SUPPLY_DUTY,
    SENSOR_FAN_SUPPLY_FLOW,
    SENSOR_FAN_SUPPLY_SPEED,
    SENSOR_HUMIDITY_EXHAUST,
    SENSOR_HUMIDITY_EXTRACT,
    SENSOR_HUMIDITY_OUTDOOR,
    SENSOR_HUMIDITY_SUPPLY,
    SENSOR_NEXT_CHANGE_FAN,
    SENSOR_OPERATING_MODE_2,
    SENSOR_POWER_USAGE,
    SENSOR_POWER_USAGE_TOTAL,
    SENSOR_POWER_USAGE_TOTAL_YEAR,
    SENSOR_PREHEATER_POWER,
    SENSOR_PREHEATER_POWER_TOTAL,
    SENSOR_PREHEATER_POWER_TOTAL_YEAR,
    SENSOR_RMOT,
    SENSOR_TARGET_TEMPERATURE,
    SENSOR_TEMPERATURE_EXHAUST,
    SENSOR_TEMPERATURE_EXTRACT,
    SENSOR_TEMPERATURE_OUTDOOR,
    SENSOR_TEMPERATURE_SUPPLY,
    SENSORS,
)
from aiocomfoconnect.sensors import (
    Sensor as AioComfoConnectSensor,
)
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import Throttle

from . import DOMAIN, SIGNAL_COMFOCONNECT_AVAILABLE, SIGNAL_COMFOCONNECT_UPDATE_RECEIVED, ComfoConnectBridge
from .pdo import (
    FLOW_UNITS,
    OPERATING_STATES,
    PRESET_TIMERS,
    SENSOR_CO2_ZONE_BASE,
    SENSOR_FLOW_UNIT,
    SENSOR_VALVE_BYPASS_POSITION,
    SENSOR_VALVE_EXTRACT_POSITION,
    SENSOR_VENTILATION_STATES,
)
from .pdo import SENSORS as EXTRA_SENSORS

_LOGGER = logging.getLogger(__name__)

MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=10)


@dataclass
class ComfoconnectRequiredKeysMixin:
    """Mixin for required keys."""

    ccb_sensor: AioComfoConnectSensor


@dataclass
class ComfoconnectSensorEntityDescription(SensorEntityDescription, ComfoconnectRequiredKeysMixin):
    """Describes ComfoConnect sensor entity."""

    throttle: bool = False
    mapping: Callable = None
    # Ignore a pushed value of 0 (the bridge emits a spurious 0 for many
    # sensors right after a reconnect). Auto-enabled for temperature/humidity.
    ignore_zero: bool = False
    # The value is an airflow in the unit configured on the unit (PDO 224).
    flow_unit: bool = False


SENSOR_TYPES = (
    ComfoconnectSensorEntityDescription(
        key=SENSOR_TEMPERATURE_EXTRACT,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        name="Inside temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ccb_sensor=SENSORS.get(SENSOR_TEMPERATURE_EXTRACT),
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_HUMIDITY_EXTRACT,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        name="Inside humidity",
        native_unit_of_measurement=PERCENTAGE,
        ccb_sensor=SENSORS.get(SENSOR_HUMIDITY_EXTRACT),
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_RMOT,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        name="Current RMOT",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ccb_sensor=SENSORS.get(SENSOR_RMOT),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_TEMPERATURE_OUTDOOR,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        name="Outside temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ccb_sensor=SENSORS.get(SENSOR_TEMPERATURE_OUTDOOR),
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_HUMIDITY_OUTDOOR,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        name="Outside humidity",
        native_unit_of_measurement=PERCENTAGE,
        ccb_sensor=SENSORS.get(SENSOR_HUMIDITY_OUTDOOR),
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_TEMPERATURE_SUPPLY,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        name="Supply temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ccb_sensor=SENSORS.get(SENSOR_TEMPERATURE_SUPPLY),
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_HUMIDITY_SUPPLY,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        name="Supply humidity",
        native_unit_of_measurement=PERCENTAGE,
        ccb_sensor=SENSORS.get(SENSOR_HUMIDITY_SUPPLY),
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_FAN_SPEED_MODE,
        state_class=SensorStateClass.MEASUREMENT,
        name="Fan speed level",
        icon="mdi:fan",
        ccb_sensor=SENSORS.get(SENSOR_FAN_SPEED_MODE),
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_FAN_SUPPLY_SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        name="Supply fan speed",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        icon="mdi:fan-plus",
        ccb_sensor=SENSORS.get(SENSOR_FAN_SUPPLY_SPEED),
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_FAN_SUPPLY_DUTY,
        state_class=SensorStateClass.MEASUREMENT,
        name="Supply fan duty",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:fan-plus",
        ccb_sensor=SENSORS.get(SENSOR_FAN_SUPPLY_DUTY),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
        ignore_zero=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_FAN_EXHAUST_SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        name="Exhaust fan speed",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        icon="mdi:fan-minus",
        ccb_sensor=SENSORS.get(SENSOR_FAN_EXHAUST_SPEED),
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_FAN_EXHAUST_DUTY,
        state_class=SensorStateClass.MEASUREMENT,
        name="Exhaust fan duty",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:fan-minus",
        ccb_sensor=SENSORS.get(SENSOR_FAN_EXHAUST_DUTY),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
        ignore_zero=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_TEMPERATURE_EXHAUST,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        name="Exhaust temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ccb_sensor=SENSORS.get(SENSOR_TEMPERATURE_EXHAUST),
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_HUMIDITY_EXHAUST,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        name="Exhaust humidity",
        native_unit_of_measurement=PERCENTAGE,
        ccb_sensor=SENSORS.get(SENSOR_HUMIDITY_EXHAUST),
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_FAN_SUPPLY_FLOW,
        state_class=SensorStateClass.MEASUREMENT,
        name="Supply airflow",
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        icon="mdi:fan-plus",
        ccb_sensor=SENSORS.get(SENSOR_FAN_SUPPLY_FLOW),
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
        flow_unit=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_FAN_EXHAUST_FLOW,
        state_class=SensorStateClass.MEASUREMENT,
        name="Exhaust airflow",
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        icon="mdi:fan-minus",
        ccb_sensor=SENSORS.get(SENSOR_FAN_EXHAUST_FLOW),
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
        flow_unit=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_BYPASS_STATE,
        state_class=SensorStateClass.MEASUREMENT,
        name="Bypass state",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:camera-iris",
        ccb_sensor=SENSORS.get(SENSOR_BYPASS_STATE),
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_DAYS_TO_REPLACE_FILTER,
        name="Days to replace filter",
        native_unit_of_measurement=UnitOfTime.DAYS,
        icon="mdi:calendar",
        ccb_sensor=SENSORS.get(SENSOR_DAYS_TO_REPLACE_FILTER),
        entity_category=EntityCategory.DIAGNOSTIC,
        ignore_zero=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_POWER_USAGE,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        name="Ventilation current power usage",
        native_unit_of_measurement=UnitOfPower.WATT,
        ccb_sensor=SENSORS.get(SENSOR_POWER_USAGE),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
        ignore_zero=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_POWER_USAGE_TOTAL,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        name="Ventilation total energy usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ccb_sensor=SENSORS.get(SENSOR_POWER_USAGE_TOTAL),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
        ignore_zero=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_PREHEATER_POWER,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        name="Preheater current power usage",
        native_unit_of_measurement=UnitOfPower.WATT,
        ccb_sensor=SENSORS.get(SENSOR_PREHEATER_POWER),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_PREHEATER_POWER_TOTAL,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        name="Preheater total energy usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ccb_sensor=SENSORS.get(SENSOR_PREHEATER_POWER_TOTAL),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_ANALOG_INPUT_1,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        name="Analog Input 1",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        ccb_sensor=SENSORS.get(SENSOR_ANALOG_INPUT_1),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_ANALOG_INPUT_2,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        name="Analog Input 2",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        ccb_sensor=SENSORS.get(SENSOR_ANALOG_INPUT_2),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_ANALOG_INPUT_3,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        name="Analog Input 3",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        ccb_sensor=SENSORS.get(SENSOR_ANALOG_INPUT_3),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_ANALOG_INPUT_4,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        name="Analog Input 4",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        ccb_sensor=SENSORS.get(SENSOR_ANALOG_INPUT_4),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_AIRFLOW_CONSTRAINTS,
        icon="mdi:fan-alert",
        name="Airflow Constraint",
        ccb_sensor=SENSORS.get(SENSOR_AIRFLOW_CONSTRAINTS),
        entity_category=EntityCategory.DIAGNOSTIC,
        mapping=lambda x: ", ".join(x) if x else "None",
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_COMFOFOND_GHE_STATE,
        state_class=SensorStateClass.MEASUREMENT,
        name="ComfoFond GHE state",
        native_unit_of_measurement=PERCENTAGE,
        ccb_sensor=SENSORS.get(SENSOR_COMFOFOND_GHE_STATE),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_COMFOFOND_TEMP_GROUND,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        name="ComfoFond ground temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ccb_sensor=SENSORS.get(SENSOR_COMFOFOND_TEMP_GROUND),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_COMFOFOND_TEMP_OUTDOOR,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        name="ComfoFond outdoor air temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ccb_sensor=SENSORS.get(SENSOR_COMFOFOND_TEMP_OUTDOOR),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_COMFOCOOL_CONDENSOR_TEMP,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        name="ComfoCool condensor temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ccb_sensor=SENSORS.get(SENSOR_COMFOCOOL_CONDENSOR_TEMP),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_DEVICE_STATE,
        device_class=SensorDeviceClass.ENUM,
        translation_key="operating_state",
        name="Operating state",
        icon="mdi:state-machine",
        options=list(OPERATING_STATES.values()),
        ccb_sensor=SENSORS.get(SENSOR_DEVICE_STATE),
        entity_category=EntityCategory.DIAGNOSTIC,
        mapping=OPERATING_STATES.get,
        # 0 (initialization) is also what the bridge pushes right after a reconnect.
        ignore_zero=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_OPERATING_MODE_2,
        device_class=SensorDeviceClass.ENUM,
        translation_key="preset_timer",
        name="Active ventilation timer",
        icon="mdi:timer-cog-outline",
        options=list(PRESET_TIMERS.values()),
        ccb_sensor=SENSORS.get(SENSOR_OPERATING_MODE_2),
        entity_category=EntityCategory.DIAGNOSTIC,
        mapping=PRESET_TIMERS.get,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_NEXT_CHANGE_FAN,
        device_class=SensorDeviceClass.DURATION,
        name="Active ventilation timer remaining",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        icon="mdi:timer-sand",
        ccb_sensor=SENSORS.get(SENSOR_NEXT_CHANGE_FAN),
        entity_category=EntityCategory.DIAGNOSTIC,
        # -1 means the timer runs indefinitely.
        mapping=lambda x: None if x < 0 else x,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_COMFORTCONTROL_MODE,
        device_class=SensorDeviceClass.ENUM,
        translation_key="sensor_ventilation",
        name="Sensor based ventilation",
        icon="mdi:leaf",
        options=list(SENSOR_VENTILATION_STATES.values()),
        ccb_sensor=SENSORS.get(SENSOR_COMFORTCONTROL_MODE),
        entity_category=EntityCategory.DIAGNOSTIC,
        mapping=SENSOR_VENTILATION_STATES.get,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_TARGET_TEMPERATURE,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        name="Target temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ccb_sensor=SENSORS.get(SENSOR_TARGET_TEMPERATURE),
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_VALVE_BYPASS_POSITION,
        state_class=SensorStateClass.MEASUREMENT,
        name="Bypass valve position",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:valve",
        ccb_sensor=EXTRA_SENSORS.get(SENSOR_VALVE_BYPASS_POSITION),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_VALVE_EXTRACT_POSITION,
        state_class=SensorStateClass.MEASUREMENT,
        name="Extract valve position",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:valve",
        ccb_sensor=EXTRA_SENSORS.get(SENSOR_VALVE_EXTRACT_POSITION),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_POWER_USAGE_TOTAL_YEAR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        name="Ventilation energy usage this year",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ccb_sensor=SENSORS.get(SENSOR_POWER_USAGE_TOTAL_YEAR),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_PREHEATER_POWER_TOTAL_YEAR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        name="Preheater energy usage this year",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ccb_sensor=SENSORS.get(SENSOR_PREHEATER_POWER_TOTAL_YEAR),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_AVOIDED_HEATING,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        name="Avoided heating power",
        native_unit_of_measurement=UnitOfPower.WATT,
        ccb_sensor=SENSORS.get(SENSOR_AVOIDED_HEATING),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_AVOIDED_HEATING_TOTAL_YEAR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        name="Avoided heating energy this year",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ccb_sensor=SENSORS.get(SENSOR_AVOIDED_HEATING_TOTAL_YEAR),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_AVOIDED_HEATING_TOTAL,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        name="Avoided heating energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ccb_sensor=SENSORS.get(SENSOR_AVOIDED_HEATING_TOTAL),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
        ignore_zero=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_AVOIDED_COOLING,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        name="Avoided cooling power",
        native_unit_of_measurement=UnitOfPower.WATT,
        ccb_sensor=SENSORS.get(SENSOR_AVOIDED_COOLING),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_AVOIDED_COOLING_TOTAL_YEAR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        name="Avoided cooling energy this year",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ccb_sensor=SENSORS.get(SENSOR_AVOIDED_COOLING_TOTAL_YEAR),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
    ),
    ComfoconnectSensorEntityDescription(
        key=SENSOR_AVOIDED_COOLING_TOTAL,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        name="Avoided cooling energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ccb_sensor=SENSORS.get(SENSOR_AVOIDED_COOLING_TOTAL),
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        throttle=True,
        ignore_zero=True,
    ),
    *(
        ComfoconnectSensorEntityDescription(
            key=SENSOR_CO2_ZONE_BASE + zone,
            device_class=SensorDeviceClass.CO2,
            state_class=SensorStateClass.MEASUREMENT,
            name=f"CO2 zone {zone}",
            native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
            ccb_sensor=EXTRA_SENSORS.get(SENSOR_CO2_ZONE_BASE + zone),
            entity_registry_enabled_default=False,
            throttle=True,
            ignore_zero=True,
        )
        for zone in range(1, 9)
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ComfoConnect sensors."""
    ccb = hass.data[DOMAIN][config_entry.entry_id]

    sensors = [ComfoConnectSensor(ccb=ccb, config_entry=config_entry, description=description) for description in SENSOR_TYPES]

    async_add_entities(sensors, True)


class ComfoConnectSensor(RestoreSensor):
    """Representation of a ComfoConnect sensor."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    entity_description: ComfoconnectSensorEntityDescription

    def __init__(
        self,
        ccb: ComfoConnectBridge,
        config_entry: ConfigEntry,
        description: ComfoconnectSensorEntityDescription,
    ) -> None:
        """Initialize the ComfoConnect sensor."""
        self._ccb = ccb
        self.entity_description = description
        self._attr_unique_id = f"{self._ccb.uuid}-{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._ccb.uuid)},
        )

    async def async_added_to_hass(self) -> None:
        """Register for sensor updates."""
        _LOGGER.debug(
            "Registering for sensor %s (%d)",
            self.entity_description.name,
            self.entity_description.key,
        )

        # Restore the last known value so the sensor isn't "unknown" at boot
        # until the bridge pushes a fresh value (some PDOs update rarely).
        if (last_data := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = last_data.native_value

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_COMFOCONNECT_AVAILABLE.format(self._ccb.uuid),
                self._handle_availability_update,
            )
        )

        # If the sensor should be throttled, pass it through the Throttle utility
        if self.entity_description.throttle:
            update_handler = Throttle(MIN_TIME_BETWEEN_UPDATES)(self._handle_update)
        else:
            update_handler = self._handle_update

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_COMFOCONNECT_UPDATE_RECEIVED.format(self._ccb.uuid, self.entity_description.key),
                update_handler,
            )
        )
        await self._ccb.register_sensor(self.entity_description.ccb_sensor)

        if self.entity_description.flow_unit:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    SIGNAL_COMFOCONNECT_UPDATE_RECEIVED.format(self._ccb.uuid, SENSOR_FLOW_UNIT),
                    self._handle_flow_unit_update,
                )
            )
            await self._ccb.register_sensor(EXTRA_SENSORS[SENSOR_FLOW_UNIT])

    def _handle_flow_unit_update(self, value: int) -> None:
        """Use the airflow unit that is configured on the unit."""
        if (unit := FLOW_UNITS.get(value)) is None or unit == self.native_unit_of_measurement:
            return
        self._attr_native_unit_of_measurement = unit
        self.schedule_update_ha_state()

    def _handle_availability_update(self, available: bool) -> None:
        """Handle availability updates."""
        self._attr_available = available
        self.schedule_update_ha_state()

    def _handle_update(self, value):
        """Handle update callbacks."""
        _LOGGER.debug(
            "Handle update for sensor %s (%d): %s",
            self.entity_description.name,
            self.entity_description.key,
            value,
        )

        # The bridge pushes a spurious 0 for many sensors right after a
        # reconnect. Drop it (keeping the last value) for sensors where 0 is
        # implausible, so the value isn't corrupted until the next real push.
        ignore_zero = self.entity_description.ignore_zero or self.device_class in (
            SensorDeviceClass.TEMPERATURE,
            SensorDeviceClass.HUMIDITY,
        )
        if ignore_zero and value == 0:
            _LOGGER.debug("Ignoring spurious 0 for %s", self.entity_description.name)
            return

        if self.entity_description.mapping:
            self._attr_native_value = self.entity_description.mapping(value)
        else:
            self._attr_native_value = value
        self.schedule_update_ha_state()
