"""
ADAC-Rückruftabelle: ein strukturierter Rückrufdatensatz ging am 300-Zeichen-Fließtextlimit
von `_saetze()` verloren (ohne Diagnose); nur ein Restsatz ohne Rückrufwort blieb und scheiterte
als `no_recall_signal`. Jetzt wird der Datensatz VOR dem Limit erkannt (`_rueckruf_datensaetze`).

Geprüft (Netzsperre + Temp-DB vor App-Import, kein Provider, keine Live-DB):
  1. echter langer ADAC-Datensatz (Fixture, Zeilen- UND Tabellenform): Datensatz erkannt, Felder
     aus demselben Datensatz, benachbarte Aktion vermischt sich nicht;
  2. Quellenstatus unverändert, keine Referenz, keine bestätigte Betroffenheit/Durchführung;
  3. langer normaler Fließtext: Verhalten unverändert;
  4. die Rückruftabelle erzeugt weiterhin keine allgemeine Schwachstelle.

    python test_rueckruf_datensatz_300_limit.py
"""
import os
import socket
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="rr_ds_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.create_connection = _gesperrt
socket.socket.connect = _gesperrt
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import technical_research as tr  # noqa: E402

_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures",
                    "adac_lexus_rc_f_seitentext_2026-11-08.txt")
_URL = "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/"
_TITEL = "Lexus RC F Ultimate Edition Automatik: Technische Daten | ADAC"

_zeilen = open(_FIX, encoding="utf-8").read().splitlines()
_START = next(i for i, z in enumerate(_zeilen) if z.strip() == "Alle Rückrufe")
_ZEILENFORM = "\n".join(_zeilen[_START:_START + 45])          # echter ADAC-Text, Zeilenform
_TABELLE = (                                                    # echte Felder, Markdown-Tabellenform
    "|  |  |\n --- |\n| Rückrufdatum | März 2018 |\n"
    "| Anlass | Kraftstoffaustritt möglich durch Riss in Pumpenabdeckung |\n"
    "| Betroffene Modelle | GS F L10 (01/16 - 08/18), LC Coupé Z10 (06/17 - 03/25), RC F XC1 (12/14 - 06/25) |\n"
    "| Variante | nur mit 5.0L V8 2UR-GSE Motor |\n"
    "| Bauzeitraum betroffener Fahrzeuge | 05.2014 bis 04.12.2017 |\n"
    "| Zusätzliche Information | Die betroffenen Fahrzeuge sind mit einem 5.0L V8 2UR-GSE Motor und 2 "
    "Kraftstoffhochdruckpumpen ausgestattet. Es besteht Brandgefahr. In der Werkstatt werden die "
    "Kraftstoffhochdruckpumpen überprüft und, falls erforderlich, ausgetauscht. |")

fehler: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        fehler.append(name)


def lauf(kategorie: str, content: str, baujahr: int = 2017):
    ab: list[dict] = []
    f = tr._extrahiere_fakten([{"url": _URL, "title": _TITEL, "content": content}], kategorie,
                              marke="Lexus", modell="RC F", baujahr=baujahr, hubraum="5.0",
                              leistung_ps=477, abgelehnt=ab)
    return f, ab


# ── 1. Datensatz erkannt, Felder aus demselben Datensatz, Nachbar getrennt ─────────────────
for name, text in (("Zeilenform", _ZEILENFORM), ("Tabellenform", _TABELLE)):
    ds = tr._rueckruf_datensaetze(text)
    check(f"{name}: Datensatz erkannt", len(ds) >= 1)
    d = ds[0]
    check(f"{name}: Anlass", d.get("anlass") == "Kraftstoffaustritt möglich durch Riss in Pumpenabdeckung")
    check(f"{name}: Variante 2UR-GSE", "2UR-GSE" in (d.get("variante") or ""))
    check(f"{name}: Bauzeitraum", "2014" in (d.get("bauzeitraum") or "") and "2017" in (d.get("bauzeitraum") or ""))
    check(f"{name}: Modelle aus demselben Datensatz", "RC F XC1" in (d.get("modelle") or ""))

ds2 = tr._rueckruf_datensaetze(_ZEILENFORM)
check("Zeilenform: zwei benachbarte Datensätze", len(ds2) == 2, str(len(ds2)))
if len(ds2) == 2:
    check("Nachbar: Felder nicht vermischt",
          "Falzkanten" in ds2[1]["anlass"] and "Falzkanten" not in ds2[0]["anlass"]
          and "2UR-GSE" not in (ds2[1].get("variante") or "")
          and "2013" in ds2[1]["bauzeitraum"] and "2013" not in ds2[0]["bauzeitraum"])

