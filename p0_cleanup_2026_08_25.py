# -*- coding: utf-8 -*-
"""
P0-DATEN-CLEANUP (DATA-TRUTH-AUDIT) — einmalige, idempotente Datenkorrektur.

Korrigiert AUSSCHLIESSLICH die im Audit konkret nachgewiesenen und extern
verifizierten Falschdaten. Keine breite DB-Neuschreibung.

Ausfuehren:   python p0_cleanup_2026_08_25.py            (Trockenlauf, kein Schreiben)
              python p0_cleanup_2026_08_25.py --apply    (in EINER Transaktion)

Sicherheiten:
  * eine einzige Transaktion; jeder Fehler -> vollstaendiger ROLLBACK
  * PRAGMA foreign_keys=ON (die Schemata haben ON DELETE CASCADE, das Pragma ist
    per Default aus) -> keine Orphans
  * jede Mutation ist durch ein INHALTS-Praedikat abgesichert, nicht nur durch die
    Zeilen-ID: verschiebt sich eine ID, greift die Regel gar nicht, statt die
    falsche Zeile zu treffen
  * idempotent: ein zweiter Lauf findet nichts mehr zu tun
  * integrity_check + foreign_key_check + Zeilenzaehlung vorher/nachher

QUELLENNACHWEIS je Korrektur steht am jeweiligen Schritt.
"""
import argparse
import os
import sqlite3
import sys

DB = os.environ.get("AUTO_KI_DB_PATH") or os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "auto-ki-backend", "auto_ki.db")

TABELLEN = ("baureihe", "motorvariante", "schwachstelle_baureihe", "schwachstelle_motor",
            "kritische_wartung", "rueckruf", "ausstattungslinie", "quelle")

protokoll: list[str] = []


def log(zeile: str) -> None:
    protokoll.append(zeile)
    print(zeile)


def norm(s):
    return " ".join((s or "").lower().split())


_STOP = {"und", "oder", "bei", "der", "die", "das", "von", "mit", "fuer", "alle"}


def tokens(s):
    """Bedeutungstragende Tokens eines Bauteil-/Namensfeldes (>=4 Zeichen)."""
    roh = "".join(ch if ch.isalnum() else " " for ch in (s or "").lower()).split()
    return {t for t in roh if len(t) >= 4 and t not in _STOP}


def ueberschneidet(bauteil, vorhandene):
    """True, wenn `bauteil` inhaltlich schon abgedeckt ist.

    Reine Gleichheit reicht nicht: der 1er-Duplikatfall zeigt, warum. Die
    aufzuloesende Baureihe fuehrt eine UNGESCHAERFTE Schwachstelle "Steuerkette"
    (Schweregrad hoch), die kanonische zwei praezise: "Steuerkette (N47
    Dieselmotoren)" und "Steuerkette (N20 Benzinmotoren)". Ein Merge nach exakter
    Gleichheit haette die unscharfe Variante zusaetzlich eingefuegt und damit
    genau die Motor-Zuordnung wieder zerstoert, die das Runtime-Gate herstellt.
    Deshalb: Ueberschneidung an einem bedeutungstragenden Token genuegt, damit die
    SPEZIFISCHERE Fassung der kanonischen Baureihe gewinnt.
    """
    t = tokens(bauteil)
    return any(t & tokens(v) for v in vorhandene)


def zaehle(conn):
    return {t: conn.execute(f"select count(*) from {t}").fetchone()[0] for t in TABELLEN}


