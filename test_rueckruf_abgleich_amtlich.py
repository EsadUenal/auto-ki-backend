"""
Rückrufpaket-Abnahme: amtliche Abgleichsmerkmale (Herstellercode, Fallzahlen DE/weltweit,
Eindeutigkeit im vollständigen amtlichen Bestand) und strukturierter Abgleich ADAC <-> KBA.

Netzsperre und Temp-DB VOR dem App-Import. Die Temp-DB ist eine KOPIE der lokalen Live-DB
(Altstand, nur lesend kopiert per SQLite-Backup) -- die Live-DB selbst wird nie geöffnet
zum Schreiben. Fehlt sie, läuft der Test auf einer frisch angelegten Temp-DB.

Export: der amtliche Gesamtexport (KBA-Rückrufdatenbank), Pfad über AUTO_KI_KBA_EXPORT_CSV
oder %TEMP%/kba7704/export.csv. Fehlt er, nur der eingecheckte AUSZUG
(tests/fixtures/kba_export_auszug_2026-11-10.csv, 10 echte Zeilen) -- dann gilt die
Eindeutigkeit nur gegen den Auszug, und der Test sagt das ausdrücklich.

Markierungen: [SYNTH] = synthetische Gegenprobe; [AMTLICH] = Werte aus dem Export;
[ADAC] = echter ADAC-Seitentext (tests/fixtures/adac_lexus_rc_f_seitentext_2026-11-08.txt).

    python test_rueckruf_abgleich_amtlich.py
"""
import hashlib
import os
import socket
import sqlite3
import sys
import tempfile
from types import SimpleNamespace

_TMP = tempfile.mkdtemp(prefix="rr_abgleich_")
_DB = os.path.join(_TMP, "altstand.db")
_LIVE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "auto-ki-backend", "auto_ki.db")
ALTSTAND = os.path.exists(_LIVE)
if ALTSTAND:
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

_VOLL = os.environ.get("AUTO_KI_KBA_EXPORT_CSV") or os.path.join(
    os.environ.get("TEMP", ""), "kba7704", "export.csv")
_AUSZUG = os.path.join(_HIER, "tests", "fixtures", "kba_export_auszug_2026-11-10.csv")
EXPORT = _VOLL if os.path.exists(_VOLL) else _AUSZUG
VOLLSTAENDIG = EXPORT == _VOLL
# Gegen den 10-Zeilen-Auszug ist der Nachtrag nur ausdrücklich erlaubt (min_zeilen=0) und
# setzt dann keinen Freigabe-Marker -- der Test setzt ihn in diesem Fall selbst (gekennzeichnet).
MINZ = None if VOLLSTAENDIG else 0

fehler: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        fehler.append(name)


def _snapshot(pfad):
    c = sqlite3.connect(pfad)
    try:
        rr = c.execute("SELECT id, baureihe_id, datum, betroffene_baujahre, mangel, abhilfe, "
                       "kba_referenz FROM rueckruf ORDER BY id").fetchall()
        fv = c.execute("SELECT fakt_art, fakt_id, fingerprint, status FROM fakt_verifikation "
                       "WHERE fakt_art='rueckruf' ORDER BY fakt_id").fetchall()
    finally:
        c.close()
    h = lambda rows: hashlib.sha256(repr(rows).encode()).hexdigest()  # noqa: E731
    return len(rr), h(rr), h(fv)


print(f"DB: {'Kopie der lokalen Live-DB (Altstand)' if ALTSTAND else 'frische Temp-DB'}")
print(f"Export: {'VOLLSTÄNDIGER amtlicher Export' if VOLLSTAENDIG else 'nur AUSZUG (10 Zeilen)'}")

vor_migration = _snapshot(_DB) if ALTSTAND else None

from app import database as db  # noqa: E402
db.ensure_tables()
from app.evidence import build_insights  # noqa: E402
from app.kaufaktionen import build_kaufaktionen  # noqa: E402
from app.kba_recall_refresh import abgleich_nachtrag, apply_sync, parse_export, plane_sync  # noqa: E402
from app.key_findings import build_key_findings_kauf  # noqa: E402
from app.models import TechnischeRecherche  # noqa: E402
from app.rueckruf_abgleich import (  # noqa: E402
    amtliche_merkmale, eindeutigkeitsindex, eindeutige_amtliche_zuordnung, ist_eindeutig,
)
from app import technical_research as tr  # noqa: E402

