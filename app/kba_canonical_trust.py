from __future__ import annotations

"""
RC-W6 — Trust-Gate fuer den canonical-only KBA-Lookup (amtliche Rueckrufe
ohne ENFAL-Baureihen-Katalogeintrag).

SICHERHEITSENTSCHEIDUNG (erste Release-Version, verbindlich laut Auftrag):
wir wollen lieber EINEN Recall nicht aus einer unsicheren Fahrzeugidentitaet
ableiten, als einen amtlichen Recall einem falschen Modell zuzuordnen. Eine
unsichere Identitaet fuehrt deshalb NIE zu einem user-sichtbaren Insight —
hoechstens zu RECALL_UNKNOWN.

KEINE NEUE STATE-MACHINE: dieses Modul liest ausschliesslich bereits
vorhandene Felder aus `app.vehicle_identity.VehicleIdentity` (`field_evidence`
mit `verification_state`/`possible_values`/`web_conflict`, siehe dessen
Moduldocstring) bzw. `app.technical_research.WebVehicleIdentity`
(`belegt`/`identitaet_konfidenz`/`belegende_domains`). Es fuegt keinem
dieser Modelle ein neues Feld hinzu.
"""

KBA_LOOKUP_ALLOWED = "KBA_LOOKUP_ALLOWED"
KBA_LOOKUP_UNCERTAIN = "KBA_LOOKUP_UNCERTAIN"
KBA_LOOKUP_DENIED = "KBA_LOOKUP_DENIED"

# Zustaende, die ein Feld fuer den canonical-only Lookup UNBRAUCHBAR machen —
# unabhaengig davon, ob "unknown" (nie bestaetigt) oder "ambiguous"/"conflict"
# (widersprochen). Dieselbe Deutung wie `app.recall_filter._bekannt()`, das
# fuer die bestehende `rueckruf_scope()`-Pruefung exakt dieselben drei
# Zustaende ausschliesst.
_UNSICHERE_ZUSTAENDE = frozenset({"unknown", "ambiguous", "conflict"})

# Zustaende, die ein Feld als UNABHAENGIG BESTAETIGT gelten lassen (nicht nur
# eine blosse, ungeprüfte Nutzerangabe). "plausible"/"web_only" bleiben
# bewusst aussen vor: beide sind laut `VehicleIdentity`-Moduldocstring
# schwaecher als eine echte Bestaetigung.
_BESTAETIGT = frozenset({"user_confirmed", "user_refined", "reference_only"})


def _feld_zustand(identity, feld: str) -> dict:
    return (getattr(identity, "field_evidence", None) or {}).get(feld) or {}


def _unsicher(fe: dict) -> bool:
    return fe.get("verification_state") in _UNSICHERE_ZUSTAENDE or bool(fe.get("web_conflict"))


def _mehrdeutig(fe: dict) -> bool:
    moegliche = fe.get("possible_values")
    return bool(moegliche) and len(moegliche) > 1


def kba_lookup_vertrauen(identity) -> str:
    """Ob `identity` (eine `VehicleIdentity`, nach `apply_web_evidence`) einen
    canonical-only KBA-Lookup ausloesen darf.

    ALLOWED    — Marke UND Modell/Nameplate sind unabhaengig bestaetigt
                 (nicht nur eine rohe Nutzerangabe), keine Mehrdeutigkeit,
                 kein Web-Konflikt, Baujahr vorhanden.
    UNCERTAIN  — Marke/Modell liegen vor und sind nicht widersprochen/
                 mehrdeutig, aber (noch) nicht unabhaengig bestaetigt (z.B.
                 nur `user_only`, reine Nutzerangabe ohne DB-/Web-Beleg).
                 Darf intern/Debug sichtbar sein, erzeugt aber KEINEN
                 user-sichtbaren Recall-Insight (siehe Moduldocstring).
    DENIED     — Marke oder Modell fehlen, sind `unknown`/`ambiguous`/im
                 Web-Konflikt, Modell hat mehrere `possible_values`, oder das
                 Baujahr fehlt. Kein canonical KBA-Lookup.
    """
    make = getattr(identity, "make", None)
    model = getattr(identity, "model", None)
    baujahr = getattr(identity, "year", None)
    if not make or not model or not baujahr:
        return KBA_LOOKUP_DENIED

    make_fe = _feld_zustand(identity, "make")
    model_fe = _feld_zustand(identity, "model")
    if _unsicher(make_fe) or _unsicher(model_fe):
        return KBA_LOOKUP_DENIED
    if _mehrdeutig(model_fe) or _mehrdeutig(make_fe):
        return KBA_LOOKUP_DENIED

    make_bestaetigt = make_fe.get("verification_state") in _BESTAETIGT
    model_bestaetigt = model_fe.get("verification_state") in _BESTAETIGT
    if make_bestaetigt and model_bestaetigt:
        return KBA_LOOKUP_ALLOWED
    return KBA_LOOKUP_UNCERTAIN
