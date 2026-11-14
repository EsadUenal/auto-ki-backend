"""
Volltext-Paket der Schwachstellenrecherche: mit der SUCHE gelieferter Originaltext (`include_raw_content`)
statt nur des Snippets -- bei unveraenderten N19-Regeln (Reichweite, Bauteilbindung, Verneinung, Modell-/
Zeitraumpruefung) in Haupt- und Candidate-Pfad.

Geprueft wird OFFLINE und mit gestubbtem Provider:
  * angefordert != geliefert: Der Stub liefert `raw_content` nur, wenn es angefordert wurde und simuliert
    auch fehlenden/leeren/zu kurzen Volltext. Ob der echte Provider den Seitentext bei
    `exclude_domains` ohne `include_domains` zuverlaessig mitliefert, ist HIER NICHT geprueft.
  * Anfragefolge unveraendert (kein zusaetzlicher Provideraufruf), kein Extract-Aufruf fuer diese Treffer.
  * ECHT: ADAC-Volltext (zwei Formate), Rettungsdatenblatt, 16 Tavily-Snippets aus Fixtures vom
    08.11.2026. Ein ECHTER positiver allgemeiner Modellbefund liegt lokal NICHT vor: alle Positivfaelle
    sind SYNTHETISCH (gekennzeichnet).

    python test_schwachstellen_volltext.py
"""
import asyncio
import json
import logging
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="vt_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


_paar = threading.local()
_orig_paar, _orig_connect = socket.socketpair, socket.socket.connect


def _socketpair(*a, **k):
    _paar.aktiv = True
    try:
        return _orig_paar(*a, **k)
    finally:
        _paar.aktiv = False


def _connect(sock, adresse):
    if getattr(_paar, "aktiv", False):
        return _orig_connect(sock, adresse)
    return _gesperrt()


socket.socketpair, socket.socket.connect = _socketpair, _connect
socket.create_connection = _gesperrt
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import database as _db  # noqa: E402

_db.ensure_tables()

import app.kaufcheck as kc  # noqa: E402
import app.technical_research as tr  # noqa: E402
from app.models import KaufCheckRequest, KaufCheckResponse  # noqa: E402
from app.technical_research import (  # noqa: E402
    _ORIGINALTEXT_MAX_ZEICHEN, _extrahiere_fakten, _mit_originaltext, _schwachstelle_candidate_fakten)

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


FIX = Path(__file__).parent / "tests" / "fixtures"
ADAC_TEXT = (FIX / "adac_lexus_rc_f_seitentext_2026-11-08.txt").read_text(encoding="utf-8")
RB = json.loads((FIX / "live_rollenbeleg_treffer_2026-11-08.json").read_text(encoding="utf-8"))
WJ = json.loads((FIX / "live_wartung_treffer_2026-11-08.json").read_text(encoding="utf-8"))
RD = (FIX / "lexus_rettungsdatenblaetter_extract_2026-11-08.txt").read_text(encoding="utf-8")
ADAC_URL = "https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/339429/"
HOCH = "https://www.auto-motor-und-sport.de/gebrauchtwagen/lexus-rc-f/"
M = "des Lexus RC F"
FUELL = ("Der Lexus RC F wird mit einem 5,0-Liter-V8 angeboten und leistet 477 PS. "
         "Die Verarbeitung gilt als hochwertig und der Innenraum bietet vier Sitzplätze. ") * 4   # > 400 Zeichen
BARE = f"Die Wasserpumpe {M} ist undicht."
RAHMEN = "Typische Schwachstellen des Lexus RC F:"


def hit(url, titel, snippet, raw=None):
    h = {"url": url, "title": titel, "content": snippet}
    if raw is not None:
        h["raw_content"] = raw
    return h


def fakten(treffer, kategorie="schwachstelle", titel_unused=None):
    ab: list[dict] = []
    f = _extrahiere_fakten(treffer, kategorie, marke="Lexus", modell="RC F", baujahr=2017,
                           generation="UXC1", abgelehnt=ab)
    return f, ab


def bauteile_aus(h):
    """Originaltext aufbereiten (wie im Provider) und durch die echte Extraktion schicken."""
    treffer, _ = _mit_originaltext([h])
    return [x.bauteil for x in fakten(treffer)[0]], fakten(treffer)[1]


print("=== A) Aufbereitung: brauchbar / Fallback / Kuerzung / eine Quelle ===")
snip = "Lexus RC F Gebrauchtwagen: Überblick zu Motor, Fahrwerk und Ausstattung für Käufer."
t1, s1 = _mit_originaltext([hit(HOCH, "Lexus RC F", snip, FUELL + RAHMEN)])
check("A1 brauchbarer Originaltext ersetzt das Snippet (eine Quelle, kein zweiter Beleg)",
      len(t1) == 1 and t1[0]["content"].startswith("Der Lexus RC F") and snip not in t1[0]["content"]
      and (s1["treffer"], s1["volltext"], s1["snippet_fallback"], s1["gekuerzt"]) == (1, 1, 0, 0), str(s1))
