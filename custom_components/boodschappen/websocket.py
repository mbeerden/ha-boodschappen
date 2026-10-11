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
        "winkels": c.producten.winkels() if hasattr(c, "producten") else [],
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
        "aanbiedingen": c.aanbiedingen.zichtbaar() if hasattr(c, "aanbiedingen") else [],
        "aanbiedingen_info": {k: (c.state.get("aanbiedingen") or {}).get(k) for k in ("bijgewerkt", "fout")},
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


async def _aanb_negeer(c, msg):
    c.aanbiedingen.negeer(msg["key"])


async def _aanb_ververs(c, msg):
    res = await c.aanbiedingen.ververs(forceer=True)
    return res.get("aanbiedingen", 0)


async def _bon_koppel(c, msg):
    await c.bon.koppel(msg["queue_id"], int(msg["product_id"]), float(msg.get("factor") or 1), bool(msg.get("onthouden", True)))


async def _bon_negeer(c, msg):
    await c.bon.negeer(msg["queue_id"], bool(msg.get("onthouden", True)))


async def _bon_nieuw(c, msg):
    await c.bon.nieuw_product(msg["queue_id"], msg["naam"], msg.get("group_id"), float(msg.get("factor") or 1))


async def _bon_herkoppel(c, msg):
    if msg.get("nieuw_naam"):
        await c.bon.herkoppel_nieuw(msg["bon_id"], msg["line_id"], msg["nieuw_naam"], msg.get("group_id"),
                                    float(msg.get("factor") or 1), msg.get("totaal"))
        return
    await c.bon.herkoppel(msg["bon_id"], msg["line_id"], msg.get("product_id"), float(msg.get("factor") or 1), bool(msg.get("negeren", False)),
                          msg.get("totaal"))


async def _p_raden(c, msg):
    return await c.producten.raden(msg["naam"])


async def _p_nieuw(c, msg):
    return await c.producten.nieuw(msg["naam"], msg.get("group_id"), msg.get("winkel_id"),
                                   float(msg.get("amount") or 1), msg.get("item_id"))


async def _p_details(c, msg):
    return await c.producten.details(int(msg["product_id"]))


async def _p_opslaan(c, msg):
    await c.producten.opslaan(int(msg["product_id"]), msg["velden"])
    return await c.producten.details(int(msg["product_id"]))


async def _p_op(c, msg):
    await c.producten.op(int(msg["product_id"]))


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
    ({vol.Required("type"): "boodschappen/product_raden", vol.Required("naam"): str}, _p_raden),
    ({vol.Required("type"): "boodschappen/product_nieuw", vol.Required("naam"): str, vol.Optional("group_id"): vol.Any(None, int),
      vol.Optional("winkel_id"): vol.Any(None, int), vol.Optional("amount"): vol.Coerce(float),
      vol.Optional("item_id"): vol.Any(None, int)}, _p_nieuw),
    ({vol.Required("type"): "boodschappen/product_details", vol.Required("product_id"): int}, _p_details),
    ({vol.Required("type"): "boodschappen/product_opslaan", vol.Required("product_id"): int, vol.Required("velden"): dict}, _p_opslaan),
    ({vol.Required("type"): "boodschappen/product_op", vol.Required("product_id"): int}, _p_op),
    ({vol.Required("type"): "boodschappen/aanbieding_negeer", vol.Required("key"): str}, _aanb_negeer),
    ({vol.Required("type"): "boodschappen/aanbiedingen_ververs"}, _aanb_ververs),
    ({vol.Required("type"): "boodschappen/bon_koppel", vol.Required("queue_id"): str, vol.Required("product_id"): int,
      vol.Optional("factor"): vol.Coerce(float), vol.Optional("onthouden"): bool}, _bon_koppel),
    ({vol.Required("type"): "boodschappen/bon_negeer", vol.Required("queue_id"): str, vol.Optional("onthouden"): bool}, _bon_negeer),
    ({vol.Required("type"): "boodschappen/bon_nieuw_product", vol.Required("queue_id"): str, vol.Required("naam"): str,
      vol.Optional("group_id"): vol.Any(int, None), vol.Optional("factor"): vol.Coerce(float)}, _bon_nieuw),
    ({vol.Required("type"): "boodschappen/bon_herkoppel", vol.Required("bon_id"): str, vol.Required("line_id"): str,
      vol.Optional("product_id"): vol.Any(int, None), vol.Optional("factor"): vol.Coerce(float), vol.Optional("negeren"): bool,
      vol.Optional("totaal"): vol.Any(None, vol.Coerce(float)), vol.Optional("nieuw_naam"): vol.Any(None, str),
      vol.Optional("group_id"): vol.Any(None, int)}, _bon_herkoppel),
]


