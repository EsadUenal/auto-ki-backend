from __future__ import annotations

"""
Versionierte DATENmigrationen der Fahrzeugdatenbank.

WARUM DIESES MODUL EXISTIERT
----------------------------
Der DATA-TRUTH-AUDIT hat konkrete, extern verifizierte Falschdaten belegt. Sie
wurden zunaechst mit einem Einmal-Skript in der lokalen Live-DB korrigiert. Das
GIT-/DB-LIFECYCLE-AUDIT hat danach gezeigt, dass das nicht reicht:

  * `db/auto_ki.db` steht in `.gitignore` — der Fahrzeugdatenbestand liegt NICHT
    im Repository.
  * `.dockerignore` schliesst `db/` und `*.db` aus — er liegt auch NICHT im Image.
  * `db/seed_data.py` seedet lediglich zwei Demo-Baureihen (BMW M4 F82/G82).
  * Produktion laeuft auf `/data/auto_ki.db` in einem persistenten Volume, das
    einmalig manuell befuellt wurde.

Ein Einmal-Skript auf einem Entwicklerrechner erreicht dieses Volume nie. Ohne
dieses Modul haette jede andere Umgebung die falschen Daten behalten.

WIE ES FUNKTIONIERT
-------------------
Die Korrekturen laufen als versionierte Migration ueber die BEREITS VORHANDENE
Architektur: die Tabelle `schema_migrations` mit einem Namensmarker, genau wie
`ersatzteil_quota_backfill` und der `chassis_codes`-Seed. `ensure_tables()` ruft
`run_data_migrations()` einmal beim App-Start auf — nicht pro Request.

Eigenschaften:
  * IDEMPOTENT  — jeder Schritt prueft seinen Zielzustand und meldet
                  "bereits korrigiert", statt erneut zu schreiben. Der Marker
                  verhindert zusaetzlich jeden zweiten Lauf.
  * TRANSAKTIONAL — alle Schritte einer Migration laufen in EINER Transaktion.
                  Integritaetspruefung erfolgt NOCH IN der Transaktion; schlaegt
                  sie fehl, wird zurueckgerollt und der Marker NICHT gesetzt.
  * PRECONDITIONS — jeder Schritt verlangt den exakt erwarteten Ausgangszustand
                  und bricht bei Abweichung ab, statt zu raten.
  * NICHT FATAL  — bricht eine Migration ab, wird das laut geloggt, die DB bleibt
                  unveraendert und die App startet trotzdem. Ein Datenbefund darf
                  keinen Serverstart verhindern; der fehlende Marker sorgt dafuer,
                  dass der naechste Start es erneut versucht.
  * KEIN LOESCHEN VON NUTZERDATEN — angefasst werden ausschliesslich die
                  Fahrzeugtabellen.

Auf einer frischen, leeren Datenbank sind alle Schritte wirkungslos (es gibt
nichts zu korrigieren); der Marker wird trotzdem gesetzt.

QUELLENNACHWEIS je Korrektur steht am jeweiligen Schritt.
"""

import logging
import sqlite3

log_ = logging.getLogger(__name__)

TABELLEN = ("baureihe", "motorvariante", "schwachstelle_baureihe", "schwachstelle_motor",
            "kritische_wartung", "rueckruf", "ausstattungslinie", "quelle")

# Protokollpuffer: das CLI-Skript liest ihn aus, der App-Start nutzt den Logger.
protokoll: list[str] = []


def log(zeile: str) -> None:
    protokoll.append(zeile)
    log_.info("%s", zeile.strip())


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
        # Leere/frische Datenbank: es gibt nichts zu korrigieren. Kein Abbruch —
        # sonst koennte die Migration auf einer frisch angelegten DB nie
        # abschliessen und wuerde bei jedem Start erneut scheitern.
        log("  [3] RAV4 II: Zeile nicht vorhanden (leere DB) — nichts zu tun")
        return
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
    if not rows:
        log("  [4] Insignia: keine 174-PS-Dieselzeilen vorhanden (leere DB) — nichts zu tun")
        return
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


_MOTOR_IDENT = "kraftstoff,leistung_ps,hubraum_ccm,motorcode"


def _spec(row):
    """Technische Identitaet OHNE Motorcode — Kraftstoff, Leistung, Hubraum.

    Der Motorcode bleibt bewusst aussen vor: dieselbe Maschine wird in der DB
    unterschiedlich genau notiert ("M178" vs. "M178 DE40 AL"). Als alleiniges
    Merkmal waere die Spec zu grob (mehrere Varianten einer Baureihe teilen sie
    sich), deshalb wirkt sie nur ZUSAMMEN mit dem Namensvergleich (siehe
    `_namens_kandidat`).
    """
    kraft, ps, hub, _code = row
    return (str(kraft or "").strip().lower(), ps, hub)


def _namens_tokens(bez):
    """Alle Tokens eines Motornamens — ohne Laengenfilter.

    `tokens()` verwirft alles unter vier Zeichen; fuer Motornamen waere davon
    nichts uebrig ("GT R", "745i"). Hier zaehlt jedes Token.
    """
    return {t for t in "".join(ch if ch.isalnum() else " " for ch in (bez or "").lower()).split()}


def _namens_kandidat(bez, spec, kandidaten):
    """Bester kanonischer Motor fuer `bez`/`spec` — oder None.

    Zwei Bedingungen muessen gemeinsam gelten, sonst wird nicht zusammengefuehrt:
      1. gleiche technische Spec (Kraftstoff, Leistung, Hubraum), und
      2. die Namens-Tokens stehen in einer Teilmengenbeziehung.

    Warum beides: die AMG-GT-Dublette fuehrt "AMG GT R" fuer den kanonischen
    "GT R" — nur der Name unterscheidet sich um das Markenkuerzel. Ein reiner
    Namensvergleich haette den Motor doppelt angelegt. Eine reine Spec-Pruefung
    waere umgekehrt zu grob: "GT R" und "GT R PRO" teilen sich Kraftstoff,
    Leistung und Hubraum vollstaendig.

    Bei mehreren Treffern gewinnt der Name mit der KLEINSTEN symmetrischen
    Differenz — so landet "AMG GT R PRO" bei "GT R PRO" und nicht bei "GT R".
    """
    tok = _namens_tokens(bez)
    treffer = []
    for k_bez, k_spec, k_vid in kandidaten:
        if k_spec != spec:
            continue
        k_tok = _namens_tokens(k_bez)
        if tok <= k_tok or k_tok <= tok:
            treffer.append((len(tok ^ k_tok), k_bez, k_vid))
    if not treffer:
        return None
    treffer.sort()
    return treffer[0][2]


def _name_abgedeckt(wert, vorhandene):
    """True, wenn `wert` als Name bereits durch einen vorhandenen Namen abgedeckt ist.

    Teilmengenbeziehung ueber ALLE Tokens, ohne Laengenfilter: "GT R Pro" ist
    durch "AMG GT R Pro" abgedeckt (und umgekehrt), "GT R" aber nicht durch
    "AMG GT Black Series".
    """
    t = _namens_tokens(wert)
    if not t:
        return False
    return any(t <= _namens_tokens(v) or _namens_tokens(v) <= t for v in vorhandene)


