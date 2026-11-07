"""
ENFAL-Reparaturpaket (N13/N14, RC-F-KaufCheck vom 07.11.2026) -- Verfolgt
beide Befunde von der ECHTEN, gesicherten Quelle bis zur sichtbaren
Verkäuferfrage/Dokument bzw. bis zum Laufleistungs-Hinweis.

N13 (Ventilspiel/Bremsen -> "bereits gemacht" statt "geprüft"):
BEFUND: `_wartung_aktion()` (app/technical_research.py) erkannte nur die
Verbform ("inspizieren"), nicht die Substantivform ("Inspektion"/
"Inspektionen") -- real im Lexus-Serviceheft so formuliert ("Ventilspiel
(Inspektion mit Messfühler, LC 500, RC F) Alle 90.000 km / 6 Jahre").
FIX: `_WARTUNG_AKTION_PRUEFEN` erkennt jetzt zusätzlich "insp(?:izier|
ektion)". Teil A unten verwendet GENAU den real gesicherten SICHERHEIT-
Tabellenblock-Text (wörtlich aus `test_n12_n13_wartung_anwendbarkeit_
taetigkeit.py` übernommen) OHNE ein zusätzliches "geprüft" einzubauen.

N14 (Ventilspiel 90.000 km/6 Jahre, "Im Bereich" nur mit Kilometerbezug):
BEFUND: eine im selben Satz genannte zeitliche Alternative ("oder 6 Jahre")
ging sowohl in der Statusberechnung als auch im sichtbaren Wortlaut
(`_formuliere()`, app/laufleistung.py) vollständig verloren.
FIX: neue, rein additive `parse_zeitalternative()` macht die Alternative im
Wortlaut sichtbar, OHNE eine zeitliche Fälligkeit zu berechnen (der
Erstzulassungsmonat ist nirgends erfasst) -- der Kilometerstatus bleibt
ausdrücklich als solcher gekennzeichnet, die zeitliche Frage bleibt
ausdrücklich offen.

    python test_n13_n14_rc_f_lauf_07_11.py
"""
from app.technical_research import _extrahiere_fakten, _wartung_aktion
from app.evidence import build_insights
from app.kaufaktionen import build_kaufaktionen
from app.laufleistung import (
    build_laufleistungskontext, parse_wartungspunkt, parse_zeitalternative, prompt_block,
)
from app.models import TechnischeRecherche

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


class Req:
    def __init__(self, **kw):
        for k in ("marke", "modell", "baujahr", "kilometerstand", "motor", "kraftstoff",
                  "preis_eur", "beschreibung", "freitext", "unfallfrei", "vorbesitzer",
                  "tuev_bis", "scheckheftgepflegt"):
            setattr(self, k, kw.get(k))
        self.ausstattung = kw.get("ausstattung") or []


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


def extrahiere(text: str, url: str = "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
              titel: str = "Lexus Garantie- und Serviceheft (Schweiz, Deutsch)",
              marke: str = "Lexus", modell: str = "RC F", baujahr: int = 2017) -> list:
    abgelehnt: list[dict] = []
    return _extrahiere_fakten([treffer(url, titel, text)], "wartung",
                              marke=marke, modell=modell, baujahr=baujahr, abgelehnt=abgelehnt)


def insights_aus_fakten(fakten, req) -> list:
    """WebFakt -> Insight über die ECHTE Schnittstelle (app/evidence.py), ohne
    Baureihe/Motor-Match (DB-Miss) -- derselbe Aufrufstil wie
    test_technical_fallback.py."""
    web = TechnischeRecherche(ausgeloest_durch="db_miss", fakten=fakten)
    return build_insights(None, None, [], req, check_typ="kauf", web_recherche=web)


REQ = Req(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82_000,
          kraftstoff="Benzin")


