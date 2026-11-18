"""Outils communs : recherche Radio Browser, sondage des flux, classement qualité.

Dépendances : bibliothèque standard uniquement (urllib), pour tourner partout,
y compris dans GitHub Actions sans `pip install`.
"""
from __future__ import annotations

import json
import re
import socket
import ssl
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone

USER_AGENT = "OndesIDF-catalog/1.0"
RB_SERVERS = ["de1.api.radio-browser.info", "de2.api.radio-browser.info", "fi1.api.radio-browser.info"]
PROBE_TIMEOUT = 10
SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


# --------------------------------------------------------------------------- Radio Browser

def _rb_get(path: str, params: dict | None = None):
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    last_err = None
    for host in RB_SERVERS:
        url = f"https://{host}{path}{query}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception as e:  # serveur suivant
            last_err = e
    raise RuntimeError(f"Radio Browser injoignable : {last_err}")


def rb_search(name: str, limit: int = 30) -> list[dict]:
    res = _rb_get("/json/stations/search", {
        "name": name, "countrycode": "FR", "hidebroken": "true",
        "order": "votes", "reverse": "true", "limit": limit,
    })
    time.sleep(0.3)  # rester poli avec l'API communautaire
    return res


def rb_by_uuid(uuid: str) -> dict | None:
    res = _rb_get("/json/stations/byuuid", {"uuids": uuid})
    return res[0] if res else None


def rb_best_matches(seed: dict) -> list[dict]:
    """Entrées Radio Browser correspondant à la station, les meilleures d'abord."""
    query = seed.get("q") or seed["name"]
    results = rb_search(query)
    # correspondance STRICTE : on refuse les déclinaisons thématiques (« Nova la nuit »,
    # « Urban Hit Black »…) et les homonymes d'autres villes (« Radio Shalom Besançon »).
    allowed = set()
    for base in {normalize(query), normalize(seed["name"]), *map(normalize, seed.get("rbNames", []))}:
        allowed |= {base, base + "fm", base + "radio", "radio" + base, base + "paris",
                    base + "france", base + "officiel", base + "iledefrance", base + "idf"}
    scored = []
    for s in results:
        n = normalize(s.get("name", ""))
        if n in allowed:
            scored.append((-int(s.get("votes", 0)), s))
    scored.sort(key=lambda t: t[0])
    return [t[1] for t in scored]


# --------------------------------------------------------------------------- Sondage des flux

_ctx = ssl.create_default_context()


def probe(url: str) -> dict:
    """Ouvre le flux, lit les en-têtes (et le master HLS), renvoie les infos techniques."""
    info = {"ok": False}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Icy-MetaData": "1"})
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT, context=_ctx) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            info["finalUrl"] = r.geturl()
            info["contentType"] = ctype
            if "mpegurl" in ctype or url.split("?")[0].endswith(".m3u8"):
                body = r.read(20000).decode("utf-8", "ignore")
                info.update(_parse_hls(body))
                info["format"] = "hls"
                info["ok"] = "#EXTM3U" in body
                return info
            chunk = r.read(4096)
            info["ok"] = len(chunk) > 0 and not ctype.startswith("text/html")
            info["format"] = "icecast" if r.headers.get("icy-name") or r.headers.get("icy-br") else "http"
            br = r.headers.get("icy-br")
            if br:
                m = re.match(r"\d+", br)
                if m:
                    b = int(m.group())
                    info["bitrate"] = b // 1000 if b >= 1000 else b  # certains serveurs donnent des bit/s
            sr = r.headers.get("icy-sr") or _audio_info_sr(r.headers.get("ice-audio-info"))
            if sr:
                info["sampleRate"] = int(sr)
            info["codec"] = _codec_from_ctype(ctype)
    except (OSError, socket.timeout, ValueError) as e:
        info["error"] = type(e).__name__ + ": " + str(e)[:120]
    return info


def _audio_info_sr(v: str | None):
    if not v:
        return None
    m = re.search(r"(?:ice-)?samplerate=(\d+)", v)
    return m.group(1) if m else None


def _codec_from_ctype(ctype: str) -> str | None:
    return {
        "audio/mpeg": "MP3", "audio/mp3": "MP3",
        "audio/aac": "AAC", "audio/aacp": "HE-AAC", "audio/x-aac": "AAC",
        "audio/ogg": "OGG", "application/ogg": "OGG", "audio/opus": "OPUS",
        "audio/flac": "FLAC",
    }.get(ctype)


def _parse_hls(body: str) -> dict:
    best = 0
    codecs = None
    for line in body.splitlines():
        if line.startswith("#EXT-X-STREAM-INF"):
            m = re.search(r"[^-]BANDWIDTH=(\d+)", line)
            if m and int(m.group(1)) > best:
                best = int(m.group(1))
                c = re.search(r'CODECS="([^"]+)"', line)
                codecs = c.group(1) if c else None
    out = {"codec": "AAC" if (codecs or "").startswith("mp4a") else ("AAC" if best else None)}
    if best:
        # le BANDWIDTH HLS inclut l'en-tête de transport : estimation du débit audio
        out["bitrate"] = int(round(best / 1000 * 0.7 / 32) * 32) or best // 1000
    return out


def bitrate_from_url(url: str) -> int | None:
    """Beaucoup d'URL portent le débit (« -128.mp3 », « /128k/ », « _64k »)."""
    m = re.search(r"(?<![0-9])(32|48|56|64|96|128|160|192|256|320)(?:k|kbps)?(?=[^0-9]|$)", url.split("?")[0].rsplit("/", 2)[-2] + "/" + url.split("?")[0].rsplit("/", 1)[-1])
    return int(m.group(1)) if m else None


def quality_of(codec: str | None, bitrate: int | None, fmt: str | None) -> str:
    if not bitrate:
        return "std"  # débit inconnu : ni « max » ni « éco » par défaut
    br = bitrate
    aac = (codec or "").upper() in ("AAC", "HE-AAC", "OPUS")
    if fmt == "hls" and br >= 128:
        return "max"
    if br >= 192 or (aac and br >= 96):
        return "max"
    if br >= 96 or (aac and br >= 48):
        return "std"
    return "eco"


QUALITY_ORDER = {"max": 0, "std": 1, "eco": 2}


def stream_rank(s: dict) -> tuple:
    # meilleure qualité d'abord ; à qualité égale, AAC avant MP3, puis débit décroissant
    codec_pref = 0 if (s.get("codec") or "").upper() in ("AAC", "HE-AAC", "OPUS") else 1
    return (not s.get("ok", False), QUALITY_ORDER.get(s.get("quality"), 3), codec_pref, -(s.get("bitrate") or 0))
