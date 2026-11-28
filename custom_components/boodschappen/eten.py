"""Eten: recepten, weekmenu (met AI-voorstel), sync naar een agenda en beoordelingen per persoon.

Opslag in Home Assistant zelf (.storage). De agenda (bijv. een Google-agenda) is het venster:
geplande maaltijden worden er als event in gezet met een kenmerk `eten:<id>` in de beschrijving.
Verschuift of verwijdert iemand zo'n event in de agenda, dan neemt de planning dat over.
"""
from __future__ import annotations

import asyncio
import base64
import copy
from datetime import date, datetime, timedelta
import json
import logging
import math
import re
import time
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .aanbiedingen import html_naar_tekst, parse_json
from .api import GrocyError
from .bronnen import (
    SITEMAP_PADEN,
    bron_advies,
    foto_prompt,
    is_sitemap_index,
    kandidaten_voor_ai,
    kies_prompt,
    kies_toepassen,
    receptlink_mogelijk,
    sitemap_locs,
    sitemap_volgorde,
    sitemaps_uit_robots,
    site_naam,
    titel_uit_url,
    url_sleutel,
    zoek_links,
)
from .recept import (  # noqa: F401 - recept_uit_jsonld wordt ook van hieruit gebruikt
    DEFAULT_BRONNEN,
    aanpas_prompt,
    aanpassing_samenvoegen,
    bron_van,
    herkomst,
    is_vreemde_taal,
    kwaliteit,
    overneem_prompt,
    recept_uit_jsonld,
    recept_van_jsonld,
    recept_verschil,
    schoon_bronnen,
    verrijk_prompt,
    verrijk_toepassen,
)
from .dagmenu import (
    BIBLIOTHEEK,
    DEFAULT_FILTERS,
    MAX_LINKS_PER_DAG,
    MAX_RECEPTEN,
    opties_prompt,
    opties_toepassen,
    recepten_selectie,
    reserveringen,
    schoon_filters,
    verdeel,
    zoekvraag,
)
from .gemini import GeminiNietBeschikbaar, sleutel_en_model, stream as gemini_stream
from .menu import (
    FEEDBACK,
    ROLLEN,
    VASTE_TITEL,
    controleer_antwoord,
    context_tekst,
    dag_info,
    default_menu,
    feedback_key,
    is_afgewezen,
    labels_voor,
    porties_voor,
    schoon_menu,
    voorraad_overzicht,
    voorstel_prompt,
)

if TYPE_CHECKING:
    from .coordinator import BoodschappenCoordinator

_LOGGER = logging.getLogger(__name__)

MARKER = re.compile(r"\beten:([0-9a-f]{8,32})\b")
GEEN_TITEL = "Geen maaltijd"
UIT_VRIEZER = re.compile(r"(?i)\buit\s+(de\s+)?vriezer\b")
SITEMAP_BEWAAR = 86400  # sitemaps van bronnen een dag onthouden
MAX_SITEMAPS = 15       # hooguit zoveel sitemap-bestanden per bron lezen
MAX_LINKS = 40000
MAX_FOTO = 8 * 1024 * 1024
FOTO_MAP = "eten_fotos"
SYNC_INTERVAL = 60  # agenda hooguit elke minuut teruglezen (leest de lokale kopie van de agenda)
VERWIJDER_NA = 600  # een maaltijd die 10 minuten uit de agenda weg is, is echt verwijderd
DAGEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
HEADERS = {  # sommige receptensites weigeren een onbekende client
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

DEFAULT_INSTELLINGEN: dict[str, Any] = {
    "agenda": None,          # calendar.* entiteit
    "tijd": "17:00",
    "duur": 60,              # minuten
    "personen": [],          # namen voor beoordelingen
    "huisregels": "",        # vrije tekst voor het AI-voorstel (wie kookt wanneer, dieetwensen, ...)
    "ai": None,              # ai_task.* entiteit (leeg = eerste)
    "bronnen": copy.deepcopy(DEFAULT_BRONNEN),  # de koks/sites waar recepten vandaan mogen komen (zie recept.py)
    "dagfilters": copy.deepcopy(DEFAULT_FILTERS),  # laatst gebruikte vinkjes en bronnen bij dag voor dag plannen
    **default_menu(),        # profielen, weekdagen, stijl, afwisseling (zie menu.py)
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
        + (f"Nu in de agenda voor deze dag: {dag['agenda']} (de wens gaat voor)\n" if dag.get("agenda") else "")
        + 
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
        "eigen": bool(r["eigen"] if "eigen" in r else (bestaand or {}).get("eigen")),
        "vertaald_uit": r.get("vertaald_uit") or (bestaand or {}).get("vertaald_uit"),
        "notitie": r.get("notitie") or "",
        "bijgewerkt": datetime.now().isoformat(timespec="minutes"),
    })
    for k in ("bron_naam", "variant_van", "variant_van_naam", "aanpassing", "vorige"):
        waarde = r[k] if k in r else (bestaand or {}).get(k)
        if waarde:
            uit[k] = waarde
        else:
            uit.pop(k, None)
    return uit


