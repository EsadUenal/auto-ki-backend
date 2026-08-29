"""
VerkaufsCheck P1 #1 — Identity-Trust-Gate.

Der VerkaufsCheck nutzte `find_baureihe` OHNE Konfidenz-Gate: eine mehrdeutige
oder erfundene Eingabe ("Golf XV", nur "BMW") wurde kommentarlos als konkrete
Baureihe behandelt und trug fahrzeugspezifische DB-Fakten. Jetzt gilt — analog
zum gefreezten KaufCheck — dieselbe Gatelogik: nur ein belastbarer Treffer
(exact / motor_alias / generation / strong) darf fahrzeugspezifische Aussagen
tragen; der Rohtreffer bleibt für die Marktrecherche erhalten.

Deterministisch: KEIN Netzwerk, KEIN LLM-Call — nur der gegatete Vorlauf von
`run_verkaufscheck` (identisch nachgebaut) plus die deterministischen Bausteine.

    python test_verkaufscheck_identity_trust.py
"""
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from app.car_lookup import (
    find_baureihe, find_baureihe_mit_vertrauen, find_motor, build_db_context,
    MATCH_EXACT, MATCH_MOTOR_ALIAS, MATCH_VERTRAUENSWUERDIG,
)
from app.evidence import build_insights
from app.fakt_verifikation import sichtbare_fakten
from app.inserat import build_listing_analyse
from app.key_findings import build_key_findings_verkauf
from app.models import VerkaufsCheckRequest, VerkaufsCheckResponse

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def gate(marke, modell, baujahr, motor=None, **kw):
    """Exakt der Identity-Trust-Vorlauf aus run_verkaufscheck (P1 #1)."""
    req = VerkaufsCheckRequest(marke=marke, modell=modell, baujahr=baujahr, motor=motor, **kw)
    br_markt, info = find_baureihe_mit_vertrauen(marke, modell, baujahr)
    mo_markt = find_motor(br_markt, motor) if br_markt else None
    br, mo = (br_markt, mo_markt) if info["belastbar"] else (None, None)
    ins = build_insights(br, mo, [], req, check_typ="verkauf")
    kf = build_key_findings_verkauf(req, br, mo, ins)
    la = build_listing_analyse(req, br, mo, ins)
    dbctx = build_db_context(br, mo, baujahr)
    return dict(req=req, info=info, br_markt=br_markt, br=br, mo=mo,
                ins=ins, kf=kf, la=la, dbctx=dbctx)


def spez_insight_kategorien(ins):
    return {i.kategorie for i in ins}


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 1) Bekanntes Fahrzeug -> belastbare Identität, DB-Fakten ===")

p_ok = gate("Volkswagen", "Passat", 2009, motor="2.0 TDI", kilometerstand=180_000,
            kraftstoff="Diesel", preis_vorstellung=6_500)
check("1.1 Identität belastbar", p_ok["info"]["belastbar"] and p_ok["info"]["konfidenz"] == "hoch")
check("1.2 Baureihe wird verwendet", p_ok["br"] is not None
      and p_ok["br"]["id"] == "volkswagen-passat-b6")
check("1.3 fahrzeugspezifische Insights vorhanden", len(p_ok["ins"]) > 0)
check("1.4 technische Insight-Kategorien vorhanden",
      spez_insight_kategorien(p_ok["ins"]) & {"schwachstelle", "rueckruf", "motorproblem"})
check("1.5 DB-Profil erreicht den Prompt", "DB-Profil:" in p_ok["dbctx"])

_g20 = gate("BMW", "320d", 2020, motor="320d", kilometerstand=80_000, kraftstoff="Diesel")
check("1.6 BMW 320d 2020 über Motor-Alias belastbar",
      _g20["info"]["belastbar"] and _g20["info"]["match_art"] == MATCH_MOTOR_ALIAS)
check("1.7 Baureihe bmw-3er-g20-g21", (_g20["br"] or {}).get("id") == "bmw-3er-g20-g21")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 2) 'Golf XV' -> NIEMALS stillschweigend Golf-Zuordnung ===")

p_xv = gate("Volkswagen", "Golf XV", 2022, kilometerstand=30_000)
check("2.1 nicht belastbar", not p_xv["info"]["belastbar"]
      and p_xv["info"]["konfidenz"] == "niedrig")
check("2.2 match_art == substring_only", p_xv["info"]["match_art"] == "substring_only")
check("2.3 Rohtreffer bleibt für die Marktrecherche verfügbar", p_xv["br_markt"] is not None)
check("2.4 aber NICHT für die deterministische Auswertung (baureihe = None)",
      p_xv["br"] is None)
check("2.5 keine fahrzeugspezifischen Insights", p_xv["ins"] == [])
check("2.6 kein DB-Profil im Prompt", "DB-Profil:" not in p_xv["dbctx"])
check("2.7 kein Rückrufprofil im Prompt", "Rückrufe" not in p_xv["dbctx"])
check("2.8 Listing-Analyse läuft trotzdem (partial)", p_xv["la"] is not None
      and p_xv["la"].gesamt > 0)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 3) Nur 'BMW' ohne Modell -> keine konkrete Baureihe als belastbar ===")

