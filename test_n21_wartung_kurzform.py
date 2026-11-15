"""
N21 -- Lesbarkeit der Wartungsausgabe: strukturierte Kurzform, getrennte Kilometer-/Zeitstatus, kurze eigenständige
Verkäuferfragen/Dokumente ohne Volltext-Wiederholung, HU "plausibel, nicht geprüft".

Gegenproben gegen INFORMATIONSVERLUST (Regeln, Bedingungen, Marktvorbehalt, Erst-/Folgeintervall, Wartungsabdeckung bei
Bündelung) und gegen irreführende Statusanzeigen. Reale Wortlaute: lex-sw-00de.pdf (Lexus Schweiz) via
test_wartung_regime_motoroel; [SYNTH] = synthetisch.

Netzsperre (extern; Loopback bleibt offen) + Temp-DB VOR den App-Importen. Lauf:
    python scripts/test_isolated.py test_n21_wartung_kurzform.py
"""
import datetime as dt
import os
import socket
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="n21_kurzform_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_TMP, "chroma")
_orig_connect = socket.socket.connect


def _connect(self, addr, *a, **k):
    if isinstance(addr, tuple) and str(addr[0]) in ("127.0.0.1", "::1", "localhost"):
        return _orig_connect(self, addr, *a, **k)
    raise RuntimeError(f"Netzzugriff im Test gesperrt: {addr!r}")


def _cc(addr, *a, **k):
    raise RuntimeError(f"Netzzugriff im Test gesperrt: {addr!r}")


socket.socket.connect = _connect
socket.create_connection = _cc
print("Netzsperre (extern):", socket.socket.connect is _connect, "| Temp-DB:", os.environ["AUTO_KI_DB_PATH"])
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)

from app import database as _db  # noqa: E402
_db.ensure_tables()
import app.laufleistung as ll  # noqa: E402
from app.evidence import build_insights  # noqa: E402
from app.kaufaktionen import MAX_SPEZIFISCH_PRO_BEREICH, _wartung_dokument_text, _wartung_frage_text, build_kaufaktionen  # noqa: E402
from app.kaufcheck_bericht import bericht  # noqa: E402
from app.vehicle_identity import VehicleIdentity  # noqa: E402
from app.laufleistung import build_laufleistungskontext  # noqa: E402
from app.models import (EvidenceQuelle, Insight, KaufCheckRequest, TechnischeRecherche, Wartungshinweis,  # noqa: E402
                        WebFakt)
from app.schreibstil import bereinige_nutzertexte  # noqa: E402
from app import vergleichstabelle as vt  # noqa: E402
from app.hu_termin import ABGELAUFEN, PLAUSIBEL, UNGEWOEHNLICH_WEIT, UNLESBAR, bewerte_hu  # noqa: E402

_orig_alter = ll.fahrzeugalter
ll.fahrzeugalter = lambda bj, heute_jahr=None: _orig_alter(bj, heute_jahr=2026)

fehler: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        fehler.append(name)


M = ll.WEITERE_STELLE_MARKER
MARKT = 'Die Quelle schränkt sich selbst ein ("falls für Ihren Markt zutreffend")'
FN = ("Motoröl und -filter: Bei Fahrzeugen mit Ölwechsel-Managementsystem: Motoröl- und -ölfilter wechseln (mit Fitness- "
      "und Sicherheitskontrollen), wenn die Ölwechsel-Hinweismeldung erscheint (zumindest alle 15.000 km / 1 Jahr), "
      "falls für Ihr Land zutreffend.")
ZS = ("Lexus Care-Zwischenservice: alle 15.000 km oder einmal jährlich, je nachdem, was zuerst eintritt. Dieser Service "
      "beinhaltet einen Öl- und Filterwechsel sowie zusätzliche Inspektionen von Lenkung, Bremsen, Beleuchtung und Reifen")
VOLL = (FN + " " + M + " " + ZS + " Die Quelle schränkt sich selbst ein (\"falls für Ihren Markt zutreffend\"). "
        "Ob das für Ihr Fahrzeug in Deutschland zutrifft, ist nicht bestätigt.")
VERBOTEN = ("fällig", "überfällig", "versäumt", "nicht durchgeführt", " fehlt ", "rückstand von")


def sauber(t):
    return not any(w in (t or "").lower() for w in VERBOTEN)


def hinweis_aus(text, km=82000, alter=9, technik=None, taetigkeit="ersetzen", titel="Motoröl: x"):
    i = Insight(id="x", kategorie="web_wartung", titel=titel, beschreibung=text, confidence="mittel",
                taetigkeit=taetigkeit, technische_bedingung=technik,
                technische_bedingung_status="unresolved" if technik else None)
    h = ll._wartungshinweise([i], km, alter)
    return h[0] if h else None


# ══ A. Kurzform aus Regelfeldern ═════════════════════════════════════════════════════════════
print("== A Kurzform und Statusfelder ==")
mo = hinweis_aus(VOLL)
check("A1 Motoröl: Kurzform nennt Tätigkeit, Erstintervall (km UND Zeit mit 'oder'), km- und Zeitstatus",
      mo is not None and mo.kurz.startswith("Wechsel: Erstintervall 15.000 km oder 1 Jahr.")
      and "Kilometer:" in mo.kurz and "Zeit:" in mo.kurz, mo.kurz if mo else "")