# ─────────────────────────────────────────────────────────────────────────────
# Schritt 1 — Phantom-Baureihe BMW 8er E63/E64
#
# QUELLE: de.wikipedia.org/wiki/BMW_E63 und auto-motor-und-sport.de (Technische
# Daten BMW 6er E63/E64, Baujahr 2003 bis 2010). Beide belegen: E63 (Coupe) und
# E64 (Cabrio) sind die BMW 6er-Reihe 2003-2010, Nachfolger des 8er-Coupes E31,
# das Mitte 1999 auslief. Modellbezeichnungen: 645Ci (N62 4,4 l, 333 PS),
# ab 07/2005 650i (N62 4,8 l, 367 PS), M6 und 635d. Ein "845Ci" oder "850i" hat
# nie existiert; zwischen E31 (bis 1999) und G15 (ab 2018) gab es keinen 8er.
#
# BEFUND IN DEN DATEN: die drei Motorvarianten der Phantom-Baureihe sind in
# Motorcode, Hubraum, Zylinderzahl und Leistung IDENTISCH mit 645Ci/650i/M6 der
# realen Baureihe bmw-6er-e63-e64 — nur umbenannt. Auch die Schwachstellen sind
# Dubletten. Es geht also nichts Eigenstaendiges verloren.
PHANTOM_BAUREIHE = "bmw-8er-e63-e64"
PHANTOM_REAL = "bmw-6er-e63-e64"
PHANTOM_MOTOREN = {"845ci", "850i"}


def schritt1_phantom_8er(conn, apply_):
    row = conn.execute("select modell, generation from baureihe where id=?",
                       (PHANTOM_BAUREIHE,)).fetchone()
    if row is None:
        log("  [1] Phantom-8er: bereits entfernt (idempotent)"); return
    if (row[0], row[1]) != ("8er", "E63/E64"):
        raise RuntimeError(f"[1] ABBRUCH: {PHANTOM_BAUREIHE} sieht anders aus als erwartet: {tuple(row)}")
    if conn.execute("select count(*) from baureihe where id=?", (PHANTOM_REAL,)).fetchone()[0] != 1:
        raise RuntimeError(f"[1] ABBRUCH: reale Baureihe {PHANTOM_REAL} fehlt — kein Abgleich moeglich")

    # Gegenprobe: gehoert wirklich nichts fachlich Eigenes dazu?
    real_specs = {(r[0], r[1], r[2]) for r in conn.execute(
        "select motorcode, hubraum_ccm, leistung_ps from motorvariante where baureihe_id=?",
        (PHANTOM_REAL,))}
    eigen = []
    for v, bez, code, hub, ps in conn.execute(
            "select variante_id,bezeichnung,motorcode,hubraum_ccm,leistung_ps "
            "from motorvariante where baureihe_id=?", (PHANTOM_BAUREIHE,)):
        if (code, hub, ps) not in real_specs:
            eigen.append(f"{bez} [{code}]")
    if eigen:
        raise RuntimeError("[1] ABBRUCH: Phantom-Baureihe enthaelt Motoren ohne Entsprechung "
                           f"in {PHANTOM_REAL}: {eigen} — manuelle Pruefung noetig")

    n = {t: conn.execute(f"select count(*) from {t} where baureihe_id=?",
                         (PHANTOM_BAUREIHE,)).fetchone()[0]
         for t in ("motorvariante", "schwachstelle_baureihe", "rueckruf", "ausstattungslinie", "quelle")}
    log(f"  [1] Phantom-8er {PHANTOM_BAUREIHE}: alle Motoren sind Spec-Dubletten von "
        f"{PHANTOM_REAL} -> Baureihe wird mit Anhang geloescht {n}")
    log("      (Rueckrufe werden NICHT auf den realen 6er migriert: sie widersprechen "
        "dessen eigenen Rueckrufen und sind wie der gesamte Bestand extern unbelegt — "
        "eine Migration wuerde unbelegte Behauptungen auf eine korrekte Baureihe tragen)")
    if apply_:
        conn.execute("delete from baureihe where id=?", (PHANTOM_BAUREIHE,))


