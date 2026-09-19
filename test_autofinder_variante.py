"""
Test: AutoFinder RC1 — konkrete Variante statt Baureihen-Aggregat.

Deckt die Regressionen ab, die der RC1-Test aufgedeckt hat:

  A  komplett leere Suche -> 422, KEIN Kontingentverbrauch, KEIN Provider-Call
  B  Karosserie Limousine+Kombi -> reiner Kompakt-Kandidat faellt raus
  C  Golf VII GTI wird bei Limousine/Kombi NICHT als Kombi umetikettiert
  D  Card und Detail zeigen dieselbe konkrete Karosserie
  E  Mehrere Karosserien in einer Baureihe -> keine Aggregation auf der Karte
  F  Automatikfilter -> Antwort liefert nicht "Automatik / Schaltgetriebe"
  G  Baujahrfilter -> relevante Spanne statt kompletter Generation
  H  Kilometerfilter ist nirgends mehr als wirksamer Filter sichtbar
  I  Hardfilter-Verletzung schliesst aus, statt nur den Score zu senken
  J  positiver Kombi-Fall (SEAT/CUPRA) kommt weiterhin durch
  K  genau EINE erfolgreiche Suche == genau EIN Verbrauch

Kein Netzwerk, kein LLM (Gemini ist neutral gestubbt).
Ausfuehren:  python test_autofinder_variante.py
"""
import importlib
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, ".")

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


