"""Generic invariants plus five full-pipeline replays, entirely offline.

Run with scripts/test_isolated.py. No fixture-specific production rules.
"""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import kaufcheck as kc
from app.bekannte_fakten import aus_request
from app.car_lookup import find_baureihe_mit_vertrauen, find_motor
from app.evidence import build_insights
from app.models import KaufCheckRequest, KaufCheckResponse, Insight
from app.motor_applicability import varianten_applicability
from app.vehicle_identity import VehicleIdentity


PROFILES = json.loads(Path("tests_fixtures/kaufcheck_multicar.json").read_text(encoding="utf-8"))


@pytest.fixture
def provider(monkeypatch):
    prompts = []

    async def forbidden(*args, **kwargs):
        pytest.fail("Real provider path must not be used")

    async def no_research(*args, **kwargs):
        return None

    async def fake(system, user):
        prompts.append((system, user))
        # Deliberate attack on every free-output path, not just missing prompt rules.
        return {"bericht": "## Kritische Risiken\nErfundenes Risikothema. "
                "Panoramadach defekt. Moderate Laufleistung. Unfallfrei. "
                "Offener Rückruf. Navigation Professional, M Sportpaket, Head-up Display.",
                "empfehlung": "finger_weg", "preis_bewertung": "guenstig",
                "risiko_evidence_ids": ["invented"], "empfehlung_evidence_ids": ["invented"],
                "marktpreis_min": 100, "marktpreis_max": 200}

    monkeypatch.setattr(kc, "call_gemini_json", fake)
    monkeypatch.setattr(kc, "recherchiere_technisch", no_research)
    monkeypatch.setattr(kc, "marktpreis_recherche_moeglich", lambda *args: False)
    monkeypatch.setattr(kc, "tavily_search_with_fallback", forbidden)
    monkeypatch.setattr(kc, "vertiefe_marktrecherche", forbidden)
    return prompts


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p["modell"])
def test_full_pipeline(profile, provider):
    req = KaufCheckRequest(**profile)
    result = KaufCheckResponse(**asyncio.run(kc.run_kaufcheck(req)))
    identity = result.vehicle_identity
    assert identity["generation"]
    assert result.motor_erkannt
    # Final-Stabilization: kanonischer Wert normalisiert, Rohangabe erhalten.
    assert identity["fuel"].lower() == "benzin"
    assert identity["field_evidence"]["fuel"]["raw_user_value"] == "Benzin"
    assert identity["field_evidence"]["fuel"]["primary_source"] == "user"
    assert identity["horsepower"] == profile["leistung_ps"]
    assert identity["field_evidence"]["horsepower"]["provided_value"] == profile["leistung_ps"]
    assert identity["field_evidence"]["horsepower"]["confidence"] == "hoch"
    if result.motor_erkannt:
        assert "Motorisierung aber nicht eindeutig" not in " ".join(result.empfehlung_gruende)
    assert result.empfehlung != "finger_weg"
    assert result.marktpreis_min is result.marktpreis_max is None
    assert "## Relevante Risiken und Hinweise" in result.bericht
    assert "## Kritische Risiken" not in result.bericht
    assert "moderate" not in result.bericht.lower()
    assert "Erfundenes Risikothema" not in result.bericht
    assert "offener rückruf" not in result.bericht.lower()
    assert "invented" not in result.risiko_evidence_ids
    risks = [i for i in result.insights if i.kategorie in ("schwachstelle", "motorproblem", "rueckruf")]
    risk_text = " ".join(f"{i.titel} {i.bauteil or ''} {i.beschreibung}" for i in risks).lower()
    if identity.get("fuel", "").lower() == "benzin":
        assert "adblue" not in risk_text and "dieselpartikelfilter" not in risk_text
    if identity.get("powertrain") in ("ICE", "MHEV"):
        assert "hochvoltspeicher" not in risk_text and "traktionsbatterie" not in risk_text
    for insight in risks:
        marker = (insight.kurztitel or insight.bauteil or insight.titel).split(":", 1)[0]
        assert marker in result.bericht
    for insight in result.insights:
        if insight.kategorie == "rueckruf":
            assert insight.recall_status["vehicle_affected"] == "unknown"
            assert insight.recall_status["completion"] == "unknown"
            assert "Betroffenheit" in insight.einfluss and "falls betroffen" in insight.einfluss
    questions = result.kaufaktionen.verkaeuferfragen
    tuning = [a for a in questions.basis + questions.fahrzeugspezifisch if "tuning" in a.id]
    assert len(tuning) == 1
    assert "software- oder hardwareseitig" in tuning[0].titel
    assert "Inserat-/Nutzereingaben" in result.datenbasis
    assert "ENFAL-Fahrzeugdatenbank" in result.datenbasis
    assert not any("Web" in source for source in result.datenbasis)
    assert profile["tuev_bis"] in result.bericht
    assert str(profile["vorbesitzer"]) in result.bericht
    assert aus_request(req).letzte_wartung.anzeige() in result.bericht
    assert result.accident_status == ("unfallfrei" if profile["unfallfrei"] == "ja" else "unknown")
    if result.accident_status == "unknown":
        assert "Unfallhistorie" in " ".join(result.empfehlung_gruende)
        assert "Zustand, Unfallfreiheit und Wartung sind Inseratsangaben" not in result.bericht


