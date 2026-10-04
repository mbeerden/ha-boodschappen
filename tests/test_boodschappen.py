import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.boodschappen.api import build_base_url  # noqa: E402
from custom_components.boodschappen.logic import GrocyData, match_product, parse_entry  # noqa: E402

RAW = ("1~Wraps volkoren~1~1|2~Tomatenblokjes~1~1|3~Pasta~1~1|4~Kokosmelk~1~1|5~Pesto~1~1|7~Paprika~2~1|8~Bananen~2~1|"
       "9~Veldsla~2~1|10~Komkommer~2~1|12~Snacktomaten~2~1|14~Tomaten~2~1|15~Uien~2~1|16~Peren~2~1|17~Appels~2~1|"
       "23~Lente-ui~2~1|31~Citroen~2~1|37~Knoflook~2~1|42~Wortels~2~1|43~Hummus naturel~3~1|46~Kipfilet~3~1|"
       "51~Kipstukjes~3~1|65~Koffie~4~1|70~Eieren~5~1|81~Melk (houdbaar)~5~1|83~Cottage cheese~6~1|"
       "84~Griekse yoghurt~6~1|85~Yoghurt~6~1|87~Kaas jong (plakken)~6~1|88~Goudse kaas (stuk, jong belegen)~6~1|"
       "98~Toiletpapier~7~1|104~Vaatwastabletten~7~1|109~Bier~8~1|111~Chips~8~1|146~Citroen prikwater (Jumbo)~8~2|"
       "245~Wasverzachter~7~2")
PRODUCTS = []
for row in RAW.split("|"):
    pid, name, gid, loc = row.split("~")
    PRODUCTS.append({"id": int(pid), "name": name, "product_group_id": int(gid), "shopping_location_id": int(loc),
                     "active": 1, "userfields": {"wekelijks": "6" if pid == "83" else ("1" if pid == "8" else None)}})
GROUPS = [{"id": i, "name": n} for i, n in enumerate(
    ["01 Pasta, rijst & wraps", "02 Groente & fruit", "03 Vlees, vis, brood & beleg", "04 Ontbijt", "05 Houdbaar",
     "06 Zuivel", "07 Schoonmaak & huishoud", "08 Noten, chips, wijn & bier", "09 Diepvries"], start=1)]
LISTS = [{"id": 1, "name": "Lidl"}, {"id": 2, "name": "Jumbo"}]
LOCS = [{"id": 1, "name": "Lidl"}, {"id": 2, "name": "Jumbo"}]


def name_of(text):
    p = match_product(parse_entry(text)[0], PRODUCTS)
    return p["name"] if p else None


@pytest.mark.parametrize("text,expected", [
    ("2 melk", ("melk", 2)), ("melk 2", ("melk", 2)), ("melk x2", ("melk", 2)), ("3x eieren", ("eieren", 3)),
    ("eieren", ("eieren", 1)), ("1,5 kipfilet", ("kipfilet", 1.5)), ("Hummus naturel", ("Hummus naturel", 1)),
])
def test_parse(text, expected):
    assert parse_entry(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("melk", "Melk (houdbaar)"), ("banaan", "Bananen"), ("bananen", "Bananen"), ("ui", "Uien"), ("uien", "Uien"),
    ("peer", "Peren"), ("appel", "Appels"), ("tomaat", "Tomaten"), ("ei", "Eieren"), ("eieren", "Eieren"),
    ("hummus", "Hummus naturel"), ("kaas", "Kaas jong (plakken)"), ("goudse kaas", "Goudse kaas (stuk, jong belegen)"),
    ("wc papier", None), ("toiletpapier", "Toiletpapier"), ("wc", None), ("wasverzachter", "Wasverzachter"),
    ("citroen", "Citroen"), ("lente ui", "Lente-ui"), ("komkomer", "Komkommer"), ("yoghurt", "Yoghurt"),
    ("griekse yoghurt", "Griekse yoghurt"), ("biertje", None), ("appeltaart", None), ("wortel", "Wortels"),
])
def test_match(text, expected):
    assert name_of(text) == expected


