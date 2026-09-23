"""
VerkaufsCheck RC1 — Produktumbau, Marketplace-Sperre und CarAPI-Adapter.

Deterministisch: KEIN echter Providercall. Gemini wird gestubbt, Tavily läuft
gar nicht erst an (keine freigegebene Marktquelle), CarAPI bekommt einen
gefälschten HTTP-Client.

Gedeckte Punkte (Auftrag §22 A-S):
  A strukturierte Preisvorstellung bleibt kanonisch
  B "scheckheftgepflegt" wird nicht "lückenlos"
  C "unfallfrei" bleibt eine Angabe
  D DSG bleibt DSG
  E CarAPI 200 wird korrekt normalisiert
  F CarAPI 404 -> sauberer Fallback
  G CarAPI Timeout -> VerkaufsCheck läuft weiter
  H Provider deaktiviert -> VerkaufsCheck läuft
  I Basismodell wird nicht als exakte Variante ausgegeben
  J kein erfundenes Preisband aus einem Einzelwert
  K Time-to-Sell ist Marktdauer, keine Verkaufszusage
  L keine gesperrten Marktplatzquellen über andere Suchpfade
  M neue optionale Felder wirken
  N drei Inseratstitel ohne erfundene Fakten
  O Foto-Plan vorhanden
  P Verhandlungsteil vorhanden
  Q Dokumente und Übergabe vorhanden
  R "Was jetzt?" ohne doppelte Nummerierung
  S keine gehäuften Gedankenstriche in erzeugten Texten

    python test_verkaufscheck_rc1.py
"""
import asyncio
import datetime as dt
import json
import re
import sys

sys.path.insert(0, ".")

from app import config  # noqa: E402
from app import carapi_provider as ca  # noqa: E402
import app.verkaufscheck as vc  # noqa: E402
import app.web_search as ws  # noqa: E402
from app.claim_sicherheit import entschaerfe_verstaerkungen  # noqa: E402
from app.inserat import finde_widersprueche, pruefe_fakten  # noqa: E402
from app.models import VerkaufsCheckRequest  # noqa: E402
from app.verkaufsplan import (  # noqa: E402
    KEINE_ORIENTIERUNG, baue_verkaufsplan, getriebe_bezeichnung, maengel_klassifiziert,
    entferne_provider_werte, runde_orientierung,
)

FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        FEHLER.append(name)


# ── Testfahrzeug: der echte RC1-Fall ─────────────────────────────────────────

def golf(**extra) -> VerkaufsCheckRequest:
    daten = dict(
        marke="VW", modell="Golf GTI", baujahr=2018, kilometerstand=92_000,
        motor="2.0 TSI, 230 PS", kraftstoff="Benzin", getriebe="DSG",
        ausstattung=["Navigation", "Sitzheizung", "LED-Scheinwerfer", "Tempomat"],
        beschreibung="Gut", preis_vorstellung=19_500, unfallfrei="ja", vorbesitzer=2,
        tuev_bis="08/2027", scheckheftgepflegt=True, farbe="Schwarz",
        optische_maengel=["Leichte Steinschläge an der Front"],
        technische_maengel=["Keine bekannten technischen Mängel"],
        verkaufsziel="ausgewogen",
    )
    daten.update(extra)
    return VerkaufsCheckRequest(**daten)


_LLM_ANTWORT = {
    "bericht": ("## Fahrzeug erkannt\nVW Golf VII GTI.\n\n## (a) Marktvergleich\n"
                "Das Fahrzeug hat ein lückenloses Scheckheft und ist nachweislich unfallfrei. "
                "Technisch einwandfrei, die bestehende Unfallfreiheit ist ein Plus.\n"),
    "preis_evidence_ids": [], "strategie_evidence_ids": [], "argument_evidence_ids": [],
}


async def _stub_gemini(system, user):
    return dict(_LLM_ANTWORT)


def lauf(req: VerkaufsCheckRequest) -> dict:
    """Kompletter VerkaufsCheck ohne echten Providercall."""
    orig = vc.call_gemini_json
    vc.call_gemini_json = _stub_gemini
    try:
        return asyncio.run(vc.run_verkaufscheck(req))
    finally:
        vc.call_gemini_json = orig


# ── Gefälschter CarAPI-HTTP-Client ───────────────────────────────────────────

