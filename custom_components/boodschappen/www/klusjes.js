// Klusjes-paneel voor Home Assistant: backlog, gepland, in behandeling en klaar; terugkerende klusjes en meldingen.
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const vanIso = (s) => new Date(String(s).slice(0, 10) + "T12:00:00");
const plusDagen = (d, n) => {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
};
const plusMaanden = (d, n) => {
  const x = new Date(d.getFullYear(), d.getMonth() + n, 1, 12);
  const laatste = new Date(x.getFullYear(), x.getMonth() + 1, 0).getDate();
  x.setDate(Math.min(d.getDate(), laatste));
  return x;
};
// zelfde rekenregel als volgende_datum in klusjes.py
const volgende = (van, h) => {
  if (!h) return null;
  const n = Math.max(1, Number(h.elke) || 1);
  if (h.eenheid === "dagen") return plusDagen(van, n);
  if (h.eenheid === "weken") return plusDagen(van, 7 * n);
  if (h.eenheid === "maanden") return plusMaanden(van, n);
  if (h.eenheid === "jaren") return plusMaanden(van, 12 * n);
  return null;
};
const herhTekst = (h) => {
  if (!h) return "eenmalig";
  const n = Number(h.elke) || 1;
  const enkel = { dagen: "dag", weken: "week", maanden: "maand", jaren: "jaar" };
  return n === 1 ? `elke ${enkel[h.eenheid] || h.eenheid}` : `elke ${n} ${h.eenheid}`;
};
const datumKort = (s) => vanIso(s).toLocaleDateString("nl-NL", { weekday: "short", day: "numeric", month: "short" });
const STATUS = [
  ["backlog", "Backlog", "📥"],
  ["gepland", "Gepland", "📅"],
  ["bezig", "In behandeling", "🛠️"],
  ["klaar", "Klaar", "✅"],
];
const EENHEDEN = [
  ["dagen", "dagen"],
  ["weken", "weken"],
  ["maanden", "maanden"],
  ["jaren", "jaar"],
];

class KlusjesPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.d = null;
    this.tab = "gepland";
    this.persoon = "";
    try {
      this.persoon = localStorage.getItem("klusjes_persoon") || "";
    } catch (e) {
      this.persoon = "";
    }
    this.modal = null; // {soort: 'bewerk'|'nieuw'|'plan'|'klaar'|'inst', id}
    this.import = null;
    this.busy = false;
    this.err = "";
    this.melding = "";
    this.breed = false;
  }

  set hass(h) {
    const first = !this._hass;
    this._hass = h;
    if (this.menuBtn) this.menuBtn.hass = h;
    if (first) this._init();
  }
  get hass() {
    return this._hass;
  }
  set narrow(n) {
    this._narrow = n;
    if (this.menuBtn) this.menuBtn.narrow = n;
  }

  connectedCallback() {
    this._timer = setInterval(() => this._poll(), 60000);
    this._vis = () => document.visibilityState === "visible" && this._poll();
    document.addEventListener("visibilitychange", this._vis);
    this._ro = new ResizeObserver(() => {
      const b = this.clientWidth >= 1000;
      if (b !== this.breed) {
        this.breed = b;
        this._render();
      }
    });
    this._ro.observe(this);
  }
  disconnectedCallback() {
    clearInterval(this._timer);
    document.removeEventListener("visibilitychange", this._vis);
    if (this._ro) this._ro.disconnect();
  }

  async _init() {
    this._shell();
    const p = new URLSearchParams(location.search);
    if (p.get("tab") && STATUS.some((s) => s[0] === p.get("tab"))) this.tab = p.get("tab");
    this.breed = this.clientWidth >= 1000;
    await this._load();
  }

  _poll() {
    const a = this.shadowRoot.activeElement;
    if (this.busy || this.modal || (a && /INPUT|SELECT|TEXTAREA/.test(a.tagName))) return;
    this._load();
  }

  async _load() {
    try {
      this.d = await this._hass.callWS({ type: "boodschappen/klusjes" });
      this.err = "";
    } catch (e) {
      this.err = e.message || "Kan Klusjes niet laden.";
    }
    this._render();
  }

  async _do(msg, melding = "") {
    if (this.busy) return null;
    this.busy = true;
    this._render();
    let res = null;
    try {
      this.d = await this._hass.callWS(msg);
      res = this.d.resultaat ?? true;
      this.err = "";
      this.melding = melding;
    } catch (e) {
      this.err = e.message || "Dat lukte niet.";
      this.melding = "";
    }
    this.busy = false;
    this._render();
    return res;
  }

  _shell() {
    this.shadowRoot.innerHTML = `
      <style>${CSS}</style>
      <header>
        <div class="menu"></div>
        <h1>Klusjes</h1>
        <button class="hbtn" data-act="inst" aria-label="Instellingen" title="Instellingen">⚙</button>
      </header>
      <div class="filter"></div>
      <nav class="tabs" role="tablist"></nav>
      <div class="main"></div>
      <div class="overlay"></div>`;
    const menu = document.createElement("ha-menu-button");
    menu.hass = this._hass;
    menu.narrow = this._narrow;
    this.shadowRoot.querySelector(".menu").appendChild(menu);
    this.menuBtn = menu;
    const root = this.shadowRoot;
    root.addEventListener("click", (e) => this._click(e));
    root.addEventListener("submit", (e) => this._submit(e));
    root.addEventListener("change", (e) => this._change(e));
  }

  // ------------------------------------------------------------------ data
  get taken() {
    return this.d?.taken || [];
  }
  taak(id) {
    return this.taken.find((t) => t.id === id);
  }
  get personen() {
    return this.d?.instellingen?.personen || [];
  }
  get vandaag() {
    return this.d?.vandaag || iso(new Date());
  }
  _gefilterd(status) {
    let l = this.taken.filter((t) => t.status === status);
    if (this.persoon === "-") l = l.filter((t) => !t.persoon);
    else if (this.persoon) l = l.filter((t) => t.persoon === this.persoon);
    const naam = (t) => t.titel.toLowerCase();
    if (status === "klaar") return l.sort((a, b) => String(b.afgerond).localeCompare(String(a.afgerond)));
    if (status === "backlog") return l.sort((a, b) => String(b.gemaakt).localeCompare(String(a.gemaakt)));
    return l.sort((a, b) => (a.datum || "9999").localeCompare(b.datum || "9999") || (a.tijd || "").localeCompare(b.tijd || "") || naam(a).localeCompare(naam(b)));
  }
  _datumLabel(d) {
    if (!d) return "";
    const dagen = Math.round((vanIso(d) - vanIso(this.vandaag)) / 86400000);
    if (dagen === 0) return "vandaag";
    if (dagen === 1) return "morgen";
    if (dagen === -1) return "gisteren te laat";
    if (dagen < 0) return `${-dagen} dagen te laat`;
    if (dagen < 7) return vanIso(d).toLocaleDateString("nl-NL", { weekday: "long" });
    return datumKort(d);
  }

  // ------------------------------------------------------------------ render
  _render() {
    const root = this.shadowRoot;
    const main = root.querySelector(".main");
    const tabs = root.querySelector(".tabs");
    const filter = root.querySelector(".filter");
    root.host.classList.toggle("bezig", this.busy);
    if (!this.d) {
      main.innerHTML = this.err ? `<p class="fout">${esc(this.err)}</p>` : `<p class="leeg">Laden…</p>`;
      return;
    }
    const y = main.scrollTop;
    const chips = [["", "Iedereen"], ...this.personen.map((p) => [p.naam, p.naam]), ["-", "Niemand"]];
    filter.innerHTML = `<div class="chips" role="group" aria-label="Filter op persoon">${chips
      .map(([v, l]) => `<button class="chip ${this.persoon === v ? "on" : ""}" data-act="persoon" data-v="${esc(v)}" aria-pressed="${this.persoon === v}">${esc(l)}</button>`)
      .join("")}</div>`;
    const tel = (s) => this._gefilterd(s).length;
    const telLaat = this._gefilterd("gepland").filter((t) => t.datum && t.datum < this.vandaag).length;
    let html = "";
    if (this.err) html += `<p class="fout" role="alert">${esc(this.err)}</p>`;
    else if (this.melding) html += `<p class="melding" role="status">${esc(this.melding)}</p>`;
    if (this.d.sync_fout) html += `<p class="fout">${esc(this.d.sync_fout)}</p>`;
    if (this.breed) {
      tabs.style.display = "none";
      html += `<div class="bord">${STATUS.map(([s, l, ic]) => `
        <section class="kolom" aria-label="${l}">
          <h2>${ic} ${l} <b>${tel(s) || ""}</b>${s === "gepland" && telLaat ? `<span class="laatlabel">${telLaat} te laat</span>` : ""}</h2>
          ${this._kolom(s)}
        </section>`).join("")}</div>`;
    } else {
      tabs.style.display = "";
      tabs.innerHTML = STATUS.map(([s, l]) => `
        <button role="tab" aria-selected="${this.tab === s}" data-act="tab" data-v="${s}" class="${this.tab === s ? "on" : ""}">
          ${l === "In behandeling" ? "Bezig" : l} <b class="${s === "gepland" && telLaat ? "rood" : ""}">${tel(s) || ""}</b></button>`).join("");
      html += this._kolom(this.tab);
    }
    main.innerHTML = html;
    main.scrollTop = y;
    this._renderModal();
  }

  _kolom(s) {
    let h = "";
    if (s === "backlog") {
      h += `<form class="snel" data-form="snel">
        <input name="titel" placeholder="Nieuw klusje…" aria-label="Nieuw klusje" autocomplete="off" enterkeyhint="done">
        <button class="primary" type="submit" aria-label="Toevoegen">+</button>
      </form>
      <button class="link" data-act="nieuw">+ met details (datum, herhaling, persoon)</button>`;
    }
    const lijst = this._gefilterd(s);
    if (!lijst.length) {
      const leeg = {
        backlog: "Geen klusjes in de backlog.",
        gepland: "Niets gepland. Plan iets vanuit de backlog.",
        bezig: "Niemand is nu met een klusje bezig.",
        klaar: `Nog niets afgerond in de laatste ${this.d.instellingen.klaar_dagen || 14} dagen.`,
      }[s];
      return h + `<p class="leeg">${leeg}</p>`;
    }
    return h + `<ul class="lijst">${lijst.map((t) => this._kaart(t)).join("")}</ul>`;
  }

  _kaart(t) {
    const laat = t.status !== "klaar" && t.datum && t.datum < this.vandaag;
    const vandaag = t.status !== "klaar" && t.datum === this.vandaag;
    const meta = [];
    if (t.status === "klaar") meta.push(`afgerond ${datumKort(t.afgerond)}`);
    else if (t.datum) meta.push(`<span class="${laat ? "rood" : ""}">${esc(this._datumLabel(t.datum))}${t.tijd ? " " + esc(t.tijd) : ""}</span>`);
    if (t.persoon) meta.push(esc(t.persoon));
    if (t.ruimte) meta.push(esc(t.ruimte));
    if (t.herhaling) meta.push(`↻ ${esc(herhTekst(t.herhaling))}`);
    let knoppen = "";
    if (t.status === "backlog") knoppen = `<button class="sec-btn" data-act="plan" data-id="${t.id}">Plan</button>`;
    if (t.status === "gepland")
      knoppen = `<button class="sec-btn" data-act="start" data-id="${t.id}">Start</button>
        <button class="vink" data-act="klaar" data-id="${t.id}" aria-label="Klaar: ${esc(t.titel)}" title="Klaar">✓</button>`;
    if (t.status === "bezig") knoppen = `<button class="vink" data-act="klaar" data-id="${t.id}" aria-label="Klaar: ${esc(t.titel)}" title="Klaar">✓</button>`;
    if (t.status === "klaar") knoppen = `<button class="sec-btn" data-act="heropen" data-id="${t.id}" title="Per ongeluk afgerond? Zet terug">Terugzetten</button>`;
    return `<li class="kaart ${laat ? "laat" : ""} ${vandaag ? "vandaag" : ""} ${t.status === "klaar" ? "af" : ""}">
      <button class="kbody" data-act="bewerk" data-id="${t.id}">
        <span class="t">${esc(t.titel)}</span>
        <small>${meta.join(" · ")}</small>
      </button>
      <div class="kknoppen">${knoppen}</div>
    </li>`;
  }

  // ------------------------------------------------------------------ modals
  _renderModal() {
    const ov = this.shadowRoot.querySelector(".overlay");
    const m = this.modal;
    if (!m) {
      ov.classList.remove("open");
      ov.innerHTML = "";
      return;
    }
    let inhoud = "";
    if (m.soort === "bewerk" || m.soort === "nieuw") inhoud = this._bewerkView(m.soort === "bewerk" ? this.taak(m.id) : null);
    else if (m.soort === "plan") inhoud = this._planView(this.taak(m.id));
    else if (m.soort === "klaar") inhoud = this._klaarView(this.taak(m.id));
    else if (m.soort === "inst") inhoud = this._instView();
    if (!inhoud) {
      this.modal = null;
      return this._renderModal();
    }
    const opnieuw = ov.classList.contains("open") && ov.dataset.soort === m.soort && ov.dataset.id === (m.id || "");
    if (opnieuw && !this._forceer) return; // formulier niet wissen terwijl iemand typt
    this._forceer = false;
    const y = ov.querySelector(".dialoog")?.scrollTop || 0;
    ov.dataset.soort = m.soort;
    ov.dataset.id = m.id || "";
    ov.classList.add("open");
    ov.innerHTML = `<div class="dialoog" role="dialog" aria-modal="true">${inhoud}</div>`;
    ov.querySelector(".dialoog").scrollTop = y;
    if (!opnieuw) {
      const f = ov.querySelector("[autofocus]");
      if (f && !("ontouchstart" in window)) f.focus();
    }
  }

  _persoonSelect(naam, waarde) {
    return `<select name="${naam}"><option value="">Niemand</option>${this.personen
      .map((p) => `<option ${p.naam === waarde ? "selected" : ""}>${esc(p.naam)}</option>`)
      .join("")}${waarde && !this.personen.some((p) => p.naam === waarde) ? `<option selected>${esc(waarde)}</option>` : ""}</select>`;
  }

  _bewerkView(t) {
    const nieuw = !t;
    t = t || { titel: "", persoon: this.persoon && this.persoon !== "-" ? this.persoon : "", herhaling: null, status: "backlog" };
    const h = t.herhaling || { elke: 4, eenheid: "weken" };
    return `<form data-form="bewerk" class="vform">
      <h2>${nieuw ? "Nieuw klusje" : "Klusje"}</h2>
      <label class="veld">Wat moet er gebeuren?<input name="titel" value="${esc(t.titel)}" required ${nieuw ? "autofocus" : ""} maxlength="120"></label>
      <div class="rij">
        <label class="veld">Wie<br>${this._persoonSelect("persoon", t.persoon)}</label>
        <label class="veld">Waar<input name="ruimte" value="${esc(t.ruimte || "")}" list="ruimtes" placeholder="Keuken"></label>
      </div>
      <datalist id="ruimtes">${(this.d.ruimtes || []).map((r) => `<option value="${esc(r)}">`).join("")}</datalist>
      <div class="rij">
        <label class="veld">Datum<input type="date" name="datum" value="${esc(t.status === "klaar" ? "" : t.datum || "")}" ${t.status === "klaar" ? "disabled" : ""}></label>
        <label class="veld smal">Tijd (optioneel)<input type="time" name="tijd" value="${esc(t.tijd || "")}"></label>
      </div>
      <fieldset class="herh">
        <legend>Herhaling</legend>
        <label class="radio"><input type="radio" name="soort" value="eenmalig" ${t.herhaling ? "" : "checked"}> Eenmalig</label>
        <label class="radio"><input type="radio" name="soort" value="terug" ${t.herhaling ? "checked" : ""}> Terugkerend, elke
          <input type="number" name="elke" min="1" max="365" value="${esc(h.elke)}" class="getal" aria-label="Aantal">
          <select name="eenheid" aria-label="Eenheid">${EENHEDEN.map(([v, l]) => `<option value="${v}" ${h.eenheid === v ? "selected" : ""}>${l}</option>`).join("")}</select>
        </label>
        ${t.laatst ? `<p class="hint">Laatst gedaan: ${esc(datumKort(t.laatst))}</p>` : ""}
      </fieldset>
      <label class="veld">Notitie<textarea name="notitie" rows="2">${esc(t.notitie || "")}</textarea></label>
      <p class="hint">${nieuw ? "Zonder datum komt het klusje in de backlog; met datum in Gepland en in de agenda." : t.in_agenda ? "Staat in de agenda." : ""}</p>
      <div class="knoppen">
        <button class="primary" type="submit">${nieuw ? "Toevoegen" : "Opslaan"}</button>
        <button class="sec-btn" type="button" data-act="sluit">Annuleren</button>
        <span class="vul"></span>
        ${!nieuw && t.status !== "klaar" ? `<button class="sec-btn" type="button" data-act="klaar" data-id="${t.id}">✓ Klaar</button>` : ""}
        ${!nieuw && t.status === "gepland" ? `<button class="sec-btn" type="button" data-act="naar_backlog" data-id="${t.id}">Naar backlog</button>` : ""}
        ${!nieuw ? `<button class="wis" type="button" data-act="weg" data-id="${t.id}">Verwijderen</button>` : ""}
      </div>
    </form>`;
  }

  _planView(t) {
    if (!t) return "";
    const v = vanIso(this.vandaag);
    const zat = plusDagen(v, (6 - v.getDay() + 7) % 7 || 7);
    const opties = [
      ["Vandaag", iso(v)],
      ["Morgen", iso(plusDagen(v, 1))],
      ["Zaterdag", iso(zat)],
      ["Over een week", iso(plusDagen(v, 7))],
    ];
    return `<form data-form="plan" class="vform">
      <h2>Plannen</h2>
      <p class="zacht">${esc(t.titel)}</p>
      <div class="chips">${opties.map(([l, d]) => `<button type="button" class="chip" data-act="zetdatum" data-v="${d}">${l}</button>`).join("")}</div>
      <div class="rij">
        <label class="veld">Datum<input type="date" name="datum" value="${esc(t.datum || iso(plusDagen(v, 1)))}" required autofocus></label>
        <label class="veld">Wie<br>${this._persoonSelect("persoon", t.persoon)}</label>
      </div>
      <div class="knoppen">
        <button class="primary" type="submit">Plannen</button>
        <button class="sec-btn" type="button" data-act="sluit">Annuleren</button>
      </div>
    </form>`;
  }

  _klaarView(t) {
    if (!t) return "";
    const voorstel = iso(volgende(vanIso(this.vandaag), t.herhaling));
    const stap = t.herhaling.eenheid === "dagen" ? 1 : 7;
    return `<form data-form="klaar" class="vform">
      <h2>Klaar ✓</h2>
      <p><b>${esc(t.titel)}</b><br><span class="zacht">↻ ${esc(herhTekst(t.herhaling))}${t.laatst ? ` · vorige keer ${esc(datumKort(t.laatst))}` : ""}</span></p>
      <label class="veld">Volgende keer<input type="date" name="volgende" value="${voorstel}" required autofocus></label>
      <div class="chips">
        <button type="button" class="chip" data-act="schuif" data-v="-${stap}">−${stap === 7 ? "1 week" : "1 dag"}</button>
        <button type="button" class="chip" data-act="zetdatum" data-v="${voorstel}">${esc(datumKort(voorstel))} (${esc(herhTekst(t.herhaling))})</button>
        <button type="button" class="chip" data-act="schuif" data-v="${stap}">+${stap === 7 ? "1 week" : "1 dag"}</button>
      </div>
      <p class="hint">Het klusje komt op die datum weer in Gepland en in de agenda.</p>
      <label class="check"><input type="checkbox" name="stop"> Niet meer herhalen (laatste keer)</label>
      <div class="knoppen">
        <button class="primary" type="submit">Afronden</button>
        <button class="sec-btn" type="button" data-act="sluit">Annuleren</button>
      </div>
    </form>`;
  }

  _instView() {
    const i = this.d.instellingen;
    const notify = this.d.notify || [];
    const personen = this._instPersonen || i.personen;
    let h = `<form data-form="inst" class="vform">
      <h2>Instellingen</h2>
      <label class="veld">Agenda voor geplande klusjes
        <select name="agenda"><option value="">Geen agenda</option>${(this.d.agendas || [])
          .map((a) => `<option ${a === this.d.agenda ? "selected" : ""}>${esc(a)}</option>`)
          .join("")}</select></label>
      <div class="rij">
        <label class="check"><input type="checkbox" name="meldingen" ${i.meldingen !== false ? "checked" : ""}> Dagelijkse melding</label>
        <label class="veld smal">om<input type="time" name="meldtijd" value="${esc(i.meldtijd || "08:00")}"></label>
        <label class="veld smal">Klaar tonen (dagen)<input type="number" name="klaar_dagen" min="1" max="365" value="${esc(i.klaar_dagen || 14)}"></label>
      </div>
      <h3>Personen</h3>
      <p class="hint">Wie krijgt een melding op welke telefoon? "Ook zonder persoon" krijgt ook de klusjes die aan niemand zijn toegewezen.</p>
      <div class="personen">${personen
        .map(
          (p, n) => `<div class="prij">
          <input name="p_naam_${n}" value="${esc(p.naam)}" aria-label="Naam" placeholder="Naam">
          <select name="p_notify_${n}" aria-label="Telefoon"><option value="">Geen melding</option>${notify
            .map((x) => `<option value="${esc(x)}" ${x === p.notify ? "selected" : ""}>${esc(x.replace("mobile_app_", "📱 "))}</option>`)
            .join("")}</select>
          <label class="check klein"><input type="checkbox" name="p_ong_${n}" ${p.ongetoewezen ? "checked" : ""}> ook zonder persoon</label>
          <button type="button" class="sec-btn klein" data-act="test" data-n="${n}" ${p.notify ? "" : "disabled"}>Test</button>
          <button type="button" class="wis klein" data-act="p_weg" data-n="${n}" aria-label="${esc(p.naam)} verwijderen">✕</button>
        </div>`
        )
        .join("")}</div>
      <button type="button" class="link" data-act="p_erbij">+ persoon</button>
      <div class="knoppen">
        <button class="primary" type="submit">Opslaan</button>
        <button class="sec-btn" type="button" data-act="sluit">Sluiten</button>
      </div>
    </form>
    <hr>
    <h3>Overnemen uit de agenda</h3>
    <p class="hint">Zoekt herhalende afspraken in de gekozen agenda en maakt er terugkerende klusjes van.</p>`;
    if (!this.import) {
      h += `<button class="sec-btn" data-act="import_zoek">Zoek herhalende afspraken</button>`;
    } else if (!this.import.length) {
      h += `<p class="leeg">Geen herhalende afspraken gevonden.</p>`;
    } else {
      h += `<form data-form="import" class="vform"><ul class="import">${this.import
        .map(
          (r, n) => `<li>
          <label class="check"><input type="checkbox" name="i_${n}" ${r.bestaat_al ? "" : "checked"}> <b>${esc(r.titel)}</b></label>
          <small class="zacht">${esc(herhTekst(r.herhaling))}${r.laatst ? ` · laatst ${esc(datumKort(r.laatst))}` : ""}${r.bestaat_al ? " · bestaat al als klusje" : ""}</small>
          <div class="rij">
            <label class="veld">Volgende keer<input type="date" name="i_datum_${n}" value="${esc(r.volgende)}"></label>
            <label class="veld">Wie<br>${this._persoonSelect(`i_persoon_${n}`, "")}</label>
          </div>
        </li>`
        )
        .join("")}</ul>
        <label class="check"><input type="checkbox" name="verwijderen" checked> Oude herhalende afspraken uit de agenda verwijderen (anders staan ze er dubbel in)</label>
        <div class="knoppen"><button class="primary" type="submit">Overnemen</button><button class="sec-btn" type="button" data-act="import_weg">Annuleren</button></div>
      </form>`;
    }
    return h;
  }

  // ------------------------------------------------------------------ events
  _click(e) {
    const b = e.target.closest("[data-act]");
    if (!b) {
      if (e.target.classList.contains("overlay")) this._sluit();
      return;
    }
    const act = b.dataset.act;
    const id = b.dataset.id;
    const f = b.closest("form");
    if (act === "tab") {
      this.tab = b.dataset.v;
      return this._render();
    }
    if (act === "persoon") {
      this.persoon = b.dataset.v;
      try {
        localStorage.setItem("klusjes_persoon", this.persoon);
      } catch (err) {
        /* niet bewaren */
      }
      return this._render();
    }
    if (act === "sluit") return this._sluit();
    if (act === "nieuw") return this._open({ soort: "nieuw" });
    if (act === "bewerk") return this._open({ soort: "bewerk", id });
    if (act === "plan") return this._open({ soort: "plan", id });
    if (act === "inst") {
      this._instPersonen = null;
      return this._open({ soort: "inst" });
    }
    if (act === "start") return this._do({ type: "boodschappen/klusjes_status", taak_id: id, status: "bezig" }, "Gestart.");
    if (act === "naar_backlog") {
      this.modal = null;
      return this._do({ type: "boodschappen/klusjes_status", taak_id: id, status: "backlog" }, "Terug in de backlog.");
    }
    if (act === "heropen") return this._do({ type: "boodschappen/klusjes_heropen", taak_id: id }, "Teruggezet.");
    if (act === "klaar") {
      const t = this.taak(id);
      if (t && t.herhaling) return this._open({ soort: "klaar", id });
      this.modal = null;
      return this._do({ type: "boodschappen/klusjes_afronden", taak_id: id }, `"${t ? t.titel : "Klusje"}" is klaar.`);
    }
    if (act === "weg") {
      const t = this.taak(id);
      if (!confirm(`"${t ? t.titel : "Dit klusje"}" verwijderen?`)) return;
      this.modal = null;
      return this._do({ type: "boodschappen/klusjes_weg", taak_id: id }, "Verwijderd.");
    }
    if (act === "zetdatum" && f) {
      f.querySelector('input[type="date"]').value = b.dataset.v;
      return;
    }
    if (act === "schuif" && f) {
      const inp = f.querySelector('input[name="volgende"]');
      inp.value = iso(plusDagen(vanIso(inp.value || this.vandaag), Number(b.dataset.v)));
      return;
    }
    if (act === "p_erbij" || act === "p_weg") {
      const lijst = this._leesPersonen();
      if (act === "p_erbij") lijst.push({ naam: "", notify: null, ongetoewezen: false });
      else lijst.splice(Number(b.dataset.n), 1);
      this._instPersonen = lijst;
      this._forceer = true;
      return this._renderModal();
    }
    if (act === "test") {
      const p = this._leesPersonen()[Number(b.dataset.n)];
      if (p && p.notify) this._do({ type: "boodschappen/klusjes_test_melding", notify: p.notify }, `Testmelding gestuurd naar ${p.naam}.`);
      return;
    }
    if (act === "import_zoek") return this._importZoek();
    if (act === "import_weg") {
      this.import = null;
      this._forceer = true;
      return this._renderModal();
    }
  }

  _open(m) {
    this.modal = m;
    this.melding = "";
    this._renderModal();
  }
  _sluit() {
    this.modal = null;
    this.import = null;
    this._instPersonen = null;
    this._renderModal();
  }

  _change(e) {
    const f = e.target.closest('form[data-form="bewerk"]');
    if (f && (e.target.name === "elke" || e.target.name === "eenheid")) f.querySelector('input[value="terug"]').checked = true;
  }

  _leesPersonen() {
    const f = this.shadowRoot.querySelector('form[data-form="inst"]');
    const basis = this._instPersonen || this.d.instellingen.personen;
    if (!f) return basis.map((p) => ({ ...p }));
    return basis.map((p, n) => ({
      naam: f.elements[`p_naam_${n}`]?.value.trim() ?? p.naam,
      notify: f.elements[`p_notify_${n}`]?.value || null,
      ongetoewezen: !!f.elements[`p_ong_${n}`]?.checked,
    }));
  }

  async _importZoek() {
    this.busy = true;
    try {
      const r = await this._hass.callWS({ type: "boodschappen/klusjes_import_zoek", agenda: this.shadowRoot.querySelector('select[name="agenda"]')?.value || null });
      this.d = r;
      this.import = r.resultaat || [];
      this.err = "";
    } catch (e) {
      this.err = e.message || "Zoeken in de agenda lukte niet.";
    }
    this.busy = false;
    this._forceer = true;
    this._render();
  }

  async _submit(e) {
    e.preventDefault();
    const f = e.target;
    const v = (n) => f.elements[n]?.value ?? "";
    const soort = f.dataset.form;
    if (soort === "snel") {
      const titel = v("titel").trim();
      if (!titel) return;
      f.elements.titel.value = "";
      const persoon = this.persoon && this.persoon !== "-" ? this.persoon : null;
      await this._do({ type: "boodschappen/klusjes_nieuw", taak: { titel, persoon } }, `"${titel}" staat in de backlog.`);
      this.shadowRoot.querySelector('form[data-form="snel"] input')?.focus();
      return;
    }
    if (soort === "bewerk") {
      const terug = f.querySelector('input[name="soort"]:checked')?.value === "terug";
      const taak = {
        titel: v("titel").trim(),
        persoon: v("persoon") || null,
        ruimte: v("ruimte").trim() || null,
        tijd: v("tijd") || null,
        notitie: v("notitie").trim() || null,
        herhaling: terug ? { elke: Number(v("elke")) || 1, eenheid: v("eenheid") } : null,
      };
      if (!f.elements.datum.disabled) taak.datum = v("datum") || null;
      if (!taak.titel) {
        this.err = "Geef het klusje een naam.";
        return this._render();
      }
      const m = this.modal;
      this.modal = null;
      if (m.soort === "nieuw") await this._do({ type: "boodschappen/klusjes_nieuw", taak }, `"${taak.titel}" toegevoegd.`);
      else await this._do({ type: "boodschappen/klusjes_wijzig", taak_id: m.id, taak }, "Opgeslagen.");
      return;
    }
    if (soort === "plan") {
      const id = this.modal.id;
      this.modal = null;
      await this._do({ type: "boodschappen/klusjes_wijzig", taak_id: id, taak: { datum: v("datum"), persoon: v("persoon") || null } }, `Gepland op ${datumKort(v("datum"))}.`);
      return;
    }
    if (soort === "klaar") {
      const id = this.modal.id;
      const stop = f.elements.stop.checked;
      this.modal = null;
      const res = await this._do(
        { type: "boodschappen/klusjes_afronden", taak_id: id, volgende: stop ? null : v("volgende"), geen_volgende: stop },
        stop ? "Klaar. Dit klusje komt niet meer terug." : `Klaar. Volgende keer: ${datumKort(v("volgende"))}.`
      );
      return res;
    }
    if (soort === "inst") {
      const inst = {
        agenda: v("agenda") || null,
        meldingen: f.elements.meldingen.checked,
        meldtijd: v("meldtijd") || "08:00",
        klaar_dagen: Number(v("klaar_dagen")) || 14,
        personen: this._leesPersonen().filter((p) => p.naam),
      };
      this.modal = null;
      this._instPersonen = null;
      await this._do({ type: "boodschappen/klusjes_instellingen", instellingen: inst }, "Instellingen opgeslagen.");
      return;
    }
    if (soort === "import") {
      const keuzes = this.import
        .map((r, n) => ({ r, n }))
        .filter(({ n }) => f.elements[`i_${n}`].checked)
        .map(({ r, n }) => ({
          titel: r.titel,
          herhaling: r.herhaling,
          tijd: r.tijd,
          uid: r.uid,
          laatst: r.laatst,
          volgende: f.elements[`i_datum_${n}`].value || r.volgende,
          persoon: f.elements[`i_persoon_${n}`].value || null,
        }));
      if (!keuzes.length) return;
      const verwijderen = f.elements.verwijderen.checked;
      if (verwijderen && !confirm(`${keuzes.length} herhalende afspraken worden uit de agenda verwijderd en komen terug als klusjes. Doorgaan?`)) return;
      this.import = null;
      this.modal = null;
      const n = await this._do({ type: "boodschappen/klusjes_import", keuzes, verwijderen }, "");
      if (n) {
        this.melding = `${n} klusjes overgenomen.`;
        this.tab = "gepland";
        this._render();
      }
    }
  }
}

