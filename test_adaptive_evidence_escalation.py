"""
Adaptive-Evidence-Research (Folgeauftrag): erster, bewusst scoped Schritt
Richtung der im Auftrag beschriebenen Zielarchitektur -- GENAU EINE
zusätzliche, orthogonale Suchrunde für "schwachstelle"/"wartung", NUR wenn
Runde 1 einen Evidence-Gap signalisiert (`eskalierbar`), PLUS ein neuer,
eigenständiger (aber klar schwächer gekennzeichneter) MULTI_SOURCE-Fakt für
>=3 unabhängige, nicht-COMMUNITY/nicht-UNKNOWN Families OHNE Herstellerbeleg
-- NIE "verified", NUR wenn eine vorherige Source-Upgrade-Nachrecherche
ergebnislos blieb (`erlaube_multi_source`).

GENERISCH getestet (erfundene Marke/Modell, erfundene Domains). KEIN
Netzwerk -- Teil 2 patcht `tavily_search` durch eine deterministische Fake-
Implementierung (identisches Muster wie
test_technical_research_wartung_orchestration.py). 0 Live Calls.

    python test_adaptive_evidence_escalation.py
"""
import asyncio

import app.technical_research as technical_research
import app.web_search as web_search
from app.evidence import _WEB_EINFLUSS_MULTI_SOURCE
from app.models import WebVehicleIdentity
from app.technical_research import (
    MIN_FAMILIES_MULTI_SOURCE,
    TavilyTechnicalResearchProvider,
    WARTUNG_STATUS_MULTI_SOURCE,
    _identity_spec_gap_queries,
    _identity_spec_gaps,
    _schwachstelle_candidate_fakten,
    _wartung_candidate_fakten,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url: str, titel: str, inhalt: str) -> dict:
    return {"url": url, "title": titel, "content": inhalt}


ZIEL = dict(marke="Testmarke", modell="Modellz", generation=None)

# Drei unabhängige, nicht-amtliche/nicht-Hersteller-Domains (Score < MIN_SCORE_FAKT,
# siehe app/web_search.py _TIER_*), deren TITEL das generische Wartungs-/
# Reparatur-Vokabular enthalten (`_WARTUNG_TITEL_SERVICE_SIGNAL`) -- dieselbe,
# bereits bestehende, domain-unabhängige Klassifikation wie in
# test_wartung_motoroel_vokabular.py (Tests C/D), nur um eine dritte Family
# erweitert. KEIN neuer Mechanismus, nur eine dritte unabhängige Quelle.
WA_FAMILIE_1 = treffer("https://www.kfzwissen-a.de/testmarke-modellz-wartung", "Testmarke Modellz Wartung A",
                      "Der Zahnriemen wird bei der Testmarke Modellz alle 180.000 km gewechselt.")
WA_FAMILIE_2 = treffer("https://www.kfzwissen-b.de/testmarke-modellz-inspektion",
                      "Testmarke Modellz Inspektion B",
                      "Der Zahnriemen wird bei der Testmarke Modellz alle 180.000 km gewechselt.")
WA_FAMILIE_3 = treffer("https://www.kfzwissen-c.de/testmarke-modellz-werkstatt",
                      "Testmarke Modellz Werkstattbericht C",
                      "Der Zahnriemen wird bei der Testmarke Modellz alle 180.000 km gewechselt.")
WA_FAMILIE_3_WIDERSPRUCH = treffer(
    "https://www.kfzwissen-c.de/testmarke-modellz-werkstatt-2", "Testmarke Modellz Werkstattbericht C2",
    "Der Zahnriemen wird bei der Testmarke Modellz alle 120.000 km gewechselt.")
WA_COMMUNITY_1 = treffer("https://motor-talk.de/testmarke-modellz-thread", "Testmarke Modellz Forum",
                        "Der Zahnriemen wird bei der Testmarke Modellz alle 180.000 km gewechselt.")
WA_COMMUNITY_2 = treffer("https://www.reddit.com/r/testmarke_modellz", "Testmarke Modellz Reddit",
                        "Der Zahnriemen wird bei der Testmarke Modellz alle 180.000 km gewechselt.")

SW_FAMILIE_1 = treffer("https://www.kfzwissen-a.de/testmarke-modellz-reparatur",
                      "Testmarke Modellz: Reparatur-Erfahrungen A",
                      "Die Wasserpumpe der Testmarke Modellz wird häufig undicht.")
SW_FAMILIE_2 = treffer("https://www.kfzwissen-b.de/testmarke-modellz-werkstatt",
                      "Testmarke Modellz Werkstattbericht B",
                      "Bei der Testmarke Modellz tritt an der Wasserpumpe häufig ein Leck auf.")
