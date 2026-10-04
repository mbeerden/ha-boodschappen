"""Boodschappen: eenvoudige winkellijsten per winkel bovenop Grocy, met bonnetjesverwerking."""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import websocket
from .api import GrocyApi, GrocyError, build_base_url
from .bon import BonVerwerker
from .const import (
    CONF_API_KEY,
    CONF_PORT,
    CONF_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
    SERVICE_TEKORTEN,
    SERVICE_WEEKVAST,
)
from .coordinator import BoodschappenCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.TODO]

SERVICE_BON = "bon_verwerken"
PANEL_URL = "boodschappen"
STATIC_URL = "/boodschappen_static"
VERSION = "0.3.0"

BON_SCHEMA = vol.Schema(
    {
        vol.Required("winkel"): cv.string,
        vol.Optional("datum"): vol.Any(None, cv.string),
        vol.Optional("bestand"): vol.Any(None, cv.string),
        vol.Required("regels"): vol.All(cv.ensure_list, [dict]),
        vol.Optional("opnieuw", default=False): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)


def grocy_connection(hass: HomeAssistant, entry: ConfigEntry) -> dict | None:
    """Verbindingsgegevens: eigen invoer, of hergebruik van de Grocy-integratie."""
    if entry.data.get(CONF_URL) and entry.data.get(CONF_API_KEY):
        return dict(entry.data)
    for grocy in hass.config_entries.async_entries("grocy"):
        if grocy.data.get(CONF_URL) and grocy.data.get(CONF_API_KEY):
            return dict(grocy.data)
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    conn = grocy_connection(hass, entry)
    if not conn:
        raise ConfigEntryNotReady("Geen Grocy-verbinding gevonden")
    verify = bool(conn.get(CONF_VERIFY_SSL, False))
    api = GrocyApi(
        async_get_clientsession(hass, verify_ssl=verify),
        build_base_url(conn[CONF_URL], conn.get(CONF_PORT)),
        conn[CONF_API_KEY],
        verify,
    )
    coordinator = BoodschappenCoordinator(hass, api, entry.entry_id)
    await coordinator.async_load_store()
    await coordinator.async_config_entry_first_refresh()
    coordinator.bon = BonVerwerker(coordinator)
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    websocket.async_register(hass)
    await _register_panel(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok and hass.data.pop(f"{DOMAIN}_panel", False):
        frontend.async_remove_panel(hass, PANEL_URL)
    return ok


async def _register_panel(hass: HomeAssistant) -> None:
    if hass.data.get(f"{DOMAIN}_panel"):
        return
    if not hass.data.get(f"{DOMAIN}_static"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "www"), cache_headers=False)]
        )
        hass.data[f"{DOMAIN}_static"] = True
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name="boodschappen-panel",
        sidebar_title="Boodschappen",
        sidebar_icon="mdi:cart-variant",
        module_url=f"{STATIC_URL}/panel.js?v={VERSION}",
        require_admin=False,
        config={},
    )
    hass.data[f"{DOMAIN}_panel"] = True


def _coordinators(hass: HomeAssistant) -> list[BoodschappenCoordinator]:
    return [
        e.runtime_data
        for e in hass.config_entries.async_loaded_entries(DOMAIN)
        if isinstance(getattr(e, "runtime_data", None), BoodschappenCoordinator)
    ]


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_WEEKVAST):
        return

    async def _weekvast(call: ServiceCall) -> ServiceResponse:
        total = 0
        for c in _coordinators(hass):
            await c.async_refresh()
            total += await c.add_weekly()
            await c.async_refresh()
        return {"toegevoegd": total}

    async def _tekorten(call: ServiceCall) -> ServiceResponse:
        total = 0
        for c in _coordinators(hass):
            await c.async_refresh()
            try:
                total += await c.add_missing()
            except GrocyError as err:
                _LOGGER.error("Tekorten ophalen mislukt: %s", err)
            await c.async_refresh()
        return {"toegevoegd": total}

    async def _bon(call: ServiceCall) -> ServiceResponse:
        coords = _coordinators(hass)
        if not coords:
            return {"fout": "Boodschappen is niet geladen"}
        c = coords[0]
        d = call.data
        result = await c.bon.verwerk(d["winkel"], d.get("datum"), d.get("bestand"), d["regels"], d.get("opnieuw", False))
        if not result.get("overgeslagen"):
            _notify(hass, result, len(c.state["queue"]))
        return result

    hass.services.async_register(DOMAIN, SERVICE_WEEKVAST, _weekvast, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_TEKORTEN, _tekorten, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_BON, _bon, schema=BON_SCHEMA, supports_response=SupportsResponse.OPTIONAL)


def _notify(hass: HomeAssistant, result: dict, open_queue: int) -> None:
    t = result["aantallen"]
    delen = []
    for key, label in (("prijs", "prijs bijgewerkt"), ("aangevuld", "aangevuld"), ("ongepland", "ongepland geboekt"),
                       ("te koppelen", "te koppelen"), ("genegeerd", "genegeerd"), ("fout", "fout")):
        if t.get(key):
            delen.append(f"{t[key]} {label}")
    msg = f"**{result['winkel']}** {result.get('datum') or ''} — totaal € {result['totaal']:.2f}\n\n" + ", ".join(delen)
    if open_queue:
        msg += f"\n\n{open_queue} regel(s) wachten op een koppeling: [open Boodschappen → Bonnetjes](/boodschappen?tab=bonnen)"
    else:
        msg += "\n\n[Bekijk het overzicht](/boodschappen?tab=bonnen)"
    hass.async_create_task(
        hass.services.async_call(
            "persistent_notification", "create",
            {"title": "Bonnetje verwerkt", "message": msg, "notification_id": f"boodschappen_bon_{result['bon_id']}"},
        )
    )
