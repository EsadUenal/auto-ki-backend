"""
KaufCheck P2-5 — Laufleistungs- und Wartungskontext.
KEIN Netzwerk, KEIN LLM-Call, KEIN Live-DB-Zugriff (reine Fixtures).

Der Kern dieser Datei ist nicht, dass etwas berechnet wird — sondern dass etwas
NICHT behauptet wird. VIRA kennt den letzten tatsächlichen Service nicht, und
kein einziger Pfad darf so tun, als kenne er ihn.

  A  Baujahr + km            -> Fahrzeugalter sinnvoll, ohne Scheinpräzision
  B  km/Jahr korrekt berechnet (Durchschnitt seit Baujahr, gerundet)
  C  kein letzter Ölwechsel bekannt   -> KEINE "fällig"-Aussage, nirgends
  D  unverified wartung_oel_km allein -> KEIN Wartungshinweis, keine Fälligkeit
  E  Wartungspunkt 120.000 km, Fahrzeug 118.000 km -> relevanter Nachweis-Hinweis
  F  Wartungspunkt 120.000 km, Fahrzeug 160.000 km -> "prüfen ob durchgeführt"
  G  Wartungspunkt weit entfernt      -> gar kein Hinweis, keine Aktion
  H  falsches Baujahr (P0-2)          -> keine Evidence, also kein Wartungshinweis
  I  scheckheft=true                  -> keine "fehlt"-Aussage
  J  Scheckheft-Nachweisprüfung bleibt erhalten
  K  source-bound Web-Wartung         -> Quellen + Herkunft bleiben erhalten
  L  DB-Miss + Web-Wartung            -> Wartungskontext trotzdem möglich
  M  DB-Miss ohne Wartungs-Evidence   -> keine Erfindung
  N  No-Market                        -> identischer Kontext
  O  keine Preisbehauptung, strukturell
  P  Kaufaktionen tragen valide Evidence-IDs
  Q  Parser / Invarianten / Regression der bestehenden Ebenen

    python test_laufleistung.py
"""
import inspect
import re

