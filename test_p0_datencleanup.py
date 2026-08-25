# -*- coding: utf-8 -*-
"""
P0-DATEN-CLEANUP — Regression gegen die Fahrzeugdatenbank.
KEIN Netzwerk, KEIN LLM-Call, KEIN Tavily.

Prueft die sieben Zusicherungen A-G des Cleanups (p0_cleanup_2026_08_25.py).
Ohne erreichbare Fahrzeugdatenbank wird sauber uebersprungen.

    python test_p0_datencleanup.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, ".")

DB = os.environ.get("AUTO_KI_DB_PATH") or os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "auto-ki-backend", "auto_ki.db")

FEHLER: list[str] = []


def check(name, ok, info=""):
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f"   {info}" if info else ""))
    if not ok:
        FEHLER.append(name)


if not os.path.exists(DB):
    print(f"[SKIP] Fahrzeugdatenbank nicht gefunden ({DB}) — Suite uebersprungen")
    raise SystemExit(0)

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row
q = lambda s, *a: [dict(r) for r in c.execute(s, a)]

if not q("select 1 from baureihe limit 1"):
    print("[SKIP] keine Baureihendaten — Suite uebersprungen")
    raise SystemExit(0)

from app.car_lookup import find_baureihe_mit_vertrauen, find_motor  # noqa: E402

# ── A) Phantom BMW 8er E63/E64 ───────────────────────────────────────────────
print("=== A) Phantom-Baureihe BMW 8er E63/E64 ===")
check("A1 Phantom-Baureihe existiert nicht mehr",
      not q("select 1 from baureihe where id='bmw-8er-e63-e64'"))
br, info = find_baureihe_mit_vertrauen("BMW", "8er", 2008)
check("A2 'BMW 8er 2008' matcht die Phantom-Baureihe nicht mehr",
      (br or {}).get("id") != "bmw-8er-e63-e64", f"-> {br and br['id']}")
# §11 A verlangt ausdruecklich: kein exact/high-confidence Match. Ein 8er aus 2008
# hat es nie gegeben; das Identity-Trust-Gate muss das als nicht belastbar melden.
check("A3 die Zuordnung ist nicht belastbar (kein exact/hoch)",
      not info["belastbar"] and info["konfidenz"] != "hoch",
      f"{info['match_art']}/{info['konfidenz']}")
check("A4 die erfundene Bezeichnung '845Ci' existiert nirgends mehr",
      not q("select 1 from motorvariante where bezeichnung='845Ci'"))
# Das reale 850i des E31 (M70B50 V12) muss ERHALTEN bleiben — es ist kein Phantom.
achtziger = q("select baureihe_id,motorcode from motorvariante where bezeichnung='850i'")
check("A5 das reale 850i des E31 bleibt unangetastet",
      len(achtziger) == 1 and achtziger[0]["baureihe_id"] == "bmw-8er-e31"
      and achtziger[0]["motorcode"] == "M70B50", str(achtziger))
check("A6 der reale 6er E63/E64 ist vollstaendig erhalten",
      len(q("select 1 from motorvariante where baureihe_id='bmw-6er-e63-e64'"
            " and bezeichnung in ('645Ci','650i','M6','630i (N52)','630i (N53)','635d')")) == 6)

# ── B) W205 C200 Plug-in-Hybrid ──────────────────────────────────────────────
print("\n=== B) W205 C200 Plug-in-Hybrid ===")
check("B1 die erfundene Zeile ist entfernt",
      not q("select 1 from motorvariante where bezeichnung='C200 Plug-in-Hybrid'"))
phev = sorted(r["bezeichnung"] for r in q(
    "select bezeichnung from motorvariante where baureihe_id='mercedes-benz-c-klasse-w205'"
    " and lower(kraftstoff)='plug-in-hybrid'"))
check("B2 die realen PHEV-Varianten bleiben",
      phev == ["C300 Plug-in-Hybrid", "C350 Plug-in-Hybrid"], str(phev))
check("B3 die uebrigen W205-Varianten sind unangetastet",
      len(q("select 1 from motorvariante where baureihe_id='mercedes-benz-c-klasse-w205'")) == 21)

# ── C) Toyota RAV4 II ────────────────────────────────────────────────────────
print("\n=== C) Toyota RAV4 II 2.0 Diesel ===")
r = q("select bezeichnung,motorcode,kraftstoff,hubraum_ccm,leistung_ps,leistung_kw"
      " from motorvariante where variante_id='toyota-rav4-ii-2-0-vvt-i'")
check("C0 die Zeile existiert weiterhin", len(r) == 1)
if r:
    r = r[0]
    check("C1 Bezeichnung ist der reale Dieselname", r["bezeichnung"] == "2.0 D-4D", str(r))
    check("C2 Motorcode ist der reale Dieselcode", r["motorcode"] == "1CD-FTV")
    check("C3 Kraftstoff bleibt Diesel — er war korrekt", r["kraftstoff"] == "Diesel")
    check("C4 die belegenden Kennwerte sind unveraendert",
          (r["hubraum_ccm"], r["leistung_ps"], r["leistung_kw"]) == (1995, 116, 85))
check("C5 kein 1AZ-FSE wird mehr als Diesel gefuehrt",
      not q("select 1 from motorvariante where motorcode='1AZ-FSE' and lower(kraftstoff)='diesel'"))
check("C6 die echte 1AZ-FSE-Benzinerzeile ist unangetastet",
      len(q("select 1 from motorvariante where motorcode='1AZ-FSE'"
            " and lower(kraftstoff)='benzin'")) == 1)

# ── D) Opel Insignia B ───────────────────────────────────────────────────────
print("\n=== D) Opel Insignia B Motorcode ===")
check("D1 der falsche Code 'F20DTH' kommt nicht mehr vor",
      not q("select 1 from motorvariante where motorcode='F20DTH'"))
z174 = q("select bezeichnung,motorcode from motorvariante"
         " where baureihe_id='opel-insignia-b' and leistung_ps=174")
check("D2 beide 174-PS-Varianten existieren weiter", len(z174) == 2, str(z174))
# Der reale Code ist mehrfach unabhaengig belegt (motordirekt.de, autodoc.de,
# fair-motors.de, Insignia-B-Forum): F20DVH, 128 kW / 174 PS, ab 04/2020.
check("D3 der belegte Code F20DVH ist gesetzt",
      all((x["motorcode"] or "") == "F20DVH" for x in z174), str(z174))
check("D4 die belegten Codes der uebrigen Diesel bleiben",
      len(q("select 1 from motorvariante where baureihe_id='opel-insignia-b'"
            " and motorcode in ('B20DTH','B16DTH','B16DTL','B20DTR')")) == 5)

# ── E) Zahnriemen auf Kettenmotoren ──────────────────────────────────────────
print("\n=== E) Zahnriemen-Eintraege auf Kettenmotoren ===")
KETTENCODES = ("M10B16", "M10B18", "S14B23", "M30B30", "M30B35", "CDAA", "CCZA", "2AZ-FE")
rest = q(f"""select w.id,b.id bid,m.bezeichnung,m.motorcode from kritische_wartung w
   join motorvariante m on m.variante_id=w.variante_id join baureihe b on b.id=m.baureihe_id
   where w.bauteil like '%Zahnriemen%'
     and (b.id in ('bmw-7er-e23','bmw-7er-e32')
          or m.motorcode in ({",".join("?" * len(KETTENCODES))}))""", *KETTENCODES)
check("E1 kein Zahnriemen-Wartungspunkt mehr auf einem Kettenmotor", not rest, str(rest))
riemen = q("""select m.bezeichnung,m.motorcode from kritische_wartung w
   join motorvariante m on m.variante_id=w.variante_id
   where m.baureihe_id='bmw-3er-e30' and w.bauteil like '%Zahnriemen%' order by m.bezeichnung""")
check("E2 echte Riemenmotoren derselben Baureihe bleiben erhalten (E30 M20/M21)",
      len(riemen) == 4 and all(x["motorcode"].startswith(("M20", "M21")) for x in riemen),
      ", ".join(f"{x['bezeichnung']}[{x['motorcode']}]" for x in riemen))
# §5: es darf KEIN Ketten-Ersatzintervall erfunden worden sein. Die vier
# Steuerketten-Eintraege auf CDAA/CCZA stammen aus Audi TT 8J und VW Passat B7 und
# standen dort schon vorher — der Cleanup hat nichts hinzugefuegt.
neue_kette = q(f"""select b.id bid,m.bezeichnung from kritische_wartung w
   join motorvariante m on m.variante_id=w.variante_id join baureihe b on b.id=m.baureihe_id
   where w.bauteil like '%Steuerkette%'
     and (b.id in ('bmw-3er-e30','bmw-7er-e23','bmw-7er-e32','skoda-superb-zweite-generation',
                   'seat-leon-zweite-generation','toyota-camry-xv30'))""")
check("E3 auf den bereinigten Fahrzeugen wurde kein Ketten-Intervall erfunden",
      not neue_kette, str(neue_kette))

# ── F) BMW 3er G20/G21 ───────────────────────────────────────────────────────
print("\n=== F) BMW G20 — eine Datenwelt ===")
check("F1 die Dublette bmw-3er-g20 ist aufgeloest",
      not q("select 1 from baureihe where id='bmw-3er-g20'"))
check("F2 nur noch EINE 3er-Baureihe ab 2019",
      q("select count(*) n from baureihe where marke='BMW' and modell='3er'"
        " and bauzeitraum_von=2019")[0]["n"] == 1)
br3, _ = find_baureihe_mit_vertrauen("BMW", "3er", 2020)
check("F3 die Aufloesung ist stabil und kanonisch", br3["id"] == "bmw-3er-g20-g21", br3["id"])
m3 = [x["bezeichnung"] for x in q("select bezeichnung from motorvariante"
                                  " where baureihe_id='bmw-3er-g20-g21' order by bezeichnung")]
check("F4 die Motorlisten sind zusammengefuehrt", len(m3) == 16, f"{len(m3)}")
for bez in ("318d", "320d xDrive", "330e", "M340d xDrive"):
    check(f"F4.{bez} vorhanden", bez in m3)
vg = q("select vorgaenger from baureihe where id='bmw-3er-g20-g21'")[0]["vorgaenger"]
check("F5 vorgaenger zeigt auf eine existierende Baureihe",
      bool(q("select 1 from baureihe where id=?", vg)), repr(vg))
check("F6 die widerspruechlichen Rueckrufe wurden NICHT zusammengeworfen",
      q("select count(*) n from rueckruf where baureihe_id='bmw-3er-g20-g21'")[0]["n"] == 3)
check("F7 der 320d ist genau EINMAL vorhanden",
      len([x for x in m3 if x == "320d"]) == 1)

# ── G) BMW 1er F20/F21 ───────────────────────────────────────────────────────
print("\n=== G) BMW F20/F21 — eine Datenwelt ===")
check("G1 die Dublette bmw-1er-f2x ist aufgeloest",
      not q("select 1 from baureihe where id='bmw-1er-f2x'"))
check("G2 nur noch EINE 1er-Baureihe ab 2011",
      q("select count(*) n from baureihe where marke='BMW' and modell='1er'"
        " and bauzeitraum_von=2011")[0]["n"] == 1)
br1, _ = find_baureihe_mit_vertrauen("BMW", "1er", 2014)
check("G3 die Aufloesung ist kanonisch", br1["id"] == "bmw-1er-f20-f21", br1["id"])
m1 = [x["bezeichnung"] for x in q("select bezeichnung from motorvariante"
                                  " where baureihe_id='bmw-1er-f20-f21' order by bezeichnung")]
check("G4 die Motorlisten sind zusammengefuehrt", len(m1) == 7, str(m1))
for bez in ("118d", "120i", "116i"):
    check(f"G4.{bez} vorhanden", bez in m1)
kar = q("select karosserie from baureihe where id='bmw-1er-f20-f21'")[0]["karosserie"]
check("G5 die Karosserien sind korrekt (Schraegheck, kein Coupe)",
      "Schrägheck" in kar and "Coup" not in kar, kar)
sw = [x["bauteil"] for x in q("select bauteil from schwachstelle_baureihe"
                              " where baureihe_id='bmw-1er-f20-f21'")]
# Der Kern des Merges: die UNSCHARFE Schwachstelle "Steuerkette" (Schweregrad hoch,
# "insbesondere N13, N20, N47") darf NICHT mituebernommen worden sein — sie haette
# die motorgenaue Trennung des Runtime-Gates wieder aufgehoben.
check("G6 die unscharfe 'Steuerkette' wurde nicht uebernommen", "Steuerkette" not in sw, str(sw))
check("G7 die motorgenau gescopten Steuerketten sind erhalten",
      any("N47" in s for s in sw) and any("N20" in s for s in sw))
check("G8 echte Zusatzinformation wurde uebernommen",
      "Batterie / Start-Stopp-System" in sw and "Fahrwerk (Verschleißteile)" in sw)
check("G9 Wartungsdaten der aufgeloesten Zeile sind erhalten (vorher 0)",
      q("select count(*) n from kritische_wartung w join motorvariante m"
        " on m.variante_id=w.variante_id where m.baureihe_id='bmw-1er-f20-f21'")[0]["n"] == 5)

# ── H) Keine Waisen ──────────────────────────────────────────────────────────
print("\n=== H) Referenzielle Integritaet ===")
for name, sql in {
    "motorvariante ohne baureihe": "select count(*) from motorvariante m where not exists"
        "(select 1 from baureihe b where b.id=m.baureihe_id)",
    "schwachstelle_baureihe ohne baureihe": "select count(*) from schwachstelle_baureihe s"
        " where not exists(select 1 from baureihe b where b.id=s.baureihe_id)",
    "rueckruf ohne baureihe": "select count(*) from rueckruf r where not exists"
        "(select 1 from baureihe b where b.id=r.baureihe_id)",
    "ausstattungslinie ohne baureihe": "select count(*) from ausstattungslinie a where not exists"
        "(select 1 from baureihe b where b.id=a.baureihe_id)",
    "schwachstelle_motor ohne motorvariante": "select count(*) from schwachstelle_motor s"
        " where not exists(select 1 from motorvariante m where m.variante_id=s.variante_id)",
    "kritische_wartung ohne motorvariante": "select count(*) from kritische_wartung w"
        " where not exists(select 1 from motorvariante m where m.variante_id=w.variante_id)",
}.items():
    n = c.execute(sql).fetchone()[0]
    check(f"H {name}: 0", n == 0, f"n={n}")

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle P0-Cleanup-Regressionen bestanden.")