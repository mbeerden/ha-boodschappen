"""Kleine async Grocy-client."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import aiohttp


class GrocyError(Exception):
    """Fout bij communicatie met Grocy."""


def build_base_url(url: str, port: int | None) -> str:
    """Maak de API-basis-URL, net zoals de HACS Grocy-integratie dat doet."""
    url = url.rstrip("/")
    if url.endswith("/api"):
        url = url[: -len("/api")]
    parts = urlsplit(url)
    if port and parts.port is None:
        netloc = f"{parts.hostname}:{port}"
        url = f"{parts.scheme}://{netloc}{parts.path}"
    return url + "/api"


class GrocyApi:
    """Minimale wrapper rond de Grocy REST API."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str, verify_ssl: bool = True) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._headers = {"GROCY-API-KEY": api_key, "Accept": "application/json"}
        self._ssl = None if verify_ssl else False

    async def _request(self, method: str, path: str, json: Any = None) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self._base}{path}",
                headers=self._headers,
                json=json,
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise GrocyError(f"{method} {path}: HTTP {resp.status} {text[:200]}")
                if resp.status == 204:
                    return None
                raw = await resp.read()
                if not raw:
                    return None
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise GrocyError(f"{method} {path}: {err}") from err

    async def get(self, path: str) -> Any:
        return await self._request("GET", path)

    async def post(self, path: str, data: Any) -> Any:
        return await self._request("POST", path, data)

    async def put(self, path: str, data: Any) -> Any:
        return await self._request("PUT", path, data)

    async def delete(self, path: str) -> Any:
        return await self._request("DELETE", path)

    # Handige wrappers -------------------------------------------------
    async def system_info(self) -> dict:
        return await self.get("/system/info")

    async def add_list_item(self, list_id: int, product_id: int | None, amount: float, note: str | None) -> int | None:
        data: dict[str, Any] = {"shopping_list_id": list_id, "amount": amount, "done": 0}
        if product_id:
            data["product_id"] = product_id
        if note:
            data["note"] = note
        res = await self.post("/objects/shopping_list", data)
        return int(res["created_object_id"]) if res and "created_object_id" in res else None

    async def update_list_item(self, item_id: int, data: dict) -> None:
        await self.put(f"/objects/shopping_list/{item_id}", data)

    async def delete_list_item(self, item_id: int) -> None:
        await self.delete(f"/objects/shopping_list/{item_id}")

    async def book_purchase(
        self, product_id: int, amount: float, shopping_location_id: int | None,
        price: float | None = None, purchased_date: str | None = None,
    ) -> str | None:
        """Boek een aankoop; geeft het transactie-id terug (voor ongedaan maken)."""
        data: dict[str, Any] = {"amount": amount, "transaction_type": "purchase"}
        if shopping_location_id:
            data["shopping_location_id"] = shopping_location_id
        if price is not None:
            data["price"] = round(price, 4)
        if purchased_date:
            data["purchased_date"] = purchased_date
        res = await self.post(f"/stock/products/{product_id}/add", data)
        if isinstance(res, list) and res:
            return res[0].get("transaction_id")
        if isinstance(res, dict):
            return res.get("transaction_id")
        return None

    async def undo_transaction(self, transaction_id: str) -> None:
        await self.post(f"/stock/transactions/{transaction_id}/undo", {})

    async def price_transaction(self, product_id: int, transaction_id: str, price: float) -> int:
        """Zet achteraf de prijs op de voorraadregels van een (afvink-)aankoop."""
        logs = await self.get(f"/objects/stock_log?query%5B%5D=transaction_id%3D{transaction_id}") or []
        stock_ids = {
            l["stock_id"] for l in logs
            if l.get("transaction_type") == "purchase" and str(l.get("undone", 0)) in ("0", "False", "false")
        }
        if not stock_ids:
            return 0
        entries = await self.get(f"/stock/products/{product_id}/entries") or []
        updated = 0
        for e in entries:
            if e.get("stock_id") in stock_ids:
                await self.put(f"/stock/entry/{e['id']}", {
                    "amount": e["amount"],
                    "best_before_date": e["best_before_date"],
                    "location_id": e["location_id"],
                    "price": round(price, 4),
                    "open": e.get("open", 0),
                    "purchased_date": e.get("purchased_date"),
                })
                updated += 1
        return updated

    async def create_product(self, name: str, group_id: int | None, shopping_location_id: int | None, template: dict) -> int:
        """Nieuw product aanmaken; eenheden en bewaarplaats komen van een vergelijkbaar product."""
        data: dict[str, Any] = {
            "name": name,
            "location_id": template.get("location_id"),
            "qu_id_purchase": template.get("qu_id_purchase"),
            "qu_id_stock": template.get("qu_id_stock"),
            "qu_id_consume": template.get("qu_id_consume") or template.get("qu_id_stock"),
            "qu_id_price": template.get("qu_id_price") or template.get("qu_id_stock"),
            "default_best_before_days": template.get("default_best_before_days") or 0,
        }
        if group_id:
            data["product_group_id"] = group_id
        if shopping_location_id:
            data["shopping_location_id"] = shopping_location_id
        res = await self.post("/objects/products", data)
        return int(res["created_object_id"])
