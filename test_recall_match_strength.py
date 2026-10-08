"""
Match-Staerke (Ebene A) + sicherer Ambiguitaets-Fallback (Ebene B) —
Audi-A4-B9-Root-Cause-Fund (KBA 9831/10206).

KEIN Netzwerk, KEIN LLM-Call, KEINE Tavily-Calls, KEINE DB-Mutation.

  A) direkter Nameplate-Treffer schlaegt Alias-Treffer (match_tier)
  B) zwei gleich starke Nameplate-Treffer bleiben ein echter Gleichstand
  C) echter Gleichstand + Fahrzeug im Candidate-Set + keine Contradiction
     -> sicherer Fallback sichtbar
  D) echter Gleichstand + Fahrzeug NICHT im Candidate-Set -> kein Fallback
  E) bekannter Kraftstoffwiderspruch (Motorcode-Kuerzel TDI/TFSI, nicht nur
     das ausgeschriebene Wort) -> NOT_APPLICABLE, kein Hinweis
  F) Bedingung unbekannt -> trotzdem sichtbar (UNKNOWN/FIN), nicht versteckt
  G) Bedingung passt -> Fallback bleibt IMMER "unclear" (nie staerker, nie
     "confirmed")
  H) zwei gleich starke ALIAS-Treffer (nicht Nameplate) bleiben ebenfalls ein
     echter Gleichstand (RS 6 / RS 6 Avant, beide nur ueber "A6"-Alias)
  I) cross-brand: derselbe Token bei zwei Marken erzeugt NIE eine
     gemeinsame Konkurrenz
  J) Produktionsfenster-Widerspruch -> kein Fallback
  K) bereits kanonischer Treffer -> kein zweiter, schwaecherer Fallback-Eintrag
  L) "Randueberlappung"/"ueberdehnte offene Generation" sind NICHT
     fallback-faehig (nur echte Modell-/Generationsmehrdeutigkeit)
  M) ein anderer Nicht-Audi-Fall beweist Markenunabhaengigkeit (Mercedes
     GLE W166/W167, real im KBA-Export resolved-by-ranking)

    python test_recall_match_strength.py
"""
import json

from app.kba_reconciliation import match_tier, _vira_modellkandidaten
from app.kba_import_kandidaten import (
    AMBIGUOUS_GENERATION, GRUND_MODELL_AMBIGUITAET, SAFE_IMPORT,
    import_kandidaten,
)
from app.recall_ambiguity_fallback import ambiguitaet_hinweise
from app.recall_filter import RECALL_NOT_APPLICABLE, rueckruf_scope
from app.vehicle_identity import VehicleIdentity

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def kba_zeile(**kw) -> dict:
    z = {
        "KBA-Referenznummer": "9001", "Rückrufcode des Herstellers": "ABC",
        "Veröffentlichungsdatum": "2020-05-01", "Marke": "AUDI",
        "Modell": "A4",
        "Mangelbezeichnung": "Die Lenkspindel kann brechen.",
        "Produktionszeitraum von": "2018", "Produktionszeitraum bis": "2020",
        "Beschreibung der Maßnahme": "Austausch der Lenkspindel.",
        "Mögliche Eingrenzung der betroffenen Modelle": "N/A",
        "Überwachung der Rückrufaktion durch das KBA": "überwacht",
    }
    z.update(kw)
    return z


def br(**kw) -> dict:
    b = {"id": "audi-a4-b9", "marke": "Audi", "modell": "A4",
         "generation": "B9", "bauzeitraum_von": 2015, "bauzeitraum_bis": 2023}
    b.update(kw)
    return b


def paare_von(kba_rows, baureihen, recalls=None):
    k = import_kandidaten(kba_rows, recalls or [], baureihen)
    return {bid: kl for bid, kl, _g in k[0].paare} if k else {}


