from __future__ import annotations

from pydantic import BaseModel, Field, field_validator
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
    # Reliability-Sprint: Ergebnis der Quality-Gate-Pipeline (§1/§14).
    # "completed_high" (Normalfall) | "completed_medium" (selten) — ein
    # "research_failed" wird NICHT als fertiger Check ausgeliefert (Kontingent
    # erstattet). Default für Alt-Checks: completed_high.
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
    # "completed_medium"; "research_failed" wird nicht als fertiger Check geliefert.
    research_status: str = "completed_high"


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
