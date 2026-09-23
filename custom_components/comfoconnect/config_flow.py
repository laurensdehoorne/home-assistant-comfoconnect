"""Config flow for the ComfoConnect integration."""

from __future__ import annotations

import logging
from typing import Any

import aiocomfoconnect
import voluptuous as vol
from aiocomfoconnect import Bridge
from aiocomfoconnect.exceptions import (
    AioComfoConnectNotConnected,
    AioComfoConnectTimeout,
    ComfoConnectError,
    ComfoConnectNotAllowed,
)
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PIN
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.typing import ConfigType
from homeassistant.util.uuid import random_uuid_hex

from .const import CONF_INSTALLER_PIN, CONF_LOCAL_UUID, CONF_UUID, DOMAIN

DEFAULT_PIN = "0000"
COMFOCONNECT_MANUAL_BRIDGE_ID = "manual"
_LOGGER = logging.getLogger(__name__)


class ComfoConnectConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a ComfoConnect config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> ComfoConnectOptionsFlow:
        """Get the options flow for this handler."""
        return ComfoConnectOptionsFlow()

    def __init__(self) -> None:
        """Initialize the Hue flow."""
        self.bridge: Bridge | None = None
        self.local_uuid: str | None = None
        self.discovered_bridges: dict[str, Bridge] | None = None

    async def async_step_import(self, import_config: ConfigType | None) -> FlowResult:
        """Import a config entry from configuration.yaml."""
        self.local_uuid = import_config.get("token")
        return await self.async_step_manual({CONF_HOST: import_config[CONF_HOST]})

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle a flow reauth."""
        self.bridge = Bridge(user_input[CONF_HOST], user_input[CONF_UUID])
        self.local_uuid = user_input[CONF_LOCAL_UUID]

        return await self._register()

    async def async_step_user(self, user_input: ConfigType | None = None) -> FlowResult:
        """Handle a flow initiated by the user."""

        if user_input is not None:
            # User has chosen to manually enter a bridge
            if user_input[CONF_UUID] == COMFOCONNECT_MANUAL_BRIDGE_ID:
                return await self.async_step_manual()

            # User has selected a discovered bridge
            if user_input[CONF_UUID] is not None:
                self.bridge = self.discovered_bridges[user_input[CONF_UUID]]

                # Don't allow to configure the same bridge twice
                await self.async_set_unique_id(self.bridge.uuid, raise_on_progress=False)
                self._abort_if_unique_id_configured()

                return await self._register()

        # Find bridges on the network and filter out the ones we already have configured
        bridges = await aiocomfoconnect.discover_bridges()
        self.discovered_bridges = {bridge.uuid: bridge for bridge in bridges if bridge.uuid not in self._async_current_ids(False)}

        # Show the bridge selection form
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_UUID): vol.In(
                        {
                            **{bridge.uuid: bridge.host for bridge in self.discovered_bridges.values()},
                            COMFOCONNECT_MANUAL_BRIDGE_ID: "Manually add a ComfoConnect LAN C Bridge",
                        }
                    )
                }
            ),
        )

    async def async_step_manual(self, user_input: ConfigType | None = None) -> FlowResult:
        """Handle manual bridge setup."""
        errors = {}
        if user_input is not None and user_input[CONF_HOST] is not None:
            # We need to discover the bridge to get its UUID
            bridges = await aiocomfoconnect.discover_bridges(user_input[CONF_HOST])
            if len(bridges) == 0:
                # Could not discover the bridge
                errors = {"base": "invalid_host"}
            else:
                self.bridge = bridges[0]
                # Don't allow to configure the same bridge twice
                await self.async_set_unique_id(self.bridge.uuid, raise_on_progress=False)
                self._abort_if_unique_id_configured()

                return await self._register()

        return self.async_show_form(
            step_id="manual",
            errors=errors,
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Change the address of the bridge, e.g. when its IP address changed."""
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            bridges = await aiocomfoconnect.discover_bridges(host)
            if not bridges:
                errors["base"] = "invalid_host"
            elif bridges[0].uuid != entry.data[CONF_UUID]:
                # A different bridge: that needs a new config entry (and registration).
                errors["base"] = "wrong_bridge"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    title=bridges[0].host,
                    data_updates={CONF_HOST: bridges[0].host},
                )

        return self.async_show_form(
            step_id="reconfigure",
            errors=errors,
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}),
        )

    async def _register(self, pin: int = None) -> FlowResult:
        """Register on the bridge."""

        if self.local_uuid is None:
            # Generate our own UUID if non is provided
            self.local_uuid = random_uuid_hex()

        # Connect to the bridge
        await self.bridge._connect(self.local_uuid)
        try:
            await self.bridge.cmd_start_session(True)

        except ComfoConnectNotAllowed:
            try:
                # We probably are not registered yet, lets try to register.
                await self.bridge.cmd_register_app(
                    self.local_uuid,
                    "Home Assistant (%s)" % self.hass.config.location_name,
                    pin or DEFAULT_PIN,
                )

            except ComfoConnectNotAllowed:
                # We have tried connecting, but we have an invalid PIN. Ask the user for a new PIN.
                errors = {"base": "invalid_pin"} if pin is not None else {}
                return await self.async_step_enter_pin({}, errors)

            # Registration went fine, connect to the bridge again
            await self.bridge.cmd_start_session(True)

        finally:
            # Disconnect
            await self.bridge._disconnect()

        if self.context.get("source") == config_entries.SOURCE_REAUTH:
            self.hass.async_create_task(self.hass.config_entries.async_reload(self.context["entry_id"]))
            return self.async_abort(reason="reauth_successful")

        return self.async_create_entry(
            title=self.bridge.host,
            data={
                CONF_HOST: self.bridge.host,
                CONF_UUID: self.bridge.uuid,
                CONF_LOCAL_UUID: self.local_uuid,
            },
        )

    async def async_step_enter_pin(
        self,
        user_input: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
    ) -> FlowResult:
        """Handle the PIN entry step."""
        if user_input and CONF_PIN in user_input:
            return await self._register(user_input[CONF_PIN])

        return self.async_show_form(
            step_id="enter_pin",
            errors=errors or {},
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PIN): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=0, max=9999, msg="A PIN must be between 0000 and 9999"),
                    )
                }
            ),
        )


class ComfoConnectOptionsFlow(config_entries.OptionsFlow):
    """
    Handle the options of a ComfoConnect bridge.

    The installer PIN unlocks the installer settings (like the airflow per
    preset), as in the installer menu of the ComfoControl app. Like the app,
    we check it against the installer PIN of the unit.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the options."""
        errors = {}
        if user_input is not None:
            pin = (user_input.get(CONF_INSTALLER_PIN) or "").strip()
            if not pin:
                return self.async_create_entry(data={})

            bridge = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
            if bridge is None:
                errors["base"] = "cannot_connect"
            else:
                try:
                    if await bridge.check_installer_pin(pin):
                        return self.async_create_entry(data={CONF_INSTALLER_PIN: pin.zfill(4)})
                    errors[CONF_INSTALLER_PIN] = "invalid_installer_pin"
                except (AioComfoConnectNotConnected, AioComfoConnectTimeout, ComfoConnectError):
                    errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="init",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_INSTALLER_PIN,
                        description={"suggested_value": self.config_entry.options.get(CONF_INSTALLER_PIN, "")},
                    ): vol.All(str, vol.Match(r"^\s*\d{0,4}\s*$")),
                }
            ),
        )