class _Antwort:
    def __init__(self, status_code: int, daten):
        self.status_code = status_code
        self._daten = daten

    def json(self):
        if isinstance(self._daten, Exception):
            raise ValueError("kein JSON")
        return self._daten


class _FakeClient:
    aufrufe: list[dict] = []

    def __init__(self, verhalten):
        self._verhalten = verhalten

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        _FakeClient.aufrufe.append({"url": url, "params": dict(params or {})})
        v = self._verhalten(url)
        if isinstance(v, Exception):
            raise v
        return v


def mit_carapi(verhalten, *, erlaubt=True):
    """Kontext: CarAPI aktiv mit gefälschtem HTTP-Client."""
    class _Ctx:
        def __enter__(self):
            self._alt = (config.CARAPI_ERLAUBT, config.CARAPI_API_KEY, ca.httpx)
            config.CARAPI_ERLAUBT = erlaubt
            config.CARAPI_API_KEY = "test-token-nicht-echt"
            ca._cache.clear()
            _FakeClient.aufrufe = []

            class _HttpxStub:
                Timeout = ca.httpx.Timeout
                TimeoutException = ca.httpx.TimeoutException
                HTTPError = ca.httpx.HTTPError

                @staticmethod
                def AsyncClient(*a, **kw):
                    return _FakeClient(verhalten)
            ca.httpx = _HttpxStub
            return self

        def __exit__(self, *a):
            config.CARAPI_ERLAUBT, config.CARAPI_API_KEY, ca.httpx = self._alt
            ca._cache.clear()
            return False
    return _Ctx()


def _valuation_ok(url):
    if "vehicle-valuation" in url:
        return _Antwort(200, {"make": "volkswagen", "model": "golf", "year": 2018,
                              "valuationPrice": 19_347, "currency": "EUR", "country": "DE",
                              "fuel": "petrol", "kw": 169, "mileage": 92000})
    return _Antwort(200, {"make": "volkswagen", "model": "golf", "country": "DE",
                          "medianDaysToSell": 24, "p25Days": 10, "p75Days": 58})


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== L) Marketplace-Abrufsperre ===")

for url in ("https://www.mobile.de/fahrzeuge/details.html?id=123",
            "https://suchen.mobile.de/auto-inserat/x",
            "https://www.autoscout24.de/angebote/vw-golf-123",
            "https://www.autoscout24.at/angebote/x", "https://www.autouncle.de/de/d/1-golf",
            "https://www.kleinanzeigen.de/s-anzeige/golf/123", "https://12gebrauchtwagen.de/auto/vw/golf",
            "https://www.classic-trader.com/de/x", "https://heycar.de/x", "https://autohero.com/x"):
    check(f"L1 gesperrt: {url[:46]}", ws.ist_abruf_gesperrt(url))
for url in ("https://www.adac.de/golf", "https://automobile.de/x", "https://www.kba.de/x",
            "https://www.autodoc.de/bremsscheiben", "https://mobiledeals.example.com/x"):
    check(f"L2 nicht gesperrt: {url[:46]}", not ws.ist_abruf_gesperrt(url))

check("L3 Ergebnisfilter entfernt Marktplatztreffer",
      [r["url"] for r in ws.ohne_gesperrte_quellen([
          {"url": "https://www.adac.de/a"}, {"url": "https://www.mobile.de/b"},
          {"url": "https://www.autoscout24.de/c"}])] == ["https://www.adac.de/a"])


async def _include_nur_marktplaetze():
    alt = ws.TAVILY_API_KEY
    ws.TAVILY_API_KEY = "test-key"
    try:
        return await ws._tavily_search_intern("golf gebraucht", include_domains=ws.MARKTPLATZ_DOMAINS)
    finally:
        ws.TAVILY_API_KEY = alt


_res, _fehler = asyncio.run(_include_nur_marktplaetze())
check("L4 Positivliste nur mit Marktplätzen -> kein Call, kein Fehler", _res == [] and _fehler is False)
check("L5 Tavily-Ausschlussliste hat gültige TLDs",
      all("." in d for d in ws.ABRUF_GESPERRT_TAVILY) and "mobile.de" in ws.ABRUF_GESPERRT_TAVILY)
check("L6 Verkaufscheck startet ohne freigegebene Quelle keine Marktrecherche",
      vc._web_marktrecherche_moeglich(golf()) is False)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== B/C) Claim-Sicherheit ===")