def fallback_ids_von(kba_rows, baureihen, recalls=None):
    k = import_kandidaten(kba_rows, recalls or [], baureihen)
    if not k:
        return []
    return sorted({bid for bid, kl, grund in k[0].paare
                  if kl == AMBIGUOUS_GENERATION and grund.endswith(GRUND_MODELL_AMBIGUITAET)})


# ══ A) Direkter Nameplate-Treffer schlaegt Alias-Treffer ════════════════════
print("\n--- A) Nameplate (Tier 1) schlaegt Alias (Tier 2) ---")
_a4 = br(id="audi-a4-b9", marke="Audi", modell="A4", generation="B9",
        bauzeitraum_von=2015, bauzeitraum_bis=2023)
_rs4_avant = br(id="audi-rs-4-avant-b9", marke="Audi", modell="RS 4 Avant",
               generation="B9", bauzeitraum_von=2017, bauzeitraum_bis=None)
check("A1 match_tier: audi-a4-b9 erreicht 'A4' als eigenen Namen (Tier 1)",
      match_tier("Audi", "A4", "A4") == 1)
check("A2 match_tier: audi-rs-4-avant-b9 erreicht 'A4' NUR per Alias (Tier 2)",
      match_tier("Audi", "RS 4 Avant", "A4") == 2)

_kba_9831 = kba_zeile(**{
    "KBA-Referenznummer": "9831", "Modell": "A6, A7, A4, A5, Q5",
    "Mangelbezeichnung": "Durch Feuchtigkeitseintritt in den Riemenstartergenerator "
                        "können Kurzschlussbrücken entstehen. Fahrzeugbrand möglich.",
    "Mögliche Eingrenzung der betroffenen Modelle":
        "Es sind ausschließlich Fahrzeuge mit 2.0 TFSI und Mild-Hybrid-System betroffen",
})
_paare_a = paare_von([_kba_9831], [_a4, _rs4_avant])
check("A3 audi-a4-b9 wird SAFE_IMPORT (direkter Nameplate-Treffer gewinnt)",
      _paare_a.get("audi-a4-b9") == SAFE_IMPORT)
check("A4 audi-rs-4-avant-b9 bleibt AMBIGUOUS_GENERATION (Alias-Treffer "
      "wird NICHT automatisch ausgeschlossen, nur nicht kanonisch)",
      _paare_a.get("audi-rs-4-avant-b9") == AMBIGUOUS_GENERATION)
_fb_a = fallback_ids_von([_kba_9831], [_a4, _rs4_avant])
check("A5 fallback_baureihen enthaelt NUR die schwaechere Alternative, "
      "NICHT den bereits kanonischen Gewinner",
      _fb_a == ["audi-rs-4-avant-b9"])


# ══ B) Echter Gleichstand (zwei Nameplate-Treffer) bleibt bestehen ══════════
print("\n--- B) echter Nameplate-Gleichstand bleibt ein Gleichstand ---")
_x5_g1 = br(id="bmw-x5-g1", marke="BMW", modell="X5", generation="G1",
           bauzeitraum_von=2013, bauzeitraum_bis=2018)
_x5_g2 = br(id="bmw-x5-g2", marke="BMW", modell="X5", generation="G2",
           bauzeitraum_von=2018, bauzeitraum_bis=2023)
_kba_x5 = kba_zeile(Marke="BMW", Modell="X5", **{
    "Produktionszeitraum von": "2017", "Produktionszeitraum bis": "2019"})
_paare_b = paare_von([_kba_x5], [_x5_g1, _x5_g2])
check("B1 beide X5-Generationen erreichen 'X5' gleich stark (beide Tier 1) "
      "-> KEINE willkuerliche canonical Entscheidung fuer g1",
      _paare_b.get("bmw-x5-g1") == AMBIGUOUS_GENERATION)
check("B2 ... und auch nicht fuer g2",
      _paare_b.get("bmw-x5-g2") == AMBIGUOUS_GENERATION)