import app.laufleistung as _L
from app.kaufaktionen import build_kaufaktionen
from app.laufleistung import (
    REFERENZ_DURCHSCHNITT, SCHWELLE_NIEDRIG,
    STATUS_DARUEBER, STATUS_ENTFERNT, STATUS_IM_BEREICH, STATUS_NAEHERT_SICH,
    build_laufleistungskontext, fahrzeugalter, km_pro_jahr,
    norm_bauteil, parse_wartungspunkt, prompt_block, status_zu_punkt,
)
from app.models import (
    EvidenceQuelle, Insight, KaufCheckResponse, Laufleistungskontext,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


class Req:
    def __init__(self, **kw):
        for k in ("marke", "modell", "baujahr", "kilometerstand", "motor", "kraftstoff",
                  "preis_eur", "beschreibung", "freitext", "unfallfrei", "vorbesitzer",
                  "tuev_bis", "scheckheftgepflegt"):
            setattr(self, k, kw.get(k))
        self.ausstattung = kw.get("ausstattung") or []


# `build_insights` baut den DB-Wartungstext wörtlich so zusammen (app/evidence.py).
# Die Fixture bildet das exakt nach, damit dieser Test die ECHTE Schnittstelle
# prüft und nicht eine bequemere Erfindung.
def db_wartung(bauteil: str, intervall: str, hinweis: str = "Unbedingt einhalten.",
               insight_id: str = "wartung-1") -> Insight:
    beschreibung = f"{hinweis} Vorgesehenes Intervall: {intervall}."
    return Insight(
        id=insight_id, kategorie="wartung",
        titel=f"{bauteil} — kritischer Wartungspunkt (TestMotor 2.0)",
        beschreibung=beschreibung,
        quellen_typen=["motorvarianten"],
        quellen=[EvidenceQuelle(typ="motorvarianten", ref=bauteil,
                                titel="VIRA-Wartungsdaten (geprüft)")],
        confidence="hoch", einfluss="Vor dem Kauf Durchführung und Nachweis klären.",
    )


def web_wartung(bauteil: str, aussage: str, insight_id: str = "web-wartung-1",
                url: str = "https://www.adac.de/beispiel") -> Insight:
    return Insight(
        id=insight_id, kategorie="web_wartung",
        titel=f"{bauteil} — Wartungsangabe aus der Webrecherche",
        beschreibung=aussage, quellen_typen=["web_technik"],
        quellen=[EvidenceQuelle(typ="web_technik", ref=bauteil, url=url,
                                titel="ADAC", qualitaet="Prüforganisation")],
        confidence="mittel",
        einfluss="Aus Webquellen belegte Intervallangabe — Nachweis der Durchführung verlangen.",
    )


def schwachstelle(bauteil: str, insight_id: str = "schwachstelle-1") -> Insight:
    return Insight(
        id=insight_id, kategorie="schwachstelle", titel=f"{bauteil} — bekannte Schwachstelle",
        beschreibung="Tritt bei dieser Baureihe gehäuft auf.",
        quellen_typen=["datenbank"],
        quellen=[EvidenceQuelle(typ="datenbank", ref=bauteil, titel="VIRA-Fahrzeugdatenbank")],
        confidence="hoch", schweregrad="mittel",
    )


HEUTE = 2026

# Fälligkeitsbehauptungen. Wortgrenzen sind zwingend: "auffällig" (Basiskatalog:
# "auffällig frisch gereinigter Motorraum") enthält "fällig" und ist völlig
# harmlos, "Steuerkette" enthält "teuer".
VERBOTEN_FAELLIGKEIT = (r"\bfällig", r"\bfaellig", r"überfällig", r"ueberfaellig",
                        r"versäumt", r"versaeumt", r"nicht durchgeführt",
                        r"nicht gemacht", r"wartungsstau", r"\bfehlt\b")

# Aussagen über eine angeblich fehlende Wartungshistorie (§11). Diese Prüfung
# gilt für ALLE Aktionstexte, nicht nur für die von P2-5 erzeugten — VIRA darf
# an keiner Stelle behaupten, die Historie fehle.
VERBOTEN_HISTORIE = (r"servicehistorie fehlt", r"wartungshistorie fehlt",
                     r"keine servicehistorie", r"keine wartungshistorie",
                     r"ohne servicehistorie", r"fehlende servicehistorie",
                     r"fehlende wartungshistorie")


# Die Zeilen, mit denen der Prompt-Block dem Modell etwas VERBIETET. Sie
# enthalten notwendigerweise genau die Wörter, nach denen hier gesucht wird
# ("schreibe niemals, ein Service sei fällig", "Leite ... KEINE Preisaussage
# ab") — sie als Verstoß zu werten wäre die Umkehrung des Gemeinten.
_ANWEISUNG = ("niemals", "schreibe", "behaupte nie", "leite aus der laufleistung",
              "zeitpunkt des letzten service ist nicht bekannt",
              "bewerte sie nicht als gut oder schlecht")


def _treffer(text: str, muster) -> list[str]:
    """Verbotene Formulierungen — aber NUR dort, wo sie behaupten statt verbieten."""
    treffer = []
    for zeile in (text or "").splitlines():
        n = zeile.lower()
        if any(a in n for a in _ANWEISUNG):
            continue
        treffer += [m for m in muster if re.search(m, n)]
    return treffer


def texte_von(ctx: Laufleistungskontext | None) -> str:
    """Alles, was P2-5 an Text erzeugt: die Hinweise und der Prompt-Block."""
    if ctx is None:
        return ""
    return "\n".join([w.hinweis for w in ctx.wartungshinweise] + [prompt_block(ctx)])


def alle_aktionen(ak) -> list:
    return [a for liste in (ak.besichtigung, ak.probefahrt, ak.verkaeuferfragen,
                            ak.dokumente)
            for a in list(liste.fahrzeugspezifisch) + list(liste.basis)]


def aktionstexte(ak, *, nur_wartung: bool = False) -> str:
    """Aktionstexte als ein Block.

    `nur_wartung=True` grenzt auf die von P2-5 beeinflussten Punkte ein. Die
    Fälligkeitsprüfung gehört dorthin: der Basiskatalog enthält seit P1-3 einen
    völlig korrekten Satz über eine "fällige Hauptuntersuchung" — die HU ist
    zeitgesteuert und hat mit dem hier verbotenen Wartungs-Fälligkeitsschluss
    nichts zu tun.
    """
    return "\n".join(f"{a.titel} {a.aktion} {a.hinweis or ''}"
                      for a in alle_aktionen(ak)
                      if not nur_wartung or a.kategorie == "wartung")


def wartungszeilen(ctx) -> list[str]:
    """Die Wartungszeilen des Prompt-Blocks — ohne die Kopf-/Verbotszeilen, die
    das Wort "Wartungspunkt" naturgemäß ebenfalls enthalten."""
    return [z for z in prompt_block(ctx).splitlines() if z.startswith("Wartungspunkt „")]


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A) Fahrzeugalter ===")

check("A1 2020er Fahrzeug ist 2026 ungefähr 6 Jahre alt",
      fahrzeugalter(2020, heute_jahr=2026) == 6)
check("A2 Baujahr = aktuelles Jahr -> Alter 0", fahrzeugalter(2026, heute_jahr=2026) == 0)
check("A3 Baujahr in der Zukunft -> None statt negativem Alter",
      fahrzeugalter(2030, heute_jahr=2026) is None)
check("A4 kein Baujahr -> None", fahrzeugalter(None, heute_jahr=2026) is None)
check("A5 Unsinns-Baujahr -> None", fahrzeugalter(1600, heute_jahr=2026) is None)
check("A6 nicht-numerisches Baujahr kippt nicht",
      fahrzeugalter("zwanzigzwanzig", heute_jahr=2026) is None)
_ctxA = build_laufleistungskontext(Req(baujahr=2020, kilometerstand=90_000), [], heute_jahr=HEUTE)
check("A7 Alter erscheint im Kontext", _ctxA.fahrzeugalter_jahre == 6)
check("A8 der Prompt kennzeichnet das Alter ausdrücklich als ungefähr",
      "ungefähr 6 Jahre" in prompt_block(_ctxA))
check("A9 der Prompt nennt den fehlenden Erstzulassungsmonat",
      "Erstzulassung" in prompt_block(_ctxA))
check("A10 keine Monats-Scheinpräzision im Kontext",
      not re.search(r"\d+[,.]\d+\s*Jahre", prompt_block(_ctxA)))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== B) km pro Jahr ===")