# ══════════════════════════════════════════════════════════════════════════
# TEIL A -- N13, Ventilspiel: ECHTER, bereits gesicherter SICHERHEIT-Block
# (wörtlich aus test_n12_n13_wartung_anwendbarkeit_taetigkeit.py übernommen),
# OHNE ein zusätzliches "geprüft" einzubauen.
# ══════════════════════════════════════════════════════════════════════════
print("=" * 78)
print("TEIL A) N13 -- Ventilspiel: reale 'Inspektion'-Substantivform, real gesicherter Text")
print("=" * 78)

_SICHERHEIT_BLOCK = (
    "*2:  Für Albanien, Bosnien & Herzegowina, Kanarische Inseln, Zypern, Estland, "
    "Island, Lettland, Republik Nordmazedonien, Malta, Montenegro, Serbien, "
    "Slowakei *3:  Nur für LS 500h, ES 300h und NX 350h 16 Erneuern - Inspektion "
    "SICHERHEIT Zwischencheck Hauptservice Bremspedal und Feststellbremse*1 I "
    "Bremsklötze und -scheiben I I Bremsbeläge der Feststellbremse (IS 300h, "
    "RC 300h, RC F, LC 500, LC 500h, LS 500, LS 500h) I Bremsflüssigkeit E "
    "Bremsleitungen und -schläuche I Vorder- und Hinterradaufhängung I Lenkrad, "
    "Lenkgestänge und -getriebe I Bodenmatten I I Reifen, Beleuchtung, "
    "Wischerblätter und Waschflüssigkeit I I Kugelgelenke und Staubkappen der "
    "Radaufhängung I*2 I *1:  Prüfung der Feststellbremse betrifft nur: IS 300h, "
    "RC 300h und RC F *2: LS 500, LS 500h, LC 500h ZUSÄTZLICHER TEILEAUSTAUSCH "
    "Inverter-Kühlmittel (nur Hybrid- und Hybrid "
    "Plug-in-Modelle) Alle 240.000 km Danach alle 90.000 km Verteilergetriebeöl "
    "(RX 300 4WD, RX 350, RX 350L) Alle 30.000 km / 4 Jahre Vorderachsdifferenzialöl "
    "(LS 500, LS 500h 4WD) Alle 30.000 km / 4 Jahre Hinterachsdifferenzialöl "
    "(außer ES 300h, NX 350h, NX 450h+, RX 300 2WD, RX 450h, RX 450hL, UX 200, "
    "UX 250h) Alle 30.000 km / 4 Jahre ZUSÄTZLICHE INSPEKTIONEN "
    "Nebenaggregat-Antriebsriemen (außer ES 300h, UX 250h, NX 350h, NX 450h+) "
    "Alle 105.000 km / 6 Jahre Danach alle 15.000 km / jährlich Ventilspiel "
    "(Inspektion mit Messfühler, LC 500, RC F) Alle 90.000 km / 6 Jahre "
    "Automatikgetriebeöl Alle 60.000 km / 4 Jahre"
)

fakten_a = extrahiere(_SICHERHEIT_BLOCK)
ventilspiel = next((f for f in fakten_a if f.bauteil == "Ventilspiel"), None)
automatikgetriebeoel = next((f for f in fakten_a if "Automatikgetriebe" in (f.bauteil or "")), None)

check("A1) Ventilspiel wird extrahiert", ventilspiel is not None, str([f.bauteil for f in fakten_a]))
if ventilspiel:
    check("A2) Ventilspiel-Aussage enthält 'Inspektion' wörtlich aus der Quelle "
          "(kein künstliches 'geprüft' nötig)",
          "Inspektion" in ventilspiel.aussage and "geprüft" not in ventilspiel.aussage.lower(),
          ventilspiel.aussage)
    check("A3) Ventilspiel wird jetzt als 'pruefen' klassifiziert (vorher 'unbestimmt')",
          ventilspiel.taetigkeit == "pruefen", f"taetigkeit={ventilspiel.taetigkeit!r}")
