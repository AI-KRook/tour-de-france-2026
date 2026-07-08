#!/usr/bin/env python3
"""Werkt data/stand.json bij met uitslagen, klassement en truien.

Bron: procyclingstats.com. Het script is bewust defensief: als een pagina
niet geladen of geparset kan worden, blijft de bestaande data staan en
eindigt het script gewoon met exitcode 0. Entries met "lock": true worden
nooit overschreven.
"""
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASIS = "https://www.procyclingstats.com/race/tour-de-france/2026"
STAND_PAD = Path(__file__).resolve().parent.parent / "data" / "stand.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; TdF-routeverkenner; +https://github.com) AppleWebKit/537.36 Chrome/126 Safari/537.36"}

# etappenummer -> (datum, starttijd lokale NL-tijd)
ETAPPES = {
    1: ("2026-07-04", "17:10"), 2: ("2026-07-05", "13:15"), 3: ("2026-07-06", "12:45"),
    4: ("2026-07-07", "13:15"), 5: ("2026-07-08", "14:15"), 6: ("2026-07-09", "12:40"),
    7: ("2026-07-10", "13:25"), 8: ("2026-07-11", "13:25"), 9: ("2026-07-12", "13:45"),
    10: ("2026-07-14", "13:25"), 11: ("2026-07-15", "14:05"), 12: ("2026-07-16", "13:40"),
    13: ("2026-07-17", "13:20"), 14: ("2026-07-18", "12:30"), 15: ("2026-07-19", "12:20"),
    16: ("2026-07-21", "13:05"), 17: ("2026-07-22", "12:35"), 18: ("2026-07-23", "11:50"),
    19: ("2026-07-24", "13:15"), 20: ("2026-07-25", "10:30"), 21: ("2026-07-26", "16:00"),
}

NL_TZ = timezone(timedelta(hours=2))  # CEST in juli


def haal(url):
    for poging in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code == 200:
                return r.text
            print(f"  {url}: HTTP {r.status_code}")
        except requests.RequestException as e:
            print(f"  {url}: {e}")
        time.sleep(3 * (poging + 1))
    return None


TUSSENVOEGSELS = {"van", "der", "de", "den", "ter", "ten", "te", "la", "le", "los",
                  "di", "del", "della", "da", "dos", "von", "af", "el", "al"}


def _cap(w):
    """Hoofdletter per naamdeel, ook na apostrof of koppelteken (O'Connor, Saint-Croix)."""
    w = w.lower()
    return re.sub(r"(^|['’\-])(\w)", lambda m: m.group(1) + m.group(2).upper(), w)


def net_naam(ruw):
    """PCS toont namen als 'POGAČAR Tadej' of 'VAN AERT Wout' -> 'Tadej Pogačar' / 'Wout van Aert'."""
    ruw = re.sub(r"\s+", " ", (ruw or "").strip())
    if not ruw:
        return ""
    delen = ruw.split(" ")
    familie, gegeven = [], []
    for d in delen:
        # hoofdletterwoorden (incl. tussenvoegsels als VAN, DER) horen bij de familienaam
        kaal = "".join(c for c in unicodedata.normalize("NFD", d) if c.isalpha())
        if kaal and kaal == kaal.upper() and not gegeven:
            familie.append(d.lower() if d.lower() in TUSSENVOEGSELS else _cap(d))
        else:
            gegeven.append(d)
    if not familie or not gegeven:
        return ruw
    return " ".join(gegeven) + " " + " ".join(familie)


def korte_naam(vol):
    delen = vol.split(" ")
    if len(delen) < 2:
        return vol
    return delen[0][0] + ". " + " ".join(delen[1:])


def parse_tijd_sec(t):
    t = re.sub(r"[^\d:]", "", t or "")
    if not t or ":" not in t:
        return None
    try:
        delen = [int(x) for x in t.split(":")]
    except ValueError:
        return None
    sec = 0
    for d in delen:
        sec = sec * 60 + d
    return sec


