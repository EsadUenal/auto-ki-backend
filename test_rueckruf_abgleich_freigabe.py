"""
Veröffentlichungsablauf des Rückrufpakets, lokal auf einer KOPIE der lokalen Live-DB (Altstand):
NUR der Abgleichs-Nachtrag (`python -m app.rueckruf_abgleich`), KEIN KBA-Import.

Ablauf, wie er später in Produktion laufen soll:
  A  neuer Code startet (ensure_tables: additive Spalten) -> Freigabe AUS, Tabellenpfad inaktiv
  B  Dry-Run des Nachtrags -> schreibt nichts
  C  Teilexport wird abgewiesen -> kein Marker
  D  Nachtrag mit vollständigem Export -> nur Abgleichsspalten, Marker gesetzt, Freigabe AN
  E  zweiter Nachtrag -> null Änderungen
Danach Gegenproben: fehlende Metadaten, 7704 + ADAC, neu auftretende Kollision.

Netzsperre und Temp-DB vor dem App-Import. Markierungen: [SYNTH] synthetische Gegenprobe,
[PROD-LESUNG] die drei in Produktion gespeicherten canonical-Zeilen zu KBA 7704 (GS-F, LC, RC-F, ohne
Baureihe; tests/fixtures/prod_rueckruf_7704_canonical_2026-11-11.json), [AMTLICH] Export, [ADAC] echter
ADAC-Seitentext, [SYNTH] Gegenproben (Kollision, Fehlerinjektion).

    python test_rueckruf_abgleich_freigabe.py
"""
import hashlib
import os
import socket
import sqlite3
import sys
import tempfile
from types import SimpleNamespace

_TMP = tempfile.mkdtemp(prefix="rr_freigabe_")
_DB = os.path.join(_TMP, "altstand.db")
_LIVE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "auto-ki-backend", "auto_ki.db")
_VOLL = os.environ.get("AUTO_KI_KBA_EXPORT_CSV") or os.path.join(
    os.environ.get("TEMP", ""), "kba7704", "export.csv")
if not (os.path.exists(_LIVE) and os.path.exists(_VOLL)):
    print("BLOCKIERT: Altstand-DB oder vollständiger KBA-Export fehlt -- Ablauf nicht prüfbar.")
    sys.exit(2)
_q = sqlite3.connect(f"file:{_LIVE}?mode=ro", uri=True)
_z = sqlite3.connect(_DB)
_q.backup(_z)
_q.close()
_z.close()
os.environ["AUTO_KI_DB_PATH"] = _DB
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_TMP, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.create_connection = _gesperrt
socket.socket.connect = _gesperrt
_HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HIER)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging  # noqa: E402
logging.basicConfig(level=logging.CRITICAL)

fehler: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        fehler.append(name)


_BESTAND_SQL = ("SELECT id, baureihe_id, datum, betroffene_baujahre, mangel, abhilfe, kba_referenz, "
                "eingrenzung_amtlich, canonical_make, canonical_nameplate FROM rueckruf ORDER BY id")


def bestand():
    c = sqlite3.connect(_DB)
    try:
        rr = c.execute(_BESTAND_SQL).fetchall()
        fv = c.execute("SELECT fakt_id, fingerprint, status, quelle, referenz FROM fakt_verifikation "
                       "WHERE fakt_art='rueckruf' ORDER BY fakt_id").fetchall()
        refs = sorted({r[6] for r in rr if r[6]})
    finally:
        c.close()
    h = lambda x: hashlib.sha256(repr(x).encode()).hexdigest()  # noqa: E731
    return {"zeilen": len(rr), "refs": h(refs), "inhalt": h(rr), "verifikation": h(fv)}


def abgleichsspalten():
    c = sqlite3.connect(_DB)
    try:
        return c.execute("SELECT id, herstellercode_amtlich, modell_amtlich, veroeffentlicht_amtlich, "
                         "anzahl_de_amtlich, anzahl_weltweit_amtlich, abgleich_eindeutig_amtlich "
                         "FROM rueckruf ORDER BY id").fetchall()
    finally:
        c.close()


vorher = bestand()

# ── A. neuer Code startet ─────────────────────────────────────────────────────────────────
print("== A. Deploy (Schema) ==")
from app import database as db  # noqa: E402
db.ensure_tables()
from app import technical_research as tr  # noqa: E402
from app.evidence import build_insights  # noqa: E402
from app.kaufaktionen import build_kaufaktionen  # noqa: E402
from app.kba_recall_refresh import parse_export  # noqa: E402
from app.key_findings import build_key_findings_kauf  # noqa: E402
from app.models import TechnischeRecherche  # noqa: E402
from app.rueckruf_abgleich import (  # noqa: E402
    FREIGABE_MARKER, TeilexportFehler, apply_nachtrag, freigabe_aktiv, invalidate_freigabe_cache,
    plane_nachtrag,
)

