"""
N14 -- HU-Widerspruch Formular vs. Inseratstext.

Referenz: Export 20261108-091413. Formular HU 09/2027, Inseratstext
"HU bis August 2027". Vorher: Text-Termin wurde nie gelesen, die Tabelle
zeigte "✓ passt" nur für das Formularfeld.

    python test_n14_hu_widerspruch.py
"""
import os
import socket
import tempfile

_tmp = tempfile.mkdtemp(prefix="n14_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = _gesperrt
socket.create_connection = _gesperrt

import datetime as dt  # noqa: E402

from app.bekannte_fakten import aus_request, basistexte, bereinige_bericht  # noqa: E402
from app.hu_termin import bewerte_hu, hu_aus_text, hu_konflikt  # noqa: E402
from app.kaufaktionen import build_kaufaktionen  # noqa: E402
from app.key_findings import _widerspruch_findings  # noqa: E402
from app.models import KaufCheckRequest  # noqa: E402
from app.vergleichstabelle import baue_zeilen, als_markdown  # noqa: E402

_FEHLER: list[str] = []
HEUTE = dt.date(2026, 11, 8)


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def req(tuev, text):
    return KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, tuev_bis=tuev,
                            beschreibung=text)


def hu_zeilen(r):
    hu = bewerte_hu(r.tuev_bis, heute=HEUTE, baujahr=r.baujahr)
    z = baue_zeilen(r, None, None, hu=hu, fakten=aus_request(r))
    return [x for x in z if x.kriterium == "HU"], als_markdown([x for x in z if x.kriterium == "HU"])


def aktionen(r):
    a = build_kaufaktionen(r, None, None, [])
    texte = {}
    for sek in ("verkaeuferfragen", "dokumente"):
        plan = getattr(a, sek)
        texte[sek] = [(x.titel, x.aktion) for x in plan.fahrzeugspezifisch + plan.basis
                      if "HU" in x.titel or "Hauptuntersuchung" in x.titel or "HU" in x.aktion]
    return texte


# ── 1) Originalfall ──────────────────────────────────────────────────────────
orig = req("09/2027", "Lexus RC F, Scheckheft gepflegt. HU bis August 2027. Unfallfrei.")
k = hu_konflikt(orig.tuev_bis, orig.beschreibung)
check("1a) Konflikt erkannt: 09/2027 vs 08/2027", k is not None and k.formular == "09/2027"
      and k.inserat == "08/2027", str(k))
zl, md = hu_zeilen(orig)
print("Inseratvergleich:", md.splitlines()[-1])
check("1b) Vergleichstabelle: genau eine HU-Zeile, beide Werte, KEIN '✓ passt'",
      len(zl) == 1 and "09/2027" in md and "08/2027" in md and "✓ passt" not in md
      and "Widerspruch" in md, md)
check("1c) Kein '✓ Plausibel' im Konfliktfall", "✓ Plausibel" not in md)
fs = [f for f in _widerspruch_findings(orig, None, None) if "HU" in f.titel]
print("Übersicht:", fs[0].beschreibung if fs else None)
check("1d) Übersicht: genau ein HU-Widerspruch mit beiden Terminen, nichts bestätigt",
      len(fs) == 1 and "09/2027" in fs[0].wert and "08/2027" in fs[0].wert
      and "keine ist bestätigt" in fs[0].beschreibung)
bt = basistexte(aus_request(orig))[("verkaeuferfragen", "hu")]
print("Frage:", bt[0], "|", bt[1])
check("1e) Verkäuferfrage nennt beide Termine und den HU-Bericht",
      "09/2027" in bt[0] and "08/2027" in bt[0] and "Prüfbericht" in bt[0])
a = aktionen(orig)
print("Nachweis:", a["dokumente"])
doks = [x for x in a["dokumente"] if x[0].startswith("HU-Bericht")]
check("1f) Dokumentennachweis: genau ein HU-Bericht-Eintrag, auf Klärung ausgerichtet",
      len(doks) == 1 and "09/2027" in doks[0][0] and "08/2027" in doks[0][0], str(doks))
check("1g) keine doppelte HU-Verkäuferfrage",
      len([x for x in a["verkaeuferfragen"] if "HU" in x[0] or "Hauptuntersuchung" in x[0]]) <= 1,
      str(a["verkaeuferfragen"]))
neu, _ = bereinige_bericht("Bis wann läuft die HU?", aus_request(orig))
check("1h) Netz für Modelltext ersetzt Frage ohne einen Termin zu bestätigen",
      "09/2027" in neu and "08/2027" in neu and "laut Inserat bis" not in neu, neu)

