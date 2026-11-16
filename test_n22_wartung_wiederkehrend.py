"""
N22 -- wiederkehrende Wartungsintervalle strukturiert erhalten und bis zur Kundenausgabe korrekt darstellen.

Befund (Export 16.11.2026, Lexus RC F): Quelle "Automatikgetriebeöl Alle 60.000 km / 4 Jahre", Kurzform
"Erstintervall 60.000 km oder 4 Jahre". Der Wortlaut blieb in Extraktion und Details erhalten; verloren ging die
Wiederholung in der Struktur (`WartungsRegel` kannte kein Wiederholungsmerkmal), sichtbar in Kurzform, Kilometertext,
Fragen und Dokumenten. Reale Wortlaute: lex-sw-00de.pdf (Lexus Garantie- und Serviceheft Schweiz, Deutsch,
tools.lexus.ch), dieselbe Extraktion wie test_wartung_regime_motoroel; [SYNTH] = synthetische Gegenprobe.

Netzsperre (extern) + Temp-DB VOR den App-Importen. Lauf:
    python scripts/test_isolated.py test_n22_wartung_wiederkehrend.py
"""
import os
import socket
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="n22_")
os.environ.setdefault("AUTO_KI_DB_PATH", os.path.join(_TMP, "t.db"))
os.environ.setdefault("AUTO_KI_CHROMA_PATH", os.path.join(_TMP, "chroma"))
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
import app.laufleistung as ll  # noqa: E402
from app.evidence import build_insights  # noqa: E402
from app.kaufaktionen import _kompakt_aus_hinweis, build_kaufaktionen  # noqa: E402
from app.kaufcheck_bericht import bericht  # noqa: E402
from app.laufleistung import WartungsRegel, build_laufleistungskontext  # noqa: E402
from app.models import EvidenceQuelle, KaufCheckRequest, TechnischeRecherche, WartungsRegelInfo, Wartungshinweis, WebFakt  # noqa: E402
from app.vehicle_identity import VehicleIdentity  # noqa: E402

_orig_alter = ll.fahrzeugalter
ll.fahrzeugalter = lambda bj, heute_jahr=None: _orig_alter(bj, heute_jahr=2026)

fehler: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        fehler.append(name)


# Dieselbe Liste wie test_wartung_regime_motoroel/test_n21 (der volle Hinweis sagt ausdrücklich, dass sich "weder eine
# Anzahl noch ein Rückstand" ableiten lässt) plus ausdrückliche Fälligkeitsformulierungen einer wiederkehrenden Regel.
VERBOTEN = ("fällig", "überfällig", "versäumt", "nicht durchgeführt", " fehlt ", "nächste Fälligkeit", "nächster Termin",
            "erneut fällig", "wieder fällig")


def sauber(t):
    return not any(w in (t or "") for w in VERBOTEN)


# ── ECHTE Wortlaute (lex-sw-00de.pdf, Seiten 15-17, extrahierter Text) ──────────────────────────────────────────────
GELTUNG = " Geltungsbereich laut Quelle: Für normale Einsatzbedingungen."
ATF = "Automatikgetriebeöl Alle 60.000 km / 4 Jahre." + GELTUNG
DIFF = ("Hinterachsdifferenzialöl (außer ES 300h, NX 350h, NX 450h+, RX 300 2WD, RX 450h, RX 450hL, UX 200, UX 250h) "
        "Alle 30.000 km / 4 Jahre." + GELTUNG)
VENTIL = "Ventilspiel (Inspektion mit Messfühler, LC 500, RC F) Alle 90.000 km / 6 Jahre." + GELTUNG
RIEMEN = ("Nebenaggregat-Antriebsriemen (außer ES 300h, UX 250h, NX 350h, NX 450h+) Alle 105.000 km / 6 Jahre "
          "Danach alle 15.000 km / jährlich." + GELTUNG)
OEL_ZS = ("Lexus Care-Zwischenservice: alle 15.000 km oder einmal jährlich, je nachdem, was zuerst eintritt. Dieser "
          "Service beinhaltet einen Öl- und Filterwechsel sowie zusätzliche Inspektionen von Lenkung, Bremsen, "
          "Beleuchtung und Reifen")
OEL_FN = ("Motoröl und -filter: Bei Fahrzeugen mit Ölwechsel-Managementsystem: Motoröl- und -ölfilter wechseln (mit "
          "Fitness- und Sicherheitskontrollen), wenn die Ölwechsel-Hinweismeldung erscheint (zumindest alle 15.000 km / "
          "1 Jahr), falls für Ihr Land zutreffend.")

