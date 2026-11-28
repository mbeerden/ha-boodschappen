"""Bronnen van recepten: sitemaps lezen, receptlinks zoeken en de prompts voor Ontdek en foto's.

Pure functies (geen Home Assistant, geen netwerk), zodat ze los te testen zijn. Het ophalen zelf staat in eten.py.

Zoeken werkt zonder eigen index: de sitemap van een site noemt alle pagina's, en bij receptsites staat de titel in de
link ('pasta-met-pompoen-en-salie'). Of een pagina echt een recept is, blijkt pas bij het overnemen (schema.org).
"""
from __future__ import annotations

import html
import json
import random
import re
from typing import Any
from urllib.parse import urlparse

from .recept import _kort, _norm, _stam, bron_van

# Plekken waar een sitemap meestal staat als robots.txt er niets over zegt (WordPress, Shopify, algemeen).
SITEMAP_PADEN = ["/sitemap_index.xml", "/sitemap.xml", "/wp-sitemap.xml", "/sitemap-index.xml"]
# Kleine sitemaps die nooit recepten bevatten.
SITEMAP_NIET = re.compile(
    r"(?i)categor|tags?[-_.]|term|taxonom|author|user|product|collection|image|attachment|video|local|nav_menu|page-sitemap|"
    r"agentic|facet|hotspot|inspiration|recipe_[a-z]+")
SITEMAP_LIEVER = re.compile(r"(?i)recip|recept|wprm|post|blog|article|kook")
# Paden die geen receptpagina zijn.
PAD_NIET = re.compile(
    r"(?i)/(tag|tags|category|categorie|categorieen|author|auteur|page|pagina|feed|wp-content|wp-json|cart|checkout|account|"
    r"contact|over-mij|over-ons|about|privacy|privacyverklaring|cookies|algemene-voorwaarden|nieuwsbrief|zoeken|search|"
    r"winkel|shop|shops|producten|products|collections|webshop|boeken|books|login|disclaimer|blogs|restaurants|foodipedia|"
    r"delis|catering|careers|inspiration|international|programmas|actueel|nieuws)(/|$)"
)
BESTAND = re.compile(r"(?i)\.(jpe?g|png|gif|webp|pdf|xml|gz|zip|mp4)$")
STOPWOORDEN = {"met", "een", "het", "de", "en", "van", "voor", "op", "in", "uit", "bij", "als", "iets", "graag", "wil", "zin", "ik",
               "wat", "met", "maar", "naar", "the", "and", "with"}


# ----------------------------------------------------------------- sitemaps
def sitemap_locs(xml: str) -> list[str]:
    """Alle <loc>-adressen uit een sitemap of sitemap-index."""
    uit = []
    for m in re.findall(r"(?is)<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", xml or ""):
        url = html.unescape(m).strip()
        if url.lower().startswith("http"):
            uit.append(url)
    return uit


def is_sitemap_index(xml: str) -> bool:
    return "<sitemapindex" in (xml or "").lower()


def sitemaps_uit_robots(robots: str) -> list[str]:
    return [m.strip() for m in re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots or "")]


def sitemap_volgorde(urls: list[str]) -> list[str]:
    """Kind-sitemaps die waarschijnlijk recepten bevatten eerst; categorieën, producten en dergelijke vallen af."""
    goed = [u for u in urls if not SITEMAP_NIET.search(urlparse(u).path)]
    return sorted(goed, key=lambda u: (0 if SITEMAP_LIEVER.search(urlparse(u).path) else 1))


# ----------------------------------------------------------------- links
def receptlink_mogelijk(url: str, bron: dict | None = None) -> bool:
    """Kan dit een receptpagina zijn? (ruwe filter op de vorm van de link; de echte controle volgt bij het overnemen)"""
    p = urlparse(url)
    if bron and not bron_van(url, [bron]):
        return False
    pad = p.path.rstrip("/")
    if not pad or p.query or BESTAND.search(pad) or PAD_NIET.search(pad + "/"):
        return False
    laatste = pad.rsplit("/", 1)[-1]
    return len(laatste) >= 5


def titel_uit_url(url: str) -> str:
    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"(?i)\.(html?|php)$", "", slug)
    tekst = re.sub(r"[-_]+", " ", slug).strip()
    return (tekst[:1].upper() + tekst[1:]) if tekst else url


def url_sleutel(url: str | None) -> str:
    """Zelfde pagina, ongeacht www, http/https, slash en parameters (om al toegevoegde recepten te herkennen)."""
    p = urlparse(str(url or "").strip().lower())
    return re.sub(r"^www\.", "", p.hostname or "") + p.path.rstrip("/")


def _vraagwoorden(vraag: str) -> list[str]:
    return [w for w in _norm(vraag).split() if len(w) >= 3 and w not in STOPWOORDEN]


def score_link(url: str, woorden: list[str]) -> int:
    """Hoeveel zoekwoorden komen in de link voor (meervoud en samenstellingen tellen mee)."""
    slug = _kort(_norm(titel_uit_url(url)))
    slugstammen = {_stam(w) for w in slug.split()}
    n = 0
    for w in woorden:
        w = _kort(w)
        if _stam(w) in slugstammen or (len(w) >= 4 and w in slug):
            n += 1
    return n


def zoek_links(urls: list[str], vraag: str, klaar: set[str] | None = None, aantal: int = 12, zaad: int | None = None) -> list[str]:
    """De beste links bij een zoekvraag; zonder vraag een willekeurige greep. Al toegevoegde recepten (klaar) vallen af."""
    klaar = klaar or set()
    kand = [u for u in urls if url_sleutel(u) not in klaar]
    woorden = _vraagwoorden(vraag)
    rng = random.Random(zaad)
    if not woorden:
        rng.shuffle(kand)
        return kand[:aantal]
    scored = [(score_link(u, woorden), u) for u in kand]
    scored = [x for x in scored if x[0] > 0]
    rng.shuffle(scored)  # gelijke scores: elke keer een andere volgorde
    scored.sort(key=lambda x: (-x[0], len(x[1])))
    return [u for _, u in scored[:aantal]]


