from __future__ import annotations

"""
Zentrale Rückruf-Allowed-Liste (Reliability-Sprint 4, §Phase 7).

EIN neutrales Modul (bewusst NICHT in app/evidence.py versteckt, um jeden Kreis-
Import mit den Aufrufern zu vermeiden — car_lookup.py/llm.py/kaufcheck.py/
verkaufscheck.py importieren alle von HIER, evidence.py importiert ebenfalls von
HIER), das für ein Fahrzeug (Baureihen-Rückrufe + erkannte Motorisierung) EINE
einzige, deterministische Entscheidung trifft: welche Rückrufe dieses Fahrzeug
überhaupt betreffen können (`gefilterte_rueckrufe`) und welche eindeutig NICHT
zutreffen (`ausgeschlossene_rueckrufe`).

Der Bug, den dieses Modul behebt: `app/evidence.py::build_insights` filterte
Hochvolt-/PHEV-Rückrufe für die STRUKTURIERTEN Insights/Key-Findings bereits
korrekt heraus — aber `app/car_lookup.py::build_db_context` (Kauf-/Verkaufscheck-
LLM-Prompt) und `app/llm.py::_sql_context` (allgemeiner Chat) lasen dieselbe
`rueckrufe`-DB-Spalte UNGEFILTERT und kippten sie roh in den Gemini-Prompt — das
LLM bekam den Hochvolt-Rückruf trotzdem zu sehen und schrieb ihn in Bericht/
Checkliste. Ab sofort nutzen ALLE Aufrufer ausschließlich `gefilterte_rueckrufe`
aus diesem Modul — keine zweite, ungefilterte Rückrufliste mehr irgendwo im Code.

Die Tabelle `rueckruf` ist NUR an baureihe_id gekoppelt (kein motorvariante_id/
kraftstoff/antrieb-Feld). Die Varianten-Einschränkung steht — wenn überhaupt — als
FREITEXT im mangel/abhilfe/betroffene_baujahre (z.B. "(Plug-in-Hybrid)",
"Hochvoltbatterie"). Daraus leiten wir DETERMINISTISCH ab, ob ein Rückruf einen
bestimmten Antrieb/Kraftstoff adressiert — ohne Raten, ohne LLM.

KBA-REFERENZ-TRUST-GATE (DATA-TRUST-AUDIT)

Der Audit hat gemessen: von 759 Rückrufzeilen tragen 589 eine `kba_referenz`.
Davon sind 200 Zeilen (76 unterschiedliche Referenzwerte) MARKENÜBERGREIFEND
mehrfach vergeben — dieselbe Nummer steht z.B. bei BMW, VW, Opel, Ford UND Seat.
Eine amtliche KBA-Aktionsnummer ist je Aktion eindeutig; taucht sie bei
verschiedenen Herstellern auf, ist mindestens einer der Datensätze falsch —
welcher, ist unbekannt. Zusätzlich enthält das Feld erkennbare Platzhalter/
Test-Artefakte: sequenzielle Ziffernfolgen ("1234567", "9876543", "012345") und
einen 64-stelligen Hex-Block aus fast nur Nullen. Bisher konnte allein die
BLOSSE ANWESENHEIT einer `kba_referenz` einen Rückruf auf die höchste
Ohne-VIN-Stufe ("variant_match", confidence "hoch") heben und dem Nutzer eine
amtlich wirkende Nummer zeigen — unabhängig davon, ob die Nummer plausibel war.

Die drei Funktionen `kba_referenz_vertrauenswuerdig` / `kba_referenz_anzeige` /
der `marke`-Parameter von `rueckruf_applicability` schließen das: eine
unplausible oder markenübergreifend kollidierende Referenz zählt für die
Vertrauensstufe wie eine FEHLENDE Referenz (Rückfall auf "series_only" statt
"variant_match") und wird nirgends mehr angezeigt — weder im Evidence-`ref`,
noch im DB-Kontext-Prompt, noch in Kaufaktionen (letztere erben das über die
bereits gegateten Insights, siehe app/evidence.py). Der zugrunde liegende
Rückrufdatensatz selbst wird NICHT verworfen: Mangel, Abhilfe und Baujahr
bleiben als konservativer Baureihen-Hinweis ("Kann Fahrzeuge dieser Baureihe
betreffen — per FIN prüfen") vollständig erhalten. Mehrfachverwendung
DERSELBEN Marke ist ausdrücklich KEIN Fehler (eine Rückrufaktion kann mehrere
Modelle eines Herstellers betreffen, z.B. bei geteiltem Zulieferer/Bauteil) und
senkt die Vertrauensstufe nicht.

Dies ist ein reines Laufzeit-Gate — keine DB wird verändert, keine Zeile
gelöscht, keine Migration ausgeführt. Die Datengrundlage bleibt vollständig
erhalten; nur die WIRKUNG einer nicht plausiblen Referenz wird begrenzt.
"""

import logging
import re

from app.database import get_rueckruf_referenzen_kurz
# Wo eine INDIVIDUELLE FIN-Abfrage moeglich ist — eine Quelle (app/fin_hinweis.py).
from app.fin_hinweis import HINWEIS_FIN as _HINWEIS_FIN

log = logging.getLogger(__name__)

_JAHR = re.compile(r"\b(?:19|20)\d{2}\b")
_BEREICH = re.compile(r"[-–]|bis")
_ALLGEMEIN = {"", "alle", "alle baujahre", "-", "n/a", "unbekannt", "diverse"}

# ── Offene Grenzen und Tendenzangaben (KaufCheck Root-Cause-Closing) ─────────
#
# Die Baujahresangaben der Schwachstellen-Tabellen sind Freitext. Die erste
# Fassung kannte nur "Bereich" (min..max aller Jahreszahlen) und "Einzeljahr"
# (exakte Mitgliedschaft). Damit wurden offene Grenzen falsch gelesen und
# Datensätze verschwanden für genau die Fahrzeuge, die sie betreffen:
#
#   "ab 2015"          2016 -> False   (nur 2015 selbst passte)
#   "bis 2012"         2009 -> False   ("bis" galt als Bereich 2012..2012)
#   "v.a. 2014-2015"   2016 -> False   ("vor allem" ist keine Grenze)
#
# Gemessen: 36 Schwachstellenzeilen mit offener Einzeljahr-Grenze, dazu
# Tendenzangaben wie "v.a." und "insbesondere". Rückrufzeilen sind nicht
# betroffen: die amtlichen Produktionszeiträume sind durchgehend echte Bereiche
# oder Einzeljahre und laufen unverändert durch den alten Pfad.
_MONAT_VOR_JAHR = r"(?:\d{1,2}\s*[./]\s*)?"
_UNGEFAEHR = re.compile(r"\b(?:ca\.?|circa|etwa|ungef[äa]hr)(?=\s|\d|$)")
_AB_GRENZE = re.compile(rf"\b(?:ab|seit)\b[^\d;]{{0,25}}?{_MONAT_VOR_JAHR}((?:19|20)\d{{2}})\b")
_BIS_GRENZE = re.compile(rf"\bbis\b[^\d;]{{0,25}}?{_MONAT_VOR_JAHR}((?:19|20)\d{{2}})\b")
# "vor allem" ist eine Tendenz, keine Grenze, deshalb ausdrücklich ausgenommen.
_VOR_GRENZE = re.compile(
    rf"\bvor\b(?!\s+allem)[^\d;]{{0,25}}?{_MONAT_VOR_JAHR}((?:19|20)\d{{2}})\b")
# Eine Tendenz beschreibt, wo ein Problem gehäuft auftritt, nicht, wo es endet.
# Sie darf ein Baujahr deshalb nie hart ausschließen.
_TENDENZ = re.compile(
    r"\bv\.\s?a\.|\b(?:vor allem|insbesondere|haupts[äa]chlich|[üu]berwiegend|besonders"
    r"|meist(?:ens)?|vorwiegend|vermehrt|h[äa]ufig)\b")
