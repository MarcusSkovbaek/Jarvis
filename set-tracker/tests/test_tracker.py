"""Tests for the scanner. No network: a fake Fetcher plays YouTube and SoundCloud.

Run from the set-tracker folder:  python -m unittest discover -s tests -v
"""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# A fixed copy of the config: the real one changes whenever artists are
# edited in the app, and tests must not break (and stop the scans) then.
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "artists.json"

from tracker import matching, pipeline, quality, sources  # noqa: E402
from tracker.sources import SourceError  # noqa: E402

SINCE = "2026-10-04T19:50:00+02:00"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

ARTIST = {
    "id": "yy", "name": "Yousuke Yukimatsu", "displayName": "¥ØU$UK€ ¥UK1MAT$U",
    "trackingSince": SINCE, "aliases": ["yukimatsu", "行松陽介"],
    "sources": {
        "youtube": {"searchQueries": ["Yousuke Yukimatsu"]},
        "soundcloud": {"searchQueries": ["yukimatsu"], "users": ["yousukeyukimatsu"]},
    },
}
CONFIG = {
    "settings": {"minDurationMinutes": 30, "minQualityScore": 60, "audioAnalysis": True},
    "trustedUploaders": ["boiler room", "lot radio", "nts"],
    "artists": [ARTIST],
}

GOOD = {"rmsDb": -14.0, "sideDb": -12.0, "clipFraction": 0.0, "silenceFraction": 0.0,
        "cutoffHz": 19000, "bassDb": 8.0}
PHONE = {"rmsDb": -16.0, "sideDb": -60.0, "clipFraction": 0.01, "silenceFraction": 0.0,
         "cutoffHz": 7500, "bassDb": -25.0}


def yt(vid, title, minutes, published="2026-10-06T18:00:00Z", uploader="Some Channel", precision=None, **kw):
    return sources._item("youtube", vid, url=f"https://www.youtube.com/watch?v={vid}", title=title,
                         uploader=uploader, durationSec=int(minutes * 60) if minutes else None,
                         publishedAt=published, publishedPrecision=precision or ("datetime" if published else None), **kw)


def sc(tid, title, minutes, published="2026-10-06T18:00:00Z", uploader="someone", uploader_url=None, **kw):
    return sources._item("soundcloud", tid, url=f"https://soundcloud.com/x/{tid}", title=title,
                         uploader=uploader, uploaderUrl=uploader_url or f"https://soundcloud.com/{uploader}",
                         durationSec=int(minutes * 60), publishedAt=published, publishedPrecision="datetime",
                         audio={"effectiveKbps": 128, "codec": "mp3", "kbps": 128}, **kw)


class FakeFetcher:
    def __init__(self, yt_results=(), sc_results=(), user_results=(), analysis=None, enrich=None,
                 fail=()):
        self.yt_results, self.sc_results, self.user_results = list(yt_results), list(sc_results), list(user_results)
        self.analysis = analysis or {}
        self.enrich_map = enrich or {}
        self.fail = set(fail)
        self.calls = []

    def youtube_search(self, q, limit, since):
        self.calls.append(("yts", q))
        self.yt_limits = getattr(self, "yt_limits", []) + [limit]
        if "youtube" in self.fail:
            raise SourceError("Sign in to confirm you're not a bot")
        return copy.deepcopy(self.yt_results)

    def youtube_channel(self, ch, limit):
        return []

    def soundcloud_search(self, q, limit, since=None):
        self.calls.append(("scs", q))
        self.limits = getattr(self, "limits", []) + [limit]
        if "soundcloud" in self.fail:
            raise SourceError("HTTP Error 403")
        return copy.deepcopy(self.sc_results)

    def soundcloud_user(self, u, limit):
        if "soundcloud" in self.fail:
            raise SourceError("HTTP Error 403")
        return copy.deepcopy(self.user_results)

    def enrich(self, item):
        self.calls.append(("enrich", item["id"]))
        extra = self.enrich_map.get(item["id"])
        if isinstance(extra, Exception):
            raise extra
        return dict(item, **(extra or {}))

    def analyze(self, item, seconds):
        self.calls.append(("analyze", item["id"]))
        m = self.analysis.get(item["id"], GOOD)
        if isinstance(m, Exception):
            raise m
        return m


def keys(config=CONFIG, platform=None):
    """Baseline keys of every search and profile in a config (optionally one platform)."""
    return [pipeline.job_key(a, pf, kind, v) for a in config["artists"]
            for pf, kind, v in pipeline.job_specs(a) if platform in (None, pf)]


def scan(fetcher, state=None, data=None, now=NOW, config=CONFIG):
    # Default: a scanner that has looked before, so new uploads count as new.
    s = pipeline.Scanner(copy.deepcopy(config), state if state is not None else {"baselined": keys(config)},
                         data or {}, fetcher, now=now, log=lambda *a: None)
    return s, s.run()


def by_id(out):
    return {it["id"]: it for it in out["items"]}


