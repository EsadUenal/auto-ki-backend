"""
Root-Cause-Audit RC-2/RC-3 — amtliche Eingrenzung fließt in rueckruf_scope() ein.

Synthetische Marke/Modell (keine realen Fahrzeuge als Testgegenstand), außer
den klar als REGRESSIONS-FIXTUREN gekennzeichneten echten KBA-Referenzen
(9831/10206, nur als Textvorlage für die Eingrenzung — kein Laufzeitbezug auf
diese Referenznummern im Produktcode).

  A) PS-/Hubraum-Scope aus der amtlichen Eingrenzung (nicht nur aus mangel/abhilfe)
  B) Antriebsart-Dimension (Mild-Hybrid) — die reproduzierte Audi-Lücke
  C) Mild-Hybrid wird NICHT mit der Hochvolt-/PHEV-Heuristik verwechselt
  D) Ausstattungsbedingung aus der Eingrenzung
  E) Bestandszeilen ohne eingrenzung_amtlich (NULL) ändern sich nicht

    python test_recall_scope_official.py
"""
import app.recall_filter as _rf

_rf.get_rueckruf_referenzen_kurz = lambda: []

from app.models import KaufCheckRequest
from app.recall_filter import (
    RECALL_NOT_APPLICABLE, RECALL_SERIES_RELEVANT, RECALL_UNKNOWN,
    RECALL_VARIANT_POSSIBLE, rueckruf_applicability, rueckruf_scope,
)
from app.vehicle_identity import VehicleIdentity

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def motor(vid, bez, code, kraftstoff, ps, getriebe='["6-Gang Manuell"]', antrieb="Front"):
    return {"variante_id": vid, "bezeichnung": bez, "motorcode": code, "kraftstoff": kraftstoff,
            "leistung_ps": ps, "getriebe": getriebe, "antrieb": antrieb,
            "schwachstellen_motor": [], "kritische_wartung": []}


def baureihe(motoren):
    return {"id": "testmarke-beta-t1", "marke": "Testmarke", "modell": "Beta",
            "generation": "T1", "bauzeitraum_von": 2015, "bauzeitraum_bis": 2023,
            "motoren": motoren, "schwachstellen_baureihe": [], "rueckrufe": []}


def req(**kw):
    basis = dict(marke="Testmarke", modell="Beta", baujahr=2018)
    basis.update(kw)
    return KaufCheckRequest(**basis)


def ident(b, m, r):
    return VehicleIdentity.from_check_context(b, m, r)


BASIS_R = {"betroffene_baujahre": "2017-2020", "kba_referenz": None,
          "mangel": "Durch Feuchtigkeitseintritt in den Riemenstartergenerator kann es "
                    "zu Kurzschluss kommen."}


# ══ A) PS-/Hubraum-Scope aus der amtlichen Eingrenzung ═══════════════════════
print("\n--- A) PS-/Hubraum-Scope wird jetzt auch aus eingrenzung_amtlich gelesen ---")
m_125 = motor("g-1", "1.4 T (125 PS)", "QX14A", "Benzin", 125)
i_125 = ident(baureihe([m_125]), m_125, req(kraftstoff="Benzin", leistung_ps=125, motor="1.4 QX14A"))
_r_nur_eingrenzung = {**BASIS_R, "mangel": "Die Ölleitung kann undicht werden.",
                     "eingrenzung_amtlich": "Bei Fahrzeugen mit 150 PS"}
check("A1 ein Scope, der NUR in eingrenzung_amtlich steht (nicht im Mangeltext), "
      "wird erkannt und schließt die falsche Leistung aus",
      rueckruf_scope(_r_nur_eingrenzung, i_125)[0] == RECALL_NOT_APPLICABLE)
m_150 = motor("g-2", "1.4 T (150 PS)", "QX14B", "Benzin", 150)
i_150 = ident(baureihe([m_150]), m_150, req(kraftstoff="Benzin", leistung_ps=150, motor="1.4 QX14B"))
check("A2 passende Leistung aus eingrenzung_amtlich -> VARIANT_POSSIBLE",
      rueckruf_scope(_r_nur_eingrenzung, i_150)[0] == RECALL_VARIANT_POSSIBLE)


# ══ B) Antriebsart-Dimension (Mild-Hybrid) — reproduzierter Audi-Fall ═══════
print("\n--- B) Mild-Hybrid-Bedingung aus der amtlichen Eingrenzung (Audi 9831/10206-Form) ---")
# Textvorlage aus dem echten KBA-Export (nur als Regressions-Fixture, kein
# Laufzeitbezug auf die Referenznummer): "Es sind ausschließlich Fahrzeuge mit
# 2.0 TFSI und Mild-Hybrid-System betroffen."
_r_mhev = {**BASIS_R,
          "eingrenzung_amtlich": "Es sind ausschließlich Fahrzeuge mit 2.0 TFSI "
                                 "und Mild-Hybrid-System betroffen"}

