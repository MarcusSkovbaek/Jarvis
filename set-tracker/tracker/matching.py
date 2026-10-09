"""Text rules: does a title name the artist, and what do its words say?

Artists stylise their names (``¥ØU$UK€ ¥UK1MAT$U`` is Yousuke Yukimatsu),
uploaders write them in other scripts, and fans add emoji. Matching therefore
works on a "compact" form: NFKC-folded, lower-cased, look-alike symbols mapped
back to letters, accents and everything that is not a letter or digit dropped.
"""

import re
import unicodedata

# Symbols people use in place of letters in stylised names.
_LOOKALIKES = str.maketrans({
    "¥": "y", "ø": "o", "$": "s", "€": "e", "£": "l", "@": "a",
    "1": "i", "0": "o", "3": "e", "4": "a", "5": "s", "7": "t",
    "!": "i", "|": "i", "ß": "ss",
})


def fold(text):
    """NFKC, lower case, accents removed. Keeps spacing and punctuation."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def compact(text):
    """Fold, map look-alikes, then keep only letters and digits."""
    # ø has no decomposition, so map it before fold() drops nothing useful.
    mapped = unicodedata.normalize("NFKC", text or "").lower().translate(_LOOKALIKES)
    mapped = fold(mapped).translate(_LOOKALIKES)
    return "".join(ch for ch in mapped if ch.isalnum())


def words(text):
    """Folded words, for keyword rules and title similarity."""
    return re.findall(r"[^\W_]+", fold(text))


def names_artist(text, aliases):
    """True when ``text`` contains any alias of the artist."""
    haystack = compact(text)
    if not haystack:
        return False
    return any(compact(alias) and compact(alias) in haystack for alias in aliases)


def _has(text, pattern):
    return re.search(pattern, fold(text)) is not None


# (code, pattern, score impact, Danish explanation shown in the page)
TITLE_RULES = [
    ("phone", r"\b(i ?phone|phone|smartphone|mobile ?recording)\b|スマホ|携帯",
     -45, "Titlen tyder på en telefonoptagelse"),
    ("crowd", r"\b(crowd|audience) (recording|footage|cam)\b|\bfrom the (crowd|floor)\b|\bfan ?cam\b|\bfront row\b|\bpov\b",
     -35, "Titlen tyder på en publikumsoptagelse"),
    ("partial", r"\b(snippet|excerpt|teaser|trailer|preview|highlights?|clip)\b",
     -30, "Titlen tyder på et uddrag, ikke et helt sæt"),
    ("part", r"\b(part|pt)\.? ?\d+\b|\(\d ?/ ?\d\)",
     -8, "Titlen tyder på en del af et opdelt sæt"),
    ("talk", r"\b(interview|documentary|talk ?show|panel|lecture|q ?& ?a)\b",
     -45, "Titlen tyder på en samtale, ikke et DJ-sæt"),
    ("support", r"\b(opening|warm ?up|warming up|support(ing)?)\s+(set\s+)?for\b",
     -45, "Titlen tyder på en anden DJ's opvarmning for kunstneren"),
    ("reaction", r"\b(reaction|reacting|reacts?|reaccion|reacción)\b",
     -45, "Titlen tyder på en reaktionsvideo"),
    ("badaudio", r"\b(low|bad|poor) (quality|audio|sound)\b|\b(audio|sound) (issues?|problems?)\b",
     -45, "Titlen nævner dårlig lyd"),
    ("reupload", r"\bre-? ?upload(ed)?\b",
     -20, "Titlen siger, at det er en genupload"),
]

POSITIVE_RULE = (
    "setword",
    r"\b(full set|dj set|dj mix|live set|mix(ed)?|boiler room|radio|podcast|b2b|all night)\b",
    3, "Titlen beskriver et DJ-sæt",
)


def title_signals(title):
    """Score adjustments implied by the words in a title."""
    signals = []
    for code, pattern, impact, text in TITLE_RULES:
        if _has(title, pattern):
            signals.append({"code": code, "impact": impact, "text": text})
    code, pattern, impact, text = POSITIVE_RULE
    if _has(title, pattern):
        signals.append({"code": code, "impact": impact, "text": text})
    return signals


_FEATURE = re.compile(r"(?<![^\W_])(?:feat|ft|featuring)(?![^\W_])\.?")
_MIXWORD = re.compile(r"(?<![^\W_])(?:mix|mixes|mixtape|megamix|mixed|playlist|compilation)(?![^\W_])")
_LISTSEP = re.compile(r"\s(?:x|&|\+|/|vs\.?)\s|,")


def featured_only(title, aliases):
    """True when a title names the artist only after "feat." in a mix or a list.

    "3 HOUR MIX feat JOHN SUMMIT x SUB FOCUS x CHRIS LAKE ..." is another DJ's
    mix with their tracks in it. "John Summit feat. Hayla live" names the artist
    first, and "Defected Croatia feat. John Summit" lists one act at an event:
    both are left alone.
    """
    text = fold(title)
    m = _FEATURE.search(text)
    if not m:
        return False
    before, after = text[:m.start()], text[m.end():]
    if names_artist(before, aliases) or not names_artist(after, aliases):
        return False
    return bool(_MIXWORD.search(before)) or len(_LISTSEP.findall(after)) >= 2


def credit_signals(title, aliases):
    """Score adjustments that depend on how the title credits this artist."""
    if featured_only(title, aliases):
        return [{"code": "featured", "impact": -45,
                 "text": "Titlen nævner kun kunstneren efter “feat.” – et mix af en anden DJ"}]
    return []


def _plain(text):
    # "and" is left out, so "drum and bass" is "drum & bass" is "Drum&Bass".
    return " ".join(w for w in words(text) if w != "and")


def mentions(text, phrase):
    """The words of `phrase` appear together in `text`; case, accents and punctuation ignored.

    "drum and bass" matches "Drum&Bass"; "1991" does not match "19912".
    """
    wanted = _plain(phrase)
    return bool(wanted) and f" {wanted} " in f" {_plain(text)} "


def is_trusted_uploader(uploader, trusted):
    """True when the uploader is one of the configured labels, radios or venues."""
    name = " ".join(words(uploader))
    if not name:
        return False
    return any(re.search(r"\b" + re.escape(" ".join(words(t))) + r"\b", name)
               for t in trusted if words(t))


_FILLER = {"dj", "set", "live", "at", "the", "in", "of", "and", "mix", "x", "b2b", "full", "a", "on",
           "from", "presents", "with", "by", "vs", "feat", "ft"}


def title_tokens(text, aliases=()):
    """Distinctive words of a title, with the artist's own names taken out.

    Words are split on spaces and punctuation but keep symbols such as ¥ and $,
    so a stylised name collapses to the same token as the plain one.
    """
    names = {compact(a) for a in aliases} | {compact(w) for a in aliases for w in a.split()}
    out = set()
    for raw in re.split(r"[\s|:;()\[\]{}<>/\\,.!?\"'“”‘’–—\-_#*+=~]+", fold(text)):
        token = compact(raw)
        if len(token) > 1 and token not in names and token not in _FILLER:
            out.add(token)
    return out


def title_similarity(a, b, aliases=()):
    """Jaccard similarity of the distinctive title words."""
    sa, sb = title_tokens(a, aliases), title_tokens(b, aliases)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _radio_slot(seconds):
    """Within two seconds of a five-minute mark: the length of a radio slot."""
    rest = int(seconds) % 300
    return min(rest, 300 - rest) <= 2


def same_recording(a, b, aliases=()):
    """Heuristic: two uploads of the same set (a reupload or a cross-post).

    The same audio has the same length to within a few seconds, so that plus
    a shared title is enough. A looser length match (a trimmed intro, a
    different encoder) needs nearly the same title: "Boiler Room: Osaka" and
    "Boiler Room: Tokyo" of similar length are two different sets. Radio
    shows run in fixed slots, so two episodes of exactly 60:00 prove nothing
    by their length; they too need nearly the same title.
    """
    da, db = a.get("durationSec"), b.get("durationSec")
    if not da or not db:
        return False
    if abs(da - db) > max(45, 0.015 * max(da, db)):
        return False
    sim = title_similarity(a.get("title", ""), b.get("title", ""), aliases)
    if abs(da - db) <= 3 and not _radio_slot(max(da, db)):
        bare = not title_tokens(a.get("title", ""), aliases) or not title_tokens(b.get("title", ""), aliases)
        return bare or sim >= 0.5
    return sim >= 0.75
