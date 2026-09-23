"""
PDO definitions that are missing or incorrect in aiocomfoconnect.

The ids, types and value meanings below are taken from the official Zehnder
ComfoControl app (CNRPDORegister and the HRU*Object enums).
"""

from __future__ import annotations

from aiocomfoconnect.const import PdoType
from aiocomfoconnect.sensors import Sensor

# ventilationUnit_alarm: an error is active on the ventilation unit.
SENSOR_ALARM = 17

# comfoCoolOffTimerSchedule_activityValue: 0 = auto, 1 = off.
# (PDO 784 is the compressor state, not the ComfoCool mode.)
SENSOR_COMFOCOOL_MODE = 69

# exhaustFanF12TimerSchedule_activityValue / supplyFanF22TimerSchedule_activityValue:
# 0 = auto, 1 = off. aiocomfoconnect names these the other way around
# (SENSOR_FAN_MODE_SUPPLY = 70, SENSOR_FAN_MODE_EXHAUST = 71).
SENSOR_EXHAUST_FAN_TIMER = 70
SENSOR_SUPPLY_FAN_TIMER = 71

# v11ExtractValve_position / v21BypassPreheaterValve_position, in 0.1 %.
SENSOR_VALVE_EXTRACT_POSITION = 97
SENSOR_VALVE_BYPASS_POSITION = 98

# ventilationConfiguration_flowUnit: 1 = kg/h, 2 = l/s, 3 = m³/h.
# aiocomfoconnect maps everything that isn't 3 to l/s, so we read it raw.
SENSOR_FLOW_UNIT = 224

# co2Sensor_co2Level1..8: CO₂ level per zone, in ppm.
SENSOR_CO2_ZONE_BASE = 1023

SENSORS = {
    SENSOR_ALARM: Sensor("Alarm", None, SENSOR_ALARM, PdoType.TYPE_CN_BOOL, bool),
    SENSOR_COMFOCOOL_MODE: Sensor("ComfoCool Mode", None, SENSOR_COMFOCOOL_MODE, PdoType.TYPE_CN_UINT8),
    SENSOR_VALVE_EXTRACT_POSITION: Sensor("Extract Valve Position", "%", SENSOR_VALVE_EXTRACT_POSITION, PdoType.TYPE_CN_UINT16, lambda x: x / 10),
    SENSOR_VALVE_BYPASS_POSITION: Sensor("Bypass Valve Position", "%", SENSOR_VALVE_BYPASS_POSITION, PdoType.TYPE_CN_UINT16, lambda x: x / 10),
    SENSOR_EXHAUST_FAN_TIMER: Sensor("Exhaust Fan Timer", None, SENSOR_EXHAUST_FAN_TIMER, PdoType.TYPE_CN_UINT8),
    SENSOR_SUPPLY_FAN_TIMER: Sensor("Supply Fan Timer", None, SENSOR_SUPPLY_FAN_TIMER, PdoType.TYPE_CN_UINT8),
    SENSOR_FLOW_UNIT: Sensor("Flow Unit", None, SENSOR_FLOW_UNIT, PdoType.TYPE_CN_UINT8),
}
SENSORS.update(
    {SENSOR_CO2_ZONE_BASE + zone: Sensor(f"CO2 Zone {zone}", "ppm", SENSOR_CO2_ZONE_BASE + zone, PdoType.TYPE_CN_UINT16) for zone in range(1, 9)}
)

# presetTimerSchedule_activeTimerID (PDO 49): the timer that currently
# determines the ventilation preset.
PRESET_TIMER_NONE = -1
PRESET_TIMERS = {
    PRESET_TIMER_NONE: "none",
    1: "preset",
    2: "preset_rf",
    3: "preset_analog",
    4: "preset_rf_analog",
    5: "manual",
    6: "boost",
    7: "boost_rf",
    8: "boost_switch",
    11: "away",
}
BOOST_TIMERS = (6, 7, 8)

# ventilationUnit_mode (PDO 16).
OPERATING_STATES = {
    0: "initialization",
    1: "normal",
    2: "filter_wizard",
    3: "commissioning_wizard",
    4: "supplier_factory",
    5: "zehnder_factory",
    6: "standby",
    7: "away",
    8: "dfc",
}

# ventilationConfiguration_sensorPresence (PDO 225).
SENSOR_VENTILATION_STATES = {
    0: "disabled",
    1: "active",
    2: "overruling",
}

# ventilationConfiguration_flowUnit (PDO 224) to Home Assistant units.
FLOW_UNITS = {
    1: "kg/h",
    2: "L/s",
    3: "m³/h",
}

# TEMPHUMCONTROL properties limitRMOTHeating / limitRMOTCooling, in 0.1 °C.
# Heating season is active while the RMOT is below the heating limit, cooling
# season while the RMOT is above the cooling limit.
PROPERTY_RMOT_LIMIT_HEATING = 2
PROPERTY_RMOT_LIMIT_COOLING = 3

# FILTER (unit 0x1C) RMI methods, as used by the filter wizard of the app.
FILTER_BEGIN_REPLACEMENT = 0x80
FILTER_END_REPLACEMENT = 0x81
FILTER_ABORT_REPLACEMENT = 0x82

# FILTER property filterLifeDays (UINT16), with the choices the app offers.
PROPERTY_FILTER_LIFE_DAYS = 2
FILTER_LIFE_DAYS_MIN = 60
FILTER_LIFE_DAYS_MAX = 180
FILTER_LIFE_DAYS_STEP = 10

# TEMPHUMCONTROL property temperaturePassivePreset (firmware R1.9.0 and newer):
# how fast the unit reacts to favourable conditions for passive heating/cooling.
PROPERTY_TEMPERATURE_PASSIVE_PRESET = 14
TEMPERATURE_PASSIVE_PRESETS = {
    0: "slow",
    1: "medium",
    2: "fast",
}

# NODECONFIGURATION property installerPinCode (a 4 digit string). The unit
# doesn't check it: the app reads it and compares it with the PIN you enter.
PROPERTY_INSTALLER_PIN = 3

# VENTILATIONCONFIG properties preset0Flow..preset3Flow (UINT16): the airflow
# of the away / low / medium / high preset. Installer menu only in the app.
PROPERTY_PRESET_FLOWS = {
    "away": 3,
    "low": 4,
    "medium": 5,
    "high": 6,
}
