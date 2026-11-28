"""Index van receptbronnen: per bron alle receptlinks en, op de achtergrond, de gegevens van elk recept.

Pure functies (geen Home Assistant, geen netwerk), zodat ze los te testen zijn. Het ophalen zelf staat in eten.py.

Waarom: zonder index las de planner bij elke vraag de sitemaps opnieuw (na een herstart tientallen seconden per
bron) en zag Gemini van een bronrecept alleen de titel uit de link. Met de index liggen de links klaar, en weten we per
recept de tijd, of het vega is en welke ingrediënten erin zitten. Daarmee kunnen we vooraf filteren (snel) en sorteren
(wens, voorraad, aanbiedingen), en krijgt Gemini minder maar betere kandidaten.

Per recept bewaren we alleen een samenvatting (een paar honderd bytes), niet de bereiding: die komt pas bij 'Plan in'
van de pagina zelf, zodat de index klein blijft.
"""
from __future__ import annotations

import random
import re
from typing import Any

from .recept import _kort, _norm, _stam, recept_uit_jsonld, recept_van_jsonld

INDEX_MAX = 3000                 # hooguit zoveel recepten per bron met gegevens
LINKS_MAX = 12000                # hooguit zoveel links per bron bewaren
LINKS_VERNIEUW = 7 * 86400       # linklijst eens per week bijwerken
FOUT_OPNIEUW = 30 * 86400        # pagina die niet lukte: na een maand nog eens proberen
PAUZE = 2.0                      # seconden tussen twee pagina's van dezelfde site (netjes blijven)
MAX_FOUTEN_OP_RIJ = 12           # zoveel mislukte pagina's achter elkaar: deze ronde stoppen met die bron
GEEN_RECEPT = "x"                # markering in de index: pagina geprobeerd, maar geen recept

# Vlees en vis (Nederlands en Engels): staat een van deze woorden in de ingrediënten, dan is het niet vega.
VLEES = re.compile(
    r"(?i)\b(kip\w*|chicken|kalkoen|turkey|rund\w*|beef|gehakt|mince[d]?|varken\w*|pork|spek\w*|bacon|pancetta|"
    r"ham|chorizo|salami|worst\w*|sausage\w*|lam(?:s\w*|svlees)?|lamb|kalf\w*|veal|eend|duck|biefstuk|steak|"
    r"vis|fish|zalm|salmon|tonijn|tuna|kabeljauw|cod|garnal\w*|prawn\w*|shrimp\w*|mossel\w*|mussel\w*|ansjovis|"
    r"anchov\w*|inktvis|squid|krab|crab|kreeft|lobster|makreel|mackerel|sardine\w*|haring|herring|gelatine|"
    r"vissaus|fish sauce|oesters?|oyster\w*|schelp\w*|clams?|scallops?|coquilles?|forel|trout|zeebaars|sea bass|"
    r"heek|hake|pollock|koolvis|merguez|prosciutto|guanciale|nduja|'nduja)\b"
)
# Hoeveelheden en eenheden vooraan een ingrediënt (om het kort te maken)
HOEVEEL = re.compile(
    r"(?i)^[\d\s½¼¾⅓⅔/.,–-]*(?:(?:g|gr|gram|kg|ml|cl|dl|l|liter|el|tl|tbsp|tsp|tablespoons?|teaspoons?|cups?|oz|lb|"
    r"x|stuks?|blik(?:je)?|tins?|cans?|teentjes?|cloves?|handful|handje|snufje|pinch|bunch|bosje|takjes?|sprigs?|"
    r"plakjes?|slices?|large|medium|small|grote?|kleine?|middelgrote?)\b\.?\s*)*(?:of\s+|van\s+)?"
)


# ----------------------------------------------------------------- een recept samenvatten
def ingredient_kort(tekst: str) -> str:
    """'2 tbsp olive oil, plus extra' → 'olive oil'. Kort, zodat de prompt klein blijft."""
    t = re.sub(r"\([^)]*\)", "", str(tekst or ""))
    t = HOEVEEL.sub("", t.strip())
    t = re.split(r"[,;]| - | – ", t, maxsplit=1)[0]
    return re.sub(r"\s+", " ", t).strip(" .:-")[:40]


def vega_gok(ld: dict, ingredienten: list[str]) -> bool | None:
    """Vega volgens de site (suitableForDiet), anders: geen vlees of vis in de ingrediënten. None als we het niet weten."""
    dieet = ld.get("suitableForDiet")
    dieet = " ".join(dieet) if isinstance(dieet, list) else str(dieet or "")
    if re.search(r"(?i)vegetarian|vegan", dieet):
        return True
    if not ingredienten:
        return None
    return not any(VLEES.search(i) for i in ingredienten)


def index_item(page: str, pagina_tekst: str = "") -> dict | None:
    """Samenvatting van een receptpagina, of None als er geen schema.org-recept op staat.

    {t: titel, tijd: min|None, p: porties|None, v: vega|None, i: [korte ingrediënten], f: foto-url|None}
    """
    ld = recept_uit_jsonld(page or "")
    if not ld:
        return None
    basis = recept_van_jsonld(ld, pagina_tekst)
    if not basis["naam"] or not basis["ingredienten"]:
        return None
    foto = ld.get("image")
    if isinstance(foto, list):
        foto = foto[0] if foto else None
    if isinstance(foto, dict):
        foto = foto.get("url")
    kort = [k for k in dict.fromkeys(ingredient_kort(i) for i in basis["ingredienten"]) if k]
    return {
        "t": basis["naam"][:120],
        "tijd": basis["tijd"],
        "p": basis["porties"],
        "v": vega_gok(ld, basis["ingredienten"]),
        "i": kort[:18],
        "f": str(foto)[:300] if foto else None,
    }