def _merge(conn, kanon, alt, apply_, tag="6"):
    if conn.execute("select count(*) from baureihe where id=?", (alt,)).fetchone()[0] == 0:
        log(f"  [{tag}] {alt}: bereits aufgeloest (idempotent)"); return
    if conn.execute("select count(*) from baureihe where id=?", (kanon,)).fetchone()[0] != 1:
        raise RuntimeError(f"[{tag}] ABBRUCH: kanonische Baureihe {kanon} fehlt")

    # Ein Motor der aufzuloesenden Baureihe gilt als bereits vorhanden, wenn ENTWEDER
    # sein Name ODER seine technische Identitaet mit einem kanonischen Motor
    # uebereinstimmt. Der Namensvergleich allein reicht nicht: die AMG-GT-Dublette
    # fuehrt "AMG GT R"/"AMG GT R PRO" fuer exakt dieselben Motoren, die kanonisch
    # "GT R"/"GT R PRO" heissen. Nach reinem Namensvergleich waeren sie umgezogen —
    # die kanonische Baureihe haette danach jeden dieser Motoren doppelt gefuehrt.
    kan_mot, kandidaten = {}, []
    for vid, bez, *rest in conn.execute(
            f"select variante_id,bezeichnung,{_MOTOR_IDENT} from motorvariante "
            "where baureihe_id=?", (kanon,)):
        kan_mot[norm(bez)] = vid
        kandidaten.append((bez, _spec(rest), vid))
    alt_mot = [(vid, bez, _spec(rest)) for vid, bez, *rest in conn.execute(
        f"select variante_id,bezeichnung,{_MOTOR_IDENT} from motorvariante "
        "where baureihe_id=?", (alt,))]

    umzug, gemeinsam, belegt = [], [], set()
    for vid, bez, spec in alt_mot:
        ziel = kan_mot.get(norm(bez))
        if ziel is None:
            frei = [k for k in kandidaten if k[2] not in belegt]
            ziel = _namens_kandidat(bez, spec, frei)
        if ziel:
            belegt.add(ziel)
            gemeinsam.append((vid, bez, ziel))
        else:
            umzug.append((vid, bez))

    log(f"  [{tag}] {alt} -> {kanon}")
    log(f"      Motorvarianten umziehen ({len(umzug)}): " +
        ", ".join(b for _, b in umzug) if umzug else "      Motorvarianten umziehen (0)")
    if apply_:
        for vid, _bez in umzug:
            conn.execute("update motorvariante set baureihe_id=? where variante_id=?", (kanon, vid))

    # Abhaengige Zeilen der GEMEINSAMEN Motoren auf den kanonischen Motor umhaengen,
    # sofern inhaltlich noch nicht abgedeckt.
    for tab in ("schwachstelle_motor", "kritische_wartung"):
        for vid, bez, ziel in gemeinsam:
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
    #
    # ZWEI Abdeckungsregeln, weil die Felder verschieden funktionieren:
    #
    #   bauteil (Schwachstelle) -> `ueberschneidet`: ein gemeinsames
    #       bedeutungstragendes Token (>=4 Zeichen) genuegt. Kurze Fuellwoerter
    #       muessen hier ausgeschlossen bleiben, sonst kollidiert jedes Bauteil
    #       mit jedem.
    #
    #   name (Ausstattungslinie) -> `_name_abgedeckt`: Teilmengenbeziehung ueber
    #       ALLE Tokens. Ausstattungsnamen bestehen fast nur aus kurzen Kuerzeln
    #       ("GT R Pro"), von denen der >=4-Filter nichts uebrig laesst. Genau
    #       daran ist der erste AMG-GT-Merge gescheitert: er hat "GT", "GT S",
    #       "GT C", "GT R" und "GT R Pro" zusaetzlich neben die bereits
    #       vorhandenen "AMG GT", "AMG GT S" usw. gehaengt und die Linienliste
    #       der kanonischen Baureihe von 6 auf 11 Eintraege aufgeblaeht.
    for tab, feld, abgedeckt in (("schwachstelle_baureihe", "bauteil", ueberschneidet),
                                 ("ausstattungslinie", "name", _name_abgedeckt)):
        vorhanden = [r[0] for r in conn.execute(
            f"select {feld} from {tab} where baureihe_id=?", (kanon,))]
        for rid, wert in conn.execute(
                f"select id,{feld} from {tab} where baureihe_id=?", (alt,)):
            if abgedeckt(wert, vorhanden):
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

# ─────────────────────────────────────────────────────────────────────────────
# Schritt 7 — BMW E23 "749" ist ein falsch benannter 735i
#
# QUELLE: de.wikipedia.org/wiki/BMW_E23 und en.wikipedia.org/wiki/BMW_7_Series_(E23),
# gestuetzt durch ultimatespecs.com (BMW E23 7 Series 735i). Belegt: die E23-Palette
# umfasst 728/728i, 730/732i, 733i, 735i und 745i — ein Modell "749" hat es nie
# gegeben. Der 735i traegt den M30B34 (Variante M30B35M) mit 3430 ccm und
# 218 PS/160 kW, gebaut 1981/82-1986.
#
# Die DB-Zeile "749" fuehrt exakt diese Werte: 3430 ccm, 6 Zylinder, 218 PS,
# 160 kW. Sie ist also kein Phantom, sondern der real existierende 735i unter
# falschem Namen — und der 735i fehlt in der Baureihe sonst vollstaendig.
# Deshalb wird umbenannt statt geloescht (Auftrag §3: "sauber dem richtigen
# Motor zuordnen"). Der Motorcode bleibt leer wie bei allen uebrigen E23-Zeilen;
# er wird hier NICHT ergaenzt, weil das eine neue Behauptung waere statt einer
# Korrektur.
E23_ALT, E23_NEU = "bmw-7er-e23-749", "bmw-7er-e23-735i"


def schritt7_e23_749(conn, apply_):
    zeile = conn.execute(
        "select bezeichnung,hubraum_ccm,zylinder,leistung_ps from motorvariante "
        "where variante_id=?", (E23_ALT,)).fetchone()
    if zeile is None:
        log("  [7] E23 '749': bereits korrigiert (idempotent)")
        return
    if tuple(zeile) != ("749", 3430, 6, 218):
        raise RuntimeError(f"[7] ABBRUCH: E23-'749'-Zeile hat unerwartete Werte {tuple(zeile)!r} "
                           f"— erwartet ('749', 3430, 6, 218)")
    if conn.execute("select count(*) from motorvariante where baureihe_id='bmw-7er-e23' "
                    "and bezeichnung='735i'").fetchone()[0]:
        raise RuntimeError("[7] ABBRUCH: es existiert bereits ein E23-735i — Umbenennung wuerde "
                           "eine Dublette erzeugen")
    if conn.execute("select count(*) from motorvariante where variante_id=?",
                    (E23_NEU,)).fetchone()[0]:
        raise RuntimeError(f"[7] ABBRUCH: Ziel-ID {E23_NEU} ist bereits vergeben")
    anhang = {t: conn.execute(f"select count(*) from {t} where variante_id=?",
                              (E23_ALT,)).fetchone()[0]
              for t in ("schwachstelle_motor", "kritische_wartung")}
    log(f"  [7] E23 '749' -> '735i' (3430 ccm, 218 PS, M30B34); Anhang wandert mit: {anhang}")
    if apply_:
        # Ein Primaerschluessel wird umgeschrieben, an dem Kindzeilen haengen. In
        # welcher Reihenfolge auch immer man vorgeht, es gibt einen Moment, in dem
        # Eltern- und Kindzeile auseinanderfallen — SQLite wuerde sofort abbrechen.
        # `defer_foreign_keys` verschiebt die Pruefung ans TRANSAKTIONSENDE: die
        # Fremdschluessel werden also weiterhin vollstaendig geprueft, nur nicht
        # nach jedem einzelnen Statement. Der Pragma-Wert gilt ausschliesslich bis
        # zum naechsten COMMIT/ROLLBACK.
        conn.execute("PRAGMA defer_foreign_keys=ON")
        conn.execute("update motorvariante set variante_id=?, bezeichnung='735i' "
                     "where variante_id=?", (E23_NEU, E23_ALT))
        for t in ("schwachstelle_motor", "kritische_wartung"):
            conn.execute(f"update {t} set variante_id=? where variante_id=?", (E23_NEU, E23_ALT))


# ─────────────────────────────────────────────────────────────────────────────
# Schritt 8 — BMW E23 745i: Zylinderzahl
#
# QUELLE: en.wikipedia.org/wiki/BMW_7_Series_(E23) und de.wikipedia.org/wiki/BMW_E23.
# Belegt: der 745i ist ein AUFGELADENER REIHENSECHSZYLINDER — bis 1982 M102
# (3.2 l), danach M106 (3.4 l) — mit durchgehend 252 PS/185 kW. Einen
# Achtzylinder hat BMW im E23 nie angeboten.
#
# Korrigiert wird ausschliesslich `zylinder` (8 -> 6). Hubraum (3210 ccm),
# Leistung und Fahrleistungen entsprechen bereits dem M102 und bleiben
# unveraendert. Der Motorcode bleibt leer wie bei allen uebrigen E23-Zeilen: die
# Zeile deckt beide Motorgenerationen ab, ein einzelner Code waere fuer die
# Haelfte der Bauzeit falsch.
def schritt8_e23_745i_zylinder(conn, apply_):
    zeilen = list(conn.execute(
        "select variante_id,bezeichnung,zylinder,hubraum_ccm,leistung_ps from motorvariante "
        "where baureihe_id='bmw-7er-e23' and bezeichnung like '745i%'"))
    offen = [z for z in zeilen if z[2] == 8]
    if not offen:
        log("  [8] E23 745i: Zylinderzahl bereits korrekt (idempotent)")
        return
    for vid, bez, zyl, hub, ps in offen:
        if (hub, ps) != (3210, 252):
            raise RuntimeError(f"[8] ABBRUCH: {bez} hat unerwartete Werte "
                               f"(hubraum={hub}, ps={ps}) — erwartet (3210, 252)")
        log(f"  [8] {bez}: zylinder {zyl} -> 6 (aufgeladener Reihensechszylinder M102/M106)")
        if apply_:
            conn.execute("update motorvariante set zylinder=6 where variante_id=?", (vid,))