def make_data(items):
    return GrocyData.from_raw(PRODUCTS, GROUPS, LISTS, LOCS, items)


def test_items_sorted_by_route_and_done_last():
    d = make_data([
        {"id": 1, "product_id": 70, "amount": 12, "shopping_list_id": 1, "done": 0},
        {"id": 2, "product_id": 8, "amount": 1, "shopping_list_id": 1, "done": 0},
        {"id": 3, "product_id": 3, "amount": 1, "shopping_list_id": 1, "done": 1},
        {"id": 4, "product_id": None, "note": "Appeltaart", "amount": 1, "shopping_list_id": 1, "done": 0},
        {"id": 5, "product_id": 245, "amount": 1, "shopping_list_id": 2, "done": 0},
    ])
    lidl = d.items_for_list(1)
    assert [i.summary for i in lidl] == ["Bananen", "Eieren (12)", "Appeltaart", "Pasta"]
    assert lidl[0].description == "02 Groente & fruit"
    assert [i.name for i in d.items_for_list(2)] == ["Wasverzachter"]


def test_store_mapping_and_weekly():
    d = make_data([])
    assert d.list_for_product(245) == 2
    assert d.list_for_product(8) == 1
    assert d.location_for_list(2) == 2
    weekly = {p["name"]: q for p, q in d.weekly_products()}
    assert weekly == {"Cottage cheese": 6, "Bananen": 1}


def test_base_url():
    assert build_base_url("http://a0d7b954-grocy", 9192) == "http://a0d7b954-grocy:9192/api"
    assert build_base_url("http://192.168.1.5:9192/", None) == "http://192.168.1.5:9192/api"
    assert build_base_url("http://192.168.1.5:9192", 9192) == "http://192.168.1.5:9192/api"
    assert build_base_url("https://x.nl/grocy/api", 443) == "https://x.nl:443/grocy/api"


# --- coordinator-acties met een nep-Grocy ---------------------------------
class FakeApi:
    def __init__(self, items):
        self.items = {i["id"]: dict(i) for i in items}
        self.next_id = 100
        self.calls = []
        self.stock_tx = 0

    async def add_list_item(self, list_id, product_id, amount, note):
        self.calls.append(("add", list_id, product_id, amount, note))
        self.next_id += 1
        self.items[self.next_id] = {"id": self.next_id, "product_id": product_id, "amount": amount,
                                    "shopping_list_id": list_id, "note": note, "done": 0}
        return self.next_id

    async def update_list_item(self, item_id, data):
        self.calls.append(("update", item_id, data))
        self.items[item_id].update(data)

    async def delete_list_item(self, item_id):
        self.calls.append(("delete", item_id))
        self.items.pop(item_id)

    async def book_purchase(self, pid, amount, loc, price=None, purchased_date=None):
        self.stock_tx += 1
        self.calls.append(("purchase", pid, amount, loc) + ((round(price, 4),) if price is not None else ()))
        return f"tx{self.stock_tx}"

    async def price_transaction(self, pid, tx, price):
        self.calls.append(("price", pid, tx, round(price, 4)))
        return 1

    async def create_product(self, name, group_id, loc, template):
        self.calls.append(("create", name, group_id, loc))
        return 999

    async def undo_transaction(self, tx):
        self.calls.append(("undo", tx))

    async def get(self, path):
        assert path == "/stock/volatile"
        return {"missing_products": [{"id": 70, "amount_missing": 8}, {"id": 104, "amount_missing": 0.5}]}


class FakeStore:
    def async_delay_save(self, fn, delay):
        self.saved = fn()


def make_coordinator(items):
    from custom_components.boodschappen.coordinator import BoodschappenCoordinator
    c = BoodschappenCoordinator.__new__(BoodschappenCoordinator)
    c.api = FakeApi(items)
    from custom_components.boodschappen.bon import BonVerwerker
    c._store = FakeStore()
    c.state = {"transactions": {}, "afvink": [], "mappings": {}, "queue": [], "bonnen": [], "favorieten": {"vast": [], "verborgen": []}}
    c._purchases = []
    c._purchases_ts = 0
    c.data = make_data(items)
    c.bon = BonVerwerker(c)

    async def _refresh():
        refresh(c)
    c.async_refresh = _refresh
    return c


