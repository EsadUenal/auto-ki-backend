from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Any

from app.autofinder_norm import KAROSSERIE_KLASSEN, GETRIEBE_KLASSEN
from app.autofinder import KRAFTSTOFF_WERTE, ANTRIEB_WERTE

# ---------- Input-Limits (Sicherheitsnetz gegen DOS/Kostenmissbrauch) ----------
# Ohne Obergrenzen kann ein einzelner Request beliebig große Strings/Listen an
# Gemini durchreichen (Kosten- und Latenzmissbrauch) oder den Server-Speicher
# mit einer riesigen JSON-Payload belasten. Werte großzügig genug für jede
# legitime Nutzung (Inserat-Volltext, langer Chat-Verlauf), aber endlich.
_MAX_TEXT_LEN      = 8_000       # einzelne Freitext-/Nachrichtenfelder
_MAX_BILD_B64_LEN  = 6_000_000   # Screenshot als Base64 (~4.5 MB Bilddatei)
_MAX_VERLAUF_LEN   = 100         # Anzahl Nachrichten im Chat-Verlauf pro Request


# ---------- Request ----------

class FahrzeugRequest(BaseModel):
    marke: str
    modell: str
    generation: str


class ChatMessage(BaseModel):
    rolle: str = Field(max_length=20)   # "user" | "ki"
    text: str = Field(max_length=_MAX_TEXT_LEN)


class ChatRequest(BaseModel):
    message: str = Field(max_length=_MAX_TEXT_LEN)
    verlauf: list[ChatMessage] = Field(default_factory=list, max_length=_MAX_VERLAUF_LEN)
    bild_base64: str | None = Field(default=None, max_length=_MAX_BILD_B64_LEN)
    stream: bool = True
    # Discover-Fast-Path: auf der Entdecken-Seite ist das Fahrzeug bereits gewählt.
    # Das Frontend übergibt es hier (z.B. "BMW 3er G20"), damit das Backend die
    # Baureihe deterministisch übernimmt statt sie bei jeder Nachricht neu aus dem
    # Text zu erraten. Per-Request übergeben -> KEIN geteilter Cache, kein Leak
    # zwischen Fahrzeugen/Nutzern.
    fahrzeug_kontext: str | None = Field(default=None, max_length=120)


class AnalyseFrageRequest(BaseModel):
    """Kontextgebundene Rückfrage zu einer bereits erstellten Check-Analyse.

    Der Analysetext (Bericht + Verdikt) wird direkt vom Frontend mitgeschickt —
    Checks werden nicht serverseitig persistiert, es gibt also keine analysis_id.
    Multi-Turn laeuft zustandslos ueber ``verlauf`` (bisherige Frage/Antwort-Paare).
    """
    analyse_kontext: str = Field(max_length=_MAX_TEXT_LEN)   # Analysetext als Kontext
    frage: str = Field(max_length=2_000)
    verlauf: list[ChatMessage] = Field(default_factory=list, max_length=_MAX_VERLAUF_LEN)
    check_typ: str = Field(default="kauf", max_length=20)     # "kauf" | "verkauf" | "ersatzteil"


# ---------- Response ----------

class FehlerDetail(BaseModel):
    code: str
    nachricht: str


class FehlerResponse(BaseModel):
    fehler: FehlerDetail


class ChatResponse(BaseModel):
    answer: str
    quelle: str          # "datenbank" | "web" | "gemischt"
    fahrzeug_referenz: list[str] = Field(default_factory=list)
    vertrauen: str       # "hoch" | "mittel" | "niedrig"
    belege: list[Any] = Field(default_factory=list)


# ---------- Kauf-Check ----------

class KaufCheckRequest(BaseModel):
    # Strukturierte Inserat-Felder
    marke: str | None = Field(default=None, max_length=100)
    modell: str | None = Field(default=None, max_length=100)
    baujahr: int | None = None
    kilometerstand: int | None = None
    motor: str | None = Field(default=None, max_length=200)   # z.B. "320d", "2.0 TDI 150 PS"
    kraftstoff: str | None = Field(default=None, max_length=100)
    preis_eur: int | None = None
    ausstattung: list[str] = Field(default_factory=list, max_length=100)
    beschreibung: str | None = Field(default=None, max_length=_MAX_TEXT_LEN)   # Freitext-Beschreibung aus dem Inserat

    # Zusätzliche Angaben — deutlich relevant für die Risikoeinschätzung
    unfallfrei: str | None = Field(default=None, max_length=20)      # "ja" | "nein" | "unbekannt"
    vorbesitzer: int | None = None     # Anzahl Vorbesitzer laut Inserat
    tuev_bis: str | None = Field(default=None, max_length=20)        # z.B. "06/2027"
    scheckheftgepflegt: bool | None = None

    # Alternativ: Volltext des Inserats (Copy-Paste von mobile.de / AutoScout)
    freitext: str | None = Field(default=None, max_length=_MAX_TEXT_LEN)

    # Optional: Screenshot des Inserats
    bild_base64: str | None = Field(default=None, max_length=_MAX_BILD_B64_LEN)


# ---------- Provenance / Evidence (Phase 1: Vertrauen & Nachvollziehbarkeit) ----------

class EvidenceQuelle(BaseModel):
    """Eine konkrete Quelle, die eine Erkenntnis (Insight) stützt.

    `typ` benennt die HERKUNFT und darf nur gesetzt werden, wenn diese Quelle die
    Aussage tatsächlich gestützt hat (keine Fake-Quellen):
    "datenbank" | "motorvarianten" | "schwachstellen" | "rueckruf_kba" |
    "web" | "marktvergleich" | "inserat" | "ki_ableitung"
    """
    typ: str
    ref: str | None = None            # z.B. KBA-Referenz, Bauteil, Baureihen-/Varianten-ID
    url: str | None = None
    titel: str | None = None
    qualitaet: str | None = None      # z.B. Web-Qualitätslabel ("Marktplatz", "Amtlich/Prüforganisation", ...)


