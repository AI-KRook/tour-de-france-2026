#!/usr/bin/env python3
"""Werkt data/stand.json bij met uitslagen, klassement en truien.

Bron: Wikipedia (en.wikipedia.org), dat de Tour-uitslagen doorgaans binnen
enkele uren na de finish bijwerkt en goed bereikbaar is vanaf GitHub Actions
(procyclingstats.com blokkeert datacenter-IP's met een Cloudflare-challenge).

Het script is bewust defensief: als een pagina niet geladen of geparset kan
worden, blijft de bestaande data staan en eindigt het met exitcode 0.
Entries met "lock": true worden nooit overschreven.
"""
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

STAND_PAD = Path(__file__).resolve().parent.parent / "data" / "stand.json"
UA = {"User-Agent": "TdF-Routeverkenner/1.0 (hobbyproject; github actions)"}
WIKI = "https://en.wikipedia.org/wiki"
PAGINAS = {
    "1-11": f"{WIKI}/2026_Tour_de_France,_Stage_1_to_Stage_11",
    "12-21": f"{WIKI}/2026_Tour_de_France,_Stage_12_to_Stage_21",
    "hoofd": f"{WIKI}/2026_Tour_de_France",
}

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
            if r.status_code == 404:
                return None  # pagina bestaat (nog) niet
            print(f"  {url}: HTTP {r.status_code}")
        except requests.RequestException as e:
            print(f"  {url}: {e}")
        time.sleep(3 * (poging + 1))
    return None


def schoon(tekst):
    """Verwijdert voetnootmarkeringen en nationaliteit: 'Olav Kooij ( NED ) [ a ]' -> 'Olav Kooij'."""
    tekst = re.sub(r"\[[^\]]*\]", "", tekst or "")
    tekst = re.sub(r"\(\s*[A-Z]{3}\s*\)", "", tekst)
    return re.sub(r"\s+", " ", tekst).strip()


def land_uit(tekst):
    """Haalt de IOC-landcode uit een rennercel: 'Olav Kooij ( NED )' -> 'NED'."""
    m = re.search(r"\(\s*([A-Z]{3})\s*\)", tekst or "")
    return m.group(1) if m else None


def korte_naam(vol):
    delen = (vol or "").split(" ")
    if len(delen) < 2:
        return vol or "—"
    return delen[0][0] + ". " + " ".join(delen[1:])


def wiki_tijd(ruw):
    """Zet wiki-notatie om: '3h 29\\' 07\"' -> '3:29:07', '+ 28\"' -> '+ 0:28', '+ 0\"' -> 'z.t.'."""
    t = (ruw or "").replace("″", '"').replace("′", "'").replace("−", "-")
    t = re.sub(r"\[[^\]]*\]", "", t).strip()
    if not t or t in ("—", "-"):
        return None
    if t.lower().rstrip(".").replace(" ", "") in ("s.t", "st", "z.t", "zt"):
        return "z.t."
    plus = t.startswith("+")
    kern = t.lstrip("+").strip()
    m = re.fullmatch(r"(?:(\d+)\s*h\s*)?(?:(\d+)\s*'\s*)?(?:(\d+)\s*\")?", kern)
    if not m or not any(m.groups()):
        return t  # onbekend formaat: ruwe tekst tonen
    u, mi, s = (int(x) if x else 0 for x in m.groups())
    if plus:
        if u * 3600 + mi * 60 + s == 0:
            return "z.t."
        return f"+ {u}:{mi:02d}:{s:02d}" if u else f"+ {mi}:{s:02d}"
    return f"{u}:{mi:02d}:{s:02d}" if u else f"{mi}:{s:02d}"


def vind_tabel(soup, caption_regex):
    for tabel in soup.find_all("table", class_="wikitable"):
        cap = tabel.find("caption")
        if cap and re.search(caption_regex, cap.get_text(" ", strip=True), re.I):
            return tabel
    return None


