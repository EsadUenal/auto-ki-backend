from __future__ import annotations

"""
"Inserat im Vergleich": deterministisch, mit Herkunft je Wert.

BEFUND (Production-Run BMW M4 F82, Befunde G/H/I)
------------------------------------------------
Die Tabelle schrieb das Modell. Der Prompt verlangte für JEDE Zeile eine Spalte
"DB-/Markterwartung" und ein Plausibilitätsurteil, auch dort, wo ENFAL gar
keine Referenz besitzt. Das Modell füllte die Lücke:

    Vorbesitzer      3               DB-/Markterwartung "1-3"        Plausibel
    Servicehistorie  teilweise       "digital / Condition Based ..."  Selten
    Kilometerstand   69.500 km       "~7.000 km/Jahr"                 Plausibel

"1-3 Vorbesitzer" ist keine Statistik, die ENFAL hat. "Condition Based
Service" beschreibt das Wartungssystem, nicht, wie oft Historien lückenhaft
sind. Und "~7.000 km/Jahr" ist der aus DIESEM Fahrzeug errechnete Wert, keine
Markterwartung.

DIE REGEL
---------
Jeder Wert trägt seine Herkunft, und verglichen wird nur, was vergleichbar ist:

    inserat       Angabe des Inserats bzw. des Nutzers
    berechnet     aus Angaben DIESES Fahrzeugs berechnet (km pro Jahr)
    db            ENFAL-Fahrzeugdatenbank (Spezifikation der erkannten Variante)
    markt         echte Markt-/Vergleichsdaten (nur mit belastbarem Median)
    amtlich       gesetzliche Regel (HU-Intervall)
    keine         keine Referenz vorhanden

Ohne Referenz gibt es KEINE Einordnung: dort steht "nicht bewertbar", nie ein
Häkchen. Das Wort "Markterwartung" erscheint nur bei Herkunft `markt`.

Die Tabelle ersetzt den Abschnitt des Modells vollständig. Das Modell schreibt
nur noch die Überschrift (app/kaufcheck.py); fehlt der Abschnitt in einem
vollständigen Bericht, wird er vor der Checkliste eingefügt.
"""

import re
from dataclasses import dataclass

from app.getriebe import AUTOMATIK, MANUELL, anzeige as getriebe_anzeige, aus_db as getriebe_aus_db
from app.hu_termin import ABGELAUFEN, PLAUSIBEL, UNGEWOEHNLICH_WEIT
from app.servicehistorie import anzeige as servicehistorie_anzeige

HERKUNFT_INSERAT = "inserat"
HERKUNFT_BERECHNET = "berechnet"
HERKUNFT_DB = "db"
HERKUNFT_MARKT = "markt"
HERKUNFT_AMTLICH = "amtlich"
HERKUNFT_KEINE = "keine"

_HERKUNFT_LABEL = {
    HERKUNFT_INSERAT: "Inserat",
    HERKUNFT_BERECHNET: "berechnet für dieses Fahrzeug",
    HERKUNFT_DB: "ENFAL-Fahrzeugdaten",
    HERKUNFT_MARKT: "Marktvergleich",
    HERKUNFT_AMTLICH: "gesetzliche Regel",
}

PASST = "✓ passt"
WEICHT_AB = "⚠ weicht ab"
WIDERSPRUCH = "❌ Widerspruch"
NICHT_BEWERTBAR = "nicht bewertbar"
KEINE_REFERENZ = "keine Vergleichsdaten"
# Wortlaut wie in `postprocess.neutralisiere_preiszeile_ohne_markt` (RC1).
KEINE_MARKTBASIS = "keine belastbare Marktbasis"


