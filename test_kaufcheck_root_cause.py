"""
Test: KaufCheck Root-Cause-Closing (Production-Run BMW M4 F82).

Nicht der M4 wird geprüft, sondern die FEHLERKLASSEN dahinter, und zwar für
beliebige Fahrzeuge. Deshalb zwei Ebenen:

  1. INVARIANTEN über den GESAMTEN Seed-Bestand: jede Baureihe mit jeder
     Motorvariante läuft durch die deterministische Pipeline (Insights,
     kanonische Risikomenge, Prüfplan, Key Findings, Empfehlungsgründe).
  2. FAHRZEUGFÄLLE mit vollem `run_kaufcheck` und einer Gemini-Attrappe, die
     absichtlich die Fehler des echten Berichts schreibt:
       A  BMW M4 F82       der reproduzierte Problemfall
       B  BMW 330i G20     frühere Fixes dürfen nicht regressieren
       C  Audi A4 B8 1.8 TFSI / Mercedes C200 W205   Nicht-BMW, andere Datenlage
       D  Audi A1 GB 25 TFSI   Fahrzeug ohne hinterlegte Schwachstelle
       E  Low-Evidence bleibt Low
       F  bekannte Wartungsangabe ("81.000 km")
       G  interne Motorbauteile
       H  Oberflächen-/Materialthemen
       I  Vergleichstabelle mit Herkunft

Invarianten (Nummern wie im Auftrag):
   1  bekannte letzte Wartung -> keine Frage, die sie als unbekannt behandelt
   2  ein Thema erscheint nach der Normalisierung nur einmal
   3  Low-Evidence eskaliert nicht zu High-Evidence
   4  interne Bauteile bekommen keine unmögliche Sicht-/Leckageprüfung
   5  Oberflächenthemen bekommen eine passende Sichtprüfung
   6  kein Nicht-Marktwert erscheint als "Markterwartung"
   7  ohne Referenz keine Plausibilitätsampel
   8  Summary und Detail nutzen dieselbe Risikomenge
   9  keine rhetorischen Gedankenstriche in Nutzertexten
  10  bekannte Fakten überleben jede Obergrenze
  11  Rückrufimport entscheidet je (Rückruf, Baureihe)
  12  FIN-Safety bleibt erhalten

KEIN Gemini, KEIN Tavily, KEIN CarAPI, KEIN Stripe, KEINE Mail. Die Datenbank
ist eine frisch aus dem Repo-Seed gebootstrappte Kopie.

Ausführen:  python test_kaufcheck_root_cause.py
"""
import asyncio
import datetime as dt
import io
import os
import re
import sys
import tempfile
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

_TMP = tempfile.mkdtemp(prefix="enfal_rootcause_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_TMP, "chroma")
os.environ["GEMINI_API_KEY"] = ""
os.environ["TAVILY_API_KEY"] = ""
os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"

import app.database as db  # noqa: E402
db.ensure_tables()

import app.car_lookup as cl  # noqa: E402
import app.kaufcheck as kc  # noqa: E402
import app.web_search as ws  # noqa: E402
from app.bekannte_fakten import aus_request as fakten_aus_request, unbekannt_fragen  # noqa: E402
from app.empfehlung_gruende import baue_empfehlung_gruende  # noqa: E402
from app.evidence import build_insights  # noqa: E402
from app.fahrzeugkontext import build_fahrzeugkontext  # noqa: E402
from app.kaufaktionen import (  # noqa: E402
    KLASSE_INTERN, KLASSE_OBERFLAECHE, KLASSE_UNBEKANNT, _besichtigung, _komponente,
    build_kaufaktionen, pruefklasse,
)
from app.key_findings import build_key_findings_kauf  # noqa: E402
from app.laufleistung import build_laufleistungskontext  # noqa: E402
from app.models import Insight, KaufCheckRequest, KaufCheckResponse, PriceAssessment  # noqa: E402
from app.recall_filter import _baujahr_passt  # noqa: E402
from app.risikothemen import (  # noqa: E402
    RISIKO_KATEGORIEN, WARTUNG_MODIFIKATION, WARTUNG_REGULAER, WARTUNG_VORBEUGEND,
    WARTUNG_ZUSTAND, bauteil_kern, kanonisiere, risikoart, wartungsart,
)
from app.schreibstil import RE_RHETORISCH, bereinige_nutzertexte, entferne_gedankenstriche  # noqa: E402
from app.vergleichstabelle import (  # noqa: E402
    HERKUNFT_MARKT, NICHT_BEWERTBAR, als_markdown, baue_zeilen,
)

FEHLER: list[str] = []
PASS = 0


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"[OK] {name}")
    else:
        FEHLER.append(name)
        print(f"[FEHLER] {name}")
        if detail:
            print(f"        {str(detail)[:400]}")


# ── Provider-Wache: jeder echte Aufruf wird gezählt und scheitert ───────────
PROVIDER = {"gemini": 0, "tavily": 0}


async def _kein_gemini(*a, **k):
    PROVIDER["gemini"] += 1
    raise RuntimeError("Gemini darf in diesem Test nicht aufgerufen werden")


async def _kein_tavily(*a, **k):
    PROVIDER["tavily"] += 1
    raise RuntimeError("Tavily darf in diesem Test nicht aufgerufen werden")


cl.call_gemini_json = _kein_gemini
ws._tavily_search_intern = _kein_tavily
kc._heute = lambda: dt.date(2026, 9, 27)


def _risiken(insights):
    return [i for i in insights if i.kategorie in RISIKO_KATEGORIEN]


def _alle_aktionen(ka):
    return [a for b in ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")
            for a in getattr(ka, b).fahrzeugspezifisch + getattr(ka, b).basis]


def _texte_aktionen(aktionen):
    return "\n".join(f"{a.titel}\n{a.aktion}\n{a.hinweis or ''}" for a in aktionen)


def deterministisch(req):
    """Die deterministische Pipeline genau wie in `run_kaufcheck`."""
    baureihe, ident = cl.find_baureihe_mit_vertrauen(req.marke, req.modell, req.baujahr)
    motor = cl.find_motor(baureihe, req.motor, req.modell) if baureihe else None
    if not ident.get("belastbar", True):
        baureihe, motor = None, None
    insights = build_insights(baureihe, motor, [], req, check_typ="kauf")
    ll = build_laufleistungskontext(req, insights, heute_jahr=2026)
    ka = build_kaufaktionen(req, baureihe, motor, insights, laufleistungskontext=ll)
    kf = build_key_findings_kauf(req, baureihe, motor, insights, None, identitaet=ident)
    return dict(baureihe=baureihe, motor=motor, insights=insights, ll=ll, ka=ka, kf=kf)


