"""Dag voor dag plannen: één lichte vraag aan Gemini per dag, met filters en een keuze uit bronnen.

Pure functies (geen Home Assistant, geen netwerk), zodat ze los te testen zijn.

Waarom licht: Gemini kiest alleen uit lijsten die wij aanleveren (eigen recepten als R-nummers, gevonden receptlinks
als L-nummers) en geeft per optie alleen een nummer en een reden terug. Geen productlijst, geen nieuwe recepten
verzinnen. Het volledige recept (ingrediënten, bereiding, koppeling aan Grocy) komt pas bij 'Plan in', en dan van de
receptpagina zelf.
"""
from __future__ import annotations

from datetime import date
import math
import re
from typing import Any

from .menu import DAGEN, MAANDEN

BIBLIOTHEEK = "__bibliotheek__"  # chip 'Mijn bibliotheek' in de bronkeuze
SNEL_MINUTEN = 30
MAX_LINKS_PER_DAG = 120
MAX_RECEPTEN = 150

DEFAULT_FILTERS: dict[str, Any] = {"voorraad": True, "snel": False, "aanbieding": True, "bronnen": [BIBLIOTHEEK], "wens": ""}


def schoon_filters(f: Any, bron_namen: list[str]) -> dict:
    """Filters uit het scherm in een vaste vorm. Onbekende bronnen vallen weg; niets gekozen = bibliotheek."""
    f = f if isinstance(f, dict) else {}
    geldig = {n.lower(): n for n in bron_namen}
    bronnen: list[str] = []
    for b in f.get("bronnen") or []:
        b = str(b)
        if b == BIBLIOTHEEK:
            naam = BIBLIOTHEEK
        else:
            naam = geldig.get(b.lower())
        if naam and naam not in bronnen:
            bronnen.append(naam)
    return {
        "voorraad": bool(f.get("voorraad", DEFAULT_FILTERS["voorraad"])),
        "snel": bool(f.get("snel", DEFAULT_FILTERS["snel"])),
        "aanbieding": bool(f.get("aanbieding", DEFAULT_FILTERS["aanbieding"])),
        "bronnen": bronnen or [BIBLIOTHEEK],
        "wens": str(f.get("wens") or "").strip()[:300],
    }


def max_minuten(dag_minuten: Any, snel: bool) -> int:
    try:
        m = int(dag_minuten or 45)
    except (TypeError, ValueError):
        m = 45
    return min(m, SNEL_MINUTEN) if snel else m


def recepten_selectie(recepten: list[dict], filters: dict, herkomst_naam: dict[str, str | None]) -> list[dict]:
    """Welke eigen recepten mogen meedoen. Bibliotheek aan: alle. Alleen bronnen gekozen: de recepten die van die bronnen
    komen (die zijn al eens goedgekeurd). herkomst_naam: {recept_id: bronnaam of None}."""
    bronnen = {b.lower() for b in filters["bronnen"] if b != BIBLIOTHEEK}
    if BIBLIOTHEEK in filters["bronnen"]:
        return list(recepten)
    return [r for r in recepten if (herkomst_naam.get(r["id"]) or "").lower() in bronnen]


def zoekvraag(filters: dict, bijna_op: list[str], acties: list[str]) -> str:
    """Woorden om de receptlinks van een bron voor te sorteren (de wens, en wat op moet of in de aanbieding is)."""
    delen = [filters.get("wens") or ""]
    if filters.get("voorraad"):
        delen += bijna_op[:8]
    if filters.get("aanbieding"):
        delen += acties[:8]
    return " ".join(d for d in delen if d)


def recept_regel(code: str, r: dict) -> str:
    """Eén regel per eigen recept: kort, zodat de prompt klein blijft."""
    delen = [f"{code}. {r['naam']}"]
    if r.get("tijd"):
        delen.append(f"{r['tijd']} min")
    if r.get("vega"):
        delen.append("vega")
    if r.get("score"):
        delen.append(f"score {r['score']}")
    if r.get("feedback"):
        delen.append("feedback: " + ", ".join(r["feedback"]))
    if r.get("laatst"):
        delen.append(f"laatst gegeten {r['laatst']}")
    if r.get("bron"):
        delen.append(f"van {r['bron']}")
    if r.get("in_huis"):
        delen.append("in huis: " + ", ".join(r["in_huis"][:5]))
    if r.get("bijna_op"):
        delen.append("moet op: " + ", ".join(r["bijna_op"][:4]))
    if r.get("actie"):
        delen.append("in de aanbieding: " + ", ".join(r["actie"][:4]))
    return " | ".join(delen)