check("A1b Standardzeile der Karte (`intervall_kurz`) trägt nur Tätigkeit + Intervall, ohne Statussätze",
      mo is not None and mo.intervall_kurz == "Wechsel: Erstintervall 15.000 km oder 1 Jahr."
      and mo.kurz.startswith(mo.intervall_kurz) and "Kilometer:" not in mo.intervall_kurz)
check("A2 Motoröl: Kurzform deutlich kürzer als der volle Hinweis; der volle Hinweis bleibt unverändert erhalten",
      mo is not None and len(mo.kurz.split()) < len(mo.hinweis.split()) / 3
      and "Zusätzlich nennt die Quelle eine bedingte Regel" in mo.hinweis and "Ölwechsel-Managementsystem" in mo.hinweis
      and mo.intervall_text == VOLL.strip(), f"{len(mo.kurz.split())} vs {len(mo.hinweis.split())}" if mo else "")
check("A3 Motoröl: bestehender `status` bleibt für ältere Verbraucher (darueber); km_status/zeit_status getrennt",
      mo is not None and mo.status == "darueber" and mo.km_status == "darueber" and mo.zeit_status == "erreicht")
check("A4 Motoröl: bedingte Zusatzregel + Marktvorbehalt in `grenzen`; Fußnote als EIGENE bedingte Regel in `regeln`",
      mo is not None and any("Marktvorbehalt" in g and "nicht bestätigt" in g for g in mo.grenzen)
      and any("bedingte Regel" in g and "ungeklärt" in g for g in mo.grenzen)
      and [r.art for r in mo.regeln].count("bedingt") == 1 and [r.art for r in mo.regeln].count("grund") == 1
      and any("Ölwechsel-Managementsystem" in r.wortlaut and r.art == "bedingt" for r in mo.regeln)
      and any("Zwischenservice" in r.wortlaut and r.art == "grund" for r in mo.regeln), str(mo.grenzen if mo else ""))
check("A5 Motoröl: Handlung konkret; keine Fälligkeits-/Versäumnisaussage in Kurzform, Handlung und Grenzen",
      mo is not None and "Nachweis verlangen" in mo.handlung and "unbekannt" in mo.handlung
      and sauber(mo.kurz) and sauber(mo.handlung) and sauber(" ".join(mo.grenzen)))

vs = hinweis_aus("Ventilspiel Alle 90.000 km / 6 Jahre", km=82000, alter=9, taetigkeit="pruefen", titel="Ventilspiel: x")
check("A6 Ventilspiel (km im Bereich, Zeit rechnerisch erreicht): alter Status 'im_bereich' bleibt, der Zeitstatus "
      "'erreicht' ist ein EIGENES Feld -- beide Dimensionen sind trennbar",
      vs is not None and vs.status == "im_bereich" and vs.km_status == "im_bereich" and vs.zeit_status == "erreicht"
      and "zeitliche Alternative (oder 6 Jahre)" in vs.zeit_text and "rechnerisch erreicht" in vs.zeit_text
      and vs.kurz.startswith("Prüfung: Erstintervall 90.000 km oder 6 Jahre."), vs.kurz if vs else "")
check("A7 ... 'erreicht' ist keine Versäumnisaussage (kein 'fällig/überfällig/versäumt/Rückstand')",
      vs is not None and sauber(vs.kurz) and sauber(vs.zeit_text) and sauber(vs.handlung))

rb = hinweis_aus("Nebenaggregat-Antriebsriemen Alle 105.000 km / 6 Jahre Danach alle 15.000 km / jährlich",
                 titel="Antriebsriemen: x")
check("A8 Erst- und Folgeintervall bleiben GETRENNT (Struktur und Kurzform); das Erstintervall wird nicht überschrieben",
      rb is not None and rb.regeln[0].erstintervall == "105.000 km oder 6 Jahre"
      and rb.regeln[0].folgeintervall == "15.000 km / 1 Jahr" and rb.regeln[0].folge_genannt
      and "Erstintervall 105.000 km oder 6 Jahre, danach 15.000 km / 1 Jahr" in rb.kurz, rb.kurz if rb else "")
check("A9 ... der Kilometerstatus bezieht sich auf den ERSTpunkt (weit entfernt), die Zeit separat (erreicht)",
      rb is not None and rb.km_status == "entfernt" and rb.zeit_status == "erreicht"
      and "Erstpunkt (105.000 km) weit entfernt" in rb.kurz)

check("A9b ... 'Kilometeranteil nicht einschlägig' bleibt erhalten; Herkunft Webrecherche steht in den Vorbehalten (letzter Eintrag)",
      rb is not None and "Kilometeranteil ist damit nicht einschlägig" in rb.km_text
      and rb.grenzen and rb.grenzen[-1] == "Die Intervallangabe stammt aus der Webrecherche, nicht aus der geprüften Fahrzeugdatenbank")
