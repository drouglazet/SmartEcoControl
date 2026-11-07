/*
 * Smart ECOcontrol : éditeur de planning de chauffe (équivalent de la page
 * « Mes plannings de chauffe » de la box).
 *
 *   type: custom:smart-ecocontrol-planning-card
 *   planning: SEMAINE        # facultatif : nom ou numéro du planning affiché au départ
 *
 * Options : title, planning, colors (eco, comfort), config_entry_id (si plusieurs boxes).
 * La carte passe par les services smart_ecocontrol.get_planning, set_planning_day,
 * copy_planning_day, create_planning, rename_planning et delete_planning.
 *
 * Une plage de confort est une période où la pièce chauffe à sa température de
 * confort ; le reste de la journée est en Éco. Règles de la box : 5 plages au plus par
 * jour, 1 heure minimum chacune et 1 heure minimum entre deux plages.
 */
(() => {
  const TAG = "smart-ecocontrol-planning-card";
  if (customElements.get(TAG)) return;

  const DOMAIN = "smart_ecocontrol";
  const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const QUARTERS = 96;
  const MIN_RANGE = 4; // 1 h
  const MIN_GAP = 4; // 1 h
  const MAX_RANGES = 5;
  const NAME_MAX = 25;
  const DEFAULT_COLORS = { eco: "#43a047", comfort: "#fb8c00" };
  const COMPACT_WIDTH = 460;

  // --- éditeur visuel : Home Assistant construit le formulaire à partir d'un schéma
  const uiLang = () =>
    (document.documentElement.lang || navigator.language || "fr").slice(0, 2) === "fr" ? "fr" : "en";
  // une couleur laissée vide dans l'éditeur ne doit pas écraser la couleur par défaut
  const cleanColors = (colors) =>
    Object.fromEntries(
      Object.entries(colors || {}).filter(([, value]) => typeof value === "string" && value.trim())
    );

  // texte lisible sur la couleur de confort choisie (noir ou blanc)
  const onColor = (color) => {
    const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(color).trim());
    if (!m) return "#1a1a1a";
    let hex = m[1];
    if (hex.length === 3) hex = [...hex].map((c) => c + c).join("");
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    const lin = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    return lum > 0.3 ? "#1a1a1a" : "#ffffff";
  };

  const TEXTS = {
    fr: {
      title: "Planning de chauffe",
      planning: "Planning",
      rooms: "Pièces : {rooms}",
      noRooms: "Aucune pièce n'utilise ce planning",
      eco: "Éco",
      comfort: "Confort",
      hint: "Touchez une zone Éco pour ajouter une plage de confort, une plage de confort pour la modifier.",
      refresh: "Relire depuis la box",
      menu: "Gérer les plannings",
      newPlanning: "Nouveau planning",
      renamePlanning: "Renommer ce planning",
      deletePlanning: "Supprimer ce planning",
      addRange: "Ajouter une plage de confort",
      editRange: "Modifier la plage de confort",
      from: "De",
      to: "à",
      save: "Enregistrer",
      cancel: "Annuler",
      remove: "Supprimer",
      copy: "Copier ce jour vers un autre jour",
      copyTitle: "Copier {day} vers :",
      copyAction: "Copier",
      allDays: "Tous les autres jours",
      clearDay: "Tout en Éco ce jour",
      nameLabel: "Nom du planning",
      nameHint: "Lettres, chiffres et espaces, {max} caractères au plus.",
      create: "Créer",
      rename: "Renommer",
      confirmDelete: "Supprimer le planning « {name} » ? Cette action est définitive.",
      loading: "Lecture de la box…",
      saving: "Envoi à la box…",
      noSpace: "Pas de place ici : une plage de confort dure 1 h au moins et doit être à 1 h au moins d'une autre plage.",
      tooMany: "Une journée compte 5 plages de confort au plus.",
      invalidRange: "L'heure de fin doit être au moins 1 h après l'heure de début.",
      noPlannings: "Aucun planning modifiable : créez-en un avec le menu.",
      retry: "Réessayer",
      saved: "Enregistré.",
    },
    en: {
      title: "Heating schedule",
      planning: "Schedule",
      rooms: "Rooms: {rooms}",
      noRooms: "No room uses this schedule",
      eco: "Eco",
      comfort: "Comfort",
      hint: "Tap an Eco area to add a comfort range, or a comfort range to edit it.",
      refresh: "Read again from the box",
      menu: "Manage schedules",
      newPlanning: "New schedule",
      renamePlanning: "Rename this schedule",
      deletePlanning: "Delete this schedule",
      addRange: "Add a comfort range",
      editRange: "Edit the comfort range",
      from: "From",
      to: "to",
      save: "Save",
      cancel: "Cancel",
      remove: "Delete",
      copy: "Copy this day to other days",
      copyTitle: "Copy {day} to:",
      copyAction: "Copy",
      allDays: "All other days",
      clearDay: "Whole day in Eco",
      nameLabel: "Schedule name",
      nameHint: "Letters, digits and spaces, {max} characters at most.",
      create: "Create",
      rename: "Rename",
      confirmDelete: "Delete the schedule “{name}”? This cannot be undone.",
      loading: "Reading the box…",
      saving: "Sending to the box…",
      noSpace: "No room here: a comfort range lasts at least 1 h and must be at least 1 h from another range.",
      tooMany: "A day holds 5 comfort ranges at most.",
      invalidRange: "The end time must be at least 1 h after the start time.",
      noPlannings: "No editable schedule: create one from the menu.",
      retry: "Try again",
      saved: "Saved.",
    },
  };

  const pad = (n) => String(n).padStart(2, "0");
  const fmt = (q) => `${pad(Math.floor(q / 4))}:${pad((q % 4) * 15)}`; // 96 -> 24:00
  const parse = (text) => {
    const [h, m] = String(text).split(":").map(Number);
    return h * 4 + Math.floor((m || 0) / 15);
  };
  const esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
    );
  const sameRanges = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  const STYLE = `
    :host { display: block; }
    ha-card { display: block; position: relative; overflow: hidden; padding: 12px 14px 14px;
              --eco: #43a047; --comfort: #fb8c00; --on-comfort: #1a1a1a; }
    button, select, input { font: inherit; color: inherit; }
    .head { display: flex; align-items: center; gap: 4px; }
    .title { flex: 1; font-size: 1.1rem; font-weight: 500; color: var(--primary-text-color); }
    .icon { width: 36px; height: 36px; border-radius: 50%; border: 0; background: transparent;
            cursor: pointer; font-size: 1.25rem; line-height: 1; color: var(--secondary-text-color); }
    .icon:hover { background: var(--secondary-background-color, rgba(128,128,128,.15)); }
    .menu { position: absolute; right: 12px; top: 50px; z-index: 4; min-width: 210px; padding: 6px 0;
            background: var(--card-background-color, #fff); border-radius: 10px;
            border: 1px solid var(--divider-color, #ccc); box-shadow: 0 4px 14px rgba(0,0,0,.25); }
    .menu button { display: block; width: 100%; text-align: left; padding: 10px 16px; border: 0;
                   background: none; cursor: pointer; color: var(--primary-text-color); }
    .menu button:hover { background: var(--secondary-background-color, rgba(128,128,128,.15)); }
    .menu button.danger { color: var(--error-color, #db4437); }
    .menu button:disabled { opacity: .4; cursor: default; }
    select, input[type=text] { padding: 8px 10px; border-radius: 8px; background: var(--card-background-color, #fff);
            border: 1px solid var(--divider-color, #bbb); color: var(--primary-text-color); }
    .planning-select { width: 100%; margin-top: 6px; font-size: 1rem; }
    .rooms { margin: 6px 2px 10px; font-size: .85rem; color: var(--secondary-text-color); }
    .scale, .row { display: grid; grid-template-columns: 2.6em 1fr 32px; align-items: center; gap: 6px; }
    .scale { margin-top: 4px; }
    .ticks { position: relative; height: 16px; font-size: .68rem; color: var(--secondary-text-color); }
    .ticks span { position: absolute; transform: translateX(-50%); }
    .ticks span:first-child { transform: none; }
    .ticks span:last-child { transform: translateX(-100%); }
    .row { margin-top: 5px; }
    .dname { font-size: .85rem; text-transform: capitalize; color: var(--secondary-text-color); }
    .row.today .dname { color: var(--primary-text-color); font-weight: 600; }
    .bar { position: relative; height: 30px; border-radius: 6px; cursor: pointer; overflow: hidden;
           background:
             linear-gradient(to right, rgba(128,128,128,.35) 1px, transparent 1px) 0 0 / 12.5% 100%,
             linear-gradient(color-mix(in srgb, var(--eco) 32%, transparent), color-mix(in srgb, var(--eco) 32%, transparent)); }
    .row.today .bar { box-shadow: 0 0 0 1.5px var(--primary-text-color); }
    .seg { position: absolute; top: 0; bottom: 0; background: var(--comfort); color: var(--on-comfort); border-radius: 5px;
           display: flex; align-items: center; justify-content: center; text-align: center; font-size: .68rem;
           font-weight: 500; line-height: 1.05; white-space: pre; overflow: hidden; pointer-events: none; }
    .seg.two { font-size: .62rem; }
    .times { display: none; grid-column: 2; margin-top: -2px; font-size: .72rem; color: var(--secondary-text-color); }
    .compact .times { display: block; }
    .compact .times:empty { display: none; }
    .now { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--primary-text-color); pointer-events: none; opacity: .8; }
    .copy { width: 30px; height: 30px; border-radius: 50%; border: 0; background: transparent; cursor: pointer;
            color: var(--secondary-text-color); font-size: 1rem; }
    .copy:hover { background: var(--secondary-background-color, rgba(128,128,128,.15)); }
    .legend { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; margin-top: 12px;
              font-size: .8rem; color: var(--secondary-text-color); }
    .dot { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 5px; vertical-align: -1px; }
    .hint { margin-top: 6px; font-size: .78rem; color: var(--secondary-text-color); }
    .banner { margin-top: 10px; padding: 9px 12px; border-radius: 8px; font-size: .85rem; }
    .banner.error { background: color-mix(in srgb, var(--error-color, #db4437) 16%, transparent); color: var(--primary-text-color);
                    border: 1px solid var(--error-color, #db4437); }
    .banner.info { background: color-mix(in srgb, var(--comfort) 16%, transparent); color: var(--primary-text-color); }
    .banner button { margin-left: 8px; }
    .empty { padding: 24px 8px; text-align: center; color: var(--secondary-text-color); }
    .busy { position: absolute; inset: 0; z-index: 6; display: flex; align-items: center; justify-content: center;
            background: color-mix(in srgb, var(--card-background-color, #fff) 72%, transparent);
            color: var(--primary-text-color); font-weight: 500; }
    .spinner { width: 18px; height: 18px; margin-right: 10px; border-radius: 50%;
               border: 3px solid var(--divider-color, #ccc); border-top-color: var(--comfort); animation: spin 0.9s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .modal { position: absolute; inset: 0; z-index: 5; display: flex; align-items: center; justify-content: center;
             padding: 12px; background: rgba(0,0,0,.45); }
    .dialog { width: 100%; max-width: 340px; padding: 16px; border-radius: 12px; box-shadow: 0 6px 24px rgba(0,0,0,.35);
              background: var(--card-background-color, #fff); color: var(--primary-text-color); }
    .dialog h3 { margin: 0 0 12px; font-size: 1.05rem; font-weight: 500; }
    .fields { display: flex; align-items: center; gap: 10px; }
    .fields label { display: flex; align-items: center; gap: 8px; }
    .dialog .note { margin-top: 10px; font-size: .8rem; color: var(--secondary-text-color); }
    .dialog .error-text { margin-top: 10px; font-size: .82rem; color: var(--error-color, #db4437); }
    .checks { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; }
    .checks label { display: flex; align-items: center; gap: 8px; text-transform: capitalize; }
    .actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; margin-top: 16px; }
    .btn { padding: 8px 14px; border-radius: 8px; cursor: pointer; border: 1px solid var(--divider-color, #bbb); background: transparent; }
    .btn.primary { background: var(--comfort); border-color: var(--comfort); color: var(--on-comfort); font-weight: 500; }
    .btn.danger { color: var(--error-color, #db4437); border-color: var(--error-color, #db4437); }
    .btn.left { margin-right: auto; }
    .btn:disabled { opacity: .45; cursor: default; }
    button:focus-visible, select:focus-visible, input:focus-visible { outline: 2px solid var(--comfort); outline-offset: 2px; }
  `;

  class SmartEcoControlPlanningCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._config = null;
      this._hass = null;
      this._lang = "fr";
      this._plannings = []; // [{id, name, rooms, editable}]
      this._current = null; // {id, name, rooms, days: [[ [start, end], ... ] x7]}
      this._started = false;
      this._busy = "";
      this._notice = null; // {kind: "error" | "info", text, retry?}
      this._dialog = null;
      this._menu = false;
      this._timers = {};
      this.shadowRoot.addEventListener("click", (event) => this._onClick(event));
      this.shadowRoot.addEventListener("change", (event) => this._onChange(event));
      this.shadowRoot.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && (this._dialog || this._menu)) {
          this._dialog = null;
          this._menu = false;
          this._render();
        } else if (event.key === "Enter" && event.target?.id === "d-name") {
          event.preventDefault();
          this._submitDialog();
        }
      });
    }

    static getConfigForm() {
      const fr = uiLang() === "fr";
      const labels = fr
        ? {
            title: "Titre",
            planning: "Planning ouvert au départ",
            colors: "Couleurs personnalisées",
            eco: "Éco",
            comfort: "Confort",
          }
        : {
            title: "Title",
            planning: "Schedule opened first",
            colors: "Custom colors",
            eco: "Eco",
            comfort: "Comfort",
          };
      const helpers = fr
        ? {
            title: "Par défaut : « Planning de chauffe ».",
            planning: "Nom ou numéro du planning. Vide : le premier planning modifiable.",
            colors: "Facultatif. Une couleur CSS, par exemple #fb8c00 ; vide = couleur par défaut.",
          }
        : {
            title: "Default: “Heating schedule”.",
            planning: "Name or number of the schedule. Empty: the first editable schedule.",
            colors: "Optional. A CSS color, for example #fb8c00; empty = default color.",
          };
      return {
        schema: [
          { name: "title", selector: { text: {} } },
          { name: "planning", selector: { text: {} } },
          {
            type: "expandable",
            name: "colors",
            title: labels.colors,
            schema: [
              { name: "eco", selector: { text: {} } },
              { name: "comfort", selector: { text: {} } },
            ],
          },
        ],
        computeLabel: (schema) => labels[schema.name] ?? schema.name,
        computeHelper: (schema) => helpers[schema.name],
      };
    }

    static getStubConfig() {
      return {};
    }

    setConfig(config) {
      const previous = this._config;
      this._config = { ...(config || {}) };
      this._render();
      if (!this._hass) return;
      // l'éditeur visuel change « planning » à la volée : on ouvre le planning demandé
      if (this._started && previous && String(previous.planning ?? "") !== String(this._config.planning ?? "")) {
        this._load();
      } else {
        this._start();
      }
    }

    getCardSize() {
      return 9;
    }

    getGridOptions() {
      return { columns: 12, rows: 9, min_columns: 6, min_rows: 7 };
    }

    set hass(hass) {
      const lang = (hass.language || "fr").slice(0, 2);
      const changed = lang !== this._lang;
      this._hass = hass;
      this._lang = lang;
      if (this._config) this._start();
      if (changed) this._render();
    }

    connectedCallback() {
      if (typeof ResizeObserver === "undefined" || this._observer) return;
      this._observer = new ResizeObserver(() => this._fitLabels());
      this._observer.observe(this);
    }

    disconnectedCallback() {
      Object.values(this._timers).forEach(clearTimeout);
      this._timers = {};
      this._observer?.disconnect();
      this._observer = null;
    }

    // ------------------------------------------------------------ données

    _t() {
      return TEXTS[this._lang] || TEXTS.en;
    }

    _start() {
      if (this._started || !this._hass || !this._config) return;
      this._started = true;
      this._load();
    }

    _entryData(data) {
      return this._config.config_entry_id
        ? { ...data, config_entry_id: this._config.config_entry_id }
        : data;
    }

    async _service(service, data) {
      const result = await this._hass.callWS({
        type: "call_service",
        domain: DOMAIN,
        service,
        service_data: this._entryData(data),
        return_response: true,
      });
      return result?.response ?? result;
    }

    _apply(response, wantedId) {
      this._plannings = response.plannings || [];
      const planning = response.planning;
      if (planning) {
        this._current = {
          id: planning.id,
          name: planning.name,
          rooms: planning.rooms,
          days: DAY_KEYS.map((key) => {
            const day = (planning.days || []).find((item) => item.day === key);
            return (day?.ranges || []).map((r) => [parse(r.start), parse(r.end)]);
          }),
        };
      } else if (wantedId === null) {
        this._current = null;
      }
    }

    _initialPlanning() {
      const editable = this._plannings.filter((p) => p.editable);
      const wanted = this._config.planning;
      if (wanted !== undefined && wanted !== null && wanted !== "") {
        const text = String(wanted).trim().toLowerCase();
        const found = editable.find(
          (p) => p.id === text || String(p.name).toLowerCase() === text
        );
        if (found) return found.id;
      }
      return editable[0]?.id ?? null;
    }

    async _run(label, task) {
      this._busy = label;
      this._notice = null;
      this._render();
      try {
        await task();
        return true;
      } catch (err) {
        this._fail(err);
        return false;
      } finally {
        this._busy = "";
        this._render();
      }
    }

    _fail(err) {
      const text = err?.message || err?.error || (typeof err === "string" ? err : JSON.stringify(err));
      this._notice = { kind: "error", text };
    }

    _toast(text) {
      this._notice = { kind: "info", text };
      clearTimeout(this._timers.notice);
      this._timers.notice = setTimeout(() => {
        if (this._notice?.kind === "info") {
          this._notice = null;
          this._render();
        }
      }, 6000);
      this._render();
    }

    async _load(planningId) {
      const t = this._t();
      await this._run(t.loading, async () => {
        let id = planningId;
        if (id === undefined) {
          this._apply(await this._service("get_planning", {}), null);
          id = this._initialPlanning();
        }
        if (id) this._apply(await this._service("get_planning", { planning: id }));
        else this._current = null;
      });
    }

    async _reload() {
      // relecture silencieuse après une erreur : montre l'état réel de la box
      if (!this._current) return;
      try {
        this._apply(await this._service("get_planning", { planning: this._current.id }));
      } catch (err) {
        // l'erreur d'origine reste affichée
      }
      this._render();
    }

    async _write(service, data) {
      const t = this._t();
      const ok = await this._run(t.saving, async () => {
        this._apply(await this._service(service, data));
      });
      if (!ok) await this._reload();
      return ok;
    }

    // ------------------------------------------------------------ affichage

    _colors() {
      return { ...DEFAULT_COLORS, ...cleanColors(this._config?.colors) };
    }

    _dayName(index, style = "short") {
      // le 1er janvier 2024 est un lundi
      return new Date(2024, 0, 1 + index).toLocaleDateString(this._lang, { weekday: style });
    }

    _render() {
      if (!this._config) return;
      const t = this._t();
      const colors = this._colors();
      const current = this._current;

      const options = this._plannings
        .filter((p) => p.editable)
        .map(
          (p) => `<option value="${esc(p.id)}"${current && p.id === current.id ? " selected" : ""}>${esc(p.name)}</option>`
        )
        .join("");

      let body;
      if (!current) {
        body = this._busy ? "" : `<div class="empty">${esc(t.noPlannings)}</div>`;
      } else {
        const now = new Date();
        const today = (now.getDay() + 6) % 7;
        const nowFraction = (now.getHours() * 60 + now.getMinutes()) / 1440;
        const ticks = [0, 3, 6, 9, 12, 15, 18, 21, 24]
          .map((h) => `<span style="left:${(h / 24) * 100}%">${pad(h)}</span>`)
          .join("");
        const rows = current.days
          .map((ranges, day) => {
            const segs = ranges
              .map(
                ([start, end]) =>
                  `<div class="seg" data-s="${fmt(start)}" data-e="${fmt(end)}"
                        style="left:${(start / QUARTERS) * 100}%;width:${((end - start) / QUARTERS) * 100}%"></div>`
              )
              .join("");
            const times = ranges.map(([start, end]) => `${fmt(start)}–${fmt(end)}`).join(" · ");
            const marker = day === today ? `<div class="now" style="left:${nowFraction * 100}%"></div>` : "";
            return `<div class="row${day === today ? " today" : ""}">
                <div class="dname">${esc(this._dayName(day))}</div>
                <div class="bar" data-day="${day}" role="button" aria-label="${esc(this._dayName(day, "long"))}">${segs}${marker}</div>
                <button class="copy" data-act="copy" data-day="${day}" title="${esc(t.copy)}" aria-label="${esc(t.copy)}">⧉</button>
                <div class="times">${esc(times)}</div>
              </div>`;
          })
          .join("");
        body = `<div class="rooms">${esc(
          current.rooms ? t.rooms.replace("{rooms}", current.rooms) : t.noRooms
        )}</div>
          <div class="scale"><span></span><div class="ticks">${ticks}</div><span></span></div>
          ${rows}
          <div class="legend"><span><span class="dot" style="background:color-mix(in srgb, var(--eco) 55%, transparent)"></span>${esc(t.eco)}</span>
            <span><span class="dot" style="background:var(--comfort)"></span>${esc(t.comfort)}</span></div>
          <div class="hint">${esc(t.hint)}</div>`;
      }

      const notice = this._notice
        ? `<div class="banner ${this._notice.kind}" role="${this._notice.kind === "error" ? "alert" : "status"}">${esc(this._notice.text)}
             ${this._notice.kind === "error" && !current ? `<button class="btn" data-act="retry">${esc(t.retry)}</button>` : ""}</div>`
        : "";

      const menu = this._menu
        ? `<div class="menu" role="menu">
             <button data-act="new-planning">${esc(t.newPlanning)}</button>
             <button data-act="rename-planning" ${current ? "" : "disabled"}>${esc(t.renamePlanning)}</button>
             <button class="danger" data-act="delete-planning" ${current ? "" : "disabled"}>${esc(t.deletePlanning)}</button>
           </div>`
        : "";

      const busy = this._busy
        ? `<div class="busy" role="status"><div class="spinner"></div>${esc(this._busy)}</div>`
        : "";

      this.shadowRoot.innerHTML = `<style>${STYLE}</style>
        <ha-card style="--eco:${esc(colors.eco)};--comfort:${esc(colors.comfort)};--on-comfort:${onColor(colors.comfort)}">
          <div class="head">
            <div class="title">${esc(this._config.title || t.title)}</div>
            <button class="icon" data-act="refresh" title="${esc(t.refresh)}" aria-label="${esc(t.refresh)}">↻</button>
            <button class="icon" data-act="menu" title="${esc(t.menu)}" aria-label="${esc(t.menu)}" aria-haspopup="true">⋮</button>
          </div>
          ${menu}
          ${options ? `<select class="planning-select" id="planning" aria-label="${esc(t.planning)}">${options}</select>` : ""}
          ${body}
          ${notice}
          ${this._renderDialog()}
          ${busy}
        </ha-card>`;

      this._fitLabels();
      const focus = this.shadowRoot.querySelector("[data-autofocus]");
      if (focus && this._dialog) focus.focus();
    }

    // Écrit les heures dans les plages quand elles tiennent ; sur une carte étroite,
    // elles sont écrites sous chaque barre.
    _fitLabels() {
      const card = this.shadowRoot.querySelector("ha-card");
      if (!card) return;
      const compact = card.clientWidth > 0 && card.clientWidth < COMPACT_WIDTH;
      card.classList.toggle("compact", compact);
      this.shadowRoot.querySelectorAll(".seg").forEach((seg) => {
        const width = seg.getBoundingClientRect().width;
        const a = seg.dataset.s;
        const b = seg.dataset.e;
        seg.classList.remove("two");
        if (compact || width < 36) seg.textContent = "";
        else if (width < 88) {
          seg.classList.add("two");
          seg.textContent = `${a}\n${b}`;
        } else seg.textContent = `${a}–${b}`;
        seg.title = `${a}–${b}`;
      });
    }

    _timeOptions(from, to, selected) {
      let html = "";
      for (let q = from; q <= to; q++) {
        html += `<option value="${q}"${q === selected ? " selected" : ""}>${fmt(q)}</option>`;
      }
      return html;
    }

    _renderDialog() {
      const dialog = this._dialog;
      if (!dialog) return "";
      const t = this._t();
      let title = "";
      let content = "";
      let actions = "";

      if (dialog.type === "range") {
        title = dialog.mode === "add" ? t.addRange : t.editRange;
        const day = this._dayName(dialog.day, "long");
        content = `<div class="note" style="margin:0 0 10px;text-transform:capitalize">${esc(day)}</div>
          <div class="fields">
            <label>${esc(t.from)}
              <select id="d-start" data-autofocus>${this._timeOptions(dialog.minStart, dialog.maxEnd - MIN_RANGE, dialog.start)}</select></label>
            <label>${esc(t.to)}
              <select id="d-end">${this._timeOptions(dialog.minStart + MIN_RANGE, dialog.maxEnd, dialog.end)}</select></label>
          </div>
          ${dialog.error ? `<div class="error-text">${esc(dialog.error)}</div>` : ""}`;
        actions = `${dialog.mode === "edit" ? `<button class="btn danger left" data-act="dialog-delete">${esc(t.remove)}</button>` : ""}
          <button class="btn" data-act="dialog-cancel">${esc(t.cancel)}</button>
          <button class="btn primary" data-act="dialog-ok">${esc(t.save)}</button>`;
      } else if (dialog.type === "copy") {
        title = t.copyTitle.replace("{day}", this._dayName(dialog.day, "long"));
        const checks = DAY_KEYS.map((key, index) =>
          index === dialog.day
            ? ""
            : `<label><input type="checkbox" data-target="${index}"${dialog.targets.includes(index) ? " checked" : ""}>${esc(this._dayName(index, "long"))}</label>`
        ).join("");
        content = `<div class="checks">${checks}</div>
          <div class="note"><button class="btn" data-act="copy-all" style="padding:4px 10px">${esc(t.allDays)}</button></div>`;
        actions = `<button class="btn left" data-act="dialog-clear">${esc(t.clearDay)}</button>
          <button class="btn" data-act="dialog-cancel">${esc(t.cancel)}</button>
          <button class="btn primary" data-act="dialog-ok" ${dialog.targets.length ? "" : "disabled"}>${esc(t.copyAction)}</button>`;
      } else if (dialog.type === "name") {
        title = dialog.mode === "create" ? t.newPlanning : t.renamePlanning;
        content = `<label style="display:block">${esc(t.nameLabel)}
            <input type="text" id="d-name" data-autofocus maxlength="${NAME_MAX}" value="${esc(dialog.name)}" style="width:100%;box-sizing:border-box;margin-top:6px"></label>
          <div class="note">${esc(t.nameHint.replace("{max}", NAME_MAX))}</div>`;
        actions = `<button class="btn" data-act="dialog-cancel">${esc(t.cancel)}</button>
          <button class="btn primary" data-act="dialog-ok">${esc(dialog.mode === "create" ? t.create : t.rename)}</button>`;
      } else if (dialog.type === "delete") {
        title = t.deletePlanning;
        content = `<div>${esc(t.confirmDelete.replace("{name}", dialog.name))}</div>`;
        actions = `<button class="btn" data-act="dialog-cancel">${esc(t.cancel)}</button>
          <button class="btn danger" data-act="dialog-ok">${esc(t.remove)}</button>`;
      }

      return `<div class="modal" data-act="dialog-backdrop"><div class="dialog" role="dialog" aria-modal="true" aria-label="${esc(title)}">
          <h3>${esc(title)}</h3>${content}<div class="actions">${actions}</div></div></div>`;
    }

    // ------------------------------------------------------------ interactions

    _onClick(event) {
      const bar = event.target.closest(".bar");
      if (bar && !this._busy && !this._dialog) {
        this._clickBar(bar, event);
        return;
      }
      const target = event.target.closest("[data-act]");
      if (!target) {
        if (this._menu) {
          this._menu = false;
          this._render();
        }
        return;
      }
      if (target.dataset.act === "dialog-backdrop" && event.target !== target) return;
      if (this._busy) return;
      switch (target.dataset.act) {
        case "refresh":
          this._load(this._current?.id ?? undefined);
          break;
        case "retry":
          this._load(this._current?.id ?? undefined);
          break;
        case "menu":
          this._menu = !this._menu;
          this._render();
          break;
        case "new-planning":
          this._menu = false;
          this._dialog = { type: "name", mode: "create", name: "" };
          this._render();
          break;
        case "rename-planning":
          this._menu = false;
          this._dialog = { type: "name", mode: "rename", name: this._current?.name ?? "" };
          this._render();
          break;
        case "delete-planning":
          this._menu = false;
          this._dialog = { type: "delete", name: this._current?.name ?? "" };
          this._render();
          break;
        case "copy":
          if (!this._current) break;
          this._dialog = { type: "copy", day: Number(target.dataset.day), targets: [] };
          this._render();
          break;
        case "copy-all":
          this._dialog.targets = DAY_KEYS.map((_, i) => i).filter((i) => i !== this._dialog.day);
          this._render();
          break;
        case "dialog-cancel":
        case "dialog-backdrop":
          this._dialog = null;
          this._render();
          break;
        case "dialog-ok":
          this._submitDialog();
          break;
        case "dialog-delete":
          this._removeRange();
          break;
        case "dialog-clear":
          this._clearDay();
          break;
        default:
      }
    }

    _onChange(event) {
      const el = event.target;
      if (el.id === "planning") {
        this._load(el.value);
      } else if (el.id === "d-start" || el.id === "d-end") {
        const d = this._dialog;
        if (!d || d.type !== "range") return;
        d.start = Number(this.shadowRoot.getElementById("d-start").value);
        d.end = Number(this.shadowRoot.getElementById("d-end").value);
        if (d.end < d.start + MIN_RANGE) d.end = Math.min(d.maxEnd, d.start + MIN_RANGE);
        d.error = "";
        this._render();
      } else if (el.dataset?.target !== undefined && this._dialog?.type === "copy") {
        const index = Number(el.dataset.target);
        const targets = new Set(this._dialog.targets);
        if (el.checked) targets.add(index);
        else targets.delete(index);
        this._dialog.targets = [...targets].sort();
        this._render();
      }
    }

    _clickBar(bar, event) {
      if (!this._current) return;
      const t = this._t();
      const day = Number(bar.dataset.day);
      const rect = bar.getBoundingClientRect();
      const fraction = rect.width ? (event.clientX - rect.left) / rect.width : 0;
      const q = Math.max(0, Math.min(QUARTERS - 1, Math.floor(fraction * QUARTERS)));
      const ranges = this._current.days[day];
      const index = ranges.findIndex(([start, end]) => q >= start && q < end);
      this._menu = false;

      if (index >= 0) {
        // modifier une plage : bornée par ses voisines (1 h d'écart)
        this._dialog = {
          type: "range",
          mode: "edit",
          day,
          index,
          minStart: index > 0 ? ranges[index - 1][1] + MIN_GAP : 0,
          maxEnd: index < ranges.length - 1 ? ranges[index + 1][0] - MIN_GAP : QUARTERS,
          start: ranges[index][0],
          end: ranges[index][1],
          error: "",
        };
      } else {
        if (ranges.length >= MAX_RANGES) {
          this._toast(t.tooMany);
          return;
        }
        const before = ranges.filter(([, end]) => end <= q).pop();
        const after = ranges.find(([start]) => start > q);
        const minStart = before ? before[1] + MIN_GAP : 0;
        const maxEnd = after ? after[0] - MIN_GAP : QUARTERS;
        if (maxEnd - minStart < MIN_RANGE) {
          this._toast(t.noSpace);
          return;
        }
        const start = Math.max(minStart, Math.min(q, maxEnd - MIN_RANGE));
        this._dialog = {
          type: "range",
          mode: "add",
          day,
          index: -1,
          minStart,
          maxEnd,
          start,
          end: start + MIN_RANGE,
          error: "",
        };
      }
      this._render();
    }

    _dayPayload(day, ranges) {
      return {
        planning: this._current.id,
        days: [DAY_KEYS[day]],
        ranges: ranges.map(([start, end]) => ({ start: fmt(start), end: fmt(end) })),
      };
    }

    async _submitDialog() {
      const d = this._dialog;
      if (!d) return;
      const t = this._t();

      if (d.type === "range") {
        if (d.end - d.start < MIN_RANGE || d.start < d.minStart || d.end > d.maxEnd) {
          d.error = t.invalidRange;
          this._render();
          return;
        }
        const ranges = this._current.days[d.day].map((r) => [...r]);
        if (d.mode === "add") ranges.push([d.start, d.end]);
        else ranges[d.index] = [d.start, d.end];
        ranges.sort((a, b) => a[0] - b[0]);
        this._dialog = null;
        if (sameRanges(ranges, this._current.days[d.day])) {
          this._render();
          return;
        }
        await this._write("set_planning_day", this._dayPayload(d.day, ranges));
      } else if (d.type === "copy") {
        const targets = d.targets.filter((i) => i !== d.day);
        this._dialog = null;
        if (!targets.length) {
          this._render();
          return;
        }
        await this._write("copy_planning_day", {
          planning: this._current.id,
          source_day: DAY_KEYS[d.day],
          target_days: targets.map((i) => DAY_KEYS[i]),
        });
      } else if (d.type === "name") {
        const input = this.shadowRoot.getElementById("d-name");
        const name = (input ? input.value : d.name).trim();
        if (!name) return;
        this._dialog = null;
        if (d.mode === "create") {
          await this._write("create_planning", { name });
        } else if (this._current) {
          const ok = await this._write("rename_planning", { planning: this._current.id, name });
          if (ok) await this._load(this._current.id); // le nom affiché vient de la relecture
        }
      } else if (d.type === "delete") {
        const id = this._current?.id;
        this._dialog = null;
        if (!id) return;
        const ok = await this._write("delete_planning", { planning: id });
        if (ok) {
          this._current = null;
          const next = this._plannings.find((p) => p.editable);
          if (next) await this._load(next.id);
          else this._render();
        }
      }
    }

    async _removeRange() {
      const d = this._dialog;
      if (!d || d.type !== "range" || d.mode !== "edit") return;
      const ranges = this._current.days[d.day].filter((_, i) => i !== d.index);
      this._dialog = null;
      await this._write("set_planning_day", this._dayPayload(d.day, ranges));
    }

    async _clearDay() {
      const d = this._dialog;
      if (!d || d.type !== "copy") return;
      this._dialog = null;
      if (!this._current.days[d.day].length) {
        this._render();
        return;
      }
      await this._write("set_planning_day", this._dayPayload(d.day, []));
    }
  }

  customElements.define(TAG, SmartEcoControlPlanningCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: TAG,
    name: "Smart ECOcontrol : planning de chauffe",
    description: "Éditeur des plannings de la box : plages de confort par jour, copie, création.",
  });
})();
