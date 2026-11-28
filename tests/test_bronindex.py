"""Bronindex: samenvatting van een receptpagina, vega-gok, kandidaten kiezen en wat nog opgehaald moet worden."""
import json

from custom_components.boodschappen import bronindex as bix
from custom_components.boodschappen.bronnen import url_sleutel


def pagina(ld: dict) -> str:
    return f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head><body></body></html>'


def test_index_item_en_vega():
    item = bix.index_item(pagina({
        "@type": "Recipe", "name": "Roasted pumpkin with feta", "totalTime": "PT40M", "recipeYield": "4 servings",
        "image": ["https://x.nl/p.jpg"],
        "recipeIngredient": ["1 kg pumpkin, cut into wedges", "2 tbsp olive oil", "150g feta (crumbled)", "1 red chilli"],
        "recipeInstructions": "Roast."}))
    assert item["t"] == "Roasted pumpkin with feta" and item["tijd"] == 40 and item["p"] == 4
    assert item["v"] is True and item["f"] == "https://x.nl/p.jpg"
    assert item["i"][:3] == ["pumpkin", "olive oil", "feta"]
    vlees = bix.index_item(pagina({"@type": "Recipe", "name": "Kip tandoori", "recipeIngredient": ["500 g kipdijfilet", "yoghurt"],
                                   "recipeInstructions": "Bak."}))
    assert vlees["v"] is False
    assert bix.index_item("<html>geen recept</html>") is None


def test_namen_in_en_score():
    item = {"t": "Pasta met courgette", "i": ["courgettes", "rode linzen", "parmezaan"]}
    assert bix.namen_in(item, ["Courgette", "Rode linzen", "Melk"]) == ["Courgette", "Rode linzen"]
    assert bix.score_item(item["t"], item, ["courgette"]) == 2
    assert bix.score_item(item["t"], item, ["linzen"]) == 1


def test_index_kandidaten_filtert_en_sorteert():
    links = [f"https://s.nl/r/{n}" for n in ("snelle-pasta", "lange-stoofpot", "geen-recept", "al-bewaard", "onbekend-gerecht", "courgette-soep")]
    d = {
        links[0]: {"t": "Snelle pasta", "tijd": 20, "i": ["pasta"]},
        links[1]: {"t": "Stoofpot", "tijd": 180, "i": ["rundvlees"]},
        links[2]: bix.GEEN_RECEPT,
        links[5]: {"t": "Soep", "tijd": 30, "i": ["courgette", "ui"]},
    }
    klaar = {url_sleutel(links[3])}
    uit = bix.index_kandidaten(links, d, ["courgette"], klaar, url_sleutel, 10, max_tijd=30, zaad=1)
    assert links[2] not in uit and links[3] not in uit and links[1] not in uit  # geen recept, al bewaard, te lang
    assert uit[0] == links[5]  # past bij de zoekvraag via de ingrediënten
    assert set(uit) == {links[0], links[4], links[5]}
    # bonus: product in de aanbieding telt mee
    uit = bix.index_kandidaten(links, d, [], set(), url_sleutel, 10, bonus=["Pasta"], zaad=1)
    assert uit[0] == links[0]


def test_te_indexeren_en_status():
    links = [f"https://s.nl/{i}" for i in range(10)]
    d = {links[0]: {"t": "A", "i": ["x"]}, links[1]: bix.GEEN_RECEPT, links[2]: {"fout": "HTTP 403", "ts": 0}}
    todo = bix.te_indexeren(links, d, nu=10, max_recepten=3)
    assert todo == links[3:7]  # ruimte 2 recepten → 4 pagina's proberen; mislukte pas na een maand opnieuw
    assert bix.te_indexeren(links, d, nu=bix.FOUT_OPNIEUW + 10, max_recepten=3)[0] == links[2]
    st = bix.status({"links": links, "d": d, "ts": 5, "klaar": True})
    assert (st["links"], st["recepten"], st["geen"], st["fout"]) == (10, 1, 1, 1)


def test_link_regel():
    r = bix.link_regel("L1", "Soep", "Ottolenghi", {"t": "Soep", "tijd": 25, "v": True, "i": ["ui", "linzen"]})
    assert r == "L1. Soep (Ottolenghi) | 25 min | vega | ingrediënten: ui, linzen"
    assert bix.link_regel("L2", "Iets", "X", None) == "L2. Iets (X)"
