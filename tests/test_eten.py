import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.boodschappen.eten import (  # noqa: E402
    benodigd,
    event_beschrijving,
    gemiddelden,
    jsonld_naar_tekst,
    marker_uit,
    recept_uit_jsonld,
    schaal,
    schoon_recept,
    week_start,
    weekmenu_prompt,
    weekdag_fouten,
)


def test_schaal():
    assert schaal(1, 1) == 1
    assert schaal(1, 0.5) == 1          # nooit minder dan 1 verpakking
    assert schaal(2, 1.5) == 3
    assert schaal(1, 1.1) == 1          # kleine overschrijding: niet een extra pak
    assert schaal(1, 1.25) == 2
    assert schaal(None, 2) == 0


def test_week_start():
    assert week_start(date(2026, 10, 11)) == date(2026, 10, 5)   # zondag -> maandag ervoor
    assert week_start(date(2026, 10, 12)) == date(2026, 10, 12)


def test_marker_roundtrip():
    item = {"id": "a1b2c3d4e5f6", "kookt": "Papa", "porties": 6, "vriezer": 2}
    txt = event_beschrijving(item, {"tijd": 30}, "https://ha.example/eten?recept=x")
    assert "Kookt: Papa" in txt and "2 naar de vriezer" in txt and "30 min" in txt
    assert marker_uit(txt) == "a1b2c3d4e5f6"
    assert marker_uit("Gewoon een afspraak") is None
    assert marker_uit(None) is None


def test_schoon_recept():
    r = schoon_recept({
        "naam": "  Wraps ", "porties": "4", "tijd": "25", "vega": 1,
        "ingredienten": ["2 paprika", {"tekst": "zout", "basis": True}, {"tekst": "", "product_id": 3},
                         {"tekst": "1 pak wraps", "product_id": "1", "aantal": "1,5"}],
        "stappen": ["Snijd", " ", "Bak"],
    })
    assert r["naam"] == "Wraps" and r["porties"] == 4 and r["tijd"] == 25 and r["vega"] is True
    assert [i["tekst"] for i in r["ingredienten"]] == ["2 paprika", "zout", "1 pak wraps"]
    assert r["ingredienten"][2] == {"tekst": "1 pak wraps", "product_id": 1, "aantal": 1.5, "basis": False}
    assert r["stappen"] == ["Snijd", "Bak"]
    # bijwerken behoudt het id
    r2 = schoon_recept({"naam": "Wraps vega"}, r)
    assert r2["id"] == r["id"] and r2["naam"] == "Wraps vega"


def test_benodigd_schaal_en_basis():
    recepten = {
        "r1": {"id": "r1", "naam": "Chili", "porties": 4, "ingredienten": [
            {"tekst": "2 blikken bonen", "product_id": 10, "aantal": 2},
            {"tekst": "zout", "product_id": 11, "aantal": 1, "basis": True},
            {"tekst": "1 ui", "product_id": 15, "aantal": 1},
            {"tekst": "koriander", "product_id": None, "aantal": 1},
        ]},
        "r2": {"id": "r2", "naam": "Soep", "porties": 4, "ingredienten": [{"tekst": "ui", "product_id": 15, "aantal": 2}]},
    }
    items = [
        {"recept_id": "r1", "porties": 8},                         # dubbele portie
        {"recept_id": "r2", "porties": 4},
        {"recept_id": "r2", "porties": 4, "boodschappen": False},  # uit de vriezer
        {"recept_id": None, "titel": "Pizza"},
    ]
    n = benodigd(items, recepten)
    assert n[10]["aantal"] == 4
    assert 11 not in n
    assert n[15]["aantal"] == 4 and n[15]["voor"] == ["Chili", "Soep"]


def test_gemiddelden():
    b = [
        {"recept_id": "r1", "persoon": "A", "score": 5, "datum": "2026-10-01"},
        {"recept_id": "r1", "persoon": "B", "score": 3, "datum": "2026-10-01"},
        {"recept_id": "r1", "persoon": "A", "score": 4, "datum": "2026-10-08"},
        {"recept_id": None, "persoon": "A", "score": 1},
    ]
    g = gemiddelden(b)
    assert g["r1"]["gem"] == 4.0 and g["r1"]["personen"] == {"A": 4.5, "B": 3.0} and g["r1"]["keer"] == 2


PAGE = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"WebPage"},{"@type":["Recipe"],"name":"Linzensoep",
"recipeYield":["4 personen"],"totalTime":"PT35M","recipeIngredient":["250 g rode linzen","1 ui"],
"recipeInstructions":[{"@type":"HowToSection","itemListElement":[{"@type":"HowToStep","text":"Fruit de ui."}]},
{"@type":"HowToStep","text":"Kook de linzen &amp; mix."}]}]}
</script></head><body>...</body></html>"""


def test_jsonld():
    r = recept_uit_jsonld(PAGE)
    assert r and r["name"] == "Linzensoep"
    t = jsonld_naar_tekst(r)
    assert "Porties: 4 personen" in t and "- 250 g rode linzen" in t
    assert "1. Fruit de ui." in t and "2. Kook de linzen & mix." in t
    assert recept_uit_jsonld("<html>geen recept</html>") is None


def test_weekdag_fouten():
    dagen = [{"datum": "2026-10-12", "dag": "maandag"}, {"datum": "2026-10-13", "dag": "dinsdag"}]
    goed = [{"datum": "2026-10-12", "dag": "maandag"}, {"datum": "2026-10-13", "dag": "Dinsdag"}]
    assert weekdag_fouten(goed, dagen) == ""
    fout = weekdag_fouten([{"datum": "2026-10-12", "dag": "zondag"}, {"datum": "2026-10-13", "dag": "dinsdag"}], dagen)
    assert "2026-10-12 is een maandag" in fout
    assert "precies" in weekdag_fouten([{"datum": "2026-10-12", "dag": "maandag"}], dagen)


def test_weekmenu_prompt_bevat_vaste_dagen():
    p = weekmenu_prompt([{"datum": "2026-10-15", "dag": "donderdag", "vast": "Bij opa"}],
                        [{"id": "r1", "naam": "Chili"}], "Woensdag kookt A", "iets met vis", date(2026, 10, 11))
    assert "Bij opa" in p and "Woensdag kookt A" in p and "iets met vis" in p and "zondag" in p
