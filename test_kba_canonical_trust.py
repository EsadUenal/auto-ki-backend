"""
RC-W6 — Trust-Gate fuer den canonical-only KBA-Lookup
(app.kba_canonical_trust.kba_lookup_vertrauen).

Sicherheitsentscheidung (verbindlich fuer die erste Release-Version): lieber
KEINEN Recall aus einer unsicheren Identitaet ableiten als einen amtlichen
Recall einem falschen Modell zuordnen. Reine Feldlektuere aus dem
bestehenden `VehicleIdentity.field_evidence`-Modell, keine neue
State-Machine.

KEIN Netzwerk, KEIN LLM-Call, KEINE DB-Mutation.

    python test_kba_canonical_trust.py
"""
from app.kba_canonical_trust import (
    KBA_LOOKUP_ALLOWED, KBA_LOOKUP_DENIED, KBA_LOOKUP_UNCERTAIN, kba_lookup_vertrauen,
)
from app.vehicle_identity import VehicleIdentity

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def ident(make_state="user_confirmed", model_state="user_confirmed", *,
          make="Mazda", model="MX-5", year=2019,
          make_possible=None, model_possible=None,
          make_conflict=None, model_conflict=None) -> VehicleIdentity:
    fe = {}
    if make is not None:
        fe["make"] = {"value": make, "verification_state": make_state}
        if make_possible:
            fe["make"]["possible_values"] = make_possible
        if make_conflict:
            fe["make"]["web_conflict"] = make_conflict
    if model is not None:
        fe["model"] = {"value": model, "verification_state": model_state}
        if model_possible:
            fe["model"]["possible_values"] = model_possible
        if model_conflict:
            fe["model"]["web_conflict"] = model_conflict
    return VehicleIdentity(make=make, model=model, year=year, field_evidence=fe)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== A) ALLOWED: Marke und Modell unabhaengig bestaetigt, Baujahr da ===")
_a = ident("user_confirmed", "user_confirmed")
check("A1", kba_lookup_vertrauen(_a) == KBA_LOOKUP_ALLOWED)

_a2 = ident("reference_only", "user_refined")
check("A2 andere bestaetigte Zustaende zaehlen ebenfalls als ALLOWED",
      kba_lookup_vertrauen(_a2) == KBA_LOOKUP_ALLOWED)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== B) UNCERTAIN: vorhanden, nicht widersprochen, aber nicht bestaetigt ===")
_b = ident("user_only", "user_only")
check("B1 reine Nutzerangabe ohne unabhaengige Bestaetigung -> UNCERTAIN",
      kba_lookup_vertrauen(_b) == KBA_LOOKUP_UNCERTAIN)

_b2 = ident("user_confirmed", "user_only")
check("B2 nur EINES der beiden Felder bestaetigt -> UNCERTAIN (nicht ALLOWED)",
      kba_lookup_vertrauen(_b2) == KBA_LOOKUP_UNCERTAIN)

_b3 = ident("plausible", "web_only")
check("B3 'plausible'/'web_only' sind vorhanden, aber nicht 'bestaetigt' -> UNCERTAIN",
      kba_lookup_vertrauen(_b3) == KBA_LOOKUP_UNCERTAIN)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== C) DENIED: make conflict ===")
_c = ident("user_confirmed", "user_confirmed", make_conflict="Honda")
check("C1 web_conflict auf 'make' -> DENIED", kba_lookup_vertrauen(_c) == KBA_LOOKUP_DENIED)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== D) DENIED: model ambiguous (mehrere possible_values) ===")
_d = ident("user_confirmed", "ambiguous", model_possible=["MX-5", "RX-8"])
check("D1 'model' verification_state='ambiguous' -> DENIED",
      kba_lookup_vertrauen(_d) == KBA_LOOKUP_DENIED)

_d2 = ident("user_confirmed", "user_confirmed", model_possible=["MX-5", "RX-8"])
check("D2 mehrere possible_values auch ohne 'ambiguous'-State -> DENIED",
      kba_lookup_vertrauen(_d2) == KBA_LOOKUP_DENIED)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== E) DENIED: fehlendes Jahr ===")
