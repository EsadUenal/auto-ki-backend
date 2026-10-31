"""
ENFAL-Folgeauftrag ("offene regionale Gültigkeit bis zum Nutzerbericht
erhalten", 2026-11): dauerhafte, gezielte Regression für die additive
Evidenzmodell-Erweiterung `WebFakt.herkunftsregion` / `WebFakt.regionale_geltung`
(app/models.py) und deren Durchreichung bis in den sichtbaren Bericht
(app/technical_research.py:`_wartung_herkunftsregion()`,
app/evidence.py:`_web_beschreibung()`).

Nachgewiesene Lücke (Kontextübergabe 2026-10-31, real am Dokument
tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf, "Lexus Garantie- und
Serviceheft", Schweiz): ein Wartungsintervall aus einer vertrauten
Herstellerdomain mit abweichender Landes-TLD lief bisher UNEINGESCHRÄNKT
gültig durch den Bericht -- `geltung_fuer_fahrzeug`/`geltungsbereich` bilden
ausschließlich das BAUJAHR-Fenster der Quelle ab, keine Region.

NUR kategorie=="wartung" (Auftragsscope). Domain-Herkunft wird NIE zu einer
Ungeeignetheitsaussage -- nur "unresolved" (Quelle bleibt gültig, nur die
Übertragbarkeit auf das deutsche Zielfahrzeug ist ungeklärt).

    python scripts/test_isolated.py test_wartung_regionale_geltung.py
"""
from app.evidence import _web_beschreibung
from app.models import WebFakt
from app.technical_research import _extrahiere_fakten, _wartung_herkunftsregion

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


# ── A) Reales Schweizer Dokument: Herkunft + offene Übertragbarkeit erkannt ──
_LEXUS_CH_REAL = treffer(
    "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
    "Lexus Garantie- und Serviceheft (Schweiz, Deutsch) -- Lexus Care-Wartungsplan",
    "Lexus RC F zusätzliche Inspektionen laut Lexus Care-Wartungsplan: "
    "Automatikgetriebeöl alle 60.000 km oder 4 Jahre.")
fakten_a = _extrahiere_fakten([_LEXUS_CH_REAL], "wartung", marke="Lexus", modell="RC F", baujahr=2017)
check("A1) genau ein Fakt erkannt (Automatikgetriebeöl)",
      len(fakten_a) == 1, str(fakten_a))
check("A2) herkunftsregion benennt die Domainendung, kein geratenes Land",
      bool(fakten_a) and fakten_a[0].herkunftsregion == "Domainendung .ch (nicht .de)",
      str(fakten_a[0].herkunftsregion if fakten_a else None))
check("A3) regionale_geltung bleibt 'unresolved' (keine Ungeeignetheit)",
      bool(fakten_a) and fakten_a[0].regionale_geltung == "unresolved",
      str(fakten_a[0].regionale_geltung if fakten_a else None))
text_a = _web_beschreibung(fakten_a[0], 2017) if fakten_a else ""
check("A4) die offene Übertragbarkeit ist im sichtbaren Hinweistext verständlich "
      "(Herkunft benannt, Geltung für Deutschland ausdrücklich offen, kein Kaufverbot)",
      "Herkunft laut Quelle: Domainendung .ch" in text_a
      and "nicht bestätigt" in text_a and "Deutschland" in text_a,
      text_a)

# ── B) Passender Herstellerbeleg mit .de-Domain: KEIN falscher Unsicherheitshinweis ─
_BMW_DE = treffer(
    "https://www.bmw.de/wartungsplan/3er", "BMW 3er Wartungsplan",
    "Beim BMW 3er gilt der Zwischenservice: alle 25.000 km oder einmal jährlich. "
    "Dieser Service umfasst einen Bremsflüssigkeitswechsel sowie eine Sichtprüfung.")
