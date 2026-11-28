"""Dag voor dag plannen in een echte HA-core: opties met meekijken (nep-Gemini met SSE), plannen, lijst terugdraaien,
en de websocket-stream zoals het scherm die gebruikt.

Draaien: python3 tests/dagplanner_smoke.py
"""
import asyncio
import time
from datetime import date, timedelta
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
PROMPTS: list[str] = []


def check(ok, wat):
    print(("OK   " if ok else "FOUT ") + wat)
    if not ok:
        FOUTEN.append(wat)


def kies_uit(prompt: str) -> list[dict]:
    """Nep-Gemini: kies de eerste R en de eerste L uit de prompt (en nog een R als die er is)."""
    r = re.findall(r"^(R\d+)\. ", prompt, re.M)
    l = re.findall(r"^(L\d+)\. ", prompt, re.M)
    keuze = (l[:1] + r[:2] + l[1:3])[:3]
    return [{"keuze": k, "reden": f"reden {k}"} for k in keuze]


def koppel_antwoord(instr: str) -> dict:
    """Nep-antwoord op de koppelvraag bij het overnemen van een recept."""
    blok = instr.split("Ingrediënten:\n", 1)[1].split("\nBereiding:", 1)[0]
    regels = [x.split(". ", 1)[1] for x in blok.splitlines() if ". " in x]
    pid = {"2 uien": 15, "250 g rode linzen": 7}
    return {"taal": "nl", "naam": "Linzensoep met kokos", "vega": True, "tags": ["soep"], "tijd": 30,
            "ingredienten": [{"tekst": x, "product_id": pid.get(x), "aantal": 2, "basis": x == "zout"} for x in regels],
            "stappen": ["Fruit de uien.", "Kook de linzen 20 minuten."]}


