"""
Test: Phase 5 VIRA Dealer (app/routers/dealer + app/dealer) — kein LLM, keine Netzwerk.

Ruft die Router-Funktionen direkt gegen eine TEMPORAERE DB. Deckt §26 ab:
Ownership (lesen/ändern/löschen), Nicht-Dealer-403, Status-Validierung, Finanz-
berechnung, from-check (Übernahme + Idempotenz), Kaufcheck-Verknüpfung, Check-
Löschung zerstört DealerVehicle nicht, Summary, Fahrzeug ohne Preise, verkauftes
Fahrzeug mit realisierter Marge, Consumer-Checks unverändert.

Ausfuehren:  python test_dealer.py
"""
import json
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="vira_dealer_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")

import app.database as db                 # noqa: E402
db.ensure_tables()

import app.routers.dealer as dealer_r     # noqa: E402
from app.dealer import require_dealer, berechne_finanzen   # noqa: E402
from app.models import DealerVehicleCreate, DealerVehicleUpdate  # noqa: E402
from fastapi import HTTPException          # noqa: E402
from pydantic import ValidationError       # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


def raises_status(fn, code) -> bool:
    try:
        fn()
        return False
    except HTTPException as e:
        return e.status_code == code


def verr(fn) -> bool:
    """True, wenn fn() eine Pydantic-ValidationError wirft (z.B. ungültiger Wert)."""
    try:
        fn()
        return False
    except ValidationError:
        return True


def neuer_user(email: str, haendler: bool) -> int:
    with db.get_conn() as conn:
        return conn.execute(
            "INSERT INTO users (email, password_hash, ist_haendler) VALUES (?, 'x', ?)",
            (email, 1 if haendler else 0),
        ).lastrowid


KAUF_ERGEBNIS = {
    "empfehlung": "nur_mit_werkstattpruefung",
    "preis_bewertung": "marktgerecht",
    "marktpreis_min": 22000, "marktpreis_max": 26000,
    "baureihe_erkannt": "bmw-3er-g20", "motor_erkannt": "bmw-3er-g20-320d",
    "vertrauen": "mittel",
    "insights": [
        {"kategorie": "marktvergleich", "marktanalyse": {"median_eur": 24000}},
        {"kategorie": "rueckruf", "applicability": "wahrscheinlich"},
    ],
    "key_findings": [
        {"stufe": "warnung", "titel": "1 relevanter Rückruf"},
        {"stufe": "info", "titel": "Preis marktgerecht"},
    ],
}


def neuer_kaufcheck(user_id: int, titel="BMW 320d 2020") -> int:
    eingabe = {"marke": "BMW", "modell": "320d", "baujahr": 2020, "kilometerstand": 78500, "motor": "2.0 Diesel"}
    with db.get_conn() as conn:
        return conn.execute(
            "INSERT INTO checks (user_id, typ, titel, eingabe, ergebnis) VALUES (?,?,?,?,?)",
            (user_id, "kauf", titel, json.dumps(eingabe), json.dumps(KAUF_ERGEBNIS)),
        ).lastrowid


dealer_uid = neuer_user("dealer@test.local", haendler=True)
dealer2_uid = neuer_user("dealer2@test.local", haendler=True)
normal_uid = neuer_user("normal@test.local", haendler=False)

# ── 5) Nicht-Dealer bekommt keinen Zugriff (Backend, nicht nur UI) ───────────
check("5: Nicht-Dealer -> 403 bei require_dealer", raises_status(lambda: require_dealer(normal_uid), 403))
check("5b: Dealer -> require_dealer gibt user_id", require_dealer(dealer_uid) == dealer_uid)

# ── 1) Dealer erstellt Fahrzeug (manuell) ────────────────────────────────────
v = dealer_r.create_vehicle(DealerVehicleCreate(marke="Mercedes-Benz", modell="C 200", baujahr=2019,
                                                kilometerstand=64300, status="beobachtung"), dealer_uid)
check("1: Fahrzeug erstellt (Status beobachtung)", v.status == "beobachtung" and v.marke == "Mercedes-Benz")
vid = v.id

# ── 2/3/4) Fremder Nutzer kann nicht lesen/ändern/löschen ────────────────────
check("2: Fremd lesen -> 404", raises_status(lambda: dealer_r.get_vehicle(vid, dealer2_uid), 404))
check("3: Fremd ändern -> 404",
      raises_status(lambda: dealer_r.update_vehicle(vid, DealerVehicleUpdate(einkaufspreis=1), dealer2_uid), 404))
