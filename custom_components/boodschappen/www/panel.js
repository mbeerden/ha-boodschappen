// Boodschappen-paneel voor Home Assistant: lijst per winkel op looproute, afronden in de winkel + bonnetjes koppelen.
const ROUTE_KLEUR = { lidl: "#1d5fae", jumbo: "#d39b00" };

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtNum = (n) => {
  const v = Number(n);
  return Number.isInteger(v) ? String(v) : v.toLocaleString("nl-NL", { maximumFractionDigits: 2 });
};
const euro = (n) => (n == null ? "" : "€ " + Number(n).toLocaleString("nl-NL", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const norm = (s) => String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/\(.*?\)/g, " ").replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();
const vakNaam = (g) => (g ? g.replace(/^\d+\s*/, "") : "Overig");

function zoek(products, q, max = 6) {
  const n = norm(q);
  if (!n) return [];
  const scored = [];
  for (const p of products) {
    const pn = norm(p.name);
    let s = 0;
    if (pn === n) s = 100;
    else if (pn.startsWith(n)) s = 80 - pn.length / 10;
    else if (pn.split(" ").some((w) => w.startsWith(n))) s = 60 - pn.length / 10;
    else if (pn.includes(n)) s = 40;
    else if (n.length >= 4 && n.split(" ").every((w) => pn.includes(w.slice(0, Math.max(3, w.length - 2))))) s = 20;
    if (s) scored.push([s, p]);
  }
  return scored.sort((a, b) => b[0] - a[0]).slice(0, max).map((x) => x[1]);
}

const svg = (d) => `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="currentColor" d="${d}"/></svg>`;
const ICOON_INFO = svg("M11,9H13V7H11M12,20C7.59,20 4,16.41 4,12C4,7.59 7.59,4 12,4C16.41,4 20,7.59 20,12C20,16.41 16.41,20 12,20M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M11,17H13V11H11V17Z");
const ICOON_COG = svg("M12,15.5A3.5,3.5 0 0,1 8.5,12A3.5,3.5 0 0,1 12,8.5A3.5,3.5 0 0,1 15.5,12A3.5,3.5 0 0,1 12,15.5M19.43,12.97C19.47,12.65 19.5,12.33 19.5,12C19.5,11.67 19.47,11.34 19.43,11L21.54,9.37C21.73,9.22 21.78,8.95 21.66,8.73L19.66,5.27C19.54,5.05 19.27,4.96 19.05,5.05L16.56,6.05C16.04,5.66 15.5,5.32 14.87,5.07L14.5,2.42C14.46,2.18 14.25,2 14,2H10C9.75,2 9.54,2.18 9.5,2.42L9.13,5.07C8.5,5.32 7.96,5.66 7.44,6.05L4.95,5.05C4.73,4.96 4.46,5.05 4.34,5.27L2.34,8.73C2.21,8.95 2.27,9.22 2.46,9.37L4.57,11C4.53,11.34 4.5,11.67 4.5,12C4.5,12.33 4.53,12.65 4.57,12.97L2.46,14.63C2.27,14.78 2.21,15.05 2.34,15.27L4.34,18.73C4.46,18.95 4.73,19.03 4.95,18.95L7.44,17.94C7.96,18.34 8.5,18.68 9.13,18.93L9.5,21.58C9.54,21.82 9.75,22 10,22H14C14.25,22 14.46,21.82 14.5,21.58L14.87,18.93C15.5,18.67 16.04,18.34 16.56,17.94L19.05,18.95C19.27,19.03 19.54,18.95 19.66,18.73L21.66,15.27C21.78,15.05 21.73,14.78 21.54,14.63L19.43,12.97Z");
const WINKELMODUS_KEY = "boodschappen_winkelmodus";
const leesWinkelmodus = () => {
  try {
    return localStorage.getItem(WINKELMODUS_KEY) === "1";
  } catch (_) {
    return false;
  }
};
const bewaarWinkelmodus = (aan) => {
  try {
    localStorage.setItem(WINKELMODUS_KEY, aan ? "1" : "0");
  } catch (_) {}
};
const datumKort = (d) => {
  if (!d) return "";
  const x = new Date(String(d).slice(0, 10) + "T12:00:00");
  return isNaN(x) ? String(d) : x.toLocaleDateString("nl-NL", { day: "numeric", month: "short" });
};

const STATUS = {
  prijs: ["Prijs bijgewerkt", "ok"],
  deels: ["Deels afgevinkt", "warn"],
  los: ["Niet afgevinkt", "warn"],
  geboekt: ["Zelf geboekt", "info"],
  "niet geboekt": ["Niet geboekt", "mute"],
  aangevuld: ["Prijs + extra geboekt", "ok"],
  ongepland: ["Ongepland geboekt", "info"],
  "te koppelen": ["Te koppelen", "warn"],
  genegeerd: ["Genegeerd", "mute"],
  fout: ["Fout", "err"],
  nieuw: ["Nieuw", "mute"],
};

class BoodschappenPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.data = null;
    this.tab = null;
    this.busy = false;
    this.winkel = leesWinkelmodus(); // winkelmodus: compacte lijst voor in de winkel (per apparaat)
    this.afrekenen = false; // afrekenscherm aan het einde van de winkelsessie
    this.lijstOpen = false; // afrekenscherm: "nog op je lijst" uitgeklapt
    this.toast = "";
    this.dealsOpen = false;
    this.dealsAlles = false;
    this.open = {}; // bon-id -> uitgeklapt
    this.edit = null; // {bon_id, line_id}
    this.err = "";
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
    this._timer = setInterval(() => this._poll(), 15000);
    this._vis = () => document.visibilityState === "visible" && this._poll();
    document.addEventListener("visibilitychange", this._vis);
  }
  disconnectedCallback() {
    clearInterval(this._timer);
    document.removeEventListener("visibilitychange", this._vis);
  }

  async _init() {
    this._shell();
    const params = new URLSearchParams(location.search);
    this.tab = params.get("tab") || null;
    this.afrekenen = params.get("afronden") === "1";
    const bon = params.get("bon");
    await this._load();
    if (bon && this.data) this._openBonPopup(bon);
  }

  _url(mut) {
    if (this.inKaart) return; // als dashboardkaart de URL van het dashboard niet aanpassen
    const url = new URL(location.href);
    mut(url.searchParams);
    history.replaceState(null, "", url);
  }

  _poll() {
    const a = this.shadowRoot.activeElement;
    if (this.busy || this.modal || (a && (a.tagName === "INPUT" || a.tagName === "SELECT"))) return;
    this._load();
  }

  async _load() {
    try {
      this.data = await this._hass.callWS({ type: "boodschappen/data" });
      this.err = "";
    } catch (e) {
      this.err = e.message || "Kan de boodschappen niet laden.";
    }
    const geldig = (t) => t === "bonnen" || this.data?.lists?.some((l) => String(l.id) === t);
    if ((!this.tab || !geldig(this.tab)) && this.data?.lists?.length) this.tab = String(this.data.lists[0].id);
    this._render();
  }

  async _do(msg, keepScroll = true) {
    if (this.busy) return;
    this.busy = true;
    this._setBusy(true);
    const y = keepScroll ? this.scrollY() : 0;
    try {
      this.data = await this._hass.callWS(msg);
      this.err = "";
    } catch (e) {
      this.err = e.message || "Dat lukte niet.";
    }
    this.busy = false;
    this._setBusy(false);
    this._render();
    if (keepScroll) this.shadowRoot.querySelector(".main").scrollTop = y;
    return this.data?.resultaat;
  }

  scrollY() {
    return this.shadowRoot.querySelector(".main")?.scrollTop || 0;
  }

  _setBusy(b) {
    this.shadowRoot.querySelector(".bar")?.classList.toggle("busy", b);
    this.shadowRoot.querySelector(".onder")?.classList.toggle("busy", b);
  }

  _toon(tekst) {
    this.toast = tekst;
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => {
      this.toast = "";
      const t = this.shadowRoot.querySelector(".toast");
      if (t) t.hidden = true;
    }, 4000);
    const t = this.shadowRoot.querySelector(".toast");
    if (t) {
      t.textContent = tekst;
      t.hidden = false;
    }
  }

  // ------------------------------------------------------------------ opbouw
  _shell() {
    this.shadowRoot.innerHTML = `
      <style>${CSS}</style>
      <header>
        <div class="menu"></div>
        <h1>Boodschappen</h1>
        <span class="vul"></span>
        <button class="wm" data-act="winkelmodus" aria-pressed="false" title="Winkelmodus: meer ruimte voor de lijst">
          <span class="wm-icoon" aria-hidden="true">🛒</span><span class="wm-tekst">Winkelmodus</span></button>
      </header>
      <nav class="tabs" role="tablist"></nav>
      <div class="bar"></div>
      <div class="main"></div>
      <div class="toast" role="status" hidden></div>
      <div class="onder"></div>
      <div class="modal" hidden></div>`;
    const menu = document.createElement("ha-menu-button");
    menu.hass = this._hass;
    menu.narrow = this._narrow;
    this.shadowRoot.querySelector(".menu").appendChild(menu);
    this.menuBtn = menu;

    const root = this.shadowRoot;
    root.addEventListener("click", (e) => this._click(e));
    root.addEventListener("input", (e) => this._input(e));
    root.addEventListener("keydown", (e) => this._key(e));
  }

  _render() {
    const d = this.data;
    const root = this.shadowRoot;
    const tabs = root.querySelector(".tabs");
    const main = root.querySelector(".main");
    if (!d) {
      main.innerHTML = this.err ? `<p class="fout">${esc(this.err)}</p>` : `<p class="leeg">Laden…</p>`;
      return;
    }
    const open = (lid) => d.items.filter((i) => i.list_id === lid && !i.done).length;
    tabs.innerHTML =
      d.lists
        .map(
          (l) => `<button role="tab" data-tab="${l.id}" class="${this.tab === String(l.id) ? "on" : ""}"
             style="--route:${this._kleur(l.name)}"><span>${esc(l.name)}</span><b>${open(l.id) || ""}</b></button>`
        )
        .join("") +
      `<button role="tab" data-tab="bonnen" class="${this.tab === "bonnen" ? "on" : ""}"><span>Bonnetjes</span>${
        d.queue.length ? `<b class="warn">${d.queue.length}</b>` : ""
      }</button>`;

    const bar = root.querySelector(".bar");
    const onder = root.querySelector(".onder");
    const lijstTab = this.tab !== "bonnen";
    if (!lijstTab) this.afrekenen = false;
    this.classList.toggle("winkel", this.winkel && lijstTab);
    const wm = root.querySelector(".wm");
    wm.hidden = !lijstTab;
    wm.setAttribute("aria-pressed", String(this.winkel));
    wm.classList.toggle("aan", this.winkel);
    if (this.tab === "bonnen") {
      bar.innerHTML = "";
      bar.className = "bar";
      onder.innerHTML = "";
      main.innerHTML = this._bonnen();
    } else if (this.afrekenen) {
      bar.classList.add("weg");
      const lst = d.lists.find((l) => String(l.id) === this.tab);
      main.style.setProperty("--route", this._kleur(lst?.name));
      onder.style.setProperty("--route", this._kleur(lst?.name));
      main.innerHTML = this._afrekenscherm(Number(this.tab), lst);
      onder.innerHTML = this._onderAfrekenen(Number(this.tab));
    } else {
      bar.classList.toggle("weg", this.winkel);
      if (!bar.querySelector("input")) {
        bar.innerHTML = `
          <div class="add">
            <input id="add" type="text" autocomplete="off" enterkeyhint="done" placeholder="Toevoegen, bijv. 2 komkommer" aria-label="Product toevoegen">
            <button class="addbtn" data-act="add" aria-label="Toevoegen">+</button>
          </div>
          <div class="sugg" role="listbox"></div>
          <div class="fav"></div>`;
      }
      bar.querySelector(".fav").innerHTML = this._favorieten(Number(this.tab));
      const lst = d.lists.find((l) => String(l.id) === this.tab);
      bar.style.setProperty("--route", this._kleur(lst?.name));
      main.innerHTML = this._lijst(Number(this.tab));
      main.style.setProperty("--route", this._kleur(lst?.name));
      onder.style.setProperty("--route", this._kleur(lst?.name));
      onder.innerHTML = this._onderLijst(Number(this.tab));
    }
    const t = root.querySelector(".toast");
    t.hidden = !this.toast;
    t.textContent = this.toast;
    if (this.err) main.insertAdjacentHTML("afterbegin", `<p class="fout">${esc(this.err)}</p>`);
  }

  _favorieten(lid) {
    const favs = (this.data.favorieten || {})[String(lid)] || [];
    if (!favs.length) return "";
    const edit = this.favEdit;
    return `<div class="favkop"><span>Vaak gekocht</span>
        <button class="link" data-act="favedit">${edit ? "Klaar" : "Aanpassen"}</button></div>
      <div class="chips-rij">${favs
        .map((f) =>
          edit
            ? `<span class="favchip edit ${f.pinned ? "pin" : ""}">${esc(f.name)}
                 <button data-act="favpin" data-pid="${f.id}" data-pinned="${f.pinned ? 1 : 0}" aria-label="${f.pinned ? "Losmaken" : "Vastpinnen"}: ${esc(f.name)}">${f.pinned ? "Losmaken" : "Vastpinnen"}</button>
                 <button data-act="favhide" data-pid="${f.id}" aria-label="Verbergen: ${esc(f.name)}">Verbergen</button></span>`
            : `<button class="favchip ${f.pinned ? "pin" : ""}" data-act="favadd" data-pid="${f.id}" data-amount="${f.amount}"
                 aria-label="${esc(f.name)} toevoegen">${esc(f.name)}${f.amount !== 1 ? ` <b>×${fmtNum(f.amount)}</b>` : ""}</button>`
        )
        .join("")}</div>`;
  }

  _kleur(name) {
    const n = norm(name);
    return ROUTE_KLEUR[Object.keys(ROUTE_KLEUR).find((k) => n.includes(k))] || "var(--primary-color)";
  }

  _lijst(lid) {
    const d = this.data;
    const items = d.items.filter((i) => i.list_id === lid);
    const todo = items.filter((i) => !i.done);
    const klaar = items.filter((i) => i.done);
    const groepen = [];
    for (const i of todo) {
      const g = i.group || "99 Overig";
      let grp = groepen.find((x) => x.g === g);
      if (!grp) groepen.push((grp = { g, items: [] }));
      grp.items.push(i);
    }
    this._actieOp = new Map(
      (this.data.aanbiedingen || []).filter((a) => a.list_id === lid && a.voordeel !== false).map((a) => [a.product_id, a])
    );
    let html = "";
    if (!todo.length) {
      html += this.winkel
        ? `<div class="leeg"><p>Alles is afgevinkt.${klaar.length ? " Rond je boodschappen af met de knop onderaan." : ""}</p></div>`
        : `<div class="leeg"><p>Niets meer te halen hier.</p><p>Voeg hierboven iets toe, of vul de lijst aan:</p></div>`;
    } else {
      html += `<ol class="route">${groepen
        .map(
          (grp) => `<li class="stop">
            <h2><span>${esc(vakNaam(grp.g))}</span><small>${grp.items.length}</small></h2>
            <ul>${grp.items.map((i) => this._item(i)).join("")}</ul>
          </li>`
        )
        .join("")}<li class="stop eind"><h2><span>Kassa</span></h2></li></ol>`;
    }
    if (this.winkel) return html;
    html += this._aanbiedingen(lid);
    html += `<div class="acties">
        <button data-act="weekvast">Vaste verse boodschappen toevoegen</button>
        <button data-act="tekorten">Tekorten aanvullen</button>
      </div>`;
    return html;
  }

  // ---------------------------------------------------------------- mandje en afronden
  _mandje(lid) {
    return this.data.items.filter((i) => i.list_id === lid && i.done);
  }

  _onderLijst(lid) {
    const n = this._mandje(lid).length;
    if (!n && !this.winkel) return "";
    const plus = this.winkel
      ? `<button class="fab" data-act="toevoegen" aria-label="Product toevoegen" title="Product toevoegen">+</button>`
      : "";
    if (!n) return `<div class="onder-rij"><span class="mandje-n leeg">Mandje is leeg</span>${plus}</div>`;
    return `<div class="onder-rij">
        <button class="mandje-n" data-act="afrekenen"><b>${n}</b> in je mandje</button>
        <button class="primary afronden" data-act="afrekenen">Afronden</button>${plus}
      </div>`;
  }

  _onderAfrekenen(lid) {
    const mandje = this._mandje(lid);
    const metProduct = mandje.filter((i) => i.product_id).length;
    return `<div class="onder-rij">
        <button class="sec-btn" data-act="afrterug">Terug</button>
        <button class="primary afronden breed" data-act="klaar" ${mandje.length ? "" : "disabled"}>
          Klaar${metProduct ? ` · ${metProduct} in voorraad boeken` : ""}</button>
      </div>`;
  }

  _afrekenscherm(lid, lst) {
    const mandje = this._mandje(lid);
    const open = this.data.items.filter((i) => i.list_id === lid && !i.done);
    let html = `<h2 class="sec afr-titel">Afronden · ${esc(lst?.name || "")}</h2>
      <p class="uitleg">Controleer wat je gekocht hebt. Pas aantallen aan of zet iets terug op de lijst.
        Met <b>Klaar</b> gaat alles in één keer in de voorraad.</p>`;
    if (!mandje.length) {
      html += `<p class="leeg">Je mandje is leeg. Vink producten af op de lijst, of voeg hieronder iets toe.</p>`;
    } else {
      html += `<ul class="afr">${mandje
        .map(
          (i) => `<li class="item afr-item">
            <span class="txt"><span class="naam">${esc(i.name)}</span>${
              i.product_id
                ? ""
                : `<small class="geen-vrd">Geen Grocy-product: telt niet mee in de voorraad.
                   <button class="link inline" data-act="maakprod" data-id="${i.id}" data-naam="${esc(i.name)}">Product maken</button></small>`
            }</span>
            <span class="stepper">
              <button data-act="min" data-id="${i.id}" data-amount="${i.amount}" ${i.amount <= 1 ? "disabled" : ""} aria-label="Minder ${esc(i.name)}">−</button>
              <span class="n">${fmtNum(i.amount)}</span>
              <button data-act="plus" data-id="${i.id}" data-amount="${i.amount}" aria-label="Meer ${esc(i.name)}">+</button>
            </span>
            <button class="terug" data-act="vink" data-id="${i.id}" data-done="0" aria-label="${esc(i.name)} terug op de lijst" title="Terug op de lijst">↩</button>
          </li>`
        )
        .join("")}</ul>`;
    }
    html += `<button class="sec-btn vergeten" data-act="toevoegen">+ Iets vergeten af te vinken</button>`;
    if (open.length) {
      html += `<section class="mandje ${this.lijstOpen ? "open" : ""}">
        <button class="kop" data-act="lijstopen" aria-expanded="${this.lijstOpen}">Nog op je lijst <b>${open.length}</b></button>
        ${
          this.lijstOpen
            ? `<p class="hint">Tik op een product dat je toch hebt gekocht.</p>
               <ul>${open
                 .map(
                   (i) => `<li class="item"><button class="vink" data-act="vink" data-id="${i.id}" data-done="1" aria-label="${esc(i.name)} in je mandje">
                       <span class="box" aria-hidden="true"></span><span class="txt"><span class="naam">${esc(i.name)}</span></span></button>
                       <span class="n">${fmtNum(i.amount)}</span></li>`
                 )
                 .join("")}</ul>`
            : ""
        }
      </section>`;
    }
    return html;
  }

  async _klaar() {
    const lid = Number(this.tab);
    const res = await this._do({ type: "boodschappen/afronden", list_id: lid }, false);
    if (!res) return;
    const delen = [`${res.geboekt} ${res.geboekt === 1 ? "product" : "producten"} in de voorraad geboekt`];
    if (res.zonder_product) delen.push(`${res.zonder_product} zonder Grocy-product`);
    if (res.fouten?.length) delen.push(`niet gelukt: ${res.fouten.join(", ")} (staat nog in je mandje)`);
    this.afrekenen = false;
    if (!res.fouten?.length) {
      this.winkel = false;
      bewaarWinkelmodus(false);
    }
    this._url((p) => p.delete("afronden"));
    this._toon(delen.join(" · "));
    this._render();
  }

  // ---------------------------------------------------------------- toevoegen-venster (winkelmodus / afrekenen)
  _openToevoegen() {
    const inMandje = this.afrekenen;
    this.modal = { soort: "toevoegen", done: inMandje };
    const favs = (this.data.favorieten || {})[this.tab] || [];
    this._toonModal(`
      <h2>${inMandje ? "Toevoegen aan je mandje" : "Toevoegen aan de lijst"}</h2>
      <div class="add"><input class="m-zoek" type="text" autocomplete="off" enterkeyhint="done"
        placeholder="Zoek of typ, bijv. 2 komkommer" aria-label="Product zoeken"></div>
      <div class="sugg m-sugg" role="listbox"></div>
      ${
        favs.length
          ? `<div class="favkop"><span>Vaak gekocht</span></div>
             <div class="chips-wrap">${favs
               .map((f) => `<button class="favchip ${f.pinned ? "pin" : ""}" data-act="mkies" data-pid="${f.id}" data-amount="${f.amount}">${esc(f.name)}${
                 f.amount !== 1 ? ` <b>×${fmtNum(f.amount)}</b>` : ""}</button>`)
               .join("")}</div>`
          : ""
      }
      <div class="knoppen"><span class="vul"></span>
        <button class="sec-btn" data-act="modsluit">Sluiten</button>
        <button class="primary" data-act="mtekst">Toevoegen</button></div>`);
    this.shadowRoot.querySelector(".m-zoek").focus();
  }

  _modalSuggest(val) {
    const box = this.shadowRoot.querySelector(".m-sugg");
    if (!box) return;
    const q = val.replace(/^\s*\d+([.,]\d+)?\s*x?\s*/i, "");
    const res = q.length >= 2 ? zoek(this.data.products, q, 6) : [];
    const exact = res.some((p) => norm(p.name) === norm(q));
    box.innerHTML =
      res.map((p) => `<button role="option" data-act="mkies" data-pid="${p.id}">${esc(p.name)}</button>`).join("") +
      (q.trim().length >= 2 && !exact && !this.modal?.done
        ? `<button class="nieuwprod" data-act="mnieuw" data-naam="${esc(q.trim())}">+ Nieuw product: ${esc(q.trim())}</button>`
        : "");
  }

  async _modalToevoegen(pid, vasteAmount) {
    const inp = this.shadowRoot.querySelector(".m-zoek");
    const val = (inp?.value || "").trim();
    if (!pid && !val) return inp?.focus();
    const m = val.match(/^\s*(\d+(?:[.,]\d+)?)/);
    const msg = { type: "boodschappen/item_add", list_id: Number(this.tab), done: !!this.modal?.done };
    let naam = val;
    if (pid) {
      msg.product_id = pid;
      msg.amount = m ? Number(m[1].replace(",", ".")) : vasteAmount || 1;
      naam = this.data.products.find((p) => p.id === pid)?.name || "Product";
    } else msg.text = val;
    this._sluitModal();
    await this._do(msg);
    if (!this.err) this._toon(`${naam} ${msg.done ? "in je mandje" : "op de lijst"}`);
  }

  // ---------------------------------------------------------------- pop-up bonnetje (vanuit de melding)
  _openBonPopup(bonId) {
    const b = (this.data.bonnen || []).find((x) => x.id === bonId);
    this.tab = "bonnen";
    this._render();
    if (!b) {
      this.modal = { soort: "bon" };
      this._toonModal(`<h2>Bonnetje niet gevonden</h2><p class="uitleg">Dit bonnetje staat niet (meer) in het overzicht.</p>
        <div class="knoppen"><span class="vul"></span><button class="sec-btn" data-act="modsluit">Sluiten</button></div>`);
      return;
    }
    this.open[b.id] = true;
    const los = b.regels.filter((r) => r.status === "los" || r.status === "deels");
    this.modal = { soort: "bon", bon: b.id };
    if (!los.length) {
      this._toonModal(`<h2>${esc(b.winkel)} · ${esc(datumKort(b.datum || b.verwerkt))}</h2>
        <p class="uitleg">Alles van dit bonnetje is al afgehandeld. De prijzen staan in Grocy.</p>
        <div class="knoppen"><span class="vul"></span><button class="sec-btn" data-act="modsluit">Sluiten</button></div>`);
      return;
    }
    this._toonModal(`
      <h2>${esc(b.winkel)} · ${esc(datumKort(b.datum || b.verwerkt))}</h2>
      <p class="uitleg">Deze producten stonden op de bon, maar waren niet (of niet allemaal) afgevinkt en staan dus niet in de voorraad.
        Vink aan wat er wél in moet.</p>
      <ul class="kieslijst">${los
        .map(
          (r) => `<li><label>
            <input type="checkbox" class="bk" value="${esc(r.id)}" checked>
            <span class="txt"><span class="naam">${esc(r.grocy_naam || r.tekst)}</span>
              <small>${r.status === "deels" ? `${fmtNum(r.rest)} meer dan afgevinkt` : `${fmtNum(r.rest)} stuks`} · ${euro(r.totaal)} · <code>${esc(r.tekst)}</code></small></span>
          </label></li>`
        )
        .join("")}</ul>
      <div class="knoppen">
        <button class="sec-btn" data-act="bonniets">Niets boeken</button>
        <span class="vul"></span>
        <button class="primary" data-act="bonboeksel">In voorraad boeken</button></div>`);
  }

  async _bonPopupBoek(niets) {
    const m = this.shadowRoot.querySelector(".modal");
    const bonId = this.modal?.bon;
    const alle = [...m.querySelectorAll(".bk")];
    const ja = niets ? [] : alle.filter((c) => c.checked).map((c) => c.value);
    const nee = alle.map((c) => c.value).filter((id) => !ja.includes(id));
    this._sluitModal();
    this._url((p) => p.delete("bon"));
    let n = 0;
    if (ja.length) n = (await this._do({ type: "boodschappen/bon_boek", bon_id: bonId, line_ids: ja })) || 0;
    if (nee.length) await this._do({ type: "boodschappen/bon_niet_boeken", bon_id: bonId, line_ids: nee });
    this._toon(n ? `${n} ${n === 1 ? "product" : "producten"} in de voorraad geboekt` : "Niets geboekt; de prijzen zijn wel bijgewerkt");
  }

  _aanbiedingen(lid) {
    const alle = (this.data.aanbiedingen || []).filter((a) => a.list_id === lid);
    if (!alle.length) return "";
    const goed = alle.filter((a) => a.voordeel !== false);
    const mat = alle.filter((a) => a.voordeel === false);
    const datum = (d) => {
      if (!d) return "";
      const x = new Date(String(d).slice(0, 10) + "T12:00:00");
      return isNaN(x) ? esc(d) : x.toLocaleDateString("nl-NL", { weekday: "short", day: "numeric", month: "short" });
    };
    const rij = (a) => {
      const geenVoordeel = a.voordeel === false;
      const prijs = a.prijs != null ? euro(a.prijs) : "";
      const wanneer = a.soort === "volgende week" && a.van ? `vanaf ${datum(a.van)}` : a.tot ? `t/m ${datum(a.tot)}` : "";
      const soort = a.soort === "coupon" ? `coupon${a.geactiveerd === false ? " (activeer in app)" : ""}` : a.soort === "volgende week" ? "volgende week" : "";
      const meer = !geenVoordeel && Number(a.advies) > Number(a.gewoon);
      const extra = [soort, wanneer, a.normale_prijs != null ? `normaal ${euro(a.normale_prijs)}` : ""].filter(Boolean).join(" · ");
      return `<li class="deal ${geenVoordeel ? "mat" : ""}">
        <div class="deal-txt">
          <div><b>${esc(a.product)}</b> <span class="deal-actie">${esc(a.actie || "")}${prijs ? ` ${prijs}` : ""}</span></div>
          <small title="${esc(a.titel || "")}">${esc(a.titel || "")}${extra ? ` · ${extra}` : ""}${meer && !a.op_lijst ? ` · advies ${fmtNum(a.advies)} (${esc(a.advies_reden || "")})` : ""}</small>
        </div>
        <div class="deal-knoppen">
          ${a.op_lijst ? `<span class="op-lijst">✓ op lijst</span>` : `
            ${meer ? `<button class="primary klein" data-act="dealadd" data-pid="${a.product_id}" data-amount="${a.advies}" title="Inslaan">Inslaan ${fmtNum(a.advies)}</button>` : ""}
            <button class="sec-btn klein" data-act="dealadd" data-pid="${a.product_id}" data-amount="${a.gewoon}">+${fmtNum(a.gewoon)}</button>`}
          <button class="link x" data-act="dealnee" data-key="${esc(a.key)}" aria-label="Niet nu: ${esc(a.product)}" title="Niet nu">×</button>
        </div>
      </li>`;
    };
    const open = this.dealsOpen;
    const kopTekst = goed.length ? `Aanbiedingen <b>${goed.length}</b>` : `Aanbiedingen <span class="zacht">geen voordeel</span>`;
    let body = "";
    if (open) {
      body = `<ul>${goed.map(rij).join("")}${this.dealsAlles ? mat.map(rij).join("") : ""}</ul>`;
      if (mat.length)
        body += `<button class="link meer" data-act="dealsalles">${this.dealsAlles ? "Verberg" : "Toon"} ${mat.length} zonder voordeel</button>`;
    }
    return `<section class="deals ${open ? "open" : ""}">
      <button class="kop" data-act="deals" aria-expanded="${open}">${kopTekst}</button>${body}</section>`;
  }

  _item(i) {
    // winkelmodus: geen tekst onder de naam; extra tekst achter de ⓘ, aanpassen achter het tandwiel
    const note = i.note && !this.winkel ? `<small>${esc(i.note)}</small>` : "";
    const infoKnop = this.winkel && i.note && !i.done
      ? `<button class="info" data-act="notitie" data-id="${i.id}" aria-label="Extra informatie bij ${esc(i.name)}" title="${esc(i.note)}">${ICOON_INFO}</button>`
      : this.winkel && !i.done ? `<span class="info leeg" aria-hidden="true"></span>` : "";
    const extra = i.done
      ? ""
      : i.product_id
      ? `${infoKnop}<button class="info" data-act="info" data-pid="${i.product_id}" aria-label="${esc(i.name)} aanpassen" title="Product aanpassen">${ICOON_COG}</button>`
      : `${infoKnop}<button class="info nieuw" data-act="maakprod" data-id="${i.id}" data-naam="${esc(i.name)}" aria-label="Maak product van ${esc(i.name)}" title="Product aanmaken (vak en winkel)">+</button>`;
    return `<li class="item ${i.done ? "done" : ""}">
      <button class="vink" data-act="vink" data-id="${i.id}" data-done="${i.done ? 0 : 1}" aria-pressed="${i.done}"
        aria-label="${esc(i.name)} ${i.done ? "terugzetten" : "afvinken"}">
        <span class="box" aria-hidden="true"></span>
        <span class="txt"><span class="naam">${esc(i.name)}${!i.done && this._actieOp && this._actieOp.has(i.product_id) ? ` <span class="tag-actie" title="${esc(this._actieOp.get(i.product_id).actie || "in de aanbieding")}">actie</span>` : ""}</span>${note}</span>
      </button>
      ${
        i.done
          ? `<span class="n">${fmtNum(i.amount)}</span>`
          : `<span class="stepper">
              <button data-act="min" data-id="${i.id}" data-amount="${i.amount}" aria-label="Minder ${esc(i.name)}">−</button>
              <span class="n">${fmtNum(i.amount)}</span>
              <button data-act="plus" data-id="${i.id}" data-amount="${i.amount}" aria-label="Meer ${esc(i.name)}">+</button>
            </span>`
      }${extra}
    </li>`;
  }

  _bonnen() {
    const d = this.data;
    let html = "";
    if (d.queue.length) {
      html += `<section class="koppel"><h2 class="sec">Te koppelen</h2>
        <p class="uitleg">Deze bonregels herkende ik niet. Kies het product; volgende keer gaat het vanzelf.</p>
        ${d.queue.map((q) => this._qcard(q)).join("")}</section>`;
    } else {
      html += `<p class="rust">Alle bonregels zijn gekoppeld.</p>`;
    }
    html += `<section><h2 class="sec">Verwerkte bonnetjes</h2>`;
    if (!d.bonnen.length) html += `<p class="leeg">Nog geen bonnetjes verwerkt sinds de nieuwe werkwijze.</p>`;
    for (const b of d.bonnen) {
      const open = this.open[b.id];
      const tel = {};
      b.regels.forEach((r) => (tel[r.status] = (tel[r.status] || 0) + 1));
      const samenv = Object.entries(tel)
        .filter(([s]) => s !== "genegeerd")
        .map(([s, n]) => `<span class="chip ${STATUS[s]?.[1] || ""}">${n} ${esc((STATUS[s]?.[0] || s).toLowerCase())}</span>`)
        .join("");
      html += `<article class="bon ${open ? "open" : ""}">
        <button class="bonkop" data-act="bon" data-id="${b.id}" aria-expanded="${!!open}">
          <span class="w">${esc(b.winkel)}</span><span class="d">${esc(b.datum || (b.verwerkt || "").slice(0, 10))}</span>
          <span class="t">${euro(b.totaal)}</span>
          <span class="chips">${samenv}</span>
        </button>
        ${open && b.regels.some((r) => r.status === "los" || r.status === "deels")
          ? `<button class="sec-btn klein bonlos" data-act="bonpopup" data-id="${b.id}">Niet afgevinkt: kiezen wat in de voorraad moet</button>` : ""}
        ${open ? `<ul class="regels">${b.regels.map((r) => this._bonregel(b, r)).join("")}</ul>` : ""}
      </article>`;
    }
    html += `</section>`;
    return html;
  }

  _bonregel(b, r) {
    const st = STATUS[r.status] || [r.status, ""];
    const editing = this.edit && this.edit.bon_id === b.id && this.edit.line_id === r.id;
    const doel = r.grocy_naam
      ? `${esc(r.grocy_naam)}${r.factor && r.factor !== 1 ? ` <em>× ${fmtNum(r.factor)} per stuk op de bon</em>` : ""}`
      : "";
    return `<li class="regel ${editing ? "editing" : ""}">
      <div class="rij">
        <code class="bontekst">${esc(r.tekst)}</code>
        <span class="bedrag">${fmtNum(r.aantal)} · ${euro(r.totaal)}</span>
      </div>
      <div class="rij2">
        <span class="chip ${st[1]}">${esc(st[0])}</span>
        <span class="doel">${doel}</span>
        ${(r.status === "los" || r.status === "deels") && r.product_id
          ? `<button class="sec-btn klein" data-act="bonboek" data-bon="${b.id}" data-line="${r.id}" title="${fmtNum(r.rest)} in de voorraad boeken">Boek ${fmtNum(r.rest)}</button>` : ""}
        ${r.status !== "te koppelen" ? `<button class="link" data-act="herkoppel" data-bon="${b.id}" data-line="${r.id}">${editing ? "Annuleren" : "Wijzigen"}</button>` : ""}
      </div>
      ${editing ? this._kiezer({ key: `e:${b.id}:${r.id}`, suggestie: r.grocy_naam || r.tekst, factor: r.factor || 1, mode: "herkoppel", bon: b.id, line: r.id, totaal: r.totaal }) : ""}
    </li>`;
  }

  _qcard(q) {
    return `<article class="qcard">
      <div class="rij">
        <code class="bontekst">${esc(q.tekst)}</code>
        <span class="bedrag">${fmtNum(q.aantal)} · ${euro(q.totaal)}</span>
      </div>
      <p class="meta">${esc(q.winkel)}${q.datum ? `, ${esc(q.datum)}` : ""}${q.suggestie && norm(q.suggestie) !== norm(q.tekst) ? ` — uitgelezen als “${esc(q.suggestie)}”` : ""}</p>
      ${this._kiezer({ key: `q:${q.id}`, suggestie: q.suggestie, factor: q.factor || 1, mode: "queue", qid: q.id })}
    </article>`;
  }

  _kiezer(o) {
    const groups = this.data.groups.map((g) => `<option value="${g.id}">${esc(vakNaam(g.name))}</option>`).join("");
    const kandidaten = zoek(this.data.products, o.suggestie, 4);
    return `<div class="kiezer" data-key="${esc(o.key)}" data-mode="${o.mode}" data-qid="${esc(o.qid || "")}" data-bon="${esc(o.bon || "")}" data-line="${esc(o.line || "")}">
      <label class="veld">Product in Grocy
        <input class="pzoek" type="text" autocomplete="off" value="" placeholder="Zoek product…">
      </label>
      <div class="kandidaten">${kandidaten
        .map((p) => `<button class="kand" data-act="kies" data-pid="${p.id}">${esc(p.name)}</button>`)
        .join("")}</div>
      <label class="veld klein">Stuks per bonregel
        <input class="factor" type="number" min="0.01" step="any" inputmode="decimal" value="${fmtNum(o.factor)}">
      </label>
      <p class="hint">Bijv. 6 als één regel op de bon een doosje van 6 eieren is.</p>
      ${o.mode === "herkoppel" ? `<label class="veld klein">Bedrag op de bon (€)
        <input class="btotaal" type="number" step="0.01" inputmode="decimal" value="${o.totaal ?? ""}" data-oud="${o.totaal ?? ""}">
      </label>
      <p class="hint">Alleen aanpassen als het bedrag verkeerd is uitgelezen (bijv. een actiekorting). Kies daarna het product, of tik op het huidige product om alleen het bedrag te corrigeren.</p>` : ""}
      <div class="knoppen">
        <button class="sec-btn" data-act="nieuw">Nieuw product…</button>
        <button class="sec-btn" data-act="negeer">Negeren</button>
      </div>
      <div class="nieuwform" hidden>
        <label class="veld">Naam <input class="nnaam" type="text" value="${esc(o.suggestie || "")}"></label>
        <label class="veld">Vak <select class="ngroep">${groups}</select></label>
        <button class="primary" data-act="maak">Product aanmaken en koppelen</button>
      </div>
    </div>`;
  }

  _totaal(k) {
    const t = k.querySelector(".btotaal");
    if (!t || t.value === "" || t.value === t.dataset.oud) return null;
    return Number(String(t.value).replace(",", "."));
  }

  // ---------------------------------------------------------------- events
  _suggest(val) {
    const box = this.shadowRoot.querySelector(".sugg");
    if (!box) return;
    const q = val.replace(/^\s*\d+([.,]\d+)?\s*x?\s*/i, "");
    const res = q.length >= 2 ? zoek(this.data.products, q, 5) : [];
    const exact = res.some((p) => norm(p.name) === norm(q));
    box.innerHTML =
      res.map((p) => `<button role="option" data-act="sugg" data-pid="${p.id}">${esc(p.name)}</button>`).join("") +
      (q.trim().length >= 2 && !exact
        ? `<button class="nieuwprod" data-act="nieuwprod" data-naam="${esc(q.trim())}">+ Nieuw product: ${esc(q.trim())}</button>`
        : "");
  }

  _input(e) {
    const t = e.target;
    if (t.id === "add") this._suggest(t.value);
    if (t.classList.contains("m-zoek")) this._modalSuggest(t.value);
    if (t.classList.contains("pzoek")) {
      const k = t.closest(".kiezer");
      const res = zoek(this.data.products, t.value || "", 6);
      k.querySelector(".kandidaten").innerHTML = res
        .map((p) => `<button class="kand" data-act="kies" data-pid="${p.id}">${esc(p.name)}</button>`)
        .join("");
    }
  }

  _key(e) {
    if (e.key === "Enter" && e.target.id === "add") {
      e.preventDefault();
      this._add();
    }
    if (e.key === "Enter" && e.target.classList.contains("m-zoek")) {
      e.preventDefault();
      this._modalToevoegen();
    }
  }

  async _add(pid) {
    const inp = this.shadowRoot.getElementById("add");
    const val = inp.value.trim();
    if (!val && !pid) return;
    const m = val.match(/^\s*(\d+(?:[.,]\d+)?)/);
    const msg = { type: "boodschappen/item_add", list_id: Number(this.tab) };
    if (pid) {
      msg.product_id = pid;
      msg.amount = m ? Number(m[1].replace(",", ".")) : 1;
    } else msg.text = val;
    inp.value = "";
    this._suggest("");
    await this._do(msg);
    this.shadowRoot.getElementById("add")?.focus();
  }

  // ---------------------------------------------------------------- productvensters
  _opties(lijst, gekozen, leeg) {
    return (leeg ? `<option value="">${esc(leeg)}</option>` : "") +
      lijst.map((x) => `<option value="${x.id}" ${Number(gekozen) === Number(x.id) ? "selected" : ""}>${esc(x.label || x.name)}</option>`).join("");
  }

  _toonModal(html) {
    const m = this.shadowRoot.querySelector(".modal");
    m.innerHTML = `<div class="venster" role="dialog" aria-modal="true">${html}</div>`;
    m.hidden = false;
    m.onclick = (e) => {
      if (e.target === m) this._sluitModal();
    };
  }

  _sluitModal() {
    if (this.modal?.soort === "bon") this._url((p) => p.delete("bon"));
    this.modal = null;
    const m = this.shadowRoot.querySelector(".modal");
    m.hidden = true;
    m.innerHTML = "";
  }

  _groepen() {
    return this.data.groups.map((g) => ({ id: g.id, label: vakNaam(g.name) }));
  }

  async _openNieuw(naam, itemId, amount) {
    this.modal = { soort: "nieuw", itemId, amount };
    const winkels = this.data.winkels || [];
    const lijstWinkel = winkels.find((w) => norm(w.name) === norm(this.data.lists.find((l) => String(l.id) === this.tab)?.name));
    this._toonModal(`
      <h2>Nieuw product</h2>
      <label class="veld">Naam <input class="m-naam" type="text" value="${esc(naam)}"></label>
      <label class="veld">Vak (looproute) <select class="m-groep">${this._opties(this._groepen(), null, "— kies een vak —")}</select></label>
      <label class="veld">Winkel <select class="m-winkel">${this._opties(winkels, lijstWinkel?.id)}</select></label>
      <label class="veld klein">Aantal <input class="m-aantal" type="number" min="0.5" step="any" inputmode="decimal" value="${fmtNum(amount || 1)}"></label>
      <p class="hint m-ai"><span class="spin" aria-hidden="true"></span> Gemini zoekt vak en winkel…</p>
      <div class="knoppen"><span class="vul"></span>
        <button class="sec-btn" data-act="modsluit">Annuleren</button>
        <button class="primary" data-act="nieuwmaak">${itemId ? "Product maken" : "Toevoegen"}</button></div>`);
    this.shadowRoot.querySelector(".m-naam").focus();
    const m = this.shadowRoot.querySelector(".modal");
    let aangeraakt = false;
    m.querySelectorAll("select").forEach((s) => s.addEventListener("change", () => (aangeraakt = true)));
    try {
      const r = await this._hass.callWS({ type: "boodschappen/product_raden", naam });
      const g = r.resultaat || {};
      if (this.modal?.soort !== "nieuw") return;
      if (!aangeraakt) {
        if (g.group_id) m.querySelector(".m-groep").value = String(g.group_id);
        if (g.winkel_id) m.querySelector(".m-winkel").value = String(g.winkel_id);
      }
      const n = m.querySelector(".m-naam");
      if (g.naam && n.value.trim().toLowerCase() === naam.trim().toLowerCase()) n.value = g.naam;
      m.querySelector(".m-ai").textContent = g.group_id ? "Voorstel van Gemini; pas aan als het niet klopt." : "Kies het vak en de winkel.";
    } catch (_) {
      const h = m.querySelector(".m-ai");
      if (h) h.textContent = "Kies het vak en de winkel.";
    }
  }

  async _nieuwMaak() {
    const m = this.shadowRoot.querySelector(".modal");
    const naam = m.querySelector(".m-naam").value.trim();
    const group = Number(m.querySelector(".m-groep").value) || null;
    if (!naam) return m.querySelector(".m-naam").focus();
    if (!group) return m.querySelector(".m-groep").focus();
    const msg = { type: "boodschappen/product_nieuw", naam, group_id: group, winkel_id: Number(m.querySelector(".m-winkel").value) || null,
      amount: Number(String(m.querySelector(".m-aantal").value).replace(",", ".")) || 1, item_id: this.modal.itemId || null };
    this._sluitModal();
    await this._do(msg);
  }

  async _openDetails(pid) {
    this.modal = { soort: "details", pid };
    this._toonModal(`<p class="hint"><span class="spin" aria-hidden="true"></span> Laden…</p>`);
    let p;
    try {
      const r = await this._hass.callWS({ type: "boodschappen/product_details", product_id: pid });
      p = r.resultaat;
    } catch (e) {
      this._toonModal(`<p class="fout">${esc(e.message || "Laden mislukt")}</p><div class="knoppen"><span class="vul"></span><button class="sec-btn" data-act="modsluit">Sluiten</button></div>`);
      return;
    }
    if (this.modal?.pid !== pid) return;
    this._toonModal(`
      <h2>${esc(p.name)}</h2>
      <label class="veld">Naam <input class="d-naam" type="text" value="${esc(p.name)}"></label>
      <label class="veld">Vak (looproute) <select class="d-groep">${this._opties(this._groepen(), p.group_id, "— geen —")}</select></label>
      <label class="veld">Winkel <select class="d-winkel">${this._opties(this.data.winkels || [], p.winkel_id, "— geen —")}</select></label>
      <div class="rij3">
        <label class="veld klein">In huis <input class="d-voorraad" type="number" min="0" step="any" inputmode="decimal" value="${fmtNum(p.voorraad)}" data-oud="${p.voorraad}"></label>
        <label class="veld klein">Minimum <input class="d-min" type="number" min="0" step="any" inputmode="decimal" value="${fmtNum(p.min_stock)}"></label>
        <label class="veld klein">Elke week <input class="d-week" type="number" min="0" step="any" inputmode="decimal" value="${fmtNum(p.wekelijks)}"></label>
      </div>
      <p class="hint">Minimum: komt bij "Tekorten aanvullen" op de lijst zodra er minder in huis is. Zet op 0 als je het alleen zelf toevoegt.
        Elke week: vaste hoeveelheid voor "Vaste verse boodschappen".</p>
      <div class="knoppen">
        <button class="sec-btn" data-act="detop" title="Voorraad op 0 en op de lijst zetten">Op</button>
        <span class="vul"></span>
        <button class="sec-btn" data-act="modsluit">Annuleren</button>
        <button class="primary" data-act="detsave">Opslaan</button></div>`);
  }

  async _detailsOpslaan() {
    const m = this.shadowRoot.querySelector(".modal");
    const num = (sel) => Number(String(m.querySelector(sel).value).replace(",", ".")) || 0;
    const v = m.querySelector(".d-voorraad");
    const velden = {
      name: m.querySelector(".d-naam").value.trim(),
      group_id: Number(m.querySelector(".d-groep").value) || null,
      winkel_id: Number(m.querySelector(".d-winkel").value) || null,
      min_stock: num(".d-min"),
      wekelijks: num(".d-week"),
    };
    if (v.value !== "" && Number(String(v.value).replace(",", ".")) !== Number(v.dataset.oud)) velden.voorraad = num(".d-voorraad");
    const pid = this.modal.pid;
    this._sluitModal();
    await this._do({ type: "boodschappen/product_opslaan", product_id: pid, velden });
  }

  async _detailsOp() {
    const pid = this.modal.pid;
    this._sluitModal();
    await this._do({ type: "boodschappen/product_op", product_id: pid });
  }

  async _click(e) {
    const b = e.target.closest("button");
    if (!b) return;
    const act = b.dataset.act;
    if (b.dataset.tab) {
      this.tab = b.dataset.tab;
      this.edit = null;
      this.afrekenen = false;
      this._url((p) => {
        if (this.tab === "bonnen") p.set("tab", "bonnen");
        else p.delete("tab");
        p.delete("afronden");
        p.delete("bon");
      });
      this._render();
      this.shadowRoot.querySelector(".main").scrollTop = 0;
      return;
    }
    const id = Number(b.dataset.id);
    switch (act) {
      case "winkelmodus":
        this.winkel = !this.winkel;
        bewaarWinkelmodus(this.winkel);
        this._render();
        this.shadowRoot.querySelector(".main").scrollTop = 0;
        return;
      case "afrekenen":
        this.afrekenen = true;
        this._url((p) => {
          p.set("tab", this.tab);
          p.set("afronden", "1");
        });
        this._render();
        this.shadowRoot.querySelector(".main").scrollTop = 0;
        return;
      case "afrterug":
        this.afrekenen = false;
        this._url((p) => p.delete("afronden"));
        return this._render();
      case "klaar":
        return this._klaar();
      case "lijstopen":
        this.lijstOpen = !this.lijstOpen;
        return this._render();
      case "toevoegen":
        return this._openToevoegen();
      case "mkies":
        return this._modalToevoegen(Number(b.dataset.pid), Number(b.dataset.amount) || 1);
      case "mtekst":
        return this._modalToevoegen();
      case "mnieuw": {
        const inp = this.shadowRoot.querySelector(".m-zoek");
        const m = (inp?.value || "").match(/^\s*(\d+(?:[.,]\d+)?)/);
        this._sluitModal();
        return this._openNieuw(b.dataset.naam, null, m ? Number(m[1].replace(",", ".")) : 1);
      }
      case "bonpopup":
        return this._openBonPopup(b.dataset.id);
      case "bonboeksel":
        return this._bonPopupBoek(false);
      case "bonniets":
        return this._bonPopupBoek(true);
      case "bonboek": {
        const n = await this._do({ type: "boodschappen/bon_boek", bon_id: b.dataset.bon, line_ids: [b.dataset.line] });
        if (n) this._toon("In de voorraad geboekt");
        return;
      }
      case "nieuwprod": {
        const inp = this.shadowRoot.getElementById("add");
        const m = (inp?.value || "").match(/^\s*(\d+(?:[.,]\d+)?)/);
        if (inp) inp.value = "";
        this._suggest("");
        return this._openNieuw(b.dataset.naam, null, m ? Number(m[1].replace(",", ".")) : 1);
      }
      case "maakprod":
        return this._openNieuw(b.dataset.naam, id, 1);
      case "info":
        return this._openDetails(Number(b.dataset.pid));
      case "notitie": {
        const it = this.data.items.find((x) => x.id === id);
        if (!it) return;
        this.modal = { soort: "notitie" };
        return this._toonModal(`<h2>${esc(it.name)}</h2><p class="notitietekst">${esc(it.note || "")}</p>
          <div class="knoppen">${it.product_id ? `<button class="sec-btn" data-act="info" data-pid="${it.product_id}">Product aanpassen</button>` : ""}
          <span class="vul"></span><button class="primary" data-act="modsluit">Sluiten</button></div>`);
      }
      case "modsluit":
        return this._sluitModal();
      case "nieuwmaak":
        return this._nieuwMaak();
      case "detsave":
        return this._detailsOpslaan();
      case "detop":
        return this._detailsOp();
      case "add":
        return this._add();
      case "sugg":
        return this._add(Number(b.dataset.pid));
      case "vink": {
        const li = b.closest(".item");
        li?.classList.add("bezig");
        return this._do({ type: "boodschappen/item_done", item_id: id, done: b.dataset.done === "1" });
      }
      case "plus":
        return this._do({ type: "boodschappen/item_amount", item_id: id, amount: Number(b.dataset.amount) + 1 });
      case "min":
        return this._do({ type: "boodschappen/item_amount", item_id: id, amount: Number(b.dataset.amount) - 1 });
      case "dealadd":
        return this._do({ type: "boodschappen/item_add", list_id: Number(this.tab), product_id: Number(b.dataset.pid), amount: Number(b.dataset.amount) || 1 });
      case "dealnee":
        return this._do({ type: "boodschappen/aanbieding_negeer", key: b.dataset.key });
      case "favadd":
        return this._do({ type: "boodschappen/item_add", list_id: Number(this.tab), product_id: Number(b.dataset.pid), amount: Number(b.dataset.amount) || 1 });
      case "favedit":
        this.favEdit = !this.favEdit;
        return this._render();
      case "favpin":
        return this._do({ type: "boodschappen/favoriet", product_id: Number(b.dataset.pid), actie: b.dataset.pinned === "1" ? "reset" : "vast" });
      case "favhide":
        return this._do({ type: "boodschappen/favoriet", product_id: Number(b.dataset.pid), actie: "verberg" });
      case "deals":
        this.dealsOpen = !this.dealsOpen;
        return this._render();
      case "dealsalles":
        this.dealsAlles = !this.dealsAlles;
        return this._render();
      case "wis":
        return this._do({ type: "boodschappen/clear_done", list_id: Number(b.dataset.list) });
      case "weekvast":
        return this._do({ type: "boodschappen/weekvast" });
      case "tekorten":
        return this._do({ type: "boodschappen/tekorten" });
      case "bon":
        this.open[b.dataset.id] = !this.open[b.dataset.id];
        return this._render();
      case "herkoppel": {
        const same = this.edit && this.edit.bon_id === b.dataset.bon && this.edit.line_id === b.dataset.line;
        this.edit = same ? null : { bon_id: b.dataset.bon, line_id: b.dataset.line };
        return this._render();
      }
      case "kies":
      case "negeer":
      case "nieuw":
      case "maak":
        return this._kiezerActie(act, b);
    }
  }

  async _kiezerActie(act, b) {
    const k = b.closest(".kiezer");
    const factor = Number(String(k.querySelector(".factor").value).replace(",", ".")) || 1;
    const mode = k.dataset.mode;
    if (act === "nieuw") {
      const f = k.querySelector(".nieuwform");
      f.hidden = !f.hidden;
      if (!f.hidden) f.querySelector(".nnaam").focus();
      return;
    }
    if (act === "kies") {
      const pid = Number(b.dataset.pid);
      if (mode === "queue") await this._do({ type: "boodschappen/bon_koppel", queue_id: k.dataset.qid, product_id: pid, factor });
      else {
        this.edit = null;
        await this._do({ type: "boodschappen/bon_herkoppel", bon_id: k.dataset.bon, line_id: k.dataset.line, product_id: pid, factor,
          totaal: this._totaal(k) });
      }
      return;
    }
    if (act === "negeer") {
      if (mode === "queue") await this._do({ type: "boodschappen/bon_negeer", queue_id: k.dataset.qid });
      else {
        this.edit = null;
        await this._do({ type: "boodschappen/bon_herkoppel", bon_id: k.dataset.bon, line_id: k.dataset.line, negeren: true });
      }
      return;
    }
    if (act === "maak") {
      const naam = k.querySelector(".nnaam").value.trim();
      if (!naam) return k.querySelector(".nnaam").focus();
      if (mode === "herkoppel") {
        this.edit = null;
        return this._do({ type: "boodschappen/bon_herkoppel", bon_id: k.dataset.bon, line_id: k.dataset.line, nieuw_naam: naam,
          group_id: Number(k.querySelector(".ngroep").value) || null, factor, totaal: this._totaal(k) });
      }
      await this._do({
        type: "boodschappen/bon_nieuw_product",
        queue_id: k.dataset.qid,
        naam,
        group_id: Number(k.querySelector(".ngroep").value) || null,
        factor,
      });
    }
  }
}

