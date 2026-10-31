"""
ENFAL-Folgeauftrag ("regionale Evidenz aus dem Dokument statt aus der
Domainendung", 2026-11): KORRIGIERT eine vorige, fehlerhafte Fassung dieser
Regression. Die vorige Fassung leitete `WebFakt.herkunftsregion`/
`regionale_geltung` aus der Domainendung der Quelle ab (z.B. "Domainendung
.ch (nicht .de)") -- eine ccTLD belegt aber WEDER den regionalen
Dokumentkontext NOCH dessen Übertragbarkeit; auch `.de`/`.eu` belegen keine
Zielgültigkeit (eine EU-Domainregistrierung ist keine Aussage über
europaweit gültige Wartungsvorschriften). Diese Fassung prüft stattdessen
NUR noch den tatsächlichen Dokumenttext (`_wartung_marktvorbehalt()`,
app/technical_research.py) -- die Domain spielt keine Rolle mehr.

Originalbeleg (Seite 4, "BETROFFENE MODELLE", Lexus-Schweiz-"Garantie- und
Serviceheft", tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf): "RC 300h
(Oktober 2015 ~) RC F (September 2014 ~) LC 500 (März 2017 ~) FALLS FÜR
IHREN MARKT ZUTREFFEND." -- das Dokument schränkt seine eigene Modellliste
AUSDRÜCKLICH mit einem Marktvorbehalt ein. Das ist der einzige erkannte,
eng gefasste Beleg -- KEINE Länder-/Spracherkennung.

    python scripts/test_isolated.py test_wartung_regionale_geltung.py
"""
from app.evidence import _web_beschreibung
from app.models import WebFakt
from app.technical_research import _extrahiere_fakten, _wartung_marktvorbehalt

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


_MARKTVORBEHALT_SATZ = (
    "Lexus RC F Wartungsplan: Automatikgetriebeöl alle 60.000 km oder 4 Jahre, "
    "falls für Ihren Markt zutreffend.")
_OHNE_VORBEHALT_SATZ = (
    "Lexus RC F Wartungsplan: Automatikgetriebeöl alle 60.000 km oder 4 Jahre.")

# ── A) Identischer Dokumentinhalt auf UNTERSCHIEDLICHEN Domains: keine
#      unterschiedliche Gültigkeit allein durch die URL ───────────────────
fakten_ch = _extrahiere_fakten(
    [treffer("https://tools.lexus.ch/heft.pdf", "Lexus Serviceheft", _MARKTVORBEHALT_SATZ)],
    "wartung", marke="Lexus", modell="RC F", baujahr=2017)
fakten_de = _extrahiere_fakten(
    [treffer("https://www.lexus.de/heft.pdf", "Lexus Serviceheft", _MARKTVORBEHALT_SATZ)],
    "wartung", marke="Lexus", modell="RC F", baujahr=2017)
check("A1) .ch und .de liefern bei IDENTISCHEM Dokumenttext dasselbe Ergebnis "
      "(regionale_geltung haengt NICHT von der Domain ab)",
      bool(fakten_ch) and bool(fakten_de)
      and fakten_ch[0].regionale_geltung == fakten_de[0].regionale_geltung == "unresolved"
      and fakten_ch[0].herkunftsregion == fakten_de[0].herkunftsregion,
      f"ch={fakten_ch} de={fakten_de}")

# ── B) Ausdrücklich PASSENDES Dokument auf .ch: kein pauschaler
#      Fremdmarkt-Warnhinweis (kein Vorbehalt im Text -> kein Flag) ────────
fakten_ch_ok = _extrahiere_fakten(
    [treffer("https://tools.lexus.ch/heft.pdf", "Lexus Serviceheft", _OHNE_VORBEHALT_SATZ)],
    "wartung", marke="Lexus", modell="RC F", baujahr=2017)
check("B1) .ch-Dokument OHNE Marktvorbehalt im Text -> KEIN Flag allein wegen der Domain",
      bool(fakten_ch_ok) and fakten_ch_ok[0].regionale_geltung is None
      and fakten_ch_ok[0].herkunftsregion is None,
      str(fakten_ch_ok))

# ── C) Ausdrücklich FREMDER Dokumentkontext auf .de: nicht allein wegen
#      .de als passend behandeln (Vorbehalt im Text -> Flag bleibt, trotz .de) ─
fakten_de_vorbehalt = _extrahiere_fakten(
    [treffer("https://www.lexus.de/heft.pdf", "Lexus Serviceheft", _MARKTVORBEHALT_SATZ)],
    "wartung", marke="Lexus", modell="RC F", baujahr=2017)