class Preisbeobachtung(BaseModel):
    """Ein einzelner, aus einem Web-Treffer EXTRAHIERTER Preis-Datenpunkt
    (Marktvergleich 2.0).

    WICHTIG (Ehrlichkeit / keine Halluzination): Tavily liefert überwiegend
    Such-/Übersichtsseiten, KEINE sauberen Einzelinserate je URL. Deshalb ist dies
    bewusst KEIN vollständiges Fahrzeugobjekt mit eigener Inserats-URL, sondern eine
    aus dem Snippet-Text herausgelöste Preis-Beobachtung. Es wird ausschließlich
    gespeichert, was wirklich extrahierbar war (fehlende Felder bleiben None) — und
    `quelle_url` ist die RECHERCHE-Seite, aus deren Text der Datenpunkt stammt, nicht
    ein einzelnes Fahrzeuginserat.
    """
    preis_eur: int
    kilometerstand: int | None = None
    baujahr: int | None = None
    quelle_domain: str | None = None
    quelle_url: str | None = None
    vergleichbarkeit: str             # "sehr_aehnlich" | "aehnlich" | "bedingt" | "ungeeignet"
    # NACH der Bewertung: die menschenlesbaren Gruende fuer `vergleichbarkeit`.
    #
    # VOR der Bewertung nutzt `marktvergleich._bewerte` dasselbe Feld intern
    # anders: liegt in `gruende[0]` ein `\x00`-Praefix, ist das der ROHTEXT, aus
    # dem die Karosserie-/Motor-/Kraftstoff-Evidenz gelesen wird (gesetzt von
    # `_roh_beobachtung` fuer Websuch-Treffer, von `app/mobile_de_provider.py`
    # fuer API-Treffer). `_bewerte` UEBERSCHREIBT `gruende` am Ende immer mit
    # den echten Gruenden — der Rohtext verlaesst `_bewerte` nie und erreicht
    # nie Bericht/Frontend. Es ist die einzige Textquelle, die es dafuer gibt;
    # kein separates Rohtext-Feld existiert (Stand Etappe 3).
    gruende: list[str] = Field(default_factory=list)
    # Reliability-Sprint 3 (§10-§13): Herkunftsart der Recherche-Seite, aus der dieser
    # Datenpunkt extrahiert wurde — "listing" (konkretes Einzelinserat) | "category"
    # (Such-/Übersichtsseite) | "unknown" (nicht klassifizierbar). NUR "listing" zählt
    # in Richtung Quellenvielfalt/HIGH (siehe marktvergleich._datenqualitaet). Additiv,
    # Default "unknown" -> alte gespeicherte Checks bleiben ladbar.
    source_type: str = "unknown"

    # ── Marktanalyse-Sprint (§3): eine MarketObservation MUSS ein Fahrzeug sein ──
    # Alle Felder additiv mit Default -> alte gespeicherte Checks bleiben ladbar.
    #
    # `listing_key` ist der STABILE Dedup-Schlüssel (§4). Er wird in dieser
    # Reihenfolge gebildet: (1) Inserats-ID aus der URL, (2) kanonisierte
    # Detail-URL, (3) Fahrzeugdaten + Preis + Kilometer. Die dritte Stufe ist
    # bewusst grob genug, um dasselbe Inserat über mehrere Rechercheseiten hinweg
    # zusammenzuführen, und gleichzeitig fein genug, um zwei tatsächlich
    # verschiedene Fahrzeuge NICHT zu verschmelzen (Preis UND km UND Baujahr
    # müssten identisch sein).
    listing_key: str | None = None
    listing_id: str | None = None
    detail_url: str | None = None       # nur gesetzt, wenn die Quelle ein Einzelinserat ist
    # Fahrzeugidentität, soweit AUS DEM PREISUMFELD belegbar (nie geraten, nie vom
    # Zielfahrzeug übernommen — ein None heißt "im Text nicht nachweisbar").
    make: str | None = None
    model: str | None = None
    generation: str | None = None
    # Woher der Generationscode stammt:
    #   "explicit_card"      — die Fahrzeugkarte nennt ihn selbst
    #   "explicit_detail"    — die Detailseite des Inserats nennt ihn
    #   "inferred_database"  — aus der geprüften Chassiscode/Karosserie-Zuordnung
    #                          der Baureihenfamilie abgeleitet (app/chassis_codes.py)
    #   "unknown"            — nicht belegbar
    # Eine EXPLIZITE Angabe schlägt immer die Ableitung.
    generation_evidence: str = "unknown"
    generation_inference_reason: str | None = None
    body: str | None = None
    # Woher die Karosserie stammt — bewusst PARALLEL zu `generation_evidence`
    # geführt statt als neues Schema-Konstrukt:
    #   "card"            — der eigene, strukturell abgegrenzte Kartentext nennt sie
    #   "detail"          — die eigene Detailseite des Inserats nennt sie
    #   "page_context"    — NUR aus dem Kontext der Suchseite (URL-Filter wie
    #                       "autos.typ_s:limousine" oder Seitentitel). Bleibt als
    #                       Kontext erhalten, ist aber KEINE Listing-Identität.
    #   "window_fallback" — aus einem bloßen Zeichenfenster ohne Kartengrenze
    #   "unknown"         — nicht belegbar
    # Nur "card" und "detail" dürfen Generation-Inference und Karosserie-Bestätigung
    # tragen (siehe marktvergleich._identitaets_body).
    body_evidence: str = "unknown"
    fuel: str | None = None
    engine_variant: str | None = None   # Verkaufsbezeichnung im Text, z.B. "320d"
    horsepower: int | None = None
    transmission: str | None = None
    # Feinkörniger Ähnlichkeitswert 0.0-1.0 (Abstand zu Baujahr/km fließt ein) —
    # ergänzt die grobe Stufe in `vergleichbarkeit`, ersetzt sie nicht.
    similarity: float = 0.0
    # Woraus der Datenpunkt stammt: "title" | "snippet" | "raw_content" — oder
    # "window_fallback", wenn er nicht aus einer strukturell bestätigten Karte,
    # sondern nur aus einem Zeichenfenster um den Preis stammt.
    #
    # Etappe 3 ergänzt "api": der Datenpunkt stammt aus einem strukturierten
    # Feld einer Marktplatz-API (app/market_data_provider.py), wurde also weder
    # aus einem Snippet gelesen noch aus HTML segmentiert. Ihn stattdessen als
    # "raw_content" auszugeben wäre eine falsche Herkunftsangabe in genau dem
    # Feld, das die Belegkette tragen soll.
    extraction_source: str = "snippet"
    # ── Kartensegmentierung (app/market_card_segmenter.py) ───────────────────
    # Wie wurde die Fahrzeugkarte abgegrenzt, aus der dieser Datenpunkt stammt?
    # "detail_link" | "block_structure" | "title_anchor" | "single_card"
    # | "window_fallback". Nur die ersten vier sind strukturell bestätigt.
    #
    # Etappe 3 ergänzt "api_structured": bei einem API-Datensatz gab es gar
    # keinen Text zu segmentieren — ein Ad-Objekt IST bereits genau ein
    # Fahrzeug. Zählt als strukturell bestätigt.
    segmentation_method: str = "window_fallback"
    structural_confidence: str = "low"   # "high" | "medium" | "low"
    start_offset: int | None = None      # Kartengrenzen im zusammengesetzten Treffertext
    end_offset: int | None = None
    # True, wenn die Karte NICHT strukturell abgegrenzt werden konnte und das alte
    # Zeichenfenster einsprang. Solche Punkte sind höchstens "bedingt" (§1/§5) und
    # dürfen die Preisstatistik nie als hochwertiger Vergleich tragen.
    window_fallback_used: bool = True
    # Kurzbegründung der Annahme-/Ablehnungsentscheidung (Diagnose §13).
    acceptance_reason: str = ""


class Marktanalyse(BaseModel):
    """Deterministisch berechneter Marktvergleich (Median + robuste Spanne) aus den
    extrahierten Preis-Beobachtungen. Ersetzt die früher rein LLM-erfundene Spanne.

    Confidence/Datenqualität wird NICHT allein aus der Anzahl abgeleitet, sondern
    auch aus der Match-Qualität — und ist bewusst konservativ, weil die Datenpunkte
    aus Snippet-Text heuristisch extrahiert sind (keine verifizierten Einzelinserate).
    """
    gefunden: int = 0                 # extrahierte Datenpunkte insgesamt
    verwendet: int = 0                # tatsächlich für Median/Spanne verwendete
    anzahl_sehr_aehnlich: int = 0
    anzahl_aehnlich: int = 0
    anzahl_bedingt: int = 0
    median_eur: int | None = None
    spanne_min_eur: int | None = None   # typischer Marktbereich (unteres Quartil, robust)
    spanne_max_eur: int | None = None   # typischer Marktbereich (oberes Quartil, robust)
    angebot_eur: int | None = None      # Angebots-/Wunschpreis des Nutzers
    differenz_eur: int | None = None    # angebot - median (negativ = unter Markt)
    differenz_pct: float | None = None
    datenqualitaet: str = "niedrig"     # "niedrig" | "mittel" | "hoch"
    methode: str | None = None          # kurze Dokumentation der Berechnungsmethode
    quellen_domains: list[str] = Field(default_factory=list)
    beobachtungen: list[Preisbeobachtung] = Field(default_factory=list)  # nur verwendete
    # Reliability-Sprint 4 (§Phase 3): Domains, aus denen NUR Kategorie-/Such-/
    # Aggregatorseiten-Preispunkte stammten (source_type=="category") — diese haben
    # den Median/die Quartile/die Datenqualität NICHT beeinflusst, dienen aber als
    # transparent ausgewiesene Hintergrund-/Discovery-Quelle. Additiv, Default leer
    # -> alte gespeicherte Checks bleiben ladbar.
    hintergrund_domains: list[str] = Field(default_factory=list)
    # ── Marktanalyse-Sprint (§2): Datenqualität und Marktabdeckung sind NICHT
    # dasselbe. `datenqualitaet` beschreibt, wie zuverlässig und wie ähnlich die
    # einzelnen Fahrzeugbeobachtungen sind (8 eindeutig validierte 320d-G20-
    # Angebote EINER Plattform sind eine bessere Basis als 8 gemischte 3er-
    # Angebote aus vier Domains). `marktabdeckung` beschreibt getrennt davon, wie
    # viele unabhängige Plattformen tatsächlich beigetragen haben. Additiv,
    # Defaults entsprechen dem alten Verhalten -> alte Checks bleiben ladbar.
    marktabdeckung: str = "eingeschraenkt"   # "eingeschraenkt" | "gut" | "breit"
    anzahl_domains: int = 0
    # True, wenn die Preisstatistik mangels ausreichend guter Beobachtungen auf nur
    # "bedingt" passende Vergleiche zurückgreifen musste (§B). Deckelt das
    # Gesamtvertrauen auf completed_medium (marktrecherche.research_status).
    fallback_bedingt: bool = False
    # Nur "bedingt" passende Beobachtungen, die die Preisstatistik NICHT beeinflusst
    # haben (§A) — reiner Kontext/Transparenz, damit nachvollziehbar bleibt, was
    # gefunden, aber bewusst nicht eingerechnet wurde. Additiv, Default leer.
    kontext_beobachtungen: list[Preisbeobachtung] = Field(default_factory=list)


class PriceAssessment(BaseModel):
    """Kanonisches, DETERMINISTISCHES Preisurteil (§6/§7/§13).

    Genau EINE Quelle der Wahrheit für die Preisbewertung. Alle Ausgabebereiche
    (obere Zusammenfassung, Key Findings, "Warum?", Langbericht, Risiko, Handlung)
    leiten ihre Preisaussage aus DIESEM Objekt ab — es gibt keine getrennte
    LLM-Neubewertung derselben Zahlen. Median/Spanne/Verdikt/Confidence stammen
    ausschließlich aus der deterministischen Marktanalyse.

    Zentraler Grundsatz (§6): Innerhalb einer breiten Spanne zu liegen bedeutet
    NICHT automatisch "marktgerecht" — Medianabweichung UND Lage in der Spanne
    werden gemeinsam berücksichtigt.
    """
    # deutlich_unter | unter | marktgerecht | oberes_segment | ueber | deutlich_ueber | unbekannt
    verdict: str = "unbekannt"
    label: str = "Preis nicht bewertbar"          # nutzerlesbares Kurzlabel
    median_eur: int | None = None
    lower_bound_eur: int | None = None            # typischer Marktbereich (unteres Quartil)
    upper_bound_eur: int | None = None            # typischer Marktbereich (oberes Quartil)
    difference_eur: int | None = None             # Angebot − Median (negativ = unter Median)
    difference_percent: float | None = None
    # unter_spanne | unteres_drittel | mittig | oberes_drittel | ueber_spanne | unbekannt
    position_in_range: str = "unbekannt"
    confidence: str = "niedrig"                   # = Datenqualität der Marktanalyse
    recommendation: str = ""                      # konkrete Handlungsempfehlung (deterministisch)
    begruendung: str = ""                         # ein Satz: Median-Lage + Spannen-Lage kombiniert