# Korrigiert (Volltext-Korrektur): Laenge allein entscheidet nicht. Vorher galten "zu kurz" und "kuerzer als das
# Snippet" als unbrauchbar; jetzt nur noch fehlender bzw. technisch unbrauchbarer Text (leer, kaum Woerter,
# Abruf-Fehlerseite, ein einzelnes Riesenwort).
unbrauchbar = [hit(HOCH + "/a", "A", snip, None), hit(HOCH + "/b", "B", snip, ""), hit(HOCH + "/c", "C", snip, "  \n  "),
               hit(HOCH + "/d", "D", snip, "Access denied. Please enable JavaScript and cookies to continue."),
               hit(HOCH + "/e", "E", snip, "y" * 450), hit(HOCH + "/f", "F", snip, "OK")]
t2, s2 = _mit_originaltext(unbrauchbar)
check("A2 fehlend / leer / Fehlerseite / kein Text -> Snippet bleibt, als Fallback gezaehlt (getrennt nach fehlt/unbrauchbar)",
      [x["content"] for x in t2] == [x["content"] for x in unbrauchbar] and s2["snippet_fallback"] == 6
      and s2["volltext"] == 0 and s2["fallback_fehlt"] == 3 and s2["fallback_unbrauchbar"] == 3, str(s2))
KURZ = f"{RAHMEN} {BARE}"
t2b, s2b = _mit_originaltext([hit(HOCH + "/k", "K", "Lexus RC F Gebrauchtwagen: " + "Überblick zu Motor und Ausstattung. " * 6, KURZ)])
check("A2b SYNTHETISCH: kurzer, kuerzerer Originaltext (< 400 Zeichen, kuerzer als das Snippet) ist verwertbar und ersetzt",
      s2b["volltext"] == 1 and t2b[0]["content"] == KURZ and len(KURZ) < 400, str(s2b))
t3, s3 = _mit_originaltext([hit(HOCH, "Lexus RC F", snip, "Absatz eins. " * 50 + "\n\n" + "x " * _ORIGINALTEXT_MAX_ZEICHEN)])
check("A3 Kuerzung nur ueber der Grenze, gezaehlt und nicht still; Anfang bleibt erhalten",
      s3["gekuerzt"] == 1 and len(t3[0]["content"]) <= _ORIGINALTEXT_MAX_ZEICHEN
      and t3[0]["content"].startswith("Absatz eins."))
t4, s4 = _mit_originaltext([hit(HOCH + "?a=1", "Lexus RC F", snip, None), hit(HOCH + "?a=1", "Lexus RC F", snip, FUELL + RAHMEN)])
check("A4 dieselbe URL einmal nur Snippet, einmal mit Originaltext: beide tragen den Originaltext (eine Quelle)",
      all(x["content"].startswith("Der Lexus RC F") for x in t4) and s4["volltext"] == 2, str(s4))
t5, s5 = _mit_originaltext(t1)
check("A5 bereits verarbeitete Treffer werden nicht erneut gezaehlt", s5["treffer"] == 0)

print("\n=== B) Auswertung des Originaltexts (Haupt-Extraktion), unveraenderte N19-Regeln ===")
b, _ = bauteile_aus(hit(HOCH, "Lexus RC F", snip, FUELL + RAHMEN + f"\n\n{BARE}\n\n" + FUELL))
check("B1 SYNTHETISCH: allgemeiner Befund NUR im Volltext (Rahmensatz + Bauteilsatz) wird Fakt; das Snippet allein "
      "traegt ihn nicht", b == ["Wasserpumpe"] and bauteile_aus(hit(HOCH, "Lexus RC F", snip))[0] == [], str(b))
b2, ab2 = bauteile_aus(hit(HOCH, "Lexus RC F", snip, FUELL + BARE + "\n\n" + FUELL))
check("B2 SYNTHETISCH: unklare Reichweite im Volltext bleibt unbestaetigt (reichweite_unbelegt)",
      b2 == [] and any(a.get("grund") == "reichweite_unbelegt" for a in ab2))
b3, ab3 = bauteile_aus(hit(HOCH, "Lexus RC F", snip, FUELL + f"Bei meinem Lexus RC F war die Wasserpumpe dreimal undicht, "
                                                      "ein bekanntes Problem.\n\n" + FUELL))
check("B3 SYNTHETISCH: Einzelbericht im Volltext bleibt unbestaetigt (einzelbericht)",
      b3 == [] and any(a.get("grund") == "einzelbericht" for a in ab3))
