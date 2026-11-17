"""
Identitäts-Diagnose der Motorisierungsfelder (Hubraum, Motorkern, Kraftstoff, Antrieb, Elektrifizierung) im normalen
Produktions-Log (INFO, Präfix `identitaet_diagnose`, Laufkennung wie `sw_diagnose`).

Geprüft wird, dass je Feld nachvollziehbar wird: Quelle (URL ohne Query, Domain), Tier nach Abwertung, Zeitfenster-Urteil,
normalisierter Claim, Ausschlussgrund, gezählte Fundstellen/Quellen/unabhängige Domains und die Konsensentscheidung -- und dass die
Diagnose allein weder Ergebnisse noch Aufrufzahl ändert.

Quellen: [ECHT] = aufgezeichnete Texte, wortgleich aus test_n6_n17_motorkern_filter.py (toyota-media.de 01.12.2016 und Replay
29.10.2026 bzw. ADAC-/Toyota-Japan-Treffer der Fixtures). [SYNTH] = synthetische Gegenprobe für Filterlogik. Der Test sagt nichts
über den Lauf vom 16.11.2026.

    python scripts/test_isolated.py test_identitaet_diagnose.py
"""
import asyncio
import logging
import os
import re
import socket
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="identdiag_")
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

from app import database as _db  # noqa: E402
_db.ensure_tables()
import app.technical_research as tr  # noqa: E402
import app.web_search as ws  # noqa: E402
from app.models import KaufCheckRequest  # noqa: E402
from app.provider_control import provider_scope  # noqa: E402
from app.vehicle_identity import VehicleIdentity  # noqa: E402

