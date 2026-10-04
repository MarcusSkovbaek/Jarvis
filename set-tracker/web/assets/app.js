/* Sætradar: renders data/sets.json. No framework and no build step, so the
   same file works on GitHub Pages, from disk, and in Safari and Edge alike. */
(function () {
  "use strict";

  var DEMO = location.hash === "#demo" || window.SET_TRACKER_DEMO === true;
  var STORE_KEY = DEMO ? "saetradar:demo:v1" : "saetradar:v1";
  var CACHE_KEY = "saetradar:data:v1";
  var BASELINE_KEY = STORE_KEY + ":visit";
  var REMOTE = document.documentElement.getAttribute("data-remote") || "";
  var MINUTE = 60 * 1000;
  var HOUR = 60 * MINUTE;
  var DAY = 24 * HOUR;
  var PLATFORM = { youtube: "YouTube", soundcloud: "SoundCloud" };
  var LINK_NAMES = {
    soundcloud: "SoundCloud", youtube: "YouTube", residentAdvisor: "Resident Advisor",
    instagram: "Instagram", bandcamp: "Bandcamp", website: "Website", mixcloud: "Mixcloud"
  };

  /* ------------------------------------------------------------ storage */

  // Reading window.localStorage itself throws where storage is blocked
  // (Safari with cookies off, sandboxed frames), so every access is guarded.
  function store(kind) { try { return window[kind] || null; } catch (e) { return null; } }
  function readJSON(storage, key) {
    try { var raw = storage && storage.getItem(key); return raw ? JSON.parse(raw) : null; } catch (e) { return null; }
  }
  function writeJSON(storage, key, value) {
    try { if (!storage) return false; storage.setItem(key, JSON.stringify(value)); return true; } catch (e) { return false; }
  }

  var LOCAL = store("localStorage");
  var SESSION = store("sessionStorage");
  var saved = readJSON(LOCAL, STORE_KEY) || {};
  var savedFilter = saved.filter || {};
  var prefs = {
    heard: saved.heard && typeof saved.heard === "object" && !Array.isArray(saved.heard) ? saved.heard : {},
    filter: {
      status: ["unheard", "all", "heard"].indexOf(savedFilter.status) !== -1 ? savedFilter.status : "unheard",
      platform: ["all", "youtube", "soundcloud"].indexOf(savedFilter.platform) !== -1 ? savedFilter.platform : "all",
      artist: typeof savedFilter.artist === "string" ? savedFilter.artist : "all"
    },
    lastVisit: typeof saved.lastVisit === "string" ? saved.lastVisit : null
  };
  function persist() { writeJSON(LOCAL, STORE_KEY, prefs); }

  // "Ny" means first seen by the scanner after this browser's previous visit.
  // The baseline is pinned per tab, so a reload does not wipe the badges.
  var visitBaseline = (function () {
    try {
      var pinned = SESSION && SESSION.getItem(BASELINE_KEY);
      if (pinned !== null && pinned !== undefined) return pinned || null;
      if (SESSION) SESSION.setItem(BASELINE_KEY, prefs.lastVisit || "");
    } catch (e) { /* private mode: badges for this load only */ }
    return prefs.lastVisit;
  })();

  var S = {
    data: null,
    from: null,
    loading: false,
    error: null,
    loadedAt: 0,
    keep: new Set(),     // toggled while looking at a filter: stay put until the filter changes
    open: new Set(),     // expanded quality panels
    undo: null,
    animate: true,       // cards rise in when data arrives, not on every click
    media: new Map()     // id -> cover canvas and images, reused so nothing blinks on re-render
  };

  /* ------------------------------------------------------------ helpers */

  function $(sel, root) { return (root || document).querySelector(sel); }

  function el(tag, attrs) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "class") node.className = v;
        else if (k === "text") node.textContent = v;
        else if (k === "style") node.setAttribute("style", v);
        else node.setAttribute(k, v === true ? "" : String(v));
      });
    }
    for (var i = 2; i < arguments.length; i++) append(node, arguments[i]);
    return node;
  }
  function append(node, kid) {
    if (kid === null || kid === undefined || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(function (k) { append(node, k); }); return; }
    node.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }

  var ICONS = {
    open: ["M7 17 17 7", "M9 7h8v8"],
    check: ["M5 12.5l4.5 4.5L19 7"],
    chevron: ["M6 9l6 6 6-6"],
    youtube: ["M21.6 7.2a2.6 2.6 0 0 0-1.8-1.8C18.2 5 12 5 12 5s-6.2 0-7.8.4a2.6 2.6 0 0 0-1.8 1.8C2 8.8 2 12 2 12s0 3.2.4 4.8a2.6 2.6 0 0 0 1.8 1.8c1.6.4 7.8.4 7.8.4s6.2 0 7.8-.4a2.6 2.6 0 0 0 1.8-1.8c.4-1.6.4-4.8.4-4.8s0-3.2-.4-4.8ZM10 15.2V8.8l5.4 3.2L10 15.2Z"],
    soundcloud: ["M11 8.2c.9-.7 2-1.2 3.3-1.2a5.2 5.2 0 0 1 5.1 4.2A2.9 2.9 0 0 1 22 14.1 2.9 2.9 0 0 1 19.1 17H11V8.2Z",
      "M2 13.2h1.2V17H2zM4.2 11.6h1.2V17H4.2zM6.4 10.2h1.2V17H6.4zM8.6 9h1.2v8H8.6z"],
    live: ["M12 12m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0", "M7.1 7.1a7 7 0 0 0 0 9.8", "M16.9 7.1a7 7 0 0 1 0 9.8"]
  };
  var FILLED = { youtube: true, soundcloud: true };

  function icon(name, cls) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "icon" + (cls ? " " + cls : ""));
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    (ICONS[name] || []).forEach(function (d) {
      var p = document.createElementNS(ns, "path");
      p.setAttribute("d", d);
      if (FILLED[name]) { p.setAttribute("fill", "currentColor"); p.setAttribute("fill-rule", "evenodd"); }
      else {
        p.setAttribute("fill", "none"); p.setAttribute("stroke", "currentColor");
        p.setAttribute("stroke-width", "2"); p.setAttribute("stroke-linecap", "round"); p.setAttribute("stroke-linejoin", "round");
        if (name === "check") p.setAttribute("class", "check-path");
      }
      svg.appendChild(p);
    });
    return svg;
  }

  // Only absolute web addresses: a missing thumbnail (null or "") must not
  // turn into a link to this page, and nothing like javascript: gets through.
  function safeUrl(url) {
    if (typeof url !== "string" || !/^https?:\/\//i.test(url.trim())) return null;
    try { return new URL(url.trim()).href; } catch (e) { return null; }
  }

  function hasCJK(text) { return /[぀-ヿ㐀-鿿ｦ-ﾟ]/.test(text || ""); }

  function hash(text) {
    var h = 2166136261;
    for (var i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }

  /* -------------------------------------------------------- formatting */

  var fmtTime = new Intl.DateTimeFormat("da-DK", { hour: "2-digit", minute: "2-digit" });
  var fmtDay = new Intl.DateTimeFormat("da-DK", { day: "numeric", month: "short" });
  var fmtDayYear = new Intl.DateTimeFormat("da-DK", { day: "numeric", month: "short", year: "numeric" });
  var fmtWeekday = new Intl.DateTimeFormat("da-DK", { weekday: "long" });
  var fmtFull = new Intl.DateTimeFormat("da-DK", { dateStyle: "long", timeStyle: "short" });

  function parseDate(iso, precision) {
    if (!iso || typeof iso !== "string") return null;
    // A date without a time means that calendar day; noon keeps it there in every time zone.
    var d = precision === "date" || iso.length === 10 ? new Date(iso.slice(0, 10) + "T12:00:00") : new Date(iso);
    return isNaN(d) ? null : d;
  }
  function startOfDay(d) { var x = new Date(d); x.setHours(0, 0, 0, 0); return x; }
  function dayDiff(d, now) { return Math.round((startOfDay(now) - startOfDay(d)) / DAY); }
  function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

  function shortDate(d, now) {
    return d.getFullYear() === now.getFullYear() ? fmtDay.format(d) : fmtDayYear.format(d);
  }

  // "for 12 min. siden", "i dag kl. 18.00", "i går kl. 18.00", "lørdag kl. 18.00", "6. okt."
  function when(iso, precision) {
    var d = parseDate(iso, precision);
    if (!d) return "ukendt tidspunkt";
    var now = new Date();
    // "date" has no time, and "approx" ("3 days ago" on YouTube) is only good to the day.
    if (precision === "date" || precision === "approx") {
      var dd = dayDiff(d, now);
      if (dd === 0) return "i dag";
      if (dd === 1) return "i går";
      return shortDate(d, now);
    }
    var diff = now - d;
    if (diff >= 0 && diff < HOUR) return "for " + Math.max(1, Math.round(diff / MINUTE)) + " min. siden";
    var days = dayDiff(d, now);
    if (days === 0) return "i dag kl. " + fmtTime.format(d);
    if (days === 1) return "i går kl. " + fmtTime.format(d);
    if (days > 1 && days < 7) return fmtWeekday.format(d) + " kl. " + fmtTime.format(d);
    return shortDate(d, now);
  }

  // Compact form for the top bar: "14.32", "i går 14.32", "6. okt."
  function compactWhen(d) {
    var now = new Date();
    var days = dayDiff(d, now);
    if (days === 0) return fmtTime.format(d);
    if (days === 1) return "i går " + fmtTime.format(d);
    return shortDate(d, now);
  }

  function clock(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    var mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
    return h ? h + ":" + mm + ":" + ss : m + ":" + ss;
  }
  function length(sec) {
    var mins = Math.round((sec || 0) / 60);
    var h = Math.floor(mins / 60), m = mins % 60;
    if (!h) return mins + " min";
    return m ? h + " t " + m + " min" : h + " t";
  }
  function decimal(n, digits) { return Number(n).toFixed(digits).replace(".", ",").replace(/^-/, "\u2212"); }

  /* --------------------------------------------------------------- data */

  function valid(d) { return d && Array.isArray(d.items) && Array.isArray(d.artists); }

  function fetchJSON(url) {
    var sep = url.indexOf("?") === -1 ? "?" : "&";
    return fetch(url + sep + "t=" + Date.now(), { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src;
      s.onload = function () { s.remove(); resolve(); };
      s.onerror = function () { s.remove(); reject(new Error("script " + src)); };
      document.head.appendChild(s);
    });
  }

  function settle(promise, from) {
    return promise.then(function (d) { return valid(d) ? { data: d, from: from } : null; }, function () { return null; });
  }

  async function fetchData() {
    if (DEMO) {
      if (!window.SET_TRACKER_DEMO_DATA) await loadScript("assets/demo.js");
      var demo = window.SET_TRACKER_DEMO_DATA(new Date());
      // First demo visit: show one set as already heard, so both looks are visible.
      if (!saved.heard) (demo.demoHeard || []).forEach(function (id) { prefs.heard[id] = new Date(Date.now() - DAY).toISOString(); });
      return { data: demo, from: "demo" };
    }
    var onWeb = /^https?:$/.test(location.protocol);
    var found = [];
    if (onWeb) found.push(await settle(fetchJSON("data/sets.json"), "site"));
    // From disk, or when the site copy is missing, read the data branch directly.
    if (REMOTE && (!onWeb || !found[0])) found.push(await settle(fetchJSON(REMOTE), "remote"));
    if (!found.some(Boolean)) {
      found.push(await settle(loadScript("data/sets.js?t=" + Date.now()).then(function () {
        return window.SET_TRACKER_DATA;
      }), "file"));
    }
    var best = found.filter(Boolean).sort(function (a, b) {
      return String(b.data.generatedAt || "").localeCompare(String(a.data.generatedAt || ""));
    })[0];
    if (best) {
      writeJSON(LOCAL, CACHE_KEY, best.data);
      return best;
    }
    var cached = readJSON(LOCAL, CACHE_KEY);
    if (valid(cached)) return { data: cached, from: "cache" };
    throw new Error("Ingen data");
  }

  async function load(manual) {
    if (S.loading) return;
    S.loading = true;
    var before = S.data ? acceptedIds(S.data) : null;
    setBusy(true);
    try {
      var res = await fetchData();
      var changed = !S.data || JSON.stringify(acceptedIds(S.data)) !== JSON.stringify(acceptedIds(res.data));
      S.data = res.data;
      S.from = res.from;
      S.animate = S.animate || changed;
      S.error = null;
      S.loadedAt = Date.now();
      if (!DEMO && res.from !== "cache") {
        prefs.lastVisit = new Date().toISOString();
        persist();
      }
      if (manual && before) {
        var fresh = acceptedIds(S.data).filter(function (id) { return before.indexOf(id) === -1; });
        toast(fresh.length ? (fresh.length === 1 ? "1 nyt sæt fundet" : fresh.length + " nye sæt fundet")
          : "Opdateret. Ingen nye sæt.");
      }
    } catch (e) {
      S.error = e;
      if (manual) toast("Kunne ikke hente data. Tjek forbindelsen.");
    } finally {
      S.loading = false;
      setBusy(false);
      render();
    }
  }

  function acceptedIds(d) {
    return d.items.filter(function (it) { return it.status === "accepted"; }).map(function (it) { return it.id; });
  }

  function setBusy(on) {
    var b = $("#refresh");
    b.setAttribute("aria-busy", on ? "true" : "false");
    b.disabled = on;
    if (on && !S.data) { $("#scan-led").dataset.state = "busy"; $("#scan-text").textContent = "Henter sæt …"; }
  }

  /* ----------------------------------------------------------- selection */

  function artistOf(id) {
    return (S.data.artists || []).find(function (a) { return a.id === id; }) || null;
  }
  function inScope(it) {
    var f = prefs.filter;
    return (f.artist === "all" || it.artistId === f.artist) && (f.platform === "all" || it.platform === f.platform);
  }
  function isHeard(it) { return Object.prototype.hasOwnProperty.call(prefs.heard, it.id); }
  function scoped(status) {
    return S.data.items.filter(function (it) { return it.status === status && inScope(it); });
  }

  /* ------------------------------------------------------------- render */

  function render() {
    renderStatus();
    renderNotice();
    if (!S.data) { renderFailure(); return; }
    // A filter value that no longer exists (an artist removed from config) falls back to "all".
    if (prefs.filter.artist !== "all" && !artistOf(prefs.filter.artist)) prefs.filter.artist = "all";
    renderPlate();
    renderControls();
    renderPending();
    renderQueue();
    renderCrate();
    renderFooter();
  }

  function statusInfo() {
    if (!S.data) return S.error ? ["bad", "Ingen forbindelse"] : ["busy", "Henter sæt …"];
    if (S.from === "demo") return ["warn", "Eksempeldata"];
    var d = S.data;
    var at = parseDate(d.generatedAt);
    if (!at) return ["idle", "Ikke tjekket endnu"];
    var health = d.health || [];
    var failed = health.filter(function (h) { return !h.ok; }).length;
    var interval = (d.settings && d.settings.scanIntervalHours) || 2;
    var age = Date.now() - at;
    var suffix = S.from === "cache" ? " · offline" : "";
    if (health.length && failed === health.length) return ["bad", "Tjek fejlede" + suffix];
    if (age > (interval * 2 + 1) * HOUR) return ["warn", "Sidste tjek " + compactWhen(at) + suffix];
    if (failed) return ["warn", compactWhen(at) + " · " + failed + " fejl" + suffix];
    return ["ok", "Tjekket " + compactWhen(at) + suffix];
  }

  function renderStatus() {
    var info = statusInfo();
    $("#scan-led").dataset.state = info[0];
    $("#scan-text").textContent = info[1];
    var at = S.data && parseDate(S.data.generatedAt);
    $("#scan").title = at && S.from !== "demo" ? "Seneste tjek " + fmtFull.format(at) + ". Se kilderne nederst." : info[1];
    var eyebrowLed = $("#plate .led");
    if (eyebrowLed) eyebrowLed.dataset.state = info[0];
  }

  function renderNotice() {
    var box = $("#notice");
    box.textContent = "";
    if (S.from === "demo") {
      append(box, el("span", null, el("strong", { text: "Eksempeldata. " }),
        "Sådan ser fund ud, når de dukker op. Titler og tal er opdigtede, og links går til kunstnerens profiler."));
      if (!window.SET_TRACKER_DEMO) append(box, el("a", { href: "./", text: "Vis rigtige data" }));
      else if (safeUrl(window.SET_TRACKER_SITE)) {
        append(box, el("a", { href: safeUrl(window.SET_TRACKER_SITE), target: "_blank", rel: "noopener noreferrer", text: "Åbn den rigtige Sætradar" }));
      }
      box.hidden = false;
    } else if (S.from === "cache" && S.data && S.data.generatedAt) {
      append(box, el("span", { text: "Ingen forbindelse. Viser den seneste gemte kopi fra " +
        fmtFull.format(new Date(S.data.generatedAt)) + "." }));
      box.hidden = false;
    } else {
      box.hidden = true;
    }
  }

  function renderFailure() {
    var q = $("#queue");
    q.setAttribute("aria-busy", S.loading ? "true" : "false");
    if (S.loading) return;
    q.textContent = "";
    append(q, el("div", { class: "empty" },
      el("h2", { text: "Data kunne ikke hentes" }),
      el("p", { text: "Sætradaren fik ikke fat i listen over sæt. Tjek forbindelsen og prøv igen." }),
      el("button", { class: "btn btn--quiet", type: "button", "data-action": "reload", text: "Prøv igen" })));
  }

  function renderPlate() {
    var plate = $("#plate");
    var artists = S.data.artists;
    var shown = prefs.filter.artist === "all" ? artists : artists.filter(function (a) { return a.id === prefs.filter.artist; });
    var many = shown.length > 1;
    plate.textContent = "";
    plate.classList.toggle("plate--many", many);
    var info = statusInfo();
    append(plate, el("span", { class: "label plate__eyebrow" }, el("span", { class: "led", "data-state": info[0] }),
      many ? "Overvåger " + shown.length + " kunstnere" : "Overvåger"));

    var names = el("h1", { class: "plate__names" });
    shown.forEach(function (a) {
      append(names, el("span", { class: "plate__name", lang: hasCJK(a.displayName) ? "ja" : null, text: a.displayName || a.name }));
    });
    append(plate, names);

    if (!many && shown[0]) {
      var a = shown[0];
      if (a.subtitle) append(plate, el("p", { class: "plate__sub", text: a.subtitle }));
      var links = el("div", { class: "plate__links" });
      Object.keys(a.links || {}).forEach(function (k) {
        var href = safeUrl(a.links[k]);
        if (!href) return;
        append(links, el("a", { class: "chip-link", href: href, target: "_blank", rel: "noopener noreferrer" },
          FILLED[k] ? icon(k, "icon--s") : null, LINK_NAMES[k] || k, icon("open", "icon--s")));
      });
      if (links.childNodes.length) append(plate, links);
    }

    var s = S.data.settings || {};
    var starts = shown.map(function (x) { return x.trackingSince; });
    var sameStart = starts.every(function (x) { return x === starts[0]; });
    var startText = "startdatoen";
    if (sameStart && starts[0]) {
      var d = new Date(starts[0]);
      startText = fmtDayYear.format(d) + ", kl. " + fmtTime.format(d);
    }
    append(plate, el("ul", { class: "criteria", "aria-label": "Krav" },
      el("li", null, el("b", { text: "Udgivet efter" }), startText),
      el("li", null, el("b", { text: "Længde" }), "over " + (s.minDurationMinutes || 30) + " min"),
      el("li", null, el("b", { text: "Lyd" }), "mindst " + (s.minQualityScore || 60) + "/100")));
  }

  function renderControls() {
    // Nothing found yet: filters would only show zeros.
    $("#controls").hidden = !S.data.items.some(function (it) { return it.status === "accepted"; });
    var accepted = scoped("accepted");
    var heard = accepted.filter(isHeard).length;
    var counts = { all: accepted.length, heard: heard, unheard: accepted.length - heard };
    document.querySelectorAll("[data-count]").forEach(function (n) { n.textContent = counts[n.dataset.count]; });

    var af = $("#artist-filter");
    var artists = S.data.artists;
    if (artists.length > 1) {
      af.textContent = "";
      [{ id: "all", name: "Alle" }].concat(artists).forEach(function (a) {
        append(af, el("button", { type: "button", "data-filter": "artist", "data-value": a.id, title: a.name },
          el("span", { text: a.name })));
      });
      af.hidden = false;
    } else {
      af.hidden = true;
    }
    document.querySelectorAll("[data-filter]").forEach(function (b) {
      b.setAttribute("aria-pressed", prefs.filter[b.dataset.filter] === b.dataset.value ? "true" : "false");
    });
  }

  function renderPending() {
    var box = $("#pending");
    var pending = scoped("pending");
    box.textContent = "";
    box.hidden = !pending.length;
    if (!pending.length) return;
    var live = pending.filter(function (it) { return /^(is_live|is_upcoming|post_live)$/.test(it.liveStatus || ""); });
    var first = live[0] || pending[0];
    var href = safeUrl(first.url);
    var link = href ? el("a", { href: href, target: "_blank", rel: "noopener noreferrer", "data-open": first.id, text: first.title }) : first.title;
    var text = live.length
      ? [live.length === 1 ? "En livestream følges: " : live.length + " livestreams følges, bl.a. ", link, ". Den vurderes, når den er slut."]
      : [pending.length === 1 ? "Et nyt sæt venter på flere oplysninger: " : pending.length + " nye sæt venter på flere oplysninger, bl.a. ",
        link, ". Det tjekkes igen ved næste scanning."];
    append(box, el("div", { class: "pending" }, icon("live"), el("span", null, text)));
  }

  function renderQueue() {
    var q = $("#queue");
    q.setAttribute("aria-busy", "false");
    var accepted = scoped("accepted");
    var status = prefs.filter.status;
    var list = accepted.filter(function (it) {
      if (S.keep.has(it.id) || status === "all") return true;
      return status === "heard" ? isHeard(it) : !isHeard(it);
    });
    q.textContent = "";
    q.classList.toggle("is-entering", S.animate);
    S.animate = false;
    if (!list.length) { append(q, emptyState(accepted)); return; }
    list.forEach(function (it, i) { append(q, card(it, i)); });
  }

  function emptyState(accepted) {
    var f = prefs.filter;
    var box = el("div", { class: "empty" });
    var allAccepted = S.data.items.filter(function (it) { return it.status === "accepted"; });
    if (!allAccepted.length) {
      var a = S.data.artists.length === 1 ? S.data.artists[0] : null;
      var since = a ? new Date(a.trackingSince) : null;
      append(box, el("div", { class: "scope", "aria-hidden": "true" }));
      append(box, el("h2", { text: "Ingen nye sæt endnu" }));
      append(box, el("p", {
        text: (since ? "Sætradaren har lyttet efter siden " + fmtDayYear.format(since) + " kl. " + fmtTime.format(since) + ". " : "") +
          "Den tjekker YouTube og SoundCloud hver " + (((S.data.settings || {}).scanIntervalHours || 2) === 1 ? "time" : "anden time") +
          ", og nye sæt over " + ((S.data.settings || {}).minDurationMinutes || 30) + " minutter med god lyd lægger sig her."
      }));
      if (a && a.links && safeUrl(a.links.soundcloud)) {
        append(box, el("p", null, "Indtil da: ",
          el("a", { href: safeUrl(a.links.soundcloud), target: "_blank", rel: "noopener noreferrer", text: "ældre sæt på SoundCloud" }), "."));
      }
      return box;
    }
    if (!accepted.length) {
      append(box, el("h2", { text: f.platform !== "all" ? "Ingen sæt fra " + PLATFORM[f.platform] + " endnu" : "Ingen sæt her endnu" }));
      append(box, el("p", { text: "Der er fundet sæt andre steder. Skift filter for at se dem." }));
      append(box, el("button", { class: "btn btn--quiet", type: "button", "data-action": "clear-filters", text: "Vis alle sæt" }));
      return box;
    }
    if (f.status === "unheard") {
      append(box, el("h2", { text: "Alt er hørt" }));
      append(box, el("p", { text: accepted.length === 1 ? "Du har hørt det ene sæt, der er fundet. Nye sæt lægger sig her." :
        "Du har hørt alle " + accepted.length + " sæt. Nye sæt lægger sig her." }));
      append(box, el("button", { class: "btn btn--quiet", type: "button", "data-filter": "status", "data-value": "all", text: "Vis alle sæt" }));
    } else {
      append(box, el("h2", { text: "Intet hørt endnu" }));
      append(box, el("p", { text: "Et sæt bliver markeret som hørt, når du åbner det. Du kan også markere det selv." }));
      append(box, el("button", { class: "btn btn--quiet", type: "button", "data-filter": "status", "data-value": "unheard", text: "Vis ikke hørte" }));
    }
    return box;
  }

  function cover(it) {
    var c = document.createElement("canvas");
    var w = 480, h = 270;
    c.width = w; c.height = h;
    var g = c.getContext && c.getContext("2d");
    if (!g) return c;
    var css = getComputedStyle(document.documentElement);
    var bg = css.getPropertyValue("--cover-bg").trim() || "#14171d";
    var wave = css.getPropertyValue("--cover-wave").trim() || "#ffb547";
    g.fillStyle = bg;
    g.fillRect(0, 0, w, h);
    var seed = hash(it.id);
    function rnd() { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; }
    var bars = 72, bw = w / bars, level = 0.45;
    g.fillStyle = wave;
    for (var i = 0; i < bars; i++) {
      level = Math.min(1, Math.max(0.06, level + (rnd() - 0.5) * 0.42));
      var env = 0.35 + 0.65 * Math.sin((i + 0.5) / bars * Math.PI);
      var bh = Math.max(2, level * env * h * 0.34);
      g.globalAlpha = 0.25 + 0.6 * level;
      g.fillRect(i * bw + bw * 0.22, h / 2 - bh, bw * 0.56, bh * 2);
    }
    g.globalAlpha = 1;
    var shade = g.createLinearGradient(0, 0, 0, h);
    shade.addColorStop(0, "rgba(0,0,0,0.25)");
    shade.addColorStop(0.5, "rgba(0,0,0,0)");
    shade.addColorStop(1, "rgba(0,0,0,0.35)");
    g.fillStyle = shade;
    g.fillRect(0, 0, w, h);
    return c;
  }

  // The generated cover and the thumbnails for one set, made once and moved
  // into each new render, so a click elsewhere never makes artwork reload.
  function media(it) {
    var thumb = !DEMO && safeUrl(it.thumbnail);
    var key = it.id + "|" + (thumb || "");
    var m = S.media.get(key);
    if (m) return m;
    m = [cover(it)];
    if (thumb) {
      var square = it.platform === "soundcloud";
      (square ? ["art__blur", "art__square"] : [""]).forEach(function (cls) {
        var img = el("img", { class: cls || null, alt: "", loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
        img.addEventListener("load", function () { img.classList.add("is-loaded"); });
        img.addEventListener("error", function () {
          img.remove();
          var i = m.indexOf(img);
          if (i !== -1) m.splice(i, 1);
        });
        img.src = thumb;
        m.push(img);
      });
    }
    S.media.set(key, m);
    return m;
  }

  function artwork(it, href, heardAt) {
    var box = el(href ? "a" : "div", href ? {
      class: "art", href: href, target: "_blank", rel: "noopener noreferrer",
      tabindex: "-1", "aria-hidden": "true", "data-open": it.id
    } : { class: "art" });
    media(it).forEach(function (n) { box.appendChild(n); });
    append(box, el("span", { class: "art__badge" }, icon(it.platform, "icon--s"), PLATFORM[it.platform] || it.platform));
    if (it.durationSec) append(box, el("span", { class: "art__clock", text: clock(it.durationSec) }));
    if (heardAt) append(box, el("span", { class: "art__stamp" }, icon("check", "icon--s"), "Hørt " + shortDate(new Date(heardAt), new Date())));
    return box;
  }

  function card(it, index) {
    var heard = isHeard(it);
    var href = safeUrl(it.url);
    var q = it.quality || {};
    var isNew = !heard && visitBaseline && it.firstSeenAt && new Date(it.firstSeenAt) > new Date(visitBaseline);
    var artist = artistOf(it.artistId);
    var node = el("article", {
      class: "set", "data-id": it.id, "data-heard": heard ? "" : null, style: "--i:" + Math.min(index, 8),
      "aria-label": it.title
    });

    append(node, artwork(it, href, heard ? prefs.heard[it.id] : null));

    var prefix = it.publishedPrecision === "firstSeen" ? "Fundet " : "Udgivet ";
    var published = parseDate(it.publishedAt, it.publishedPrecision);
    var main = el("div", { class: "set__main" },
      el("div", { class: "set__by" },
        el("span", { text: [S.data.artists.length > 1 && artist ? artist.name : null, it.uploader].filter(Boolean).join(" · ") })),
      el("h2", { class: "set__title", lang: hasCJK(it.title) ? "ja" : null },
        href ? el("a", { href: href, target: "_blank", rel: "noopener noreferrer", "data-open": it.id, text: it.title }) : it.title),
      el("div", { class: "set__facts" },
        isNew ? el("span", { class: "tag tag--new", text: "Ny" }) : null,
        it.example ? el("span", { class: "tag tag--example", text: "Eksempel" }) : null,
        el("span", {
          class: "fact",
          title: published ? (/^(date|approx)$/.test(it.publishedPrecision || "") ? fmtDayYear : fmtFull).format(published) : null,
          text: prefix + when(it.publishedAt, it.publishedPrecision)
        }),
        // The artwork clock shows the length; this copy is for screen readers.
        it.durationSec ? el("span", { class: "sr-only", text: "Længde " + length(it.durationSec) }) : null));

    var alts = (it.alternates || []).filter(function (a) { return safeUrl(a.url); });
    if (alts.length) {
      var alt = el("div", { class: "set__alt" }, "Også på ");
      alts.forEach(function (a, i) {
        if (i) append(alt, ", ");
        append(alt, el("a", { href: safeUrl(a.url), target: "_blank", rel: "noopener noreferrer", "data-open": it.id },
          PLATFORM[a.platform] || a.platform, " ", icon("open", "icon--s")));
      });
      append(main, alt);
    }
    append(node, main);

    var side = el("div", { class: "set__side" });
    var panelId = "qd-" + hash(it.id).toString(36);
    var open = S.open.has(it.id);
    if (typeof q.score === "number") {
      var lit = Math.round(q.score / 10);
      var leds = el("span", { class: "meter__leds", "aria-hidden": "true" });
      for (var i = 0; i < 10; i++) append(leds, el("i", { class: i < lit ? "on" : null }));
      append(side, el("button", {
        class: "meter", type: "button", "data-meter": it.id, "aria-expanded": open ? "true" : "false", "aria-controls": panelId,
        "aria-label": "Lydkvalitet " + q.score + " af 100, " + q.label + (q.verified ? "" : ", ikke lydmålt") + ". Vis detaljer"
      },
        el("span", { class: "label meter__label", text: "Lyd" }),
        leds,
        el("span", { class: "meter__score", text: q.score }),
        el("span", { class: "meter__word" }, (q.label || "") + (q.verified ? "" : " · ikke lydmålt"), icon("chevron", "icon--s"))));
    }
    var platformName = PLATFORM[it.platform] || "kilden";
    append(side, el("div", { class: "actions" },
      href ? el("a", { class: "btn btn--play", href: href, target: "_blank", rel: "noopener noreferrer", "data-open": it.id },
        el("span", { text: heard ? "Åbn igen" : "Åbn på " + platformName }), icon("open", "icon--s")) : el("span"),
      el("button", {
        class: "btn btn--heard", type: "button", "data-toggle-heard": it.id, "aria-pressed": heard ? "true" : "false",
        "aria-label": heard ? "Hørt. Tryk for at markere som ikke hørt" : "Markér som hørt",
        title: heard ? "Markér som ikke hørt" : "Markér som hørt"
      }, el("span", { class: "tick" }, icon("check")), el("span", { text: "Hørt" }))));
    append(node, side);

    if (open) append(node, qualityPanel(it, panelId));
    return node;
  }

  function qualityPanel(it, id) {
    var q = it.quality || {};
    var a = q.analysis;
    var panel = el("div", { class: "qd", id: id });
    if (a) {
      var cutoff = Math.min(1, (a.cutoffHz || 0) / 22050);
      var bass = a.bassDb >= -6 ? "Fyldig" : a.bassDb >= -18 ? "Normal" : "Svag";
      append(panel, el("div", { class: "qd__readouts" },
        readout("Frekvensloft", decimal((a.cutoffHz || 0) / 1000, 1) + " kHz",
          el("div", { class: "readout__bar", role: "img", "aria-label": "Lyd op til " + decimal((a.cutoffHz || 0) / 1000, 1) + " af 22 kHz" },
            el("span", { style: "width:" + (cutoff * 100).toFixed(1) + "%" }))),
        readout("Bas", bass),
        readout("Clipping", decimal((a.clipFraction || 0) * 100, 2) + " %"),
        readout("Stilhed", Math.round((a.silenceFraction || 0) * 100) + " %"),
        readout("Niveau", decimal(a.rmsDb || 0, 1) + " dBFS"),
        readout("Stereo", a.sideDb < -40 ? "Mono" : "Stereo")));
    }
    var list = el("ul", { class: "qd__signals" });
    (q.signals || []).forEach(function (s) {
      var n = s.impact || 0;
      append(list, el("li", null,
        el("span", { class: "impact " + (n > 0 ? "impact--up" : n < 0 ? "impact--down" : ""), text: n > 0 ? "+" + n : n < 0 ? "−" + Math.abs(n) : "±0" }),
        el("span", { text: s.text })));
    });
    if (list.childNodes.length) append(panel, list);
    var base = (S.data.settings && S.data.settings.qualityBase) || 62;
    var min = (S.data.settings && S.data.settings.minQualityScore) || 60;
    append(panel, el("p", {
      class: "qd__note",
      text: (a ? "Målt på to lydudsnit fra midten af sættet. " :
        "Lyden kunne ikke måles" + (q.analysisError ? " (" + q.analysisError + ")" : "") + ", så vurderingen bygger på uploader, bitrate og titel. ") +
        "Scoren starter på " + base + ", justeres af fundene ovenfor og skal være mindst " + min + "."
    }));
    return panel;
  }

  function readout(label, value, extra) {
    return el("div", { class: "readout" }, el("span", { class: "label", text: label }), el("span", { class: "readout__value", text: value }), extra || null);
  }

  function renderCrate() {
    var crate = $("#crate");
    var rejected = scoped("rejected");
    crate.hidden = !rejected.length;
    if (!rejected.length) return;
    $("#crate-count").textContent = "(" + rejected.length + ")";
    var list = $("#crate-list");
    list.textContent = "";
    rejected.forEach(function (it) {
      var href = safeUrl(it.url);
      var meta = [PLATFORM[it.platform], it.uploader, when(it.publishedAt, it.publishedPrecision),
        it.durationSec ? length(it.durationSec) : null,
        it.quality && typeof it.quality.score === "number" ? "lyd " + it.quality.score + "/100" : null].filter(Boolean).join(" · ");
      var why = el("ul", { class: "reject__why" });
      (it.reasons || []).forEach(function (r) { append(why, el("li", { text: r })); });
      append(list, el("li", { class: "reject" },
        href ? el("a", { class: "reject__title", href: href, target: "_blank", rel: "noopener noreferrer", "data-open": it.id, text: it.title })
          : el("span", { class: "reject__title", text: it.title }),
        el("span", { class: "reject__meta", text: meta }),
        why.childNodes.length ? why : null));
    });
  }

  function renderFooter() {
    var d = S.data;
    var ul = $("#sources");
    ul.textContent = "";
    var health = (d.health || []).filter(function (h) { return prefs.filter.artist === "all" || h.artistId === prefs.filter.artist; });
    if (!health.length) {
      append(ul, el("li", null, el("span", { class: "led" }), el("span", { class: "src__label", text: "Første tjek er ikke kørt endnu" })));
    }
    health.forEach(function (h) {
      append(ul, el("li", null,
        el("span", { class: "led", "data-state": h.ok ? "ok" : "bad" }),
        el("span", { class: "src__label", title: h.label, text: h.label }),
        el("span", { class: "src__result", text: h.ok ? (h.found === 1 ? "1 resultat" : (h.found || 0) + " resultater") : "fejl" }),
        h.ok ? null : el("span", { class: "src__error", text: h.error || "Ukendt fejl" })));
    });

    var times = $("#times");
    var parts = [];
    var at = parseDate(d.generatedAt);
    if (at && S.from !== "demo") {
      parts.push("Seneste tjek " + when(d.generatedAt));
      var interval = (d.settings && d.settings.scanIntervalHours) || 2;
      var next = new Date(at.getTime() + interval * HOUR);
      parts.push(next > new Date() ? "næste omkring kl. " + fmtTime.format(next) : "næste tjek er på vej");
    }
    var source = { remote: "data hentet direkte fra GitHub", file: "data fra lokal fil", cache: "gemt kopi uden forbindelse" }[S.from];
    if (source) parts.push(source);
    times.textContent = parts.length ? cap(parts.join(" · ")) + "." : "";

    var s = d.settings || {};
    var how = $("#howto");
    how.textContent = "";
    [
      "Titlen nævner kunstneren, eller kunstneren har selv uploadet det.",
      "Det er udgivet efter startdatoen og varer over " + (s.minDurationMinutes || 30) + " minutter.",
      "Lyden scorer mindst " + (s.minQualityScore || 60) + "/100. Scoren bygger på to lydudsnit, der måles for frekvensloft, bas, clipping og stilhed, plus uploader, bitrate og titel.",
      "Genuploads af ældre sæt og dubletter fra en anden platform bliver skjult."
    ].forEach(function (t) { append(how, el("li", { text: t })); });
  }

  /* ------------------------------------------------------------ actions */

  function setHeard(id, on) {
    if (on) prefs.heard[id] = new Date().toISOString();
    else delete prefs.heard[id];
    S.keep.add(id);
    persist();
  }

  function markOpened(id) {
    if (!S.data || prefs.heard[id]) return;
    setHeard(id, true);
    // Re-render after the browser has followed the link, never during the click.
    setTimeout(function () {
      render();
      toast("Markeret som hørt", function () { setHeard(id, false); render(); });
    }, 0);
  }

  function toggleHeard(id) {
    var on = !prefs.heard[id];
    setHeard(id, on);
    render();
    toast(on ? "Markeret som hørt" : "Markeret som ikke hørt", function () { setHeard(id, !on); render(); });
  }

  function setFilter(kind, value) {
    if (prefs.filter[kind] === value) return;
    prefs.filter[kind] = value;
    S.keep.clear();
    persist();
    render();
  }

  var toastTimer = null;
  function toast(text, undo) {
    var t = $("#toast");
    $("#toast-text").textContent = text;
    S.undo = undo || null;
    $("#toast-undo").hidden = !undo;
    t.hidden = true;
    void t.offsetWidth;   // restart the entry animation
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; S.undo = null; }, undo ? 6000 : 3500);
  }

  // Focus survives a re-render: find the control that had it by its data attributes.
  function focusKey(node) {
    if (!node || !node.getAttribute) return null;
    var keys = ["data-toggle-heard", "data-meter", "data-filter", "data-action"];
    for (var i = 0; i < keys.length; i++) {
      if (node.hasAttribute(keys[i])) {
        var sel = "[" + keys[i] + '="' + CSS.escape(node.getAttribute(keys[i])) + '"]';
        if (keys[i] === "data-filter") sel += '[data-value="' + CSS.escape(node.getAttribute("data-value")) + '"]';
        return sel;
      }
    }
    return null;
  }
  function withFocus(fn) {
    var key = focusKey(document.activeElement);
    fn();
    if (key) { var n = $(key); if (n) n.focus({ preventScroll: true }); }
  }

  document.addEventListener("click", function (e) {
    var t = e.target;
    if (!(t instanceof Element)) return;
    var opener = t.closest("[data-open]");
    if (opener) { markOpened(opener.getAttribute("data-open")); return; }
    var btn = t.closest("button, [data-filter]");
    if (!btn) return;
    if (btn.id === "refresh") { load(true); return; }
    if (btn.id === "toast-undo") {
      var undo = S.undo;
      $("#toast").hidden = true;
      S.undo = null;
      if (undo) withFocus(undo);
      return;
    }
    withFocus(function () {
      if (btn.hasAttribute("data-toggle-heard")) toggleHeard(btn.getAttribute("data-toggle-heard"));
      else if (btn.hasAttribute("data-meter")) {
        var id = btn.getAttribute("data-meter");
        if (S.open.has(id)) S.open.delete(id); else S.open.add(id);
        render();
      } else if (btn.hasAttribute("data-filter")) setFilter(btn.getAttribute("data-filter"), btn.getAttribute("data-value"));
      else if (btn.getAttribute("data-action") === "clear-filters") {
        prefs.filter.platform = "all"; prefs.filter.artist = "all"; S.keep.clear(); persist(); render();
      } else if (btn.getAttribute("data-action") === "reload") load(true);
    });
  });

  // Middle click opens a tab too.
  document.addEventListener("auxclick", function (e) {
    if (e.button !== 1 || !(e.target instanceof Element)) return;
    var opener = e.target.closest("[data-open]");
    if (opener) markOpened(opener.getAttribute("data-open"));
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !$("#toast").hidden) $("#toast").hidden = true;
  });

  // Coming back to the tab or the home-screen app: fetch again if it has been a while.
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && !DEMO && Date.now() - S.loadedAt > 5 * MINUTE) load(false);
  });
  setInterval(function () {
    if (!document.hidden && !DEMO && Date.now() - S.loadedAt > 15 * MINUTE) load(false);
  }, MINUTE);
  // Another tab marked something as heard.
  window.addEventListener("storage", function (e) {
    if (e.key !== STORE_KEY || !e.newValue) return;
    try {
      var next = JSON.parse(e.newValue);
      prefs.heard = next.heard || {};
      if (S.data) render();
    } catch (err) { /* ignore */ }
  });
  window.addEventListener("hashchange", function () {
    if ((location.hash === "#demo") !== DEMO) location.reload();
  });

  window.SetTracker = { reload: function () { return load(false); }, state: S, prefs: prefs };
  load(false);
})();
