"""Pure logica (zonder Home Assistant) - los te testen."""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re
import unicodedata

_AMOUNT_FRONT = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(?:x|×|stuks?|st\.?)?\s+(.+)$", re.I)
_AMOUNT_BACK = re.compile(r"^(.+?)\s+(?:x|×)?\s*(\d+(?:[.,]\d+)?)\s*(?:x|×|stuks?|st\.?)?\s*$", re.I)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_entry(text: str) -> tuple[str, float]:
    """'2 melk' / 'melk 2' / 'melk x2' -> ('melk', 2). Zonder getal: aantal 1."""
    text = text.strip()
    m = re.match(r"^(.+?)\s*\((\d+(?:[.,]\d+)?)\)\s*$", text)  # 'Eieren (12)', zoals de lijst het toont
    if m:
        return m.group(1).strip(), float(m.group(2).replace(",", "."))
    m = _AMOUNT_FRONT.match(text)
    if m:
        return m.group(2).strip(), float(m.group(1).replace(",", "."))
    m = _AMOUNT_BACK.match(text)
    if m and not re.search(r"\d", m.group(1)[-1:]):
        return m.group(1).strip(), float(m.group(2).replace(",", "."))
    return text, 1.0


def _plural_match(q: str, n: str) -> bool:
    """Eenvoudige Nederlandse enkelvoud/meervoud-check."""
    if q == n:
        return True
    for a, b in ((q, n), (n, q)):
        for suffix in ("en", "s", "n", "'s", "eren"):
            if a + suffix == b:
                return True
        # klinkerverdubbeling: banaan -> bananen, peer -> peren, tomaat -> tomaten
        m = re.match(r"^(.*)([aeou])\2([a-z])$", a)
        if m and f"{m.group(1)}{m.group(2)}{m.group(3)}en" == b:
            return True
    return False


def match_product(name: str, products: list[dict], threshold: float = 0.78) -> dict | None:
    """Zoek het best passende Grocy-product bij vrije tekst."""
    q = normalize(name)
    if not q:
        return None
    best, best_score = None, 0.0
    for p in products:
        if str(p.get("active", 1)) == "0":
            continue
        n = normalize(p["name"])
        if not n:
            continue
        if n == q:
            return p
        score = SequenceMatcher(None, q, n).ratio()
        words = n.split()
        if _plural_match(q, n):
            # 'ui' -> 'Uien', 'banaan' -> 'Bananen', 'appel' -> 'Appels'
            score = max(score, 0.97)
        elif q == words[0]:
            # 'melk' -> 'Melk (houdbaar)', 'kaas' -> 'Kaas jong (plakken)'
            score = max(score, 0.92 - 0.02 * (len(words) - 1))
        elif q in words and len(q) >= 4:
            # 'kaas' -> 'Goudse kaas'; korte woorden ('ui' in 'lente-ui') tellen niet
            score = max(score, 0.82 - 0.02 * (len(words) - 1))
        elif n.startswith(q) and len(q) >= 4:
            score = max(score, 0.84)
        if score > best_score:
            best, best_score = p, score
    return best if best_score >= threshold else None


def fmt_amount(amount: float) -> str:
    return str(int(amount)) if float(amount).is_integer() else f"{amount:g}".replace(".", ",")


@dataclass
class ListItem:
    id: int
    list_id: int
    product_id: int | None
    note: str | None
    amount: float
    done: bool
    product_name: str | None = None
    group_name: str | None = None

    @property
    def name(self) -> str:
        if self.product_name:
            return self.product_name
        return (self.note or "").strip() or "?"

    @property
    def summary(self) -> str:
        if self.amount and float(self.amount) != 1:
            return f"{self.name} ({fmt_amount(self.amount)})"
        return self.name

    @property
    def description(self) -> str | None:
        parts = []
        if self.group_name:
            parts.append(self.group_name)
        if self.product_name and self.note:
            parts.append(self.note.strip())
        return " · ".join(parts) or None

    @property
    def sort_key(self) -> tuple:
        group = self.group_name or "99 Overig"
        return (group, self.name.lower())


