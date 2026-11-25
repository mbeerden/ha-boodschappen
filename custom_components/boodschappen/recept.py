"""Recepten van een bron: letterlijk overnemen, kwaliteit controleren en de herkomst bepalen.

Pure functies (geen Home Assistant), zodat ze los te testen zijn.

Uitgangspunt: een recept komt van een kok, niet van de AI. Bij het importeren worden ingrediënten en
bereiding letterlijk uit de bron overgenomen; de AI koppelt alleen aan producten (en vertaalt zo nodig).
"""
from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urlparse

# De bronnen waaruit het gezin recepten wil hebben. 'pad' beperkt een bron tot een deel van een site.
DEFAULT_BRONNEN: list[dict[str, Any]] = [
    {"naam": "Ottolenghi", "domein": "ottolenghi.co.uk", "pad": "/pages/recipes", "type": "website", "aan": True, "notitie": ""},
    {"naam": "Yvette van Boven", "domein": "vpro.nl", "pad": "/koken-met-van-boven", "type": "website", "aan": True, "notitie": ""},
    {"naam": "Uit Paulines Keuken", "domein": "uitpaulineskeuken.nl", "pad": "", "type": "website", "aan": True, "notitie": ""},
    {"naam": "Brenda Kookt", "domein": "brendakookt.nl", "pad": "", "type": "website", "aan": True, "notitie": ""},
]
BRON_TYPES = ("website", "boek", "anders")

# Smaakmakers: kruiden, specerijen, zout/bouillon, zuur en kant-en-klare smaakbasis.
SMAAK = re.compile(
    r"(?i)\b(zout|zeezout|peper|kruid\w*|oregano|basilicum|tijm|rozemarijn|laurier\w*|peterselie|koriander\w*|dille|"
    r"bieslook|munt|salie|dragon|kervel|paprikapoeder|komijn\w*|kerrie\w*|kurkuma|kaneel|nootmuskaat|foelie|kruidnagel\w*|"
    r"chili\w*|cayenne\w*|gember\w*|knoflook\w*|sambal|harissa|ras el hanout|garam masala|za.?atar|sumak|sumac|"
    r"bouillon\w*|fond|ketjap|sojasaus|soja|miso|vissaus|oestersaus|worcester\w*|mosterd|azijn|citroen\w*|limoen\w*|"
    r"pesto|currypasta|tomatenpuree|tapenade|kappertjes|ansjovis|tahin\w*|sesamolie|honing|"
    r"salt|pepper|thyme|basil|parsley|cumin|coriander|cinnamon|turmeric|ginger|garlic|stock|lemon|lime|vinegar|mustard)\b"
)
# Dingen die je in een bereiding niet apart hoeft te noemen ("breng op smaak", "verhit de pan").
STIL = re.compile(r"(?i)\b(zout|zeezout|peper|water|olie|olijfolie|zonnebloemolie|boter|salt|pepper|oil)\b")
EENHEDEN = {
    "g", "gr", "gram", "kg", "ml", "cl", "dl", "l", "liter", "el", "tl", "eetlepel", "eetlepels", "theelepel", "theelepels",
    "stuk", "stuks", "teen", "tenen", "teentje", "teentjes", "blik", "blikje", "blikjes", "pot", "potje", "zak", "zakje",
    "snuf", "snufje", "scheut", "scheutje", "handje", "handjes", "takje", "takjes", "bos", "bosje", "plak", "plakken",
    "plakjes", "kop", "kopje", "klein", "kleine", "grote", "groot", "halve", "half", "verse", "vers", "gedroogde",
    "gedroogd", "fijngesneden", "gesnipperde", "geraspte", "naar", "smaak", "eventueel", "optioneel", "voor", "erbij",
    "tbsp", "tsp", "cup", "cups", "pinch", "fresh", "dried", "large", "small",
}


# ----------------------------------------------------------------- JSON-LD
def laad_json_soepel(tekst: str) -> Any:
    """JSON zoals het op websites staat: ook met regeleinden in tekst of een komma te veel voor ] of }."""
    tekst = tekst.strip()
    try:
        return json.loads(tekst, strict=False)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",(\s*[\]}])", r"\1", tekst), strict=False)


def recept_uit_jsonld(page: str) -> dict | None:
    """Zoek een schema.org Recipe in de JSON-LD van een webpagina."""
    for blok in re.findall(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page):
        try:
            data = laad_json_soepel(blok)
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


