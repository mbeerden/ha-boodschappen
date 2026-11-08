"""Klusjes: huishoudtaken met de statussen backlog, gepland, bezig en klaar.

- Eenmalige en terugkerende klusjes (elke x dagen/weken/maanden/jaren). Rond je een terugkerend klusje af,
  dan kies je meteen de volgende datum; die staat dan weer in Gepland.
- Geplande klusjes staan als afspraak in een agenda naar keuze, met een kenmerk `klusje:<id>` in de
  beschrijving. Verschuif je de afspraak in de agenda, dan neemt de planning de nieuwe datum over.
- Elke dag op een vaste tijd een melding per persoon met de klusjes van vandaag en wat te laat is.
- Terugkerende afspraken uit een bestaande agenda kun je overnemen als klusjes.
"""
from __future__ import annotations

import calendar as _cal
import copy
from datetime import date, datetime, timedelta
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Callable
import uuid

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .coordinator import BoodschappenCoordinator

_LOGGER = logging.getLogger(__name__)

STATUSSEN = ("backlog", "gepland", "bezig", "klaar")
EENHEDEN = ("dagen", "weken", "maanden", "jaren")
MARKER = "klusje:"
SYNC_INTERVAL = 60
VERWIJDER_NA = 600       # een afspraak die 10 minuten weg is uit de agenda, is echt verwijderd
BEWAAR_KLAAR = 400       # afgeronde klusjes zoveel dagen bewaren (voor 'laatst gedaan')
EVENT_DUUR = 30          # minuten, voor klusjes met een tijd

DEFAULT_INSTELLINGEN: dict[str, Any] = {
    "agenda": None,           # calendar.* entiteit
    "agenda_gekozen": False,
    "meldtijd": "08:00",
    "meldingen": True,
    "klaar_dagen": 14,
    "personen": [],           # [{"naam", "notify", "ongetoewezen"}]
}


# ----------------------------------------------------------------- hulpfuncties (zonder HA, getest)
def nieuw_id() -> str:
    return uuid.uuid4().hex[:12]


def plus_maanden(d: date, n: int) -> date:
    m = d.month - 1 + n
    j = d.year + m // 12
    m = m % 12 + 1
    return date(j, m, min(d.day, _cal.monthrange(j, m)[1]))


def volgende_datum(van: date, herhaling: dict | None) -> date | None:
    """De volgende keer: `van` plus het interval."""
    if not herhaling:
        return None
    n = max(1, int(herhaling.get("elke") or 1))
    e = herhaling.get("eenheid")
    if e == "dagen":
        return van + timedelta(days=n)
    if e == "weken":
        return van + timedelta(weeks=n)
    if e == "maanden":
        return plus_maanden(van, n)
    if e == "jaren":
        return plus_maanden(van, 12 * n)
    return None


def herhaling_tekst(h: dict | None) -> str:
    if not h:
        return "eenmalig"
    n = int(h.get("elke") or 1)
    enkel = {"dagen": "dag", "weken": "week", "maanden": "maand", "jaren": "jaar"}
    e = h.get("eenheid", "weken")
    return f"elke {enkel.get(e, e)}" if n == 1 else f"elke {n} {e}"


def schoon_herhaling(h: Any) -> dict | None:
    if not isinstance(h, dict) or not h.get("eenheid"):
        return None
    if h["eenheid"] not in EENHEDEN:
        raise ValueError(f"Onbekende eenheid: {h['eenheid']}")
    try:
        n = int(h.get("elke") or 1)
    except (TypeError, ValueError) as err:
        raise ValueError("Interval moet een getal zijn") from err
    return {"elke": max(1, min(n, 365)), "eenheid": h["eenheid"]}


def herhaling_uit_rrule(rrule: str | None) -> dict | None:
    """'FREQ=WEEKLY;INTERVAL=2;BYDAY=SU' -> {'elke': 2, 'eenheid': 'weken'}."""
    if not rrule:
        return None
    delen = dict(p.split("=", 1) for p in str(rrule).replace("RRULE:", "").split(";") if "=" in p)
    eenheid = {"DAILY": "dagen", "WEEKLY": "weken", "MONTHLY": "maanden", "YEARLY": "jaren"}.get(delen.get("FREQ", "").upper())
    if not eenheid:
        return None
    try:
        n = int(delen.get("INTERVAL", 1))
    except ValueError:
        n = 1
    return {"elke": max(1, n), "eenheid": eenheid}