ko = hinweis_aus("Motoröl alle 15.000 km")
check("A10 reine Kilometerregel: KEINE Zeitregel erfunden (keine_zeitregel, kein Zeittext, kein 'jährlich'/'Jahr')",
      ko is not None and ko.zeit_status == "keine_zeitregel" and ko.zeit_text is None and "Zeit:" not in ko.kurz
      and "Jahr" not in ko.kurz and "jährlich" not in ko.kurz, ko.kurz if ko else "")
ko2 = hinweis_aus("Motoröl alle 15.000 km. Danach jährlich")
check("A11 'jährlich' nur im Folgeintervall: keine Zeitschwelle des Erstintervalls (keine_zeitregel); das Folgeintervall "
      "steht als solches da",
      ko2 is not None and ko2.zeit_status == "keine_zeitregel" and ko2.regeln[0].folgeintervall == "1 Jahr"
      and ko2.kurz.count("danach 1 Jahr") == 1 and "Zeit:" not in ko2.kurz, ko2.kurz if ko2 else "")

hu_ = hinweis_aus("Ventilspiel alle 90.000 km / 6 Jahre", km=82000, alter=None, titel="Ventilspiel: x")
check("A12 Baujahr unbekannt: Zeitstatus 'unbekannt' (NICHT 'nicht erreicht'), Grund 'ohne Baujahr'",
      hu_ is not None and hu_.zeit_status == "unbekannt" and "ohne bekanntes Baujahr nicht bestimmbar" in hu_.zeit_text
      and "nicht erreicht" not in (hu_.zeit_text or ""), hu_.zeit_text if hu_ else "")
hg = hinweis_aus("Ventilspiel alle 90.000 km / 6 Jahre", km=82000, alter=6, titel="Ventilspiel: x")
check("A13 Altersgrenze (Alter 6, Schwelle 6): 'grenzwertig', der Erstzulassungsmonat entscheidet -- Unsicherheit bleibt",
      hg is not None and hg.zeit_status == "grenzwertig" and "ohne Erstzulassungsmonat offen" in hg.zeit_text)
hn = hinweis_aus("Ventilspiel alle 90.000 km / 6 Jahre", km=82000, alter=2, titel="Ventilspiel: x")
check("A14 Alter 2, Schwelle 6: 'nicht_erreicht' nur, weil es auch im ältesten Fall nicht erreicht ist",
      hn is not None and hn.zeit_status == "nicht_erreicht" and "noch nicht erreicht" in hn.zeit_text)

ht = hinweis_aus("Inverter-Kühlmittel Alle 90.000 km / 4 Jahre", technik="nur Hybrid- und Hybrid Plug-in-Modelle",
                 titel="Inverter-Kühlmittel: x")
check("A15 ungeklärte technische Voraussetzung: Zeit 'bedingt' (nicht als erreicht), Anwendbarkeit in `grenzen`",
      ht is not None and ht.zeit_status == "bedingt" and "ungeklärten technischen Voraussetzung" in ht.zeit_text
      and any("Anwendbarkeit ungeklärt: nur Hybrid- und Hybrid Plug-in-Modelle" in g for g in ht.grenzen),
      str(ht.zeit_status if ht else ""))
hb = hinweis_aus(FN)
check("A16 NUR bedingte Regel: km_status 'bedingt', zeit_status 'bedingt', nichts als erreicht ausgegeben",
      hb is not None and hb.km_status == "bedingt" and hb.zeit_status == "bedingt"
      and "erreicht" not in hb.kurz.replace("nicht bewertet", "") and "nur unter ungeklärter Voraussetzung" in hb.kurz,
      hb.kurz if hb else "")
hm = hinweis_aus("Motoröl alle 10.000 km. " + M + " Motoröl alle 15.000 km oder 1 Jahr.")
check("A17 mehrere verschiedene Grundregeln: keine Auswahl, beide genannt, Zeit 'uneindeutig', nichts kombiniert",
      hm is not None and hm.zeit_status == "uneindeutig" and "mehrere verschiedene Angaben" in hm.kurz
      and "„10.000 km“" in hm.kurz and "„15.000 km oder 1 Jahr“" in hm.kurz and "10.000 km oder 1 Jahr" not in hm.kurz
      and "frühester genannter Punkt" in hm.kurz, hm.kurz if hm else "")
und = hinweis_aus("Motoröl alle 30.000 km und 2 Jahre")
check("A18 UND-Verknüpfung bleibt UND (nicht zu 'oder' zusammengezogen)",
      und is not None and und.regeln[0].verknuepfung == "und" and "30.000 km und 2 Jahre" in und.kurz,
      und.kurz if und else "")

# ══ B. Fragen und Dokumente in der Kette ═════════════════════════════════════════════════════
print("\n== B Verkäuferfragen und Dokumente (Kette) ==")
REQ = KaufCheckRequest(marke="Toyota", modell="Yaris", baujahr=2017, kilometerstand=82000, kraftstoff="Benzin",
                       ausstattung=[], vorbesitzer=2, scheckheftgepflegt=True)