check("A1 Schema-Migration ändert den Rückrufbestand nicht", bestand() == vorher)
invalidate_freigabe_cache()
check("A2 Freigabe AUS vor dem Nachtrag", not freigabe_aktiv())

_FIX = os.path.join(_HIER, "tests", "fixtures", "adac_lexus_rc_f_seitentext_2026-11-08.txt")
_zl = open(_FIX, encoding="utf-8").read().splitlines()
_S = next(i for i, z in enumerate(_zl) if z.strip() == "Alle Rückrufe")
ADAC = "\n".join(_zl[_S:_S + 45])
_URL = "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/"


def web():
    return tr._extrahiere_fakten([{"url": _URL, "title": "Lexus RC F Ultimate Edition Automatik | ADAC",
                                   "content": ADAC}], "rueckruf", marke="Lexus", modell="RC F",
                                 baujahr=2017, hubraum="5.0", leistung_ps=477, abgelehnt=[])


check("A3 Tabellenpfad inaktiv: ADAC-Tabelle erzeugt keinen Web-Rückruf (wie 606a6bb)", len(web()) == 0)


def plan(export):
    return plane_nachtrag(export, db.get_alle_rueckrufe_fuer_sync(),
                          {b["id"]: b.get("marke") for b in db.get_alle_baureihen_kurz()})


def anwenden(p):
    with db.get_conn() as conn:
        e = apply_nachtrag(conn, p)
    db.invalidate_referenzdaten_cache()
    return e


export = parse_export(_VOLL)
print(f"  Export: {len(export)} amtliche Zeilen")

# ── B. Dry-Run ────────────────────────────────────────────────────────────────────────────
print("== B. Dry-Run ==")
spalten_vor = abgleichsspalten()
p_b = plan(export)
print(f"  Plan: {len(p_b['updates'])} Updates, {p_b['unveraendert']} unverändert, "
      f"{len(p_b['unmatched'])} Referenz nicht im Export, {len(p_b['konflikte'])} Konflikte")
check("B1 Dry-Run schreibt nichts", abgleichsspalten() == spalten_vor and bestand() == vorher)

# ── C. Teilexport abgewiesen ──────────────────────────────────────────────────────────────
print("== C. Teilexport ==")
try:
    plan(export[:10])
    abgewiesen = False
except TeilexportFehler:
    abgewiesen = True
check("C1 Teilexport (10 Zeilen) wird abgewiesen", abgewiesen)
invalidate_freigabe_cache()
check("C2 kein Marker nach abgewiesenem Teilexport", not freigabe_aktiv())

# ── D. Nachtrag ───────────────────────────────────────────────────────────────────────────
print("== D. Nachtrag ==")
e_d = anwenden(p_b)
nachher = bestand()
for k in ("zeilen", "refs", "inhalt", "verifikation"):
    check(f"D1 {k} unverändert ({'Zeilenzahl ' + str(nachher['zeilen']) if k == 'zeilen' else k})",
          nachher[k] == vorher[k])
check("D2 Abgleichsmerkmale geschrieben", e_d["aktualisiert"] == len(p_b["updates"]) > 0,
      f"{e_d['aktualisiert']}")
invalidate_freigabe_cache()
check("D3 Freigabe AN (Marker gesetzt)", freigabe_aktiv())

# ── E. zweiter Nachtrag ───────────────────────────────────────────────────────────────────
print("== E. zweiter Nachtrag ==")
p_e = plan(export)
e_e = anwenden(p_e)
check("E1 zweiter Nachtrag: null Änderungen", e_e["aktualisiert"] == 0 and not p_e["updates"]
      and not p_e["entzogen"], f"{e_e['aktualisiert']}")

# ── F. fehlende Metadaten ─────────────────────────────────────────────────────────────────
print("== F. fehlende Metadaten ==")
c = sqlite3.connect(_DB)
ohne_ref = c.execute("SELECT COUNT(*) FROM rueckruf WHERE COALESCE(kba_referenz,'')=''").fetchone()[0]
ohne_ref_gefuellt = c.execute("SELECT COUNT(*) FROM rueckruf WHERE COALESCE(kba_referenz,'')='' "
                              "AND (abgleich_eindeutig_amtlich IS NOT NULL OR anzahl_de_amtlich IS NOT NULL)").fetchone()[0]
