"""
N18 -- "Kaufen nach Besichtigung" bei fehlenden belastbaren Schwachstellen- UND
Wartungsdaten: sichtbar "Analyse eingeschränkt" mit konkretem Hinweis.

Live-Bericht 20261108-091413: Identität belegt, Technikrecherche lief (partial),
0 Schwachstellen, 0 Wartung, kein Markt -> Überschrift unverändert
"KAUFEN NACH BESICHTIGUNG", Begründung "rein technische Einschätzung".

Geprüft: Kurzansicht (empfehlung_anzeige/-hinweis/-gruende) und Bericht.
Interne Stufe und `state` bleiben unverändert.

    python test_n18_technik_luecke.py
"""
import os
import socket
import tempfile
from types import SimpleNamespace

_tmp = tempfile.mkdtemp(prefix="n18_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = _gesperrt
socket.create_connection = _gesperrt

from app.empfehlung_gruende import baue_empfehlung_gruende  # noqa: E402
from app.empfehlungs_floor import wende_floor_an  # noqa: E402
from app.empfehlungs_policy import entscheide  # noqa: E402
from app.kaufaktionen import build_kaufaktionen  # noqa: E402
from app.kaufcheck_bericht import bericht  # noqa: E402
from app.models import Insight, KaufCheckRequest, PriceAssessment  # noqa: E402
from app.vehicle_identity import VehicleIdentity  # noqa: E402

_FEHLER: list[str] = []
ANZEIGE_N18 = "Analyse eingeschränkt: keine bestätigten Schwachstellen- und Wartungsdaten"


def check(name, bedingung, detail=""):
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def identitaet():
    v = VehicleIdentity(make="Lexus", model="RC F", year=2017, generation="UXC1",
                        engine_code="2UR-GSE", engine_name="5.0 V8", fuel="Benzin", horsepower=477)
    web = {"verification_state": "web_only", "primary_source": "web", "confirmed_by": ["web"]}
    user = {"verification_state": "user_only", "primary_source": "user", "confirmed_by": []}
    v.field_evidence = {"make": dict(web), "model": dict(web), "generation": dict(web),
                        "engine_code": dict(web), "engine_name": dict(user), "fuel": dict(user),
                        "year": dict(user), "horsepower": dict(user)}
    return v


REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000,
                       leistung_ps=477, kraftstoff="Benzin", getriebe="Automatik", preis_eur=49000)


def ins(id_, kat, **kw):
    return Insight(id=id_, kategorie=kat, titel=f"{kat} (synthetisch)", beschreibung="synthetisch",
                   confidence=kw.pop("confidence", "hoch"), **kw)


def lauf(insights, fakten_kat, markt, technik="partial", rueckruf="success"):
    ident = identitaet()
    web = SimpleNamespace(phasen_status={"identitaet": "success", "rueckruf": rueckruf,
                                         "technik": technik},
                          fakten=[SimpleNamespace(kategorie=k) for k in fakten_kat], identitaet=None)
    nach_floor, _ = wende_floor_an("kaufen_nach_besichtigung", insights)
    ent = entscheide(nach_floor, ident, web_recherche=web, insights=insights)
    label = "Preis marktgerecht"
    gruende = baue_empfehlung_gruende(REQ, None, None, insights, [], ent.empfehlung, markt,
                                      label if markt else None, identity=ident,
                                      technik_luecke=ent.technik_luecke)
    pa = PriceAssessment(verdict="marktgerecht", label=label) if markt else PriceAssessment()
    text = bericht(REQ, ident, None, None, insights, build_kaufaktionen(REQ, None, None, insights),
                   gruende, ent.empfehlung, pa, markt, None, None, ["Webquellen"],
                   entscheidung=ent, web_recherche=web)
    zeilen = text.splitlines()
    i = zeilen.index("## Kaufempfehlung")
    j = zeilen.index("## Relevante Risiken und Hinweise")
    abschnitt = "\n".join(z for z in zeilen[i:j] if z.strip())
    return ent, gruende, abschnitt


def zeige(titel, ent, abschnitt):
    print("-" * 70)
    print(titel)
    print(f"  stufe={ent.empfehlung} state={ent.state} anzeige={ent.anzeige!r}")
    print(f"  hinweis={ent.hinweis!r}")
    for z in abschnitt.splitlines():
        print("  |", z)


# A) Live-ähnlich: weder Schwachstellen noch Wartung, kein Markt
a, ag, aa = lauf([], [], False)
zeige("A) Live-Lage", a, aa)
check("A: Kurzansicht-Anzeige 'Analyse eingeschränkt' mit fehlenden Daten", a.anzeige == ANZEIGE_N18, a.anzeige)
check("A: Hinweis nennt die Datenlage und ist keine Entwarnung",
      bool(a.hinweis) and "weder belastbare Schwachstellen- noch belastbare Wartungsdaten" in a.hinweis
      and "keine Entwarnung" in a.hinweis)
check("A: interne Stufe/Status/Identität unverändert",
      a.empfehlung == "kaufen_nach_besichtigung" and a.state == "NORMAL"
      and a.identitaet.stufe == "verified")
check("A: Bericht: Überschrift und Hinweis direkt in '## Kaufempfehlung'",
      "**ANALYSE EINGESCHRÄNKT:" in aa and "weder belastbare Schwachstellen-" in aa
      and "**KAUFEN NACH BESICHTIGUNG**" not in aa, aa)
