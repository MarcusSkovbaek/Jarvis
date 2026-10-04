"""Is a recording pleasant to listen to?

Two kinds of evidence feed one 0-100 score:

* Metadata: who uploaded it, the best audio bitrate on offer, and words in the
  title such as "phone recording" or "snippet".
* The sound itself: two short excerpts are downloaded and measured. A phone or
  crowd recording, or a re-encode of a re-encode, shows up as a low frequency
  ceiling, missing bass, digital clipping, long silences or a very low level.

The score starts at BASE and every finding moves it up or down. Each finding
carries a Danish sentence so the page can show why a set was accepted or not.
"""

import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

BASE = 62
SAMPLE_RATE = 44100

# Bits per second buy different amounts of quality in different codecs.
CODEC_EFFICIENCY = {"opus": 1.6, "aac": 1.3, "mp4a": 1.3, "vorbis": 1.3, "mp3": 1.0}


def effective_bitrate(formats):
    """Best audio bitrate on offer, scaled to mp3-equivalent kbps.

    Returns (effective_kbps, codec, raw_kbps) or (None, None, None).
    """
    best = (None, None, None)
    for f in formats or []:
        acodec = (f.get("acodec") or "").lower()
        if not acodec or acodec == "none":
            continue
        abr = f.get("abr") or (f.get("tbr") if f.get("vcodec") in (None, "none") else None)
        if not abr:
            continue
        family = next((k for k in CODEC_EFFICIENCY if acodec.startswith(k)), None)
        eff = abr * CODEC_EFFICIENCY.get(family, 1.0)
        if best[0] is None or eff > best[0]:
            best = (round(eff), family or acodec, round(abr))
    return best


def label_for(score):
    if score >= 85:
        return "Fremragende"
    if score >= 72:
        return "Meget god"
    if score >= 60:
        return "God"
    return "Under niveau"


# ---------------------------------------------------------------------------
# Measuring the sound
# ---------------------------------------------------------------------------