class Matching(unittest.TestCase):
    aliases = ["Yousuke Yukimatsu", "¥ØU$UK€ ¥UK1MAT$U", "yukimatsu", "行松陽介"]

    def test_stylised_and_plain_names_match(self):
        for title in ["¥ØU$UK€ ¥UK1MAT$U | Boiler Room: Tokyo",
                      "Yousuke Yukimatsu @ The Lot Radio 09-10-2025",
                      "YOUSUKE YUKIMATSU b2b someone",
                      "行松陽介 DJ set at Circus Osaka",
                      "Ｙｏｕｓｕｋｅ　Ｙｕｋｉｍａｔｓｕ (fullwidth)",
                      "¥UK1MAT$U all night long"]:
            self.assertTrue(matching.names_artist(title, self.aliases), title)

    def test_other_titles_do_not_match(self):
        for title in ["Yusuke Yamamoto live", "Boiler Room Tokyo: DJ Nobu", "Matsuri mix", ""]:
            self.assertFalse(matching.names_artist(title, self.aliases), title)

    def test_title_rules(self):
        codes = lambda t: {s["code"] for s in matching.title_signals(t)}  # noqa: E731
        self.assertIn("phone", codes("Yukimatsu (iPhone recording) full set"))
        self.assertIn("partial", codes("Yukimatsu snippet from last night"))
        self.assertIn("talk", codes("Yousuke Yukimatsu Interview"))
        self.assertIn("setword", codes("Yousuke Yukimatsu | Boiler Room"))
        self.assertNotIn("phone", codes("Yukimatsu at Phonox"))
        self.assertNotIn("partial", codes("Yukimatsu – Eclipse Festival"))

    def test_titles_that_name_him_but_are_not_his_sets(self):
        codes = lambda t: {s["code"] for s in matching.title_signals(t)}  # noqa: E731
        self.assertIn("support", codes("Dune B2B Selina Eshraghi at The Concourse Project | Opening for Yousuke Yukimatsu"))
        self.assertIn("support", codes("Warm up for ¥ØU$UK€ ¥UK1MAT$U @ Circus"))
        self.assertIn("reaction", codes("REACCION A LA SESSION YOUSUKE YUKIMATSU EN BOILER ROOM"))
        self.assertIn("reaction", codes("Producer reacts to Yousuke Yukimatsu Boiler Room"))
        self.assertNotIn("support", codes("¥ØU$UK€ ¥UK1MAT$U | Boiler Room: Tokyo"))
        self.assertNotIn("reaction", codes("¥ØU$UK€ ¥UK1MAT$U b2b someone (Full Set)"))

    def test_trusted_uploader(self):
        trusted = CONFIG["trustedUploaders"]
        self.assertTrue(matching.is_trusted_uploader("Boiler Room", trusted))
        self.assertTrue(matching.is_trusted_uploader("The Lot Radio", trusted))
        self.assertFalse(matching.is_trusted_uploader("boilerroomfan99", trusted))
        self.assertFalse(matching.is_trusted_uploader("", trusted))

    def test_same_recording(self):
        a = {"title": "¥ØU$UK€ ¥UK1MAT$U | Boiler Room: Tokyo", "durationSec": 3640}
        b = {"title": "Yousuke Yukimatsu Boiler Room Tokyo (reupload)", "durationSec": 3655}
        c = {"title": "Yousuke Yukimatsu at Dekmantel", "durationSec": 3650}
        self.assertTrue(matching.same_recording(a, b, self.aliases))
        self.assertFalse(matching.same_recording(a, c, self.aliases))
        self.assertFalse(matching.same_recording(a, {"title": a["title"], "durationSec": 5400}, self.aliases))

    def test_similar_but_different_sets_are_kept_apart(self):
        tokyo = {"title": "¥ØU$UK€ ¥UK1MAT$U | Boiler Room: Tokyo", "durationSec": 3640}
        osaka = {"title": "¥ØU$UK€ ¥UK1MAT$U | Boiler Room: Osaka", "durationSec": 3610}
        self.assertFalse(matching.same_recording(tokyo, osaka, self.aliases))
        year1 = {"title": "Yousuke Yukimatsu – Rainbow Disco Club 2025", "durationSec": 5400}
        year2 = {"title": "Yousuke Yukimatsu – Rainbow Disco Club 2026", "durationSec": 5420}
        self.assertFalse(matching.same_recording(year1, year2, self.aliases))

    def test_radio_episodes_of_the_same_length_are_different_sets(self):
        ep1 = {"title": "NTS - Body Motion w/ Yousuke Yukimatsu (091118)", "durationSec": 3600}
        ep2 = {"title": "NTS - Body Motion w Yousuke Yukimatsu & Bossman Wines 260419", "durationSec": 3600}
        self.assertFalse(matching.same_recording(ep1, ep2, self.aliases))
        bare = {"title": "Yousuke Yukimatsu", "durationSec": 7200}
        self.assertFalse(matching.same_recording(bare, {"title": "Yukimatsu @ Lot Radio", "durationSec": 7200},
                                                 self.aliases))
        # A real reupload keeps the title, slot length or not.
        self.assertTrue(matching.same_recording(ep1, {"title": "Yousuke Yukimatsu - NTS Body Motion 091118",
                                                      "durationSec": 3601}, self.aliases))
        # Off a radio slot, the same length to the second plus a bare title is enough.
        self.assertTrue(matching.same_recording({"title": "Yousuke Yukimatsu", "durationSec": 3734},
                                                {"title": "¥ØU$UK€ ¥UK1MAT$U | Boiler Room: Osaka", "durationSec": 3735},
                                                self.aliases))

    def test_artist_named_only_after_feat_in_a_mix(self):
        summit = ["JOHN SUMMIT", "John Summit"]
        mix = ("Tech House & Drum & Bass Mix | FULL CIRCLE - 3 HOUR MIX feat JOHN SUMMIT x SUB FOCUS x "
               "CHRIS LAKE x MAU P x CULTURE SHOCK x DIMENSION")
        self.assertTrue(matching.featured_only(mix, summit))
        self.assertTrue(matching.featured_only(mix, ["Sub Focus"]))
        self.assertTrue(matching.featured_only("Best of 2026 mix ft. John Summit", summit))
        for title in ["John Summit feat. Hayla - Live at Coachella 2026",          # the artist comes first
                      "Defected Croatia 2026 feat. John Summit (Full Set)",       # one act at an event
                      "Boiler Room x Defected ft. John Summit & Hayla",
                      "Armin van Buuren F2F John Summit @ A State of Trance 2026"]:
            self.assertFalse(matching.featured_only(title, summit), title)
        self.assertEqual([s["code"] for s in matching.credit_signals(mix, summit)], ["featured"])

    def test_mentions_whole_words_only(self):
        self.assertTrue(matching.mentions("Tech House & Drum&Bass Mix", "drum and bass"))
        self.assertTrue(matching.mentions("WORSHIP (Sub Focus, Dimension) live", "sub focus"))
        self.assertTrue(matching.mentions("DJ Remmy | Praise & Worship TikTok Live", "Praise"))
        self.assertFalse(matching.mentions("Praiseworthy closing set", "praise"))
        self.assertFalse(matching.mentions("Live 19912", "1991"))
        self.assertFalse(matching.mentions("anything", "and"))

    def test_stylised_name_tokens_collapse(self):
        self.assertEqual(matching.title_tokens("¥ØU$UK€ ¥UK1MAT$U | Boiler Room: Tokyo", self.aliases),
                         {"boiler", "room", "tokyo"})
        self.assertEqual(matching.title_tokens("行松陽介 DJ set @ Circus", self.aliases), {"circus"})


class Quality(unittest.TestCase):
    sr = quality.SAMPLE_RATE

    def _music(self, seconds=30):
        rng = np.random.default_rng(7)
        n = self.sr * seconds

        def pink():
            spec = np.fft.rfft(rng.standard_normal(n))
            f = np.fft.rfftfreq(n, 1 / self.sr)
            f[0] = 1
            x = np.fft.irfft(spec / np.sqrt(f), n)
            return x / np.abs(x).max()
        t = np.arange(int(self.sr * 0.25)) / self.sr
        kick = np.sin(2 * np.pi * 55 * t) * np.exp(-t * 12)
        kicks = np.zeros(n)
        for s in range(0, n - len(kick), self.sr // 2):
            kicks[s:s + len(kick)] += kick
        left, right = pink(), pink()
        return np.stack([0.3 * left + 0.4 * kicks, 0.3 * (0.7 * left + 0.3 * right) + 0.4 * kicks], 1)

    @staticmethod
    def _filter(x, lo=None, hi=None, sr=44100):
        spec = np.fft.rfft(x)
        f = np.fft.rfftfreq(len(x), 1 / sr)
        if hi:
            spec[f > hi] = 0
        if lo:
            spec[f < lo] = 0
        return np.fft.irfft(spec, len(x))

    def test_clean_stereo_mix_scores_high(self):
        m = quality.measure(self._music())
        self.assertGreater(m["cutoffHz"], 18000)
        self.assertGreater(m["bassDb"], 0)
        s = quality.score(quality.analysis_signals(m))
        self.assertGreaterEqual(s, 75)

    def test_phone_recording_scores_low(self):
        music = self._music()
        mono = self._filter(music[:, 0], lo=250, hi=7000)
        clean = mono / np.abs(mono).max()
        m = quality.measure(np.stack([clean, clean], 1))
        self.assertLess(m["cutoffHz"], 8000)
        self.assertLess(m["sideDb"], -40)
        self.assertLess(m["bassDb"], -18)
        self.assertLess(quality.score(quality.analysis_signals(m)), 30)
        # Overdriven: clipping smears energy upward, so the ceiling looks
        # fine, but the clipping, mono and missing bass still sink it.
        hot = np.clip(clean * 1.6, -1, 1)
        m = quality.measure(np.stack([hot, hot], 1))
        self.assertGreater(m["clipFraction"], 0.003)
        self.assertLess(quality.score(quality.analysis_signals(m)), 40)

    def test_lossy_16k_ceiling_is_still_fine(self):
        music = self._music()
        mp3ish = np.stack([self._filter(music[:, i], hi=16000) for i in (0, 1)], 1)
        m = quality.measure(mp3ish)
        self.assertTrue(15500 <= m["cutoffHz"] <= 16500, m["cutoffHz"])
        self.assertGreaterEqual(quality.score(quality.analysis_signals(m)), 70)

    def test_silence_is_flagged(self):
        music = self._music()
        music[: len(music) // 2] = 0
        m = quality.measure(music)
        self.assertGreater(m["silenceFraction"], 0.3)
        self.assertIn("silence", {s["code"] for s in quality.analysis_signals(m)})

    def test_decode_through_ffmpeg(self):
        if not quality.ffmpeg_available():
            self.skipTest("ffmpeg not installed")
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "anoisesrc=d=8:c=pink:r=44100:a=0.3", "-ac", "2", str(wav)], check=True)
            samples = quality.decode(wav)
            self.assertEqual(samples.shape[1], 2)
            self.assertAlmostEqual(len(samples) / quality.SAMPLE_RATE, 8, delta=0.1)
            self.assertIn("cutoffHz", quality.measure(samples))

    def test_hls_window_picks_the_segments_of_an_excerpt(self):
        playlist = "\n".join(["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-TARGETDURATION:10",
                               '#EXT-X-MAP:URI="init.mp4"'] +
                              [f"#EXTINF:10.0,\nseg{i}.m4s" for i in range(30)] + ["#EXT-X-ENDLIST"])
        master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=160000\naudio/index.m3u8\n"
        pages = {"https://cdn.example/a/master.m3u8": master, "https://cdn.example/a/audio/index.m3u8": playlist}
        original = quality._http_text
        quality._http_text = lambda url, headers: pages[url]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path, offset = quality.hls_window("https://cdn.example/a/master.m3u8", {}, 72, 20, tmp)
                text = path.read_text()
                self.assertEqual(offset, 2.0)
                self.assertIn('#EXT-X-MAP:URI="https://cdn.example/a/audio/init.mp4"', text)
                self.assertEqual([l for l in text.splitlines() if l.endswith(".m4s")],
                                 [f"https://cdn.example/a/audio/seg{i}.m4s" for i in (7, 8, 9)])
                with self.assertRaises(ValueError):
                    quality.hls_window("https://cdn.example/a/master.m3u8", {}, 9999, 20, tmp)
        finally:
            quality._http_text = original

    def test_excerpts_from_hls_fmp4_end_to_end(self):
        """SoundCloud serves fMP4 HLS, which plain ffmpeg seeking cannot read."""
        if not quality.ffmpeg_available():
            self.skipTest("ffmpeg not installed")
        import functools
        import http.server
        import os
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=120:c=pink:a=0.3",
                            "-ac", "2", "-c:a", "aac", "-b:a", "128k", "-f", "hls", "-hls_time", "10",
                            "-hls_segment_type", "fmp4", "-hls_playlist_type", "vod", str(Path(tmp) / "a.m3u8")],
                           check=True)
            handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=tmp)
            handler.log_message = lambda *a: None
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            saved = {k: os.environ.get(k) for k in ("no_proxy", "NO_PROXY")}
            os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/a.m3u8"
                samples = quality.decode_stream(url, {}, 60, 15, "m3u8_native")
                self.assertAlmostEqual(len(samples) / quality.SAMPLE_RATE, 15, delta=0.5)
                self.assertGreater(quality.measure(samples)["cutoffHz"], 12000)
            finally:
                server.shutdown()
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_bitrate_by_codec(self):
        eff, codec, raw = quality.effective_bitrate([
            {"acodec": "opus", "abr": 160, "vcodec": "none"},
            {"acodec": "mp4a.40.2", "abr": 129, "vcodec": "none"},
            {"acodec": "none", "vcodec": "avc1", "tbr": 3000},
        ])
        self.assertEqual((eff, codec, raw), (256, "opus", 160))
        self.assertEqual(quality.effective_bitrate([]), (None, None, None))

    def test_score_is_clamped(self):
        self.assertEqual(quality.score([{"impact": -500}]), 0)
        self.assertEqual(quality.score([{"impact": 500}]), 100)