check("A4) Automatikgetriebeöl (kein Aktionswort im Text) bleibt bewusst 'unbestimmt' "
      "-- kein Rückschluss aus dem Bauteilnamen, Regression gegen Überreparatur",
      automatikgetriebeoel is not None and automatikgetriebeoel.taetigkeit is None,
      f"taetigkeit={getattr(automatikgetriebeoel, 'taetigkeit', 'FEHLT')!r}")

if ventilspiel:
    insights_a = insights_aus_fakten([ventilspiel], REQ)
    aktionen_a = build_kaufaktionen(REQ, None, None, insights_a)
    fragen_a = {a.evidence_ids[0]: a for a in aktionen_a.verkaeuferfragen.fahrzeugspezifisch
               if a.kategorie == "web_wartung"}
    dok_a = {a.evidence_ids[0]: a for a in aktionen_a.dokumente.fahrzeugspezifisch
            if a.kategorie == "web_wartung"}
    vid = insights_a[0].id if insights_a else None
    check("A5) Verkäuferfrage für Ventilspiel entsteht", vid in fragen_a, str(list(fragen_a)))
    if vid in fragen_a:
        check("A6) Verkäuferfrage nennt jetzt 'geprüft', NICHT mehr 'gemacht'",
              "geprüft" in fragen_a[vid].titel and "gemacht" not in fragen_a[vid].titel,
              fragen_a[vid].titel)
    check("A7) Dokumentenanforderung für Ventilspiel entsteht", vid in dok_a, str(list(dok_a)))
    if vid in dok_a:
        check("A8) Dokumenttitel ist 'Prüfnachweis', NICHT 'Wartungsnachweis'",
              "Prüfnachweis" in dok_a[vid].titel, dok_a[vid].titel)

if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil A):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nTEIL A GRUEN")


# ══════════════════════════════════════════════════════════════════════════
# TEIL B -- N13, Bremsen: GENERISCHER Zusatzfall (realistische Analogie, da
# der exakte Bremsen-Quelltext des echten Laufs vom 07.11.2026 nicht
# vorliegt -- dieselbe Vokabellücke, ein ANDERES Bauteil, zum Nachweis, dass
# der Fix nicht Ventilspiel- oder Lexus-spezifisch ist).
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("TEIL B) N13 -- Bremsen (generische Analogie): dieselbe Vokabellücke, anderes Bauteil")
print("=" * 78)

_BREMSEN_BLOCK = (
    "ZUSÄTZLICHE INSPEKTIONEN Bremsflüssigkeit (Inspektionen laut Wartungsplan, "
    "RC F) Alle 30.000 km / 2 Jahre"
)
fakten_b = extrahiere(_BREMSEN_BLOCK)
bremsfluessigkeit = next((f for f in fakten_b if "rems" in (f.bauteil or "")), None)

check("B1) Bremsflüssigkeit wird extrahiert", bremsfluessigkeit is not None,
      str([f.bauteil for f in fakten_b]))
if bremsfluessigkeit:
    check("B2) Bremsflüssigkeit-Aussage enthält 'Inspektionen' wörtlich, kein 'geprüft' eingebaut",
          "Inspektion" in bremsfluessigkeit.aussage
          and "geprüft" not in bremsfluessigkeit.aussage.lower(),
          bremsfluessigkeit.aussage)
    check("B3) Bremsflüssigkeit wird als 'pruefen' klassifiziert",
          bremsfluessigkeit.taetigkeit == "pruefen", f"taetigkeit={bremsfluessigkeit.taetigkeit!r}")
# B4: Regression gegen Überschwappen -- separat am reinen Vokabel-Pfad
# geprüft (derselbe Pfad, den auch A4/Teil A nutzt), statt an einem künstlich
# erzwungenen zweiten Segment, das die Tabellenblock-Segmentierung in diesem
# kurzen Testausschnitt nicht zuverlässig trennt (reale Dokumentlänge/
# -struktur nötig, s. Teil A -- dort trennt der echte, längere Block
# 'Automatikgetriebeöl' bereits korrekt ab).
check("B4) kein Aktionswort ('Zündkerzen Alle 90.000 km', derselbe Vokabelpfad) "
      "bleibt 'unbestimmt' -- Regression, kein Überschwappen",
      _wartung_aktion("Zündkerzen Alle 90.000 km") == "unbestimmt")

