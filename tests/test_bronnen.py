import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.boodschappen import bronnen as b  # noqa: E402
from custom_components.boodschappen.recept import (  # noqa: E402
    aanpassing_samenvoegen,
    herkomst,
    recept_verschil,
    schoon_bronnen,
)


def test_sitemap_parsing():
    xml = "<urlset><url><loc>https://a.nl/x</loc></url><url><loc><![CDATA[ https://a.nl/y?a=1&amp;b=2 ]]></loc></url><loc>mailto:x</loc></urlset>"
    assert b.sitemap_locs(xml) == ["https://a.nl/x", "https://a.nl/y?a=1&b=2"]
    assert b.is_sitemap_index("<sitemapindex></sitemapindex>") and not b.is_sitemap_index(xml)
    assert b.sitemaps_uit_robots("User-agent: *\nSitemap: https://a.nl/s.xml\nsitemap:https://a.nl/t.xml") == ["https://a.nl/s.xml", "https://a.nl/t.xml"]
    kids = ["https://a.nl/category-sitemap.xml", "https://a.nl/post-sitemap.xml", "https://a.nl/misc.xml", "https://a.nl/product-sitemap.xml"]
    assert b.sitemap_volgorde(kids) == ["https://a.nl/post-sitemap.xml", "https://a.nl/misc.xml"]


def test_receptlink_filter():
    assert b.receptlink_mogelijk("https://a.nl/linzensoep-met-kokos")
    assert not b.receptlink_mogelijk("https://a.nl/")
    assert not b.receptlink_mogelijk("https://a.nl/category/soep")
    assert not b.receptlink_mogelijk("https://a.nl/foto.jpg")
    assert not b.receptlink_mogelijk("https://a.nl/recept?id=1")
    assert not b.receptlink_mogelijk("https://b.nl/linzensoep-met-kokos", {"naam": "A", "domein": "a.nl", "pad": ""})
    assert b.receptlink_mogelijk("https://www.a.nl/linzensoep-met-kokos", {"naam": "A", "domein": "a.nl", "pad": ""})


def test_titel_en_sleutel():
    assert b.titel_uit_url("https://a.nl/x/linzen-soep.html") == "Linzen soep"
    assert b.url_sleutel("https://www.A.nl/x/y/?q=1") == b.url_sleutel("http://a.nl/x/y")


def test_zoek_links():
    urls = ["https://a.nl/linzensoep-met-kokos", "https://a.nl/pasta-pesto", "https://a.nl/rode-linzen-curry", "https://a.nl/taart-appel"]
    uit = b.zoek_links(urls, "iets met linzen", zaad=1)
    assert set(uit) == {urls[0], urls[2]}
    # al toegevoegd valt af
    assert b.zoek_links(urls, "linzen", klaar={b.url_sleutel(urls[0])}, zaad=1) == [urls[2]]
    # zonder vraag: willekeurige greep, begrensd
    assert len(b.zoek_links(urls, "", aantal=2, zaad=1)) == 2
    kand = b.kandidaten_voor_ai(urls, "linzen", max_n=4, zaad=1)
    assert len(kand) == 4 and set(kand[:2]) == {urls[0], urls[2]}


def test_kies_toepassen_negeert_verzinsels():
    kand = [{"n": 1, "titel": "A", "url": "u1"}, {"n": 2, "titel": "B", "url": "u2"}]
    res = [{"n": 2, "reden": "snel"}, {"n": 9, "reden": "verzonnen"}, {"n": 2, "reden": "dubbel"}, "rommel", {"n": "x"}, {"n": 1}]
    uit = b.kies_toepassen(res, kand)
    assert [u["url"] for u in uit] == ["u2", "u1"] and uit[0]["reden"] == "snel"
    assert b.kies_toepassen({"geen": "lijst"}, kand) == []
    prompt = b.kies_prompt(kand, "snel", "ctx", ["Uien"], ["Wraps"])
    assert "1. A" in prompt and "Uien" in prompt and "Wraps" in prompt


def test_site_naam_en_advies():
    assert b.site_naam('<meta property="og:site_name" content="Lekker &amp; Simpel">', "x.nl") == "Lekker & Simpel"
    assert b.site_naam("<title>Soep | Kookblog</title>", "x.nl") == "Soep"
    assert b.site_naam("", "x.nl") == "x.nl"
    assert "Klaar" in b.bron_advies(True, 5) and "geen bruikbare sitemap" in b.bron_advies(True, 0)
    assert "schema.org" in b.bron_advies(False, 3) and "foto" in b.bron_advies(None, 0)


