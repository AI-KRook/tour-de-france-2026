#!/usr/bin/env python3
"""Werkt data/stand.json bij met uitslagen, klassement en truien.

Bron: Wikipedia (en.wikipedia.org), dat de Tour-uitslagen doorgaans binnen
enkele uren na de finish bijwerkt en goed bereikbaar is vanaf GitHub Actions
(procyclingstats.com blokkeert datacenter-IP's met een Cloudflare-challenge).

Het script is bewust defensief: als een pagina niet geladen of geparset kan
worden, blijft de bestaande data staan en eindigt het met exitcode 0.
Entries met "lock": true worden nooit overschreven.
"""
import hashlib
import json
import os
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

# etappenummer -> (startplaats, finishplaats, type) voor het dagcommentaar
ETAPPE_INFO = {
    1: ("Barcelona", "Barcelona", "tijdrit"), 2: ("Tarragona", "Barcelona", "heuvels"),
    3: ("Granollers", "Les Angles", "berg"), 4: ("Carcassonne", "Foix", "heuvels"),
    5: ("Lannemezan", "Pau", "vlak"), 6: ("Pau", "Gavarnie-Gèdre", "berg"),
    7: ("Hagetmau", "Bordeaux", "vlak"), 8: ("Périgueux", "Bergerac", "vlak"),
    9: ("Malemort", "Ussel", "heuvels"), 10: ("Aurillac", "Le Lioran", "berg"),
    11: ("Vichy", "Nevers", "vlak"), 12: ("Magny-Cours", "Chalon-sur-Saône", "vlak"),
    13: ("Dole", "Belfort", "heuvels"), 14: ("Mulhouse", "Le Markstein", "berg"),
    15: ("Champagnole", "Plateau de Solaison", "berg"), 16: ("Évian-les-Bains", "Thonon-les-Bains", "tijdrit"),
    17: ("Chambéry", "Voiron", "vlak"), 18: ("Voiron", "Orcières-Merlette", "berg"),
    19: ("Gap", "Alpe d'Huez", "berg"), 20: ("Le Bourg-d'Oisans", "Alpe d'Huez", "berg"),
    21: ("Thoiry", "Parijs", "vlak"),
}


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


