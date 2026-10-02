"""
Unfall-Tri-State END-TO-END (Release-Hardening, Root Cause 7).

BEFUND-PRÜFUNG (Auftrag, Production Smoke 2)
---------------------------------------------
Der Auftrag beschreibt einen Production-Report, der bei Unfallstatus "Nicht
angegeben" trotzdem "Laut Inserat unfallfrei" samt Dokumenten-Aktion
"Unfallfreiheit schriftlich festhalten" ausgegeben haben soll, und verlangt,
VOR jeder Änderung den tatsächlich gesendeten Wert zu verifizieren.

Verifiziert (Frontend-Quelle, auto-ki-web/src/components/KaufCheckView.tsx):
die Dropdown-Option "Nicht angegeben" sendet `unfallfrei=""` (leerer String),
NICHT den Text "nicht angegeben" und NICHT "unbekannt" — das ist die dritte,
separate Option "Im Inserat nicht erwähnt" (`unfallfrei="unbekannt"`).

Ein direkter Produktions-DB-Zugriff (um den TATSÄCHLICH gesendeten Wert jenes
konkreten historischen Laufs zu lesen) war in dieser Sitzung durch die
Sandbox-Policy blockiert ("Production Reads") — das ist im Abschlussbericht
als Grenze dokumentiert. Diese Suite prüft stattdessen, ob der mit dem
Frontend-Code tatsächlich sendbare Wertebereich (""/"unbekannt"/"ja"/"nein"
+ beliebiger Freitext) über die GESAMTE sichtbare Kette hinweg korrekt bleibt
— generisch, ohne eine einzige Marke/Modell/Generation zu nennen.

  A) unfallfrei="" (Dropdown "Nicht angegeben") -> UNKNOWN ueberall
  B) unfallfrei="unbekannt" (Dropdown "Im Inserat nicht erwähnt") -> UNKNOWN
  C) unfallfrei="ja" (Dropdown "Laut Inserat unfallfrei") -> UNFALLFREI-Pfad
  D) unfallfrei="nein" (Dropdown "Unfallschaden angegeben") -> UNFALL-Pfad
  E) Widerspruch: Struktur "ja", Freitext behauptet Unfallschaden -> UNKNOWN
  F) Freitext allein, ausdrückliche Unsicherheit -> UNKNOWN trotz Erwähnung
  G) Freitext allein, eindeutige Behauptung -> UNFALLFREI/UNFALL auch ohne
     strukturierten Wert

    python test_unfall_tristate_e2e.py
"""
from app.bekannte_fakten import UNFALL, UNFALLFREI, UNKNOWN, unfall_detail, unfall_status
from app.kaufaktionen import DOKUMENTE, VERKAEUFERFRAGEN, build_kaufaktionen
from app.models import KaufCheckRequest

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def req(**kw) -> KaufCheckRequest:
    """Generisches Fahrzeug — bewusst keine reale Marke/Modell/Generation, um
    jeden Verdacht auf einen Spezialfall-Patch auszuschliessen."""
    basis = dict(marke="Testmarke", modell="Testmodell", baujahr=2020,
                kilometerstand=50_000, preis_eur=15_000)
    basis.update(kw)
    return KaufCheckRequest(**basis)


def dokumente_titel(request) -> set[str]:
    plan = build_kaufaktionen(request, None, None, [])
    return {p.titel for p in plan.dokumente.fahrzeugspezifisch}


def verkaeuferfragen_titel(request) -> set[str]:
    plan = build_kaufaktionen(request, None, None, [])
    return {p.titel for p in plan.verkaeuferfragen.fahrzeugspezifisch}


UNFALLFREI_DOKUMENT = "Unfallfreiheit schriftlich festhalten"
UNFALL_DOKUMENT = "Unfallreparatur dokumentieren lassen"
UNFALL_FRAGE = "Welche Schäden, Nachlackierungen oder Reparaturen gab es?"


# ══════════════════════════════════════════════════════════════════════════
print("\n=== A) unfallfrei=\"\" (Dropdown \"Nicht angegeben\") -> UNKNOWN ueberall ===")
r_a = req(unfallfrei="", beschreibung="Servicehistorie vollständig vorhanden. Tuning nicht angegeben.")
check("A1 unfall_status == UNKNOWN", unfall_status(r_a) == UNKNOWN)
check("A2 unfall_detail.state == unknown", unfall_detail(r_a).unknown)
check("A3 Dokumente nennen NICHT 'Unfallfreiheit schriftlich festhalten'",
      UNFALLFREI_DOKUMENT not in dokumente_titel(r_a))