ohne_fallzahl = c.execute("SELECT COUNT(*) FROM rueckruf WHERE abgleich_eindeutig_amtlich IS NOT NULL "
                          "AND anzahl_de_amtlich IS NULL").fetchone()[0]
ohne_fallzahl_frei = c.execute("SELECT COUNT(*) FROM rueckruf WHERE abgleich_eindeutig_amtlich=1 "
                               "AND (anzahl_de_amtlich IS NULL OR anzahl_weltweit_amtlich IS NULL)").fetchone()[0]
frei = c.execute("SELECT COUNT(*) FROM rueckruf WHERE abgleich_eindeutig_amtlich=1").fetchone()[0]
c.close()
print(f"  {ohne_ref} Zeilen ohne Referenz, {ohne_fallzahl} amtliche ohne Fallzahl, {frei} freigegeben")
check("F1 Zeilen ohne Referenz bleiben ohne Merkmale", ohne_ref_gefuellt == 0)
check("F2 amtliche Zeile ohne Fallzahl erhält NIE die Freigabe", ohne_fallzahl_frei == 0)

# ── G. 7704 + ADAC über die ECHTE Produktionszuordnung ────────────────────────────────────
print("== G. 7704 + ADAC (Produktionszuordnung: drei canonical-Zeilen GS-F / LC / RC-F, ohne Baureihe) ==")
import json  # noqa: E402
# [PROD-LESUNG 2026-11-11] Testdatenherkunft: tests/fixtures/prod_rueckruf_7704_canonical_2026-11-11.json
# = die tatsächlich gespeicherten Zeilen (rein lesender Produktionsabgleich), nicht nachgebildet.
_prod = json.load(open(os.path.join(_HIER, "tests", "fixtures", "prod_rueckruf_7704_canonical_2026-11-11.json"),
                       encoding="utf-8"))
z7704 = next(z for z in export if z["KBA-Referenznummer"].lstrip("0") == "7704")


def zeilen_7704_einfuegen():
    """Die drei Produktionszeilen über INSERT + verifiziere_amtlich einspielen; der Fingerprint muss
    dem in Produktion gelesenen entsprechen (gleicher Inhalt -> gleiche Verifikation)."""
    from app.kba_recall_refresh import verifiziere_amtlich
    ids = []
    with db.get_conn() as conn:
        for r in _prod["zeilen"]:
            cur = conn.execute(
                "INSERT INTO rueckruf (baureihe_id,datum,betroffene_baujahre,mangel,abhilfe,kba_referenz,"
                "eingrenzung_amtlich,prod_von_amtlich,prod_bis_amtlich,canonical_make,canonical_nameplate) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                tuple(r[x] for x in ("baureihe_id", "datum", "betroffene_baujahre", "mangel", "abhilfe",
                                     "kba_referenz", "eingrenzung_amtlich", "prod_von_amtlich",
                                     "prod_bis_amtlich", "canonical_make", "canonical_nameplate")))
            verifiziere_amtlich(conn, cur.lastrowid, r, heute=_prod["verifikation"]["geprueft_am"])
            ids.append(cur.lastrowid)
        fps = {x[0] for x in conn.execute(
            "SELECT fingerprint FROM fakt_verifikation WHERE fakt_art='rueckruf' AND fakt_id IN (%s)"
            % ",".join("?" * len(ids)), ids)}
    return ids, fps


ids7704, fps = zeilen_7704_einfuegen()
check("G0 Fingerprint der eingespielten Zeilen = Produktions-Fingerprint", fps == {_prod["verifikation"]["fingerprint"]},
      str(fps))
mit_7704 = bestand()
# Freigabe ist nach Abschnitt D bereits AN. Für den Vorher-Nachher-Nachweis wird der Marker zunächst
# entfernt (Zustand "neuer Code deployt, Nachtrag noch nicht gelaufen") und dann der echte Nachtrag gefahren.
with db.get_conn() as _c:
    _c.execute("DELETE FROM schema_migrations WHERE name=?", (FREIGABE_MARKER,))
    _c.execute("UPDATE rueckruf SET herstellercode_amtlich=NULL, modell_amtlich=NULL, veroeffentlicht_amtlich=NULL, "
               "anzahl_de_amtlich=NULL, anzahl_weltweit_amtlich=NULL, abgleich_eindeutig_amtlich=NULL WHERE id IN (%s)"
               % ",".join("?" * len(ids7704)), ids7704)
invalidate_freigabe_cache()
db.invalidate_referenzdaten_cache()
check("G0b Zustand vor dem Nachtrag: Freigabe AUS", not freigabe_aktiv())