def voll(req, bericht, empfehlung="nur_mit_werkstattpruefung", evidence=None):
    """Voller `run_kaufcheck` mit Gemini-Attrappe; gibt Ergebnis und Prompt zurück."""
    prompt = {}

    async def _fake(system, user):
        prompt["system"], prompt["user"] = system, user
        return {"bericht": bericht, "empfehlung": empfehlung, "preis_bewertung": "unbekannt",
                "marktpreis_min": None, "marktpreis_max": None,
                "empfehlung_evidence_ids": list(evidence or []), "preis_evidence_ids": [],
                "risiko_evidence_ids": list(evidence or [])}
    kc.call_gemini_json = _fake
    try:
        erg = asyncio.run(kc.run_kaufcheck(req))
    finally:
        kc.call_gemini_json = _kein_gemini
    return erg, prompt


def nutzertexte(erg) -> list[str]:
    """Alle Nutzertexte eines Ergebnisses (ohne Quellen fremder Seiten)."""
    out: list[str] = []

    def _sammle(x, schluessel=None):
        if schluessel in ("belege", "quellen", "web_identitaet", "marktanalyse"):
            return
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            for k, v in x.items():
                _sammle(v, k)
        elif isinstance(x, list):
            for v in x:
                _sammle(v, schluessel)
        elif hasattr(x, "model_dump"):
            _sammle(x.model_dump(), schluessel)
    _sammle(erg)
    return out


# Ein Bericht wie im echten M4-Lauf: Gedankenstriche, erfundene Markterwartungen,
# eine Frage nach der bekannten Wartung und eine Fälligkeit aus einem
# ungeprüften Hinweis.
def fehlerbericht(fahrzeug, bauteil_a, bauteil_b):
    return f"""## Fahrzeug erkannt
{fahrzeug}.

## Kaufempfehlung
**NUR MIT WERKSTATTPRÜFUNG**
Die Laufleistung ist sensibel — {bauteil_a} und {bauteil_b} werden jetzt fällig.

## Kritische Risiken
- **{bauteil_a}** — vorbeugender Wechsel empfohlen.

## Preis-Einschätzung
Keine belastbare Marktbasis.

## Inserat im Vergleich
| Kriterium | Inserat-Angabe | DB-/Markterwartung | Plausibilität |
|---|---|---|---|
| Vorbesitzer | 3 | 1-3 Vorbesitzer | ✓ Plausibel |
| Servicehistorie | teilweise vorhanden | digital / Condition Based Service | ⚠ Selten (aber möglich) |
| Kilometerstand | 69.500 km | ~7.000 km/Jahr | ✓ Plausibel |

## Besichtigungs-Checkliste
- [ ] Wann war die letzte Wartung, und was wurde dabei gemacht?
- [ ] Wie viele Vorbesitzer hatte das Fahrzeug?
"""


# ══ Die Fahrzeuge ════════════════════════════════════════════════════════════
M4 = KaufCheckRequest(
    marke="BMW", modell="M4", baujahr=2016, kilometerstand=69500, motor="3.0 Biturbo 431 PS",
    kraftstoff="benzin", leistung_ps=431, getriebe="manuell", preis_eur=43500,
    verkaeuferart="privat", vorbesitzer=3, tuev_bis="05/2027", servicehistorie="teilweise",
    beschreibung=("BMW M4 Coupé, 3.0 Biturbo, 431 PS, Hinterradantrieb, 6-Gang manuell. "
                  "Dritter Halter. Letzte Wartung laut Verkäufer bei ca. 64.000 km durchgeführt. "
                  "Servicehistorie teilweise vorhanden, HU bis 05/2027."))
G20 = KaufCheckRequest(
    marke="BMW", modell="330i", baujahr=2019, kilometerstand=78500, motor="330i",
    kraftstoff="benzin", leistung_ps=258, getriebe="automatik", preis_eur=26900,
    verkaeuferart="haendler", servicehistorie="vollstaendig_angegeben", vorbesitzer=2,
    tuev_bis="08/2027",
    beschreibung=("BMW 330i Limousine, Hinterradantrieb, Steptronic Automatik. "
                  "Zweiter Halter, Fahrzeug aus erster Hand des Autohauses übernommen. "
                  "Letzte Wartung laut Verkäufer bei ca. 72.000 km durchgeführt. "
                  "Servicehistorie vollständig, HU neu bis 08/2027."))
A4 = KaufCheckRequest(
    marke="Audi", modell="A4", baujahr=2011, kilometerstand=140000, motor="1.8 TFSI (120 PS)",
    kraftstoff="benzin", leistung_ps=120, getriebe="automatik", preis_eur=9900,
    verkaeuferart="haendler", vorbesitzer=2, tuev_bis="03/2027", servicehistorie="teilweise",
    unfallfrei="ja",
    beschreibung="Audi A4 Avant 1.8 TFSI multitronic. Letzte Wartung bei 81.000 km, Zahnriemen neu.")
C200 = KaufCheckRequest(
    marke="Mercedes-Benz", modell="C-Klasse", baujahr=2016, kilometerstand=98000, motor="C200",
    kraftstoff="benzin", preis_eur=19900, verkaeuferart="privat", vorbesitzer=1,
    servicehistorie="vollstaendig_angegeben",
    beschreibung="C200 Limousine, scheckheftgepflegt, Inspektion 04/2025 bei 91.000 km.")
A1 = KaufCheckRequest(
    marke="Audi", modell="A1", baujahr=2020, kilometerstand=42000, motor="25 TFSI",
    kraftstoff="benzin", preis_eur=15900, verkaeuferart="haendler", vorbesitzer=1,
    servicehistorie="vollstaendig_angegeben")


# ══ A) BMW M4 F82 ════════════════════════════════════════════════════════════
print("\n=== A) BMW M4 F82: der reproduzierte Problemfall ===")
dm4 = deterministisch(M4)
erg_m4, prompt_m4 = voll(M4, fehlerbericht("BMW M4 F82", "Pleuellager", "Kurbelnabe"),
                         evidence=[i.id for i in dm4["insights"]])
motor_m4 = dm4["motor"] or {}
check("A1 F82 erkannt", (dm4["baureihe"] or {}).get("id") == "bmw-m4-f82", dm4["baureihe"])
check("A2 S55B30 mit 431 PS erkannt",
      motor_m4.get("motorcode") == "S55B30" and motor_m4.get("leistung_ps") == 431, motor_m4)
check("A3 Benzin und Hinterradantrieb aus der Variante",
      "benzin" in (motor_m4.get("kraftstoff") or "").lower()
      and (motor_m4.get("antrieb") or "").lower().startswith("heck"))
probefahrt = _texte_aktionen(dm4["ka"].probefahrt.basis)
check("A4 Schaltgetriebe: Kupplungsprüfung statt Fahrstufen",
      "Kupplung" in probefahrt and "Fahrstufe" not in probefahrt, probefahrt[:300])
dok = _texte_aktionen(dm4["ka"].dokumente.fahrzeugspezifisch + dm4["ka"].dokumente.basis)
check("A5 Privatverkauf: Vollmacht bei abweichendem Halter", "Vollmacht" in dok)
check("A6 3 Vorbesitzer im Teil-II-Punkt", "3 Vorbesitzer" in dok)
check("A7 Wartung bei 64.000 km extrahiert",
      fakten_aus_request(M4).letzte_wartung is not None
      and fakten_aus_request(M4).letzte_wartung.km == 64000)