def fmt_gap(sec):
    if sec <= 0:
        return "—"
    u, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    return f"+ {u}:{m:02d}:{s:02d}" if u else f"+ {m}:{s:02d}"


def fmt_duur(sec):
    u, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    return f"{u}:{m:02d}:{s:02d}" if u else f"{m}:{s:02d}"


def eerste_resultstabel(soup):
    for tabel in soup.select("table"):
        klassen = " ".join(tabel.get("class") or [])
        if "results" in klassen:
            return tabel
    return None


def parse_rijen(tabel):
    """Geeft lijst van dicts: pos, naam, ploeg, tijd_sec (cumulatief) of tijd_ruw."""
    kop = [th.get_text(" ", strip=True).lower() for th in tabel.select("thead th")]

    def kol(*namen):
        for i, k in enumerate(kop):
            if any(n in k for n in namen):
                return i
        return None

    i_tijd = kol("time")
    rijen = []
    vorige_sec = None
    for tr in tabel.select("tbody tr"):
        tds = tr.select("td")
        if not tds:
            continue
        pos_txt = tds[0].get_text(strip=True)
        if not re.fullmatch(r"\d{1,3}", pos_txt):
            continue  # DNF/DNS/kopregels overslaan
        rider = tr.select_one('a[href^="rider"]')
        team = tr.select_one('a[href^="team"]')
        naam = net_naam(rider.get_text(" ", strip=True)) if rider else ""
        if not naam:
            continue
        tijd_ruw = ""
        if i_tijd is not None and i_tijd < len(tds):
            tekst = tds[i_tijd].get_text(" ", strip=True)
            # PCS plakt tijden soms dubbel aan elkaar ("4:10:454:10:45"): pak de eerste echte tijd
            m = re.search(r"(?:\d{1,3}:)?\d{1,2}:\d{2}", tekst)
            if m:
                tijd_ruw = ("+ " if tekst.lstrip().startswith("+") else "") + m.group(0)
            else:
                tijd_ruw = tekst
        sec = parse_tijd_sec(tijd_ruw)
        if tijd_ruw in (",,", "", "-", '"'):
            sec = vorige_sec
        if sec is not None:
            vorige_sec = sec
        rijen.append({
            "pos": int(pos_txt),
            "naam": naam,
            "ploeg": team.get_text(" ", strip=True) if team else "",
            "sec": sec,
            "ruw": tijd_ruw,
        })
    # Normaliseren naar cumulatieve tijden. Rijen na de leider tonen soms een
    # achterstand ("+ 0:28") in plaats van een totaaltijd; een echte totaaltijd
    # is nooit kleiner dan die van de leider, dus kleiner = achterstand.
    if rijen and rijen[0]["sec"] is not None:
        leider = rijen[0]["sec"]
        for r in rijen[1:]:
            if r["sec"] is not None and r["sec"] < leider:
                r["sec"] = leider + r["sec"]
    return rijen


def pod_regels(rijen):
    """Bouwt de top-10-regels in het formaat van de app."""
    regels = []
    leider_sec = rijen[0]["sec"]
    for r in rijen[:10]:
        deel = f'{r["pos"]}. {r["naam"]}'
        if r["ploeg"]:
            deel += f' ({r["ploeg"]})'
        if r["pos"] == 1 and leider_sec:
            deel += f" · {fmt_duur(leider_sec)}"
        elif r["sec"] is not None and leider_sec is not None:
            gap = r["sec"] - leider_sec
            deel += " z.t." if gap <= 0 else f" {fmt_gap(gap)}"
        regels.append(deel)
    return regels


def scrape_etappe(n):
    html = haal(f"{BASIS}/stage-{n}")
    if not html:
        return None
    tabel = eerste_resultstabel(BeautifulSoup(html, "html.parser"))
    if tabel is None:
        return None
    rijen = parse_rijen(tabel)
    if len(rijen) < 3 or rijen[0]["pos"] != 1:
        return None
    w = rijen[0]
    return {
        "w": w["naam"],
        "note": f'{w["ploeg"]}' if w["ploeg"] else "",
        "pod": pod_regels(rijen),
        "bron": "procyclingstats.com",
    }


