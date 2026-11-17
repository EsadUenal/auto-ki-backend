"""
Hubraum in cm³/ccm mit Tausendertrennzeichen -- EINE Normalisierung für Hubraum-Claims und Motorkern-Prüfung.

Befund (Offline-Probe 16.11.2026): "4.969 cm³" wurde als "969 cm³" gelesen und ergab 1.0 statt 5.0; ein Satz mit
"5,0 Liter V8 (4.969 cm³)" galt dadurch als mehrdeutig und zählte nicht als Motorkern-Beleg. Dieser Test belegt die
NORMALISIERUNG an synthetischen Sätzen ([SYNTH], nur die Schreibweise des Hubraums variiert) und die bestehenden N6/N17-Regeln
an den aufgezeichneten Texten (Quellen wie in test_n6_n17_motorkern_ccm.py). Er sagt nichts darüber, ob der Fehler im Lauf
vom 16.11. mitgewirkt hat.

    python scripts/test_isolated.py test_hubraum_tausendertrenner.py
"""
import os
import socket
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="hubraum_")
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
from app.hubraum_norm import CCM_RE, ccm_als_liter  # noqa: E402
from app.technical_research import (_hubraum_liter_werte, _hubraum_werte_litergleich, _identitaets_claims,  # noqa: E402
                                    _ziel, werte_identitaet_aus)

FAILS: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def werte(s):
    return [w for w, _ in _hubraum_werte_litergleich(s)]


NBSP, NNBSP, THIN = " ", " ", " "

print("== A: Normalisierung (gemeinsame Funktion, [SYNTH]) ==")
for schreibweise in ("4969", "4.969", "4 969", "4,969", f"4{NBSP}969", f"4{NNBSP}969", f"4{THIN}969"):
    check(f"A1 '{schreibweise!r} cm³' -> 5.0 (Hubraum-Erkennung der Motorkern-Prüfung)",
          werte(f"Der V8 hat {schreibweise} cm³ Hubraum.") == ["5.0"], str(werte(f"Der V8 hat {schreibweise} cm³ Hubraum.")))
for einheit in ("cm³", "cm3", "ccm", "cm 3", "CCM"):
    check(f"A2 Einheit {einheit!r} mit Tausenderpunkt -> 5.0", werte(f"V8 mit 4.969 {einheit}") == ["5.0"])
check("A3 1.998 cm³ -> 2.0", werte("Vierzylinder mit 1.998 cm³") == ["2.0"])
check("A3b 1 998 cm³ und 1,998 cm³ -> 2.0", werte("Vierzylinder mit 1 998 cm³") == ["2.0"] and werte("Vierzylinder mit 1,998 cm³") == ["2.0"])
check("A4 998 cm³ -> 1.0 (kein Trennzeichen, bleibt unverändert)", werte("Kleinwagen mit 998 cm³") == ["1.0"])
check("A5 4969 cm³ und die bestehenden Literformate bleiben unverändert",
      werte("V8 mit 4969 cm³") == ["5.0"] and werte("5,0-Liter-V8") == ["5.0"] and werte("5.0L V8") == ["5.0"]
      and werte("5,0 Liter V8") == ["5.0"] and werte("2.0 l Motor") == ["2.0"])
check("A6 keine Teilzahl: '4.969 cm³' liefert NICHT 1.0 (969); 'ccm_als_liter' liest die ganze Zahl",
      "1.0" not in werte("V8 mit 4.969 cm³") and ccm_als_liter("4.969") == "5.0" and ccm_als_liter("4 969") == "5.0"
      and ccm_als_liter("4,969") == "5.0" and ccm_als_liter("998") == "1.0")
check("A7 keine Zahl mitten in einer Dezimalzahl ('4.9691 cm³'), kein Trennzeichen-Raten ohne 3er-Gruppe ('4.96 cm³')",
      werte("V8 mit 4.9691 cm³") == [] and werte("V8 mit 4.96 cm³") == [])
check("A8 Angaben ohne cm³-Einheit werden nicht gelesen (Jahr, Preis, Drehzahl, Verbrauch)",
      werte("Baujahr 2017, 4.969 Euro, 7.100 U/min, 11,8 l/100 km") == [])