zeilen = parse_export(EXPORT)
je_ref = {}
for z in zeilen:
    je_ref.setdefault(z["KBA-Referenznummer"].lstrip("0"), z)

# ── I. Migration und Import auf der Altstand-Kopie ────────────────────────────────────────
print("\n== I. Migration/Import ==")
c = sqlite3.connect(_DB)
spalten = {r[1] for r in c.execute("PRAGMA table_info(rueckruf)")}
c.close()
check("I1 sechs additive Spalten vorhanden", {
    "herstellercode_amtlich", "modell_amtlich", "veroeffentlicht_amtlich", "anzahl_de_amtlich",
    "anzahl_weltweit_amtlich", "abgleich_eindeutig_amtlich"} <= spalten)
if ALTSTAND:
    nach_migration = _snapshot(_DB)
    check("I2 Migration: Zeilen, Inhalte und Verifikationen unverändert", nach_migration == vor_migration)

plan = plane_sync(zeilen)
with db.get_conn() as conn:
    erst = apply_sync(conn, plan)
n_nach_import = _snapshot(_DB)[0]
inhalt_vor_nachtrag = _snapshot(_DB)
n1 = abgleich_nachtrag(zeilen, apply_=True, min_zeilen=MINZ)
check("I3 Nachtrag schreibt Merkmale", n1["aktualisiert"] > 0, str(n1["aktualisiert"]))
check("I4 Nachtrag ändert weder Originaldaten noch Verifikation (Fingerprint-Felder)",
      _snapshot(_DB) == inhalt_vor_nachtrag)
n2 = abgleich_nachtrag(zeilen, apply_=True, min_zeilen=MINZ)
check("I5 zweiter Nachtrag: nichts mehr zu tun", n2["aktualisiert"] == 0 and not n2["plan"]["updates"],
      str(n2["aktualisiert"]))
# Der Sync konvergiert auf diesem Altstand im 2. Lauf (+2 Zeilen honda-civic-7 / mercedes-sl-r231,
# identisch auf 606a6bb -- Bestandsverhalten, nicht Teil dieses Pakets), danach ist er leer.
with db.get_conn() as conn:
    zweit = apply_sync(conn, plane_sync(zeilen))
n3 = abgleich_nachtrag(zeilen, apply_=True, min_zeilen=MINZ)
check("I6a Nachtrag erfasst auch die Zeilen des zweiten Imports", n3["aktualisiert"] == zweit["eingefuegt"],
      f"{n3['aktualisiert']} vs {zweit['eingefuegt']}")
plan3 = plane_sync(zeilen)
check("I6b dritter Import: nichts mehr zu tun", not plan3["neue_zeilen"] and not plan3["neue_zeilen_konditional"])
check("I6c dritter Nachtrag: nichts mehr zu tun", abgleich_nachtrag(zeilen, apply_=True, min_zeilen=MINZ)["aktualisiert"] == 0)
_c = sqlite3.connect(_DB)
dubl = _c.execute("SELECT COUNT(*) FROM (SELECT kba_referenz, baureihe_id, COUNT(*) n FROM rueckruf "
                  "WHERE kba_referenz IS NOT NULL GROUP BY 1,2 HAVING n>1)").fetchone()[0]
_c.close()
check("I6d keine doppelten (Referenz, Baureihe)-Zeilen", dubl == 0, str(dubl))
n_nach_import = _snapshot(_DB)[0]
c = sqlite3.connect(_DB)
ohne_ref_null = c.execute("SELECT COUNT(*) FROM rueckruf WHERE (kba_referenz IS NULL OR kba_referenz='') "
                          "AND abgleich_eindeutig_amtlich IS NOT NULL").fetchone()[0]