check("A8 Servicehistorie 'teilweise' greift",
      any(a.id == "dokument-basis-serviceheft" and "teilweise" in a.aktion
          for a in dm4["ka"].dokumente.fahrzeugspezifisch + dm4["ka"].dokumente.basis))
check("A9 kein FIN-/VIN-Feld im Request",
      not any(f in KaufCheckRequest.model_fields for f in ("fin", "vin", "fahrgestellnummer")))
check("A10 ohne Marktdaten keine Preisspanne",
      erg_m4["marktpreis_min"] is None and erg_m4["marktpreis_max"] is None)

# Befund A: bekannte Wartung, trotz 6er-Limit
fragen_m4 = _texte_aktionen(dm4["ka"].verkaeuferfragen.fahrzeugspezifisch
                            + dm4["ka"].verkaeuferfragen.basis)
check("A11 die Verkäuferfragen sind voll (Limit greift)",
      len(dm4["ka"].verkaeuferfragen.fahrzeugspezifisch) == 6)
check("A12 keine Frage 'Wann war die letzte Wartung' im Prüfplan",
      "Wann war die letzte Wartung" not in fragen_m4, fragen_m4[:500])
check("A13 stattdessen die geschärfte Frage mit 64.000 km",
      "Was umfasste die im Inserat genannte Wartung bei rund 64.000 km?" in fragen_m4)
check("A14 auch im Bericht des Modells ersetzt",
      "Wann war die letzte Wartung" not in erg_m4["bericht"]
      and "bei rund 64.000 km" in erg_m4["bericht"])

# Befund C/E: ein Thema, ehrliche Evidenz
pleuel = [i for i in _risiken(erg_m4["insights"]) if "pleuellager" in (i.bauteil or "").lower()]
check("A15 Pleuellager erscheint genau einmal", len(pleuel) == 1, [i.titel for i in pleuel])
check("A16 und bleibt 'niedrig' belegt",
      pleuel and pleuel[0].confidence == "niedrig", pleuel and pleuel[0].confidence)
check("A17 beide Datensätze bleiben nachvollziehbar",
      pleuel and pleuel[0].zusammengefuehrt and "diskutiert" in pleuel[0].beschreibung
      and "Weiterer Datensatz" in pleuel[0].beschreibung)
check("A18 kein 'kritischer Wartungspunkt' mehr",
      not any("kritischer Wartungspunkt" in i.titel for i in erg_m4["insights"]))
check("A19 kein ungeprüfter Wartungseintrag mit Datenqualität hoch",
      not any(i.kategorie == "wartung" and i.trust != "verified" and i.confidence != "niedrig"
              for i in erg_m4["insights"]))
kurbel = [i for i in _risiken(erg_m4["insights"]) if "kurbelnabe" in (i.bauteil or "").lower()]
check("A20 Kurbelnabe: das eigentliche Risiko ist für 2016 sichtbar ('v.a. 2014-2015')",
      len(kurbel) == 1 and kurbel[0].kategorie == "motorproblem",
      [(i.kategorie, i.titel) for i in kurbel])

# Befund D/F/B
bes_m4 = {a.titel: a.aktion for a in dm4["ka"].besichtigung.fahrzeugspezifisch}
check("A21 Pleuellager: keine Sichtprüfung, sondern Symptome und Fachwerkstatt",
      "Pleuellager" in bes_m4 and "nicht einsehbar" in bes_m4["Pleuellager"]
      and "Leckagen" not in bes_m4["Pleuellager"], bes_m4.get("Pleuellager"))
alle_m4 = _texte_aktionen(_alle_aktionen(dm4["ka"]))
check("A22 kein 'Wartungsnachweis Kurbelnabe', keine 'letzte Durchführung'",
      "Wartungsnachweis Kurbelnabe" not in alle_m4 and "letzte Durchführung" not in alle_m4)
check("A23 CFK-Dach: Oberflächenprüfung statt Öffnungsprüfung",
      "CFK-Dach Klarlack" in bes_m4 and "Ablösungen" in bes_m4["CFK-Dach Klarlack"]
      and "öffnen und schließen" not in bes_m4["CFK-Dach Klarlack"], bes_m4.get("CFK-Dach Klarlack"))
check("A24 EDC-Dämpfer und Hinterachsträger-Buchse landen auf demselben "
      "Fahrwerks-Schlüssel (Grundlage der Zusammenlegung)",
      _komponente("EDC-Dämpfer")["schluessel"] == "fahrwerk"
      and _komponente("Hinterachsträger-Buchse")["schluessel"] == "fahrwerk")
# Root-Cause-Closing (KBA-Paar-Closing): der F82 hat inzwischen 5 zusaetzliche,
# echte amtliche Rueckrufe (vorher fuer sich sicher, aber verloren, siehe
# app/kba_import_batch_c.py). Deren Verkaeuferfragen sind sicherheitsrelevant
# und rangieren vor der zusammengelegten Fahrwerksfrage — das Limit
# MAX_SPEZIFISCH_PRO_BEREICH verdraengt sie deshalb jetzt aus der sichtbaren
# Liste. Das ist die bekannte, dokumentierte Cap-Falle (siehe
# project_enfal_kaufcheck_inputs), keine Regression der Zusammenlegung selbst
# (siehe A24 oben): mehr echte Sicherheitsrueckrufe verdraengen zu Recht eine
# einzelne Verschleissfrage.
check("A24b die drei hoechstrangigen Rueckrufe verdraengen die Fahrwerksfrage "
      "aus den sichtbaren sechs Verkaeuferfragen (erwartete Cap-Folge)",
      sum(1 for a in dm4["ka"].verkaeuferfragen.fahrzeugspezifisch
          if a.id.startswith("frage-rueckruf")) == 3
      and "EDC-Dämpfer" not in fragen_m4)

# Befund J/4.4
gruende_m4 = " ".join(erg_m4["empfehlung_gruende"])
check("A25 keine 'keine schwerwiegende Schwachstelle' neben Motorrisiken",
      "keine schwerwiegende" not in gruende_m4, gruende_m4)
check("A26 die Zusammenfassung nennt die niedrige Datenqualität und behauptet keinen Mangel",
      "Datenqualität niedrig" in gruende_m4 and "kein festgestellter Mangel" in gruende_m4,
      gruende_m4)
kf_titel = [f.titel for f in erg_m4["key_findings"]]
check("A27 kein 'Bekanntes Motorproblem' für ungeprüfte Hinweise",
      "Bekanntes Motorproblem" not in kf_titel and any("gemeldete Hinweise" in t for t in kf_titel),
      kf_titel)
check("A28 ungeprüfte Hinweise sind kein Beleg der Empfehlung",
      not any(e in {i.id for i in erg_m4["insights"] if i.confidence == "niedrig"}
              for e in erg_m4["empfehlung_evidence_ids"]), erg_m4["empfehlung_evidence_ids"])
check("A29 Fälligkeit aus ungeprüftem Hinweis wird neutralisiert",
      "werden jetzt fällig" not in erg_m4["bericht"] and "prüfen" in erg_m4["bericht"])

