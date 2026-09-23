"""Support to control a Zehnder ComfoAir Q350/450/600 ventilation unit."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import struct
from dataclasses import dataclass
from datetime import timedelta

from aiocomfoconnect import ComfoConnect, discover_bridges
from aiocomfoconnect import bridge as aiocomfoconnect_bridge
from aiocomfoconnect.bridge import EventBus, Message
from aiocomfoconnect.const import (
    SUBUNIT_01,
    SUBUNIT_05,
    UNIT_FILTER,
    UNIT_NODECONFIGURATION,
    UNIT_SCHEDULE,
    UNIT_TEMPHUMCONTROL,
    UNIT_VENTILATIONCONFIG,
    ComfoCoolMode,
    PdoType,
)
from aiocomfoconnect.exceptions import (
    AioComfoConnectNotConnected,
    AioComfoConnectTimeout,
    ComfoConnectError,
    ComfoConnectNotAllowed,
)
from aiocomfoconnect.properties import (
    PROPERTY_FIRMWARE_VERSION,
    PROPERTY_MODEL,
    PROPERTY_NAME,
)
from aiocomfoconnect.protobuf import zehnder_pb2
from aiocomfoconnect.sensors import SENSOR_RMOT, Sensor
from aiocomfoconnect.util import bytestring, version_decode
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .const import CONF_INSTALLER_PIN, CONF_LOCAL_UUID, CONF_UUID, DOMAIN
from .pdo import PROPERTY_FILTER_LIFE_DAYS, PROPERTY_INSTALLER_PIN, PROPERTY_TEMPERATURE_PASSIVE_PRESET

PLATFORMS: list[Platform] = [
    Platform.FAN,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.BUTTON,
]

_LOGGER = logging.getLogger(__name__)

SIGNAL_COMFOCONNECT_UPDATE_RECEIVED = "comfoconnect_update_{}_{}"
SIGNAL_COMFOCONNECT_AVAILABLE = "comfoconnect_available_{}"
SIGNAL_COMFOCONNECT_RMOT_LIMIT = "comfoconnect_rmot_limit_{}_{}"

# The ComfoControl app probes every 5 seconds and drops the connection after
# 10 seconds of silence. We probe a bit less often, but often enough to notice
# a dead connection quickly.
KEEP_ALIVE_INTERVAL = timedelta(seconds=10)

# Schedule subunit and timer ids, as used by the ComfoControl app
# (VentilationUnit.SchedulerInstances and HRUScheduleObject.*Timers).
SUBUNIT_HOOD = 0x09
TIMER_PRESET_BOOST = 0x06
TIMER_PRESET_BOOST_RF = 0x07
TIMER_PRESET_AWAY = 0x0B
TIMER_HOOD = 0x01

# Value of the ComfoCool-off timer that switches ComfoCool off
# (HRUScheduleObject.ScheduleComfoCoolValues: AUTO = 0, OFF = 1).
COMFOCOOL_VALUE_OFF = 0x01

# Time to wait before connecting again after the connection failed or was lost.
RECONNECT_DELAY = 5

# How long the first connection may take before the setup is retried later.
INITIAL_CONNECT_TIMEOUT = 30


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Zehnder ComfoConnect integration from yaml."""
    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data=config[DOMAIN],
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zehnder ComfoConnect from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    try:
        bridge = ComfoConnectBridge(hass, entry.data[CONF_HOST], entry.data[CONF_UUID])
        await bridge.connect(entry.data[CONF_LOCAL_UUID])

    except ComfoConnectNotAllowed:
        raise ConfigEntryAuthFailed("Access denied")

    except ComfoConnectError as err:
        raise ConfigEntryError from err

    except AioComfoConnectTimeout as err:
        # We got a timeout, this can happen when the IP address of the bridge has changed.
        _LOGGER.warning(
            'Timeout connecting to bridge "%s", trying discovery again.',
            entry.data[CONF_HOST],
        )

        bridges = await discover_bridges()
        discovered_bridge = next((b for b in bridges if b.uuid == entry.data[CONF_UUID]), None)
        if not discovered_bridge:
            _LOGGER.warning('Unable to discover bridge "%s". Retrying later.', entry.data[CONF_UUID])
            raise ConfigEntryNotReady from err

        # Try again, with the updated host this time
        bridge = ComfoConnectBridge(hass, discovered_bridge.host, entry.data[CONF_UUID])
        try:
            await bridge.connect(entry.data[CONF_LOCAL_UUID])

            # Update the host in the config entry
            hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_HOST: discovered_bridge.host})

        except ComfoConnectNotAllowed:
            raise ConfigEntryAuthFailed("Access denied")

        except (AioComfoConnectTimeout, ComfoConnectError) as err:
            raise ConfigEntryNotReady from err

    hass.data[DOMAIN][entry.entry_id] = bridge

    # Get device information
    try:
        bridge_info = await bridge.cmd_version_request()
        unit_model = await bridge.get_property(PROPERTY_MODEL)
        unit_firmware = await bridge.get_property(PROPERTY_FIRMWARE_VERSION)
        unit_name = await bridge.get_property(PROPERTY_NAME)
    except (AioComfoConnectNotConnected, AioComfoConnectTimeout) as err:
        # Bridge connected but did not answer device-info requests in time.
        # Retry setup later instead of failing the integration outright.
        await bridge.disconnect()
        hass.data[DOMAIN].pop(entry.entry_id)
        raise ConfigEntryNotReady("Timeout while reading device information") from err

    device_registry = dr.async_get(hass)

    # Add Bridge to device registry
    bridge_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, bridge_info.serialNumber)},
        manufacturer="Zehnder",
        name="ComfoConnect LAN C Bridge",
        model="ComfoConnect LAN C",
        serial_number=bridge_info.serialNumber,
        sw_version=version_decode(bridge_info.gatewayVersion),
    )

    # Add Ventilation Unit to device registry
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, bridge.uuid)},
        manufacturer="Zehnder",
        name=unit_name,
        model=unit_model,
        sw_version=version_decode(unit_firmware),
        via_device_id=bridge_device.id,
    )

    # Installer settings are only offered when the installer PIN is configured
    # and matches the PIN of the unit (like the installer menu of the app).
    if pin := entry.options.get(CONF_INSTALLER_PIN):
        try:
            bridge.installer_mode = await bridge.check_installer_pin(pin)
        except (AioComfoConnectNotConnected, AioComfoConnectTimeout, ComfoConnectError) as err:
            _LOGGER.warning("Could not check the installer PIN, installer settings are not available: %s", err)
        else:
            if not bridge.installer_mode:
                _LOGGER.warning("The configured installer PIN is not correct, installer settings are not available.")

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload when the options (installer PIN) change.
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    async def send_keepalive(now) -> None:
        """
        Probe the bridge and report availability.

        The bridge restores a lost connection by itself (see
        ComfoConnectBridge._reconnect_loop); we only probe it and update the
        availability of the entities.
        """
        _LOGGER.debug("Sending keepalive...")
        try:
            # Use cmd_time_request as a keepalive since cmd_keepalive doesn't send back a reply we can wait for
            await bridge.cmd_time_request()
        except (AioComfoConnectNotConnected, AioComfoConnectTimeout, ComfoConnectError):
            _LOGGER.debug("Keepalive failed; bridge unavailable (reconnecting in the background).")
            set_available(False)
        else:
            set_available(True)

    last_available: bool | None = None

    def set_available(available: bool) -> None:
        """Notify the entities, but only when the availability changed."""
        nonlocal last_available
        if available == last_available:
            return
        last_available = available
        dispatcher_send(hass, SIGNAL_COMFOCONNECT_AVAILABLE.format(bridge.uuid), available)

    entry.async_on_unload(async_track_time_interval(hass, send_keepalive, KEEP_ALIVE_INTERVAL))

    # Disconnect when shutting down
    async def disconnect_bridge(event):
        """Close connection to the bridge."""
        await bridge.disconnect()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, disconnect_bridge))

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry after its options changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        bridge = hass.data[DOMAIN][entry.entry_id]
        await bridge.disconnect()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