class Insight(BaseModel):
    """Eine nachvollziehbare Erkenntnis mit Herkunft (Provenance).

    Wiederverwendbar für Kauf- und Verkaufscheck. `confidence` ist bewusst dreistufig
    ("hoch" | "mittel" | "niedrig") — KEINE scheinpräzisen Prozentwerte.
    """
    id: str
    # "schwachstelle" | "rueckruf" | "motorproblem" | "marktvergleich" | "wartung" | ...
    # "wartung" (kritischer Wartungspunkt der erkannten Motorvariante) wird bewusst
    # als LETZTE Sektion erzeugt, damit die fortlaufenden IDs aller übrigen
    # Kategorien unverändert bleiben — siehe app/evidence.py::build_insights.
    kategorie: str
    titel: str
    beschreibung: str
    quellen_typen: list[str] = Field(default_factory=list)   # abgeleitet aus quellen[].typ
    quellen: list[EvidenceQuelle] = Field(default_factory=list)
    # confidence = WIE BELASTBAR die Aussage ist (Provenance: Zuordnung, Baujahr-
    # Deckung, Quellenart) — hängt NIE vom Schweregrad ab.
    confidence: str                   # "hoch" | "mittel" | "niedrig"
    # schweregrad = WIE SCHLIMM das Problem ist (nur wo aus der DB vorhanden).
    # Bewusst ein EIGENES Feld, damit "wie schlimm" und "wie belastbar" strikt
    # getrennt bleiben. Fließt NICHT in confidence ein.
    schweregrad: str | None = None    # z.B. "gering" | "mittel" | "hoch" (nur Schwachstelle Baureihe)
    # applicability = WIE SICHER die Erkenntnis GENAU DIESES Fahrzeug betrifft
    # (nur Rückrufe). Strikt getrennt von confidence (Beleglage) und schweregrad
    # (wie schlimm). Reliability-Sprint 3 (§27/§28), VIER produzierbare Stufen +
    # eine fünfte, aktuell UNERREICHBARE (keine VIN-Erfassung im System):
    #   "confirmed_by_vin" | "variant_match" | "series_only" | "unclear" | "incompatible"
    # Ohne echte VIN-Prüfung wird applicability NIEMALS "confirmed_by_vin" — die
    # stärkste tatsächlich erreichbare Stufe ("variant_match") bedeutet "KANN diese
    # Variante betreffen, per FIN prüfen", NICHT "betrifft dein Fahrzeug garantiert".
    # "incompatible" (Antriebs-/Variantenwiderspruch) wird vollständig ausgeblendet.
    applicability: str | None = None
    # trust = WOHER die Aussage stammt und ob sie eine HARTE Wirkung tragen darf
    # (DATA-SAFETY-RUNTIME-GATE). Vierte, strikt getrennte Achse neben confidence
    # (Beleglage), schweregrad (wie schlimm) und applicability (betrifft dieses
    # Fahrzeug). Sie beantwortet genau eine Frage: darf dieser Fakt allein die
    # Kaufempfehlung verschärfen?
    #
    #   "verified"       — DB-Fakt, dessen Baureihe für diese Faktenart in
    #                      `verification` ausdrücklich als verified+source
    #                      hinterlegt ist (app/verification.py). NUR diese Stufe
    #                      ist floor-fähig.
    #   "unverified_db"  — DB-Fakt ohne gespeicherte Provenance. Darf als Hinweis
    #                      erscheinen und Prüfpunkte erzeugen, aber NIE allein die
    #                      Empfehlung verschärfen.
    #   "web"            — aus der technischen Webrecherche, mit URL + Qualitäts-
    #                      stufe. Trägt eigene Confidence, aber keinen Schweregrad.
    #   "user"           — Angabe aus dem Inserat/vom Nutzer.
    #   "abgeleitet"     — deterministisch berechnet (Marktvergleich).
    #
    # Der Default ist bewusst "unverified_db": eine Evidence, die ihre Herkunft
    # nicht ausdrücklich setzt, darf nicht versehentlich hart wirken.
    trust: str = "unverified_db"
    einfluss: str | None = None       # Einfluss auf die Empfehlung / Preis / Strategie
    # Nur beim Marktvergleich-Insight gesetzt: der strukturierte, deterministisch
    # berechnete Marktvergleich (Median, robuste Spanne, verwendete Datenpunkte).
    marktanalyse: Marktanalyse | None = None


class KeyFinding(BaseModel):
    """Phase 2 — verdichtete Kern-Erkenntnis ("Das solltest du wissen").

    Ein KeyFinding erzeugt KEINE neue Wahrheit: es fasst bereits vorhandene,
    deterministisch abgeleitete Daten (Marktanalyse, Rückruf-Applicability,
    Schwachstellen, Inserat-Widersprüche) zu einer sofort verständlichen Aussage
    zusammen. Passt eine Evidence dazu, referenziert `evidence_ids` deren EXISTIERENDE
    Insight-IDs (kein Erfinden). Bewusst KEINE scheinpräzisen Prozentwerte im Text.
    """
    id: str
    kategorie: str        # "preis" | "betrug" | "rueckruf" | "schwachstelle" | "motorproblem" |
                          # "widerspruch" | "vorteil" | "marktposition" | "angaben" |
                          # "ausstattung" | "datenqualitaet"
    # stufe = Dringlichkeit/Ton (KEINE Angstmache): "kritisch" | "warnung" | "chance" | "info".
    stufe: str
    icon: str | None = None            # dezentes Emoji für die Karte
    titel: str
    beschreibung: str
    wert: str | None = None            # kompakte Kennzahl, z.B. "↓ 3.010 € · ca. 11,4 %"
    aktion: str | None = None          # konkrete Handlungsempfehlung
    # Nur EXISTIERENDE Insight-IDs (siehe `insights`) — leer, wenn keine passende
    # Evidence existiert (z.B. rein aus Inserat-Daten abgeleiteter Widerspruch).
    evidence_ids: list[str] = Field(default_factory=list)
    prioritaet: int = 0                # Sortierwert (höher = wichtiger); Cap 5 nach Sortierung


class Kaufaktion(BaseModel):
    """P1-3 — EINE konkrete, deterministisch abgeleitete Handlung vor dem Kauf.

    Eine Kaufaktion erzeugt KEINE neue Wahrheit. Sie übersetzt bereits vorhandene,
    geprüfte Evidence (Baureihen-Schwachstelle, Motorproblem, KBA-Rückruf) bzw. eine
    Angabe aus dem Inserat in einen Prüfschritt. KEIN LLM ist beteiligt — weder als
    Generator noch als Quelle (der Markdown-Bericht wird NICHT ausgewertet).

    Grundsätze:
    - Fahrzeugspezifisch vor generisch: eine Aktion existiert nur, weil für DIESES
      Fahrzeug eine passende Evidence/Angabe vorliegt — nie, weil etwas "bei
      Gebrauchtwagen generell sinnvoll" ist.
    - `evidence_ids` referenzieren ausschließlich EXISTIERENDE Insight-IDs. Leer ist
      erlaubt und ehrlich (Inserat-/Wartungsangaben haben keine Insight-ID) —
      erfundene IDs gibt es nicht.
    - Vollständig marktpreis-unabhängig: keine Preisaktion, keine Nachverhandlungs-
      Aktion, keine "günstig/teuer"-Aussage (§15 P1-3).
    """
    id: str                            # stabil & inhaltsbasiert, z.B. "besichtigung-bremsen"
    bereich: str                       # "besichtigung" | "probefahrt" | "verkaeuferfragen" | "dokumente"
    # typ trennt die BEIDEN EBENEN des Prüfplans strikt voneinander:
    #   "fahrzeugspezifisch" — entstanden aus echter Evidence zu DIESEM Fahrzeug
    #                          (Schwachstelle, Motorproblem, Rückruf, Wartungspunkt)
    #                          oder aus einer ausdrücklichen Angabe im Inserat.
    #   "basis"              — allgemeiner professioneller Prüfstandard, der für jeden
    #                          Gebrauchtwagenkauf gilt. Behauptet NICHTS über dieses
    #                          Fahrzeug ("sieh nach Rost", nicht "hier ist Rost") und
    #                          hat deshalb korrekterweise keine evidence_ids.
    # Das Frontend kann damit "Bei diesem Fahrzeug besonders wichtig" von
    # "Allgemeine Checkliste" eindeutig unterscheiden.
    typ: str = "fahrzeugspezifisch"
    # Kurze Überschrift; bei bereich="verkaeuferfragen" die KONKRETE Frage.
    titel: str
    aktion: str                        # konkrete, ausführbare Beschreibung
    # "kritisch" | "hoch" | "mittel" für fahrzeugspezifische Punkte, "basis" für den
    # allgemeinen Prüfstandard — eine normale Basisprüfung wird bewusst NIE künstlich
    # zu "kritisch" hochgestuft.
    prioritaet: str
    # Abschnittsüberschrift für Anzeige und Ausdruck (z.B. "Bremsen",
    # "Vor Fahrtbeginn", "Fahrzeugpapiere"). Rein strukturell, keine Aussage.
    gruppe: str | None = None
    # Optionaler kurzer Zusatz — vor allem Sicherheits-/Rahmenhinweise
    # ("Nur wenn kein Fahrzeug folgt und der Verkehr es sicher zulässt.").
    hinweis: str | None = None
    # Nur EXISTIERENDE Insight-IDs (siehe `insights`).
    evidence_ids: list[str] = Field(default_factory=list)
    # Herkunft: "schwachstelle" | "motorproblem" | "rueckruf" | "wartung" | "inserat"
    kategorie: str | None = None
    schweregrad: str | None = None     # nur wo aus der DB vorhanden (Baureihen-Schwachstelle)
    kostenhinweis: str | None = None   # nur wo `kosten_ca` einen echten Betrag enthält
    rang: int = 0                      # deterministischer Sortierwert (höher = wichtiger)


