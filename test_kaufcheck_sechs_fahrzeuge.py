"""
KaufCheck-Final-Stabilization — Sechs-Fahrzeug-Regression (offline).

Die sechs Production-Fälle, die die Bug-Klassen aufgedeckt haben, laufen hier
durch den ECHTEN Produktionspfad `run_kaufcheck` (Identität, Evidence,
Präsenz, Rückruf-Scope, Empfehlungs-Policy, Bericht). Nicht echt sind nur die
externen Provider:
  * Gemini ist gestubbt und liefert absichtlich schädlichen Freitext (der nicht
    durchdringen darf),
  * Tavily/Marktrecherche sind gesperrt,
  * der technische Web-Fallback läuft über den Fixture-Provider mit
    realistischen Snippets (nur beim DB-Miss relevant).

Die Prüfungen sind die GENERISCHEN Invarianten, angewandt auf diese sechs
Eingaben — keine Regel in app/ kennt eines dieser Fahrzeuge.

    python scripts/test_isolated.py test_kaufcheck_sechs_fahrzeuge.py
"""
import asyncio
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import kaufcheck as kc
from app.empfehlungs_policy import STATE_LIMITED, STATE_NORMAL
from app.models import KaufCheckRequest, KaufCheckResponse
from app.technical_research import FixtureTechnicalResearchProvider, recherchiere_technisch

_FEHLER: list[str] = []
_ANZAHL = {"n": 0}
ERGEBNIS: dict[str, str] = {}


def check(fzg: str, name: str, bedingung: bool, detail: str = "") -> None:
    _ANZAHL["n"] += 1
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {fzg}: {name}" + (f"  -> {detail}" if not bedingung and detail else ""))
    if not bedingung:
        _FEHLER.append(f"{fzg}: {name}")
        ERGEBNIS[fzg] = "FAIL"
    else:
        ERGEBNIS.setdefault(fzg, "PASS")


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# Realistische Web-Snippets für den DB-Miss (Inhalte sinngemäß wie deutsche
# Fach-/Behördenseiten; KEINE Anweisungen, nur Daten).
MX5_WEB = {
    "identitaet": [
        t("https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/mazda/mx-5/",
          "Mazda MX-5 (ND) seit 2015: Technische Daten",
          "Der Mazda MX-5 ND (seit 2015) ist die vierte Generation des Roadsters. Der 2.0 "
          "SKYACTIV-G leistet seit dem Modelljahr 2019 184 PS, Benziner, Hinterradantrieb, "
          "6-Gang-Schaltgetriebe."),
        t("https://www.auto-motor-und-sport.de/test/mazda-mx-5-2-0-skyactiv-g-184/",
          "Mazda MX-5 2.0 Skyactiv-G 184 im Test",
          "Die vierte Generation des Mazda MX-5 (ND, seit 2015): 2.0 Skyactiv-G mit 184 PS, "
          "Benziner, Heckantrieb und 6-Gang-Schaltgetriebe."),
        t("https://www.autobild.de/artikel/mazda-mx-5-nc-gebrauchtwagen/",
          "Mazda MX-5 NC Gebrauchtwagen-Check",
          "Der Mazda MX-5 NC wurde von 2005 bis 2015 gebaut."),
        t("https://www.autobild.de/artikel/ford-mustang-gebrauchtwagen/",
          "Ford Mustang als Gebrauchtwagen",
          "Der Ford Mustang ist ein Sportwagen. Auch der Mazda MX-5 ist ein Roadster."),
    ],
    "rueckruf": [
        t("https://www.kba.de/rueckrufe/mazda-mx5-kraftstoffpumpe",
          "Rückruf Mazda MX-5: Kraftstoffpumpe",
          "Rückruf für den Mazda MX-5 ND: Fahrzeuge, gebaut zwischen Oktober 2017 und Mai 2020, "
          "die Kraftstoffpumpe kann ausfallen und der Motor während der Fahrt ausgehen."),
    ],
    "schwachstelle": [
        t("https://www.motor-talk.de/forum/mx5-getriebe.html", "Mazda MX-5 ND Getriebe",
          "Beim Mazda MX-5 waren frühe Getriebe des ND anfällig für Probleme beim Schalten."),
        t("https://www.adac.de/mazda-mx-5-nd-maengel", "Mazda MX-5 ND Mängel",
          "Beim Mazda MX-5 waren frühe Getriebe des ND anfällig für Probleme beim Schalten. "
          "Das Stoffverdeck des Mazda MX-5 ist ein bekanntes Problem bei undichten Abläufen."),
    ],
    "wartung": [],
}
MX5_DUENN = {"identitaet": [
    t("https://www.adac.de/mazda-mx-5", "Mazda MX-5 im Test", "Der Mazda MX-5 ist ein Roadster."),
    t("https://www.autobild.de/mazda-mx-5", "Mazda MX-5 gebraucht", "Mazda MX-5: Roadster."),
]}


