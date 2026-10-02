"""
Release-Hardening (Continuation, Blocker 1): eine fehlgeschlagene
Recherchephase muss die Analyse-Confidence beeinflussen, nicht nur eine
Berichtszeile erzeugen. KEIN Netzwerk, KEIN LLM-Call, KEIN Tavily-Call.

BEFUND: `phasen_status` (vorherige Runde) unterschied SUCCESS/PARTIAL/FAILED/
NOT_RUN bereits korrekt, aber nichts in der Empfehlungs-Policy
(app/empfehlungs_policy.py::entscheide) LAS dieses Feld — ein DB-Miss mit
VOLL bestätigter Identität, aber fehlgeschlagener Rückrufrecherche, zeigte
dieselbe Sicherheit (STATE_NORMAL) wie eine vollständig abgeschlossene
Analyse.

REGEL (kleinste generische Erweiterung der BESTEHENDEN Policy, siehe
app/empfehlungs_policy.py): Identität VERIFIED + mindestens eine
sicherheits-/technisch relevante Phase FAILED -> STATE_LIMITED (derselbe
Zustand, der schon für unvollständige Identität existiert), aber die
Empfehlung selbst bleibt UNVERÄNDERT — kein automatisches Zurücksetzen auf
"unbekannt", kein "Finger weg". PARTIAL (dünne Abdeckung) wird bewusst NICHT
eskaliert — das trägt bereits die bestehende Pro-Insight-Confidence
(app/evidence.py, siehe test_phase_completeness_powertrain.py Abschnitt A3).

GENERISCH: alle Fixtures unten verwenden eine erfundene Marke/Modell
("Testmarke Beta"), kein Mazda-/BMW-Code in der Logik.

  1  alle Phasen SUCCESS                         -> STATE_NORMAL, Empfehlung
                                                      unverändert, kein Hinweis
  2  Rückruf SUCCESS mit 0 Treffern               -> weiterhin STATE_NORMAL
                                                      (kein "Bestrafen" von
                                                      ehrlichen Nulltreffern)
  3  Rückruf FAILED                               -> STATE_LIMITED, Empfehlung
                                                      bleibt, Hinweis nennt
                                                      Rückrufrecherche
  4  Technik FAILED                               -> STATE_LIMITED, Hinweis
                                                      nennt die
                                                      Technik-Recherche
  5  Technik PARTIAL (nur Tier-3)                 -> bleibt STATE_NORMAL auf
                                                      Policy-Ebene (die
                                                      niedrigere Confidence
                                                      sitzt bereits am
                                                      einzelnen Web-Insight)
  6  Identität selbst NICHT verifiziert           -> bestehendes Verhalten
     (rueckruf/technik NOT_RUN)                      unverändert (dominiert
                                                      weiterhin)
  7  Bericht: kein doppelter Hinweis bei VERIFIED + FAILED (die zentrale
     Policy kommuniziert es bereits über `entscheidung.hinweis`)

    python test_research_confidence.py
"""
from app.empfehlungs_policy import (
    ANZEIGE_LIMITED_FORSCHUNG, INSUFFICIENT, PARTIAL, STATE_INSUFFICIENT,
    STATE_LIMITED, STATE_NORMAL, VERIFIED, entscheide,
)
from app.kaufcheck_bericht import forschungsluecken_hinweis
from app.models import KaufCheckRequest, TechnischeRecherche
from app.vehicle_identity import VehicleIdentity

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


# ── Synthetische, erfundene Fahrzeugdaten ───────────────────────────────────
def motor(vid, bez, code, kraftstoff, ps):
    return {"variante_id": vid, "bezeichnung": bez, "motorcode": code, "kraftstoff": kraftstoff,
            "leistung_ps": ps, "getriebe": '["6-Gang Manuell"]', "antrieb": "Front",
            "schwachstellen_motor": [], "kritische_wartung": []}


def baureihe(motoren):
    return {"id": "testmarke-beta-t1", "marke": "Testmarke", "modell": "Beta",
            "generation": "T1", "bauzeitraum_von": 2014, "bauzeitraum_bis": 2022,
            "motoren": motoren, "schwachstellen_baureihe": [], "rueckrufe": []}