class Pruefliste(BaseModel):
    """EINE der vier Checklisten — ein eigenständiges, für sich druckbares Arbeitsblatt.

    Bewusst zwei getrennte Listen statt einer gemischten (§12): fahrzeugspezifische
    Punkte stehen im Produkt ZUERST ("Bei diesem Fahrzeug besonders wichtig"), der
    allgemeine Prüfstandard danach. Sie werden NICHT vorab zusammengeworfen — die
    Trennung ist die eigentliche Aussage.

    Print-/PDF-Bereitschaft: Diese Klasse trägt alles, was ein Renderer für EIN
    Arbeitsblatt braucht (Bereich, Titelzeile, Fahrzeugbezeichnung, Punkte). Es gibt
    bewusst KEIN übergeordnetes Sammel-Exportobjekt und keine kombinierte Liste: das
    Produktkonzept sind vier getrennte praktische Arbeitsblätter, die einzeln
    ausgedruckt und einzeln abgehakt werden. Ein gemeinsamer Export könnte später
    zusätzlich entstehen, ist hier aber ausdrücklich nicht vorbereitet.
    """
    bereich: str                       # "besichtigung" | "probefahrt" | "verkaeuferfragen" | "dokumente"
    export_title: str                  # Überschrift des Arbeitsblatts, z.B. "Besichtigungs-Checkliste"
    # Kurzbezeichnung des Fahrzeugs für die Kopfzeile des Ausdrucks, z.B.
    # "BMW 3er G20 (2020)". None, wenn nicht einmal Marke/Modell bekannt sind.
    fahrzeug: str | None = None
    fahrzeugspezifisch: list[Kaufaktion] = Field(default_factory=list)
    basis: list[Kaufaktion] = Field(default_factory=list)


class Kaufaktionen(BaseModel):
    """P1-3 — der vollständige Prüfplan: vier eigenständige Checklisten.

    Additiv: alte gespeicherte Checks ohne dieses Feld bleiben gültig (dann vier
    leere Prüflisten). Leere Listen sind ein zulässiges, ehrliches Ergebnis — ohne
    belastbare Evidence wird kein fahrzeugspezifischer Punkt erfunden; die
    Basis-Checkliste steht trotzdem zur Verfügung.
    """
    besichtigung: Pruefliste = Field(
        default_factory=lambda: Pruefliste(bereich="besichtigung",
                                           export_title="Besichtigungs-Checkliste"))
    probefahrt: Pruefliste = Field(
        default_factory=lambda: Pruefliste(bereich="probefahrt",
                                           export_title="Probefahrt-Checkliste"))
    verkaeuferfragen: Pruefliste = Field(
        default_factory=lambda: Pruefliste(bereich="verkaeuferfragen",
                                           export_title="Fragen an den Verkäufer"))
    dokumente: Pruefliste = Field(
        default_factory=lambda: Pruefliste(bereich="dokumente",
                                           export_title="Dokumenten-Checkliste"))


class WebVehicleIdentity(BaseModel):
    """Technischer Web-Fallback — die per Webrecherche BELEGTE Fahrzeugidentität.

    Entsteht nur, wenn der DB-Pfad kein belastbares Profil liefert (echter Miss oder
    Identity-Trust-Gate schlägt an) UND die Recherche das Fahrzeug als reales
    Serienfahrzeug bestätigt.

    Abgrenzung zur DB-Baureihe: Hier gibt es KEINE `baureihe_id`. Ein reales
    Fahrzeug, das VIRA nicht kennt, bekommt auch keine erfundene lokale ID — es
    wird für DIESEN Check temporär beschrieben, nicht in den Bestand aufgenommen.
    `belegt=False` heißt ausdrücklich: die Eingabe ließ sich NICHT als reales
    Fahrzeug bestätigen (Fantasiebezeichnung) — dann bleiben alle Detailfelder leer
    und es entsteht keine fahrzeugspezifische Aussage.
    """
    belegt: bool = False
    marke: str | None = None
    modell: str | None = None
    generation: str | None = None
    bauzeitraum_von: int | None = None
    bauzeitraum_bis: int | None = None
    motor: str | None = None
    kraftstoff: str | None = None
    leistung_ps: int | None = None
    confidence: str = "niedrig"          # "hoch" | "mittel" | "niedrig"
    # Wie viele UNABHÄNGIGE Domains die Identität gestützt haben — die Grundlage
    # der `confidence` und zugleich der Schutz gegen eine einzelne SEO-Seite.
    belegende_domains: int = 0
    quellen: list[EvidenceQuelle] = Field(default_factory=list)


class WebFakt(BaseModel):
    """Ein EINZELNER, quellengebundener technischer Fakt aus der Webrecherche.

    Kein Fakt ohne Quelle: `quellen` ist für jeden hier entstehenden Eintrag
    verpflichtend befüllt (der Provider verwirft Kandidaten ohne belastbare URL).
    Das unterscheidet diese Schicht von einer bloßen LLM-Erinnerung.

    `kategorie` bleibt bewusst getrennt von den DB-Kategorien (siehe
    app/technical_research.py): eine Web-Schwachstelle wird nie als
    DB-Schwachstelle ausgegeben.
    """
    kategorie: str                       # "schwachstelle" | "rueckruf" | "wartung"
    bauteil: str | None = None           # normalisiertes Bauteil, wenn erkennbar
    aussage: str                         # der konkrete Fakt in einem Satz
    confidence: str = "niedrig"          # "hoch" | "mittel" | "niedrig"
    # Nur bei Rückrufen: dieselbe konservative Semantik wie app/recall_filter.py —
    # ohne FIN-Prüfung nie "betrifft dieses Fahrzeug".
    applicability: str | None = None
    quellen: list[EvidenceQuelle] = Field(default_factory=list)


class TechnischeRecherche(BaseModel):
    """Ergebnis EINES technischen Web-Fallback-Laufs — rein request-bezogen.

    Wird NICHT persistiert und ändert nichts an der Datenbank (kein Baureihen-
    Import, kein Überschreiben, kein `verification`-Upgrade). Der Kontext gilt
    ausschließlich für den laufenden Check.
    """
    # Warum der Fallback lief — einer der TRIGGER_*-Werte aus
    # app/technical_research.py ("db_miss", "identitaet_unsicher", "motor_fehlt",
    # "konflikt"). Leer, wenn kein Fallback stattfand.
    ausgeloest_durch: str | None = None
    identitaet: WebVehicleIdentity | None = None
    fakten: list[WebFakt] = Field(default_factory=list)
    # True, wenn der Provider technisch ausgefallen ist (Netzwerk/API). Dann gilt
    # das Ergebnis als unvollständig — es wird aber NIE eine Exception nach oben
    # gereicht, die den Kaufcheck abbrechen würde.
    provider_fehler: bool = False


class Fahrzeugkontext(BaseModel):
    """P1-4 — ergänzender Fahrzeugkontext aus der VIRA-Fahrzeugdatenbank.

    Zweck: Felder nutzbar machen, die im Datenbestand seit jeher gepflegt werden,
    den Kaufcheck aber bislang gar nicht erreicht haben.

    ABGRENZUNG ZU EVIDENCE (der wichtigste Punkt): Dies ist ausdrücklich KEINE
    Evidence. Ein Insight (siehe `insights`) ist eine geprüfte Aussage ÜBER DIESES
    FAHRZEUG mit Herkunft und Confidence — eine Schwachstelle, ein KBA-Rückruf, ein
    Wartungspunkt. Der Fahrzeugkontext hier beschreibt dagegen die BAUREIHE
    allgemein: wie man die Generation erkennt, in welchem Segment sie liegt, welches
    Ölwechsel-Intervall der Hersteller vorsieht. Er begründet keine Aussage über den
    Zustand des Fahrzeugs und taucht deshalb bewusst nicht in `insights` auf und wird
    von keiner Kaufaktion referenziert.

    Vertrauensstufen der Felder (siehe app/fahrzeugkontext.py):
      strukturiert  — `segment`, `wartung_oel_km`
      Freitext      — `erkennung_generation`, `facelift_merkmale`,
                      `wartung_hu_intervall`, `vorgaenger`

    BEWUSST NICHT ENTHALTEN: `kaufberatung`. Das Feld ist nur bei 22 % der Baureihen
    befüllt und werblich formuliert — es wird weder hier ausgegeben noch in den
    Prompt kopiert.

    Alle Felder sind optional und werden nur gesetzt, wenn ein echter Wert vorliegt.
    Es gibt keine Platzhalter und keine "nicht erfasst"-Werte.

    Enthält KEINE berechnete Wartungsfälligkeit (P2-5): `wartung_oel_km` ist das
    Herstellerintervall, nicht die Aussage, dass ein Service ansteht.
    """
    baureihe_id: str | None = None
    generation: str | None = None            # z.B. "G20/G21"
    segment: str | None = None               # z.B. "Mittelklasse" (strukturiert)
    vorgaenger: str | None = None            # aufgelöster Klarname, z.B. "Opel Insignia A"
    erkennung_generation: str | None = None  # Freitext, gekürzt
    facelift_merkmale: str | None = None     # Freitext, gekürzt
    wartung_oel_km: int | None = None        # Herstellerintervall in km (strukturiert)
    wartung_hu_intervall: str | None = None  # Freitext, zeitbezogen — NIE gegen km rechnen

    def hat_inhalt(self) -> bool:
        """True, sobald mindestens ein inhaltliches Feld belegt ist.

        `baureihe_id` und `generation` zählen bewusst NICHT mit: beide stehen bereits
        an anderer Stelle in der Antwort (`baureihe_erkannt`) und wären allein kein
        Grund, dem Frontend einen ansonsten leeren Kontextblock zu schicken.
        """
        return any((self.segment, self.vorgaenger, self.erkennung_generation,
                    self.facelift_merkmale, self.wartung_oel_km,
                    self.wartung_hu_intervall))


