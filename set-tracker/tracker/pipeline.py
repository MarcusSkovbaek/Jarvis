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
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, quote_plus

from . import matching, quality, sources
from .sources import SourceError

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "artists.json"
DATA_DIR = ROOT / "web" / "data"

FINAL = ("accepted", "rejected", "duplicate")
# Title rules about what an upload is, rather than how it sounds.
CONTENT_CODES = {"partial", "talk", "support", "reaction", "featured"}
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


APPROX_SLACK = timedelta(hours=36)


def published_after(item, since):
    """Was this published after the tracking start? None when unknown.

    A date without a time is compared by calendar day, inclusively: an upload
    dated on the start day may well be later than the start time. An
    approximate time ("3 days ago" in a YouTube listing) gets 36 hours of
    slack; anything that old was online before the first scan and is caught
    by the baseline instead.
    """
    if not item.get("publishedAt"):
        return None
    precision = item.get("publishedPrecision")
    if precision == "date":
        return item["publishedAt"][:10] >= since.date().isoformat()
    when = parse_time(item["publishedAt"])
    if precision == "approx":
        return when >= since - APPROX_SLACK
    return when > since


def clearly_after(item, since):
    """Published after the start even at the far end of its uncertainty.

    Used on a search's first answer, where an upload that is only roughly
    dated normally counts as old. With a start date in the past, "2 weeks
    ago" can still be clearly inside the window.
    """
    if not item.get("publishedAt"):
        return False
    precision = item.get("publishedPrecision")
    if precision == "date":
        return item["publishedAt"][:10] > (since + timedelta(days=1)).date().isoformat()
    when = parse_time(item["publishedAt"])
    if precision == "approx":
        return when >= since + APPROX_SLACK
    return when > since


# ---------------------------------------------------------------------------
# Network access, behind one object so tests can replace it
# ---------------------------------------------------------------------------

class Fetcher:
    def __init__(self, youtube_api_key=None, log=print):
        self.key = youtube_api_key
        self.log = log

    def youtube_search(self, query, limit, since_iso):
        if self.key:
            try:
                return sources.youtube_search_api(query, limit, since_iso, self.key)
            except SourceError as e:
                # Typically the daily quota: search the web page instead.
                self.log(f"    YouTube API failed ({e}); using the search page instead")
        return sources.youtube_search_ytdlp(query, limit)

    def youtube_channel(self, channel, limit):
        return sources.youtube_channel_ytdlp(channel, limit)

    def soundcloud_search(self, query, limit, since_iso=None):
        return sources.soundcloud_search(query, limit, since_iso)

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
        url, headers, protocol = sources.audio_stream(item)
        duration = item["durationSec"]
        parts, errors = [], []
        for start in (int(duration * 0.30), int(duration * 0.65)):
            try:
                parts.append(quality.measure(quality.decode_stream(url, headers, start, segment_seconds, protocol)))
            except Exception as e:          # one bad excerpt should not sink the other
                errors.append(str(e))
                self.log(f"    excerpt at {start} s: {e}")
        if not parts:
            raise SourceError("lydudsnittene kunne ikke læses" + (f" ({errors[0]})" if errors else ""))
        return quality.merge_measurements(parts)


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------

def search_queries(artist, platform):
    """Every spelling of the artist's name, then the platform's own extra queries.

    Uploaders write a stylised name both ways (¥ØU$UK€ ¥UK1MAT$U and Yousuke
    Yukimatsu), and the search engines treat ¥, $ and € as text, so each
    spelling finds uploads the others miss. Repeats are dropped, ignoring
    case and full-width forms.
    """
    queries = [*artist.get("searchNames", []),
               *artist.get("sources", {}).get(platform, {}).get("searchQueries", [])]
    out, seen = [], set()
    for q in queries:
        key = unicodedata.normalize("NFKC", q or "").casefold().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(q.strip())
    return out


def job_specs(artist):
    """(platform, kind, value) for each search, channel and profile of an artist."""
    src = artist.get("sources", {})
    specs = [("youtube", "search", q) for q in search_queries(artist, "youtube")]
    specs += [("youtube", "channel", ch) for ch in src.get("youtube", {}).get("channels", [])]
    specs += [("soundcloud", "search", q) for q in search_queries(artist, "soundcloud")]
    specs += [("soundcloud", "user", u) for u in src.get("soundcloud", {}).get("users", [])]
    return specs


