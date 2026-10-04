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
        if "youtube" in self.fail:
            raise SourceError("Sign in to confirm you're not a bot")
        return copy.deepcopy(self.yt_results)

    def youtube_channel(self, ch, limit):
        return []

    def soundcloud_search(self, q, limit):
        self.calls.append(("scs", q))
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
        def approx(vid, title, hours_ago):
            return yt(vid, title, 70, published=pipeline.iso(NOW - timedelta(hours=hours_ago)), precision="approx")
        # First look at YouTube: an approximate date is not proof of being new.
        s, out = scan(FakeFetcher(yt_results=[approx("a", "Yukimatsu set", 5)]),
                      state={"baselined": keys(platform="soundcloud")})
        self.assertEqual(s.seen["yt:a"]["reason"], "baseline")
        # After the baseline: three weeks ago is old, two hours ago is new.
        f = FakeFetcher(yt_results=[approx("o", "Yukimatsu old set", 24 * 21), approx("n", "Yukimatsu new set", 2)],
                        enrich={"yt:o": SourceError("bot"), "yt:n": SourceError("bot")})
        s2, out2 = scan(f)
        self.assertEqual(s2.seen["yt:o"]["reason"], "before")
        self.assertNotIn(("enrich", "yt:o"), f.calls)
        self.assertEqual(by_id(out2)["yt:n"]["status"], "accepted")
        self.assertEqual(by_id(out2)["yt:n"]["publishedPrecision"], "approx")

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
        self.assertTrue(any("trackingSince" in p for p in problems))
        self.assertTrue(any("to gange" in p for p in problems))

    def test_shipped_config_is_valid(self):
        cfg = json.loads((ROOT / "config" / "artists.json").read_text("utf-8"))
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

    def test_shipped_config_searches_plain_and_stylised_names_everywhere(self):
        artist = json.loads((ROOT / "config" / "artists.json").read_text("utf-8"))["artists"][0]
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
        artist = json.loads((ROOT / "config" / "artists.json").read_text("utf-8"))["artists"][0]
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