_ALLE_BAUJAHRE = re.compile(r"\balle\s+baujahre\b")
_ECHTER_BEREICH = re.compile(
    rf"(?:19|20)\d{{2}}\s*(?:[-–]|bis)\s*(?:ca\.?\s*)?{_MONAT_VOR_JAHR}(?:19|20)\d{{2}}")


# ── KBA-Referenz-Plausibilität ────────────────────────────────────────────────
#
# Format-Regeln DATENGETRIEBEN aus den 589 tatsächlich befüllten Referenzen
# hergeleitet (siehe Modulkopf), nicht vermutet:
#   567  rein numerisch, 3–8 Stellen ("9600", "011400")
#     8  Zifferngruppen mit Leerzeichen/Bindestrich ("64-0034", "80 14 11")
#     6  ein Buchstabe + Ziffern, 6–8 Stellen ("8A800000") — Mercedes-Schreibweise
#    12  erkennbare Platzhalter: sequenzielle Ziffernfolgen ("1234567", "9876543",
#        "012345") — real vorkommende Testdaten, kein einziger Treffer im
#        plausiblen Rest
#     4  eindeutig implausibel: ein Freitext-Satz (37 Zeichen) und ein
#        64-stelliger Hex-Block aus fast nur Nullen (3 Varianten)
#
# Der laengste real vorkommende plausible Wert ist 8 Zeichen lang; die Grenze
# unten liegt bewusst grosszuegig bei 12, um kuenftige, etwas laengere aber
# echte Formate nicht sofort zu verwerfen — sie faengt nur die beiden
# eindeutigen Ausreisser oben ab.
_MAX_REFERENZ_LAENGE = 12
_MIN_REFERENZ_LAENGE = 3
_SEQUENZ_MIN_LAENGE = 5
_TRENNER = re.compile(r"[\s-]+")
_ALNUM_MUSTER = re.compile(r"[0-9]?[A-Za-z][0-9]{5,7}")

# AMTLICHE Schreibweise mit angehaengtem Kennbuchstaben ("14004R", "16905R").
#
# BATCH-A-BEFUND: der KBA-Gesamtexport vom 27.08.2026 (7.816 Datensaetze) kennt
# genau drei Referenzformate — 4.702 vierstellig, 1.944 fuenfstellig und 1.170
# fuenfstellig mit angehaengtem "R" (15,0 %). Die obige, aus dem ERFUNDENEN
# VIRA-Altbestand hergeleitete Regel verlangt den Buchstaben VORNE
# (Mercedes-Schreibweise "8A800000") und hat deshalb jede einzelne dieser 1.170
# echten amtlichen Nummern als unplausibel verworfen. Wirkung war zwar in die
# sichere Richtung (Rueckfall auf "series_only", Nummer wird nicht angezeigt),
# aber inhaltlich falsch: eine amtliche Referenz galt als Platzhalter.
#
# Die Ergaenzung ist bewusst eng — Ziffernkern plus GENAU EIN Buchstabe am Ende;
# der Ziffernkern durchlaeuft dieselbe Sequenzpruefung wie die reine
# Ziffernform, damit "123456R" weiterhin als Testmuster faellt.
_AMTLICH_SUFFIX_MUSTER = re.compile(r"([0-9]{3,8})[A-Za-z]")


def _ist_sequentiell(ziffern: str) -> bool:
    """Erkennt Platzhalter-/Testwerte wie '1234567' oder '9876543': jede Ziffer
    genau +1 bzw. -1 zur vorigen, über die GESAMTE Länge. Ab 5 Ziffern geprüft —
    kürzer wären zu viele echte Nummern zufällig betroffen (z.B. '123' wäre ein
    plausibler realer Wert, kein Testmuster)."""
    if len(ziffern) < _SEQUENZ_MIN_LAENGE:
        return False
    diffs = {int(b) - int(a) for a, b in zip(ziffern, ziffern[1:])}
    return diffs in ({1}, {-1})


def kba_referenz_format_plausibel(kba: str | None) -> bool:
    """Reine Formatprüfung — OHNE Kenntnis anderer Referenzen/Marken.

    Lehnt ab: leer, zu lang (Freitext/Hex-Platzhalter), zu kurz, und erkennbar
    sequenzielle Ziffernfolgen. Akzeptiert sowohl die reine Ziffernform als auch
    die beobachteten Varianten mit Leerzeichen/Bindestrich-Trennern, die
    einbuchstabige alphanumerische Form und die amtliche Form mit angehaengtem
    Kennbuchstaben ("14004R").
    """
    kba = (kba or "").strip()
    if not kba or len(kba) > _MAX_REFERENZ_LAENGE:
        return False
    kern = _TRENNER.sub("", kba)
    if kern.isdigit():
        return len(kern) >= _MIN_REFERENZ_LAENGE and not _ist_sequentiell(kern)
    suffix = _AMTLICH_SUFFIX_MUSTER.fullmatch(kern)
    if suffix:
        return not _ist_sequentiell(suffix.group(1))
    return bool(_ALNUM_MUSTER.fullmatch(kern))


def _hersteller(marke: str | None) -> str:
    """Marke auf den HERSTELLER normalisiert ("Mercedes-AMG" -> "MERCEDES-BENZ").

    BATCH-A-BEFUND: die Kollisionsprüfung verglich bisher die rohe
    VIRA-Markenzeichenkette. VIRA führt den AMG GT unter der Marke
    "Mercedes-AMG", die E-Klasse unter "Mercedes-Benz" — beim KBA stehen beide
    unter MERCEDES-BENZ. Eine einzige amtliche Aktion über beide Modelle
    (z.B. KBA 10715, 12026) sah dadurch aus wie dieselbe Nummer bei zwei
    Herstellern, galt als Kollision, und die korrekte amtliche Referenz wurde
    ausgeblendet — die Zeile fiel auf "series_only" zurück. Die Zuordnung
    Untermarke -> Hersteller steht bereits in
    `app.kba_reconciliation.MARKE_MAP`; sie wird hier benutzt statt neu
    erfunden. Echte markenübergreifende Kollisionen (BMW gegen Renault) erkennt
    die Prüfung unverändert.
    """
    from app.kba_reconciliation import kba_marke
    return kba_marke(marke or "")


def _referenz_marken_index() -> dict[str, set[str]]:
    """Normalisierte Referenz -> Menge der Marken, die sie tragen — aus der
    gecachten DB-Liste (app.database.get_rueckruf_referenzen_kurz, 60s TTL).
    Neu aufgebaut bei jedem Aufruf (billig: <600 Zeilen), damit kein eigener
    Cache mit eigener Invalidierungslogik entsteht.

    Ein DB-Fehler (z.B. Tabelle noch nicht angelegt, Verbindung weg) darf die
    Formatprüfung — die primäre Sicherung — nicht mit hinunterreißen: dann gilt
    einfach kein bekannter Kollisionsfall (leerer Index), nicht "Referenz
    ungültig". Dieselbe Vorsicht wie bei app/fahrzeugkontext.py::_vorgaenger.
    """
    try:
        zeilen = get_rueckruf_referenzen_kurz()
    except Exception:
        log.info("recall_filter: Referenz-Kollisionsindex nicht verfügbar (DB-Fehler)")
        return {}
    index: dict[str, set[str]] = {}
    for zeile in zeilen:
        ref = (zeile.get("kba_referenz") or "").strip().upper()
        marke = _hersteller(zeile.get("marke"))
        if ref and marke:
            index.setdefault(ref, set()).add(marke)
    return index


def kba_referenz_kollidiert_markenuebergreifend(kba: str, marke: str | None) -> bool:
    """Ob dieselbe Referenz bei einer ANDEREN Marke als `marke` auftaucht.

    Mehrfachverwendung innerhalb DERSELBEN Marke zählt ausdrücklich NICHT als
    Kollision (§2 des Auftrags) — eine Rückrufaktion kann mehrere Modelle eines
    Herstellers betreffen. Ohne bekannte Marke (marke=None) kann keine Kollision
    geprüft werden -> gilt als unauffällig (die Formatprüfung bleibt trotzdem
    wirksam).
    """
    if not marke:
        return False
    marken = _referenz_marken_index().get(kba.strip().upper())
    if not marken:
        return False
    return bool(marken - {_hersteller(marke)})