check("4: Fremd löschen -> 404", raises_status(lambda: dealer_r.delete_vehicle(vid, dealer2_uid), 404))
check("4b: Fahrzeug nach Fremdzugriff unverändert vorhanden",
      dealer_r.get_vehicle(vid, dealer_uid).id == vid)

# ── 6) Status-Validierung ────────────────────────────────────────────────────
check("6: ungültiger Status -> ValidationError", verr(lambda: DealerVehicleCreate(marke="X", status="quatsch")))
check("6b: negativer Preis -> ValidationError", verr(lambda: DealerVehicleCreate(einkaufspreis=-5)))
check("6c: negativer km -> ValidationError", verr(lambda: DealerVehicleCreate(kilometerstand=-1)))

# ── 7) Finanzberechnung korrekt ──────────────────────────────────────────────
f = berechne_finanzen({"einkaufspreis": 20000, "nebenkosten": 800, "geplanter_verkaufspreis": 25000,
                       "tatsaechlicher_verkaufspreis": None})
check("7: Gesamteinsatz 20.800", f.gesamteinsatz == 20800)
check("7b: mögliche Bruttomarge 4.200", f.moegliche_bruttomarge == 4200)
check("7c: Marge-% auf Verkaufspreis (~16,8)", f.moegliche_marge_pct == 16.8)
# Negative Marge korrekt negativ
fn = berechne_finanzen({"einkaufspreis": 26000, "nebenkosten": 500, "geplanter_verkaufspreis": 25000})
check("7d: negative Marge bleibt negativ", fn.moegliche_bruttomarge == -1500)
# Fehlende Werte -> None (keine erfundene 0-€-Marge)
fm = berechne_finanzen({"einkaufspreis": None, "nebenkosten": None, "geplanter_verkaufspreis": 25000})
check("7e: fehlender Einkauf -> gesamteinsatz None, Marge None",
      fm.gesamteinsatz is None and fm.moegliche_bruttomarge is None)

# ── 13) Fahrzeug ohne Preise funktioniert (keine Fake-Marge) ─────────────────
v_ohne = dealer_r.create_vehicle(DealerVehicleCreate(marke="VW", modell="Golf", baujahr=2016,
                                                     kilometerstand=120000), dealer_uid)
check("13: Fahrzeug ohne Preise -> Marge None (kein 0 €)",
      v_ohne.finanzen.moegliche_bruttomarge is None and v_ohne.finanzen.gesamteinsatz is None)

# ── 8) from-check übernimmt Fahrzeugdaten ────────────────────────────────────
cid = neuer_kaufcheck(dealer_uid)
fromv = dealer_r.create_from_check(cid, dealer_uid)
check("8: from-check übernimmt Marke/Modell/Baujahr/km",
      fromv.marke == "BMW" and fromv.modell == "320d" and fromv.baujahr == 2020 and fromv.kilometerstand == 78500)
check("8b: from-check Status beobachtung + baureihe aus Ergebnis",
      fromv.status == "beobachtung" and fromv.baureihe == "bmw-3er-g20")

# ── 10) Kaufcheck-Verknüpfung: VIRA-Signale übernommen ───────────────────────
check("10: Empfehlung aus Kaufcheck übernommen", fromv.vira.vorhanden and fromv.vira.empfehlung == "nur_mit_werkstattpruefung")
check("10b: Triage-Empfehlung gemappt (nach_pruefung)", fromv.triage.empfehlung == "nach_pruefung")
check("10c: Markt-Median aus Insights (24.000)", fromv.vira.markt_median == 24000)
check("10d: Risiko-Hinweise aus Key Findings (Warnung)", any("Rückruf" in h for h in fromv.vira.risiko_hinweise))
check("10e: Triage-Risiko 'pruefen' (relevanter Rückruf, keine kritische Warnung)", fromv.triage.risiko == "pruefen")

# ── 9) from-check mehrfach -> kein Duplikat (idempotent) ─────────────────────
fromv2 = dealer_r.create_from_check(cid, dealer_uid)
check("9: from-check erneut -> selbe ID (kein Duplikat)", fromv2.id == fromv.id)
alle = dealer_r.list_vehicles(dealer_uid)
check("9b: genau ein Fahrzeug mit diesem Kaufcheck",
      sum(1 for x in alle if x.kaufcheck_id == cid) == 1)