# ---------------------------------------------------------------- Eten
def eten_payload(c) -> dict[str, Any]:
    d = c.data
    out = c.eten.payload()
    out["producten"] = sorted(
        [{"id": pid, "name": p["name"]} for pid, p in d.products.items() if str(p.get("active", 1)) != "0"],
        key=lambda x: x["name"].lower(),
    )
    return out


def _eten(fn, sync: bool = False):
    @websocket_api.async_response
    async def wrapper(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
        c = _coordinator(hass)
        if c is None or not hasattr(c, "eten"):
            connection.send_error(msg["id"], "not_ready", "Boodschappen is niet geladen")
            return
        try:
            extra = await fn(c.eten, msg)
            if sync:
                await c.eten.sync()
        except (GrocyError, ValueError) as err:
            connection.send_error(msg["id"], "failed", str(err))
            return
        except Exception as err:  # noqa: BLE001 - AI/agenda/netwerk: melden i.p.v. hangen
            connection.send_error(msg["id"], "failed", f"{type(err).__name__}: {err}")
            return
        result = eten_payload(c)
        if extra is not None:
            result["resultaat"] = extra
        connection.send_result(msg["id"], result)

    return wrapper


async def _e_data(e, msg):
    return None


async def _e_recept(e, msg):
    return e.recept_opslaan(msg["recept"])


async def _e_recept_weg(e, msg):
    e.recept_verwijderen(msg["recept_id"])


async def _e_import(e, msg):
    return await e.recept_importeren(msg.get("url"), msg.get("tekst"))


async def _e_koppel(e, msg):
    await e.recept_koppelen(msg["recept_id"])


async def _e_voorstel(e, msg):
    await e.voorstel_maken(msg["start"], int(msg.get("dagen") or 7), msg.get("wens") or "")


async def _e_voorstel_wijzig(e, msg):
    e.voorstel_wijzig(msg["datum"], msg["velden"])


async def _e_voorstel_weg(e, msg):
    e.voorstel_weg()


async def _e_goedkeuren(e, msg):
    return await e.voorstel_goedkeuren()


async def _e_plan(e, msg):
    item = await e.plan(msg["datum"], msg.get("velden") or {}, msg.get("plan_id"))
    return item["id"]


async def _e_plan_weg(e, msg):
    await e.plan_verwijderen(msg["plan_id"])


async def _e_overnemen(e, msg):
    item = await e.overnemen(msg["datum"], msg.get("uid"), msg.get("recept_id"), msg.get("titel"), msg.get("velden"))
    return item["id"]


async def _e_dag_aanpassen(e, msg):
    await e.dag_aanpassen(msg["datum"], msg.get("wens") or "", msg.get("plan_id"))


async def _e_sug_toepassen(e, msg):
    return await e.suggestie_toepassen(msg["datum"])


async def _e_sug_weg(e, msg):
    e.suggestie_weg(msg["datum"])


async def _e_historie(e, msg):
    return e.historie(msg["items"])


async def _e_lijst(e, msg):
    for p in e.s["planning"]:
        if p["id"] in msg["plan_ids"]:
            p["op_lijst"] = False
    return await e.ingredienten_op_lijst(msg["plan_ids"])


async def _e_beoordeel(e, msg):
    e.beoordeel(msg["plan_id"], msg["persoon"], msg.get("score"), msg.get("opmerking") or "")


async def _e_instellingen(e, msg):
    e.instellingen_opslaan(msg["instellingen"])
    await e.sync(forceer=True)


async def _e_sync(e, msg):
    await e.sync(forceer=True)


ETEN_COMMANDS = [
    ({vol.Required("type"): "boodschappen/eten"}, _e_data, True),
    ({vol.Required("type"): "boodschappen/eten_sync"}, _e_sync, False),
    ({vol.Required("type"): "boodschappen/eten_recept", vol.Required("recept"): dict}, _e_recept, False),
    ({vol.Required("type"): "boodschappen/eten_recept_weg", vol.Required("recept_id"): str}, _e_recept_weg, False),
    ({vol.Required("type"): "boodschappen/eten_import", vol.Optional("url"): vol.Any(None, str),
      vol.Optional("tekst"): vol.Any(None, str)}, _e_import, False),
    ({vol.Required("type"): "boodschappen/eten_koppel", vol.Required("recept_id"): str}, _e_koppel, False),
    ({vol.Required("type"): "boodschappen/eten_voorstel", vol.Required("start"): str, vol.Optional("dagen"): int,
      vol.Optional("wens"): vol.Any(None, str)}, _e_voorstel, False),
    ({vol.Required("type"): "boodschappen/eten_voorstel_wijzig", vol.Required("datum"): str,
      vol.Required("velden"): dict}, _e_voorstel_wijzig, False),
    ({vol.Required("type"): "boodschappen/eten_voorstel_weg"}, _e_voorstel_weg, False),
    ({vol.Required("type"): "boodschappen/eten_goedkeuren"}, _e_goedkeuren, False),
    ({vol.Required("type"): "boodschappen/eten_plan", vol.Required("datum"): str, vol.Optional("plan_id"): vol.Any(None, str),
      vol.Optional("velden"): dict}, _e_plan, False),
    ({vol.Required("type"): "boodschappen/eten_plan_weg", vol.Required("plan_id"): str}, _e_plan_weg, False),
    ({vol.Required("type"): "boodschappen/eten_overnemen", vol.Required("datum"): str, vol.Optional("uid"): vol.Any(None, str),
      vol.Optional("recept_id"): vol.Any(None, str), vol.Optional("titel"): vol.Any(None, str),
      vol.Optional("velden"): dict}, _e_overnemen, False),
    ({vol.Required("type"): "boodschappen/eten_dag_aanpassen", vol.Required("datum"): str,
      vol.Optional("wens"): vol.Any(None, str), vol.Optional("plan_id"): vol.Any(None, str)}, _e_dag_aanpassen, False),
    ({vol.Required("type"): "boodschappen/eten_suggestie_toepassen", vol.Required("datum"): str}, _e_sug_toepassen, False),
    ({vol.Required("type"): "boodschappen/eten_suggestie_weg", vol.Required("datum"): str}, _e_sug_weg, False),
    ({vol.Required("type"): "boodschappen/eten_historie", vol.Required("items"): [dict]}, _e_historie, False),
    ({vol.Required("type"): "boodschappen/eten_op_lijst", vol.Required("plan_ids"): [str]}, _e_lijst, False),
    ({vol.Required("type"): "boodschappen/eten_beoordeel", vol.Required("plan_id"): str, vol.Required("persoon"): str,
      vol.Optional("score"): vol.Any(None, vol.All(int, vol.Range(min=1, max=5))),
      vol.Optional("opmerking"): vol.Any(None, str)}, _e_beoordeel, False),
    ({vol.Required("type"): "boodschappen/eten_instellingen", vol.Required("instellingen"): dict}, _e_instellingen, False),
]


def async_register(hass: HomeAssistant) -> None:
    if hass.data.get(f"{DOMAIN}_ws"):
        return
    hass.data[f"{DOMAIN}_ws"] = True
    for schema, fn in COMMANDS:
        handler = _handler(fn)
        websocket_api.async_register_command(hass, websocket_api.websocket_command(schema)(handler))
    for schema, fn, sync in ETEN_COMMANDS:
        websocket_api.async_register_command(hass, websocket_api.websocket_command(schema)(_eten(fn, sync)))