def req(**kw):
    basis = dict(marke="Testmarke", modell="Beta", baujahr=2018)
    basis.update(kw)
    return KaufCheckRequest(**basis)


m1 = motor("x-1", "2.0 T (150 PS)", "QX12B20", "Benzin", 150)
b1 = baureihe([m1])
r1 = req(motor="QX12 2.0 Turbo", kraftstoff="Benzin", leistung_ps=150, getriebe="manuell",
         antrieb="Front")
IDENTITY_VERIFIED = VehicleIdentity.from_check_context(b1, m1, r1)


def recherche(**phasen_status) -> TechnischeRecherche:
    return TechnischeRecherche(ausgeloest_durch="db_miss", phasen_status=phasen_status)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== Vorbedingung: die synthetische Identität ist wirklich VERIFIED ===")
check("V0 IDENTITY_VERIFIED ist tatsächlich VERIFIED (sonst testen die Fälle 1-5 das Falsche)",
      entscheide("kaufen_nach_besichtigung", IDENTITY_VERIFIED).identitaet.stufe == VERIFIED)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== 1) Alle Phasen SUCCESS -> STATE_NORMAL ===")
r_voll = recherche(identitaet="success", rueckruf="success", technik="success")
e1 = entscheide("kaufen_nach_besichtigung", IDENTITY_VERIFIED, web_recherche=r_voll)
check("1a state == STATE_NORMAL", e1.state == STATE_NORMAL)
check("1b Empfehlung unverändert", e1.empfehlung == "kaufen_nach_besichtigung")
check("1c kein Hinweis", e1.hinweis is None)
check("1d anzeige trägt KEINEN Forschungs-Zusatz", ANZEIGE_LIMITED_FORSCHUNG not in e1.anzeige)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== 2) Rückruf SUCCESS mit 0 Treffern -> weiterhin STATE_NORMAL ===")
# phasen_status kennt nur den AUSFÜHRUNGSSTATUS, nicht die Trefferzahl — ein
# Rückruf-Fund-Objekt mit fakten=[] und phasen_status rueckruf=SUCCESS ist
# ein gültiges, vollständiges Nulltreffer-Ergebnis (siehe vorherige Runde,
# test_phase_completeness_powertrain.py A1). Dieselbe Fixture wie Test 1
# beweist das bereits strukturell (phasen_status enthält keine Zählung),
# hier zusätzlich mit explizit leeren `fakten`.
r_null = TechnischeRecherche(ausgeloest_durch="db_miss", fakten=[],
                             phasen_status={"identitaet": "success", "rueckruf": "success",
                                           "technik": "success"})
e2 = entscheide("kaufen_nach_besichtigung", IDENTITY_VERIFIED, web_recherche=r_null)
check("2a state bleibt STATE_NORMAL (0 Treffer ist kein Fehlschlag)", e2.state == STATE_NORMAL)
check("2b Empfehlung unverändert, nicht 'bestraft'", e2.empfehlung == "kaufen_nach_besichtigung")


# ══════════════════════════════════════════════════════════════════════════
print("\n=== 3) Rückruf FAILED -> STATE_LIMITED, Empfehlung bleibt ===")
r_rr_failed = recherche(identitaet="success", rueckruf="failed", technik="success")
e3 = entscheide("kaufen_nach_besichtigung", IDENTITY_VERIFIED, web_recherche=r_rr_failed)
check("3a state == STATE_LIMITED", e3.state == STATE_LIMITED)
check("3b Empfehlung bleibt UNVERÄNDERT (kein automatisches 'unbekannt'/'Finger weg')",
      e3.empfehlung == "kaufen_nach_besichtigung")
check("3c Hinweis nennt die Rückrufrecherche", e3.hinweis is not None and "Rückrufrecherche" in e3.hinweis)
check("3d anzeige trägt den Forschungs-Zusatz", ANZEIGE_LIMITED_FORSCHUNG in e3.anzeige)
check("3e Identitätsstufe bleibt VERIFIED (die Identität selbst ist nicht das Problem)",
      e3.identitaet.stufe == VERIFIED)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== 4) Technik FAILED -> STATE_LIMITED, Hinweis nennt Technik-Recherche ===")
