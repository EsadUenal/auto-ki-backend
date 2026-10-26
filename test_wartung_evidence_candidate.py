"""
Wartungs-Evidence-Schicht (Source-Trust-Fix, Scope: NUR kategorie=="wartung").

BEFUND (Audit-Sitzung Wartungs-Quellenvertrauen)
-------------------------------------------------
`_extrahiere_fakten()` verwirft jeden Treffer mit score_domain < MIN_SCORE_FAKT
komplett, bevor dessen Inhalt gelesen wird ("source_quality"). Für "wartung"
traf das real Werkstatt-/Teileportale (repareo.de, autodoc.de -- hier NUR als
Testfixture-Namen, siehe app/technical_research.py für den Produktionscode
ohne jeden Domain-Hardcode), die oft die konkretesten motor-/generations-
spezifischen Werte lieferten, während Hersteller-Landingpages häufig GAR
KEINE modellscharfe Zahl nennen.

FIX (app/technical_research.py, `_wartung_candidate_fakten` + Helfer):
Treffer unterhalb der Schwelle werden NICHT verworfen, sondern als CANDIDATE
EVIDENCE geprüft und NUR zu einem bestätigten WebFakt, wenn mindestens ZWEI
unabhängige Source-Families (eTLD+1-Näherung) denselben Bauteil-/Regime-
Schlüssel mit demselben Intervall belegen und die Applicability (Marke/
Modell/Generation/Motor) passt. Betrifft AUSSCHLIESSLICH "wartung" -- der
bestehende Gate-Pfad (score >= MIN_SCORE_FAKT) bleibt für jede Kategorie
unverändert.

    python test_wartung_evidence_candidate.py
"""
from app.technical_research import _ziel, phase_fakten

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def _treffer(url: str, titel: str, inhalt: str) -> dict:
    return {"url": url, "title": titel, "content": inhalt}


class _Ident:
    def __init__(self, generation=None):
        self.generation = generation


def _lauf(treffer_liste: list[dict], *, marke: str, modell: str, motor: str | None = None,
         generation: str | None = None, getriebe: str | None = None, kategorie: str = "wartung"):
    extra = {}
    if generation:
        extra["generation"] = generation
    if getriebe:
        extra["getriebe"] = getriebe
    z = _ziel(marke, modell, 2019, motor, extra or None)
    abgelehnt: list[dict] = []
    fakten = phase_fakten({kategorie: treffer_liste}, z, _Ident(generation), (kategorie,), abgelehnt)
    return fakten, abgelehnt


def _gruende(abgelehnt: list[dict]) -> set[str]:
    return {a.get("grund") for a in abgelehnt}


# Produktentscheidung (Fortsetzungsauftrag, "Gestützte Wartungshinweise
# sichtbar machen"): CORROBORATED (>=2 unabhängige, nicht-UNKNOWN Secondary-
# Familien, gleicher Wert, kein Primärbeleg) erzeugt jetzt einen EIGENEN,
# sichtbaren WebFakt (beleglage="uebereinstimmend_unbestaetigt",
# confidence="niedrig") statt intern verworfen zu werden
# ("wartung_corroborated_not_verified" existiert als Ablehnungsgrund NICHT
# mehr für diesen Fall). Der Evidenzstatus selbst bleibt unverändert
# CORROBORATED -- NIE "verified", NIE vermischt mit einem echten Primärbeleg.
# Siehe test_wartung_corroborated_sichtbar.py für die End-to-End-Sicht
# (Bericht, Einfluss-Text, Schutzregeln). Dieser Helfer hält die hiesigen
# Assertions auf das eigentlich Interessante fokussiert: IST es sichtbar,
# UND bleibt es als CORROBORATED (nicht VERIFIED) gekennzeichnet.
def _corroborated(fakten: list, bauteil_enthaelt: str | None = None):
    kandidaten = [f for f in fakten if getattr(f, "beleglage", None) == "uebereinstimmend_unbestaetigt"]
    if bauteil_enthaelt is not None:
        kandidaten = [f for f in kandidaten if bauteil_enthaelt in (f.bauteil or "")]
    return kandidaten


print("=" * 78)
print("A) PRIMARY + eindeutige Applicability -> VERIFIED (bestehender Pfad, unveraendert)")
print("=" * 78)
treffer = _treffer("https://www.volkswagen.at/inspektion-golf", "VW Golf Inspektion",
                   "Der Zahnriemen wird beim 2.0 TDI alle 210.000 km gewechselt.")
fakten, abgelehnt = _lauf([treffer], marke="VW", modell="Golf", motor="2.0 TDI")
check("Hersteller-Quelle allein ergibt einen bestätigten Wartungsfakt",
      len(fakten) == 1, f"fakten={fakten}")
