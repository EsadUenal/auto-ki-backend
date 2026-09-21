from __future__ import annotations

"""
P1-4 — Fahrzeugkontext: vorhandene DB-Felder kontrolliert nutzbar machen.

Der ENFAL-Datenbestand enthält pro Baureihe mehrere Felder, die der Kaufcheck
bislang GAR NICHT gelesen hat (gemessen: sie tauchen weder in `build_db_context`
noch in `build_insights` noch in den Kaufaktionen auf — geschrieben werden sie nur
von `admin_llm`/`db_writer`). Dieses Modul holt sie kontrolliert herein.

VERTRAUENSSTUFEN (§9) — der zentrale Punkt dieses Moduls

  A) Strukturierte DB-Fakten
     `segment` (kurzes Vokabular) und `wartung_oel_km` (INTEGER, 10.000–30.000 km).
     Maschinell auswertbar, eindeutig.

  B) Hilfreicher DB-Freitext
     `erkennung_generation`, `facelift_merkmale`, `wartung_hu_intervall`,
     `vorgaenger`. Redaktionell gepflegt und inhaltlich brauchbar, aber
     unstrukturiert und im Einzelfall unscharf.

  C) Nicht ausreichend vertrauenswürdig
     `kaufberatung` — nur 94 von 421 Baureihen (22 %), und der vorhandene Text ist
     werblich ("exzellente Kombination aus sportlicher Fahrdynamik"). Genau die
     Marketingsprache, die der Kaufcheck-Systemprompt verbietet. Dieses Feld wird
     hier NICHT gelesen, NICHT in den Prompt kopiert, NICHT zu Evidence und NICHT
     für Kaufaktionen verwendet. Eine spätere Reinigung wäre ein eigener Schritt.

Was A und B GEMEINSAM haben, und warum das wichtig ist: beides ist KONTEXT, keine
Evidence. Diese Felder werden bewusst NICHT zu Insights. Ein Fließtext zur
Generationserkennung darf nicht dieselbe Vertrauensstufe erhalten wie ein
KBA-Rückruf oder eine strukturierte Schwachstelle — sonst wäre die gesamte
Provenance-Architektur aus Phase 1 entwertet. Der Kontext hilft beim VERSTEHEN des
Fahrzeugs; er begründet keine Aussage über seinen Zustand.

Die bestehende Fahrzeugidentität (`VehicleIdentity`, `find_baureihe`,
`find_motor`) bleibt führend (§3). Nichts hier fließt in die Erkennung zurück —
diese Felder beschreiben die BEREITS erkannte Baureihe, sie bestimmen sie nicht.

Bewusst NICHT Teil von P1-4 (gehört zu P2-5, §13): Es wird nichts gegen den
Kilometerstand gerechnet. Kein "Ölwechsel fällig", kein "überfällig", keine
km/Jahr, kein Laufleistungs-Risiko. `wartung_oel_km` wird als Herstellerintervall
bereitgestellt — mehr nicht.

Und ausdrücklich keine Kilometerlogik für die HU (§8): Die Hauptuntersuchung ist
in Deutschland zeitgesteuert, nicht kilometergesteuert. `wartung_hu_intervall`
wird deshalb als Freitext durchgereicht und nie in eine Laufleistungsrechnung
gezogen.
"""

import logging
import json
import re

from app.models import Fahrzeugkontext

log = logging.getLogger(__name__)

# Obergrenze für die beiden langen Freitextfelder im Prompt (§10: kompakt halten).
# Gemessen: `erkennung_generation` Ø 408 / max 761 Zeichen, `facelift_merkmale`
# Ø 374 / max 745. Bei 500 Zeichen bleibt der ganz überwiegende Teil vollständig
# erhalten, und der Prompt wächst im schlimmsten Fall um ~1.000 Zeichen.
MAX_FREITEXT = 500

# `segment` ist zu 99,8 % befüllt, das Vokabular aber nicht vollständig sauber:
# neben "Kompaktklasse"/"Mittelklasse"/"Kompakt-SUV" stehen 8 Datensätze mit
# blossen Segmentbuchstaben ("A", "D-Segment"). Die sind für einen Nutzer wertlos
# und werden verworfen, statt sie als Fahrzeugeinordnung auszugeben.
_SEGMENT_UNBRAUCHBAR = re.compile(r"^[a-f]([ -]?segment)?$", re.IGNORECASE)


