"""
Marktanalyse-Sprint — Live-Diagnose der automatischen Marktanalyse (§12/§13).

Fährt die ECHTE Kaufcheck-Marktpipeline mit Cache-Bypass für zwei Testfälle:

  A) BMW 320d G20, 2019, 190 PS, Diesel, Automatik, 120.000 km
  B) Opel Insignia B Grand Sport, 2020, 2.0 Diesel, 174 PS, Automatik, 115.000 km

Je Lauf werden dokumentiert (§13): rohe Treffer, validierte einzigartige Fahrzeuge,
sehr ähnlich / ähnlich / bedingt / verworfen samt Ablehnungsgründen, verwendete
Domains, Datenqualität, Marktabdeckung, best_so_far-Verlauf, Median, Marktspanne und
der finale research_status. Zusätzlich (§12) JEDE median-tragende MarketObservation
mit allen Feldern einzeln — damit nachprüfbar ist, ob es sich wirklich um mehrere
UNTERSCHIEDLICHE Fahrzeuge handelt.

WICHTIG (Abweichung zur früheren Root-Cause-Diagnose): `baue_ziel` bekommt hier
dieselben Referenzdaten wie die Produktion (alle Baureihen + alle Motorvarianten).
Das alte Skript übergab zwei leere Listen — damit waren Fremdmodell- UND
Fremdmotor-Erkennung inaktiv und die Messung nicht repräsentativ.

Nutzt ECHTE Tavily-Calls (TAVILY_API_KEY nötig) — kostet Credits.

    python scripts/diagnose_marktanalyse.py                  # beide Fälle, je 3 Läufe
    python scripts/diagnose_marktanalyse.py bmw              # nur Fall A
    python scripts/diagnose_marktanalyse.py insignia         # nur Fall B
    python scripts/diagnose_marktanalyse.py bmw --speichern  # 1 Lauf + Rohdaten als JSON

`--speichern` legt den kompletten Lauf (Suchergebnisse inkl. raw_content sowie jede
extrahierte Fahrzeugkarte mit ihrem isolierten Kartentext) unter `diagnose_runs/`
ab — der Ordner ist per .gitignore ausgeschlossen. Nur damit lässt sich nach dem
Lauf noch manuell prüfen, ob die Karten sauber getrennt wurden; Tavilys Antworten
leben sonst nur 300 s im Prozess-Cache. Mit `--speichern` läuft je Fall bewusst nur
EIN Durchgang (Kontingentschonung).
"""
from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, ".")

from app.car_lookup import find_baureihe, find_motor                       # noqa: E402
from app.config import TAVILY_API_KEY                                      # noqa: E402
from app.database import (                                                 # noqa: E402
    get_alle_baureihen_kurz, get_alle_motorvarianten_kurz,
)
import app.marktrecherche as mr                                            # noqa: E402
from app.marktrecherche import (                                           # noqa: E402
    baue_deep_queries, baue_rare_queries, vertiefe_marktrecherche,
)
from app.marktvergleich import (                                           # noqa: E402
    _bewerte, _cap_pro_url, _eindeutige_karosserie, _extrahiere_aus_text,
    _karten_hash, _trim_ausreisser, _zaehlt_als_fahrzeug, analysiere_markt, baue_ziel,
    ist_teile_suchseite,
)
from scripts.diagnose_store import DiagnoseRecorder                        # noqa: E402
from app.vehicle_identity import VehicleIdentity                           # noqa: E402
from app.web_search import (                                               # noqa: E402
    US_QUELLEN_AUSSCHLUSS, ist_einzelinserat, ist_info_domain, ist_kategorieseite,
    ist_marktplatz_domain,
)

FAELLE = {
    # "G20" gehört zur Fahrzeugidentität des Regressionsfalls und muss deshalb in der
    # Nutzerangabe stehen. Die Datenbank führt Limousine und Touring in EINER
    # Baureihe ("G20/G21") und kennt G20 nicht getrennt — ohne die explizite Angabe
    # gelten beide Codes als Ziel, und G21-Tourings landen im Median.
    "bmw": dict(marke="BMW", modell="320d G20", baujahr=2019, kilometerstand=120_000,
                motor="320d 190 PS", kraftstoff="Diesel", getriebe="Automatik",
                preis_eur=24_900),
    "insignia": dict(marke="Opel", modell="Insignia Grand Sport", baujahr=2020,
                     kilometerstand=115_000, motor="2.0 Diesel 174 PS",
                     kraftstoff="Diesel", getriebe="Automatik", preis_eur=17_900),
}


