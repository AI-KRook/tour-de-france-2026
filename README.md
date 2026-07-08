# Tour de France 2026 · Routeverkenner

Interactieve webapp die de Tour de France 2026 volgt: parcourskaart, etappeprofielen, uitslagen, klassement en een geschatte live-positie van het peloton.

**Live:** zie de GitHub Pages-URL van deze repo.

## Automatisch bijhouden

Alles wordt automatisch bijgewerkt via GitHub Actions (`.github/workflows/update.yml`):

- **Uitslagen, klassement en truien**: `scripts/update_stand.py` haalt na elke etappe de top 10, de GC-top 10 en de vier truiendragers op van procyclingstats.com en schrijft ze naar `data/stand.json`. Draait elk kwartier tijdens koersuren (12:00 tot 19:00 UTC in juli) plus dagelijks om 05:00 UTC.
- **Exacte routes**: `scripts/fetch_routes.py` downloadt de officiële GPX-bestanden (letour.fr / ASO via cyclingstage.com) en zet ze om naar `data/routes.json`. Etappes zonder GPX krijgen een indicatieve lijn totdat het bestand beschikbaar is.
- **Publicatie**: elke run publiceert de site opnieuw naar GitHub Pages.

De app zelf ververst de data elke 10 minuten in de browser en herberekent de geschatte pelotonpositie elke minuut.

## Handmatig ingrijpen

- Een etappe-uitslag in `data/stand.json` met `"lock": true` wordt nooit door het script overschreven (handig voor handmatig verrijkte teksten, zoals de ploegentijdrit).
- Workflow handmatig draaien: Actions → "Data bijwerken en site publiceren" → Run workflow.
- GPX-bestanden kunnen ook handmatig in de app geïmporteerd worden (knop onderaan); die worden in de browser bewaard (localStorage).

## Bronnen

Uitslagen en klassement: procyclingstats.com · Routes: letour.fr / ASO via cyclingstage.com · Kaart: © OpenStreetMap-bijdragers · Kaartweergave: Leaflet · Grafieken: Chart.js