def _text(wert) -> str | None:
    """Freitext normalisieren; leere Strings und Platzhalter werden zu None.

    Wichtig, weil die DB leere Strings statt NULL enthält (z.B. 36 Baureihen mit
    `wartung_hu_intervall = ''`) — ohne diese Normalisierung entstünden leere
    Prompt-Zeilen und leere Frontend-Felder.
    """
    if wert is None:
        return None
    t = str(wert).strip()
    if not t or t in ("-", "—", "n/a", "N/A", "null", "None", "?"):
        return None
    return t


def _kuerze(wert: str | None, maxlen: int = MAX_FREITEXT) -> str | None:
    """Freitext auf `maxlen` kürzen — möglichst an einer Satzgrenze.

    Bewusst am letzten vollständigen Satz abgeschnitten statt mitten im Wort: ein
    halber Satz im Prompt lädt das Modell dazu ein, ihn selbst zu Ende zu denken.
    Findet sich keine Satzgrenze im hinteren Drittel, wird hart gekürzt und mit
    Auslassungszeichen als unvollständig markiert.
    """
    t = _text(wert)
    if t is None or len(t) <= maxlen:
        return t
    schnitt = t[:maxlen]
    ende = max(schnitt.rfind(". "), schnitt.rfind("! "), schnitt.rfind("? "))
    if ende >= maxlen // 2:
        return schnitt[:ende + 1]
    return schnitt.rstrip() + " …"


def _segment(baureihe: dict) -> str | None:
    s = _text(baureihe.get("segment"))
    if s is None or _SEGMENT_UNBRAUCHBAR.match(s):
        return None
    return s


def _oel_km(baureihe: dict) -> int | None:
    """`wartung_oel_km` als Zahl — Stufe A, das einzige wirklich strukturierte
    Wartungsfeld. Unplausible Werte werden verworfen statt weitergereicht."""
    wert = baureihe.get("wartung_oel_km")
    if wert in (None, ""):
        return None
    try:
        km = int(wert)
    except (TypeError, ValueError):
        log.info("Fahrzeugkontext: wartung_oel_km nicht numerisch (%r) — verworfen", wert)
        return None
    # Gemessene Spanne im Bestand: 10.000–30.000 km. Der Rahmen ist bewusst weit
    # gefasst; er soll nur offensichtlichen Datenmüll (0, 5, 3.000.000) abfangen.
    if not (1_000 <= km <= 100_000):
        log.info("Fahrzeugkontext: wartung_oel_km unplausibel (%s km) — verworfen", km)
        return None
    return km


def _aufloeser_aus_cache(baureihe_id: str) -> dict | None:
    """Standard-Auflöser: nutzt die BEREITS GECACHTE Referenzliste aller Baureihen
    (`get_alle_baureihen_kurz`, dieselbe Quelle, die der Kaufcheck ohnehin lädt).

    Bewusst KEINE neue DB-Funktion und keine zusätzliche Abfrage: der Cache enthält
    id/marke/modell/generation bereits, ein linearer Durchlauf über 421 Zeilen ist
    gegenüber einem weiteren SQLite-Zugriff vernachlässigbar.
    """
    from app.database import get_alle_baureihen_kurz   # lokal: kein Zirkelimport
    for b in get_alle_baureihen_kurz():
        if b.get("id") == baureihe_id:
            return b
    return None


def _vorgaenger(baureihe: dict, aufloeser=None) -> str | None:
    """Vorgängergeneration als LESBARER Name — oder None.

    Das Feld ist im Bestand uneinheitlich befüllt (gemessen über 305 befüllte
    Datensätze):

      209  eine echte Baureihen-ID ("opel-insignia-a")  -> wird zum Klarnamen
            aufgelöst ("Opel Insignia A")
       52  bereits menschenlesbar ("F30/F31", "BMW 3er E92/E93") -> unverändert
       44  slug-artig, aber die ID existiert NICHT ("mercedes-benz-e-klasse-w124")
            -> VERWORFEN

    Die dritte Gruppe wird bewusst nicht ausgegeben: Sie liesse sich nur durch
    Raten in einen Namen zurückverwandeln ("mercedes benz e klasse w124"), und ein
    geratener Modellname ist schlechter als gar keine Angabe. Der Kaufcheck sagt
    dann schlicht nichts über den Vorgänger.

    `aufloeser` ist eine Funktion id -> dict|None (Vorgabe: DB-Zugriff), damit
    dieser Pfad testbar bleibt, ohne eine echte Datenbank zu brauchen.
    """
    roh = _text(baureihe.get("vorgaenger"))
    if roh is None:
        return None
    # Sieht es nicht wie ein Slug aus, ist es bereits ein lesbarer Name.
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", roh):
        return roh
    if aufloeser is None:
        aufloeser = _aufloeser_aus_cache
    try:
        treffer = aufloeser(roh)
    except Exception:            # DB-Ausfall darf den Kaufcheck nicht kippen
        log.info("Fahrzeugkontext: Vorgänger '%s' nicht auflösbar (DB-Fehler)", roh)
        return None
    if not treffer:
        log.info("Fahrzeugkontext: Vorgänger-Slug '%s' zeigt auf keine bekannte "
                 "Baureihe — nicht ausgegeben", roh)
        return None
    name = " ".join(str(treffer.get(k) or "").strip()
                    for k in ("marke", "modell", "generation")).strip()
    return re.sub(r"\s+", " ", name) or None


