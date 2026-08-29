"""
P2-A + P2-B — VerkaufsCheck-Transparenzluecken.

P2-A: Bei nicht belastbarer Fahrzeugidentitaet unterdrueckt das Identity-Gate
      korrekt alle fahrzeugspezifischen DB-Fakten — bisher aber STILL. Jetzt
      entsteht ein erklaerendes Key-Finding "Fahrzeug nicht eindeutig
      identifiziert" ueber die BESTEHENDE Key-Findings-Struktur (kategorie
      "identitaet", wie im Kaufcheck) — kein neues Feld, keine Frontend-Aenderung.

P2-B: `neutralisiere_no_market_preisurteil` uebersprang Markdown-Tabellenzeilen
      komplett. Eine vom Modell erfundene Zeile `| Marktwert | 8.500 € |` konnte
      dadurch ueberleben. Jetzt wird ZELLENWEISE geprueft; echte Fahrzeugdaten
      (Baujahr, km, kW) bleiben unangetastet.
      Nebenbefund aus dem Bestand: `_RE_EURO_ZAHL` endete auf `(?:€|EUR)\\b` und
      matchte dadurch NIE einen Betrag in €-Schreibweise — der Guard griff
      faktisch nur bei "EUR". Auch das ist hier abgedeckt.

Deterministisch: KEIN Netzwerk, KEIN LLM-Call.

    python test_verkaufscheck_transparenz.py
"""
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from app.car_lookup import find_baureihe_mit_vertrauen, find_motor
from app.evidence import build_insights
from app.key_findings import build_key_findings_verkauf
from app.models import VerkaufsCheckRequest, Marktanalyse
from app.postprocess import neutralisiere_no_market_preisurteil as N

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def pipeline(marke, modell, baujahr, motor=None, marktanalyse=None, belege=None, **kw):
    """Der deterministische VerkaufsCheck-Vorlauf inkl. Identity-Trust-Gate —
    exakt wie in run_verkaufscheck."""
    req = VerkaufsCheckRequest(marke=marke, modell=modell, baujahr=baujahr,
                               motor=motor, **kw)
    br_markt, info = find_baureihe_mit_vertrauen(marke, modell, baujahr)
    mo_markt = find_motor(br_markt, motor) if br_markt else None
    br, mo = (br_markt, mo_markt) if info["belastbar"] else (None, None)
    ins = build_insights(br, mo, belege or [], req, check_typ="verkauf",
                         marktanalyse=marktanalyse)
    kf = build_key_findings_verkauf(req, br, mo, ins, None, identitaet=info)
    return dict(info=info, br=br, ins=ins, kf=kf)


def ident_findings(kf):
    return [f for f in kf if f.kategorie == "identitaet"]


def kf_text(kf):
    return " ".join(f"{f.titel} {f.beschreibung} {f.aktion or ''}" for f in kf).lower()


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== P2-A 1) 'Golf XV' -> Identity-Finding vorhanden ===")

p = pipeline("Volkswagen", "Golf XV", 2022, kilometerstand=30000)
idf = ident_findings(p["kf"])
check("1.1 Identitaet nicht belastbar", not p["info"]["belastbar"])
check("1.2 genau EIN identitaet-Key-Finding", len(idf) == 1)
check("1.3 Titel benennt die Unsicherheit klar",
      idf and "nicht eindeutig identifiziert" in idf[0].titel.lower())
check("1.4 Finding erklaert die Einschraenkung",
      idf and "eingeschränkt" in idf[0].beschreibung.lower())

# Die entscheidende Zusicherung: das Finding darf nicht die TATSAECHLICH
# gematchte Baureihe nennen (hier `volkswagen-golf-viii`) — genau davor schuetzt
# das Gate. Der Hinweistext enthaelt ein STATISCHES Formatbeispiel
# ("z.B. „Golf VII“ oder „3er G20“") aus dem geteilten `FEHLENDE_ANGABE`; das ist
# fuer jede Eingabe identisch, wird nicht aus dem Nutzertext abgeleitet und ist
# damit keine Vermutung ueber DIESES Fahrzeug.
_txt = kf_text(idf)
check("1.5 Finding nennt NICHT die gematchte Generation (kein 'golf viii'/'golf i')",
      idf and "golf viii" not in _txt and "golf i " not in _txt
      and "volkswagen-golf" not in _txt)
# Der Hinweistext haengt AUSSCHLIESSLICH von der Match-Art ab, nie vom
# Eingabestring: zwei voellig verschiedene Eingaben mit derselben Match-Art
# ergeben denselben Text. Damit ist das Formatbeispiel nachweislich keine
# Ableitung aus dem Nutzertext.
_p_toyota = pipeline("Toyota", "Corolla Hyperdrive", 2022)
_toyota_txt = kf_text(ident_findings(_p_toyota["kf"]))
check("1.5b beide Eingaben haben Match-Art 'substring_only'",
      p["info"]["match_art"] == "substring_only"
      and _p_toyota["info"]["match_art"] == "substring_only")