# ─────────────────────────────────────────────────────────────────────────────
# Schritt 2 — W205 "C200 Plug-in-Hybrid"
#
# QUELLE: de.wikipedia.org/wiki/Mercedes-Benz_Baureihe_205 und
# auto-motor-und-sport.de (Technische Daten W205/S205/C205/A205, 2014-2021).
# Die W205-Plug-in-Hybride heissen C 350 e (2015-2018, 279 PS Systemleistung) und
# C 300 e (ab 2019). Ein "C200 Plug-in-Hybrid" existiert nicht.
#
# BEFUND: Leistung/Drehmoment/Hubraum der Zeile (211 PS / 155 kW / 350 Nm /
# 1991 ccm) sind identisch mit der ebenfalls vorhandenen Zeile "C250 Mild-Hybrid";
# 211 PS ist die Verbrennerleistung des M274, keine PHEV-Systemleistung. Die Zeile
# ist damit kein umbenannter realer Motor, sondern eine Kopie. Zeilen fuer
# C300 Plug-in-Hybrid und C350 Plug-in-Hybrid sind bereits vorhanden — es wird
# also nichts umbenannt (das erzeugte nur ein zweites Duplikat), sondern geloescht.
PHANTOM_MOTOR = "mercedes-benz-c-klasse-w205-c200-plug-in-hybrid"


def schritt2_w205_c200_phev(conn, apply_):
    row = conn.execute("select bezeichnung, leistung_ps, kraftstoff from motorvariante "
                       "where variante_id=?", (PHANTOM_MOTOR,)).fetchone()
    if row is None:
        log("  [2] W205 C200 PHEV: bereits entfernt (idempotent)"); return
    if (row[0], row[2]) != ("C200 Plug-in-Hybrid", "Plug-in-Hybrid"):
        raise RuntimeError(f"[2] ABBRUCH: unerwarteter Inhalt: {tuple(row)}")
    ersatz = [r[0] for r in conn.execute(
        "select bezeichnung from motorvariante where baureihe_id='mercedes-benz-c-klasse-w205' "
        "and lower(kraftstoff)='plug-in-hybrid' and variante_id<>?", (PHANTOM_MOTOR,))]
    if not ersatz:
        raise RuntimeError("[2] ABBRUCH: keine realen PHEV-Zeilen vorhanden — nicht loeschen")
    anhang = {t: conn.execute(f"select count(*) from {t} where variante_id=?",
                              (PHANTOM_MOTOR,)).fetchone()[0]
              for t in ("schwachstelle_motor", "kritische_wartung")}
    log(f"  [2] W205 '{row[0]}' ({row[1]} PS): existiert nicht -> loeschen. "
        f"Reale PHEV-Zeilen bleiben: {ersatz}. Anhang: {anhang}")
    if apply_:
        conn.execute("delete from motorvariante where variante_id=?", (PHANTOM_MOTOR,))


# ─────────────────────────────────────────────────────────────────────────────
# Schritt 3 — Toyota RAV4 II "2.0 VVT-i" / 1AZ-FSE / Diesel
#
# QUELLE: auto-data.net (Toyota RAV4 II XA20 2.0 16V D-4D 116 Hp 4WD) und
# autodoc.de/autoteileprofi.de Teilekatalog (RAV4 II CLA20_/CLA21_, 2.0 D 4WD,
# 116 PS / 85 kW, 1995 ccm, Motorcode 1CD-FTV, 05/2001-11/2005).
#
# ABWEICHUNG VOM AUDIT-BEFUND (bewusst): der Audit las den Fall als "Kraftstoff
# falsch". Die Daten tragen das nicht. Hubraum (1995 ccm), Leistung (116 PS /
# 85 kW), Antrieb und die abhaengigen Zeilen (Turbolader-Schwachstelle,
# Partikelfilter-Wartung) beschreiben eindeutig den 2.0 D-4D. FALSCH sind
# Bezeichnung und Motorcode, die vom Benziner uebernommen wurden. Korrigiert wird
# deshalb genau das — der Kraftstoff "Diesel" bleibt, weil er stimmt.
RAV4_ID = "toyota-rav4-ii-2-0-vvt-i"