def _abschnitt(titel: str) -> None:
    print("\n" + "=" * 88)
    print(titel)
    print("=" * 88)


def _klassifiziere(url: str, titel: str) -> str:
    if ist_info_domain(url):
        return "info"
    if ist_einzelinserat(url, titel):
        return "listing_detail"
    if ist_kategorieseite(url, titel):
        return "marktplatz_trefferliste" if ist_marktplatz_domain(url) else "aggregator_kategorie"
    return "unknown"


def karten_mit_text(results: list[dict], ziel: dict) -> list[dict]:
    """Bewertet ALLE extrahierten Datenpunkte neu — inklusive der verworfenen, die
    `analysiere_markt` (bewusst) nicht zurückgibt — und hält dabei den ISOLIERTEN
    Kartentext fest.

    Der Kartentext existiert in der Produktionspipeline nur flüchtig: er wird von
    `_extrahiere_aus_text` in `gruende[0]` (mit \\x00 als Marker) zwischengelegt und
    von `_bewerte` sofort durch die Bewertungsgründe ersetzt. Für die Diagnose wird
    er hier ABGEGRIFFEN, bevor `_bewerte` läuft — ohne Eingriff in den
    Produktionscode.

    Rückgabe je Karte: {url, card_index, card_text, card_hash, beobachtung}.
    """
    out: list[dict] = []
    for r in results:
        url, titel = r.get("url", "") or "", r.get("title", "") or ""
        if ist_info_domain(url) or ist_teile_suchseite(url, titel):
            continue
        if ist_einzelinserat(url, titel):
            typ = "listing"
        elif ist_kategorieseite(url, titel):
            typ = "market_category" if ist_marktplatz_domain(url) else "category"
        else:
            typ = "unknown"
        inhalt = r.get("content", "") or ""
        raw = (r.get("raw_content") or "")[:20_000]
        text = f"{titel}\n{inhalt}\n{raw}"
        grenzen = (len(titel) + 1, len(titel) + 1 + len(inhalt) + 1)
        roh = _extrahiere_aus_text(text, url, typ, grenzen=grenzen,
                                   seiten_body=_eindeutige_karosserie(f"{url} {titel}"))
        for i, b in enumerate(roh):
            kartentext = b.gruende[0][1:] if (b.gruende and b.gruende[0].startswith("\x00")) else ""
            bewertet = _bewerte(b, ziel)
            out.append({
                "url": url, "card_index": i, "card_text": kartentext,
                # §4: Der Card-Hash ist nur bei einer strukturell bestätigten Karte
                # eine Identität. Über ein Zeichenfenster wäre er eine
                # Scheingenauigkeit — dort bleibt er bewusst leer, damit die
                # Diagnosedatei die schwache Herkunft nicht kaschiert.
                "card_hash": (None if bewertet.window_fallback_used
                              else _karten_hash(kartentext)),
                "beobachtung": bewertet,
            })
    return out


def _alle_beobachtungen(results: list[dict], ziel: dict) -> list:
    """Nur die bewerteten Beobachtungen (ohne Kartentext) — für die Konsolenausgabe."""
    return [k["beobachtung"] for k in karten_mit_text(results, ziel)]


def _acceptance_status(b, verwendet_keys: set, kontext_keys: set,
                       schon_gezaehlt: set | None = None) -> str:
    """Wie ist es dieser Karte am Ende ergangen (§ Diagnose-Persistenz)?

    `schon_gezaehlt` (optional): bereits vergebene listing_keys. Dieselbe Anzeige
    taucht in der Rohextraktion mehrfach auf (mehrere Rechercheseiten zeigen sie),
    `analysiere_markt` führt sie aber zu EINER Beobachtung zusammen. Ohne diesen
    Abgleich bekämen alle Fundstellen den Status "verwendet" und die Methodenzähler
    im Diagnosekopf zählten dieselbe Karte mehrfach — der Kopf würde also nicht mehr
    zu den tatsächlich verwendeten Marktbeobachtungen passen.
    """
    if b.vergleichbarkeit == "ungeeignet":
        return "rejected"
    if b.listing_key in verwendet_keys:
        if schon_gezaehlt is not None:
            if b.listing_key in schon_gezaehlt:
                return "dublette"
            schon_gezaehlt.add(b.listing_key)
        return "verwendet"
    if b.vergleichbarkeit == "bedingt" or b.listing_key in kontext_keys:
        return "conditional"
    # Fachlich passend, aber unterwegs herausgefallen (Dublette, Deckelung pro
    # Seite, Ausreißer-Trim, Kategorieseiten-Regel).
    return "gefiltert"