def fakt(bauteil, aussage, domain, taetigkeit, *, markt=False, bedingung=None):
    return WebFakt(kategorie="wartung", bauteil=bauteil, aussage=aussage, confidence="mittel",
                   quellen=[EvidenceQuelle(typ="web", url=f"https://www.{domain}/wartung.pdf", titel=f"Wartungsplan {domain}",
                                           qualitaet="Hersteller")],
                   taetigkeit=taetigkeit,
                   regionale_geltung="unresolved" if markt else None, herkunftsregion=MARKT if markt else None,
                   technische_bedingung=bedingung, technische_bedingung_status="unresolved" if bedingung else None)


FAKTEN = [
    fakt("Motoröl", ZS + " " + M + " " + FN, "quelle-a.de", "ersetzen", markt=True),
    fakt("Ventilspiel", "Ventilspiel Inspektion: Alle 90.000 km / 6 Jahre", "quelle-b.de", "pruefen", markt=True),
    fakt("Nebenaggregat-Antriebsriemen", "Antriebsriemen Alle 105.000 km / 6 Jahre Danach alle 15.000 km / jährlich",
         "quelle-c.de", "ersetzen", markt=True),
    fakt("Automatikgetriebeöl", "Automatikgetriebeöl prüfen: alle 60.000 km oder 4 Jahre", "quelle-d.de", "pruefen"),
    fakt("Kühlmittel", "Kühlmittel prüfen: alle 30.000 km oder 2 Jahre", "quelle-e.de", "pruefen",
         bedingung="nur Fahrzeuge mit Zusatzheizung"),
]


def rueckruf(k):
    return {"id": 91000 + k, "baureihe_id": None, "datum": "2023-01-19", "betroffene_baujahre": "2014-2019",
            "mangel": f"[SYNTH] Rückruf {k}: Bauteil {k} kann ausfallen.", "abhilfe": f"Bauteil {k} wird ersetzt.",
            "kba_referenz": f"9904{k}", "canonical_make": "TOYOTA", "canonical_nameplate": "YARIS", "_trust": "verified",
            "eingrenzung_amtlich": "YARIS", "prod_von_amtlich": 2014, "prod_bis_amtlich": 2019}


def kette(rueckrufe):
    ins = build_insights(None, None, [], REQ, check_typ="kauf",
                         web_recherche=TechnischeRecherche(ausgeloest_durch="db_miss", fakten=FAKTEN),
                         canonical_rueckrufe=rueckrufe)
    lk = build_laufleistungskontext(REQ, ins, heute_jahr=2026)
    ka = build_kaufaktionen(REQ, None, None, ins, laufleistungskontext=lk)
    antwort = bereinige_nutzertexte({"insights": [i.model_dump() for i in ins], "kaufaktionen": ka.model_dump(),
                                     "laufleistungskontext": lk.model_dump() if lk else None})
    return ins, lk, ka, antwort


ins, lk, ka, antwort = kette([])
check("B0 Vorbedingung: alle fünf Wartungen erreichen die Laufleistungskarte",
      len(lk.wartungshinweise) == 5, str(sorted(w.bauteil for w in lk.wartungshinweise)))
for bereich in ("verkaeuferfragen", "dokumente"):
    pl = antwort["kaufaktionen"][bereich]["fahrzeugspezifisch"]
    wart = [a for a in pl if a["kategorie"] in ("wartung", "web_wartung")]
    check(f"B1 {bereich}: alle fünf Wartungspunkte als Einzelaktion (Limit {MAX_SPEZIFISCH_PRO_BEREICH} unverändert)",
          len(wart) == 5 and len(pl) <= MAX_SPEZIFISCH_PRO_BEREICH, str([a["titel"] for a in wart]))
    for w in lk.wartungshinweise:
        kernsatz = " ".join(w.hinweis.split()[:12])
        check(f"B2 {bereich} {w.bauteil}: KEIN Volltext des Hinweises in einer Aktion (Kurzform statt Wiederholung)",
              not any(kernsatz in a["aktion"] for a in wart), kernsatz)
mo_f = next(a for a in antwort["kaufaktionen"]["verkaeuferfragen"]["fahrzeugspezifisch"] if "Motoröl" in a["titel"])
mo_d = next(a for a in antwort["kaufaktionen"]["dokumente"]["fahrzeugspezifisch"] if "Motoröl" in a["titel"])
mo_k_hinweis = next(w for w in lk.wartungshinweise if w.bauteil == "Motoröl").hinweis
print("  FRAGE   :", mo_f["titel"], "\n           ", mo_f["aktion"])
print("  DOKUMENT:", mo_d["titel"], "\n           ", mo_d["aktion"])
check("B3 Motoröl-Frage: die Frage (Titel) erfragt Tätigkeit, Durchführung, Datum und Kilometerstand; die Aktion trägt nur den "
      "Bezug (Intervall, Anlass, knappe Vorbehalte)",
      "gewechselt" in mo_f["titel"] and "wann und bei welchem Kilometerstand" in mo_f["titel"]
      and mo_f["aktion"].startswith("Laut Quelle: Erstintervall 15.000 km oder 1 Jahr. Anlass: ")
      and "Marktbezug: Geltung in Deutschland nicht bestätigt" in mo_f["aktion"]
      and "eine zusätzliche Regel der Quelle (15.000 km oder 1 Jahr) gilt nur unter einer Voraussetzung, die für dieses "
          "Fahrzeug ungeklärt ist" in mo_f["aktion"], mo_f["aktion"])