check("1.5c identischer Hinweistext trotz voellig anderer Eingabe "
      "(Text ist match-art-abhaengig, nicht eingabeabhaengig)",
      _toyota_txt == _txt)
check("1.5d Toyota-Fall nennt ebenfalls keine gematchte Baureihe",
      "corolla" not in _toyota_txt)
_p_ix7 = pipeline("BMW", "iX7", 2024)
check("1.5e 'BMW iX7' (token_inner) nennt kein 'x7'",
      "x7" not in kf_text(ident_findings(_p_ix7["kf"])))
check("1.6 Finding sagt, was fehlt", idf and bool(idf[0].aktion))
check("1.7 keine Golf-Fakten: keine fahrzeugspezifischen Insights", p["ins"] == [])
check("1.8 keine harte Safety-Eskalation (Stufe bleibt Warnung)",
      idf and idf[0].stufe == "warnung")
check("1.9 kein Angst-/Fehlerton ('Fehler'/'ungültig'/'falsch')",
      idf and not any(w in kf_text(idf) for w in ("fehler", "ungültig", "falsch")))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== P2-A 2) nur 'BMW' ohne Modell -> Identity-Finding vorhanden ===")

p = pipeline("BMW", None, 2019, kilometerstand=90000)
idf = ident_findings(p["kf"])
check("2.1 Identitaet nicht belastbar", not p["info"]["belastbar"])
check("2.2 genau EIN identitaet-Key-Finding", len(idf) == 1)
check("2.3 keine konkrete Baureihenbehauptung (kein '1er'/'3er'/'M4')",
      idf and not any(b in kf_text(idf) for b in ("1er", "3er", "m4", "x1")))
check("2.4 keine fahrzeugspezifischen Insights", p["ins"] == [])
check("2.5 Baureihe wird nicht verwendet", p["br"] is None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== P2-A 3) bekanntes Fahrzeug -> KEIN falsches Warning ===")

for marke, modell, bj, mot in (("BMW", "320d", 2020, "320d"),
                               ("Opel", "Insignia", 2018, "2.0 Diesel 170 PS"),
                               ("Volkswagen", "Passat", 2009, "2.0 TDI")):
    p = pipeline(marke, modell, bj, motor=mot)
    check(f"3.x {marke} {modell}: Identitaet belastbar", p["info"]["belastbar"])
    check(f"3.x {marke} {modell}: KEIN identitaet-Finding",
          len(ident_findings(p["kf"])) == 0)
    check(f"3.x {marke} {modell}: fahrzeugspezifische Insights vorhanden",
          len(p["ins"]) > 0)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== P2-A 4) No-Market UND unsichere Identitaet koexistieren ===")

MA_LEER = Marktanalyse(gefunden=12, verwendet=0, datenqualitaet="niedrig")
# Belege wie im echten No-Market-Lauf: der Marktvergleich-Insight (und damit das
# Datenqualitaets-Finding) entsteht nur, wenn Web-Belege vorliegen — ohne sie
# waere das Fixture unrealistisch.
BELEGE = [{"typ": "web", "titel": "Beispielangebot", "url": "https://beispiel.de/a",
           "snippet": "Vergleichsangebot", "qualitaet": "Marktplatz"}]
p = pipeline("Volkswagen", "Golf XV", 2022, kilometerstand=30000,
             marktanalyse=MA_LEER, belege=BELEGE)
idf = ident_findings(p["kf"])
dq = [f for f in p["kf"] if f.kategorie == "datenqualitaet"]
check("4.1 identitaet-Finding vorhanden", len(idf) == 1)
check("4.2 Markt-Datenqualitaets-Finding ebenfalls vorhanden", len(dq) >= 1)
check("4.3 kein Preis-/Marktpositions-Finding ohne Marktdaten",
      not any(f.kategorie in ("preis", "marktposition") for f in p["kf"]))
check("4.4 beide Zustaende stoeren sich nicht (>= 2 Findings)", len(p["kf"]) >= 2)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== P2-A 5) Rueckwaertskompatibilitaet ===")

req = VerkaufsCheckRequest(marke="BMW", modell="320d", baujahr=2020, motor="320d")
br, info = find_baureihe_mit_vertrauen("BMW", "320d", 2020)
mo = find_motor(br, "320d")
ins = build_insights(br, mo, [], req, check_typ="verkauf")
check("5.1 build_key_findings_verkauf ohne den neuen Parameter aufrufbar",
      isinstance(build_key_findings_verkauf(req, br, mo, ins, None), list))
check("5.2 ohne Parameter entsteht KEIN identitaet-Finding",
      not ident_findings(build_key_findings_verkauf(req, br, mo, ins, None)))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== P2-B A) Markdown-Tabelle mit erfundenem Marktwert ===")