from app.empfehlung_gruende import baue_empfehlung_gruende  # noqa: E402
from app.hu_termin import konflikt_hinweis, konflikt_satz  # noqa: E402

SATZ = "Im Formular steht 09/2027, im Inserattext 08/2027. Welcher Termin gilt, bitte im HU-Bericht prüfen."
check("1i) Kernsatz mit Herkunft beider Termine", konflikt_hinweis(k) == SATZ, konflikt_hinweis(k))
alle = " ".join([fs[0].beschreibung, md, bt[0], bt[1], doks[0][0], doks[0][1], konflikt_satz(k)])
check("1j) nirgends 'Inseratstext'/'laut Inserat' für den Formulartermin",
      "Inseratstext" not in alle and "laut Inserat" not in alle, alle)
check("1k) Verkäuferfrage, Nachweis und Übersicht nennen Formular UND Inserattext",
      all("Formular" in t and "Inserattext" in t for t in (fs[0].beschreibung, bt[1], doks[0][1])))
hu_o = bewerte_hu(orig.tuev_bis, heute=HEUTE, baujahr=orig.baujahr)
gruende = baue_empfehlung_gruende(orig, None, None, [], [], "kaufen_nach_besichtigung",
                                  False, None, hu=hu_o)
hu_g = [x for x in gruende if "HU" in x]
print("Empfehlungsgrund:", hu_g)
check("1l) Empfehlungsgrund: genau eine HU-Aussage, Konfliktwortlaut, kein 'laut Inserat gültig'",
      hu_g == [SATZ], str(hu_g))
gl = baue_empfehlung_gruende(gleich_r := req("09/2027", "HU bis September 2027."), None, None, [], [],
                             "kaufen_nach_besichtigung", False, None,
                             hu=bewerte_hu("09/2027", heute=HEUTE, baujahr=2017))
check("1m) übereinstimmend: bisheriger Grund unverändert",
      any("HU laut Inserat gültig bis 09/2027" in x for x in gl), str(gl))

# ── 2) zwei übereinstimmende Angaben ─────────────────────────────────────────
gleich = req("09/2027", "HU bis September 2027.")
check("2a) kein Konflikt bei gleichem Termin", hu_konflikt(gleich.tuev_bis, gleich.beschreibung) is None)
zl, md = hu_zeilen(gleich)
check("2b) bisherige Anzeige unverändert ('✓ passt')", "✓ passt" in md and "09/2027" in md, md)
check("2c) keine Widerspruchs-Finding", not [f for f in _widerspruch_findings(gleich, None, None) if "HU" in f.titel])

# ── 3) nur ein Termin vorhanden ──────────────────────────────────────────────
nur_form = req("09/2027", "Gepflegtes Fahrzeug.")
nur_text = req(None, "HU bis August 2027.")
check("3a) nur Formular: kein Konflikt", hu_konflikt(nur_form.tuev_bis, nur_form.beschreibung) is None)
check("3b) nur Text: kein Konflikt (und keine Formularübernahme)",
      hu_konflikt(nur_text.tuev_bis, nur_text.beschreibung) is None and nur_text.tuev_bis is None)
check("3c) nur Formular: bisherige Tabelle", "✓ passt" in hu_zeilen(nur_form)[1])

# ── 4) andere Datumsangaben sind keine HU ────────────────────────────────────
andere = req("09/2027", "Erstzulassung 03/2017, Service im August 2025, Inserat vom 05.11.2026, "
                        "Winterreifen bis Oktober 2027, Garantie bis 12/2028.")
check("4a) andere Daten werden nicht als HU gelesen", hu_aus_text(andere.beschreibung) is None)
check("4b) kein Konflikt durch andere Daten",
      hu_konflikt(andere.tuev_bis, andere.beschreibung) is None)
check("4c) mehrdeutig (zwei verschiedene HU-Termine im Text) -> kein Konflikt",
      hu_konflikt("09/2027", "HU bis 08/2027, später TÜV bis 03/2028") is None)
check("4d) 'HU neu' ohne Datum -> kein Konflikt", hu_konflikt("09/2027", "HU neu, TÜV frisch") is None)
check("4e) Schreibweisen 'TÜV: 08/2027' / 'Hauptuntersuchung bis 08.2027'",
      hu_aus_text("TÜV: 08/2027")[0] == (8, 2027)
      and hu_aus_text("Hauptuntersuchung bis 08.2027")[0] == (8, 2027))

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
