# Home Assistant Zehnder ComfoAir Q integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A custom integration for Home Assistant to control and monitor a Zehnder ComfoAir Q ventilation unit through a ComfoConnect LAN C bridge. It uses the
[aiocomfoconnect](https://github.com/michaelarnauts/aiocomfoconnect) library.

> **Based on [michaelarnauts/home-assistant-comfoconnect](https://github.com/michaelarnauts/home-assistant-comfoconnect).**
> This is a fork of the integration by Michaël Arnauts, who also wrote the aiocomfoconnect library. All credit for the original integration and the
> protocol work goes to him. This fork adds more entities, aligns the behaviour with the official Zehnder ComfoControl app and makes the connection to the
> bridge more robust (see [Differences from the original](#differences-from-the-original)).

## Features

### Control

* Ventilation speed (away / low / medium / high) and mode (auto / manual)
* Boost (10 – 60 minutes, or off)
* Bypass mode, balance mode, temperature profile and ComfoCool mode
* Season detection: heating and cooling season RMOT limits, and "start heating/cooling season now" buttons
* Clear the errors of the unit

### Sensors

* Temperatures and humidity of the extract, exhaust, outdoor and supply air
* Airflow (in the unit that is configured on the ventilation unit), fan speeds and duty cycles
* Power and energy usage of the ventilation and the preheater, and the avoided heating and cooling
* Bypass state and valve positions
* RMOT (running mean outdoor temperature), target temperature and whether the heating / cooling season is active
* Days until the filter needs to be replaced
* Operating state, alarm, the active ventilation timer (preset, manual, boost, away, …) and its remaining time
* Sensor based ventilation, analog inputs, ComfoFond, ComfoCool and CO₂ sensors (per zone)

**Note: Not all sensors are enabled by default. You can enable them on the integration page.**

### Behaviour

* Configurable through the UI, with support for multiple bridges
* Changes to the fan speed won't be reverted after 2 hours
* Ignores the invalid sensor values the bridge sends at the start of a session (workaround for a bridge firmware bug)
* Throttles high frequency sensor updates (airflow & fan duty) to once every 10 seconds
* Entities go unavailable when the bridge connection is lost, and recover automatically on reconnect

## Differences from the original

* **Aligned with the ComfoControl app.** The meaning of the values and the commands are taken from the official Zehnder ComfoControl Android app:
  * The ComfoCool select follows the ComfoCool mode instead of the compressor state, and switching ComfoCool off sends the value the app uses.
  * Changing the fan speed first cancels a running boost, away or cooker hood timer, like the app does, so the change takes effect immediately.
  * Boost and balance mode are updated live instead of only by polling.
  * Airflow sensors use the unit that is configured on the ventilation unit (m³/h, l/s or kg/h).
* **Season detection.** The heating and cooling season RMOT limits can be set from Home Assistant, as in the Season detection screen of the app.
* **More robust connection.** The integration runs its own reconnect loop, which keeps trying when the bridge refuses a session, stops cleanly when the
  integration is reloaded, retries the setup when the bridge is unreachable at startup, and doesn't drop the connection for a single unanswered request.
* **More entities**, like the operating state, alarm, active ventilation timer, target temperature, valve positions, avoided heating and cooling, yearly
  energy usage and CO₂ per zone.

## Installation

### HACS

The easiest way to install this integration is through [HACS](https://hacs.xyz/).

1. Add this repository (`https://github.com/laurensdehoorne/home-assistant-comfoconnect`) as a custom repository in HACS.
   See [here](https://hacs.xyz/docs/faq/custom_repositories) for more information.
2. Install the `Zehnder ComfoAirQ` integration.
3. Restart Home Assistant.

This integration uses the same `comfoconnect` domain as the original custom integration and the built-in Home Assistant integration, so it replaces them:
remove the original custom integration first if you have it installed.

If you have the built-in `comfoconnect` integration configured in `configuration.yaml`, the configuration should be picked up, but you might need to change
your existing sensor ids. You should also remove the old configuration from the `configuration.yaml` file.

If not, you can add the integration through the UI by going to the integrations page and adding the `Zehnder ComfoAirQ` integration.

## Credits

* [Michaël Arnauts](https://github.com/michaelarnauts) for the original [home-assistant-comfoconnect](https://github.com/michaelarnauts/home-assistant-comfoconnect)
  integration and the [aiocomfoconnect](https://github.com/michaelarnauts/aiocomfoconnect) library.
* Everyone who contributed to the original integration.