def marker_uit(tekst: str | None) -> str | None:
    m = re.search(r"klusje:([0-9a-f]{12})", tekst or "")
    return m.group(1) if m else None


def event_beschrijving(taak: dict, link: str | None) -> str:
    regels = []
    if taak.get("persoon"):
        regels.append(f"Wie: {taak['persoon']}")
    if taak.get("ruimte"):
        regels.append(f"Waar: {taak['ruimte']}")
    if taak.get("herhaling"):
        regels.append(f"Herhaling: {herhaling_tekst(taak['herhaling'])}")
    if taak.get("notitie"):
        regels.append("")
        regels.append(taak["notitie"])
    if link:
        regels.append("")
        regels.append(link)
    regels.append("")
    regels.append(f"{MARKER}{taak['id']}")
    return "\n".join(regels)


def schoon_tijd(t: Any) -> str | None:
    if not t:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})", str(t))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("Tijd moet als uu:mm")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def schoon_datum(d: Any) -> str | None:
    if not d:
        return None
    try:
        return date.fromisoformat(str(d)[:10]).isoformat()
    except ValueError as err:
        raise ValueError("Ongeldige datum") from err


def zichtbaar(taken: list[dict], klaar_dagen: int, vandaag: date) -> list[dict]:
    """Alle open klusjes, plus wat in de laatste `klaar_dagen` dagen is afgerond."""
    grens = (vandaag - timedelta(days=max(0, int(klaar_dagen)))).isoformat()
    return [t for t in taken if t["status"] != "klaar" or (t.get("afgerond") or "")[:10] >= grens]


def melding_tekst(taken: list[dict], vandaag: date) -> str:
    regels = []
    for t in sorted(taken, key=lambda x: (x.get("datum") or "", x["titel"].lower())):
        d = t.get("datum")
        if d and d < vandaag.isoformat():
            dagen = (vandaag - date.fromisoformat(d)).days
            regels.append(f"• {t['titel']} ({dagen} {'dag' if dagen == 1 else 'dagen'} te laat)")
        else:
            regels.append(f"• {t['titel']}" + (f" ({t['tijd']})" if t.get("tijd") else ""))
    return "\n".join(regels)


def te_doen(taken: list[dict], vandaag: date) -> list[dict]:
    """Klusjes die vandaag of eerder gepland staan en nog open zijn."""
    v = vandaag.isoformat()
    return [t for t in taken if t["status"] in ("gepland", "bezig") and t.get("datum") and t["datum"] <= v]


def reeksen_uit_events(events: list[dict], vandaag: date) -> list[dict]:
    """Terugkerende afspraken groeperen tot voorstellen om over te nemen.

    events: [{'uid', 'titel', 'start' (date), 'tijd' (str|None), 'rrule'}]
    """
    per: dict[str, dict] = {}
    for e in events:
        if not e.get("rrule"):
            continue
        h = herhaling_uit_rrule(e["rrule"])
        if not h:
            continue
        sleutel = e.get("uid") or e["titel"]
        r = per.setdefault(sleutel, {"uid": e.get("uid"), "titel": e["titel"], "herhaling": h, "tijd": e.get("tijd"),
                                     "laatst": None, "volgende": None})
        d = e["start"].isoformat()
        if d < vandaag.isoformat():
            r["laatst"] = max(r["laatst"] or d, d)
        elif not r["volgende"] or d < r["volgende"]:
            r["volgende"] = d
    uit = []
    for r in per.values():
        if not r["volgende"]:
            basis = date.fromisoformat(r["laatst"]) if r["laatst"] else vandaag
            r["volgende"] = (volgende_datum(basis, r["herhaling"]) or vandaag).isoformat()
        uit.append(r)
    return sorted(uit, key=lambda x: x["titel"].lower())


