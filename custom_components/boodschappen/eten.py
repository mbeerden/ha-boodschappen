"""Eten: recepten, weekmenu (met AI-voorstel), sync naar een agenda en beoordelingen per persoon.

Opslag in Home Assistant zelf (.storage). De agenda (bijv. een Google-agenda) is het venster:
geplande maaltijden worden er als event in gezet met een kenmerk `eten:<id>` in de beschrijving.
Verschuift of verwijdert iemand zo'n event in de agenda, dan neemt de planning dat over.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import json
import logging
import math
import re
import time
import uuid
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .aanbiedingen import html_naar_tekst, parse_json
from .api import GrocyError

if TYPE_CHECKING:
    from .coordinator import BoodschappenCoordinator

_LOGGER = logging.getLogger(__name__)

MARKER = re.compile(r"\beten:([0-9a-f]{8,32})\b")
GEEN_TITEL = "Geen maaltijd"
UIT_VRIEZER = re.compile(r"(?i)\buit\s+(de\s+)?vriezer\b")
SYNC_INTERVAL = 300  # agenda hooguit elke 5 minuten teruglezen
DAGEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]

DEFAULT_INSTELLINGEN: dict[str, Any] = {
    "agenda": None,          # calendar.* entiteit
    "tijd": "17:00",
    "duur": 60,              # minuten
    "personen": [],          # namen voor beoordelingen
    "huisregels": "",        # vrije tekst voor het AI-voorstel (wie kookt wanneer, dieetwensen, ...)
    "ai": None,              # ai_task.* entiteit (leeg = eerste)
}


# ----------------------------------------------------------------- pure helpers
def nieuw_id() -> str:
    return uuid.uuid4().hex[:12]


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def schaal(aantal: float | None, factor: float) -> float:
    """Benodigd aantal (in Grocy-eenheden) geschaald, afgerond op hele stuks (minimaal 1)."""
    if not aantal:
        return 0.0
    x = float(aantal) * factor
    return float(max(1, math.ceil(x - 0.15)))


def marker_uit(tekst: str | None) -> str | None:
    m = MARKER.search(tekst or "")
    return m.group(1) if m else None


def event_beschrijving(item: dict, recept: dict | None, link: str | None) -> str:
    regels = []
    if item.get("kookt"):
        regels.append(f"Kookt: {item['kookt']}")
    if item.get("porties"):
        p = f"Porties: {fmt(item['porties'])}"
        if item.get("vriezer"):
            p += f" ({fmt(item['vriezer'])} naar de vriezer)"
        regels.append(p)
    if item.get("notitie"):
        regels.append(item["notitie"])
    if recept and recept.get("tijd"):
        regels.append(f"Bereidingstijd: {recept['tijd']} min")
    if link:
        regels.append(f"Recept: {link}")
    regels.append("")
    regels.append(f"eten:{item['id']}")
    return "\n".join(regels)


def fmt(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f.is_integer() else str(f).replace(".", ",")


def gemiddelden(beoordelingen: list[dict]) -> dict[str, dict]:
    """Per recept: gemiddelde score, per persoon gemiddelde, aantal keer gegeten (datums)."""
    uit: dict[str, dict] = {}
    for b in beoordelingen:
        rid = b.get("recept_id")
        if not rid or b.get("score") is None:
            continue
        r = uit.setdefault(rid, {"scores": [], "personen": {}, "datums": set()})
        r["scores"].append(float(b["score"]))
        r["personen"].setdefault(b.get("persoon") or "?", []).append(float(b["score"]))
        if b.get("datum"):
            r["datums"].add(b["datum"])
    return {
        rid: {
            "gem": round(sum(r["scores"]) / len(r["scores"]), 1),
            "personen": {p: round(sum(s) / len(s), 1) for p, s in r["personen"].items()},
            "keer": len(r["datums"]),
        }
        for rid, r in uit.items()
    }


def benodigd(items: list[dict], recepten: dict[str, dict]) -> dict[int, dict]:
    """Totaal per Grocy-product over de geplande maaltijden: {product_id: {aantal, voor: [titels]}}."""
    tot: dict[int, dict] = {}
    for it in items:
        if not it.get("boodschappen", True):
            continue
        r = recepten.get(it.get("recept_id") or "")
        if not r:
            continue
        basis = float(r.get("porties") or 4)
        factor = float(it.get("porties") or basis) / basis
        for ing in r.get("ingredienten", []):
            pid = ing.get("product_id")
            if not pid or ing.get("basis"):
                continue
            n = schaal(ing.get("aantal") or 1, factor)
            t = tot.setdefault(int(pid), {"aantal": 0.0, "voor": []})
            t["aantal"] += n
            if r["naam"] not in t["voor"]:
                t["voor"].append(r["naam"])
    return tot


def recept_uit_jsonld(page: str) -> dict | None:
    """Zoek een schema.org Recipe in de JSON-LD van een webpagina."""
    for blok in re.findall(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page):
        try:
            data = json.loads(blok.strip())
        except json.JSONDecodeError:
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                t = x.get("@type")
                if t == "Recipe" or (isinstance(t, list) and "Recipe" in t):
                    return x
                stack.extend(v for k, v in x.items() if k in ("@graph", "mainEntity", "itemListElement"))
    return None


def jsonld_naar_tekst(r: dict) -> str:
    """Compacte tekst van een JSON-LD-recept (voor de AI)."""
    def stappen(x: Any) -> list[str]:
        if isinstance(x, str):
            return [s.strip() for s in re.split(r"\n+", x) if s.strip()]
        out: list[str] = []
        for s in x or []:
            if isinstance(s, str):
                out.append(s)
            elif isinstance(s, dict):
                if s.get("@type") == "HowToSection":
                    out.extend(stappen(s.get("itemListElement")))
                elif s.get("text"):
                    out.append(s["text"])
        return out

    yld = r.get("recipeYield")
    if isinstance(yld, list):
        yld = yld[0] if yld else None
    delen = [
        f"Naam: {r.get('name')}",
        f"Porties: {yld}",
        f"Tijd: {r.get('totalTime') or r.get('cookTime') or ''}",
        "Ingrediënten:",
        *[f"- {html_naar_tekst(str(i))}" for i in r.get("recipeIngredient") or []],
        "Bereiding:",
        *[f"{n}. {html_naar_tekst(s)}" for n, s in enumerate(stappen(r.get("recipeInstructions")), 1)],
    ]
    return "\n".join(delen)


def recept_prompt(bron: str, producten: list[dict]) -> str:
    return (
        "Zet dit recept om naar JSON (Nederlands). Antwoord ALLEEN met één JSON-object:\n"
        '{"naam": str, "porties": int, "tijd": int (totale minuten), "vega": bool, "tags": [str],\n'
        ' "ingredienten": [{"tekst": "200 g kipfilet", "product_id": int|null, "aantal": number, "basis": bool}],\n'
        ' "stappen": [str]}\n'
        "Regels: product_id is het id uit de productlijst hieronder dat je hiervoor in de winkel koopt "
        "(null als er niets past); aantal is hoeveel verpakkingen/stuks van dat product nodig zijn voor het "
        "aantal porties van het recept (rond naar boven af, minimaal 1); basis=true voor dingen die altijd in "
        "huis zijn (zout, peper, olie, water, gedroogde kruiden), die gaan niet op de boodschappenlijst. "
        "Stappen kort en duidelijk, één handeling per stap.\n\n"
        f"Producten (id: naam): {json.dumps({p['id']: p['name'] for p in producten}, ensure_ascii=False)}\n\n"
        f"Recept:\n{bron[:20000]}"
    )


def dagen_tekst(dagen: list[dict]) -> str:
    regels = []
    for d in dagen:
        r = f"- {d['dag'].upper()} {d['datum']}"
        if d.get("vast"):
            r += f" — vast: {d['vast']}"
        elif d.get("agenda"):
            r += f" — in de agenda staat: {d['agenda']}"
        regels.append(r)
    return "\n".join(regels)


def weekmenu_prompt(
    dagen: list[dict],
    recepten: list[dict],
    huisregels: str,
    wens: str,
    vandaag: date,
    correctie: str = "",
) -> str:
    return (
        "Je plant de avondmaaltijden van een gezin. Maak een voorstel voor de dagen hieronder.\n"
        "Antwoord ALLEEN met een JSON-lijst, precies één object per te plannen dag, in dezelfde volgorde:\n"
        '[{"datum": "YYYY-MM-DD", "dag": "maandag", "recept_id": str|null, "titel": str, "kookt": str, '
        '"porties": int, "vriezer": int, "boodschappen": bool, "geen": bool, "reden": str}]\n'
        "- Neem datum én weekdag over zoals hieronder gegeven. Let goed op de weekdag: de huisregels gaan per weekdag.\n"
        "- Kies bij voorkeur recepten uit de receptenlijst (recept_id + titel = naam). Een nieuw gerecht mag "
        "(recept_id null), maar alleen als het echt iets toevoegt.\n"
        "- porties = alle porties die gekookt worden (incl. porties voor de vriezer); vriezer = hoeveel daarvan "
        "de vriezer in gaan. boodschappen=false als er niets voor gekocht hoeft te worden (eten uit de vriezer, "
        "iemand anders kookt).\n"
        "- Weeg mee: de huisregels, de beoordelingen per persoon (scores 1-5) van wie die dag mee-eet, "
        "afwisseling (niet hetzelfde als recent gegeten), en de bereidingstijd.\n"
        "- Dagen met 'vast' zijn al gepland: neem die titel over, kies geen ander gerecht.\n"
        "- Dagen waar al iets 'in de agenda staat' hebben een globaal idee (bijv. 'Curry (voor 3x koken)' of "
        "'Soep (uit de vriezer)'): houd je daaraan. Kies er een passend recept bij als dat er is, anders neem je "
        "de titel over. Bij 'uit de vriezer', een uitje of als iemand anders kookt: geen recept nodig en "
        "boodschappen=false.\n"
        "- geen=true als er die dag geen avondmaaltijd thuis is (bijv. de wens zegt dat jullie dan niet koken "
        "of uit eten gaan); dan geen recept, titel 'Geen maaltijd', boodschappen=false.\n"
        "- kookt: de naam van wie kookt, of leeg als er niet gekookt wordt.\n"
        "- reden: één korte zin waarom.\n\n"
        f"Vandaag: {DAGEN[vandaag.weekday()]} {vandaag.isoformat()}\n"
        f"Huisregels:\n{huisregels or '-'}\n\n"
        f"Wensen voor deze week: {wens or '-'}\n\n"
        f"Te plannen dagen:\n{dagen_tekst(dagen)}\n\n"
        + (f"LET OP, correctie op je vorige antwoord: {correctie}\n\n" if correctie else "")
        + f"Recepten: {json.dumps(recepten, ensure_ascii=False)}"
    )


def dag_prompt(
    dag: dict,
    wens: str,
    week: list[dict],
    recepten: list[dict],
    huisregels: str,
    producten: list[dict],
    vandaag: date,
) -> str:
    return (
        "Je past één avondmaaltijd aan in het weekmenu van een gezin. Doe één voorstel voor deze dag.\n"
        "Antwoord ALLEEN met één JSON-object:\n"
        '{"recept_id": str|null, "titel": str, "kookt": str, "porties": int, "vriezer": int, "boodschappen": bool, "geen": bool, '
        '"reden": str, "nieuw_recept": null | {"naam": str, "porties": int, "tijd": int, "vega": bool, "tags": [str], '
        '"ingredienten": [{"tekst": str, "product_id": int|null, "aantal": number, "basis": bool}], "stappen": [str]}}\n'
        "- Volg de wens van de gebruiker; die gaat voor.\n"
        "- Past een recept uit de receptenlijst bij de wens, kies dat (recept_id, titel = naam, nieuw_recept null).\n"
        "- Past er geen, schrijf dan een nieuw, goed uitvoerbaar recept (recept_id null, nieuw_recept ingevuld, "
        "titel = naam). Ingrediënten: tekst altijd mét hoeveelheid (bijv. '2 uien', '400 g aardappelen', "
        "'1 blik tomatenblokjes'); product_id = id uit de productlijst dat je ervoor koopt (null als er niets "
        "past); aantal = verpakkingen/stuks voor de porties van het recept; basis=true voor wat altijd in huis is "
        "(zout, peper, olie, water, gedroogde kruiden). Stappen kort, één handeling per stap.\n"
        "- Houd rekening met de huisregels (wie kookt en wie eet mee op deze weekdag, dieetwensen) en kies niet "
        "iets wat al elders in deze week staat.\n"
        "- porties = alle porties die gekookt worden (incl. vriezer); vriezer = hoeveel daarvan de vriezer in gaan.\n"
        "- Zegt de wens dat er niet gekookt wordt of dat jullie uit eten gaan: geen=true, titel 'Geen maaltijd', "
        "geen recept, boodschappen=false.\n"
        "- reden: één korte zin.\n\n"
        f"Vandaag: {DAGEN[vandaag.weekday()]} {vandaag.isoformat()}\n"
        f"Deze dag: {dag['dag'].upper()} {dag['datum']}\n"
        f"Wens: {wens or '-'}\n\n"
        f"Huisregels:\n{huisregels or '-'}\n\n"
        f"Rest van de week:\n{dagen_tekst(week) or '-'}\n\n"
        f"Recepten: {json.dumps(recepten, ensure_ascii=False)}\n\n"
        f"Producten (id: naam): {json.dumps({p['id']: p['name'] for p in producten}, ensure_ascii=False)}"
    )


def weekdag_fouten(res: list, dagen: list[dict]) -> str:
    """Controleer of de AI de juiste weekdag bij elke datum gebruikt; geeft een correctietekst of ''."""
    juist = {d["datum"]: d["dag"] for d in dagen}
    fouten = []
    for x in res:
        if not isinstance(x, dict):
            continue
        iso = str(x.get("datum") or "")[:10]
        dag = str(x.get("dag") or "").strip().lower()
        if iso in juist and dag and dag != juist[iso]:
            fouten.append(f"{iso} is een {juist[iso]}, niet een {dag}")
    if {str(x.get("datum"))[:10] for x in res if isinstance(x, dict)} != set(juist):
        fouten.append("geef precies de gevraagde datums terug")
    return "; ".join(fouten)


def recept_compact(r: dict, gem: dict | None, laatst: str | None) -> dict:
    out = {"id": r["id"], "naam": r["naam"], "tijd": r.get("tijd"), "vega": r.get("vega"), "tags": r.get("tags") or []}
    if gem:
        out["score"] = gem["gem"]
        out["per_persoon"] = gem["personen"]
    if laatst:
        out["laatst_gegeten"] = laatst
    return out


def schoon_recept(r: dict, bestaand: dict | None = None) -> dict:
    """Valideer/normaliseer een recept (van AI, import of editor)."""
    def num(v: Any, d: float | None = None) -> float | None:
        try:
            return float(str(v).replace(",", ".")) if v not in (None, "") else d
        except ValueError:
            return d

    ingr = []
    for i in r.get("ingredienten") or []:
        if isinstance(i, str):
            i = {"tekst": i}
        tekst = str(i.get("tekst") or "").strip()
        if not tekst:
            continue
        pid = i.get("product_id")
        try:
            pid = int(pid) if pid not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            pid = None
        ingr.append({"tekst": tekst, "product_id": pid, "aantal": num(i.get("aantal"), 1) or 1, "basis": bool(i.get("basis"))})
    stappen = [str(s).strip() for s in (r.get("stappen") or []) if str(s).strip()]
    uit = dict(bestaand or {})
    uit.update({
        "id": (bestaand or {}).get("id") or r.get("id") or nieuw_id(),
        "naam": str(r.get("naam") or (bestaand or {}).get("naam") or "Naamloos").strip(),
        "porties": int(num(r.get("porties"), 4) or 4),
        "tijd": int(num(r.get("tijd"), 0) or 0) or None,
        "vega": bool(r.get("vega")),
        "vega_gezet": bool(r.get("vega_gezet") or (bestaand or {}).get("vega_gezet")),
        "tags": [str(t).strip() for t in (r.get("tags") or []) if str(t).strip()][:8],
        "ingredienten": ingr,
        "stappen": stappen,
        "bron": r.get("bron") or (bestaand or {}).get("bron"),
        "mealie_slug": r.get("mealie_slug") or (bestaand or {}).get("mealie_slug"),
        "notitie": r.get("notitie") or "",
        "bijgewerkt": datetime.now().isoformat(timespec="minutes"),
    })
    return uit


# ----------------------------------------------------------------- de module
class Eten:
    def __init__(self, coordinator: "BoodschappenCoordinator", entry_id: str) -> None:
        self.c = coordinator
        self.hass = coordinator.hass
        self._store: Store = Store(self.hass, 1, f"boodschappen.{entry_id}.eten")
        self.s: dict[str, Any] = {}
        self._sync_ts = 0.0
        self.extern: list[dict] = []  # agenda-events zonder kenmerk (bijv. handmatig ingevoerd)
        self.sync_fout: str | None = None

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.s = {
            "recepten": data.get("recepten", {}),
            "planning": data.get("planning", []),
            "voorstel": data.get("voorstel"),
            "suggesties": data.get("suggesties", {}),
            "beoordelingen": data.get("beoordelingen", []),
            "instellingen": {**DEFAULT_INSTELLINGEN, **(data.get("instellingen") or {})},
        }

    def save(self) -> None:
        cutoff = (date.today() - timedelta(days=120)).isoformat()
        self.s["planning"] = [p for p in self.s["planning"] if p["datum"] >= cutoff]
        gisteren = (date.today() - timedelta(days=1)).isoformat()
        self.s["suggesties"] = {k: v for k, v in self.s.get("suggesties", {}).items() if k >= gisteren}
        self._store.async_delay_save(lambda: self.s, 1)

    @property
    def inst(self) -> dict:
        return self.s["instellingen"]

    # --- payload voor het paneel -----------------------------------------
    def payload(self) -> dict:
        gem = gemiddelden(self.s["beoordelingen"])
        laatst: dict[str, str] = {}
        for p in self.s["planning"]:
            if p.get("recept_id") and p["datum"] <= date.today().isoformat():
                laatst[p["recept_id"]] = max(laatst.get(p["recept_id"], ""), p["datum"])
        recepten = []
        for r in sorted(self.s["recepten"].values(), key=lambda x: x["naam"].lower()):
            recepten.append({**r, "score": gem.get(r["id"]), "laatst": laatst.get(r["id"])})
        return {
            "recepten": recepten,
            "planning": sorted(self.s["planning"], key=lambda p: p["datum"]),
            "voorstel": self.s["voorstel"],
            "suggesties": self.s["suggesties"],
            "beoordelingen": self.s["beoordelingen"][-400:],
            "instellingen": self.inst,
            "extern": self.extern,
            "sync_fout": self.sync_fout,
            "agendas": sorted(self.hass.states.async_entity_ids("calendar")),
            "ai_taken": sorted(self.hass.states.async_entity_ids("ai_task")),
        }

    # --- recepten ----------------------------------------------------------
    def recept_opslaan(self, r: dict) -> str:
        bestaand = self.s["recepten"].get(r.get("id") or "")
        nieuw = schoon_recept(r, bestaand)
        self.s["recepten"][nieuw["id"]] = nieuw
        self.save()
        return nieuw["id"]

    def recept_verwijderen(self, rid: str) -> None:
        self.s["recepten"].pop(rid, None)
        self.save()

    async def recept_importeren(self, url: str | None, tekst: str | None) -> str:
        """Recept van een URL (JSON-LD of paginatekst) of uit geplakte tekst, via de AI-taak."""
        bron = (tekst or "").strip()
        if url:
            session = async_get_clientsession(self.hass)
            async with session.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (HomeAssistant)"}) as resp:
                if resp.status >= 400:
                    raise ValueError(f"Pagina niet bereikbaar (HTTP {resp.status})")
                page = await resp.text()
            ld = recept_uit_jsonld(page)
            bron = jsonld_naar_tekst(ld) if ld else html_naar_tekst(page)[:20000]
        if not bron:
            raise ValueError("Geef een link of plak een recept")
        res = await self._vraag_ai("Eten: recept omzetten", recept_prompt(bron, self._producten()))
        if not isinstance(res, dict) or not res.get("naam"):
            raise ValueError("De AI kon er geen recept van maken")
        if url:
            res["bron"] = url
        return self.recept_opslaan(res)

    async def recept_koppelen(self, rid: str) -> None:
        """Laat de AI de ingrediënten (opnieuw) aan Grocy-producten koppelen."""
        r = self.s["recepten"].get(rid)
        if not r:
            raise ValueError("Recept niet gevonden")
        bron = "\n".join([f"Naam: {r['naam']}", f"Porties: {r.get('porties')}", "Ingrediënten:",
                          *[f"- {i['tekst']}" for i in r["ingredienten"]], "Bereiding:",
                          *[f"{n}. {s}" for n, s in enumerate(r["stappen"], 1)]])
        res = await self._vraag_ai("Eten: ingrediënten koppelen", recept_prompt(bron, self._producten()))
        if not isinstance(res, dict):
            raise ValueError("De AI gaf geen bruikbaar antwoord")
        nieuw = {**r, "ingredienten": res.get("ingredienten") or r["ingredienten"]}
        # alleen aanvullen wat nog ontbreekt; de rest van het recept blijft zoals het is
        if "vega" in res and not r.get("vega_gezet"):
            nieuw["vega"] = bool(res["vega"])
        if not r.get("tijd") and res.get("tijd"):
            nieuw["tijd"] = res["tijd"]
        if not r.get("tags") and res.get("tags"):
            nieuw["tags"] = res["tags"]
        self.recept_opslaan(nieuw)

    def _producten(self) -> list[dict]:
        d = self.c.data
        return [{"id": pid, "name": p["name"]} for pid, p in d.products.items() if str(p.get("active", 1)) != "0"]

    # --- weekmenu ---------------------------------------------------------
    async def voorstel_maken(self, start: str, dagen: int, wens: str) -> None:
        begin = date.fromisoformat(start)
        await self.sync(forceer=True)
        gepland = {p["datum"]: p for p in self.s["planning"]}
        extern = {}
        for e in self.extern:
            extern.setdefault(e["datum"], e["titel"])
        te_plannen = []
        for n in range(dagen):
            d = begin + timedelta(days=n)
            iso = d.isoformat()
            dag = {"datum": iso, "dag": DAGEN[d.weekday()]}
            if iso in gepland:
                dag["vast"] = gepland[iso]["titel"]
            elif iso in extern:
                dag["agenda"] = extern[iso]
            te_plannen.append(dag)
        gem = gemiddelden(self.s["beoordelingen"])
        laatst: dict[str, str] = {}
        for p in self.s["planning"]:
            if p.get("recept_id"):
                laatst[p["recept_id"]] = max(laatst.get(p["recept_id"], ""), p["datum"])
        recepten = [recept_compact(r, gem.get(r["id"]), laatst.get(r["id"])) for r in self.s["recepten"].values()]
        correctie = ""
        for _poging in range(2):
            res = await self._vraag_ai(
                "Eten: weekmenu voorstellen",
                weekmenu_prompt(te_plannen, recepten, self.inst.get("huisregels", ""), wens, date.today(), correctie),
            )
            if not isinstance(res, list):
                raise ValueError("De AI gaf geen bruikbaar weekmenu terug")
            correctie = weekdag_fouten(res, te_plannen)
            if not correctie:
                break
            _LOGGER.info("Weekmenu: weekdagen klopten niet (%s), opnieuw", correctie)
        vast = {d["datum"]: d.get("vast") for d in te_plannen}
        vervangt: dict[str, list[str]] = {}
        for e in self.extern:
            if e.get("uid"):
                vervangt.setdefault(e["datum"], []).append(e["uid"])
        dagen_uit = []
        for x in res:
            iso = str(x.get("datum") or "")[:10]
            if iso not in vast:
                continue
            rid = x.get("recept_id") if x.get("recept_id") in self.s["recepten"] else None
            idee = extern.get(iso) or ""
            if UIT_VRIEZER.search(idee) or UIT_VRIEZER.search(str(x.get("titel") or "")):
                # eten uit de vriezer: titel uit de agenda houden, niets te koken of te kopen
                x = {**x, "titel": idee or x.get("titel"), "kookt": "", "porties": None, "vriezer": 0, "boodschappen": False}
                rid = None
            geen = bool(x.get("geen")) and not vast[iso]
            if geen:
                x = {**x, "titel": GEEN_TITEL, "kookt": "", "porties": None, "vriezer": 0, "boodschappen": False}
                rid = None
            titel = self.s["recepten"][rid]["naam"] if rid else str(x.get("titel") or "").strip()
            dagen_uit.append({
                "geen": geen,
                "datum": iso, "recept_id": rid, "titel": titel or "?",
                "kookt": "" if str(x.get("kookt") or "").strip().lower() in ("niemand", "-", "geen", "n.v.t.") else (x.get("kookt") or ""),
                "porties": x.get("porties") or None, "vriezer": x.get("vriezer") or 0,
                "boodschappen": bool(x.get("boodschappen", True)) and not vast[iso],
                "reden": x.get("reden") or "", "vast": bool(vast[iso]),
                "agenda": extern.get(iso), "vervangt": [] if vast[iso] else vervangt.get(iso, []),
            })
        dagen_uit.sort(key=lambda x: x["datum"])
        self.s["voorstel"] = {"start": start, "wens": wens, "dagen": dagen_uit, "gemaakt": datetime.now().isoformat(timespec="minutes")}
        self.save()

    def voorstel_wijzig(self, datum: str, velden: dict) -> None:
        v = self.s.get("voorstel")
        if not v:
            raise ValueError("Er is geen voorstel")
        for d in v["dagen"]:
            if d["datum"] == datum:
                if isinstance(velden.get("concept"), dict):
                    # (bewerkt) nieuw recept bij deze dag; komt pas bij goedkeuren in de bibliotheek
                    d["concept"] = schoon_recept(velden["concept"], {"id": velden["concept"].get("id") or nieuw_id()})
                if "recept_id" in velden:
                    rid = velden["recept_id"] or None
                    concept = d.get("concept")
                    if concept and rid == concept["id"]:
                        d["recept_id"] = rid
                        d["titel"] = concept["naam"]
                    else:
                        d["recept_id"] = rid if rid in self.s["recepten"] else None
                        if d["recept_id"]:
                            d["titel"] = self.s["recepten"][rid]["naam"]
                if "geen" in velden and bool(velden["geen"]) != bool(d.get("geen")):
                    if velden["geen"]:
                        d["vorige"] = {k: d.get(k) for k in ("recept_id", "titel", "boodschappen")}
                        d.update({"geen": True, "recept_id": None, "titel": GEEN_TITEL, "boodschappen": False})
                    else:
                        d.update({"geen": False, **(d.pop("vorige", None) or {"titel": "?", "boodschappen": True})})
                for k in ("titel", "kookt", "porties", "vriezer", "boodschappen", "overslaan", "reden", "wens"):
                    if k in velden and not (k == "titel" and d.get("recept_id") and "recept_id" not in velden):
                        d[k] = velden[k]
        self.save()

    async def dag_aanpassen(self, datum: str, wens: str, plan_id: str | None = None) -> None:
        """Eén dag opnieuw laten invullen door de AI, met een wens. In een voorstel direct vervangen;
        bij een geplande dag eerst als suggestie tonen (toepassen of weggooien)."""
        d = date.fromisoformat(datum)
        ws = week_start(d)
        v = self.s.get("voorstel")
        in_voorstel = bool(v) and not plan_id and any(x["datum"] == datum for x in v["dagen"])
        # rest van de week: voorstel, planning en losse agenda-events
        week: dict[str, dict] = {}
        for p in self.s["planning"]:
            if ws.isoformat() <= p["datum"] < (ws + timedelta(days=7)).isoformat():
                week[p["datum"]] = {"datum": p["datum"], "dag": DAGEN[date.fromisoformat(p["datum"]).weekday()], "vast": p.get("titel")}
        for e in self.extern:
            if ws.isoformat() <= e["datum"] < (ws + timedelta(days=7)).isoformat() and e["datum"] not in week:
                week[e["datum"]] = {"datum": e["datum"], "dag": DAGEN[date.fromisoformat(e["datum"]).weekday()], "agenda": e["titel"]}
        if v:
            for x in v["dagen"]:
                if ws.isoformat() <= x["datum"] < (ws + timedelta(days=7)).isoformat():
                    week[x["datum"]] = {"datum": x["datum"], "dag": DAGEN[date.fromisoformat(x["datum"]).weekday()], "vast": x["titel"]}
        week.pop(datum, None)
        gem = gemiddelden(self.s["beoordelingen"])
        laatst: dict[str, str] = {}
        for p in self.s["planning"]:
            if p.get("recept_id"):
                laatst[p["recept_id"]] = max(laatst.get(p["recept_id"], ""), p["datum"])
        recepten = [recept_compact(r, gem.get(r["id"]), laatst.get(r["id"])) for r in self.s["recepten"].values()]
        res = await self._vraag_ai("Eten: dag aanpassen", dag_prompt(
            {"datum": datum, "dag": DAGEN[d.weekday()]}, wens, sorted(week.values(), key=lambda x: x["datum"]),
            recepten, self.inst.get("huisregels", ""), self._producten(), date.today()))
        if not isinstance(res, dict):
            raise ValueError("De AI gaf geen bruikbaar voorstel terug")
        concept = None
        rid = res.get("recept_id") if res.get("recept_id") in self.s["recepten"] else None
        if not rid and isinstance(res.get("nieuw_recept"), dict) and res["nieuw_recept"].get("naam"):
            concept = schoon_recept({**res["nieuw_recept"], "id": None})
            concept["tags"] = list(dict.fromkeys([*concept["tags"], "nieuw"]))[:8]
            rid = concept["id"]
        titel = concept["naam"] if concept else (self.s["recepten"][rid]["naam"] if rid else str(res.get("titel") or "?"))
        kookt = str(res.get("kookt") or "")
        if kookt.strip().lower() in ("niemand", "-", "geen", "n.v.t."):
            kookt = ""
        if res.get("geen"):
            concept, rid, titel, kookt = None, None, GEEN_TITEL, ""
            res = {**res, "porties": None, "vriezer": 0, "boodschappen": False}
        nieuw = {
            "geen": bool(res.get("geen")),
            "datum": datum, "recept_id": rid, "titel": titel, "kookt": kookt,
            "porties": res.get("porties") or None, "vriezer": res.get("vriezer") or 0,
            "boodschappen": bool(res.get("boodschappen", True)), "reden": res.get("reden") or "",
            "wens": wens, "concept": concept,
        }
        if in_voorstel:
            for i, x in enumerate(v["dagen"]):
                if x["datum"] == datum:
                    v["dagen"][i] = {**x, **nieuw, "vast": False, "overslaan": False}
        else:
            nieuw["plan_id"] = plan_id
            self.s["suggesties"][datum] = nieuw
        self.save()

    async def suggestie_toepassen(self, datum: str) -> dict:
        sug = self.s["suggesties"].pop(datum, None)
        if not sug:
            raise ValueError("Geen suggestie voor deze dag")
        if sug.get("concept"):
            self.s["recepten"][sug["concept"]["id"]] = sug["concept"]
        item = await self.plan(datum, {k: sug.get(k) for k in ("recept_id", "titel", "kookt", "porties", "vriezer", "boodschappen", "geen")},
                               sug.get("plan_id"), sync=False)
        item["op_lijst"] = False
        self.save()
        toegevoegd = await self.ingredienten_op_lijst([item["id"]]) if item.get("boodschappen", True) else 0
        return {"op_lijst": toegevoegd}

    def suggestie_weg(self, datum: str) -> None:
        self.s["suggesties"].pop(datum, None)
        self.save()

    def voorstel_weg(self) -> None:
        self.s["voorstel"] = None
        self.save()

    async def voorstel_goedkeuren(self) -> dict:
        v = self.s.get("voorstel")
        if not v:
            raise ValueError("Er is geen voorstel")
        nieuw: list[dict] = []
        for d in v["dagen"]:
            if d.get("overslaan") or d.get("vast"):
                continue
            c = d.get("concept")
            if c and d.get("recept_id") == c["id"]:
                self.s["recepten"][c["id"]] = c  # nieuw recept pas nu in de bibliotheek
            nieuw.append(await self.plan(d["datum"], {k: d.get(k) for k in ("recept_id", "titel", "kookt", "porties", "vriezer", "boodschappen", "geen")}, sync=False))
            await self._extern_weg(d.get("vervangt") or [])
        self.s["voorstel"] = None
        self.save()
        toegevoegd = await self.ingredienten_op_lijst([p["id"] for p in nieuw])
        return {"gepland": len(nieuw), "op_lijst": toegevoegd}

    async def plan(self, datum: str, velden: dict, plan_id: str | None = None, sync: bool = True) -> dict:
        """Eén dag plannen of wijzigen (en de agenda bijwerken)."""
        item = next((p for p in self.s["planning"] if p["id"] == plan_id), None) if plan_id else None
        if item is None:
            # één maaltijd per dag: bestaande van deze dag vervangen
            item = next((p for p in self.s["planning"] if p["datum"] == datum), None)
        if item is None:
            item = {"id": nieuw_id(), "datum": datum, "uid": None, "op_lijst": False}
            self.s["planning"].append(item)
        oud_recept = item.get("recept_id")
        item["datum"] = datum
        for k in ("recept_id", "titel", "kookt", "porties", "vriezer", "boodschappen", "notitie", "geen"):
            if k in velden:
                item[k] = velden[k]
        if item.get("geen"):
            # we koken niet: alleen een markering in de agenda, niets te kopen
            item.update({"recept_id": None, "titel": GEEN_TITEL, "kookt": "", "porties": None,
                         "vriezer": 0, "boodschappen": False})
        if item.get("recept_id") and item["recept_id"] in self.s["recepten"]:
            item["titel"] = self.s["recepten"][item["recept_id"]]["naam"]
        else:
            item["recept_id"] = None
        if item.get("recept_id") != oud_recept:
            item["op_lijst"] = False
        item.setdefault("boodschappen", True)
        item.pop("historie", None)
        if not item.get("uid"):
            # losse agenda-events op die dag (eerder handmatig gezet) vervangen, geen dubbele maaltijd
            await self._extern_weg([e["uid"] for e in self.extern if e["datum"] == datum and e.get("uid")])
        await self._event_schrijven(item)
        self.save()
        if sync and velden.get("lijst"):
            await self.ingredienten_op_lijst([item["id"]])
        return item

    async def _extern_weg(self, uids: list[str]) -> None:
        ent = self._agenda()
        for uid in uids:
            if ent is not None and uid:
                try:
                    await ent.async_delete_event(uid)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Los event verwijderen: %s", err)
        self.extern = [e for e in self.extern if e.get("uid") not in uids]

    async def overnemen(self, datum: str, uid: str | None, recept_id: str | None, titel: str | None,
                        velden: dict | None = None) -> dict:
        """Een los agenda-event (zonder kenmerk) vervangen door een geplande maaltijd."""
        await self._extern_weg([uid] if uid else [])
        return await self.plan(datum, {**(velden or {}), "recept_id": recept_id, "titel": titel or "?"})

    def historie(self, items: list[dict]) -> int:
        """Eerder gegeten maaltijden vastleggen (zonder de agenda aan te raken), voor 'laatst gegeten'."""
        bekend = {p["datum"] for p in self.s["planning"]}
        n = 0
        for x in items:
            if x["datum"] in bekend or x.get("recept_id") not in self.s["recepten"]:
                continue
            self.s["planning"].append({"id": nieuw_id(), "datum": x["datum"], "recept_id": x["recept_id"],
                                       "titel": self.s["recepten"][x["recept_id"]]["naam"], "uid": None,
                                       "op_lijst": True, "boodschappen": False, "historie": True})
            bekend.add(x["datum"])
            n += 1
        self.save()
        return n

    async def plan_verwijderen(self, plan_id: str) -> None:
        item = next((p for p in self.s["planning"] if p["id"] == plan_id), None)
        if not item:
            return
        await self._event_verwijderen(item)
        self.s["planning"].remove(item)
        self.save()

    async def ingredienten_op_lijst(self, plan_ids: list[str]) -> int:
        items = [p for p in self.s["planning"] if p["id"] in plan_ids and not p.get("op_lijst")
                 and p["datum"] >= date.today().isoformat()]
        nodig = benodigd(items, self.s["recepten"])
        if not nodig:
            for p in items:
                p["op_lijst"] = True
            self.save()
            return 0
        voorraad: dict[int, float] = {}
        try:
            for s in await self.c.api.get("/stock") or []:
                voorraad[int(s["product_id"])] = float(s.get("amount") or 0)
        except GrocyError as err:
            _LOGGER.warning("Voorraad ophalen mislukt, alles gaat op de lijst: %s", err)
        await self.c.async_refresh()
        d = self.c.data
        toegevoegd = 0
        for pid, n in nodig.items():
            if pid not in d.products:
                continue
            lid = d.list_for_product(pid)
            al = d.open_item_for_product(lid, pid)
            tekort = n["aantal"] - voorraad.get(pid, 0) - (al.amount if al else 0)
            if tekort <= 0:
                continue
            await self.c.add_product(lid, pid, float(math.ceil(tekort)), f"recept: {', '.join(n['voor'])}"[:120])
            toegevoegd += 1
        for p in items:
            p["op_lijst"] = True
        self.save()
        return toegevoegd

    # --- beoordelingen -----------------------------------------------------
    def beoordeel(self, plan_id: str, persoon: str, score: int | None, opmerking: str = "") -> None:
        item = next((p for p in self.s["planning"] if p["id"] == plan_id), None)
        if not item:
            raise ValueError("Maaltijd niet gevonden")
        b = self.s["beoordelingen"]
        b[:] = [x for x in b if not (x["plan_id"] == plan_id and x["persoon"] == persoon)]
        if score is not None:
            b.append({"plan_id": plan_id, "recept_id": item.get("recept_id"), "titel": item.get("titel"),
                      "datum": item["datum"], "persoon": persoon, "score": int(score),
                      "opmerking": opmerking.strip(), "ts": int(time.time())})
        self.save()

    def instellingen_opslaan(self, inst: dict) -> None:
        self.inst["agenda_gekozen"] = True
        for k in DEFAULT_INSTELLINGEN:
            if k in inst:
                self.inst[k] = inst[k]
        if isinstance(self.inst.get("personen"), str):
            self.inst["personen"] = [p.strip() for p in self.inst["personen"].split(",") if p.strip()]
        self._sync_ts = 0
        self.save()

    # --- agenda ------------------------------------------------------------
    def _agenda(self):
        if not self.inst.get("agenda") and not self.inst.get("agenda_gekozen"):
            # eerste keer: een agenda met 'eet' in de naam voorstellen (pas na het opstarten bekend)
            cal = sorted(e for e in self.hass.states.async_entity_ids("calendar") if "eet" in e)
            if cal:
                self.inst["agenda"] = cal[0]
                self.inst["agenda_gekozen"] = True
                self.save()
        ent_id = self.inst.get("agenda")
        if not ent_id:
            return None
        comp = self.hass.data.get("calendar")
        return comp.get_entity(ent_id) if comp else None

    def _tijden(self, datum: str) -> tuple[datetime, datetime]:
        uur, minuut = (int(x) for x in str(self.inst.get("tijd") or "17:00").split(":")[:2])
        start = datetime.combine(date.fromisoformat(datum), datetime.min.time()).replace(
            hour=uur, minute=minuut, tzinfo=dt_util.get_default_time_zone())
        return start, start + timedelta(minutes=int(self.inst.get("duur") or 60))

    def _link(self, item: dict) -> str | None:
        if not item.get("recept_id"):
            return None
        try:
            from homeassistant.helpers.network import get_url

            return f"{get_url(self.hass, prefer_external=True)}/eten?recept={item['recept_id']}"
        except Exception:  # noqa: BLE001 - geen URL bekend
            return None

    async def _event_schrijven(self, item: dict) -> None:
        ent = self._agenda()
        if ent is None:
            return
        await self._event_verwijderen(item)
        start, eind = self._tijden(item["datum"])
        beschrijving = event_beschrijving(item, self.s["recepten"].get(item.get("recept_id") or ""), self._link(item))
        try:
            await ent.async_create_event(dtstart=start, dtend=eind, summary=item["titel"], description=beschrijving)
        except Exception as err:  # noqa: BLE001
            self.sync_fout = f"Agenda bijwerken mislukt: {err}"
            _LOGGER.warning("Agenda-event maken mislukt: %s", err)
            return
        self.sync_fout = None
        # uid opzoeken via het kenmerk
        try:
            events = await ent.async_get_events(self.hass, start - timedelta(hours=1), eind + timedelta(hours=1))
            for e in events:
                if marker_uit(e.description) == item["id"]:
                    item["uid"] = e.uid
                    break
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Event teruglezen mislukt: %s", err)

    async def _event_verwijderen(self, item: dict) -> None:
        ent = self._agenda()
        if ent is None or not item.get("uid"):
            return
        try:
            await ent.async_delete_event(item["uid"])
        except Exception as err:  # noqa: BLE001 - al weg in de agenda
            _LOGGER.debug("Event verwijderen: %s", err)
        item["uid"] = None

    async def sync(self, forceer: bool = False) -> None:
        """Agenda teruglezen: verschoven/verwijderde maaltijden overnemen, overige events tonen."""
        if not forceer and time.time() - self._sync_ts < SYNC_INTERVAL:
            return
        self._sync_ts = time.time()
        ent = self._agenda()
        if ent is None:
            self.extern = []
            return
        van = week_start(date.today()) - timedelta(days=14)
        tot = date.today() + timedelta(days=35)
        tz = dt_util.get_default_time_zone()
        try:
            events = await ent.async_get_events(
                self.hass, datetime.combine(van, datetime.min.time(), tz), datetime.combine(tot, datetime.min.time(), tz))
        except Exception as err:  # noqa: BLE001
            self.sync_fout = f"Agenda lezen mislukt: {err}"
            return
        self.sync_fout = None
        per_id: dict[str, Any] = {}
        extern = []
        for e in events:
            pid = marker_uit(e.description)
            if not isinstance(e.start, datetime):
                continue  # hele-dag-events (bijv. afvalkalender) zijn geen maaltijden
            start = dt_util.as_local(e.start).date()
            if pid:
                per_id[pid] = (e, start.isoformat())
            else:
                extern.append({"datum": start.isoformat(), "titel": e.summary, "uid": e.uid,
                               "beschrijving": (e.description or "")[:300]})
        self.extern = sorted(extern, key=lambda x: x["datum"])
        gewijzigd = False
        for item in list(self.s["planning"]):
            if item["datum"] < van.isoformat() or item["datum"] >= tot.isoformat():
                continue
            if item["id"] in per_id:
                e, d = per_id[item["id"]]
                item["uid"] = e.uid
                if d != item["datum"]:
                    andere = next((p for p in self.s["planning"] if p["datum"] == d and p["id"] != item["id"]), None)
                    if andere is None:
                        item["datum"] = d
                        gewijzigd = True
                if e.summary and e.summary != item.get("titel") and not item.get("recept_id"):
                    item["titel"] = e.summary
                    gewijzigd = True
            elif item.get("uid"):
                # stond in de agenda maar is daar verwijderd
                self.s["planning"].remove(item)
                gewijzigd = True
        if gewijzigd:
            self.save()

    # --- AI ---------------------------------------------------------------
    async def _vraag_ai(self, taak: str, instructie: str) -> Any:
        ent = self.inst.get("ai") or next(iter(sorted(self.hass.states.async_entity_ids("ai_task"))), None)
        if not ent:
            raise ValueError("Geen AI-taak (ai_task) gevonden")
        for poging in range(3):
            try:
                res = await self.hass.services.async_call(
                    "ai_task", "generate_data", {"task_name": taak, "instructions": instructie, "entity_id": ent},
                    blocking=True, return_response=True,
                )
                return parse_json((res or {}).get("data"))
            except Exception as err:  # noqa: BLE001 - tijdelijke drukte bij de AI-dienst: kort wachten en opnieuw
                tijdelijk = any(x in str(err) for x in ("503", "UNAVAILABLE", "high demand", "429", "overloaded"))
                if not tijdelijk or poging == 2:
                    raise
                _LOGGER.info("AI-taak tijdelijk niet beschikbaar, opnieuw over %s s", 5 * (poging + 1))
                await asyncio.sleep(5 * (poging + 1))
        return None