# ══ A. Struktur: Wiederholung nur am ausgewerteten Wert ══════════════════════════════════════════════════════════
print("== A WartungsRegel.wiederkehrend ==")
for name, text, km, zeit in (("ATF", ATF, 60000, (4, "Jahre")), ("Differenzialöl", DIFF, 30000, (4, "Jahre")),
                             ("Ventilspiel", VENTIL, 90000, (6, "Jahre")), ("Motoröl Zwischenservice", OEL_ZS, 15000, (1, "Jahre")),
                             ("Motoröl Fußnote (bedingt)", OEL_FN, 15000, (1, "Jahre"))):
    r = WartungsRegel(" ".join(text.split()))
    check(f"A1 [ECHT] {name}: wiederkehrend, Kilometer/Zeit/Verknüpfung unverändert",
          r.wiederkehrend is True and r.km and r.km[0] == km and r.zeit == zeit and r.verbinder == "oder",
          f"{r.wiederkehrend} {r.km} {r.zeit} {r.verbinder}")
r_fn = WartungsRegel(" ".join(OEL_FN.split()))
check("A2 [ECHT] Motoröl-Fußnote bleibt BEDINGT (Hinweismeldung/Land) -- die Wiederholung ändert die Bedingung nicht",
      r_fn.bedingt is True)
r_rb = WartungsRegel(" ".join(RIEMEN.split()))
check("A3 [ECHT] Riemen: ausdrückliches Erst-/Folgeintervall ('Danach ...') ist KEINE reine Wiederholung; Erstintervall "
      "105.000 km / 6 Jahre bleibt",
      r_rb.wiederkehrend is False and r_rb.km[0] == 105000 and r_rb.zeit == (6, "Jahre"))
for name, text, erwartet in (
        ("einmaliges Intervall", "Zahnriemen wechseln bei 150.000 km", False),
        ("'alle' einer ANDEREN Tätigkeit/eines anderen Werts", "Zahnriemen wechseln bei 150.000 km (Sichtprüfung alle 15.000 km)",
         False),
        ("'alle' in der Modellbedingung", "Gilt für alle Modelle außer Hybrid: Bremsflüssigkeit nach 2 Jahren wechseln", False),
        ("'alle' vor einer Variantenzahl", "Für alle 4 Varianten: Getriebeöl 60.000 km", False),
        ("Zeitangabe 'jährlich' als erster Wert", "Klimaanlage prüfen: jährlich", True),
        ("'jeden' unmittelbar vor dem Wert", "Luftfilter jeden 30.000 km prüfen", True)):
    r = WartungsRegel(text)
    check(f"A4 [SYNTH] {name}: wiederkehrend={erwartet}", r.wiederkehrend is erwartet, f"{r.wiederkehrend} km={r.km}")
r_x = WartungsRegel("Zahnriemen wechseln bei 150.000 km (Sichtprüfung alle 15.000 km)")
check("A5 [SYNTH] der ausgewertete Wert bleibt der der Tätigkeit (150.000 km), nicht der wiederkehrende Fremdwert",
      r_x.km[0] == 150000)
check("A6 abwärtskompatibel: ältere Regel-/Hinweisdaten ohne Feld laden mit wiederkehrend=False/None",
      WartungsRegelInfo(art="grund", erstintervall="60.000 km oder 4 Jahre", wortlaut="x").wiederkehrend is False
      and Wartungshinweis(bauteil="x", status="darueber", intervall_text="x", hinweis="x", herkunft="web_wartung",
                          evidence_id="x").wiederkehrend is None
      and ll.regel_intervall_text(WartungsRegelInfo(art="grund", erstintervall="60.000 km oder 4 Jahre", wortlaut="x"))
      == "60.000 km oder 4 Jahre")

# ══ B. Kette bis Karte, Verkäuferfragen, Dokumente, Bündelzeile, Bericht ═══════════════════════════════════════════
print("\n== B Kette (Karte, Fragen, Dokumente, Bericht) ==")
URL = "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf"
MARKT = "Die Quelle schränkt sich selbst ein (\"falls für Ihren Markt zutreffend\")."


def fakt(bauteil, aussage, taetigkeit):
    return WebFakt(kategorie="wartung", bauteil=bauteil, aussage=" ".join(aussage.split()), confidence="mittel",
                   quellen=[EvidenceQuelle(typ="web", url=URL, titel="Lexus Garantie- und Serviceheft", qualitaet="Hersteller")],
                   taetigkeit=taetigkeit, regionale_geltung="unresolved", herkunftsregion=MARKT)