def _zeige_beobachtung(b, praefix: str = "  ") -> None:
    print(f"{praefix}listing_key      : {b.listing_key}")
    print(f"{praefix}listing_id       : {b.listing_id}")
    print(f"{praefix}quelle/detail    : {b.detail_url or b.quelle_url}")
    print(f"{praefix}Marke/Modell     : {b.make} / {b.model}")
    print(f"{praefix}Generation       : {b.generation}")
    print(f"{praefix}Motor / Leistung : {b.engine_variant} / "
          f"{b.horsepower if b.horsepower is not None else '—'} PS")
    print(f"{praefix}Kraftstoff/Karo. : {b.fuel} / {b.body}")
    print(f"{praefix}Getriebe         : {b.transmission}")
    print(f"{praefix}Baujahr / km     : {b.baujahr} / {b.kilometerstand}")
    print(f"{praefix}Preis            : {b.preis_eur} €")
    print(f"{praefix}Similarity       : {b.vergleichbarkeit} ({b.similarity})")
    print(f"{praefix}extraction_source: {b.extraction_source} (source_type={b.source_type})")
    print(f"{praefix}Begründung       : {b.acceptance_reason}")
    print(praefix + "-" * 70)


async def einzelner_lauf(fall: str, lauf_nr: int, *, speichern: bool = False) -> dict:
    _abschnitt(f"FALL {fall.upper()} — LAUF {lauf_nr} (Cache-Bypass)")
    req = SimpleNamespace(**FAELLE[fall])
    print(f"Eingabe: {FAELLE[fall]}")
    t0 = time.perf_counter()

    # Rohdaten-Mitschnitt (optional, §Diagnose-Persistenz): Tavilys Antworten leben
    # sonst nur 300 s im Prozess-Cache und sind nach dem Lauf unwiederbringlich weg.
    # Der Wrapper puffert Query + Treffer; die Zuordnung zur Query-STUFE erfolgt
    # nach dem Lauf anhand des Stufen-Logs (gleiche Reihenfolge).
    recorder = DiagnoseRecorder(fall, lauf_nr, eingabe=dict(FAELLE[fall])) if speichern else None
    mitschnitt: list[tuple[str, list]] = []
    original_suche = mr.tavily_search_mit_status

    async def _mitschneidende_suche(query, *a, **kw):
        results, fehler = await original_suche(query, *a, **kw)
        mitschnitt.append((query, list(results or [])))
        return results, fehler

    if recorder:
        mr.tavily_search_mit_status = _mitschneidende_suche

    baureihe = find_baureihe(req.marke, req.modell, req.baujahr)
    motor_match = find_motor(baureihe, req.motor) if baureihe else None
    print(f"Baureihe erkannt : {baureihe.get('id') if baureihe else None}")
    print(f"Motor erkannt    : {motor_match.get('bezeichnung') if motor_match else None}")

    # Identisch zur Produktion (app/kaufcheck.py) — vollständige Referenzdaten.
    ziel = baue_ziel(baureihe, motor_match, req,
                     get_alle_baureihen_kurz() if baureihe else [],
                     get_alle_motorvarianten_kurz() if baureihe else [])
    print(f"Zielprofil       : gen={sorted(ziel['generation_tokens'])} "
          f"motor={sorted(ziel['ziel_motor_tokens'])} "
          f"fremd_motor={sorted(ziel['fremd_motor_tokens'])} "
          f"ps={ziel['leistung_ps']} karosserie={ziel['karosserie']} "
          f"kraftstoff={ziel['kraftstoff']} facelift={ziel['facelift_jahr']}")

    identity = VehicleIdentity.from_market_context(baureihe, motor_match, req)
    try:
        ergebnisse, ma, diag = await vertiefe_marktrecherche(
            [], baue_deep_queries(identity), ziel, req.preis_eur, US_QUELLEN_AUSSCHLUSS,
            count=20, zweck=f"{fall}-lauf-{lauf_nr}",
            rare_queries=baue_rare_queries(identity), bypass_cache=True)
    finally:
        mr.tavily_search_mit_status = original_suche

    # ── Query-Stufen ─────────────────────────────────────────────────────────
    print(f"\nQuery-Stufen durchlaufen: {len(diag['stufen'])}")
    for s in diag["stufen"]:
        print(f"  [{s.get('stufe')}] {s.get('label')!r} query={s.get('query')!r}")
        print(f"      felder={s.get('felder')} weggelassen={s.get('weggelassene_felder')}")
        print(f"      roh={s.get('roh')} neu={s.get('neu')} akzeptiert={s.get('akzeptiert')} "
              f"quali={s.get('quali')} abdeckung={s.get('abdeckung')}")

    # ── best_so_far-Verlauf (§11) ────────────────────────────────────────────
    print("\nbest_so_far-Verlauf:")
    for e in diag["best_so_far"]:
        marke = "  <== ÜBERNOMMEN" if e["uebernommen"] else ""
        print(f"  [{e['stufe']:<8}] status={e['status']:<17} quali={e['quali']:<8} "
              f"abdeckung={e['abdeckung']:<14} verwendet={e['verwendet']:<3}{marke}")
    print(f"  bester Stand aus Stufe: {diag['bester_stand_stufe']}")

    # ── Rohtreffer-Klassifikation ────────────────────────────────────────────
    typen: dict[str, int] = {}
    for r in ergebnisse:
        t = _klassifiziere(r.get("url", ""), r.get("title", ""))
        typen[t] = typen.get(t, 0) + 1
    print(f"\nRohe Treffer (Seiten) im besten Stand: {len(ergebnisse)}")
    for k, v in typen.items():
        print(f"  {k:<16}: {v}")

    # ── Validierung: alle Datenpunkte inkl. Ablehnungsgründen ────────────────
    # Gezählt wird NACH der Deduplizierung (§4) — genau so, wie analysiere_markt
    # rechnet. Eine Rohzählung würde dieselbe Anzeige mehrfach mitzählen und den
    # Bericht schönen.
    alle = _alle_beobachtungen(ergebnisse, ziel)
    uniq: list = []
    gesehen: set[str] = set()
    for b in alle:
        if b.listing_key in gesehen:
            continue
        gesehen.add(b.listing_key)
        uniq.append(b)
    stufen_zaehler = {"sehr_aehnlich": 0, "aehnlich": 0, "bedingt": 0, "ungeeignet": 0}
    ablehnungen: dict[str, int] = {}
    for b in uniq:
        stufen_zaehler[b.vergleichbarkeit] = stufen_zaehler.get(b.vergleichbarkeit, 0) + 1
        if b.vergleichbarkeit == "ungeeignet":
            # Zwei Arten von Ablehnung unterscheiden: ein HARTES Ausschlusskriterium
            # (acceptance_reason beginnt mit "verworfen:") gegenüber einer bloß
            # KUMULIERTEN Abwertung (mehrere weiche Unschärfen summieren sich bis
            # zur Unbrauchbarkeit). Sonst tauchen positive Teilbefunde wie
            # "Kraftstoff bestätigt" fälschlich als Ablehnungsgrund auf.
            if b.acceptance_reason.startswith("verworfen:"):
                grund = b.acceptance_reason[len("verworfen:"):].strip().split(" (")[0]
            else:
                grund = "kumulierte Unschärfe (" + ", ".join(
                    g.split(" (")[0] for g in b.gruende if "nicht bestätigt" in g
                    or "unbekannt" in g or "abweichend" in g or "andere" in g) + ")"
            ablehnungen[grund] = ablehnungen.get(grund, 0) + 1
    print(f"\nExtrahierte Datenpunkte roh    : {len(alle)}")
    print(f"davon eindeutig (listing_key)  : {len(uniq)}")
    print(f"  sehr ähnlich : {stufen_zaehler['sehr_aehnlich']}")
    print(f"  ähnlich      : {stufen_zaehler['aehnlich']}")
    print(f"  bedingt      : {stufen_zaehler['bedingt']}")
    print(f"  verworfen    : {stufen_zaehler['ungeeignet']}")
    if ablehnungen:
        print("  Ablehnungsgründe:")
        for grund, n in sorted(ablehnungen.items(), key=lambda x: -x[1]):
            print(f"    {n:>3}x  {grund}")

    # ── Trichter: von den validierten Punkten zum Median ─────────────────────
    nutzbar = [b for b in uniq if _zaehlt_als_fahrzeug(b)]
    sehr = [b for b in nutzbar if b.vergleichbarkeit == "sehr_aehnlich"]
    aehn = [b for b in nutzbar if b.vergleichbarkeit == "aehnlich"]
    bedingt = [b for b in nutzbar if b.vergleichbarkeit == "bedingt"]
    kand = sehr + aehn
    if len(kand) < 3:
        kand = kand + bedingt
    mit_attr = [b for b in kand if b.baujahr is not None or b.kilometerstand is not None]
    nach_attr = mit_attr if len(mit_attr) >= 5 else kand
    nach_cap = _cap_pro_url(nach_attr)
    nach_trim, entfernt = _trim_ausreisser(nach_cap)
    print("\nTrichter bis zum Median:")
    print(f"  eindeutig gesamt                 : {len(uniq)}")
    print(f"  als Fahrzeug zählend (§3)        : {len(nutzbar)}")
    print(f"  Kandidaten (sehr ähnlich+ähnlich): {len(kand)}")
    print(f"  nach Attribut-Filter             : {len(nach_attr)}")
    print(f"  nach Deckelung pro Seite         : {len(nach_cap)}")
    print(f"  nach Ausreißer-Trim              : {len(nach_trim)} (entfernt: {len(entfernt)})")
    if len(nach_cap) < len(nach_attr):
        gedeckelt: dict[str, int] = {}
        for b in nach_attr:
            gedeckelt[b.quelle_url or ""] = gedeckelt.get(b.quelle_url or "", 0) + 1
        print("  durch die Deckelung verlorene Seiten:")
        for u, c in sorted(gedeckelt.items(), key=lambda x: -x[1])[:5]:
            print(f"    {c:>3} Punkte  {u[:90]}")

    # ── Ergebnis ─────────────────────────────────────────────────────────────
    print(f"\nValidiert/median-tragend       : {ma.verwendet}")
    print(f"Verwendete Domains             : {ma.quellen_domains}")
    print(f"Hintergrund-Domains (Kategorie): {ma.hintergrund_domains}")
    print(f"Datenqualität                  : {ma.datenqualitaet}")
    print(f"Marktabdeckung                 : {ma.marktabdeckung} ({ma.anzahl_domains} Plattform/en)")
    print(f"Median                         : {ma.median_eur} €")
    print(f"Marktspanne                    : {ma.spanne_min_eur}–{ma.spanne_max_eur} €")
    print(f"research_status                : {diag['research_status']} "
          f"(grund: {diag.get('research_failure_grund')})")
    print(f"Dauer                          : {round((time.perf_counter() - t0) * 1000)} ms")

    if ma.beobachtungen:
        print(f"\n§12 — ALLE {len(ma.beobachtungen)} median-tragenden MarketObservations einzeln:")
        for i, b in enumerate(ma.beobachtungen, 1):
            print(f"\n  #{i}")
            _zeige_beobachtung(b)

    # ── Rohdaten-Mitschnitt schreiben (optional) ─────────────────────────────
    if recorder:
        # Query-Stufe je Tavily-Aufruf: das Stufen-Log enthält nach dem "initial"-
        # Eintrag genau einen Eintrag je tatsächlich ausgeführtem Aufruf, in
        # derselben Reihenfolge wie der Mitschnitt.
        stufen = [s for s in diag["stufen"] if s.get("stufe") != "initial"]
        for i, (query, results) in enumerate(mitschnitt):
            s = stufen[i] if i < len(stufen) else {}
            label = f"{s.get('stufe', i + 1)}:{s.get('label', 'unbekannt')}"
            recorder.merke_suche(query=query, stufe=label, results=results)

        verwendet_keys = {b.listing_key for b in ma.beobachtungen}
        kontext_keys = {b.listing_key for b in ma.kontext_beobachtungen}
        schon_gezaehlt: set[str] = set()
        for k in karten_mit_text(ergebnisse, ziel):
            b = k["beobachtung"]
            recorder.merke_karte(
                source_result_url=k["url"], card_index=k["card_index"],
                card_text=k["card_text"], beobachtung=b,
                acceptance_status=_acceptance_status(b, verwendet_keys, kontext_keys,
                                                     schon_gezaehlt),
                card_hash=k["card_hash"])

        recorder.merke_zusammenfassung(
            research_status=diag["research_status"],
            research_failure_grund=diag.get("research_failure_grund"),
            datenqualitaet=ma.datenqualitaet, marktabdeckung=ma.marktabdeckung,
            verwendet=ma.verwendet, median_eur=ma.median_eur,
            spanne_min_eur=ma.spanne_min_eur, spanne_max_eur=ma.spanne_max_eur,
            quellen_domains=list(ma.quellen_domains),
            hintergrund_domains=list(ma.hintergrund_domains),
            fallback_bedingt=ma.fallback_bedingt,
            bester_stand_stufe=diag.get("bester_stand_stufe"),
            # §7: Verbrauch der Extract-Nachladung (Calls, URLs, Cache, Fehler).
            extract=diag.get("extract"),
            such_stufen=len([s for s in diag["stufen"] if s.get("stufe") != "initial"]),
            dauer_ms=diag.get("dauer_ms"),
            zielprofil={
                "generation_tokens": sorted(ziel["generation_tokens"]),
                "fremd_generationen": sorted(ziel["fremd_generationen"]),
                "ziel_motor_tokens": sorted(ziel["ziel_motor_tokens"]),
                "fremd_motor_tokens": sorted(ziel["fremd_motor_tokens"]),
                "leistung_ps": ziel["leistung_ps"], "karosserie": ziel["karosserie"],
                "kraftstoff": ziel["kraftstoff"], "facelift_jahr": ziel["facelift_jahr"],
            })
        pfad = recorder.schreibe()
        print(f"\nRohdaten gespeichert: {pfad}")
        print(f"  {len(recorder.suchergebnisse)} Suchergebnisse, {len(recorder.karten)} Karten")
        print(f"  Kartensegmentierung strukturell: {recorder.als_dict()['segmentierung']['strukturell']}")

    return {
        "fall": fall, "lauf": lauf_nr, "status": diag["research_status"],
        "quali": ma.datenqualitaet, "abdeckung": ma.marktabdeckung,
        "verwendet": ma.verwendet, "eindeutig": len(uniq), "median": ma.median_eur,
        "spanne": (ma.spanne_min_eur, ma.spanne_max_eur), "domains": ma.quellen_domains,
        "dauer_ms": round((time.perf_counter() - t0) * 1000),
    }