@pytest.mark.parametrize("field,value,scope,expected", [
    ("fuel", "Benzin", "diesel_only", "incompatible"),
    ("fuel", "Diesel", "diesel_only", "kompatibel"),
    ("powertrain", "ICE", "phev_only", "incompatible"),
    ("powertrain", "PHEV", "phev_only", "kompatibel"),
    ("powertrain", "ICE", "ev_only", "incompatible"),
    ("powertrain", "EV", "ev_only", "kompatibel"),
    ("transmission", "automatik", "manual_only", "incompatible"),
    ("transmission", "manuell", "automatic_only", "incompatible"),
    ("transmission", None, "automatic_only", "unklar"),
])
def test_applicability(field, value, scope, expected):
    identity = VehicleIdentity(**{field: value})
    assert varianten_applicability({"applicability_rules": {"scope": scope}}, identity)[0] == expected


def test_intrinsic_components_and_downstream_filtering():
    req = KaufCheckRequest(marke="Example", modell="Model", baujahr=2020,
                           kraftstoff="Benzin", getriebe="Automatik", powertrain="ICE")
    b = {"marke": "Example", "modell": "Model", "generation": "XX1", "motoren": [],
         "schwachstellen_baureihe": [{"id": 1, "bauteil": "AdBlue-System", "beschreibung": "Füllstand prüfen"}],
         "rueckrufe": [{"id": 2, "mangel": "Hochvoltspeicher", "betroffene_baujahre": "2019-2021"}]}
    assert build_insights(b, None, [], req) == []
    diesel = req.model_copy(update={"kraftstoff": "Diesel"})
    assert any(i.bauteil == "AdBlue-System" for i in build_insights(b, None, [], diesel))


def test_request_isolation_and_output_allowlist(provider):
    a = KaufCheckRequest(**PROFILES[1]).model_copy(update={"ausstattung": ["SENTINEL-A-ONLY", "Head-up Display"]})
    b = KaufCheckRequest(**PROFILES[2]).model_copy(update={"ausstattung": ["SENTINEL-B-ONLY"]})
    original = copy.deepcopy(b.model_dump())
    asyncio.run(kc.run_kaufcheck(a))
    result = asyncio.run(kc.run_kaufcheck(b))
    assert "SENTINEL-A-ONLY" not in provider[-1][1]
    assert "Head-up Display" not in provider[-1][1]
    assert "SENTINEL-B-ONLY" in provider[-1][1]
    assert "Head-up Display" not in result["bericht"]
    assert "Navigation Professional" not in result["bericht"]
    assert b.model_dump() == original


