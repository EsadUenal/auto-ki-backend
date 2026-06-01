"""
LLM-gestützte Admin-Funktionen:
  - entwurf_erstellen(): Fahrzeug-Schema-Entwurf für 1 Generation
  - generationen_auflisten(): Batch — listet alle Generationen einer Baureihe auf
"""
from __future__ import annotations

import json
import re
from datetime import date

from google.genai import types as genai_types

from app.llm import _get_client
from app.config import LLM_MODEL
from app.gemini_retry import with_retry_sync, RateLimitExhausted  # noqa: F401 (re-export)

# ------------------------------------------------------------------ #
#  System-Prompt für Schema-Generierung                               #
# ------------------------------------------------------------------ #

_SCHEMA_SYSTEM = """Du bist ein präziser Automobil-Datenbankassistent.
Deine Aufgabe: Ein vollständiges Fahrzeug-Datenprofil als valides JSON erstellen.

PFLICHTREGELN:
1. Antworte NUR mit einem einzigen JSON-Objekt, kein Text davor oder danach, keine Markdown-Fences.
2. Unsichere oder geschätzte Werte (Preise, Verbrauch, CO2): als Zahl eintragen UND den Schlüssel "hinweise" im Wurzelobjekt mit einem Eintrag ergänzen, z.B. {"neupreis_ca_eur": "ca."}.
3. Unbekannte Felder → null. Niemals raten oder erfinden. Lieber null als falsch.
4. Harte Zahlen (PS, kW, Nm, Hubraum, 0-100, Vmax) nur eintragen, wenn du sie mit hoher Sicherheit kennst. Sonst null.
5. IDs automatisch generieren: baureihe_id = "{marke}-{modell}-{generation}" in Kleinbuchstaben mit Bindestrichen. variante_id = "{baureihe_id}-{bezeichnung-slug}".
6. Das Feld "letzte_aktualisierung" immer auf das heutige Datum (YYYY-MM) setzen.
7. "kraftstoff" nur aus: Benzin, Diesel, Elektro, Plug-in-Hybrid, Mild-Hybrid.
8. "antrieb" nur aus: Heck, Front, Allrad.
9. "schweregrad" in schwachstellen_baureihe nur aus: gering, mittel, hoch.
10. "typ" in ausstattungslinien nur aus: Basis, Ausstattungslinie, M Performance, Echtes M-Modell.

AUSGABE-SCHEMA (exakt diese Struktur, alle Felder vorhanden):
{
  "id": "...",
  "marke": "...",
  "modell": "...",
  "generation": "...",
  "bauzeitraum_von": <Zahl|null>,
  "bauzeitraum_bis": <Zahl|null>,
  "karosserie": ["..."],
  "segment": "...",
  "vorgaenger": <"..."|null>,
  "erkennung_generation": "...",
  "facelift_merkmale": <"..."|null>,
  "adac_pannenkennziffer": <"..."|null>,
  "tuev_maengelquote": <"..."|null>,
  "dekra_urteil": <"..."|null>,
  "euro_ncap_sterne": <0-5|null>,
  "euro_ncap_jahr": <Zahl|null>,
  "wartung_oel_km": <Zahl|null>,
  "wartung_hu_intervall": <"..."|null>,
  "kaufberatung": <"..."|null>,
  "letzte_aktualisierung": "YYYY-MM",
  "ausstattungslinien": [
    {"name":"...","typ":"...","optische_merkmale":"...","abgrenzung":<"..."|null>}
  ],
  "schwachstellen_baureihe": [
    {"bauteil":"...","beschreibung":"...","betroffene_baujahre":"...","schweregrad":"gering|mittel|hoch"}
  ],
  "rueckrufe": [
    {"datum":"...","betroffene_baujahre":"...","mangel":"...","abhilfe":"...","kba_referenz":<"..."|null>}
  ],
  "quellen": [],
  "motoren": [
    {
      "variante_id": "...",
      "bezeichnung": "...",
      "motorcode": <"..."|null>,
      "kraftstoff": "...",
      "hubraum_ccm": <Zahl|null>,
      "zylinder": <Zahl|null>,
      "leistung_ps": <Zahl|null>,
      "leistung_kw": <Zahl|null>,
      "drehmoment_nm": <Zahl|null>,
      "getriebe": ["..."],
      "antrieb": "Heck|Front|Allrad",
      "beschleunigung_0_100": <Zahl|null>,
      "vmax_kmh": <Zahl|null>,
      "verbrauch_wltp": <Zahl|null>,
      "verbrauch_real": <Zahl|null>,
      "co2_g_km": <Zahl|null>,
      "neupreis_ca_eur": <Zahl|null>,
      "heck_emblem": <"..."|null>,
      "optische_unterscheidung": <"..."|null>,
      "schwachstellen_motor": [
        {"bauteil":"...","beschreibung":"...","baujahre":<"..."|null>,"kosten_ca":<"..."|null>}
      ],
      "kritische_wartung": [
        {"bauteil":"...","intervall":"...","hinweis":<"..."|null>}
      ]
    }
  ],
  "hinweise": {}
}"""