b4, _ = bauteile_aus(hit(HOCH, "Lexus RC F", snip, FUELL + f"\n\nLexus GS F\n\n{RAHMEN.replace('RC F', 'GS F')}\n\n{BARE}\n\n" + FUELL))
check("B4 SYNTHETISCH: Rahmen eines Fremdmodells (Lexus GS F) liefert dem Zielmodell keinen Befund", b4 == [], str(b4))
b5, _ = bauteile_aus(hit(HOCH, "Lexus RC F Gebrauchtwagen", snip,
                         FUELL + "\n\nTypische Schwachstellen des Lexus GS F\n\n"
                         "Die Wasserpumpe ist häufig undicht und muss oft getauscht werden.\n\n" + FUELL))
check("B5 SYNTHETISCH: Absatz unter einer Fremdmodell-Ueberschrift ohne eigene Modellnennung wird dem RC F NICHT "
      "zugeordnet (der Absatzwechsel gibt den Titelkontext nicht zurueck)", b5 == [], str(b5))
b5b, _ = bauteile_aus(hit(HOCH, "Lexus RC F Gebrauchtwagen", snip,
                          FUELL + "\n\nTypische Schwachstellen des Lexus GS F\n\n"
                          "Die Wasserpumpe ist häufig undicht.\n\nLexus RC F\n\nBeim Lexus RC F ist die Wasserpumpe "
                          "häufig undicht.\n\n" + FUELL))
check("B5b SYNTHETISCH: nennt ein spaeterer Satz das Zielmodell selbst, gilt wieder das Ziel", b5b == ["Wasserpumpe"], str(b5b))
b6, _ = bauteile_aus(hit(HOCH, "Typische Schwachstellen des Lexus RC F", snip, FUELL + BARE + "\n\n" + FUELL))
check("B6 SYNTHETISCH: Seitentitel allein bestaetigt keinen Befund", b6 == [])
SNIP_POS = f"Die Wasserpumpe {M} ist häufig undicht"          # Snippet-Ausschnitt, am Satzende abgeschnitten
b7, ab7 = bauteile_aus(hit(HOCH, "Lexus RC F", SNIP_POS, FUELL + f"Eine häufig undichte Wasserpumpe {M} ist kein "
                                                           "bekanntes Problem.\n\n" + FUELL))
check("B7 SYNTHETISCH: positives Snippet-Fragment, im Volltext derselben Quelle relativierter Satz -> kein Fakt, "
      "das Snippet wird nicht erneut versucht (Kontrast: allein traegt das Fragment einen Fakt)",
      b7 == [] and any(a.get("grund") == "benign_relativiert" for a in ab7)
      and bauteile_aus(hit(HOCH, "Lexus RC F", SNIP_POS))[0] == ["Wasserpumpe"])
b8, _ = bauteile_aus(hit(HOCH, "Lexus RC F", "Beim Lexus RC F ist die Wasserpumpe häufig undicht.", None))
check("B8 SYNTHETISCH: ohne Volltext gilt das bisherige Snippet-Verhalten (allgemeiner Befund im Snippet bleibt Fakt)",
      b8 == ["Wasserpumpe"])
lang = f"Die Wasserpumpe {M} " + "und die Dichtungen " * 60 + "ist häufig undicht"          # unpunktierter Riesenblock
b9, ab9 = bauteile_aus(hit(HOCH, "Lexus RC F", snip, FUELL + lang + "\n\n" + FUELL))
check("B9 SYNTHETISCH: ein einzelner Claim aus Volltext ueber 800 Zeichen wird protokolliert verworfen (claim_zu_lang)",
      b9 == [] and any(a.get("grund") == "claim_zu_lang" for a in ab9))
b10, _ = bauteile_aus(hit("https://www.caranddriver.example/lexus-rc-f", "Lexus RC F review", "Lexus RC F review overview and specs for buyers.",
                         "The Lexus RC F is powered by a 5.0-liter V8. " * 12 + "\n\nTypical weaknesses of the Lexus RC F:\n\n"
                         "The water pump of the Lexus RC F leaks often.\n\n"))
# Volltext-Korrektur A: kurze Originaltexte
SNIP_POS2 = f"Die Wasserpumpe {M} ist häufig undicht [...] mehr dazu im Gebrauchtwagen-Check zum Lexus RC F"
REL_KURZ = f"Eine häufig undichte Wasserpumpe {M} ist kein bekanntes Problem."
b11, ab11 = bauteile_aus(hit(HOCH, "Lexus RC F", SNIP_POS2, REL_KURZ))
check("B11 SYNTHETISCH: positives Snippet, kuerzerer relativierender Originaltext (< 400 Zeichen, kuerzer als das Snippet) "
      "-> kein Fakt (kein Snippet-Retry); das Snippet allein traegt einen Fakt",
      b11 == [] and any(a.get("grund") == "benign_relativiert" for a in ab11)
      and bauteile_aus(hit(HOCH, "Lexus RC F", SNIP_POS2))[0] == ["Wasserpumpe"], str(b11))
