"""
"Nicht überwacht" ist eine Aufsichts-/Verfahrensangabe des KBA, keine Aussage
über Sicherheitsrelevanz — Opel-Astra-K-Root-Cause-Fund (KBA 8322,
"Hauptbremszylinder").

KEIN Netzwerk, KEIN LLM-Call, KEINE Tavily-Calls, KEINE DB-Mutation.

  A) Reproduktionsfall: KBA 8322 verschwindet nicht mehr
  B) expliziter Widerspruch schliesst weiterhin aus
  C) UNKNOWN schliesst NICHT aus
  D) bestehende Audi-/Mercedes-Faelle bleiben unveraendert
  E) keine Dubletten
  F) Verkaeuferfragen/Dokumente uebernehmen einen neu aufgenommenen Recall

    python test_kba_not_monitored_safety.py
"""
from app.kba_import_kandidaten import SAFE_IMPORT, import_kandidaten
from app.evidence import build_insights
from app.kaufaktionen import build_kaufaktionen
from app.models import KaufCheckRequest
from app.vehicle_identity import VehicleIdentity

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def kba_zeile(**kw) -> dict:
    z = {
        "KBA-Referenznummer": "8322", "Rückrufcode des Herstellers": "E181803130 (18-C-120)",
        "Veröffentlichungsdatum": "2018-12-11", "Marke": "OPEL", "Modell": "ASTRA",
        "Mangelbezeichnung": "Fehlerhafte Hauptbremszylinder können zu "
                             "Funktionseinschränkungen der Bremse führen.",
        "Produktionszeitraum von": "2015", "Produktionszeitraum bis": "2018",
        "Beschreibung der Maßnahme": "",
        "Mögliche Eingrenzung der betroffenen Modelle": "N/A",
        "Überwachung der Rückrufaktion durch das KBA": "nicht überwacht",
    }
    z.update(kw)
    return z


def br(**kw) -> dict:
    b = {"id": "opel-astra-k", "marke": "Opel", "modell": "Astra",
         "generation": "K", "bauzeitraum_von": 2015, "bauzeitraum_bis": 2021}
    b.update(kw)
    return b


# ══ A) Reproduktionsfall: KBA 8322 verschwindet nicht mehr ══════════════════
print("\n--- A) KBA 8322 (Opel Astra K, Hauptbremszylinder) ---")
_k = import_kandidaten([kba_zeile()], [], [br()])
check("A1 KBA 8322 ist ein Kandidat (nicht laenger ein stiller continue)",
      len(_k) == 1)
check("A2 KBA 8322 wird SAFE_IMPORT (eindeutige Baureihe, keine Dublette, "
      "keine Variantenbeschraenkung)",
      bool(_k) and _k[0].klasse == SAFE_IMPORT)
check("A3 KBA 8322 bleibt auf series_only (series-level) -- nie "
      "'confirmed_by_vin', das Fahrzeug wird nicht als sicher betroffen "
      "dargestellt",
      bool(_k) and _k[0].applicability == "series_only")

# ══ B) expliziter Widerspruch schliesst weiterhin aus ═══════════════════════
print("\n--- B) expliziter Widerspruch (Marke nicht VIRA-gefuehrt) schliesst weiterhin aus ---")
_k_fremd = import_kandidaten([kba_zeile(Marke="FERRARI")], [], [br()])
check("B1 eine Marke, die VIRA nicht fuehrt, bleibt ausgeschlossen (Marken-Gate "
      "unveraendert, unabhaengig von Sicherheitsrelevanz)",
      _k_fremd == [])

# ══ C) UNKNOWN (keine Eingrenzung) schliesst NICHT aus ══════════════════════
print("\n--- C) fehlende/nicht-aufloesbare Eingrenzung schliesst NICHT aus ---")
check("C1 amtliche Eingrenzung 'N/A' (trivial) -> kein Ausschluss, SAFE_IMPORT "
      "bleibt bestehen (= A2, keine erfundene Einschraenkung)",
      bool(_k) and _k[0].klasse == SAFE_IMPORT and not _k[0].eingrenzung)

# ══ D) bestehende Audi-/Mercedes-Faelle bleiben unveraendert ════════════════
print("\n--- D) Audi/Mercedes-Faelle (ueberwacht) bleiben unveraendert ---")
_audi_a4 = br(id="audi-a4-b9", marke="Audi", modell="A4", generation="B9",
             bauzeitraum_von=2015, bauzeitraum_bis=2023)
_kba_9831 = kba_zeile(
    **{"KBA-Referenznummer": "9831", "Marke": "AUDI", "Modell": "A4",
       "Mangelbezeichnung": "Durch Feuchtigkeitseintritt in den Riemenstartergenerator "
                            "können Kurzschlussbrücken entstehen. Fahrzeugbrand möglich.",
       "Produktionszeitraum von": "2017", "Produktionszeitraum bis": "2020",
       "Mögliche Eingrenzung der betroffenen Modelle":
           "Es sind ausschließlich Fahrzeuge mit 2.0 TFSI und Mild-Hybrid-System betroffen",
       "Überwachung der Rückrufaktion durch das KBA": "überwacht"})
