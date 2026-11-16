"""
SW-N19/E1 (A, C) -- redaktionelle englische Fachpresse und der Zustand "begrenzter Bericht".

Reale Quelle (gesichtet am 15.11.2026, ECHTE URL im Test, kein Domain-Austausch):
  https://www.pistonheads.com/news/ph-buying-guides/lexus-rc-f-ph-used-buying-guide/47506
  "Lexus RC F | PH Used Buying Guide", Tony Middlehurst, 6. August 2023 (Seitenmetadaten: type News / Motoring News,
  Kategorie "PH Buying Guides"). PistonHeads trennt öffentlich Redaktion ("reviews and buying guides written by our expert
  team", Pfad /news/), Forum (/gassing/), Kleinanzeigen und Auktionen (https://www.pistonheads.com/about-us).
Die Textauszüge sind reale Sätze dieser Seite in Originalreihenfolge (Regressionsbasis). Mit [SYNTH] gekennzeichnete
Gegenproben sind erfunden und beweisen nur die Filterlogik. Der Test sagt NICHT, ob der RC F die genannten Fehler hat.

    python scripts/test_isolated.py test_sw_n19_fachpresse_begrenzter_bericht.py
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="swn19a_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_tmp, "chroma")
import socket  # noqa: E402


def _gesperrt(*a, **k):
    raise RuntimeError("Netzzugriff im Test gesperrt")


socket.socket.connect = lambda *a, **k: _gesperrt()
socket.create_connection = _gesperrt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)

from app import database as _db  # noqa: E402
_db.ensure_tables()
import app.technical_research as tr  # noqa: E402
from app.evidence import build_insights  # noqa: E402
from app.kaufaktionen import build_kaufaktionen  # noqa: E402
from app.kaufcheck_bericht import bericht  # noqa: E402
from app.models import KaufCheckRequest, TechnischeRecherche  # noqa: E402
from app.vehicle_identity import VehicleIdentity  # noqa: E402
from app.web_search import KATEGORIE_RUECKRUFE, KATEGORIE_SCHWACHSTELLEN, KATEGORIE_WARTUNG, curate_results, score_domain  # noqa: E402

FAILS: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


REAL = "https://www.pistonheads.com/news/ph-buying-guides/lexus-rc-f-ph-used-buying-guide/47506"
TITEL = "Lexus RC F | PH Used Buying Guide | PistonHeads UK"
S_SERVO = ("Some owners have had an HVAC servo motor malfunction, usually at around 25,000 miles, which prevented the "
           "passenger side vent from blowing cold air.")
S_SEALER = ("Some RC Fs had poorly-applied rear wheelarch sealer which could lead to water ingress and eventual rust, "
            "especially in high salt-use regions (step forward UK).")
S_US_DASH = "American owners in particular have reported problems with cracking/delaminating dashboards and trim panels."
S_OEL = ("We have heard of heavier-than-normal oil usage on higher-mileage cars but carbon buildup hasn’t been an issue "
         "because the engine is both port- and direct-injected.")
S_RUECKRUF = ("There was a recall for a delaminating fuel line which could cause petrol to leak directly onto the engine, "
              "and there was some trouble with the dampers in the high-pressure fuel pumps (there are two on the RC F) "
              "which ‘chirp’ when they start to go bad.")
S_TVD = ("some of those who tried it found the multiplate-clutch TVD to be less predictable than the mech diff")
S_GETRIEBE = ("If you spent a lot of time in towns the intelligent transmission would get lazy and begin to introduce "
              "noticeable delays or rethinks into the quick-downchange process.")
S_SEAT = ("Taller folk found the seat bases to be too high, especially with the sunroof fitted, promoting a hunched, "
          "bent-neck posture.")
S_SITZ = "Others have experienced a section of the heated/ventilated seat becoming very hot when the cooling function was engaged."
S_VERSTAERKER = "The amplifier for the ML sound system sat in the boot and could play up when it got hot."
TEXT = "\n\n".join([
    "ENGINE & GEARBOX", S_RUECKRUF + " " + S_OEL, S_GETRIEBE,
    "The torque-vectoring differential was available as a £4,000 option. TVD gave a useful sensation of centredness "
    "on initial turn-in, but " + S_TVD + ".",
    "BODYWORK", S_SEALER,
    "INTERIOR", "The RC F driving position didn’t suit everybody. " + S_SEAT + " The dashtop also seemed quite high.",
    "One thing you’d think there’d be little dispute about was the quality of Lexus materials. " + S_US_DASH,
    S_SERVO + " " + S_SITZ + " " + S_VERSTAERKER])
ZIEL = dict(marke="Lexus", modell="RC F", baujahr=2017, generation="UXC1", hubraum="5.0", fuel_ziel="benzin",
            powertrains_ziel=frozenset({"ICE"}), motor="5.0 V8")


def fakten(url, text=TEXT, titel=TITEL):
    ab: list[dict] = []
    f = tr._extrahiere_fakten([{"url": url, "title": titel, "content": text}], "schwachstelle", abgelehnt=ab, **ZIEL)
    return f, ab


def grund(ab, teil):
    return sorted({a["grund"] for a in ab if teil in a.get("satz", "")})


print("== A: Quellenzulassung nach Publikationsart (nur Schwachstellen) ==")
sc = lambda u, k=KATEGORIE_SCHWACHSTELLEN: tr._score_domain_fuer_marke(u, k, "Lexus")  # noqa: E731
check("A1 PistonHeads-Redaktionsartikel (echte URL, /news/) hat den bestehenden Fachmedien-Wert",
      sc(REAL) == sc("https://www.adac.de/rund-ums-fahrzeug/autokatalog") == 55, str(sc(REAL)))
check("A2 PistonHeads-FORUM derselben Domain erhält diese Qualität NICHT",
      sc("https://www.pistonheads.com/gassing/topic.asp?h=0&f=1&t=1957777") == 0)
check("A3 PistonHeads-Kleinanzeigen und -Auktionen ebenfalls nicht",
      sc("https://www.pistonheads.com/classifieds/used-cars/lexus/rc-f") == 0
      and sc("https://www.pistonheads.com/auctions/lexus-rc-f") == 0)
check("A4 evo-Redaktion zählt, evo-Forum/Leserbewertung/Community nicht",
      sc("https://www.evo.co.uk/lexus/rc/f/engine-gearbox") == 55 and sc("https://www.evo.co.uk/forum/thread") == 0
      and sc("https://www.caranddriver.com/community/x") == 0 and sc("https://forum.pistonheads.com/x") == 0
      and sc("https://www.whatcar.com/lexus/rc/reader-reviews/x") == 0)
check("A5 nicht geprüfte Domains (Blog-Netzwerk, andere Medien) bleiben unqualifiziert",
      sc("https://www.jalopnik.com/x") == 0 and sc("https://www.autocar.co.uk/x") == 0)
check("A6 Wartung, Rückrufe und die generische Einstufung bleiben für dieselbe URL UNVERÄNDERT (keine globale Aufwertung)",
      sc(REAL, KATEGORIE_WARTUNG) == 0 and sc(REAL, KATEGORIE_RUECKRUFE) == 0 and score_domain(REAL) == 0
      and score_domain(REAL, KATEGORIE_SCHWACHSTELLEN) == 0)
check("A7 bestehende Fachmedien/Amtliche/Hersteller unverändert",
      sc("https://www.tuvsud.com/x") == 50 and sc("https://www.lexus.co.uk/x") == 48 and sc("https://www.motor-talk.de/x") == 27)
fuellung = [{"url": f"https://www.motor-talk.de/forum/t{i}.html", "title": f"t{i}", "content": "x"} for i in range(9)]
treffer = fuellung + [{"url": REAL, "title": TITEL, "content": "x"}]
check("A8 Kuratierung: ohne kategorie-bewusste Bewertung wird die Fachpresse abgeschnitten, mit ihr bleibt sie erhalten",
      REAL not in [r["url"] for r in curate_results(treffer, kategorie=KATEGORIE_SCHWACHSTELLEN, max_results=8)]
      and REAL in [r["url"] for r in curate_results(
          treffer, kategorie=KATEGORIE_SCHWACHSTELLEN, max_results=8,
          score_fn=lambda u: tr._score_domain_kategorie(u, KATEGORIE_SCHWACHSTELLEN))])

print("\n== B: reale Aussagen der echten Quelle -- Verarbeitungsausgang je Aussage ==")
f, ab = fakten(REAL)
bt = {x.bauteil: x for x in f}
check("B1 genau drei Aussagen werden aufgenommen: Klimaanlage (Stellmotor), Rost (Radlaufdichtmasse), Sitzkühlfunktion",
      set(bt) == {"Klimaanlage", "Rost", "Sitzheizung/-belüftung"}, str(set(bt)))
for name in ("Klimaanlage", "Rost", "Sitzheizung/-belüftung"):
    x = bt.get(name)
    check(f"B2 {name}: begrenzter Bericht, niedrige Confidence, Beleglage und offene Achsen (Baujahr, Markt) als "
          f"bestehende Unsicherheitszustände",
          x is not None and x.beleglage == "begrenzter_bericht" and x.confidence == "niedrig"
          and x.geltung_fuer_fahrzeug == "unresolved" and x.regionale_geltung == "unresolved"
          and [q.qualitaet for q in x.quellen] == ["Fachmedien"] and x.quellen[0].url == REAL, str(x))
check("B3 der englische Quellenwortlaut bleibt als gekennzeichnetes Zitat erhalten (inkl. 'Some owners', '25,000 miles')",
      "Some owners have had an HVAC servo motor malfunction, usually at around 25,000 miles" in bt["Klimaanlage"].aussage
      and "englischsprachiges Originalzitat" in bt["Klimaanlage"].aussage
      and "Some RC Fs had poorly-applied rear wheelarch sealer" in bt["Rost"].aussage)
check("B4 Hörensagen ('We have heard of heavier-than-normal oil usage') wird nicht zur Aussage", "hoerensagen" in grund(ab, "oil usage"),
      str(grund(ab, "oil usage")))
check("B5 US-Aussage ('American owners ... dashboards') wird NICHT als Betroffenheit übernommen",
      all("dashboard" not in x.aussage.lower() for x in f) and not [a for a in ab if "dashboards" in a.get("satz", "")
                                                                  and a["grund"] == "ok"], str(grund(ab, "dashboards")))
check("B6 Rückruf-/Pumpendämpfer-Satz wird nicht als zusätzlicher, unabhängiger Defekt gezählt (Rückrufpfad)",
      "rueckrufabschnitt" in grund(ab, "recall") and all("pump" not in x.aussage.lower() for x in f), str(grund(ab, "recall")))
check("B7 Fahrkritik (Getriebe 'lazy', TVD 'less predictable') wird kein technischer Defekt",
      all(x.bauteil not in ("Getriebe", "Differenzial") for x in f))
check("B8 Sitz-Kühlfunktion ('very hot when the cooling function was engaged') ist eine Fehlfunktion der Funktion und wird "
      "als begrenzter Bericht aufgenommen; der Verstärker ('could play up when it got hot') trägt jetzt ein Problemsignal, "
      "scheitert aber an der Reichweite (nur 'could', kein gemeldeter Fall, keine Teilmenge), nicht mehr an no_problem_signal",
      "Others have experienced" in bt["Sitzheizung/-belüftung"].aussage
      and "no_problem_signal" not in grund(ab, "heated/ventilated") + grund(ab, "amplifier")
      and grund(ab, "amplifier") == ["reichweite_unbelegt"] and "fremdfahrzeug_segment" not in {a["grund"] for a in ab},
      str(grund(ab, "amplifier")))
f_forum, ab_forum = fakten("https://www.pistonheads.com/gassing/topic.asp?h=0&f=1&t=1957777")
check("B9 derselbe Text unter einer Forum-URL derselben Domain: nichts wird bestätigt (source_quality)",
      f_forum == [] and {a["grund"] for a in ab_forum} == {"source_quality"})
f_fremd, _ = fakten("https://www.jalopnik.com/lexus-rc-f-guide/")
check("B10 nicht geprüfte Domain: nichts wird bestätigt", f_fremd == [])

print("\n== C: Aussageumfang bleibt erhalten ==")
S1 = "Many RC F owners report that the HVAC servo motor fails, a common problem on the 5.0-litre V8 cars."
f_s1, ab_s1 = fakten(REAL, "The RC F is a Lexus coupe. " + S1)
check("C1 [SYNTH] eine Häufigkeitsaussage ('many ... common') ist KEIN begrenzter Bericht: sie läuft durch die "
      "unveränderten Markt-/Anwendungs-Gates und wird ohne Marktbeleg nicht übernommen",
      f_s1 == [] and any(a["grund"].startswith("en_markt") for a in ab_s1), str([a["grund"] for a in ab_s1]))
S2 = "In Europe, the 5.0-litre V8 in the Lexus RC F built between 2015 and 2018 has a common water pump failure, and many owners report coolant leaks."
f_s2, _ = fakten(REAL, S2)
check("C2 [SYNTH] belegte, häufige, passende Aussage wird weiterhin ALS NORMALER Fakt bestätigt (kein begrenzter Status)",
      [x.bauteil for x in f_s2] == ["Wasserpumpe"] and f_s2[0].beleglage is None and f_s2[0].geltung_fuer_fahrzeug in (None, "covered"),
      str([(x.bauteil, x.beleglage) for x in f_s2]))
f_s3, ab_s3 = fakten(REAL, "The RC F is a Lexus coupe. Some American owners have had an HVAC servo motor malfunction.")
check("C3 [SYNTH] 'some' + US-Marker: Markt 'fremd' sperrt auch den begrenzten Bericht",
      f_s3 == [] and any(a["grund"] == "en_markt_fremd" for a in ab_s3), str([a["grund"] for a in ab_s3]))
f_s4, _ = fakten(REAL, "The RC F is a Lexus coupe. One owner had an HVAC servo motor malfunction after 25,000 miles.")
check("C4 [SYNTH] ein einzelner Besitzer bleibt ein Einzelbericht und wird nicht aufgenommen", f_s4 == [])

print("\n== F: Problemvokabular -- Gegenproben (Komponenten- und Funktionsbezug, keine globalen Schlagworte) ==")
from app import schwachstelle_englisch as en_  # noqa: E402
check("F1 'cooling function ... very hot' und 'amplifier could play up' tragen ein Problemsignal",
      en_.hat_problem(S_SITZ) and en_.hat_problem(S_VERSTAERKER))
check("F2 [SYNTH] 'play up' ohne technische Komponente und 'very hot' ohne Kühlfunktion tragen KEIN Problemsignal",
      not en_.hat_problem("The kids tend to play up on long drives.")
      and not en_.hat_problem("The exhaust gets very hot when the engine is on.")
      and not en_.hat_problem("The seat becomes very hot in direct sun.")
      and not en_.hat_problem("The heated seat becomes very hot when the heating function is engaged."))
f_h, ab_h = fakten(REAL, "The RC F is a Lexus coupe. We have heard of a seat that gets very hot when the cooling function was engaged.")
check("F3 [SYNTH] Hörensagen bleibt gesperrt, auch mit neuem Problemsignal", f_h == [] and "hoerensagen" in {a["grund"] for a in ab_h},
      str([a["grund"] for a in ab_h]))
f_u, ab_u = fakten(REAL, "The RC F is a Lexus coupe. American owners have experienced a seat becoming very hot when the cooling function was engaged.")
check("F4 [SYNTH] US-Marker sperrt auch die Sitzaussage", f_u == [], str([a["grund"] for a in ab_u]))

print("\n== D: sichtbarer Endpfad (Insight, Prüfpunkte, Fragen, Bericht) ==")
REQ = KaufCheckRequest(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82000, kraftstoff="Benzin",
                       motor="5.0 V8, Heckantrieb", ausstattung=[], vorbesitzer=2, scheckheftgepflegt=True)
ins = build_insights(None, None, [], REQ, check_typ="kauf", canonical_rueckrufe=[],
                     web_recherche=TechnischeRecherche(ausgeloest_durch="db_miss", fakten=f))
iw = {i.bauteil: i for i in ins if i.kategorie == "web_schwachstelle"}
check("D1 alle drei Aussagen werden Insights mit eigener Beleglage; der Titel nennt den begrenzten Bericht",
      set(iw) == {"Klimaanlage", "Rost", "Sitzheizung/-belüftung"}
      and all("(begrenzter Bericht einer Fachquelle)" in i.titel and i.beleglage == "begrenzter_bericht" for i in iw.values()))
b_klima = iw["Klimaanlage"].beschreibung
check("D2 Beschreibung: Zitat, 'keine Häufigkeit', 'nicht belegt' für Baujahr und Deutschland, Quelle; keine Häufigkeitsbehauptung",
      "Eine Fachquelle berichtet" in b_klima and "usually at around 25,000 miles" in b_klima
      and "eine Häufigkeit oder eine modellweite Schwachstelle ist daraus nicht belegt" in b_klima
      and "ob dieses Fahrzeug (Baujahr 2017) dazu gehört, ist nicht belegt" in b_klima
      and "Ob das für Ihr Fahrzeug in Deutschland zutrifft, ist nicht bestätigt" in b_klima
      and "bekannt" not in b_klima.lower() and "häufig" not in b_klima.lower().replace("häufigkeit", ""), b_klima)
check("D3 Einfluss: begrenzter Bericht, nicht als Mangel werten",
      "Begrenzter Bericht einer Fachquelle" in iw["Rost"].einfluss and "nicht als Mangel werten" in iw["Rost"].einfluss
      and iw["Rost"].confidence == "niedrig")
ka = build_kaufaktionen(REQ, None, None, ins)
besi = {a.titel: a.aktion for a in ka.besichtigung.fahrzeugspezifisch}
fra = {a.titel: a.aktion for a in ka.verkaeuferfragen.fahrzeugspezifisch}
check("D4 Besichtigung: gezielter Prüfpunkt mit Herkunftshinweis (Webquelle, Geltung nicht belegt)",
      "Klimaanlage" in besi and "Rost" in besi and "Geltung für dieses Baujahr nicht belegt" in besi["Klimaanlage"])
fk = fra.get("Wurde am Bauteil „Klimaanlage“ bereits gearbeitet oder etwas ersetzt?", "")
check("D5 Verkäuferfrage: 'einzelne Fälle', 'keine belegte Häufigkeit', 'nicht belegt', KEIN 'Problem'/'bekannter Schwachpunkt'",
      "Eine Fachquelle berichtet von einzelnen Fällen" in fk and "keine belegte Häufigkeit" in fk
      and "ob dieses Fahrzeug dazu gehört, ist nicht belegt" in fk and "Schwachpunkt" not in fk and "als Problem" not in fk, fk)
check("D6 kein künstliches Dokument für den begrenzten Hinweis",
      not [a for a in ka.dokumente.fahrzeugspezifisch if a.kategorie == "web_schwachstelle"])
text = bericht(REQ, VehicleIdentity.from_check_context(None, None, REQ), None, None, ins, ka, [], "kaufen_nach_besichtigung",
               type("PA", (), {"label": None})(), False, None, None, ["Inserat"])
abschnitt = text.split("Relevante Risiken und Hinweise", 1)[1].split("\n## ", 1)[0]
check("D7 Bericht: Aussage, begrenzter Status, Vorbehalte und Quelle (pistonheads.com, Fachmedien) stehen im Abschnitt",
      "### Klimaanlage" in abschnitt and "### Rost" in abschnitt and "Some owners have had an HVAC servo motor" in abschnitt
      and "Some RC Fs had poorly-applied rear wheelarch sealer" in abschnitt and "pistonheads.com (Fachmedien)" in abschnitt
      and abschnitt.count("keine belegte Häufigkeit") >= 3 and "Quellen-/Datensatzqualität: niedrig" in abschnitt)
check("D8 nichts davon taucht als Rückruf, Dämpfer oder US-Armaturenbrett auf",
      "dashboard" not in abschnitt.lower() and "Pulsationsdämpfer" not in abschnitt and "damper" not in abschnitt.lower())

print("\n== E: ein begrenzter Bericht verdeckt keine Datenlücke ==")
from app.empfehlungs_policy import technik_datenluecke  # noqa: E402
wr_nur_begrenzt = TechnischeRecherche(ausgeloest_durch="db_miss", fakten=f, phasen_status={"technik": "success"})
check("E1 nur begrenzte Berichte: die Analyse bleibt als 'eingeschränkt' (Schwachstellen und Wartung ohne belastbare Daten) "
      "gekennzeichnet",
      technik_datenluecke(wr_nur_begrenzt, ins) == ("Schwachstellen", "Wartung"), str(technik_datenluecke(wr_nur_begrenzt, ins)))
wr_normal = TechnischeRecherche(ausgeloest_durch="db_miss", fakten=f + f_s2, phasen_status={"technik": "success"})
check("E2 [SYNTH] mit einem normalen, belegten Schwachstellenfakt entfällt die Datenlücke wie bisher",
      technik_datenluecke(wr_normal, None) == (), str(technik_datenluecke(wr_normal, None)))

print("\n" + ("ALLE GRÜN" if not FAILS else f"{len(FAILS)} FEHLER: {FAILS}"))
sys.exit(1 if FAILS else 0)