def schritt3_rav4(conn, apply_):
    row = conn.execute("select bezeichnung,motorcode,kraftstoff,hubraum_ccm,leistung_ps,leistung_kw "
                       "from motorvariante where variante_id=?", (RAV4_ID,)).fetchone()
    if row is None:
        raise RuntimeError(f"[3] ABBRUCH: {RAV4_ID} nicht gefunden")
    bez, code, kr, hub, ps, kw = row
    if (bez, code) == ("2.0 D-4D", "1CD-FTV"):
        log("  [3] RAV4 II: bereits korrigiert (idempotent)"); return
    if (kr, hub, ps, kw) != ("Diesel", 1995, 116, 85):
        raise RuntimeError(f"[3] ABBRUCH: Zeile passt nicht zum verifizierten 1CD-FTV: {tuple(row)}")
    if (bez, code) != ("2.0 VVT-i", "1AZ-FSE"):
        raise RuntimeError(f"[3] ABBRUCH: unerwartete Ausgangswerte: {bez!r}/{code!r}")
    log(f"  [3] RAV4 II: bezeichnung {bez!r} -> '2.0 D-4D', motorcode {code!r} -> '1CD-FTV' "
        f"(Diesel/1995ccm/116PS/85kW bleiben — sie belegen den 1CD-FTV)")
    if apply_:
        conn.execute("update motorvariante set bezeichnung='2.0 D-4D', motorcode='1CD-FTV' "
                     "where variante_id=?", (RAV4_ID,))


# ─────────────────────────────────────────────────────────────────────────────
# Schritt 4 — Opel Insignia B Motorcode "F20DTH"
#
# QUELLEN: motordirekt.de fuehrt den Motor "F20DVH" ausdruecklich fuer den
# Insignia B Grand Sport (Z18) und Sports Tourer (Z18) 2.0 CDTi 174 PS; der
# autodoc.de-Teilekatalog listet ihn fuer "Insignia B Sports Tourer 2.0 CDTi
# 174 PS Diesel 128 kW 2020-2026" ebenso wie fair-motors.de; das Insignia-B-Forum
# fuehrt einen eigenen Thread "Unterschied 2.0 Diesel alt 170 PS (D20DTH) und neu
# 174 PS (F20DVH)". Leistung (128 kW / 174 PS) und Drehmoment (380 Nm) decken sich
# mit adac.de und auto-data.net zum Facelift-2.0d.
#
# Die "F"-Kennung ist bei Opel keine Erfindung, sondern die Familie der
# PSA-basierten Diesel — der Bestand fuehrt sie korrekt auch bei Astra K
# (F15DVC/F15DVH) und Mokka B (F15DVH). Der DB-Wert "F20DTH" ist damit eine
# Verschreibung des realen "F20DVH" (T statt V), kein voellig freies Phantasiewort.
#
# ENTSCHEIDUNG nach Auftrag §4: die Zuordnung IST eindeutig belegt (vier
# unabhaengige Fundstellen, konsistent in Leistung, Drehmoment, Bauzeit und
# Karosserievarianten) -> der Code wird KORRIGIERT statt entfernt.
#
# HINWEIS zur Historie dieses Schrittes: eine erste Fassung dieses Skripts hat den
# Wert nur geleert, weil die damalige Recherche B20DTH/D20DTH/F20DVH
# nebeneinander fand und keine Zuordnung trug. Die gezielte Nachrecherche hat das
# aufgeloest. Deshalb behandelt der Schritt BEIDE Ausgangszustaende (alter
# Falschwert und bereits geleertes Feld) und bleibt idempotent.
INSIGNIA_ALT = "F20DTH"
INSIGNIA_NEU = "F20DVH"


def schritt4_insignia(conn, apply_):
    rows = conn.execute(
        "select variante_id,bezeichnung,motorcode,leistung_ps,kraftstoff from motorvariante "
        "where baureihe_id='opel-insignia-b' and leistung_ps=174 and lower(kraftstoff)='diesel'"
    ).fetchall()
    if len(rows) != 2:
        raise RuntimeError(f"[4] ABBRUCH: erwartet 2 Insignia-174-PS-Dieselzeilen, gefunden {len(rows)}")
    offen = [r for r in rows if (r[2] or "") != INSIGNIA_NEU]
    if not offen:
        log(f"  [4] Insignia: Motorcode bereits {INSIGNIA_NEU} (idempotent)"); return
    for vid, bez, code, ps, _kr in offen:
        if (code or "") not in (INSIGNIA_ALT, ""):
            raise RuntimeError(f"[4] ABBRUCH: unerwarteter Ausgangscode {code!r} bei {bez!r}")
        log(f"  [4] Insignia {bez!r} ({ps} PS): motorcode {code!r} -> {INSIGNIA_NEU!r} "
            f"(belegt durch motordirekt.de/autodoc.de/fair-motors.de + Insignia-B-Forum)")
    if apply_:
        conn.executemany("update motorvariante set motorcode=? where variante_id=?",
                         [(INSIGNIA_NEU, r[0]) for r in offen])