p_bmw = gate("BMW", None, 2018, kilometerstand=90_000)
check("3.1 nicht belastbar", not p_bmw["info"]["belastbar"])
check("3.2 match_art marke_only oder ambiguous",
      p_bmw["info"]["match_art"] in ("marke_only", "ambiguous"))
check("3.3 keine Baureihe verwendet", p_bmw["br"] is None)
check("3.4 keine fahrzeugspezifischen Insights", p_bmw["ins"] == [])
check("3.5 kein DB-Profil im Prompt", "DB-Profil:" not in p_bmw["dbctx"])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 4) Mehrdeutige Eingabe -> keine harten baureihenspezifischen Aussagen ===")

for marke, modell, bj in [("Audi", "RS Q8", 2022), ("Audi", "TT RS", 2018),
                          ("BMW", "iX1", 2022)]:
    p = gate(marke, modell, bj)
    check(f"4.x {marke} {modell}: nicht belastbar", not p["info"]["belastbar"])
    check(f"4.x {marke} {modell}: keine Insights", p["ins"] == [])
    check(f"4.x {marke} {modell}: kein DB-Profil im Prompt", "DB-Profil:" not in p["dbctx"])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 5) Rejected Fakt bleibt verborgen ===")

# Der VerkaufsCheck laedt `baureihe` ueber get_baureihe -> `sichtbare_fakten`
# entfernt bereits alle als `rejected` gesperrten Fakten (geteiltes Gate, exakt
# derselbe Pfad wie beim KaufCheck). Hier direkt geprueft: ein `_gesperrt`-Fakt
# ueberlebt `sichtbare_fakten` nicht und taucht damit in keinem Insight auf.
_schwachstellen = [
    {"bauteil": "Steuerkette", "beschreibung": "Kettenlängung möglich",
     "schweregrad": "hoch", "betroffene_baujahre": "2015-2020",
     "_trust": "unverified_db", "_gesperrt": True},
    {"bauteil": "Wasserpumpe", "beschreibung": "vorzeitiger Ausfall",
     "schweregrad": "mittel", "betroffene_baujahre": "2015-2020",
     "_trust": "verified", "_gesperrt": False},
]
_sichtbar = sichtbare_fakten(_schwachstellen)
check("5.1 sichtbare_fakten entfernt den gesperrten (rejected) Fakt",
      len(_sichtbar) == 1 and _sichtbar[0]["bauteil"] == "Wasserpumpe")

_rej_baureihe = {
    "id": "test-rejected", "marke": "TestMarke", "modell": "TestModell",
    "generation": "T1", "bauzeitraum_von": 2015, "bauzeitraum_bis": 2020,
    "karosserie": ["Limousine"], "motoren": [],
    "schwachstellen_baureihe": _sichtbar, "rueckrufe": [],
}
_req_rej = VerkaufsCheckRequest(marke="TestMarke", modell="TestModell", baujahr=2017)
_ins_rej = build_insights(_rej_baureihe, None, [], _req_rej, check_typ="verkauf")
_titel = " ".join(i.titel + " " + (i.beschreibung or "") for i in _ins_rej).lower()
check("5.2 rejected Schwachstelle NICHT als Insight sichtbar", "steuerkette" not in _titel)
check("5.3 verbleibende Schwachstelle bleibt sichtbar", "wasserpumpe" in _titel)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 6) Unverified Fakt -> sichtbar, aber ohne harte Trust-Stufe ===")

_unv_baureihe = dict(_rej_baureihe, id="test-unverified", schwachstellen_baureihe=[
    {"bauteil": "Turbolader", "beschreibung": "Ölkohle-Bildung", "schweregrad": "hoch",
     "betroffene_baujahre": "2015-2020", "_trust": "unverified_db", "_gesperrt": False},
])
_ins_unv = build_insights(_unv_baureihe, None, [], _req_rej, check_typ="verkauf")
check("6.1 unverified Fakt erscheint als Insight", len(_ins_unv) >= 1)
check("6.2 Trust der Insight ist NICHT 'verified'",
      all(getattr(i, "trust", None) != "verified" for i in _ins_unv))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 7) Rückwärtskompatibilität ===")

check("7.1 find_baureihe (Alt-Signatur) liefert weiterhin dieselbe Baureihe",
      (find_baureihe("BMW", "320d", 2020) or {}).get("id")
      == (_g20["br_markt"] or {}).get("id"))
_alt = VerkaufsCheckResponse(bericht="alt", quelle="datenbank", vertrauen="hoch")
check("7.2 Alt-Response ohne die neuen Felder bleibt gültig",
      _alt.identitaet_konfidenz == "hoch" and _alt.identitaet_match_art is None)
check("7.3 neue Response-Felder sind keine Pflichtfelder",
      VerkaufsCheckResponse.model_fields["identitaet_konfidenz"].is_required() is False
      and VerkaufsCheckResponse.model_fields["identitaet_match_art"].is_required() is False)
check("7.4 belastbare Match-Arten unverändert",
      MATCH_EXACT in MATCH_VERTRAUENSWUERDIG and MATCH_MOTOR_ALIAS in MATCH_VERTRAUENSWUERDIG)


print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE VERKAUFSCHECK-IDENTITY-TRUST-TESTS GRUEN")