class Wartungshinweis(BaseModel):
    """P2-5 — EIN Wartungspunkt, dessen hinterlegtes Intervall in der Nähe der
    tatsächlichen Laufleistung dieses Fahrzeugs liegt.

    DER ZENTRALE PUNKT: Dies ist ausdrücklich KEINE Fälligkeitsaussage. VIRA weiß
    NICHT, wann die Arbeit zuletzt durchgeführt wurde — es gibt im gesamten System
    kein Feld dafür (weder im Inserat-Request noch in der Fahrzeugdatenbank; siehe
    app/laufleistung.py, Abschnitt "Was NICHT bekannt ist"). Ein Hinweis sagt
    deshalb immer nur: an dieser Stelle des Fahrzeuglebens ist dieser Wartungspunkt
    RELEVANT, lass dir den Nachweis zeigen. Er sagt nie "fällig", "überfällig" oder
    "nicht gemacht".

    Entsteht ausschließlich aus einer EXISTIERENDEN Evidence (kritischer
    Wartungspunkt der erkannten Motorvariante oder quellengebundener Web-Fakt) mit
    einem konkret auswertbaren Kilometer-Intervall. `wartung_oel_km` aus dem
    Fahrzeugkontext (P1-4) erzeugt NIEMALS einen Wartungshinweis — das Feld ist
    unverified, liegt auf Baureihen- statt Motorebene und trägt keine Quelle.
    """
    bauteil: str
    # "naehert_sich" | "im_bereich" | "darueber" — "entfernt" erzeugt bewusst
    # GAR KEINEN Hinweis (keine Warnung ohne Anlass).
    status: str
    punkt_km: int                      # unterer/erster Wert des hinterlegten Intervalls
    punkt_bis_km: int | None = None    # oberer Wert, nur bei einer Spanne ("150.000 - 250.000 km")
    # Kilometerstand minus `punkt_km`. Negativ = der Punkt liegt noch voraus.
    differenz_km: int
    intervall_text: str                # der unveränderte Originaltext der Evidence
    hinweis: str                       # ausformulierter Satz — nie eine Fälligkeitsbehauptung
    herkunft: str                      # "db_wartung" | "web_wartung"
    evidence_id: str                   # IMMER eine existierende Insight-ID (§12)
    quellen: list[EvidenceQuelle] = Field(default_factory=list)


class Laufleistungskontext(BaseModel):
    """P2-5 — Kilometerstand und Fahrzeugalter eingeordnet, plus die daraus
    relevanten Wartungspunkte.

    ABGRENZUNGEN, die dieses Modell trägt:

    * KEINE Preisaussage (§13). Das Modul bekommt weder Marktanalyse noch Preis;
      eine "deswegen günstig/teuer"-Aussage ist strukturell nicht konstruierbar.
      Der Kontext ist bei `completed_no_market` identisch (§14).
    * KEINE Modulo-Fälligkeit (§2). Es wird nie `kilometerstand % intervall`
      gerechnet. Berechnet wird ausschließlich der Abstand zum ERSTEN hinterlegten
      Wartungspunkt.
    * KEINE Scheinpräzision (§4/§5). `fahrzeugalter_jahre` ist ein ganzzahliger
      Näherungswert aus dem Baujahr (kein Erstzulassungsmonat im System),
      `km_pro_jahr` ein auf 100 km gerundeter DURCHSCHNITT seit dem Baujahr — nicht
      die tatsächliche Fahrleistung eines Vorbesitzers in einem einzelnen Jahr.

    Alle Felder sind optional, weil jede Zutat einzeln fehlen kann: ohne Baujahr
    kein Alter, ohne Alter keine km/Jahr, ohne Kilometerstand keine
    Wartungshinweise.

    BEWUSST KEINE qualitative Einordnung von `km_pro_jahr` (z.B. "niedrig" /
    "durchschnittlich" / "erhöht"): im Projekt existiert dafür keine belastbare
    fachliche Schwellenbasis (kein ADAC-/DAT-/KBA-Referenzwert, keine zitierte
    Quelle) — nur zwei interne Heuristik-Literale aus Phase 2
    (app/key_findings.py, für eine einzelne "unterdurchschnittlich"-Meldung).
    Eine dritte, daraus gespiegelte Grenze für "erhöht" wäre eine frei
    erfundene Universalgrenze gewesen. Ausgegeben wird nur die berechnete
    Zahl — siehe app/laufleistung.py.
    """
    kilometerstand: int | None = None
    fahrzeugalter_jahre: int | None = None
    km_pro_jahr: int | None = None
    wartungshinweise: list[Wartungshinweis] = Field(default_factory=list)
    # Ehrlichkeits-Marker und zugleich die wichtigste Einzelaussage dieses Modells:
    # Der letzte tatsächliche Service ist VIRA nicht bekannt. Das Feld ist
    # aktuell IMMER False — es existiert keine Datenquelle, die es True machen
    # könnte (siehe Wartungshinweis). Es steht trotzdem explizit da, damit
    # Frontend und Prompt die Unwissenheit ausdrücken können, statt sie zu
    # verschweigen.
    letzter_service_bekannt: bool = False

    def hat_inhalt(self) -> bool:
        return any((self.kilometerstand, self.fahrzeugalter_jahre,
                    self.km_pro_jahr, self.wartungshinweise))


# ---------- Kauf-Check ----------