check("B3a Motoröl-Frage: KEINE angehängte Handlungsanweisung, KEIN Quellenzitat, KEIN Fußnotenwortlaut, keine Statuserklärung",
      "Nach Datum, Kilometerstand und Beleg fragen" not in mo_f["aktion"] and "zeigen lassen" not in mo_f["aktion"]
      and "„" not in mo_f["aktion"] and "Ölwechsel-Managementsystem" not in mo_f["aktion"]
      and "Hinweismeldung" not in mo_f["aktion"] and "Ohne bekannten Bezug" not in mo_f["aktion"]
      and "nicht ausgewertet" not in mo_f["aktion"] and sauber(mo_f["aktion"]), mo_f["aktion"])
check("B4 Motoröl-Dokument: konkreter Beleg mit Datum und Kilometerstand, Intervallbezug, knappe Vorbehalte, Herkunft",
      "Wechselnachweis" in mo_d["titel"]
      and mo_d["aktion"].startswith("Rechnung oder Eintrag im Serviceheft mit Datum und Kilometerstand zeigen lassen. "
                                    "Bezug laut Quelle: Erstintervall 15.000 km oder 1 Jahr.")
      and "Marktbezug: Geltung in Deutschland nicht bestätigt" in mo_d["aktion"]
      and "zusätzliche Regel der Quelle (15.000 km oder 1 Jahr)" in mo_d["aktion"]
      and mo_d["aktion"].endswith("Die Intervallangabe stammt aus der Webrecherche, nicht aus der geprüften Fahrzeugdatenbank."),
      mo_d["aktion"])
check("B4a Motoröl-Dokument: KEINE erneute Wartungserklärung (kein Anlass-/Statussatz, kein Zitat, keine Durchführungsfrage)",
      "Anlass:" not in mo_d["aktion"] and "nach dem Baujahr" not in mo_d["aktion"] and "„" not in mo_d["aktion"]
      and "Ob die Arbeit gemacht wurde" not in mo_d["aktion"] and "Ohne bekannten Bezug" not in mo_d["aktion"]
      and "Ölwechsel-Managementsystem" not in mo_d["aktion"] and sauber(mo_d["aktion"]), mo_d["aktion"])
check("B5 Motoröl: Frage-Aktion und Dokument-Aktion enthalten keinen Satz des vollen Hinweises (kein Textvolltext)",
      not any(sa.strip() and sa.strip() in (mo_f["aktion"] + " " + mo_d["aktion"])
              for sa in mo_k_hinweis.split(". ")[:4]), "")
mo_k_w = next(w for w in lk.wartungshinweise if w.bauteil == "Motoröl")
check("B5a Vollständiges bleibt erreichbar: Karte trägt vollen Hinweis, Handlung (Durchführung unbekannt), beide Regeln mit Wortlaut, Quelle",
      "Ohne bekannten Bezug" in mo_k_w.hinweis and "ist unbekannt" in mo_k_w.handlung
      and any("Ölwechsel-Managementsystem" in r.wortlaut and "falls für Ihr Land zutreffend" in r.wortlaut and r.art == "bedingt"
              for r in mo_k_w.regeln)
      and any("Zwischenservice" in r.wortlaut and r.art == "grund" for r in mo_k_w.regeln) and mo_k_w.quellen)
rb_f = next(a for a in antwort["kaufaktionen"]["verkaeuferfragen"]["fahrzeugspezifisch"] if "Antriebsriemen" in a["titel"])
check("B6 Antriebsriemen: Erst- UND Folgeintervall getrennt in der Frage",
      "Erstintervall 105.000 km oder 6 Jahre, danach 15.000 km / 1 Jahr" in rb_f["aktion"], rb_f["aktion"])
kue = next(a for a in antwort["kaufaktionen"]["verkaeuferfragen"]["fahrzeugspezifisch"]
           if "Kühlmittel" in a["titel"] or "Anwendbarkeit" in a["titel"])
check("B7 ungeklärte Anwendbarkeit bleibt die ZUERST gestellte Frage (N12); die Aktion nennt Intervallbezug und Bedingung knapp, "
      "ohne den langen Erklärtext zu wiederholen",
      "Trifft die Bedingung" in kue["titel"] and "nur Fahrzeuge mit Zusatzheizung" in kue["aktion"]
      and "Laut Quelle: Erstintervall 30.000 km oder 2 Jahre" in kue["aktion"]
      and "Eine Webrecherche nennt" not in kue["aktion"], kue["aktion"])
