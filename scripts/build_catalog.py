"""Construit stations.json à partir de seed/stations.seed.json.

  python scripts/build_catalog.py            # reconstruit tout
  python scripts/build_catalog.py fip rtl    # seulement certaines stations (fusion dans l'existant)

Sources des flux :
  - Radio France : URL officielles icecast.radiofrance.fr / stream.radiofrance.fr
  - autres       : radio-browser.info (meilleures correspondances), puis sondage réel
Un champ "streams" ou "logo" présent dans la seed est prioritaire (correction manuelle).
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from catalog_lib import (SCHEMA_VERSION, bitrate_from_url, now_iso, probe, quality_of,
                         rb_best_matches, stream_rank)

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "seed" / "stations.seed.json"
OUT = ROOT / "stations.json"
MAX_STREAMS = 4


def radio_france_candidates(rf: str) -> list[str]:
    return [
        f"https://stream.radiofrance.fr/{rf}/{rf}.m3u8?id=radiofrance",
        f"https://icecast.radiofrance.fr/{rf}-hifi.aac",
        f"https://icecast.radiofrance.fr/{rf}-midfi.mp3",
        f"https://icecast.radiofrance.fr/{rf}-lofi.aac",
    ]


def build_stream(url: str, hint: dict | None = None) -> dict | None:
    info = probe(url)
    # un flux http qui existe aussi en https : on garde le https
    if url.startswith("http://"):
        secure = probe("https://" + url[7:])
        if secure.get("ok"):
            url, info = "https://" + url[7:], secure
    if not info.get("ok"):
        return None
    hint = hint or {}
    codec = info.get("codec") or (hint.get("codec") or "").upper() or None
    if codec in ("UNKNOWN", ""):
        codec = None
    bitrate = info.get("bitrate") or hint.get("bitrate") or bitrate_from_url(url) or None
    if bitrate and bitrate >= 1000:
        bitrate //= 1000
    fmt = info.get("format", "http")
    return {
        "url": url,
        "codec": codec,
        "bitrate": bitrate,
        "sampleRate": info.get("sampleRate"),
        "format": fmt,
        "quality": quality_of(codec, bitrate, fmt),
        "ok": True,
        "lastChecked": now_iso(),
    }


def build_station(seed: dict) -> tuple[dict, list[str]]:
    notes: list[str] = []
    matches = []
    try:
        if not seed.get("skipRb"):
            matches = rb_best_matches(seed)
    except Exception as e:  # Radio Browser indisponible : on continue sans
        notes.append(f"radio-browser: {e}")
    top = matches[0] if matches else None

    candidates: list[tuple[str, dict | None]] = []
    if seed.get("streams"):
        candidates += [(s["url"], s) for s in seed["streams"]]
    elif seed.get("rf"):
        candidates += [(u, None) for u in radio_france_candidates(seed["rf"])]
    else:
        seen = set()
        for m in matches[:6]:
            u = m.get("url_resolved") or m.get("url")
            if u and u not in seen:
                seen.add(u)
                candidates.append((u, {"codec": m.get("codec"), "bitrate": m.get("bitrate")}))

    streams = [s for s in (build_stream(u, h) for u, h in candidates) if s]
    # dédoublonnage par URL finale
    uniq = {s["url"]: s for s in streams}
    streams = sorted(uniq.values(), key=stream_rank)[:MAX_STREAMS]
    if not streams:
        notes.append("AUCUN FLUX VALIDE")

    logo = seed.get("logo") or (top.get("favicon") if top else None) or None
    if not logo:
        notes.append("pas de logo")

    station = {
        "id": seed["id"],
        "name": seed["name"],
        "fm": seed.get("fm"),
        "dab": seed.get("dab"),
        "genres": seed.get("genres", []),
        "type": seed.get("type", "locale"),
        "group": seed.get("group"),
        "web": seed.get("web") or (top.get("homepage") if top else None) or None,
        "logo": logo,
        "rfApi": seed.get("rfApi"),
        "rbUuid": top.get("stationuuid") if top and not seed.get("rf") else None,
        "streams": streams,
    }
    return station, notes


def main(argv: list[str]) -> int:
    seeds = json.loads(SEED.read_text(encoding="utf-8"))["stations"]
    only = set(argv)
    if only:
        seeds = [s for s in seeds if s["id"] in only]

    existing = {}
    if OUT.exists():
        existing = {s["id"]: s for s in json.loads(OUT.read_text(encoding="utf-8"))["stations"]}

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(build_station, seeds))

    for station, notes in results:
        prev = existing.get(station["id"])
        # si on ne trouve plus rien, on garde l'ancienne version plutôt que de perdre la station
        if not station["streams"] and prev and prev.get("streams"):
            notes.append("conservé depuis la version précédente")
            station["streams"] = prev["streams"]
        existing[station["id"]] = station
        flag = "OK " if station["streams"] else "!! "
        best = station["streams"][0] if station["streams"] else {}
        print(f"{flag}{station['id']:<26} {len(station['streams'])} flux  "
              f"{best.get('codec') or '-':<6} {best.get('bitrate') or '-':>4}k  {'; '.join(notes)}")

    order = [s["id"] for s in json.loads(SEED.read_text(encoding="utf-8"))["stations"]]
    stations = [existing[i] for i in order if i in existing]
    OUT.write_text(json.dumps({
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now_iso(),
        "stations": stations,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    ok = sum(1 for s in stations if s["streams"])
    print(f"\n{ok}/{len(stations)} stations avec au moins un flux valide -> {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