def kba_referenz_vertrauenswuerdig(kba: str | None, marke: str | None = None) -> bool:
    """Ob diese KBA-Referenz die Vertrauensstufe eines Rückrufs heben und dem
    Nutzer als belastbare Nummer gezeigt werden darf.

    Zwei unabhängige, konservative Bedingungen — beide müssen gelten:
      1. Format plausibel (siehe `kba_referenz_format_plausibel`).
      2. Keine markenübergreifende Kollision (siehe
         `kba_referenz_kollidiert_markenuebergreifend`).
    """
    kba = (kba or "").strip()
    if not kba_referenz_format_plausibel(kba):
        return False
    return not kba_referenz_kollidiert_markenuebergreifend(kba, marke)


def kba_referenz_anzeige(kba: str | None, marke: str | None = None) -> str | None:
    """Die Referenz, so wie sie angezeigt und für die Vertrauensstufe genutzt
    werden darf — oder None, wenn sie das Plausibilitätsgate nicht besteht.

    EIN zentraler Punkt, an dem alle Aufrufer (Evidence, DB-Kontext-Prompt,
    Kaufaktionen über die Insight-Quelle) dieselbe Entscheidung sehen — keine
    zweite, abweichende Anzeige-Logik irgendwo im Code.
    """
    kba = (kba or "").strip()
    return kba if kba and kba_referenz_vertrauenswuerdig(kba, marke) else None


def referenz_ist_belegt(r: dict | None) -> bool:
    """Ist die `kba_referenz` dieses Rückrufs INHALTLICH belegt — nicht nur
    formatplausibel?

    RECALL-VERIFICATION-PILOT (§9 des Auftrags: "Format plausibel != inhaltlich
    verified"). Die drei Funktionen `kba_referenz_format_plausibel` /
    `kba_referenz_kollidiert_markenuebergreifend` / `kba_referenz_vertrauenswuerdig`
    prüfen SCHREIBWEISE und Eindeutigkeit einer Nummer. Beides sind
    Plausibilitätsaussagen: eine frei erfundene, aber sechsstellige und nur einmal
    vergebene Nummer besteht sie mühelos. Der DATA-TRUTH-AUDIT hat genau das
    gemessen — kein einziges Referenzformat des Bestands entsprach echten
    KBA-Nummern, und trotzdem passierten 567 Werte die Formatprüfung.

    Belegt ist eine Referenz erst, wenn der Fakt selbst gegen eine amtliche Quelle
    geprüft und mit `status='verified'` in `fakt_verifikation` hinterlegt wurde.
    Diese Information hängt bereits als `_trust` am Rückruf-Dict (gesetzt von
    `app/fakt_verifikation.py::annotiere` über `app/database.py::get_baureihe`) —
    es wird hier weder eine neue Quelle gelesen noch eine neue Regel erfunden.

    WIRKUNG: nur ein `verified` Rückruf kann die stärkste Ohne-VIN-Stufe
    "variant_match" erreichen. Ein ungeprüfter bleibt auf "series_only" ("Für Teile
    der Baureihe gemeldet — per FIN prüfen"), bleibt aber vollständig sichtbar. Das
    macht die Floor-Vorbedingung aus `app/empfehlungs_floor.py` strukturell wahr:
    dort verlangt `darf_floor_tragen` ohnehin `trust == "verified"` — beide Bedingungen
    fallen jetzt zusammen, statt unabhängig voneinander gelten zu müssen.

    ZWEITE, GENAUSO WICHTIGE WIRKUNG: das Aufräumen unbelegter Referenzen wird
    nebenwirkungsfrei. Der Kollisionsindex ist bestandsabhängig — löscht man eine
    erfundene Nummer bei Fahrzeug A, kann dieselbe Nummer bei Fahrzeug B dadurch
    "kollisionsfrei" und damit vertrauenswürdig werden. Ohne dieses Gate hätte die
    Bereinigung der Pilot-Rückrufe zwei unbeteiligte Baureihen (BMW 5er G30,
    Mercedes S-Klasse W222) still auf "variant_match"/Confidence "hoch" gehoben.

    Fehlt `_trust` ganz (Aufrufer, die Rückruf-Dicts selbst bauen), gilt der Fakt
    als ungeprüft — fail-safe in die vorsichtige Richtung.
    """
    return ((r or {}).get("_trust") or "").strip().lower() == "verified"


def rueckruf_ist_belegt(r: dict | None, marke: str | None = None) -> bool:
    """Darf dieser Rückruf dem Nutzer überhaupt gezeigt werden?

    KAUFCHECK-RC1: Der Bestand enthält 731 Rückrufzeilen ohne amtliche Referenz —
    Altbestand aus der Erstbefüllung. Wo sie einzeln geprüft wurden, war das
    Ergebnis ernüchternd: beim BMW 3er G20 ist der "Bremskraftverstärker-Rückruf
    2020-03" eine Fehlzitation (die zitierte Quelle betraf 2013 den N20-Motor),
    der "Schweißnähte an der Lenkung"-Rückruf existiert in dieser Form nicht
    (der echte Lenkungsrückruf KBA 10009 betrifft die Spurstange). Ein Abgleich
    aller 731 Zeilen gegen den amtlichen KBA-Gesamtexport fand selbst mit sehr
    großzügigen Kriterien nur für 47 % überhaupt ein mögliches Gegenstück.

    Ein Rückruf ist eine Sicherheitsaussage über ein reales Fahrzeug. Ein
    erfundener Rückruf ist nicht "vorsichtig", sondern falsch — er schickt den
    Nutzer mit einer nicht existierenden Aktion zum Verkäufer. Sichtbar ist
    deshalb nur, was belegt ist:

      * eine plausible, nicht kollidierende KBA-Referenz (kba_referenz_anzeige), oder
      * eine Einzelverifikation mit status='verified' (z.B. über NHTSA belegt).

    Alles andere bleibt in der Datenbank, erscheint aber nirgends — weder in
    Insights, Key Findings, Kaufaktionen, LLM-Kontext noch im Chat.
    """
    if not r:
        return False
    # Zuerst die billige Prüfung: eine Einzelverifikation entscheidet sofort.
    # Die KBA-Kollisionsprüfung baut ihren Index je Aufruf neu auf und liegt hier
    # im heißen Pfad von `get_baureihe` (AutoFinder ruft ihn pro Kandidat auf).
    if (r.get("_trust") or "").strip().lower() == "verified":
        return True
    kba = (r.get("kba_referenz") or "").strip()
    return bool(kba and kba_referenz_anzeige(kba, marke))


def nur_belegte_rueckrufe(zeilen: list[dict] | None, marke: str | None = None) -> list[dict]:
    """Filtert unbelegte Rückrufe heraus (siehe `rueckruf_ist_belegt`)."""
    if not zeilen:
        return zeilen or []
    belegt = [r for r in zeilen if rueckruf_ist_belegt(r, marke)]
    if len(belegt) != len(zeilen):
        log.info("%d unbelegte(r) Rückruf(e) ohne amtliche Referenz ausgeblendet.",
                 len(zeilen) - len(belegt))
    return belegt


def _jahre(text: str | None) -> list[int]:
    return [int(y) for y in _JAHR.findall(text or "")]


