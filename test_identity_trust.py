"""
Identity-Trust-Gate — schwache/mehrdeutige Baureihen-Zuordnung darf keine
fahrzeugspezifische Aussage mehr tragen. KEIN Netzwerk, KEIN LLM-Call.

Hintergrund (DATA-TRUST-AUDIT): `find_baureihe` vergibt +4 Punkte für
`ml in rl or rl in ml`. Der Score wurde geloggt und danach VERWORFEN — der
Aufrufer konnte einen exakten Treffer nicht von einem zufälligen Teilstring
unterscheiden. Von acht erfundenen Modellnamen lösten sieben auf eine reale
Baureihe auf; "BMW iX7" erzeugte anschließend acht fahrzeugspezifische
Schwachstellen-Aktionen für ein Fahrzeug, das es nicht gibt.

  A-E)  die fünf erfundenen Modelle
  F)    BMW X1 vs. iX1
  G)    Audi Q8 vs. RS Q8
  H)    Audi TT vs. TT RS
  I)    BMW G20 2020 -> sicher
  J)    BMW G20 1995 -> nicht voll vertrauenswürdig
  K)    Opel Insignia B -> bestehender sicherer Treffer
  L)    unsicher -> keine fahrzeugspezifischen Aktionen
  M)    unsicher -> Basislisten vorhanden
  N)    unsicher -> keine konkreten Rückrufe
  O)    sicher   -> Ergebnis semantisch identisch zur Vorfassung
  P/Q)  keine Regression in P1-3 / P1-4

    python test_identity_trust.py
"""
from app.car_lookup import (
    find_baureihe, find_baureihe_mit_vertrauen, build_db_context,
    MATCH_EXACT, MATCH_MOTOR_ALIAS, MATCH_GENERATION, MATCH_STRONG,
    MATCH_AMBIGUOUS, MATCH_SUBSTRING, MATCH_TOKEN_INNER, MATCH_MARKE_ONLY,
    MATCH_NONE, MATCH_VERTRAUENSWUERDIG, _substring_art, _karosserie_vokabular,
)
from app.evidence import build_insights
from app.fahrzeugkontext import build_fahrzeugkontext
from app.kaufaktionen import build_kaufaktionen
from app.key_findings import build_key_findings_kauf
from app.models import KaufCheckRequest, KaufCheckResponse

_FEHLER: list[str] = []
BEREICHE = ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")


def check(name: str, bedingung: bool) -> None:
    status = "OK  " if bedingung else "FAIL"
    print(f"[{status}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def identitaet(marke, modell, baujahr):
    return find_baureihe_mit_vertrauen(marke, modell, baujahr)[1]


def pipeline(marke, modell, baujahr, motor=None, **kw):
    """Der deterministische Teil des Kaufchecks — exakt wie in run_kaufcheck,
    inklusive Identity-Trust-Gate."""
    from app.car_lookup import find_motor
    req = KaufCheckRequest(marke=marke, modell=modell, baujahr=baujahr, motor=motor, **kw)
    br_markt, info = find_baureihe_mit_vertrauen(marke, modell, baujahr)
    mo_markt = find_motor(br_markt, motor) if br_markt else None
    br, mo = (br_markt, mo_markt) if info["belastbar"] else (None, None)
    ctx = build_fahrzeugkontext(br)
    ins = build_insights(br, mo, [], req, check_typ="kauf")
    ka = build_kaufaktionen(req, br, mo, ins)
    kf = build_key_findings_kauf(req, br, mo, ins, None, identitaet=info)
    return dict(req=req, info=info, br_markt=br_markt, br=br, mo=mo,
                ctx=ctx, ins=ins, ka=ka, kf=kf,
                dbctx=build_db_context(br, mo, baujahr, fahrzeugkontext=ctx))


def spez(ka):
    return [a for b in BEREICHE for a in getattr(ka, b).fahrzeugspezifisch]


def basis(ka):
    return [a for b in BEREICHE for a in getattr(ka, b).basis]


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 0) Klassifikation der Match-Arten ===")

check("0.1 exakter Modellname", _substring_art("Golf", "Golf") == MATCH_EXACT)
check("0.2 Treffer IM Token erkannt", _substring_art("iX7", "X7") == MATCH_TOKEN_INNER)
check("0.3 Treffer IM Token (Ziffern)", _substring_art("A4711", "A4") == MATCH_TOKEN_INNER)
check("0.4 Tokengrenze mit bekanntem Aufbauwort", _substring_art("3er Touring", "3er") == MATCH_STRONG)
check("0.5 Tokengrenze mit unbekanntem Restwort",
      _substring_art("Golf XV", "Golf") == MATCH_SUBSTRING)