class KaufCheckResponse(BaseModel):
    bericht: str                                   # Markdown-Bericht
    empfehlung: str                                # "kaufen" | "kaufen_nach_besichtigung" | "nur_mit_werkstattpruefung" | "preis_nachverhandeln" | "hohes_risiko" | "finger_weg" | "unbekannt"
    preis_bewertung: str                           # "extrem_guenstig" | "guenstig" | "marktgerecht" | "teuer" | "extrem_teuer" | "unbekannt"
    marktpreis_min: int | None = None              # EUR
    marktpreis_max: int | None = None              # EUR
    baureihe_erkannt: str | None = None            # DB-ID der erkannten Baureihe
    motor_erkannt: str | None = None               # DB-ID der erkannten Motorvariante
    quelle: str                                    # "datenbank" | "web" | "gemischt"
    vertrauen: str                                 # "hoch" | "mittel" | "niedrig"
    belege: list[Any] = Field(default_factory=list)
    # Phase 1: strukturierte, nachvollziehbare Erkenntnisse (additiv, backward-compatible).
    insights: list[Insight] = Field(default_factory=list)
    # Phase 1 Schicht B: welche vorhandenen Insight-IDs (siehe `insights`) die jeweilige
    # LLM-Entscheidung stützen. Backend-validiert — enthält nur existierende IDs.
    empfehlung_evidence_ids: list[str] = Field(default_factory=list)
    preis_evidence_ids: list[str] = Field(default_factory=list)
    risiko_evidence_ids: list[str] = Field(default_factory=list)
    # Phase 2: verdichtete Kern-Erkenntnisse ("Das solltest du wissen"), max. 5,
    # deterministisch aus den obigen Daten. Additiv -> alte Checks ohne dieses Feld
    # laden weiter (Default []).
    key_findings: list[KeyFinding] = Field(default_factory=list)
    # Reliability-Sprint: kanonisches, deterministisches Preisurteil (§6/§7/§13).
    # Alle Preis-Ausgaben leiten sich hieraus ab. None nur bei Alt-Checks.
    price_assessment: PriceAssessment | None = None
    # Ergebnis des KAUFCHECKS (nicht der Marktrecherche — die bewertet
    # app/marktrecherche.research_status separat und unverändert weiter):
    #   "completed_high"       — Normalfall, belastbarer Marktvergleich
    #   "completed_medium"     — belastbar, aber eingeschränkte Marktabdeckung
    #   "completed_no_market"  — P0-1: Check fachlich VOLLSTÄNDIG abgeschlossen
    #                            (Fahrzeug, Schwachstellen, Rückrufe, Insights,
    #                            Empfehlung), aber KEIN belastbarer Marktpreis.
    #                            Preisfelder sind dann zwingend leer:
    #                            marktpreis_min/max = None, preis_bewertung =
    #                            "unbekannt", price_assessment.verdict =
    #                            "unbekannt". Das ist KEIN Fehler und löst KEINE
    #                            Kontingent-Rückerstattung aus.
    # Identity-Trust-Gate: wie verlässlich die erkannte Baureihe ist.
    #   "hoch"    — exakter Modelltreffer, exakter Motor-/Verkaufsbezeichnungs-
    #               treffer, genannte Generation, oder Substring mit ausschließlich
    #               bekannten Aufbauwörtern ("3er Touring"). Voller Funktionsumfang.
    #   "niedrig" — nur ein Teiltreffer, mehrdeutig, oder das Baujahr widerspricht
    #               dem Bauzeitraum. Dann entstehen KEINE fahrzeugspezifischen
    #               Aussagen: `baureihe_erkannt` bleibt leer, `insights`,
    #               `key_findings` und die fahrzeugspezifischen Kaufaktionen sind
    #               ohne DB-Befund. Die Basis-Checklisten bleiben vollständig.
    # Unterscheidet sich von `vertrauen`: das bewertet die Quellenlage
    # (DB/Web/gemischt), dies die Fahrzeug-Zuordnung selbst.
    identitaet_konfidenz: str = "hoch"
    # Warum die Zuordnung so bewertet wurde — eine der MATCH_*-Konstanten aus
    # app/car_lookup.py ("exact", "motor_alias", "generation_match", "strong",
    # "ambiguous", "substring_only", "token_inner", "marke_only", "no_match").
    identitaet_match_art: str | None = None
    # Technischer Web-Fallback: woher die TECHNISCHEN Fahrzeugdaten dieses Checks
    # stammen. Bewusst getrennt von `quelle` (das bewertet die Gesamtlage inkl.
    # Marktdaten) und von `vertrauen`:
    #   "db"          — belastbares DB-Profil, kein technischer Fallback nötig
    #   "db_plus_web" — DB-Profil vorhanden UND gezielt per Web ergänzt
    #   "web"         — kein DB-Profil, Fahrzeug per Webrecherche belegt
    #   "partial"     — weder belastbares DB-Profil noch belegte Web-Identität;
    #                   der Check läuft mit Nutzerangaben + Basis-Prüfplänen weiter
    # Das Frontend kann daraus später "Daten aus der VIRA-Datenbank" vs. "durch
    # aktuelle Webrecherche ergänzt" transparent machen.
    technical_coverage: str = "db"
    # Die per Webrecherche belegte Identität, falls ein Fallback lief. None, wenn
    # kein Fallback nötig war oder nichts belegt werden konnte.
    web_identitaet: WebVehicleIdentity | None = None
    # P1-4: ergänzender Fahrzeugkontext aus der Fahrzeugdatenbank (Segment,
    # Generations-/Facelift-Merkmale, Vorgänger, Wartungsintervalle). Additiv und
    # optional -> alte Checks ohne dieses Feld laden weiter (Default: None).
    # Ausdrücklich KEINE Evidence und keine Wartungsfälligkeit — siehe Fahrzeugkontext.
    fahrzeugkontext: Fahrzeugkontext | None = None
    # P2-5: Laufleistungs- und Wartungskontext (Alter, km/Jahr, relevante
    # Wartungspunkte). Additiv und optional -> alte Checks ohne dieses Feld laden
    # weiter (Default: None). Enthält KEINE Fälligkeitsaussage und KEINE
    # Preisaussage — siehe Laufleistungskontext.
    laufleistungskontext: Laufleistungskontext | None = None
    # P1-3: deterministische Kaufaktionen (Besichtigung/Probefahrt/Verkäuferfragen/
    # Dokumente), abgeleitet aus denselben Insights/Inserat-Daten wie key_findings.
    # Additiv -> alte Checks ohne dieses Feld laden weiter (Default: vier leere Listen).
    kaufaktionen: Kaufaktionen = Field(default_factory=Kaufaktionen)
    # "research_failed" wird vom Kaufcheck NICHT mehr ausgeliefert (der
    # Verkaufscheck nutzt es weiterhin — dort IST der Marktpreis das Produkt).
    # Default für Alt-Checks: completed_high.
    research_status: str = "completed_high"


# ---------- Verkaufs-Check ----------

class VerkaufsCheckRequest(BaseModel):
    marke: str | None = Field(default=None, max_length=100)
    modell: str | None = Field(default=None, max_length=100)
    baujahr: int | None = None
    kilometerstand: int | None = None
    motor: str | None = Field(default=None, max_length=200)
    kraftstoff: str | None = Field(default=None, max_length=100)
    getriebe: str | None = Field(default=None, max_length=60)        # "Automatik" | "Schaltgetriebe" | ...
    farbe: str | None = Field(default=None, max_length=60)
    ausstattung: list[str] = Field(default_factory=list, max_length=100)
    beschreibung: str | None = Field(default=None, max_length=_MAX_TEXT_LEN)        # Zustand, Besonderheiten (Nichtraucher, Scheckheft, ...)
    # Phase 4: der tatsächliche, vom Verkäufer eingegebene Inserats-Beschreibungstext
    # (Freitext). Getrennt von `beschreibung` (Zustands-Label) und `freitext`
    # (Alt-Feld, das die strukturierten Felder ERSETZT — siehe _format_fahrzeug).
    # Wird für die Widerspruchsprüfung (Beschreibung vs. Strukturdaten) genutzt.
    inserat_text: str | None = Field(default=None, max_length=_MAX_TEXT_LEN)
    inserat_titel: str | None = Field(default=None, max_length=200)                 # vom Nutzer eingegebener Titel (optional)
    maengel: list[str] = Field(default_factory=list, max_length=100)  # bekannte Mängel / anstehende Reparaturen
    preis_vorstellung: int | None = None   # eigene Preisvorstellung des Verkäufers (optional)

    # Zusätzliche Angaben — verbessern die Preiseinschätzung deutlich
    unfallfrei: str | None = Field(default=None, max_length=20)      # "ja" | "nein" | "unbekannt"
    vorbesitzer: int | None = None
    tuev_bis: str | None = Field(default=None, max_length=20)        # z.B. "06/2027"
    scheckheftgepflegt: bool | None = None

    freitext: str | None = Field(default=None, max_length=_MAX_TEXT_LEN)            # alternative Freitexteingabe
    bild_base64: str | None = Field(default=None, max_length=_MAX_BILD_B64_LEN)


# ---------- Phase 4: Inseratsanalyse & -optimierung (Seller-Tools) ----------

class FehlendeAngabe(BaseModel):
    """Eine im Inserat fehlende Angabe, nach Wichtigkeit kategorisiert.

    `wichtigkeit` ist deterministisch vergeben (kein LLM): "kritisch" | "wichtig" |
    "optional". `feld` ist der nutzerlesbare Name (z.B. "TÜV/HU").
    """
    feld: str
    wichtigkeit: str                  # "kritisch" | "wichtig" | "optional"


class ListingAnalyse(BaseModel):
    """Deterministische Qualitätsanalyse des Inserats (Phase 4).

    Erzeugt KEINE neue Wahrheit und KEINEN erfundenen KI-Score: Vollständigkeit ist
    ein transparenter Zählwert (`vorhanden`/`gesamt`), das Label leitet sich fest
    daraus ab. Alle Listen enthalten nur, was aus den Eingaben oder den geprüften
    Strukturdaten belegbar ist. Additiv/backward-compatible — alte Checks besitzen
    das Feld nicht (Default None).
    """
    qualitaet: str                    # "sehr_gut" | "gut" | "verbesserbar" | "unvollstaendig"
    vorhanden: int                    # Anzahl vorhandener wichtiger Angaben
    gesamt: int                       # Gesamtzahl geprüfter wichtiger Angaben
    staerken: list[str] = Field(default_factory=list)          # Vertrauensfaktoren (belegt)
    verkaufsargumente: list[str] = Field(default_factory=list) # aus Ausstattung/Daten (max ~6)
    fehlende_angaben: list[FehlendeAngabe] = Field(default_factory=list)
    probleme: list[str] = Field(default_factory=list)          # Widersprüche / schwache Angaben
    verbesserungen: list[str] = Field(default_factory=list)    # konkrete Handlungsempfehlungen
    preis_hinweis: str | None = None
    titel_vorschlag: str | None = None                         # deterministisch aus Strukturdaten
    # Nur EXISTIERENDE Insight-IDs (Marktvergleich) — leer, wenn keine passt.
    evidence_ids: list[str] = Field(default_factory=list)


class InseratOptimierung(BaseModel):
    """LLM-erzeugte, FAKTEN-GEPRÜFTE optimierte Inseratsversion (Phase 4).

    Der LLM ist ausschließlich Textersteller. Vor der Rückgabe wird der Text gegen
    die Eingangsdaten geprüft (`pruefe_fakten`): unbelegte Positiv-Behauptungen
    (unfallfrei, Scheckheft, TÜV neu, Vorbesitzer, nicht genannte Ausstattung)
    werden entfernt/neutralisiert, bekannte Mängel bleiben ehrlich enthalten.
    """
    titel: str
    beschreibung: str
    generiert_am: str | None = None   # ISO-Zeitstempel (für Anzeige/Persistenz)
    # Transparenz: welche Aussagen der Fakten-Schutz entfernt/neutralisiert hat.
    entfernte_behauptungen: list[str] = Field(default_factory=list)


