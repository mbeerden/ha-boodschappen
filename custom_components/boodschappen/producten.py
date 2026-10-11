"""Productbeheer vanuit de boodschappenlijst: nieuw product aanmaken, details bekijken en aanpassen.

Alles gaat via de Grocy-API; Grocy zelf hoeft niet geopend te worden.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .api import GrocyError
from .logic import normalize

if TYPE_CHECKING:
    from .coordinator import BoodschappenCoordinator

_LOGGER = logging.getLogger(__name__)


def _num(v: Any, d: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return d


def raad_prompt(naam: str, groepen: dict[int, str], winkels: dict[int, str], voorbeelden: list[str]) -> str:
    return (
        "Een gezin zet een nieuw product op de boodschappenlijst. Kies in welk vak (looproute) van de winkel het "
        "ligt en in welke winkel ze het meestal kopen. Antwoord ALLEEN met JSON: "
        '{"naam": str, "group_id": int, "winkel_id": int}\n'
        "- naam: nette Nederlandse productnaam met hoofdletter, zonder aantal (bijv. 'Bakpoeder').\n"
        "- Kies bij twijfel de eerste winkel; die wordt het meest gebruikt.\n\n"
        f"Product: {naam}\n"
        f"Vakken (id: naam): {groepen}\n"
        f"Winkels (id: naam): {winkels}\n"
        f"Voorbeelden van producten per vak: {voorbeelden[:40]}"
    )


class Producten:
    def __init__(self, coordinator: "BoodschappenCoordinator") -> None:
        self.c = coordinator

    @property
    def d(self):
        return self.c.data

    def winkels(self) -> list[dict]:
        """Grocy-winkels die ook een boodschappenlijst hebben (bijv. Lidl, Jumbo)."""
        lijsten = {normalize(l["name"]) for l in self.d.lists.values()}
        uit = [{"id": lid, "name": l["name"]} for lid, l in sorted(self.d.locations.items())
               if normalize(l["name"]) in lijsten]
        return uit or [{"id": lid, "name": l["name"]} for lid, l in sorted(self.d.locations.items())]

    def _list_for_location(self, loc_id: int | None) -> int:
        loc = self.d.locations.get(int(loc_id)) if loc_id else None
        if loc:
            for lid, l in self.d.lists.items():
                if normalize(l["name"]) == normalize(loc["name"]):
                    return lid
        return min(self.d.lists) if self.d.lists else 1

    # --- nieuw product -----------------------------------------------------
    async def raden(self, naam: str) -> dict:
        """Vak en winkel voorstellen voor een nieuw product (AI-taak; zonder AI: geen voorstel)."""
        groepen = {gid: g["name"] for gid, g in sorted(self.d.groups.items())}
        winkels = {w["id"]: w["name"] for w in self.winkels()}
        per_groep: dict[int, list[str]] = {}
        for p in self.d.products.values():
            if p.get("product_group_id"):
                per_groep.setdefault(int(p["product_group_id"]), []).append(p["name"])
        voorbeelden = [f"{groepen.get(g, g)}: {', '.join(n[:5])}" for g, n in per_groep.items()]
        uit = {"naam": naam.strip()[:1].upper() + naam.strip()[1:], "group_id": None, "winkel_id": next(iter(winkels), None)}
        try:
            res = await self.c.aanbiedingen._vraag_ai(raad_prompt(naam, groepen, winkels, voorbeelden))
        except Exception as err:  # noqa: BLE001 - zonder AI kan de gebruiker zelf kiezen
            _LOGGER.debug("Product raden mislukt: %s", err)
            return uit
        if isinstance(res, dict):
            if res.get("naam"):
                uit["naam"] = str(res["naam"]).strip()
            if int(_num(res.get("group_id"), 0)) in groepen:
                uit["group_id"] = int(_num(res.get("group_id")))
            if int(_num(res.get("winkel_id"), 0)) in winkels:
                uit["winkel_id"] = int(_num(res.get("winkel_id")))
        return uit

    async def nieuw(self, naam: str, group_id: int | None, winkel_id: int | None, amount: float = 1,
                    item_id: int | None = None) -> int:
        """Product in Grocy aanmaken en op de lijst van zijn winkel zetten (een los tekst-item wordt vervangen)."""
        naam = (naam or "").strip()
        if not naam:
            raise ValueError("Geef het product een naam")
        bestaand = next((p for p in self.d.products.values() if normalize(p["name"]) == normalize(naam)), None)
        if bestaand:
            pid = int(bestaand["id"])
        else:
            template = next((p for p in self.d.products.values()
                             if group_id and p.get("product_group_id") and int(p["product_group_id"]) == int(group_id)), None)
            template = template or next(iter(self.d.products.values()), {})
            pid = await self.c.api.create_product(naam, group_id, winkel_id, template)
        if item_id:
            oud = next((i for i in self.d.items if i.id == int(item_id)), None)
            if oud:
                amount = oud.amount or amount
                await self.c.api.delete_list_item(oud.id)
        await self.c.async_refresh()
        lid = self._list_for_location(winkel_id)
        await self.c.add_product(lid, pid, float(amount or 1))
        return pid

    # --- details -----------------------------------------------------------
    async def details(self, pid: int) -> dict:
        p = await self.c.api.get(f"/objects/products/{pid}")
        if not p:
            raise ValueError("Product niet gevonden")
        voorraad = 0.0
        try:
            st = await self.c.api.get(f"/stock/products/{pid}") or {}
            voorraad = _num(st.get("stock_amount"), 0)
        except GrocyError as err:
            _LOGGER.debug("Voorraad ophalen mislukt: %s", err)
        uf = (self.d.products.get(int(pid)) or {}).get("userfields") or {}
        return {
            "id": int(pid), "name": p.get("name"),
            "group_id": int(p["product_group_id"]) if p.get("product_group_id") else None,
            "winkel_id": int(p["shopping_location_id"]) if p.get("shopping_location_id") else None,
            "min_stock": _num(p.get("min_stock_amount"), 0),
            "voorraad": voorraad,
            "wekelijks": _num(uf.get("wekelijks"), 0),
        }

    async def opslaan(self, pid: int, velden: dict) -> None:
        upd: dict[str, Any] = {}
        if velden.get("name"):
            upd["name"] = str(velden["name"]).strip()
        if "group_id" in velden:
            upd["product_group_id"] = velden["group_id"] or None
        if "winkel_id" in velden:
            upd["shopping_location_id"] = velden["winkel_id"] or None
        if "min_stock" in velden:
            upd["min_stock_amount"] = max(0.0, _num(velden["min_stock"], 0))
        if upd:
            await self.c.api.put(f"/objects/products/{pid}", upd)
        if "wekelijks" in velden:
            w = max(0.0, _num(velden["wekelijks"], 0))
            await self.c.api.put(f"/userfields/products/{pid}", {"wekelijks": (str(int(w)) if w.is_integer() else str(w)) if w else ""})
        if "voorraad" in velden and velden["voorraad"] is not None:
            await self.voorraad_zetten(pid, max(0.0, _num(velden["voorraad"], 0)))
        await self.c.async_refresh()
        # een product dat van winkel wisselt: open lijst-items meeverhuizen
        if "winkel_id" in velden:
            doel = self.d.list_for_product(int(pid))
            for i in [i for i in self.d.items if i.product_id == int(pid) and not i.done and i.list_id != doel]:
                await self.c.api.update_list_item(i.id, {"shopping_list_id": doel})
            await self.c.async_refresh()

    async def voorraad_zetten(self, pid: int, nieuw: float) -> None:
        st = await self.c.api.get(f"/stock/products/{pid}") or {}
        if abs(_num(st.get("stock_amount"), 0) - nieuw) < 1e-6:
            return
        await self.c.api.post(f"/stock/products/{pid}/inventory", {"new_amount": nieuw})

    async def op(self, pid: int) -> None:
        """'Op': voorraad naar 0 en meteen op de lijst van de eigen winkel."""
        await self.voorraad_zetten(pid, 0)
        await self.c.async_refresh()
        lid = self.d.list_for_product(int(pid))
        if not self.d.open_item_for_product(lid, int(pid)):
            await self.c.api.add_list_item(lid, int(pid), 1, "op")
        await self.c.async_refresh()