def parse_result_tabel(tabel):
    """Wikitable met Rank/Rider/Team/Time (of Rank/Team/Time bij een ploegentijdrit)."""
    kop = [schoon(c.get_text(" ", strip=True)).lower() for c in tabel.find_all("tr")[0].find_all(["th", "td"])]

    def kol(naam):
        return kop.index(naam) if naam in kop else None

    i_naam = kol("rider") if kol("rider") is not None else kol("team")
    i_ploeg = kol("team") if kol("rider") is not None else None
    i_tijd = kol("time")
    if i_naam is None:
        return []
    rijen = []
    for tr in tabel.find_all("tr")[1:]:
        cellen = tr.find_all(["th", "td"])
        nodig = max(i for i in (i_naam, i_ploeg, i_tijd, 0) if i is not None)
        if len(cellen) <= nodig:
            continue
        pos_txt = schoon(cellen[0].get_text(" ", strip=True))
        m = re.match(r"(\d{1,3})", pos_txt)
        if not m:
            continue
        ruwe_naam = cellen[i_naam].get_text(" ", strip=True)
        naam = schoon(ruwe_naam)
        if not naam:
            continue
        rijen.append({
            "pos": int(m.group(1)),
            "naam": naam,
            "land": land_uit(ruwe_naam),
            "ploeg": schoon(cellen[i_ploeg].get_text(" ", strip=True)) if i_ploeg is not None else "",
            "tijd": wiki_tijd(cellen[i_tijd].get_text(" ", strip=True)) if i_tijd is not None else None,
        })
    return rijen


def pod_structuur(rijen):
    return [{"pos": r["pos"], "naam": r["naam"], "land": r["land"],
             "ploeg": r["ploeg"], "tijd": r["tijd"]} for r in rijen[:10]]


def expandeer_grid(tabel):
    """Zet een wikitable met row-/colspans om naar een rechthoekig grid van teksten."""
    grid = []
    hangend = {}  # kolomindex -> [resterende rijen, tekst]
    for tr in tabel.find_all("tr"):
        rij = []
        kol = 0

        def vul_hangend():
            nonlocal kol
            while kol in hangend and hangend[kol][0] > 0:
                rij.append(hangend[kol][1])
                hangend[kol][0] -= 1
                kol += 1

        vul_hangend()
        for cel in tr.find_all(["th", "td"]):
            vul_hangend()
            tekst = schoon(cel.get_text(" ", strip=True))
            span = int(cel.get("rowspan") or 1)
            colspan = int(cel.get("colspan") or 1)
            for _ in range(colspan):
                rij.append(tekst)
                if span > 1:
                    hangend[kol] = [span - 1, tekst]
                kol += 1
            vul_hangend()
        grid.append(rij)
    return grid


def truien_uit_hoofdpagina(html, na_etappe):
    """Leest de 'Classification leadership'-tabel en geeft de dragers na etappe N."""
    soup = BeautifulSoup(html, "html.parser")
    tabel = vind_tabel(soup, r"lassification leadership")
    if tabel is None:
        # tabel heeft niet altijd class wikitable; zoek ook op caption alleen
        for t in soup.find_all("table"):
            cap = t.find("caption")
            if cap and "lassification leadership" in cap.get_text(" ", strip=True):
                tabel = t
                break
    if tabel is None:
        return None
    grid = expandeer_grid(tabel)
    if not grid:
        return None
    kop = [c.lower() for c in grid[0]]

    def kol(deel):
        for i, k in enumerate(kop):
            if deel in k:
                return i
        return None

    kolommen = {"geel": kol("general"), "groen": kol("points"),
                "bollen": kol("mountains"), "wit": kol("young")}
    doel = None
    for rij in grid[1:]:
        if rij and rij[0].strip() == str(na_etappe):
            doel = rij
            break
    if doel is None:
        return None
    truien = {}
    for trui, i in kolommen.items():
        if i is not None and i < len(doel) and doel[i]:
            truien[trui] = doel[i]  # volledige naam; land/ploeg wordt elders opgezocht
    return truien or None


