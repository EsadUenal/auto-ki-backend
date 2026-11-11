"""
Abnahme ADAC-Rückruftabelle + KBA 7704 gemeinsam bis zur sichtbaren Ausgabe (Insights,
Kernbefunde, Verkäuferfragen/Nachweise). Netzsperre + Temp-DB vor App-Import, kein Provider.

EINGABEN
  * ADAC-Tabelle: ECHTER Seitentext (tests/fixtures/adac_lexus_rc_f_seitentext_2026-11-08.txt).
  * KBA 7704: BERICHTSBASIERT -- Wortlaut + Datum aus Live-Bericht 20261108-091413 (Request
    TbQThkRXTnOri_35EuroCg): 22.03.2018, "Ein Bruch des Pulsationsdämpfers der
    Kraftstoffhochdruckpumpe kann zum Austritt von Kraftstoff führen." Der interne KBA-Rohdatensatz
    liegt NICHT vor: betroffene Baujahre/Variante der Zeile sind hier NICHT angenommen (leer);
    `_trust="verified"` ist eine SYNTHETISCHE Annahme (so, wie der Bericht die Zeile ausweist).
  * Synthetische Gegenproben sind im Namen mit [SYNTH] markiert.

    python test_rueckruf_datensatz_adac_kba7704.py
"""
import os
import socket
import sys
import tempfile
from types import SimpleNamespace

_tmp = tempfile.mkdtemp(prefix="rr_7704_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.create_connection = _gesperrt
socket.socket.connect = _gesperrt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import technical_research as tr  # noqa: E402
# Rückruftabellen-Pfad ist erst nach dem Abgleichs-Nachtrag aktiv (Freigabe-Marker). Diese
# Tests prüfen den FREIGEGEBENEN Pfad: Marker in der Temp-DB ausdrücklich setzen.
from app import database as _db  # noqa: E402
from app.rueckruf_abgleich import FREIGABE_MARKER, invalidate_freigabe_cache  # noqa: E402
_db.ensure_tables()
with _db.get_conn() as _c:
    _c.execute("INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)", (FREIGABE_MARKER,))
invalidate_freigabe_cache()
from app.evidence import build_insights  # noqa: E402
from app.key_findings import build_key_findings_kauf  # noqa: E402
from app.kaufaktionen import build_kaufaktionen  # noqa: E402
from app.models import TechnischeRecherche  # noqa: E402

_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures",
                    "adac_lexus_rc_f_seitentext_2026-11-08.txt")
_URL = "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/"
_zeilen = open(_FIX, encoding="utf-8").read().splitlines()
_START = next(i for i, z in enumerate(_zeilen) if z.strip() == "Alle Rückrufe")
ADAC_ECHT = "\n".join(_zeilen[_START:_START + 45])

KBA_7704 = {  # berichtsbasiert (siehe Docstring)
    "id": 7704001, "datum": "2018-03-22", "kba_referenz": "7704",
    "mangel": "Ein Bruch des Pulsationsdämpfers der Kraftstoffhochdruckpumpe kann zum Austritt von "
              "Kraftstoff führen.",
    "abhilfe": None, "betroffene_baujahre": None,
    "canonical_make": "LEXUS", "canonical_nameplate": "RC", "_trust": "verified",   # [SYNTH] Trust-Annahme
}

fehler: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        fehler.append(name)


def req(baujahr=2017):
    return SimpleNamespace(baujahr=baujahr, ausstattung=None, beschreibung=None, freitext=None,
                           kraftstoff=None, marke="Lexus", modell="RC F")


def web_fakten(baujahr=2017, content=ADAC_ECHT):
    ab: list[dict] = []
    f = tr._extrahiere_fakten([{"url": _URL, "title": "Lexus RC F Ultimate Edition Automatik | ADAC",
                                "content": content}], "rueckruf", marke="Lexus", modell="RC F",
                              baujahr=baujahr, hubraum="5.0", leistung_ps=477, abgelehnt=ab)
    return f, ab


# ── A) Datumsgrenze: exakter Wortlaut bleibt, Vergleich nur auf Jahresebene ───────────────
f17, _ = web_fakten(2017)
check("A1 Baujahr 2017: ein Fakt, nur 'vehicle_possible' (kein genauer Stichtag-Abgleich möglich)",
      len(f17) == 1 and f17[0].applicability == "vehicle_possible")
check("A2 genaues Enddatum '04.12.2017' bleibt im Wortlaut (nicht zu '12/2017' aufgeweitet)",
      bool(f17) and "04.12.2017" in f17[0].aussage and "12/2017" not in f17[0].aussage)
check("A3 Baujahr 2018 (nach dem Enddatum): kein Fakt", len(web_fakten(2018)[0]) == 0)
check("A4 Baujahr 2013 (vor Bauzeitraum 05.2014): die Pumpen-Aktion entfällt (nur die Nachbaraktion 2013-2014 bleibt)",
      not any("Pumpenabdeckung" in f.aussage for f in web_fakten(2013)[0]))
check("A5 kein Monats-/Tagesfeld im System: Erstzulassung wird für Web-Rückrufe nicht ausgewertet",
      "erstzulassung" not in open(tr.__file__, encoding="utf-8").read().lower().split("def _extrahiere_fakten")[1][:60000])