check("B1 90.000 km / 6 Jahre = 15.000 km/Jahr", km_pro_jahr(90_000, 6) == 15_000)
check("B2 118.000 km / 17 Jahre auf 100 gerundet", km_pro_jahr(118_000, 17) == 6_900)
check("B3 Alter 0 -> kein Wert (keine Division durch die Erstperiode)",
      km_pro_jahr(30_000, 0) is None)
check("B4 kein Kilometerstand -> None", km_pro_jahr(None, 5) is None)
check("B5 unplausibler Kilometerstand -> None", km_pro_jahr(9_000_000, 5) is None)
check("B6 im Kontext korrekt", _ctxA.km_pro_jahr == 15_000)
check("B7 der Prompt nennt es ausdrücklich als Durchschnitt",
      "Durchschnittliche Fahrleistung seit dem Baujahr" in prompt_block(_ctxA))
check("B8 der Prompt behauptet NICHT die Fahrweise eines Vorbesitzers",
      "Vorbesitzer" not in prompt_block(_ctxA).split("Mittelwert")[0])
check("B9 der Prompt kennzeichnet den Wert als Mittelwert über die Lebensdauer",
      "Mittelwert über die gesamte" in prompt_block(_ctxA))

check("B10 Schwelle 'niedrig' stammt aus dem Bestand (key_findings)",
      SCHWELLE_NIEDRIG == 10_000 and REFERENZ_DURCHSCHNITT == 15_000)

# Nachtrag zur ursprünglichen Fassung: das Modul erzeugte aus SCHWELLE_NIEDRIG/
# REFERENZ_DURCHSCHNITT eine dreistufige Einordnung ("niedrig"/"durchschnittlich"
# /"erhoeht") inkl. einer frei gespiegelten dritten Grenze (SCHWELLE_ERHOEHT).
# Für keinen der drei Werte existiert im Projekt eine zitierte fachliche
# Quelle — es sind interne Phase-2-Heuristiken, die vor P2-5 nur EINEN
# einseitigen Vergleich trugen ("<= 10.000 -> Vorteil"), keine Klassifikation.
# Diese Prüfungen stellen sicher, dass die Klassifikation vollständig entfernt
# bleibt und nur noch die nackte Zahl ausgegeben wird.
check("B11 keine Einordnungs-Konstanten mehr im Modul",
      not any(hasattr(_L, n) for n in
              ("EINORDNUNG_NIEDRIG", "EINORDNUNG_DURCHSCHNITTLICH",
               "EINORDNUNG_ERHOEHT", "SCHWELLE_ERHOEHT", "MIN_ALTER_EINORDNUNG")))
check("B12 keine Einordnungsfunktion mehr im Modul",
      not hasattr(_L, "einordnung"))
check("B13 key_findings nutzt weiterhin dieselbe Konstante",
      __import__("app.key_findings", fromlist=["x"]).SCHWELLE_NIEDRIG is SCHWELLE_NIEDRIG)
check("B14 das Modell trägt kein Einordnungsfeld mehr",
      "laufleistungs_einordnung" not in Laufleistungskontext.model_fields)
_ctxB = build_laufleistungskontext(Req(baujahr=2025, kilometerstand=30_000), [], heute_jahr=HEUTE)
check("B15 einjähriges Fahrzeug: km/Jahr vorhanden, keine Klassifikation möglich",
      _ctxB.km_pro_jahr == 30_000)
# "durchschnittlich"/"gut"/"schlecht" dürfen im Prompt-Block vorkommen — als
# Wort "durchschnittliche Fahrleistung" (eine Berechnungsgröße, kein Label) und
# in der ausdrücklichen Anweisung "NICHT als gut oder schlecht" bewerten. Genau
# darum geht es: KEIN eigenständiges Klassifikations-LABEL wie "niedrig" oder
# "erhöht" als Attribut der Fahrleistung, keine der drei ehemaligen Stufen.
check("B16 keine Einordnungs-LABELS ('niedrig'/'erhöht') im Prompt-Block",
      not any(w in prompt_block(_ctxA).lower() for w in ("niedrig", "erhöht", "erhoeht")))
check("B16b 'gut'/'schlecht' kommen NUR im Bewertungsverbot vor, nicht als Urteil",
      _treffer(prompt_block(_ctxA), (r"\bgut\b", r"\bschlecht\b")) == [])
check("B17 der Prompt zeigt km/Jahr als reinen Durchschnittswert",
      "grober Durchschnitt seit dem Baujahr" in prompt_block(_ctxA))
check("B18 der Prompt verbietet ausdrücklich eine Gut/Schlecht-Bewertung",
      "NICHT als gut oder schlecht" in prompt_block(_ctxA))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C/D) Keine Fälligkeit ohne bekannten letzten Service ===")

_ins_zahn = [db_wartung("Zahnriemen", "120.000 km")]
_ctxC = build_laufleistungskontext(Req(baujahr=2009, kilometerstand=118_000),
                                   _ins_zahn, heute_jahr=HEUTE)
check("C1 letzter Service ist ausdrücklich als unbekannt markiert",
      _ctxC.letzter_service_bekannt is False)
check("C2 der Prompt sagt die Unwissenheit ausdrücklich",
      "letzten Service ist NICHT bekannt" in prompt_block(_ctxC))
check("C3 kein verbotenes Fälligkeitswort in irgendeinem erzeugten Text",
      _treffer(texte_von(_ctxC), VERBOTEN_FAELLIGKEIT) == [])
