"""Start een echte HA-core met de integratie, een Local Calendar als eet-agenda en een nep-AI-taak.

Draaien: python3 tests/eten_smoke.py
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

    # eet-agenda: Local Calendar
    r = await hass.config_entries.flow.async_init("local_calendar", context={"source": "user"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"calendar_name": "Eet Planning"})
    await hass.async_block_till_done()
    check(hass.states.get("calendar.eet_planning") is not None, "agenda calendar.eet_planning bestaat")

    # een los event (zoals eerder handmatig ingevoerd)
    morgen = VANDAAG + timedelta(days=1)
    await hass.services.async_call("calendar", "create_event", {
        "summary": "Nasi (opa en oma)", "start_date_time": f"{morgen.isoformat()} 17:00:00",
        "end_date_time": f"{morgen.isoformat()} 18:00:00"}, target={"entity_id": "calendar.eet_planning"}, blocking=True)
    await hass.services.async_call("calendar", "create_event", {
        "summary": "Papier", "start_date": morgen.isoformat(), "end_date": (morgen + timedelta(days=1)).isoformat()},
        target={"entity_id": "calendar.eet_planning"}, blocking=True)

    # nep-AI-taak
    vragen = []

    async def ai(call):
        vragen.append(call.data["task_name"])
        AI_LOG.append(call.data["instructions"])
        instr = call.data["instructions"]
        if "weekmenu" in call.data["task_name"]:
            blok = instr.split("Te plannen dagen:\n", 1)[1].split("\n\n", 1)[0]
            dagen = [{"datum": m.group(2), "dag": m.group(1).lower()}
                     for m in re.finditer(r"^- ([A-Z]+) (\d{4}-\d{2}-\d{2}) — wij koken", blok, re.M)]
            recepten = json.loads(re.search(r"Recepten: (\[.*?\])\n\nProducten", instr, re.S).group(1))
            per = {r["naam"]: r["id"] for r in recepten}
            uit = []
            for d in dagen:
                opties = []
                if "Linzensoep" in per:
                    opties.append({"rol": "favoriet", "recept_id": per["Linzensoep"], "titel": "Linzensoep", "reden": "lekker"})
                if "Wraps" in per:
                    opties.append({"rol": "slim", "recept_id": per["Wraps"], "titel": "Wraps", "reden": "wraps in de aanbieding"})
                opties.append({"rol": "nieuw", "recept_id": None, "titel": "Vega goulash", "reden": "eens proberen", "nieuw_recept": {
                    "naam": "Vega goulash", "porties": 5, "tijd": 50, "vega": True, "tags": ["stoof"],
                    "ingredienten": [{"tekst": "3 lente-uitjes", "product_id": 23, "aantal": 3}, {"tekst": "paprikapoeder", "basis": True}],
                    "stappen": ["Snijd", "Stoof 40 min"]}})
                while len(opties) < 3:
                    opties.append({"rol": "nieuw", "recept_id": None, "titel": f"Extra {len(opties)}", "reden": "-"})
                uit.append({"datum": d["datum"], "dag": d["dag"], "opties": opties})
            return {"data": json.dumps(uit)}
        if "dag aanpassen" in call.data["task_name"]:
            dag = re.search(r"Deze dag: ([A-Z]+) (\S+)", instr)
            assert dag, "dag ontbreekt in prompt"
            if "Wens: we gaan uit eten" in instr:
                return {"data": json.dumps({"recept_id": None, "titel": "Geen maaltijd", "geen": True, "kookt": "", "reden": "uit eten"})}
            if "goulash" in instr:
                return {"data": json.dumps({"recept_id": None, "titel": "Vega goulash", "kookt": "Papa", "porties": 5,
                    "vriezer": 0, "boodschappen": True, "reden": "wens", "nieuw_recept": {
                        "naam": "Vega goulash", "porties": 5, "tijd": 50, "vega": True, "tags": ["stoof"],
                        "ingredienten": [{"tekst": "3 lente-uitjes", "product_id": 23, "aantal": 3}, {"tekst": "paprikapoeder", "basis": True}],
                        "stappen": ["Snijd", "Stoof 40 min"]}})}
            rec = json.loads(re.search(r"Recepten: (\[.*?\])\n", instr, re.S).group(1))
            return {"data": json.dumps({"recept_id": rec[0]["id"], "titel": rec[0]["naam"], "kookt": "Mama", "porties": 4,
                                        "vriezer": 0, "boodschappen": True, "reden": "uit de bibliotheek", "nieuw_recept": None})}
        return {"data": "```json\n" + json.dumps({
            "naam": "Linzensoep", "porties": 4, "tijd": 35, "vega": True, "tags": ["soep"],
            "ingredienten": [{"tekst": "2 uien", "product_id": 15, "aantal": 2},
                             {"tekst": "zout", "product_id": None, "aantal": 1, "basis": True},
                             {"tekst": "2 paprika", "product_id": 7, "aantal": 2}],
            "stappen": ["Fruit de ui", "Kook alles 20 minuten"]}) + "\n```"}

    hass.services.async_register("ai_task", "generate_data", ai, supports_response=SupportsResponse.ONLY)
    hass.states.async_set("ai_task.nep", "unknown")

    grocy_entry = config_entries.ConfigEntry(
        domain="grocy", title="Grocy", data={"url": "http://127.0.0.1", "port": 9192, "api_key": "geheim", "verify_ssl": False},
        source="user", version=1, minor_version=1, options={}, unique_id=None, discovery_keys={}, subentries_data=None,
    )
    hass.config_entries._entries[grocy_entry.entry_id] = grocy_entry
    await hass.config_entries.flow.async_init("boodschappen", context={"source": "user"})
    await hass.async_block_till_done()

    from homeassistant.components import frontend
    from custom_components.boodschappen import websocket as ws

    check("eten" in hass.data.get(frontend.DATA_PANELS, {}), "paneel /eten geregistreerd")
    c = ws._coordinator(hass)
    e = c.eten
    e._agenda()
    check(e.inst["agenda"] == "calendar.eet_planning", "agenda automatisch gekozen")
    e.instellingen_opslaan({"personen": "A, B, C", "huisregels": "Zondag dubbel koken"})
    check(e.inst["personen"] == ["A", "B", "C"], "personen opgeslagen")

    # recept importeren uit tekst + handmatig recept
    rid = await e.recept_importeren(None, "Linzensoep met ui en paprika ...")
    r = e.s["recepten"][rid]
    check(r["naam"] == "Linzensoep" and len(r["ingredienten"]) == 3 and r["ingredienten"][1]["basis"], "recept via AI omgezet")
    # koppelen vult vega/tijd/labels aan als die ontbreken
    kale = e.recept_opslaan({"naam": "Kale soep", "ingredienten": ["2 uien"], "stappen": ["Kook"]})
    await e.recept_koppelen(kale)
    k = e.s["recepten"][kale]
    check(k["vega"] and k["tijd"] == 35 and k["tags"] == ["soep"] and k["ingredienten"][0]["product_id"] == 15
          and k["stappen"] == ["Kook"], "koppelen vult aan, stappen blijven")
    e.recept_verwijderen(kale)
    rid2 = e.recept_opslaan({"naam": "Wraps", "porties": 4, "ingredienten": [{"tekst": "wraps", "product_id": 1, "aantal": 1}],
                             "stappen": ["Vullen"]})

    # agenda teruglezen: los event zichtbaar
    await e.sync(forceer=True)
    check([x["titel"] for x in e.extern] == ["Nasi (opa en oma)"], "los agenda-event zichtbaar, hele-dag-event genegeerd")

    # dagen-info voor de keuzestap: standaard uit de profielen en dagregels
    dag6 = (VANDAAG + timedelta(days=6)).isoformat()
    info = e.dagen_info(morgen.isoformat(), dag6)
    check(len(info) == 6 and info[0]["agenda"] == "Nasi (opa en oma)" and info[0]["aanwezig"] == ["A", "B", "C"] and info[0]["aan"],
          "dagen-info met aanwezigen en agenda-idee")

    # vriezerdag: geen AI-opties, vaste titel, geen boodschappen
    vragen.clear()
    await e.voorstel_maken([{"datum": dag6, "aan": True, "soort": "vriezer"}], "")
    vd = e.s["voorstel"]["dagen"][0]
    check(not vd["opties"] and e._gekozen(vd)["titel"] == "Uit de vriezer" and not vragen, "vriezerdag zonder AI, vaste titel")
    e.voorstel_weg()

    # weekmenu voorstellen voor 3 dagen vanaf morgen, met 3 opties per dag
    dagen3 = [(morgen + timedelta(days=i)).isoformat() for i in range(3)]
    await e.voorstel_maken([{"datum": dagen3[0], "aan": True, "aanwezig": ["A", "B", "C"]},
                            {"datum": dagen3[1], "aan": True, "aanwezig": ["A", "B", "C"], "wens": "iets met soep"},
                            {"datum": dagen3[2], "aan": True, "aanwezig": ["A", "B", "C"]}], "lekker herfstig")
    v = e.s["voorstel"]
    d0 = v["dagen"][0]
    check(len(v["dagen"]) == 3 and all(len(x["opties"]) == 3 for x in v["dagen"]) and d0["keuze"] == 0, "3 dagen met elk 3 opties")
    check(d0["agenda"] == "Nasi (opa en oma)" and d0["vervangt"] and d0["porties"] == 3, "agenda-idee meegegeven, porties uit aanwezigen")
    check("in de agenda staat al als idee: Nasi (opa en oma)" in AI_LOG[-1] and "wens voor deze dag: iets met soep" in AI_LOG[-1]
          and "In huis: Uien" in AI_LOG[-1], "prompt met agenda, wens per dag en voorraad")
    soep = d0["opties"][0]
    check(soep["labels"]["in_huis"] == ["Uien"] and soep["rol"] == "favoriet", "labels: uien in huis")
    nieuw_c = d0["opties"][2]["concept"]
    check(nieuw_c and nieuw_c["id"] not in e.s["recepten"] and "nieuw" in nieuw_c["tags"], "nieuw recept als concept, nog niet bewaard")
    e.voorstel_wijzig(dagen3[1], {"keuze": 1})
    e.voorstel_wijzig(dagen3[2], {"keuze": 2})
    STATE["log"].clear()
    res = await e.voorstel_goedkeuren()
    check(res["gepland"] == 3 and len(res["plan_ids"]) == 3, f"3 dagen gepland ({res})")
    check(not [l for l in STATE["log"] if l[0] == "add"], "nog niets op de lijst voor het overzicht")
    concept_id = next(p for p in e.s["planning"] if p["datum"] == dagen3[2])["recept_id"]
    check(concept_id in e.s["recepten"], "gekozen nieuw recept nu in de bibliotheek")
    ov = await e.benodigd_overzicht(res["plan_ids"])
    rij = {r["product_id"]: r for r in ov["rijen"]}
    print("   overzicht:", [(r["naam"], r["nodig"], r["in_huis"], r["toevoegen"]) for r in ov["rijen"]])
    # 3 porties: linzensoep (4p) 2 uien -> 2, 1 in huis -> 1; paprika 2; wraps 1; goulash (5p) 3 lente-ui -> 2
    check(rij[15]["toevoegen"] == 1 and rij[15]["in_huis"] == 1 and rij[7]["toevoegen"] == 2 and rij[1]["toevoegen"] == 1
          and rij[23]["toevoegen"] == 2, "benodigd: geschaald op porties, voorraad afgetrokken")
    n = await e.lijst_toevoegen(ov["plan_ids"], [{"product_id": r["product_id"], "aantal": r["toevoegen"], "list_id": r["list_id"]}
                                                 for r in ov["rijen"] if r["product_id"] != 7], [{"tekst": "verse kruiden"}])
    adds = [l[1] for l in STATE["log"] if l[0] == "add"]
    print("   op lijst:", [(a.get("product_id"), a.get("amount"), a.get("note")) for a in adds])
    per = {a["product_id"]: a["amount"] for a in adds}
    check(n == 4 and per.get(15) == 1 and 7 not in per and per.get(23) == 2 and any(a.get("note") == "verse kruiden" for a in adds),
          "alleen gekozen producten op de lijst, los item als tekst")
    check(all(p["op_lijst"] for p in e.s["planning"] if p["id"] in ov["plan_ids"]), "planning gemarkeerd als op de lijst")

    # events in de agenda met kenmerk
    tz = hass.config.time_zone
    from homeassistant.util import dt as dt_util
    start = dt_util.start_of_local_day(datetime.combine(VANDAAG, datetime.min.time()))
    events = await hass.services.async_call("calendar", "get_events", {"start_date_time": start.isoformat(), "duration": {"days": 7}},
                                            target={"entity_id": "calendar.eet_planning"}, blocking=True, return_response=True)
    evs = events["calendar.eet_planning"]["events"]
    print("   agenda:", [(x["start"], x["summary"]) for x in evs])
    eigen = [x for x in evs if "eten:" in (x.get("description") or "")]
    check(len(eigen) == 3 and len([x for x in evs if "17:00" in x["start"]]) == 3, "3 events om 17:00 met kenmerk, los event vervangen")
    check(all(p.get("uid") for p in e.s["planning"]), "uid van events opgeslagen")
    check(any("Porties: 3" in x["description"] for x in eigen), "beschrijving met porties")

    # in de agenda verschuiven: eerste maaltijd naar dag 5
    plan = sorted(e.s["planning"], key=lambda p: p["datum"])[0]
    ent = hass.data["calendar"].get_entity("calendar.eet_planning")
    nieuwe_dag = VANDAAG + timedelta(days=5)
    nstart = dt_util.start_of_local_day(datetime.combine(nieuwe_dag, datetime.min.time())) + timedelta(hours=17)
    await ent.async_update_event(plan["uid"], {"dtstart": nstart, "dtend": nstart + timedelta(hours=1),
                                               "summary": plan["titel"], "description": f"verschoven\n\neten:{plan['id']}"})
    await e.sync(forceer=True)
    check(plan["datum"] == nieuwe_dag.isoformat(), "verschuiving in agenda overgenomen")

    # agenda tijdelijk leeg (bijv. net na opstarten): planning blijft staan
    voor = len(e.s["planning"])
    echte = ent.async_get_events

    async def leeg(*a, **k):
        return [x for x in await echte(*a, **k) if "eten:" not in (x.description or "")][:1] or []

    ent.async_get_events = leeg
    await e.sync(forceer=True)
    ent.async_get_events = echte
    await e.sync(forceer=True)
    check(not any(p.get("mist_sinds") for p in e.s["planning"]), "na volledige respons weer in orde")
    check(len(e.s["planning"]) == voor and all(p.get("uid") for p in e.s["planning"] if not p.get("historie")),
          "lege/halve agenda-respons verwijdert niets")

    # in de agenda verwijderen
    tweede = sorted(e.s["planning"], key=lambda p: p["datum"])[0]
    await ent.async_delete_event(tweede["uid"])
    await e.sync(forceer=True)
    check(any(p["id"] == tweede["id"] for p in e.s["planning"]) and tweede.get("mist_sinds"), "eerste keer weg: nog niet verwijderd")
    tweede["mist_sinds"] -= 601
    await e.sync(forceer=True)
    check(all(p["id"] != tweede["id"] for p in e.s["planning"]), "verwijdering in agenda overgenomen")

    # los event overnemen en koppelen aan recept
    overmorgen = VANDAAG + timedelta(days=2)
    await hass.services.async_call("calendar", "create_event", {
        "summary": "Soep", "start_date_time": f"{(VANDAAG + timedelta(days=8)).isoformat()} 17:00:00",
        "end_date_time": f"{(VANDAAG + timedelta(days=8)).isoformat()} 18:00:00"}, target={"entity_id": "calendar.eet_planning"}, blocking=True)
    await e.sync(forceer=True)
    los = e.extern[0]
    pid = (await e.overnemen(los["datum"], los["uid"], rid, None))["id"]
    await e.sync(forceer=True)
    check(not e.extern and any(p["id"] == pid and p["titel"] == "Linzensoep" for p in e.s["planning"]), "los event overgenomen")

    # wijzigen via de planning: ander recept -> event vervangen
    p = next(p for p in e.s["planning"] if p["id"] == pid)
    oude_uid = p["uid"]
    await e.plan(p["datum"], {"recept_id": rid2}, pid)
    check(p["titel"] == "Wraps" and p["uid"] and p["uid"] != oude_uid, "recept gewisseld, event vervangen")

    # historie (zonder agenda)
    n = e.historie([{"datum": (VANDAAG - timedelta(days=20)).isoformat(), "recept_id": rid},
                    {"datum": (VANDAAG - timedelta(days=21)).isoformat(), "recept_id": "bestaat-niet"}])
    lr = next(x for x in ws.eten_payload(c)["recepten"] if x["id"] == rid)
    check(n == 1 and lr["laatst"] == (VANDAAG - timedelta(days=20)).isoformat(), "historie vastgelegd")
    await e.sync(forceer=True)
    check(any(p.get("historie") for p in e.s["planning"]), "historie blijft na sync")
    # plannen op een dag met een los event: los event verdwijnt
    dag9 = (VANDAAG + timedelta(days=9)).isoformat()
    await hass.services.async_call("calendar", "create_event", {"summary": "Idee", "start_date_time": f"{dag9} 17:00:00",
        "end_date_time": f"{dag9} 18:00:00"}, target={"entity_id": "calendar.eet_planning"}, blocking=True)
    await e.sync(forceer=True)
    pl = await e.plan(dag9, {"recept_id": rid})
    await e.sync(forceer=True)
    check(not any(x["datum"] == dag9 for x in e.extern) and pl["uid"], "plannen vervangt los event")
    await e.plan_verwijderen(pl["id"])

    # beoordelen
    e.beoordeel(pid, "A", 5)
    e.beoordeel(pid, "B", 2)
    e.beoordeel(pid, "B", 3)  # overschrijft
    data = ws.eten_payload(c)
    sc = next(x for x in data["recepten"] if x["id"] == rid2)["score"]
    check(sc and sc["gem"] == 4.0 and sc["personen"] == {"A": 5.0, "B": 3.0}, "beoordeling per persoon")
    check(len(data["producten"]) > 10 and data["agendas"] == ["calendar.eet_planning"], "payload compleet")

    # websocket-commando's geregistreerd
    from homeassistant.components.websocket_api import const as wsc
    handlers = hass.data[wsc.DOMAIN] if wsc.DOMAIN in hass.data else {}
    check("boodschappen/eten_goedkeuren" in handlers, "websocket-commando's geregistreerd")

    # --- per dag nieuwe opties, feedback, weggooien ---
    dag12 = (VANDAAG + timedelta(days=12)).isoformat()
    await e.voorstel_maken([{"datum": dag12, "aan": True}], "")
    e.feedback(rid2, None, "niet_lekker", True)
    await e.voorstel_dag_opnieuw(dag12, "iets warms")
    vd = e.s["voorstel"]["dagen"][0]
    check(vd["wens"] == "iets warms" and "Wraps" not in [o["titel"] for o in vd["opties"]]
          and '"Wraps"' not in AI_LOG[-1].split("Recepten: ")[1].split("Producten")[0], "niet lekker: niet meer voorgesteld")
    check("Vinden we NIET lekker" in AI_LOG[-1] and "Wraps" in AI_LOG[-1], "feedback in de prompt")
    e.feedback(rid2, None, "niet_lekker", False)
    e.feedback(None, "Vega goulash", "lekker_snel", True)
    check(e.s["feedback"]["titel:vega goulash"]["soorten"].get("lekker_snel"), "feedback op titel (concept)")
    e.feedback(None, "Vega goulash", "teveel_werk", True)
    check(list(e.s["feedback"]["titel:vega goulash"]["soorten"]) == ["teveel_werk"], "tegenstrijdige feedback vervangen")
    weg_id = next(o["concept"]["id"] for o in vd["opties"] if o.get("concept"))
    e.voorstel_weg()
    check(weg_id not in e.s["recepten"], "weggegooid concept niet bewaard")
    p12 = await e.plan(dag12, {"recept_id": rid})
    cid = rid

    # geplande dag: eerst suggestie, planning blijft tot toepassen
    oud_uid = p12["uid"]
    await e.dag_aanpassen(dag12, "iets anders", p12["id"])
    sug = e.s["suggesties"].get(dag12)
    check(sug and p12["recept_id"] == cid and p12["uid"] == oud_uid, "suggestie getoond, planning ongewijzigd")
    r_t = await e.suggestie_toepassen(dag12)
    check(r_t["plan_ids"] == [p12["id"]], "toepassen geeft plan voor het boodschappenoverzicht")
    check(p12["recept_id"] == sug["recept_id"] and p12["uid"] != oud_uid and dag12 not in e.s["suggesties"],
          "suggestie toegepast, agenda bijgewerkt")
    await e.dag_aanpassen(dag12, "goulash", p12["id"])
    sug2 = e.s["suggesties"][dag12]["concept"]["id"]
    e.suggestie_weg(dag12)
    check(dag12 not in e.s["suggesties"] and sug2 not in e.s["recepten"], "suggestie weggegooid")
    await e.dag_aanpassen(dag12, "goulash", p12["id"])
    sug3 = e.s["suggesties"][dag12]["concept"]["id"]
    await e.suggestie_toepassen(dag12)
    check(sug3 in e.s["recepten"] and p12["titel"] == "Vega goulash", "toegepast concept in bibliotheek")
    await e.plan_verwijderen(p12["id"])

    # --- los agenda-idee (bijv. vandaag) via de AI aanpassen ---
    dag18 = (VANDAAG + timedelta(days=18)).isoformat()
    await hass.services.async_call("calendar", "create_event", {"summary": "Curry (voor 3x koken)", "start_date_time": f"{dag18} 17:00:00",
        "end_date_time": f"{dag18} 18:00:00"}, target={"entity_id": "calendar.eet_planning"}, blocking=True)
    await e.sync(forceer=True)
    vragen.clear()
    await e.dag_aanpassen(dag18, "goulash")
    check(dag18 in e.s["suggesties"] and not any(p["datum"] == dag18 for p in e.s["planning"]), "suggestie bij los agenda-idee")
    check(AI_LOG and "Nu in de agenda voor deze dag: Curry (voor 3x koken)" in AI_LOG[-1], "agenda-idee in de prompt")
    await e.suggestie_toepassen(dag18)
    await e.sync(forceer=True)
    p18 = next(p for p in e.s["planning"] if p["datum"] == dag18)
    check(p18["titel"] == "Vega goulash" and not any(x["datum"] == dag18 for x in e.extern), "toegepast, los idee vervangen")
    await e.plan_verwijderen(p18["id"])

    # --- boodschappen eerst uit, later alsnog aan ---
    dag20 = (VANDAAG + timedelta(days=20)).isoformat()
    rid20 = e.recept_opslaan({"naam": "Worteltaart", "porties": 4, "ingredienten": [{"tekst": "1 kg wortels", "product_id": 42, "aantal": 1}]})
    STATE["log"].clear()
    p20 = await e.plan(dag20, {"recept_id": rid20, "boodschappen": False, "lijst": True})
    check(not [l for l in STATE["log"] if l[0] == "add"], "boodschappen uit: niets op de lijst")
    await e.plan(dag20, {"boodschappen": True, "lijst": True}, p20["id"])
    check([l for l in STATE["log"] if l[0] == "add"], "boodschappen later aan: ingrediënten alsnog op de lijst")
    await e.plan_verwijderen(p20["id"])

    # --- geen maaltijd ---
    dag16 = (VANDAAG + timedelta(days=16)).isoformat()
    STATE["log"].clear()
    g = await e.plan(dag16, {"geen": True, "titel": "Geen maaltijd", "lijst": True})
    check(g["geen"] and g["titel"] == "Geen maaltijd" and not g["recept_id"] and g["boodschappen"] is False and g["uid"],
          "geen maaltijd gepland en in agenda")
    check(not [l for l in STATE["log"] if l[0] == "add"], "geen maaltijd: niets op de lijst")
    g = await e.plan(dag16, {"geen": False, "recept_id": rid, "boodschappen": True}, g["id"])
    check(not g["geen"] and g["recept_id"] == rid, "geen maaltijd weer omgezet naar recept")
    await e.plan_verwijderen(g["id"])
    # geen maaltijd in een voorstel
    await e.voorstel_maken([{"datum": dag16, "aan": True}], "")
    e.voorstel_wijzig(dag16, {"geen": True})
    e.voorstel_wijzig(dag16, {"geen": False})
    check(e._gekozen(e.s["voorstel"]["dagen"][0])["titel"] == "Linzensoep", "geen maaltijd terugdraaien houdt de keuze")
    e.voorstel_wijzig(dag16, {"geen": True})
    res = await e.voorstel_goedkeuren()
    pg = next(p for p in e.s["planning"] if p["datum"] == dag16)
    check(pg["geen"] and pg["titel"] == "Geen maaltijd" and res["plan_ids"] == [], "goedgekeurd als geen maaltijd")
    await e.dag_aanpassen(dag16, "we gaan uit eten", pg["id"])
    sg = e.s["suggesties"][dag16]
    check(sg["geen"] and sg["titel"] == "Geen maaltijd" and not sg["recept_id"], "AI-suggestie: geen maaltijd")
    e.suggestie_weg(dag16)
    await e.plan_verwijderen(pg["id"])

    await e.plan_verwijderen(pid)
    await e.sync(forceer=True)
    check(all(p["id"] != pid for p in e.s["planning"]), "plan verwijderd")
    print("AI-taken:", vragen)
    await hass.async_stop(force=True)
    await runner.cleanup()
    print("\nRESULTAAT:", "alles goed" if not FOUTEN else f"{len(FOUTEN)} fout(en): {FOUTEN}")


if __name__ == "__main__":
    asyncio.run(main())