class Sources(unittest.TestCase):
    def test_iso_duration(self):
        self.assertEqual(sources.parse_iso_duration("PT1H2M3S"), 3723)
        self.assertEqual(sources.parse_iso_duration("PT45M"), 2700)
        self.assertEqual(sources.parse_iso_duration("P0D"), None)
        self.assertEqual(sources.parse_iso_duration("garbage"), None)

    def test_api_video_mapping(self):
        v = {"id": "abc", "snippet": {"title": "T", "channelTitle": "Boiler Room", "channelId": "UC1",
                                      "publishedAt": "2026-10-05T10:00:00Z", "liveBroadcastContent": "none",
                                      "thumbnails": {"high": {"url": "h.jpg"}, "maxres": {"url": "m.jpg"}}},
             "contentDetails": {"duration": "PT1H5M"}, "statistics": {"viewCount": "42"}}
        it = sources.from_api_video(v)
        self.assertEqual(it["id"], "yt:abc")
        self.assertEqual(it["durationSec"], 3900)
        self.assertEqual(it["thumbnail"], "m.jpg")
        self.assertEqual(it["viewCount"], 42)
        self.assertIsNone(it["liveStatus"])
        v["snippet"]["liveBroadcastContent"] = "upcoming"
        self.assertEqual(sources.from_api_video(v)["liveStatus"], "is_upcoming")

    def test_ytdlp_soundcloud_flat_mapping(self):
        info = {"ie_key": "Soundcloud", "id": 123, "url": "https://api.soundcloud.com/tracks/123",
                "webpage_url": "https://soundcloud.com/a/b", "title": "Yukimatsu mix", "duration": 3700.4,
                "timestamp": 1791100000, "uploader": "a", "uploader_url": "https://soundcloud.com/a",
                "thumbnails": [{"url": "https://i1.sndcdn.com/artworks-x-large.jpg", "width": 100, "height": 100}]}
        it = sources.from_ytdlp(info)
        self.assertEqual(it["id"], "sc:123")
        self.assertEqual(it["url"], "https://soundcloud.com/a/b")
        self.assertEqual(it["platform"], "soundcloud")
        self.assertEqual(it["durationSec"], 3700)
        self.assertEqual(it["publishedPrecision"], "datetime")
        self.assertTrue(it["thumbnail"].endswith("-t500x500.jpg"))

    def test_flat_youtube_dates_are_approximate(self):
        it = sources.from_ytdlp({"_type": "url", "ie_key": "Youtube", "id": "abcdefghijk", "title": "x",
                                 "url": "https://www.youtube.com/watch?v=abcdefghijk", "timestamp": 1791000000})
        self.assertEqual(it["publishedPrecision"], "approx")
        full = sources.from_ytdlp({"extractor_key": "Youtube", "id": "abcdefghijk", "title": "x", "timestamp": 1791000000,
                                   "webpage_url": "https://www.youtube.com/watch?v=abcdefghijk"})
        self.assertEqual(full["publishedPrecision"], "datetime")

    def test_only_videos_from_youtube_listings(self):
        keep = {"ie_key": "Youtube", "id": "YcBVJ6A5Zpg"}
        drop = [{"ie_key": "YoutubeTab", "id": "UCxsM4c_lbbqtqBfdCHnuZhQ"},
                {"ie_key": "YoutubeTab", "id": "PLgZPLMzFruKw1CZQDZrDPVRBiRtaqzvGE"},
                {"ie_key": "Youtube", "id": "RDT1tcUfUhR5U"}]
        self.assertTrue(sources._is_video(keep))
        self.assertFalse(any(sources._is_video(e) for e in drop))

    def test_youtube_api_pages_until_the_limit(self):
        pages = [{"items": [{"id": {"videoId": f"v{i:010d}"}} for i in range(50)], "nextPageToken": "p2"},
                 {"items": [{"id": {"videoId": f"w{i:010d}"}} for i in range(50)], "nextPageToken": "p3"},
                 {"items": [{"id": {"videoId": "x0000000000"}}]}]
        calls = []

        def fake_get(endpoint, params, key):
            calls.append((endpoint, dict(params)))
            return pages[len([c for c in calls if c[0] == "search"]) - 1]
        original = (sources._api_get, sources.youtube_videos_api)
        sources._api_get = fake_get
        sources.youtube_videos_api = lambda ids, key: ids
        try:
            ids = sources.youtube_search_api("q", 120, "2026-09-01T00:00:00Z", "k")
        finally:
            sources._api_get, sources.youtube_videos_api = original
        self.assertEqual(len(ids), 101)
        self.assertEqual([c[1].get("pageToken") for c in calls], [None, "p2", "p3"])
        self.assertEqual([c[1]["maxResults"] for c in calls], [50, 50, 20])
        self.assertTrue(all(c[1]["publishedAfter"] == "2026-09-01T00:00:00Z" for c in calls))

    def test_soundcloud_date_filter_covers_the_start(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(sources._soundcloud_window(None), "last_month")
        self.assertEqual(sources._soundcloud_window(pipeline.iso(now - timedelta(days=3))), "last_month")
        self.assertEqual(sources._soundcloud_window(pipeline.iso(now - timedelta(days=90))), "last_year")
        self.assertIsNone(sources._soundcloud_window(pipeline.iso(now - timedelta(days=500))))

    def test_search_url_sorts_by_date_and_keeps_videos(self):
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(sources.youtube_search_url("行松陽介")).query)
        self.assertEqual(q, {"search_query": ["行松陽介"], "sp": ["CAISAhAB"]})

    def test_ytdlp_youtube_date_only(self):
        it = sources.from_ytdlp({"ie_key": "Youtube", "id": "v1", "url": "https://www.youtube.com/watch?v=v1",
                                 "title": "x", "upload_date": "20261005"})
        self.assertEqual((it["publishedAt"], it["publishedPrecision"]), ("2026-10-05", "date"))
        self.assertIn("i.ytimg.com/vi/v1/", it["thumbnail"])


