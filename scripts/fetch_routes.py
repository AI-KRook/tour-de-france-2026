#!/usr/bin/env python3
"""Haalt officiële GPX-routes op (letour.fr / ASO via cyclingstage.com) en zet ze
om naar data/routes.json in het formaat dat de app gebruikt (pts/cum/prof/km/hm/top).

Alleen etappes die nog niet in routes.json staan worden opgehaald; het script
faalt nooit hard (ontbrekende GPX = de app toont een indicatieve lijn).
"""
import json
import math
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

ROUTES_PAD = Path(__file__).resolve().parent.parent / "data" / "routes.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
CDN = "https://cdn.cyclingstage.com/images/tour-de-france/2026"


def haversine(a, b):
    R = 6371.0
    rad = math.pi / 180
    dlat = (b[0] - a[0]) * rad
    dlng = (b[1] - a[1]) * rad
    h = math.sin(dlat / 2) ** 2 + math.cos(a[0] * rad) * math.cos(b[0] * rad) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def parse_gpx(tekst):
    # namespaces negeren door lokale tagnamen te matchen
    wortel = ET.fromstring(tekst)
    punten = []
    for el in wortel.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("trkpt", "rtept"):
            lat, lon = float(el.get("lat")), float(el.get("lon"))
            ele = None
            for kind in el:
                if kind.tag.rsplit("}", 1)[-1] == "ele":
                    try:
                        ele = float(kind.text)
                    except (TypeError, ValueError):
                        ele = None
            punten.append((lat, lon, ele))
    return punten


def verwerk(punten):
    """Zelfde logica als de GPX-import in de app."""
    cum_vol = [0.0]
    for i in range(1, len(punten)):
        cum_vol.append(cum_vol[-1] + haversine(punten[i - 1], punten[i]))
    tot_km = cum_vol[-1]
    stap = max(1, len(punten) // 900)
    pts, cum = [], []
    for i in range(0, len(punten), stap):
        pts.append([round(punten[i][0], 5), round(punten[i][1], 5)])
        cum.append(round(cum_vol[i], 2))
    if cum[-1] != round(tot_km, 2):
        pts.append([round(punten[-1][0], 5), round(punten[-1][1], 5)])
        cum.append(round(tot_km, 2))
    prof, hm, top = [], 0.0, 0.0
    laatste_ele, laatste_km = None, -1.0
    heeft_ele = any(p[2] is not None for p in punten)
    if heeft_ele:
        for i, p in enumerate(punten):
            ele = p[2]
            if ele is None:
                continue
            top = max(top, ele)
            if laatste_ele is not None and ele > laatste_ele + 3:
                hm += ele - laatste_ele
                laatste_ele = ele
            elif laatste_ele is None or ele < laatste_ele:
                laatste_ele = ele
            if cum_vol[i] - laatste_km >= 0.5 or i == len(punten) - 1:
                prof.append([round(cum_vol[i], 2), round(ele)])
                laatste_km = cum_vol[i]
    return {
        "pts": pts, "cum": cum, "prof": prof if heeft_ele else None,
        "km": round(tot_km, 1), "hm": round(hm), "top": round(top),
    }


def main():
    routes = json.loads(ROUTES_PAD.read_text(encoding="utf-8")) if ROUTES_PAD.exists() else {}
    gewijzigd = False
    for n in range(1, 22):
        if str(n) in routes:
            continue
        data = None
        for naam in (f"stage-{n}-route.gpx", f"stage-{n}.gpx", f"stage-{n}-parcours.gpx"):
            url = f"{CDN}/{naam}"
            try:
                r = requests.get(url, headers=UA, timeout=45)
            except requests.RequestException as e:
                print(f"  etappe {n}: {e}")
                continue
            if r.status_code != 200 or b"<gpx" not in r.content[:2000]:
                continue
            try:
                punten = parse_gpx(r.text)
            except ET.ParseError:
                continue
            if len(punten) < 50:
                continue
            data = verwerk(punten)
            print(f"Etappe {n}: {naam} → {data['km']} km, {data['hm']} hm, {len(data['pts'])} punten")
            break
        if data:
            routes[str(n)] = data
            gewijzigd = True
            time.sleep(2)
    if gewijzigd:
        ROUTES_PAD.write_text(
            json.dumps(routes, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"data/routes.json weggeschreven ({len(routes)} etappes).")
    else:
        print(f"Geen nieuwe routes (al {len(routes)} aanwezig).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
