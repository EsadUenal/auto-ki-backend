"""
I5 -- der Modellbezug bleibt ueber einen Doppelpunkt erhalten, aber NUR fuer zusammengehoerige Aussagen.

"BMW 3er: der 316d leistet 122 PS als Diesel." ist EINE Aussage ueber den 3er: das Segment hinter dem Doppelpunkt
praediziert ueber das Modell des Kopfes. Vorher zaehlte der Satz fuer die Zusatzwort-Pruefung nicht (Segmentgrenze
":"), der belegte Teil der Motorangabe ("316d 122 PS") blieb unbestaetigt. Ein Doppelpunkt allein vererbt nichts.

Quellen: SYNTHETISCH (Filterlogik, keine Fahrzeugfakten); Fahrzeug BMW 3er (DB-Baureihe vorhanden, Motor "316d" nicht).

    python scripts/test_isolated.py test_i5_modellbindung_doppelpunkt.py
"""
import asyncio
import os
import socket
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import app.recall_filter as _rf  # noqa: E402
_rf.get_rueckruf_referenzen_kurz = lambda: []

from app.car_lookup import find_baureihe_mit_vertrauen, find_motor  # noqa: E402
from app.models import KaufCheckRequest  # noqa: E402
from app.technical_research import FixtureTechnicalResearchProvider, recherchiere_technisch  # noqa: E402

FEHLER: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FEHLER.append(name)


def pruefe_isolation():
    db = os.environ.get("AUTO_KI_DB_PATH", "")
    try:
        socket.create_connection(("example.com", 80), timeout=1)
        netz = False
    except Exception:
        netz = True
    print("Isolation: Netz gesperrt =", netz, "| Temp-DB =", db)
    check("0 Isolation: Netz gesperrt und temporäre DB (nicht die Live-DB)", netz and "auto-ki-backend" not in db.replace("\\", "/").split("/Local/")[-1][:20] and bool(db))


pruefe_isolation()

ADAC = {"url": "https://www.adac.de/bmw-3er-g20", "title": "BMW 3er G20 technische Daten",
        "content": "Der BMW 3er ist als 316d mit 122 PS erhältlich."}
MOTOR = "316d 122 PS Nutzfahrzeugvariante"


def lauf(zweite, motor=MOTOR, erste=ADAC):
    req = KaufCheckRequest(marke="BMW", modell="3er", baujahr=2020, motor=motor)
    br_markt, info = find_baureihe_mit_vertrauen("BMW", "3er", 2020)
    mo_markt = find_motor(br_markt, motor) if br_markt else None
    br, mo = (br_markt, mo_markt) if info["belastbar"] else (None, None)
    quellen = [q for q in (erste, zweite) if q]
    prov = FixtureTechnicalResearchProvider({"identitaet": quellen, "schwachstelle": [], "rueckruf": [], "wartung": []})
    web = asyncio.run(recherchiere_technisch(req, br_markt, info, br, mo, provider=prov))
    ident = web.identitaet
    kd = ((ident.diagnose or {}).get("felder", {}).get("engine_name", {}).get("kern", {}))
    return ident, kd


def zweite(text, url="https://www.autobild.de/bmw-3er"):
    return {"url": url, "title": "BMW 3er", "content": text}


print("== A: zusammengehoerige Aussage ueber den Doppelpunkt ==")
ident, kd = lauf(zweite("BMW 3er: der 316d leistet 122 PS als Diesel."))
check("A1 Originalsatz mit Doppelpunkt bestaetigt den belegten Teil '316d 122 PS'", ident.motor == "316d 122 PS", str(ident.motor))
check("A2 der unbelegte Zusatz 'Nutzfahrzeugvariante' bleibt unbestaetigt (nicht in der bestaetigten Angabe, als unbelegter Rest gefuehrt)",
      "Nutzfahrzeugvariante" not in (ident.motor or "") and kd.get("unbelegter_rest") == ["Nutzfahrzeugvariante"],
      str((ident.motor, kd.get("unbelegter_rest"))))