FAKTEN = [fakt("Automatikgetriebeöl", ATF, "pruefen"), fakt("Hinterachsdifferenzialöl", DIFF, "ersetzen"),
          fakt("Ventilspiel", VENTIL, "pruefen"), fakt("Nebenaggregat-Antriebsriemen", RIEMEN, "pruefen"),
          fakt("Motoröl", OEL_ZS + " " + ll.WEITERE_STELLE_MARKER + " " + OEL_FN, "ersetzen")]
REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, motor="5.0 V8, Heckantrieb",
                       kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", ausstattung=[], vorbesitzer=2,
                       scheckheftgepflegt=True)
ins = build_insights(None, None, [], REQ, check_typ="kauf",
                     web_recherche=TechnischeRecherche(ausgeloest_durch="db_miss", fakten=FAKTEN), canonical_rueckrufe=[])
lk = build_laufleistungskontext(REQ, ins, heute_jahr=2026)
ka = build_kaufaktionen(REQ, None, None, ins, laufleistungskontext=lk)
w = {x.bauteil: x for x in lk.wartungshinweise}
check("B0 Vorbedingung: alle fünf Wartungen erreichen die Karte", set(w) == {f.bauteil for f in FAKTEN}, str(sorted(w)))
for b, erwartet, taet in (("Automatikgetriebeöl", "Prüfung: alle 60.000 km oder 4 Jahre.", "pruefen"),
                          ("Hinterachsdifferenzialöl", "Wechsel: alle 30.000 km oder 4 Jahre.", "ersetzen"),
                          ("Ventilspiel", "Prüfung: alle 90.000 km oder 6 Jahre.", "pruefen"),
                          ("Motoröl", "Wechsel: alle 15.000 km oder 1 Jahr.", "ersetzen")):
    x = w[b]
    check(f"B1 [ECHT] {b}: Karte '{erwartet}', wiederkehrend markiert, kein 'Erstintervall'/'Erstpunkt' in Kurzform und "
          f"Kilometertext; Tätigkeit, Marktvorbehalt, Quelle unverändert",
          x.intervall_kurz == erwartet and x.wiederkehrend is True and "Erstintervall" not in x.kurz
          and "Erstpunkt" not in (x.km_text or "") and x.taetigkeit == taet
          and any(g.startswith("Marktvorbehalt") for g in x.grenzen) and x.quellen and x.quellen[0].url == URL
          and all(r.wiederkehrend for r in x.regeln) and sauber(x.kurz) and sauber(x.hinweis),
          f"{x.intervall_kurz} | {x.km_text}")
atf = w["Automatikgetriebeöl"]
check("B2 [ECHT] ATF: Kilometertext sagt nur 'mindestens einmal erreicht' (keine Anzahl, keine nächste Fälligkeit, ohne "
      "letzte Durchführung); Zeitstatus und voller Hinweis unverändert",
      atf.km_status == "darueber"
      and atf.km_text == "Intervall (60.000 km) bei dieser Laufleistung rechnerisch mindestens einmal erreicht"
      and atf.zeit_status == "erreicht" and "zeitliche Alternative (oder 4 Jahre)" in atf.zeit_text
      and "weder eine Anzahl noch ein Rückstand" in atf.hinweis, f"{atf.km_text} | {atf.hinweis[:120]}")
rb = w["Nebenaggregat-Antriebsriemen"]
check("B3 [ECHT] Riemen unverändert: 'Erstintervall 105.000 km oder 6 Jahre, danach 15.000 km / 1 Jahr', Erstpunkt-Text",
      rb.intervall_kurz == "Prüfung: Erstintervall 105.000 km oder 6 Jahre, danach 15.000 km / 1 Jahr."
      and rb.wiederkehrend is False and (rb.km_text or "").startswith("Erstpunkt (105.000 km)")
      and rb.regeln[0].folgeintervall == "15.000 km / 1 Jahr", f"{rb.intervall_kurz} | {rb.km_text}")
mo = w["Motoröl"]
check("B4 [ECHT] Motoröl: bedingte Zusatzregel bleibt bedingt und getrennt (Wortlaut Hinweismeldung/Land), Grenzen unverändert",
      [r.art for r in mo.regeln] == ["grund", "bedingt"] and "Hinweismeldung" in mo.regeln[1].wortlaut
      and any("zusätzliche bedingte Regel" in g for g in mo.grenzen), str([(r.art, r.wiederkehrend) for r in mo.regeln]))


def aktion(bereich, bauteil):
    return next((a for a in getattr(ka, bereich).fahrzeugspezifisch if f"„{bauteil}“" in a.titel or bauteil in a.titel), None)