check("C4 der Hinweistext verlangt einen NACHWEIS statt zu behaupten",
      "Nachweis" in _ctxC.wartungshinweise[0].hinweis)
check("C5 der Prompt verbietet die Fälligkeitsaussage ausdrücklich",
      "niemals" in prompt_block(_ctxC).lower() and "überfällig" in prompt_block(_ctxC))
check("C6 der Prompt verbietet die 'Servicehistorie fehlt'-Aussage",
      "Servicehistorie fehle" in prompt_block(_ctxC))

# D) wartung_oel_km ist NICHT Teil der Eingabe dieses Moduls — strukturell.
_sig = inspect.signature(build_laufleistungskontext).parameters
check("D1 build_laufleistungskontext kennt keinen Ölintervall-Parameter",
      not any("oel" in p.lower() or "fahrzeugkontext" in p.lower() for p in _sig))
_quelle = inspect.getsource(__import__("app.laufleistung", fromlist=["x"]))
_code_ohne_doku = "\n".join(z for z in _quelle.splitlines()
                            if not z.strip().startswith("#"))
check("D2 das Modul liest `wartung_oel_km` an keiner Stelle im Code",
      "wartung_oel_km" not in _code_ohne_doku.split('"""')[-1])
_ctxD = build_laufleistungskontext(Req(baujahr=2018, kilometerstand=160_000), [], heute_jahr=HEUTE)
check("D3 ohne Wartungs-Evidence entsteht KEIN Wartungshinweis",
      _ctxD.wartungshinweise == [])
check("D4 ... aber Alter und km/Jahr bleiben verfügbar",
      _ctxD.fahrzeugalter_jahre == 8 and _ctxD.km_pro_jahr == 20_000)
check("D5 kein verbotenes Wort im Ölintervall-losen Fall",
      _treffer(texte_von(_ctxD), VERBOTEN_FAELLIGKEIT) == [])
_ak_d = build_kaufaktionen(Req(baujahr=2018, kilometerstand=160_000), None, None, [],
                           laufleistungskontext=_ctxD)
check("D6 ohne Wartungs-Evidence entsteht auch keine Wartungsaktion",
      not [a for liste in (_ak_d.verkaeuferfragen, _ak_d.dokumente)
           for a in liste.fahrzeugspezifisch if a.kategorie == "wartung"])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== E) Wartungspunkt 120.000 km, Fahrzeug 118.000 km ===")

_wE = _ctxC.wartungshinweise
check("E1 genau ein Wartungshinweis", len(_wE) == 1)
check("E2 Status 'im relevanten Bereich'", _wE[0].status == STATUS_IM_BEREICH)
check("E3 der hinterlegte Punkt ist korrekt übernommen", _wE[0].punkt_km == 120_000)
check("E4 Differenz deterministisch berechnet", _wE[0].differenz_km == -2_000)
check("E5 der Text nennt den relevanten Bereich",
      "relevanten Bereich" in _wE[0].hinweis)
check("E6 der Text verlangt den Nachweis", "Nachweis" in _wE[0].hinweis)
check("E7 der Text sagt ausdrücklich, dass die Durchführung unbekannt ist",
      "geht aus den vorliegenden Daten nicht hervor" in _wE[0].hinweis)
check("E8 keine Fälligkeitsbehauptung",
      _treffer(_wE[0].hinweis, VERBOTEN_FAELLIGKEIT) == [])
check("E9 Evidence-ID ist die echte Insight-ID", _wE[0].evidence_id == "wartung-1")
check("E10 Herkunft korrekt als DB gekennzeichnet", _wE[0].herkunft == "db_wartung")
check("E11 der Originaltext des Intervalls bleibt erhalten",
      _wE[0].intervall_text == "120.000 km")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== F) Wartungspunkt 120.000 km, Fahrzeug 160.000 km ===")

_ctxF = build_laufleistungskontext(Req(baujahr=2009, kilometerstand=160_000),
                                   [db_wartung("Zahnriemen", "120.000 km")], heute_jahr=HEUTE)
_wF = _ctxF.wartungshinweise[0]
check("F1 Status 'darueber'", _wF.status == STATUS_DARUEBER)
check("F2 der Text formuliert eine PRÜFUNG, keine Feststellung",
      "sollte geprüft werden, ob die Wartung bereits durchgeführt wurde" in _wF.hinweis)
check("F3 'darueber' behauptet NICHT 'nicht gemacht'",
      _treffer(_wF.hinweis, VERBOTEN_FAELLIGKEIT) == [])
check("F4 der Text sagt ausdrücklich, dass die Daten dazu schweigen",
      "sagen darüber nichts aus" in _wF.hinweis)
check("F5 Differenz korrekt", _wF.differenz_km == 40_000)
check("F6 kein Modulo: verglichen wird gegen den ERSTEN Punkt, nicht 160.000 % 120.000",
      _wF.punkt_km == 120_000 and "40.000 km zurück" in _wF.hinweis)
_src = inspect.getsource(__import__("app.laufleistung", fromlist=["x"]))
def _nur_code(quelltext: str) -> str:
    """Quelltext ohne Docstrings und ohne Kommentarzeilen.

    Nötig, weil der Modulkopf `kilometerstand % intervall` ausdrücklich zitiert —
    als die Rechnung, die bewusst NICHT gebaut wurde. Eine Prüfung über den
    Rohtext würde genau diese Dokumentation als Verstoß werten.
    """
    ausserhalb = quelltext.split('"""')[::2]
    return "\n".join(z for z in "\n".join(ausserhalb).splitlines()
                     if not z.strip().startswith("#"))


