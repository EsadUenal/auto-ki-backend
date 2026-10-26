"""
Produktentscheidung (Fortsetzungsauftrag, "Gestützte Wartungshinweise sichtbar
machen"): CORROBORATED-Wartungskandidaten (>=2 unabhängige, nicht-UNKNOWN
Secondary-Quellen, gleicher Wert, kein Primär-/Herstellerbeleg) werden jetzt
als EIGENER, klar schwächer markierter Insight ausgegeben, statt intern
verworfen zu werden. Der Evidenzstatus selbst bleibt unverändert CORROBORATED
-- das ist KEINE Freigabe, diese Angaben als VERIFIED zu behandeln.

Realer Fall: carwiki.de + repareo.de (beide real, siehe test_replay_
fortsetzung_abnahme.py, WebFetch 2026-10-26) nennen übereinstimmend
"20.000 km / 12 Monate" für den Mazda MX-5 ND -- bisher NIE sichtbar
(CORROBORATED galt als grundsätzlich unsichtbar), jetzt als eigener,
niedrig-confidence Hinweis mit beiden Quellen.

    python scripts/test_isolated.py test_wartung_corroborated_sichtbar.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import kaufcheck as kc
from app.models import KaufCheckRequest, KaufCheckResponse
from app.technical_research import (
    FixtureTechnicalResearchProvider, _wartung_candidate_fakten, recherchiere_technisch,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── Teil A: reine Candidate-Logik (_wartung_candidate_fakten) ───────────────
_CARWIKI_ND = t("https://carwiki.de/mazda-mx-5-oelwechsel", "Der Ölwechsel beim Mazda MX-5",
                "Beim Mazda MX-5 ND (ab 2015) muss der Ölwechsel immer nach 20.000 Kilometern "
                "oder spätestens nach 12 Monaten erfolgen.")
_REPAREO_ND = t("https://www.repareo.de/oelwechsel-mazda-mx-5/", "Ölwechsel Mazda MX-5",
                "Roadster aktuellerer Baujahre wie der Mazda MX-5 ND benötigen alle 20.000 km "
                "oder alle 12 Monate einen Ölwechsel.")

_f, _ab, _esk = _wartung_candidate_fakten(
    [_CARWIKI_ND, _REPAREO_ND], marke="Mazda", modell="MX-5", generation="ND",
    motor="2.0 SKYACTIV-G", getriebe="Schaltgetriebe", haupt_fakten=[], erlaube_multi_source=False)
check("A1: reale carwiki.de+repareo.de-Übereinstimmung ist jetzt sichtbar (1 Fakt, vorher 0)",
      len(_f) == 1, str(_f))
if _f:
    check("A2: Evidenzstatus bleibt CORROBORATED (eigener beleglage-Wert, NIE 'verified')",
          _f[0].beleglage == "uebereinstimmend_unbestaetigt", str(_f[0].beleglage))
    check("A3: Confidence bleibt niedrig (schwächer als 'mehrfach_unabhaengig'/MULTI_SOURCE)",
          _f[0].confidence == "niedrig", str(_f[0].confidence))
    check("A4: beide realen Quellen sind zitiert", {q.url for q in _f[0].quellen} ==
          {_CARWIKI_ND["url"], _REPAREO_ND["url"]}, str([q.url for q in _f[0].quellen]))

# UNKNOWN-Tier bleibt unverändert unsichtbar (alter Schutz, nicht gelockert).
_UNKNOWN_1 = t("https://irgendein-blog.example/mx5-notizen", "Meine MX-5 Notizen",
              "Beim Mazda MX-5 ND (ab 2015) muss das Motoröl alle 20.000 km gewechselt werden.")
_UNKNOWN_2 = t("https://ein-anderes-forum.example/mx5-thread", "MX-5 Thread",
              "Beim Mazda MX-5 ND (ab 2015) muss das Motoröl alle 20.000 km gewechselt werden.")
_fu, _abu, _esku = _wartung_candidate_fakten(
    [_UNKNOWN_1, _UNKNOWN_2], marke="Mazda", modell="MX-5", generation="ND",
    motor="2.0 SKYACTIV-G", getriebe="Schaltgetriebe", haupt_fakten=[], erlaube_multi_source=False)
check("B1: UNKNOWN+UNKNOWN bleibt unsichtbar (Schutz vor Quellenaufwertung unverändert)",
      len(_fu) == 0, str(_fu))
check("B2: Ablehnungsgrund bleibt nachvollziehbar (eigener Grund, kein stilles Verschwinden)",
      any(a.get("grund") == "wartung_corroborated_unknown_tier" for a in _abu), str(_abu))

# Widersprechende Werte (CONFLICT) bleiben unverändert unsichtbar.
_WIDERSPRUCH = t("https://www.autoteile-xyz.de/mx5-oelwechsel", "Mazda MX-5 Ölwechsel",
                 "Beim Mazda MX-5 ND (ab 2015) muss der Ölwechsel alle 10.000 km erfolgen.")
_fc, _abc, _eskc = _wartung_candidate_fakten(
    [_CARWIKI_ND, _WIDERSPRUCH], marke="Mazda", modell="MX-5", generation="ND",
    motor="2.0 SKYACTIV-G", getriebe="Schaltgetriebe", haupt_fakten=[], erlaube_multi_source=False)
check("C1: widersprechende Werte (20.000 vs. 10.000 km) bleiben CONFLICT, nicht sichtbar",
      len(_fc) == 0 and any(a.get("grund") == "wartung_candidate_conflict" for a in _abc),
      str(_abc))


# ── Teil B: End-to-End über run_kaufcheck -- sichtbarer Bericht ────────────
async def _lauf(req: KaufCheckRequest, web_fixture: dict) -> KaufCheckResponse:
    orig = (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
            kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche)

    async def gemini(system, user):
        return {"bericht": "KAUFEN.", "empfehlung": "kaufen",
                "risiko_evidence_ids": [], "empfehlung_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen")

    prov = FixtureTechnicalResearchProvider(web_fixture)

    async def technik(r, br, info, brg, mm, provider=None):
        return await recherchiere_technisch(r, br, info, brg, mm, provider=prov)

    kc.call_gemini_json, kc.recherchiere_technisch = gemini, technik
    kc.tavily_search_with_fallback, kc.vertiefe_marktrecherche = verboten, verboten
    kc.marktpreis_recherche_moeglich = lambda *a: False
    try:
        return KaufCheckResponse(**await kc.run_kaufcheck(req))
    finally:
        (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
         kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche) = orig


_WEB_FIXTURE = {
    "identitaet": [
        t("https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/mazda/mx-5/",
          "Mazda MX-5 (ND) seit 2015: Technische Daten",
          "Der Mazda MX-5 ND (seit 2015) ist die vierte Generation des Roadsters. Der 2.0 "
          "SKYACTIV-G leistet seit dem Modelljahr 2019 184 PS, Benziner, Hinterradantrieb, "
          "6-Gang-Schaltgetriebe."),
        t("https://www.auto-motor-und-sport.de/test/mazda-mx-5-2-0-skyactiv-g-184/",
          "Mazda MX-5 2.0 Skyactiv-G 184 im Test",
          "Die vierte Generation des Mazda MX-5 (ND, seit 2015): 2.0 Skyactiv-G mit 184 PS, "
          "Benziner, Heckantrieb und 6-Gang-Schaltgetriebe."),
    ],
    "wartung": [_CARWIKI_ND, _REPAREO_ND],
    "schwachstelle": [], "rueckruf": [],
}

_r = asyncio.run(_lauf(KaufCheckRequest(
    marke="Mazda", modell="MX-5", baujahr=2019, kilometerstand=31000, motor="2.0 SKYACTIV-G",
    kraftstoff="Benzin", leistung_ps=184, getriebe="Schaltgetriebe", antrieb="Heck",
    ausstattung=[]), web_fixture=_WEB_FIXTURE))

check("D1: kein DB-Treffer (reiner Web-Fall, nicht mit der realen ENFAL-Seed-Baureihe verwechselt)",
      _r.baureihe_erkannt is None, str(_r.baureihe_erkannt))
_web_wartung = [i for i in _r.insights if i.kategorie == "web_wartung"]
check("D2: genau EIN sichtbarer web_wartung-Insight im Bericht", len(_web_wartung) == 1,
      str([i.titel for i in _web_wartung]))
if _web_wartung:
    i = _web_wartung[0]
    check("D3: Titel benennt bereits selbst, was offen ist (kein Herstellerbeleg)",
          "Herstellerbeleg" in i.titel, i.titel)
    check("D4: Beschreibung nennt den Wartungsgegenstand und die konkrete Angabe (20.000)",
          "20.000" in i.beschreibung, i.beschreibung)
    check("D5: Einfluss erklärt verständlich Übereinstimmung + fehlende Primärbestätigung",
          "übereinstimmend" in i.einfluss and "nicht ausreichend bestätigt" in i.einfluss, i.einfluss)
    check("D6: Einfluss nennt die konkrete Gegenprüfung (Wartungsplan/Serviceheft/Hersteller)",
          "Wartungsplan" in i.einfluss and "Serviceheft" in i.einfluss and "Hersteller" in i.einfluss,
          i.einfluss)
    check("D7: Confidence bleibt niedrig im sichtbaren Insight", i.confidence == "niedrig")
    check("D8: beide realen Quellen erscheinen im Insight", len(i.quellen) == 2,
          str([q.url for q in i.quellen]))
check("D9: Hinweis steht im sichtbaren Bericht (res.bericht)", "Ölwechsel" in _r.bericht
      or "Oelwechsel" in _r.bericht)

# Schutzregeln: keine abgeleitete Überfälligkeit/Risikopunkte/Preisabzüge/
# negative Empfehlung allein aus diesem Hinweis.
check("E1: keine Überfälligkeits-/Fälligkeits-Behauptung im Bericht",
      not any(w in _r.bericht for w in ("ist fällig", "überfällig", "Wartungsversäumnis")))
check("E2: Empfehlung bleibt normal (kein Preis-/Risikoabzug allein durch diesen Hinweis)",
      _r.recommendation_state == "NORMAL" and _r.empfehlung in ("kaufen", "kaufen_nach_besichtigung"),
      f"{_r.recommendation_state} / {_r.empfehlung}")
check("E3: dieser Hinweis erscheint NICHT als Empfehlungsgrund (bleibt WARTUNG_REGULAER, "
      "kein eigener Risikopunkt)", not any("Ölwechsel" in g or "Oelwechsel" in g
                                           for g in _r.empfehlung_gruende), str(_r.empfehlung_gruende))
check("E4: keine Verwechslung mit einem bestätigten Wartungsplan -- die Datenqualitäts-"
      "zeile selbst nennt 'niedrig', kein 'bestätigt'/'nachgewiesen' ohne Einschränkung",
      "Quellen-/Datensatzqualität: niedrig." in _r.bericht, _r.bericht[:2000])


# ── Fachliche Passung: nicht allein aus identischen Zahlen ableiten ────────
# Zwei Quellen mit DEMSELBEN Intervall, aber einem ANDEREN Wartungsgegenstand
# (Bremsflüssigkeit statt Ölwechsel) dürfen NICHT zusammengeführt werden --
# die Signatur (Bauteil+Aktion+Regime+Intervall) muss vollständig matchen,
# nicht nur die Zahl.
_ANDERER_GEGENSTAND = t("https://www.autoteile-xyz.de/mx5-bremsfluessigkeit",
                        "Mazda MX-5 Bremsflüssigkeit",
                        "Beim Mazda MX-5 ND (ab 2015) wird die Bremsflüssigkeit alle 20.000 km "
                        "gewechselt.")
_fg, _abg, _eskg = _wartung_candidate_fakten(
    [_CARWIKI_ND, _ANDERER_GEGENSTAND], marke="Mazda", modell="MX-5", generation="ND",
    motor="2.0 SKYACTIV-G", getriebe="Schaltgetriebe", haupt_fakten=[], erlaube_multi_source=False)
check("F1: gleiche Zahl (20.000 km), aber ANDERER Wartungsgegenstand (Bremsflüssigkeit "
      "statt Ölwechsel) -> KEINE Zusammenführung -- jeweils nur 1 Familie, nichts sichtbar",
      len([f for f in _fg if getattr(f, "beleglage", None) == "uebereinstimmend_unbestaetigt"]) == 0,
      str(_fg))


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE WARTUNG-CORROBORATED-SICHTBAR-TESTS GRUEN")