def zelfde_renner(a, b):
    """Bepaalt of twee namen dezelfde renner zijn, accent- en hoofdletterongevoelig
    en bestand tegen verschillende volledigheid. 'Tadej Pogačar' (Wikipedia),
    'Tadej Pogacar' en 'Isaac del Toro' vs 'Isaac Del Toro Romero' (letour) tellen
    als dezelfde renner: de woorden van de kortste naam zitten volledig in de andere."""
    import unicodedata

    def woorden(s):
        s = unicodedata.normalize("NFKD", s or "")
        s = "".join(c for c in s if not unicodedata.combining(c))
        return set(s.lower().split())

    wa, wb = woorden(a), woorden(b)
    if not wa or not wb:
        return False
    return wa <= wb or wb <= wa


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
    i_pnt = kol("points")
    if i_naam is None:
        return []
    rijen = []
    for tr in tabel.find_all("tr")[1:]:
        cellen = tr.find_all(["th", "td"])
        nodig = max(i for i in (i_naam, i_ploeg, i_tijd, i_pnt, 0) if i is not None)
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
        pnt = None
        if i_pnt is not None:
            mp = re.search(r"\d+", cellen[i_pnt].get_text(" ", strip=True))
            pnt = int(mp.group(0)) if mp else None
        rijen.append({
            "pos": int(m.group(1)),
            "naam": naam,
            "land": land_uit(ruwe_naam),
            "ploeg": schoon(cellen[i_ploeg].get_text(" ", strip=True)) if i_ploeg is not None else "",
            "tijd": wiki_tijd(cellen[i_tijd].get_text(" ", strip=True)) if i_tijd is not None else None,
            "pnt": pnt,
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


WERKWOORD = {
    "vlak": "sprintte naar de zege in",
    "tijdrit": "was de snelste tegen de klok in",
    "berg": "bedwong als eerste de slotklim naar",
    "heuvels": "sloeg toe in",
}
TRUI_LABEL = {"geel": "de gele trui", "groen": "de groene trui",
              "bollen": "de bollentrui", "wit": "de witte trui"}


MAAND = {1: "januari", 2: "februari", 3: "maart", 4: "april", 5: "mei", 6: "juni",
         7: "juli", 8: "augustus", 9: "september", 10: "oktober", 11: "november", 12: "december"}


def maak_feiten(stand):
    """Feitenblad voor het dagcommentaar: de laatste uitslag, truiwissels en de stand."""
    uitslagen = stand.get("uitslagen", {})
    volgende = next((n for n in sorted(ETAPPES) if not (uitslagen.get(str(n)) or {}).get("pod")), None)
    vorige = max((n for n in sorted(ETAPPES) if (uitslagen.get(str(n)) or {}).get("pod")), default=None)
    if volgende is None or vorige is None:
        return None
    u = uitslagen[str(vorige)]
    k = stand.get("klassement") or {}
    top10 = k.get("top10") or []
    feiten = {
        "volgende": {"n": volgende, "start": ETAPPE_INFO[volgende][0],
                     "finish": ETAPPE_INFO[volgende][1], "type": ETAPPE_INFO[volgende][2],
                     "datum": ETAPPES[volgende][0]},
        "vorige": {"n": vorige, "finish": ETAPPE_INFO[vorige][1], "type": ETAPPE_INFO[vorige][2],
                   "top3": [{"naam": p.get("naam"), "ploeg": p.get("ploeg"), "tijd": p.get("tijd")}
                            for p in (u.get("pod") or [])[:3]]},
        "wissels": [w for w in (k.get("wissels") or [])
                    if not zelfde_renner(w.get("van"), w.get("naar"))],
        "truien": {t: (v.get("naam") if isinstance(v, dict) else v)
                   for t, v in (k.get("truien") or {}).items()},
    }
    if len(top10) > 1:
        feiten["geel"] = {"naam": top10[0]["naam"], "achtervolger": top10[1]["naam"],
                          "achterstand": top10[1].get("tijd")}
    return feiten


def regels_commentaar(f):
    """Dagcommentaar volgens een vast patroon; werkt zonder API-sleutel."""
    top3 = f["vorige"]["top3"]
    w = top3[0]
    zin = w["naam"] + (f' ({w["ploeg"]})' if w.get("ploeg") else "")
    zin += f' {WERKWOORD.get(f["vorige"]["type"], "won in")} {f["vorige"]["finish"]}'
    rest = [p["naam"] for p in top3[1:] if p.get("naam")]
    if rest:
        zin += ", voor " + " en ".join(rest)
    zinnen = [zin + "."]
    if f["wissels"]:
        delen = [f'{x["naar"]} neemt {TRUI_LABEL.get(x["trui"], x["trui"])} over van {x["van"]}'
                 for x in f["wissels"]]
        zinnen.append(" en ".join(delen) + ".")
    else:
        zinnen.append("Alle truien blijven om dezelfde schouders.")
    if f.get("geel"):
        achterstand = str(f["geel"].get("achterstand") or "").replace("+ ", "")
        zinnen.append(f'In het algemeen klassement leidt {f["geel"]["naam"]}, '
                      f'met {f["geel"]["achtervolger"]} als eerste achtervolger op {achterstand}.')
    return " ".join(zinnen)


def claude_commentaar(f):
    """Laat Claude het dagcommentaar schrijven. Geeft None terug zonder API-sleutel
    of bij fouten, zodat het vaste patroon het overneemt."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        prompt = (
            "Je bent een enthousiaste Nederlandse wielercommentator bij de Tour de France 2026. "
            "Schrijf een terugblik van drie of vier zinnen op de vorige etappe als opmaat naar de volgende rit. "
            "Gebruik uitsluitend de feiten hieronder en verzin niets. Noem de winnaar, zeg iets over de truien "
            "en de stand, en sluit af met precies één zin die de brug slaat naar de komende etappe. "
            "Schrijf lopende tekst zonder opsommingen, zonder kopjes en zonder gedachtestreepjes. "
            "Antwoord met alleen die tekst.\n\nFeiten (JSON):\n" + json.dumps(f, ensure_ascii=False)
        )
        resp = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.stop_reason == "refusal":
            return None
        tekst = next((b.text for b in resp.content if b.type == "text"), "").strip()
        return tekst or None
    except Exception as e:
        print(f"  Claude-commentaar mislukt, vast patroon gebruikt: {e}")
        return None


def maak_feiten_samenvatting(stand, n):
    """Feitenblad voor de terugblik op een verreden etappe n."""
    u = (stand.get("uitslagen", {}) or {}).get(str(n))
    if not (u and u.get("pod")):
        return None
    k = stand.get("klassement") or {}
    top10 = k.get("top10") or []
    info = ETAPPE_INFO.get(n, ("", "", ""))
    feiten = {
        "etappe": n, "start": info[0], "finish": info[1], "type": info[2],
        "podium": [{"naam": p.get("naam"), "ploeg": p.get("ploeg"),
                    "tijd": (p.get("tijd") or "").replace("+ ", "+")}
                   for p in (u.get("pod") or [])[:5]],
        "wissels": [w for w in ((k.get("wissels") or []) if k.get("naEtappe") == n else [])
                    if not zelfde_renner(w.get("van"), w.get("naar"))],
        "truien": {t: (v.get("naam") if isinstance(v, dict) else v)
                   for t, v in (k.get("truien") or {}).items()},
    }
    if len(top10) > 1:
        feiten["klassement_top3"] = [{"naam": r.get("naam"), "achterstand": r.get("tijd")}
                                     for r in top10[:3]]
    return feiten


def regels_samenvatting(f):
    """Terugblik volgens een vast patroon; werkt zonder API-sleutel."""
    pod = f["podium"]
    w = pod[0]
    zin = w["naam"] + (f' ({w["ploeg"]})' if w.get("ploeg") else "")
    zin += f' {WERKWOORD.get(f["type"], "won in")} {f["finish"]}'
    if len(pod) > 1 and pod[1].get("tijd") and pod[1]["tijd"] not in ("", "—"):
        zin += f', met {pod[1]["naam"]} op {pod[1]["tijd"]}'
    elif len(pod) > 1:
        zin += f', voor {pod[1]["naam"]}'
    zinnen = [zin + "."]
    if f.get("wissels"):
        # truien die naar dezelfde renner gaan, bundelen tot één zin
        per_renner = {}
        for x in f["wissels"]:
            per_renner.setdefault(x["naar"], []).append(TRUI_LABEL.get(x["trui"], x["trui"]))
        delen = []
        for naar, truien in per_renner.items():
            truitekst = truien[0] if len(truien) == 1 else " en ".join([", ".join(truien[:-1]), truien[-1]])
            delen.append(f'{naar} pakt {truitekst}')
        zinnen.append(". ".join(d[0].upper() + d[1:] for d in delen) + ".")
    else:
        zinnen.append("Alle truien bleven om dezelfde schouders.")
    if f.get("klassement_top3") and len(f["klassement_top3"]) > 1:
        g = f["klassement_top3"]
        ach = str(g[1].get("achterstand") or "").replace("+ ", "")
        zinnen.append(f'In het algemeen klassement leidt {g[0]["naam"]}, '
                      f'met {g[1]["naam"]} op {ach}.')
    return " ".join(zinnen)


def claude_samenvatting(f):
    """Laat Claude het raceverloop samenvatten. Geeft None terug zonder API-sleutel
    of bij fouten, zodat het vaste patroon het overneemt."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        prompt = (
            "Je bent een enthousiaste Nederlandse wielercommentator bij de Tour de France 2026. "
            "Schrijf een korte terugblik van drie of vier zinnen op het verloop van de zojuist verreden etappe. "
            "Gebruik uitsluitend de feiten hieronder en verzin niets. Noem de winnaar en hoe hij won "
            "(solo, uit een kopgroep, in een sprint, afgaand op de tijdsverschillen op het podium), "
            "iets over het klassement en eventuele truiwissels. "
            "Schrijf lopende tekst zonder opsommingen, zonder kopjes en zonder gedachtestreepjes. "
            "Antwoord met alleen die tekst.\n\nFeiten (JSON):\n" + json.dumps(f, ensure_ascii=False)
        )
        resp = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.stop_reason == "refusal":
            return None
        tekst = next((b.text for b in resp.content if b.type == "text"), "").strip()
        return tekst or None
    except Exception as e:
        print(f"  Claude-samenvatting mislukt, vast patroon gebruikt: {e}")
        return None


def main():
    # stand.json inlezen; bij een beschadigd bestand stoppen zonder iets te
    # overschrijven, zodat een kapotte versie nooit wordt gepubliceerd
    if STAND_PAD.exists():
        try:
            stand = json.loads(STAND_PAD.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"stand.json onleesbaar ({e}); geen wijzigingen doorgevoerd.")
            return 0
    else:
        stand = {}
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
        if bestaand and bestaand.get("bron") == "en.wikipedia.org" and datum < vandaag:
            continue  # eerdere etappe al via Wikipedia binnen; niet blijven herscrapen
        # (letour-uitslagen worden bewust wél opnieuw gecontroleerd tegen Wikipedia)
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

    # 2) de vier klassementen: tussenstanden op de hoofdpagina
    KLASSEMENT_DEFS = {
        "geel":   (r"General classification after stage (\d+)", "tijd"),
        "groen":  (r"Points classification after stage (\d+)", "punten"),
        "bollen": (r"Mountains classification after stage (\d+)", "punten"),
        "wit":    (r"Young rider classification after stage (\d+)", "tijd"),
    }

    def fmt_rijen(rijen, eenheid):
        uit = []
        for r in rijen[:10]:
            if eenheid == "punten":
                waarde = f"{r['pnt']} ptn" if r.get("pnt") is not None else "—"
            else:
                waarde = "—" if r["pos"] == 1 else (r["tijd"] or "—")
            uit.append({"pos": r["pos"], "naam": r["naam"], "land": r["land"],
                        "ploeg": r["ploeg"], "tijd": waarde})
        return uit

    def zoek_klassement(soup, patroon, min_rijen=3):
        beste = (0, None)
        for tabel in soup.find_all("table", class_="wikitable"):
            cap = tabel.find("caption")
            if not cap:
                continue
            m = re.search(patroon, cap.get_text(" ", strip=True), re.I)
            if m and int(m.group(1)) > beste[0]:
                rijen = parse_result_tabel(tabel)
                if len(rijen) >= min_rijen:
                    beste = (int(m.group(1)), rijen)
        return beste

    hoofd_html = haal(PAGINAS["hoofd"])
    hoofd_soup = BeautifulSoup(hoofd_html, "html.parser") if hoofd_html else None
    klassementen, beste_n = {}, 0
    if hoofd_soup:
        for trui, (patroon, eenheid) in KLASSEMENT_DEFS.items():
            n_kl, rijen = zoek_klassement(hoofd_soup, patroon)
            if rijen:
                onthoud(rijen)
                klassementen[trui] = {"naEtappe": n_kl, "eenheid": eenheid,
                                      "top10": fmt_rijen(rijen, eenheid)}
                beste_n = max(beste_n, n_kl)

    # fallback voor geel: de GC-tabel op de etappepagina's
    if "geel" not in klassementen:
        for sleutel_pag in ("12-21", "1-11"):
            soup = pagina(sleutel_pag)
            if soup is None:
                continue
            n_kl, rijen = zoek_klassement(soup, KLASSEMENT_DEFS["geel"][0], min_rijen=5)
            if rijen:
                onthoud(rijen)
                klassementen["geel"] = {"naEtappe": n_kl, "eenheid": "tijd",
                                        "top10": fmt_rijen(rijen, "tijd")}
                beste_n = max(beste_n, n_kl)
                break

    if klassementen.get("geel"):
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

        gc_top = klassementen["geel"]["top10"]
        truien = {"geel": trui_entry(gc_top[0]["naam"])}
        # de nummers 1 van de andere klassementen zijn meestal ook de dragers
        extra = truien_uit_hoofdpagina(hoofd_html, beste_n) if hoofd_html else None
        if not extra:
            extra = {t: klassementen[t]["top10"][0]["naam"] for t in ("groen", "bollen", "wit")
                     if klassementen.get(t)}
        if extra:
            truien.update({trui: trui_entry(naam) for trui, naam in extra.items()})
        else:
            for trui in ("groen", "bollen", "wit"):
                if oud.get("truien", {}).get(trui):
                    truien[trui] = oud["truien"][trui]
        MND = {1: "januari", 2: "februari", 3: "maart", 4: "april", 5: "mei", 6: "juni",
               7: "juli", 8: "augustus", 9: "september", 10: "oktober", 11: "november", 12: "december"}
        # truiwissels t.o.v. de vorige stand (voer voor het dagcommentaar)
        wissels = []
        for trui in ("geel", "groen", "bollen", "wit"):
            oud_v = (oud.get("truien") or {}).get(trui)
            oud_naam = oud_v.get("naam") if isinstance(oud_v, dict) else oud_v
            nieuw_naam = (truien.get(trui) or {}).get("naam")
            if oud_naam and nieuw_naam and not zelfde_renner(oud_naam, nieuw_naam):
                wissels.append({"trui": trui, "van": oud_naam, "naar": nieuw_naam})
        if oud.get("naEtappe") == beste_n and not wissels:
            wissels = [w for w in (oud.get("wissels") or [])
                       if not zelfde_renner(w.get("van"), w.get("naar"))]
        nieuw_klassement = {
            "naEtappe": beste_n,
            "truien": truien,
            "top10": gc_top,
            "klassementen": klassementen,
            "wissels": wissels,
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

    # 2b) letour.fr als primaire bron: verse rituitslag, klassementen en live
    #     wegroepen (aangeleverd door capture_live.js). Overschrijft de
    #     Wikipedia-data zodra beschikbaar; Wikipedia blijft het vangnet.
    live_pad = Path(__file__).resolve().parent / "live_out.json"
    live = None
    if live_pad.exists():
        try:
            live = json.loads(live_pad.read_text(encoding="utf-8"))
        except Exception:
            live = None

    if live and live.get("etappe"):
        n = live["etappe"]
        sl = str(n)

        # LET OP: de officiële RITUITSLAG (podium) komt bewust NIET meer van de
        # live letour-aankomstdata. Die bleek onbetrouwbaar voor het eindresultaat
        # (o.a. verkeerde sprintvolgorde in Bordeaux), dus Wikipedia is weer de
        # gezaghebbende bron voor de rituitslag. Het algemeen klassement (GC, op
        # cumulatieve tijd) is wél betrouwbaar en actueel via letour, dus dat
        # houden we; Wikipedia blijft daar het vangnet voor.

        # de vier klassementen van letour: alleen als de rit klaar is
        kl = live.get("klassementen")
        if live.get("klaar") and kl:
            oud = stand.get("klassement") or {}
            truien, klassementen = {}, {}
            for trui in ("geel", "groen", "bollen", "wit"):
                blok = kl.get(trui)
                if blok and blok.get("top10"):
                    leider = blok["top10"][0]
                    truien[trui] = {"naam": leider["naam"], "kort": korte_naam(leider["naam"]),
                                    "land": leider.get("land"), "ploeg": leider.get("ploeg") or ""}
                    klassementen[trui] = {"naEtappe": n, "eenheid": blok.get("eenheid"),
                                          "top10": blok["top10"]}
                elif (oud.get("truien") or {}).get(trui):
                    truien[trui] = oud["truien"][trui]
            gc_top = (klassementen.get("geel") or {}).get("top10") or []
            wissels = []
            for trui in ("geel", "groen", "bollen", "wit"):
                oud_v = (oud.get("truien") or {}).get(trui)
                oud_naam = oud_v.get("naam") if isinstance(oud_v, dict) else oud_v
                nieuw_naam = (truien.get(trui) or {}).get("naam")
                if oud_naam and nieuw_naam and not zelfde_renner(oud_naam, nieuw_naam):
                    wissels.append({"trui": trui, "van": oud_naam, "naar": nieuw_naam})
            if oud.get("naEtappe") == n and not wissels:
                wissels = [w for w in (oud.get("wissels") or [])
                           if not zelfde_renner(w.get("van"), w.get("naar"))]
            nieuw_kl = {
                "naEtappe": n, "truien": truien, "top10": gc_top,
                "klassementen": klassementen, "wissels": wissels,
                "voetnoot": f"Stand na etappe {n}, automatisch bijgewerkt "
                            f"{nu.day} {MAAND[nu.month]} {nu.strftime('%H:%M')} uur (bron: letour.fr).",
            }
            # nooit terugvallen naar een eerdere etappe
            if gc_top and (oud.get("naEtappe") or 0) <= n and \
               {k: v for k, v in oud.items() if k != "voetnoot"} != \
               {k: v for k, v in nieuw_kl.items() if k != "voetnoot"}:
                stand["klassement"] = nieuw_kl
                gewijzigd = True
                print(f"Klassement van letour.fr na etappe {n}; geel: "
                      f"{(truien.get('geel') or {}).get('kort')}")

        # live wegroepen: alleen tijdens de koers
        koers = live.get("koers")
        if koers and koers.get("groepen"):
            if stand.get("koers") != koers:
                stand["koers"] = koers
                gewijzigd = True
                print(f"Koerssituatie: etappe {koers.get('etappe')}, "
                      f"{len(koers['groepen'])} groepen")
        elif stand.get("koers"):
            del stand["koers"]
            gewijzigd = True
            print("Koerssituatie verwijderd (geen rijdende koers)")
    elif stand.get("koers"):
        del stand["koers"]
        gewijzigd = True
        print("Koerssituatie verwijderd (geen livedata)")

    # 2c) rennerprofielen (data/renners.json): posities, ploeg, leeftijd,
    #     carrièrecijfers en de beste rituitslag tot nu toe
    renners_pad = STAND_PAD.parent / "renners.json"
    if live and live.get("renners"):
        renners = live["renners"]
        import unicodedata

        def _norm(s):
            s = unicodedata.normalize("NFKD", s or "")
            return " ".join("".join(c for c in s if not unicodedata.combining(c)).lower().split())

        naam2bib = {_norm(r.get("naam")): b for b, r in renners.items()}
        beste = {}
        for sl, u in stand.get("uitslagen", {}).items():
            try:
                et = int(sl)
            except ValueError:
                continue
            for p in (u.get("pod") or []):
                b = naam2bib.get(_norm(p.get("naam")))
                pos = p.get("pos")
                if b is None or not isinstance(pos, int) or pos < 1:
                    continue
                cur = beste.get(b)
                if cur is None or pos < cur[0] or (pos == cur[0] and et > cur[1]):
                    beste[b] = (pos, et)
        for b, (pos, et) in beste.items():
            renners[b]["beste"] = {"pos": pos, "etappe": et}
        nieuw_r = json.dumps(renners, ensure_ascii=False, sort_keys=True)
        oud_r = ""
        if renners_pad.exists():
            try:
                oud_r = json.dumps(json.loads(renners_pad.read_text(encoding="utf-8")),
                                   ensure_ascii=False, sort_keys=True)
            except Exception:
                oud_r = ""
        if nieuw_r != oud_r:
            renners_pad.write_text(json.dumps(renners, ensure_ascii=False) + "\n", encoding="utf-8")
            gewijzigd = True
            print(f"renners.json bijgewerkt ({len(renners)} renners)")

    # 3) dagcommentaar voor de eerstvolgende rit; vernieuwt zodra de feiten wijzigen
    feiten = maak_feiten(stand)
    if feiten:
        vingerafdruk = hashlib.sha256(
            json.dumps(feiten, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        if (stand.get("dagcommentaar") or {}).get("feiten") != vingerafdruk:
            tekst = claude_commentaar(feiten)
            bron = "claude-opus-4-8" if tekst else "vast patroon"
            stand["dagcommentaar"] = {
                "voorEtappe": feiten["volgende"]["n"],
                "tekst": tekst or regels_commentaar(feiten),
                "bron": bron,
                "feiten": vingerafdruk,
            }
            gewijzigd = True
            print(f"Dagcommentaar vernieuwd voor etappe {feiten['volgende']['n']} ({bron})")

    # 4) terugblik op de laatst verreden etappe; vernieuwt zodra de feiten wijzigen
    laatste = max((int(sl) for sl, u in stand.get("uitslagen", {}).items()
                   if (u or {}).get("pod")), default=None)
    if laatste is not None:
        f_sam = maak_feiten_samenvatting(stand, laatste)
        if f_sam:
            vinger = hashlib.sha256(
                json.dumps(f_sam, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
            stand.setdefault("samenvattingen", {})
            bestaand = stand["samenvattingen"].get(str(laatste))
            if not bestaand or bestaand.get("feiten") != vinger:
                tekst = claude_samenvatting(f_sam)
                bron = "claude-opus-4-8" if tekst else "vast patroon"
                stand["samenvattingen"][str(laatste)] = {
                    "tekst": tekst or regels_samenvatting(f_sam),
                    "bron": bron, "feiten": vinger,
                }
                gewijzigd = True
                print(f"Samenvatting vernieuwd voor etappe {laatste} ({bron})")

    if gewijzigd:
        stand["bijgewerkt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        STAND_PAD.write_text(json.dumps(stand, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("data/stand.json weggeschreven.")
    else:
        print("Geen wijzigingen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
