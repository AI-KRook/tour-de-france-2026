# Tour de France 2026 · Routeverkenner

Mobiele webapp die de Tour de France 2026 volgt: parcourskaart met exacte tracés, etappeprofielen, uitslagen, klassement, truiendragers en een geschatte live-positie van het peloton.

**Live:** https://ai-krook.github.io/tour-de-france-2026/

## Opbouw

- `index.html`: de complete app (mobiel-eerst, vier tabbladen: Vandaag, Etappes, Klassement, Uitslagen). Bij elke renner staan land (vlag) en ploeg.
- `data/routes.json`: de exacte routes en hoogteprofielen van alle 21 etappes (eenmalig gegenereerd uit de officiële GPX-bestanden van letour.fr / ASO).
- `data/stand.json`: uitslagen, klassement en truiendragers; wordt automatisch bijgewerkt.
- `manifest.webmanifest` + `icon.svg`: de app is op een telefoon te installeren via "Zet op beginscherm".

## Automatisch bijhouden

`.github/workflows/update.yml` draait elk kwartier tijdens koersuren (12:00 tot 19:00 UTC in juli) plus dagelijks om 05:00 UTC:

1. `scripts/update_stand.py` haalt van Wikipedia per etappe de top 10, de GC-top 10 en de vier truiendragers op (met nationaliteit en ploeg) en schrijft ze naar `data/stand.json`. Het script is idempotent en overschrijft nooit entries met `"lock": true`.
2. Wijzigingen worden gecommit en de site wordt opnieuw gepubliceerd via de `gh-pages`-branch.

De app zelf ververst de data elke 10 minuten in de browser en herberekent de geschatte pelotonpositie elke minuut.

## Bronnen

Uitslagen, klassement en truien: en.wikipedia.org · Routes: letour.fr / ASO · Kaart: © OpenStreetMap-bijdragers · Kaartweergave: Leaflet · Grafieken: Chart.js
