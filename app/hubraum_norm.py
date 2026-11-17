"""Hubraum in cm³/ccm -> Liter: EINE Erkennung und Umrechnung für alle Pfade.

Befund (Offline-Probe 16.11.2026): "4.969 cm³" (Tausenderpunkt, wie in deutschen Fachtexten üblich) wurde als "969 cm³" gelesen
und ergab 1.0 statt 5.0; ein Satz mit "5,0 Liter V8 (4.969 cm³)" zählte dadurch als mehrdeutig. Dieselbe Erkennung nutzen die
Hubraum-Claims und die Motorkern-Prüfung (app/technical_research.py), das Label/Wert-Muster und die Inserat-Erkennung
(app/vehicle_identity.py): keine zweite, abweichende Normalisierung.

Gelesen werden:
  * ungruppierte Werte mit 3-5 Ziffern ("4969 cm³", "998 ccm")
  * Werte mit GENAU EINER Dreiergruppe nach 1-2 Ziffern, getrennt durch Punkt, Komma, Leerzeichen, geschütztes Leerzeichen
    (U+00A0), schmales geschütztes Leerzeichen (U+202F) oder Schmalraum (U+2009): "4.969", "4,969", "4 969", "1.998"
Nicht verändert: Liter-Dezimalangaben ("5,0 Liter", "5.0L"), Angaben ohne cm³-Einheit. Keine pauschale Entfernung von Satzzeichen:
nur eine Gruppierung "1-2 Ziffern + Trenner + 3 Ziffern" direkt vor der Einheit zählt als Tausenderschreibweise.
"""
from __future__ import annotations

import re

_TRENNER = r"[.,    ]"
# Zahl: gruppiert (1-2 Ziffern, Trenner, 3 Ziffern, danach keine weitere Ziffer) ODER ungruppiert (3-5 Ziffern).
CCM_ZAHL = r"\d{1,2}" + _TRENNER + r"\d{3}(?!\d)|\d{3,5}"
_EINHEIT = r"(?:cm\s*3|cm\s*³|ccm)"

# Gruppe 1 = die Zahl samt Trenner. `(?<!\d[.,])`: kein Start mitten in einer Dezimalzahl ("4.9691 cm³" -> kein "9691").
CCM_RE = re.compile(r"\b(?<!\d[.,])(" + CCM_ZAHL + r")\s*" + _EINHEIT + r"\b", re.IGNORECASE)


def ccm_als_liter(ccm_text: str) -> str:
    """"4969" / "4.969" / "4 969" -> "5.0" (kaufmännische Rundung auf eine Nachkommastelle, wie bisher)."""
    ziffern = re.sub(r"\D", "", ccm_text or "")
    liter = round(int(ziffern) / 1000, 1)
    return f"{liter:.1f}"