check("A9 vorangestellte Zahl mit Leerzeichen ('Baujahr 2017 4969 cm³', '10 000 U/min 998 cm³') bleibt ohne Folgefehler",
      werte("Baujahr 2017 4969 cm³") == ["5.0"] and werte("10 000 U/min 998 cm³") == ["1.0"])
check("A10 Literangabe + gleichwertige cm³ im selben Satz: kein falscher Widerspruch (5,0 Liter V8 (4.969 cm³))",
      werte("Ein 5,0 Liter V8 (4.969 cm³).") == ["5.0", "5.0"] and werte("Ein 5,0 Liter V8 (4 969 cm³).") == ["5.0", "5.0"])
check("A11 ECHTE Widersprüche bleiben Widersprüche (5,0 Liter vs. 3.456 cm³; 4.969 vs. 3.456)",
      werte("Ein 5,0 Liter V8 mit 3.456 cm³.") == ["5.0", "3.5"] and werte("Wahlweise 4.969 cm³ oder 3.456 cm³.") == ["5.0", "3.5"])
check("A12 die Liter-Funktion selbst ist unverändert (kein cm³)", _hubraum_liter_werte("V8 mit 4.969 cm³") == [])
check("A13 CCM_RE gibt die Zahl samt Trenner als Gruppe 1 (Verträglichkeit mit allen Aufrufern)",
      [m.group(1) for m in CCM_RE.finditer("V8 4.969 cm³, I4 1998 ccm")] == ["4.969", "1998"])

print("\n== B: Hubraum-CLAIMS verwenden dieselbe Normalisierung ==")
ZIEL = _ziel("Lexus", "RC F", 2017, "5.0 V8, Heckantrieb", None)


def claim_werte(satz, titel="Lexus RC F Technische Daten", host="www.autobild.de"):
    r = {"url": f"https://{host}/synthetisch/rcf", "title": titel, "content": satz}
    return sorted({str(c["wert"]) for c in _identitaets_claims(r, ZIEL) if c["feld"] == "displacement"})


check("B1 Claims: '4969 cm³', '4.969 cm³', '4 969 cm³', '4,969 cm³' ergeben alle 5.0",
      all(claim_werte(f"Der Lexus RC F hat {s} Hubraum.") == ["5.0"] for s in
          ("4969 cm³", "4.969 cm³", "4 969 cm³", "4,969 cm³", f"4{NBSP}969 cm³", f"4{NNBSP}969 ccm")),
      str([claim_werte(f"Der Lexus RC F hat {s} Hubraum.") for s in ("4969 cm³", "4.969 cm³")]))
check("B2 Claims: 'Hubraum 1.998 cm³' (Ziel-Familie) -> 2.0, kein 1.0", claim_werte("Der Lexus RC F hat 1.998 cm³ Hubraum.") == ["2.0"])
_PAAR_PUNKT = "Lexus RC F\n\nHubraum\n\n4.969 cm³\n\nLeistung"
_PAAR_PLAIN = "Lexus RC F\n\nHubraum\n\n4969 cm³\n\nLeistung"
check("B3 Claims: Label/Wert-Paar aus getrennten Absätzen ('Hubraum' / '4.969 cm³') wird wie '4969 cm³' gelesen",
      claim_werte(_PAAR_PUNKT) == claim_werte(_PAAR_PLAIN) == ["5.0"], str((claim_werte(_PAAR_PUNKT), claim_werte(_PAAR_PLAIN))))

print("\n== C: Motorkern-Prüfung (N6/N17-Regeln bleiben) ==")
RC = "5.0 V8, Heckantrieb"


def synth(satz, host="www.auto-motor-und-sport.de", titel="Lexus RC F Test"):
    return {"url": f"https://{host}/synthetisch/rcf", "title": titel, "content": satz}


def kern(motor, quellen, baujahr=2017, modell="RC F"):
    w, _ = werte_identitaet_aus(quellen, _ziel("Lexus", modell, baujahr, motor, None))
    return w


def beleg(w):
    return w.motor is not None, bool((w.feldwerte.get("engine_name") or {}).get("kern_vollstaendig"))


def zwei(satz):
    return [synth(satz), synth(satz, host="www.autobild.de")]