# ─────────────────────────────────────────────────────────────────────────────
# Schritt 9 — BMW E23 "745iA" ist keine eigene Motorvariante
#
# QUELLE: dieselbe wie Schritt 8. Der Zusatz "A" bezeichnet bei BMW dieser Zeit
# die Automatikausfuehrung, keine eigene Motorisierung — der E23 745i wurde
# ohnehin ausschliesslich mit Automatikgetriebe gebaut.
#
# Die DB fuehrt beide Zeilen mit IDENTISCHEN Werten in Hubraum, Zylinderzahl,
# Leistung, Drehmoment, Vmax und Beschleunigung. Entfernt wird deshalb die
# Getriebe-Dublette; die kanonische Zeile "745i" bleibt vollstaendig erhalten.
# Preconditions verlangen echte Wertgleichheit — bei Abweichung Abbruch statt
# stillem Datenverlust.
def schritt9_e23_745ia(conn, apply_):
    felder = "hubraum_ccm,zylinder,leistung_ps,leistung_kw,drehmoment_nm,vmax_kmh,beschleunigung_0_100"
    dub = conn.execute(f"select variante_id,{felder} from motorvariante "
                       "where baureihe_id='bmw-7er-e23' and bezeichnung='745iA'").fetchone()
    if dub is None:
        log("  [9] E23 '745iA': bereits aufgeloest (idempotent)")
        return
    kanon = conn.execute(f"select variante_id,{felder} from motorvariante "
                         "where baureihe_id='bmw-7er-e23' and bezeichnung='745i'").fetchone()
    if kanon is None:
        raise RuntimeError("[9] ABBRUCH: kanonische Zeile '745i' fehlt")
    if tuple(dub[1:]) != tuple(kanon[1:]):
        raise RuntimeError(f"[9] ABBRUCH: '745iA' ist NICHT wertgleich mit '745i' "
                           f"({tuple(dub[1:])!r} vs {tuple(kanon[1:])!r}) — kein blinder Loeschvorgang")
    anhang = {t: conn.execute(f"select count(*) from {t} where variante_id=?",
                              (dub[0],)).fetchone()[0]
              for t in ("schwachstelle_motor", "kritische_wartung")}
    log(f"  [9] E23 '745iA' entfernt (wertgleiche Getriebedublette von '745i'); "
        f"Anhang faellt mit weg: {anhang}")
    if apply_:
        for t in ("schwachstelle_motor", "kritische_wartung"):
            conn.execute(f"delete from {t} where variante_id=?", (dub[0],))
        conn.execute("delete from motorvariante where variante_id=?", (dub[0],))


# ─────────────────────────────────────────────────────────────────────────────
# Schritt 10/11 — Stub- bzw. fehlbenannte Dubletten ganzer Baureihen
#
# 10) `vw-golf-8` steht neben der vollstaendigen `volkswagen-golf-viii`. Die
#     Stub-Zeile hat KEIN Bauzeitraum, KEINE Karosserie, KEIN Segment, keine
#     Schwachstellen, Rueckrufe, Ausstattungslinien oder Wartungsdaten — nur eine
#     einzige Motorzeile "2.0 TSI" mit 245 PS ohne Motorcode. Die kanonische
#     Baureihe fuehrt denselben Motor als "GTI" [DNPA] mit 245 PS.
#     QUELLE: de.wikipedia.org/wiki/VW_Golf_VIII (Modellpalette, Bauzeit ab 2019).
#
# 11) `mercedes-amg-gt-r192` traegt 2014-2021 und genau zwei Motorzeilen, die
#     wertgleiche Kopien von "GT R" und "GT R PRO" der Baureihe
#     `mercedes-amg-gt-c190` sind. Der Code "R192" ist doppelt falsch: die erste
#     AMG-GT-Generation ist C190 (Coupe) bzw. R190 (Roadster), die ZWEITE
#     Generation traegt C192 und laeuft erst ab 2023 mit voellig anderen Modellen
#     (GT 55, GT 63, GT 63 S E Performance). Diese Zeile enthaelt also keine
#     Daten der zweiten Generation, sondern eine fehlbenannte Teilkopie der ersten.
#     QUELLE: de.wikipedia.org/wiki/Mercedes-AMG_Baureihe_190 und
#     de.wikipedia.org/wiki/Mercedes-AMG_C_192, gestuetzt durch
#     auto-motor-und-sport.de (AMG GT C190 bzw. C192, technische Daten).
#
# Beide werden nur entfernt, wenn NICHTS Eigenes verloren geht: jede Motorzeile
# muss in der kanonischen Baureihe durch Kraftstoff UND Leistung abgedeckt sein,
# und es darf keinen eigenen Anhang geben. Sonst Abbruch.
STUB_DUBLETTEN = [
    ("volkswagen-golf-viii", "vw-golf-8", "10"),
]

# Der AMG GT ist KEIN reiner Stub: `mercedes-amg-gt-r192` fuehrt eigene
# Schwachstellen, Rueckrufe und sechs Ausstattungslinien. Die Precondition von
# `_loesche_dublette` hat das erkannt und abgebrochen — zu Recht. Er laeuft
# deshalb ueber die vollstaendige Merge-Strategie (Schritt 11), die Eigenes
# uebernimmt und nur inhaltlich Abgedecktes verwirft.
AMG_GT_MERGE = ("mercedes-amg-gt-c190", "mercedes-amg-gt-r192")


def _loesche_dublette(conn, kanon, alt, tag, apply_):
    if conn.execute("select count(*) from baureihe where id=?", (alt,)).fetchone()[0] == 0:
        log(f"  [{tag}] {alt}: bereits aufgeloest (idempotent)")
        return
    if conn.execute("select count(*) from baureihe where id=?", (kanon,)).fetchone()[0] != 1:
        raise RuntimeError(f"[{tag}] ABBRUCH: kanonische Baureihe {kanon} fehlt")

    # Eigener Anhang an der Baureihe? Dann ist es kein reiner Stub.
    for tabelle in ("schwachstelle_baureihe", "rueckruf", "ausstattungslinie", "quelle"):
        n = conn.execute(f"select count(*) from {tabelle} where baureihe_id=?", (alt,)).fetchone()[0]
        if n:
            raise RuntimeError(f"[{tag}] ABBRUCH: {alt} hat {n} eigene Zeile(n) in {tabelle} — "
                               f"kein reiner Stub, manuelle Pruefung noetig")
    # Jede Motorzeile muss in der kanonischen Baureihe abgedeckt sein.
    kanon_specs = {(str(k or "").lower(), p) for k, p in conn.execute(
        "select kraftstoff,leistung_ps from motorvariante where baureihe_id=?", (kanon,))}
    for vid, bez, kraft, ps in conn.execute(
            "select variante_id,bezeichnung,kraftstoff,leistung_ps from motorvariante "
            "where baureihe_id=?", (alt,)):
        if (str(kraft or "").lower(), ps) not in kanon_specs:
            raise RuntimeError(f"[{tag}] ABBRUCH: Motor {bez!r} ({kraft}, {ps} PS) aus {alt} ist in "
                               f"{kanon} nicht abgedeckt — Loeschung wuerde Daten verlieren")
        for t in ("schwachstelle_motor", "kritische_wartung"):
            n = conn.execute(f"select count(*) from {t} where variante_id=?", (vid,)).fetchone()[0]
            if n:
                raise RuntimeError(f"[{tag}] ABBRUCH: Motor {bez!r} aus {alt} hat {n} eigene "
                                   f"Zeile(n) in {t}")
    motoren = [b for (b,) in conn.execute(
        "select bezeichnung from motorvariante where baureihe_id=?", (alt,))]
    log(f"  [{tag}] {alt} entfernt — vollstaendig durch {kanon} abgedeckt "
        f"(Motoren: {motoren or 'keine'})")
    if apply_:
        conn.execute("delete from motorvariante where baureihe_id=?", (alt,))
        conn.execute("delete from baureihe where id=?", (alt,))
        conn.execute("update baureihe set vorgaenger=? where vorgaenger=?", (kanon, alt))


def schritt10_stub_dubletten(conn, apply_):
    for kanon, alt, tag in STUB_DUBLETTEN:
        _loesche_dublette(conn, kanon, alt, tag, apply_)