def kandidaten_voor_ai(urls: list[str], vraag: str, klaar: set[str] | None = None, max_n: int = 300, zaad: int | None = None) -> list[str]:
    """Links om aan de AI voor te leggen: eerst wat bij de vraag past, aangevuld met een willekeurige greep."""
    klaar = klaar or set()
    rng = random.Random(zaad)
    kand = [u for u in urls if url_sleutel(u) not in klaar]
    gekozen = zoek_links(kand, vraag, klaar, aantal=max_n // 2, zaad=zaad) if _vraagwoorden(vraag) else []
    rest = [u for u in kand if u not in set(gekozen)]
    rng.shuffle(rest)
    return (gekozen + rest)[:max_n]


# ----------------------------------------------------------------- bron controleren
def site_naam(page: str, domein: str) -> str:
    """Naam van de site uit een webpagina (og:site_name of de titel zonder omschrijving)."""
    m = re.search(r'(?is)<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\'](.*?)["\']', page) \
        or re.search(r'(?is)<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:site_name["\']', page)
    if m and m.group(1).strip():
        return html.unescape(m.group(1)).strip()
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
    if m:
        titel = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
        delen = [d.strip() for d in re.split(r"\s[|–—-]\s", titel) if d.strip()]
        if delen:
            return min(delen, key=len)[:60]
    return domein


def bron_advies(leesbaar: bool | None, gevonden: int) -> str:
    if leesbaar and gevonden:
        return f"Klaar: ik vond {gevonden} mogelijke receptpagina's en een daarvan is als recept te lezen. Zoeken en overnemen kan."
    if leesbaar:
        return "Een recept van deze site kan ik overnemen als je de link plakt. Zoeken in de bron lukt niet: de site heeft geen bruikbare sitemap."
    if gevonden:
        return "Ik vond pagina's, maar geen herkenbaar recept (schema.org). Overnemen lukt dan met geplakte tekst of een foto."
    return "Geen herkenbare recepten gevonden. Overnemen lukt met een link naar één recept, geplakte tekst of een foto."


# ----------------------------------------------------------------- AI
def kies_prompt(kandidaten: list[dict], wens: str, context: str, in_huis: list[str], acties: list[str], n: int = 8) -> str:
    """Laat de AI uit een lijst receptlinks (alleen de titels) de beste kiezen; hij geeft nummers terug, nooit zelf links."""
    return (
        f"Je helpt een gezin met een recept zoeken. Hieronder staat een lijst gerechten (nummer + titel van receptsites, soms in het "
        f"Engels; bij sommige ook tijd en ingrediënten). Kies de {n} die het best passen en geef ze in volgorde van passendheid.\n"
        "Antwoord ALLEEN met een JSON-lijst: "
        '[{"n": int, "reden": str}] — n is het nummer uit de lijst, reden is één korte Nederlandse zin.\n'
        "- Kies alleen nummers die in de lijst staan; verzin niets.\n"
        "- Weeg mee: de wens, het gezin (dieet, allergieën, wat ze niet eten), wat in huis is en wat in de aanbieding is.\n"
        "- Kies een gevarieerde set (niet vijf keer hetzelfde soort gerecht) en liever eenvoudige gerechten dan ingewikkelde.\n\n"
        f"Wens: {wens or 'iets lekkers en eenvoudigs voor een doordeweekse dag'}\n\n"
        f"{context}\n\n"
        f"In huis (voorraad): {', '.join(in_huis) or '-'}\n"
        f"In de aanbieding: {', '.join(acties) or '-'}\n\n"
        "Gerechten:\n" + "\n".join(k.get("regel") or f"{k['n']}. {k['titel']}" for k in kandidaten)
    )


def kies_toepassen(res: Any, kandidaten: list[dict], n: int = 8) -> list[dict]:
    """Antwoord van de AI omzetten naar links; nummers die niet bestaan worden genegeerd."""
    per_nr = {k["n"]: k for k in kandidaten}
    uit, gezien = [], set()
    for x in res if isinstance(res, list) else []:
        if not isinstance(x, dict):
            continue
        try:
            nr = int(x.get("n"))
        except (TypeError, ValueError):
            continue
        if nr in per_nr and nr not in gezien:
            gezien.add(nr)
            uit.append({**per_nr[nr], "reden": str(x.get("reden") or "").strip()})
    return uit[:n]


def foto_prompt(producten: list[dict], aantal: int, bron_naam: str | None = None) -> str:
    """Recept uit foto's (bijv. een kookboek): letterlijk overnemen, niets verzinnen."""
    from .recept import overneem_prompt

    kop = (
        f"De {'foto' if aantal == 1 else str(aantal) + ' foto’s'} hierbij {'toont' if aantal == 1 else 'tonen'} één recept"
        + (f" uit '{bron_naam}'" if bron_naam else "")
        + " (bijvoorbeeld een kookboekpagina). Lees alle tekst nauwkeurig en neem het recept volledig over. "
        "Is een stuk onleesbaar, laat dat dan niet weg maar noteer het in 'notitie' (gebruik daarvoor het extra veld "
        '"notitie": str).\n\n'
    )
    return kop + overneem_prompt("(zie de bijgevoegde foto's)", producten).replace("Haal het recept uit de tekst hieronder", "Haal het recept uit de foto's")


def json_regel(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False)
