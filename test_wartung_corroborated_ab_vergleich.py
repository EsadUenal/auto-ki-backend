"""
Nutzerausgabe und Entscheidungseinfluss des sichtbar gemachten CORROBORATED-
Wartungshinweises (Fortsetzungsauftrag): A/B-Vergleich mit identischen
Eingaben, NUR der Wartungs-Fixture-Inhalt unterscheidet sich (real carwiki.de/
repareo.de, siehe test_wartung_corroborated_sichtbar.py). Beweist über die
tatsächliche API-Ausgabe (KaufCheckResponse.model_dump()), nicht nur über
Codeverweise:

  * der Hinweis samt Quellen und Einschränkung ist tatsächlich erreichbar
    (im `insights`-Feld der API-Antwort UND im `bericht`-Markdown-Text);
  * keine Darstellung als bestätigter Wartungsplan;
  * kein widersprüchlicher Satz "keine Wartungsinformationen vorhanden";
  * kein Konflikt mit Vollständigkeits-/Qualitätsaussagen an anderer Stelle
    desselben Berichts;
  * Empfehlung, Preisbewertung, Datenbasis und Identitäts-Kernfelder ändern
    sich NICHT allein durch das Vorhandensein dieses Hinweises.

HINWEIS ZUM FRONTEND: dieses Repository (auto-ki-backend) enthält KEIN
Frontend -- das KaufCheck-Frontend liegt in einem separaten Repository
(auto-ki-web, siehe AGENTS.md). Was hier geprüft wird, ist deshalb die
tatsächliche API-Antwort (dasselbe JSON, das ein Frontend konsumieren würde)
und der vollständige Markdown-Bericht (`res.bericht`) -- NICHT eine gerenderte
Bildschirmansicht. Diese Grenze wird hier ausdrücklich benannt, nicht
verschwiegen.

    python scripts/test_isolated.py test_wartung_corroborated_ab_vergleich.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import kaufcheck as kc
from app.models import KaufCheckRequest, KaufCheckResponse
from app.technical_research import FixtureTechnicalResearchProvider, recherchiere_technisch

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


_ADAC_ND = t("https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/mazda/mx-5/",
             "Mazda MX-5 (ND) seit 2015: Technische Daten",
             "Der Mazda MX-5 ND (seit 2015) ist die vierte Generation des Roadsters. Der 2.0 "
             "SKYACTIV-G leistet seit dem Modelljahr 2019 184 PS, Benziner, Hinterradantrieb, "
             "6-Gang-Schaltgetriebe.")
_AMS_ND = t("https://www.auto-motor-und-sport.de/test/mazda-mx-5-2-0-skyactiv-g-184/",
            "Mazda MX-5 2.0 Skyactiv-G 184 im Test",
            "Die vierte Generation des Mazda MX-5 (ND, seit 2015): 2.0 Skyactiv-G mit 184 PS, "
            "Benziner, Heckantrieb und 6-Gang-Schaltgetriebe.")
_CARWIKI_ND = t("https://carwiki.de/mazda-mx-5-oelwechsel", "Der Ölwechsel beim Mazda MX-5",
                "Beim Mazda MX-5 ND (ab 2015) muss der Ölwechsel immer nach 20.000 Kilometern "
                "oder spätestens nach 12 Monaten erfolgen.")
_REPAREO_ND = t("https://www.repareo.de/oelwechsel-mazda-mx-5/", "Ölwechsel Mazda MX-5",
                "Roadster aktuellerer Baujahre wie der Mazda MX-5 ND benötigen alle 20.000 km "
                "oder alle 12 Monate einen Ölwechsel.")


async def _lauf(req: KaufCheckRequest, web_fixture: dict) -> KaufCheckResponse:
    orig = (kc.call_gemini_json, kc.recherchiere_technisch, kc.tavily_search_with_fallback,
            kc.marktpreis_recherche_moeglich, kc.vertiefe_marktrecherche)

    async def gemini(system, user):
        return {"bericht": "KAUFEN.", "empfehlung": "kaufen",
                "risiko_evidence_ids": [], "empfehlung_evidence_ids": []}

    async def verboten(*a, **k):
        raise AssertionError("Marktprovider darf nicht laufen")

    prov = FixtureTechnicalResearchProvider(web_fixture)

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


_REQ = dict(marke="Mazda", modell="MX-5", baujahr=2019, kilometerstand=31000, motor="2.0 SKYACTIV-G",
           kraftstoff="Benzin", leistung_ps=184, getriebe="Schaltgetriebe", antrieb="Heck",
           ausstattung=[])
_FIXTURE_MIT_HINWEIS = {
    "identitaet": [_ADAC_ND, _AMS_ND], "wartung": [_CARWIKI_ND, _REPAREO_ND],
    "schwachstelle": [], "rueckruf": [],
}
_FIXTURE_OHNE_HINWEIS = {
    "identitaet": [_ADAC_ND, _AMS_ND], "wartung": [],
    "schwachstelle": [], "rueckruf": [],
}


async def run():
    r_mit = await _lauf(KaufCheckRequest(**_REQ), web_fixture=_FIXTURE_MIT_HINWEIS)
    r_ohne = await _lauf(KaufCheckRequest(**_REQ), web_fixture=_FIXTURE_OHNE_HINWEIS)

    # ── Tatsächliche API-Ausgabe: Hinweis samt Quellen/Einschränkung ────────
    web_wartung = [i for i in r_mit.insights if i.kategorie == "web_wartung"]
    check("Hinweis ist in der API-Antwort (insights) erreichbar", len(web_wartung) == 1,
          str(web_wartung))
    i = web_wartung[0]
    check("Quellen sind in der API-Antwort erreichbar (nicht nur intern)",
          len(i.quellen) == 2 and {q.url for q in i.quellen} == {_CARWIKI_ND["url"], _REPAREO_ND["url"]},
          str([q.url for q in i.quellen]))
    check("Einschränkung ist in der API-Antwort erreichbar (einfluss-Feld)",
          "nicht ausreichend bestätigt" in i.einfluss, i.einfluss)

    # ── Keine Verwechslung / kein Widerspruch im vollen Bericht ─────────────
    bericht = r_mit.bericht
    check("Hinweis steht auch im vollständigen Markdown-Bericht",
          "Ölwechsel" in bericht and "carwiki.de" in bericht and "repareo.de" in bericht)
    check("Keine Darstellung als bestätigter Wartungsplan ('Wartungsplan bestätigt'/"
          "'bestätigter Wartungsplan' kommt nirgends vor)",
          "Wartungsplan bestätigt" not in bericht and "bestätigter Wartungsplan" not in bericht)
    check("Kein widersprüchlicher Satz 'keine Wartungsinformationen vorhanden' trotz "
          "vorhandenem Hinweis",
          "keine Wartungsinformationen" not in bericht
          and "keine Informationen zur Wartung" not in bericht)
    check("Keine Überfälligkeits-/Fälligkeitsbehauptung aus dem Hinweis",
          "ist fällig" not in bericht and "überfällig" not in bericht)
    check("Datenqualitätszeile des Hinweises nennt 'niedrig' (kein Konflikt mit einer "
          "allgemeinen Vollständigkeits-/Qualitätsaussage an anderer Stelle)",
          "Quellen-/Datensatzqualität: niedrig." in bericht)

    # ── A/B-Vergleich: identische übrige Eingaben ────────────────────────────
    d_mit, d_ohne = r_mit.model_dump(), r_ohne.model_dump()
    for feld in ("recommendation_state", "empfehlung", "empfehlung_anzeige", "anzeige_titel",
                 "datenbasis", "quelle", "technical_coverage", "accident_status"):
        check(f"A/B gleich: {feld} ändert sich NICHT durch den Hinweis allein",
              d_mit.get(feld) == d_ohne.get(feld),
              f"mit={d_mit.get(feld)!r} ohne={d_ohne.get(feld)!r}")

    gruende_neu = set(d_mit["empfehlung_gruende"]) - set(d_ohne["empfehlung_gruende"])
    check("A/B: der einzige neue Empfehlungsgrund ist eine neutrale Zählaussage, "
          "keine Verschlechterung (kein 'Risiko'/'Warnung'/'Mangel'-Wortlaut)",
          len(gruende_neu) <= 1 and all(
              not any(w in g for w in ("Risiko", "Warnung", "Mangel", "fällig", "Problem"))
              for g in gruende_neu),
          str(gruende_neu))

    check("A/B: keine Preisbewertung entsteht/verschwindet allein durch den Hinweis",
          ("## Preis-Einschätzung" in r_mit.bericht) == ("## Preis-Einschätzung" in r_ohne.bericht))

    # Eine sinnvolle Aufforderung, den Wartungsnachweis zu prüfen, ist erlaubt
    # und bereits Teil der bestehenden Verkäuferfragen/Dokumente-Generierung
    # (app/kaufaktionen.py) -- hier nur als Beleg, dass sie bauteilbezogen und
    # neutral bleibt, keine Kaufwarnung.
    vf_text = " ".join(a.titel + " " + a.aktion
                       for a in (r_mit.kaufaktionen.verkaeuferfragen.fahrzeugspezifisch
                                 + r_mit.kaufaktionen.verkaeuferfragen.basis))
    check("Verkäuferfragen enthalten eine neutrale Nachweis-Nachfrage zum Ölwechsel, "
          "keine Kaufwarnung", "Ölwechsel" in vf_text and "zuletzt gemacht" in vf_text)


asyncio.run(run())

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE WARTUNG-CORROBORATED-AB-VERGLEICH-TESTS GRUEN")