class VerkaufsCheckResponse(BaseModel):
    bericht: str                                   # Markdown-Bericht
    schnellverkaufs_preis: int | None = None       # unteres Ende — zügiger Verkauf (= Marktvergleich-Untergrenze)
    maximal_preis: int | None = None               # oberes realistisches Ende (= Marktvergleich-Obergrenze)
    empfohlener_preis: int | None = None           # empfohlene Mitte (= Marktmedian)
    # §11: KEINE scheinpräzisen Tageszahlen mehr, wenn keine belastbaren historischen
    # Standzeitdaten vorliegen — bleiben None. Stattdessen Vermarktungs-KATEGORIEN.
    verkaufsdauer_tage_schnell: int | None = None
    verkaufsdauer_tage_maximal: int | None = None
    # §11: Vermarktungsdauer als Kategorie ("voraussichtlich schneller" |
    # "durchschnittliche Vermarktungsdauer" | "wahrscheinlich längere Vermarktung").
    verkaufsdauer_schnell: str | None = None
    verkaufsdauer_empfohlen: str | None = None
    verkaufsdauer_maximal: str | None = None
    marktpreis_min: int | None = None              # Markt-Untergrenze aus Web
    marktpreis_max: int | None = None              # Markt-Obergrenze aus Web
    baureihe_erkannt: str | None = None
    motor_erkannt: str | None = None
    quelle: str
    vertrauen: str
    belege: list[Any] = Field(default_factory=list)
    # Phase 1: strukturierte, nachvollziehbare Erkenntnisse (additiv, backward-compatible).
    insights: list[Insight] = Field(default_factory=list)
    # Phase 1 Schicht B: welche vorhandenen Insight-IDs (siehe `insights`) die jeweilige
    # LLM-Entscheidung stützen. Backend-validiert — enthält nur existierende IDs.
    preis_evidence_ids: list[str] = Field(default_factory=list)
    strategie_evidence_ids: list[str] = Field(default_factory=list)
    argument_evidence_ids: list[str] = Field(default_factory=list)
    # Phase 2: verdichtete Kern-Erkenntnisse ("Das solltest du wissen"), max. 5,
    # deterministisch. Additiv -> alte Checks laden weiter (Default []).
    key_findings: list[KeyFinding] = Field(default_factory=list)
    # Phase 4: deterministische Inseratsanalyse (Qualität, fehlende Angaben,
    # Verkaufsargumente). Additiv -> alte Checks ohne dieses Feld laden weiter.
    listing_analyse: ListingAnalyse | None = None
    # Phase 4: optimierte Inseratsversion — NICHT bei jedem Check erzeugt, sondern
    # on-demand über einen separaten Endpoint. Beim erneuten Öffnen eines
    # gespeicherten Checks liegt eine bereits erzeugte Version hier (persistiert)
    # vor -> kein neuer LLM-Call.
    inserat_optimierung: InseratOptimierung | None = None
    # Reliability-Sprint: kanonisches, deterministisches Preisurteil (§6/§7/§13) —
    # dieselbe Quelle für obere Zusammenfassung, Bericht, Key Findings und Strategie.
    price_assessment: PriceAssessment | None = None
    # Reliability-Sprint: Quality-Gate-Ergebnis (§1/§14). "completed_high" |
    # "completed_medium" | "completed_no_market" (P1 #2 — Check fachlich vollständig,
    # aber kein belastbarer Marktpreis: alle Preisfelder None, price_assessment.
    # verdict = "unbekannt"; KEIN Fehler, KEINE Kontingent-Rückerstattung).
    # "research_failed" wird weiterhin nicht als fertiger Check geliefert.
    research_status: str = "completed_high"
    # P1 #1: Verlässlichkeit der Fahrzeug-Zuordnung (analog KaufCheckResponse).
    #   "hoch"    — exakter Modell-/Motor-/Generationstreffer oder Substring mit
    #               ausschließlich bekannten Aufbauwörtern ("3er Touring"). Voller
    #               Funktionsumfang, fahrzeugspezifische DB-Fakten möglich.
    #   "niedrig" — nur Teiltreffer, mehrdeutig, oder Baujahr widerspricht dem
    #               Bauzeitraum. Dann bleibt `baureihe_erkannt` leer und es
    #               entstehen KEINE fahrzeugspezifischen Aussagen; Inseratsanalyse,
    #               Key-Findings und Bericht laufen ohne DB-Befund weiter.
    identitaet_konfidenz: str = "hoch"
    # Eine der MATCH_*-Konstanten aus app/car_lookup.py.
    identitaet_match_art: str | None = None


# ---------- Phase 5: VIRA Dealer (Händler-Bestand) ----------

DEALER_STATUS = ("beobachtung", "einkauf_geplant", "im_bestand", "verkauft")


class DealerVehicleCreate(BaseModel):
    """Manuelles Anlegen eines Händler-Fahrzeugs (ohne vorherigen Check).

    Alle Preise/Kilometer werden auf >= 0 validiert. Fahrzeug-Kerndaten optional —
    ein Händler darf auch mit minimalen Angaben eine Akte anlegen und später ergänzen.
    """
    marke: str | None = Field(default=None, max_length=100)
    modell: str | None = Field(default=None, max_length=100)
    baureihe: str | None = Field(default=None, max_length=100)
    motor: str | None = Field(default=None, max_length=200)
    baujahr: int | None = Field(default=None, ge=1900, le=2100)
    kilometerstand: int | None = Field(default=None, ge=0)
    status: str = Field(default="beobachtung")
    einkaufspreis: int | None = Field(default=None, ge=0)
    nebenkosten: int | None = Field(default=None, ge=0)
    geplanter_verkaufspreis: int | None = Field(default=None, ge=0)
    interne_notiz: str | None = Field(default=None, max_length=_MAX_TEXT_LEN)

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in DEALER_STATUS:
            raise ValueError(f"status muss einer von {DEALER_STATUS} sein")
        return v


class DealerVehicleUpdate(BaseModel):
    """Teilweises Update eines Händler-Fahrzeugs (PATCH). Alle Felder optional —
    nur gesetzte Felder werden geändert. `tatsaechlicher_verkaufspreis` typischerweise
    beim Wechsel auf status='verkauft'."""
    marke: str | None = Field(default=None, max_length=100)
    modell: str | None = Field(default=None, max_length=100)
    baureihe: str | None = Field(default=None, max_length=100)
    motor: str | None = Field(default=None, max_length=200)
    baujahr: int | None = Field(default=None, ge=1900, le=2100)
    kilometerstand: int | None = Field(default=None, ge=0)
    status: str | None = None
    einkaufspreis: int | None = Field(default=None, ge=0)
    nebenkosten: int | None = Field(default=None, ge=0)
    geplanter_verkaufspreis: int | None = Field(default=None, ge=0)
    tatsaechlicher_verkaufspreis: int | None = Field(default=None, ge=0)
    interne_notiz: str | None = Field(default=None, max_length=_MAX_TEXT_LEN)
    verkaufscheck_id: int | None = None   # bestehenden Verkaufscheck verknüpfen

    @field_validator("status")
    @classmethod
    def _status(cls, v: str | None) -> str | None:
        if v is not None and v not in DEALER_STATUS:
            raise ValueError(f"status muss einer von {DEALER_STATUS} sein")
        return v


class DealerFinance(BaseModel):
    """Deterministischer Margenrechner. KEINE Steuer-/Gewinn-/Gewährleistungs-/
    Finanzierungsannahmen — nur die vom Nutzer eingegebenen Zahlen.

    Marge-Prozent bezieht sich auf den VERKAUFSPREIS (Marge / Verkaufspreis).
    Fehlende Grundwerte -> None (Frontend zeigt "–", niemals eine erfundene 0-€-Marge).
    """
    einkaufspreis: int | None = None
    nebenkosten: int | None = None
    gesamteinsatz: int | None = None
    geplanter_verkaufspreis: int | None = None
    moegliche_bruttomarge: int | None = None
    moegliche_marge_pct: float | None = None
    tatsaechlicher_verkaufspreis: int | None = None
    realisierte_bruttomarge: int | None = None
    realisierte_marge_pct: float | None = None
    hinweis: str = ("Bruttomarge vor Steuern, Gewährleistung, Finanzierung und sonstigen "
                    "Betriebskosten. Marge-% bezogen auf den Verkaufspreis.")


class DealerViraKauf(BaseModel):
    """Aus dem verknüpften KAUFCHECK übernommene VIRA-Signale (nichts neu berechnet)."""
    vorhanden: bool = False
    kaufcheck_id: int | None = None
    empfehlung: str | None = None
    preis_bewertung: str | None = None
    markt_median: int | None = None
    markt_min: int | None = None
    markt_max: int | None = None
    risiko_hinweise: list[str] = Field(default_factory=list)   # aus Key Findings (kritisch/warnung)
    key_findings_count: int = 0


class DealerViraVerkauf(BaseModel):
    """Aus dem verknüpften VERKAUFSCHECK übernommene Signale (nichts neu berechnet)."""
    vorhanden: bool = False
    verkaufscheck_id: int | None = None
    empfohlener_preis: int | None = None
    markt_median: int | None = None
    inserat_qualitaet: str | None = None
    hat_optimierung: bool = False


class DealerTriage(BaseModel):
    """Schnelle Vergleichs-Signale (KEIN Fake-Score). Werte stammen ausschließlich
    aus dem verknüpften Kaufcheck bzw. der deterministischen Marge."""
    empfehlung: str = "unklar"   # kaufen | nach_pruefung | vorsicht | nicht_empfohlen | unklar
    preis: str = "unklar"        # guenstig | marktgerecht | teuer | unklar
    risiko: str = "unklar"       # gering | mittel | erhoeht | pruefen | unklar
    marge_eur: int | None = None


