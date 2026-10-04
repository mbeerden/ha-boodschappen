"""Aanbiedingen van producten die je vaak koopt, met een inslaadvoorstel.

Bronnen
- Lidl: de integratie 'lidl' (ha-lidl) levert aanbiedingen van je filiaal (deze en volgende week)
  en - met Lidl Plus-login - je coupons, als sensor-attributen.
- Jumbo: de openbare pagina jumbo.com/aanbiedingen/nu (server-side gerenderd).

De koppeling 'aanbieding -> jouw Grocy-product' doet een AI-taak (bijv. Google Gemini) in één
aanroep per winkel, alleen als de aanbiedingen of je productlijst veranderd zijn.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import html
import json
import logging
import math
import re
import time
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GrocyError

if TYPE_CHECKING:
    from .coordinator import BoodschappenCoordinator

_LOGGER = logging.getLogger(__name__)

JUMBO_URL = "https://www.jumbo.com/aanbiedingen/nu"
MAX_KANDIDATEN = 120
MAX_TEKST = 45000


# ----------------------------------------------------------------- pure helpers
def html_naar_tekst(page: str) -> str:
    """Ruwe HTML naar platte tekst (zonder scripts/styles), compact."""
    page = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|article|section|a|span)>", "\n", page)
    page = re.sub(r"<[^>]+>", " ", page)
    page = html.unescape(page)
    lines = [re.sub(r"\s+", " ", l).strip() for l in page.splitlines()]
    out, prev = [], None
    for l in lines:
        if l and l != prev:
            out.append(l)
        prev = l
    return "\n".join(out)


def parse_json(text: Any) -> Any:
    """Haal JSON uit een AI-antwoord (ook met ```json-blokken of tekst eromheen)."""
    if isinstance(text, (list, dict)):
        return text
    s = str(text or "").strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = s.find(opener), s.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(s[i : j + 1])
            except json.JSONDecodeError:
                continue
    return None


def euro(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"(\d+)[.,](\d{1,2})|(\d+)", str(v))
    if not m:
        return None
    if m.group(1):
        return float(f"{m.group(1)}.{m.group(2).ljust(2, '0')}")
    return float(m.group(3))


def actie_prijs(actie: Any) -> float | None:
    """Bedrag uit een actietekst: '2 voor 2,99' -> 2.99; 'voor 4,49 per 350 gram' -> 4.49; '1+1 gratis' -> None."""
    m = re.search(r"(\d+)[.,](\d{2})\b", str(actie or ""))
    return float(f"{m.group(1)}.{m.group(2)}") if m else None


def korting_pct(actie: Any) -> float | None:
    """'25% KORTING' / '32% OFF' / 'UP TO 23% OFF' -> 0.25 / 0.32 / 0.23."""
    m = re.search(r"(\d{1,2})\s*%", str(actie or ""))
    return int(m.group(1)) / 100 if m else None


def multibuy(actie: str) -> int:
    """'2 voor 4,99' / '2e halve prijs' / '1+1 gratis' -> 2; '3 voor 5' -> 3; anders 1."""
    a = (actie or "").lower()
    m = re.search(r"(\d+)\s*voor\b", a)
    if m:
        return max(1, int(m.group(1)))
    m = re.search(r"(\d+)\s*\+\s*(\d+)", a)
    if m:
        return int(m.group(1)) + int(m.group(2))
    m = re.search(r"(\d+)e\s+(?:halve prijs|gratis)", a)
    if m:
        return int(m.group(1))
    return 1


def prijs_per_stuk(prijs: float | None, actie: str) -> float | None:
    """Actieprijs omgerekend naar één stuk: '2 voor 1,49' -> 0,745; '2e halve prijs' bij 2,00 -> 1,50."""
    if prijs is None:
        return None
    a = (actie or "").lower()
    n = multibuy(a)
    if re.search(r"\d+\s*voor\b", a) and n > 1:
        return prijs / n
    if re.search(r"(\d+)e\s+halve prijs", a):
        return prijs * (n - 0.5) / n
    if re.search(r"(\d+)\s*\+\s*(\d+)", a):
        m = re.search(r"(\d+)\s*\+\s*(\d+)", a)
        return prijs * int(m.group(1)) / n
    return prijs


def stuks_in_verpakking(*teksten: Any) -> int:
    """'Scharreleieren 10 stuks' / '6 st.' / '4 x 125 g' -> 10 / 6 / 4; anders 1."""
    for t in teksten:
        m = re.search(r"(\d+)\s*(?:stuks?|st\b|st\.|x\b)", str(t or "").lower())
        if m and 1 < int(m.group(1)) <= 48:
            return int(m.group(1))
    return 1


def voordeel(normaal: float | None, actie_per_stuk: float | None) -> bool | None:
    """True = goedkoper dan wat je normaal betaalt; None = niet te zeggen."""
    if not normaal or actie_per_stuk is None:
        return None
    return actie_per_stuk < normaal * 0.97


def inslaad_advies(
    *, wekelijks: float, gewoon: float, voorraad: float, houdbaar_dagen: int,
    invries_dagen: int, mag_invriezen: bool, actie: str, max_stuks: int = 12,
) -> tuple[float, str]:
    """Hoeveel kopen tijdens de aanbieding, en waarom (korte uitleg)."""
    gewoon = max(1.0, float(gewoon or 1))
    if wekelijks <= 0:
        wekelijks = gewoon / 2  # weinig historie: voorzichtig
    if houdbaar_dagen is None or houdbaar_dagen < 0:
        houdbaar_dagen = 365  # -1 in Grocy = verloopt nooit
    reden = ""
    if houdbaar_dagen >= 60:
        weken, reden = 6.0, "lang houdbaar"
    elif mag_invriezen and invries_dagen and invries_dagen > 0:
        weken, reden = min(8.0, invries_dagen / 7), "in te vriezen"
    else:
        weken, reden = max(1.0, min(2.0, houdbaar_dagen / 7)), "beperkt houdbaar"
    nodig = math.ceil(wekelijks * weken) - max(0.0, voorraad)
    advies = max(gewoon, float(nodig))
    stap = multibuy(actie)
    if stap > 1:
        advies = math.ceil(advies / stap) * stap
    advies = float(min(advies, max(max_stuks, stap)))
    return advies, reden


REGELS = (
    "Let op: titels kunnen Engels zijn. Ook de variant moet kloppen: vers en diepvries zijn verschillend, "
    "rookworst is geen rookvlees, jonge kaas is geen (extra) belegen/oude kaas of cheddar/smeltkaas, "
    "kipfilet is geen kipdrumstick. Vage verzamelacties ('diverse wijnen', 'zomerwijnen', 'various') krijgen "
    "hoogstens zeker 0.5, tenzij mijn product er duidelijk onder valt. "
)


def match_prompt(winkel: str, aanbod: list[dict] | None, tekst: str | None, producten: list[dict]) -> str:
    prod = "\n".join(f"{p['id']}: {p['name']}" for p in producten)
    if aanbod is not None:
        bron = "\n".join(
            f"{a['key']}: {a.get('titel')}{(' - ' + a['omschrijving']) if a.get('omschrijving') else ''} | {a.get('merk') or ''} | {a.get('actie') or ''} | prijs {a.get('prijs') or '-'}"
            for a in aanbod
        )
        taak = (
            f"Hieronder staan de aanbiedingen van {winkel} (sleutel: titel | merk | actie | prijs) en mijn vaste producten "
            "(id: naam). Geef ALLEEN aanbiedingen die echt over hetzelfde soort product gaan als een van mijn producten "
            "(ander merk of verpakking mag; een andere productsoort niet). " + REGELS +
            'Antwoord uitsluitend met JSON: [{"key": "...", "product_id": 123, "zeker": 0.0-1.0}]. Geen match: [].'
        )
        return f"{taak}\n\nAANBIEDINGEN:\n{bron}\n\nMIJN PRODUCTEN:\n{prod}"
    taak = (
        f"Hieronder staat de tekst van de aanbiedingenpagina van {winkel} en een lijst met mijn vaste producten (id: naam). "
        "Zoek de aanbiedingen die echt over hetzelfde soort product gaan als een van mijn producten (ander merk mag; "
        "een andere productsoort niet; 'alle X' telt als match voor X). " + REGELS +
        'Antwoord uitsluitend met JSON: [{"titel": "...", "actie": "bijv. 2 voor 4,99 / 2e halve prijs / 1,49", '
        '"prijs": 1.49 of null, "geldig_tot": "YYYY-MM-DD" of null, "product_id": 123, "zeker": 0.0-1.0}]. Geen match: [].'
    )
    return f"{taak}\n\nMIJN PRODUCTEN:\n{prod}\n\nPAGINATEKST:\n{(tekst or '')[:MAX_TEKST]}"


# ----------------------------------------------------------------- service
class Aanbiedingen:
    def __init__(self, coordinator: "BoodschappenCoordinator") -> None:
        self.c = coordinator
        self.hass = coordinator.hass

    @property
    def state(self) -> dict:
        return self.c.state.setdefault("aanbiedingen", {"resultaat": [], "hash": {}, "genegeerd": [], "gemeld": [], "bijgewerkt": None, "fout": {}})

    # --- bronnen -------------------------------------------------------
    def _lidl_bron(self) -> list[dict]:
        reg = er.async_get(self.hass)
        out: list[dict] = []
        for ent in reg.entities.values():
            if ent.platform != "lidl" or not ent.entity_id.startswith("sensor."):
                continue
            st = self.hass.states.get(ent.entity_id)
            if not st:
                continue
            for attr in ("discounts", "coupons", "available_coupons", "activated_coupons"):
                items = st.attributes.get(attr)
                if not isinstance(items, list):
                    continue
                soort = "coupon" if "coupon" in attr else ("volgende week" if "preview" in ent.entity_id else "folder")
                for o in items:
                    if not isinstance(o, dict) or o.get("is_online_shop") is True:
                        continue
                    titel = o.get("title") or o.get("name") or o.get("description")
                    if not titel:
                        continue
                    key = str(o.get("id") or f"{titel}|{o.get('start_date')}")
                    out.append({
                        "key": f"lidl:{soort}:{key}"[:80], "titel": titel, "merk": o.get("brand"),
                        "actie": o.get("discount") if o.get("discount") not in (None, "-") else (o.get("discount_text") or o.get("promotion")),
                        "prijs": euro(o.get("price")), "oude_prijs": euro(o.get("old_price")),
                        "van": o.get("start_date") or o.get("start_validity_date") or o.get("startValidityDate"),
                        "tot": o.get("end_date") or o.get("end_validity_date") or o.get("endValidityDate") or o.get("expiration_date"),
                        "soort": soort, "verpakking": o.get("packaging"),
                        "omschrijving": o.get("description") if soort == "coupon" else None,
                        "geactiveerd": o.get("activated") if soort == "coupon" else None,
                    })
        # dubbele (zelfde titel+periode) eruit
        uniek: dict[str, dict] = {}
        for o in out:
            uniek.setdefault(f"{o['titel']}|{o['van']}|{o['soort']}", o)
        return list(uniek.values())

    async def _jumbo_tekst(self) -> str:
        session = async_get_clientsession(self.hass)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
            "Accept-Language": "nl-NL,nl;q=0.9",
        }
        async with session.get(JUMBO_URL, headers=headers, timeout=30) as resp:
            resp.raise_for_status()
            return html_naar_tekst(await resp.text())

    # --- kandidaten en AI ----------------------------------------------------
    def _kandidaten(self) -> list[dict]:
        d = self.c.data
        ids: dict[int, None] = {}
        for lst in self.c.favorites().values():
            for f in lst:
                ids[int(f["id"])] = None
        for p, _ in d.weekly_products():
            ids[int(p["id"])] = None
        for pid, p in d.products.items():
            if float(p.get("min_stock_amount") or 0) > 0:
                ids[pid] = None
        for pid, n in self._aankoopdagen().items():
            if n >= 2:
                ids[pid] = None
        prods = [{"id": pid, "name": d.products[pid]["name"]} for pid in ids if pid in d.products]
        return prods[:MAX_KANDIDATEN]

    def _aankoopdagen(self) -> dict[int, int]:
        dagen: dict[int, set] = {}
        for r in self.c._purchases:
            try:
                dagen.setdefault(int(r["product_id"]), set()).add(str(r.get("purchased_date"))[:10])
            except (KeyError, TypeError, ValueError):
                continue
        return {k: len(v) for k, v in dagen.items()}

    def _ai_entity(self) -> str | None:
        ents = sorted(self.hass.states.async_entity_ids("ai_task"))
        return ents[0] if ents else None

    async def _vraag_ai(self, instructie: str) -> Any:
        ent = self._ai_entity()
        if not ent:
            raise RuntimeError("Geen AI-taak (ai_task) gevonden om aanbiedingen te koppelen")
        res = await self.hass.services.async_call(
            "ai_task", "generate_data",
            {"task_name": "Boodschappen: aanbiedingen koppelen", "instructions": instructie, "entity_id": ent},
            blocking=True, return_response=True,
        )
        return parse_json((res or {}).get("data"))

    # --- hoofdroutine ----------------------------------------------------
    async def ververs(self, forceer: bool = False) -> dict:
        st = self.state
        producten = self._kandidaten()
        prod_hash = hashlib.sha1(json.dumps(producten, sort_keys=True).encode()).hexdigest()[:12]
        nieuw: list[dict] = []
        behouden = {r["winkel"]: [] for r in st["resultaat"]}
        for r in st["resultaat"]:
            behouden[r["winkel"]].append(r)

        # Lidl
        lidl = self._lidl_bron()
        if lidl:
            h = hashlib.sha1(json.dumps(lidl, sort_keys=True, default=str).encode()).hexdigest()[:12] + prod_hash
            if forceer or st["hash"].get("Lidl") != h:
                try:
                    matches = await self._vraag_ai(match_prompt("Lidl", lidl, None, producten)) or []
                    per_key = {o["key"]: o for o in lidl}
                    regels = []
                    for m in matches if isinstance(matches, list) else []:
                        o = per_key.get(str(m.get("key")))
                        if o and _pid(m) and float(m.get("zeker") or 0) >= 0.6:
                            regels.append({**o, "product_id": _pid(m), "zeker": float(m.get("zeker") or 0)})
                    behouden["Lidl"] = await self._verrijk("Lidl", regels)
                    st["hash"]["Lidl"] = h
                    st["fout"].pop("Lidl", None)
                except Exception as err:  # noqa: BLE001 - bron mag de rest niet breken
                    _LOGGER.warning("Lidl-aanbiedingen koppelen mislukt: %s", err)
                    st["fout"]["Lidl"] = str(err)

        # Jumbo
        try:
            tekst = await self._jumbo_tekst()
            h = hashlib.sha1(tekst.encode()).hexdigest()[:12] + prod_hash
            if forceer or st["hash"].get("Jumbo") != h:
                matches = await self._vraag_ai(match_prompt("Jumbo", None, tekst, producten)) or []
                regels = []
                for m in matches if isinstance(matches, list) else []:
                    if _pid(m) and float(m.get("zeker") or 0) >= 0.6 and m.get("titel"):
                        regels.append({
                            "key": f"jumbo:{m['titel']}|{m.get('geldig_tot')}"[:80], "titel": m["titel"], "merk": None,
                            "actie": m.get("actie"), "prijs": euro(m.get("prijs")), "oude_prijs": None,
                            "van": None, "tot": m.get("geldig_tot"), "soort": "folder",
                            "product_id": _pid(m), "zeker": float(m.get("zeker") or 0),
                        })
                behouden["Jumbo"] = await self._verrijk("Jumbo", regels)
                st["hash"]["Jumbo"] = h
            st["fout"].pop("Jumbo", None)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Jumbo-aanbiedingen ophalen/koppelen mislukt: %s", err)
            st["fout"]["Jumbo"] = str(err)

        vandaag = date.today().isoformat()
        alles = [r for lst in behouden.values() for r in lst if not r.get("tot") or str(r["tot"])[:10] >= vandaag]
        st["resultaat"] = alles
        st["bijgewerkt"] = datetime.now().isoformat(timespec="minutes")
        st["genegeerd"] = [g for g in st["genegeerd"] if any(r["key"] == g for r in alles)]
        for r in alles:
            if r["key"] not in st["gemeld"] and r.get("voordeel") is not False:
                nieuw.append(r)
        st["gemeld"] = [r["key"] for r in alles]
        self.c.save()
        return {"aanbiedingen": len(alles), "nieuw": nieuw}

    async def _verrijk(self, winkel: str, regels: list[dict]) -> list[dict]:
        """Voeg normale prijs, voorraad en inslaadadvies toe."""
        d = self.c.data
        list_id = next((lid for lid, l in d.lists.items() if l["name"].lower() == winkel.lower()), None)
        weekly = self._weekverbruik()
        fav = {f["id"]: f for lst in self.c.favorites().values() for f in lst}
        uit: list[dict] = []
        gezien: set[tuple] = set()
        for r in sorted(regels, key=lambda x: -x.get("zeker", 0)):
            pid = int(r["product_id"])
            p = d.products.get(pid)
            if not p or (pid, r["soort"]) in gezien:
                continue
            gezien.add((pid, r["soort"]))
            info: dict = {}
            try:
                info = await self.c.api.get(f"/stock/products/{pid}") or {}
            except GrocyError:
                pass
            gewoon = float((fav.get(pid) or {}).get("amount") or 1)
            advies, reden = inslaad_advies(
                wekelijks=weekly.get(pid, 0.0), gewoon=gewoon,
                voorraad=float(info.get("stock_amount") or 0),
                houdbaar_dagen=int(p.get("default_best_before_days") or 0),
                invries_dagen=int(p.get("default_best_before_days_after_freezing") or 0),
                mag_invriezen=str(p.get("should_not_be_frozen", 0)) in ("0", "False", "false"),
                actie=str(r.get("actie") or ""),
            )
            normaal = _num(info.get("avg_price")) or _num(info.get("last_price"))
            if r.get("prijs") is None:
                r = {**r, "prijs": actie_prijs(r.get("actie"))}
            pct = korting_pct(r.get("actie"))
            if r.get("prijs") is None and pct and normaal:
                # alleen een percentage (bijv. coupon '25% korting'): reken met je eigen normale prijs per stuk
                per_stuk, stuks = normaal * (1 - pct), 1
            else:
                per_stuk = prijs_per_stuk(r.get("prijs"), str(r.get("actie") or ""))
                stuks = stuks_in_verpakking(r.get("titel"), r.get("verpakking"), r.get("actie"))
            if stuks == 1 and r.get("prijs") is not None:
                # anders: het aantal per verpakking dat bij de bonnetjes is gekoppeld (bijv. eieren = 6)
                stuks = int(max([float(m.get("factor") or 1) for m in self.c.state.get("mappings", {}).values()
                                 if m.get("product_id") == pid] or [1]))
            if per_stuk is not None and stuks > 1:
                per_stuk = per_stuk / stuks
            goed = voordeel(normaal, per_stuk) if r.get("soort") != "coupon" else True
            if goed is False:
                advies = gewoon  # geen voordeel: niet inslaan
            uit.append({
                **r, "winkel": winkel, "list_id": list_id, "product": p["name"],
                "normale_prijs": normaal, "prijs_per_stuk": _num(per_stuk), "voordeel": goed,
                "voorraad": _num(info.get("stock_amount")) or 0, "gewoon": gewoon,
                "advies": advies, "advies_reden": reden,
            })
        return uit

    def _weekverbruik(self) -> dict[int, float]:
        tot: dict[int, float] = {}
        for r in self.c._purchases:
            try:
                tot[int(r["product_id"])] = tot.get(int(r["product_id"]), 0.0) + float(r.get("amount") or 0)
            except (KeyError, TypeError, ValueError):
                continue
        return {k: v / 8 for k, v in tot.items()}

    def negeer(self, key: str) -> None:
        if key not in self.state["genegeerd"]:
            self.state["genegeerd"].append(key)
        self.c.save()

    def zichtbaar(self) -> list[dict]:
        st = self.state
        op_lijst = {(i.list_id, i.product_id) for i in self.c.data.items if i.product_id and not i.done}
        return [
            {**r, "op_lijst": (r.get("list_id"), r["product_id"]) in op_lijst}
            for r in st["resultaat"] if r["key"] not in st["genegeerd"]
        ]


def _pid(m: dict) -> int | None:
    try:
        return int(m.get("product_id"))
    except (TypeError, ValueError):
        return None


def _num(v: Any) -> float | None:
    try:
        return round(float(v), 2) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