SW_FAMILIE_3 = treffer("https://www.kfzwissen-c.de/testmarke-modellz-reparatur",
                      "Testmarke Modellz: Reparatur-Erfahrungen C",
                      "Die Wasserpumpe der Testmarke Modellz ist anfällig und verliert Kühlmittel.")
# Dieselbe Komponente (Wasserpumpe), aber DREI verschiedene Fehlertypen --
# dürfen NIE zusammen einen Fakt erzeugen (Weakness-Claim-Signatur, §B
# Auftrag).
SW_ANDERER_FEHLERTYP_GERAEUSCH = treffer(
    "https://www.kfzwissen-d.de/testmarke-modellz-reparatur", "Testmarke Modellz: Reparatur-Erfahrungen D",
    "Die Wasserpumpe der Testmarke Modellz entwickelt häufig ein Quietschen.")
SW_ANDERER_FEHLERTYP_AUSFALL = treffer(
    "https://www.kfzwissen-e.de/testmarke-modellz-reparatur", "Testmarke Modellz: Reparatur-Erfahrungen E",
    "Die Wasserpumpe der Testmarke Modellz hat häufig einen Totalausfall.")
# Fehlertyp nicht sicher bestimmbar (nur "Problem", kein konkretes Muster) --
# darf mit NICHTS zusammengeführt werden, auch nicht mit einem zweiten,
# ebenfalls unbestimmten Claim.
SW_UNBESTIMMTER_FEHLERTYP_1 = treffer(
    "https://www.kfzwissen-f.de/testmarke-modellz-reparatur", "Testmarke Modellz: Reparatur-Erfahrungen F",
    "Die Testmarke Modellz hat ein bekanntes Problem mit der Wasserpumpe.")
SW_UNBESTIMMTER_FEHLERTYP_2 = treffer(
    "https://www.kfzwissen-g.de/testmarke-modellz-reparatur", "Testmarke Modellz: Reparatur-Erfahrungen G",
    "Die Wasserpumpe der Testmarke Modellz gilt als Schwachstelle.")

print("=== A) Wartung: 3 unabhängige Families, gleicher Wert, OHNE Erlaubnis "
      "-> bleibt (noch) nicht sichtbar, aber eskalierbar=True ===")
fakten_a, abgelehnt_a, eskalierbar_a = _wartung_candidate_fakten(
    [WA_FAMILIE_1, WA_FAMILIE_2, WA_FAMILIE_3], marke=ZIEL["marke"], modell=ZIEL["modell"],
    generation=ZIEL["generation"], motor=None, erlaube_multi_source=False)
check("A1 kein sichtbarer Fakt ohne erlaube_multi_source", fakten_a == [])
check("A2 eskalierbar=True signalisiert den Gap an den Aufrufer", eskalierbar_a is True)
check("A3 Reject-Grund 'wartung_multi_source_pending_upgrade' sichtbar",
      any(a.get("grund") == "wartung_multi_source_pending_upgrade" for a in abgelehnt_a),
      str(abgelehnt_a))

print("\n=== B) Dieselben 3 Families, MIT erlaube_multi_source=True -> EIN "
      "eigenständiger, schwächer gekennzeichneter Fakt ===")
fakten_b, abgelehnt_b, eskalierbar_b = _wartung_candidate_fakten(
    [WA_FAMILIE_1, WA_FAMILIE_2, WA_FAMILIE_3], marke=ZIEL["marke"], modell=ZIEL["modell"],
    generation=ZIEL["generation"], motor=None, erlaube_multi_source=True)
check("B1 genau EIN sichtbarer Fakt", len(fakten_b) == 1, str(fakten_b))
check("B2 confidence bleibt 'mittel', NIE 'hoch' (nie 'verified')",
      bool(fakten_b) and fakten_b[0].confidence == "mittel")
check("B3 beleglage korrekt markiert", bool(fakten_b) and fakten_b[0].beleglage == "mehrfach_unabhaengig")
check("B4 alle 3 Quellen im Fakt enthalten (eine Quelle je Family, dedupliziert)",
      bool(fakten_b) and len(fakten_b[0].quellen) == 3, str(fakten_b[0].quellen) if fakten_b else "")

print("\n=== C) Nur 2 Families (unter der MULTI_SOURCE-Schwelle) -> bleibt "
      "CORROBORATED-nicht-sichtbar, AUCH MIT erlaube_multi_source=True ===")
check(f"Schwelle ist {MIN_FAMILIES_MULTI_SOURCE} (Kontrolle)", MIN_FAMILIES_MULTI_SOURCE == 3)
fakten_c, abgelehnt_c, eskalierbar_c = _wartung_candidate_fakten(
    [WA_FAMILIE_1, WA_FAMILIE_2], marke=ZIEL["marke"], modell=ZIEL["modell"],
    generation=ZIEL["generation"], motor=None, erlaube_multi_source=True)