_res_std = lauf(golf())
_bericht = _res_std["bericht"]
check("B1 kein 'lückenloses Scheckheft' im Bericht", "lückenlos" not in _bericht.lower())
check("B2 Scheckheft-Angabe bleibt erhalten", "scheckheft" in _bericht.lower())
check("C1 keine nachgewiesene Unfallfreiheit", not re.search(
    r"nachweislich\s+unfallfrei|bestehende\s+Unfallfreiheit", _bericht, re.IGNORECASE))
check("C2 Unfallfreiheit als Angabe gekennzeichnet",
      "laut deiner angabe" in _bericht.lower() or "angegebene unfallfreiheit" in _bericht.lower())
check("C3 kein 'technisch einwandfrei'", "technisch einwandfrei" not in _bericht.lower())

_titel, _beschr, _entfernt = pruefe_fakten(
    "VW Golf GTI | lückenloses Scheckheft",
    "Nachweislich unfallfreies Fahrzeug mit vollständiger Servicehistorie.", golf())
check("B3 Inseratstext ohne 'lückenlos'/'vollständige Servicehistorie'",
      "lückenlos" not in (_titel + _beschr).lower()
      and "vollständige servicehistorie" not in (_titel + _beschr).lower())
check("C4 Inserat behauptet keine nachgewiesene Unfallfreiheit",
      "nachweislich" not in (_titel + _beschr).lower())

_plan_std = _res_std["verkaufsplan"]
_werttreiber_text = json.dumps(_plan_std["werttreiber"], ensure_ascii=False).lower()
check("B4 Werttreiber nennt Scheckheft als Angabe",
      "laut deiner angabe" in _werttreiber_text and "lückenlos" not in _werttreiber_text)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== D) Getriebe ===")

check("D1 DSG bleibt DSG", getriebe_bezeichnung(golf()) == "DSG")
check("D2 DSG aus dem Motortext erkannt",
      getriebe_bezeichnung(golf(getriebe="Automatik", motor="2.0 TSI DSG")) == "DSG")
check("D3 Schaltgetriebe bleibt Schaltgetriebe",
      getriebe_bezeichnung(golf(getriebe="Schaltgetriebe", motor="2.0 TSI")) == "Schaltgetriebe")
check("D4 DSG steht in allen drei Titeln",
      all("DSG" in t["text"] for t in _plan_std["inserat"]["titel"]))
check("D5 DSG steht im Fahrzeugblock",
      any(z["label"] == "Getriebe" and z["wert"] == "DSG" for z in _plan_std["fahrzeug"]["zeilen"]))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A/M) Eingaben bleiben kanonisch ===")

_widerspruch = finde_widersprueche(golf(
    preis_vorstellung=21_000, kilometerstand=92_000,
    inserat_text="Golf GTI, 92.000 km, Preisvorstellung 19.500 €."))
check("A1 abweichende Preisangabe im Text wird gemeldet",
      any("preisvorstellung" in w.lower() for w in _widerspruch))
check("A2 übereinstimmender Kilometerstand erzeugt KEINE Warnung",
      not any("kilometerstand" in w.lower() for w in _widerspruch))
_res_preis = lauf(golf(preis_vorstellung=21_000,
                       inserat_text="Golf GTI, Preisvorstellung 19.500 €."))
check("A3 strukturierte Preisvorstellung bleibt maßgeblich",
      _res_preis["verkaufsplan"]["verhandlung"]["preise"]["preisvorstellung_eur"] == 21_000)

_res_m = lauf(golf(schluessel_anzahl=2, zweiter_radsatz=True, reifen_zustand="gut",
                   wartungsnachweise="vollstaendig", letzter_service_datum="03/2026",
                   letzter_service_km=88_000, preis_untergrenze=18_000, verkaufsziel="schnell",
                   import_status="reimport", tuning="Tieferlegungsfedern"))
