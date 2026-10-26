"""
Gezielte Abschlussprüfung (Fortsetzungsauftrag): die Wiederverwendung von
`gleiches_rueckruf_ereignis` (Token-Überlappung) allein beweist noch keine
sichere Kampagnenidentität. Zwei REPRODUZIERTE Lücken, jetzt behoben:

  1. Unterschiedliche explizite Kampagnen-/Referenznummern trotz ähnlichem,
     weitgehend formelhaftem Text und gleicher URL kollidierten bisher zu
     EINEM Fakt (`_rueckruf_referenz` erkennt die Nummer, ein Konflikt
     sperrt jede Zusammenführung -- auch über den normalen Bauteil-
     Vokabularschlüssel).
  2. Zwei Sätze, die ZUFÄLLIG denselben Bauteil-Vokabularschlüssel treffen,
     aber inhaltlich zu wenig teilen (unterschiedliche Fehler/Ursachen/
     Maßnahmen), kollidierten bisher STILLSCHWEIGEND (kein Ablehnungs-
     Eintrag, der zweite Claim verschwand einfach). Jetzt gilt Ereignis-
     gleichheit auch für den schlichten Schlüssel selbst -- außer der
     bestehende Kandidat trägt noch keinen substanziellen Inhalt (reine
     Kopf-/Titelzeile), dann bündelt es wie bisher.

Beide Sperren bewusst generisch (Regex auf übliche deutsche Referenz-
Bezeichnungen, Token-Überlappung) -- keine Fahrzeug-/Bauteil-/Kampagnen-
Sonderliste.

    python scripts/test_isolated.py test_rueckruf_buendelung_gegenpruefung.py
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.technical_research import _extrahiere_fakten, _rueckruf_referenz

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Reine Erkennung der Referenznummer ──────────────────────────────────────
check("_rueckruf_referenz erkennt 'Referenznummer 12345'",
      _rueckruf_referenz("Rückruf (Referenznummer 12345) für den Lexus RC F.") == "12345")
check("_rueckruf_referenz erkennt 'Rückrufcode AB-123'",
      _rueckruf_referenz("Siehe Rückrufcode AB-123 für Details.") == "AB-123")
check("_rueckruf_referenz liefert None ohne jede Referenzangabe",
      _rueckruf_referenz("Ein ganz normaler Rückrufsatz ohne Kennnummer.") is None)


# ── Fall 1: identische Aktion, zwei Bauteile -> EIN Hinweis (bereits belegt, ──
# hier als Grundlinie erneut geprüft, damit die Gegenprüfungen unten im
# selben Kontext bestehen bleiben) ──────────────────────────────────────────
_GLEICHE_AKTION = [t(
    "https://www.kba.de/rueckrufe/lexus-kraftstoffpumpe",
    "Rückruf Lexus RC F: Kraftstoffpumpe",
    "Rückruf für den Lexus RC F: Fahrzeuge der Baujahre 2015 bis 2017, deren Kraftstoffpumpe "
    "ausfallen kann, wodurch der Motor während der Fahrt ausgehen kann. Der Rückruf nennt als "
    "Ursache einen defekten Pulsationsdämpfer in der Kraftstoffpumpe, wodurch der Motor während "
    "der Fahrt ausgehen kann.")]
_f1 = _extrahiere_fakten(_GLEICHE_AKTION, kategorie="rueckruf", marke="Lexus", modell="RC F",
                         baujahr=2017, generation=None, abgelehnt=[])
check("Grundlinie: gleiche Aktion, zwei Bauteile -> EIN Hinweis",
      len(_f1) == 1, str([f.bauteil for f in _f1]))


# ── Fall 2: unterschiedliche explizite Kampagnen-IDs trotz ähnlichem Text ───
# und GLEICHER URL -> müssen GETRENNT bleiben.
_UNTERSCHIEDLICHE_IDS = [t(
    "https://www.kba.de/rueckrufe/lexus-sammelseite",
    "Rückrufe Lexus RC F",
    "Rückruf (Referenznummer 12345) für den Lexus RC F: Fahrzeuge, deren Kraftstoffpumpe "
    "ausfallen kann, wodurch der Motor während der Fahrt ausgehen kann. Rückruf "
    "(Referenznummer 67890) für den Lexus RC F: unabhängig vom ersten Rückruf kann die "
    "Kraftstoffpumpe ausfallen, wodurch der Motor während der Fahrt ausgehen kann.")]
_f2 = _extrahiere_fakten(_UNTERSCHIEDLICHE_IDS, kategorie="rueckruf", marke="Lexus", modell="RC F",
                         baujahr=2017, generation=None, abgelehnt=[])
check("Fall 2: unterschiedliche explizite Kampagnen-IDs trotz ähnlichem Text und gleicher "
      "URL -> bleiben GETRENNT (zwei Hinweise, nicht fälschlich zusammengeführt)",
      len(_f2) == 2, str([(f.bauteil, f.aussage[:60]) for f in _f2]))


# ── Fall 3: gleiches Bauteil, unterschiedliche Fehler/Ursachen -> KEINE ─────
# unbegründete Zusammenführung (reproduzierte stille Kollision, jetzt
# getrennt UND mit sichtbarem Fakt für beide statt stillem Verlust).
_GLEICHES_BAUTEIL_ANDERE_URSACHE = [t(
    "https://www.kba.de/rueckrufe/lexus-sammelseite-2",
    "Rückrufe Lexus RC F",
    "Rückruf für den Lexus RC F: die Kraftstoffpumpe kann undicht werden. Rückruf für den "
    "Lexus RC F: unabhängig davon kann die Kraftstoffpumpe einen Kabelbruch haben.")]
_f3 = _extrahiere_fakten(_GLEICHES_BAUTEIL_ANDERE_URSACHE, kategorie="rueckruf", marke="Lexus",
                         modell="RC F", baujahr=2017, generation=None, abgelehnt=[])
check("Fall 3: gleiches Bauteil, zwei eigenständige Fehlerursachen (undicht vs. Kabelbruch) "
      "-> bleiben GETRENNT, keiner geht mehr stillschweigend verloren",
      len(_f3) == 2, str([f.aussage for f in _f3]))


# ── Fall 4: widersprüchliche Aktionsidentität wird NICHT durch reine ────────
# Token-Ähnlichkeit übergangen -- auch wenn der Resttext nach Abzug der
# Referenznummer stark überlappt, entscheidet die Referenznummer allein.
_STARKE_TEXTUEBERLAPPUNG_ANDERE_ID = [t(
    "https://www.kba.de/rueckrufe/lexus-identischer-text",
    "Rückruf Lexus RC F",
    "Rückruf (Rückrufcode LX-001) für den Lexus RC F: Fahrzeuge der Baujahre 2015 bis 2017, "
    "deren Kraftstoffpumpe ausfallen kann, wodurch der Motor während der Fahrt ausgehen kann, "
    "wenn die Pumpe stark beansprucht wird. Rückruf (Rückrufcode LX-002) für den Lexus RC F: "
    "Fahrzeuge der Baujahre 2015 bis 2017, deren Kraftstoffpumpe ausfallen kann, wodurch der "
    "Motor während der Fahrt ausgehen kann, wenn die Pumpe stark beansprucht wird.")]
_f4 = _extrahiere_fakten(_STARKE_TEXTUEBERLAPPUNG_ANDERE_ID, kategorie="rueckruf", marke="Lexus",
                         modell="RC F", baujahr=2017, generation=None, abgelehnt=[])
check("Fall 4: nahezu identischer Text, aber EXPLIZIT unterschiedliche Rückrufcodes "
      "(LX-001 vs. LX-002) -> die Referenznummer entscheidet, NICHT die hohe "
      "Textüberlappung -- bleiben GETRENNT",
      len(_f4) == 2, str([f.aussage[:40] for f in _f4]))


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE RUECKRUF-BUENDELUNG-GEGENPRUEFUNGEN GRUEN")
