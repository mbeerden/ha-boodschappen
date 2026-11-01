"""Weekmenu-advies: profielen, wie eet er mee, dagregels, feedback, voorraad/aanbiedingen en de AI-prompt.

Pure functies (geen Home Assistant), zodat ze los te testen zijn.
"""
from __future__ import annotations

import copy
from datetime import date, timedelta
import json
import math
import re
from typing import Any

DAGEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
MAANDEN = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober",
           "november", "december"]

# soort dag: wij koken / eten uit de vriezer / iemand anders kookt / niet thuis
SOORTEN = {"koken": "wij koken", "vriezer": "uit de vriezer", "anders": "iemand anders kookt", "niet": "niet thuis"}
VASTE_TITEL = {"vriezer": "Uit de vriezer", "anders": "Iemand anders kookt", "niet": "Geen maaltijd"}

FEEDBACK = {
    "wel_lekker": "Wel lekker",
    "lekker_snel": "Lekker snel",
    "niet_lekker": "Niet lekker",
    "teveel_werk": "Teveel werk",
}
ROLLEN = ("favoriet", "slim", "nieuw")

DEFAULT_WEEKDAG = {"soort": "koken", "kookt": "", "minuten": 45, "vriezer": 0, "notitie": ""}
DEFAULT_MENU_INSTELLINGEN: dict[str, Any] = {
    "profielen": [],
    "weekdagen": [dict(DEFAULT_WEEKDAG) for _ in range(7)],
    "stijl": {"apparaten": [], "voorkeuren": ""},
    "afwisseling": {"vlees_max": 3, "vis_min": 1, "herhaal_weken": 3, "vleesvervanger": True},
}


def default_menu() -> dict:
    return copy.deepcopy(DEFAULT_MENU_INSTELLINGEN)


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def norm(s: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower())).strip()


def feedback_key(recept_id: str | None, titel: str | None) -> str:
    return recept_id or f"titel:{norm(titel)}"


# ----------------------------------------------------------------- instellingen normaliseren
def _int(v: Any, d: int) -> int:
    try:
        return int(float(str(v).replace(",", ".")))
    except (TypeError, ValueError):
        return d


def _dagen(v: Any) -> list[int]:
    return sorted({_int(x, -1) for x in (v or []) if 0 <= _int(x, -1) <= 6})


def schoon_profiel(p: Any) -> dict | None:
    if isinstance(p, str):
        p = {"naam": p}
    if not isinstance(p, dict) or not str(p.get("naam") or "").strip():
        return None
    pat = p.get("patroon") or {}
    soort = pat.get("soort") if pat.get("soort") in ("altijd", "dagen", "omdeweek") else "altijd"
    anker = None
    if pat.get("anker"):
        try:
            anker = week_start(date.fromisoformat(str(pat["anker"])[:10])).isoformat()
        except ValueError:
            anker = None
    return {
        "naam": str(p["naam"]).strip(),
        "kind": bool(p.get("kind")),
        "dieet": str(p.get("dieet") or "").strip(),
        "niet_eten": str(p.get("niet_eten") or "").strip(),
        "allergie": str(p.get("allergie") or "").strip(),
        "lievelingskost": str(p.get("lievelingskost") or "").strip(),
        "patroon": {
            "soort": soort,
            "dagen": _dagen(pat.get("dagen")) if "dagen" in pat else list(range(7)),
            "dagen_b": _dagen(pat.get("dagen_b")),
            "anker": anker,
        },
    }


def schoon_weekdagen(v: Any) -> list[dict]:
    uit = []
    for i in range(7):
        w = (v[i] if isinstance(v, list) and i < len(v) and isinstance(v[i], dict) else {}) or {}
        uit.append({
            "soort": w.get("soort") if w.get("soort") in SOORTEN else "koken",
            "kookt": str(w.get("kookt") or "").strip(),
            "minuten": max(5, _int(w.get("minuten"), 45)),
            "vriezer": max(0, _int(w.get("vriezer"), 0)),
            "notitie": str(w.get("notitie") or "").strip(),
        })
    return uit