_k_audi = import_kandidaten([_kba_9831], [], [_audi_a4])
check("D1 KBA 9831 (ueberwacht=True) bleibt unveraendert SAFE_IMPORT -- der "
      "nicht_ueberwacht-Fix aendert nichts an bereits ueberwachten Faellen",
      bool(_k_audi) and _k_audi[0].klasse == SAFE_IMPORT)

_mercedes_c = br(id="mercedes-benz-c-klasse-w205", marke="Mercedes-Benz",
                 modell="C-Klasse", generation="W205",
                 bauzeitraum_von=2014, bauzeitraum_bis=2021)
_kba_mb = kba_zeile(
    **{"KBA-Referenznummer": "11352", "Marke": "MERCEDES-BENZ", "Modell": "C-KLASSE",
       "Mangelbezeichnung": "Der Airbag kann im Fehlerfall nicht auslösen.",
       "Produktionszeitraum von": "2017", "Produktionszeitraum bis": "2021",
       "Mögliche Eingrenzung der betroffenen Modelle": "N/A",
       "Überwachung der Rückrufaktion durch das KBA": "überwacht"})
_k_mb = import_kandidaten([_kba_mb], [], [_mercedes_c])
check("D2 ein ueberwachter Mercedes-Fall bleibt unveraendert klassifizierbar "
      "(hier: SAFE_IMPORT, keine Regression durch den Opel-Fix)",
      bool(_k_mb) and _k_mb[0].klasse == SAFE_IMPORT)

# ══ E) keine Dubletten ═══════════════════════════════════════════════════
print("\n--- E) bereits vorhandene Zeile wird nicht erneut als Kandidat gefunden ---")
_vorhanden = [{"id": 1, "baureihe_id": "opel-astra-k", "kba_referenz": "8322",
              "datum": "2018-12", "betroffene_baujahre": "2015-2018",
              "mangel": kba_zeile()["Mangelbezeichnung"], "abhilfe": None}]
_k_dup = import_kandidaten([kba_zeile()], _vorhanden, [br()])
check("E1 KBA 8322, bereits kanonisch fuer opel-astra-k vorhanden, erzeugt "
      "KEINEN erneuten Kandidaten (kein Duplikat)", _k_dup == [])

# ══ F) Verkaeuferfragen/Dokumente uebernehmen den Recall ════════════════════
print("\n--- F) Checklisten-Propagation (Verkaeuferfragen/Dokumente) ---")
_baureihe_live = {
    "id": "opel-astra-k", "marke": "Opel", "modell": "Astra", "generation": "K",
    "bauzeitraum_von": 2015, "bauzeitraum_bis": 2021,
    "rueckrufe": [{
        "id": 9001, "baureihe_id": "opel-astra-k", "kba_referenz": "8322",
        "datum": "2018-12-11", "betroffene_baujahre": "2015-2018",
        "mangel": "Fehlerhafte Hauptbremszylinder können zu Funktionseinschränkungen "
                 "der Bremse führen.",
        "abhilfe": None, "eingrenzung_amtlich": None,
        "prod_von_amtlich": 2015, "prod_bis_amtlich": 2018,
    }],
}
_req = KaufCheckRequest(marke="Opel", modell="Astra", baujahr=2018,
                        kilometerstand=80000, motor="1.4 Turbo", leistung_ps=125,
                        antrieb="Frontantrieb", kraftstoff="Benzin",
                        getriebe="Schaltgetriebe", preis_eur=12000)
_identity = VehicleIdentity.from_check_context(_baureihe_live, None, _req)
_insights = build_insights(_baureihe_live, None, [], _req, check_typ="kauf",
                           identity=_identity)
_rueckruf_insights = [i for i in _insights if i.kategorie == "rueckruf"]
check("F1 der Hauptbremszylinder-Recall erscheint als Insight",
      any("remszylinder" in i.beschreibung for i in _rueckruf_insights))
check("F2 applicability bleibt series_only/unclear, NIE confirmed_by_vin",
      all(i.applicability != "confirmed_by_vin" for i in _rueckruf_insights))

_aktionen = build_kaufaktionen(_req, _baureihe_live, None, _insights)
_fragen_titel = [a.titel for a in _aktionen.verkaeuferfragen.fahrzeugspezifisch]
_dok_titel = [a.titel for a in _aktionen.dokumente.fahrzeugspezifisch]
check("F3 die Verkaeuferfrage zum Rueckruf wird erzeugt",
      any("ückruf" in t for t in _fragen_titel))
check("F4 der Dokumenten-Checklistenpunkt zum Rueckruf wird erzeugt",
      any("ückruf" in t for t in _dok_titel))
check("F5 kein Duplikat innerhalb derselben Liste (genau EIN Rueckruf-Eintrag "
      "je Checkliste)",
      sum(1 for t in _fragen_titel if "ückruf" in t) == 1
      and sum(1 for t in _dok_titel if "ückruf" in t) == 1)


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE NICHT-UEBERWACHT-SICHERHEITSRELEVANT-TESTS GRUEN")