# Hersteller mit fahrzeugabhaengig berechnetem Serviceintervall. Bewusst nur,
# wo das System eindeutig belegt ist: BMW (und MINI) fuehren seit den
# Baureihen ab 2003 den Condition Based Service — die Faelligkeit berechnet das
# Fahrzeug selbst und zeigt sie im Service-Menue. Eine starre km-Zahl ist dort
# keine Herstellervorgabe. Fuer alle anderen Marken bleibt der neutrale Hinweis.
_VARIABLER_SERVICE: dict[str, tuple[int, str]] = {
    "bmw": (2003, "Condition Based Service (CBS)"),
    "mini": (2003, "Condition Based Service (CBS)"),
}


def _wartungssystem(baureihe: dict) -> str | None:
    marke = (baureihe.get("marke") or "").strip().lower()
    eintrag = _VARIABLER_SERVICE.get(marke)
    if not eintrag:
        return None
    ab, name = eintrag
    try:
        von = int(baureihe.get("bauzeitraum_von") or 0)
    except (TypeError, ValueError):
        return None
    return name if von >= ab else None


def _oel_hinweis(system: str | None, km: int | None) -> str | None:
    if km is None:
        return None
    if system:
        return (f"Das Fahrzeug berechnet die Servicefälligkeit selbst ({system}): "
                f"die aktuelle Fälligkeit steht im Service-Menü des Fahrzeugs. Der Wert "
                f"aus der Datenbank ist nur eine Orientierung, keine feste Herstellervorgabe.")
    return ("Richtwert aus der Fahrzeugdatenbank. Maßgeblich ist die Service-Vorgabe des "
            "Herstellers bzw. die Serviceanzeige im Fahrzeug.")


def generationslabel(baureihe: dict | None, karosserie_text: str | None) -> str | None:
    """Werkscode passend zur im Inserat genannten Karosserie ("G20" statt "G20/G21").

    Wiederverwendung der AutoFinder-Aufloesung (`loese_generationslabel`) —
    keine zweite Logik. Nur wenn der Inserattext GENAU EINE Karosserieklasse nennt
    und die Baureihe eine gepruefte `chassis_codes`-Zuordnung hat; sonst bleibt das
    Sammellabel stehen. Es wird nie ein Code geraten.
    """
    if not baureihe:
        return None
    generation = _text(baureihe.get("generation"))
    if not karosserie_text or not baureihe.get("chassis_codes"):
        return generation
    from app.autofinder_norm import normalisiere_karosserie_text
    from app.autofinder_variante import loese_generationslabel
    klassen = normalisiere_karosserie_text(karosserie_text)
    if len(klassen) != 1:
        return generation
    codes = baureihe.get("chassis_codes")
    # `get_baureihe` liefert die Zuordnung bereits geparst (dict), die AutoFinder-
    # Funktion erwartet den gespeicherten JSON-Text — sonst faellt sie still auf
    # das Sammellabel zurueck.
    if isinstance(codes, dict):
        codes = json.dumps(codes, ensure_ascii=False)
    return loese_generationslabel(generation, codes, next(iter(klassen)))