def test_schoon_bronnen_types():
    uit = schoon_bronnen([
        {"naam": "Ottolenghi", "domein": "https://www.ottolenghi.co.uk/pages/recipes"},
        {"naam": "Plenty", "type": "boek", "domein": "negeer.nl", "notitie": "kast"},
        {"naam": "plenty", "type": "boek"},
        {"naam": "Oma", "type": "anders", "aan": False},
        {"naam": "", "domein": ""},
        {"naam": "Kookboek", "type": "website"},
    ])
    namen = [x["naam"] for x in uit]
    assert namen == ["Ottolenghi", "Plenty", "Oma"]
    o, p, oma = uit
    assert o["type"] == "website" and o["domein"] == "ottolenghi.co.uk" and o["pad"] == "/pages/recipes" and o["aan"] is True
    assert p["type"] == "boek" and not p.get("domein") and p["notitie"] == "kast"
    assert oma["aan"] is False
    # tekstvorm
    assert schoon_bronnen("Testkok | www.test.nl/recepten")[0]["pad"] == "/recepten"


def test_herkomst_bron_naam_eerst():
    bronnen = schoon_bronnen([{"naam": "Plenty", "type": "boek"}, {"naam": "Test", "domein": "test.nl"}])
    assert herkomst({"bron_naam": "plenty", "bron": "https://test.nl/x"}, bronnen) == {"soort": "bron", "naam": "Plenty"}
    assert herkomst({"bron": "https://test.nl/x"}, bronnen) == {"soort": "bron", "naam": "Test"}
    assert herkomst({}, bronnen)["soort"] == "onbekend"


OUD = {"naam": "Linzensoep", "porties": 4, "tijd": 35, "vega": True, "tags": ["soep"],
       "ingredienten": [{"tekst": "2 uien", "product_id": 15, "aantal": 2, "basis": False},
                        {"tekst": "250 g rode linzen", "product_id": 3, "aantal": 1},
                        {"tekst": "zout", "product_id": None, "basis": True}],
       "stappen": ["Fruit de ui", "Kook 20 minuten"]}


def test_aanpassing_samenvoegen_behoudt_koppelingen():
    res = {"naam": "Linzensoep met kokos", "ingredienten": [
        {"tekst": "2 uien", "product_id": 99, "aantal": 5},
        {"tekst": "250 g rode linzen", "product_id": None},
        {"tekst": "200 ml kokosmelk", "product_id": 8, "aantal": 1},
        "zout"],
        "stappen": ["Fruit de ui", "Voeg kokosmelk toe", "  "], "wijzigingen": ["Kokosmelk erbij", ""]}
    nieuw = aanpassing_samenvoegen(OUD, res)
    ing = {i["tekst"]: i for i in nieuw["ingredienten"]}
    assert ing["2 uien"]["product_id"] == 15 and ing["2 uien"]["aantal"] == 2  # origineel wint
    assert ing["250 g rode linzen"]["product_id"] == 3
    assert ing["200 ml kokosmelk"]["product_id"] == 8
    assert ing["zout"]["basis"] is True
    assert nieuw["stappen"] == ["Fruit de ui", "Voeg kokosmelk toe"] and nieuw["wijzigingen"] == ["Kokosmelk erbij"]
    assert nieuw["naam"] == "Linzensoep met kokos" and nieuw["porties"] == 4 and nieuw["vega"] is True


@pytest.mark.parametrize("res", [None, [], {"ingredienten": ["een"], "stappen": ["x"]}, {"ingredienten": ["a", "b"], "stappen": []}])
def test_aanpassing_samenvoegen_weigert_halve_recepten(res):
    with pytest.raises(ValueError):
        aanpassing_samenvoegen(OUD, res)


def test_recept_verschil():
    nieuw = {"naam": "Linzensoep met kokos", "porties": 6, "tijd": 35, "stappen": ["Fruit de ui", "Voeg kokosmelk toe"],
             "ingredienten": [{"tekst": "2 uien"}, {"tekst": "400 g rode linzen"}, {"tekst": "200 ml kokosmelk"}]}
    v = recept_verschil(OUD, nieuw)
    assert v["naam"] == ["Linzensoep", "Linzensoep met kokos"] and v["porties"] == [4, 6] and v["tijd"] is None
    assert v["ingredienten"]["erbij"] == ["200 ml kokosmelk"]
    assert v["ingredienten"]["weg"] == ["zout"]
    assert v["ingredienten"]["gewijzigd"] == [{"van": "250 g rode linzen", "naar": "400 g rode linzen"}]
    assert v["stappen_nieuw"] == [False, True]