check("0.6 kein Zusammenhang -> None", _substring_art("Corsa", "Golf") is None)
check("0.7 Aufbauvokabular kommt aus der DB, nicht aus einer Wortliste",
      {"touring", "avant", "variant", "kombi", "limousine"} <= _karosserie_vokabular())
check("0.8 'xv'/'hyperdrive' sind KEINE Aufbauwörter",
      not ({"xv", "hyperdrive", "ultra"} & _karosserie_vokabular()))
check("0.9 nur vier Arten gelten als belastbar",
      MATCH_VERTRAUENSWUERDIG == {MATCH_EXACT, MATCH_MOTOR_ALIAS, MATCH_GENERATION, MATCH_STRONG})


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A-E) Die fünf erfundenen Modelle ===")

ERFUNDEN = [
    ("A", "BMW", "iX7", 2024),
    ("B", "Audi", "A4711", 2022),
    ("C", "Volkswagen", "Golf XV", 2022),
    ("D", "Opel", "Corsa-Z", 2022),
    ("E", "Toyota", "Corolla Hyperdrive", 2022),
]
for buchstabe, marke, modell, bj in ERFUNDEN:
    p = pipeline(marke, modell, bj)
    i = p["info"]
    check(f"{buchstabe}1 {marke} {modell}: nicht belastbar ({i['match_art']})",
          not i["belastbar"] and i["konfidenz"] == "niedrig")
    check(f"{buchstabe}2 {modell}: keine fahrzeugspezifische Evidence", p["ins"] == [])
    check(f"{buchstabe}3 {modell}: keine Schwachstellen-/Motorproblem-Aktionen",
          not [a for a in spez(p["ka"]) if a.kategorie in ("schwachstelle", "motorproblem")])
    check(f"{buchstabe}4 {modell}: keine konkreten Rückrufe",
          not [a for a in spez(p["ka"]) if a.kategorie == "rueckruf"])
    check(f"{buchstabe}5 {modell}: Basis-Checklisten vollständig vorhanden",
          all(len(getattr(p["ka"], b).basis) >= 8 for b in BEREICHE))
    check(f"{buchstabe}6 {modell}: Unsicherheit im Key Finding sichtbar",
          any(f.kategorie == "identitaet" for f in p["kf"]))
    check(f"{buchstabe}7 {modell}: kein DB-Profil im Prompt",
          "DB-Profil:" not in p["dbctx"])
    check(f"{buchstabe}8 {modell}: kein Fahrzeugkontext", p["ctx"] is None)

# Der Rohtreffer bleibt für die Marktrecherche erhalten (kein Datenverlust)
_ix7 = pipeline("BMW", "iX7", 2024)
check("A9 Rohtreffer bleibt für die Marktanalyse verfügbar", _ix7["br_markt"] is not None)
check("A10 aber NICHT für die deterministische Auswertung", _ix7["br"] is None)
check("A11 der Hinweis nennt KEINE vermutete Baureihe",
      all("x7" not in f"{f.titel} {f.beschreibung} {f.aktion}".lower()
          for f in _ix7["kf"] if f.kategorie == "identitaet"))
check("A12 der Hinweis sagt, was fehlt",
      any("modellbezeichnung" in (f.aktion or "").lower()
          for f in _ix7["kf"] if f.kategorie == "identitaet"))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== F/G/H) Echte Teilstring-Konflikte ===")

check("F1 BMW X1 bleibt ein sicherer Treffer",
      identitaet("BMW", "X1", 2022)["belastbar"])
check("F2 BMW iX1 wird NICHT als X1 durchgewinkt",
      not identitaet("BMW", "iX1", 2022)["belastbar"])
check("F3 iX1 erzeugt keine X1-Aktionen",
      not [a for a in spez(pipeline("BMW", "iX1", 2022)["ka"])
           if a.kategorie in ("schwachstelle", "motorproblem", "rueckruf")])

check("G1 Audi Q8 bleibt ein sicherer Treffer",
      identitaet("Audi", "Q8", 2022)["match_art"] == MATCH_EXACT)
check("G2 Audi RS Q8 wird NICHT dem Q8 zugeordnet",
      not identitaet("Audi", "RS Q8", 2022)["belastbar"])

check("H1 Audi TT bleibt ein sicherer Treffer",
      identitaet("Audi", "TT", 2018)["match_art"] == MATCH_EXACT)