def refresh(c):
    c.data = make_data(list(c.api.items.values()))


def run(coro):
    return asyncio.run(coro)


def test_add_text_matches_and_merges():
    c = make_coordinator([{"id": 1, "product_id": 70, "amount": 2, "shopping_list_id": 1, "done": 0}])
    run(c.add_text(1, "6 eieren"))
    assert c.api.items[1]["amount"] == 8  # opgehoogd, geen dubbel item
    run(c.add_text(1, "2 banaan"))
    assert c.api.calls[-1] == ("add", 1, 8, 2.0, None)
    run(c.add_text(1, "appeltaart"))
    assert c.api.calls[-1] == ("add", 1, None, 1.0, "appeltaart")


def test_check_books_purchase_and_uncheck_undoes():
    c = make_coordinator([{"id": 1, "product_id": 245, "amount": 2, "shopping_list_id": 2, "done": 0},
                          {"id": 2, "product_id": None, "note": "Taart", "amount": 1, "shopping_list_id": 1, "done": 0}])
    run(c.set_done(1, True))
    assert ("purchase", 245, 2.0, 2) in c.api.calls
    assert c.api.items[1]["done"] == 1
    assert c._transactions == {"1": "tx1"}
    refresh(c)
    run(c.set_done(1, True))  # nogmaals afvinken: niet dubbel boeken
    assert sum(1 for x in c.api.calls if x[0] == "purchase") == 1
    run(c.set_done(1, False))
    assert ("undo", "tx1") in c.api.calls and c.api.items[1]["done"] == 0 and c._transactions == {}
    run(c.set_done(2, True))  # notitie-item: geen voorraadboeking
    assert sum(1 for x in c.api.calls if x[0] == "purchase") == 1


def test_weekly_and_missing():
    c = make_coordinator([{"id": 1, "product_id": 8, "amount": 1, "shopping_list_id": 1, "done": 0}])
    assert run(c.add_weekly()) == 1  # bananen staan er al, cottage cheese erbij
    assert c.api.calls[-1] == ("add", 1, 83, 6.0, "weekvast")
    refresh(c)
    assert run(c.add_weekly()) == 0
    assert run(c.add_missing()) == 2
    assert ("add", 1, 104, 1, "voorraad laag") in c.api.calls


def test_rename_updates_amount():
    c = make_coordinator([{"id": 1, "product_id": 70, "amount": 2, "shopping_list_id": 1, "done": 0},
                          {"id": 2, "product_id": None, "note": "taart", "amount": 1, "shopping_list_id": 1, "done": 0}])
    run(c.rename(1, "Eieren (12)"))
    run(c.rename(2, "slagroomtaart 2"))
    assert c.api.items[1]["amount"] == 12
    assert c.api.items[2]["note"] == "slagroomtaart" and c.api.items[2]["amount"] == 2


# --- bonnetjes -------------------------------------------------------------
from custom_components.boodschappen.bon import prepare_lines, suggest_factor, mapping_key, _date  # noqa: E402


def test_prepare_lines_discount_and_ignore():
    lines = prepare_lines([
        {"bon": "KIPFILET", "product": "Kipfilet", "aantal": 1, "totaal": "5,49"},
        {"bon": "Korting", "product": "Korting", "aantal": 1, "totaal": -1.00},
        {"bon": "STATIEGELD", "product": "Statiegeld", "aantal": 1, "totaal": 0.15},
        {"bon": "BANANEN", "product": "Bananen", "aantal": 1, "totaal": 1.19},
    ])
    assert [(l["tekst"], l["totaal"], l["status"]) for l in lines] == [
        ("KIPFILET", 4.49, "nieuw"), ("STATIEGELD", 0.15, "genegeerd"), ("BANANEN", 1.19, "nieuw")]
    assert lines[0]["korting"] == -1.0
    assert suggest_factor("VRIJE UITLOOP EIEREN 6ST") == 6 and suggest_factor("Eieren 10 stuks") == 10 and suggest_factor("Bananen") == 1
    assert _date("03-10-2026") == "2026-10-03" and _date("2026-10-03") == "2026-10-03" and _date("onzin") is None