def schritt11_amg_gt(conn, apply_):
    """Fehlbenannte AMG-GT-Parallelwelt in die kanonische C190 ueberfuehren.

    Wie bei den BMW-Duplikaten werden die RUECKRUFE der aufzuloesenden Baureihe
    NICHT uebernommen: `mercedes-amg-gt-r192` fuehrt drei Rueckrufe, die
    dieselben Maengel beschreiben wie drei bereits vorhandene der C190 — mit
    abweichenden Daten UND abweichenden Referenznummern. Keiner von beiden
    Saetzen ist extern belegt. Sie zusammenzuwerfen wuerde die Zahl unbelegter
    Behauptungen verdoppeln statt sie zu klaeren.
    """
    _merge(conn, AMG_GT_MERGE[0], AMG_GT_MERGE[1], apply_, tag="11")


# ─────────────────────────────────────────────────────────────────────────────
# Schritt 12 — W205 C 300 e: Verbrennerleistung statt Systemleistung
#
# QUELLE: auto-data.net (Mercedes-Benz C-class W205 facelift 2018, C 300e
# EQ Power 4MATIC 9G-TRONIC, 320 Hp), ecomento.de (Mercedes C 300 e 2019) und
# ADAC-Autokatalog (C 300 e 4MATIC 9G-TRONIC, 235 kW/320 PS). Alle drei belegen
# uebereinstimmend: Systemleistung 235 kW / 320 PS aus 2.0-l-Benziner
# (155 kW/211 PS) plus E-Maschine (90 kW/122 PS).
#
# Die DB fuehrte 211 PS/155 kW — das ist exakt die VERBRENNERLEISTUNG allein.
# Dass `leistung_ps` bei Plug-in-Hybriden in dieser Tabelle die SYSTEMleistung
# meint, belegt die Schwesterzeile "C350 Plug-in-Hybrid" mit 279 PS (ebenfalls
# Systemleistung). Korrigiert werden deshalb genau diese beiden Felder.
#
# BEWUSST NICHT geaendert: Drehmoment, Vmax und Beschleunigung. Fuer sie liegt
# kein gleichermassen eindeutiger Systemwert vor; sie bleiben als offener Punkt
# ausgewiesen, statt sie zu raten.
C300E = "mercedes-benz-c-klasse-w205-c300-plug-in-hybrid"


def schritt12_c300e_systemleistung(conn, apply_):
    zeile = conn.execute("select leistung_ps,leistung_kw,hubraum_ccm from motorvariante "
                         "where variante_id=?", (C300E,)).fetchone()
    if zeile is None:
        log("  [12] W205 C300e: Zeile nicht vorhanden (leere DB) — nichts zu tun")
        return
    ps, kw, hub = zeile
    if (ps, kw) == (320, 235):
        log("  [12] W205 C300e: Systemleistung bereits korrekt (idempotent)")
        return
    if (ps, kw, hub) != (211, 155, 1991):
        raise RuntimeError(f"[12] ABBRUCH: C300e hat unerwartete Werte ({ps} PS, {kw} kW, "
                           f"{hub} ccm) — erwartet (211, 155, 1991)")
    log("  [12] W205 C300e: 211 PS/155 kW (nur Verbrenner) -> 320 PS/235 kW (Systemleistung)")
    if apply_:
        conn.execute("update motorvariante set leistung_ps=320, leistung_kw=235 "
                     "where variante_id=?", (C300E,))


# ─────────────────────────────────────────────────────────────────────────────
# Schritt 13 — Opel Insignia B (Facelift) 2.0 Diesel 174 PS: Hubraum
#
# QUELLE: Typdaten zum Motor F20DVH (motorteiledirekt.de, Motorenhaendler-
# Typdatenblatt): Bohrung 84,0 mm, Hub 90,0 mm, 4 Zylinder, Verdichtung 15,5:1,
# 128 kW/174 PS, 380 Nm, Steuerkette. Die Angaben sind in sich rechnerisch
# konsistent: 84 mm Bohrung und 90 mm Hub ergeben 1995,4 ccm — also 1995 ccm,
# nicht die in der DB stehenden 1998 ccm.
#
# Die in einer frueheren Recherche aufgetauchten 1956 ccm gehoeren zum
# VORGAENGERMOTOR B20DTH/D20DTH (2.0 CDTI, 170 PS) und sind fuer die
# Facelift-Zeile nicht einschlaegig; beide Zeilen dieses Motors stehen in der DB
# getrennt und behalten ihre 1956 ccm.
INSIGNIA_F20DVH = ("opel-insignia-b-2.0-diesel-174-ps-facelift",
                   "opel-insignia-b-2.0-diesel-174-ps-allrad-facelift")


def schritt13_insignia_hubraum(conn, apply_):
    offen = [(v, h) for v, h in conn.execute(
        "select variante_id,hubraum_ccm from motorvariante where variante_id in (?,?)",
        INSIGNIA_F20DVH) if h != 1995]
    if not offen:
        log("  [13] Insignia F20DVH: Hubraum bereits korrekt (idempotent)")
        return
    for vid, hub in offen:
        if hub != 1998:
            raise RuntimeError(f"[13] ABBRUCH: {vid} hat unerwarteten Hubraum {hub} — erwartet 1998")
        log(f"  [13] {vid}: hubraum 1998 -> 1995 ccm (Bohrung 84 x Hub 90 mm)")
        if apply_:
            conn.execute("update motorvariante set hubraum_ccm=1995 where variante_id=?", (vid,))


# ─────────────────────────────────────────────────────────────────────────────
# NICHT geaendert (geprueft, kein Fehler bzw. keine eindeutige Beleglage)
#
# * Kia Niro `kia-niro-de` vs. `kia-niro-sg2`: KEINE Dublette. DE ist die erste
#   Generation (2016-2022), SG2 die zweite (ab 2022) — belegt durch
#   de.wikipedia.org/wiki/Kia_Niro und auto-motor-und-sport.de (Niro 2. Generation,
#   Baujahr ab 2022). Beide Zeilen bleiben unveraendert. Offen bleibt, dass
#   `kia-niro-de` faelschlich `bauzeitraum_von=2022` traegt statt 2016; das ist
#   ein Einzelfeldfehler an einer REALEN Generation und keine Dublette — er wird
#   im Bericht als offener Punkt ausgewiesen, nicht hier nebenbei mitgeaendert.
#
# * Drehmoment/Vmax/Beschleunigung des W205 C300e (siehe Schritt 12).
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Registry + Runner
# ─────────────────────────────────────────────────────────────────────────────
#
# ─────────────────────────────────────────────────────────────────────────────
# Verification-Pilot — kuratierte Einzelfakt-Verifikationen
#
# Traegt die handgeprueften Zuordnungen aus app/verifikation_pilot_daten.py in die
# Tabelle `fakt_verifikation` ein. Fuer jeden Eintrag wird der Fingerprint aus dem
# AKTUELLEN Datenbankinhalt berechnet — passt der Fakt spaeter nicht mehr dazu,
# verfaellt die Verifikation automatisch (app/fakt_verifikation.py).
#
# Der Schritt ist idempotent (UNIQUE(fakt_art, fakt_id) + UPDATE bei Bestand) und
# ueberspringt sauber, was in dieser Datenbank gar nicht existiert.
def schritt_verifikation_pilot(conn, apply_):
    from app.fakt_verifikation import FAKT_ARTEN, fingerprint
    from app.verifikation_pilot_daten import GEPRUEFT_AM, PILOT_VERIFIKATIONEN

    vorhanden = {r[0] for r in conn.execute(
        "select name from sqlite_master where type='table'")}
    if "fakt_verifikation" not in vorhanden:
        log("  [V] fakt_verifikation-Tabelle fehlt — Verifikations-Pilot uebersprungen")
        return

    neu_ = aktualisiert = uebersprungen = 0
    for fakt_art, fakt_id, status, quelle, stufe, url, referenz, notiz in PILOT_VERIFIKATIONEN:
        tabelle, idspalte, _spalten = FAKT_ARTEN[fakt_art]
        zeile = conn.execute(
            f'select * from "{tabelle}" where {idspalte}=?', (fakt_id,)).fetchone()
        if zeile is None:
            uebersprungen += 1
            log(f"  [V] {fakt_art} #{fakt_id}: Fakt nicht vorhanden — uebersprungen")
            continue
        spalten = [d[0] for d in conn.execute(
            f'select * from "{tabelle}" limit 1').description]
        fp = fingerprint(fakt_art, dict(zip(spalten, zeile)))
        bestand = conn.execute(
            "select id from fakt_verifikation where fakt_art=? and fakt_id=?",
            (fakt_art, fakt_id)).fetchone()
        if bestand:
            aktualisiert += 1
            if apply_:
                conn.execute(
                    "update fakt_verifikation set fingerprint=?, status=?, quelle=?, "
                    "quelle_stufe=?, url=?, referenz=?, geprueft_am=?, notiz=? "
                    "where fakt_art=? and fakt_id=?",
                    (fp, status, quelle, stufe, url, referenz, GEPRUEFT_AM, notiz,
                     fakt_art, fakt_id))
        else:
            neu_ += 1
            if apply_:
                conn.execute(
                    "insert into fakt_verifikation (fakt_art, fakt_id, fingerprint, status, "
                    "quelle, quelle_stufe, url, referenz, geprueft_am, notiz) "
                    "values (?,?,?,?,?,?,?,?,?,?)",
                    (fakt_art, fakt_id, fp, status, quelle, stufe, url, referenz,
                     GEPRUEFT_AM, notiz))
    log(f"  [V] Verifikations-Pilot: {neu_} neu, {aktualisiert} aktualisiert, "
        f"{uebersprungen} uebersprungen (Fakt nicht in dieser DB)")