const CSS = `
:host {
  display: flex; flex-direction: column; height: 100%; position: relative;
  background: var(--primary-background-color); color: var(--primary-text-color);
  font-family: var(--paper-font-body1_-_font-family, Roboto, system-ui, sans-serif);
  --route: var(--primary-color);
  --lijn: color-mix(in srgb, var(--route) 70%, transparent);
  --zacht: color-mix(in srgb, var(--primary-text-color) 8%, transparent);
  --rand: var(--divider-color, rgba(127,127,127,.25));
}
* { box-sizing: border-box; }
button { font: inherit; color: inherit; background: none; border: 0; cursor: pointer; -webkit-tap-highlight-color: transparent; }
button:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
header { display: flex; align-items: center; gap: 4px; height: var(--header-height, 56px); padding: 0 12px 0 4px;
  background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff); flex: none; }
header h1 { font-size: 20px; font-weight: 400; margin: 0 0 0 8px; white-space: nowrap; }
header .vul { flex: 1; }
.wm { display: flex; align-items: center; gap: 6px; height: 36px; padding: 0 12px; border-radius: 18px; font-size: 14px;
  border: 1px solid color-mix(in srgb, currentColor 40%, transparent); }
.wm.aan { background: var(--app-header-text-color, #fff); color: var(--app-header-background-color, var(--primary-color)); border-color: transparent; font-weight: 600; }
.wm[hidden] { display: none; }
@media (max-width: 380px) { .wm-tekst { display: none; } }
.bar.weg { display: none; }

/* Winkelmodus: zo veel mogelijk ruimte voor de lijst (split screen) */
:host(.winkel) header { height: 44px; }
:host(.winkel) header h1 { font-size: 16px; }
:host(.winkel) .tabs { padding-top: 0; }
:host(.winkel) .tabs button { padding: 8px 12px 9px; }
:host(.winkel) .main { padding-top: 2px; padding-bottom: 16px; }
:host(.winkel) .item { min-height: 46px; margin-bottom: 4px; }
:host(.winkel) .vink { min-height: 46px; padding-top: 6px; padding-bottom: 6px; }
:host(.winkel) .naam { font-size: 16px; }
:host(.winkel) .stop h2 { padding: 4px 0 2px; }
:host(.winkel) .stepper button { height: 38px; }
:host(.winkel) button.info { height: 38px; width: 34px; margin-right: 2px; }
:host(.winkel) .info.leeg { width: 34px; margin-right: 2px; }
:host(.winkel) .stepper button { width: 34px; }
:host(.winkel) .vink { gap: 10px; padding-left: 10px; }
:host(.winkel) .naam { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.txt { overflow: hidden; }
.naam { overflow-wrap: anywhere; }

/* Onderbalk: mandje en afronden */
.onder { flex: none; position: relative; background: var(--card-background-color); border-top: 1px solid var(--rand);
  padding: 8px 12px calc(8px + env(safe-area-inset-bottom, 0px)); }
.onder:empty { display: none; }
.onder.busy::after { content: ""; position: absolute; left: 0; top: -1px; height: 2px; width: 30%; background: var(--route); animation: laden 1s ease-in-out infinite; }
.onder-rij { display: flex; align-items: center; gap: 8px; max-width: 640px; margin: 0 auto; }
.mandje-n { flex: 1; text-align: left; font-size: 15px; padding: 8px 2px; }
.mandje-n b { display: inline-block; min-width: 24px; padding: 2px 7px; margin-right: 4px; border-radius: 12px; background: var(--route); color: #fff; text-align: center; }
.mandje-n.leeg { color: var(--secondary-text-color); }
.primary.afronden { background: var(--route); padding: 0 22px; }
.primary.afronden.breed { flex: 1; }
.primary:disabled { opacity: .45; cursor: default; }
.fab { flex: none; width: 48px; height: 48px; border-radius: 50%; background: var(--route); color: #fff; font-size: 30px; line-height: 1;
  box-shadow: 0 2px 6px rgba(0,0,0,.3); }
.toast { position: absolute; left: 50%; bottom: 76px; transform: translateX(-50%); z-index: 5; max-width: calc(100% - 32px);
  background: var(--primary-text-color); color: var(--primary-background-color); padding: 10px 16px; border-radius: 10px; font-size: 14px;
  box-shadow: 0 2px 10px rgba(0,0,0,.3); }
.toast[hidden] { display: none; }

/* Afrekenscherm */
.afr-titel { margin-top: 10px; }
.afr { list-style: none; margin: 0 0 8px; padding: 0; }
.afr-item { padding-left: 14px; }
.afr-item .txt { flex: 1; padding: 8px 0; }
.geen-vrd { color: var(--warning-color, #b26a00) !important; }
.link.inline { margin: 0; padding: 0 0 0 4px; font-size: 13px; }
.terug { flex: none; width: 40px; height: 44px; margin-right: 4px; border-radius: 10px; font-size: 20px; color: var(--secondary-text-color); }
.terug:active { background: var(--zacht); }
.stepper button:disabled { opacity: .3; cursor: default; }
.vergeten { margin: 4px 0 8px; border-style: dashed; color: var(--route); }
.chips-wrap { display: flex; flex-wrap: wrap; gap: 6px; }
.kieslijst { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.kieslijst label { display: flex; align-items: flex-start; gap: 12px; padding: 10px 12px; border-radius: 12px; background: var(--zacht); cursor: pointer; }
.kieslijst input { width: 22px; height: 22px; margin: 1px 0 0; accent-color: var(--primary-color); flex: none; }
.kieslijst code { font-size: 12px; }
.bonlos { margin: 10px 0 0; border-color: var(--warning-color, #f0a020); }
.sec-btn.klein { padding: 6px 12px; font-size: 13px; }
.tabs { display: flex; gap: 4px; padding: 8px 12px 0; background: var(--card-background-color); border-bottom: 1px solid var(--rand); overflow-x: auto; flex: none; }
.tabs button { position: relative; padding: 12px 14px 13px; display: flex; align-items: center; gap: 8px; color: var(--secondary-text-color); font-weight: 500; white-space: nowrap; }
.tabs button.on { color: var(--primary-text-color); }
.tabs button.on::after { content: ""; position: absolute; left: 10px; right: 10px; bottom: -1px; height: 3px; border-radius: 3px 3px 0 0; background: var(--route, var(--primary-color)); }
.tabs b { font-size: 12px; min-width: 20px; height: 20px; line-height: 20px; padding: 0 6px; border-radius: 10px; background: var(--zacht); font-weight: 600; }
.tabs b:empty { display: none; }
.tabs b.warn { background: var(--warning-color, #f0a020); color: #000; }
.bar { position: relative; flex: none; background: var(--card-background-color); padding: 10px 12px; border-bottom: 1px solid var(--rand); }
.bar:empty { display: none; }
.bar.busy::after { content: ""; position: absolute; left: 0; bottom: -1px; height: 2px; width: 30%; background: var(--route); animation: laden 1s ease-in-out infinite; }
@keyframes laden { from { left: -30%; } to { left: 100%; } }
@media (prefers-reduced-motion: reduce) { .bar.busy::after { animation: none; width: 100%; opacity: .5; } }
.add { display: flex; gap: 8px; }
.add input { flex: 1; min-width: 0; height: 48px; padding: 0 14px; font-size: 17px; border-radius: 12px; border: 1px solid var(--rand);
  background: var(--primary-background-color); color: var(--primary-text-color); }
.addbtn { width: 48px; height: 48px; border-radius: 12px; background: var(--route); color: #fff; font-size: 28px; line-height: 1; }
.sugg { display: flex; flex-wrap: wrap; gap: 6px; }
.sugg:not(:empty) { margin-top: 8px; }
.sugg button { padding: 8px 12px; border-radius: 18px; border: 1px solid var(--rand); background: var(--primary-background-color); font-size: 15px; }
.fav:not(:empty) { margin-top: 10px; }
.favkop { display: flex; align-items: baseline; justify-content: space-between; font-size: 13px; color: var(--secondary-text-color); margin: 0 2px 6px; }
.favkop .link { margin: 0; padding: 4px 2px; }
.chips-rij { display: flex; gap: 6px; overflow-x: auto; padding-bottom: 2px; scrollbar-width: none; -webkit-overflow-scrolling: touch; }
.chips-rij::-webkit-scrollbar { display: none; }
.favchip { flex: none; display: inline-flex; align-items: center; gap: 6px; padding: 9px 13px; border-radius: 20px; font-size: 15px;
  background: color-mix(in srgb, var(--route) 12%, var(--card-background-color)); border: 1px solid color-mix(in srgb, var(--route) 35%, transparent); white-space: nowrap; }
.favchip b { font-weight: 600; font-size: 13px; color: var(--secondary-text-color); }
.favchip.pin { border-color: var(--route); border-width: 2px; padding: 8px 12px; }
.favchip.edit { padding: 6px 6px 6px 12px; }
.favchip.edit button { font-size: 12px; padding: 5px 8px; border-radius: 14px; background: var(--zacht); }
.main { flex: 1; overflow-y: auto; padding: 8px 12px 32px; -webkit-overflow-scrolling: touch; }
.fout { background: color-mix(in srgb, var(--error-color, #c00) 15%, transparent); color: var(--primary-text-color); padding: 10px 12px; border-radius: 10px; }
.leeg, .rust { color: var(--secondary-text-color); padding: 12px 4px 0; }
.leeg p { margin: 0 0 6px; }

/* De looproute: één doorlopende lijn met een halte per vak */
.route { list-style: none; margin: 4px 0 0; padding: 0; }
.stop { position: relative; padding-left: 30px; }
.stop::before { content: ""; position: absolute; left: 10px; top: 0; bottom: 0; width: 4px; background: var(--lijn); }
.stop:first-child::before { top: 16px; }
.stop.eind::before { bottom: auto; height: 16px; }
.stop h2 { position: relative; display: flex; align-items: baseline; gap: 8px; margin: 0; padding: 8px 0 4px; font-size: 13px; font-weight: 600;
  letter-spacing: .02em; color: var(--secondary-text-color); }
.stop h2::before { content: ""; position: absolute; left: -26px; top: 9px; width: 12px; height: 12px; border-radius: 50%;
  background: var(--card-background-color); border: 4px solid var(--route); }
.stop.eind h2::before { background: var(--route); }
.stop h2 small { font-weight: 400; }
.stop ul, .mandje ul { list-style: none; margin: 0 0 8px; padding: 0; }
.item { display: flex; align-items: center; gap: 6px; background: var(--card-background-color); border-radius: 12px; margin: 0 0 6px;
  box-shadow: 0 0 0 1px var(--rand) inset; min-height: 56px; transition: opacity .2s; }
.item.bezig { opacity: .45; }
.vink { flex: 1; min-width: 0; display: flex; align-items: center; gap: 12px; padding: 10px 4px 10px 12px; text-align: left; min-height: 56px; }
.box { flex: none; width: 26px; height: 26px; border-radius: 8px; border: 2px solid var(--secondary-text-color); display: grid; place-items: center; }
.done .box { background: var(--route); border-color: var(--route); }
.done .box::after { content: ""; width: 7px; height: 13px; border: solid #fff; border-width: 0 3px 3px 0; transform: translateY(-2px) rotate(45deg); }
.txt { display: flex; flex-direction: column; min-width: 0; }
.naam { font-size: 17px; line-height: 1.25; }
.txt small { font-size: 13px; color: var(--secondary-text-color); line-height: 1.3; margin-top: 2px; overflow-wrap: anywhere; }
.done .naam { text-decoration: line-through; color: var(--secondary-text-color); }
.stepper { flex: none; display: flex; align-items: center; padding-right: 4px; }
.stepper button { width: 40px; height: 44px; font-size: 22px; color: var(--secondary-text-color); border-radius: 10px; }
.stepper button:active { background: var(--zacht); }
.n { min-width: 26px; text-align: center; font-size: 17px; font-variant-numeric: tabular-nums; font-weight: 600; padding-right: 8px; }
.stepper .n { padding: 0; }
.acties { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0 8px; }
.acties button, .wis, .sec-btn { padding: 10px 14px; border-radius: 22px; border: 1px solid var(--rand); background: var(--card-background-color); font-size: 14px; }
.mandje { margin-top: 18px; border-top: 1px solid var(--rand); padding-top: 4px; }
.mandje .kop { width: 100%; text-align: left; padding: 12px 4px; font-weight: 500; display: flex; align-items: center; gap: 8px; }
.mandje .kop::after { content: "▾"; margin-left: auto; transition: transform .2s; }
.mandje.open .kop::after { transform: rotate(180deg); }
.mandje .kop b { font-size: 12px; padding: 2px 8px; border-radius: 10px; background: var(--zacht); }
.mandje .item { box-shadow: none; background: transparent; min-height: 48px; }
.wis { color: var(--error-color, #c62828); border-color: color-mix(in srgb, var(--error-color, #c62828) 40%, transparent); }

/* Productvensters */
button.info { flex: none; width: 36px; height: 44px; margin-right: 4px; border-radius: 10px; font-size: 18px; color: var(--secondary-text-color); }
button.info.nieuw { font-size: 22px; color: var(--route); font-weight: 600; }
button.info:active { background: var(--zacht); }
.sugg .nieuwprod { border-style: dashed; color: var(--route); font-weight: 500; }
.modal { position: fixed; inset: 0; z-index: 10; background: rgba(0,0,0,.45); display: flex; align-items: flex-start; justify-content: center;
  padding: max(12px, env(safe-area-inset-top, 0px)) 10px 10px; }
.modal[hidden] { display: none; }
.venster { width: 100%; max-width: 520px; max-height: calc(100% - 8px); overflow-y: auto; background: var(--card-background-color);
  border-radius: 18px; padding: 16px 16px 18px; display: grid; gap: 10px; box-shadow: 0 6px 24px rgba(0,0,0,.35); }
@media (min-width: 700px) { .modal { padding-top: 10vh; } }
.notitietekst { margin: 0; font-size: 16px; line-height: 1.4; overflow-wrap: anywhere; }
button.info svg { display: block; margin: 0 auto; }
.info.leeg { display: inline-block; flex: none; width: 36px; margin-right: 4px; }
.venster h2 { margin: 0 0 2px; font-size: 19px; font-weight: 500; }
.venster .rij3 { display: flex; gap: 8px; flex-wrap: wrap; }
.venster .rij3 .veld { flex: 1; min-width: 90px; }
.venster .rij3 .veld input { width: 100%; }
.venster .knoppen { margin-top: 4px; align-items: center; }
.venster .vul { flex: 1; }
.venster .primary { padding: 0 20px; }
.spin { display: inline-block; width: 14px; height: 14px; vertical-align: -2px; margin-right: 6px; border-radius: 50%;
  border: 2px solid color-mix(in srgb, var(--route) 25%, transparent); border-top-color: var(--route); animation: draai .8s linear infinite; }
@keyframes draai { to { transform: rotate(360deg); } }

/* Aanbiedingen */
.deals { margin-top: 14px; border-top: 1px solid var(--rand); padding-top: 4px; }
.deals .kop { width: 100%; text-align: left; padding: 12px 4px; font-weight: 500; display: flex; align-items: center; gap: 8px; }
.deals .kop::after { content: "▾"; margin-left: auto; transition: transform .2s; }
.deals.open .kop::after { transform: rotate(180deg); }
.deals .kop b { font-size: 12px; padding: 2px 8px; border-radius: 10px; background: var(--route); color: #fff; }
.deals .kop .zacht { font-size: 13px; color: var(--secondary-text-color); font-weight: 400; }
.deals ul { list-style: none; margin: 0; padding: 0; }
.deal { display: flex; align-items: center; gap: 8px; padding: 8px 4px; border-bottom: 1px solid var(--rand); }
.deal-txt { flex: 1; min-width: 0; }
.deal-txt small { display: block; color: var(--secondary-text-color); font-size: 12px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.deal-actie { color: var(--route); font-weight: 600; font-size: 14px; }
.deal-knoppen { display: flex; gap: 6px; align-items: center; flex-shrink: 0; }
.deal-knoppen .klein { height: auto; min-height: 34px; padding: 4px 10px; }
.deal-knoppen .primary { background: var(--route); color: #fff; border: 0; font-size: 14px; }
.deal-knoppen .x { font-size: 20px; padding: 4px 8px; color: var(--secondary-text-color); }
.deal.mat { opacity: .55; }
.deal.mat .deal-actie { color: var(--secondary-text-color); }
.deals .meer { padding: 10px 4px; font-size: 13px; }
.tag-actie { font-size: 11px; font-weight: 600; padding: 1px 6px; border-radius: 8px; background: var(--route); color: #fff; vertical-align: 2px; }
.op-lijst { font-size: 13px; color: var(--success-color, #2e7d32); font-weight: 500; }

/* Bonnetjes */
.sec { font-size: 15px; font-weight: 600; margin: 18px 4px 8px; }
.uitleg { margin: -4px 4px 10px; color: var(--secondary-text-color); font-size: 14px; }
.qcard, .bon { background: var(--card-background-color); border-radius: 14px; box-shadow: 0 0 0 1px var(--rand) inset; padding: 12px; margin-bottom: 10px; }
.qcard { border-left: 4px solid var(--warning-color, #f0a020); }
.rij { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; }
.bontekst { font-family: "Courier New", ui-monospace, monospace; font-size: 15px; font-weight: 700; letter-spacing: .03em; text-transform: uppercase; overflow-wrap: anywhere; }
.bedrag { color: var(--secondary-text-color); font-variant-numeric: tabular-nums; white-space: nowrap; font-size: 14px; }
.meta { margin: 4px 0 8px; color: var(--secondary-text-color); font-size: 13px; }
.kiezer { display: grid; gap: 8px; margin-top: 6px; }
.veld { display: grid; gap: 4px; font-size: 13px; color: var(--secondary-text-color); }
.veld input, .veld select { height: 44px; padding: 0 12px; border-radius: 10px; border: 1px solid var(--rand); font-size: 16px;
  background: var(--primary-background-color); color: var(--primary-text-color); }
.veld.klein input { width: 120px; }
.kandidaten { display: flex; flex-wrap: wrap; gap: 6px; }
.kand { padding: 9px 13px; border-radius: 20px; background: color-mix(in srgb, var(--primary-color) 14%, transparent); font-size: 15px; font-weight: 500; }
.hint { margin: -4px 0 0; font-size: 12px; color: var(--secondary-text-color); }
.knoppen { display: flex; gap: 8px; flex-wrap: wrap; }
.nieuwform { display: grid; gap: 8px; padding: 10px; border-radius: 10px; background: var(--zacht); }
.primary { height: 44px; border-radius: 22px; background: var(--primary-color); color: var(--text-primary-color, #fff); font-weight: 500; }
.bonkop { width: 100%; display: grid; grid-template-columns: auto 1fr auto; gap: 2px 10px; text-align: left; align-items: baseline; }
.bonkop .w { font-weight: 600; }
.bonkop .d { color: var(--secondary-text-color); font-size: 14px; }
.bonkop .t { font-variant-numeric: tabular-nums; font-weight: 600; }
.bonkop .chips { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.chip { display: inline-block; font-size: 12px; line-height: 1.5; padding: 2px 8px; border-radius: 10px; background: var(--zacht); white-space: nowrap; }
.chip.ok { background: color-mix(in srgb, var(--success-color, #2e7d32) 18%, transparent); }
.chip.info { background: color-mix(in srgb, var(--info-color, #1e88e5) 18%, transparent); }
.chip.warn { background: color-mix(in srgb, var(--warning-color, #f0a020) 28%, transparent); }
.chip.err { background: color-mix(in srgb, var(--error-color, #c62828) 22%, transparent); }
.regels { list-style: none; margin: 10px 0 0; padding: 0; border-top: 1px dashed var(--rand); }
.regel { padding: 10px 0; border-bottom: 1px dashed var(--rand); }
.regel:last-child { border-bottom: 0; }
.rij2 { display: flex; align-items: center; gap: 8px; margin-top: 4px; flex-wrap: wrap; font-size: 14px; }
.doel em { font-style: normal; color: var(--secondary-text-color); font-size: 13px; }
.link { margin-left: auto; color: var(--primary-color); font-size: 14px; padding: 6px 2px; }
@media (min-width: 700px) {
  .main { padding: 12px 24px 32px; }
  .main > *, .bar > * { max-width: 640px; margin-left: auto; margin-right: auto; }
}
`;

if (!customElements.get("boodschappen-panel")) customElements.define("boodschappen-panel", BoodschappenPanel);