@dataclass
class GrocyData:
    products: dict[int, dict] = field(default_factory=dict)
    groups: dict[int, dict] = field(default_factory=dict)
    lists: dict[int, dict] = field(default_factory=dict)
    locations: dict[int, dict] = field(default_factory=dict)
    items: list[ListItem] = field(default_factory=list)

    @classmethod
    def from_raw(cls, products: list, groups: list, lists: list, locations: list, items: list) -> "GrocyData":
        d = cls(
            products={int(p["id"]): p for p in products},
            groups={int(g["id"]): g for g in groups},
            lists={int(l["id"]): l for l in lists},
            locations={int(l["id"]): l for l in locations},
        )
        for i in items:
            pid = int(i["product_id"]) if i.get("product_id") else None
            p = d.products.get(pid) if pid else None
            g = d.groups.get(int(p["product_group_id"])) if p and p.get("product_group_id") else None
            d.items.append(ListItem(
                id=int(i["id"]),
                list_id=int(i.get("shopping_list_id") or 1),
                product_id=pid,
                note=i.get("note"),
                amount=float(i.get("amount") or 1),
                done=str(i.get("done", 0)) == "1",
                product_name=p["name"] if p else None,
                group_name=g["name"] if g else None,
            ))
        return d

    def items_for_list(self, list_id: int) -> list[ListItem]:
        return sorted((i for i in self.items if i.list_id == list_id), key=lambda i: (i.done, i.sort_key))

    def location_for_list(self, list_id: int) -> int | None:
        """Grocy-winkel (shopping location) met dezelfde naam als de lijst."""
        lst = self.lists.get(list_id)
        if not lst:
            return None
        for loc_id, loc in self.locations.items():
            if normalize(loc["name"]) == normalize(lst["name"]):
                return loc_id
        return None

    def list_for_product(self, product_id: int) -> int:
        """Lijst die hoort bij de standaardwinkel van het product (anders de eerste lijst)."""
        p = self.products.get(product_id) or {}
        loc = self.locations.get(int(p["shopping_location_id"])) if p.get("shopping_location_id") else None
        if loc:
            for list_id, lst in self.lists.items():
                if normalize(lst["name"]) == normalize(loc["name"]):
                    return list_id
        return min(self.lists) if self.lists else 1

    def open_item_for_product(self, list_id: int, product_id: int) -> ListItem | None:
        for i in self.items:
            if i.list_id == list_id and i.product_id == product_id and not i.done:
                return i
        return None

    def weekly_products(self) -> list[tuple[dict, float]]:
        out = []
        for p in self.products.values():
            uf = p.get("userfields") or {}
            try:
                qty = float(uf.get("wekelijks") or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty > 0 and str(p.get("active", 1)) != "0":
                out.append((p, qty))
        return out


def compute_favorites(
    purchases: list[dict],
    data: "GrocyData",
    pinned: list[int] | None = None,
    hidden: list[int] | None = None,
    limit: int = 12,
    min_days: int = 2,
) -> dict[int, list[dict]]:
    """'Vaak gekocht' per lijst, uit aankoopregels (stock_log purchases).

    - frequentie = aantal verschillende aankoopdagen
    - gebruikelijk aantal = mediaan van de hoeveelheid per aankoopdag
    - vastgepinde producten staan altijd bovenaan, verborgen producten nooit
    - producten die al open op een lijst staan of 'weekvast' zijn, worden overgeslagen
    """
    pinned = [int(p) for p in (pinned or [])]
    hidden = {int(p) for p in (hidden or [])}
    per_day: dict[int, dict[str, float]] = {}
    for row in purchases:
        try:
            pid = int(row["product_id"])
            amount = float(row.get("amount") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if amount <= 0 or str(row.get("undone", 0)) not in ("0", "False", "false"):
            continue
        day = str(row.get("purchased_date") or row.get("row_created_timestamp") or "")[:10]
        days = per_day.setdefault(pid, {})
        days[day] = days.get(day, 0.0) + amount

    on_list = {i.product_id for i in data.items if i.product_id and not i.done}
    weekly = {int(p["id"]) for p, _ in data.weekly_products()}

    def usual(pid: int) -> float:
        vals = sorted(per_day.get(pid, {}).values())
        if not vals:
            return 1.0
        mid = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
        return max(1.0, round(mid))

    candidates: list[tuple[int, int, int]] = []  # (pinned_rank, -frequency, pid)
    for pid in set(per_day) | set(pinned):
        p = data.products.get(pid)
        if not p or str(p.get("active", 1)) == "0" or pid in hidden or pid in on_list:
            continue
        freq = len(per_day.get(pid, {}))
        is_pinned = pid in pinned
        if not is_pinned and (freq < min_days or pid in weekly):
            continue
        rank = pinned.index(pid) if is_pinned else 10_000
        candidates.append((rank, -freq, pid))

    out: dict[int, list[dict]] = {lid: [] for lid in data.lists}
    for rank, negfreq, pid in sorted(candidates, key=lambda c: (c[0], c[1], data.products[c[2]]["name"].lower())):
        lid = data.list_for_product(pid)
        bucket = out.setdefault(lid, [])
        if len(bucket) >= limit and rank == 10_000:
            continue
        bucket.append({
            "id": pid, "name": data.products[pid]["name"], "amount": usual(pid),
            "count": -negfreq, "pinned": rank != 10_000,
        })
    return out