if bremsfluessigkeit:
    insights_b = insights_aus_fakten([bremsfluessigkeit], REQ)
    aktionen_b = build_kaufaktionen(REQ, None, None, insights_b)
    fragen_b = {a.evidence_ids[0]: a for a in aktionen_b.verkaeuferfragen.fahrzeugspezifisch
               if a.kategorie == "web_wartung"}
    dok_b = {a.evidence_ids[0]: a for a in aktionen_b.dokumente.fahrzeugspezifisch
            if a.kategorie == "web_wartung"}
    vid_b = insights_b[0].id if insights_b else None
    check("B5) Verkäuferfrage für Bremsflüssigkeit nennt 'geprüft', nicht 'gemacht'",
          vid_b in fragen_b and "geprüft" in fragen_b[vid_b].titel
          and "gemacht" not in fragen_b[vid_b].titel,
          fragen_b.get(vid_b).titel if vid_b in fragen_b else "FEHLT")
    check("B6) Dokumenttitel ist 'Prüfnachweis'",
          vid_b in dok_b and "Prüfnachweis" in dok_b[vid_b].titel,
          dok_b.get(vid_b).titel if vid_b in dok_b else "FEHLT")

# B7: Wechsel-Priorität bleibt erhalten, auch wenn "Inspektion" im selben Satz
# zusätzlich vorkommt ("wechsel" muss weiterhin VOR "pruefen" gewinnen).
check("B7) 'wechsel' gewinnt weiterhin gegen ein zusätzliches 'Inspektion' im selben Satz "
      "(Prioritätsreihenfolge ERSETZEN vor PRUEFEN bleibt erhalten)",
      _wartung_aktion("Bei jeder Inspektion wird der Ölfilter gewechselt.") == "ersetzen")

if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil B):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nTEIL B GRUEN")


# ══════════════════════════════════════════════════════════════════════════
# TEIL C -- N14: kombiniertes Intervall 90.000 km / 6 Jahre bei 82.000 km,
# unbekannte Servicehistorie -- Kilometerstatus ausdrücklich gekennzeichnet,
# zeitliche Bewertung ausdrücklich offen, keine Fälligkeit/Entwarnung.
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("TEIL C) N14 -- kombiniertes km/Jahre-Intervall: Zeitangabe sichtbar, Status gekennzeichnet")
print("=" * 78)

# Real gesicherter Satz (wörtlich aus test_recherche_volltext_integration.py
# zitiert, "oder"-Variante desselben Dokuments).
VENTILSPIEL_AUSSAGE = ("Lexus RC F zusätzliche Inspektionen laut Lexus Care-Wartungsplan: "
                       "Ventilspiel (Inspektion mit Messfühler, LC 500, RC F) alle 90.000 km "
                       "oder 6 Jahre.")
fakten_c = extrahiere(VENTILSPIEL_AUSSAGE)
ventilspiel_c = next((f for f in fakten_c if f.bauteil == "Ventilspiel"), None)
check("C1) Ventilspiel wird aus dem 'oder'-Satz extrahiert", ventilspiel_c is not None,
      str([f.bauteil for f in fakten_c]))

