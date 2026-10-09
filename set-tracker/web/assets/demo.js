/* Example data for the #demo view: shows what finds look like before the
   real scanner has found any. Every item is marked as an example, and links
   go to the artist's real profile pages rather than to invented uploads. */
window.SET_TRACKER_DEMO_DATA = function (now) {
  "use strict";
  var H = 3600 * 1000;
  function ago(hours) { return new Date(now.getTime() - hours * H).toISOString().replace(/\.\d+Z$/, "Z"); }
  var yt = "https://www.youtube.com/results?search_query=Yousuke+Yukimatsu&sp=CAI%253D";
  function ys(q) { return "https://www.youtube.com/results?search_query=" + encodeURIComponent(q).replace(/%20/g, "+") + "&sp=CAI%253D"; }
  function ss(q) { return "https://soundcloud.com/search/sounds?q=" + encodeURIComponent(q); }
  var sc = "https://soundcloud.com/yousukeyukimatsu";
  var good = { rmsDb: -13.8, sideDb: -11.2, clipFraction: 0.00002, silenceFraction: 0, cutoffHz: 19750, bassDb: 9.4 };
  function sig(list) { return list.map(function (s) { return { code: s[0], impact: s[1], text: s[2] }; }); }

  return {
    version: 1,
    generatedAt: ago(0.63),
    scanOk: true,
    settings: { minDurationMinutes: 30, minQualityScore: 60, scanIntervalHours: 2, qualityBase: 62 },
    demoHeard: ["demo:lot-radio"],
    artists: [{
      id: "yousuke-yukimatsu",
      name: "Yousuke Yukimatsu",
      displayName: "¥ØU$UK€ ¥UK1MAT$U",
      subtitle: "Yousuke Yukimatsu · 行松陽介",
      searchNames: ["Yousuke Yukimatsu", "¥ØU$UK€ ¥UK1MAT$U", "YØU$UK€ YUK1MAT$U", "Yosuke Yukimatsu", "行松陽介"],
      trackingSince: "2026-10-04T17:50:00Z",
      links: { soundcloud: sc, youtube: yt, residentAdvisor: "https://ra.co/dj/yosukeyukimatsu-jp" }
    }],
    health: [
      { artistId: "yousuke-yukimatsu", platform: "youtube", label: "YouTube-søgning “Yousuke Yukimatsu”", url: ys("Yousuke Yukimatsu"), ok: true, found: 40 },
      { artistId: "yousuke-yukimatsu", platform: "youtube", label: "YouTube-søgning “¥ØU$UK€ ¥UK1MAT$U”", url: ys("¥ØU$UK€ ¥UK1MAT$U"), ok: true, found: 40 },
      { artistId: "yousuke-yukimatsu", platform: "youtube", label: "YouTube-søgning “YØU$UK€ YUK1MAT$U”", url: ys("YØU$UK€ YUK1MAT$U"), ok: true, found: 26 },
      { artistId: "yousuke-yukimatsu", platform: "youtube", label: "YouTube-søgning “行松陽介”", url: ys("行松陽介"), ok: true, found: 40 },
      { artistId: "yousuke-yukimatsu", platform: "soundcloud", label: "SoundCloud-søgning “Yousuke Yukimatsu”", url: ss("Yousuke Yukimatsu"), ok: true, found: 41 },
      { artistId: "yousuke-yukimatsu", platform: "soundcloud", label: "SoundCloud-søgning “¥ØU$UK€ ¥UK1MAT$U”", url: ss("¥ØU$UK€ ¥UK1MAT$U"), ok: true, found: 40 },
      { artistId: "yousuke-yukimatsu", platform: "soundcloud", label: "SoundCloud-søgning “行松陽介”", url: ss("行松陽介"), ok: true, found: 27 }
    ],
    items: [
      {
        id: "demo:osaka", example: true, artistId: "yousuke-yukimatsu", platform: "youtube", status: "accepted",
        url: yt, title: "¥ØU$UK€ ¥UK1MAT$U | Boiler Room: Osaka", uploader: "Boiler Room",
        publishedAt: ago(5.2), publishedPrecision: "datetime", firstSeenAt: ago(4.6), durationSec: 3734,
        alternates: [{ platform: "soundcloud", url: sc, uploader: "Boiler Room" }],
        quality: {
          score: 96, label: "Fremragende", verified: true, analysis: good,
          signals: sig([["trusted", 12, "Uploadet af en kendt platform (Boiler Room)"], ["setword", 3, "Titlen beskriver et DJ-sæt"],
            ["bitrate", 4, "Høj bitrate (160 kbps opus)"], ["bandwidth", 12, "Fuld frekvensgengivelse (op til 19,8 kHz)"],
            ["bass", 3, "Fyldig bas"]])
        }
      },
      {
        id: "demo:lot-radio", example: true, artistId: "yousuke-yukimatsu", platform: "soundcloud", status: "accepted",
        url: sc, title: "Yousuke Yukimatsu @ The Lot Radio", uploader: "The Lot Radio",
        publishedAt: ago(50), publishedPrecision: "datetime", firstSeenAt: ago(49), durationSec: 7231,
        quality: {
          score: 92, label: "Fremragende", verified: true,
          analysis: { rmsDb: -15.1, sideDb: -14.6, clipFraction: 0, silenceFraction: 0.01, cutoffHz: 16250, bassDb: 6.2 },
          signals: sig([["trusted", 12, "Uploadet af en kendt platform (The Lot Radio)"], ["setword", 3, "Titlen beskriver et DJ-sæt"],
            ["bitrate", 0, "Standard bitrate (128 kbps mp3)"], ["bandwidth", 12, "Fuld frekvensgengivelse (op til 16,3 kHz)"],
            ["bass", 3, "Fyldig bas"]])
        }
      },
      {
        id: "demo:zone-unknown", example: true, artistId: "yousuke-yukimatsu", platform: "soundcloud", status: "accepted",
        url: sc, title: "行松陽介 – Zone Unknown, all night long", uploader: "yousukeyukimatsu",
        publishedAt: ago(140), publishedPrecision: "datetime", firstSeenAt: ago(139), durationSec: 11525,
        quality: {
          score: 77, label: "Meget god", verified: false, analysis: null,
          analysisError: "SoundCloud svarede ikke på lydudsnittet",
          signals: sig([["own", 12, "Uploadet af kunstneren selv"], ["bitrate", 3, "God bitrate (160 kbps aac)"]])
        }
      },
      {
        id: "demo:rdc", example: true, artistId: "yousuke-yukimatsu", platform: "youtube", status: "accepted",
        url: yt, title: "Yousuke Yukimatsu – Rainbow Disco Club 2026 (full set)", uploader: "Rainbow Disco Club",
        publishedAt: ago(216), publishedPrecision: "datetime", firstSeenAt: ago(215), durationSec: 5500,
        quality: {
          score: 87, label: "Fremragende", verified: true,
          analysis: { rmsDb: -12.4, sideDb: -16.2, clipFraction: 0.0011, silenceFraction: 0, cutoffHz: 15750, bassDb: 7.1 },
          signals: sig([["trusted", 12, "Uploadet af en kendt platform (Rainbow Disco Club)"], ["setword", 3, "Titlen beskriver et DJ-sæt"],
            ["bitrate", 3, "God bitrate (128 kbps opus)"], ["bandwidth", 12, "Fuld frekvensgengivelse (op til 15,8 kHz)"],
            ["bass", 3, "Fyldig bas"], ["clipping", -8, "Let digital forvrængning"]])
        }
      },
      {
        id: "demo:live", example: true, artistId: "yousuke-yukimatsu", platform: "youtube", status: "pending",
        url: yt, title: "Yousuke Yukimatsu – live fra Zone Unknown", uploader: "Zone Unknown", liveStatus: "is_live",
        publishedAt: ago(0.8), publishedPrecision: "datetime", firstSeenAt: ago(0.7), durationSec: null
      },
      {
        id: "demo:phone", example: true, artistId: "yousuke-yukimatsu", platform: "soundcloud", status: "rejected",
        url: sc, title: "yukimatsu @ circus osaka (crowd recording)", uploader: "user-4821",
        publishedAt: ago(30), publishedPrecision: "datetime", firstSeenAt: ago(29), durationSec: 2860,
        quality: { score: 9, label: "Under niveau", verified: true, signals: [] },
        reasons: ["Lydkvaliteten vurderes for lav (9/100)", "Titlen tyder på en publikumsoptagelse",
          "Svag bas, typisk for telefon- eller rumoptagelser", "Mono-optagelse"]
      },
      {
        id: "demo:reupload", example: true, artistId: "yousuke-yukimatsu", platform: "youtube", status: "rejected",
        url: yt, title: "Yousuke Yukimatsu Boiler Room Tokyo (reupload)", uploader: "mixarchive",
        publishedAt: ago(75), publishedPrecision: "date", firstSeenAt: ago(74), durationSec: 3651,
        quality: { score: 80, label: "Meget god", verified: true, signals: [] },
        reasons: ["Genupload af et ældre sæt (“¥ØU$UK€ ¥UK1MAT$U | Boiler Room: Tokyo”)"]
      },
      {
        id: "demo:excerpt", example: true, artistId: "yousuke-yukimatsu", platform: "youtube", status: "rejected",
        url: yt, title: "Yousuke Yukimatsu live (excerpt)", uploader: "nightshots",
        publishedAt: ago(100), publishedPrecision: "datetime", firstSeenAt: ago(99), durationSec: 2045,
        quality: { score: 38, label: "Under niveau", verified: true, signals: [] },
        reasons: ["Lydkvaliteten vurderes for lav (38/100)", "Titlen tyder på et uddrag, ikke et helt sæt", "Begrænset diskant (stopper ved 11,5 kHz)"]
      }
    ]
  };
};
