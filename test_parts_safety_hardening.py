"""Parts Safety Hardening Runde 1 — deterministisch, ohne Netzwerk.

Die Tests laufen gegen den produktiven Matcher und den produktiven Router. Nur die
externen Tavily-/Gemini-Antworten sowie der DB-Resolver werden an der Systemgrenze
kontrolliert ersetzt.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(
    tempfile.gettempdir(), f"vira_parts_safety_{os.getpid()}.db"
)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from app.ersatzteil_kompat import klassifiziere, parse_bauteil, parse_fahrzeug  # noqa: E402
import app.routers.ersatzteile as et  # noqa: E402


_fails: list[str] = []


def check(name: str, condition: bool) -> None:
    print(("[OK] " if condition else "[FAIL] ") + name)
    if not condition:
        _fails.append(name)


def klass(fahrzeug: str, bauteil: str, produkt: str) -> str:
    return klassifiziere(parse_fahrzeug(fahrzeug), parse_bauteil(bauteil), produkt)[0]


# 1–5: bestätigte False Positives und konservativer Default.
check(
    "1 Golf VII 2.0 TDI vs 1.2-TSI-Bremse -> nicht confirmed",
    klass(
        "VW Golf VII 2016 2.0 TDI 150 PS",
        "Bremsscheiben vorne",
        "Bremsscheiben VW Golf VII nur 1.2 TSI Vorderachse 276 mm",
    ) != "confirmed",
)
check(
    "2 DSG vs Schaltgetriebe-Kupplung -> nicht confirmed",
    klass(
        "VW Golf VII 2016 2.0 TDI DSG",
        "Kupplung",
        "Kupplungssatz VW Golf VII 2.0 TDI nur Schaltgetriebe",
    ) != "confirmed",
)
check(
    "3 Golf VII vs Golf VI -> nicht confirmed",
    klass("Volkswagen Golf VII 2016", "ABS-Sensor", "ABS-Sensor Volkswagen Golf VI")
    != "confirmed",
)
check(
    "4 gleiche Baureihe ohne Parts-Fitmentnachweis -> uncertain",
    klass("BMW 320i E92", "Ölfilter", "Ölfilter BMW 320i E92") == "uncertain",
)
check(
    "5 unsichere Fahrzeugidentität -> uncertain",
    klass("BMW", "Ölfilter", "Ölfilter BMW") == "uncertain",
)

# 6–10: harte Widersprüche bleiben hart; Safety-Kategorien bleiben konservativ.
check(
    "6 Herstellerkonflikt -> rejected",
    klass("BMW 320i E92", "Ölfilter", "Ölfilter Audi A4 B8") == "rejected",
)
check(
    "7 Achskonflikt -> rejected",
    klass("BMW 320i E92", "Bremsscheiben vorne", "Bremsscheiben BMW E92 Hinterachse")
    == "rejected",
)
check(
    "8 Performance-Konflikt -> rejected",
    klass("BMW M3 E92", "Bremsscheiben vorne", "Bremsscheiben BMW M4 F82 Vorderachse")
    == "rejected",
)
check(
    "9 Bremse ohne autoritative Fitment-Evidenz -> uncertain",
    klass("BMW 320i E92", "Bremsscheiben vorne", "Bremsscheiben BMW 320i E92 Vorderachse")
    == "uncertain",
)
check(
    "10 Lenkung ohne autoritative Fitment-Evidenz -> uncertain",
    klass("BMW 320i E92", "Lenkgetriebe", "Lenkgetriebe BMW 320i E92") == "uncertain",
)


async def _router_response(result: dict) -> dict:
    old_search = et._mehrstufige_suche
    old_llm = et.call_gemini_json
    old_identity = getattr(et, "_parts_identity_context", None)

    async def fake_search(*_args, **_kwargs):
        return [{
            "title": "Kontrollierte Fixture",
            "url": "https://example.invalid/fixture",
            "content": "Bremsscheiben-Angebot, 199 EUR",
        }]

    async def fake_llm(*_args, **_kwargs):
        return result

    et._mehrstufige_suche = fake_search
    et.call_gemini_json = fake_llm
    if old_identity is not None:
        et._parts_identity_context = lambda _text: {
            "baureihe_belastbar": True,
            "motor_belastbar": True,
            "match_art": "exact",
        }
    try:
        return await et.ersatzteil_suche(
            et.SucheBody(fahrzeug="BMW M3 E92", bauteil="Bremsscheiben vorne"), 1
        )
    finally:
        et._mehrstufige_suche = old_search
        et.call_gemini_json = old_llm
        if old_identity is not None:
            et._parts_identity_context = old_identity


# 11: Vorfilter-LLM-Prosa darf keinen später verworfenen Treffer zurückbringen.
leak_result = {
    "ergebnisse": [
        {
            "teilename": "Bremsscheiben BMW E92 320d Vorderachse",
            "anbieter": "Shop A",
            "preis_eur": 49,
            "marke_typ": "nachbau",
            "qualitaetsstufe": "Budget",
            "url": "https://example.invalid/320d",
            "passt_fahrzeug": "BMW E92 320d",
            "hinweis": "",
        },
        {
            "teilename": "Bremsscheiben BMW M3 E92 Vorderachse 360mm",
            "anbieter": "Shop B",
            "preis_eur": 210,
            "marke_typ": "unbekannt",
            "qualitaetsstufe": "Typ nicht verifiziert",
            "url": "https://example.invalid/m3",
            "passt_fahrzeug": "BMW M3 E92",
            "hinweis": "",
        },
    ],
    "empfehlung": "Das günstige 320d-Teil von Shop A ist die beste Wahl.",
    "empfohlener_index": 0,
}
leak_response = asyncio.run(_router_response(leak_result))
check(
    "11 verworfener Kandidat leakt nicht in finale Empfehlung",
    "320d-Teil" not in leak_response["empfehlung"]
    and all("320d" not in e["teilename"] for e in leak_response["ergebnisse"]),
)

# 12: LLM-Herkunftsbehauptung ohne autoritative Evidenz muss neutralisiert werden.
oem_result = {
    "ergebnisse": [{
        "teilename": "Original OEM Bremsscheiben BMW M3 E92 Vorderachse 360mm",
        "anbieter": "Shop X",
        "preis_eur": 199,
        "marke_typ": "oem",
        "qualitaetsstufe": "Original",
        "url": "https://example.invalid/plain",
        "passt_fahrzeug": "BMW M3 E92",
        "hinweis": "Originalteil laut Angebot",
    }],
    "empfehlung": "Das Original-OEM-Teil ist die beste Wahl.",
    "empfohlener_index": 0,
}
oem_response = asyncio.run(_router_response(oem_result))
oem_entry = oem_response["ergebnisse"][0]
check(
    "12 unbelegter OEM-/Original-Claim wird neutralisiert",
    oem_entry.get("marke_typ") == "unbekannt"
    and oem_entry.get("qualitaetsstufe") == "Typ nicht verifiziert"
    and "original" not in oem_entry.get("teilename", "").lower()
    and "oem" not in oem_entry.get("teilename", "").lower()
    and "original" not in oem_entry.get("hinweis", "").lower()
    and "Original-OEM" not in oem_response["empfehlung"],
)

# 13: Nicht-sicherheitskritisch ist ohne Fitmentbeweis UNKNOWN, nicht inkompatibel.
check(
    "13 neutraler Kandidat ohne Fitmentbeweis -> uncertain",
    klass("BMW 320i E92", "Ölfilter", "MANN Ölfilter BMW 320i E92") == "uncertain",
)

# 14–15: Der produktive Router benutzt die bestehenden VIRA-Resolver als Gate.
old_baureihe = et.find_baureihe_mit_vertrauen
old_motor = et.find_motor
resolver_calls: list[tuple] = []
motor_calls: list[tuple] = []


def fake_baureihe(marke, modell, baujahr):
    resolver_calls.append((marke, modell, baujahr))
    return ({"id": "volkswagen-golf-vii", "motoren": [{"bezeichnung": "2.0 TDI"}]},
            {"match_art": "generation_match", "belastbar": True})


def fake_motor(baureihe, hint):
    motor_calls.append((baureihe["id"], hint))
    return {"bezeichnung": "2.0 TDI"}


try:
    et.find_baureihe_mit_vertrauen = fake_baureihe
    et.find_motor = fake_motor
    gate = et._parts_identity_context("VW Golf VII 2016 2.0 TDI 150 PS")
finally:
    et.find_baureihe_mit_vertrauen = old_baureihe
    et.find_motor = old_motor

check(
    "14 bestehender Baureihen- und Motorresolver ist verpflichtendes Parts-Gate",
    gate["baureihe_belastbar"] is True
    and gate["motor_belastbar"] is True
    and len(resolver_calls) == 1
    and len(motor_calls) == 1,
)


def fake_unsicher(_marke, _modell, _baujahr):
    return ({"id": "unsicher", "motoren": []},
            {"match_art": "marke_only", "belastbar": False})


try:
    et.find_baureihe_mit_vertrauen = fake_unsicher
    et.find_motor = lambda *_args: (_ for _ in ()).throw(
        AssertionError("find_motor darf bei unsicherer Baureihe nicht laufen")
    )
    unsicheres_gate = et._parts_identity_context("BMW")
finally:
    et.find_baureihe_mit_vertrauen = old_baureihe
    et.find_motor = old_motor

check(
    "15 unsichere Baureihe sperrt Motorauflösung und positive Identity",
    unsicheres_gate["baureihe_belastbar"] is False
    and unsicheres_gate["motor_belastbar"] is False,
)

check(
    "16 Spannungsangabe 12 V ist kein harter Golf-V-Generationskonflikt",
    klass("VW Golf VII 2016", "Sensor", "Sensor VW Golf 12 V") == "uncertain",
)

gate_ergebnisse, gate_index = et._bewerte_kompatibilitaet(
    "BMW",
    "Ölfilter",
    [{"teilename": "Ölfilter BMW", "passt_fahrzeug": "BMW", "hinweis": ""}],
    {"baureihe_belastbar": False, "motor_genannt": False, "motor_belastbar": False},
)
check(
    "17 unsicheres Identity-Gate erzwingt transparenten UNKNOWN-Grund",
    gate_index is None
    and gate_ergebnisse[0]["kompatibilitaet"] == "uncertain"
    and gate_ergebnisse[0]["kompat_grund"] == "Fahrzeugidentität nicht belastbar bestätigt",
)

motor_ergebnisse, motor_index = et._bewerte_kompatibilitaet(
    "VW Golf VII 2016 2.0 TDI",
    "Ölfilter",
    [{"teilename": "Ölfilter VW Golf VII", "passt_fahrzeug": "VW Golf VII", "hinweis": ""}],
    {"baureihe_belastbar": True, "motor_genannt": True, "motor_belastbar": False},
)
check(
    "18 unaufgelöster Motor erzwingt transparenten UNKNOWN-Grund",
    motor_index is None
    and motor_ergebnisse[0]["kompatibilitaet"] == "uncertain"
    and motor_ergebnisse[0]["kompat_grund"] == "Motorisierung nicht belastbar bestätigt",
)


print()
if _fails:
    print(f"{len(_fails)} Test(s) fehlgeschlagen: " + "; ".join(_fails))
    raise SystemExit(1)
print("Alle Parts-Safety-Hardening-Tests bestanden.")