# Befund 4.5: Prompt
user = prompt_m4["user"]
check("A30 Prompt: kanonischer Block mit Beleglage",
      '"canonical_risks"' in user and '"confidence"' in user)
check("A31 Prompt: keine Rohliste 'Kritische Wartung:' und kein '(DB, geprüft)'",
      "Kritische Wartung:" not in user and "DB, geprüft" not in user)
check("A32 Systemprompt nennt das DB-Profil nicht mehr 'geprüfte Fakten'",
      "geprüfte Fakten (Schwachstellen" not in prompt_m4["system"])

# Befund G/H/I
tabelle = erg_m4["bericht"].split("## Inserat im Vergleich", 1)[1].split("\n## ", 1)[0]
check("A33 keine 'Markterwartung' ohne Marktdaten", "Markterwartung" not in erg_m4["bericht"])
check("A34 Vorbesitzer ohne erfundene Verteilung",
      re.search(r"\| Vorbesitzer \| 3 \| keine Vergleichsdaten \| nicht bewertbar \|", tabelle)
      is not None and "1-3" not in tabelle, tabelle)
check("A35 Servicehistorie wird nicht gegen 'Condition Based Service' gewertet",
      "Condition Based" not in tabelle and "Selten" not in tabelle, tabelle)
check("A36 km/Jahr ist als berechneter Wert dieses Fahrzeugs gekennzeichnet",
      "berechnet rund 7.000 km pro Jahr" in tabelle
      and re.search(r"\| Kilometerstand \|[^|]*\| keine Vergleichsdaten \| nicht bewertbar \|",
                    tabelle) is not None, tabelle)

# Befund K/L
fk = erg_m4["fahrzeugkontext"]
check("A37 kein rhetorischer Gedankenstrich im Fahrzeugprofil",
      not RE_RHETORISCH.search(fk.erkennung_generation or ""), fk.erkennung_generation)
check("A38 F82-LCI: LED-Rückleuchten, OLED nur bei Sondermodellen",
      "LED-Rückleuchten" in (fk.facelift_merkmale or "")
      and "OLED-Rückleuchten gab es nur bei Sondermodellen" in (fk.facelift_merkmale or ""),
      fk.facelift_merkmale)
check("A39 kein rhetorischer Gedankenstrich irgendwo im Ergebnis (Inv. 9)",
      not [t for t in nutzertexte(erg_m4) if RE_RHETORISCH.search(t)],
      [t[:120] for t in nutzertexte(erg_m4) if RE_RHETORISCH.search(t)][:3])


# ══ B) BMW 330i G20 ══════════════════════════════════════════════════════════
print("\n=== B) BMW 330i G20: frühere Fixes dürfen nicht regressieren ===")
dg20 = deterministisch(G20)
erg_g20, _ = voll(G20, fehlerbericht("BMW 3er G20", "Steuerkette", "Kühlung"),
                  empfehlung="kaufen_nach_besichtigung")
rr_g20 = [i for i in erg_g20["insights"] if i.kategorie == "rueckruf"]
check("B1 die drei amtlichen Rückrufe sind da",
      len(rr_g20) == 3 and all(i.trust == "verified" for i in rr_g20),
      [(i.kurztitel, i.trust) for i in rr_g20])
check("B2 alle drei stehen in 'Kritische Risiken' (Rückruf-Konsistenz)",
      all((i.kurztitel or "").split(":")[0] in erg_g20["bericht"].split("## Preis")[0]
          for i in rr_g20))
fragen_g20 = _texte_aktionen(_alle_aktionen(dg20["ka"]))
check("B3 Händler: Vertragspartner und Auftrag werden erfragt",
      "wer der Vertragspartner ist" in fragen_g20 and "im eigenen Namen oder im Auftrag" in fragen_g20)
check("B4 Automatik: Fahrstufen statt Kupplung",
      "Fahrstufe" in _texte_aktionen(dg20["ka"].probefahrt.basis)
      and "Kupplung darf nicht durchrutschen" not in _texte_aktionen(dg20["ka"].probefahrt.basis))
check("B5 72.000 km erkannt und nicht erneut erfragt",
      "Was umfasste die im Inserat genannte Wartung bei rund 72.000 km?" in fragen_g20
      and "Wann war die letzte Wartung" not in fragen_g20)
check("B6 FIN-Copy ohne KBA-Individualprüfung",
      "KBA prüfen" not in fragen_g20 and "FIN" in fragen_g20)
check("B7 2 Vorbesitzer im Teil-II-Punkt", "2 Vorbesitzer" in fragen_g20)


# ══ C) Nicht-BMW ═════════════════════════════════════════════════════════════
print("\n=== C) Nicht-BMW mit anderer Datenlage ===")
da4 = deterministisch(A4)
check("C1 Audi A4 B8 1.8 TFSI erkannt",
      (da4["baureihe"] or {}).get("id", "").startswith("audi-a4-b8") and da4["motor"] is not None,
      (da4["baureihe"] or {}).get("id"))
kette = [i for i in _risiken(da4["insights"]) if "steuerkette" in (i.bauteil or "").lower()]
check("C2 Steuerkette aus Motorproblem und Wartungseintrag: EIN Thema",
      len(kette) == 1 and kette[0].zusammengefuehrt, [(i.kategorie, i.titel) for i in kette])
bes_a4 = {a.id: a.aktion for a in da4["ka"].besichtigung.fahrzeugspezifisch}
check("C3 Steuerkette: Kaltstartprüfung, keine Sichtprüfung",
      "KALT" in bes_a4.get("besichtigung-steuerkette", "")
      and "Leckagen" not in bes_a4.get("besichtigung-steuerkette", ""), bes_a4)
fragen_a4 = _texte_aktionen(da4["ka"].verkaeuferfragen.fahrzeugspezifisch
                            + da4["ka"].verkaeuferfragen.basis)
check("C4 bekannte Wartung (81.000 km) wird nicht als unbekannt erfragt",
      "Wann war die letzte Wartung" not in fragen_a4 and "bei rund 81.000 km" in fragen_a4)
check("C5 'unfallfrei laut Inserat' wird nicht erneut offen erfragt",
      "Hatte das Fahrzeug einen Unfall" not in fragen_a4
      and "trotz der Angabe „unfallfrei“" in fragen_a4)
dc2 = deterministisch(C200)
rr_c2 = [i for i in dc2["insights"] if i.kategorie == "rueckruf"]
check("C6 Mercedes C200 W205: belegte Rückrufe erscheinen", len(rr_c2) >= 3, len(rr_c2))
check("C7 ohne Motordaten keine erfundenen Motorprobleme",
      not [i for i in dc2["insights"] if i.kategorie in ("motorproblem", "wartung")])
check("C8 Inspektion 04/2025 bei 91.000 km erkannt",
      (fakten_aus_request(C200).letzte_wartung or None) is not None
      and fakten_aus_request(C200).letzte_wartung.km == 91000)