b12, _ = bauteile_aus(hit(HOCH, "Lexus RC F", "Typische Schwachstellen des Lexus RC F: die Wasserpumpe ist undicht [...] "
                          "Mehr dazu im Gebrauchtwagen-Check zum Lexus RC F", KURZ))
check("B12 SYNTHETISCH: kurzer verwertbarer allgemeiner Befund (Rahmen + Satz, < 400 Zeichen) wird verarbeitet",
      b12 == ["Wasserpumpe"], str(b12))
b13, _ = bauteile_aus(hit(HOCH, "Lexus RC F", f"Die Wasserpumpe {M} ist häufig undicht",
                          "Access denied. Please enable JavaScript and cookies to continue."))
check("B13 SYNTHETISCH: technisch unbrauchbarer Text (Fehlerseite) -> echter Snippet-Fallback bleibt erhalten",
      b13 == ["Wasserpumpe"], str(b13))

# Volltext-Korrektur B: Fremdmodell-Ueberschriften ohne Marke
FREMD_ABSATZ = "Die Wasserpumpe ist häufig undicht und muss oft getauscht werden."


def abschnitt(ueberschrift, davor=""):
    return hit(HOCH, "Lexus RC F Gebrauchtwagen", snip, FUELL + davor + f"\n\n{ueberschrift}\n\n{FREMD_ABSATZ}\n\n" + FUELL)


mit_marke = bauteile_aus(abschnitt("Typische Schwachstellen des Lexus GS F"))[0]
ohne_marke = bauteile_aus(abschnitt("Typische Schwachstellen GS F"))[0]
check("B14 SYNTHETISCH: derselbe belegte Fremdabschnitt mit und ohne Markenname -> kein Zielfakt",
      mit_marke == [] and ohne_marke == [], f"{mit_marke} / {ohne_marke}")
check("B14b SYNTHETISCH: Fremdmodell auf der Seite hinter der Marke belegt, Ueberschrift nur mit Kuerzel -> kein Zielfakt",
      bauteile_aus(abschnitt("Typische Schwachstellen RX", davor="\n\nDer Lexus RX im Vergleich"))[0] == []
      and bauteile_aus(abschnitt("Typische Schwachstellen GS F", davor="\n\nLexus GS F im Vergleich"))[0] == [])
check("B14c SYNTHETISCH: Ueberschrift mit Buchstaben+Ziffern-Bezeichnung (mehrdeutig, nicht belegt) erbt dem Abschnitt "
      "den Titelkontext nicht", bauteile_aus(abschnitt("Typische Schwachstellen LC 500"))[0] == [])
check("B15 SYNTHETISCH: Abkuerzungen und Motorangaben in Ueberschriften sind keine Fremdmodelle (Befund bleibt)",
      bauteile_aus(abschnitt("Typische Schwachstellen ABS ESP"))[0] == ["Wasserpumpe"]
      and bauteile_aus(abschnitt("Typische Schwachstellen 5.0 V8"))[0] == ["Wasserpumpe"])
nach_fremd = bauteile_aus(hit(HOCH, "Lexus RC F Gebrauchtwagen", snip,
                              FUELL + f"\n\nTypische Schwachstellen GS F\n\n{FREMD_ABSATZ}\n\nLexus RC F\n\n"
                              "Die Wasserpumpe ist häufig undicht und muss oft getauscht werden.\n\n" + FUELL))[0]
check("B16 SYNTHETISCH: auf den Fremdabschnitt folgt eine eindeutige Zielmodell-Ueberschrift ('Lexus RC F') -> Befund moeglich",
      nach_fremd == ["Wasserpumpe"], str(nach_fremd))
check("B16b SYNTHETISCH: Zielmodell-Ueberschrift auch ohne Marke ('RC F Schwachstellen') -> Befund",
      bauteile_aus(abschnitt("RC F Schwachstellen"))[0] == ["Wasserpumpe"])
check("B10 SYNTHETISCH: englischer Volltext erhaelt durch dieses Paket keine neue Freigabe (keine deutsche "
      "Marktgeltung behauptet, aber auch keine Auswertung)", b10 == [])

print("\n=== C) ECHTE Texte als Volltext (Fixtures vom 08.11.2026) ===")
echt = [
    ("ADAC-Katalogseite (Textformat)", hit(ADAC_URL, "Lexus RC F | ADAC", snip, ADAC_TEXT)),
    ("ADAC-Seite UXC1 (Markdown-Tabellen)", hit(RB["kandidat"], "Lexus RC F | ADAC", snip, RB["extrakt"])),
    ("Rettungsdatenblatt", hit("https://www.lexus.de/rettungskarte.pdf", "Lexus RC F Rettungskarte", snip, RD)),
]
for name, h in echt:
    bt, _ = bauteile_aus(h)
    check(f"C {name}: als Originaltext keine Schwachstelle (kein Fremdbauteil, keine Garantiezeile)", bt == [], str(bt))