def job_key(artist, platform, kind, value):
    return f"{artist['id']}:{platform}:{kind}:{value}"


def job_url(platform, kind, value):
    """The page on the platform's own site that shows what a search or profile finds."""
    if platform == "youtube" and kind == "search":
        return f"https://www.youtube.com/results?search_query={quote_plus(value)}&sp=CAI%253D"   # newest first
    if platform == "soundcloud" and kind == "search":
        return f"https://soundcloud.com/search/sounds?q={quote(value)}"
    if value.startswith("http"):
        base = value
    elif platform == "youtube":
        # "@name", "channel/UC…" or a bare channel id "UC…"
        bare_id = value.startswith("UC") and "/" not in value and len(value) >= 20
        base = "https://www.youtube.com/" + ("channel/" + value if bare_id else value.lstrip("/"))
    else:
        base = "https://soundcloud.com/" + value
    return base.rstrip("/") + ("/videos" if platform == "youtube" else "/tracks")


class Scanner:
    def __init__(self, config, state, data, fetcher, now=None, log=print, clock=time.monotonic):
        self.config = config
        self.settings = config.get("settings", {})
        self.trusted = config.get("trustedUploaders", [])
        self.state = state
        self.seen = state.setdefault("seen", {})
        # One key per search, profile or channel (see job_key) once it has
        # answered. The first answer of a search is a baseline: everything in
        # it was online already. Kept per search, because a search that fails
        # on the first run, or a spelling added to the config later, finds
        # old uploads the others never listed. (Older state kept one key per
        # artist or platform; those are ignored, which costs one more baseline.)
        self.baselined = {b for b in state.get("baselined", []) if b.count(":") >= 3}
        self.items = {it["id"]: it for it in data.get("items", [])}
        # The start date each artist was scanned with last time, to notice when
        # it is changed in the app: earlier means look again further back,
        # later means drop what is now before the start.
        self.since_state = state.setdefault("since", {})
        self.problems = list(config.get("_problems", []))
        self.fetch = fetcher
        self.now = now or datetime.now(timezone.utc)
        self.log = log
        self.health = []
        self.min_sec = int(self.settings.get("minDurationMinutes", 30)) * 60
        self.min_score = int(self.settings.get("minQualityScore", 60))
        self.limit = int(self.settings.get("maxResultsPerQuery", 40))
        self.deep_limit = max(self.limit, int(self.settings.get("deepResultsPerQuery", 150)))
        # Judging an upload (details and a sound check) takes seconds to
        # minutes. A start date moved a year back can turn up far more than one
        # job may take: past GitHub's time limit nothing is saved, and the next
        # scan would try the same again. So judging stops after this long and
        # the rest waits for the next scan, which searches as deep again.
        self.clock = clock
        self.started = clock()
        self.budget = float(self.settings.get("judgeBudgetMinutes", 15)) * 60
        self.deferred = {}
        self.deep_pending = set(state.get("deepPending", []))
        # The title filters (mustMention, exclude) each artist was scanned with.
        self.filter_state = state.setdefault("filters", {})

    # -- helpers ------------------------------------------------------------

    def out_of_time(self):
        return self.clock() - self.started > self.budget

    def _aliases(self, artist):
        names = [artist.get("name"), artist.get("displayName"), *artist.get("searchNames", []),
                 *artist.get("aliases", [])]
        return [a for a in names if a]

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
            "publishedAt": item.get("publishedAt"), "precision": item.get("publishedPrecision"),
            "url": item.get("url"), "uploader": item.get("uploader", "")[:100], "uploaderUrl": item.get("uploaderUrl"),
            "at": iso(self.now),
        }

    def _jobs(self, artist, limit=None):
        """(platform, label, key, url, fetch) for every search, channel and profile."""
        since = iso(parse_time(artist["trackingSince"]))
        n = limit or self.limit
        fetch = {
            ("youtube", "search"): lambda q: self.fetch.youtube_search(q, n, since),
            ("youtube", "channel"): lambda ch: self.fetch.youtube_channel(ch, n),
            ("soundcloud", "search"): lambda q: self.fetch.soundcloud_search(q, n, since),
            ("soundcloud", "user"): lambda u: self.fetch.soundcloud_user(u, n),
        }
        labels = {("youtube", "search"): "YouTube-søgning “{}”", ("youtube", "channel"): "YouTube-kanal {}",
                  ("soundcloud", "search"): "SoundCloud-søgning “{}”", ("soundcloud", "user"): "SoundCloud-profil {}"}
        for platform, kind, value in job_specs(artist):
            yield (platform, labels[(platform, kind)].format(value), job_key(artist, platform, kind, value),
                   job_url(platform, kind, value), lambda f=fetch[(platform, kind)], v=value: f(v))

    def _filters(self, artist):
        def words(key):
            return [w.strip() for w in artist.get(key) or [] if isinstance(w, str) and w.strip()]
        return words("mustMention"), words("exclude")

    def passes_filters(self, artist, item):
        """The artist's own title filters, for names that are also common words.

        `exclude`: a title naming any of these is skipped ("praise", "church" for
        WORSHIP). `mustMention`: the title or uploader must also name one of
        these ("Sub Focus", "drum and bass"), unless the upload is the artist's
        own or comes from a known platform such as Boiler Room.
        """
        must, exclude = self._filters(artist)
        title = item.get("title", "")
        if any(matching.mentions(title, w) for w in exclude):
            return False
        if not must or self._own(artist, item) or matching.is_trusted_uploader(item.get("uploader", ""), self.trusted):
            return True
        text = f"{title} {item.get('uploader', '')}"
        return any(matching.mentions(text, w) for w in must)

    # -- judging ------------------------------------------------------------

    def _metadata(self, item, artist):
        """(own, trusted, signals, content signals) from everything but the sound."""
        own = self._own(artist, item)
        trusted = not own and matching.is_trusted_uploader(item.get("uploader", ""), self.trusted)
        titled = matching.title_signals(item.get("title", ""))
        if not own:
            titled += matching.credit_signals(item.get("title", ""), self._aliases(artist))
        signals = quality.metadata_signals(item, trusted, own, titled)
        content = sorted((s for s in signals if s["code"] in CONTENT_CODES), key=lambda s: s["impact"])
        return own, trusted, signals, content

    def _ruled_out(self, signals, content):
        # Even the best possible sound could not lift it to the minimum score.
        return bool(content) and quality.score(signals) + quality.MAX_ANALYSIS_BONUS < self.min_score

    def recheck_titles(self, artist):
        """Sets accepted before a title rule existed are judged again by it."""
        for item in list(self.items.values()):
            if (item.get("artistId") == artist["id"] and item.get("status") == "accepted"
                    and item.get("durationSec") and self._ruled_out(*self._metadata(item, artist)[2:])):
                self.evaluate(item, artist)
                self.log(f"    {item['status']:9} {item['id']}  {item.get('title', '')[:70]} (title rule)")

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

        own, trusted, signals, content = self._metadata(item, artist)
        if self._ruled_out(signals, content):
            # The title alone rules it out (a talk, a clip, another DJ's mix),
            # whatever the sound: no need to download and measure it.
            score = quality.score(signals)
            item["quality"] = {"score": score, "label": quality.label_for(score), "signals": signals,
                               "analysis": None, "verified": False, "analysisError": None}
            item["status"] = "rejected"
            item["reasons"] = [s["text"] for s in content[:3]]
            return item

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
        vouched = own or trusted or bool(item.get("uploaderVerified"))
        item["quality"] = {
            "score": score,
            "label": quality.label_for(score),
            "signals": signals,
            "analysis": analysis,
            "verified": analysis is not None,
            "analysisError": analysis_error,
        }
        if analysis is None and not vouched:
            # Without a measurement the score rests on words alone; that is
            # only good enough when the uploader can be trusted.
            item["status"] = "rejected"
            item["reasons"] = ["Lyden kunne ikke måles, og uploaderen er hverken kendt eller verificeret"]
        elif score >= self.min_score:
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
        if not self.passes_filters(artist, item):
            self._remember(item, artist, "filtered")
            return None

        # Cheap decisions on search-result data, before any extra requests.
        if published_after(item, since) is False:
            self._remember(item, artist, "before")
            return None
        if item.get("durationSec") and item["durationSec"] <= self.min_sec and item.get("liveStatus") not in LIVE:
            # Tracks, edits and clips: never sets, so not worth showing either.
            self._remember(item, artist, "short")
            return None
        if first_run and item.get("publishedPrecision") != "datetime" and not clearly_after(item, since):
            # A search's first answer: anything it cannot date exactly was
            # online already, unless even a rough date puts it after the start.
            self._remember(item, artist, "baseline")
            return None
        if self.out_of_time():
            # Not remembered, so the next scan finds it and judges it then.
            self.deferred[artist["id"]] = self.deferred.get(artist["id"], 0) + 1
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
            if self.out_of_time():
                continue                    # still pending; looked at again next scan
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

    def apply_start(self, artist, since):
        """Bring stored results in line with the artist's start date.

        Returns True when this run should search further back than usual: the
        start was moved earlier, or a new artist starts in the past.
        """
        aid = artist["id"]
        previous = parse_time(self.since_state.get(aid)) if self.since_state.get(aid) else None
        self.since_state[aid] = iso(since)
        if previous is None:
            known = (any(s.get("artistId") == aid for s in self.seen.values())
                     or any(it.get("artistId") == aid for it in self.items.values()))
            # Known artist from before start dates were tracked: nothing changed.
            return not known and since < self.now - timedelta(days=1)
        if since < previous:
            # Uploads judged too old under the previous start may count now.
            reopened = 0
            for key, entry in list(self.seen.items()):
                if (entry.get("artistId") == aid and entry.get("reason") in ("before", "baseline", "expired")
                        and entry.get("publishedAt")
                        and published_after({"publishedAt": entry["publishedAt"],
                                             "publishedPrecision": entry.get("precision") or "approx"}, since)):
                    del self.seen[key]
                    reopened += 1
            self.log(f"  start moved back to {iso(since)}: {reopened} older uploads will be judged again")
            return True
        if since > previous:
            dropped = 0
            for item_id, item in list(self.items.items()):
                if item.get("artistId") == aid and published_after(item, since) is False:
                    self._remember(item, artist, "before")
                    del self.items[item_id]
                    dropped += 1
            self.log(f"  start moved forward to {iso(since)}: {dropped} sets are now before it")
        return False

    def apply_filters(self, artist):
        """Bring stored results in line with the artist's title filters.

        Sets the filters now rule out are dropped, and uploads they skipped
        before but let through now are looked at again. Returns True when
        there are such uploads, so the searches reach back far enough to find
        them.
        """
        aid = artist["id"]
        must, exclude = self._filters(artist)
        signature = json.dumps([sorted(must), sorted(exclude)], ensure_ascii=False)
        previous = self.filter_state.get(aid)
        self.filter_state[aid] = signature
        if previous == signature:
            return False
        # Uploads the old filters skipped that the new ones let through: look again.
        reopened = 0
        for key, entry in list(self.seen.items()):
            if (entry.get("artistId") == aid and entry.get("reason") == "filtered"
                    and self.passes_filters(artist, entry)):
                del self.seen[key]
                reopened += 1
        dropped = 0
        for item_id, item in list(self.items.items()):
            if item.get("artistId") == aid and not self.passes_filters(artist, item):
                self._remember(item, artist, "filtered")
                del self.items[item_id]
                dropped += 1
        if dropped or reopened:
            self.log(f"  title filters changed: {dropped} sets dropped, {reopened} skipped uploads looked at again")
        return reopened > 0

    def forget_removed_artists(self, known_ids):
        """Results of artists no longer in the config are dropped; re-adding one starts fresh."""
        for item_id, item in list(self.items.items()):
            if item.get("artistId") not in known_ids:
                del self.items[item_id]
        for key, entry in list(self.seen.items()):
            if entry.get("artistId") not in known_ids:
                del self.seen[key]
        self.baselined = {b for b in self.baselined if b.split(":", 1)[0] in known_ids}
        for aid in list(self.since_state):
            if aid not in known_ids:
                del self.since_state[aid]
        for aid in list(self.filter_state):
            if aid not in known_ids:
                del self.filter_state[aid]
        self.deep_pending &= set(known_ids)

    def run(self):
        known_ids = {a.get("id") for a in self.config.get("_allArtists", self.config["artists"])}
        self.forget_removed_artists(known_ids)
        for artist in self.config["artists"]:
            since = parse_time(artist["trackingSince"])
            moved = self.apply_start(artist, since)
            refiltered = self.apply_filters(artist)
            # Also deep when the last scan ran out of time before it was done.
            deep = moved or refiltered or artist["id"] in self.deep_pending
            fresh = [f"{pf} {kind} {value}" for pf, kind, value in job_specs(artist)
                     if job_key(artist, pf, kind, value) not in self.baselined]
            self.log(f"{artist['name']}: tracking since {iso(since)}{' (searching further back)' if deep else ''}"
                     f"{' (first look, taken as baseline: ' + '; '.join(fresh) + ')' if fresh else ''}")
            batch, found_by, answered = {}, {}, []
            for platform, label, key, url, job in self._jobs(artist, self.deep_limit if deep else self.limit):
                entry = {"artistId": artist["id"], "platform": platform, "label": label, "url": url,
                         "at": iso(self.now)}
                try:
                    found = job()
                    entry.update(ok=True, found=len(found))
                    answered.append(key)
                    for it in found:
                        batch.setdefault(it["id"], it)
                        found_by.setdefault(it["id"], set()).add(key)
                except SourceError as e:
                    entry.update(ok=False, found=0, error=str(e))
                except Exception as e:
                    entry.update(ok=False, found=0, error=f"{type(e).__name__}: {e}")
                self.health.append(entry)
                self.log(f"  {'ok ' if entry['ok'] else 'ERR'} {label}: "
                         f"{entry.get('found')} {entry.get('error', '')}")

            handled = set()
            # Newest first: if time runs out, what waits is the oldest.
            ordered = sorted(batch.values(), key=lambda it: it.get("publishedAt") or "", reverse=True)
            for it in ordered:
                # Only searches that have answered before can tell new from old.
                first = not (found_by[it["id"]] & self.baselined)
                stored = self.consider(it, artist, since, first)
                if stored:
                    handled.add(stored["id"])
            self.recheck_pending(artist, since, handled)
            self.recheck_titles(artist)
            self.dedupe(artist, handled)
            self.baselined.update(answered)
            waiting = self.deferred.get(artist["id"], 0)
            if waiting:
                self.deep_pending.add(artist["id"])
                self.log(f"  out of time: {waiting} uploads are judged at the next scan")
            else:
                self.deep_pending.discard(artist["id"])
        self.prune()
        self.state["baselined"] = sorted(self.baselined)
        self.state["since"] = self.since_state
        self.state["filters"] = self.filter_state
        self.state["deepPending"] = sorted(self.deep_pending)
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
            "searchNames": [q for q in search_queries({"searchNames": a.get("searchNames", [])}, "")] or [a["name"]],
            "trackingSince": iso(parse_time(a["trackingSince"])), "links": a.get("links", {}),
            "profiles": {
                "soundcloud": list(a.get("sources", {}).get("soundcloud", {}).get("users", [])),
                "youtube": list(a.get("sources", {}).get("youtube", {}).get("channels", [])),
            },
            # Uploads found but not judged yet, because the scan ran out of time.
            "backlog": self.deferred.get(a["id"], 0),
            "mustMention": self._filters(a)[0],
            "exclude": self._filters(a)[1],
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
            "problems": self.problems,
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