async def main():
    app = fake_grocy()

    async def stock(req):
        return web.json_response([{"product_id": 15, "amount": 1}])  # 1 ui in huis

    async def receptpagina(req):
        return web.Response(content_type="text/html", text="""<html><head><script type="application/ld+json">
        {"@context": "https://schema.org/", "@type": "Recipe", "name": "Linzensoep met kokos", "recipeYield": "4",
         "recipeIngredient": ["2 uien", "250 g rode linzen", "zout"],
         "recipeInstructions": [{"@type": "HowToStep", "text": "Fruit de uien."}, {"@type": "HowToStep", "text": "Kook de linzen 20 minuten."}]}
        </script></head><body></body></html>""")

    async def robots(req):
        return web.Response(text="User-agent: *\nSitemap: http://127.0.0.1:9193/sitemap.xml\n")

    async def sitemap(req):
        return web.Response(content_type="application/xml", text="""<urlset>
        <url><loc>http://127.0.0.1:9193/linzensoep-met-kokos</loc></url><url><loc>http://127.0.0.1:9193/pasta-pesto-met-spinazie</loc></url>
        </urlset>""")

    # nep-Gemini: SSE met eerst twee gedachten, dan het antwoord in twee stukken
    gemini_vragen = []

    async def gemini(req):
        body = await req.json()
        gemini_vragen.append({"key": req.headers.get("x-goog-api-key"), "pad": req.path, "body": body})
        prompt = body["contents"][0]["parts"][0]["text"]
        if "recept van een kok" in prompt:
            antwoord = json.dumps(koppel_antwoord(prompt))
        else:
            PROMPTS.append(prompt)
            antwoord = json.dumps(kies_uit(prompt))
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(req)
        delen = [{"text": "**Wie eet er mee**\n\nTwee volwassenen, één vega.", "thought": True},
                 {"text": "**Voorraad**\n\nEr is nog ui.", "thought": True},
                 {"text": antwoord[:10]}, {"text": antwoord[10:]}]
        for d in delen:
            await resp.write(b"data: " + json.dumps({"candidates": [{"content": {"parts": [d]}}]}).encode() + b"\r\n\r\n")
            await asyncio.sleep(0.01)
        await resp.write_eof()
        return resp

    app.router.add_get("/api/stock", stock)
    app.router.add_get("/robots.txt", robots)
    app.router.add_get("/sitemap.xml", sitemap)
    app.router.add_get("/linzensoep-met-kokos", receptpagina)
    app.router.add_get("/pasta-pesto-met-spinazie", receptpagina)
    app.router.add_post("/v1beta/models/{model}", gemini)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 9193).start()

    cfg = Path(tempfile.mkdtemp())
    shutil.copytree(ROOT / "custom_components", cfg / "custom_components")

    import aiohttp
    from homeassistant import auth as ha_auth, bootstrap, config_entries, loader
    from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
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
    await async_setup_component(hass, "http", {"http": {"server_port": 18125}})
    await async_setup_component(hass, "homeassistant", {})
    await async_setup_component(hass, "websocket_api", {})

    r = await hass.config_entries.flow.async_init("local_calendar", context={"source": "user"})
    await hass.config_entries.flow.async_configure(r["flow_id"], {"calendar_name": "Eet Planning"})
    await hass.async_block_till_done()

    ai_vragen = []

    async def ai(call):
        ai_vragen.append(call.data["task_name"])
        instr = call.data["instructions"]
        if call.data["task_name"] == "Eten: dagvoorstel":
            PROMPTS.append(instr)
            return {"data": json.dumps(kies_uit(instr))}
        if "recept van een kok" in instr:
            return {"data": json.dumps(koppel_antwoord(instr))}
        return {"data": "{}"}

    hass.services.async_register("ai_task", "generate_data", ai, supports_response=SupportsResponse.ONLY)
    hass.states.async_set("ai_task.nep", "unknown")

    grocy_entry = config_entries.ConfigEntry(
        domain="grocy", title="Grocy", data={"url": "http://127.0.0.1", "port": 9193, "api_key": "geheim", "verify_ssl": False},
        source="user", version=1, minor_version=1, options={}, unique_id=None, discovery_keys={}, subentries_data=None,
    )
    hass.config_entries._entries[grocy_entry.entry_id] = grocy_entry
    await hass.config_entries.flow.async_init("boodschappen", context={"source": "user"})
    await hass.async_block_till_done()

    from custom_components.boodschappen import gemini as gm, websocket as ws

    c = ws._coordinator(hass)
    e = c.eten
    e._agenda()
    e.instellingen_opslaan({"personen": "A, B", "bronnen": [
        {"naam": "Testkok", "domein": "127.0.0.1", "pad": "", "type": "website", "aan": True, "notitie": ""}]})
    # de bron draait op poort 9193: robots.txt via https://127.0.0.1 omleiden
    echte_haal = e._haal

    async def haal(url, max_tekens=3_000_000):
        return await echte_haal(url.replace("https://127.0.0.1/", "http://127.0.0.1:9193/"), max_tekens)

    e._haal = haal
    soep = e.recept_opslaan({"naam": "Paprikasoep", "porties": 2, "tijd": 25, "vega": True,
                             "ingredienten": [{"tekst": "2 uien", "product_id": 15, "aantal": 1}, {"tekst": "3 paprika", "product_id": 7, "aantal": 3}],
                             "stappen": ["Kook"]})
    wraps = e.recept_opslaan({"naam": "Wraps", "porties": 2, "ingredienten": [{"tekst": "wraps", "product_id": 1, "aantal": 1}], "stappen": ["Vullen"]})
    morgen = (VANDAAG + timedelta(days=1)).isoformat()
    overmorgen = (VANDAAG + timedelta(days=2)).isoformat()

    # 1. zonder Google-sleutel: via de AI-taak, met stappen maar zonder gedachten
    ev: list[dict] = []
    opties = await e.dag_opties(morgen, {"voorraad": True, "snel": True, "aanbieding": False,
                                         "bronnen": ["__bibliotheek__", "Testkok"], "wens": "soep"}, ["A", "B"], ev.append)
    soorten = [x["soort"] for x in ev]
    check(len(opties) == 3 and opties[0]["soort"] == "link" and opties[0]["url"].endswith("linzensoep-met-kokos"),
          f"3 opties, eerste een link van de bron ({[o['titel'] for o in opties]})")
    check(opties[1]["recept_id"] in (soep, wraps) and opties[1]["bron"] == "Mijn bibliotheek", "eigen recept als optie")
    check("gedachte" not in soorten and any("AI-taak" in x.get("tekst", "") for x in ev), "zonder sleutel: AI-taak, geen gedachten")
    check(any(x.get("id") == "bron-Testkok" and "2 links" in x["tekst"] for x in ev), "stap: bron ingelezen")
    check(ai_vragen == ["Eten: dagvoorstel"], "één lichte AI-vraag")
    p = PROMPTS[-1]
    check("hooguit 30 minuten" in p and "In huis: Uien" in p and "In de aanbieding" not in p and "Producten (id" not in p,
          "prompt volgt de vinkjes en is licht")
    check(e.s["dagopties"][morgen]["filters"]["snel"] and e.inst["dagfilters"]["bronnen"] == ["__bibliotheek__", "Testkok"],
          "opties en vinkjes bewaard")

    # 1b. bronindex: links stonden al in de index (na het inlezen); de achtergrondronde haalt de receptgegevens op
    from custom_components.boodschappen import bronindex as bix
    bix.PAUZE = 0
    sl = "127.0.0.1"
    check(e.idx.get(sl, {}).get("klaar") and len(e.idx[sl]["links"]) == 2, "links na het inlezen meteen in de index")
    await e._index_ronde()
    st = bix.status(e.idx[sl])
    check(st["recepten"] == 2 and st["bezig"] is None, f"achtergrondronde haalt receptgegevens op ({st})")
    item = e.idx[sl]["d"]["http://127.0.0.1:9193/linzensoep-met-kokos"]
    check(item["t"] == "Linzensoep met kokos" and item["v"] is True and "rode linzen" in item["i"], f"samenvatting van het recept ({item})")
    links, status = await e._bron_links(e.inst["bronnen"][0])
    check(status.get("index") and len(links) == 2, "links komen daarna uit de index (geen sitemap meer lezen)")
    info = e.bronnen_info()["Testkok"]["index"]
    check(info["recepten"] == 2 and info["links"] == 2, "indexstatus in het paneel")
    await e._index_store.async_save({"bronnen": e.idx})
    opgeslagen = await e._index_store.async_load()
    check(len(opgeslagen["bronnen"][sl]["d"]) == 2, "index blijft bewaard (overleeft een herstart)")
    ev.clear()
    PROMPTS.clear()
    opties_i = await e.dag_opties(morgen, {"voorraad": True, "snel": False, "aanbieding": False, "bronnen": ["Testkok"], "wens": "linzen"},
                                  ["A", "B"], ev.append)
    check("| ingrediënten:" in PROMPTS[-1] and "vega" in PROMPTS[-1], "prompt bevat tijd/vega/ingrediënten uit de index")
    lo = next(o for o in opties_i if o["soort"] == "link")
    check(lo["titel"] == "Linzensoep met kokos" and lo["vega"] and lo["labels"].get("in_huis") == ["Uien"],
          f"optiekaart met gegevens en labels uit de index ({lo})")
    e.index_vernieuwen("Testkok")
    check(e.idx[sl]["ts"] == 0, "opnieuw indexeren zet de linklijst klaar voor vernieuwen")
    e.idx[sl]["ts"] = time.time()

    # alleen de bron (geen bibliotheek): geen R-nummers
    ev.clear()
    PROMPTS.clear()
    await e.dag_opties(morgen, {"bronnen": ["Testkok"]}, None, ev.append)
    check("Eigen recepten:" not in PROMPTS[-1] and "L1." in PROMPTS[-1], "alleen Testkok: alleen links van die bron")

    # 2. met Google-sleutel: direct naar Gemini met streaming en gedachten
    google = config_entries.ConfigEntry(
        domain="google_generative_ai_conversation", title="Google", data={"api_key": "sleutel123"}, source="user", version=2,
        minor_version=1, options={}, unique_id=None, discovery_keys={},
        subentries_data=[{"data": {"chat_model": "models/gemini-test"}, "subentry_type": "ai_task_data", "title": "AI", "unique_id": None}],
    )
    hass.config_entries._entries[google.entry_id] = google
    gm.API = "http://127.0.0.1:9193/v1beta/models/{model}:streamGenerateContent?alt=sse"
    ai_vragen.clear()
    ev.clear()
    opties = await e.dag_opties(overmorgen, {"bronnen": ["__bibliotheek__", "Testkok"]}, ["A"], ev.append)
    gedachten = [x["tekst"] for x in ev if x["soort"] == "gedachte"]
    check(len(gedachten) == 2 and "Wie eet er mee" in gedachten[0], "gedachten van Gemini live doorgegeven")
    check(gemini_vragen and gemini_vragen[-1]["key"] == "sleutel123" and "gemini-test" in gemini_vragen[-1]["pad"]
          and gemini_vragen[-1]["body"]["generationConfig"]["thinkingConfig"] == {"includeThoughts": True},
          "sleutel en model uit de Google-integratie")
    check(len(opties) == 3 and not ai_vragen, "antwoord uit de stream, AI-taak niet nodig")

    # 3. plan in: link → recept ophalen, plannen, producten direct op de lijst
    STATE["log"].clear()
    ev.clear()
    link = next(o for o in e.s["dagopties"][morgen]["opties"] if o["soort"] == "link")
    res = await e.dag_inplannen(morgen, {"soort": "link", "url": link["url"], "bron": "Testkok"}, ["A", "B"], ev.append)
    item = next(x for x in e.s["planning"] if x["datum"] == morgen)
    adds = [x[1] for x in STATE["log"] if x[0] == "add"]
    print("   op lijst:", [(a.get("product_id"), a.get("amount")) for a in adds], "in huis:", res["in_huis"])
    # 2 porties van een 4-persoonsrecept: uien 2x0.5=1 (1 in huis → 0 kopen), linzen 2x0.5=1 → 1
    check(res["titel"] == "Linzensoep met kokos" and res["recept_id"] in e.s["recepten"], "recept overgenomen en gepland")
    check([(a["product_id"], a["amount"]) for a in adds] == [(7, 1.0)] and res["in_huis"] == ["Uien"], "alleen wat nodig is op de lijst")
    check(item["lijst"] == [{"list_id": item["lijst"][0]["list_id"], "product_id": 7, "aantal": 1.0}] and item["uit_voorraad"] == {"15": 1.0},
          "maaltijd onthoudt lijstregels en voorraad")
    check(any(x.get("id") == "ophalen" and x["status"] == "klaar" for x in ev) and morgen not in e.s["dagopties"], "stappen bij plannen, opties opgeruimd")
    kv = gemini_vragen[-1]["body"]
    check("recept van een kok" in kv["contents"][0]["parts"][0]["text"] and kv["generationConfig"]["maxOutputTokens"] >= 16000
          and "thinkingConfig" not in kv["generationConfig"] and "Eten: recept koppelen" not in ai_vragen,
          "koppelen gaat direct naar Gemini met ruime antwoordlengte (geen MAX_TOKENS)")

    # tweede dag gebruikt ook ui: de ene ui is al voor morgen → nu kopen
    STATE["log"].clear()
    await e.dag_inplannen(overmorgen, {"soort": "recept", "recept_id": soep}, ["A", "B"], ev.append)
    adds = {x[1]["product_id"]: x[1]["amount"] for x in STATE["log"] if x[0] == "add"}
    puts = [x for x in STATE["log"] if x[0] == "put"]
    print("   tweede dag:", adds, puts)
    check(adds.get(15) == 1.0 and (adds.get(7) == 3.0 or any(x[2].get("amount") == 4.0 for x in puts)),
          "ui die al gereserveerd is telt niet als voorraad; paprika samengevoegd")

    # wisselen: ander gerecht op dag 2 → wat de soep op de lijst zette gaat eraf
    STATE["log"].clear()
    await e.dag_inplannen(overmorgen, {"soort": "recept", "recept_id": wraps}, ["A", "B"], ev.append)
    log = STATE["log"]
    print("   wisselen:", log)
    weg = [x for x in log if x[0] in ("put", "del")]
    check(len(weg) >= 2 and any(x[0] == "add" and x[1]["product_id"] == 1 for x in log), "oude producten eraf, wraps erop")

    # vriezerdag: plan zonder boodschappen, lijst van wraps eraf
    STATE["log"].clear()
    r2 = await e.dag_inplannen(overmorgen, {"soort": "vriezer"}, None, ev.append)
    check(r2["titel"] == "Uit de vriezer" and any(x[0] in ("put", "del") for x in STATE["log"]), "vriezerdag draait de lijst terug")

    # verwijderen draait ook terug
    STATE["log"].clear()
    await e.plan_verwijderen(item["id"])
    check(any(x[0] in ("put", "del") for x in STATE["log"]), "maaltijd verwijderd → linzen van de lijst")

    # 4. via de websocket, zoals het scherm
    user = await hass.auth.async_create_user("test", group_ids=["system-admin"])
    rt = await hass.auth.async_create_refresh_token(user, client_name="t", token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN)
    token = hass.auth.async_create_access_token(rt)
    async with aiohttp.ClientSession() as s, s.ws_connect("http://127.0.0.1:18125/api/websocket") as w:
        await w.receive_json()
        await w.send_json({"type": "auth", "access_token": token})
        check((await w.receive_json())["type"] == "auth_ok", "websocket ingelogd")
        await w.send_json({"id": 1, "type": "boodschappen/eten_dag_opties", "datum": morgen,
                           "filters": {"bronnen": ["__bibliotheek__"]}, "aanwezig": ["A"]})
        msgs = []
        while True:
            m = await asyncio.wait_for(w.receive_json(), 30)
            msgs.append(m)
            if m.get("type") == "event" and m["event"]["soort"] in ("klaar", "fout"):
                break
        check(msgs[0]["type"] == "result" and msgs[0]["success"], "eerst een bevestiging")
        evs = [m["event"] for m in msgs[1:]]
        check(any(x["soort"] == "gedachte" for x in evs) and evs[-1]["soort"] == "klaar" and len(evs[-1]["resultaat"]) >= 1
              and "dagopties" in evs[-1]["eten"], "stappen, gedachten en klaar met de nieuwe stand")
        # fout netjes gemeld
        await w.send_json({"id": 2, "type": "boodschappen/eten_dag_inplannen", "datum": morgen, "keuze": {"soort": "recept", "recept_id": "bestaatniet"}})
        m = await w.receive_json()
        while not (m.get("type") == "event" and m["event"]["soort"] in ("klaar", "fout")):
            m = await asyncio.wait_for(w.receive_json(), 30)
        check(m["event"] == {"soort": "fout", "tekst": "Recept niet gevonden"}, "fout als event")

    await hass.async_stop()
    await runner.cleanup()
    print("\n" + ("ALLES GOED" if not FOUTEN else f"{len(FOUTEN)} FOUT(EN): " + "; ".join(FOUTEN)))
    sys.exit(1 if FOUTEN else 0)


asyncio.run(main())