check("H2 Audi TT RS wird NICHT dem TT zugeordnet",
      not identitaet("Audi", "TT RS", 2018)["belastbar"])

# Im Audit zusätzlich gefundene False Positives über den Motorpfad
check("H3 'Golf GTI' landet nicht mehr sicher beim VW up!",
      not identitaet("Volkswagen", "Golf GTI", 2015)["belastbar"])
check("H4 'e-tron' landet nicht mehr sicher beim RS e-tron GT",
      not identitaet("Audi", "e-tron", 2021)["belastbar"])


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== I/J/K) Sichere Treffer und Baujahres-Widerspruch ===")

_g20 = pipeline("BMW", "320d", 2020, motor="320d")
check("I1 BMW 320d 2020: belastbar", _g20["info"]["belastbar"])
check("I2 über den Motor-Alias erkannt", _g20["info"]["match_art"] == MATCH_MOTOR_ALIAS)
check("I3 Baureihe erkannt", _g20["br"] is not None and _g20["br"]["id"] == "bmw-3er-g20-g21")
check("I4 Motor erkannt", _g20["mo"] is not None)
check("I5 Evidence vorhanden", len(_g20["ins"]) > 0)
check("I6 fahrzeugspezifische Aktionen vorhanden", len(spez(_g20["ka"])) > 0)
check("I7 kein Identitäts-Warnfinding",
      not any(f.kategorie == "identitaet" for f in _g20["kf"]))

_g20_alt = pipeline("BMW", "320d", 1995, motor="320d")
check("J1 BMW 320d 1995: NICHT belastbar", not _g20_alt["info"]["belastbar"])
check("J2 keine Evidence aus der falschen Generation", _g20_alt["ins"] == [])
check("J3 keine Aktionen aus der falschen Generation",
      not [a for a in spez(_g20_alt["ka"]) if a.kategorie != "inserat"])
check("J4 Basislisten trotzdem vorhanden", len(basis(_g20_alt["ka"])) >= 40)

_ins_b = pipeline("Opel", "Insignia", 2020, motor="2.0 Diesel 174 PS")
check("K1 Opel Insignia B: belastbar", _ins_b["info"]["belastbar"])
check("K2 Baureihe unverändert", _ins_b["br"]["id"] == "opel-insignia-b")
# RECALL-PILOT: vorher 5. Der NOx-Rückruf (#546) trug den unbelegten
# Bauzeitraum "2019-2020" und erschien deshalb an diesem Fahrzeug. Der amtliche
# Rückruf 011422 betrifft die Baujahre 2013-2018 — für einen 2020er Insignia B
# ist er nicht einschlägig und fällt korrekt heraus. Der Zweck dieser Prüfung
# (Identität stimmt, Evidence entsteht) bleibt unberührt; zusätzlich wird jetzt
# ausdrücklich festgehalten, dass der Wegfall genau diesen Rückruf betrifft.
check("K3 Evidence unverändert vorhanden", len(_ins_b["ins"]) == 4)
check("K3b der amtliche NOx-Rückruf gilt für Baujahr 2020 korrekt NICHT mehr",
      not any("Abschalteinrichtung" in f.titel for f in _ins_b["ins"]))
check("K3c die Baureihen-Schwachstellen sind vollständig erhalten",
      len([f for f in _ins_b["ins"] if f.kategorie == "schwachstelle"]) == 3)
check("K4 Fahrzeugkontext vorhanden", _ins_b["ctx"] is not None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== L/M/N) Verhalten bei unsicherer Identität ===")

_u = pipeline("Toyota", "Corolla Hyperdrive", 2022)
check("L1 KEINE fahrzeugspezifische Aktion aus DB-Daten",
      all(a.kategorie == "inserat" for a in spez(_u["ka"])))
check("L2 keine Insights", _u["ins"] == [])
check("L3 keine Key Findings aus DB-Befunden",
      all(f.kategorie in ("identitaet", "vorteil") for f in _u["kf"]))
check("M1 Besichtigungs-Basis vollständig", len(_u["ka"].besichtigung.basis) >= 12)
check("M2 Probefahrt-Basis vollständig", len(_u["ka"].probefahrt.basis) >= 15)
check("M3 Verkäuferfragen-Basis vollständig", len(_u["ka"].verkaeuferfragen.basis) >= 8)
check("M4 Dokumenten-Basis vollständig", len(_u["ka"].dokumente.basis) >= 8)
check("M5 alle vier Listen bleiben exportierbar",
      all(getattr(_u["ka"], b).export_title for b in BEREICHE))