# ─────────────────────────────────────────────────────────────────────────────
# Schritt 5 — Zahnriemen-Wartungseintraege auf KETTEN-Motoren
#
# QUELLEN je Motorfamilie:
#   BMW M10  — bmw-02-club.de / e30-talk.com / autosmotor.de: Kettenantrieb.
#   BMW S14  — en.wikipedia.org/wiki/BMW_S14, Teilekataloge ECS/Pelican fuehren
#              Steuerketten und Kettenspanner fuer den E30 M3.
#   BMW M30  — en.wikipedia.org/wiki/BMW_M30 und Bavarian Autosport: SOHC mit
#              Kettenantrieb ("Big Six"). E23-Modelle 728i/730/732i/733i tragen
#              den M30, der 745i den turboaufgeladenen M30-Ableger M102;
#              FEBI/mecatechnic fuehren fuer den E23 Steuerketten.
#   VAG EA888 (CDAA 1.8 TSI, CCZA 2.0 TSI) — carwiki.de/vw-ea888-motor und
#              kfz-dietrich.com: Nockenwellenantrieb ueber Steuerkette, im
#              Unterschied zum riemengetriebenen EA113.
#   Toyota 2AZ-FE — Teilekataloge fuehren Steuerkettensaetze; der Motor ist
#              kettengetrieben.
#
# ES WIRD KEIN ERSATZINTERVALL ERFUNDEN (§5). Die falschen Zeilen werden nur
# entfernt. Motoren derselben Baureihen mit ECHTEM Zahnriemen (E30 M20/M21)
# bleiben ausdruecklich unangetastet.
KETTEN_ZEILEN = [
    (569, "bmw-3er-e30", "316i", "M10B16"), (570, "bmw-3er-e30", "318i", "M10B18"),
    (573, "bmw-3er-e30", "M3", "S14B23"),
    (692, "bmw-7er-e23", "728i", ""), (693, "bmw-7er-e23", "730", ""),
    (694, "bmw-7er-e23", "732i", ""), (695, "bmw-7er-e23", "733i", ""),
    (696, "bmw-7er-e23", "745i", ""), (697, "bmw-7er-e23", "745iA", ""),
    (698, "bmw-7er-e23", "749", ""),
    (699, "bmw-7er-e32", "730i", "M30B30"), (700, "bmw-7er-e32", "735i", "M30B35"),
    (1443, "skoda-superb-zweite-generation", "1.8 TSI", "CDAA"),
    (1444, "skoda-superb-zweite-generation", "2.0 TSI", "CCZA"),
    (1476, "seat-leon-zweite-generation", "1.8 TSI", "CDAA"),
    (1548, "toyota-camry-xv30", "2.4 VVT-i", "2AZ-FE"),
]