def opties_prompt(
    dag: dict,
    filters: dict,
    context: str,
    week: list[str],
    recepten: list[dict],
    links: list[dict],
    in_huis: list[str],
    bijna_op: list[str],
    acties: list[str],
    vandaag: date,
) -> str:
    """De vraag voor één dag. recepten: [{code, naam, tijd, vega, ...}], links: [{code, titel, bron}]."""
    minuten = max_minuten(dag.get("minuten"), filters["snel"])
    mee = ", ".join(dag.get("aanwezig") or []) or "niemand opgegeven"
    regels = [
        "Je bent de maaltijdadviseur van een gezin. Stel voor de avondmaaltijd van deze dag precies 3 verschillende "
        "gerechten voor. Kies ALLEEN uit de genummerde lijsten hieronder; verzin zelf geen gerechten.",
        'Antwoord ALLEEN met een JSON-lijst van 3 objecten: [{"keuze": "R3" of "L12", "reden": str}]',
        "- R-nummers zijn recepten uit de eigen receptenbibliotheek. L-nummers zijn recepten van receptsites (soms "
        "Engels); van de meeste staan tijd, vega en de belangrijkste ingrediënten erbij, van sommige alleen de titel.",
        "- reden: één korte Nederlandse zin waarom dit past. Noem concreet wat in huis is of in de aanbieding als je "
        "daarop kiest.",
        "- Houd je strikt aan dieet en allergieën van wie er mee-eet. Eet er iemand vegetarisch mee, kies dan iets "
        "vegetarisch of iets met een duidelijke vega-variant (zeg dat in de reden).",
        f"- Bereidingstijd hooguit {minuten} minuten" + (
            " (er moet snel gekookt worden: kies alleen echt snelle gerechten, en recepten met feedback 'lekker_snel' "
            "hebben de voorkeur)." if filters["snel"] else "."),
        "- Niets wat 'niet_lekker' is, liever niets met 'teveel_werk', en niet iets wat deze week al op het menu staat "
        "of de laatste weken gegeten is.",
        "- Maak de 3 opties echt verschillend (andere keuken of hoofdingrediënt).",
    ]
    if filters["voorraad"]:
        regels.append("- Kijk naar de voorraad: kies bij voorkeur gerechten die gebruiken wat in huis is, vooral wat op moet.")
    if filters["aanbieding"]:
        regels.append("- Kijk naar de aanbiedingen: kies bij voorkeur gerechten met producten die nu in de aanbieding zijn.")
    if recepten and links:
        regels.append("- Er zijn eigen recepten én recepten van receptsites: kies van allebei minstens één, tenzij er "
                      "echt niets past.")
    regels += [
        "",
        f"Vandaag: {DAGEN[vandaag.weekday()]} {vandaag.isoformat()}. Seizoen: {MAANDEN[vandaag.month - 1]}.",
        f"Deze dag: {dag['dag']} {dag['datum']}. Eten mee: {mee}."
        + (f" Kookt: {dag['kookt']}." if dag.get("kookt") else "")
        + (f" Kook {dag['vriezer']} extra porties voor de vriezer (moet in te vriezen zijn)." if dag.get("vriezer") else "")
        + (f" Afspraak: {dag['notitie']}." if dag.get("notitie") else ""),
        f"Wens: {filters['wens'] or '-'}",
        f"Al op het menu deze week: {', '.join(week) or '-'}",
        "",
        context,
        "",
    ]
    if filters["voorraad"]:
        regels.append(f"Moet op (gaat bijna over de datum): {', '.join(bijna_op) or '-'}")
        regels.append(f"In huis: {', '.join(in_huis[:80]) or '-'}")
    if filters["aanbieding"]:
        regels.append(f"In de aanbieding: {', '.join(acties[:30]) or '-'}")
    if recepten:
        regels += ["", "Eigen recepten:", *[recept_regel(r["code"], r) for r in recepten]]
    if links:
        regels += ["", "Recepten van receptsites:", *[l.get("regel") or f"{l['code']}. {l['titel']} ({l['bron']})" for l in links]]
    return "\n".join(regels)


def opties_toepassen(res: Any, codes: dict[str, dict], n: int = 3) -> list[dict]:
    """Antwoord omzetten naar [{code, reden, ...item}]; onbekende nummers en dubbelen vallen weg."""
    uit, gezien = [], set()
    lijst = res if isinstance(res, list) else (res.get("opties") if isinstance(res, dict) else None) or []
    for x in lijst:
        if isinstance(x, str):
            x = {"keuze": x}
        if not isinstance(x, dict):
            continue
        code = re.sub(r"\s+", "", str(x.get("keuze") or x.get("code") or "")).upper()
        if code in codes and code not in gezien:
            gezien.add(code)
            uit.append({**codes[code], "code": code, "reden": str(x.get("reden") or "").strip()})
    return uit[:n]


# ----------------------------------------------------------------- voorraad reserveren en lijstregels
def reserveringen(planning: list[dict], vandaag: str, behalve: str | None = None) -> dict[int, float]:
    """Wat andere geplande maaltijden (vanaf vandaag) al uit de voorraad 'gereserveerd' hebben: {product_id: aantal}."""
    tot: dict[int, float] = {}
    for p in planning:
        if p.get("id") == behalve or p.get("datum", "") < vandaag:
            continue
        for pid, n in (p.get("uit_voorraad") or {}).items():
            tot[int(pid)] = tot.get(int(pid), 0.0) + float(n or 0)
    return tot


def verdeel(nodig: dict[int, float], voorraad: dict[int, float], gereserveerd: dict[int, float]) -> tuple[dict[int, float], dict[int, float]]:
    """Per product: hoeveel uit de voorraad (wat nog niet door een andere maaltijd is gereserveerd) en hoeveel kopen
    (hele verpakkingen). Geeft (uit_voorraad, kopen)."""
    uit_voorraad: dict[int, float] = {}
    kopen: dict[int, float] = {}
    for pid, n in nodig.items():
        vrij = max(0.0, float(voorraad.get(pid, 0.0)) - float(gereserveerd.get(pid, 0.0)))
        neem = min(float(n), vrij)
        if neem > 0:
            uit_voorraad[pid] = round(neem, 3)
        rest = float(n) - neem
        if rest > 1e-9:
            kopen[pid] = float(max(1, math.ceil(rest - 1e-9)))
    return uit_voorraad, kopen
