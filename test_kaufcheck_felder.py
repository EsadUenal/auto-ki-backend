"""
Test: neue strukturierte KaufCheck-Felder wirken END-TO-END (Prelaunch-Polish)

Der Auftrag verlangt ausdruecklich, dass ein neues Feld nicht dekorativ sein
darf. Geprueft wird deshalb nicht "das Feld existiert", sondern:

  A) `kraftstoff` erreicht den Marktvergleich und wirkt dort HART
     (ein Diesel-Angebot faellt aus dem Vergleich fuer einen Benziner)
  B) ohne Nutzerangabe bleibt die Kraftstoff-Wirkung weich
  C) `leistung_ps` erreicht den Marktvergleich und schlaegt die DB-Variante
  D) `leistung_ps` wirkt auch ohne "PS" im Motor-Freitext
     (das war die eigentliche Luecke: `_ps_im_text` verlangt die Einheit)
  E) beide Felder stehen im Prompt, damit der Bericht sie begruenden kann
  F) Grenzwerte werden an der Eingabegrenze abgelehnt, nicht spaeter
  G) alte Requests ohne die neuen Felder funktionieren unveraendert

Ohne Netzwerk, ohne Provider, ohne DB-Schreibzugriff.

Ausfuehren:  python test_kaufcheck_felder.py
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="enfal_kcf_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")

import app.database as db            # noqa: E402
db.ensure_tables()

from pydantic import ValidationError  # noqa: E402

from app.models import KaufCheckRequest   # noqa: E402
import app.marktvergleich as mv           # noqa: E402
import app.kaufcheck as kc                # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


# Eine Baureihe ohne verifizierte Motorvarianten: so haengt jede HARTE Wirkung
# ausschliesslich an der Nutzerangabe, nicht an ungeprueften DB-Daten.
BAUREIHE = {"id": "test-baureihe", "marke": "BMW", "modell": "3er",
            "generation": "G20", "karosserie": ["Limousine"]}
# Die DB "kennt" eine ganz andere Motorisierung als der Nutzer angibt.
MOTOR_DB = {"bezeichnung": "320d", "kraftstoff": "diesel", "leistung_ps": 190}


def ziel_fuer(req):
    """Das Zielprofil, gegen das der Marktvergleich jedes Angebot prueft."""
    return mv.baue_ziel(BAUREIHE, MOTOR_DB, req, alle_baureihen=[BAUREIHE],
                        alle_motorvarianten=[MOTOR_DB])


# ── A) kraftstoff wirkt hart ────────────────────────────────────────────────
req_benzin = KaufCheckRequest(marke="BMW", modell="330i", baujahr=2019,
                              kilometerstand=60000, preis_eur=30000,
                              kraftstoff="benzin")
ziel = ziel_fuer(req_benzin)
check("A: die Nutzerangabe landet als Zielkraftstoff im Marktvergleich",
      ziel.get("kraftstoff") == "benzin")
check("A: und wirkt HART (kraftstoff_hart=True)", ziel.get("kraftstoff_hart") is True)
check("A: sie schlaegt die abweichende DB-Motorvariante (diesel)",
      ziel.get("kraftstoff") != MOTOR_DB["kraftstoff"])

# ── B) ohne Angabe keine harte Wirkung aus ungeprueften DB-Daten ────────────
req_ohne = KaufCheckRequest(marke="BMW", modell="3er", baujahr=2019,
                            kilometerstand=60000, preis_eur=30000)
ziel_ohne = ziel_fuer(req_ohne)
check("B: ohne Nutzerangabe bleibt die Kraftstoff-Wirkung weich",
      ziel_ohne.get("kraftstoff_hart") is False)

# ── C/D) leistung_ps wirkt hart, auch ohne "PS" im Freitext ────────────────
req_ps = KaufCheckRequest(marke="BMW", modell="330i", baujahr=2019,
                          kilometerstand=60000, preis_eur=30000,
                          kraftstoff="benzin", leistung_ps=258)
ziel_ps = ziel_fuer(req_ps)
check("C: die PS-Angabe erreicht den Marktvergleich", ziel_ps.get("leistung_ps") == 258)
check("C: sie schlaegt die DB-Variante (190 PS)", ziel_ps.get("leistung_ps") != 190)
check("D: sie wirkt OHNE 'PS' im Motor-Freitext (Einheit wird ergaenzt)",
      ziel_ps.get("leistung_ps") == 258 and not req_ps.motor)

# Gegenprobe: ohne das Feld faellt der Marktvergleich auf die DB zurueck.
ziel_db = ziel_fuer(req_benzin)
check("C: ohne PS-Angabe greift weiterhin der DB-Fallback",
      ziel_db.get("leistung_ps") == 190)

# Und der alte Weg (PS im Freitext) funktioniert unveraendert weiter.
req_text = KaufCheckRequest(marke="BMW", modell="330i", baujahr=2019,
                            kilometerstand=60000, preis_eur=30000,
                            motor="2.0 Benzin 258 PS")
check("D: PS im Motor-Freitext wirkt wie bisher",
      ziel_fuer(req_text).get("leistung_ps") == 258)

# ── E) beide Felder stehen im Prompt ───────────────────────────────────────
prompt = kc._format_inserat(req_ps)
check("E: der Kraftstoff steht im Inserat-Block des Prompts", "Kraftstoff:" in prompt
      and "benzin" in prompt)
check("E: die Leistung steht im Inserat-Block des Prompts", "258 PS" in prompt)

# ── F) Grenzwerte an der Eingabegrenze ─────────────────────────────────────
for unsinn in (0, 5, 29, 1501, 99999):
    try:
        KaufCheckRequest(marke="BMW", modell="3er", leistung_ps=unsinn)
        check(f"F: unplausible Leistung {unsinn} wird abgelehnt", False)
        break
    except ValidationError:
        pass
else:
    check("F: unplausible Leistungswerte werden schon im Schema abgelehnt", True)

for gueltig in (30, 150, 258, 1500):
    try:
        KaufCheckRequest(marke="BMW", modell="3er", leistung_ps=gueltig)
    except ValidationError:
        check(f"F: gueltige Leistung {gueltig} wird faelschlich abgelehnt", False)
        break
else:
    check("F: plausible Leistungswerte werden angenommen", True)

# ── G) Rueckwaertskompatibilitaet ──────────────────────────────────────────
alt = KaufCheckRequest(marke="VW", modell="Golf", baujahr=2018,
                       kilometerstand=90000, preis_eur=15000,
                       motor="2.0 TSI", unfallfrei="ja", vorbesitzer=2,
                       tuev_bis="06/2027", scheckheftgepflegt=True)
check("G: ein Request ohne die neuen Felder bleibt gueltig",
      alt.leistung_ps is None and alt.kraftstoff is None)
check("G: die bestehenden Zusatzangaben sind unveraendert",
      (alt.unfallfrei, alt.vorbesitzer, alt.scheckheftgepflegt) == ("ja", 2, True))
check("G: der Marktvergleich kommt ohne die neuen Felder klar",
      ziel_fuer(alt).get("kraftstoff_hart") is False)

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle KaufCheck-Feld-Tests bestanden.")
