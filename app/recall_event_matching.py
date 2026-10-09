from __future__ import annotations

"""
Generische "ist das dieselbe Rückrufmeldung?"-Pruefung — gemeinsame Utility
fuer zwei unabhaengige Konsumenten:

  * app/technical_research.py (Paket A, RC-W1): ob eine scope-lose Teaser-
    Quelle dieselbe Meldung beschreibt wie ein bereits per Baujahr/Hubraum/
    Leistung ausgeschlossener Artikel.
  * app/kaufcheck.py (Paket B, RC-W6, Abschnitt 9): ob ein Web-Rückruf-Fakt
    dieselbe amtliche KBA-Meldung beschreibt wie ein bereits gefundener
    canonical-only Rueckruf — dann ist die amtliche Quelle primaer, der
    Web-Fakt wird nicht zusaetzlich als eigener Insight ausgegeben.

Extrahiert statt an Ort und Stelle neu erfunden oder privat zwischen Modulen
importiert — beide Konsumenten vergleichen "Inhaltstragende Tokens einer
Aussage, abzueglich Marke/Modell/Bauteil/Rückruf-Vokabular/Füllwörter"
nach demselben, bewusst konservativen Mindestueberlappungs-Kriterium.
"""

_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                          "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def _norm(text: str | None) -> str:
    import re
    t = (text or "").strip().lower().translate(_UMLAUTE)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _tokens(text: str | None) -> set[str]:
    return {t for t in _norm(text).split() if t}


_FUELLWOERTER = frozenset({
    "der", "die", "das", "des", "dem", "den", "ein", "eine", "einen", "einem", "einer",
    "und", "oder", "fuer", "von", "im", "am", "zu", "ist", "sind", "auch", "nur", "bei",
    "aus", "auf", "als", "mit", "nach", "vor", "ueber", "unter", "sich", "es", "er", "sie",
})

# Wie viele INHALTSTRAGENDE Tokens (nicht Marke/Modell/Bauteil/Rückruf-
# Vokabular) zwei Rückruf-Aussagen teilen müssen, um als dieselbe Meldung zu
# gelten. Bauteil ALLEIN reicht nicht — zwei unabhängige Rückrufe desselben
# Bauteils dürfen sich nicht gegenseitig beeinflussen. Bewusst konservativ:
# im Zweifel NICHT als dasselbe Ereignis gelten.
MIN_GLEICHHEIT_TOKENS = 2


def inhaltstokens(satz: str, marke: str | None, modell: str | None,
                  bauteil: str | None = None) -> set[str]:
    """Die INHALTSTRAGENDEN Tokens eines Satzes — alles ausser Marke, Modell,
    Bauteil, dem Rückruf-Vokabular und generischen Füllwörtern."""
    kern = _tokens(marke) | _tokens(modell) | _tokens(bauteil)
    uebrig = _tokens(satz) - _FUELLWOERTER - {"rueckruf", "recall", "rueckrufaktion"} - kern
    # Kompositum-Flexionsformen ("kraftstoffleitungs" aus "Kraftstoffleitungs-
    # Rückruf") zaehlen ebenfalls zum Kern — exakte Mengendifferenz reicht
    # dafuer nicht, deshalb zusaetzlich ein fuzzy Praefix-Abgleich.
    return {t for t in uebrig
           if not any(len(k) >= 4 and (t.startswith(k) or k.startswith(t)) for k in kern)}


def gleiches_rueckruf_ereignis(kandidat_tokens: set[str],
                               andere_tokens: "list[set[str]] | set[str]") -> bool:
    """Ob `kandidat_tokens` mit MINDESTENS EINEM Token-Set aus `andere_tokens`
    ausreichend (>= MIN_GLEICHHEIT_TOKENS) ueberlappt. `andere_tokens` darf
    ein einzelnes Set oder eine Liste von Sets sein."""
    sets = [andere_tokens] if isinstance(andere_tokens, set) else list(andere_tokens)
    return any(len(kandidat_tokens & ex) >= MIN_GLEICHHEIT_TOKENS for ex in sets)
