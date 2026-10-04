"""Websocket-API voor de Boodschappen-pagina."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .api import GrocyError
from .const import DOMAIN


def _coordinator(hass: HomeAssistant):
    from .coordinator import BoodschappenCoordinator

    for e in hass.config_entries.async_loaded_entries(DOMAIN):
        c = getattr(e, "runtime_data", None)
        if isinstance(c, BoodschappenCoordinator):
            return c
    return None


def payload(c) -> dict[str, Any]:
    d = c.data
    items = []
    for lst in d.lists:
        for i in d.items_for_list(lst):
            p = d.products.get(i.product_id) if i.product_id else None
            items.append({
                "id": i.id, "list_id": i.list_id, "product_id": i.product_id, "name": i.name,
                "amount": i.amount, "note": i.note if i.product_id else None, "group": i.group_name,
                "done": i.done, "weekvast": bool(p and (p.get("userfields") or {}).get("wekelijks")),
            })
    bonnen = []
    for b in c.state["bonnen"][:15]:
        bonnen.append({k: b.get(k) for k in ("id", "winkel", "datum", "bestand", "verwerkt", "totaal")} | {
            "regels": [
                {k: l.get(k) for k in ("id", "tekst", "aantal", "totaal", "status", "product_id", "grocy_naam", "factor", "bron", "voorraad", "korting")}
                for l in b["regels"]
            ]
        })
    return {
        "lists": [{"id": lid, "name": l["name"]} for lid, l in sorted(d.lists.items())],
        "groups": [{"id": gid, "name": g["name"]} for gid, g in sorted(d.groups.items(), key=lambda x: x[1]["name"])],
        "products": sorted(
            [{"id": pid, "name": p["name"], "group_id": p.get("product_group_id"), "list_id": d.list_for_product(pid)}
             for pid, p in d.products.items() if str(p.get("active", 1)) != "0"],
            key=lambda x: x["name"].lower(),
        ),
        "items": items,
        "queue": c.state["queue"],
        "bonnen": bonnen,
        "koppelingen": len(c.state["mappings"]),
        "favorieten": {str(k): v for k, v in c.favorites().items()},
    }


def _handler(fn):
    """Voer een actie uit, ververs, en stuur de nieuwe stand terug."""

    @websocket_api.async_response
    async def wrapper(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
        c = _coordinator(hass)
        if c is None:
            connection.send_error(msg["id"], "not_ready", "Boodschappen is niet geladen")
            return
        try:
            extra = await fn(c, msg)
        except (GrocyError, ValueError) as err:
            connection.send_error(msg["id"], "failed", str(err))
            return
        await c.async_refresh()
        result = payload(c)
        if extra is not None:
            result["resultaat"] = extra
        connection.send_result(msg["id"], result)

    return wrapper


async def _data(c, msg):
    return None


async def _item_add(c, msg):
    if msg.get("product_id"):
        await c.add_product(msg["list_id"], int(msg["product_id"]), float(msg.get("amount") or 1))
    else:
        await c.add_text(msg["list_id"], msg["text"])


async def _item_done(c, msg):
    await c.set_done(int(msg["item_id"]), bool(msg["done"]))


async def _item_amount(c, msg):
    await c.set_amount(int(msg["item_id"]), float(msg["amount"]))


async def _item_delete(c, msg):
    await c.delete_items([int(msg["item_id"])])


async def _clear_done(c, msg):
    return await c.clear_done(int(msg["list_id"]))


async def _weekvast(c, msg):
    return await c.add_weekly()


async def _tekorten(c, msg):
    return await c.add_missing()


async def _favoriet(c, msg):
    c.set_favorite(int(msg["product_id"]), msg["actie"])


async def _bon_koppel(c, msg):
    await c.bon.koppel(msg["queue_id"], int(msg["product_id"]), float(msg.get("factor") or 1), bool(msg.get("onthouden", True)))


async def _bon_negeer(c, msg):
    await c.bon.negeer(msg["queue_id"], bool(msg.get("onthouden", True)))


async def _bon_nieuw(c, msg):
    await c.bon.nieuw_product(msg["queue_id"], msg["naam"], msg.get("group_id"), float(msg.get("factor") or 1))


async def _bon_herkoppel(c, msg):
    await c.bon.herkoppel(msg["bon_id"], msg["line_id"], msg.get("product_id"), float(msg.get("factor") or 1), bool(msg.get("negeren", False)))


COMMANDS = [
    ({vol.Required("type"): "boodschappen/data"}, _data),
    ({vol.Required("type"): "boodschappen/item_add", vol.Required("list_id"): int,
      vol.Optional("text"): str, vol.Optional("product_id"): int, vol.Optional("amount"): vol.Coerce(float)}, _item_add),
    ({vol.Required("type"): "boodschappen/item_done", vol.Required("item_id"): int, vol.Required("done"): bool}, _item_done),
    ({vol.Required("type"): "boodschappen/item_amount", vol.Required("item_id"): int, vol.Required("amount"): vol.Coerce(float)}, _item_amount),
    ({vol.Required("type"): "boodschappen/item_delete", vol.Required("item_id"): int}, _item_delete),
    ({vol.Required("type"): "boodschappen/clear_done", vol.Required("list_id"): int}, _clear_done),
    ({vol.Required("type"): "boodschappen/weekvast"}, _weekvast),
    ({vol.Required("type"): "boodschappen/tekorten"}, _tekorten),
    ({vol.Required("type"): "boodschappen/favoriet", vol.Required("product_id"): int,
      vol.Required("actie"): vol.In(["vast", "verberg", "reset"])}, _favoriet),
    ({vol.Required("type"): "boodschappen/bon_koppel", vol.Required("queue_id"): str, vol.Required("product_id"): int,
      vol.Optional("factor"): vol.Coerce(float), vol.Optional("onthouden"): bool}, _bon_koppel),
    ({vol.Required("type"): "boodschappen/bon_negeer", vol.Required("queue_id"): str, vol.Optional("onthouden"): bool}, _bon_negeer),
    ({vol.Required("type"): "boodschappen/bon_nieuw_product", vol.Required("queue_id"): str, vol.Required("naam"): str,
      vol.Optional("group_id"): vol.Any(int, None), vol.Optional("factor"): vol.Coerce(float)}, _bon_nieuw),
    ({vol.Required("type"): "boodschappen/bon_herkoppel", vol.Required("bon_id"): str, vol.Required("line_id"): str,
      vol.Optional("product_id"): vol.Any(int, None), vol.Optional("factor"): vol.Coerce(float), vol.Optional("negeren"): bool}, _bon_herkoppel),
]


def async_register(hass: HomeAssistant) -> None:
    if hass.data.get(f"{DOMAIN}_ws"):
        return
    hass.data[f"{DOMAIN}_ws"] = True
    for schema, fn in COMMANDS:
        handler = _handler(fn)
        websocket_api.async_register_command(hass, websocket_api.websocket_command(schema)(handler))