class DealerVehicle(BaseModel):
    """Vollständige Händler-Fahrzeugakte inkl. deterministisch berechneter Finanzen
    und aus verknüpften Checks übernommener VIRA-Signale."""
    id: int
    marke: str | None = None
    modell: str | None = None
    baureihe: str | None = None
    motor: str | None = None
    baujahr: int | None = None
    kilometerstand: int | None = None
    status: str
    interne_notiz: str | None = None
    kaufcheck_id: int | None = None
    verkaufscheck_id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    sold_at: str | None = None
    finanzen: DealerFinance
    vira: DealerViraKauf
    verkauf: DealerViraVerkauf
    triage: DealerTriage
    braucht_aufmerksamkeit: bool = False
    aufmerksamkeit_gruende: list[str] = Field(default_factory=list)


class DealerSummary(BaseModel):
    """Dashboard-Kennzahlen. Kapital/Margen nur, wenn Daten vorhanden sind
    (sonst None -> Frontend zeigt "–"). Keine Fake-KPIs."""
    fahrzeuge_gesamt: int = 0
    beobachtung: int = 0
    einkauf_geplant: int = 0
    im_bestand: int = 0
    verkauft: int = 0
    gebundenes_kapital: int | None = None
    geplante_bruttomarge: int | None = None
    realisierte_bruttomarge: int | None = None
    braucht_aufmerksamkeit: int = 0


# ---------- AutoFinder (Runde 2: HTTP-Vertrag) ----------
#
# Diese Klassen sind der API-Vertrag von POST /api/v1/autofinder. Sie sind
# NICHT dasselbe wie `app.autofinder.AutoFinderRequest`/`AutoFinderKandidat`
# (Dataclasses der Ranking-Engine aus Runde 1) — der Router übersetzt zwischen
# beiden (siehe app/routers/autofinder.py). Getrennt gehalten, damit die
# Engine unabhängig von FastAPI/Pydantic bleibt und der HTTP-Vertrag eigene
# Validierung (422 statt stillem Fehlverhalten) tragen kann.
#
# Budget/Kilometerstand/praktisch/komfortabel/familie werden hier ENTGEGEN-
# GENOMMEN, aber in Runde 2 nicht ausgewertet — siehe app/autofinder.py
# Moduldoc "VORBEREITET, ABER NICHT AUSGEWERTET". Kein Feld wird hier
# verworfen, damit eine spätere Runde die Architektur erweitert statt umbaut.

_AUTOFINDER_NUTZUNG = ("stadt", "gemischt", "langstrecke")
_AUTOFINDER_MAX_LISTE = 20   # Sicherheitsnetz gegen überlange Filterlisten


def _validiere_enum_liste(werte: list[str], erlaubt: tuple[str, ...], feldname: str) -> list[str]:
    erlaubt_lower = {e.lower() for e in erlaubt}
    for w in werte:
        if w.strip().lower() not in erlaubt_lower:
            raise ValueError(f"{feldname}: {w!r} ist kein bekannter Wert (erlaubt: {erlaubt})")
    return werte


class AutoFinderRequest(BaseModel):
    """Nutzereingaben für den AutoFinder — kostenlos, kein Check-Credit."""

    # ---- BASIS ---- Budget/Kilometer: kein Marktpreis-/Gebrauchtwagen-
    # Datenbestand vorhanden, deshalb aktuell KEIN harter Filter (§4/§13).
    budget_min: int | None = Field(default=None, ge=0)
    budget_max: int | None = Field(default=None, ge=0)
    baujahr_von: int | None = Field(default=None, ge=1900, le=2100)
    baujahr_bis: int | None = Field(default=None, ge=1900, le=2100)
    kilometer_max: int | None = Field(default=None, ge=0)

    # ---- FAHRZEUG (harte Filter) ----
    marken_bevorzugt: list[str] = Field(default_factory=list, max_length=_AUTOFINDER_MAX_LISTE)
    marken_ausschliessen: list[str] = Field(default_factory=list, max_length=_AUTOFINDER_MAX_LISTE)
    karosserie: list[str] = Field(default_factory=list, max_length=_AUTOFINDER_MAX_LISTE)
    kraftstoff: list[str] = Field(default_factory=list, max_length=_AUTOFINDER_MAX_LISTE)
    getriebe: list[str] = Field(default_factory=list, max_length=_AUTOFINDER_MAX_LISTE)
    antrieb: list[str] = Field(default_factory=list, max_length=_AUTOFINDER_MAX_LISTE)
    leistung_min_ps: int | None = Field(default=None, ge=0, le=2000)
    leistung_max_ps: int | None = Field(default=None, ge=0, le=2000)

    # ---- NUTZUNG (steuert Score, kein Hard Filter) ----
    nutzung: str | None = Field(default=None)   # "stadt" | "gemischt" | "langstrecke"
    km_pro_jahr: int | None = Field(default=None, ge=0)

    # ---- PRIORITÄTEN ----
    sportlich: bool = False
    sparsam: bool = False
    fahranfaenger: bool = False
    praktisch: bool = False       # aktuell ohne Effekt — siehe Moduldoc oben
    komfortabel: bool = False     # aktuell ohne Effekt
    familie: bool = False         # aktuell ohne Effekt

    @field_validator("karosserie")
    @classmethod
    def _val_karosserie(cls, v: list[str]) -> list[str]:
        return _validiere_enum_liste(v, KAROSSERIE_KLASSEN, "karosserie")

    @field_validator("getriebe")
    @classmethod
    def _val_getriebe(cls, v: list[str]) -> list[str]:
        return _validiere_enum_liste(v, GETRIEBE_KLASSEN, "getriebe")

    @field_validator("kraftstoff")
    @classmethod
    def _val_kraftstoff(cls, v: list[str]) -> list[str]:
        return _validiere_enum_liste(v, KRAFTSTOFF_WERTE, "kraftstoff")

    @field_validator("antrieb")
    @classmethod
    def _val_antrieb(cls, v: list[str]) -> list[str]:
        return _validiere_enum_liste(v, ANTRIEB_WERTE, "antrieb")

    @field_validator("nutzung")
    @classmethod
    def _val_nutzung(cls, v: str | None) -> str | None:
        if v is not None and v not in _AUTOFINDER_NUTZUNG:
            raise ValueError(f"nutzung muss einer von {_AUTOFINDER_NUTZUNG} sein")
        return v

    @model_validator(mode="after")
    def _val_min_le_max(self) -> "AutoFinderRequest":
        if self.budget_min is not None and self.budget_max is not None \
                and self.budget_min > self.budget_max:
            raise ValueError("budget_min darf nicht größer als budget_max sein")
        if self.leistung_min_ps is not None and self.leistung_max_ps is not None \
                and self.leistung_min_ps > self.leistung_max_ps:
            raise ValueError("leistung_min_ps darf nicht größer als leistung_max_ps sein")
        if self.baujahr_von is not None and self.baujahr_bis is not None \
                and self.baujahr_von > self.baujahr_bis:
            raise ValueError("baujahr_von darf nicht größer als baujahr_bis sein")
        return self


class AutoFinderSuchfilterHinweis(BaseModel):
    """Vorbereitete Struktur für 'So findest du dieses Auto' (mobile.de/
    AutoScout24-taugliche Filterangaben). NUR die Struktur — in Runde 2 wird
    hier NICHTS befüllt und KEINE Plattform-URL erzeugt (§14 explizit NICHT
    in dieser Runde)."""
    marke: str | None = None
    modell: str | None = None
    baujahr_von: int | None = None
    baujahr_bis: int | None = None
    leistung_min_ps: int | None = None
    leistung_max_ps: int | None = None
    kraftstoff: str | None = None
    getriebe: str | None = None
    karosserie: str | None = None
    kilometer_max: int | None = None
    preis_max: int | None = None


class AutoFinderKandidatOut(BaseModel):
    """Ein Kandidat im HTTP-Vertrag — 1:1-Übersetzung von
    `app.autofinder.AutoFinderKandidat` (Runde 1), keine zweite Ranking-Logik."""

    # -- Identität --
    baureihe_id: str
    variante_id: str
    marke: str
    modell: str
    generation: str
    motor: str
    baujahr_von: int | None = None
    baujahr_bis: int | None = None
    leistung_ps: int | None = None
    kraftstoff: str
    getriebe: list[str] = Field(default_factory=list)
    antrieb: str | None = None
    karosserie: list[str] = Field(default_factory=list)

    # -- Ranking --
    match_score: float
    datenqualitaet: float
    match_gruende: list[str] = Field(default_factory=list)
    trade_offs: list[str] = Field(default_factory=list)

    # -- Herkunft --
    source_type: str = "internal_db"   # "internal_db" | "web_discovered" (Runde 2: immer internal_db)
    visual_key: str = ""

    # -- Markt (vorbereitet, §5/§13 — in Runde 2 IMMER None) --
    market_price_min: int | None = None
    market_price_max: int | None = None
    market_price_median: int | None = None
    market_data_quality: str | None = None
    market_sample_size: int | None = None

    # -- Spätere Suchfilter (vorbereitet, §5/§14 — in Runde 2 IMMER None) --
    such_filter_hinweis: AutoFinderSuchfilterHinweis | None = None


class AutoFinderResponse(BaseModel):
    """Antwort von POST /api/v1/autofinder."""
    status: str   # "ok" | "no_internal_match"
    kandidaten: list[AutoFinderKandidatOut] = Field(default_factory=list)
    total_candidates_considered: int = 0
    filters_applied: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    data_scope_hint: str
