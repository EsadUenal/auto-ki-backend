"""
Etappe 3 §16 — EINMALIGER Sandbox-Integrationstest.

Prueft die Provider-GRENZE, NICHT die Marktqualitaet:
  Sandbox -> MobileDeProvider -> list[Preisbeobachtung] -> marktvergleich._bewerte

Das Ergebnis ist AUSDRUECKLICH KEINE Marktanalyse. Die Sandbox enthaelt
synthetische und teils widerspruechliche Datensaetze (bekannt: make/model
"CITROEN/C3" bei modelDescription "C5 Aircross Shine"). Es werden hier deshalb
weder Medianwerte noch Trefferquoten als Produktaussage berechnet.

Keine Secrets im Output.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.marktvergleich import _bewerte, _dedupliziere, baue_ziel  # noqa: E402
from app.mobile_de_provider import MobileDeProvider  # noqa: E402
from app.vehicle_identity import VehicleIdentity  # noqa: E402
from app.web_search import marktquellen_freigabe  # noqa: E402


class Req:
    """Minimaler CheckRequest-Ersatz fuer baue_ziel (nur die gelesenen Attribute)."""
    def __init__(self, **kw):
        self.marke = kw.get("marke")
        self.modell = kw.get("modell")
        self.baujahr = kw.get("baujahr")
        self.kilometerstand = kw.get("kilometerstand")
        self.motor = kw.get("motor")
        self.kraftstoff = kw.get("kraftstoff")
        self.leistung_ps = kw.get("leistung_ps")
        self.getriebe = kw.get("getriebe")


async def main() -> int:
    identity = VehicleIdentity(make="Volkswagen", model="Golf", year=2013,
                               mileage=60000, fuel="Benzin")
    req = Req(marke="Volkswagen", modell="Golf", baujahr=2013,
              kilometerstand=60000, kraftstoff="Benzin")

    provider = MobileDeProvider()
    print(f"provider={provider.name} konfiguriert={provider.konfiguriert}")

    beobachtungen, fehler = await provider.find_comparables(identity, limit=8)
    print(f"[1] Provider: {len(beobachtungen)} Beobachtungen, technischer_fehler={fehler}")
    if not beobachtungen:
        print("    -> keine Beobachtungen, Rest uebersprungen")
        return 1

    b0 = beobachtungen[0]
    print(f"[2] Herkunftsfelder: extraction_source={b0.extraction_source!r} "
          f"segmentation_method={b0.segmentation_method!r} "
          f"confidence={b0.structural_confidence!r} "
          f"window_fallback_used={b0.window_fallback_used}")
    print(f"[3] Unbewertet uebergeben: vergleichbarkeit={b0.vergleichbarkeit!r} "
          f"make={b0.make!r} model={b0.model!r} engine_variant={b0.engine_variant!r} "
          f"generation={b0.generation!r}/{b0.generation_evidence!r}")

    ziel = baue_ziel(None, None, req, [], [])

    # ── A) OHNE Freigabe: Source-Policy muss zuschlagen ──────────────────────
    ohne = [_bewerte(b.model_copy(deep=True), ziel) for b in beobachtungen]
    policy_abgelehnt = sum(1 for b in ohne
                           if "nicht freigegeben" in " ".join(b.gruende).lower())
    print(f"[4] OHNE Freigabe: {policy_abgelehnt}/{len(ohne)} per Source-Policy verworfen "
          f"(erwartet: alle — mobile.de ist nicht freigegeben)")

    # ── B) MIT Testfreigabe: greift die fachliche Etappe-1-Logik ueberhaupt? ──
    with marktquellen_freigabe({"mobile.de"}):
        mit = [_bewerte(b.model_copy(deep=True), ziel) for b in beobachtungen]

    stufen: dict[str, int] = {}
    for b in mit:
        stufen[b.vergleichbarkeit] = stufen.get(b.vergleichbarkeit, 0) + 1
    print(f"[5] MIT Testfreigabe: Stufenverteilung {stufen}")
    print("    (KEINE Marktaussage — nur der Nachweis, dass _bewerte laeuft)")

    for b in mit[:4]:
        grund = b.gruende[0][:90] if b.gruende else ""
        print(f"    - id={b.listing_id} {b.preis_eur}EUR {b.baujahr} {b.kilometerstand}km "
              f"fuel={b.fuel} ps={b.horsepower} body={b.body} -> {b.vergleichbarkeit} | {grund}")

    # ── C) Dedupe auf API-Beobachtungen ─────────────────────────────────────
    with marktquellen_freigabe({"mobile.de"}):
        doppelt = [_bewerte(b.model_copy(deep=True), ziel)
                   for b in beobachtungen + beobachtungen]
    uniq, konflikte = _dedupliziere(doppelt)
    print(f"[6] Dedupe: {len(doppelt)} -> {len(uniq)} "
          f"(erwartet {len(beobachtungen)}), konflikte={len(konflikte)}")

    # ── D) Invarianten ──────────────────────────────────────────────────────
    ok = True
    if policy_abgelehnt != len(ohne):
        print("    FAIL: Source-Policy hat nicht alle verworfen"); ok = False
    if len(uniq) != len(beobachtungen):
        print("    FAIL: Dedupe hat nicht auf die Ausgangsmenge reduziert"); ok = False
    if any(b.window_fallback_used for b in beobachtungen):
        print("    FAIL: API-Beobachtung als window_fallback markiert"); ok = False
    if any(b.extraction_source != "api" for b in beobachtungen):
        print("    FAIL: falsche extraction_source"); ok = False
    if any(b.generation is not None for b in beobachtungen):
        print("    FAIL: Provider hat eine Generation gesetzt"); ok = False
    if any(b.engine_variant is not None for b in beobachtungen):
        print("    FAIL: Provider hat engine_variant vorbelegt"); ok = False

    print("[7] Invarianten:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
