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
const GEZICHT = ["", "😖", "🙁", "😐", "🙂", "😍"];
const GEZICHT_TEKST = ["", "Vies", "Niet lekker", "Gaat wel", "Lekker", "Heerlijk"];

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
    if (this.busy || this.edit || this.aanpas || (this.view && this.view.soort !== "recept") || (a && /INPUT|SELECT|TEXTAREA/.test(a.tagName))) return;
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
          : v
          ? ""
          : `<div class="voorstelbalk">
              <input id="wens" type="text" placeholder="Wensen, bijv. iets met vis, snel op woensdag" aria-label="Wensen voor deze week">
              <button class="primary" data-act="voorstel">Stel week voor</button>
            </div>`
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
    const v = d.voorstel;
    const voorstelDagen = v ? Object.fromEntries(v.dagen.map((x) => [x.datum, x])) : {};
    const voorstelHier = v && v.dagen.some((x) => dagen.includes(x.datum));
    let html = "";
    if (v && !voorstelHier) {
      html += `<p class="melding">Er staat een voorstel klaar voor week ${weekNr(vanIso(v.start))}. <button class="link" data-act="naarvoorstel">Bekijk</button></p>`;
    }
    if (voorstelHier) {
      html += `<div class="voorstelkop"><div><b>Voorstel</b><small>${v.wens ? `Wens: ${esc(v.wens)}` : "Pas aan waar nodig en keur goed."}</small></div>
        <div class="knoppen">
          <button class="sec-btn" data-act="voorstelweg">Weggooien</button>
          <button class="sec-btn" data-act="voorstel" data-opnieuw="1">Opnieuw</button>
          <button class="primary" data-act="goedkeuren">Goedkeuren</button>
        </div></div>`;
    }
    html += `<ol class="dagen">`;
    for (const ds of dagen) {
      const p = plan[ds];
      const vs = voorstelDagen[ds];
      const ex = extern[ds] || [];
      const klasse = ds === vandaag ? "vandaag" : ds < vandaag ? "voorbij" : "";
      html += `<li class="dag ${klasse} ${vs && !vs.vast ? "voorstel" : ""}">
        <div class="dagkop"><span class="dn">${esc(dagNaam(ds))}</span><span class="dd">${esc(datumKort(ds))}${ds === vandaag ? " · vandaag" : ""}</span></div>
        ${this._dagInhoud(ds, p, vs, ex, vandaag)}
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
    if (vs && !vs.vast) return this._voorstelDag(vs);
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
        <button class="link" data-act="geenmaaltijd" data-datum="${ds}">Geen maaltijd</button></div>`;
    if (ds >= vandaag) html += this._suggestie(ds) || this._aanpasBlok(ds, null);
    return html;
  }

  _receptOpties(gekozen, leegTekst = "— eigen titel, zonder recept —", concept = null) {
    return `<option value="">${esc(leegTekst)}</option>` +
      (concept ? `<option value="${concept.id}" ${concept.id === gekozen ? "selected" : ""}>✨ ${esc(concept.naam)} (nieuw recept)</option>` : "") +
      this.recepten.map((r) => `<option value="${r.id}" ${r.id === gekozen ? "selected" : ""}>${esc(r.naam)}${r.vega ? " 🌱" : ""}</option>`).join("");
  }

  _voorstelDag(vs) {
    const ds = vs.datum;
    if (vs.geen)
      return `<div class="vform geen" data-datum="${ds}">
        <p class="geentitel">Geen maaltijd <small>we koken niet</small></p>
        ${vs.agenda ? `<p class="reden">In de agenda stond: ${esc(vs.agenda)}</p>` : ""}
        ${vs.reden ? `<p class="reden">${esc(vs.reden)}</p>` : ""}
        <div class="rij3">
          <label class="check"><input type="checkbox" data-vs="geen" checked> Geen maaltijd</label>
          <label class="check"><input type="checkbox" data-vs="overslaan" ${vs.overslaan ? "checked" : ""}> Niet plannen</label>
        </div>
        ${this._aanpasBlok(ds, null, vs.wens)}
      </div>`;
    return `<div class="vform ${vs.overslaan ? "uit" : ""}" data-datum="${ds}">
      <label class="veld"><span>Gerecht</span>
        <select data-vs="recept_id">${this._receptOpties(vs.recept_id, undefined, vs.concept)}</select></label>
      ${vs.concept && vs.recept_id === vs.concept.id ? this._conceptInfo(vs.concept) : ""}
      ${this._aanpasBlok(ds, null, vs.wens)}
      ${!vs.recept_id ? `<label class="veld"><span>Titel</span><input data-vs="titel" value="${esc(vs.titel)}"></label>` : ""}
      ${vs.agenda ? `<p class="reden">In de agenda stond: ${esc(vs.agenda)}</p>` : ""}
      ${vs.reden ? `<p class="reden">${esc(vs.reden)}</p>` : ""}
      <div class="rij3">
        <label class="veld"><span>Kookt</span><input data-vs="kookt" value="${esc(vs.kookt || "")}"></label>
        <label class="veld smal"><span>Porties</span><input data-vs="porties" type="number" min="1" inputmode="numeric" value="${esc(vs.porties ?? "")}"></label>
        <label class="veld smal"><span>Vriezer</span><input data-vs="vriezer" type="number" min="0" inputmode="numeric" value="${esc(vs.vriezer || 0)}"></label>
      </div>
      <div class="rij3">
        <label class="check"><input type="checkbox" data-vs="boodschappen" ${vs.boodschappen !== false ? "checked" : ""}> Boodschappen</label>
        <label class="check"><input type="checkbox" data-vs="geen"> Geen maaltijd</label>
        <label class="check"><input type="checkbox" data-vs="overslaan" ${vs.overslaan ? "checked" : ""}> Niet plannen</label>
      </div>
    </div>`;
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
    const personen = this.d.instellingen.personen || [];
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
    return `<div class="beoordeel" role="group" aria-label="Hoe was het?">
      <div class="bkop"><b>Hoe was het?</b><button class="link" data-act="beoordeelopen" data-id="">Klaar</button></div>
      ${personen
        .map(
          (x) => `<div class="persoon"><span class="pn">${esc(x)}</span><span class="gezichten">${[1, 2, 3, 4, 5]
            .map(
              (s) => `<button class="gz ${scores[x] === s ? "on" : ""}" data-act="score" data-id="${p.id}" data-persoon="${esc(x)}" data-score="${s}"
                 aria-pressed="${scores[x] === s}" aria-label="${esc(x)}: ${GEZICHT_TEKST[s]}" title="${GEZICHT_TEKST[s]}">${GEZICHT[s]}</button>`
            )
            .join("")}</span></div>`
        )
        .join("")}
      <p class="hint">Niet mee gegeten? Laat leeg, of tik nogmaals om te wissen.</p>
    </div>`;
  }

  // ------------------------------------------------------------------ recepten
  _receptenBar() {
    const tags = [...new Set(this.recepten.flatMap((r) => r.tags || []))].sort();
    return `<div class="add">
        <input id="zoek" type="search" placeholder="Zoek recept of ingrediënt" value="${esc(this.zoek)}" aria-label="Zoek recept">
        <button class="addbtn" data-act="nieuw" aria-label="Nieuw recept" title="Nieuw recept">+</button>
      </div>
      <div class="chips-rij filters">
        <button class="filt ${this.filter === "" ? "on" : ""}" data-act="filter" data-f="">Alles</button>
        <button class="filt ${this.filter === "vega" ? "on" : ""}" data-act="filter" data-f="vega">🌱 Vega</button>
        <button class="filt ${this.filter === "snel" ? "on" : ""}" data-act="filter" data-f="snel">≤ 30 min</button>
        <button class="filt ${this.filter === "top" ? "on" : ""}" data-act="filter" data-f="top">Favoriet</button>
        ${tags.map((t) => `<button class="filt ${this.filter === "tag:" + t ? "on" : ""}" data-act="filter" data-f="tag:${esc(t)}">${esc(t)}</button>`).join("")}
      </div>`;
  }

  _receptenLijst() {
    const q = norm(this.zoek);
    let lijst = this.recepten.filter(
      (r) => !q || norm(r.naam).includes(q) || (r.ingredienten || []).some((i) => norm(i.tekst).includes(q)) || (r.tags || []).some((t) => norm(t).includes(q))
    );
    const f = this.filter;
    if (f === "vega") lijst = lijst.filter((r) => r.vega);
    if (f === "snel") lijst = lijst.filter((r) => r.tijd && r.tijd <= 30);
    if (f === "top") lijst = lijst.filter((r) => r.score && r.score.gem >= 4).sort((a, b) => b.score.gem - a.score.gem);
    if (f.startsWith("tag:")) lijst = lijst.filter((r) => (r.tags || []).includes(f.slice(4)));
    if (!this.recepten.length)
      return `<div class="leeg"><p>Nog geen recepten.</p><p>Tik op + om er een toe te voegen via een link, geplakte tekst of zelf invullen.</p></div>`;
    if (!lijst.length) return `<p class="leeg">Geen recepten gevonden.</p>`;
    return `<ul class="recepten">${lijst
      .map((r) => {
        const meta = [r.tijd ? `${r.tijd} min` : "", r.vega ? "🌱 vega" : "", r.laatst ? `laatst ${datumKort(r.laatst)}` : ""].filter(Boolean).join(" · ");
        return `<li><button class="rkaart" data-act="open" data-id="${r.id}">
          <span class="t">${esc(r.naam)}</span>
          <span class="rmeta">${meta}${r.score ? `<span class="score" title="Gemiddeld ${fmtNum(r.score.gem)} van 5">${GEZICHT[Math.round(r.score.gem)]} ${fmtNum(r.score.gem)}</span>` : ""}</span>
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
        ${sc ? `<p class="scores">${Object.entries(sc.personen).map(([p, s]) => `<span>${esc(p)} ${GEZICHT[Math.round(s)]}</span>`).join("")}<small>${sc.keer}× gegeten</small></p>` : ""}
        <div class="knoppen">
          <button class="primary groot" data-act="koken" data-id="${r.id}">Start koken</button>
          <button class="sec-btn" data-act="bewerk" data-id="${r.id}">Bewerken</button>
        </div>
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
        ${r.bron ? `<p class="bron"><a href="${esc(r.bron)}" target="_blank" rel="noopener">Originele bron</a></p>` : ""}
      </article>`;
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
        <div class="knoppen">
          ${id ? `<button class="wis" data-act="receptweg" data-id="${id}">Verwijderen</button>` : ""}
          ${id ? `<button class="sec-btn" data-act="koppel" data-id="${id}" title="Laat de AI de ingrediënten aan Grocy-producten koppelen">Automatisch koppelen</button>` : ""}
          <span class="vul"></span>
          <button class="primary" data-act="receptopslaan" data-id="${id || ""}">Opslaan</button>
        </div>
      </form>`;
  }

  _nieuwView() {
    return `<button class="terug" data-act="terug">‹ Terug</button>
      <h2 class="kop2">Nieuw recept</h2>
      <section class="kaart">
        <h3>Van een website</h3>
        <label class="veld"><span>Link naar het recept</span><input id="imp-url" type="url" inputmode="url" placeholder="https://…"></label>
        <button class="primary" data-act="importeer" data-bron="url">Ophalen</button>
      </section>
      <section class="kaart">
        <h3>Tekst plakken</h3>
        <label class="veld"><span>Ingrediënten en bereiding, zoals je ze hebt</span><textarea id="imp-tekst" rows="6"></textarea></label>
        <button class="primary" data-act="importeer" data-bron="tekst">Omzetten</button>
      </section>
      <section class="kaart">
        <h3>Zelf invullen</h3>
        <button class="sec-btn" data-act="handmatig">Leeg recept</button>
      </section>
      <p class="hint">Ophalen en omzetten gaat via de AI-taak en koppelt de ingrediënten aan je Grocy-producten. Controleer het resultaat daarna even.</p>`;
  }

  _instView() {
    const i = this.d.instellingen;
    return `<button class="terug" data-act="terug">‹ Terug</button>
      <h2 class="kop2">Instellingen</h2>
      <form class="bewerk" onsubmit="return false">
        <label class="veld"><span>Agenda voor de maaltijden</span>
          <select name="agenda"><option value="">— geen —</option>${this.d.agendas.map((a) => `<option ${a === i.agenda ? "selected" : ""}>${esc(a)}</option>`).join("")}</select></label>
        <div class="rij3">
          <label class="veld smal"><span>Tijd</span><input name="tijd" type="time" value="${esc(i.tijd || "17:00")}"></label>
          <label class="veld smal"><span>Duur (min)</span><input name="duur" type="number" min="5" value="${esc(i.duur || 60)}"></label>
        </div>
        <label class="veld"><span>Personen die beoordelen (komma's)</span><input name="personen" value="${esc((i.personen || []).join(", "))}"></label>
        <label class="veld"><span>Huisregels voor het weekmenu-voorstel</span>
          <textarea name="huisregels" rows="9" placeholder="Wie kookt wanneer, wie eet mee, dieetwensen, vriezerdagen…">${esc(i.huisregels || "")}</textarea></label>
        <label class="veld"><span>AI-taak</span>
          <select name="ai"><option value="">Automatisch (${esc(this.d.ai_taken[0] || "geen")})</option>${this.d.ai_taken.map((a) => `<option ${a === i.ai ? "selected" : ""}>${esc(a)}</option>`).join("")}</select></label>
        <div class="knoppen"><span class="vul"></span><button class="primary" data-act="instopslaan">Opslaan</button></div>
      </form>`;
  }

  // ------------------------------------------------------------------ events
  _input(e) {
    if (e.target.id === "zoek") {
      this.zoek = e.target.value;
      this._zoekFocus = true;
      this.shadowRoot.querySelector(".main").innerHTML = this._receptenLijst();
    }
  }

  async _change(e) {
    const t = e.target;
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
        this.week = maandag(vanIso(this.d.voorstel.start));
        return this._render();
      case "voorstel": {
        const wens = root.querySelector("#wens")?.value || (b.dataset.opnieuw ? this.d.voorstel?.wens || "" : "");
        const vandaag = iso(new Date());
        const dagen = this._weekDagen().filter((x) => x >= vandaag);
        if (!dagen.length) {
          this.err = "Deze week is al voorbij. Ga naar een volgende week.";
          return this._render();
        }
        if (!this.recepten.length) {
          this.err = "Voeg eerst een paar recepten toe, dan kan het voorstel daaruit kiezen.";
          return this._render();
        }
        return this._do({ type: "boodschappen/eten_voorstel", start: dagen[0], dagen: dagen.length, wens }, "Gemini maakt een weekmenu…");
      }
      case "voorstelweg":
        return this._do({ type: "boodschappen/eten_voorstel_weg" });
      case "goedkeuren": {
        const res = await this._do({ type: "boodschappen/eten_goedkeuren" }, "In de agenda zetten en boodschappen bijwerken…");
        if (res && typeof res === "object") {
          this.melding = `${res.gepland} maaltijd(en) in de agenda gezet${res.op_lijst ? `, ${res.op_lijst} product(en) op de boodschappenlijst` : ""}.`;
          this._render();
        }
        return;
      }
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
        const res = await this._do({ type: "boodschappen/eten_suggestie_toepassen", datum: b.dataset.datum }, "Agenda en boodschappen bijwerken…");
        if (res && typeof res === "object") {
          this.melding = `Aangepast in de agenda${res.op_lijst ? `, ${res.op_lijst} product(en) op de boodschappenlijst` : ""}.`;
          this._render();
        }
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
          lijst: val("boodschappen").checked,
          geen: val("geen").checked,
        };
        if (velden.geen) Object.assign(velden, { recept_id: null, titel: "Geen maaltijd", boodschappen: false, lijst: false });
        if (!velden.recept_id && !velden.titel) {
          this.err = "Kies een recept of vul een titel in.";
          return this._render();
        }
        this.edit = null;
        this.bezigDag = b.dataset.datum;
        if (b.dataset.uid !== undefined && !b.dataset.id)
          return this._do({ type: "boodschappen/eten_overnemen", datum: b.dataset.datum, uid: b.dataset.uid || null,
            recept_id: velden.recept_id, titel: velden.titel, velden }, "Agenda bijwerken…");
        return this._do({ type: "boodschappen/eten_plan", datum: b.dataset.datum, plan_id: b.dataset.id || null, velden }, "Agenda bijwerken…");
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
        this.view = { soort: "recept", id: b.dataset.id };
        this.porties = null;
        this._scrollTop = true;
        return this._render();
      case "terug":
        this.view = this.view?.soort === "bewerk" && this.view.id ? { soort: "recept", id: this.view.id } : null;
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
        this.view = { soort: "nieuw" };
        this._scrollTop = true;
        return this._render();
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
        const msg = b.dataset.bron === "url" ? { url, tekst: null } : { url: null, tekst };
        if (!(msg.url || msg.tekst)) {
          this.err = b.dataset.bron === "url" ? "Plak eerst een link." : "Plak eerst een recept.";
          return this._render();
        }
        const id = await this._do({ type: "boodschappen/eten_import", ...msg }, "Recept ophalen en omzetten…");
        if (typeof id === "string") {
          this.view = { soort: "bewerk", id };
          this.melding = "Recept omgezet. Controleer het even en sla op.";
          this._scrollTop = true;
          this._render();
        }
        return;
      }
      case "instellingen":
        this.view = { soort: "instellingen" };
        this._scrollTop = true;
        return this._render();
      case "instopslaan": {
        const f = root.querySelector("form.bewerk");
        const v = (n) => f.querySelector(`[name=${n}]`).value;
        await this._do({ type: "boodschappen/eten_instellingen", instellingen: {
          agenda: v("agenda") || null, tijd: v("tijd") || "17:00", duur: Number(v("duur") || 60),
          personen: v("personen"), huisregels: v("huisregels"), ai: v("ai") || null } });
        if (!this.err) {
          this.view = null;
          this.melding = "Instellingen opgeslagen.";
          this._render();
        }
        return;
      }
    }
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
@media (min-width: 700px) {
  .main { padding: 14px 24px 96px; }
  .main > *, .bar > * { max-width: 720px; margin-left: auto; margin-right: auto; }
}
`;

if (!customElements.get("eten-panel")) customElements.define("eten-panel", EtenPanel);
