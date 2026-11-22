"""
DB-05 -- unverifizierte DB-Wartungsregeln erreichen die Ausgabe (offline, Isolationsrunner).

    python scripts/test_isolated.py test_db_wartung_unverifiziert_ausgabe.py

Die Fälle sind ORIGINAL-Zeilen aus `kritische_wartung` (Seed, in die Temp-DB geladen). Sie laufen durch den echten
Produzentenpfad: DB-Zeile -> `build_insights` (trust bleibt `unverified_db`, Beschreibung lautet "Hinterlegter
Wartungshinweis: ...") -> `build_laufleistungskontext` -> `build_kaufaktionen`; zusätzlich `run_kaufcheck` mit gestubbtem
Gemini (erfasst die Berichtseingabe), leerem Web-Fixture und verbotenem Marktpfad. Es wird KEINE Verifikation gesetzt und
KEIN Präfix umgeschrieben.

Vor der Korrektur erzeugte jede dieser Zeilen KEINEN Kilometerhinweis (`_INTERVALL_SATZ` erwartete "Vorgesehenes
Intervall:"), und mehrere Kilometerwerte wurden auf den ersten reduziert.
"""
import asyncio
import re
import sqlite3
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import database, kaufcheck as kc
from app import laufleistung as ll
from app.evidence import build_insights
from app.kaufaktionen import build_kaufaktionen
from app.models import KaufCheckRequest, KaufCheckResponse
from app.technical_research import FixtureTechnicalResearchProvider, recherchiere_technisch

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f"  -> {detail}" if not bedingung and detail else ""))
    if not bedingung:
        _FEHLER.append(name)


con = sqlite3.connect(os.environ["AUTO_KI_DB_PATH"])
con.row_factory = sqlite3.Row


def zeile(wid: int):
    return con.execute(
        "select w.id,w.variante_id,w.bauteil,w.intervall,w.hinweis,m.bezeichnung,m.kraftstoff,m.leistung_ps,m.antrieb,"
        "b.marke,b.modell,b.generation,b.bauzeitraum_von from kritische_wartung w "
        "join motorvariante m on m.variante_id=w.variante_id join baureihe b on b.id=m.baureihe_id "
        "where w.id=?", (wid,)).fetchone()


def request_zu(r, km: int, baujahr: int | None = None) -> KaufCheckRequest:
    return KaufCheckRequest(marke=r["marke"], modell=r["modell"], baujahr=baujahr or r["bauzeitraum_von"] or 2010,
                            kilometerstand=km, motor=r["bezeichnung"], kraftstoff=r["kraftstoff"],
                            leistung_ps=r["leistung_ps"], antrieb=r["antrieb"], ausstattung=[])


def kette(wid: int, km: int, baujahr: int | None = None):
    """Direkter Produzentenpfad (kein Identitätsmatching): get_baureihe -> build_insights -> Laufleistung -> Aktionen."""
    r = zeile(wid)
    br = database.get_baureihe(r["marke"], r["modell"], r["generation"])
    motor = next(m for m in br["motoren"] if m["variante_id"] == r["variante_id"])
    req = request_zu(r, km, baujahr)
    ins = build_insights(br, motor, [], req, check_typ="kauf")
    mine = [i for i in ins if i.kategorie == "wartung" and i.bauteil == r["bauteil"]]
    ctx = ll.build_laufleistungskontext(req, ins)
    ka = build_kaufaktionen(req, br, motor, ins, laufleistungskontext=ctx)
    hs = [h for h in ctx.wartungshinweise if h.bauteil == r["bauteil"]] if ctx else []
    return r, mine, ctx, hs, ka


def aktionstext(ka, bereiche=("verkaeuferfragen", "dokumente")) -> str:
    out = []
    for b in bereiche:
        p = getattr(ka, b)
        out += [f"{a.titel} {a.aktion}" for a in p.fahrzeugspezifisch + p.basis]
    return "\n".join(out)


GEMINI: list[str] = []


