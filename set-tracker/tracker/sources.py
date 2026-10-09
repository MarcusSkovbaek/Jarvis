"""Where candidates come from.

Every source returns plain dicts in one shape (see ``_item``), so the rest of
the pipeline never cares which site or library produced them:

* YouTube Data API v3, when ``YOUTUBE_API_KEY`` is set. Exact publish times,
  durations and live state, and it is never asked to prove it is not a bot.
* yt-dlp for YouTube search and channel pages when there is no key.
* yt-dlp for SoundCloud search and user pages (SoundCloud has no public API).

``enrich`` fills in what a search result lacks (exact date, audio formats) and
``excerpts`` downloads two short pieces of audio for the sound analysis.
"""

import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


class SourceError(Exception):
    pass


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _item(platform, vid, **kw):
    prefix = {"youtube": "yt", "soundcloud": "sc"}[platform]
    base = {
        "id": f"{prefix}:{vid}",
        "platform": platform,
        "url": None, "title": "", "uploader": "", "uploaderUrl": None,
        "publishedAt": None, "publishedPrecision": None,
        "durationSec": None, "thumbnail": None, "viewCount": None,
        "liveStatus": None, "audio": None,
        "description": "",
    }
    base.update({k: v for k, v in kw.items() if v is not None})
    return base


def _date_fields(info):
    """publishedAt/precision from a yt-dlp info dict.

    A YouTube search or channel listing only says "3 days ago"; yt-dlp turns
    that into a timestamp, which is marked "approx" so it is never mistaken
    for the exact time a video page gives.
    """
    flat_youtube = info.get("_type") == "url" and (info.get("ie_key") or "").lower().startswith("youtube")
    for key in ("timestamp", "release_timestamp"):
        if info.get(key):
            return _iso(info[key]), "approx" if flat_youtube else "datetime"
    d = info.get("upload_date") or info.get("release_date")
    if d and re.fullmatch(r"\d{8}", d):
        return f"{d[:4]}-{d[4:6]}-{d[6:]}", "date"
    return None, None


def _best_thumbnail(info):
    if info.get("thumbnail"):
        return info["thumbnail"]
    thumbs = [t for t in info.get("thumbnails") or [] if t.get("url")]
    if not thumbs:
        return None
    thumbs.sort(key=lambda t: (t.get("width") or 0) * (t.get("height") or 0) or (t.get("preference") or 0))
    return thumbs[-1]["url"]


def from_ytdlp(info):
    """Map a yt-dlp info dict (flat or full) to our item shape."""
    from .quality import effective_bitrate

    extractor = (info.get("ie_key") or info.get("extractor_key") or info.get("extractor") or "").lower()
    url = info.get("webpage_url") or info.get("url") or ""
    platform = "soundcloud" if ("soundcloud" in extractor or "soundcloud.com" in url) else "youtube"
    vid = str(info.get("id"))
    if platform == "youtube":
        url = f"https://www.youtube.com/watch?v={vid}"
    published, precision = _date_fields(info)
    eff, codec, raw = effective_bitrate(info.get("formats"))
    thumb = _best_thumbnail(info)
    if platform == "youtube" and not thumb:
        thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    if platform == "soundcloud" and thumb:
        thumb = re.sub(r"-(large|t\d+x\d+|crop|small|original)\.(jpg|png)$", r"-t500x500.\2", thumb)
    return _item(
        platform, vid,
        url=url,
        title=info.get("title") or "",
        uploader=info.get("uploader") or info.get("channel") or "",
        uploaderUrl=info.get("uploader_url") or info.get("channel_url"),
        publishedAt=published, publishedPrecision=precision,
        durationSec=int(info["duration"]) if info.get("duration") else None,
        thumbnail=thumb,
        viewCount=info.get("view_count"),
        liveStatus=info.get("live_status"),
        uploaderVerified=True if info.get("channel_is_verified") else None,
        audio={"effectiveKbps": eff, "codec": codec, "kbps": raw} if eff else None,
        description=(info.get("description") or "")[:2000],
    )


# ---------------------------------------------------------------------------
# yt-dlp
# ---------------------------------------------------------------------------