_plan_m = _res_m["verkaufsplan"]
_treiber_m = json.dumps(_plan_m["werttreiber"], ensure_ascii=False)
_minderer_m = json.dumps(_plan_m["wertminderer"], ensure_ascii=False)
check("M1 zweiter Radsatz wird als Werttreiber genutzt", "Radsatz" in _treiber_m)
check("M2 Schlüsselanzahl wird genutzt", "2 Schlüssel" in _treiber_m)
check("M3 Reimport erscheint bei den Wertminderern", "eimport" in _minderer_m)
check("M4 Tuning erscheint bei den Wertminderern", "Tuning" in _minderer_m)
check("M5 Untergrenze ergibt einen Verhandlungsspielraum",
      _plan_m["verhandlung"]["preise"]["spielraum_eur"] == 1_500)
check("M6 Verkaufsziel 'schnell' wirkt", _plan_m["strategie"]["ziel"] == "schnell")
check("M7 Dokumentenliste kennt Umbauten-Nachweise",
      any("Umbau" in d["dokument"] for d in _plan_m["dokumente"]))
check("M8 'Keine bekannten technischen Mängel' zählt nicht als Mangel",
      maengel_klassifiziert(golf())["technisch"] == []
      and maengel_klassifiziert(golf())["verneint"])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E-K) CarAPI-Adapter ===")

with mit_carapi(_valuation_ok):
    _res_api = lauf(golf())
_markt = _res_api["verkaufsplan"]["markt"]
_o = _markt["orientierung"]
check("E1 Valuation normalisiert (Status ok)", _o["status"] == "ok")
check("E2 Wert wird auf eine Orientierungsgröße gerundet",
      _o["wert_eur"] == 19_500 and runde_orientierung(19_347) == 19_500)
check("E3 genau zwei CarAPI-Aufrufe je Check", len(_FakeClient.aufrufe) == 2)
check("E4 Token steht nicht im normalisierten Ergebnis",
      "test-token-nicht-echt" not in json.dumps(_res_api["verkaufsplan"], ensure_ascii=False))
check("E5 country=DE wird gesendet",
      all(a["params"].get("country") == "DE" for a in _FakeClient.aufrufe))
check("E6 Leistung in kW wird mitgeschickt",
      any(a["params"].get("kw") == 169 for a in _FakeClient.aufrufe))

check("I1 Basismodell wird transparent benannt", "Basismodell" in _o["spezifitaet"])
check("I2 Ausstattungslinie wird NICHT als bewertet behauptet",
      "GTI" in _o["spezifitaet"] and "nicht als eigene Stufe garantiert" in _o["spezifitaet"])
check("I3 erhöhte Unsicherheit wird ausgewiesen", _o["unsicherheit"] == "erhoeht")
check("I4 Orientierung ist als solche gekennzeichnet",
      "Orientierung" in _o["hinweis"] and "kein Gutachten" in _o["hinweis"])

# Nur die PREIS-Orientierung prüfen: die Marktdauer führt legitim einen Median
# der Inseratstage (das ist eine Zeit-, keine Preisstatistik).
_orient_text = json.dumps(_o, ensure_ascii=False).lower()
check("J1 kein erfundener Median/Quartil in der Preisorientierung",
      not any(w in _orient_text for w in ("median", "quartil", "stichprobe")))
check("J2 keine erfundene Preisspanne",
      "spanne" not in _orient_text and "–" not in _o["text"])
check("J3 Wert ist als 'ca.' ausgewiesen", _o["text"].startswith("Externe Marktindikation: ca."))
check("J4 Vergleich mit der Preisvorstellung ist sachlich",
      _o["vergleich"]["differenz_eur"] == 0 and "im Bereich" in _o["vergleich"]["einordnung"])
check("J5 keine Preisstrategie-Zahlen außerhalb des Marktblocks",
      _res_api["schnellverkaufs_preis"] is None and _res_api["empfohlener_preis"] is None)

_d = _markt["dauer"]
check("K1 Time-to-Sell normalisiert", _d["status"] == "ok" and _d["median_tage"] == 24
      and _d["p25_tage"] == 10 and _d["p75_tage"] == 58)
check("K2 als Inserats-/Marktdauer formuliert",
      "online" in _d["text"] and "verschwindet" in _d["text"])
check("K3 keine Verkaufszusage", not re.search(r"wird\s+in\s+\d+\s+tagen\s+verkauft", _d["text"], re.I)
      and "keine Zusage" in _d["hinweis"])
check("K4 saubere Verteilung gilt als normal aufgeloest", _d["aufloesung"] == "normal")

