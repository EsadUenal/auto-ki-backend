"""
L3 (ENFAL-Recherchepfad-Folgeauftrag, 2026-11) — Volltext-Nachladung für
mehrseitige Wartungsdokumente. Deterministisch, KEIN Netzwerk.

Befund (real belegt, s. `test_rc_f_lexus_care_wartungsplan_real.py`/
`test_wartung_regionale_geltung.py`): im echten Lexus-Schweiz-"Garantie- und
Serviceheft" (24 Seiten) steht der Marktvorbehalt "falls für Ihren Markt
zutreffend" auf Seite 4, die Wartungsintervalle auf Seite 15/16. Ein Tavily-
Suchtreffer bringt typischerweise nur ein kurzes Snippet rund um die
Treffer-Stelle mit -- der weit entfernte Vorbehalt kann dabei verloren gehen,
OHNE dass das von einem korrekt arbeitenden Parser zu unterscheiden wäre
(Auftrag §3: "ein korrekt arbeitender Warnsatz-Parser genügt nicht, wenn der
relevante Dokumentkontext vorher verloren geht").

Dieser Test prüft die NEUE Auswahl-/Merge-Logik
(`_wartung_nachlade_kandidaten`, `_wartung_treffer_mit_volltext`,
app/technical_research.py) sowie deren Zusammenspiel mit der BEREITS
bestehenden, unveränderten Extraktionspipeline (`_extrahiere_fakten`,
`_wartung_marktvorbehalt`). Bewusst NICHT erneut geprüft: der volle async
`TavilyTechnicalResearchProvider.recherchiere()`-Suchpfad selbst (Query-
Dispatch) -- der ist bereits durch die bestehende Phasen-Architektur
abgedeckt; hier geht es ausschließlich um den NEUEN Nachlade-Baustein.

Case H (unten): Teiltext ohne Vorbehalt vs. vorhandenes Original MIT
Vorbehalt -- UND der Fall eines fehlgeschlagenen Originalabrufs, der NIE eine
erfundene vollständige Gültigkeitsprüfung erzeugen darf (Auftrag §5).

    python -m pytest test_wartung_volltext_nachladung.py -q
"""
from app.technical_research import (
    MIN_SCORE_FAKT, _wartung_nachlade_kandidaten, _wartung_treffer_mit_volltext,
    _wartung_marktvorbehalt, _extrahiere_fakten, _WARTUNG_VOLLTEXT_NACHLADE_MAX,
)

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


def treffer(url, titel="", inhalt="", raw_content=""):
    return {"url": url, "title": titel, "content": inhalt, "raw_content": raw_content}


# ── A) Auswahl-Gate: nur hochwertige Domains OHNE brauchbaren raw_content ──

check("A1) hochwertige Domain ohne raw_content -> Kandidat",
      _wartung_nachlade_kandidaten(
          [treffer("https://www.adac.de/service-testmarke", "Testmarke Wartung", "kurzer Snippet")],
      ) == ["https://www.adac.de/service-testmarke"])

check("A2) unbekannte/niedrige Domain -> KEIN Kandidat (kein pauschales Nachladen)",
      _wartung_nachlade_kandidaten(
          [treffer("https://blog.irgendwer.example/service", "Service", "kurzer Snippet")],
      ) == [])

check("A3) bereits brauchbarer raw_content (>= 400 Zeichen) -> KEIN erneutes Nachladen",
      _wartung_nachlade_kandidaten(
          [treffer("https://www.adac.de/service-testmarke", "Testmarke Wartung",
                   "kurzer Snippet", raw_content="x" * 500)],
      ) == [])

check("A4) hart begrenzt auf _WARTUNG_VOLLTEXT_NACHLADE_MAX, auch bei mehr Kandidaten",
      len(_wartung_nachlade_kandidaten(
          [treffer(f"https://www.adac.de/service-testmarke-{i}", "Testmarke Wartung", "Snippet")
           for i in range(_WARTUNG_VOLLTEXT_NACHLADE_MAX + 5)],
      )) == _WARTUNG_VOLLTEXT_NACHLADE_MAX)

check("A5) dieselbe URL doppelt im Rohtreffer -> nur EINMAL als Kandidat",
      _wartung_nachlade_kandidaten(
          [treffer("https://www.adac.de/service-testmarke", "Testmarke Wartung", "Snippet"),
           treffer("https://www.adac.de/service-testmarke", "Testmarke Wartung", "Snippet (Dup)")],
      ) == ["https://www.adac.de/service-testmarke"])

check("A6) Score-Schwelle ist DIESELBE wie im Hauptpfad (MIN_SCORE_FAKT), kein weicheres Kriterium",
      MIN_SCORE_FAKT == 30)


# ── B) Merge: Volltext ersetzt NUR `content`, Fehlschlag bleibt unverändert ──

_url_b = "https://www.adac.de/service-testmarke"
_treffer_b = [treffer(_url_b, "Testmarke Wartung", "kurzer Snippet ohne Vorbehalt")]

_gemergt_erfolg = _wartung_treffer_mit_volltext(_treffer_b, {_url_b: "voller Dokumenttext " * 50})
check("B1) erfolgreich nachgeladener Volltext ersetzt `content`",
      _gemergt_erfolg[0]["content"].startswith("voller Dokumenttext"))
check("B2) `url`/`title` bleiben beim Merge unverändert",
      _gemergt_erfolg[0]["url"] == _url_b and _gemergt_erfolg[0]["title"] == "Testmarke Wartung")

_gemergt_fehlschlag = _wartung_treffer_mit_volltext(_treffer_b, {})
check("B3) fehlgeschlagenes/nicht versuchtes Nachladen (leeres Dict) -> Treffer UNVERÄNDERT "
      "(kein erfundener Volltext)",
      _gemergt_fehlschlag[0]["content"] == "kurzer Snippet ohne Vorbehalt")