DEMONIEM = {
    "french": "FRA", "dutch": "NED", "belgian": "BEL", "danish": "DEN", "slovenian": "SLO",
    "norwegian": "NOR", "british": "GBR", "american": "USA", "spanish": "ESP", "italian": "ITA",
    "german": "GER", "australian": "AUS", "swiss": "SUI", "austrian": "AUT", "portuguese": "POR",
    "irish": "IRL", "polish": "POL", "czech": "CZE", "slovak": "SVK", "luxembourgish": "LUX",
    "canadian": "CAN", "colombian": "COL", "ecuadorian": "ECU", "mexican": "MEX", "eritrean": "ERI",
    "south african": "RSA", "kazakh": "KAZ", "hungarian": "HUN", "estonian": "EST", "latvian": "LAT",
    "lithuanian": "LTU", "croatian": "CRO", "romanian": "ROU", "ukrainian": "UKR",
    "new zealand": "NZL", "japanese": "JPN", "israeli": "ISR", "swedish": "SWE", "finnish": "FIN",
    "russian": "RUS", "belarusian": "BLR", "argentine": "ARG", "brazilian": "BRA", "greek": "GRE",
    "chinese": "CHN", "costa rican": "CRC", "uruguayan": "URU", "venezuelan": "VEN",
}


def renner_pagina_info(naam):
    """Fallback voor renners die in geen enkele uitslagtabel staan (bijv. de bergtrui-
    drager na een vroege vlucht): lees land en ploeg van de eigen Wikipedia-pagina."""
    html = haal(f"{WIKI}/{naam.replace(' ', '_')}")
    if not html:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    info = {}
    p = soup.select_one("div.mw-parser-output > p:not(.mw-empty-elt)")
    if p:
        intro = schoon(p.get_text(" ", strip=True)).lower()[:250]
        for dem, code in DEMONIEM.items():
            if re.search(rf"\ban? {dem} ", intro):
                info["land"] = code
                break
    if "land" not in info:
        # betrouwbare fallback: de paginacategorie "French male cyclists" e.d.
        m = re.search(r"([A-Z][A-Za-z\- ]+?) (?:male|female) cyclists", html)
        if m and m.group(1).lower() in DEMONIEM:
            info["land"] = DEMONIEM[m.group(1).lower()]
    for tr in soup.select("table.infobox tr"):
        th = tr.find("th")
        if th and "current team" in th.get_text(" ", strip=True).lower():
            td = tr.find("td")
            if td:
                info["ploeg"] = schoon(td.get_text(" ", strip=True))
            break
    return info