check("C1 kein sichtbarer Fakt bei nur 2 Families, obwohl erlaubt", fakten_c == [])
check("C2 Grund bleibt das bestehende 'wartung_corroborated_not_verified' (unveraendert)",
      any(a.get("grund") == "wartung_corroborated_not_verified" for a in abgelehnt_c), str(abgelehnt_c))

print("\n=== D) 3 Families, aber EINE widerspricht im Wert -> CONFLICT, NIE "
      "MULTI_SOURCE (auch nicht mit erlaube_multi_source=True) ===")
fakten_d, abgelehnt_d, _ = _wartung_candidate_fakten(
    [WA_FAMILIE_1, WA_FAMILIE_2, WA_FAMILIE_3_WIDERSPRUCH], marke=ZIEL["marke"], modell=ZIEL["modell"],
    generation=ZIEL["generation"], motor=None, erlaube_multi_source=True)
check("D1 kein Fakt bei Widerspruch", fakten_d == [])
check("D2 als Conflict erkannt", any(a.get("grund") == "wartung_candidate_conflict" for a in abgelehnt_d),
      str(abgelehnt_d))

print("\n=== E) 3 COMMUNITY-Quellen (Forenbeiträge) -> zählen NIE als eigene "
      "Family, auch nicht zusammen, auch nicht mit erlaube_multi_source=True ===")
fakten_e, abgelehnt_e, eskalierbar_e = _wartung_candidate_fakten(
    [WA_COMMUNITY_1, WA_COMMUNITY_2], marke=ZIEL["marke"], modell=ZIEL["modell"],
    generation=ZIEL["generation"], motor=None, erlaube_multi_source=True)
check("E1 kein Fakt aus reinem Community-Konsens", fakten_e == [])
check("E2 nicht einmal als eskalierbar markiert (0 zaehlende Families)", eskalierbar_e is False)

print("\n=== F) Schwachstelle: dieselbe Logik -- 3 unabhängige Families "
      "OHNE Hauptpfad-Anker, OHNE Erlaubnis -> eskalierbar, kein Fakt ===")
fakten_f, abgelehnt_f, eskalierbar_f = _schwachstelle_candidate_fakten(
    [SW_FAMILIE_1, SW_FAMILIE_2, SW_FAMILIE_3], marke=ZIEL["marke"], modell=ZIEL["modell"],
    generation=ZIEL["generation"], haupt_fakten=[], erlaube_multi_source=False)
check("F1 kein sichtbarer Fakt ohne Erlaubnis", fakten_f == [])
check("F2 eskalierbar=True", eskalierbar_f is True)
check("F3 Grund 'schwachstelle_multi_source_pending_upgrade' sichtbar",
      any(a.get("grund") == "schwachstelle_multi_source_pending_upgrade" for a in abgelehnt_f),
      str(abgelehnt_f))

print("\n=== G) Schwachstelle: dieselben 3 Families MIT Erlaubnis -> EIN "
      "eigenständiger, schwächer gekennzeichneter Fakt ===")
fakten_g, abgelehnt_g, _ = _schwachstelle_candidate_fakten(
    [SW_FAMILIE_1, SW_FAMILIE_2, SW_FAMILIE_3], marke=ZIEL["marke"], modell=ZIEL["modell"],
    generation=ZIEL["generation"], haupt_fakten=[], erlaube_multi_source=True)
check("G1 genau EIN sichtbarer Fakt", len(fakten_g) == 1, str(fakten_g))
check("G2 confidence='mittel', beleglage korrekt",
      bool(fakten_g) and fakten_g[0].confidence == "mittel"
      and fakten_g[0].beleglage == "mehrfach_unabhaengig")
check("G3 das Bauteil im Fakt ist 'Wasserpumpe' (Kontrolle fuer die Tests unten)",
      bool(fakten_g) and fakten_g[0].bauteil == "Wasserpumpe", str(fakten_g))

print("\n=== Weakness-Claim-Signatur (§B Auftrag) ===")

print("--- W1) 3 Families, gleiches Bauteil + GLEICHER Fehlertyp -> MULTI_SOURCE moeglich "
      "(= Test G oben, hier nur als expliziter Matrix-Fall wiederholt) ---")
check("W1 siehe G1/G2 oben", len(fakten_g) == 1 and fakten_g[0].beleglage == "mehrfach_unabhaengig")

print("--- W2) 3 Families, gleiches Bauteil + DREI VERSCHIEDENE Fehlertypen -> "
      "NIEMALS zusammen corroborated ---")