fakten_b = _extrahiere_fakten([_BMW_DE], "wartung", marke="BMW", modell="3er", baujahr=2020)
check("B1) .de-Herstellerquelle bekommt KEINE herkunftsregion",
      bool(fakten_b) and fakten_b[0].herkunftsregion is None and fakten_b[0].regionale_geltung is None,
      str(fakten_b))
text_b = _web_beschreibung(fakten_b[0], 2020) if fakten_b else ""
check("B2) kein Regional-Hinweis im Bericht für die .de-Quelle",
      "Herkunft laut Quelle" not in text_b, text_b)

# ── C) EU-Domain: Zielmarktvertrag (Deutschland liegt in der EU) -- kein Flag ──
_HERSTELLER_EU = treffer(
    "https://www.lexus.eu/wartungsplan", "Lexus EU Wartungsplan",
    "Beim Lexus RC F gilt der Zwischenservice: alle 15.000 km oder einmal jährlich. "
    "Dieser Service umfasst einen Ölwechsel sowie eine Sichtprüfung.")
fakten_c = _extrahiere_fakten([_HERSTELLER_EU], "wartung", marke="Lexus", modell="RC F", baujahr=2017)
check("C1) .eu-Domain zählt laut vorhandenem Zielmarktvertrag (_ZIELMARKT_ORTE) "
      "als Zielmarkt, nicht als Fremdmarkt -- kein Flag",
      bool(fakten_c) and fakten_c[0].herkunftsregion is None and fakten_c[0].regionale_geltung is None,
      str(fakten_c))

# ── D) Fehlende regionale Information erfindet kein Land ────────────────────
check("D1) generische .com-Domain ohne Landes-TLD -> kein Flag, kein geratenes Land",
      _wartung_herkunftsregion("https://www.repareo.com/lexus-rc-f-service") == (None, None))
check("D2) ICANN-Dot-Brand-Endung (.toyota) ist keine Landes-TLD -> kein Flag",
      _wartung_herkunftsregion("https://global.toyota/service") == (None, None))
check("D3) keine URL -> kein Flag",
      _wartung_herkunftsregion(None) == (None, None) and _wartung_herkunftsregion("") == (None, None))
check("D4) mehrteiliges Public Suffix (.co.uk) ist keine einzelne Landes-TLD -> kein Flag",
      _wartung_herkunftsregion("https://www.example.co.uk/service") == (None, None))

# ── E) Scope-Isolierung: NUR kategorie=="wartung" bekommt die neuen Felder ──
_SCHWACHSTELLE_CH = treffer(
    "https://tools.lexus.ch/fileadmin/schwachstelle.pdf", "Lexus RC F Schwachstelle",
    "Beim Lexus RC F tritt gelegentlich Ölverlust an der Ölwanne auf.")
fakten_e = _extrahiere_fakten([_SCHWACHSTELLE_CH], "schwachstelle", marke="Lexus", modell="RC F",
                             baujahr=2017)
check("E1) kategorie=='schwachstelle' bekommt NIE herkunftsregion/regionale_geltung "
      "(additiv, Default unverändert außerhalb des Auftragsscope)",
      all(f.herkunftsregion is None and f.regionale_geltung is None for f in fakten_e),
      str(fakten_e))

# ── F) Default-Verhalten bei nicht gesetzten Feldern unverändert (Pydantic-Default) ─
_fakt_ohne_feld = WebFakt(kategorie="wartung", aussage="Test ohne Herkunftsfeld")
check("F1) WebFakt ohne explizite Herkunftsfelder bleibt None/None (altes Verhalten "
      "bestehender/gespeicherter Checks unverändert)",
      _fakt_ohne_feld.herkunftsregion is None and _fakt_ohne_feld.regionale_geltung is None)

print()
if _FEHLER:
    print(f"FEHLER: {len(_FEHLER)} Check(s) fehlgeschlagen: {_FEHLER}")
    raise SystemExit(1)
print("ALLE WARTUNG-REGIONALE-GELTUNG-TESTS GRUEN")