erg_c2, _ = voll(C200, fehlerbericht("Mercedes C200 W205", "Turbolader", "Steuerkette"),
                 empfehlung="kaufen_nach_besichtigung")
check("C9 Nicht-BMW: kein Gedankenstrich, keine erfundene Markterwartung",
      not [t for t in nutzertexte(erg_c2) if RE_RHETORISCH.search(t)]
      and "Markterwartung" not in erg_c2["bericht"])


# ══ D) Fahrzeug ohne hinterlegte Schwachstelle ═══════════════════════════════
print("\n=== D) Audi A1 GB 25 TFSI: keine künstlichen Risiken ===")
da1 = deterministisch(A1)
check("D1 erkannt", (da1["baureihe"] or {}).get("id") == "audi-a1-gb", (da1["baureihe"] or {}).get("id"))
check("D2 keine technischen Risiken erfunden", not _risiken(da1["insights"]))
check("D3 keine technischen Prüfpunkte ohne Evidence",
      not [a for a in da1["ka"].besichtigung.fahrzeugspezifisch + da1["ka"].probefahrt.fahrzeugspezifisch])
gr_a1 = baue_empfehlung_gruende(A1, da1["baureihe"], da1["motor"], da1["insights"], da1["kf"],
                                "kaufen_nach_besichtigung", False, None)
check("D4 Zusammenfassung sagt ehrlich 'keine Schwachstelle hinterlegt'",
      any("keine Schwachstelle hinterlegt" in g for g in gr_a1), gr_a1)
check("D5 kein Motor-Warnfinding", not [f for f in da1["kf"] if f.kategorie == "motorproblem"])


# ══ E) Low-Evidence bleibt Low (Inv. 3) ══════════════════════════════════════
print("\n=== E) Low-Evidence bleibt Low ===")


def _ins(iid, kategorie, bauteil, conf, trust, beschreibung, schweregrad=None):
    return Insight(id=iid, kategorie=kategorie, titel=f"{bauteil}: x", beschreibung=beschreibung,
                   confidence=conf, trust=trust, bauteil=bauteil, schweregrad=schweregrad)


zwei_low = kanonisiere([_ins("m1", "motorproblem", "Kolben", "niedrig", "unverified_db", "kann reißen"),
                        _ins("w1", "wartung", "Kolben", "niedrig", "unverified_db",
                             "Vorbeugender Tausch empfohlen.")])
check("E1 zwei ungeprüfte Aussagen ergeben EIN Thema mit 'niedrig'",
      len(zwei_low) == 1 and zwei_low[0].confidence == "niedrig", [i.confidence for i in zwei_low])
mit_beleg = kanonisiere([_ins("s1", "schwachstelle", "Turbolader", "hoch", "verified", "Lagerschaden."),
                         _ins("m2", "motorproblem", "Turbolader (TFSI)", "niedrig", "unverified_db",
                              "Ölverlust.")])
check("E2 eine unabhängig belegte Aussage führt, ohne dass die schwache angehoben wird",
      len(mit_beleg) == 1 and mit_beleg[0].id == "s1" and mit_beleg[0].confidence == "hoch"
      and "Weiterer Datensatz" in mit_beleg[0].beschreibung)
check("E3 bei Widerspruch gewinnt die zurückhaltendere Aussage",
      kanonisiere([_ins("w9", "wartung", "Pleuellager", "niedrig", "unverified_db",
                        "Vorbeugender Wechsel empfohlen."),
                   _ins("m9", "motorproblem", "Pleuellager", "niedrig", "unverified_db",
                        "vorbeugender Wechsel diskutiert")])[0].id == "m9")
agr = kanonisiere([_ins("a", "schwachstelle", "AGR-System", "niedrig", "unverified_db", "x"),
                   _ins("b", "motorproblem", "AGR-Ventil", "niedrig", "unverified_db", "y"),
                   _ins("c", "motorproblem", "AGR-Kühler", "niedrig", "unverified_db", "z")])
check("E4 verschiedene Bauteile (AGR-Ventil, AGR-Kühler) verschmelzen nie", len(agr) == 3)
check("E5 'Kupplung' und 'Haldex-Kupplung' bleiben getrennt",
      len(kanonisiere([_ins("k", "schwachstelle", "Kupplung (bei Schaltgetriebe)", "niedrig",
                            "unverified_db", "x"),
                       _ins("h", "wartung", "Haldex-Kupplung Öl", "niedrig", "unverified_db",
                            "y")])) == 2)
check("E6 'Zahnriemen' gehört zu 'Zahnriemen im Ölbad'",
      len(kanonisiere([_ins("z1", "motorproblem", "Zahnriemen im Ölbad", "niedrig",
                            "unverified_db", "x"),
                       _ins("z2", "wartung", "Zahnriemen", "niedrig", "unverified_db",
                            "y")])) == 1)


# ══ F) Wartungsarten (4.3) ═══════════════════════════════════════════════════
print("\n=== F) Wartungsarten ===")
check("F1 'Zündkerzen alle 60.000 km' ist reguläre Wartung",
      wartungsart("Alle 60.000 km", "Wechsel", "Zündkerzen") == WARTUNG_REGULAER)
check("F2 'präventives Pinnen/Capture-Plate' ist ein Umbau",
      wartungsart("Zustand prüfen", "präventives Pinnen/Capture-Plate", "Kurbelnabe")
      == WARTUNG_MODIFIKATION)
check("F3 'vorbeugender Wechsel empfohlen' ist vorbeugend",
      wartungsart("~50-80 tkm", "vorbeugender Wechsel empfohlen", "Pleuellager")
      == WARTUNG_VORBEUGEND)
check("F4 'Prüfung ab 100.000 km' ist Zustandsprüfung",
      wartungsart("Prüfung ab 100.000 km", "Auf Rasseln achten.", "Steuerkette") == WARTUNG_ZUSTAND)
check("F5 'Drallklappen entfernen/Blindstopfen' ist ein Umbau",
      wartungsart("Prüfung/Entfernung empfohlen", "Vorbeugende Entfernung.", "Drallklappen")
      == WARTUNG_MODIFIKATION)


# ══ G/H) Prüfklassen über ALLE Bauteile des Bestands (Inv. 4, 5) ═════════════
print("\n=== G/H) Prüfklassen über alle Bauteile ===")
import sqlite3  # noqa: E402

with sqlite3.connect(os.environ["AUTO_KI_DB_PATH"]) as _c:
    BAUTEILE = sorted({b for (b,) in _c.execute(
        "select bauteil from schwachstelle_baureihe union select bauteil from schwachstelle_motor "
        "union select bauteil from kritische_wartung") if b})
unmoeglich = re.compile(r"sichtprüf|auf leckagen|umliegenden bereich|zustand, leckagen", re.I)
verstoss_intern, verstoss_unbekannt, verstoss_oberflaeche = [], [], []
for b in BAUTEILE:
    kl = pruefklasse(b)
    text = _besichtigung(_komponente(b), b)
    if kl == KLASSE_INTERN and text and unmoeglich.search(text):
        verstoss_intern.append(b)
    if kl == KLASSE_UNBEKANNT and text:
        verstoss_unbekannt.append(b)
    if kl == KLASSE_OBERFLAECHE and "bauteil" in (_komponente(b) or {}).get("besichtigung", "") \
            and not ("Risse" in text and "Ablösungen" in text and "Trübungen" in text):
        verstoss_oberflaeche.append(b)