check("Confidence mind. 'mittel' (Tier-1-Bonus, bestehendes Verhalten)",
      bool(fakten) and fakten[0].confidence in ("mittel", "hoch"))

print("\n" + "=" * 78)
print("B) SECONDARY allein -> UNVERIFIED")
print("=" * 78)
repareo_zahnriemen = _treffer("https://www.repareo.de/inspektion-vw-golf-7", "VW Golf 7 Inspektion",
                              "Der Zahnriemen wird beim 2.0 TDI alle 210.000 km gewechselt.")
fakten, abgelehnt = _lauf([repareo_zahnriemen], marke="VW", modell="Golf", motor="2.0 TDI")
check("Einzelne Secondary-Quelle erzeugt KEINEN bestätigten Wartungsfakt",
      len(fakten) == 0, f"fakten={fakten}")
check("Candidate ist als 'wartung_candidate_unverified' sichtbar (Observability)",
      "wartung_candidate_unverified" in _gruende(abgelehnt))

print("\n" + "=" * 78)
print("C) SECONDARY A + unabhängige SECONDARY B, gleicher Wert -> AGREEMENT=MATCH,")
print("   aber NICHT automatisch VERIFIED (Härtungsrunde: CORROBORATED, intern)")
print("=" * 78)
autoteile_zahnriemen = _treffer("https://www.autoteile-xyz.de/golf-zahnriemen", "VW Golf 7 Wartung",
                                "Der Zahnriemen wird beim 2.0 TDI alle 210.000 km gewechselt.")
fakten, abgelehnt = _lauf([repareo_zahnriemen, autoteile_zahnriemen],
                         marke="VW", modell="Golf", motor="2.0 TDI")
check("Zwei Secondary-Quellen alleine erzeugen jetzt einen sichtbaren, aber klar "
      "als CORROBORATED (nicht VERIFIED) markierten Hinweis",
      len(_corroborated(fakten)) == 1, f"fakten={fakten}")
check("Confidence bleibt niedrig, Evidenzstatus bleibt CORROBORATED (kein VERIFIED)",
      bool(_corroborated(fakten)) and _corroborated(fakten)[0].confidence == "niedrig")

print("\n" + "=" * 78)
print("D) zwei URLs gleiche Root-Domain -> zählen als 1 Quelle")
print("=" * 78)
autodoc_info = _treffer("https://www.autodoc.de/info/vw-golf-zahnriemen", "VW Golf Zahnriemen",
                        "Der Zahnriemen wird beim 2.0 TDI alle 210.000 km gewechselt.")
autodoc_club = _treffer("https://club.autodoc.de/manuals/wartung/vw/golf", "VW Golf Wartungsplan",
                        "Der Zahnriemen wird beim 2.0 TDI alle 210.000 km gewechselt.")
fakten, abgelehnt = _lauf([autodoc_info, autodoc_club], marke="VW", modell="Golf", motor="2.0 TDI")
check("club.autodoc.de + autodoc.de zählen als EINE Source-Family -> kein Fakt",
      len(fakten) == 0, f"fakten={fakten}")
check("Q) (alias D) gleiche Root-Domain bleibt auch in der gehärteten Policy EINE Family",
      len(fakten) == 0, f"fakten={fakten}")

print("\n" + "=" * 78)
print("E) gleicher Anbieter, gleiche Zahl -> kein künstlicher Konsens")
print("=" * 78)
autodoc_a = _treffer("https://www.autodoc.de/info/a", "VW Golf Wartung A",
                     "Der Zahnriemen wird beim 2.0 TDI alle 210.000 km gewechselt.")
autodoc_b = _treffer("https://www.autodoc.de/info/b", "VW Golf Wartung B",
                     "Der Zahnriemen wird beim 2.0 TDI alle 210.000 km gewechselt.")
fakten, abgelehnt = _lauf([autodoc_a, autodoc_b], marke="VW", modell="Golf", motor="2.0 TDI")
check("Zwei URLs derselben Domain erzeugen KEINEN künstlichen Zwei-Stimmen-Konsens",
      len(fakten) == 0, f"fakten={fakten}")

print("\n" + "=" * 78)
print("F) sekundäre Quellen widersprechen -> CONFLICT")
print("=" * 78)
autoteile_konflikt = _treffer("https://www.autoteile-xyz.de/golf-zahnriemen-2", "VW Golf 7 Wartung",
                              "Der Zahnriemen wird beim 2.0 TDI alle 150.000 km gewechselt.")
