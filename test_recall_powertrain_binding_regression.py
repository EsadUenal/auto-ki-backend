"""
Recall-Regressionsschutz: Powertrain-Herkunft darf Recall-Applicability nicht
über eine falsche Claim-Bindung verfaelschen (Package G, Forensik-
Folgeauftrag).

Production-Befund (Lexus RC F): ein ueber Package A jetzt behobener Claim-
Binding-Fehler setzte `identity.powertrain="HEV"` aus einer Sibling-Varianten-
Erwaehnung auf derselben Seite. Dieser Wert floss UNVERAENDERT in
`recall_filter.rueckruf_scope()`s Antriebsart-Pruefung und drehte das Ergebnis
von "nicht pruefbar" auf "Variantenbedingung passt". Die Recall-Scope-Logik
SELBST bleibt in dieser Runde bewusst unveraendert (siehe bereits bestehende
test_recall_scope_official.py fuer deren eigene Abdeckung) -- dieser Test
beweist END-ZU-ENDE, dass der reparierte Input (Package A) die bekannte
Fehlerkette tatsaechlich schliesst, ohne die Recall-Logik selbst anzufassen.

GENERISCH (erfundene Marke/Modell), KEIN Hardcode auf eine konkrete Recall-
Referenznummer.

  1) Falscher Sibling-Powertrain-Claim setzt identity.powertrain NICHT mehr
     (Package A, End-zu-Ende ueber werte_identitaet_aus + apply_web_evidence)
  2) Unbekannter Powertrain -> Recall-Antriebsart-Dimension bleibt UNKNOWN
     (offen), nie kuenstlich "passt"
  3) Erst ein korrekt (ohne Sibling-Konfusion) belegter, passender Powertrain
     darf die Dimension auf "passt" heben
  4) Scope (rueckruf_scope) / Confidence (rueckruf_applicability) / Completion
     (confirmed_by_vin) bleiben getrennte Konzepte

    python test_recall_powertrain_binding_regression.py
"""
import app.recall_filter as _rf

_rf.get_rueckruf_referenzen_kurz = lambda: []

from app.models import TechnischeRecherche
from app.recall_filter import RECALL_UNKNOWN, RECALL_VARIANT_POSSIBLE, rueckruf_scope
from app.technical_research import werte_identitaet_aus
from app.vehicle_identity import VehicleIdentity

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def treffer(url: str, titel: str, inhalt: str) -> dict:
    return {"url": url, "title": titel, "content": inhalt}


def identity_aus_web(treffer_liste: list[dict], ziel: dict) -> VehicleIdentity:
    """Derselbe Pfad wie in Produktion: Web-Identität ermitteln, dann in die
    kanonische (leere, DB-miss-) VehicleIdentity übernehmen."""
    web_identitaet, _ = werte_identitaet_aus(treffer_liste, ziel)
    identity = VehicleIdentity(make=ziel.get("marke"), model=ziel.get("modell"))
    identity.apply_web_evidence(TechnischeRecherche(identitaet=web_identitaet))
    return identity


ZIEL = dict(marke="Testmarke", modell="Modellz F", baujahr=2021, motor=None)

# Generischer, amtlich eingegrenzter Hochvolt-Rückruf (kein Fahrzeug-Hardcode,
# keine reale Referenznummer) -- dieselbe Struktur wie
# test_recall_scope_official.py verwendet.
#
# Backend-Folgeauftrag (Powertrain-Präzision): "Hybridantrieb" ist seit der
# Präzisionskorrektur auch in der amtlichen Eingrenzung selbst nicht mehr auf
# eine konkrete Klasse (MHEV/HEV/PHEV) auflösbar und macht die Dimension
# dadurch zu Recht "offen" (siehe app/recall_filter.py::rueckruf_scope, Zweig
# `hybridbezug_erwaehnt`) statt sie stillschweigend zu überspringen -- das
# testet test_recall_scope_official.py bereits separat. DIESER Test will
# END-ZU-ENDE zeigen, dass eine korrekt (ohne Sibling-Konfusion) aufgelöste
# Antriebsart die Dimension auf "passt" heben darf -- dafür braucht die
# amtliche Eingrenzung hier eine ebenso spezifische Formulierung.
_R_HOCHVOLT = {
    "betroffene_baujahre": "2019-2023", "kba_referenz": None,
    "mangel": "Durch einen Fertigungsfehler kann es zu einem Kurzschluss kommen.",
    "eingrenzung_amtlich": "Betroffen sind ausschließlich Fahrzeuge als Vollhybrid.",
}

print("=== 1) Falscher Sibling-Powertrain-Claim setzt identity.powertrain NICHT "
      "(Package A, End-zu-Ende) ===")
treffer_mit_sibling = [
    treffer("https://www.fachmedium1.de/testmarke-modellz-f", "Testmarke Modellz F im Test",
            "Die Testmarke Modellz F ist ein reiner Verbrenner. Die Baureihe gibt es "
            "daneben auch als Testmarke Modellz Hybrid mit Hybridantrieb."),
    treffer("https://www.fachmedium2.de/testmarke-modellz-f", "Testmarke Modellz F Daten",
            "Testmarke Modellz F: reiner Verbrenner. Als Testmarke Modellz Hybrid ist "
            "zusätzlich eine Hybridvariante erhältlich."),
]
identity_sibling = identity_aus_web(treffer_mit_sibling, ZIEL)
check("1a identity.powertrain bleibt None (kein Konsens aus Sibling-Claims)",
      identity_sibling.powertrain is None)

