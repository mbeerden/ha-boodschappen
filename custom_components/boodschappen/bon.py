"""Bonnetjes verwerken: prijzen op afvink-boekingen, ongeplande aankopen boeken, onbekende regels in een wachtrij.

Uitgangspunt: het AFVINKEN in de winkel boekt de voorraad. Een bonnetje
- zet de betaalde prijs op die afvink-boeking (geen extra voorraad),
- boekt alleen wat niet (of te weinig) is afgevinkt: ongeplande aankopen,
- zet regels die niet te koppelen zijn in een wachtrij, af te handelen op de Boodschappen-pagina.
"""
from __future__ import annotations

from datetime import datetime
import logging
import re
import time
from typing import TYPE_CHECKING, Any
import uuid

from .api import GrocyError
from .logic import match_product, normalize

if TYPE_CHECKING:
    from .coordinator import BoodschappenCoordinator

_LOGGER = logging.getLogger(__name__)

# Regels die nooit voorraad zijn
NEGEER_WOORDEN = ("statiegeld", "emballage", "draagtas", "tas ", "plastic tas", "papieren tas", "lidl plus", "spaarzegel")
KORTING_WOORDEN = ("korting", "actie", "voordeel", "bonus", "prijsverlaging", "lidl plus korting")
AFVINK_VENSTER = 4 * 24 * 3600  # afvink-boekingen tot 4 dagen terug koppelen aan een bon


