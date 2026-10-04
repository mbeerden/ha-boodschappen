// Boodschappen-paneel voor Home Assistant: lijst per winkel op looproute + bonnetjes koppelen.
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

const STATUS = {
  prijs: ["Prijs bijgewerkt", "ok"],
  aangevuld: ["Prijs + extra geboekt", "ok"],
  ongepland: ["Ongepland gekocht", "info"],
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
    this.mandjeOpen = false;
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
    this.tab = params.get("tab") === "bonnen" ? "bonnen" : null;
    await this._load();
  }

  _poll() {
    const a = this.shadowRoot.activeElement;
    if (this.busy || (a && (a.tagName === "INPUT" || a.tagName === "SELECT"))) return;
    this._load();
  }

  async _load() {
    try {
      this.data = await this._hass.callWS({ type: "boodschappen/data" });
      this.err = "";
    } catch (e) {
      this.err = e.message || "Kan de boodschappen niet laden.";
    }
    if (!this.tab && this.data?.lists?.length) this.tab = String(this.data.lists[0].id);
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
  }

  // ------------------------------------------------------------------ opbouw
  _shell() {
    this.shadowRoot.innerHTML = `
      <style>${CSS}</style>
      <header>
        <div class="menu"></div>
        <h1>Boodschappen</h1>
      </header>
      <nav class="tabs" role="tablist"></nav>
      <div class="bar"></div>
      <div class="main"></div>`;
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
    if (this.tab === "bonnen") {
      bar.innerHTML = "";
      bar.className = "bar";
      main.innerHTML = this._bonnen();
    } else {
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
    }
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
      html += `<div class="leeg"><p>Niets meer te halen hier.</p><p>Voeg hierboven iets toe, of vul de lijst aan:</p></div>`;
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
    html += this._aanbiedingen(lid);
    html += `<div class="acties">
        <button data-act="weekvast">Vaste verse boodschappen toevoegen</button>
        <button data-act="tekorten">Tekorten aanvullen</button>
      </div>`;
    if (klaar.length) {
      html += `<section class="mandje ${this.mandjeOpen ? "open" : ""}">
        <button class="kop" data-act="mandje" aria-expanded="${this.mandjeOpen}">In je mandje <b>${klaar.length}</b></button>
        ${
          this.mandjeOpen
            ? `<ul>${klaar.map((i) => this._item(i)).join("")}</ul>
               <button class="wis" data-act="wis" data-list="${lid}">Afgevinkte verwijderen</button>`
            : ""
        }
      </section>`;
    }
    return html;
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
    const note = i.note ? `<small>${esc(i.note)}</small>` : "";
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
      }
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
        ${r.status !== "te koppelen" ? `<button class="link" data-act="herkoppel" data-bon="${b.id}" data-line="${r.id}">${editing ? "Annuleren" : "Wijzigen"}</button>` : ""}
      </div>
      ${editing ? this._kiezer({ key: `e:${b.id}:${r.id}`, suggestie: r.grocy_naam || r.tekst, factor: r.factor || 1, mode: "herkoppel", bon: b.id, line: r.id }) : ""}
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
      <div class="knoppen">
        ${o.mode === "queue" ? `<button class="sec-btn" data-act="nieuw">Nieuw product…</button>` : ""}
        <button class="sec-btn" data-act="negeer">Negeren</button>
      </div>
      <div class="nieuwform" hidden>
        <label class="veld">Naam <input class="nnaam" type="text" value="${esc(o.suggestie || "")}"></label>
        <label class="veld">Vak <select class="ngroep">${groups}</select></label>
        <button class="primary" data-act="maak">Product aanmaken en koppelen</button>
      </div>
    </div>`;
  }

  // ---------------------------------------------------------------- events
  _suggest(val) {
    const box = this.shadowRoot.querySelector(".sugg");
    if (!box) return;
    const q = val.replace(/^\s*\d+([.,]\d+)?\s*x?\s*/i, "");
    const res = q.length >= 2 ? zoek(this.data.products, q, 5) : [];
    box.innerHTML = res
      .map((p) => `<button role="option" data-act="sugg" data-pid="${p.id}">${esc(p.name)}</button>`)
      .join("");
  }

  _input(e) {
    const t = e.target;
    if (t.id === "add") this._suggest(t.value);
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

  async _click(e) {
    const b = e.target.closest("button");
    if (!b) return;
    const act = b.dataset.act;
    if (b.dataset.tab) {
      this.tab = b.dataset.tab;
      this.edit = null;
      const url = new URL(location.href);
      if (this.tab === "bonnen") url.searchParams.set("tab", "bonnen");
      else url.searchParams.delete("tab");
      history.replaceState(null, "", url);
      this._render();
      this.shadowRoot.querySelector(".main").scrollTop = 0;
      return;
    }
    const id = Number(b.dataset.id);
    switch (act) {
      case "add":
        return this._add();
      case "sugg":
        return this._add(Number(b.dataset.pid));
      case "vink": {
        const li = b.closest(".item");
        li.classList.add("bezig");
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
      case "mandje":
        this.mandjeOpen = !this.mandjeOpen;
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
        await this._do({ type: "boodschappen/bon_herkoppel", bon_id: k.dataset.bon, line_id: k.dataset.line, product_id: pid, factor });
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
  display: flex; flex-direction: column; height: 100%;
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
header h1 { font-size: 20px; font-weight: 400; margin: 0 0 0 8px; }
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
.main { flex: 1; overflow-y: auto; padding: 8px 12px 96px; -webkit-overflow-scrolling: touch; }
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
.chip { font-size: 12px; padding: 3px 8px; border-radius: 10px; background: var(--zacht); white-space: nowrap; }
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
  .main { padding: 12px 24px 96px; }
  .main > *, .bar > * { max-width: 640px; margin-left: auto; margin-right: auto; }
}
`;

if (!customElements.get("boodschappen-panel")) customElements.define("boodschappen-panel", BoodschappenPanel);