# Formatplatzhalter (`log.info("... %s ...")`) sind keine Rechnung und werden
# vor der Prüfung entfernt — was danach an `%` übrig bleibt, wäre ein Operator.
_code = re.sub(r"%[sdrif]", "", _nur_code(_src))
check("F7 im ausführbaren Code wird nirgends modulo gerechnet",
      "%" not in _code)
check("F8 die Dokumentation benennt das Modulo-Verbot trotzdem ausdrücklich",
      "kilometerstand % intervall" in _src)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== G) Wartungspunkt weit entfernt ===")

_ctxG = build_laufleistungskontext(Req(baujahr=2020, kilometerstand=40_000),
                                   [db_wartung("Zahnriemen", "120.000 km")], heute_jahr=HEUTE)
check("G1 kein Wartungshinweis ohne Anlass", _ctxG.wartungshinweise == [])
check("G2 Status-Funktion liefert 'entfernt'",
      status_zu_punkt(40_000, 120_000, None) == STATUS_ENTFERNT)
check("G3 der Prompt-Block nennt trotzdem Alter und km/Jahr",
      "Fahrzeugalter" in prompt_block(_ctxG) and "Fahrleistung" in prompt_block(_ctxG))
check("G4 aber keine Wartungszeile", wartungszeilen(_ctxG) == [])
_ak_g = build_kaufaktionen(Req(baujahr=2020, kilometerstand=40_000), None, None,
                           [db_wartung("Zahnriemen", "120.000 km")],
                           laufleistungskontext=_ctxG)
_frage_g = [a for a in _ak_g.verkaeuferfragen.fahrzeugspezifisch if "Zahnriemen" in a.titel]
check("G5 die allgemeine Wartungsfrage bleibt (aus der Evidence), ohne km-Dramatik",
      len(_frage_g) == 1 and "relevanten Bereich" not in _frage_g[0].aktion)
check("G6 kein verbotenes Wort in den Wartungsaktionen",
      _treffer(aktionstexte(_ak_g, nur_wartung=True), VERBOTEN_FAELLIGKEIT) == [])

# Die vier Stufen an EINEM Punkt, damit die Grenzen bewusst gesetzt bleiben.
check("G7 Stufe 'naehert_sich' unterhalb des Fensters",
      status_zu_punkt(100_000, 120_000, None) == STATUS_NAEHERT_SICH)
check("G8 Spanne: innerhalb der Spanne ist 'im_bereich'",
      status_zu_punkt(200_000, 150_000, 250_000) == STATUS_IM_BEREICH)
check("G9 Spanne: erst über dem oberen Wert wird es 'darueber'",
      status_zu_punkt(280_000, 150_000, 250_000) == STATUS_DARUEBER)
check("G10 kleines Intervall bekommt ein kleines Fenster (Untergrenze 5.000 km)",
      status_zu_punkt(16_000, 20_000, None) == STATUS_IM_BEREICH
      and status_zu_punkt(13_000, 20_000, None) == STATUS_NAEHERT_SICH
      and status_zu_punkt(8_000, 20_000, None) == STATUS_ENTFERNT)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== H) Falsches Baujahr (P0-2) ===")

# P0-2 wirkt VOR diesem Modul: passt das Baujahr nicht zum Bauzeitraum, liefert
# `find_baureihe_mit_vertrauen` keine belastbare Baureihe, `build_insights`
# erzeugt keinen Wartungs-Insight — und ohne Insight kann hier nichts entstehen.
# Genau das wird hier nachgestellt: leere Evidence trotz vorhandener Laufleistung.
_ctxH = build_laufleistungskontext(Req(baujahr=1985, kilometerstand=118_000), [],
                                   heute_jahr=HEUTE)
check("H1 ohne Wartungs-Evidence entsteht kein Wartungshinweis",
      _ctxH.wartungshinweise == [])
check("H2 ... obwohl der Kilometerstand im 'relevanten' Bereich läge",
      status_zu_punkt(118_000, 120_000, None) == STATUS_IM_BEREICH)
check("H3 das Modul erfindet keine eigene Baujahreslogik",
      "bauzeitraum" not in _src.lower().split('"""')[-1])
_ak_h = build_kaufaktionen(Req(baujahr=1985, kilometerstand=118_000), None, None, [],
                           laufleistungskontext=_ctxH)
check("H4 keine Wartungsaktion aus dem Nichts",
      not [a for a in _ak_h.dokumente.fahrzeugspezifisch if a.kategorie == "wartung"])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== I/J) Scheckheft ===")

_req_scheck = Req(marke="Audi", modell="A3", baujahr=2009, kilometerstand=118_000,
                  scheckheftgepflegt=True)
_ak_i = build_kaufaktionen(_req_scheck, None, None, _ins_zahn, laufleistungskontext=_ctxC)
_txt_i = aktionstexte(_ak_i)
check("I1 keine 'Servicehistorie fehlt'-Aussage bei scheckheftgepflegt=True",
      _treffer(_txt_i, VERBOTEN_HISTORIE) == []
      and _treffer(aktionstexte(_ak_i, nur_wartung=True), VERBOTEN_FAELLIGKEIT) == [])