def req():
    return SimpleNamespace(baujahr=2017, ausstattung=None, beschreibung=None, freitext=None,
                           kraftstoff=None, marke="Lexus", modell="RC F")


def pumpenaktion(i):
    t = f"{i.titel} {i.beschreibung}".lower()
    return any(w in t for w in ("pulsationsdämpfer", "kraftstoffhochdruckpumpe", "pumpenabdeckung"))


def canonical_lesen():
    """Der echte canonical-Lesepfad des KaufCheck (app.kba_canonical_lookup ruft genau diese
    Funktion; der Vertrauens-Vorfilter davor ist hier NICHT Teil des Tests, s. Hinweis unten)."""
    db.invalidate_referenzdaten_cache()
    return db.get_rueckrufe_canonical_fuer_identity("LEXUS", {"RC-F"}, 2017)


def bericht(canonical):
    fakten = web()
    ins = build_insights(None, None, [], req(), check_typ="kauf",
                         web_recherche=TechnischeRecherche(fakten=fakten), canonical_rueckrufe=canonical)
    return (ins, build_key_findings_kauf(req(), None, None, ins),
            build_kaufaktionen(req(), None, None, ins), fakten)


# G-vorher: Tabellenpfad gesperrt
canon_vor = canonical_lesen()
check("G1 RC-F-Lesepfad liefert genau die passende Zeile (RC-F), nicht GS-F/LC",
      [(r["kba_referenz"], r["canonical_nameplate"]) for r in canon_vor] == [("7704", "RC-F")],
      str([(r["kba_referenz"], r["canonical_nameplate"]) for r in canon_vor]))
ins_v, _, _, fakten_v = bericht(canon_vor)
akt_v = [i for i in ins_v if i.kategorie in ("rueckruf", "web_rueckruf") and pumpenaktion(i)]
check("G2 vor dem Nachtrag: Tabellenpfad gesperrt (kein Web-Fakt), 7704 genau einmal",
      len(fakten_v) == 0 and len(akt_v) == 1 and akt_v[0].kategorie == "rueckruf")

# G-Nachtrag
p_g = plan(export)
check("G3 Nachtrag erfasst genau die drei 7704-Zeilen", sorted(u["id"] for u in p_g["updates"]) == sorted(ids7704),
      str(sorted(u["id"] for u in p_g["updates"])))
check("G3b alle drei: 18SMD-010, 207 / 18.400, eindeutig", all(
    (u["herstellercode_amtlich"], u["anzahl_de_amtlich"], u["anzahl_weltweit_amtlich"],
     u["abgleich_eindeutig_amtlich"]) == ("18SMD-010", 207, 18400, 1) for u in p_g["updates"]))
anwenden(p_g)
check("G4 Bestand unverändert durch den Nachtrag (Zeilen, Referenzen, Inhalte, Verifikation)",
      bestand() == mit_7704)
invalidate_freigabe_cache()
check("G4b Freigabe AN nach dem Nachtrag", freigabe_aktiv())

# G-nachher
canon = canonical_lesen()
check("G5 Lesepfad liefert die RC-F-Zeile jetzt mit Abgleichsmerkmalen",
      [(r["kba_referenz"], r["anzahl_de_amtlich"], r["abgleich_eindeutig_amtlich"]) for r in canon] == [("7704", 207, 1)])
ins, kf, ka, fakten = bericht(canon)
check("G6 Tabellenpfad aktiv: ADAC-Web-Fakt entsteht (trägt Rohdatensatz)", len(fakten) == 1)
akt = [i for i in ins if i.kategorie in ("rueckruf", "web_rueckruf") and pumpenaktion(i)]
print("  Bericht:", [(i.kategorie, i.titel) for i in akt])
check("G7 Bericht: 7704 genau EINMAL, amtlich führend", len(akt) == 1 and akt[0].kategorie == "rueckruf")
check("G8 Nachweise: Referenz 7704 vorhanden, kein Web-Nachweis daneben",
      bool(akt) and any("7704" in str(q.ref or "") for q in akt[0].quellen)
      and not any(getattr(q, "typ", "") == "web_technik" for i in ins if pumpenaktion(i) for q in i.quellen))
check("G9 Kurzbefund ohne Web-Doppelung",
      not any("Webrecherche" in f.beschreibung for f in kf if f.kategorie == "rueckruf"))