# ----------------------------------------------------------------- de module
class Eten:
    def __init__(self, coordinator: "BoodschappenCoordinator", entry_id: str) -> None:
        self.c = coordinator
        self.hass = coordinator.hass
        self._store: Store = Store(self.hass, 1, f"boodschappen.{entry_id}.eten")
        self.s: dict[str, Any] = {}
        self._sync_ts = 0.0
        self._sitemaps: dict[str, dict] = {}
        self._zoek_nota = ""
        self._ooit_gelezen = False
        self.extern: list[dict] = []  # agenda-events zonder kenmerk (bijv. handmatig ingevoerd)
        self.sync_fout: str | None = None

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.s = {
            "recepten": data.get("recepten", {}),
            "planning": data.get("planning", []),
            "voorstel": data.get("voorstel"),
            "dagopties": data.get("dagopties", {}),
            "suggesties": data.get("suggesties", {}),
            "beoordelingen": data.get("beoordelingen", []),
            "feedback": data.get("feedback", {}),
            "aanpassingen": data.get("aanpassingen", {}),
            "instellingen": {**copy.deepcopy(DEFAULT_INSTELLINGEN), **(data.get("instellingen") or {})},
        }
        self.s["instellingen"].update(schoon_menu(self.s["instellingen"]))
        self.s["instellingen"]["bronnen"] = schoon_bronnen(self.s["instellingen"].get("bronnen"))
        for b in self.s["instellingen"]["bronnen"]:
            if b["domein"] == "ottolenghi.co.uk" and not b["pad"]:
                b["pad"] = "/pages/recipes"  # de recepten van Ottolenghi staan onder /pages/recipes
        if self.s["voorstel"] and self.s["voorstel"].get("versie") != 2:
            self.s["voorstel"] = None  # voorstel in het oude formaat (één gerecht per dag): opnieuw maken

    def save(self) -> None:
        cutoff = (date.today() - timedelta(days=120)).isoformat()
        self.s["planning"] = [p for p in self.s["planning"] if p["datum"] >= cutoff]
        gisteren = (date.today() - timedelta(days=1)).isoformat()
        self.s["suggesties"] = {k: v for k, v in self.s.get("suggesties", {}).items() if k >= gisteren}
        self.s["dagopties"] = {k: v for k, v in self.s.get("dagopties", {}).items() if k >= gisteren}
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
            recepten.append({**r, "score": gem.get(r["id"]), "laatst": laatst.get(r["id"]),
                             "herkomst": herkomst(r, self.inst["bronnen"]), "kwaliteit": kwaliteit(r)})
        return {
            "recepten": recepten,
            "planning": sorted(self.s["planning"], key=lambda p: p["datum"]),
            "voorstel": self.s["voorstel"],
            "dagopties": self.s.get("dagopties", {}),
            "suggesties": self.s["suggesties"],
            "beoordelingen": self.s["beoordelingen"][-400:],
            "feedback": self.s["feedback"],
            "feedback_soorten": FEEDBACK,
            "aanpassingen": self.s.get("aanpassingen", {}),
            "bronnen_info": self.bronnen_info(gem),
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

    async def recept_importeren(self, url: str | None, tekst: str | None, fotos: list[str] | None = None,
                                bron_naam: str | None = None) -> str:
        """Recept van een link of uit geplakte tekst.

        Heeft de pagina een schema.org-recept, dan nemen we ingrediënten en bereiding letterlijk over en koppelt de
        AI alleen de producten (en vertaalt als het recept niet Nederlands is). Anders haalt de AI het recept uit
        de tekst, met de opdracht niets weg te laten of te verzinnen.
        """
        producten = self._producten()
        if url:
            url = url.strip()
            if not url.lower().startswith(("http://", "https://")):
                raise ValueError("Dat is geen link (begin met https://)")
            session = async_get_clientsession(self.hass)
            async with session.get(url, timeout=30, headers=HEADERS) as resp:
                if resp.status >= 400:
                    raise ValueError(f"Pagina niet bereikbaar (HTTP {resp.status})")
                page = await resp.text()
            pagina_tekst = html_naar_tekst(page)
            ld = recept_uit_jsonld(page)
            basis = recept_van_jsonld(ld, pagina_tekst) if ld else None
            if basis and basis["naam"] and basis["ingredienten"] and basis["stappen"]:
                vreemd = is_vreemde_taal(basis)
                for _poging in range(2 if vreemd else 1):
                    res = await self._vraag_ai("Eten: recept koppelen", verrijk_prompt(basis, producten, vertalen=vreemd))
                    recept = verrijk_toepassen(basis, res, vertaald=True if vreemd else None)
                    if not vreemd or recept.get("vertaald_uit"):
                        break
                if vreemd and not recept.get("vertaald_uit"):
                    recept["notitie"] = "Niet vertaald: de vertaling was onvolledig, dus dit is de tekst van de bron."
            else:
                recept = await self._vraag_ai("Eten: recept overnemen", overneem_prompt(pagina_tekst, producten))
                if not isinstance(recept, dict) or not recept.get("naam"):
                    raise ValueError("Op deze pagina staat geen volledig recept")
            recept["bron"] = url
            recept["eigen"] = False
        elif fotos:
            recept = await self._recept_uit_fotos(fotos, producten, bron_naam)
        else:
            bron = (tekst or "").strip()
            if not bron:
                raise ValueError("Geef een link, plak een recept of maak een foto")
            recept = await self._vraag_ai("Eten: recept overnemen", overneem_prompt(bron, producten))
            if not isinstance(recept, dict) or not recept.get("naam"):
                raise ValueError("De AI kon er geen recept van maken")
        if not url and bron_naam:
            if bron_naam.lower() not in {b["naam"].lower() for b in self.inst["bronnen"]}:
                raise ValueError("Onbekende bron")
            recept["bron_naam"] = next(b["naam"] for b in self.inst["bronnen"] if b["naam"].lower() == bron_naam.lower())
        recept.pop("id", None)
        return self.recept_opslaan(recept)

    async def _recept_uit_fotos(self, fotos: list[str], producten: list[dict], bron_naam: str | None) -> dict:
        """Recept uit één of meer foto's (bijv. een kookboekpagina): de AI leest ze, wij bewaren niets van de foto."""
        media = (self.hass.config.media_dirs or {}).get("local")
        if not media:
            raise ValueError("Er is geen media-map ingesteld in Home Assistant, dus foto's lezen kan niet")
        if len(fotos) > 4:
            raise ValueError("Hooguit 4 foto's per recept")
        bestanden: list[tuple[str, str, bytes]] = []  # (naam, mime, bytes)
        for f in fotos:
            m = re.fullmatch(r"data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\s]+)", str(f).strip())
            if not m:
                raise ValueError("Dit soort bestand kan ik niet lezen (gebruik een JPEG-, PNG- of WebP-foto)")
            data = base64.b64decode(m.group(2))
            if len(data) > MAX_FOTO:
                raise ValueError("Een foto is te groot (max 8 MB)")
            ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[m.group(1)]
            bestanden.append((f"{nieuw_id()}.{ext}", m.group(1), data))
        from pathlib import Path

        map_ = Path(media) / FOTO_MAP

        def schrijf() -> None:
            map_.mkdir(parents=True, exist_ok=True)
            for naam, _mime, data in bestanden:
                (map_ / naam).write_bytes(data)

        def opruimen() -> None:
            for naam, _mime, _data in bestanden:
                (map_ / naam).unlink(missing_ok=True)

        await self.hass.async_add_executor_job(schrijf)
        try:
            res = await self._vraag_ai(
                "Eten: recept uit foto", foto_prompt(producten, len(bestanden), bron_naam),
                attachments=[{"media_content_id": f"media-source://media_source/local/{FOTO_MAP}/{naam}", "media_content_type": mime}
                             for naam, mime, _d in bestanden])
        finally:
            await self.hass.async_add_executor_job(opruimen)
        if not isinstance(res, dict) or not res.get("naam"):
            raise ValueError("Ik kon op de foto geen volledig recept lezen. Probeer een scherpere foto of fotografeer de hele pagina.")
        res["bron"] = None
        res["eigen"] = False
        return res

    async def recept_koppelen(self, rid: str) -> None:
        """Laat de AI de ingrediënten (opnieuw) aan Grocy-producten koppelen; de tekst van het recept blijft staan."""
        r = self.s["recepten"].get(rid)
        if not r:
            raise ValueError("Recept niet gevonden")
        res = await self._vraag_ai("Eten: ingrediënten koppelen", verrijk_prompt(r, self._producten()))
        if not isinstance(res, dict):
            raise ValueError("De AI gaf geen bruikbaar antwoord")
        nieuw = {**r, "ingredienten": verrijk_toepassen(r, res, vertaald=False)["ingredienten"]}
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

    # --- recept aanpassen met Gemini --------------------------------------------
    async def recept_aanpassen(self, rid: str, wens: str) -> None:
        """Laat Gemini een recept aanpassen. Het resultaat is een voorstel (met verschil); pas na kiezen wordt er iets bewaard."""
        r = self.s["recepten"].get(rid)
        if not r:
            raise ValueError("Recept niet gevonden")
        wens = (wens or "").strip()
        if not wens:
            raise ValueError("Vertel Gemini wat er anders moet, bijvoorbeeld 'maak het vegetarisch'")
        context = context_tekst(self.menu, self.inst.get("huisregels", ""), self.s["feedback"], date.today())
        fb = sorted((self.s["feedback"].get(rid) or {}).get("soorten") or {})
        if fb:
            context += f"\nFeedback van het gezin op dit recept: {', '.join(fb)}."
        producten = self._producten()
        correctie, nieuw, punten = "", None, []
        for poging in range(2):
            res = await self._vraag_ai("Eten: recept aanpassen", aanpas_prompt(r, wens, producten, context, correctie))
            nieuw = aanpassing_samenvoegen(r, res)
            punten = kwaliteit(nieuw)
            if not punten or poging == 1:
                break
            correctie = "; ".join(punten) + ". Maak het recept compleet (alle ingrediënten, kruiden en stappen)."
        self.s["aanpassingen"][rid] = {
            "wens": wens, "recept": nieuw, "wijzigingen": nieuw.pop("wijzigingen", []), "verschil": recept_verschil(r, nieuw),
            "kwaliteit": punten, "gemaakt": datetime.now().isoformat(timespec="minutes"),
        }
        self.save()

    def aanpassing_weg(self, rid: str) -> None:
        self.s["aanpassingen"].pop(rid, None)
        self.save()

    def aanpassing_toepassen(self, rid: str, modus: str) -> str:
        """Voorstel bewaren: als variant (nieuw recept, origineel blijft) of het origineel vervangen (vorige versie bewaard)."""
        p = self.s["aanpassingen"].get(rid)
        r = self.s["recepten"].get(rid)
        if not p or not r:
            raise ValueError("Er is geen voorstel voor dit recept")
        inhoud = {k: p["recept"].get(k) for k in ("naam", "porties", "tijd", "vega", "tags", "ingredienten", "stappen")}
        if modus == "variant":
            naam = inhoud["naam"] or r["naam"]
            if naam.strip().lower() == r["naam"].strip().lower():
                naam += " (aangepast)"
            nieuw = {**{k: v for k, v in r.items() if k not in ("id", "vorige", "variant_van", "variant_van_naam", "aanpassing")},
                     **inhoud, "naam": naam, "id": None, "vega_gezet": True,
                     "variant_van": r["id"], "variant_van_naam": r["naam"], "aanpassing": p["wens"]}
            nieuw_id_ = schoon_recept(nieuw, None)
            self.s["recepten"][nieuw_id_["id"]] = nieuw_id_
            uit = nieuw_id_["id"]
        elif modus == "vervang":
            vorige = {k: r.get(k) for k in ("naam", "porties", "tijd", "vega", "tags", "ingredienten", "stappen")}
            samen = {**r, **inhoud, "vega_gezet": True, "vorige": vorige, "aanpassing": p["wens"]}
            self.s["recepten"][rid] = schoon_recept(samen, r)
            uit = rid
        else:
            raise ValueError("Onbekende keuze")
        self.s["aanpassingen"].pop(rid, None)
        self.save()
        return uit

    def aanpassing_ongedaan(self, rid: str) -> None:
        r = self.s["recepten"].get(rid)
        if not r or not r.get("vorige"):
            raise ValueError("Er is geen vorige versie om naar terug te gaan")
        samen = {**r, **r["vorige"], "vorige": None, "aanpassing": None}
        self.s["recepten"][rid] = schoon_recept(samen, r)
        self.save()

    # --- bronnen ------------------------------------------------------------------
    def bronnen_info(self, gem: dict | None = None) -> dict[str, dict]:
        """Per bron: aantal recepten, gemiddelde score van het gezin en feedback. {naam: {...}}"""
        gem = gem if gem is not None else gemiddelden(self.s["beoordelingen"])
        bronnen = self.inst["bronnen"]
        info = {b["naam"]: {"recepten": 0, "scores": [], "wel": 0, "niet": 0, "ai_check": 0} for b in bronnen}
        for r in self.s["recepten"].values():
            h = herkomst(r, bronnen)
            if h["soort"] != "bron" or h["naam"] not in info:
                continue
            i = info[h["naam"]]
            i["recepten"] += 1
            g = gem.get(r["id"])
            if g:
                i["scores"].append(g["gem"])
            soorten = (self.s["feedback"].get(r["id"]) or {}).get("soorten") or {}
            i["wel"] += 1 if "wel_lekker" in soorten else 0
            i["niet"] += 1 if "niet_lekker" in soorten else 0
        return {n: {"recepten": i["recepten"], "score": round(sum(i["scores"]) / len(i["scores"]), 1) if i["scores"] else None,
                    "wel": i["wel"], "niet": i["niet"]} for n, i in info.items()}

    def bronnen_opslaan(self, lijst: list[dict]) -> None:
        """De hele bronnenlijst vervangen. Een bron met 'oud' (vorige naam) is hernoemd: recepten die eraan hangen gaan mee."""
        hernoemd = {str(b["oud"]).strip().lower(): str(b.get("naam") or "").strip() for b in lijst
                    if isinstance(b, dict) and b.get("oud") and str(b.get("naam") or "").strip()
                    and str(b["oud"]).strip().lower() != str(b["naam"]).strip().lower()}
        schoon = schoon_bronnen(lijst)
        if not schoon and lijst:
            raise ValueError("Geef de bron een naam en (bij een website) een adres")
        namen = {b["naam"].lower() for b in schoon}
        for r in self.s["recepten"].values():
            bn = str(r.get("bron_naam") or "").lower()
            if bn in hernoemd and hernoemd[bn].lower() in namen:
                r["bron_naam"] = next(b["naam"] for b in schoon if b["naam"].lower() == hernoemd[bn].lower())
        self.inst["bronnen"] = schoon
        self.save()

    def recept_bron_zetten(self, rid: str, bron_naam: str | None, eigen: bool = False) -> None:
        """Herkomst van een recept met de hand koppelen: aan een bron, als eigen recept, of terug naar onbekend."""
        r = self.s["recepten"].get(rid)
        if not r:
            raise ValueError("Recept niet gevonden")
        if bron_naam:
            echt = next((b["naam"] for b in self.inst["bronnen"] if b["naam"].lower() == bron_naam.lower()), None)
            if not echt:
                raise ValueError("Onbekende bron")
            r["bron_naam"], r["eigen"] = echt, False
            if str(r.get("bron") or "").lower() in ("ai", "gemini"):
                r["bron"] = None
            r["tags"] = [t for t in r.get("tags") or [] if t != "nieuw"]
        else:
            r.pop("bron_naam", None)
            r["eigen"] = bool(eigen)
        self.save()

    async def _haal(self, url: str, max_tekens: int = 3_000_000) -> str:
        session = async_get_clientsession(self.hass)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=25), headers=HEADERS) as resp:
            if resp.status >= 400:
                raise ValueError(f"Niet bereikbaar (HTTP {resp.status})")
            return (await resp.text())[:max_tekens]

    async def _sitemap_start(self, bron: dict) -> dict:
        """Begin met het inlezen van de sitemaps van een bron: de wachtrij vullen uit robots.txt of de gebruikelijke plekken."""
        basis = f"https://{bron['domein']}"
        uit_robots: list[str] = []
        try:
            uit_robots = sitemaps_uit_robots(await self._haal(basis + "/robots.txt", 200_000))
        except Exception as err:  # noqa: BLE001 - geen robots.txt: dan de gebruikelijke plekken
            _LOGGER.debug("robots.txt van %s: %s", bron["domein"], err)
        return {"ts": time.time(), "links": [], "wachtrij": list(dict.fromkeys(uit_robots)),
                "reserve": [basis + p for p in SITEMAP_PADEN], "gelezen": 0, "klaar": False}

    async def _sitemap_verder(self, bron: dict, e: dict, budget: float) -> None:
        """Lees sitemaps tot de tijd op is of alles gelezen is. Vier tegelijk; alleen links die bij de bron horen blijven."""
        eind = time.monotonic() + budget
        while time.monotonic() < eind and e["gelezen"] < MAX_SITEMAPS and len(e["links"]) < MAX_LINKS:
            if not e["wachtrij"]:
                if e["reserve"] and not e["links"]:
                    e["wachtrij"], e["reserve"] = e["reserve"], []
                else:
                    e["klaar"] = True
                    return
            groep = [u for u in e["wachtrij"][:4]]
            e["wachtrij"] = e["wachtrij"][4:]
            groep = [u for u in groep if not u.lower().endswith(".gz")]

            async def lees(url: str) -> tuple[str, str | None]:
                try:
                    return url, await self._haal(url, 12_000_000)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Sitemap %s: %s", url, err)
                    return url, None

            for _url, xml in await asyncio.gather(*(lees(u) for u in groep)):
                e["gelezen"] += 1
                if xml is None:
                    continue
                locs = sitemap_locs(xml)
                if is_sitemap_index(xml):
                    e["wachtrij"] = sitemap_volgorde(locs) + e["wachtrij"]
                else:
                    e["links"].extend(u for u in locs if bron_van(u, [bron]) and receptlink_mogelijk(u, bron))
        if not e["wachtrij"] and not (e["reserve"] and not e["links"]):
            e["klaar"] = True
        e["links"] = list(dict.fromkeys(e["links"]))[:MAX_LINKS]

    async def _bron_links(self, bron: dict, vernieuw: bool = False, budget: float = 40.0) -> tuple[list[str], dict]:
        """Mogelijke receptlinks van een websitebron. Grote sites worden stap voor stap ingelezen (en een dag onthouden);
        status: {klaar, gelezen, wachtrij}."""
        sleutel = bron["domein"] + bron.get("pad", "")
        e = self._sitemaps.get(sleutel)
        if vernieuw or e is None or (e["klaar"] and time.time() - e["ts"] > SITEMAP_BEWAAR):
            e = self._sitemaps[sleutel] = await self._sitemap_start(bron)
        if not e["klaar"]:
            await self._sitemap_verder(bron, e, budget)
        return list(e["links"]), {"klaar": e["klaar"], "gelezen": e["gelezen"], "wachtrij": len(e["wachtrij"])}

    async def bron_controleren(self, url: str) -> dict:
        """Een nieuwe bron nakijken: naam, kunnen we er recepten van lezen en kunnen we erin zoeken."""
        url = (url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        p = urlparse(url)
        domein = re.sub(r"(?i)^www\.", "", p.hostname or "").lower()
        if not domein or "." not in domein:
            raise ValueError("Dat is geen geldig adres")
        pad = p.path.rstrip("/")
        try:
            pagina = await self._haal(url, 2_000_000)
        except ValueError as err:
            raise ValueError(f"De site is niet te bereiken: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise ValueError(f"De site is niet te bereiken ({type(err).__name__})") from err
        bron = {"naam": site_naam(pagina, domein), "domein": domein, "pad": pad, "type": "website", "aan": True, "notitie": ""}
        links: list[str] = []
        status: dict = {"klaar": True, "gelezen": 0, "wachtrij": 0}
        try:
            links, status = await self._bron_links(bron, vernieuw=True, budget=20.0)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Sitemap controleren: %s", err)
        leesbaar = bool(recept_uit_jsonld(pagina))
        if not leesbaar:
            for l in links[:2]:  # steekproef: twee pagina's (receptsites vragen om rust; we doen er maar weinig)
                try:
                    if recept_uit_jsonld(await self._haal(l, 2_000_000)):
                        leesbaar = True
                        break
                except Exception:  # noqa: BLE001
                    continue
        advies = bron_advies(leesbaar, len(links))
        if not status["klaar"]:
            advies += f" (De sitemap is groot; ik las {status['gelezen']} bestanden en lees de rest bij het zoeken verder in.)"
        return {**bron, "gevonden": len(links), "leesbaar": leesbaar, "advies": advies}

    async def bron_zoeken(self, bron_naam: str | None, vraag: str, ai: bool = False) -> dict:
        """Recepten zoeken in de bronnen (websites). Zonder vraag een willekeurige greep. ai=True: Gemini kiest uit de kandidaten
        wat bij de wens, het gezin, de voorraad en de aanbiedingen past. Resultaat: {resultaten: [{url, titel, bron, reden}], nota}."""
        bronnen = [b for b in self.inst["bronnen"] if b["type"] == "website" and b.get("aan", True)]
        if bron_naam:
            bronnen = [b for b in bronnen if b["naam"].lower() == bron_naam.lower()]
            if not bronnen:
                raise ValueError("Die bron staat niet aan of is geen website")
        if not bronnen:
            raise ValueError("Er is geen website als bron. Voeg er een toe bij Bronnen.")
        klaar = {url_sleutel(r.get("bron")) for r in self.s["recepten"].values() if r.get("bron")}
        per_url: dict[str, str] = {}
        fouten: list[str] = []
        bezig: list[str] = []
        for b in bronnen:
            try:
                links, status = await self._bron_links(b, budget=40.0 / len(bronnen))
                for u in links:
                    per_url.setdefault(u, b["naam"])
                if not status["klaar"]:
                    bezig.append(f"{b['naam']} ({status['gelezen']} bestanden gelezen)")
            except Exception as err:  # noqa: BLE001
                fouten.append(f"{b['naam']}: {err}")
        if not per_url:
            if bezig:
                raise ValueError("Ik ben nog bezig met het inlezen van " + ", ".join(bezig) + ". Probeer het over een minuut nog eens.")
            raise ValueError("Ik kon in deze bron(nen) niets vinden om in te zoeken." + (f" ({'; '.join(fouten)})" if fouten else "")
                             + " Plak een link of gebruik een foto.")
        self._zoek_nota = ("Nog niet alles ingelezen: " + ", ".join(bezig) + ". Zoek straks nog eens voor meer resultaten.") if bezig else ""
        vraag = (vraag or "").strip()
        if not ai:
            links = zoek_links(list(per_url), vraag, klaar, 12)
            return {"resultaten": [{"url": u, "titel": titel_uit_url(u), "bron": per_url[u], "reden": ""} for u in links],
                    "nota": self._zoek_nota}
        kand = kandidaten_voor_ai(list(per_url), vraag, klaar, 300)
        kandidaten = [{"n": n, "url": u, "titel": titel_uit_url(u), "bron": per_url[u]} for n, u in enumerate(kand, 1)]
        voorraad, _bijna = await self._voorraad()
        namen = {p["id"]: p["name"] for p in self._producten()}
        in_huis = sorted({namen[pid] for pid, a in voorraad.items() if a and pid in namen})[:80]
        acties = sorted({namen[pid] for pid in self._acties() if pid in namen})[:25]
        context = context_tekst(self.menu, self.inst.get("huisregels", ""), self.s["feedback"], date.today())
        res = await self._vraag_ai("Eten: recepten kiezen uit bronnen", kies_prompt(kandidaten, vraag, context, in_huis, acties))
        gekozen = kies_toepassen(res, kandidaten)
        if not gekozen:
            raise ValueError("Gemini kon niets passends kiezen. Probeer een andere wens of zoek zelf.")
        return {"resultaten": [{"url": k["url"], "titel": k["titel"], "bron": k["bron"], "reden": k["reden"]} for k in gekozen],
                "nota": self._zoek_nota}

    # --- weekmenu ---------------------------------------------------------
    # --- weekmenu: context ----------------------------------------------------
    @property
    def menu(self) -> dict:
        return schoon_menu(self.inst)

    async def _voorraad(self) -> tuple[dict[int, float], set[int]]:
        try:
            stock = await self.c.api.get("/stock") or []
        except GrocyError as err:
            _LOGGER.warning("Voorraad ophalen mislukt: %s", err)
            stock = []
        return voorraad_overzicht(stock, date.today())

    def _acties(self) -> dict[int, dict]:
        """Aanbiedingen met voordeel: {product_id: aanbieding}."""
        aanb = getattr(self.c, "aanbiedingen", None)
        if aanb is None:
            return {}
        try:
            lijst = aanb.zichtbaar()
        except Exception:  # noqa: BLE001 - aanbiedingen zijn een extraatje
            return {}
        return {int(a["product_id"]): a for a in lijst if a.get("product_id") and a.get("voordeel") is not False}

    def _recepten_compact(self) -> list[dict]:
        gem = gemiddelden(self.s["beoordelingen"])
        laatst: dict[str, str] = {}
        for p in self.s["planning"]:
            if p.get("recept_id"):
                laatst[p["recept_id"]] = max(laatst.get(p["recept_id"], ""), p["datum"])
        uit = []
        for r in self.s["recepten"].values():
            if is_afgewezen(self.s["feedback"], r["id"], r["naam"]):
                continue  # 'niet lekker': nooit meer voorstellen
            c = recept_compact(r, gem.get(r["id"]), laatst.get(r["id"]))
            fb = sorted((self.s["feedback"].get(r["id"]) or {}).get("soorten") or {})
            if fb:
                c["feedback"] = fb
            uit.append(c)
        return uit

    def dagen_info(self, van: str, tot: str) -> list[dict]:
        """Standaard per dag in de gekozen periode: wie eet mee, soort dag, wie kookt, tijd, wat er al staat."""
        begin, eind = date.fromisoformat(van), date.fromisoformat(tot)
        if eind < begin:
            begin, eind = eind, begin
        if (eind - begin).days > 20:
            raise ValueError("Kies een periode van hooguit 3 weken")
        menu = self.menu
        gepland = {p["datum"]: p for p in self.s["planning"]}
        extern: dict[str, str] = {}
        for e in self.extern:
            extern.setdefault(e["datum"], e["titel"])
        uit = []
        d = begin
        while d <= eind:
            info = dag_info(menu, d)
            p = gepland.get(info["datum"])
            info["gepland"] = p.get("titel") if p else None
            info["agenda"] = extern.get(info["datum"])
            info["aan"] = not p and info["datum"] >= date.today().isoformat()
            uit.append(info)
            d += timedelta(days=1)
        return uit

    # --- weekmenu: voorstel met 3 opties per dag ---------------------------
    def _optie(self, x: dict, labels_ctx: tuple, dag: dict) -> dict | None:
        """AI-optie omzetten: bestaand recept, nieuw recept (concept) of alleen een titel."""
        voorraad, bijna, acties, namen = labels_ctx
        rid = x.get("recept_id") if x.get("recept_id") in self.s["recepten"] else None
        concept = None
        if not rid and isinstance(x.get("nieuw_recept"), dict) and x["nieuw_recept"].get("naam"):
            concept = schoon_recept({**x["nieuw_recept"], "id": None})
            concept["tags"] = list(dict.fromkeys([*concept["tags"], "nieuw"]))[:8]
            concept["bron"] = "AI"
        recept = self.s["recepten"].get(rid) if rid else concept
        titel = recept["naam"] if recept else str(x.get("titel") or "").strip()
        if not titel or is_afgewezen(self.s["feedback"], rid, titel):
            return None
        rol = x.get("rol") if x.get("rol") in ROLLEN else ("nieuw" if concept else "favoriet")
        return {
            "id": nieuw_id(), "rol": rol, "recept_id": rid or (concept["id"] if concept else None),
            "titel": titel, "reden": str(x.get("reden") or "").strip(), "concept": concept,
            "tijd": (recept or {}).get("tijd"), "vega": bool((recept or {}).get("vega")),
            "labels": labels_voor(recept, voorraad, bijna, acties, namen),
        }

    async def _vraag_opties(self, te_plannen: list[dict], overige: list[dict], wens: str) -> dict[str, list[dict]]:
        menu = self.menu
        voorraad, bijna = await self._voorraad()
        acties = self._acties()
        namen = {p["id"]: p["name"] for p in self._producten()}
        context = context_tekst(menu, self.inst.get("huisregels", ""), self.s["feedback"], date.today())
        recepten = self._recepten_compact()
        correctie = ""
        res: Any = None
        for _poging in range(2):
            res = await self._vraag_ai("Eten: weekmenu met opties", voorstel_prompt(
                te_plannen, overige, menu["profielen"], context, wens, recepten, namen, voorraad, bijna, acties,
                date.today(), correctie))
            correctie = controleer_antwoord(res, te_plannen)
            if not correctie:
                break
            _LOGGER.info("Weekmenu: antwoord klopte niet (%s), opnieuw", correctie)
        if not isinstance(res, list):
            raise ValueError("De AI gaf geen bruikbaar weekmenu terug")
        ctx = (voorraad, bijna, acties, namen)
        per_dag: dict[str, list[dict]] = {}
        dagen = {d["datum"]: d for d in te_plannen}
        for x in res:
            if not isinstance(x, dict):
                continue
            iso = str(x.get("datum") or "")[:10]
            if iso not in dagen:
                continue
            opties = []
            gezien = set()
            for o in x.get("opties") or []:
                if not isinstance(o, dict):
                    continue
                opt = self._optie(o, ctx, dagen[iso])
                if opt and opt["titel"].lower() not in gezien:
                    gezien.add(opt["titel"].lower())
                    opties.append(opt)
            per_dag[iso] = opties[:3]
        return per_dag

    async def voorstel_maken(self, dagen: list[dict], wens: str) -> None:
        """Voorstel voor de gekozen dagen. dagen: [{datum, aan, aanwezig, soort, kookt, minuten, vriezer, wens}]."""
        if not dagen:
            raise ValueError("Kies minstens één dag")
        await self.sync(forceer=True)
        menu = self.menu
        gepland = {p["datum"]: p for p in self.s["planning"]}
        extern: dict[str, str] = {}
        vervangt: dict[str, list[str]] = {}
        for e in self.extern:
            extern.setdefault(e["datum"], e["titel"])
            if e.get("uid"):
                vervangt.setdefault(e["datum"], []).append(e["uid"])
        uit, te_plannen, overige = [], [], []
        for x in sorted(dagen, key=lambda y: y["datum"]):
            iso = str(x["datum"])[:10]
            basis = dag_info(menu, date.fromisoformat(iso))
            d = {**basis, **{k: x[k] for k in ("soort", "kookt", "minuten", "vriezer", "aanwezig", "wens", "notitie") if k in x}}
            d["agenda"] = extern.get(iso)
            d["aanwezig"] = [n for n in d.get("aanwezig") or [] if n]
            if not x.get("aan", True):
                if iso in gepland:
                    overige.append({**d, "vast_titel": gepland[iso].get("titel")})
                continue
            d.update({"opties": [], "keuze": None, "overslaan": False, "vervangt": vervangt.get(iso, []),
                      "porties": porties_voor(menu["profielen"], d["aanwezig"]) + int(d.get("vriezer") or 0)})
            if d["soort"] == "koken":
                te_plannen.append(d)
            uit.append(d)
        if te_plannen:
            opties = await self._vraag_opties(te_plannen, overige, wens)
            for d in te_plannen:
                d["opties"] = opties.get(d["datum"], [])
                d["keuze"] = 0 if d["opties"] else None
        self.s["voorstel"] = {
            "versie": 2, "van": uit[0]["datum"] if uit else dagen[0]["datum"], "tot": uit[-1]["datum"] if uit else dagen[-1]["datum"],
            "wens": wens, "dagen": uit, "gemaakt": datetime.now().isoformat(timespec="minutes"),
        }
        self.save()

    async def voorstel_dag_opnieuw(self, datum: str, wens: str) -> None:
        """Drie nieuwe opties voor één dag in het voorstel (met een wens)."""
        v = self.s.get("voorstel")
        d = next((x for x in (v or {}).get("dagen", []) if x["datum"] == datum), None)
        if d is None:
            raise ValueError("Deze dag staat niet in het voorstel")
        d["wens"] = wens
        d["soort"] = "koken"
        overige = [{**x, "vast_titel": self._gekozen(x)["titel"]} for x in v["dagen"]
                   if x["datum"] != datum and self._gekozen(x)]
        opties = await self._vraag_opties([d], overige, v.get("wens", ""))
        d["opties"] = opties.get(datum, [])
        d["keuze"] = 0 if d["opties"] else None
        d["geen"] = False
        self.save()

    def _gekozen(self, d: dict) -> dict | None:
        if d.get("overslaan"):
            return None
        if d.get("geen") or d.get("soort") in VASTE_TITEL:
            soort = "niet" if d.get("geen") else d["soort"]
            return {"recept_id": None, "titel": d.get("notitie") if soort == "anders" and d.get("notitie") else VASTE_TITEL[soort],
                    "concept": None, "geen": soort == "niet", "boodschappen": False}
        if d.get("keuze") is None or d["keuze"] >= len(d.get("opties") or []):
            return None
        o = d["opties"][d["keuze"]]
        return {**o, "geen": False, "boodschappen": True}

    def voorstel_wijzig(self, datum: str, velden: dict) -> None:
        """Keuze per dag: keuze (index), geen maaltijd, overslaan, wie eet mee, porties, wie kookt."""
        v = self.s.get("voorstel")
        if not v:
            raise ValueError("Er is geen voorstel")
        menu = self.menu
        for d in v["dagen"]:
            if d["datum"] != datum:
                continue
            if "keuze" in velden:
                k = velden["keuze"]
                d["keuze"] = int(k) if k is not None and 0 <= int(k) < len(d.get("opties") or []) else None
                d["geen"] = False
            for k in ("geen", "overslaan"):
                if k in velden:
                    d[k] = bool(velden[k])
            if "kookt" in velden:
                d["kookt"] = str(velden["kookt"] or "")
            if "aanwezig" in velden:
                d["aanwezig"] = [str(n) for n in velden["aanwezig"] or []]
                d["porties"] = porties_voor(menu["profielen"], d["aanwezig"]) + int(d.get("vriezer") or 0)
            if velden.get("porties"):
                d["porties"] = max(1, int(velden["porties"]))
        self.save()

    async def dag_aanpassen(self, datum: str, wens: str, plan_id: str | None = None) -> None:
        """Eén dag opnieuw laten invullen door de AI, met een wens. In een voorstel direct vervangen;
        bij een geplande dag eerst als suggestie tonen (toepassen of weggooien)."""
        d = date.fromisoformat(datum)
        ws = week_start(d)
        v = self.s.get("voorstel")
        in_voorstel = False  # in het voorstel vraag je per dag nieuwe opties (voorstel_dag_opnieuw)
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
                g = self._gekozen(x)
                if g and ws.isoformat() <= x["datum"] < (ws + timedelta(days=7)).isoformat():
                    week[x["datum"]] = {"datum": x["datum"], "dag": DAGEN[date.fromisoformat(x["datum"]).weekday()], "vast": g["titel"]}
        week.pop(datum, None)
        gem = gemiddelden(self.s["beoordelingen"])
        laatst: dict[str, str] = {}
        for p in self.s["planning"]:
            if p.get("recept_id"):
                laatst[p["recept_id"]] = max(laatst.get(p["recept_id"], ""), p["datum"])
        recepten = self._recepten_compact()
        res = await self._vraag_ai("Eten: dag aanpassen", dag_prompt(
            {"datum": datum, "dag": DAGEN[d.weekday()],
             "agenda": next((e["titel"] for e in self.extern if e["datum"] == datum), None)},
            wens, sorted(week.values(), key=lambda x: x["datum"]),
            recepten, context_tekst(self.menu, self.inst.get("huisregels", ""), self.s["feedback"], date.today()),
            self._producten(), date.today()))
        if not isinstance(res, dict):
            raise ValueError("De AI gaf geen bruikbaar voorstel terug")
        concept = None
        rid = res.get("recept_id") if res.get("recept_id") in self.s["recepten"] else None
        if not rid and isinstance(res.get("nieuw_recept"), dict) and res["nieuw_recept"].get("naam"):
            concept = schoon_recept({**res["nieuw_recept"], "id": None})
            concept["tags"] = list(dict.fromkeys([*concept["tags"], "nieuw"]))[:8]
            concept["bron"] = "AI"
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
        return {"plan_ids": [item["id"]] if item.get("boodschappen", True) and item.get("recept_id") else []}

    def suggestie_weg(self, datum: str) -> None:
        self.s["suggesties"].pop(datum, None)
        self.save()

    def voorstel_weg(self) -> None:
        self.s["voorstel"] = None
        self.save()

    async def voorstel_goedkeuren(self) -> dict:
        """Gekozen opties plannen (agenda). De boodschappen gaan pas na het overzicht van benodigde producten op de lijst."""
        v = self.s.get("voorstel")
        if not v:
            raise ValueError("Er is geen voorstel")
        nieuw: list[dict] = []
        for d in v["dagen"]:
            g = self._gekozen(d)
            if not g:
                continue
            c = g.get("concept")
            if c:
                self.s["recepten"][c["id"]] = c  # nieuw recept pas nu in de bibliotheek
            velden = {"recept_id": g.get("recept_id"), "titel": g["titel"], "kookt": d.get("kookt") or "",
                      "porties": d.get("porties") if g.get("boodschappen") else None,
                      "vriezer": d.get("vriezer") or 0 if g.get("boodschappen") else 0,
                      "boodschappen": bool(g.get("boodschappen")), "geen": bool(g.get("geen"))}
            item = await self.plan(d["datum"], velden, sync=False)
            item["aanwezig"] = d.get("aanwezig") or []
            nieuw.append(item)
            await self._extern_weg(d.get("vervangt") or [])
        self.s["voorstel"] = None
        self.save()
        return {"gepland": len(nieuw), "plan_ids": [p["id"] for p in nieuw if p.get("boodschappen", True) and p.get("recept_id")]}

    # --- dag voor dag plannen ----------------------------------------------
    # Per dag een lichte vraag aan Gemini (alleen nummers + reden), met live meekijken. 'Plan in' haalt het recept op
    # (bij een link van een bron), zet het in planning en agenda en de producten direct op de lijst. Elke maaltijd
    # onthoudt wat ze op de lijst zette (lijst) en wat ze uit de voorraad gebruikt (uit_voorraad), zodat wisselen of
    # verwijderen dat netjes terugdraait.
    def _herkomst_namen(self) -> dict[str, str | None]:
        uit: dict[str, str | None] = {}
        for r in self.s["recepten"].values():
            h = herkomst(r, self.inst["bronnen"])
            uit[r["id"]] = h["naam"] if h["soort"] == "bron" else None
        return uit

    async def _vraag_ai_live(self, taak: str, prompt: str, stuur) -> Any:
        """Gemini direct (met gedachten in het meekijkvenster); anders de AI-taak van Home Assistant."""
        km = sleutel_en_model(self.hass)
        if km:
            key, model = km
            stuur({"soort": "stap", "id": "ai", "tekst": f"Gemini ({model}) denkt na…", "status": "bezig"})
            try:
                tekst = await gemini_stream(async_get_clientsession(self.hass), key, model, prompt,
                                            lambda t: stuur({"soort": "gedachte", "tekst": t}))
                res = parse_json(tekst)
                if res is not None:
                    stuur({"soort": "stap", "id": "ai", "tekst": f"Gemini ({model}) heeft gekozen", "status": "klaar"})
                    return res
                _LOGGER.warning("Dagvoorstel: geen JSON van Gemini: %s", tekst[:200])
            except GeminiNietBeschikbaar as err:
                _LOGGER.info("Dagvoorstel: Gemini direct lukte niet (%s), via de AI-taak", err)
            stuur({"soort": "stap", "id": "ai", "tekst": "Direct meekijken lukte niet; ik vraag het via de AI-taak (zonder tussenstappen)…",
                   "status": "bezig"})
        else:
            stuur({"soort": "stap", "id": "ai", "tekst": "Gemini denkt na via de AI-taak (geen Google AI-sleutel gevonden, dus zonder tussenstappen)…",
                   "status": "bezig"})
        res = await self._vraag_ai(taak, prompt)
        stuur({"soort": "stap", "id": "ai", "tekst": "Gemini heeft gekozen", "status": "klaar"})
        return res

    async def dag_opties(self, datum: str, filters: dict, aanwezig: list[str] | None, stuur) -> list[dict]:
        """Drie opties voor één dag. stuur(event) krijgt de tussenstappen voor het meekijkvenster."""
        t0 = time.monotonic()
        datum = str(datum)[:10]
        d = date.fromisoformat(datum)
        bronnen_aan = [b for b in self.inst["bronnen"] if b.get("aan", True)]
        f = schoon_filters(filters, [b["naam"] for b in bronnen_aan])
        self.inst["dagfilters"] = {k: f[k] for k in ("voorraad", "snel", "aanbieding", "bronnen")}
        menu = self.menu
        dag = dag_info(menu, d)
        if aanwezig is not None:
            dag["aanwezig"] = [str(n) for n in aanwezig if str(n).strip()]
        stuur({"soort": "stap", "id": "wie", "status": "klaar",
               "tekst": f"Wie eet mee: {', '.join(dag['aanwezig']) or 'niemand opgegeven'}"
                        f" · max {min(int(dag['minuten'] or 45), 30) if f['snel'] else dag['minuten']} min"})

        # voorraad en aanbiedingen (altijd ophalen: ook voor de labels bij de opties)
        voorraad, bijna = await self._voorraad()
        namen = {p["id"]: p["name"] for p in self._producten()}
        acties = self._acties()
        in_huis = sorted({namen[pid] for pid in voorraad if pid in namen})
        bijna_op = sorted({namen[pid] for pid in bijna if pid in namen})
        actie_namen = sorted({namen.get(pid) or str(a.get("product") or "") for pid, a in acties.items()} - {""})
        if f["voorraad"]:
            stuur({"soort": "stap", "id": "voorraad", "status": "klaar",
                   "tekst": f"Voorraad bekeken: {len(in_huis)} producten in huis"
                            + (f", moet op: {', '.join(bijna_op[:4])}" if bijna_op else "")})
        if f["aanbieding"]:
            stuur({"soort": "stap", "id": "acties", "status": "klaar",
                   "tekst": f"Aanbiedingen: {len(actie_namen)} producten" + (f", o.a. {', '.join(actie_namen[:4])}" if actie_namen else "")})

        # eigen recepten (R-nummers)
        herk = self._herkomst_namen()
        compact = recepten_selectie(self._recepten_compact(), f, herk)
        compact.sort(key=lambda r: (-(r.get("score") or 0), -len(r.get("feedback") or []), r.get("laatst_gegeten") or ""))
        lab_v, lab_b = (voorraad, bijna) if f["voorraad"] else ({}, set())
        lab_a = acties if f["aanbieding"] else {}
        rec_codes: list[dict] = []
        for i, r in enumerate(compact[:MAX_RECEPTEN], 1):
            lab = labels_voor(self.s["recepten"][r["id"]], lab_v, lab_b, lab_a, namen)
            rec_codes.append({
                "code": f"R{i}", "recept_id": r["id"], "naam": r["naam"], "tijd": r.get("tijd"), "vega": r.get("vega"),
                "score": r.get("score"), "feedback": r.get("feedback"), "laatst": r.get("laatst_gegeten"),
                "bron": herk.get(r["id"]), "in_huis": lab["in_huis"], "bijna_op": lab["bijna_op"],
                "actie": [re.sub(r"\s*\(.*\)$", "", x) for x in lab["actie"]],
            })
        if BIBLIOTHEEK in f["bronnen"]:
            stuur({"soort": "stap", "id": "bib", "status": "klaar", "tekst": f"Mijn bibliotheek: {len(rec_codes)} recepten"})
        elif rec_codes:
            stuur({"soort": "stap", "id": "bib", "status": "klaar",
                   "tekst": f"Al bewaard van deze bron(nen): {len(rec_codes)} recepten"})

        # receptlinks van de gekozen websites (L-nummers)
        web = [b for b in bronnen_aan if b["naam"] in f["bronnen"] and b["type"] == "website"]
        klaar = {url_sleutel(r.get("bron")) for r in self.s["recepten"].values() if r.get("bron")}
        vraag = zoekvraag(f, bijna_op, actie_namen)
        link_codes: list[dict] = []
        per_bron = MAX_LINKS_PER_DAG // max(1, len(web))
        for b in web:
            sid = f"bron-{b['naam']}"
            stuur({"soort": "stap", "id": sid, "status": "bezig", "tekst": f"{b['naam']}: receptenlijst ophalen…"})
            try:
                links, status = await self._bron_links(b, budget=25.0 / len(web))
            except Exception as err:  # noqa: BLE001 - één bron die niet wil mag de rest niet tegenhouden
                stuur({"soort": "stap", "id": sid, "status": "fout", "tekst": f"{b['naam']}: niet bereikbaar ({err})"})
                continue
            kand = kandidaten_voor_ai(links, vraag, klaar, per_bron)
            for u in kand:
                link_codes.append({"code": f"L{len(link_codes) + 1}", "url": u, "titel": titel_uit_url(u), "bron": b["naam"]})
            tekst = f"{b['naam']}: {len(links)} recepten gevonden, {len(kand)} voorgelegd"
            if not status["klaar"]:
                tekst += " (nog niet alles ingelezen)"
            stuur({"soort": "stap", "id": sid, "status": "klaar" if kand else "fout", "tekst": tekst})
        if not rec_codes and not link_codes:
            raise ValueError("Er is niets om uit te kiezen. Zet 'Mijn bibliotheek' aan of kies een andere bron.")

        # de rest van de week, zodat Gemini niet twee keer hetzelfde voorstelt
        van, tot = (d - timedelta(days=6)).isoformat(), (d + timedelta(days=6)).isoformat()
        week = [p["titel"] for p in self.s["planning"]
                if van <= p["datum"] <= tot and p["datum"] != datum and p.get("titel") and not p.get("geen")]
        context = context_tekst(menu, self.inst.get("huisregels", ""), self.s["feedback"], date.today())
        prompt = opties_prompt(dag, f, context, week, rec_codes, link_codes, in_huis, bijna_op, actie_namen, date.today())
        res = await self._vraag_ai_live("Eten: dagvoorstel", prompt, stuur)
        codes = {c["code"]: c for c in rec_codes + link_codes}
        gekozen = opties_toepassen(res, codes)
        if not gekozen:
            raise ValueError("Gemini gaf geen bruikbare keuze. Probeer het nog eens.")
        opties: list[dict] = []
        for o in gekozen:
            if o.get("recept_id"):
                rec = self.s["recepten"][o["recept_id"]]
                opties.append({
                    "id": nieuw_id(), "soort": "recept", "recept_id": rec["id"], "titel": rec["naam"], "url": None,
                    "bron": herk.get(rec["id"]) or "Mijn bibliotheek", "tijd": rec.get("tijd"), "vega": bool(rec.get("vega")),
                    "reden": o["reden"], "labels": labels_voor(rec, voorraad, bijna, acties, namen),
                })
            else:
                opties.append({
                    "id": nieuw_id(), "soort": "link", "recept_id": None, "titel": o["titel"], "url": o["url"],
                    "bron": o["bron"], "tijd": None, "vega": False, "reden": o["reden"], "labels": {},
                })
        duur = round(time.monotonic() - t0)
        self.s["dagopties"][datum] = {"opties": opties, "filters": f, "aanwezig": dag["aanwezig"],
                                      "gemaakt": datetime.now().isoformat(timespec="minutes"), "duur": duur}
        self.save()
        stuur({"soort": "stap", "id": "klaar", "status": "klaar", "tekst": f"Klaar in {duur} s"})
        return opties

    def dag_opties_weg(self, datum: str) -> None:
        self.s["dagopties"].pop(str(datum)[:10], None)
        self.save()

    async def dag_inplannen(self, datum: str, keuze: dict, aanwezig: list[str] | None, stuur) -> dict:
        """Eén dag plannen en de boodschappen direct op de lijst zetten.

        keuze: {soort: 'recept', recept_id} | {soort: 'link', url, bron} | {soort: 'vriezer'|'anders'|'niet'}.
        """
        datum = str(datum)[:10]
        d = date.fromisoformat(datum)
        menu = self.menu
        info = dag_info(menu, d)
        soort = keuze.get("soort")
        if soort in VASTE_TITEL:
            titel = info["notitie"] if soort == "anders" and info.get("notitie") else VASTE_TITEL[soort]
            stuur({"soort": "stap", "id": "plan", "status": "bezig", "tekst": "In de planning en de agenda zetten…"})
            item = await self.plan(datum, {"recept_id": None, "titel": titel, "kookt": "", "porties": None, "vriezer": 0,
                                           "boodschappen": False, "geen": soort == "niet"}, sync=False)
            await self._lijst_terug(item)
            stuur({"soort": "stap", "id": "plan", "status": "klaar", "tekst": f"Gepland: {item['titel']}"})
            self.s["dagopties"].pop(datum, None)
            self.save()
            return {"plan_id": item["id"], "titel": item["titel"], "recept_id": None, "toegevoegd": [], "in_huis": [], "los": []}

        rid = keuze.get("recept_id")
        if soort == "link":
            url = str(keuze.get("url") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                raise ValueError("Geen geldige link")
            bestaand = next((r for r in self.s["recepten"].values() if r.get("bron") and url_sleutel(r["bron"]) == url_sleutel(url)), None)
            if bestaand:
                rid = bestaand["id"]
            else:
                stuur({"soort": "stap", "id": "ophalen", "status": "bezig",
                       "tekst": f"Recept ophalen van {keuze.get('bron') or urlparse(url).hostname} en koppelen aan je producten…"})
                rid = await self.recept_importeren(url, None)
                r = self.s["recepten"][rid]
                ingr = [i for i in r.get("ingredienten") or [] if not i.get("basis")]
                gekoppeld = sum(1 for i in ingr if i.get("product_id"))
                stuur({"soort": "stap", "id": "ophalen", "status": "klaar",
                       "tekst": f"Recept overgenomen: {r['naam']} ({len(r.get('ingredienten') or [])} ingrediënten, "
                                f"{gekoppeld} van {len(ingr)} gekoppeld aan Grocy)"
                                + (" · vertaald" if r.get("vertaald_uit") else "")})
        if not rid or rid not in self.s["recepten"]:
            raise ValueError("Recept niet gevonden")
        aanwezig = [str(n) for n in (aanwezig if aanwezig is not None else info["aanwezig"]) if str(n).strip()]
        vriezer = int(info.get("vriezer") or 0)
        stuur({"soort": "stap", "id": "plan", "status": "bezig", "tekst": "In de planning en de agenda zetten…"})
        item = await self.plan(datum, {"recept_id": rid, "titel": self.s["recepten"][rid]["naam"], "kookt": info.get("kookt") or "",
                                       "porties": porties_voor(menu["profielen"], aanwezig) + vriezer, "vriezer": vriezer,
                                       "boodschappen": True, "geen": False}, sync=False)
        item["aanwezig"] = aanwezig
        stuur({"soort": "stap", "id": "plan", "status": "klaar", "tekst": f"Gepland: {item['titel']} ({item['porties']} porties)"})
        stuur({"soort": "stap", "id": "lijst", "status": "bezig", "tekst": "Boodschappen op de lijst zetten…"})
        res = await self._lijst_voor_plan(item)
        n = len(res["toegevoegd"])
        stuur({"soort": "stap", "id": "lijst", "status": "klaar",
               "tekst": f"{n} {'product' if n == 1 else 'producten'} op de lijst" if n else "Alles is al in huis"})
        self.s["dagopties"].pop(datum, None)
        self.save()
        return {"plan_id": item["id"], "titel": item["titel"], "recept_id": rid, **res}

    async def _lijst_voor_plan(self, item: dict) -> dict:
        """Zet de boodschappen voor één geplande maaltijd op de lijst; voorraad die een andere maaltijd al gebruikt telt niet mee."""
        await self._lijst_terug(item)
        nodig = benodigd([item], self.s["recepten"])
        voorraad, _bijna = await self._voorraad()
        gereserveerd = reserveringen(self.s["planning"], date.today().isoformat(), behalve=item["id"])
        uit_v, kopen = verdeel({pid: n["aantal"] for pid, n in nodig.items()}, voorraad, gereserveerd)
        await self.c.async_refresh()
        d = self.c.data
        regels, toegevoegd = [], []
        for pid, n in sorted(kopen.items()):
            if pid not in d.products:
                continue
            lid = d.list_for_product(pid)
            await self.c.add_product(lid, pid, n, f"recept: {item.get('titel') or ''}"[:120])
            regels.append({"list_id": lid, "product_id": pid, "aantal": n})
            toegevoegd.append({"product_id": pid, "naam": d.products[pid]["name"], "aantal": n,
                               "winkel": d.lists.get(lid, {}).get("name", "?")})
        r = self.s["recepten"].get(item.get("recept_id") or "") or {}
        los = [i["tekst"] for i in r.get("ingredienten") or [] if not i.get("product_id") and not i.get("basis")]
        item["lijst"] = regels
        item["uit_voorraad"] = {str(pid): n for pid, n in uit_v.items()}
        item["op_lijst"] = True
        self.save()
        await self.c.async_refresh()
        return {"toegevoegd": toegevoegd,
                "in_huis": [d.products[pid]["name"] for pid in uit_v if pid in d.products],
                "los": los}

    async def _lijst_terug(self, item: dict) -> int:
        """Haal van de lijst wat deze maaltijd erop zette (alleen wat nog open staat, niet wat al in het mandje ligt)."""
        regels = item.pop("lijst", None) or []
        item.pop("uit_voorraad", None)
        if not regels or item.get("datum", "") < date.today().isoformat():
            return 0
        await self.c.async_refresh()
        d = self.c.data
        n = 0
        for r in regels:
            try:
                open_ = d.open_item_for_product(int(r["list_id"]), int(r["product_id"]))
                if not open_:
                    continue
                await self.c.set_amount(open_.id, round(open_.amount - float(r["aantal"]), 3))
                n += 1
            except (GrocyError, KeyError, ValueError) as err:
                _LOGGER.warning("Lijstregel terugdraaien mislukt: %s", err)
        item["op_lijst"] = False
        return n

    # --- benodigde producten: eerst tonen, dan pas op de lijst --------------
    async def benodigd_overzicht(self, plan_ids: list[str]) -> dict:
        items = [p for p in self.s["planning"] if p["id"] in plan_ids and p["datum"] >= date.today().isoformat()]
        nodig = benodigd(items, self.s["recepten"])
        voorraad, _bijna = await self._voorraad()
        await self.c.async_refresh()
        d = self.c.data
        acties = self._acties()
        rijen = []
        for pid, n in nodig.items():
            if pid not in d.products:
                continue
            lid = d.list_for_product(pid)
            al = sum(i.amount for i in d.items if i.product_id == pid and not i.done)
            in_huis = voorraad.get(pid, 0.0)
            tekort = n["aantal"] - in_huis - al
            rijen.append({
                "product_id": pid, "naam": d.products[pid]["name"], "list_id": lid,
                "winkel": d.lists.get(lid, {}).get("name", "?"), "nodig": n["aantal"], "in_huis": in_huis,
                "op_lijst": al, "toevoegen": float(max(0, math.ceil(tekort - 1e-9))), "voor": n["voor"],
                "actie": (acties.get(pid) or {}).get("actie"),
            })
        rijen.sort(key=lambda r: (r["winkel"], r["naam"].lower()))
        los = []
        for it in items:
            r = self.s["recepten"].get(it.get("recept_id") or "")
            if not r or not it.get("boodschappen", True):
                continue
            for ing in r.get("ingredienten") or []:
                if not ing.get("product_id") and not ing.get("basis"):
                    los.append({"tekst": ing["tekst"], "voor": r["naam"]})
        return {"plan_ids": [p["id"] for p in items], "rijen": rijen, "los": los,
                "lijsten": [{"id": lid, "name": l["name"]} for lid, l in sorted(d.lists.items())]}

    async def lijst_toevoegen(self, plan_ids: list[str], regels: list[dict], los: list[dict]) -> int:
        """Gekozen producten (met aantallen) op de lijst van hun winkel zetten."""
        await self.c.async_refresh()
        d = self.c.data
        titels = {p["id"]: p.get("titel") for p in self.s["planning"]}
        notitie = ("recept: " + ", ".join(t for pid, t in titels.items() if pid in plan_ids and t))[:120]
        n = 0
        for r in regels:
            pid, aantal = int(r["product_id"]), float(r.get("aantal") or 0)
            if aantal <= 0 or pid not in d.products:
                continue
            await self.c.add_product(int(r.get("list_id") or d.list_for_product(pid)), pid, aantal, notitie)
            n += 1
        for x in los:
            if str(x.get("tekst") or "").strip():
                lid = int(x.get("list_id") or min(d.lists))
                await self.c.api.add_list_item(lid, None, 1, str(x["tekst"]).strip()[:120])
                n += 1
        for p in self.s["planning"]:
            if p["id"] in plan_ids:
                p["op_lijst"] = True
        self.save()
        await self.c.async_refresh()
        return n

    # --- feedback (👍 wel lekker, ⚡ lekker snel, 👎 niet lekker, 🕒 teveel werk) ----
    def feedback(self, recept_id: str | None, titel: str | None, soort: str, aan: bool) -> None:
        if soort not in FEEDBACK:
            raise ValueError("Onbekende feedback")
        if recept_id and recept_id in self.s["recepten"]:
            titel = self.s["recepten"][recept_id]["naam"]
        elif recept_id:
            recept_id = None  # concept dat (nog) niet in de bibliotheek staat: op titel onthouden
        if not titel:
            raise ValueError("Geen gerecht om te beoordelen")
        key = feedback_key(recept_id, titel)
        f = self.s["feedback"].setdefault(key, {"recept_id": recept_id, "titel": titel, "soorten": {}})
        f["titel"] = titel
        if aan:
            f["soorten"][soort] = date.today().isoformat()
            # tegenstrijdige knoppen uitzetten
            for a, b in (("wel_lekker", "niet_lekker"), ("lekker_snel", "teveel_werk")):
                if soort == a:
                    f["soorten"].pop(b, None)
                elif soort == b:
                    f["soorten"].pop(a, None)
        else:
            f["soorten"].pop(soort, None)
        if not f["soorten"]:
            self.s["feedback"].pop(key, None)
        self.save()

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
        oud_bs = item.get("boodschappen", True)
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
        if item.get("lijst") and (item.get("recept_id") != oud_recept or item.get("geen") or not item.get("boodschappen", True)):
            await self._lijst_terug(item)  # wat dit vorige gerecht op de lijst zette, gaat er weer af
        if item.get("recept_id") != oud_recept or (item.get("boodschappen", True) and not oud_bs):
            item["op_lijst"] = False  # ander recept, of boodschappen later alsnog aangezet
        item.setdefault("boodschappen", True)
        item.pop("historie", None)
        if not item.get("uid"):
            # losse agenda-events op die dag (eerder handmatig gezet) vervangen, geen dubbele maaltijd
            await self._extern_weg([e["uid"] for e in self.extern if e["datum"] == datum and e.get("uid")])
        item.pop("mist_sinds", None)
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
        await self._lijst_terug(item)
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
        if "personen" in inst and "profielen" not in inst:
            # alleen namen opgegeven: bestaande profielen houden, nieuwe namen erbij
            bestaand = {p["naam"]: p for p in self.inst.get("profielen") or []}
            self.inst["profielen"] = [bestaand.get(n, {"naam": n}) for n in self.inst["personen"]]
        self.inst.update(schoon_menu(self.inst))
        self.inst["bronnen"] = schoon_bronnen(self.inst.get("bronnen"))
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
            self._sync_ts = time.time() - SYNC_INTERVAL + 30  # over 30 s opnieuw
            return
        if not events:
            # Lege agenda: bijna altijd 'nog niet geladen' (net na het opstarten). Dan niets concluderen —
            # anders zouden geplande maaltijden als 'verwijderd in de agenda' worden gezien.
            heeft_eigen = any(p.get("uid") and van.isoformat() <= p["datum"] < tot.isoformat() for p in self.s["planning"])
            if heeft_eigen or self.extern or not self._ooit_gelezen:
                self._sync_ts = 0.0  # bij de volgende vraag meteen opnieuw proberen
                return
        self._ooit_gelezen = True
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
                if item.pop("mist_sinds", None):
                    gewijzigd = True
                if d != item["datum"]:
                    andere = next((p for p in self.s["planning"] if p["datum"] == d and p["id"] != item["id"]), None)
                    if andere is None:
                        item["datum"] = d
                        gewijzigd = True
                if e.summary and e.summary != item.get("titel") and not item.get("recept_id"):
                    item["titel"] = e.summary
                    gewijzigd = True
            elif item.get("uid"):
                # Stond in de agenda maar is er nu niet: pas verwijderen als het een tijd lang zo blijft
                # (de agenda kan net na het opstarten nog half geladen zijn).
                if not item.get("mist_sinds"):
                    item["mist_sinds"] = time.time()
                    gewijzigd = True
                elif time.time() - item["mist_sinds"] >= VERWIJDER_NA:
                    self.s["planning"].remove(item)
                    gewijzigd = True
        if gewijzigd:
            self.save()

    # --- AI ---------------------------------------------------------------
    async def _vraag_ai(self, taak: str, instructie: str, attachments: list[dict] | None = None) -> Any:
        km = None if attachments else sleutel_en_model(self.hass)
        if km:
            # rechtstreeks naar Gemini: sneller en zonder de korte maximale antwoordlengte van de AI-taak
            key, model = km
            try:
                tekst = await gemini_stream(async_get_clientsession(self.hass), key, model, instructie,
                                            lambda _t: None, gedachten=False, timeout=180)
                res = parse_json(tekst)
                if res is not None:
                    return res
                _LOGGER.warning("%s: geen JSON van Gemini, via de AI-taak: %s", taak, tekst[:200])
            except GeminiNietBeschikbaar as err:
                _LOGGER.info("%s: Gemini direct lukte niet (%s), via de AI-taak", taak, err)
        ent = self.inst.get("ai") or next(iter(sorted(self.hass.states.async_entity_ids("ai_task"))), None)
        if not ent:
            raise ValueError("Geen AI-taak (ai_task) gevonden")
        for poging in range(3):
            try:
                data = {"task_name": taak, "instructions": instructie, "entity_id": ent}
                if attachments:
                    data["attachments"] = attachments
                res = await self.hass.services.async_call(
                    "ai_task", "generate_data", data, blocking=True, return_response=True,
                )
                return parse_json((res or {}).get("data"))
            except Exception as err:  # noqa: BLE001 - tijdelijke drukte bij de AI-dienst: kort wachten en opnieuw
                tijdelijk = any(x in str(err) for x in ("503", "UNAVAILABLE", "high demand", "429", "overloaded"))
                if not tijdelijk or poging == 2:
                    raise
                _LOGGER.info("AI-taak tijdelijk niet beschikbaar, opnieuw over %s s", 5 * (poging + 1))
                await asyncio.sleep(5 * (poging + 1))
        return None