# Realtest 2026-09-22 (VW Golf, DE): p25 7, Median 81, p75 81 — zwei Werte fallen
# zusammen. Das darf nicht wie eine feine Statistik aussehen.
with mit_carapi(lambda url: _Antwort(200, {"make": "vw", "model": "golf", "country": "DE",
                                           "medianDaysToSell": 81, "p25Days": 7, "p75Days": 81})
                if "time-to-sell" in url else _Antwort(404, {"error": "keine Daten"})):
    _res_grob = lauf(golf())
_d_grob = _res_grob["verkaufsplan"]["markt"]["dauer"]
check("K5 entartete Verteilung wird als grob gekennzeichnet",
      _d_grob["aufloesung"] == "grob" and "grobe" in _d_grob["hinweis"])
check("K6 die Zahlen bleiben trotzdem stehen",
      _d_grob["median_tage"] == 81 and _d_grob["p25_tage"] == 7)

with mit_carapi(lambda url: _Antwort(404, {"error": "Insufficient market data"})):
    _res_404 = lauf(golf())
_o404 = _res_404["verkaufsplan"]["markt"]["orientierung"]
check("F1 404 -> sauberer Fallback mit dem echten Fallback-Text",
      _o404["status"] == "nicht_verfuegbar" and _o404["text"] == KEINE_ORIENTIERUNG)
check("F1b 404 -> Grund wird benannt, keine Preiszahl",
      _o404.get("grund") is not None and "wert_eur" not in _o404)
check("F2 404 -> Rest des Plans vollständig",
      bool(_res_404["verkaufsplan"]["inserat"]["titel"]) and bool(_res_404["bericht"]))

with mit_carapi(lambda url: ca.httpx.TimeoutException("zu langsam")):
    _res_timeout = lauf(golf())
check("G1 Timeout -> VerkaufsCheck liefert weiterhin ein Ergebnis",
      _res_timeout["verkaufsplan"]["markt"]["orientierung"]["status"] == "nicht_verfuegbar"
      and len(_res_timeout["verkaufsplan"]["naechste_schritte"]) >= 3)

with mit_carapi(_valuation_ok, erlaubt=False):
    _res_aus = lauf(golf())
check("H1 Provider deaktiviert -> kein einziger Aufruf", _FakeClient.aufrufe == [])
check("H2 Provider deaktiviert -> Check vollständig",
      _res_aus["verkaufsplan"]["markt"]["orientierung"]["status"] == "nicht_verfuegbar"
      and bool(_res_aus["verkaufsplan"]["fotoplan"]["fotos"]))
check("H3 Production-Default ist AUS", ca.ist_aktiv() is False)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== Persistenz (CarAPI-Terms: max. 24 h) ===")

_gespeichert = entferne_provider_werte({"verkaufsplan": _res_api["verkaufsplan"]})
_gp = _gespeichert["verkaufsplan"]["markt"]
check("P1 Providerwerte werden vor dem Speichern entfernt",
      _gp["orientierung"]["status"] == "nicht_gespeichert" and _gp["dauer"] is None
      and "19.500" not in json.dumps(_gespeichert, ensure_ascii=False))
check("P2 übriger Verkaufsplan bleibt gespeichert erhalten",
      bool(_gespeichert["verkaufsplan"]["inserat"]["titel"])
      and bool(_gespeichert["verkaufsplan"]["dokumente"]))
check("P3 idempotent", entferne_provider_werte(_gespeichert) == _gespeichert)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== N-S) Produktstruktur ===")

_plan = _plan_std
_titel = _plan["inserat"]["titel"]
check("N1 drei Titelvarianten", len(_titel) == 3
      and {t["art"] for t in _titel} == {"sachlich", "verkaufsstark", "kompakt"})
_titel_text = " ".join(t["text"] for t in _titel).lower()
check("N2 keine erfundene Ausstattung im Titel",
      "leder" not in _titel_text and "panorama" not in _titel_text and "ahk" not in _titel_text)
check("N3 keine Superlative", not re.search(r"top!!|bestpreis|schnäppchen|traumauto", _titel_text))
_ohne_scheckheft = lauf(golf(scheckheftgepflegt=None, unfallfrei=None))["verkaufsplan"]
check("N4 ohne Angabe kein Scheckheft/unfallfrei im Titel",
      not any("scheckheft" in t["text"].lower() or "unfallfrei" in t["text"].lower()
              for t in _ohne_scheckheft["inserat"]["titel"]))