t = "| Kriterium | Wert |\n|---|---|\n| Marktwert | 8.500 € |\n"
o = N(t)
check("A1 Marktwert-Zelle neutralisiert", "8.500" not in o)
check("A2 Zeilenlabel bleibt lesbar erhalten", "Marktwert" in o)
check("A3 Tabellenstruktur erhalten", o.count("|") == t.count("|"))
check("A4 Trennzeile unangetastet", "|---|---|" in o)

t = "| Kriterium | Wert |\n|---|---|\n| Preis | 7.000–9.000 EUR |\n"
o = N(t)
check("A5 Euro-SPANNE neutralisiert (auch ohne Marktkontext-Label)",
      "7.000" not in o and "9.000" not in o)

t = "| Median | 12.400 € |\n"
check("A6 Median-Zeile neutralisiert", "12.400" not in N(t))

t = "| Preisbereich | 7.000 bis 9.000 Euro |\n"
check("A7 'bis'-Spanne mit 'Euro' neutralisiert", "7.000" not in N(t))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== P2-B B) Markdown-Tabelle mit Preisurteil ===")

check("B1 'marktgerecht' in Tabelle neutralisiert",
      "marktgerecht" not in N("| Bewertung | marktgerecht |\n").lower())
check("B2 'zu teuer' in Tabelle neutralisiert",
      "teuer" not in N("| Einschätzung | zu teuer |\n").lower())
check("B3 'günstig' in Tabelle neutralisiert",
      "günstig" not in N("| Bewertung | günstig |\n").lower())
check("B4 'überteuert' in Tabelle neutralisiert",
      "überteuert" not in N("| Bewertung | überteuert |\n").lower())


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== P2-B C) Normale Fahrzeugdaten-Tabelle bleibt UNVERAENDERT ===")

t = ("| Kriterium | Angabe |\n|---|---|\n"
     "| Baujahr | 2019 |\n| Kilometerstand | 150.000 km |\n"
     "| Leistung | 140 kW |\n| Verbrauch | 5,3 l/100km |\n"
     "| Getriebe | Automatik |\n| Vorbesitzer | 2 |\n")
o = N(t)
check("C1 Tabelle exakt unveraendert", o == t)
check("C2 Baujahr erhalten", "2019" in o)
check("C3 Kilometerstand erhalten", "150.000 km" in o)
check("C4 Leistung erhalten", "140 kW" in o)

check("C5 eigener Angebotspreis (Einzelbetrag ohne Marktkontext) bleibt",
      N("| Preis | 13.500 € |\n") == "| Preis | 13.500 € |\n")
check("C6 Reparaturkosten in Tabelle bleiben",
      N("| Zahnriemen | 900 € |\n") == "| Zahnriemen | 900 € |\n")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== P2-B D) Fliesstext-Guard funktioniert weiterhin ===")

check("D1 Marktwert im Fliesstext neutralisiert",
      "8.500" not in N("Der Marktwert liegt bei 8.500 €."))
check("D2 EUR-Schreibweise weiterhin neutralisiert",
      "14.000" not in N("Der Marktwert betraegt ca. 14.000 EUR."))
check("D3 Urteilswort im Fliesstext neutralisiert",
      "marktgerecht" not in N("Das Angebot ist marktgerecht.").lower())
check("D4 Reparaturkosten im Fliesstext bleiben",
      N("Ein Zahnriemenwechsel kostet etwa 900 €.")
      == "Ein Zahnriemenwechsel kostet etwa 900 €.")
check("D5 Bestandsbug behoben: €-Betraege wurden vorher NIE erkannt",
      "8.500" not in N("Der Marktpreis liegt bei 8.500 €."))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== P2-B E) Market verfuegbar -> Guard wird gar nicht erst gerufen ===")

# Der Guard ist ausschliesslich im No-Market-Pfad verdrahtet (app/verkaufscheck.py
# und app/kaufcheck.py: `if not markt_verfuegbar`). Hier wird belegt, dass ein
# legitimes Preisurteil unangetastet bleibt, wenn der Guard NICHT laeuft.
legit = ("## (b) Empfohlene Preisspanne\n\n"
         "| Preis-Kategorie | Betrag (€) |\n|---|---|\n"
         "| Schnellverkauf | 12.200 |\n| Empfohlener Preis | 13.050 |\n")
check("E1 ohne Guard-Aufruf bleibt das echte Preisurteil vollstaendig",
      "13.050" in legit and "12.200" in legit)
import app.verkaufscheck as vc_mod
import inspect
_src = inspect.getsource(vc_mod.run_verkaufscheck)
check("E2 Guard ist im Code an 'not markt_verfuegbar' gebunden",
      "if not markt_verfuegbar:" in _src
      and "neutralisiere_no_market_preisurteil" in _src)


print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE VERKAUFSCHECK-TRANSPARENZ-TESTS GRUEN")