check(f"G1 kein internes Bauteil mit Sicht-/Leckageprüfung ({len(BAUTEILE)} Bauteile)",
      not verstoss_intern, verstoss_intern[:5])
check("G2 unbekannte Prüfklasse -> keine erfundene Besichtigung",
      not verstoss_unbekannt, verstoss_unbekannt[:5])
for b in ("Pleuellager", "Pleuellagerschalen", "Kurbelwelle", "Kolben", "Nockenwelle",
          "Ausgleichswelle", "Kurbelnabe (crank hub)", "Kipphebel", "Hydrostößel"):
    check(f"G3 '{b}' ist internes Bauteil", pruefklasse(b) == KLASSE_INTERN, pruefklasse(b))
check("G4 Nockenwellensensor ist Elektronik, nicht Motorinneres",
      pruefklasse("Nockenwellensensor") != KLASSE_INTERN)
check("G5 Kurbelwellensimmerring bleibt Ölspurenprüfung",
      (_komponente("Kurbelwellensimmerring") or {}).get("schluessel") == "oelverlust")
check("H1 Oberflächenthemen bekommen Risse/Ablösungen/Trübungen", not verstoss_oberflaeche,
      verstoss_oberflaeche[:5])
check("H2 CFK-Dach Klarlack ist Oberfläche, kein Öffnungsdach",
      (_komponente("CFK-Dach Klarlack") or {}).get("schluessel") == "oberflaeche")
for b in ("Panoramadach", "Schiebedach undicht", "Verdeckmechanismus", "Hardtop-Mechanismus"):
    check(f"H3 '{b}' behält die Öffnungs-/Dichtungsprüfung",
          (_komponente(b) or {}).get("schluessel") == "dach_fenster")
check("H4 'Laufbuchse' (Zylinder) ist kein Fahrwerk",
      (_komponente("Zylinderlaufbuchsen") or {}).get("schluessel") != "fahrwerk")

# Substring-Fallen der Bauteiltabelle: das Muster steckt in einem anderen Wort.
for bauteil, verboten, soll in (
        ("Hydrostößel", "rost", "motor_innen"),                  # hyd-ROST-ößel
        ("Ventilsitze", "innenraum", "motor_innen"),             # Ventil-SITZ-e
        ("Zylinderabschaltung (ACT)", "getriebe", "sensorik"),   # ab-SCHALTUNG
        ("Ansaugkrümmer", "abgasanlage", "sensorik"),            # Ansaug- ist kein Abgas
        ("Haldex-Kupplung Öl", "kupplung", "allradantrieb"),
        ("Viskokupplung (Syncro)", "kupplung", "allradantrieb"),
        ("Kompressorkupplung", "kupplung", "turbolader"),
        ("Verteilergetriebe (VTG)", "getriebe", "allradantrieb"),
        ("Hinterachsgetriebeöl (4MATIC)", "getriebe", "allradantrieb"),
        ("Getriebe (Doppelkupplung)", "getriebe", "automatikgetriebe"),
        ("Kupplung (bei Schaltgetriebe)", "getriebe", "kupplung"),
        ("5.0L V8 Motor (insbesondere HO-Version)", "sensorik", "motor"),  # insbe-SONDE-re
        ("Elektronik (Feststellbremse, Lenksäulensteuergerät)", "bremsen", "sensorik")):
    ist = (_komponente(bauteil) or {}).get("schluessel")
    check(f"G6 '{bauteil}' -> {soll} (nicht {verboten})", ist == soll, ist)


# ══ I) Vergleichstabelle (Inv. 6, 7) ═════════════════════════════════════════
print("\n=== I) Vergleichstabelle mit Herkunft ===")
zeilen_ohne = baue_zeilen(M4, dm4["baureihe"], dm4["motor"], laufleistungskontext=dm4["ll"],
                          price_assessment=PriceAssessment(), markt_verfuegbar=False,
                          fakten=fakten_aus_request(M4))
check("I1 ohne Referenz immer 'nicht bewertbar'",
      all(z.einordnung == NICHT_BEWERTBAR for z in zeilen_ohne if z.referenz is None))
check("I2 ohne Marktdaten keine Markt-Herkunft",
      not any(z.referenz_herkunft == HERKUNFT_MARKT for z in zeilen_ohne))
check("I3 Tabellenkopf ohne 'Markterwartung'", "Markterwartung" not in als_markdown(zeilen_ohne))
check("I3b Preis ohne Markt: 'keine belastbare Marktbasis', nicht bewertet",
      "| Preis | 43.500 € | keine belastbare Marktbasis | nicht bewertbar |"
      in als_markdown(zeilen_ohne), als_markdown(zeilen_ohne))
pa = PriceAssessment(verdict="marktgerecht", label="Marktgerecht", median_eur=45000,
                     lower_bound_eur=42000, upper_bound_eur=48000, confidence="mittel")
zeilen_mit = baue_zeilen(M4, dm4["baureihe"], dm4["motor"], laufleistungskontext=dm4["ll"],
                         price_assessment=pa, markt_verfuegbar=True, fakten=fakten_aus_request(M4))
preis = [z for z in zeilen_mit if z.kriterium == "Preis"][0]
check("I4 mit belastbarem Markt: Preiszeile mit Markt-Herkunft und Median",
      preis.referenz_herkunft == HERKUNFT_MARKT and "45.000" in preis.referenz)
check("I5 nur die Preiszeile trägt Markt-Herkunft",
      [z.kriterium for z in zeilen_mit if z.referenz_herkunft == HERKUNFT_MARKT] == ["Preis"])
check("I6 Leistung weicht von der Variante ab -> Hinweis statt Häkchen",
      [z for z in baue_zeilen(M4.model_copy(update={"leistung_ps": 390}), dm4["baureihe"],
                              dm4["motor"]) if z.kriterium == "Motor/Leistung"][0].einordnung
      .startswith("⚠"))


# ══ Invarianten über den GESAMTEN Bestand (1, 2, 3, 8, 9, 10, 12) ════════════
print("\n=== Invarianten über alle Baureihen und Motorvarianten ===")
start = time.time()
with sqlite3.connect(os.environ["AUTO_KI_DB_PATH"]) as _c:
    ALLE = _c.execute("select marke, modell, generation, bauzeitraum_von, bauzeitraum_bis "
                      "from baureihe order by id").fetchall()
verst = {k: [] for k in ("1", "2", "3", "8a", "8b", "9", "12a", "12b", "D")}
kombis = 0
FIN_BEHAUPTUNG = re.compile(r"(dieses|ihr|dein) fahrzeug ist (?:[^.]{0,60})?betroffen"
                            r"|betrifft (dein|ihr|dieses) fahrzeug", re.I)
