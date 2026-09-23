/*
  app.js — dashboard behaviour.

  Plain ES5-compatible JavaScript, no framework, no build step, no external
  requests beyond this app's own /api/v1 routes.
*/
(function () {
  "use strict";

  var body = document.body;
  var API = body.getAttribute("data-api-prefix") || "/api/v1";
  var REFRESH = parseInt(body.getAttribute("data-refresh-seconds"), 10) || 900;

  // ---------------------------------------------------------------- helpers

  function post(path, payload) {
    return fetch(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {})
    }).then(function (response) { return response.json(); });
  }

  function get(path) {
    return fetch(API + path).then(function (response) { return response.json(); });
  }

  var toastTimer = null;
  function toast(message, isError) {
    var el = document.getElementById("toast");
    el.textContent = message;
    el.className = "banner " + (isError ? "banner--warn" : "banner--info");
    el.hidden = false;
    if (toastTimer) { clearTimeout(toastTimer); }
    toastTimer = setTimeout(function () { el.hidden = true; }, 4000);
  }

  function rowOf(element) { return element.closest(".row"); }

  function rowData(row) {
    return {
      category: row.getAttribute("data-category"),
      conversation_id: row.getAttribute("data-conversation-id"),
      entry_id: row.getAttribute("data-entry-id"),
      subject: row.getAttribute("data-subject")
    };
  }

  function removeRow(row) {
    row.classList.add("row--removing");
    setTimeout(function () { row.remove(); }, 250);
  }

  // Buttons carry an icon and a <span> label; change only the label so the
  // icon survives.
  function setLabel(button, text) {
    var label = button.querySelector("span");
    (label || button).textContent = text;
  }

  // Three routes to the clipboard, most reliable first:
  //   1. the desktop window's own bridge (window.pywebview.api), which
  //      writes through the operating system and needs no browser permission;
  //   2. the async Clipboard API — needs a secure context, which localhost is;
  //   3. a hidden textarea and execCommand("copy") for anything older.
  function copyToClipboard(text) {
    var bridge = window.pywebview && window.pywebview.api &&
      window.pywebview.api.copy_text;
    if (bridge) {
      // If the bridge never answers, do not leave the button hanging: after
      // a second and a half, fall back to the browser's own clipboard.
      var answered = false;
      return new Promise(function (resolve, reject) {
        var timer = setTimeout(function () {
          if (!answered) { answered = true; browserCopy(text).then(resolve, reject); }
        }, 1500);
        window.pywebview.api.copy_text(text).then(function (ok) {
          if (answered) { return; }
          answered = true; clearTimeout(timer);
          (ok ? Promise.resolve() : browserCopy(text)).then(resolve, reject);
        }, function () {
          if (answered) { return; }
          answered = true; clearTimeout(timer);
          browserCopy(text).then(resolve, reject);
        });
      });
    }
    return browserCopy(text);
  }

  function browserCopy(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).catch(function () {
        return legacyCopy(text);
      });
    }
    return legacyCopy(text);
  }

  function legacyCopy(text) {
    return new Promise(function (resolve, reject) {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.left = "-9999px";
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
      document.body.removeChild(area);
      ok ? resolve() : reject(new Error("Clipboard unavailable"));
    });
  }

  // Fallback when the clipboard is unavailable: render the prompt in a
  // read-only textarea inside the panel and select it, so Ctrl+C works.
  function showPromptFallback(panel, text) {
    if (!panel) { return; }
    var existing = panel.querySelector(".gpt-panel__fallback");
    if (!existing) {
      existing = document.createElement("textarea");
      existing.className = "gpt-panel__input gpt-panel__fallback";
      existing.setAttribute("readonly", "");
      existing.setAttribute("rows", "12");
      existing.setAttribute("aria-label", "Prompt text — copy this manually");
      panel.insertBefore(existing, panel.firstChild);
    }
    existing.value = text;
    existing.focus();
    existing.select();
  }

  // ----------------------------------------------------------- row actions

  function openInOutlook(entryId) {
    post("/outlook/open", { entry_id: entryId }).then(function (data) {
      if (!data.ok) { toast(data.error || "Could not open the item.", true); }
    }).catch(function () { toast("Could not reach Jarvis.", true); });
  }

  function snooze(row) {
    var data = rowData(row);
    post("/actions/snooze", data).then(function (result) {
      if (result.ok) {
        toast("Snoozed for " + result.days + " days: " + data.subject);
        removeRow(row);
      } else {
        toast(result.error || "Could not snooze.", true);
      }
    });
  }

  function dismiss(row) {
    var data = rowData(row);
    post("/actions/dismiss", data).then(function (result) {
      if (result.ok) {
        toast("Dismissed: " + data.subject);
        removeRow(row);
      } else {
        toast(result.error || "Could not dismiss.", true);
      }
    });
  }

  function copyPrompt(row, button) {
    var data = rowData(row);
    var query = "?category=" + encodeURIComponent(data.category) +
      "&conversation_id=" + encodeURIComponent(data.conversation_id) +
      "&entry_id=" + encodeURIComponent(data.entry_id);

    get("/prompt" + query).then(function (result) {
      if (!result.ok) {
        toast(result.error || "Could not build the prompt.", true);
        return;
      }
      // Always reveal the response panel, even if the copy itself fails —
      // the prompt text is still available from the API.
      var panel = row.querySelector(".gpt-panel");
      if (panel) { panel.hidden = false; }
      copyToClipboard(result.prompt).then(function () {
        toast("Prompt v" + result.prompt_version +
          " copied. Paste it into PrivateGPT, then paste the response below.");
        setLabel(button, "Copied ✓");
        setTimeout(function () {
          setLabel(button, "Copy PrivateGPT prompt");
        }, 2500);
      }).catch(function () {
        // Some browsers block clipboard writes outside a trusted gesture. Show
        // the prompt, pre-selected, so Ctrl+C still gets the job done.
        showPromptFallback(panel, result.prompt);
        toast("Could not reach the clipboard. The prompt is shown below, " +
          "already selected — press Ctrl+C to copy it.", true);
      });
    });
  }

  function saveResponse(row, button) {
    var data = rowData(row);
    var panel = row.querySelector(".gpt-panel");
    var input = panel.querySelector(".js-response-input");
    var error = panel.querySelector(".gpt-panel__error");
    var target = panel.querySelector(".gpt-panel__card");

    error.hidden = true;
    button.disabled = true;

    post("/responses", {
      category: data.category,
      conversation_id: data.conversation_id,
      entry_id: data.entry_id,
      raw: input.value
    }).then(function (result) {
      button.disabled = false;
      if (!result.ok) {
        error.textContent = result.error;
        error.hidden = false;
        return;           // nothing rendered, nothing saved, nothing broken
      }
      target.innerHTML = result.html;
      input.value = "";
      toast("Response saved and rendered.");
    }).catch(function () {
      button.disabled = false;
      error.textContent = "Could not reach Jarvis.";
      error.hidden = false;
    });
  }

  function openReplyDraft(button) {
    var card = button.closest(".response-card");
    var draft = card.querySelector(".response-card__draft");
    post("/outlook/reply", {
      entry_id: button.getAttribute("data-entry-id"),
      body: draft ? draft.textContent : ""
    }).then(function (result) {
      if (result.ok) {
        toast("Reply All draft opened in Outlook. Review it and send it yourself.");
      } else {
        toast(result.error || "Could not open the draft.", true);
      }
    });
  }

  // ------------------------------------------------ PrivateGPT dashboard

  function copyDashboardPrompt(button) {
    var original = button.querySelector("span") ?
      button.querySelector("span").textContent : button.textContent;
    get("/prompt/dashboard").then(function (result) {
      if (!result.ok) {
        toast(result.error || "Could not build the dashboard prompt.", true);
        return;
      }
      var preview = document.getElementById("dashboard-prompt-preview");
      if (preview) { preview.value = result.prompt; }
      var stat = document.getElementById("dashboard-prompt-stat");
      if (stat) {
        stat.textContent = result.words.toLocaleString() + " words, v" +
          result.prompt_version;
      }
      copyToClipboard(result.prompt).then(function () {
        toast("Dashboard prompt copied. Paste it into PrivateGPT, then save " +
          "its answer as a .html file and open it.");
        setLabel(button, "Copied ✓");
        setTimeout(function () { setLabel(button, original); }, 2500);
      }).catch(function () {
        // Show it, selected, on the PrivateGPT view so Ctrl+C still works.
        showView("assistant");
        var details = document.querySelector(".preview");
        if (details) { details.open = true; }
        if (preview) { preview.focus(); preview.select(); }
        toast("Could not reach the clipboard. The prompt is shown in the " +
          "preview, already selected — press Ctrl+C to copy it.", true);
      });
    }).catch(function () { toast("Could not reach Jarvis.", true); });
  }

  // -------------------------------------------- check a generated page
  // The file is read by the browser from this computer's disk and posted
  // only to Jarvis's own local server, which checks it in memory.

  function escapeHtml(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function renderVerdict(target, fileName, result) {
    var safe = result.verdict === "safe";
    var html = '<div class="verdict__head verdict__head--' + (safe ? "safe" : "unsafe") + '">' +
      (safe ? "Safe to open" : "Don't open this page") +
      '<span class="verdict__file">' + escapeHtml(fileName) + "</span></div>";
    if (safe) {
      html += "<p>The policy line is in place and nothing in the page can " +
        "load from, send to, or move to the network. Double-click the file " +
        "to open it.</p>";
    } else {
      html += "<p>Ask PrivateGPT to build the page again, reminding it of " +
        "the rules in the prompt. Found:</p>";
    }
    function list(items, cls) {
      if (!items.length) { return ""; }
      return '<ul class="verdict__list ' + cls + '">' + items.map(function (f) {
        return "<li><strong>" + escapeHtml(f.why) + "</strong>" +
          (f.line ? " — line " + f.line : "") +
          (f.excerpt ? "<code>" + escapeHtml(f.excerpt) + "</code>" : "") + "</li>";
      }).join("") + "</ul>";
    }
    html += list(result.blocking, "verdict__list--blocking");
    if (result.advisory.length) {
      html += "<p class=\"muted\">Also noted (blocked by the policy line, so " +
        "harmless):</p>" + list(result.advisory, "verdict__list--advisory");
    }
    target.innerHTML = html;
    target.hidden = false;
  }

  var pageInput = document.getElementById("page-check-input");
  if (pageInput) {
    pageInput.addEventListener("change", function () {
      var file = pageInput.files && pageInput.files[0];
      var target = document.getElementById("page-check-result");
      if (!file || !target) { return; }
      var reader = new FileReader();
      reader.onload = function () {
        post("/check-page", { source: String(reader.result) }).then(function (result) {
          if (!result.ok) { toast(result.error || "Could not check the page.", true); return; }
          renderVerdict(target, file.name, result);
        }).catch(function () { toast("Could not reach Jarvis.", true); });
        pageInput.value = "";          // choosing the same file again re-checks
      };
      reader.onerror = function () { toast("Could not read that file.", true); };
      reader.readAsText(file);
    });
  }

  // ---------------------------------------------------------------- views
  // Every view is in the page; the rail shows one at a time. The current
  // view lives in the URL fragment, so a reload — including the automatic
  // one — comes back to the same place.

  var VIEWS = ["home", "email", "calendar", "assistant", "settings"];

  function showView(name) {
    if (VIEWS.indexOf(name) === -1) { name = "home"; }
    document.body.setAttribute("data-view", name);
    Array.prototype.forEach.call(document.querySelectorAll(".view"), function (el) {
      var active = el.getAttribute("data-view") === name;
      el.hidden = !active;
      el.classList.toggle("is-active", active);
    });
    Array.prototype.forEach.call(document.querySelectorAll(".rail__item"), function (el) {
      el.classList.toggle("is-active", el.getAttribute("data-view-link") === name);
    });
    if (window.location.hash !== "#" + name) {
      history.replaceState(null, "", "#" + name);
    }
    var main = document.querySelector(".main");
    if (main) { main.scrollTop = 0; }
  }

  window.addEventListener("hashchange", function () {
    showView(window.location.hash.replace("#", ""));
  });
  showView(window.location.hash.replace("#", "") || "home");

  // --------------------------------------------------------- event routing

  document.addEventListener("click", function (event) {
    var target = event.target;

    var opener = target.closest(".js-open-item");
    if (opener && !target.closest(".row__actions") && !target.closest(".gpt-panel")) {
      openInOutlook(opener.getAttribute("data-entry-id"));
      return;
    }
    if (target.closest(".js-snooze")) { snooze(rowOf(target)); return; }
    if (target.closest(".js-dismiss")) { dismiss(rowOf(target)); return; }

    var copyButton = target.closest(".js-copy-prompt");
    if (copyButton) { copyPrompt(rowOf(copyButton), copyButton); return; }

    var saveButton = target.closest(".js-save-response");
    if (saveButton) { saveResponse(rowOf(saveButton), saveButton); return; }

    var replyButton = target.closest(".js-open-reply");
    if (replyButton) { openReplyDraft(replyButton); return; }

    var dashboardButton = target.closest(".js-copy-dashboard-prompt");
    if (dashboardButton) { copyDashboardPrompt(dashboardButton); return; }

    var viewLink = target.closest("[data-view-link]");
    if (viewLink) {
      event.preventDefault();
      showView(viewLink.getAttribute("data-view-link"));
      return;
    }
  });

  // ------------------------------------------------------- topbar controls

  var syncButton = document.getElementById("sync-now");
  if (syncButton) {
    syncButton.addEventListener("click", function () {
      syncButton.disabled = true;
      setLabel(syncButton, "Syncing…");
      post("/sync", {}).then(function (result) {
        syncButton.disabled = false;
        setLabel(syncButton, "Sync now");
        if (result.ok) {
          window.location.reload();
        } else {
          toast((result.result && result.result.error) || "Sync failed.", true);
        }
      }).catch(function () {
        syncButton.disabled = false;
        setLabel(syncButton, "Sync now");
        toast("Could not reach Jarvis.", true);
      });
    });
  }

  var purgeButton = document.getElementById("purge-now");
  if (purgeButton) {
    purgeButton.addEventListener("click", function () {
      post("/purge", {}).then(function (result) {
        if (!result.ok) { toast("Purge failed.", true); return; }
        var removed = result.removed;
        toast("Purged " + (removed.dismissals + removed.snoozes + removed.sync_runs) +
          " record(s) older than " + result.retention_days + " days.");
      });
    });
  }

  // ------------------------------------------------------------ auto refresh
  // Skipped while text is being typed, so a half-pasted response is never lost.
  setInterval(function () {
    var active = document.activeElement;
    if (active && active.tagName === "TEXTAREA" && active.value.trim()) { return; }
    window.location.reload();
  }, REFRESH * 1000);
})();
