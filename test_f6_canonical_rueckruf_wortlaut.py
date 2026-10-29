"""
F6 (wiedergefundene Originalbefunde, 2026-10-29): Rückruftexte behaupteten
"Baureihe", obwohl fuer canonical-only Rueckrufe (RC-W6: Marke+Nameplate ohne
ENFAL-Baureihen-Katalogeintrag, `baureihe_id` nicht gesetzt) real nur eine
kanonische Modellzuordnung vorliegt. Der Fix unterscheidet den Wortlaut anhand
`r.get("baureihe_id")` (rohe Rueckruf-Zeile) bzw. `Insight.rueckruf_baureihe_bekannt`
(abgeleitetes Insight-Feld) an allen betroffenen Stellen:
  - app/recall_filter.py: rueckruf_applicability() variant_hinweis/einfluss,
    rueckruf_wortlaut(), _annotiere()
  - app/evidence.py: Insight-Titel, format_evidence_for_prompt()
  - app/key_findings.py: _rueckruf_findings() unclear-Sammelfinding
  - app/kaufaktionen.py: _aus_rueckrufen() Verkaeuferfrage/Dokument

Deterministisch, kein Netzwerk/LLM/DB-Schreibzugriff.

    python test_f6_canonical_rueckruf_wortlaut.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_f6_"), "test.db")
sys.path.insert(0, ".")

from app.recall_filter import rueckruf_applicability, rueckruf_wortlaut  # noqa: E402
from app.evidence import build_insights, format_evidence_for_prompt  # noqa: E402
from app.key_findings import build_key_findings_kauf  # noqa: E402
from app.kaufaktionen import build_kaufaktionen  # noqa: E402
from app.models import Insight  # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


# ── Basis-Rueckruf-Zeile, einmal MIT (Katalog-Baureihe), einmal OHNE
# (canonical-only) `baureihe_id` ─────────────────────────────────────────────
_MODELLWEIT_KATALOG = {
    "id": 9001, "baureihe_id": "test-baureihe", "datum": "2021-05-05",
    "betroffene_baujahre": "2019-2021",
    "mangel": "Die Verschraubung des Lenkgetriebes kann sich loesen.",
    "abhilfe": "Austausch der Schrauben am Lenkgetriebe.",
    "kba_referenz": "11331", "_trust": "verified",
}
# canonical-only (RC-W6): dieselben Daten, aber KEIN `baureihe_id`-Schluessel
# (so liefert app.database.get_rueckrufe_canonical_fuer_identity die Zeilen
# tatsaechlich -- s. Modulkopf dort).
_MODELLWEIT_CANONICAL = {k: v for k, v in _MODELLWEIT_KATALOG.items() if k != "baureihe_id"}
_MODELLWEIT_CANONICAL["canonical_make"] = "LEXUS"
_MODELLWEIT_CANONICAL["canonical_nameplate"] = "RC"

_MIT_QUALIFIER_KATALOG = {**_MODELLWEIT_KATALOG, "id": 9002,
                         "betroffene_baujahre": "2019-2021 (Plug-in-Hybrid)"}
_MIT_QUALIFIER_CANONICAL = {k: v for k, v in _MIT_QUALIFIER_KATALOG.items() if k != "baureihe_id"}

print("=" * 70)
print("1) rueckruf_applicability(): series_only-Wortlaut (kba_ok-Zweig)")
print("=" * 70)

_, _, einfluss_katalog, _ = rueckruf_applicability(
    _MODELLWEIT_KATALOG, True, "11331", {"kraftstoff": "Diesel"}, marke="Opel")
check("1a Katalog-Baureihe -> 'Baureihe' im Wortlaut", "Baureihe" in einfluss_katalog)

_, _, einfluss_canonical, _ = rueckruf_applicability(
    _MODELLWEIT_CANONICAL, True, "11331", {"kraftstoff": "Diesel"}, marke="Opel")
# "ENFAL-Baureihen-Zuordnung" (als ehrliche Einschraenkung) ist erlaubt und
# erwuenscht -- nur die BEHAUPTUNG "betrifft die Baureihe" darf nicht mehr
# vorkommen (das war F6). Deshalb kein blosser Substring-Check auf "Baureihe".
check("1b canonical-only -> KEINE Baureihen-BEHAUPTUNG mehr ('betrifft die Baureihe')",
      "betrifft die Baureihe" not in einfluss_canonical)
check("1c canonical-only -> 'Modell' im Wortlaut", "Modell" in einfluss_canonical)
check("1c-2 canonical-only -> nennt ehrlich 'keine ENFAL-Baureihen-Zuordnung'",
      "keine ENFAL-Baureihen-Zuordnung" in einfluss_canonical)
check("1d canonical-only -> Beleglage/Applicability unveraendert (series_only, hoch)",
      rueckruf_applicability(_MODELLWEIT_CANONICAL, True, "11331",
                             {"kraftstoff": "Diesel"}, marke="Opel")[0:2] == ("series_only", "hoch"))

print("\n" + "=" * 70)
print("2) rueckruf_applicability(): unclear-Wortlaut (Scope-Achse unbekannt)")
print("=" * 70)

_appl_u_k, _, _, hinweis_katalog = rueckruf_applicability(
    _MIT_QUALIFIER_KATALOG, True, "11331", None, marke="Opel")
check("2a Vorbedingung: Scope unbekannt -> unclear", _appl_u_k == "unclear")
check("2b Katalog-Baureihe -> 'Baureihe' im Zusatztext", "Baureihe" in hinweis_katalog)

_appl_u_c, _, _, hinweis_canonical = rueckruf_applicability(
    _MIT_QUALIFIER_CANONICAL, True, "11331", None, marke="Opel")
check("2c canonical-only bleibt unclear", _appl_u_c == "unclear")
check("2d canonical-only -> KEINE Baureihen-BEHAUPTUNG mehr ('Für die Baureihe hinterlegt')",
      "Für die Baureihe hinterlegt" not in hinweis_canonical)
check("2e canonical-only -> 'Modell' im Zusatztext", "Modell" in hinweis_canonical)

print("\n" + "=" * 70)
print("3) rueckruf_wortlaut(): LLM-Prompt-Wortlaut (format_evidence_for_prompt)")
print("=" * 70)

check("3a Katalog bekannt (Default) -> 'Baureihe'",
      "Baureihe" in rueckruf_wortlaut("series_only"))
check("3b Katalog bekannt explizit True -> 'Baureihe'",
      "Baureihe" in rueckruf_wortlaut("series_only", baureihe_bekannt=True))
check("3c canonical-only -> 'Modell', keine Baureihen-BEHAUPTUNG ('Für Teile der Baureihe')",
      "Modell" in rueckruf_wortlaut("series_only", baureihe_bekannt=False)
      and "Für Teile der Baureihe" not in rueckruf_wortlaut("series_only", baureihe_bekannt=False))
check("3d FIN-Pruefung bleibt in beiden Fassungen erhalten",
      "FIN" in rueckruf_wortlaut("series_only", baureihe_bekannt=True)
      and "FIN" in rueckruf_wortlaut("series_only", baureihe_bekannt=False))
check("3e andere Applicability-Stufen unveraendert (kein 'Baureihe'-Sonderfall)",
      rueckruf_wortlaut("variant_match", baureihe_bekannt=False)
      == rueckruf_wortlaut("variant_match", baureihe_bekannt=True))

print("\n" + "=" * 70)
print("4) build_insights(): Titel + rueckruf_baureihe_bekannt-Feld")
print("=" * 70)


def req(baujahr=2020):
    from types import SimpleNamespace
    return SimpleNamespace(baujahr=baujahr, ausstattung=None, beschreibung=None, freitext=None,
                           kraftstoff=None)


# Vorbestehender, von F6 UNABHAENGIGER Befund (separat gemeldet, hier nicht
# behoben): build_insights() ruft fuer eine Baureihe MIT `id` ungefragt den
# Ambiguitaets-Fallback (Ebene B) auf, der `kba_rueckruf_review` lesen will --
# diese Tabelle existiert in einer frisch (ohne `ensure_tables()`) angelegten
# Test-DB nicht (auch test_evidence.py/test_kaufaktionen.py trifft das bereits
# unveraendert auf HEAD, s. Abschlussbericht). Fuer DIESEN F6-Test ist dieser
# Pfad irrelevant (er betrifft nur bereits-katalogisierte Baureihen); ein
# gezielter Stub haelt den Test unabhaengig von diesem separaten Befund.
import app.database as _db
_db.get_alle_review_fallback_kurz = lambda: []

# 4a) Katalog-Baureihe: baureihe["rueckrufe"] traegt baureihe_id -> "Baureihe" im Titel.
BAUREIHE_KATALOG = {"id": "test-baureihe", "marke": "Opel", "modell": "Insignia",
                    "schwachstellen_baureihe": [], "rueckrufe": [_MIT_QUALIFIER_KATALOG]}
ins_katalog = build_insights(BAUREIHE_KATALOG, None, [], req(2020), check_typ="kauf")
rk_katalog = [i for i in ins_katalog if i.kategorie == "rueckruf"]
check("4a Katalog-Rueckruf erzeugt genau einen Insight", len(rk_katalog) == 1)
check("4b Katalog-Rueckruf: Titel nennt 'Baureihe'",
      bool(rk_katalog) and "Baureihe" in rk_katalog[0].titel)
check("4c Katalog-Rueckruf: rueckruf_baureihe_bekannt ist True",
      bool(rk_katalog) and rk_katalog[0].rueckruf_baureihe_bekannt is True)

# 4b) canonical-only: baureihe=None (wie in app/kaufcheck.py, RC-W6), Rueckruf kommt
# ausschliesslich ueber canonical_rueckrufe (ohne baureihe_id).
ins_canonical = build_insights(None, None, [], req(2020), check_typ="kauf",
                               canonical_rueckrufe=[_MIT_QUALIFIER_CANONICAL])
rk_canonical = [i for i in ins_canonical if i.kategorie == "rueckruf"]
check("4d canonical-only Rueckruf erzeugt genau einen Insight", len(rk_canonical) == 1)
check("4e canonical-only: Titel nennt 'Modell', NICHT 'Baureihe'",
      bool(rk_canonical) and "Modell" in rk_canonical[0].titel
      and "Baureihe" not in rk_canonical[0].titel)
check("4f canonical-only: rueckruf_baureihe_bekannt ist False",
      bool(rk_canonical) and rk_canonical[0].rueckruf_baureihe_bekannt is False)
check("4g canonical-only: Beleglage/Applicability unveraendert (dieselbe Pipeline)",
      bool(rk_canonical) and rk_canonical[0].applicability == rk_katalog[0].applicability
      and rk_canonical[0].confidence == rk_katalog[0].confidence)

print("\n" + "=" * 70)
print("5) format_evidence_for_prompt(): LLM sieht 'Modell' statt 'Baureihe'")
print("=" * 70)

prompt_katalog = format_evidence_for_prompt(rk_katalog)
prompt_canonical = format_evidence_for_prompt(rk_canonical)
check("5a Katalog-Prompt nennt 'Baureihe'", "Baureihe" in prompt_katalog)
check("5b canonical-Prompt nennt 'Modell', nicht 'Baureihe'",
      "Modell" in prompt_canonical and "Baureihe" not in prompt_canonical)

print("\n" + "=" * 70)
print("6) key_findings._rueckruf_findings(): unclear-Sammelfinding")
print("=" * 70)

kf_canonical = build_key_findings_kauf(req(2020), None, None, rk_canonical)
rk_finding_c = [f for f in kf_canonical if f.kategorie == "rueckruf"]
check("6a canonical-only Finding erzeugt (unclear-Bucket)", bool(rk_finding_c))
if rk_finding_c:
    check("6b canonical-only Finding: 'dieses Modell', nicht 'die Baureihe'",
          "Modell" in rk_finding_c[0].beschreibung and "Baureihe" not in rk_finding_c[0].beschreibung)

kf_katalog = build_key_findings_kauf(req(2020), BAUREIHE_KATALOG, None, rk_katalog)
rk_finding_k = [f for f in kf_katalog if f.kategorie == "rueckruf"]
check("6c Katalog-Finding erzeugt (unclear-Bucket)", bool(rk_finding_k))
if rk_finding_k:
    check("6d Katalog-Finding: weiterhin 'die Baureihe'",
          "Baureihe" in rk_finding_k[0].beschreibung)

print("\n" + "=" * 70)
print("7) kaufaktionen._aus_rueckrufen(): Verkaeuferfrage/Dokument")
print("=" * 70)

ka_canonical = build_kaufaktionen(req(2020), None, None, rk_canonical)
frage_c = next((f for f in ka_canonical.verkaeuferfragen.fahrzeugspezifisch
               if f.kategorie == "rueckruf"), None)
check("7a canonical-only: Verkaeuferfrage vorhanden", frage_c is not None)
if frage_c:
    check("7b canonical-only: Aktion sagt 'dieses Modell', nicht 'dieser Baureihe'",
          "dieses Modell" in frage_c.aktion and "Baureihe" not in frage_c.aktion)

ka_katalog = build_kaufaktionen(req(2020), BAUREIHE_KATALOG, None, rk_katalog)
frage_k = next((f for f in ka_katalog.verkaeuferfragen.fahrzeugspezifisch
               if f.kategorie == "rueckruf"), None)
check("7c Katalog: Verkaeuferfrage vorhanden", frage_k is not None)
if frage_k:
    check("7d Katalog: Aktion sagt weiterhin 'dieser Baureihe'",
          "dieser Baureihe" in frage_k.aktion)

print("\n" + "=" * 70)
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print(" -", f)
    sys.exit(1)
print("Alle F6-Canonical-Rueckruf-Wortlaut-Tests bestanden.")