def build_fahrzeugkontext(baureihe: dict | None, *, aufloeser=None,
                          karosserie_text: str | None = None) -> Fahrzeugkontext | None:
    """Baut den strukturierten Fahrzeugkontext — oder None ohne erkannte Baureihe.

    Es werden AUSSCHLIESSLICH tatsächlich vorhandene Werte übernommen. Fehlt ein
    Feld, bleibt es None; es wird nie ein Platzhalter, eine Schätzung oder ein
    "nicht erfasst" erzeugt. Sind am Ende alle Felder leer, liefert die Funktion
    None statt eines leeren Objekts.

    Marktdaten spielen hier keine Rolle: die Funktion bekommt weder Marktanalyse
    noch Preis und liefert bei `completed_no_market` exakt dasselbe Ergebnis (§14).
    """
    if not baureihe:
        return None

    ctx = Fahrzeugkontext(
        baureihe_id=_text(baureihe.get("id")),
        generation=generationslabel(baureihe, karosserie_text),
        segment=_segment(baureihe),
        vorgaenger=_vorgaenger(baureihe, aufloeser),
        erkennung_generation=_kuerze(baureihe.get("erkennung_generation")),
        facelift_merkmale=_kuerze(baureihe.get("facelift_merkmale")),
        wartung_oel_km=_oel_km(baureihe),
        wartung_system=_wartungssystem(baureihe),
        wartung_oel_hinweis=_oel_hinweis(_wartungssystem(baureihe), _oel_km(baureihe)),
        wartung_hu_intervall=_text(baureihe.get("wartung_hu_intervall")),
    )
    if not ctx.hat_inhalt():
        return None
    return ctx


def prompt_block(ctx: Fahrzeugkontext | None) -> str:
    """Kompakter Prompt-Abschnitt — leerer String, wenn nichts vorliegt.

    Zwei Dinge macht dieser Block bewusst explizit:

    1. Er ist als ERGÄNZENDER Kontext überschrieben, nicht als Befund. Ohne diese
       Kennzeichnung würde das Modell einen Generations-Fließtext genauso
       behandeln wie eine geprüfte Schwachstelle.
    2. Er verbietet ausdrücklich, aus dem Ölwechsel-Intervall eine Fälligkeit
       abzuleiten. Das Intervall ist eine Herstellerangabe; die Verrechnung mit dem
       Kilometerstand ist bewusst noch nicht gebaut (P2-5), und ein Modell, das
       "Ölwechsel überfällig" schreibt, würde diese Aussage frei erfinden.

    Es werden nur vorhandene Werte gezeigt — keine "nicht erfasst"-Zeilen (§10).
    """
    if ctx is None:
        return ""
    zeilen: list[str] = []
    if ctx.segment:
        zeilen.append(f"Fahrzeugsegment: {ctx.segment}")
    if ctx.vorgaenger:
        zeilen.append(f"Vorgängergeneration: {ctx.vorgaenger}")
    if ctx.wartung_oel_km:
        km = f"{ctx.wartung_oel_km:,}".replace(",", ".")
        zeilen.append(f"Ölwechsel-Richtwert (Fahrzeugdatenbank, KEINE starre "
                      f"Herstellervorgabe): {km} km")
    if ctx.wartung_system:
        zeilen.append(f"Serviceintervall wird fahrzeugabhängig berechnet: {ctx.wartung_system}. "
                      "Nenne keine feste km-Zahl als Herstellerintervall, sondern verweise "
                      "auf die Serviceanzeige im Fahrzeug.")
    if ctx.wartung_hu_intervall:
        zeilen.append(f"HU-Intervall (Angabe aus der Fahrzeugdatenbank): {ctx.wartung_hu_intervall}")
    if ctx.erkennung_generation:
        zeilen.append(f"Merkmale dieser Generation: {ctx.erkennung_generation}")
    if ctx.facelift_merkmale:
        zeilen.append(f"Facelift-Merkmale dieser Baureihe: {ctx.facelift_merkmale}")
    if not zeilen:
        return ""
    kopf = [
        "## Fahrzeug-Zusatzkontext (Fahrzeugdatenbank, ERGÄNZEND)",
        "Diese Angaben beschreiben die Baureihe allgemein. Sie sind KEINE geprüfte "
        "Evidence zu diesem konkreten Fahrzeug und KEIN Befund: nutze sie nur zur "
        "Einordnung und zur Beschreibung erkennbarer Merkmale, niemals als Mangel.",
    ]
    # Die beiden Schutzregeln stehen NUR dort, wo sie etwas bewachen. Ein Verbot zum
    # Ölwechsel-Intervall in einem Prompt, der gar kein Intervall nennt, wäre nicht
    # nur überflüssiger Text (§10) — es würde das Thema überhaupt erst einführen.
    if ctx.wartung_oel_km:
        kopf.append(
            "Aus dem Ölwechsel-Intervall darf KEINE Fälligkeit abgeleitet werden: "
            "schreibe niemals, ein Service sei fällig, überfällig oder versäumt worden.")
    if ctx.wartung_hu_intervall:
        kopf.append(
            "Das HU-Intervall ist zeitbezogen und darf NICHT mit dem Kilometerstand "
            "verrechnet werden.")
    return "\n".join([*kopf, *zeilen])