MARKER_VERIFIKATION_PILOT = "verifikation_pilot_v1"
SCHRITTE_VERIFIKATION_PILOT = (schritt_verifikation_pilot,)


# ── RECALL-VERIFICATION-/CLEANUP-PILOT ───────────────────────────────────────
#
# Zwei Schritte, strikt in dieser Reihenfolge: erst die Datenkorrekturen, dann
# die Verifikationen. Nur so entstehen die Fingerprints ueber den KORRIGIERTEN
# Inhalt — ein Fingerprint ueber den alten Stand waere sofort stale und die
# Verifikation zur Laufzeit wirkungslos (app/fakt_verifikation.py).

def schritt_recall_korrekturen(conn, apply_):
    """Korrigiert die fachlich falschen Rueckrufzeilen der vier Pilotfahrzeuge.

    Jede Korrektur traegt eine PRECONDITION (`erwartet`). Geschrieben wird nur,
    wenn die Zeile exakt den erwarteten Ausgangszustand hat. Traegt sie bereits
    die Zielwerte, gilt die Korrektur als erledigt — das macht den Schritt
    idempotent, ohne dass er sich auf den Migrationsmarker verlassen muesste.
    Jede andere Belegung wird uebersprungen: eine Zeile, die weder der eine noch
    der andere Stand ist, wurde zwischenzeitlich veraendert, und dann ist Nicht-
    Schreiben die richtige Antwort.
    """
    from app.recall_pilot_daten import RECALL_KORREKTUREN

    geaendert = bereits = uebersprungen = fehlend = 0
    for fakt_id, baureihe_id, erwartet, neu, _begruendung in RECALL_KORREKTUREN:
        zeile = conn.execute(
            "select * from rueckruf where id=? and baureihe_id=?",
            (fakt_id, baureihe_id)).fetchone()
        if zeile is None:
            fehlend += 1
            log(f"  [R] rueckruf #{fakt_id} ({baureihe_id}) nicht vorhanden — uebersprungen")
            continue
        ist = dict(zip([d[0] for d in conn.execute(
            "select * from rueckruf limit 1").description], zeile))

        if all(ist.get(k) == v for k, v in neu.items()):
            bereits += 1
            continue
        if not all(ist.get(k) == v for k, v in erwartet.items()):
            uebersprungen += 1
            abweichung = {k: ist.get(k) for k, v in erwartet.items() if ist.get(k) != v}
            log(f"  [R] rueckruf #{fakt_id}: Ausgangszustand weicht ab {abweichung!r} "
                f"— NICHT geschrieben")
            continue

        geaendert += 1
        if apply_:
            spalten = ", ".join(f"{k}=?" for k in neu)
            conn.execute(f"update rueckruf set {spalten} where id=?",
                         (*neu.values(), fakt_id))
        log(f"  [R] rueckruf #{fakt_id} ({baureihe_id}) korrigiert: "
            + ", ".join(f"{k}: {ist.get(k)!r} -> {v!r}" for k, v in neu.items()))

    log(f"  [R] Recall-Korrekturen: {geaendert} geaendert, {bereits} bereits korrekt, "
        f"{uebersprungen} wegen abweichendem Ausgangszustand uebersprungen, "
        f"{fehlend} Zeilen fehlen in dieser DB")


def schritt_recall_verifikationen(conn, apply_):
    """Schreibt die kuratierten Rueckruf-Verifikationen (nach den Korrekturen)."""
    from app.fakt_verifikation import FAKT_ARTEN, fingerprint
    from app.recall_pilot_daten import GEPRUEFT_AM, RECALL_VERIFIKATIONEN

    vorhanden = {r[0] for r in conn.execute(
        "select name from sqlite_master where type='table'")}
    if "fakt_verifikation" not in vorhanden:
        log("  [R] fakt_verifikation-Tabelle fehlt — Recall-Verifikationen uebersprungen")
        return

    neu_ = aktualisiert = uebersprungen = 0
    for fakt_art, fakt_id, status, quelle, stufe, url, referenz, notiz in RECALL_VERIFIKATIONEN:
        tabelle, idspalte, _spalten = FAKT_ARTEN[fakt_art]
        zeile = conn.execute(
            f'select * from "{tabelle}" where {idspalte}=?', (fakt_id,)).fetchone()
        if zeile is None:
            uebersprungen += 1
            log(f"  [R] {fakt_art} #{fakt_id}: Fakt nicht vorhanden — uebersprungen")
            continue
        spalten = [d[0] for d in conn.execute(
            f'select * from "{tabelle}" limit 1').description]
        fp = fingerprint(fakt_art, dict(zip(spalten, zeile)))
        bestand = conn.execute(
            "select id from fakt_verifikation where fakt_art=? and fakt_id=?",
            (fakt_art, fakt_id)).fetchone()
        if bestand:
            aktualisiert += 1
            if apply_:
                conn.execute(
                    "update fakt_verifikation set fingerprint=?, status=?, quelle=?, "
                    "quelle_stufe=?, url=?, referenz=?, geprueft_am=?, notiz=? "
                    "where fakt_art=? and fakt_id=?",
                    (fp, status, quelle, stufe, url, referenz, GEPRUEFT_AM, notiz,
                     fakt_art, fakt_id))
        else:
            neu_ += 1
            if apply_:
                conn.execute(
                    "insert into fakt_verifikation (fakt_art, fakt_id, fingerprint, status, "
                    "quelle, quelle_stufe, url, referenz, geprueft_am, notiz) "
                    "values (?,?,?,?,?,?,?,?,?,?)",
                    (fakt_art, fakt_id, fp, status, quelle, stufe, url, referenz,
                     GEPRUEFT_AM, notiz))
    log(f"  [R] Recall-Verifikationen: {neu_} neu, {aktualisiert} aktualisiert, "
        f"{uebersprungen} uebersprungen (Fakt nicht in dieser DB)")


MARKER_RECALL_PILOT = "recall_pilot_v1"
SCHRITTE_RECALL_PILOT = (schritt_recall_korrekturen, schritt_recall_verifikationen)


# ── NACHTRAG: fehlender, amtlich belegter Insignia-B-Rueckruf (KBA 12223) ────
#
# Eigener Marker und eigene Funktion: `recall_pilot_v1` ist abgeschlossen und
# auf master gemergt und wird nicht nachtraeglich veraendert.