alle16 = WJ["ohne UXC1"] + WJ["mit UXC1"]
check("C 16 echte Tavily-Snippets: Verhalten unveraendert (Snippet-Fallback, keine Schwachstelle)",
      all(bauteile_aus(hit(h["url"], h.get("title") or "", h["content"]))[0] == [] for h in alle16))

print("\n=== D) Candidate-Pfad (Quellen unter der Schwelle, drei qualifizierende Familien) ===")
FAM = ["https://www.werkstatt-eins.example/rc-f", "https://www.reparatur-zwei.example/rc-f",
       "https://www.kfz-drei.example/rc-f"]


def cand(raws, snippets=None):
    tr_ = [hit(u, "Lexus RC F Werkstatt-Ratgeber", (snippets or ["Werkstatt-Ratgeber zum Lexus RC F im Überblick."] * 3)[i], r)
           for i, (u, r) in enumerate(zip(FAM, raws))]
    tr_, _ = _mit_originaltext(tr_)
    return _schwachstelle_candidate_fakten(tr_, marke="Lexus", modell="RC F", generation=None, haupt_fakten=[],
                                           erlaube_multi_source=True)


check("D0 Gegenprobe ist echt: drei Familien, secondary, unter der Schwelle",
      all(tr._wartung_source_type({"url": u, "title": "Lexus RC F Werkstatt-Ratgeber"}) == "secondary" for u in FAM)
      and len({tr._source_family(u) for u in FAM}) == 3
      and all(tr._score_domain_fuer_marke(u, tr.KATEGORIE_SCHWACHSTELLEN, "Lexus") < tr.MIN_SCORE_FAKT for u in FAM))
f1, _, _ = cand([FUELL + RAHMEN + f"\n\n{BARE}\n\n" + FUELL] * 3)
check("D1 SYNTHETISCH: allgemeiner Befund (Rahmen + Satz) nur im Volltext, drei Familien -> Mehrquellen-Fakt",
      len(f1) == 1 and f1[0].beleglage == "mehrfach_unabhaengig", str([(x.bauteil, x.beleglage) for x in f1]))
f2, ab2c, _ = cand([FUELL + BARE + "\n\n" + FUELL] * 3)
check("D2 SYNTHETISCH: dieselben Familien mit unklarer Reichweite im Volltext -> KEIN Fakt (reichweite_unbelegt)",
      f2 == [] and sum(1 for a in ab2c if a.get("grund") == "reichweite_unbelegt") == 3)
f3, ab3c, _ = cand([FUELL + "Bei meinem Lexus RC F war die Wasserpumpe dreimal undicht.\n\n" + FUELL] * 3)
check("D3 SYNTHETISCH: Einzelberichte im Volltext -> KEIN Fakt (einzelbericht)",
      f3 == [] and sum(1 for a in ab3c if a.get("grund") == "einzelbericht") == 3)
f4, _, _ = cand([FUELL + f"Eine häufig undichte Wasserpumpe {M} ist kein bekanntes Problem.\n\n" + FUELL] * 3,
                snippets=[f"Die Wasserpumpe {M} ist häufig undicht."] * 3)
check("D4 SYNTHETISCH: positives Snippet, relativierender Volltext in allen drei Familien -> KEIN Fakt", f4 == [])

f5, _, _ = cand([FUELL + "\n\nTypische Schwachstellen GS F\n\nDie Wasserpumpe ist häufig undicht und muss oft getauscht werden.\n\n" + FUELL] * 3)
check("D5 SYNTHETISCH: Fremdabschnitt ohne Markenname im Volltext, drei Familien -> KEIN Fakt",
      f5 == [])
f6, _, _ = cand([FUELL + "\n\nLexus RC F\n\nDie Wasserpumpe ist häufig undicht und muss oft getauscht werden.\n\n" + FUELL] * 3)
check("D6 SYNTHETISCH: derselbe Aufbau unter eindeutiger Zielmodell-Ueberschrift -> Befund moeglich (Positivkontrolle)",
      len(f6) == 1, str([(x.bauteil, x.beleglage) for x in f6]))
f7, _, _ = cand([REL_KURZ] * 3, snippets=[SNIP_POS2] * 3)
check("D7 SYNTHETISCH: positives Snippet, kuerzerer relativierender Originaltext in allen drei Familien -> KEIN Fakt",
      f7 == [])

print("\n=== E) Provider-Ebene: Anfragefolge, Extract, Diagnose, Sichtbarkeit (gestubbter run_kaufcheck) ===")
treffer_ = lambda u, t, c: {"url": u, "title": t, "content": c}   # noqa: E731
RK_URL = ("https://www.lexus.de/content/dam/lexus/nmsc/germany/lexus-besitzer/fahrzeuginformationen/"
          "RCF_tcm-3150-1284184.pdf")
