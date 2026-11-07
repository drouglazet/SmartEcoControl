/*
 * Smart ECOcontrol : carte thermostat colorée selon l'allure de chauffe.
 *
 *   type: custom:smart-ecocontrol-card
 *   entity: climate.salon
 *
 * Options : name, step (pas des boutons +/-, 0,5 par défaut), colors (voir COULEURS).
 * La carte lit les attributs de l'entité climate de l'intégration (allure_name,
 * setpoint_visible, available_derogations, derogation_until, planning...).
 */
(() => {
  const TAG = "smart-ecocontrol-card";
  if (customElements.get(TAG)) return;

  // COULEURS : une par allure (modifiables avec « colors: » dans la configuration)
  const DEFAULT_COLORS = {
    eco: "#43a047", // vert
    comfort_minus_2: "#ffd54f", // jaune
    comfort_minus_1: "#ffb300", // ambre
    comfort: "#fb8c00", // orange
    comfort_plus_1: "#e64a19", // rouge orangé
    frost_protection: "#039be5", // bleu
    off: "#9e9e9e", // gris
    shedding: "#8e24aa", // violet (délestage)
    unknown: "#9e9e9e",
  };

  // --- éditeur visuel : Home Assistant construit le formulaire à partir d'un schéma
  const uiLang = () =>
    (document.documentElement.lang || navigator.language || "fr").slice(0, 2) === "fr" ? "fr" : "en";
  // une couleur laissée vide dans l'éditeur ne doit pas écraser la couleur par défaut
  const cleanColors = (colors) =>
    Object.fromEntries(
      Object.entries(colors || {}).filter(([, value]) => typeof value === "string" && value.trim())
    );

  const TEXTS = {
    fr: {
      allure: {
        eco: "Éco",
        comfort_minus_2: "Confort −2",
        comfort_minus_1: "Confort −1",
        comfort: "Confort",
        comfort_plus_1: "Confort +1",
        frost_protection: "Hors-gel",
        off: "Arrêt",
        shedding: "Délestée",
        unknown: "—",
      },
      hvac: { auto: "Auto", heat: "Manuel", off: "Arrêt" },
      preset: { eco: "Éco", comfort: "Confort", frost: "Hors-gel" },
      window: "Fenêtre ouverte",
      planning: "Planning",
      until: "Dérogation jusqu'à {time}",
      unavailable: "Indisponible",
      less: "Baisser la température",
      more: "Augmenter la température",
      noEntity: "Indiquez une entité climate de l'intégration Smart ECOcontrol.",
    },
    en: {
      allure: {
        eco: "Eco",
        comfort_minus_2: "Comfort −2",
        comfort_minus_1: "Comfort −1",
        comfort: "Comfort",
        comfort_plus_1: "Comfort +1",
        frost_protection: "Frost",
        off: "Off",
        shedding: "Load-shed",
        unknown: "—",
      },
      hvac: { auto: "Auto", heat: "Manual", off: "Off" },
      preset: { eco: "Eco", comfort: "Comfort", frost: "Frost" },
      window: "Window open",
      planning: "Schedule",
      until: "Override until {time}",
      unavailable: "Unavailable",
      less: "Lower the temperature",
      more: "Raise the temperature",
      noEntity: "Set a climate entity of the Smart ECOcontrol integration.",
    },
  };

  // [clé de dérogation, preset Home Assistant, allure correspondante]
  const DEROGATIONS = [
    ["eco", "eco", "eco"],
    ["comfort", "comfort", "comfort"],
    ["frost", "frost_protection", "frost_protection"],
  ];

  const FEATURE_TARGET_TEMPERATURE = 1;
  const BUSY_TIMEOUT_MS = 25000; // la box est lente : ~3 s par commande
  const DEBOUNCE_MS = 1200;

  // Cadran : arc de 270° ouvert vers le bas, comme la carte thermostat de Home Assistant
  const CX = 100;
  const CY = 100;
  const R = 84;
  const START = 135;
  const SWEEP = 270;
  const point = (deg) => {
    const rad = (deg * Math.PI) / 180;
    return [CX + R * Math.cos(rad), CY + R * Math.sin(rad)];
  };
  const arc = (fraction) => {
    if (!(fraction > 0)) return "";
    const f = Math.min(fraction, 1);
    const [x0, y0] = point(START);
    const [x1, y1] = point(START + SWEEP * f);
    const large = SWEEP * f > 180 ? 1 : 0;
    return `M${x0.toFixed(2)} ${y0.toFixed(2)}A${R} ${R} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  };

  const esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
    );

  const STYLE = `
    :host { display: block; }
    ha-card { display: block; padding: 16px 16px 14px; position: relative; overflow: hidden;
              --ct: color-mix(in srgb, var(--c, #888) 72%, var(--primary-text-color, #212121)); }
    .tint { position: absolute; inset: 0; background: var(--c); opacity: .09; pointer-events: none; }
    .title { position: relative; text-align: center; font-size: 1.05rem; font-weight: 500;
             color: var(--primary-text-color); margin-bottom: 4px; }
    .dial { position: relative; width: 100%; max-width: 230px; margin: 0 auto; aspect-ratio: 1; }
    svg { width: 100%; height: 100%; display: block; }
    .track { fill: none; stroke: var(--c); stroke-opacity: .22; stroke-width: 14; stroke-linecap: round; }
    .arc { fill: none; stroke: var(--c); stroke-width: 14; stroke-linecap: round; }
    .knob { fill: var(--card-background-color, #fff); stroke: var(--c); stroke-width: 4; }
    .center { position: absolute; inset: 0; display: flex; flex-direction: column;
              align-items: center; justify-content: center; text-align: center; padding: 0 22%; }
    .mode { font-size: .95rem; font-weight: 500; color: var(--primary-text-color); }
    .value { font-size: 3.1rem; line-height: 1.05; color: var(--primary-text-color);
             font-weight: 400; display: flex; align-items: flex-start; justify-content: center; }
    .value.word { font-size: 2rem; font-weight: 500; color: var(--ct); margin: 4px 0; }
    .value .unit { font-size: 1.1rem; margin-top: .35em; margin-left: 2px; }
    .sub { font-size: .9rem; font-weight: 500; color: var(--ct); }
    .sub.muted { color: var(--secondary-text-color); font-weight: 400; }
    .controls { position: relative; display: flex; justify-content: center; gap: 22px;
                margin-top: -34px; height: 48px; }
    .controls.empty { visibility: hidden; }
    .round { width: 48px; height: 48px; border-radius: 50%; cursor: pointer; font-size: 1.6rem;
             line-height: 1; display: grid; place-items: center; padding: 0;
             background: var(--card-background-color, #fff); color: var(--primary-text-color);
             border: 1px solid var(--divider-color, #bbb); }
    .round:disabled { opacity: .4; cursor: default; }
    .badge { position: relative; text-align: center; margin: 10px 0 0; font-size: .85rem;
             font-weight: 500; color: var(--ct); }
    .chips { position: relative; display: flex; justify-content: center; gap: 8px;
             flex-wrap: wrap; margin-top: 10px; }
    .chip { cursor: pointer; border-radius: 16px; padding: 6px 12px; font-size: .85rem;
            font-weight: 500; background: transparent; color: var(--chip-text);
            border: 1.5px solid var(--chip); }
    .chip.active { background: var(--chip); color: #fff; }
    .chip:disabled { opacity: .35; cursor: default; }
    .planning { position: relative; text-align: center; margin-top: 10px; font-size: .8rem;
                color: var(--secondary-text-color); }
    .error { position: relative; text-align: center; margin-top: 8px; font-size: .8rem;
             color: var(--error-color, #db4437); }
    .busy .dial, .busy .chips, .busy .controls { opacity: .6; }
    .unavailable { position: relative; text-align: center; padding: 28px 0;
                   color: var(--secondary-text-color); }
    button:focus-visible { outline: 2px solid var(--c); outline-offset: 2px; }
  `;

  class SmartEcoControlCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._config = null;
      this._hass = null;
      this._stateObj = null;
      this._lang = "fr";
      this._pending = null; // consigne en cours de réglage
      this._editing = false; // vrai tant que les clics +/- ne sont pas envoyés
      this._busy = false;
      this._error = "";
      this._timers = {};
      this.shadowRoot.addEventListener("click", (event) => this._onClick(event));
    }

    static getConfigForm() {
      const fr = uiLang() === "fr";
      const labels = fr
        ? {
            entity: "Pièce (entité climate)",
            name: "Nom affiché",
            step: "Pas des boutons − / +",
            colors: "Couleurs personnalisées",
            eco: "Éco",
            comfort_minus_2: "Confort −2",
            comfort_minus_1: "Confort −1",
            comfort: "Confort",
            comfort_plus_1: "Confort +1",
            frost_protection: "Hors-gel",
            off: "Arrêt",
            shedding: "Délestage",
          }
        : {
            entity: "Room (climate entity)",
            name: "Displayed name",
            step: "Step of the − / + buttons",
            colors: "Custom colors",
            eco: "Eco",
            comfort_minus_2: "Comfort −2",
            comfort_minus_1: "Comfort −1",
            comfort: "Comfort",
            comfort_plus_1: "Comfort +1",
            frost_protection: "Frost protection",
            off: "Off",
            shedding: "Load shedding",
          };
      const helpers = fr
        ? {
            name: "Par défaut : le nom de la pièce.",
            step: "0,5 °C par défaut.",
            colors: "Facultatif. Une couleur CSS par allure, par exemple #2e7d32 ; vide = couleur par défaut.",
          }
        : {
            name: "Default: the room name.",
            step: "0.5 °C by default.",
            colors: "Optional. A CSS color per mode, for example #2e7d32; empty = default color.",
          };
      return {
        schema: [
          {
            name: "entity",
            required: true,
            selector: { entity: { filter: { domain: "climate", integration: "smart_ecocontrol" } } },
          },
          { name: "name", selector: { text: {} } },
          { name: "step", selector: { number: { min: 0.1, max: 2, step: 0.1, mode: "box" } } },
          {
            type: "expandable",
            name: "colors",
            title: labels.colors,
            schema: Object.keys(DEFAULT_COLORS)
              .filter((key) => key !== "unknown")
              .map((key) => ({ name: key, selector: { text: {} } })),
          },
        ],
        computeLabel: (schema) => labels[schema.name] ?? schema.name,
        computeHelper: (schema) => helpers[schema.name],
      };
    }

    static getStubConfig(hass) {
      const entity = Object.keys(hass?.states ?? {}).find(
        (id) => id.startsWith("climate.") && hass.states[id].attributes?.allure_name !== undefined
      );
      return { entity: entity ?? "" };
    }

    setConfig(config) {
      if (!config || !config.entity) throw new Error("Indiquez « entity: climate.… »");
      this._config = { step: 0.5, ...config };
      this._render();
    }

    getCardSize() {
      return 5;
    }

    getGridOptions() {
      return { columns: 6, rows: 5, min_columns: 4, min_rows: 4 };
    }

    set hass(hass) {
      this._hass = hass;
      if (!this._config) return;
      const lang = (hass.language || "fr").slice(0, 2);
      const stateObj = hass.states?.[this._config.entity];
      if (stateObj === this._stateObj && lang === this._lang) return;
      if (stateObj !== this._stateObj) this._settle(); // nouvel état : fin d'attente
      this._stateObj = stateObj;
      this._lang = lang;
      this._render();
    }

    disconnectedCallback() {
      Object.values(this._timers).forEach(clearTimeout);
      this._timers = {};
    }

    // ----------------------------------------------------------- affichage

    _t() {
      return TEXTS[this._lang] || TEXTS.en;
    }

    _color(allure) {
      const colors = { ...DEFAULT_COLORS, ...cleanColors(this._config?.colors) };
      return colors[allure] || colors.unknown;
    }

    _number(value) {
      return Number(value).toLocaleString(this._lang, {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      });
    }

    _render() {
      const root = this.shadowRoot;
      const t = this._t();
      const stateObj = this._stateObj;
      if (!this._config) return;

      if (!stateObj || ["unavailable", "unknown"].includes(stateObj.state)) {
        root.innerHTML = `<style>${STYLE}</style>
          <ha-card style="--c:${this._color("unknown")}"><div class="unavailable">${esc(
            stateObj ? t.unavailable : t.noEntity
          )}</div></ha-card>`;
        return;
      }

      const a = stateObj.attributes;
      const allure = a.allure_name || "unknown";
      const color = this._color(allure);
      const label = t.allure[allure] ?? t.allure.unknown;
      const settable = ((a.supported_features ?? 0) & FEATURE_TARGET_TEMPERATURE) !== 0;
      const showsNumber = a.setpoint_visible === true && a.setpoint != null;
      const min = Number(a.min_temp ?? 7);
      const max = Number(a.max_temp ?? 28);
      const target = this._pending ?? (settable ? a.temperature : a.setpoint);
      const fraction = showsNumber
        ? (Number(target) - 7) / (28 - 7) // échelle de la box : 7 à 28 °C
        : allure === "off" || allure === "unknown"
          ? 0
          : 1;

      const [kx, ky] = point(START + SWEEP * Math.max(0, Math.min(1, fraction)));
      const knob = showsNumber ? `<circle class="knob" cx="${kx.toFixed(2)}" cy="${ky.toFixed(2)}" r="9"/>` : "";

      const hvac = t.hvac[stateObj.state] ?? stateObj.state;
      const centerValue = showsNumber
        ? `<div class="value">${esc(this._number(target))}<span class="unit">°C</span></div>
           <div class="sub">${esc(label)}</div>`
        : `<div class="value word">${esc(label)}</div>
           <div class="sub muted">${a.window_open ? esc(t.window) : "&nbsp;"}</div>`;

      const controls = settable
        ? `<div class="controls">
             <button class="round" data-action="less" aria-label="${esc(t.less)}">−</button>
             <button class="round" data-action="more" aria-label="${esc(t.more)}">+</button>
           </div>`
        : `<div class="controls empty" aria-hidden="true">
             <button class="round" tabindex="-1" disabled>−</button>
             <button class="round" tabindex="-1" disabled>+</button>
           </div>`;

      const active = a.preset_mode;
      const available = a.available_derogations || [];
      const chips = DEROGATIONS.map(([kind, preset, chipAllure]) => {
        const on = active === preset;
        const disabled = !on && !available.includes(kind);
        return `<button class="chip${on ? " active" : ""}" style="--chip:${this._color(chipAllure)};--chip-text:color-mix(in srgb, ${this._color(chipAllure)} 72%, var(--primary-text-color, #212121))"
                  data-action="derogation" data-preset="${preset}" ${disabled ? "disabled" : ""}
                  aria-pressed="${on}">${esc(t.preset[kind])}</button>`;
      }).join("");

      const badge = a.derogation_until
        ? `<div class="badge">${esc(t.until.replace("{time}", a.derogation_until))}</div>`
        : "";
      const planning = a.planning
        ? `<div class="planning">${esc(t.planning)} : ${esc(a.planning)}</div>`
        : "";
      const name = this._config.name || a.friendly_name || this._config.entity;

      root.innerHTML = `<style>${STYLE}</style>
        <ha-card class="${this._busy ? "busy" : ""}" style="--c:${color}">
          <div class="tint"></div>
          <div class="title">${esc(name)}</div>
          <div class="dial">
            <svg viewBox="0 0 200 200" aria-hidden="true">
              <path class="track" d="${arc(1)}"/>
              <path class="arc" d="${arc(fraction)}"/>
              ${knob}
            </svg>
            <div class="center">
              <div class="mode">${esc(hvac)}</div>
              ${centerValue}
            </div>
          </div>
          ${controls}
          ${badge}
          <div class="chips">${chips}</div>
          ${planning}
          <div class="error">${esc(this._error)}</div>
        </ha-card>`;
    }

    // ----------------------------------------------------------- commandes

    _onClick(event) {
      const button = event.target.closest("button[data-action]");
      if (!button || button.disabled || !this._hass || !this._stateObj) return;
      const a = this._stateObj.attributes;
      switch (button.dataset.action) {
        case "less":
        case "more": {
          const step = Number(this._config.step) || 0.5;
          const current = Number(this._pending ?? a.temperature);
          if (Number.isNaN(current)) return;
          const wanted = current + (button.dataset.action === "more" ? step : -step);
          const clamped = Math.min(Number(a.max_temp ?? 28), Math.max(Number(a.min_temp ?? 7), wanted));
          this._pending = Math.round(clamped * 10) / 10;
          this._editing = true;
          this._render();
          clearTimeout(this._timers.debounce);
          this._timers.debounce = setTimeout(() => this._sendTemperature(), DEBOUNCE_MS);
          break;
        }
        case "derogation": {
          const preset = button.dataset.preset;
          const next = a.preset_mode === preset ? "none" : preset; // second clic : retour au planning
          this._call("climate", "set_preset_mode", { preset_mode: next });
          break;
        }
        default:
      }
    }

    _sendTemperature() {
      const temperature = this._pending;
      this._editing = false;
      if (temperature == null) return;
      this._call("climate", "set_temperature", { temperature });
    }

    async _call(domain, service, data) {
      this._error = "";
      this._setBusy(true);
      try {
        await this._hass.callService(domain, service, {
          entity_id: this._config.entity,
          ...data,
        });
      } catch (err) {
        this._pending = null;
        this._setBusy(false);
        this._error = err?.message || String(err);
        clearTimeout(this._timers.error);
        this._timers.error = setTimeout(() => {
          this._error = "";
          this._render();
        }, 8000);
        this._render();
      }
      // Sans erreur : on attend le nouvel état de l'entité (voir set hass)
    }

    _setBusy(busy) {
      this._busy = busy;
      clearTimeout(this._timers.busy);
      if (busy) this._timers.busy = setTimeout(() => this._settle(true), BUSY_TIMEOUT_MS);
      this._render();
    }

    _settle(rerender = false) {
      clearTimeout(this._timers.busy);
      const changed = this._busy || (this._pending !== null && !this._editing);
      this._busy = false;
      if (!this._editing) this._pending = null; // ne pas écraser un réglage en cours
      if (rerender && changed) this._render();
    }
  }

  customElements.define(TAG, SmartEcoControlCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: TAG,
    name: "Smart ECOcontrol",
    description: "Thermostat d'une pièce, coloré selon l'allure : Éco, Confort, Hors-gel, Arrêt…",
  });
})();