async def main() -> None:
    if not TAVILY_API_KEY:
        print("TAVILY_API_KEY nicht gesetzt — Live-Diagnose nicht möglich.")
        return
    argumente = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    speichern = "--speichern" in flags
    # Ein einzelner Lauf je Fall reicht, wenn es um die manuelle Kartenprüfung geht
    # (und schont das Tavily-Kontingent).
    laeufe = 1 if ("--einmal" in flags or speichern) else 3
    gewaehlt = argumente[0] if argumente else None
    faelle = [gewaehlt] if gewaehlt in FAELLE else list(FAELLE)

    alle: list[dict] = []
    for fall in faelle:
        for i in range(1, laeufe + 1):
            alle.append(await einzelner_lauf(fall, i, speichern=speichern))

    _abschnitt("ZUSAMMENFASSUNG — alle Läufe")
    for e in alle:
        print(f"  {e['fall']:<9} Lauf {e['lauf']}: status={e['status']:<17} "
              f"quali={e['quali']:<8} abdeckung={e['abdeckung']:<14} "
              f"verwendet={e['verwendet']:<3} median={e['median']} "
              f"spanne={e['spanne']} domains={e['domains']} ({e['dauer_ms']} ms)")
    for fall in faelle:
        laeufe = [e for e in alle if e["fall"] == fall]
        ok = sum(1 for e in laeufe if e["status"] in ("completed_high", "completed_medium"))
        print(f"\n  {fall}: {ok}/{len(laeufe)} Läufe mit auslieferbarem Ergebnis.")


if __name__ == "__main__":
    asyncio.run(main())
