"""
Zeitliche Anwendbarkeit von Quellen mit explizitem Modelljahres-Marker.

Befund (Lauf 977695250d, 17.11.2026): `lexus.com/.../2021/MY21-Lexus-RC-Brochure.pdf` (Tier 1, kein Datenblatt-Fenster) bestätigte den
Motorkern für Zielbaujahr 2017; der Modelljahres-Marker "MY21" im Dateinamen wurde nicht berücksichtigt. Regel: ein expliziter Marker
(MY21/MY2021, Model Year 2021, Modelljahr 2021) in Titel oder Dateiname der URL ist ein Fenster [Jahr, Jahr] (wie ein
Datenblatt-Fenster); das Vorjahr bleibt ohne Geltungsnachweis UNGEKLÄRT (kein Widerspruch, keine Bestätigung); bloße Jahreszahlen, Ordnerjahre, Fließtext und Prosa ("My 2017 ...") zählen nicht; konkrete Fenster haben Vorrang;
mehrere verschiedene Modelljahre sind mehrdeutig. Marktanwendbarkeit (US/DE) bleibt eine getrennte, hier NICHT gelöste Achse.

[ECHT]  = URL/Metadaten aus dem Log des Laufs 977695250d (kein Originaltext verfügbar).
[SYNTH] = ergänzte Texte; sie beweisen nur die Regel, keinen historischen Replay.

    python scripts/test_isolated.py test_modelljahr_anwendbarkeit.py
"""
import os
import socket
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="modelljahr_")
os.environ.setdefault("AUTO_KI_DB_PATH", os.path.join(_tmp, "t.db"))
os.environ.setdefault("AUTO_KI_CHROMA_PATH", os.path.join(_tmp, "chroma"))
_orig_connect = socket.socket.connect


def _connect(self, addr, *a, **k):
    if isinstance(addr, tuple) and str(addr[0]) in ("127.0.0.1", "::1", "localhost"):
        return _orig_connect(self, addr, *a, **k)
    raise RuntimeError(f"Netzzugriff im Test gesperrt: {addr!r}")


def _cc(addr, *a, **k):
    raise RuntimeError(f"Netzzugriff im Test gesperrt: {addr!r}")


socket.socket.connect = _connect
socket.create_connection = _cc
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)

from app import database as _db  # noqa: E402
_db.ensure_tables()
import app.technical_research as tr  # noqa: E402

FAILS: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


MY21_URL = "https://www.lexus.com/content/dam/lexus/documents/brochures/models/2021/MY21-Lexus-RC-Brochure.pdf"   # [ECHT]
fenster = tr._modelljahr_fenster

print("== A: Erkennung expliziter Modelljahres-Marker ==")
check("A1 [ECHT] MY21-Dateiname -> Fenster 2021-2021 (keine automatische Vorjahres-Erweiterung)", fenster(MY21_URL, "") == (2021, 2021), str(fenster(MY21_URL, "")))
check("A2 Ordnerjahr im Pfad ('/models/2021/') allein ist kein Modelljahr", fenster("https://x.com/brochures/models/2021/RC-Brochure.pdf", "") is None)
check("A3 bloße Jahreszahlen in Titel/Datei (Test, Copyright, Veröffentlichung) sind kein Modelljahr",
      fenster("https://x.de/test-2021-lexus.html", "Lexus RC F Test 2021 (c) 2021, veröffentlicht 03.05.2021") is None)
check("A4 Prosa 'My 2017 ...' / 'my 21 ...' ist kein Marker (MY nur Großschreibung, ohne Leerzeichen)",
      fenster("https://x.de/a.html", "My 2017 Lexus RC F im Alltag") is None and fenster("https://x.de/a.html", "my 21 Gründe") is None)
check("A5 MY2017 -> 2017-2017; Model Year 2021 / Modelljahr 2021 im Titel -> 2021-2021",
      fenster("https://x.de/MY2017-RC.pdf", "") == (2017, 2017) and fenster("https://x.de/a", "Lexus RC Model Year 2021") == (2021, 2021)
      and fenster("https://x.de/a", "Lexus RC Modelljahr 2021 Preisliste") == (2021, 2021))
check("A6 Kurzjahr ohne eindeutige Jahrhundertzuordnung wird nicht interpretiert (MY45, MY50)",
      fenster("https://x.de/MY45.pdf", "") is None and fenster("https://x.de/MY50.pdf", "") is None)