def probe(config, urls):
    """Judge given uploads end to end (metadata, excerpts, score) without touching data.

    A health check for the parts a scan only reaches when something new turns
    up: run it in GitHub Actions on a known set to see that downloads and the
    sound analysis still work from there.
    """
    fetcher = Fetcher(youtube_api_key=os.environ.get("YOUTUBE_API_KEY") or None)
    artist = config["artists"][0]
    scanner = Scanner(config, {}, {}, fetcher)
    lines = ["## Sætradar probe", ""]
    failed = 0
    for url in urls:
        platform = "soundcloud" if "soundcloud.com" in url else "youtube"
        item = sources._item(platform, url, url=url)
        try:
            item = fetcher.enrich(item)
        except SourceError as e:
            failed += 1
            lines += [f"### {url}", f"- metadata: **fejl** – {e}", ""]
            print(f"{url}\n  metadata FAILED: {e}")
            continue
        item["artistId"] = artist["id"]
        scanner.evaluate(item, artist)
        q = item.get("quality") or {}
        verdict = item["status"]
        a = q.get("analysis")
        lines += [f"### [{item.get('title')}]({url})",
                  f"- uploader: {item.get('uploader')} · udgivet {item.get('publishedAt')} ({item.get('publishedPrecision')})"
                  f" · længde {item.get('durationSec')} s",
                  f"- lyd-bitrate: {item.get('audio')}",
                  f"- lydanalyse: {a if a else 'fejl – ' + str(q.get('analysisError'))}",
                  f"- score: **{q.get('score')}** ({q.get('label')}) → {verdict}",
                  *[f"  - {s['impact']:+d} {s['text']}" for s in q.get("signals", [])],
                  ""]
        print(f"{url}\n  {item.get('title')!r} by {item.get('uploader')!r}, {item.get('durationSec')} s, "
              f"published {item.get('publishedAt')}\n  audio {item.get('audio')}\n  analysis {a or q.get('analysisError')}\n"
              f"  score {q.get('score')} -> {verdict}")
        if not a:
            failed += 1
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return 1 if failed else 0


