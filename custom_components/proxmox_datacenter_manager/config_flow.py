"""Config flow for Proxmox Datacenter Manager integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    AuthenticationError,
    ConnectionError,
    ProxmoxDatacenterManagerAPI,
    ProxmoxDatacenterManagerError,
)
from .const import (
    CONF_API_TOKEN_ID,
    CONF_API_TOKEN_SECRET,
    CONF_NODE_SENSORS,
    CONF_VM_FILTER,
    CONF_VM_SENSORS,
    DEFAULT_NODE_SENSORS,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DEFAULT_VM_FILTER,
    DEFAULT_VM_SENSORS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    session = async_get_clientsession(hass, verify_ssl=data.get(CONF_VERIFY_SSL, True))

    api = ProxmoxDatacenterManagerAPI(
        host=data[CONF_HOST],
        port=data.get(CONF_PORT, DEFAULT_PORT),
        api_token_id=data[CONF_API_TOKEN_ID],
        api_token_secret=data[CONF_API_TOKEN_SECRET],
        verify_ssl=data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        session=session,
    )

    try:
        version_info = await api.get_version()
        version = version_info.get("version", "unknown") if isinstance(version_info, dict) else "unknown"
    except AuthenticationError as err:
        raise InvalidAuth from err
    except ConnectionError as err:
        raise CannotConnect from err
    except ProxmoxDatacenterManagerError as err:
        raise CannotConnect from err

    return {"title": f"PDM {data[CONF_HOST]}", "version": version}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Proxmox Datacenter Manager."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Check if we already have this host configured
                await self.async_set_unique_id(
                    f"{user_input[CONF_HOST]}:{user_input.get(CONF_PORT, DEFAULT_PORT)}"
                )
                self._abort_if_unique_id_configured()

                return self.async_create_entry(title=info["title"], data=user_input)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_API_TOKEN_ID): str,
                vol.Required(CONF_API_TOKEN_SECRET): str,
                vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "api_token_format": "user@realm!tokenname"
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> FlowResult:
        """Handle reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reauthentication confirmation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            reauth_entry = self._get_reauth_entry()
            data = {**reauth_entry.data, **user_input}

            try:
                await validate_input(self.hass, data)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data=data,
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_API_TOKEN_ID): str,
                vol.Required(CONF_API_TOKEN_SECRET): str,
            }
        )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=data_schema,
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Proxmox Datacenter Manager."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_NODE_SENSORS,
                        default=options.get(CONF_NODE_SENSORS, DEFAULT_NODE_SENSORS),
                    ): bool,
                    vol.Optional(
                        CONF_VM_SENSORS,
                        default=options.get(CONF_VM_SENSORS, DEFAULT_VM_SENSORS),
                    ): bool,
                    vol.Optional(
                        CONF_VM_FILTER,
                        default=options.get(CONF_VM_FILTER, DEFAULT_VM_FILTER),
                    ): str,
                }
            ),
        )


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""