# ----------------------------------------------------------------- zoeken en kiezen
def _woorden(tekst: str) -> set[str]:
    return {_stam(w) for w in _kort(_norm(tekst)).split() if len(w) >= 3}


def namen_in(item: dict, namen: list[str]) -> list[str]:
    """Welke productnamen (bijv. 'Courgette', 'Rode linzen') in de ingrediënten van dit recept voorkomen."""
    ingr = " ".join(item.get("i") or [])
    if not ingr:
        return []
    stammen = _woorden(ingr)
    platte = _kort(_norm(ingr))
    uit = []
    for naam in namen:
        ws = [w for w in _kort(_norm(naam)).split() if len(w) >= 4]
        if ws and all(_stam(w) in stammen or w in platte for w in ws):
            uit.append(naam)
    return uit


def score_item(titel: str, item: dict | None, woorden: list[str]) -> int:
    """Hoeveel zoekwoorden in de titel of de ingrediënten staan (titel telt dubbel)."""
    t = _woorden(titel)
    i = _woorden(" ".join((item or {}).get("i") or []))
    n = 0
    for w in woorden:
        s = _stam(_kort(w))
        if s in t:
            n += 2
        elif s in i:
            n += 1
    return n


def is_recept(item: Any) -> bool:
    return isinstance(item, dict) and bool(item.get("t"))


def index_kandidaten(
    links: list[str],
    details: dict[str, Any],
    woorden: list[str],
    klaar: set[str],
    sleutel,
    max_n: int,
    max_tijd: int | None = None,
    bonus: list[str] | None = None,
    zaad: int | None = None,
) -> list[str]:
    """Links om aan Gemini voor te leggen, met hulp van de index.

    - pagina's waarvan we weten dat het geen recept is vallen af, net als wat al in de bibliotheek staat (klaar);
    - met max_tijd (lekker snel) vallen recepten af die bekend langer duren;
    - eerst wat bij de zoekwoorden past (titel of ingrediënten) of producten gebruikt uit bonus (voorraad/aanbiedingen),
      dan een willekeurige greep, met recepten waarvan we de gegevens kennen voorop.
    """
    rng = random.Random(zaad)
    bonus = bonus or []
    passend: list[tuple[int, str]] = []
    bekend: list[str] = []
    onbekend: list[str] = []
    for u in links:
        d = details.get(u)
        if d == GEEN_RECEPT or sleutel(u) in klaar:
            continue
        if is_recept(d):
            if max_tijd and d.get("tijd") and d["tijd"] > max_tijd:
                continue
            s = score_item(d["t"], d, woorden) + 2 * len(namen_in(d, bonus))
            (passend.append((s, u)) if s > 0 else bekend.append(u))
        else:
            s = score_item(titel_slug(u), None, woorden)
            (passend.append((s, u)) if s > 0 else onbekend.append(u))
    rng.shuffle(passend)
    passend.sort(key=lambda x: -x[0])
    rng.shuffle(bekend)
    rng.shuffle(onbekend)
    eerst = [u for _, u in passend[: max(1, max_n * 2 // 3)]] if passend else []
    rest = [u for _, u in passend[len(eerst):]] + bekend + onbekend
    return (eerst + rest)[:max_n]


def titel_slug(url: str) -> str:
    slug = re.sub(r"(?i)\.(html?|php)$", "", url.rstrip("/").rsplit("/", 1)[-1])
    return re.sub(r"[-_]+", " ", slug)


def link_regel(code: str, titel: str, bron: str, item: dict | None) -> str:
    """Eén regel per bronrecept in de prompt: met tijd, vega en de belangrijkste ingrediënten als we die kennen."""
    delen = [f"{code}. {titel} ({bron})"]
    if is_recept(item):
        if item.get("tijd"):
            delen.append(f"{item['tijd']} min")
        if item.get("v") is True:
            delen.append("vega")
        elif item.get("v") is False:
            delen.append("met vlees/vis")
        if item.get("i"):
            delen.append("ingrediënten: " + ", ".join(item["i"][:10]))
    return " | ".join(delen)


def te_indexeren(links: list[str], details: dict[str, Any], nu: float, max_recepten: int = INDEX_MAX) -> list[str]:
    """Welke links nog opgehaald moeten worden (in volgorde), tot er max_recepten recepten in de index staan."""
    recepten = sum(1 for d in details.values() if is_recept(d))
    ruimte = max_recepten - recepten
    if ruimte <= 0:
        return []
    uit = []
    for u in links:
        d = details.get(u)
        if d is None or (isinstance(d, dict) and d.get("fout") and nu - d.get("ts", 0) > FOUT_OPNIEUW):
            uit.append(u)
    # er zitten ook pagina's tussen die geen recept blijken; neem wat ruimer
    return uit[: ruimte * 2]


def status(entry: dict | None) -> dict:
    """Samenvatting voor het paneel."""
    if not entry:
        return {"links": 0, "recepten": 0, "geen": 0, "fout": 0, "ts": None, "klaar": False}
    d = entry.get("d") or {}
    return {
        "links": len(entry.get("links") or []),
        "recepten": sum(1 for x in d.values() if is_recept(x)),
        "geen": sum(1 for x in d.values() if x == GEEN_RECEPT),
        "fout": sum(1 for x in d.values() if isinstance(x, dict) and x.get("fout")),
        "ts": entry.get("ts"),
        "klaar": bool(entry.get("klaar")),
        "bezig": entry.get("bezig"),
        "melding": entry.get("melding"),
    }