f_atf, d_atf = aktion("verkaeuferfragen", "Automatikgetriebeöl"), aktion("dokumente", "Automatikgetriebeöl")
check("B5 [ECHT] Verkäuferfrage ATF: 'Laut Quelle: alle 60.000 km oder 4 Jahre', Anlass mit 'mindestens einmal erreicht', "
      "Frage nach Durchführung (keine Fälligkeit)",
      f_atf is not None and f_atf.aktion.startswith("Laut Quelle: alle 60.000 km oder 4 Jahre. Anlass: Intervall (60.000 km)")
      and "Erstintervall" not in f_atf.aktion and "Erstpunkt" not in f_atf.aktion and "bereits geprüft" in f_atf.titel
      and sauber(f_atf.aktion), f_atf.aktion if f_atf else "")
check("B6 [ECHT] Dokument ATF: Bezug 'alle 60.000 km oder 4 Jahre', Vorbehalte und Herkunft unverändert",
      d_atf is not None and "Bezug laut Quelle: alle 60.000 km oder 4 Jahre." in d_atf.aktion
      and "Marktbezug: Geltung in Deutschland nicht bestätigt" in d_atf.aktion
      and d_atf.aktion.endswith("nicht aus der geprüften Fahrzeugdatenbank."), d_atf.aktion if d_atf else "")
f_mo = aktion("verkaeuferfragen", "Motoröl")
check("B7 [ECHT] Motoröl-Frage: Zusatzregel als wiederkehrend UND weiterhin ungeklärt",
      f_mo is not None and "eine zusätzliche Regel der Quelle (alle 15.000 km oder 1 Jahr) gilt nur unter einer "
      "Voraussetzung, die für dieses Fahrzeug ungeklärt ist" in f_mo.aktion, f_mo.aktion if f_mo else "")
f_rb = aktion("verkaeuferfragen", "Nebenaggregat-Antriebsriemen")
check("B8 [ECHT] Riemen-Frage unverändert (Erst-/Folgeintervall)",
      f_rb is not None and "Laut Quelle: Erstintervall 105.000 km oder 6 Jahre, danach 15.000 km / 1 Jahr." in f_rb.aktion)
kompakt = _kompakt_aus_hinweis(mo, "Motoröl")
check("B9 [ECHT] Bündelzeile (Sammelpunkt am Limit): Intervall und bedingte Zusatzregel wiederkehrend, Marktvorbehalt bleibt",
      kompakt is not None and "laut Quelle: alle 15.000 km oder 1 Jahr" in kompakt
      and "zusätzlich bedingte Regel, Voraussetzung ungeklärt: alle 15.000 km oder 1 Jahr" in kompakt
      and "Marktvorbehalt der Quelle" in kompakt, kompakt or "")
text = bericht(REQ, VehicleIdentity.from_check_context(None, None, REQ), None, None, ins, ka, [], "kaufen_nach_besichtigung",
               type("PA", (), {"label": None})(), False, None, None, ["Inserat"])
b = " ".join(text.split())
check("B10 [ECHT] Vollbericht: Verkäuferfrage UND Dokument ATF tragen 'alle 60.000 km oder 4 Jahre'; Riemen unverändert; "
      "kein 'Erstintervall 60.000'",
      b.count("alle 60.000 km oder 4 Jahre") >= 2 and "Erstintervall 60.000 km" not in b
      and "Erstintervall 105.000 km oder 6 Jahre, danach 15.000 km / 1 Jahr" in b, "")
import re  # noqa: E402
_WARTUNGSSAETZE = [s for s in re.split(r"(?<=\.)\s+|\s-\s\*\*", b) if "laut Quelle:" in s or "Laut Quelle:" in s]
_wartung_aktionen = [a.aktion for bereich in ("verkaeuferfragen", "dokumente")
                     for a in getattr(ka, bereich).fahrzeugspezifisch if a.kategorie in ("wartung", "web_wartung")]
check("B11 Wartungsaktionen und Wartungssätze des Berichts ohne Fälligkeits-/Versäumnisaussage (Wortgrenze; HU-/Besichtigungs"
      "texte wie 'auffällig' gehören nicht zu N22)",
      _WARTUNGSSAETZE and _wartung_aktionen
      and all(not re.search(r"\b(?:fällig|überfällig|versäumt)\w*", t) and sauber(t)
              for t in _WARTUNGSSAETZE + _wartung_aktionen), str(len(_WARTUNGSSAETZE)))

print("\n" + ("ALLE GRÜN" if not fehler else f"{len(fehler)} FEHLER: " + "; ".join(fehler)))
sys.exit(1 if fehler else 0)