fakten_w2, abgelehnt_w2, eskalierbar_w2 = _schwachstelle_candidate_fakten(
    [SW_FAMILIE_1, SW_ANDERER_FEHLERTYP_GERAEUSCH, SW_ANDERER_FEHLERTYP_AUSFALL],
    marke=ZIEL["marke"], modell=ZIEL["modell"], generation=ZIEL["generation"],
    haupt_fakten=[], erlaube_multi_source=True)
check("W2a kein gemeinsamer Fakt trotz 3 Quellen zum selben Bauteil -- "
      "'undicht'/'Geraeusch'/'Ausfall' sind DREI verschiedene Schwachstellen",
      fakten_w2 == [], str(fakten_w2))
check("W2b jede der drei bleibt isoliert (je 1 Family -> nicht einmal eskalierbar)",
      eskalierbar_w2 is False, str(abgelehnt_w2))

print("--- W3) gleicher Fehlertyp, unterschiedliche Formulierung (Synonyme) -> "
      "matcht nach sicherer Normalisierung trotzdem zusammen ---")
check("W3 'undicht' und 'Leck' normalisieren auf denselben Fehlertyp",
      technical_research._schwachstelle_failure_mode("wird haeufig undicht")
      == technical_research._schwachstelle_failure_mode("ein Leck tritt auf"))
check("W3b Test G (unterschiedliche Formulierungen: undicht/Leck/verliert Kuehlmittel) "
      "ist der praktische Beweis dafuer -- alle 3 wurden zusammengefuehrt", len(fakten_g) == 1)

print("--- W4) Sibling-Variante (fremdes Modell im Claim) -> weiterhin ausgeschlossen "
      "(bestehende Claim-Binding-Logik aus der vorigen Runde, hier NICHT neu gebaut, "
      "nur als Kontrolle mitgefuehrt) ---")
SW_SIBLING = treffer("https://www.kfzwissen-h.de/testmarke-modellino-reparatur",
                     "Testmarke Modellino: Reparatur-Erfahrungen H",
                     "Die Testmarke Modellino hat eine undichte Wasserpumpe.")
fakten_w4, abgelehnt_w4, _ = _schwachstelle_candidate_fakten(
    [SW_FAMILIE_1, SW_FAMILIE_2, SW_SIBLING], marke=ZIEL["marke"], modell=ZIEL["modell"],
    generation=ZIEL["generation"], haupt_fakten=[], erlaube_multi_source=True)
check("W4 Sibling-Claim traegt NICHT zur Family-Zaehlung bei -- nur 2 echte Families, "
      "kein MULTI_SOURCE-Fakt", fakten_w4 == [], str(fakten_w4))

print("--- W5) Widersprechender Claim (hier: Fehlertyp-Divergenz bei sonst gleichem "
      "Bauteil) -> keine Promotion (identisch zu W2 -- Weakness hat keinen "
      "numerischen Wert wie Wartung, die Fehlertyp-Trennung UEBERNIMMT diese Rolle) ---")
check("W5 siehe W2 -- strukturell identischer Schutz", fakten_w2 == [])

print("\n=== H) Exakte, vom Auftrag vorgegebene User-Facing-Formulierung "
      "(nie 'verifiziert') ===")
check("H1 Weakness-Satz vorhanden und enthaelt NICHT 'verifiziert'",
      "verifiziert" not in _WEB_EINFLUSS_MULTI_SOURCE["schwachstelle"].lower())
check("H2 Maintenance-Satz entspricht der Auftragsvorgabe",
      _WEB_EINFLUSS_MULTI_SOURCE["wartung"]
      == "Mehrere unabhängige Quellen nennen dieses Intervall; ein Herstellerbeleg "
         "wurde nicht gefunden.")

print("\n=== Identity-Spec-Gap-Recherche (§C Auftrag) ===")


def _identitaet(**kwargs) -> WebVehicleIdentity:
    basis = dict(belegt=True, marke="Testmarke", modell="Modellz", generation="G1",
                kraftstoff="Benzin", antrieb="Front", getriebe="automatik", powertrain="ICE")
    basis.update(kwargs)
    return WebVehicleIdentity(**basis)


print("--- Transmission-Spec-H) nur EIN Feld offen -> gezielte, aber weiterhin "
      "gebündelte (nicht pro Feld einzelne) Query ---")