check("C1) .de-Dokument MIT Marktvorbehalt im Text -> Flag bleibt bestehen "
      "(die Domain allein macht es NICHT automatisch passend)",
      bool(fakten_de_vorbehalt) and fakten_de_vorbehalt[0].regionale_geltung == "unresolved")

# ── D) Reales Lexus-Dokument: tatsächlicher Herkunftsbeleg + sichtbarer Hinweis ─
_LEXUS_CH_REAL = treffer(
    "https://tools.lexus.ch/fileadmin/pdf_new/lex-sw-00de.pdf",
    "Lexus Garantie- und Serviceheft (Schweiz, Deutsch) -- Betroffene Modelle, "
    "Lexus Care-Wartungsplan",
    "BETROFFENE MODELLE Produktionsstart RC 300h (Oktober 2015 ~) RC F "
    "(September 2014 ~) LC 500 (März 2017 ~) Falls für Ihren Markt zutreffend.\n\n"
    "Lexus RC F zusätzliche Inspektionen laut Lexus Care-Wartungsplan: "
    "Automatikgetriebeöl alle 60.000 km oder 4 Jahre.")
fakten_real = _extrahiere_fakten([_LEXUS_CH_REAL], "wartung", marke="Lexus", modell="RC F",
                                 baujahr=2017)
check("D1) genau ein Fakt erkannt (Automatikgetriebeöl)", len(fakten_real) == 1, str(fakten_real))
check("D2) herkunftsregion benennt den ECHTEN Textbeleg, kein geratenes Land",
      bool(fakten_real) and fakten_real[0].herkunftsregion is not None
      and "markt" in fakten_real[0].herkunftsregion.lower()
      and "schweiz" not in fakten_real[0].herkunftsregion.lower()
      and "lexus" not in fakten_real[0].herkunftsregion.lower(),
      str(fakten_real[0].herkunftsregion if fakten_real else None))
check("D3) regionale_geltung bleibt 'unresolved' (keine Ungeeignetheit)",
      bool(fakten_real) and fakten_real[0].regionale_geltung == "unresolved")
text_d = _web_beschreibung(fakten_real[0], 2017) if fakten_real else ""
check("D4) der sichtbare Hinweis ist verständlich formuliert, OHNE technischen "
      "Domain-Jargon (z.B. keine 'Domainendung .ch'-Formulierung)",
      "Domainendung" not in text_d and "Deutschland" in text_d and "nicht bestätigt" in text_d,
      text_d)

# ── E) Fehlender Dokumentbeleg: keine erfundene Region ──────────────────────
check("E1) kein Marktvorbehalt im Dokumenttext -> kein Flag, egal welche Domain",
      _wartung_marktvorbehalt([treffer("https://www.repareo.com/service", "Service",
                                       _OHNE_VORBEHALT_SATZ)]) == (None, None))
check("E2) leere Treffer-Liste -> kein Flag", _wartung_marktvorbehalt([]) == (None, None))

# ── F) Scope-Isolierung: NUR kategorie=="wartung" ───────────────────────────
fakten_schwachstelle = _extrahiere_fakten(
    [treffer("https://tools.lexus.ch/heft.pdf", "Lexus Schwachstelle",
             "Beim Lexus RC F tritt gelegentlich Ölverlust an der Ölwanne auf, "
             "falls für Ihren Markt zutreffend.")],
    "schwachstelle", marke="Lexus", modell="RC F", baujahr=2017)
check("F1) kategorie=='schwachstelle' bekommt NIE herkunftsregion/regionale_geltung",
      all(f.herkunftsregion is None and f.regionale_geltung is None for f in fakten_schwachstelle),
      str(fakten_schwachstelle))

# ── G) Default-Verhalten bei nicht gesetzten Feldern unverändert ───────────
_fakt_ohne_feld = WebFakt(kategorie="wartung", aussage="Test ohne Herkunftsfeld")
check("G1) WebFakt ohne explizite Herkunftsfelder bleibt None/None",
      _fakt_ohne_feld.herkunftsregion is None and _fakt_ohne_feld.regionale_geltung is None)

print()
if _FEHLER:
    print(f"FEHLER: {len(_FEHLER)} Check(s) fehlgeschlagen: {_FEHLER}")
    raise SystemExit(1)
print("ALLE WARTUNG-REGIONALE-GELTUNG-TESTS GRUEN")