# ══ H) Echter Gleichstand bei ZWEI Alias-Treffern (RS6 / RS6 Avant) ═════════
print("\n--- H) echter Gleichstand bei zwei Alias-Treffern (kein Nameplate-Sieger) ---")
_rs6 = br(id="audi-rs-6-c8", marke="Audi", modell="RS 6", generation="C8",
         bauzeitraum_von=2019, bauzeitraum_bis=2023)
_rs6_avant = br(id="audi-rs-6-avant-c8", marke="Audi", modell="RS 6 Avant",
               generation="C8", bauzeitraum_von=2019, bauzeitraum_bis=2023)
check("H0 Vorbedingung: RS 6 erreicht 'A6' NUR per Alias (nicht Tier 1)",
      "A6" in _vira_modellkandidaten("Audi", "RS 6")
      and match_tier("Audi", "RS 6", "A6") == 2)
check("H0b Vorbedingung: RS 6 Avant erreicht 'A6' ebenfalls NUR per Alias",
      match_tier("Audi", "RS 6 Avant", "A6") == 2)
_kba_rs6 = kba_zeile(Marke="AUDI", Modell="A6", **{
    "Produktionszeitraum von": "2020", "Produktionszeitraum bis": "2021"})
_paare_h = paare_von([_kba_rs6], [_rs6, _rs6_avant])
check("H1 RS 6 bleibt AMBIGUOUS_GENERATION (Alias-Gleichstand mit RS 6 Avant, "
      "keine Rangfolge zwischen zwei gleich schwachen Treffern)",
      _paare_h.get("audi-rs-6-c8") == AMBIGUOUS_GENERATION)
check("H2 RS 6 Avant ebenso",
      _paare_h.get("audi-rs-6-avant-c8") == AMBIGUOUS_GENERATION)


# ══ I) Cross-Brand: derselbe Token bei zwei Marken konkurriert NIE ══════════
print("\n--- I) cross-brand: kein gemeinsamer Wettbewerb ueber Markengrenzen ---")
_audi_a4 = br(id="audi-a4-b9", marke="Audi", modell="A4")
_fiktive_fremdmarke_a4 = br(id="fremdmarke-a4-x1", marke="Fremdmarke", modell="A4",
                            generation="X1", bauzeitraum_von=2015, bauzeitraum_bis=2023)
_kba_cross = kba_zeile(Marke="AUDI", Modell="A4")
_paare_i = paare_von([_kba_cross], [_audi_a4, _fiktive_fremdmarke_a4])
check("I1 nur die Audi-Baureihe erscheint ueberhaupt als Paar (Marken-Gate "
      "greift VOR jeder Token-Konkurrenz)",
      set(_paare_i) == {"audi-a4-b9"})
check("I2 die Audi-Baureihe ist dadurch eindeutig SAFE_IMPORT, nicht etwa "
      "durch eine (nicht existierende) markenuebergreifende Konkurrenz blockiert",
      _paare_i.get("audi-a4-b9") == SAFE_IMPORT)


# ══ L) "Randueberlappung"/"ueberdehnte Generation" sind NICHT fallback-faehig ═
print("\n--- L) andere Ambiguitaetsarten bleiben review-only (keine Fallback-Kandidaten) ---")
_g_klasse_offen = br(id="mercedes-benz-g-klasse-w463", marke="Mercedes-Benz",
                     modell="G-Klasse", generation="W463",
                     bauzeitraum_von=1990, bauzeitraum_bis=None)
_kba_ueberdehnt = kba_zeile(Marke="MERCEDES-BENZ", Modell="G-KLASSE", **{
    "Produktionszeitraum von": "2024", "Produktionszeitraum bis": "2025"})
_fb_l1 = fallback_ids_von([_kba_ueberdehnt], [_g_klasse_offen])
check("L1 eine ueberdehnte offene Generation liefert KEINE fallback_baureihen "
      "(andere Unsicherheitsart, nicht Modell-/Generationsmehrdeutigkeit)",
      _fb_l1 == [])

