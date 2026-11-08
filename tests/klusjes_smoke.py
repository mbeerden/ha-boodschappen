"""Start een echte HA-core met de integratie en een Local Calendar als onderhoudsagenda, en test Klusjes.

Draaien: python3 tests/klusjes_smoke.py
"""
import asyncio
from datetime import date, datetime, timedelta
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))
from ha_smoke import STATE, fake_grocy  # noqa: E402

VANDAAG = date.today()
FOUTEN: list[str] = []
AI_LOG: list[str] = []


def check(ok, wat):
    print(("OK   " if ok else "FOUT ") + wat)
    if not ok:
        FOUTEN.append(wat)


async def main():
    app = fake_grocy()

    async def stock(req):
        return web.json_response([{"product_id": 15, "amount": 1}])  # 1 ui in huis

    app.router.add_get("/api/stock", stock)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 9192).start()

    cfg = Path(tempfile.mkdtemp())
    shutil.copytree(ROOT / "custom_components", cfg / "custom_components")

    import aiohttp
    from homeassistant import auth as ha_auth, bootstrap, config_entries, loader
    from homeassistant.core import HomeAssistant, SupportsResponse
    from homeassistant.helpers import aiohttp_client as _ac
    from homeassistant.setup import async_setup_component

    class _R(aiohttp.ThreadedResolver):
        async def real_close(self):
            pass

    _ac._async_make_resolver = lambda hass: _R()
    hass = HomeAssistant(str(cfg))
    hass.config.skip_pip = True
    from homeassistant.util import dt as dt_util0
    hass.config.time_zone = "Europe/Amsterdam"
    dt_util0.set_default_time_zone(dt_util0.get_time_zone("Europe/Amsterdam"))
    loader.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await bootstrap.async_load_base_functionality(hass)
    hass.auth = await ha_auth.auth_manager_from_config(hass, [{"type": "homeassistant"}], [])
    await async_setup_component(hass, "http", {"http": {"server_port": 18124}})
    await async_setup_component(hass, "homeassistant", {})

    # agenda "Onderhoud" met een herhalende afspraak en een gewone afspraak
    r = await hass.config_entries.flow.async_init("local_calendar", context={"source": "user"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"calendar_name": "Onderhoud"})
    await hass.async_block_till_done()
    cal = hass.data["calendar"].get_entity("calendar.onderhoud")
    tz = dt_util0.get_default_time_zone()
    start = datetime.combine(VANDAAG - timedelta(days=10), datetime.min.time()).replace(hour=8, minute=30, tzinfo=tz)
    await cal.async_create_event(dtstart=start, dtend=start + timedelta(minutes=30), summary="Oven kleine beurt",
                                 rrule="FREQ=WEEKLY;INTERVAL=2")
    await cal.async_create_event(dtstart=VANDAAG + timedelta(days=3), dtend=VANDAAG + timedelta(days=4), summary="Losse afspraak")

    meldingen = []

    async def notify(call):
        meldingen.append(dict(call.data))

    hass.services.async_register("notify", "mobile_app_a", notify)


    grocy_entry = config_entries.ConfigEntry(
        domain="grocy", title="Grocy", data={"url": "http://127.0.0.1", "port": 9192, "api_key": "geheim", "verify_ssl": False},
        source="user", version=1, minor_version=1, options={}, unique_id=None, discovery_keys={}, subentries_data=None,
    )
    hass.config_entries._entries[grocy_entry.entry_id] = grocy_entry
    await hass.config_entries.flow.async_init("boodschappen", context={"source": "user"})
    await hass.async_block_till_done()

    from homeassistant.components import frontend
    from custom_components.boodschappen import websocket as ws

    check("klusjes" in hass.data.get(frontend.DATA_PANELS, {}), "paneel /klusjes geregistreerd")
    c = ws._coordinator(hass)
    c.eten.instellingen_opslaan({"personen": "A, B"})
    k = c.klusjes
    p = k.payload()
    check(p["agenda"] == "calendar.onderhoud", "agenda Onderhoud automatisch gekozen")
    check([x["naam"] for x in p["instellingen"]["personen"]] == ["A", "B"] and p["instellingen"]["personen"][0]["notify"] == "mobile_app_a"
          and p["instellingen"]["personen"][1]["notify"] is None, "personen uit Eten, telefoon van A gevonden")
    check(hass.states.get("todo.klusjes") is not None, "todo.klusjes bestaat")

    # eenmalig klusje in de backlog, dan plannen
    t = await k.toevoegen({"titel": "Lamp vervangen", "ruimte": "Toilet"})
    check(t["status"] == "backlog" and not t["uid"], "nieuw zonder datum -> backlog, niet in agenda")
    vandaag = VANDAAG.isoformat()
    await k.wijzigen(t["id"], {"datum": vandaag, "persoon": "A"})
    check(t["status"] == "gepland" and t["uid"], "datum gezet -> gepland en in de agenda")
    await k.status(t["id"], "bezig")
    check(t["status"] == "bezig" and t["uid"], "bezig blijft in de agenda")
    await k.afronden(t["id"])
    check(t["status"] == "klaar" and not t["uid"] and t["afgerond"], "eenmalig afgerond, uit de agenda")
    await k.heropenen(t["id"])
    check(t["status"] == "gepland" and t["uid"], "heropenen zet terug in gepland")
    await k.verwijderen(t["id"])
    check(all(x["id"] != t["id"] for x in k.taken), "verwijderd")

    # overnemen uit de agenda
    gevonden = await k.import_zoeken()
    check([x["titel"] for x in gevonden] == ["Oven kleine beurt"], "herhalende afspraak gevonden, losse niet")
    g = gevonden[0]
    check(g["herhaling"] == {"elke": 2, "eenheid": "weken"} and g["tijd"] == "08:30" and g["laatst"] == (VANDAAG - timedelta(days=10)).isoformat()
          and g["volgende"] == (VANDAAG + timedelta(days=4)).isoformat(), "interval, tijd, laatst en volgende kloppen")
    n = await k.importeren([{**g, "persoon": "A"}], verwijderen=True)
    await hass.async_block_till_done()
    check(n == 1, "1 klusje overgenomen")
    ev = await cal.async_get_events(hass, datetime.combine(VANDAAG - timedelta(days=30), datetime.min.time(), tz),
                                    datetime.combine(VANDAAG + timedelta(days=60), datetime.min.time(), tz))
    titels = sorted(e.summary for e in ev)
    check(titels == ["Losse afspraak", "Oven kleine beurt (A)"], f"oude reeks weg, eigen afspraak erin: {titels}")
    oven = next(x for x in k.taken if x["titel"] == "Oven kleine beurt" and x["status"] == "gepland")
    check(oven["tijd"] == "08:30" and oven["reeks"], "tijd en reeks bewaard")
    pl = k.payload()
    check(next(x for x in pl["taken"] if x["id"] == oven["id"])["laatst"] == g["laatst"], "laatst gedaan uit de agenda overgenomen")

    # verschuiven in de agenda wordt overgenomen
    e = next(e for e in ev if e.summary.startswith("Oven"))
    await cal.async_delete_event(e.uid)
    nieuw_start = datetime.combine(VANDAAG + timedelta(days=6), datetime.min.time()).replace(hour=9, tzinfo=tz)
    await cal.async_create_event(dtstart=nieuw_start, dtend=nieuw_start + timedelta(minutes=30), summary=e.summary,
                                 description=e.description)
    await k.sync(forceer=True)
    check(oven["datum"] == (VANDAAG + timedelta(days=6)).isoformat() and oven["tijd"] == "09:00", "verschoven in agenda -> datum en tijd over")

    # afronden van een terugkerend klusje met gekozen datum
    volgende = (VANDAAG + timedelta(days=15)).isoformat()
    nieuw = await k.afronden(oven["id"], volgende)
    check(oven["status"] == "klaar" and nieuw["status"] == "gepland" and nieuw["datum"] == volgende and nieuw["reeks"] == oven["reeks"]
          and nieuw["uid"], "terugkerend: nieuw klusje op gekozen datum, in de agenda")
    pl = k.payload()
    check(next(x for x in pl["taken"] if x["id"] == nieuw["id"])["laatst"] == vandaag, "laatst gedaan = vandaag")
    # heropenen haalt het vervolg weg
    await k.heropenen(oven["id"])
    check(all(x["id"] != nieuw["id"] for x in k.taken) and oven["status"] == "gepland", "heropenen haalt vervolg weg")
    # standaard volgende datum = vandaag + interval
    nieuw = await k.afronden(oven["id"])
    check(nieuw["datum"] == (VANDAAG + timedelta(weeks=2)).isoformat(), "standaard volgende = vandaag + 2 weken")
    # stoppen
    gestopt = await k.afronden(nieuw["id"], geen_volgende=True)
    check(gestopt["status"] == "klaar" and not any(x["status"] != "klaar" and x["titel"] == "Oven kleine beurt" for x in k.taken),
          "niet meer herhalen")

    # meldingen
    a = await k.toevoegen({"titel": "Vandaag-klus", "datum": vandaag, "persoon": "A"})
    b = await k.toevoegen({"titel": "Te laat", "datum": (VANDAAG - timedelta(days=2)).isoformat()})
    await k.toevoegen({"titel": "Van B", "datum": vandaag, "persoon": "B"})
    per = k.meldingen_voor(VANDAAG)
    check(list(per) == ["mobile_app_a"] and [x["titel"] for x in per["mobile_app_a"]] == ["Vandaag-klus"], "A krijgt alleen eigen klus")
    k.instellingen_opslaan({"personen": [{"naam": "A", "notify": "mobile_app_a", "ongetoewezen": True}, {"naam": "B"}]})
    per = k.meldingen_voor(VANDAAG)
    check(sorted(x["titel"] for x in per["mobile_app_a"]) == ["Te laat", "Vandaag-klus"], "ook zonder persoon")
    k.instellingen_opslaan({"meldtijd": "00:00"})
    n = await k.melden()
    await hass.async_block_till_done()
    check(n == 1 and meldingen and "1 te laat" in meldingen[-1]["title"] and meldingen[-1]["data"]["url"] == "/klusjes", "melding verstuurd")
    check(await k.melden() == 0, "maar één keer per dag")
    st = hass.states.get("todo.klusjes")
    check(st.state == str(len(k.open_taken())) and st.attributes["te_laat"] == 1, f"todo-entiteit telt mee: {st.state}")

    # todo: afvinken
    await hass.services.async_call("todo", "update_item", {"item": a["id"], "status": "completed"},
                                   target={"entity_id": "todo.klusjes"}, blocking=True)
    check(a["status"] == "klaar", "afvinken via todo")
    await hass.services.async_call("todo", "add_item", {"item": "Via Assist"}, target={"entity_id": "todo.klusjes"}, blocking=True)
    check(any(x["titel"] == "Via Assist" and x["status"] == "backlog" for x in k.taken), "toevoegen via todo -> backlog")

    # verwijderd in de agenda -> terug naar de backlog (na een wachttijd)
    import custom_components.boodschappen.klusjes as km
    km.VERWIJDER_NA = 0
    await cal.async_delete_event(b["uid"])
    await k.sync(forceer=True)
    await k.sync(forceer=True)
    check(b["status"] == "backlog" and not b["datum"], "in agenda verwijderd -> backlog")

    # websocket-payload
    from custom_components.boodschappen.websocket import KLUSJES_COMMANDS
    check(len(KLUSJES_COMMANDS) == 11, "websocket-commando's")
    await hass.async_stop(force=True)
    await runner.cleanup()
    print("\nRESULTAAT:", "alles goed" if not FOUTEN else f"{len(FOUTEN)} fout(en): {FOUTEN}")


if __name__ == "__main__":
    asyncio.run(main())