check("A3 Schwelle unveraendert: Entscheidung 'bestaetigt' mit zwei Domains Tier<=2", kd.get("entscheidung") == "bestaetigt"
      and kd.get("domains_tier<=2") == 2, str({k: kd.get(k) for k in ("entscheidung", "domains_tier<=2")}))
ident2, kd2 = lauf(zweite("Der BMW 3er 316d leistet 122 PS als Diesel."))
check("A4 derselbe Satz OHNE Doppelpunkt: unveraendert bestaetigt", ident2.motor == "316d 122 PS", str(ident2.motor))

print("\n== B: kein pauschales Vererben -- der Doppelpunkt allein genuegt nicht ==")
FAELLE = {
    "B1 Kopf nennt das Modell nicht": "Technische Daten: der 316d leistet 122 PS als Diesel.",
    "B2 Doppelpunkt schliesst nicht das erste Segment ab": "BMW 3er, Technik: der 316d leistet 122 PS als Diesel.",
    "B3 fremde Marke im Segment": "BMW 3er: bei Mercedes C-Klasse leistet der 316d 122 PS als Diesel.",
    "B4 anderes Modell derselben Marke": "BMW 3er: im BMW X1 leistet der 316d 122 PS als Diesel.",
    "B5 'die Sonderedition dagegen'": "BMW 3er: die Sonderedition dagegen leistet mit dem 316d 122 PS als Diesel.",
    "B6 Gegensatz ohne Variantenwort": "BMW 3er: der 316d leistet dagegen 122 PS als Diesel.",
    "B7 Beschraenkung auf eine Teilmenge": "BMW 3er: nur für die Limousine leistet der 316d 122 PS als Diesel.",
    "B8 Variantenbezeichnung": "BMW 3er: die Ausführung mit 316d leistet 122 PS als Diesel.",
}
for name, text in FAELLE.items():
    i, k = lauf(zweite(text))
    check(f"{name}: keine Bestaetigung durch den Satz (nur eine qualifizierte Domain)", i.motor is None, str(i.motor))

print("\n== C: Verneinung und Domain-Schwelle ==")
i, k = lauf(zweite("BMW 3er: es gibt keinen 316d mit 122 PS als Diesel."))
check("C1 verneinte Aussage ueber den Doppelpunkt bestaetigt nichts", i.motor is None, str(i.motor))
i, k = lauf(zweite("BMW 3er: der 316d leistet nicht 122 PS als Diesel."))
i_ohne, _ = lauf(zweite("Der BMW 3er 316d leistet nicht 122 PS als Diesel."))
check("C2 verneinte Leistung: '122 PS' wird nicht bestaetigt, und der Doppelpunkt aendert das Ergebnis gegenueber dem Satz "
      "ohne Doppelpunkt nicht", "122" not in (i.motor or "") and i.motor == i_ohne.motor, str((i.motor, i_ohne.motor)))
i, k = lauf(zweite("BMW 3er: der 316d leistet 122 PS als Diesel."), erste={"url": "https://www.adac.de/bmw-3er-g20",
            "title": "BMW 3er G20", "content": "Der BMW 3er ist ein Mittelklassewagen."})
check("C3 eine einzelne qualifizierte Domain reicht weiterhin nicht", i.motor is None, str((i.motor, k.get("grund"))))
i, k = lauf(zweite("BMW 3er: der 316d leistet 122 PS als Diesel.", url="https://www.adac.de/bmw-3er-test"))
check("C4 dieselbe Domain zweimal zaehlt nicht als zwei Domains", i.motor is None, str((i.motor, k.get("domains_tier<=2"))))

print("\n" + ("ALLE GRÜN" if not FEHLER else f"{len(FEHLER)} FEHLER: {FEHLER}"))
sys.exit(1 if FEHLER else 0)