check("A6 Jahresnormalisierung nur LESEND: 04.12.2017 -> Fenster endet 2017, nicht 2018",
      tr._zeitraum_aussage("Rückruf März 2018: x. Bauzeitraum betroffener Fahrzeuge: 05.2014 bis 04.12.2017. "
                           "Variante: y.") == (2014, 2017))
check("A7 Fließtext mit Datum bleibt unverändert unparsbar (kein Verhaltenswechsel)",
      tr._zeitraum_aussage("Gebaut 05.2014 bis 04.12.2017.") == tr.zeitraum("Gebaut 05.2014 bis 04.12.2017."))

# ── B) gemeinsam bis zur sichtbaren Ausgabe ───────────────────────────────────────────────
recherche = TechnischeRecherche(fakten=f17)
ins = build_insights(None, None, [], req(2017), check_typ="kauf", web_recherche=recherche,
                     canonical_rueckrufe=[KBA_7704])
rr = [i for i in ins if i.kategorie in ("rueckruf", "web_rueckruf")]
print("\n  Insights:", [(i.kategorie, i.titel) for i in rr])
kba = [i for i in rr if i.kategorie == "rueckruf"]
web = [i for i in rr if i.kategorie == "web_rueckruf"]
check("B1 KBA-Insight vorhanden (einmal)", len(kba) == 1)
# Nur Wortlaut/Datum aus dem Bericht, OHNE amtliche Abgleichsmerkmale (Fallzahlen, Eindeutigkeit):
# keine automatische Zusammenführung -- beide bleiben sichtbar (fehlende Merkmale belegen nichts).
check("B2a nur berichtsbasierter Eintrag ohne amtliche Merkmale: KEINE Zusammenführung",
      len(rr) == 2, f"{len(rr)} Insights: {[i.titel for i in rr]}")

# Derselbe Eintrag MIT den amtlichen Merkmalen aus dem Export (vollständig, sonst Auszug).
from app.kba_recall_refresh import parse_export  # noqa: E402
from app.rueckruf_abgleich import amtliche_merkmale, eindeutigkeitsindex, ist_eindeutig  # noqa: E402
_voll = os.environ.get("AUTO_KI_KBA_EXPORT_CSV") or os.path.join(os.environ.get("TEMP", ""), "kba7704", "export.csv")
_exp = parse_export(_voll if os.path.exists(_voll) else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures", "kba_export_auszug_2026-11-10.csv"))
_z = next(z for z in _exp if z["KBA-Referenznummer"].lstrip("0") == "7704")
_m = amtliche_merkmale(_z)
KBA_7704_AMTLICH = {**KBA_7704, **{k: _m[k] for k in (
    "herstellercode_amtlich", "modell_amtlich", "veroeffentlicht_amtlich", "anzahl_de_amtlich",
    "anzahl_weltweit_amtlich")}, "prod_von_amtlich": _m["prod_von"], "prod_bis_amtlich": _m["prod_bis"],
    "abgleich_eindeutig_amtlich": int(ist_eindeutig(_m, eindeutigkeitsindex(_exp)))}
ins_amt = build_insights(None, None, [], req(2017), check_typ="kauf", web_recherche=recherche,
                         canonical_rueckrufe=[KBA_7704_AMTLICH])
rr_amt = [i for i in ins_amt if i.kategorie in ("rueckruf", "web_rueckruf")]
check("B2b mit amtlichen Merkmalen: Aktion steht EINMAL, amtlich führend",
      len(rr_amt) == 1 and rr_amt[0].kategorie == "rueckruf", f"{[i.titel for i in rr_amt]}")
ins = ins_amt
rr = rr_amt

kf = build_key_findings_kauf(req(2017), None, None, ins)
ka = build_kaufaktionen(req(2017), None, None, ins)
fragen = [f for f in ka.verkaeuferfragen.fahrzeugspezifisch if f.kategorie == "rueckruf"]
print("  Kernbefunde Rückruf:", [f.beschreibung[:90] for f in kf if f.kategorie == "rueckruf"])
print("  Verkäuferfragen Rückruf:", [getattr(f, "frage", "")[:90] for f in fragen])
check("B3 Kernbefund: höchstens EIN Rückruf-Finding", len([f for f in kf if f.kategorie == "rueckruf"]) <= 1)
check("B4 Verkäuferfragen: nicht doppelt", len(fragen) <= 1, str(len(fragen)))
check("B5 keine bestätigte FIN-Betroffenheit oder Durchführung abgeleitet", not any(
    w in (i.beschreibung or "").lower() + (i.titel or "").lower()
    for i in rr for w in ("durchgeführt", "durchgefuehrt", "fin bestätigt", "ist betroffen")))

# ── C) ohne den KBA-Eintrag steht die ADAC-Aktion trotzdem einmal da ─────────────────────
ins_adac = build_insights(None, None, [], req(2017), check_typ="kauf", web_recherche=recherche)
check("C1 nur ADAC (kein KBA): genau ein web_rueckruf-Insight",
      len([i for i in ins_adac if i.kategorie == "web_rueckruf"]) == 1)

print()
print("ALLE GRÜN" if not fehler else f"{len(fehler)} FEHLER: {fehler}")
sys.exit(1 if fehler else 0)