def schritt5_zahnriemen(conn, apply_):
    getroffen, offen = [], []
    for wid, bid, bez, code in KETTEN_ZEILEN:
        row = conn.execute(
            "select w.bauteil, m.bezeichnung, m.motorcode, m.baureihe_id, w.intervall "
            "from kritische_wartung w join motorvariante m on m.variante_id=w.variante_id "
            "where w.id=?", (wid,)).fetchone()
        if row is None:
            continue                       # bereits entfernt -> idempotent
        bauteil, m_bez, m_code, m_bid, intervall = row
        if "zahnriemen" not in (bauteil or "").lower():
            offen.append(f"#{wid}: bauteil={bauteil!r} ist kein Zahnriemen — uebersprungen")
            continue
        if (m_bid, m_bez, m_code or "") != (bid, bez, code):
            offen.append(f"#{wid}: erwartet {bid}/{bez}/{code!r}, gefunden "
                         f"{m_bid}/{m_bez}/{m_code!r} — uebersprungen")
            continue
        getroffen.append((wid, bid, bez, code, intervall))
    for wid, bid, bez, code, intervall in getroffen:
        log(f"  [5] #{wid:5d} {bid:32s} {bez:12s} [{code or '-':8s}] "
            f"Zahnriemen {intervall!r} -> geloescht (Kettenmotor)")
    if offen:
        for z in offen:
            log(f"  [5] OFFEN {z}")
    log(f"  [5] {len(getroffen)} von {len(KETTEN_ZEILEN)} Zeilen betroffen "
        f"({len(KETTEN_ZEILEN) - len(getroffen) - len(offen)} bereits entfernt)")
    if apply_ and getroffen:
        conn.executemany("delete from kritische_wartung where id=?",
                         [(w[0],) for w in getroffen])


# ─────────────────────────────────────────────────────────────────────────────
# Schritt 6/7 — Duplikat-Baureihen zusammenfuehren
#
# Ein reales Fahrzeug darf nicht abhaengig vom Match-Score in zwei
# widerspruechlichen Datenwelten landen.
#
# BMW 3er: kanonisch bmw-3er-g20-g21. Begruendung: dorthin loest der Kaufcheck
#   heute auf (11 gespeicherte Checks referenzieren diese ID, 0 die andere), die
#   Generation "G20/G21" ist korrekt fuer Limousine UND Touring, und nur diese
#   Zeile fuehrt chassis_codes, die der Marktvergleich zur Karosserietrennung
#   braucht (app/chassis_codes.py).
# BMW 1er: kanonisch bmw-1er-f20-f21. Hier wird bewusst GEGEN die aktuelle
#   Aufloesung entschieden: bmw-1er-f2x fuehrt die Karosserien als "Limousine
#   (5-Tuerer)" und "Coupe (3-Tuerer)" — der F20/F21 ist ein Schraegheck, ein
#   Coupe gab es in dieser Reihe nicht (das war der 2er F22). Die kanonische
#   Zeile hat die korrekten Karosserien, die korrekten Werkscodes und die
#   motorgenau gescopten Steuerketten-Schwachstellen (N47 Diesel / N20 Benzin),
#   die das Runtime-Gate ueberhaupt erst trennen kann.
#
# RUECKRUFE werden NICHT zusammengefuehrt (Auftrag §7): die beiden Saetze
# widersprechen einander (z.B. G20 009695 "Bremskraftunterstuetzung" gegen
# 009696 "Bremskraftverstaerker"), keiner ist extern belegt. Ein Merge wuerde die
# Zahl unbelegter Behauptungen verdoppeln statt sie zu klaeren.
MERGES = [("bmw-3er-g20-g21", "bmw-3er-g20"), ("bmw-1er-f20-f21", "bmw-1er-f2x")]