for name, text in (("Zeilenform", _ZEILENFORM), ("Tabellenform", _TABELLE)):
    fakten, ab = lauf("rueckruf", text)
    check(f"{name}: genau EIN Rückruf-Fakt (Nachbaraktion Baujahr-fremd, nicht vermischt)", len(fakten) == 1, str(len(fakten)))
    if fakten:
        f = fakten[0]
        check(f"{name}: Aussage trägt Anlass, Variante, Zeitraum, Modelle", all(
            x in f.aussage for x in ("Pumpenabdeckung", "2UR-GSE", "05.2014 bis 04.12.2017", "RC F XC1")))
        check(f"{name}: Nachbaraktion nicht in der Aussage", "Falzkanten" not in f.aussage)
        check(f"{name}: Bauteil kommt aus Anlass/Zusatz, nicht aus der Variante ('Motor')",
              (f.bauteil or "").lower() != "motor", str(f.bauteil))
        # ── 2. nichts bestätigt, nichts abgeleitet ──
        check(f"{name}: Quellenstatus unverändert (Fachquelle ADAC, nie 'hoch')", f.confidence != "hoch", f.confidence)
        check(f"{name}: nur 'möglich betroffen', keine bestätigte Betroffenheit",
              f.applicability == "vehicle_possible", str(f.applicability))
        check(f"{name}: keine Rückrufnummer abgeleitet",
              not tr._rueckruf_referenz(f.aussage) and not any(
                  getattr(f, a, None) for a in ("kba_referenz", "referenz", "rueckruf_nr")))
        check(f"{name}: kein 'verified'/'bestaetigt' im Ergebnis", "verified" not in repr(f).lower())
        check(f"{name}: keine Durchführung/FIN-Betroffenheit behauptet",
              not any(w in f.aussage.lower() for w in ("durchgeführt", "durchgefuehrt", "fin ", "erledigt")))
    check(f"{name}: Nachbaraktion mit ausdrücklichem Grund abgelehnt",
          any(a.get("grund") == "baujahr_ausserhalb" and "Falzkanten" in a.get("satz", "") for a in ab)
          or name == "Tabellenform")

# Baujahr außerhalb des Fensters: derselbe Datensatz erzeugt dann keinen Fakt.
f_alt, ab_alt = lauf("rueckruf", _TABELLE, baujahr=2012)
check("Baujahr 2012: Datensatz-Fenster schließt aus, kein Fakt", len(f_alt) == 0)

# ── 3. langer normaler Fließtext: unverändert ──────────────────────────────────────────────
lang = ("Der Hersteller ruft Fahrzeuge zurück, weil die Kraftstoffleitung unter bestimmten Umständen "
        "undicht werden kann und dadurch Kraftstoff austritt, was bei weiterer Nutzung eine erhebliche "
        "Brandgefahr darstellt; betroffen sind nach Angaben des Herstellers sehr viele Fahrzeuge "
        "verschiedener Baureihen und Baujahre, die in mehreren Werken gebaut wurden und die in der "
        "Werkstatt kostenlos geprüft werden müssen.")
check("Fließtext >300 Zeichen hat keinen Datensatz", tr._rueckruf_datensaetze(lang) == [])
check("Fließtext: Satzliste identisch zum alten Verhalten", tr._rueckruf_saetze(lang) == tr._saetze(lang))
check("Fließtext >300 Zeichen wird weiterhin NICHT aufgeweitet", len(tr._rueckruf_saetze(lang)) == 0)
kurz = "Lexus ruft den RC F wegen der Kraftstoffleitung zurück. Betroffen sind Fahrzeuge der Baujahre 2015 bis 2018."
check("kurzer Fließtext: Satzliste identisch", tr._rueckruf_saetze(kurz) == tr._saetze(kurz))
ein_feld = "Hinweis zum Anlass der Reparatur: Der Anlass war ein verlorener Schlüssel und kein Rückruf."
check("einzelnes 'Anlass' im Fließtext ist kein Datensatz", tr._rueckruf_datensaetze(ein_feld) == [])

# ── 4. Rückruftabelle erzeugt keine allgemeine Schwachstelle ──────────────────────────────
# Tabellenform: keine. Zeilenform: der Fließtext-Absatz 'Zusätzliche Information' erzeugte auf
# 606a6bb EINE Schwachstelle ('Es besteht die Möglichkeit, dass ein Pulsationsdämpfer ...'). Er ist
# jetzt an seinen Datensatz gebunden (`_im_rueckruf_zusatz`) -- separate Schwachstellen derselben
# Seite prüft test_rueckruf_abgleich_amtlich.py (VIII).
fw_t, _ = lauf("schwachstelle", _TABELLE)
check("Tabellenform: keine Schwachstelle aus der Rückruftabelle", len(fw_t) == 0, str(len(fw_t)))
fw_z, _ = lauf("schwachstelle", _ZEILENFORM)
check("Zeilenform: kein Schwachstellen-Fakt stammt aus dem Datensatz (Anlass/Variante/Modelle)",
      not any(("Rückruf März" in f.aussage or "Betroffene Modelle" in f.aussage
               or "Variante:" in f.aussage) for f in fw_z))
check("Zeilenform: Altfall behoben -- der Zusatzabsatz des Rückrufdatensatzes erzeugt keine Schwachstelle",
      len(fw_z) == 0, str([f.aussage[:60] for f in fw_z]))

print()
print("ALLE GRÜN" if not fehler else f"{len(fehler)} FEHLER: {fehler}")
sys.exit(1 if fehler else 0)