async def volllauf(req):
    orig = (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
            kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche)

    async def gemini(system, user):
        GEMINI.append(user)
        return {"bericht": "STUB", "empfehlung": "kaufen", "risiko_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen")

    prov = FixtureTechnicalResearchProvider({})

    async def technik(rq, br, info, brg, mm, provider=None):
        return await recherchiere_technisch(rq, br, info, brg, mm, provider=prov)

    kc.call_gemini_json, kc.recherchiere_technisch = gemini, technik
    kc.tavily_search_with_fallback, kc.vertiefe_marktrecherche = verboten, verboten
    kc.marktpreis_recherche_moeglich = lambda *a: False
    try:
        return KaufCheckResponse(**await kc.run_kaufcheck(req))
    finally:
        (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
         kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche) = orig


FAELLIGKEIT = re.compile(r"überfällig|ueberfaellig|versäumt|nicht durchgeführt|bestätigte Herstellervorgabe", re.I)


def unbehauptet(text: str) -> bool:
    """Weder Fälligkeit noch Versäumnis noch eine bestätigte Herstellervorgabe wird behauptet."""
    return not FAELLIGKEIT.search(text) and not re.search(r"\bfällig\b", text, re.I)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A) einfacher eindeutiger Kilometerfall (Seed 34, 'Alle 20.000 km'), trust bleibt unverified_db ===")
r, mine, ctx, hs, ka = kette(34, 45000)
check("A0 Originalzeile", r["intervall"] == "Alle 20.000 km" and r["bauteil"] == "Zündkerzen", str(tuple(r)))
check("A1 Insight vorhanden, trust unverändert unverified_db, Wortlaut 'Hinterlegter Wartungshinweis'",
      len(mine) == 1 and mine[0].trust == "unverified_db" and "Hinterlegter Wartungshinweis:" in mine[0].beschreibung)
check("A2 Intervall als strukturierter Träger", mine and mine[0].wartung_intervall == "Alle 20.000 km")
check("A3 Kilometerhinweis entsteht", len(hs) == 1, str(len(hs)))
h = hs[0] if hs else None
check("A4 Punkt 20.000 km, Status darueber, Bezug auf die Evidence", h and h.punkt_km == 20000
      and h.status == "darueber" and h.evidence_id == mine[0].id)
check("A5 Prüfstatus im Hinweis und in den Grenzen sichtbar", h and "Ungeprüfte Datenbankangabe" in h.hinweis
      and any(g.startswith("Ungeprüfte Datenbankangabe") for g in h.grenzen))
check("A6 keine Fälligkeit/Versäumnis/Herstellervorgabe behauptet", h and unbehauptet(h.hinweis + (h.kurz or "")))
check("A7 Tätigkeit nicht erfunden (Intervalltext nennt keine)", h and h.taetigkeit is None
      and "Tätigkeit nicht belegt" in (h.intervall_kurz or ""))
aktionen_a = aktionstext(ka)
check("A8 Frage/Dokument tragen den Prüfstatus", "ungeprüft" in aktionen_a.lower())


print("\n=== B) Variantenalternativen ohne belegte Auswahl (Seed 1107 '(PD)/(CR)', 1432 Spannen, 779 'je nach') ===")
for wid, km in ((1107, 135000), (1107, 165000), (1432, 135000), (1432, 165000), (779, 135000)):
    r, mine, ctx, hs, ka = kette(wid, km, 2009)
    h = hs[0] if hs else None
    tag = f"B{wid}@{km}"
    check(f"{tag} Hinweis entsteht, trust unverändert", len(hs) == 1 and mine[0].trust == "unverified_db", str(len(hs)))
    check(f"{tag} kein gewählter Kilometerpunkt, Status 'unbewertet', keine Differenz",
          h and h.punkt_km is None and h.punkt_bis_km is None and h.differenz_km is None and h.status == "unbewertet")
    check(f"{tag} kein 'erreicht'/'im Bereich' aus einem früheren Wert",
          h and not re.search(r"rechnerisch (mindestens einmal )?erreicht|im relevanten Bereich|liegt im Bereich", h.hinweis + (h.kurz or "")))
    check(f"{tag} km_status 'alternativen', Original-Intervall erhalten",
          h and h.km_status == "alternativen" and h.intervall_text == r["intervall"])
    check(f"{tag} beide Alternativen samt Zuordnung stehen im Hinweis",
          h and all(t in h.hinweis for t in re.findall(r"\(P[D]\)|\(CR\)|je nach Motorcode|120\.000|180\.000|150\.000|210\.000", r["intervall"])))
    check(f"{tag} Grenze nennt die Alternativen ohne belegte Auswahl",
          h and any(g.startswith("Mehrere alternative Kilometerangaben ohne belegte Auswahl") for g in h.grenzen))
    txt = aktionstext(ka)
    check(f"{tag} Aktionen erwähnen die fehlende Auswahl/Bedingung", "alternative Kilometerangaben" in txt or "Voraussetzung" in txt)
    check(f"{tag} Rang nicht angehoben (kein belegt erreichter Punkt)",
          all(a.prioritaet != "kritisch" for a in ka.verkaeuferfragen.fahrzeugspezifisch if a.kategorie == "wartung"))
