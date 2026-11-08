import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.boodschappen.klusjes import (  # noqa: E402
    event_beschrijving,
    herhaling_tekst,
    herhaling_uit_rrule,
    marker_uit,
    melding_tekst,
    reeksen_uit_events,
    schoon_herhaling,
    schoon_tijd,
    te_doen,
    volgende_datum,
    zichtbaar,
)


def test_volgende_datum():
    d = date(2026, 1, 31)
    assert volgende_datum(d, {"elke": 3, "eenheid": "dagen"}) == date(2026, 2, 3)
    assert volgende_datum(d, {"elke": 2, "eenheid": "weken"}) == date(2026, 2, 14)
    assert volgende_datum(d, {"elke": 1, "eenheid": "maanden"}) == date(2026, 2, 28)   # einde maand
    assert volgende_datum(d, {"elke": 13, "eenheid": "maanden"}) == date(2027, 2, 28)
    assert volgende_datum(date(2028, 2, 29), {"elke": 1, "eenheid": "jaren"}) == date(2029, 2, 28)
    assert volgende_datum(d, None) is None


def test_herhaling():
    assert herhaling_tekst({"elke": 1, "eenheid": "weken"}) == "elke week"
    assert herhaling_tekst({"elke": 3, "eenheid": "maanden"}) == "elke 3 maanden"
    assert herhaling_tekst(None) == "eenmalig"
    assert schoon_herhaling({"elke": "2", "eenheid": "weken"}) == {"elke": 2, "eenheid": "weken"}
    assert schoon_herhaling({"elke": 0, "eenheid": "dagen"}) == {"elke": 1, "eenheid": "dagen"}
    assert schoon_herhaling(None) is None
    with pytest.raises(ValueError):
        schoon_herhaling({"elke": 1, "eenheid": "eeuwen"})


def test_rrule():
    assert herhaling_uit_rrule("FREQ=WEEKLY;WKST=MO;INTERVAL=2;BYDAY=SU") == {"elke": 2, "eenheid": "weken"}
    assert herhaling_uit_rrule("FREQ=MONTHLY;BYDAY=1SU") == {"elke": 1, "eenheid": "maanden"}
    assert herhaling_uit_rrule("RRULE:FREQ=MONTHLY;INTERVAL=3") == {"elke": 3, "eenheid": "maanden"}
    assert herhaling_uit_rrule("FREQ=YEARLY") == {"elke": 1, "eenheid": "jaren"}
    assert herhaling_uit_rrule(None) is None
    assert herhaling_uit_rrule("FREQ=HOURLY") is None


def test_tijd():
    assert schoon_tijd("8:30") == "08:30"
    assert schoon_tijd("08:30:00") == "08:30"
    assert schoon_tijd("") is None
    with pytest.raises(ValueError):
        schoon_tijd("25:00")


def test_marker():
    t = {"id": "a1b2c3d4e5f6", "titel": "Oven", "persoon": "P", "herhaling": {"elke": 2, "eenheid": "weken"}, "notitie": "rooster ook"}
    txt = event_beschrijving(t, "https://ha.example/klusjes")
    assert "Wie: P" in txt and "elke 2 weken" in txt and "rooster ook" in txt
    assert marker_uit(txt) == "a1b2c3d4e5f6"
    assert marker_uit("eten:a1b2c3d4e5f6") is None
    assert marker_uit(None) is None


def test_zichtbaar_en_te_doen():
    v = date(2026, 11, 8)
    taken = [
        {"id": "1", "titel": "a", "status": "klaar", "afgerond": "2026-11-01T10:00"},
        {"id": "2", "titel": "b", "status": "klaar", "afgerond": "2026-10-01T10:00"},
        {"id": "3", "titel": "c", "status": "gepland", "datum": "2026-11-08"},
        {"id": "4", "titel": "d", "status": "gepland", "datum": "2026-11-05"},
        {"id": "5", "titel": "e", "status": "gepland", "datum": "2026-11-09"},
        {"id": "6", "titel": "f", "status": "backlog", "datum": None},
        {"id": "7", "titel": "g", "status": "bezig", "datum": "2026-11-07"},
    ]
    assert [t["id"] for t in zichtbaar(taken, 14, v)] == ["1", "3", "4", "5", "6", "7"]
    assert [t["id"] for t in te_doen(taken, v)] == ["3", "4", "7"]
    tekst = melding_tekst(te_doen(taken, v), v)
    assert "d (3 dagen te laat)" in tekst and "g (1 dag te laat)" in tekst and "• c" in tekst


def test_reeksen_uit_events():
    v = date(2026, 11, 8)
    events = [
        {"uid": "u1", "titel": "Oven kleine beurt", "start": date(2026, 11, 1), "tijd": "08:30", "rrule": "FREQ=WEEKLY;INTERVAL=2;BYDAY=SU"},
        {"uid": "u1", "titel": "Oven kleine beurt", "start": date(2026, 11, 15), "tijd": "08:30", "rrule": "FREQ=WEEKLY;INTERVAL=2;BYDAY=SU"},
        {"uid": "u1", "titel": "Oven kleine beurt", "start": date(2026, 11, 29), "tijd": "08:30", "rrule": "FREQ=WEEKLY;INTERVAL=2;BYDAY=SU"},
        {"uid": "u2", "titel": "Filter", "start": date(2026, 10, 4), "tijd": None, "rrule": "FREQ=MONTHLY;INTERVAL=2;BYDAY=1SU"},
        {"uid": "u3", "titel": "Verjaardag", "start": date(2026, 11, 20), "tijd": None, "rrule": None},
    ]
    r = reeksen_uit_events(events, v)
    assert [x["titel"] for x in r] == ["Filter", "Oven kleine beurt"]
    oven = r[1]
    assert oven["volgende"] == "2026-11-15" and oven["laatst"] == "2026-11-01" and oven["tijd"] == "08:30"
    assert oven["herhaling"] == {"elke": 2, "eenheid": "weken"}
    # geen toekomstige afspraak in het venster: laatste + interval
    assert r[0]["volgende"] == "2026-12-04"