_e = ident("user_confirmed", "user_confirmed", year=None)
check("E1 kein Baujahr -> DENIED", kba_lookup_vertrauen(_e) == KBA_LOOKUP_DENIED)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== F) DENIED: make/model unknown oder fehlend ===")
_f1 = ident("unknown", "user_confirmed")
check("F1 make 'unknown' -> DENIED", kba_lookup_vertrauen(_f1) == KBA_LOOKUP_DENIED)

_f2 = ident("user_confirmed", "user_confirmed", model=None)
check("F2 kein Modell -> DENIED", kba_lookup_vertrauen(_f2) == KBA_LOOKUP_DENIED)

_f3 = ident("user_confirmed", "conflict")
check("F3 model 'conflict' -> DENIED", kba_lookup_vertrauen(_f3) == KBA_LOOKUP_DENIED)


# ══════════════════════════════════════════════════════════════════════════
print("\n=== G) Echte, gute User+Web-Identitaet (voller Pfad wie im Mazda-Sentinel) ===")
# Reproduziert exakt den Zustand nach app.vehicle_identity.VehicleIdentity.
# from_check_context() + apply_web_evidence() im echten Mazda-MX-5-Shadow-Lauf
# (Forensik-Audit, Paket A): make/model werden durch >=2 unabhaengige
# Web-Domains bestaetigt (user_confirmed), Baujahr ist eine reine, unbestaetigte
# Nutzerangabe (user_only) -- und das reicht fuer `year`, das nur VORHANDEN
# sein muss, nicht unabhaengig bestaetigt (siehe Moduldocstring).
import asyncio

from app.technical_research import FixtureTechnicalResearchProvider, recherchiere_technisch
from app.car_lookup import find_baureihe_mit_vertrauen, find_motor
from app.models import KaufCheckRequest


def _treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


_fixtures_mazda = {
    "identitaet": [
        _treffer("https://www.adac.de/mazda-mx-5-test", "Mazda MX-5 im Test",
                "Der Mazda MX-5 ist ein Roadster mit 2.0 SKYACTIV-G Benzinmotor."),
        _treffer("https://www.autobild.de/mazda-mx-5", "Mazda MX-5 Gebrauchtwagen",
                "Mazda MX-5: Roadster, 2.0 SKYACTIV-G, Benzin, Hinterradantrieb."),
    ],
    "rueckruf": [], "schwachstelle": [], "wartung": [],
}


async def _baue_echte_mazda_identity():
    req = KaufCheckRequest(marke="Mazda", modell="MX-5", baujahr=2019,
                           motor="2.0 SKYACTIV-G", kraftstoff="Benzin",
                           leistung_ps=184, kilometerstand=58_700)
    br_markt, info = find_baureihe_mit_vertrauen(req.marke, req.modell, req.baujahr)
    mo_markt = find_motor(br_markt, req.motor, req.modell, req=req) if br_markt else None
    br, mo = (br_markt, mo_markt) if info["belastbar"] else (None, None)
    provider = FixtureTechnicalResearchProvider(_fixtures_mazda)
    web = await recherchiere_technisch(req, br_markt, info, br, mo, provider=provider)
    identity = VehicleIdentity.from_check_context(br, mo, req)
    identity.apply_web_evidence(web)
    return identity


_g = asyncio.run(_baue_echte_mazda_identity())
check("G1 DB-Miss (Mazda nicht katalogisiert) -> br=None, trotzdem Web-Identitaet",
      _g.make == "Mazda" and _g.model == "MX-5")
check("G2 make/model durch Web unabhaengig bestaetigt (user_confirmed)",
      _g.field_evidence.get("make", {}).get("verification_state") == "user_confirmed"
      and _g.field_evidence.get("model", {}).get("verification_state") == "user_confirmed")
check("G3 echte Mazda-Sentinel-Identitaet -> KBA_LOOKUP_ALLOWED",
      kba_lookup_vertrauen(_g) == KBA_LOOKUP_ALLOWED)


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE KBA-CANONICAL-TRUST-TESTS GRUEN")