def test_bon_prices_checked_off_and_books_unplanned():
    c = make_coordinator([{"id": 1, "product_id": 8, "amount": 1, "shopping_list_id": 1, "done": 0},
                          {"id": 2, "product_id": 10, "amount": 1, "shopping_list_id": 1, "done": 0}])
    run(c.set_done(1, True))                       # bananen afgevinkt -> tx1 geboekt
    refresh(c)
    res = run(c.bon.verwerk("Lidl", "2026-10-04", "/media/bonnetjes/lidl/a.jpg", [
        {"bon": "BANANEN", "product": "Bananen", "grocy": "Bananen", "aantal": 1, "totaal": 1.19},
        {"bon": "KOMKOMMER", "product": "Komkommer", "grocy": "Komkommer", "aantal": 1, "totaal": 0.79},  # niet afgevinkt
        {"bon": "CHOCO PASEO", "product": "Chocoladepasta", "grocy": "", "aantal": 1, "totaal": 2.49},  # onbekend
    ]))
    purchases = [x for x in c.api.calls if x[0] == "purchase"]
    assert purchases == [("purchase", 8, 1.0, 1), ("purchase", 10, 1.0, 1, 0.79)]   # bananen niet nogmaals geboekt
    assert ("price", 8, "tx1", 1.19) in c.api.calls
    assert c.api.items[2]["done"] == 1             # komkommer op de lijst -> afgevinkt zonder extra boeking
    assert res["aantallen"] == {"prijs": 1, "ongepland": 1, "te koppelen": 1}
    assert len(c.state["queue"]) == 1 and c.state["queue"][0]["tekst"] == "CHOCO PASEO"
    # zelfde bestand nogmaals: overslaan
    assert run(c.bon.verwerk("Lidl", None, "/media/bonnetjes/lidl/a.jpg", []))["overgeslagen"]


def test_queue_koppel_remembers_mapping_with_factor():
    c = make_coordinator([{"id": 1, "product_id": 70, "amount": 12, "shopping_list_id": 1, "done": 0}])
    run(c.set_done(1, True))                       # 12 eieren afgevinkt -> tx1
    refresh(c)
    run(c.bon.verwerk("Lidl", None, "b1", [{"bon": "SCHARRELEIEREN 6ST", "product": "Scharreleieren", "aantal": 2, "totaal": 3.78}]))
    q = c.state["queue"][0]
    assert q["factor"] == 6
    run(c.bon.koppel(q["id"], 70, 6))
    assert ("price", 70, "tx1", 0.315) in c.api.calls      # 3,78 / 12 stuks
    assert not [x for x in c.api.calls if x[0] == "purchase" and x[1] == 70 and len(x) > 4]
    assert c.state["queue"] == []
    assert c.state["mappings"][mapping_key("Lidl", "SCHARRELEIEREN 6ST")]["factor"] == 6
    # volgende bon met dezelfde tekst: automatisch, met factor 6, ongepland (niet afgevinkt)
    run(c.bon.verwerk("Lidl", None, "b2", [{"bon": "SCHARRELEIEREN 6ST", "product": "Scharreleieren", "aantal": 1, "totaal": 1.89}]))
    assert c.api.calls[-1][:3] == ("purchase", 70, 6.0) and c.state["queue"] == []
    regel = c.state["bonnen"][0]["regels"][0]
    assert regel["bron"] == "koppeling" and regel["status"] == "ongepland"