nur_getriebe_offen = _identitaet(getriebe=None)
gaps_h = _identity_spec_gaps(nur_getriebe_offen)
check("H) genau 'transmission' als offenes Feld erkannt", gaps_h == ["transmission"], str(gaps_h))
queries_h = _identity_spec_gap_queries("Testmarke Modellz", "2018", gaps_h)
check("H) genau 2 Queries (DE+EN), nicht mehr", len(queries_h) == 2, str(queries_h))
check("H) Query nennt NUR Getriebe/transmission, keine anderen Feld-Woerter "
      "(Generation/Kraftstoff/Antrieb/Hybrid waeren bereits belegt)",
      all(w not in queries_h[0][1] for w in ("Generation", "Kraftstoff", "Radantrieb", "Hybrid"))
      and "Getriebe" in queries_h[0][1] and "transmission" in queries_h[1][1], str(queries_h))

print("--- Spec-I) mehrere offene Specs -> EIN gebündeltes DE/EN-Query-Paar, "
      "NICHT ein Call pro Feld ---")
alles_offen = _identitaet(generation=None, kraftstoff=None, antrieb=None, getriebe=None, powertrain=None)
gaps_i = _identity_spec_gaps(alles_offen)
check("I) alle 5 web-belegbaren Felder als offen erkannt", set(gaps_i) == {
      "generation", "fuel", "drivetrain", "transmission", "powertrain"}, str(gaps_i))
queries_i = _identity_spec_gap_queries("Testmarke Modellz", "2018", gaps_i)
check("I) weiterhin genau 2 Queries -- GEBUENDELT, nicht 5 Felder x 2 = 10 Calls",
      len(queries_i) == 2, str(queries_i))
check("I) DE-Query nennt alle 5 offenen Felder in EINEM String",
      all(w in queries_i[0][1] for w in ("Generation", "Kraftstoff", "Radantrieb", "Getriebe", "Hybrid")),
      str(queries_i))

print("--- kein Gap -> keine unnoetige Zusatzsuche (Identity bereits stark "
      "bestaetigt) ---")
kein_gap = _identitaet()
check("kein Gap -> leere Query-Liste, 0 Zusatzcalls", _identity_spec_gap_queries(
      "Testmarke Modellz", "2018", _identity_spec_gaps(kein_gap)) == [])

check("engine_name/horsepower/displacement sind NICHT Teil der web-"
      "eigenstaendig auffindbaren Gap-Felder (eigener Confirm-only-Pfad, s. unten)",
      not any(f in ("engine_name", "horsepower", "displacement") for f in gaps_i))

print("\n=== Confirm-only-Spec-Gap-Recherche (§2 Folgeauftrag) ===")
from app.technical_research import _identity_confirm_gaps  # noqa: E402

ZIEL_MIT_MOTOR = dict(marke="Testmarke", modell="Modellz", motor="5.0 V8",
                      hubraum=5.0, leistung_ps=477)
ZIEL_OHNE_ANGABEN = dict(marke="Testmarke", modell="Modellz", motor=None,
                         hubraum=None, leistung_ps=None)

print("--- A) User gibt engine_name an, nicht bestaetigt -> Confirm-Gap ---")
ident_unconfirmed = _identitaet(motor=None)
gaps_a = _identity_confirm_gaps(ZIEL_MIT_MOTOR, ident_unconfirmed)
check("A) 'engine_name' als Confirm-Gap erkannt", "engine_name" in gaps_a, str(gaps_a))

print("--- B) kein engine_name angegeben -> KEINE Web-Erfindung, kein Gap ---")
gaps_b = _identity_confirm_gaps(ZIEL_OHNE_ANGABEN, ident_unconfirmed)
check("B) kein Confirm-Gap ohne Nutzerwert (leere Liste)", gaps_b == [], str(gaps_b))

print("--- C) User-Leistung vorhanden, unbestaetigt -> Confirm-Gap moeglich ---")
gaps_c = _identity_confirm_gaps(ZIEL_MIT_MOTOR, ident_unconfirmed)
check("C) 'horsepower' UND 'displacement' ebenfalls als Confirm-Gap erkannt",
      "horsepower" in gaps_c and "displacement" in gaps_c, str(gaps_c))

print("--- D) bereits webbestaetigter Wert -> kein Zusatzcall fuer DIESES Feld ---")
ident_confirmed = _identitaet(motor="5.0 V8")
ident_confirmed.feldwerte["displacement"] = {"value": 5.0, "confidence": "mittel", "domains": 2}
ident_confirmed.leistung_ps = 477
gaps_d = _identity_confirm_gaps(ZIEL_MIT_MOTOR, ident_confirmed)
check("D) alle 3 Felder bereits bestaetigt -> keine Confirm-Gaps mehr", gaps_d == [], str(gaps_d))

print("--- E) mehrere offene Confirm-only-Specs -> GEBUENDELTE Query, nicht "
      "1 Call pro Feld ---")