def main():
    stand = json.loads(STAND_PAD.read_text(encoding="utf-8")) if STAND_PAD.exists() else {}
    stand.setdefault("uitslagen", {})
    nu = datetime.now(NL_TZ)
    vandaag = nu.strftime("%Y-%m-%d")
    gewijzigd = False

    paginas = {}

    def pagina(sleutel):
        if sleutel not in paginas:
            html = haal(PAGINAS[sleutel])
            paginas[sleutel] = BeautifulSoup(html, "html.parser") if html else None
        return paginas[sleutel]

    # naam -> {land, ploeg}, opgebouwd uit alle geparste tabellen (voor de truiendragers)
    renner_info = {}

    def onthoud(rijen):
        for r in rijen:
            if r["naam"] and (r["land"] or r["ploeg"]):
                renner_info[r["naam"]] = {"land": r["land"], "ploeg": r["ploeg"]}

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
        soup = pagina("1-11" if n <= 11 else "12-21")
        if soup is None:
            continue
        tabel = vind_tabel(soup, rf"Stage {n} result")
        if tabel is None:
            print(f"Etappe {n}: nog geen uitslag op Wikipedia")
            continue
        rijen = parse_result_tabel(tabel)
        if len(rijen) < 3 or rijen[0]["pos"] != 1:
            print(f"Etappe {n}: uitslagtabel nog niet bruikbaar")
            continue
        onthoud(rijen)
        w = rijen[0]
        nieuw = {
            "w": w["naam"],
            "wLand": w["land"],
            "wPloeg": w["ploeg"],
            "note": (bestaand or {}).get("note") or "",
            "pod": pod_structuur(rijen),
            "bron": "en.wikipedia.org",
        }
        if bestaand != nieuw:
            stand["uitslagen"][sleutel] = nieuw
            gewijzigd = True
            print(f"Etappe {n}: winnaar {w['naam']}")
        else:
            print(f"Etappe {n}: ongewijzigd")

    # 2) klassement: hoogste 'General classification after stage N' die er is
    beste_n, beste_tabel = 0, None
    for sleutel_pag in ("12-21", "1-11"):
        soup = pagina(sleutel_pag)
        if soup is None:
            continue
        for tabel in soup.find_all("table", class_="wikitable"):
            cap = tabel.find("caption")
            if not cap:
                continue
            m = re.search(r"General classification after stage (\d+)", cap.get_text(" ", strip=True), re.I)
            if m and int(m.group(1)) > beste_n:
                rijen = parse_result_tabel(tabel)
                if len(rijen) >= 5:
                    beste_n, beste_tabel = int(m.group(1)), rijen
        if beste_tabel:
            break  # de 12-21-pagina heeft altijd een nieuwere stand dan 1-11

    if beste_tabel:
        onthoud(beste_tabel)
        oud = stand.get("klassement") or {}

        def trui_entry(vol_naam):
            info = renner_info.get(vol_naam)
            if info is None:
                # hergebruik wat de vorige run al wist, anders de rennerpagina raadplegen
                for oud_trui in (oud.get("truien") or {}).values():
                    if isinstance(oud_trui, dict) and oud_trui.get("naam") == vol_naam and oud_trui.get("land"):
                        info = oud_trui
                        break
            if info is None:
                info = renner_pagina_info(vol_naam)
            return {"naam": vol_naam, "kort": korte_naam(vol_naam),
                    "land": info.get("land"), "ploeg": info.get("ploeg") or ""}

        top10 = [{"pos": r["pos"], "naam": r["naam"], "land": r["land"], "ploeg": r["ploeg"],
                  "tijd": "—" if r["pos"] == 1 else (r["tijd"] or "—")} for r in beste_tabel[:10]]
        truien = {"geel": trui_entry(beste_tabel[0]["naam"])}
        hoofd_html = haal(PAGINAS["hoofd"])
        extra = truien_uit_hoofdpagina(hoofd_html, beste_n) if hoofd_html else None
        if extra:
            truien.update({trui: trui_entry(naam) for trui, naam in extra.items()})
        else:
            for trui in ("groen", "bollen", "wit"):
                if oud.get("truien", {}).get(trui):
                    truien[trui] = oud["truien"][trui]
        MND = {1: "januari", 2: "februari", 3: "maart", 4: "april", 5: "mei", 6: "juni",
               7: "juli", 8: "augustus", 9: "september", 10: "oktober", 11: "november", 12: "december"}
        nieuw_klassement = {
            "naEtappe": beste_n,
            "truien": truien,
            "top10": top10,
            "voetnoot": f"Stand na etappe {beste_n}, automatisch bijgewerkt "
                        f"{nu.day} {MND[nu.month]} {nu.strftime('%H:%M')} uur (bron: Wikipedia).",
        }
        # alleen schrijven als er inhoudelijk iets veranderde (voetnoot telt niet mee)
        if {k: v for k, v in oud.items() if k != "voetnoot"} != \
           {k: v for k, v in nieuw_klassement.items() if k != "voetnoot"}:
            stand["klassement"] = nieuw_klassement
            gewijzigd = True
            print(f"Klassement bijgewerkt na etappe {beste_n}; geel: {truien['geel']['kort']}")
        else:
            print(f"Klassement ongewijzigd (na etappe {beste_n})")
    else:
        print("Geen bruikbare GC-tabel gevonden")

    if gewijzigd:
        stand["bijgewerkt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        STAND_PAD.write_text(json.dumps(stand, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("data/stand.json weggeschreven.")
    else:
        print("Geen wijzigingen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