fragen = [f for f in ka.verkaeuferfragen.fahrzeugspezifisch if f.kategorie == "rueckruf"]
check("G10 Verkäuferfragen: genau eine Rückruffrage", len(fragen) == 1, str(len(fragen)))
print("  OFFEN (nicht Teil dieses Tests): Ob die Vertrauensprüfung für canonical-Rückrufe")
print("  (app.kba_canonical_trust) im ECHTEN KaufCheck die Zeile durchlässt, ist nur mit einem echten")
print("  KaufCheck belegbar. Der Live-Bericht 20261108-091413 führte 7704, ein Nachbau der Identität")
print("  (Lexus RC F 2017) wurde hier als 'unsicher' eingestuft -> Lesepfad direkt geprüft.")

# ── H. neu auftretende Kollision ──────────────────────────────────────────────────────────
print("== H. neue Kollision ==")
kollision = dict(z7704)
kollision["KBA-Referenznummer"] = "99999"                       # [SYNTH] neue Aktion, gleiche Merkmale
kollision["Mangelbezeichnung"] = "Synthetische Gegenprobe: anderer Defekt mit identischen Merkmalen."
export_k = export + [kollision]
p_h = plan(export_k)
print("  Freigabe entzogen:", [(e["kba_referenz"], e["grund"]) for e in p_h["entzogen"]])
check("H1 die neue Kollision entzieht genau den drei 7704-Zeilen die Freigabe",
      sorted(e["id"] for e in p_h["entzogen"]) == sorted(ids7704) and len(p_h["updates"]) == 3,
      str(len(p_h["updates"])))
vor_h = bestand()
anwenden(p_h)
check("H2 Bestand unverändert durch den Entzug", bestand() == vor_h)
c = sqlite3.connect(_DB)
check("H3 7704 jetzt eindeutig=0 (alle drei Zeilen)", [r[0] for r in c.execute(
    "SELECT abgleich_eindeutig_amtlich FROM rueckruf WHERE id IN (%s)" % ",".join("?" * len(ids7704)),
    ids7704)] == [0, 0, 0])
c.close()
ins_h, _, _, _ = bericht(canonical_lesen())
akt_h = [i for i in ins_h if i.kategorie in ("rueckruf", "web_rueckruf") and pumpenaktion(i)]
check("H4 nach Entzug keine automatische Zusammenführung: beide sichtbar", len(akt_h) == 2,
      str([(i.kategorie, i.titel) for i in akt_h]))
check("H5 erneuter Nachtrag mit derselben Kollision: null Änderungen", anwenden(plan(export_k))["aktualisiert"] == 0)

# ── I. Transaktionsabbruch: keine Teilfreigabe ────────────────────────────────────────────
print("== I. Transaktionsabbruch ==")
with db.get_conn() as _c:     # Zustand "Deploy, Nachtrag noch nicht gelaufen" wiederherstellen
    _c.execute("DELETE FROM schema_migrations WHERE name=?", (FREIGABE_MARKER,))
    _c.execute("UPDATE rueckruf SET herstellercode_amtlich=NULL, modell_amtlich=NULL, veroeffentlicht_amtlich=NULL, "
               "anzahl_de_amtlich=NULL, anzahl_weltweit_amtlich=NULL, abgleich_eindeutig_amtlich=NULL")
invalidate_freigabe_cache()
p_i = plan(export)
kaputt = dict(p_i)
kaputt["updates"] = p_i["updates"][:-1] + [{**p_i["updates"][-1], "id": object()}]   # [SYNTH] Fehler im letzten Update
vor_i = bestand()
try:
    with db.get_conn() as conn:
        apply_nachtrag(conn, kaputt)
    abgebrochen = False
except Exception:
    abgebrochen = True
c = sqlite3.connect(_DB)
mit_merkmal = c.execute("SELECT COUNT(*) FROM rueckruf WHERE abgleich_eindeutig_amtlich IS NOT NULL").fetchone()[0]
marker = bool(c.execute("SELECT 1 FROM schema_migrations WHERE name=?", (FREIGABE_MARKER,)).fetchone())
c.close()
invalidate_freigabe_cache()
check("I1 Abbruch beim letzten Update", abgebrochen)
check("I2 danach kein einziges Update und kein Marker (Rollback, keine Teilfreigabe)",
      mit_merkmal == 0 and not marker and not freigabe_aktiv(), f"{mit_merkmal} {marker}")
check("I3 Bestand unverändert", bestand() == vor_i)
anwenden(plan(export))
invalidate_freigabe_cache()
check("I4 regulärer Lauf danach: Freigabe AN", freigabe_aktiv())

print()
print("ALLE GRÜN" if not fehler else f"{len(fehler)} FEHLER: {fehler}")
sys.exit(1 if fehler else 0)
