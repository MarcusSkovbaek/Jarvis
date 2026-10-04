"""One scan: find candidates, judge them, write the data the page reads.

Run from the ``set-tracker`` folder:  python -m tracker [--data-dir DIR]

Files (the data folder defaults to web/data):
  config/artists.json   who to track and how (edit this to add artists)
  DIR/sets.json         everything the page shows
  DIR/sets.js           the same data as a script, so index.html also works
                        when opened straight from disk (file://)
  DIR/seen.json         uploads already judged irrelevant (old, short,
                        baseline), so they are not fetched again
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import matching, quality, sources
from .sources import SourceError

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "artists.json"
DATA_DIR = ROOT / "web" / "data"

FINAL = ("accepted", "rejected", "duplicate")
LIVE = ("is_live", "is_upcoming", "post_live")
PLATFORM_NAMES = {"youtube": "YouTube", "soundcloud": "SoundCloud"}


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(text):
    """ISO date or datetime -> aware datetime (date-only means 00:00 UTC)."""
    if not text:
        return None
    text = text.strip()
    if len(text) == 10:
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def published_after(item, since):
    """Was this published after the tracking start?

    A date without a time (common on YouTube via yt-dlp) is compared by
    calendar day, inclusively: an upload dated on the start day may well be
    later than the start time, and the first-run baseline already catches the
    ones that were online before tracking began.
    """
    if not item.get("publishedAt"):
        return None
    if item.get("publishedPrecision") == "date":
        return item["publishedAt"][:10] >= since.date().isoformat()
    return parse_time(item["publishedAt"]) > since


# ---------------------------------------------------------------------------
# Network access, behind one object so tests can replace it
# ---------------------------------------------------------------------------

class Fetcher:
    def __init__(self, youtube_api_key=None, log=print):
        self.key = youtube_api_key
        self.log = log

    def youtube_search(self, query, limit, since_iso):
        if self.key:
            return sources.youtube_search_api(query, limit, since_iso, self.key)
        return sources.youtube_search_ytdlp(query, limit)

    def youtube_channel(self, channel, limit):
        return sources.youtube_channel_ytdlp(channel, limit)

    def soundcloud_search(self, query, limit):
        return sources.soundcloud_search(query, limit)

    def soundcloud_user(self, user, limit):
        return sources.soundcloud_user(user, limit)

    def enrich(self, item):
        if item["platform"] == "youtube" and self.key and item.get("publishedPrecision") == "datetime":
            # The API already gave exact data; yt-dlp is only worth a try for
            # the audio formats, and failing that is harmless.
            try:
                return sources.enrich(item)
            except SourceError:
                return item
        return sources.enrich(item)

    def analyze(self, item, segment_seconds):
        if not quality.ffmpeg_available():
            raise SourceError("ffmpeg mangler på maskinen")
        duration = item["durationSec"]
        starts = [int(duration * 0.30), int(duration * 0.65)]
        with tempfile.TemporaryDirectory(prefix="set-tracker-") as tmp:
            files = sources.excerpts(item, starts, segment_seconds, tmp)
            parts = []
            for f in files:
                try:
                    parts.append(quality.measure(quality.decode(f, seconds=segment_seconds + 5)))
                except Exception as e:      # one bad excerpt should not sink the other
                    self.log(f"    excerpt {f.name}: {e}")
            if not parts:
                raise SourceError("lydudsnittene kunne ikke hentes")
            return quality.merge_measurements(parts)


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------

class Scanner:
    def __init__(self, config, state, data, fetcher, now=None, log=print):
        self.config = config
        self.settings = config.get("settings", {})
        self.trusted = config.get("trustedUploaders", [])
        self.state = state
        self.seen = state.setdefault("seen", {})
        self.baselined = set(state.get("baselined", []))
        self.items = {it["id"]: it for it in data.get("items", [])}
        self.fetch = fetcher
        self.now = now or datetime.now(timezone.utc)
        self.log = log
        self.health = []
        self.min_sec = int(self.settings.get("minDurationMinutes", 30)) * 60
        self.min_score = int(self.settings.get("minQualityScore", 60))
        self.limit = int(self.settings.get("maxResultsPerQuery", 40))

    # -- helpers ------------------------------------------------------------

    def _aliases(self, artist):
        return [a for a in [artist.get("name"), artist.get("displayName"), *artist.get("aliases", [])] if a]

    def _own(self, artist, item):
        url = (item.get("uploaderUrl") or "").lower().rstrip("/")
        own = [o.lower().rstrip("/") for o in artist.get("ownAccounts", [])]
        own += [f"soundcloud.com/{u.lower()}" for u in artist.get("sources", {}).get("soundcloud", {}).get("users", [])
                if not u.startswith("http")]
        return bool(url) and any(url.endswith(o) for o in own)

    def _remember(self, item, artist, reason):
        self.seen[item["id"]] = {
            "artistId": artist["id"], "reason": reason,
            "title": item.get("title", "")[:200], "durationSec": item.get("durationSec"),
            "publishedAt": item.get("publishedAt"), "at": iso(self.now),
        }

    def _jobs(self, artist):
        src = artist.get("sources", {})
        since = iso(parse_time(artist["trackingSince"]))
        yt, sc = src.get("youtube", {}), src.get("soundcloud", {})
        for q in yt.get("searchQueries", []):
            yield "youtube", f"YouTube-søgning “{q}”", lambda q=q: self.fetch.youtube_search(q, self.limit, since)
        for ch in yt.get("channels", []):
            yield "youtube", f"YouTube-kanal {ch}", lambda ch=ch: self.fetch.youtube_channel(ch, self.limit)
        for q in sc.get("searchQueries", []):
            yield "soundcloud", f"SoundCloud-søgning “{q}”", lambda q=q: self.fetch.soundcloud_search(q, self.limit)
        for u in sc.get("users", []):
            yield "soundcloud", f"SoundCloud-profil {u}", lambda u=u: self.fetch.soundcloud_user(u, self.limit)

    # -- judging ------------------------------------------------------------

    def evaluate(self, item, artist):
        """Set status, reasons and quality on an item that is after the start date."""
        item["checkedAt"] = iso(self.now)
        item.pop("reasons", None)

        if item.get("liveStatus") in LIVE or not item.get("durationSec"):
            item["status"] = "pending"
            item.setdefault("pendingSince", iso(self.now))
            item["reasons"] = (["Livestream i gang – vurderes, når den er slut"] if item.get("liveStatus") in LIVE
                               else ["Længden kunne ikke aflæses endnu – prøver igen ved næste tjek"])
            return item
        item.pop("pendingSince", None)

        if item["durationSec"] <= self.min_sec:
            item["status"] = "rejected"
            item["reasons"] = [f"For kort ({round(item['durationSec'] / 60)} min – kræver over "
                               f"{self.min_sec // 60} min)"]
            return item

        own = self._own(artist, item)
        trusted = not own and matching.is_trusted_uploader(item.get("uploader", ""), self.trusted)
        signals = quality.metadata_signals(item, trusted, own, matching.title_signals(item.get("title", "")))

        analysis, analysis_error = None, None
        if self.settings.get("audioAnalysis", True):
            try:
                analysis = self.fetch.analyze(item, int(self.settings.get("analysisSegmentSeconds", 45)))
                signals += quality.analysis_signals(analysis)
            except SourceError as e:
                analysis_error = str(e)
            except Exception as e:             # analysis is a bonus, never fatal
                analysis_error = f"{type(e).__name__}: {e}"

        score = quality.score(signals)
        item["quality"] = {
            "score": score,
            "label": quality.label_for(score),
            "signals": signals,
            "analysis": analysis,
            "verified": analysis is not None,
            "analysisError": analysis_error,
        }
        if score >= self.min_score:
            item["status"] = "accepted"
        else:
            item["status"] = "rejected"
            worst = sorted((s for s in signals if s["impact"] < 0), key=lambda s: s["impact"])
            item["reasons"] = [f"Lydkvaliteten vurderes for lav ({score}/100)"] + [s["text"] for s in worst[:3]]
        return item

    def consider(self, item, artist, since, first_run):
        """Decide what to do with one search result. Returns the stored item or None."""
        item = dict(item, artistId=artist["id"])
        known = self.items.get(item["id"])
        if known and known.get("status") in FINAL:
            if item.get("viewCount"):
                known["viewCount"] = item["viewCount"]
            return None
        if item["id"] in self.seen:
            return None
        if not (matching.names_artist(item.get("title", ""), self._aliases(artist)) or self._own(artist, item)):
            return None

        # Cheap decisions on search-result data, before any extra requests.
        if published_after(item, since) is False:
            self._remember(item, artist, "before")
            return None
        if item.get("durationSec") and item["durationSec"] <= self.min_sec and item.get("liveStatus") not in LIVE:
            # Tracks, edits and clips: never sets, so not worth showing either.
            self._remember(item, artist, "short")
            return None
        if first_run and not item.get("publishedAt"):
            self._remember(item, artist, "baseline")
            return None

        needs_more = (not item.get("publishedAt") or not item.get("durationSec") or not item.get("audio")
                      or item.get("liveStatus") in LIVE or item.get("publishedPrecision") != "datetime")
        if needs_more:
            try:
                item = dict(self.fetch.enrich(item), artistId=artist["id"])
            except SourceError as e:
                item["enrichError"] = str(e)
                self.log(f"    enrich {item['id']}: {e}")

        if not item.get("publishedAt") and known and known.get("publishedAt"):
            # Seen before (still pending): keep the time it was first found.
            item["publishedAt"], item["publishedPrecision"] = known["publishedAt"], known.get("publishedPrecision")
        if not item.get("publishedAt"):
            if first_run:
                self._remember(item, artist, "baseline")
                return None
            # Not seen in any earlier scan, so it appeared since the last one.
            item["publishedAt"], item["publishedPrecision"] = iso(self.now), "firstSeen"
        if not published_after(item, since):
            self._remember(item, artist, "before")
            return None

        item.setdefault("firstSeenAt", iso(self.now))
        if known:
            item["firstSeenAt"] = known.get("firstSeenAt", item["firstSeenAt"])
            if known.get("pendingSince"):
                item.setdefault("pendingSince", known["pendingSince"])
        self.evaluate(item, artist)
        self.items[item["id"]] = item
        self.log(f"    {item['status']:9} {item['id']}  {item.get('title', '')[:70]}")
        return item

    def recheck_pending(self, artist, since, handled):
        max_days = int(self.settings.get("pendingMaxDays", 7))
        for item in list(self.items.values()):
            if item.get("artistId") != artist["id"] or item.get("status") != "pending" or item["id"] in handled:
                continue
            started = parse_time(item.get("pendingSince")) or self.now
            if self.now - started > timedelta(days=max_days):
                item["status"] = "rejected"
                item["reasons"] = (["Livestreamen blev aldrig afsluttet eller gjort tilgængelig"]
                                   if item.get("liveStatus") in LIVE
                                   else [f"Længden kunne ikke aflæses i {max_days} dage"])
                continue
            try:
                fresh = dict(self.fetch.enrich(item), artistId=artist["id"])
            except SourceError as e:
                self.log(f"    recheck {item['id']}: {e}")
                continue
            fresh["pendingSince"] = item.get("pendingSince")
            self.evaluate(fresh, artist)
            self.items[item["id"]] = fresh

    def dedupe(self, artist, new_ids):
        """New uploads of something already known become duplicates or reuploads."""
        aliases = self._aliases(artist)
        older = [s for s in self.seen.values()
                 if s.get("artistId") == artist["id"] and s.get("reason") in ("before", "baseline")]
        new = sorted((self.items[i] for i in new_ids if self.items[i].get("status") == "accepted"),
                     key=lambda it: it.get("publishedAt") or "")
        for item in new:
            old = next((s for s in older if matching.same_recording(item, s, aliases)), None)
            if old:
                item["status"] = "rejected"
                item["reasons"] = [f"Genupload af et ældre sæt (“{old.get('title', '')[:80]}”)"]
                continue
            primary = next((p for p in self.items.values()
                            if p is not item and p.get("artistId") == artist["id"] and p.get("status") == "accepted"
                            and (p["id"] not in new_ids or (p.get("publishedAt") or "") <= (item.get("publishedAt") or ""))
                            and matching.same_recording(item, p, aliases)), None)
            if primary:
                item["status"] = "duplicate"
                item["duplicateOf"] = primary["id"]
                alt = {"platform": item["platform"], "url": item["url"], "uploader": item.get("uploader", "")}
                alts = primary.setdefault("alternates", [])
                if all(a["url"] != alt["url"] for a in alts):
                    alts.append(alt)

    # -- the whole run --------------------------------------------------------

    def run(self):
        for artist in self.config["artists"]:
            since = parse_time(artist["trackingSince"])
            first_run = artist["id"] not in self.baselined
            self.log(f"{artist['name']}: tracking since {iso(since)}{' (first run: baseline)' if first_run else ''}")
            batch = {}
            for platform, label, job in self._jobs(artist):
                entry = {"artistId": artist["id"], "platform": platform, "label": label, "at": iso(self.now)}
                try:
                    found = job()
                    entry.update(ok=True, found=len(found))
                    for it in found:
                        batch.setdefault(it["id"], it)
                except SourceError as e:
                    entry.update(ok=False, found=0, error=str(e))
                except Exception as e:
                    entry.update(ok=False, found=0, error=f"{type(e).__name__}: {e}")
                self.health.append(entry)
                self.log(f"  {'ok ' if entry['ok'] else 'ERR'} {label}: "
                         f"{entry.get('found')} {entry.get('error', '')}")

            handled = set()
            ordered = sorted(batch.values(), key=lambda it: it.get("publishedAt") or "9999")
            for it in ordered:
                stored = self.consider(it, artist, since, first_run)
                if stored:
                    handled.add(stored["id"])
            self.recheck_pending(artist, since, handled)
            self.dedupe(artist, handled)

            # Only call the baseline done when at least one source answered;
            # otherwise the next run would mistake old uploads for new ones.
            if any(h["ok"] for h in self.health if h["artistId"] == artist["id"]):
                self.baselined.add(artist["id"])
        self.prune()
        self.state["baselined"] = sorted(self.baselined)
        return self.output()

    def prune(self):
        keep = timedelta(days=int(self.settings.get("rejectedKeepDays", 120)))
        for item_id, item in list(self.items.items()):
            if item.get("status") in ("rejected", "duplicate"):
                checked = parse_time(item.get("checkedAt")) or self.now
                if self.now - checked > keep:
                    self._remember(item, {"id": item.get("artistId")}, "expired")
                    del self.items[item_id]
        horizon = self.now - timedelta(days=400)
        for key, entry in list(self.seen.items()):
            if (parse_time(entry.get("at")) or self.now) < horizon:
                del self.seen[key]

    def output(self):
        artists = [{
            "id": a["id"], "name": a["name"], "displayName": a.get("displayName") or a["name"],
            "subtitle": a.get("subtitle") or (a["name"] if a.get("displayName") not in (None, a["name"]) else ""),
            "trackingSince": iso(parse_time(a["trackingSince"])), "links": a.get("links", {}),
        } for a in self.config["artists"]]
        items = []
        for it in self.items.values():
            clean = {k: v for k, v in it.items() if k not in ("description",)}
            items.append(clean)
        items.sort(key=lambda it: (it.get("publishedAt") or "", it["id"]), reverse=True)
        ok_runs = [h for h in self.health if h["ok"]]
        return {
            "version": 1,
            "generatedAt": iso(self.now),
            "scanOk": bool(ok_runs) or not self.health,
            "settings": {
                "minDurationMinutes": self.min_sec // 60,
                "minQualityScore": self.min_score,
                "qualityBase": quality.BASE,
                "scanIntervalHours": self.settings.get("scanIntervalHours", 2),
            },
            "artists": artists,
            "health": self.health,
            "items": items,
        }


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_js(path, data):
    """The data as a script, for index.html opened from disk where fetch() is blocked."""
    js = "window.SET_TRACKER_DATA = " + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + ";\n"
    tmp = Path(path).with_suffix(".js.tmp")
    tmp.write_text(js, encoding="utf-8")
    os.replace(tmp, path)


def write_outputs(data, state, data_path, js_path, state_path):
    write_json(data_path, data)
    write_js(js_path, data)
    write_json(state_path, state)


def step_summary(output):
    """Markdown for the GitHub Actions run page."""
    lines = ["## Sætradar", ""]
    status = {"accepted": "✅ med", "rejected": "✖ fra", "duplicate": "↔ dublet", "pending": "⏳ afventer"}
    if output["items"]:
        lines += ["| Status | Kvalitet | Titel |", "|---|---|---|"]
        for it in output["items"][:25]:
            q = (it.get("quality") or {}).get("score", "–")
            title = it.get("title", "").replace("|", "/")
            lines.append(f"| {status.get(it['status'], it['status'])} | {q} | [{title}]({it['url']}) |")
    else:
        lines.append("Ingen sæt efter startdatoen endnu.")
    lines += ["", "| Kilde | Resultat |", "|---|---|"]
    for h in output["health"]:
        result = f"{h['found']} fund" if h["ok"] else f"fejl: {h.get('error', '')}".replace("|", "/")
        lines.append(f"| {h['label']} | {result} |")
    return "\n".join(lines) + "\n"


def validate_config(config):
    problems = []
    ids = set()
    for a in config.get("artists", []):
        for key in ("id", "name", "trackingSince", "sources"):
            if not a.get(key):
                problems.append(f"artist {a.get('id', '?')}: mangler '{key}'")
        if a.get("id") in ids:
            problems.append(f"artist-id '{a['id']}' bruges to gange")
        ids.add(a.get("id"))
        try:
            parse_time(a.get("trackingSince"))
        except Exception:
            problems.append(f"artist {a.get('id')}: trackingSince er ikke en gyldig dato")
    if not config.get("artists"):
        problems.append("ingen kunstnere i config")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scan YouTube and SoundCloud for new DJ sets.")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="where sets.json and seen.json live")
    parser.add_argument("--no-analysis", action="store_true", help="skip the audio excerpts")
    parser.add_argument("--placeholder", action="store_true",
                        help="write empty data for the configured artists without scanning")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)
    data_path, js_path, state_path = data_dir / "sets.json", data_dir / "sets.js", data_dir / "seen.json"

    config = load_json(args.config, None)
    if config is None:
        print(f"Config not found: {args.config}", file=sys.stderr)
        return 2
    problems = validate_config(config)
    if problems:
        print("Config problems:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    if args.no_analysis:
        config.setdefault("settings", {})["audioAnalysis"] = False

    if args.placeholder:
        output = Scanner(config, {}, {}, fetcher=None, log=lambda *a: None).output()
        output["generatedAt"] = None
        write_json(data_path, output)
        write_js(js_path, output)
        print(f"Wrote placeholder data for {len(output['artists'])} artist(s) to {data_dir}")
        return 0

    state = load_json(state_path, {})
    data = load_json(data_path, {})
    fetcher = Fetcher(youtube_api_key=os.environ.get("YOUTUBE_API_KEY") or None)
    scanner = Scanner(config, state, data, fetcher)
    output = scanner.run()
    write_outputs(output, scanner.state, data_path, js_path, state_path)
    accepted = sum(1 for it in output["items"] if it["status"] == "accepted")
    print(f"Done: {accepted} accepted, {len(output['items'])} stored, "
          f"{sum(1 for h in output['health'] if not h['ok'])} source errors.")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(step_summary(output))
    # A scan where every source failed is worth a red mark in Actions, but the
    # data written above (with the errors in it) is still useful to the page.
    return 0 if output["scanOk"] else 1