check("N1 kein Rückruf als konkreter Befund",
      not [a for a in spez(_u["ka"]) + basis(_u["ka"]) if a.kategorie == "rueckruf"])
check("N2 kein Rückruf-Insight", not [i for i in _u["ins"] if i.kategorie == "rueckruf"])
check("N3 kein Rückruf-Text in irgendeiner Aktion",
      not any("rückrufaktion" in f"{a.titel} {a.aktion}".lower()
              for a in spez(_u["ka"]) if a.kategorie != "basis"))
check("N4 der Prompt enthält kein DB-Rückrufprofil", "Rückrufe" not in _u["dbctx"])
check("N5 Nutzerangaben bleiben sichtbar",
      any(a.kategorie == "inserat" for a in spez(_u["ka"])))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== O/P/Q) Keine Regression bei sicheren Treffern ===")

SICHER = [("BMW", "320d", 2020, "320d"), ("Opel", "Insignia", 2020, "2.0 Diesel 174 PS"),
          ("Volkswagen", "Golf", 2015, "1.4 TSI"), ("Audi", "A4", 2018, "2.0 TDI"),
          ("Mercedes-Benz", "C 200", 2019, "C 200")]
for marke, modell, bj, mot in SICHER:
    p = pipeline(marke, modell, bj, motor=mot)
    # find_baureihe (unveraenderte Signatur) muss dieselbe Baureihe liefern
    roh = find_baureihe(marke, modell, bj)
    check(f"O1 {marke} {modell}: find_baureihe unverändert",
          (roh or {}).get("id") == (p["br_markt"] or {}).get("id"))
    check(f"O2 {marke} {modell}: belastbar, Baureihe wird verwendet",
          p["info"]["belastbar"] and p["br"] is not None)
    check(f"O3 {marke} {modell}: kein Identitäts-Warnfinding",
          not any(f.kategorie == "identitaet" for f in p["kf"]))
    check(f"O4 {marke} {modell}: DB-Profil erreicht den Prompt", "DB-Profil:" in p["dbctx"])
    check(f"P1 {marke} {modell}: P1-3 Basislisten unverändert vorhanden",
          all(len(getattr(p["ka"], b).basis) > 0 for b in BEREICHE))
    check(f"Q1 {marke} {modell}: P1-4 Fahrzeugkontext vorhanden", p["ctx"] is not None)

check("O5 Evidence-IDs bei sicherem Treffer weiterhin gültig",
      all(set(a.evidence_ids) <= {i.id for i in _g20["ins"]} for a in spez(_g20["ka"])))
check("P2 fahrzeugspezifisch/basis bleiben getrennt",
      all(a.typ == "fahrzeugspezifisch" for a in spez(_g20["ka"]))
      and all(a.typ == "basis" for a in basis(_g20["ka"])))
check("Q2 Fahrzeugkontext ist weiterhin keine Evidence",
      all(i.kategorie != "fahrzeugkontext" for i in _g20["ins"]))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== R) Rückwärtskompatibilität ===")

check("R1 find_baureihe liefert weiterhin dict|None",
      isinstance(find_baureihe("BMW", "320d", 2020), dict)
      and find_baureihe("Wuling", "Hongguang Mini EV", 2021) is None)
check("R2 unbekanntes Fahrzeug -> no_match",
      identitaet("Wuling", "Hongguang Mini EV", 2021)["match_art"] == MATCH_NONE)
check("R3 nur Marke ohne Modell gilt als Rateweg",
      identitaet("BMW", None, 2016)["match_art"] in (MATCH_MARKE_ONLY, MATCH_AMBIGUOUS)
      and not identitaet("BMW", None, 2016)["belastbar"])
_alt = KaufCheckResponse(bericht="alt", empfehlung="kaufen", preis_bewertung="marktgerecht",
                         quelle="datenbank", vertrauen="hoch")
check("R4 Alt-Check ohne die neuen Felder bleibt gültig",
      _alt.identitaet_konfidenz == "hoch" and _alt.identitaet_match_art is None)
check("R5 neue Felder sind keine Pflichtfelder",
      KaufCheckResponse.model_fields["identitaet_konfidenz"].is_required() is False
      and KaufCheckResponse.model_fields["identitaet_match_art"].is_required() is False)
check("R6 build_key_findings_kauf bleibt ohne den neuen Parameter aufrufbar",
      isinstance(build_key_findings_kauf(_g20["req"], _g20["br"], _g20["mo"],
                                         _g20["ins"], None), list))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE IDENTITY-TRUST-TESTS GRUEN")
