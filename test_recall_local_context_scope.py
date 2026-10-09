"""
PAKET A — False-Positive-Schutz: RC-W2 (Sentence-Level-Fremdthemen-Kontamination)
und RC-W1 (Teaser-/Index-Seiten erzeugen scope-lose Doppel-Fakten).

Beide Root Causes wurden im Forensik-Audit des ENFAL-Webresearch-Fallbacks
an einem echten, read-only Tavily-Lauf gegen den Mazda-MX-5-Sentinel
(Mazda MX-5 ND, 2019, 2.0 SKYACTIV-G, 184 PS) bewiesen:

RC-W2: Ein Rückruf-Satz wird nur dann erneut auf Fahrzeugbezug geprüft, wenn
die GESAMTE Artikel-Ausrichtung "schwach" ist. Bei starker Ausrichtung (Titel
nennt Marke+Modell) wird JEDER "Rückruf"-Satz der Seite ungeprüft übernommen —
auch ein Satz aus einem "Mehr zum Thema"-Teaser über eine andere Marke
(realer Fund: ein VW/Skoda-Lenkungsrückruf wurde als Mazda-MX-5-Rückruf
extrahiert).

RC-W1: Ein vollständiger Fachartikel mit explizitem Scope ("1,5-Liter-
Benzinmotor, Baujahre 2015-2018") wird korrekt per Baujahr/Hubraum verworfen.
Dieselbe Meldung erscheint aber zusätzlich als bloße Schlagzeile auf einer
Kategorie-/Teaser-Seite OHNE jeden Scope — und erzeugt dort unkontrolliert
einen eigenen, scope-losen "series_only"-Fakt (reale Quelle im Audit:
"Mazda MX-5 Typ ND ► aktuelle Artikel & Tests").

GENERISCHER MECHANISMUS (keine Marken-/Modell-/Domain-Sonderregeln):
  * `_fremde_marke_satz()` — der SATZ, der zu einem Rückruf-Fakt werden soll,
    darf keine andere Marke (app.vehicle_identity.MARKEN) nennen, ohne auch
    die eigene Marke oder das eigene Modell zu nennen.
  * Cross-Source-Konsolidierung über denselben Bauteil-Schlüssel: ein Artikel,
    der wegen Baujahr/Hubraum/Leistung explizit ausgeschlossen wurde, sperrt
    dasselbe Bauteil-Thema für JEDE andere Quelle in diesem Lauf — ein
    scope-loser Teaser darf einen bereits belegten Ausschluss nicht wieder
    "auferstehen" lassen.
  * `_ist_substanziell()` — fehlt jede Scope-Information UND besteht die
    einzige Beleglage aus einer reinen Schlagzeile ohne jeden inhaltlichen
    Überschuss über Marke+Modell+Bauteil+"Rückruf" hinaus, wird NICHT
    automatisch auf "series_only" (gesamte Baureihe betroffen) geschlossen.

KEIN Netzwerk, KEIN LLM-Call, KEINE DB-Mutation. Reiner Funktionstest von
`app.technical_research._extrahiere_fakten()`.

    python test_recall_local_context_scope.py
"""
from app.technical_research import _extrahiere_fakten

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    status = "OK  " if bedingung else "FAIL"
    print(f"[{status}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def treffer(url: str, titel: str, inhalt: str) -> dict:
    return {"url": url, "title": titel, "content": inhalt}


# ══════════════════════════════════════════════════════════════════════════
print("\n=== A) Mazda-Sentinel / Scope-Duplikat (RC-W1, bewiesener Fall) ===")

# Treffer 1: vollständiger Fachartikel MIT explizitem, ausschliessendem Scope.
_treffer_a1 = treffer(
    "https://www.autobild.de/mazda-mx-5-kraftstoffleitung-rueckruf",
    "Kraftstoffleitungs-Rückruf für Mazda MX-5 (ND): Heizen Sie nicht mit Ihrem MX-5",
    "Mazda ruft den MX-5 der vierten Generation (Baureihe ND) wegen möglicher Probleme "
    "an der Kraftstoffleitung zurück. Betroffen sind ausschließlich Modelle mit "
    "1,5-Liter-Benzinmotor aus den Baujahren 2015 bis 2018.")
# Treffer 2: Kategorie-/Teaser-Seite — dieselbe Meldung, NUR die Schlagzeile, kein Scope.
_treffer_a2 = treffer(
    "https://www.autobild.de/marken-modelle/mazda/mx-5/news",
    "Mazda MX-5 ► aktuelle Artikel & Tests",
    "Kraftstoffleitungs-Rückruf für Mazda MX-5 (ND).")

_abgelehnt_a: list[dict] = []
_fakten_a = _extrahiere_fakten([_treffer_a1, _treffer_a2], "rueckruf",
                               marke="Mazda", modell="MX-5", baujahr=2019,
                               hubraum="2.0", leistung_ps=184, abgelehnt=_abgelehnt_a)
check("A1 KEIN Recall-Fakt für das 2019er 2.0-Fahrzeug (weder aus Artikel noch Teaser)",
      _fakten_a == [])
check("A2 Ablehnungsgrund für den Volltext-Artikel: Baujahr außerhalb",
      any(a.get("grund") == "baujahr_ausserhalb_artikel" for a in _abgelehnt_a))
check("A3 Ablehnungsgrund für die Teaser-Seite dokumentiert (Cross-Source-Sperre ODER "
      "Substanz-Schwelle — nach der Event-Gleichheits-Härtung fängt hier bewusst die "
      "Substanz-Schwelle, da die blosse Schlagzeile zu wenig eigenen Inhalt mit dem "
      "ausschliessenden Artikel teilt, um sicher als 'dasselbe Ereignis' zu gelten)",
      any((a.get("grund") or "").startswith(("rueckruf_thema_bereits_ausgeschlossen",
                                             "rueckruf_ohne_substanz"))
          for a in _abgelehnt_a))


# ══════════════════════════════════════════════════════════════════════════
print("\n=== B) Fremdmarken-Satz innerhalb passender Seite (RC-W2, bewiesener Fall) ===")

_treffer_b = treffer(
    "https://www.autobild.de/mazda-mx5-rf-auslieferung-stopp",
    "Mazda stoppt MX-5 RF Auslieferung wegen Motor-Software",
    "Mazda stoppt die Auslieferung des MX-5 RF wegen eines Fehlers in der "
    "Motor-Steuerungssoftware. Der Lenkungs-Rückruf im VW-Konzern fällt bei "
    "Skoda größer aus als gedacht.")

_abgelehnt_b: list[dict] = []
_fakten_b = _extrahiere_fakten([_treffer_b], "rueckruf", marke="Mazda", modell="MX-5",
                               baujahr=2019, abgelehnt=_abgelehnt_b)
check("B1 KEIN Mazda-Recall-Fakt aus dem Fremdmarken-Satz (VW/Skoda)",
      _fakten_b == [])
check("B2 Ablehnungsgrund sichtbar: fremde Marke im Satz",
      any((a.get("grund") or "").startswith("fremde_marke_satz") for a in _abgelehnt_b))


# ══════════════════════════════════════════════════════════════════════════
print("\n=== C) Echter Recall auf passender Seite — bleibt akzeptiert (keine Überreaktion) ===")

_treffer_c = treffer(
    "https://www.autobild.de/mazda-mx-5-kraftstoffleitung-rueckruf-2",
    "Rückruf für den Mazda MX-5",
    "Mazda meldet einen Rückruf des MX-5 wegen eines Lecks an der Kraftstoffleitung, "
    "nachdem mehrere Kunden einen auffälligen Geruch bemerkt hatten.")

_fakten_c = _extrahiere_fakten([_treffer_c], "rueckruf", marke="Mazda", modell="MX-5",
                               baujahr=2019)
check("C1 echter, unkontaminierter Rückruf mit substanzieller Beschreibung bleibt akzeptiert",
      len(_fakten_c) == 1)
if _fakten_c:
    check("C2 Bauteil korrekt als 'Kraftstoffleitung' erkannt",
          "kraftstoffleitung" in (_fakten_c[0].bauteil or "").lower())


# ══════════════════════════════════════════════════════════════════════════
print("\n=== D) Pronomen-/Kontext-Fall: Folgesatz wiederholt Marke/Modell nicht ===")

_treffer_d = treffer(
    "https://www.autobild.de/mazda-mx-5-rueckruf-kraftstoffleitung-2",
    "Kraftstoffleitungs-Rückruf für Mazda MX-5 (ND)",
    "Betroffen sind Fahrzeuge aus den Baujahren 2017 bis 2021.")

_fakten_d = _extrahiere_fakten([_treffer_d], "rueckruf", marke="Mazda", modell="MX-5",
                               baujahr=2019)
check("D1 Fakt entsteht trotzdem (lokaler Kontext/Artikel-Scope reicht, "
      "der Folgesatz muss Marke/Modell nicht wiederholen)",
      len(_fakten_d) == 1)
if _fakten_d:
    check("D2 applicability = vehicle_possible (Baujahr 2019 liegt im Scope-Fenster "
          "2017-2021, aus dem NICHT-wiederholenden Folgesatz übernommen)",
          _fakten_d[0].applicability == "vehicle_possible")


# ══════════════════════════════════════════════════════════════════════════
print("\n=== E) Scope unknown: bloßer Teaser ohne jede Substanz ===")

_treffer_e = treffer(
    "https://www.autobild.de/marken-modelle/mazda/mx-5/news",
    "Mazda MX-5 ► aktuelle Artikel & Tests",
    "Kraftstoffleitungs-Rückruf für Mazda MX-5 (ND).")

_abgelehnt_e: list[dict] = []
_fakten_e = _extrahiere_fakten([_treffer_e], "rueckruf", marke="Mazda", modell="MX-5",
                               baujahr=2019, abgelehnt=_abgelehnt_e)
check("E1 reiner Teaser ohne jede Substanz wird NICHT automatisch 'series_only'",
      _fakten_e == [])
check("E2 Ablehnung dokumentiert die fehlende Substanz",
      any((a.get("grund") or "").startswith("rueckruf_ohne_substanz") for a in _abgelehnt_e))


# ══════════════════════════════════════════════════════════════════════════
print("\n=== F) Expliziter Scope-Match: Recall bleibt sichtbar ===")

_treffer_f = treffer(
    "https://www.autobild.de/mazda-mx-5-rueckruf-steuergeraet",
    "Rückruf für den Mazda MX-5: Steuergerät betroffen",
    "Betroffen sind Fahrzeuge aus den Baujahren 2017 bis 2021.")

_fakten_f = _extrahiere_fakten([_treffer_f], "rueckruf", marke="Mazda", modell="MX-5",
                               baujahr=2019)
check("F1 Recall mit explizit passendem Baujahr-Scope bleibt sichtbar", len(_fakten_f) == 1)
if _fakten_f:
    check("F2 applicability = vehicle_possible (Scope passt)",
          _fakten_f[0].applicability == "vehicle_possible")


# ══════════════════════════════════════════════════════════════════════════
print("\n=== G) Expliziter Scope-Widerspruch: verworfen ===")

_fakten_g = _extrahiere_fakten([_treffer_a1], "rueckruf", marke="Mazda", modell="MX-5",
                               baujahr=2019, hubraum="2.0")
check("G1 expliziter Scope-Widerspruch (1,5L/2015-2018 vs. 2.0L/2019) -> kein Fakt",
      _fakten_g == [])


# ══════════════════════════════════════════════════════════════════════════
print("\n=== H) Cross-Source-Review: zwei UNTERSCHIEDLICHE Rückrufe, gleiches Bauteil ===")
# Review-Fund: die ursprüngliche Cross-Source-Sperre nutzte NUR den
# Bauteil-Schlüssel — ein ausgeschlossener Rückruf hätte dadurch einen
# tatsächlich ANDEREN Rückruf mit demselben Bauteil blockieren können.
# Jetzt zählt zusätzlich (a) ein eigener, passender Scope beim Kandidaten
# selbst (dann greift die Sperre nie — siehe H1) und (b) echte inhaltliche
# Überlappung der Kernaussage, wenn kein eigener Scope vorliegt (H1b).

# H1: Treffer B trägt einen EIGENEN, explizit passenden Scope (2,0 l /
# 2017-2021 deckt Baujahr 2019) — die Sperre darf hier NIE greifen,
# unabhängig von jeder Inhaltsähnlichkeit zu Treffer A.
_treffer_h1_b = treffer(
    "https://www.autobild.de/mazda-mx-5-rueckruf-schelle-kraftstoffleitung",
    "Rückruf für den Mazda MX-5: zweite, unabhängige Aktion",
    "Bei einer separaten Rückrufaktion muss Mazda Fahrzeuge mit 2,0-Liter-Motor aus den "
    "Baujahren 2017 bis 2021 in die Werkstatt rufen, weil sich eine Schelle an der "
    "Kraftstoffleitung lösen und Vibrationen verursachen kann.")

_abgelehnt_h1: list[dict] = []
_fakten_h1 = _extrahiere_fakten([_treffer_a1, _treffer_h1_b], "rueckruf",
                                marke="Mazda", modell="MX-5", baujahr=2019,
                                abgelehnt=_abgelehnt_h1)
check("H1a Treffer A (1,5L/2015-2018) bleibt ausgeschlossen",
      any(a.get("grund") == "baujahr_ausserhalb_artikel" for a in _abgelehnt_h1))
check("H1b Treffer B (eigener, passender Scope 2,0L/2017-2021) bleibt SICHTBAR "
      "— die Cross-Source-Sperre darf einen Kandidaten mit eigenem Scope-Beleg nie entfernen",
      len(_fakten_h1) == 1)
if _fakten_h1:
    check("H1c Treffer B hat applicability = vehicle_possible (eigener Scope-Match)",
          _fakten_h1[0].applicability == "vehicle_possible")

# H1b (verschärfter Fall): Treffer B hat KEINEN eigenen numerischen Scope
# (Baujahr/Hubraum/PS), aber eine klar ANDERE, substanzielle Kernaussage als
# Treffer A (Schelle/Vibration statt Hitze/Riss) — die Inhaltsüberlappung mit
# Treffer A ist praktisch null. Die Sperre darf trotz gleichen Bauteils NICHT
# greifen; die eigene Substanz trägt den Fakt als "series_only".
_treffer_h1b_b = treffer(
    "https://www.autobild.de/mazda-mx-5-rueckruf-schelle-vibration",
    "Rückruf für den Mazda MX-5: unabhängige zweite Aktion",
    "Bei einer separaten Rückrufaktion muss Mazda den MX-5 in die Werkstatt rufen, weil "
    "sich eine Schelle an der Kraftstoffleitung durch Vibrationen lösen und ein leichtes "
    "Klappern verursachen kann.")

_abgelehnt_h1b: list[dict] = []
_fakten_h1b = _extrahiere_fakten([_treffer_a1, _treffer_h1b_b], "rueckruf",
                                 marke="Mazda", modell="MX-5", baujahr=2019,
                                 abgelehnt=_abgelehnt_h1b)
check("H1b-1 Treffer A (1,5L/2015-2018) bleibt ausgeschlossen",
      any(a.get("grund") == "baujahr_ausserhalb_artikel" for a in _abgelehnt_h1b))
check("H1b-2 Treffer B (eigene, klar andere Kernaussage, kein eigener Scope) bleibt "
      "SICHTBAR — keine ausreichende Inhaltsüberlappung mit dem ausgeschlossenen Ereignis",
      len(_fakten_h1b) == 1)
if _fakten_h1b:
    check("H1b-3 Treffer B wurde NICHT über die Cross-Source-Sperre verworfen",
          not any("rueckruf_thema_bereits_ausgeschlossen" in (a.get("grund") or "")
                  for a in _abgelehnt_h1b))

# H2 (Kontrollfall): Treffer B ist ein bloßer Teaser DERSELBEN Meldung wie
# Treffer A (keine eigene Substanz, keine eigene Kernaussage) — dieser MUSS
# weiterhin verworfen bleiben (hier typischerweise über die Substanz-Schwelle,
# da zu wenig eigener Inhalt für einen sicheren Cross-Source-Abgleich vorliegt
# — siehe Test A/A3).
_abgelehnt_h2: list[dict] = []
_fakten_h2 = _extrahiere_fakten([_treffer_a1, _treffer_a2], "rueckruf",
                                marke="Mazda", modell="MX-5", baujahr=2019,
                                hubraum="2.0", leistung_ps=184, abgelehnt=_abgelehnt_h2)
check("H2 Teaser derselben Meldung lässt Treffer A NICHT wieder auferstehen",
      _fakten_h2 == [])


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE RECALL-LOCAL-CONTEXT-SCOPE-TESTS GRUEN")