kue_d = next(a for a in antwort["kaufaktionen"]["dokumente"]["fahrzeugspezifisch"] if "Anwendbarkeit" in a["titel"])
check("B7a Anwendbarkeits-Dokument: Titel, konkrete Klärung (Fahrzeugschein/FIN) und Intervallbezug, kein Anlass-/Statussatz",
      "Technische Anwendbarkeit" in kue_d["titel"] and "Fahrzeugschein oder FIN" in kue_d["aktion"]
      and "Bezug laut Quelle: Erstintervall 30.000 km oder 2 Jahre" in kue_d["aktion"] and "Anlass:" not in kue_d["aktion"],
      kue_d["aktion"])
kw = antwort["laufleistungskontext"]["wartungshinweise"]
mo_k = next(w for w in kw if w["bauteil"] == "Motoröl")
check("B8 Karte/API: voller `hinweis`, `intervall_text`, `quellen` UND strukturierte `regeln` bleiben erhalten",
      len(mo_k["hinweis"].split()) > 100 and mo_k["intervall_text"] and mo_k["quellen"] and len(mo_k["regeln"]) == 2
      and mo_k["km_status"] and mo_k["zeit_status"] and mo_k["kurz"] and isinstance(mo_k["grenzen"], list),
      str(sorted(mo_k.keys())))

def bericht_zu(ins_, lk_, ka_):
    return bericht(REQ, VehicleIdentity.from_check_context(None, None, REQ), None, None, ins_, ka_, [],
                   "kaufen_nach_besichtigung", type("PA", (), {"label": None})(), False, lk_, None, ["Inserat"])


b_voll = " ".join(bericht_zu(ins, lk, ka).split())
check("B9 Bericht: der vollständige Motoröl-Wortlaut (Fußnote und Kopfzeile) und seine Quelle stehen weiter im Wartungs-/"
      "Risikoabschnitt -- unabhängig von der Kürzung in Frage und Dokument",
      "## Relevante Risiken und Hinweise" in b_voll and "Ölwechsel-Managementsystem" in b_voll
      and "falls für Ihr Land zutreffend" in b_voll and "Lexus Care-Zwischenservice" in b_voll and "quelle-a.de" in b_voll,
      b_voll[:200])
check("B9a Bericht: alle fünf Wartungen mit eigener Quelle weiter im Risikoabschnitt",
      all(d in b_voll for d in ("quelle-a.de", "quelle-b.de", "quelle-c.de", "quelle-d.de", "quelle-e.de")))
check("B9b Bericht trägt Frage- und Dokumenttext der Wartungen in der neuen Kurzform (genau je Bereich einmal)",
      b_voll.count(mo_f["aktion"]) == 1 and b_voll.count(mo_d["aktion"]) == 1)

# B10: Evidence-Titel und Berichtsüberschrift
ge_i = next(i for i in ins if i.kategorie == "web_wartung" and getattr(i, "bauteil", None) == "Automatikgetriebeöl")
check("B10 Evidence-Titel benennt die strukturierte Fahrzeugbedingung (Getriebeart), Präsenz bleibt UNBEKANNT, kein 'verbaut'",
      ge_i.titel.startswith("Falls das Fahrzeug folgendes Merkmal hat (Automatik-/Doppelkupplungsgetriebe, am Fahrzeug nicht bestätigt): ")
      and ge_i.presence_state == "unknown" and "verbaut" not in ge_i.titel, ge_i.titel)
check("B10a Berichtsüberschrift im Abschnitt 'Relevante Risiken und Hinweise' übernimmt den Titel; Beschreibung, Intervall und Quelle "
      "bleiben",
      f"### {ge_i.titel}" in bericht_zu(ins, lk, ka) and "Automatikgetriebeöl verbaut" not in bericht_zu(ins, lk, ka)
      and "60.000 km" in ge_i.beschreibung and ge_i.quellen, ge_i.titel)
# Neutrale Gegenprobe: anderes Fahrzeugmerkmal (Antrieb), anderer Wartungsname -- gleiche allgemeine Ableitung, keine Namensregel
_ins_n = build_insights(None, None, [], REQ, check_typ="kauf", canonical_rueckrufe=[],
                        web_recherche=TechnischeRecherche(ausgeloest_durch="db_miss", fakten=[
                            fakt("Haldex-Öl", "Haldex-Öl wechseln: alle 40.000 km oder 3 Jahre", "quelle-h.de", "ersetzen")]))
_h = next((i for i in _ins_n if i.kategorie == "web_wartung"), None)
from app.ausstattung_praesenz import abhaengigkeit as _abh  # noqa: E402
_a, _ = _abh("Haldex-Öl")
check("B10b Gegenprobe anderes Merkmal (Antrieb, anderer Wartungsname): Bedingung aus der Klasse der Abhängigkeit, Präsenz unbekannt",
      _h is not None and _a is not None and _a.art == "antrieb"
      and _h.titel.startswith(f"Falls das Fahrzeug folgendes Merkmal hat ({_a.klasse}, am Fahrzeug nicht bestätigt): ")
      and _h.presence_state == "unknown" and "Haldex" not in _h.titel.split(":")[0], _h.titel if _h else "kein Insight")