_scheck = [a for a in _ak_i.dokumente.fahrzeugspezifisch if a.id.endswith("scheckheft")]
check("I2 die Scheckheft-Angabe erscheint als Inserat-Angabe", len(_scheck) == 1)
check("I3 sie wird als Angabe des Inserats gekennzeichnet, nicht als Tatsache",
      "Das Inserat gibt" in _scheck[0].aktion)
check("I4 sie bleibt eine PRÜF-Aktion", "prüfen" in _scheck[0].titel.lower()
      or "durchsehen" in _scheck[0].aktion)
check("J1 Nachweisprüfung bleibt trotz scheckheftgepflegt=True möglich",
      "Nachweis" in _txt_i or "Beleg" in _txt_i)
check("J2 die laufleistungsbezogene Nachweisaktion existiert daneben weiter",
      any(a.kategorie == "wartung" and "Zahnriemen" in a.titel
          for a in _ak_i.dokumente.fahrzeugspezifisch))
_req_ohne = Req(baujahr=2009, kilometerstand=118_000, scheckheftgepflegt=None)
_ak_j = build_kaufaktionen(_req_ohne, None, None, _ins_zahn, laufleistungskontext=_ctxC)
check("J3 fehlende Angabe bleibt eine FRAGE, keine Feststellung",
      any(a.titel.endswith("?") and "Scheckheft" in a.titel
          for a in _ak_j.verkaeuferfragen.fahrzeugspezifisch)
      and _treffer(aktionstexte(_ak_j), VERBOTEN_HISTORIE) == [])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== K/L/M) Web-Wartung und DB-Miss ===")

_ins_web = [web_wartung("Zahnriemen",
                        "Der Zahnriemen sollte bei diesem Motor alle 120.000 km "
                        "gewechselt werden.")]
_ctxK = build_laufleistungskontext(Req(baujahr=2009, kilometerstand=118_000),
                                   _ins_web, heute_jahr=HEUTE)
_wK = _ctxK.wartungshinweise[0]
check("K1 Web-Wartung erzeugt einen Wartungshinweis", _wK.status == STATUS_IM_BEREICH)
check("K2 die Quelle bleibt vollständig erhalten",
      _wK.quellen and _wK.quellen[0].url == "https://www.adac.de/beispiel")
check("K3 der Quellen-Typ bleibt 'web_technik' und wird nicht zu 'datenbank'",
      _wK.quellen[0].typ == "web_technik")
check("K4 Herkunft ist als Web gekennzeichnet", _wK.herkunft == "web_wartung")
check("K5 die Herkunft steht im TEXT, nicht nur im Feld (Ausdruck ohne Oberfläche)",
      "aus der Webrecherche" in _wK.hinweis)
check("K6 Evidence-ID ist die echte Web-Insight-ID", _wK.evidence_id == "web-wartung-1")
check("K7 keine Fälligkeitsbehauptung aus Webquellen",
      _treffer(_wK.hinweis, VERBOTEN_FAELLIGKEIT) == [])

# L) DB-Miss: keine Baureihe, kein Motor — nur Web-Evidence.
_req_L = Req(marke="Dacia", modell="Duster", baujahr=2015, kilometerstand=118_000)
_ctxL = build_laufleistungskontext(_req_L, _ins_web, heute_jahr=HEUTE)
_ak_l = build_kaufaktionen(_req_L, None, None, _ins_web, laufleistungskontext=_ctxL)
check("L1 DB-Miss + Web-Wartung -> Wartungskontext entsteht",
      len(_ctxL.wartungshinweise) == 1)
check("L2 Alter und km/Jahr funktionieren ohne DB-Treffer",
      _ctxL.fahrzeugalter_jahre == 11 and _ctxL.km_pro_jahr == 10_700)
check("L3 die Aktion trägt die Web-Evidence-ID",
      any("web-wartung-1" in a.evidence_ids
          for a in _ak_l.dokumente.fahrzeugspezifisch))
check("L4 der Web-Schlüssel kollidiert nicht mit dem DB-Schlüssel",
      any(a.id == "dokument-wartung-web-zahnriemen"
          for a in _ak_l.dokumente.fahrzeugspezifisch))

# M) DB-Miss OHNE jede Wartungs-Evidence.
_ctxM = build_laufleistungskontext(_req_L, [schwachstelle("Turbolader")], heute_jahr=HEUTE)
check("M1 keine Wartungs-Evidence -> keine Wartungsbehauptung",
      _ctxM.wartungshinweise == [])
check("M2 der Rest des Kontexts läuft weiter",
      _ctxM.kilometerstand == 118_000 and _ctxM.fahrzeugalter_jahre == 11)
check("M3 eine Schwachstellen-Evidence wird NICHT zur Wartung umgedeutet",
      wartungszeilen(_ctxM) == [])
check("M4 kein verbotenes Wort",
      _treffer(texte_von(_ctxM), VERBOTEN_FAELLIGKEIT) == [])

# DB gewinnt gegen Web bei gleichem Bauteil.
_ctxKL = build_laufleistungskontext(Req(baujahr=2009, kilometerstand=118_000),
                                    _ins_zahn + _ins_web, heute_jahr=HEUTE)
check("M5 derselbe Wartungspunkt aus DB und Web ergibt EINEN Hinweis",
      len(_ctxKL.wartungshinweise) == 1)
