"""Configuration de l'intégration depuis l'interface Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EcoControlClient, EcoControlConnectionError, EcoControlError
from .const import (
    CONF_DEROGATION_HOURS,
    CONF_SCAN_INTERVAL,
    DEFAULT_DEROGATION_HOURS,
    DEFAULT_HOST,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class EcoControlConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            await self.async_set_unique_id(host)
            self._abort_if_unique_id_configured()
            client = EcoControlClient(async_get_clientsession(self.hass), host)
            try:
                await client.validate()
            except EcoControlConnectionError:
                errors["base"] = "cannot_connect"
            except EcoControlError:
                errors["base"] = "invalid_response"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Erreur inattendue pendant la validation")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"Smart ECOcontrol ({host})", data={CONF_HOST: host}
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOST, default=DEFAULT_HOST): str}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return EcoControlOptionsFlow()


class EcoControlOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=3600)),
                    vol.Required(
                        CONF_DEROGATION_HOURS,
                        default=options.get(
                            CONF_DEROGATION_HOURS, DEFAULT_DEROGATION_HOURS
                        ),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.25, max=24)),
                }
            ),
        )
