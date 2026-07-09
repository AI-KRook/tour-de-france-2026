// Vangt de live koerssituatie (wegroepen + tijdsverschillen) van
// racecenter.letour.fr en schrijft die naar scripts/koers_out.json.
//
// De site tekent een tijdgebonden, HMAC-gesigneerd token (xdt) in elke
// API-url. Dat token laten we de pagina zelf genereren: we openen de site
// in een headless browser en onderscheppen de JSON-antwoorden van
//   /api/pack-2026-<etappe>      (de wegroepen met tijdsgaten)
//   /api/allCompetitors-2026     (bib -> naam + nationaliteit)
// Zo blijft het werken ook als ASO het tokengeheim wisselt.

const fs = require("fs");
const path = require("path");

const UIT = path.join(__dirname, "koers_out.json");

// "Tiesj BENOOT" -> "T. Benoot"; "Jonas VINGEGAARD HANSEN" -> "J. Vingegaard Hansen"
function korteNaam(voor, achter) {
  const net = (achter || "")
    .trim()
    .toLowerCase()
    .split(/([ -])/)
    .map((d) => (/[ -]/.test(d) ? d : d.charAt(0).toUpperCase() + d.slice(1)))
    .join("");
  const init = (voor || "").trim().charAt(0).toUpperCase();
  return init ? `${init}. ${net}` : net;
}

function fmtGat(sec) {
  sec = Math.round(sec);
  if (!sec || sec <= 0) return "";
  const m = Math.floor(sec / 60), s = sec % 60;
  return m ? `+${m}:${String(s).padStart(2, "0")}` : `+${s}s`;
}

function vertaalNaam(naam) {
  return (naam || "")
    .replace(/^Tête de la course$/i, "Kopgroep")
    .replace(/^Peloton$/i, "Peloton")
    .replace(/^Gr\.\s*/i, "Groep ")
    .replace(/Maillot Jaune/i, "gele trui")
    .replace(/Maillot Vert/i, "groene trui")
    .replace(/Maillot à Pois/i, "bollentrui")
    .replace(/Maillot Blanc/i, "witte trui");
}

// Bouwt het compacte koers-object uit de twee API-antwoorden.
// Geeft null terug als er geen bruikbare, nog rijdende koers is.
function buildKoers(packArr, compArr, isoNu) {
  if (!Array.isArray(packArr) || !packArr.length) return null;
  const blok = packArr[0];
  const groepen0 = blok.groups || [];
  if (!groepen0.length) return null;

  // etappenummer uit _origin/_bind of uit stageType-context halen lukt niet
  // betrouwbaar; de aanroeper geeft het etappenummer mee via env.
  const etappe = parseInt(process.env.KOERS_ETAPPE || "0", 10) || null;

  // bib -> renner
  const perBib = {};
  for (const r of compArr || []) {
    if (r && r.bib != null) {
      perBib[r.bib] = {
        naam: korteNaam(r.firstname, r.lastname),
        land: (r.nationality || "").toUpperCase(),
      };
    }
  }

  const gesorteerd = [...groepen0].sort((a, b) => (a.order || 0) - (b.order || 0));

  // koers voorbij? leider heeft niets meer te rijden -> niet tonen
  const leider = gesorteerd[0];
  const restLeider = leider.computedRemainingDistance ?? leider.remainingDistance ?? 0;
  if (restLeider <= 0) return null;

  const groepen = gesorteerd.map((g) => {
    const bibs = (g.bibs || []).map((b) => b.bib);
    const renners = bibs
      .map((bib) => perBib[bib])
      .filter(Boolean)
      .slice(0, 8)
      .map((r) => [r.naam, r.land]);
    const meer = bibs.length - renners.length;
    return {
      naam: vertaalNaam(g.name),
      aantal: g.size || bibs.length || 0,
      gat: fmtGat(g.computedRelative ?? g.relative ?? 0),
      renners,
      meer: meer > 0 ? meer : 0,
    };
  });

  return {
    etappe,
    bijgewerkt: isoNu,
    groepen,
    bron: "racecenter.letour.fr",
  };
}

async function main() {
  const { chromium } = require("playwright");
  const browser = await chromium.launch();
  const page = await browser.newPage();
  let pack = null, comp = null, stageGezien = null;

  page.on("response", async (r) => {
    const url = r.url();
    try {
      const m = url.match(/\/api\/pack-2026-(\d+)/);
      if (m) { stageGezien = parseInt(m[1], 10); pack = await r.json(); }
      else if (url.includes("/api/allCompetitors-2026")) { comp = await r.json(); }
    } catch (e) { /* niet-JSON antwoord negeren */ }
  });

  await page.goto("https://racecenter.letour.fr/nl", { waitUntil: "networkidle", timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(15000);
  await browser.close();

  if (stageGezien && !process.env.KOERS_ETAPPE) process.env.KOERS_ETAPPE = String(stageGezien);
  const nu = process.env.KOERS_NU || new Date().toISOString();
  const koers = buildKoers(pack, comp, nu);
  if (koers) {
    fs.writeFileSync(UIT, JSON.stringify(koers));
    console.log(`koers weggeschreven: etappe ${koers.etappe}, ${koers.groepen.length} groepen`);
  } else {
    if (fs.existsSync(UIT)) fs.unlinkSync(UIT);
    console.log("geen rijdende koers; niets weggeschreven");
  }
}

module.exports = { buildKoers, fmtGat, korteNaam, vertaalNaam };

if (require.main === module) {
  main().catch((e) => { console.error(e); process.exit(0); });
}