r, mine, ctx, hs, ka = kette(779, 135000, 2009)
check("B779 'je nach Motorcode' bleibt als bedingte Regel erhalten",
      hs and hs[0].regeln and hs[0].regeln[0].art == "bedingt" and "je nach Motorcode" in hs[0].regeln[0].wortlaut)
check("B779 die Voraussetzung steht in den Vorbehalten der Aktionen", "Voraussetzung" in aktionstext(ka))
r, mine, ctx, hs, ka = kette(1107, 135000, 2009)
check("B1107 PD-Wasserpumpen-Hinweis der Zeile bleibt auf der Evidence-Karte", "Bei PD-Motoren" in mine[0].beschreibung)


print("\n=== C) Erst-/Folgeintervall mit ausdrücklicher Prüfung (Seed 1605 bZ4X) ===")
r, mine, ctx, hs, ka = kette(1605, 160000, 2023)
h = hs[0] if hs else None
check("C0 Originalzeile", "Erste Prüfung nach 150.000 km oder 10 Jahren, danach alle 30.000 km oder 2 Jahre" == r["intervall"])
check("C1 Tätigkeit 'pruefen' stammt aus dem Intervalltext, nicht aus dem Bauteil",
      mine[0].taetigkeit == "pruefen" and h and h.taetigkeit == "pruefen")
check("C2 Kurzform nennt Prüfung, nicht 'Tätigkeit nicht belegt'", h and (h.intervall_kurz or "").startswith("Prüfung:")
      and "Tätigkeit nicht belegt" not in (h.kurz or ""))
check("C3 Erst- und Folgeintervall samt Zeitalternativen bleiben sichtbar",
      h and "Erstintervall 150.000 km oder 10 Jahre" in h.kurz and "danach 30.000 km / 2 Jahre" in h.kurz)
check("C4 Kilometerstatus nur für das Erstintervall (im Bereich), Zeit nicht erreicht, Status ungeprüft sichtbar",
      h and h.status == "im_bereich" and h.zeit_status == "nicht_erreicht"
      and any(g.startswith("Ungeprüfte Datenbankangabe") for g in h.grenzen))
r2, mine2, ctx2, hs2, ka2 = kette(1605, 40000, 2023)
check("C5 weit vor dem Erstpunkt kein Hinweis (keine Warnung ohne Anlass)", hs2 == [])
check("C6 keine Fälligkeit behauptet", unbehauptet(h.hinweis + h.kurz + aktionstext(ka)))
check("C7 Frage fragt nach der Prüfung (belegte Tätigkeit)",
      any("geprüft" in a.titel.lower() for a in ka.verkaeuferfragen.fahrzeugspezifisch if a.kategorie == "wartung"))


print("\n=== D) Herstellerangabe 'Lifetime' gegen bloße Wechselempfehlung (Seed 935) ===")
r, mine, ctx, hs, ka = kette(935, 90000, 2009)
h = hs[0] if hs else None
check("D0 Originalzeile", "Lifetime" in r["intervall"] and "empfohlen" in r["intervall"])
check("D1 Hinweis entsteht, Bereich 60.000-120.000 km", h and h.punkt_km == 60000 and h.punkt_bis_km == 120000)
check("D2 Einordnung im Wortlaut bleibt in Grenzen und Hinweistext erhalten",
      h and any(g.startswith("Hinterlegter Wortlaut mit Einordnung") and "Lifetime" in g and "empfohlen" in g for g in h.grenzen)
      and "Lifetime" in h.hinweis and "empfohlen" in h.hinweis)
check("D2b auch der Kurztext (`kurz`) nennt Herstellerangabe 'Lifetime' und die Wechselempfehlung",
      h and "Lifetime" in (h.kurz or "") and "empfohlen" in (h.kurz or ""))