queries_e = _identity_spec_gap_queries("Testmarke Modellz", "2018", [],
                                       ziel=ZIEL_MIT_MOTOR, confirm_felder=gaps_c)
check("E) weiterhin genau 2 Queries fuer 3 offene Confirm-Felder", len(queries_e) == 2,
      str(queries_e))
check("E) DE-Query enthaelt den konkreten Nutzer-Claim (5.0 V8, 477 PS)",
      "5.0 V8" in queries_e[0][1] and "477 PS" in queries_e[0][1], str(queries_e))
check("E) EN-Query nutzt die Auftragsvorgabe 'engine specifications'",
      "engine specifications" in queries_e[1][1], str(queries_e))

print("--- kein Nutzerwert fuer irgendein Confirm-Feld -> keine Zusatzquery, "
      "keine erfundene Angabe ---")
queries_keine_erfindung = _identity_spec_gap_queries("Testmarke Modellz", "2018", [],
                                                      ziel=ZIEL_OHNE_ANGABEN,
                                                      confirm_felder=_identity_confirm_gaps(
                                                          ZIEL_OHNE_ANGABEN, ident_unconfirmed))
check("ohne jeden Nutzerwert bleibt die Query-Liste leer", queries_keine_erfindung == [])

print("--- discover- UND confirm-Gaps gemeinsam offen -> IMMER NOCH genau 2 "
      "Queries (volle Buendelung) ---")
queries_kombiniert = _identity_spec_gap_queries(
    "Testmarke Modellz", "2018", ["transmission"], ziel=ZIEL_MIT_MOTOR, confirm_felder=["horsepower"])
check("kombiniert: genau 2 Queries", len(queries_kombiniert) == 2, str(queries_kombiniert))
check("kombiniert: DE-Query enthaelt sowohl 'Getriebe' (discover) als auch "
      "'477 PS' (confirm)", "Getriebe" in queries_kombiniert[0][1]
      and "477 PS" in queries_kombiniert[0][1], str(queries_kombiniert))


# ── Teil 2: Orchestrierung (Suchrunde 2 nur bei Gap, generisch, kein Netzwerk) ──

IDENTITAET_TREFFER = [
    treffer("https://www.adac.de/testmarke-modellz", "Testmarke Modellz (seit 2015)",
            "Die Testmarke Modellz (seit 2015): 2.0 Turbo mit 200 PS, Benzin, Frontantrieb."),
    treffer("https://www.auto-motor-und-sport.de/testmarke-modellz", "Testmarke Modellz im Test",
            "Testmarke Modellz (seit 2015) mit 2.0 Turbo und 200 PS, Frontantrieb."),
]
# Hauptpfad-Fakt (Score >= MIN_SCORE_FAKT) -- loest in Runde 1 SOFORT Stop aus,
# KEINE Eskalation (Abschnitt 14, Fall A: "erste Suchrunde findet High-Trust").
ADAC_WASSERPUMPE_STARK = treffer(
    "https://www.adac.de/testmarke-modellz-wasserpumpe", "Testmarke Modellz Wartung",
    "Die Wasserpumpe wird bei der Testmarke Modellz alle 90.000 km ersetzt.")


class FakeTavily:
    def __init__(self, routing: dict[str, list[dict]]):
        self.routing = routing
        self.calls: list[str] = []

    async def __call__(self, query, count=5, include_domains=None, exclude_domains=None,
                       include_raw_content=False, bypass_cache=False, search_depth="basic"):
        self.calls.append(query)
        if "technische Daten" in query or "Generation Baureihe Bauzeitraum" in query:
            return list(IDENTITAET_TREFFER)
        if "Rückruf" in query:
            return []
        for substr, treffer_liste in self.routing.items():
            if substr in query:
                return list(treffer_liste)
        return []


def _patch(fake: FakeTavily):
    web_search.tavily_search = fake
    technical_research.tavily_search = fake


async def _recherchiere(routing):
    fake = FakeTavily(routing)
    _patch(fake)
    provider = TavilyTechnicalResearchProvider()
    recherche = await provider.recherchiere(marke="Testmarke", modell="Modellz", baujahr=2018,
                                            motor="2.0 Turbo 200 PS", ausgeloest_durch="test")
    return recherche, fake.calls


def _esk_calls(calls, *achsen):
    return [c for c in calls if any(a in c for a in achsen)]


async def test_h_keine_eskalation_bei_sofortigem_high_trust():
    recherche, calls = await _recherchiere({
        "Wartungsintervall Serviceintervall": [ADAC_WASSERPUMPE_STARK],
    })
    check("H) kein Evidence-Gap -> exakt 10 Calls (2 Identitaet + 2 Identity-Spec-Gap "
          "+ 2 Rueckruf + 4 Technik), KEINE Weakness/Wartung-Eskalationsrunde",
          len(calls) == 10, str(calls))
    check("H) Hauptpfad-Fakt kam durch", any(f.bauteil == "Wasserpumpe" for f in recherche.fakten))


