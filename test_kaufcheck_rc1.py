"""Regressionstests KaufCheck RC1 — Replay des echten Produktionsfalls.

Deterministisch, KEIN Gemini-, KEIN Tavily-Aufruf:
  * Eingabe      = exakt der RC1-Testinput (BMW 330i, 2019, 95.000 km, …)
  * LLM-Antwort  = die ECHTE, in Production gespeicherte Gemini-Antwort dieses
                   Checks (tests_fixtures/kaufcheck_rc1_bmw_330i.json)
  * Webtreffer   = die vier ECHTEN Treffer dieses Checks (mobile.de "Access
                   denied", PicClick-Teileangebot, Ultraleicht-Verkaufsseite,
                   BMW-X3-Leasing)
  * Datenbank    = Kopie der lokalen DB, auf die der G20-Nachtrag angewendet
                   wurde (die lokale DB selbst bleibt unberührt)
  * Datum        = fest 21.09.2026 (Tag des Befunds)

Geprüft wird die Pipeline NACH dem LLM — genau dort liegen die Root Causes:
HU-Bewertung, Variantenzuordnung, Quellenfilter, Preis ohne Markt, Rückruf-
Sperre, Empfehlung vs. Risiken, Checklisten-Templates, Datenqualität.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

# ── Isolierte DB-Kopie mit angewandtem G20-Nachtrag ─────────────────────────
from app.config import DB_PATH as _LIVE  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="kc_rc1_")
_DB = os.path.join(_TMP, "auto_ki.db")
shutil.copy(_LIVE, _DB)
os.environ["AUTO_KI_DB_PATH"] = _DB
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_TMP, "chroma")
for _m in [m for m in list(sys.modules) if m.startswith("app")]:
    del sys.modules[_m]

import app.data_migrations as dm  # noqa: E402

with sqlite3.connect(_DB) as _c:
    dm.schritt_kba_g20_nachtrag(_c, True)
    _c.commit()

import app.kaufcheck as kc  # noqa: E402
import app.database as database  # noqa: E402
from app.models import KaufCheckRequest  # noqa: E402
from app.hu_termin import bewerte_hu, bereinige_bericht, PLAUSIBEL, ABGELAUFEN, UNGEWOEHNLICH_WEIT  # noqa: E402
from app.recall_filter import rueckruf_ist_belegt, gefilterte_rueckrufe  # noqa: E402
from app.kaufaktionen import schwachstellen_art, verkaeuferfrage, getriebe_art  # noqa: E402
from app.markt_quellen import ablehnungsgrund  # noqa: E402
from app.postprocess import neutralisiere_preiszeile_ohne_markt  # noqa: E402

FEHLER: list[str] = []
PASS = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FEHLER.append(f"{name}{(' — ' + detail) if detail else ''}")
        print(f"  FAIL  {name}{(' — ' + detail) if detail else ''}")


FIX = json.load(open("tests_fixtures/kaufcheck_rc1_bmw_330i.json", encoding="utf-8"))
E = FIX["eingabe"]
HEUTE = dt.date(2026, 9, 21)

REQ = KaufCheckRequest(
    marke=E["marke"], modell=E["modell"], baujahr=E["baujahr"],
    kilometerstand=E["kilometerstand"], motor=E["motor"], preis_eur=E["preis"],
    ausstattung=[a.strip() for a in E["ausstattung"].split(",")],
    beschreibung=E["beschreibung"], unfallfrei=E["unfallfrei"],
    vorbesitzer=E["vorbesitzer"], tuev_bis=E["tuevBis"], scheckheftgepflegt=E["scheckheft"],
)


async def _fake_gemini(system, user_msg):
    _fake_gemini.user_msg = user_msg
    _fake_gemini.system = system
    return json.loads(json.dumps(FIX["llm"]))


async def _fake_tavily(queries, count=8, exclude_domains=None, **kw):
    return [dict(r) for r in FIX["web"]]


async def _fake_vertiefen(roh, deep, ziel, preis, excl, **kw):
    ma = kc.analysiere_markt(roh, ziel, preis)
    return roh, ma, {"research_failure_grund": "data_exhausted"}


async def _kein_web_fallback(*a, **k):
    return None


kc.call_gemini_json = _fake_gemini
kc.tavily_search_with_fallback = _fake_tavily
kc.vertiefe_marktrecherche = _fake_vertiefen
kc.recherchiere_technisch = _kein_web_fallback
kc.TAVILY_API_KEY = "test"
kc._heute = lambda: HEUTE

ERG = asyncio.run(kc.run_kaufcheck(REQ))


def _alle_aktionen(bereich: str) -> list:
    pl = ERG["kaufaktionen"][bereich] if isinstance(ERG["kaufaktionen"], dict) \
        else getattr(ERG["kaufaktionen"], bereich)
    pl = pl if isinstance(pl, dict) else pl.model_dump()
    return pl["fahrzeugspezifisch"] + pl["basis"]


def _dump(x):
    return x.model_dump() if hasattr(x, "model_dump") else x


# ══ A/B: HU ══════════════════════════════════════════════════════════════════
def test_hu():
    print("\n[A] HU 09/2028 im September 2026 ist plausibel und sauber formatiert")
    check("Eingabe '092028' wird zu '09/2028' normalisiert", REQ.tuev_bis == "09/2028", REQ.tuev_bis)
    hu = ERG["hu_pruefung"]
    check("Deterministischer Status 'plausibel'", hu and hu["status"] == PLAUSIBEL, str(hu))
    check("24 Monate bis zur Fälligkeit", hu and hu["monate_bis_faellig"] == 24, str(hu))
    b = ERG["bericht"]
    for verboten in ("Tippfehler", "unmöglich", "unplausible TÜV", "092028", "Maximal 2 Jahre ab Prüfung"):
        check(f"Bericht enthält nicht '{verboten}'", verboten not in b)
    check("Tabellenzeile TÜV zeigt 09/2028 und ✓ Plausibel",
          "| TÜV-Gültigkeit | 09/2028 |" in b and "✓ Plausibel |" in b.split("TÜV-Gültigkeit")[1][:160])
    titel = [a["titel"] for a in map(_dump, _alle_aktionen("dokumente"))]
    check("Dokumenten-Checkliste nennt 09/2028, nie 092028",
          any("09/2028" in t for t in titel) and not any("092028" in t for t in titel), str(titel[:4]))
    check("Prompt trägt das heutige Datum", "HEUTIGES DATUM: 09/2026" in _fake_gemini.user_msg)
    check("Prompt trägt die verbindliche HU-Bewertung",
          "Bewertung: plausibel" in _fake_gemini.user_msg and "NICHT neu" in _fake_gemini.user_msg)

    print("\n[B] Alte und extrem ferne HU werden erkannt — ohne Jahreszahl im Code")
    check("05/2025 ist abgelaufen", bewerte_hu("05/2025", HEUTE, 2019).status == ABGELAUFEN)
    check("01/2031 ist ungewöhnlich weit", bewerte_hu("01/2031", HEUTE, 2019).status == UNGEWOEHNLICH_WEIT)
    check("Gleiche Angabe ein Jahr später: 09/2028 gilt weiter als plausibel",
          bewerte_hu("09/2028", dt.date(2027, 9, 1), 2019).status == PLAUSIBEL)
    check("Neuwagen: Erst-HU nach 36 Monaten plausibel",
          bewerte_hu("09/2029", HEUTE, 2026).status == PLAUSIBEL)
    check("Bericht-Netz lässt eine abgelaufene HU als Warnung stehen",
          "⚠ Abgelaufen" in bereinige_bericht("| TÜV | 05/2025 | x | ✓ Plausibel |",
                                              bewerte_hu("05/2025", HEUTE, 2019)))


# ══ C: Inseratsangaben ═══════════════════════════════════════════════════════
def test_claims():
    print("\n[C] 'scheckheftgepflegt' wird nicht zu 'lückenlos' verstärkt")
    texte = " ".join(f"{_dump(f)['titel']} {_dump(f)['beschreibung']}" for f in ERG["key_findings"])
    check("Kein 'lückenlos' in den Key Findings", "lückenlos" not in texte.lower(), texte[:200])
    # Die Wartungsangabe heißt seit "KaufCheck Inputs Final" nicht mehr
    # "scheckheftgepflegt", sondern trägt den kanonischen Satz aus
    # app/servicehistorie.py. Geprüft wird weiterhin dasselbe: die Angabe ist als
    # ANGABE DES INSERATS gekennzeichnet und nicht als Befund formuliert.
    check("Als Inseratsangabe gekennzeichnet",
          "Laut Inserat wird eine vollständige Servicehistorie angegeben." in texte,
          texte[:300])
    check("Regel gegen Verstärkung steht im System-Prompt",
          "NICHT \"lückenlose Wartungshistorie\"" in kc._SYSTEM)


# ══ D: Variante ══════════════════════════════════════════════════════════════
def test_variante():
    print("\n[D] Limousine + Hinterradantrieb → G20, 330i ohne xDrive")
    check("Motor: Heckantrieb-330i statt 330i xDrive",
          ERG["motor_erkannt"] == "bmw-3er-g20-g21-330i", ERG["motor_erkannt"])
    ctx = _dump(ERG["fahrzeugkontext"])
    check("Fahrzeugkontext zeigt G20", ctx["generation"] == "G20", ctx["generation"])
    fz = _dump(ERG["kaufaktionen"]["besichtigung"] if isinstance(ERG["kaufaktionen"], dict)
               else ERG["kaufaktionen"].besichtigung)["fahrzeug"]
    check("Checklisten-Kopf zeigt G20, nicht G20/G21", fz == "BMW 3er G20 (2019)", fz)


# ══ E/F: Markt ═══════════════════════════════════════════════════════════════
def test_markt():
    print("\n[E] Die vier echten Fremd-/Fehlertreffer sind keine Marktquellen")
    check("Keine Belege aus den vier Treffern", ERG["belege"] == [], str(ERG["belege"])[:200])
    urls = " ".join(json.dumps(_dump(i), ensure_ascii=False) for i in ERG["insights"])
    for dom in ("suchen.mobile.de", "picclick.de", "helmuts-ul-seiten.de", "leasingmarkt.de"):
        check(f"{dom} taucht nirgends als Quelle auf", dom not in urls)

    print("\n[F] Ohne Marktbasis keine Preiswertung — auch nicht in der Tabelle")
    b = ERG["bericht"]
    check("Keine Stufe 'Selten' in der Preiszeile", "Selten (aber möglich)" not in b)
    check("Preiszeile neutral", "| Preis | 24.900 € | keine belastbare Marktbasis | nicht bewertbar |" in b)
    check("preis_bewertung bleibt 'unbekannt'", ERG["preis_bewertung"] == "unbekannt")
    check("Keine Marktspanne", ERG["marktpreis_min"] is None and ERG["marktpreis_max"] is None)
    check("Research-Status 'completed_no_market'", ERG["research_status"] == "completed_no_market")


# ══ G/H/I: Checklisten, Service ══════════════════════════════════════════════
def test_checklisten():
    print("\n[G] Geräusch ist kein Bauteil")
    fragen = [_dump(a)["titel"] for a in _alle_aktionen("verkaeuferfragen")]
    check("Keine Frage nach dem 'Bauteil „Knarzgeräusche …“'",
          not any("Bauteil „Knarz" in f for f in fragen), str([f for f in fragen if "Knarz" in f]))
    check("Geräusch-Template generisch",
          verkaeuferfrage("Klappergeräusche Fahrwerk", schwachstellen_art("Klappergeräusche Fahrwerk"))
          .startswith("Sind Ihnen Auffälligkeiten"))
    check("Bauteil-Template bleibt für echte Bauteile",
          verkaeuferfrage("Steuerkette", schwachstellen_art("Steuerkette")).startswith("Wurde am Bauteil"))
    besicht = {_dump(a)["titel"]: _dump(a)["aktion"] for a in _alle_aktionen("besichtigung")}
    knarz = [v for k, v in besicht.items() if "Knarz" in k]
    check("Knarz-Besichtigung prüft Geräusche, nicht Sitzverschleiß",
          knarz and "Verschleiß, Risse" not in knarz[0], str(knarz))

    print("\n[H] Keine Peilstab-Pflicht, Getriebe passend")
    alle = " ".join(_dump(a)["aktion"] for b_ in ("besichtigung", "probefahrt")
                    for a in _alle_aktionen(b_))
    check("Kein 'Ölstand am Peilstab' als Pflichthandlung", "Ölstand am Peilstab" not in alle)
    check("Ölstand nach Herstellervorgabe", "nach Herstellervorgabe" in alle)
    check("Getriebe als Automatik erkannt", getriebe_art(REQ, None) == "automatik")
    check("Keine Kupplungsprüfung beim Automatikwagen",
          "Kupplung" not in alle and "Handschaltung" not in alle,
          [s for s in alle.split(".") if "Kupplung" in s or "Handschalt" in s][:2].__str__())

    print("\n[I] Variables Serviceintervall statt starrer Herstellerzahl")
    ctx = _dump(ERG["fahrzeugkontext"])
    check("CBS als Servicesystem erkannt", ctx.get("wartung_system") == "Condition Based Service (CBS)")
    check("Hinweis: DB-Wert ist nur Orientierung", "nur eine Orientierung" in (ctx.get("wartung_oel_hinweis") or ""))
    check("Prompt nennt den Wert nicht als Herstellervorgabe",
          "Herstellerangabe): alle" not in _fake_gemini.user_msg
          and "KEINE starre Herstellervorgabe" in _fake_gemini.user_msg)


# ══ J/M: Empfehlung ══════════════════════════════════════════════════════════
def test_empfehlung():
    print("\n[J] 'Warum diese Empfehlung?' ≠ 'Warum diese Risiken?'")
    e_ids, r_ids = set(ERG["empfehlung_evidence_ids"]), set(ERG["risiko_evidence_ids"])
    check("Keine gemeinsame Evidence", not (e_ids & r_ids), f"{e_ids & r_ids}")
    g = ERG["empfehlung_gruende"]
    check("Empfehlung hat eigene Begründung", len(g) >= 3, str(g))
    check("Begründung nennt die Identität", any("330i" in x and "B48" in x for x in g), str(g[:1]))
    print("\n[M] Technische Empfehlung ohne Preisbasis bestätigt den Preis nicht")
    check("Begründung stellt klar: Preis nicht bewertet",
          any(x.startswith("Preis nicht bewertet") for x in g))
    check("Keine Zeile behauptet einen fairen/guten Preis",
          not any(w in " ".join(g).lower() for w in ("fair", "günstig", "marktgerecht", "guter preis")))


# ══ K/L: Rückrufe ════════════════════════════════════════════════════════════
def test_rueckrufe():
    print("\n[K] Belegte Baureihen-Rückrufe erscheinen nur als 'FIN prüfen'")
    rr = [i for i in map(_dump, ERG["insights"]) if i["kategorie"] == "rueckruf"]
    refs = sorted((q.get("ref") or "") for i in rr for q in i["quellen"])
    check("Genau die drei amtlich belegten Rückrufe", refs == ["10009", "15632R", "9839"], str(refs))
    check("Alle nur baureihenweit (series_only)",
          all(i["applicability"] == "series_only" for i in rr), str([i["applicability"] for i in rr]))
    check("Alle mit FIN-Hinweis", all("FIN" in (i["einfluss"] or "") for i in rr))
    check("Starterrelais-Rückruf (Oktober 2025) ist enthalten",
          any("Starterrelais" in i["beschreibung"] for i in rr))

    print("\n[L] Unbelegte Rückrufe erscheinen nirgends")
    b = ERG["bericht"]
    for alt in ("Bremskraftverstärker", "Schweißnähte an der Lenkung"):
        check(f"'{alt}' nicht in den Insights",
              not any(alt in i["beschreibung"] for i in rr))
        check(f"'{alt}' nicht in den Kaufaktionen",
              not any(alt in _dump(a)["titel"] for bereich in ("dokumente", "verkaeuferfragen")
                      for a in _alle_aktionen(bereich)))
    for alt_ in ("Bremskraftverstärker", "Schweißnähte an der Lenkung"):
        check(f"'{alt_}' auch aus dem (alten) LLM-Bericht entfernt", alt_ not in b)
    check("Gesperrte Rückrufe gehen nicht über /fahrzeug nach außen",
          "rueckrufe_gesperrt" in open("app/routers/fahrzeug.py", encoding="utf-8").read()
          and 'data.pop("rueckrufe_gesperrt"' in open("app/routers/fahrzeug.py", encoding="utf-8").read())
    check("Unbelegter Altrückruf ist gesperrt",
          not rueckruf_ist_belegt({"kba_referenz": None, "mangel": "x"}, "BMW"))
    check("Amtliche Referenz ist belegt", rueckruf_ist_belegt({"kba_referenz": "10009"}, "BMW"))
    check("Einzelverifikation ohne Referenz ist belegt",
          rueckruf_ist_belegt({"kba_referenz": None, "_trust": "verified"}, "BMW"))
    g20 = database.get_baureihe("BMW", "3er", "G20/G21")
    sichtbar = {r["id"] for r in g20["rueckrufe"]}
    check("DB-Lesepunkt blendet #11/#12 aus", not ({11, 12} & sichtbar), str(sichtbar))
    check("PHEV-Rückruf entfällt beim Benziner",
          all("Hochvolt" not in r["mangel"] for r in gefilterte_rueckrufe(
              g20["rueckrufe"], {"kraftstoff": "Benzin"}, 2019, marke="BMW")))


# ══ Datenqualität ════════════════════════════════════════════════════════════
def test_datenqualitaet():
    print("\n[N] Datenqualität folgt der Beleglage, Einzelberichte sind keine 'bekannte Schwachstelle'")
    sw = [i for i in map(_dump, ERG["insights"]) if i["kategorie"] == "schwachstelle"]
    sw_titel = {i["titel"]: i for i in sw}
    soft = next((i for t, i in sw_titel.items() if t.startswith("Software")), None)
    knarz = next((i for t, i in sw_titel.items() if t.startswith("Knarz")), None)
    check("Software: 'vereinzelt' → gemeldeter Hinweis, nicht bekannte Schwachstelle",
          soft and soft["titel"].endswith("gemeldeter Hinweis"), soft and soft["titel"])
    check("Knarzgeräusche (unverifiziert): Datenqualität niedrig",
          knarz and knarz["confidence"] == "niedrig", knarz and knarz["confidence"])
    check("Software (verifiziert): Datenqualität nicht niedrig",
          soft and soft["confidence"] in ("hoch", "mittel"), soft and soft["confidence"])


def test_quellen_einzeln():
    print("\n[O] Quellentor einzeln: Fehler-, Teile-, Leasing- und Fremdseiten")
    ziel = {"modell_tokens": {"3er", "330i", "320d", "xdrive"}}
    f = lambda t, u, c: ablehnungsgrund({"title": t, "url": u, "content": c}, ziel, "BMW")
    check("Access denied", f("Zugriff verweigert / Access denied", "https://m.de", "x" * 100) == "fehlerseite")
    check("Teileangebot", f("Autoteile Federung", "https://picclick.de/Auto-Motorrad-Teile/x", "x" * 100) == "teileangebot")
    check("Leasing", f("BMW 330i Leasing", "https://leasingmarkt.de/x", "x" * 100) == "leasing")
    check("Fremdes Modell trotz 'xDrive'", f("BMW X3 xDrive30i", "https://x.de", "BMW X3 xDrive30i 2019 32.900 €") == "modell_fehlt")
    check("Echtes 330i-Inserat bleibt",
          f("BMW 330i Limousine 2019", "https://autoscout24.de/a", "BMW 330i, 2019, 88.000 km, 26.490 €") is None)
    check("Preiszeile-Neutralisierung greift auch isoliert",
          "nicht bewertbar" in neutralisiere_preiszeile_ohne_markt("| Preis | 9.000 € | – | ⚠ Selten |"))



# ══ Closing-Pass: Datenqualität, Rückruf-Kurztitel, Motorcode, Textstil ══════
def test_closing():
    from app.rueckruf_titel import rueckruf_kurztitel, MAX_TITEL
    from app.kba_g20_nachtrag_daten import ZEILEN
    from app.empfehlung_gruende import baue_empfehlung_gruende
    from app.pruefplan_basis import (BASIS_BESICHTIGUNG, BASIS_PROBEFAHRT,
                                     BASIS_VERKAEUFERFRAGEN, BASIS_DOKUMENTE)

    print("\n[A/B] Software/Infotainment: verifizierte Einzelaussage ist nicht 'hoch'")
    soft = next(i for i in map(_dump, ERG["insights"])
                if i["kategorie"] == "schwachstelle" and i["titel"].startswith("Software"))
    check("Software/Infotainment: Datenqualität 'mittel' statt 'hoch'",
          soft["confidence"] == "mittel", soft["confidence"])

    print("\n[C] Rückruf-Kurztitel: kurz, verständlich, ohne abgeschnittene Halbsätze")
    erwartet = {"10009": "Spurstange: Bruchgefahr",
                "9839": "Gurtschloss: fehlerhafte Airbag- und Gurtstraffer-Auslösung",
                "15632R": "Starterrelais: Brandgefahr"}
    for z in ZEILEN:
        check(f"KBA {z['kba_referenz']}: '{erwartet[z['kba_referenz']]}'",
              rueckruf_kurztitel(z["mangel"]) == erwartet[z["kba_referenz"]],
              rueckruf_kurztitel(z["mangel"]))
    rr = [i for i in map(_dump, ERG["insights"]) if i["kategorie"] == "rueckruf"]
    check("Insight-Titel beginnt mit dem Kurztitel, Einstufung in Klammern",
          all(i["titel"] == f"{i['kurztitel']} (KBA-Rückruf, Baureihe)" for i in rr),
          str([i["titel"] for i in rr]))
    with sqlite3.connect(_DB) as c_:
        alle = [r[0] for r in c_.execute(
            "select mangel from rueckruf where kba_referenz is not null and trim(kba_referenz)<>''")]
    titel = [rueckruf_kurztitel(m) for m in alle]
    check(f"Alle {len(alle)} belegten Rückrufe: Titel <= {MAX_TITEL} Zeichen",
          all(len(t) <= MAX_TITEL for t in titel), str(max(titel, key=len)))
    check("Kein Kurztitel endet mit '…'", not any("…" in t for t in titel))
    check("Kein Kurztitel enthält einen Gedankenstrich", not any("—" in t for t in titel))
    alle_texte = " ".join(
        [f["beschreibung"] or "" for f in map(_dump, ERG["key_findings"])]
        + [_dump(a)["titel"] for b_ in ("verkaeuferfragen", "dokumente") for a in _alle_aktionen(b_)])
    check("Findings und Checklisten nutzen die Kurztitel, keine abgeschnittenen Halbsätze",
          "Spurstange: Bruchgefahr" in alle_texte and "Belastunge…" not in alle_texte
          and "Aufgrund fehlerhafter Auslegung" not in alle_texte)
    check("Kein Fahrzeug-Sonderfall im Titelmodul",
          not any(w in open("app/rueckruf_titel.py", encoding="utf-8").read().lower()
                  for w in ("bmw", "g20", "330i", "15632", "10009", "9839")))

    print("\n[D/H] Amtliche Originalbeschreibung bleibt vollständig und unverändert")
    for z in ZEILEN:
        treffer = [i for i in rr if (q := i["quellen"]) and q[0].get("ref") == z["kba_referenz"]]
        check(f"KBA {z['kba_referenz']}: voller amtlicher Mangeltext in der Detailbeschreibung",
              treffer and treffer[0]["beschreibung"].startswith(z["mangel"]),
              treffer and treffer[0]["beschreibung"][:80])
        check(f"KBA {z['kba_referenz']}: amtliche Abhilfe unverändert enthalten",
              treffer and z["abhilfe"] in treffer[0]["beschreibung"])
    with sqlite3.connect(_DB) as c_:
        db_mangel = {r[0]: r[1] for r in c_.execute(
            "select kba_referenz, mangel from rueckruf where id in (4033,4034,4035)")}
    check("Amtliche Texte in der DB durch die Stilregel nicht verändert",
          all(db_mangel[z["kba_referenz"]] == z["mangel"] for z in ZEILEN))
    check("Amtlicher Gedankenstrich im Originaltext bliebe erhalten",
          rueckruf_kurztitel("Kurz — amtlich") == "Kurz — amtlich")

    print("\n[E/F] Motorcode: konkret bleibt konkret, nichts wird erfunden")
    check("Replay: angezeigter Code ist der gespeicherte B48B20",
          any("(B48B20, 258 PS)" in g for g in ERG["empfehlung_gruende"]), str(ERG["empfehlung_gruende"][:1]))
    check("Replay: keine künstliche Präzisierung auf B48B20B",
          not any("B48B20B" in g for g in ERG["empfehlung_gruende"]))
    br = {"marke": "BMW", "modell": "3er", "generation": "G20"}
    konkret = baue_empfehlung_gruende(REQ, br, {"bezeichnung": "320i", "motorcode": "B48A20M1",
                                                "leistung_ps": 184}, [], [], "kaufen", False, None)
    check("Konkreter Variantencode wird nicht auf die Motorfamilie reduziert",
          "(B48A20M1, 184 PS)" in konkret[0], konkret[0])
    familie = baue_empfehlung_gruende(REQ, br, {"bezeichnung": "320i", "motorcode": "B48",
                                                "leistung_ps": 184}, [], [], "kaufen", False, None)
    check("Nur Motorfamilie bekannt: bleibt 'B48'", "(B48, 184 PS)" in familie[0], familie[0])
    check("Prompt verlangt den Motorcode exakt wie in der DB",
          "Motorcode exakt so, wie er im DB-Kontext steht" in kc._SYSTEM)

    print("\n[G] Von ENFAL erzeugte Texte ohne gehäufte Gedankenstriche")
    erzeugt = []
    for b_ in ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente"):
        for a in map(_dump, _alle_aktionen(b_)):
            erzeugt += [a["titel"], a["aktion"], a.get("hinweis") or ""]
    erzeugt += [f"{_dump(f)['titel']} {_dump(f)['beschreibung'] or ''} {_dump(f).get('aktion') or ''}"
                for f in ERG["key_findings"]]
    erzeugt += ERG["empfehlung_gruende"] + [ERG["hu_pruefung"]["hinweis"]]
    erzeugt += [f"{i['titel']} {i.get('einfluss') or ''}" for i in map(_dump, ERG["insights"])]
    ctx = _dump(ERG["fahrzeugkontext"])
    erzeugt += [ctx.get("wartung_oel_hinweis") or ""]
    mit_strich = [t for t in erzeugt if "—" in t]
    check("Kein Gedankenstrich in Checklisten, Findings, Begründungen, Titeln, Hinweisen",
          not mit_strich, str(mit_strich[:3]))
    katalog = [t for kat in (BASIS_BESICHTIGUNG, BASIS_PROBEFAHRT, BASIS_VERKAEUFERFRAGEN,
                             BASIS_DOKUMENTE) for e in kat for t in (e[2], e[3], e[4] or "")]
    check("Basis-Katalog frei von Gedankenstrichen",
          not [t for t in katalog if "—" in t], str([t for t in katalog if "—" in t][:2]))
    # Die Regel liegt jetzt zentral in app/schreibstil.py und gilt fuer alle
    # generierenden Oberflaechen. Geprueft wird deshalb, dass GENAU diese
    # Fassung im Prompt steht, nicht mehr der frueher lokale Wortlaut
    # ("Gedankenstriche sparsam") -- sonst faellt ein Prompt still zurueck.
    from app.schreibstil import STILREGEL_GEDANKENSTRICHE
    check("Zentrale Stilregel steht im KaufCheck-Prompt",
          STILREGEL_GEDANKENSTRICHE in kc._SYSTEM)

try:
    test_hu()
    test_claims()
    test_variante()
    test_markt()
    test_checklisten()
    test_empfehlung()
    test_rueckrufe()
    test_datenqualitaet()
    test_quellen_einzeln()
    test_closing()
finally:
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n{PASS} PASS / {len(FEHLER)} FAIL")
if FEHLER:
    for f_ in FEHLER:
        print(" -", f_)
    raise SystemExit(1)
print("ALLE KAUFCHECK-RC1-TESTS GRUEN")

if os.environ.get("KC_RC1_ZEIGEN"):
    print("\n===== BERICHT =====\n" + ERG["bericht"])
    print("\n===== EMPFEHLUNG_GRUENDE =====")
    for g_ in ERG["empfehlung_gruende"]:
        print(" -", g_)

if os.environ.get("KC_RC1_TITEL"):
    for i in map(_dump, ERG["insights"]):
        if i["kategorie"] == "rueckruf":
            print("TITEL:", i["titel"], "| KURZ:", i["kurztitel"])
    for f in map(_dump, ERG["key_findings"]):
        print("KF:", f["titel"], "|", f["beschreibung"])
    for b_ in ("verkaeuferfragen", "dokumente"):
        for a in map(_dump, _alle_aktionen(b_)):
            if "Rückruf" in a["titel"]:
                print(b_.upper()+":", a["titel"])