fakten, abgelehnt = _lauf([repareo_zahnriemen, autoteile_konflikt],
                         marke="VW", modell="Golf", motor="2.0 TDI")
check("Widersprechende Secondary-Werte ergeben KEINEN bestätigten Fakt",
      len(fakten) == 0, f"fakten={fakten}")
check("Konflikt ist als 'wartung_candidate_conflict' sichtbar",
      "wartung_candidate_conflict" in _gruende(abgelehnt))

print("\n" + "=" * 78)
print("G) 15k fixed vs 30k LongLife -> kein echter Conflict")
print("=" * 78)
brems_fest = _treffer("https://www.repareo.de/mazda-mx5-bremse", "Mazda MX-5 Wartung",
                      "Die Bremsfluessigkeit wird beim 2.0 SKYACTIV-G alle 2 Jahre gewechselt.")
brems_longlife = _treffer("https://www.autoteile-xyz.de/mazda-mx5-bremse", "Mazda MX-5 Wartung",
                          "Die Bremsfluessigkeit wird beim 2.0 SKYACTIV-G im LongLife-Intervall "
                          "alle 4 Jahre gewechselt.")
fakten, abgelehnt = _lauf([brems_fest, brems_longlife], marke="Mazda", modell="MX-5",
                         motor="2.0 SKYACTIV-G", generation="ND")
check("Fest- und LongLife-Intervall werden NICHT als Conflict gewertet",
      "wartung_candidate_conflict" not in _gruende(abgelehnt))

print("\n" + "=" * 78)
print("H) Mazda ND vs NB -> keine Cross-Bestätigung")
print("=" * 78)
mx5_nd = _treffer("https://www.repareo.de/mazda-mx5-nd", "Mazda MX-5 Wartung",
                  "Die Zuendkerzen werden beim 2.0 SKYACTIV-G alle 120.000 km gewechselt.")
mx5_nb = _treffer("https://www.autoteile-xyz.de/mazda-mx5-nb", "Mazda MX-5 NB Wartung",
                  "Die Zuendkerzen werden beim 2.0 SKYACTIV-G alle 120.000 km gewechselt.")
fakten, abgelehnt = _lauf([mx5_nd, mx5_nb], marke="Mazda", modell="MX-5",
                         motor="2.0 SKYACTIV-G", generation="ND")
check("NB-Treffer wird wegen fremder Generation verworfen",
      "andere_generation" in _gruende(abgelehnt))
check("Ohne gültige zweite Family bleibt der Fakt unbestätigt",
      len(fakten) == 0, f"fakten={fakten}")

print("\n" + "=" * 78)
print("I) gleicher Modellname, anderer Motor -> keine Cross-Bestätigung")
print("=" * 78)
golf_tsi = _treffer("https://www.autoteile-xyz.de/golf-tsi-zahnriemen", "VW Golf 7 Wartung",
                    "Der Zahnriemen wird beim 1.0 TSI alle 90.000 km gewechselt.")
fakten, abgelehnt = _lauf([repareo_zahnriemen, golf_tsi],
                         marke="VW", modell="Golf", motor="2.0 TDI")
check("Claim mit explizit ANDEREM Motor wird als Widerspruch verworfen",
      "wartung_motor_widerspricht" in _gruende(abgelehnt))
check("Ohne gültige zweite Family bleibt der Fakt unbestätigt",
      len(fakten) == 0, f"fakten={fakten}")

print("\n" + "=" * 78)
print("J) unklare Applicability (Modell nicht erkennbar) -> kein bestätigter Fakt")
print("=" * 78)
allgemein = _treffer("https://www.repareo.de/allgemeine-wartung", "Allgemeine Fahrzeugpflege",
                     "Der Zahnriemen wird alle 210.000 km gewechselt.")
fakten, abgelehnt = _lauf([allgemein], marke="VW", modell="Golf", motor="2.0 TDI")
check("Treffer ohne Modellbezug liefert keinen Fakt", len(fakten) == 0, f"fakten={fakten}")
check("Ablehnungsgrund ist 'modell_fehlt'", "modell_fehlt" in _gruende(abgelehnt))

print("\n" + "=" * 78)
print("K) Community + Secondary -> Community zählt NICHT als Bestätigung")
print("=" * 78)
mx5_repareo_zuendkerzen = _treffer("https://www.repareo.de/mazda-mx5-zuendkerzen", "Mazda MX-5 Wartung",
                                   "Die Zuendkerzen werden beim 2.0 SKYACTIV-G alle 120.000 km gewechselt.")