from app.kaufaktionen import wartung_kurzbegruendung   # noqa: E402
check("D3 und im Karten-Vorbehalt (Kurzbegründung)", "Lifetime" in (wartung_kurzbegruendung(h) or ""))
alles = aktionstext(ka)
check("D4 Lifetime/Empfehlung steht in Frage oder Dokument oder Sammelpunkt (voller Sammelpfad, nicht ID-gefiltert)",
      "Lifetime" in alles, alles[-400:])
check("D5 Tätigkeit stammt aus dem Intervalltext ('Wechsel empfohlen')", mine[0].taetigkeit == "ersetzen")


print("\n=== E) reine Zeitregel und nichtnumerische Regeln: keine erfundenen Kilometer ===")
def erste_mit_insight(*intervalle: str) -> int:
    """Kleinste Seed-ID mit diesem Intervalltext, zu der der Produzent unter den Kette-Bedingungen ein Wartungs-Insight
    erzeugt (die Motorgate-Filter schließen manche Zeilen aus; das ist nicht Gegenstand dieses Tests)."""
    for intervall in intervalle:
        for (wid,) in con.execute("select id from kritische_wartung where intervall=? order by id", (intervall,)).fetchall():
            if kette(wid, 120000, 2012)[1]:
                return wid
    raise AssertionError(f"keine Zeile mit Insight fuer {intervalle!r}")


zeit_id = erste_mit_insight("Alle 2 Jahre")
frei_id = erste_mit_insight("Kein fester Intervall", "Nach Bedarf", "Bei Bedarf", "Regelmäßige Diagnose",
                            "Sichtprüfung bei jeder Inspektion")
for wid in (zeit_id, frei_id):
    r, mine, ctx, hs, ka = kette(wid, 120000, 2012)
    check(f"E{wid} Insight bleibt mit Intervalltext ({r['intervall']!r})", len(mine) == 1 and mine[0].wartung_intervall == r["intervall"])
    check(f"E{wid} kein Kilometerhinweis, kein erfundener Kilometerwert", hs == [])
    check(f"E{wid} Verkäuferfrage/Nachweis bleiben (ohne Hinweis)",
          any(mine[0].id in a.evidence_ids for b in ("verkaeuferfragen", "dokumente")
              for a in getattr(ka, b).fahrzeugspezifisch + getattr(ka, b).basis))


print("\n=== F) Weitergabe: Berichtseingabe, deterministischer Bericht, Fragen, Dokumente (Volllauf Seed 1107) ===")
r = zeile(1107)
res = asyncio.run(volllauf(request_zu(r, 135000, 2009)))
eingabe = GEMINI[-1]
w_ins = [i for i in res.insights if i.kategorie == "wartung" and i.bauteil == "Zahnriemen"]
hs = [h for h in (res.laufleistungskontext.wartungshinweise if res.laufleistungskontext else []) if h.bauteil == "Zahnriemen"]
check("F0 DB-Treffer über das echte Matching, Quelle Datenbank", res.baureihe_erkannt == "volkswagen-passat-b6"
      and res.motor_erkannt == "volkswagen-passat-b6-2.0-tdi-140ps" and res.quelle == "datenbank")
check("F1 Insight trust unverified_db, Hinweis vorhanden und unbewertet", w_ins and w_ins[0].trust == "unverified_db"
      and len(hs) == 1 and hs[0].status == "unbewertet")
check("F2 GEMINI-EINGABE: Laufleistungsblock enthält die Wartungszeile samt Alternativen und Prüfstatus",
      f"Wartungspunkt „Zahnriemen“ (Beleg {w_ins[0].id})" in eingabe and "(PD)" in eingabe and "(CR)" in eingabe
      and "Ungeprüfte Datenbankangabe" in eingabe)
check("F3 GEMINI-EINGABE: Evidence-Zeile kennzeichnet den Eintrag als ungeprüft",
      any(z.startswith(f"[{w_ins[0].id}]") and "ungeprüft" in z for z in eingabe.splitlines()))