def schritt_insignia_012223(conn, apply_):
    """Ergaenzt EINE fehlende, amtlich belegte Rueckrufzeile samt Verifikation.

    Idempotenz laeuft ueber den natuerlichen Schluessel (baureihe_id +
    kba_referenz), NICHT ueber den Migrationsmarker: bei einer frischen
    Installation legt bereits der Seed die Zeile an (mit derselben expliziten
    ID), und die Migration darf sie dann nicht ein zweites Mal einfuegen.

    Ist die vorgesehene ID von einem FREMDEN Fakt belegt, wird nichts
    geschrieben. Das waere ein Zustand, den dieser Schritt nicht kennt — dann
    ist Nicht-Schreiben die einzig sichere Antwort.
    """
    from app.fakt_verifikation import FAKT_ARTEN, fingerprint
    from app.recall_insignia_012223_daten import (
        GEPRUEFT_AM, NEUER_RUECKRUF, NEUE_VERIFIKATION,
    )

    fakt_id = NEUER_RUECKRUF["id"]
    baureihe_id = NEUER_RUECKRUF["baureihe_id"]
    kba = NEUER_RUECKRUF["kba_referenz"]

    if conn.execute("select 1 from baureihe where id=?", (baureihe_id,)).fetchone() is None:
        log(f"  [I] Baureihe {baureihe_id} fehlt — Nachtrag uebersprungen")
        return

    vorhanden = conn.execute(
        "select id from rueckruf where baureihe_id=? and kba_referenz=?",
        (baureihe_id, kba)).fetchone()
    belegt_fremd = conn.execute(
        "select baureihe_id, kba_referenz from rueckruf where id=?", (fakt_id,)).fetchone()

    if vorhanden:
        if vorhanden[0] != fakt_id:
            log(f"  [I] Rueckruf KBA {kba} existiert bereits unter ID {vorhanden[0]} "
                f"(erwartet {fakt_id}) — keine Dublette angelegt")
        else:
            log(f"  [I] Rueckruf KBA {kba} bereits vorhanden (#{fakt_id}) — unveraendert")
    elif belegt_fremd is not None:
        log(f"  [I] ID {fakt_id} ist von einem anderen Fakt belegt "
            f"({belegt_fremd[0]}, ref={belegt_fremd[1]!r}) — NICHTS geschrieben")
        return
    else:
        if apply_:
            conn.execute(
                "insert into rueckruf (id, baureihe_id, datum, betroffene_baujahre, "
                "mangel, abhilfe, kba_referenz) values (?,?,?,?,?,?,?)",
                (fakt_id, baureihe_id, NEUER_RUECKRUF["datum"],
                 NEUER_RUECKRUF["betroffene_baujahre"], NEUER_RUECKRUF["mangel"],
                 NEUER_RUECKRUF["abhilfe"], kba))
        log(f"  [I] Rueckruf #{fakt_id} ({baureihe_id}, KBA {kba}) neu angelegt: "
            f"{NEUER_RUECKRUF['mangel'][:60]}")

    # Verifikation NACH dem Einfuegen — der Fingerprint muss ueber den
    # tatsaechlich gespeicherten Inhalt gehen, sonst waere er sofort stale.
    if "fakt_verifikation" not in {r[0] for r in conn.execute(
            "select name from sqlite_master where type='table'")}:
        log("  [I] fakt_verifikation-Tabelle fehlt — Verifikation uebersprungen")
        return

    fakt_art, v_fakt_id, status, quelle, stufe, url, referenz, notiz = NEUE_VERIFIKATION
    tabelle, idspalte, _spalten = FAKT_ARTEN[fakt_art]
    zeile = conn.execute(
        f'select * from "{tabelle}" where {idspalte}=?', (v_fakt_id,)).fetchone()
    if zeile is None:
        log(f"  [I] {fakt_art} #{v_fakt_id} nicht vorhanden — Verifikation uebersprungen")
        return
    spalten = [d[0] for d in conn.execute(f'select * from "{tabelle}" limit 1').description]
    fp = fingerprint(fakt_art, dict(zip(spalten, zeile)))
    bestand = conn.execute(
        "select id from fakt_verifikation where fakt_art=? and fakt_id=?",
        (fakt_art, v_fakt_id)).fetchone()
    if apply_:
        if bestand:
            conn.execute(
                "update fakt_verifikation set fingerprint=?, status=?, quelle=?, "
                "quelle_stufe=?, url=?, referenz=?, geprueft_am=?, notiz=? "
                "where fakt_art=? and fakt_id=?",
                (fp, status, quelle, stufe, url, referenz, GEPRUEFT_AM, notiz,
                 fakt_art, v_fakt_id))
        else:
            conn.execute(
                "insert into fakt_verifikation (fakt_art, fakt_id, fingerprint, status, "
                "quelle, quelle_stufe, url, referenz, geprueft_am, notiz) "
                "values (?,?,?,?,?,?,?,?,?,?)",
                (fakt_art, v_fakt_id, fp, status, quelle, stufe, url, referenz,
                 GEPRUEFT_AM, notiz))
    log(f"  [I] Verifikation fuer {fakt_art} #{v_fakt_id}: status={status}, "
        f"Stufe={stufe}, Referenz={referenz}")


MARKER_INSIGNIA_012223 = "recall_insignia_012223_v1"
SCHRITTE_INSIGNIA_012223 = (schritt_insignia_012223,)


# -- KBA-GESAMTABGLEICH ------------------------------------------------------
#
# Drei Schritte in fester Reihenfolge:
#   1. Dubletten entfernen  - bevor irgendetwas anderes IDs anfasst
#   2. Korrekturen + amtliche Referenzen der 15 kuratierten Faelle
#   3. ALLE uebrigen Referenzen entfernen und die Verifikationen schreiben
# Die Verifikationen entstehen zuletzt, damit die Fingerprints ueber den
# KORRIGIERTEN Inhalt gehen - sonst waeren sie sofort stale.

def schritt_kba_dubletten(conn, apply_):
    """Entfernt wortgleiche Rueckruf-Dubletten derselben Baureihe."""
    from app.kba_abgleich_daten import DUBLETTEN

    entfernt = uebersprungen = 0
    for weg, kanon, baureihe_id, _begr in DUBLETTEN:
        a = conn.execute(
            "select mangel, abhilfe, baureihe_id from rueckruf where id=?",
            (weg,)).fetchone()
        b = conn.execute(
            "select mangel, abhilfe, baureihe_id from rueckruf where id=?",
            (kanon,)).fetchone()
        if a is None:
            uebersprungen += 1
            log(f"  [K] Dublette #{weg} bereits entfernt")
            continue
        if b is None:
            uebersprungen += 1
            log(f"  [K] Kanon #{kanon} fehlt - Dublette #{weg} NICHT entfernt")
            continue
        if tuple(a)[:2] != tuple(b)[:2] or a[2] != baureihe_id:
            uebersprungen += 1
            log(f"  [K] #{weg}/#{kanon} nicht mehr wortgleich - NICHT entfernt")
            continue
        offen = conn.execute(
            "select count(*) from fakt_verifikation where fakt_art='rueckruf' "
            "and fakt_id=?", (weg,)).fetchone()[0]
        if offen:
            uebersprungen += 1
            log(f"  [K] #{weg} traegt {offen} Verifikation(en) - NICHT entfernt")
            continue
        entfernt += 1
        if apply_:
            conn.execute("delete from rueckruf where id=?", (weg,))
        log(f"  [K] Dublette #{weg} entfernt (kanonisch bleibt #{kanon}, {baureihe_id})")
    log(f"  [K] Dubletten: {entfernt} entfernt, {uebersprungen} uebersprungen")


def schritt_kba_korrekturen(conn, apply_):
    """Setzt Datum, Bauzeitraum und amtliche Referenz der 15 belegten Faelle."""
    from app.kba_abgleich_daten import VERIFIZIERTE_ZUORDNUNGEN

    geaendert = bereits = uebersprungen = 0
    for eintrag in VERIFIZIERTE_ZUORDNUNGEN:
        fakt_id, baureihe_id, erwartet, neu_werte = eintrag[0], eintrag[1], eintrag[2], eintrag[3]
        zeile = conn.execute("select * from rueckruf where id=? and baureihe_id=?",
                             (fakt_id, baureihe_id)).fetchone()
        if zeile is None:
            uebersprungen += 1
            log(f"  [K] rueckruf #{fakt_id} ({baureihe_id}) fehlt - uebersprungen")
            continue
        spalten = [d[0] for d in conn.execute(
            "select * from rueckruf limit 1").description]
        ist = dict(zip(spalten, zeile))
        if all(ist.get(k) == v for k, v in neu_werte.items()):
            bereits += 1
            continue
        if not all(ist.get(k) == v for k, v in erwartet.items()):
            uebersprungen += 1
            abw = {k: ist.get(k) for k, v in erwartet.items() if ist.get(k) != v}
            log(f"  [K] rueckruf #{fakt_id}: Ausgangszustand weicht ab {abw!r} "
                f"- NICHT geschrieben")
            continue
        geaendert += 1
        if apply_:
            sql = ", ".join(f"{k}=?" for k in neu_werte)
            conn.execute(f"update rueckruf set {sql} where id=?",
                         (*neu_werte.values(), fakt_id))
        log(f"  [K] rueckruf #{fakt_id} korrigiert: "
            + ", ".join(f"{k}: {ist.get(k)!r} -> {v!r}" for k, v in neu_werte.items()))
    log(f"  [K] Korrekturen: {geaendert} geaendert, {bereits} bereits korrekt, "
        f"{uebersprungen} uebersprungen")