for s4969, s_punkt, s_blank, s_komma, s_nbsp in (
        ("Der Lexus RC F wird von einem V8-Saugmotor mit 4969 cm³ angetrieben.",
         "Der Lexus RC F wird von einem V8-Saugmotor mit 4.969 cm³ angetrieben.",
         "Der Lexus RC F wird von einem V8-Saugmotor mit 4 969 cm³ angetrieben.",
         "Der Lexus RC F wird von einem V8-Saugmotor mit 4,969 cm³ angetrieben.",
         f"Der Lexus RC F wird von einem V8-Saugmotor mit 4{NBSP}969 cm³ angetrieben."),):
    ref = beleg(kern(RC, zwei(s4969)))
    check("C1 Referenz: zwei Tier-2-Domains, V8 + 4969 cm³ im selben Satz -> Motorkern bestätigt", ref == (True, True), str(ref))
    for name, s in (("4.969", s_punkt), ("4 969", s_blank), ("4,969", s_komma), ("4<NBSP>969", s_nbsp)):
        check(f"C2 '{name} cm³' wird wie '4969 cm³' behandelt (Motorkern bestätigt)", beleg(kern(RC, zwei(s))) == ref, str(beleg(kern(RC, zwei(s)))))
TIER1 = [synth("Der Lexus RC F wird von einem V8-Saugmotor mit 4.969 cm³ angetrieben.", host="www.toyota-media.de",
               titel="Lexus RC F Presseinformation")]
check("C3 EINE Tier-1-Herstellerquelle mit '4.969 cm³' bestätigt wie mit '4969 cm³' (Schwelle unverändert: 1 Tier-1 genügt)",
      beleg(kern(RC, TIER1)) == (True, True) == beleg(kern(RC, [dict(TIER1[0], content=TIER1[0]["content"].replace("4.969", "4969"))])))
EINE_T2 = [synth("Der Lexus RC F wird von einem V8-Saugmotor mit 4.969 cm³ angetrieben.")]
check("C4 EINE Tier-2-Domain bestätigt weiterhin NICHT (Schwelle unverändert, unabhängig von der Schreibweise)",
      kern(RC, EINE_T2).motor is None and kern(RC, [synth(EINE_T2[0]["content"].replace("4.969", "4969"))]).motor is None)
check("C5 'Liter + Tausender-cm³' im selben Satz widerspricht sich nicht -> bestätigt",
      beleg(kern(RC, zwei("Der Lexus RC F hat einen 5,0 Liter V8 (4.969 cm³)."))) == (True, True))
check("C6 echter Widerspruch im selben Satz bleibt mehrdeutig -> nicht bestätigt (5,0 Liter V8 mit 3.456 cm³)",
      kern(RC, zwei("Der Lexus RC F hat einen 5,0 Liter V8 mit 3.456 cm³ Hubraum.")).motor is None)
check("C7 falscher Hubraum (Ziel 3.5 V8, Quelle 4.969 cm³) bestätigt nicht", kern("3.5 V8, Heckantrieb", zwei(
      "Der Lexus RC F wird von einem V8-Saugmotor mit 4.969 cm³ angetrieben.")).motor is None)
check("C8 Bauart und Hubraum in getrennten Sätzen zählen weiterhin NICHT (auch mit Tausenderpunkt)",
      kern(RC, zwei("Der RC F ist ein V8-Sportcoupe. Seine Maschine hat 4.969 cm³ Hubraum.")).motor is None)
check("C9 Datenblatt-Fenster NACH dem Baujahr schließt auch '4.969 cm³'-Sätze weiterhin aus",
      kern(RC, zwei("Lexus RC F Datenblatt Modellstart 01/18 Modellende 03/19. Der RC F hat einen V8 mit 4.969 cm³ Hubraum.")).motor is None
      and beleg(kern(RC, zwei("Lexus RC F Datenblatt Modellstart 01/16 Modellende 12/17. Der RC F hat einen V8 mit 4.969 cm³ Hubraum."))) == (True, True))

print("\n" + ("ALLE GRÜN" if not FAILS else f"{len(FAILS)} FEHLER: {FAILS}"))
sys.exit(1 if FAILS else 0)