mx5_forum = _treffer("https://www.motor-talk.de/forum/mx5-zuendkerzen", "Mazda MX-5 Forum",
                     "Die Zuendkerzen werden beim 2.0 SKYACTIV-G alle 120.000 km gewechselt.")
fakten, abgelehnt = _lauf([mx5_repareo_zuendkerzen, mx5_forum], marke="Mazda", modell="MX-5",
                         motor="2.0 SKYACTIV-G", generation="ND")
check("Community-Quelle zählt nicht als zweite bestätigende Stimme",
      len(fakten) == 0, f"fakten={fakten}")

print("\n" + "=" * 78)
print("L) zwei unabhängige Secondary-Quellen, gleicher Wert, kompatible Variante -> AGREEMENT,")
print("   aber ohne Bedingung A/B weiterhin NICHT VERIFIED (Härtungsrunde)")
print("=" * 78)
mx5_autoteile_zuendkerzen = _treffer("https://www.autoteile-xyz.de/mazda-mx5-zuendkerzen",
                                     "Mazda MX-5 Wartung",
                                     "Die Zuendkerzen werden beim 2.0 SKYACTIV-G alle 120.000 km gewechselt.")
fakten, abgelehnt = _lauf([mx5_repareo_zuendkerzen, mx5_autoteile_zuendkerzen],
                         marke="Mazda", modell="MX-5", motor="2.0 SKYACTIV-G", generation="ND")
check("Zwei Secondary-Quellen (Mazda) alleine erzeugen jetzt einen sichtbaren "
      "CORROBORATED-Hinweis (nicht VERIFIED)",
      len(_corroborated(fakten)) == 1, f"fakten={fakten}")

print("\n" + "=" * 78)
print("M) interne Inkonsistenz derselben Quelle -> Unreliable, NICHT zwei Stimmen")
print("=" * 78)
mx5_repareo_inkonsistent = _treffer(
    "https://www.repareo.de/mazda-mx5-zuendkerzen-inkonsistent", "Mazda MX-5 Wartung",
    "Die Zuendkerzen werden beim 2.0 SKYACTIV-G alle 100.000 km gewechselt. "
    "Die Zuendkerzen werden beim 2.0 SKYACTIV-G alle 120.000 km gewechselt.")
fakten, abgelehnt = _lauf([mx5_repareo_inkonsistent, mx5_autoteile_zuendkerzen],
                         marke="Mazda", modell="MX-5", motor="2.0 SKYACTIV-G", generation="ND")
check("Intern inkonsistente Quelle bestätigt NICHT künstlich den Fakt",
      len(fakten) == 0, f"fakten={fakten}")
check("Inkonsistenz ist als 'wartung_quelle_intern_inkonsistent' sichtbar",
      "wartung_quelle_intern_inkonsistent" in _gruende(abgelehnt))


print("\n" + "=" * 78)
print("Erweiterte Testmatrix (Haertungsrunde): N-Y")
print("=" * 78)

print("\n--- N) Secondary A + Secondary B, verschiedene Domains, gleicher Wert ---")
print("    -> MATCH/CORROBORATED, NICHT automatisch VERIFIED")
n1 = _treffer("https://www.kfz-tipps-online.de/golf-steuerkette", "VW Golf Werkstatt-Tipp",
             "Die Steuerkette wird beim 2.0 TDI alle 180.000 km geprueft.")
n2 = _treffer("https://www.motorwissen24.de/golf-steuerkette", "VW Golf Reparatur-Ratgeber",
             "Die Steuerkette wird beim 2.0 TDI alle 180.000 km geprueft.")
fakten, abgelehnt = _lauf([n1, n2], marke="VW", modell="Golf", motor="2.0 TDI")
check("N) zwei verschiedene Secondary-Domains alleine -> sichtbar, aber CORROBORATED, kein VERIFIED",
      len(_corroborated(fakten)) == 1, f"fakten={fakten}")
check("R) (alias N) verschiedene Root-Domains beweisen KEINE echte Unabhängigkeit "
      "-> bleibt bei CORROBORATED (niedrige Confidence), nicht VERIFIED",
      len(_corroborated(fakten)) == 1 and _corroborated(fakten)[0].confidence == "niedrig")

print("\n--- O) Secondary + Secondary, zusätzlich durch bestehenden STRONG_SECONDARY-Pfad ---")
print("    cross-tier bestätigt (Bedingung A) -> VERIFIED")
o_adac = _treffer("https://www.adac.de/golf-steuerkette-check", "VW Golf Steuerkette Check",
                  "Die Steuerkette wird beim 2.0 TDI alle 180.000 km geprueft.")
