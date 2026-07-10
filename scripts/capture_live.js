// Vangt de live wedstrijddata van racecenter.letour.fr en schrijft die naar
// scripts/live_out.json: de wegroepen tijdens de koers, de rituitslag en de
// vier klassementen (geel/groen/bollen/wit).
//
// De site tekent een tijdgebonden, HMAC-gesigneerd token (xdt) in elke
// API-url. Dat token laten we de pagina zelf genereren: we openen de site in
// een headless browser en onderscheppen de JSON-antwoorden van:
//   /api/pack-2026-<etappe>          wegroepen met tijdsgaten
//   /api/allCompetitors-2026         bib/hash -> naam, nationaliteit, ploeg
//   /api/rankingType-2026-<etappe>   alle klassementen (itg/ipg/img/ijg)
//   /api/rankingTypeArrival-2026-<n> aankomstvolgorde per doorkomst
// Zo blijft het werken ook als ASO het tokengeheim wisselt.

const fs = require("fs");
const path = require("path");
const UIT = path.join(__dirname, "live_out.json");

// ---------- tekst-helpers ----------
function titel(woord) {
  return (woord || "")
    .toLowerCase()
    .split(/([ \-'])/)
    .map((d) => (/[ \-']/.test(d) ? d : d.charAt(0).toUpperCase() + d.slice(1)))
    .join("");
}
function volNaam(r) {
  const voor = titel((r.firstname || "").trim());
  const achter = titel((r.lastname || "").trim());
  return `${voor} ${achter}`.trim();
}
function kortNaam(r) {
  const init = (r.firstname || "").trim().charAt(0).toUpperCase();
  const achter = titel((r.lastname || "").trim());
  return init ? `${init}. ${achter}` : achter;
}
function ploegNet(naam) {
  // "LIDL-TREK" -> "Lidl-Trek", "UAE TEAM EMIRATES XRG" -> "UAE Team Emirates XRG"
  return (naam || "")
    .split(" ")
    .map((w) => (w.length <= 3 && w === w.toUpperCase() ? w : titel(w)))
    .join(" ");
}

function fmtTijd(ms) {
  // gap in ms -> "+ m:ss" of "+ h:mm:ss"; 0 -> "—"
  const s = Math.round((ms || 0) / 1000);
  if (s <= 0) return "—";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const mmss = `${m}:${String(sec).padStart(2, "0")}`;
  return h ? `+ ${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `+ ${mmss}`;
}
function fmtGatKort(sec) {
  sec = Math.round(sec || 0);
  if (sec <= 0) return "";
  const m = Math.floor(sec / 60), s = sec % 60;
  return m ? `+${m}:${String(s).padStart(2, "0")}` : `+${s}s`;
}
function vertaalGroep(naam) {
  return (naam || "")
    .replace(/^Tête de la course$/i, "Kopgroep")
    .replace(/^Peloton$/i, "Peloton")
    .replace(/^Gr\.\s*/i, "Groep ")
    .replace(/Maillot Jaune/i, "gele trui")
    .replace(/Maillot Vert/i, "groene trui")
    .replace(/Maillot à Pois/i, "bollentrui")
    .replace(/Maillot Blanc/i, "witte trui");
}

// ---------- rennerindex uit allCompetitors ----------
function bouwIndex(compArr, codeNaam) {
  const perBib = {}, perHash = {};
  for (const r of compArr || []) {
    if (!r || (r.lastname == null && r.firstname == null)) continue;
    const code = (r._origin || "").split("-").pop();
    const info = {
      naam: volNaam(r),
      kort: kortNaam(r),
      land: (r.nationality || "").toUpperCase(),
      ploeg: ploegNet(codeNaam[code] || ""),
    };
    if (r.bib != null) perBib[r.bib] = info;
    if (r._id) perHash[r._id] = info;
  }
  return { perBib, perHash };
}
function vindRenner(idx, r) {
  const h = (r.$rider || "").split(":").pop();
  if (h && idx.perHash[h]) return idx.perHash[h];
  if (r.bib != null && idx.perBib[r.bib]) return idx.perBib[r.bib];
  return null;
}

// ---------- klassementen ----------
// type-codes bij letour: itg=geel(GC), ipg=groen(punten), img=bollen(berg),
// ijg=wit(jongeren); de rituitslag zit in de aankomst (rankingTypeArrival).
const TRUI_TYPE = { geel: "itg", groen: "ipg", bollen: "img", wit: "ijg" };
const EENHEID = { geel: "tijd", groen: "punten", bollen: "punten", wit: "tijd" };

function top10Uit(rankings, idx, eenheid) {
  const geldig = (rankings || []).filter((r) => (r.position || 0) >= 1);
  geldig.sort((a, b) => a.position - b.position);
  const uit = [];
  for (const r of geldig.slice(0, 10)) {
    const who = vindRenner(idx, r);
    if (!who) continue;
    const waarde = eenheid === "punten"
      ? `${r.absolute || 0} ptn`
      : (r.position === 1 ? "—" : fmtTijd(r.relative));
    uit.push({ pos: r.position, naam: who.naam, land: who.land, ploeg: who.ploeg, tijd: waarde });
  }
  return uit;
}

function buildKlassementen(rankArr, idx) {
  const perType = {};
  for (const it of rankArr || []) {
    if (it && it.type && Array.isArray(it.rankings) && it.rankings.length && !(it.type in perType)) {
      perType[it.type] = it.rankings;
    }
  }
  const out = {};
  for (const [trui, code] of Object.entries(TRUI_TYPE)) {
    const rk = perType[code];
    if (!rk) continue;
    const top10 = top10Uit(rk, idx, EENHEID[trui]);
    if (top10.length) out[trui] = { eenheid: EENHEID[trui], top10 };
  }
  return Object.keys(out).length ? out : null;
}

// ---------- rennerprofielen ----------
function leeftijd(birthISO, isoNu) {
  if (!birthISO) return null;
  const b = new Date(birthISO), n = new Date(isoNu);
  if (isNaN(b)) return null;
  let a = n.getFullYear() - b.getFullYear();
  const m = n.getMonth() - b.getMonth();
  if (m < 0 || (m === 0 && n.getDate() < b.getDate())) a--;
  return a > 0 && a < 120 ? a : null;
}
// bib -> {pos, waarde} voor één klassement
function posMapUit(rankArr, code, eenheid) {
  let rk = null;
  for (const it of rankArr || []) {
    if (it && it.type === code && Array.isArray(it.rankings) && it.rankings.length) { rk = it.rankings; break; }
  }
  const m = {};
  for (const r of rk || []) {
    if ((r.position || 0) >= 1 && r.bib != null) {
      m[r.bib] = { pos: r.position, waarde: eenheid === "punten"
        ? `${r.absolute || 0} ptn` : (r.position === 1 ? "—" : fmtTijd(r.relative)) };
    }
  }
  return m;
}
function buildRenners(compArr, rankArr, codeNaam, isoNu) {
  if (!Array.isArray(compArr) || !compArr.length) return null;
  const pos = {
    geel: posMapUit(rankArr, "itg", "tijd"), groen: posMapUit(rankArr, "ipg", "punten"),
    bollen: posMapUit(rankArr, "img", "punten"), wit: posMapUit(rankArr, "ijg", "tijd"),
  };
  const out = {};
  for (const r of compArr) {
    if (!r || r.bib == null || (r.lastname == null && r.firstname == null)) continue;
    const code = (r._origin || "").split("-").pop();
    const rec = {
      bib: r.bib, naam: volNaam(r), kort: kortNaam(r), land: (r.nationality || "").toUpperCase(),
      ploeg: ploegNet(codeNaam[code] || ""), leeftijd: leeftijd(r.birthdate, isoNu),
      zeges: r.victories || 0, podia: r.podiums || 0, foto: r.profile_sm || r.profile || "",
    };
    for (const t of ["geel", "groen", "bollen", "wit"]) if (pos[t][r.bib]) rec[t] = pos[t][r.bib];
    out[r.bib] = rec;
  }
  return Object.keys(out).length ? out : null;
}

// rituitslag uit de aankomst (rankingTypeArrival, finish-doorkomst)
function buildUitslag(arrivalArr, idx) {
  if (!Array.isArray(arrivalArr) || !arrivalArr.length) return null;
  // finish = doorkomst met de grootste checkpoint-waarde
  const fin = arrivalArr.reduce((a, b) => ((b.checkpoint || 0) > (a.checkpoint || 0) ? b : a));
  const rk = (fin.rankings || []).filter((r) => (r.position || 0) >= 1);
  if (rk.length < 1) return null;
  rk.sort((a, b) => a.position - b.position);
  const pod = [];
  for (const r of rk.slice(0, 10)) {
    const who = vindRenner(idx, r);
    if (!who) continue;
    pod.push({
      pos: r.position, naam: who.naam, land: who.land, ploeg: who.ploeg,
      tijd: r.position === 1 ? "" : fmtTijd(r.relative),
    });
  }
  if (!pod.length || pod[0].pos !== 1) return null;
  const w = pod[0];
  return { w: w.naam, wLand: w.land, wPloeg: w.ploeg, note: "", pod, bron: "letour.fr" };
}

// wegroepen uit pack
function buildKoers(packArr, idx, etappe, isoNu) {
  if (!Array.isArray(packArr) || !packArr.length) return null;
  const groepen0 = packArr[0].groups || [];
  if (!groepen0.length) return null;
  const gesorteerd = [...groepen0].sort((a, b) => (a.order || 0) - (b.order || 0));
  const leider = gesorteerd[0];
  const rest = leider.computedRemainingDistance ?? leider.remainingDistance ?? 0;
  if (rest <= 0) return null; // koers voorbij -> geen live groepen
  const groepen = gesorteerd.map((g) => {
    const bibs = (g.bibs || []).map((b) => b.bib);
    const renners = bibs.map((b) => { const w = idx.perBib[b]; return w ? [w.kort, w.land, b] : null; })
      .filter(Boolean).slice(0, 8);
    const meer = bibs.length - renners.length;
    return {
      naam: vertaalGroep(g.name),
      aantal: g.size || bibs.length || 0,
      gat: fmtGatKort(g.computedRelative ?? g.relative ?? 0), // pack-gaten zijn in seconden
      renners, meer: meer > 0 ? meer : 0,
    };
  });
  return { etappe, bijgewerkt: isoNu, groepen, bron: "racecenter.letour.fr" };
}

// koers voorbij? leider heeft niets meer te rijden
function koersKlaar(packArr) {
  if (!Array.isArray(packArr) || !packArr.length) return false;
  const g = [...(packArr[0].groups || [])].sort((a, b) => (a.order || 0) - (b.order || 0))[0];
  if (!g) return false;
  return (g.computedRemainingDistance ?? g.remainingDistance ?? 1) <= 0;
}

function bouwAlles(data, etappe, isoNu) {
  // uitslag en klassement moeten van EXACT de huidige etappe komen; de pagina
  // laadt soms ook naburige etappes. Ontbreekt de data van deze etappe, dan
  // liever niets bijwerken dan de stand van een andere etappe tonen.
  const rank = (data.rankByStage || {})[etappe] || null;
  const arrival = (data.arrivalByStage || {})[etappe] || null;
  const codeNaam = {};
  for (const it of rank || []) if (it && it.code && it.name) codeNaam[it.code] = it.name;
  const idx = bouwIndex(data.comp, codeNaam);
  return {
    etappe,
    bijgewerkt: isoNu,
    klaar: koersKlaar(data.pack),
    koers: buildKoers(data.pack, idx, etappe, isoNu),
    uitslag: buildUitslag(arrival, idx),
    klassementen: buildKlassementen(rank, idx),
    renners: buildRenners(data.comp, rank, codeNaam, isoNu),
  };
}

async function main() {
  const { chromium } = require("playwright");
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const data = { pack: null, comp: null, rankByStage: {}, arrivalByStage: {} };
  let stage = null;
  page.on("response", async (r) => {
    const url = r.url();
    try {
      let m;
      if ((m = url.match(/\/api\/pack-2026-(\d+)/))) { stage = parseInt(m[1], 10); data.pack = await r.json(); }
      else if (url.includes("/api/allCompetitors-2026")) data.comp = await r.json();
      else if ((m = url.match(/\/api\/rankingTypeArrival-2026-(\d+)/))) data.arrivalByStage[+m[1]] = await r.json();
      else if ((m = url.match(/\/api\/rankingType-2026-(\d+)/))) data.rankByStage[+m[1]] = await r.json();
    } catch (e) { /* niet-JSON negeren */ }
  });
  await page.goto("https://racecenter.letour.fr/nl", { waitUntil: "networkidle", timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(18000);
  await browser.close();

  const etappe = parseInt(process.env.KOERS_ETAPPE || stage || "0", 10) || null;
  const nu = process.env.KOERS_NU || new Date().toISOString();
  const res = bouwAlles(data, etappe, nu);
  const heeftIets = res.uitslag || res.klassementen || res.koers || res.renners;
  if (heeftIets) {
    fs.writeFileSync(UIT, JSON.stringify(res));
    console.log(`live weggeschreven: etappe ${res.etappe}, klaar=${res.klaar}, ` +
      `uitslag=${!!res.uitslag}, klassementen=${res.klassementen ? Object.keys(res.klassementen).join("/") : "geen"}, ` +
      `koers=${res.koers ? res.koers.groepen.length + " groepen" : "geen"}`);
  } else {
    if (fs.existsSync(UIT)) fs.unlinkSync(UIT);
    console.log("geen bruikbare livedata; niets weggeschreven");
  }
}

module.exports = { bouwAlles, buildUitslag, buildKlassementen, buildKoers, buildRenners, bouwIndex, fmtTijd, ploegNet };

if (require.main === module) {
  main().catch((e) => { console.error(e); process.exit(0); });
}