def _baujahr_passt(betroffene: str | None, baujahr: int | None) -> bool | None:
    """Ob `baujahr` in die Baujahr-Angabe fällt.

    True  = fällt eindeutig hinein
    False = fällt eindeutig NICHT hinein (Rückruf ist für dieses Fahrzeug irrelevant)
    None  = nicht bestimmbar (allgemeine Angabe oder kein Baujahr) -> als bedingt werten
    """
    if betroffene is None:
        return None
    t = betroffene.strip().lower()
    if t in _ALLGEMEIN:
        return None
    if baujahr is None:
        return None
    jahre = _jahre(betroffene)
    if not jahre:
        return None

    unten, oben = _grenzen(t, jahre)
    if unten is None and oben is None:
        # Einzeljahr(e) ohne Grenzwort: exakte Mitgliedschaft, wie bisher.
        drin = baujahr in jahre
    else:
        drin = ((unten is None or baujahr >= unten)
                and (oben is None or baujahr <= oben))

    # Weiche Angaben schließen nie hart aus: "v.a. 2014-2015" heißt nicht
    # "nur 2014-2015", und "Alle Baujahre, besonders bis 2001" gilt für alle.
    # Die Tendenz zählt nur, wenn sie VOR der Jahresangabe steht und damit die
    # Jahre selbst einschränkt. In "2009-2014 (insbesondere frühe Baujahre)"
    # ist der Bereich hart, das "insbesondere" betrifft nur einen Teil davon.
    tendenz = _TENDENZ.search(t)
    erstes_jahr = _JAHR.search(t)
    if ((tendenz and erstes_jahr and tendenz.start() < erstes_jahr.start())
            or _ALLE_BAUJAHRE.search(t)):
        return True if drin else None
    if drin:
        return True
    # "bis ca. 2013": ein Jahr daneben ist unklar, nicht ausgeschlossen.
    if _UNGEFAEHR.search(t) and (unten is not None or oben is not None):
        abstand = (unten - baujahr) if (unten is not None and baujahr < unten) \
            else (baujahr - oben)
        if abstand <= 1:
            return None
    return False


def _grenzen(t: str, jahre: list[int]) -> tuple[int | None, int | None]:
    """Untere/obere Grenze einer Baujahresangabe, None = offen.

    Rückgabe (None, None) heißt: keine Grenze erkennbar, die Angabe nennt
    einzelne Jahre (exakte Mitgliedschaft).
    """
    if len(jahre) >= 2 and (_ECHTER_BEREICH.search(t) or _BEREICH.search(t)):
        return min(jahre), max(jahre)
    unten = oben = None
    m = _AB_GRENZE.search(t)
    if m:
        unten = int(m.group(1))
    m = _BIS_GRENZE.search(t)
    if m:
        oben = int(m.group(1))
    else:
        m = _VOR_GRENZE.search(t)
        if m:
            oben = int(m.group(1)) - 1
    if unten is not None or oben is not None:
        return unten, oben
    if _BEREICH.search(t):
        # Bisheriges Verhalten für Restformen wie "2014-" bleibt erhalten.
        return min(jahre), max(jahre)
    return None, None


# Signalwörter, die einen Rückruf auf Hochvolt-/Hybrid-/Elektro-Antrieb eingrenzen.
#
# BUGFIX (Insignia-Nachtrag): bis hierher wurde per reinem Substring-Vergleich
# gesucht (`any(w in text for w in _HV_WORTE)`). Damit traf "elektro" auch in
# "elektronisch", "Elektronik" und "elektromechanisch" — Wörter, die ELEKTRONIK
# beschreiben, nicht einen Hochvolt-ANTRIEB. Folge: jeder Rückruf, der ein
# elektronisches Steuergerät nennt, galt als Hochvolt-/PHEV-Rückruf und wurde
# für jedes Verbrennerfahrzeug als "incompatible" VOLLSTÄNDIG ausgeblendet —
# aus Findings, Kaufaktionen und jedem LLM-Prompt.
#
# Gemessen an der Datenbank betraf das 8 Rückrufe, darunter mehrere
# sicherheitsrelevante: Ausfall der Lenkunterstützung (Audi Q7, Audi RS 7, VW
# Tiguan, Mercedes A-Klasse), fehlerhafte Programmierung der elektronischen
# Feststellbremse (Toyota Corolla) und des elektronischen Stabilitätsprogramms
# (Toyota Camry, Toyota Hilux). Keiner davon war je für einen Verbrenner
# sichtbar. Aufgefallen ist es, weil der amtlich belegte Insignia-Rückruf
# KBA 12223 ("... weil das elektronische Bremssteuermodul nicht korrekt
# konfiguriert ist") aus demselben Grund unsichtbar blieb.
#
# Die Erkennung läuft jetzt über ein Muster mit Wortanfangs-Grenze und einem
# ausdrücklichen Ausschluss für "elektronisch*"/"Elektronik*"/
# "elektromechanisch*". Alles andere bleibt unverändert: "Hochvoltbatterie",
# "Elektromotor", "Plug-in-Hybrid" usw. treffen weiterhin.
#
# FOLGE-FIX (Safety-Check vor dem Insignia-012223-Merge): "elektrisch" war
# zusätzlich semantisch zu breit und wird hier ENTFERNT, nicht nur eingegrenzt.
# Eine "elektrische Kraftstoffpumpe", "elektrische Servolenkung", "elektrische
# Feststellbremse" oder "elektrische Zusatzwasserpumpe" ist 12-Volt-Technik in
# einem ganz gewöhnlichen Verbrenner — das sind serienmäßige Bauteile, keine
# Hochvoltsysteme. Gemessen an der Datenbank betraf das 29 Rückrufe, darunter
# sicherheitsrelevante: Ausfall der (elektrischen) Servolenkung an neun
# Baureihen (Mercedes A/E/S/GLA/GLC/AMG-GT, BMW i4, Opel Insignia/Zafira/Corsa/
# Astra), Ausfall der elektrischen Feststellbremse (Audi A8, Ford Focus), und
# fünf Kraftstoffpumpen-Rückrufe bei BMW. Jeder davon galt bislang für JEDEN
# Verbrenner der jeweiligen Baureihe als "incompatible" und war unsichtbar.
#
# NEUE SEMANTIK: statt eines Blacklist-Ausschlusses ("alles außer den drei
# Endungen") jetzt eine WHITELIST eindeutiger Hochvolt-/Antriebsbegriffe. Ein
# Rückruf grenzt sich nur dann auf einen bestimmten Antrieb ein, wenn der Text
# eines dieser Wörter enthält — sonst gilt er (wie jeder Rückruf ohne
# erkennbaren Antriebsbezug) für die ganze Baureihe unabhängig vom Motor. Kein
# NLP, keine neue Kategorie: dieselbe zweistufige Logik wie zuvor
# (Scope erkannt -> Antriebsabgleich; kein Scope -> Baureihen-Rückruf), nur mit
# einer engeren, eindeutigeren Wortliste.
_HV_MUSTER = re.compile(
    r"(?<![a-zäöüß])(?:"
    r"hochvolt|hochspannung|hv[-\s]?batterie|traktionsbatterie|antriebsbatterie|"
    r"plug-?\s?in|plugin|phev|hybrid|"
    r"elektro(?!nisch|nik|mechanisch)"
    r")",
    re.IGNORECASE,
)


# Normierung des Kraftstoffs (Motorvariante ODER Rückruf-Freitext-Qualifier).
def _norm_kraftstoff(text: str | None) -> str | None:
    t = (text or "").lower()
    if not t:
        return None
    if "mild" in t:
        return "mild"          # Mild-Hybrid (48V) — NICHT das Hochvolt-System eines PHEV/BEV
    if any(k in t for k in ("plug-in", "plug in", "plugin", "phev")):
        return "phev"
    if "elektro" in t or "electric" in t:
        return "elektro"
    if "hybrid" in t:
        return "phev"          # generisches "Hybrid" -> Hochvolt-Antrieb (nicht Mild s.o.)
    if "diesel" in t:
        return "diesel"
    if "benzin" in t or "petrol" in t:
        return "benzin"
    return None


