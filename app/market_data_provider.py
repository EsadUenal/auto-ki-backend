from __future__ import annotations

"""
MarketDataProvider — Schnittstelle fuer strukturierte Marktdatenquellen (Etappe 3).

ABGRENZUNG ZU `app/search_provider.py` (wichtig, sonst stehen zwei Abstraktionen
nebeneinander, ohne dass klar ist welche gilt):

  `SearchProvider.search(query, ...) -> (textseiten, fehler)` ist eine
  SUCHMASCHINEN-Schnittstelle: Query rein, Textseiten raus. Sie passt auf Tavily
  und auf jeden anderen Websuch-Dienst — aber auf KEINE Marktplatz-API. mobile.de
  bekommt kein Suchwort, sondern strukturierte Filter, und liefert keine Seiten,
  sondern Anzeigen-Objekte.

  `MarketDataProvider.find_comparables(identity, ...) -> (beobachtungen, fehler)`
  liegt eine Ebene HOEHER: Fahrzeug rein, Vergleichsbeobachtungen raus. Der Weg
  dahin (Websuche + Regex ODER API + Feldmapping) ist Sache der Implementierung.

DIE ENTSCHEIDENDE REGEL: Ein Provider liefert **UNBEWERTETE** Beobachtungen.

  Er entscheidet NICHT ueber Modelltreue, Motorvariante, Generation, Kraftstoff,
  Karosserie, Similarity, Median oder Quellenfreigabe. Das alles bleibt
  ausschliesslich in `app/marktvergleich._bewerte(b, ziel)` und den Funktionen
  drumherum — der in Etappe 1 geprueften Logik. Wuerde ein Provider auch nur
  einen dieser Schritte "schon mal mitmachen", gaebe es zwei Wahrheiten darueber,
  was ein gueltiges Vergleichsfahrzeug ist. Genau das soll die Schnittstelle
  verhindern.

  Praktische Konsequenz fuer Implementierungen: `_bewerte` liest seine Belege aus
  einem Evidenztext, den `app/marktvergleich._roh_beobachtung` in
  `gruende[0]` mit einem `\\x00`-Praefix ablegt. Ein Provider, dessen
  Beobachtungen von `_bewerte` geprueft werden sollen, MUSS diesen Text
  mitliefern (siehe `evidenztext`), sonst hat die Bewertung schlicht nichts zu
  lesen und faellt konservativ durch. Felder, die `_bewerte` selbst SETZT
  (`make`, `model`, `generation`, `engine_variant`), darf ein Provider NICHT
  vorbelegen — sie wuerden ueberschrieben und taeuschten eine Bestaetigung vor,
  die nie stattgefunden hat.
"""

from typing import Any, Protocol

from app.models import Preisbeobachtung
from app.vehicle_identity import VehicleIdentity

# Herkunftskennzeichnung fuer strukturierte API-Daten (Etappe 3).
#
# Das bestehende Vokabular kannte nur textbasierte Herkuenfte
# ("title"/"snippet"/"raw_content"/"window_fallback" bzw. "detail_link"/
# "block_structure"/"title_anchor"/"single_card"/"window_fallback"). Ein
# API-Datensatz ist keine davon: er wurde weder aus einem Snippet gelesen noch
# aus einer HTML-Seite segmentiert. Ihn als "raw_content"/"detail_link"
# auszugeben waere eine falsche Herkunftsangabe in genau dem Feld, das in
# Etappe 1 die Belegkette tragen soll.
EXTRACTION_SOURCE_API = "api"
SEGMENTATION_METHOD_API = "api_structured"


def evidenztext(*teile: Any) -> str:
    """Baut den Evidenztext, den `marktvergleich._bewerte` aus `gruende[0]` liest.

    Nur wirklich vorhandene Angaben werden aufgenommen — `None`/leere Werte
    fallen raus, damit kein Feld "belegt" aussieht, das die Quelle gar nicht
    geliefert hat. Der Text ist eine reine Serialisierung der Quelldaten, KEINE
    Anreicherung: es darf nichts hineingeschrieben werden, was die Quelle nicht
    selbst sagt (kein Zielfahrzeug, keine DB-Ableitung, keine Vermutung).
    """
    worte = [str(t).strip() for t in teile if t is not None and str(t).strip()]
    return " ".join(worte)


class MarketDataProvider(Protocol):
    """Austauschbare Quelle fuer Vergleichsfahrzeuge."""

    name: str

    async def find_comparables(
        self,
        identity: VehicleIdentity,
        *,
        limit: int = 20,
    ) -> tuple[list[Preisbeobachtung], bool]:
        """Vergleichsbeobachtungen zum Fahrzeug — UNBEWERTET.

        Rueckgabe `(beobachtungen, hatte_technischen_fehler)`. Die zweite
        Komponente hat dieselbe Semantik wie bei
        `app.web_search.tavily_search_mit_status`: `True` NUR bei echtem
        Netzwerk-/API-/Konfigurationsausfall, NICHT bei einer legitim leeren
        Trefferliste. Diese Trennung traegt in `marktrecherche.research_status`
        die Unterscheidung technical_failure vs. data_exhausted — ein Provider,
        der beides vermischt, macht daraus wieder eine Fehldiagnose.
        """
        ...


class FixtureProvider:
    """Deterministischer Provider fuer Tests und Replays — kein Netz, keine Secrets.

    Zweck ist ausdruecklich NICHT, Marktdaten zu simulieren, sondern die
    Provider-GRENZE testbar zu machen: dieselbe Schnittstelle, vorhersagbarer
    Inhalt. Damit laesst sich pruefen, dass die Etappe-1-Bewertung mit
    Provider-Beobachtungen genauso umgeht wie mit den bisherigen, ohne dafuer
    eine Sandbox, einen API-Key oder eine Netzverbindung zu brauchen.
    """

    def __init__(self, beobachtungen: list[Preisbeobachtung] | None = None, *,
                 name: str = "fixture", hatte_technischen_fehler: bool = False) -> None:
        self.name = name
        self._beobachtungen = list(beobachtungen or [])
        self._fehler = bool(hatte_technischen_fehler)
        # Diagnose: womit wurde der Provider zuletzt aufgerufen? Bewusst nur
        # mitgeschrieben, nie ausgewertet — ein Fixture darf sein Ergebnis nicht
        # von der Anfrage abhaengig machen, sonst testet man die Fixture-Logik.
        self.letzte_anfrage: tuple[VehicleIdentity | None, int] | None = None

    async def find_comparables(
        self,
        identity: VehicleIdentity,
        *,
        limit: int = 20,
    ) -> tuple[list[Preisbeobachtung], bool]:
        self.letzte_anfrage = (identity, limit)
        # Kopien zurueckgeben: `_bewerte` mutiert die Beobachtung in-place. Ohne
        # Kopie waere ein zweiter Lauf gegen dieselbe Fixture nicht mehr
        # deterministisch (der erste haette `vergleichbarkeit`/`gruende` bereits
        # ueberschrieben) — ein Replay wuerde dann je nach Aufrufreihenfolge
        # anders ausgehen.
        return [b.model_copy(deep=True) for b in self._beobachtungen[:limit]], self._fehler