@dataclass(frozen=True)
class Vergleichszeile:
    kriterium: str
    angabe: str
    referenz: str | None           # None = keine Referenz
    referenz_herkunft: str         # HERKUNFT_*
    einordnung: str                # PASST | WEICHT_AB | WIDERSPRUCH | NICHT_BEWERTBAR | ...
    ohne_referenz: str = KEINE_REFERENZ   # Zellentext, wenn keine Referenz vorliegt

    def zelle_referenz(self) -> str:
        if self.referenz is None or self.referenz_herkunft == HERKUNFT_KEINE:
            return self.ohne_referenz
        return f"{self.referenz} ({_HERKUNFT_LABEL[self.referenz_herkunft]})"


def _km(n: int) -> str:
    return f"{n:,} km".replace(",", ".")


def _eur(n: int) -> str:
    return f"{n:,} €".replace(",", ".")


_KRAFTSTOFF = {"benzin": "Benzin", "diesel": "Diesel", "elektro": "Elektro",
               "hybrid": "Hybrid", "plug-in-hybrid": "Plug-in-Hybrid", "lpg": "Autogas",
               "cng": "Erdgas"}


def _kraftstoff_norm(text: str | None) -> str | None:
    t = (text or "").strip().lower()
    if not t:
        return None
    if "plug" in t:
        return "plug-in-hybrid"
    for k in ("diesel", "elektro", "hybrid", "benzin", "lpg", "cng"):
        if k in t:
            return k
    if "super" in t or "otto" in t:
        return "benzin"
    return t


