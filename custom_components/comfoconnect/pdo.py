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