def schoon_menu(inst: dict) -> dict:
    """Zet de menu-instellingen in een vaste vorm (ook voor oude opslag met alleen namen in 'personen')."""
    prof = [x for x in (schoon_profiel(p) for p in (inst.get("profielen") or [])) if x]
    if not prof and inst.get("personen"):
        namen = inst["personen"] if isinstance(inst["personen"], list) else str(inst["personen"]).split(",")
        prof = [x for x in (schoon_profiel(str(n).strip()) for n in namen) if x]
    stijl = inst.get("stijl") or {}
    app = stijl.get("apparaten") or []
    if isinstance(app, str):
        app = app.split(",")
    afw = {**DEFAULT_MENU_INSTELLINGEN["afwisseling"], **(inst.get("afwisseling") or {})}
    return {
        "profielen": prof,
        "personen": [p["naam"] for p in prof],
        "weekdagen": schoon_weekdagen(inst.get("weekdagen")),
        "stijl": {"apparaten": [str(a).strip() for a in app if str(a).strip()], "voorkeuren": str(stijl.get("voorkeuren") or "").strip()},
        "afwisseling": {
            "vlees_max": max(0, _int(afw.get("vlees_max"), 3)),
            "vis_min": max(0, _int(afw.get("vis_min"), 1)),
            "herhaal_weken": max(0, _int(afw.get("herhaal_weken"), 3)),
            "vleesvervanger": bool(afw.get("vleesvervanger")),
        },
    }


# ----------------------------------------------------------------- wie eet er mee
def eet_mee(profiel: dict, d: date) -> bool:
    pat = profiel.get("patroon") or {}
    soort = pat.get("soort", "altijd")
    if soort == "altijd":
        return True
    if soort == "dagen":
        return d.weekday() in (pat.get("dagen") or [])
    # om de week: de week van het anker is week A ('dagen'), de week erna week B ('dagen_b')
    anker = date.fromisoformat(pat["anker"]) if pat.get("anker") else week_start(d)
    weken = (week_start(d) - anker).days // 7
    return d.weekday() in ((pat.get("dagen") or []) if weken % 2 == 0 else (pat.get("dagen_b") or []))


def aanwezig_op(profielen: list[dict], d: date) -> list[str]:
    return [p["naam"] for p in profielen if eet_mee(p, d)]


def porties_voor(profielen: list[dict], namen: list[str]) -> int:
    """Porties voor wie er mee-eet: volwassene 1, kind een halve (afgerond naar boven, minimaal 1)."""
    per = {p["naam"]: (0.5 if p.get("kind") else 1.0) for p in profielen}
    return max(1, math.ceil(sum(per.get(n, 1.0) for n in namen) - 1e-9))


def dag_info(menu: dict, d: date) -> dict:
    """Standaard voor een datum uit de dagregels en profielen."""
    w = menu["weekdagen"][d.weekday()]
    aanwezig = aanwezig_op(menu["profielen"], d)
    return {
        "datum": d.isoformat(), "dag": DAGEN[d.weekday()], "soort": w["soort"], "kookt": w["kookt"],
        "minuten": w["minuten"], "vriezer": w["vriezer"], "notitie": w["notitie"], "aanwezig": aanwezig,
    }


# ----------------------------------------------------------------- feedback
def feedback_lijsten(feedback: dict) -> dict[str, list[dict]]:
    """{soort: [{key, recept_id, titel}]} uit de opgeslagen feedback."""
    uit: dict[str, list[dict]] = {k: [] for k in FEEDBACK}
    for key, f in (feedback or {}).items():
        for soort in f.get("soorten") or {}:
            if soort in uit:
                uit[soort].append({"key": key, "recept_id": f.get("recept_id"), "titel": f.get("titel")})
    return uit


