"""
verify_web.py — Phase 1 validation items 8, 10, 13 and 14, plus the API.

  8  Flask serves the dashboard with mock data and no internet access
 10  the collapsible response panel exists under every row
 13  snooze hides an item and it reappears after expiry
 14  "No reply needed" hides an item and it stays hidden across syncs

Run:  python tests\verify_web.py
"""

import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="jarvis_web_")
config.DB_PATH = os.path.join(_TMP, "verify.db")
config.LOG_PATH = os.path.join(_TMP, "sync.log")

import app as app_module  # noqa: E402
import db  # noqa: E402
import mock_outlook  # noqa: E402
import response_renderer as rr  # noqa: E402
import sync  # noqa: E402
from outlook_reader import OutlookReader  # noqa: E402

FAILURES = []

GOOD_RESPONSE = """SITUATION: The revised figures went out nine days ago with no reply. The contingency line is still unconfirmed.
ACTION: reply
DRAFT REPLY: Hi Lars, following up on the Q3 figures. Could you confirm the contingency line this week so we can lock the forecast?
URGENCY: Medium - The forecast lock date is approaching."""


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def main():
    sync.configure_logging(console=False)
    db.init_db()

    loop = sync.SyncLoop(reader_factory=lambda: OutlookReader(backend="mock"))
    loop.sync_now()

    flask_app = app_module.create_app(
        sync_loop=loop,
        outlook_reader_factory=lambda: OutlookReader(backend="mock"),
    )
    client = flask_app.test_client()

    # =======================================================================
    print("8. dashboard renders")
    # =======================================================================
    response = client.get("/")
    html = response.get_data(as_text=True)
    check("GET / returns 200", response.status_code == 200, str(response.status_code))
    check("page is HTML", "<!DOCTYPE html>" in html)

    for title in config.CATEGORY_TITLES.values():
        check(f"section present: {title}", title in html)

    check("last sync timestamp shown", "Last sync:" in html)
    check("mock subjects rendered",
          "Q3 budget revision - figures for review" in html
          and "Can you confirm the crane access dates?" in html)
    check("age bands rendered",
          all(label in html for label, _, _ in config.AGE_BANDS))
    # The indicator is an inline SVG icon (the emoji rendered in colour and
    # clashed with the dark theme). What matters is that the paperclip is
    # there and carries the filenames as its tooltip.
    check("attachment paperclip rendered",
          re.search(r'<span class="clip" title="[^"]*budget_q3_v2\.xlsx[^"]*">\s*<svg',
                    html) is not None)
    check("attachment names rendered", "budget_q3_v2.xlsx" in html)
    check("meeting rows rendered", "Pre-qualification meeting - Skagen" in html)
    check("pending acceptance indicator rendered",
          "Not accepted or declined" in html)
    check("unprepared meeting flagged", "No prep found" in html)
    check("settings panel present with config values",
          "SENT_AWAITING_REPLY_DAYS" in html and "Clear old records" in html)

    print("\n  Offline-safety scan of every served asset:")
    assets = ["/", "/static/style.css", "/static/app.js"]
    external = re.compile(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//""", re.I)
    # Any absolute URL that would need the network, wherever it appears:
    # markup attributes, CSS url(), or a fetch target in the JS.
    absolute_url = re.compile(r"""(?:https?:)?//[a-z0-9]""", re.I)
    for path in assets:
        text = client.get(path).get_data(as_text=True)
        hits = external.findall(text)
        check(f"no external src/href in {path}", not hits, str(hits[:3]))
        # Strip comments first so the word "CDN" in a comment is not a hit.
        code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
        code = re.sub(r"\{#.*?#\}", "", code, flags=re.S)
        check(f"no absolute URL in {path}",
              not absolute_url.findall(code), str(absolute_url.findall(code)[:3]))
        check(f"no known CDN host referenced in {path}",
              not any(host in code.lower() for host in
                      ("cdn.", "cdnjs", "jsdelivr", "unpkg", "googleapis",
                       "bootstrapcdn")))
    check("no @import in the stylesheet",
          "@import" not in client.get("/static/style.css").get_data(as_text=True))

    # =======================================================================
    print("\n  action buttons and the collapsible panel (item 10)")
    # =======================================================================
    check("Snooze button rendered",
          f"Snooze {config.SNOOZE_DAYS} days" in html)
    check("'No reply needed' button rendered", "No reply needed" in html)
    check("'Copy PrivateGPT prompt' button rendered",
          "Copy PrivateGPT prompt" in html)
    check("collapsible panel present under rows",
          'class="gpt-panel"' in html)
    check("panel starts hidden",
          'class="gpt-panel" hidden' in html)
    check("panel has the labelled textarea",
          "Paste PrivateGPT response here" in html)
    check("panel has the 'Save and render' button", "Save and render" in html)
    check("panel is revealed by the copy button in app.js",
          "panel.hidden = false" in
          client.get("/static/app.js").get_data(as_text=True))

    # =======================================================================
    print("\n  API surface (/api/v1)")
    # =======================================================================
    status = client.get(f"{config.API_PREFIX}/status").get_json()
    check("GET /status", status["ok"] and status["counts"]["awaiting_reply"] == 5,
          str(status["counts"]))
    items = client.get(f"{config.API_PREFIX}/items").get_json()
    check("GET /items returns all three categories",
          len(items[config.CATEGORY_OVERDUE_INBOUND]) == 6
          and len(items[config.CATEGORY_AWAITING_REPLY]) == 5
          and len(items[config.CATEGORY_MEETING]) == 7)
    check("GET /items includes age bands",
          [b["label"] for b in items["awaiting_reply_bands"]]
          == [label for label, _, _ in config.AGE_BANDS])
    one = client.get(f"{config.API_PREFIX}/items/{config.CATEGORY_MEETING}").get_json()
    check("GET /items/<category>", one["count"] == 7)
    check("unknown category is a 404",
          client.get(f"{config.API_PREFIX}/items/nope").status_code == 404)
    settings = client.get(f"{config.API_PREFIX}/settings").get_json()
    check("GET /settings exposes config and the prompt changelog",
          settings["settings"]["USER_EMAIL"] == config.USER_EMAIL
          and settings["prompt_changelog"][0]["version"] == 1)

    # =======================================================================
    print("\n9b. prompt route")
    # =======================================================================
    target = next(i for i in items[config.CATEGORY_AWAITING_REPLY]
                  if i["attachments"])
    prompt = client.get(
        f"{config.API_PREFIX}/prompt",
        query_string={"category": config.CATEGORY_AWAITING_REPLY,
                      "conversation_id": target["conversation_id"]},
    ).get_json()
    check("GET /prompt returns the full prompt", prompt["ok"]
          and prompt["prompt"].startswith("=== PRIVATEGPT PROMPT"))
    check("prompt includes attachment names",
          all(n in prompt["prompt"] for n in target["attachments"]))

    # =======================================================================
    print("\n11b/12b. response route renders the card server-side")
    # =======================================================================
    saved = client.post(f"{config.API_PREFIX}/responses", json={
        "category": config.CATEGORY_AWAITING_REPLY,
        "conversation_id": target["conversation_id"],
        "entry_id": target["entry_id"],
        "raw": GOOD_RESPONSE,
    }).get_json()
    check("POST /responses parses and saves", saved["ok"], str(saved)[:80])
    check("card HTML comes from response_card.html",
          'class="response-card"' in saved["html"]
          and "SUBJECT:" in saved["html"] and "DAYS WAITING:" in saved["html"])
    check("all four fields present in the card",
          all(text in saved["html"] for text in
              ("SITUATION", "ACTION:", "URGENCY:", "DRAFT REPLY")))
    check("'Open reply in Outlook' button present",
          "Open reply in Outlook" in saved["html"])
    check("saved response persisted",
          db.latest_ai_response(target["entry_id"])["urgency"] == "Medium")

    bad = client.post(f"{config.API_PREFIX}/responses", json={
        "category": config.CATEGORY_AWAITING_REPLY,
        "conversation_id": target["conversation_id"],
        "raw": "Just some prose with no headings.",
    }).get_json()
    check("malformed response rejected with the exact message",
          bad["ok"] is False and bad["error"] == rr.PARSE_ERROR_MESSAGE,
          bad.get("error", ""))
    check("nothing extra was saved", db.stats()["ai_responses"] == 1)

    reloaded = client.get("/").get_data(as_text=True)
    check("saved card re-renders on page load",
          "The revised figures went out nine days ago" in reloaded)

    # =======================================================================
    print("\n  Outlook actions")
    # =======================================================================
    mock_outlook.reset_call_log()
    opened = client.post(f"{config.API_PREFIX}/outlook/open",
                         json={"entry_id": target["entry_id"]}).get_json()
    check("POST /outlook/open calls Display() with the row's EntryID",
          opened["ok"] and mock_outlook.DISPLAYED[-1]["entry_id"] == target["entry_id"],
          str(mock_outlook.DISPLAYED[-1:]))

    replied = client.post(f"{config.API_PREFIX}/outlook/reply", json={
        "entry_id": target["reply_entry_id"],
        "body": "Hi Lars, following up on the Q3 figures.",
    }).get_json()
    draft = mock_outlook.DRAFTS[-1]
    check("POST /outlook/reply opens a pre-filled Reply All draft",
          replied["ok"] and draft.Body.startswith("Hi Lars,")
          and draft.Displayed and not draft.Sent)

    unavailable = app_module.create_app(
        sync_loop=loop,
        outlook_reader_factory=lambda: OutlookReader(
            application=mock_outlook.UnavailableOutlookApplication()),
    ).test_client()
    outcome = unavailable.post(f"{config.API_PREFIX}/outlook/open",
                               json={"entry_id": target["entry_id"]})
    check("Outlook unavailable returns 503, not a crash",
          outcome.status_code == 503 and outcome.get_json()["ok"] is False)

    # =======================================================================
    print("\n13. snooze hides an item and it returns after expiry")
    # =======================================================================
    victim = items[config.CATEGORY_OVERDUE_INBOUND][0]
    before = len(client.get(f"{config.API_PREFIX}/items/"
                            f"{config.CATEGORY_OVERDUE_INBOUND}").get_json()["items"])
    client.post(f"{config.API_PREFIX}/actions/snooze", json={
        "category": config.CATEGORY_OVERDUE_INBOUND,
        "conversation_id": victim["conversation_id"],
        "entry_id": victim["entry_id"],
        "subject": victim["subject"],
        "seconds": 2,          # short expiry for the test; UI uses SNOOZE_DAYS
    })
    after = client.get(f"{config.API_PREFIX}/items/"
                       f"{config.CATEGORY_OVERDUE_INBOUND}").get_json()["items"]
    check("item disappears after snoozing", len(after) == before - 1,
          f"{before} -> {len(after)}")
    check("it is the right item gone",
          victim["conversation_id"] not in [i["conversation_id"] for i in after])
    check("it is gone from the rendered page too",
          victim["subject"] not in client.get("/").get_data(as_text=True))

    print("       waiting for the snooze to expire...")
    time.sleep(2.2)
    resurfaced = client.get(f"{config.API_PREFIX}/items/"
                            f"{config.CATEGORY_OVERDUE_INBOUND}").get_json()["items"]
    check("item reappears once the snooze expires", len(resurfaced) == before,
          f"{len(resurfaced)} items")
    check("it reappears without needing a sync",
          victim["conversation_id"] in [i["conversation_id"] for i in resurfaced])

    default = client.post(f"{config.API_PREFIX}/actions/snooze", json={
        "category": config.CATEGORY_OVERDUE_INBOUND,
        "conversation_id": victim["conversation_id"],
    }).get_json()
    check(f"default snooze is {config.SNOOZE_DAYS} days",
          default["days"] == config.SNOOZE_DAYS, str(default["days"]))
    client.post(f"{config.API_PREFIX}/actions/restore", json={
        "category": config.CATEGORY_OVERDUE_INBOUND,
        "conversation_id": victim["conversation_id"],
    })

    # =======================================================================
    print("\n14. 'No reply needed' is permanent across syncs")
    # =======================================================================
    doomed = items[config.CATEGORY_AWAITING_REPLY][0]
    client.post(f"{config.API_PREFIX}/actions/dismiss", json={
        "category": config.CATEGORY_AWAITING_REPLY,
        "conversation_id": doomed["conversation_id"],
        "entry_id": doomed["entry_id"],
        "subject": doomed["subject"],
    })
    remaining = client.get(f"{config.API_PREFIX}/items/"
                           f"{config.CATEGORY_AWAITING_REPLY}").get_json()["items"]
    check("item disappears after dismissal", len(remaining) == 4,
          str(len(remaining)))

    loop.sync_now()
    after_sync = client.get(f"{config.API_PREFIX}/items/"
                            f"{config.CATEGORY_AWAITING_REPLY}").get_json()["items"]
    check("it does not come back after a sync", len(after_sync) == 4,
          str(len(after_sync)))
    check("still absent from the rendered page",
          doomed["subject"] not in client.get("/").get_data(as_text=True))
    loop.sync_now()
    check("still absent after a second sync",
          doomed["conversation_id"] not in
          [i["conversation_id"] for i in
           client.get(f"{config.API_PREFIX}/items/"
                      f"{config.CATEGORY_AWAITING_REPLY}").get_json()["items"]])
    check("dismissal is scoped to its category",
          doomed["conversation_id"] not in db.dismissed_ids(
              config.CATEGORY_OVERDUE_INBOUND))

    # =======================================================================
    print("\n  settings actions")
    # =======================================================================
    purged = client.post(f"{config.API_PREFIX}/purge", json={}).get_json()
    check("manual purge runs and reports counts", purged["ok"]
          and purged["retention_days"] == config.RETENTION_DAYS, str(purged))
    check("recent records are NOT purged",
          doomed["conversation_id"] in db.dismissed_ids(
              config.CATEGORY_AWAITING_REPLY))
    triggered = client.post(f"{config.API_PREFIX}/sync", json={}).get_json()
    check("manual sync via the API", triggered["ok"],
          str(triggered.get("result")))

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s):")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("app.py + api.py + templates + static — ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