o1 = _treffer("https://www.kfz-tipps-online.de/golf-steuerkette-2", "VW Golf Werkstatt-Tipp",
             "Die Steuerkette wird beim 2.0 TDI alle 180.000 km geprueft.")
o2 = _treffer("https://www.motorwissen24.de/golf-steuerkette-2", "VW Golf Reparatur-Ratgeber",
             "Die Steuerkette wird beim 2.0 TDI alle 180.000 km geprueft.")
fakten, abgelehnt = _lauf([o_adac, o1, o2], marke="VW", modell="Golf", motor="2.0 TDI")
wartung_fakten = [f for f in fakten if f.kategorie == "wartung"]
check("O) ADAC (STRONG_SECONDARY, bestehender Pfad) liefert bereits einen Fakt",
      len(wartung_fakten) == 1, f"fakten={wartung_fakten}")
check("O) Cross-Tier-Bestätigung (Bedingung A) REICHERT den bestehenden Fakt an, statt einen "
      "zweiten, inhaltsgleichen WebFakt zu erzeugen (§6E Pre-Commit-Review)",
      len(wartung_fakten) == 1
      and {q.url for q in wartung_fakten[0].quellen} >= {o_adac["url"], o1["url"], o2["url"]},
      f"quellen={[q.url for q in wartung_fakten[0].quellen]}")
check("O) für diese Secondary-Gruppe erscheint KEIN 'wartung_corroborated_not_verified' mehr",
      not any(a.get("grund") == "wartung_corroborated_not_verified" and a.get("url") in (o1["url"], o2["url"])
             for a in abgelehnt))

print("\n--- Z) Secondary + Secondary, EXPLIZITE Provenienz-SELBSTAUSKUNFT -> weiterhin CORROBORATED,")
print("    NICHT VERIFIED (Härtungsrunde 3: Bedingung B entfernt, §1 Auftrag)")
z1 = _treffer("https://www.kfz-tipps-online.de/golf-steuerkette-prov", "VW Golf Werkstatt-Tipp",
             "Laut Hersteller wird die Steuerkette beim 2.0 TDI alle 180.000 km geprueft.")
z2 = _treffer("https://www.motorwissen24.de/golf-steuerkette-prov", "VW Golf Reparatur-Ratgeber",
             "Die Steuerkette wird beim 2.0 TDI alle 180.000 km geprueft.")
fakten, abgelehnt = _lauf([z1, z2], marke="VW", modell="Golf", motor="2.0 TDI")
check("Z) reine Provenienz-Selbstauskunft genügt NICHT für VERIFIED -- sichtbar, aber "
      "nur als CORROBORATED",
      len(_corroborated(fakten)) == 1, f"fakten={fakten}")
check("Z) bleibt bei niedriger Confidence (keine Aufwertung durch die unbelegte "
      "Selbstauskunft 'laut Hersteller')",
      bool(_corroborated(fakten)) and _corroborated(fakten)[0].confidence == "niedrig")

print("\n--- AA) Secondary + PRIMARY (strukturell IDENTISCHE Signature) -> VERIFIED ---")
aa_primary = _treffer("https://www.volkswagen.at/golf-steuerkette", "VW Golf Steuerkette",
                      "Die Steuerkette wird beim 2.0 TDI alle 180.000 km ersetzt.")
aa_sek1 = _treffer("https://www.kfz-tipps-online.de/golf-steuerkette-aa", "VW Golf Werkstatt-Tipp",
                   "Die Steuerkette wird beim 2.0 TDI alle 180.000 km ersetzt.")
aa_sek2 = _treffer("https://www.motorwissen24.de/golf-steuerkette-aa", "VW Golf Reparatur-Ratgeber",
                   "Die Steuerkette wird beim 2.0 TDI alle 180.000 km ersetzt.")
fakten, abgelehnt = _lauf([aa_primary, aa_sek1, aa_sek2], marke="VW", modell="Golf", motor="2.0 TDI")
aa_wartung = [f for f in fakten if f.kategorie == "wartung"]
check("AA) gleiche Komponente+Aktion+Intervall+Regime (PRIMARY+2 Secondary) -> VERIFIED, "
      "angereichert auf EINEN Fakt (kein Duplikat)",
      len(aa_wartung) == 1
      and {q.url for q in aa_wartung[0].quellen} >= {aa_primary["url"], aa_sek1["url"], aa_sek2["url"]},
      f"fakten={aa_wartung}")

print("\n--- AB) gleiche Komponente + gleiches Intervall, aber 'prüfen' vs. 'ersetzen' -> KEIN VERIFIED ---")
ab_primary = _treffer("https://www.volkswagen.at/golf-getriebe", "VW Golf Getriebe",
                      "Das Getriebeoel wird beim 2.0 TDI alle 60.000 km geprueft.")
