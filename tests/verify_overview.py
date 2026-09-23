"""
verify_overview.py — the home screen's rules, checked one by one.

The overview adds judgement calls on top of the three verified lists: which
greeting, what the headline says, which three items make "Today's focus",
what the ring measures, and how the next day's meetings are laid out. Each
of those is a plain function in dashboard.py, and each is pinned down here
with hand-built inputs, including the edges (5:00 exactly, a meeting that has
already ended, two meetings at once, no meetings at all).

Then the whole thing is checked end to end against the mock mailbox, through
the rendered page.

Run:  python tests\\verify_overview.py
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config  # noqa: E402

FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def rule(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def local(hour, minute=0, day_offset=0):
    base = datetime.now().astimezone().replace(hour=0, minute=0, second=0,
                                               microsecond=0)
    return base + timedelta(days=day_offset, hours=hour, minutes=minute)


def email(entry_id, days, priority="normal", who="Lars Petersen"):
    return {"entry_id": entry_id, "subject": f"Subject {entry_id}",
            "days_waiting": days, "priority": priority,
            "counterparty_display": who}


def meeting(entry_id, start, minutes=60, pending=False, unprepared=False,
            all_day=False):
    end = start + timedelta(minutes=minutes)
    now = datetime.now().astimezone()
    return {"entry_id": entry_id, "subject": f"Meeting {entry_id}",
            "date": start.astimezone(timezone.utc).isoformat(),
            "end": end.astimezone(timezone.utc).isoformat(),
            "date_display": start.strftime("%a %d %b, %H:%M"),
            "hours_until": (start - now).total_seconds() / 3600,
            "pending_response": pending, "unprepared": unprepared,
            "all_day": all_day, "location": "Teams"}


def main():
    import dashboard as d

    rule("Greeting follows the local clock")
    cases = [(4, 59, "Good evening"), (5, 0, "Good morning"),
             (11, 59, "Good morning"), (12, 0, "Good afternoon"),
             (17, 59, "Good afternoon"), (18, 0, "Good evening"),
             (23, 30, "Good evening")]
    for hour, minute, expected in cases:
        got = d.greeting(local(hour, minute))
        check(f"{hour:02d}:{minute:02d} -> {expected}", got == expected, got)

    rule("Headline says what needs attention, in plain English")
    check("nothing at all",
          d.headline([], [], []) ==
          "Nothing is waiting on you. Your inbox and calendar are clear.")
    one = d.headline([email("a", 3)], [], [])
    check("one email, singular", one == "1 email is waiting on you.", one)
    q = d.headline([email("a", 3, "high"), email("b", 4)], [], [])
    check("questions are called out",
          q == "2 emails are waiting on you (1 with a direct question).", q)
    full = d.headline([email("a", 3)], [email("b", 9), email("c", 12)],
                      [meeting("m", local(10, 0, 1), pending=True)])
    check("three parts join with commas and 'and'",
          full == ("1 email is waiting on you, 2 threads are waiting on "
                   "others and 1 meeting needs an answer."), full)
    only_sent = d.headline([], [email("b", 9)], [])
    check("starts with a capital when email is absent",
          only_sent == "1 thread is waiting on others.", only_sent)

    rule("Initials and avatar colours")
    for name, expected in [("Lars Petersen", "LP"),
                           ("lars.petersen@nordvind.dk", "LP"),
                           ("Søren Vad", "SV"), ("Madonna", "MA"),
                           ("Anne-Marie Holm Jensen", "AJ"),
                           ("", "?"), (None, "?"),
                           ("Lars Petersen <lars@nordvind.dk>", "LP")]:
        got = d.initials(name)
        check(f"{name!r} -> {expected}", got == expected, got)
    tones = {d.avatar_tone(n) for n in ["a", "b", "Lars", "Maria", "Thomas", "x" * 40]}
    check("tones are within the palette",
          all(0 <= t < d.AVATAR_TONES for t in tones), str(sorted(tones)))
    check("the same person always gets the same colour",
          d.avatar_tone("Lars Petersen") == d.avatar_tone("lars petersen"))

    rule("Today's focus: most pressing first, one of each kind, no repeats")
    overdue = [email("in-old", 30), email("in-q", 5, "high"),
               email("in-q-older", 9, "high")]
    awaiting = [email("out-40", 40), email("out-8", 8)]
    soon = meeting("m-soon", local(0, 0, 0) + timedelta(
        hours=datetime.now().astimezone().hour + 3), pending=True)
    focus = d.focus_items(overdue, awaiting, [soon])
    kinds = [f["kind"] for f in focus]
    ids = [f["entry_id"] for f in focus]
    check("three items", len(focus) == 3, str(ids))
    check("a direct question comes first", kinds[0] == "question", str(kinds))
    check("the oldest question wins", ids[0] == "in-q-older", ids[0])
    check("then the imminent unanswered meeting", kinds[1] == "meeting", str(kinds))
    check("then the longest-waiting email to you",
          kinds[2] == "reply" and ids[2] == "in-old", str(ids))
    check("no item appears twice", len(set(ids)) == len(ids))
    only_sent = d.focus_items([], awaiting, [])
    check("with only sent threads, the oldest is chased first",
          [f["entry_id"] for f in only_sent] == ["out-40", "out-8"],
          str([f["entry_id"] for f in only_sent]))
    far = meeting("m-far", local(10, 0, 5), pending=True)
    check("a meeting beyond the prep window is not a focus item",
          all(f["entry_id"] != "m-far" for f in d.focus_items([], [], [far])))
    check("nothing to do gives an empty list", d.focus_items([], [], []) == [])
    reasons = [f["reason"] for f in focus]
    check("every item carries a reason", all(reasons), str(reasons))

    rule("Ring: share of upcoming meetings answered")
    check("no meetings, no ring", d.response_ring([]) is None)
    ring = d.response_ring([meeting("a", local(9, 0, 1)),
                            meeting("b", local(10, 0, 1), pending=True),
                            meeting("c", local(11, 0, 1)),
                            meeting("d", local(12, 0, 1))])
    check("3 of 4 answered is 75%", ring["percent"] == 75
          and ring["answered"] == 3 and ring["total"] == 4, str(ring))
    check("arc length matches the percentage",
          abs(ring["dash"] / ring["gap"] - 0.75) < 0.001, str(ring))

    rule("Calendar: the next day with meetings, laid out on an hour axis")
    tomorrow = [meeting("t1", local(9, 0, 1), 60),
                meeting("t2", local(9, 30, 1), 60),      # overlaps t1
                meeting("t3", local(14, 0, 1), 30),
                meeting("allday", local(0, 0, 1), 1440, all_day=True)]
    day = d.calendar_day(tomorrow, local(20, 0))
    check("picks tomorrow when today is done", day["label"] == "Tomorrow",
          day["label"])
    check("all-day items are left off the timeline",
          [e["entry_id"] for e in day["events"]] == ["t1", "t2", "t3"],
          str([e["entry_id"] for e in day["events"]]))
    t1, t2, t3 = day["events"]
    check("overlapping meetings go side by side",
          t1["lane"] != t2["lane"] and t1["width"] == t2["width"] == 50.0,
          f"{t1['lane']}/{t2['lane']} {t1['width']}%")
    check("the axis covers the working day",
          day["hours"][0] == "08:00" and day["hours"][-1] == "17:00",
          f"{day['hours'][0]}-{day['hours'][-1]}")
    check("positions are proportional",
          abs(t1["top"] - 100 / 9) < 0.1, f"top={t1['top']}")
    check("a 30-minute meeting is drawn on one line", t3["compact"])

    today = [meeting("done", local(8, 0), 30),
             meeting("later", local(21, 0), 60)]
    day2 = d.calendar_day(today, local(12, 0))
    check("today is shown while meetings remain",
          day2 and day2["label"] == "Today", str(day2 and day2["label"]))
    check("a meeting that has ended is dropped",
          [e["entry_id"] for e in day2["events"]] == ["later"])
    check("the axis stretches to fit a late meeting",
          day2["hours"][-1] == "22:00", day2["hours"][-1])
    check("an early meeting stretches it the other way",
          d.calendar_day([meeting("dawn", local(5, 30, 1))],
                         local(20, 0))["hours"][0] == "05:00")
    check("no meetings, no timeline", d.calendar_day([], local(9, 0)) is None)
    check("later days are counted",
          d.calendar_day(tomorrow + [meeting("x", local(9, 0, 3))],
                         local(20, 0))["later"] == 1)

    rule("End to end: the mock mailbox through the rendered page")
    workdir = tempfile.mkdtemp(prefix="jarvis_overview_")
    config.OUTLOOK_BACKEND = "mock"
    config.DB_PATH = os.path.join(workdir, "jarvis.db")
    config.LOG_PATH = os.path.join(workdir, "sync.log")
    import app as app_module
    import db
    import sync
    db.init_db()
    sync.run_sync()
    view = d.build_view()
    ov = view["overview"]
    check("overview is part of the view",
          set(ov) >= {"greeting", "headline", "focus", "ring", "day",
                      "top_overdue", "top_awaiting"})
    check("every email row has initials and a tone",
          all("initials" in i and "tone" in i
              for i in view["overdue_inbound"] + view["awaiting_reply"]))
    check("the summary card leads with direct questions",
          ov["top_overdue"][0]["priority"] == "high")
    html = app_module.create_app().test_client().get("/").get_data(as_text=True)
    check("greeting rendered", ov["greeting"] in html)
    check("headline rendered", ov["headline"] in html)
    check("all five home cards rendered",
          all(f"card--{c}" in html for c in
              ("email", "calendar", "brief", "awaiting", "focus")))
    check("all five views present",
          all(f'data-view="{v}"' in html for v in
              ("home", "email", "calendar", "assistant", "settings")))
    check("only the home view is visible on first paint",
          html.count('class="view is-active"') == 1
          and 'data-view="home" id="view-home"' in html)
    check("focus items rendered", all(f["title"] in html for f in ov["focus"]))
    check("ring rendered with its percentage",
          f'{ov["ring"]["percent"]}%' in html)
    check("timeline rendered with its events",
          ov["day"] and all(f'data-entry-id="{e["entry_id"]}"' in html
                            for e in ov["day"]["events"]))
    check("the dashboard prompt can be copied from home and the PrivateGPT view",
          html.count("js-copy-dashboard-prompt") >= 3)

    print(f"\n{'=' * 72}")
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED:")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("All overview checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
