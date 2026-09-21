"""Qualitätstor für Webtreffer, die als MARKTPREIS-Quelle angezeigt werden.

RC1-BEFUND
----------
Für einen BMW 330i G20 zeigte der KaufCheck als Marktquellen: eine mobile.de-
Seite mit "Zugriff verweigert", ein PicClick-Teileangebot (Niveauregulierung),
eine Verkaufsseite für ein Ultraleichtflugzeug ("Dreiachser") und ein
BMW-X3-Leasingangebot. Die Marktanalyse selbst hatte daraus korrekt KEINEN
Preis abgeleitet — die Seiten wurden trotzdem als Belege ausgespielt.

Ursache: der bisherige Anzeige-Filter (`marktvergleich.modell_relevant`) warf
nur Seiten hinaus, die ein FREMDES Modell nennen. Seiten ohne jedes
Modellsignal galten als "neutral" und blieben stehen — Fehlerseiten,
Teileshops und fachfremde Seiten haben aber genau KEIN Modellsignal.

REGEL (umgekehrt)
-----------------
Eine Seite zählt nur dann als Marktquelle, wenn sie POSITIV zum gesuchten
Fahrzeug passt, und nie, wenn sie erkennbar keine Gebrauchtwagen-Preisquelle
ist:

  1. Keine Fehler-/Sperrseite (Access denied, Captcha, 403, "nicht gefunden").
  2. Kein Teile-/Zubehörangebot.
  3. Kein Leasing-/Neuwagen-Ratenangebot — das sind keine Gebrauchtpreise.
  4. Marke UND Modell (oder eine Motor-Verkaufsbezeichnung des Modells) werden
     auf der Seite genannt.
  5. Kein erkennbares Fremdmodell (bestehende Prüfung aus marktvergleich).

Kein Fahrzeug-Sonderfall: alle Signale sind generische Seitentypen bzw.
datengetriebene Modell-Tokens aus `baue_ziel`.
"""
from __future__ import annotations

import re

from app.marktvergleich import _ist_fremdmodell, _wort_tokens

_FEHLERSEITE = re.compile(
    r"access denied|zugriff verweigert|captcha|are you a robot|bist du ein mensch|"
    r"\b403\b|forbidden|seite nicht gefunden|page not found|\b404\b|"
    r"enable javascript|javascript aktivieren|checking your browser|"
    r"quoting the displayed ref id|request blocked",
    re.IGNORECASE)

_TEILE = re.compile(
    r"autoteile|ersatzteil|auto-motorrad-teile|teile\s*(?:&|und)\s*zubeh|zubehör|zubehoer|"
    r"niveauregulierung|/teile/|federung[-\s]lenkung|felgen[-\s]?shop|reifen[-\s]?shop",
    re.IGNORECASE)

_LEASING = re.compile(r"\bleasing\b|leasingmarkt|leasingrate|monatsrate", re.IGNORECASE)

_MIN_INHALT = 80

# Antriebs-/Ausstattungskuerzel, die in `modell_tokens` landen koennen, aber
# kein MODELL belegen: "xDrive" steht beim X3 genauso wie beim 3er.
_SCHWACHE_TOKENS = frozenset({"xdrive", "sdrive", "quattro", "4matic", "4motion",
                              "awd", "4x4", "hybrid", "diesel", "benzin"})


def _text(r: dict) -> str:
    return f"{r.get('title', '')} {r.get('url', '')} {r.get('content', '')}"


def ablehnungsgrund(r: dict, ziel: dict, marke: str | None) -> str | None:
    """None = als Marktquelle geeignet, sonst ein kurzer Grund (für Logs/Tests)."""
    titel_url = f"{r.get('title', '')} {r.get('url', '')}"
    inhalt = (r.get("content") or "").strip()
    if _FEHLERSEITE.search(titel_url) or _FEHLERSEITE.search(inhalt[:400]):
        return "fehlerseite"
    if len(inhalt) < _MIN_INHALT and not (r.get("title") or "").strip():
        return "kein_inhalt"
    if _TEILE.search(titel_url):
        return "teileangebot"
    if _LEASING.search(titel_url):
        return "leasing"
    worte = _wort_tokens(_text(r))
    marke_n = (marke or "").strip().lower()
    if marke_n and marke_n not in worte and marke_n.replace("-", " ") not in _text(r).lower():
        return "marke_fehlt"
    modell_tokens = (ziel.get("modell_tokens") or set()) - _SCHWACHE_TOKENS
    if modell_tokens and not (worte & modell_tokens):
        return "modell_fehlt"
    if _ist_fremdmodell(worte, ziel) is not None and not (worte & modell_tokens):
        return "fremdmodell"
    return None


def geeignete_marktquellen(results: list[dict], ziel: dict, marke: str | None) -> list[dict]:
    return [r for r in results or [] if ablehnungsgrund(r, ziel, marke) is None]