def step_summary(output):
    """Markdown for the GitHub Actions run page."""
    lines = ["## Sætradar", ""]
    for problem in output.get("problems", []):
        lines.append(f"> ⚠ Sprunget over: {problem}")
    if output.get("problems"):
        lines.append("")
    waiting = sum(a.get("backlog", 0) for a in output.get("artists", []))
    if waiting:
        lines += [f"> ⏳ Tiden til at vurdere sæt blev brugt op: {waiting} uploads vurderes ved næste scanning.", ""]
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


def artist_problems(artist, seen_ids=()):
    """What is wrong with one artist entry, in Danish, or [] when it can be scanned."""
    who = artist.get("name") or artist.get("id") or "?"
    problems = []
    aid = artist.get("id")
    if not isinstance(aid, str) or not aid.strip() or ":" in aid:
        problems.append(f"{who}: mangler et gyldigt id")
    elif aid in seen_ids:
        problems.append(f"{who}: id'et '{aid}' bruges af en anden kunstner")
    if not isinstance(artist.get("name"), str) or not artist["name"].strip():
        problems.append(f"{who}: mangler et navn")
    try:
        if parse_time(artist.get("trackingSince")) is None:
            raise ValueError
    except Exception:
        problems.append(f"{who}: startdatoen er ikke en gyldig dato")
    for key, what in (("mustMention", "“Skal også nævne”"), ("exclude", "“Udelad titler med”"), ("searchNames", "stavemåderne")):
        value = artist.get(key)
        if value is not None and not (isinstance(value, list) and all(isinstance(w, str) for w in value)):
            problems.append(f"{who}: {what} skal være en liste af ord")
    sources_ = artist.get("sources")
    if not isinstance(sources_, dict):
        problems.append(f"{who}: mangler 'sources'")
    elif not problems and not job_specs(artist):
        problems.append(f"{who}: ingen stavemåder, søgninger eller profiler at holde øje med")
    return problems