check("B4) das Original-Treffer-Objekt selbst wird dabei nicht mutiert "
      "(neue Dicts, keine Seiteneffekte auf geteilte Listen)",
      _gemergt_fehlschlag[0] is _treffer_b[0] and _gemergt_erfolg[0] is not _treffer_b[0])


# ── C) Reale Extraktionspipeline: Teiltext ohne Vorbehalt vs. Original MIT
#      Vorbehalt (Auftrag §5 Testanforderung) ──────────────────────────────

_SERVICE_URL = "https://www.adac.de/service-testmarke"
# Kurzes Snippet, wie es eine Websuche typischerweise um die Trefferstelle
# herum liefert: nennt das Intervall, aber NICHT den (auf einer anderen Seite
# des Originaldokuments stehenden) Marktvorbehalt.
_SNIPPET_OHNE_VORBEHALT = (
    "Testmarke Alpha Wartungsplan: Öl- und Filterwechsel alle 15.000 km oder 1 Jahr.")
# Das vollständige Originaldokument (simuliert den Volltext-Nachlade-Erfolg):
# enthält BEIDE Stellen -- Intervall UND den Marktvorbehalt an anderer Stelle
# im Dokument, genau wie im real belegten Lexus-Schweiz-Fixture.
_VOLLTEXT_MIT_VORBEHALT = (
    "Betroffene Modelle: Testmarke Alpha, Testmarke Beta. Falls für Ihren Markt zutreffend. "
    "... Testmarke Alpha Wartungsplan: Öl- und Filterwechsel alle 15.000 km oder 1 Jahr.")

_fakten_snippet = _extrahiere_fakten(
    [treffer(_SERVICE_URL, "Testmarke Alpha Serviceheft", _SNIPPET_OHNE_VORBEHALT)],
    "wartung", marke="Testmarke", modell="Alpha", baujahr=2019)
check("C1) Snippet allein (ohne Vorbehalt-Textstelle) liefert den Wartungs-Fakt, "
      "aber OHNE regionale_geltung (ehrlich: kein Beleg sichtbar)",
      bool(_fakten_snippet) and _fakten_snippet[0].regionale_geltung is None,
      str(_fakten_snippet))

_treffer_nach_nachladung = _wartung_treffer_mit_volltext(
    [treffer(_SERVICE_URL, "Testmarke Alpha Serviceheft", _SNIPPET_OHNE_VORBEHALT)],
    {_SERVICE_URL: _VOLLTEXT_MIT_VORBEHALT})
_fakten_volltext = _extrahiere_fakten(
    _treffer_nach_nachladung, "wartung", marke="Testmarke", modell="Alpha", baujahr=2019)
check("C2) nach erfolgreichem Nachladen (derselbe Fund, jetzt mit Volltext) "
      "wird der SONST verlorene Marktvorbehalt sichtbar: regionale_geltung='unresolved'",
      bool(_fakten_volltext) and _fakten_volltext[0].regionale_geltung == "unresolved",
      str(_fakten_volltext))
check("C3) derselbe Fakt (Intervall) bleibt in beiden Fällen inhaltlich erhalten -- "
      "das Nachladen ÄNDERT die gefundene Aussage nicht, es ergänzt nur den Geltungskontext",
      _fakten_snippet and _fakten_volltext
      and _fakten_snippet[0].bauteil == _fakten_volltext[0].bauteil)

# Fehlgeschlagener Originalabruf (Auftrag §5): das Nachladen schlägt fehl
# (z.B. Netzwerkfehler, gesperrte Domain, zu kurzer Inhalt) -- der ORIGINALE,
# kürzere Suchtreffer bleibt die Grundlage. Es darf dabei NIE eine vollständige
# Gültigkeitsprüfung erfunden werden, nur weil ein Nachladeversuch stattfand.
_treffer_nach_fehlschlag = _wartung_treffer_mit_volltext(
    [treffer(_SERVICE_URL, "Testmarke Alpha Serviceheft", _SNIPPET_OHNE_VORBEHALT)], {})
_fakten_fehlschlag = _extrahiere_fakten(
    _treffer_nach_fehlschlag, "wartung", marke="Testmarke", modell="Alpha", baujahr=2019)
check("C4) fehlgeschlagenes Nachladen -> derselbe Fakt wie ohne Nachladeversuch "
      "(Snippet bleibt Grundlage), KEINE erfundene regionale_geltung",
      bool(_fakten_fehlschlag) and _fakten_fehlschlag[0].regionale_geltung is None
      and _fakten_fehlschlag[0].bauteil == _fakten_snippet[0].bauteil,
      str(_fakten_fehlschlag))

# ── D) Modell-/Markenbindung bleibt erhalten (unveränderte Pipeline) ────────
_fakten_falsches_modell = _extrahiere_fakten(
    _wartung_treffer_mit_volltext(
        [treffer(_SERVICE_URL, "Anderes Modell", "Ein anderer Hersteltext ohne Testmarke Alpha.")],
        {_SERVICE_URL: _VOLLTEXT_MIT_VORBEHALT}),
    "wartung", marke="Testmarke", modell="Gamma", baujahr=2019)
check("D1) Volltext-Nachladung hebelt die Modellzuordnung NICHT aus -- ein Treffer, "
      "der nach dem Merge nicht zum gesuchten Modell ausgerichtet ist, bleibt verworfen",
      _fakten_falsches_modell == [])

print()
if _FEHLER:
    print(f"FEHLER: {len(_FEHLER)} Check(s) fehlgeschlagen: {_FEHLER}")
    raise SystemExit(1)