_randlage_ziel = br(id="ford-galaxy-gen2", marke="Ford", modell="Galaxy",
                    generation="2", bauzeitraum_von=2006, bauzeitraum_bis=2015)
_kba_randlage = kba_zeile(Marke="FORD", Modell="GALAXY", **{
    "Produktionszeitraum von": "2015", "Produktionszeitraum bis": "2020"})
_fb_l2 = fallback_ids_von([_kba_randlage], [_randlage_ziel])
check("L2 eine reine Randueberlappung liefert ebenfalls KEINE fallback_baureihen",
      _fb_l2 == [])


# ══ Ebene B: sicherer Ambiguitaets-Fallback zur Laufzeit ════════════════════
print("\n--- C/D/E/F/G/J/K) sicherer Ambiguitaets-Fallback (ambiguitaet_hinweise) ---")

_review_9831 = {
    "kba_referenz": "9831", "klasse": "AMBIGUOUS_GENERATION", "marke": "AUDI",
    "modell": "A6, A7, A4, A5, Q5",
    "mangel": "Durch Feuchtigkeitseintritt in den Riemenstartergenerator können "
             "Kurzschlussbrücken entstehen. Fahrzeugbrand möglich.",
    "produktionszeitraum": "2017-2020",
    "eingrenzung_amtlich": "Es sind ausschließlich Fahrzeuge mit 2.0 TFSI und "
                          "Mild-Hybrid-System betroffen",
    "fallback_baureihen": json.dumps(["audi-rs-4-avant-b9"]),
}
_review_10206 = {**_review_9831, "kba_referenz": "10206",
                 "eingrenzung_amtlich": "Es sind ausschließlich Fahrzeuge mit 2.0 TDI "
                                       "und Mild-Hybrid-System betroffen"}
_reviews = [_review_9831, _review_10206]

# C) echter Gleichstand, Fahrzeug im Candidate-Set, keine Contradiction -> sichtbar
_id_rs4_unklar = VehicleIdentity(make="Audi", model="RS 4 Avant", fuel="Benzin", year=2018)
_hints_c = ambiguitaet_hinweise("audi-rs-4-avant-b9", _id_rs4_unklar, 2018, _reviews, set())
check("C1 RS4-Avant-Vehicle, MHEV-Status unbekannt: 9831 wird als sicherer "
      "Fallback-Hinweis sichtbar",
      any(h["kba_referenz"] == "9831" for h in _hints_c))

# D) Fahrzeug NICHT im Candidate-Set -> kein Fallback
_hints_d = ambiguitaet_hinweise("audi-a5-ii", _id_rs4_unklar, 2018, _reviews, set())
check("D1 eine Baureihe, die gar nicht in fallback_baureihen steht, bekommt "
      "keinen Hinweis", _hints_d == [])

# E) bekannter Kraftstoffwiderspruch (TDI-Kuerzel, nicht nur das Wort) -> ausgeschlossen
_hints_e = ambiguitaet_hinweise("audi-rs-4-avant-b9", _id_rs4_unklar, 2018, _reviews, set())
check("E1 10206 (2.0 TDI) wird fuer ein Benzin-Fahrzeug NICHT angezeigt "
      "(Kraftstoffwiderspruch ueber das Motorcode-Kuerzel erkannt, nicht nur "
      "das ausgeschriebene Wort)",
      not any(h["kba_referenz"] == "10206" for h in _hints_e))
check("E2 ... waehrend 9831 (2.0 TFSI, passend) weiterhin sichtbar bleibt",
      any(h["kba_referenz"] == "9831" for h in _hints_e))