RK_VOLL = ("Lexus RC F\n(UXC1 (EU, M), ab 09/2014)\nStand: 10/2014\nLexus RC F (UXC1 (EU, ab 09/2014)\n"
           "12V Spannungsversorgung trennen\nLexus RC F\n(UXC1 (EU, M), ab 09/2014)")
ADAC_ID = treffer_(ADAC_URL, "Lexus RC F Ultimate Edition Automatik (08/24 - 06/25): Technische Daten | ADAC",
                   "Marke Lexus Modell RC F Typ Ultimate Edition Automatik Baureihe RC (UXC1) (12/14 - 06/25) "
                   "Herstellerinterne Baureihenbezeichnung UXC1 Motorcode 2UR-GSE Leistung maximal in PS 464 "
                   "Antriebsart Hinterrad Getriebeart Automatikgetriebe")
AMS = treffer_("https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
               "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
               "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis 477 PS. Hubraum 1998 cm3 bis "
               "4969 cm3. LEXUS RC F TRACK EDITION IM SUPERTEST: Was kann der V8-Sauger auf der Rennstrecke? "
               "LEXUS LC 500 UND RC F: V8-Sportler auf Abschiedstour.")
REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, motor="5.0 V8",
                       kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", antrieb="Heck", ausstattung=[])


def db_miss(marke, modell, baujahr):
    return None, {"match_art": "no_match", "konfidenz": "niedrig", "belastbar": False,
                  "fehlende_angabe": "simulierter DB-Miss, Testdouble"}


