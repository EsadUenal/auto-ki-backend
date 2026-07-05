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
    debug_build: str | None = None  # TEMPORÄR: Instanz-Verifikation, vor Prod-Release entfernen


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