check("F4 gestubbte Gemini-Ausgabe wirkt nicht auf Bericht (nur Auswahl-IDs): 'STUB' fehlt im Bericht", "STUB" not in res.bericht)
bericht_abschnitt = res.bericht.split("## Relevante Risiken und Hinweise", 1)[1].split("\n## ", 1)[0]
check("F5 BERICHT (deterministisch), Abschnitt 'Relevante Risiken': Titel, Hinweis- und Intervalltext der Evidence",
      "Zahnriemen: hinterlegter Wartungshinweis" in bericht_abschnitt and "120.000 km (PD) / 180.000 km (CR)" in bericht_abschnitt
      and "Bei PD-Motoren auf Ölpumpenantrieb achten" in bericht_abschnitt)
check("F6 BERICHT: Verkäuferfrage zum Zahnriemen mit Alternativen und Prüfstatus",
      any("Zahnriemen" in z and "(PD)" in z and "(CR)" in z and "ungeprüfte" in z and z.startswith("- **Wurde die Wartung")
          for z in res.bericht.splitlines()),
      " || ".join(z[:400] for z in res.bericht.splitlines() if "Zahnriemen" in z))
fragen = [a for a in res.kaufaktionen.verkaeuferfragen.fahrzeugspezifisch if w_ins[0].id in a.evidence_ids]
dok = [a for a in res.kaufaktionen.dokumente.fahrzeugspezifisch if w_ins[0].id in a.evidence_ids]
check("F7 genau eine Frage und ein Nachweis zum Punkt (kein Duplikat durch Hinweis + Insight)", len(fragen) == 1 and len(dok) == 1,
      f"{len(fragen)} / {len(dok)}")
check("F8 Frage trägt Herkunft/Prüfstatus und die Alternativen; Dokument verlangt Nachweis mit Bezug",
      fragen and "ungeprüft" in fragen[0].aktion.lower() and "alternative Kilometerangaben" in fragen[0].aktion
      and dok and "Bezug laut ungeprüfter Datenbankangabe" in dok[0].aktion)
check("F9 Evidence-Karte: Kurzbegründung benennt Prüfstatus und fehlende Auswahl",
      "ungeprüfte Orientierung" in (w_ins[0].kurzbegruendung or "") and "ohne belegte Auswahl" in (w_ins[0].kurzbegruendung or ""))
check("F10 nirgends Fälligkeit, Versäumnis oder bestätigte Herstellervorgabe (Ausgabe, ohne Prompt-Verbote)",
      unbehauptet(res.bericht + hs[0].hinweis + (w_ins[0].kurzbegruendung or "")))


print("\n=== G) Sammelpfad: Seed 935 im Volllauf, Getriebeöl (Tiptronic) darf nicht allein wegen Limit/Filter verloren gehen ===")
r = zeile(935)
res = asyncio.run(volllauf(request_zu(r, 90000, 2009)))
w9 = [i for i in res.insights if i.kategorie == "wartung" and i.bauteil == "Getriebeöl (Tiptronic)"]
fragen_alle = aktionstext(res.kaufaktionen, ("verkaeuferfragen",))
dok_alle = aktionstext(res.kaufaktionen, ("dokumente",))
check("G0 Insight vorhanden", len(w9) == 1)
print("   Verkäuferfragen erwähnen 'Getriebeöl (Tiptronic)':", "Getriebeöl (Tiptronic)" in fragen_alle,
      "| Dokumente:", "Getriebeöl (Tiptronic)" in dok_alle)
check("G1 Verkäuferfragen: der Punkt ist konkret sichtbar (Einzelaktion ODER Sammelzeile)", "Getriebeöl (Tiptronic)" in fragen_alle)
check("G2 Dokumente: der Punkt ist konkret sichtbar", "Getriebeöl (Tiptronic)" in dok_alle)
check("G3 Einordnung 'Lifetime/empfohlen' bleibt in Fragen oder Dokumenten sichtbar", "Lifetime" in fragen_alle or "Lifetime" in dok_alle)
check("G4 Evidence-ID des Punktes steht an mindestens einer Frage und einem Nachweis",
      any(w9[0].id in a.evidence_ids for a in res.kaufaktionen.verkaeuferfragen.fahrzeugspezifisch + res.kaufaktionen.verkaeuferfragen.basis)
      and any(w9[0].id in a.evidence_ids for a in res.kaufaktionen.dokumente.fahrzeugspezifisch + res.kaufaktionen.dokumente.basis))

# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{'ALLE GRUEN' if not _FEHLER else 'FEHLER: ' + ', '.join(_FEHLER)}")
raise SystemExit(1 if _FEHLER else 0)