def decode(path, seconds=None):
    """Decode any audio file to float32 stereo at SAMPLE_RATE with ffmpeg."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path)]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-ac", "2", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True, timeout=180).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)


def _http_text(url, headers):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "replace")


def hls_window(url, headers, start, seconds, folder):
    """A small local playlist with only the HLS segments covering an excerpt.

    ffmpeg cannot seek into HLS with fMP4 segments (SoundCloud's format):
    asked to start at 20 minutes it returns nothing. Given just the right
    segments, it reads them like any short file. Returns (path, offset of
    the excerpt within the first segment).
    """
    text = _http_text(url, headers)
    if "#EXT-X-STREAM-INF" in text:               # a master playlist: take the first variant
        variant = next(l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#"))
        url = urllib.parse.urljoin(url, variant)
        text = _http_text(url, headers)

    def absolute(line):
        return re.sub(r'URI="([^"]+)"', lambda m: f'URI="{urllib.parse.urljoin(url, m.group(1))}"', line)

    header, segments, pending, t = [], [], [], 0.0
    lines = iter(text.splitlines())
    for line in lines:
        line = line.strip()
        if line.startswith(("#EXT-X-MAP", "#EXT-X-KEY")):
            (pending if segments else header).append(absolute(line))
        elif line.startswith("#EXTINF:"):
            duration = float(line[8:].split(",")[0])
            uri = next((l.strip() for l in lines if l.strip() and not l.startswith("#")), None)
            if uri is None:
                break
            segments.append((t, duration, pending + [line], urllib.parse.urljoin(url, uri)))
            pending, t = [], t + duration
    chosen = [sg for sg in segments if sg[0] + sg[1] > start and sg[0] < start + seconds]
    if not chosen:
        raise ValueError("udsnittet ligger uden for afspilningslisten")
    target = max(int(sg[1]) + 1 for sg in chosen)
    out = ["#EXTM3U", "#EXT-X-VERSION:7", f"#EXT-X-TARGETDURATION:{target}", "#EXT-X-PLAYLIST-TYPE:VOD", *header]
    for _, _, tags, uri in chosen:
        out += tags + [uri]
    out.append("#EXT-X-ENDLIST")
    path = Path(folder) / "window.m3u8"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path, start - chosen[0][0]


def _ffmpeg_pcm(args, headers=None):
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if headers:
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    cmd += args + ["-vn", "-ac", "2", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
    run = subprocess.run(cmd, capture_output=True, timeout=240)
    if run.returncode != 0:
        tail = run.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [f"exit {run.returncode}"]
        raise RuntimeError(f"ffmpeg: {tail[0][:200]}")
    return np.frombuffer(run.stdout, dtype=np.float32).reshape(-1, 2)


def decode_stream(url, headers, start, seconds, protocol=""):
    """Decode `seconds` of audio from `start` straight off a stream URL."""
    if "m3u8" in (protocol or "") or ".m3u8" in urllib.parse.urlparse(url).path:
        with tempfile.TemporaryDirectory(prefix="set-tracker-") as tmp:
            playlist, offset = hls_window(url, headers, start, seconds, tmp)
            # The playlist is a local file now; its segment links are signed
            # URLs that need no headers (and -headers would not apply to a file).
            return _ffmpeg_pcm(["-protocol_whitelist", "file,http,https,tcp,tls,crypto",
                                "-i", str(playlist), "-ss", f"{offset:.2f}", "-t", str(int(seconds))])
    samples = _ffmpeg_pcm(["-ss", str(int(start)), "-i", url, "-t", str(int(seconds))], headers)
    if len(samples) < SAMPLE_RATE * seconds * 0.8:
        # Some containers do not seek on input; read up to the excerpt instead.
        samples = _ffmpeg_pcm(["-i", url, "-ss", str(int(start)), "-t", str(int(seconds))], headers)
    return samples


def _db(x):
    return 10 * np.log10(np.maximum(x, 1e-20))


def measure(samples, sr=SAMPLE_RATE):
    """Numbers describing how a stretch of audio sounds. Pure function."""
    samples = np.asarray(samples, dtype=np.float64)
    if samples.ndim == 1:
        samples = np.stack([samples, samples], axis=1)
    if len(samples) < sr * 5:
        raise ValueError("for kort lydudsnit til analyse")
    left, right = samples[:, 0], samples[:, 1]
    mid = (left + right) / 2
    side = (left - right) / 2

    # Level and stereo width.
    rms_db = _db(np.mean(mid ** 2))
    side_db = _db(np.mean(side ** 2)) - rms_db

    # Digital clipping: samples sitting on full scale.
    clip_fraction = float(np.mean(np.abs(samples) >= 0.999))

    # Silence: 100 ms frames far below the programme level.
    frame = sr // 10
    n = len(mid) // frame
    frame_rms = _db(np.mean(mid[: n * frame].reshape(n, frame) ** 2, axis=1))
    silence_fraction = float(np.mean(frame_rms < -60))

    # Average power spectrum (Welch, Hann window, 50 % overlap).
    size = 8192
    window = np.hanning(size)
    hop = size // 2
    starts = range(0, len(mid) - size, hop)
    power = np.zeros(size // 2 + 1)
    count = 0
    for s in starts:
        seg = mid[s:s + size]
        if np.mean(seg ** 2) < 1e-9:      # skip silent frames, they only add noise floor
            continue
        power += np.abs(np.fft.rfft(seg * window)) ** 2
        count += 1
    if count == 0:
        raise ValueError("lydudsnittet er stille")
    power /= count
    freqs = np.fft.rfftfreq(size, 1 / sr)

    def band(lo, hi):
        sel = (freqs >= lo) & (freqs < hi)
        return _db(np.mean(power[sel]))

    reference = band(200, 4000)
    bass_rel = band(40, 120) - reference

    # Frequency ceiling: the highest 250 Hz band still within 65 dB of the
    # midrange. Lossy encoders and phone microphones cut off sharply.
    cutoff = 0.0
    edges = np.arange(1000, min(sr / 2, 22000), 250)
    for lo in edges:
        if band(lo, lo + 250) > reference - 65:
            cutoff = float(lo + 250)

    return {
        "rmsDb": round(float(rms_db), 1),
        "sideDb": round(float(side_db), 1),
        "clipFraction": round(clip_fraction, 6),
        "silenceFraction": round(silence_fraction, 3),
        "cutoffHz": int(cutoff),
        "bassDb": round(float(bass_rel), 1),
    }


def analysis_signals(m):
    """Score findings from measure()'s numbers."""
    out = []

    def add(code, impact, text):
        out.append({"code": code, "impact": impact, "text": text})

    khz = f"{m['cutoffHz'] / 1000:.1f}".replace(".", ",")
    if m["cutoffHz"] >= 15500:
        add("bandwidth", 12, f"Fuld frekvensgengivelse (op til {khz} kHz)")
    elif m["cutoffHz"] >= 13000:
        add("bandwidth", 3, f"Næsten fuld frekvensgengivelse (op til {khz} kHz)")
    elif m["cutoffHz"] >= 10000:
        add("bandwidth", -15, f"Begrænset diskant (stopper ved {khz} kHz)")
    else:
        add("bandwidth", -40, f"Mudret lyd: intet over {khz} kHz")

    if m["bassDb"] < -18:
        add("thin", -15, "Svag bas, typisk for telefon- eller rumoptagelser")
    elif m["bassDb"] >= -6:
        add("bass", 3, "Fyldig bas")

    if m["clipFraction"] > 0.003:
        add("clipping", -25, "Kraftig digital forvrængning (clipping)")
    elif m["clipFraction"] > 0.0005:
        add("clipping", -8, "Let digital forvrængning")

    if m["silenceFraction"] > 0.3:
        add("silence", -30, "Lange stille passager i lydudsnittene")
    elif m["silenceFraction"] > 0.1:
        add("silence", -8, "Stille passager i lydudsnittene")

    if m["rmsDb"] < -38:
        add("quiet", -15, "Meget lav lydstyrke")

    if m["sideDb"] < -40:
        add("mono", -6, "Mono-optagelse")
    return out


def merge_measurements(parts):
    """Combine several excerpts: the worst finding of each kind counts."""
    return {
        "rmsDb": round(float(np.mean([p["rmsDb"] for p in parts])), 1),
        "sideDb": round(float(np.max([p["sideDb"] for p in parts])), 1),
        "clipFraction": max(p["clipFraction"] for p in parts),
        "silenceFraction": round(float(np.mean([p["silenceFraction"] for p in parts])), 3),
        "cutoffHz": max(p["cutoffHz"] for p in parts),
        "bassDb": round(float(np.max([p["bassDb"] for p in parts])), 1),
    }


def ffmpeg_available():
    return shutil.which("ffmpeg") is not None


# ---------------------------------------------------------------------------
# The score
# ---------------------------------------------------------------------------

def metadata_signals(item, trusted, own_account, title_findings):
    out = list(title_findings)
    if own_account:
        out.append({"code": "own", "impact": 12, "text": "Uploadet af kunstneren selv"})
    elif trusted:
        out.append({"code": "trusted", "impact": 12,
                    "text": f"Uploadet af en kendt platform ({item.get('uploader')})"})
    elif item.get("uploaderVerified"):
        out.append({"code": "verified", "impact": 6,
                    "text": f"Uploadet af en verificeret kanal ({item.get('uploader')})"})

    eff = (item.get("audio") or {}).get("effectiveKbps")
    raw = (item.get("audio") or {}).get("kbps")
    codec = (item.get("audio") or {}).get("codec") or ""
    if eff:
        desc = f"{raw} kbps {codec}".strip()
        if eff >= 250:
            out.append({"code": "bitrate", "impact": 4, "text": f"Høj bitrate ({desc})"})
        elif eff >= 160:
            out.append({"code": "bitrate", "impact": 3, "text": f"God bitrate ({desc})"})
        elif eff >= 120:
            out.append({"code": "bitrate", "impact": 0, "text": f"Standard bitrate ({desc})"})
        elif eff >= 80:
            out.append({"code": "bitrate", "impact": -8, "text": f"Lav bitrate ({desc})"})
        else:
            out.append({"code": "bitrate", "impact": -25, "text": f"Meget lav bitrate ({desc})"})
    return out


def score(signals):
    total = BASE + sum(s["impact"] for s in signals)
    return max(0, min(100, int(round(total))))