m_tfsi_mhev = motor("a-1", "2.0 TFSI (Mild-Hybrid)", "DEMA", "Mild-Hybrid", 190)
i_mhev = ident(baureihe([m_tfsi_mhev]), m_tfsi_mhev,
              req(kraftstoff="Benzin", leistung_ps=190, motor="2.0 DEMA"))
check("B1 Fahrzeug MIT Mild-Hybrid (DB-Rohwert 'Mild-Hybrid') -> VARIANT_POSSIBLE, "
      "NICHT stillschweigend als unconditional series_only behandelt",
      rueckruf_scope(_r_mhev, i_mhev)[0] in (RECALL_VARIANT_POSSIBLE, RECALL_UNKNOWN))

m_tfsi_ice = motor("a-2", "2.0 TFSI (ohne Mild-Hybrid)", "CVKB", "Benzin", 190)
i_ice = ident(baureihe([m_tfsi_ice]), m_tfsi_ice,
             req(kraftstoff="Benzin", leistung_ps=190, motor="2.0 CVKB"))
check("B2 Fahrzeug OHNE Mild-Hybrid (reiner ICE-Rohwert 'Benzin') -> NOT_APPLICABLE "
      "(bekannter Widerspruch, nicht stillschweigend eingeschlossen)",
      rueckruf_scope(_r_mhev, i_ice)[0] == RECALL_NOT_APPLICABLE)

i_unbekannt = ident(baureihe([]), None, req())
check("B3 Powertrain unbekannt (kein erkannter Motor) -> UNKNOWN, weder MATCH "
      "noch NO_MATCH",
      rueckruf_scope(_r_mhev, i_unbekannt)[0] == RECALL_UNKNOWN)


# ══ C) Keine Verwechslung mit der Hochvolt-/PHEV-Heuristik ══════════════════
print("\n--- C) Mild-Hybrid wird NICHT wie ein Hochvolt-/PHEV-Rueckruf behandelt ---")
# Der volle rueckruf_applicability()-Pfad: _HV_MUSTER darf aus eingrenzung_amtlich
# NICHT "hybrid" herauslesen und den Rueckruf faelschlich als Hochvolt-Rueckruf
# gegen einen Mild-Hybrid als 'incompatible' werten.
appl, *_ = rueckruf_applicability(_r_mhev, True, "", m_tfsi_mhev, marke="Testmarke",
                                  identity=i_mhev)
check("C1 Mild-Hybrid-Fahrzeug wird NICHT als 'incompatible' zu einem "
      "vermeintlichen Hochvolt-Rueckruf behandelt",
      appl != "incompatible")


# ══ D) Ausstattungsbedingung aus der Eingrenzung ═════════════════════════════
print("\n--- D) Ausstattungsbedingung aus eingrenzung_amtlich (nicht nur mangel) ---")
_r_ausstattung = {**BASIS_R, "mangel": "Fehlfunktion kann auftreten.",
                  "eingrenzung_amtlich": "Ausstattung mit Anhängerkupplung"}


class _Req:
    def __init__(self, ausstattung):
        self.ausstattung = ausstattung
        self.beschreibung = ""
        self.freitext = ""


i_mit_kupplung = ident(baureihe([m_125]), m_125,
                       req(kraftstoff="Benzin", leistung_ps=125, motor="1.4 QX14A"))
_r_mit_ausstattung_kontext = {**_r_ausstattung, "_ausstattung": ["Anhängerkupplung"],
                             "_freitext": ""}
check("D1 bestätigt vorhandene, nur in eingrenzung_amtlich genannte Ausstattung "
      "hebt die Stufe (VARIANT_POSSIBLE)",
      rueckruf_scope(_r_mit_ausstattung_kontext, i_mit_kupplung)[0] == RECALL_VARIANT_POSSIBLE)
_r_ohne_ausstattung_kontext = {**_r_ausstattung, "_ausstattung": [], "_freitext": ""}
check("D2 unbekannte Ausstattung (nicht angegeben) -> UNKNOWN, nicht stillschweigend "
      "ausgeschlossen",
      rueckruf_scope(_r_ohne_ausstattung_kontext, i_mit_kupplung)[0] == RECALL_UNKNOWN)


# ══ E) Rückwärtskompatibilität — NULL/fehlendes Feld ändert nichts ══════════
print("\n--- E) Bestandszeilen ohne eingrenzung_amtlich verhalten sich unveraendert ---")
_r_alt = {**BASIS_R, "mangel": "Die Ölleitung kann undicht werden."}  # kein eingrenzung_amtlich-Key
check("E1 fehlendes eingrenzung_amtlich -> SERIES_RELEVANT wie zuvor (kein Scope "
      "erkennbar)",
      rueckruf_scope(_r_alt, i_125)[0] == RECALL_SERIES_RELEVANT)
_r_alt_none = {**_r_alt, "eingrenzung_amtlich": None}
check("E2 explizit None verhält sich identisch zu fehlendem Schlüssel",
      rueckruf_scope(_r_alt_none, i_125) == rueckruf_scope(_r_alt, i_125))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE RECALL-SCOPE-OFFICIAL-TESTS GRUEN")