def _merge(conn, kanon, alt, apply_):
    if conn.execute("select count(*) from baureihe where id=?", (alt,)).fetchone()[0] == 0:
        log(f"  [6] {alt}: bereits aufgeloest (idempotent)"); return
    if conn.execute("select count(*) from baureihe where id=?", (kanon,)).fetchone()[0] != 1:
        raise RuntimeError(f"[6] ABBRUCH: kanonische Baureihe {kanon} fehlt")

    kan_mot = {norm(b): v for v, b in conn.execute(
        "select variante_id,bezeichnung from motorvariante where baureihe_id=?", (kanon,))}
    alt_mot = list(conn.execute(
        "select variante_id,bezeichnung from motorvariante where baureihe_id=?", (alt,)))

    umzug, gemeinsam = [], []
    for vid, bez in alt_mot:
        (gemeinsam if norm(bez) in kan_mot else umzug).append((vid, bez))

    log(f"  [6] {alt} -> {kanon}")
    log(f"      Motorvarianten umziehen ({len(umzug)}): " +
        ", ".join(b for _, b in umzug) if umzug else "      Motorvarianten umziehen (0)")
    if apply_:
        for vid, _bez in umzug:
            conn.execute("update motorvariante set baureihe_id=? where variante_id=?", (kanon, vid))

    # Abhaengige Zeilen der GEMEINSAMEN Motoren auf den kanonischen Motor umhaengen,
    # sofern inhaltlich noch nicht abgedeckt.
    for tab in ("schwachstelle_motor", "kritische_wartung"):
        for vid, bez in gemeinsam:
            ziel = kan_mot[norm(bez)]
            vorhanden = [r[0] for r in conn.execute(
                f"select bauteil from {tab} where variante_id=?", (ziel,))]
            for rid, bauteil in conn.execute(
                    f"select id,bauteil from {tab} where variante_id=?", (vid,)):
                if ueberschneidet(bauteil, vorhanden):
                    log(f"      {tab} #{rid} {bauteil!r} @ {bez}: verworfen "
                        f"(kanonisch bereits abgedeckt)")
                else:
                    log(f"      {tab} #{rid} {bauteil!r} @ {bez}: umgehaengt")
                    vorhanden.append(bauteil)
                    if apply_:
                        conn.execute(f"update {tab} set variante_id=? where id=?", (ziel, rid))

    # Baureihen-Schwachstellen und Ausstattungslinien
    for tab, feld in (("schwachstelle_baureihe", "bauteil"), ("ausstattungslinie", "name")):
        vorhanden = [r[0] for r in conn.execute(
            f"select {feld} from {tab} where baureihe_id=?", (kanon,))]
        for rid, wert in conn.execute(
                f"select id,{feld} from {tab} where baureihe_id=?", (alt,)):
            if ueberschneidet(wert, vorhanden):
                log(f"      {tab} #{rid} {wert!r}: verworfen (kanonisch bereits abgedeckt)")
            else:
                log(f"      {tab} #{rid} {wert!r}: umgehaengt")
                vorhanden.append(wert)
                if apply_:
                    conn.execute(f"update {tab} set baureihe_id=? where id=?", (kanon, rid))

    # Vorgaenger: eine gueltige Baureihen-ID schlaegt einen Freitext-Platzhalter.
    kv = conn.execute("select vorgaenger from baureihe where id=?", (kanon,)).fetchone()[0]
    av = conn.execute("select vorgaenger from baureihe where id=?", (alt,)).fetchone()[0]
    kv_ok = bool(conn.execute("select 1 from baureihe where id=?", (kv or "",)).fetchone())
    av_ok = bool(conn.execute("select 1 from baureihe where id=?", (av or "",)).fetchone())
    if not kv_ok and av_ok:
        log(f"      vorgaenger {kv!r} (ungueltig) -> {av!r} (gueltige Baureihen-ID)")
        if apply_:
            conn.execute("update baureihe set vorgaenger=? where id=?", (av, kanon))
    elif not kv_ok:
        log(f"      vorgaenger {kv!r} bleibt ungueltig — beide Kandidaten sind Freitext (OFFEN)")

    rest = {t: conn.execute(f"select count(*) from {t} where baureihe_id=?", (alt,)).fetchone()[0]
            for t in ("motorvariante", "schwachstelle_baureihe", "rueckruf",
                      "ausstattungslinie", "quelle")}
    log(f"      {alt} wird geloescht; verbleibender Anhang faellt per CASCADE weg: {rest}")
    if apply_:
        conn.execute("delete from baureihe where id=?", (alt,))


def schritt67_duplikate(conn, apply_):
    for kanon, alt in MERGES:
        _merge(conn, kanon, alt, apply_)