_BATCH_SYSTEM = """Du bist ein Automobil-Experte.
Deine Aufgabe: Für eine gegebene Modellreihe alle offiziellen Baureihen-Generationen auflisten.

Antworte NUR mit einem JSON-Array, kein Text davor oder danach, keine Markdown-Fences.
Format: [{"marke":"...","modell":"...","generation":"...","baujahr_von":YYYY,"baujahr_bis":YYYY_oder_null}]
Nur tatsächliche Generationen, keine Facelift-Varianten als eigene Generation."""


def _extract_json(text: str) -> dict | list:
    """Extrahiert JSON aus der LLM-Antwort, auch wenn Markdown-Fences vorhanden sind."""
    text = text.strip()
    # Markdown-Fences entfernen
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


async def entwurf_erstellen(marke: str, modell: str, generation: str) -> dict:
    """Lässt Gemini ein vollständiges Fahrzeug-Schema ausfüllen."""
    client = _get_client()
    heute  = date.today().strftime("%Y-%m")

    prompt = (
        f"Erstelle ein vollständiges Datenprofil für: {marke} {modell} {generation}.\n"
        f"Heutiges Datum (für letzte_aktualisierung): {heute}.\n"
        "Fülle alle Felder die du kennst aus. Unbekannte Felder → null. "
        "Unsichere Zahlen (Preis, Verbrauch, CO2) in 'hinweise' markieren."
    )

    cfg = genai_types.GenerateContentConfig(
        system_instruction=_SCHEMA_SYSTEM, temperature=0.1
    )
    response = with_retry_sync(lambda: client.models.generate_content(
        model=LLM_MODEL,
        contents=[{"role": "user", "parts": [{"text": prompt}]}],
        config=cfg,
    ))

    data = _extract_json(response.text)

    # Sicherheitsnetz: Pflichtfelder erzwingen
    data.setdefault("marke", marke)
    data.setdefault("modell", modell)
    data.setdefault("generation", generation)
    data.setdefault("hinweise", {})
    data.setdefault("letzte_aktualisierung", heute)

    return data


async def generationen_auflisten(anfrage: str) -> list[dict]:
    """
    Für Batch-Modus: 'alle BMW 4er Generationen' →
    [{"marke":"BMW","modell":"4er","generation":"F32","baujahr_von":2013,"baujahr_bis":2020}, ...]
    """
    client = _get_client()

    cfg = genai_types.GenerateContentConfig(
        system_instruction=_BATCH_SYSTEM, temperature=0.1
    )
    response = with_retry_sync(lambda: client.models.generate_content(
        model=LLM_MODEL,
        contents=[{"role": "user", "parts": [{"text": anfrage}]}],
        config=cfg,
    ))

    return _extract_json(response.text)
