"""Config flow: hergebruikt de Grocy-integratie, of vraagt URL en API-sleutel."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GrocyApi, GrocyError, build_base_url
from .const import CONF_API_KEY, CONF_PORT, CONF_URL, CONF_VERIFY_SSL, DEFAULT_PORT, DOMAIN


class BoodschappenConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _test(self, conn: dict) -> str | None:
        verify = bool(conn.get(CONF_VERIFY_SSL, False))
        api = GrocyApi(
            async_get_clientsession(self.hass, verify_ssl=verify),
            build_base_url(conn[CONF_URL], conn.get(CONF_PORT)),
            conn[CONF_API_KEY],
            verify,
        )
        try:
            await api.system_info()
            await api.get("/objects/shopping_lists")
        except GrocyError:
            return "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        for grocy in self.hass.config_entries.async_entries("grocy"):
            if grocy.data.get(CONF_URL) and grocy.data.get(CONF_API_KEY):
                if await self._test(grocy.data) is None:
                    return self.async_create_entry(title="Boodschappen (via Grocy-integratie)", data={})
        return await self.async_step_handmatig()

    async def async_step_handmatig(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            err = await self._test(user_input)
            if err is None:
                return self.async_create_entry(title="Boodschappen", data=user_input)
            errors["base"] = err
        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default="http://a0d7b954-grocy"): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_API_KEY): str,
                vol.Optional(CONF_VERIFY_SSL, default=False): bool,
            }
        )
        return self.async_show_form(step_id="handmatig", data_schema=schema, errors=errors)
