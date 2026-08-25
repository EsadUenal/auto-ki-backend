"""
Vertrauensstufen für Fakten der Fahrzeug-Datenbank.

Warum es das gibt
-----------------
Große Teile der Fahrzeug-DB wurden ursprünglich generativ erzeugt und nie einzeln
fachlich geprüft. Der DB-Trust-Audit hat belegt, dass darin reale Fehler stecken
(`bmw-8er-e63-e64` führt die Codes und Motoren der 6er-Reihe) — und gleichzeitig,
dass die Marktanalyse aus genau diesen Daten **harte** Ablehnungen ableitet: in
einem einzigen BMW-Lauf 163 Ablehnungen „anderes Modell", 123 „andere Generation"
und 15 „andere Motorvariante".

Ein falscher Datensatz kann damit ein korrektes Inserat aus dem Median werfen.
Deshalb gilt für den Marktvergleich: ungeprüfte DB-Daten dürfen unterstützen, aber
nicht als harte Wahrheit entscheiden.

Was hier NICHT passiert
-----------------------
Es wird nichts gelöscht und nichts umgeschrieben. Die DB bleibt vollständig
nutzbar für Chat, Kaufcheck, Suchunterstützung und Kandidatengenerierung. Diese
Datei liefert nur die Antwort auf die Frage: „Darf dieser Fakt eine harte
Marktentscheidung tragen?"

Statusstufen
------------
``unverified``  importiert/generiert, ungeprüft — der Default für ALLES, was keine
                ausdrückliche Angabe trägt (fehlende Spalte, NULL, kaputtes JSON).
``reviewed``    im Projekt angesehen und plausibilisiert, aber ohne gespeicherten
                externen Nachweis. Reicht ausdrücklich NICHT für harte Wirkung.
``verified``    fachlich geprüft UND mit gespeicherter Quelle. Nur diese Stufe darf
                hart ablehnen oder positiv inferieren.
``rejected``    als falsch erkannt. Zählt nie als Wissen.

`verified` ohne nichtleere `source` wird bewusst zu `reviewed` herabgestuft — sonst
könnten wir uns den Nachweis später selbst vormachen.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

UNVERIFIED = "unverified"
REVIEWED = "reviewed"
VERIFIED = "verified"
REJECTED = "rejected"

_ERLAUBT = {UNVERIFIED, REVIEWED, VERIFIED, REJECTED}

# Fachliche Fakten, die im Marktvergleich hart wirken können. Kraftstoff, Leistung,
# Getriebe und Antrieb hängen alle an derselben `motorvariante`-Zeile und werden
# deshalb gemeinsam unter "motorvarianten" geführt — sie stammen aus einer Quelle
# und wurden nie getrennt geprüft; eine feinere Aufteilung wäre Scheingenauigkeit.
#
# DATA-SAFETY-RUNTIME-GATE: additiv um die drei Faktenarten erweitert, aus denen
# der Kaufcheck seine fahrzeugspezifischen Aussagen bildet. Sie stehen HIER und
# nicht in einer zweiten Trust-Registry, damit es genau EINE Stelle gibt, an der
# "darf dieser Fakt hart wirken?" beantwortet wird. Der Kaufcheck liest sie über
# `app/evidence.py::_trust_der_baureihe`.
#
#   schwachstellen   -> schwachstelle_baureihe
#   motorprobleme    -> schwachstelle_motor   (hängt an motorvariante)
#   rueckrufe        -> rueckruf
#   wartung          -> kritische_wartung     (hängt an motorvariante)
#
# Stand heute trägt KEINE der 421 Baureihen einen `verification`-Eintrag; alle vier
# stehen damit faktisch auf `unverified`. Das ist Absicht: die Stufe wird nicht
# behauptet, sondern muss eingetragen werden.
FAKTEN = ("generation", "chassis_codes", "karosserie", "motorvarianten", "facelift",
          "schwachstellen", "motorprobleme", "rueckrufe", "wartung")


def _als_dict(baureihe) -> dict:
    """Verifikationsobjekt einer Baureihe — fehlertolerant.

    Ein defekter oder unerwarteter Eintrag darf den Marktvergleich niemals
    abbrechen; im Zweifel gilt alles als ungeprüft.
    """
    if not isinstance(baureihe, dict):
        return {}
    roh = baureihe.get("verification")
    if isinstance(roh, dict):
        return roh
    if isinstance(roh, str) and roh.strip():
        try:
            geladen = json.loads(roh)
        except (ValueError, TypeError):
            log.warning("verification-JSON unlesbar bei Baureihe %r",
                        baureihe.get("id"))
            return {}
        if isinstance(geladen, dict):
            return geladen
    return {}


def _eintrag(baureihe, fakt: str) -> dict:
    wert = _als_dict(baureihe).get(fakt)
    if isinstance(wert, dict):
        return wert
    if isinstance(wert, str):          # Kurzform {"generation": "verified"}
        return {"status": wert}
    return {}


def verification_source(baureihe, fakt: str) -> str | None:
    quelle = _eintrag(baureihe, fakt).get("source")
    return quelle.strip() if isinstance(quelle, str) and quelle.strip() else None


def verification_date(baureihe, fakt: str) -> str | None:
    datum = _eintrag(baureihe, fakt).get("date")
    return datum.strip() if isinstance(datum, str) and datum.strip() else None


def verification_status(baureihe, fakt: str) -> str:
    """Vertrauensstufe eines Fakts. Fehlt etwas oder ist es defekt: ``unverified``."""
    status = _eintrag(baureihe, fakt).get("status")
    if not isinstance(status, str):
        return UNVERIFIED
    status = status.strip().lower()
    if status not in _ERLAUBT:
        return UNVERIFIED
    # §4: "verified" ohne gespeicherten Nachweis ist keine Verifikation.
    if status == VERIFIED and not verification_source(baureihe, fakt):
        return REVIEWED
    return status


def is_verified(baureihe, fakt: str) -> bool:
    """Darf dieser Fakt eine HARTE Marktentscheidung tragen?"""
    return verification_status(baureihe, fakt) == VERIFIED


def is_rejected(baureihe, fakt: str) -> bool:
    return verification_status(baureihe, fakt) == REJECTED


def darf_als_wissen_gelten(baureihe, fakt: str) -> bool:
    """Darf der Fakt überhaupt als (weiche) Information verwendet werden?

    Alles außer ``rejected`` — ungeprüfte Daten bleiben für Similarity, Suche und
    Diagnose nutzbar, sie dürfen nur nicht hart entscheiden.
    """
    return not is_rejected(baureihe, fakt)