def schritt_kba_referenzen_und_verifikation(conn, apply_):
    """Entfernt jede nicht amtlich bestaetigte `kba_referenz` und schreibt die
    Verifikationen der belegten Faelle.

    Die Allowlist ist genau die Menge der kuratierten Fakt-IDs. Alles andere
    verliert seine Nummer: 569 von 570 Referenzen des Bestands sind entweder
    frei erfunden oder gehoeren amtlich zu einem anderen Fahrzeug. Der
    Rueckrufinhalt bleibt dabei unangetastet - es verschwindet nur die
    Scheingenauigkeit einer erfundenen Aktennummer.
    """
    from app.fakt_verifikation import FAKT_ARTEN, fingerprint
    from app.kba_abgleich_daten import (
        GEPRUEFT_AM, KBA_QUELLE, KBA_URL, VERIFIZIERTE_ZUORDNUNGEN,
        verifizierte_ids,
    )

    erlaubt = sorted(verifizierte_ids())
    platzhalter = ",".join("?" * len(erlaubt))
    betroffen = conn.execute(
        f"select count(*) from rueckruf where kba_referenz is not null "
        f"and trim(kba_referenz) <> '' and id not in ({platzhalter})",
        erlaubt).fetchone()[0]
    if apply_:
        conn.execute(
            f"update rueckruf set kba_referenz=NULL where kba_referenz is not null "
            f"and trim(kba_referenz) <> '' and id not in ({platzhalter})",
            erlaubt)
    log(f"  [K] {betroffen} nicht amtlich bestaetigte KBA-Referenzen entfernt "
        f"({len(erlaubt)} bestaetigte bleiben)")

    vorhandene_tabellen = {r[0] for r in conn.execute(
        "select name from sqlite_master where type='table'")}
    if "fakt_verifikation" not in vorhandene_tabellen:
        log("  [K] fakt_verifikation-Tabelle fehlt - Verifikationen uebersprungen")
        return

    neu_ = aktualisiert = fehlend = 0
    tabelle, idspalte, _sp = FAKT_ARTEN["rueckruf"]
    for eintrag in VERIFIZIERTE_ZUORDNUNGEN:
        fakt_id, ref, code, notiz = eintrag[0], eintrag[4], eintrag[5], eintrag[6]
        zeile = conn.execute(f'select * from "{tabelle}" where {idspalte}=?',
                             (fakt_id,)).fetchone()
        if zeile is None:
            fehlend += 1
            continue
        spalten = [d[0] for d in conn.execute(
            f'select * from "{tabelle}" limit 1').description]
        fp = fingerprint("rueckruf", dict(zip(spalten, zeile)))
        referenz = f"{ref} (Herstellercode {code})" if code else ref
        bestand = conn.execute(
            "select id from fakt_verifikation where fakt_art='rueckruf' and fakt_id=?",
            (fakt_id,)).fetchone()
        if bestand:
            aktualisiert += 1
            if apply_:
                conn.execute(
                    "update fakt_verifikation set fingerprint=?, status=?, quelle=?, "
                    "quelle_stufe=?, url=?, referenz=?, geprueft_am=?, notiz=? "
                    "where fakt_art='rueckruf' and fakt_id=?",
                    (fp, "verified", KBA_QUELLE, "A", KBA_URL, referenz,
                     GEPRUEFT_AM, notiz, fakt_id))
        else:
            neu_ += 1
            if apply_:
                conn.execute(
                    "insert into fakt_verifikation (fakt_art, fakt_id, fingerprint, "
                    "status, quelle, quelle_stufe, url, referenz, geprueft_am, notiz) "
                    "values ('rueckruf',?,?,?,?,?,?,?,?,?)",
                    (fakt_id, fp, "verified", KBA_QUELLE, "A", KBA_URL, referenz,
                     GEPRUEFT_AM, notiz))
    log(f"  [K] Verifikationen: {neu_} neu, {aktualisiert} aktualisiert, "
        f"{fehlend} Fakt fehlt")


MARKER_KBA_ABGLEICH = "kba_abgleich_v1"
SCHRITTE_KBA_ABGLEICH = (schritt_kba_dubletten, schritt_kba_korrekturen,
                         schritt_kba_referenzen_und_verifikation)


# -- BATCH A: amtliche Rueckrufe mit GESCHLOSSENER Zielgeneration ------------
#
# Der Import-Dry-Run hat 530 Rueckrufe als SAFE_IMPORT klassifiziert; 240 davon
# zielen auf eine OFFENE Generation und tragen damit die nicht aufloesbare
# Generationsfrage (BMW iX3 G08). Batch A ist die Teilmenge mit geschlossener
# Zielgeneration, nach vier zusaetzlichen Toren (app/kba_import_batch_a.py):
# 196 Rueckrufe -> 271 Zeilen. Die Daten liegen kuratiert und versioniert in
# app/kba_batch_a_daten.py.
#
# IDEMPOTENZ UND SELBSTHEILUNG
# Der Schritt laeuft ueber die EXPLIZITEN IDs, nicht ueber den Marker. Das ist
# noetig, weil bei einer frischen Installation zuerst der Seed diese Zeilen
# anlegt und danach `schritt_kba_referenzen_und_verifikation` laeuft: dessen
# Allowlist kennt nur die 15 Faelle des Gesamtabgleichs und wuerde die
# amtlichen Referenzen der Batch-A-Zeilen wieder entfernen. Dieser Schritt
# stellt sie deshalb wieder her, statt sie ein zweites Mal einzufuegen — er
# repariert eine vorhandene eigene Zeile und legt nur an, was fehlt.
#
# Ist eine vorgesehene ID von einem FREMDEN Fakt belegt (andere Baureihe oder
# anderer Mangeltext), wird fuer diese Zeile NICHTS geschrieben. Das ist ein
# Zustand, den dieser Schritt nicht kennt — Nicht-Schreiben ist die einzig
# sichere Antwort.

_BATCH_A_SPALTEN = ("baureihe_id", "datum", "betroffene_baujahre", "mangel",
                    "abhilfe", "kba_referenz")


def _batch_a_notiz(z: dict) -> str:
    from app.kba_import_batch_a import QUELLENVERMERK

    teile = [f"Amtlicher Datensatz: Modelle {z['amtliche_modelle']!r}, "
             f"Produktionszeitraum {z['amtlicher_zeitraum']}, "
             f"Veroeffentlichung {z['amtliches_datum']}."]
    if z["betroffene_baujahre"] != z["amtlicher_zeitraum"]:
        teile.append(f"Baujahre auf den Bauzeitraum der Baureihe verengt "
                     f"({z['betroffene_baujahre']}).")
    if z["datum"] is None:
        teile.append("Datum nicht uebernommen: amtlicher Sammelstempel "
                     "2008-01-01 des Erstbefuellungslaufs.")
    teile.append(QUELLENVERMERK)
    return " ".join(teile)


def schritt_batch_a_zeilen(conn, apply_):
    """Legt die Batch-A-Rueckrufe an bzw. stellt sie wieder her."""
    from app.kba_batch_a_daten import ZEILEN

    neu = repariert = unveraendert = uebersprungen = 0
    spalten_sql = ", ".join(_BATCH_A_SPALTEN)
    for z in ZEILEN:
        fid, bid = z["id"], z["baureihe_id"]
        soll = {s: z[s] for s in _BATCH_A_SPALTEN}

        if conn.execute("select 1 from baureihe where id=?", (bid,)).fetchone() is None:
            uebersprungen += 1
            log(f"  [A] Baureihe {bid} fehlt - #{fid} uebersprungen")
            continue

        zeile = conn.execute(f"select {spalten_sql} from rueckruf where id=?",
                             (fid,)).fetchone()
        if zeile is None:
            # Natuerlicher Schluessel ist (baureihe_id, kba_referenz) — EINE
            # amtliche Aktion je Baureihe. Bewusst NICHT der Mangeltext: der
            # amtliche Bestand fuehrt mehrere eigenstaendige Aktionen mit
            # wortgleicher Mangelbezeichnung (z.B. VW 9777 fuer die Produktion
            # 1997-1999 und VW 11267 fuer 2000, verschiedene Herstellercodes;
            # die Takata-Wellen beim Viano). Ueber den Text zu entdoppeln haette
            # 14 eigenstaendige amtliche Aktionen verschluckt. Gegen echte
            # Dubletten mit dem VORHANDENEN Bestand sichert Tor A3 in
            # app/kba_import_batch_a.py.
            fremd = conn.execute(
                "select id from rueckruf where baureihe_id=? and kba_referenz=?",
                (bid, z["kba_referenz"])).fetchone()
            if fremd:
                uebersprungen += 1
                log(f"  [A] KBA {z['kba_referenz']} steht auf {bid} bereits unter "
                    f"#{fremd[0]} - keine Dublette angelegt")
                continue
            neu += 1
            if apply_:
                conn.execute(
                    f"insert into rueckruf (id, {spalten_sql}) values (?,?,?,?,?,?,?)",
                    (fid, *[soll[s] for s in _BATCH_A_SPALTEN]))
            log(f"  [A] #{fid} ({bid}, KBA {z['kba_referenz']}) angelegt: "
                f"{z['mangel'][:60]}")
            continue

        ist = dict(zip(_BATCH_A_SPALTEN, zeile))
        if ist["baureihe_id"] != bid or ist["mangel"] != z["mangel"]:
            uebersprungen += 1
            log(f"  [A] ID {fid} ist von einem anderen Fakt belegt "
                f"({ist['baureihe_id']}) - NICHTS geschrieben")
            continue
        abweichend = {s: soll[s] for s in _BATCH_A_SPALTEN if ist[s] != soll[s]}
        if not abweichend:
            unveraendert += 1
            continue
        repariert += 1
        if apply_:
            sql = ", ".join(f"{s}=?" for s in abweichend)
            conn.execute(f"update rueckruf set {sql} where id=?",
                         (*abweichend.values(), fid))
        log(f"  [A] #{fid} wiederhergestellt: "
            + ", ".join(f"{s}: {ist[s]!r} -> {v!r}" for s, v in abweichend.items()))
    log(f"  [A] Zeilen: {neu} neu, {repariert} wiederhergestellt, "
        f"{unveraendert} unveraendert, {uebersprungen} uebersprungen")


