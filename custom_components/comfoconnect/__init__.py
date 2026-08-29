"""Support to control a Zehnder ComfoAir Q350/450/600 ventilation unit."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from aiocomfoconnect import ComfoConnect, discover_bridges
from aiocomfoconnect.bridge import EventBus
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
from aiocomfoconnect.sensors import Sensor
from aiocomfoconnect.util import version_decode
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

from .const import CONF_LOCAL_UUID, CONF_UUID, DOMAIN

PLATFORMS: list[Platform] = [
    Platform.FAN,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.BUTTON,
]

_LOGGER = logging.getLogger(__name__)

SIGNAL_COMFOCONNECT_UPDATE_RECEIVED = "comfoconnect_update_{}_{}"
SIGNAL_COMFOCONNECT_AVAILABLE = "comfoconnect_available_{}"

KEEP_ALIVE_INTERVAL = timedelta(seconds=30)

# Maximum time we wait for a watchdog-initiated reconnect to complete. The
# reconnect loop keeps running in the background when this expires.
RECONNECT_TIMEOUT = 30


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

        except ComfoConnectError as err:
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
    device_registry.async_get_or_create(
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
        via_device=(DOMAIN, bridge_info.serialNumber),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    reconnect_lock = asyncio.Lock()

    async def restart_connection_if_dead() -> None:
        """Restart the connection when the library's reconnect loop is gone.

        The reconnect loop normally recovers from a dropped connection by
        itself, but it stops for good if it ever ends with an unexpected
        exception. Without this watchdog the integration would then stay
        offline until Home Assistant is restarted.
        """
        if bridge.reconnect_loop_alive() or reconnect_lock.locked():
            return

        async with reconnect_lock:
            _LOGGER.warning("The reconnect loop is no longer running, reconnecting to bridge %s.", bridge.host)
            await bridge.disconnect()
            try:
                async with asyncio.timeout(RECONNECT_TIMEOUT):
                    await bridge.connect(entry.data[CONF_LOCAL_UUID])
            except (TimeoutError, AioComfoConnectTimeout, ComfoConnectError, OSError) as err:
                # The reconnect loop retries on its own from here on.
                _LOGGER.debug("Reconnecting to the bridge did not succeed (yet): %s", err)

    async def send_keepalive(now) -> None:
        """Probe the bridge and report availability.

        ComfoConnect.connect() runs its own internal reconnect loop, so we must
        not call connect() again here while that loop is alive: doing so spawns
        duplicate reconnect loops and read tasks, which get orphaned ("Task was
        destroyed but it is pending!"). We only probe the bridge and update
        entity availability; the library restores the connection on its own,
        unless its reconnect loop died (see restart_connection_if_dead).
        """
        _LOGGER.debug("Sending keepalive...")
        try:
            # Use cmd_time_request as a keepalive since cmd_keepalive doesn't send back a reply we can wait for
            await bridge.cmd_time_request()
        except (AioComfoConnectNotConnected, AioComfoConnectTimeout):
            _LOGGER.debug("Keepalive failed; bridge unavailable (library will reconnect).")
            dispatcher_send(hass, SIGNAL_COMFOCONNECT_AVAILABLE.format(bridge.uuid), False)
            await restart_connection_if_dead()
        else:
            dispatcher_send(hass, SIGNAL_COMFOCONNECT_AVAILABLE.format(bridge.uuid), True)

    entry.async_on_unload(async_track_time_interval(hass, send_keepalive, KEEP_ALIVE_INTERVAL))

    # Disconnect when shutting down
    async def disconnect_bridge(event):
        """Close connection to the bridge."""
        await bridge.disconnect()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, disconnect_bridge))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        bridge = hass.data[DOMAIN][entry.entry_id]
        await bridge.disconnect()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class SafeEventBus(EventBus):
    """An event bus that tolerates replies we are no longer waiting for.

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
        self._watched_tasks = set()

    async def connect(self, uuid: str) -> None:
        """Connect to the bridge and keep an eye on its reconnect loop."""
        await super().connect(uuid)

        # Retrieve the result of the reconnect loop when it ends, so a crash is
        # logged by us instead of surfacing as "Task exception was never
        # retrieved".
        for task in self._tasks:
            if task not in self._watched_tasks:
                self._watched_tasks.add(task)
                task.add_done_callback(self._reconnect_task_done)

    def reconnect_loop_alive(self) -> bool:
        """Return True while the library's reconnect loop is still running."""
        return any(not task.done() for task in self._tasks)

    @callback
    def _reconnect_task_done(self, task) -> None:
        """Report why the reconnect loop stopped."""
        self._watched_tasks.discard(task)
        if task.cancelled():
            return
        if err := task.exception():
            _LOGGER.warning("The connection to the bridge stopped unexpectedly: %s", err)

    async def _connect(self, uuid: str):
        """Connect to the bridge, using an event bus that survives stray replies."""
        read_task = await super()._connect(uuid)

        # The event bus is created (empty) by the connect above; replace it
        # before the read task gets a chance to process a message.
        self._event_bus = SafeEventBus()

        return read_task

    async def _disconnect(self):
        """Disconnect from the bridge, ignoring an already broken connection."""
        try:
            await super()._disconnect()
        except OSError as err:
            # E.g. ConnectionResetError while flushing the socket on shutdown.
            _LOGGER.debug("Error while closing the connection to the bridge: %s", err)

    async def _process_message(self):
        """Process a message from the bridge without killing the read loop.

        The library only translates an incomplete read into a disconnect. Any
        other error escapes the read loop and terminates the reconnect loop with
        it, so we handle those here: signal a disconnect when the connection is
        gone (the reconnect loop then reconnects), and otherwise keep reading.
        """
        try:
            await super()._process_message()
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