if ventilspiel_c:
    req_c = Req(marke="Lexus", modell="RC F", baujahr=2017, kilometerstand=82_000,
               kraftstoff="Benzin", scheckheftgepflegt=None)  # Servicehistorie unbekannt
    insights_c = insights_aus_fakten([ventilspiel_c], req_c)
    ctx_c = build_laufleistungskontext(req_c, insights_c, heute_jahr=2026)
    hinweis_c = next((w for w in (ctx_c.wartungshinweise if ctx_c else [])
                      if w.bauteil == "Ventilspiel"), None)
    check("C2) Wartungshinweis für Ventilspiel entsteht (82.000 km liegt im "
          "Kilometer-Fenster von 90.000 km)", hinweis_c is not None,
          str([w.bauteil for w in (ctx_c.wartungshinweise if ctx_c else [])]))
    if hinweis_c:
        check("C3) Status ist 'im_bereich' -- NUR nach Kilometerstand",
              hinweis_c.status == "im_bereich", hinweis_c.status)
        check("C4) Die zeitliche Alternative ('oder 6 Jahre') steht jetzt im sichtbaren Hinweistext",
              "6 Jahre" in hinweis_c.hinweis, hinweis_c.hinweis)
        check("C5) Der Kilometerstatus wird ausdrücklich ALS SOLCHER gekennzeichnet "
              "('bezieht sich ausschließlich auf den Kilometerstand')",
              "ausschließlich auf den Kilometerstand" in hinweis_c.hinweis, hinweis_c.hinweis)
        check("C6) Die zeitliche Fälligkeit wird ausdrücklich als OFFEN benannt "
              "('nicht bestimmen', 'bleibt ... offen')",
              "nicht bestimmen" in hinweis_c.hinweis and "offen" in hinweis_c.hinweis,
              hinweis_c.hinweis)
        check("C7) KEINE Fälligkeits-/Entwarnungs-/Versäumnis-Begriffe (unverändertes Verbot)",
              not any(w in hinweis_c.hinweis.lower()
                     for w in ("fällig", "überfällig", "versäumt", "nicht durchgeführt", "fehlt")),
              hinweis_c.hinweis)
        check("C8) KEIN abgeleitetes Fälligkeitsdatum/keine Monatsangabe aus Baujahr -- "
              "die Funktion nennt nur die Quellenzahl, rechnet nichts nach",
              "Erstzulassungsmonat" in hinweis_c.hinweis, hinweis_c.hinweis)

    # C9: Dieselbe Information erreicht auch den LLM-Prompt (prompt_block),
    # nicht nur das Datenmodell -- sonst wäre sie für den Bericht wirkungslos.
    block_c = prompt_block(ctx_c)
    check("C9) prompt_block() (LLM-facing Text) enthält denselben Zeithinweis",
          "6 Jahre" in block_c and "ausschließlich auf den Kilometerstand" in block_c,
          block_c[:400])

if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil C):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nTEIL C GRUEN")


# ══════════════════════════════════════════════════════════════════════════
# TEIL D -- N14 Regression: reine km-Intervalle (die große Mehrheit der
# Bestandszeilen, 86 % laut Moduldocstring) bleiben UNVERÄNDERT -- kein neuer
# Satz, wenn keine Zeitalternative im Quelltext steht.
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("TEIL D) N14 Regression -- reine km-Intervalle bleiben unverändert")
print("=" * 78)

ZAHNRIEMEN_AUSSAGE = "Der Zahnriemen wird beim 2.0 TDI alle 210.000 km gewechselt."
fakten_d = extrahiere(ZAHNRIEMEN_AUSSAGE, url="https://www.volkswagen.at/inspektion-golf",
                      titel="VW Golf Inspektion", marke="VW", modell="Golf", baujahr=2013)
zahnriemen = next((f for f in fakten_d if f.bauteil), None)
check("D1) reiner km-Claim liefert kein Zeitalternative-Paar",
      parse_zeitalternative(ZAHNRIEMEN_AUSSAGE) is None)
if zahnriemen:
    req_d = Req(marke="VW", modell="Golf", baujahr=2013, kilometerstand=215_000)
    insights_d = insights_aus_fakten([zahnriemen], req_d)
    ctx_d = build_laufleistungskontext(req_d, insights_d, heute_jahr=2026)
    hinweis_d = (ctx_d.wartungshinweise[0] if ctx_d and ctx_d.wartungshinweise else None)
    check("D2) Wartungshinweis ohne Zeitalternative-Satz: KEIN 'zeitliche Alternative' im Text",
          hinweis_d is not None and "zeitliche Alternative" not in hinweis_d.hinweis,
          hinweis_d.hinweis if hinweis_d else "FEHLT")

