"""
Test: die Buchfuehrung ueber den Rueckrufbestand geht auf.

WARUM ES DIESEN TEST GIBT
--------------------------
Vier Suiten (test_kba_abgleich, test_p0_datencleanup,
test_recall_insignia_012223, test_recall_pilot) pruefen Aussagen ueber den
Bestand des KBA-GESAMTABGLEICHS. Jede von ihnen blendet dafuer die spaeter
importierten Chargen aus. Kommt eine neue Charge hinzu und traegt sich nirgends
ein, zaehlen diese Suiten sie stillschweigend mit und schlagen fehl, obwohl an
den Daten nichts falsch ist.

Genau das ist passiert: der G20-Nachtrag (18e0283, drei amtliche BMW-Rueckrufe
aus dem KBA-Gesamtexport) hatte als einziges Importmodul keinen
`zeilen_ids()`-Helfer und war in drei der vier Suiten nicht eingetragen. Die
Folge waren vier rote Suiten ohne Produktfehler — teuer zu diagnostizieren,
weil "rot" nach Datenfehler aussieht.

Dieser Test macht die Buchfuehrung explizit:

    Gesamtbestand = Gesamtabgleich (746) + Summe aller Import-Chargen

Schlaegt er fehl, ist die Ursache benannt, statt sie sich aus vier
Zaehlfehlern rekonstruieren zu muessen.

Ohne Netzwerk, ohne Provider. Ausfuehren: python test_recall_bestand_buchfuehrung.py
"""
import importlib
import pathlib
import re

from app.database import get_conn

FEHLER = []


def check(name, cond, detail=""):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FEHLER.append(name)


# Ergebnis des KBA-Gesamtabgleichs: 749 Seed-Zeilen minus 3 wortgleiche
# Dubletten (G-Klasse, Audi A1, Audi TT RS). Diese Zahl ist ein abgeschlossener
# historischer Stand und aendert sich nicht mehr; jeder Zuwachs ist eine Charge.
GESAMTABGLEICH = 746

# Alle Module, die Rueckrufzeilen importieren. Neue Charge -> hier eintragen.
IMPORT_MODULE = (
    "app.kba_batch_a_daten",
    "app.kba_batch_b1_daten",
    "app.kba_mixed_target_daten",
    "app.kba_g20_nachtrag_daten",
)


# ── A) Jedes Importmodul liefert seine IDs ──────────────────────────────────
chargen = {}
for name in IMPORT_MODULE:
    modul = importlib.import_module(name)
    hat_helfer = hasattr(modul, "zeilen_ids") and callable(modul.zeilen_ids)
    check(f"A: {name} hat zeilen_ids()", hat_helfer)
    if not hat_helfer:
        continue
    ids = modul.zeilen_ids()
    zeilen = getattr(modul, "ZEILEN", ())
    check(f"A: {name} — zeilen_ids() deckt sich mit ZEILEN",
          len(ids) == len(zeilen), f"{len(ids)} vs {len(zeilen)}")
    chargen[name] = ids

alle_import_ids = set().union(*chargen.values()) if chargen else set()
check("A: keine ID wird von zwei Chargen beansprucht",
      len(alle_import_ids) == sum(len(v) for v in chargen.values()))


# ── B) Kein Importmodul fehlt in der Liste ──────────────────────────────────
#
# Gegen das eigentliche Versaeumnis: ein neues `kba_*_daten.py` mit ZEILEN, das
# niemand eingetragen hat. Reine Referenzdaten ohne ZEILEN (z.B. Audit-Listen)
# sind kein Import und bleiben aussen vor.
gefunden = []
for pfad in sorted(pathlib.Path("app").glob("kba_*_daten.py")):
    quelle = pfad.read_text(encoding="utf-8")
    if re.search(r"^ZEILEN\s*[:=]", quelle, re.M):
        gefunden.append(f"app.{pfad.stem}")
check("B: alle Importmodule sind in IMPORT_MODULE eingetragen",
      set(gefunden) == set(IMPORT_MODULE),
      f"nicht eingetragen: {sorted(set(gefunden) - set(IMPORT_MODULE))}")


# ── C) Die Buchfuehrung geht auf ────────────────────────────────────────────
with get_conn() as conn:
    gesamt = conn.execute("SELECT COUNT(*) FROM rueckruf").fetchone()[0]
    vorhanden = {r[0] for r in conn.execute("SELECT id FROM rueckruf")}

erwartet = GESAMTABGLEICH + len(alle_import_ids)
check(f"C: Gesamtbestand = {GESAMTABGLEICH} + {len(alle_import_ids)} = {erwartet}",
      gesamt == erwartet, f"tatsaechlich {gesamt}")

fehlende = alle_import_ids - vorhanden
check("C: jede importierte Zeile liegt auch wirklich in der Datenbank",
      not fehlende, f"fehlend: {sorted(fehlende)[:10]}")


# ── D) Die drei Dubletten bleiben entfernt ──────────────────────────────────
from app.kba_abgleich_daten import DUBLETTEN   # noqa: E402

noch_da = [d[0] for d in DUBLETTEN if d[0] in vorhanden]
check("D: die drei wortgleichen Dubletten sind und bleiben entfernt",
      not noch_da, f"wieder da: {noch_da}")
for _weg, kanonisch, _bid, _begr in DUBLETTEN:
    check(f"D: die kanonische Zeile #{kanonisch} ist erhalten geblieben",
          kanonisch in vorhanden)


print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print("  -", f)
    print("\nHinweis: kam eine neue amtliche Charge hinzu, gehoert sie in")
    print("IMPORT_MODULE (hier) UND in die Ausblendung der vier Bestandssuiten.")
    raise SystemExit(1)
print("Rueckruf-Buchfuehrung geht auf.")