check("N5 Faktenblock und Kurzbeschreibung vorhanden",
      len(_plan["inserat"]["faktenblock"]) >= 6 and len(_plan["inserat"]["kurzbeschreibung"]) > 40)
check("N6 bekannte Mängel stehen ehrlich in der Beschreibung",
      "Steinschläge" in _plan["inserat"]["beschreibung"])

check("O1 Foto-Plan mit mindestens 14 Motiven", len(_plan["fotoplan"]["fotos"]) >= 14)
check("O2 Foto-Plan nummeriert fortlaufend",
      [f["nr"] for f in _plan["fotoplan"]["fotos"]] == list(range(1, len(_plan["fotoplan"]["fotos"]) + 1)))
check("O3 Mängel werden fotografiert",
      any("Mangel" in f["motiv"] for f in _plan["fotoplan"]["fotos"]))
check("O4 Hinweise zu Kennzeichen und Ehrlichkeit",
      any("Kennzeichen" in h for h in _plan["fotoplan"]["hinweise"]))

check("P4 Verhandlungsteil vorhanden",
      bool(_plan["verhandlung"]["argumente"]) and bool(_plan["verhandlung"]["fairness"]))
check("P5 typische Käuferargumente kommen aus den echten Mängeln",
      any("Steinschläge" in a["einwand"] for a in _plan["verhandlung"]["argumente"]))

check("Q1 Dokumentenliste vollständig",
      {"Zulassungsbescheinigung Teil I (Fahrzeugschein)",
       "Zulassungsbescheinigung Teil II (Fahrzeugbrief)"} <= {d["dokument"] for d in _plan["dokumente"]})
check("Q2 Übergabe-Checkliste vorhanden", len(_plan["uebergabe"]["punkte"]) >= 4)
check("Q3 Zahlungshinweis nennt den Zahlungseingang",
      any("Gutschrift" in p or "Zahlungseingang" in p for p in _plan["uebergabe"]["punkte"]))
check("Q4 keine vorgetäuschte Rechtsberatung", "Keine Rechtsberatung" in _plan["uebergabe"]["hinweis"])

_schritte = _plan["naechste_schritte"]
check("R1 3 bis 5 nächste Schritte", 3 <= len(_schritte) <= 5)
check("R2 keine eigene Nummerierung im Text",
      not any(re.match(r"^\s*\d+[.)]", s) for s in _schritte))
check("R3 keine doppelten Schritte", len(set(_schritte)) == len(_schritte))

_erzeugt = "\n".join([
    json.dumps(_plan["werttreiber"], ensure_ascii=False),
    json.dumps(_plan["wertminderer"], ensure_ascii=False),
    json.dumps(_plan["vorbereitung"], ensure_ascii=False),
    json.dumps(_plan["verhandlung"], ensure_ascii=False),
    json.dumps(_plan["uebergabe"], ensure_ascii=False),
    json.dumps(_plan["fotoplan"], ensure_ascii=False),
    _plan["inserat"]["beschreibung"], _plan["inserat"]["kurzbeschreibung"],
])
check("S1 keine langen Gedankenstriche in erzeugten Texten", "—" not in _erzeugt)
check("S2 auch die Marktbausteine ohne Gedankenstrich",
      "—" not in json.dumps(_res_api["verkaufsplan"]["markt"], ensure_ascii=False))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== Fahrzeugerkennung (Golf GTI) ===")

check("V1 Golf GTI wird belastbar erkannt", _res_std["baureihe_erkannt"] == "volkswagen-golf-vii")
check("V2 Variante wird ausgewiesen", _plan["fahrzeug"]["variante"] == "GTI")
check("V3 mehrdeutige Motorvariante wird offengelegt",
      any("nicht eindeutig" in h for h in _plan["fahrzeug"]["hinweise"]))
check("V4 Inseratsqualität trennt Vollständigkeit und Transparenz",
      _plan["inseratsqualitaet"]["vollstaendigkeit"]["gesamt"] == 11
      and "label" in _plan["inseratsqualitaet"]["transparenz"])

print()
print(f"{len(FEHLER)} FAIL" if FEHLER else "ALLE TESTS GRÜN")
for f in FEHLER:
    print("  -", f)
sys.exit(1 if FEHLER else 0)