alt_ohne_ref = c.execute("SELECT COUNT(*) FROM rueckruf WHERE kba_referenz IS NULL OR kba_referenz=''").fetchone()[0]
mit_merkmal = c.execute("SELECT COUNT(*) FROM rueckruf WHERE abgleich_eindeutig_amtlich IS NOT NULL").fetchone()[0]
eindeutig = c.execute("SELECT COUNT(*) FROM rueckruf WHERE abgleich_eindeutig_amtlich=1").fetchone()[0]
c.close()
print(f"  Bestand: {n_nach_import} Zeilen, {mit_merkmal} mit Merkmalen ({eindeutig} eindeutig), "
      f"{alt_ohne_ref} alte Zeilen ohne Referenz")
check("I7 alte Zeilen ohne Referenz bleiben unverändert ohne Merkmale", ohne_ref_null == 0)

# ── II. Kollisionen gegen den amtlichen Bestand ───────────────────────────────────────────
print("\n== II. Kollisionen ==")
index = eindeutigkeitsindex(zeilen)
for ref, soll, was in (("6997", False, "BMW G310R, gleiche Merkmale, anderer Defekt"),
                       ("6999", False, "BMW G310R (Gegenstück)"),
                       ("1210", False, "Lancia Phedra, gleiche Merkmale, anderer Defekt"),
                       ("1201", False, "Lancia Phedra (Gegenstück)"),
                       ("7704", True, "Lexus LC/GS-F/RC-F, Kraftstoffhochdruckpumpe"),
                       ("8036", True, "Lexus IS/GS, Pulsationsdämpfer (eigene Aktion)")):
    z = je_ref.get(ref)
    check(f"II {ref} eindeutig={soll} ({was})", z is not None and ist_eindeutig(amtliche_merkmale(z), index) is soll)
m7704 = amtliche_merkmale(je_ref["7704"])
check("II Herstellercode/Fallzahlen 7704 übernommen [AMTLICH]",
      (m7704["herstellercode_amtlich"], m7704["anzahl_de_amtlich"], m7704["anzahl_weltweit_amtlich"])
      == ("18SMD-010", 207, 18400), str(m7704))
if not VOLLSTAENDIG:
    print("  HINWEIS: Eindeutigkeit nur gegen den Auszug geprüft, nicht gegen den vollständigen Bestand.")

# ── III. amtlicher Eintrag 7704 + ADAC bis zur sichtbaren Ausgabe ─────────────────────────
print("\n== III. KBA 7704 + ADAC ==")
BAUREIHE = "lexus-rc-xc10"