class Pipeline(unittest.TestCase):
    def test_accepts_long_new_good_set_with_link(self):
        f = FakeFetcher(yt_results=[yt("a", "¥ØU$UK€ ¥UK1MAT$U | Boiler Room: Osaka", 62, uploader="Boiler Room")])
        _, out = scan(f)
        item = by_id(out)["yt:a"]
        self.assertEqual(item["status"], "accepted")
        self.assertEqual(item["url"], "https://www.youtube.com/watch?v=a")
        self.assertGreaterEqual(item["quality"]["score"], 85)
        self.assertTrue(item["quality"]["verified"])

    def test_rejects_old_uploads_quietly(self):
        f = FakeFetcher(yt_results=[yt("old", "Yousuke Yukimatsu DJ set", 90, published="2026-09-01T10:00:00Z")])
        s, out = scan(f)
        self.assertNotIn("yt:old", by_id(out))
        self.assertEqual(s.seen["yt:old"]["reason"], "before")

    def test_start_time_is_exclusive_to_the_minute(self):
        before = yt("b", "Yukimatsu set", 60, published="2026-10-04T17:49:00Z")   # 19:49 CEST
        # A different length, so the reupload check does not take it for the first one.
        after = yt("c", "Yukimatsu set two", 75, published="2026-10-04T17:51:00Z")
        _, out = scan(FakeFetcher(yt_results=[before, after]))
        self.assertNotIn("yt:b", by_id(out))
        self.assertEqual(by_id(out)["yt:c"]["status"], "accepted")

    def test_short_items_never_shown(self):
        f = FakeFetcher(sc_results=[sc("1", "Yukimatsu - Track", 6), sc("2", "Yukimatsu mix", 30)])
        s, out = scan(f)
        self.assertEqual(out["items"], [])
        self.assertEqual(s.seen["sc:1"]["reason"], "short")
        self.assertEqual(s.seen["sc:2"]["reason"], "short")   # exactly 30 min is not "longer than"

    def test_length_known_only_after_enrich(self):
        f = FakeFetcher(yt_results=[yt("e", "Yukimatsu live", None)], enrich={"yt:e": {"durationSec": 1500}})
        _, out = scan(f)
        self.assertEqual(by_id(out)["yt:e"]["status"], "rejected")
        self.assertIn("For kort", by_id(out)["yt:e"]["reasons"][0])

    def test_unrelated_titles_ignored(self):
        f = FakeFetcher(yt_results=[yt("u", "DJ Nobu | Boiler Room Tokyo", 70)])
        s, out = scan(f)
        self.assertEqual(out["items"], [])
        self.assertNotIn("yt:u", s.seen)
        self.assertNotIn(("analyze", "yt:u"), f.calls)

    def test_own_account_counts_without_name_in_title(self):
        own = sc("9", "Live at Circus Osaka 2026.10.12", 120, uploader="yousukeyukimatsu")
        _, out = scan(FakeFetcher(user_results=[own]))
        item = by_id(out)["sc:9"]
        self.assertEqual(item["status"], "accepted")
        self.assertIn("own", {s["code"] for s in item["quality"]["signals"]})

    def test_bad_sound_rejected_with_reasons(self):
        f = FakeFetcher(yt_results=[yt("p", "Yukimatsu full set", 75)], analysis={"yt:p": PHONE})
        _, out = scan(f)
        item = by_id(out)["yt:p"]
        self.assertEqual(item["status"], "rejected")
        self.assertTrue(any("Mudret" in r for r in item["reasons"]))

    def test_phone_title_rejected_even_without_analysis(self):
        f = FakeFetcher(yt_results=[yt("q", "Yukimatsu phone recording", 75)],
                        analysis={"yt:q": SourceError("Sign in to confirm you're not a bot")})
        _, out = scan(f)
        self.assertEqual(by_id(out)["yt:q"]["status"], "rejected")

    def test_unmeasured_sound_needs_a_known_or_verified_uploader(self):
        blocked = SourceError("Sign in to confirm you're not a bot")
        f = FakeFetcher(yt_results=[yt("r", "Yousuke Yukimatsu DJ set", 75, uploader="random fan"),
                                    yt("t", "¥ØU$UK€ ¥UK1MAT$U | Boiler Room: Osaka", 75, uploader="Boiler Room"),
                                    yt("v", "Yousuke Yukimatsu - Festival 2026 (Full Set)", 75, uploader="Some Festival",
                                       uploaderVerified=True)],
                        analysis={"yt:r": blocked, "yt:t": blocked, "yt:v": blocked})
        _, out = scan(f)
        items = by_id(out)
        self.assertEqual(items["yt:r"]["status"], "rejected")
        self.assertIn("hverken kendt eller verificeret", items["yt:r"]["reasons"][0])
        for vid in ("yt:t", "yt:v"):
            self.assertEqual(items[vid]["status"], "accepted", vid)
            self.assertFalse(items[vid]["quality"]["verified"])
            self.assertIn("not a bot", items[vid]["quality"]["analysisError"])
        self.assertIn("verified", {s["code"] for s in items["yt:v"]["quality"]["signals"]})

    def test_crowd_words(self):
        codes = lambda t: {s["code"] for s in matching.title_signals(t)}  # noqa: E731
        for title in ["Yukimatsu fancam ultra", "Yukimatsu front row", "POV: Yukimatsu drops gabber"]:
            self.assertIn("crowd", codes(title), title)

    def test_first_run_baselines_undated_results(self):
        f = FakeFetcher(yt_results=[yt("z", "Yukimatsu set", 70, published=None)],
                        enrich={"yt:z": SourceError("bot check")})
        s, out = scan(f, state={})
        self.assertEqual(out["items"], [])
        self.assertEqual(s.seen["yt:z"]["reason"], "baseline")
        self.assertNotIn(("enrich", "yt:z"), f.calls)
        self.assertEqual(sorted(s.state["baselined"]), sorted(keys()))

    def test_after_baseline_undated_new_item_uses_first_seen(self):
        f = FakeFetcher(yt_results=[yt("n", "Yukimatsu set", 70, published=None)],
                        enrich={"yt:n": SourceError("bot check")})
        _, out = scan(f)
        item = by_id(out)["yt:n"]
        self.assertEqual(item["publishedPrecision"], "firstSeen")
        self.assertEqual(item["status"], "accepted")

    def test_baseline_not_marked_done_when_all_sources_fail(self):
        f = FakeFetcher(fail={"youtube", "soundcloud"})
        s, out = scan(f, state={})
        self.assertEqual(s.state["baselined"], [])
        self.assertFalse(out["scanOk"])
        self.assertTrue(all(not h["ok"] and h["error"] for h in out["health"]))

    def test_partial_failure_still_reports(self):
        f = FakeFetcher(sc_results=[sc("5", "Yukimatsu @ NTS", 120, uploader="NTS")], fail={"youtube"})
        _, out = scan(f)
        self.assertTrue(out["scanOk"])
        self.assertEqual(by_id(out)["sc:5"]["status"], "accepted")
        self.assertEqual({h["platform"]: h["ok"] for h in out["health"]}, {"youtube": False, "soundcloud": True})

    def test_livestream_pending_then_accepted(self):
        live = yt("l", "Yukimatsu LIVE now", None, liveStatus="is_live")
        f = FakeFetcher(yt_results=[live])
        s, out = scan(f)
        self.assertEqual(by_id(out)["yt:l"]["status"], "pending")
        # Next scan: the stream has ended, search no longer returns it.
        f2 = FakeFetcher(enrich={"yt:l": {"liveStatus": "was_live", "durationSec": 7200}})
        _, out2 = scan(f2, state=s.state, data=out, now=NOW + timedelta(hours=2))
        self.assertEqual(by_id(out2)["yt:l"]["status"], "accepted")

    def test_pending_expires(self):
        f = FakeFetcher(yt_results=[yt("l", "Yukimatsu LIVE", None, liveStatus="is_upcoming")])
        s, out = scan(f)
        _, out2 = scan(FakeFetcher(), state=s.state, data=out, now=NOW + timedelta(days=8))
        self.assertEqual(by_id(out2)["yt:l"]["status"], "rejected")

    def test_known_items_not_refetched(self):
        f = FakeFetcher(yt_results=[yt("a", "Yukimatsu DJ set", 62)])
        s, out = scan(f)
        f2 = FakeFetcher(yt_results=[yt("a", "Yukimatsu DJ set", 62, viewCount=999)])
        _, out2 = scan(f2, state=s.state, data=out, now=NOW + timedelta(hours=2))
        self.assertNotIn(("analyze", "yt:a"), f2.calls)
        self.assertEqual(by_id(out2)["yt:a"]["viewCount"], 999)
        self.assertEqual(by_id(out2)["yt:a"]["firstSeenAt"], by_id(out)["yt:a"]["firstSeenAt"])

    def test_cross_post_becomes_alternate(self):
        a = yt("a", "¥ØU$UK€ ¥UK1MAT$U | Boiler Room Osaka", 62, uploader="Boiler Room",
               published="2026-10-06T18:00:00Z")
        b = sc("7", "Yousuke Yukimatsu | Boiler Room Osaka", 62.3, uploader="Boiler Room",
               published="2026-10-06T19:00:00Z")
        _, out = scan(FakeFetcher(yt_results=[a], sc_results=[b]))
        items = by_id(out)
        self.assertEqual(items["yt:a"]["status"], "accepted")
        self.assertEqual(items["sc:7"]["status"], "duplicate")
        self.assertEqual(items["yt:a"]["alternates"][0]["platform"], "soundcloud")

    def test_reupload_of_old_set_rejected(self):
        old = yt("o", "¥ØU$UK€ ¥UK1MAT$U | Boiler Room: Tokyo", 60.5, uploader="Boiler Room",
                 published="2025-03-01T10:00:00Z")
        s, out = scan(FakeFetcher(yt_results=[old]))
        re_up = sc("8", "Yousuke Yukimatsu Boiler Room Tokyo", 60.7, uploader="fan123")
        _, out2 = scan(FakeFetcher(sc_results=[re_up]), state=s.state, data=out, now=NOW + timedelta(hours=2))
        item = by_id(out2)["sc:8"]
        self.assertEqual(item["status"], "rejected")
        self.assertIn("Genupload", item["reasons"][0])

    def test_baseline_is_per_platform(self):
        # What happened on the first real run: YouTube failed, SoundCloud worked.
        old_video = yt("v", "¥ØU$UK€ ¥UK1MAT$U - Coachella 2026 (Full Set)", 70, published=None)
        s, out = scan(FakeFetcher(yt_results=[old_video], fail={"youtube"}), state={})
        self.assertEqual(sorted(s.state["baselined"]), sorted(keys(platform="soundcloud")))
        # Next run YouTube answers: its existing videos are baseline, not news.
        f2 = FakeFetcher(yt_results=[old_video], enrich={"yt:v": SourceError("bot check")})
        s2, out2 = scan(f2, state=s.state, data=out, now=NOW + timedelta(hours=2))
        self.assertEqual(out2["items"], [])
        self.assertEqual(s2.seen["yt:v"]["reason"], "baseline")
        self.assertTrue(set(keys(platform="youtube")) <= set(s2.state["baselined"]))

    def test_legacy_baseline_keys_are_redone(self):
        old_video = yt("v", "Yukimatsu live set", 70, published=None)
        for legacy in (["yy"], ["yy:youtube", "yy:soundcloud"]):
            s, out = scan(FakeFetcher(yt_results=[old_video]), state={"baselined": legacy})
            self.assertEqual(out["items"], [], legacy)
            self.assertEqual(s.seen["yt:v"]["reason"], "baseline", legacy)

    def test_a_spelling_added_later_starts_with_its_own_baseline(self):
        class PerQuery(FakeFetcher):
            def youtube_search(self, q, limit, since):
                self.calls.append(("yts", q))
                return copy.deepcopy(self.by_query.get(q, []))
        plain = dict(ARTIST, searchNames=["Yousuke Yukimatsu"], sources={"youtube": {}, "soundcloud": {}})
        cfg1 = dict(copy.deepcopy(CONFIG), artists=[plain])
        f1 = PerQuery()
        f1.by_query = {"Yousuke Yukimatsu": [yt("a", "Yousuke Yukimatsu set", 70, published=None)]}
        s, out = scan(f1, state={}, config=cfg1)
        # The stylised spelling is added to the config. Its first results are
        # old uploads the plain search never listed; a new upload found by the
        # established search is still news.
        styled = dict(plain, searchNames=["Yousuke Yukimatsu", "¥ØU$UK€ ¥UK1MAT$U"])
        cfg2 = dict(copy.deepcopy(CONFIG), artists=[styled])
        f2 = PerQuery(enrich={"yt:old": SourceError("bot"), "yt:new": SourceError("bot")})
        f2.by_query = {"¥ØU$UK€ ¥UK1MAT$U": [yt("old", "¥ØU$UK€ ¥UK1MAT$U | Boiler Room", 70, published=None,
                                               uploader="Boiler Room")],
                       "Yousuke Yukimatsu": [yt("new", "Yousuke Yukimatsu | Boiler Room", 80, published=None,
                                                uploader="Boiler Room")]}
        s2, out2 = scan(f2, state=s.state, data=out, now=NOW + timedelta(hours=2), config=cfg2)
        self.assertEqual(s2.seen["yt:old"]["reason"], "baseline")
        self.assertEqual(by_id(out2)["yt:new"]["status"], "accepted")
        self.assertIn(pipeline.job_key(styled, "youtube", "search", "¥ØU$UK€ ¥UK1MAT$U"), s2.state["baselined"])

    def test_approximate_dates_from_listings(self):
        def approx(vid, title, when):
            return yt(vid, title, 70, published=pipeline.iso(when), precision="approx")
        since = pipeline.parse_time(SINCE)
        blocked = {"yt:a": SourceError("bot"), "yt:b": SourceError("bot"), "yt:o": SourceError("bot")}
        # First look at YouTube (SoundCloud has been seen before).
        first = FakeFetcher(yt_results=[approx("a", "Yukimatsu set", NOW - timedelta(hours=5)),
                                        approx("b", "Yukimatsu other set", since + timedelta(hours=10)),
                                        approx("o", "Yukimatsu old set", NOW - timedelta(days=21))],
                            enrich=blocked)
        s, out = scan(first, state={"baselined": keys(platform="soundcloud")})
        # "5 hours ago" is clearly after the start: judged even on a first look.
        self.assertEqual(by_id(out)["yt:a"]["status"], "accepted")
        self.assertEqual(by_id(out)["yt:a"]["publishedPrecision"], "approx")
        # Too close to the start to tell from a rough date: noted as already there.
        self.assertEqual(s.seen["yt:b"]["reason"], "baseline")
        # Three weeks ago is before the start.
        self.assertEqual(s.seen["yt:o"]["reason"], "before")
        self.assertNotIn(("enrich", "yt:o"), first.calls)
        # After the first look, a new roughly dated upload counts as new.
        later = FakeFetcher(yt_results=[approx("n", "Yukimatsu new set", NOW + timedelta(hours=1))],
                            enrich={"yt:n": SourceError("bot")})
        _, out2 = scan(later, state=s.state, data=out, now=NOW + timedelta(hours=2))
        self.assertEqual(by_id(out2)["yt:n"]["status"], "accepted")

    def test_reupload_of_baseline_set_rejected(self):
        old = yt("o", "Yousuke Yukimatsu | Boiler Room Tokyo", 60.5, published=None)
        s, out = scan(FakeFetcher(yt_results=[old], enrich={"yt:o": SourceError("bot check")}), state={})
        self.assertEqual(s.seen["yt:o"]["reason"], "baseline")
        re_up = sc("8", "Yousuke Yukimatsu Boiler Room Tokyo", 60.5, uploader="fan123")
        _, out2 = scan(FakeFetcher(sc_results=[re_up]), state=s.state, data=out, now=NOW + timedelta(hours=2))
        self.assertEqual(by_id(out2)["sc:8"]["status"], "rejected")

    def test_pending_without_length_is_not_called_a_livestream(self):
        f = FakeFetcher(yt_results=[yt("p", "Yukimatsu set", None, published=None)],
                        enrich={"yt:p": SourceError("bot check")})
        s, out = scan(f)
        item = by_id(out)["yt:p"]
        self.assertEqual(item["status"], "pending")
        self.assertNotIn("Livestream", item["reasons"][0])
        # The next scan keeps the time it was first found.
        f2 = FakeFetcher(yt_results=[yt("p", "Yukimatsu set", None, published=None)],
                         enrich={"yt:p": SourceError("bot check")})
        _, out2 = scan(f2, state=s.state, data=out, now=NOW + timedelta(hours=2))
        self.assertEqual(by_id(out2)["yt:p"]["publishedAt"], item["publishedAt"])

    def test_output_files_and_script_twin(self):
        _, out = scan(FakeFetcher(yt_results=[yt("a", "Yukimatsu </script><b>set", 62)]))
        with tempfile.TemporaryDirectory() as tmp:
            d, js, st = Path(tmp, "sets.json"), Path(tmp, "sets.js"), Path(tmp, "seen.json")
            pipeline.write_outputs(out, {"seen": {}}, d, js, st)
            self.assertEqual(json.loads(d.read_text("utf-8"))["items"][0]["id"], "yt:a")
            text = js.read_text("utf-8")
            self.assertTrue(text.startswith("window.SET_TRACKER_DATA = "))
            self.assertNotIn("</script>", text)
            self.assertNotIn("description", text)

    def test_config_validation(self):
        self.assertEqual(pipeline.validate_config(CONFIG), [])
        bad = {"artists": [{"id": "a"}, {"id": "a", "name": "x", "trackingSince": "nope", "sources": {}}]}
        problems = pipeline.validate_config(bad)
        self.assertTrue(any("startdatoen" in p for p in problems), problems)
        self.assertTrue(any("bruges af en anden" in p for p in problems), problems)
        self.assertTrue(any("mangler et navn" in p for p in problems), problems)

    def test_fixture_config_is_valid(self):
        cfg = json.loads(FIXTURE_CONFIG.read_text("utf-8"))
        self.assertEqual(pipeline.validate_config(cfg), [])

    def test_every_spelling_is_searched_on_every_platform(self):
        artist = dict(ARTIST, searchNames=["Yousuke Yukimatsu", "¥ØU$UK€ ¥UK1MAT$U", "ＹＯＵＳＵＫＥ ＹＵＫＩＭＡＴＳＵ"],
                      sources={"youtube": {"searchQueries": ["Yousuke Yukimatsu DJ set", "yousuke yukimatsu"]},
                               "soundcloud": {"searchQueries": ["yukimatsu"]}})
        # Full-width and lower-case repeats are searched once.
        self.assertEqual(pipeline.search_queries(artist, "youtube"),
                         ["Yousuke Yukimatsu", "¥ØU$UK€ ¥UK1MAT$U", "Yousuke Yukimatsu DJ set"])
        self.assertEqual(pipeline.search_queries(artist, "soundcloud"),
                         ["Yousuke Yukimatsu", "¥ØU$UK€ ¥UK1MAT$U", "yukimatsu"])
        cfg = dict(copy.deepcopy(CONFIG), artists=[artist])
        f = FakeFetcher()
        scan(f, config=cfg)
        for kind in ("yts", "scs"):
            searched = [q for k, q in f.calls if k == kind]
            self.assertIn("Yousuke Yukimatsu", searched, kind)
            self.assertIn("¥ØU$UK€ ¥UK1MAT$U", searched, kind)

    def test_config_searches_plain_and_stylised_names_everywhere(self):
        artist = json.loads(FIXTURE_CONFIG.read_text("utf-8"))["artists"][0]
        for platform in ("youtube", "soundcloud"):
            queries = pipeline.search_queries(artist, platform)
            for name in ("Yousuke Yukimatsu", "¥ØU$UK€ ¥UK1MAT$U", "YØU$UK€ YUK1MAT$U", "行松陽介"):
                self.assertIn(name, queries, platform)

    def test_special_characters_reach_the_searches_unchanged(self):
        from urllib.parse import parse_qs, urlparse
        for name in ("¥ØU$UK€ ¥UK1MAT$U", "YØU$UK€ YUK1MAT$U", "行松陽介"):
            q = parse_qs(urlparse(sources.youtube_search_url(name)).query)
            self.assertEqual(q["search_query"], [name])

    def test_spelling_variants_in_titles_are_recognised(self):
        artist = json.loads(FIXTURE_CONFIG.read_text("utf-8"))["artists"][0]
        scanner = pipeline.Scanner({"artists": [artist]}, {}, {}, FakeFetcher(), now=NOW, log=lambda *a: None)
        aliases = scanner._aliases(artist)
        for title in ["YØU$UK€ YUK1MAT$U live set in Sofia", "Yousuke Yuk1matsu @ Boiler Room",
                      "Yosuke Yukimatsu DJ set", "¥ØU$UK€ ¥UK1MAT$U | HÖR", "行松陽介 DJ"]:
            self.assertTrue(matching.names_artist(title, aliases), title)

    def test_output_lists_the_spellings_for_the_page(self):
        _, out = scan(FakeFetcher(), config=dict(copy.deepcopy(CONFIG),
                                               artists=[dict(ARTIST, searchNames=["Yousuke Yukimatsu", "¥ØU$UK€ ¥UK1MAT$U"])]))
        self.assertEqual(out["artists"][0]["searchNames"], ["Yousuke Yukimatsu", "¥ØU$UK€ ¥UK1MAT$U"])

    def test_youtube_api_failure_falls_back_to_the_search_page(self):
        calls = []
        original = (sources.youtube_search_api, sources.youtube_search_ytdlp)

        def api(*a):
            calls.append("api")
            raise SourceError("YouTube API: quotaExceeded")

        def page(q, limit):
            calls.append("page")
            return [yt("a", "Yukimatsu set", 70)]
        sources.youtube_search_api, sources.youtube_search_ytdlp = api, page
        try:
            found = pipeline.Fetcher(youtube_api_key="k", log=lambda *a: None).youtube_search("q", 10, "x")
        finally:
            sources.youtube_search_api, sources.youtube_search_ytdlp = original
        self.assertEqual(calls, ["api", "page"])
        self.assertEqual(found[0]["id"], "yt:a")

    # -- start date changed in the app ---------------------------------------

    def _artist(self, **kw):
        return dict(copy.deepcopy(ARTIST), **kw)

    def _cfg(self, *artists):
        return dict(copy.deepcopy(CONFIG), artists=list(artists))

    def test_known_artist_without_start_history_is_left_alone(self):
        # State from before start dates were tracked: no deep search, nothing reopened.
        f = FakeFetcher(yt_results=[yt("a", "Yukimatsu DJ set", 62)])
        s, out = scan(f)
        state = copy.deepcopy(s.state)
        state.pop("since")
        f2 = FakeFetcher()
        s2, _ = scan(f2, state=state, data=out, now=NOW + timedelta(hours=2))
        self.assertEqual(set(f2.yt_limits), {40})
        self.assertEqual(s2.state["since"]["yy"], pipeline.iso(pipeline.parse_time(SINCE)))

    def test_moving_the_start_back_reopens_older_uploads(self):
        older = yt("old", "Yousuke Yukimatsu | Boiler Room: Osaka", 62, published="2026-09-20T18:00:00Z",
                   uploader="Boiler Room")
        s, out = scan(FakeFetcher(yt_results=[older]))
        self.assertEqual(s.seen["yt:old"]["reason"], "before")
        self.assertEqual(s.seen["yt:old"]["precision"], "datetime")
        # In the app the start is moved to 1 September.
        cfg = self._cfg(self._artist(trackingSince="2026-09-01T00:00:00Z"))
        f2 = FakeFetcher(yt_results=[older])
        s2, out2 = scan(f2, state=s.state, data=out, now=NOW + timedelta(hours=2), config=cfg)
        self.assertEqual(by_id(out2)["yt:old"]["status"], "accepted")
        self.assertNotIn("yt:old", s2.seen)
        self.assertEqual(set(f2.yt_limits), {150})          # the search reached further back
        self.assertEqual(out2["artists"][0]["trackingSince"], "2026-09-01T00:00:00Z")
        # The run after that searches as usual again.
        f3 = FakeFetcher(yt_results=[older])
        scan(f3, state=s2.state, data=out2, now=NOW + timedelta(hours=4), config=cfg)
        self.assertEqual(set(f3.yt_limits), {40})

    def test_moving_the_start_forward_drops_what_is_now_before_it(self):
        early = yt("e", "Yousuke Yukimatsu DJ set", 62, published="2026-10-05T18:00:00Z")
        late = yt("l", "Yukimatsu all night long", 240, published="2026-10-09T18:00:00Z")
        s, out = scan(FakeFetcher(yt_results=[early, late]))
        self.assertEqual({it["id"] for it in out["items"]}, {"yt:e", "yt:l"})
        cfg = self._cfg(self._artist(trackingSince="2026-10-08T00:00:00Z"))
        s2, out2 = scan(FakeFetcher(yt_results=[early, late]), state=s.state, data=out,
                        now=NOW + timedelta(hours=2), config=cfg)
        self.assertEqual({it["id"] for it in out2["items"]}, {"yt:l"})
        self.assertEqual(s2.seen["yt:e"]["reason"], "before")

    def test_new_artist_starting_in_the_past_searches_deep_and_judges_dated_finds(self):
        other = self._artist(id="dj", name="DJ Other", displayName="DJ Other", searchNames=["DJ Other"],
                             aliases=[], trackingSince="2026-09-01T00:00:00Z",
                             sources={"youtube": {}, "soundcloud": {}})
        cfg = self._cfg(copy.deepcopy(ARTIST), other)
        two_weeks = yt("w", "DJ Other – live at Somewhere (full set)", 90,
                       published=pipeline.iso(NOW - timedelta(days=14)), precision="approx", uploader="Boiler Room")
        f = FakeFetcher(yt_results=[two_weeks], enrich={"yt:w": SourceError("bot")},
                        analysis={"yt:w": SourceError("bot")})
        _, out = scan(f, state={"baselined": keys(self._cfg(copy.deepcopy(ARTIST)))}, config=cfg)
        self.assertEqual(by_id(out)["yt:w"]["status"], "accepted")
        self.assertEqual(by_id(out)["yt:w"]["artistId"], "dj")
        self.assertIn(150, f.yt_limits)

    def test_out_of_time_the_oldest_wait_for_the_next_scan(self):
        # On this clock every sound check takes ten minutes; judging stops after fifteen.
        t = [0.0]

        class Slow(FakeFetcher):
            def analyze(self, item, seconds):
                t[0] += 600
                return super().analyze(item, seconds)

        sets = [yt(f"s{i}", f"Yousuke Yukimatsu DJ set {i}", 62, published=f"2026-10-0{5 + i}T18:00:00Z")
                for i in range(4)]

        def run(state, data, now):
            t[0] = 0.0
            f = Slow(yt_results=sets)
            s = pipeline.Scanner(copy.deepcopy(CONFIG), state, data, f, now=now, log=lambda *a: None,
                                 clock=lambda: t[0])
            return s, s.run(), f

        s, out, _ = run({"baselined": keys()}, {}, NOW)
        # The newest two are judged; the two oldest are neither shown nor remembered.
        self.assertEqual(sorted(by_id(out)), ["yt:s2", "yt:s3"])
        self.assertEqual(out["artists"][0]["backlog"], 2)
        self.assertNotIn("yt:s0", s.seen)
        self.assertEqual(s.state["deepPending"], ["yy"])
        self.assertIn("2 uploads vurderes ved næste scanning", pipeline.step_summary(out))
        # The next scan searches as deep again and judges the rest.
        s2, out2, f2 = run(s.state, out, NOW + timedelta(hours=2))
        self.assertEqual(set(f2.yt_limits), {150})
        self.assertEqual(sorted(by_id(out2)), ["yt:s0", "yt:s1", "yt:s2", "yt:s3"])
        self.assertEqual(out2["artists"][0]["backlog"], 0)
        self.assertEqual(s2.state["deepPending"], [])
        _, _, f3 = run(s2.state, out2, NOW + timedelta(hours=4))
        self.assertEqual(set(f3.yt_limits), {40})

    def test_out_of_time_nothing_more_is_fetched(self):
        waiting = dict(yt("p", "Yousuke Yukimatsu live", None), artistId="yy", status="pending",
                       pendingSince=pipeline.iso(NOW - timedelta(hours=3)))
        f = FakeFetcher(yt_results=[yt("n", "Yousuke Yukimatsu DJ set", 62)])
        clock = iter(range(0, 10 ** 9, 10 ** 4)).__next__     # each look at the clock is hours later
        s = pipeline.Scanner(copy.deepcopy(CONFIG), {"baselined": keys()}, {"items": [waiting]}, f, now=NOW,
                             log=lambda *a: None, clock=clock)
        out = s.run()
        self.assertFalse([c for c in f.calls if c[0] in ("enrich", "analyze")])
        self.assertEqual(by_id(out)["yt:p"]["status"], "pending")
        self.assertNotIn("yt:n", by_id(out))
        self.assertNotIn("yt:n", s.seen)
        self.assertEqual(out["artists"][0]["backlog"], 1)

    def test_removed_artist_is_forgotten(self):
        other = self._artist(id="dj", name="DJ Other", displayName="DJ Other", searchNames=["DJ Other"], aliases=[])
        cfg = self._cfg(copy.deepcopy(ARTIST), other)
        f = FakeFetcher(yt_results=[yt("a", "Yukimatsu DJ set", 62), yt("b", "DJ Other – set", 90),
                                    yt("c", "DJ Other – old", 90, published="2026-01-01T00:00:00Z")])
        s, out = scan(f, state={"baselined": keys(cfg)}, config=cfg)
        self.assertIn("yt:b", by_id(out))
        s2, out2 = scan(FakeFetcher(), state=s.state, data=out, now=NOW + timedelta(hours=2),
                        config=self._cfg(copy.deepcopy(ARTIST)))
        self.assertEqual({it["artistId"] for it in out2["items"]}, {"yy"})
        self.assertFalse(any(v["artistId"] == "dj" for v in s2.seen.values()))
        self.assertFalse(any(k.startswith("dj:") for k in s2.state["baselined"]))
        self.assertNotIn("dj", s2.state["since"])

    def test_a_broken_artist_is_skipped_and_reported(self):
        broken = {"id": "bad", "name": "Broken", "trackingSince": "not a date", "sources": {}}
        ready, problems = pipeline.prepare_config(self._cfg(copy.deepcopy(ARTIST), broken))
        self.assertEqual([a["id"] for a in ready["artists"]], ["yy"])
        self.assertTrue(problems and "Broken" in problems[0])
        f = FakeFetcher(yt_results=[yt("a", "Yukimatsu DJ set", 62)])
        s, out = scan(f, config=ready)
        self.assertEqual(out["problems"], problems)
        self.assertIn("yt:a", by_id(out))
        # The broken artist's earlier results are kept until it is fixed or removed.
        self.assertNotIn("bad", {a["id"] for a in out["artists"]})

    def test_output_lists_profiles(self):
        _, out = scan(FakeFetcher())
        self.assertEqual(out["artists"][0]["profiles"], {"soundcloud": ["yousukeyukimatsu"], "youtube": []})
        self.assertEqual(out["problems"], [])

    def test_every_search_links_to_its_page(self):
        _, out = scan(FakeFetcher())
        urls = {h["label"]: h["url"] for h in out["health"]}
        self.assertEqual(urls["YouTube-søgning “Yousuke Yukimatsu”"],
                         "https://www.youtube.com/results?search_query=Yousuke+Yukimatsu&sp=CAI%253D")
        self.assertEqual(urls["SoundCloud-søgning “yukimatsu”"], "https://soundcloud.com/search/sounds?q=yukimatsu")
        self.assertEqual(urls["SoundCloud-profil yousukeyukimatsu"], "https://soundcloud.com/yousukeyukimatsu/tracks")
        self.assertEqual(pipeline.job_url("soundcloud", "search", "¥ØU$UK€ ¥UK1MAT$U"),
                         "https://soundcloud.com/search/sounds?q=%C2%A5%C3%98U%24UK%E2%82%AC%20%C2%A5UK1MAT%24U")
        self.assertEqual(pipeline.job_url("youtube", "channel", "https://www.youtube.com/@boilerroom"),
                         "https://www.youtube.com/@boilerroom/videos")
        self.assertEqual(pipeline.job_url("youtube", "channel", "@lotradio"), "https://www.youtube.com/@lotradio/videos")
        self.assertEqual(pipeline.job_url("youtube", "channel", "UCGBpxWJr9FNOcFYA5GkKrMg"),
                         "https://www.youtube.com/channel/UCGBpxWJr9FNOcFYA5GkKrMg/videos")
        self.assertEqual(pipeline.job_url("soundcloud", "user", "https://soundcloud.com/djx/"), "https://soundcloud.com/djx/tracks")

    def _worship(self, **kw):
        return dict({"id": "worship", "name": "WORSHIP", "displayName": "WORSHIP", "trackingSince": SINCE,
                     "searchNames": ["WORSHIP"], "aliases": [], "sources": {"youtube": {}, "soundcloud": {}}}, **kw)

    def test_title_filters_for_a_name_that_is_also_a_word(self):
        church = yt("c", "Live Prayer & Worship | UPPERROOM Prayer Room", 120, uploader="UPPERROOM")
        mix = sc("m", "WORSHIP 2026 MIX | CLASSICS & NEW - VOLUME 02", 114, uploader="jkdthedj")
        real = yt("r", "WORSHIP (Sub Focus, Dimension, Culture Shock & 1991) live @ Let It Roll", 90)
        boiler = yt("b", "WORSHIP | Boiler Room London", 75, uploader="Boiler Room")
        f = FakeFetcher(yt_results=[church, real, boiler], sc_results=[mix])
        artist = self._worship(mustMention=["Sub Focus", "Dimension", "drum and bass"], exclude=["prayer", "church"])
        cfg = self._cfg(artist)
        s, out = scan(f, state={"baselined": keys(cfg)}, config=cfg)
        self.assertEqual(sorted(by_id(out)), ["yt:b", "yt:r"])     # Boiler Room counts without the words
        self.assertEqual(s.seen["yt:c"]["reason"], "filtered")
        self.assertEqual(s.seen["sc:m"]["reason"], "filtered")
        self.assertNotIn(("analyze", "yt:c"), f.calls)               # skipped before any work
        self.assertEqual(out["artists"][0]["mustMention"], ["Sub Focus", "Dimension", "drum and bass"])
        self.assertEqual(out["artists"][0]["exclude"], ["prayer", "church"])

    def test_adding_filters_drops_sets_already_shown_and_changing_them_looks_again(self):
        church = sc("c", "Worship and Prayer 3 October 2026", 82, uploader="Let There Be Light")
        mix = sc("m", "WORSHIP 2026 MIX | CLASSICS & NEW - VOLUME 02", 114, uploader="jkdthedj")
        cfg = self._cfg(self._worship())
        s, out = scan(FakeFetcher(sc_results=[church, mix]), state={"baselined": keys(cfg)}, config=cfg)
        self.assertEqual(sorted(by_id(out)), ["sc:c", "sc:m"])       # what WORSHIP found before filters
        # Filters are added in the app: both go, without another search.
        strict = self._cfg(self._worship(mustMention=["drum and bass"], exclude=["prayer"]))
        f2 = FakeFetcher(sc_results=[])
        s2, out2 = scan(f2, state=s.state, data=out, now=NOW + timedelta(hours=2), config=strict)
        self.assertEqual(out2["items"], [])
        self.assertEqual({s2.seen["sc:c"]["reason"], s2.seen["sc:m"]["reason"]}, {"filtered"})
        self.assertEqual(set(f2.limits), {40})
        # Loosened again: what was skipped is looked at again, further back.
        loose = self._cfg(self._worship(exclude=["prayer"]))
        f3 = FakeFetcher(sc_results=[church, mix])
        s3, out3 = scan(f3, state=s2.state, data=out2, now=NOW + timedelta(hours=4), config=loose)
        self.assertEqual(sorted(by_id(out3)), ["sc:m"])
        self.assertEqual(s3.seen["sc:c"]["reason"], "filtered")
        self.assertEqual(set(f3.limits), {150})
        # Unchanged after that: an ordinary scan.
        f4 = FakeFetcher(sc_results=[church, mix])
        scan(f4, state=s3.state, data=out3, now=NOW + timedelta(hours=6), config=loose)
        self.assertEqual(set(f4.limits), {40})

    def test_another_djs_mix_is_ruled_out_by_its_title_without_measuring(self):
        mix = sc("m", "Tech House & DnB Mix | 3 HOUR MIX feat Yousuke Yukimatsu x Sub Focus x Chris Lake", 180,
                 uploader="DUBAYCE")
        f = FakeFetcher(sc_results=[mix])
        _, out = scan(f)
        item = by_id(out)["sc:m"]
        self.assertEqual(item["status"], "rejected")
        self.assertIn("feat.", item["reasons"][0])
        self.assertNotIn(("analyze", "sc:m"), f.calls)
        self.assertFalse(item["quality"]["verified"])

    def test_a_set_accepted_before_a_title_rule_is_judged_again(self):
        mix = sc("m", "3 HOUR MIX feat Yousuke Yukimatsu x Sub Focus x Chris Lake", 180, uploader="DUBAYCE")
        accepted = dict(mix, artistId="yy", status="accepted", firstSeenAt=pipeline.iso(NOW),
                        quality={"score": 83, "signals": []})
        f = FakeFetcher()
        _, out = scan(f, data={"items": [accepted]})
        item = by_id(out)["sc:m"]
        self.assertEqual(item["status"], "rejected")
        self.assertIn("feat.", item["reasons"][0])
        self.assertNotIn(("analyze", "sc:m"), f.calls)
        # A good set is left as it was, without being measured again.
        good = dict(sc("g", "Yousuke Yukimatsu @ Lot Radio", 120, uploader="The Lot Radio"),
                    artistId="yy", status="accepted", quality={"score": 80, "signals": []})
        f2 = FakeFetcher()
        _, out2 = scan(f2, data={"items": [good]})
        self.assertEqual(by_id(out2)["sc:g"]["status"], "accepted")
        self.assertEqual(by_id(out2)["sc:g"]["quality"]["score"], 80)
        self.assertFalse([c for c in f2.calls if c[0] == "analyze"])

    def test_filters_must_be_lists_of_words(self):
        bad = self._worship(mustMention="Sub Focus")
        ready, problems = pipeline.prepare_config(self._cfg(copy.deepcopy(ARTIST), bad))
        self.assertEqual([a["id"] for a in ready["artists"]], ["yy"])
        self.assertIn("Skal også nævne", problems[0])

    def test_second_artist_is_independent(self):
        other = copy.deepcopy(ARTIST)
        other.update(id="other", name="DJ Other", displayName="DJ Other", aliases=["dj other"])
        cfg = copy.deepcopy(CONFIG)
        cfg["artists"].append(other)
        f = FakeFetcher(yt_results=[yt("a", "Yukimatsu DJ set", 62), yt("b", "DJ Other – live", 90)])
        _, out = scan(f, state={"baselined": keys(cfg)}, config=cfg)
        items = by_id(out)
        self.assertEqual(items["yt:a"]["artistId"], "yy")
        self.assertEqual(items["yt:b"]["artistId"], "other")
        self.assertEqual([a["id"] for a in out["artists"]], ["yy", "other"])


if __name__ == "__main__":
    unittest.main()