FAILS: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def t(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── [ECHT] aufgezeichnete Quellen (wortgleich aus test_n6_n17_motorkern_filter.py) ─────────────────────────────────
ADAC_AUTOMATIK = t("https://www.adac.de/rund-ums-fahrzeug/autokatalog/marken-modelle/lexus/rc/xc1/288844/",
      "Lexus RC F Automatik (01/18 - 03/19): Technische Daten, Bilder, Preise | ADAC",
      "Marke Lexus Modell RC F Typ Automatik Baureihe RC (UXC1) (12/14 - 06/25) "
      "Herstellerinterne Baureihenbezeichnung UXC1 Modellstart 01/18 Modellende 03/19 "
      "Baureihenstart 12/14 Baureihenende 06/25 HSN/TSN Schluesselnummern 5013/ALQ "
      "Motorcode 2UR-GSE Leistung maximal in PS 464 Antriebsart Hinterrad "
      "Getriebeart Automatikgetriebe Kraftstoffart Super Hubraum 4969 ccm")
CARWIKI = t("https://carwiki.de/lexus-rc-hsn-tsn-schluesselnummer/",
           "Lexus RC HSN & TSN | Tabelle aller Schluesselnummern",
           "Uebersicht: Alle Schluesselnummern (HSN/TSN) der Lexus RC Baureihen. "
           "Hersteller: Lexus Fahrzeug: RC Baureihe: RC XC1 Karosserie: Coupe "
           "MODELL HSN SCHLUESSELNUMMER TSN SCHLUESSELNUMMER KRAFTSTOFF HUBRAUM LEISTUNG BAUJAHR "
           "Lexus RC F Carbon Automatik 5013 AJM Benzin 4969 cm3 477 PS 2014 bis 2025")
AMS = t("https://www.auto-motor-und-sport.de/marken-modelle/lexus/rc/",
       "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015)",
       "Lexus RC (1. Generation) (XC1) im Ueberblick (ab 2015). Leistung 181 PS bis "
       "477 PS. Hubraum 1998 cm3 bis 4969 cm3. LEXUS RC F TRACK EDITION IM SUPERTEST: "
       "Was kann der V8-Sauger auf der Rennstrecke? LEXUS LC 500 UND RC F: "
       "V8-Sportler auf Abschiedstour.")
TOYOTA_JAPAN = t(
    "https://global.toyota/en/detail/4101939",
    "Lexus Launches All-new 'RC F' High-performance Coupe in Japan | Toyota Motor Corporation Official Global Website",
    "Tokyo, October 23, 2014-Lexus' all-new high-performance coupe, the \"RC F\", "
    "went on sale today in Japan. Engine Transmission Driveline Price RC F Carbon "
    "Exterior package 2UR-GSE (V8 5.0-liter) 8-Speed SPDS Rear-wheel drive "
    "10,300,000 yen 9,530,000 yen.")
TOYOTA_MEDIA_DE = t(
    "https://www.toyota-media.de/blog/lexus-modelle/artikel/neues-adaptiv-variables-fahrwerk-fuer-lexus-gs-f-und-rc-f/text",
    "NEUES ADAPTIV VARIABLES FAHRWERK FÜR LEXUS F-MODELLE",
    "Köln, 1. Dezember 2016. Lexus hat die Fahrstabilität und den Fahrkomfort der "
    "beiden F-Modelle GS F (Kraftstoffverbrauch kombiniert 11,2/100km; CO2-"
    "Emissionen 260g/km) und RC F (Kraftstoffverbrauch kombiniert 10,8/100km; "
    "CO2-Emissionen 251g/km) mit der neuesten Generation des adaptiv variablen "
    "Fahrwerks AVS aufgewertet. Der Lexus GS F und der RC F werden beide von "
    "einem 5,0 Liter V8-Saugmotor angetrieben, der 351 kW / 477 PS leistet und "
    "an eine Achtstufen-Sport-Automatik gekoppelt ist. Neben dem neuen adaptiv "
    "variablen Fahrwerk bietet Lexus den GS F jetzt mit einer neuen grauen "
    "(Graphitgrau) und den RC F mit einer neuen schwarzen Lackierung "
    "(Graphitschwarz) an. Lexus: Die 1989 eingeführte Marke Lexus ist weltweit bekannt für "
    "Produktqualität, die Maßstäbe setzt. Lexus ist der erste und bis heute "
    "einzige Hersteller von Premium-Fahrzeugen mit einer umfassenden Palette "
    "von Hybridfahrzeugen. Diese umfasst den ersten Premium-Kompaktwagen mit "
    "Vollhybridantrieb Lexus CT, das Sportcoupé Lexus RC sowie die "
    "Sportlimousine Lexus IS mit unterschiedlichen Motorvarianten.")
ECHT = [CARWIKI, AMS, TOYOTA_MEDIA_DE, ADAC_AUTOMATIK, TOYOTA_JAPAN]


def synth(satz, host="www.auto-motor-und-sport.de", titel="Lexus RC F Test", pfad="/synthetisch/rcf"):
    return t(f"https://{host}{pfad}", titel, satz)


class Sammler(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.zeilen: list[str] = []

    def emit(self, record):
        self.zeilen.append(record.getMessage())


def mit_log(fn):
    h, lg = Sammler(), logging.getLogger("app.technical_research")
    alt = lg.level
    lg.setLevel(logging.INFO)
    lg.addHandler(h)
    try:
        erg = fn()
    finally:
        lg.removeHandler(h)
        lg.setLevel(alt)
    return erg, [z for z in h.zeilen if z.startswith("identitaet_diagnose")]


def feld_wert(zeile, name):
    m = re.search(rf"\b{re.escape(name)}=(\S+)", zeile)
    return m.group(1) if m else None


def zeilen_von(zeilen, feld, stufe):
    return [z for z in zeilen if f" feld={feld} " in z and f" stufe={stufe} " in z]


ZIEL = tr._ziel("Lexus", "RC F", 2017, "5.0 V8, Heckantrieb", None)
ZIEL.update(leistung_ps=477)


def auswerten(quellen, runde=1):
    def lauf():
        w, _ = tr.werte_identitaet_aus(quellen, ZIEL)
        tr._log_identitaet_felder("TESTLAUF", runde, w)
        return w
    return mit_log(lauf)


print("== A: aufgezeichnete Quellen -- passender, zeitlich unpassender und zu schwacher Beleg ==")
w, z = auswerten(ECHT)
check("A0 eine Laufkennung und die Runde in jeder Zeile", z and all(" lauf=TESTLAUF " in x and " runde=1 " in x for x in z), str(len(z)))
hub = zeilen_von(z, "displacement", "quelle")
hub_s = zeilen_von(z, "displacement", "entscheidung")[0]
tm = next(x for x in hub if "toyota-media.de" in x)
check("A1 [ECHT] Hubraum, passender Beleg: Toyota-Media (Hersteller-Presse 2016), Tier 1, claim 5.0, gezählt",
      feld_wert(tm, "tier") == "1" and feld_wert(tm, "claim") == "5.0" and feld_wert(tm, "status") == "gezaehlt"
      and feld_wert(tm, "zeitfenster") == "kein_fenster", tm)
adac = next(x for x in hub if "adac.de" in x)
check("A2 [ECHT] Hubraum, zeitlich unpassend: ADAC-Datenblatt 01/18-03/19 -> Zeitfenster 'ausserhalb', claim 5.0 verworfen mit Grund",
      feld_wert(adac, "zeitfenster") == "ausserhalb" and feld_wert(adac, "status") == "verworfen:baujahr_ausserhalb_datenblatt"
      and feld_wert(adac, "claim") == "5.0", adac)
jp = next(x for x in hub if "global.toyota" in x)
check("A3 [ECHT] Hubraum, Fremdmarkt: Japan-Meldung Tier-1-Domain, aber auf Tier 3 gedeckelt (tier=3 tier_roh=1 fremdmarkt_gedeckelt=ja)",
      feld_wert(jp, "tier") == "3" and feld_wert(jp, "tier_roh") == "1" and feld_wert(jp, "fremdmarkt_gedeckelt") == "ja", jp)
check("A4 [ECHT] Hubraum-Entscheidung: 4 Fundstellen/4 Quellen, 3 gültige Domains (davon 1 mit Tier<=2, 1 Tier 1), 1 verworfen, "
      "Konsens bestätigt, Ergebnis 5.0",
      feld_wert(hub_s, "fundstellen") == "4" and feld_wert(hub_s, "quellen") == "4" and feld_wert(hub_s, "domains_gueltig") == "3"
      and feld_wert(hub_s, "domains_tier<=2") == "1" and feld_wert(hub_s, "domains_tier1") == "1"
      and "baujahr_ausserhalb_datenblatt=1" in hub_s and feld_wert(hub_s, "konsens") == "bestaetigt"
      and (feld_wert(hub_s, "ergebnis") or "").startswith("5.0("), hub_s)
kuebel = zeilen_von(z, "fuel", "quelle")
kraft = zeilen_von(z, "fuel", "entscheidung")[0]
cw = next(x for x in kuebel if "carwiki.de" in x)
check("A5 [ECHT] Kraftstoff, zu schwacher Beleg: nur CarWiki (Tier 3) 'benzin'; ADAC 'Kraftstoffart Super' zeitlich verworfen",
      feld_wert(cw, "tier") == "3" and feld_wert(cw, "claim") == "benzin" and feld_wert(cw, "status") == "gezaehlt"
      and any("adac.de" in x and "verworfen:baujahr_ausserhalb_datenblatt" in x for x in kuebel), "\n".join(kuebel))
check("A6 [ECHT] Kraftstoff-Entscheidung: nicht bestätigt, Grund 'keine_starke_quelle_tier<=2', 0 Domains mit Tier<=2, Ergebnis offen",
      feld_wert(kraft, "konsens") == "nicht_bestaetigt" and feld_wert(kraft, "grund") == "keine_starke_quelle_tier<=2"
      and feld_wert(kraft, "domains_tier<=2") == "0" and feld_wert(kraft, "ergebnis") == "offen"
      and "benzin:gewicht=1" in kraft, kraft)
ant = zeilen_von(z, "drivetrain", "entscheidung")[0]
check("A7 [ECHT] Antrieb (Heck): nur die auf Tier 3 gedeckelte Japan-Fundstelle zählt, ADAC verworfen -> nicht bestätigt",
      feld_wert(ant, "konsens") == "nicht_bestaetigt" and feld_wert(ant, "grund") == "keine_starke_quelle_tier<=2"
      and feld_wert(ant, "domains_gueltig") == "1" and "baujahr_ausserhalb_datenblatt=1" in ant, ant)
pt = zeilen_von(z, "powertrain", "entscheidung")[0]
check("A8 [ECHT] Elektrifizierung: kein Claim -> 'kein_gueltiger_claim', Ergebnis offen; nichts aus fehlenden Hybridangaben abgeleitet",
      feld_wert(pt, "konsens") == "nicht_bestaetigt" and feld_wert(pt, "grund") == "kein_gueltiger_claim"
      and feld_wert(pt, "fundstellen") == "0" and feld_wert(pt, "ergebnis") == "offen" and not zeilen_von(z, "powertrain", "quelle"), pt)
mk = zeilen_von(z, "engine_name", "entscheidung")[0]
mk_q = zeilen_von(z, "engine_name", "quelle")
check("A9 [ECHT] Motorkern: Tier-1-Herstellerbeleg gezählt (Schwelle 1 Tier-1), Japan-Beleg auf Tier 3 gedeckelt und nicht gezählt, "
      "ADAC wegen Zeitfenster ausgeschlossen (Kernbeleg NICHT geprüft)",
      feld_wert(mk, "konsens") == "bestaetigt" and feld_wert(mk, "quellen_tier1") == "1" and feld_wert(mk, "kern") == "v8"
      and any("toyota-media.de" in x and feld_wert(x, "status") == "gezaehlt" for x in mk_q)
      and any("global.toyota" in x and feld_wert(x, "status") == "nicht_gezaehlt:beleg_tier_3"
              and feld_wert(x, "fremdmarkt_gedeckelt") == "ja" for x in mk_q)
      and any("adac.de" in x and "ausgeschlossen:baujahr_ausserhalb_datenblatt(kernbeleg_nicht_geprueft)" in x for x in mk_q),
      mk + "\n" + "\n".join(mk_q))
check("A10 Fundstellen, Quellen und Domains werden getrennt gezählt (Motorkern: geprüfte Quellen, Quellen mit Kernbeleg, Fundstellen)",
      feld_wert(mk, "quellen_geprueft") == "5" and feld_wert(mk, "quellen_mit_kernbeleg") == "2"
      and feld_wert(mk, "fundstellen_kernbeleg") == "2" and feld_wert(mk, "domains_tier<=2") == "1", mk)

print("\n== B: Motorkern-Entscheidung -- Schwelle knapp verfehlt vs. erreicht, kein Kernbeleg ==")
# Grundlage, die die Identität belegt (zwei Domains), aber selbst keinen V8-Kernbeleg trägt (ADAC-Fenster ausserhalb, AMS/CarWiki ohne V8 im Hubraumsatz)
BASIS = [ADAC_AUTOMATIK, AMS, CARWIKI]
EINE_T2 = BASIS + [synth("Der Lexus RC F wird von einem 5,0 Liter V8 angetrieben.", host="www.autobild.de")]
_, zb = auswerten(EINE_T2)
mb = zeilen_von(zb, "engine_name", "entscheidung")[0]
check("B1 [SYNTH] EINE Tier-2-Domain mit Kernbeleg, kein Tier-1: nicht bestätigt, Grund nennt die knapp verfehlte Schwelle",
      feld_wert(mb, "konsens") == "nicht_bestaetigt" and feld_wert(mb, "grund") == "schwelle_verfehlt_1_domain_tier<=2_ohne_tier1"
      and feld_wert(mb, "domains_tier<=2") == "1" and feld_wert(mb, "quellen_tier1") == "0" and feld_wert(mb, "ergebnis") == "offen", mb)
ZWEI_T2 = BASIS + [synth("Der Lexus RC F wird von einem 5,0 Liter V8 angetrieben.", host="www.autobild.de"),
                  synth("Der Lexus RC F fährt mit einem 5,0-Liter-V8.", host="www.auto-motor-und-sport.de", pfad="/synthetisch/zwei")]
_, zc = auswerten(ZWEI_T2)
mc = zeilen_von(zc, "engine_name", "entscheidung")[0]
check("B2 [SYNTH] Positivkontrolle: zwei Tier-2-Domains -> bestätigt (Schwelle unverändert)",
      feld_wert(mc, "konsens") == "bestaetigt" and feld_wert(mc, "domains_tier<=2") == "2"
      and (feld_wert(mc, "ergebnis") or "").startswith("5.0_V8("), mc)
_, zd = auswerten(BASIS)
md = zeilen_von(zd, "engine_name", "entscheidung")[0]
check("B3 [ECHT] nur ADAC (Fenster ausserhalb) + CarWiki + AMS (ohne V8 im Hubraumsatz): kein Kernbeleg, Grund 'kein_kernbeleg'",
      feld_wert(md, "konsens") == "nicht_bestaetigt" and feld_wert(md, "grund") == "kein_kernbeleg"
      and feld_wert(md, "quellen_mit_kernbeleg") == "0", md)
_, ze = auswerten(BASIS + [synth("Der Lexus RC F wird von einem V8-Saugmotor mit 4.969 cm³ angetrieben.", host="www.autobild.de"),
                           synth("Der Lexus RC F wird von einem V8-Saugmotor mit 4.969 cm³ angetrieben.", pfad="/synthetisch/zwei")])
check("B4 [SYNTH] Tausenderpunkt-Hubraum wird als Kernbeleg gezählt (zwei Tier-2-Domains, bestätigt)",
      feld_wert(zeilen_von(ze, "engine_name", "entscheidung")[0], "konsens") == "bestaetigt")

print("\n== B2: mehr als zehn Quellen -- entscheidende Quelle nach Position zehn bleibt sichtbar ==")
VIELE = [synth("Der Lexus RC F hat einen 5,0 Liter V8.", host="www.auto-motor-und-sport.de", pfad=f"/synthetisch/viel{i}")
         for i in range(12)] + [AMS, TOYOTA_MEDIA_DE, ADAC_AUTOMATIK]
_, zv = auswerten(VIELE)
hub_v = zeilen_von(zv, "displacement", "quelle")
nrs = [int(feld_wert(x, "nr")) for x in hub_v]
check("B2a mehr als zehn Quellenzeilen je Feld, fortlaufend nummeriert, keine Obergrenze",
      len(hub_v) > 10 and nrs == list(range(1, len(nrs) + 1)), str(len(hub_v)))
adac_v = [x for x in hub_v if "adac.de" in x]
check("B2b die verworfene ADAC-Quelle steht hinter Position 10 und traegt ihren Ablehnungsgrund",
      bool(adac_v) and int(feld_wert(adac_v[0], "nr")) > 10
      and feld_wert(adac_v[0], "status") == "verworfen:baujahr_ausserhalb_datenblatt", str(adac_v))
check("B2c kein 'weitere_quellen'-Rest mehr in den Entscheidungszeilen", not any("weitere_quellen" in x for x in zv))

print("\n== C: Konsens-Gründe und Datenschutz ==")
_, zf = auswerten(BASIS + [synth("Der Lexus RC F hat 5,0 Liter V8 mit 477 PS und ist ein Benziner.", host="www.autobild.de"),
                           synth("Der Lexus RC F fährt mit einem Benzinmotor.", host="www.auto-motor-und-sport.de", pfad="/synthetisch/zwei")])
kf = zeilen_von(zf, "fuel", "entscheidung")[0]
check("C1 [SYNTH] Kraftstoff: 'benzin' mit zwei Tier-2-Domains (+ CarWiki Tier 3) wird bestätigt (Gewicht 5, Grund 'schwelle_erreicht')",
      feld_wert(kf, "konsens") == "bestaetigt" and feld_wert(kf, "grund") == "schwelle_erreicht" and "benzin:gewicht=5" in kf and feld_wert(kf, "domains_tier<=2") == "2", kf)
QUERY_QUELLE = synth("Der Lexus RC F wird von einem 5,0 Liter V8-Saugmotor angetrieben.", host="www.toyota-media.de",
              pfad="/blog/x?token=GEHEIM123&session=abc#frag")
_, zq = auswerten(BASIS + [QUERY_QUELLE])
alle = "\n".join(zq)
check("C2 URL ohne Query/Fragment, kein Seitentext, keine Schlüssel/Tokens in den Zeilen",
      "GEHEIM123" not in alle and "session=" not in alle and "#frag" not in alle and "Saugmotor angetrieben" not in alle
      and "www.toyota-media.de/blog/x" in alle, alle[:300])
w_q, _ = tr.werte_identitaet_aus(BASIS + [QUERY_QUELLE], ZIEL)
check("C3 Diagnose ist ein interner Wert: weder in model_dump noch in JSON der Identität",
      w_q.diagnose is not None and "diagnose" not in w_q.model_dump() and "diagnose" not in w_q.model_dump_json())

print("\n== D: Diagnose ändert Ergebnis und Aufrufzahl nicht (Providerlauf mit kontrollierten Suchantworten) ==")
IDENT_QUELLEN = ECHT


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class FakeClient:
    posts: list[dict] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        q = json["query"]
        FakeClient.posts.append({"query": q, "raw": json.get("include_raw_content")})
        if any(x in q for x in ("Rückruf", "Schwachstellen", "Gebrauchtwagen", "Wartungsintervall", "Inspektionsintervall")):
            return _Resp({"results": []})
        return _Resp({"results": [dict(r) for r in IDENT_QUELLEN]})


ws.httpx.AsyncClient = FakeClient
ws.TAVILY_API_KEY = "offline-test-key-not-real"


async def providerlauf():
    ws._cache.clear()
    FakeClient.posts = []
    async with provider_scope("kaufcheck", key="identdiag-test") as scope:
        rec = await tr.TavilyTechnicalResearchProvider().recherchiere(
            marke="Lexus", modell="RC F", baujahr=2017, motor="5.0 V8, Heckantrieb", ausgeloest_durch="test",
            ziel={"leistung_ps": 477, "kraftstoff": "Benzin", "getriebe": "Automatik"})
        return rec, dict(scope.calls), dict(scope.skipped)


(rec, calls, skipped), pz = mit_log(lambda: asyncio.run(providerlauf()))
check("D1 beide Identitätsrunden sind mit EINER Laufkennung zuordenbar (dieselbe wie TechnischeRecherche.diagnose_lauf)",
      {feld_wert(x, "lauf") for x in pz} == {rec.diagnose_lauf} and {feld_wert(x, "runde") for x in pz} >= {"1"}, str(sorted({feld_wert(x, "runde") for x in pz})))
check("D2 je Feld eine Entscheidungszeile pro Runde (fünf Felder)",
      all(len(zeilen_von([x for x in pz if " runde=1 " in x], f, "entscheidung")) == 1 for f in tr._IDENT_DIAG_FELDER))
check("D3 Aufrufzahl: jeder HTTP-Versuch ist genau ein gezählter Tavily-Call, nichts übersprungen, Anfragenzähler stimmt",
      calls["tavily"] == len(FakeClient.posts) == rec.anfragen and skipped["tavily"] == 0 and calls["gemini"] == 0,
      f"{calls} {skipped} posts={len(FakeClient.posts)} anfragen={rec.anfragen}")
check("D4 die Diagnose steckt nicht in der serialisierten Recherche", "diagnose" not in rec.identitaet.model_dump()
      and "diagnose" not in rec.model_dump() and "diagnose_lauf" not in rec.model_dump())

print("\n== E: Weitergabe an Ausgabe und Rückrufprüfung ==")
REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, motor="5.0 V8, Heckantrieb",
                       kraftstoff="Benzin", leistung_ps=477, getriebe="Automatik", antrieb="Heck", ausstattung=[])
identity = VehicleIdentity.from_check_context(None, None, REQ)
identity.apply_web_evidence(rec)
_, ze = mit_log(lambda: tr.identitaet_diagnose_ausgabe(rec, identity))
wg = {feld_wert(x, "feld"): x for x in ze if " stufe=weitergabe " in x}
check("E1 alle fünf Felder in der Weitergabe; Hubraum von Web bestätigt, Motorbezeichnung nur als Teilbestätigung '5.0 V8', "
      "Kraftstoff/Antrieb ohne Web-Wert (user_only), Elektrifizierung unknown",
      set(wg) == set(tr._IDENT_DIAG_FELDER)
      and feld_wert(wg["displacement"], "bestaetigt_durch") == "web" and feld_wert(wg["displacement"], "web_wert") == "5.0"
      and feld_wert(wg["engine_name"], "status") == "user_only" and feld_wert(wg["engine_name"], "bestaetigte_teile") == "5.0_V8"
      and feld_wert(wg["fuel"], "status") == "user_only" and feld_wert(wg["fuel"], "web_wert") == "-"
      and feld_wert(wg["drivetrain"], "status") == "user_only" and feld_wert(wg["drivetrain"], "web_wert") == "-"
      and feld_wert(wg["powertrain"], "status") == "unknown", " | ".join(wg.values()))
achsen = next((x for x in ze if " stufe=rueckruf_achsen " in x), "")
check("E2 Rückruf-Achsen: Kraftstoff, mögliche Antriebsarten und Freigabe des Klassen-Scopes nachvollziehbar; Elektrifizierung "
      "unbekannt -> 'offen:antriebsart_unbekannt' (nichts aus Heckantrieb/Benzin abgeleitet)",
      feld_wert(achsen, "klassenscope") == "offen:antriebsart_unbekannt" and feld_wert(achsen, "antriebsarten") == "-"
      and feld_wert(wg["powertrain"], "status") in ("unknown", "ambiguous"), achsen + "\n" + wg["powertrain"])
check("E3 ohne Laufkennung (kein Web-Lauf) schreibt die Ausgabe-Stufe nichts", mit_log(
    lambda: tr.identitaet_diagnose_ausgabe(None, identity))[1] == [])

print("\n" + ("ALLE GRÜN" if not FAILS else f"{len(FAILS)} FEHLER: {FAILS}"))
sys.exit(1 if FAILS else 0)