ab_sek1 = _treffer("https://www.kfz-tipps-online.de/golf-getriebe-ab", "VW Golf Werkstatt-Tipp",
                   "Das Getriebeoel wird beim 2.0 TDI alle 60.000 km gewechselt.")
ab_sek2 = _treffer("https://www.motorwissen24.de/golf-getriebe-ab", "VW Golf Reparatur-Ratgeber",
                   "Das Getriebeoel wird beim 2.0 TDI alle 60.000 km gewechselt.")
fakten, abgelehnt = _lauf([ab_primary, ab_sek1, ab_sek2], marke="VW", modell="Golf", motor="2.0 TDI")
ab_verified = [f for f in fakten if f.kategorie == "wartung" and getattr(f, "beleglage", None) != "uebereinstimmend_unbestaetigt"]
check("AB) 'prüfen' (PRIMARY) bleibt sein EIGENER, VERIFIED-Fakt (kein Cross-Tier-Match "
      "mit der 'wechseln'-Secondary-Gruppe -- andere Aktion, andere Signatur)",
      sum(1 for f in ab_verified if f.bauteil and "etriebe" in f.bauteil) == 1, f"fakten={fakten}")
check("AB) die zwei Secondary-Quellen (gleiche Aktion 'wechseln', gleicher Wert) sind jetzt "
      "sichtbar, aber als EIGENER, CORROBORATED-Fakt -- NICHT mit dem PRIMARY-'prüfen'-Fakt "
      "vermischt",
      len(_corroborated(fakten, "etriebe")) == 1, f"fakten={fakten}")

print("\n--- AC) gleiches Bauteil, aber fixed vs. LongLife -> KEIN VERIFIED ---")
ac_primary = _treffer("https://www.volkswagen.at/golf-oel", "VW Golf Oelwechsel",
                      "Das Motoroel wird beim 2.0 TDI im LongLife-Intervall alle 30.000 km gewechselt.")
ac_sek1 = _treffer("https://www.kfz-tipps-online.de/golf-oel-ac", "VW Golf Werkstatt-Tipp",
                   "Das Motoroel wird beim 2.0 TDI alle 15.000 km gewechselt.")
ac_sek2 = _treffer("https://www.motorwissen24.de/golf-oel-ac", "VW Golf Reparatur-Ratgeber",
                   "Das Motoroel wird beim 2.0 TDI alle 15.000 km gewechselt.")
fakten, abgelehnt = _lauf([ac_primary, ac_sek1, ac_sek2], marke="VW", modell="Golf", motor="2.0 TDI")
check("AC) LongLife (PRIMARY) bestätigt NICHT das fixe Secondary-Intervall (andere Signatur) "
      "-- die zwei fixen Secondary-Quellen sind jetzt sichtbar, aber als EIGENER, "
      "CORROBORATED-Fakt (nicht mit dem LongLife-PRIMARY-Fakt vermischt)",
      len(_corroborated(fakten, "otoroel")) == 1, f"fakten={fakten}")

print("\n--- AD) gleicher Wert, aber anderer Motor -> KEIN VERIFIED (bereits durch I/Y belegt) ---")
check("AD) siehe Test I/Y oben -- expliziter Motor-Widerspruch verhindert jede Cross-Bestätigung", True)

print("\n--- AE) gleicher Wert, aber andere Generation -> KEIN VERIFIED (bereits durch H/X belegt) ---")
check("AE) siehe Test H/X oben -- fremde Generation verhindert jede Cross-Bestätigung", True)

print("\n--- AF) gleicher Wert, aber explizit anderer Markt -> KEIN VERIFIED (bereits durch T belegt) ---")
check("AF) siehe Test T oben -- expliziter US-Markt-Hinweis verhindert Verwertung", True)

print("\n--- AG) nur Substring-Überschneidung im Bauteilnamen -> KEIN VERIFIED ---")
ag_primary = _treffer("https://www.volkswagen.at/golf-steuerkette-ag", "VW Golf Steuerkette",
                      "Die Steuerkette wird beim 2.0 TDI alle 180.000 km ersetzt.")
ag_sek1 = _treffer("https://www.kfz-tipps-online.de/golf-zahnriemen-ag", "VW Golf Werkstatt-Tipp",
                   "Der Zahnriemen wird beim 2.0 TDI alle 180.000 km ersetzt.")
ag_sek2 = _treffer("https://www.motorwissen24.de/golf-zahnriemen-ag", "VW Golf Reparatur-Ratgeber",
                   "Der Zahnriemen wird beim 2.0 TDI alle 180.000 km ersetzt.")