def _schoon(s: Any) -> str:
    s = re.sub(r"<[^>]+>", " ", str(s or ""))
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def duur_minuten(v: Any) -> int | None:
    """ISO 8601-duur ('PT1H15M') of een getal naar minuten."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return int(v) or None
    m = re.fullmatch(r"(?i)\s*P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?\s*", str(v))
    if not m or not any(m.groups()):
        return None
    d, h, mi = (int(x or 0) for x in m.groups())
    return (d * 24 * 60 + h * 60 + mi) or None


def _stappen(x: Any) -> list[str]:
    if isinstance(x, str):
        return [s for s in (_schoon(d) for d in re.split(r"\n+|</p>|<br\s*/?>", x)) if s]
    uit: list[str] = []
    for s in x or []:
        if isinstance(s, str):
            if _schoon(s):
                uit.append(_schoon(s))
        elif isinstance(s, dict):
            if s.get("itemListElement"):
                uit.extend(_stappen(s["itemListElement"]))
            elif _schoon(s.get("text") or s.get("name")):
                uit.append(_schoon(s.get("text") or s.get("name")))
    return uit


def recept_van_jsonld(ld: dict, pagina_tekst: str = "") -> dict:
    """Recept letterlijk uit JSON-LD: naam, porties, tijd, ingrediënten en stappen zoals de bron ze geeft.

    Ontbreken porties of tijd in de JSON-LD (bijv. bij Ottolenghi), dan zoeken we ze in de paginatekst.
    """
    yld = ld.get("recipeYield")
    if isinstance(yld, list):
        yld = next((y for y in yld if re.search(r"\d", str(y))), None)
    m = re.search(r"\d+", str(yld or ""))
    porties = int(m.group()) if m else None
    if not porties:
        m = re.search(r"(?i)\b(?:serves|voor)\s+(\d+)\b|\b(\d+)\s+(?:personen|porties|servings)\b", pagina_tekst)
        porties = int(next(g for g in m.groups() if g)) if m else None

    tijd = duur_minuten(ld.get("totalTime"))
    if not tijd:
        delen = [duur_minuten(ld.get("prepTime")), duur_minuten(ld.get("cookTime"))]
        tijd = sum(d for d in delen if d) or None
    if not tijd:
        gevonden = re.findall(r"(?i)\b(?:prep|cook|bereidingstijd|voorbereiding|oventijd)\w*:?\s+(\d+)\s*min", pagina_tekst)
        tijd = sum(int(g) for g in gevonden[:2]) or None

    auteur = ld.get("author")
    if isinstance(auteur, list):
        auteur = auteur[0] if auteur else None
    if isinstance(auteur, dict):
        auteur = auteur.get("name")
    return {
        "naam": _schoon(ld.get("name")),
        "porties": porties,
        "tijd": tijd,
        "ingredienten": [t for t in (re.sub(r"^[\s•▢▪◦·*–-]+", "", _schoon(i)) for i in ld.get("recipeIngredient") or []) if t],
        "stappen": _stappen(ld.get("recipeInstructions")),
        "auteur": _schoon(auteur) or None,
    }


NL_WOORDEN = {"de", "het", "een", "en", "met", "tot", "voeg", "minuten", "van", "op", "snijd", "snij", "doe", "bak", "in", "erbij"}
EN_WOORDEN = {"the", "and", "with", "until", "into", "then", "add", "minutes", "of", "to", "for", "cut", "heat", "stir", "a"}


def is_vreemde_taal(recept: dict) -> bool:
    """Is de bereiding duidelijk niet Nederlands? (ruwe telling van veelgebruikte woorden)"""
    woorden = re.findall(r"[a-z]+", " ".join(recept.get("stappen") or []).lower())
    nl = sum(w in NL_WOORDEN for w in woorden)
    en = sum(w in EN_WOORDEN for w in woorden)
    return en > nl and en >= 5


# ----------------------------------------------------------------- de AI: alleen koppelen en vertalen
def verrijk_prompt(recept: dict, producten: list[dict], vertalen: bool = False) -> str:
    """Vraag de AI om bij een letterlijk overgenomen recept de producten te koppelen (en zo nodig te vertalen)."""
    return (
        ("LET OP: dit recept is NIET Nederlands. Vertaal het volledig (punt 2): geef bij elk ingrediënt de Nederlandse "
         "'tekst' en geef alle stappen in het Nederlands, in precies hetzelfde aantal.\n" if vertalen else "") +
        "Hieronder staat een recept van een kok. Je mag er NIETS aan veranderen: geen ingrediënt of stap weglaten, "
        "niets toevoegen, niets samenvatten. Je doet alleen dit:\n"
        "1. Per ingrediënt (zelfde aantal, zelfde volgorde): product_id = het id uit de productlijst dat je ervoor in de "
        "winkel koopt (null als er niets past); aantal = hoeveel verpakkingen/stuks van dat product nodig zijn voor het "
        "aantal porties (rond naar boven af, minimaal 1); basis=true voor wat altijd in huis is (zout, peper, olie, "
        "water, gedroogde kruiden en specerijen).\n"
        "2. taal: 'nl' als het recept al Nederlands is, anders de taalcode. Is het NIET Nederlands, vertaal dan elk "
        "ingrediënt en elke stap volledig en getrouw naar het Nederlands (zelfde aantal, zelfde volgorde) en reken "
        "cups, ounces en Fahrenheit om naar gram, milliliter en graden Celsius. Is het al Nederlands, laat dan "
        "'tekst' en 'stappen' weg.\n"
        "3. vega (geen vlees of vis), tags (hooguit 4 korte labels zoals pasta, oven, soep, salade) en, als ze "
        "ontbreken, porties en tijd (totale minuten) zoals het recept ze noemt (anders null).\n"
        "Antwoord ALLEEN met één JSON-object:\n"
        '{"taal": str, "naam": str (Nederlands), "vega": bool, "tags": [str], "porties": int|null, "tijd": int|null,\n'
        ' "ingredienten": [{"tekst": str, "product_id": int|null, "aantal": number, "basis": bool}],\n'
        ' "stappen": [str]}\n\n'
        f"Producten (id: naam): {json.dumps({p['id']: p['name'] for p in producten}, ensure_ascii=False)}\n\n"
        f"Recept:\nNaam: {recept.get('naam')}\nPorties: {recept.get('porties') or '?'}\nTijd: {recept.get('tijd') or '?'}\n"
        "Ingrediënten:\n" + "\n".join(f"{n}. {t}" for n, t in enumerate(_teksten(recept), 1))
        + "\nBereiding:\n" + "\n".join(f"{n}. {s}" for n, s in enumerate(recept.get("stappen") or [], 1))
    )


def _teksten(recept: dict) -> list[str]:
    return [i["tekst"] if isinstance(i, dict) else str(i) for i in recept.get("ingredienten") or []]


def verrijk_toepassen(recept: dict, res: Any, vertaald: bool | None = None) -> dict:
    """Voeg het antwoord van de AI samen met het bronrecept; de tekst van de bron wint altijd.

    Alleen bij een recept in een andere taal nemen we de vertaling over, en alleen als die even veel regels heeft.
    Klopt het aantal ingrediënten niet, dan blijven ze ongekoppeld staan (liever niet gekoppeld dan iets kwijt).
    """
    res = res if isinstance(res, dict) else {}
    origineel = _teksten(recept)
    oud = [i if isinstance(i, dict) else {} for i in recept.get("ingredienten") or []]
    ai_ing = [i for i in (res.get("ingredienten") or []) if isinstance(i, dict)]
    if vertaald is None:
        vertaald = str(res.get("taal") or "nl").lower()[:2] != "nl"
    gelijk = len(ai_ing) == len(origineel)

    ingredienten = []
    for n, tekst in enumerate(origineel):
        ai = ai_ing[n] if gelijk else {}
        nl = str(ai.get("tekst") or "").strip()
        ingredienten.append({
            "tekst": nl if (vertaald and nl) else tekst,
            "product_id": ai.get("product_id") if gelijk else oud[n].get("product_id"),
            "aantal": ai.get("aantal") if gelijk else (oud[n].get("aantal") or 1),
            "basis": bool(ai.get("basis")) if gelijk else bool(oud[n].get("basis")),
        })

    stappen = list(recept.get("stappen") or [])
    ai_st = [str(s).strip() for s in (res.get("stappen") or []) if str(s).strip()]
    stappen_vertaald = bool(vertaald and stappen and len(ai_st) == len(stappen))
    if stappen_vertaald:
        stappen = ai_st

    uit = {**recept, "ingredienten": ingredienten, "stappen": stappen}
    uit.pop("auteur", None)
    if vertaald and res.get("naam"):
        uit["naam"] = str(res["naam"]).strip()
    uit["vega"] = bool(res.get("vega"))
    uit["tags"] = [str(t).strip() for t in (res.get("tags") or []) if str(t).strip()][:4]
    for veld in ("porties", "tijd"):
        if not uit.get(veld) and res.get(veld):
            uit[veld] = res[veld]
    if stappen_vertaald and gelijk and all(str(a.get("tekst") or "").strip() for a in ai_ing):
        taal = str(res.get("taal") or "").lower()[:5]
        uit["vertaald_uit"] = taal if taal and taal[:2] != "nl" else "en"
    return uit


def overneem_prompt(bron: str, producten: list[dict]) -> str:
    """Recept uit losse tekst (paginatekst zonder JSON-LD of geplakt): volledig overnemen, niets verzinnen."""
    return (
        "Haal het recept uit de tekst hieronder en zet het om naar JSON (Nederlands). Antwoord ALLEEN met één JSON-object:\n"
        '{"naam": str, "porties": int, "tijd": int (totale minuten), "vega": bool, "tags": [str],\n'
        ' "ingredienten": [{"tekst": "200 g kipfilet", "product_id": int|null, "aantal": number, "basis": bool}],\n'
        ' "stappen": [str]}\n'
        "Regels:\n"
        "- Neem ALLE ingrediënten en ALLE bereidingsstappen volledig en getrouw over, met hoeveelheden, tijden en "
        "temperaturen. Laat niets weg (ook geen kruiden, zout of garnering), vat niet samen en verzin er niets bij. "
        "Vertaal naar het Nederlands als de tekst in een andere taal is.\n"
        "- Staat er geen volledig recept (ingrediënten én bereiding) in de tekst, antwoord dan met "
        '{"naam": null}.\n'
        "- product_id is het id uit de productlijst hieronder dat je hiervoor in de winkel koopt (null als er niets "
        "past); aantal is hoeveel verpakkingen/stuks van dat product nodig zijn voor het aantal porties van het "
        "recept (rond naar boven af, minimaal 1); basis=true voor dingen die altijd in huis zijn (zout, peper, olie, "
        "water, gedroogde kruiden), die gaan niet op de boodschappenlijst.\n\n"
        f"Producten (id: naam): {json.dumps({p['id']: p['name'] for p in producten}, ensure_ascii=False)}\n\n"
        f"Tekst:\n{bron[:20000]}"
    )


# ----------------------------------------------------------------- kwaliteit
def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-zà-ÿ0-9 ]", " ", str(s or "").lower())).strip()


OPTIONEEL = re.compile(r"(?i)optioneel|eventueel|\bevt\b|naar smaak|naar wens|garner|erbij|om te serveren|voor erbij")


def _kort(s: str) -> str:
    """Dubbele klinkers enkel, zodat 'tomaat' en 'tomaten' op elkaar lijken."""
    return re.sub(r"([aeou])\1", r"\1", s)


def _stam(w: str) -> str:
    """Grove stam: meervoud en verkleinwoord eraf ('uien' -> 'ui', 'tomaatjes' -> 'tomat')."""
    for eind in ("jes", "en", "s"):
        if w.endswith(eind) and len(w) - len(eind) >= 2:
            w = w[: -len(eind)]
            break
    return _kort(w)


def _kernwoorden(tekst: str) -> list[str]:
    """De woorden waaraan je een ingrediënt in de bereiding herkent (zonder hoeveelheid, eenheid, toelichting)."""
    tekst = re.sub(r"\([^)]*\)", " ", tekst.split(",")[0])
    return [w for w in _norm(tekst).split() if len(w) >= 3 and not w.isdigit() and w not in EENHEDEN]


def _komt_voor(woord: str, stappen: str, stapwoorden: set[str]) -> bool:
    """Staat dit woord in de bereiding? Ook als meervoud, verkleinwoord of deel van een samenstelling."""
    woord = _kort(woord)
    if _stam(woord) in stappen or (len(woord) >= 5 and woord[:4] in stappen):
        return True
    # samenstelling: 'kastanjechampignons' is genoemd als de bereiding 'champignons' zegt
    return any(len(s) >= 5 and s in woord for s in stapwoorden)


def niet_in_bereiding(recept: dict) -> list[str]:
    """Ingrediënten die nergens in de stappen terugkomen (optionele en garnering tellen niet mee)."""
    stappen = _kort(_norm(" ".join(recept.get("stappen") or [])))
    stapwoorden = {_stam(w) for w in stappen.split()}
    uit = []
    for tekst in _teksten(recept):
        if STIL.search(tekst) or OPTIONEEL.search(tekst):
            continue
        woorden = _kernwoorden(tekst)
        if woorden and not any(_komt_voor(w, stappen, stapwoorden) for w in woorden):
            uit.append(tekst)
    return uit


def kwaliteit(recept: dict) -> list[str]:
    """Wat er aan een recept ontbreekt om het goed te kunnen koken ([] = compleet)."""
    teksten = _teksten(recept)
    stappen = [s for s in recept.get("stappen") or [] if str(s).strip()]
    punten: list[str] = []
    if not teksten:
        punten.append("Geen ingrediënten")
    elif len(teksten) < 5:
        punten.append(f"Maar {len(teksten)} ingrediënten")
    if teksten and not any(SMAAK.search(t) for t in teksten):
        punten.append("Geen kruiden of andere smaakmakers")
    if not stappen:
        punten.append("Geen bereiding")
    else:
        if len(" ".join(stappen)) < 250:
            punten.append("Bereiding is erg kort")
        mist = niet_in_bereiding(recept)
        if len(mist) >= 2:
            punten.append("Niet genoemd in de bereiding: " + ", ".join(mist[:4]) + ("…" if len(mist) > 4 else ""))
    if not recept.get("porties"):
        punten.append("Aantal porties onbekend")
    return punten


# ----------------------------------------------------------------- herkomst
def schoon_bronnen(v: Any) -> list[dict]:
    """Bronnenlijst in vaste vorm; accepteert ook regels 'Naam | domein/pad'.

    Een bron heeft een naam en een type: website (met domein en eventueel pad), boek of anders (zonder adres).
    `aan` = False laat de bron staan voor de herkomst van bestaande recepten, maar slaat hem over bij Ontdek.
    """
    if isinstance(v, str):
        v = [r for r in v.splitlines() if r.strip()]
    uit: list[dict] = []
    gezien: set[str] = set()
    for b in v or []:
        if isinstance(b, str):
            naam, _, adres = b.partition("|")
            if not adres:
                naam, adres = "", naam
            b = {"naam": naam.strip(), "domein": adres.strip()}
        if not isinstance(b, dict):
            continue
        adres = re.sub(r"(?i)^https?://", "", str(b.get("domein") or "").strip()).strip("/")
        domein, _, pad = adres.partition("/")
        domein = re.sub(r"(?i)^www\.", "", domein).lower()
        pad = str(b.get("pad") or (f"/{pad}" if pad else "")).rstrip("/")
        naam = str(b.get("naam") or "").strip() or domein
        soort = str(b.get("type") or "").lower()
        if soort not in BRON_TYPES:
            soort = "website" if domein else "boek"
        if soort != "website":
            domein, pad = "", ""
        if not naam or (soort == "website" and not domein) or naam.lower() in gezien:
            continue
        gezien.add(naam.lower())
        uit.append({"naam": naam, "domein": domein, "pad": pad, "type": soort,
                    "aan": bool(b["aan"]) if "aan" in b else True, "notitie": str(b.get("notitie") or "").strip()})
    return uit


def bron_van(url: str | None, bronnen: list[dict]) -> dict | None:
    """De opgegeven bron waar deze link bij hoort (of None)."""
    if not url or not str(url).lower().startswith("http"):
        return None
    p = urlparse(str(url))
    host = re.sub(r"^www\.", "", (p.hostname or "").lower())
    for b in bronnen:
        if (host == b["domein"] or host.endswith("." + b["domein"])) and p.path.startswith(b.get("pad") or ""):
            return b
    return None


def herkomst(recept: dict, bronnen: list[dict]) -> dict:
    """Waar een recept vandaan komt: {soort, naam}.

    bron = van een van de gekozen koks; web = van een andere site; eigen = van het gezin zelf;
    ai = door de AI bedacht; onbekend = geen bron bekend (nog te controleren).
    """
    bron = str(recept.get("bron") or "").strip()
    if recept.get("eigen"):
        return {"soort": "eigen", "naam": "Eigen recept"}
    gekozen = str(recept.get("bron_naam") or "").strip().lower()
    if gekozen:
        for b in bronnen:
            if b["naam"].lower() == gekozen:
                return {"soort": "bron", "naam": b["naam"]}
    if bron.lower().startswith("http"):
        b = bron_van(bron, bronnen)
        if b:
            return {"soort": "bron", "naam": b["naam"]}
        return {"soort": "web", "naam": re.sub(r"^www\.", "", urlparse(bron).hostname or "website")}
    if re.match(r"(?i)\s*(ai|gemini|claude|chatgpt)\b", bron) or (not bron and "nieuw" in (recept.get("tags") or [])):
        return {"soort": "ai", "naam": "Bedacht door AI"}
    if bron:
        return {"soort": "eigen", "naam": bron}
    return {"soort": "onbekend", "naam": "Herkomst onbekend"}


# ----------------------------------------------------------------- aanpassen met de AI
def aanpas_prompt(recept: dict, wens: str, producten: list[dict], context: str, correctie: str = "") -> str:
    """Vraag de AI een bestaand recept aan te passen op een wens, en het volledige resultaat terug te geven."""
    return (
        "Je past een bestaand recept aan op een wens van het gezin. Antwoord ALLEEN met één JSON-object:\n"
        '{"naam": str, "porties": int, "tijd": int (totale minuten), "vega": bool, "tags": [str],\n'
        ' "ingredienten": [{"tekst": str, "product_id": int|null, "aantal": number, "basis": bool}],\n'
        ' "stappen": [str], "wijzigingen": [str]}\n'
        "Regels:\n"
        "- Voer de wens uit en laat de rest van het recept zoals het is: naam, opbouw en smaak blijven herkenbaar. "
        "Verander niets wat niet bij de wens hoort.\n"
        "- Geef het VOLLEDIGE aangepaste recept: alle ingrediënten (met hoeveelheden) en alle stappen, ook wat niet "
        "verandert. Niet samenvatten, niets weglaten.\n"
        "- De bereiding moet compleet en uitvoerbaar zijn: één handeling per stap, met tijden, temperaturen en de "
        "kruiden of andere smaakmakers op de plek waar ze erbij horen. Vervang je iets (bijv. kip door tofu), voeg dan "
        "ook toe wat daarbij hoort (marinade, kruiden) en pas bereidingstijden aan.\n"
        "- Hoeveelheden kloppen met het aantal porties. Vraagt de wens een ander aantal porties, schaal dan alles.\n"
        "- Houd rekening met het gezin hieronder: allergieën nooit gebruiken, en wat ze niet eten vermijden.\n"
        "- Is de wens niet goed uitvoerbaar, geef dan toch je beste poging en begin 'wijzigingen' met een korte "
        "waarschuwing.\n"
        "- wijzigingen: 2 tot 6 korte Nederlandse zinnen over wat je veranderd hebt.\n"
        "- naam: houd de naam, tenzij de wens het gerecht echt anders maakt (bijv. 'vega' of 'met kip'); "
        "dan een korte passende naam.\n"
        "- product_id = het id uit de productlijst dat je ervoor in de winkel koopt (null als er niets past); aantal = "
        "verpakkingen/stuks voor de porties van het recept (naar boven afronden, minimaal 1); basis=true voor wat "
        "altijd in huis is (zout, peper, olie, water, gedroogde kruiden).\n\n"
        + (f"LET OP, correctie op je vorige antwoord: {correctie}\n\n" if correctie else "")
        + f"{context}\n\n"
        f"Wens: {wens}\n\n"
        f"Huidig recept:\nNaam: {recept.get('naam')}\nPorties: {recept.get('porties') or '?'}\nTijd: {recept.get('tijd') or '?'}\n"
        "Ingrediënten:\n" + "\n".join(f"{n}. {t}" for n, t in enumerate(_teksten(recept), 1))
        + "\nBereiding:\n" + "\n".join(f"{n}. {s}" for n, s in enumerate(recept.get("stappen") or [], 1))
        + f"\n\nProducten (id: naam): {json.dumps({p['id']: p['name'] for p in producten}, ensure_ascii=False)}"
    )


def aanpassing_samenvoegen(oud: dict, res: Any) -> dict:
    """Antwoord van de AI omzetten naar een recept; wat onveranderd is houdt zijn koppeling aan het Grocy-product.

    Geeft een ValueError als het antwoord geen bruikbaar recept is (liever een fout dan een half recept bewaren).
    """
    if not isinstance(res, dict):
        raise ValueError("Gemini gaf geen bruikbaar recept terug")
    ingr_oud = {_norm(i["tekst"]): i for i in oud.get("ingredienten") or [] if isinstance(i, dict) and i.get("tekst")}
    ingredienten = []
    for i in res.get("ingredienten") or []:
        if isinstance(i, str):
            i = {"tekst": i}
        if not isinstance(i, dict) or not str(i.get("tekst") or "").strip():
            continue
        tekst = str(i["tekst"]).strip()
        vorig = ingr_oud.get(_norm(tekst))
        if vorig:  # ongewijzigde regel: koppeling en aantal van het origineel behouden
            ingredienten.append({"tekst": tekst, "product_id": vorig.get("product_id"), "aantal": vorig.get("aantal") or 1,
                                 "basis": bool(vorig.get("basis"))})
        else:
            ingredienten.append({"tekst": tekst, "product_id": i.get("product_id"), "aantal": i.get("aantal") or 1,
                                 "basis": bool(i.get("basis"))})
    stappen = [str(s).strip() for s in res.get("stappen") or [] if str(s).strip()]
    if len(ingredienten) < 2 or not stappen:
        raise ValueError("Gemini gaf geen volledig recept terug (ingrediënten of bereiding ontbreken). Probeer het opnieuw.")
    wijz = [str(w).strip() for w in res.get("wijzigingen") or [] if str(w).strip()][:8]
    return {
        "naam": str(res.get("naam") or oud.get("naam") or "").strip() or oud.get("naam") or "Naamloos",
        "porties": res.get("porties") or oud.get("porties"),
        "tijd": res.get("tijd") or oud.get("tijd"),
        "vega": bool(res["vega"]) if "vega" in res else bool(oud.get("vega")),
        "tags": [str(t).strip() for t in res.get("tags") or oud.get("tags") or [] if str(t).strip()][:6],
        "ingredienten": ingredienten,
        "stappen": stappen,
        "wijzigingen": wijz,
    }


def recept_verschil(oud: dict, nieuw: dict) -> dict:
    """Wat er verandert tussen twee versies van een recept, om te tonen voordat je kiest.

    ingredienten: erbij, weg en gewijzigd (zelfde hoofdingrediënt, andere regel); stappen: per nieuwe stap of die nieuw is.
    """
    def sleutel(t: str) -> str:
        return _norm(t)

    oude = [i["tekst"] for i in oud.get("ingredienten") or [] if isinstance(i, dict) and i.get("tekst")]
    nieuwe = [i["tekst"] for i in nieuw.get("ingredienten") or [] if isinstance(i, dict) and i.get("tekst")]
    oude_s, nieuwe_s = {sleutel(t) for t in oude}, {sleutel(t) for t in nieuwe}
    weg = [t for t in oude if sleutel(t) not in nieuwe_s]
    erbij = [t for t in nieuwe if sleutel(t) not in oude_s]
    gewijzigd: list[dict] = []
    for t in list(weg):
        kern = _kernwoorden(t)
        if not kern:
            continue
        for u in erbij:
            if kern[0] in _kernwoorden(u)[:2] or (_kernwoorden(u) and _kernwoorden(u)[0] == kern[0]):
                gewijzigd.append({"van": t, "naar": u})
                weg.remove(t)
                erbij.remove(u)
                break
    oude_stappen = {sleutel(s) for s in oud.get("stappen") or []}
    return {
        "naam": [oud.get("naam"), nieuw.get("naam")] if sleutel(oud.get("naam") or "") != sleutel(nieuw.get("naam") or "") else None,
        "porties": [oud.get("porties"), nieuw.get("porties")] if oud.get("porties") != nieuw.get("porties") else None,
        "tijd": [oud.get("tijd"), nieuw.get("tijd")] if oud.get("tijd") != nieuw.get("tijd") else None,
        "ingredienten": {"erbij": erbij, "weg": weg, "gewijzigd": gewijzigd},
        "stappen_nieuw": [sleutel(s) not in oude_stappen for s in nieuw.get("stappen") or []],
    }