# ── 11) Check-Löschung zerstört DealerVehicle NICHT (SET NULL) ───────────────
import app.routers.checks as checks_r      # noqa: E402
checks_r.delete_check(cid, dealer_uid)
still = dealer_r.get_vehicle(fromv.id, dealer_uid)
check("11: Fahrzeug existiert nach Check-Löschung weiter", still.id == fromv.id)
check("11b: kaufcheck_id auf NULL gesetzt", still.kaufcheck_id is None)
check("11c: VIRA-Signale entfallen sauber (vorhanden=False)", still.vira.vorhanden is False)

# ── 14) Verkauftes Fahrzeug -> realisierte Marge + sold_at ───────────────────
sale = dealer_r.update_vehicle(vid, DealerVehicleUpdate(
    einkaufspreis=20000, nebenkosten=800, geplanter_verkaufspreis=25000), dealer_uid)
check("14a: mögliche Marge vor Verkauf 4.200", sale.finanzen.moegliche_bruttomarge == 4200)
sale = dealer_r.update_vehicle(vid, DealerVehicleUpdate(status="verkauft", tatsaechlicher_verkaufspreis=24000), dealer_uid)
check("14b: realisierte Bruttomarge 3.200", sale.finanzen.realisierte_bruttomarge == 3200)
check("14c: status verkauft + sold_at gesetzt", sale.status == "verkauft" and sale.sold_at is not None)

# ── 12) Summary korrekt ──────────────────────────────────────────────────────
s = dealer_r.dealer_summary(dealer_uid)
# Bestand: keiner mehr "im_bestand" (vid ist verkauft); mehrere beobachtung.
check("12: Summary zählt verkaufte Fahrzeuge", s.verkauft == 1)
check("12b: realisierte Bruttomarge summiert (3.200)", s.realisierte_bruttomarge == 3200)
check("12c: fahrzeuge_gesamt == Anzahl Fahrzeuge des Dealers", s.fahrzeuge_gesamt == len(dealer_r.list_vehicles(dealer_uid)))
# Ein Fahrzeug in Bestand ohne Einkaufspreis -> braucht Aufmerksamkeit
vb = dealer_r.create_vehicle(DealerVehicleCreate(marke="Audi", modell="A4", status="im_bestand"), dealer_uid)
check("12d: im_bestand ohne Einkaufspreis -> braucht Aufmerksamkeit",
      vb.braucht_aufmerksamkeit and any("Einkaufspreis" in g for g in vb.aufmerksamkeit_gruende))
check("12e: gebundenes_kapital None (kein Bestandsfahrzeug mit Einsatz)",
      dealer_r.dealer_summary(dealer_uid).gebundenes_kapital is None)

# ── from-check nur für Kauf, nur eigener Check ───────────────────────────────
vk_cid = None
with db.get_conn() as conn:
    vk_cid = conn.execute("INSERT INTO checks (user_id, typ, titel, eingabe, ergebnis) VALUES (?,?,?,?,?)",
                          (dealer_uid, "verkauf", "VK", "{}", "{}")).lastrowid
check("F1: from-check mit Verkaufscheck -> 422", raises_status(lambda: dealer_r.create_from_check(vk_cid, dealer_uid), 422))
fremd_cid = neuer_kaufcheck(dealer2_uid)
check("F2: from-check mit fremdem Check -> 404", raises_status(lambda: dealer_r.create_from_check(fremd_cid, dealer_uid), 404))

# ── 15/16) Consumer-Kauf-/Verkaufscheck-Logik unverändert (Smoke) ────────────
# (Die Dealer-Erweiterung fügt nur additive Spalten/Router hinzu; Consumer-Checks
#  laufen weiter über ihre eigenen Router. Hier: Kaufcheck-Persistenz unberührt.)
c2 = neuer_kaufcheck(normal_uid, "Consumer-Kauf")
got = checks_r.get_check(c2, normal_uid)
check("15/16: Consumer-Kaufcheck unverändert lesbar", got["typ"] == "kauf" and got["ergebnis"]["empfehlung"] == "nur_mit_werkstattpruefung")

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Dealer-Tests bestanden.")