check("A4 Dokumente nennen NICHT 'Unfallreparatur dokumentieren lassen'",
      UNFALL_DOKUMENT not in dokumente_titel(r_a))
check("A5 Verkäuferfrage zur Unfallhistorie wird stattdessen gestellt",
      UNFALL_FRAGE in verkaeuferfragen_titel(r_a))


# ══════════════════════════════════════════════════════════════════════════
print("\n=== B) unfallfrei=\"unbekannt\" (Dropdown \"Im Inserat nicht erwähnt\") -> UNKNOWN ===")
r_b = req(unfallfrei="unbekannt")
check("B1 unfall_status == UNKNOWN", unfall_status(r_b) == UNKNOWN)
check("B2 Dokumente nennen NICHT 'Unfallfreiheit schriftlich festhalten'",
      UNFALLFREI_DOKUMENT not in dokumente_titel(r_b))
check("B3 Verkäuferfrage zur Unfallhistorie wird gestellt",
      UNFALL_FRAGE in verkaeuferfragen_titel(r_b))


# ══════════════════════════════════════════════════════════════════════════
print("\n=== C) unfallfrei=\"ja\" (Dropdown \"Laut Inserat unfallfrei\") -> UNFALLFREI-Pfad ===")
r_c = req(unfallfrei="ja")
check("C1 unfall_status == UNFALLFREI", unfall_status(r_c) == UNFALLFREI)
check("C2 Dokumente nennen 'Unfallfreiheit schriftlich festhalten'",
      UNFALLFREI_DOKUMENT in dokumente_titel(r_c))
check("C3 KEINE offene Verkäuferfrage zur Unfallhistorie mehr",
      UNFALL_FRAGE not in verkaeuferfragen_titel(r_c))


# ══════════════════════════════════════════════════════════════════════════
print("\n=== D) unfallfrei=\"nein\" (Dropdown \"Unfallschaden angegeben\") -> UNFALL-Pfad ===")
r_d = req(unfallfrei="nein")
check("D1 unfall_status == UNFALL", unfall_status(r_d) == UNFALL)
check("D2 Dokumente nennen 'Unfallreparatur dokumentieren lassen'",
      UNFALL_DOKUMENT in dokumente_titel(r_d))


# ══════════════════════════════════════════════════════════════════════════
print("\n=== E) Widerspruch: Struktur 'ja' (unfallfrei), Freitext behauptet Unfallschaden ===")
r_e = req(unfallfrei="ja", beschreibung="Das Fahrzeug ist nicht unfallfrei, ein Schaden wurde repariert.")
check("E1 unfall_status == UNKNOWN (Widerspruch, ENFAL entscheidet nicht)",
      unfall_status(r_e) == UNKNOWN)
check("E2 Konflikt ist am TriState vermerkt", unfall_detail(r_e).konflikt)
check("E3 Dokumente behaupten NICHT 'Unfallfreiheit schriftlich festhalten'",
      UNFALLFREI_DOKUMENT not in dokumente_titel(r_e))


# ══════════════════════════════════════════════════════════════════════════
print("\n=== F) Freitext allein, ausdrückliche Unsicherheit -> UNKNOWN trotz Erwähnung ===")
r_f = req(unfallfrei=None,
          beschreibung="Ob das Fahrzeug unfallfrei ist, ist nicht abschließend bekannt.")
check("F1 unfall_status == UNKNOWN (Unsicherheitsmarker dominiert)",
      unfall_status(r_f) == UNKNOWN)
r_f2 = req(unfallfrei=None, beschreibung="Keine eindeutige Angabe zu Unfallschäden vorhanden.")
check("F2 'keine eindeutige Angabe zu Unfallschäden' -> UNKNOWN (nicht UNFALL)",
      unfall_status(r_f2) == UNKNOWN)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== G) Freitext allein, eindeutige Behauptung -> wirkt auch ohne Strukturfeld ===")
r_g1 = req(unfallfrei=None, beschreibung="Das Fahrzeug ist laut Vorbesitzer unfallfrei.")
check("G1 eindeutige Freitext-Behauptung 'unfallfrei' -> UNFALLFREI",
      unfall_status(r_g1) == UNFALLFREI)
r_g2 = req(unfallfrei=None, beschreibung="Das Fahrzeug ist nicht unfallfrei und wurde nach "
                                         "einem Schaden repariert.")
check("G2 eindeutige Freitext-Behauptung 'nicht unfallfrei' -> UNFALL",
      unfall_status(r_g2) == UNFALL)


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE UNFALL-TRISTATE-E2E-TESTS GRUEN")