check("A7 verschiedene Modelljahre in Titel und Datei -> mehrdeutig (keine gesicherte Passung)",
      fenster("https://x.de/MY21-RC.pdf", "Lexus RC MY2019 Broschüre") == tr.ZEITRAUM_MEHRDEUTIG)
check("A8 Marker nur aus Titel/Dateiname: Fließtext und Pfadanfang zählen nicht",
      fenster("https://x.de/MY19/RC.pdf", "") is None)

print("\n== B: Wirkung in der Quellenqualifikation (Zielbaujahr 2017) ==")
ZIEL = tr._ziel("Lexus", "RC F", 2017, "5.0 V8, Heckantrieb", None)
ZIEL.update(leistung_ps=477)
TEXT = "Der Lexus RC F wird von einem 5.0-Liter V8-Saugmotor angetrieben."


def quelle(url, titel="Lexus RC Brochure", text=TEXT):
    return {"url": url, "title": titel, "content": text}


q21 = tr._qualifizierte_quelle(quelle(MY21_URL), ZIEL)
check("B1 [ECHT-URL] MY21-Broschüre: für Zielbaujahr 2017 verworfen, Grund 'baujahr_ausserhalb_modelljahr', Zeitfenster 'ausserhalb'",
      q21["verwurf_grund_datenblatt"] == "baujahr_ausserhalb_modelljahr" and q21["zeitfenster"] == "ausserhalb", str(q21["verwurf_grund_datenblatt"]))
q17 = tr._qualifizierte_quelle(quelle("https://www.lexus.com/brochures/MY17-Lexus-RC-Brochure.pdf"), ZIEL)
q18 = tr._qualifizierte_quelle(quelle("https://www.lexus.com/brochures/MY18-Lexus-RC-Brochure.pdf"), ZIEL)
check("B2 Zielmodelljahr im Scope (MY17 fuer Baujahr 2017) wird nicht ausgeschlossen",
      q17["verwurf_grund_datenblatt"] is None and q17["zeitfenster"] == "passt")
check("B2b Vorjahr ohne Geltungsnachweis (MY18 fuer Baujahr 2017): zeitlich UNGEKLAERT, ohne positive Qualifikation, aber nicht als "
      "bewiesener Widerspruch ('ungeklaert', nicht 'ausserhalb')",
      q18["verwurf_grund_datenblatt"] == "modelljahr_vorjahr_ungeklaert" and q18["zeitfenster"] == "ungeklaert")
ZIEL20 = tr._ziel("Lexus", "RC F", 2020, "5.0 V8, Heckantrieb", None)
q21_20 = tr._qualifizierte_quelle(quelle(MY21_URL), ZIEL20)
check("B2c MY21 fuer Baujahr 2020: nicht automatisch passend (ungeklaert, keine positive Qualifikation)",
      q21_20["verwurf_grund_datenblatt"] == "modelljahr_vorjahr_ungeklaert" and q21_20["zeitfenster"] == "ungeklaert")
q21_21 = tr._qualifizierte_quelle(quelle(MY21_URL), tr._ziel("Lexus", "RC F", 2021, "5.0 V8, Heckantrieb", None))
check("B2d MY21 fuer Baujahr 2021 passt", q21_21["verwurf_grund_datenblatt"] is None and q21_21["zeitfenster"] == "passt")
qm = tr._qualifizierte_quelle(quelle("https://x.com/MY21-RC.pdf", "Lexus RC MY2019 Broschüre"), ZIEL)
check("B3 widersprüchliche Modelljahre: 'modelljahr_mehrdeutig', nicht als Passung behandelt",
      qm["verwurf_grund_datenblatt"] == "modelljahr_mehrdeutig" and qm["zeitfenster"] == "mehrdeutig")
qfrei = tr._qualifizierte_quelle(quelle("https://www.lexus.com/models/2021/RC-Brochure.pdf"), ZIEL)
check("B4 ohne expliziten Marker (nur Ordnerjahr) bleibt die Quelle unverändert zulässig ('kein_fenster')",
      qfrei["verwurf_grund_datenblatt"] is None and qfrei["zeitfenster"] == "kein_fenster")
ADAC_TEXT = ("Lexus RC F Automatik Modellstart 12/14 Modellende 12/17 Motorart Otto Hubraum 4969 ccm Kraftstoffart Super "
             "Leistung 477 PS Antriebsart Hinterrad")