# F) Bedingung (MHEV) unbekannt -> trotzdem sichtbar, nicht versteckt.
# Mit identity.fuel="Benzin" explizit bekannt (passt zu "2.0 TFSI") hebt
# bereits die Kraftstoff-Dimension allein den Scope auf VARIANT_POSSIBLE,
# obwohl die Antriebsart (MHEV) weiterhin offen bleibt — das ist das
# GEWUENSCHTE, "staerkste bekannte Signal zaehlt"-Verhalten, kein Fehler.
# Fuer den Fall, dass WIRKLICH gar nichts bekannt ist (weder Kraftstoff noch
# Antriebsart), eine zweite Identity ganz ohne Kraftstoffangabe:
check("F1 (= C1) unbekannter MHEV-Status versteckt den Hinweis NICHT, wenn "
      "zumindest der Kraftstoff bekannt ist und passt",
      any(h["kba_referenz"] == "9831" for h in _hints_c))
_id_rs4_voellig_unklar = VehicleIdentity(make="Audi", model="RS 4 Avant", year=2018)
_scope_f, _ = rueckruf_scope(
    {"mangel": _review_9831["mangel"], "abhilfe": None,
     "betroffene_baujahre": "2017-2020",
     "eingrenzung_amtlich": _review_9831["eingrenzung_amtlich"]},
    _id_rs4_voellig_unklar)
check("F2 ist WIRKLICH NICHTS bekannt (weder Kraftstoff noch Antriebsart), "
      "meldet der Scope-Motor ehrlich UNKNOWN statt etwas zu erfinden",
      _scope_f == "UNKNOWN")
_hints_f2 = ambiguitaet_hinweise("audi-rs-4-avant-b9", _id_rs4_voellig_unklar,
                                2018, _reviews, set())
check("F3 ... und auch DANN verschwindet der Hinweis nicht einfach",
      any(h["kba_referenz"] == "9831" for h in _hints_f2))

# G) Bedingung passt explizit -> Fallback bleibt IMMER "unclear", nie staerker.
# Release-Gate-Fund (Audi-9831-Shadow-Proof): "2.0 TFSI UND Mild-Hybrid-System"
# ist eine KONJUNKTION — displacement MUSS hier ebenfalls bekannt sein, sonst
# bleibt "Hubraum" offen und der Gesamt-Scope korrekt bei UNKNOWN (siehe
# Kombinationsregel in rueckruf_scope: JEDE offene Dimension degradiert das
# Ergebnis, kein einzelner Treffer darf das mehr uebertoenen).
_id_rs4_mhev = VehicleIdentity(make="Audi", model="RS 4 Avant", fuel="Benzin",
                               displacement="2.0", year=2018, powertrain="MHEV")
_scope_g, _ = rueckruf_scope(
    {"mangel": _review_9831["mangel"], "abhilfe": None,
     "betroffene_baujahre": "2017-2020",
     "eingrenzung_amtlich": _review_9831["eingrenzung_amtlich"]},
    _id_rs4_mhev)
check("G1 Vorbedingung: sind WIRKLICH ALLE Dimensionen bekannt und passend "
      "(Hubraum+Kraftstoff+Antriebsart), meldet der Scope-Motor VARIANT_POSSIBLE",
      _scope_g == "VARIANT_POSSIBLE")
_hints_g = ambiguitaet_hinweise("audi-rs-4-avant-b9", _id_rs4_mhev, 2018, _reviews, set())
check("G2 der Fallback-Hinweis selbst bleibt trotzdem sichtbar (Scope nicht "
      "NOT_APPLICABLE)", any(h["kba_referenz"] == "9831" for h in _hints_g))
# (Die Begrenzung auf applicability="unclear" sitzt in app.evidence.build_insights,
#  nicht in ambiguitaet_hinweise selbst — dort gibt es bewusst GAR KEIN
#  "applicability"-Feld im Rueckgabe-Dict, das staerker als die feste Stufe
#  waere, die der Aufrufer erzwingt.)
check("G3 das Fallback-Dict selbst traegt KEIN eigenes 'applicability'-Feld "
      "(die Deckelung auf 'unclear' erzwingt ausschliesslich der Aufrufer "
      "app.evidence.build_insights, nicht dieses Modul)",
      "applicability" not in _hints_g[0])

