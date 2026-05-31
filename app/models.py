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
