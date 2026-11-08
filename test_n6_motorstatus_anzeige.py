"""
N6 -- Motorstatus in Übersicht und Detail konsistent (Anzeigefix).

Referenz: Live-Bericht 20261108-091413, Lexus RC F, Nutzereingabe
"5.0 V8, Heckantrieb". Übersicht sagte "Generation und Motorisierung ...
plausibilisiert", ohne zu benennen, WAS die Motorisierung trägt.
Parser, Schwellen und Empfehlungslogik bleiben unverändert.

    python test_n6_motorstatus_anzeige.py
"""
import os
import socket
import tempfile

_tmp = tempfile.mkdtemp(prefix="n6_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = _gesperrt
socket.create_connection = _gesperrt

from app.anzeige import herkunft  # noqa: E402
from app.empfehlungs_policy import identitaet_aufloesen  # noqa: E402
from app.key_findings import _identitaets_finding  # noqa: E402
from app.vehicle_identity import VehicleIdentity  # noqa: E402

_FEHLER: list[str] = []


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def _basis(engine_name_fe, engine_code=None, code_fe=None):
    i = VehicleIdentity(make="Lexus", model="RC F", generation="UXC1",
                        engine_name="5.0 V8, Heckantrieb", engine_code=engine_code)
    web = {"verification_state": "web_only", "confirmed_by": ["web"]}
    i.field_evidence = {
        "make": dict(web), "model": dict(web), "generation": dict(web),
        "engine_name": engine_name_fe,
    }
    if code_fe:
        i.field_evidence["engine_code"] = code_fe
    return i


USER_ONLY = {"verification_state": "user_only", "confirmed_by": [],
             "raw_user_value": "5.0 V8, Heckantrieb"}
CODE_WEB = {"verification_state": "web_only", "confirmed_by": ["web"]}


def uebersicht(i):
    return _identitaets_finding("x", True, i).aktion


# A) nur Motorcode belegt (Live-ähnlich)
a = _basis(dict(USER_ONLY), "2UR-GSE", dict(CODE_WEB))
ta = uebersicht(a)
print("A) Übersicht:", ta)
print("A) Detail   :", herkunft(a.field_evidence["engine_name"], "engine_name"))
check("A1) Stufe unverändert: Motorisierung gilt als belegt",
      not identitaet_aufloesen(a).fehlend)
check("A2) Übersicht nennt den Motorcode als tragenden Beleg", "Motorcode 2UR-GSE" in ta, ta)
check("A3) Übersicht sagt ausdrücklich, dass die Motorbezeichnung nicht bestätigt ist",
      "nicht bestätigt" in ta and "Motorbezeichnung" in ta, ta)
check("A4) keine Hubraum-/V8-Bestätigung in der Übersicht", "V8" not in ta and "5.0" not in ta, ta)
check("A5) Detailzeile zeigt keinen Teilbeleg, der nicht vorliegt",
      "durch Webquellen bestätigt" not in herkunft(a.field_evidence["engine_name"], "engine_name"))

# B) Motorcode plus belegter Motorkern
b = _basis(dict(USER_ONLY, bestaetigte_teile=["5.0 V8"]), "2UR-GSE", dict(CODE_WEB))
tb = uebersicht(b)
print("B) Übersicht:", tb)
print("B) Detail   :", herkunft(b.field_evidence["engine_name"], "engine_name"))
check("B1) Stufe unverändert", not identitaet_aufloesen(b).fehlend)
check("B2) Übersicht nennt Motorcode UND den bestätigten Teil „5.0 V8“",
      "2UR-GSE" in tb and "5.0 V8" in tb and "übrige Angabe nicht geprüft" in tb, tb)
check("B3) Detail nennt den belegten Teil", "5.0 V8" in herkunft(b.field_evidence["engine_name"], "engine_name"))

# C) weder Motorcode noch Motorbezeichnung belegt
c = _basis(dict(USER_ONLY))
check("C1) Stufe: Motorisierung bleibt offen", "Motorisierung" in identitaet_aufloesen(c).fehlend)
tc = uebersicht(c)
print("C) Übersicht:", tc)
check("C2) keine 'ausreichend plausibilisiert'-Aussage",
      "ausreichend plausibilisiert" not in tc and "Motorcode" in tc, tc)

# D) Motorbezeichnung belegt, kein Code
d = _basis(dict(CODE_WEB))
d.engine_name = "5.0 V8"
td = uebersicht(d)
print("D) Übersicht:", td)
check("D1) Übersicht nennt Motorbezeichnung, Motorcode nicht belegt",
      "Motorbezeichnung „5.0 V8“ belegt" in td and "Motorcode nicht belegt" in td, td)

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