fakten, abgelehnt = _lauf([ag_primary, ag_sek1, ag_sek2], marke="VW", modell="Golf", motor="2.0 TDI")
check("AG) 'Steuerkette' (PRIMARY) bestätigt NICHT 'Zahnriemen' (anderer Bauteil-Schlüssel) "
      "-- die zwei 'Zahnriemen'-Secondary-Quellen sind jetzt sichtbar, aber als EIGENER, "
      "CORROBORATED-Fakt (nicht mit dem 'Steuerkette'-PRIMARY-Fakt vermischt)",
      len(_corroborated(fakten, "ahnriemen")) == 1, f"fakten={fakten}")

print("\n--- AH) echte strukturierte Übereinstimmung trotz unterschiedlicher Formulierung -> VERIFIED ---")
ah_primary = _treffer("https://www.volkswagen.at/golf-steuerkette-ah", "VW Golf Steuerkette",
                      "Die Steuerkette wird beim 2.0 TDI alle 180.000 km ersetzt.")
ah_sek1 = _treffer("https://www.kfz-tipps-online.de/golf-steuerkette-ah", "VW Golf Werkstatt-Tipp",
                   "Die Steuerkette wird beim 2.0 TDI nach 180000 Kilometer ersetzt.")
ah_sek2 = _treffer("https://www.motorwissen24.de/golf-steuerkette-ah", "VW Golf Reparatur-Ratgeber",
                   "Die Steuerkette wird beim 2.0 TDI alle 180.000 km ersetzt.")
fakten, abgelehnt = _lauf([ah_primary, ah_sek1, ah_sek2], marke="VW", modell="Golf", motor="2.0 TDI")
ah_wartung = [f for f in fakten if f.kategorie == "wartung"]
check("AH) 'alle 180.000 km' und 'nach 180000 Kilometer' gelten als dieselbe Signatur -> VERIFIED, "
      "angereichert auf EINEN Fakt",
      len(ah_wartung) == 1
      and {q.url for q in ah_wartung[0].quellen} >= {ah_primary["url"], ah_sek1["url"], ah_sek2["url"]},
      f"fakten={ah_wartung}")

print("\n--- AI) provenance_claimed ohne überprüfbare Primärquelle -> keine Verification-Aufwertung ---")
check("AI) siehe Test Z oben -- provenance_claimed=True erzeugt dennoch kein VERIFIED", True)

print("\n--- P) Unknown + Unknown, gleicher Wert -> NIE VERIFIED ---")
p1 = _treffer("https://www.irgendeinblog24.de/golf", "Mein Autoblog ueber den VW Golf",
             "Die Bremsscheiben werden beim 2.0 TDI alle 80.000 km kontrolliert.")
p2 = _treffer("https://www.carfan-netz.de/golf", "Fancommunity rund um den VW Golf",
             "Die Bremsscheiben werden beim 2.0 TDI alle 80.000 km kontrolliert.")
fakten, abgelehnt = _lauf([p1, p2], marke="VW", modell="Golf", motor="2.0 TDI")
check("P) zwei UNKNOWN-Quellen werden NIE VERIFIED, auch bei exaktem Wertgleichklang",
      len(fakten) == 0, f"fakten={fakten}")
check("P) UNKNOWN+UNKNOWN bleibt UNSICHTBAR -- die Produktentscheidung ('CORROBORATED "
      "sichtbar machen') lockert diesen separaten, älteren Schutz vor Quellenaufwertung "
      "NICHT (anders als bei echten, klassifizierbaren Secondary-Quellen oben)",
      "wartung_corroborated_unknown_tier" in _gruende(abgelehnt))

print("\n--- S) Ziel-Getriebe DSG, Quelle 'nur Schaltgetriebe' -> reject ---")
s1 = _treffer("https://www.kfz-tipps-online.de/golf-getriebe", "VW Golf Werkstatt-Tipp",
             "Nur mit Schaltgetriebe erhaeltlich wird das Getriebeoel alle 60.000 km gewechselt.")
fakten, abgelehnt = _lauf([s1], marke="VW", modell="Golf", motor="2.0 TDI", getriebe="DSG")
check("S) expliziter Getriebe-Widerspruch wird verworfen",
      "wartung_getriebe_widerspricht" in _gruende(abgelehnt))

print("\n--- T) EU-Ziel, Quelle explizit 'US market schedule' -> nicht blind verifizieren ---")
t1 = _treffer("https://www.kfz-tipps-online.de/golf-us", "VW Golf Werkstatt-Tipp",
             "US market maintenance schedule: Der Zahnriemen wird alle 100.000 km gewechselt.")
