from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any


# ---------- Request ----------

class FahrzeugRequest(BaseModel):
    marke: str
    modell: str
    generation: str


class ChatMessage(BaseModel):
    rolle: str   # "user" | "ki"
    text: str


class ChatRequest(BaseModel):
    message: str
    verlauf: list[ChatMessage] = Field(default_factory=list)
    bild_base64: str | None = None   # Phase 1: Feld vorhanden, noch nicht verarbeitet
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


# ---------- Kauf-Check ----------

class KaufCheckRequest(BaseModel):
    # Strukturierte Inserat-Felder
    marke: str | None = None
    modell: str | None = None
    baujahr: int | None = None
    kilometerstand: int | None = None
    motor: str | None = None          # z.B. "320d", "2.0 TDI 150 PS"
    kraftstoff: str | None = None
    preis_eur: int | None = None
    ausstattung: list[str] = Field(default_factory=list)
    beschreibung: str | None = None   # Freitext-Beschreibung aus dem Inserat

    # Alternativ: Volltext des Inserats (Copy-Paste von mobile.de / AutoScout)
    freitext: str | None = None

    # Optional: Screenshot des Inserats
    bild_base64: str | None = None


class KaufCheckResponse(BaseModel):
    bericht: str                                   # Markdown-Bericht
    empfehlung: str                                # "kaufen" | "verhandeln" | "finger_weg" | "unbekannt"
    preis_bewertung: str                           # "guter_deal" | "fair" | "zu_teuer" | "unbekannt"
    marktpreis_min: int | None = None              # EUR
    marktpreis_max: int | None = None              # EUR
    baureihe_erkannt: str | None = None            # DB-ID der erkannten Baureihe
    motor_erkannt: str | None = None               # DB-ID der erkannten Motorvariante
    quelle: str                                    # "datenbank" | "web" | "gemischt"
    vertrauen: str                                 # "hoch" | "mittel" | "niedrig"
    belege: list[Any] = Field(default_factory=list)
