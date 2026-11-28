// Eten-paneel voor Home Assistant: weekmenu (met AI-voorstel), recepten, koken en beoordelen per persoon.
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtNum = (n) => {
  const v = Number(n);
  return Number.isInteger(v) ? String(v) : v.toLocaleString("nl-NL", { maximumFractionDigits: 1 });
};
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const vanIso = (s) => new Date(String(s).slice(0, 10) + "T12:00:00");
const plusDagen = (d, n) => {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
};
const maandag = (d) => plusDagen(d, -((d.getDay() + 6) % 7));
const weekNr = (d) => {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const dag = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - dag);
  return Math.ceil(((t - new Date(Date.UTC(t.getUTCFullYear(), 0, 1))) / 86400000 + 1) / 7);
};
const dagNaam = (s, lang = "long") => vanIso(s).toLocaleDateString("nl-NL", { weekday: lang });
const datumKort = (s) => vanIso(s).toLocaleDateString("nl-NL", { day: "numeric", month: "short" });
const norm = (s) => String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
const GEZICHT = ["", "👎", "🙁", "😐", "🙂", "👍"];
const GEZICHT_TEKST = ["", "Niet lekker", "Niet lekker", "Gaat wel", "Lekker", "Wel lekker"];
// zelfde sleutel als menu.feedback_key in de integratie
const normPy = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();
const FB = [
  ["wel_lekker", "👍", "Wel lekker"],
  ["lekker_snel", "⚡", "Lekker snel"],
  ["niet_lekker", "👎", "Niet lekker"],
  ["teveel_werk", "🕒", "Teveel werk"],
];
const ROL = { favoriet: ["⭐", "Favoriet"], slim: ["💡", "Slim: actie & voorraad"], nieuw: ["✨", "Nieuw om te proberen"] };
const SOORT = [
  ["koken", "Wij koken"],
  ["vriezer", "Uit de vriezer"],
  ["anders", "Iemand anders kookt"],
  ["niet", "Niet thuis"],
];
const KORT = ["ma", "di", "wo", "do", "vr", "za", "zo"];
const SNEL_AANPASSEN = ["Vegetarisch maken", "Sneller klaar (max 30 min)", "Meer kruiden en smaak", "Koolhydraatarmer", "Voor 5 personen", "Zonder oven", "Pittiger", "Kindvriendelijker"];
const BRON_TYPE = { website: "Website", boek: "Kookboek", anders: "Anders" };
const BIB = "__bibliotheek__"; // chip 'Mijn bibliotheek' bij dag voor dag plannen (zelfde als dagmenu.BIBLIOTHEEK)
// kleine markdown voor de gedachten van Gemini: **vet**, alinea's
const mini = (t) =>
  esc(t)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .split(/\n{2,}/)
    .map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`)
    .join("");

class EtenPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.d = null;
    this.tab = "week";
    this.week = maandag(new Date());
    this.view = null; // {soort: 'recept'|'koken'|'bewerk'|'nieuw'|'instellingen', id}
    this.edit = null; // datum van de dag die bewerkt wordt (planning)
    this.zoek = "";
    this.filter = "";
    this.porties = null;
    this.stap = 0;
    this.busy = false;
    this.bezigTekst = "";
    this.err = "";
    this.melding = "";
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
  }
  disconnectedCallback() {
    clearInterval(this._timer);
    document.removeEventListener("visibilitychange", this._vis);
    this._wakeLos();
  }

  async _init() {
    this._shell();
    const p = new URLSearchParams(location.search);
    if (p.get("recept")) this.view = { soort: "recept", id: p.get("recept") };
    if (p.get("tab") === "recepten") this.tab = "recepten";
    await this._load();
  }

  _poll() {
    const a = this.shadowRoot.activeElement;
    if (this.busy || this.edit || this.aanpas || this.raanpas || (this.view && this.view.soort !== "recept") || (a && /INPUT|SELECT|TEXTAREA/.test(a.tagName))) return;
    this._load();
  }

  async _load() {
    try {
      this.d = await this._hass.callWS({ type: "boodschappen/eten" });
      this.err = "";
    } catch (e) {
      this.err = e.message || "Kan Eten niet laden.";
    }
    this._render();
  }

  async _do(msg, bezig = "") {
    if (this.busy) return null;
    this.busy = true;
    this.bezigTekst = bezig;
    this._render();
    let res = null;
    try {
      this.d = await this._hass.callWS(msg);
      res = this.d.resultaat ?? true;
      this.err = "";
    } catch (e) {
      this.err = e.message || "Dat lukte niet.";
    }
    this.busy = false;
    this.bezigTekst = "";
    this.bezigDag = null;
    this._render();
    return res;
  }

  _shell() {
    this.shadowRoot.innerHTML = `
      <style>${CSS}</style>
      <header>
        <div class="menu"></div>
        <h1>Eten</h1>
        <button class="hbtn" data-act="bronnen" aria-label="Bronnen" title="Bronnen van recepten">📚</button>
        <button class="hbtn" data-act="instellingen" aria-label="Instellingen" title="Instellingen">⚙</button>
      </header>
      <nav class="tabs" role="tablist"></nav>
      <div class="bar"></div>
      <div class="main"></div>
      <div class="status" role="status" aria-live="polite"></div>`;
    const menu = document.createElement("ha-menu-button");
    menu.hass = this._hass;
    menu.narrow = this._narrow;
    this.shadowRoot.querySelector(".menu").appendChild(menu);
    this.menuBtn = menu;
    const root = this.shadowRoot;
    root.addEventListener("click", (e) => this._click(e));
    root.addEventListener("input", (e) => this._input(e));
    root.addEventListener("change", (e) => this._change(e));
    root.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.id === "dag-wens") {
        e.preventDefault();
        root.querySelector("[data-act=dagvoorstel]")?.click();
      }
      if (e.key === "Enter" && e.target.id === "ont-vraag") {
        e.preventDefault();
        root.querySelector("[data-act=ontzoek]")?.click();
      }
    });
  }

  get recepten() {
    return this.d?.recepten || [];
  }
  recept(id) {
    return this.recepten.find((r) => r.id === id);
  }
  product(id) {
    return (this.d?.producten || []).find((p) => p.id === Number(id));
  }

  // ------------------------------------------------------------------ render
  _render() {
    const root = this.shadowRoot;
    const st = root.querySelector(".status");
    if (st) {
      st.classList.toggle("aan", this.busy);
      st.innerHTML = this.busy ? `<span class="spinner" aria-hidden="true"></span><span>${esc(this.bezigTekst || "Bezig…")}</span>` : "";
    }
    const main = root.querySelector(".main");
    const tabs = root.querySelector(".tabs");
    const bar = root.querySelector(".bar");
    bar.className = "bar" + (this.busy ? " busy" : "");
    if (!this.d) {
      main.innerHTML = this.err ? `<p class="fout">${esc(this.err)}</p>` : `<p class="leeg">Laden…</p>`;
      return;
    }
    const y = main.scrollTop;
    const v = this.view;
    if (v) {
      tabs.style.display = "none";
      bar.innerHTML = this.busy ? `<span class="bezig">${esc(this.bezigTekst || "Bezig…")}</span>` : "";
      if (v.soort === "recept") main.innerHTML = this._receptView(v.id);
      else if (v.soort === "koken") main.innerHTML = this._kokenView(v.id);
      else if (v.soort === "bewerk") main.innerHTML = this._bewerkView(v.id);
      else if (v.soort === "nieuw") main.innerHTML = this._nieuwView();
      else if (v.soort === "instellingen") main.innerHTML = this._instView();
      else if (v.soort === "plannen") main.innerHTML = this._plannenView();
      else if (v.soort === "voorstel") main.innerHTML = this._voorstelView();
      else if (v.soort === "dag") main.innerHTML = this._dagView();
      else if (v.soort === "benodigd") main.innerHTML = this._benodigdView();
      else if (v.soort === "bronnen") main.innerHTML = this._bronnenView();
      else if (v.soort === "ontdek") main.innerHTML = this._ontdekView();
      main.classList.toggle("koken", v.soort === "koken");
    } else {
      main.classList.remove("koken");
      tabs.style.display = "";
      tabs.innerHTML = `
        <button role="tab" data-tab="week" class="${this.tab === "week" ? "on" : ""}">Weekmenu</button>
        <button role="tab" data-tab="recepten" class="${this.tab === "recepten" ? "on" : ""}">Recepten <b>${this.recepten.length || ""}</b></button>`;
      if (this.tab === "week") {
        bar.innerHTML = this._weekBar();
        main.innerHTML = this._week();
      } else {
        bar.innerHTML = this._receptenBar();
        main.innerHTML = this._receptenLijst();
        const z = bar.querySelector("#zoek");
        if (z && this._zoekFocus) {
          z.focus();
          z.setSelectionRange(z.value.length, z.value.length);
        }
      }
    }
    if (this.melding) main.insertAdjacentHTML("afterbegin", `<p class="melding" role="status">${esc(this.melding)}</p>`);
    if (this.err) main.insertAdjacentHTML("afterbegin", `<p class="fout" role="alert">${esc(this.err)}</p>`);
    if (this.d.sync_fout && !v) main.insertAdjacentHTML("afterbegin", `<p class="fout">${esc(this.d.sync_fout)}</p>`);
    if (!this._scrollTop) main.scrollTop = y;
    else {
      main.scrollTop = 0;
      this._scrollTop = false;
    }
  }

  // ------------------------------------------------------------------ weekmenu
  _weekBar() {
    const w = this.week;
    const eind = plusDagen(w, 6);
    const v = this.d.voorstel;
    const bereik = `${w.getDate()} ${w.toLocaleDateString("nl-NL", { month: "short" })} – ${eind.getDate()} ${eind.toLocaleDateString("nl-NL", { month: "short" })}`;
    return `
      <div class="weeknav">
        <button class="rond" data-act="week" data-n="-1" aria-label="Vorige week">‹</button>
        <div class="weektitel"><b>Week ${weekNr(w)}</b><small>${bereik}</small></div>
        <button class="rond" data-act="week" data-n="1" aria-label="Volgende week">›</button>
      </div>
      ${
        this.busy
          ? `<p class="bezig">${esc(this.bezigTekst || "Bezig…")}</p>`
          : `<div class="voorstelbalk">${
              v
                ? `<button class="primary breed" data-act="naarvoorstel">✨ Voorstel bekijken (${esc(datumKort(v.van))} – ${esc(datumKort(v.tot))})</button>`
                : `<button class="primary breed" data-act="dagstart">✨ Dag voor dag plannen</button>`
            }</div>`
      }`;
  }

  _weekDagen() {
    return [...Array(7)].map((_, i) => iso(plusDagen(this.week, i)));
  }

  _week() {
    const d = this.d;
    const vandaag = iso(new Date());
    const dagen = this._weekDagen();
    const plan = Object.fromEntries(d.planning.map((p) => [p.datum, p]));
    const extern = {};
    for (const e of d.extern) (extern[e.datum] ||= []).push(e);
    let html = `<ol class="dagen">`;
    for (const ds of dagen) {
      const p = plan[ds];
      const ex = extern[ds] || [];
      const klasse = ds === vandaag ? "vandaag" : ds < vandaag ? "voorbij" : "";
      html += `<li class="dag ${klasse}">
        <div class="dagkop"><span class="dn">${esc(dagNaam(ds))}</span><span class="dd">${esc(datumKort(ds))}${ds === vandaag ? " · vandaag" : ""}</span></div>
        ${this._dagInhoud(ds, p, null, ex, vandaag)}
      </li>`;
    }
    html += `</ol>`;
    if (!this.recepten.length) {
      html += `<p class="leeg">Nog geen recepten. Voeg ze toe onder <button class="link" data-tab="recepten">Recepten</button>, dan kan het voorstel ze gebruiken.</p>`;
    }
    return html;
  }

  _dagInhoud(ds, p, vs, ex, vandaag) {
    if (this.busy && this.bezigDag === ds)
      return `<div class="laadblok" role="status"><span class="spinner" aria-hidden="true"></span><span>${esc(this.bezigTekst || "Bezig…")}</span></div>`;
    if (this.edit === ds) return this._planForm(ds, p, ex[0]);
    if (p && p.geen) {
      return `<div class="maaltijd geen">
          <div class="mtitel"><span class="t">${esc(p.titel || "Geen maaltijd")}</span><small>we koken niet</small></div>
          <div class="mknoppen"><button class="sec-btn" data-act="plan" data-datum="${ds}">Wijzig</button></div>
        </div>${ds >= vandaag ? this._suggestie(ds) || this._aanpasBlok(ds, p.id) : ""}`;
    }
    if (p) {
      const r = p.recept_id ? this.recept(p.recept_id) : null;
      const meta = [p.kookt ? `kookt: ${esc(p.kookt)}` : "", p.porties ? `${fmtNum(p.porties)} porties` : "",
        p.vriezer ? `${fmtNum(p.vriezer)} naar vriezer` : "", r?.tijd ? `${r.tijd} min` : "",
        p.boodschappen === false ? "geen boodschappen" : ""].filter(Boolean).join(" · ");
      const beoordeel = ds <= vandaag && (this.d.instellingen.personen || []).length ? this._beoordeling(p) : "";
      return `<div class="maaltijd">
          <button class="mtitel" ${r ? `data-act="open" data-id="${r.id}"` : `data-act="plan" data-datum="${ds}"`}>
            <span class="t">${esc(p.titel)}</span>${meta ? `<small>${meta}</small>` : ""}
            ${!p.recept_id ? `<small class="zacht">geen recept gekoppeld</small>` : ""}
          </button>
          <div class="mknoppen">
            ${r ? `<button class="sec-btn" data-act="koken" data-id="${r.id}">Koken</button>` : ""}
            <button class="sec-btn" data-act="plan" data-datum="${ds}">Wijzig</button>
          </div>
        </div>${ds >= vandaag ? this._suggestie(ds) || this._aanpasBlok(ds, p.id) : ""}${beoordeel}`;
    }
    let html = "";
    for (const e of ex) {
      html += `<div class="extern"><div><span class="t">${esc(e.titel)}</span><small>staat in de agenda</small></div>
        <div class="mknoppen">
          <button class="sec-btn" data-act="geenmaaltijd" data-datum="${ds}" data-uid="${esc(e.uid || "")}">Geen maaltijd</button>
          <button class="sec-btn" data-act="plan" data-datum="${ds}">Koppel recept</button>
        </div></div>`;
    }
    if (!ex.length)
      html += `<div class="leegrij"><button class="leegdag" data-act="plan" data-datum="${ds}">+ Plan een maaltijd</button>
        ${ds >= vandaag ? `<button class="link" data-act="dagstart" data-datum="${ds}">✨ Stel voor</button>` : ""}
        <button class="link" data-act="geenmaaltijd" data-datum="${ds}">Geen maaltijd</button></div>`;
    if (ds >= vandaag) html += this._suggestie(ds) || this._aanpasBlok(ds, null);
    return html;
  }

  _receptOpties(gekozen, leegTekst = "— eigen titel, zonder recept —", concept = null) {
    return `<option value="">${esc(leegTekst)}</option>` +
      (concept ? `<option value="${concept.id}" ${concept.id === gekozen ? "selected" : ""}>✨ ${esc(concept.naam)} (nieuw recept)</option>` : "") +
      this.recepten.map((r) => `<option value="${r.id}" ${r.id === gekozen ? "selected" : ""}>${esc(r.naam)}${r.vega ? " 🌱" : ""}</option>`).join("");
  }

  _aanpasBlok(ds, planId, vorigeWens = "") {
    if (this.aanpas !== ds)
      return `<button class="ai-btn" data-act="aanpasopen" data-datum="${ds}">✨ Aanpassen met Gemini</button>`;
    return `<div class="aanpas">
      <label class="veld"><span>Wat wil je op ${esc(dagNaam(ds))}?</span>
        <input class="aanpas-wens" type="text" enterkeyhint="go" value="${esc(vorigeWens || "")}"
          placeholder="Bijv. een ovenschotel, of een vegetarische goulash"></label>
      <div class="knoppen">
        <span class="vul"></span>
        <button class="sec-btn" data-act="aanpasdicht">Annuleren</button>
        <button class="primary" data-act="aanpasdoe" data-datum="${ds}" ${planId ? `data-plan="${planId}"` : ""}>Doe voorstel</button>
      </div>
    </div>`;
  }

  _conceptInfo(c) {
    return `<details class="concept"><summary>✨ Nieuw recept${c.tijd ? ` · ${c.tijd} min` : ""} · ${(c.ingredienten || []).length} ingrediënten${c.vega ? " · 🌱" : ""}</summary>
      <ul class="ingr">${(c.ingredienten || []).map((i) => {
        const p = i.product_id ? this.product(i.product_id) : null;
        return `<li><span>${esc(i.tekst)}</span>${i.basis ? `<small>in huis</small>` : p ? `<small>${esc(p.name)}</small>` : `<small class="warn">niet gekoppeld</small>`}</li>`;
      }).join("")}</ul>
      <ol class="stappen">${(c.stappen || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol>
      <p class="hint">Komt in je recepten zodra je dit goedkeurt.</p>
    </details>`;
  }

  _suggestie(ds) {
    const s = (this.d.suggesties || {})[ds];
    if (!s) return "";
    const meta = [s.kookt ? `kookt: ${esc(s.kookt)}` : "", s.porties ? `${fmtNum(s.porties)} porties` : "", s.vriezer ? `${fmtNum(s.vriezer)} naar vriezer` : ""].filter(Boolean).join(" · ");
    return `<div class="suggestie">
      <p class="sugkop">✨ Voorstel van Gemini${s.wens ? ` voor “${esc(s.wens)}”` : ""}</p>
      <p class="sugtitel">${esc(s.titel)}</p>
      ${meta ? `<small>${meta}</small>` : ""}
      ${s.reden ? `<p class="reden">${esc(s.reden)}</p>` : ""}
      ${s.concept ? this._conceptInfo(s.concept) : ""}
      <div class="knoppen">
        <button class="sec-btn" data-act="sugweg" data-datum="${ds}">Weggooien</button>
        <button class="sec-btn" data-act="aanpasopen" data-datum="${ds}" data-wens="${esc(s.wens || "")}">Ander voorstel</button>
        <span class="vul"></span>
        <button class="primary" data-act="sugtoepassen" data-datum="${ds}">Toepassen</button>
      </div>
    </div>`;
  }

  _planForm(ds, p, ex) {
    const titel = p?.geen ? "" : p?.titel || ex?.titel || "";
    return `<div class="vform" data-plan="${ds}">
      <label class="veld"><span>Gerecht</span>
        <select name="recept_id">${this._receptOpties(p?.recept_id)}</select></label>
      <label class="veld"><span>Titel (als er geen recept is)</span><input name="titel" value="${esc(titel)}"></label>
      <div class="rij3">
        <label class="veld"><span>Kookt</span><input name="kookt" value="${esc(p?.kookt || "")}"></label>
        <label class="veld smal"><span>Porties</span><input name="porties" type="number" min="1" inputmode="numeric" value="${esc(p?.porties ?? "")}"></label>
        <label class="veld smal"><span>Vriezer</span><input name="vriezer" type="number" min="0" inputmode="numeric" value="${esc(p?.vriezer || 0)}"></label>
      </div>
      <label class="check"><input type="checkbox" name="boodschappen" ${p?.boodschappen !== false ? "checked" : ""}> Ingrediënten op de boodschappenlijst</label>
      <label class="check"><input type="checkbox" name="geen" ${p?.geen ? "checked" : ""}> Geen maaltijd (we koken niet)</label>
      <div class="knoppen">
        ${p ? `<button class="wis" data-act="planweg" data-id="${p.id}">Verwijderen</button>` : ""}
        <span class="vul"></span>
        <button class="sec-btn" data-act="planannuleer">Annuleren</button>
        <button class="primary" data-act="planopslaan" data-datum="${ds}" ${p ? `data-id="${p.id}"` : ""} ${ex && !p ? `data-uid="${esc(ex.uid || "")}"` : ""}>Opslaan</button>
      </div>
    </div>`;
  }

  _beoordeling(p) {
    const alle = this.d.instellingen.personen || [];
    const personen = (p.aanwezig || []).length ? p.aanwezig.filter((x) => alle.includes(x)) : alle;
    const scores = {};
    for (const b of this.d.beoordelingen) if (b.plan_id === p.id) scores[b.persoon] = b.score;
    const n = Object.keys(scores).length;
    const open = this._beoordeelOpen === p.id || (this._beoordeelOpen !== "__dicht__" && n === 0 && p.datum === iso(new Date()));
    if (!open) {
      const samenvat = n
        ? personen.filter((x) => scores[x]).map((x) => `<span title="${esc(x)}: ${GEZICHT_TEKST[scores[x]]}">${esc(x.slice(0, 1))}${GEZICHT[scores[x]]}</span>`).join(" ")
        : "Nog niet beoordeeld";
      return `<button class="beoordeelkop" data-act="beoordeelopen" data-id="${p.id}"><span>Hoe was het?</span><span class="sv">${samenvat}</span></button>`;
    }
    const knop = (x, s, icoon, tekst) => `<button class="duim ${scores[x] === s ? "on" : ""}" data-act="score" data-id="${p.id}" data-persoon="${esc(x)}"
        data-score="${s}" aria-pressed="${scores[x] === s}" aria-label="${esc(x)}: ${tekst}" title="${tekst}">${icoon}</button>`;
    return `<div class="beoordeel" role="group" aria-label="Hoe was het?">
      <div class="bkop"><b>Hoe was het?</b><button class="link" data-act="beoordeelopen" data-id="">Klaar</button></div>
      ${personen
        .map((x) => {
          const oud = scores[x] && scores[x] !== 1 && scores[x] !== 5 ? `<small class="zacht">${GEZICHT[scores[x]]}</small>` : "";
          return `<div class="persoon"><span class="pn">${esc(x)} ${oud}</span><span class="gezichten">
            ${knop(x, 5, "👍", "Wel lekker")}${knop(x, 1, "👎", "Niet lekker")}</span></div>`;
        })
        .join("")}
      ${p.recept_id ? `<div class="persoon"><span class="pn">Bereiding</span><span class="gezichten">${this._fbKnoppen(p.recept_id, p.titel, ["lekker_snel", "teveel_werk"], true)}</span></div>` : ""}
      <p class="hint">Niet mee gegeten? Laat leeg, of tik nogmaals om te wissen.</p>
    </div>`;
  }

  // ------------------------------------------------------------------ feedback-knoppen
  _fbVan(rid, titel) {
    const fb = this.d.feedback || {};
    const inBieb = rid && this.recept(rid);
    return (inBieb ? fb[rid] : fb["titel:" + normPy(titel)])?.soorten || {};
  }

  _fbKnoppen(rid, titel, soorten = FB.map((f) => f[0]), groot = false) {
    const aan = this._fbVan(rid, titel);
    return FB.filter((f) => soorten.includes(f[0]))
      .map(
        ([k, icoon, tekst]) => `<button class="fb ${groot ? "groot" : ""} ${aan[k] ? "on" : ""} fb-${k}" data-act="fb" data-rid="${esc(rid || "")}"
          data-titel="${esc(titel || "")}" data-soort="${k}" aria-pressed="${!!aan[k]}" title="${tekst}">
          <span aria-hidden="true">${icoon}</span><span class="fbt">${tekst}</span></button>`
      )
      .join("");
  }

  // ------------------------------------------------------------------ voorstel: keuzestap
  _plannenView() {
    const pl = this.plan;
    if (!pl) return `<button class="terug" data-act="terug">‹ Terug</button><p class="leeg">Laden…</p>`;
    const personen = this.d.instellingen.personen || [];
    const aan = pl.dagen.filter((d) => d.aan);
    let html = `<button class="terug" data-act="terug">‹ Weekmenu</button>
      <h2 class="kop2">Weekmenu voorstellen</h2>
      <div class="rij3">
        <label class="veld smal2"><span>Van</span><input type="date" data-pl="van" value="${pl.van}"></label>
        <label class="veld smal2"><span>Tot en met</span><input type="date" data-pl="tot" value="${pl.tot}"></label>
      </div>
      <label class="veld"><span>Wens voor deze periode (optioneel)</span>
        <input data-pl="wens" value="${esc(pl.wens || "")}" placeholder="Bijv. iets met vis, of veel groente"></label>
      <p class="hint">Zet de dagen aan waarvoor Gemini 3 opties moet bedenken. Tik op een naam als iemand niet mee-eet.</p>
      <ol class="pdagen">`;
    for (const d of pl.dagen) {
      const info = [d.gepland ? `al gepland: ${esc(d.gepland)}` : "", d.agenda ? `agenda: ${esc(d.agenda)}` : ""].filter(Boolean).join(" · ");
      html += `<li class="pdag ${d.aan ? "" : "uit"}">
        <label class="pkop"><input type="checkbox" data-pd="aan" data-datum="${d.datum}" ${d.aan ? "checked" : ""}>
          <span class="dn">${esc(dagNaam(d.datum))}</span><span class="dd">${esc(datumKort(d.datum))}</span>
          ${info ? `<small class="zacht">${info}</small>` : ""}</label>`;
      if (d.aan) {
        html += `<div class="segment" role="group" aria-label="Soort dag">${SOORT.map(
          ([k, t]) => `<button class="${d.soort === k ? "on" : ""}" data-act="pdsoort" data-datum="${d.datum}" data-soort="${k}" aria-pressed="${d.soort === k}">${t}</button>`
        ).join("")}</div>`;
        if (d.soort === "koken") {
          html += `<div class="chips-wrap">${personen
            .map((n) => `<button class="pers ${d.aanwezig.includes(n) ? "on" : ""}" data-act="pdpers" data-datum="${d.datum}" data-naam="${esc(n)}"
              aria-pressed="${d.aanwezig.includes(n)}">${d.aanwezig.includes(n) ? "✓ " : ""}${esc(n)}</button>`)
            .join("")}</div>
            <div class="rij3">
              <label class="veld"><span>Kookt</span><input data-pd="kookt" data-datum="${d.datum}" value="${esc(d.kookt || "")}"></label>
              <label class="veld smal"><span>Max min</span><input type="number" min="5" step="5" inputmode="numeric" data-pd="minuten" data-datum="${d.datum}" value="${esc(d.minuten || 45)}"></label>
              <label class="veld smal"><span>Extra vriezer</span><input type="number" min="0" inputmode="numeric" data-pd="vriezer" data-datum="${d.datum}" value="${esc(d.vriezer || 0)}"></label>
            </div>
            <input class="pwens" data-pd="wens" data-datum="${d.datum}" value="${esc(d.wens || "")}" placeholder="Wens voor deze dag (optioneel)" aria-label="Wens voor ${esc(dagNaam(d.datum))}">
            ${d.notitie ? `<p class="hint">Afspraak: ${esc(d.notitie)}</p>` : ""}`;
        } else if (d.notitie) html += `<p class="hint">${esc(d.notitie)}</p>`;
      }
      html += `</li>`;
    }
    html += `</ol>
      <div class="plakbalk"><span class="zacht">${aan.length} ${aan.length === 1 ? "dag" : "dagen"}</span>
        <button class="primary" data-act="planmaak" ${aan.length ? "" : "disabled"}>✨ Maak voorstel</button></div>`;
    return html;
  }

  _planDag(datum) {
    return this.plan?.dagen.find((d) => d.datum === datum);
  }

  async _planLaad(van, tot) {
    let dagen = [];
    try {
      const r = await this._hass.callWS({ type: "boodschappen/eten_dagen", van, tot });
      dagen = (r.resultaat || []).map((d) => ({ ...d, wens: "" }));
      this.err = "";
    } catch (e) {
      this.err = e.message || "Kon de dagen niet laden.";
    }
    const oud = Object.fromEntries((this.plan?.dagen || []).map((d) => [d.datum, d]));
    this.plan = { van, tot, wens: this.plan?.wens || "", dagen: dagen.map((d) => oud[d.datum] || d) };
    this._render();
  }

  async _planMaak() {
    const pl = this.plan;
    const dagen = pl.dagen.map((d) => ({
      datum: d.datum, aan: d.aan, soort: d.soort, aanwezig: d.aanwezig, kookt: d.kookt,
      minuten: Number(d.minuten) || 45, vriezer: Number(d.vriezer) || 0, wens: d.wens || "",
    }));
    const n = dagen.filter((d) => d.aan && d.soort === "koken").length;
    const res = await this._do({ type: "boodschappen/eten_voorstel", dagen, wens: pl.wens || "" },
      n ? `Gemini bedenkt 3 opties voor ${n} ${n === 1 ? "dag" : "dagen"}… (20–40 s)` : "Voorstel maken…");
    if (res && this.d.voorstel) {
      this.plan = null;
      this.view = { soort: "voorstel" };
      this._scrollTop = true;
      this._render();
    }
  }

  // ------------------------------------------------------------------ voorstel: kiezen
  _voorstelView() {
    const v = this.d.voorstel;
    if (!v) return `<button class="terug" data-act="terug">‹ Weekmenu</button><p class="leeg">Er is geen voorstel.</p>`;
    const gekozen = v.dagen.filter((d) => !d.overslaan && (d.soort !== "koken" || d.geen || d.keuze !== null)).length;
    let html = `<button class="terug" data-act="terug">‹ Weekmenu</button>
      <h2 class="kop2">Voorstel ${esc(datumKort(v.van))} – ${esc(datumKort(v.tot))}</h2>
      ${v.wens ? `<p class="hint">Wens: ${esc(v.wens)}</p>` : ""}
      <p class="hint">Kies per dag een gerecht. Met de knoppen leert Gemini wat jullie wel en niet willen.</p>`;
    for (const d of v.dagen) html += this._voorstelDagKaart(d);
    html += `<div class="plakbalk">
        <button class="sec-btn" data-act="voorstelweg">Weggooien</button>
        <span class="vul"></span>
        <button class="primary" data-act="goedkeuren" ${gekozen ? "" : "disabled"}>Akkoord · ${gekozen} ${gekozen === 1 ? "dag" : "dagen"} plannen</button>
      </div>`;
    return html;
  }

  _voorstelDagKaart(d) {
    const ds = d.datum;
    const meta = [
      d.soort === "koken" && d.aanwezig?.length ? `👥 ${d.aanwezig.map(esc).join(", ")}` : "",
      d.soort === "koken" && d.kookt ? `kookt: ${esc(d.kookt)}` : "",
      d.soort === "koken" ? `max ${d.minuten} min` : "",
      d.soort === "koken" && d.porties ? `${d.porties} porties${d.vriezer ? ` (${d.vriezer} vriezer)` : ""}` : "",
    ].filter(Boolean).join(" · ");
    let body = "";
    if (this.busy && this.bezigDag === ds) {
      body = `<div class="laadblok" role="status"><span class="spinner" aria-hidden="true"></span><span>${esc(this.bezigTekst)}</span></div>`;
    } else if (d.overslaan) {
      body = `<p class="zacht">Wordt niet gepland.</p>`;
    } else if (d.soort !== "koken") {
      const t = { vriezer: "Uit de vriezer", anders: d.notitie || "Iemand anders kookt", niet: "Geen maaltijd" }[d.soort];
      body = `<p class="geentitel">${esc(t)} <small>geen boodschappen</small></p>`;
    } else if (d.geen) {
      body = `<p class="geentitel">Geen maaltijd <small>we koken niet</small></p>`;
    } else if (!d.opties?.length) {
      body = `<p class="zacht">Gemini gaf geen bruikbare opties. Vraag nieuwe opties.</p>`;
    } else {
      body = `<div class="opties">${d.opties.map((o, i) => this._optieKaart(d, o, i)).join("")}</div>`;
    }
    const nieuwOpen = this._optieWens === ds;
    return `<section class="vdag ${d.overslaan ? "uit" : ""}" data-datum="${ds}">
      <div class="dagkop"><span class="dn">${esc(dagNaam(ds))}</span><span class="dd">${esc(datumKort(ds))}</span></div>
      ${meta ? `<p class="vmeta">${meta}</p>` : ""}
      ${d.wens ? `<p class="hint">Wens: ${esc(d.wens)}</p>` : ""}
      ${body}
      ${
        nieuwOpen
          ? `<div class="aanpas"><label class="veld"><span>Wat wil je op ${esc(dagNaam(ds))}?</span>
               <input class="optie-wens" value="${esc(d.wens || "")}" placeholder="Bijv. iets met de airfryer, of soep"></label>
               <div class="knoppen"><span class="vul"></span><button class="sec-btn" data-act="optiewensdicht">Annuleren</button>
               <button class="primary" data-act="optienieuw" data-datum="${ds}">3 nieuwe opties</button></div></div>`
          : `<div class="knoppen klein">
               ${d.overslaan ? "" : `<button class="sec-btn" data-act="optiewens" data-datum="${ds}">✨ Nieuwe opties</button>`}
               ${d.soort === "koken" && !d.overslaan ? `<button class="sec-btn" data-act="vgeen" data-datum="${ds}" data-aan="${d.geen ? 0 : 1}">${d.geen ? "Toch koken" : "Geen maaltijd"}</button>` : ""}
               <button class="link" data-act="voverslaan" data-datum="${ds}" data-aan="${d.overslaan ? 0 : 1}">${d.overslaan ? "Toch plannen" : "Niet plannen"}</button>
             </div>`
      }
    </section>`;
  }

  _optieKaart(d, o, i) {
    const gekozen = d.keuze === i;
    const fb = this._fbVan(o.recept_id, o.titel);
    const af = !!fb.niet_lekker;
    const [ricoon, rtekst] = ROL[o.rol] || ROL.favoriet;
    const l = o.labels || {};
    const labels = [
      ...(l.actie || []).map((x) => `<span class="lab actie">🏷 ${esc(x)}</span>`),
      ...(l.bijna_op || []).map((x) => `<span class="lab bijna">⏰ ${esc(x)} op maken</span>`),
      ...(l.in_huis || []).slice(0, 4).map((x) => `<span class="lab huis">📦 ${esc(x)}</span>`),
    ].join("");
    return `<div class="optie ${gekozen ? "on" : ""} ${af ? "af" : ""}">
      <button class="okies" data-act="kies" data-datum="${d.datum}" data-i="${i}" aria-pressed="${gekozen}" ${af ? "disabled" : ""}>
        <span class="radio" aria-hidden="true"></span>
        <span class="otekst">
          <span class="rol">${ricoon} ${esc(rtekst)}</span>
          <span class="t">${esc(o.titel)}</span>
          <small>${[o.tijd ? `${o.tijd} min` : "", o.vega ? "🌱 vega" : ""].filter(Boolean).join(" · ")}</small>
          ${o.reden ? `<span class="reden">${esc(o.reden)}</span>` : ""}
          ${labels ? `<span class="labels">${labels}</span>` : ""}
        </span>
      </button>
      ${o.concept ? this._conceptInfo(o.concept) : ""}
      ${af ? `<p class="hint">Niet lekker: wordt niet meer voorgesteld.</p>` : ""}
      <div class="fbrij">${this._fbKnoppen(o.recept_id, o.titel)}</div>
    </div>`;
  }

  // ------------------------------------------------------------------ dag voor dag plannen
  // Per dag: wie eet mee, vinkjes (voorraad, snel, aanbieding), bronnen, wens → Gemini geeft 3 opties (met
  // meekijkvenster) → "Plan in" zet het in planning en agenda en de producten direct op de lijst.
  async _dagStart(datum = null) {
    const vandaag = iso(new Date());
    let van = datum || (iso(this.week) > vandaag ? iso(this.week) : vandaag);
    if (van < vandaag) van = vandaag;
    const f = this.d.instellingen.dagfilters || {};
    this.dp = {
      van, dagen: [], sel: datum, perDag: {}, res: {},
      filters: { voorraad: f.voorraad ?? true, snel: !!f.snel, aanbieding: f.aanbieding ?? true, bronnen: f.bronnen?.length ? [...f.bronnen] : [BIB] },
    };
    this.live = null;
    this.view = { soort: "dag" };
    this._scrollTop = true;
    this._render();
    await this._dagLaad(van);
  }

  async _dagLaad(van) {
    const tot = iso(plusDagen(vanIso(van), 6));
    try {
      const r = await this._hass.callWS({ type: "boodschappen/eten_dagen", van, tot });
      this.dp.dagen = r.resultaat || [];
      this.err = "";
    } catch (e) {
      this.err = e.message || "Kon de dagen niet laden.";
    }
    this.dp.van = van;
    const inBereik = this.dp.dagen.some((d) => d.datum === this.dp.sel);
    if (!inBereik) {
      const vrij = this.dp.dagen.find((d) => d.datum >= iso(new Date()) && !this._geplandOp(d.datum) && d.soort === "koken");
      this.dp.sel = (vrij || this.dp.dagen[0] || {}).datum || van;
    }
    this._render();
  }

  _geplandOp(datum) {
    return (this.d?.planning || []).find((p) => p.datum === datum && !p.historie);
  }

  _dagState(datum) {
    const info = this.dp.dagen.find((d) => d.datum === datum) || { soort: "koken", aanwezig: [] };
    if (!this.dp.perDag[datum]) {
      const opties = (this.d.dagopties || {})[datum];
      this.dp.perDag[datum] = { soort: info.soort || "koken", aanwezig: [...(opties?.aanwezig || info.aanwezig || [])], wens: opties?.filters?.wens || "" };
    }
    return { info, st: this.dp.perDag[datum] };
  }

  _dagView() {
    const dp = this.dp;
    if (!dp) return `<button class="terug" data-act="terug">‹ Weekmenu</button>`;
    const vandaag = iso(new Date());
    const strip = dp.dagen
      .map((d) => {
        const p = this._geplandOp(d.datum);
        const heeftOpties = !!(this.d.dagopties || {})[d.datum];
        const icoon = p ? (p.geen ? "—" : "✓") : heeftOpties ? "✨" : { vriezer: "❄", anders: "🍽", niet: "—" }[d.soort] || "";
        const voorbij = d.datum < vandaag;
        return `<button class="dpil ${d.datum === dp.sel ? "on" : ""} ${p ? "klaar" : ""} ${voorbij ? "voorbij" : ""}" data-act="dagkies" data-datum="${d.datum}"
          aria-pressed="${d.datum === dp.sel}" aria-label="${esc(dagNaam(d.datum))} ${esc(datumKort(d.datum))}${p ? `: ${esc(p.titel)}` : ""}">
          <span class="dk">${esc(dagNaam(d.datum, "short"))}</span><b>${vanIso(d.datum).getDate()}</b><small>${icoon}</small></button>`;
      })
      .join("");
    const vorigeKan = dp.van > vandaag;
    let html = `<button class="terug" data-act="terug">‹ Weekmenu</button>
      <h2 class="kop2">Dag voor dag plannen</h2>
      <div class="dstrip">
        <button class="rond klein" data-act="dagschuif" data-n="-7" ${vorigeKan ? "" : "disabled"} aria-label="Vorige dagen">‹</button>
        <div class="dpillen">${strip || `<span class="zacht">Laden…</span>`}</div>
        <button class="rond klein" data-act="dagschuif" data-n="7" aria-label="Volgende dagen">›</button>
      </div>`;
    if (dp.sel && dp.dagen.length) html += this._dagPaneel(dp.sel);
    return html;
  }

  _dagPaneel(datum) {
    const { info, st } = this._dagState(datum);
    const p = this._geplandOp(datum);
    const live = this.live && this.live.datum === datum ? this.live : null;
    const bezig = !!this.live?.bezig;
    const personen = this.d.instellingen.personen || [];
    const f = this.dp.filters;
    const opties = (this.d.dagopties || {})[datum];
    const res = this.dp.res[datum];
    let html = `<section class="dpaneel" aria-label="${esc(dagNaam(datum))}">
      <div class="dagkop"><span class="dn">${esc(dagNaam(datum))}</span><span class="dd">${esc(datumKort(datum))}</span>
        ${info.kookt ? `<span class="zacht">· kookt: ${esc(info.kookt)}</span>` : ""}</div>`;
    if (p && !res) {
      const r = p.recept_id ? this.recept(p.recept_id) : null;
      html += `<div class="dgepland"><span>Gepland: <b>${esc(p.titel)}</b>${p.op_lijst && p.lijst?.length ? ` <small class="zacht">· boodschappen staan op de lijst</small>` : ""}</span>
        ${r ? `<button class="link" data-act="dagrecept" data-id="${r.id}">Recept</button>` : ""}</div>
        <p class="hint">Kies hieronder iets anders om het te vervangen. Wat dit gerecht op de lijst zette, gaat er dan weer af.</p>`;
    }
    html += `<div class="segment" role="group" aria-label="Soort dag">${SOORT.map(
      ([k, t]) => `<button class="${st.soort === k ? "on" : ""}" data-act="dagsoort" data-soort="${k}" aria-pressed="${st.soort === k}" ${bezig ? "disabled" : ""}>${t}</button>`
    ).join("")}</div>`;
    if (st.soort !== "koken") {
      const t = st.soort === "anders" && info.notitie ? info.notitie : { vriezer: "Uit de vriezer", anders: "Iemand anders kookt", niet: "Geen maaltijd" }[st.soort];
      html += `<div class="knoppen"><button class="primary" data-act="dagvast" ${bezig ? "disabled" : ""}>Plan in: ${esc(t)}</button>
        <span class="zacht">geen boodschappen</span></div>`;
    } else {
      html += `<div class="dblok"><span class="dlabel">Wie eet mee</span><div class="chips-wrap">${personen
        .map((n) => `<button class="pers ${st.aanwezig.includes(n) ? "on" : ""}" data-act="dagpers" data-naam="${esc(n)}" aria-pressed="${st.aanwezig.includes(n)}">${st.aanwezig.includes(n) ? "✓ " : ""}${esc(n)}</button>`)
        .join("")}</div></div>
        <div class="dblok"><span class="dlabel">Gemini let op</span><div class="chips-wrap">
          ${[["voorraad", "📦 Kijk naar de voorraad"], ["snel", "⚡ Lekker snel"], ["aanbieding", "🏷 In de aanbieding"]]
            .map(([k, t]) => `<button class="tog ${f[k] ? "on" : ""}" data-act="dagtog" data-k="${k}" role="switch" aria-checked="${!!f[k]}">${f[k] ? "☑" : "☐"} ${t}</button>`)
            .join("")}</div></div>
        <div class="dblok"><span class="dlabel">Recepten uit</span><div class="chips-wrap">
          ${this._dagBronnen().map(([k, t]) => {
            const aan = f.bronnen.includes(k);
            return `<button class="tog bron ${aan ? "on" : ""}" data-act="dagbron" data-k="${esc(k)}" aria-pressed="${aan}">${aan ? "✓ " : ""}${esc(t)}</button>`;
          }).join("")}</div>
          ${f.bronnen.includes(BIB) ? "" : `<small class="zacht">Alleen nieuwe recepten van ${esc(f.bronnen.join(", "))} (plus wat je daarvan al bewaard hebt).</small>`}
        </div>
        <div class="dwens"><input id="dag-wens" value="${esc(st.wens)}" placeholder="Wens (optioneel), bijv. iets met pompoen" aria-label="Wens voor ${esc(dagNaam(datum))}" ${bezig ? "disabled" : ""}>
          <button class="primary" data-act="dagvoorstel" ${bezig || !f.bronnen.length ? "disabled" : ""}>✨ ${opties ? "Nieuwe opties" : "Stel voor"}</button></div>`;
    }
    if (live) html += `<div class="meekijk-plek">${this._meekijk(live)}</div>`;
    if (res) html += this._dagResultaat(res);
    else if (st.soort === "koken" && opties && !(live && live.bezig && live.modus === "opties")) {
      html += `<div class="opties">${opties.opties.map((o, i) => this._dagOptie(datum, o, i, bezig)).join("")}</div>
        <p class="hint">Voorgesteld ${esc(String(opties.gemaakt || "").slice(11, 16))}${opties.duur ? ` · in ${opties.duur} s` : ""}. Met de knoppen leert Gemini wat jullie wel en niet lekker vinden.</p>`;
    }
    const volgende = this._dagVolgende(datum);
    html += `<div class="knoppen">${volgende ? `<span class="vul"></span><button class="sec-btn" data-act="dagkies" data-datum="${volgende}" ${bezig ? "disabled" : ""}>Volgende dag: ${esc(dagNaam(volgende))} →</button>` : ""}</div>
      </section>`;
    return html;
  }

  _dagBronnen() {
    const web = (this.d.instellingen.bronnen || []).filter((b) => b.aan !== false && b.type === "website");
    return [[BIB, "📚 Mijn bibliotheek"], ...web.map((b) => [b.naam, b.naam])];
  }

  _dagVolgende(datum) {
    const i = this.dp.dagen.findIndex((d) => d.datum === datum);
    return i >= 0 && i < this.dp.dagen.length - 1 ? this.dp.dagen[i + 1].datum : null;
  }

  _dagOptie(datum, o, i, bezig) {
    const fb = this._fbVan(o.recept_id, o.titel);
    const af = !!fb.niet_lekker;
    const l = o.labels || {};
    const labels = [
      ...(l.actie || []).map((x) => `<span class="lab actie">🏷 ${esc(x)}</span>`),
      ...(l.bijna_op || []).map((x) => `<span class="lab bijna">⏰ ${esc(x)} op maken</span>`),
      ...(l.in_huis || []).slice(0, 4).map((x) => `<span class="lab huis">📦 ${esc(x)}</span>`),
    ].join("");
    const bekijk = o.recept_id
      ? `<button class="link" data-act="dagrecept" data-id="${o.recept_id}">Recept bekijken</button>`
      : o.url ? `<a class="link" href="${esc(o.url)}" target="_blank" rel="noopener">Bekijk op de site ↗</a>` : "";
    return `<div class="optie dopt ${af ? "af" : ""}">
      <div class="otekst">
        <span class="rol">${o.soort === "link" ? "🌐" : "📚"} ${esc(o.bron || "")}${o.soort === "link" ? " · nieuw recept" : ""}</span>
        <span class="t">${esc(o.titel)}</span>
        ${o.tijd || o.vega ? `<small>${[o.tijd ? `${o.tijd} min` : "", o.vega ? "🌱 vega" : ""].filter(Boolean).join(" · ")}</small>` : ""}
        ${o.reden ? `<span class="reden">${esc(o.reden)}</span>` : ""}
        ${labels ? `<span class="labels">${labels}</span>` : ""}
      </div>
      ${af ? `<p class="hint">Niet lekker: wordt niet meer voorgesteld.</p>` : ""}
      <div class="fbrij">${this._fbKnoppen(o.recept_id, o.titel)}</div>
      <div class="knoppen klein">${bekijk}<span class="vul"></span>
        <button class="primary klein" data-act="daginplan" data-i="${i}" ${bezig || af ? "disabled" : ""}>Plan in</button></div>
    </div>`;
  }

  _dagResultaat(res) {
    const perWinkel = {};
    for (const t of res.toegevoegd || []) (perWinkel[t.winkel] ||= []).push(t);
    let html = `<div class="dres" role="status">
      <p class="drestitel">✓ Gepland: <b>${esc(res.titel)}</b></p>`;
    const n = (res.toegevoegd || []).length;
    if (res.recept_id) {
      html += `<p>🛒 ${n ? `${n} ${n === 1 ? "product" : "producten"} op de boodschappenlijst` : "Niets nodig: alles is al in huis"}</p>`;
      for (const [w, lijst] of Object.entries(perWinkel))
        html += `<p class="dreswinkel"><b>${esc(w)}:</b> ${lijst.map((t) => `${esc(t.naam)}${t.aantal > 1 ? ` ×${fmtNum(t.aantal)}` : ""}`).join(", ")}</p>`;
      if (res.in_huis?.length) html += `<p class="zacht">📦 Uit de voorraad: ${res.in_huis.map(esc).join(", ")}</p>`;
      if (res.los?.length) html += `<p class="zacht">⚠ Niet gekoppeld aan Grocy (zelf toevoegen als het nodig is): ${res.los.map(esc).join(", ")}</p>`;
    }
    html += `<div class="knoppen klein">
        ${res.recept_id ? `<button class="sec-btn" data-act="dagrecept" data-id="${res.recept_id}">Recept bekijken</button>` : ""}
        <button class="link" data-act="dagopnieuw">Toch iets anders kiezen</button></div></div>`;
    return html;
  }

  // meekijkvenster: tussenstappen en de gedachten van Gemini, live
  _meekijk(live) {
    const icoon = { bezig: `<span class="spinner klein" aria-hidden="true"></span>`, klaar: "✓", fout: "⚠", info: "ℹ" };
    const kop = live.bezig
      ? live.modus === "plannen" ? "Bezig met plannen…" : "Gemini denkt na…"
      : live.fout ? "Dat lukte niet" : live.modus === "plannen" ? "Zo ging het plannen" : "Zo kwam Gemini tot deze opties";
    const open = live.bezig || live.fout || live.open;
    return `<details class="meekijk ${live.bezig ? "bezig" : ""}" ${open ? "open" : ""}>
      <summary data-act="meekijkklap">${live.bezig ? `<span class="spinner klein" aria-hidden="true"></span>` : "🔎"} ${esc(kop)}</summary>
      <ol class="mstappen">${live.stappen.map((s) => `<li class="ms-${s.status || "info"}"><span class="mi">${icoon[s.status] || icoon.info}</span><span>${esc(s.tekst)}</span></li>`).join("")}</ol>
      ${live.gedachten ? `<div class="gedachten" aria-live="off">${mini(live.gedachten)}</div>` : ""}
      ${live.fout ? `<p class="fout">${esc(live.fout)}</p>` : ""}
    </details>`;
  }

  _meekijkBijwerken() {
    const plek = this.shadowRoot.querySelector(".meekijk-plek");
    if (!plek || !this.live) return this._render();
    plek.innerHTML = this._meekijk(this.live);
    const g = plek.querySelector(".gedachten");
    if (g) g.scrollTop = g.scrollHeight;
  }

  _stream(msg, datum, modus) {
    if (this.live?.bezig) return Promise.resolve(null);
    this.live = { datum, modus, stappen: [], gedachten: "", bezig: true, fout: "", open: false };
    this.err = "";
    this._render();
    return new Promise((resolve) => {
      let klaar = false;
      const einde = (waarde) => {
        if (klaar) return;
        klaar = true;
        this.live.bezig = false;
        this._liveStop = null;
        resolve(waarde);
      };
      const opEvent = (ev) => {
        if (klaar) return;
        if (ev.soort === "stap") {
          const i = this.live.stappen.findIndex((s) => s.id === ev.id);
          if (i >= 0) this.live.stappen[i] = ev;
          else this.live.stappen.push(ev);
        } else if (ev.soort === "gedachte") {
          const g = this.live.gedachten;
          this.live.gedachten = g + (g && !g.endsWith("\n") ? "\n\n" : "") + ev.tekst;
        } else if (ev.soort === "klaar") {
          if (ev.eten) this.d = ev.eten;
          einde(ev.resultaat ?? true);
          return this._render();
        } else if (ev.soort === "fout") {
          this.live.fout = ev.tekst || "Dat lukte niet.";
          einde(null);
          return this._render();
        }
        this._meekijkBijwerken();
      };
      this._hass.connection
        .subscribeMessage(opEvent, msg)
        .then((unsub) => {
          this._liveStop = () => {
            try {
              Promise.resolve(unsub()).catch(() => {});
            } catch (_) {}
          };
          if (klaar) this._liveStop = null;
        })
        .catch((e) => {
          this.live.fout = e.message || "Dat lukte niet.";
          einde(null);
          this._render();
        });
    });
  }

  _liveAfbreken() {
    if (this._liveStop) this._liveStop();
    this._liveStop = null;
    if (this.live?.bezig) this.live = null;
  }

  async _dagVoorstel() {
    const datum = this.dp.sel;
    const { st } = this._dagState(datum);
    delete this.dp.res[datum];
    await this._stream({
      type: "boodschappen/eten_dag_opties", datum, aanwezig: st.aanwezig,
      filters: { ...this.dp.filters, wens: st.wens || "" },
    }, datum, "opties");
  }

  async _dagInplannen(keuze) {
    const datum = this.dp.sel;
    const { st } = this._dagState(datum);
    const res = await this._stream({ type: "boodschappen/eten_dag_inplannen", datum, keuze, aanwezig: st.aanwezig }, datum, "plannen");
    if (res && typeof res === "object") {
      this.dp.res[datum] = res;
      this._render();
    }
  }

  // ------------------------------------------------------------------ benodigde producten
  async _openBenodigd(planIds, terugMelding = "") {
    if (!planIds?.length) {
      this.view = null;
      this.melding = terugMelding;
      return this._render();
    }
    const res = await this._do({ type: "boodschappen/eten_benodigd", plan_ids: planIds }, "Benodigde producten berekenen…");
    if (!res || typeof res !== "object") return;
    this.bn = {
      plan_ids: res.plan_ids,
      melding: terugMelding,
      lijsten: res.lijsten || [],
      rijen: res.rijen.map((r) => ({ ...r, aantal: r.toevoegen || 1, sel: r.toevoegen > 0 })),
      los: res.los.map((x) => ({ ...x, sel: false, list_id: (res.lijsten[0] || {}).id })),
      genoegOpen: false,
    };
    this.view = { soort: "benodigd" };
    this._scrollTop = true;
    this._render();
  }

  _benodigdView() {
    const b = this.bn;
    if (!b) return `<button class="terug" data-act="terug">‹ Terug</button>`;
    const nodig = b.rijen.filter((r) => r.toevoegen > 0);
    const genoeg = b.rijen.filter((r) => r.toevoegen <= 0);
    const winkels = [...new Set(nodig.map((r) => r.winkel))];
    const rij = (r) => {
      const idx = b.rijen.indexOf(r);
      const info = [`nodig ${fmtNum(r.nodig)}`, r.in_huis ? `in huis ${fmtNum(r.in_huis)}` : "", r.op_lijst ? `al op lijst ${fmtNum(r.op_lijst)}` : ""]
        .filter(Boolean).join(" · ");
      return `<li class="bnrij ${r.sel ? "" : "uit"}">
        <label class="bnkies"><input type="checkbox" data-bn="${idx}" ${r.sel ? "checked" : ""}>
          <span class="txt"><span class="t">${esc(r.naam)}${r.actie ? ` <span class="lab actie">🏷 ${esc(r.actie)}</span>` : ""}</span>
          <small>${info} · voor ${r.voor.map(esc).join(", ")}</small></span></label>
        <span class="stepper"><button data-act="bnstap" data-i="${idx}" data-n="-1" ${r.aantal <= 1 ? "disabled" : ""} aria-label="Minder ${esc(r.naam)}">−</button>
          <span class="n">${fmtNum(r.aantal)}</span>
          <button data-act="bnstap" data-i="${idx}" data-n="1" aria-label="Meer ${esc(r.naam)}">+</button></span>
      </li>`;
    };
    const n = b.rijen.filter((r) => r.sel).length + b.los.filter((x) => x.sel).length;
    let html = `<h2 class="kop2">Benodigde producten</h2>
      ${b.melding ? `<p class="melding">${esc(b.melding)}</p>` : ""}
      <p class="hint">Dit is nog niet op de lijst gezet. Pas aan wat nodig is en tik dan op Toevoegen.</p>`;
    if (!nodig.length && !b.los.length) html += `<p class="leeg">Alles is al in huis of staat al op de lijst.</p>`;
    for (const w of winkels) {
      html += `<h3 class="bnwinkel">${esc(w)}</h3><ul class="bnlijst">${nodig.filter((r) => r.winkel === w).map(rij).join("")}</ul>`;
    }
    if (b.los.length) {
      html += `<h3 class="bnwinkel">Niet gekoppeld aan Grocy</h3><ul class="bnlijst">${b.los
        .map((x, i) => `<li class="bnrij ${x.sel ? "" : "uit"}">
          <label class="bnkies"><input type="checkbox" data-bnlos="${i}" ${x.sel ? "checked" : ""}>
            <span class="txt"><span class="t">${esc(x.tekst)}</span><small>voor ${esc(x.voor)}</small></span></label>
          <select data-bnlijst="${i}" aria-label="Lijst">${b.lijsten.map((l) => `<option value="${l.id}" ${l.id === x.list_id ? "selected" : ""}>${esc(l.name)}</option>`).join("")}</select>
        </li>`)
        .join("")}</ul>`;
    }
    if (genoeg.length) {
      html += `<section class="genoeg ${b.genoegOpen ? "open" : ""}">
        <button class="kop" data-act="genoeg" aria-expanded="${b.genoegOpen}">Al in huis of op de lijst <b>${genoeg.length}</b></button>
        ${b.genoegOpen ? `<ul class="bnlijst">${genoeg.map(rij).join("")}</ul>` : ""}</section>`;
    }
    html += `<div class="plakbalk">
        <button class="sec-btn" data-act="bnoverslaan">Overslaan</button><span class="vul"></span>
        <button class="primary" data-act="bntoevoegen" ${n ? "" : "disabled"}>Voeg toe aan boodschappenlijsten (${n})</button>
      </div>`;
    return html;
  }

  async _bnToevoegen() {
    const b = this.bn;
    const regels = b.rijen.filter((r) => r.sel && r.aantal > 0).map((r) => ({ product_id: r.product_id, aantal: r.aantal, list_id: r.list_id }));
    const los = b.los.filter((x) => x.sel).map((x) => ({ tekst: x.tekst, list_id: Number(x.list_id) }));
    const n = await this._do({ type: "boodschappen/eten_lijst_toevoegen", plan_ids: b.plan_ids, regels, los }, "Op de boodschappenlijsten zetten…");
    if (n === null || this.err) return;
    this.bn = null;
    this.view = null;
    this.melding = `${n} ${n === 1 ? "product" : "producten"} op de boodschappenlijsten gezet.`;
    this._scrollTop = true;
    this._render();
  }

  // ------------------------------------------------------------------ recepten
  _receptenBar() {
    const tags = [...new Set(this.recepten.flatMap((r) => r.tags || []))].sort();
    return `<div class="add">
        <input id="zoek" type="search" placeholder="Zoek recept of ingrediënt" value="${esc(this.zoek)}" aria-label="Zoek recept">
        <button class="addbtn zoekbtn" data-act="ontdek" aria-label="Ontdek recepten uit je bronnen" title="Ontdek uit je bronnen">🔎</button>
        <button class="addbtn" data-act="nieuw" aria-label="Nieuw recept" title="Nieuw recept">+</button>
      </div>
      <div class="chips-rij filters">
        <button class="filt ${this.filter === "" ? "on" : ""}" data-act="filter" data-f="">Alles</button>
        <button class="filt ${this.filter === "vega" ? "on" : ""}" data-act="filter" data-f="vega">🌱 Vega</button>
        <button class="filt ${this.filter === "snel" ? "on" : ""}" data-act="filter" data-f="snel">≤ 30 min</button>
        <button class="filt ${this.filter === "top" ? "on" : ""}" data-act="filter" data-f="top">👍 Favoriet</button>
        <button class="filt ${this.filter === "fb:lekker_snel" ? "on" : ""}" data-act="filter" data-f="fb:lekker_snel">⚡ Lekker snel</button>
        <button class="filt ${this.filter === "fb:niet_lekker" ? "on" : ""}" data-act="filter" data-f="fb:niet_lekker">👎 Niet lekker</button>
        <button class="filt ${this.filter === "bron" ? "on" : ""}" data-act="filter" data-f="bron">✓ Van onze bronnen</button>
        <button class="filt ${this.filter === "check" ? "on" : ""}" data-act="filter" data-f="check">⚠ Controleren${
          this.recepten.filter((r) => this._teControleren(r)).length ? ` (${this.recepten.filter((r) => this._teControleren(r)).length})` : ""}</button>
        ${tags.map((t) => `<button class="filt ${this.filter === "tag:" + t ? "on" : ""}" data-act="filter" data-f="tag:${esc(t)}">${esc(t)}</button>`).join("")}
      </div>`;
  }

  // Een recept is nog te controleren als het door de AI is bedacht, de herkomst onbekend is of er iets aan ontbreekt.
  _teControleren(r) {
    const soort = r.herkomst?.soort;
    return soort === "ai" || soort === "onbekend" || (r.kwaliteit || []).length > 0;
  }

  _herkomstLabel(r) {
    const h = r.herkomst;
    if (!h) return "";
    const icoon = { bron: "✓", web: "🔗", eigen: "🏠", ai: "🤖", onbekend: "?" }[h.soort] || "";
    return `<span class="herkomst h-${esc(h.soort)}">${icoon} ${esc(h.naam)}</span>`;
  }

  _receptenLijst() {
    const q = norm(this.zoek);
    let lijst = this.recepten.filter(
      (r) => !q || norm(r.naam).includes(q) || (r.ingredienten || []).some((i) => norm(i.tekst).includes(q)) || (r.tags || []).some((t) => norm(t).includes(q))
    );
    const f = this.filter;
    if (f === "vega") lijst = lijst.filter((r) => r.vega);
    if (f === "snel") lijst = lijst.filter((r) => r.tijd && r.tijd <= 30);
    if (f === "top")
      lijst = lijst.filter((r) => (r.score && r.score.gem >= 4) || this._fbVan(r.id, r.naam).wel_lekker)
        .sort((a, b) => (b.score?.gem || 4.5) - (a.score?.gem || 4.5));
    if (f.startsWith("fb:")) lijst = lijst.filter((r) => this._fbVan(r.id, r.naam)[f.slice(3)]);
    if (f === "bron") lijst = lijst.filter((r) => r.herkomst?.soort === "bron");
    if (f === "check") lijst = lijst.filter((r) => this._teControleren(r));
    if (f.startsWith("tag:")) lijst = lijst.filter((r) => (r.tags || []).includes(f.slice(4)));
    if (!this.recepten.length)
      return `<div class="leeg"><p>Nog geen recepten.</p><p>Tik op + om er een toe te voegen via een link, geplakte tekst, een foto of zelf invullen, of op 🔎 om er een te zoeken in je bronnen.</p></div>`;
    if (!lijst.length) return `<p class="leeg">Geen recepten gevonden.</p>`;
    return `<ul class="recepten">${lijst
      .map((r) => {
        const meta = [r.tijd ? `${r.tijd} min` : "", r.vega ? "🌱 vega" : "", r.laatst ? `laatst ${datumKort(r.laatst)}` : ""].filter(Boolean).join(" · ");
        return `<li><button class="rkaart" data-act="open" data-id="${r.id}">
          <span class="t">${esc(r.naam)}</span>
          <span class="rmeta">${meta}${r.score ? `<span class="score" title="Gemiddeld ${fmtNum(r.score.gem)} van 5">${GEZICHT[Math.round(r.score.gem)]} ${fmtNum(r.score.gem)}</span>` : ""}${
            FB.filter((x) => this._fbVan(r.id, r.naam)[x[0]]).map((x) => `<span title="${x[2]}">${x[1]}</span>`).join("")}</span>
          <span class="rmeta">${this._herkomstLabel(r)}${r.variant_van ? `<span class="herkomst" title="Aangepast van ${esc(r.variant_van_naam || "een ander recept")}">✨ variant</span>` : ""}${(r.kwaliteit || []).length ? `<span class="kwal" title="${esc(r.kwaliteit.join("; "))}">⚠ ${esc(r.kwaliteit[0])}${r.kwaliteit.length > 1 ? ` (+${r.kwaliteit.length - 1})` : ""}</span>` : ""}</span>
        </button></li>`;
      })
      .join("")}</ul>`;
  }

  _schaalTekst(tekst, factor) {
    if (factor === 1) return esc(tekst);
    return esc(tekst).replace(/^(\d+(?:[.,]\d+)?)(?=\s|[a-zA-Z])/, (m) => {
      const v = parseFloat(m.replace(",", ".")) * factor;
      return `<b>${fmtNum(Math.round(v * 10) / 10)}</b>`;
    });
  }

  _receptView(id) {
    const r = this.recept(id);
    if (!r) return `<button class="terug" data-act="terug">‹ Terug</button><p class="leeg">Recept niet gevonden.</p>`;
    const porties = this.porties || r.porties || 4;
    const factor = porties / (r.porties || 4);
    const sc = r.score;
    return `<button class="terug" data-act="terug">‹ Terug</button>
      <article class="recept">
        <h2>${esc(r.naam)}</h2>
        <p class="rmeta">${[r.tijd ? `${r.tijd} min` : "", r.vega ? "🌱 vega" : "", ...(r.tags || [])].filter(Boolean).map(esc).join(" · ")}</p>
        <p class="rmeta">${this._herkomstLabel(r)}${r.bron && /^https?:/i.test(r.bron) ? `<a class="bronlink" href="${esc(r.bron)}" target="_blank" rel="noopener">Origineel recept</a>` : ""}${r.vertaald_uit ? `<span>vertaald</span>` : ""}</p>
        ${this._variantInfo(r)}
        ${(r.kwaliteit || []).length ? `<div class="kwalblok" role="note"><b>Controleer dit recept</b><ul>${r.kwaliteit.map((k) => `<li>${esc(k)}</li>`).join("")}</ul></div>` : ""}
        ${r.herkomst?.soort === "ai" ? `<p class="hint">Dit recept is door de AI bedacht en komt niet van een van jullie bronnen.</p>` : ""}
        ${["ai", "onbekend", "web"].includes(r.herkomst?.soort) ? this._herkomstKoppelen(r) : ""}
        ${this._aanpassingBlok(r)}
        ${sc ? `<p class="scores">${Object.entries(sc.personen).map(([p, s]) => `<span>${esc(p)} ${GEZICHT[Math.round(s)]}</span>`).join("")}<small>${sc.keer}× gegeten</small></p>` : ""}
        <div class="fbrij">${this._fbKnoppen(r.id, r.naam, undefined, true)}</div>
        <div class="knoppen">
          <button class="primary groot" data-act="koken" data-id="${r.id}">Start koken</button>
          <button class="sec-btn" data-act="bewerk" data-id="${r.id}">Bewerken</button>
          <button class="sec-btn" data-act="raanpas" data-id="${r.id}">✨ Aanpassen met Gemini</button>
        </div>
        ${this.raanpas?.id === r.id ? this._aanpasKaart(r) : ""}
        ${r.vorige ? `<p class="hint">Dit recept is aangepast${r.aanpassing ? ` (“${esc(r.aanpassing)}”)` : ""}. <button class="link" data-act="aanpasongedaan" data-id="${r.id}">Ongedaan maken: terug naar de vorige versie</button></p>` : ""}
        <div class="porties"><span>Porties</span>
          <span class="stepper"><button data-act="porties" data-n="-1" aria-label="Minder porties">−</button><span class="n">${fmtNum(porties)}</span>
          <button data-act="porties" data-n="1" aria-label="Meer porties">+</button></span></div>
        <h3>Ingrediënten</h3>
        <ul class="ingr">${(r.ingredienten || [])
          .map((i) => {
            const p = i.product_id ? this.product(i.product_id) : null;
            return `<li><span>${this._schaalTekst(i.tekst, factor)}</span>${
              i.basis ? `<small>in huis</small>` : p ? `<small>${esc(p.name)}</small>` : `<small class="warn">niet gekoppeld</small>`
            }</li>`;
          })
          .join("")}</ul>
        <h3>Bereiding</h3>
        <ol class="stappen">${(r.stappen || []).map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
        ${r.notitie ? `<p class="notitie">${esc(r.notitie)}</p>` : ""}
      </article>`;
  }

  _variantInfo(r) {
    const van = r.variant_van ? this.recept(r.variant_van) : null;
    const varianten = this.recepten.filter((x) => x.variant_van === r.id);
    if (!r.variant_van && !varianten.length) return "";
    return `<p class="rmeta varianten">${
      r.variant_van
        ? `✨ Variant van ${van ? `<button class="link" data-act="open" data-id="${van.id}">${esc(van.naam)}</button>` : esc(r.variant_van_naam || "een ander recept")}${r.aanpassing ? ` · “${esc(r.aanpassing)}”` : ""}`
        : ""
    }${varianten.length ? `Varianten: ${varianten.map((x) => `<button class="link" data-act="open" data-id="${x.id}">${esc(x.naam)}</button>`).join(" · ")}` : ""}</p>`;
  }

  _herkomstKoppelen(r) {
    const bronnen = this.d.instellingen.bronnen || [];
    return `<details class="herkomstkoppel"><summary>Herkomst koppelen of controleren</summary>
      <p class="hint">Weet je waar dit recept vandaan komt? Koppel het aan een bron, of zoek of het recept in je bronnen staat.</p>
      <div class="knoppen klein">
        ${bronnen.length ? `<select id="rbron" aria-label="Bron">${bronnen.map((b) => `<option>${esc(b.naam)}</option>`).join("")}</select>
        <button class="sec-btn" data-act="rbronzet" data-id="${r.id}">Koppel aan bron</button>` : ""}
        <button class="sec-btn" data-act="reigen" data-id="${r.id}">Het is een eigen recept</button>
        <button class="sec-btn" data-act="ontdek" data-vraag="${esc(r.naam)}">🔎 Zoek in mijn bronnen</button>
      </div></details>`;
  }

  _aanpasKaart(r) {
    const w = this.raanpas?.wens || "";
    return `<section class="kaart aanpaskaart" aria-label="Aanpassen met Gemini">
      <h3>✨ Aanpassen met Gemini</h3>
      <p class="hint">Gemini past dit recept aan en laat je eerst zien wat er verandert. Pas daarna bewaar je het, als variant of in plaats van het origineel.</p>
      <div class="chips-wrap">${SNEL_AANPASSEN.map((t) => `<button class="filt" data-act="raanpassnel" data-t="${esc(t)}">${esc(t)}</button>`).join("")}</div>
      <label class="veld"><span>Wat moet er anders?</span>
        <textarea class="raanpas-wens" rows="3" placeholder="bijv. maak het vegetarisch en gebruik wat meer kruiden">${esc(w)}</textarea></label>
      <div class="knoppen">
        <button class="primary" data-act="raanpasdoe" data-id="${r.id}">Laat Gemini aanpassen</button>
        <button class="sec-btn" data-act="raanpasdicht">Annuleren</button>
      </div></section>`;
  }

  _aanpassingBlok(r) {
    const p = this.d.aanpassingen?.[r.id];
    if (!p) return "";
    const v = p.verschil || {};
    const ing = v.ingredienten || { erbij: [], weg: [], gewijzigd: [] };
    const nieuw = p.recept || {};
    const geenIng = !ing.erbij.length && !ing.weg.length && !ing.gewijzigd.length;
    const wijz = (x) => (x ? `<p class="verschil">${esc(x[2] || "")}<s>${esc(x[0] ?? "—")}</s> → <b>${esc(x[1] ?? "—")}</b></p>` : "");
    return `<section class="voorstelblok" aria-label="Voorstel van Gemini">
      <p class="sugkop">✨ Voorstel van Gemini voor: “${esc(p.wens)}”</p>
      ${(p.wijzigingen || []).length ? `<ul class="wijz">${p.wijzigingen.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
      ${v.naam ? `<p class="verschil">Naam: <s>${esc(v.naam[0])}</s> → <b>${esc(v.naam[1])}</b></p>` : ""}
      ${v.porties ? `<p class="verschil">Porties: <s>${esc(v.porties[0] ?? "—")}</s> → <b>${esc(v.porties[1] ?? "—")}</b></p>` : ""}
      ${v.tijd ? `<p class="verschil">Tijd: <s>${esc(v.tijd[0] ?? "—")} min</s> → <b>${esc(v.tijd[1] ?? "—")} min</b></p>` : ""}
      <h3>Ingrediënten</h3>
      <div class="diff">
        ${ing.gewijzigd.map((g) => `<div class="d-wijzig">↔ <s>${esc(g.van)}</s> → ${esc(g.naar)}</div>`).join("")}
        ${ing.erbij.map((t) => `<div class="d-erbij">+ ${esc(t)}</div>`).join("")}
        ${ing.weg.map((t) => `<div class="d-weg">− ${esc(t)}</div>`).join("")}
        ${geenIng ? `<p class="hint">De ingrediënten blijven gelijk.</p>` : ""}
      </div>
      <details open><summary>Nieuwe bereiding (${(nieuw.stappen || []).length} stappen, gewijzigde stappen gemarkeerd)</summary>
        <ol class="stappen">${(nieuw.stappen || []).map((st, i) => `<li class="${v.stappen_nieuw?.[i] ? "nw" : ""}">${esc(st)}</li>`).join("")}</ol></details>
      ${(p.kwaliteit || []).length ? `<div class="kwalblok" role="note"><b>Let op</b><ul>${p.kwaliteit.map((k) => `<li>${esc(k)}</li>`).join("")}</ul></div>` : ""}
      <div class="knoppen">
        <button class="primary" data-act="aanpastoe" data-id="${r.id}" data-modus="variant">Opslaan als variant</button>
        <button class="sec-btn" data-act="aanpastoe" data-id="${r.id}" data-modus="vervang">Origineel vervangen</button>
        <button class="sec-btn" data-act="raanpasnogmaals" data-id="${r.id}">Opnieuw, met andere wens</button>
        <button class="wis" data-act="aanpasweg" data-id="${r.id}">Weggooien</button>
      </div></section>`;
  }

  // ------------------------------------------------------------------ bronnen
  _bronnenView() {
    const bronnen = this.d.instellingen.bronnen || [];
    const info = this.d.bronnen_info || {};
    const n = this.bronNieuw ||= { type: "website", url: "", naam: "", notitie: "", check: null };
    const kaart = (b, i) => {
      const inf = info[b.naam] || { recepten: 0 };
      const adres = b.type === "website" ? `<a href="https://${esc(b.domein + (b.pad || ""))}" target="_blank" rel="noopener">${esc(b.domein + (b.pad || ""))}</a>` : "";
      if (this.bronEdit === i)
        return `<div class="kaart bron" data-i="${i}">
          <label class="veld"><span>Naam</span><input class="be-naam" value="${esc(b.naam)}"></label>
          ${b.type === "website" ? `<label class="veld"><span>Adres (eventueel met pad)</span><input class="be-adres" value="${esc(b.domein + (b.pad || ""))}" inputmode="url"></label>` : ""}
          <label class="veld"><span>Notitie</span><input class="be-notitie" value="${esc(b.notitie || "")}" placeholder="bijv. boek uit de bibliotheek"></label>
          <div class="knoppen"><button class="primary" data-act="bronopslaan" data-i="${i}">Opslaan</button>
            <button class="sec-btn" data-act="bronbewerkdicht">Annuleren</button></div></div>`;
      return `<div class="kaart bron ${b.aan === false ? "uit" : ""}">
        <div class="bronkop"><b>${esc(b.naam)}</b><span class="typebadge">${esc(BRON_TYPE[b.type] || b.type)}</span><span class="vul"></span>
          <label class="check klein" title="Uit: deze bron doet niet mee bij Ontdek"><input type="checkbox" data-bronaan="${i}" ${b.aan === false ? "" : "checked"}> aan</label></div>
        ${adres ? `<p class="rmeta">${adres}</p>` : ""}
        <p class="rmeta"><span>${inf.recepten} ${inf.recepten === 1 ? "recept" : "recepten"}</span>${inf.score ? `<span class="score">${GEZICHT[Math.round(inf.score)]} gem. ${fmtNum(inf.score)}</span>` : ""}${
          inf.wel ? `<span>👍 ${inf.wel}</span>` : ""}${inf.niet ? `<span>👎 ${inf.niet}</span>` : ""}</p>
        ${b.notitie ? `<p class="hint">${esc(b.notitie)}</p>` : ""}
        <div class="knoppen klein">
          ${b.type === "website" ? `<button class="sec-btn" data-act="ontdek" data-bron="${esc(b.naam)}">🔎 Zoek recepten</button>` : `<button class="sec-btn" data-act="nieuw" data-bron="${esc(b.naam)}">📷 Recept uit foto</button>`}
          <button class="sec-btn" data-act="bronbewerk" data-i="${i}">Bewerken</button>
          <span class="vul"></span>
          <button class="wis klein" data-act="bronweg" data-i="${i}">Verwijderen</button>
        </div></div>`;
    };
    const c = n.check;
    return `<button class="terug" data-act="terug">‹ Terug</button>
      <h2 class="kop2">Bronnen</h2>
      <p class="hint">De koks, sites en boeken waar jullie recepten vandaan komen. Bij elk recept zie je de herkomst; bij Ontdek (🔎) zoek je in de websites die hier aan staan.</p>
      ${bronnen.length ? bronnen.map(kaart).join("") : `<p class="leeg">Nog geen bronnen. Voeg er hieronder een toe.</p>`}
      <section class="kaart" aria-label="Bron toevoegen">
        <h3>Bron toevoegen</h3>
        <label class="veld"><span>Soort</span>
          <select id="bn-type">${Object.entries(BRON_TYPE).map(([k, t]) => `<option value="${k}" ${n.type === k ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        ${n.type === "website" ? `
          <label class="veld"><span>Adres van de site (of van een deel ervan)</span>
            <input id="bn-url" type="url" inputmode="url" placeholder="https://…" value="${esc(n.url)}"></label>
          <button class="sec-btn" data-act="broncheck">Controleer de site</button>
          ${c ? `<div class="${c.leesbaar ? "melding" : "kwalblok"}" role="status"><b>${esc(c.naam)}</b> (${esc(c.domein + (c.pad || ""))})<br>${esc(c.advies)}</div>` : ""}` : ""}
        <label class="veld"><span>Naam</span><input id="bn-naam" value="${esc(n.naam || (c ? c.naam : ""))}" placeholder="${n.type === "boek" ? "bijv. Ottolenghi Simple" : "bijv. Brenda Kookt"}"></label>
        <label class="veld"><span>Notitie (optioneel)</span><input id="bn-notitie" value="${esc(n.notitie)}"></label>
        <button class="primary" data-act="bronnieuw">Bron toevoegen</button>
        ${n.type !== "website" ? `<p class="hint">Recepten uit een boek voeg je toe met een foto van de pagina (bij + → Foto van een recept) en kies daar deze bron.</p>` : ""}
      </section>`;
  }

  async _bronnenOpslaan(lijst, bezig = "Bronnen opslaan…") {
    return this._do({ type: "boodschappen/eten_bronnen", bronnen: lijst }, bezig);
  }

  // ------------------------------------------------------------------ ontdek
  _ontdekView() {
    const o = this.ont ||= { bron: "", vraag: "", res: null, nota: "" };
    const bronnen = (this.d.instellingen.bronnen || []).filter((b) => b.type === "website" && b.aan !== false);
    const lijst = o.res
      ? o.res.length
        ? `<ul class="ontdek">${o.res
            .map(
              (x, i) => `<li class="orij ${x.fout ? "mislukt" : ""}">
            <div class="otekst"><span class="t">${esc(x.titel)}</span>
              <small>${esc(x.bron)}${x.reden ? ` · ${esc(x.reden)}` : ""}</small>
              ${x.fout ? `<small class="warn">${esc(x.fout)}</small>` : ""}</div>
            <div class="knoppen klein">
              <a class="sec-btn" href="${esc(x.url)}" target="_blank" rel="noopener" aria-label="Bekijk ${esc(x.titel)} op de site">↗ Bekijk</a>
              ${x.toegevoegd ? `<button class="sec-btn" data-act="open" data-id="${esc(x.toegevoegd)}">✓ Toegevoegd · open</button>`
                : x.fout ? "" : `<button class="primary klein" data-act="ontvoeg" data-i="${i}">Toevoegen</button>`}
            </div></li>`
            )
            .join("")}</ul>${o.nota ? `<p class="hint">${esc(o.nota)}</p>` : ""}`
        : `<p class="leeg">Niets gevonden. Probeer een ander woord, of laat Gemini kiezen.</p>`
      : "";
    return `<button class="terug" data-act="terug">‹ Terug</button>
      <h2 class="kop2">Ontdek uit je bronnen</h2>
      ${bronnen.length ? "" : `<p class="melding">Er staat geen website als bron aan. <button class="link" data-act="bronnen">Bronnen beheren</button></p>`}
      <section class="kaart">
        <label class="veld"><span>Bron</span>
          <select id="ont-bron"><option value="">Alle bronnen</option>${bronnen.map((b) => `<option ${o.bron === b.naam ? "selected" : ""}>${esc(b.naam)}</option>`).join("")}</select></label>
        <label class="veld"><span>Zoekwoord of wens</span>
          <input id="ont-vraag" type="search" placeholder="bijv. pompoen, snelle pasta, iets met linzen" value="${esc(o.vraag)}"></label>
        <div class="knoppen"><button class="primary" data-act="ontzoek">Zoeken</button>
          <button class="sec-btn" data-act="ontai" title="Gemini kiest op basis van je wens, het gezin, de voorraad en de aanbiedingen">✨ Laat Gemini kiezen</button></div>
        <p class="hint">Zoeken kijkt naar de titels op de sites (bij Ottolenghi zijn die Engels). Zonder zoekwoord krijg je een willekeurige greep. Gemini kiest ook met jullie voorkeuren, de voorraad en de aanbiedingen, ook in een andere taal.</p>
      </section>
      ${lijst}`;
  }

  _kokenView(id) {
    const r = this.recept(id);
    if (!r) return `<button class="terug" data-act="terug">‹ Terug</button>`;
    const stappen = r.stappen || [];
    const n = stappen.length;
    const i = Math.max(0, Math.min(this.stap, n));
    const factor = (this.porties || r.porties || 4) / (r.porties || 4);
    const ingr = `<ul class="ingr compact">${(r.ingredienten || []).map((x) => `<li>${this._schaalTekst(x.tekst, factor)}</li>`).join("")}</ul>`;
    let kern;
    if (i === 0) kern = `<p class="kstap-label">Klaarzetten</p>${ingr}`;
    else kern = `<p class="kstap-label">Stap ${i} van ${n}</p><p class="kstap">${esc(stappen[i - 1])}</p>`;
    return `<div class="kokenkop"><button class="terug" data-act="stopkoken">‹ Stoppen</button><b>${esc(r.naam)}</b></div>
      <div class="voortgang" aria-hidden="true"><span style="width:${n ? (i / n) * 100 : 0}%"></span></div>
      <section class="kokenkern" aria-live="polite">${kern}</section>
      ${i > 0 ? `<details class="ingrklap"><summary>Ingrediënten</summary>${ingr}</details>` : ""}
      <div class="kokennav">
        <button class="sec-btn groot" data-act="stap" data-n="-1" ${i === 0 ? "disabled" : ""}>Vorige</button>
        ${i < n ? `<button class="primary groot" data-act="stap" data-n="1">${i === 0 ? "Beginnen" : "Volgende"}</button>`
          : `<button class="primary groot" data-act="stopkoken">Eet smakelijk!</button>`}
      </div>`;
  }

  _bewerkView(id) {
    const r = id ? this.recept(id) : this._concept;
    if (!r) return `<button class="terug" data-act="terug">‹ Terug</button>`;
    const rijen = (r.ingredienten || []).concat([{ tekst: "", product_id: null, aantal: 1 }]);
    return `<button class="terug" data-act="terug">‹ Annuleren</button>
      <form class="bewerk" onsubmit="return false">
        <label class="veld"><span>Naam</span><input name="naam" value="${esc(r.naam || "")}" required></label>
        <div class="rij3">
          <label class="veld smal"><span>Porties</span><input name="porties" type="number" min="1" inputmode="numeric" value="${esc(r.porties || 4)}"></label>
          <label class="veld smal"><span>Minuten</span><input name="tijd" type="number" min="0" inputmode="numeric" value="${esc(r.tijd || "")}"></label>
          <label class="check"><input type="checkbox" name="vega" ${r.vega ? "checked" : ""}> Vega</label>
        </div>
        <label class="veld"><span>Labels (komma's)</span><input name="tags" value="${esc((r.tags || []).join(", "))}" placeholder="pasta, oven, soep"></label>
        <h3>Ingrediënten</h3>
        <p class="hint">Koppel aan een Grocy-product om het op de boodschappenlijst te krijgen. Aantal = verpakkingen/stuks voor ${esc(r.porties || 4)} porties.</p>
        <datalist id="producten">${(this.d.producten || []).map((p) => `<option value="${esc(p.name)}">`).join("")}</datalist>
        <div class="ingrrijen">${rijen
          .map((i, n) => {
            const p = i.product_id ? this.product(i.product_id) : null;
            return `<div class="irij" data-n="${n}">
              <input class="itekst" placeholder="bijv. 2 uien" value="${esc(i.tekst)}" aria-label="Ingrediënt">
              <input class="iprod" list="producten" placeholder="Grocy-product" value="${esc(p ? p.name : "")}" aria-label="Grocy-product">
              <input class="iaantal" type="number" min="0" step="0.5" inputmode="decimal" value="${esc(i.aantal ?? 1)}" aria-label="Aantal">
              <label class="check klein" title="Altijd in huis (niet op de lijst)"><input type="checkbox" class="ibasis" ${i.basis ? "checked" : ""}> in huis</label>
            </div>`;
          })
          .join("")}</div>
        <button class="link" data-act="ingrrij">+ Regel</button>
        <h3>Bereiding</h3>
        <label class="veld"><span>Eén stap per regel</span><textarea name="stappen" rows="8">${esc((r.stappen || []).join("\n"))}</textarea></label>
        <label class="veld"><span>Notitie</span><textarea name="notitie" rows="2">${esc(r.notitie || "")}</textarea></label>
        <label class="veld"><span>Bron (link)</span><input name="bron" value="${esc(r.bron || "")}"></label>
        <label class="check"><input type="checkbox" name="eigen" ${r.eigen ? "checked" : ""}> Eigen recept (van onszelf, hoeft niet van een bron te komen)</label>
        <div class="knoppen">
          ${id ? `<button class="wis" data-act="receptweg" data-id="${id}">Verwijderen</button>` : ""}
          ${id ? `<button class="sec-btn" data-act="koppel" data-id="${id}" title="Laat de AI de ingrediënten aan Grocy-producten koppelen">Automatisch koppelen</button>` : ""}
          <span class="vul"></span>
          <button class="primary" data-act="receptopslaan" data-id="${id || ""}">Opslaan</button>
        </div>
      </form>`;
  }

  _nieuwView() {
    const bronnen = this.d.instellingen.bronnen || [];
    const opties = (gekozen) => `<option value="">— geen bron —</option>${bronnen.map((b) => `<option ${gekozen === b.naam ? "selected" : ""}>${esc(b.naam)}</option>`).join("")}`;
    const fotos = this._fotos || [];
    return `<button class="terug" data-act="terug">‹ Terug</button>
      <h2 class="kop2">Nieuw recept</h2>
      <div class="knoppen klein">
        <button class="sec-btn" data-act="ontdek">🔎 Zoek in je bronnen</button>
        <button class="sec-btn" data-act="bronnen">📚 Bronnen beheren</button>
      </div>
      <section class="kaart">
        <h3>Van een website</h3>
        <label class="veld"><span>Link naar het recept</span><input id="imp-url" type="url" inputmode="url" placeholder="https://…"></label>
        <button class="primary" data-act="importeer" data-bron="url">Ophalen</button>
      </section>
      <section class="kaart">
        <h3>Foto van een recept</h3>
        <p class="hint">Bijvoorbeeld een pagina uit een kookboek. Loopt het recept over meer pagina's, kies dan er tot 4 tegelijk. De foto wordt alleen gelezen en niet bewaard.</p>
        <label class="veld"><span>Foto('s) maken of kiezen</span><input id="imp-foto" type="file" accept="image/*" multiple></label>
        ${fotos.length ? `<p class="melding" role="status">${fotos.length} ${fotos.length === 1 ? "foto" : "foto's"} klaar om te lezen.</p>` : ""}
        ${this._fotoFout ? `<p class="fout" role="alert">${esc(this._fotoFout)}</p>` : ""}
        <label class="veld"><span>Uit welke bron? (optioneel)</span><select id="imp-bron-foto">${opties(this._fotoBron || "")}</select></label>
        <button class="primary" data-act="importeer" data-bron="foto" ${fotos.length ? "" : "disabled"}>Omzetten</button>
      </section>
      <section class="kaart">
        <h3>Tekst plakken</h3>
        <label class="veld"><span>Ingrediënten en bereiding, zoals je ze hebt</span><textarea id="imp-tekst" rows="6"></textarea></label>
        <label class="veld"><span>Uit welke bron? (optioneel)</span><select id="imp-bron-tekst">${opties("")}</select></label>
        <button class="primary" data-act="importeer" data-bron="tekst">Omzetten</button>
      </section>
      <section class="kaart">
        <h3>Zelf invullen</h3>
        <button class="sec-btn" data-act="handmatig">Leeg recept</button>
      </section>
      <p class="hint">Van een website nemen we ingrediënten en bereiding letterlijk over; Gemini koppelt alleen de ingrediënten aan je Grocy-producten en vertaalt als het recept niet Nederlands is. Ontbreekt er iets, dan zie je dat bij het recept.</p>
      <p class="hint">Jullie bronnen: ${bronnen.map((b) => (b.domein ? `<a href="https://${esc(b.domein + (b.pad || ""))}" target="_blank" rel="noopener">${esc(b.naam)}</a>` : esc(b.naam))).join(" · ") || "nog geen"}</p>`;
  }

  // Foto verkleinen (max 1600 px, JPEG) zodat hij snel en binnen de berichtgrootte van Home Assistant blijft.
  async _fotoNaarData(file) {
    const url = URL.createObjectURL(file);
    try {
      const img = await new Promise((ok, fout) => {
        const i = new Image();
        i.onload = () => ok(i);
        i.onerror = () => fout(new Error("Dit bestand is geen foto die ik kan lezen."));
        i.src = url;
      });
      const f = Math.min(1, 1600 / Math.max(img.naturalWidth, img.naturalHeight));
      const c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(img.naturalWidth * f));
      c.height = Math.max(1, Math.round(img.naturalHeight * f));
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      return c.toDataURL("image/jpeg", 0.82);
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  _instView() {
    const i = this.d.instellingen;
    const e = (this.inst ||= JSON.parse(JSON.stringify(i)));
    const vandaagMa = iso(maandag(new Date()));
    const dagVinkjes = (pad, dagen) =>
      `<span class="dagvink">${KORT.map((k, n) => `<label><input type="checkbox" data-pad="${pad}" data-dag="${n}" ${(dagen || []).includes(n) ? "checked" : ""}>${k}</label>`).join("")}</span>`;
    const persoon = (p, n) => {
      const pat = p.patroon || { soort: "altijd" };
      const weekA = !pat.anker || Math.round((vanIso(vandaagMa) - vanIso(pat.anker)) / 604800000) % 2 === 0;
      return `<div class="kaart pkaart">
        <div class="rij3">
          <label class="veld"><span>Naam</span><input data-pad="profielen.${n}.naam" value="${esc(p.naam)}"></label>
          <label class="check"><input type="checkbox" data-pad="profielen.${n}.kind" ${p.kind ? "checked" : ""}> Kind (halve portie)</label>
        </div>
        <div class="rij3">
          <label class="veld"><span>Dieet</span><input data-pad="profielen.${n}.dieet" value="${esc(p.dieet || "")}" placeholder="bijv. vegetarisch"></label>
          <label class="veld"><span>Allergie (altijd vermijden)</span><input data-pad="profielen.${n}.allergie" value="${esc(p.allergie || "")}"></label>
        </div>
        <div class="rij3">
          <label class="veld"><span>Eet niet</span><input data-pad="profielen.${n}.niet_eten" value="${esc(p.niet_eten || "")}" placeholder="bijv. champignons, pittig"></label>
          <label class="veld"><span>Lievelingskost</span><input data-pad="profielen.${n}.lievelingskost" value="${esc(p.lievelingskost || "")}"></label>
        </div>
        <label class="veld"><span>Eet mee</span>
          <select data-pad="profielen.${n}.patroon.soort">
            <option value="altijd" ${pat.soort === "altijd" ? "selected" : ""}>Altijd</option>
            <option value="dagen" ${pat.soort === "dagen" ? "selected" : ""}>Op vaste dagen</option>
            <option value="omdeweek" ${pat.soort === "omdeweek" ? "selected" : ""}>Om de week verschillend</option>
          </select></label>
        ${pat.soort === "dagen" ? dagVinkjes(`profielen.${n}.patroon.dagen`, pat.dagen) : ""}
        ${
          pat.soort === "omdeweek"
            ? `<div class="omdeweek">
                 <div><small>Week A</small>${dagVinkjes(`profielen.${n}.patroon.dagen`, pat.dagen)}</div>
                 <div><small>Week B</small>${dagVinkjes(`profielen.${n}.patroon.dagen_b`, pat.dagen_b)}</div>
                 <label class="veld"><span>Deze week (week ${weekNr(new Date())}) is</span>
                   <select data-anker="${n}"><option value="A" ${weekA ? "selected" : ""}>week A</option><option value="B" ${weekA ? "" : "selected"}>week B</option></select></label>
               </div>`
            : ""
        }
        <div class="knoppen"><span class="vul"></span><button class="wis klein" data-act="persweg" data-i="${n}" aria-label="${esc(p.naam)} verwijderen">Verwijderen</button></div>
      </div>`;
    };
    const wd = e.weekdagen || [];
    return `<button class="terug" data-act="terug">‹ Terug</button>
      <h2 class="kop2">Instellingen</h2>
      <form class="bewerk" onsubmit="return false">
        <h3>Wie zijn we</h3>
        <p class="hint">Gemini houdt rekening met dieet, allergieën en wie er die dag mee-eet. Kinderen tellen als halve portie.</p>
        ${(e.profielen || []).map(persoon).join("")}
        <button class="sec-btn" data-act="persnieuw">+ Persoon</button>

        <h3>Per weekdag</h3>
        <p class="hint">Standaard per dag. In het voorstel kun je het per dag nog aanpassen.</p>
        <div class="wdtabel">${wd
          .map(
            (w, n) => `<div class="wdrij">
              <b class="wdnaam">${KORT[n]}</b>
              <select data-pad="weekdagen.${n}.soort" aria-label="Soort dag ${KORT[n]}">${SOORT.map(([k, t]) => `<option value="${k}" ${w.soort === k ? "selected" : ""}>${t}</option>`).join("")}</select>
              <input data-pad="weekdagen.${n}.kookt" value="${esc(w.kookt || "")}" placeholder="Kookt" aria-label="Wie kookt op ${KORT[n]}">
              <input type="number" min="5" step="5" data-pad="weekdagen.${n}.minuten" value="${esc(w.minuten || 45)}" aria-label="Max minuten ${KORT[n]}" title="Max minuten">
              <input type="number" min="0" data-pad="weekdagen.${n}.vriezer" value="${esc(w.vriezer || 0)}" aria-label="Extra porties vriezer ${KORT[n]}" title="Extra porties voor de vriezer">
              <input class="wdnotitie" data-pad="weekdagen.${n}.notitie" value="${esc(w.notitie || "")}" placeholder="Afspraak, bijv. iets makkelijks" aria-label="Afspraak ${KORT[n]}">
            </div>`
          )
          .join("")}
          <p class="hint">Kolommen: soort · wie kookt · max minuten · extra porties voor de vriezer · afspraak.</p></div>

        <h3>Kookstijl</h3>
        <label class="veld"><span>Apparaten (komma's)</span><input data-pad="stijl.apparaten" value="${esc((e.stijl?.apparaten || []).join(", "))}" placeholder="stoomoven, airfryer"></label>
        <label class="veld"><span>Voorkeuren</span><textarea data-pad="stijl.voorkeuren" rows="3" placeholder="Gezond, koolhydraatbewust…">${esc(e.stijl?.voorkeuren || "")}</textarea></label>

        <h3>Afwisseling</h3>
        <div class="rij3">
          <label class="veld smal"><span>Max vlees/week</span><input type="number" min="0" data-pad="afwisseling.vlees_max" value="${esc(e.afwisseling?.vlees_max ?? 3)}"></label>
          <label class="veld smal"><span>Min vis/week</span><input type="number" min="0" data-pad="afwisseling.vis_min" value="${esc(e.afwisseling?.vis_min ?? 1)}"></label>
          <label class="veld smal"><span>Herhalen na (weken)</span><input type="number" min="0" data-pad="afwisseling.herhaal_weken" value="${esc(e.afwisseling?.herhaal_weken ?? 3)}"></label>
        </div>
        <label class="check"><input type="checkbox" data-pad="afwisseling.vleesvervanger" ${e.afwisseling?.vleesvervanger ? "checked" : ""}> Bij vlees mag een vleesvervanger als vega-variant</label>

        <h3>Overige afspraken</h3>
        <label class="veld"><span>Vrije tekst voor uitzonderingen</span>
          <textarea data-pad="huisregels" rows="4">${esc(e.huisregels || "")}</textarea></label>

        <h3>Bronnen voor recepten</h3>
        <p class="hint">De koks, sites en boeken waar jullie recepten vandaan komen staan nu op een eigen scherm.</p>
        <button class="sec-btn" data-act="bronnen">📚 Bronnen beheren</button>

        <h3>Agenda en AI</h3>
        <label class="veld"><span>Agenda voor de maaltijden</span>
          <select data-pad="agenda"><option value="">— geen —</option>${this.d.agendas.map((a) => `<option ${a === e.agenda ? "selected" : ""}>${esc(a)}</option>`).join("")}</select></label>
        <div class="rij3">
          <label class="veld smal"><span>Tijd</span><input data-pad="tijd" type="time" value="${esc(e.tijd || "17:00")}"></label>
          <label class="veld smal"><span>Duur (min)</span><input data-pad="duur" type="number" min="5" value="${esc(e.duur || 60)}"></label>
        </div>
        <label class="veld"><span>AI-taak</span>
          <select data-pad="ai"><option value="">Automatisch (${esc(this.d.ai_taken[0] || "geen")})</option>${this.d.ai_taken.map((a) => `<option ${a === e.ai ? "selected" : ""}>${esc(a)}</option>`).join("")}</select></label>
      </form>
      <div class="plakbalk"><button class="sec-btn" data-act="terug">Annuleren</button><span class="vul"></span>
        <button class="primary" data-act="instopslaan">Opslaan</button></div>`;
  }

  _zetPad(pad, waarde) {
    const delen = pad.split(".");
    let o = this.inst;
    for (const k of delen.slice(0, -1)) o = o[/^\d+$/.test(k) ? Number(k) : k] ??= {};
    o[delen[delen.length - 1]] = waarde;
  }
  _leesPad(pad) {
    return pad.split(".").reduce((o, k) => (o == null ? o : o[/^\d+$/.test(k) ? Number(k) : k]), this.inst);
  }

  // ------------------------------------------------------------------ events
  _input(e) {
    if (e.target.classList?.contains("raanpas-wens") && this.raanpas) this.raanpas.wens = e.target.value;
    if (e.target.id === "ont-vraag" && this.ont) this.ont.vraag = e.target.value;
    if (e.target.id === "dag-wens" && this.dp?.sel) this._dagState(this.dp.sel).st.wens = e.target.value;
    if (e.target.id === "bn-url" && this.bronNieuw) this.bronNieuw.url = e.target.value;
    if (e.target.id === "bn-naam" && this.bronNieuw) this.bronNieuw.naam = e.target.value;
    if (e.target.id === "bn-notitie" && this.bronNieuw) this.bronNieuw.notitie = e.target.value;
    if (e.target.id === "zoek") {
      this.zoek = e.target.value;
      this._zoekFocus = true;
      this.shadowRoot.querySelector(".main").innerHTML = this._receptenLijst();
    }
  }

  async _change(e) {
    const t = e.target;
    if (t.id === "ont-bron" && this.ont) {
      this.ont.bron = t.value;
      return;
    }
    if (t.id === "imp-bron-foto") {
      this._fotoBron = t.value;
      return;
    }
    if (t.id === "bn-type" && this.bronNieuw) {
      this.bronNieuw = { type: t.value, url: "", naam: "", notitie: "", check: null };
      return this._render();
    }
    if (t.id === "imp-foto") {
      this._fotoFout = "";
      try {
        const files = [...t.files].slice(0, 4);
        this._fotos = await Promise.all(files.map((f) => this._fotoNaarData(f)));
        if (this._fotos.reduce((n, x) => n + x.length, 0) > 3_000_000) {
          this._fotos = [];
          this._fotoFout = "De foto's zijn samen te groot. Kies er minder, of fotografeer iets verder weg.";
        }
      } catch (err) {
        this._fotos = [];
        this._fotoFout = err.message || "Foto lezen mislukt.";
      }
      return this._render();
    }
    if (t.dataset.bronaan !== undefined) {
      const lijst = JSON.parse(JSON.stringify(this.d.instellingen.bronnen || []));
      lijst[Number(t.dataset.bronaan)].aan = t.checked;
      return this._bronnenOpslaan(lijst);
    }
    if (t.dataset.pad !== undefined && this.inst) {
      if (t.dataset.dag !== undefined) {
        const huidig = new Set(this._leesPad(t.dataset.pad) || []);
        if (t.checked) huidig.add(Number(t.dataset.dag));
        else huidig.delete(Number(t.dataset.dag));
        this._zetPad(t.dataset.pad, [...huidig].sort());
      } else if (t.type === "checkbox") this._zetPad(t.dataset.pad, t.checked);
      else if (t.type === "number") this._zetPad(t.dataset.pad, t.value === "" ? 0 : Number(t.value));
      else if (t.dataset.pad === "stijl.apparaten") this._zetPad(t.dataset.pad, t.value.split(",").map((x) => x.trim()).filter(Boolean));
      else this._zetPad(t.dataset.pad, t.value);
      if (t.tagName === "SELECT" && /patroon\.soort$/.test(t.dataset.pad)) this._render();
      return;
    }
    if (t.dataset.anker !== undefined && this.inst) {
      const ma = maandag(new Date());
      const pat = (this.inst.profielen[Number(t.dataset.anker)].patroon ||= {});
      pat.anker = iso(t.value === "A" ? ma : plusDagen(ma, -7));
      return;
    }
    if (t.dataset.pl !== undefined && this.plan) {
      if (t.dataset.pl === "wens") {
        this.plan.wens = t.value;
        return;
      }
      const van = t.dataset.pl === "van" ? t.value : this.plan.van;
      let tot = t.dataset.pl === "tot" ? t.value : this.plan.tot;
      if (!van || !tot) return;
      if (tot < van) tot = van;
      return this._planLaad(van, tot);
    }
    if (t.dataset.pd !== undefined && this.plan) {
      const d = this._planDag(t.dataset.datum);
      if (!d) return;
      if (t.dataset.pd === "aan") {
        d.aan = t.checked;
        return this._render();
      }
      d[t.dataset.pd] = t.type === "number" ? Number(t.value) : t.value;
      return;
    }
    if (t.dataset.bn !== undefined && this.bn) {
      this.bn.rijen[Number(t.dataset.bn)].sel = t.checked;
      return this._render();
    }
    if (t.dataset.bnlos !== undefined && this.bn) {
      this.bn.los[Number(t.dataset.bnlos)].sel = t.checked;
      return this._render();
    }
    if (t.dataset.bnlijst !== undefined && this.bn) {
      this.bn.los[Number(t.dataset.bnlijst)].list_id = Number(t.value);
      return;
    }
    const vs = t.dataset.vs;
    if (vs) {
      const datum = t.closest("[data-datum]").dataset.datum;
      let waarde = t.type === "checkbox" ? t.checked : t.value;
      if (vs === "porties" || vs === "vriezer") waarde = t.value === "" ? null : Number(t.value);
      await this._do({ type: "boodschappen/eten_voorstel_wijzig", datum, velden: { [vs]: waarde } });
    }
    if (t.classList.contains("iprod")) {
      const p = (this.d.producten || []).find((x) => norm(x.name) === norm(t.value));
      t.classList.toggle("onbekend", !!t.value && !p);
    }
  }

  async _click(e) {
    const tabBtn = e.target.closest("[data-tab]");
    if (tabBtn && !e.target.closest("[data-act]")) {
      this.tab = tabBtn.dataset.tab;
      this.view = null;
      this._render();
      return;
    }
    const b = e.target.closest("[data-act]");
    if (!b || b.disabled) return;
    const a = b.dataset.act;
    this.melding = "";
    const root = this.shadowRoot;
    switch (a) {
      case "week":
        this.week = plusDagen(this.week, 7 * Number(b.dataset.n));
        this.edit = null;
        return this._render();
      case "naarvoorstel":
        this.view = { soort: "voorstel" };
        this._scrollTop = true;
        return this._render();
      case "dagstart":
        return this._dagStart(b.dataset.datum || null);
      case "dagschuif": {
        const van = iso(plusDagen(vanIso(this.dp.van), Number(b.dataset.n)));
        const vandaag = iso(new Date());
        this.dp.sel = null;
        return this._dagLaad(van < vandaag ? vandaag : van);
      }
      case "dagkies":
        if (this.live?.bezig) return;
        this.dp.sel = b.dataset.datum;
        if (this.live && this.live.datum !== b.dataset.datum) this.live = null;
        this._scrollTop = true;
        return this._render();
      case "dagsoort":
        this._dagState(this.dp.sel).st.soort = b.dataset.soort;
        return this._render();
      case "dagpers": {
        const st = this._dagState(this.dp.sel).st;
        const n = b.dataset.naam;
        st.aanwezig = st.aanwezig.includes(n) ? st.aanwezig.filter((x) => x !== n) : [...st.aanwezig, n];
        return this._render();
      }
      case "dagtog":
        this.dp.filters[b.dataset.k] = !this.dp.filters[b.dataset.k];
        return this._render();
      case "dagbron": {
        const k = b.dataset.k;
        const f = this.dp.filters;
        f.bronnen = f.bronnen.includes(k) ? f.bronnen.filter((x) => x !== k) : [...f.bronnen, k];
        return this._render();
      }
      case "dagvoorstel":
        return this._dagVoorstel();
      case "daginplan": {
        const o = ((this.d.dagopties || {})[this.dp.sel]?.opties || [])[Number(b.dataset.i)];
        if (!o) return;
        return this._dagInplannen(o.soort === "link" ? { soort: "link", url: o.url, bron: o.bron } : { soort: "recept", recept_id: o.recept_id });
      }
      case "dagvast":
        return this._dagInplannen({ soort: this._dagState(this.dp.sel).st.soort });
      case "dagopnieuw":
        delete this.dp.res[this.dp.sel];
        this.live = null;
        return this._render();
      case "dagrecept":
        this._terugNaar = { soort: "dag" };
        this.view = { soort: "recept", id: b.dataset.id };
        this._scrollTop = true;
        return this._render();
      case "meekijkklap":
        e.preventDefault();
        if (this.live) this.live.open = !b.closest("details").open;
        if (this.live && !this.live.bezig) this._meekijkBijwerken();
        else b.closest("details").open = !b.closest("details").open;
        return;
      case "planstart": {
        const vandaag = iso(new Date());
        const ma = iso(this.week);
        const van = ma > vandaag ? ma : vandaag;
        const tot = iso(plusDagen(vanIso(van), 6)); // standaard 7 dagen; in de keuzestap vrij aan te passen
        this.plan = null;
        this.view = { soort: "plannen" };
        this._scrollTop = true;
        this._render();
        return this._planLaad(van, tot);
      }
      case "pdsoort": {
        const d = this._planDag(b.dataset.datum);
        if (d) d.soort = b.dataset.soort;
        return this._render();
      }
      case "pdpers": {
        const d = this._planDag(b.dataset.datum);
        if (d) {
          const n = b.dataset.naam;
          d.aanwezig = d.aanwezig.includes(n) ? d.aanwezig.filter((x) => x !== n) : [...d.aanwezig, n];
        }
        return this._render();
      }
      case "planmaak":
        return this._planMaak();
      case "kies":
        return this._do({ type: "boodschappen/eten_voorstel_wijzig", datum: b.dataset.datum, velden: { keuze: Number(b.dataset.i) } });
      case "vgeen":
        return this._do({ type: "boodschappen/eten_voorstel_wijzig", datum: b.dataset.datum, velden: { geen: b.dataset.aan === "1" } });
      case "voverslaan":
        return this._do({ type: "boodschappen/eten_voorstel_wijzig", datum: b.dataset.datum, velden: { overslaan: b.dataset.aan === "1" } });
      case "optiewens":
        this._optieWens = b.dataset.datum;
        this._render();
        root.querySelector(".optie-wens")?.focus();
        return;
      case "optiewensdicht":
        this._optieWens = null;
        return this._render();
      case "optienieuw": {
        const wens = root.querySelector(".optie-wens")?.value.trim() || "";
        this._optieWens = null;
        this.bezigDag = b.dataset.datum;
        return this._do({ type: "boodschappen/eten_voorstel_dag", datum: b.dataset.datum, wens }, `Gemini bedenkt nieuwe opties voor ${dagNaam(b.dataset.datum)}…`);
      }
      case "fb": {
        const aan = b.getAttribute("aria-pressed") !== "true";
        return this._do({ type: "boodschappen/eten_feedback", recept_id: b.dataset.rid || null, titel: b.dataset.titel || null,
          soort: b.dataset.soort, aan });
      }
      case "voorstelweg":
        if (b.dataset.zeker !== "1") {
          b.dataset.zeker = "1";
          b.textContent = "Zeker weggooien?";
          return;
        }
        await this._do({ type: "boodschappen/eten_voorstel_weg" });
        this.view = null;
        return this._render();
      case "goedkeuren": {
        const res = await this._do({ type: "boodschappen/eten_goedkeuren" }, "In de agenda zetten…");
        if (res && typeof res === "object") {
          const m = `${res.gepland} ${res.gepland === 1 ? "maaltijd" : "maaltijden"} in de agenda gezet.`;
          return this._openBenodigd(res.plan_ids, m);
        }
        return;
      }
      case "bnstap": {
        const r = this.bn.rijen[Number(b.dataset.i)];
        r.aantal = Math.max(1, r.aantal + Number(b.dataset.n));
        r.sel = true;
        return this._render();
      }
      case "genoeg":
        this.bn.genoegOpen = !this.bn.genoegOpen;
        return this._render();
      case "bnoverslaan":
        this.melding = this.bn?.melding ? `${this.bn.melding} Niets op de boodschappenlijst gezet.` : "Niets op de boodschappenlijst gezet.";
        this.bn = null;
        this.view = null;
        this._scrollTop = true;
        return this._render();
      case "bntoevoegen":
        return this._bnToevoegen();
      case "persnieuw":
        this.inst.profielen = [...(this.inst.profielen || []), { naam: "", patroon: { soort: "altijd", dagen: [0, 1, 2, 3, 4, 5, 6] } }];
        return this._render();
      case "persweg":
        this.inst.profielen.splice(Number(b.dataset.i), 1);
        return this._render();
      case "plan":
        this.edit = b.dataset.datum;
        return this._render();
      case "aanpasopen": {
        this.aanpas = b.dataset.datum;
        this._aanpasWens = b.dataset.wens || "";
        this._render();
        const inp = root.querySelector(".aanpas-wens");
        if (inp) {
          if (this._aanpasWens) inp.value = this._aanpasWens;
          inp.focus();
          inp.addEventListener("keydown", (ev) => {
            if (ev.key === "Enter") root.querySelector("[data-act=aanpasdoe]")?.click();
          });
        }
        return;
      }
      case "aanpasdicht":
        this.aanpas = null;
        return this._render();
      case "aanpasdoe": {
        const wens = root.querySelector(".aanpas-wens")?.value.trim() || "";
        if (!wens) {
          this.err = "Vertel Gemini wat je wilt, bijvoorbeeld 'een ovenschotel'.";
          return this._render();
        }
        this.aanpas = null;
        this.bezigDag = b.dataset.datum;
        return this._do({ type: "boodschappen/eten_dag_aanpassen", datum: b.dataset.datum, wens, plan_id: b.dataset.plan || null },
          `Gemini zoekt iets voor ${dagNaam(b.dataset.datum)}…`);
      }
      case "sugtoepassen": {
        this.bezigDag = b.dataset.datum;
        const res = await this._do({ type: "boodschappen/eten_suggestie_toepassen", datum: b.dataset.datum }, "Agenda bijwerken…");
        if (res && typeof res === "object") return this._openBenodigd(res.plan_ids, "Aangepast in de agenda.");
        return;
      }
      case "sugweg":
        return this._do({ type: "boodschappen/eten_suggestie_weg", datum: b.dataset.datum });
      case "geenmaaltijd": {
        this.bezigDag = b.dataset.datum;
        const velden = { geen: true, titel: "Geen maaltijd", boodschappen: false };
        if (b.dataset.uid)
          return this._do({ type: "boodschappen/eten_overnemen", datum: b.dataset.datum, uid: b.dataset.uid, recept_id: null,
            titel: "Geen maaltijd", velden }, "In de agenda zetten…");
        return this._do({ type: "boodschappen/eten_plan", datum: b.dataset.datum, plan_id: null, velden }, "In de agenda zetten…");
      }
      case "planannuleer":
        this.edit = null;
        return this._render();
      case "planopslaan": {
        const f = b.closest("[data-plan]");
        const val = (n) => f.querySelector(`[name=${n}]`);
        const velden = {
          recept_id: val("recept_id").value || null,
          titel: val("titel").value.trim() || null,
          kookt: val("kookt").value.trim(),
          porties: val("porties").value ? Number(val("porties").value) : null,
          vriezer: Number(val("vriezer").value || 0),
          boodschappen: val("boodschappen").checked,
          lijst: false,
          geen: val("geen").checked,
        };
        if (velden.geen) Object.assign(velden, { recept_id: null, titel: "Geen maaltijd", boodschappen: false, lijst: false });
        if (!velden.recept_id && !velden.titel) {
          this.err = "Kies een recept of vul een titel in.";
          return this._render();
        }
        this.edit = null;
        this.bezigDag = b.dataset.datum;
        const planId =
          b.dataset.uid !== undefined && !b.dataset.id
            ? await this._do({ type: "boodschappen/eten_overnemen", datum: b.dataset.datum, uid: b.dataset.uid || null,
                recept_id: velden.recept_id, titel: velden.titel, velden }, "Agenda bijwerken…")
            : await this._do({ type: "boodschappen/eten_plan", datum: b.dataset.datum, plan_id: b.dataset.id || null, velden }, "Agenda bijwerken…");
        const item = typeof planId === "string" ? this.d.planning.find((x) => x.id === planId) : null;
        if (item && velden.boodschappen && velden.recept_id && !item.op_lijst) return this._openBenodigd([planId], "Opgeslagen in de agenda.");
        return;
      }
      case "planweg":
        this.edit = null;
        return this._do({ type: "boodschappen/eten_plan_weg", plan_id: b.dataset.id }, "Uit de agenda halen…");
      case "beoordeelopen":
        this._beoordeelOpen = b.dataset.id || "__dicht__";
        return this._render();
      case "score": {
        const p = this.d.planning.find((x) => x.id === b.dataset.id);
        const huidig = this.d.beoordelingen.find((x) => x.plan_id === b.dataset.id && x.persoon === b.dataset.persoon);
        const score = huidig && huidig.score === Number(b.dataset.score) ? null : Number(b.dataset.score);
        this._beoordeelOpen = p?.id;
        return this._do({ type: "boodschappen/eten_beoordeel", plan_id: b.dataset.id, persoon: b.dataset.persoon, score });
      }
      case "filter":
        this.filter = b.dataset.f;
        return this._render();
      case "open":
        this._terugNaar = null;
        this.view = { soort: "recept", id: b.dataset.id };
        this.porties = null;
        this._scrollTop = true;
        return this._render();
      case "terug":
        if (this.view?.soort === "instellingen") this.inst = null;
        if (this.view?.soort === "plannen") this.plan = null;
        if (this.view?.soort === "dag") {
          this._liveAfbreken();
          this.dp = null;
          this.live = null;
        }
        this._fotos = null;
        this._fotoFout = "";
        this.raanpas = null;
        this.bronEdit = null;
        if (["bronnen", "ontdek", "nieuw", "recept"].includes(this.view?.soort) && this._terugNaar) {
          this.bronEdit = null;
          this.view = this._terugNaar;
          this._terugNaar = null;
        } else this.view = this.view?.soort === "bewerk" && this.view.id ? { soort: "recept", id: this.view.id } : null;
        this._scrollTop = true;
        return this._render();
      case "porties": {
        const r = this.recept(this.view.id);
        this.porties = Math.max(1, (this.porties || r.porties || 4) + Number(b.dataset.n));
        return this._render();
      }
      case "koken":
        this.view = { soort: "koken", id: b.dataset.id };
        this.stap = 0;
        this._scrollTop = true;
        this._wakeAan();
        return this._render();
      case "stap":
        this.stap += Number(b.dataset.n);
        this._scrollTop = true;
        return this._render();
      case "stopkoken":
        this._wakeLos();
        this.view = { soort: "recept", id: this.view.id };
        return this._render();
      case "bewerk":
        this.view = { soort: "bewerk", id: b.dataset.id };
        this._scrollTop = true;
        return this._render();
      case "nieuw":
        this._fotoBron = b.dataset.bron || "";
        this._fotos = null;
        this._fotoFout = "";
        this._terugNaar = this.view?.soort === "bronnen" ? this.view : null;
        this.view = { soort: "nieuw" };
        this._scrollTop = true;
        return this._render();
      case "bronnen":
        this._terugNaar = this.view && this.view.soort !== "bronnen" ? this.view : this._terugNaar;
        this.view = { soort: "bronnen" };
        this.bronEdit = null;
        this._scrollTop = true;
        return this._render();
      case "bronbewerk":
        this.bronEdit = Number(b.dataset.i);
        return this._render();
      case "bronbewerkdicht":
        this.bronEdit = null;
        return this._render();
      case "bronopslaan": {
        const i = Number(b.dataset.i);
        const kaart = b.closest("[data-i]");
        const lijst = JSON.parse(JSON.stringify(this.d.instellingen.bronnen || []));
        const bron = lijst[i];
        const naam = kaart.querySelector(".be-naam").value.trim();
        if (!naam) {
          this.err = "Geef de bron een naam.";
          return this._render();
        }
        bron.oud = bron.naam;
        bron.naam = naam;
        if (bron.type === "website") {
          const adres = kaart.querySelector(".be-adres").value.trim();
          if (!adres) {
            this.err = "Een website heeft een adres nodig.";
            return this._render();
          }
          bron.domein = adres;
          delete bron.pad;
        }
        bron.notitie = kaart.querySelector(".be-notitie").value.trim();
        this.bronEdit = null;
        return this._bronnenOpslaan(lijst);
      }
      case "bronweg": {
        const i = Number(b.dataset.i);
        const bron = (this.d.instellingen.bronnen || [])[i];
        const aantal = (this.d.bronnen_info?.[bron.naam] || {}).recepten || 0;
        if (b.dataset.zeker !== "1") {
          b.dataset.zeker = "1";
          b.textContent = aantal ? `Zeker? ${aantal} recepten verliezen hun herkomst` : "Zeker weggooien?";
          return;
        }
        const lijst = JSON.parse(JSON.stringify(this.d.instellingen.bronnen || []));
        lijst.splice(i, 1);
        return this._bronnenOpslaan(lijst, "Bron verwijderen…");
      }
      case "broncheck": {
        const n = this.bronNieuw;
        const url = (root.querySelector("#bn-url")?.value || "").trim();
        if (!url) {
          this.err = "Plak eerst het adres van de site.";
          return this._render();
        }
        n.url = url;
        n.check = null;
        const res = await this._do({ type: "boodschappen/eten_bron_check", url }, "Site bekijken (even geduld, dit kan een halve minuut duren)…");
        if (res && typeof res === "object") {
          n.check = res;
          if (!n.naam) n.naam = res.naam;
        }
        return this._render();
      }
      case "bronnieuw": {
        const n = this.bronNieuw;
        const naam = (root.querySelector("#bn-naam")?.value || "").trim();
        const notitie = (root.querySelector("#bn-notitie")?.value || "").trim();
        const lijst = JSON.parse(JSON.stringify(this.d.instellingen.bronnen || []));
        const nieuw = { naam, type: n.type, notitie, aan: true };
        if (n.type === "website") {
          const c = n.check;
          const url = (root.querySelector("#bn-url")?.value || "").trim();
          if (!url) {
            this.err = "Plak het adres van de site.";
            return this._render();
          }
          nieuw.domein = c ? c.domein : url;
          nieuw.pad = c ? c.pad : "";
        }
        if (!naam) {
          this.err = "Geef de bron een naam.";
          return this._render();
        }
        if (lijst.some((x) => x.naam.toLowerCase() === naam.toLowerCase())) {
          this.err = "Er is al een bron met die naam.";
          return this._render();
        }
        lijst.push(nieuw);
        this.bronNieuw = null;
        await this._bronnenOpslaan(lijst, "Bron toevoegen…");
        if (!this.err) this.melding = `${naam} is toegevoegd.`;
        return this._render();
      }
      case "ontdek":
        this._terugNaar = this.view && this.view.soort !== "ontdek" ? this.view : this._terugNaar;
        this.ont = { bron: b.dataset.bron || "", vraag: b.dataset.vraag || "", res: null, nota: "" };
        this.view = { soort: "ontdek" };
        this._scrollTop = true;
        this._render();
        if (this.ont.vraag) return this._ontdekZoek(false);
        return;
      case "ontzoek":
        return this._ontdekZoek(false);
      case "ontai":
        return this._ontdekZoek(true);
      case "ontvoeg": {
        const x = this.ont.res[Number(b.dataset.i)];
        const id = await this._do({ type: "boodschappen/eten_import", url: x.url }, `${x.titel} ophalen…`);
        if (typeof id === "string") x.toegevoegd = id;
        else x.fout = this.err || "Dit lukte niet.";
        this.err = "";
        return this._render();
      }
      case "rbronzet": {
        const bron = root.querySelector("#rbron")?.value;
        return this._do({ type: "boodschappen/eten_recept_bron", recept_id: b.dataset.id, bron_naam: bron });
      }
      case "reigen":
        return this._do({ type: "boodschappen/eten_recept_bron", recept_id: b.dataset.id, bron_naam: null, eigen: true });
      case "raanpas":
        this.raanpas = { id: b.dataset.id, wens: "" };
        this._render();
        root.querySelector(".raanpas-wens")?.focus();
        return;
      case "raanpasdicht":
        this.raanpas = null;
        return this._render();
      case "raanpassnel": {
        const veld = root.querySelector(".raanpas-wens");
        const huidig = (veld?.value || "").trim();
        const t = b.dataset.t;
        this.raanpas.wens = huidig ? `${huidig.replace(/[.,;]\s*$/, "")}, ${t.toLowerCase()}` : t;
        this._render();
        const nieuw = root.querySelector(".raanpas-wens");
        nieuw?.focus();
        nieuw?.setSelectionRange(nieuw.value.length, nieuw.value.length);
        return;
      }
      case "raanpasdoe": {
        const wens = (root.querySelector(".raanpas-wens")?.value || "").trim();
        if (!wens) {
          this.err = "Vertel Gemini wat er anders moet, bijvoorbeeld 'maak het vegetarisch'.";
          return this._render();
        }
        this.raanpas = null;
        await this._do({ type: "boodschappen/eten_aanpassen", recept_id: b.dataset.id, wens }, "Gemini past het recept aan (dit duurt even)…");
        this._scrollTop = !this.err;
        return this._render();
      }
      case "raanpasnogmaals": {
        const p = this.d.aanpassingen?.[b.dataset.id];
        this.raanpas = { id: b.dataset.id, wens: p?.wens || "" };
        return this._render();
      }
      case "aanpasweg":
        return this._do({ type: "boodschappen/eten_aanpassing_weg", recept_id: b.dataset.id });
      case "aanpastoe": {
        if (b.dataset.modus === "vervang" && b.dataset.zeker !== "1") {
          b.dataset.zeker = "1";
          b.textContent = "Zeker? Het origineel wordt vervangen";
          return;
        }
        const id = await this._do({ type: "boodschappen/eten_aanpassing_toepassen", recept_id: b.dataset.id, modus: b.dataset.modus }, "Opslaan…");
        if (typeof id === "string") {
          this.view = { soort: "recept", id };
          this.melding = b.dataset.modus === "variant" ? "Opgeslagen als nieuwe variant. Het origineel is ongewijzigd." : "Recept aangepast. Je kunt het ongedaan maken onder aan de knoppen.";
          this._scrollTop = true;
          this._render();
        }
        return;
      }
      case "aanpasongedaan":
        return this._do({ type: "boodschappen/eten_aanpassing_ongedaan", recept_id: b.dataset.id }, "Vorige versie terugzetten…");
      case "handmatig":
        this._concept = { naam: "", porties: 4, ingredienten: [], stappen: [] };
        this.view = { soort: "bewerk", id: null };
        return this._render();
      case "ingrrij": {
        const box = root.querySelector(".ingrrijen");
        const eerste = box.querySelector(".irij");
        const nieuw = eerste.cloneNode(true);
        nieuw.querySelectorAll("input").forEach((x) => {
          if (x.type === "checkbox") x.checked = false;
          else x.value = x.classList.contains("iaantal") ? "1" : "";
        });
        box.appendChild(nieuw);
        nieuw.querySelector(".itekst").focus();
        return;
      }
      case "receptopslaan": {
        const f = root.querySelector("form.bewerk");
        const v = (n) => f.querySelector(`[name=${n}]`);
        const ingredienten = [...f.querySelectorAll(".irij")]
          .map((rij) => {
            const naam = rij.querySelector(".iprod").value;
            const p = (this.d.producten || []).find((x) => norm(x.name) === norm(naam));
            return {
              tekst: rij.querySelector(".itekst").value.trim(),
              product_id: p ? p.id : null,
              aantal: Number(rij.querySelector(".iaantal").value || 1),
              basis: rij.querySelector(".ibasis").checked,
            };
          })
          .filter((i) => i.tekst);
        const recept = {
          id: b.dataset.id || undefined,
          naam: v("naam").value.trim(),
          porties: Number(v("porties").value || 4),
          tijd: v("tijd").value ? Number(v("tijd").value) : null,
          vega: v("vega").checked,
          vega_gezet: true,
          tags: v("tags").value.split(",").map((x) => x.trim()).filter(Boolean),
          ingredienten,
          stappen: v("stappen").value.split("\n").map((x) => x.trim()).filter(Boolean),
          notitie: v("notitie").value.trim(),
          bron: v("bron").value.trim() || null,
          eigen: v("eigen").checked,
        };
        if (!recept.naam) {
          this.err = "Geef het recept een naam.";
          return this._render();
        }
        const id = await this._do({ type: "boodschappen/eten_recept", recept });
        if (typeof id === "string") {
          this.view = { soort: "recept", id };
          this._scrollTop = true;
          this._render();
        }
        return;
      }
      case "receptweg": {
        const r = this.recept(b.dataset.id);
        if (b.dataset.zeker !== "1") {
          b.dataset.zeker = "1";
          b.textContent = `Zeker? ${r ? r.naam : ""} verwijderen`;
          return;
        }
        this.view = null;
        return this._do({ type: "boodschappen/eten_recept_weg", recept_id: b.dataset.id });
      }
      case "koppel":
        await this._do({ type: "boodschappen/eten_koppel", recept_id: b.dataset.id }, "Ingrediënten koppelen…");
        this.view = { soort: "bewerk", id: b.dataset.id };
        return this._render();
      case "importeer": {
        const url = root.querySelector("#imp-url").value.trim();
        const tekst = root.querySelector("#imp-tekst").value.trim();
        let msg;
        if (b.dataset.bron === "foto") {
          msg = { url: null, tekst: null, fotos: this._fotos || [], bron_naam: root.querySelector("#imp-bron-foto")?.value || null };
        } else if (b.dataset.bron === "url") msg = { url, tekst: null };
        else msg = { url: null, tekst, bron_naam: root.querySelector("#imp-bron-tekst")?.value || null };
        if (!(msg.url || msg.tekst || (msg.fotos || []).length)) {
          this.err = b.dataset.bron === "url" ? "Plak eerst een link." : b.dataset.bron === "foto" ? "Kies eerst een foto." : "Plak eerst een recept.";
          return this._render();
        }
        const id = await this._do({ type: "boodschappen/eten_import", ...msg },
          b.dataset.bron === "foto" ? "Gemini leest de foto (dit duurt even)…" : "Recept ophalen en omzetten…");
        if (typeof id === "string") {
          this._fotos = null;
          this.view = { soort: "bewerk", id };
          this.melding = "Recept omgezet. Controleer het even en sla op.";
          this._scrollTop = true;
          this._render();
        }
        return;
      }
      case "instellingen":
        this.inst = null;
        this.view = { soort: "instellingen" };
        this._scrollTop = true;
        return this._render();
      case "instopslaan": {
        // waarden die nog focus hebben (zonder change-event) ook meenemen
        root.querySelectorAll("form.bewerk [data-pad]").forEach((x) => {
          if (x.type !== "checkbox" && x.dataset.dag === undefined) x.dispatchEvent(new Event("change", { bubbles: true }));
        });
        const e = this.inst;
        e.profielen = (e.profielen || []).filter((p) => (p.naam || "").trim());
        await this._do({ type: "boodschappen/eten_instellingen", instellingen: {
          agenda: e.agenda || null, tijd: e.tijd || "17:00", duur: Number(e.duur || 60), huisregels: e.huisregels || "",
          ai: e.ai || null, profielen: e.profielen, weekdagen: e.weekdagen, stijl: e.stijl, afwisseling: e.afwisseling } });
        if (!this.err) {
          this.inst = null;
          this.view = null;
          this.melding = "Instellingen opgeslagen.";
          this._render();
        }
        return;
      }
    }
  }

  async _ontdekZoek(ai) {
    const root = this.shadowRoot;
    const o = this.ont;
    o.bron = root.querySelector("#ont-bron")?.value ?? o.bron;
    o.vraag = (root.querySelector("#ont-vraag")?.value ?? o.vraag).trim();
    o.res = null;
    o.nota = "";
    const res = await this._do({ type: "boodschappen/eten_bron_zoek", bron: o.bron || null, vraag: o.vraag, ai },
      ai ? "Gemini kiest recepten (dit duurt even)…" : "Zoeken in de bronnen (de eerste keer duurt het even)…");
    if (res && typeof res === "object") {
      o.res = res.resultaten || [];
      o.nota = res.nota || "";
    }
    this._scrollTop = false;
    return this._render();
  }

  async _wakeAan() {
    try {
      if ("wakeLock" in navigator) this._wake = await navigator.wakeLock.request("screen");
    } catch (_) {
      /* niet ondersteund: geen probleem */
    }
  }
  _wakeLos() {
    try {
      this._wake?.release();
    } catch (_) {}
    this._wake = null;
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
}
* { box-sizing: border-box; }
button { font: inherit; color: inherit; background: none; border: 0; cursor: pointer; -webkit-tap-highlight-color: transparent; }
button:disabled { opacity: .4; cursor: default; }
button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
header { display: flex; align-items: center; gap: 4px; height: var(--header-height, 56px); padding: 0 8px 0 4px;
  background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff); flex: none; }
header h1 { font-size: 20px; font-weight: 400; margin: 0 0 0 8px; flex: 1; }
.hbtn { width: 44px; height: 44px; font-size: 22px; border-radius: 50%; }
.tabs { display: flex; gap: 4px; padding: 8px 12px 0; background: var(--card-background-color); border-bottom: 1px solid var(--rand); flex: none; }
.tabs button { position: relative; padding: 12px 14px 13px; display: flex; align-items: center; gap: 8px; color: var(--secondary-text-color); font-weight: 500; }
.tabs button.on { color: var(--primary-text-color); }
.tabs button.on::after { content: ""; position: absolute; left: 10px; right: 10px; bottom: -1px; height: 3px; border-radius: 3px 3px 0 0; background: var(--acc); }
.tabs b { font-size: 12px; min-width: 20px; height: 20px; line-height: 20px; padding: 0 6px; border-radius: 10px; background: var(--zacht); font-weight: 600; }
.tabs b:empty { display: none; }
.bar { position: relative; flex: none; background: var(--card-background-color); padding: 10px 12px; border-bottom: 1px solid var(--rand); }
.bar:empty { display: none; }
.bar.busy::after { content: ""; position: absolute; left: 0; bottom: -1px; height: 2px; width: 30%; background: var(--acc); animation: laden 1s ease-in-out infinite; }
@keyframes laden { from { left: -30%; } to { left: 100%; } }
@media (prefers-reduced-motion: reduce) { .bar.busy::after { animation: none; width: 100%; opacity: .5; } }
.bezig { margin: 6px 2px 0; color: var(--secondary-text-color); font-size: 14px; }
.main { flex: 1; overflow-y: auto; padding: 10px 12px 96px; -webkit-overflow-scrolling: touch; }
.fout { background: color-mix(in srgb, var(--error-color, #c00) 15%, transparent); padding: 10px 12px; border-radius: 10px; }
.melding { background: color-mix(in srgb, var(--success-color, #2e7d32) 15%, transparent); padding: 10px 12px; border-radius: 10px; }
.leeg { color: var(--secondary-text-color); padding: 12px 4px 0; }
.leeg p { margin: 0 0 6px; }
.hint { font-size: 13px; color: var(--secondary-text-color); margin: 4px 2px 8px; }
.zacht { color: var(--secondary-text-color); }
.link { color: var(--primary-color); font-size: 14px; padding: 6px 2px; }
.knoppen { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 10px; }
.vul { flex: 1; }
.primary { min-height: 44px; padding: 0 18px; border-radius: 22px; background: var(--acc); color: var(--text-primary-color, #fff); font-weight: 500; }
.sec-btn { min-height: 40px; padding: 0 14px; border-radius: 20px; border: 1px solid var(--rand); background: var(--card-background-color); font-size: 14px; }
.wis { min-height: 40px; padding: 0 14px; border-radius: 20px; font-size: 14px; color: var(--error-color, #c62828); border: 1px solid color-mix(in srgb, var(--error-color, #c62828) 40%, transparent); }
.groot { min-height: 56px; font-size: 18px; padding: 0 24px; border-radius: 28px; }

/* week */
.weeknav { display: flex; align-items: center; gap: 8px; }
.rond { width: 44px; height: 44px; border-radius: 50%; font-size: 26px; line-height: 1; background: var(--zacht); }
.weektitel { flex: 1; text-align: center; display: grid; }
.weektitel small { color: var(--secondary-text-color); font-size: 13px; }
.voorstelbalk { display: flex; gap: 8px; margin-top: 10px; }
.voorstelbalk input { flex: 1; min-width: 0; height: 44px; padding: 0 12px; border-radius: 22px; border: 1px solid var(--rand);
  background: var(--primary-background-color); color: var(--primary-text-color); font-size: 15px; }
.voorstelkop { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; padding: 12px;
  border-radius: 14px; background: color-mix(in srgb, var(--acc) 12%, var(--card-background-color)); margin-bottom: 10px; }
.voorstelkop > div:first-child { display: grid; }
.voorstelkop small { color: var(--secondary-text-color); }
.voorstelkop .knoppen { margin: 0; }
.dagen { list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }
.dag { background: var(--card-background-color); border-radius: 14px; box-shadow: 0 0 0 1px var(--rand) inset; padding: 10px 12px; }
.dag.vandaag { box-shadow: 0 0 0 2px var(--acc) inset; }
.dag.voorbij { opacity: .85; }
.dag.voorstel { border-left: 4px solid var(--acc); }
.dagkop { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.dn { font-weight: 600; text-transform: capitalize; }
.dd { color: var(--secondary-text-color); font-size: 13px; }
.maaltijd { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mtitel { flex: 1; min-width: 180px; text-align: left; display: grid; gap: 2px; padding: 4px 0; }
.mtitel .t, .extern .t, .rkaart .t { font-size: 17px; line-height: 1.25; }
.mtitel small, .extern small { color: var(--secondary-text-color); font-size: 13px; }
.mknoppen { display: flex; gap: 6px; }
.extern { display: flex; align-items: center; gap: 8px; justify-content: space-between; padding: 4px 0; }
.extern > div { display: grid; }
.leegdag { width: 100%; text-align: left; padding: 10px 0; color: var(--primary-color); }
.vform { display: grid; gap: 8px; }
.vform.uit { opacity: .5; }
.reden { margin: 0; font-size: 13px; color: var(--secondary-text-color); font-style: italic; }
.rij3 { display: flex; gap: 8px; flex-wrap: wrap; align-items: end; }
.rij3 .veld { flex: 1; min-width: 120px; }
.rij3 .veld.smal { flex: 0 1 100px; min-width: 80px; }
.veld { display: grid; gap: 4px; font-size: 13px; color: var(--secondary-text-color); }
.veld input, .veld select, .veld textarea { min-height: 44px; padding: 8px 12px; border-radius: 10px; border: 1px solid var(--rand); font-size: 16px;
  background: var(--primary-background-color); color: var(--primary-text-color); font-family: inherit; width: 100%; }
.veld textarea { line-height: 1.4; resize: vertical; }
.check { display: inline-flex; align-items: center; gap: 8px; min-height: 44px; font-size: 15px; }
.check input { width: 20px; height: 20px; }
.check.klein { font-size: 13px; min-height: 0; color: var(--secondary-text-color); white-space: nowrap; }
.check.klein input { width: 18px; height: 18px; }

/* laden */
.spinner { flex: none; width: 20px; height: 20px; border-radius: 50%; border: 3px solid color-mix(in srgb, var(--acc) 25%, transparent);
  border-top-color: var(--acc); animation: draai .8s linear infinite; }
@keyframes draai { to { transform: rotate(360deg); } }
.laadblok { display: flex; align-items: center; gap: 12px; padding: 18px 4px; color: var(--secondary-text-color); font-size: 15px; }
.status { position: absolute; left: 50%; bottom: 20px; transform: translate(-50%, 20px); opacity: 0; pointer-events: none;
  display: flex; align-items: center; gap: 10px; padding: 12px 18px; border-radius: 24px; max-width: calc(100% - 32px);
  background: var(--card-background-color); color: var(--primary-text-color); box-shadow: 0 4px 16px rgba(0,0,0,.25);
  font-size: 15px; transition: opacity .15s, transform .15s; }
.status.aan { opacity: 1; transform: translate(-50%, 0); }
@media (prefers-reduced-motion: reduce) { .spinner { animation-duration: 2.4s; } .status { transition: none; } }
/* geen maaltijd */
.maaltijd.geen .t, .geentitel { color: var(--secondary-text-color); font-style: italic; }
.geentitel { margin: 4px 0; font-size: 17px; }
.geentitel small { font-style: normal; font-size: 13px; margin-left: 6px; }
.leegrij { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.leegrij .leegdag { width: auto; }

/* aanpassen met Gemini */
.ai-btn { justify-self: start; margin-top: 4px; padding: 8px 2px; color: var(--primary-color); font-size: 14px; font-weight: 500; text-align: left; }
.aanpas, .suggestie { display: grid; gap: 6px; margin-top: 8px; padding: 10px 12px; border-radius: 12px;
  background: color-mix(in srgb, var(--acc) 10%, var(--card-background-color)); }
.aanpas .knoppen, .suggestie .knoppen { margin-top: 4px; }
.sugkop { margin: 0; font-size: 13px; color: var(--secondary-text-color); }
.sugtitel { margin: 0; font-size: 17px; font-weight: 500; }
.suggestie small { color: var(--secondary-text-color); font-size: 13px; }
.concept { font-size: 14px; }
.concept summary { cursor: pointer; padding: 6px 0; color: var(--primary-color); }
.concept .ingr li { font-size: 14px; padding: 6px 2px; }
.concept .stappen li { font-size: 14px; padding: 3px 0 5px 4px; }

/* beoordelen */
.beoordeelkop { width: 100%; display: flex; justify-content: space-between; gap: 8px; margin-top: 8px; padding: 10px 0 2px;
  border-top: 1px dashed var(--rand); font-size: 14px; color: var(--secondary-text-color); text-align: left; }
.beoordeelkop .sv { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; color: var(--primary-text-color); }
.beoordeel { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--rand); }
.bkop { display: flex; justify-content: space-between; align-items: center; }
.persoon { display: flex; align-items: center; gap: 8px; padding: 4px 0; flex-wrap: wrap; }
.pn { flex: 1; min-width: 80px; font-weight: 500; }
.gezichten { display: flex; gap: 4px; }
.gz { width: 48px; height: 48px; border-radius: 50%; font-size: 26px; filter: grayscale(1); opacity: .55; transition: transform .1s; }
.gz.on { filter: none; opacity: 1; background: color-mix(in srgb, var(--acc) 18%, transparent); transform: scale(1.08); }
@media (prefers-reduced-motion: reduce) { .gz { transition: none; } }

/* recepten */
.add { display: flex; gap: 8px; }
.add input { flex: 1; min-width: 0; height: 48px; padding: 0 14px; font-size: 17px; border-radius: 12px; border: 1px solid var(--rand);
  background: var(--primary-background-color); color: var(--primary-text-color); }
.addbtn { width: 48px; height: 48px; border-radius: 12px; background: var(--acc); color: #fff; font-size: 28px; line-height: 1; }
.chips-rij { display: flex; gap: 6px; overflow-x: auto; padding: 10px 0 2px; scrollbar-width: none; }
.chips-rij::-webkit-scrollbar { display: none; }
.filt { flex: none; padding: 8px 13px; border-radius: 18px; border: 1px solid var(--rand); font-size: 14px; white-space: nowrap; }
.filt.on { background: var(--acc); color: #fff; border-color: var(--acc); }
.recepten { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.rkaart { width: 100%; text-align: left; display: grid; gap: 3px; padding: 12px 14px; border-radius: 12px;
  background: var(--card-background-color); box-shadow: 0 0 0 1px var(--rand) inset; min-height: 56px; }
.rmeta { color: var(--secondary-text-color); font-size: 13px; display: flex; flex-wrap: wrap; gap: 4px 10px; }
.score { font-weight: 600; color: var(--primary-text-color); }
.herkomst { font-size: 12px; padding: 1px 8px; border-radius: 999px; background: var(--zacht); color: var(--primary-text-color); white-space: nowrap; }
.herkomst.h-bron { background: color-mix(in srgb, var(--success-color, #2e7d32) 18%, transparent); }
.herkomst.h-ai, .herkomst.h-onbekend { background: color-mix(in srgb, var(--warning-color, #f9a825) 22%, transparent); }
.kwal { color: var(--warning-color, #b26a00); font-size: 12px; }
.kwalblok { border-left: 4px solid var(--warning-color, #f9a825); background: var(--zacht); padding: 8px 12px; border-radius: 8px; margin: 8px 0; font-size: 14px; }
.kwalblok ul { margin: 4px 0 0; padding-left: 18px; }
.bronlink { color: var(--primary-color); }
.terug { padding: 8px 4px; color: var(--primary-color); font-size: 15px; }
.recept h2, .kop2 { font-size: 24px; font-weight: 500; margin: 6px 0 4px; line-height: 1.2; }
.recept h3, .bewerk h3, .kaart h3 { font-size: 16px; margin: 20px 0 8px; }
.kaart h3 { margin-top: 0; }
.scores { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 14px; margin: 6px 0; align-items: baseline; }
.scores small { color: var(--secondary-text-color); }
.porties { display: flex; align-items: center; justify-content: space-between; margin-top: 16px; padding: 6px 12px; border-radius: 12px; background: var(--zacht); }
.stepper { display: flex; align-items: center; }
.stepper button { width: 44px; height: 44px; font-size: 22px; border-radius: 10px; }
.n { min-width: 30px; text-align: center; font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
.ingr { list-style: none; padding: 0; margin: 0; }
.ingr li { display: flex; justify-content: space-between; gap: 10px; padding: 9px 2px; border-bottom: 1px solid var(--rand); font-size: 16px; }
.ingr li small { color: var(--secondary-text-color); font-size: 12px; text-align: right; }
.ingr li small.warn { color: var(--warning-color, #b26a00); }
.ingr.compact li { font-size: 18px; }
.stappen { padding-left: 22px; margin: 0; }
.stappen li { padding: 6px 0 10px 4px; font-size: 16px; line-height: 1.5; }
.notitie { background: var(--zacht); padding: 10px 12px; border-radius: 10px; }
.bron a { color: var(--primary-color); }
.kaart { background: var(--card-background-color); border-radius: 14px; box-shadow: 0 0 0 1px var(--rand) inset; padding: 14px; margin: 10px 0; display: grid; gap: 10px; }
.kaart .primary, .kaart .sec-btn { justify-self: start; }
.bewerk { display: grid; gap: 10px; }
.ingrrijen { display: grid; gap: 8px; }
.irij { display: grid; grid-template-columns: 1fr 1fr 64px auto; gap: 6px; align-items: center; }
.irij input:not([type=checkbox]) { min-height: 42px; padding: 6px 10px; border-radius: 10px; border: 1px solid var(--rand); font-size: 15px;
  background: var(--primary-background-color); color: var(--primary-text-color); min-width: 0; width: 100%; }
.irij .iprod.onbekend { border-color: var(--warning-color, #f0a020); }
@media (max-width: 560px) { .irij { grid-template-columns: 1fr 64px; } .irij .iprod { grid-column: 1; } .irij .check { grid-column: 2; grid-row: 2; } }

/* koken */
.main.koken { display: flex; flex-direction: column; padding-bottom: 16px; }
.main.koken > * { width: 100%; }
.main.koken .kokennav { margin-top: auto; padding-top: 16px; }
.kokenkop { display: flex; align-items: center; gap: 10px; }
.kokenkop b { font-size: 16px; }
.voortgang { height: 6px; border-radius: 3px; background: var(--zacht); margin: 8px 0 16px; overflow: hidden; }
.voortgang span { display: block; height: 100%; background: var(--acc); transition: width .2s; }
.kokenkern { flex: 1; }
.kstap-label { color: var(--secondary-text-color); font-size: 15px; margin: 0 0 8px; text-transform: uppercase; letter-spacing: .05em; }
.kstap { font-size: clamp(22px, 3.4vw, 34px); line-height: 1.4; margin: 0; }
.ingrklap { margin: 16px 0; }
.ingrklap summary { cursor: pointer; padding: 10px 0; color: var(--primary-color); font-size: 16px; }
.kokennav { display: flex; gap: 12px; margin-top: 16px; }
.kokennav button { flex: 1; }
/* voorstel: keuzestap, opties, benodigd, instellingen */
.primary.breed { width: 100%; }
.plakbalk { position: sticky; bottom: -96px; margin: 16px -12px -96px; padding: 10px 12px calc(10px + env(safe-area-inset-bottom, 0px));
  display: flex; gap: 8px; align-items: center; background: var(--card-background-color); border-top: 1px solid var(--rand); z-index: 2; }
.main:has(.plakbalk) { padding-bottom: 0; }
.main:has(.plakbalk) .plakbalk { bottom: 0; margin-bottom: 0; }
.veld.smal2 { flex: 1 1 140px; }
.pdagen { list-style: none; padding: 0; margin: 8px 0 0; display: grid; gap: 8px; }
.pdag { background: var(--card-background-color); border-radius: 14px; box-shadow: 0 0 0 1px var(--rand) inset; padding: 10px 12px; display: grid; gap: 8px; }
.pdag.uit { opacity: .6; }
.pkop { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; min-height: 32px; cursor: pointer; }
.pkop input { width: 22px; height: 22px; accent-color: var(--acc); }
.pkop small { flex-basis: 100%; margin-left: 32px; font-size: 12px; }
.segment { display: flex; flex-wrap: wrap; gap: 4px; }
.segment button { padding: 7px 11px; border-radius: 16px; font-size: 13px; border: 1px solid var(--rand); }
.segment button.on { background: var(--acc); color: #fff; border-color: var(--acc); }
.chips-wrap { display: flex; flex-wrap: wrap; gap: 6px; }
.pers { padding: 7px 12px; border-radius: 16px; font-size: 14px; border: 1px dashed var(--rand); color: var(--secondary-text-color); text-decoration: line-through; }
.pers.on { border: 1px solid color-mix(in srgb, var(--acc) 60%, transparent); background: color-mix(in srgb, var(--acc) 14%, transparent);
  color: var(--primary-text-color); text-decoration: none; }
.pwens { min-height: 40px; padding: 6px 12px; border-radius: 10px; border: 1px solid var(--rand); font-size: 15px; width: 100%;
  background: var(--primary-background-color); color: var(--primary-text-color); }
.vdag { background: var(--card-background-color); border-radius: 14px; box-shadow: 0 0 0 1px var(--rand) inset; padding: 10px 12px; margin-bottom: 10px; }
.vdag.uit { opacity: .55; }
.vmeta { margin: 0 0 8px; font-size: 13px; color: var(--secondary-text-color); }
.opties { display: grid; gap: 8px; }
@media (min-width: 900px) { .opties { grid-template-columns: repeat(3, 1fr); } }
.optie { border-radius: 12px; box-shadow: 0 0 0 1px var(--rand) inset; padding: 8px 10px; display: grid; gap: 6px; align-content: start; }
.optie.on { box-shadow: 0 0 0 2px var(--acc) inset; background: color-mix(in srgb, var(--acc) 8%, transparent); }
.optie.af { opacity: .5; }
.okies { display: flex; gap: 10px; text-align: left; align-items: flex-start; padding: 2px 0; }
.radio { flex: none; width: 22px; height: 22px; margin-top: 2px; border-radius: 50%; border: 2px solid var(--secondary-text-color); }
.optie.on .radio { border: 7px solid var(--acc); }
.otekst { display: grid; gap: 3px; min-width: 0; }
.otekst .rol { font-size: 12px; color: var(--secondary-text-color); }
.otekst .t { font-size: 17px; line-height: 1.25; font-weight: 500; }
.otekst small { color: var(--secondary-text-color); font-size: 13px; }
.otekst .reden { font-size: 13px; color: var(--secondary-text-color); font-style: italic; }
.labels { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px; }
.lab { font-size: 12px; padding: 2px 7px; border-radius: 9px; background: var(--zacht); white-space: nowrap; font-style: normal; }
.lab.actie { background: color-mix(in srgb, var(--warning-color, #f0a020) 25%, transparent); }
.lab.bijna { background: color-mix(in srgb, var(--error-color, #c62828) 20%, transparent); }
.lab.huis { background: color-mix(in srgb, var(--success-color, #2e7d32) 18%, transparent); }
.fbrij { display: flex; flex-wrap: wrap; gap: 4px; }
.fb { display: inline-flex; align-items: center; gap: 4px; padding: 5px 9px; border-radius: 14px; font-size: 13px; border: 1px solid var(--rand);
  color: var(--secondary-text-color); }
.fb.on { color: var(--primary-text-color); border-color: transparent; background: color-mix(in srgb, var(--acc) 22%, transparent); }
.fb.fb-niet_lekker.on, .fb.fb-teveel_werk.on { background: color-mix(in srgb, var(--error-color, #c62828) 22%, transparent); }
.fb.groot { padding: 8px 12px; font-size: 14px; }
.optie .fbt { display: none; }
@media (min-width: 480px) and (max-width: 899px) { .optie .fbt { display: inline; } }
.optie .fb { padding: 6px 10px; font-size: 15px; }
.knoppen.klein { margin-top: 8px; }
.knoppen.klein .sec-btn { min-height: 36px; padding: 0 12px; font-size: 13px; }
.bnwinkel { font-size: 15px; margin: 16px 2px 6px; }
.bnlijst { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.bnrij { display: flex; align-items: center; gap: 6px; background: var(--card-background-color); border-radius: 12px; box-shadow: 0 0 0 1px var(--rand) inset; padding: 4px 4px 4px 10px; }
.bnrij.uit { opacity: .55; }
.bnkies { flex: 1; min-width: 0; display: flex; gap: 10px; align-items: center; padding: 6px 0; cursor: pointer; }
.bnkies input { width: 22px; height: 22px; flex: none; accent-color: var(--acc); }
.bnkies .txt { display: grid; min-width: 0; }
.bnkies .t { font-size: 16px; }
.bnkies small { font-size: 12px; color: var(--secondary-text-color); }
.bnrij select { min-height: 38px; border-radius: 8px; border: 1px solid var(--rand); background: var(--primary-background-color); color: var(--primary-text-color); }
.genoeg { margin-top: 14px; }
.genoeg .kop { width: 100%; text-align: left; padding: 10px 2px; display: flex; gap: 8px; align-items: center; color: var(--secondary-text-color); }
.genoeg .kop::after { content: "▾"; margin-left: auto; }
.genoeg.open .kop::after { transform: rotate(180deg); }
.genoeg .kop b { font-size: 12px; padding: 2px 8px; border-radius: 10px; background: var(--zacht); }
.duim { width: 52px; height: 48px; border-radius: 24px; font-size: 24px; filter: grayscale(1); opacity: .5; }
.duim.on { filter: none; opacity: 1; background: color-mix(in srgb, var(--acc) 18%, transparent); }
.pkaart { gap: 8px; }
.pkaart .wis.klein { min-height: 36px; padding: 0 10px; font-size: 13px; }
.dagvink { display: flex; flex-wrap: wrap; gap: 4px; }
.dagvink label { display: inline-flex; align-items: center; gap: 3px; padding: 4px 8px; border-radius: 12px; background: var(--zacht); font-size: 13px; }
.omdeweek { display: grid; gap: 6px; }
.omdeweek small { display: block; color: var(--secondary-text-color); margin-bottom: 2px; }
.wdtabel { display: grid; gap: 6px; }
.wdrij { display: grid; grid-template-columns: 32px 1.3fr 1fr 64px 64px 2fr; gap: 6px; align-items: center; }
.wdrij select, .wdrij input { min-height: 40px; padding: 6px 8px; border-radius: 8px; border: 1px solid var(--rand); font-size: 14px; min-width: 0; width: 100%;
  background: var(--primary-background-color); color: var(--primary-text-color); }
@media (max-width: 600px) { .wdrij { grid-template-columns: 26px 1fr 1fr 50px 50px; } .wdrij .wdnotitie { grid-column: 2 / -1; } .wdrij { padding-bottom: 6px; border-bottom: 1px dashed var(--rand); } }

/* aanpassen met Gemini, bronnen, ontdek */
.addbtn.zoekbtn { background: var(--zacht); color: var(--primary-text-color); font-size: 22px; }
.aanpaskaart { border: 1px solid color-mix(in srgb, var(--acc) 40%, transparent); }
.voorstelblok { margin: 14px 0; padding: 12px 14px; border-radius: 14px; display: grid; gap: 6px;
  background: color-mix(in srgb, var(--acc) 10%, var(--card-background-color)); box-shadow: 0 0 0 1px color-mix(in srgb, var(--acc) 35%, transparent) inset; }
.voorstelblok h3 { margin: 8px 0 2px; }
.wijz { margin: 2px 0 4px; padding-left: 20px; }
.wijz li { padding: 2px 0; }
.verschil { margin: 0; font-size: 14px; }
.verschil s { color: var(--secondary-text-color); }
.diff { display: grid; gap: 4px; }
.diff > div { padding: 6px 10px; border-radius: 8px; font-size: 15px; }
.d-erbij { background: color-mix(in srgb, var(--success-color, #2e7d32) 18%, transparent); }
.d-weg { background: color-mix(in srgb, var(--error-color, #c62828) 16%, transparent); text-decoration: line-through; text-decoration-color: color-mix(in srgb, var(--error-color, #c62828) 60%, transparent); }
.d-wijzig { background: color-mix(in srgb, var(--warning-color, #f9a825) 20%, transparent); }
.d-wijzig s { color: var(--secondary-text-color); }
.voorstelblok details summary { cursor: pointer; padding: 8px 0; color: var(--primary-color); }
.stappen li.nw { background: color-mix(in srgb, var(--success-color, #2e7d32) 14%, transparent); border-radius: 8px; }
.varianten { gap: 4px 6px; align-items: center; }
.varianten .link { padding: 0; }
.herkomstkoppel { margin: 8px 0; font-size: 14px; }
.herkomstkoppel summary { cursor: pointer; padding: 6px 0; color: var(--primary-color); }
.herkomstkoppel select, .knoppen.klein select { min-height: 38px; border-radius: 8px; border: 1px solid var(--rand);
  background: var(--primary-background-color); color: var(--primary-text-color); padding: 0 8px; font-size: 14px; }
.bron.uit { opacity: .6; }
.bronkop { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.bronkop b { font-size: 17px; }
.typebadge { font-size: 12px; padding: 1px 8px; border-radius: 999px; background: var(--zacht); color: var(--secondary-text-color); }
.bron .rmeta a, .ontdek a.sec-btn { color: var(--primary-color); }
.ontdek { list-style: none; margin: 10px 0 0; padding: 0; display: grid; gap: 8px; }
.orij { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 12px;
  border-radius: 12px; background: var(--card-background-color); box-shadow: 0 0 0 1px var(--rand) inset; }
.orij.mislukt { opacity: .7; }
.orij .otekst { flex: 1; min-width: 200px; }
.orij .knoppen { margin: 0; }
.orij a.sec-btn { display: inline-flex; align-items: center; text-decoration: none; }
.otekst small.warn { color: var(--warning-color, #b26a00); }
.primary.klein { min-height: 40px; padding: 0 16px; font-size: 14px; }
/* dag voor dag plannen */
.dstrip { display: flex; align-items: center; gap: 6px; margin: 6px 0 12px; }
.rond.klein { width: 36px; height: 36px; font-size: 22px; flex: none; }
.dpillen { flex: 1; display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 4px; }
.dpil { display: grid; justify-items: center; gap: 1px; padding: 6px 0 4px; border-radius: 12px; border: 1px solid var(--rand);
  background: var(--card-background-color); min-height: 62px; }
.dpil .dk { font-size: 12px; color: var(--secondary-text-color); text-transform: capitalize; }
.dpil b { font-size: 17px; font-variant-numeric: tabular-nums; }
.dpil small { font-size: 12px; min-height: 16px; color: var(--secondary-text-color); }
.dpil.klaar small { color: var(--success-color, #2e7d32); font-weight: 700; }
.dpil.voorbij { opacity: .6; }
.dpil.on { background: var(--acc); border-color: var(--acc); color: var(--text-primary-color, #fff); }
.dpil.on .dk, .dpil.on small { color: inherit; }
.dpaneel { background: var(--card-background-color); border-radius: 14px; box-shadow: 0 0 0 1px var(--rand) inset; padding: 12px 14px;
  display: grid; gap: 10px; }
.dpaneel .dagkop { margin: 0; flex-wrap: wrap; }
.dgepland { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 12px; border-radius: 10px;
  background: color-mix(in srgb, var(--success-color, #2e7d32) 12%, transparent); }
.dpaneel > .hint { margin-top: -6px; }
.dblok { display: grid; gap: 6px; }
.dlabel { font-size: 13px; color: var(--secondary-text-color); }
.tog { padding: 8px 12px; border-radius: 18px; font-size: 14px; border: 1px solid var(--rand); background: var(--card-background-color); }
.tog.on { border-color: color-mix(in srgb, var(--acc) 60%, transparent); background: color-mix(in srgb, var(--acc) 16%, transparent); }
.tog.bron.on { background: var(--acc); color: var(--text-primary-color, #fff); border-color: var(--acc); }
.dwens { display: flex; gap: 8px; flex-wrap: wrap; }
.dwens input { flex: 1 1 200px; min-width: 0; min-height: 44px; padding: 0 14px; border-radius: 22px; border: 1px solid var(--rand); font-size: 15px;
  background: var(--primary-background-color); color: var(--primary-text-color); }
.meekijk { border-radius: 12px; background: color-mix(in srgb, var(--acc) 7%, var(--card-background-color));
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--acc) 30%, transparent) inset; padding: 2px 12px; }
.meekijk summary { cursor: pointer; padding: 10px 0; font-weight: 500; display: flex; align-items: center; gap: 8px; list-style: none; }
.meekijk summary::-webkit-details-marker { display: none; }
.meekijk summary::after { content: "▾"; margin-left: auto; color: var(--secondary-text-color); }
.meekijk[open] summary::after { transform: rotate(180deg); }
.spinner.klein { display: inline-block; width: 14px; height: 14px; border-width: 2px; vertical-align: -2px; }
.mstappen { list-style: none; margin: 0 0 8px; padding: 0; display: grid; gap: 4px; font-size: 14px; }
.mstappen li { display: flex; gap: 8px; align-items: baseline; }
.mstappen .mi { flex: none; width: 16px; text-align: center; }
.mstappen .ms-klaar .mi { color: var(--success-color, #2e7d32); }
.mstappen .ms-fout { color: var(--warning-color, #b26a00); }
.mstappen .ms-bezig .mi { align-self: center; }
.gedachten { max-height: 220px; overflow-y: auto; margin: 0 0 10px; padding: 8px 10px; border-radius: 8px; background: var(--zacht);
  font-size: 13px; line-height: 1.45; color: var(--secondary-text-color); border-left: 3px solid color-mix(in srgb, var(--acc) 50%, transparent); }
.gedachten p { margin: 0 0 6px; }
.gedachten b { color: var(--primary-text-color); }
.optie.dopt { gap: 4px; }
.optie.dopt .knoppen { margin-top: 2px; }
.optie.dopt a.link { text-decoration: none; }
.dres { display: grid; gap: 4px; padding: 10px 12px; border-radius: 12px; background: color-mix(in srgb, var(--success-color, #2e7d32) 12%, transparent); }
.dres p { margin: 0; font-size: 14px; }
.dres .drestitel { font-size: 16px; }
.dreswinkel { padding-left: 22px; }
@media (max-width: 420px) { .dpil .dk { font-size: 11px; } .dpil b { font-size: 15px; } .dstrip .rond.klein { width: 30px; } }
@media (min-width: 700px) {
  .main { padding: 14px 24px 96px; }
  .main > *, .bar > * { max-width: 720px; margin-left: auto; margin-right: auto; }
}
`;

if (!customElements.get("eten-panel")) customElements.define("eten-panel", EtenPanel);