def baue_zeilen(req, baureihe: dict | None, motor_match: dict | None, *,
                hu=None, laufleistungskontext=None, price_assessment=None,
                markt_verfuegbar: bool = False, fakten=None) -> list[Vergleichszeile]:
    """Die Vergleichszeilen aus strukturierten Daten, jede mit Herkunft."""
    zeilen: list[Vergleichszeile] = []

    # Baujahr gegen den Bauzeitraum der erkannten Generation.
    baujahr = getattr(req, "baujahr", None)
    if baujahr:
        von = (baureihe or {}).get("bauzeitraum_von")
        bis = (baureihe or {}).get("bauzeitraum_bis")
        if von:
            generation = (baureihe or {}).get("generation") or ""
            ref = f"Bauzeitraum {generation} {von} bis {bis or 'heute'}".replace("  ", " ")
            drin = von <= baujahr <= (bis or 9999)
            zeilen.append(Vergleichszeile("Baujahr", str(baujahr), ref, HERKUNFT_DB,
                                          PASST if drin else WEICHT_AB))
        else:
            zeilen.append(Vergleichszeile("Baujahr", str(baujahr), None, HERKUNFT_KEINE,
                                          NICHT_BEWERTBAR))

    # Kilometerstand: die Jahresfahrleistung ist ein BERECHNETER Wert dieses
    # Fahrzeugs. Sie steht deshalb bei der Angabe, nicht als Referenz.
    km = getattr(req, "kilometerstand", None)
    if km:
        angabe = _km(km)
        pro_jahr = getattr(laufleistungskontext, "km_pro_jahr", None)
        if pro_jahr:
            angabe += (f"; berechnet rund {_km(pro_jahr)} pro Jahr seit dem Baujahr")
        zeilen.append(Vergleichszeile("Kilometerstand", angabe, None, HERKUNFT_KEINE,
                                      NICHT_BEWERTBAR))

    # Motor/Leistung gegen die erkannte Variante.
    ps = getattr(req, "leistung_ps", None)
    motor_text = (getattr(req, "motor", None) or "").strip()
    if ps or motor_text:
        angabe = ", ".join(filter(None, [motor_text or None, f"{ps} PS" if ps else None]))
        if motor_match:
            ref = ", ".join(filter(None, [motor_match.get("bezeichnung"),
                                          motor_match.get("motorcode"),
                                          f"{motor_match.get('leistung_ps')} PS"
                                          if motor_match.get("leistung_ps") else None]))
            db_ps = motor_match.get("leistung_ps")
            einordnung = PASST if (not ps or not db_ps or ps == db_ps) else WEICHT_AB
            zeilen.append(Vergleichszeile("Motor/Leistung", angabe, ref, HERKUNFT_DB,
                                          einordnung))
        else:
            zeilen.append(Vergleichszeile("Motor/Leistung", angabe, None, HERKUNFT_KEINE,
                                          NICHT_BEWERTBAR))

    # Kraftstoff.
    kraftstoff = getattr(req, "kraftstoff", None)
    if kraftstoff:
        k_req = _kraftstoff_norm(kraftstoff)
        angabe = _KRAFTSTOFF.get(k_req, kraftstoff)
        if motor_match and motor_match.get("kraftstoff"):
            k_db = _kraftstoff_norm(motor_match.get("kraftstoff"))
            zeilen.append(Vergleichszeile(
                "Kraftstoff", angabe, motor_match.get("kraftstoff"), HERKUNFT_DB,
                PASST if k_req == k_db else WEICHT_AB))
        else:
            zeilen.append(Vergleichszeile("Kraftstoff", angabe, None, HERKUNFT_KEINE,
                                          NICHT_BEWERTBAR))

    # Getriebe gegen die Getriebeoptionen der Variante.
    getriebe = getattr(req, "getriebe", None)
    if getriebe:
        angabe = getriebe_anzeige(getriebe) or getriebe
        optionen = (motor_match or {}).get("getriebe")
        if isinstance(optionen, str):
            optionen_text = optionen.strip("[]").replace('"', "")
        else:
            optionen_text = ", ".join(optionen or [])
        if motor_match and optionen_text:
            db_arten = getriebe_aus_db(motor_match)
            if db_arten is None:
                einordnung = PASST if getriebe in (AUTOMATIK, MANUELL) else NICHT_BEWERTBAR
            else:
                einordnung = PASST if getriebe == db_arten else WEICHT_AB
            zeilen.append(Vergleichszeile("Getriebe", angabe, optionen_text, HERKUNFT_DB,
                                          einordnung))
        else:
            zeilen.append(Vergleichszeile("Getriebe", angabe, None, HERKUNFT_KEINE,
                                          NICHT_BEWERTBAR))

    # Preis: eine Markterwartung gibt es NUR mit belastbarem Median.
    preis = getattr(req, "preis_eur", None)
    if preis:
        pa = price_assessment
        if markt_verfuegbar and pa is not None and getattr(pa, "median_eur", None):
            ref = f"Median {_eur(pa.median_eur)}"
            if pa.lower_bound_eur and pa.upper_bound_eur:
                ref += (f", typischer Bereich {_eur(pa.lower_bound_eur)} bis "
                        f"{_eur(pa.upper_bound_eur)}")
            zeilen.append(Vergleichszeile("Preis", _eur(preis), ref, HERKUNFT_MARKT,
                                          pa.label or NICHT_BEWERTBAR))
        else:
            zeilen.append(Vergleichszeile("Preis", _eur(preis), None, HERKUNFT_KEINE,
                                          NICHT_BEWERTBAR, ohne_referenz=KEINE_MARKTBASIS))

    # Vorbesitzer: ENFAL hat dazu keine Statistik. Prüfbar ist nur der Abgleich
    # mit den Papieren, und der steht in der Checkliste.
    vorbesitzer = getattr(req, "vorbesitzer", None)
    if vorbesitzer is not None:
        zeilen.append(Vergleichszeile("Vorbesitzer", str(vorbesitzer), None, HERKUNFT_KEINE,
                                      NICHT_BEWERTBAR))

    # Letzte Wartung: vergleichbar ist sie nur mit dem Kilometerstand DESSELBEN
    # Inserats (Widerspruchsprüfung), nicht mit einem Intervall.
    wartung = getattr(fakten, "letzte_wartung", None) if fakten is not None else None
    if wartung is not None:
        angabe = f"{wartung.anzeige()} (laut Inserat)"
        if km:
            zeilen.append(Vergleichszeile(
                "Letzte Wartung", angabe, f"Kilometerstand {_km(km)}", HERKUNFT_INSERAT,
                WIDERSPRUCH if getattr(fakten, "wartung_widerspruch", False)
                else "✓ widerspruchsfrei"))
        else:
            zeilen.append(Vergleichszeile("Letzte Wartung", angabe, None, HERKUNFT_KEINE,
                                          NICHT_BEWERTBAR))

    # Servicehistorie: eine Angabe ohne Vergleichsgröße. Das Wartungssystem des
    # Herstellers (z.B. Condition Based Service) ist KEIN Vergleichswert dafür.
    sh = servicehistorie_anzeige(getattr(fakten, "servicehistorie", None)) if fakten else None
    if sh:
        zeilen.append(Vergleichszeile("Servicehistorie", sh, None, HERKUNFT_KEINE,
                                      NICHT_BEWERTBAR))

    # HU gegen die gesetzliche Regel und das heutige Datum.
    if hu is not None:
        status = getattr(hu, "status", None)
        einordnung = {PLAUSIBEL: PASST, ABGELAUFEN: "⚠ abgelaufen",
                      UNGEWOEHNLICH_WEIT: "⚠ ungewöhnlich weit: im Fahrzeugschein prüfen"
                      }.get(status, "⚠ nicht eindeutig lesbar")
        zeilen.append(Vergleichszeile(
            "HU", f"bis {hu.anzeige}", "alle 24 Monate, Erst-HU nach 36 Monaten",
            HERKUNFT_AMTLICH, einordnung))
    return zeilen


