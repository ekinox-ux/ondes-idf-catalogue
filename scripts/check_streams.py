"""Vérification nocturne des flux de stations.json (lancé par GitHub Actions).

Pour chaque flux : on le sonde, on met à jour ok / lastChecked / codec / débit.
Pour une station dont plus aucun flux ne répond, ou qui n'a aucun flux : on relance
la recherche complète (build_catalog.build_station) pour trouver un remplaçant.
Écrit un rapport lisible dans report.md.

  python scripts/check_streams.py
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from build_catalog import OUT, SEED, build_station
from catalog_lib import SCHEMA_VERSION, now_iso, probe, quality_of, stream_rank

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "report.md"


def recheck(stream: dict) -> dict:
    info = probe(stream["url"])
    s = dict(stream)
    s["ok"] = bool(info.get("ok"))
    s["lastChecked"] = now_iso()
    if s["ok"]:
        s["codec"] = info.get("codec") or s.get("codec")
        s["bitrate"] = info.get("bitrate") or s.get("bitrate")
        s["sampleRate"] = info.get("sampleRate") or s.get("sampleRate")
        s["quality"] = quality_of(s["codec"], s["bitrate"], s.get("format"))
    return s


def main() -> int:
    data = json.loads(OUT.read_text(encoding="utf-8"))
    seeds = {s["id"]: s for s in json.loads(SEED.read_text(encoding="utf-8"))["stations"]}
    stations = data["stations"]

    flat = [(i, s) for i, st in enumerate(stations) for s in st["streams"]]
    with ThreadPoolExecutor(max_workers=12) as pool:
        checked = list(pool.map(lambda t: recheck(t[1]), flat))
    for st in stations:
        st["streams"] = []
    for (i, _), s in zip(flat, checked):
        stations[i]["streams"].append(s)

    repaired, dead = [], []
    for idx, st in enumerate(stations):
        if any(s["ok"] for s in st["streams"]):
            st["streams"].sort(key=stream_rank)
            continue
        seed = seeds.get(st["id"])
        if not seed:
            continue
        fresh, _ = build_station(seed)
        if fresh["streams"]:
            fresh["logo"] = fresh["logo"] or st.get("logo")
            stations[idx] = fresh
            repaired.append(st["id"])
        else:
            dead.append(st["id"])

    # stations ajoutées dans la seed depuis le dernier passage
    known = {s["id"] for s in stations}
    added = []
    for sid, seed in seeds.items():
        if sid not in known:
            fresh, _ = build_station(seed)
            stations.append(fresh)
            added.append(sid)
    order = list(seeds)
    stations.sort(key=lambda s: order.index(s["id"]) if s["id"] in order else 10_000)

    data.update(schemaVersion=SCHEMA_VERSION, generatedAt=now_iso(), stations=stations)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    total = len(stations)
    alive = sum(1 for s in stations if any(f["ok"] for f in s["streams"]))
    streams_ok = sum(1 for s in checked if s["ok"])
    lines = [
        f"# Rapport de vérification — {now_iso()}",
        "",
        f"- Stations jouables : **{alive}/{total}**",
        f"- Flux testés : {len(checked)}, valides : {streams_ok}",
        f"- Réparées automatiquement : {', '.join(repaired) or 'aucune'}",
        f"- Ajoutées depuis la seed : {', '.join(added) or 'aucune'}",
        f"- Sans flux : {', '.join(dead) or 'aucune'}",
        "",
        "| Station | Flux OK | Meilleur flux |",
        "|---|---|---|",
    ]
    for st in stations:
        ok = [f for f in st["streams"] if f["ok"]]
        best = ok[0] if ok else None
        desc = f"{best['codec'] or '?'} {best['bitrate'] or '?'}k" if best else "—"
        lines.append(f"| {st['name']} | {len(ok)}/{len(st['streams'])} | {desc} |")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:7]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