const CSS = `
:host {
  display: flex; flex-direction: column; height: 100%; position: relative;
  background: var(--primary-background-color); color: var(--primary-text-color);
  font-family: var(--paper-font-body1_-_font-family, Roboto, system-ui, sans-serif);
  --acc: var(--primary-color);
  --zacht: color-mix(in srgb, var(--primary-text-color) 8%, transparent);
  --rand: var(--divider-color, rgba(127,127,127,.25));
  --rood: var(--error-color, #c62828);
}
:host(.bezig) .main { opacity: .7; }
* { box-sizing: border-box; }
button { font: inherit; color: inherit; background: none; border: 0; cursor: pointer; -webkit-tap-highlight-color: transparent; }
button:disabled { opacity: .4; cursor: default; }
button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
header { display: flex; align-items: center; gap: 4px; height: var(--header-height, 56px); padding: 0 8px 0 4px;
  background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff); flex: none; }
header h1 { font-size: 20px; font-weight: 400; margin: 0 0 0 8px; flex: 1; }
.hbtn { width: 44px; height: 44px; font-size: 22px; border-radius: 50%; }
.filter { flex: none; background: var(--card-background-color); padding: 8px 12px 0; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { min-height: 36px; padding: 0 14px; border-radius: 18px; background: var(--zacht); font-size: 14px; }
.chip.on { background: color-mix(in srgb, var(--acc) 22%, transparent); color: var(--primary-text-color); font-weight: 500; }
.tabs { display: flex; gap: 2px; padding: 4px 8px 0; background: var(--card-background-color); border-bottom: 1px solid var(--rand); flex: none; overflow-x: auto; }
.tabs button { position: relative; padding: 12px 10px 13px; display: flex; align-items: center; gap: 6px; color: var(--secondary-text-color); font-weight: 500; white-space: nowrap; }
.tabs button.on { color: var(--primary-text-color); }
.tabs button.on::after { content: ""; position: absolute; left: 8px; right: 8px; bottom: -1px; height: 3px; border-radius: 3px 3px 0 0; background: var(--acc); }
.tabs b, .kolom h2 b { font-size: 12px; min-width: 20px; height: 20px; line-height: 20px; padding: 0 6px; border-radius: 10px; background: var(--zacht); font-weight: 600; text-align: center; }
.tabs b:empty, .kolom h2 b:empty { display: none; }
b.rood { background: var(--rood); color: #fff; }
.main { flex: 1; overflow-y: auto; padding: 10px 12px 96px; -webkit-overflow-scrolling: touch; }
.fout { background: color-mix(in srgb, var(--rood) 15%, transparent); padding: 10px 12px; border-radius: 10px; }
.melding { background: color-mix(in srgb, var(--success-color, #2e7d32) 15%, transparent); padding: 10px 12px; border-radius: 10px; }
.leeg { color: var(--secondary-text-color); padding: 12px 4px 0; }
.hint { font-size: 13px; color: var(--secondary-text-color); margin: 4px 2px; }
.zacht { color: var(--secondary-text-color); }
.rood { color: var(--rood); font-weight: 500; }
.link { color: var(--primary-color); font-size: 14px; padding: 8px 2px; }
.knoppen { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 12px; }
.vul { flex: 1; }
.primary { min-height: 44px; padding: 0 18px; border-radius: 22px; background: var(--acc); color: var(--text-primary-color, #fff); font-weight: 500; }
.sec-btn { min-height: 40px; padding: 0 14px; border-radius: 20px; border: 1px solid var(--rand); background: var(--card-background-color); font-size: 14px; }
.wis { min-height: 40px; padding: 0 14px; border-radius: 20px; font-size: 14px; color: var(--rood); border: 1px solid color-mix(in srgb, var(--rood) 40%, transparent); }
.klein { min-height: 34px; padding: 0 10px; font-size: 13px; }
.snel { display: flex; gap: 8px; }
.snel input { flex: 1; min-width: 0; height: 44px; padding: 0 14px; border-radius: 22px; border: 1px solid var(--rand);
  background: var(--card-background-color); color: var(--primary-text-color); font-size: 16px; }
.snel .primary { width: 44px; padding: 0; font-size: 24px; }
.lijst { list-style: none; padding: 0; margin: 8px 0 0; display: grid; gap: 8px; }
.kaart { display: flex; align-items: center; gap: 6px; background: var(--card-background-color); border-radius: 12px;
  box-shadow: 0 0 0 1px var(--rand) inset; padding: 4px 8px 4px 4px; }
.kaart.vandaag { box-shadow: 0 0 0 2px var(--acc) inset; }
.kaart.laat { box-shadow: 0 0 0 2px var(--rood) inset; background: color-mix(in srgb, var(--rood) 7%, var(--card-background-color)); }
.kaart.af .t { text-decoration: line-through; color: var(--secondary-text-color); }
.kbody { flex: 1; min-width: 0; text-align: left; display: grid; gap: 2px; padding: 8px; border-radius: 8px; }
.kbody .t { font-size: 16px; line-height: 1.25; overflow-wrap: anywhere; }
.kbody small { color: var(--secondary-text-color); font-size: 13px; }
.kknoppen { display: flex; gap: 6px; align-items: center; flex: none; }
.vink { width: 44px; height: 44px; border-radius: 50%; border: 2px solid var(--acc); color: var(--acc); font-size: 20px; font-weight: 700; }
.vink:hover { background: color-mix(in srgb, var(--acc) 15%, transparent); }
.bord { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; align-items: start; }
.kolom { background: var(--zacht); border-radius: 14px; padding: 10px; min-height: 200px; }
.kolom h2 { font-size: 15px; font-weight: 500; margin: 2px 4px 10px; display: flex; align-items: center; gap: 8px; }
.kolom .kaart { flex-wrap: wrap; }
.laatlabel { font-size: 12px; color: var(--rood); font-weight: 500; }
.overlay { display: none; position: absolute; inset: 0; background: rgba(0,0,0,.45); z-index: 5; align-items: flex-end; justify-content: center; }
.overlay.open { display: flex; }
.dialoog { background: var(--card-background-color); width: 100%; max-width: 560px; max-height: 92%; overflow-y: auto;
  border-radius: 18px 18px 0 0; padding: 16px 16px 24px; }
@media (min-width: 700px) { .overlay { align-items: center; } .dialoog { border-radius: 18px; } }
.dialoog h2 { margin: 0 0 8px; font-size: 20px; font-weight: 500; }
.dialoog h3 { margin: 14px 0 4px; font-size: 16px; font-weight: 500; }
.dialoog hr { border: 0; border-top: 1px solid var(--rand); margin: 18px 0 6px; }
.vform { display: grid; gap: 10px; }
.rij { display: flex; gap: 10px; flex-wrap: wrap; align-items: end; }
.rij .veld { flex: 1; min-width: 140px; }
.rij .veld.smal { flex: 0 1 130px; min-width: 110px; }
.veld { display: grid; gap: 4px; font-size: 13px; color: var(--secondary-text-color); }
.veld br { display: none; }
.veld input, .veld select, .veld textarea, .prij input, .prij select, .herh select, .herh input {
  min-height: 44px; padding: 8px 12px; border-radius: 10px; border: 1px solid var(--rand); font-size: 16px;
  background: var(--primary-background-color); color: var(--primary-text-color); font-family: inherit; width: 100%; }
.herh { border: 1px solid var(--rand); border-radius: 12px; padding: 8px 12px 10px; margin: 0; display: grid; gap: 8px; }
.herh legend { font-size: 13px; color: var(--secondary-text-color); padding: 0 4px; }
.radio { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 15px; }
.radio input[type="radio"] { width: 20px; height: 20px; }
.herh .getal { width: 76px; }
.herh select { width: auto; }
.check { display: flex; align-items: center; gap: 8px; font-size: 15px; min-height: 40px; }
.check input { width: 20px; height: 20px; }
.check.klein { font-size: 13px; min-height: 34px; }
.personen { display: grid; gap: 8px; }
.prij { display: grid; grid-template-columns: 1fr 1.3fr auto auto auto; gap: 6px; align-items: center; }
@media (max-width: 600px) { .prij { grid-template-columns: 1fr 1fr; } .prij .check { grid-column: 1 / -1; } }
.import { list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }
.import li { border: 1px solid var(--rand); border-radius: 12px; padding: 8px 10px; display: grid; gap: 4px; }
@media (min-width: 700px) and (max-width: 999px) {
  .main { padding: 14px 24px 96px; }
  .main > * { max-width: 720px; margin-left: auto; margin-right: auto; }
  .filter .chips { max-width: 720px; margin: 0 auto; }
}
@media (min-width: 1000px) { .main { padding: 14px 16px 40px; } }
`;

if (!customElements.get("klusjes-panel")) customElements.define("klusjes-panel", KlusjesPanel);