def als_markdown(zeilen: list[Vergleichszeile]) -> str:
    if not zeilen:
        return ""
    kopf = ["| Kriterium | Angabe | Referenz (Herkunft) | Einordnung |",
            "|---|---|---|---|"]
    return "\n".join(kopf + [
        f"| {z.kriterium} | {z.angabe} | {z.zelle_referenz()} | {z.einordnung} |"
        for z in zeilen])


# ── Einsetzen in den Bericht ─────────────────────────────────────────────────

_ABSCHNITT = re.compile(r"^##\s*Inserat[^\n]*Vergleich[^\n]*\n", re.IGNORECASE | re.MULTILINE)
_NAECHSTER = re.compile(r"^##\s", re.MULTILINE)
_CHECKLISTE = re.compile(r"^##\s*Besichtigungs", re.IGNORECASE | re.MULTILINE)
_HINWEIS = ("Jeder Wert nennt seine Herkunft. Wo ENFAL keine Vergleichsdaten hat, wird "
            "nicht bewertet.")


def setze_in_bericht(bericht: str, tabelle: str) -> str:
    """Ersetzt den Abschnitt "Inserat im Vergleich" durch die deterministische
    Tabelle. Fehlt der Abschnitt in einem vollständigen Bericht, wird er vor der
    Besichtigungs-Checkliste eingefügt. Ein Kurzbericht (Rückfrage bei fehlenden
    Kerndaten) bleibt unverändert."""
    if not bericht or not tabelle:
        return bericht
    abschnitt = f"## Inserat im Vergleich\n{tabelle}\n\n{_HINWEIS}\n\n"
    m = _ABSCHNITT.search(bericht)
    if m:
        rest = bericht[m.end():]
        n = _NAECHSTER.search(rest)
        ende = m.end() + (n.start() if n else len(rest))
        return bericht[:m.start()] + abschnitt + bericht[ende:].lstrip("\n")
    if "## Kaufempfehlung" not in bericht:
        return bericht
    c = _CHECKLISTE.search(bericht)
    if c:
        return bericht[:c.start()] + abschnitt + bericht[c.start():]
    return bericht.rstrip() + "\n\n" + abschnitt.rstrip() + "\n"
