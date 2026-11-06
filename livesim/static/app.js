/* LiveSim dashboard - dependency-free single-page app. All server text is inserted with textContent. */
"use strict";
(() => {
  // ---------------------------------------------------------------- utilities
  const $ = (selector, root = document) => root.querySelector(selector);
  const SVG_NS = "http://www.w3.org/2000/svg";
  const DOM_PROPS = new Set(["value", "checked", "disabled", "selected", "open", "hidden", "indeterminate"]);

  const storage = {
    get(key, fallback = null) {
      try { const v = localStorage.getItem("livesim." + key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem("livesim." + key, JSON.stringify(value)); } catch { /* private mode */ }
    },
  };

  function append(el, kids) {
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  // Like el.replaceChildren, but skips null/false and flattens arrays (the native call would stringify them).
  function fill(el, ...kids) {
    el.replaceChildren();
    return append(el, kids);
  }

  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") el.className = value;
      else if (key === "text") el.textContent = value;
      else if (key === "dataset") Object.assign(el.dataset, value);
      else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
      else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
      else if (DOM_PROPS.has(key)) el[key] = value;
      else el.setAttribute(key, value === true ? "" : value);
    }
    return append(el, kids);
  }

  function s(tag, attrs, ...kids) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value !== null && value !== undefined) el.setAttribute(key, value);
    }
    return append(el, kids);
  }

  const ICONS = {
    play: { d: "M5 3.2v9.6l7.6-4.8z", fill: true },
    pause: { d: "M4.5 3h2.6v10H4.5zM8.9 3h2.6v10H8.9z", fill: true },
    copy: { d: "M5.5 5.5V3.8c0-.7.6-1.3 1.3-1.3h5.4c.7 0 1.3.6 1.3 1.3v5.4c0 .7-.6 1.3-1.3 1.3h-1.7M3.8 5.5h5.4c.7 0 1.3.6 1.3 1.3v5.4c0 .7-.6 1.3-1.3 1.3H3.8c-.7 0-1.3-.6-1.3-1.3V6.8c0-.7.6-1.3 1.3-1.3z" },
    theme: { d: "M8 2a6 6 0 1 0 0 12z", fill: true, ring: true },
    arrow: { d: "M3 8h9M8.5 4.5 12 8l-3.5 3.5" },
  };
  function icon(name) {
    const spec = ICONS[name];
    const svg = s("svg", { viewBox: "0 0 16 16", "aria-hidden": "true" });
    if (spec.ring) svg.append(s("circle", { cx: 8, cy: 8, r: 6, fill: "none", stroke: "currentColor", "stroke-width": 1.5 }));
    svg.append(s("path", spec.fill ? { d: spec.d, fill: "currentColor" } : { d: spec.d, fill: "none", stroke: "currentColor", "stroke-width": 1.5, "stroke-linecap": "round", "stroke-linejoin": "round" }));
    return svg;
  }

  const nf0 = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
  const compactFormat = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
  function compact(value) {
    if (value === null || value === undefined || Number.isNaN(value)) return "–";
    return Math.abs(value) >= 10000 ? compactFormat.format(value) : nf0.format(value);
  }
  function smart(value) {
    if (value === null || value === undefined || !Number.isFinite(value)) return "–";
    const a = Math.abs(value);
    if (a >= 10000) return compactFormat.format(value);
    if (a >= 100) return nf0.format(value);
    if (a >= 10) return value.toFixed(1);
    return value.toFixed(2);
  }
  const percent = (value) => (value === null || value === undefined ? "–" : `${(value * 100).toFixed(2)}%`);
  function bytes(n) {
    if (!n) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
    return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
  }
  function ago(isoText) {
    if (!isoText) return "never";
    const secs = Math.max(0, Math.round((Date.now() - Date.parse(isoText)) / 1000));
    if (secs < 60) return `${secs}s ago`;
    if (secs < 3600) return `${Math.round(secs / 60)} min ago`;
    return `${Math.round(secs / 3600)} h ago`;
  }

  function toast(message, isError = false) {
    const el = $("#toast");
    el.textContent = message;
    el.classList.toggle("error", isError);
    el.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => el.classList.remove("show"), isError ? 5000 : 2200);
  }

  async function copyText(text, label = "Copied") {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const area = h("textarea", { style: { position: "fixed", opacity: "0" } });
      area.value = text;
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    toast(label);
  }

  function copyButton(getText, label = "Copy") {
    return h("button", { class: "btn btn-small copy", type: "button", onclick: () => copyText(typeof getText === "function" ? getText() : getText) }, icon("copy"), label);
  }

  function codeBlock(code, lang = "") {
    return h("div", { class: "code" },
      h("div", { class: "code-head" }, h("span", { class: "lang", text: lang }), copyButton(code)),
      h("pre", null, h("code", { text: code })));
  }

  // ---------------------------------------------------------------- API
  function apiKey() { return storage.get("apiKey", ""); }
  function withKey(url) {
    const key = apiKey();
    if (!key) return url;
    return url + (url.includes("?") ? "&" : "?") + "api_key=" + encodeURIComponent(key);
  }
  function errorText(data) {
    if (!data) return "";
    if (typeof data === "string") return data;
    const detail = data.detail;
    if (Array.isArray(detail)) return detail.map((e) => `${(e.loc || []).slice(1).join(".")}: ${e.msg}`).join("; ");
    return detail || (data.error && data.error.message) || "";
  }
  async function api(path, { method = "GET", body } = {}) {
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (apiKey()) headers["X-API-Key"] = apiKey();
    let response;
    try {
      response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch {
      throw new Error("Cannot reach the LiveSim server.");
    }
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (response.status === 401) showKeyBanner();
    if (!response.ok) throw new Error(errorText(data) || `${response.status} ${response.statusText}`);
    return data;
  }

  function showKeyBanner() {
    if ($("#key-banner")) return;
    const input = h("input", { type: "password", placeholder: "API key", "aria-label": "API key", style: { width: "260px" } });
    const banner = h("div", { class: "card key-banner", id: "key-banner" },
      h("strong", { text: "This server requires an API key." }),
      input,
      h("button", { class: "btn btn-primary", type: "button", onclick: () => { storage.set("apiKey", input.value.trim()); location.reload(); } }, "Save key"));
    $("#view").prepend(banner);
  }

  // ---------------------------------------------------------------- shared state & polling
  const state = { streams: [], byName: {}, overview: null, online: true, view: null, viewName: null };

  async function refresh() {
    try {
      const [streams, overview] = await Promise.all([api("/api/v1/streams"), api("/api/v1/overview")]);
      state.streams = streams;
      state.byName = Object.fromEntries(streams.map((st) => [st.name, st]));
      state.overview = overview;
      state.online = true;
    } catch (error) {
      state.online = false;
      state.error = error.message;
    }
    updateTopbar();
    if (state.view && state.view.update) {
      try { state.view.update(); } catch (error) { console.error(error); }
    }
  }
  async function pollLoop() {
    await refresh();
    setTimeout(pollLoop, document.hidden ? 5000 : 1500);
  }

  function statusPill(kind, label) {
    return h("span", { class: `status ${kind}` }, h("span", { class: "dot", "aria-hidden": "true" }), label);
  }
  function streamStatus(st) {
    return st.config.running ? statusPill("live", "Live") : statusPill("paused", "Paused");
  }

  async function patchStream(name, patch, message) {
    try {
      const updated = await api(`/api/v1/streams/${name}`, { method: "PATCH", body: patch });
      if (state.byName[name]) Object.assign(state.byName[name], { config: updated.config });
      if (message) toast(message);
      refresh();
      return updated;
    } catch (error) {
      toast(error.message, true);
      throw error;
    }
  }

  function updateTopbar() {
    const badge = $("#global-rate");
    fill(badge);
    if (!state.online) {
      badge.append(statusPill("error", "Server unreachable"));
    } else if (state.overview) {
      const o = state.overview;
      badge.append(statusPill(o.streams_running ? "live" : "paused", `${smart(o.records_per_sec)} records/s · ${o.streams_running}/${o.streams_total} live`));
    }
  }

  // ---------------------------------------------------------------- charts
  function niceScale(lo, hi, ticks = 4, asPercent = false) {
    const range = hi - lo || Math.abs(hi) || 1;
    const raw = range / ticks;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const norm = raw / mag;
    const step = +((norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag).toPrecision(12);
    const min = Math.floor(lo / step) * step;
    const max = Math.ceil(hi / step) * step;
    const decimals = (String(step).split(".")[1] || "").length;
    const big = Math.max(Math.abs(min), Math.abs(max)) >= 10000;
    const pctDecimals = Math.max(0, decimals - 2);
    const fmt = (v) => {
      if (asPercent) return `${(v * 100).toFixed(pctDecimals)}%`;  // the axis must read in the same unit as the values
      return big ? compactFormat.format(v) : v.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    };
    return { min, max: max === min ? min + step : max, step, fmt };
  }

  function sparkline(width = 132, height = 36) {
    const svg = s("svg", { class: "spark", width, height, viewBox: `0 0 ${width} ${height}`, role: "img" });
    const line = s("path", { class: "spark-line" });
    const dot = s("circle", { class: "spark-dot", r: 4 });
    svg.append(line, dot);
    return {
      el: svg,
      update(values, label) {
        const vals = values.slice(-60);
        const max = Math.max(...vals, 0.0001);
        const pad = 5;
        const x = (i) => pad + (i * (width - 2 * pad)) / Math.max(1, vals.length - 1);
        const y = (v) => height - pad - (v / max) * (height - 2 * pad);
        line.setAttribute("d", vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(""));
        dot.setAttribute("cx", x(vals.length - 1));
        dot.setAttribute("cy", y(vals[vals.length - 1] || 0));
        svg.setAttribute("aria-label", label);
      },
    };
  }

  function chartCard(title) {
    const HEIGHT = 196;
    const M = { l: 52, r: 70, t: 14, b: 26 };
    const nowValue = h("div", { class: "now" }, "–");
    const subtitle = h("div", { class: "subtle" });
    const tableToggle = h("button", { class: "link-button", type: "button", "aria-pressed": "false" }, "Table view");
    const tooltip = h("div", { class: "tooltip", hidden: true }, h("strong"), h("span"));
    const svg = s("svg", { role: "img", tabindex: "0", height: HEIGHT });
    const chart = h("div", { class: "chart" }, svg, tooltip);
    const tableHost = h("div");
    const card = h("section", { class: "card chart-card" },
      h("div", { class: "card-head" }, h("div", null, h("h3", { text: title }), subtitle), h("div", { style: { textAlign: "right" } }, nowValue, tableToggle)),
      chart, tableHost);
    let data = { values: [], format: smart, zero: true, unit: "" };
    let geometry = null;
    let hover = null;
    let showTable = false;
    let crosshair = null;
    let marker = null;

    tableToggle.addEventListener("click", () => {
      showTable = !showTable;
      tableToggle.textContent = showTable ? "Chart view" : "Table view";
      tableToggle.setAttribute("aria-pressed", String(showTable));
      render();
    });

    function lastIndex(values) {
      for (let i = values.length - 1; i >= 0; i--) if (values[i] !== null && Number.isFinite(values[i])) return i;
      return -1;
    }
    const label = (i, n) => (n - 1 - i === 0 ? "now" : `${n - 1 - i}s ago`);

    function renderTable() {
      const rows = [];
      for (let i = data.values.length - 1; i >= 0 && rows.length < 60; i--) {
        rows.push(h("tr", null, h("td", { text: label(i, data.values.length) }), h("td", { class: "num", text: data.values[i] === null ? "–" : data.format(data.values[i]) })));
      }
      return h("div", { class: "table-wrap", style: { maxHeight: `${HEIGHT}px` } },
        h("table", null, h("thead", null, h("tr", null, h("th", { text: "Time" }), h("th", { class: "num", text: title }))), h("tbody", null, rows)));
    }

    function showHover(index) {
      if (!geometry) return;
      const { x, y, n, values } = geometry;
      hover = Math.max(0, Math.min(n - 1, index));
      const value = values[hover];
      const px = x(hover);
      crosshair.setAttribute("x1", px);
      crosshair.setAttribute("x2", px);
      crosshair.setAttribute("visibility", "visible");
      const hasValue = value !== null && Number.isFinite(value);
      const py = hasValue ? y(value) : M.t + 10;
      marker.setAttribute("cx", px);
      marker.setAttribute("cy", py);
      marker.setAttribute("visibility", hasValue ? "visible" : "hidden");
      tooltip.hidden = false;
      tooltip.firstChild.textContent = hasValue ? data.format(value) : "no data";
      tooltip.lastChild.textContent = label(hover, n);
      tooltip.style.left = `${Math.max(60, Math.min(px, geometry.width - 60))}px`;
      tooltip.style.top = `${py}px`;
    }
    function hideHover() {
      hover = null;
      tooltip.hidden = true;
      if (crosshair) crosshair.setAttribute("visibility", "hidden");
      if (marker) marker.setAttribute("visibility", "hidden");
    }

    function render() {
      const values = data.values;
      chart.hidden = showTable;
      fill(tableHost, showTable ? renderTable() : "");
      if (showTable) return;
      const width = Math.max(260, chart.clientWidth || 600);
      svg.setAttribute("viewBox", `0 0 ${width} ${HEIGHT}`);
      fill(svg);
      const finite = values.filter((v) => v !== null && Number.isFinite(v));
      if (!finite.length) {
        svg.append(s("text", { class: "tick", x: width / 2, y: HEIGHT / 2, "text-anchor": "middle" }, "Waiting for data..."));
        geometry = null;
        return;
      }
      let lo = data.zero ? Math.min(0, ...finite) : Math.min(...finite);
      let hi = Math.max(...finite);
      if (hi === lo) {
        hi += Math.abs(hi) * 0.1 || (data.format === percent ? 0.05 : 1);  // all-zero shares get a 0-5% axis, not 0-100%
        if (!data.zero) lo -= Math.abs(lo) * 0.1 || 1;
      }
      const scale = niceScale(lo, hi, 4, data.format === percent);
      const pw = width - M.l - M.r;
      const ph = HEIGHT - M.t - M.b;
      const n = values.length;
      const x = (i) => M.l + (n <= 1 ? pw : (i * pw) / (n - 1));
      const y = (v) => M.t + ph - ((v - scale.min) / (scale.max - scale.min)) * ph;
      const steps = Math.round((scale.max - scale.min) / scale.step);
      for (let k = 0; k <= steps; k++) {
        const v = scale.min + k * scale.step;
        const yy = Math.round(y(v)) + 0.5;
        svg.append(s("line", { class: k === 0 ? "baseline" : "gridline", x1: M.l, x2: M.l + pw, y1: yy, y2: yy }));
        svg.append(s("text", { class: "tick", x: M.l - 8, y: yy + 4, "text-anchor": "end" }, scale.fmt(v)));
      }
      for (let i = n - 1; i >= 0; i -= 30) {
        const secondsAgo = n - 1 - i;
        const text = secondsAgo === 0 ? "now" : secondsAgo % 60 === 0 ? `${secondsAgo / 60} min ago` : `${secondsAgo}s`;
        svg.append(s("text", { class: "tick", x: x(i), y: HEIGHT - 6, "text-anchor": i === n - 1 ? "end" : "middle" }, text));
      }
      let line = "";
      let area = "";
      let segment = [];
      const base = y(Math.max(scale.min, 0));
      const flush = () => {
        if (segment.length) {
          line += segment.map((p, k) => `${k ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
          if (data.zero) area += `M${segment[0][0].toFixed(1)},${base}` + segment.map((p) => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("") + `L${segment[segment.length - 1][0].toFixed(1)},${base}Z`;
        }
        segment = [];
      };
      values.forEach((v, i) => { if (v === null || !Number.isFinite(v)) flush(); else segment.push([x(i), y(v)]); });
      flush();
      if (area) svg.append(s("path", { class: "area", d: area }));
      svg.append(s("path", { class: "line", d: line }));
      const li = lastIndex(values);
      if (li >= 0) {
        svg.append(s("circle", { class: "marker", cx: x(li), cy: y(values[li]), r: 4 }));
        svg.append(s("text", { class: "end-label", x: x(li) + 9, y: y(values[li]) + 4 }, data.format(values[li])));
      }
      crosshair = s("line", { class: "crosshair", y1: M.t, y2: M.t + ph, visibility: "hidden" });
      marker = s("circle", { class: "marker", r: 4, visibility: "hidden" });
      svg.append(crosshair, marker, s("rect", { x: M.l, y: 0, width: pw, height: HEIGHT, fill: "transparent" }));
      geometry = { x, y, n, values, width, pw };
      svg.setAttribute("aria-label", `${title}: latest ${li >= 0 ? data.format(values[li]) : "no data"}. Use the arrow keys to read values, or switch to the table view.`);
      if (hover !== null) showHover(hover);
    }

    svg.addEventListener("pointermove", (event) => {
      if (!geometry) return;
      const rect = svg.getBoundingClientRect();
      const px = ((event.clientX - rect.left) * geometry.width) / rect.width;
      showHover(Math.round(((px - M.l) / geometry.pw) * (geometry.n - 1)));
    });
    svg.addEventListener("pointerleave", hideHover);
    svg.addEventListener("blur", hideHover);
    svg.addEventListener("focus", () => geometry && showHover(geometry.n - 1));
    svg.addEventListener("keydown", (event) => {
      if (!geometry) return;
      if (event.key === "ArrowLeft") { showHover((hover ?? geometry.n - 1) - 1); event.preventDefault(); }
      else if (event.key === "ArrowRight") { showHover((hover ?? geometry.n - 1) + 1); event.preventDefault(); }
      else if (event.key === "Escape") hideHover();
    });
    new ResizeObserver(() => render()).observe(chart);

    return {
      el: card,
      update(values, options = {}) {
        data = { ...data, ...options, values };
        subtitle.textContent = options.subtitle || subtitle.textContent;
        const current = options.current !== undefined ? options.current : values[lastIndex(values)];
        nowValue.textContent = current === null || current === undefined ? "–" : data.format(current);
        render();
      },
    };
  }

  function mean(values) {
    const finite = values.filter((v) => v !== null && Number.isFinite(v));
    return finite.length ? finite.reduce((a, b) => a + b, 0) / finite.length : null;
  }

  // ---------------------------------------------------------------- generic pieces
  function pageHead(title, description, ...actions) {
    return h("div", { class: "page-head" }, h("div", null, h("h1", { text: title }), description ? h("p", { text: description }) : null), actions.length ? h("div", { class: "row" }, actions) : null);
  }
  function streamSelect(current, onChange) {
    const select = h("select", { "aria-label": "Stream", onchange: (e) => onChange(e.target.value) },
      state.streams.map((st) => h("option", { value: st.name, selected: st.name === current }, st.title)));
    return select;
  }
  function tabs(names, onSelect) {
    const buttons = names.map((name, i) => h("button", { type: "button", role: "tab", "aria-selected": String(i === 0), onclick: () => select(i) }, name));
    const bar = h("div", { class: "subtabs", role: "tablist" }, buttons);
    function select(i) {
      buttons.forEach((b, k) => b.setAttribute("aria-selected", String(k === i)));
      onSelect(i);
    }
    return { el: bar, select };
  }
  function streamChips(selected) {
    const chosen = new Set(selected || []);
    const buttons = state.streams.map((st) => h("button", {
      type: "button", class: "chip", "aria-pressed": String(chosen.has(st.name)),
      onclick: (e) => {
        const on = e.currentTarget.getAttribute("aria-pressed") !== "true";
        e.currentTarget.setAttribute("aria-pressed", String(on));
        on ? chosen.add(st.name) : chosen.delete(st.name);
      },
    }, st.title));
    return { el: h("div", { class: "chips" }, buttons), value: () => (chosen.size ? [...chosen] : null) };
  }
  function cellValue(value) {
    if (value === null || value === undefined) return h("td", { class: "null", text: "null" });
    if (typeof value === "number") return h("td", { class: "num", text: String(value) });
    if (typeof value === "boolean") return h("td", { text: String(value) });
    const text = String(value);
    return h("td", { class: text.length > 60 ? "wrap" : null, text });
  }
  function resultTable(columns, rows, freshFrom = Infinity, seqIndex = -1) {
    return h("table", null,
      h("thead", null, h("tr", null, columns.map((c) => h("th", { text: c })))),
      h("tbody", null, rows.map((row) => h("tr", { class: seqIndex >= 0 && row[seqIndex] > freshFrom ? "fresh" : null }, row.map(cellValue)))));
  }

  // ---------------------------------------------------------------- Streams view
  function StreamsView(root) {
    const heroValue = h("div", { class: "value" }, "–");
    const tiles = {
      running: h("div", { class: "value" }, "–"),
      stored: h("div", { class: "value" }, "–"),
      size: h("div", { class: "value" }, "–"),
      subs: h("div", { class: "value" }, "–"),
    };
    const tile = (label, value, sub) => h("div", { class: "card tile" }, h("div", { class: "label", text: label }), value, sub ? h("div", { class: "sub", text: sub }) : null);
    root.append(
      pageHead("Live data streams", "Ten realistic, continuously generated datasets. Every stream is queryable, streamable and connectable: open one to explore it, or go to Connect for ready-made connection strings."),
      h("div", { class: "hero" },
        h("div", { class: "card hero-figure" }, h("div", { class: "label", text: "Records per second, all streams" }), heroValue),
        tile("Streams live", tiles.running),
        tile("Records stored", tiles.stored, "SQLite store, all streams"),
        tile("Database size", tiles.size),
        tile("Live subscribers", tiles.subs, "SSE, WebSocket and TCP clients")),
    );
    if (!storage.get("personasHidden", false)) {
      const persona = (title, text, links) => h("div", { class: "card persona" }, h("h3", { text: title }), h("p", { class: "subtle", text }),
        h("div", { class: "links" }, links.map(([label, href]) => h("a", { href }, label))));
      const box = h("div", { class: "personas" },
        persona("Data analyst", "Connect Power BI, Tableau or Excel to the OData feed and build dashboards on a star schema (facts + dim_* tables) that refreshes with new data.",
          [["Power BI guide", "#/connect/powerbi"], ["Tableau guide", "#/connect/tableau"], ["SQL Lab", "#/sql"]]),
        persona("Data engineer", "Build pipelines on live feeds: cursor-based REST pulls, SSE / WebSocket / TCP streams, Kafka topics, a CDC feed and a partitioned file landing zone.",
          [["Spark guide", "#/connect/spark"], ["Sinks", "#/sinks"], ["Inventory CDC", "#/explore/inventory_cdc"]]),
        persona("Data scientist", "Backfill weeks of labelled data - fraud, machine failures, sentiment, demand - then score models against the live stream.",
          [["Python guide", "#/connect/python"], ["Card fraud", "#/explore/card_transactions"], ["IoT telemetry", "#/explore/iot_telemetry"]]));
      const hide = h("button", { class: "link-button", type: "button", onclick: () => { storage.set("personasHidden", true); box.remove(); hide.remove(); } }, "Hide these tips");
      root.append(box, h("div", { style: { margin: "-12px 0 16px", textAlign: "right" } }, hide));
    }
    const grid = h("div", { class: "grid cards" });
    root.append(grid);
    const cards = new Map();

    function buildCard(st) {
      const status = h("span");
      const value = h("div", { class: "value" }, "–");
      const spark = sparkline();
      const meta = h("span", { class: "meta" });
      const toggle = h("button", { class: "btn btn-small", type: "button" });
      const rate = h("input", { type: "number", min: "0.01", max: "2000", step: "any", "aria-label": `${st.title} events per second`, title: "Events per second" });
      rate.addEventListener("change", () => {
        const v = parseFloat(rate.value);
        if (v > 0) patchStream(st.name, { rate: v }, `${st.title}: ${v} events/s`);
      });
      toggle.addEventListener("click", () => {
        const current = state.byName[st.name];
        patchStream(st.name, { running: !current.config.running }, `${current.title} ${current.config.running ? "paused" : "started"}`);
      });
      const el = h("article", { class: "card stream-card" },
        h("div", { class: "card-head" }, h("span", { class: "eyebrow", text: st.category }), status),
        h("h3", null, h("a", { href: `#/explore/${st.name}`, text: st.title })),
        h("p", { class: "desc", text: st.description }),
        h("div", { class: "stream-metrics" }, h("div", null, value, h("div", { class: "label", text: "records / s" })), spark.el),
        h("div", { class: "stream-foot" }, toggle, h("label", { class: "row", style: { gap: "6px" } }, rate, h("span", { class: "meta", text: "events/s" })), h("span", { class: "spacer" }), meta,
          h("a", { class: "btn btn-small btn-quiet", href: `#/explore/${st.name}`, "aria-label": `Open ${st.title}` }, "Open", icon("arrow"))));
      grid.append(el);
      return {
        update(cur) {
          fill(status, streamStatus(cur));
          value.textContent = smart(cur.stats.records_per_sec);
          spark.update(cur.series.records_avg, `${cur.title}: ${smart(cur.stats.records_per_sec)} records per second over the last two minutes`);
          meta.textContent = `${compact(cur.stats.stored)} stored`;
          fill(toggle, icon(cur.config.running ? "pause" : "play"), cur.config.running ? "Pause" : "Start");
          if (document.activeElement !== rate) rate.value = cur.config.rate;
        },
      };
    }

    function update() {
      const o = state.overview;
      if (o) {
        heroValue.textContent = smart(o.records_per_sec);
        tiles.running.textContent = `${o.streams_running} / ${o.streams_total}`;
        tiles.stored.textContent = compact(o.stored);
        tiles.size.textContent = bytes(o.db_size_bytes);
        tiles.subs.textContent = String(Object.values(o.subscribers || {}).reduce((a, b) => a + b, 0));
      }
      for (const st of state.streams) {
        if (!cards.has(st.name)) cards.set(st.name, buildCard(st));
        cards.get(st.name).update(st);
      }
    }
    update();
    return { update };
  }

  // ---------------------------------------------------------------- Explorer view
  function ExploreView(root, route) {
    let name = route.arg && state.byName[route.arg] ? route.arg : storage.get("explore.stream", "orders");
    if (!state.byName[name]) name = state.streams[0] && state.streams[0].name;
    let detail = null;
    let source = null;
    let tail = [];
    let tailPaused = false;
    let renderPending = false;
    let lastRenderedSeq = Infinity;
    let schemaCache = null;
    let activeTab = 0;

    const titleEl = h("h1");
    const descEl = h("p");
    const statusEl = h("span");
    const runButton = h("button", { class: "btn", type: "button" });
    const rateInput = h("input", { type: "number", min: "0.01", max: "2000", step: "any", "aria-label": "Events per second" });
    const seasonal = h("input", { type: "checkbox" });
    const selectHost = h("span");
    const tilesEl = h("div", { class: "tiles" });
    const recordsChart = chartCard("Records per second");
    const metricChart = chartCard("Metric");
    const tailBody = h("div", { class: "table-wrap tail" });
    const tailState = h("span", { class: "muted" });
    const tailButton = h("button", { class: "btn btn-small", type: "button" }, icon("pause"), "Pause tail");
    const tabHost = h("div");
    const tabBar = tabs(["Schema", "Data quality", "History", "Practice ideas", "Endpoints"], (i) => { activeTab = i; renderTab(); });
    const connectLink = h("a", { class: "btn btn-small" }, "Connect this stream", icon("arrow"));

    runButton.addEventListener("click", () => {
      const cur = state.byName[name];
      patchStream(name, { running: !cur.config.running }, `${cur.title} ${cur.config.running ? "paused" : "started"}`);
    });
    rateInput.addEventListener("change", () => {
      const v = parseFloat(rateInput.value);
      if (v > 0) patchStream(name, { rate: v }, `Rate set to ${v} events/s`);
    });
    seasonal.addEventListener("change", () => patchStream(name, { seasonality: seasonal.checked }, seasonal.checked ? "Time-of-day pattern on" : "Constant rate"));
    tailButton.addEventListener("click", () => {
      tailPaused = !tailPaused;
      fill(tailButton, icon(tailPaused ? "play" : "pause"), tailPaused ? "Resume tail" : "Pause tail");
      if (!tailPaused) scheduleRender();
    });

    root.append(
      h("div", { class: "page-head" },
        h("div", null, h("div", { class: "row" }, titleEl, statusEl), descEl),
        h("div", { class: "row" }, selectHost)),
      h("div", { class: "card row", style: { marginBottom: "16px", gap: "14px" } },
        runButton,
        h("label", { class: "row", style: { gap: "6px" } }, h("span", { class: "subtle", text: "Rate" }), rateInput, h("span", { class: "subtle", text: "events/s" })),
        h("label", { class: "switch" }, seasonal, h("span", { text: "Time-of-day pattern" })),
        h("span", { class: "spacer" }),
        connectLink),
      tilesEl,
      h("div", { class: "grid two" }, recordsChart.el, metricChart.el),
      h("section", { class: "section card" },
        h("div", { class: "card-head" }, h("div", null, h("h2", { text: "Live tail" }), h("p", { class: "subtle", text: "Newest records first, pushed over Server-Sent Events." })), h("div", { class: "row" }, tailState, tailButton)),
        tailBody),
      h("section", { class: "section card" }, tabBar.el, tabHost));

    function tileEl(label, value, sub) {
      return h("div", { class: "card tile" }, h("div", { class: "label", text: label }), h("div", { class: "value", text: value }), sub ? h("div", { class: "sub", text: sub }) : null);
    }

    function openTail() {
      if (source) source.close();
      tail = [];
      lastRenderedSeq = Infinity;
      fill(tailBody, h("div", { class: "empty", text: "Waiting for live records..." }));
      source = new EventSource(withKey(`/api/v1/streams/${name}/sse`));
      source.onopen = () => { tailState.textContent = "Connected"; };
      source.onerror = () => { tailState.textContent = "Reconnecting..."; };
      source.onmessage = (event) => {
        try { tail.unshift(JSON.parse(event.data)); } catch { return; }
        if (tail.length > 60) tail.length = 50;
        scheduleRender();
      };
    }
    function scheduleRender() {
      if (renderPending || tailPaused) return;
      renderPending = true;
      setTimeout(() => { renderPending = false; renderTail(); }, 350);
    }
    function renderTail() {
      if (!detail || tailPaused || !tail.length) return;
      const columns = detail.fields.map((f) => f.name);
      const seqIndex = columns.indexOf("_seq");
      const rows = tail.slice(0, 50).map((r) => columns.map((c) => r[c]));
      const prevMax = lastRenderedSeq;
      lastRenderedSeq = tail[0]._seq;
      fill(tailBody, resultTable(columns, rows, prevMax === Infinity ? Infinity : prevMax, seqIndex));
    }

    function renderTab() {
      if (!detail) return;
      const panel = [renderSchema, renderChaos, renderHistory, renderPractice, renderEndpoints][activeTab]();
      fill(tabHost, panel);
    }

    function renderSchema() {
      const actions = h("div", { class: "row", style: { marginBottom: "12px" } });
      const addCopy = (label, key) => actions.append(h("button", {
        class: "btn btn-small", type: "button",
        onclick: async () => {
          try {
            schemaCache = schemaCache || (await api(`/api/v1/streams/${name}/schema`));
            const value = schemaCache[key];
            copyText(typeof value === "string" ? value : JSON.stringify(value, null, 2), `${label} copied`);
          } catch (error) { toast(error.message, true); }
        },
      }, icon("copy"), label));
      addCopy("JSON Schema", "json_schema");
      addCopy("Spark DDL", "spark_ddl");
      addCopy("PostgreSQL DDL", "postgres_ddl");
      addCopy("SQL Server DDL", "sqlserver_ddl");
      const keys = detail.key_fields.join(", ");
      return h("div", null,
        actions,
        h("p", { class: "subtle", style: { marginBottom: "10px" } }, `Business key: ${keys}. Joins to: ${detail.related.join(", ") || "none"}. Every record also carries _seq (an incremental cursor) and _ingested_at.`),
        h("div", { class: "table-wrap" }, h("table", null,
          h("thead", null, h("tr", null, ["Field", "Type", "Role", "Description"].map((c) => h("th", { text: c })))),
          h("tbody", null, detail.fields.map((f) => h("tr", null,
            h("td", null, h("code", { text: f.name })), h("td", null, h("span", { class: "type-badge", text: f.type })),
            h("td", { text: f.role }), h("td", { class: "wrap", text: f.description || "" })))))));
    }

    function renderChaos() {
      const chaos = state.byName[name].config.chaos;
      const enabled = h("input", { type: "checkbox", checked: chaos.enabled });
      const rate = h("input", { type: "range", min: "0", max: "50", step: "1", value: String(Math.round(chaos.rate * 100)), "aria-label": "Share of records affected" });
      const rateLabel = h("strong", { text: `${Math.round(chaos.rate * 100)}%` });
      rate.addEventListener("input", () => { rateLabel.textContent = `${rate.value}%`; });
      const checks = Object.entries(detail.chaos_issues).map(([key, text]) => {
        const box = h("input", { type: "checkbox", value: key, checked: chaos.issues.includes(key) });
        return { key, box, el: h("label", { class: "check" }, box, h("span", null, h("strong", { text: key }), h("span", { class: "subtle", text: ` - ${text}` }))) };
      });
      const save = h("button", { class: "btn btn-primary", type: "button" }, "Save data-quality settings");
      save.addEventListener("click", () => patchStream(name, {
        chaos: { enabled: enabled.checked, rate: parseInt(rate.value, 10) / 100, issues: checks.filter((c) => c.box.checked).map((c) => c.key) },
      }, enabled.checked ? "Chaos on: data is now messy" : "Chaos off: clean data"));
      return h("div", { class: "stack" },
        h("p", { class: "subtle", text: "Turn on realistic data-quality problems to practise validation, cleaning, deduplication and late-data handling. Values stay type-valid, so BI tools and SQL sinks keep working - the data just gets messy. Keys and ML labels are never corrupted." }),
        h("label", { class: "switch" }, enabled, h("span", { text: "Inject data-quality issues" })),
        h("label", { class: "row" }, h("span", { class: "subtle", text: "Share of records affected" }), rate, rateLabel),
        h("div", { class: "stack", style: { gap: "8px" } }, checks.map((c) => c.el)),
        h("div", { class: "row" }, save, h("span", { class: "muted", text: `${compact(state.byName[name].stats.chaos_affected)} records affected since start` })));
    }

    function renderHistory() {
      const days = h("input", { type: "number", min: "0.1", max: "365", step: "any", value: "7", "aria-label": "Days of history" });
      const events = h("input", { type: "number", min: "1", max: "2000000", step: "1", placeholder: "auto", style: { width: "130px" }, "aria-label": "Number of events" });
      const hint = h("span", { class: "muted" });
      const progressHost = h("div");
      const updateHint = () => { hint.textContent = `Default: ${compact(Math.round(detail.history_per_day * (parseFloat(days.value) || 0)))} events (${compact(detail.history_per_day)} per day)`; };
      days.addEventListener("input", updateHint);
      updateHint();
      const go = h("button", { class: "btn btn-primary", type: "button" }, "Generate history");
      go.addEventListener("click", async () => {
        try {
          const body = { days: parseFloat(days.value) };
          if (events.value) body.events = parseInt(events.value, 10);
          await api(`/api/v1/streams/${name}/backfill`, { method: "POST", body });
          toast("Backfill started");
          refresh();
        } catch (error) { toast(error.message, true); }
      });
      const panel = h("div", { class: "stack" },
        h("p", { class: "subtle", text: "Generate historical events (following the daily and weekly pattern) so dashboards and models have weeks of data immediately. History lands in the store, SQL, Kafka and file sinks - not in live push feeds or webhooks. _seq keeps increasing, so incremental loaders pick it up as a late-arriving load." }),
        h("div", { class: "row", style: { gap: "12px" } },
          h("label", { class: "row" }, h("span", { class: "subtle", text: "Days" }), days),
          h("label", { class: "row" }, h("span", { class: "subtle", text: "Events" }), events),
          go, hint),
        progressHost);
      panel.update = () => {
        const bf = state.byName[name].stats.backfill;
        if (!bf) { fill(progressHost); return; }
        const pct = bf.events ? Math.round((bf.done / bf.events) * 100) : 100;
        fill(progressHost, h("div", { class: "stack", style: { gap: "6px" } },
          h("div", { class: "row" }, h("strong", { text: bf.running ? `Generating... ${pct}%` : "Last backfill complete" }),
            h("span", { class: "muted", text: `${compact(bf.records)} records over ${bf.days} days${bf.finished_at ? ", finished " + ago(bf.finished_at) : ""}` })),
          h("div", { class: "progress", role: "progressbar", "aria-valuenow": String(pct), "aria-valuemin": "0", "aria-valuemax": "100" }, h("div", { style: { width: `${pct}%` } }))));
      };
      panel.update();
      return panel;
    }

    function renderPractice() {
      const personas = [["analyst", "Data analyst"], ["engineer", "Data engineer"], ["scientist", "Data scientist"]];
      return h("div", { class: "grid three" }, personas.map(([key, label]) => h("div", null,
        h("h3", { text: label, style: { marginBottom: "8px" } }),
        h("ul", { class: "list" }, (detail.use_cases[key] || []).map((text) => h("li", { text }))))));
    }

    function renderEndpoints() {
      const base = location.origin;
      const ws = base.replace(/^http/, "ws");
      const rows = [
        ["JSON (cursor)", `${base}/api/v1/streams/${name}/records?after_seq=0&limit=1000`],
        ["CSV", `${base}/api/v1/streams/${name}/records.csv?limit=10000`],
        ["NDJSON", `${base}/api/v1/streams/${name}/records.ndjson?limit=10000`],
        ["OData", `${base}/odata/${name}`],
        ["Server-Sent Events", `${base}/api/v1/streams/${name}/sse`],
        ["WebSocket", `${ws}/ws?streams=${name}`],
        ["Schema", `${base}/api/v1/streams/${name}/schema`],
        ["Latest (memory)", `${base}/api/v1/streams/${name}/latest?n=50`],
      ];
      return h("div", { class: "stack" },
        h("div", { class: "url-list" }, rows.map(([label, url]) => h("div", { class: "url-row" }, h("span", { class: "eyebrow", text: label }), h("code", { text: url }), copyButton(url)))),
        h("p", null, h("a", { href: `#/connect/powerbi?stream=${name}` }, "Step-by-step guides for Power BI, Tableau, Excel, Python, Spark, Kafka and databases ->")));
    }

    function update() {
      const cur = state.byName[name];
      if (!cur || !detail) return;
      fill(statusEl, streamStatus(cur));
      fill(runButton, icon(cur.config.running ? "pause" : "play"), cur.config.running ? "Pause stream" : "Start stream");
      if (document.activeElement !== rateInput) rateInput.value = cur.config.rate;
      seasonal.checked = cur.config.seasonality;
      const st = cur.stats;
      const factor = cur.config.rate ? st.effective_rate / cur.config.rate : 0;
      fill(tilesEl, 
        tileEl("Records per second", smart(st.records_per_sec), "10-second average"),
        tileEl("Effective rate", `${smart(st.effective_rate)} events/s`, cur.config.seasonality && cur.config.running ? `time-of-day pattern x${factor.toFixed(2)}` : cur.config.running ? "constant rate" : "paused"),
        tileEl("Stored rows", compact(st.stored), "retained in the SQLite store"),
        tileEl("Emitted since start", compact(st.emitted)),
        tileEl("Last _seq", nf0.format(st.last_seq), st.last_emit_at ? `last record ${ago(st.last_emit_at)}` : "no records yet"));
      const series = cur.series;
      const metric = cur.metric;
      const metricField = detail.fields.find((f) => f.name === metric.field);
      const isPct = metricField && metricField.type === "bool";
      recordsChart.update(series.records_avg, { subtitle: "Last 2 minutes, 5-second moving average", format: smart, zero: true });
      metricChart.update(series.metric, {
        subtitle: `Last 2 minutes, ${series.metric_window}`,
        format: isPct ? percent : smart,
        zero: metric.agg !== "avg" || isPct,
      });
      const panel = tabHost.firstChild;
      if (panel && panel.update) panel.update();
    }

    async function load(next) {
      name = next;
      storage.set("explore.stream", name);
      schemaCache = null;
      const cur = state.byName[name];
      titleEl.textContent = cur.title;
      descEl.textContent = cur.description;
      connectLink.href = `#/connect/powerbi?stream=${name}`;
      fill(selectHost, streamSelect(name, (value) => { location.hash = `#/explore/${value}`; }));
      metricChart.el.querySelector("h3").textContent = cur.metric.label;
      try {
        detail = await api(`/api/v1/streams/${name}`);
      } catch (error) { toast(error.message, true); return; }
      openTail();
      renderTab();
      update();
    }

    if (name) load(name);
    else fill(root, h("div", { class: "empty", text: "No streams available." }));
    return {
      update,
      accept(next) {
        if (next.arg && next.arg !== name && state.byName[next.arg]) load(next.arg);
        return true;
      },
      destroy() { if (source) source.close(); },
    };
  }

  // ---------------------------------------------------------------- Connect view
  function ConnectView(root, route) {
    let stream = route.query.get("stream") || storage.get("connect.stream", "orders");
    if (!state.byName[stream]) stream = "orders";
    let platform = route.arg || storage.get("connect.platform", "powerbi");
    let data = null;
    const nav = h("nav", { class: "platforms", "aria-label": "Platforms" });
    const content = h("div", { class: "stack" });
    const selectHost = h("span");
    const baseEl = h("code");
    root.append(
      pageHead("Connect", "Copy-paste connection strings, URLs and code for the tool you use. Snippets are filled in for the selected stream and this server's address.",
        h("label", { class: "row" }, h("span", { class: "subtle", text: "Stream" }), selectHost)),
      h("div", { class: "connect" }, nav, content));

    function go(id, nextStream = stream) { location.hash = `#/connect/${id}?stream=${nextStream}`; }

    function renderNav() {
      fill(nav);
      let group = null;
      for (const p of data.platforms) {
        if (p.group !== group) { group = p.group; nav.append(h("div", { class: "eyebrow group", text: group })); }
        nav.append(h("button", { type: "button", "aria-current": String(p.id === platform), onclick: () => go(p.id) }, p.name));
      }
    }
    function renderContent() {
      const p = data.platforms.find((x) => x.id === platform) || data.platforms[0];
      platform = p.id;
      storage.set("connect.platform", platform);
      baseEl.textContent = data.base_url;
      fill(content, 
        h("div", { class: "card" }, h("h2", { text: p.name }), h("p", { class: "subtle", style: { marginTop: "4px" }, text: p.summary }),
          h("p", { class: "muted", style: { marginTop: "8px" } }, "Server address: ", baseEl, data.auth.api_key_required ? " · API key required" : "")),
        ...p.sections.map((sec) => h("section", { class: "card conn-section" },
          h("h3", { text: sec.title }),
          sec.text ? h("p", { text: sec.text }) : null,
          sec.steps.length ? h("ol", null, sec.steps.map((step) => h("li", { text: step }))) : null,
          sec.code ? codeBlock(sec.code, sec.lang) : null,
          sec.table ? h("div", { class: "table-wrap", style: { maxHeight: "320px" } }, resultTable(sec.table.columns, sec.table.rows)) : null,
          sec.note ? h("p", { class: "note", text: sec.note }) : null)));
    }
    async function load() {
      storage.set("connect.stream", stream);
      fill(selectHost, streamSelect(stream, (value) => go(platform, value)));
      try {
        data = await api(`/api/v1/connections?stream=${encodeURIComponent(stream)}`);
      } catch (error) { fill(content, h("div", { class: "error-box", text: error.message })); return; }
      renderNav();
      renderContent();
    }
    load();
    return {
      accept(next) {
        const nextStream = next.query.get("stream") || stream;
        const nextPlatform = next.arg || platform;
        const streamChanged = nextStream !== stream && state.byName[nextStream];
        platform = nextPlatform;
        if (streamChanged) { stream = nextStream; load(); } else if (data) { renderNav(); renderContent(); }
        return true;
      },
    };
  }

  // ---------------------------------------------------------------- SQL Lab
  const EXAMPLES = [
    ["Revenue by channel, last hour", `SELECT channel,
       COUNT(DISTINCT order_id) AS orders,
       ROUND(SUM(line_total), 2) AS revenue
FROM orders
WHERE event_time >= strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-1 hour')
GROUP BY channel
ORDER BY revenue DESC;`],
    ["Star schema: category margin", `SELECT p.category,
       ROUND(SUM(o.line_total), 2) AS revenue,
       ROUND(SUM(o.line_total - o.quantity * p.unit_cost), 2) AS gross_margin,
       ROUND(100.0 * SUM(o.line_total - o.quantity * p.unit_cost) / SUM(o.line_total), 1) AS margin_pct
FROM orders o
JOIN dim_product p ON p.product_id = o.product_id
WHERE o.order_status = 'completed'
GROUP BY p.category
ORDER BY revenue DESC;`],
    ["Clickstream funnel by device", `SELECT device_type,
       COUNT(DISTINCT session_id) AS sessions,
       COUNT(DISTINCT CASE WHEN event_type = 'add_to_cart' THEN session_id END) AS carts,
       COUNT(DISTINCT CASE WHEN event_type = 'purchase' THEN session_id END) AS purchases,
       ROUND(100.0 * COUNT(DISTINCT CASE WHEN event_type = 'purchase' THEN session_id END) / COUNT(DISTINCT session_id), 2) AS conversion_pct
FROM clickstream
GROUP BY device_type
ORDER BY sessions DESC;`],
    ["Fraud rate by merchant category", `SELECT merchant_category,
       COUNT(*) AS transactions,
       SUM(is_fraud) AS fraud,
       ROUND(100.0 * AVG(is_fraud), 2) AS fraud_pct,
       ROUND(SUM(CASE WHEN is_fraud = 1 THEN amount END), 2) AS fraud_amount
FROM card_transactions
GROUP BY merchant_category
ORDER BY fraud_pct DESC;`],
    ["Machines needing attention", `SELECT device_id, device_type, site_id, status, health_state,
       temperature_c, vibration_mm_s, event_time
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY device_id ORDER BY event_time DESC) AS rn
      FROM iot_telemetry)
WHERE rn = 1 AND status <> 'OK'
ORDER BY status DESC, vibration_mm_s DESC;`],
    ["Service health, last 15 minutes", `SELECT service, version,
       COUNT(*) AS requests,
       ROUND(AVG(latency_ms)) AS avg_latency_ms,
       MAX(latency_ms) AS max_latency_ms,
       ROUND(100.0 * AVG(status_code >= 500), 2) AS error_pct
FROM app_logs
WHERE event_time >= strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-15 minutes')
GROUP BY service, version
ORDER BY error_pct DESC, avg_latency_ms DESC;`],
    ["1-minute OHLC candles", `SELECT DISTINCT minute,
       FIRST_VALUE(price) OVER w AS open,
       MAX(price) OVER w AS high,
       MIN(price) OVER w AS low,
       LAST_VALUE(price) OVER w AS close,
       SUM(volume) OVER w AS volume
FROM (SELECT substr(event_time, 1, 16) AS minute, price, volume, event_time
      FROM stock_ticks WHERE symbol = 'NVTK')
WINDOW w AS (PARTITION BY minute ORDER BY event_time
             ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
ORDER BY minute DESC
LIMIT 30;`],
    ["Current stock from the CDC feed", `SELECT product_id, warehouse_id, quantity_on_hand, reorder_point, row_version
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY product_id, warehouse_id ORDER BY lsn DESC) AS rn
      FROM inventory_cdc)
WHERE rn = 1 AND op <> 'DELETE' AND quantity_on_hand < reorder_point
ORDER BY quantity_on_hand
LIMIT 50;`],
    ["Energy load by city and hour", `SELECT city,
       substr(event_time, 1, 13) AS hour_utc,
       ROUND(AVG(power_kw), 2) AS avg_kw,
       ROUND(AVG(solar_kw), 2) AS avg_solar_kw,
       ROUND(AVG(outdoor_temp_c), 1) AS temp_c
FROM energy_meters
GROUP BY city, hour_utc
ORDER BY hour_utc DESC, city
LIMIT 48;`],
    ["Data quality: duplicate keys", `SELECT order_id, line_number, COUNT(*) AS copies, MIN(_seq) AS first_seq, MAX(_seq) AS last_seq
FROM orders
GROUP BY order_id, line_number
HAVING COUNT(*) > 1
ORDER BY copies DESC
LIMIT 50;`],
  ];

  function SqlView(root) {
    const editor = h("textarea", { class: "editor", spellcheck: "false", "aria-label": "SQL query" });
    editor.value = storage.get("sql.query", EXAMPLES[0][1]);
    const examples = h("select", { "aria-label": "Example queries" }, h("option", { value: "" }, "Example queries..."), EXAMPLES.map(([label], i) => h("option", { value: String(i) }, label)));
    const runBtn = h("button", { class: "btn btn-primary", type: "button" }, icon("play"), "Run");
    const auto = h("input", { type: "checkbox" });
    const meta = h("span", { class: "result-meta" });
    const downloadBtn = h("button", { class: "btn btn-small", type: "button", disabled: true }, "Download CSV");
    const results = h("div");
    const tableList = h("div", { class: "card table-list" }, h("div", { class: "empty", text: "Loading tables..." }));
    let last = null;
    let timer = null;
    let running = false;

    examples.addEventListener("change", () => {
      if (examples.value !== "") { editor.value = EXAMPLES[+examples.value][1]; storage.set("sql.query", editor.value); run(); }
    });
    editor.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); run(); }
    });
    editor.addEventListener("input", () => storage.set("sql.query", editor.value));
    runBtn.addEventListener("click", () => run());
    auto.addEventListener("change", () => {
      clearInterval(timer);
      if (auto.checked) { run(); timer = setInterval(() => run(true), 5000); }
    });
    downloadBtn.addEventListener("click", () => {
      if (!last) return;
      const esc = (v) => (v === null || v === undefined ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
      const csv = [last.columns.map(esc).join(","), ...last.rows.map((r) => r.map(esc).join(","))].join("\n");
      const link = h("a", { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: "livesim-query.csv" });
      link.click();
      URL.revokeObjectURL(link.href);
    });

    root.append(
      pageHead("SQL Lab", "Practise SQL on live data. Queries run read-only against the SQLite store (SQLite dialect, window functions supported). Turn on auto-refresh to watch results change."),
      h("div", { class: "sql" }, tableList,
        h("div", { class: "stack" },
          h("div", { class: "card stack" }, editor,
            h("div", { class: "row" }, runBtn, h("kbd", { class: "muted", text: "Ctrl + Enter" }), examples, h("span", { class: "spacer" }),
              h("label", { class: "switch" }, auto, h("span", { text: "Auto-refresh every 5 s" })))),
          h("div", { class: "card stack" }, h("div", { class: "row" }, meta, h("span", { class: "spacer" }), downloadBtn), results))));

    async function run(silent = false) {
      if (running) return;
      running = true;
      if (!silent) meta.textContent = "Running...";
      results.classList.add("refetching");
      try {
        last = await api("/api/v1/sql", { method: "POST", body: { sql: editor.value, limit: 1000 } });
        meta.textContent = `${nf0.format(last.row_count)} row${last.row_count === 1 ? "" : "s"}${last.truncated ? " (first 1,000 shown)" : ""} · ${last.elapsed_ms} ms · ${new Date().toLocaleTimeString()}`;
        fill(results, last.columns.length ? h("div", { class: "table-wrap", style: { maxHeight: "520px" } }, resultTable(last.columns, last.rows)) : h("div", { class: "empty", text: "Query returned no columns." }));
        downloadBtn.disabled = !last.rows.length;
      } catch (error) {
        meta.textContent = "Query failed";
        fill(results, h("div", { class: "error-box", text: error.message }));
        downloadBtn.disabled = true;
      } finally {
        running = false;
        results.classList.remove("refetching");
      }
    }

    async function loadTables() {
      try {
        const tables = await api("/api/v1/sql/tables");
        const insert = (text) => {
          const { selectionStart: a, selectionEnd: b, value } = editor;
          editor.value = value.slice(0, a) + text + value.slice(b);
          editor.focus();
          editor.selectionStart = editor.selectionEnd = a + text.length;
        };
        fill(tableList, h("div", { class: "eyebrow", style: { marginBottom: "6px" }, text: "Tables - click to insert" }),
          tables.map((t) => h("details", null,
            h("summary", null, h("span", null, h("button", { class: "link-button", type: "button", onclick: (e) => { e.preventDefault(); insert(t.name); } }, t.name)), h("span", { class: "muted", text: compact(t.rows) })),
            h("ul", null, t.columns.map((c) => h("li", null, h("button", { type: "button", onclick: () => insert(c.name) }, h("span", { text: c.name }), h("span", { class: "muted", text: c.type }))))))));
      } catch (error) {
        fill(tableList, h("div", { class: "error-box", text: error.message }));
      }
    }
    loadTables();
    run();
    return { destroy() { clearInterval(timer); } };
  }

  // ---------------------------------------------------------------- Sinks view
  function SinksView(root) {
    let status = null;
    let loading = false;
    const statusHosts = {};
    const hostFor = (key) => (statusHosts[key] = statusHosts[key] || h("div", { class: "stack", style: { gap: "8px" } }));

    root.append(pageHead("Sinks", "Where the simulator sends data. The SQLite store is always on; turn on files, your own database, Kafka or webhooks here. Changes apply immediately and are remembered in data/state.json."));
    const grid = h("div", { class: "grid two" });
    root.append(grid);

    function sinkState(entry) {
      if (!entry || !entry.status) return statusPill("paused", entry && entry.error ? "Off - failed to start" : "Off");
      return entry.status.healthy ? statusPill("live", "Running") : statusPill("error", "Errors");
    }
    function kv(pairs) {
      return h("dl", { class: "kv" }, pairs.filter(Boolean).flatMap(([k, v]) => [h("dt", { text: k }), h("dd", null, v)]));
    }
    function errorLine(entry) {
      const text = (entry && entry.status && entry.status.last_error) || (entry && entry.error);
      return text ? h("div", { class: "error-box", text }) : null;
    }
    async function save(path, body, message) {
      try {
        await api(path, { method: "PUT", body });
        toast(message);
        await load();
      } catch (error) { toast(error.message, true); }
    }

    function build() {
      // SQLite store
      const sqlite = status.sqlite;
      grid.append(h("section", { class: "card stack" },
        h("div", { class: "card-head" }, h("div", null, h("h2", { text: "SQLite store" }), h("p", { text: "Always on. Backs REST, OData and SQL Lab; open it with any SQLite tool while LiveSim runs." })), statusPill("live", "Running")),
        hostFor("sqlite"),
        codeBlock(`File        ${sqlite.path}\nSQLAlchemy  sqlite:///${sqlite.path.replace(/\\/g, "/")}\nJDBC        jdbc:sqlite:${sqlite.path.replace(/\\/g, "/")}\nODBC        Driver={SQLite3 ODBC Driver};Database=${sqlite.path};`, "text")));

      // Landing zone
      const lake = status.lake.config;
      const lakeOn = h("input", { type: "checkbox", checked: lake.enabled });
      const lakeFormat = h("select", null, ["jsonl", "csv", "parquet"].map((f) => h("option", { value: f, selected: f === lake.format }, f)));
      const lakeRoll = h("input", { type: "number", min: "5", max: "3600", value: String(lake.roll_seconds) });
      const lakeKeep = h("input", { type: "number", min: "0.1", step: "any", value: String(lake.retention_hours) });
      const lakeStreams = streamChips(lake.streams);
      grid.append(h("section", { class: "card stack" },
        h("div", { class: "card-head" }, h("div", null, h("h2", { text: "File landing zone" }), h("p", { text: "Hive-partitioned files (stream/date=/hour=) for Spark, Databricks, DuckDB or a lakehouse. Files appear when they roll." })), h("span", { "data-state": "lake" })),
        h("label", { class: "switch" }, lakeOn, h("span", { text: "Write files" })),
        h("div", { class: "form-grid" },
          h("label", { class: "field" }, "Format", lakeFormat),
          h("label", { class: "field" }, "Roll files every (seconds)", lakeRoll),
          h("label", { class: "field" }, "Keep files for (hours)", lakeKeep)),
        h("div", { class: "field" }, h("span", { class: "subtle", text: "Streams (none selected = all)" }), lakeStreams.el),
        h("div", { class: "row" }, h("button", { class: "btn btn-primary", type: "button", onclick: () => save("/api/v1/sinks/lake", {
          enabled: lakeOn.checked, format: lakeFormat.value, roll_seconds: parseInt(lakeRoll.value, 10), retention_hours: parseFloat(lakeKeep.value), streams: lakeStreams.value(),
        }, lakeOn.checked ? "Landing zone on" : "Landing zone off") }, "Save")),
        hostFor("lake")));

      // SQL database
      const sql = status.sql.config;
      const sqlOn = h("input", { type: "checkbox", checked: sql.enabled });
      const sqlUrl = h("input", { type: "text", value: sql.url || "", placeholder: "postgresql+psycopg://user:password@localhost:5432/livesim", spellcheck: "false" });
      const sqlSchema = h("input", { type: "text", value: sql.schema || "", placeholder: "optional, e.g. livesim" });
      const sqlCopy = h("input", { type: "checkbox", checked: true });
      const sqlStreams = streamChips(sql.streams);
      grid.append(h("section", { class: "card stack" },
        h("div", { class: "card-head" }, h("div", null, h("h2", { text: "SQL database" }), h("p", { text: "Write every stream and the dim_* tables into PostgreSQL, SQL Server, MySQL, Snowflake... any SQLAlchemy URL (install its driver). Great for DirectQuery dashboards." })), h("span", { "data-state": "sql" })),
        h("label", { class: "switch" }, sqlOn, h("span", { text: "Write to this database" })),
        h("label", { class: "field" }, "SQLAlchemy URL", sqlUrl),
        h("div", { class: "form-grid" }, h("label", { class: "field" }, "Schema", sqlSchema),
          h("label", { class: "check", style: { alignSelf: "end" } }, sqlCopy, "Copy rows already stored locally")),
        h("div", { class: "field" }, h("span", { class: "subtle", text: "Streams (none selected = all)" }), sqlStreams.el),
        h("div", { class: "row" }, h("button", { class: "btn btn-primary", type: "button", onclick: () => save("/api/v1/sinks/sql", {
          enabled: sqlOn.checked, url: sqlUrl.value.trim(), schema: sqlSchema.value.trim() || null, streams: sqlStreams.value(), copy_existing: sqlCopy.checked,
        }, sqlOn.checked ? "SQL sink connected" : "SQL sink off") }, "Save & connect")),
        hostFor("sql")));

      // Kafka
      const kafka = status.kafka.config;
      const kOn = h("input", { type: "checkbox", checked: kafka.enabled });
      const kBootstrap = h("input", { type: "text", value: kafka.bootstrap_servers || "", placeholder: "localhost:19092", spellcheck: "false" });
      const kPrefix = h("input", { type: "text", value: kafka.topic_prefix || "livesim." });
      const kSingle = h("input", { type: "text", value: kafka.single_topic || "", placeholder: "optional: one topic for everything" });
      const kExtra = h("textarea", { rows: "4", spellcheck: "false", placeholder: '{"security.protocol": "SASL_SSL", "sasl.mechanisms": "PLAIN", ...}' });
      kExtra.value = Object.keys(kafka.config || {}).length ? JSON.stringify(kafka.config, null, 2) : "";
      const kStreams = streamChips(kafka.streams);
      grid.append(h("section", { class: "card stack" },
        h("div", { class: "card-head" }, h("div", null, h("h2", { text: "Kafka" }), h("p", { text: "Produce JSON records to Kafka, Redpanda, Azure Event Hubs, Confluent Cloud or a Fabric Eventstream. Keyed by each record's business key." })), h("span", { "data-state": "kafka" })),
        h("label", { class: "switch" }, kOn, h("span", { text: "Produce to Kafka" })),
        h("div", { class: "form-grid" },
          h("label", { class: "field" }, "Bootstrap servers", kBootstrap),
          h("label", { class: "field" }, "Topic prefix", kPrefix),
          h("label", { class: "field" }, "Single topic", kSingle)),
        h("label", { class: "field" }, "Extra librdkafka settings (JSON)", kExtra),
        h("div", { class: "field" }, h("span", { class: "subtle", text: "Streams (none selected = all)" }), kStreams.el),
        h("div", { class: "row" }, h("button", { class: "btn btn-primary", type: "button", onclick: () => {
          let extra = {};
          try { extra = kExtra.value.trim() ? JSON.parse(kExtra.value) : {}; } catch { toast("Extra settings must be valid JSON", true); return; }
          save("/api/v1/sinks/kafka", { enabled: kOn.checked, bootstrap_servers: kBootstrap.value.trim(), topic_prefix: kPrefix.value.trim() || "livesim.", single_topic: kSingle.value.trim() || null, config: extra, streams: kStreams.value() },
            kOn.checked ? "Kafka connected" : "Kafka sink off");
        } }, "Save & connect")),
        hostFor("kafka")));

      // Webhooks
      const wStream = h("select", null, state.streams.map((st) => h("option", { value: st.name }, st.title)));
      const wUrl = h("input", { type: "url", placeholder: "https://api.powerbi.com/beta/.../rows?key=... or any https endpoint", spellcheck: "false" });
      const wFormat = h("select", null, [["array", "JSON array"], ["powerbi", "Power BI push (booleans as 1/0)"], ["ndjson", "NDJSON"], ["envelope", "Envelope {stream, count, records}"]].map(([v, l]) => h("option", { value: v }, l)));
      const wFields = h("input", { type: "text", placeholder: "optional: event_time,amount,merchant_category", spellcheck: "false" });
      const wBatch = h("input", { type: "number", min: "1", max: "10000", value: "100" });
      const wInterval = h("input", { type: "number", min: "0.2", step: "any", value: "2" });
      const fillWebhookForm = () => {
        const cfg = status.webhooks[wStream.value] && status.webhooks[wStream.value].config;
        wUrl.value = cfg ? cfg.url : "";
        wFormat.value = cfg ? cfg.format : "array";
        wFields.value = cfg && cfg.fields ? cfg.fields.join(",") : "";
        wBatch.value = cfg ? cfg.batch_size : 100;
        wInterval.value = cfg ? cfg.interval_sec : 2;
      };
      wStream.addEventListener("change", fillWebhookForm);
      fillWebhookForm();
      const webhookAction = async (method, suffix, message) => {
        try {
          await api(`/api/v1/sinks/webhooks/${wStream.value}${suffix}`, { method });
          toast(message);
          await load();
        } catch (error) { toast(error.message, true); }
      };
      grid.append(h("section", { class: "card stack" },
        h("div", { class: "card-head" }, h("div", null, h("h2", { text: "Webhooks" }), h("p", { text: "POST batches of a stream's live records to any HTTP endpoint: Power BI streaming models, Azure Functions, n8n, Zapier, webhook.site..." })), h("span", { "data-state": "webhooks" })),
        h("div", { class: "form-grid" }, h("label", { class: "field" }, "Stream", wStream), h("label", { class: "field" }, "Payload format", wFormat)),
        h("label", { class: "field" }, "Endpoint URL", wUrl),
        h("div", { class: "form-grid" }, h("label", { class: "field" }, "Fields (comma separated)", wFields), h("label", { class: "field" }, "Batch size", wBatch), h("label", { class: "field" }, "Flush every (seconds)", wInterval)),
        h("div", { class: "row" },
          h("button", { class: "btn btn-primary", type: "button", onclick: () => save(`/api/v1/sinks/webhooks/${wStream.value}`, {
            url: wUrl.value.trim(), format: wFormat.value, fields: wFields.value.trim() ? wFields.value.split(",").map((f) => f.trim()).filter(Boolean) : null,
            batch_size: parseInt(wBatch.value, 10), interval_sec: parseFloat(wInterval.value),
          }, "Webhook saved") }, "Save webhook"),
          h("button", { class: "btn", type: "button", onclick: () => webhookAction("POST", "/test", "Test record sent") }, "Send test"),
          h("button", { class: "btn btn-danger", type: "button", onclick: () => webhookAction("DELETE", "", "Webhook removed").then(fillWebhookForm) }, "Remove")),
        hostFor("webhooks")));

      // Live consumers
      grid.append(h("section", { class: "card stack" },
        h("div", { class: "card-head" }, h("div", null, h("h2", { text: "Live consumers" }), h("p", { text: "Clients currently connected to the push feeds." }))),
        hostFor("consumers")));
    }

    function render() {
      const sq = status.sqlite;
      fill(hostFor("sqlite"), kv([["Size", bytes(sq.size_bytes)], ["Rows written since start", nf0.format(sq.written)], ["Buffered", nf0.format(sq.buffered)], ["Last write", ago(sq.last_write_at)]]), errorLine({ status: sq }));
      for (const key of ["lake", "sql", "kafka"]) {
        const badge = grid.querySelector(`[data-state="${key}"]`);
        if (badge) fill(badge, sinkState(status[key]));
      }
      const lk = status.lake;
      fill(hostFor("lake"), lk.status ? kv([["Folder", lk.status.path], ["Files written", nf0.format(lk.status.files_written)], ["Records written", nf0.format(lk.status.written)], ["Open files", String(lk.status.open_files)]]) : kv([["Folder", lk.path]]), errorLine(lk));
      const sl = status.sql;
      const copy = sl.status && sl.status.copy;
      fill(hostFor("sql"), sl.status ? kv([["Database", `${sl.status.dialect} - ${sl.status.url}`], ["Records written", nf0.format(sl.status.written)],
        copy ? ["History copy", copy.running ? `${nf0.format(copy.copied)} / ${nf0.format(copy.total)} rows...` : `${nf0.format(copy.copied)} rows copied`] : null,
        ["Last write", ago(sl.status.last_write_at)]]) : "", errorLine(sl));
      const kf = status.kafka;
      fill(hostFor("kafka"), kf.status ? kv([["Brokers", kf.status.bootstrap_servers], ["Topics", kf.status.topic_pattern], ["Delivered", nf0.format(kf.status.delivered)], ["Pending", nf0.format(kf.status.pending)], ["Delivery failures", nf0.format(kf.status.delivery_failures)]]) : "", errorLine(kf));
      const hooks = Object.entries(status.webhooks);
      const wbadge = grid.querySelector('[data-state="webhooks"]');
      if (wbadge) fill(wbadge, hooks.length ? (hooks.every(([, w]) => w.status.healthy) ? statusPill("live", `${hooks.length} active`) : statusPill("error", "Errors")) : statusPill("paused", "None"));
      fill(hostFor("webhooks"), hooks.length ? h("div", { class: "table-wrap" }, resultTable(["Stream", "URL", "Format", "Sent", "Last HTTP status", "Last error"],
        hooks.map(([streamName, w]) => [streamName, w.status.url, w.status.format, w.status.written, w.status.last_status, w.status.last_error]))) : "");
      const subs = status.subscribers || {};
      fill(hostFor("consumers"), kv([["Server-Sent Events", String(subs.sse || 0)], ["WebSocket", String(subs.websocket || 0)], ["TCP firehose", `${subs.tcp || 0} (port ${status.tcp_port || "off"})`]]));
    }

    async function load() {
      if (loading) return;
      loading = true;
      try {
        status = await api("/api/v1/sinks");
        if (!grid.childElementCount) build();
        render();
      } catch (error) {
        if (!grid.childElementCount) fill(grid, h("div", { class: "error-box", text: error.message }));
      } finally { loading = false; }
    }
    load();
    return { update: load };
  }

  // ---------------------------------------------------------------- router & boot
  const VIEWS = { streams: StreamsView, explore: ExploreView, connect: ConnectView, sql: SqlView, sinks: SinksView };

  function parseHash() {
    const raw = location.hash.replace(/^#\/?/, "");
    const [path, query] = raw.split("?");
    const parts = path.split("/").filter(Boolean);
    return { name: VIEWS[parts[0]] ? parts[0] : "streams", arg: parts[1] ? decodeURIComponent(parts[1]) : null, query: new URLSearchParams(query || "") };
  }

  function navigate() {
    const route = parseHash();
    document.querySelectorAll(".tabs a").forEach((a) => {
      if (a.dataset.route === route.name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    if (state.view && state.viewName === route.name && state.view.accept && state.view.accept(route)) return;
    if (state.view && state.view.destroy) state.view.destroy();
    const main = $("#view");
    fill(main);
    state.viewName = route.name;
    state.view = VIEWS[route.name](main, route);
    document.title = `${{ streams: "Streams", explore: "Explorer", connect: "Connect", sql: "SQL Lab", sinks: "Sinks" }[route.name]} · LiveSim`;
  }

  function setupTopbar() {
    const start = $("#btn-start-all");
    const stop = $("#btn-stop-all");
    start.append(icon("play"), "Start all");
    stop.append(icon("pause"), "Pause all");
    start.addEventListener("click", async () => { try { await api("/api/v1/control/start-all", { method: "POST" }); toast("All streams started"); refresh(); } catch (e) { toast(e.message, true); } });
    stop.addEventListener("click", async () => { try { await api("/api/v1/control/stop-all", { method: "POST" }); toast("All streams paused"); refresh(); } catch (e) { toast(e.message, true); } });
    const toggle = $("#theme-toggle");
    toggle.append(icon("theme"));
    toggle.addEventListener("click", () => {
      const root = document.documentElement;
      const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("livesim.theme", root.dataset.theme); } catch { /* private mode */ }
      if (state.view && state.view.update) state.view.update();
    });
  }

  async function boot() {
    await refresh();
    if (!state.streams.length) {
      fill($("#view"), h("div", { class: "card error-box", text: `Could not load streams: ${state.error || "unknown error"}. Retrying...` }));
      setTimeout(boot, 3000);
      return;
    }
    window.addEventListener("hashchange", navigate);
    navigate();
    setTimeout(pollLoop, 1500);
  }
  setupTopbar();
  boot();
})();