qadac = tr._qualifizierte_quelle(quelle("https://www.adac.de/x/MY21-katalog.html", "Lexus RC F Automatik (12/14 - 12/17): Technische Daten", ADAC_TEXT), ZIEL)
check("B5 vorhandenes Datenblatt-Fenster hat Vorrang vor einem vereinzelten Marker und wird nicht verkürzt (hier: passt für 2017)",
      qadac["verwurf_grund_datenblatt"] is None and qadac["zeitfenster"] == "passt")
qadac_aus = tr._qualifizierte_quelle(quelle("https://www.adac.de/x/289", "Lexus RC F Automatik (01/18 - 03/19): Technische Daten",
                                            ADAC_TEXT.replace("12/14", "01/18").replace("12/17", "03/19")), ZIEL)
check("B6 bestehende ADAC-Zeitfensterprüfung unverändert (Fenster 2018-2019 -> 'baujahr_ausserhalb_datenblatt')",
      qadac_aus["verwurf_grund_datenblatt"] == "baujahr_ausserhalb_datenblatt")
qtext = tr._qualifizierte_quelle(quelle("https://www.lexus.com/brochures/RC-Brochure.pdf", "Lexus RC",
                                        TEXT + " Neu im MY21: Farbe Rot. Model Year 2021 Zubehör."), ZIEL)
check("B7 Marker im Fließtext (fremder Abschnitt) greifen nicht", qtext["verwurf_grund_datenblatt"] is None)

print("\n== C: Motorkonsens ([SYNTH]-Texte, [ECHT]-URL der Broschüre) ==")
AUTOZ = {"url": "https://www.autozeitung.de/lexus-rc-f-test-der-knallharte-rc-f-im-test-108181.html",
         "title": "Lexus RC F im Test", "content": "Der Lexus RC F wird von einem 5.0 Liter V8-Saugmotor angetrieben."}
CARW = {"url": "https://carwiki.de/lexus-rc-hsn-tsn-schluesselnummer/", "title": "Lexus RC HSN & TSN",
        "content": "Lexus RC F Carbon Automatik 5013 AJM Benzin 4969 cm3 477 PS 2014 bis 2025"}
BROSCHUERE = {"url": MY21_URL, "title": "Lexus RC Brochure", "content": "Der Lexus RC F wird von einem 5.0-Liter V8-Saugmotor angetrieben."}
BROSCHUERE17 = {**BROSCHUERE, "url": "https://www.lexus.com/brochures/MY17-Lexus-RC-Brochure.pdf"}
w_ohne, _ = tr.werte_identitaet_aus([AUTOZ, CARW], ZIEL)
w_my21, _ = tr.werte_identitaet_aus([AUTOZ, CARW, BROSCHUERE], ZIEL)
w_my17, _ = tr.werte_identitaet_aus([AUTOZ, CARW, BROSCHUERE17], ZIEL)
check("C1 ohne die Broschüre: Motorkern offen (nur ein Tier-<=2-Beleg)", "engine_name" not in w_ohne.feldwerte)
check("C2 mit der MY21-Broschüre bleibt der Motorkern gleich offen (der ausgeschlossene Beleg bestätigt weder direkt noch teilweise)",
      "engine_name" not in w_my21.feldwerte)
eng = (w_my21.diagnose or {}).get("felder", {}).get("engine_name", {})
bq = [q for q in eng.get("quellen", []) if "MY21" in (q.get("url") or "")]
check("C3 Diagnose nennt den Ausschlussgrund der Broschüre nachvollziehbar",
      bool(bq) and "baujahr_ausserhalb_modelljahr" in str(bq[0].get("ausschluss")), str(bq))
check("C4 Gegenprobe: dieselbe Broschüre als MY17 (Zielmodelljahr im Scope) zählt weiter als Tier-1-Beleg und bestätigt den Motorkern",
      "engine_name" in w_my17.feldwerte and w_my17.feldwerte["engine_name"].get("value"), str(w_my17.feldwerte.get("engine_name")))
dn = (w_my21.feldwerte.get("displacement") or {}).get("domain_namen") or set()
check("C5 Hubraum 5.0 wird getrennt nach denselben Regeln geprüft: die MY21-Broschüre trägt dazu nichts bei (nur verbleibende Belege)",
      "lexus.com" not in set(dn), str(dn))

print("\n" + ("ALLE GRÜN" if not FAILS else f"{len(FAILS)} FEHLER: {FAILS}"))
sys.exit(1 if FAILS else 0)