check("M6 ... und zwar den geprüften aus der Datenbank",
      _ctxKL.wartungshinweise[0].herkunft == "db_wartung")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== N) No-Market ===")

_req_N = Req(marke="Audi", modell="A3", baujahr=2009, kilometerstand=160_000, preis_eur=8_900)
_ctxN1 = build_laufleistungskontext(_req_N, _ins_zahn, heute_jahr=HEUTE)
_req_N2 = Req(marke="Audi", modell="A3", baujahr=2009, kilometerstand=160_000, preis_eur=None)
_ctxN2 = build_laufleistungskontext(_req_N2, _ins_zahn, heute_jahr=HEUTE)
check("N1 Kontext ist identisch, ob ein Angebotspreis vorliegt oder nicht",
      _ctxN1.model_dump() == _ctxN2.model_dump())
check("N2 Prompt-Block identisch", prompt_block(_ctxN1) == prompt_block(_ctxN2))
_ak_n1 = build_kaufaktionen(_req_N, None, None, _ins_zahn, laufleistungskontext=_ctxN1)
_ak_n2 = build_kaufaktionen(_req_N2, None, None, _ins_zahn, laufleistungskontext=_ctxN2)
check("N3 auch die Kaufaktionen sind identisch",
      _ak_n1.model_dump() == _ak_n2.model_dump())


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== O) Keine Preisbehauptung ===")

check("O1 die Funktion nimmt strukturell keinen Preis- oder Marktparameter",
      not any(w in p.lower() for p in _sig
              for w in ("preis", "markt", "price", "median")))
# Nur die von P2-5 erzeugten Texte: der Basiskatalog spricht seit P1-3
# legitim über Kaufvertrag und Kosten, das ist nicht Gegenstand dieses Tickets.
_o_text = (texte_von(_ctxN1) + "\n" + aktionstexte(_ak_n1, nur_wartung=True)).lower()
_PREISWORTE = (r"\bgünstig", r"\bguenstig", r"\bteuer", r"preisabschlag", r"marktwert",
               r"nachverhandeln", r"verhandlungsspielraum", r"wertminderung",
               r"€", r"\beur\b", r"\bpreis\b")
check("O2 kein Preiswort in Kontext oder Wartungsaktionen",
      _treffer(_o_text, _PREISWORTE) == [])
check("O3 der Prompt verbietet die Preisableitung ausdrücklich",
      "KEINE Preisaussage" in prompt_block(_ctxN1))
check("O4 das Modul importiert weder Marktvergleich noch Preisurteil",
      "marktvergleich" not in _src and "preisurteil" not in _src)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== P) Kaufaktionen und Evidence-IDs ===")

_req_P = Req(marke="Audi", modell="A3", baujahr=2009, kilometerstand=160_000,
             scheckheftgepflegt=True, tuev_bis="06/2027", unfallfrei="ja", vorbesitzer=2)
_ctxP = build_laufleistungskontext(_req_P, _ins_zahn, heute_jahr=HEUTE)
_ak_p = build_kaufaktionen(_req_P, None, None, _ins_zahn, laufleistungskontext=_ctxP)
_gueltig = {i.id for i in _ins_zahn}
_alle_p = [a for liste in (_ak_p.besichtigung, _ak_p.probefahrt, _ak_p.verkaeuferfragen,
                           _ak_p.dokumente)
           for a in list(liste.fahrzeugspezifisch) + list(liste.basis)]
check("P1 jede referenzierte Evidence-ID existiert wirklich",
      all(ev in _gueltig for a in _alle_p for ev in a.evidence_ids))
_wartung_p = [a for a in _alle_p if a.kategorie == "wartung"]
check("P2 die Wartungsaktionen tragen eine Evidence-ID (nie leer)",
      _wartung_p and all(a.evidence_ids for a in _wartung_p))
check("P3 genau eine Wartungsaktion je Bereich (kein Duplikat durch P2-5)",
      len([a for a in _ak_p.verkaeuferfragen.fahrzeugspezifisch if a.kategorie == "wartung"]) == 1
      and len([a for a in _ak_p.dokumente.fahrzeugspezifisch if a.kategorie == "wartung"]) == 1)
check("P4 der laufleistungsbezogene, konkretere Text gewinnt",
      any("liegt rund" in a.aktion for a in _ak_p.dokumente.fahrzeugspezifisch))
check("P5 keine Besichtigungs-/Probefahrtaktion aus einem Wartungspunkt",
      not [a for a in list(_ak_p.besichtigung.fahrzeugspezifisch)
           + list(_ak_p.probefahrt.fahrzeugspezifisch) if a.kategorie == "wartung"])
_ak_ohne = build_kaufaktionen(_req_P, None, None, _ins_zahn)
check("P6 ohne P2-5-Kontext verhält sich build_kaufaktionen exakt wie zuvor",
      len(_ak_ohne.dokumente.fahrzeugspezifisch)
      == len(_ak_p.dokumente.fahrzeugspezifisch))
check("P7 die Basislisten wachsen durch P2-5 nicht",
      len(_ak_ohne.dokumente.basis) == len(_ak_p.dokumente.basis)
      and len(_ak_ohne.verkaeuferfragen.basis) == len(_ak_p.verkaeuferfragen.basis))
check("P8 der relevante Wartungspunkt steht über der reinen Angaben-Aktion",
      max((a.rang for a in _ak_p.dokumente.fahrzeugspezifisch if a.kategorie == "wartung"),
          default=0)
      > max((a.rang for a in _ak_p.dokumente.fahrzeugspezifisch
             if a.id.endswith("scheckheft")), default=0))
