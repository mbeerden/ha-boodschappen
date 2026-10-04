"""Start een echte HA-core met de integratie tegen een nep-Grocy-server."""
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_boodschappen import GROUPS, LISTS, LOCS, PRODUCTS  # noqa: E402

STATE = {
    "items": [
        {"id": 1, "product_id": 70, "note": None, "amount": 8, "shopping_list_id": 1, "done": 0, "qu_id": 2},
        {"id": 2, "product_id": 8, "note": "fruit", "amount": 1, "shopping_list_id": 1, "done": 0, "qu_id": 2},
        {"id": 3, "product_id": None, "note": "Mozzarella (bol)", "amount": 2, "shopping_list_id": 1, "done": 0},
        {"id": 4, "product_id": 245, "note": None, "amount": 1, "shopping_list_id": 2, "done": 0},
    ],
    "next": 10, "log": [],
}


stock_log_ref = [None]


def fake_grocy():
    app = web.Application()

    def auth(req):
        if req.headers.get("GROCY-API-KEY") != "geheim":
            raise web.HTTPUnauthorized()

    async def objects(req):
        auth(req)
        ent = req.match_info["ent"]
        data = {"products": PRODUCTS, "product_groups": GROUPS, "shopping_lists": LISTS,
                "shopping_locations": LOCS, "shopping_list": STATE["items"]}[ent]
        return web.json_response(data)

    async def add_item(req):
        auth(req)
        body = await req.json()
        STATE["next"] += 1
        body["id"] = STATE["next"]
        body.setdefault("note", None)
        body.setdefault("product_id", None)
        STATE["items"].append(body)
        STATE["log"].append(("add", body))
        return web.json_response({"created_object_id": str(STATE["next"])})

    async def put_item(req):
        auth(req)
        body = await req.json()
        for i in STATE["items"]:
            if i["id"] == int(req.match_info["id"]):
                i.update(body)
        STATE["log"].append(("put", int(req.match_info["id"]), body))
        return web.Response(status=204)

    async def del_item(req):
        auth(req)
        STATE["items"] = [i for i in STATE["items"] if i["id"] != int(req.match_info["id"])]
        STATE["log"].append(("del", int(req.match_info["id"])))
        return web.Response(status=204)

    async def stock_add(req):
        auth(req)
        body = await req.json()
        STATE["log"].append(("purchase", int(req.match_info["id"]), body))
        return web.json_response([{"id": 1, "transaction_id": "tx-abc"}])

    async def undo(req):
        auth(req)
        STATE["log"].append(("undo", req.match_info["tx"]))
        return web.Response(status=204)

    async def sysinfo(req):
        auth(req)
        return web.json_response({"grocy_version": {"Version": "4.7.1"}})

    app.router.add_get("/api/objects/stock_log", lambda r: stock_log_ref[0](r))
    app.router.add_get("/api/objects/{ent}", objects)
    app.router.add_post("/api/objects/shopping_list", add_item)
    app.router.add_put("/api/objects/shopping_list/{id}", put_item)
    app.router.add_delete("/api/objects/shopping_list/{id}", del_item)
    app.router.add_post("/api/stock/products/{id}/add", stock_add)
    app.router.add_post("/api/stock/transactions/{tx}/undo", undo)
    app.router.add_get("/api/system/info", sysinfo)

    async def stock_log(req):
        auth(req)
        return web.json_response([{"stock_id": "S1", "transaction_type": "purchase", "undone": 0}])

    async def entries(req):
        auth(req)
        return web.json_response([{"id": 5, "stock_id": "S1", "amount": 8, "best_before_date": "2026-10-20", "location_id": 3, "open": 0, "purchased_date": "2026-10-04"}])

    async def put_entry(req):
        auth(req)
        STATE["log"].append(("price-entry", int(req.match_info["id"]), (await req.json())["price"]))
        return web.json_response({})

    async def new_product(req):
        auth(req)
        body = await req.json()
        PRODUCTS.append({"id": 500, "name": body["name"], "product_group_id": body.get("product_group_id"),
                         "shopping_location_id": body.get("shopping_location_id"), "active": 1, "userfields": {}})
        STATE["log"].append(("new-product", body["name"]))
        return web.json_response({"created_object_id": "500"})

    stock_log_ref[0] = stock_log
    app.router.add_get("/api/stock/products/{id}/entries", entries)
    app.router.add_put("/api/stock/entry/{id}", put_entry)
    app.router.add_post("/api/objects/products", new_product)
    return app