class _Quiet:
    """yt-dlp prints errors even when quiet; they reach us as exceptions anyway."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _ydl_opts(**extra):
    opts = {
        "logger": _Quiet(),
        "quiet": True, "no_warnings": True, "skip_download": True,
        "socket_timeout": 20, "retries": 3, "extractor_retries": 2,
        "noplaylist": True, "ignoreerrors": False,
        "cachedir": str(Path(tempfile.gettempdir()) / "set-tracker-ytdlp"),
        # "3 days ago" in YouTube listings becomes an approximate timestamp.
        "extractor_args": {"youtubetab": {"approximate_date": [""]}},
    }
    cookies = os.environ.get("YTDLP_COOKIES_FILE")
    if cookies and Path(cookies).is_file():
        opts["cookiefile"] = cookies
    opts.update(extra)
    return opts


def _ydl():
    try:
        import yt_dlp
    except ImportError as e:   # pragma: no cover - environment problem
        raise SourceError("yt-dlp er ikke installeret") from e
    return yt_dlp


def _flat_list(url, limit):
    """Entries of a search, channel or profile page, without opening each one."""
    yt_dlp = _ydl()
    try:
        # playlistend stops paging once enough entries are in; search pages never end.
        with yt_dlp.YoutubeDL(_ydl_opts(extract_flat="in_playlist", playlistend=limit)) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise SourceError(_short_error(e)) from e
    return [e for e in (info or {}).get("entries") or [] if e][:limit]


def _short_error(e):
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).strip()
    text = text.replace("ERROR: ", "")
    return text.splitlines()[0][:240] if text else type(e).__name__


def youtube_search_url(query):
    """YouTube's own results page: videos only, newest first (sp=CAISAhAB)."""
    return "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query}) + "&sp=CAISAhAB"


def _is_video(entry):
    """Search pages also list channels, playlists and "Mix" radios; keep videos."""
    kind = (entry.get("ie_key") or "Youtube").lower()
    return kind == "youtube" and re.fullmatch(r"[\w-]{11}", str(entry.get("id") or "")) is not None


def youtube_search_ytdlp(query, limit):
    return [from_ytdlp(e) for e in _flat_list(youtube_search_url(query), limit) if _is_video(e)]


def youtube_channel_ytdlp(channel, limit):
    url = channel if channel.startswith("http") else f"https://www.youtube.com/{channel}"
    url = url.rstrip("/")
    if not re.search(r"/(videos|streams|playlist)", url) and "playlist?list=" not in url:
        url += "/videos"
    return [from_ytdlp(e) for e in _flat_list(url, limit) if _is_video(e)]


def soundcloud_search(query, limit, since_iso=None):
    """Newest uploads first when possible, then SoundCloud's own relevance order.

    SoundCloud ranks search results by popularity, so a set uploaded an hour
    ago can sit below dozens of old favourites. The web site narrows a search
    with ``filter.created_at``; yt-dlp has no switch for it, so this reaches
    into its search extractor and falls back quietly if that ever changes.
    """
    results, seen = [], set()
    window = _soundcloud_window(since_iso)
    if window:
        try:
            results += _soundcloud_recent(query, limit, window)
        except Exception:
            pass
    results += [from_ytdlp(e) for e in _flat_list(f"scsearch{limit}:{query}", limit)]
    out = []
    for it in results:
        if it["id"] not in seen:
            seen.add(it["id"])
            out.append(it)
    return out


def _soundcloud_window(since_iso):
    """SoundCloud's own date filter that still covers the start date (None: no filter)."""
    if not since_iso:
        return "last_month"
    try:
        since = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
    except ValueError:
        return "last_month"
    age = datetime.now(timezone.utc) - (since if since.tzinfo else since.replace(tzinfo=timezone.utc))
    if age.days < 28:
        return "last_month"
    if age.days < 360:
        return "last_year"
    return None


def _soundcloud_recent(query, limit, window="last_month"):
    import itertools

    yt_dlp = _ydl()
    with yt_dlp.YoutubeDL(_ydl_opts(extract_flat="in_playlist")) as ydl:
        ie = ydl.get_info_extractor("SoundcloudSearch")
        ie.initialize()
        entries = ie._get_collection("search/tracks", query, limit=min(limit, 50), q=query,
                                     **{"filter.created_at": window})
        return [from_ytdlp(e) for e in itertools.islice(entries, limit) if e]


def soundcloud_user(user, limit):
    url = user if user.startswith("http") else f"https://soundcloud.com/{user}"
    url = url.rstrip("/")
    if not url.endswith("/tracks"):
        url += "/tracks"
    return [from_ytdlp(e) for e in _flat_list(url, limit)]