def schritt_batch_a_verifikation(conn, apply_):
    """Schreibt je Batch-A-Zeile genau eine `verified`-Verifikation (Stufe A)."""
    from app.fakt_verifikation import FAKT_ARTEN, fingerprint
    from app.kba_batch_a_daten import GEPRUEFT_AM, ZEILEN
    from app.kba_import_batch_a import KBA_QUELLE, KBA_URL

    if "fakt_verifikation" not in {r[0] for r in conn.execute(
            "select name from sqlite_master where type='table'")}:
        log("  [A] fakt_verifikation-Tabelle fehlt - Verifikationen uebersprungen")
        return

    tabelle, idspalte, _sp = FAKT_ARTEN["rueckruf"]
    neu = aktualisiert = fehlend = 0
    for z in ZEILEN:
        fid = z["id"]
        zeile = conn.execute(f'select * from "{tabelle}" where {idspalte}=?',
                             (fid,)).fetchone()
        if zeile is None:
            fehlend += 1
            continue
        spalten = [d[0] for d in conn.execute(
            f'select * from "{tabelle}" limit 1').description]
        ist = dict(zip(spalten, zeile))
        # Nur die EIGENEN Zeilen verifizieren. Steht dort etwas anderes, hat
        # schritt_batch_a_zeilen bereits nichts geschrieben - dann darf hier
        # erst recht keine Vertrauensstufe entstehen.
        if ist["baureihe_id"] != z["baureihe_id"] or ist["mangel"] != z["mangel"]:
            fehlend += 1
            continue
        fp = fingerprint("rueckruf", ist)
        code = z["herstellercode"]
        referenz = (f"{z['kba_referenz']} (Herstellercode {code})" if code
                    else z["kba_referenz"])
        notiz = _batch_a_notiz(z)
        werte = (fp, "verified", KBA_QUELLE, "A", KBA_URL, referenz,
                 GEPRUEFT_AM, notiz)
        bestand = conn.execute(
            "select id from fakt_verifikation where fakt_art='rueckruf' and fakt_id=?",
            (fid,)).fetchone()
        if bestand:
            aktualisiert += 1
            if apply_:
                conn.execute(
                    "update fakt_verifikation set fingerprint=?, status=?, quelle=?, "
                    "quelle_stufe=?, url=?, referenz=?, geprueft_am=?, notiz=? "
                    "where fakt_art='rueckruf' and fakt_id=?", (*werte, fid))
        else:
            neu += 1
            if apply_:
                conn.execute(
                    "insert into fakt_verifikation (fakt_art, fakt_id, fingerprint, "
                    "status, quelle, quelle_stufe, url, referenz, geprueft_am, notiz) "
                    "values ('rueckruf',?,?,?,?,?,?,?,?,?)", (fid, *werte))
    log(f"  [A] Verifikationen: {neu} neu, {aktualisiert} aktualisiert, "
        f"{fehlend} ohne eigene Zeile")


MARKER_BATCH_A = "kba_batch_a_v1"
SCHRITTE_BATCH_A = (schritt_batch_a_zeilen, schritt_batch_a_verifikation)


# Der Marker traegt eine Version im Namen. Kommen spaeter weitere Datenkorrekturen
# hinzu, bekommen sie einen EIGENEN Marker und eine eigene Funktion — dieser hier
# wird nie nachtraeglich veraendert, sonst liefe er auf bereits migrierten
# Datenbanken nicht mehr.
MARKER_P0_V1 = "p0_fahrzeugdaten_korrekturen_v1"

SCHRITTE_P0_V1 = (
    schritt1_phantom_8er,
    schritt2_w205_c200_phev,
    schritt3_rav4,
    schritt4_insignia,
    schritt5_zahnriemen,
    schritt67_duplikate,
    schritt7_e23_749,
    schritt8_e23_745i_zylinder,
    schritt9_e23_745ia,
    schritt10_stub_dubletten,
    schritt11_amg_gt,
    schritt12_c300e_systemleistung,
    schritt13_insignia_hubraum,
)

MIGRATIONEN = (
    (MARKER_P0_V1, SCHRITTE_P0_V1),
    (MARKER_VERIFIKATION_PILOT, SCHRITTE_VERIFIKATION_PILOT),
    # Muss NACH dem Verifikations-Pilot laufen: er korrigiert drei von dessen
    # Rueckruf-Eintraegen (Fehlzitation bzw. Status `partially_verified` mit dem
    # Quellentext "keine belastbare Quelle gefunden") auf `unverified`.
    (MARKER_RECALL_PILOT, SCHRITTE_RECALL_PILOT),
    # Nachtrag: EIN fehlender, amtlich belegter Insignia-B-Rueckruf (KBA 12223).
    (MARKER_INSIGNIA_012223, SCHRITTE_INSIGNIA_012223),
    # Gesamtabgleich des Rueckrufbestands gegen den amtlichen KBA-Export.
    (MARKER_KBA_ABGLEICH, SCHRITTE_KBA_ABGLEICH),
    # Muss NACH dem Gesamtabgleich laufen: der entfernt bei einer frischen
    # Installation alle Referenzen ausserhalb seiner eigenen Allowlist — auch
    # die der geseedeten Batch-A-Zeilen. Dieser Schritt stellt sie wieder her.
    (MARKER_BATCH_A, SCHRITTE_BATCH_A),
)


def _marker_gesetzt(conn: sqlite3.Connection, marker: str) -> bool:
    try:
        return conn.execute("SELECT 1 FROM schema_migrations WHERE name=?",
                            (marker,)).fetchone() is not None
    except sqlite3.Error:
        # Tabelle noch nicht angelegt -> Migration lief sicher noch nicht.
        return False


def _fahrzeugtabellen_vorhanden(conn: sqlite3.Connection) -> bool:
    vorhanden = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return set(TABELLEN).issubset(vorhanden)


def fuehre_migration_aus(conn: sqlite3.Connection, marker: str, schritte) -> bool:
    """Fuehrt EINE Migration transaktional aus. True = angewendet (Marker gesetzt).

    Preconditions und Integritaet werden noch INNERHALB der Transaktion geprueft;
    jede Verletzung fuehrt zum Rollback, der Marker bleibt dann ungesetzt.
    """
    if _marker_gesetzt(conn, marker):
        return False
    if not _fahrzeugtabellen_vorhanden(conn):
        log_.info("Datenmigration %s uebersprungen: Fahrzeugtabellen fehlen noch.", marker)
        return False

    protokoll.clear()
    vorher = zaehle(conn)
    try:
        conn.execute("BEGIN")
        for schritt in schritte:
            schritt(conn, True)
        fehler = pruefe_integritaet(conn)
        if fehler:
            raise RuntimeError("Integritaetsverletzung: " + "; ".join(fehler))
        conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (marker,))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        log_.error("Datenmigration %s ABGEBROCHEN (%s) — Datenbank unveraendert, "
                   "Marker nicht gesetzt, naechster Start versucht es erneut.", marker, exc)
        return False

    nachher = zaehle(conn)
    diff = {k: nachher[k] - vorher[k] for k in vorher if nachher[k] != vorher[k]}
    log_.info("Datenmigration %s angewendet. Zeilendifferenz: %s",
              marker, diff or "keine (Daten waren bereits korrekt)")
    return True


def run_data_migrations(conn: sqlite3.Connection) -> None:
    """Alle noch nicht angewendeten Datenmigrationen ausfuehren (App-Start)."""
    for marker, schritte in MIGRATIONEN:
        fuehre_migration_aus(conn, marker, schritte)
