"""
F18 (bedingte Rückwärts-Kontextzuordnung) am TATSÄCHLICH betroffenen Pfad:
dem Secondary-/Candidate-Pfad (`_wartung_candidate_fakten`), nicht dem
Hauptpfad. Ein im Hauptpfad erfolgreicher Fall (Tier-1/2-Quelle, article-level
stark ausgerichtet) braucht F18s satzweise Rückreferenz-Logik gar nicht --
jeder Satz einer stark ausgerichteten Seite zählt dort ohnehin (das ist KEIN
F18-Beweis, siehe Fortsetzungsauftrag).

Verfolgt wird deshalb die vollständige Kette für eine Secondary-Quelle:
    Quelltext -> _wartung_kandidat_saetze -> Kandidat (_wartung_candidate_fakten)
    -> Evidenzstatus (_wartung_verification_status) -> ggf. sichtbarer Bericht
      (Anreicherung einer bereits vorhandenen Primärevidenz, Bedingung A).

Fixtures A/B sind dieselben wie in
test_rc_f_schwachstelle_wartung_reparaturpaket.py (F18-Abschnitt, dort auf
Satzlisten-Ebene geprüft) -- hier bis zum Evidenzstatus weiterverfolgt.

Zusätzlich: Reproduktion und Fix einer realen Kontamination ("im Hauptpfad/
Candidate-Pfad zählt jeder Satz automatisch, weil die Seite stark ausgerichtet
ist"): eine zum Zielmodell passende Seite, die im Fließtext zusätzlich eine
fremde VARIANTE nennt, durfte diese Aussage nicht allein wegen des
Artikeltitels übernehmen -- sie vergiftete sonst (als "intern inkonsistente"
Quelle) auch die eigene, korrekte Aussage derselben Seite.

    python scripts/test_isolated.py test_f18_candidate_pfad_wartung.py
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.models import EvidenceQuelle, WebFakt
from app.technical_research import (
    _claim_widerspricht_fahrzeug_lokal, _fremdfahrzeug_explizit_lokal,
    _wartung_candidate_fakten, _wartung_kandidat_saetze, ausgerichtet,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Schritt 1: Quelltext -- bewusst SCHWACH ausgerichtete Seite ─────────────
# (Titel nennt weder Marke noch Modell -- genau der Fall, der F18s
# Rückreferenz-Logik überhaupt erst nötig macht; eine stark ausgerichtete
# Seite bräuchte sie nicht, siehe Moduldocstring.)
_POSITIV = t("https://beispiel.de/c", "Wartung",
            "Alle 60.000 Kilometer wird das Getriebeoel gewechselt. "
            "Diese Angabe gilt fuer den Lexus RC F.")
_NEGATIV = t("https://beispiel.de/b", "Wartung",
            "Alle 60.000 Kilometer wird das Getriebeoel generell gewechselt. "
            "Der Lexus RC F kam 2014 auf den Markt.")

check("Schritt 1: Positivquelle ist SCHWACH ausgerichtet (F18 ueberhaupt einschlaegig)",
      ausgerichtet(_POSITIV, "Lexus", "RC F") == (True, "schwach"))

# ── Schritt 2: _wartung_kandidat_saetze ─────────────────────────────────────
_ab1: list[dict] = []
_saetze_positiv = _wartung_kandidat_saetze(_POSITIV, "Lexus", "RC F", _ab1)
_ab2: list[dict] = []
_saetze_negativ = _wartung_kandidat_saetze(_NEGATIV, "Lexus", "RC F", _ab2)

check("Schritt 2 (Positivfall): die intervalltragende Aussage uebersteht die "
      "Rueckreferenz-Pruefung (echte Rueckreferenz im Folgesatz)",
      any("60.000" in s for s in _saetze_positiv), str(_saetze_positiv))
check("Schritt 2 (Gegenfall): dieselbe Aussage OHNE echte Rueckreferenz "
      "(blosse Zielmodellnennung) wird NICHT uebernommen",
      not any("60.000" in s for s in _saetze_negativ), str(_saetze_negativ))

# ── Schritt 3: Kandidat (_wartung_candidate_fakten) ─────────────────────────
# 3a) der F18-Kandidat ALLEIN: zu wenige Families -> UNVERIFIED, nichts sichtbar.
_f3a, _ab3a, _esk3a = _wartung_candidate_fakten(
    [_POSITIV], marke="Lexus", modell="RC F", generation=None, motor="5.0 V8",
    getriebe="Automatik", haupt_fakten=[], erlaube_multi_source=False)
check("Schritt 3a: F18-Kandidat allein bleibt UNVERIFIED (kein Fakt)",
      len(_f3a) == 0 and any(a.get("grund") == "wartung_candidate_unverified" for a in _ab3a))

# 3b) derselbe Negativfall ALLEIN: erzeugt gar KEINEN Kandidaten -- der Beweis,
# dass Schritt 3a NICHT zufällig, sondern ursaechlich von F18 abhaengt.
_f3b, _ab3b, _esk3b = _wartung_candidate_fakten(
    [_NEGATIV], marke="Lexus", modell="RC F", generation=None, motor="5.0 V8",
    getriebe="Automatik", haupt_fakten=[], erlaube_multi_source=False)
check("Schritt 3b: Gegenfall erzeugt GAR KEINEN Kandidaten (kein Fakt, keine Ablehnung "
      "zur Intervallaussage) -- F18 war fuer Schritt 3a tatsaechlich noetig, "
      "kein Hauptpfad-/Zufallserfolg",
      len(_f3b) == 0 and not any("60.000" in (a.get("satz") or "") for a in _ab3b),
      str(_ab3b))

# ── Schritt 4: Evidenzstatus -- zweite unabhaengige Secondary-Quelle ────────
_ZWEITE_QUELLE = t("https://andere-quelle.de/lexus-rc-f-wartung", "Wartung RC F",
                   "Alle 60.000 Kilometer wird das Getriebeoel beim Lexus RC F gewechselt.")
_f4, _ab4, _esk4 = _wartung_candidate_fakten(
    [_POSITIV, _ZWEITE_QUELLE], marke="Lexus", modell="RC F", generation=None,
    motor="5.0 V8", getriebe="Automatik", haupt_fakten=[], erlaube_multi_source=False)
# Produktentscheidung (Fortsetzungsauftrag, "Gestützte Wartungshinweise
# sichtbar machen", NACH diesem F18-Test entschieden): CORROBORATED wird jetzt
# sichtbar gemacht -- der Evidenzstatus bleibt dabei unveraendert CORROBORATED
# (eigener beleglage-Wert, niedrige Confidence), NIE "verified". Kein
# Reporting-Bug mehr, aber auch keine Aufwertung: siehe
# test_wartung_corroborated_sichtbar.py fuer die volle Herleitung/Schutzregeln.
check("Schritt 4: zwei unabhaengige Secondary-Familien (inkl. F18-Kandidat) -> "
      "jetzt sichtbar, aber klar als CORROBORATED (nicht VERIFIED) markiert",
      len(_f4) == 1 and _f4[0].beleglage == "uebereinstimmend_unbestaetigt"
      and _f4[0].confidence == "niedrig")

# ── Schritt 5: sichtbarer Bericht -- MIT passender, unabhaengiger Primaerevidenz ──
# Bedingung A: ein bereits vom Hauptpfad erzeugter WebFakt mit DERSELBEN
# Signatur wird NICHT dupliziert, sondern um die Candidate-Quellen (inkl. des
# F18-Kandidaten) angereichert -- dieselbe Objektinstanz, keine zweite,
# widerspruechliche Anzeige.
_primaer = WebFakt(
    kategorie="wartung", bauteil="Getriebeoel",
    aussage="Alle 60.000 km wird das Getriebeoel gewechselt",
    confidence="hoch", applicability=None,
    quellen=[EvidenceQuelle(typ="web_technik", url="https://media.lexus.eu/rc-f-service",
                            titel="Lexus Serviceplan RC F", qualitaet="Hersteller")],
    geltungsbereich=None, geltung_fuer_fahrzeug=None, scope_text=None)
_quellen_vor = {q.url for q in _primaer.quellen}
_f5, _ab5, _esk5 = _wartung_candidate_fakten(
    [_POSITIV, _ZWEITE_QUELLE], marke="Lexus", modell="RC F", generation=None,
    motor="5.0 V8", getriebe="Automatik", haupt_fakten=[_primaer], erlaube_multi_source=False)
_quellen_nach = {q.url for q in _primaer.quellen}

check("Schritt 5: KEIN zweiter, eigenstaendiger Fakt wird erzeugt (Anreicherung "
      "statt Duplikat)", len(_f5) == 0, str(_f5))
check("Schritt 5: die Primaerquelle bleibt erhalten (kein Verlust bestehender Evidence)",
      "https://media.lexus.eu/rc-f-service" in _quellen_nach)
check("Schritt 5: der F18-Kandidat selbst ist NACHWEISLICH in der Primaerevidenz "
      "gelandet (Provenienz/Pfad bestaetigt -- kein 'scheinbarer Erfolg' bei "
      "tatsaechlich verlorenem Secondary-Kandidaten)",
      "https://beispiel.de/c" in _quellen_nach and "https://beispiel.de/c" not in _quellen_vor,
      str(_quellen_nach))
check("Schritt 5: auch die zweite unabhaengige Quelle landet in derselben Evidenz",
      "https://andere-quelle.de/lexus-rc-f-wartung" in _quellen_nach)


# ── Fremdvarianten-Kontamination (reproduzierter Befund + Fix) ──────────────
# "Im Hauptpfad/Candidate-Pfad zaehlt jeder Satz automatisch, weil die Seite
# stark ausgerichtet ist" -- konkret geprueft: eine Seite, deren TITEL bereits
# Marke+Modell traegt (stark ausgerichtet, F18s Distanz-/Rueckreferenz-Logik
# greift nicht), nennt im Fliesstext trotzdem eine FREMDE Variante
# (Lexus RC 300h statt RC F). Diese Aussage darf nicht gelten.
_KONTAMINIERT = t(
    "https://carwiki.de/lexus-rc-f-wartung", "Lexus RC F: Wartung und Inspektion",
    "Die Lexus RC Baureihe umfasst mehrere Varianten. Beim Lexus RC 300h "
    "(Hybrid) wird das Motoroel bereits alle 10.000 km gewechselt. "
    "Beim Lexus RC F wird das Motoroel alle 60.000 km gewechselt.")
_UNABHAENGIG = t("https://www.repareo.de/oelwechsel-lexus-rc-f", "Oelwechsel Lexus RC F",
                 "Beim Lexus RC F wird das Motoroel alle 60.000 km gewechselt.")

check("Kontamination: die Fremdvarianten-Aussage (RC 300h) wird vom Fremdfahrzeug-Check "
      "als Widerspruch erkannt", _fremdfahrzeug_explizit_lokal(
          "Beim Lexus RC 300h (Hybrid) wird das Motoroel bereits alle 10.000 km gewechselt.",
          "Lexus", "RC F") is not None)
check("Kontamination: die eigene, korrekte RC-F-Aussage derselben Seite bleibt unbetroffen",
      _fremdfahrzeug_explizit_lokal(
          "Beim Lexus RC F wird das Motoroel alle 60.000 km gewechselt.",
          "Lexus", "RC F") is None)

_abK: list[dict] = []
_saetze_kontaminiert = _wartung_kandidat_saetze(_KONTAMINIERT, "Lexus", "RC F", _abK)
check("Fix: die Fremdvarianten-Aussage erscheint NICHT mehr unter den Kandidatensaetzen",
      not any("10.000" in s for s in _saetze_kontaminiert), str(_saetze_kontaminiert))
check("Fix: die eigene RC-F-Aussage bleibt als Kandidatensatz erhalten",
      any("60.000" in s for s in _saetze_kontaminiert), str(_saetze_kontaminiert))

_fK, _abK2, _eskK = _wartung_candidate_fakten(
    [_KONTAMINIERT, _UNABHAENGIG], marke="Lexus", modell="RC F", generation=None,
    motor="5.0 V8", getriebe="Automatik", haupt_fakten=[], erlaube_multi_source=False)
check("Fix: die kontaminierte Quelle gilt NICHT mehr als 'intern inkonsistent' -- "
      "ihre eigene, korrekte Aussage bestaetigt zusammen mit der unabhaengigen Quelle "
      "jetzt sichtbar als CORROBORATED (weiterhin nicht VERIFIED, da kein Primaerbeleg)",
      len(_fK) == 1 and _fK[0].beleglage == "uebereinstimmend_unbestaetigt"
      and not any(a.get("grund") == "wartung_quelle_intern_inkonsistent" for a in _abK2),
      str(_fK) + " | " + str(_abK2))

# Regressionsschutz: die VOLLE Pruefung (inkl. der weicheren Nominalphrasen-
# Heuristik) darf bei einem gewoehnlichen, der Funktion unbekannten Bauteilwort
# am Satzanfang einer STARK ausgerichteten Seite NICHT mehr pauschal greifen --
# nur noch der engere Fremdfahrzeug-Check. Realer Fund (Haertungsrunde-
# Regression): "Die Zuendkerzen werden beim 2.0 SKYACTIV-G ... gewechselt."
# wurde von der vollen Pruefung als 'unsichere Nominalphrase' fehlklassifiziert.
_ZUENDKERZEN_SATZ = "Die Zuendkerzen werden beim 2.0 SKYACTIV-G alle 120.000 km gewechselt."
check("Regressionsschutz: der enge Fremdfahrzeug-Check (jetzt im Candidate-Pfad "
      "IMMER aktiv) laesst ein gewoehnliches, unbekanntes Bauteilwort am "
      "Satzanfang unbeanstandet", _fremdfahrzeug_explizit_lokal(
          _ZUENDKERZEN_SATZ, "Mazda", "MX-5") is None)
check("Regressionsschutz: die VOLLE Pruefung (nur noch im 'schwach'-Pfad genutzt) "
      "bleibt bei diesem Satz unveraendert -- die weichere Heuristik ist NICHT "
      "kaputt, sie ist jetzt nur korrekt auf den schwachen Pfad begrenzt",
      _claim_widerspricht_fahrzeug_lokal(_ZUENDKERZEN_SATZ, "Mazda", "MX-5") is not None)


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE F18-CANDIDATE-PFAD-TESTS GRUEN")