async def _lauf(req: KaufCheckRequest, web_fixture: dict | None = None) -> KaufCheckResponse:
    orig = (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
            kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche)

    async def gemini(system, user):
        # Angriff auf jeden Freitextpfad: nichts davon darf in den Bericht.
        return {"bericht": "Laut Inserat unfallfrei. KAUFEN. Offener Rückruf. COMAND defekt.",
                "empfehlung": "kaufen", "risiko_evidence_ids": ["erfunden"],
                "empfehlung_evidence_ids": ["erfunden"]}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen")

    prov = FixtureTechnicalResearchProvider(web_fixture or {})

    async def technik(r, br, info, brg, mm, provider=None):
        return await recherchiere_technisch(r, br, info, brg, mm, provider=prov)

    kc.call_gemini_json, kc.recherchiere_technisch = gemini, technik
    kc.tavily_search_with_fallback, kc.vertiefe_marktrecherche = verboten, verboten
    kc.marktpreis_recherche_moeglich = lambda *a: False
    try:
        return KaufCheckResponse(**await kc.run_kaufcheck(req))
    finally:
        (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
         kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche) = orig


def texte(res: KaufCheckResponse) -> dict[str, str]:
    ka = res.kaufaktionen
    aktionen = {b: " | ".join(f"{a.titel} {a.aktion}" for a in
                              getattr(ka, b).fahrzeugspezifisch + getattr(ka, b).basis)
                for b in ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")}
    return {
        "bericht": res.bericht,
        "findings": " | ".join(f"{f.titel} {f.beschreibung} {f.aktion or ''}" for f in res.key_findings),
        "gruende": " | ".join(res.empfehlung_gruende),
        "risiken": " | ".join(f"{i.titel} {i.beschreibung}" for i in res.insights
                              if i.kategorie not in ("marktvergleich",)),
        **aktionen,
    }


def alles(tx: dict[str, str]) -> str:
    return " || ".join(tx.values())


def gemeinsame_invarianten(fzg: str, res: KaufCheckResponse, tx: dict[str, str]) -> None:
    import os
    if os.environ.get("ENFAL_DUMP_DIR"):
        # Optional: vollständige Ausgaben zur manuellen Durchsicht ablegen.
        from pathlib import Path
        ziel = Path(os.environ["ENFAL_DUMP_DIR"]) / (re.sub(r"\W+", "_", fzg) + ".md")
        ziel.write_text("\n\n".join(f"## [{k}]\n{v}" for k, v in tx.items()), encoding="utf-8")
    a = alles(tx)
    check(fzg, "kein LLM-Freitext im Bericht", "COMAND defekt" not in a and "Offener Rückruf" not in a)
    check(fzg, "keine erfundenen Evidence-IDs", "erfunden" not in res.risiko_evidence_ids)
    check(fzg, "kein 'Datenbank + Web' ohne DB-Beitrag",
          not (res.quelle == "gemischt" and not res.baureihe_erkannt))
    check(fzg, "Überschrift und Empfehlung aus derselben Entscheidung",
          (res.recommendation_state == STATE_NORMAL) == ("analyse eingeschränkt" not in res.bericht.lower()))
    check(fzg, "Kosten nie als nackte Zahl",
          not re.search(r"(?:Kosten\w*|Reparaturkosten)[^.]{0,20}:\s*\d[\d.]*(?:\s|\.|$)(?!\s*€)", a))
    titel = res.anzeige_titel or ""
    # Klammergruppen ("(40 TFSI)") sind EINE Einheit (app/anzeige.py).
    woerter = [w.casefold() for w in re.findall(r"[\wÄÖÜäöüß.]+", re.sub(r"\([^)]*\)", "", titel))]
    check(fzg, f"Titel ohne Token-Wiederholung ({titel})", len(woerter) == len(set(woerter)))
    check(fzg, "Marke nie klein geschrieben im Titel", not titel[:1].islower())
    # Kraftstoff und Antriebsart nie auf derselben Achse
    fe = res.vehicle_identity["field_evidence"]
    check(fzg, "identity.fuel ist nie eine Antriebsart",
          (res.vehicle_identity.get("fuel") or "").lower() not in ("mild-hybrid", "plug-in-hybrid", "hybrid"))
    check(fzg, "kein Kraftstoff-Widerspruch Benzin vs. Hybrid",
          not [f for f in res.key_findings if f.kategorie == "widerspruch" and "Kraftstoff" in f.titel])
    check(fzg, "Benzin (Nutzer) bleibt Primärquelle",
          fe["fuel"]["primary_source"] == "user" and fe["fuel"]["raw_user_value"] == "Benzin")


def keine_unfallfreiheit(fzg: str, tx: dict[str, str]) -> None:
    a = alles(tx)
    for verboten in ("Laut Inserat unfallfrei", "trotz der Angabe „unfallfrei“", "Unfallfreiheit schriftlich festhalten",
                     "als unfallfrei an"):
        check(fzg, f"Unfall UNKNOWN: kein '{verboten}'", verboten not in a)


# ══════════════════════════════════════════════════════════════════════════════
async def main():
    # ── 1) BMW M4 F82 ──────────────────────────────────────────────────────────
    fzg = "BMW M4 F82"
    r = await _lauf(KaufCheckRequest(
        marke="BMW", modell="M4 F82", baujahr=2016, kilometerstand=69500, motor="S55B30 3.0 Biturbo Heck",
        kraftstoff="Benzin", leistung_ps=431, getriebe="Schaltgetriebe", ausstattung=[], preis_eur=43500,
        vorbesitzer=3, tuev_bis="05/2027", servicehistorie="teilweise", tuning="nicht angegeben"))
    tx = texte(r)
    gemeinsame_invarianten(fzg, r, tx)
    keine_unfallfreiheit(fzg, tx)
    vi = r.vehicle_identity
    check(fzg, "Generation F82, Motorcode S55B30 eindeutig", vi["generation"] == "F82" and vi["engine_code"] == "S55B30")
    check(fzg, "Getriebe manuell (Nutzer)", vi["transmission"] == "manuell")
    check(fzg, "Tuning 'nicht angegeben' -> unknown, keine Behauptung",
          r.insights is not None and "kein Tuning" not in alles(tx) and "Tuning vorhanden" not in alles(tx))
    # Kein RISIKO/keine AKTION zu einem Doppelkupplungsgetriebe. (Eine beiläufige
    # Erwähnung im Beschreibungstext eines anderen Bauteils oder die Getriebe-
    # optionen der Referenzvariante in der Vergleichstabelle sind kein Prüfpunkt.)
    dkg_risiken = [i.titel for i in r.insights if re.search(r"doppelkupplung|DKG|mechatronik",
                                                            f"{i.titel} {i.bauteil or ''}", re.I)]
    dkg_aktionen = [a.titel for b in ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")
                    for a in getattr(r.kaufaktionen, b).fahrzeugspezifisch
                    if re.search(r"doppelkupplung|DKG|mechatronik", a.titel, re.I)]
    check(fzg, "kein DKG-Risiko und keine DKG-Aktion am Schaltwagen", not dkg_risiken and not dkg_aktionen,
          str(dkg_risiken + dkg_aktionen)[:300])
    check(fzg, "Empfehlungsgründe ohne 'M4 F82 M4'", "M4 F82 M4" not in tx["gruende"] and "M4 M4" not in tx["gruende"])
    check(fzg, "Identität verifiziert -> normale Empfehlung", r.recommendation_state == STATE_NORMAL)
    edc = [i for i in r.insights if re.search(r"\bEDC\b|adaptiv", f"{i.titel} {i.bauteil or ''}", re.I)]
    check(fzg, "EDC (Ausstattung leer) nur bedingt",
          all(i.presence_state == "unknown" and i.titel.startswith("Falls ") for i in edc))
    for bereich in ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente"):
        teile = [s for s in tx[bereich].split(" | ") if re.search(r"\bEDC\b|adaptive D", s)]
        check(fzg, f"EDC in {bereich} nur bedingt", all("Falls " in s for s in teile), str(teile)[:200])
    print("   ", fzg, "->", r.recommendation_state, r.empfehlung, "|", r.anzeige_titel, "| EDC-Insights:", len(edc))

    # ── 2) BMW 330i G20 ────────────────────────────────────────────────────────
    fzg = "BMW 330i G20"
    r = await _lauf(KaufCheckRequest(
        marke="BMW", modell="330i G20", baujahr=2020, kilometerstand=64200, motor="B48 2.0 Turbo",
        kraftstoff="Benzin", leistung_ps=258, getriebe="Automatik", antrieb="Heck", ausstattung=[],
        vorbesitzer=2, tuev_bis="03/2027", servicehistorie="vollstaendig"))
    tx = texte(r)
    gemeinsame_invarianten(fzg, r, tx)
    keine_unfallfreiheit(fzg, tx)
    vi = r.vehicle_identity
    check(fzg, "Motorcode B48B20 (Nutzer B48 präzisiert, Rohwert bleibt)",
          vi["engine_code"] == "B48B20" and vi["field_evidence"]["engine_code"]["raw_user_value"] == "B48")
    check(fzg, "Hochvolt-/PHEV-Rückruf am Verbrenner entfernt",
          not [i for i in r.insights if i.kategorie == "rueckruf" and re.search(r"hochvolt", i.beschreibung, re.I)])
    check(fzg, "Identität verifiziert -> normale Empfehlung", r.recommendation_state == STATE_NORMAL)
    print("   ", fzg, "->", r.recommendation_state, r.empfehlung, "|", r.anzeige_titel)

    # ── 3) Audi A4 B9 ──────────────────────────────────────────────────────────
    fzg = "Audi A4 B9"
    r = await _lauf(KaufCheckRequest(
        marke="Audi", modell="A4 B9", baujahr=2018, kilometerstand=88400, motor="2.0 TFSI",
        kraftstoff="Benzin", leistung_ps=190, getriebe="Automatik", antrieb="Front", ausstattung=[]))
    tx = texte(r)
    gemeinsame_invarianten(fzg, r, tx)
    vi = r.vehicle_identity
    fe = vi["field_evidence"]
    check(fzg, "mehrere DB-Motorcodes -> nicht eindeutig, mögliche Codes gelistet",
          vi.get("engine_code") is None and fe["engine_code"]["verification_state"] == "ambiguous"
          and len(fe["engine_code"]["possible_values"]) >= 2)
    check(fzg, "Antriebsart ICE/MHEV mehrdeutig, kein Wert", vi.get("powertrain") is None
          and fe["powertrain"]["verification_state"] == "ambiguous")
    check(fzg, "Bericht: 'mögliche Motorcodes'", "mögliche Motorcodes" in r.bericht)
    check(fzg, "Benzin + Mild-Hybrid-Zeile ist kein Kraftstoffkonflikt",
          fe["fuel"]["verification_state"] == "user_confirmed")
    check(fzg, "Titel nennt keinen der möglichen Codes als Identität",
          not any(c in (r.anzeige_titel or "") for c in fe["engine_code"]["possible_values"]))
    check(fzg, "keine doppelte Leistung in der Vergleichstabelle",
          not re.search(r"190 PS[^|\n]*190 PS", r.bericht))
    check(fzg, "Motor-Anzeige ohne unbelegten Mild-Hybrid-Zusatz", "Hybrid" not in (vi.get("engine_name") or ""))
    print("   ", fzg, "->", r.recommendation_state, r.empfehlung, "|", r.anzeige_titel,
          "| codes:", fe["engine_code"].get("possible_values"), "| pt:", fe["powertrain"].get("possible_values"))

    # ── 4) Mercedes C300 W205 ──────────────────────────────────────────────────
    fzg = "Mercedes C300 W205"
    r = await _lauf(KaufCheckRequest(
        marke="Mercedes-Benz", modell="C 300 W205", baujahr=2019, kilometerstand=73600, motor="M264",
        kraftstoff="Benzin", leistung_ps=258, getriebe="Automatik", antrieb="Heck",
        ausstattung=["AMG Line", "Multibeam LED", "Burmester Soundsystem", "Panorama-Schiebedach",
                     "Sitzheizung", "elektrische Sitze", "Rückfahrkamera"]))
    tx = texte(r)
    gemeinsame_invarianten(fzg, r, tx)
    vi = r.vehicle_identity
    fe = vi["field_evidence"]
    check(fzg, "Nutzer-Motorcode M264 bleibt erhalten (DB kennt keinen)", vi["engine_code"] == "M264"
          and fe["engine_code"]["primary_source"] == "user")
    check(fzg, "Antriebsart ICE/MHEV nicht als sicher dargestellt",
          fe["powertrain"]["verification_state"] in ("ambiguous", "unknown"))
    comand = [s for b in ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")
              for s in tx[b].split(" | ") if "COMAND" in s]
    check(fzg, "COMAND (nicht angegeben) überall nur bedingt", all("Falls " in s for s in comand),
          str(comand)[:300])
    check(fzg, "COMAND-Risiko bedingt", all(i.titel.startswith("Falls ") for i in r.insights
                                           if "COMAND" in f"{i.titel} {i.bauteil or ''}"))
    fahrt = [s for s in tx["probefahrt"].split(" | ") if re.search(r"COMAND|Infotainment|Navi", s)]
    check(fzg, "Infotainment-Probefahrt ohne Beschleunigungs-/Ruckel-Template",
          all(not re.search(r"beschleunig|ruckel", s, re.I) for s in fahrt), str(fahrt)[:300])
    angegeben = [i for i in r.insights if re.search(r"burmester|panorama|multibeam", f"{i.titel} {i.bauteil or ''}", re.I)]
    check(fzg, "angegebene Ausstattung ist nicht bedingt", all(not i.titel.startswith("Falls ") for i in angegeben))
    print("   ", fzg, "->", r.recommendation_state, r.empfehlung, "|", r.anzeige_titel,
          "| COMAND-Aktionen:", len(comand))

    # ── 5) Opel Astra K ────────────────────────────────────────────────────────
    fzg = "Opel Astra K"
    r = await _lauf(KaufCheckRequest(
        marke="Opel", modell="Astra K", baujahr=2018, kilometerstand=92300, motor="1.4 Turbo",
        kraftstoff="Benzin", leistung_ps=125, getriebe="Schaltgetriebe", antrieb="Front", ausstattung=[],
        unfallfrei="nicht angegeben",
        beschreibung="Keine eindeutige Angabe zu früheren Unfallschäden oder Nachlackierungen."))
    tx = texte(r)
    gemeinsame_invarianten(fzg, r, tx)
    keine_unfallfreiheit(fzg, tx)
    vi = r.vehicle_identity
    fe = vi["field_evidence"]
    check(fzg, "accident_status unknown", r.accident_status == "unknown")
    check(fzg, "B14XFL/D14XFL/F14XFL -> mögliche Motorcodes, keine Identität",
          vi.get("engine_code") is None and len(fe["engine_code"].get("possible_values") or []) == 3)
    rr = [i for i in r.insights if i.kategorie == "rueckruf"]
    check(fzg, "kein Rückruf als 'Variante betroffen' ohne Variantenbeleg",
          all(i.recall_state in ("SERIES_RELEVANT", "UNKNOWN") for i in rr), str([(i.kurztitel, i.recall_state) for i in rr]))
    check(fzg, "Rückrufe FIN-first, nie 'betrifft dein Fahrzeug'",
          all("betrifft dein" not in (i.einfluss or "").lower() for i in rr))
    intelli = [s for b in ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")
               for s in tx[b].split(" | ") if re.search(r"intellilink", s, re.I)]
    check(fzg, "IntelliLink (Ausstattung leer) überall nur bedingt", all("Falls " in s for s in intelli), str(intelli)[:300])
    print("   ", fzg, "->", r.recommendation_state, r.empfehlung, "|", r.anzeige_titel,
          "| Rückrufe:", [(i.kurztitel, i.recall_state) for i in rr])

    # ── 6) Mazda MX-5 ND (DB-Miss) ─────────────────────────────────────────────
    fzg = "Mazda MX-5 (DB-Miss)"
    req6 = dict(marke="mazda", modell="MX-5", baujahr=2019, kilometerstand=58700, motor="2.0 SKYACTIV-G",
                kraftstoff="Benzin", leistung_ps=184, getriebe="Schaltgetriebe", antrieb="Heck", ausstattung=[],
                servicehistorie="vollstaendig")
    r = await _lauf(KaufCheckRequest(**req6), web_fixture=MX5_WEB)
    tx = texte(r)
    gemeinsame_invarianten(fzg, r, tx)
    keine_unfallfreiheit(fzg, tx)
    vi = r.vehicle_identity
    fe = vi["field_evidence"]
    check(fzg, "kein DB-Treffer", r.baureihe_erkannt is None)
    check(fzg, "Web-Generation ND erreicht die kanonische Identität",
          vi.get("generation") == "ND" and fe["generation"]["primary_source"] == "web")
    check(fzg, "Leistung: Nutzer Primärquelle, Web bestätigt",
          fe["horsepower"]["primary_source"] == "user" and "web" in fe["horsepower"]["confirmed_by"])
    check(fzg, "Motor bleibt Nutzerangabe (keine Umetikettierung)",
          fe["engine_name"]["primary_source"] == "user")
    check(fzg, "Identität verifiziert (Gen + Motorisierung) -> normale Empfehlung",
          r.recommendation_state == STATE_NORMAL)
    check(fzg, "Datenbasis ohne ENFAL-Datenbank, Chip 'web'",
          "ENFAL-Fahrzeugdatenbank" not in r.datenbasis and r.quelle == "web")
    check(fzg, "Marke 'mazda' -> 'Mazda'", (r.anzeige_titel or "").startswith("Mazda"))
    web_rr = [i for i in r.insights if i.kategorie == "web_rueckruf"]
    check(fzg, "Web-Rückruf Kraftstoffpumpe (Fenster 2017-2020) -> möglicherweise betroffen",
          any(i.applicability == "vehicle_possible" for i in web_rr), str([(i.titel, i.applicability) for i in web_rr]))
    check(fzg, "Ford-Mustang-/NC-Seiten nicht als Beleg",
          "ford-mustang" not in alles(tx) and "mx-5-nc" not in alles(tx))
    getriebe = [i for i in r.insights if i.kategorie == "web_schwachstelle" and "etriebe" in (i.bauteil or i.titel)]
    check(fzg, "'frühe Getriebe' nie als bekannter Schwachpunkt dieses 2019ers",
          all(i.geltung_fuer_fahrzeug == "unresolved" and i.confidence == "niedrig" for i in getriebe))
    check(fzg, "kein 'Datenbank + Web' im Bericht", "Datenbank + Web" not in r.bericht)
    print("   ", fzg, "->", r.recommendation_state, r.empfehlung, "|", r.anzeige_titel, "| quelle:", r.quelle,
          "| datenbasis:", r.datenbasis)

    # Mazda mit dünner Webquellenlage: Identitäts-Floor greift im echten Pfad.
    fzg = "Mazda MX-5 (DB-Miss, dünne Web-Identität)"
    r = await _lauf(KaufCheckRequest(**req6), web_fixture=MX5_DUENN)
    tx = texte(r)
    gemeinsame_invarianten(fzg, r, tx)
    check(fzg, "Generation/Motorisierung offen -> LIMITED_ANALYSIS, keine Freigabe",
          r.recommendation_state == STATE_LIMITED and r.empfehlung == "unbekannt")
    check(fzg, "Bericht: kein 'KAUFEN NACH BESICHTIGUNG'", "KAUFEN NACH BESICHTIGUNG" not in r.bericht)
    check(fzg, "Überschrift 'Fahrzeugidentität eingeschränkt'", "## Fahrzeugidentität eingeschränkt" in r.bericht)
    print("   ", fzg, "->", r.recommendation_state, r.empfehlung, "|", r.empfehlung_anzeige)


asyncio.run(main())

print("\n" + "=" * 60)
for fzg, st in ERGEBNIS.items():
    print(f"{st}  {fzg}")
print(f"{_ANZAHL['n']} Prüfungen")
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("SECHS-FAHRZEUG-REGRESSION GRUEN")
