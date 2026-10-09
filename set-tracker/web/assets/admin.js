/* Sætradar: what is tracked (artists and their start dates), edited in the
   app and saved straight to config/artists.json in the GitHub repo. Saving
   needs a GitHub token that only this browser keeps; GitHub then starts a
   scan by itself (a change to the config is a push), and this follows it
   until the new data is on the page. */
(function () {
  "use strict";

  var ST = window.SetTracker;
  var dialog = document.getElementById("manage");
  if (!ST || !dialog) return;

  var ui = ST.ui;
  var el = ui.el;
  var append = ui.append;
  var icon = ui.icon;

  var root = document.documentElement;
  var REPO = root.getAttribute("data-repo") || "";
  var CONFIG_PATH = root.getAttribute("data-config") || "set-tracker/config/artists.json";
  var WORKFLOW = root.getAttribute("data-workflow") || "set-tracker.yml";
  var API = "https://api.github.com";
  var AUTH_KEY = "saetradar:github:v1";
  var POLL_MS = window.SET_TRACKER_POLL_MS || 8000;
  var DAY = 864e5;
  var MAX_NAMES = 8;            // spellings per artist; each is one YouTube search per scan
  var MAX_WORDS = 25;           // words in each title filter
  var READ_ONLY = ST.demo;      // the example view, and the preview, never write anywhere

  var body = document.getElementById("manage-body");
  var titleEl = document.getElementById("manage-title");
  var backBtn = document.getElementById("manage-back");

  var A = {
    auth: readAuth(),   // { token, login }
    branch: null,
    file: null,         // { config, sha } as last read from GitHub
    loading: false,
    view: "list",       // list | form | connect
    editing: null,      // id of the artist in the form, or null for a new one
    names: [],          // extra spellings in the form
    focus: null,
    busy: false,
    error: null,
    confirmRemove: false,
    follow: null,       // the scan being followed: { target, text, state, url, started }
    timer: null,
    opener: null,
    fetching: null,     // the read of the file in flight
    pendingFocus: false,
    formSha: null,      // the version of the file the form was filled from
    dirty: false,       // something typed or picked in the form since
    dispatching: false,
    why: null           // "scan": the connect view was opened to start a scan
  };

  /* --------------------------------------------------------------- auth */

  function storage() { try { return window.localStorage || null; } catch (e) { return null; } }
  function readAuth() {
    try {
      var s = storage(); var raw = s && s.getItem(AUTH_KEY);
      var v = raw ? JSON.parse(raw) : null;
      return v && typeof v.token === "string" && v.token ? v : null;
    } catch (e) { return null; }
  }
  function writeAuth(v) {
    try { var s = storage(); if (!s) return; if (v) s.setItem(AUTH_KEY, JSON.stringify(v)); else s.removeItem(AUTH_KEY); } catch (e) { /* kept for this visit only */ }
  }

  /* ------------------------------------------------------------- GitHub */

  function GitHubError(message, status) { var e = new Error(message); e.status = status; return e; }

  function explain(status, data, res) {
    var remaining = res && res.headers && res.headers.get("x-ratelimit-remaining");
    if (status === 401) return "GitHub afviste tokenet. Det er måske udløbet eller kopieret forkert.";
    if ((status === 403 || status === 429) && remaining === "0") return "GitHub har brug for en pause (for mange forespørgsler). Prøv igen om lidt.";
    if (status === 403) return "Tokenet har ikke lov til det. Giv det adgang til Jarvis med “Contents: Read and write” og “Actions: Read and write”.";
    if (status === 404) return "GitHub kunne ikke finde repoet eller filen. Har tokenet adgang til Jarvis-repoet?";
    if (status === 409 || (status === 422 && /sha/i.test(data && data.message || ""))) return "Filen blev ændret et andet sted samtidig. Prøv igen.";
    return "GitHub svarede med fejl " + status + (data && data.message ? " (" + data.message + ")" : "") + ".";
  }

  async function gh(path, opts) {
    opts = opts || {};
    var token = opts.token || (A.auth && A.auth.token);
    // Only headers GitHub's CORS policy allows from a browser: Accept, Authorization, Content-Type.
    var headers = { "Accept": "application/vnd.github+json" };
    if (token) headers.Authorization = "Bearer " + token;
    if (opts.body) headers["Content-Type"] = "application/json";
    var res;
    try {
      res = await fetch(API + path, {
        method: opts.method || "GET", headers: headers, cache: "no-store",
        body: opts.body ? JSON.stringify(opts.body) : undefined
      });
    } catch (e) {
      throw GitHubError("Ingen forbindelse til GitHub. Tjek internettet og prøv igen.", 0);
    }
    var data = null;
    if (res.status !== 204) { try { data = await res.json(); } catch (e) { data = null; } }
    if (!res.ok) throw GitHubError(explain(res.status, data, res), res.status);
    return data;
  }

  // JSON on GitHub is base64 of UTF-8 bytes; ¥, € and 行松陽介 must survive both ways.
  function b64encode(text) {
    var bytes = new TextEncoder().encode(text), bin = "";
    for (var i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(bin);
  }
  function b64decode(b64) {
    var bin = atob(String(b64 || "").replace(/\s+/g, ""));
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder().decode(bytes);
  }

  function contentsPath() {
    return "/repos/" + REPO + "/contents/" + CONFIG_PATH.split("/").map(encodeURIComponent).join("/");
  }

  async function branch() {
    if (!A.branch) A.branch = (await gh("/repos/" + REPO)).default_branch;
    return A.branch;
  }

  async function readConfig() {
    var b = await branch();
    var res = await gh(contentsPath() + "?ref=" + encodeURIComponent(b));
    var config;
    try { config = JSON.parse(b64decode(res.content)); } catch (e) { throw GitHubError("Opsætningsfilen i repoet kunne ikke læses (ugyldig JSON).", 0); }
    if (!config || !Array.isArray(config.artists)) throw GitHubError("Opsætningsfilen i repoet mangler listen over kunstnere.", 0);
    return { config: config, sha: res.sha };
  }

  async function loadConfig(force) {
    if (!A.auth || READ_ONLY) return;
    if (A.file && !force) return;
    if (A.fetching) return A.fetching;
    A.loading = true;
    render();
    A.fetching = (async function () {
      try { A.file = await readConfig(); A.error = null; }
      catch (e) { A.error = e.message; if (e.status === 401) disconnect(true); }
      finally {
        A.loading = false;
        A.fetching = null;
        // A form filled from an older copy of the file, and not touched yet,
        // is filled again: saving it must not undo a change made elsewhere.
        var stale = A.view === "form" && A.editing && A.file && A.formSha && A.formSha !== A.file.sha && !A.dirty
          && !!body.querySelector("form");
        if (stale) A.names = null;
        render(stale);
        focusFirst();
      }
    })();
    return A.fetching;
  }

  // Read, change, write; if someone else saved in between, read again and redo the change.
  // `describe` gives the commit message once the change is made (it can depend on it).
  async function commit(change, describe) {
    var b = await branch();
    for (var attempt = 1; attempt <= 3; attempt++) {
      var file = await readConfig();
      var next = change(JSON.parse(JSON.stringify(file.config)));
      var message = typeof describe === "function" ? describe() : describe;
      try {
        var res = await gh(contentsPath(), {
          method: "PUT",
          body: { message: message, content: b64encode(JSON.stringify(next, null, 2) + "\n"), sha: file.sha, branch: b }
        });
        A.file = { config: next, sha: res.content && res.content.sha };
        return res.commit && res.commit.sha;
      } catch (e) {
        if ((e.status === 409 || e.status === 422) && attempt < 3) continue;
        throw e;
      }
    }
  }

  /* ------------------------------------------------------- the artists */

  function artists() {
    if (A.file) return A.file.config.artists;
    var d = ST.state.data;
    return d ? d.artists : [];
  }

  function scanned(id) {
    var d = ST.state.data;
    return !!(d && d.artists.some(function (a) { return a.id === id; }));
  }

  // Uploads the last scan found but had no time left to judge.
  function backlogOf(id) {
    var d = ST.state.data;
    var a = d && d.artists.find(function (x) { return x.id === id; });
    return (a && a.backlog) || 0;
  }

  function fold(text) { return String(text || "").normalize("NFKC").toLowerCase().trim(); }

  function uniqueNames(list) {
    var seen = {}, out = [];
    list.forEach(function (n) {
      n = String(n || "").trim();
      var key = fold(n);
      if (n && !seen[key]) { seen[key] = true; out.push(n); }
    });
    return out;
  }

  function slug(name) {
    var s = String(name).normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase()
      .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);
    return s || "kunstner";
  }

  function uniqueId(base, taken) {
    var id = base, n = 2;
    while (taken.indexOf(id) !== -1) id = base + "-" + n++;
    return id;
  }

  function soundcloudUser(text) {
    var t = String(text).trim();
    var m = t.match(/^(?:https?:\/\/)?(?:www\.|m\.)?soundcloud\.com\/([A-Za-z0-9_-]+)/i);
    if (m) return m[1].toLowerCase();
    return /^[A-Za-z0-9_-]{2,64}$/.test(t) ? t.toLowerCase() : null;
  }

  function youtubeChannel(text) {
    var t = String(text).trim();
    if (/^@[\w.-]{3,40}$/.test(t)) return "https://www.youtube.com/" + t;
    var m = t.match(/^(?:https?:\/\/)?(?:www\.|m\.)?youtube\.com\/(@[\w.-]{3,40}|channel\/UC[\w-]{20,24}|c\/[\w.-]+|user\/[\w.-]+)/i);
    return m ? "https://www.youtube.com/" + m[1] : null;
  }

  function splitList(text) {
    return String(text || "").split(/[\s,]+/).map(function (x) { return x.trim(); }).filter(Boolean);
  }

  function pad(n) { return String(n).padStart(2, "0"); }
  function toLocalInput(iso) {
    var d = new Date(iso);
    if (isNaN(d)) d = new Date();
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }
  // "2026-10-04T19:50" from the date field is local time. Built by hand, so no
  // browser can read it as UTC.
  function fromLocalInput(value) {
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value || "");
    if (!m) return null;
    var d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
    return isNaN(d) || d.getDate() !== +m[3] ? null : d;
  }
  function isoUTC(d) { return d.toISOString().replace(/\.\d{3}Z$/, "Z"); }

  var fmtLong = new Intl.DateTimeFormat("da-DK", { weekday: "long", day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" });

  // The date field looks different in every browser and language; this line
  // says in Danish exactly which moment is picked.
  function echoSince() {
    var inputEl = body.querySelector("#f-since"), echo = body.querySelector("#f-since-echo");
    if (!inputEl || !echo) return;
    var d = fromLocalInput(inputEl.value);
    echo.textContent = d ? fmtLong.format(d) : "";
  }

  // A toast would sit under the sheet's dimmed backdrop; the sheet shows its own status.
  function notify(text) { if (!dialog.open) ST.toast(text); }

  function describeStart(iso) {
    var d = new Date(iso);
    return isNaN(d) ? "ukendt dato" : ui.fmtDayYear.format(d) + ", kl.\u00a0" + ui.fmtTime.format(d);
  }

  // A scan is being followed and has not ended yet.
  function scanning() { return !!A.follow && A.follow.state !== "done" && A.follow.state !== "failed"; }

  /* --------------------------------------------------------------- views */

  function open(view, opts) {
    opts = opts || {};
    if (!dialog.open) A.opener = document.activeElement;
    A.why = opts.why || null;
    A.error = null;
    A.confirmRemove = false;
    if ((view === "form") && (READ_ONLY || !A.auth)) view = READ_ONLY ? "list" : "connect";
    A.view = view;
    if (view === "form") startForm(opts.id || null, opts.focus || null);
    if (A.auth && !READ_ONLY && !A.file) A.loading = true;   // loadConfig below; no flash of "Prøv igen"
    A.pendingFocus = true;
    render(true);
    if (!dialog.open) {
      try { dialog.showModal(); } catch (e) { dialog.setAttribute("open", ""); }
      root.classList.add("has-sheet");
    }
    focusFirst();
    // Always read the file fresh: it may have been changed from another device.
    if (A.auth && !READ_ONLY) loadConfig(true);
  }

  function close() {
    if (dialog.open) dialog.close();
  }

  // Where focus goes when the control that had it is gone: the sheet's title.
  function focusTitle() {
    titleEl.setAttribute("tabindex", "-1");
    titleEl.focus({ preventScroll: true });
  }
  titleEl.addEventListener("blur", function () { titleEl.removeAttribute("tabindex"); });

  // Rebuilding part of the sheet must not throw away keyboard focus: find the
  // same control again, or else land on the title.
  function keepFocus(rebuild) {
    var active = document.activeElement;
    var inside = !!(active && active !== body && body.contains(active));
    var key = null;
    if (inside && active.id) key = "#" + CSS.escape(active.id);
    else if (inside && active.getAttribute("data-act")) {
      key = '[data-act="' + active.getAttribute("data-act") + '"]' +
        (active.getAttribute("data-id") ? '[data-id="' + CSS.escape(active.getAttribute("data-id")) + '"]' : "");
    }
    rebuild();
    if (!inside) return;
    var again = key && body.querySelector(key);
    if (again && !again.disabled) { if (document.activeElement !== again) again.focus({ preventScroll: true }); }
    else if (!dialog.contains(document.activeElement) || (document.activeElement && document.activeElement.disabled)) focusTitle();
  }

  dialog.addEventListener("close", function () {
    root.classList.remove("has-sheet");
    A.view = "list";
    A.error = null;
    if (A.opener && A.opener.focus && document.contains(A.opener)) A.opener.focus({ preventScroll: true });
  });
  // A tap on the dimmed backdrop closes the sheet; a text selection that
  // merely ends out there (pressed inside, released outside) does not.
  var pressedOutside = false;
  dialog.addEventListener("pointerdown", function (e) { pressedOutside = e.target === dialog; });
  dialog.addEventListener("click", function (e) {
    if (e.target === dialog && pressedOutside) close();
    pressedOutside = false;
  });

  function startForm(id, focus) {
    A.editing = id;
    A.focus = focus;
    A.names = null;     // filled in from the file when the form is built
  }

  function focusFirst() {
    // Only right after a view opens, never while someone is typing.
    if (!A.pendingFocus) return;
    if (A.view === "list") { A.pendingFocus = false; return; }
    if (A.view === "form" && A.editing && A.focus !== "since" && touch()) {
      // On a phone, focus in a text field brings up the keyboard over the
      // form; changing a monitor rarely starts with its name.
      A.pendingFocus = false;
      focusTitle();
      return;
    }
    var target = null;
    if (A.view === "form") target = body.querySelector(A.focus === "since" ? "#f-since" : "#f-name");
    else if (A.view === "connect") target = body.querySelector("#f-token");
    if (target) {
      A.pendingFocus = false;
      target.focus({ preventScroll: false });
      if (A.focus === "since") target.scrollIntoView({ block: "center" });
    }
  }

  function touch() {
    try { return window.matchMedia("(pointer: coarse)").matches; } catch (e) { return false; }
  }

  // `fresh` builds the view anew; otherwise an open form keeps what is typed.
  function render(fresh) {
    if (!dialog) return;
    backBtn.hidden = A.view === "list";
    titleEl.textContent = A.view === "connect" ? "Forbind GitHub"
      : A.view === "form" ? (A.editing ? "Ret overvågning" : "Ny overvågning") : "Overvågninger";
    if (!fresh && A.view === "form" && body.querySelector("form")) {
      renderFormState();
      return;
    }
    keepFocus(function () {
      body.textContent = "";
      if (A.view === "connect") renderConnect();
      else if (A.view === "form") renderForm();
      else renderList();
    });
  }

  // While a scan is followed only its status line changes; the list stays as it is.
  function refreshFollow() {
    if (A.view !== "list") return;
    keepFocus(function () {
      var old = body.querySelector(".sheet__follow"), box = followBox();
      if (old && box) old.replaceWith(box);
      else if (old) old.remove();
      else if (box) append(body, box);
      var scan = body.querySelector("[data-act=scan-now]");
      if (scan) scan.disabled = A.busy || A.dispatching;
    });
  }

  function errorBox(text) {
    return text ? el("p", { class: "sheet__error", role: "alert", text: text }) : null;
  }

  function renderList() {
    var d = ST.state.data;
    if (READ_ONLY) {
      append(body, el("p", { class: "sheet__note" }, "I eksempelvisningen kan overvågninger ikke ændres. ",
        "I den rigtige Sætradar kan du tilføje kunstnere og flytte startdatoen her."));
    } else if (!A.auth) {
      append(body, el("div", { class: "sheet__card sheet__card--quiet" },
        el("p", null, "Du kan se, hvad der overvåges. For at tilføje kunstnere og ændre startdatoer skal appen have lov at gemme i dit GitHub-repo."),
        el("button", { class: "btn btn--play", type: "button", "data-act": "connect-view", text: "Forbind GitHub" })));
    } else {
      append(body, el("div", { class: "sheet__account" },
        el("span", { class: "led", "data-state": "ok" }),
        el("span", { text: "Forbundet som " + A.auth.login }),
        el("button", { class: "linklike", type: "button", "data-act": "disconnect", text: "Afbryd" })));
    }
    append(body, errorBox(A.error));
    (d && d.problems || []).forEach(function (p) {
      append(body, el("p", { class: "sheet__error", text: "Kunne ikke scannes: " + p }));
    });

    var list = el("ul", { class: "monitors" });
    var all = artists();
    if (A.loading && !all.length) append(list, el("li", { class: "monitor monitor--loading", text: "Henter overvågninger …" }));
    all.forEach(function (a) {
      var names = uniqueNames(a.searchNames || [a.name]);
      var waiting = A.file && !scanned(a.id);
      var backlog = waiting ? 0 : backlogOf(a.id);
      append(list, el("li", { class: "monitor" },
        el("div", { class: "monitor__main" },
          el("span", { class: "monitor__name", lang: ui.hasCJK(a.displayName) ? "ja" : null, text: a.displayName || a.name }),
          a.displayName && a.displayName !== a.name ? el("span", { class: "monitor__sub", text: a.name }) : null,
          el("span", { class: "monitor__meta" },
            "Søger fra " + describeStart(a.trackingSince) + " · " + (names.length === 1 ? "1\u00a0stavemåde" : names.length + "\u00a0stavemåder")),
          waiting ? el("span", { class: "tag tag--wait", text: "Venter på første scanning" }) : null,
          backlog ? el("span", { class: "monitor__note", text: "Søger stadig længere tilbage: " + backlog + " uploads vurderes ved næste scanning." }) : null),
        A.auth && !READ_ONLY ? el("button", { class: "btn btn--quiet", type: "button", "data-act": "edit", "data-id": a.id, disabled: A.busy },
          icon("edit", "icon--s"), el("span", { text: "Ret" })) : null));
    });
    append(body, list);
    if (!all.length && !A.loading) append(body, el("p", { class: "sheet__note", text: "Ingen overvågninger endnu." }));

    if (A.auth && !READ_ONLY) {
      append(body, el("div", { class: "sheet__actions" },
        el("button", { class: "btn btn--play", type: "button", "data-act": "add", disabled: A.busy || A.loading }, icon("plus", "icon--s"), el("span", { text: "Tilføj overvågning" })),
        el("button", { class: "btn btn--quiet", type: "button", "data-act": "scan-now", disabled: A.busy || A.dispatching, text: "Scan nu" })));
    }
    append(body, followBox());
  }

  function followBox() {
    var f = A.follow;
    if (!f) return null;
    var box = el("div", { class: "sheet__follow", role: "status" },
      el("span", { class: "led", "data-state": f.state === "failed" ? "bad" : f.state === "done" ? "ok" : "busy" }),
      el("span", { text: f.text }));
    if (f.url && ui.safeUrl(f.url)) append(box, el("a", { href: ui.safeUrl(f.url), target: "_blank", rel: "noopener noreferrer", text: "Se på GitHub" }));
    return box;
  }

  // A labelled field. `control` is what is shown (an input, or a group around
  // one); `inputEl` is the input the label, hint and error belong to.
  function field(key, id, label, control, hint, inputEl) {
    inputEl = inputEl || control;
    inputEl.id = id;
    var hintId = hint ? id + "-hint" : null;
    inputEl.setAttribute("aria-describedby", [hintId, id + "-err"].filter(Boolean).join(" "));
    return el("div", { class: "field", "data-field": key },
      el("label", { for: id, text: label }), control,
      hint ? el("p", { class: "field__hint", id: hintId, text: hint }) : null,
      el("p", { class: "field__error", id: id + "-err", hidden: true }));
  }

  function showFieldErrors(errors) {
    body.querySelectorAll("[data-field]").forEach(function (f) {
      var key = f.getAttribute("data-field"), msg = errors[key];
      var err = f.querySelector(".field__error");
      var inputEl = f.querySelector("input");
      f.classList.toggle("field--error", !!msg);
      err.textContent = msg || "";
      err.hidden = !msg;
      if (inputEl) { if (msg) inputEl.setAttribute("aria-invalid", "true"); else inputEl.removeAttribute("aria-invalid"); }
    });
    var first = body.querySelector("[aria-invalid=true]");
    if (first) {
      var folded = first.closest("details");
      if (folded) folded.open = true;
      first.focus();
    }
  }

  function input(attrs) {
    return el("input", Object.assign({ autocomplete: "off", autocapitalize: "off", spellcheck: "false" }, attrs));
  }

  function renderForm() {
    if (!A.file) {
      // The form edits the file in the repo, so it waits for it: the page's
      // own data leaves out details (like profiles) a save would then drop.
      append(body, el("p", { class: "sheet__note", role: "status", text: A.loading ? "Henter opsætningen fra GitHub …" : "" }));
      append(body, errorBox(A.error));
      if (!A.loading) append(body, el("div", { class: "sheet__actions" },
        el("button", { class: "btn btn--quiet", type: "button", "data-act": "retry", text: "Prøv igen" })));
      return;
    }
    var a = A.editing ? artists().find(function (x) { return x.id === A.editing; }) : null;
    if (A.editing && !a) { A.view = "list"; A.error = "Overvågningen findes ikke længere."; render(true); return; }
    if (A.names === null) {
      A.names = a ? uniqueNames(a.searchNames || []).filter(function (n) {
        return fold(n) !== fold(a.name) && fold(n) !== fold(a.displayName || "");
      }) : [];
    }
    A.formSha = A.file.sha;
    A.dirty = false;
    var src = (a && a.sources) || {};
    var form = el("form", { class: "sheet__form", novalidate: true });

    append(form, field("name", "f-name", "Kunstnerens navn",
      input({ name: "name", value: a ? a.name : "", maxlength: "80", autocapitalize: "words", required: true }),
      "Med almindelige bogstaver, fx Yousuke Yukimatsu."));
    append(form, field("display", "f-display", "Navnet som kunstneren selv skriver det (valgfrit)",
      input({ name: "display", value: a && a.displayName && a.displayName !== a.name ? a.displayName : "", maxlength: "80" }),
      "Med specialtegn, fx ¥ØU$UK€ ¥UK1MAT$U. Vises som overskrift."));

    var nameInput = input({ name: "extra", placeholder: "fx YØU$UK€ YUK1MAT$U eller 行松陽介", maxlength: "80", enterkeyhint: "done" });
    var must = (a && a.mustMention) || [], skip = (a && a.exclude) || [];
    var namesGroup = el("div", { class: "names" },
      el("ul", { class: "chips", id: "f-names-list", "aria-label": "Andre stavemåder" }),
      el("div", { class: "chips__add" }, nameInput,
        el("button", { class: "btn btn--quiet", type: "button", "data-act": "add-name", text: "Tilføj" })));
    append(form, field("names", "f-extra", "Andre stavemåder og søgninger", namesGroup,
      "Navnene ovenfor søges altid. Hver linje her søges også på YouTube og SoundCloud, fx en anden stavemåde eller “WORSHIP drum and bass”.", nameInput));

    var since = input({ name: "since", type: "datetime-local", required: true,
      value: toLocalInput(a ? a.trackingSince : new Date().toISOString()) });
    var sinceGroup = el("div", { class: "since" }, since,
      el("p", { class: "since__echo", id: "f-since-echo", "aria-live": "polite" }),
      el("div", { class: "presets", role: "group", "aria-label": "Hurtigvalg" },
        [["now", "Fra nu"], ["7", "7 dage"], ["30", "30 dage"], ["365", "1 år"]].map(function (p) {
          return el("button", { class: "preset", type: "button", "data-act": "preset", "data-days": p[0], text: p[1] });
        })));
    append(form, field("since", "f-since", "Vis sæt udgivet efter", sinceGroup,
      a ? "Flytter du datoen tilbage, søger næste scanning længere tilbage. Flytter du den frem, forsvinder sæt fra før den nye dato."
        : "Vælg en dato i fortiden for også at få sæt, der allerede er udgivet.", since));

    append(form, field("soundcloud", "f-soundcloud", "SoundCloud-profil (valgfrit)",
      input({ name: "soundcloud", value: ((src.soundcloud || {}).users || []).join(", "), placeholder: "soundcloud.com/navn", inputmode: "url" }),
      "Uploads derfra tæller med, også når titlen ikke nævner navnet."));
    append(form, field("youtube", "f-youtube", "YouTube-kanal (valgfrit)",
      input({ name: "youtube", value: ((src.youtube || {}).channels || []).join(", "), placeholder: "@kanal eller link", inputmode: "url" }),
      "Kanalens nyeste videoer gennemgås ved hver scanning."));

    // For names that are also ordinary words ("WORSHIP"): folded away unless in use.
    append(form, el("details", { class: "more", id: "f-more", open: must.length || skip.length ? true : null },
      el("summary", null, el("span", { text: "Undgå forkerte fund" }), el("span", { class: "more__hint", text: "valgfrit" })),
      el("div", { class: "more__body" },
        el("p", { class: "field__hint", text: "Til navne, der også er almindelige ord. Skriv ord eller navne adskilt af komma." }),
        field("must", "f-must", "Skal også nævne",
          input({ name: "must", value: must.join(", "), placeholder: "fx Sub Focus, Dimension, drum and bass", maxlength: "600" }),
          "Et fund tæller kun, hvis titlen eller uploaderen også nævner ét af disse. Kunstnerens egne profiler og kendte platforme som Boiler Room tæller altid."),
        field("exclude", "f-exclude", "Udelad titler med",
          input({ name: "exclude", value: skip.join(", "), placeholder: "fx praise, prayer, church", maxlength: "600" }),
          "Fund, hvis titel nævner et af disse ord, springes over."))));

    append(form, el("div", { class: "sheet__error-slot", id: "f-error" }));
    append(form, el("div", { class: "sheet__actions" },
      el("button", { class: "btn btn--play", type: "submit", "data-act": "save", id: "f-save" }, el("span", { text: a ? "Gem ændringer" : "Tilføj og scan" })),
      el("button", { class: "btn btn--quiet", type: "button", "data-act": "cancel", text: "Annuller" })));
    if (a) {
      append(form, el("div", { class: "sheet__danger" },
        el("button", { class: "btn btn--danger", type: "button", "data-act": "remove", id: "f-remove" }, el("span", { text: "Fjern overvågning" }))));
    }
    append(body, form);
    since.addEventListener("input", echoSince);
    since.addEventListener("change", echoSince);
    renderChips();
    echoSince();
    renderFormState();
  }

  function renderChips() {
    var list = body.querySelector("#f-names-list");
    if (!list) return;
    list.textContent = "";
    A.names.forEach(function (n, i) {
      append(list, el("li", { class: "chip", lang: ui.hasCJK(n) ? "ja" : null },
        el("span", { text: n }),
        el("button", { type: "button", class: "chip__x", "data-act": "drop-name", "data-index": String(i), "aria-label": "Fjern " + n }, icon("close", "icon--s"))));
    });
    list.hidden = !A.names.length;
  }

  function renderFormState() {
    // aria-disabled, not disabled: the button keeps focus while it works.
    var save = body.querySelector("#f-save");
    if (save) {
      save.setAttribute("aria-disabled", A.busy ? "true" : "false");
      save.querySelector("span").textContent = A.busy && !A.removing ? "Gemmer …" : (A.editing ? "Gem ændringer" : "Tilføj og scan");
    }
    var remove = body.querySelector("#f-remove");
    if (remove) {
      remove.setAttribute("aria-disabled", A.busy ? "true" : "false");
      remove.querySelector("span").textContent = A.removing ? "Fjerner …" : A.confirmRemove ? "Tryk igen for at fjerne" : "Fjern overvågning";
      remove.classList.toggle("is-armed", A.confirmRemove || !!A.removing);
    }
    var slot = body.querySelector("#f-error");
    if (slot) { slot.textContent = ""; append(slot, errorBox(A.error)); }
  }

  function renderConnect() {
    var steps = el("ol", { class: "steps" },
      el("li", null, "Åbn ", el("a", {
        href: "https://github.com/settings/personal-access-tokens/new?name=S%C3%A6tradar&description=Overv%C3%A5gninger%20i%20S%C3%A6tradar",
        target: "_blank", rel: "noopener noreferrer", text: "GitHubs side for nye tokens"
      }), "."),
      el("li", null, "Under ", el("b", { text: "Repository access" }), " vælger du ", el("b", { text: "Only select repositories" }), " og ", el("b", { text: REPO.split("/")[1] || "repoet" }), "."),
      el("li", null, "Under ", el("b", { text: "Permissions" }), ": ", el("b", { text: "Contents" }), " og ", el("b", { text: "Actions" }), " sættes til ", el("b", { text: "Read and write" }), "."),
      el("li", null, "Tryk ", el("b", { text: "Generate token" }), ", kopiér det og sæt det ind herunder."));
    append(body, el("p", { class: "sheet__lead", text: A.why === "scan"
      ? "Scanningerne kører på GitHub. For at starte én herfra skal appen forbindes til dit GitHub-repo én gang med et adgangstoken, der kun gælder dét repo. Bagefter starter scanningen med det samme."
      : "Sætradaren gemmer overvågninger direkte i dit GitHub-repo. Det kræver et adgangstoken, der kun gælder dét repo." }));
    append(body, steps);
    var form = el("form", { class: "sheet__form", novalidate: true });
    append(form, field("token", "f-token", "Token", input({ name: "token", type: "password", placeholder: "github_pat_…" }),
      "Tokenet gemmes kun i denne browser og sendes kun til GitHub."));
    append(form, el("div", { class: "sheet__error-slot", id: "f-error" }, errorBox(A.error)));
    append(form, el("div", { class: "sheet__actions" },
      el("button", { class: "btn btn--play", type: "submit", "data-act": "connect", id: "f-connect", "aria-disabled": A.busy ? "true" : "false" }, el("span", { text: A.busy ? "Forbinder …" : "Forbind" })),
      el("button", { class: "btn btn--quiet", type: "button", "data-act": "cancel", text: "Annuller" })));
    append(body, form);
  }

  /* ------------------------------------------------------------- actions */

  function readForm() {
    var f = body.querySelector("form");
    var v = {
      name: f.elements.name.value.trim().replace(/\s+/g, " "),
      display: f.elements.display.value.trim().replace(/\s+/g, " "),
      since: fromLocalInput(f.elements.since.value),
      soundcloud: splitList(f.elements.soundcloud.value),
      youtube: splitList(f.elements.youtube.value)
    };
    var errors = {};
    if (!v.name) errors.name = "Skriv kunstnerens navn.";
    if (!v.since) errors.since = "Vælg dato og klokkeslæt.";
    else if (v.since - Date.now() > 365 * DAY) errors.since = "Datoen ligger mere end et år ude i fremtiden.";
    v.users = v.soundcloud.map(soundcloudUser);
    if (v.users.some(function (u) { return !u; })) errors.soundcloud = "Skriv et brugernavn eller et link til profilen, fx soundcloud.com/navn.";
    v.channels = v.youtube.map(youtubeChannel);
    if (v.channels.some(function (c) { return !c; })) errors.youtube = "Skriv et @kanalnavn eller et link til kanalen, fx youtube.com/@navn.";
    // A spelling typed but not yet added with "Tilføj" counts too.
    v.pending = f.elements.extra.value.trim().replace(/\s+/g, " ");
    v.names = uniqueNames([v.name, v.display].concat(A.names, v.pending ? [v.pending] : []));
    if (v.names.length > MAX_NAMES) errors.names = "Højst " + MAX_NAMES + " stavemåder i alt (hver koster en søgning pr. scanning).";
    v.users = uniqueNames(v.users);
    v.channels = uniqueNames(v.channels);
    v.must = uniqueNames(splitPhrases(f.elements.must.value));
    v.exclude = uniqueNames(splitPhrases(f.elements.exclude.value));
    [["must", v.must], ["exclude", v.exclude]].forEach(function (pair) {
      if (pair[1].length > MAX_WORDS) errors[pair[0]] = "Højst " + MAX_WORDS + " ord eller navne.";
      else if (pair[1].some(function (w) { return w.length > 60; })) errors[pair[0]] = "Adskil ordene med komma (højst 60 tegn hver).";
    });
    return { value: v, errors: errors };
  }

  // "Sub Focus, drum and bass; dnb" -> ["Sub Focus", "drum and bass", "dnb"]
  function splitPhrases(text) {
    return String(text || "").split(/[,;\n]+/).map(function (x) { return x.trim().replace(/\s+/g, " "); }).filter(Boolean);
  }

  function addPendingName() {
    var inputEl = body.querySelector("#f-extra");
    if (!inputEl) return;
    var n = inputEl.value.trim().replace(/\s+/g, " ");
    if (!n) return;
    A.names = uniqueNames(A.names.concat([n]));
    A.dirty = true;
    inputEl.value = "";
    renderChips();
    inputEl.focus();
  }

  // The next chip's button takes focus, or the field when none is left.
  function dropName(index) {
    A.names.splice(index, 1);
    A.dirty = true;
    renderChips();
    var buttons = body.querySelectorAll(".chip__x");
    var next = buttons[Math.min(index, buttons.length - 1)] || body.querySelector("#f-extra");
    if (next) next.focus();
  }

  function applyForm(artist, v) {
    var oldName = artist.name;
    artist.name = v.name;
    artist.displayName = v.display || v.name;
    if (!artist.subtitle || artist.subtitle === oldName) artist.subtitle = artist.displayName !== artist.name ? artist.name : "";
    artist.trackingSince = isoUTC(v.since);
    artist.searchNames = v.names;
    if (v.must.length) artist.mustMention = v.must; else delete artist.mustMention;
    if (v.exclude.length) artist.exclude = v.exclude; else delete artist.exclude;
    artist.sources = artist.sources || {};
    artist.sources.youtube = Object.assign({ searchQueries: [] }, artist.sources.youtube, { channels: v.channels });
    artist.sources.soundcloud = Object.assign({ searchQueries: [] }, artist.sources.soundcloud, { users: v.users });
    artist.links = artist.links || {};
    if (v.users[0]) artist.links.soundcloud = "https://soundcloud.com/" + v.users[0];
    if (v.channels[0]) artist.links.youtube = v.channels[0];
    else if (!artist.links.youtube) {
      artist.links.youtube = "https://www.youtube.com/results?search_query=" + encodeURIComponent(v.name).replace(/%20/g, "+") + "&sp=CAI%253D";
    }
    return artist;
  }

  async function save() {
    var r = readForm();
    A.error = null;
    showFieldErrors(r.errors);
    if (Object.keys(r.errors).length) { renderFormState(); return; }
    if (r.value.pending) { A.names = uniqueNames(A.names.concat([r.value.pending])); body.querySelector("#f-extra").value = ""; renderChips(); }
    var v = r.value;
    var editing = A.editing;
    A.busy = true;
    render();
    try {
      var message;
      var sha = await commit(function (cfg) {
        cfg.artists = cfg.artists || [];
        var clash = cfg.artists.find(function (x) { return x.id !== editing && fold(x.name) === fold(v.name); });
        if (clash) throw GitHubError("Der findes allerede en overvågning af " + clash.name + ".", 0);
        if (editing) {
          var current = cfg.artists.find(function (x) { return x.id === editing; });
          if (!current) throw GitHubError("Overvågningen er fjernet et andet sted.", 0);
          var moved = Date.parse(current.trackingSince) !== v.since.getTime();
          applyForm(current, v);
          message = "Sætradar: ret " + v.name + (moved ? " (søger fra " + describeStart(isoUTC(v.since)).replace(/\u00a0/g, " ") + ")" : "");
        } else {
          var artist = applyForm({ id: uniqueId(slug(v.name), cfg.artists.map(function (x) { return x.id; })), aliases: [] }, v);
          cfg.artists.push(artist);
          message = "Sætradar: overvåg " + v.name;
        }
        return cfg;
      }, function () { return message; });
      A.view = "list";
      A.editing = null;
      A.busy = false;
      render();
      notify(editing ? "Gemt. Sætradaren scanner igen nu." : "Tilføjet. Sætradaren scanner nu.");
      follow({ sha: sha });
    } catch (e) {
      A.error = e.message;
      if (e.status === 401) disconnect(true);
    } finally {
      A.busy = false;
      render();
    }
  }

  async function remove() {
    if (!A.confirmRemove) {
      A.confirmRemove = true;
      renderFormState();
      clearTimeout(A.disarm);
      A.disarm = setTimeout(function () { A.confirmRemove = false; renderFormState(); }, 5000);
      return;
    }
    var id = A.editing;
    var name = (artists().find(function (x) { return x.id === id; }) || {}).name || id;
    clearTimeout(A.disarm);
    A.busy = true;
    A.removing = true;
    A.confirmRemove = false;
    renderFormState();
    try {
      var sha = await commit(function (cfg) {
        cfg.artists = (cfg.artists || []).filter(function (x) { return x.id !== id; });
        return cfg;
      }, "Sætradar: stop med at overvåge " + name);
      A.view = "list";
      A.editing = null;
      A.busy = false;
      A.removing = false;
      render();
      notify(name + " overvåges ikke længere.");
      follow({ sha: sha });
    } catch (e) {
      A.error = e.message;
      if (e.status === 401) disconnect(true);
    } finally {
      A.busy = false;
      A.removing = false;
      render();
    }
  }

  async function connect() {
    var token = (body.querySelector("#f-token").value || "").trim();
    if (!token) { A.error = "Sæt tokenet ind først."; renderConnectState(); body.querySelector("#f-token").focus(); return; }
    A.busy = true;
    A.error = null;
    renderConnectState();
    try {
      var user = await gh("/user", { token: token });
      var repo = await gh("/repos/" + REPO, { token: token });
      A.branch = repo.default_branch;
      await gh(contentsPath() + "?ref=" + encodeURIComponent(A.branch), { token: token });
      A.auth = { token: token, login: user.login || "GitHub" };
      writeAuth(A.auth);
      A.file = null;
      A.view = "list";
      A.busy = false;
      notify("Forbundet som " + A.auth.login);
      render(true);
      syncPage();
      loadConfig(true);
      if (A.why === "scan") { A.why = null; scanNow(); }
    } catch (e) {
      A.error = e.message;
    } finally {
      A.busy = false;
      if (A.view === "connect") renderConnectState();
    }
  }

  function renderConnectState() {
    var btn = body.querySelector("#f-connect");
    if (btn) {
      btn.setAttribute("aria-disabled", A.busy ? "true" : "false");
      btn.querySelector("span").textContent = A.busy ? "Forbinder …" : "Forbind";
    }
    var slot = body.querySelector("#f-error");
    if (slot) { slot.textContent = ""; append(slot, errorBox(A.error)); }
  }

  function disconnect(expired) {
    A.auth = null;
    A.file = null;
    A.branch = null;
    writeAuth(null);
    // Following a scan needs the token too.
    clearTimeout(A.timer);
    if (A.follow) { A.follow = null; ST.setActivity(null); }
    if (!expired) notify("Afbrudt. Tokenet er slettet fra denne browser.");
    A.view = expired ? "connect" : "list";
    render(true);
    syncPage();
  }

  // The page's refresh button and "Scan nu" depend on the connection.
  function syncPage() { if (ST.state.data) ST.render(); }

  // Starts a scan on GitHub now, also while another one runs: GitHub queues
  // it and runs it next. From the page (top bar, "Scan nu") as well as here.
  async function scanNow() {
    if (READ_ONLY) return;
    if (!A.auth) { open("connect", { why: "scan" }); return; }
    if (A.dispatching) return;
    var queued = scanning();
    A.dispatching = true;
    A.error = null;
    refreshFollow();
    try {
      var b = await branch();
      var after = Date.now();
      await gh("/repos/" + REPO + "/actions/workflows/" + encodeURIComponent(WORKFLOW) + "/dispatches", { method: "POST", body: { ref: b } });
      A.dispatching = false;
      notify(queued ? "Scanning sat i kø: den starter, når den igangværende er færdig." : "Scanning startet.");
      follow({ after: after, event: "workflow_dispatch" });
    } catch (e) {
      A.dispatching = false;
      var text = e.status === 403 ? "Tokenet må ikke starte scanninger. Giv det “Actions: Read and write”." : e.message;
      if (e.status === 401) {
        A.error = e.message;
        disconnect(true);
        if (!dialog.open) ST.toast("GitHub afviste tokenet. Forbind igen under Overvågninger.");
      } else if (dialog.open) {
        A.error = text;
        render();
      } else {
        refreshFollow();
        ST.toast(text);
      }
    }
  }

  /* --------------------------------------------------- following a scan */

  function follow(target) {
    clearTimeout(A.timer);
    A.follow = { target: target, text: "Venter på GitHub …", state: "waiting", url: null, started: Date.now() };
    ST.setActivity("Scanner …");
    refreshFollow();
    A.timer = setTimeout(poll, Math.min(POLL_MS, 3000));
  }

  async function poll() {
    var f = A.follow;
    if (!f) return;
    try {
      var t = f.target;
      var q = t.sha ? "&head_sha=" + encodeURIComponent(t.sha) : t.event ? "&event=" + encodeURIComponent(t.event) : "";
      var res = await gh("/repos/" + REPO + "/actions/workflows/" + encodeURIComponent(WORKFLOW) + "/runs?per_page=5" + q);
      var run = (res.workflow_runs || []).filter(function (r) {
        // A minute's slack for a clock that is off, but not when skipping a cancelled run.
        return t.sha ? r.head_sha === t.sha : r.id !== t.skip && Date.parse(r.created_at) >= t.after - (t.skip ? 0 : 60000);
      })[0];
      if (A.follow !== f) return;           // stopped while asking
      if (run) {
        f.url = run.html_url;
        f.runCreated = run.created_at;
        if (run.status === "completed" && run.conclusion === "cancelled") {
          // A newer run took its place in GitHub's queue; it scans the same, newer config.
          f.target = { after: Date.parse(run.created_at), skip: run.id };
          f.state = "waiting";
          f.text = "Venter på den næste scanning …";
          run = null;
        } else if (run.status === "completed") { finish(run.conclusion); return; }
      }
      if (run) {
        f.state = "running";
        f.text = run.status === "queued" || run.status === "pending" || run.status === "waiting"
          ? "I kø hos GitHub …" : "Scanner YouTube og SoundCloud og udgiver siden …";
      }
    } catch (e) {
      if (A.follow !== f) return;
      if (e.status === 401) {
        // The token stopped working: no point asking for 30 minutes.
        A.error = e.message;
        disconnect(true);
        return;
      }
      f.text = "Kan ikke følge scanningen lige nu: " + e.message;
    }
    if (Date.now() - f.started > 30 * 60000) { finish("timeout"); return; }
    refreshFollow();
    A.timer = setTimeout(poll, POLL_MS);
  }

  function accepted() {
    var d = ST.state.data;
    return d ? d.items.filter(function (it) { return it.status === "accepted"; }).map(function (it) { return it.id; }) : [];
  }

  async function finish(conclusion) {
    var f = A.follow;
    if (!f) return;
    ST.setActivity(null);
    if (conclusion !== "success") {
      f.state = "failed";
      f.text = conclusion === "timeout" ? "Scanningen tager usædvanligt lang tid. Siden opdaterer sig selv, når den er færdig."
        : "Scanningen meldte fejl. Se detaljerne på GitHub.";
      refreshFollow();
      // Part of it may have worked (the page is published unless its checks failed).
      ST.reload();
      return;
    }
    f.state = "done";
    f.text = "Færdig. Henter de nye data …";
    refreshFollow();
    var before = accepted();
    // GitHub Pages can take a moment to serve the new deployment.
    var since = Date.parse(f.runCreated || 0) || f.started;
    for (var i = 0; i < 8; i++) {
      await ST.reload();
      var d = ST.state.data;
      if (d && Date.parse(d.generatedAt || 0) >= since - 5000) break;
      await new Promise(function (r) { setTimeout(r, POLL_MS); });
    }
    if (A.follow !== f) return;
    var fresh = accepted().filter(function (id) { return before.indexOf(id) === -1; }).length;
    var waiting = (ST.state.data ? ST.state.data.artists : []).reduce(function (n, a) { return n + (a.backlog || 0); }, 0);
    f.text = "Opdateret " + ui.fmtTime.format(new Date()) + ": " +
      (fresh === 0 ? "ingen nye sæt." : fresh === 1 ? "1 nyt sæt." : fresh + " nye sæt.") +
      (waiting ? " " + waiting + " ældre uploads vurderes ved næste scanning." : "");
    refreshFollow();
    if (A.auth) loadConfig(true);
    notify(f.text);
    setTimeout(function () { if (A.follow === f) { A.follow = null; refreshFollow(); } }, 15000);
  }

  /* -------------------------------------------------------------- events */

  document.addEventListener("click", function (e) {
    var t = e.target instanceof Element ? e.target.closest("[data-manage]") : null;
    if (!t) return;
    var what = t.getAttribute("data-manage");
    if (what === "list") open("list");
    else if (what === "add") open("form", { id: null });
    else if (what.indexOf("start:") === 0) open(A.auth && !READ_ONLY ? "form" : "list", { id: what.slice(6), focus: "since" });
    else if (what.indexOf("edit:") === 0) open("form", { id: what.slice(5) });
  });

  document.getElementById("manage-close").addEventListener("click", close);
  backBtn.addEventListener("click", function () {
    A.view = "list";
    A.why = null;
    A.error = null;
    A.confirmRemove = false;
    render(true);
    focusTitle();
  });

  body.addEventListener("click", function (e) {
    var b = e.target instanceof Element ? e.target.closest("[data-act]") : null;
    if (!b || b.disabled || (b.getAttribute("aria-disabled") === "true" && !b.form)) return;
    var act = b.getAttribute("data-act");
    if (act === "save" || act === "connect") return;      // handled by the form's submit
    e.preventDefault();
    if (act === "connect-view") { A.view = "connect"; A.error = null; A.pendingFocus = true; render(true); focusFirst(); }
    else if (act === "disconnect") disconnect(false);
    else if (act === "add") { A.view = "form"; startForm(null, null); A.pendingFocus = true; render(true); focusFirst(); }
    else if (act === "edit") { A.view = "form"; startForm(b.getAttribute("data-id"), null); A.pendingFocus = true; render(true); focusFirst(); }
    else if (act === "cancel") { A.view = "list"; A.why = null; A.error = null; render(true); }
    else if (act === "add-name") addPendingName();
    else if (act === "drop-name") dropName(Number(b.getAttribute("data-index")));
    else if (act === "preset") {
      var days = b.getAttribute("data-days");
      var d = days === "now" ? new Date() : new Date(Date.now() - Number(days) * DAY);
      body.querySelector("#f-since").value = toLocalInput(d.toISOString());
      A.dirty = true;
      echoSince();
    }
    else if (act === "remove") remove();
    else if (act === "retry") { A.error = null; loadConfig(true); }
    else if (act === "scan-now") scanNow();
  });

  body.addEventListener("submit", function (e) {
    e.preventDefault();
    if (A.busy) return;
    if (A.view === "connect") connect();
    else if (A.view === "form") save();
  });

  body.addEventListener("keydown", function (e) {
    // Enter also confirms a Japanese or Chinese word being typed; that Enter is the keyboard's own.
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter" && e.target && e.target.id === "f-extra") { e.preventDefault(); addPendingName(); }
  });

  body.addEventListener("input", function (e) {
    if (A.view !== "form" || !e.target || !e.target.form) return;
    A.dirty = true;
    // A field's error goes as soon as it is being corrected.
    var f = e.target.closest(".field--error");
    if (f) {
      f.classList.remove("field--error");
      var err = f.querySelector(".field__error");
      if (err) { err.hidden = true; err.textContent = ""; }
      e.target.removeAttribute("aria-invalid");
    }
  });

  // Tests and the preview use this; it never holds the token.
  ST.admin = {
    open: open, close: close, scanNow: scanNow,
    connected: function () { return !!A.auth && !READ_ONLY; },
    state: function () { return { view: A.view, connected: !!A.auth, following: !!A.follow }; }
  };
})();