def test_mixed_quality_is_counted_not_guessed():
    from app.empfehlung_gruende import _technische_datenlage
    risks = [Insight(id="a", kategorie="schwachstelle", titel="Software", beschreibung="Vereinzelt",
                     confidence="mittel", trust="verified", schweregrad="gering"),
             Insight(id="b", kategorie="schwachstelle", titel="Geräusche", beschreibung="Selten",
                     confidence="niedrig", trust="unverified_db", schweregrad="gering")]
    text = " ".join(_technische_datenlage(risks, "kaufen_nach_besichtigung"))
    assert "1 Hinweis mit Datenqualität mittel" in text
    assert "1 Hinweis mit Datenqualität niedrig" in text
    assert "alle" not in text or "alle sind ungeprüft" not in text


@pytest.mark.parametrize("value,status", [(None, "unknown"), ("unbekannt", "unknown"),
                                           ("ja", "unfallfrei"), ("nein", "unfall")])
def test_accident_status(value, status):
    assert aus_request(KaufCheckRequest(unfallfrei=value)).unfall == status


@pytest.mark.parametrize("value,expected", [(None, 1), ("unbekannt", 1),
                                               ("Stage 1", 1), ("kein Tuning", 0)])
def test_tuning_question_matches_structured_status(value, expected):
    from app.kaufaktionen import build_kaufaktionen
    req = KaufCheckRequest(marke="Example", modell="Model", baujahr=2020, tuning=value)
    actions = build_kaufaktionen(req, None, None, [])
    questions = actions.verkaeuferfragen.basis + actions.verkaeuferfragen.fahrzeugspezifisch
    assert len([a for a in questions if "tuning" in a.id]) == expected


def test_explicit_generation_generic(monkeypatch):
    import app.car_lookup as cl
    rows = [{"id": "example", "marke": "Example-Motors", "modell": "Familie", "generation": "ZX42",
             "bauzeitraum_von": 2010, "bauzeitraum_bis": 2022}]
    monkeypatch.setattr(cl, "get_alle_baureihen_kurz", lambda: rows)
    monkeypatch.setattr(cl, "get_alle_motorvarianten_kurz", lambda: [])
    monkeypatch.setattr(cl, "get_baureihe", lambda *a: rows[0])
    for make, model in [("example motors", "Ausführung ZX42"), ("EXAMPLE--MOTORS", "ZX42")]:
        b, match = find_baureihe_mit_vertrauen(make, model, 2019)
        assert b and match["belastbar"]
    assert not find_baureihe_mit_vertrauen("Example Motors", "ZX42", 1990)[1]["belastbar"]


def test_motor_requires_unique_consistent_candidate():
    variants = [{"variante_id": str(n), "bezeichnung": name, "motorcode": "AB12", "leistung_ps": 150,
                 "kraftstoff": "Benzin", "antrieb": drive, "getriebe": ["Automatik"]}
                for n, name, drive in [(1, "Sport", "Front"), (2, "Sport AWD", "Allrad")]]
    br = {"motoren": variants, "generation": "ZX42"}
    req = KaufCheckRequest(motor="1.5 Turbo AB12", leistung_ps=150, kraftstoff="Benzin")
    assert find_motor(br, req.motor, req=req) is None
    assert find_motor(br, req.motor, req=req.model_copy(update={"antrieb": "FWD"}))["variante_id"] == "1"
    assert find_motor(br, req.motor, req=req.model_copy(update={"leistung_ps": 300})) is None


def test_motor_uses_known_base_fuel_but_keeps_missing_db_code_unknown():
    variants = [
        {"variante_id": "petrol", "bezeichnung": "2.0 TFSI", "motorcode": "",
         "leistung_ps": 190, "kraftstoff": "Mild-Hybrid", "antrieb": "Front"},
        {"variante_id": "diesel", "bezeichnung": "2.0 TDI", "motorcode": "DX12",
         "leistung_ps": 190, "kraftstoff": "Mild-Hybrid", "antrieb": "Front"},
    ]
    br = {"motoren": variants, "generation": "ZX42"}
    req = KaufCheckRequest(motor="2.0 Turbo AB12", leistung_ps=190,
                           kraftstoff="Benzin", antrieb="FWD")
    assert find_motor(br, req.motor, req=req)["variante_id"] == "petrol"