def _num(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("€", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return default


def mapping_key(winkel: str, tekst: str) -> str:
    return f"{normalize(winkel)}|{normalize(tekst)}"


def suggest_factor(tekst: str) -> float:
    """'EIEREN 6ST' / 'Eieren 10 stuks' -> 6 / 10 (alleen een voorstel in het koppelscherm)."""
    m = re.search(r"(\d+)\s*(?:st\b|stk|stuks|x\b)", tekst.lower())
    return float(m.group(1)) if m and 1 < int(m.group(1)) <= 48 else 1.0


def prepare_lines(regels: list[dict]) -> list[dict]:
    """Normaliseer de door Gemini uitgelezen regels; kortingsregels gaan van de regel ervoor af."""
    out: list[dict] = []
    for r in regels or []:
        tekst = str(r.get("bon") or r.get("product") or "").strip()
        product = str(r.get("product") or tekst).strip()
        totaal = _num(r.get("totaal"))
        aantal = _num(r.get("aantal"), 1.0) or 1.0
        ntekst = (tekst + " " + product).lower()
        if totaal < 0 or (any(w in ntekst for w in KORTING_WOORDEN) and totaal <= 0):
            if out and out[-1]["status"] != "genegeerd":
                out[-1]["totaal"] = round(out[-1]["totaal"] + totaal, 2)
                out[-1]["korting"] = round(out[-1].get("korting", 0) + totaal, 2)
            continue
        line = {
            "id": uuid.uuid4().hex[:10],
            "tekst": tekst or product,
            "product": product,
            "grocy": str(r.get("grocy") or "").strip(),
            "aantal": aantal,
            "totaal": round(totaal, 2),
            "status": "nieuw",
        }
        if any(w in f" {ntekst} " for w in NEGEER_WOORDEN) or totaal == 0:
            line["status"] = "genegeerd"
        out.append(line)
    return out


class BonVerwerker:
    def __init__(self, coordinator: "BoodschappenCoordinator") -> None:
        self.c = coordinator

    @property
    def state(self) -> dict:
        return self.c.state

    # ------------------------------------------------------------------
    def _winkel_ids(self, winkel: str) -> tuple[int | None, int | None]:
        """(shopping_location_id, list_id) bij een winkelnaam van de bon."""
        data = self.c.data
        w = normalize(winkel)
        loc_id = next((lid for lid, l in data.locations.items() if normalize(l["name"]) and normalize(l["name"]) in w), None)
        list_id = next((lid for lid, l in data.lists.items() if normalize(l["name"]) and normalize(l["name"]) in w), None)
        return loc_id, list_id

    def _winkel(self, winkel: str) -> str:
        """Vaste winkelnaam ('Lidl Centrum' / 'LIDL' -> 'Lidl'), zodat koppelingen per winkel blijven werken."""
        w = normalize(winkel)
        for l in list(self.c.data.locations.values()) + list(self.c.data.lists.values()):
            n = normalize(l["name"])
            if n and n in w:
                return l["name"]
        return winkel

    def _key(self, winkel: str, tekst: str) -> str:
        return mapping_key(self._winkel(winkel), tekst)

    def _find_product(self, line: dict, winkel: str) -> tuple[int | None, float, str]:
        """Geeft (product_id, factor, bron). bron: koppeling | gemini | naam | negeren | ''."""
        products = self.c.data.products
        m = self.state["mappings"].get(self._key(winkel, line["tekst"]))
        if m:
            if m.get("negeren"):
                return None, 1.0, "negeren"
            if int(m["product_id"]) in products:
                return int(m["product_id"]), float(m.get("factor") or 1), "koppeling"
        if line.get("grocy"):
            g = normalize(line["grocy"])
            for pid, p in products.items():
                if normalize(p["name"]) == g:
                    return pid, 1.0, "gemini"
        p = match_product(line["product"], list(products.values()), threshold=0.86)
        if p:
            return int(p["id"]), 1.0, "naam"
        return None, 1.0, ""

    async def _apply(self, pid: int, amount: float, totaal: float, winkel: str, datum: str | None) -> dict:
        """Prijs op afvink-boekingen zetten en het restant als ongeplande aankoop boeken."""
        loc_id, _ = self._winkel_ids(winkel)
        price = (totaal / amount) if amount > 0 and totaal > 0 else None
        now = time.time()
        kandidaten = sorted(
            (a for a in self.state["afvink"] if a["product_id"] == pid and a.get("prijs") is None and now - a["ts"] <= AFVINK_VENSTER),
            key=lambda a: a["ts"], reverse=True,
        )
        rest = amount
        geprijsd = 0.0
        for a in kandidaten:
            if rest <= 0.001:
                break
            if price is not None:
                try:
                    await self.c.api.price_transaction(pid, a["tx"], price)
                except GrocyError as err:
                    _LOGGER.warning("Prijs zetten mislukt voor product %s: %s", pid, err)
            a["prijs"] = price if price is not None else 0
            rest -= float(a["amount"])
            geprijsd += float(a["amount"])
        result: dict[str, Any] = {"tx": None, "geprijsd": geprijsd, "geboekt": 0.0}
        if rest > 0.001:
            # niet (of te weinig) afgevinkt: als ongeplande aankoop boeken, open lijst-item afvinken zonder te boeken
            for item in self.c.data.items:
                if item.product_id == pid and not item.done:
                    await self.c.api.update_list_item(item.id, {"done": 1})
                    break
            result["tx"] = await self.c.api.book_purchase(pid, rest, loc_id, price, _date(datum))
            result["geboekt"] = rest
        result["status"] = "ongepland" if geprijsd == 0 else ("aangevuld" if result["geboekt"] else "prijs")
        return result

    async def verwerk(self, winkel: str, datum: str | None, bestand: str | None, regels: list[dict], opnieuw: bool = False) -> dict:
        if bestand and not opnieuw and any(b.get("bestand") == bestand for b in self.state["bonnen"]):
            return {"overgeslagen": True, "reden": "bon al verwerkt"}
        await self.c.async_refresh()
        lines = prepare_lines(regels)
        winkel = self._winkel(winkel)
        bon = {
            "id": uuid.uuid4().hex[:10],
            "winkel": winkel, "datum": datum, "bestand": bestand,
            "verwerkt": datetime.now().isoformat(timespec="minutes"),
            "totaal": round(sum(l["totaal"] for l in lines if l["status"] != "genegeerd"), 2),
            "regels": lines,
        }
        for line in lines:
            if line["status"] == "genegeerd":
                continue
            pid, factor, bron = self._find_product(line, winkel)
            if bron == "negeren":
                line["status"] = "genegeerd"
                continue
            if pid is not None and bron in ("gemini", "naam") and suggest_factor(line["tekst"]) > 1:
                # verpakking met meerdere stuks (bijv. 'EIEREN 6ST'): eerst laten bevestigen, anders klopt de voorraad niet
                pid = None
            if pid is None:
                line["status"] = "te koppelen"
                self.state["queue"].append({
                    "id": line["id"], "bon_id": bon["id"], "winkel": winkel, "datum": datum,
                    "tekst": line["tekst"], "suggestie": line["product"], "aantal": line["aantal"],
                    "totaal": line["totaal"], "factor": suggest_factor(line["tekst"]),
                })
                continue
            await self._apply_line(line, pid, factor, bron, winkel, datum)
        self.state["bonnen"].insert(0, bon)
        self.c.save()
        await self.c.async_refresh()
        return self.samenvatting(bon)

    async def _apply_line(self, line: dict, pid: int, factor: float, bron: str, winkel: str, datum: str | None) -> None:
        amount = round(line["aantal"] * factor, 3)
        try:
            res = await self._apply(pid, amount, line["totaal"], winkel, datum)
        except GrocyError as err:
            _LOGGER.error("Bonregel %s verwerken mislukt: %s", line["tekst"], err)
            line["status"] = "fout"
            return
        p = self.c.data.products.get(pid, {})
        line.update({
            "product_id": pid, "grocy_naam": p.get("name"), "factor": factor, "bron": bron,
            "status": res["status"], "tx": res["tx"], "voorraad": amount,
            "prijs_per_eenheid": round(line["totaal"] / amount, 4) if amount else None,
        })

    def samenvatting(self, bon: dict) -> dict:
        tel: dict[str, int] = {}
        for l in bon["regels"]:
            tel[l["status"]] = tel.get(l["status"], 0) + 1
        return {"bon_id": bon["id"], "winkel": bon["winkel"], "datum": bon["datum"], "totaal": bon["totaal"], "aantallen": tel}

    # Koppelscherm --------------------------------------------------------
    def _queue_item(self, qid: str) -> dict:
        q = next((q for q in self.state["queue"] if q["id"] == qid), None)
        if q is None:
            raise ValueError("Regel staat niet (meer) in de wachtrij")
        return q

    def _bon_line(self, bon_id: str, line_id: str) -> tuple[dict | None, dict | None]:
        bon = next((b for b in self.state["bonnen"] if b["id"] == bon_id), None)
        line = next((l for l in bon["regels"] if l["id"] == line_id), None) if bon else None
        return bon, line

    async def koppel(self, qid: str, product_id: int, factor: float = 1.0, onthouden: bool = True) -> dict:
        q = self._queue_item(qid)
        if onthouden:
            self.state["mappings"][self._key(q["winkel"], q["tekst"])] = {
                "product_id": int(product_id), "factor": float(factor or 1), "tekst": q["tekst"], "winkel": q["winkel"],
            }
        await self.c.async_refresh()
        bon, line = self._bon_line(q["bon_id"], q["id"])
        if line is None:
            line = {"id": q["id"], "tekst": q["tekst"], "product": q["suggestie"], "aantal": q["aantal"], "totaal": q["totaal"]}
        await self._apply_line(line, int(product_id), float(factor or 1), "handmatig", q["winkel"], q.get("datum"))
        self.state["queue"] = [x for x in self.state["queue"] if x["id"] != qid]
        self.c.save()
        await self.c.async_refresh()
        return line

    async def negeer(self, qid: str, onthouden: bool = True) -> None:
        q = self._queue_item(qid)
        if onthouden:
            self.state["mappings"][self._key(q["winkel"], q["tekst"])] = {"negeren": True, "tekst": q["tekst"], "winkel": q["winkel"]}
        _, line = self._bon_line(q["bon_id"], q["id"])
        if line:
            line["status"] = "genegeerd"
        self.state["queue"] = [x for x in self.state["queue"] if x["id"] != qid]
        self.c.save()

    async def nieuw_product(self, qid: str, naam: str, group_id: int | None, factor: float = 1.0) -> dict:
        q = self._queue_item(qid)
        loc_id, _ = self._winkel_ids(q["winkel"])
        products = self.c.data.products
        template = next((p for p in products.values() if group_id and p.get("product_group_id") and int(p["product_group_id"]) == int(group_id)), None)
        template = template or next(iter(products.values()), {})
        pid = await self.c.api.create_product(naam.strip(), group_id, loc_id, template)
        await self.c.async_refresh()
        return await self.koppel(qid, pid, factor, True)

    async def herkoppel(self, bon_id: str, line_id: str, product_id: int | None, factor: float = 1.0, negeren: bool = False) -> dict | None:
        """Een al verwerkte bonregel corrigeren (andere koppeling, ander aantal per verpakking, of negeren)."""
        bon, line = self._bon_line(bon_id, line_id)
        if line is None:
            raise ValueError("Bonregel niet gevonden")
        if line.get("tx"):
            try:
                await self.c.api.undo_transaction(line["tx"])
            except GrocyError as err:
                _LOGGER.warning("Boeking van bonregel ongedaan maken mislukt: %s", err)
            line["tx"] = None
        # eventuele prijs-koppelingen met afvink-boekingen weer vrijgeven
        for a in self.state["afvink"]:
            if a["product_id"] == line.get("product_id") and a.get("prijs") is not None and line.get("prijs_per_eenheid") is not None \
                    and abs((a.get("prijs") or 0) - line["prijs_per_eenheid"]) < 1e-6:
                a["prijs"] = None
        key = self._key(bon["winkel"], line["tekst"])
        if negeren:
            self.state["mappings"][key] = {"negeren": True, "tekst": line["tekst"], "winkel": bon["winkel"]}
            line["status"] = "genegeerd"
            self.c.save()
            return line
        self.state["mappings"][key] = {"product_id": int(product_id), "factor": float(factor or 1), "tekst": line["tekst"], "winkel": bon["winkel"]}
        await self.c.async_refresh()
        await self._apply_line(line, int(product_id), float(factor or 1), "handmatig", bon["winkel"], bon.get("datum"))
        self.c.save()
        await self.c.async_refresh()
        return line

    def vergeet_koppeling(self, key: str) -> None:
        self.state["mappings"].pop(key, None)
        self.c.save()


def _date(datum: str | None) -> str | None:
    """Accepteer '2026-10-03', '03-10-2026', '3/10/2026'; anders None (dan gebruikt Grocy vandaag)."""
    if not datum:
        return None
    s = str(datum).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%y", "%d/%m/%y"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