# ─────────────────────────────────────────────────────────────────────────────
def pruefe_integritaet(conn):
    """integrity_check + foreign_key_check + Waisenzaehlung.

    Der foreign_key_check wird auf die FAHRZEUGTABELLEN eingegrenzt. Grund: die
    Live-DB traegt eine VORBESTEHENDE, sachfremde Verletzung — eine Zeile in
    `einwilligung` verweist auf einen geloeschten `users`-Eintrag. Sie hat mit den
    Fahrzeugdaten nichts zu tun, ist nicht Gegenstand dieses Cleanups und wird
    hier deshalb weder repariert noch als Blocker behandelt; sie wird aber
    ausgewiesen, damit sie nicht unbemerkt bleibt.
    """
    fehler = []
    ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if ic != "ok":
        fehler.append(f"integrity_check: {ic}")
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    eigene = [r for r in fk if r[0] in TABELLEN]
    fremde = [r for r in fk if r[0] not in TABELLEN]
    if fremde:
        log(f"    (vorbestehend, ausserhalb dieses Cleanups: {len(fremde)} FK-Verletzung(en) "
            f"in {sorted({r[0] for r in fremde})} — unveraendert gelassen)")
    if eigene:
        fehler.append(f"foreign_key_check (Fahrzeugtabellen): {eigene[:5]}")
    waisen = {
        "motorvariante ohne baureihe": "select count(*) from motorvariante m "
            "where not exists(select 1 from baureihe b where b.id=m.baureihe_id)",
        "schwachstelle_baureihe ohne baureihe": "select count(*) from schwachstelle_baureihe s "
            "where not exists(select 1 from baureihe b where b.id=s.baureihe_id)",
        "rueckruf ohne baureihe": "select count(*) from rueckruf r "
            "where not exists(select 1 from baureihe b where b.id=r.baureihe_id)",
        "ausstattungslinie ohne baureihe": "select count(*) from ausstattungslinie a "
            "where not exists(select 1 from baureihe b where b.id=a.baureihe_id)",
        "schwachstelle_motor ohne motorvariante": "select count(*) from schwachstelle_motor s "
            "where not exists(select 1 from motorvariante m where m.variante_id=s.variante_id)",
        "kritische_wartung ohne motorvariante": "select count(*) from kritische_wartung w "
            "where not exists(select 1 from motorvariante m where m.variante_id=w.variante_id)",
    }
    for name, sql in waisen.items():
        n = conn.execute(sql).fetchone()[0]
        log(f"    {name}: {n}")
        if n:
            fehler.append(f"{name}={n}")
    return fehler


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="Aenderungen wirklich schreiben")
    p.add_argument("--db", default=DB)
    args = p.parse_args()

    log(f"DB: {args.db}")
    log(f"MODUS: {'APPLY (schreibend)' if args.apply else 'TROCKENLAUF (nur Bericht)'}")
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON")
    vorher = zaehle(conn)
    log("\nZeilen VORHER: " + ", ".join(f"{k}={v}" for k, v in vorher.items()))
    log("\n-- Integritaet VORHER --")
    if pruefe_integritaet(conn):
        log("!! DB ist schon vor dem Cleanup nicht integer — Abbruch")
        return 2

    log("\n-- Korrekturen --")
    try:
        conn.execute("BEGIN")
        schritt1_phantom_8er(conn, args.apply)
        schritt2_w205_c200_phev(conn, args.apply)
        schritt3_rav4(conn, args.apply)
        schritt4_insignia(conn, args.apply)
        schritt5_zahnriemen(conn, args.apply)
        schritt67_duplikate(conn, args.apply)
        log("\n-- Integritaet NACHHER (noch in der Transaktion) --")
        fehler = pruefe_integritaet(conn)
        if fehler:
            raise RuntimeError("Integritaetsverletzung: " + "; ".join(fehler))
        nachher = zaehle(conn)
        log("\nZeilen NACHHER: " + ", ".join(f"{k}={v}" for k, v in nachher.items()))
        log("DIFF:           " + ", ".join(
            f"{k}={nachher[k] - vorher[k]:+d}" for k in vorher if nachher[k] != vorher[k]) or "keine")
        if args.apply:
            conn.commit()
            log("\nCOMMIT ausgefuehrt.")
        else:
            conn.rollback()
            log("\nTrockenlauf — ROLLBACK, nichts geschrieben.")
    except Exception as exc:
        conn.rollback()
        log(f"\n!! FEHLER: {exc}\n!! ROLLBACK — die Datenbank ist unveraendert.")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())