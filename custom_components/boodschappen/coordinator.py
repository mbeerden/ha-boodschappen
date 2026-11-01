"""Coordinator: haalt periodiek de boodschappenlijsten uit Grocy en voert acties uit."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GrocyApi, GrocyError
from .const import DOMAIN, NOTE_TEKORT, NOTE_WEEKVAST, SCAN_INTERVAL_SECONDS
from .logic import GrocyData, compute_favorites, match_product, parse_entry

_LOGGER = logging.getLogger(__name__)

AFVINK_BEWAARTIJD = 30 * 24 * 3600  # winkelboekingen 30 dagen bewaren voor koppeling met (late) bonnetjes
VERGETEN_NA = 3 * 3600  # na 3 uur een melding als afgevinkte items nog niet zijn afgerond
FAVORIETEN_WEKEN = 8
FAVORIETEN_VERVERS = 3600  # aankoophistorie 1x per uur ophalen


class BoodschappenCoordinator(DataUpdateCoordinator[GrocyData]):
    def __init__(self, hass: HomeAssistant, api: GrocyApi, entry_id: str) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS))
        self.api = api
        self._old_store: Store = Store(hass, 1, f"{DOMAIN}.{entry_id}.transacties")
        self._store: Store = Store(hass, 1, f"{DOMAIN}.{entry_id}.data")
        # state: transacties per lijst-item, winkelboekingen (afvink), bon-koppelingen, wachtrij en bonnen,
        # afgevinkt-tijdstip per lijst-item (voor de herinnering) en lijsten waarvoor al een herinnering is gestuurd
        self.state: dict[str, Any] = {
            "transactions": {}, "afvink": [], "mappings": {}, "queue": [], "bonnen": [],
            "favorieten": {"vast": [], "verborgen": []}, "afgevinkt": {}, "herinnerd": [],
        }
        self._purchases: list[dict] = []
        self._purchases_ts = 0.0

    async def async_load_store(self) -> None:
        data = await self._store.async_load()
        if data is None:
            old = await self._old_store.async_load() or {}
            data = {"transactions": old}
        for key, default in (("transactions", {}), ("afvink", []), ("mappings", {}), ("queue", []), ("bonnen", []),
                             ("favorieten", {"vast": [], "verborgen": []}), ("eigen_items", []), ("eigen_init", False),
                             ("afgevinkt", {}), ("herinnerd", []),
                             ("aanbiedingen", {"resultaat": [], "hash": {}, "genegeerd": [], "gemeld": [], "bijgewerkt": None, "fout": {}})):
            self.state[key] = data.get(key, default)

    def save(self) -> None:
        cutoff = time.time() - AFVINK_BEWAARTIJD
        self.state["afvink"] = [a for a in self.state["afvink"] if a.get("ts", 0) >= cutoff]
        self.state["bonnen"] = self.state["bonnen"][:30]
        self._store.async_delay_save(lambda: self.state, 2)

    @property
    def _transactions(self) -> dict[str, str]:
        return self.state["transactions"]

    async def _async_update_data(self) -> GrocyData:
        try:
            products = await self.api.get("/objects/products")
            groups = await self.api.get("/objects/product_groups")
            lists = await self.api.get("/objects/shopping_lists")
            locations = await self.api.get("/objects/shopping_locations")
            items = await self.api.get("/objects/shopping_list")
        except GrocyError as err:
            raise UpdateFailed(str(err)) from err
        data = GrocyData.from_raw(products, groups, lists, locations, items)
        if time.time() - self._purchases_ts > FAVORIETEN_VERVERS:
            try:
                since = (datetime.now() - timedelta(weeks=FAVORIETEN_WEKEN)).strftime("%Y-%m-%d")
                self._purchases = await self.api.get(
                    "/objects/stock_log?query%5B%5D=transaction_type%3Dpurchase"
                    f"&query%5B%5D=undone%3D0&query%5B%5D=purchased_date%3E%3D{since}"
                ) or []
                self._purchases_ts = time.time()
            except GrocyError as err:
                _LOGGER.debug("Aankoophistorie ophalen mislukt: %s", err)
        await self._verkeerde_winkel(data)
        known = {str(i.id) for i in data.items}
        stale = [k for k in self._transactions if k not in known]
        for k in stale:
            self._transactions.pop(k, None)
        if self._afgevinkt_bijwerken(data) or stale:
            self.save()
        return data

    def _afgevinkt_bijwerken(self, data: GrocyData) -> bool:
        """Bijhouden sinds wanneer een item in het mandje zit (ook als het buiten de pagina is afgevinkt)."""
        av = self.state.setdefault("afgevinkt", {})
        nu = time.time()
        gewijzigd = False
        in_mandje = {str(i.id) for i in data.items if i.done and str(i.id) not in self._transactions}
        for k in list(av):
            if k not in in_mandje:
                av.pop(k)
                gewijzigd = True
        for k in in_mandje - set(av):
            av[k] = nu
            gewijzigd = True
        return gewijzigd

    async def _verkeerde_winkel(self, data: GrocyData) -> None:
        """Items die Grocy zelf toevoegt (bijv. 'automatisch onder minimumvoorraad op de lijst') komen altijd op
        de eerste lijst. Zulke items verhuizen naar de lijst van de winkel van het product. Wat je zelf (via deze
        integratie) op een andere lijst zet, blijft staan."""
        eigen = set(self.state.setdefault("eigen_items", []))
        eigen |= self.api.aangemaakt
        if not self.state.get("eigen_init"):
            # eerste keer: alles wat er nu staat geldt als bewust zo neergezet
            eigen |= {i.id for i in data.items}
            self.state["eigen_init"] = True
        verplaatst = False
        for i in data.items:
            if i.id in eigen or i.done or not i.product_id:
                continue
            doel = data.list_for_product(i.product_id)
            if doel != i.list_id and doel in data.lists:
                try:
                    await self.api.update_list_item(i.id, {"shopping_list_id": doel})
                    i.list_id = doel
                    verplaatst = True
                    _LOGGER.debug("Item %s verplaatst naar lijst %s", i.name, doel)
                except GrocyError as err:
                    _LOGGER.debug("Item verplaatsen mislukt: %s", err)
            eigen.add(i.id)  # één keer corrigeren; daarna mag je het zelf verplaatsen
        bestaand = {i.id for i in data.items}
        nieuw = sorted(eigen & bestaand)
        if nieuw != self.state["eigen_items"] or verplaatst:
            self.state["eigen_items"] = nieuw
            self.save()

    # Lijst-acties ------------------------------------------------------------
    async def add_text(self, list_id: int, text: str, note: str | None = None, done: bool = False) -> int | None:
        """Vrije tekst toevoegen: '2 melk' wordt product Melk, aantal 2. done=True: meteen in het mandje."""
        name, amount = parse_entry(text)
        product = match_product(name, list(self.data.products.values()))
        if product:
            return await self.add_product(list_id, int(product["id"]), amount, note, done)
        item_id = await self.api.add_list_item(list_id, None, amount, note or name)
        if done and item_id:
            await self._in_mandje(item_id)
        return item_id

    async def add_product(self, list_id: int, product_id: int, amount: float, note: str | None = None,
                          done: bool = False) -> int | None:
        if done:
            # vergeten af te vinken: als los item in het mandje (een open item met hetzelfde product blijft staan)
            item_id = await self.api.add_list_item(list_id, product_id, amount, note)
            if item_id:
                await self._in_mandje(item_id)
            return item_id
        existing = self.data.open_item_for_product(list_id, product_id)
        if existing:
            await self.api.update_list_item(existing.id, {"amount": existing.amount + amount})
            return existing.id
        return await self.api.add_list_item(list_id, product_id, amount, note)

    async def _in_mandje(self, item_id: int) -> None:
        self.state.setdefault("afgevinkt", {})[str(item_id)] = time.time()
        await self.api.update_list_item(item_id, {"done": 1})
        self.save()

    async def set_amount(self, item_id: int, amount: float) -> None:
        if amount <= 0:
            await self.delete_items([item_id])
        else:
            await self.api.update_list_item(item_id, {"amount": amount})

    async def set_done(self, item_id: int, done: bool) -> None:
        """Afvinken zet het item alleen in je mandje; de voorraad wordt pas geboekt bij afronden()."""
        item = next((i for i in self.data.items if i.id == item_id), None)
        if item is None:
            return
        key = str(item_id)
        if done:
            self.state.setdefault("afgevinkt", {}).setdefault(key, time.time())
            await self.api.update_list_item(item_id, {"done": 1})
        else:
            self.state.setdefault("afgevinkt", {}).pop(key, None)
            tx = self._transactions.pop(key, None)
            if tx:
                # item van vóór de afrekenwerkwijze dat al geboekt was: die boeking terugdraaien
                try:
                    await self.api.undo_transaction(tx)
                except GrocyError as err:
                    _LOGGER.warning("Kon aankoop van %s niet ongedaan maken: %s", item.name, err)
                self.state["afvink"] = [a for a in self.state["afvink"] if a["tx"] != tx]
            await self.api.update_list_item(item_id, {"done": 0})
        self.save()

    async def afronden(self, list_id: int) -> dict:
        """Einde van de winkelsessie: alles in het mandje in één keer in de voorraad boeken en van de lijst halen."""
        mandje = [i for i in self.data.items if i.list_id == list_id and i.done]
        loc = self.data.location_for_list(list_id)
        vandaag = datetime.now().strftime("%Y-%m-%d")
        geboekt, zonder_product, fouten = 0, 0, []
        for item in mandje:
            key = str(item.id)
            if item.product_id and key not in self._transactions:
                try:
                    tx = await self.api.book_purchase(item.product_id, item.amount, loc)
                except GrocyError as err:
                    _LOGGER.error("Boeken van %s mislukt: %s", item.name, err)
                    fouten.append(item.name)
                    continue  # blijft in het mandje, zodat je het opnieuw kunt proberen
                if tx:
                    self.state["afvink"].append({
                        "tx": tx, "product_id": item.product_id, "amount": item.amount,
                        "list_id": list_id, "ts": time.time(), "datum": vandaag, "prijs": None,
                    })
                geboekt += 1
            elif not item.product_id:
                zonder_product += 1
            await self.api.delete_list_item(item.id)
            self._transactions.pop(key, None)
            self.state.setdefault("afgevinkt", {}).pop(key, None)
        self.state["herinnerd"] = [x for x in self.state.get("herinnerd", []) if x != list_id]
        self.save()
        return {"geboekt": geboekt, "zonder_product": zonder_product, "fouten": fouten}

    def vergeten_mandjes(self) -> list[dict]:
        """Lijsten met items die al langer dan VERGETEN_NA in het mandje zitten en waarvoor nog geen melding is gestuurd."""
        av = self.state.get("afgevinkt", {})
        nu = time.time()
        out = []
        for lid, lst in self.data.lists.items():
            mandje = [i for i in self.data.items if i.list_id == lid and i.done and str(i.id) in av]
            if not mandje or lid in self.state.get("herinnerd", []):
                continue
            if nu - min(av[str(i.id)] for i in mandje) >= VERGETEN_NA:
                out.append({"list_id": lid, "naam": lst["name"], "aantal": len(mandje)})
        return out

    def herinnering_verstuurd(self, list_id: int) -> None:
        self.state.setdefault("herinnerd", []).append(list_id)
        self.save()

    async def rename(self, item_id: int, text: str) -> None:
        item = next((i for i in self.data.items if i.id == item_id), None)
        if item is None:
            return
        name, amount = parse_entry(text)
        if item.product_id:
            if amount != item.amount:
                await self.api.update_list_item(item_id, {"amount": amount})
        else:
            await self.api.update_list_item(item_id, {"note": name, "amount": amount})

    async def delete_items(self, item_ids: list[int]) -> None:
        for item_id in item_ids:
            await self.api.delete_list_item(item_id)
            self._transactions.pop(str(item_id), None)
            self.state.setdefault("afgevinkt", {}).pop(str(item_id), None)
        self.save()

    async def clear_done(self, list_id: int) -> int:
        done = [i.id for i in self.data.items if i.list_id == list_id and i.done]
        await self.delete_items(done)
        return len(done)

    def favorites(self) -> dict[int, list[dict]]:
        f = self.state["favorieten"]
        return compute_favorites(self._purchases, self.data, f.get("vast"), f.get("verborgen"))

    def set_favorite(self, product_id: int, actie: str) -> None:
        f = self.state["favorieten"]
        vast = [p for p in f.get("vast", []) if p != product_id]
        verborgen = [p for p in f.get("verborgen", []) if p != product_id]
        if actie == "vast":
            vast.append(product_id)
        elif actie == "verberg":
            verborgen.append(product_id)
        self.state["favorieten"] = {"vast": vast, "verborgen": verborgen}
        self.save()

    async def add_weekly(self) -> int:
        """Vaste verse producten (userfield 'wekelijks') op de juiste lijst zetten."""
        added = 0
        for product, qty in self.data.weekly_products():
            pid = int(product["id"])
            list_id = self.data.list_for_product(pid)
            if self.data.open_item_for_product(list_id, pid):
                continue
            await self.api.add_list_item(list_id, pid, qty, NOTE_WEEKVAST)
            added += 1
        return added

    async def add_missing(self) -> int:
        """Producten onder minimumvoorraad op de lijst van hun winkel zetten."""
        vol = await self.api.get("/stock/volatile")
        added = 0
        for m in (vol or {}).get("missing_products", []):
            pid = int(m["id"])
            if pid not in self.data.products:
                continue
            list_id = self.data.list_for_product(pid)
            if self.data.open_item_for_product(list_id, pid):
                continue
            amount = float(m.get("amount_missing") or 1)
            await self.api.add_list_item(list_id, pid, max(amount, 1), NOTE_TEKORT)
            added += 1
        return added