def test_queue_negeer_and_nieuw_product_and_herkoppel():
    c = make_coordinator([])
    run(c.bon.verwerk("Jumbo Centrum", None, "b3", [
        {"bon": "PAPIEREN TAS", "product": "Tas", "aantal": 1, "totaal": 0.25},
        {"bon": "JUMBO WAFELS", "product": "Wafels", "aantal": 1, "totaal": 1.50},
        {"bon": "XYZ SAUS", "product": "Xyz saus", "aantal": 1, "totaal": 2.00},
    ]))
    q = {x["tekst"]: x for x in c.state["queue"]}
    assert set(q) == {"JUMBO WAFELS", "XYZ SAUS"}
    run(c.bon.negeer(q["XYZ SAUS"]["id"]))
    run(c.bon.nieuw_product(q["JUMBO WAFELS"]["id"], "Wafels", 4, 1))
    assert ("create", "Wafels", 4, 2) in c.api.calls       # Jumbo-winkel herkend uit 'Jumbo Centrum'
    assert c.state["queue"] == []
    bon = c.state["bonnen"][0]
    wafel = next(l for l in bon["regels"] if l["tekst"] == "JUMBO WAFELS")
    assert wafel["status"] == "ongepland" and wafel["product_id"] == 999 and wafel["tx"]
    run(c.bon.herkoppel(bon["id"], wafel["id"], 70, 1))
    assert ("undo", "tx1") in c.api.calls
    assert wafel["product_id"] == 70
    # genegeerde tekst wordt de volgende keer stil overgeslagen
    run(c.bon.verwerk("Jumbo", None, "b4", [{"bon": "XYZ SAUS", "product": "Xyz saus", "aantal": 1, "totaal": 2.0}]))
    assert c.state["queue"] == [] and c.state["bonnen"][0]["regels"][0]["status"] == "genegeerd"


def test_multipack_auto_match_goes_to_queue():
    c = make_coordinator([])
    run(c.bon.verwerk("Lidl", None, "b9", [{"bon": "VRIJE UITLOOP EIEREN 6ST", "product": "Eieren", "grocy": "Eieren", "aantal": 2, "totaal": 3.78}]))
    assert c.state["queue"][0]["factor"] == 6 and not [x for x in c.api.calls if x[0] in ("purchase", "price")]


from custom_components.boodschappen.logic import compute_favorites  # noqa: E402


def _p(pid, amount, day):
    return {"product_id": pid, "amount": amount, "purchased_date": day, "undone": 0}


def test_favorites_frequency_amount_and_filters():
    purchases = [
        _p(70, 12, "2026-09-05"), _p(70, 6, "2026-09-12"), _p(70, 6, "2026-09-12"), _p(70, 12, "2026-09-26"),  # eieren: 3 dagen
        _p(3, 1, "2026-09-05"), _p(3, 2, "2026-09-19"),            # pasta: 2 dagen
        _p(65, 1, "2026-09-05"),                                   # koffie: 1 dag -> te weinig
        _p(83, 6, "2026-09-05"), _p(83, 6, "2026-09-12"),          # cottage cheese: weekvast -> niet
        _p(245, 1, "2026-09-05"), _p(245, 1, "2026-09-20"),        # wasverzachter -> Jumbo
        {"product_id": 10, "amount": 1, "purchased_date": "2026-09-05", "undone": 1},  # ongedaan
        _p(10, 1, "2026-09-06"), _p(10, 1, "2026-09-13"),          # komkommer: staat open op lijst -> niet
    ]
    d = make_data([{"id": 1, "product_id": 10, "amount": 1, "shopping_list_id": 1, "done": 0}])
    fav = compute_favorites(purchases, d)
    assert [(f["name"], f["amount"], f["count"]) for f in fav[1]] == [("Eieren", 12, 3), ("Pasta", 2, 2)]
    assert [f["name"] for f in fav[2]] == ["Wasverzachter"]
    # vastpinnen (ook met weinig historie) en verbergen
    fav = compute_favorites(purchases, d, pinned=[65], hidden=[70])
    assert [f["name"] for f in fav[1]] == ["Koffie", "Pasta"] and fav[1][0]["pinned"]


def test_set_favorite_toggles():
    c = make_coordinator([])
    c.set_favorite(70, "vast"); c.set_favorite(3, "verberg")
    assert c.state["favorieten"] == {"vast": [70], "verborgen": [3]}
    c.set_favorite(70, "reset"); c.set_favorite(3, "vast")
    assert c.state["favorieten"] == {"vast": [3], "verborgen": []}