check("A: Preis separat 'nicht bewertbar', pauschaler Satz ersetzt (Kurzansicht und Bericht)",
      "Preis nicht bewertbar: keine belastbare Marktpreisbasis." in ag
      and "rein technische Einschätzung" not in aa
      and "rein technische Einschätzung" not in " ".join(ag))

# B) belastbare Web-Befunde + Markt
b, bg, ba = lauf([ins("w1", "web_schwachstelle", trust="web"), ins("w2", "web_wartung", trust="web")],
                 ["schwachstelle", "wartung"], True)
zeige("B) belastbare Befunde", b, ba)
check("B: unverändert 'Kaufen nach Besichtigung', keine Einschränkung",
      b.anzeige == "Kaufen nach Besichtigung" and b.hinweis is None and not b.technik_luecke
      and "**KAUFEN NACH BESICHTIGUNG**" in ba and "Analyse eingeschränkt" not in ba, b.anzeige)

# C) nur die Marktbasis fehlt
c, cg, ca = lauf([ins("w1", "web_schwachstelle", trust="web")], ["schwachstelle"], False)
zeige("C) nur Marktbasis fehlt", c, ca)
check("C: Überschrift bleibt, Preis separat nicht bewertet, kein N18-Hinweis",
      c.anzeige == "Kaufen nach Besichtigung" and c.hinweis is None
      and any(g.startswith("Preis nicht bewertet") for g in cg), c.anzeige)

# D) strengere Warnungen haben Vorrang
d, dg, da = lauf([ins("s1", "schwachstelle", schweregrad="hoch", trust="verified")],
                 ["schwachstelle"], False)
zeige("D1) verifizierte schwere Schwachstelle", d, da)
check("D1: Floor-Stufe 'Nur mit Werkstattprüfung' bleibt, keine N18-Einschränkung",
      d.empfehlung == "nur_mit_werkstattpruefung" and d.anzeige == "Nur mit Werkstattprüfung"
      and not d.technik_luecke)
e, eg, ea = lauf([], [], False, rueckruf="failed")
zeige("D2) Recherchephase fehlgeschlagen", e, ea)
check("D2: fehlgeschlagene Phase hat Vorrang (LIMITED, eigener Wortlaut)",
      e.state == "LIMITED_ANALYSIS" and "Recherche nicht vollständig abgeschlossen" in e.anzeige
      and not e.technik_luecke, e.anzeige)
v = identitaet()
v.field_evidence["generation"] = {"verification_state": "user_only"}
p = entscheide("kaufen_nach_besichtigung", v, insights=[],
               web_recherche=SimpleNamespace(phasen_status={"technik": "partial"}, fakten=[]))
check("D3: Identität partial -> bestehender Wortlaut/Stufe unverändert",
      p.empfehlung == "unbekannt" and p.state == "LIMITED_ANALYSIS" and not p.technik_luecke, p.anzeige)

# E) Datenbankbefunde: nur GEPRÜFTE (trust=verified) zählen als Befund
for name, db in (("verifizierte DB-Wartung", ins("d1", "wartung", trust="verified")),
                 ("verifizierte DB-Schwachstelle (mittel)",
                  ins("d2", "schwachstelle", schweregrad="mittel", trust="verified")),
                 ("verifiziertes DB-Motorproblem", ins("d3", "motorproblem", trust="verified"))):
    x, xg, xa = lauf([db], [], False)
    check(f"E: {name} -> keine Einschränkung wegen fehlender Daten",
          not x.technik_luecke and x.anzeige != ANZEIGE_N18, x.anzeige)
    check(f"E: {name} -> Status unverändert",
          x.state == "NORMAL" and x.empfehlung in ("kaufen_nach_besichtigung", "nur_mit_werkstattpruefung"))
for name, db in (("ungeprüfte DB-Wartung (Default-Trust)", ins("u1", "wartung")),
                 ("ungeprüfte DB-Wartung (explizit)", ins("u2", "wartung", trust="unverified_db")),
                 ("ungeprüfte DB-Schwachstelle", ins("u3", "schwachstelle", schweregrad="mittel")),
                 ("ungeprüftes DB-Motorproblem", ins("u4", "motorproblem"))):
    x, xg, xa = lauf([db], [], False)
    zeige(f"E2) {name}", x, xa)
    check(f"E2: {name} hebt die Einschränkung NICHT auf",
          x.anzeige == ANZEIGE_N18 and x.technik_luecke == ("Schwachstellen", "Wartung"), x.anzeige)
    check(f"E2: {name} -> Stufe/Status unverändert (Floor greift bei ungeprüftem Fakt nicht)",
          x.empfehlung == "kaufen_nach_besichtigung" and x.state == "NORMAL")
# ungeprüfter DB-Eintrag + geprüfter Webbefund: der geprüfte Befund genügt
x, xg, xa = lauf([ins("u5", "wartung"), ins("w3", "web_wartung", trust="web")], ["wartung"], False)
check("E3: ungeprüfter DB-Eintrag + Webbefund -> keine Einschränkung", not x.technik_luecke, x.anzeige)

# F) Technikphase nicht gelaufen: keine Aussage über Abdeckung
f, fg, fa = lauf([], [], False, technik="not_run")
check("F: Technikphase nicht gelaufen -> kein N18-Fall",
      not f.technik_luecke and f.anzeige == "Kaufen nach Besichtigung", f.anzeige)

print()
if _FEHLER:
    print("FEHLER:", _FEHLER)
    raise SystemExit(1)
print("ALLE OK")