def amtliche_zeile(ref, baureihe_id=BAUREIHE):
    """[AMTLICH] Exportdatensatz -> rueckruf-Zeile über den echten Importweg (INSERT +
    verifiziere_amtlich) und den echten Nachtrag. Die Baureihen-BINDUNG ist [SYNTH]: lokal liegt
    7704 im Review (Altstand), im Live-Bericht stand die Aktion am RC F."""
    from app.kba_import_kandidaten import ImportKandidat
    from app.kba_recall_refresh import verifiziere_amtlich
    z = je_ref[ref]
    k = ImportKandidat(z)
    zeile = {"baureihe_id": baureihe_id, "datum": z["Veröffentlichungsdatum"],
             "betroffene_baujahre": f"{k.prod_von}-{k.prod_bis}", "mangel": z["Mangelbezeichnung"],
             "abhilfe": (z.get("Beschreibung der Maßnahme") or "").strip() or None,
             "kba_referenz": z["KBA-Referenznummer"]}
    with db.get_conn() as conn:
        vorhanden = conn.execute("SELECT id FROM rueckruf WHERE baureihe_id=? AND kba_referenz=?",
                                 (baureihe_id, zeile["kba_referenz"])).fetchone()
        if not vorhanden:
            cur = conn.execute(
                "INSERT INTO rueckruf (baureihe_id,datum,betroffene_baujahre,mangel,abhilfe,"
                "kba_referenz,eingrenzung_amtlich,prod_von_amtlich,prod_bis_amtlich) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (*zeile.values(), k.eingrenzung, k.prod_von, k.prod_bis))
            verifiziere_amtlich(conn, cur.lastrowid, zeile, heute="2026-11-10")
    abgleich_nachtrag(zeilen, apply_=True, min_zeilen=MINZ)
    db.invalidate_referenzdaten_cache()


def baureihe_lesen():
    _c = sqlite3.connect(_DB)
    m, mo, g = _c.execute("SELECT marke, modell, generation FROM baureihe WHERE id=?", (BAUREIHE,)).fetchone()
    _c.close()
    db.invalidate_referenzdaten_cache()
    return db.get_baureihe(m, mo, g)


amtliche_zeile("7704")
if not VOLLSTAENDIG:
    with db.get_conn() as _c:   # [SYNTH] Marker nur im Auszug-Modus von Hand
        _c.execute("INSERT OR IGNORE INTO schema_migrations (name) VALUES ('rueckruf_abgleich_nachtrag_v1')")
from app.rueckruf_abgleich import freigabe_aktiv, invalidate_freigabe_cache  # noqa: E402
invalidate_freigabe_cache()
check("III0 Freigabe des Tabellenpfads aktiv", freigabe_aktiv())
br = baureihe_lesen()
amt7704 = [r for r in br["rueckrufe"] if str(r.get("kba_referenz")).lstrip("0") == "7704"]
check("III1 7704 über den echten Lesepfad mit Merkmalen", len(amt7704) == 1
      and amt7704[0].get("anzahl_de_amtlich") == 207 and amt7704[0].get("abgleich_eindeutig_amtlich") == 1,
      str(amt7704[:1]))

_FIX = os.path.join(_HIER, "tests", "fixtures", "adac_lexus_rc_f_seitentext_2026-11-08.txt")
_zl = open(_FIX, encoding="utf-8").read().splitlines()
_S = next(i for i, z in enumerate(_zl) if z.strip() == "Alle Rückrufe")
ADAC = "\n".join(_zl[_S:_S + 45])
_URL = "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/"


def web(content=ADAC, kategorie="rueckruf", baujahr=2017):
    return tr._extrahiere_fakten([{"url": _URL, "title": "Lexus RC F Ultimate Edition Automatik | ADAC",
                                   "content": content}], kategorie, marke="Lexus", modell="RC F",
                                 baujahr=baujahr, hubraum="5.0", leistung_ps=477, abgelehnt=[])


def req(baujahr=2017):
    return SimpleNamespace(baujahr=baujahr, ausstattung=None, beschreibung=None, freitext=None,
                           kraftstoff=None, marke="Lexus", modell="RC F")


def bericht(baureihe, fakten, baujahr=2017, canonical=None):
    ins = build_insights(baureihe, None, [], req(baujahr), check_typ="kauf",
                         web_recherche=TechnischeRecherche(fakten=fakten), canonical_rueckrufe=canonical)
    return ins, build_key_findings_kauf(req(baujahr), baureihe, None, ins), \
        build_kaufaktionen(req(baujahr), baureihe, None, ins)


def ist_pumpenaktion(i):
    t = f"{i.titel} {i.beschreibung}".lower()
    return any(w in t for w in ("pulsationsdämpfer", "kraftstoffhochdruckpumpe", "pumpenabdeckung"))


f_adac = web()
check("III2 ADAC-Fakt trägt seinen Rohdatensatz [ADAC]", len(f_adac) == 1
      and (f_adac[0].rueckruf_datensatz or {}).get("anzahl", "").startswith("207"))
ins, kf, ka = bericht(br, f_adac)
akt = [i for i in ins if i.kategorie in ("rueckruf", "web_rueckruf") and ist_pumpenaktion(i)]
print("  Insights:", [(i.kategorie, i.titel) for i in akt])
check("III3 Bericht: Aktion genau EINMAL", len(akt) == 1, str(len(akt)))
check("III4 der amtliche Eintrag ist führend (kategorie 'rueckruf', nicht web)",
      bool(akt) and akt[0].kategorie == "rueckruf")
check("III5 KBA-Referenz 7704 im Nachweis erhalten",
      bool(akt) and any("7704" in str(q.ref or "") for q in akt[0].quellen), str(akt[0].quellen if akt else ""))
kf_r = [f for f in kf if f.kategorie == "rueckruf"]
check("III6 Kurzbefund: keine Web-Doppelung", not any("Webrecherche" in f.beschreibung for f in kf_r),
      str([f.beschreibung[:120] for f in kf_r]))
fragen = [f for f in ka.verkaeuferfragen.fahrzeugspezifisch if f.kategorie == "rueckruf"]
check("III7 Verkäuferfragen: höchstens eine Rückruffrage", len(fragen) <= 1, str(len(fragen)))
nachweise = [q for i in ins if ist_pumpenaktion(i) for q in i.quellen]
check("III8 Nachweise: kein Web-Nachweis neben dem amtlichen", not any(
    getattr(q, "typ", "") == "web_technik" for q in nachweise))
check("III9 Originaldaten erhalten (amtlicher Mangeltext unverändert)",
      bool(akt) and je_ref["7704"]["Mangelbezeichnung"] in akt[0].beschreibung)

# ── IV. zwei getrennte Aktionen mit derselben Komponente ──────────────────────────────────
print("\n== IV. getrennte Aktionen ==")
amtliche_zeile("8036")       # [AMTLICH] Datensatz, Bindung an den RC F [SYNTH]
br2 = baureihe_lesen()
ins2, _, _ = bericht(br2, f_adac, baujahr=2014)
refs = sorted({str(q.ref) for i in ins2 if i.kategorie == "rueckruf" for q in i.quellen if q.ref})
print("  amtliche Referenzen im Bericht:", refs)
check("IV1 8036 (IS/GS, Pulsationsdämpfer) bleibt neben 7704 sichtbar",
      any("8036" in r for r in refs) and any("7704" in r for r in refs))
web_ins2 = [i for i in ins2 if i.kategorie == "web_rueckruf" and ist_pumpenaktion(i)]
check("IV2 ADAC-Datensatz wird nur 7704 zugeordnet (kein Web-Duplikat)", len(web_ins2) == 0)
ANDERE_ADAC = ("Rückrufe & Mängel\nRückrufdatum\nJuni 2019\nAnlass\nRiss in der Kraftstoffhochdruckpumpe\n"
               "Betroffene Modelle\nRC F XC1 (12/14 - 06/25)\nVariante\nnur mit 5.0L V8 2UR-GSE Motor\n"
               "Bauzeitraum betroffener Fahrzeuge\n01.2015 bis 12.2018\nAnzahl betroffener Fahrzeuge\n"
               "55 (Deutschland) 3.100 (weltweit)\nZusätzliche Information\nDie Kraftstoffhochdruckpumpe "
               "wird geprüft und ggf. ersetzt.")   # [SYNTH] andere Aktion, gleiche Komponente
f_andere = web(ANDERE_ADAC)
ins3, _, _ = bericht(br, f_andere)
check("IV3 [SYNTH] andere Web-Aktion (gleiche Komponente, andere Merkmale) bleibt sichtbar",
      len([i for i in ins3 if i.kategorie == "web_rueckruf"]) == 1
      and len([i for i in ins3 if i.kategorie == "rueckruf" and ist_pumpenaktion(i)]) == 1)

# ── V. Kollision / Widerspruch / alte Daten ohne Fallzahlen ───────────────────────────────
print("\n== V. keine automatische Zusammenführung ==")
basis = dict(amt7704[0])
d = f_adac[0].rueckruf_datensatz
check("V1 [SYNTH] amtlicher Eintrag mit Kollision im Bestand (eindeutig=0): keine Zuordnung",
      eindeutige_amtliche_zuordnung(d, [{**basis, "abgleich_eindeutig_amtlich": 0}]) is None)
check("V2 [SYNTH] Widerspruch Fallzahl weltweit: keine Zuordnung",
      eindeutige_amtliche_zuordnung(d, [{**basis, "anzahl_weltweit_amtlich": 18401}]) is None)
check("V3 [SYNTH] Widerspruch Monat: keine Zuordnung",
      eindeutige_amtliche_zuordnung(d, [{**basis, "veroeffentlicht_amtlich": "2018-04-02"}]) is None)
check("V4 [SYNTH] Widerspruch Modellmenge: keine Zuordnung",
      eindeutige_amtliche_zuordnung(d, [{**basis, "modell_amtlich": "IS, GS"}]) is None)
check("V5 [SYNTH] zwei passende Einträge verschiedener Referenz: keine Zuordnung",
      eindeutige_amtliche_zuordnung(d, [basis, {**basis, "kba_referenz": "99999"}]) is None)
alt = {k: v for k, v in basis.items()}
for k in ("herstellercode_amtlich", "modell_amtlich", "veroeffentlicht_amtlich", "anzahl_de_amtlich",
          "anzahl_weltweit_amtlich", "abgleich_eindeutig_amtlich"):
    alt[k] = None
check("V6 alte Zeile ohne Fallzahlen: keine Zuordnung, kein Fehler",
      eindeutige_amtliche_zuordnung(d, [alt]) is None)
ins_alt, _, _ = bericht({**br, "rueckrufe": [alt]}, f_adac)
check("V7 alte Zeile ohne Fallzahlen: beide bleiben sichtbar (bisheriges Verhalten)",
      len([i for i in ins_alt if ist_pumpenaktion(i)]) == 2)
d_ohne = {k: v for k, v in d.items() if k != "anzahl"}
check("V8 ADAC ohne Fallzahlen: keine Zuordnung", eindeutige_amtliche_zuordnung(d_ohne, [basis]) is None)
check("V9 Kennung wird nicht abgeleitet (Web-Datensatz bleibt ohne Referenz)",
      "kba_referenz" not in d and "herstellercode" not in d)

# ── VI. ADAC allein ───────────────────────────────────────────────────────────────────────
print("\n== VI. ADAC allein ==")
ins_a, kf_a, _ = bericht(None, f_adac)
wa = [i for i in ins_a if i.kategorie == "web_rueckruf"]
check("VI1 genau ein Web-Rückrufhinweis", len(wa) == 1 and not [i for i in ins_a if i.kategorie == "rueckruf"])
if wa:
    check("VI2 kein verified, keine KBA-Referenz, Confidence nicht hoch",
          wa[0].trust != "verified" and not any(q.ref for q in wa[0].quellen) and wa[0].confidence != "hoch",
          f"{wa[0].trust} {wa[0].confidence}")

# ── VII. Baujahr 2017: Stichtag offen, keine FIN/Durchführung ─────────────────────────────
print("\n== VII. Baujahr 2017 ==")
check("VII1 genaues Enddatum 04.12.2017 bleibt im Wortlaut", "04.12.2017" in f_adac[0].aussage)
check("VII2 nur 'möglich' (vehicle_possible), kein Stichtagsabgleich", f_adac[0].applicability == "vehicle_possible")
texte = " ".join(f"{i.titel} {i.beschreibung}" for i in ins + ins_a if i.kategorie in ("rueckruf", "web_rueckruf")).lower()
texte += " ".join(f.beschreibung for f in kf + kf_a).lower()
check("VII3 keine bestätigte FIN-Betroffenheit oder Durchführung",
      not any(w in texte for w in ("wurde durchgeführt", "ist durchgeführt", "fin bestätigt",
                                    "ihr fahrzeug ist betroffen", "dieses fahrzeug ist betroffen")))

# ── VIII. Rückrufabsatz vs. separate Schwachstelle ────────────────────────────────────────
print("\n== VIII. Schwachstellen-Trennung ==")
SEPARAT = ("Beim Lexus RC F kommt es häufig zu Undichtigkeiten an der Ventildeckeldichtung, "
           "die zu Ölverlust am Motor führen.")      # [SYNTH] eigene Schwachstelle derselben Seite
fs = web(ADAC + "\n\nBekannte Schwächen\n" + SEPARAT, kategorie="schwachstelle")
print("  Schwachstellen:", [(f.bauteil, f.aussage[:70]) for f in fs])
check("VIII1 Zusatzabsatz des Rückrufs erzeugt keine Schwachstelle (Altfall)",
      not any("Pulsationsdämpfer" in f.aussage for f in fs))
check("VIII2 [SYNTH] separate Schwachstelle derselben Seite bleibt sichtbar",
      any("Ventildeckeldichtung" in f.aussage for f in fs))
check("VIII3 Rückruftabelle allein: keine Schwachstelle", len(web(ADAC, kategorie="schwachstelle")) == 0)

print()
print("ALLE GRÜN" if not fehler else f"{len(fehler)} FEHLER: {fehler}")
sys.exit(1 if fehler else 0)