fakten, abgelehnt = _lauf([t1], marke="VW", modell="Golf", motor="2.0 TDI")
check("T) expliziter US-Markt-Hinweis wird verworfen, nicht als EU-Beleg verwertet",
      "wartung_us_markt_claim" in _gruende(abgelehnt))

print("\n--- U) (alias G) LongLife-Condition bleibt erhalten, kein Conflict ---")
check("U) siehe Test G oben -- Fest- vs. LongLife-Intervall erzeugt keinen Conflict", True)

print("\n--- V) '120.000 km' vs. 'alle 120000 Kilometer' -> semantischer MATCH ---")
v1 = _treffer("https://www.kfz-tipps-online.de/golf-steuerkette-norm", "VW Golf Werkstatt-Tipp",
             "Die Steuerkette wird beim 2.0 TDI alle 120.000 km geprueft.")
v2 = _treffer("https://www.motorwissen24.de/golf-steuerkette-norm", "VW Golf Reparatur-Ratgeber",
             "Die Steuerkette wird beim 2.0 TDI nach 120000 Kilometer geprueft.")
fakten, abgelehnt = _lauf([v1, v2], marke="VW", modell="Golf", motor="2.0 TDI")
v_corrob = _corroborated(fakten)
check("V) unterschiedlich geschriebene, aber gleiche km-Werte werden als EIN Thema erkannt "
      "-- EIN sichtbarer CORROBORATED-Fakt mit BEIDEN Quellen, nicht zwei getrennte Singles",
      len(v_corrob) == 1 and {q.url for q in v_corrob[0].quellen} == {v1["url"], v2["url"]},
      f"fakten={fakten}")

print("\n--- W) 'prüfen bei 120k' vs. 'ersetzen bei 120k' -> KEIN Match ---")
w1 = _treffer("https://www.kfz-tipps-online.de/golf-steuerkette-aktion", "VW Golf Werkstatt-Tipp",
             "Die Steuerkette wird beim 2.0 TDI alle 120.000 km geprueft.")
w2 = _treffer("https://www.motorwissen24.de/golf-steuerkette-aktion", "VW Golf Reparatur-Ratgeber",
             "Die Steuerkette wird beim 2.0 TDI alle 120.000 km ersetzt.")
fakten, abgelehnt = _lauf([w1, w2], marke="VW", modell="Golf", motor="2.0 TDI")
check("W) unterschiedliche Wartungs-Aktion (pruefen vs. ersetzen) wird NICHT zusammengeführt",
      "wartung_candidate_conflict" not in _gruende(abgelehnt)
      and "wartung_corroborated_not_verified" not in _gruende(abgelehnt))
check("W) beide bleiben stattdessen einzeln unverified (je eigene Themen-Gruppe)",
      sum(1 for a in abgelehnt if a.get("grund") == "wartung_candidate_unverified"
         and a.get("url") in (w1["url"], w2["url"])) == 2)

print("\n--- X) (alias H) gleicher Wert, andere Generation -> kein Agreement ---")
check("X) siehe Test H oben -- Mazda NB wird wegen fremder Generation ausgeschlossen", True)

print("\n--- Y) (alias I) gleicher Wert, anderer Motor -> kein Agreement ---")
check("Y) siehe Test I oben -- expliziter Motor-Widerspruch verhindert Cross-Bestätigung", True)


print("\n" + "=" * 78)
print("Regression: schwachstelle bleibt beim ALTEN Gate (keine Candidate-Logik)")
print("=" * 78)
repareo_schwachstelle = _treffer(
    "https://www.repareo.de/golf-schwachstelle", "VW Golf Schwachstellen",
    "Die Wasserpumpe beim Golf ist undicht und zeigt Lagerschaden.")
fakten_sw, abgelehnt_sw = _lauf([repareo_schwachstelle], marke="VW", modell="Golf",
                                kategorie="schwachstelle")
check("Secondary-Quelle bleibt für 'schwachstelle' beim alten Gate verworfen (source_quality)",
      len(fakten_sw) == 0 and "source_quality" in _gruende(abgelehnt_sw))
check("Keine 'wartung_*'-Gründe tauchen für 'schwachstelle' auf (Scope-Isolierung)",
      not any(str(g or "").startswith("wartung_") for g in _gruende(abgelehnt_sw)))


print("\n" + "=" * 78)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for fehler in _FEHLER:
        print(f"  - {fehler}")
    raise SystemExit(1)
print("ALLE WARTUNGS-EVIDENCE-CANDIDATE-TESTS GRUEN")