def scrape_klassement_cat(cat):
    html = haal(f"{BASIS}/{cat}")
    if not html:
        return None
    tabel = eerste_resultstabel(BeautifulSoup(html, "html.parser"))
    if tabel is None:
        return None
    rijen = parse_rijen(tabel)
    return rijen if rijen else None


def main():
    stand = json.loads(STAND_PAD.read_text(encoding="utf-8")) if STAND_PAD.exists() else {}
    stand.setdefault("uitslagen", {})
    nu = datetime.now(NL_TZ)
    vandaag = nu.strftime("%Y-%m-%d")
    gewijzigd = False

    # 1) etappe-uitslagen
    for n, (datum, start) in ETAPPES.items():
        sleutel = str(n)
        bestaand = stand["uitslagen"].get(sleutel)
        if bestaand and bestaand.get("lock"):
            continue
        if datum > vandaag:
            continue
        if datum == vandaag:
            # pas proberen vanaf ± 2,5 uur na de start
            su, sm = map(int, start.split(":"))
            if (nu.hour * 60 + nu.minute) < su * 60 + sm + 150:
                continue
        if bestaand and bestaand.get("bron") and datum < vandaag:
            continue  # eerdere etappe al binnen; niet blijven herscrapen
        print(f"Etappe {n} ({datum}) ophalen…")
        res = scrape_etappe(n)
        if res:
            if bestaand and bestaand.get("note"):
                res["note"] = bestaand["note"]
            stand["uitslagen"][sleutel] = res
            gewijzigd = True
            print(f"  winnaar: {res['w']}")
        else:
            print("  nog geen (bruikbare) uitslag")

    # 2) klassement + truien
    verreden = [n for n, (d, _) in ETAPPES.items()
                if str(n) in stand["uitslagen"] and stand["uitslagen"][str(n)].get("pod")]
    if verreden:
        gc = scrape_klassement_cat("gc")
        if gc and len(gc) >= 10 and gc[0]["pos"] == 1:
            leider_sec = gc[0]["sec"]
            top10 = []
            for r in gc[:10]:
                if r["pos"] == 1 or r["sec"] is None or leider_sec is None:
                    tijd = "—" if r["pos"] == 1 else (r["ruw"] or "—")
                else:
                    gap = r["sec"] - leider_sec
                    tijd = "z.t." if gap <= 0 else fmt_gap(gap)
                top10.append({"pos": r["pos"], "naam": r["naam"], "ploeg": r["ploeg"], "tijd": tijd})
            truien = {"geel": korte_naam(gc[0]["naam"])}
            for cat, trui in (("points", "groen"), ("kom", "bollen"), ("youth", "wit")):
                rijen = scrape_klassement_cat(cat)
                if rijen:
                    truien[trui] = korte_naam(rijen[0]["naam"])
                else:
                    oud = (stand.get("klassement") or {}).get("truien", {}).get(trui)
                    if oud:
                        truien[trui] = oud
            stand["klassement"] = {
                "naEtappe": max(verreden),
                "truien": truien,
                "top10": top10,
                "voetnoot": f"Stand na etappe {max(verreden)}, automatisch bijgewerkt "
                            f"{nu.strftime('%-d %B').replace('July', 'juli')} {nu.strftime('%H:%M')} uur "
                            f"(bron: procyclingstats.com).",
            }
            gewijzigd = True
            print(f"Klassement bijgewerkt na etappe {max(verreden)}; geel: {truien['geel']}")
        else:
            print("Klassement niet bijgewerkt (geen bruikbare GC-tabel)")

    if gewijzigd:
        stand["bijgewerkt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        STAND_PAD.write_text(json.dumps(stand, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("data/stand.json weggeschreven.")
    else:
        print("Geen wijzigingen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