def _paren_qualifier(betroffene: str | None) -> str | None:
    """Antriebs-Qualifier aus einem Klammerzusatz wie '2019-2020 (Plug-in-Hybrid)'."""
    if not betroffene:
        return None
    m = re.search(r"\(([^)]*)\)", betroffene)
    return _norm_kraftstoff(m.group(1)) if m else None


def _fahrzeug_achsen(motor_match: dict | None, identity=None):
    """(Kraftstoffart, mögliche Antriebsarten) für den Scope-Vergleich.

    Mit kanonischer Identität gilt nur sie. Ohne: `_kraftstoff_db` /
    `_kraftstoff_nutzer` (von app/evidence.py gesetzt) trennen DB-Rohwert und
    Nutzerangabe; fehlen sie (ältere Aufrufer, z.B. der Chat-Kontext), gilt
    `kraftstoff` wie bisher als Wert der erkannten Motorisierung."""
    from app.kraftstoff_powertrain import fahrzeug_achsen
    if identity is not None:
        return fahrzeug_achsen(identity=identity)
    m = motor_match or {}
    if "_kraftstoff_db" in m or "_kraftstoff_nutzer" in m:
        db, nutzer = m.get("_kraftstoff_db"), m.get("_kraftstoff_nutzer")
    else:
        db, nutzer = m.get("kraftstoff"), None
    return fahrzeug_achsen(db_kraftstoff=db, bezeichnung=m.get("bezeichnung"),
                           motorcode=m.get("motorcode"), nutzer_text=nutzer)


# Antriebe, die ein Hochvolt-System besitzen (für den Abgleich mit HV-Rückrufen).
_HAT_HOCHVOLT = {"phev", "elektro"}

# Reliability-Sprint 3 (§27/§28): "exakt" wurde bisher als "Betrifft dein Fahrzeug"
# angezeigt — allein aus Baujahr-Text-Match + vorhandener KBA-Referenznummer, OHNE
# jede VIN-/FIN-Prüfung (die es im System nicht gibt). Das war zu sicher formuliert.
# Vierstufige Semantik (fünfte Stufe reserviert, aktuell unerreichbar):
#   confirmed_by_vin — NUR nach echter VIN-Prüfung. Wird vom Code aktuell NIE erzeugt
#                       (keine VIN-Erfassung im System) — bewusst kein Fake-Feature.
#   variant_match     — der Rückruf grenzt sich AUSDRÜCKLICH auf einen Antrieb/eine
#                        Variante ein, VIRA kann die Bedingung auflösen, sie passt,
#                        das Baujahr passt UND die KBA-Referenz ist belegt. Ohne
#                        eine solche amtliche Bedingung wird diese Stufe NIE
#                        vergeben (Floor-Safety-Audit, Batch A).
#   series_only       — Baujahr passt bzw. allgemeiner Baureihen-Rückruf, aber ohne
#                        die belastbarste Kombination aus Variante+Referenz.
#   unclear           — Betroffenheit nicht bestimmbar.
#   incompatible       — Antriebs-/Variantenwiderspruch, wird vollständig ausgeblendet.

RUECKRUF_APPLICABILITY_TEXT: dict[str, str] = {
    "confirmed_by_vin": "Für dieses Fahrzeug per FIN bestätigt",
    "variant_match": "Kann Fahrzeuge dieser Variante betreffen: per FIN prüfen",
    "series_only": "Für Teile der Baureihe gemeldet: per FIN prüfen",
    "unclear": "Betroffenheit unklar: per FIN prüfen",
}


# ── Varianten-Scope eines Rückrufs (KaufCheck-Final-Stabilization, Cluster G) ──
#
# BEFUND (Production-Run Opel Astra K 1.4 Turbo 125 PS): ein Rückruf erschien,
# obwohl er nach externer Beleglage eine andere Motorisierung betrifft. Die
# Applicability kannte nur ZWEI Achsen: Baujahr und Hochvolt-Antrieb. Eine im
# Rückruf genannte Leistung, ein Hubraum, ein Motorcode, eine Kraftstoffart,
# eine Getriebe- oder Antriebsbedingung oder eine Ausstattungsabhängigkeit
# wurden nie gegen das Fahrzeug geprüft — jede Baureihen-Übereinstimmung
# genügte ("series match alone").
#
# REGEL (generisch, datengetrieben): aus dem Rückruf wird ein Scope gelesen —
# optional strukturiert (`scope_motorcodes`, `scope_leistung_ps`,
# `scope_hubraum`, `scope_kraftstoff`), sonst aus ausdrücklichen Angaben im
# amtlichen Text. Jede Scope-Dimension wird NUR gegen einen BEKANNTEN Wert der
# kanonischen Identität geprüft (Nutzerangabe oder eindeutige Referenz; ein
# mehrdeutiger Wert schließt nichts aus):
#   bekannter Widerspruch        -> NOT_APPLICABLE (wird entfernt)
#   Scope passt ausdrücklich     -> VARIANT_POSSIBLE
#   Scope da, Wert unbekannt     -> UNKNOWN
#   kein Scope                   -> SERIES_RELEVANT (FIN-first, nie "offen")
# Die ~1.400 Bestandszeilen werden dafür NICHT verändert.

RECALL_SERIES_RELEVANT = "SERIES_RELEVANT"
RECALL_VARIANT_POSSIBLE = "VARIANT_POSSIBLE"
RECALL_VEHICLE_POSSIBLE = "VEHICLE_POSSIBLE"
RECALL_NOT_APPLICABLE = "NOT_APPLICABLE"
RECALL_UNKNOWN = "UNKNOWN"

RECALL_STATE_AUS_APPLICABILITY = {
    "variant_match": RECALL_VARIANT_POSSIBLE, "series_only": RECALL_SERIES_RELEVANT,
    "unclear": RECALL_UNKNOWN, "incompatible": RECALL_NOT_APPLICABLE,
    "vehicle_possible": RECALL_VEHICLE_POSSIBLE,
}

_RE_SCOPE_PS = re.compile(r"\b(\d{2,3})\s*PS\b", re.I)
_RE_SCOPE_KW = re.compile(r"\b(\d{2,3})\s*kW\b", re.I)
_RE_SCOPE_HUBRAUM = re.compile(
    r"\b(\d[.,]\d)\s*(?:-?\s*l(?:iter)?\b|tsi|tfsi|tdi|turbo|cdti|dci|hdi|crdi|t-?gdi|ecoboost"
    r"|puretech|skyactiv|vtec|i-?vtec|-?\s*motor)", re.I)
_RE_SCOPE_MOTORCODE = re.compile(
    r"(?:motor(?:en|code|kennbuchstabe|kennung|typ)?|aggregat)\s*[:(]?\s*((?:[A-Z]{1,4}\d{2,3}[A-Z0-9]*"
    r"(?:\s*(?:,|/|und|oder)\s*)?)+)|\b([A-Z]{1,4}\d{2,3}[A-Z0-9]*)-?Motor", re.I)
_RE_SCOPE_DIESEL = re.compile(r"diesel(?:motor|fahrzeug|variante|modell)\w*|\bdiesel\b", re.I)
_RE_SCOPE_BENZIN = re.compile(r"benzin(?:er|motor|fahrzeug|variante)\w*|ottomotor\w*|\bbenziner\b", re.I)


def _bekannt(identity, feld: str):
    """Ein Identitätswert, der eine Aussage TRAGEN darf (nicht mehrdeutig,
    nicht unbekannt, kein Konflikt)."""
    if identity is None:
        return None
    fe = (getattr(identity, "field_evidence", None) or {}).get(feld) or {}
    if fe.get("verification_state") in ("ambiguous", "unknown"):
        return None
    wert = getattr(identity, feld, None)
    return wert if wert not in (None, "") else None


def _scope_zahlen(rx, text) -> set[int]:
    return {int(m) for m in rx.findall(text or "")}