@dataclass
class PropertyRange:
    """A numeric property of the unit, with its allowed range and step."""

    value: float
    minimum: float
    maximum: float
    step: float


class SafeEventBus(EventBus):
    """
    An event bus that tolerates replies we are no longer waiting for.

    The library deletes the listeners of a reference right after emitting, so a
    late or duplicate reply for a reference that has no listeners left raises a
    KeyError. That exception escapes the read loop and kills the reconnect loop,
    leaving the integration offline until Home Assistant is restarted.
    """

    def emit(self, event_name, event):
        """Emit an event to the event bus."""
        for future in self.listeners.pop(event_name, ()):
            if future.done():
                continue
            if isinstance(event, Exception):
                future.set_exception(event)
            else:
                future.set_result(event)


class ComfoConnectBridge(ComfoConnect):
    """Representation of a ComfoConnect bridge."""

    def __init__(self, hass: HomeAssistant, host: str, uuid: str):
        """Initialize the ComfoConnect bridge."""
        super().__init__(
            host,
            uuid,
            sensor_callback=self.sensor_callback,
            alarm_callback=self.alarm_callback,
        )
        self.hass = hass
        self._closing = False
        self._reconnect_task: asyncio.Task | None = None
        self._read_task: asyncio.Task | None = None
        self._last_received = 0.0
        self._session_refused = False
        # Whether installer settings are available (see CONF_INSTALLER_PIN).
        self.installer_mode = False

    async def connect(self, uuid: str) -> None:
        """
        Connect to the bridge and keep the connection alive in the background.

        This replaces the reconnect loop of aiocomfoconnect, which stops for
        good when a reconnect is refused ("invalid state"), keeps reconnecting
        after disconnect(), orphans its read task, and never gives up (nor
        returns) when the bridge is unreachable at startup.
        """
        self._closing = False
        connected = self._loop.create_future()
        self._reconnect_task = self._loop.create_task(self._reconnect_loop(uuid, connected))
        self._reconnect_task.add_done_callback(self._reconnect_task_done)
        try:
            await asyncio.wait_for(asyncio.shield(connected), INITIAL_CONNECT_TIMEOUT)
        except TimeoutError as err:
            connected.cancel()
            await self.disconnect()
            raise AioComfoConnectTimeout(f"Could not connect to the bridge at {self.host}") from err
        except BaseException:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        """Disconnect from the bridge and stop reconnecting."""
        self._closing = True
        if (task := self._reconnect_task) is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._close_connection()

    async def _reconnect_loop(self, uuid: str, connected: asyncio.Future) -> None:
        """(Re)connect to the bridge whenever the connection is lost, until disconnect()."""
        try:
            while not self._closing:
                try:
                    self._read_task = await self._connect(uuid)
                    await self.cmd_start_session(True)
                    self._session_refused = False

                    # Buffer the sensor values for a moment: the bridge sends
                    # invalid values right after connecting (see aiocomfoconnect).
                    if self.sensor_delay:
                        if self._sensor_hold is not None:
                            self._sensor_hold.cancel()
                        self._sensors_values = {}
                        self._sensor_hold = self._loop.call_later(self.sensor_delay, self._unhold_sensors)

                    # Register the sensors (again, when we lost the connection).
                    for sensor in list(self._sensors.values()):
                        await self.cmd_rpdo_request(sensor.id, sensor.type)

                    if not connected.done():
                        connected.set_result(True)

                    # Wait until the connection is lost (or we are closing).
                    await self._read_task

                except ComfoConnectNotAllowed as err:
                    if not connected.done():
                        connected.set_exception(err)
                        return
                    # E.g. while another client holds the session. Keep trying,
                    # but only warn once until a session succeeds again.
                    log = _LOGGER.debug if self._session_refused else _LOGGER.warning
                    log("The bridge refused the session, retrying every %d seconds: %s", RECONNECT_DELAY, err)
                    self._session_refused = True

                except (AioComfoConnectNotConnected, AioComfoConnectTimeout, ComfoConnectError, OSError) as err:
                    _LOGGER.info("The connection to the bridge was lost or could not be made, retrying in %d seconds: %r", RECONNECT_DELAY, err)

                except Exception:
                    _LOGGER.exception("Unexpected error in the connection to the bridge, retrying in %d seconds", RECONNECT_DELAY)

                await self._close_connection()
                if not self._closing:
                    await asyncio.sleep(RECONNECT_DELAY)
        finally:
            if not connected.done():
                connected.set_exception(AioComfoConnectNotConnected("Disconnected before the connection was made"))

    async def _close_connection(self) -> None:
        """Stop the read task and close the socket."""
        read_task, self._read_task = self._read_task, None
        if read_task is not None:
            if not read_task.done():
                read_task.cancel()
            try:
                await read_task
            except (asyncio.CancelledError, Exception) as err:
                # Only retrieve the result, so it isn't reported as never retrieved.
                _LOGGER.debug("The read task ended with: %r", err)
        await self._disconnect()

    @callback
    def _reconnect_task_done(self, task: asyncio.Task) -> None:
        """Report it when the reconnect loop ends by itself (it shouldn't)."""
        if task.cancelled():
            return
        if err := task.exception():
            _LOGGER.error("The connection to the bridge stopped unexpectedly: %r", err)

    async def register_sensor(self, sensor: Sensor):
        """
        Register a sensor on the bridge.

        Unlike aiocomfoconnect, this doesn't reset the last known value of a
        sensor that is already registered (e.g. the RMOT, used by a sensor and
        by the season buttons), and it doesn't fail while the connection is
        down: the reconnect loop registers all sensors when it reconnects.
        """
        self._sensors[sensor.id] = sensor
        self._sensors_values.setdefault(sensor.id, None)
        try:
            await self.cmd_rpdo_request(sensor.id, sensor.type)
        except (AioComfoConnectNotConnected, AioComfoConnectTimeout) as err:
            _LOGGER.debug("Sensor %d will be registered when the connection is back: %r", sensor.id, err)

    def _sensor_callback(self, sensor_id, sensor_value):
        """Process a sensor update, ignoring sensors that we didn't register (yet)."""
        if sensor_id not in self._sensors:
            # The bridge can send updates for PDOs that this client registered
            # in an earlier session, before the entity registered it again.
            _LOGGER.debug("Ignoring an update for unregistered sensor %s", sensor_id)
            return
        super()._sensor_callback(sensor_id, sensor_value)

    async def _send(self, request, request_type, params: dict | None = None, reply: bool = True):
        """
        Send a command and wait for its reply.

        Based on aiocomfoconnect's _send, with two differences:
        - The message reference is reserved before anything is awaited, so
          concurrent requests can't get the same reference (and each other's
          reply).
        - An unanswered request only drops the connection when the bridge sent
          nothing at all while we waited, like the ComfoControl app, which
          treats a connection as lost after a period of silence. Otherwise a
          single slow reply (e.g. while many entities start up) makes us
          reconnect and register everything again.
        """
        if not self.is_connected():
            raise AioComfoConnectNotConnected

        reference = self._reference
        self._reference += 1

        cmd = zehnder_pb2.GatewayOperation()
        cmd.type = request_type
        cmd.reference = reference

        msg = request()
        if params is not None:
            for param, value in params.items():
                if value is not None:
                    setattr(msg, param, value)

        message = Message(cmd, msg, self._local_uuid, self.uuid)

        fut = self._loop.create_future()
        if reply:
            self._event_bus.add_listener(reference, fut)
        else:
            fut.set_result(None)

        _LOGGER.debug("TX %s", message)
        self._writer.write(message.encode())
        await self._writer.drain()

        timeout = aiocomfoconnect_bridge.TIMEOUT
        try:
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError as exc:
            if self._loop.time() - self._last_received >= timeout:
                _LOGGER.warning("The bridge did not send anything for %d seconds, reconnecting.", timeout)
                await self._disconnect()
            else:
                _LOGGER.debug("No reply from the bridge to request %d (type %d)", reference, request_type)
            raise AioComfoConnectTimeout("Timeout while waiting for response from bridge") from exc

    async def _connect(self, uuid: str):
        """Connect to the bridge, using an event bus that survives stray replies."""
        read_task = await super()._connect(uuid)

        # The event bus is created (empty) by the connect above; replace it
        # before the read task gets a chance to process a message.
        self._event_bus = SafeEventBus()
        self._last_received = self._loop.time()

        return read_task

    async def _disconnect(self):
        """Disconnect from the bridge, ignoring an already broken connection."""
        try:
            await super()._disconnect()
        except OSError as err:
            # E.g. ConnectionResetError while flushing the socket on shutdown.
            _LOGGER.debug("Error while closing the connection to the bridge: %s", err)

    async def _process_message(self):
        """
        Process a message from the bridge without killing the read loop.

        The library only translates an incomplete read into a disconnect. Any
        other error escapes the read loop and terminates the reconnect loop with
        it, so we handle those here: signal a disconnect when the connection is
        gone (the reconnect loop then reconnects), and otherwise keep reading.
        """
        try:
            await super()._process_message()
            self._last_received = self._loop.time()
        except AioComfoConnectNotConnected:
            raise
        except OSError as err:
            # E.g. ConnectionResetError: the bridge dropped the connection.
            _LOGGER.info("The connection to the bridge was lost: %s", err)
            await self._disconnect()
            raise AioComfoConnectNotConnected("The connection was closed.") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error while processing a message from the bridge")
            if self.is_connected():
                return
            raise AioComfoConnectNotConnected("The connection was closed.") from err

    async def _disable_timer(self, subunit: int, timer: int) -> None:
        """Disable a schedule timer entry, ignoring an error when it wasn't enabled."""
        try:
            await self.cmd_rmi_request(bytes([0x85, UNIT_SCHEDULE, subunit, timer]))
        except ComfoConnectError as err:
            _LOGGER.debug("Could not disable timer %d of schedule %d: %s", timer, subunit, err)

    async def set_speed(self, speed):
        """
        Set the ventilation speed, like the ComfoControl app does.

        The app first cancels the boost, away and cooker hood timers
        (cancelPlusMinTimers), since those take priority over the preset.
        Without this, changing the speed during a boost has no visible effect
        until the boost ends.
        """
        await self._disable_timer(SUBUNIT_01, TIMER_PRESET_BOOST)
        await self._disable_timer(SUBUNIT_01, TIMER_PRESET_BOOST_RF)
        await self._disable_timer(SUBUNIT_HOOD, TIMER_HOOD)
        await self._disable_timer(SUBUNIT_01, TIMER_PRESET_AWAY)
        await super().set_speed(speed)

    async def set_comfocool_mode(self, mode, timeout=-1):
        """
        Set the ComfoCool mode (auto / off).

        aiocomfoconnect enables the ComfoCool-off timer with value 0 (auto);
        the ComfoControl app uses value 1 (off).
        """
        if mode == ComfoCoolMode.OFF:
            await self.cmd_rmi_request(
                bytestring(
                    [
                        0x84,
                        UNIT_SCHEDULE,
                        SUBUNIT_05,
                        0x01,
                        0x00,
                        0x00,
                        0x00,
                        0x00,
                        timeout.to_bytes(4, "little", signed=True),
                        COMFOCOOL_VALUE_OFF,
                    ]
                )
            )
            return
        await super().set_comfocool_mode(mode, timeout)

    async def get_property_range(self, unit: int, property_id: int, *, signed: bool = False, scale: int = 1) -> PropertyRange:
        """Read a 16 bit property with its allowed range and step."""
        # 0x70 = actual value | range | step, like the app requests them.
        result = await self.cmd_rmi_request(bytes([0x01, unit, SUBUNIT_01, 0x70, property_id]))
        if len(result.message) < 8:
            raise ComfoConnectError(f"Unexpected response for property {unit}/{property_id}: {result.message.hex()}")
        values = struct.unpack("<hhhh" if signed else "<HHHH", result.message[:8])
        return PropertyRange(*(value / scale for value in values))

    async def get_rmot_limit(self, property_id: int) -> PropertyRange:
        """Read an RMOT limit of the season detection (INT16 in 0.1 °C), with its range and step."""
        return await self.get_property_range(UNIT_TEMPHUMCONTROL, property_id, signed=True, scale=10)

    async def set_rmot_limit(self, property_id: int, value: float) -> None:
        """Set an RMOT limit of the season detection (in °C)."""
        await self.set_property_typed(UNIT_TEMPHUMCONTROL, SUBUNIT_01, property_id, round(value * 10), PdoType.TYPE_CN_INT16)
        dispatcher_send(self.hass, SIGNAL_COMFOCONNECT_RMOT_LIMIT.format(self.uuid, property_id), value)

    async def get_filter_life_days(self) -> PropertyRange:
        """Read after how many days the filters should be replaced."""
        return await self.get_property_range(UNIT_FILTER, PROPERTY_FILTER_LIFE_DAYS)

    async def set_filter_life_days(self, days: float) -> None:
        """Set after how many days the filters should be replaced."""
        await self.set_property_typed(UNIT_FILTER, SUBUNIT_01, PROPERTY_FILTER_LIFE_DAYS, int(days), PdoType.TYPE_CN_UINT16)

    async def filter_replacement(self, method: int) -> None:
        """Begin, end or abort the filter replacement (the filter wizard of the app)."""
        await self.cmd_rmi_request(bytes([method, UNIT_FILTER, SUBUNIT_01]))

    async def get_temperature_passive_preset(self) -> int:
        """Read how fast the unit reacts to favourable passive heating/cooling conditions."""
        return await self.get_single_property(UNIT_TEMPHUMCONTROL, SUBUNIT_01, PROPERTY_TEMPERATURE_PASSIVE_PRESET, PdoType.TYPE_CN_UINT8)

    async def set_temperature_passive_preset(self, value: int) -> None:
        """Set how fast the unit reacts to favourable passive heating/cooling conditions."""
        await self.set_property(UNIT_TEMPHUMCONTROL, SUBUNIT_01, PROPERTY_TEMPERATURE_PASSIVE_PRESET, value)

    async def get_preset_flow(self, property_id: int) -> PropertyRange:
        """Read the airflow of a ventilation preset, with its allowed range."""
        return await self.get_property_range(UNIT_VENTILATIONCONFIG, property_id)

    async def set_preset_flow(self, property_id: int, flow: float) -> None:
        """Set the airflow of a ventilation preset (installer setting)."""
        if not self.installer_mode:
            raise ComfoConnectError("Installer settings require the installer PIN")
        await self.set_property_typed(UNIT_VENTILATIONCONFIG, SUBUNIT_01, property_id, int(flow), PdoType.TYPE_CN_UINT16)

    async def check_installer_pin(self, pin: str) -> bool:
        """Return whether the PIN matches the installer PIN of the unit."""
        unit_pin = await self.get_single_property(UNIT_NODECONFIGURATION, SUBUNIT_01, PROPERTY_INSTALLER_PIN, PdoType.TYPE_CN_STRING)
        return str(pin).strip().zfill(4) == str(unit_pin).strip()

    async def start_season_now(self, property_id: int) -> float:
        """
        Set an RMOT limit to the current RMOT, like the ComfoControl app does.

        The app rounds the RMOT and the allowed range to whole degrees and
        clamps the RMOT to that range. Returns the new limit.
        """
        rmot = self._sensors_values.get(SENSOR_RMOT)
        if rmot is None:
            raise ComfoConnectError("The current RMOT is not known yet")

        def round_half_up(value: float) -> int:
            return math.floor(value + 0.5)

        limit = await self.get_rmot_limit(property_id)
        value = min(max(round_half_up(rmot / 10), round_half_up(limit.minimum)), round_half_up(limit.maximum))
        await self.set_rmot_limit(property_id, value)
        return value

    @callback
    def sensor_callback(self, sensor: Sensor, value):
        """Notify listeners that we have received an update."""
        dispatcher_send(
            self.hass,
            SIGNAL_COMFOCONNECT_UPDATE_RECEIVED.format(self.uuid, sensor.id),
            value,
        )

    @callback
    def alarm_callback(self, node_id, errors):
        """Print alarm updates."""
        message = f"Alarm received for Node {node_id}:\n"
        for error_id, error in errors.items():
            message += f"* {error_id}: {error}\n"
        _LOGGER.warning(message)