import app.ausstattung_praesenz as _ap  # noqa: E402
check("B10c Ausstattung (nicht Wartung) bleibt unverändert: 'Falls EDC vorhanden: ' und Nicht-Wartung nutzt weiter 'verbaut'",
      _ap.bedingung("EDC", next(x for x in _ap.ABHAENGIGKEITEN if x.art == "ausstattung")) == "Falls EDC vorhanden: "
      and _ap.bedingung("Automatikgetriebeöl", _a) == "Falls Automatikgetriebeöl verbaut: ")

# ══ C. Bündelung: nichts geht durch das Limit verloren ═══════════════════════════════════════════════════
print("\n== C Bündelung (sechs Rückrufe belegen alle Einzelplätze) ==")
ins2, lk2, ka2, antwort2 = kette([rueckruf(k) for k in range(1, 7)])
alle = [f.bauteil for f in FAKTEN]
for bereich in ("verkaeuferfragen", "dokumente"):
    pl = antwort2["kaufaktionen"][bereich]
    sammel = [a for a in pl["basis"] if a["id"].endswith("basis-wartungsrest")]
    check(f"C1 {bereich}: genau ein Sammelpunkt, jede der fünf Wartungen (inkl. Antriebsriemen, Getriebeöl) ist benannt",
          len(sammel) == 1 and all(f"„{b}“" in sammel[0]["aktion"] for b in alle), str([a["id"] for a in pl["basis"]]))

for bereich in ("verkaeuferfragen", "dokumente"):
    sammel = [a for a in antwort2["kaufaktionen"][bereich]["basis"] if a["id"].endswith("basis-wartungsrest")]
    txt = sammel[0]["aktion"] if sammel else ""
    check(f"C2 {bereich}: jede Wartung mit Tätigkeit und Intervall laut Quelle, ohne gekappte Zitate ('...')",
          all(f"„{b}“ ({t}; laut Quelle: " in txt for b, t in (("Motoröl", "Wechsel"), ("Ventilspiel", "Prüfung"),
                                                           ("Nebenaggregat-Antriebsriemen", "Wechsel"), ("Automatikgetriebeöl", "Prüfung"),
                                                           ("Kühlmittel", "Prüfung")))
          and "..." not in txt and "Erstintervall 105.000 km oder 6 Jahre, danach 15.000 km / 1 Jahr" in txt
          and "Anwendbarkeit ungeklärt, zuerst klären: nur Fahrzeuge mit Zusatzheizung" in txt
          and txt.count("Marktvorbehalt der Quelle, Geltung in Deutschland nicht bestätigt") == 3
          and "zusätzlich bedingte Regel, Voraussetzung ungeklärt: 15.000 km oder 1 Jahr" in txt, txt)
for bereich in ("verkaeuferfragen", "dokumente"):
    sp = next(a for a in antwort2["kaufaktionen"][bereich]["basis"] if a["id"].endswith("basis-wartungsrest"))
    txt = sp["titel"] + " " + sp["aktion"]
    check(f"C3 {bereich}: der Betriebsstoff wird nicht als verbautes Bauteil bezeichnet; die Bedingung nennt das Fahrzeugmerkmal "
          f"(Getriebeart), ungeklärt",
          "Automatikgetriebeöl verbaut" not in txt and "Öl verbaut" not in txt
          and "Falls das Fahrzeug folgendes Merkmal hat (Automatik-/Doppelkupplungsgetriebe, am Fahrzeug nicht bestätigt): „Automatikgetriebeöl“" in txt,
          sp["aktion"])
sp_f = next(a for a in antwort2["kaufaktionen"]["verkaeuferfragen"]["basis"] if a["id"].endswith("basis-wartungsrest"))
sp_d = next(a for a in antwort2["kaufaktionen"]["dokumente"]["basis"] if a["id"].endswith("basis-wartungsrest"))
check("C4 Sammelfrage setzt keine Durchführung voraus (ergebnisoffen), Einleitung fragt nach Durchführung",
      sp_f["titel"] == "Wurden die folgenden Wartungsarbeiten durchgeführt, und wenn ja, wann und bei welchem Kilometerstand?"
      and "jeweils nach Durchführung, Datum, Kilometerstand und Beleg fragen" in sp_f["aktion"] and "gemacht" not in sp_f["titel"],
      sp_f["titel"])
check("C5 Sammel-Dokument: Nachweis nur 'falls durchgeführt'",
      "falls durchgeführt" in sp_d["titel"] and "jeweils falls durchgeführt Rechnung oder Serviceheft-Eintrag" in sp_d["aktion"])
check("C6 beide Sammelpunkte nennen alle fünf Wartungen einzeln mit Tätigkeit, Intervall und Vorbehalt (unverändert)",
      all(f"„{b}“ (" in t["aktion"] for t in (sp_f, sp_d)
          for b in ("Motoröl", "Ventilspiel", "Nebenaggregat-Antriebsriemen", "Automatikgetriebeöl", "Kühlmittel"))
      and sp_f["aktion"].count("laut Quelle: Erstintervall") == 5)