def rueckruf_scope(r: dict, identity) -> tuple[str, str | None]:
    """Varianten-Scope eines Rückrufs gegen die kanonische Identität.

    Rückgabe (recall_state, begründung). Siehe Kommentar oben.

    Root-Cause-Audit RC-2/RC-3: `eingrenzung_amtlich` (additive Spalte, siehe
    `app/database.py::_migrate_schema`) fließt ab sofort in den gescannten
    Text ein — die amtliche Eingrenzung ist die PRÄZISESTE verfügbare Quelle
    für genau diese Scope-Dimensionen (PS/Hubraum/Motorcode/Kraftstoff), nicht
    nur ein Nebenprodukt von `mangel`/`abhilfe`. Ohne Backfill/Neuimport ist
    das Feld für Bestandszeilen NULL und ändert das Verhalten nicht."""
    text = " ".join(str(r.get(f) or "") for f in ("mangel", "abhilfe", "betroffene_baujahre",
                                                  "scope_text", "eingrenzung_amtlich"))
    passt_explizit: list[str] = []
    offen: list[str] = []

    # Leistung
    ps_scope = set(r.get("scope_leistung_ps") or []) or _scope_zahlen(_RE_SCOPE_PS, text)
    kw_scope = _scope_zahlen(_RE_SCOPE_KW, text)
    if kw_scope and not ps_scope:
        ps_scope = {round(k * 1.35962) for k in kw_scope}
    if ps_scope:
        ps = _bekannt(identity, "horsepower")
        if ps is None:
            offen.append("Leistung")
        elif not any(abs(int(ps) - s) <= 3 for s in ps_scope):
            return RECALL_NOT_APPLICABLE, (f"Rückruf nennt {', '.join(str(s) for s in sorted(ps_scope))} PS; "
                                           f"das Fahrzeug hat {ps} PS")
        else:
            passt_explizit.append("Leistung")

    # Hubraum
    hub_scope = {h.replace(",", ".") for h in (r.get("scope_hubraum") or [])} or \
        {m.replace(",", ".") for m in _RE_SCOPE_HUBRAUM.findall(text)}
    if hub_scope:
        hub = getattr(identity, "displacement", None) if identity is not None else None
        if not hub:
            offen.append("Hubraum")
        elif hub not in hub_scope:
            return RECALL_NOT_APPLICABLE, (f"Rückruf nennt {', '.join(sorted(hub_scope))} l Hubraum; "
                                           f"das Fahrzeug hat {hub} l")
        else:
            passt_explizit.append("Hubraum")

    # Motorcode
    codes: set[str] = {c.upper() for c in (r.get("scope_motorcodes") or [])}
    for m in _RE_SCOPE_MOTORCODE.finditer(text):
        for c in re.split(r"\s*(?:,|/|und|oder)\s*", (m.group(1) or m.group(2) or "")):
            c = c.strip().upper()
            if re.fullmatch(r"[A-Z]{1,4}\d{2,3}[A-Z0-9]*", c):
                codes.add(c)
    if codes:
        from app.vehicle_identity import _code_passt
        fe = ((getattr(identity, "field_evidence", None) or {}).get("engine_code") or {}) if identity else {}
        eigener = _bekannt(identity, "engine_code")
        moegliche = fe.get("possible_values") or ([eigener] if eigener else [])
        if not moegliche:
            offen.append("Motorcode")
        elif not any(_code_passt(mc, c) for mc in moegliche for c in codes):
            return RECALL_NOT_APPLICABLE, (f"Rückruf nennt Motorcode(s) {', '.join(sorted(codes))}; "
                                           f"das Fahrzeug: {', '.join(moegliche)}")
        elif eigener and any(_code_passt(eigener, c) for c in codes):
            passt_explizit.append("Motorcode")
        else:
            offen.append("Motorcode")

    # Kraftstoff (nur ausdrückliche Kraftstoffwörter)
    fuel_scope = set(r.get("scope_kraftstoff") or [])
    if not fuel_scope:
        if _RE_SCOPE_DIESEL.search(text) and not _RE_SCOPE_BENZIN.search(text):
            fuel_scope = {"diesel"}
        elif _RE_SCOPE_BENZIN.search(text) and not _RE_SCOPE_DIESEL.search(text):
            fuel_scope = {"benzin"}
    if fuel_scope:
        fuel = _norm_kraftstoff(str(_bekannt(identity, "fuel") or ""))
        if fuel is None:
            offen.append("Kraftstoff")
        elif fuel not in fuel_scope:
            return RECALL_NOT_APPLICABLE, f"Rückruf betrifft {'/'.join(sorted(fuel_scope))}-Varianten"
        else:
            passt_explizit.append("Kraftstoff")

    # Antriebsart aus der AMTLICHEN Eingrenzung (Root-Cause-Audit RC-2/RC-3,
    # Reproduktionsfall: "Es sind ausschließlich Fahrzeuge mit 2.0 TFSI und
    # Mild-Hybrid-System betroffen"). BEWUSST getrennt von der Hochvolt-/PHEV-
    # Heuristik weiter unten in `rueckruf_applicability()` (`_HV_MUSTER`): diese
    # erkennt nur Hochvolt-Systeme (PHEV/HEV/BEV) und würde "Mild-Hybrid" fälsch-
    # lich wie ein Hochvolt-Wort behandeln (das Wort "hybrid" steckt in "Mild-
    # Hybrid" drin) — MHEV ist eine eigene, NICHT-Hochvolt-Antriebsklasse
    # (`kraftstoff_powertrain.POWERTRAIN_MHEV`). Ausdrücklich NUR aus
    # `eingrenzung_amtlich` gelesen (nicht aus mangel/abhilfe), damit nur eine
    # amtlich belegte Bedingung ausgewertet wird, keine freie Interpretation.
    eingr_amtlich = str(r.get("eingrenzung_amtlich") or "")
    if eingr_amtlich:
        from app.kraftstoff_powertrain import powertrain_aus_freitext
        from app.kraftstoff_powertrain import scope_passt as _antrieb_scope_passt
        pt_wort = powertrain_aus_freitext(eingr_amtlich)
        pt_scope = {"MHEV": "mild", "PHEV": "phev", "HEV": "phev", "BEV": "elektro"}.get(pt_wort)
        if pt_scope:
            fuel_id, powertrains_id = _fahrzeug_achsen(None, identity=identity)
            ok = _antrieb_scope_passt(pt_scope, fuel_id, powertrains_id)
            if ok is False:
                return RECALL_NOT_APPLICABLE, (
                    f"Amtliche Eingrenzung nennt {pt_wort}-Antrieb als Bedingung; "
                    f"die erkannte Antriebsart passt nicht")
            if ok is None:
                offen.append("Antriebsart")
            else:
                passt_explizit.append("Antriebsart")

    # Komponentenabhängigkeit (Getriebe, Antrieb, Antriebsart, Ausstattung) —
    # dieselbe zentrale Präsenzlogik wie für Schwachstellen. Amtliche
    # Eingrenzung MIT einbezogen (RC-2/RC-3): eine Ausstattungsbedingung kann
    # ebenso gut dort stehen wie im Mangeltext (z.B. "Ausstattung mit
    # Handbediengerät ...").
    from app.ausstattung_praesenz import ABSENT, PRESENT as P_PRESENT, UNKNOWN as P_UNKNOWN, praesenz
    zustand, abh, bez = praesenz(f"{r.get('mangel') or ''} {eingr_amtlich}", identity,
                                 r.get("_ausstattung"), r.get("_freitext"))
    if zustand == ABSENT:
        return RECALL_NOT_APPLICABLE, f"Rückruf betrifft Fahrzeuge mit {abh.klasse}; nicht verbaut"
    if zustand == P_UNKNOWN and abh is not None:
        offen.append(abh.klasse)
    # Release-Hardening: bestätigt vorhandene Ausstattung durfte die Applicability
    # bisher nie heben — nur ABSENT (ausschließen) und UNKNOWN (offen) wurden
    # ausgewertet, PRESENT war ein No-op. Bestätigte Ausstattung, die der
    # Rückruf voraussetzt, darf die Stufe auf VARIANT_POSSIBLE heben (genau wie
    # ein passender PS-/Hubraum-/Motorcode-Scope oben) — NIEMALS weiter, denn
    # diese Funktion kennt ohnehin keine höhere Stufe als VARIANT_POSSIBLE
    # (ein VIN-bestätigtes "confirmed_by_vin"/"Rückruf offen" entsteht hier nie).
    elif zustand == P_PRESENT and abh is not None:
        passt_explizit.append(abh.klasse)

    if offen and not passt_explizit:
        return RECALL_UNKNOWN, "Variantenbedingung nicht prüfbar: " + ", ".join(offen)
    if passt_explizit:
        return RECALL_VARIANT_POSSIBLE, "Variantenbedingung passt: " + ", ".join(passt_explizit)
    return RECALL_SERIES_RELEVANT, None