print("\n=== 2) Unbekannter Powertrain -> Recall-Antriebsart-Dimension bleibt "
      "UNKNOWN (offen), nie künstlich 'passt' ===")
scope_sibling, begruendung_sibling = rueckruf_scope(_R_HOCHVOLT, identity_sibling)
check(f"2a Scope ist UNKNOWN (offen), NICHT VARIANT_POSSIBLE (begruendung={begruendung_sibling!r})",
      scope_sibling == RECALL_UNKNOWN)
check("2b Begründung benennt die offene Dimension als 'nicht prüfbar', "
      "nicht als bestätigten Treffer",
      "nicht prüfbar" in (begruendung_sibling or ""))

print("\n=== 3) Erst ein korrekt belegter, passender Powertrain darf die "
      "Dimension auf 'passt' heben ===")
treffer_eindeutig = [
    # Dieselben (echten) Tier-2-Fachmedien-Domains wie in
    # test_phase_completeness_powertrain.py -- `konsens()` verlangt mind.
    # eine Tier<=2-Quelle, bevor ein Wert überhaupt "stark" sein kann;
    # fiktive Domains ohne Tier-Einstufung würden hier IMMER None bleiben.
    # Nur die Domain ist real, Marke/Modell/Inhalt bleiben erfunden.
    #
    # Backend-Folgeauftrag (Powertrain-Präzision): "Hybridantrieb" ist ein
    # generischer Hybridbezug, der nach der Präzisionskorrektur in
    # app/kraftstoff_powertrain.py bewusst NICHT mehr automatisch "HEV"
    # ergibt (könnte genauso Mild- oder Plug-in-Hybrid sein). Diese Fixture
    # prüft die SIBLING-/Konsens-Bindung, nicht die Klassenpräzision -- dafür
    # jetzt eine tatsächlich spezifische Formulierung ("Vollhybrid"), damit
    # der Test weiterhin einen aufgelösten, nicht-None Powertrain liefert.
    treffer("https://www.adac.de/testmarke-modellz-hybrid", "Testmarke Modellz Hybrid im Test",
            "Die Testmarke Modellz Hybrid wird ausschließlich als Vollhybrid angeboten."),
    treffer("https://www.auto-motor-und-sport.de/testmarke-modellz-hybrid", "Testmarke Modellz Hybrid Daten",
            "Testmarke Modellz Hybrid: Vollhybrid, kombinierte Systemleistung."),
]
ziel_hybrid = dict(marke="Testmarke", modell="Modellz Hybrid", baujahr=2021, motor=None)
identity_eindeutig = identity_aus_web(treffer_eindeutig, ziel_hybrid)
check("3a identity.powertrain korrekt als HEV belegt (keine Sibling-Konfusion "
      "in dieser Fixture)", identity_eindeutig.powertrain == "HEV")
scope_eindeutig, begruendung_eindeutig = rueckruf_scope(_R_HOCHVOLT, identity_eindeutig)
check(f"3b Scope wird VARIANT_POSSIBLE -- jetzt auf Basis ECHTER, nicht "
      f"fehlgebundener Evidence (begruendung={begruendung_eindeutig!r})",
      scope_eindeutig == RECALL_VARIANT_POSSIBLE)

print("\n=== 5) Widersprüchliche Powertrain-Claims -> KEIN falscher Konsens, "
      "Recall-Scope bleibt UNKNOWN (§D PASS-GATE-Folgeauftrag) ===")
treffer_widerspruch = [
    treffer("https://www.fachmedium1.de/testmarke-modellz-g", "Testmarke Modellz G im Test",
            "Die Testmarke Modellz G wird ausschließlich mit Hybridantrieb angeboten."),
    treffer("https://www.fachmedium2.de/testmarke-modellz-g", "Testmarke Modellz G Daten",
            "Die Testmarke Modellz G ist ein reiner Verbrenner, kein Hybridantrieb."),
]
ziel_widerspruch = dict(marke="Testmarke", modell="Modellz G", baujahr=2021, motor=None)
identity_widerspruch = identity_aus_web(treffer_widerspruch, ziel_widerspruch)
check("5a widersprechende, vergleichbar starke Quellen -> identity.powertrain "
      "bleibt None (kein 'erstbestes Signal gewinnt')", identity_widerspruch.powertrain is None)
scope_widerspruch, begruendung_widerspruch = rueckruf_scope(_R_HOCHVOLT, identity_widerspruch)
check(f"5b Recall-Scope bleibt UNKNOWN trotz vorhandener (aber widersprüchlicher) "
      f"Evidence (begruendung={begruendung_widerspruch!r})",
      scope_widerspruch == RECALL_UNKNOWN)

print("\n=== 4) Scope / Confidence / Completion bleiben getrennte Konzepte ===")
check("4a rueckruf_scope() liefert niemals 'confirmed_by_vin' (das ist eine "
      "andere, hier nie erzeugte Stufe)",
      scope_sibling not in ("confirmed_by_vin",) and scope_eindeutig not in ("confirmed_by_vin",))
check("4b Scope-Ergebnis ist ein reiner String-Zustand, keine Confidence-Zahl "
      "und kein Severity-Wert vermischt",
      isinstance(scope_sibling, str) and isinstance(scope_eindeutig, str))

print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE RECALL-POWERTRAIN-BINDING-REGRESSIONSTESTS GRUEN")