def validate_config(config):
    """Every problem in the config, for tests and for the command line."""
    problems, ids = [], set()
    for a in config.get("artists", []):
        problems += artist_problems(a, ids)
        ids.add(a.get("id"))
    if not config.get("artists"):
        problems.append("ingen kunstnere i config")
    return problems


def prepare_config(config):
    """Split the config into the artists that can be scanned and the problems of the rest.

    The app edits the config, so one bad entry must not stop the others:
    it is skipped, and its problem is shown on the page.
    """
    good, problems, ids = [], [], set()
    for a in config.get("artists", []):
        found = artist_problems(a, ids)
        ids.add(a.get("id"))
        if found:
            problems += found
        else:
            good.append(a)
    ready = dict(config, artists=good, _allArtists=list(config.get("artists", [])), _problems=problems)
    return ready, problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scan YouTube and SoundCloud for new DJ sets.")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="where sets.json and seen.json live")
    parser.add_argument("--no-analysis", action="store_true", help="skip the audio excerpts")
    parser.add_argument("--placeholder", action="store_true",
                        help="write empty data for the configured artists without scanning")
    parser.add_argument("--probe", nargs="+", metavar="URL",
                        help="fetch and judge these uploads and print the verdict; writes nothing")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)
    data_path, js_path, state_path = data_dir / "sets.json", data_dir / "sets.js", data_dir / "seen.json"

    try:
        config = load_json(args.config, None)
    except json.JSONDecodeError as e:
        print(f"Config is not valid JSON: {e}", file=sys.stderr)
        return 2
    if config is None:
        print(f"Config not found: {args.config}", file=sys.stderr)
        return 2
    config, problems = prepare_config(config)
    if problems:
        print("Skipped because of config problems:\n  " + "\n  ".join(problems), file=sys.stderr)
    if not config["artists"] and not problems:
        print("No artists in the config.", file=sys.stderr)
        return 2
    if args.no_analysis:
        config.setdefault("settings", {})["audioAnalysis"] = False

    if args.probe:
        if not config["artists"]:
            print("No artist to judge the links against.", file=sys.stderr)
            return 2
        return probe(config, args.probe)

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
    return 0 if output["scanOk"] and config["artists"] else 1