check("P9 ein erreichter Wartungspunkt wird NICHT zu 'kritisch' hochgestuft",
      all(a.prioritaet != "kritisch" for a in _wartung_p))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== Q) Parser, Invarianten, Regression ===")

_PARSER_FAELLE = [
    ("120.000 km", (120_000, None)),
    ("Alle 60.000 km", (60_000, None)),
    ("alle 60.000 km oder 4 Jahre", (60_000, None)),
    ("90.000 - 120.000 km oder alle 5-6 Jahre", (90_000, 120_000)),
    ("Ca. 150.000 - 250.000 km", (150_000, 250_000)),
    ("~50-80 tkm", (50_000, 80_000)),
    ("Prüfung ab 100.000 km", (100_000, None)),
    ("15.000 km oder jährlich", (15_000, None)),
    ("120.000 km / 180.000 km (je nach Motorcode)", (120_000, None)),
    ("60000", (60_000, None)),
    ("Alle 2 Jahre", None),
    ("2 Jahre", None),
    ("Jährlich", None),
    ("Kein fester Intervall", None),
    ("Bei Geräuschen prüfen", None),
    ("Nach Bedarf", None),
    ("Sichtprüfung bei jeder Inspektion", None),
    ("Regelmäßige Diagnose", None),
    ("", None),
    (None, None),
]
for _text, _erwartet in _PARSER_FAELLE:
    check(f"Q-parse {_text!r} -> {_erwartet}", parse_wartungspunkt(_text) == _erwartet)

check("Q1 eine reine Jahreszahl wird NICHT als Kilometerpunkt gelesen",
      parse_wartungspunkt("2018") is None)
check("Q2 eine Zeitangabe wird nie gegen den Kilometerstand gerechnet",
      build_laufleistungskontext(Req(baujahr=2009, kilometerstand=118_000),
                                 [db_wartung("Bremsflüssigkeit", "Alle 2 Jahre")],
                                 heute_jahr=HEUTE).wartungshinweise == [])
check("Q3 der freie Hinweistext erzeugt keinen Wartungspunkt",
      build_laufleistungskontext(
          Req(baujahr=2009, kilometerstand=118_000),
          [db_wartung("Dieselpartikelfilter", "Nach Bedarf",
                      hinweis="Bei Kurzstrecke unter 20.000 km verstopft der DPF.")],
          heute_jahr=HEUTE).wartungshinweise == [])

# Die Lehre aus P1-3: ein normalisiertes Muster muss selbst normalisiert sein,
# sonst greift es bei jedem Bauteil mit Umlaut nie.
check("Q4 Bauteil-Normalisierung löst Umlaute auf",
      norm_bauteil("Zündspulen") == "zuendspulen"
      and norm_bauteil("AGR-Kühler") == "agr kuehler")
check("Q5 DB- und Web-Schreibweise ergeben denselben Dedup-Schlüssel",
      norm_bauteil("Zahnriemen") == norm_bauteil(" zahnriemen "))

check("Q6 ohne Kilometerstand entstehen keine Wartungshinweise",
      build_laufleistungskontext(Req(baujahr=2009), _ins_zahn,
                                 heute_jahr=HEUTE).wartungshinweise == [])
check("Q7 komplett leerer Request -> None statt eines leeren Objekts",
      build_laufleistungskontext(Req(), [], heute_jahr=HEUTE) is None)
check("Q8 unplausibler Kilometerstand wird verworfen, ohne zu kippen",
      build_laufleistungskontext(Req(baujahr=2009, kilometerstand=99_000_000), _ins_zahn,
                                 heute_jahr=HEUTE).kilometerstand is None)
check("Q9 mehr als MAX_HINWEISE Punkte werden begrenzt",
      len(build_laufleistungskontext(
          Req(baujahr=2000, kilometerstand=200_000),
          [db_wartung(f"Bauteil {n}", "120.000 km", insight_id=f"wartung-{n}")
           for n in range(12)], heute_jahr=HEUTE).wartungshinweise) == 6)
check("Q10 die Reihenfolge ist deterministisch",
      [w.bauteil for w in build_laufleistungskontext(
          Req(baujahr=2000, kilometerstand=200_000),
          [db_wartung("Zahnriemen", "120.000 km", insight_id="wartung-1"),
           db_wartung("Ölwechsel", "190.000 km", insight_id="wartung-2")],
          heute_jahr=HEUTE).wartungshinweise] == ["Zahnriemen", "Ölwechsel"])

_roh = {"bericht": "", "empfehlung": "unbekannt", "preis_bewertung": "unbekannt",
        "quelle": "datenbank", "vertrauen": "hoch"}
check("Q11 alte Checks ohne das neue Feld bleiben ladbar",
      KaufCheckResponse(**_roh).laufleistungskontext is None)
check("Q12 das neue Feld überlebt einen Serialisierungs-Rundlauf",
      KaufCheckResponse(**{**_roh, "laufleistungskontext": _ctxC})
      .model_dump()["laufleistungskontext"]["wartungshinweise"][0]["evidence_id"]
      == "wartung-1")
check("Q13 leerer Kontext erzeugt leeren Prompt-Block", prompt_block(None) == "")


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE KAUFCHECK-P2-5-TESTS GRUEN")