async def test_i_eskalation_nur_fuer_schwachstelle():
    recherche, calls = await _recherchiere({
        "bekannte Probleme Schwachstellen": [SW_FAMILIE_1, SW_FAMILIE_2],
    })
    esk_calls = _esk_calls(calls, "Werkstattinformation", "technical service bulletin")
    check("I) genau 2 zusaetzliche Eskalations-Calls fuer Schwachstelle",
          len(esk_calls) == 2, str(calls))
    check("I) insgesamt 12 Calls (10 Basis inkl. Identity-Spec-Gap + 2 Eskalation), "
          "Wartung eskaliert NICHT mit", len(calls) == 12, str(calls))
    check("I) keine Wartungs-Eskalations-Achsen aufgerufen",
          not _esk_calls(calls, "Betriebsanleitung Serviceheft Wartungsplan",
                        "owner's manual service schedule"))
    check("D) Runde 2 lieferte KEINE neue Evidence -> STOP, keine Runde-3-Claim-"
          "Bestaetigungs-Calls ('bestaetigt Herstellerangabe'/'confirmed specification')",
          not _esk_calls(calls, "bestätigt Herstellerangabe", "confirmed specification"), str(calls))


async def test_g_eskalation_nur_fuer_wartung():
    recherche, calls = await _recherchiere({
        "Wartungsintervall Serviceintervall": [WA_FAMILIE_1, WA_FAMILIE_2],
    })
    esk_calls = _esk_calls(calls, "Betriebsanleitung Serviceheft Wartungsplan",
                           "owner's manual service schedule")
    check("G) genau 2 zusaetzliche Eskalations-Calls fuer Wartung", len(esk_calls) == 2, str(calls))
    check("G) insgesamt 12 Calls, Schwachstelle eskaliert NICHT mit", len(calls) == 12, str(calls))
    check("G) keine Schwachstelle-Eskalations-Achsen aufgerufen",
          not _esk_calls(calls, "Werkstattinformation", "technical service bulletin"))


async def test_j_eskalation_loest_den_gap_in_runde_2():
    # Runde 1 liefert 2 Families (Testmarke Modellz) -- eskalierbar, aber
    # (noch) kein Fakt. Die Eskalations-Query ("...technical service
    # bulletin...") liefert eine DRITTE, unabhaengige Family -> MULTI_SOURCE
    # wird in Runde 2 sichtbar.
    recherche, calls = await _recherchiere({
        "bekannte Probleme Schwachstellen": [SW_FAMILIE_1, SW_FAMILIE_2],
        "technical service bulletin": [SW_FAMILIE_3],
    })
    sw_fakten = [f for f in recherche.fakten if f.kategorie == "schwachstelle"]
    check("J) Runde 2 macht aus dem Gap einen sichtbaren, schwaecheren Fakt",
          len(sw_fakten) == 1, str(sw_fakten))
    check("J) beleglage korrekt gesetzt", bool(sw_fakten) and sw_fakten[0].beleglage == "mehrfach_unabhaengig")
    check("J) genau 12 Calls (kein dritter Suchlauf -- GENAU EINE Eskalationsrunde)",
          len(calls) == 12, str(calls))


async def test_k_wartung_und_schwachstelle_eskalieren_unabhaengig():
    recherche, calls = await _recherchiere({
        "bekannte Probleme Schwachstellen": [SW_FAMILIE_1, SW_FAMILIE_2],
        "Wartungsintervall Serviceintervall": [WA_FAMILIE_1, WA_FAMILIE_2],
    })
    check("K) beide Kategorien eskalieren -> 10 Basis + 2 + 2 = 14 Calls", len(calls) == 14, str(calls))


# Zweite Seite DERSELBEN Family (kfzwissen-a.de) -- erhöht `n_kandidaten`
# (neue URL), aber NICHT `families` (bleibt bei 2, unter der Schwelle) --
# echter, aber unzureichender Fortschritt (Test C).
SW_FAMILIE_1_ZWEITE_SEITE = treffer(
    "https://www.kfzwissen-a.de/testmarke-modellz-wasserpumpe-update",
    "Testmarke Modellz: Wasserpumpe Update",
    "Die Wasserpumpe der Testmarke Modellz wird häufig undicht.")