class LogSammler(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.zeilen: list[str] = []

    def emit(self, rec):
        self.zeilen.append(rec.getMessage())


async def lauf(sw_a=None, sw_b=None, nach=None, mit_raw=True):
    """sw_a/sw_b: Treffer der beiden Schwachstellen-Anfragen; nach: Treffer der Schwachstellen-Nachsuche."""
    calls: list[tuple[str, bool]] = []
    extract_urls: list[str] = []
    routing = [("Generation Modellcode", [treffer_(RK_URL, "Lexus RC F Rettungskarte", "12V trennen")]),
               ("technische Daten", [ADAC_ID]), ("Generation Baureihe Bauzeitraum", [AMS]),
               ("bekannte Probleme Schwachstellen", sw_a or []),
               ("Gebrauchtwagen Test TÜV Kaufberatung", sw_b or []),
               ("Werkstattinformation", nach or []), ("technical service bulletin", nach or [])]

    async def search(query, *, count=8, exclude_domains=None, include_domains=None, include_raw_content=False):
        calls.append((query, bool(include_raw_content)))
        for teil, liste in routing:
            if teil in query:
                aus = []
                for h in liste:
                    h = dict(h)
                    if not (include_raw_content and mit_raw):
                        h.pop("raw_content", None)      # Provider liefert Seitentext nur, wenn angefordert
                    aus.append(h)
                return aus
        return []

    async def extract(urls):
        extract_urls.extend(urls)
        g = {u: RK_VOLL for u in urls if u == RK_URL}
        return g, {"angefragt": len(urls), "aus_cache": 0, "extract_calls": 1, "extract_urls": len(urls),
                   "erfolge": len(g), "fehler": len(urls) - len(g)}

    async def gemini(system, user):
        return {"bericht": "x", "empfehlung": "kaufen_nach_besichtigung", "risiko_evidence_ids": [],
                "empfehlung_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen")

    orig = (kc.call_gemini_json, kc.tavily_search_with_fallback, kc.marktpreis_recherche_moeglich,
            kc.vertiefe_marktrecherche, kc.find_baureihe_mit_vertrauen, tr.tavily_search, tr.hole_raw_content)
    kc.call_gemini_json = gemini
    kc.tavily_search_with_fallback = kc.vertiefe_marktrecherche = verboten
    kc.marktpreis_recherche_moeglich = lambda *a: False
    kc.find_baureihe_mit_vertrauen = db_miss
    tr.tavily_search, tr.hole_raw_content = search, extract
    sammler = LogSammler()
    lg = logging.getLogger("app.technical_research")
    lg.addHandler(sammler)
    alt = lg.level
    lg.setLevel(logging.INFO)
    try:
        r = KaufCheckResponse(**await kc.run_kaufcheck(REQ))
        return r, calls, extract_urls, sammler.zeilen
    finally:
        lg.removeHandler(sammler)
        lg.setLevel(alt)
        (kc.call_gemini_json, kc.tavily_search_with_fallback, kc.marktpreis_recherche_moeglich,
         kc.vertiefe_marktrecherche, kc.find_baureihe_mit_vertrauen, tr.tavily_search,
         tr.hole_raw_content) = orig


def sichtbar(r):
    akt = r.kaufaktionen
    teile = [r.bericht]
    for sek in (akt.verkaeuferfragen, akt.besichtigung, akt.probefahrt, akt.dokumente):
        teile += [f"{a.titel} {a.aktion}" for a in sek.fahrzeugspezifisch + sek.basis]
    return "\n".join(teile)


def sw(r):
    return [i for i in r.insights if i.kategorie in ("web_schwachstelle", "schwachstelle")]


MARKER = "SEITENTEXT_MARKER_DARF_NICHT_IM_LOG_STEHEN"


async def pfad():
    voll = FUELL + RAHMEN + f"\n\n{BARE}\n\n" + FUELL + MARKER
    # 1) allgemeiner Befund nur im Volltext -> sichtbar; ohne Volltext (Provider liefert nichts) -> nicht
    r1, calls1, ext1, log1 = await lauf(sw_a=[hit(HOCH, "Lexus RC F Gebrauchtwagen", snip, voll)])
    check("E1 SYNTHETISCH: allgemeiner Befund nur im Volltext erreicht Insight, Bericht, Frage und Nachweis",
          any("Wasserpumpe" in i.titel for i in sw(r1)) and "Wasserpumpe" in r1.bericht and "Wasserpumpe" in sichtbar(r1),
          str([i.titel for i in sw(r1)]))
    r1b, *_ = await lauf(sw_a=[hit(HOCH, "Lexus RC F Gebrauchtwagen", snip, voll)], mit_raw=False)
    check("E2 derselbe Treffer, Provider liefert KEINEN Volltext -> Snippet-Fallback, nichts Sichtbares "
          "(angefordert != geliefert)", sw(r1b) == [] and "Wasserpumpe" not in sichtbar(r1b))
    # 3) Anfragefolge/Extract: Anforderung nur fuer Schwachstellen-Anfragen, kein Extract fuer diese Treffer
    sw_calls = [c for c in calls1 if "bekannte Probleme Schwachstellen" in c[0] or "Gebrauchtwagen Test TÜV" in c[0]]
    andere = [c for c in calls1 if c not in sw_calls]
    check("E3 Seitentext wird nur fuer die beiden Schwachstellen-Anfragen angefordert, fuer alle anderen nicht",
          len(sw_calls) == 2 and all(c[1] for c in sw_calls) and not any(c[1] for c in andere), str(calls1))
    check("E4 kein Extract-Aufruf fuer Schwachstellen-Treffer", HOCH not in ext1)
    # unveraenderte Folge: gleicher Szenario-Typ ohne Befund, mit/ohne Volltext -> identische Anfragefolge
    bare = [hit(HOCH, "Lexus RC F Gebrauchtwagen", snip, FUELL + BARE + "\n\n" + FUELL)]
    ra, calls_a, ext_a, _ = await lauf(sw_a=bare, mit_raw=True)
    rb, calls_b, ext_b, _ = await lauf(sw_a=bare, mit_raw=False)
    check("E5 Anfragefolge und Anzahl bleiben mit und ohne Volltext identisch (keine zusaetzlichen Provideraufrufe)",
          [c[0] for c in calls_a] == [c[0] for c in calls_b] and len(calls_a) == len(calls_b) and ext_a == ext_b,
          f"{len(calls_a)} vs {len(calls_b)}")
    check("E6 SYNTHETISCH: bare-Aussage im Volltext -> weiterhin nichts Sichtbares", sw(ra) == [] and "Wasserpumpe" not in sichtbar(ra))
    # 7) Diagnose
    zeilen = [z for z in log1 if "schwachstelle_volltext" in z]
    check("E7 Diagnose je Runde: angefordert, Trefferzahl, brauchbarer Volltext, Snippet-Fallback; kein Seitentext im Log",
          any("round=1" in z and "angefordert_anfragen=2" in z and "volltext_brauchbar=1" in z
              and "snippet_fallback=0" in z for z in zeilen) and not any(MARKER in z for z in log1), str(zeilen))
    # 8) Haupt-Fallback: Fremdmodell-Rahmen, Einzelbericht, Seitentitel
    for name, h in (("Fremdmodell-Rahmen", hit(HOCH, "Lexus RC F", snip, FUELL + "\n\nLexus GS F\n\n"
                                                + RAHMEN.replace("RC F", "GS F") + f"\n\n{BARE}\n\n" + FUELL)),
                    ("Einzelbericht", hit(HOCH, "Lexus RC F", snip, FUELL + "Bei meinem Lexus RC F war die Wasserpumpe "
                                          "dreimal undicht.\n\n" + FUELL)),
                    ("Seitentitel", hit(HOCH, RAHMEN.rstrip(":"), snip, FUELL + BARE + "\n\n" + FUELL))):
        r, *_ = await lauf(sw_a=[h])
        check(f"E8 SYNTHETISCH: {name} im Volltext -> bis Bericht, Fragen und Dokumente nichts Sichtbares",
              sw(r) == [] and "Wasserpumpe" not in sichtbar(r), str([i.titel for i in sw(r)]))
    # 9) Widerspruch Snippet positiv / Volltext relativiert
    r9, *_ = await lauf(sw_a=[hit(HOCH, "Lexus RC F", f"Die Wasserpumpe {M} ist häufig undicht",
                                  FUELL + f"Eine häufig undichte Wasserpumpe {M} ist kein bekanntes Problem.\n\n" + FUELL)])
    check("E9 SYNTHETISCH: positives Snippet + relativierender Volltext -> nichts Sichtbares", sw(r9) == [])
    # 10) ECHT: ADAC-Volltext
    r10, *_ = await lauf(sw_a=[hit(ADAC_URL, "Lexus RC F | ADAC", snip, ADAC_TEXT)])
    check("E10 ECHT: ADAC-Volltext -> kein Schwachstellen-Insight, kein Spurhalteassistent in Bericht/Fragen/Dokumenten",
          sw(r10) == [] and "Spurhalteassistent" not in sichtbar(r10))
    # Volltext-Korrektur: kurze Originaltexte und Fremdabschnitte ohne Marke bis Bericht/Fragen/Dokumente
    r12, *_ = await lauf(sw_a=[hit(HOCH, "Lexus RC F", SNIP_POS2, REL_KURZ)])
    check("E12 SYNTHETISCH: positives Snippet + kuerzerer relativierender Originaltext -> nichts Sichtbares",
          sw(r12) == [] and "Wasserpumpe" not in sichtbar(r12))
    r13, *_, log13 = await lauf(sw_a=[hit(HOCH, "Lexus RC F", "Lexus RC F im Ueberblick fuer Kaufinteressenten und Besitzer.", KURZ)])
    check("E13 SYNTHETISCH: kurzer verwertbarer allgemeiner Befund (< 400 Zeichen) erreicht Insight, Bericht, Frage, Nachweis",
          any("Wasserpumpe" in i.titel for i in sw(r13)) and "Wasserpumpe" in r13.bericht and "Wasserpumpe" in sichtbar(r13),
          str([i.titel for i in sw(r13)]))
    r14, *_, log14 = await lauf(sw_a=[hit(HOCH, "Lexus RC F Gebrauchtwagen", "Die Wasserpumpe des Lexus RC F ist häufig undicht",
                                          "Access denied. Please enable JavaScript and cookies to continue.")])
    check("E14 SYNTHETISCH: technisch unbrauchbarer Text -> Snippet-Fallback (Befund aus dem Snippet bleibt sichtbar), Diagnose "
          "nennt den Grund", any("Wasserpumpe" in i.titel for i in sw(r14))
          and any("snippet_fallback=1 (fehlt=0 unbrauchbar=1)" in z for z in log14 if "schwachstelle_volltext" in z),
          str([z for z in log14 if "schwachstelle_volltext" in z]))
    for name, ueber in (("Fremdabschnitt MIT Marke", "Typische Schwachstellen des Lexus GS F"),
                        ("Fremdabschnitt OHNE Marke", "Typische Schwachstellen GS F")):
        r, *_ = await lauf(sw_a=[abschnitt(ueber)])
        check(f"E15 SYNTHETISCH: {name} -> bis Bericht, Fragen und Dokumente kein Zielfakt",
              sw(r) == [] and "Wasserpumpe" not in sichtbar(r), str([i.titel for i in sw(r)]))
    r16, *_ = await lauf(sw_a=[abschnitt("Lexus RC F")])
    check("E16 SYNTHETISCH: eindeutiger Zielmodell-Abschnitt -> Befund bleibt moeglich (Insight, Bericht, Frage/Nachweis)",
          any("Wasserpumpe" in i.titel for i in sw(r16)) and "Wasserpumpe" in sichtbar(r16), str([i.titel for i in sw(r16)]))
    # 11) Nachsuche: Seitentext wird auch dort angefordert und genutzt
    r11, calls11, ext11, log11 = await lauf(
        sw_a=[hit(HOCH, "Lexus RC F Gebrauchtwagen", "Lexus RC F Gebrauchtwagen im Überblick für Kaufinteressenten.")],
        nach=[hit("https://www.auto-motor-und-sport.de/lexus-rc-f-werkstatt/", "Lexus RC F Werkstatt", snip,
                  FUELL + RAHMEN + f"\n\n{BARE}\n\n" + FUELL)])
    nach_calls = [c for c in calls11 if "Werkstattinformation" in c[0] or "technical service bulletin" in c[0]]
    check("E11 SYNTHETISCH: Schwachstellen-Nachsuche fordert Seitentext an, nutzt ihn (Befund sichtbar), ohne Extract",
          bool(nach_calls) and all(c[1] for c in nach_calls) and any("Wasserpumpe" in i.titel for i in sw(r11))
          and not (set(ext11) - {RK_URL}), f"{nach_calls} {[i.titel for i in sw(r11)]}")


asyncio.run(pfad())

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