# Einzelaktion: gleiche Korrektur der Präsenz-Bedingung
ge_f = next(a for a in antwort["kaufaktionen"]["verkaeuferfragen"]["fahrzeugspezifisch"] if "Automatikgetriebeöl" in a["titel"])
ge_d = next(a for a in antwort["kaufaktionen"]["dokumente"]["fahrzeugspezifisch"] if "Automatikgetriebeöl" in a["titel"])
check("C7 Einzelfrage und Einzeldokument zum Getriebeöl: Fahrzeugmerkmal statt 'verbaut'",
      ge_f["titel"].startswith("Falls das Fahrzeug folgendes Merkmal hat (Automatik-/Doppelkupplungsgetriebe, am Fahrzeug nicht bestätigt): Wurde")
      and ge_d["titel"].startswith("Falls das Fahrzeug folgendes Merkmal hat (Automatik-/Doppelkupplungsgetriebe, am Fahrzeug nicht bestätigt): ")
      and "verbaut" not in ge_f["titel"] + ge_d["titel"], ge_f["titel"])
print("  SAMMELPUNKT (Fragen):", next(a for a in antwort2["kaufaktionen"]["verkaeuferfragen"]["basis"] if a["id"].endswith("basis-wartungsrest"))["aktion"])

# ══ D. HU ═════════════════════════════════════════════════════════════════════════════════════
print("\n== D HU ==")
heute = dt.date(2026, 11, 15)


def hu_zeile(roh, baujahr=2017):
    b = bewerte_hu(roh, heute, baujahr)
    z = vt.baue_zeilen(REQ, None, None, hu=b, fakten=None)
    return b, next((x for x in z if x.kriterium == "HU"), None)


b, z = hu_zeile("08/2027")
check("D1 PLAUSIBEL: 'Angabe plausibel, nicht geprüft', Inseratangabe gekennzeichnet, Handlung 'HU-Bericht oder Plakette'",
      b.status == PLAUSIBEL and z is not None and z.einordnung == vt.HU_PLAUSIBEL_NICHT_GEPRUEFT
      and "nicht geprüft" in z.einordnung and "HU-Bericht oder Plakette" in z.einordnung
      and "(Angabe im Inserat)" in z.angabe, str(z))
check("D2 PLAUSIBEL: weder '✓' noch 'passt' in der Zeile; die gesetzliche Referenz bleibt",
      z is not None and "✓" not in vt.als_markdown([z]) and "passt" not in vt.als_markdown([z])
      and "24 Monate" in z.zelle_referenz() and "gesetzliche Regel" in z.zelle_referenz())
check("D3 generisches PASST bleibt unverändert definiert", vt.PASST == "✓ passt")
b2, z2 = hu_zeile("01/2026")
check("D4 abgelaufen unverändert", b2.status == ABGELAUFEN and z2.einordnung == "⚠ abgelaufen" and z2.angabe == "bis 01/2026")
b3, z3 = hu_zeile("11/2035", baujahr=2020)
check("D5 ungewöhnlich weit unverändert", b3.status == UNGEWOEHNLICH_WEIT
      and z3.einordnung == "⚠ ungewöhnlich weit: im Fahrzeugschein prüfen" and z3.angabe == "bis 11/2035")
b4, z4 = hu_zeile("abc")
check("D6 unlesbar unverändert", b4.status == UNLESBAR and z4.einordnung == "⚠ nicht eindeutig lesbar")

# ══ E. Ältere Antworten ohne neue Felder ═══════════════════════════════════════════════════
print("\n== E Rückfall für ältere Hinweise ==")
alt = Wartungshinweis(bauteil="Motoröl", status="darueber", punkt_km=15000, differenz_km=67000,
                      intervall_text="alle 15.000 km", hinweis="Alter, voller Hinweistext.", herkunft="web_wartung",
                      evidence_id="x")
check("E1 Modell ohne neue Felder ist gültig; Statusfelder bleiben None (kein erfundener Zeitstatus)",
      alt.km_status is None and alt.zeit_status is None and alt.kurz is None and alt.regeln == [] and alt.grenzen == [])
check("E2 Kaufaktionen fallen ohne Kurzform auf den bisherigen Text (voller Hinweis + Handlung) zurück",
      _wartung_frage_text(alt) == "Alter, voller Hinweistext. Nach Datum, Kilometerstand und Beleg fragen."
      and _wartung_dokument_text(alt) == "Alter, voller Hinweistext. Rechnung oder Eintrag im Serviceheft mit Datum und "
                                         "Kilometerstand zeigen lassen.")
roundtrip = Wartungshinweis.model_validate(alt.model_dump(exclude={"km_status", "km_text", "zeit_status", "zeit_text", "kurz",
                                                                   "handlung", "grenzen", "regeln"}))
check("E3 Antwort-JSON ohne die neuen Schlüssel lässt sich weiter laden", roundtrip.hinweis == alt.hinweis)

print("\n" + ("ALLE GRÜN" if not fehler else f"{len(fehler)} FEHLER: {fehler}"))
sys.exit(1 if fehler else 0)