# J) Produktionsfenster-Widerspruch -> kein Fallback
_hints_j = ambiguitaet_hinweise("audi-rs-4-avant-b9", _id_rs4_unklar, 2010, _reviews, set())
check("J1 Baujahr 2010 ausserhalb des amtlichen Fensters 2017-2020 -> kein Hinweis",
      _hints_j == [])

# K) bereits kanonischer Treffer -> kein zweiter, schwaecherer Fallback-Eintrag
_hints_k = ambiguitaet_hinweise("audi-rs-4-avant-b9", _id_rs4_unklar, 2018, _reviews, {"9831"})
check("K1 wenn 9831 fuer diese Baureihe schon kanonisch vorliegt, erscheint "
      "KEIN zweiter Ambiguitaets-Hinweis dafuer",
      not any(h["kba_referenz"] == "9831" for h in _hints_k))
check("K2 ... 10206 bleibt davon unberuehrt (eigene Referenz, eigene Pruefung, "
      "hier ohnehin durch Kraftstoff ausgeschlossen)",
      not any(h["kba_referenz"] == "10206" for h in _hints_k))


# ══ M) Markenunabhaengigkeit: ein echter Nicht-Audi-Fall aus dem KBA-Export ══
print("\n--- M) Markenunabhaengigkeit (Mercedes GLE W166/W167) ---")
# Realer Fall aus dem aktuellen KBA-Gesamtexport (Produktions-Dry-Run dieser
# Serie): "GLE" ist der eigene Name von mercedes-benz-gle-w167 (Tier 1), aber
# fuer die FACELIFT-Variante von W166 nur per MODELL_MAP-Alias erreichbar
# (("MERCEDES-BENZ","GLE COUPE"): {"GLE"} — eine abgeleitete Karosserievariante,
# nicht die Baureihe selbst). Derselbe Mechanismus, andere Marke, kein
# Sonderfall.
_gle_w167 = br(id="mercedes-benz-gle-w167", marke="Mercedes-Benz", modell="GLE",
              generation="W167", bauzeitraum_von=2018, bauzeitraum_bis=2023)
_gle_coupe = br(id="mercedes-benz-gle-coupe-c167", marke="Mercedes-Benz",
               modell="GLE Coupe", generation="C167",
               bauzeitraum_von=2019, bauzeitraum_bis=2023)
check("M0 Vorbedingung: GLE Coupe erreicht 'GLE' NUR per Alias",
      match_tier("Mercedes-Benz", "GLE Coupe", "GLE") == 2)
check("M0b Vorbedingung: GLE (W167) erreicht 'GLE' als eigenen Namen",
      match_tier("Mercedes-Benz", "GLE", "GLE") == 1)
_kba_gle = kba_zeile(Marke="MERCEDES-BENZ", Modell="GLE", **{
    "Produktionszeitraum von": "2019", "Produktionszeitraum bis": "2021"})
_paare_m = paare_von([_kba_gle], [_gle_w167, _gle_coupe])
check("M1 mercedes-benz-gle-w167 gewinnt den Token 'GLE' eindeutig "
      "(derselbe Mechanismus wie Audi A4/RS4, andere Marke)",
      _paare_m.get("mercedes-benz-gle-w167") == SAFE_IMPORT)
check("M2 mercedes-benz-gle-coupe-c167 bleibt AMBIGUOUS_GENERATION "
      "(Alias-Treffer, nicht ausgeschlossen, nur nicht kanonisch)",
      _paare_m.get("mercedes-benz-gle-coupe-c167") == AMBIGUOUS_GENERATION)


print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE MATCH-STAERKE-/AMBIGUITAETS-FALLBACK-TESTS GRUEN")