r_tech_failed = recherche(identitaet="success", rueckruf="success", technik="failed")
e4 = entscheide("kaufen_nach_besichtigung", IDENTITY_VERIFIED, web_recherche=r_tech_failed)
check("4a state == STATE_LIMITED", e4.state == STATE_LIMITED)
check("4b Empfehlung bleibt unverändert", e4.empfehlung == "kaufen_nach_besichtigung")
check("4c Hinweis nennt technische Schwachstellen, NICHT die Rückrufrecherche",
      e4.hinweis is not None and "technischen Schwachstellen" in e4.hinweis
      and "Rückrufrecherche" not in e4.hinweis)

print("--- Zusatz: Rückruf UND Technik FAILED -> beide im Hinweis genannt ---")
r_beide_failed = recherche(identitaet="success", rueckruf="failed", technik="failed")
e_beide = entscheide("kaufen_nach_besichtigung", IDENTITY_VERIFIED, web_recherche=r_beide_failed)
check("4d beide Phasen im selben Hinweis genannt",
      "Rückrufrecherche" in e_beide.hinweis and "technischen Schwachstellen" in e_beide.hinweis)
check("4e trotzdem nur EIN Satz pro Phase (keine Dopplung)",
      e_beide.hinweis.count("Rückrufrecherche") == 1)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== 5) Technik PARTIAL (nur Tier-3) -> bleibt STATE_NORMAL auf Policy-Ebene ===")
r_partial = recherche(identitaet="success", rueckruf="success", technik="partial")
e5 = entscheide("kaufen_nach_besichtigung", IDENTITY_VERIFIED, web_recherche=r_partial)
check("5a state bleibt STATE_NORMAL (PARTIAL eskaliert NICHT die Empfehlungs-Policy)",
      e5.state == STATE_NORMAL)
check("5b kein Policy-Hinweis (die niedrigere Confidence sitzt am einzelnen "
      "Web-Insight, nicht hier — siehe test_phase_completeness_powertrain.py A3)",
      e5.hinweis is None)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== 6) Identität selbst NICHT verifiziert -> bestehendes Verhalten unverändert ===")
identitaet_unsicher = VehicleIdentity.from_check_context(None, None, req())
r_notrun = recherche(identitaet="failed", rueckruf="not_run", technik="not_run")
e6 = entscheide("kaufen_nach_besichtigung", identitaet_unsicher, web_recherche=r_notrun)
check("6a Identitäts-Unsicherheit dominiert weiterhin (state INSUFFICIENT, nicht durch "
      "die neue Forschungs-Logik verändert)",
      e6.state == STATE_INSUFFICIENT and e6.identitaet.stufe == INSUFFICIENT)
check("6b Empfehlung wird wie bisher auf 'unbekannt' zurückgesetzt "
      "(unverändertes bestehendes Verhalten)", e6.empfehlung == "unbekannt")
e6b = entscheide("kaufen_nach_besichtigung", identitaet_unsicher)
check("6c identisches Ergebnis auch ganz ohne web_recherche-Argument (voll "
      "rückwärtskompatibel)", e6b.state == e6.state and e6b.empfehlung == e6.empfehlung)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== 7) Bericht: kein doppelter Hinweis bei VERIFIED + FAILED ===")
check("7a forschungsluecken_hinweis() liefert NICHTS, wenn die Policy (entscheidung) "
      "dieselbe Lücke schon über `.hinweis` trägt (VERIFIED)",
      forschungsluecken_hinweis(r_rr_failed, e3) == [])
check("7b dieselbe Funktion liefert weiterhin den Standalone-Hinweis, wenn die "
      "Identität NICHT verifiziert ist (die Policy deckt diesen Fall nicht ab)",
      len(forschungsluecken_hinweis(r_rr_failed, None)) > 0)


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE RESEARCH-CONFIDENCE-TESTS GRUEN")