def is_afgewezen(feedback: dict, recept_id: str | None, titel: str | None) -> bool:
    f = (feedback or {}).get(feedback_key(recept_id, titel)) or {}
    if "niet_lekker" in (f.get("soorten") or {}):
        return True
    if titel:
        n = norm(titel)
        return any(norm(x.get("titel")) == n and "niet_lekker" in (x.get("soorten") or {}) for x in (feedback or {}).values())
    return False


# ----------------------------------------------------------------- voorraad en aanbiedingen
def voorraad_overzicht(stock: list[dict], vandaag: date, dagen_bijna: int = 4) -> tuple[dict[int, float], set[int]]:
    """({product_id: aantal in huis}, {product_ids die binnen een paar dagen over de datum gaan})."""
    hoeveel: dict[int, float] = {}
    bijna: set[int] = set()
    grens = (vandaag + timedelta(days=dagen_bijna)).isoformat()
    for s in stock or []:
        try:
            pid = int(s.get("product_id"))
            n = float(s.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        hoeveel[pid] = n
        bbd = str(s.get("best_before_date") or "")[:10]
        if bbd and bbd < "2999" and bbd <= grens:
            bijna.add(pid)
    return hoeveel, bijna


def labels_voor(recept: dict | None, voorraad: dict[int, float], bijna: set[int], acties: dict[int, dict],
                productnamen: dict[int, str]) -> dict:
    """Wat dit gerecht gebruikt uit de aanbiedingen en de voorraad (zelf berekend, niet door de AI)."""
    uit = {"actie": [], "in_huis": [], "bijna_op": []}
    if not recept:
        return uit
    for ing in recept.get("ingredienten") or []:
        pid = ing.get("product_id")
        if not pid or ing.get("basis"):
            continue
        pid = int(pid)
        naam = productnamen.get(pid) or ing.get("tekst") or "?"
        if pid in acties:
            a = acties[pid]
            tekst = f"{naam} ({a.get('actie') or 'actie'}, {a.get('winkel') or ''})".replace(", )", ")")
            if tekst not in uit["actie"]:
                uit["actie"].append(tekst)
        if pid in bijna and naam not in uit["bijna_op"]:
            uit["bijna_op"].append(naam)
        elif pid in voorraad and naam not in uit["in_huis"]:
            uit["in_huis"].append(naam)
    return uit


# ----------------------------------------------------------------- de prompt
def context_tekst(menu: dict, overig: str, feedback: dict, vandaag: date) -> str:
    """Vaste achtergrond voor elk voorstel: wie zijn we, kookstijl, afwisseling, feedback, overige afspraken."""
    regels = ["Gezin:"]
    for p in menu["profielen"]:
        delen = [p["naam"] + (" (kind)" if p.get("kind") else "")]
        if p.get("dieet"):
            delen.append(f"dieet: {p['dieet']}")
        if p.get("allergie"):
            delen.append(f"ALLERGIE (nooit gebruiken): {p['allergie']}")
        if p.get("niet_eten"):
            delen.append(f"eet niet: {p['niet_eten']}")
        if p.get("lievelingskost"):
            delen.append(f"lievelingskost: {p['lievelingskost']}")
        regels.append("- " + "; ".join(delen))
    st = menu["stijl"]
    if st.get("apparaten"):
        regels.append(f"Apparaten in de keuken: {', '.join(st['apparaten'])} (kies gerust gerechten die daarmee makkelijk gaan).")
    if st.get("voorkeuren"):
        regels.append(f"Kookstijl en voorkeuren: {st['voorkeuren']}")
    a = menu["afwisseling"]
    regels.append(
        f"Afwisseling over de hele periode: hooguit {a['vlees_max']}x vlees per week, minstens {a['vis_min']}x vis per week, "
        f"een gerecht pas na {a['herhaal_weken']} weken opnieuw (zie laatst_gegeten), niet twee dagen achter elkaar dezelfde keuken."
        + (" Bij vlees mag een vleesvervanger als vega-variant ernaast." if a.get("vleesvervanger") else "")
    )
    regels.append(f"Seizoen: het is {MAANDEN[vandaag.month - 1]}; kies groente van het seizoen.")
    fb = feedback_lijsten(feedback)
    for soort, uitleg in (
        ("wel_lekker", "Vinden we lekker (vaker voorstellen)"),
        ("lekker_snel", "Lekker én snel (goed voor dagen met weinig tijd)"),
        ("teveel_werk", "Vonden we te veel werk (niet op dagen met weinig tijd, en liever iets simpelers)"),
        ("niet_lekker", "Vinden we NIET lekker (nooit voorstellen, ook geen variant)"),
    ):
        if fb[soort]:
            regels.append(f"{uitleg}: {', '.join(sorted({x['titel'] for x in fb[soort] if x.get('titel')}))}")
    if overig.strip():
        regels.append(f"Overige afspraken:\n{overig.strip()}")
    return "\n".join(regels)


def dag_regel(d: dict, profielen: list[dict]) -> str:
    kind = {p["naam"] for p in profielen if p.get("kind")}
    r = f"- {d['dag'].upper()} {d['datum']}"
    if d.get("vast_titel"):
        return r + f" — staat al vast: {d['vast_titel']}"
    if d.get("soort") != "koken":
        return r + f" — {SOORTEN.get(d.get('soort'), d.get('soort'))}" + (f" ({d['notitie']})" if d.get("notitie") else "")
    delen = ["wij koken"]
    if d.get("kookt"):
        delen.append(f"kookt: {d['kookt']}")
    delen.append(f"max {d.get('minuten') or 45} min bereiding")
    mee = [n + (" (kind)" if n in kind else "") for n in d.get("aanwezig") or []]
    delen.append(f"eten mee: {', '.join(mee) or 'niemand opgegeven'}")
    if d.get("vriezer"):
        delen.append(f"kook {d['vriezer']} extra porties voor de vriezer (geschikt om in te vriezen)")
    if d.get("notitie"):
        delen.append(f"afspraak: {d['notitie']}")
    if d.get("agenda"):
        delen.append(f"in de agenda staat al als idee: {d['agenda']}")
    if d.get("wens"):
        delen.append(f"wens voor deze dag: {d['wens']}")
    return r + " — " + "; ".join(delen)


def voorstel_prompt(
    te_plannen: list[dict],
    overige: list[dict],
    profielen: list[dict],
    context: str,
    wens: str,
    recepten: list[dict],
    producten: dict[int, str],
    voorraad: dict[int, float],
    bijna: set[int],
    acties: dict[int, dict],
    vandaag: date,
    correctie: str = "",
) -> str:
    in_huis = [producten.get(pid, str(pid)) for pid in voorraad if pid in producten]
    bijna_op = [producten.get(pid, str(pid)) for pid in bijna if pid in producten]
    actie_tekst = [f"{producten.get(pid, a.get('product') or pid)}: {a.get('actie') or ''} bij {a.get('winkel') or '?'}"
                   for pid, a in acties.items()]
    return (
        "Je bent de maaltijdadviseur van een gezin. Geef voor ELKE dag hieronder precies 3 opties voor de avondmaaltijd.\n"
        "Antwoord ALLEEN met een JSON-lijst, één object per dag, in dezelfde volgorde:\n"
        '[{"datum": "YYYY-MM-DD", "dag": "maandag", "opties": [\n'
        '  {"rol": "favoriet"|"slim"|"nieuw", "recept_id": str|null, "titel": str, "reden": str,\n'
        '   "nieuw_recept": null | {"naam": str, "porties": int, "tijd": int, "vega": bool, "tags": [str],\n'
        '     "ingredienten": [{"tekst": str, "product_id": int|null, "aantal": number, "basis": bool}], "stappen": [str]}}]}]\n'
        "De 3 opties per dag, in deze volgorde:\n"
        "1. rol 'favoriet': een recept uit de receptenlijst dat goed beoordeeld of als lekker gemarkeerd is.\n"
        "2. rol 'slim': een recept uit de receptenlijst dat het meest gebruikt van wat in de aanbieding is en van wat al "
        "in huis is (vooral wat bijna over de datum gaat).\n"
        "3. rol 'nieuw': een NIEUW recept om eens te proberen (recept_id null, nieuw_recept volledig ingevuld). "
        "Ingrediënten altijd met hoeveelheid in de tekst; product_id = id uit de productlijst dat je ervoor koopt (null "
        "als er niets past); aantal = verpakkingen/stuks voor de porties; basis=true voor zout, peper, olie, water, "
        "gedroogde kruiden. Ook hier gebruik je bij voorkeur aanbiedingen en voorraad.\n"
        "Regels:\n"
        "- Neem datum én weekdag precies over. Elke dag heeft eigen regels: wie eet er mee, wie kookt, hoeveel tijd.\n"
        "- Houd je strikt aan dieet en allergieën van wie die dag mee-eet. Eet iemand vegetarisch mee, dan is het "
        "gerecht vega of heeft het een duidelijke vega-variant (zeg dat in de reden).\n"
        "- Respecteer de maximale bereidingstijd van de dag. Met kinderen aan tafel: iets wat kinderen ook eten.\n"
        "- Niet dezelfde optie op meerdere dagen, en houd rekening met de afwisseling over de hele periode.\n"
        "- Als de receptenlijst te weinig passends heeft, mag 'favoriet' of 'slim' ook een nieuw recept zijn.\n"
        "- reden: één korte zin, noem concreet welke aanbieding of voorraad je gebruikt als dat zo is.\n\n"
        f"Vandaag: {DAGEN[vandaag.weekday()]} {vandaag.isoformat()}\n\n"
        f"{context}\n\n"
        f"Wens voor deze periode: {wens or '-'}\n\n"
        f"Te plannen dagen:\n{chr(10).join(dag_regel(d, profielen) for d in te_plannen)}\n\n"
        + (f"Overige dagen in de periode (ter info, niet plannen):\n{chr(10).join(dag_regel(d, profielen) for d in overige)}\n\n" if overige else "")
        + (f"LET OP, correctie op je vorige antwoord: {correctie}\n\n" if correctie else "")
        + f"Nu in de aanbieding: {'; '.join(actie_tekst) or '-'}\n"
        f"Gaat bijna over de datum (eerst opmaken): {', '.join(sorted(bijna_op)) or '-'}\n"
        f"In huis: {', '.join(sorted(in_huis)) or '-'}\n\n"
        f"Recepten: {json.dumps(recepten, ensure_ascii=False)}\n\n"
        f"Producten (id: naam): {json.dumps(producten, ensure_ascii=False)}"
    )


def controleer_antwoord(res: Any, dagen: list[dict]) -> str:
    """Correctietekst als datums/weekdagen of het aantal opties niet kloppen ('' = goed)."""
    if not isinstance(res, list):
        return "antwoord met een JSON-lijst"
    juist = {d["datum"]: d["dag"] for d in dagen}
    fouten = []
    gezien = set()
    for x in res:
        if not isinstance(x, dict):
            continue
        iso = str(x.get("datum") or "")[:10]
        gezien.add(iso)
        dag = str(x.get("dag") or "").strip().lower()
        if iso in juist and dag and dag != juist[iso]:
            fouten.append(f"{iso} is een {juist[iso]}, niet een {dag}")
        if iso in juist and len([o for o in (x.get("opties") or []) if isinstance(o, dict)]) < 3:
            fouten.append(f"geef voor {iso} precies 3 opties")
    if gezien != set(juist):
        fouten.append("geef precies de gevraagde datums terug")
    return "; ".join(fouten)
