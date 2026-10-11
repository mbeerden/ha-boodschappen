// Dashboardkaarten: de Boodschappen- en Eten-pagina als Lovelace-kaart
// (type: custom:boodschappen-card en custom:eten-card), bijv. voor een wandtablet.
import "./panel.js";
import "./eten.js";

function maakKaart(tag, paneel) {
  return class extends HTMLElement {
    setConfig(config) {
      this._config = config || {};
    }

    set hass(h) {
      if (!this._p) {
        this.style.display = "block";
        this.style.height = this._config.height || "calc(100vh - var(--header-height, 56px))";
        this.style.overflow = "hidden";
        const p = document.createElement(paneel);
        p.narrow = false;
        p.inKaart = true;
        this._p = p;
        this.appendChild(p);
        if (!this._config.show_header && p.shadowRoot) {
          const s = new CSSStyleSheet();
          s.replaceSync("header { display: none !important; }");
          p.shadowRoot.adoptedStyleSheets = [...p.shadowRoot.adoptedStyleSheets, s];
        }
      }
      this._p.hass = h;
    }

    getCardSize() {
      return 12;
    }

    getGridOptions() {
      return { columns: "full", rows: 12 };
    }
  };
}

if (!customElements.get("boodschappen-card")) customElements.define("boodschappen-card", maakKaart("boodschappen-card", "boodschappen-panel"));
if (!customElements.get("eten-card")) customElements.define("eten-card", maakKaart("eten-card", "eten-panel"));

window.customCards = window.customCards || [];
for (const [type, name, description] of [
  ["boodschappen-card", "Boodschappen", "Boodschappenlijst per winkel"],
  ["eten-card", "Eten", "Weekmenu, recepten, koken en beoordelen"],
]) {
  if (!window.customCards.some((c) => c.type === type)) window.customCards.push({ type, name, description });
}