for marke, modell, generation, von, bis in ALLE:
    br = db.get_baureihe(marke, modell, generation)
    if not br:
        continue
    baujahr = (von or 2015) + 1 if (bis is None or (von or 0) + 1 <= bis) else von
    for motor in br.get("motoren") or [{}]:
        kombis += 1
        req = KaufCheckRequest(
            marke=marke, modell=modell, baujahr=baujahr, kilometerstand=150000,
            motor=motor.get("bezeichnung"), vorbesitzer=2, tuev_bis="06/2027",
            servicehistorie="teilweise", unfallfrei="ja",
            beschreibung="Letzte Wartung bei 81.000 km durchgeführt.")
        ins = build_insights(br, motor or None, [], req, check_typ="kauf")
        ll = build_laufleistungskontext(req, ins, heute_jahr=2026)
        ka = bereinige_nutzertexte(build_kaufaktionen(req, br, motor or None, ins,
                                                      laufleistungskontext=ll))
        kf = bereinige_nutzertexte(build_key_findings_kauf(req, br, motor or None, ins, None))
        gr = bereinige_nutzertexte(baue_empfehlung_gruende(req, br, motor or None, ins, kf,
                                                           "nur_mit_werkstattpruefung", False, None))
        name = f"{br['id']}/{motor.get('variante_id')}"
        aktionen = _alle_aktionen(ka)
        texte = _texte_aktionen(aktionen)
        # 1 / 10: bekannte Fakten nie als unbekannt erfragt, egal wie voll die Listen sind
        if unbekannt_fragen(texte, fakten_aus_request(req)):
            verst["1"].append(name)
        # 2: kein Thema doppelt (gleicher Kern oder gleiche Abkürzung)
        kerne = [bauteil_kern(i.bauteil) for i in _risiken(ins)]
        for a in range(len(kerne)):
            for b2 in range(a + 1, len(kerne)):
                if (kerne[a].kern and kerne[a].kern == kerne[b2].kern) or (kerne[a].alias & kerne[b2].alias):
                    verst["2"].append(name)
        # 3: ohne Verifikation nie mehr als "niedrig"
        if any(i.trust not in ("verified", "web") and i.confidence != "niedrig"
               for i in _risiken(ins)):
            verst["3"].append(name)
        # 8: Zusammenfassung widerspricht den Risiken nicht
        schwer_oder_offen = [i for i in _risiken(ins)
                             if i.kategorie in ("schwachstelle", "motorproblem")
                             and (i.schweregrad is None or i.schweregrad.lower() in ("hoch", "kritisch", "sehr hoch"))]
        if schwer_oder_offen and any("keine schwerwiegende" in g for g in gr):
            verst["8a"].append(name)
        if any(f.titel == "Bekanntes Motorproblem" for f in kf) and not any(
                i.kategorie == "motorproblem" and i.trust == "verified" for i in ins):
            verst["8b"].append(name)
        # 9: kein rhetorischer Gedankenstrich
        if RE_RHETORISCH.search(texte + "\n".join(gr) + "\n".join(f"{f.titel} {f.beschreibung} {f.aktion or ''}" for f in kf)):
            verst["9"].append(name)
        # 12: FIN-Safety
        if any(i.kategorie == "rueckruf" and i.applicability == "confirmed_by_vin" for i in ins):
            verst["12a"].append(name)
        rr_aktionen = [a for a in aktionen if (a.kategorie or "").startswith("rueckruf")
                       or (a.kategorie or "") == "web_rueckruf"]
        if any("FIN" not in a.aktion for a in rr_aktionen if a.bereich == "dokumente") \
                or FIN_BEHAUPTUNG.search(texte):
            verst["12b"].append(name)
        # D: der alte Rückfalltext ist verschwunden
        if "und den umliegenden Bereich auf erkennbare Auffälligkeiten" in texte:
            verst["D"].append(name)
dauer = time.time() - start
print(f"        {kombis} Kombinationen in {dauer:.1f} s")
check(f"INV 1/10 bekannte Wartung/HU/Vorbesitzer nie als unbekannt ({kombis} Fahrzeuge)",
      not verst["1"], verst["1"][:5])
check("INV 2 kein technisches Thema doppelt", not verst["2"], verst["2"][:5])
check("INV 3 ungeprüfte Aussagen tragen nie mehr als 'niedrig'", not verst["3"], verst["3"][:5])
check("INV 8a keine 'keine schwerwiegende Schwachstelle' neben schweren/offenen Risiken",
      not verst["8a"], verst["8a"][:5])
check("INV 8b 'Bekanntes Motorproblem' nur mit belegtem Motorproblem", not verst["8b"],
      verst["8b"][:5])
check("INV 9 keine rhetorischen Gedankenstriche in Prüfplan, Findings, Gründen", not verst["9"],
      verst["9"][:5])
check("INV 12a nie 'per FIN bestätigt' ohne FIN-Prüfung", not verst["12a"], verst["12a"][:5])
check("INV 12b Rückruf-Nachweise nennen die FIN, keine Betroffenheitsbehauptung",
      not verst["12b"], verst["12b"][:5])
check("INV 4 (Rest) der generische Rückfalltext kommt nirgends mehr vor", not verst["D"],
      verst["D"][:5])

# 9 für die Datenbanktexte des Fahrzeugprofils, alle Baureihen
mit_strich = []
for marke, modell, generation, _v, _b in ALLE:
    fk = build_fahrzeugkontext(db.get_baureihe(marke, modell, generation))
    if fk and any(RE_RHETORISCH.search(getattr(fk, f) or "") for f in
                  ("erkennung_generation", "facelift_merkmale", "vorgaenger", "segment",
                   "wartung_hu_intervall")):
        mit_strich.append(fk.baureihe_id)
check(f"INV 9 Fahrzeugprofil aller {len(ALLE)} Baureihen ohne rhetorischen Gedankenstrich",
      not mit_strich, mit_strich[:5])


# ══ 11) Rückrufimport je (Rückruf, Baureihe) ═════════════════════════════════
print("\n=== 11) Rückrufimport entscheidet je Paar ===")
from app.kba_import_kandidaten import (  # noqa: E402
    POSSIBLE_DUPLICATE, SAFE_IMPORT, import_kandidaten, verlorene_paare,
)


def _kba(**kw):
    z = {"KBA-Referenznummer": "9001", "Rückrufcode des Herstellers": "ABC",
         "Veröffentlichungsdatum": "2020-05-01", "Marke": "BMW", "Modell": "X5, X6",
         "Mangelbezeichnung": "Der Gasgenerator des Airbags kann bersten.",
         "Produktionszeitraum von": "2009", "Produktionszeitraum bis": "2012",
         "Beschreibung der Maßnahme": "Austausch des Gasgenerators.",
         "Mögliche Eingrenzung der betroffenen Modelle": "N/A",
         "Überwachung der Rückrufaktion durch das KBA": "überwacht"}
    z.update(kw)
    return z


