import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.boodschappen.dagmenu import (  # noqa: E402
    BIBLIOTHEEK,
    max_minuten,
    opties_prompt,
    opties_toepassen,
    recepten_selectie,
    reserveringen,
    schoon_filters,
    verdeel,
    zoekvraag,
)
from custom_components.boodschappen.gemini import delen_uit_chunk, model_naam  # noqa: E402

BRONNEN = ["Ottolenghi", "Brenda Kookt"]


def test_schoon_filters():
    f = schoon_filters({"voorraad": False, "snel": True, "bronnen": ["ottolenghi", "Onbekend", "Ottolenghi"], "wens": " soep "}, BRONNEN)
    assert f == {"voorraad": False, "snel": True, "aanbieding": True, "bronnen": ["Ottolenghi"], "wens": "soep"}
    assert schoon_filters({}, BRONNEN)["bronnen"] == [BIBLIOTHEEK]          # niets gekozen: bibliotheek
    assert schoon_filters({"bronnen": [BIBLIOTHEEK, "Brenda Kookt"]}, BRONNEN)["bronnen"] == [BIBLIOTHEEK, "Brenda Kookt"]
    assert schoon_filters(None, BRONNEN)["voorraad"] is True


def test_max_minuten():
    assert max_minuten(45, False) == 45
    assert max_minuten(45, True) == 30
    assert max_minuten(20, True) == 20
    assert max_minuten(None, False) == 45


def test_recepten_selectie():
    rec = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    herk = {"a": "Ottolenghi", "b": None, "c": "Brenda Kookt"}
    assert recepten_selectie(rec, {"bronnen": [BIBLIOTHEEK]}, herk) == rec
    # alleen Ottolenghi: wel de al bewaarde Ottolenghi-recepten, geen andere
    assert [r["id"] for r in recepten_selectie(rec, {"bronnen": ["Ottolenghi"]}, herk)] == ["a"]


def test_zoekvraag():
    f = {"wens": "iets met pompoen", "voorraad": True, "aanbieding": False}
    assert zoekvraag(f, ["spinazie"], ["kip"]) == "iets met pompoen spinazie"
    assert zoekvraag({"wens": "", "voorraad": False, "aanbieding": True}, ["spinazie"], ["kip"]) == "kip"


def test_opties_prompt_is_licht_en_volgt_vinkjes():
    dag = {"dag": "woensdag", "datum": "2026-12-02", "aanwezig": ["Marcel", "Marlies"], "minuten": 45, "kookt": "Marcel",
           "vriezer": 0, "notitie": ""}
    rec = [{"code": "R1", "naam": "Pompoensoep", "tijd": 30, "vega": True, "in_huis": ["pompoen"]}]
    links = [{"code": "L1", "titel": "Roasted squash with feta", "bron": "Ottolenghi"}]
    f = {"voorraad": False, "snel": True, "aanbieding": False, "bronnen": ["Ottolenghi"], "wens": ""}
    p = opties_prompt(dag, f, "Gezin:\n- Marcel", [], rec, links, ["pompoen"], [], ["kip"], date(2026, 11, 28))
    assert "R1. Pompoensoep | 30 min | vega" in p and "L1. Roasted squash with feta (Ottolenghi)" in p
    assert "hooguit 30 minuten" in p
    assert "In huis:" not in p and "In de aanbieding:" not in p   # vinkjes uit: niet meesturen
    assert "Producten (id" not in p                                 # geen productlijst meer
    f2 = {**f, "voorraad": True, "aanbieding": True, "snel": False}
    p2 = opties_prompt(dag, f2, "", [], rec, [], ["pompoen"], ["spinazie"], ["kip"], date(2026, 11, 28))
    assert "In huis: pompoen" in p2 and "In de aanbieding: kip" in p2 and "hooguit 45 minuten" in p2


def test_opties_toepassen():
    codes = {"R1": {"recept_id": "x"}, "L2": {"url": "https://a/b"}}
    res = [{"keuze": "r1", "reden": "lekker"}, {"keuze": "L 2"}, {"keuze": "R1"}, {"keuze": "L9"}, "nonsens"]
    uit = opties_toepassen(res, codes)
    assert [o["code"] for o in uit] == ["R1", "L2"]
    assert uit[0]["recept_id"] == "x" and uit[0]["reden"] == "lekker"
    assert opties_toepassen({"opties": [{"keuze": "L2"}]}, codes)[0]["url"] == "https://a/b"
    assert opties_toepassen(None, codes) == []


def test_verdeel_en_reserveringen():
    planning = [
        {"id": "a", "datum": "2026-12-01", "uit_voorraad": {"5": 1}},
        {"id": "b", "datum": "2026-11-01", "uit_voorraad": {"5": 3}},   # verleden: telt niet
        {"id": "c", "datum": "2026-12-03", "uit_voorraad": {"7": 0.5}},
    ]
    res = reserveringen(planning, "2026-11-28", behalve="c")
    assert res == {5: 1.0}
    # 2 kip in huis, waarvan 1 al voor maaltijd a: van de 3 nodig 1 uit voorraad, 2 kopen
    uit_v, kopen = verdeel({5: 3, 8: 0.4}, {5: 2}, res)
    assert uit_v == {5: 1.0}
    assert kopen == {5: 2.0, 8: 1.0}    # hele verpakkingen
    uit_v, kopen = verdeel({5: 1}, {5: 4}, {})
    assert uit_v == {5: 1.0} and kopen == {}


def test_gemini_chunks():
    obj = {"candidates": [{"content": {"parts": [{"text": "**Plan** pompoen", "thought": True}, {"text": "[{\"keuze\""}]}}]}
    assert delen_uit_chunk(obj) == [(True, "**Plan** pompoen"), (False, "[{\"keuze\"")]
    assert delen_uit_chunk({}) == []
    assert model_naam("models/gemini-2.5-flash") == "gemini-2.5-flash"
    assert model_naam(None) == "gemini-2.5-flash"
