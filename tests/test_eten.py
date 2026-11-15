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
    weekdag_fouten,
)
from custom_components.boodschappen.menu import (  # noqa: E402
    aanwezig_op,
    context_tekst,
    controleer_antwoord,
    dag_info,
    feedback_lijsten,
    is_afgewezen,
    labels_voor,
    porties_voor,
    schoon_menu,
    voorraad_overzicht,
    voorstel_prompt,
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




# --- weekmenu-advies ---------------------------------------------------------
def _menu():
    return schoon_menu({
        "profielen": [
            {"naam": "A"}, {"naam": "B", "dieet": "vegetarisch"}, {"naam": "K", "kind": True, "lievelingskost": "soep"},
            {"naam": "S", "patroon": {"soort": "omdeweek", "dagen": [2, 3, 4], "dagen_b": [2, 3, 4, 5], "anker": "2026-11-04"}},
            {"naam": "W", "patroon": {"soort": "dagen", "dagen": [5, 6]}},
        ],
        "weekdagen": [{"soort": "vriezer"}, {"soort": "vriezer"}, {"kookt": "A", "minuten": 40}, {"soort": "anders", "notitie": "Opa kookt"},
                      {"kookt": "B"}, {"minuten": 20, "notitie": "iets makkelijks"}, {"vriezer": 6, "minuten": 60}],
        "stijl": {"apparaten": "stoomoven, airfryer", "voorkeuren": "gezond"},
    })


def test_schoon_menu_en_oude_personen():
    m = schoon_menu({"personen": ["A", "B"]})
    assert m["personen"] == ["A", "B"] and m["profielen"][0]["patroon"]["soort"] == "altijd"
    assert len(m["weekdagen"]) == 7 and m["weekdagen"][0]["soort"] == "koken"
    m = _menu()
    assert m["stijl"]["apparaten"] == ["stoomoven", "airfryer"]
    assert m["profielen"][3]["patroon"]["anker"] == "2026-11-02"   # naar de maandag van die week


def test_aanwezigheid_om_de_week_en_porties():
    m = _menu()
    p = m["profielen"]
    # week van het anker (2 nov) = week A: wo t/m vr; week erna = week B: wo t/m za
    assert aanwezig_op(p, date(2026, 11, 7)) == ["A", "B", "K", "W"]            # zaterdag week A: S niet
    assert aanwezig_op(p, date(2026, 11, 14)) == ["A", "B", "K", "S", "W"]      # zaterdag week B: S wel
    assert "S" in aanwezig_op(p, date(2026, 10, 28))                             # ook terug in de tijd (week A)
    assert porties_voor(p, ["A", "B", "K"]) == 3 and porties_voor(p, ["A", "K"]) == 2
    d = dag_info(m, date(2026, 11, 8))                                            # zondag
    assert d["soort"] == "koken" and d["vriezer"] == 6 and d["minuten"] == 60 and d["dag"] == "zondag"
    assert dag_info(m, date(2026, 11, 5))["soort"] == "anders"


def test_voorraad_labels_en_feedback():
    voorraad, bijna = voorraad_overzicht([
        {"product_id": 1, "amount": 2, "best_before_date": "2026-11-03"},
        {"product_id": 2, "amount": 1, "best_before_date": "2999-12-31"},
        {"product_id": 3, "amount": 0},
    ], date(2026, 11, 1))
    assert voorraad == {1: 2.0, 2: 1.0} and bijna == {1}
    r = {"ingredienten": [{"tekst": "courgette", "product_id": 1}, {"tekst": "rijst", "product_id": 2},
                          {"tekst": "kip", "product_id": 4}, {"tekst": "zout", "product_id": 5, "basis": True}]}
    lab = labels_voor(r, voorraad, bijna, {4: {"actie": "1+1", "winkel": "Lidl"}}, {1: "Courgette", 2: "Rijst", 4: "Kipfilet"})
    assert lab == {"actie": ["Kipfilet (1+1, Lidl)"], "in_huis": ["Rijst"], "bijna_op": ["Courgette"]}
    fb = {"r1": {"recept_id": "r1", "titel": "Curry", "soorten": {"niet_lekker": "2026-11-01"}},
          "titel:wrap": {"recept_id": None, "titel": "Wrap", "soorten": {"lekker_snel": "x", "wel_lekker": "x"}}}
    assert is_afgewezen(fb, "r1", "Curry") and is_afgewezen(fb, None, "curry") and not is_afgewezen(fb, None, "Wrap")
    assert [x["titel"] for x in feedback_lijsten(fb)["lekker_snel"]] == ["Wrap"]


def test_voorstel_prompt_en_controle():
    m = _menu()
    ctx = context_tekst(m, "Pizza op zaterdag", {"r1": {"titel": "Curry", "soorten": {"niet_lekker": "x"}}}, date(2026, 11, 1))
    assert "B; dieet: vegetarisch" in ctx and "K (kind)" in ctx and "stoomoven" in ctx and "november" in ctx
    assert "NIET lekker" in ctx and "Curry" in ctx and "Pizza op zaterdag" in ctx
    dag = {**dag_info(m, date(2026, 11, 8)), "wens": "soep", "agenda": "Curry (3x)"}
    p = voorstel_prompt([dag], [{**dag_info(m, date(2026, 11, 9)), "vast_titel": "Restjes"}], m["profielen"], ctx, "snel",
                        [{"id": "r1", "naam": "Soep"}], {1: "Courgette"}, {1: 1.0}, {1}, {1: {"actie": "-30%", "winkel": "Jumbo"}},
                        date(2026, 11, 1))
    assert "- ZONDAG 2026-11-08 — wij koken" in p and "kook 6 extra porties voor de vriezer" in p and "wens voor deze dag: soep" in p
    assert "eten mee: A, B, K (kind)" in p and "staat al vast: Restjes" in p and "Courgette: -30% bij Jumbo" in p
    assert "Gaat bijna over de datum (eerst opmaken): Courgette" in p and "precies 3 opties" in p
    goed = [{"datum": "2026-11-08", "dag": "zondag", "opties": [{}, {}, {}]}]
    assert controleer_antwoord(goed, [dag]) == ""
    assert "3 opties" in controleer_antwoord([{"datum": "2026-11-08", "dag": "zondag", "opties": [{}]}], [dag])
    assert "is een zondag" in controleer_antwoord([{"datum": "2026-11-08", "dag": "maandag", "opties": [{}, {}, {}]}], [dag])
    assert controleer_antwoord({"x": 1}, [dag])


# ----------------------------------------------------------------- recepten van een bron
from custom_components.boodschappen.recept import (  # noqa: E402
    DEFAULT_BRONNEN,
    duur_minuten,
    is_vreemde_taal,
    herkomst,
    kwaliteit,
    laad_json_soepel,
    niet_in_bereiding,
    recept_van_jsonld,
    schoon_bronnen,
    verrijk_toepassen,
)

OTTOLENGHI = """<html><script type="application/ld+json">
	{
		"@context": "https://schema.org/",
		"@type": "Recipe",
		"name": "Spiced olive pasta",
		"author": {"@type": "Organization", "name": "Ottolenghi"},
		"recipeIngredient": ["200g pasta", "50g unsalted butter", "75g feta cheese",],
		"recipeInstructions": [
			{"@type": "HowToStep", "text": "Cook the pasta."},
			{"@type": "HowToStep", "text": "Melt the butter and stir through."},]}
</script><body>Serves 2 Prep 5 min Cook 10 min</body></html>"""


def test_jsonld_met_komma_te_veel_en_tijd_uit_tekst():
    assert laad_json_soepel('{"a": [1, 2,],}') == {"a": [1, 2]}
    ld = recept_uit_jsonld(OTTOLENGHI)
    r = recept_van_jsonld(ld, "Serves 2\nPrep 5 min\nCook 10 min")
    assert r["naam"] == "Spiced olive pasta" and r["auteur"] == "Ottolenghi"
    assert r["porties"] == 2 and r["tijd"] == 15
    assert r["ingredienten"] == ["200g pasta", "50g unsalted butter", "75g feta cheese"]
    assert r["stappen"] == ["Cook the pasta.", "Melt the butter and stir through."]


def test_recept_van_jsonld_letterlijk():
    r = recept_van_jsonld(recept_uit_jsonld(PAGE))
    assert r["porties"] == 4
    assert "250 g rode linzen" in r["ingredienten"]
    assert r["stappen"] == ["Fruit de ui.", "Kook de linzen & mix."]
    assert duur_minuten("PT1H15M") == 75 and duur_minuten("PT30M") == 30 and duur_minuten("") is None


def test_verrijk_bron_wint():
    basis = {"naam": "Soep", "porties": 4, "tijd": None, "ingredienten": ["1 ui", "2 tl komijn", "zout"],
             "stappen": ["Fruit de ui.", "Voeg komijn toe."]}
    # Nederlands: de AI mag teksten niet vervangen, ook niet als ze het toch probeert
    res = {"taal": "nl", "vega": True, "tags": ["soep"], "tijd": 20, "stappen": ["Samengevat."],
           "ingredienten": [{"tekst": "ui", "product_id": 5, "aantal": 1, "basis": False},
                            {"tekst": "komijn", "product_id": None, "aantal": 1, "basis": True},
                            {"tekst": "zout", "product_id": None, "aantal": 1, "basis": True}]}
    r = verrijk_toepassen(basis, res)
    assert [i["tekst"] for i in r["ingredienten"]] == ["1 ui", "2 tl komijn", "zout"]
    assert r["ingredienten"][0]["product_id"] == 5 and r["ingredienten"][1]["basis"] is True
    assert r["stappen"] == basis["stappen"] and r["tijd"] == 20 and r["vega"] is True
    # de AI laat een ingrediënt weg: niets raakt kwijt, alleen de koppeling ontbreekt
    r = verrijk_toepassen(basis, {"taal": "nl", "ingredienten": res["ingredienten"][:2]})
    assert len(r["ingredienten"]) == 3 and r["ingredienten"][0]["product_id"] is None
    # Engels: vertaling alleen overnemen als het aantal regels klopt
    en = {"naam": "Soup", "ingredienten": ["1 onion", "salt"], "stappen": ["Fry.", "Season."]}
    r = verrijk_toepassen(en, {"taal": "en", "naam": "Soep", "stappen": ["Bak.", "Breng op smaak."],
                               "ingredienten": [{"tekst": "1 ui", "product_id": 5}, {"tekst": "zout", "basis": True}]})
    assert r["naam"] == "Soep" and [i["tekst"] for i in r["ingredienten"]] == ["1 ui", "zout"]
    assert r["stappen"] == ["Bak.", "Breng op smaak."] and r["vertaald_uit"] == "en"
    r = verrijk_toepassen(en, {"taal": "en", "stappen": ["Alles in één."], "ingredienten": []})
    assert r["stappen"] == ["Fry.", "Season."] and [i["tekst"] for i in r["ingredienten"]] == ["1 onion", "salt"]


def test_kwaliteit():
    kaal = {"porties": 6, "ingredienten": [{"tekst": t} for t in (
        "500g volkoren pasta", "700ml passata", "300g wortels", "200g gedroogde linzen", "2 uien", "2 el olijfolie")],
        "stappen": ["Snipper de uien en snijd de wortels.", "Fruit de ui in olijfolie.", "Kook de pasta."]}
    punten = kwaliteit(kaal)
    assert "Geen kruiden of andere smaakmakers" in punten
    assert "Bereiding is erg kort" in punten
    assert any(p.startswith("Niet genoemd in de bereiding") and "passata" in p and "linzen" in p for p in punten)
    assert niet_in_bereiding(kaal) == ["700ml passata", "200g gedroogde linzen"]
    goed = {"porties": 4, "ingredienten": [{"tekst": t} for t in (
        "2 teentjes knoflook", "1 ui", "1 rode paprika", "2 tl komijn", "400 g kidneybonen", "zout en peper")],
        "stappen": ["Snipper de ui en de knoflook en fruit ze een paar minuten in een grote pan tot de ui glazig is.",
                    "Snijd de paprika in blokjes, voeg toe met de komijn en bak twee minuten mee op middelhoog vuur.",
                    "Voeg de kidneybonen toe, laat een kwartier zachtjes pruttelen en breng op smaak."]}
    assert kwaliteit(goed) == []
    assert "Geen bereiding" in kwaliteit({"porties": 2, "ingredienten": goed["ingredienten"], "stappen": []})


def test_bronnen_en_herkomst():
    bronnen = schoon_bronnen(DEFAULT_BRONNEN)
    assert herkomst({"bron": "https://www.brendakookt.nl/recepten/chili-sin-carne/"}, bronnen) == {"soort": "bron", "naam": "Brenda Kookt"}
    assert herkomst({"bron": "https://www.vpro.nl/koken-met-van-boven/artikelen/recept-x"}, bronnen)["naam"] == "Yvette van Boven"
    assert herkomst({"bron": "https://www.vpro.nl/tegenlicht/x"}, bronnen)["soort"] == "web"
    assert herkomst({"bron": "https://www.leukerecepten.nl/x"}, bronnen) == {"soort": "web", "naam": "leukerecepten.nl"}
    assert herkomst({"bron": "Claude (weekmenu okt 2026)"}, bronnen)["soort"] == "ai"
    assert herkomst({"bron": "AI"}, bronnen)["soort"] == "ai"
    assert herkomst({"bron": None, "tags": ["pasta", "nieuw"]}, bronnen)["soort"] == "ai"
    assert herkomst({"bron": None, "tags": ["AGV"]}, bronnen)["soort"] == "onbekend"
    assert herkomst({"bron": None, "eigen": True}, bronnen)["soort"] == "eigen"
    assert schoon_bronnen("Jamie | https://www.jamieoliver.com/recipes/\nmob.co.uk") == [
        {"naam": "Jamie", "domein": "jamieoliver.com", "pad": "/recipes"},
        {"naam": "mob.co.uk", "domein": "mob.co.uk", "pad": ""}]


def test_bereiding_herkent_meervoud_en_samenstellingen():
    r = {"ingredienten": [{"tekst": t} for t in (
        "2 uien", "500 g kastanjechampignons", "1 groentebouillonblokje", "400-500 g rundergehakt", "3 paprika's",
        "250 g snacktomaten", "1 zak nacho chips", "Mosterd (optioneel)", "verse basilicum om te garneren", "2 preserved lemons")],
        "stappen": ["Snipper de ui en bak de champignons.", "Voeg het bouillonblokje en het gehakt toe.",
                    "Snijd de paprika en de tomaatjes."]}
    assert niet_in_bereiding(r) == ["1 zak nacho chips", "2 preserved lemons"]


def test_vreemde_taal_en_opsommingstekens():
    en = {"stappen": ["Preheat the oven to 200°C.", "Cut the courgettes into rounds and add the oil, then roast for 15 minutes."]}
    nl = {"stappen": ["Snipper de ui en fruit in de olie.", "Voeg de linzen toe en laat 10 minuten sudderen met de tomaten."]}
    assert is_vreemde_taal(en) and not is_vreemde_taal(nl) and not is_vreemde_taal({"stappen": []})
    ld = {"@type": "Recipe", "name": "X", "recipeIngredient": ["• 1 grote ui", "▢ 2 el olie", "zout"], "recipeInstructions": "Bak."}
    assert recept_van_jsonld(ld)["ingredienten"] == ["1 grote ui", "2 el olie", "zout"]
    # onvolledige vertaling: de brontekst blijft staan en het recept telt niet als vertaald
    basis = {"naam": "Soup", "ingredienten": ["1 onion", "salt"], "stappen": ["Fry.", "Season."]}
    r = verrijk_toepassen(basis, {"taal": "nl", "ingredienten": [{"tekst": "1 ui"}, {"tekst": "zout"}]}, vertaald=True)
    assert r["stappen"] == ["Fry.", "Season."] and not r.get("vertaald_uit")
    r = verrijk_toepassen(basis, {"ingredienten": [{"tekst": "1 ui"}, {"tekst": "zout"}], "stappen": ["Bak.", "Kruid."]}, vertaald=True)
    assert r["stappen"] == ["Bak.", "Kruid."] and r["vertaald_uit"] == "en"