def rueckruf_applicability(r: dict, passt: bool | None, kba: str, motor_match: dict | None,
                           marke: str | None = None, identity=None):
    """Bestimmt, WIE SICHER ein Rückruf GENAU DIESES Fahrzeug betrifft.

    Rückgabe: (applicability, confidence, einfluss, variant_hinweis)
      applicability: "variant_match" | "series_only" | "unclear" | "incompatible"
                      (theoretisch auch "confirmed_by_vin" — aktuell nie erzeugt)
      confidence:    Beleglage des Insights ("hoch"|"mittel"|"niedrig")
      einfluss:      Handlungshinweis
      variant_hinweis: Zusatztext für die Beschreibung (oder "")

    Strikt getrennte Konzepte:
      severity  = wie schlimm            (eigenes Feld, hier NICHT berührt)
      confidence= wie gut belegt         (Beleglage/Provenance)
      applicability = betrifft dieses Fahrzeug  (Varianten-/Antriebs-Zuordnung) —
        OHNE VIN-Prüfung niemals "confirmed_by_vin"/eine "Betrifft dein Fahrzeug
        garantiert"-Aussage (§27).

    `marke` (optional, KBA-Trust-Gate): nur mit ihr kann eine markenübergreifende
    Kollision der `kba_referenz` erkannt werden (siehe Modulkopf). Eine unplausible
    oder kollidierende Referenz zählt hier wie eine FEHLENDE — sie hebt die Stufe
    nicht auf "variant_match", der Rückruf selbst bleibt konservativ als
    "series_only" erhalten.
    """
    text = " ".join(filter(None, [r.get("mangel"), r.get("abhilfe"), r.get("betroffene_baujahre")]))
    ist_hv_rueckruf = bool(_HV_MUSTER.search(text))
    qualifier = _paren_qualifier(r.get("betroffene_baujahre"))       # z.B. "phev"
    # Final-Stabilization (Cluster D): Kraftstoffart und Antriebsart sind zwei
    # Achsen (app/kraftstoff_powertrain.py). Vorher wurde EIN normierter Wert
    # (Nutzerangabe "Benzin" ODER DB-Rohwert "Plug-in-Hybrid") gegen den Scope
    # verglichen — ein Hochvolt-Rückruf fiel dadurch bei einem Benzin-PHEV weg.
    fuel, powertrains = _fahrzeug_achsen(motor_match, identity)
    # KBA-Trust-Gate: eine unplausible/kollidierende Referenz zählt wie keine.
    # RECALL-PILOT (§9): und eine bloß FORMATPLAUSIBLE zählt ebenfalls wie keine.
    kba_ok = bool(kba_referenz_anzeige(kba, marke)) and referenz_ist_belegt(r)

    # Der Rückruf grenzt sich auf einen bestimmten Antrieb ein (Klammer-Qualifier
    # ODER klarer Hochvolt-/Hybrid-Bezug).
    scope = qualifier or ("phev" if ist_hv_rueckruf else None)
    if scope:
        from app.kraftstoff_powertrain import scope_passt
        matcht = scope_passt(scope, fuel, powertrains)
        if matcht is None:
            # Die für DIESEN Scope maßgebliche Achse ist unbekannt (Motor nicht
            # erkannt, nur eine Kraftstoffangabe zu einem Antriebsart-Scope,
            # mehrdeutige Elektrifizierung) -> Betroffenheit NICHT bestimmbar.
            return ("unclear", "niedrig",
                    f"Betroffenheit unklar: der Rückruf betrifft bestimmte Varianten. {_HINWEIS_FIN}",
                    "Für die Baureihe hinterlegt; die genaue Variantenbetroffenheit ist ohne erkannte Motorisierung nicht gesichert.")
        if matcht:
            # Passende Variante + Baujahr-Deckung + PLAUSIBLE KBA-Referenz -> stärkste
            # OHNE-VIN erreichbare Stufe: "kann diese Variante betreffen", nicht
            # "betrifft".
            if passt is True and kba_ok:
                return ("variant_match", "hoch",
                        f"Sicherheitsrelevant. {_HINWEIS_FIN}", "")
            return ("series_only", "mittel",
                    f"Sicherheitsrelevant. {_HINWEIS_FIN}", "")
        # Klarer Antriebs-Widerspruch (§8): z.B. Hochvolt-/PHEV-Rückruf, Fahrzeug ist
        # nachweislich Diesel. Die Motorisierung ist ERKANNT und passt eindeutig NICHT
        # -> "incompatible". Solche Rückrufe werden VOLLSTÄNDIG aus den sichtbaren
        # Findings UND aus jedem LLM-Prompt entfernt (kein Anzeigen als "unklare
        # Betroffenheit").
        scope_label = {"phev": "Plug-in-Hybrid-/Hochvolt-Varianten", "elektro": "Elektro-Varianten",
                       "diesel": "Diesel-Varianten", "benzin": "Benzin-Varianten",
                       "mild": "Mild-Hybrid-Varianten"}.get(scope, "bestimmte Varianten")
        return ("incompatible", "hoch",
                f"Betrifft laut Datenlage {scope_label}: die erkannte Motorisierung gehört nicht dazu.",
                f"Dieser Rückruf betrifft {scope_label}; die erkannte Motorisierung passt eindeutig nicht dazu.")

    # Kein Antriebs-Scope erkennbar -> allgemeiner BAUREIHEN-Rückruf (z.B. Bremse,
    # Lenkung). Er gilt für die ganze Baureihe im amtlichen Produktionsfenster.
    #
    # FLOOR-SAFETY-AUDIT (BATCH A): hier stand bisher `variant_match`, sobald das
    # Baujahr traf und die Referenz das Trust-Gate passiert hatte. Das war eine
    # Verwechslung der beiden Achsen — Baujahr-Deckung ist eine ZEITLICHE
    # Zuordnung, keine VARIANTEN-Aussage. Der amtliche Datensatz sagt in diesen
    # Fällen ausdrücklich nur "Modell + Produktionszeitraum" und im Beipacktext
    # der Rückrufdatenbank sogar wörtlich, dass "in der Regel nicht alle Fahrzeuge
    # des Typs auch tatsächlich betroffen" sind. Aus "dein Baujahr liegt im
    # Fenster" ein "deine Variante ist betroffen" zu machen, behauptet mehr als
    # die Quelle hergibt — und hob über `empfehlungs_floor` zugleich die
    # Kaufempfehlung an.
    #
    # Der Modulkopf beschrieb `series_only` schon immer als "allgemeiner
    # Baureihen-Rückruf"; der Code widersprach seinem eigenen Kommentar. Gemessen
    # am Batch-A-Bestand betraf das 260 von 269 neuen Zeilen.
    #
    # Die BELEGLAGE geht dabei nicht verloren: eine amtlich verifizierte Referenz
    # hebt weiterhin die `confidence` auf "hoch" und den Wortlaut auf die
    # FIN-Prüfung. Nur die Varianten-BEHAUPTUNG entfällt. Was den Rückruf
    # tatsächlich auf ein Exemplar eingrenzt, ist die FIN — und die hat VIRA nicht.
    #
    # Final-Stabilization (Cluster G): bevor ein Rückruf als Baureihen-Rückruf
    # gilt, wird sein Varianten-Scope gegen die KANONISCHE Identität geprüft.
    if identity is not None:
        scope_state, grund = rueckruf_scope(r, identity)
        if scope_state == RECALL_NOT_APPLICABLE:
            return ("incompatible", "hoch", f"Betrifft laut Datenlage nicht dieses Fahrzeug: {grund}.",
                    f"Nicht zutreffend: {grund}.")
        if scope_state == RECALL_UNKNOWN:
            return ("unclear", "niedrig",
                    f"Betroffenheit unklar: der Rückruf betrifft bestimmte Varianten. {_HINWEIS_FIN}",
                    f"{grund}.")
        if scope_state == RECALL_VARIANT_POSSIBLE:
            if passt is True and kba_ok:
                return ("variant_match", "hoch", f"Sicherheitsrelevant. {_HINWEIS_FIN}", f"{grund}.")
            return ("series_only", "mittel", f"Sicherheitsrelevant. {_HINWEIS_FIN}", f"{grund}.")
    if passt is True:
        if kba_ok:
            return ("series_only", "hoch",
                    f"Sicherheitsrelevant: betrifft die Baureihe im gemeldeten "
                    f"Zeitraum. {_HINWEIS_FIN}", "")
        return ("series_only", "mittel",
                f"Sicherheitsrelevant. {_HINWEIS_FIN}", "")
    return ("series_only", "mittel",
            f"Sicherheitsrelevant. Baujahr-Zuordnung nicht eindeutig. {_HINWEIS_FIN}", "")