async def main():
    runner = web.AppRunner(fake_grocy())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 9192)
    await site.start()

    cfg = Path(tempfile.mkdtemp())
    shutil.copytree(ROOT / "custom_components", cfg / "custom_components")

    from homeassistant import loader
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import entity_registry, area_registry, device_registry, floor_registry, label_registry, category_registry
    from homeassistant.setup import async_setup_component
    from homeassistant.helpers import translation  # noqa: F401
    from homeassistant import bootstrap

    import aiohttp
    from homeassistant.helpers import aiohttp_client as _ac
    class _R(aiohttp.ThreadedResolver):
        async def real_close(self): pass
    _ac._async_make_resolver = lambda hass: _R()
    hass = HomeAssistant(str(cfg))
    await hass.async_start() if False else None
    hass.config.skip_pip = True
    loader.async_setup(hass)
    from homeassistant import config_entries
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await bootstrap.async_load_base_functionality(hass)
    from homeassistant import auth as ha_auth
    hass.auth = await ha_auth.auth_manager_from_config(hass, [{"type": "homeassistant"}], [])
    await async_setup_component(hass, "http", {"http": {"server_port": 18123}})
    await async_setup_component(hass, "homeassistant", {})

    # Bestaande HACS Grocy-integratie nabootsen (alleen de config entry)
    grocy_entry = config_entries.ConfigEntry(
        domain="grocy", title="Grocy", data={"url": "http://127.0.0.1", "port": 9192, "api_key": "geheim", "verify_ssl": False},
        source="user", version=1, minor_version=1, options={}, unique_id=None, discovery_keys={}, subentries_data=None,
    )
    hass.config_entries._entries[grocy_entry.entry_id] = grocy_entry

    result = await hass.config_entries.flow.async_init("boodschappen", context={"source": "user"})
    print("FLOW:", result["type"], result.get("title"))
    await hass.async_block_till_done()

    states = {s.entity_id: s for s in hass.states.async_all("todo")}
    for eid, s in sorted(states.items()):
        print("STATE", eid, s.state, s.attributes.get("friendly_name"))

    async def items(eid):
        r = await hass.services.async_call("todo", "get_items", {}, target={"entity_id": eid}, blocking=True, return_response=True)
        return [(i["summary"], i["status"], i.get("description")) for i in r[eid]["items"]]

    print("LIDL", await items("todo.boodschappen_lidl"))
    print("JUMBO", await items("todo.boodschappen_jumbo"))

    await hass.services.async_call("todo", "add_item", {"item": "3 komkommer"}, target={"entity_id": "todo.boodschappen_lidl"}, blocking=True)
    await hass.services.async_call("todo", "add_item", {"item": "appeltaart"}, target={"entity_id": "todo.boodschappen_lidl"}, blocking=True)
    await hass.services.async_call("todo", "update_item", {"item": "Eieren (8)", "status": "completed"}, target={"entity_id": "todo.boodschappen_lidl"}, blocking=True)
    print("LIDL na acties", await items("todo.boodschappen_lidl"))
    await hass.services.async_call("todo", "update_item", {"item": "Eieren (8)", "status": "needs_action"}, target={"entity_id": "todo.boodschappen_lidl"}, blocking=True)
    await hass.services.async_call("todo", "update_item", {"item": "Wasverzachter", "status": "completed"}, target={"entity_id": "todo.boodschappen_jumbo"}, blocking=True)
    await asyncio.sleep(1.5)
    await hass.services.async_call("todo", "remove_completed_items", {}, target={"entity_id": "todo.boodschappen_jumbo"}, blocking=True)
    r = await hass.services.async_call("boodschappen", "weekvast_toevoegen", {}, blocking=True, return_response=True)
    print("WEEKVAST", r)
    await asyncio.sleep(1.5)
    print("LIDL eind", await items("todo.boodschappen_lidl"))
    print("JUMBO eind", await items("todo.boodschappen_jumbo"))
    # --- nieuw: paneel, websocket-data en bonnetjes ---
    from homeassistant.components import frontend
    print("PANEL", "boodschappen" in hass.data.get(frontend.DATA_PANELS, {}))
    await hass.services.async_call("todo", "update_item", {"item": "Eieren (8)", "status": "completed"}, target={"entity_id": "todo.boodschappen_lidl"}, blocking=True)
    r = await hass.services.async_call("boodschappen", "bon_verwerken", {
        "winkel": "LIDL Centrum", "datum": "04-10-2026", "bestand": "/media/bonnetjes/lidl/x.jpg",
        "regels": [
            {"bon": "VRIJE UITLOOP EIEREN 6ST", "product": "Eieren", "grocy": "Eieren", "aantal": 2, "totaal": 3.78},
            {"bon": "KORTING", "product": "Korting", "aantal": 1, "totaal": -0.50},
            {"bon": "KOMKOMMER", "product": "Komkommer", "grocy": "Komkommer", "aantal": 1, "totaal": 0.79},
            {"bon": "PASEO CHOCO", "product": "Chocopasta", "grocy": "", "aantal": 1, "totaal": 2.49},
        ]}, blocking=True, return_response=True)
    print("BON", r)
    from custom_components.boodschappen import websocket as ws
    c = ws._coordinator(hass)
    data = ws.payload(c)
    print("WS queue", [(q["tekst"], q["factor"]) for q in data["queue"]], "bonnen", len(data["bonnen"]))
    qid = data["queue"][0]["id"]
    await c.bon.nieuw_product(qid, "Chocopasta", 4, 1)
    print("WS na nieuw product", [l["status"] for l in ws.payload(c)["bonnen"][0]["regels"]])
    notes = [s.attributes.get("title") for s in hass.states.async_all("persistent_notification")]
    print("NOTIF", hass.states.async_entity_ids("persistent_notification")[:3], notes[:2])
    print("GROCY-LOG:")
    for l in STATE["log"]:
        print("  ", json.dumps(l))
    await hass.async_stop(force=True)
    await runner.cleanup()


asyncio.run(main())