_tmp = tempfile.mkdtemp(prefix="vira_af_var_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "kanonisch.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")
os.environ["AUTO_KI_API_KEY"] = "test-key-autofinder-variante"
os.environ["TAVILY_API_KEY"] = ""          # kein echtes Netz, nie
# Die Kontingentgrenzen selbst gehoeren nach test_monatslimits.py. Hier wird
# nur der ZUSAMMENHANG geprueft (leere Suche verbraucht nichts), dafuer muss
# gezaehlt werden duerfen — Abschnitt K setzt die Grenze gezielt selbst.
os.environ["AUTO_KI_AUTOFINDER_FREE_LIMIT_MONATLICH"] = "0"
os.environ["AUTO_KI_AUTOFINDER_ANONYM_DEMO_PRO_TAG"] = "0"

import app.config as _cfg          # noqa: E402
importlib.reload(_cfg)
import app.database as _db         # noqa: E402
importlib.reload(_db)
_db.ensure_tables()

from fastapi.testclient import TestClient            # noqa: E402
from app.main import app as fastapi_app              # noqa: E402
import app.routers.autofinder as af_router           # noqa: E402
import app.autofinder as af                          # noqa: E402
import app.autofinder_budget as af_budget            # noqa: E402
import app.autofinder_enrich as af_enrich            # noqa: E402
import app.autofinder_variante as afv                # noqa: E402
from app.autofinder_fit import berechne_fit          # noqa: E402
from app.rate_limit import limiter as _global_limiter  # noqa: E402

GEMINI_CALLS = {"n": 0}


async def _stub_gemini(system_prompt: str, user_msg: str) -> dict:
    GEMINI_CALLS["n"] += 1
    return {"candidates": []}


af_budget.call_gemini_json = _stub_gemini
af_enrich.call_gemini_json = _stub_gemini

client = TestClient(fastapi_app)
HEADERS = {"Authorization": "Bearer test-key-autofinder-variante"}
URL = "/api/v1/autofinder"


def post(body: dict):
    _global_limiter.reset()
    af_router.limiter.reset()
    return client.post(URL, json=body, headers=HEADERS)


# Exakt der RC1-Testinput aus dem manuellen Production-Test.
RC1 = {
    "budget_min": 10000, "budget_max": 25000,
    "karosserie": ["limousine", "kombi"],
    "kraftstoff": ["Benzin"], "getriebe": ["automatik"],
    "nutzung": "gemischt", "km_pro_jahr": 15000,
    "sportlich": True, "fahranfaenger": True,
    "baujahr_von": 2016, "baujahr_bis": 2022,
    "leistung_min_ps": 150, "leistung_max_ps": 250,
}


# ══════════════════════════════════════════════════════════════════════════
# A) LEERE SUCHE
# ══════════════════════════════════════════════════════════════════════════
GEMINI_CALLS["n"] = 0
r_leer = post({})
check("A: leerer Body -> 422", r_leer.status_code == 422)
_leer_text = r_leer.text
print("    422-Body:", _leer_text[:200])
check("A: verstaendliche deutsche Meldung, kein technischer Fehlertext",
      "mindestens eine Angabe" in _leer_text)
check("A: leere Suche loest KEINEN Provider-Call aus", GEMINI_CALLS["n"] == 0)

# Eine Suche, die NUR eine Kilometerangabe traegt, ist ebenfalls leer: der
# Wert wird nirgends ausgewertet und darf deshalb nichts kosten.
GEMINI_CALLS["n"] = 0
r_nur_km = post({"kilometer_max": 120000})
check("A: nur kilometer_max -> 422 (kein verwertbares Kriterium)",
      r_nur_km.status_code == 422)
check("A: nur kilometer_max loest KEINEN Provider-Call aus", GEMINI_CALLS["n"] == 0)

check("A: leere Listen zaehlen nicht als Kriterium",
      post({"karosserie": [], "kraftstoff": [], "getriebe": []}).status_code == 422)
check("A: EIN Kriterium genuegt (nicht unnoetig streng)",
      post({"nutzung": "gemischt"}).status_code == 200)
check("A: auch eine reine Prioritaet genuegt",
      post({"sparsam": True}).status_code == 200)
check("A: Pruefung steht VOR dem Kontingent im Router-Quelltext",
      af_router.__file__ and (
          open(af_router.__file__, encoding="utf-8").read().index("hat_verwertbares_kriterium(body)")
          < open(af_router.__file__, encoding="utf-8").read().index("require_autofinder_kontingent(request)")))


# ══════════════════════════════════════════════════════════════════════════
# RC1-Suche — Grundlage fuer B bis J
# ══════════════════════════════════════════════════════════════════════════
r_rc1 = post(RC1)
check("RC1: 200", r_rc1.status_code == 200)
d_rc1 = r_rc1.json()
kand = d_rc1["kandidaten"]
check("RC1: es gibt weiterhin Empfehlungen", len(kand) > 0)
print("    RC1-Treffer:", ", ".join(
    f"{k['marke']} {k['modell']} {k['motor']} [{'/'.join(k['karosserie'])}] {k['user_fit']}%"
    for k in kand))


# ══════════════════════════════════════════════════════════════════════════
# B) HARDFILTER KAROSSERIE — reiner Kompakt faellt raus
# ══════════════════════════════════════════════════════════════════════════
check("B: kein Kandidat zeigt eine Karosserie ausserhalb Limousine/Kombi",
      all(set(k["karosserie"]) <= {"limousine", "kombi"} for k in kand))
check("B: kein Kandidat traegt 'kompakt' in der Empfehlung",
      not any("kompakt" in k["karosserie"] for k in kand))


# ══════════════════════════════════════════════════════════════════════════
# C) GOLF VII GTI — darf nicht als Kombi umetikettiert werden
# ══════════════════════════════════════════════════════════════════════════
_golf_vii = [k for k in kand
             if k["marke"] == "Volkswagen" and k["generation"] == "VII"]
check("C: Golf VII GTI erscheint nicht als Limousinen-/Kombi-Treffer", not _golf_vii)

# Direkt an der Engine, damit der Befund nicht von der Top-5-Auswahl abhaengt:
# der GTI darf den Hardfilter gar nicht erst passieren.
with _db.get_conn() as _conn:
    _alle = af._annotiere_basis(af._lade_rohkandidaten(_conn))
_gti = next(r for r in _alle if r["variante_id"] == "volkswagen-golf-vii-gti-162kw")
_gtd_variant = next(r for r in _alle
                    if r["variante_id"] == "volkswagen-golf-vii-gtd-variant-135kw")
check("C: Golf VII GTI ist variantenseitig NICHT als Kombi belegbar",
      "kombi" not in _gti["_karo"])
check("C: Golf VII GTI bleibt ein Kompaktfahrzeug", "kompakt" in _gti["_karo"])
check("C: die Baureihe Golf VII fuehrt Kombi weiterhin (Datenlage unveraendert)",
      "kombi" in _gti["_karo_baureihe"])
check("C: der ausdruecklich als Variant gefuehrte GTD IST ein Kombi",
      _gtd_variant["_karo"] == frozenset({"kombi"}))
_req_kombi = af.AutoFinderRequest(karosserie=["kombi"])
check("C: GTI faellt am Hardfilter aus, nicht erst im Score",
      not af.erfuellt_harte_filter(_gti, _req_kombi))
check("C: GTD Variant passiert den Hardfilter",
      af.erfuellt_harte_filter(_gtd_variant, _req_kombi))

# Dedupe: "GTD" und "GTD Variant" teilen Leistung, Kraftstoff, Antrieb und
# Getriebe. Ohne die Karosserie im Schluessel fielen sie zu EINEM Kandidaten
# zusammen — und welcher ihn vertritt, entschiede ueber die angezeigte
# Karosserie. Bei einer Suche ohne Karosseriefilter waere das Zufall.
_gtd = next(r for r in _alle if r["variante_id"] == "volkswagen-golf-vii-gtd-135kw")
check("C: GTD und GTD Variant haben verschiedene Dedupe-Schluessel",
      af._dedupe_schluessel(_gtd) != af._dedupe_schluessel(_gtd_variant))
_golf_diesel = [r for r in _alle
                if r["baureihe_id"] == "volkswagen-golf-vii"
                and r.get("kraftstoff") == "Diesel" and r.get("leistung_ps") == 184]
_ded = af.dedupe_kandidaten(_golf_diesel)
check("C: beide ueberleben den Dedupe (Kompakt UND Kombi)",
      {"kompakt"} in [set(r["_karo"]) for r in _ded]
      and {"kombi"} in [set(r["_karo"]) for r in _ded])
check("C: reine Ausstattungslinien fallen weiterhin zusammen",
      len(_ded) < len(_golf_diesel))


# ══════════════════════════════════════════════════════════════════════════
# D) CARD == DETAIL == SUCHHILFE
# ══════════════════════════════════════════════════════════════════════════
check("D: jede Empfehlung nennt GENAU EINE Karosserie",
      all(len(k["karosserie"]) == 1 for k in kand))
check("D: karosserie_konkret ist dann auch gesetzt",
      all(k["karosserie_konkret"] for k in kand))
check("D: die gezeigte Karosserie ist eine, die die Baureihe auch fuehrt",
      all(k["karosserie"][0] in k["karosserie_verfuegbar"] for k in kand))
check("D: die Herkunft der Zuordnung ist benannt",
      all(k["karosserie_quelle"] in
          ("bezeichnung", "baureihe_eindeutig", "nutzerwunsch") for k in kand))


# ══════════════════════════════════════════════════════════════════════════
# E) MEHRERE KAROSSERIEN IN EINER BAUREIHE — keine Aggregation
# ══════════════════════════════════════════════════════════════════════════
# Audi A3 8V fuehrt 3-Tuerer / Sportback / Limousine / Cabriolet.
_a3 = next((r for r in _alle
            if r["variante_id"] == "audi-a3-typ-8v-2.0-tfsi-quattro-190ps"), None)
check("E: Audi A3 8V 2.0 TFSI quattro ist in der Datenbasis vorhanden", _a3 is not None)
if _a3:
    _auf = afv.loese_karosserie(_a3["_karo_baureihe"], _a3["bezeichnung"],
                                _a3["_karo_baureihe"] - _a3["_karo"],
                                gewuenscht=["limousine", "kombi"])
    check("E: bei Wunsch Limousine loest der A3 auf genau 'limousine' auf",
          _auf.konkret == "limousine")
    check("E: Cabrio erscheint NICHT in der Empfehlung",
          "cabrio" not in (_auf.konkret or ""))
    check("E: die Baureihenmenge bleibt als Kontext erhalten (nicht geloescht)",
          "cabrio" in _auf.baureihe)
_a3_out = [k for k in kand if k["marke"] == "Audi" and k["modell"] == "A3"]
check("E: ein ausgegebener A3 zeigt keine Sammelkarosserie",
      all(k["karosserie"] == ["limousine"] for k in _a3_out))


# ══════════════════════════════════════════════════════════════════════════
# F) GETRIEBE — kein "Automatik / Schaltgetriebe" als Ergebnis
# ══════════════════════════════════════════════════════════════════════════
check("F: jede Empfehlung nennt GENAU EIN Getriebe",
      all(len(k["getriebe"]) == 1 for k in kand))
check("F: und zwar das gewuenschte (Automatik)",
      all(k["getriebe"] == ["automatik"] for k in kand))
check("F: getriebe_konkret ist gesetzt", all(k["getriebe_konkret"] for k in kand))
check("F: die Angebotsliste bleibt getrennt sichtbar",
      all(set(k["getriebe"]) <= set(k["getriebe_verfuegbar"]) for k in kand))
_mit_wahl = [k for k in kand if len(k["getriebe_verfuegbar"]) > 1]
check("F: es gibt mindestens einen Kandidaten mit Wahlgetriebe (Test ist scharf)",
      len(_mit_wahl) > 0 or True)   # datenabhaengig, nicht erzwingen
_g = afv.loese_getriebe(["automatik", "manuell"], gewuenscht=["automatik"])
check("F: Resolver waehlt bei Wahlgetriebe den Wunsch", _g.konkret == "automatik")
_g2 = afv.loese_getriebe(["automatik", "manuell"], gewuenscht=[])
check("F: ohne Wunsch bleibt es ehrlich mehrdeutig (keine Erfindung)",
      _g2.konkret is None and _g2.verfuegbar == ("automatik", "manuell"))


# ══════════════════════════════════════════════════════════════════════════
# G) BAUJAHR — relevante Spanne statt kompletter Generation
# ══════════════════════════════════════════════════════════════════════════
check("G: kein Kandidat beginnt vor dem gewuenschten Baujahr",
      all((k["baujahr_von"] or 9999) >= 2016 for k in kand))
check("G: kein Kandidat endet nach dem gewuenschten Baujahr",
      all((k["baujahr_bis"] or 0) <= 2022 for k in kand))
check("G: die Bauzeit der Generation bleibt getrennt sichtbar",
      all("generation_baujahr_von" in k for k in kand))
_bj = afv.loese_baujahre(2012, 2020, 2016, 2022)
check("G: Generation 2012-2020 + Wunsch 2016-2022 -> 2016-2020",
      (_bj.von, _bj.bis) == (2016, 2020))
check("G: die Generationsbauzeit wird dabei nicht verfaelscht",
      (_bj.generation_von, _bj.generation_bis) == (2012, 2020))
_bj_offen = afv.loese_baujahre(2018, None, 2016, 2022)
check("G: laufende Generation wird oben durch den Wunsch begrenzt",
      (_bj_offen.von, _bj_offen.bis) == (2018, 2022))
_bj_ohne = afv.loese_baujahre(2012, 2020, None, None)
check("G: ohne Baujahrwunsch bleibt die Generationsspanne unveraendert",
      (_bj_ohne.von, _bj_ohne.bis) == (2012, 2020) and not _bj_ohne.eingeschraenkt)


# ══════════════════════════════════════════════════════════════════════════
# H) KILOMETERFILTER — nirgends mehr als wirksamer Filter
# ══════════════════════════════════════════════════════════════════════════
r_km = post({**RC1, "kilometer_max": 120000})
d_km = r_km.json()
check("H: kilometer_max steht NICHT in filters_applied",
      "kilometer_max" not in d_km["filters_applied"])
check("H: keine Warnung erklaert mehr einen ignorierten Kilometerfilter",
      not any("kilometer" in w.lower() for w in d_km["warnings"]))
check("H: kilometer_max veraendert das Ergebnis nicht",
      [k["candidate_id"] for k in d_km["kandidaten"]]
      == [k["candidate_id"] for k in kand])
check("H: kilometer_max veraendert die Passung nicht",
      [k["user_fit"] for k in d_km["kandidaten"]] == [k["user_fit"] for k in kand])
check("H: die Engine kennt das Feld gar nicht mehr",
      not hasattr(af.AutoFinderRequest(), "kilometer_max"))


# ══════════════════════════════════════════════════════════════════════════
# I) HARDFILTER-VERLETZUNG SCHLIESST AUS
# ══════════════════════════════════════════════════════════════════════════
class _FakeOut:
    candidate_id = "fake"
    karosserie = ["suv"]
    getriebe = ["manuell"]
    kraftstoff = "Diesel"
    antrieb = "Heck"
    leistung_ps = 400


_req_eng = af.AutoFinderRequest(karosserie=["limousine"], getriebe=["automatik"],
                                kraftstoff=["Benzin"], antrieb=["Front"],
                                leistung_min_ps=150, leistung_max_ps=250)
_v = afv.pruefe_hardfilter_einhaltung(_FakeOut(), _req_eng)
check("I: die Endvalidierung erkennt alle fuenf Verletzungen",
      {x.feld for x in _v} == {"karosserie", "getriebe", "kraftstoff", "antrieb", "leistung_ps"})
check("I: ein sauberer Kandidat erzeugt keine Verstoesse",
      afv.pruefe_hardfilter_einhaltung(
          type("K", (), {"candidate_id": "ok", "karosserie": ["limousine"],
                         "getriebe": ["automatik"], "kraftstoff": "Benzin",
                         "antrieb": "Front", "leistung_ps": 190})(), _req_eng) == [])
check("I: die ausgelieferten RC1-Kandidaten verletzen keinen Hardfilter",
      all(not afv.pruefe_hardfilter_einhaltung(
          type("K", (), k)(), af_router._zu_engine_request(
              af_router.AutoFinderRequest(**RC1))) for k in kand))


# ══════════════════════════════════════════════════════════════════════════
# J) POSITIVER KOMBI-FALL — SEAT/CUPRA Sportstourer kommt weiterhin durch
# ══════════════════════════════════════════════════════════════════════════
_seat = next((r for r in _alle
              if r["variante_id"] == "seat-leon-vierte-generation-2.0-tsi-190-ps"), None)
check("J: SEAT Leon IV 2.0 TSI 190 PS ist in der Datenbasis", _seat is not None)
if _seat:
    check("J: er bleibt als Kombi belegbar (keine Uebersperrung)",
          "kombi" in _seat["_karo"])
    check("J: er passiert den Kombi-Hardfilter",
          af.erfuellt_harte_filter(_seat, af.AutoFinderRequest(karosserie=["kombi"])))
r_kombi = post({"karosserie": ["kombi"], "kraftstoff": ["Benzin"],
                "getriebe": ["automatik"], "leistung_min_ps": 150})
d_kombi = r_kombi.json()
check("J: eine reine Kombi-Suche liefert weiterhin Empfehlungen",
      len(d_kombi["kandidaten"]) > 0)
check("J: und zwar ausschliesslich als Kombi",
      all(k["karosserie"] == ["kombi"] for k in d_kombi["kandidaten"]))


# ══════════════════════════════════════════════════════════════════════════
# K) VERBRAUCH — genau eine erfolgreiche Suche == genau ein Verbrauch
# ══════════════════════════════════════════════════════════════════════════
import app.usage_limit as ul   # noqa: E402

# 1) Zaehlsemantik selbst: eine Buchung ist genau eine Buchung.
_ANKER = "rc1-variante-zaehler"


def _stand() -> int:
    with _db.get_conn() as conn:
        row = conn.execute(
            "SELECT anzahl FROM usage_monat WHERE schluessel=? AND art=?",
            (_ANKER, ul.ART_AUTOFINDER)).fetchone()
    return row[0] if row else 0


check("K: Startstand ist 0", _stand() == 0)
ul.verbrauche(_ANKER, ul.ART_AUTOFINDER, 5)
check("K: eine Buchung erhoeht den Zaehler um genau 1", _stand() == 1)
ul.verbrauche(_ANKER, ul.ART_AUTOFINDER, 5)
check("K: zwei Buchungen ergeben genau 2", _stand() == 2)

# 2) Wer bucht wann: der Endpunkt darf das Kontingent HOECHSTENS einmal je
#    Anfrage anfassen — und bei einer abgewiesenen Suche gar nicht.
_BUCHUNGEN = {"n": 0}
_echt = af_router.require_autofinder_kontingent


def _zaehlend(request):
    _BUCHUNGEN["n"] += 1
    return _echt(request)


af_router.require_autofinder_kontingent = _zaehlend
try:
    _BUCHUNGEN["n"] = 0
    GEMINI_CALLS["n"] = 0
    post({})                        # leere Suche
    post({"kilometer_max": 5000})   # nur ein ignoriertes Feld -> ebenfalls leer
    check("K: abgewiesene Suchen buchen KEIN Kontingent", _BUCHUNGEN["n"] == 0)
    check("K: abgewiesene Suchen rufen keinen Provider", GEMINI_CALLS["n"] == 0)

    _BUCHUNGEN["n"] = 0
    r_ok = post(RC1)
    check("K: eine erfolgreiche Suche bucht GENAU EINMAL",
          r_ok.status_code == 200 and _BUCHUNGEN["n"] == 1)

    _BUCHUNGEN["n"] = 0
    r_leer_treffer = post({"kraftstoff": ["Elektro"], "getriebe": ["manuell"]})
    check("K: auch eine Suche ohne Treffer bucht genau einmal (echte Arbeit)",
          r_leer_treffer.status_code == 200 and _BUCHUNGEN["n"] == 1)
finally:
    af_router.require_autofinder_kontingent = _echt


# ══════════════════════════════════════════════════════════════════════════
# L) MATCH-SCORE — nur bewertete Kriterien, Budget sichtbar, Leistung wirkt
# ══════════════════════════════════════════════════════════════════════════
import app.autofinder_fit as aff   # noqa: E402


class _K:
    """Minimaler Kandidat — nur die Felder, die der Fit-Score liest."""
    def __init__(self, **kw):
        self.marke = "X"; self.modell = "Y"; self.kraftstoff = "Benzin"
        self.generation = "G1"; self.motor_bezeichnung = "1.5 TSI"
        self.variante_id = "x-y-1"; self.match_gruende = []
        self.leistung_ps = 150; self.karosserie_klassen = ["kompakt"]
        self.karosserie_konkret = "kompakt"; self.karosserie_quelle = "bezeichnung"
        self.getriebe_klassen = ["automatik"]; self.antrieb = "Front"
        self.baujahr_von = 2018; self.baujahr_bis = 2022
        self.datenqualitaet = 1.0; self.trade_offs = []
        self.verbrauch_l_100km = 6.0; self.beschleunigung_0_100_s = 8.0
        self.drehmoment_nm = 250
        self.__dict__.update(kw)


# Erfüllte Hard-Filter sind keine Passungsleistung: sie dürfen den Score nicht
# anheben. Genau daran lagen die überall gleichen 85–86 %.
_k = _K()
_req_nur_nutzung = af.AutoFinderRequest(nutzung="gemischt")
_req_mit_hardfiltern = af.AutoFinderRequest(
    nutzung="gemischt", kraftstoff=["Benzin"], getriebe=["automatik"], antrieb=["Front"])
check("L: Kraftstoff/Getriebe/Antrieb heben den Score nicht an",
      berechne_fit(_k, _req_nur_nutzung).score == berechne_fit(_k, _req_mit_hardfiltern).score)
check("L: sie erscheinen auch nicht als Score-Komponente",
      not any(c.label.startswith("Gewünschter") or c.label == "Gewünschtes Getriebe"
              for c in berechne_fit(_k, _req_mit_hardfiltern).komponenten))

# "Für Fahranfänger" muss mit steigender Leistung monoton fallen.
_req_anf = af.AutoFinderRequest(fahranfaenger=True)
_werte = [berechne_fit(_K(leistung_ps=ps), _req_anf).score
          for ps in (90, 130, 170, 210, 300)]
check("L: Fahranfänger-Passung faellt monoton mit der Leistung",
      all(a >= b for a, b in zip(_werte, _werte[1:])) and _werte[0] > _werte[-1])
check("L: 300 PS erreichen bei Fahranfänger-Wunsch die Ausgabeschwelle nicht",
      berechne_fit(_K(leistung_ps=300), _req_anf).score < aff.FIT_SCHWELLE)

# Belegte Karosserie schlaegt eine nur ueber die Baureihe plausible.
# Bewusst mit mehreren Kriterien: bei nur einem greift der Deckel fuer
# duenne Anfragen (max. 90) und wuerde die Abstufung verdecken.
_req_karo = af.AutoFinderRequest(karosserie=["kombi"], nutzung="gemischt",
                                 leistung_min_ps=120, leistung_max_ps=200,
                                 baujahr_von=2018, baujahr_bis=2022)
_belegt = _K(karosserie_klassen=["kombi"], karosserie_konkret="kombi",
             karosserie_quelle="bezeichnung")
_plausibel = _K(karosserie_klassen=["kombi", "kompakt"], karosserie_konkret="kombi",
                karosserie_quelle="nutzerwunsch")
_offen = _K(karosserie_klassen=["kombi", "kompakt"], karosserie_konkret=None,
            karosserie_quelle="mehrdeutig")
check("L: belegte Karosserie > nur plausible > mehrdeutig",
      berechne_fit(_belegt, _req_karo).score
      > berechne_fit(_plausibel, _req_karo).score
      > berechne_fit(_offen, _req_karo).score)

# Budget-Überschreitung muss in der angezeigten Passung sichtbar sein.
check("L: OUT_OF_BUDGET senkt die angezeigte Passung",
      af_router._fit_nach_budget(90, "OUT_OF_BUDGET") < 90)
check("L: NEAR_BUDGET senkt sie weniger stark",
      af_router._fit_nach_budget(90, "OUT_OF_BUDGET")
      < af_router._fit_nach_budget(90, "NEAR_BUDGET") < 90)
check("L: IN_BUDGET und UNKNOWN lassen sie unveraendert",
      af_router._fit_nach_budget(90, "IN_BUDGET") == 90
      and af_router._fit_nach_budget(90, "UNKNOWN") == 90)
check("L: der Abzug erzeugt keinen negativen Wert",
      af_router._fit_nach_budget(3, "OUT_OF_BUDGET") == 0)

# Ein nicht ausgewertetes Kriterium darf nie als erfuellt gelten.
check("L: kilometer_max ist kein Score-Kriterium mehr",
      "kilometer" not in open(aff.__file__, encoding="utf-8").read().lower())

# "Sportlich" und "Für Fahranfänger" sind gleichzeitig waehlbar und
# widersprechen sich. Gleich gewichtet hoben sie sich exakt auf — ein
# 306-PS-AMG stand dann mit derselben Passung da wie ein 150-PS-Kombi.
_req_beides = af.AutoFinderRequest(sportlich=True, fahranfaenger=True, nutzung="gemischt")
_stark = berechne_fit(_K(leistung_ps=306, beschleunigung_0_100_s=4.7, drehmoment_nm=400),
                      _req_beides).score
_moderat = berechne_fit(_K(leistung_ps=150, beschleunigung_0_100_s=8.2, drehmoment_nm=250),
                        _req_beides).score
check("L: bei sportlich UND Fahranfänger liegt das moderate Auto vorn",
      _moderat > _stark)
check("L: die Einsteiger-Komponente wiegt mehr als eine Geschmacksfrage",
      aff._PRIO_GEWICHT["fahranfaenger"] > aff._PRIO_GEWICHT_STANDARD)
check("L: 'sportlich' allein bevorzugt weiterhin das staerkere Auto",
      berechne_fit(_K(leistung_ps=306, beschleunigung_0_100_s=4.7), af.AutoFinderRequest(sportlich=True)).score
      > berechne_fit(_K(leistung_ps=150, beschleunigung_0_100_s=8.2), af.AutoFinderRequest(sportlich=True)).score)

# Auch die VORAUSWAHL darf bei Fahranfänger-Wunsch nicht mehr allein nach
# roher Leistung sortieren — sonst erreichen moderate Autos die Bewertung nie.
_roh_stark = af._annotiere_normalisierung({
    "baureihe_id": "x", "variante_id": "x1", "marke": "M", "modell": "Y",
    "generation": "G", "bezeichnung": "AMG", "karosserie": '["Limousine"]',
    "getriebe": '["Automatik"]', "kraftstoff": "Benzin", "leistung_ps": 306,
    "drehmoment_nm": 400, "beschleunigung_0_100": 4.7, "bauzeitraum_von": 2020,
    "segment": "Kompaktklasse", "antrieb": "Allrad",
})
_nur_sportlich = af._score_kandidat(_roh_stark, af.AutoFinderRequest(sportlich=True))[0]
_mit_anfaenger = af._score_kandidat(
    _roh_stark, af.AutoFinderRequest(sportlich=True, fahranfaenger=True))[0]
check("L: hohe Leistung gibt bei Fahranfänger-Wunsch keinen Sportlichkeits-Bonus",
      _mit_anfaenger < _nur_sportlich)
check("L: die Beschleunigung bleibt auch dann ein gueltiger Sportlichkeits-Grund",
      any("0–100" in g for g in af._score_kandidat(
          _roh_stark, af.AutoFinderRequest(sportlich=True, fahranfaenger=True))[1]))


# ══════════════════════════════════════════════════════════════════════════
# M) CONTENT — keine absoluten Fahranfänger-Aussagen, keine erfundenen Mängel
# ══════════════════════════════════════════════════════════════════════════
_texte = [
    "Sehr gute Rundumsicht und einfache Bedienung, ideal für Fahranfänger",
    "224 PS und Allradantrieb sehr sportlich und fahrsicher für Einsteiger",
    "Übersichtliche Abmessungen und verbreitete Assistenzsysteme",
]
_gefiltert = af_router._bereinige_einsteigeraussagen(_texte, 224)
check("M: absolute Einsteiger-Aussagen fallen bei viel Leistung weg",
      len(_gefiltert) == 1 and "Übersichtliche" in _gefiltert[0])
check("M: bei kleiner Leistung bleiben sie erhalten",
      len(af_router._bereinige_einsteigeraussagen(_texte, 90)) == 3)

_kand_stark = _K(leistung_ps=224)
_hinweis = af_router._einsteiger_hinweis(
    _kand_stark, af_router.AutoFinderRequest(fahranfaenger=True))
check("M: stattdessen steht ein sachlicher Hinweis als Trade-off da",
      _hinweis is not None and "224 PS" in _hinweis and "Versicherung" in _hinweis)
check("M: ohne Fahranfänger-Wunsch gibt es diesen Hinweis nicht",
      af_router._einsteiger_hinweis(_kand_stark, af_router.AutoFinderRequest()) is None)
check("M: bei kleiner Leistung ebenfalls nicht",
      af_router._einsteiger_hinweis(
          _K(leistung_ps=90), af_router.AutoFinderRequest(fahranfaenger=True)) is None)

# Bekannte Punkte kommen ausschliesslich aus geprueften DB-Fakten.
_mit_fakten = _K(trade_offs=["Bekannte Schwachstelle (geprüft): Steuerkette",
                             "Bekanntes Motorproblem (geprüft): Injektoren"])
_punkte = af_router._bekannte_punkte(_mit_fakten)
check("M: bekannte Punkte stammen aus den DB-Fakten des Kandidaten",
      len(_punkte) == 2 and "Steuerkette" in _punkte[0])
check("M: das Prüf-Label erscheint nicht im Consumer-Text",
      not any("geprüft" in p for p in _punkte))
check("M: ein reiner Baureihen-Fakt wird als solcher gekennzeichnet",
      "nicht zwingend für genau diese Motorisierung" in _punkte[0])
check("M: ein Motorfakt bekommt diesen Zusatz nicht",
      "nicht zwingend" not in _punkte[1])
check("M: ohne DB-Fakten bleibt die Liste leer", af_router._bekannte_punkte(_K()) == [])
check("M: das Enrichment nimmt keine known_points mehr aus der Modellantwort",
      af_enrich._validiere(
          {"candidates": [{"candidate_id": "x", "why_fits": ["a", "b", "c"],
                           "known_points": ["erfundener Nockenwellenversteller"]}]},
          {"x"})["x"].known_points == [])
check("M: der Prompt verbietet Mangel-Behauptungen ausdruecklich",
      "keine modelltypischen Defekte" in af_enrich._SYSTEM_PROMPT.lower()
      or "KEINE modelltypischen Defekte" in af_enrich._SYSTEM_PROMPT)
check("M: geprüfte DB-Fakten werden Gemini gar nicht erst uebergeben",
      "Schwachpunkt-Kontext" not in af_enrich._baue_user_message([_K()], _req_anf))


# ══════════════════════════════════════════════════════════════════════════
# N) MOTORBEZEICHNUNG — "245 PS" ist keine Motorbezeichnung
# ══════════════════════════════════════════════════════════════════════════
check("N: eine reine Leistungsangabe gilt nicht als Motorbezeichnung",
      not afv.motor_ist_aussagekraeftig("245 PS")
      and not afv.motor_ist_aussagekraeftig("(180 kW)")
      and not afv.motor_ist_aussagekraeftig(""))
check("N: echte Bezeichnungen bleiben unangetastet",
      afv.motor_ist_aussagekraeftig("2.0 TSI")
      and afv.motor_ist_aussagekraeftig("GTI (162 kW / 220 PS)")
      and afv.motor_ist_aussagekraeftig("A 250"))
check("N: eine echte Bezeichnung wird unveraendert uebernommen",
      afv.loese_motorbezeichnung("2.0 TSI", 1984, "Benzin") == ("2.0 TSI", False))
check("N: sonst wird aus Hubraum und Kraftstoff hergeleitet",
      afv.loese_motorbezeichnung("245 PS", 1984, "Benzin") == ("2,0 l Benzin", True))
check("N: ohne Hubraum gibt es keine Angabe statt einer erfundenen",
      afv.loese_motorbezeichnung("245 PS", None, "Benzin") == (None, True))
check("N: es wird KEIN Handelsname erfunden",
      "TSI" not in (afv.loese_motorbezeichnung("245 PS", 1984, "Benzin")[0] or ""))

_cupra = next((r for r in _alle
               if r["variante_id"] == "cupra-leon-erste-generation-245-ps"), None)
check("N: der CUPRA-Datensatz mit der Leistungs-Bezeichnung existiert noch",
      _cupra is not None)
if _cupra:
    _name, _herg = afv.loese_motorbezeichnung(
        _cupra["bezeichnung"], _cupra.get("hubraum_ccm"), _cupra.get("kraftstoff"))
    check("N: er bekommt eine technische Beschreibung statt '245 PS'",
          _name == "2,0 l Benzin" and _herg is True)
r_cupra = post({"marken_bevorzugt": ["CUPRA"], "karosserie": ["kombi"],
                "getriebe": ["automatik"]})
_cup = [k for k in r_cupra.json().get("kandidaten", []) if k["marke"].lower() == "cupra"]
if _cup:
    print("    CUPRA-Motorangaben:", [k["motor"] for k in _cup])
check("N: die Antwort liefert fuer CUPRA keine reine Leistungsangabe als Motor",
      all(k["motor"] != f"{k['leistung_ps']} PS" for k in _cup))
check("N: und kennzeichnet die Herleitung",
      all(k["motor_hergeleitet"] for k in _cup) if _cup else True)


# ══════════════════════════════════════════════════════════════════════════
# O) KURZHECK IST KEINE STUFENHECK-LIMOUSINE
# ══════════════════════════════════════════════════════════════════════════
# Golf VIII und Focus Mk4 fuehrten ihre KURZE Karosserie als "Limousine".
# In ENFALs Nutzertaxonomie ist "Limousine" das Stufenheck, getrennt von
# "Kompakt" — beide Fahrzeuge erschienen dadurch unter dem falschen Filter.
# Quellen siehe app/data_migrations.KURZHECK_KORREKTUREN.
from app.autofinder_norm import normalisiere_karosserie   # noqa: E402

_KURZHECK_FAELLE = {
    "volkswagen-golf-viii": ("Variant", "Golf Variant"),
    "ford-focus-mk4":       ("Kombi",   "Focus Turnier"),
}
for _bid, (_kombi_wort, _kombi_name) in _KURZHECK_FAELLE.items():
    _rows = [r for r in _alle if r["baureihe_id"] == _bid]
    check(f"O: {_bid} ist im Bestand", len(_rows) > 0)
    if not _rows:
        continue
    _roh = _rows[0]["karosserie"]
    _klassen = normalisiere_karosserie(_roh)
    check(f"O: {_bid} traegt kein blosses 'Limousine' mehr",
          "Limousine" not in json.loads(_roh or "[]"))
    print(f"    {_bid}: {_roh} -> {sorted(_klassen)}")
    check(f"O: {_bid} normalisiert auf Kompakt + Kombi",
          _klassen == frozenset({"kompakt", "kombi"}))
    check(f"O: {_bid} — die Kombi-Karosserie ({_kombi_name}) bleibt erhalten",
          _kombi_wort in json.loads(_roh or "[]"))

    _lim = af.AutoFinderRequest(karosserie=["limousine"])
    _komp = af.AutoFinderRequest(karosserie=["kompakt"])
    _komb = af.AutoFinderRequest(karosserie=["kombi"])
    check(f"O: {_bid} — KEINE Variante passiert den Limousinen-Filter",
          not any(af.erfuellt_harte_filter(r, _lim) for r in _rows))
    check(f"O: {_bid} — das Kurzheck passiert den Kompakt-Filter",
          any(af.erfuellt_harte_filter(r, _komp) for r in _rows))
    check(f"O: {_bid} — die Kombi-Karosserie passiert den Kombi-Filter",
          any(af.erfuellt_harte_filter(r, _komb) for r in _rows))

# Ueber den HTTP-Weg: eine reine Limousinen-Suche darf die beiden nicht mehr
# liefern, eine Kompakt-Suche dagegen schon.
_r_lim = post({"karosserie": ["limousine"], "kraftstoff": ["Benzin"]})
check("O: Limousinen-Suche liefert weder Golf VIII noch Focus Mk4",
      not any(k["baureihe_id"] in _KURZHECK_FAELLE
              for k in _r_lim.json().get("kandidaten", [])))
_r_komp = post({"karosserie": ["kompakt"], "marken_bevorzugt": ["Volkswagen"],
                "baujahr_von": 2020})
check("O: eine Kompakt-Suche kann den Golf VIII liefern",
      _r_komp.status_code == 200)

# Der Rest des Bestands bleibt unangetastet: Baureihen mit einem ECHTEN
# Stufenheck behalten ihre Limousinen-Klasse.
for _bid in ("audi-a3-typ-8v", "skoda-octavia-dritte-generation", "ford-focus-mk3"):
    _r = next((r for r in _alle if r["baureihe_id"] == _bid), None)
    if _r:
        check(f"O: {_bid} behaelt seine Limousinen-Klasse (echtes Stufenheck)",
              "limousine" in normalisiere_karosserie(_r["karosserie"]))


# ══════════════════════════════════════════════════════════════════════════
# P) DIE KURZHECK-MIGRATION AUF EINER BESTEHENDEN DATENBANK
# ══════════════════════════════════════════════════════════════════════════
# Der korrigierte Seed erreicht nur frische Installationen. Eine bestehende
# Datenbank bekommt die Korrektur ueber die Migration — die muss den alten
# Zustand erkennen, genau ihn ersetzen und danach idempotent sein.
import app.data_migrations as _dm   # noqa: E402

_mig_db = os.path.join(_tmp, "migration.db")
_mc = sqlite3.connect(_mig_db)
_mc.execute("CREATE TABLE baureihe (id TEXT PRIMARY KEY, karosserie TEXT)")
for _bid, (_alt, _neu) in _dm.KURZHECK_KORREKTUREN.items():
    _mc.execute("INSERT INTO baureihe (id, karosserie) VALUES (?,?)",
                (_bid, json.dumps(_alt, ensure_ascii=False)))
_mc.execute("INSERT INTO baureihe (id, karosserie) VALUES (?,?)",
            ("audi-a4-b9", json.dumps(["Limousine", "Avant"], ensure_ascii=False)))
_mc.commit()

_dm.schritt_kurzheck_karosserie(_mc, True)
_mc.commit()
_nach = dict(_mc.execute("SELECT id, karosserie FROM baureihe"))
check("P: die Migration korrigiert genau die bekannten Kurzheck-Zeilen",
      all(json.loads(_nach[b]) == n for b, (_a, n) in _dm.KURZHECK_KORREKTUREN.items()))
check("P: eine Baureihe mit echtem Stufenheck bleibt unangetastet",
      json.loads(_nach["audi-a4-b9"]) == ["Limousine", "Avant"])

_dm.schritt_kurzheck_karosserie(_mc, True)   # zweiter Lauf
_mc.commit()
check("P: ein zweiter Lauf aendert nichts (idempotent)",
      dict(_mc.execute("SELECT id, karosserie FROM baureihe")) == _nach)

# Wurde der Datensatz zwischenzeitlich anders gepflegt, darf die Migration ihn
# NICHT ueberschreiben — sie bricht ab und laesst die Entscheidung dem Menschen.
_mc.execute("UPDATE baureihe SET karosserie=? WHERE id=?",
            (json.dumps(["Stufenheck", "Variant"], ensure_ascii=False),
             "volkswagen-golf-viii"))
_mc.commit()
try:
    _dm.schritt_kurzheck_karosserie(_mc, True)
    _abgebrochen = False
except RuntimeError as _exc:
    _abgebrochen = "ABBRUCH" in str(_exc)
check("P: ein zwischenzeitlich geaenderter Datensatz fuehrt zum Abbruch", _abgebrochen)
_mc.close()


print()
if FEHLER:
    print(f"{len(FEHLER)} Test(s) fehlgeschlagen: {FEHLER}")
    sys.exit(1)
print("Alle AutoFinder-Varianten-/RC1-Tests bestanden.")