_BR = [{"id": "bmw-x5-e70", "marke": "BMW", "modell": "X5", "generation": "E70",
        "bauzeitraum_von": 2006, "bauzeitraum_bis": 2013},
       {"id": "bmw-x6-e71", "marke": "BMW", "modell": "X6", "generation": "E71",
        "bauzeitraum_von": 2008, "bauzeitraum_bis": 2014}]
_dublette_x5 = {"id": 1, "baureihe_id": "bmw-x5-e70", "datum": "2019",
                "betroffene_baujahre": "2009-2012", "mangel": "Airbag: Gasgenerator kann bersten.",
                "abhilfe": "Gasgenerator tauschen.", "kba_referenz": None}
k = import_kandidaten([_kba()], [_dublette_x5], _BR)
paare = {bid: kl for bid, kl, _g in k[0].paare} if k else {}
check("11a Dublette nur beim X5: das X6-Paar bleibt sicher",
      paare == {"bmw-x5-e70": POSSIBLE_DUPLICATE, "bmw-x6-e71": SAFE_IMPORT}, paare)
check("11b die Rückruf-Klasse bleibt die strengste (historische Chargen unverändert)",
      k and k[0].klasse == POSSIBLE_DUPLICATE)
check("11c das X6-Paar steht in der Liste der verlorenen Paare",
      [(v["referenz"], v["baureihe_id"]) for v in verlorene_paare(k)] == [("9001", "bmw-x6-e71")])
_ref_x5 = dict(_dublette_x5, kba_referenz="9001")
k2 = import_kandidaten([_kba()], [_ref_x5], _BR)
check("11d Referenz nur beim X5 im Bestand: für den X6 bleibt der Rückruf Kandidat",
      k2 and {b: kl for b, kl, _g in k2[0].paare}.get("bmw-x6-e71") == SAFE_IMPORT)
check("11e Referenz bei ALLEN Zielen im Bestand: gar kein Kandidat",
      import_kandidaten([_kba()], [_ref_x5, dict(_ref_x5, id=2, baureihe_id="bmw-x6-e71")],
                        _BR) == [])
folge = _kba(**{"KBA-Referenznummer": "9002",
                "Mangelbezeichnung": "Nicht der Spezifikation entsprechende Verschraubung des "
                                     "Hinterachsträgers kann zu kritischen Fahrsituationen führen."})
check("11f Sicherheitsfolge im amtlichen Text macht den Rückruf zum Kandidaten",
      bool(import_kandidaten([folge], [], _BR)))
check("11g ohne Sicherheitsbezug weiterhin kein Kandidat",
      import_kandidaten([_kba(**{"KBA-Referenznummer": "9003",
                                 "Mangelbezeichnung": "Das Radio zeigt die falsche Uhrzeit an.",
                                 "Beschreibung der Maßnahme": "Software-Update."})],
                        [], _BR) == [])


# ══ Baujahresangaben (offene Grenzen, Tendenzen) ═════════════════════════════
print("\n=== Baujahresangaben ===")
for text, bj, soll in (("ab 2015", 2016, True), ("ab 2015", 2014, False),
                       ("bis 2012", 2009, True), ("bis 2012", 2014, False),
                       ("Bis ca. 2013", 2014, None), ("v.a. 2014-2015", 2016, None),
                       ("2009-2014 (insbesondere frühe Baujahre)", 2016, False),
                       ("2018-2020", 2019, True), ("2018-2020", 2016, False), ("2020", 2020, True),
                       ("Alle Baujahre, verstärkt vor 2000", 2005, None)):
    check(f"BJ {text!r} / {bj} -> {soll}", _baujahr_passt(text, bj) is soll, _baujahr_passt(text, bj))


# ══ Schreibstil-Netz ═════════════════════════════════════════════════════════
print("\n=== Schreibstil-Netz ===")
for roh, soll in (("KLEINE Niere — klarer Unterschied", "KLEINE Niere: klarer Unterschied"),
                  ("zeigen lassen — auch die Rechnung", "zeigen lassen, auch die Rechnung"),
                  ("Der Motor — ein Sechszylinder — ist robust", "Der Motor, ein Sechszylinder, ist robust"),
                  ("Spanne 12.000 – 22.000 €", "Spanne 12.000–22.000 €"),
                  ("Bauzeit 2014 – 2020.", "Bauzeit 2014–2020."),
                  ("E-Mail, KI-Chat, 2.0-TDI, 2019-2021", "E-Mail, KI-Chat, 2.0-TDI, 2019-2021")):
    check(f"Stil {roh!r}", entferne_gedankenstriche(roh) == soll, entferne_gedankenstriche(roh))


# ══ Datenfix und historische Reports ═════════════════════════════════════════
print("\n=== Datenfix und Kompatibilität ===")
seed = open("db/seed_fahrzeugdaten.sql", encoding="utf-8").read()
check("DF1 Seed trägt den korrigierten F82-LCI-Text",
      "neu gestaltete LED-Rückleuchten. OLED-Rückleuchten gab es nur bei Sondermodellen" in seed
      and "serienmäßig, OLED-Rückleuchten.'" not in seed)
check("DF2 Seed ohne den Gedankenstrich im F82-Profil", "KLEINE Niere —" not in seed)
import app.data_migrations as dmig  # noqa: E402

with sqlite3.connect(os.environ["AUTO_KI_DB_PATH"]) as _c:
    dmig.schritt_textkorrekturen(_c, True)          # zweiter Lauf: idempotent
    _c.execute("update baureihe set facelift_merkmale='anders gepflegt' where id='bmw-m4-f82'")
    try:
        dmig.schritt_textkorrekturen(_c, False)
        abgebrochen = False
    except RuntimeError:
        abgebrochen = True
    _c.rollback()
check("DF3 Textkorrektur bricht bei unbekanntem Ausgangstext ab (kein Überschreiben)", abgebrochen)
alt = {"bericht": "x", "empfehlung": "kaufen", "preis_bewertung": "unbekannt",
       "quelle": "datenbank", "vertrauen": "hoch",
       "insights": [{"id": "wartung-7", "kategorie": "wartung",
                     "titel": "Kurbelnabe: kritischer Wartungspunkt (M4)",
                     "beschreibung": "alt", "confidence": "hoch"}]}
geladen = KaufCheckResponse(**alt)
check("HR1 gespeicherter Alt-Check lädt unverändert (keine rückwirkende Änderung)",
      geladen.insights[0].titel == "Kurbelnabe: kritischer Wartungspunkt (M4)"
      and geladen.insights[0].confidence == "hoch")
check("HR2 nebenbelege werden nicht serialisiert",
      "nebenbelege" not in erg_m4["insights"][0].model_dump())


# ══ Provider ═════════════════════════════════════════════════════════════════
print("\n=== Provider ===")
check(f"PV keine echten Provider-Aufrufe (Gemini {PROVIDER['gemini']}, Tavily {PROVIDER['tavily']})",
      PROVIDER == {"gemini": 0, "tavily": 0}, PROVIDER)

print()
print(f"{PASS} Prüfungen bestanden, {len(FEHLER)} fehlgeschlagen.")
if FEHLER:
    for f in FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle Root-Cause-Tests bestanden.")