def _annotiere(r: dict, motor_match: dict | None, baujahr: int | None,
               marke: str | None = None, identity=None) -> dict:
    """Baut EINE annotierte Kopie eines Rückruf-Datensatzes: Original-Felder +
    applicability/confidence/einfluss/variant_hinweis + ein fertig formatierter
    `text` (für Prompt-/DB-Kontext-Einbettung, MIT Applicability-Formulierung statt
    nacktem mangel/abhilfe).

    KBA-Trust-Gate: `kba_referenz_anzeige` ist die EINZIGE Referenz, die Aufrufer
    dem Nutzer zeigen dürfen (Roh-`kba_referenz` bleibt über `**r` zwar im Dict,
    aber ausschließlich für Diagnosezwecke — nicht für die Anzeige gedacht).

    `identity` (optional, RC-1-Fix): ohne sie prüft `rueckruf_applicability`
    nur die Hochvolt-/PHEV-Schlüsselwort-Heuristik (HV_MUSTER/Klammerzusatz) —
    der strukturierte `rueckruf_scope()`-Motor (Leistung/Hubraum/Motorcode/
    Kraftstoff-Text/Ausstattungspräsenz) läuft NUR mit einer kanonischen
    Identität. Alle Aufrufer dieses Moduls sollen ihre Identität durchreichen,
    sobald sie eine haben — das macht `gefilterte_rueckrufe` endgültig zur
    EINEN Allowed-List, unabhängig davon, für welchen Ausgabe-Pfad sie
    aufgerufen wird. Ohne `identity` (Default) bleibt das Verhalten BYTEGLEICH
    zum bisherigen Code — kein bestehender Aufrufer ändert sich ungefragt."""
    passt = _baujahr_passt(r.get("betroffene_baujahre"), baujahr)
    kba = (r.get("kba_referenz") or "").strip()
    kba_anzeige = kba_referenz_anzeige(kba, marke)
    applicability, confidence, einfluss, variant_hinweis = rueckruf_applicability(
        r, passt, kba, motor_match, marke=marke, identity=identity)
    beschr = (r.get("mangel") or "").strip()
    if r.get("abhilfe"):
        beschr = f"{beschr}{'' if beschr.endswith(('.', '!', '?')) else '.'} Abhilfe: {r['abhilfe'].strip()}"
    if r.get("datum"):
        beschr = f"{beschr} (Rückruf {r['datum']})"
    wortlaut = RUECKRUF_APPLICABILITY_TEXT.get(applicability, applicability)
    text = f"{beschr} [{wortlaut}]" + (f". {variant_hinweis}" if variant_hinweis else "")
    return {
        **r,
        "passt_baujahr": passt,
        "applicability": applicability,
        "confidence": confidence,
        "einfluss": einfluss,
        "variant_hinweis": variant_hinweis,
        "text": text,
        "kba_referenz_anzeige": kba_anzeige,
    }


def gefilterte_rueckrufe(rueckrufe: list[dict] | None, motor_match: dict | None,
                         baujahr: int | None, marke: str | None = None,
                         identity=None) -> list[dict]:
    """Die EINE zentrale Allowed-List (§Phase 7): nur Rückrufe, die dieses Fahrzeug
    laut Datenlage betreffen KÖNNTEN — Baujahr-eindeutig-unpassende UND Antriebs-
    widersprüchliche ("incompatible", z.B. Hochvolt-Rückruf bei erkanntem Diesel)
    Rückrufe werden entfernt. Jeder zurückgegebene Eintrag trägt zusätzlich
    `applicability`/`confidence`/`einfluss`/`text` (fertig für Insights, Key
    Findings, DB-Kontext, LLM-Prompt, Chat-Kontext, Risikoübersicht — EIN Aufruf,
    EIN Ergebnis für alle Konsumenten).

    `marke` (optional, KBA-Trust-Gate): ermöglicht die markenübergreifende
    Kollisionsprüfung der `kba_referenz`. Ohne sie bleibt die Formatprüfung
    trotzdem wirksam — nur die Kollisionsprüfung entfällt dann.

    `identity` (optional, Root-Cause-Audit RC-1): kanonische `VehicleIdentity`.
    Durchgereicht an `rueckruf_applicability`/`rueckruf_scope` — damit trifft
    JEDER Aufrufer, der eine Identität besitzt, dieselbe, vollständige
    Varianten-/Antriebs-/Ausstattungsentscheidung wie `app/evidence.py`, statt
    nur die ältere Hochvolt-Schlüsselwort-Heuristik zu sehen. Ohne `identity`
    (Default `None`) ist das Verhalten unverändert — bestehende Aufrufer ohne
    Identität (z.B. der allgemeine Chat-Kontext) ändern sich nicht."""
    out = []
    for r in rueckrufe or []:
        passt = _baujahr_passt(r.get("betroffene_baujahre"), baujahr)
        if passt is False:
            continue
        annotiert = _annotiere(r, motor_match, baujahr, marke=marke, identity=identity)
        if annotiert["applicability"] == "incompatible":
            continue
        out.append(annotiert)
    return out


def ausgeschlossene_rueckrufe(rueckrufe: list[dict] | None, motor_match: dict | None,
                              baujahr: int | None, marke: str | None = None,
                              identity=None) -> list[dict]:
    """Komplement zu `gefilterte_rueckrufe` — Rückrufe, die für dieses Fahrzeug
    NACHWEISLICH NICHT gelten (Baujahr-unpassend ODER applicability=="incompatible").
    Grundlage für den Report-Validator (§Phase 8): welche Begriffe dürfen im
    fertigen LLM-Bericht NICHT auftauchen.

    `identity` (optional, RC-1): siehe `gefilterte_rueckrufe` — dieselbe
    Durchreichung, damit Allowed- und Excluded-Liste IMMER auf derselben
    Entscheidung beruhen, nie auf zwei unabhängig berechneten."""
    out = []
    for r in rueckrufe or []:
        passt = _baujahr_passt(r.get("betroffene_baujahre"), baujahr)
        if passt is False:
            out.append({**r, "ausschlussgrund": "baujahr_unpassend"})
            continue
        annotiert = _annotiere(r, motor_match, baujahr, marke=marke, identity=identity)
        if annotiert["applicability"] == "incompatible":
            out.append({**annotiert, "ausschlussgrund": "antrieb_unpassend"})
    return out
