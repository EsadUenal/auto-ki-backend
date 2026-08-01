from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any

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
    gruende: list[str] = Field(default_factory=list)


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


class Insight(BaseModel):
    """Eine nachvollziehbare Erkenntnis mit Herkunft (Provenance).

    Wiederverwendbar für Kauf- und Verkaufscheck. `confidence` ist bewusst dreistufig
    ("hoch" | "mittel" | "niedrig") — KEINE scheinpräzisen Prozentwerte.
    """
    id: str
    kategorie: str                    # "schwachstelle" | "rueckruf" | "motorproblem" | "marktvergleich" | ...
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
    # (wie schlimm): "exakt" | "wahrscheinlich" | "unklar". Ein Baureihen-Rückruf,
    # dessen Varianten-/Antriebs-Zuordnung nicht gesichert ist, ist "unklar" —
    # NICHT automatisch "betrifft dein Fahrzeug".
    applicability: str | None = None
    einfluss: str | None = None       # Einfluss auf die Empfehlung / Preis / Strategie
    # Nur beim Marktvergleich-Insight gesetzt: der strukturierte, deterministisch
    # berechnete Marktvergleich (Median, robuste Spanne, verwendete Datenpunkte).
    marktanalyse: Marktanalyse | None = None


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


# ---------- Verkaufs-Check ----------

class VerkaufsCheckRequest(BaseModel):
    marke: str | None = Field(default=None, max_length=100)
    modell: str | None = Field(default=None, max_length=100)
    baujahr: int | None = None
    kilometerstand: int | None = None
    motor: str | None = Field(default=None, max_length=200)
    kraftstoff: str | None = Field(default=None, max_length=100)
    ausstattung: list[str] = Field(default_factory=list, max_length=100)
    beschreibung: str | None = Field(default=None, max_length=_MAX_TEXT_LEN)        # Zustand, Besonderheiten (Nichtraucher, Scheckheft, ...)
    maengel: list[str] = Field(default_factory=list, max_length=100)  # bekannte Mängel / anstehende Reparaturen
    preis_vorstellung: int | None = None   # eigene Preisvorstellung des Verkäufers (optional)

    # Zusätzliche Angaben — verbessern die Preiseinschätzung deutlich
    unfallfrei: str | None = Field(default=None, max_length=20)      # "ja" | "nein" | "unbekannt"
    vorbesitzer: int | None = None
    tuev_bis: str | None = Field(default=None, max_length=20)        # z.B. "06/2027"
    scheckheftgepflegt: bool | None = None

    freitext: str | None = Field(default=None, max_length=_MAX_TEXT_LEN)            # alternative Freitexteingabe
    bild_base64: str | None = Field(default=None, max_length=_MAX_BILD_B64_LEN)


class VerkaufsCheckResponse(BaseModel):
    bericht: str                                   # Markdown-Bericht
    schnellverkaufs_preis: int | None = None       # unteres Ende — zügiger Verkauf
    maximal_preis: int | None = None               # oberes realistisches Ende
    empfohlener_preis: int | None = None           # empfohlene Mitte
    verkaufsdauer_tage_schnell: int | None = None  # geschätzte Tage bei Schnellverkauf
    verkaufsdauer_tage_maximal: int | None = None  # geschätzte Tage bei Maximalpreis
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