async def test_c_und_e_runde3_bei_fortschritt_dann_endgueltiger_stop():
    # Runde 1: 2 Families (eskalierbar). Runde 2 (Source-Upgrade) bringt eine
    # ZWEITE Seite der BEREITS bekannten Family kfzwissen-a.de -- echter
    # Fortschritt (neue URL/neuer Candidate), aber die Family-Zahl bleibt bei
    # 2 (unter der MULTI_SOURCE-Schwelle) -> Runde 3 wird ausgelöst (Test C).
    # Runde 3 (Claim-Bestätigung) bringt NICHTS Neues -> endgültiger Stop,
    # KEINE vierte Runde (Test E).
    recherche, calls = await _recherchiere({
        "bekannte Probleme Schwachstellen": [SW_FAMILIE_1, SW_FAMILIE_2],
        "Werkstattinformation": [SW_FAMILIE_1_ZWEITE_SEITE],
    })
    runde3_calls = _esk_calls(calls, "bestätigt Herstellerangabe", "confirmed specification")
    check("C) Runde 2 brachte Fortschritt (neue URL, Family-Zahl unveraendert) -> "
          "Runde 3 WURDE ausgeloest", len(runde3_calls) == 2, str(calls))
    check("E) trotz Runde 3 bleibt der Fakt offen (weiterhin nur 2 Families) -> "
          "kein sichtbarer Fakt, aber auch KEINE vierte Runde",
          not any(f.kategorie == "schwachstelle" for f in recherche.fakten))
    check("E) exakt 14 Calls (10 Basis + 2 Runde-2 + 2 Runde-3), keine fuenfte/"
          "sechste Eskalations-Query", len(calls) == 14, str(calls))


class FakeTavilyMitCeiling(FakeTavily):
    """Simuliert ein erschoepftes Provider-Call-Budget (`claim_call()`/
    `ProviderCallLimitExceeded`, app/provider_control.py) ab dem
    `ceiling`-ten Call -- derselbe Fehlerpfad, den `suche()` in
    `recherchiere()` bereits per generischem `except Exception` abfaengt.
    Test F prueft, dass das NIE nach aussen durchschlaegt."""

    def __init__(self, routing, *, ceiling: int):
        super().__init__(routing)
        self.ceiling = ceiling

    async def __call__(self, query, **kwargs):
        if len(self.calls) >= self.ceiling:
            self.calls.append(query)
            raise RuntimeError("Provider-Aufrufbudget ausgeschöpft (simuliert)")
        return await super().__call__(query, **kwargs)


async def test_f_hard_ceiling_erzeugt_sauberen_stop():
    # Ceiling bei 11 Calls: die Basis-Runde (10 Calls: 2 Identitaet + 2
    # Identity-Spec-Gap + 2 Rueckruf + 4 Technik) läuft noch vollständig
    # durch, der ERSTE Weakness-Eskalations-Call (Call Nr. 11) schlägt fehl --
    # KEINE Exception darf nach außen dringen, `recherchiere()` muss ein
    # normales Ergebnis liefern (Stop-Bedingung F, §A Auftrag).
    fake = FakeTavilyMitCeiling(
        {"bekannte Probleme Schwachstellen": [SW_FAMILIE_1, SW_FAMILIE_2]}, ceiling=11)
    _patch(fake)
    provider = TavilyTechnicalResearchProvider()
    try:
        recherche = await provider.recherchiere(marke="Testmarke", modell="Modellz", baujahr=2018,
                                                 motor="2.0 Turbo 200 PS", ausgeloest_durch="test")
        keine_exception = True
    except Exception as exc:                                           # pragma: no cover
        keine_exception = False
        recherche = None
        print(f"      (unerwartete Exception: {exc!r})")
    check("F) Hard-Ceiling-Fehler schlägt NICHT nach außen durch (saubere Rückgabe)",
          keine_exception)
    check("F) Identität trotz Ceiling-Fehler in Runde 2 weiterhin belegt",
          keine_exception and recherche is not None and recherche.identitaet.belegt)
    check("F) kein sichtbarer Fakt aus dem fehlgeschlagenen Eskalationsversuch erzwungen",
          keine_exception and recherche is not None
          and not any(f.kategorie == "schwachstelle" for f in recherche.fakten))


async def _run_all():
    await test_h_keine_eskalation_bei_sofortigem_high_trust()
    await test_i_eskalation_nur_fuer_schwachstelle()
    await test_g_eskalation_nur_fuer_wartung()
    await test_j_eskalation_loest_den_gap_in_runde_2()
    await test_k_wartung_und_schwachstelle_eskalieren_unabhaengig()
    await test_c_und_e_runde3_bei_fortschritt_dann_endgueltiger_stop()
    await test_f_hard_ceiling_erzeugt_sauberen_stop()


asyncio.run(_run_all())

print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE ADAPTIVE-EVIDENCE-ESCALATION-TESTS GRUEN")