def enrich(item):
    """Full metadata for one item (exact date, audio formats, live state)."""
    yt_dlp = _ydl()
    try:
        with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
            info = ydl.extract_info(item["url"], download=False)
    except Exception as e:
        raise SourceError(_short_error(e)) from e
    full = from_ytdlp(info)
    merged = dict(item)
    for key, value in full.items():
        if value not in (None, "", []):
            merged[key] = value
    # The API's exact timestamp beats yt-dlp's date-only fallback.
    if item.get("publishedPrecision") == "datetime" and full.get("publishedPrecision") != "datetime":
        merged["publishedAt"] = item["publishedAt"]
        merged["publishedPrecision"] = "datetime"
    return merged


def audio_stream(item):
    """Address and request headers of the best audio stream of an upload.

    ffmpeg then reads just the excerpts it needs straight from there, which
    works for plain files and for HLS playlists alike (SoundCloud serves
    HLS; a section download through yt-dlp left files ffmpeg could not read).
    """
    yt_dlp = _ydl()
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(format="bestaudio/best")) as ydl:
            info = ydl.extract_info(item["url"], download=False)
    except Exception as e:
        raise SourceError(_short_error(e)) from e
    chosen = info if info.get("url") else next(iter(info.get("requested_formats") or []), {})
    if not chosen.get("url"):
        raise SourceError("ingen lydstrøm fundet")
    return chosen["url"], chosen.get("http_headers") or {}, chosen.get("protocol") or ""


# ---------------------------------------------------------------------------
# YouTube Data API v3
# ---------------------------------------------------------------------------

_API = "https://www.googleapis.com/youtube/v3/"


def _api_get(endpoint, params, key):
    params = dict(params, key=key)
    url = _API + endpoint + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8"))["error"]["message"]
        except Exception:
            msg = str(e)
        raise SourceError(f"YouTube API: {msg}") from e
    except Exception as e:
        raise SourceError(f"YouTube API: {_short_error(e)}") from e


def parse_iso_duration(text):
    """'PT1H2M3S' -> 3723. Returns None for unknown or zero (live) durations."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", text or "")
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    total = d * 86400 + h * 3600 + mi * 60 + s
    return total or None


def from_api_video(v):
    sn = v.get("snippet", {})
    thumbs = sn.get("thumbnails", {})
    thumb = next((thumbs[k]["url"] for k in ("maxres", "standard", "high", "medium", "default") if k in thumbs), None)
    live = sn.get("liveBroadcastContent")
    live_status = {"live": "is_live", "upcoming": "is_upcoming"}.get(live)
    if not live_status and v.get("liveStreamingDetails"):
        live_status = "was_live"
    stats = v.get("statistics", {})
    return _item(
        "youtube", v["id"],
        url=f"https://www.youtube.com/watch?v={v['id']}",
        title=sn.get("title", ""),
        uploader=sn.get("channelTitle", ""),
        uploaderUrl=f"https://www.youtube.com/channel/{sn['channelId']}" if sn.get("channelId") else None,
        publishedAt=sn.get("publishedAt"), publishedPrecision="datetime" if sn.get("publishedAt") else None,
        durationSec=parse_iso_duration(v.get("contentDetails", {}).get("duration")),
        thumbnail=thumb,
        viewCount=int(stats["viewCount"]) if stats.get("viewCount") else None,
        liveStatus=live_status,
        description=(sn.get("description") or "")[:2000],
    )


def youtube_search_api(query, limit, since_iso, key):
    """Newest videos after the start date; pages of 50 until the limit (each page costs 100 units)."""
    ids, token = [], None
    while len(ids) < limit:
        params = {"part": "id", "q": query, "type": "video", "order": "date",
                  "maxResults": min(limit - len(ids), 50), "publishedAfter": since_iso}
        if token:
            params["pageToken"] = token
        data = _api_get("search", params, key)
        ids += [it["id"]["videoId"] for it in data.get("items", []) if it.get("id", {}).get("videoId")]
        token = data.get("nextPageToken")
        if not token:
            break
    return youtube_videos_api(ids[:limit], key)


def youtube_videos_api(ids, key):
    out = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        data = _api_get("videos", {
            "part": "snippet,contentDetails,statistics,liveStreamingDetails",
            "id": ",".join(chunk), "maxResults": 50,
        }, key)
        out += [from_api_video(v) for v in data.get("items", [])]
    return out
