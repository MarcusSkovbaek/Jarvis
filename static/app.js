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

  // Clipboard API needs a secure context; localhost qualifies, but fall back
  // to a hidden textarea so the button still works everywhere.
  function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
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
        button.textContent = "Copied ✓";
        setTimeout(function () {
          button.textContent = "Copy PrivateGPT prompt";
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
  });

  // ------------------------------------------------------- topbar controls

  var settingsButton = document.getElementById("toggle-settings");
  if (settingsButton) {
    settingsButton.addEventListener("click", function () {
      var panel = document.getElementById("settings-panel");
      panel.hidden = !panel.hidden;
    });
  }

  var syncButton = document.getElementById("sync-now");
  if (syncButton) {
    syncButton.addEventListener("click", function () {
      syncButton.disabled = true;
      syncButton.textContent = "Syncing…";
      post("/sync", {}).then(function (result) {
        syncButton.disabled = false;
        syncButton.textContent = "Sync now";
        if (result.ok) {
          window.location.reload();
        } else {
          toast((result.result && result.result.error) || "Sync failed.", true);
        }
      }).catch(function () {
        syncButton.disabled = false;
        syncButton.textContent = "Sync now";
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