# ----------------------------------------------------------------- de module
class Klusjes:
    def __init__(self, coordinator: "BoodschappenCoordinator", entry_id: str) -> None:
        self.c = coordinator
        self.hass = coordinator.hass
        self._store: Store = Store(self.hass, 1, f"boodschappen.{entry_id}.klusjes")
        self.s: dict[str, Any] = {}
        self._sync_ts = 0.0
        self._ooit_gelezen = False
        self.sync_fout: str | None = None
        self._luisteraars: list[Callable[[], None]] = []

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.s = {
            "taken": data.get("taken", []),
            "instellingen": {**copy.deepcopy(DEFAULT_INSTELLINGEN), **(data.get("instellingen") or {})},
            "gemeld": data.get("gemeld"),
        }

    def save(self) -> None:
        grens = (date.today() - timedelta(days=BEWAAR_KLAAR)).isoformat()
        self.s["taken"] = [t for t in self.s["taken"] if t["status"] != "klaar" or (t.get("afgerond") or "") >= grens]
        self._store.async_delay_save(lambda: self.s, 1)
        for cb in list(self._luisteraars):
            cb()

    def luister(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._luisteraars.append(cb)
        return lambda: self._luisteraars.remove(cb)

    @property
    def inst(self) -> dict:
        return self.s["instellingen"]

    @property
    def taken(self) -> list[dict]:
        return self.s["taken"]

    def taak(self, tid: str) -> dict:
        t = next((x for x in self.taken if x["id"] == tid), None)
        if t is None:
            raise ValueError("Dit klusje bestaat niet meer")
        return t

    def open_taken(self) -> list[dict]:
        return [t for t in self.taken if t["status"] != "klaar"]

    # --- personen ---------------------------------------------------------
    def personen(self) -> list[dict]:
        if not self.inst.get("personen"):
            # eerste keer: dezelfde personen als bij Eten, met een melding naar hun telefoon als die er is
            namen = []
            eten = getattr(self.c, "eten", None)
            if eten is not None:
                namen = list(eten.inst.get("personen") or [])
            diensten = set((self.hass.services.async_services().get("notify") or {}).keys())
            self.inst["personen"] = [
                {"naam": n, "notify": f"mobile_app_{re.sub(r'[^a-z0-9]+', '_', n.lower())}"
                 if f"mobile_app_{re.sub(r'[^a-z0-9]+', '_', n.lower())}" in diensten else None,
                 "ongetoewezen": False}
                for n in namen
            ]
        return self.inst["personen"]

    # --- payload voor het paneel -----------------------------------------
    def payload(self) -> dict:
        vandaag = dt_util.now().date()
        laatst: dict[str, str] = {}
        for t in self.taken:
            if t["status"] == "klaar" and t.get("reeks"):
                d = (t.get("afgerond") or "")[:10]
                if d > laatst.get(t["reeks"], ""):
                    laatst[t["reeks"]] = d
        taken = []
        for t in zichtbaar(self.taken, self.inst.get("klaar_dagen", 14), vandaag):
            x = {k: t.get(k) for k in ("id", "titel", "status", "datum", "tijd", "persoon", "ruimte", "notitie",
                                       "herhaling", "afgerond", "gemaakt", "reeks", "volgende_id")}
            x["laatst"] = laatst.get(t.get("reeks") or "")
            x["in_agenda"] = bool(t.get("uid"))
            taken.append(x)
        diensten = sorted((self.hass.services.async_services().get("notify") or {}).keys())
        areas = []
        try:
            from homeassistant.helpers import area_registry as ar

            areas = sorted(a.name for a in ar.async_get(self.hass).async_list_areas())
        except Exception:  # noqa: BLE001
            pass
        ruimtes = sorted({*areas, *(t["ruimte"] for t in self.taken if t.get("ruimte"))}, key=str.lower)
        return {
            "taken": taken,
            "vandaag": vandaag.isoformat(),
            "instellingen": {**self.inst, "personen": self.personen()},
            "agendas": sorted(self.hass.states.async_entity_ids("calendar")),
            "agenda": self.inst.get("agenda") or self._agenda_id(),
            "notify": [d for d in diensten if d not in ("notify", "send_message", "persistent_notification")],
            "ruimtes": ruimtes,
            "sync_fout": self.sync_fout,
        }

    # --- bewerken ---------------------------------------------------------
    def _velden(self, t: dict, v: dict) -> None:
        if "titel" in v:
            titel = str(v["titel"] or "").strip()
            if not titel:
                raise ValueError("Geef het klusje een naam")
            t["titel"] = titel[:120]
        for k in ("persoon", "ruimte", "notitie"):
            if k in v:
                t[k] = (str(v[k]).strip() or None) if v[k] is not None else None
        if "tijd" in v:
            t["tijd"] = schoon_tijd(v["tijd"])
        if "herhaling" in v:
            t["herhaling"] = schoon_herhaling(v["herhaling"])
            if t["herhaling"] and not t.get("reeks"):
                t["reeks"] = t["id"]
        if "datum" in v:
            t["datum"] = schoon_datum(v["datum"])
            if t["datum"] and t["status"] == "backlog":
                t["status"] = "gepland"
            elif not t["datum"] and t["status"] == "gepland":
                t["status"] = "backlog"

    async def toevoegen(self, v: dict) -> dict:
        t = {"id": nieuw_id(), "titel": "", "status": "backlog", "datum": None, "tijd": None, "persoon": None,
             "ruimte": None, "notitie": None, "herhaling": None, "reeks": None, "uid": None,
             "gemaakt": dt_util.now().isoformat(timespec="minutes"), "afgerond": None}
        self._velden(t, v)
        if v.get("status") in ("bezig",):
            t["status"] = "bezig"
        self.taken.append(t)
        await self._agenda_bij(t)
        self.save()
        return t

    async def wijzigen(self, tid: str, v: dict) -> dict:
        t = self.taak(tid)
        self._velden(t, v)
        await self._agenda_bij(t)
        self.save()
        return t

    async def status(self, tid: str, status: str, datum: str | None = None) -> dict:
        if status not in STATUSSEN or status == "klaar":
            raise ValueError("Ongeldige status")
        t = self.taak(tid)
        if datum is not None:
            t["datum"] = schoon_datum(datum)
        if status == "gepland" and not t.get("datum"):
            raise ValueError("Kies een datum om te plannen")
        if status == "backlog":
            t["datum"] = None
        t["status"] = status
        t["afgerond"] = None
        await self._agenda_bij(t)
        self.save()
        return t

    async def afronden(self, tid: str, volgende: str | None = None, geen_volgende: bool = False) -> dict:
        """Klaar. Bij een terugkerend klusje komt er een nieuw klusje in Gepland op `volgende`."""
        t = self.taak(tid)
        vandaag = dt_util.now().date()
        t["status"] = "klaar"
        t["afgerond"] = dt_util.now().isoformat(timespec="minutes")
        await self._event_verwijderen(t)
        nieuw = None
        if t.get("herhaling") and not geen_volgende:
            d = schoon_datum(volgende) or (volgende_datum(vandaag, t["herhaling"]) or vandaag).isoformat()
            nieuw = {**{k: t.get(k) for k in ("titel", "tijd", "persoon", "ruimte", "notitie", "herhaling")},
                     "id": nieuw_id(), "status": "gepland", "datum": d, "reeks": t.get("reeks") or t["id"], "uid": None,
                     "gemaakt": dt_util.now().isoformat(timespec="minutes"), "afgerond": None}
            t["reeks"] = nieuw["reeks"]
            t["volgende_id"] = nieuw["id"]
            self.taken.append(nieuw)
            await self._event_schrijven(nieuw)
        self.save()
        return nieuw or t

    async def heropenen(self, tid: str) -> dict:
        """Per ongeluk afgerond: terugzetten en het automatisch gemaakte vervolg weer weghalen."""
        t = self.taak(tid)
        vid = t.pop("volgende_id", None)
        if vid:
            v = next((x for x in self.taken if x["id"] == vid), None)
            if v is not None and v["status"] in ("gepland", "backlog"):
                await self._event_verwijderen(v)
                self.taken.remove(v)
        t["status"] = "gepland" if t.get("datum") else "backlog"
        t["afgerond"] = None
        await self._agenda_bij(t)
        self.save()
        return t

    async def verwijderen(self, tid: str) -> None:
        t = self.taak(tid)
        await self._event_verwijderen(t)
        self.taken.remove(t)
        self.save()

    def instellingen_opslaan(self, inst: dict) -> None:
        uit = dict(self.inst)
        if "agenda" in inst:
            a = inst["agenda"] or None
            if a and not str(a).startswith("calendar."):
                raise ValueError("Kies een agenda")
            uit["agenda"] = a
            uit["agenda_gekozen"] = True
        if "meldtijd" in inst:
            uit["meldtijd"] = schoon_tijd(inst["meldtijd"]) or "08:00"
        if "meldingen" in inst:
            uit["meldingen"] = bool(inst["meldingen"])
        if "klaar_dagen" in inst:
            uit["klaar_dagen"] = max(1, min(int(inst["klaar_dagen"] or 14), 365))
        if "personen" in inst:
            personen = []
            for p in inst["personen"] or []:
                naam = str(p.get("naam") or "").strip()
                if naam:
                    personen.append({"naam": naam[:40], "notify": p.get("notify") or None,
                                     "ongetoewezen": bool(p.get("ongetoewezen"))})
            uit["personen"] = personen
        self.s["instellingen"] = uit
        self._sync_ts = 0
        self.save()

    # --- agenda ------------------------------------------------------------
    def _agenda_id(self) -> str | None:
        if not self.inst.get("agenda") and not self.inst.get("agenda_gekozen"):
            cal = sorted(e for e in self.hass.states.async_entity_ids("calendar") if "onderhoud" in e or "klus" in e)
            if cal:
                self.inst["agenda"] = cal[0]
                self.inst["agenda_gekozen"] = True
                self._store.async_delay_save(lambda: self.s, 1)
        return self.inst.get("agenda")

    def _agenda(self):
        ent_id = self._agenda_id()
        if not ent_id:
            return None
        comp = self.hass.data.get("calendar")
        return comp.get_entity(ent_id) if comp else None

    def _link(self) -> str | None:
        try:
            from homeassistant.helpers.network import get_url

            return f"{get_url(self.hass, prefer_external=True)}/klusjes"
        except Exception:  # noqa: BLE001
            return None

    def _tijden(self, t: dict) -> tuple[Any, Any]:
        d = date.fromisoformat(t["datum"])
        if t.get("tijd"):
            uur, minuut = (int(x) for x in t["tijd"].split(":"))
            start = datetime.combine(d, datetime.min.time()).replace(hour=uur, minute=minuut,
                                                                     tzinfo=dt_util.get_default_time_zone())
            return start, start + timedelta(minutes=EVENT_DUUR)
        return d, d + timedelta(days=1)

    async def _agenda_bij(self, t: dict) -> None:
        if t["status"] in ("gepland", "bezig") and t.get("datum"):
            await self._event_schrijven(t)
        else:
            await self._event_verwijderen(t)

    async def _event_schrijven(self, t: dict) -> None:
        ent = self._agenda()
        if ent is None:
            return
        await self._event_verwijderen(t)
        start, eind = self._tijden(t)
        titel = t["titel"] + (f" ({t['persoon']})" if t.get("persoon") else "")
        try:
            await ent.async_create_event(dtstart=start, dtend=eind, summary=titel,
                                         description=event_beschrijving(t, self._link()))
        except Exception as err:  # noqa: BLE001
            self.sync_fout = f"Agenda bijwerken mislukt: {err}"
            _LOGGER.warning("Klusje in de agenda zetten mislukt: %s", err)
            return
        self.sync_fout = None
        t["agenda_datum"] = t["datum"]
        try:
            tz = dt_util.get_default_time_zone()
            d = date.fromisoformat(t["datum"])
            events = await ent.async_get_events(self.hass, datetime.combine(d - timedelta(days=1), datetime.min.time(), tz),
                                                datetime.combine(d + timedelta(days=2), datetime.min.time(), tz))
            for e in events:
                if marker_uit(e.description) == t["id"]:
                    t["uid"] = e.uid
                    break
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Klusje-event teruglezen mislukt: %s", err)

    async def _event_verwijderen(self, t: dict) -> None:
        t.pop("mist_sinds", None)
        ent = self._agenda()
        if ent is None or not t.get("uid"):
            t["uid"] = None
            return
        try:
            await ent.async_delete_event(t["uid"])
        except Exception as err:  # noqa: BLE001 - al weg in de agenda
            _LOGGER.debug("Klusje-event verwijderen: %s", err)
        t["uid"] = None

    async def sync(self, forceer: bool = False) -> None:
        """Agenda teruglezen: verschoven afspraken overnemen; verwijderde afspraken -> terug naar de backlog."""
        if not forceer and time.time() - self._sync_ts < SYNC_INTERVAL:
            return
        self._sync_ts = time.time()
        ent = self._agenda()
        if ent is None:
            return
        tz = dt_util.get_default_time_zone()
        vandaag = dt_util.now().date()
        van, tot = vandaag - timedelta(days=90), vandaag + timedelta(days=400)
        try:
            events = await ent.async_get_events(self.hass, datetime.combine(van, datetime.min.time(), tz),
                                                datetime.combine(tot, datetime.min.time(), tz))
        except Exception as err:  # noqa: BLE001
            self.sync_fout = f"Agenda lezen mislukt: {err}"
            self._sync_ts = time.time() - SYNC_INTERVAL + 30
            return
        heeft = any(t.get("uid") for t in self.open_taken())
        if not events and (heeft or not self._ooit_gelezen):
            self._sync_ts = 0.0  # net opgestart: agenda nog niet geladen
            return
        self._ooit_gelezen = True
        self.sync_fout = None
        per_id: dict[str, Any] = {}
        for e in events:
            tid = marker_uit(e.description)
            if tid:
                per_id[tid] = e
        gewijzigd = False
        for t in self.open_taken():
            if not t.get("uid") or not t.get("datum") or not (van.isoformat() <= t["datum"] < tot.isoformat()):
                continue
            e = per_id.get(t["id"])
            if e is not None:
                t["uid"] = e.uid
                if t.pop("mist_sinds", None):
                    gewijzigd = True
                if isinstance(e.start, datetime):
                    start = dt_util.as_local(e.start)
                    d, tijd = start.date().isoformat(), start.strftime("%H:%M")
                else:
                    d, tijd = e.start.isoformat(), None
                if d != t["datum"] or (tijd and tijd != t.get("tijd")):
                    t["datum"], t["agenda_datum"] = d, d
                    if tijd:
                        t["tijd"] = tijd
                    gewijzigd = True
            elif not t.get("mist_sinds"):
                t["mist_sinds"] = time.time()
                gewijzigd = True
            elif time.time() - t["mist_sinds"] >= VERWIJDER_NA:
                # in de agenda verwijderd: niet weggooien, maar terug naar de backlog
                t.pop("mist_sinds", None)
                t["uid"], t["datum"], t["status"] = None, None, "backlog"
                gewijzigd = True
        if gewijzigd:
            self.save()

    # --- overnemen uit een agenda -----------------------------------------
    async def import_zoeken(self, agenda: str | None = None) -> list[dict]:
        ent_id = agenda or self._agenda_id()
        comp = self.hass.data.get("calendar")
        ent = comp.get_entity(ent_id) if comp and ent_id else None
        if ent is None:
            raise ValueError("Kies eerst een agenda")
        tz = dt_util.get_default_time_zone()
        vandaag = dt_util.now().date()
        events = await ent.async_get_events(self.hass, datetime.combine(vandaag - timedelta(days=400), datetime.min.time(), tz),
                                            datetime.combine(vandaag + timedelta(days=400), datetime.min.time(), tz))
        lijst = []
        for e in events:
            if marker_uit(e.description):
                continue
            if isinstance(e.start, datetime):
                s = dt_util.as_local(e.start)
                lijst.append({"uid": e.uid, "titel": e.summary, "start": s.date(), "tijd": s.strftime("%H:%M"), "rrule": e.rrule})
            else:
                lijst.append({"uid": e.uid, "titel": e.summary, "start": e.start, "tijd": None, "rrule": e.rrule})
        bekend = {t["titel"].lower() for t in self.open_taken()}
        uit = reeksen_uit_events(lijst, vandaag)
        for r in uit:
            r["bestaat_al"] = r["titel"].lower() in bekend
        return uit

    async def importeren(self, keuzes: list[dict], verwijderen: bool) -> int:
        ent = self._agenda()
        n = 0
        for k in keuzes:
            t = await self.toevoegen({
                "titel": k.get("titel"), "datum": k.get("volgende"), "tijd": k.get("tijd"),
                "herhaling": k.get("herhaling"), "persoon": k.get("persoon"), "ruimte": k.get("ruimte"),
            })
            if k.get("laatst"):
                # 'laatst gedaan' bewaren als afgerond klusje in dezelfde reeks
                self.taken.append({**{x: t.get(x) for x in ("titel", "tijd", "persoon", "ruimte", "herhaling")},
                                   "id": nieuw_id(), "status": "klaar", "datum": k["laatst"], "reeks": t["reeks"],
                                   "uid": None, "gemaakt": t["gemaakt"], "afgerond": f"{k['laatst']}T00:00",
                                   "notitie": None, "geimporteerd": True})
            n += 1
            if verwijderen and ent is not None and k.get("uid"):
                try:
                    await ent.async_delete_event(k["uid"])  # de hele herhalende reeks
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Oude reeks '%s' verwijderen mislukt: %s", k.get("titel"), err)
                    self.sync_fout = f"Oude afspraak '{k.get('titel')}' kon niet uit de agenda: {err}"
        self.save()
        return n

    # --- meldingen ---------------------------------------------------------
    def meldingen_voor(self, vandaag: date) -> dict[str, list[dict]]:
        """Per notify-dienst de klusjes van vandaag en wat te laat is."""
        uit: dict[str, list[dict]] = {}
        open_ = te_doen(self.taken, vandaag)
        for p in self.personen():
            if not p.get("notify"):
                continue
            eigen = [t for t in open_ if t.get("persoon") == p["naam"]
                     or (p.get("ongetoewezen") and not t.get("persoon"))]
            if eigen:
                uit.setdefault(p["notify"], []).extend(x for x in eigen if x not in uit.get(p["notify"], []))
        return uit

    async def melden(self, forceer: bool = False, alleen: str | None = None) -> int:
        nu = dt_util.now()
        vandaag = nu.date()
        if not forceer:
            if not self.inst.get("meldingen", True) or self.s.get("gemeld") == vandaag.isoformat():
                return 0
            if nu.strftime("%H:%M") < (self.inst.get("meldtijd") or "08:00"):
                return 0
        per = self.meldingen_voor(vandaag)
        if alleen:
            per = {k: v for k, v in per.items() if k == alleen} or ({alleen: []} if forceer else {})
        n = 0
        for dienst, taken in per.items():
            if not taken and not forceer:
                continue
            te_laat = sum(1 for t in taken if t["datum"] < vandaag.isoformat())
            titel = f"Klusjes vandaag: {len(taken)}" + (f" ({te_laat} te laat)" if te_laat else "")
            bericht = melding_tekst(taken, vandaag) or "Geen klusjes vandaag. Dit is een testmelding."
            try:
                await self.hass.services.async_call("notify", dienst, {
                    "title": titel, "message": bericht,
                    "data": {"url": "/klusjes", "clickAction": "/klusjes", "tag": "klusjes", "group": "klusjes"},
                }, blocking=True)
                n += 1
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Klusjes-melding via notify.%s mislukt: %s", dienst, err)
        if not forceer:
            self.s["gemeld"] = vandaag.isoformat()
            self._store.async_delay_save(lambda: self.s, 1)
        return n