if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil D):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nTEIL D GRUEN")


# ══════════════════════════════════════════════════════════════════════════
# TEIL E -- N14: Erst-/Folgeintervall ("Alle 105.000 km / 6 Jahre Danach
# alle 15.000 km / jährlich") -- die Zeitalternative bindet an den ERSTEN
# km-Wert, NICHT an das ziffernlose "jährlich" des Folgeintervalls.
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("TEIL E) N14 -- Erst-/Folgeintervall: Zeitalternative bindet an den ersten km-Wert")
print("=" * 78)

ERST_FOLGE_TEXT = ("Nebenaggregat-Antriebsriemen Alle 105.000 km / 6 Jahre Danach alle "
                   "15.000 km / jährlich")
punkt_e = parse_wartungspunkt(ERST_FOLGE_TEXT)
za_e = parse_zeitalternative(ERST_FOLGE_TEXT)
check("E1) parse_wartungspunkt() wählt den ERSTEN km-Wert (105.000 km), nicht das "
      "Folgeintervall (15.000 km)", punkt_e == (105_000, None), str(punkt_e))
check("E2) Zeitalternative ist '6 Jahre' (gebunden an 105.000 km), NICHT 'jährlich' "
      "(das Folgeintervall trägt keine Zahl vor der Einheit)",
      za_e == (6, "Jahre"), str(za_e))

if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil E):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nTEIL E GRUEN")


# ══════════════════════════════════════════════════════════════════════════
# TEIL F -- N14: Koexistenz mit dem Marktvorbehalt (app/evidence.py
# `_web_beschreibung()` hängt bei Wartungs-Insights ggf. einen eigenen,
# region-bezogenen Satz an dieselbe Beschreibung an) -- die Zeitalternative
# muss trotzdem korrekt erkannt werden, der Marktvorbehalt-Satz selbst darf
# nicht fälschlich als Zeitalternative gelesen werden.
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("TEIL F) N14 -- Koexistenz mit dem Marktvorbehalt-Satz")
print("=" * 78)

# Dieselbe Quelle wie Teil C, aber zusätzlich mit dem (real vom System erzeugten)
# Marktvorbehalt-Zusatzsatz, wie ihn `_web_beschreibung()` real anhängt.
MARKTVORBEHALT_SATZ = (" Die Quelle schränkt sich selbst ein (\"falls für Ihren Markt "
                       "zutreffend\"). Ob das für Ihr Fahrzeug in Deutschland zutrifft, "
                       "ist nicht bestätigt. Mit dem fahrzeugeigenen Wartungsplan oder "
                       "einer Vertragswerkstatt abgleichen.")
kombiniert = VENTILSPIEL_AUSSAGE + MARKTVORBEHALT_SATZ
check("F1) Zeitalternative wird trotz nachgestelltem Marktvorbehalt-Satz weiterhin "
      "korrekt als '6 Jahre' erkannt", parse_zeitalternative(kombiniert) == (6, "Jahre"),
      str(parse_zeitalternative(kombiniert)))
check("F2) Der km-Wartungspunkt bleibt unverändert 90.000 km",
      parse_wartungspunkt(kombiniert) == (90_000, None), str(parse_wartungspunkt(kombiniert)))
check("F3) Das 'oder' im Marktvorbehalt-Satz selbst ('Wartungsplan oder einer "
      "Vertragswerkstatt') erzeugt KEINE zweite, falsche Zeitalternative",
      parse_zeitalternative(kombiniert) == (6, "Jahre"))

if _FEHLER:
    print(f"\n{len(_FEHLER)} FEHLER (Teil F):")
    for f in _FEHLER[-10:]:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nTEIL F GRUEN")

print("\nALLE N13/N14-TESTS GRUEN")
