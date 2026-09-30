from __future__ import annotations

"""
EINE zentrale Empfehlungs-Policy: keine Empfehlung ist sicherer als die
Fahrzeugidentität, auf der sie steht (KaufCheck-Final-Stabilization, Cluster J).

BEFUND (Production-Run Mazda MX-5, DB-Miss)
-------------------------------------------
Der Bericht zeigte "Fahrzeugidentität eingeschränkt", Generation, Motorcode
und Antriebsart "nicht sicher bekannt" — und trotzdem "KAUFEN NACH
BESICHTIGUNG".

ROOT CAUSE (im echten Produktionspfad `run_kaufcheck`)
------------------------------------------------------
1. Die Empfehlung wurde fest auf "kaufen_nach_besichtigung" gesetzt, sobald
   Marke, Modell und Baujahr im Request standen.
2. Der vorherige "Identitäts-Floor" prüfte nur zwei Booleans: DB-Treffer
   belastbar ODER `web_recherche.identitaet.belegt`. "belegt" bedeutete
   lediglich: Marke und Modell stehen als Token auf zwei Domains. Für einen
   MX-5 ist das praktisch immer wahr — der Floor griff nie, obwohl Generation
   und Motorisierung unaufgelöst blieben.
3. Die Überschrift ("Fahrzeugidentität eingeschränkt") und die Empfehlung
   wurden aus VERSCHIEDENEN Signalen berechnet und konnten sich widersprechen.

REGEL
-----
Die Identitätsstufe wird aus der KANONISCHEN Identität berechnet (dieselbe,
die der Bericht anzeigt):

  verified      Marke+Modell unabhängig bestätigt (DB oder Web) UND Generation
                bekannt UND Motorisierung belegt (DB-Variante oder
                Web-bestätigte Leistung).
  partial       Marke+Modell bestätigt, Generation oder Motorisierung offen.
  insufficient  Marke+Modell nicht unabhängig bestätigt.

Nur `verified` erlaubt eine normale Empfehlung. Sonst:
  recommendation_state = LIMITED_ANALYSIS bzw. INSUFFICIENT_IDENTITY,
  Empfehlung "unbekannt" ("keine Empfehlung möglich"). Eine VORSICHTIGERE
  Stufe (Werkstattprüfung, hohes Risiko), die der Floor aus belegten Risiken
  gesetzt hat, bleibt erhalten — Vorsicht ist nie "zu selbstsicher".
Wird die Identität später über Webquellen ausreichend bestätigt, ist die
normale Empfehlung wieder erlaubt — ohne Sonderpfad.
"""

from dataclasses import dataclass, field

VERIFIED = "verified"
PARTIAL = "partial"
INSUFFICIENT = "insufficient"

STATE_NORMAL = "NORMAL"
STATE_LIMITED = "LIMITED_ANALYSIS"
STATE_INSUFFICIENT = "INSUFFICIENT_IDENTITY"

# Doppelpunkt statt Gedankenstrich: die zentrale Schreibstil-Regel
# (app/schreibstil.py) gilt auch für diese Anzeige.
ANZEIGE_LIMITED = "Analyse eingeschränkt: Fahrzeugvariante nicht vollständig verifiziert"
ANZEIGE_INSUFFICIENT = "Analyse eingeschränkt: Fahrzeugidentität nicht bestätigt"

# Empfehlungen, die eine Freigabe ausdrücken. Nur diese werden bei
# unvollständiger Identität zurückgenommen.
_MILDE = {"kaufen", "kaufen_nach_besichtigung", "preis_nachverhandeln"}

_ANZEIGE_NORMAL = {
    "kaufen": "Kaufen", "kaufen_nach_besichtigung": "Kaufen nach Besichtigung",
    "nur_mit_werkstattpruefung": "Nur mit Werkstattprüfung",
    "preis_nachverhandeln": "Preis nachverhandeln", "hohes_risiko": "Hohes Risiko",
    "finger_weg": "Finger weg", "unbekannt": "Keine Empfehlung möglich",
}

_BESTAETIGT = ("user_confirmed", "user_refined", "reference_only", "plausible", "web_only")


def _bestaetigt(fe: dict | None) -> bool:
    fe = fe or {}
    if fe.get("verification_state") in _BESTAETIGT:
        return True
    return bool(set(fe.get("confirmed_by") or []) & {"enfal", "web"})


@dataclass(frozen=True)
class IdentitaetsAufloesung:
    stufe: str
    fehlend: tuple[str, ...] = ()
    quellen: tuple[str, ...] = field(default_factory=tuple)


def identitaet_aufloesen(identity) -> IdentitaetsAufloesung:
    fe = identity.field_evidence if identity is not None else {}
    quellen = sorted({q for e in fe.values() for q in ([e.get("primary_source")]
                      + list(e.get("confirmed_by") or [])) if q in ("enfal", "web")})
    if not (_bestaetigt(fe.get("make")) and _bestaetigt(fe.get("model"))
            and identity.make and identity.model):
        return IdentitaetsAufloesung(INSUFFICIENT, ("Marke/Modell",), tuple(quellen))
    fehlend: list[str] = []
    if not (identity.generation and _bestaetigt(fe.get("generation"))):
        fehlend.append("Generation")
    motor_belegt = (
        (identity.engine_name and fe.get("engine_name", {}).get("verification_state")
         in ("user_refined", "reference_only", "user_confirmed", "web_only")
         and "enfal" in ((fe.get("engine_name") or {}).get("provenance") or []))
        or (identity.horsepower and _bestaetigt(fe.get("horsepower"))
            and fe.get("horsepower", {}).get("verification_state") != "user_only")
    )
    if not motor_belegt:
        fehlend.append("Motorisierung")
    return IdentitaetsAufloesung(VERIFIED if not fehlend else PARTIAL, tuple(fehlend),
                                 tuple(quellen))


@dataclass(frozen=True)
class Empfehlungsentscheidung:
    empfehlung: str
    state: str
    anzeige: str
    hinweis: str | None
    identitaet: IdentitaetsAufloesung


def entscheide(kandidat: str | None, identity) -> Empfehlungsentscheidung:
    """Die finale Empfehlung — die EINZIGE Stelle, die über den Zustand
    entscheidet. Bericht, API-Feld und Frontend lesen dieses Ergebnis."""
    aufl = identitaet_aufloesen(identity)
    kandidat = (kandidat or "unbekannt").strip().lower()
    if aufl.stufe == VERIFIED:
        return Empfehlungsentscheidung(kandidat, STATE_NORMAL,
                                       _ANZEIGE_NORMAL.get(kandidat, kandidat), None, aufl)
    state = STATE_LIMITED if aufl.stufe == PARTIAL else STATE_INSUFFICIENT
    anzeige_basis = ANZEIGE_LIMITED if aufl.stufe == PARTIAL else ANZEIGE_INSUFFICIENT
    offen = ", ".join(aufl.fehlend)
    hinweis = (f"Nicht sicher belegt: {offen}. Fahrzeugspezifische Aussagen sind "
               f"deshalb eingeschränkt; eine Kaufempfehlung ist erst nach Klärung "
               f"(Fahrzeugschein, FIN) möglich.")
    if kandidat in _MILDE or kandidat == "unbekannt":
        return Empfehlungsentscheidung("unbekannt", state, anzeige_basis, hinweis, aufl)
    # Vorsichtigere Stufe bleibt, der Zustand bleibt trotzdem sichtbar begrenzt.
    return Empfehlungsentscheidung(kandidat, state,
                                   f"{_ANZEIGE_NORMAL.get(kandidat, kandidat)} ({anzeige_basis})",
                                   hinweis, aufl)
